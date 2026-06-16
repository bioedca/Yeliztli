"""Tests for the generic PRS calculator engine (P3-14).

Covers:
  - T3-13: PRS calculator produces correct score from known weights and genotypes.
  - T3-14: PRS percentile calculation with bootstrap CI is correct against
    reference distribution.
  - Dosage counting (0/1/2 copies of effect allele).
  - z-score and percentile conversion.
  - Bootstrap CI bounds are reasonable.
  - Ancestry mismatch warning fires / does not fire.
  - Findings storage (module tag, category='prs', evidence_level=1).
  - Insufficient coverage handling.
  - Edge cases: empty weight set, no genotype data, zero-std reference.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend.analysis.prs import (
    PRSResult,
    PRSSNPContribution,
    PRSSNPWeight,
    PRSWeightSet,
    _count_effect_allele,
    check_ancestry_mismatch,
    compute_prs,
    compute_prs_bootstrap_ci,
    compute_prs_percentile,
    run_prs,
    store_prs_findings,
)
from backend.db.tables import annotated_variants, findings

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def weight_set() -> PRSWeightSet:
    """A small test weight set with known SNPs and weights."""
    return PRSWeightSet(
        name="Test PRS (Breast cancer)",
        trait="breast_cancer",
        module="cancer",
        source_ancestry="EUR",
        source_study="Test et al. 2024",
        source_pmid="12345678",
        sample_size=100000,
        weights=[
            PRSSNPWeight(rsid="rs1001", effect_allele="A", weight=0.10),
            PRSSNPWeight(rsid="rs1002", effect_allele="G", weight=0.20),
            PRSSNPWeight(rsid="rs1003", effect_allele="T", weight=-0.05),
            PRSSNPWeight(rsid="rs1004", effect_allele="C", weight=0.15),
            PRSSNPWeight(rsid="rs1005", effect_allele="A", weight=0.08),
        ],
        reference_mean=0.30,
        reference_std=0.25,
    )


@pytest.fixture()
def sample_with_prs_variants(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine with annotated variants matching the test weight set."""
    variants = [
        # rs1001: AA (hom effect) → dosage 2
        {
            "rsid": "rs1001",
            "chrom": "1",
            "pos": 100000,
            "genotype": "AA",
            "annotation_coverage": 0,
        },
        # rs1002: AG (het) → dosage 1 for G
        {
            "rsid": "rs1002",
            "chrom": "2",
            "pos": 200000,
            "genotype": "AG",
            "annotation_coverage": 0,
        },
        # rs1003: TT (hom effect) → dosage 2 for T
        {
            "rsid": "rs1003",
            "chrom": "3",
            "pos": 300000,
            "genotype": "TT",
            "annotation_coverage": 0,
        },
        # rs1004: CG (het) → dosage 1 for C
        {
            "rsid": "rs1004",
            "chrom": "4",
            "pos": 400000,
            "genotype": "CG",
            "annotation_coverage": 0,
        },
        # rs1005: NOT PRESENT → missing SNP
    ]
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(annotated_variants), variants)
    return sample_engine


def _uncalibrated_continuous_weight_set() -> PRSWeightSet:
    return PRSWeightSet(
        name="Continuous calibration PRS",
        trait="calibration_test",
        module="cancer",
        source_ancestry="EUR",
        source_study="Test et al. 2026",
        source_pmid="99999999",
        sample_size=100000,
        weights=[
            PRSSNPWeight(rsid="rsCAL1", effect_allele="T", weight=1.0),
            PRSSNPWeight(rsid="rsCAL2", effect_allele="G", weight=-0.5),
        ],
        reference_mean=0.0,
        reference_std=1.0,
        calibrated=False,
    )


def _uncalibrated_continuous_position_weight_set() -> PRSWeightSet:
    return PRSWeightSet(
        name="Continuous calibration positional PRS",
        trait="calibration_position_test",
        module="cancer",
        source_ancestry="EUR",
        source_study="Test et al. 2026",
        source_pmid="99999999",
        sample_size=100000,
        weights=[
            PRSSNPWeight(rsid="", chrom="chr1", pos=100, effect_allele="T", weight=1.0),
            PRSSNPWeight(rsid="", chrom="2", pos=200, effect_allele="G", weight=-0.5),
        ],
        reference_mean=0.0,
        reference_std=1.0,
        calibrated=False,
    )


def _seed_continuous_calibration_inputs(
    sample_engine: sa.Engine,
    *,
    with_ancestry: bool,
) -> None:
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(annotated_variants),
            [
                {
                    "rsid": "rsCAL1",
                    "chrom": "1",
                    "pos": 100,
                    "ref": "C",
                    "alt": "T",
                    "genotype": "TT",
                    "gnomad_af_eur": 0.25,
                    "gnomad_af_afr": 0.75,
                    "gnomad_af_amr": 0.45,
                    "gnomad_af_eas": 0.10,
                    "gnomad_af_sas": 0.35,
                    "annotation_coverage": 4,
                },
                {
                    "rsid": "rsCAL2",
                    "chrom": "2",
                    "pos": 200,
                    "ref": "A",
                    "alt": "G",
                    "genotype": "AG",
                    "gnomad_af_eur": 0.50,
                    "gnomad_af_afr": 0.20,
                    "gnomad_af_amr": 0.40,
                    "gnomad_af_eas": 0.70,
                    "gnomad_af_sas": 0.55,
                    "annotation_coverage": 4,
                },
            ],
        )
        if with_ancestry:
            conn.execute(
                sa.insert(findings).values(
                    module="ancestry",
                    category="nnls_admixture",
                    finding_text="Inferred ancestry",
                    detail_json=json.dumps(
                        {
                            "top_population": "EUR",
                            "nnls_fractions": {"EUR": 0.75, "AFR": 0.25},
                        }
                    ),
                )
            )


