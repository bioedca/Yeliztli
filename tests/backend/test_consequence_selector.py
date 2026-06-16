"""Tests for consequence severity ranking + MANE Select flagging (P2-03).

Dedicated test coverage for:
- T2-02: consequence_severity correctly ranks stop_gained > missense > synonymous
- T2-03: MANE Select column is present in the annotated_variants schema

Also covers:
- consequence_severity() public API
- Edge cases: empty/None inputs, unknown terms, single terms
"""

from __future__ import annotations

from backend.annotation.vep_bundle import (
    CONSEQUENCE_SEVERITY,
    consequence_severity,
)

# ═══════════════════════════════════════════════════════════════════════
# T2-02: consequence_severity ranking
# ═══════════════════════════════════════════════════════════════════════


class TestConsequenceSeverity:
    """T2-02: Most-severe consequence correctly ranks SO terms."""

    def test_stop_gained_gt_missense(self) -> None:
        """stop_gained > missense_variant."""
        assert consequence_severity("stop_gained") > consequence_severity("missense_variant")

    def test_missense_gt_synonymous(self) -> None:
        """missense_variant > synonymous_variant."""
        assert consequence_severity("missense_variant") > consequence_severity(
            "synonymous_variant"
        )

    def test_stop_gained_gt_synonymous(self) -> None:
        """Transitive: stop_gained > synonymous_variant."""
        assert consequence_severity("stop_gained") > consequence_severity("synonymous_variant")

    def test_frameshift_gt_missense(self) -> None:
        assert consequence_severity("frameshift_variant") > consequence_severity(
            "missense_variant"
        )

    def test_splice_acceptor_gt_splice_region(self) -> None:
        assert consequence_severity("splice_acceptor_variant") > consequence_severity(
            "splice_region_variant"
        )

    def test_transcript_ablation_is_most_severe(self) -> None:
        """transcript_ablation has the highest severity score."""
        max_term = max(CONSEQUENCE_SEVERITY, key=CONSEQUENCE_SEVERITY.get)
        assert max_term == "transcript_ablation"
        assert consequence_severity("transcript_ablation") == 35

    def test_intergenic_is_least_severe(self) -> None:
        assert consequence_severity("intergenic_variant") == 0

    def test_compound_consequence_uses_max(self) -> None:
        """Compound &-delimited terms return the max severity."""
        compound = "missense_variant&splice_region_variant"
        assert consequence_severity(compound) == CONSEQUENCE_SEVERITY["missense_variant"]

    def test_compound_three_terms(self) -> None:
        compound = "synonymous_variant&missense_variant&intron_variant"
        assert consequence_severity(compound) == CONSEQUENCE_SEVERITY["missense_variant"]

    def test_none_returns_negative(self) -> None:
        assert consequence_severity(None) == -1

    def test_empty_string_returns_negative(self) -> None:
        assert consequence_severity("") == -1

    def test_unknown_term_returns_zero(self) -> None:
        assert consequence_severity("totally_unknown_term") == 0

    def test_all_so_terms_have_scores(self) -> None:
        """Every term in the ranking dict has a non-negative score."""
        for term, score in CONSEQUENCE_SEVERITY.items():
            assert score >= 0, f"{term} has negative score {score}"

    def test_ranking_covers_ensembl_core_terms(self) -> None:
        """Key Ensembl VEP terms are present in the ranking."""
        core_terms = [
            "transcript_ablation",
            "stop_gained",
            "frameshift_variant",
            "missense_variant",
            "synonymous_variant",
            "intron_variant",
            "intergenic_variant",
        ]
        for term in core_terms:
            assert term in CONSEQUENCE_SEVERITY, f"Missing core term: {term}"


# ═══════════════════════════════════════════════════════════════════════
# Integration: MANE Select in annotated_variants schema
# ═══════════════════════════════════════════════════════════════════════


class TestManeSelectSchema:
    """Verify mane_select column exists in annotated_variants schema."""

    def test_mane_select_column_exists(self) -> None:
        from backend.db.tables import annotated_variants

        col_names = [c.name for c in annotated_variants.columns]
        assert "mane_select" in col_names

    def test_mane_select_is_boolean(self) -> None:
        import sqlalchemy as sa

        from backend.db.tables import annotated_variants

        col = annotated_variants.c.mane_select
        assert isinstance(col.type, sa.Boolean)

    def test_mane_select_defaults_to_false(self) -> None:
        """mane_select has a server_default of 0 (false)."""
        from backend.db.tables import annotated_variants

        col = annotated_variants.c.mane_select
        assert col.server_default is not None
        assert str(col.server_default.arg) == "0"
