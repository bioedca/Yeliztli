"""Tests for bundle update functions (Step 26).

Each of :func:`run_vep_bundle_update`, :func:`run_lai_bundle_update`, and
:func:`run_ancestry_pca_bundle_update` must leave a row in both
``database_versions`` and ``update_history`` after a successful run. The
tests stand up a local HTTP server for each function so the actual
download + sha256 verification path is exercised end-to-end.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import sqlite3
import tarfile
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from backend.config import Settings
from backend.db import manifest as manifest_mod
from backend.db.tables import database_versions, reference_metadata, update_history
from backend.db.update_manager import (
    UpdateResult,
    run_ancestry_pca_bundle_update,
    run_gnomad_bundle_update,
    run_lai_bundle_update,
    run_vep_bundle_update,
)

# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_manifest_cache_and_env(monkeypatch: pytest.MonkeyPatch):
    """Each test starts with an empty manifest cache and no env override."""
    monkeypatch.delenv(manifest_mod.MANIFEST_PATH_ENV, raising=False)
    manifest_mod.reset_cache()
    yield
    manifest_mod.reset_cache()


@pytest.fixture
def data_dir_with_ref(tmp_path: Path) -> Path:
    """tmp ``data_dir`` with an empty reference.db (all tables created)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "downloads").mkdir()
    engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    reference_metadata.create_all(engine)
    engine.dispose()
    return data_dir