# ── Dosage counting tests ────────────────────────────────────────────────


class TestCountEffectAllele:
    """Test _count_effect_allele dosage computation."""

    def test_homozygous_effect(self) -> None:
        assert _count_effect_allele("AA", "A") == 2

    def test_heterozygous(self) -> None:
        assert _count_effect_allele("AG", "A") == 1

    def test_homozygous_non_effect(self) -> None:
        assert _count_effect_allele("GG", "A") == 0

    def test_case_insensitive(self) -> None:
        assert _count_effect_allele("ag", "A") == 1
        assert _count_effect_allele("AG", "a") == 1

    def test_none_genotype(self) -> None:
        assert _count_effect_allele(None, "A") == 0

    def test_empty_genotype(self) -> None:
        assert _count_effect_allele("", "A") == 0

    def test_no_call_dashes(self) -> None:
        assert _count_effect_allele("--", "A") == 0

    def test_no_call_zeros(self) -> None:
        assert _count_effect_allele("00", "A") == 0

    def test_single_char_genotype(self) -> None:
        assert _count_effect_allele("A", "A") == 0


# ── Core PRS computation tests ──────────────────────────────────────────


class TestComputePRS:
    """Test PRS score computation from weight sets and genotypes."""

    def test_correct_raw_score(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        """T3-13: PRS calculator produces correct score from known weights."""
        result = compute_prs(weight_set, sample_with_prs_variants)

        # Expected: rs1001 (0.10*2=0.20) + rs1002 (0.20*1=0.20) +
        #           rs1003 (-0.05*2=-0.10) + rs1004 (0.15*1=0.15)
        # rs1005 is missing → 0
        expected = 0.20 + 0.20 + (-0.10) + 0.15
        assert result.raw_score == pytest.approx(expected, abs=1e-10)

    def test_snps_used_count(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        result = compute_prs(weight_set, sample_with_prs_variants)
        assert result.snps_used == 4  # rs1005 missing
        assert result.snps_total == 5

    def test_coverage_fraction(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        result = compute_prs(weight_set, sample_with_prs_variants)
        assert result.coverage_fraction == pytest.approx(0.8)

    def test_is_sufficient(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        result = compute_prs(weight_set, sample_with_prs_variants)
        assert result.is_sufficient is True  # 80% coverage > 50% threshold

    def test_metadata_propagated(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        result = compute_prs(weight_set, sample_with_prs_variants)
        assert result.weight_set_name == "Test PRS (Breast cancer)"
        assert result.trait == "breast_cancer"
        assert result.module == "cancer"
        assert result.source_ancestry == "EUR"
        assert result.source_pmid == "12345678"
        assert result.sample_size == 100000

    def test_evidence_level_is_1(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        """PRS components should be ★☆☆☆ (evidence level 1)."""
        result = compute_prs(weight_set, sample_with_prs_variants)
        assert result.evidence_level == 1

    def test_contributions_populated(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        result = compute_prs(weight_set, sample_with_prs_variants)
        assert len(result.contributions) == 5  # All weight set SNPs

        # Check specific contribution
        rs1001 = [c for c in result.contributions if c.rsid == "rs1001"][0]
        assert rs1001.dosage == 2
        assert rs1001.contribution == pytest.approx(0.20)
        assert rs1001.genotype == "AA"

    def test_missing_snp_contribution(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        result = compute_prs(weight_set, sample_with_prs_variants)
        rs1005 = [c for c in result.contributions if c.rsid == "rs1005"][0]
        assert rs1005.dosage == 0
        assert rs1005.contribution == 0.0
        assert rs1005.genotype is None

    def test_empty_sample_returns_zero(
        self, weight_set: PRSWeightSet, sample_engine: sa.Engine
    ) -> None:
        result = compute_prs(weight_set, sample_engine)
        assert result.raw_score == 0.0
        assert result.snps_used == 0
        assert result.coverage_fraction == 0.0
        assert result.is_sufficient is False


# ── Percentile & z-score tests ──────────────────────────────────────────


class TestPercentileComputation:
    """Test z-score and percentile calculation."""

    def test_z_score_calculation(self) -> None:
        """T3-14 (partial): Verify z-score against manual calculation."""
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.55,
        )
        result = compute_prs_percentile(result, reference_mean=0.30, reference_std=0.25)

        expected_z = (0.55 - 0.30) / 0.25  # = 1.0
        assert result.z_score == pytest.approx(expected_z, abs=0.001)

    def test_percentile_at_mean(self) -> None:
        """Score at the mean should give ~50th percentile."""
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.30,
        )
        result = compute_prs_percentile(result, reference_mean=0.30, reference_std=0.25)
        assert result.percentile == pytest.approx(50.0, abs=0.1)

    def test_percentile_one_sd_above(self) -> None:
        """Score 1 SD above mean → ~84.13th percentile."""
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.55,
        )
        result = compute_prs_percentile(result, reference_mean=0.30, reference_std=0.25)
        assert result.percentile == pytest.approx(84.13, abs=0.5)

    def test_percentile_one_sd_below(self) -> None:
        """Score 1 SD below mean → ~15.87th percentile."""
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.05,
        )
        result = compute_prs_percentile(result, reference_mean=0.30, reference_std=0.25)
        assert result.percentile == pytest.approx(15.87, abs=0.5)

    def test_zero_std_gives_50th(self) -> None:
        """Zero reference std should default to 50th percentile."""
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.55,
        )
        result = compute_prs_percentile(result, reference_mean=0.30, reference_std=0.0)
        assert result.percentile == 50.0
        assert result.z_score == 0.0


# ── Bootstrap CI tests ──────────────────────────────────────────────────


class TestBootstrapCI:
    """Test bootstrap confidence interval computation."""

    def test_bootstrap_ci_bounds(self) -> None:
        """T3-14: Bootstrap CI bounds should bracket the point estimate."""
        contributions = [
            PRSSNPContribution(
                rsid=f"rs{i}",
                effect_allele="A",
                weight=0.1,
                genotype="AA",
                dosage=2,
                contribution=0.2,
            )
            for i in range(20)
        ]
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=4.0,
            z_score=2.0,
            percentile=97.72,
            snps_used=20,
            snps_total=20,
            coverage_fraction=1.0,
            contributions=contributions,
        )
        result = compute_prs_bootstrap_ci(
            result,
            reference_mean=0.0,
            reference_std=2.0,
            n_iterations=1000,
            rng_seed=42,
        )

        assert result.has_bootstrap_ci
        assert result.bootstrap_ci_lower is not None
        assert result.bootstrap_ci_upper is not None
        assert result.bootstrap_ci_lower <= result.bootstrap_ci_upper
        assert result.bootstrap_iterations == 1000

    def test_bootstrap_ci_narrows_with_more_snps(self) -> None:
        """CI should be narrower when more SNPs contribute.

        Uses varying contributions so bootstrap resampling produces
        meaningful variance (identical contributions yield zero CI width).
        """
        # Small weight set with varied contributions
        small_weights = [0.8, 0.2, 0.6, 0.1, 0.3]
        small_contribs = [
            PRSSNPContribution(
                rsid=f"rs{i}",
                effect_allele="A",
                weight=w,
                genotype="AG",
                dosage=1,
                contribution=w,
            )
            for i, w in enumerate(small_weights)
        ]
        small_result = PRSResult(
            weight_set_name="Small",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=sum(small_weights),
            snps_used=5,
            snps_total=5,
            coverage_fraction=1.0,
            contributions=small_contribs,
        )
        small_result = compute_prs_bootstrap_ci(
            small_result,
            reference_mean=0.0,
            reference_std=2.0,
            n_iterations=1000,
            rng_seed=42,
        )

        # Large weight set with the same total score but 100 varied contributions
        large_weights = [(i % 5) * 0.01 + 0.01 for i in range(100)]
        total = sum(large_weights)
        large_contribs = [
            PRSSNPContribution(
                rsid=f"rs{i}",
                effect_allele="A",
                weight=w,
                genotype="AG",
                dosage=1,
                contribution=w,
            )
            for i, w in enumerate(large_weights)
        ]
        large_result = PRSResult(
            weight_set_name="Large",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=total,
            snps_used=100,
            snps_total=100,
            coverage_fraction=1.0,
            contributions=large_contribs,
        )
        large_result = compute_prs_bootstrap_ci(
            large_result,
            reference_mean=0.0,
            reference_std=2.0,
            n_iterations=1000,
            rng_seed=42,
        )

        small_width = small_result.bootstrap_ci_upper - small_result.bootstrap_ci_lower
        large_width = large_result.bootstrap_ci_upper - large_result.bootstrap_ci_lower
        assert small_width > 0  # Sanity check: small set has variance
        assert large_width < small_width

    def test_bootstrap_reproducible_with_seed(self) -> None:
        """Same seed should give identical CI bounds."""
        contributions = [
            PRSSNPContribution(
                rsid="rs1",
                effect_allele="A",
                weight=0.1,
                genotype="AG",
                dosage=1,
                contribution=0.1,
            ),
            PRSSNPContribution(
                rsid="rs2",
                effect_allele="G",
                weight=0.2,
                genotype="GG",
                dosage=2,
                contribution=0.4,
            ),
        ]
        result1 = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.5,
            snps_used=2,
            snps_total=2,
            coverage_fraction=1.0,
            contributions=list(contributions),
        )
        result2 = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.5,
            snps_used=2,
            snps_total=2,
            coverage_fraction=1.0,
            contributions=list(contributions),
        )
        result1 = compute_prs_bootstrap_ci(
            result1,
            reference_mean=0.0,
            reference_std=1.0,
            n_iterations=500,
            rng_seed=99,
        )
        result2 = compute_prs_bootstrap_ci(
            result2,
            reference_mean=0.0,
            reference_std=1.0,
            n_iterations=500,
            rng_seed=99,
        )
        assert result1.bootstrap_ci_lower == result2.bootstrap_ci_lower
        assert result1.bootstrap_ci_upper == result2.bootstrap_ci_upper

    def test_bootstrap_zero_iterations_returns_point_estimate(self) -> None:
        """A disabled bootstrap should not try to percentile an empty sample."""
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.5,
            percentile=72.0,
            contributions=[
                PRSSNPContribution(
                    rsid="rs1",
                    effect_allele="A",
                    weight=0.1,
                    genotype="AA",
                    dosage=2,
                    contribution=0.2,
                )
            ],
        )
        result = compute_prs_bootstrap_ci(
            result,
            reference_mean=0.0,
            reference_std=1.0,
            n_iterations=0,
        )
        assert result.bootstrap_ci_lower == 72.0
        assert result.bootstrap_ci_upper == 72.0
        assert result.bootstrap_iterations == 0

    def test_bootstrap_zero_std_returns_point_estimate(self) -> None:
        """Zero reference std should return point estimate as CI bounds."""
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.5,
            percentile=72.0,
            contributions=[],
        )
        result = compute_prs_bootstrap_ci(
            result,
            reference_mean=0.0,
            reference_std=0.0,
        )
        assert result.bootstrap_ci_lower == 72.0
        assert result.bootstrap_ci_upper == 72.0

    def test_bootstrap_no_contributions_returns_point_estimate(self) -> None:
        """No contributions should return point estimate as CI bounds."""
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.0,
            percentile=50.0,
            contributions=[
                PRSSNPContribution(
                    rsid="rs1",
                    effect_allele="A",
                    weight=0.1,
                    genotype=None,
                    dosage=0,
                    contribution=0.0,
                )
            ],
        )
        result = compute_prs_bootstrap_ci(
            result,
            reference_mean=0.0,
            reference_std=1.0,
        )
        assert result.bootstrap_ci_lower == 50.0
        assert result.bootstrap_ci_upper == 50.0


