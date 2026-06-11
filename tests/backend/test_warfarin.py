"""VKORC1 + CYP4F2 warfarin-dosing context (SW-E1 warfarin layer / #13).

Verifies the forward-strand genotype → dose-direction mapping the route serves:
VKORC1 c.-1639G>A A allele lowers the dose (higher sensitivity); CYP4F2 *3 (T)
raises it. All genotypes are GRCh37 plus/forward strand (as real 23andMe data is).
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.analysis.warfarin import (
    CYP4F2_RSID,
    VKORC1_RSID,
    WARFARIN_CPIC_PMID,
    assess_warfarin,
    cyp4f2_phenotype,
    vkorc1_phenotype,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants


def _make_sample(genotypes: dict[str, str]) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    rows = [
        {"rsid": rsid, "chrom": "16", "pos": 1000 + i, "genotype": g}
        for i, (rsid, g) in enumerate(genotypes.items())
    ]
    with engine.begin() as conn:
        conn.execute(raw_variants.insert(), rows)
    return engine


class TestVkorc1Phenotype:
    def test_homozygous_reference_is_normal(self) -> None:
        p = vkorc1_phenotype(0)
        assert p["diplotype"] == "G/G"
        assert p["dose_effect"] == "typical"

    def test_heterozygous_is_increased_sensitivity(self) -> None:
        p = vkorc1_phenotype(1)
        assert p["diplotype"] == "G/A"
        assert p["dose_effect"] == "lower"
        assert "Increased" in p["phenotype"]

    def test_homozygous_alt_is_highest_sensitivity(self) -> None:
        p = vkorc1_phenotype(2)
        assert p["diplotype"] == "A/A"
        assert p["dose_effect"] == "lowest"

    def test_none_when_uncalled(self) -> None:
        assert vkorc1_phenotype(None) is None


class TestCyp4f2Phenotype:
    def test_homozygous_reference_no_effect(self) -> None:
        p = cyp4f2_phenotype(0)
        assert p["diplotype"] == "*1/*1"
        assert p["dose_effect"] == "typical"

    def test_one_star3_is_modestly_higher(self) -> None:
        p = cyp4f2_phenotype(1)
        assert p["diplotype"] == "*1/*3"
        assert p["dose_effect"] == "higher"

    def test_two_star3_is_higher(self) -> None:
        p = cyp4f2_phenotype(2)
        assert p["diplotype"] == "*3/*3"
        assert p["dose_effect"] == "higher"

    def test_none_when_uncalled(self) -> None:
        assert cyp4f2_phenotype(None) is None


class TestAssessWarfarin:
    def _genes(self, result: dict) -> dict:
        return {g["gene"]: g for g in result["genes"]}

    def test_sensitive_and_high_dose_combination(self) -> None:
        # VKORC1 A/A (forward TT) → lowest dose; CYP4F2 *3/*3 (forward TT) → higher dose.
        engine = _make_sample({VKORC1_RSID: "TT", CYP4F2_RSID: "TT"})
        genes = self._genes(assess_warfarin(engine))
        assert genes["VKORC1"]["diplotype"] == "A/A"
        assert genes["VKORC1"]["dose_effect"] == "lowest"
        assert genes["CYP4F2"]["diplotype"] == "*3/*3"
        assert genes["CYP4F2"]["dose_effect"] == "higher"

    def test_reference_genotypes_are_typical(self) -> None:
        engine = _make_sample({VKORC1_RSID: "CC", CYP4F2_RSID: "CC"})
        genes = self._genes(assess_warfarin(engine))
        assert genes["VKORC1"]["diplotype"] == "G/G"
        assert genes["CYP4F2"]["diplotype"] == "*1/*1"
        assert genes["VKORC1"]["called"] and genes["CYP4F2"]["called"]

    def test_heterozygous_directions(self) -> None:
        engine = _make_sample({VKORC1_RSID: "CT", CYP4F2_RSID: "CT"})
        genes = self._genes(assess_warfarin(engine))
        assert genes["VKORC1"]["dose_effect"] == "lower"  # G/A → lower dose
        assert genes["CYP4F2"]["dose_effect"] == "higher"  # *1/*3 → higher dose

    def test_uncalled_when_variant_absent(self) -> None:
        engine = _make_sample({VKORC1_RSID: "CT"})  # no CYP4F2 row
        result = assess_warfarin(engine)
        genes = self._genes(result)
        assert genes["VKORC1"]["called"] is True
        assert genes["CYP4F2"]["called"] is False
        assert genes["CYP4F2"]["diplotype"] is None
        assert result["any_called"] is True

    def test_no_call_genotype_not_assessed(self) -> None:
        engine = _make_sample({VKORC1_RSID: "--", CYP4F2_RSID: "CC"})
        genes = self._genes(assess_warfarin(engine))
        assert genes["VKORC1"]["called"] is False
        assert genes["CYP4F2"]["called"] is True

    def test_none_called_sets_any_called_false(self) -> None:
        engine = _make_sample({"rs_other": "AA"})
        result = assess_warfarin(engine)
        assert result["any_called"] is False

    def test_context_only_disclosure_and_citation(self) -> None:
        engine = _make_sample({VKORC1_RSID: "CC", CYP4F2_RSID: "CC"})
        result = assess_warfarin(engine)
        assert result["context_only"] is True
        assert result["note"]
        assert WARFARIN_CPIC_PMID in result["pmid_citations"]
        # No milligram dose is ever emitted.
        assert "mg" not in result["note"].lower().split("dosing")[0]
