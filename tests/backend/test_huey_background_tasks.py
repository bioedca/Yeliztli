"""Coverage for the three previously-untested Huey background tasks (#539).

``run_lai_task``, ``run_update_check_task`` and ``periodic_update_check`` had
**zero** test references while their structurally-identical sibling
``run_annotation_task`` is thoroughly covered by ``test_huey_annotation.py``.
This module locks the async-job *wrapper* logic those tasks own:

* job-status transitions (``running`` → ``complete`` / ``failed``), which the
  SSE progress endpoint streams to the frontend;
* the completion/failure messaging (LAI top-ancestry summary, update-check
  counts, error recording);
* the periodic scheduler's interval branching (startup no-op / weekly
  skip-if-recent / weekly run-if-stale / default run) and its naive→UTC
  datetime coercion.

Tasks are invoked via ``.call_local()`` (huey 2.x: runs the decorated function
directly, bypassing the queue — the idiom ``test_huey_annotation.py`` uses), and
the heavy dependencies (``run_lai_analysis``, ``run_scheduled_update_check``)
are mocked so the tests exercise only the wrapper, not the analysis.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from backend.config import Settings
from backend.db.connection import get_registry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    jobs,
    raw_variants,
    reference_metadata,
    samples,
    update_history,
)
from backend.tasks.huey_tasks import (
    create_update_check_job,
    periodic_update_check,
    run_lai_task,
    run_update_check_task,
)

SEED_RAW_VARIANTS = [
    {"rsid": "rs1", "chrom": "1", "pos": 100, "genotype": "AG"},
]


# ── Fixtures / helpers ─────────────────────────────────────────────────


@pytest.fixture
def huey_env(tmp_data_dir: Path):
    """reference.db (samples + jobs + update_history) + a sample DB, patched registry."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="abc123",
            )
        )
    ref_engine.dispose()

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_db_path.parent.mkdir(parents=True, exist_ok=True)
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)
    with sample_engine.begin() as conn:
        conn.execute(raw_variants.insert(), SEED_RAW_VARIANTS)
    sample_engine.dispose()

    with (
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.tasks.huey_tasks.get_settings", return_value=settings),
    ):
        reset_registry()
        yield {"settings": settings, "sample_id": 1}
        reset_registry()


def _job_row(job_id: str):
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        return conn.execute(sa.select(jobs).where(jobs.c.job_id == job_id)).fetchone()


def _make_job(
    job_id: str, job_type: str, *, sample_id: int | None = None, status: str = "pending"
) -> None:
    registry = get_registry()
    now = datetime.now(UTC)
    with registry.reference_engine.begin() as conn:
        conn.execute(
            jobs.insert().values(
                job_id=job_id,
                sample_id=sample_id,
                job_type=job_type,
                status=status,
                progress_pct=0.0,
                message="",
                created_at=now,
                updated_at=now,
            )
        )


def _seed_update_history(updated_at: datetime, db_name: str = "clinvar") -> None:
    registry = get_registry()
    with registry.reference_engine.begin() as conn:
        conn.execute(
            update_history.insert().values(
                db_name=db_name,
                previous_version="v1",
                new_version="v2",
                updated_at=updated_at,
            )
        )


def _lai_result(global_ancestry: dict, *, chroms: int = 22) -> SimpleNamespace:
    """A stand-in for the LAIResult the real run_lai_analysis returns.

    run_lai_task only reads ``.global_ancestry`` (dict of pop → {"fraction": …})
    and ``.metadata.get("chromosomes_analyzed", 0)``.
    """
    return SimpleNamespace(
        global_ancestry=global_ancestry,
        metadata={"chromosomes_analyzed": chroms},
    )


# ═══════════════════════════════════════════════════════════════════════
# run_lai_task() — the user LAI (chromosome-painting) job
# ═══════════════════════════════════════════════════════════════════════