@pytest.fixture
def serve_payload() -> Callable[[bytes], str]:
    """Factory: call ``serve_payload(bytes)`` to spin up an HTTP server.

    The server supports plain GET and Range requests so it can be reused
    by the direct httpx path (``run_vep_bundle_update``) and the
    DownloadManager-driven paths (LAI / PCA).
    """
    servers: list[HTTPServer] = []

    def _make(payload: bytes) -> str:
        def _handler_factory(*args: Any, **kwargs: Any) -> BaseHTTPRequestHandler:
            return _PayloadHandler(payload, *args, **kwargs)

        server = HTTPServer(("127.0.0.1", 0), _handler_factory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        host, port = server.server_address
        return f"http://{host}:{port}/payload"

    yield _make

    for srv in servers:
        srv.shutdown()


class _PayloadHandler(BaseHTTPRequestHandler):
    """Range-aware HTTP handler that serves a fixed in-memory payload."""

    def __init__(self, payload: bytes, *args: Any, **kwargs: Any) -> None:
        self._payload = payload
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        range_header = self.headers.get("Range")
        if range_header:
            _, spec = range_header.split("=", 1)
            start = int(spec.rstrip("-").split("-")[0])
            end = len(self._payload)
            if start >= end:
                self.send_response(416)
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end - 1}/{len(self._payload)}")
            self.send_header("Content-Length", str(end - start))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(self._payload[start:end])
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(self._payload)))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(self._payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return None


def _write_manifest(tmp_path: Path, bundles: dict) -> Path:
    payload = {
        "schema_version": 1,
        "generated_at": "2026-05-08T00:00:00Z",
        "bundles": bundles,
        "pipeline_pins": {},
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _query_one(ref_path: Path, table: sa.Table, db_name: str) -> Any:
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    try:
        with engine.connect() as conn:
            return conn.execute(sa.select(table).where(table.c.db_name == db_name)).fetchone()
    finally:
        engine.dispose()


def _query_all(ref_path: Path, table: sa.Table, db_name: str) -> list:
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    try:
        with engine.connect() as conn:
            return conn.execute(sa.select(table).where(table.c.db_name == db_name)).fetchall()
    finally:
        engine.dispose()


# ──────────────────────────────────────────────────────────────────────
# Payload builders
# ──────────────────────────────────────────────────────────────────────


def _build_minimal_vep_bundle(build_date: str = "2026-05-01") -> bytes:
    """Return a SQLite bundle file with a ``bundle_metadata`` table."""
    path = Path(__import__("tempfile").mkstemp(suffix=".db")[1])
    try:
        with sqlite3.connect(str(path)) as conn:
            conn.execute("CREATE TABLE bundle_metadata (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute(
                "INSERT INTO bundle_metadata (key, value) VALUES (?, ?)",
                ("build_date", build_date),
            )
            conn.commit()
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


def _build_minimal_lai_tarball() -> bytes:
    """In-memory tarball with the 22-chromosome gnomix_models layout."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for chrom in range(1, 23):
            for fname in ("base_coefs.npz", "metadata.npz", "smoother.json"):
                info = tarfile.TarInfo(name=f"gnomix_models/chr{chrom}/{fname}")
                data = b"test"
                info.size = len(data)
                tf.addfile(info, fileobj=io.BytesIO(data))
    return buf.getvalue()


# ──────────────────────────────────────────────────────────────────────
# run_vep_bundle_update
# ──────────────────────────────────────────────────────────────────────


class TestRunVepBundleUpdate:
    def test_writes_database_versions_and_update_history(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        data_dir_with_ref: Path,
        serve_payload: Callable[[bytes], str],
    ) -> None:
        payload = _build_minimal_vep_bundle(build_date="2026-05-01")
        url = serve_payload(payload)
        sha = hashlib.sha256(payload).hexdigest()

        manifest_path = _write_manifest(
            tmp_path,
            {
                "vep_bundle": {
                    "version": "v2.0",
                    "build_date": "2026-05-01",
                    "url": url,
                    "sha256": sha,
                    "size_bytes": len(payload),
                },
            },
        )
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        # Redirect the in-repo bundled copy so the real bundles/ dir is untouched.
        fake_bundled = tmp_path / "bundled"
        fake_bundled.mkdir()
        from backend.db import database_registry as registry_mod

        monkeypatch.setattr(registry_mod, "BUNDLED_DIR", fake_bundled)

        settings = Settings(data_dir=data_dir_with_ref, wal_mode=False)
        result = run_vep_bundle_update(settings)

        assert isinstance(result, UpdateResult)
        assert result.db_name == "vep_bundle"
        # Plan §5.5: manifest semver is the authoritative new_version (not the
        # bundle's build_date).
        assert result.new_version == "v2.0"
        assert result.download_size_bytes == len(payload)

        ref_path = data_dir_with_ref / "reference.db"

        version_row = _query_one(ref_path, database_versions, "vep_bundle")
        assert version_row is not None
        assert version_row.version == "v2.0"
        assert version_row.checksum_sha256 == sha
        assert version_row.file_size_bytes == len(payload)

        history = _query_all(ref_path, update_history, "vep_bundle")
        assert len(history) == 1
        assert history[0].new_version == "v2.0"
        assert history[0].previous_version is None
        assert history[0].download_size_bytes == len(payload)

        # The bundled copy is mirrored into the patched in-repo directory.
        assert (fake_bundled / "vep_bundle.db").exists()

    def test_checksum_mismatch_returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        data_dir_with_ref: Path,
        serve_payload: Callable[[bytes], str],
    ) -> None:
        payload = _build_minimal_vep_bundle()
        url = serve_payload(payload)

        manifest_path = _write_manifest(
            tmp_path,
            {
                "vep_bundle": {
                    "version": "v2.0",
                    "build_date": "2026-05-01",
                    "url": url,
                    "sha256": "f" * 64,  # deliberately wrong
                    "size_bytes": len(payload),
                },
            },
        )
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        fake_bundled = tmp_path / "bundled"
        fake_bundled.mkdir()
        from backend.db import database_registry as registry_mod

        monkeypatch.setattr(registry_mod, "BUNDLED_DIR", fake_bundled)

        settings = Settings(data_dir=data_dir_with_ref, wal_mode=False)
        assert run_vep_bundle_update(settings) is None

        ref_path = data_dir_with_ref / "reference.db"
        assert _query_one(ref_path, database_versions, "vep_bundle") is None
        assert _query_all(ref_path, update_history, "vep_bundle") == []

    def test_records_previous_version_from_database_versions(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        data_dir_with_ref: Path,
        serve_payload: Callable[[bytes], str],
    ) -> None:
        # Seed a prior version row so the history delta is captured.
        ref_path = data_dir_with_ref / "reference.db"
        seed_engine = sa.create_engine(f"sqlite:///{ref_path}")
        try:
            with seed_engine.begin() as conn:
                conn.execute(
                    database_versions.insert().values(
                        db_name="vep_bundle",
                        version="2026-04-01",
                        file_size_bytes=1,
                        checksum_sha256=None,
                    )
                )
        finally:
            seed_engine.dispose()

        payload = _build_minimal_vep_bundle(build_date="2026-05-01")
        url = serve_payload(payload)
        sha = hashlib.sha256(payload).hexdigest()

        manifest_path = _write_manifest(
            tmp_path,
            {
                "vep_bundle": {
                    "version": "v2.0",
                    "build_date": "2026-05-01",
                    "url": url,
                    "sha256": sha,
                    "size_bytes": len(payload),
                },
            },
        )
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        fake_bundled = tmp_path / "bundled"
        fake_bundled.mkdir()
        from backend.db import database_registry as registry_mod

        monkeypatch.setattr(registry_mod, "BUNDLED_DIR", fake_bundled)

        settings = Settings(data_dir=data_dir_with_ref, wal_mode=False)
        result = run_vep_bundle_update(settings)

        assert result is not None
        assert result.previous_version == "2026-04-01"
        history = _query_all(ref_path, update_history, "vep_bundle")
        assert len(history) == 1
        assert history[0].previous_version == "2026-04-01"
        # Manifest semver is the new_version (Plan §5.5).
        assert history[0].new_version == "v2.0"

    def test_returns_none_when_remote_payload_missing_metadata(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        data_dir_with_ref: Path,
        serve_payload: Callable[[bytes], str],
    ) -> None:
        """SQLite with bundle_metadata table but no build_date → no rows written."""
        bad_path = Path(__import__("tempfile").mkstemp(suffix=".db")[1])
        try:
            with sqlite3.connect(str(bad_path)) as conn:
                conn.execute("CREATE TABLE bundle_metadata (key TEXT PRIMARY KEY, value TEXT)")
                # Deliberately omit the build_date row.
                conn.commit()
            payload = bad_path.read_bytes()
        finally:
            bad_path.unlink(missing_ok=True)

        url = serve_payload(payload)
        sha = hashlib.sha256(payload).hexdigest()
        manifest_path = _write_manifest(
            tmp_path,
            {
                "vep_bundle": {
                    "version": "v2.0",
                    "build_date": "2026-05-01",
                    "url": url,
                    "sha256": sha,
                    "size_bytes": len(payload),
                },
            },
        )
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        fake_bundled = tmp_path / "bundled"
        fake_bundled.mkdir()
        from backend.db import database_registry as registry_mod

        monkeypatch.setattr(registry_mod, "BUNDLED_DIR", fake_bundled)

        settings = Settings(data_dir=data_dir_with_ref, wal_mode=False)
        assert run_vep_bundle_update(settings) is None

        ref_path = data_dir_with_ref / "reference.db"
        assert _query_one(ref_path, database_versions, "vep_bundle") is None
        assert _query_all(ref_path, update_history, "vep_bundle") == []

    def test_previous_version_falls_back_to_local_build_date(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        data_dir_with_ref: Path,
        serve_payload: Callable[[bytes], str],
    ) -> None:
        """No database_versions row + local bundle file → previous_version derived from SQLite."""
        # Pre-stage a local bundle with an older build_date.
        local_bundle = data_dir_with_ref / "vep_bundle.db"
        with sqlite3.connect(str(local_bundle)) as conn:
            conn.execute("CREATE TABLE bundle_metadata (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute(
                "INSERT INTO bundle_metadata (key, value) VALUES (?, ?)",
                ("build_date", "2026-03-15"),
            )
            conn.commit()

        payload = _build_minimal_vep_bundle(build_date="2026-05-01")
        url = serve_payload(payload)
        sha = hashlib.sha256(payload).hexdigest()

        manifest_path = _write_manifest(
            tmp_path,
            {
                "vep_bundle": {
                    "version": "v2.0",
                    "build_date": "2026-05-01",
                    "url": url,
                    "sha256": sha,
                    "size_bytes": len(payload),
                },
            },
        )
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        fake_bundled = tmp_path / "bundled"
        fake_bundled.mkdir()
        from backend.db import database_registry as registry_mod

        monkeypatch.setattr(registry_mod, "BUNDLED_DIR", fake_bundled)

        settings = Settings(data_dir=data_dir_with_ref, wal_mode=False)
        result = run_vep_bundle_update(settings)

        assert result is not None
        assert result.previous_version == "2026-03-15"
        history = _query_all(data_dir_with_ref / "reference.db", update_history, "vep_bundle")
        assert len(history) == 1
        assert history[0].previous_version == "2026-03-15"
        # Manifest semver is the new_version (Plan §5.5).
        assert history[0].new_version == "v2.0"

    def test_returns_none_on_network_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        data_dir_with_ref: Path,
    ) -> None:
        """Unreachable URL → no rows written, function returns None."""
        # Bind a socket to grab an unused port, then close so the URL refuses.
        import socket

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            unused_port = s.getsockname()[1]
        unreachable_url = f"http://127.0.0.1:{unused_port}/vep.db"

        manifest_path = _write_manifest(
            tmp_path,
            {
                "vep_bundle": {
                    "version": "v2.0",
                    "build_date": "2026-05-01",
                    "url": unreachable_url,
                    "sha256": "a" * 64,
                    "size_bytes": 1,
                },
            },
        )
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        fake_bundled = tmp_path / "bundled"
        fake_bundled.mkdir()
        from backend.db import database_registry as registry_mod

        monkeypatch.setattr(registry_mod, "BUNDLED_DIR", fake_bundled)

        settings = Settings(data_dir=data_dir_with_ref, wal_mode=False)
        assert run_vep_bundle_update(settings, timeout=2.0) is None

        ref_path = data_dir_with_ref / "reference.db"
        assert _query_one(ref_path, database_versions, "vep_bundle") is None


# ──────────────────────────────────────────────────────────────────────
# run_lai_bundle_update
# ──────────────────────────────────────────────────────────────────────


class TestRunLaiBundleUpdate:
    def test_writes_database_versions_and_update_history(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        data_dir_with_ref: Path,
        serve_payload: Callable[[bytes], str],
    ) -> None:
        payload = _build_minimal_lai_tarball()
        url = serve_payload(payload)
        sha = hashlib.sha256(payload).hexdigest()

        manifest_path = _write_manifest(
            tmp_path,
            {
                "lai_bundle": {
                    "version": "v1.1",
                    "build_date": "2026-04-07",
                    "url": url,
                    "sha256": sha,
                    "size_bytes": len(payload),
                },
            },
        )
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        settings = Settings(data_dir=data_dir_with_ref, wal_mode=False)
        result = run_lai_bundle_update(settings)

        assert isinstance(result, UpdateResult)
        assert result.db_name == "lai_bundle"
        assert result.new_version == "v1.1"
        assert result.download_size_bytes == len(payload)

        # Bundle was extracted into the data dir.
        bundle_dir = data_dir_with_ref / "lai_bundle"
        assert bundle_dir.is_dir()
        assert (bundle_dir / "gnomix_models" / "chr1" / "smoother.json").exists()
        # The downloaded tarball is removed after extraction.
        assert not (settings.downloads_dir / "lai_bundle.tar.gz").exists()

        ref_path = data_dir_with_ref / "reference.db"
        version_row = _query_one(ref_path, database_versions, "lai_bundle")
        assert version_row is not None
        assert version_row.version == "v1.1"
        assert version_row.checksum_sha256 == sha
        # Extracted size = 22 chroms × 3 files × 4 bytes
        assert version_row.file_size_bytes == 22 * 3 * 4

        history = _query_all(ref_path, update_history, "lai_bundle")
        assert len(history) == 1
        assert history[0].new_version == "v1.1"
        assert history[0].download_size_bytes == len(payload)
        assert history[0].previous_version is None

    def test_returns_none_when_manifest_missing_entry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        data_dir_with_ref: Path,
    ) -> None:
        manifest_path = _write_manifest(tmp_path, {})
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        settings = Settings(data_dir=data_dir_with_ref, wal_mode=False)
        assert run_lai_bundle_update(settings) is None

        ref_path = data_dir_with_ref / "reference.db"
        assert _query_one(ref_path, database_versions, "lai_bundle") is None
        assert _query_all(ref_path, update_history, "lai_bundle") == []

    def test_returns_none_when_reference_db_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        serve_payload: Callable[[bytes], str],
    ) -> None:
        payload = _build_minimal_lai_tarball()
        url = serve_payload(payload)
        sha = hashlib.sha256(payload).hexdigest()
        manifest_path = _write_manifest(
            tmp_path,
            {
                "lai_bundle": {
                    "version": "v1.1",
                    "build_date": "2026-04-07",
                    "url": url,
                    "sha256": sha,
                    "size_bytes": len(payload),
                },
            },
        )
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        data_dir = tmp_path / "empty_data"
        data_dir.mkdir()
        settings = Settings(data_dir=data_dir, wal_mode=False)

        assert run_lai_bundle_update(settings) is None

    def test_returns_none_when_tarball_is_invalid(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        data_dir_with_ref: Path,
        serve_payload: Callable[[bytes], str],
    ) -> None:
        """A tarball missing chromosome models fails extraction → no rows written."""
        # Valid tarball but only one chromosome — _extract_lai_bundle's validator
        # will raise ValueError listing the missing files.
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo(name="gnomix_models/chr1/smoother.json")
            data = b"test"
            info.size = len(data)
            tf.addfile(info, fileobj=io.BytesIO(data))
        payload = buf.getvalue()

        url = serve_payload(payload)
        sha = hashlib.sha256(payload).hexdigest()
        manifest_path = _write_manifest(
            tmp_path,
            {
                "lai_bundle": {
                    "version": "v1.1",
                    "build_date": "2026-04-07",
                    "url": url,
                    "sha256": sha,
                    "size_bytes": len(payload),
                },
            },
        )
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        settings = Settings(data_dir=data_dir_with_ref, wal_mode=False)
        assert run_lai_bundle_update(settings) is None

        ref_path = data_dir_with_ref / "reference.db"
        # No history row — extraction failed before we could record it.
        assert _query_all(ref_path, update_history, "lai_bundle") == []


# ──────────────────────────────────────────────────────────────────────
# run_ancestry_pca_bundle_update
# ──────────────────────────────────────────────────────────────────────


class TestRunAncestryPcaBundleUpdate:
    def test_writes_database_versions_and_update_history(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        data_dir_with_ref: Path,
        serve_payload: Callable[[bytes], str],
    ) -> None:
        # The .npz contents are opaque to the update function — any bytes work
        # because we don't load the file, only stage + record it.
        payload = b"NPZ\x00fake-bundle-content" * 32
        url = serve_payload(payload)
        sha = hashlib.sha256(payload).hexdigest()

        manifest_path = _write_manifest(
            tmp_path,
            {
                "ancestry_pca": {
                    "version": "v1.1",
                    "build_date": "2026-05-01",
                    "url": url,
                    "sha256": sha,
                    "size_bytes": len(payload),
                },
            },
        )
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        settings = Settings(data_dir=data_dir_with_ref, wal_mode=False)
        result = run_ancestry_pca_bundle_update(settings)

        assert isinstance(result, UpdateResult)
        assert result.db_name == "ancestry_pca"
        assert result.new_version == "v1.1"
        assert result.download_size_bytes == len(payload)

        # File now lives at data_dir/ancestry_pca_bundle.npz.
        dest = data_dir_with_ref / "ancestry_pca_bundle.npz"
        assert dest.exists()
        assert dest.read_bytes() == payload

        ref_path = data_dir_with_ref / "reference.db"
        version_row = _query_one(ref_path, database_versions, "ancestry_pca")
        assert version_row is not None
        assert version_row.version == "v1.1"
        assert version_row.checksum_sha256 == sha
        assert version_row.file_size_bytes == len(payload)

        history = _query_all(ref_path, update_history, "ancestry_pca")
        assert len(history) == 1
        assert history[0].new_version == "v1.1"
        assert history[0].download_size_bytes == len(payload)

    def test_returns_none_when_manifest_has_no_url(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        data_dir_with_ref: Path,
    ) -> None:
        manifest_path = _write_manifest(
            tmp_path,
            {
                "ancestry_pca": {
                    "version": "v1.0",
                    "build_date": "2026-04-07",
                    "url": "",
                    "sha256": "a" * 64,
                    "size_bytes": 414_432,
                },
            },
        )
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        settings = Settings(data_dir=data_dir_with_ref, wal_mode=False)
        assert run_ancestry_pca_bundle_update(settings) is None

        ref_path = data_dir_with_ref / "reference.db"
        assert _query_one(ref_path, database_versions, "ancestry_pca") is None
        assert _query_all(ref_path, update_history, "ancestry_pca") == []

    def test_returns_none_when_manifest_missing_entry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        data_dir_with_ref: Path,
    ) -> None:
        manifest_path = _write_manifest(tmp_path, {})
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        settings = Settings(data_dir=data_dir_with_ref, wal_mode=False)
        assert run_ancestry_pca_bundle_update(settings) is None

    def test_returns_none_on_checksum_mismatch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        data_dir_with_ref: Path,
        serve_payload: Callable[[bytes], str],
    ) -> None:
        """DownloadManager raises on bad sha256; the bundle wrapper swallows it."""
        payload = b"some-bytes" * 16
        url = serve_payload(payload)
        # Deliberately wrong sha256.
        manifest_path = _write_manifest(
            tmp_path,
            {
                "ancestry_pca": {
                    "version": "v1.1",
                    "build_date": "2026-05-01",
                    "url": url,
                    "sha256": "0" * 64,
                    "size_bytes": len(payload),
                },
            },
        )
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        settings = Settings(data_dir=data_dir_with_ref, wal_mode=False)
        assert run_ancestry_pca_bundle_update(settings) is None

        ref_path = data_dir_with_ref / "reference.db"
        assert _query_one(ref_path, database_versions, "ancestry_pca") is None
        assert _query_all(ref_path, update_history, "ancestry_pca") == []


# ──────────────────────────────────────────────────────────────────────
# run_gnomad_bundle_update
# ──────────────────────────────────────────────────────────────────────


class TestRunGnomadBundleUpdate:
    def test_writes_database_versions_and_update_history(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        data_dir_with_ref: Path,
        serve_payload: Callable[[bytes], str],
    ) -> None:
        # The hosted gnomAD asset is gzip-compressed to fit GitHub's release
        # asset limit; the runner installs the decompressed SQLite file.
        payload = b"SQLite format 3\x00fake-gnomad-af-db" * 64
        compressed_payload = gzip.compress(payload)
        url = serve_payload(compressed_payload) + "/gnomad_af.db.gz"
        sha = hashlib.sha256(compressed_payload).hexdigest()

        manifest_path = _write_manifest(
            tmp_path,
            {
                "gnomad": {
                    "version": "v1.0.0",
                    "build_date": "2026-06-06",
                    "url": url,
                    "sha256": sha,
                    "size_bytes": len(payload),
                },
            },
        )
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        settings = Settings(data_dir=data_dir_with_ref, wal_mode=False)
        result = run_gnomad_bundle_update(settings)

        assert isinstance(result, UpdateResult)
        assert result.db_name == "gnomad"
        assert result.new_version == "v1.0.0"
        assert result.download_size_bytes == len(compressed_payload)

        # File now lives at data_dir/gnomad_af.db (standalone).
        dest = data_dir_with_ref / "gnomad_af.db"
        assert dest.exists()
        assert dest.read_bytes() == payload

        ref_path = data_dir_with_ref / "reference.db"
        version_row = _query_one(ref_path, database_versions, "gnomad")
        assert version_row is not None
        assert version_row.version == "v1.0.0"
        assert version_row.checksum_sha256 == sha
        assert version_row.file_size_bytes == len(payload)

        history = _query_all(ref_path, update_history, "gnomad")
        assert len(history) == 1
        assert history[0].new_version == "v1.0.0"
        assert history[0].download_size_bytes == len(compressed_payload)
        assert history[0].previous_version is None

    def test_returns_none_when_manifest_missing_entry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        data_dir_with_ref: Path,
    ) -> None:
        """Deferred state: no bundles['gnomad'] entry → no-op, no rows written."""
        manifest_path = _write_manifest(tmp_path, {})
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        settings = Settings(data_dir=data_dir_with_ref, wal_mode=False)
        assert run_gnomad_bundle_update(settings) is None

        ref_path = data_dir_with_ref / "reference.db"
        assert _query_one(ref_path, database_versions, "gnomad") is None
        assert _query_all(ref_path, update_history, "gnomad") == []

    def test_returns_none_when_manifest_has_no_url(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        data_dir_with_ref: Path,
    ) -> None:
        manifest_path = _write_manifest(
            tmp_path,
            {
                "gnomad": {
                    "version": "v1.0.0",
                    "build_date": "2026-06-06",
                    "url": "",
                    "sha256": "a" * 64,
                    "size_bytes": 2_000_000_000,
                },
            },
        )
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        settings = Settings(data_dir=data_dir_with_ref, wal_mode=False)
        assert run_gnomad_bundle_update(settings) is None

        ref_path = data_dir_with_ref / "reference.db"
        assert _query_one(ref_path, database_versions, "gnomad") is None
        assert _query_all(ref_path, update_history, "gnomad") == []

    def test_returns_none_on_checksum_mismatch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        data_dir_with_ref: Path,
        serve_payload: Callable[[bytes], str],
    ) -> None:
        """DownloadManager raises on bad sha256; the bundle wrapper swallows it."""
        payload = b"gnomad-bytes" * 16
        url = serve_payload(payload)
        manifest_path = _write_manifest(
            tmp_path,
            {
                "gnomad": {
                    "version": "v1.0.0",
                    "build_date": "2026-06-06",
                    "url": url,
                    "sha256": "0" * 64,  # deliberately wrong
                    "size_bytes": len(payload),
                },
            },
        )
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        settings = Settings(data_dir=data_dir_with_ref, wal_mode=False)
        assert run_gnomad_bundle_update(settings) is None

        ref_path = data_dir_with_ref / "reference.db"
        assert _query_one(ref_path, database_versions, "gnomad") is None
        assert _query_all(ref_path, update_history, "gnomad") == []

    def test_returns_none_when_reference_db_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        serve_payload: Callable[[bytes], str],
    ) -> None:
        payload = b"SQLite format 3\x00fake" * 16
        url = serve_payload(payload)
        sha = hashlib.sha256(payload).hexdigest()
        manifest_path = _write_manifest(
            tmp_path,
            {
                "gnomad": {
                    "version": "v1.0.0",
                    "build_date": "2026-06-06",
                    "url": url,
                    "sha256": sha,
                    "size_bytes": len(payload),
                },
            },
        )
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

        data_dir = tmp_path / "empty_data"
        data_dir.mkdir()
        settings = Settings(data_dir=data_dir, wal_mode=False)

        assert run_gnomad_bundle_update(settings) is None