# ── Ancestry mismatch tests ────────────────────────────────────────────


class TestAncestryMismatch:
    """Test ancestry mismatch warning logic."""

    def test_mismatch_fires_when_different(self) -> None:
        """T3-15 prerequisite: mismatch warning fires when ancestry differs."""
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.5,
        )
        result = check_ancestry_mismatch(result, inferred_ancestry="EAS")
        assert result.ancestry_mismatch is True
        assert result.ancestry_warning_text is not None
        assert "EUR" in result.ancestry_warning_text
        assert "EAS" in result.ancestry_warning_text

    def test_no_mismatch_when_matching(self) -> None:
        """T3-16 prerequisite: no mismatch warning when ancestries match."""
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.5,
        )
        result = check_ancestry_mismatch(result, inferred_ancestry="EUR")
        assert result.ancestry_mismatch is False
        assert result.ancestry_warning_text is None

    def test_case_insensitive_match(self) -> None:
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.5,
        )
        result = check_ancestry_mismatch(result, inferred_ancestry="eur")
        assert result.ancestry_mismatch is False

    def test_none_ancestry_gives_informational_warning(self) -> None:
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.5,
        )
        result = check_ancestry_mismatch(result, inferred_ancestry=None)
        assert result.ancestry_mismatch is False
        assert result.ancestry_warning_text is not None
        assert "not been run" in result.ancestry_warning_text


