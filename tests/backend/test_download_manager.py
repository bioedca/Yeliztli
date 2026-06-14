"""Tests for the resumable download manager (P1-17).

Covers:
- Fresh download with progress tracking and SHA-256 verification
- Resumable download via HTTP Range headers
- Checksum mismatch detection
- Download record persistence (checkpoint offsets)
- Job creation for SSE progress
- Error handling (network failures, missing records)
- Finding existing resumable downloads
"""

from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.db.download_manager import (
    CHECKPOINT_INTERVAL,
    ChecksumMismatchError,
    DownloadError,
    DownloadManager,
    DownloadResult,
    _compute_sha256,
)
from backend.db.tables import downloads, jobs, reference_metadata

# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def ref_engine() -> sa.Engine:
    """In-memory SQLite engine with reference tables."""
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    return engine


@pytest.fixture
def dl_dir(tmp_path: Path) -> Path:
    """Temporary downloads directory."""
    d = tmp_path / "downloads"
    d.mkdir()
    return d


@pytest.fixture
def manager(ref_engine: sa.Engine, dl_dir: Path) -> DownloadManager:
    """DownloadManager wired to in-memory engine and temp dir.

    Uses a no-op sleep so retry backoff doesn't slow the suite down.
    """
    return DownloadManager(ref_engine, dl_dir, sleep=lambda _delay: None)


# ═══════════════════════════════════════════════════════════════════════
# Test HTTP server with Range support
# ═══════════════════════════════════════════════════════════════════════

TEST_DATA = b"A" * 1024 + b"B" * 1024 + b"C" * 1024  # 3 KiB


class RangeHTTPHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler supporting Range requests."""

    data = TEST_DATA

    def do_GET(self) -> None:
        range_header = self.headers.get("Range")
        if range_header:
            # Parse "bytes=START-"
            _, range_spec = range_header.split("=", 1)
            start_str = range_spec.rstrip("-").split("-")[0]
            start = int(start_str)
            end = len(self.data)

            if start >= len(self.data):
                self.send_response(416)  # Range Not Satisfiable
                self.end_headers()
                return

            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end - 1}/{len(self.data)}")
            self.send_header("Content-Length", str(end - start))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(self.data[start:end])
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(self.data)))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(self.data)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Suppress default HTTP server logging."""


class ErrorHTTPHandler(BaseHTTPRequestHandler):
    """Handler that always returns 500."""

    def do_GET(self) -> None:
        self.send_response(500)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


