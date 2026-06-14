"""Tests for the database update manager (P4-16).

Covers:
- Bandwidth window parsing and enforcement
- Version checking (get_current_version)
- ClinVar update check (HTTP HEAD mock)
- Differential ClinVar update (reclassification detection)
- Re-annotation pre-check (single sample, all samples)
- Re-annotation prompt CRUD (create, get, dismiss)
- Update history recording and retrieval
- Scheduled update orchestrator
- Huey task wrappers
- API endpoints
"""

from __future__ import annotations

from datetime import time as dt_time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    auto_update_settings,
    clinvar_variants,
    database_versions,
    reference_metadata,
    watched_variants,
)
from backend.db.update_manager import (
    AUTO_UPDATE_DEFAULTS,
    BANDWIDTH_WINDOW_THRESHOLD,
    UpdateCheckResult,
    UpdateResult,
    VersionInfo,
    _create_reannotation_prompt,
    _precheck_clinvar,
    _record_update_history,
    _record_version,
    check_all_updates,
    check_clinvar_update,
    dismiss_prompt,
    format_version_display,
    get_active_prompts,
    get_all_version_stamps,
    get_auto_update,
    get_current_version,
    get_update_history,
    parse_time_window,
    run_scheduled_update_check,
    set_auto_update,
    should_download_now,
)


def _settings_for_test(tmp_path: Path) -> Settings:
    """Real Settings instance anchored at a tmp_path-backed data_dir.

    Using real Settings instead of MagicMock guarantees that any code path
    touching ``settings.data_dir`` / ``settings.reference_db_path`` resolves
    to a concrete Path and cannot stringify a child mock into a sqlite URL
    that drops a zero-byte file at the repo root.
    """
    return Settings(data_dir=tmp_path, wal_mode=False)


# ═══════════════════════════════════════════════════════════════════════
# Bandwidth window tests
# ═══════════════════════════════════════════════════════════════════════


class TestParseTimeWindow:
    def test_normal_window(self):
        start, end = parse_time_window("02:00-06:00")
        assert start == dt_time(2, 0)
        assert end == dt_time(6, 0)

    def test_midnight_span(self):
        start, end = parse_time_window("22:00-06:00")
        assert start == dt_time(22, 0)
        assert end == dt_time(6, 0)

    def test_with_spaces(self):
        start, end = parse_time_window(" 03:30 - 05:45 ")
        assert start == dt_time(3, 30)
        assert end == dt_time(5, 45)

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid time window"):
            parse_time_window("not-a-window")

    def test_single_time(self):
        with pytest.raises(ValueError, match="Invalid time window"):
            parse_time_window("02:00")


class TestShouldDownloadNow:
    def test_small_download_always_proceeds(self):
        # Under 100 MB threshold
        assert should_download_now(50 * 1024 * 1024, "02:00-06:00") is True

    def test_no_window_always_proceeds(self):
        assert should_download_now(200 * 1024 * 1024, None) is True

    def test_large_download_within_window(self):
        with patch("backend.db.update_manager.datetime") as mock_dt:
            mock_dt.now.return_value.time.return_value = dt_time(3, 0)
            assert should_download_now(200 * 1024 * 1024, "02:00-06:00") is True

    def test_large_download_outside_window(self):
        with patch("backend.db.update_manager.datetime") as mock_dt:
            mock_dt.now.return_value.time.return_value = dt_time(12, 0)
            assert should_download_now(200 * 1024 * 1024, "02:00-06:00") is False

    def test_exactly_at_threshold_is_gated(self):
        # Exactly 100 MB is NOT under threshold (< not <=), so bandwidth window applies
        with patch("backend.db.update_manager.datetime") as mock_dt:
            mock_dt.now.return_value.time.return_value = dt_time(12, 0)
            assert should_download_now(BANDWIDTH_WINDOW_THRESHOLD, "02:00-06:00") is False


# ═══════════════════════════════════════════════════════════════════════
# Version management tests
# ═══════════════════════════════════════════════════════════════════════


class TestGetCurrentVersion:
    def test_no_version(self, reference_engine):
        assert get_current_version(reference_engine, "clinvar") is None

    def test_with_version(self, reference_engine):
        with reference_engine.begin() as conn:
            conn.execute(database_versions.insert().values(db_name="clinvar", version="20260301"))
        assert get_current_version(reference_engine, "clinvar") == "20260301"


class TestRecordVersion:
    def test_insert_new(self, reference_engine):
        _record_version(reference_engine, "clinvar", "20260301", 1000)
        v = get_current_version(reference_engine, "clinvar")
        assert v == "20260301"

    def test_update_existing(self, reference_engine):
        _record_version(reference_engine, "clinvar", "20260301", 1000)
        _record_version(reference_engine, "clinvar", "20260315", 1100)
        v = get_current_version(reference_engine, "clinvar")
        assert v == "20260315"


# ═══════════════════════════════════════════════════════════════════════
# ClinVar update check tests
# ═══════════════════════════════════════════════════════════════════════


