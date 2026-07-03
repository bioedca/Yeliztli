"""FastAPI route tests for the DB-health / resume / verify / clean endpoints.

Covers the four endpoints appended to ``backend/api/routes/databases.py`` by the
download-hardening change (backed by ``backend/db/db_health.py``):

    GET  /api/databases/health             — fused per-DB health records
    POST /api/databases/resume             — resume an interrupted download
    POST /api/databases/{db_name}/verify   — deep PRAGMA quick_check
    POST /api/databases/{db_name}/clean    — remove a partial/corrupt artifact

Reuses the exact TestClient + settings/registry override pattern from
``test_databases_api.py`` (patch ``get_settings`` in main / connection /
databases routes, ``reset_registry`` around the client) and the local
Range-capable ``http.server`` pattern from ``test_download_manager.py``. No real
network or real bundles — fast tier (no ``slow`` marker).
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.api.routes.databases import _active_sessions
from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.database_registry import (
    DATABASES,
    _record_db_version,
    get_all_databases,
    get_database,
)
from backend.db.tables import (
    clinvar_variants,
    database_versions,
    download_session_jobs,
    download_sessions,
    downloads,
    jobs,
    reference_metadata,
)

# Every field DatabaseHealthResponse must expose, per the db_health spec.
_HEALTH_FIELDS = {
    "name",
    "display_name",
    "build_mode",
    "required",
    "state",
    "present",
    "version",
    "downloaded_at",
    "file_size_bytes",
    "expected_size_bytes",
    "integrity_ok",
    "integrity_detail",
    "resumable",
    "download_id",
    "downloaded_bytes",
    "total_bytes",
    "progress_pct",
    "active_job_id",
    "last_error",
    "can_clean",
    "can_resume",
    "can_verify",
}

_VALID_STATES = {
    "not_installed",
    "downloading",
    "building",
    "partial",
    "corrupt",
    "ready",
    "failed",
}


# ═══════════════════════════════════════════════════════════════════════
# Local Range-capable HTTP server (mirrors test_download_manager.py)
# ═══════════════════════════════════════════════════════════════════════

TEST_DATA = b"A" * 1024 + b"B" * 1024 + b"C" * 1024  # 3 KiB
TEST_DATA_SHA256 = hashlib.sha256(TEST_DATA).hexdigest()


class RangeHTTPHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler supporting open-ended ``Range`` requests."""

    data = TEST_DATA

    def do_GET(self) -> None:
        range_header = self.headers.get("Range")
        if range_header:
            _, range_spec = range_header.split("=", 1)
            start = int(range_spec.rstrip("-").split("-")[0])
            end = len(self.data)
            if start >= len(self.data):
                self.send_response(416)
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
        pass


@pytest.fixture
def range_server():
    """Local HTTP server supporting Range requests for resume tests."""
    server = HTTPServer(("127.0.0.1", 0), RangeHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def server_url(server: HTTPServer, path: str) -> str:
    host, port = server.server_address
    return f"http://{host}:{port}/{path}"


# ═══════════════════════════════════════════════════════════════════════
# Fixtures (mirror test_databases_api.py db_client / clear_sessions)
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def settings(tmp_data_dir: Path) -> Settings:
    """Settings rooted at the temp data dir, with reference.db pre-created.

    reference.db always exists in a real install; the health/verify/clean code
    treats reference-resident DBs as 'data table holds rows', not 'file exists'.
    """
    s = Settings(data_dir=tmp_data_dir, wal_mode=False)
    engine = sa.create_engine(f"sqlite:///{s.reference_db_path}")
    reference_metadata.create_all(engine)
    engine.dispose()
    return s


@pytest.fixture
def db_client(settings: Settings) -> TestClient:
    """FastAPI TestClient wired to the temp-dir settings + a fresh registry."""
    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.api.routes.databases.get_settings", return_value=settings),
    ):
        reset_registry()
        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc
        reset_registry()


@pytest.fixture(autouse=True)
def clear_sessions():
    """Clear active SSE sessions between tests."""
    _active_sessions.clear()
    yield
    _active_sessions.clear()


# ── helpers ───────────────────────────────────────────────────────────


def _ref_engine(settings: Settings) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{settings.reference_db_path}")


