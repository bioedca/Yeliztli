"""Tests for download executor singleton and session lifecycle (Finding 3).

Covers:
- Module-level ThreadPoolExecutor singleton
- Session persistence in download_sessions / download_session_jobs tables
- Startup cleanup of interrupted/stale sessions
- DELETE /api/databases/sessions/{session_id} endpoint
- GET /api/databases/sessions endpoint
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa
from _downloads import wait_for_downloads
from fastapi.testclient import TestClient

from backend.api.routes.databases import (
    _active_sessions,
    cleanup_interrupted_sessions,
    get_download_executor,
    shutdown_executor,
)
from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.tables import (
    download_session_jobs,
    download_sessions,
    reference_metadata,
)

# ── Fixtures ─────────────────────────────────────────────────────────


def _make_engine(tmp_data_dir: Path) -> sa.Engine:
    """Create a reference.db engine with all tables."""
    ref_path = tmp_data_dir / "reference.db"
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    return engine


# ── Executor singleton tests ─────────────────────────────────────────


class TestExecutorSingleton:
    """The download executor should be a module-level singleton."""

    def test_get_executor_returns_same_instance(self):
        shutdown_executor()  # Reset state
        ex1 = get_download_executor()
        ex2 = get_download_executor()
        assert ex1 is ex2
        shutdown_executor()

    def test_shutdown_clears_executor(self):
        shutdown_executor()
        ex1 = get_download_executor()
        shutdown_executor()
        ex2 = get_download_executor()
        assert ex1 is not ex2
        shutdown_executor()


# ── Session persistence tests ────────────────────────────────────────


class TestSessionPersistence:
    """Download sessions should be persisted in the database."""

    def test_session_persisted_after_download_trigger(self, tmp_data_dir: Path):
        _active_sessions.clear()
        engine = _make_engine(tmp_data_dir)
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

        # The trigger persists the session before the download runs, so this
        # test needs no download at all. It holds the submitted download in
        # flight until the assertions are done, so the session is still
        # in_progress, then waits for it. The real download, against a refused
        # port, retried for ~10 s into later tests (#2543).
        release = threading.Event()

        def _held_download(**_kwargs: object) -> None:
            release.wait(timeout=30)

        with (
            patch("backend.main.get_settings", return_value=settings),
            patch("backend.db.connection.get_settings", return_value=settings),
            patch("backend.api.routes.databases.get_settings", return_value=settings),
            patch("backend.api.routes.databases._run_download", new=_held_download),
        ):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as client:
                try:
                    # Create a fake DB to download
                    from backend.db.database_registry import DATABASES, DatabaseInfo

                    test_db = DatabaseInfo(
                        name="testdb",
                        display_name="Test DB",
                        description="Test",
                        url="http://127.0.0.1:1/nonexistent",
                        filename="testdb.db",
                        expected_size_bytes=100,
                        required=False,
                        build_mode="download",
                        target_db="standalone",
                    )

                    with patch.dict(DATABASES, {"testdb": test_db}):
                        resp = client.post(
                            "/api/databases/download",
                            json={"databases": ["testdb"]},
                        )

                    assert resp.status_code == 202
                    session_id = resp.json()["session_id"]

                    # Verify session is in the database
                    ref_engine = sa.create_engine(f"sqlite:///{tmp_data_dir / 'reference.db'}")
                    with ref_engine.connect() as conn:
                        row = conn.execute(
                            sa.select(download_sessions).where(
                                download_sessions.c.session_id == session_id
                            )
                        ).fetchone()
                        assert row is not None
                        assert row.status == "in_progress"

                        # Check session jobs
                        job_rows = conn.execute(
                            sa.select(download_session_jobs).where(
                                download_session_jobs.c.session_id == session_id
                            )
                        ).fetchall()
                        assert len(job_rows) == 1
                        assert job_rows[0].db_name == "testdb"
                    ref_engine.dispose()
                finally:
                    release.set()
                    wait_for_downloads()
            reset_registry()
        _active_sessions.clear()
        engine.dispose()


# ── Startup cleanup tests ────────────────────────────────────────────


class TestStartupCleanup:
    """On startup, in-progress sessions should be marked interrupted/stale."""

    def test_recent_sessions_marked_interrupted(self, tmp_data_dir: Path):
        engine = _make_engine(tmp_data_dir)
        now = datetime.now(UTC)

        with engine.begin() as conn:
            conn.execute(
                download_sessions.insert().values(
                    session_id="recent-session",
                    status="in_progress",
                    created_at=now - timedelta(minutes=5),
                    updated_at=now - timedelta(minutes=5),
                )
            )

        count = cleanup_interrupted_sessions(engine)
        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(
                sa.select(download_sessions.c.status).where(
                    download_sessions.c.session_id == "recent-session"
                )
            ).fetchone()
            assert row.status == "interrupted"
        engine.dispose()

    def test_old_sessions_marked_stale(self, tmp_data_dir: Path):
        engine = _make_engine(tmp_data_dir)
        now = datetime.now(UTC)

        with engine.begin() as conn:
            conn.execute(
                download_sessions.insert().values(
                    session_id="old-session",
                    status="in_progress",
                    created_at=now - timedelta(hours=2),
                    updated_at=now - timedelta(hours=2),
                )
            )

        count = cleanup_interrupted_sessions(engine)
        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(
                sa.select(download_sessions.c.status).where(
                    download_sessions.c.session_id == "old-session"
                )
            ).fetchone()
            assert row.status == "stale"
        engine.dispose()

    def test_completed_sessions_not_affected(self, tmp_data_dir: Path):
        engine = _make_engine(tmp_data_dir)
        now = datetime.now(UTC)

        with engine.begin() as conn:
            conn.execute(
                download_sessions.insert().values(
                    session_id="done-session",
                    status="complete",
                    created_at=now - timedelta(hours=3),
                    updated_at=now - timedelta(hours=3),
                )
            )

        count = cleanup_interrupted_sessions(engine)
        assert count == 0

        with engine.connect() as conn:
            row = conn.execute(
                sa.select(download_sessions.c.status).where(
                    download_sessions.c.session_id == "done-session"
                )
            ).fetchone()
            assert row.status == "complete"
        engine.dispose()

    def test_no_sessions_returns_zero(self, tmp_data_dir: Path):
        engine = _make_engine(tmp_data_dir)
        count = cleanup_interrupted_sessions(engine)
        assert count == 0
        engine.dispose()


# ── DELETE session endpoint tests ────────────────────────────────────


class TestDeleteSession:
    """DELETE /api/databases/sessions/{session_id} endpoint."""

    def test_delete_completed_session(self, tmp_data_dir: Path):
        _active_sessions.clear()
        engine = _make_engine(tmp_data_dir)
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

        # Insert a completed session
        now = datetime.now(UTC)
        with engine.begin() as conn:
            conn.execute(
                download_sessions.insert().values(
                    session_id="del-session",
                    status="complete",
                    created_at=now,
                    updated_at=now,
                )
            )
            conn.execute(
                download_session_jobs.insert().values(
                    session_id="del-session",
                    db_name="testdb",
                    job_id="del-job-1",
                )
            )
        engine.dispose()

        with (
            patch("backend.main.get_settings", return_value=settings),
            patch("backend.db.connection.get_settings", return_value=settings),
            patch("backend.api.routes.databases.get_settings", return_value=settings),
        ):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as client:
                resp = client.delete("/api/databases/sessions/del-session")
                assert resp.status_code == 204

                # Session should be gone
                ref_engine = sa.create_engine(f"sqlite:///{tmp_data_dir / 'reference.db'}")
                with ref_engine.connect() as conn:
                    row = conn.execute(
                        sa.select(download_sessions).where(
                            download_sessions.c.session_id == "del-session"
                        )
                    ).fetchone()
                    assert row is None
                ref_engine.dispose()
            reset_registry()
        _active_sessions.clear()

    def test_delete_in_progress_returns_409(self, tmp_data_dir: Path):
        _active_sessions.clear()
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        _make_engine(tmp_data_dir).dispose()

        with (
            patch("backend.main.get_settings", return_value=settings),
            patch("backend.db.connection.get_settings", return_value=settings),
            patch("backend.api.routes.databases.get_settings", return_value=settings),
        ):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as client:
                # Insert in-progress session AFTER app startup (so cleanup doesn't touch it)
                ref_engine = sa.create_engine(f"sqlite:///{tmp_data_dir / 'reference.db'}")
                now = datetime.now(UTC)
                with ref_engine.begin() as conn:
                    conn.execute(
                        download_sessions.insert().values(
                            session_id="active-session",
                            status="in_progress",
                            created_at=now,
                            updated_at=now,
                        )
                    )
                ref_engine.dispose()

                resp = client.delete("/api/databases/sessions/active-session")
                assert resp.status_code == 409
            reset_registry()
        _active_sessions.clear()

    def test_delete_nonexistent_returns_404(self, tmp_data_dir: Path):
        _active_sessions.clear()
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        _make_engine(tmp_data_dir).dispose()

        with (
            patch("backend.main.get_settings", return_value=settings),
            patch("backend.db.connection.get_settings", return_value=settings),
            patch("backend.api.routes.databases.get_settings", return_value=settings),
        ):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as client:
                resp = client.delete("/api/databases/sessions/nope")
                assert resp.status_code == 404
            reset_registry()
        _active_sessions.clear()


# ── GET sessions endpoint tests ──────────────────────────────────────


class TestListSessions:
    """GET /api/databases/sessions endpoint."""

    def test_list_sessions_empty(self, tmp_data_dir: Path):
        _active_sessions.clear()
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        _make_engine(tmp_data_dir).dispose()

        with (
            patch("backend.main.get_settings", return_value=settings),
            patch("backend.db.connection.get_settings", return_value=settings),
            patch("backend.api.routes.databases.get_settings", return_value=settings),
        ):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as client:
                resp = client.get("/api/databases/sessions")
                assert resp.status_code == 200
                assert resp.json() == []
            reset_registry()
        _active_sessions.clear()

    def test_list_sessions_returns_data(self, tmp_data_dir: Path):
        _active_sessions.clear()
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        engine = _make_engine(tmp_data_dir)

        now = datetime.now(UTC)
        with engine.begin() as conn:
            conn.execute(
                download_sessions.insert().values(
                    session_id="list-session",
                    status="complete",
                    created_at=now,
                    updated_at=now,
                )
            )
            conn.execute(
                download_session_jobs.insert().values(
                    session_id="list-session",
                    db_name="clinvar",
                    job_id="list-job-1",
                )
            )
        engine.dispose()

        with (
            patch("backend.main.get_settings", return_value=settings),
            patch("backend.db.connection.get_settings", return_value=settings),
            patch("backend.api.routes.databases.get_settings", return_value=settings),
        ):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as client:
                resp = client.get("/api/databases/sessions")
                assert resp.status_code == 200
                data = resp.json()
                assert len(data) == 1
                assert data[0]["session_id"] == "list-session"
                assert data[0]["status"] == "complete"
                assert len(data[0]["databases"]) == 1
                assert data[0]["databases"][0]["db_name"] == "clinvar"
            reset_registry()
        _active_sessions.clear()