@pytest.fixture
def http_server():
    """Start a local HTTP server with Range support."""
    server = HTTPServer(("127.0.0.1", 0), RangeHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


@pytest.fixture
def error_server():
    """Start a local HTTP server that returns 500."""
    server = HTTPServer(("127.0.0.1", 0), ErrorHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def server_url(server: HTTPServer) -> str:
    host, port = server.server_address
    return f"http://{host}:{port}/testfile.db"


# ═══════════════════════════════════════════════════════════════════════
# Tests: _compute_sha256
# ═══════════════════════════════════════════════════════════════════════


def test_compute_sha256(tmp_path: Path) -> None:
    """SHA-256 computation matches hashlib reference."""
    path = tmp_path / "test.bin"
    data = b"hello world"
    path.write_bytes(data)

    result = _compute_sha256(path)
    expected = hashlib.sha256(data).hexdigest()
    assert result == expected


# ═══════════════════════════════════════════════════════════════════════
# Tests: fresh download
# ═══════════════════════════════════════════════════════════════════════


def test_start_fresh_download(
    manager: DownloadManager,
    ref_engine: sa.Engine,
    http_server: HTTPServer,
    dl_dir: Path,
) -> None:
    """Full fresh download completes and verifies SHA-256."""
    url = server_url(http_server)
    expected_sha256 = hashlib.sha256(TEST_DATA).hexdigest()

    result = manager.start(url, "testfile.db", expected_sha256=expected_sha256)

    assert isinstance(result, DownloadResult)
    assert result.error is None
    assert result.verified is True
    assert result.sha256 == expected_sha256
    assert result.total_bytes == len(TEST_DATA)
    assert result.dest_path == dl_dir / "testfile.db"
    assert result.dest_path.read_bytes() == TEST_DATA

    # Verify download record is marked complete
    with ref_engine.connect() as conn:
        row = conn.execute(
            sa.select(downloads.c.status, downloads.c.downloaded_bytes).where(
                downloads.c.id == result.download_id
            )
        ).fetchone()
    assert row is not None
    assert row.status == "complete"
    assert row.downloaded_bytes == len(TEST_DATA)

    # Verify job record is complete
    with ref_engine.connect() as conn:
        job = conn.execute(
            sa.select(jobs.c.status, jobs.c.progress_pct).where(jobs.c.job_id == result.job_id)
        ).fetchone()
    assert job is not None
    assert job.status == "complete"
    assert job.progress_pct == 100.0


def test_start_download_with_progress_callback(
    manager: DownloadManager,
    http_server: HTTPServer,
) -> None:
    """Progress callback is called during download."""
    url = server_url(http_server)
    progress_calls: list[tuple[int, int | None]] = []

    def on_progress(downloaded: int, total: int | None) -> None:
        progress_calls.append((downloaded, total))

    result = manager.start(url, "test_progress.db", progress_callback=on_progress)
    assert result.error is None
    assert len(progress_calls) > 0
    # Final progress should reflect total bytes
    last_downloaded, last_total = progress_calls[-1]
    assert last_downloaded == len(TEST_DATA)
    assert last_total == len(TEST_DATA)


def test_start_download_no_checksum(
    manager: DownloadManager,
    http_server: HTTPServer,
    dl_dir: Path,
) -> None:
    """Download succeeds without expected checksum."""
    url = server_url(http_server)
    result = manager.start(url, "no_checksum.db")

    assert result.error is None
    assert result.verified is True
    assert result.sha256 is not None
    assert (dl_dir / "no_checksum.db").read_bytes() == TEST_DATA


# ═══════════════════════════════════════════════════════════════════════
# Tests: checksum mismatch
# ═══════════════════════════════════════════════════════════════════════


def test_checksum_mismatch_raises(
    manager: DownloadManager,
    http_server: HTTPServer,
    dl_dir: Path,
) -> None:
    """ChecksumMismatchError raised when SHA-256 doesn't match."""
    url = server_url(http_server)

    with pytest.raises(ChecksumMismatchError, match="SHA-256 mismatch"):
        manager.start(url, "bad_checksum.db", expected_sha256="wrong_hash")

    # Temp file should be cleaned up
    assert not (dl_dir / "bad_checksum.db.tmp").exists()
    assert not (dl_dir / "bad_checksum.db").exists()


# ═══════════════════════════════════════════════════════════════════════
# Tests: resume download
# ═══════════════════════════════════════════════════════════════════════


def test_resume_download(
    manager: DownloadManager,
    ref_engine: sa.Engine,
    http_server: HTTPServer,
    dl_dir: Path,
) -> None:
    """Resume picks up from checkpointed byte offset using Range header."""
    url = server_url(http_server)
    partial_size = 1024  # First 1 KiB written

    # Simulate a partial download: write partial data + create DB record
    tmp_path = dl_dir / "resume_test.db.tmp"
    tmp_path.write_bytes(TEST_DATA[:partial_size])

    # Create download record with partial offset
    with ref_engine.begin() as conn:
        result = conn.execute(
            downloads.insert().values(
                url=url,
                dest_path=str(dl_dir / "resume_test.db"),
                total_bytes=len(TEST_DATA),
                downloaded_bytes=partial_size,
                checksum_sha256=None,
                status="failed",
            )
        )
        download_id = result.lastrowid

    # Resume the download
    result = manager.resume(download_id)

    assert result.error is None
    assert result.total_bytes == len(TEST_DATA)
    dest = dl_dir / "resume_test.db"
    assert dest.exists()
    assert dest.read_bytes() == TEST_DATA


def test_resume_nonexistent_raises(manager: DownloadManager) -> None:
    """Resuming a nonexistent download raises DownloadError."""
    with pytest.raises(DownloadError, match="not found"):
        manager.resume(99999)


def test_resume_complete_raises(
    manager: DownloadManager,
    http_server: HTTPServer,
) -> None:
    """Resuming an already-complete download raises DownloadError."""
    url = server_url(http_server)
    # First, complete a download
    result = manager.start(url, "already_done.db")
    assert result.error is None

    with pytest.raises(DownloadError, match="already complete"):
        manager.resume(result.download_id)


# ═══════════════════════════════════════════════════════════════════════
# Tests: auto-resume via start()
# ═══════════════════════════════════════════════════════════════════════


def test_start_finds_resumable(
    manager: DownloadManager,
    ref_engine: sa.Engine,
    http_server: HTTPServer,
    dl_dir: Path,
) -> None:
    """start() finds an existing incomplete download and resumes it."""
    url = server_url(http_server)
    dest_path = str(dl_dir / "auto_resume.db")
    partial_size = 512

    # Create partial temp file
    tmp_path = dl_dir / "auto_resume.db.tmp"
    tmp_path.write_bytes(TEST_DATA[:partial_size])

    # Create an incomplete download record
    with ref_engine.begin() as conn:
        conn.execute(
            downloads.insert().values(
                url=url,
                dest_path=dest_path,
                total_bytes=len(TEST_DATA),
                downloaded_bytes=partial_size,
                status="failed",
            )
        )

    # start() should find and resume
    result = manager.start(url, "auto_resume.db")
    assert result.error is None
    assert (dl_dir / "auto_resume.db").read_bytes() == TEST_DATA


def test_start_recovers_from_midstream_drop(
    ref_engine: sa.Engine,
    dl_dir: Path,
) -> None:
    """A single start() auto-retries a mid-stream connection drop and completes."""

    class DropOnceHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        dropped = False

        def do_GET(self) -> None:
            range_header = self.headers.get("Range")
            if range_header:
                start = int(range_header.split("=", 1)[1].split("-", 1)[0])
                self.send_response(206)
                self.send_header(
                    "Content-Range", f"bytes {start}-{len(TEST_DATA) - 1}/{len(TEST_DATA)}"
                )
                self.send_header("Content-Length", str(len(TEST_DATA) - start))
                self.end_headers()
                self.wfile.write(TEST_DATA[start:])
                return
            if not type(self).dropped:
                type(self).dropped = True
                self.send_response(200)
                self.send_header("Content-Length", str(len(TEST_DATA)))
                self.end_headers()
                self.wfile.write(TEST_DATA[:1024])
                self.close_connection = True
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(TEST_DATA)))
            self.end_headers()
            self.wfile.write(TEST_DATA)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = HTTPServer(("127.0.0.1", 0), DropOnceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        url = f"http://{host}:{port}/testfile.db"
        manager = DownloadManager(ref_engine, dl_dir, sleep=lambda _delay: None)

        result = manager.start(url, "drop_recover.db")

        assert result.error is None
        assert result.total_bytes == len(TEST_DATA)
        assert (dl_dir / "drop_recover.db").read_bytes() == TEST_DATA
    finally:
        server.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# Tests: network errors
# ═══════════════════════════════════════════════════════════════════════


def test_download_server_error(
    manager: DownloadManager,
    ref_engine: sa.Engine,
    error_server: HTTPServer,
) -> None:
    """Download failure is recorded in downloads and jobs tables."""
    url = server_url(error_server)
    result = manager.start(url, "will_fail.db")

    assert result.error is not None
    assert "500" in result.error or "Server Error" in result.error

    # Download record should be marked failed
    with ref_engine.connect() as conn:
        row = conn.execute(
            sa.select(downloads.c.status).where(downloads.c.id == result.download_id)
        ).fetchone()
    assert row is not None
    assert row.status == "failed"

    # Job should be marked failed
    with ref_engine.connect() as conn:
        job = conn.execute(
            sa.select(jobs.c.status, jobs.c.error).where(jobs.c.job_id == result.job_id)
        ).fetchone()
    assert job is not None
    assert job.status == "failed"
    assert job.error is not None


# ═══════════════════════════════════════════════════════════════════════
# Tests: get_status
# ═══════════════════════════════════════════════════════════════════════


def test_get_status(
    manager: DownloadManager,
    http_server: HTTPServer,
) -> None:
    """get_status returns dict for existing download, None for missing."""
    url = server_url(http_server)
    result = manager.start(url, "status_test.db")

    status = manager.get_status(result.download_id)
    assert status is not None
    assert status["status"] == "complete"
    assert status["url"] == url

    assert manager.get_status(99999) is None


# ═══════════════════════════════════════════════════════════════════════
# Tests: checkpoint interval
# ═══════════════════════════════════════════════════════════════════════


def test_checkpoint_writes_offset(
    ref_engine: sa.Engine,
    dl_dir: Path,
) -> None:
    """Downloads larger than CHECKPOINT_INTERVAL checkpoint multiple times."""
    # Use data larger than checkpoint interval
    large_data = b"X" * (CHECKPOINT_INTERVAL + 1024)

    class LargeHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(large_data)))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(large_data)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = HTTPServer(("127.0.0.1", 0), LargeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        url = server_url(server)
        manager = DownloadManager(ref_engine, dl_dir)

        # Track checkpoint updates
        checkpoint_offsets: list[int] = []
        orig_checkpoint = manager._checkpoint_offset

        def tracking_checkpoint(download_id: int, offset: int) -> None:
            checkpoint_offsets.append(offset)
            orig_checkpoint(download_id, offset)

        manager._checkpoint_offset = tracking_checkpoint  # type: ignore[assignment]

        result = manager.start(url, "large_file.db")
        assert result.error is None
        # Should have at least 2 checkpoints (mid-download + final)
        assert len(checkpoint_offsets) >= 2
        assert checkpoint_offsets[-1] == len(large_data)
    finally:
        server.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# Tests: DownloadManager init creates dir
# ═══════════════════════════════════════════════════════════════════════


def test_manager_creates_downloads_dir(
    ref_engine: sa.Engine,
    tmp_path: Path,
) -> None:
    """DownloadManager creates downloads_dir if it doesn't exist."""
    new_dir = tmp_path / "new" / "nested" / "downloads"
    assert not new_dir.exists()

    DownloadManager(ref_engine, new_dir)
    assert new_dir.exists()


# ═══════════════════════════════════════════════════════════════════════
# Tests: job records
# ═══════════════════════════════════════════════════════════════════════


def test_job_type_is_download(
    manager: DownloadManager,
    ref_engine: sa.Engine,
    http_server: HTTPServer,
) -> None:
    """Job records created by download manager have job_type='download'."""
    url = server_url(http_server)
    result = manager.start(url, "job_type_test.db")

    with ref_engine.connect() as conn:
        job = conn.execute(
            sa.select(jobs.c.job_type).where(jobs.c.job_id == result.job_id)
        ).fetchone()
    assert job is not None
    assert job.job_type == "download"


# ═══════════════════════════════════════════════════════════════════════
# Tests: durable If-Range validator persistence (PR-15)
# ═══════════════════════════════════════════════════════════════════════


class _EtagHTTPHandler(BaseHTTPRequestHandler):
    """Serves the body with a fixed ETag (for validator-persistence tests)."""

    data = TEST_DATA
    etag = '"etag-xyz"'

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("ETag", self.etag)
        self.send_header("Content-Length", str(len(self.data)))
        self.end_headers()
        self.wfile.write(self.data)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


def test_validator_persisted_on_download(
    manager: DownloadManager,
    ref_engine: sa.Engine,
) -> None:
    """The captured ETag lands on the downloads row, so a later cross-process
    resume can seed it as If-Range."""
    server = HTTPServer(("127.0.0.1", 0), _EtagHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        url = f"http://{host}:{port}/etagfile.db"
        result = manager.start(url, "etagfile.db")
        assert result.error is None
        with ref_engine.connect() as conn:
            row = conn.execute(
                sa.select(downloads.c.validator).where(downloads.c.id == result.download_id)
            ).fetchone()
        assert row.validator == '"etag-xyz"'
    finally:
        server.shutdown()


def test_download_succeeds_when_validator_column_absent(
    ref_engine: sa.Engine,
    dl_dir: Path,
) -> None:
    """A DownloadManager on a not-yet-backfilled reference.db (no ``validator``
    column — e.g. the Huey consumer's raw engine before the API lifespan ran the
    backfill) must complete the download, not crash on the validator query."""
    # Recreate downloads WITHOUT the validator column (pre-PR-15 shape).
    with ref_engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE downloads"))
        conn.execute(
            sa.text(
                "CREATE TABLE downloads ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  url TEXT NOT NULL,"
                "  dest_path TEXT NOT NULL,"
                "  total_bytes INTEGER,"
                "  downloaded_bytes INTEGER DEFAULT 0,"
                "  checksum_sha256 TEXT,"
                "  status TEXT DEFAULT 'pending',"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  updated_at DATETIME"
                ")"
            )
        )

    manager = DownloadManager(ref_engine, dl_dir, sleep=lambda _delay: None)
    assert manager._validator_supported is False

    server = HTTPServer(("127.0.0.1", 0), _EtagHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        url = f"http://{host}:{port}/novalidator.db"
        result = manager.start(url, "novalidator.db")
        assert result.error is None
        # Read degrades to None rather than raising "no such column".
        assert manager._get_validator(result.download_id) is None
    finally:
        server.shutdown()