def _seed_clinvar(settings: Settings) -> None:
    """Put a row in clinvar_variants so clinvar reads as ready/valid."""
    engine = _ref_engine(settings)
    with engine.begin() as conn:
        conn.execute(
            clinvar_variants.insert().values(
                rsid="rs1",
                chrom="1",
                pos=100,
                ref="A",
                alt="G",
                significance="Pathogenic",
                review_stars=2,
            )
        )
    engine.dispose()


def _write_corrupt_standalone(path: Path) -> None:
    """Write a non-SQLite blob so the standalone DB probe fails."""
    path.write_bytes(b"this is definitely not a sqlite database image")


def _write_ready_gnomad(path: Path) -> None:
    """Create a valid, non-empty gnomad_af.db (state -> ready with a version)."""
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                CREATE TABLE gnomad_af (
                    rsid TEXT,
                    chrom TEXT,
                    pos INTEGER,
                    ref TEXT,
                    alt TEXT,
                    af_global REAL,
                    homozygous_count INTEGER
                )
                """
            )
        )
        conn.execute(sa.text("INSERT INTO gnomad_af VALUES ('rs1', '1', 100, 'A', 'G', 0.01, 0)"))
    engine.dispose()


def _seed_active_job(client_engine: sa.Engine, db_name: str, job_id: str = "active-job") -> None:
    """Insert a running download job + session linkage for ``db_name``.

    MUST be called *after* the TestClient lifespan has started, otherwise the
    startup ``recover_orphaned_jobs`` / ``cleanup_interrupted_sessions`` sweeps
    reset the row before the test sees it.
    """
    now = datetime.now(UTC)
    with client_engine.begin() as conn:
        conn.execute(
            jobs.insert().values(
                job_id=job_id,
                sample_id=None,
                job_type="database_download",
                status="running",
                progress_pct=10.0,
                message="downloading",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            download_sessions.insert().values(
                session_id="active-session", status="in_progress", created_at=now, updated_at=now
            )
        )
        conn.execute(
            download_session_jobs.insert().values(
                session_id="active-session", db_name=db_name, job_id=job_id
            )
        )


# ═══════════════════════════════════════════════════════════════════════
# GET /api/databases/health
# ═══════════════════════════════════════════════════════════════════════


class TestDatabaseHealthEndpoint:
    def test_health_returns_200_one_entry_per_db(self, db_client: TestClient):
        resp = db_client.get("/api/databases/health")
        assert resp.status_code == 200

        data = resp.json()
        assert "databases" in data
        # One record per registered database.
        assert len(data["databases"]) == len(get_all_databases())
        returned_names = {d["name"] for d in data["databases"]}
        assert returned_names == {db.name for db in get_all_databases()}

    def test_health_response_shape(self, db_client: TestClient):
        """Every record carries the full DatabaseHealthResponse field set."""
        data = db_client.get("/api/databases/health").json()
        for record in data["databases"]:
            assert set(record.keys()) == _HEALTH_FIELDS
            assert record["state"] in _VALID_STATES
            # expected_size_bytes is always populated from the registry.
            assert isinstance(record["expected_size_bytes"], int)
            assert record["expected_size_bytes"] > 0

    def test_health_fresh_install_mostly_not_installed(self, db_client: TestClient):
        """A freshly-created install (only an empty reference.db) reports every
        DB as not_installed — no artifacts, no version stamps, no jobs."""
        data = db_client.get("/api/databases/health").json()
        for record in data["databases"]:
            assert record["state"] == "not_installed"
            assert record["present"] is False
            assert record["version"] is None
            assert record["active_job_id"] is None
            assert record["can_resume"] is False
            assert record["can_verify"] is False

    def test_health_reflects_ready_standalone(self, db_client: TestClient, settings: Settings):
        """A valid standalone file + a version stamp surfaces as ready."""
        _write_ready_gnomad(settings.data_dir / "gnomad_af.db")
        engine = _ref_engine(settings)
        _record_db_version(engine, "gnomad", "v1", 100)
        engine.dispose()

        data = db_client.get("/api/databases/health").json()
        gnomad = next(d for d in data["databases"] if d["name"] == "gnomad")
        assert gnomad["state"] == "ready"
        assert gnomad["present"] is True
        assert gnomad["integrity_ok"] is True
        assert gnomad["can_verify"] is True
        assert gnomad["can_clean"] is False

    def test_health_reflects_corrupt_standalone(self, db_client: TestClient, settings: Settings):
        """A present-but-unreadable standalone file surfaces as corrupt."""
        _write_corrupt_standalone(settings.data_dir / "gnomad_af.db")
        engine = _ref_engine(settings)
        _record_db_version(engine, "gnomad", "v1", 7)
        engine.dispose()

        data = db_client.get("/api/databases/health").json()
        gnomad = next(d for d in data["databases"] if d["name"] == "gnomad")
        assert gnomad["state"] == "corrupt"
        assert gnomad["integrity_ok"] is False
        assert gnomad["can_clean"] is True


# ═══════════════════════════════════════════════════════════════════════
# POST /api/databases/resume
# ═══════════════════════════════════════════════════════════════════════


class TestResumeEndpoint:
    def test_resume_unknown_db_404(self, db_client: TestClient):
        resp = db_client.post("/api/databases/resume", json={"db_name": "nonexistent"})
        assert resp.status_code == 404

    def test_resume_no_partial_409(self, db_client: TestClient):
        """A download-mode DB with no .tmp partial cannot be resumed."""
        resp = db_client.post("/api/databases/resume", json={"db_name": "lai_bundle"})
        assert resp.status_code == 409

    def test_resume_in_flight_409(self, db_client: TestClient, settings: Settings):
        """A resumable partial that already has a running job is rejected (409)."""
        dl_dest = settings.downloads_dir / "encode_ccres.db"
        dl_dest.with_suffix(dl_dest.suffix + ".tmp").write_bytes(TEST_DATA[:512])

        engine = _ref_engine(settings)
        with engine.begin() as conn:
            conn.execute(
                downloads.insert().values(
                    url="http://example/encode_ccres.db",
                    dest_path=str(dl_dest),
                    total_bytes=len(TEST_DATA),
                    downloaded_bytes=512,
                    status="failed",
                )
            )
        # Mark a running job AFTER the lifespan recovery sweep (engine shared
        # with the app via the same on-disk reference.db file).
        _seed_active_job(engine, "encode_ccres")
        engine.dispose()

        resp = db_client.post("/api/databases/resume", json={"db_name": "encode_ccres"})
        assert resp.status_code == 409

    def test_resume_completes_from_partial(
        self,
        db_client: TestClient,
        settings: Settings,
        range_server: HTTPServer,
    ):
        """A resumable partial resumes via HTTP Range and the job reaches complete.

        Patch the lai_bundle registry entry to point at the local Range server,
        carry the matching SHA-256 (so the DownloadManager checksum passes), and
        drop the tarball post_download hook (we serve raw bytes, not a tarball).
        """
        url = server_url(range_server, "lai_bundle.tar.gz")
        lai = get_database("lai_bundle")
        assert lai is not None and lai.build_mode == "download"
        patched = replace(lai, url=url, sha256=TEST_DATA_SHA256, post_download=None)

        # Lay down a half-finished .tmp partial + a failed downloads row.
        dl_dest = settings.downloads_dir / patched.filename
        tmp_path = dl_dest.with_suffix(dl_dest.suffix + ".tmp")
        partial = 1024
        tmp_path.write_bytes(TEST_DATA[:partial])

        engine = _ref_engine(settings)
        with engine.begin() as conn:
            conn.execute(
                downloads.insert().values(
                    url=url,
                    dest_path=str(dl_dest),
                    total_bytes=len(TEST_DATA),
                    downloaded_bytes=partial,
                    status="failed",
                )
            )
        engine.dispose()

        with patch.dict(DATABASES, {"lai_bundle": patched}):
            resp = db_client.post("/api/databases/resume", json={"db_name": "lai_bundle"})
            assert resp.status_code == 202
            body = resp.json()
            assert body["session_id"].startswith("dbdl-")
            assert len(body["downloads"]) == 1
            assert body["downloads"][0]["db_name"] == "lai_bundle"
            job_id = body["downloads"][0]["job_id"]

            # Poll the background job to completion (download finishes fast).
            poll_engine = _ref_engine(settings)
            final_status = None
            for _ in range(80):  # up to ~8s
                with poll_engine.connect() as conn:
                    row = conn.execute(
                        sa.select(jobs.c.status).where(jobs.c.job_id == job_id)
                    ).fetchone()
                if row and row.status in ("complete", "failed"):
                    final_status = row.status
                    break
                time.sleep(0.1)
            poll_engine.dispose()

        assert final_status == "complete"
        final_dest = settings.data_dir / patched.filename
        assert final_dest.exists()
        assert final_dest.read_bytes() == TEST_DATA


# ═══════════════════════════════════════════════════════════════════════
# POST /api/databases/{db_name}/verify
# ═══════════════════════════════════════════════════════════════════════


class TestVerifyEndpoint:
    def test_verify_unknown_db_404(self, db_client: TestClient):
        resp = db_client.post("/api/databases/nonexistent/verify")
        assert resp.status_code == 404

    def test_verify_valid_reference_resident_ok(self, db_client: TestClient, settings: Settings):
        """A seeded reference-resident DB (clinvar) verifies ok with deep depth."""
        _seed_clinvar(settings)
        resp = db_client.post("/api/databases/clinvar/verify")
        assert resp.status_code == 200
        body = resp.json()
        assert body["db_name"] == "clinvar"
        assert body["ok"] is True
        assert body["depth"] == "deep"

    def test_verify_valid_standalone_ok(self, db_client: TestClient, settings: Settings):
        """A valid standalone gnomad_af.db verifies ok."""
        _write_ready_gnomad(settings.data_dir / "gnomad_af.db")
        resp = db_client.post("/api/databases/gnomad/verify")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["depth"] == "deep"

    def test_verify_corrupt_standalone_not_ok(self, db_client: TestClient, settings: Settings):
        """A corrupt standalone file fails the deep quick_check (ok=False)."""
        _write_corrupt_standalone(settings.data_dir / "gnomad_af.db")
        resp = db_client.post("/api/databases/gnomad/verify")
        assert resp.status_code == 200
        body = resp.json()
        assert body["db_name"] == "gnomad"
        assert body["ok"] is False
        assert body["detail"]  # carries a non-empty diagnostic


# ═══════════════════════════════════════════════════════════════════════
# POST /api/databases/{db_name}/clean
# ═══════════════════════════════════════════════════════════════════════


class TestCleanEndpoint:
    def test_clean_unknown_db_404(self, db_client: TestClient):
        resp = db_client.post("/api/databases/nonexistent/clean")
        assert resp.status_code == 404

    def test_clean_ready_db_409(self, db_client: TestClient, settings: Settings):
        """A healthy (ready) DB refuses cleaning — nothing to recover."""
        _write_ready_gnomad(settings.data_dir / "gnomad_af.db")
        engine = _ref_engine(settings)
        _record_db_version(engine, "gnomad", "v1", 100)
        engine.dispose()

        resp = db_client.post("/api/databases/gnomad/clean")
        assert resp.status_code == 409
        # The artifact is untouched.
        assert (settings.data_dir / "gnomad_af.db").exists()

    def test_clean_active_db_409(self, db_client: TestClient, settings: Settings):
        """A DB with an active download/build job refuses cleaning."""
        # .tmp partial + a failed downloads row, then a running job seeded post-lifespan.
        dl_dest = settings.downloads_dir / "encode_ccres.db"
        dl_dest.with_suffix(dl_dest.suffix + ".tmp").write_bytes(TEST_DATA[:256])

        engine = _ref_engine(settings)
        with engine.begin() as conn:
            conn.execute(
                downloads.insert().values(
                    url="http://example/encode_ccres.db",
                    dest_path=str(dl_dest),
                    total_bytes=len(TEST_DATA),
                    downloaded_bytes=256,
                    status="failed",
                )
            )
        _seed_active_job(engine, "encode_ccres")
        engine.dispose()

        resp = db_client.post("/api/databases/encode_ccres/clean")
        assert resp.status_code == 409
        # The partial is still on disk (not cleaned while active).
        assert dl_dest.with_suffix(dl_dest.suffix + ".tmp").exists()

    def test_clean_corrupt_removes_artifact_and_version(
        self, db_client: TestClient, settings: Settings
    ):
        """Cleaning a corrupt standalone DB removes the file and version row."""
        gnomad_path = settings.data_dir / "gnomad_af.db"
        _write_corrupt_standalone(gnomad_path)
        engine = _ref_engine(settings)
        _record_db_version(engine, "gnomad", "v1", 7)
        engine.dispose()

        resp = db_client.post("/api/databases/gnomad/clean")
        assert resp.status_code == 200
        body = resp.json()
        assert body["db_name"] == "gnomad"
        assert str(gnomad_path) in body["removed"]

        # File is gone.
        assert not gnomad_path.exists()

        # database_versions row is gone.
        engine = _ref_engine(settings)
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions.c.db_name).where(
                    database_versions.c.db_name == "gnomad"
                )
            ).fetchone()
        engine.dispose()
        assert row is None
