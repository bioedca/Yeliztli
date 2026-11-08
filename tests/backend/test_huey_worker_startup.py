"""Worker startup initializes its own reference schema before recovering jobs."""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock

import pytest
import sqlalchemy as sa

from backend.config import Settings
from backend.db import connection, reference_schema
from backend.db.tables import jobs, log_entries, reference_metadata
from backend.logging_config import configure_logging
from backend.tasks import huey_tasks


@pytest.fixture(params=[False, True], ids=["rollback", "wal"])
def worker_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request):
    """Use an empty reference and queue snapshot, with no API lifespan or log write."""
    settings = Settings(data_dir=tmp_path, wal_mode=request.param, _env_file=None)
    monkeypatch.setattr(connection, "get_settings", lambda: settings)
    connection.reset_registry()
    monkeypatch.setattr(huey_tasks.huey, "pending", lambda: [])
    monkeypatch.setattr(huey_tasks.huey, "scheduled", lambda: [])
    configure_logging()
    try:
        yield connection.get_registry().reference_engine
    finally:
        connection.reset_registry()


def test_startup_creates_reference_tables_without_api_or_logging(worker_reference) -> None:
    assert sa.inspect(worker_reference).get_table_names() == []
    assert (
        huey_tasks.huey._startup["recover_worker_jobs"]
        is huey_tasks._recover_jobs_on_worker_startup
    )

    huey_tasks._recover_jobs_on_worker_startup()
    huey_tasks._recover_jobs_on_worker_startup()

    assert set(reference_metadata.tables) <= set(sa.inspect(worker_reference).get_table_names())
    with worker_reference.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(jobs)) == 0
        assert conn.scalar(sa.select(sa.func.count()).select_from(log_entries)) == 0


def test_native_worker_startup_without_an_api_process(tmp_path: Path) -> None:
    """Exercise Huey's registered startup callback in a fresh interpreter."""
    script = """
import sys
from pathlib import Path
from threading import Event

import sqlalchemy as sa
from huey.consumer import Worker

from backend import config

config.DEFAULT_DATA_DIR = Path(sys.argv[1]) / 'config'
config.Settings.model_config['env_file'] = None
config.get_settings.cache_clear()

from backend.db.connection import get_registry, reset_registry
from backend.db.tables import jobs, log_entries, reference_metadata
from backend.tasks.huey_tasks import huey

assert 'backend.main' not in sys.modules
assert not config.get_settings().reference_db_path.exists()
try:
    Worker(huey, Event(), 0.1, 1.0, 1.15).initialize()
    engine = get_registry().reference_engine
    assert set(reference_metadata.tables) <= set(sa.inspect(engine).get_table_names())
    with engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(jobs)) == 0
        assert conn.scalar(sa.select(sa.func.count()).select_from(log_entries)) == 0
    assert 'backend.main' not in sys.modules
    print('worker startup ready')
finally:
    reset_registry()
"""
    env = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("YELIZTLI_")
    }
    env["YELIZTLI_DATA_DIR"] = str(tmp_path / "data")
    env["YELIZTLI_HUEY_IMMEDIATE"] = "0"
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "worker startup ready" in result.stdout
    assert 'startup hook "recover_worker_jobs" failed' not in result.stderr


def test_worker_and_api_bootstrap_can_race(worker_reference) -> None:
    """Independent API/worker connections can initialize the same empty file."""
    api_engine = connection.DBRegistry(connection.get_settings()).reference_engine
    start = Barrier(2, timeout=10)

    def start_api() -> None:
        start.wait()
        reference_schema.bootstrap_reference_schema_tables(api_engine)
        reference_schema.ensure_reference_schema_current(api_engine)

    def start_worker() -> None:
        start.wait()
        huey_tasks._recover_jobs_on_worker_startup()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            api = pool.submit(start_api)
            worker = pool.submit(start_worker)
            api.result(timeout=20)
            worker.result(timeout=20)
        # A later worker start and the API's additive migration remain idempotent.
        huey_tasks._recover_jobs_on_worker_startup()
        assert reference_schema.ensure_reference_schema_current(api_engine) is False
        assert set(reference_metadata.tables) <= set(sa.inspect(api_engine).get_table_names())
        with api_engine.connect() as conn:
            assert conn.scalar(sa.select(sa.func.count()).select_from(jobs)) == 0
    finally:
        api_engine.dispose()


