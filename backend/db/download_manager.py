"""Resumable HTTP download manager with SHA-256 verification and SSE progress.

Downloads large database files using HTTP Range requests so interrupted
transfers can resume from the last checkpointed byte offset. Progress is
tracked in the ``downloads`` and ``jobs`` tables in reference.db, enabling
SSE streaming to the frontend via the existing job progress infrastructure.

Usage::

    from backend.db.download_manager import DownloadManager

    dm = DownloadManager(reference_engine, downloads_dir)
    job_id = dm.start("https://example.com/big.db", "big.db",
                       expected_sha256="abc123...")
    # Poll job_id via SSE for progress.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
import structlog

from backend.annotation.http_download import stream_download
from backend.db.tables import downloads, jobs

if TYPE_CHECKING:
    from collections.abc import Callable

logger = structlog.get_logger(__name__)

# How often to flush byte-offset to the downloads table (bytes).
CHECKPOINT_INTERVAL = 1_048_576  # 1 MiB

# Default chunk size for streaming reads.
CHUNK_SIZE = 65_536  # 64 KiB

# Default HTTP timeout (connect / total).
DEFAULT_CONNECT_TIMEOUT = 30.0
DEFAULT_TOTAL_TIMEOUT = 3600.0  # 1 hour for large files


@dataclass
class DownloadResult:
    """Outcome of a completed (or failed) download."""

    download_id: int
    job_id: str
    dest_path: Path
    total_bytes: int
    sha256: str | None = None
    verified: bool = False
    error: str | None = None


class DownloadError(Exception):
    """Raised when a download fails irrecoverably."""


class ChecksumMismatchError(DownloadError):
    """Raised when the SHA-256 of the downloaded file doesn't match."""