class TestCheckClinvarUpdate:
    def test_update_available(self, reference_engine):
        # Set old version
        _record_version(reference_engine, "clinvar", "20250101")

        mock_resp = MagicMock()
        mock_resp.headers = {
            "Last-Modified": "Thu, 20 Mar 2026 00:00:00 GMT",
            "Content-Length": "30000000",
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("backend.db.update_manager.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.head.return_value = mock_resp

            result = check_clinvar_update(reference_engine)

        assert result is not None
        assert result.db_name == "clinvar"
        assert result.latest_version == "20260320"
        assert result.download_size_bytes == 30000000

    def test_already_up_to_date(self, reference_engine):
        _record_version(reference_engine, "clinvar", "20260401")

        mock_resp = MagicMock()
        mock_resp.headers = {
            "Last-Modified": "Thu, 20 Mar 2026 00:00:00 GMT",
            "Content-Length": "30000000",
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("backend.db.update_manager.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.head.return_value = mock_resp

            result = check_clinvar_update(reference_engine)

        assert result is None

    def test_network_error_returns_none(self, reference_engine):
        with patch("backend.db.update_manager.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.head.side_effect = Exception("Network error")

            result = check_clinvar_update(reference_engine)
        assert result is None


class TestCheckAllUpdates:
    """``check_all_updates`` dispatches via ``CHECK_FNS`` (Step 25).

    Each registered callable is invoked uniformly as
    ``fn(reference_engine, settings, timeout=timeout)``. The aggregated
    :class:`UpdateCheckResult` must contain every db_name across exactly one
    of ``available`` / ``up_to_date`` / ``errors``.
    """

    def _stub_check_fns(self, **overrides):
        """Build a stub dispatch dict covering every real CHECK_FNS key.

        Each override key replaces the default ``None``-returning stub. Returns
        a dict suitable for ``patch.dict(CHECK_FNS, …, clear=True)``.
        """
        from backend.db.update_manager import CHECK_FNS

        stubs = {name: MagicMock(return_value=None) for name in CHECK_FNS}
        stubs.update(overrides)
        return stubs

    def test_returns_update_check_result(self, reference_engine):
        stubs = self._stub_check_fns()
        with patch.dict(
            "backend.db.update_manager.CHECK_FNS",
            stubs,
            clear=True,
        ):
            result = check_all_updates(reference_engine)

        assert isinstance(result, UpdateCheckResult)

    def test_visits_every_db_in_check_fns(self, reference_engine):
        """Every registered DB must end up in exactly one bucket (no gaps)."""
        from backend.db.update_manager import CHECK_FNS

        expected = set(CHECK_FNS)
        stubs = self._stub_check_fns()

        with patch.dict(
            "backend.db.update_manager.CHECK_FNS",
            stubs,
            clear=True,
        ):
            result = check_all_updates(reference_engine)

        # All-None stubs → every key in up_to_date, none in available/errors.
        assert set(result.up_to_date) == expected
        assert result.available == []
        assert result.errors == []

        # And each stub was actually called (no key silently skipped).
        for fn in stubs.values():
            fn.assert_called_once()

    def test_version_info_lands_in_available(self, reference_engine):
        info = VersionInfo(
            db_name="clinvar",
            latest_version="20260320",
            download_url="https://example.com/clinvar.vcf.gz",
            download_size_bytes=30_000_000,
        )
        stubs = self._stub_check_fns(clinvar=MagicMock(return_value=info))

        with patch.dict(
            "backend.db.update_manager.CHECK_FNS",
            stubs,
            clear=True,
        ):
            result = check_all_updates(reference_engine)

        assert info in result.available
        assert "clinvar" not in result.up_to_date

    def test_none_lands_in_up_to_date(self, reference_engine):
        stubs = self._stub_check_fns(gnomad=MagicMock(return_value=None))
        with patch.dict(
            "backend.db.update_manager.CHECK_FNS",
            stubs,
            clear=True,
        ):
            result = check_all_updates(reference_engine)

        assert "gnomad" in result.up_to_date

    def test_exception_lands_in_errors(self, reference_engine):
        stubs = self._stub_check_fns(dbnsfp=MagicMock(side_effect=RuntimeError("boom")))

        with patch.dict(
            "backend.db.update_manager.CHECK_FNS",
            stubs,
            clear=True,
        ):
            result = check_all_updates(reference_engine)

        assert any("dbnsfp: boom" == err for err in result.errors)
        assert "dbnsfp" not in result.up_to_date
        # A failing check must not abort the sweep: every other DB still visited.
        from backend.db.update_manager import CHECK_FNS

        assert set(result.up_to_date) == set(CHECK_FNS) - {"dbnsfp"}

    def test_dispatch_passes_settings_and_timeout(self, reference_engine, tmp_path: Path):
        settings = _settings_for_test(tmp_path)
        stub = MagicMock(return_value=None)
        stubs = self._stub_check_fns(clinvar=stub)

        with patch.dict(
            "backend.db.update_manager.CHECK_FNS",
            stubs,
            clear=True,
        ):
            check_all_updates(reference_engine, timeout=12.5, settings=settings)

        stub.assert_called_once_with(reference_engine, settings, timeout=12.5)

    def test_aggregates_mixed_results(self, reference_engine):
        available_info = VersionInfo(
            db_name="vep_bundle",
            latest_version="2026-04-07",
            download_url="https://example.com/vep_bundle.db",
            download_size_bytes=400_000_000,
        )
        stubs = self._stub_check_fns(
            vep_bundle=MagicMock(return_value=available_info),
            gwas_catalog=MagicMock(side_effect=ValueError("api down")),
        )

        with patch.dict(
            "backend.db.update_manager.CHECK_FNS",
            stubs,
            clear=True,
        ):
            result = check_all_updates(reference_engine)

        assert available_info in result.available
        assert any("gwas_catalog: api down" == err for err in result.errors)
        # Bucket disjointness: no db_name appears in more than one bucket.
        seen = set(result.up_to_date) | {v.db_name for v in result.available}
        for err in result.errors:
            seen.add(err.split(":", 1)[0])
        from backend.db.update_manager import CHECK_FNS

        assert seen == set(CHECK_FNS)


# ═══════════════════════════════════════════════════════════════════════
# Update history tests
# ═══════════════════════════════════════════════════════════════════════


class TestUpdateHistory:
    def test_record_and_retrieve(self, reference_engine):
        _record_update_history(
            reference_engine,
            db_name="clinvar",
            previous_version="20260301",
            new_version="20260315",
            variants_added=100,
            variants_reclassified=5,
            download_size_bytes=30000000,
            duration_seconds=45,
        )

        history = get_update_history(reference_engine)
        assert len(history) == 1
        assert history[0]["db_name"] == "clinvar"
        assert history[0]["previous_version"] == "20260301"
        assert history[0]["new_version"] == "20260315"
        assert history[0]["variants_added"] == 100
        assert history[0]["variants_reclassified"] == 5

    def test_filter_by_db_name(self, reference_engine):
        _record_update_history(
            reference_engine,
            db_name="clinvar",
            previous_version=None,
            new_version="20260301",
        )
        _record_update_history(
            reference_engine,
            db_name="gwas",
            previous_version=None,
            new_version="2026-03",
        )

        clinvar_history = get_update_history(reference_engine, db_name="clinvar")
        assert len(clinvar_history) == 1
        assert clinvar_history[0]["db_name"] == "clinvar"

    def test_limit(self, reference_engine):
        for i in range(10):
            _record_update_history(
                reference_engine,
                db_name="clinvar",
                previous_version=None,
                new_version=f"2026030{i}",
            )

        history = get_update_history(reference_engine, limit=3)
        assert len(history) == 3

    def test_ordered_most_recent_first(self, reference_engine):
        _record_update_history(
            reference_engine,
            db_name="clinvar",
            previous_version=None,
            new_version="20260301",
        )
        _record_update_history(
            reference_engine,
            db_name="clinvar",
            previous_version="20260301",
            new_version="20260315",
        )

        history = get_update_history(reference_engine)
        assert history[0]["new_version"] == "20260315"
        assert history[1]["new_version"] == "20260301"


# ═══════════════════════════════════════════════════════════════════════
# Re-annotation prompt tests
# ═══════════════════════════════════════════════════════════════════════


class TestReannotationPrompts:
    def test_create_prompt(self, reference_engine):
        _create_reannotation_prompt(
            reference_engine,
            sample_id=1,
            db_name="clinvar",
            db_version="20260315",
            candidate_count=3,
        )

        prompts = get_active_prompts(reference_engine)
        assert len(prompts) == 1
        assert prompts[0]["sample_id"] == 1
        assert prompts[0]["db_name"] == "clinvar"
        assert prompts[0]["candidate_count"] == 3

    def test_update_existing_prompt(self, reference_engine):
        _create_reannotation_prompt(
            reference_engine,
            sample_id=1,
            db_name="clinvar",
            db_version="20260301",
            candidate_count=2,
        )
        _create_reannotation_prompt(
            reference_engine,
            sample_id=1,
            db_name="clinvar",
            db_version="20260315",
            candidate_count=5,
        )

        prompts = get_active_prompts(reference_engine)
        assert len(prompts) == 1
        assert prompts[0]["candidate_count"] == 5
        assert prompts[0]["db_version"] == "20260315"

    def test_dismiss_prompt(self, reference_engine):
        _create_reannotation_prompt(
            reference_engine,
            sample_id=1,
            db_name="clinvar",
            db_version="20260315",
            candidate_count=3,
        )

        prompts = get_active_prompts(reference_engine)
        assert len(prompts) == 1

        ok = dismiss_prompt(reference_engine, prompts[0]["id"])
        assert ok is True

        prompts = get_active_prompts(reference_engine)
        assert len(prompts) == 0

    def test_dismiss_nonexistent(self, reference_engine):
        ok = dismiss_prompt(reference_engine, 999)
        assert ok is False

    def test_create_prompt_with_watched_data(self, reference_engine):
        """T4-22m: Prompt stores watched variant reclassification details."""
        watched = [
            {
                "rsid": "rs80357906",
                "gene_symbol": "BRCA2",
                "old_significance": "Uncertain_significance",
                "new_significance": "Likely_pathogenic",
            }
        ]
        _create_reannotation_prompt(
            reference_engine,
            sample_id=1,
            db_name="clinvar",
            db_version="20260315",
            candidate_count=3,
            watched_count=1,
            watched_details=watched,
        )

        prompts = get_active_prompts(reference_engine)
        assert len(prompts) == 1
        assert prompts[0]["watched_count"] == 1
        assert len(prompts[0]["watched_details"]) == 1
        assert prompts[0]["watched_details"][0]["rsid"] == "rs80357906"
        assert prompts[0]["watched_details"][0]["new_significance"] == "Likely_pathogenic"

    def test_prompt_no_watched_data_defaults(self, reference_engine):
        """T4-22n: Prompt without watched data has zero count and empty list."""
        _create_reannotation_prompt(
            reference_engine,
            sample_id=1,
            db_name="clinvar",
            db_version="20260315",
            candidate_count=3,
        )

        prompts = get_active_prompts(reference_engine)
        assert len(prompts) == 1
        assert prompts[0]["watched_count"] == 0
        assert prompts[0]["watched_details"] == []

    def test_update_prompt_with_watched_data(self, reference_engine):
        """Updating a prompt replaces watched data."""
        _create_reannotation_prompt(
            reference_engine,
            sample_id=1,
            db_name="clinvar",
            db_version="20260301",
            candidate_count=2,
        )
        watched = [
            {
                "rsid": "rs80357906",
                "gene_symbol": "BRCA2",
                "old_significance": "Uncertain_significance",
                "new_significance": "Likely_pathogenic",
            }
        ]
        _create_reannotation_prompt(
            reference_engine,
            sample_id=1,
            db_name="clinvar",
            db_version="20260315",
            candidate_count=5,
            watched_count=1,
            watched_details=watched,
        )

        prompts = get_active_prompts(reference_engine)
        assert len(prompts) == 1
        assert prompts[0]["candidate_count"] == 5
        assert prompts[0]["watched_count"] == 1
        assert prompts[0]["watched_details"][0]["rsid"] == "rs80357906"

    def test_filter_by_sample_id(self, reference_engine):
        _create_reannotation_prompt(
            reference_engine,
            sample_id=1,
            db_name="clinvar",
            db_version="20260315",
            candidate_count=3,
        )
        _create_reannotation_prompt(
            reference_engine,
            sample_id=2,
            db_name="clinvar",
            db_version="20260315",
            candidate_count=1,
        )

        prompts = get_active_prompts(reference_engine, sample_id=1)
        assert len(prompts) == 1
        assert prompts[0]["sample_id"] == 1


# ═══════════════════════════════════════════════════════════════════════
# Pre-check tests
# ═══════════════════════════════════════════════════════════════════════


class TestPrecheck:
    def test_clinvar_precheck_with_reclassification(self, reference_engine, sample_engine):
        # Seed sample with annotated variants
        create_sample_tables(sample_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert(),
                [
                    {
                        "rsid": "rs429358",
                        "chrom": "19",
                        "pos": 44908684,
                        "clinvar_significance": "risk_factor",
                        "annotation_coverage": 2,
                    },
                    {
                        "rsid": "rs7412",
                        "chrom": "19",
                        "pos": 44908822,
                        "clinvar_significance": "benign",
                        "annotation_coverage": 2,
                    },
                ],
            )

        old_sigs = {"rs429358": "risk_factor", "rs7412": "benign"}
        new_sigs = {"rs429358": "risk_factor", "rs7412": "Pathogenic"}

        result = _precheck_clinvar(
            sample_engine,
            reference_engine,
            sample_id=1,
            old_significances=old_sigs,
            new_significances=new_sigs,
        )

        assert result.candidate_count == 1
        assert result.reclassified_variants[0]["rsid"] == "rs7412"
        assert result.reclassified_variants[0]["old_significance"] == "benign"
        assert result.reclassified_variants[0]["new_significance"] == "Pathogenic"

    def test_precheck_no_changes(self, reference_engine, sample_engine):
        create_sample_tables(sample_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert(),
                [
                    {
                        "rsid": "rs429358",
                        "chrom": "19",
                        "pos": 44908684,
                        "clinvar_significance": "risk_factor",
                        "annotation_coverage": 2,
                    },
                ],
            )

        old_sigs = {"rs429358": "risk_factor"}
        new_sigs = {"rs429358": "risk_factor"}

        result = _precheck_clinvar(
            sample_engine,
            reference_engine,
            sample_id=1,
            old_significances=old_sigs,
            new_significances=new_sigs,
        )

        assert result.candidate_count == 0

    def test_precheck_empty_sample(self, reference_engine, sample_engine):
        create_sample_tables(sample_engine)

        result = _precheck_clinvar(
            sample_engine,
            reference_engine,
            sample_id=1,
            old_significances={},
            new_significances={},
        )
        assert result.candidate_count == 0

    def test_precheck_watched_variant_reclassification(self, reference_engine, sample_engine):
        create_sample_tables(sample_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert(),
                [
                    {
                        "rsid": "rs80357906",
                        "chrom": "17",
                        "pos": 43091983,
                        "clinvar_significance": "Uncertain_significance",
                        "gene_symbol": "BRCA2",
                        "annotation_coverage": 2,
                    },
                ],
            )
            conn.execute(
                watched_variants.insert(),
                [
                    {
                        "rsid": "rs80357906",
                        "clinvar_significance_at_watch": "Uncertain_significance",
                    },
                ],
            )

        old_sigs = {"rs80357906": "Uncertain_significance"}
        new_sigs = {"rs80357906": "Likely_pathogenic"}

        result = _precheck_clinvar(
            sample_engine,
            reference_engine,
            sample_id=1,
            old_significances=old_sigs,
            new_significances=new_sigs,
        )

        assert result.candidate_count == 1
        assert len(result.watched_reclassified) == 1
        assert result.watched_reclassified[0]["rsid"] == "rs80357906"
        assert result.watched_reclassified[0]["new_significance"] == "Likely_pathogenic"

    def test_precheck_watched_no_significance_change(self, reference_engine, sample_engine):
        """T4-22n: Pre-check does NOT upgrade banner when watched variant has no change."""
        create_sample_tables(sample_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert(),
                [
                    {
                        "rsid": "rs80357906",
                        "chrom": "17",
                        "pos": 43091983,
                        "clinvar_significance": "Uncertain_significance",
                        "gene_symbol": "BRCA2",
                        "annotation_coverage": 2,
                    },
                ],
            )
            conn.execute(
                watched_variants.insert(),
                [
                    {
                        "rsid": "rs80357906",
                        "clinvar_significance_at_watch": "Uncertain_significance",
                    },
                ],
            )

        # Significance stays the same
        old_sigs = {"rs80357906": "Uncertain_significance"}
        new_sigs = {"rs80357906": "Uncertain_significance"}

        result = _precheck_clinvar(
            sample_engine,
            reference_engine,
            sample_id=1,
            old_significances=old_sigs,
            new_significances=new_sigs,
        )

        assert result.candidate_count == 0
        assert len(result.watched_reclassified) == 0

    def test_precheck_watched_direct_query_reclassification(self, reference_engine, sample_engine):
        """T4-22m variant: Watched variant reclassification detected via direct query path."""
        create_sample_tables(sample_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert(),
                [
                    {
                        "rsid": "rs80357906",
                        "chrom": "17",
                        "pos": 43091983,
                        "clinvar_significance": "Uncertain_significance",
                        "gene_symbol": "BRCA2",
                        "annotation_coverage": 2,
                    },
                ],
            )
            conn.execute(
                watched_variants.insert(),
                [
                    {
                        "rsid": "rs80357906",
                        "clinvar_significance_at_watch": "Uncertain_significance",
                    },
                ],
            )

        # Seed reference DB with updated ClinVar significance
        with reference_engine.begin() as conn:
            conn.execute(
                clinvar_variants.insert(),
                [
                    {
                        "rsid": "rs80357906",
                        "chrom": "17",
                        "pos": 43091983,
                        "ref": "A",
                        "alt": "G",
                        "significance": "Likely_pathogenic",
                        "review_stars": 2,
                    },
                ],
            )

        # Call without precomputed dicts (direct query path)
        result = _precheck_clinvar(
            sample_engine,
            reference_engine,
            sample_id=1,
        )

        assert result.candidate_count == 1
        assert len(result.watched_reclassified) == 1
        assert result.watched_reclassified[0]["rsid"] == "rs80357906"
        assert result.watched_reclassified[0]["old_significance"] == "Uncertain_significance"
        assert result.watched_reclassified[0]["new_significance"] == "Likely_pathogenic"

    def test_precheck_direct_query(self, reference_engine, sample_engine):
        """Pre-check without precomputed dicts (queries reference DB)."""
        create_sample_tables(sample_engine)

        # Seed reference with updated significance
        with reference_engine.begin() as conn:
            conn.execute(
                clinvar_variants.insert(),
                [
                    {
                        "rsid": "rs429358",
                        "chrom": "19",
                        "pos": 44908684,
                        "ref": "T",
                        "alt": "C",
                        "significance": "Pathogenic",
                        "review_stars": 3,
                    },
                ],
            )

        # Sample has old significance
        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert(),
                [
                    {
                        "rsid": "rs429358",
                        "chrom": "19",
                        "pos": 44908684,
                        "clinvar_significance": "risk_factor",
                        "annotation_coverage": 2,
                    },
                ],
            )

        result = _precheck_clinvar(
            sample_engine,
            reference_engine,
            sample_id=1,
        )

        assert result.candidate_count == 1
        assert result.reclassified_variants[0]["new_significance"] == "Pathogenic"


# ═══════════════════════════════════════════════════════════════════════
# Auto-update toggle tests
# ═══════════════════════════════════════════════════════════════════════


class TestAutoUpdateToggle:
    """Step 12: get_auto_update / set_auto_update round-trip + fallback."""

    def test_get_falls_back_to_defaults_when_row_missing(self, reference_engine):
        # No rows in auto_update_settings — fall back to AUTO_UPDATE_DEFAULTS.
        for db_name, expected in AUTO_UPDATE_DEFAULTS.items():
            assert get_auto_update(reference_engine, db_name) is expected

    def test_get_unknown_db_defaults_false(self, reference_engine):
        assert get_auto_update(reference_engine, "no_such_db") is False

    def test_set_inserts_then_get_returns_stored_value(self, reference_engine):
        # Override default (clinvar default is True → store False).
        set_auto_update(reference_engine, "clinvar", False)
        assert get_auto_update(reference_engine, "clinvar") is False

        # Override default (vep_bundle default is False → store True).
        set_auto_update(reference_engine, "vep_bundle", True)
        assert get_auto_update(reference_engine, "vep_bundle") is True

    def test_set_updates_existing_row(self, reference_engine):
        set_auto_update(reference_engine, "gnomad", False)
        set_auto_update(reference_engine, "gnomad", True)
        assert get_auto_update(reference_engine, "gnomad") is True

        # Only one row should exist for the key (primary key constraint).
        with reference_engine.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(auto_update_settings)
                .where(auto_update_settings.c.db_name == "gnomad")
            ).scalar_one()
        assert count == 1

    def test_set_updates_timestamp(self, reference_engine):
        set_auto_update(reference_engine, "dbnsfp", False)
        with reference_engine.connect() as conn:
            first = conn.execute(
                sa.select(auto_update_settings.c.updated_at).where(
                    auto_update_settings.c.db_name == "dbnsfp"
                )
            ).scalar_one()

        set_auto_update(reference_engine, "dbnsfp", True)
        with reference_engine.connect() as conn:
            second = conn.execute(
                sa.select(auto_update_settings.c.updated_at).where(
                    auto_update_settings.c.db_name == "dbnsfp"
                )
            ).scalar_one()

        assert second >= first

    def test_scheduled_check_honors_stored_toggle(self, reference_engine, tmp_path: Path):
        """run_scheduled_update_check reads via get_auto_update, not the dict."""
        settings = _settings_for_test(tmp_path)

        registry = MagicMock()
        registry.reference_engine = reference_engine
        registry.settings = settings

        # ClinVar default = True; explicitly disable it via the table.
        set_auto_update(reference_engine, "clinvar", False)

        update_info = VersionInfo(
            db_name="clinvar",
            latest_version="20260320",
            download_url="https://example.com/clinvar.vcf.gz",
            download_size_bytes=30_000_000,
        )

        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(available=[update_info]),
            ),
            patch(
                "backend.db.update_manager.run_clinvar_update",
            ) as mock_update,
        ):
            run_scheduled_update_check(registry)

        # With the toggle off, run_clinvar_update must NOT be invoked.
        mock_update.assert_not_called()

    def test_scheduled_check_runs_when_table_enables_default_off(
        self, reference_engine, tmp_path: Path
    ):
        """vep_bundle default is False; flip it on via the table → update runs."""
        settings = _settings_for_test(tmp_path)

        registry = MagicMock()
        registry.reference_engine = reference_engine
        registry.settings = settings

        set_auto_update(reference_engine, "vep_bundle", True)

        update_info = VersionInfo(
            db_name="vep_bundle",
            latest_version="2026-03-20",
            download_url="https://example.com/vep_bundle.db",
            download_size_bytes=400_000_000,
        )

        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(available=[update_info]),
            ),
            patch(
                "backend.db.update_manager.run_vep_bundle_update",
                return_value=UpdateResult(
                    db_name="vep_bundle",
                    previous_version="2026-03-01",
                    new_version="2026-03-20",
                ),
            ) as mock_update,
        ):
            run_scheduled_update_check(registry)

        mock_update.assert_called_once_with(settings)