def test_startup_preserves_protected_jobs_and_legacy_logs(
    worker_reference, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference_schema.bootstrap_reference_schema_tables(worker_reference)
    now = datetime.now(UTC)
    stale = now - huey_tasks.HUEY_PENDING_ENQUEUE_GRACE - timedelta(minutes=1)
    states = {
        "orphan-running": ("annotation", "running", stale),
        "orphan-pending": ("annotation", "pending", stale),
        "orphan-cancelling": ("annotation", "cancelling", stale),
        "queued-pending": ("update_check", "pending", stale),
        "scheduled-pending": ("update_check", "pending", stale),
        "fresh-pending": ("update_check", "pending", now),
        "api-running": ("database_download", "running", stale),
        "complete": ("annotation", "complete", stale),
    }
    with worker_reference.begin() as conn:
        conn.execute(
            jobs.insert(),
            [
                {
                    "job_id": job_id,
                    "job_type": job_type,
                    "status": status,
                    "created_at": created_at,
                    "updated_at": created_at,
                    "message": "synthetic job",
                    "progress_pct": 25.0,
                }
                for job_id, (job_type, status, created_at) in states.items()
            ],
        )
        before = {row["job_id"]: dict(row) for row in conn.execute(jobs.select()).mappings()}
        assert len(before) == len(states)
        # A pre-policy log table must retain its historic row when recovery logs.
        log_entries.drop(conn)
        conn.exec_driver_sql(
            "CREATE TABLE log_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "timestamp DATETIME, level TEXT NOT NULL, logger TEXT, "
            "message TEXT, event_data TEXT)"
        )
        conn.exec_driver_sql(
            "INSERT INTO log_entries (level, message, event_data) "
            "VALUES ('INFO', 'legacy synthetic event', '{}')"
        )

    monkeypatch.setattr(
        huey_tasks.huey,
        "pending",
        lambda: [huey_tasks.run_update_check_task.s("queued-pending")],
    )
    monkeypatch.setattr(
        huey_tasks.huey,
        "scheduled",
        lambda: [huey_tasks.run_update_check_task.s("scheduled-pending")],
    )
    full_migration = Mock(side_effect=AssertionError("worker must not run API content repairs"))
    monkeypatch.setattr(reference_schema, "ensure_reference_schema_current", full_migration)
    monkeypatch.setattr(huey_tasks, "_worker_logging_schema_engine", None)
    huey_tasks._configure_worker_logging()

    huey_tasks._recover_jobs_on_worker_startup()

    with worker_reference.connect() as conn:
        after = {row["job_id"]: dict(row) for row in conn.execute(jobs.select()).mappings()}
        assert set(after) == set(before)
        assert {key: row["status"] for key, row in after.items()} == {
            "orphan-running": "failed",
            "orphan-pending": "failed",
            "orphan-cancelling": "cancelled",
            "queued-pending": "pending",
            "scheduled-pending": "pending",
            "fresh-pending": "pending",
            "api-running": "running",
            "complete": "complete",
        }
        for key in (
            "queued-pending",
            "scheduled-pending",
            "fresh-pending",
            "api-running",
            "complete",
        ):
            assert after[key] == before[key]
        for key in ("orphan-running", "orphan-pending"):
            assert after[key]["error"] == "Huey worker stopped before the background job completed"
        assert after["orphan-cancelling"]["message"] == "Cancelled during worker restart"
        assert (
            conn.execute(
                sa.select(log_entries.c.presentation_policy_version).where(
                    log_entries.c.message == "legacy synthetic event"
                )
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(
                sa.select(log_entries.c.message).where(
                    log_entries.c.message == "worker_orphaned_jobs_recovered"
                )
            ).scalar_one()
            == "worker_orphaned_jobs_recovered"
        )
    full_migration.assert_not_called()

    huey_tasks._recover_jobs_on_worker_startup()
    with worker_reference.connect() as conn:
        assert {
            row["job_id"]: dict(row) for row in conn.execute(jobs.select()).mappings()
        } == after


def test_bootstrap_failure_propagates_before_recovery_and_can_retry(
    worker_reference, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as failure:
        bootstrap = Mock(side_effect=RuntimeError("synthetic bootstrap failure"))
        recover = Mock()
        failure.setattr(reference_schema, "bootstrap_reference_schema_tables", bootstrap)
        failure.setattr(huey_tasks, "recover_worker_orphaned_jobs", recover)
        with pytest.raises(RuntimeError, match="synthetic bootstrap failure"):
            huey_tasks._recover_jobs_on_worker_startup()
        bootstrap.assert_called_once_with(worker_reference)
        recover.assert_not_called()
        assert sa.inspect(worker_reference).get_table_names() == []

    huey_tasks._recover_jobs_on_worker_startup()
    with worker_reference.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(jobs)) == 0


def test_recovery_failure_is_not_hidden_by_bootstrap(
    worker_reference, monkeypatch: pytest.MonkeyPatch
) -> None:
    recover = Mock(side_effect=RuntimeError("synthetic recovery failure"))
    monkeypatch.setattr(huey_tasks, "recover_worker_orphaned_jobs", recover)
    with pytest.raises(RuntimeError, match="synthetic recovery failure"):
        huey_tasks._recover_jobs_on_worker_startup()
    assert "jobs" in sa.inspect(worker_reference).get_table_names()


def test_unavailable_queue_still_prevents_recovery(
    worker_reference, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(huey_tasks, "_queued_worker_job_ids", lambda: None)
    recover = Mock()
    monkeypatch.setattr(huey_tasks, "recover_worker_orphaned_jobs", recover)
    huey_tasks._recover_jobs_on_worker_startup()
    recover.assert_not_called()
    assert sa.inspect(worker_reference).get_table_names() == []