class TestRunLaiTask:
    def test_completes_and_summarizes_top_ancestry(self, huey_env: dict) -> None:
        """Success path: job → complete @ 100% with the top-ancestry summary."""
        _make_job("lai-ok", "lai", sample_id=1)
        result = _lai_result({"EUR": {"fraction": 0.7}, "AMR": {"fraction": 0.3}}, chroms=22)

        with patch("backend.analysis.lai.run_lai_analysis", return_value=result):
            run_lai_task.call_local(1, "lai-ok")

        row = _job_row("lai-ok")
        assert row.status == "complete"
        assert row.progress_pct == 100.0
        assert "22 chromosomes analyzed" in row.message
        assert "top ancestry: EUR" in row.message

    def test_empty_global_ancestry_guard(self, huey_env: dict) -> None:
        """Empty global_ancestry → ``top_pop`` stays "" (no ``max()`` over empty)."""
        _make_job("lai-empty", "lai", sample_id=1)
        result = _lai_result({}, chroms=0)

        with patch("backend.analysis.lai.run_lai_analysis", return_value=result):
            run_lai_task.call_local(1, "lai-empty")

        row = _job_row("lai-empty")
        assert row.status == "complete"
        assert row.message.endswith("top ancestry: ")

    def test_failure_marks_job_failed(self, huey_env: dict) -> None:
        """A raise from run_lai_analysis → status failed with the error recorded."""
        _make_job("lai-fail", "lai", sample_id=1)

        with patch(
            "backend.analysis.lai.run_lai_analysis",
            side_effect=RuntimeError("lai boom"),
        ):
            run_lai_task.call_local(1, "lai-fail")

        row = _job_row("lai-fail")
        assert row.status == "failed"
        assert "lai boom" in row.error

    def test_reports_per_chromosome_progress(self, huey_env: dict) -> None:
        """The per-chromosome progress callback updates progress_pct (SSE depends on it)."""
        _make_job("lai-prog", "lai", sample_id=1)

        def fake_lai(*, sample_id, sample_engine, progress_callback):
            progress_callback("Painting chr1", 0.25)
            progress_callback("Painting chr2", 0.5)
            return _lai_result({"EUR": {"fraction": 1.0}}, chroms=2)

        captured: list[float] = []
        from backend.tasks.huey_tasks import _update_job as real_update

        def capture(jid, *, status, progress_pct=0.0, **kwargs):
            captured.append(progress_pct)
            real_update(jid, status=status, progress_pct=progress_pct, **kwargs)

        with (
            patch("backend.analysis.lai.run_lai_analysis", side_effect=fake_lai),
            patch("backend.tasks.huey_tasks._update_job", side_effect=capture),
        ):
            run_lai_task.call_local(1, "lai-prog")

        assert 25.0 in captured
        assert 50.0 in captured
        assert captured[-1] == 100.0
        assert _job_row("lai-prog").status == "complete"

    def test_cancellation_short_circuits(self, huey_env: dict) -> None:
        """A cancelled job stops mid-run and is NOT overwritten to complete/failed."""
        _make_job("lai-cancel", "lai", sample_id=1, status="cancelled")

        def fake_lai(*, sample_id, sample_engine, progress_callback):
            # Raises AnnotationCancelledError because _is_job_cancelled is patched True.
            progress_callback("Painting chr1", 0.5)
            return _lai_result({"EUR": {"fraction": 1.0}})

        with (
            patch("backend.analysis.lai.run_lai_analysis", side_effect=fake_lai),
            patch("backend.tasks.huey_tasks._is_job_cancelled", return_value=True),
        ):
            run_lai_task.call_local(1, "lai-cancel")

        row = _job_row("lai-cancel")
        assert row.status not in ("complete", "failed")


# ═══════════════════════════════════════════════════════════════════════
# run_update_check_task() — on-demand / startup update check
# ═══════════════════════════════════════════════════════════════════════


