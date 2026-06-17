"""Tests for the centralized 4-star evidence level framework (P3-40).

Covers PRD §3.4 evidence star criteria:
  T3-41: ClinVar P with 2+ stars → 4-star
  T3-42: Single GWAS hit → 1-star

Also validates:
  - ClinVar-based assignment for all review star counts
  - Gene baseline capping for 0-star reviews
  - Ensemble pathogenic fallback
  - CPIC classification mapping
  - GWAS evidence tiers (replicated, single cohort, sub-threshold)
  - Fixed module-level constants (PRS, ancestry)
  - Evidence cap utility
"""

from __future__ import annotations

import pytest

from backend.analysis.evidence import (
    ANCESTRY_EVIDENCE_LEVEL,
    EVIDENCE_DEFINITIVE,
    EVIDENCE_LABELS,
    EVIDENCE_MODERATE,
    EVIDENCE_PRELIMINARY,
    EVIDENCE_STRONG,
    PRS_EVIDENCE_LEVEL,
    TRAITS_EVIDENCE_CAP,
    assign_clinvar_evidence_level,
    assign_cpic_evidence_level,
    assign_gwas_evidence_level,
    cap_evidence_level,
)

# ── Constants ────────────────────────────────────────────────────────────


class TestConstants:
    """Verify evidence level constants match PRD §3.4."""

    def test_level_values(self):
        assert EVIDENCE_DEFINITIVE == 4
        assert EVIDENCE_STRONG == 3
        assert EVIDENCE_MODERATE == 2
        assert EVIDENCE_PRELIMINARY == 1

    def test_labels_defined_for_all_levels(self):
        for level in (1, 2, 3, 4):
            assert level in EVIDENCE_LABELS

    def test_prs_level(self):
        assert PRS_EVIDENCE_LEVEL == 1

    def test_ancestry_level(self):
        assert ANCESTRY_EVIDENCE_LEVEL == 2

    def test_traits_cap(self):
        assert TRAITS_EVIDENCE_CAP == 2


# ── ClinVar-based evidence ──────────────────────────────────────────────


