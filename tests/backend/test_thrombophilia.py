"""Tests for the inherited thrombophilia module (FVL + Prothrombin).

Validates strand-harmonized calling (rs6025 / rs1799963 are minus-strand
cross-vendor pitfalls), the relative+absolute risk framing, the carriage gate,
and indeterminate handling — all from synthetic genotypes seeded into a real
sample DB.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend.analysis.thrombophilia import (
    assess_thrombophilia,
    load_thrombophilia_panel,
    store_thrombophilia_findings,
)
from backend.db.tables import findings, raw_variants


@pytest.fixture()
def panel():
    return load_thrombophilia_panel()


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), rows)


def _fvl(genotype: str) -> dict:
    return {"rsid": "rs6025", "chrom": "1", "pos": 169549811, "genotype": genotype}


def _f2(genotype: str) -> dict:
    return {"rsid": "rs1799963", "chrom": "11", "pos": 46761055, "genotype": genotype}


class TestFactorVLeiden:
    def test_fvl_heterozygous_relative_and_absolute(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_fvl("GA"), _f2("GG")])
        a = assess_thrombophilia(panel, sample_engine)
        assert len(a.calls) == 1
        call = a.calls[0]
        assert call.risk_classification == "Factor V Leiden heterozygous"
        # Relative AND absolute risk must both be present.
        assert "3–5×" in call.finding_text or "3-5" in call.finding_text
        assert "10%" in call.finding_text  # absolute-risk framing
        assert call.detail["absolute_risk_context"]

    def test_fvl_minus_strand_calls_identical(self, panel, sample_engine: sa.Engine) -> None:
        """rs6025 reported on the minus strand ('TC') must call the same as 'GA'."""
        _seed(sample_engine, [_fvl("TC"), _f2("GG")])
        a = assess_thrombophilia(panel, sample_engine)
        assert len(a.calls) == 1
        assert a.calls[0].risk_classification == "Factor V Leiden heterozygous"

    def test_fvl_homozygous(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_fvl("AA"), _f2("GG")])
        a = assess_thrombophilia(panel, sample_engine)
        assert a.calls[0].risk_classification == "Factor V Leiden homozygous"
        assert a.calls[0].zygosity == "hom_alt"


class TestProthrombin:
    def test_f2_heterozygous(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_fvl("GG"), _f2("GA")])
        a = assess_thrombophilia(panel, sample_engine)
        assert a.calls[0].risk_classification == "Prothrombin G20210A heterozygous"


class TestDoubleCarrier:
    def test_double_carrier_or_524(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_fvl("GA"), _f2("GA")])
        a = assess_thrombophilia(panel, sample_engine)
        assert len(a.calls) == 1  # single combined headline finding
        call = a.calls[0]
        assert "double carrier" in call.risk_classification.lower()
        assert "5.24" in call.finding_text
        # A true double *heterozygote* is het at both loci.
        assert call.zygosity == "het"


class TestCompoundHomozygous:
    """Regression for #114 — the broad first-match double-carrier rule must not
    shadow homozygous FVL/F2 context. A homozygous locus paired with a second
    risk allele has to resolve to its genotype-specific compound model, not the
    generic ``het`` double-carrier headline."""

    def test_fvl_homozygous_plus_f2_carrier(self, panel, sample_engine: sa.Engine) -> None:
        # rs6025 AA (homozygous FVL) + rs1799963 GA (PT carrier) — the #114 case.
        _seed(sample_engine, [_fvl("AA"), _f2("GA")])
        a = assess_thrombophilia(panel, sample_engine)
        assert len(a.calls) == 1
        call = a.calls[0]
        assert call.detail["model_id"] == "fvl_homozygous_f2_carrier"
        assert call.risk_classification == (
            "Factor V Leiden homozygous + Prothrombin G20210A carrier"
        )
        # Must NOT be reported as a plain heterozygous double carrier.
        assert call.zygosity != "het"
        assert "homozygous" in call.finding_text.lower()
        assert call.detail["dosages"] == {"rs6025": 2, "rs1799963": 1}

    def test_f2_homozygous_plus_fvl_carrier(self, panel, sample_engine: sa.Engine) -> None:
        # rs1799963 AA (homozygous PT) + rs6025 GA (FVL carrier).
        _seed(sample_engine, [_fvl("GA"), _f2("AA")])
        a = assess_thrombophilia(panel, sample_engine)
        assert len(a.calls) == 1
        call = a.calls[0]
        assert call.detail["model_id"] == "f2_homozygous_fvl_carrier"
        assert call.risk_classification == (
            "Prothrombin G20210A homozygous + Factor V Leiden carrier"
        )
        assert call.zygosity != "het"
        assert "homozygous" in call.finding_text.lower()
        assert call.detail["dosages"] == {"rs6025": 1, "rs1799963": 2}

    def test_double_homozygous(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_fvl("AA"), _f2("AA")])
        a = assess_thrombophilia(panel, sample_engine)
        assert len(a.calls) == 1
        call = a.calls[0]
        assert call.detail["model_id"] == "double_homozygous"
        assert call.zygosity == "hom_alt"
        assert "double-homozygous" in call.finding_text.lower()
        assert call.detail["dosages"] == {"rs6025": 2, "rs1799963": 2}

    def test_single_homozygous_loci_unchanged(self, panel, sample_engine: sa.Engine) -> None:
        # A homozygote with the *other* locus reference must still hit the
        # single-locus homozygous model, not a compound one.
        _seed(sample_engine, [_fvl("AA"), _f2("GG")])
        a = assess_thrombophilia(panel, sample_engine)
        assert a.calls[0].detail["model_id"] == "fvl_homozygous"
        assert a.calls[0].zygosity == "hom_alt"


class TestNegativeAndIndeterminate:
    def test_both_reference_no_finding(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_fvl("GG"), _f2("GG")])
        a = assess_thrombophilia(panel, sample_engine)
        assert a.calls == []

    def test_off_chip_f2_indeterminate_fvl_unaffected(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # F2 absent (off-chip); FVL het still called.
        _seed(sample_engine, [_fvl("GA")])
        a = assess_thrombophilia(panel, sample_engine)
        assert a.calls[0].risk_classification == "Factor V Leiden heterozygous"
        assert "rs1799963" in a.indeterminate_loci


class TestPanelGuard:
    def test_all_models_have_absolute_risk(self, panel) -> None:
        """Every thrombophilia model sets odds_ratio, so each must carry an
        absolute_risk_context (enforced at load time)."""
        for model in panel.genotype_models:
            assert model.absolute_risk_context, model.id


class TestStorage:
    def test_stored_with_module_and_category(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_fvl("GA"), _f2("GA")])
        a = assess_thrombophilia(panel, sample_engine)
        count = store_thrombophilia_findings(a, sample_engine)
        assert count == 1
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.module == "thrombophilia")
            ).fetchone()
        assert row.category == "risk_genotype"
        assert row.clinvar_significance is None
        detail = json.loads(row.detail_json)
        assert detail["odds_ratio"]