# ── Full pipeline tests ─────────────────────────────────────────────────


class TestRunPRS:
    """Test the full PRS pipeline convenience function."""

    def test_full_pipeline(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        """T3-13 + T3-14: Full pipeline produces correct score with CI."""
        result = run_prs(
            weight_set,
            sample_with_prs_variants,
            inferred_ancestry="EUR",
            n_bootstrap=500,
            rng_seed=42,
        )

        # Raw score: 0.20 + 0.20 + (-0.10) + 0.15 = 0.45
        assert result.raw_score == pytest.approx(0.45, abs=1e-10)

        # z-score: (0.45 - 0.30) / 0.25 = 0.6
        assert result.z_score == pytest.approx(0.6, abs=0.01)

        # Percentile should be around 72.57 (z=0.6 → Φ(0.6)≈0.7257)
        assert result.percentile is not None
        assert 70 < result.percentile < 76

        # Bootstrap CI should exist
        assert result.has_bootstrap_ci
        assert result.bootstrap_ci_lower < result.bootstrap_ci_upper

        # No ancestry mismatch
        assert result.ancestry_mismatch is False
        assert result.ancestry_warning_text is None

    def test_full_pipeline_with_zero_bootstrap_keeps_percentile(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        result = run_prs(
            weight_set,
            sample_with_prs_variants,
            inferred_ancestry="EUR",
            n_bootstrap=0,
        )

        assert result.percentile is not None
        assert result.bootstrap_ci_lower == result.percentile
        assert result.bootstrap_ci_upper == result.percentile
        assert result.bootstrap_iterations == 0

    def test_full_pipeline_with_mismatch(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        result = run_prs(
            weight_set,
            sample_with_prs_variants,
            inferred_ancestry="AFR",
            n_bootstrap=100,
            rng_seed=42,
        )
        assert result.ancestry_mismatch is True
        assert "AFR" in result.ancestry_warning_text

    def test_uncalibrated_weight_set_uses_continuous_reference_distribution(
        self, sample_engine: sa.Engine
    ) -> None:
        _seed_continuous_calibration_inputs(sample_engine, with_ancestry=True)
        result = run_prs(
            _uncalibrated_continuous_weight_set(),
            sample_engine,
            inferred_ancestry="EUR",
            n_bootstrap=25,
            rng_seed=42,
        )

        assert result.raw_score == pytest.approx(1.5)
        assert result.calibrated is True
        assert result.calibration_method == "ancestry_continuous"
        assert result.calibration_variants_used == 2
        assert result.calibration_variants_total == 2
        assert result.calibration_ancestry_fractions == {"EUR": 0.75, "AFR": 0.25}
        assert result.calibration_reference_mean is not None
        assert result.calibration_reference_std is not None
        assert result.z_score is not None
        assert result.percentile is not None
        assert result.has_bootstrap_ci

        store_prs_findings([result], sample_engine, module="cancer")
        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.category == "prs")).fetchone()
        detail = json.loads(row.detail_json)
        assert detail["calibrated"] is True
        assert detail["calibration_method"] == "ancestry_continuous"
        assert detail["calibration_variants_used"] == 2
        assert detail["calibration_variants_total"] == 2
        assert detail["calibration_ancestry_fractions"] == {"EUR": 0.75, "AFR": 0.25}
        pmids = json.loads(row.pmid_citations)
        assert {"99999999", "37198491", "38374346"} <= set(pmids)

    def test_position_only_weight_set_uses_continuous_reference_distribution(
        self, sample_engine: sa.Engine
    ) -> None:
        _seed_continuous_calibration_inputs(sample_engine, with_ancestry=True)
        result = run_prs(
            _uncalibrated_continuous_position_weight_set(),
            sample_engine,
            inferred_ancestry="EUR",
            n_bootstrap=25,
            rng_seed=42,
        )

        assert result.raw_score == pytest.approx(1.5)
        assert result.snps_used == 2
        assert result.calibrated is True
        assert result.calibration_method == "ancestry_continuous"
        assert result.calibration_variants_used == 2
        assert result.calibration_variants_total == 2
        assert result.z_score is not None
        assert result.percentile is not None
        assert result.has_bootstrap_ci

    def test_uncalibrated_weight_set_without_ancestry_still_withholds_percentile(
        self, sample_engine: sa.Engine
    ) -> None:
        _seed_continuous_calibration_inputs(sample_engine, with_ancestry=False)
        result = run_prs(
            _uncalibrated_continuous_weight_set(),
            sample_engine,
            inferred_ancestry=None,
            n_bootstrap=25,
            rng_seed=42,
        )

        assert result.calibrated is False
        assert result.calibration_method is None
        assert result.z_score is None
        assert result.percentile is None
        assert result.bootstrap_iterations == 0


# ── Findings storage tests ──────────────────────────────────────────────


class TestStorePRSFindings:
    """Test PRS findings storage in the sample database."""

    def test_stores_correct_count(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        result = run_prs(
            weight_set,
            sample_with_prs_variants,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        count = store_prs_findings([result], sample_with_prs_variants, module="cancer")
        assert count == 1

    def test_findings_have_prs_category(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        result = run_prs(
            weight_set,
            sample_with_prs_variants,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        store_prs_findings([result], sample_with_prs_variants, module="cancer")

        with sample_with_prs_variants.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "cancer",
                    findings.c.category == "prs",
                )
            ).fetchall()
        assert len(rows) == 1
        assert rows[0].category == "prs"
        assert rows[0].evidence_level == 1  # ★☆☆☆

    def test_finding_text_contains_research_use_only(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        result = run_prs(
            weight_set,
            sample_with_prs_variants,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        store_prs_findings([result], sample_with_prs_variants, module="cancer")

        with sample_with_prs_variants.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.category == "prs")).fetchone()
        assert "Research Use Only" in row.finding_text

    def test_finding_text_contains_percentile(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        result = run_prs(
            weight_set,
            sample_with_prs_variants,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        store_prs_findings([result], sample_with_prs_variants, module="cancer")

        with sample_with_prs_variants.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.category == "prs")).fetchone()
        assert "percentile" in row.finding_text

    def test_detail_json_has_ancestry_source_tag(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        """Every PRS finding must include an ancestry source tag."""
        result = run_prs(
            weight_set,
            sample_with_prs_variants,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        store_prs_findings([result], sample_with_prs_variants, module="cancer")

        with sample_with_prs_variants.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.category == "prs")).fetchone()
        detail = json.loads(row.detail_json)
        assert detail["source_ancestry"] == "EUR"
        assert detail["research_use_only"] is True

    def test_detail_json_has_bootstrap_ci(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        result = run_prs(
            weight_set,
            sample_with_prs_variants,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        store_prs_findings([result], sample_with_prs_variants, module="cancer")

        with sample_with_prs_variants.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.category == "prs")).fetchone()
        detail = json.loads(row.detail_json)
        assert "bootstrap_ci_lower" in detail
        assert "bootstrap_ci_upper" in detail
        assert detail["bootstrap_iterations"] == 100

    def test_detail_json_has_trait_architecture(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        """SW-A2: every PRS finding carries the static trait-architecture block."""
        result = run_prs(
            weight_set,
            sample_with_prs_variants,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        store_prs_findings([result], sample_with_prs_variants, module="cancer")

        with sample_with_prs_variants.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.category == "prs")).fetchone()
        arch = json.loads(row.detail_json)["architecture"]
        assert {"heritability", "portability", "calibration", "citation"} <= set(arch)
        assert "h²_twin > h²_SNP > h²_PRS" in arch["heritability"]
        assert "ding" in arch["portability"].lower()
        assert "calibration is not accuracy" in arch["calibration"].lower()

    def test_detail_json_has_return_framing(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        """SW-A1: every PRS finding carries the consolidated return-framing block."""
        result = run_prs(
            weight_set,
            sample_with_prs_variants,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        store_prs_findings([result], sample_with_prs_variants, module="cancer")

        with sample_with_prs_variants.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.category == "prs")).fetchone()
        rf = json.loads(row.detail_json)["return_framing"]
        assert rf["research_use_only"] is True
        assert rf["source_population"] == "EUR"
        assert "EUR" in rf["source_population_label"]
        assert "95% CI" in rf["ci_label"]

    def test_prs_score_and_percentile_stored(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        result = run_prs(
            weight_set,
            sample_with_prs_variants,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        store_prs_findings([result], sample_with_prs_variants, module="cancer")

        with sample_with_prs_variants.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.category == "prs")).fetchone()
        assert row.prs_score is not None
        assert row.prs_percentile is not None

    def test_insufficient_coverage_not_stored(
        self, weight_set: PRSWeightSet, sample_engine: sa.Engine
    ) -> None:
        """Results with < 50% coverage should not be stored as findings."""
        result = run_prs(
            weight_set,
            sample_engine,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        assert result.is_sufficient is False
        count = store_prs_findings([result], sample_engine, module="cancer")
        assert count == 0

    def test_insufficient_coverage_clears_stale_prs_finding(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        """A completed no-store PRS pass should clear stale PRS findings."""
        stale_result = run_prs(
            weight_set,
            sample_with_prs_variants,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        assert store_prs_findings([stale_result], sample_with_prs_variants, module="cancer") == 1

        current_result = PRSResult(
            weight_set_name="Current PRS",
            trait="breast_cancer",
            module="cancer",
            source_ancestry="EUR",
            source_study="Current",
            source_pmid="12345678",
            sample_size=100000,
            raw_score=0.0,
            snps_used=0,
            snps_total=5,
            coverage_fraction=0.0,
        )
        assert store_prs_findings([current_result], sample_with_prs_variants, module="cancer") == 0

        with sample_with_prs_variants.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "cancer", findings.c.category == "prs")
            ).scalar()
        assert count == 0

    def test_clears_previous_prs_findings(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        result = run_prs(
            weight_set,
            sample_with_prs_variants,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        store_prs_findings([result], sample_with_prs_variants, module="cancer")
        store_prs_findings([result], sample_with_prs_variants, module="cancer")

        with sample_with_prs_variants.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "cancer", findings.c.category == "prs")
            ).scalar()
        assert count == 1  # Not 2

    def test_does_not_clear_monogenic_findings(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        """PRS storage should only clear PRS findings, not monogenic."""
        # Insert a monogenic finding first
        with sample_with_prs_variants.begin() as conn:
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

        result = run_prs(
            weight_set,
            sample_with_prs_variants,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        store_prs_findings([result], sample_with_prs_variants, module="cancer")

        with sample_with_prs_variants.connect() as conn:
            monogenic = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(
                    findings.c.module == "cancer",
                    findings.c.category == "monogenic_variant",
                )
            ).scalar()
            prs_count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(
                    findings.c.module == "cancer",
                    findings.c.category == "prs",
                )
            ).scalar()
        assert monogenic == 1  # Preserved
        assert prs_count == 1

    def test_multiple_results_stored(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        """Multiple PRS results can be stored at once."""
        result1 = run_prs(
            weight_set,
            sample_with_prs_variants,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        # Create a second weight set with different trait
        weight_set2 = PRSWeightSet(
            name="Test PRS (Prostate cancer)",
            trait="prostate_cancer",
            module="cancer",
            source_ancestry="EUR",
            source_study="Test2",
            source_pmid="87654321",
            sample_size=50000,
            weights=weight_set.weights,
            reference_mean=0.30,
            reference_std=0.25,
        )
        result2 = run_prs(
            weight_set2,
            sample_with_prs_variants,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        count = store_prs_findings([result1, result2], sample_with_prs_variants, module="cancer")
        assert count == 2

    def test_empty_results_clear_stale_prs_finding(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        result = run_prs(
            weight_set,
            sample_with_prs_variants,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        assert store_prs_findings([result], sample_with_prs_variants, module="cancer") == 1

        count = store_prs_findings([], sample_with_prs_variants, module="cancer")
        assert count == 0

        with sample_with_prs_variants.connect() as conn:
            stale_count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "cancer", findings.c.category == "prs")
            ).scalar()
        assert stale_count == 0

    def test_pmid_stored_as_json(
        self, weight_set: PRSWeightSet, sample_with_prs_variants: sa.Engine
    ) -> None:
        result = run_prs(
            weight_set,
            sample_with_prs_variants,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        store_prs_findings([result], sample_with_prs_variants, module="cancer")

        with sample_with_prs_variants.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.category == "prs")).fetchone()
        pmids = json.loads(row.pmid_citations)
        assert "12345678" in pmids


# ── Strand harmonization tests (EXPANSION_STRATEGY.md §10 / PR-0) ─────────


def _harmonized_weight_set(weights: list[PRSSNPWeight]) -> PRSWeightSet:
    return PRSWeightSet(
        name="Harmonization test",
        trait="test",
        module="cancer",
        source_ancestry="EUR",
        source_study="Test",
        source_pmid="111",
        sample_size=1000,
        weights=weights,
        reference_mean=0.0,
        reference_std=1.0,
    )


class TestStrandHarmonization:
    """compute_prs resolves reverse strands, drops ambiguous palindromes, and
    discloses no-calls — only activated when the weight carries other_allele."""

    def test_reverse_strand_flip_scores_correctly(self, sample_engine: sa.Engine) -> None:
        """A minus-strand genotype that the old code scored 0 now scores 2."""
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rsFLIP",
                        "chrom": "1",
                        "pos": 1,
                        "genotype": "GG",  # reverse strand of effect C / other T
                        "gnomad_af_global": 0.20,
                        "annotation_coverage": 4,
                    }
                ],
            )
        ws = _harmonized_weight_set(
            [PRSSNPWeight(rsid="rsFLIP", effect_allele="C", other_allele="T", weight=0.5)]
        )
        result = compute_prs(ws, sample_engine)

        assert result.raw_score == pytest.approx(1.0)  # 0.5 * dosage 2
        assert result.snps_strand_flipped == 1
        c = result.contributions[0]
        assert c.dosage == 2
        assert c.match_status == "matched_flip"
        assert c.strand == "flip"

    def test_palindrome_het_near_half_scored_recoverable(self, sample_engine: sa.Engine) -> None:
        # #353: a palindromic HETEROZYGOTE is strand-invariant at ANY MAF, so near
        # 0.5 it now resolves to one effect copy and is scored (recoverable coverage)
        # rather than dropped. Only homozygotes remain strand-ambiguous near 0.5.
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rsPAL",
                        "chrom": "1",
                        "pos": 2,
                        "genotype": "AT",
                        "gnomad_af_global": 0.50,
                        "annotation_coverage": 4,
                    }
                ],
            )
        ws = _harmonized_weight_set(
            [PRSSNPWeight(rsid="rsPAL", effect_allele="A", other_allele="T", weight=0.9)]
        )
        result = compute_prs(ws, sample_engine)

        assert result.raw_score == pytest.approx(0.9)  # one effect copy * weight
        assert result.snps_ambiguous_dropped == 0
        assert result.snps_used == 1  # counted as covered
        assert result.contributions[0].match_status == "matched_ref"

    def test_palindrome_homozygote_away_from_half_dropped(self, sample_engine: sa.Engine) -> None:
        # #247: a palindromic HOMOZYGOTE is strand-ambiguous for a single sample
        # (a minus-strand "AA" would be the complement "TT"), so even away from MAF
        # 0.5 it is dropped rather than counted at + strand face value — otherwise a
        # minus-strand chip call inverts the effect-allele dosage in the raw score.
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rsPAL2",
                        "chrom": "1",
                        "pos": 3,
                        "genotype": "AA",
                        "gnomad_af_global": 0.04,
                        "annotation_coverage": 4,
                    }
                ],
            )
        ws = _harmonized_weight_set(
            [PRSSNPWeight(rsid="rsPAL2", effect_allele="A", other_allele="T", weight=0.3)]
        )
        result = compute_prs(ws, sample_engine)

        assert result.raw_score == pytest.approx(0.0)
        assert result.snps_ambiguous_dropped == 1
        assert result.snps_used == 0
        assert result.contributions[0].match_status == "ambiguous_dropped"

    def test_palindrome_heterozygote_away_from_half_scored(self, sample_engine: sa.Engine) -> None:
        # A palindromic HETEROZYGOTE is strand-invariant (exactly one effect-allele
        # copy on either strand), so away from MAF 0.5 it still scores its single
        # copy — only the strand-ambiguous homozygote is dropped (#247).
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rsPAL3",
                        "chrom": "1",
                        "pos": 4,
                        "genotype": "AT",
                        "gnomad_af_global": 0.04,
                        "annotation_coverage": 4,
                    }
                ],
            )
        ws = _harmonized_weight_set(
            [PRSSNPWeight(rsid="rsPAL3", effect_allele="A", other_allele="T", weight=0.3)]
        )
        result = compute_prs(ws, sample_engine)

        assert result.raw_score == pytest.approx(0.3)  # 0.3 * dosage 1 (strand-invariant)
        assert result.snps_ambiguous_dropped == 0
        assert result.snps_used == 1

    def test_no_call_disclosed_not_counted(self, sample_engine: sa.Engine) -> None:
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rsNC",
                        "chrom": "1",
                        "pos": 4,
                        "genotype": "--",
                        "gnomad_af_global": 0.2,
                        "annotation_coverage": 4,
                    }
                ],
            )
        ws = _harmonized_weight_set(
            [PRSSNPWeight(rsid="rsNC", effect_allele="C", other_allele="T", weight=0.5)]
        )
        result = compute_prs(ws, sample_engine)

        assert result.snps_no_call == 1
        assert result.snps_used == 0
        assert result.raw_score == 0.0
        assert result.contributions[0].match_status == "no_call"

    def test_disclosure_counters_in_detail_json(self, sample_engine: sa.Engine) -> None:
        """store_prs_findings surfaces the harmonization counters."""
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rsFLIP",
                        "chrom": "1",
                        "pos": 1,
                        "genotype": "GG",
                        "gnomad_af_global": 0.2,
                        "annotation_coverage": 4,
                    },
                    {
                        "rsid": "rsPAL",
                        "chrom": "1",
                        "pos": 2,
                        # Palindromic HOMOZYGOTE: still strand-ambiguous for a single
                        # sample, so still dropped — exercises the ambiguous_dropped
                        # disclosure counter (a het near 0.5 now resolves, #353).
                        "genotype": "AA",
                        "gnomad_af_global": 0.5,
                        "annotation_coverage": 4,
                    },
                ],
            )
        ws = _harmonized_weight_set(
            [
                PRSSNPWeight(rsid="rsFLIP", effect_allele="C", other_allele="T", weight=0.5),
                PRSSNPWeight(rsid="rsPAL", effect_allele="A", other_allele="T", weight=0.5),
            ]
        )
        result = run_prs(ws, sample_engine, inferred_ancestry="EUR", n_bootstrap=50, rng_seed=1)
        store_prs_findings([result], sample_engine, module="cancer")

        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.category == "prs")).fetchone()
        detail = json.loads(row.detail_json)
        assert detail["snps_strand_flipped"] == 1
        assert detail["snps_ambiguous_dropped"] == 1
        assert detail["snps_no_call"] == 0
        assert detail["snps_unresolved"] == 0

    def test_legacy_weights_unchanged_without_other_allele(self, sample_engine: sa.Engine) -> None:
        """Without other_allele, a reverse-strand genotype keeps the old (0) score
        — harmonization must not silently activate and change legacy results."""
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rsLEG",
                        "chrom": "1",
                        "pos": 5,
                        "genotype": "GG",
                        "gnomad_af_global": 0.2,
                        "annotation_coverage": 4,
                    }
                ],
            )
        ws = _harmonized_weight_set(
            [PRSSNPWeight(rsid="rsLEG", effect_allele="C", weight=0.5)]  # no other_allele
        )
        result = compute_prs(ws, sample_engine)
        assert result.raw_score == 0.0
        assert result.snps_strand_flipped == 0
        assert result.snps_used == 1  # legacy path still counts it as covered (dosage 0)


