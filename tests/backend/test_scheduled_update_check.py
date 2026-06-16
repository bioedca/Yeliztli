"""Tests for ``run_scheduled_update_check`` dispatch behavior (Step 27).

Covers the orchestrator's behavior across every dispatch branch:

* per-DB auto-update toggle (``get_auto_update``) skips the update
* bandwidth window (``should_download_now``) defers the update
* bundle dispatch routes through the manifest-driven ``run_<bundle>_update``
  functions for ``vep_bundle``, ``lai_bundle``, and ``ancestry_pca``
* ClinVar dispatch routes through ``run_clinvar_update(registry)``
* pipeline DBs dispatch through ``huey_tasks.run_database_update_task``
* a failure in one dispatch is recorded in ``errors`` and does not abort
  the sweep
* a candidate whose name has no dispatch path is logged and ignored
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from backend.db.tables import auto_update_settings
from backend.db.update_manager import (
    UpdateCheckResult,
    UpdateResult,
    VersionInfo,
    run_scheduled_update_check,
    set_auto_update,
)


@pytest.fixture(autouse=True)
def _setup_complete():
    """Treat first-run setup as finished for every dispatch test in this module.

    ``run_scheduled_update_check`` now no-ops while first-run setup is active
    (the suppress-during-setup guard, ``_first_run_setup_active``). That guard
    reads global ``~/.yeliztli`` state via the setup-status predicates, so
    without this these dispatch tests would pass only on a box with a
    provisioned install and redden on a clean CI runner. Forcing the guard to
    ``False`` keeps them exercising the dispatch path. The guard itself is
    covered hermetically in
    ``test_update_manager.py::TestSuppressAutoUpdateDuringSetup``.
    """
    with patch("backend.db.update_manager._first_run_setup_active", return_value=False):
        yield


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _make_registry(
    reference_engine: sa.Engine,
    tmp_path: Path,
    *,
    window: str | None = None,
) -> MagicMock:
    """Build a registry mock whose ``settings`` has real Path-backed dirs.

    Guards against stringified-mock paths leaking into CWD if a dispatch code
    path touches ``settings.data_dir`` or ``settings.reference_db_path`` before
    the runner's patch fires.
    """
    settings = MagicMock()
    settings.data_dir = tmp_path
    settings.reference_db_path = tmp_path / "reference.db"
    settings.downloads_dir = tmp_path / "downloads"
    settings.samples_dir = tmp_path / "samples"
    settings.update_download_window = window
    registry = MagicMock()
    registry.reference_engine = reference_engine
    registry.settings = settings
    return registry


def _info(db_name: str, *, size: int = 1_000_000) -> VersionInfo:
    return VersionInfo(
        db_name=db_name,
        latest_version="v-new",
        download_url=f"https://example.com/{db_name}",
        download_size_bytes=size,
    )


# ──────────────────────────────────────────────────────────────────────
# Auto-update toggle gating
# ──────────────────────────────────────────────────────────────────────


class TestToggleGating:
    def test_pipeline_db_skipped_when_toggle_off(self, reference_engine, tmp_path: Path):
        """Toggle off → ``huey_tasks`` plumbing is never invoked."""
        registry = _make_registry(reference_engine, tmp_path)
        set_auto_update(reference_engine, "gnomad", False)

        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(available=[_info("gnomad")]),
            ),
            patch("backend.tasks.huey_tasks.run_database_update_task") as mock_run_task,
            patch("backend.tasks.huey_tasks.create_database_update_job") as mock_create_job,
        ):
            result = run_scheduled_update_check(registry)

        mock_run_task.assert_not_called()
        mock_create_job.assert_not_called()
        assert result.available[0].db_name == "gnomad"
        assert result.errors == []

    def test_lai_bundle_skipped_when_toggle_off(self, reference_engine, tmp_path: Path):
        """Bundle toggle off → manifest-driven runner is not called."""
        registry = _make_registry(reference_engine, tmp_path)
        set_auto_update(reference_engine, "lai_bundle", False)

        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(available=[_info("lai_bundle")]),
            ),
            patch("backend.db.update_manager.run_lai_bundle_update") as mock_lai,
        ):
            run_scheduled_update_check(registry)

        mock_lai.assert_not_called()


# ──────────────────────────────────────────────────────────────────────
# Bandwidth-window gating
# ──────────────────────────────────────────────────────────────────────


class TestBandwidthWindow:
    def test_pipeline_db_deferred_outside_window(self, reference_engine, tmp_path: Path):
        """Outside-window dispatch is short-circuited for pipeline DBs."""
        registry = _make_registry(reference_engine, tmp_path, window="02:00-06:00")
        set_auto_update(reference_engine, "gnomad", True)

        update_info = _info("gnomad", size=200 * 1024 * 1024)

        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(available=[update_info]),
            ),
            patch(
                "backend.db.update_manager.should_download_now",
                return_value=False,
            ) as mock_window,
            patch("backend.tasks.huey_tasks.run_database_update_task") as mock_run_task,
            patch("backend.tasks.huey_tasks.create_database_update_job") as mock_create_job,
        ):
            run_scheduled_update_check(registry)

        mock_window.assert_called_once_with(200 * 1024 * 1024, "02:00-06:00")
        mock_run_task.assert_not_called()
        mock_create_job.assert_not_called()

    def test_bundle_deferred_outside_window(self, reference_engine, tmp_path: Path):
        """LAI bundle outside the window does not call the manifest runner."""
        registry = _make_registry(reference_engine, tmp_path, window="02:00-06:00")
        set_auto_update(reference_engine, "lai_bundle", True)

        update_info = _info("lai_bundle", size=500 * 1024 * 1024)

        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(available=[update_info]),
            ),
            patch(
                "backend.db.update_manager.should_download_now",
                return_value=False,
            ),
            patch("backend.db.update_manager.run_lai_bundle_update") as mock_lai,
        ):
            run_scheduled_update_check(registry)

        mock_lai.assert_not_called()

    def test_dispatch_proceeds_inside_window(self, reference_engine, tmp_path: Path):
        """Inside-window candidates dispatch as normal."""
        registry = _make_registry(reference_engine, tmp_path, window="02:00-06:00")
        set_auto_update(reference_engine, "cpic", True)

        update_info = _info("cpic", size=5_000_000)

        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(available=[update_info]),
            ),
            patch(
                "backend.db.update_manager.should_download_now",
                return_value=True,
            ),
            patch("backend.tasks.huey_tasks.run_database_update_task") as mock_run_task,
            patch(
                "backend.tasks.huey_tasks.create_database_update_job",
                return_value="job-cpic",
            ) as mock_create_job,
        ):
            run_scheduled_update_check(registry)

        mock_create_job.assert_called_once_with("cpic")
        mock_run_task.assert_called_once_with("job-cpic", "cpic")


# ──────────────────────────────────────────────────────────────────────
# Bundle dispatch — manifest path
# ──────────────────────────────────────────────────────────────────────


class TestBundleDispatch:
    def test_vep_bundle_dispatch(self, reference_engine, tmp_path: Path):
        registry = _make_registry(reference_engine, tmp_path)
        set_auto_update(reference_engine, "vep_bundle", True)

        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(available=[_info("vep_bundle")]),
            ),
            patch(
                "backend.db.update_manager.run_vep_bundle_update",
                return_value=UpdateResult(
                    db_name="vep_bundle",
                    previous_version=None,
                    new_version="2026-05-01",
                ),
            ) as mock_vep,
        ):
            result = run_scheduled_update_check(registry)

        mock_vep.assert_called_once_with(registry.settings)
        assert result.errors == []

    def test_lai_bundle_dispatch(self, reference_engine, tmp_path: Path):
        registry = _make_registry(reference_engine, tmp_path)
        # lai_bundle is not in AUTO_UPDATE_DEFAULTS — enable it explicitly.
        set_auto_update(reference_engine, "lai_bundle", True)

        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(available=[_info("lai_bundle")]),
            ),
            patch(
                "backend.db.update_manager.run_lai_bundle_update",
                return_value=UpdateResult(
                    db_name="lai_bundle",
                    previous_version=None,
                    new_version="v1.1",
                ),
            ) as mock_lai,
        ):
            result = run_scheduled_update_check(registry)

        mock_lai.assert_called_once_with(registry.settings)
        assert result.errors == []

    def test_ancestry_pca_dispatch(self, reference_engine, tmp_path: Path):
        registry = _make_registry(reference_engine, tmp_path)
        set_auto_update(reference_engine, "ancestry_pca", True)

        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(available=[_info("ancestry_pca")]),
            ),
            patch(
                "backend.db.update_manager.run_ancestry_pca_bundle_update",
                return_value=UpdateResult(
                    db_name="ancestry_pca",
                    previous_version=None,
                    new_version="v1.0",
                ),
            ) as mock_pca,
        ):
            result = run_scheduled_update_check(registry)

        mock_pca.assert_called_once_with(registry.settings)
        assert result.errors == []

    def test_gnomad_bundle_dispatch(self, reference_engine, tmp_path: Path):
        """gnomad now routes through the bundle runner (not the huey build path)."""
        registry = _make_registry(reference_engine, tmp_path)
        # gnomad's default toggle is True; a small _info() size stays under the
        # bandwidth window so the dispatch proceeds synchronously.
        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(available=[_info("gnomad")]),
            ),
            patch(
                "backend.db.update_manager.run_gnomad_bundle_update",
                return_value=UpdateResult(
                    db_name="gnomad",
                    previous_version=None,
                    new_version="v1.0.0",
                ),
            ) as mock_gnomad,
            # Guard against gnomad accidentally falling through to the huey path.
            patch("backend.tasks.huey_tasks.create_database_update_job") as mock_create_job,
            patch("backend.tasks.huey_tasks.run_database_update_task") as mock_run_task,
        ):
            result = run_scheduled_update_check(registry)

        mock_gnomad.assert_called_once_with(registry.settings)
        mock_create_job.assert_not_called()
        mock_run_task.assert_not_called()
        assert result.errors == []


# ──────────────────────────────────────────────────────────────────────
# Pipeline DB dispatch — huey_tasks plumbing
# ──────────────────────────────────────────────────────────────────────


class TestPipelineDispatch:
    def test_pipeline_dispatch_queues_huey_task(self, reference_engine, tmp_path: Path):
        """Pipeline DBs reuse ``run_database_update_task`` via Huey."""
        registry = _make_registry(reference_engine, tmp_path)
        # dbnsfp's default toggle is True and it is still a pipeline build
        # (gnomad now ships as a bundle) — exercise the default huey path.

        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(available=[_info("dbnsfp")]),
            ),
            patch(
                "backend.tasks.huey_tasks.create_database_update_job",
                return_value="job-dbnsfp",
            ) as mock_create_job,
            patch("backend.tasks.huey_tasks.run_database_update_task") as mock_run_task,
        ):
            result = run_scheduled_update_check(registry)

        mock_create_job.assert_called_once_with("dbnsfp")
        mock_run_task.assert_called_once_with("job-dbnsfp", "dbnsfp")
        assert result.errors == []

    def test_pipeline_dispatch_uses_table_toggle(self, reference_engine, tmp_path: Path):
        """Pipeline-DB dispatch reads ``auto_update_settings``, not the dict."""
        registry = _make_registry(reference_engine, tmp_path)
        # cpic default is True. Flip it off explicitly via the table.
        set_auto_update(reference_engine, "cpic", False)
        # Confirm the row landed.
        with reference_engine.connect() as conn:
            stored = conn.execute(
                sa.select(auto_update_settings.c.enabled).where(
                    auto_update_settings.c.db_name == "cpic"
                )
            ).scalar_one()
        assert stored is False

        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(available=[_info("cpic")]),
            ),
            patch("backend.tasks.huey_tasks.create_database_update_job") as mock_create_job,
            patch("backend.tasks.huey_tasks.run_database_update_task") as mock_run_task,
        ):
            run_scheduled_update_check(registry)

        mock_create_job.assert_not_called()
        mock_run_task.assert_not_called()


# ──────────────────────────────────────────────────────────────────────
# ClinVar dispatch
# ──────────────────────────────────────────────────────────────────────


class TestClinvarDispatch:
    def test_clinvar_dispatch_passes_registry(self, reference_engine, tmp_path: Path):
        registry = _make_registry(reference_engine, tmp_path)

        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(available=[_info("clinvar")]),
            ),
            patch(
                "backend.db.update_manager.run_clinvar_update",
                return_value=UpdateResult(
                    db_name="clinvar",
                    previous_version=None,
                    new_version="20260520",
                ),
            ) as mock_clinvar,
        ):
            result = run_scheduled_update_check(registry)

        mock_clinvar.assert_called_once_with(registry)
        assert result.errors == []


# ──────────────────────────────────────────────────────────────────────
# Error handling
# ──────────────────────────────────────────────────────────────────────


class TestDispatchErrors:
    def test_dispatch_failure_recorded_in_errors_and_sweep_continues(
        self, reference_engine, tmp_path: Path
    ):
        """An exception in one update is captured; the next update still runs."""
        registry = _make_registry(reference_engine, tmp_path)
        # ``lai_bundle`` is not in AUTO_UPDATE_DEFAULTS — explicitly enable it
        # so the orchestrator reaches the dispatch path.
        set_auto_update(reference_engine, "lai_bundle", True)

        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(
                    available=[_info("lai_bundle"), _info("ancestry_pca")]
                ),
            ),
            patch(
                "backend.db.update_manager.run_lai_bundle_update",
                side_effect=RuntimeError("boom"),
            ) as mock_lai,
            patch(
                "backend.db.update_manager.run_ancestry_pca_bundle_update",
                return_value=UpdateResult(
                    db_name="ancestry_pca",
                    previous_version=None,
                    new_version="v1.0",
                ),
            ) as mock_pca,
        ):
            result = run_scheduled_update_check(registry)

        mock_lai.assert_called_once_with(registry.settings)
        mock_pca.assert_called_once_with(registry.settings)
        assert any("lai_bundle update failed" in err for err in result.errors)
        # The second dispatch ran despite the first one raising.
        assert "boom" in result.errors[0]

    def test_unknown_db_logged_and_ignored(self, reference_engine, tmp_path: Path):
        """A check_all_updates result for a name with no dispatch path is skipped.

        This shouldn't normally happen — CHECK_FNS is the source of truth — but
        the orchestrator must not crash if it does, and the sweep must continue.
        """
        registry = _make_registry(reference_engine, tmp_path)
        set_auto_update(reference_engine, "not_a_real_db", True)

        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(
                    available=[_info("not_a_real_db"), _info("clinvar")]
                ),
            ),
            patch(
                "backend.db.update_manager.run_clinvar_update",
                return_value=UpdateResult(
                    db_name="clinvar",
                    previous_version=None,
                    new_version="20260520",
                ),
            ) as mock_clinvar,
        ):
            result = run_scheduled_update_check(registry)

        # ClinVar still ran even though the unknown DB came first.
        mock_clinvar.assert_called_once_with(registry)
        assert result.errors == []
