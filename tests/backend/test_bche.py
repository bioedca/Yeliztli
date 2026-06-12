"""BCHE succinylcholine/mivacurium apnea-risk context (SW-E6).

Verifies the forward-strand genotype → BChE-deficiency-risk mapping the route
serves: the atypical (rs1799807, forward C) and K (rs1803274, forward T) alleles
reduce activity, the atypical allele being the major determinant. All genotypes
are GRCh37 plus/forward strand (as real 23andMe data is).
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.analysis.bche import (
    BCHE_ATYPICAL_RSID,
    BCHE_K_RSID,
    BCHE_PMID_CITATIONS,
    assess_bche,
    bche_risk,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants


def _make_sample(genotypes: dict[str, str]) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    rows = [
        {"rsid": rsid, "chrom": "3", "pos": 1000 + i, "genotype": g}
        for i, (rsid, g) in enumerate(genotypes.items())
    ]
    with engine.begin() as conn:
        conn.execute(raw_variants.insert(), rows)
    return engine


class TestBcheRisk:
    def test_homozygous_atypical_is_high(self) -> None:
        r = bche_risk(2, 0)
        assert r["risk_category"] == "high"

    def test_heterozygous_atypical_is_intermediate(self) -> None:
        r = bche_risk(1, 0)
        assert r["risk_category"] == "intermediate"

    def test_atypical_het_with_k_notes_phase_caveat(self) -> None:
        r = bche_risk(1, 1)
        assert r["risk_category"] == "intermediate"
        assert "phase" in r["detail"].lower()

    def test_k_only_is_mild(self) -> None:
        # Atypical (major determinant) typed and absent, ≥1 K allele → genuine mild.
        assert bche_risk(0, 1)["risk_category"] == "mild"
        assert bche_risk(0, 2)["risk_category"] == "mild"

    def test_k_present_atypical_uncalled_is_indeterminate(self) -> None:
        # A K allele observed while the major-determinant atypical variant is uncalled
        # cannot be reported as "mild": an untyped atypical allele could place this in a
        # moderate-to-severe deficiency genotype the array did not resolve (gh #140).
        for k in (1, 2):
            r = bche_risk(None, k)
            assert r is not None
            assert r["risk_category"] == "indeterminate"
            assert "major" in r["detail"].lower()
            assert "not callable" in r["detail"].lower()

    def test_neither_allele_is_typical(self) -> None:
        assert bche_risk(0, 0)["risk_category"] == "typical"

    def test_none_both_returns_none(self) -> None:
        assert bche_risk(None, None) is None

    def test_only_k_assayed_and_absent_is_indeterminate(self) -> None:
        # Atypical (major determinant) not called and no K allele → cannot assess.
        assert bche_risk(None, 0) is None

    def test_atypical_assayed_absent_k_unknown_is_typical(self) -> None:
        r = bche_risk(0, None)
        assert r["risk_category"] == "typical"
        assert "not all" in r["detail"].lower()  # coverage caveat present


class TestAssessBche:
    def test_homozygous_atypical_high_risk(self) -> None:
        engine = _make_sample({BCHE_ATYPICAL_RSID: "CC", BCHE_K_RSID: "CC"})
        result = assess_bche(engine)
        assert result["risk_category"] == "high"
        assert result["coverage_complete"] is True
        variants = {v["rsid"]: v for v in result["variants"]}
        assert variants[BCHE_ATYPICAL_RSID]["reduced_activity_alleles"] == 2
        assert variants[BCHE_K_RSID]["reduced_activity_alleles"] == 0

    def test_typical_when_both_reference(self) -> None:
        engine = _make_sample({BCHE_ATYPICAL_RSID: "TT", BCHE_K_RSID: "CC"})
        result = assess_bche(engine)
        assert result["risk_category"] == "typical"
        assert result["any_called"] is True

    def test_k_heterozygous_is_mild(self) -> None:
        engine = _make_sample({BCHE_ATYPICAL_RSID: "TT", BCHE_K_RSID: "CT"})
        assert assess_bche(engine)["risk_category"] == "mild"

    def test_atypical_het_intermediate(self) -> None:
        engine = _make_sample({BCHE_ATYPICAL_RSID: "TC", BCHE_K_RSID: "CC"})
        assert assess_bche(engine)["risk_category"] == "intermediate"

    def test_incomplete_coverage_when_atypical_missing(self) -> None:
        # K observed but the major-determinant atypical variant uncalled → the result is
        # indeterminate, NOT a complete "mild" K-only call (gh #140).
        engine = _make_sample({BCHE_K_RSID: "CT"})  # no atypical row
        result = assess_bche(engine)
        assert result["coverage_complete"] is False
        assert result["any_called"] is True
        assert result["risk_category"] == "indeterminate"
        assert "not callable" in result["detail"].lower()

    def test_no_call_genotype_not_assessed(self) -> None:
        engine = _make_sample({BCHE_ATYPICAL_RSID: "--", BCHE_K_RSID: "--"})
        result = assess_bche(engine)
        assert result["any_called"] is False
        assert result["risk_category"] is None
        assert "neither" in result["detail"].lower()

    def test_k_called_but_atypical_uncalled_is_unassessed_not_typical(self) -> None:
        # K reference-only with the major-determinant atypical variant uncalled must
        # NOT read as "typical" — risk is None but any_called is True (CodeRabbit #61).
        engine = _make_sample({BCHE_ATYPICAL_RSID: "--", BCHE_K_RSID: "CC"})
        result = assess_bche(engine)
        assert result["any_called"] is True
        assert result["risk_category"] is None
        assert "atypical" in result["detail"].lower()
        assert "not assessed" in result["detail"].lower()
        assert "neither" not in result["detail"].lower()

    def test_context_only_disclosure_and_citation(self) -> None:
        engine = _make_sample({BCHE_ATYPICAL_RSID: "TT", BCHE_K_RSID: "CC"})
        result = assess_bche(engine)
        assert result["context_only"] is True
        assert result["note"]
        assert set(BCHE_PMID_CITATIONS) <= set(result["pmid_citations"])
