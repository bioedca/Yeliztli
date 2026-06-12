"""Tests for the APOL1 kidney-risk module (G1/G2 + N264K, ancestry-contextualized, recessive).

APOL1 risk is recessive (two risk alleles across G1/G2), interpreted with
African-ancestry validation context, and modified by N264K (rs73885316). The
honesty guardrails under test: directly observed high-risk genotypes are reported
with an ancestry caveat when global ancestry is not predominantly AFR; partial
indeterminate calls are suppressed outside that ancestry context; the G2 indel
being off-chip yields a partial genotype, never a false low-risk; an unassessed
N264K caveats a high-risk call rather than overstating it; common risk alleles
write clinvar_significance=NULL.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend.analysis.apol1 import assess_apol1, load_apol1_panel, store_apol1_findings
from backend.db.tables import findings, raw_variants


@pytest.fixture()
def panel():
    return load_apol1_panel()


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    if rows:
        with engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), rows)


def _seed_ancestry(engine: sa.Engine, top_population: str, fraction: float = 0.85) -> None:
    detail = {"top_population": top_population, "admixture_fractions": {top_population: fraction}}
    with engine.begin() as conn:
        conn.execute(
            sa.insert(findings),
            [
                {
                    "module": "ancestry",
                    "category": "nnls_admixture",
                    "evidence_level": 1,
                    "finding_text": f"Ancestry: {top_population}",
                    "detail_json": json.dumps(detail),
                }
            ],
        )


def _g1(genotype: str) -> dict:  # risk G / ref A
    return {"rsid": "rs73885319", "chrom": "22", "pos": 36661906, "genotype": genotype}


def _g1b(genotype: str) -> dict:  # rs60910145, risk G / ref T
    return {"rsid": "rs60910145", "chrom": "22", "pos": 36662034, "genotype": genotype}


def _g2(genotype: str) -> dict:  # indel risk D / ref I
    return {"rsid": "rs71785313", "chrom": "22", "pos": 36662042, "genotype": genotype}


def _n264k(genotype: str) -> dict:  # modifier-present A / ref C
    return {"rsid": "rs73885316", "chrom": "22", "pos": 36661674, "genotype": genotype}


class TestHighRiskAFR:
    def test_g1_homozygous_high_risk(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("GG"), _g2("II"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert a.ancestry_suppressed is False
        assert len(a.calls) == 1
        call = a.calls[0]
        assert "high-risk" in call.risk_classification.lower()
        assert "10.5" in call.finding_text and "7.3" in call.finding_text
        caveats = " ".join(call.detail["caveats"]).lower()
        assert "recessive" in caveats  # recessive note

    def test_g2_homozygous_high_risk(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("AA"), _g2("DD"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert len(a.calls) == 1
        assert "high-risk" in a.calls[0].risk_classification.lower()

    def test_g1_g2_compound_high_risk(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("AG"), _g2("DI"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert len(a.calls) == 1
        assert "high-risk" in a.calls[0].risk_classification.lower()

    def test_single_g1_allele_low_risk_no_finding(self, panel, sample_engine: sa.Engine) -> None:
        # G2 confirmed reference -> genuinely low-risk (one allele, recessive).
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("AG"), _g2("II"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert a.calls == []  # one risk allele is not high-risk (recessive)

    def test_single_g1_allele_g2_off_chip_is_indeterminate(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # One G1 allele typed, the G2 6-bp deletion off-chip -> the recessive
        # status cannot be determined; disclose a partial genotype, never silent.
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("AG")])  # G2 (rs71785313) absent
        a = assess_apol1(panel, sample_engine)
        assert len(a.calls) == 1
        call = a.calls[0]
        assert "indeterminate" in call.risk_classification.lower()
        assert call.detail["indeterminate"] is True
        assert "rs71785313" in call.detail["untyped_loci"]
        assert "not a low-risk result" in call.finding_text.lower()

    def test_indeterminate_suppressed_for_non_african_ancestry(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_g1("AG")])  # G2 off-chip, but EUR -> not actionable
        a = assess_apol1(panel, sample_engine)
        assert a.calls == []
        assert a.ancestry_suppressed is True


class TestAncestryGate:
    def test_eur_observed_high_risk_caveated(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_g1("AA"), _g2("DD"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert len(a.calls) == 1
        assert "high-risk" in a.calls[0].risk_classification.lower()
        assert a.ancestry_suppressed is False
        assert (
            "Top global ancestry does not prove local ancestry at APOL1" in a.calls[0].finding_text
        )
        assert any(
            "Top global ancestry does not prove local ancestry at APOL1" in caveat
            for caveat in a.calls[0].detail["caveats"]
        )

    def test_afr_below_half_observed_high_risk_caveated(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        _seed_ancestry(sample_engine, "AFR", fraction=0.49)
        _seed(sample_engine, [_g1("AA"), _g2("DD"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert len(a.calls) == 1
        assert a.ancestry_suppressed is False
        assert "extra validation caution" in a.calls[0].finding_text
        assert any("extra validation caution" in caveat for caveat in a.calls[0].detail["caveats"])

    def test_no_ancestry_observed_high_risk_caveated(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # No ancestry finding seeded -> inferred ancestry unknown, but the observed
        # two-risk-allele genotype is still reported with a validation caveat.
        _seed(sample_engine, [_g1("AA"), _g2("DD"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert len(a.calls) == 1
        assert a.ancestry_suppressed is False
        assert "extra validation caution" in a.calls[0].finding_text
        assert any("extra validation caution" in caveat for caveat in a.calls[0].detail["caveats"])


class TestN264KModifier:
    def test_n264k_present_attenuates(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("AA"), _g2("DD"), _n264k("CA")])  # one Lys (A) copy
        a = assess_apol1(panel, sample_engine)
        assert len(a.calls) == 1
        call = a.calls[0]
        assert "attenuat" in call.risk_classification.lower()
        assert call.evidence_stars == 1
        assert "38036523" in call.pmids
        assert all(pmid.isdigit() for pmid in call.pmids)

    def test_g1_hom_g2_and_n264k_off_chip_both_caveats(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # G1/G1 fires high-risk, but G2 (indel) and N264K are both off-chip.
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("GG")])  # G2 and N264K absent
        a = assess_apol1(panel, sample_engine)
        assert len(a.calls) == 1
        call = a.calls[0]
        assert "high-risk" in call.risk_classification.lower()  # still high-risk, not low
        caveats = " ".join(call.detail["caveats"]).lower()
        assert "partial genotype" in caveats  # G2 not typed
        assert "n264k" in caveats and "overstated" in caveats  # modifier not assessed
        assert "rs71785313" in a.indeterminate_loci


class TestStorageAndGuardrails:
    def test_clinvar_significance_null(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("GG"), _g2("II"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert store_apol1_findings(a, sample_engine) == 1
        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.module == "apol1")).fetchone()
        assert row.clinvar_significance is None
        assert row.gene_symbol == "APOL1"
        assert row.evidence_level == 3  # high-risk recessive model is 3 stars

    def test_observed_non_afr_high_risk_stores_caveated_finding(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_g1("AA"), _g2("DD"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert store_apol1_findings(a, sample_engine) == 1
        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.module == "apol1")).fetchone()
        assert "Top global ancestry does not prove local ancestry at APOL1" in row.finding_text
        detail = json.loads(row.detail_json)
        assert any(
            "Top global ancestry does not prove local ancestry at APOL1" in caveat
            for caveat in detail["caveats"]
        )

    def test_indeterminate_non_afr_stores_nothing(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_g1("AG")])
        a = assess_apol1(panel, sample_engine)
        assert a.calls == []
        assert a.ancestry_suppressed is True
        assert store_apol1_findings(a, sample_engine) == 0