# ═══════════════════════════════════════════════════════════════════════
# Scheduled update orchestrator tests
# ═══════════════════════════════════════════════════════════════════════


class TestScheduledUpdateCheck:
    def test_orchestrator_skips_auto_disabled(self, reference_engine, tmp_path: Path):
        settings = _settings_for_test(tmp_path)

        registry = MagicMock()
        registry.reference_engine = reference_engine
        registry.settings = settings

        # gnomAD's default toggle is on — explicitly disable so the scheduler
        # has a reason to skip this candidate.
        set_auto_update(reference_engine, "gnomad", False)

        update_info = VersionInfo(
            db_name="gnomad",
            latest_version="4.0",
            download_url="https://example.com/gnomad.db.gz",
            download_size_bytes=2_000_000_000,
        )

        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(available=[update_info]),
            ),
            patch("backend.tasks.huey_tasks.run_database_update_task") as mock_run_task,
            patch("backend.tasks.huey_tasks.create_database_update_job") as mock_create_job,
        ):
            result = run_scheduled_update_check(registry)

        # Toggle off → no pipeline-update dispatch.
        mock_run_task.assert_not_called()
        mock_create_job.assert_not_called()
        assert len(result.available) == 1

    def test_orchestrator_runs_clinvar_auto_update(self, reference_engine, tmp_path: Path):
        settings = _settings_for_test(tmp_path)

        registry = MagicMock()
        registry.reference_engine = reference_engine
        registry.settings = settings

        update_info = VersionInfo(
            db_name="clinvar",
            latest_version="20260320",
            download_url="https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh37/clinvar.vcf.gz",
            download_size_bytes=30_000_000,
        )

        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(available=[update_info]),
            ),
            patch(
                "backend.db.update_manager.run_clinvar_update",
                return_value=UpdateResult(
                    db_name="clinvar",
                    previous_version="20260301",
                    new_version="20260320",
                ),
            ) as mock_update,
        ):
            run_scheduled_update_check(registry)

        mock_update.assert_called_once_with(registry)


