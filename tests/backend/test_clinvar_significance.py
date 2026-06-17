"""Tests for the shared ClinVar pathogenic-significance matcher (#813).

ClinVar stores compound significance strings whose *primary* classification is
(Likely) Pathogenic with a secondary clause appended via ``|`` or ``,`` (e.g.
``"Pathogenic|drug response"``, ``"Pathogenic, low penetrance"``). The old
exact-match set dropped them, silently missing carriers. These tests lock the
primary-token matcher — and the #799 boundary that ``"Conflicting classifications
of pathogenicity"`` is NOT pathogenic.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from backend.analysis.clinvar_significance import (
    is_low_penetrance_or_risk_allele,
    is_pathogenic_primary,
    low_penetrance_or_risk_allele_filter,
    pathogenic_significance_filter,
    primary_pathogenic_classification,
)
from backend.db.tables import annotated_variants, sample_metadata_obj

_PATHOGENIC = [
    "Pathogenic",
    "Likely pathogenic",
    "Pathogenic|drug response",
    "Pathogenic|risk factor",  # "risk factor" is a clinical-impact clause, not "risk allele"
    "Pathogenic|Affects",
    "Likely pathogenic|risk factor",
]

# Low-penetrance / risk-allele modifiers: a distinct ClinGen category, NOT ordinary
# high-penetrance Mendelian P/LP, so they must NOT be promoted into the pathogenic
# path (#987). The leading token is Pathogenic/Likely pathogenic, but the modifier
# downgrades them.
_NON_MENDELIAN = [
    "Pathogenic, low penetrance",
    "Likely pathogenic, low penetrance",
    "Pathogenic|low penetrance",
    "Pathogenic, Established risk allele",
    "Likely pathogenic|Likely risk allele",
]

_NOT_PATHOGENIC = [
    "Conflicting classifications of pathogenicity",  # #799 boundary — not a confident call
    "Benign",
    "Likely benign",
    "Benign/Likely benign",
    "Uncertain significance",
    "drug response",  # secondary clause alone is not a pathogenic primary
    "risk factor",
    "Established risk allele",  # standalone risk-allele term — not Mendelian P/LP (#987)
    "Pathogenic/Likely pathogenic",  # slash compounds are normalized before storage
    *_NON_MENDELIAN,  # low-penetrance / risk-allele compounds (#987)
    "",
    None,
]


class TestPathogenicSignificanceFilter:
    """The SQLAlchemy predicate must select exactly the primary-pathogenic rows."""

    def _query(self, significances: list[str]) -> set[str]:
        engine = sa.create_engine("sqlite://")
        sample_metadata_obj.create_all(engine)
        with engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": f"rs{i}",
                        "chrom": "1",
                        "pos": 1000 + i,
                        "genotype": "CT",
                        "zygosity": "het",
                        "clinvar_significance": sig,
                        "annotation_coverage": 0,
                    }
                    for i, sig in enumerate(significances)
                ],
            )
            rows = conn.execute(
                sa.select(annotated_variants.c.clinvar_significance).where(
                    pathogenic_significance_filter(annotated_variants.c.clinvar_significance)
                )
            ).fetchall()
        return {r.clinvar_significance for r in rows}

    def test_selects_primary_pathogenic_including_compounds(self) -> None:
        matched = self._query(_PATHOGENIC + [s for s in _NOT_PATHOGENIC if s])
        assert matched == set(_PATHOGENIC)

    def test_conflicting_is_not_selected(self) -> None:
        # The exact #799 boundary: "Conflicting classifications of pathogenicity"
        # contains "pathogenicity" but is not a pathogenic primary classification.
        matched = self._query(["Pathogenic", "Conflicting classifications of pathogenicity"])
        assert matched == {"Pathogenic"}

    def test_low_penetrance_and_risk_allele_compounds_excluded(self) -> None:
        # #987: ClinGen low-penetrance / risk-allele assertions are a distinct
        # category — not ordinary high-penetrance Mendelian P/LP — so the filter
        # must NOT select them, even though their primary token is Pathogenic. A
        # plain "drug response" / "risk factor" clause is still kept.
        matched = self._query(
            ["Pathogenic", "Pathogenic|drug response", "Pathogenic|risk factor", *_NON_MENDELIAN]
        )
        assert matched == {"Pathogenic", "Pathogenic|drug response", "Pathogenic|risk factor"}


class TestLowPenetranceRiskAlleleFilter:
    """The lower-penetrance/risk-allele tier is distinct from ordinary P/LP."""

    def _query(self, significances: list[str]) -> set[str]:
        engine = sa.create_engine("sqlite://")
        sample_metadata_obj.create_all(engine)
        with engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": f"rs_lp_{i}",
                        "chrom": "1",
                        "pos": 2000 + i,
                        "genotype": "CT",
                        "zygosity": "het",
                        "clinvar_significance": sig,
                        "annotation_coverage": 0,
                    }
                    for i, sig in enumerate(significances)
                ],
            )
            rows = conn.execute(
                sa.select(annotated_variants.c.clinvar_significance).where(
                    low_penetrance_or_risk_allele_filter(annotated_variants.c.clinvar_significance)
                )
            ).fetchall()
        return {r.clinvar_significance for r in rows}

    def test_selects_low_penetrance_and_risk_allele_terms(self) -> None:
        matched = self._query(
            [
                "Pathogenic, low penetrance",
                "Pathogenic/Established risk allele",
                "Established risk allele",
                "Pathogenic|risk factor",
                "Pathogenic",
            ]
        )
        assert matched == {
            "Pathogenic, low penetrance",
            "Pathogenic/Established risk allele",
            "Established risk allele",
        }

    @pytest.mark.parametrize("significance", _NON_MENDELIAN + ["Established risk allele"])
    def test_python_predicate_identifies_distinct_tier(self, significance: str) -> None:
        assert is_low_penetrance_or_risk_allele(significance) is True

    @pytest.mark.parametrize("significance", ["Pathogenic|risk factor", "Pathogenic", None])
    def test_python_predicate_keeps_risk_factor_out(self, significance: str | None) -> None:
        assert is_low_penetrance_or_risk_allele(significance) is False


class TestPathogenicPrimaryPredicate:
    @pytest.mark.parametrize("significance", _PATHOGENIC)
    def test_python_predicate_matches_sql_boundary(self, significance: str) -> None:
        assert is_pathogenic_primary(significance) is True
        assert primary_pathogenic_classification(significance) in {
            "Pathogenic",
            "Likely pathogenic",
        }

    @pytest.mark.parametrize("significance", _NOT_PATHOGENIC)
    def test_python_predicate_excludes_non_primary_pathogenic(
        self, significance: str | None
    ) -> None:
        assert is_pathogenic_primary(significance) is False
        assert primary_pathogenic_classification(significance) is None
