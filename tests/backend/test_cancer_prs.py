"""Tests for cancer PRS integration (P3-15).

Covers:
  - Weight set loading from JSON (4 cancer types)
  - PRS computation for breast, prostate, colorectal, melanoma
  - Bootstrap CI generation
  - Ancestry mismatch propagation
  - Findings storage with module='cancer', category='prs'
  - Insufficient coverage handling
  - CancerPRSResult aggregation properties
  - API endpoints for cancer PRS
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.cancer_prs import (
    CANCER_PRS_TRAITS,
    CancerPRSResult,
    load_cancer_prs_weights,
    store_cancer_prs_findings,
)
from backend.analysis.cancer_prs import (
    run_cancer_prs as _run_cancer_prs,
)
from backend.analysis.prs import PRSResult, PRSWeightSet
from backend.db.tables import annotated_variants, findings

# ── Fixtures ──────────────────────────────────────────────────────────────

WEIGHTS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "cancer_prs_weights.json"
)


@pytest.fixture()
def cancer_weight_sets() -> list[PRSWeightSet]:
    """Load cancer PRS weight sets from the real JSON file."""
    return load_cancer_prs_weights(WEIGHTS_PATH)


def run_cancer_prs(*args, **kwargs) -> CancerPRSResult:
    """Test helper: keep legacy all-trait assertions in an explicit XY context."""
    kwargs.setdefault("inferred_sex", "XY")
    return _run_cancer_prs(*args, **kwargs)


@pytest.fixture()
def sample_with_prs_snps(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine with annotated variants matching cancer PRS SNPs.

    Includes SNPs from all four cancer PRS weight sets so coverage
    is sufficient for testing.
    """
    # Load real weight sets to get all rsids
    weight_sets = load_cancer_prs_weights(WEIGHTS_PATH)
    all_rsids: set[str] = set()
    for ws in weight_sets:
        all_rsids.update(ws.rsid_set())

    # Create variants for all PRS SNPs with deterministic genotypes
    variants = []
    for i, rsid in enumerate(sorted(all_rsids)):
        # Alternate genotypes: effect/effect, effect/ref, ref/ref
        alleles = ["A", "C", "G", "T"]
        a1 = alleles[i % 4]
        a2 = alleles[(i + 1) % 4]
        variants.append(
            {
                "rsid": rsid,
                "chrom": str((i % 22) + 1),
                "pos": 100000 + i * 1000,
                "genotype": f"{a1}{a2}",
                "annotation_coverage": 0,
            }
        )

    with sample_engine.begin() as conn:
        conn.execute(sa.insert(annotated_variants), variants)
    return sample_engine


