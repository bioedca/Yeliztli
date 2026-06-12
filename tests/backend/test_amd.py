"""Tests for the AMD module (CFH Y402H rs1061170 / ARMS2 rs10490924).

AMD reports **common GWAS risk alleles, not ClinVar P/LP** — so every finding
must carry ``clinvar_significance = None`` and capped evidence stars (≤3). The
honesty guardrails under test: relative ORs always paired with absolute context;
the "~47% / just under half / all 52 variants" architecture statement is encoded
verbatim and never overstated to "more than half"; off-chip loci → indeterminate.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend.analysis.amd import assess_amd, load_amd_panel, store_amd_findings
from backend.db.tables import findings, raw_variants
from backend.disclaimers import AMD_DISCLAIMER_TEXT


@pytest.fixture()
def panel():
    return load_amd_panel()


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    if not rows:
        return
    with engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), rows)


def _cfh(genotype: str) -> dict:
    return {"rsid": "rs1061170", "chrom": "1", "pos": 196659237, "genotype": genotype}


def _arms2(genotype: str) -> dict:
    return {"rsid": "rs10490924", "chrom": "10", "pos": 124214448, "genotype": genotype}


class TestGenotypes:
    def test_cfh_homozygous(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_cfh("CC"), _arms2("GG")])
        a = assess_amd(panel, sample_engine)
        assert len(a.calls) == 1
        call = a.calls[0]
        assert call.risk_classification == "CFH Y402H homozygous"
        assert call.evidence_stars == 3
        assert "7.2" in call.finding_text

    def test_arms2_homozygous(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_cfh("TT"), _arms2("TT")])
        a = assess_amd(panel, sample_engine)
        assert a.calls[0].risk_classification == "ARMS2 homozygous"
        assert "5.5" in a.calls[0].finding_text

    def test_double_homozygous_wide_ci(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_cfh("CC"), _arms2("TT")])
        a = assess_amd(panel, sample_engine)
        call = a.calls[0]
        assert "double-homozygous" in call.risk_classification
        assert "33.3" in call.finding_text
        text = call.finding_text.lower()
        assert "illustrative" in text
        assert "wide confidence interval" in text

    def test_both_heterozygous_intermediate(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_cfh("CT"), _arms2("GT")])
        a = assess_amd(panel, sample_engine)
        call = a.calls[0]
        assert "compound heterozygous" in call.risk_classification
        assert call.evidence_stars == 2

    def test_cfh_heterozygous(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_cfh("CT"), _arms2("GG")])
        a = assess_amd(panel, sample_engine)
        assert a.calls[0].risk_classification == "CFH Y402H heterozygous"

    def test_cfh_homozygous_arms2_het_keeps_both_loci(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        """CC/GT must not drop the ARMS2 het locus (issue #168)."""
        _seed(sample_engine, [_cfh("CC"), _arms2("GT")])
        a = assess_amd(panel, sample_engine)
        assert len(a.calls) == 1
        call = a.calls[0]
        assert call.model_id == "cfh_homozygous_arms2_het"
        # Both typed risk loci are recorded in the finding, not just CFH.
        assert set(call.detail["genotype_calls"]) == {"rs1061170", "rs10490924"}
        assert call.detail["genotype_calls"]["rs10490924"] == "GT"
        assert call.evidence_stars == 3
        assert "synergistic" in call.finding_text.lower()

    def test_cfh_het_arms2_homozygous_keeps_both_loci(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        """CT/TT must not drop the CFH het locus (issue #168)."""
        _seed(sample_engine, [_cfh("CT"), _arms2("TT")])
        a = assess_amd(panel, sample_engine)
        assert len(a.calls) == 1
        call = a.calls[0]
        assert call.model_id == "cfh_het_arms2_homozygous"
        assert set(call.detail["genotype_calls"]) == {"rs1061170", "rs10490924"}
        assert call.detail["genotype_calls"]["rs1061170"] == "CT"
        assert call.evidence_stars == 3
        # Relative odds must still be paired with absolute-risk context.
        assert "absolute" in call.detail["absolute_risk_context"].lower()

    def test_no_risk_no_finding(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_cfh("TT"), _arms2("GG")])
        a = assess_amd(panel, sample_engine)
        assert a.calls == []


class TestHonestyGuardrails:
    def test_not_clinvar_pathogenic(self, panel, sample_engine: sa.Engine) -> None:
        """Common risk alleles must never write a ClinVar significance."""
        _seed(sample_engine, [_cfh("CC"), _arms2("TT")])
        a = assess_amd(panel, sample_engine)
        store_amd_findings(a, sample_engine)
        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.module == "amd")).fetchone()
        assert row.clinvar_significance is None
        assert row.evidence_level <= 3

    def test_relative_paired_with_absolute(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_cfh("CC"), _arms2("GG")])
        a = assess_amd(panel, sample_engine)
        detail = a.calls[0].detail
        assert detail["odds_ratio"]  # relative
        assert detail["absolute_risk_context"]  # absolute always present
        assert "absolute" in detail["absolute_risk_context"].lower()

    def test_architecture_statement_not_overstated(self) -> None:
        """The 52-variant / ~47% statement must say 'just under half', never 'more than half'."""
        assert "47%" in AMD_DISCLAIMER_TEXT
        assert "just under half" in AMD_DISCLAIMER_TEXT.lower()
        assert "52 variants" in AMD_DISCLAIMER_TEXT
        assert "more than half" not in AMD_DISCLAIMER_TEXT.lower()


class TestStrandAndIndeterminate:
    def test_off_chip_arms2_indeterminate(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_cfh("CC")])  # ARMS2 absent
        a = assess_amd(panel, sample_engine)
        # CFH CC still fires (homozygous), but ARMS2 is flagged indeterminate.
        assert a.calls[0].risk_classification == "CFH Y402H homozygous"
        assert "rs10490924" in a.indeterminate_loci

    def test_off_chip_cfh_indeterminate(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_arms2("TT")])  # CFH absent
        a = assess_amd(panel, sample_engine)
        # ARMS2 TT still fires (homozygous), but CFH is flagged indeterminate.
        assert a.calls[0].risk_classification == "ARMS2 homozygous"
        assert "rs1061170" in a.indeterminate_loci

    def test_both_off_chip_no_false_clear(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [])  # both absent
        a = assess_amd(panel, sample_engine)
        assert a.calls == []
        assert set(a.indeterminate_loci) == {"rs1061170", "rs10490924"}


class TestStorage:
    def test_stored(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_cfh("CC"), _arms2("GG")])
        a = assess_amd(panel, sample_engine)
        assert store_amd_findings(a, sample_engine) == 1
        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.module == "amd")).fetchone()
        assert row.category == "risk_genotype"
        assert row.gene_symbol == "CFH"
        detail = json.loads(row.detail_json)
        assert detail["genotype_calls"]["rs1061170"] == "CC"