# ── Weight set data class tests ─────────────────────────────────────────


class TestPRSWeightSet:
    """Test PRSWeightSet properties."""

    def test_snp_count(self, weight_set: PRSWeightSet) -> None:
        assert weight_set.snp_count == 5

    def test_rsid_set(self, weight_set: PRSWeightSet) -> None:
        expected = {"rs1001", "rs1002", "rs1003", "rs1004", "rs1005"}
        assert weight_set.rsid_set() == expected

    def test_empty_weight_set(self) -> None:
        ws = PRSWeightSet(
            name="Empty",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=0,
            weights=[],
            reference_mean=0.0,
            reference_std=1.0,
        )
        assert ws.snp_count == 0
        assert ws.rsid_set() == set()


# ── PRSResult data class tests ──────────────────────────────────────────


class TestPRSResult:
    """Test PRSResult properties."""

    def test_is_sufficient_true(self) -> None:
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.5,
            coverage_fraction=0.5,
        )
        assert result.is_sufficient is True

    def test_is_sufficient_false(self) -> None:
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.5,
            coverage_fraction=0.49,
        )
        assert result.is_sufficient is False

    def test_has_bootstrap_ci_false(self) -> None:
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.5,
        )
        assert result.has_bootstrap_ci is False

    def test_has_bootstrap_ci_true(self) -> None:
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.5,
            bootstrap_ci_lower=40.0,
            bootstrap_ci_upper=60.0,
        )
        assert result.has_bootstrap_ci is True