@pytest.fixture()
def sample_partial_coverage(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine with only a few PRS SNPs — below 50% for most traits."""
    variants = [
        {
            "rsid": "rs2981582",
            "chrom": "10",
            "pos": 123456,
            "genotype": "GG",
            "annotation_coverage": 0,
        },
        {
            "rsid": "rs1447295",
            "chrom": "8",
            "pos": 128500000,
            "genotype": "AA",
            "annotation_coverage": 0,
        },
    ]
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(annotated_variants), variants)
    return sample_engine


# ── Weight set loading tests ──────────────────────────────────────────────


class TestLoadCancerPRSWeights:
    """Test loading cancer PRS weight sets from JSON."""

    def test_loads_four_weight_sets(self, cancer_weight_sets: list[PRSWeightSet]) -> None:
        assert len(cancer_weight_sets) == 4

    def test_all_traits_present(self, cancer_weight_sets: list[PRSWeightSet]) -> None:
        traits = {ws.trait for ws in cancer_weight_sets}
        assert traits == CANCER_PRS_TRAITS

    def test_breast_cancer_weight_set(self, cancer_weight_sets: list[PRSWeightSet]) -> None:
        breast = [ws for ws in cancer_weight_sets if ws.trait == "breast_cancer"][0]
        assert breast.name == "Breast cancer (BCAC)"
        assert breast.source_ancestry == "EUR"
        assert breast.source_pmid == "30554720"
        assert breast.sample_size == 228951
        assert breast.snp_count > 0
        assert breast.module == "cancer"

    def test_prostate_cancer_weight_set(self, cancer_weight_sets: list[PRSWeightSet]) -> None:
        prostate = [ws for ws in cancer_weight_sets if ws.trait == "prostate_cancer"][0]
        assert prostate.name == "Prostate cancer (PRACTICAL)"
        assert prostate.source_pmid == "29892016"
        assert prostate.snp_count > 0

    def test_colorectal_cancer_weight_set(self, cancer_weight_sets: list[PRSWeightSet]) -> None:
        crc = [ws for ws in cancer_weight_sets if ws.trait == "colorectal_cancer"][0]
        assert crc.name == "Colorectal cancer (CRC)"
        assert crc.source_pmid == "30510241"
        assert crc.snp_count > 0

    def test_melanoma_weight_set(self, cancer_weight_sets: list[PRSWeightSet]) -> None:
        mel = [ws for ws in cancer_weight_sets if ws.trait == "melanoma"][0]
        assert mel.name == "Melanoma (GenoMEL)"
        assert mel.source_pmid == "32341527"
        assert mel.snp_count > 0

    def test_all_module_is_cancer(self, cancer_weight_sets: list[PRSWeightSet]) -> None:
        for ws in cancer_weight_sets:
            assert ws.module == "cancer"

    def test_weights_have_valid_structure(self, cancer_weight_sets: list[PRSWeightSet]) -> None:
        for ws in cancer_weight_sets:
            for w in ws.weights:
                assert w.rsid.startswith("rs")
                assert w.effect_allele in ("A", "C", "G", "T")
                assert isinstance(w.weight, float)

    def test_bundled_sets_are_uncalibrated(self, cancer_weight_sets: list[PRSWeightSet]) -> None:
        """Shipped cancer weight sets carry only placeholder reference params, so
        they must load as uncalibrated and the engine withholds the percentile
        (issue #7)."""
        for ws in cancer_weight_sets:
            assert ws.calibrated is False

    def test_file_not_found_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_cancer_prs_weights(Path("/nonexistent/weights.json"))


# ── Cancer PRS computation tests ─────────────────────────────────────────


class TestRunCancerPRS:
    """Test running cancer PRS for all four traits."""

    def test_computes_all_four_traits(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        assert len(result.results) == 4
        traits = {r.trait for r in result.results}
        assert traits == CANCER_PRS_TRAITS

    def test_prostate_prs_allowed_for_xy_context(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            inferred_sex="XY",
            n_bootstrap=100,
            rng_seed=42,
        )

        assert "prostate_cancer" in result.trait_names
        assert set(result.trait_names) == CANCER_PRS_TRAITS

    @pytest.mark.parametrize("inferred_sex", ["XX", "unknown", "manual_review"])
    def test_prostate_prs_skipped_without_xy_context(
        self,
        cancer_weight_sets: list[PRSWeightSet],
        sample_with_prs_snps: sa.Engine,
        inferred_sex: str,
    ) -> None:
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            inferred_sex=inferred_sex,
            n_bootstrap=100,
            rng_seed=42,
        )

        assert "prostate_cancer" not in result.trait_names
        assert set(result.trait_names) == CANCER_PRS_TRAITS - {"prostate_cancer"}

    def test_uncalibrated_sets_withhold_percentile(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        """The bundled sets are uncalibrated, so percentile / z-score / CI are
        withheld even when coverage is sufficient — no miscalibrated number is
        emitted (issue #7). raw_score is still computed."""
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        for r in result.results:
            assert r.calibrated is False
            assert r.percentile is None
            assert r.z_score is None
            assert r.has_bootstrap_ci is False
            assert r.raw_score is not None

    def test_calibrated_set_still_emits_percentile(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        """Guardrail is conditional: a weight set declaring a validated reference
        distribution (calibrated=True) still produces a percentile + bootstrap CI."""
        ws = replace(cancer_weight_sets[0], calibrated=True, reference_mean=0.5, reference_std=0.5)
        result = run_cancer_prs(
            [ws],
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        r = result.results[0]
        if r.is_sufficient:
            assert r.calibrated is True
            assert r.percentile is not None
            assert 0 <= r.percentile <= 100
            assert r.has_bootstrap_ci
            assert r.bootstrap_ci_lower <= r.bootstrap_ci_upper

    def test_all_evidence_level_is_1(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        """PRS components = ★☆☆☆ (evidence level 1)."""
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        for r in result.results:
            assert r.evidence_level == 1

    def test_ancestry_mismatch_propagated(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            inferred_ancestry="AFR",
            n_bootstrap=100,
            rng_seed=42,
        )
        for r in result.results:
            assert r.ancestry_mismatch is True
            assert r.ancestry_warning_text is not None
            assert "AFR" in r.ancestry_warning_text

    def test_no_mismatch_when_matching(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        for r in result.results:
            assert r.ancestry_mismatch is False

    def test_partial_coverage_mostly_insufficient(
        self, cancer_weight_sets: list[PRSWeightSet], sample_partial_coverage: sa.Engine
    ) -> None:
        """Only 2 SNPs present — all 4 traits should be insufficient (<50% coverage)."""
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_partial_coverage,
            n_bootstrap=100,
            rng_seed=42,
        )
        # 2 SNPs out of 15-25 per trait is well below 50%
        assert result.sufficient_count == 0
        assert len(result.insufficient_traits) == 4
        for r in result.results:
            assert r.is_sufficient is False

    def test_empty_sample_all_insufficient(
        self, cancer_weight_sets: list[PRSWeightSet], sample_engine: sa.Engine
    ) -> None:
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_engine,
            n_bootstrap=100,
            rng_seed=42,
        )
        assert result.sufficient_count == 0
        assert len(result.insufficient_traits) == 4

    def test_reproducible_with_seed(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        r1 = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        r2 = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        for a, b in zip(r1.results, r2.results):
            assert a.percentile == b.percentile
            assert a.bootstrap_ci_lower == b.bootstrap_ci_lower
            assert a.bootstrap_ci_upper == b.bootstrap_ci_upper


# ── CancerPRSResult dataclass tests ──────────────────────────────────────


class TestCancerPRSResult:
    """Test CancerPRSResult aggregation properties."""

    def test_sufficient_count(self) -> None:
        result = CancerPRSResult(
            results=[
                PRSResult(
                    weight_set_name="A",
                    trait="a",
                    module="cancer",
                    source_ancestry="EUR",
                    source_study="Test",
                    source_pmid="1",
                    sample_size=1000,
                    raw_score=0.5,
                    coverage_fraction=0.8,
                ),
                PRSResult(
                    weight_set_name="B",
                    trait="b",
                    module="cancer",
                    source_ancestry="EUR",
                    source_study="Test",
                    source_pmid="2",
                    sample_size=1000,
                    raw_score=0.3,
                    coverage_fraction=0.3,
                ),
            ]
        )
        assert result.sufficient_count == 1

    def test_insufficient_traits(self) -> None:
        result = CancerPRSResult(
            results=[
                PRSResult(
                    weight_set_name="A",
                    trait="breast_cancer",
                    module="cancer",
                    source_ancestry="EUR",
                    source_study="Test",
                    source_pmid="1",
                    sample_size=1000,
                    raw_score=0.5,
                    coverage_fraction=0.3,
                ),
            ]
        )
        assert result.insufficient_traits == ["breast_cancer"]

    def test_trait_names(self) -> None:
        result = CancerPRSResult(
            results=[
                PRSResult(
                    weight_set_name="A",
                    trait="breast_cancer",
                    module="cancer",
                    source_ancestry="EUR",
                    source_study="Test",
                    source_pmid="1",
                    sample_size=1000,
                    raw_score=0.5,
                ),
                PRSResult(
                    weight_set_name="B",
                    trait="melanoma",
                    module="cancer",
                    source_ancestry="EUR",
                    source_study="Test",
                    source_pmid="2",
                    sample_size=1000,
                    raw_score=0.3,
                ),
            ]
        )
        assert result.trait_names == ["breast_cancer", "melanoma"]


# ── Findings storage tests ───────────────────────────────────────────────


class TestStoreCancerPRSFindings:
    """Test cancer PRS findings storage."""

    def test_stores_sufficient_results(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        count = store_cancer_prs_findings(prs_result, sample_with_prs_snps)
        assert count == prs_result.sufficient_count
        assert count > 0

    def test_findings_have_prs_category(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(prs_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "cancer",
                    findings.c.category == "prs",
                )
            ).fetchall()
        assert len(rows) > 0
        for row in rows:
            assert row.category == "prs"
            assert row.evidence_level == 1

    def test_finding_text_has_research_use_only(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(prs_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            rows = conn.execute(sa.select(findings).where(findings.c.category == "prs")).fetchall()
        for row in rows:
            assert "Research Use Only" in row.finding_text

    def test_uncalibrated_finding_text_and_percentile(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        """Stored uncalibrated findings report no percentile, both in the column
        and the human-readable text (issue #7)."""
        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(prs_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            rows = conn.execute(sa.select(findings).where(findings.c.category == "prs")).fetchall()
        assert rows  # sufficient-coverage findings are still stored
        for row in rows:
            assert row.prs_percentile is None
            assert "percentile" in row.finding_text.lower()
            assert "uncalibrated" in row.finding_text.lower()
            assert json.loads(row.detail_json)["calibrated"] is False

    def test_detail_json_has_trait(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(prs_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            rows = conn.execute(sa.select(findings).where(findings.c.category == "prs")).fetchall()
        for row in rows:
            detail = json.loads(row.detail_json)
            assert "trait" in detail
            assert detail["trait"] in CANCER_PRS_TRAITS

    def test_non_xy_rerun_clears_prostate_prs(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        xy_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            inferred_sex="XY",
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(xy_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            initial_rows = conn.execute(
                sa.select(findings.c.detail_json).where(
                    findings.c.module == "cancer",
                    findings.c.category == "prs",
                )
            ).fetchall()

        initial_traits = {json.loads(row.detail_json)["trait"] for row in initial_rows}
        assert "prostate_cancer" in initial_traits

        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            inferred_sex="XX",
            n_bootstrap=100,
            rng_seed=42,
        )
        count = store_cancer_prs_findings(prs_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            rows = conn.execute(
                sa.select(findings.c.detail_json).where(
                    findings.c.module == "cancer",
                    findings.c.category == "prs",
                )
            ).fetchall()

        stored_traits = {json.loads(row.detail_json)["trait"] for row in rows}
        expected_traits = {r.trait for r in prs_result.results if r.is_sufficient}
        assert expected_traits
        assert count == prs_result.sufficient_count
        assert "prostate_cancer" not in stored_traits
        assert stored_traits == expected_traits

    def test_detail_json_has_bootstrap_ci(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(prs_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            rows = conn.execute(sa.select(findings).where(findings.c.category == "prs")).fetchall()
        for row in rows:
            detail = json.loads(row.detail_json)
            assert "bootstrap_ci_lower" in detail
            assert "bootstrap_ci_upper" in detail
            assert detail["research_use_only"] is True

    def test_does_not_store_insufficient(
        self, cancer_weight_sets: list[PRSWeightSet], sample_engine: sa.Engine
    ) -> None:
        """Results with < 50% coverage should not be stored."""
        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_engine,
            n_bootstrap=100,
            rng_seed=42,
        )
        count = store_cancer_prs_findings(prs_result, sample_engine)
        assert count == 0

    def test_does_not_clear_monogenic_findings(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        """PRS storage should not affect monogenic findings."""
        with sample_with_prs_snps.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "cancer",
                        "category": "monogenic_variant",
                        "evidence_level": 4,
                        "finding_text": "BRCA1 rs80357906 — Pathogenic",
                    }
                ],
            )

        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(prs_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            monogenic = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(
                    findings.c.module == "cancer",
                    findings.c.category == "monogenic_variant",
                )
            ).scalar()
        assert monogenic == 1

    def test_empty_results_clear_stale_prs_finding(self, sample_engine: sa.Engine) -> None:
        """When the score DB is unavailable, run_cancer_prs yields empty results;
        store_cancer_prs_findings must then clear a stale cancer/prs finding rather
        than surface a previously computed percentile with broken provenance (#245)."""
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "cancer",
                        "category": "prs",
                        "evidence_level": 2,
                        "finding_text": "Stale breast cancer PRS: 90th percentile",
                    }
                ],
            )
        store_cancer_prs_findings(CancerPRSResult(results=[]), sample_engine)
        with sample_engine.connect() as conn:
            stale = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "cancer", findings.c.category == "prs")
            ).scalar()
        assert stale == 0

    def test_clears_previous_prs_on_rerun(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(prs_result, sample_with_prs_snps)
        first_count = prs_result.sufficient_count

        # Run again
        store_cancer_prs_findings(prs_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(
                    findings.c.module == "cancer",
                    findings.c.category == "prs",
                )
            ).scalar()
        assert count == first_count  # Not doubled
