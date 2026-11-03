"""The real update worker independently rejects unsupported queued identities."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from huey.storage import SqliteStorage

from backend.db import build_guard, connection
from backend.db.database_registry import DATABASES, get_build_fn
from backend.db.update_manager import _BUNDLE_DBS, _DOWNLOAD_DBS
from backend.tasks import huey_tasks as tasks

SUPPORTED = sorted(
    set(_BUNDLE_DBS)
    | set(_DOWNLOAD_DBS)
    | {key for key in DATABASES if get_build_fn(key) is not None}
)


@pytest.mark.parametrize(
    "key", ["../outside", "/absolute", "unknown_safe_name", "", None, True, [], {}]
)
def test_worker_rejects_before_registry_claim_or_dispatch(key, tmp_path, monkeypatch):
    updates = Mock()
    monkeypatch.setattr(tasks, "_update_job", updates)
    monkeypatch.setattr(build_guard, "_locks", {})

    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported worker identity reached registry or dispatch")

    monkeypatch.setattr(connection, "get_registry", forbidden)
    monkeypatch.setattr(tasks, "_execute_database_update", forbidden)
    tasks.run_database_update_task.call_local("synthetic-job", key)
    updates.assert_called_once()
    args, kwargs = updates.call_args
    assert args == ("synthetic-job",)
    assert kwargs["status"] == "failed"
    assert "unsupported" in kwargs["error"].lower()
    assert build_guard._locks == {}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("key", SUPPORTED)
def test_every_supported_worker_key_enters_real_claim(key, tmp_path, monkeypatch):
    assert SUPPORTED
    monkeypatch.setattr(
        connection,
        "get_registry",
        lambda: SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path)),
    )
    calls = []

    def execute(job_id, name, source_binding=None):
        assert build_guard.is_cross_process_build_claimed(name, tmp_path)
        calls.append((job_id, name, source_binding))

    monkeypatch.setattr(tasks, "_execute_database_update", execute)
    tasks.run_database_update_task.call_local("synthetic-job", key, "synthetic-binding")
    assert calls == [("synthetic-job", key, "synthetic-binding")]
    assert (tmp_path / ".claims" / f"{key}.claim").is_file()
    assert not build_guard.is_cross_process_build_claimed(key, tmp_path)


@pytest.mark.parametrize("key", ["clinvar", "unknown_safe_name", "../outside"])
def test_sqlite_queue_preserves_identity_and_worker_rechecks_it(key, tmp_path, monkeypatch):
    queue = tasks.huey
    old_storage, old_immediate = queue.storage, queue.immediate
    queue.immediate = False
    isolated = SqliteStorage("synthetic-worker-test", filename=str(tmp_path / "queue.db"))
    queue.storage = isolated
    calls = []
    updates = Mock()
    monkeypatch.setattr(tasks, "_update_job", updates)
    monkeypatch.setattr(
        connection,
        "get_registry",
        lambda: SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path / "data")),
    )

    def execute(job_id, name, source_binding=None):
        assert build_guard.is_cross_process_build_claimed(name, tmp_path / "data")
        calls.append((job_id, name, source_binding))

    monkeypatch.setattr(tasks, "_execute_database_update", execute)
    try:
        tasks.run_database_update_task("synthetic-job", key, "synthetic-binding")
        assert calls == [] and not (tmp_path / "data").exists()
        queued = queue.dequeue()
        assert queued is not None
        assert queued.args == ("synthetic-job", key, "synthetic-binding")
        queue.execute(queued)
        assert queue.dequeue() is None
        if key == "clinvar":
            assert calls == [("synthetic-job", key, "synthetic-binding")]
            updates.assert_not_called()
            assert (tmp_path / "data" / ".claims" / "clinvar.claim").is_file()
        else:
            assert calls == []
            updates.assert_called_once()
            assert updates.call_args.kwargs["status"] == "failed"
            assert not (tmp_path / "data").exists()
    finally:
        isolated.close()
        queue.immediate = old_immediate
        queue.storage = old_storage


def test_unregistered_worker_name_is_rejected_even_with_a_builder(tmp_path, monkeypatch):
    from backend.db import database_registry

    updates = Mock()
    monkeypatch.setattr(tasks, "_update_job", updates)
    monkeypatch.setattr(database_registry, "get_build_fn", lambda name: object())

    def forbidden():
        pytest.fail("Unregistered worker identity reached registry access")

    monkeypatch.setattr(connection, "get_registry", forbidden)
    tasks.run_database_update_task.call_local("synthetic-job", "unknown_safe_name")
    updates.assert_called_once()
    assert updates.call_args.kwargs["status"] == "failed"
    assert list(tmp_path.iterdir()) == []