class TestRunUpdateCheckTask:
    def test_completes_with_summary(self, huey_env: dict) -> None:
        """Counts of available + up-to-date land in the completion message."""
        job_id = create_update_check_job()
        result = SimpleNamespace(available=["clinvar"], up_to_date=["vep", "gnomad"], errors=[])

        with patch("backend.db.update_manager.run_scheduled_update_check", return_value=result):
            run_update_check_task.call_local(job_id)

        row = _job_row(job_id)
        assert row.status == "complete"
        assert row.progress_pct == 100.0
        assert "1 update(s) available" in row.message
        assert "2 up to date" in row.message
        assert row.error is None

    def test_errors_recorded_in_error_field(self, huey_env: dict) -> None:
        """Per-DB errors are summarized in the message and joined into ``error``."""
        job_id = create_update_check_job()
        result = SimpleNamespace(
            available=[], up_to_date=[], errors=["clinvar: timeout", "vep: 404"]
        )

        with patch("backend.db.update_manager.run_scheduled_update_check", return_value=result):
            run_update_check_task.call_local(job_id)

        row = _job_row(job_id)
        assert row.status == "complete"
        assert "2 error(s)" in row.message
        assert "clinvar: timeout" in row.error

    def test_no_changes_uses_default_message(self, huey_env: dict) -> None:
        """Nothing available/up-to-date/errored → the fallback message."""
        job_id = create_update_check_job()
        result = SimpleNamespace(available=[], up_to_date=[], errors=[])

        with patch("backend.db.update_manager.run_scheduled_update_check", return_value=result):
            run_update_check_task.call_local(job_id)

        row = _job_row(job_id)
        assert row.status == "complete"
        assert row.message == "Update check complete"

    def test_failure_marks_job_failed(self, huey_env: dict) -> None:
        """A raise from run_scheduled_update_check → status failed with the error."""
        job_id = create_update_check_job()

        with patch(
            "backend.db.update_manager.run_scheduled_update_check",
            side_effect=RuntimeError("registry boom"),
        ):
            run_update_check_task.call_local(job_id)

        row = _job_row(job_id)
        assert row.status == "failed"
        assert "registry boom" in row.error


# ═══════════════════════════════════════════════════════════════════════
# periodic_update_check() — daily scheduler, gated by update_check_interval
# ═══════════════════════════════════════════════════════════════════════


