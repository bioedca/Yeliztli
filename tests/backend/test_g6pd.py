"""G6PD deficiency X-linked context (SW-E6).

Verifies the forward-strand, sex-aware deficiency calling the route serves:
hemizygous males (single-char chrX calls) → deficient on one allele; females →
deficient only when homozygous/compound, heterozygous → *variable* (never a
reassuring "normal"). Strands are GRCh37 plus/forward (as real 23andMe data is).
"""

from __future__ import annotations

from unittest.mock import patch

import sqlalchemy as sa

from backend.analysis.g6pd import (
    G6PD_376_RSID,
    G6PD_A_MINUS_RSID,
    G6PD_MED_RSID,
    G6PD_PMID_CITATIONS,
    _deficiency_alleles,
    assess_g6pd,
    g6pd_phenotype,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants


def _make_sample(genotypes: dict[str, str]) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    rows = [
        {"rsid": rsid, "chrom": "X", "pos": 153764217 + i, "genotype": g}
        for i, (rsid, g) in enumerate(genotypes.items())
    ]
    with engine.begin() as conn:
        conn.execute(raw_variants.insert(), rows)
    return engine


class TestDeficiencyAlleles:
    def test_hemizygous_single_char(self) -> None:
        assert _deficiency_alleles("T", "C", "T") == {"deficiency": 1, "copies": 1}
        assert _deficiency_alleles("C", "C", "T") == {"deficiency": 0, "copies": 1}

    def test_diploid(self) -> None:
        assert _deficiency_alleles("CC", "C", "T") == {"deficiency": 0, "copies": 2}
        assert _deficiency_alleles("CT", "C", "T") == {"deficiency": 1, "copies": 2}
        assert _deficiency_alleles("TT", "C", "T") == {"deficiency": 2, "copies": 2}

    def test_no_call_and_invalid(self) -> None:
        assert _deficiency_alleles("--", "C", "T") is None
        assert _deficiency_alleles("", "C", "T") is None
        assert _deficiency_alleles("G", "C", "T") is None  # unexpected base
        assert _deficiency_alleles("CG", "C", "T") is None  # third allele


class TestG6pdPhenotype:
    def test_male_one_allele_is_deficient(self) -> None:
        assert g6pd_phenotype("XY", 1, True)["phenotype"] == "deficient"

    def test_male_zero_is_normal(self) -> None:
        assert g6pd_phenotype("XY", 0, True)["phenotype"] == "normal"

    def test_female_two_is_deficient(self) -> None:
        assert g6pd_phenotype("XX", 2, True)["phenotype"] == "deficient"

    def test_female_one_is_variable(self) -> None:
        assert g6pd_phenotype("XX", 1, True)["phenotype"] == "variable"

    def test_female_zero_is_normal(self) -> None:
        assert g6pd_phenotype("XX", 0, True)["phenotype"] == "normal"

    def test_unknown_sex_with_deficiency_is_indeterminate(self) -> None:
        for sex in ("unknown", "manual_review"):
            v = g6pd_phenotype(sex, 1, True)
            assert v["phenotype"] == "indeterminate"
            assert "sex" in v["detail"].lower()

    def test_not_called_is_indeterminate(self) -> None:
        assert g6pd_phenotype("XX", 0, False)["phenotype"] == "indeterminate"


class TestAssessG6pd:
    def _assess(self, sex: str, genotypes: dict[str, str]) -> dict:
        engine = _make_sample(genotypes)
        with patch("backend.analysis.g6pd.infer_biological_sex", return_value=sex):
            return assess_g6pd(engine)

    def test_hemizygous_male_a_minus_deficient(self) -> None:
        r = self._assess("XY", {G6PD_A_MINUS_RSID: "T"})  # single-char hemizygous
        assert r["phenotype"] == "deficient"
        assert r["at_risk"] is True
        assert "rasburicase" in r["high_risk_drugs"]
        assert r["inferred_sex"] == "XY"

    def test_hemizygous_male_normal(self) -> None:
        r = self._assess("XY", {G6PD_A_MINUS_RSID: "C"})
        assert r["phenotype"] == "normal"
        assert r["at_risk"] is False
        assert r["high_risk_drugs"] == []

    def test_female_heterozygous_is_variable(self) -> None:
        r = self._assess("XX", {G6PD_A_MINUS_RSID: "CT"})
        assert r["phenotype"] == "variable"
        assert r["at_risk"] is True  # variable still warrants caution

    def test_female_homozygous_deficient(self) -> None:
        r = self._assess("XX", {G6PD_A_MINUS_RSID: "TT"})
        assert r["phenotype"] == "deficient"

    def test_female_compound_heterozygote_deficient(self) -> None:
        # A- het + Mediterranean het = two deficient X's → deficient.
        r = self._assess("XX", {G6PD_A_MINUS_RSID: "CT", G6PD_MED_RSID: "GA"})
        assert r["phenotype"] == "deficient"
        assert r["at_risk"] is True
        assert r["high_risk_drugs"]  # drug context surfaced for the deficient compound
        # Both deficiency loci were callable and each contributed an allele.
        by_rsid = {v["rsid"]: v for v in r["variants"]}
        assert by_rsid[G6PD_A_MINUS_RSID]["deficiency_alleles"] == 1
        assert by_rsid[G6PD_MED_RSID]["deficiency_alleles"] == 1

    def test_female_reference_normal(self) -> None:
        # Negative control: no deficiency allele → no risk surfaced.
        r = self._assess("XX", {G6PD_A_MINUS_RSID: "CC", G6PD_MED_RSID: "GG"})
        assert r["phenotype"] == "normal"
        assert r["at_risk"] is False
        assert r["high_risk_drugs"] == []

    def test_unknown_sex_with_deficiency_surfaces_drug_warning(self) -> None:
        r = self._assess("unknown", {G6PD_A_MINUS_RSID: "CT"})
        assert r["phenotype"] == "indeterminate"
        assert r["at_risk"] is True  # deficiency allele present → still warn
        assert r["high_risk_drugs"]

    def test_no_variant_called_is_indeterminate(self) -> None:
        r = self._assess("XY", {G6PD_A_MINUS_RSID: "--", G6PD_MED_RSID: "--"})
        assert r["any_called"] is False
        assert r["phenotype"] == "indeterminate"
        assert r["at_risk"] is False

    def test_a_plus_nondeficient_flagged_as_context(self) -> None:
        # 376G present (rs1050829 = C) with A- reference → A+ non-deficient allele.
        r = self._assess("XX", {G6PD_A_MINUS_RSID: "CC", G6PD_376_RSID: "CC"})
        assert r["a_plus_nondeficient_present"] is True
        assert r["phenotype"] == "normal"

    def test_context_only_disclosure_and_citation(self) -> None:
        r = self._assess("XY", {G6PD_A_MINUS_RSID: "C"})
        assert r["context_only"] is True
        assert r["note"]
        assert set(G6PD_PMID_CITATIONS) <= set(r["pmid_citations"])