class TestAssignClinvarEvidenceLevel:
    """Test ClinVar-based evidence assignment per PRD §3.4."""

    # T3-41: ClinVar P with 2+ stars → 4-star
    def test_pathogenic_2_star_review(self):
        """★★★★ — ClinVar Pathogenic with ≥2-star review."""
        result = assign_clinvar_evidence_level("Pathogenic", 2)
        assert result == 4

    def test_pathogenic_3_star_review(self):
        """★★★★ — ClinVar Pathogenic with 3-star review."""
        result = assign_clinvar_evidence_level("Pathogenic", 3)
        assert result == 4

    def test_pathogenic_4_star_review(self):
        """★★★★ — ClinVar Pathogenic with 4-star review."""
        result = assign_clinvar_evidence_level("Pathogenic", 4)
        assert result == 4

    def test_likely_pathogenic_2_star_review(self):
        """★★★★ — ClinVar LP with ≥2-star review."""
        result = assign_clinvar_evidence_level("Likely pathogenic", 2)
        assert result == 4

    def test_pathogenic_compound_2_star(self):
        """★★★★ — ClinVar P primary with a secondary clause."""
        result = assign_clinvar_evidence_level("Pathogenic|drug response", 2)
        assert result == 4

    def test_likely_pathogenic_compound_2_star(self):
        """★★★★ — ClinVar LP primary with a (non-downgrading) secondary clause."""
        result = assign_clinvar_evidence_level("Likely pathogenic|risk factor", 2)
        assert result == 4

    def test_pathogenic_1_star_review(self):
        """★★★★ — ClinVar Pathogenic with 1-star review still gets 4."""
        result = assign_clinvar_evidence_level("Pathogenic", 1)
        assert result == 4

    def test_pathogenic_compound_1_star_review(self):
        """★★★★ — ClinVar Pathogenic primary with 1-star review still gets 4."""
        result = assign_clinvar_evidence_level("Pathogenic|drug response", 1)
        assert result == 4

    def test_low_penetrance_not_promoted_to_definitive(self):
        """#1027 — low-penetrance P/LP is capped at MODERATE, not ordinary
        high-penetrance P/LP."""
        assert assign_clinvar_evidence_level("Pathogenic, low penetrance", 2) == EVIDENCE_MODERATE
        assert (
            assign_clinvar_evidence_level("Likely pathogenic, low penetrance", 2)
            == EVIDENCE_MODERATE
        )

    def test_risk_allele_compound_not_promoted(self):
        """#1027 — a risk-allele compound is capped at MODERATE."""
        assert (
            assign_clinvar_evidence_level("Pathogenic, Established risk allele", 3)
            == EVIDENCE_MODERATE
        )

    def test_likely_pathogenic_1_star_review(self):
        """★★★☆ — ClinVar LP with 1-star review."""
        result = assign_clinvar_evidence_level("Likely pathogenic", 1)
        assert result == 3

    def test_pathogenic_0_star_no_baseline(self):
        """★★☆☆ — ClinVar P with 0 stars, no gene baseline."""
        result = assign_clinvar_evidence_level("Pathogenic", 0)
        assert result == 2

    def test_likely_pathogenic_0_star_no_baseline(self):
        """★★☆☆ — ClinVar LP with 0 stars, no gene baseline."""
        result = assign_clinvar_evidence_level("Likely pathogenic", 0)
        assert result == 2

    def test_pathogenic_0_star_with_high_baseline(self):
        """★★☆☆ — 0 stars capped at min(gene_baseline, 2)."""
        result = assign_clinvar_evidence_level("Pathogenic", 0, gene_baseline=4)
        assert result == 2  # capped at 2

    def test_pathogenic_0_star_with_low_baseline(self):
        """★☆☆☆ — 0 stars capped at min(gene_baseline=1, 2) = 1."""
        result = assign_clinvar_evidence_level("Pathogenic", 0, gene_baseline=1)
        assert result == 1

    def test_no_clinvar_no_ensemble(self):
        """★☆☆☆ — No ClinVar, no ensemble prediction."""
        result = assign_clinvar_evidence_level(None, None)
        assert result == 1

    def test_empty_clinvar_significance(self):
        """★☆☆☆ — Empty string significance."""
        result = assign_clinvar_evidence_level("", 0)
        assert result == 1

    def test_slash_compound_storage_phantom_is_not_matched(self):
        """★☆☆☆ — slash compounds are not stored; only pipe/comma compounds match."""
        result = assign_clinvar_evidence_level("Pathogenic/Likely pathogenic", 2)
        assert result == 1

    def test_vus_no_ensemble(self):
        """★☆☆☆ — VUS without ensemble pathogenic."""
        result = assign_clinvar_evidence_level("Uncertain significance", 1)
        assert result == 1

    def test_benign_no_ensemble(self):
        """★☆☆☆ — Benign variant."""
        result = assign_clinvar_evidence_level("Benign", 2)
        assert result == 1

    def test_ensemble_pathogenic_no_clinvar(self):
        """★☆☆☆ — ensemble (in-silico) support alone stays PRELIMINARY, not ★★ (F19)."""
        result = assign_clinvar_evidence_level(None, None, ensemble_pathogenic=True)
        assert result == 1

    def test_ensemble_pathogenic_with_vus(self):
        """★☆☆☆ — a VUS with ensemble support is not promoted to ★★ (F19).

        In-silico prediction is computational, not functional, evidence; the PRD
        rubric reserves ★★ MODERATE for functional/clinical evidence.
        """
        result = assign_clinvar_evidence_level(
            "Uncertain significance", 1, ensemble_pathogenic=True
        )
        assert result == 1

    def test_ensemble_pathogenic_does_not_override_clinvar(self):
        """ClinVar P/LP takes precedence over ensemble flag."""
        result = assign_clinvar_evidence_level("Pathogenic", 2, ensemble_pathogenic=True)
        assert result == 4  # ClinVar P ≥2 stars wins

    def test_none_stars_treated_as_zero(self):
        """None review stars treated as 0."""
        result = assign_clinvar_evidence_level("Pathogenic", None)
        assert result == 2  # P with 0 stars


# ── CPIC-based evidence ─────────────────────────────────────────────────


class TestAssignCpicEvidenceLevel:
    """Test CPIC classification → evidence star mapping."""

    def test_tier_a(self):
        """CPIC Tier A → ★★★★."""
        assert assign_cpic_evidence_level("A") == 4

    def test_tier_b(self):
        """CPIC Tier B → ★★★☆."""
        assert assign_cpic_evidence_level("B") == 3

    def test_tier_c(self):
        """CPIC Tier C → ★★☆☆."""
        assert assign_cpic_evidence_level("C") == 2

    def test_tier_d(self):
        """CPIC Tier D → ★★☆☆."""
        assert assign_cpic_evidence_level("D") == 2

    def test_none_classification(self):
        """None → ★★☆☆ (default)."""
        assert assign_cpic_evidence_level(None) == 2

    def test_unknown_classification(self):
        """Unknown string → ★★☆☆ (default)."""
        assert assign_cpic_evidence_level("X") == 2


# ── GWAS-based evidence ─────────────────────────────────────────────────