class TestPeriodicUpdateCheck:
    """Locks the four interval outcomes + the naive→UTC datetime coercion.

    ``create_update_check_job`` / ``run_update_check_task`` are patched as spies
    so we assert *whether* a check is dispatched without running one.
    ``periodic_update_check`` re-imports ``get_settings`` from ``backend.config``
    at call time, so that is the patch target.
    """

    def test_startup_is_noop(self, huey_env: dict) -> None:
        settings = SimpleNamespace(update_check_interval="startup")
        with (
            patch("backend.config.get_settings", return_value=settings),
            patch("backend.tasks.huey_tasks.create_update_check_job") as mk_job,
            patch("backend.tasks.huey_tasks.run_update_check_task") as run_task,
        ):
            periodic_update_check.call_local()

        mk_job.assert_not_called()
        run_task.assert_not_called()

    def test_weekly_skips_when_recent(self, huey_env: dict) -> None:
        _seed_update_history(datetime.now(UTC) - timedelta(days=2))
        settings = SimpleNamespace(update_check_interval="weekly")
        with (
            patch("backend.config.get_settings", return_value=settings),
            patch("backend.tasks.huey_tasks.create_update_check_job") as mk_job,
            patch("backend.tasks.huey_tasks.run_update_check_task") as run_task,
        ):
            periodic_update_check.call_local()

        mk_job.assert_not_called()
        run_task.assert_not_called()

    def test_weekly_runs_when_stale(self, huey_env: dict) -> None:
        _seed_update_history(datetime.now(UTC) - timedelta(days=10))
        settings = SimpleNamespace(update_check_interval="weekly")
        with (
            patch("backend.config.get_settings", return_value=settings),
            patch(
                "backend.tasks.huey_tasks.create_update_check_job", return_value="job-stale"
            ) as mk_job,
            patch("backend.tasks.huey_tasks.run_update_check_task") as run_task,
        ):
            periodic_update_check.call_local()

        mk_job.assert_called_once()
        run_task.assert_called_once_with("job-stale")

    def test_weekly_runs_when_no_history(self, huey_env: dict) -> None:
        """No update_history rows → last_check is None → falls through to run."""
        settings = SimpleNamespace(update_check_interval="weekly")
        with (
            patch("backend.config.get_settings", return_value=settings),
            patch(
                "backend.tasks.huey_tasks.create_update_check_job", return_value="job-none"
            ) as mk_job,
            patch("backend.tasks.huey_tasks.run_update_check_task") as run_task,
        ):
            periodic_update_check.call_local()

        mk_job.assert_called_once()
        run_task.assert_called_once_with("job-none")

    def test_weekly_naive_timestamp_coerced_to_utc(self, huey_env: dict) -> None:
        """A tz-naive recent ``updated_at`` is coerced to UTC and skips (no crash)."""
        _seed_update_history(datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1))
        settings = SimpleNamespace(update_check_interval="weekly")
        with (
            patch("backend.config.get_settings", return_value=settings),
            patch("backend.tasks.huey_tasks.create_update_check_job") as mk_job,
            patch("backend.tasks.huey_tasks.run_update_check_task") as run_task,
        ):
            periodic_update_check.call_local()

        mk_job.assert_not_called()
        run_task.assert_not_called()

    def test_default_interval_runs(self, huey_env: dict) -> None:
        """Any non-startup/non-weekly interval (e.g. "daily") runs the check."""
        settings = SimpleNamespace(update_check_interval="daily")
        with (
            patch("backend.config.get_settings", return_value=settings),
            patch(
                "backend.tasks.huey_tasks.create_update_check_job", return_value="job-daily"
            ) as mk_job,
            patch("backend.tasks.huey_tasks.run_update_check_task") as run_task,
        ):
            periodic_update_check.call_local()

        mk_job.assert_called_once()
        run_task.assert_called_once_with("job-daily")


# ═══════════════════════════════════════════════════════════════════════
# run_database_update_task() — cross-process claim guard (PR-13b)
# ═══════════════════════════════════════════════════════════════════════


class TestRunDatabaseUpdateTaskClaim:
    """The Huey update task must skip (not race) when another process already
    holds the cross-process build claim for the same DB."""

    def test_skips_and_fails_job_when_claimed(self, huey_env: dict) -> None:
        from backend.db.build_guard import cross_process_build_claim
        from backend.tasks.huey_tasks import run_database_update_task

        settings = huey_env["settings"]
        job_id = "dbup-clinvar-claimed"
        _make_job(job_id, "database_update")

        # Hold the claim (stands in for the wizard building clinvar in the API
        # process). The task must not reach the build path.
        with cross_process_build_claim("clinvar", settings.data_dir):
            with patch("backend.tasks.huey_tasks._execute_database_update") as exec_mock:
                run_database_update_task.call_local(job_id, "clinvar")

        exec_mock.assert_not_called()
        row = _job_row(job_id)
        assert row.status == "failed"
        assert "another process" in (row.error or "").lower()

    def test_runs_build_when_not_claimed(self, huey_env: dict) -> None:
        from backend.tasks.huey_tasks import run_database_update_task

        job_id = "dbup-clinvar-free"
        _make_job(job_id, "database_update")

        # Claim free → the wrapper delegates to the (stubbed) build path.
        with patch("backend.tasks.huey_tasks._execute_database_update") as exec_mock:
            run_database_update_task.call_local(job_id, "clinvar")

        exec_mock.assert_called_once_with(job_id, "clinvar")