class DownloadManager:
    """Manages resumable file downloads with progress tracking.

    Each download creates a row in the ``downloads`` table (byte-offset
    checkpoint) and a corresponding row in the ``jobs`` table (SSE-visible
    progress).  If a download is interrupted, calling :meth:`resume` with
    the same ``download_id`` picks up from the last checkpoint.

    Args:
        engine: SQLAlchemy engine for reference.db.
        downloads_dir: Directory where downloaded files are stored.
    """

    def __init__(
        self,
        engine: sa.Engine,
        downloads_dir: Path,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._engine = engine
        self._downloads_dir = downloads_dir
        self._downloads_dir.mkdir(parents=True, exist_ok=True)
        # Injectable for tests so retry backoff doesn't sleep for real.
        self._sleep = sleep
        # The durable-If-Range ``validator`` column is added by the reference-schema
        # backfill, which runs in the API lifespan — but a DownloadManager can be
        # built on a raw engine in the Huey consumer (bundle auto-updates) whose
        # shared reference.db may not be backfilled yet. Probe once so the
        # validator read/write degrade to a no-op there instead of raising
        # "no such column" mid-download; the column appears (and full behaviour
        # resumes) for the next manager once any backfill has run on the file.
        self._validator_supported = self._detect_validator_column()

    def _detect_validator_column(self) -> bool:
        try:
            cols = {c["name"] for c in sa.inspect(self._engine).get_columns("downloads")}
        except Exception:  # noqa: BLE001 - missing table / inspect failure → unsupported
            return False
        return "validator" in cols

    # ── Public API ────────────────────────────────────────────────────

    def start(
        self,
        url: str,
        filename: str,
        *,
        expected_sha256: str | None = None,
        progress_callback: Callable[[int, int | None], None] | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        total_timeout: float = DEFAULT_TOTAL_TIMEOUT,
    ) -> DownloadResult:
        """Start a new download (or resume if a pending record exists).

        Args:
            url: Remote URL to download.
            filename: Local filename (relative to downloads_dir).
            expected_sha256: Optional expected SHA-256 hex digest.
            progress_callback: Called with ``(downloaded_bytes, total_bytes)``.
            connect_timeout: HTTP connect timeout in seconds.
            total_timeout: HTTP total timeout in seconds.

        Returns:
            DownloadResult describing the outcome.
        """
        dest_path = self._downloads_dir / filename
        job_id = f"dl-{uuid.uuid4().hex[:12]}"

        # Check for an existing incomplete download of the same URL+dest
        existing = self._find_resumable(url, str(dest_path))
        if existing is not None:
            download_id, downloaded_bytes = existing
            logger.info(
                "download_resume_existing",
                download_id=download_id,
                url=url,
                offset=downloaded_bytes,
            )
        else:
            download_id = self._create_download_record(
                url=url,
                dest_path=str(dest_path),
                expected_sha256=expected_sha256,
            )
            downloaded_bytes = 0

        # Create job for SSE progress tracking
        self._create_job(job_id, download_id)

        return self._execute_download(
            download_id=download_id,
            job_id=job_id,
            url=url,
            dest_path=dest_path,
            offset=downloaded_bytes,
            expected_sha256=expected_sha256,
            progress_callback=progress_callback,
            connect_timeout=connect_timeout,
            total_timeout=total_timeout,
        )

    def resume(
        self,
        download_id: int,
        *,
        progress_callback: Callable[[int, int | None], None] | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        total_timeout: float = DEFAULT_TOTAL_TIMEOUT,
    ) -> DownloadResult:
        """Resume an interrupted download by its ID.

        Args:
            download_id: Primary key in the downloads table.
            progress_callback: Called with ``(downloaded_bytes, total_bytes)``.
            connect_timeout: HTTP connect timeout in seconds.
            total_timeout: HTTP total timeout in seconds.

        Returns:
            DownloadResult describing the outcome.

        Raises:
            DownloadError: If the download record is not found or already complete.
        """
        record = self._get_download_record(download_id)
        if record is None:
            raise DownloadError(f"Download {download_id} not found")
        if record["status"] == "complete":
            raise DownloadError(f"Download {download_id} is already complete")

        job_id = f"dl-{uuid.uuid4().hex[:12]}"
        self._create_job(job_id, download_id)

        logger.info(
            "download_resume",
            download_id=download_id,
            url=record["url"],
            offset=record["downloaded_bytes"],
        )

        return self._execute_download(
            download_id=download_id,
            job_id=job_id,
            url=record["url"],
            dest_path=Path(record["dest_path"]),
            offset=record["downloaded_bytes"] or 0,
            expected_sha256=record["checksum_sha256"],
            progress_callback=progress_callback,
            connect_timeout=connect_timeout,
            total_timeout=total_timeout,
        )

    def get_status(self, download_id: int) -> dict | None:
        """Return current status of a download record, or None."""
        return self._get_download_record(download_id)

    # ── Internal: download execution ──────────────────────────────────

    def _execute_download(
        self,
        *,
        download_id: int,
        job_id: str,
        url: str,
        dest_path: Path,
        offset: int,
        expected_sha256: str | None,
        progress_callback: Callable[[int, int | None], None] | None,
        connect_timeout: float,
        total_timeout: float,
    ) -> DownloadResult:
        """Core download loop: resilient streaming with Range resume, checkpoint, verify."""
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")

        # Update status to downloading
        self._update_download_status(download_id, "downloading")
        self._update_job(job_id, status="running", progress_pct=0.0, message="Starting download")

        # Resume only from a real partial; ignore a stale DB offset with no file.
        if not tmp_path.exists():
            offset = 0

        state = {"last_sse": 0.0, "last_checkpoint": offset, "total_set": False}

        def _on_progress(written: int, total: int | None) -> None:
            # Forward to the external caller's callback.
            if progress_callback is not None:
                progress_callback(written, total)
            # Persist the advertised total once (drives the SSE percentage).
            if total and not state["total_set"]:
                self._update_total_bytes(download_id, total)
                state["total_set"] = True
            # Checkpoint the byte offset periodically for cross-process resume.
            # A forced full restart resets the file size, so track regressions to
            # keep the checkpoint cadence correct (the real resume offset always
            # comes from the file size, not this DB value).
            if written < state["last_checkpoint"]:
                state["last_checkpoint"] = written
            if written - state["last_checkpoint"] >= CHECKPOINT_INTERVAL:
                self._checkpoint_offset(download_id, written)
                state["last_checkpoint"] = written
            # Throttled, non-fatal SSE progress update.
            now = time.monotonic()
            if total and total > 0 and now - state["last_sse"] >= 2.0:
                pct = min((written / total) * 100.0, 99.9)
                try:
                    self._update_job(
                        job_id,
                        status="running",
                        progress_pct=pct,
                        message=f"{written:,} / {total:,} bytes",
                    )
                except sa.exc.OperationalError:
                    pass  # progress update is non-critical
                state["last_sse"] = now

        try:
            # resumable=True: keep the partial across calls so a previously
            # interrupted download (tracked in the downloads table) resumes from
            # its checkpointed bytes instead of restarting from zero.
            #
            # Seed the If-Range validator from the row so THIS (possibly
            # cross-process) resume's first Range request is guarded against an
            # upstream that rotated since the partial was written — without it the
            # server could splice new bytes onto a stale partial. on_validator
            # persists the validator the moment it is captured, so even a download
            # that dies mid-transfer leaves one behind for the next resume.
            outcome = stream_download(
                url,
                tmp_path,
                progress_callback=_on_progress,
                timeout=total_timeout,
                connect_timeout=connect_timeout,
                chunk_size=CHUNK_SIZE,
                resumable=True,
                validator=self._get_validator(download_id),
                on_validator=lambda v: self._update_validator(download_id, v),
                sleep=self._sleep,
            )
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            current_offset = tmp_path.stat().st_size if tmp_path.exists() else 0
            self._checkpoint_offset(download_id, current_offset)
            self._update_download_status(download_id, "failed")
            self._update_job(job_id, status="failed", progress_pct=0.0, error=error_msg)
            logger.exception("download_failed", download_id=download_id, url=url, error=error_msg)
            return DownloadResult(
                download_id=download_id,
                job_id=job_id,
                dest_path=dest_path,
                total_bytes=current_offset,
                error=error_msg,
            )

        current_offset = outcome.total_bytes
        # Final checkpoint.
        self._checkpoint_offset(download_id, current_offset)

        # SHA-256 verification
        sha256 = _compute_sha256(tmp_path)
        verified = True
        if expected_sha256 and sha256 != expected_sha256:
            error_msg = f"SHA-256 mismatch: expected {expected_sha256}, got {sha256}"
            self._update_download_status(download_id, "failed")
            self._update_job(job_id, status="failed", progress_pct=100.0, error=error_msg)
            tmp_path.unlink(missing_ok=True)
            logger.error("download_checksum_mismatch", download_id=download_id, url=url)
            raise ChecksumMismatchError(error_msg)

        # Atomic rename on success. Guard it so a filesystem error here doesn't
        # leave the record stuck in "downloading" with no result returned.
        try:
            tmp_path.replace(dest_path)
        except OSError as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            self._update_download_status(download_id, "failed")
            self._update_job(job_id, status="failed", progress_pct=100.0, error=error_msg)
            logger.exception("download_finalize_failed", download_id=download_id, error=error_msg)
            return DownloadResult(
                download_id=download_id,
                job_id=job_id,
                dest_path=dest_path,
                total_bytes=current_offset,
                sha256=sha256,
                error=error_msg,
            )

        # Mark complete
        self._update_download_status(download_id, "complete")
        self._update_job(
            job_id,
            status="complete",
            progress_pct=100.0,
            message="Download complete",
        )

        logger.info(
            "download_complete",
            download_id=download_id,
            dest=str(dest_path),
            bytes=current_offset,
            sha256=sha256,
        )

        return DownloadResult(
            download_id=download_id,
            job_id=job_id,
            dest_path=dest_path,
            total_bytes=current_offset,
            sha256=sha256,
            verified=verified,
        )

    # ── Internal: database helpers ────────────────────────────────────

    def _create_download_record(
        self, *, url: str, dest_path: str, expected_sha256: str | None
    ) -> int:
        """Insert a new row into the downloads table and return its ID."""
        now = datetime.now(UTC)
        with self._engine.begin() as conn:
            result = conn.execute(
                downloads.insert().values(
                    url=url,
                    dest_path=dest_path,
                    total_bytes=None,
                    downloaded_bytes=0,
                    checksum_sha256=expected_sha256,
                    status="pending",
                    created_at=now,
                    updated_at=now,
                )
            )
            return result.lastrowid  # type: ignore[return-value]

    def _find_resumable(self, url: str, dest_path: str) -> tuple[int, int] | None:
        """Find an incomplete download for the same URL + dest_path.

        Returns ``(download_id, downloaded_bytes)`` or None.
        """
        with self._engine.connect() as conn:
            row = conn.execute(
                sa.select(downloads.c.id, downloads.c.downloaded_bytes)
                .where(
                    downloads.c.url == url,
                    downloads.c.dest_path == dest_path,
                    downloads.c.status.in_(["pending", "downloading", "failed"]),
                )
                .order_by(downloads.c.created_at.desc())
                .limit(1)
            ).fetchone()
        if row is None:
            return None
        return (row.id, row.downloaded_bytes or 0)

    def _get_download_record(self, download_id: int) -> dict | None:
        """Fetch a download record as a dict."""
        with self._engine.connect() as conn:
            row = conn.execute(
                sa.select(
                    downloads.c.id,
                    downloads.c.url,
                    downloads.c.dest_path,
                    downloads.c.total_bytes,
                    downloads.c.downloaded_bytes,
                    downloads.c.checksum_sha256,
                    downloads.c.status,
                    downloads.c.created_at,
                    downloads.c.updated_at,
                ).where(downloads.c.id == download_id)
            ).fetchone()
        if row is None:
            return None
        return row._asdict()

    def _update_download_status(self, download_id: int, status: str) -> None:
        """Update the status field of a download record."""
        with self._engine.begin() as conn:
            conn.execute(
                downloads.update()
                .where(downloads.c.id == download_id)
                .values(status=status, updated_at=datetime.now(UTC))
            )

    def _update_total_bytes(self, download_id: int, total_bytes: int) -> None:
        """Set the total_bytes for a download record."""
        with self._engine.begin() as conn:
            conn.execute(
                downloads.update()
                .where(downloads.c.id == download_id)
                .values(total_bytes=total_bytes, updated_at=datetime.now(UTC))
            )

    def _checkpoint_offset(self, download_id: int, offset: int) -> None:
        """Persist the current byte offset to the downloads table."""
        with self._engine.begin() as conn:
            conn.execute(
                downloads.update()
                .where(downloads.c.id == download_id)
                .values(downloaded_bytes=offset, updated_at=datetime.now(UTC))
            )

    def _get_validator(self, download_id: int) -> str | None:
        """Return the persisted If-Range validator for a download, if any."""
        if not self._validator_supported:
            return None
        with self._engine.connect() as conn:
            row = conn.execute(
                sa.select(downloads.c.validator).where(downloads.c.id == download_id)
            ).fetchone()
        return row.validator if row else None

    def _update_validator(self, download_id: int, validator: str) -> None:
        """Persist the If-Range validator captured during the transfer.

        Called by ``stream_download`` the moment a validator is (re)captured, so
        a download interrupted mid-stream still leaves a validator for the next
        cross-process resume to send as ``If-Range``. No-ops if the column is not
        present yet (see ``__init__``) — the transfer still completes, just
        without the durable validator until the schema is backfilled.
        """
        if not self._validator_supported:
            return
        with self._engine.begin() as conn:
            conn.execute(
                downloads.update()
                .where(downloads.c.id == download_id)
                .values(validator=validator, updated_at=datetime.now(UTC))
            )

    def _create_job(self, job_id: str, download_id: int) -> None:
        """Create a job record for SSE progress tracking."""
        now = datetime.now(UTC)
        with self._engine.begin() as conn:
            conn.execute(
                jobs.insert().values(
                    job_id=job_id,
                    sample_id=None,
                    job_type="download",
                    status="pending",
                    progress_pct=0.0,
                    message=f"Download #{download_id} queued",
                    created_at=now,
                    updated_at=now,
                )
            )

    def _update_job(
        self,
        job_id: str,
        *,
        status: str,
        progress_pct: float,
        message: str = "",
        error: str | None = None,
        _retries: int = 5,
    ) -> None:
        """Update job progress for SSE visibility with retry on contention."""
        for attempt in range(_retries):
            try:
                with self._engine.begin() as conn:
                    conn.execute(
                        jobs.update()
                        .where(jobs.c.job_id == job_id)
                        .values(
                            status=status,
                            progress_pct=progress_pct,
                            message=message,
                            error=error,
                            updated_at=datetime.now(UTC),
                        )
                    )
                return
            except sa.exc.OperationalError:
                if attempt < _retries - 1:
                    time.sleep(0.1 * (2**attempt))
                else:
                    raise


def _compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()