# ═══════════════════════════════════════════════════════════════════════
# API endpoint tests
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def update_client(tmp_data_dir: Path) -> TestClient:
    """FastAPI TestClient with update routes enabled."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc

        reset_registry()


class TestUpdateAPI:
    def test_check_updates(self, update_client):
        """``GET /api/updates/check`` dispatches every check via ``CHECK_FNS``.

        The endpoint → ``check_all_updates`` loop iterates the module-level
        ``CHECK_FNS`` dict (built at import), so patching the bound name
        ``backend.db.update_manager.check_clinvar_update`` is a no-op — the dict
        still holds the original callable. We must patch the dict itself. Every
        entry is stubbed to return ``None`` (no available update, no network), so
        all DBs land in ``up_to_date`` and the assertions actually prove the
        dispatch reached the patched callables.
        """
        from backend.db.update_manager import CHECK_FNS

        stubs = {name: MagicMock(return_value=None) for name in CHECK_FNS}
        with patch.dict(
            "backend.db.update_manager.CHECK_FNS",
            stubs,
            clear=True,
        ):
            resp = update_client.get("/api/updates/check")

        assert resp.status_code == 200
        data = resp.json()
        assert "available" in data
        assert "up_to_date" in data
        assert "checked_at" in data
        # Dispatch went through the patched dict: every stubbed check was invoked
        # and its None return landed the DB in up_to_date (none in available).
        assert set(data["up_to_date"]) == set(stubs)
        assert data["available"] == []
        for stub in stubs.values():
            stub.assert_called_once()

    def test_get_status(self, update_client):
        resp = update_client.get("/api/updates/status")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        db_names = [d["db_name"] for d in data]
        assert "clinvar" in db_names

    def test_get_history_empty(self, update_client):
        resp = update_client.get("/api/updates/history")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_prompts_empty(self, update_client):
        resp = update_client.get("/api/updates/prompts")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_dismiss_nonexistent_prompt(self, update_client):
        resp = update_client.post("/api/updates/prompts/999/dismiss")
        assert resp.status_code == 404

    def test_trigger_unsupported_db(self, update_client):
        resp = update_client.post("/api/updates/trigger", json={"db_name": "nonexistent_db"})
        assert resp.status_code == 400
        assert "not supported" in resp.json()["detail"].lower()

    def test_trigger_clinvar_update(self, update_client):
        with (
            patch(
                "backend.tasks.huey_tasks.run_database_update_task",
            ),
            patch(
                "backend.tasks.huey_tasks.create_database_update_job",
                return_value="test-job-id",
            ),
        ):
            resp = update_client.post("/api/updates/trigger", json={"db_name": "clinvar"})

        assert resp.status_code == 202
        data = resp.json()
        assert data["db_name"] == "clinvar"
        assert data["job_id"] == "test-job-id"

    def test_trigger_bundle_dbs_accepted(self, update_client):
        """Regression: vep_bundle / lai_bundle / ancestry_pca are valid update
        targets. lai_bundle and ancestry_pca previously 400'd because the
        endpoint's supported set only covered build-function DBs + vep_bundle,
        even though check_all_updates surfaces them and the scheduler can apply
        them. The UI must never offer an update the endpoint rejects.
        """
        for db_name in ("vep_bundle", "lai_bundle", "ancestry_pca"):
            with (
                patch("backend.tasks.huey_tasks.run_database_update_task"),
                patch(
                    "backend.tasks.huey_tasks.create_database_update_job",
                    return_value=f"job-{db_name}",
                ),
            ):
                resp = update_client.post("/api/updates/trigger", json={"db_name": db_name})
            assert resp.status_code == 202, (db_name, resp.text)
            assert resp.json()["db_name"] == db_name

    def test_status_returns_enhanced_fields(self, update_client):
        """P4-17: status returns display_name, version_display, downloaded_at."""
        resp = update_client.get("/api/updates/status")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

        first = data[0]
        assert "display_name" in first
        assert "version_display" in first
        assert "downloaded_at" in first
        assert "update_available" in first
        assert isinstance(first["update_available"], bool)

        # Check that display_name comes from database_registry
        clinvar = next((d for d in data if d["db_name"] == "clinvar"), None)
        assert clinvar is not None
        assert clinvar["display_name"] == "ClinVar"


# ═══════════════════════════════════════════════════════════════════════
# Version stamping tests (P4-17 / T4-18)
# ═══════════════════════════════════════════════════════════════════════


class TestVersionStamping:
    """T4-18: Version stamps correctly recorded after each database load/update."""

    def test_get_all_version_stamps_empty(self, reference_engine):
        stamps = get_all_version_stamps(reference_engine)
        assert stamps == []

    def test_get_all_version_stamps_after_record(self, reference_engine):
        _record_version(reference_engine, "clinvar", "20260315", 30_000_000)
        _record_version(reference_engine, "gnomad", "2.1.1", 2_000_000_000)

        stamps = get_all_version_stamps(reference_engine)
        assert len(stamps) == 2

        by_name = {s["db_name"]: s for s in stamps}
        assert by_name["clinvar"]["version"] == "20260315"
        assert by_name["clinvar"]["file_size_bytes"] == 30_000_000
        assert by_name["clinvar"]["downloaded_at"] is not None
        assert by_name["gnomad"]["version"] == "2.1.1"

    def test_version_stamp_updated_on_second_record(self, reference_engine):
        _record_version(reference_engine, "clinvar", "20260301", 29_000_000)
        _record_version(reference_engine, "clinvar", "20260315", 30_000_000)

        stamps = get_all_version_stamps(reference_engine)
        assert len(stamps) == 1
        assert stamps[0]["version"] == "20260315"
        assert stamps[0]["file_size_bytes"] == 30_000_000

    def test_format_version_display_clinvar_date(self):
        assert format_version_display("20260315", "clinvar") == "Mar 2026"
        assert format_version_display("20260101", "clinvar") == "Jan 2026"
        assert format_version_display("20251201", "clinvar") == "Dec 2025"

    def test_format_version_display_non_date(self):
        assert format_version_display("2.1.1", "gnomad") == "2.1.1"
        assert format_version_display("4.6a", "dbnsfp") == "4.6a"
        assert format_version_display("110", "vep_bundle") == "110"

    def test_format_version_display_none(self):
        assert format_version_display(None, "clinvar") is None

    def test_stamps_have_downloaded_at_as_iso(self, reference_engine):
        _record_version(reference_engine, "clinvar", "20260315", 1000)

        stamps = get_all_version_stamps(reference_engine)
        assert len(stamps) == 1
        # downloaded_at should be an ISO-format string
        dt_str = stamps[0]["downloaded_at"]
        assert dt_str is not None
        # Verify it parses
        from datetime import datetime

        datetime.fromisoformat(dt_str)


# ═══════════════════════════════════════════════════════════════════════
# VEP bundle semver write tests (Step 6 — ADNA-00b part 2, Plan §5.5)
# ═══════════════════════════════════════════════════════════════════════
#
# ``run_vep_bundle_update`` now writes the manifest's ``version`` (semver)
# into ``database_versions`` rather than the bundle's ``build_date``. When
# the downloaded SQLite carries its own ``bundle_metadata.bundle_version``
# that disagrees with the manifest, a structured warning
# (``vep_bundle_metadata_version_mismatch``) is logged but the update
# never fails — the manifest is the contract. Pre-v2.0.0 bundles omit
# ``bundle_version`` entirely and the parity check is silently skipped.


class TestRunVepBundleUpdateSemver:
    """Step 6: manifest semver write + bundle-metadata parity advisory."""

    @staticmethod
    def _bundle_bytes(
        *, build_date: str = "2026-05-01", bundle_version: str | None = "v2.0.0"
    ) -> bytes:
        """Build an in-memory SQLite bundle with the requested metadata rows."""
        import sqlite3
        import tempfile

        path = Path(tempfile.mkstemp(suffix=".db")[1])
        try:
            with sqlite3.connect(str(path)) as conn:
                conn.execute("CREATE TABLE bundle_metadata (key TEXT PRIMARY KEY, value TEXT)")
                conn.execute(
                    "INSERT INTO bundle_metadata (key, value) VALUES (?, ?)",
                    ("build_date", build_date),
                )
                if bundle_version is not None:
                    conn.execute(
                        "INSERT INTO bundle_metadata (key, value) VALUES (?, ?)",
                        ("bundle_version", bundle_version),
                    )
                conn.commit()
            return path.read_bytes()
        finally:
            path.unlink(missing_ok=True)

    @staticmethod
    def _serve(payload: bytes) -> tuple[str, object]:
        """Spin up an in-memory HTTP server that returns ``payload`` on GET."""
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from typing import Any

        class _Handler(BaseHTTPRequestHandler):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self._payload = payload
                super().__init__(*args, **kwargs)

            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Length", str(len(self._payload)))
                self.send_header("Content-Type", "application/octet-stream")
                self.end_headers()
                self.wfile.write(self._payload)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return None

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        return f"http://{host}:{port}/payload", server

    @staticmethod
    def _write_manifest(tmp_path: Path, *, url: str, sha256: str, size: int, version: str) -> Path:
        """Write a minimal manifest with a single ``vep_bundle`` entry."""
        import json as _json

        path = tmp_path / "manifest.json"
        path.write_text(
            _json.dumps(
                {
                    "schema_version": 1,
                    "generated_at": "2026-05-18T00:00:00Z",
                    "bundles": {
                        "vep_bundle": {
                            "version": version,
                            "build_date": "2026-05-01",
                            "url": url,
                            "sha256": sha256,
                            "size_bytes": size,
                        },
                    },
                    "pipeline_pins": {},
                }
            ),
            encoding="utf-8",
        )
        return path

    @pytest.fixture(autouse=True)
    def _clear_manifest_cache(self, monkeypatch: pytest.MonkeyPatch):
        from backend.db import manifest as manifest_mod

        monkeypatch.delenv(manifest_mod.MANIFEST_PATH_ENV, raising=False)
        manifest_mod.reset_cache()
        yield
        manifest_mod.reset_cache()

    def _settings_with_ref(self, tmp_path: Path) -> Settings:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "downloads").mkdir()
        engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
        reference_metadata.create_all(engine)
        engine.dispose()
        return Settings(data_dir=data_dir, wal_mode=False)

    def _patch_bundled_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        from backend.db import database_registry as registry_mod

        fake_bundled = tmp_path / "bundled"
        fake_bundled.mkdir()
        monkeypatch.setattr(registry_mod, "BUNDLED_DIR", fake_bundled)
        return fake_bundled

    def test_manifest_semver_is_written_not_build_date(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Happy path: manifest ``version`` (semver) lands in database_versions."""
        import hashlib

        from backend.db import manifest as manifest_mod
        from backend.db.update_manager import run_vep_bundle_update

        settings = self._settings_with_ref(tmp_path)
        self._patch_bundled_dir(tmp_path, monkeypatch)

        payload = self._bundle_bytes(build_date="2026-05-01", bundle_version="v2.0.0")
        url, server = self._serve(payload)
        try:
            sha = hashlib.sha256(payload).hexdigest()
            manifest_path = self._write_manifest(
                tmp_path, url=url, sha256=sha, size=len(payload), version="v2.0.0"
            )
            monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

            result = run_vep_bundle_update(settings)
        finally:
            server.shutdown()

        assert result is not None
        # Manifest semver — not the bundle's ``build_date`` — is the new
        # version recorded against the row.
        assert result.new_version == "v2.0.0"

        ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
        try:
            row = (
                ref_engine.connect()
                .execute(
                    sa.select(database_versions).where(database_versions.c.db_name == "vep_bundle")
                )
                .fetchone()
            )
        finally:
            ref_engine.dispose()
        assert row is not None
        assert row.version == "v2.0.0"

    def test_metadata_version_mismatch_emits_structured_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """File ``bundle_version`` ≠ manifest ``version`` → structured warning, no failure."""
        import hashlib

        from structlog.testing import capture_logs

        from backend.db import manifest as manifest_mod
        from backend.db.update_manager import run_vep_bundle_update

        settings = self._settings_with_ref(tmp_path)
        self._patch_bundled_dir(tmp_path, monkeypatch)

        # Build a bundle whose embedded bundle_version disagrees with the
        # manifest — common during a botched release where the asset and the
        # manifest entry get out of sync.
        payload = self._bundle_bytes(build_date="2026-05-01", bundle_version="v1.9.9")
        url, server = self._serve(payload)
        try:
            sha = hashlib.sha256(payload).hexdigest()
            manifest_path = self._write_manifest(
                tmp_path, url=url, sha256=sha, size=len(payload), version="v2.0.0"
            )
            monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

            with capture_logs() as cap_logs:
                result = run_vep_bundle_update(settings)
        finally:
            server.shutdown()

        # Manifest semver still wins; the warning is advisory only.
        assert result is not None
        assert result.new_version == "v2.0.0"

        warnings = [
            ev for ev in cap_logs if ev.get("event") == "vep_bundle_metadata_version_mismatch"
        ]
        assert len(warnings) == 1
        warn = warnings[0]
        assert warn["log_level"] == "warning"
        assert warn["manifest_version"] == "v2.0.0"
        assert warn["metadata_bundle_version"] == "v1.9.9"
        assert warn["build_date"] == "2026-05-01"

    def test_missing_metadata_bundle_version_is_tolerated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Pre-v2.0.0 bundles omit ``bundle_version`` — no warning, no failure."""
        import hashlib

        from structlog.testing import capture_logs

        from backend.db import manifest as manifest_mod
        from backend.db.update_manager import run_vep_bundle_update

        settings = self._settings_with_ref(tmp_path)
        self._patch_bundled_dir(tmp_path, monkeypatch)

        payload = self._bundle_bytes(build_date="2026-05-01", bundle_version=None)
        url, server = self._serve(payload)
        try:
            sha = hashlib.sha256(payload).hexdigest()
            manifest_path = self._write_manifest(
                tmp_path, url=url, sha256=sha, size=len(payload), version="v2.0.0"
            )
            monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))

            with capture_logs() as cap_logs:
                result = run_vep_bundle_update(settings)
        finally:
            server.shutdown()

        assert result is not None
        assert result.new_version == "v2.0.0"
        assert [
            ev for ev in cap_logs if ev.get("event") == "vep_bundle_metadata_version_mismatch"
        ] == []
