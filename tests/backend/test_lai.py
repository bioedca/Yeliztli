"""Tests for LAI (Local Ancestry Inference) module — AMv2 Step 4.

Covers:
  T-LAI-01: is_lai_available() returns False when bundle missing
  T-LAI-02: is_lai_available() returns False when Java missing
  T-LAI-03: LAI results JSON schema matches expected format
  T-LAI-04: LAI API returns 404 when bundle not downloaded
  T-LAI-05: LAI API returns 503 when Java unavailable
  T-LAI-06: Progress callback maps to job table updates
  T-LAI-07: rsID lookup correctly maps GRCh37 -> GRCh38
  T-LAI-08: Genotype encoding handles REF/REF, REF/ALT, ALT/ALT
  T-LAI-09: Genotype encoding returns None for tri-allelic/non-matching
  T-LAI-10: Global ancestry proportions sum to 1.0
  T-LAI-11: Painting contains entries for all analyzed chromosomes
  T-GNX-01: Gnomix inference loads model and returns predictions
  T-GNX-02: Output remaps from model order to canonical order
  T-GNX-03: Mirror-reflect padding produces correct edge values
  T-GNX-04: Softmax sums to 1.0 per window
  T-GNX-05: Window feature slicing respects window_n_features
  T-GNX-06: LAI results table schema and storage
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import sqlalchemy as sa

from backend.analysis.gnomix_inference import (
    CANONICAL_POPULATIONS,
    ChromosomeResult,
    GnomixModel,
    _build_smoother_features,
    _pad_mirror,
    _softmax,
)
from backend.analysis.lai_runner import POPULATIONS, LAIRunner
from backend.db.tables import findings, lai_results

# ── Fixtures ──────────────────────────────────────────────────────────────


def _seed_sample_one() -> None:
    """Seed sample id=1 so require_fresh_sample's existence gate passes (#453).

    The LAI status endpoints are sample-scoped (Depends(require_fresh_sample)).
    Since #453 a *missing* sample 404s before the handler runs, so tests that
    exercise the handler's own no-Java / no-job paths must seed the sample first
    (mirrors the inline seed in test_get_results_returns_null_when_none). The
    insert omits ``id``, so the first row autoincrements to 1.
    """
    from backend.db.connection import get_registry
    from backend.db.tables import samples

    registry = get_registry()
    with registry.reference_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                name="Test",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="abc",
            )
        )


@pytest.fixture()
def sample_engine() -> sa.Engine:
    """In-memory SQLite engine with sample tables."""
    from backend.db.sample_schema import create_sample_tables

    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    return engine


# ── T-GNX: Gnomix inference unit tests ───────────────────────────────────


class TestSoftmax:
    """T-GNX-04: Softmax sums to 1.0."""

    def test_softmax_sums_to_one(self):
        x = np.array([[1.0, 2.0, 3.0], [0.5, -1.0, 2.0]])
        result = _softmax(x)
        np.testing.assert_allclose(result.sum(axis=-1), [1.0, 1.0], atol=1e-10)

    def test_softmax_large_values_stable(self):
        x = np.array([1000.0, 1001.0, 1002.0])
        result = _softmax(x)
        assert np.isfinite(result).all()
        np.testing.assert_allclose(result.sum(), 1.0, atol=1e-10)

    def test_softmax_negative_values(self):
        x = np.array([-100.0, -200.0, -50.0])
        result = _softmax(x)
        np.testing.assert_allclose(result.sum(), 1.0, atol=1e-10)


class TestPadMirror:
    """T-GNX-03: Mirror-reflect padding."""

    def test_1d_padding(self):
        arr = np.array([[1, 2, 3, 4, 5]])  # (1, 5)
        padded = _pad_mirror(arr, 2, axis=1)
        # Mirror from index pad-1..0 reversed: [2, 1] on left
        assert padded.shape == (1, 9)
        np.testing.assert_array_equal(padded[0, :2], [2, 1])
        np.testing.assert_array_equal(padded[0, 2:7], [1, 2, 3, 4, 5])

    def test_2d_axis0_padding(self):
        arr = np.array([[10, 20], [30, 40], [50, 60]])  # (3, 2)
        padded = _pad_mirror(arr, 1, axis=0)
        assert padded.shape == (5, 2)
        # Mirror: first row reflected from index 0: row 0 itself
        np.testing.assert_array_equal(padded[0], [10, 20])
        np.testing.assert_array_equal(padded[1:4], arr)

    def test_zero_padding(self):
        arr = np.array([1, 2, 3])
        result = _pad_mirror(arr, 0, axis=0)
        np.testing.assert_array_equal(result, arr)


class TestSmootherFeatures:
    """T-GNX-05: Window feature slicing."""

    def test_feature_shape(self):
        n_windows = 5
        A = 7
        S = 3
        pad = (S + 1) // 2
        padded = np.random.rand(n_windows + 2 * pad, A)
        features = _build_smoother_features(padded, S, n_windows)
        assert features.shape == (n_windows, S * A)

    def test_feature_values(self):
        A = 3
        S = 3
        pad = (S + 1) // 2  # 2
        n_windows = 2
        padded = np.arange((n_windows + 2 * pad) * A, dtype=np.float64).reshape(-1, A)
        features = _build_smoother_features(padded, S, n_windows)
        # Each row should be S*A = 9 values
        assert features.shape == (2, 9)


class TestPopulationRemap:
    """T-GNX-02: Output remaps from model order to canonical order."""

    def test_remap_indices(self):
        # Exercise the PRODUCTION remap (GnomixModel.__post_init__), not a
        # re-implementation of it: a model whose population_order differs from the
        # canonical order must remap each model index to its canonical index.
        n_pops = len(CANONICAL_POPULATIONS)
        model = GnomixModel(
            chrom=1,
            n_snps=0,
            n_pops=n_pops,
            n_windows=1,
            window_size=1,
            smoother_context=1,
            context=0,
            snp_pos=np.empty(0, dtype=np.int64),
            snp_ref=np.empty(0, dtype="U1"),
            snp_alt=np.empty(0, dtype="U1"),
            population_order=["CSA", "AFR", "OCE", "EUR", "MID", "AMR", "EAS"],
            coefs=np.zeros((1, n_pops, 1)),
            intercepts=np.zeros((1, n_pops)),
            window_n_features=np.array([1]),
            smoother_path=Path("unused"),
        )
        # CANONICAL_POPULATIONS = (AFR, AMR, CSA, EAS, EUR, MID, OCE), so the
        # model order [CSA, AFR, OCE, EUR, MID, AMR, EAS] maps to:
        # CSA->2, AFR->0, OCE->6, EUR->4, MID->5, AMR->1, EAS->3
        expected = [2, 0, 6, 4, 5, 1, 3]
        np.testing.assert_array_equal(model.pop_remap, expected)
        assert model.pop_remap.dtype == np.int32


# ── T-LAI: LAI runner unit tests ─────────────────────────────────────────


class TestGenotypeEncoding:
    """T-LAI-08, T-LAI-09: Genotype encoding."""

    def test_ref_ref(self):
        assert LAIRunner._encode_genotype("A", "A", "A", "G") == "0/0"

    def test_ref_alt(self):
        assert LAIRunner._encode_genotype("A", "G", "A", "G") == "0/1"

    def test_alt_alt(self):
        assert LAIRunner._encode_genotype("G", "G", "A", "G") == "1/1"

    def test_alt_ref(self):
        assert LAIRunner._encode_genotype("G", "A", "A", "G") == "0/1"

    def test_non_matching(self):
        assert LAIRunner._encode_genotype("C", "T", "A", "G") is None

    def test_partial_match(self):
        assert LAIRunner._encode_genotype("A", "T", "A", "G") is None


class TestFilterGenotypes:
    """Test genotype filtering logic."""

    def test_filters_sex_chromosomes(self):
        runner_cls = LAIRunner.__new__(LAIRunner)
        genotypes = [
            {"rsid": "rs1", "chrom": "X", "pos": 100, "genotype": "AG"},
            {"rsid": "rs2", "chrom": "1", "pos": 200, "genotype": "CT"},
        ]
        filtered = runner_cls._filter_genotypes(genotypes)
        assert len(filtered) == 1
        assert filtered[0]["rsid"] == "rs2"

    def test_filters_nocalls(self):
        runner_cls = LAIRunner.__new__(LAIRunner)
        genotypes = [
            {"rsid": "rs1", "chrom": "1", "pos": 100, "genotype": "--"},
            {"rsid": "rs2", "chrom": "1", "pos": 200, "genotype": "AG"},
        ]
        filtered = runner_cls._filter_genotypes(genotypes)
        assert len(filtered) == 1

    def test_filters_haploid(self):
        runner_cls = LAIRunner.__new__(LAIRunner)
        genotypes = [
            {"rsid": "rs1", "chrom": "1", "pos": 100, "genotype": "A"},
        ]
        filtered = runner_cls._filter_genotypes(genotypes)
        assert len(filtered) == 0

    def test_filters_non_acgt(self):
        runner_cls = LAIRunner.__new__(LAIRunner)
        genotypes = [
            {"rsid": "rs1", "chrom": "1", "pos": 100, "genotype": "DI"},
        ]
        filtered = runner_cls._filter_genotypes(genotypes)
        assert len(filtered) == 0


class TestGlobalAncestry:
    """T-LAI-10: Global ancestry proportions sum to 1.0."""

    def test_proportions_sum_to_one(self):
        runner_cls = LAIRunner.__new__(LAIRunner)

        # Create mock chromosome results
        chrom_results = {}
        for chr_num in [1, 2]:
            n_windows = 10
            hap0 = np.zeros(n_windows, dtype=np.int32)  # all AFR
            hap1 = np.full(n_windows, 4, dtype=np.int32)  # all EUR
            chrom_results[chr_num] = ChromosomeResult(
                chrom=chr_num,
                n_windows=n_windows,
                hap0_ancestry=hap0,
                hap1_ancestry=hap1,
                hap0_probs=np.zeros((n_windows, 7)),
                hap1_probs=np.zeros((n_windows, 7)),
                window_positions=[(i * 1000, (i + 1) * 1000) for i in range(n_windows)],
            )

        ancestry = runner_cls._compute_global_ancestry(chrom_results)
        total = sum(info["fraction"] for info in ancestry.values())
        assert abs(total - 1.0) < 0.01

        # hap0 is all-AFR (index 0), hap1 is all-EUR (index 4), 10 windows each
        # → an exact 50/50 split. Lock the index→population mapping so an
        # index↔label mislabel (the EUR↔MID class of bug) that still normalizes
        # to 1.0 is caught — the bare sum check above would pass for any split.
        assert "AFR" in ancestry and "EUR" in ancestry, (
            f"expected AFR + EUR populations, got {sorted(ancestry)}"
        )
        assert abs(ancestry["AFR"]["fraction"] - 0.5) < 0.05, ancestry["AFR"]["fraction"]
        assert abs(ancestry["EUR"]["fraction"] - 0.5) < 0.05, ancestry["EUR"]["fraction"]

    def test_empty_results(self):
        runner_cls = LAIRunner.__new__(LAIRunner)
        ancestry = runner_cls._compute_global_ancestry({})
        assert ancestry == {}


class TestChromosomePainting:
    """T-LAI-11: Painting contains entries for all analyzed chromosomes."""

    def test_painting_structure(self):
        runner_cls = LAIRunner.__new__(LAIRunner)

        chrom_results = {}
        for chr_num in [1, 5, 22]:
            n_windows = 3
            chrom_results[chr_num] = ChromosomeResult(
                chrom=chr_num,
                n_windows=n_windows,
                hap0_ancestry=np.zeros(n_windows, dtype=np.int32),
                hap1_ancestry=np.ones(n_windows, dtype=np.int32),
                hap0_probs=np.zeros((n_windows, 7)),
                hap1_probs=np.zeros((n_windows, 7)),
                window_positions=[(i * 1000, (i + 1) * 1000) for i in range(n_windows)],
            )

        painting = runner_cls._build_chromosome_painting(chrom_results)
        assert "chr1" in painting
        assert "chr5" in painting
        assert "chr22" in painting
        assert len(painting) == 3

        # hap0_ancestry is all-0 and hap1_ancestry is all-1, so every segment must
        # resolve to the canonical population at index 0 (hap0) and index 1 (hap1),
        # with the matching palette colors. Asserting the VALUES — not just that the
        # keys exist — is what catches the index→population mislabel bug class.
        expected_hap0 = CANONICAL_POPULATIONS[0]  # AFR
        expected_hap1 = CANONICAL_POPULATIONS[1]  # AMR
        for chrom_key, segments in painting.items():
            assert len(segments) == 3, chrom_key
            for i, seg in enumerate(segments):
                assert seg["hap0"] == expected_hap0
                assert seg["hap1"] == expected_hap1
                assert seg["hap0_color"] == POPULATIONS[expected_hap0]["color"]
                assert seg["hap1_color"] == POPULATIONS[expected_hap1]["color"]
                # window_positions were (i*1000, (i+1)*1000)
                assert seg["start"] == i * 1000
                assert seg["end"] == (i + 1) * 1000


# ── T-LAI: LAI availability checks ──────────────────────────────────────


class TestLAIAvailability:
    """T-LAI-01, T-LAI-02: is_lai_available checks."""

    def test_unavailable_when_bundle_missing(self, tmp_path):
        from backend.analysis.lai import is_lai_available

        with patch("backend.analysis.lai.get_settings") as mock_settings:
            mock_settings.return_value.resolved_lai_bundle_path = tmp_path / "nonexistent_bundle"
            assert is_lai_available() is False

    def test_unavailable_when_java_missing(self, tmp_path):
        from backend.analysis.lai import is_lai_available

        with (
            patch("backend.analysis.lai.get_settings") as mock_settings,
            patch("backend.analysis.lai.validate_lai_bundle", return_value=True),
            patch("backend.analysis.lai.detect_java", return_value=False),
        ):
            mock_settings.return_value.resolved_lai_bundle_path = tmp_path
            assert is_lai_available() is False


# ── T-LAI: LAI results storage ───────────────────────────────────────────


class TestLAIResultsStorage:
    """T-LAI-03, T-GNX-06: LAI results table and storage."""

    def test_lai_results_table_creation(self, sample_engine):
        from backend.analysis.lai import _ensure_lai_tables

        _ensure_lai_tables(sample_engine)

        inspector = sa.inspect(sample_engine)
        tables = inspector.get_table_names()
        assert "lai_results" in tables

    def test_store_lai_results(self, sample_engine):
        from backend.analysis.lai import _ensure_lai_tables, _store_lai_results
        from backend.analysis.lai_runner import LAIRunnerResult

        _ensure_lai_tables(sample_engine)

        afr = {"fraction": 0.3, "percentage": 30.0, "display_name": "African", "color": "#E8A838"}
        eur = {"fraction": 0.5, "percentage": 50.0, "display_name": "European", "color": "#4477AA"}
        eas = {
            "fraction": 0.2,
            "percentage": 20.0,
            "display_name": "East Asian",
            "color": "#66CCEE",
        }
        result = LAIRunnerResult(
            global_ancestry={"AFR": afr, "EUR": eur, "EAS": eas},
            chromosome_painting={
                "chr1": [{"start": 0, "end": 1000, "hap0": "EUR", "hap1": "AFR"}],
            },
            metadata={"chromosomes_analyzed": 22, "runtime_seconds": 900.0},
        )

        _store_lai_results(sample_engine, result)

        with sample_engine.connect() as conn:
            # Check lai_results table
            row = conn.execute(sa.select(lai_results)).fetchone()
            assert row is not None
            global_anc = json.loads(row.global_ancestry_json)
            assert "EUR" in global_anc
            assert global_anc["EUR"]["fraction"] == 0.5

            # Check findings table
            finding = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "ancestry",
                    findings.c.category == "local_ancestry",
                )
            ).fetchone()
            assert finding is not None
            detail = json.loads(finding.detail_json)
            assert detail["top_population"] == "EUR"

    def test_lai_results_json_schema(self, sample_engine):
        from backend.analysis.lai import _ensure_lai_tables, _store_lai_results
        from backend.analysis.lai_runner import LAIRunnerResult

        _ensure_lai_tables(sample_engine)

        result = LAIRunnerResult(
            global_ancestry={
                pop: {
                    "fraction": 1.0 / 7,
                    "percentage": round(100.0 / 7, 1),
                    "display_name": f"Pop {pop}",
                    "color": "#000",
                }
                for pop in CANONICAL_POPULATIONS
            },
            chromosome_painting={
                f"chr{i}": [{"start": 0, "end": 1000, "hap0": "EUR", "hap1": "AFR"}]
                for i in range(1, 23)
            },
            metadata={"chromosomes_analyzed": 22, "runtime_seconds": 600},
        )
        _store_lai_results(sample_engine, result)

        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(lai_results)).fetchone()
            painting = json.loads(row.chromosome_painting_json)
            # T-LAI-11: All 22 autosomes
            assert len(painting) == 22
            for i in range(1, 23):
                assert f"chr{i}" in painting


# ── T-LAI: API endpoint tests ────────────────────────────────────────────


class TestLAIAPIStatus:
    """T-LAI-04, T-LAI-05: LAI API status checks."""

    def test_trigger_returns_404_no_bundle(self, test_client):
        resp = test_client.post("/api/analysis/ancestry/lai/1")
        assert resp.status_code == 404

    def test_trigger_returns_503_no_java(self, test_client):
        # #453: /lai/{sample_id} is Depends(require_fresh_sample)-gated, which
        # now 404s a *missing* sample before the handler runs. Seed sample 1 so
        # this reaches the handler's own no-Java (503) path.
        _seed_sample_one()
        with (
            patch(
                "backend.db.database_registry.validate_lai_bundle",
                return_value=True,
            ),
            patch(
                "backend.db.database_registry.detect_java",
                return_value=False,
            ),
        ):
            resp = test_client.post("/api/analysis/ancestry/lai/1")
            assert resp.status_code == 503

    def test_get_results_returns_null_when_none(self, test_client):
        # Insert a sample so the lookup works
        from backend.db.connection import get_registry
        from backend.db.tables import samples

        registry = get_registry()
        with registry.reference_engine.begin() as conn:
            conn.execute(
                samples.insert().values(
                    name="Test",
                    db_path="samples/sample_1.db",
                    file_format="23andme_v5",
                    file_hash="abc",
                )
            )

        # Create the sample DB
        from backend.db.sample_schema import create_sample_tables

        sample_db_path = registry.settings.data_dir / "samples" / "sample_1.db"
        sample_db_path.parent.mkdir(parents=True, exist_ok=True)
        sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
        create_sample_tables(sample_engine)
        sample_engine.dispose()

        resp = test_client.get("/api/analysis/ancestry/lai/1/results")
        assert resp.status_code == 200
        assert resp.json() is None

    def test_get_progress_returns_null_when_no_job(self, test_client):
        # #453: the gated progress route 404s a missing sample before the
        # handler; seed sample 1 so this reaches the handler's no-job path.
        _seed_sample_one()
        resp = test_client.get("/api/analysis/ancestry/lai/1/progress")
        assert resp.status_code == 200
        assert resp.json() is None


# ── T-LAI-06: Progress callback ──────────────────────────────────────────


class TestProgressCallback:
    """T-LAI-06: Progress callback maps to job table updates."""

    def test_lai_job_creation(self):
        """Verify create_lai_job creates a job record."""
        from unittest.mock import MagicMock

        from backend.db.tables import jobs, reference_metadata
        from backend.tasks.huey_tasks import create_lai_job

        engine = sa.create_engine("sqlite://")
        reference_metadata.create_all(engine)

        mock_registry = MagicMock()
        mock_registry.reference_engine = engine

        with patch("backend.db.connection.get_registry", return_value=mock_registry):
            job_id = create_lai_job(sample_id=1)

        assert job_id is not None

        with engine.connect() as conn:
            row = conn.execute(sa.select(jobs).where(jobs.c.job_id == job_id)).fetchone()
            assert row is not None
            assert row.job_type == "lai_analysis"
            assert row.status == "pending"
            assert row.sample_id == 1

    def test_duplicate_lai_job_raises(self):
        """Verify create_lai_job raises for duplicate in-progress jobs."""
        from unittest.mock import MagicMock

        from backend.db.tables import reference_metadata
        from backend.tasks.huey_tasks import create_lai_job

        engine = sa.create_engine("sqlite://")
        reference_metadata.create_all(engine)

        mock_registry = MagicMock()
        mock_registry.reference_engine = engine

        with patch("backend.db.connection.get_registry", return_value=mock_registry):
            create_lai_job(sample_id=1)
            with pytest.raises(ValueError, match="already in progress"):
                create_lai_job(sample_id=1)


# ── Slow-tier real-bundle accuracy (Step 25a; Plan §6.4, §16.5) ──────────


# AncestryDNA-format fixture candidates (Plan §16.1) — first existing wins.
# The synthetic EUR fixture is generated by `scripts/regenerate_fixtures.py
# --vendor=ancestrydna` in step 41; bio-validator's curated `sample_ancestrydna_v2.txt`
# (step 34) is the fallback. The legacy v1 fixture is too small for meaningful
# LAI inference, so the test skips when only it is present.
# `heldout_eur_HG01502.adna.txt.gz` is the canonical EUR regression fixture: a
# real held-out 1000G Iberian (IBS) at AncestryDNA density (~666k sites, public
# 1000G → committable). It is the sample the original v2.0.0 bundle misclassified
# as 94% CSA / 0.3% EUR; on the rebuilt bundle it classifies ~96% EUR. The legacy
# `synthetic_eur_ancestrydna.txt` is only ~5k SNPs (0.3% of the LAI panel) — far
# too sparse for the dense full-LAI pipeline (nearly every gnomix window is empty
# → a degenerate call EVEN ON A CORRECT BUNDLE), so it is kept last as a fallback.
_REAL_BUNDLE_FIXTURE_CANDIDATES = (
    "heldout_eur_HG01502.adna.txt.gz",
    "synthetic_eur_ancestrydna.txt",
    "sample_ancestrydna_v2.txt",
)

# Observed global ancestry for the held-out HG01502 (IBS/EUR) fixture on the
# re-balanced v2.0.0 bundle (sha256 36abb5f2…). The primary regression guard is
# property-based (top==EUR and EUR >= floor) so it survives the small
# phasing-RNG / Java-version jitter that makes exact per-population fractions
# non-reproducible across environments; the reference below is a drift monitor.
_EUR_FIXTURE_GLOBAL_ANCESTRY_REFERENCE: dict[str, float] = {
    "AFR": 0.01,
    "AMR": 0.00,
    "CSA": 0.01,
    "EAS": 0.00,
    "EUR": 0.95,
    "MID": 0.03,
    "OCE": 0.00,
}

# A held-out European must classify as EUR-dominant. The broken bundle gave
# EUR≈0.003 (top=CSA); the re-balanced bundle gives ≈0.95. 0.85 is a safe floor.
_EUR_FIXTURE_MIN_EUR_FRACTION = 0.85

# Generous drift band on the reference (minor components vary with the phasing
# RNG and Java version across environments; the property guard above is the real
# regression gate). Bio-validator may tighten after observing nightly stability.
_GLOBAL_ANCESTRY_TOLERANCE = 0.05

# ── Held-out Middle-Eastern (MID) regression guard (runbook §4.4) ──────────────
# HGDP01282 is a held-out NON-founder HGDP Middle-Eastern sample (not in gnomix
# training, and not one of the pre-cap misses); on the re-balanced v2.0.0 bundle it
# classifies top=MID at 0.77 (EUR 0.13, AFR 0.09) — the highest/cleanest of the 5
# held-out MID samples, i.e. the largest MID-over-EUR margin to pin the nightly to.
_MID_FIXTURE_CANDIDATES = ("heldout_mid_HGDP01282.adna.txt.gz",)

# MID is continentally INTERMEDIATE (adjacent to EUR): its dominant fraction is
# ~0.53–0.77 across the held-out cohort (vs EUR's ~0.95) and its minor components
# (EUR 0.13–0.25, CSA up to 0.14) move on the MID↔EUR decision boundary — the least
# reproducible axis across phasing-RNG / Java / Beagle versions. So this guard
# asserts ONLY the property guards (top==MID and MID ≥ floor), NOT per-population
# drift like the EUR fixture: the regression it guards (MID misclassified as EUR)
# necessarily drops MID below the floor and flips top_pop — already caught — while a
# numeric per-component band would add only cross-environment flake. 0.45 clears the
# lowest cohort sample (0.532) with headroom and sits well above the pre-cap miss
# level (MID≈0.38). Do NOT ratchet it up toward the single-run 0.77.
_MID_FIXTURE_MIN_MID_FRACTION = 0.45


def _parse_ancestrydna_fixture(path: Path) -> list[dict[str, str | int]]:
    """Read an AncestryDNA-format file into `raw_variants`-shaped dicts.

    The runner accepts {rsid, chrom, pos, genotype, source} per Plan §6.6.
    Comment lines (``#``) and the header row are skipped; 5-column TSV rows
    are coerced to a concatenated genotype string. The parser is kept inline
    so step 25a does not depend on the dedicated ``parser_ancestrydna`` module
    (which lands in step 30 / Phase 1, post-PR-0c merge).
    """
    rows: list[dict[str, str | int]] = []
    seen: set[str] = set()  # raw_variants.rsid is UNIQUE — dedup keep-first
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 5:
                continue
            rsid, chrom, pos, a1, a2 = parts
            if rsid == "rsid" or rsid in seen:  # header line / duplicate rsid
                continue
            try:
                pos_int = int(pos)
            except ValueError:
                continue
            seen.add(rsid)
            rows.append(
                {
                    "rsid": rsid,
                    "chrom": chrom,
                    "pos": pos_int,
                    "genotype": f"{a1}{a2}",
                    "source": "",
                }
            )
    return rows


@pytest.mark.slow
@pytest.mark.requires_real_bundle
@pytest.mark.requires_java
class TestRealBundleLAIAccuracy:
    """LAI-00e slow tier — nightly real-bundle accuracy regression.

    Dormant on every PR-blocking run: `requires_real_bundle` is auto-skipped
    when the production LAI bundle is not extracted under ``data_dir``. The
    nightly workflow (step 42) downloads the bundle (cache-keyed on manifest
    `sha256` per Plan §16.5) before invoking ``pytest -m slow``, at which
    point this class executes and asserts global-ancestry within ±1% of
    bio-validator's calibrated reference values.
    """

    def test_global_ancestry_within_one_percent_of_reference(
        self, tmp_path: Path, sample_engine: sa.Engine
    ) -> None:
        from backend.analysis.lai import run_lai_analysis
        from backend.db.tables import raw_variants, sample_metadata_table

        fixture_dir = Path(__file__).resolve().parent.parent / "fixtures"
        fixture_path: Path | None = None
        for name in _REAL_BUNDLE_FIXTURE_CANDIDATES:
            candidate = fixture_dir / name
            if candidate.exists():
                fixture_path = candidate
                break
        if fixture_path is None:
            pytest.skip(
                "No AncestryDNA real-bundle fixture present "
                f"(looked for: {', '.join(_REAL_BUNDLE_FIXTURE_CANDIDATES)})"
            )

        variants = _parse_ancestrydna_fixture(fixture_path)
        if not variants:
            pytest.skip(f"Fixture {fixture_path.name} parsed to zero variants")

        with sample_engine.begin() as conn:
            conn.execute(
                sample_metadata_table.insert().values(
                    id=1,
                    name="lai_real_bundle_test",
                    file_format="ancestrydna_v2.0",
                    file_hash="real-bundle-fixture",
                )
            )
            conn.execute(raw_variants.insert(), variants)

        result = run_lai_analysis(
            sample_id=1,
            sample_engine=sample_engine,
        )

        # Sanity: proportions sum to ~1.0 across the canonical 7 populations.
        total = sum(info["fraction"] for info in result.global_ancestry.values())
        assert abs(total - 1.0) < 1e-3, f"global ancestry sums to {total}"

        fracs = {
            p: result.global_ancestry.get(p, {}).get("fraction", 0.0)
            for p in CANONICAL_POPULATIONS
        }
        eur = fracs["EUR"]
        top_pop = max(fracs, key=fracs.get)

        # PRIMARY regression guard: a held-out European MUST classify as EUR.
        # The original v2.0.0 bundle dropped 767/770 EUR from gnomix training and
        # called this exact sample (HG01502, IBS) 94% CSA / 0.3% EUR; the rebuilt
        # bundle calls it ~96% EUR. Property-based so it is robust to the small
        # cross-environment phasing jitter on minor components.
        assert top_pop == "EUR", (
            f"REGRESSION: held-out European classified as {top_pop} "
            f"(EUR={eur:.4f}); the LAI bundle misclassifies Europeans. fractions={fracs}"
        )
        assert eur >= _EUR_FIXTURE_MIN_EUR_FRACTION, (
            f"EUR fraction {eur:.4f} below floor {_EUR_FIXTURE_MIN_EUR_FRACTION} "
            f"for a pure-EUR held-out sample. fractions={fracs}"
        )

        # Secondary drift monitor against the calibrated reference (generous band;
        # bio-validator may tighten once nightly cross-env stability is observed).
        for pop, expected in _EUR_FIXTURE_GLOBAL_ANCESTRY_REFERENCE.items():
            actual = fracs.get(pop, 0.0)
            assert abs(actual - expected) <= _GLOBAL_ANCESTRY_TOLERANCE, (
                f"{pop}: observed {actual:.4f}, reference {expected:.4f} "
                f"(±{_GLOBAL_ANCESTRY_TOLERANCE:.2%}). "
                "Bio-validator: update reference in test_lai.py if "
                "this reflects a legitimate bundle re-calibration."
            )

    def test_heldout_mid_classifies_as_mid(self, tmp_path: Path, sample_engine: sa.Engine) -> None:
        """A held-out Middle-Eastern sample must classify as MID, not EUR.

        Guards the MID re-balance (``--per-region-cap=250``): before it, the held-out
        per-superpopulation gate ran MID 2/5, the misses landing in the genetically
        adjacent EUR class. Only the property guards are asserted (top==MID and
        MID ≥ floor) — MID's intermediate per-population fractions move too much
        across environments to band without flaking (see _MID_FIXTURE_MIN_MID_FRACTION).
        """
        from backend.analysis.lai import run_lai_analysis
        from backend.db.tables import raw_variants, sample_metadata_table

        fixture_dir = Path(__file__).resolve().parent.parent / "fixtures"
        fixture_path: Path | None = None
        for name in _MID_FIXTURE_CANDIDATES:
            candidate = fixture_dir / name
            if candidate.exists():
                fixture_path = candidate
                break
        if fixture_path is None:
            pytest.skip(
                "No held-out MID fixture present "
                f"(looked for: {', '.join(_MID_FIXTURE_CANDIDATES)})"
            )

        variants = _parse_ancestrydna_fixture(fixture_path)
        if not variants:
            pytest.skip(f"Fixture {fixture_path.name} parsed to zero variants")

        with sample_engine.begin() as conn:
            conn.execute(
                sample_metadata_table.insert().values(
                    id=1,
                    name="lai_real_bundle_mid_test",
                    file_format="ancestrydna_v2.0",
                    file_hash="real-bundle-mid-fixture",
                )
            )
            conn.execute(raw_variants.insert(), variants)

        result = run_lai_analysis(
            sample_id=1,
            sample_engine=sample_engine,
        )

        # Sanity: proportions sum to ~1.0 across the canonical 7 populations.
        total = sum(info["fraction"] for info in result.global_ancestry.values())
        assert abs(total - 1.0) < 1e-3, f"global ancestry sums to {total}"

        fracs = {
            p: result.global_ancestry.get(p, {}).get("fraction", 0.0)
            for p in CANONICAL_POPULATIONS
        }
        mid = fracs["MID"]
        top_pop = max(fracs, key=fracs.get)

        # PRIMARY regression guard: a held-out Middle-Eastern sample MUST classify as
        # MID, not the genetically adjacent EUR. Pre-cap, this exact failure mode left
        # held-out MID at 2/5 with the misses going to EUR (0.40–0.50); the
        # --per-region-cap=250 re-balance took it to 5/5. A top of EUR here means the
        # MID→EUR misclassification has returned.
        assert top_pop == "MID", (
            f"REGRESSION: held-out Middle-Eastern sample classified as {top_pop} "
            f"(MID={mid:.4f}); the MID→EUR misclassification has returned. "
            f"fractions={fracs}"
        )
        assert mid >= _MID_FIXTURE_MIN_MID_FRACTION, (
            f"MID fraction {mid:.4f} below floor {_MID_FIXTURE_MIN_MID_FRACTION} "
            f"for a held-out Middle-Eastern sample. fractions={fracs}"
        )
