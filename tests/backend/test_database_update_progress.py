"""A manual database update must report live build progress.

The setup wizard's builds feed ``download_progress``/``parse_progress``
callbacks into every registered build function (``_execute_build`` in
``backend.api.routes.databases``). The update-manager task calls the same
functions; without the callbacks its job row sits at 0% for a whole
multi-gigabyte build and the Update Manager cannot show progress.
"""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.tasks import huey_tasks as tasks


@pytest.fixture
def wired_update(tmp_path, monkeypatch):
    """Patch the task's collaborators; return (job-updates, captured, drive).

    ``drive`` lets each test choose what the fake build does with its
    callbacks, so they run from inside the build — where they run in
    production.
    """
    from backend.db import connection as db_connection
    from backend.db import database_registry

    updates = Mock()
    monkeypatch.setattr(tasks, "_update_job", updates)

    engine = Mock(name="reference_engine")
    settings = SimpleNamespace(downloads_dir=tmp_path / "downloads", data_dir=tmp_path)
    registry = SimpleNamespace(reference_engine=engine, settings=settings)
    # Never touch the real registry or claim files: the user may have a live
    # update running while this test executes.
    monkeypatch.setattr(db_connection, "get_registry", lambda: registry)
    monkeypatch.setattr(tasks, "build_lock", lambda _name: nullcontext())

    captured: dict[str, object] = {}
    drive: dict[str, object] = {}

    def fake_build_fn(target_engine, downloads_dir, download_progress=None, parse_progress=None):
        captured["engine"] = target_engine
        captured["downloads_dir"] = downloads_dir
        captured["download_progress"] = download_progress
        captured["parse_progress"] = parse_progress
        runner = drive.get("run")
        if runner is not None:
            runner(download_progress, parse_progress)

    monkeypatch.setattr(database_registry, "get_build_fn", lambda _name: fake_build_fn)
    monkeypatch.setattr(
        database_registry,
        "DATABASES",
        {"clinvar": SimpleNamespace(target_db="reference", display_name="ClinVar")},
    )
    return updates, captured, drive


def test_reference_build_receives_and_reports_progress_callbacks(wired_update):
    updates, captured, drive = wired_update

    def run(download_progress, parse_progress):
        download_progress(50, 100)
        parse_progress(50_000)

    drive["run"] = run
    tasks._execute_database_update("job-1", "clinvar")

    assert callable(captured["download_progress"])
    assert callable(captured["parse_progress"])

    running = [c.kwargs for c in updates.call_args_list if c.kwargs.get("status") == "running"]
    # Download bytes map to 0-90% of the job; parse to 90-99% — the wizard's
    # mapping, mirrored here.
    assert any(
        c.get("progress_pct") == 45.0 and "Downloading ClinVar... 45%" in (c.get("message") or "")
        for c in running
    )
    assert any(
        c.get("progress_pct") == 90.5
        and "Importing ClinVar... 50,000 records" in (c.get("message") or "")
        for c in running
    )
    assert updates.call_args_list[-1].kwargs["status"] == "complete"
    assert updates.call_args_list[-1].kwargs["progress_pct"] == 100.0


def test_progress_writes_are_throttled(wired_update):
    updates, _captured, drive = wired_update

    def run(download_progress, _parse_progress):
        download_progress(10, 100)
        download_progress(80, 100)  # within the 2 s throttle window: dropped

    drive["run"] = run
    tasks._execute_database_update("job-2", "clinvar")

    pcts = [
        c.kwargs.get("progress_pct")
        for c in updates.call_args_list
        if c.kwargs.get("status") == "running"
    ]
    assert 9.0 in pcts
    assert 72.0 not in pcts