class TestAssignGwasEvidenceLevel:
    """Test GWAS-based evidence assignment per PRD §3.4."""

    # T3-42: Single GWAS hit → 1-star (sub-threshold example)
    def test_single_gwas_sub_threshold(self):
        """★☆☆☆ — Single study, p > 5e-8."""
        result = assign_gwas_evidence_level(p_value=1e-6)
        assert result == 1

    def test_single_gwas_genome_wide(self):
        """★★☆☆ — Single cohort GWAS hit p < 5e-8."""
        result = assign_gwas_evidence_level(p_value=1e-9)
        assert result == 2

    def test_replicated_gwas(self):
        """★★★☆ — Replicated in ≥2 cohorts with GW significance."""
        result = assign_gwas_evidence_level(replicated=True, p_value=1e-10)
        assert result == 3

    def test_replicated_but_not_significant(self):
        """★☆☆☆ — Replicated but not genome-wide significant."""
        result = assign_gwas_evidence_level(replicated=True, p_value=1e-5)
        assert result == 1

    def test_high_or_genome_wide(self):
        """★★★★ — OR > 5 with genome-wide significance."""
        result = assign_gwas_evidence_level(p_value=1e-12, odds_ratio=6.5)
        assert result == 4

    def test_high_or_not_significant(self):
        """★☆☆☆ — OR > 5 but not genome-wide significant."""
        result = assign_gwas_evidence_level(p_value=1e-5, odds_ratio=8.0)
        assert result == 1

    def test_moderate_or_genome_wide(self):
        """★★☆☆ — OR ≤ 5, genome-wide significant, not replicated."""
        result = assign_gwas_evidence_level(p_value=1e-9, odds_ratio=2.5)
        assert result == 2

    def test_no_p_value(self):
        """★☆☆☆ — No p-value provided."""
        result = assign_gwas_evidence_level()
        assert result == 1

    def test_no_p_value_but_replicated(self):
        """★☆☆☆ — Replicated but no p-value (can't verify significance)."""
        result = assign_gwas_evidence_level(replicated=True)
        assert result == 1

    def test_exactly_threshold_p_value(self):
        """★☆☆☆ — p-value exactly at 5e-8 (not less than)."""
        result = assign_gwas_evidence_level(p_value=5e-8)
        assert result == 1

    def test_just_below_threshold(self):
        """★★☆☆ — p-value just below 5e-8."""
        result = assign_gwas_evidence_level(p_value=4.99e-8)
        assert result == 2


# ── Cap utility ──────────────────────────────────────────────────────────


class TestCapEvidenceLevel:
    """Test evidence level capping utility."""

    def test_cap_below(self):
        """Level below cap is unchanged."""
        assert cap_evidence_level(1, 2) == 1

    def test_cap_at(self):
        """Level at cap is unchanged."""
        assert cap_evidence_level(2, 2) == 2

    def test_cap_above(self):
        """Level above cap is reduced."""
        assert cap_evidence_level(4, 2) == 2

    def test_traits_cap(self):
        """Traits & Personality cap at ★★☆☆."""
        assert cap_evidence_level(3, TRAITS_EVIDENCE_CAP) == 2
        assert cap_evidence_level(1, TRAITS_EVIDENCE_CAP) == 1


# ── Integration: backward compatibility ──────────────────────────────────


class TestBackwardCompatibility:
    """Verify centralized functions produce same results as old module-local functions.

    The old functions in cancer.py, cardiovascular.py, carrier_status.py all
    had identical logic. These tests confirm the centralized version matches.
    """

    @pytest.mark.parametrize(
        "sig,stars,baseline,expected",
        [
            ("Pathogenic", 2, 4, 4),
            ("Pathogenic", 3, 4, 4),
            ("Likely pathogenic", 2, 3, 4),
            ("Pathogenic", 1, 4, 4),
            ("Likely pathogenic", 1, 3, 3),
            ("Pathogenic", 0, 4, 2),
            ("Pathogenic", 0, 1, 1),
            ("Likely pathogenic", 0, 3, 2),
            ("Likely pathogenic", 0, 2, 2),
        ],
    )
    def test_clinvar_with_gene_baseline(self, sig: str, stars: int, baseline: int, expected: int):
        """Match old _assign_evidence_level(sig, stars, gene_baseline) behavior."""
        result = assign_clinvar_evidence_level(sig, stars, gene_baseline=baseline)
        assert result == expected

    @pytest.mark.parametrize(
        "classification,expected",
        [
            ("A", 4),
            ("B", 3),
            ("C", 2),
            ("D", 2),
            (None, 2),
        ],
    )
    def test_cpic_mapping(self, classification: str | None, expected: int):
        """Match old _CPIC_CLASSIFICATION_STARS dict behavior."""
        assert assign_cpic_evidence_level(classification) == expected
