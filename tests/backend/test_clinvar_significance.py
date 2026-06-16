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
    is_pathogenic_primary,
    pathogenic_significance_filter,
    primary_pathogenic_classification,
)
from backend.db.tables import annotated_variants, sample_metadata_obj

_PATHOGENIC = [
    "Pathogenic",
    "Likely pathogenic",
    "Pathogenic|drug response",
    "Pathogenic, low penetrance",
    "Pathogenic|risk factor",
    "Pathogenic|Affects",
    "Likely pathogenic|risk factor",
    "Likely pathogenic, low penetrance",
]

_NOT_PATHOGENIC = [
    "Conflicting classifications of pathogenicity",  # #799 boundary — not a confident call
    "Benign",
    "Likely benign",
    "Benign/Likely benign",
    "Uncertain significance",
    "drug response",  # secondary clause alone is not a pathogenic primary
    "risk factor",
    "Pathogenic/Likely pathogenic",  # slash compounds are normalized before storage
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
