"""Tests for the hereditary haemochromatosis (HFE) module.

Seeds synthetic genotypes into a real sample DB and asserts the produced
findings: genotype-combination classification, sex-stratified penetrance, the
carriage/negative gate, and indeterminate handling for off-chip / no-call probes.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend.analysis.hemochromatosis import (
    assess_hemochromatosis,
    load_hemochromatosis_panel,
    store_hemochromatosis_findings,
)
from backend.db.tables import findings, raw_variants


@pytest.fixture()
def panel():
    return load_hemochromatosis_panel()


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), rows)


def _xy_chr_rows() -> list[dict]:
    """chrX non-PAR homozygous + chrY typed → infer_biological_sex == 'XY'."""
    return [
        {"rsid": "rsX1", "chrom": "X", "pos": 50_000_000, "genotype": "GG"},
        {"rsid": "rsY1", "chrom": "Y", "pos": 2_700_000, "genotype": "GG"},
    ]


class TestC282YHomozygous:
    def test_homozygous_finding_sex_indeterminate(self, panel, sample_engine: sa.Engine) -> None:
        # Only the HFE SNP seeded → no chrX/chrY → sex indeterminate → both
        # penetrance figures shown.
        _seed(
            sample_engine, [{"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "AA"}]
        )
        a = assess_hemochromatosis(panel, sample_engine)

        assert len(a.calls) == 1
        call = a.calls[0]
        assert call.risk_classification == "C282Y homozygous"
        assert call.zygosity == "hom_alt"
        assert call.evidence_stars == 3
        assert "56.4%" in call.finding_text
        assert "40.5%" in call.finding_text

    def test_homozygous_sex_stratified_male(self, panel, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine,
            [
                {"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "AA"},
                *_xy_chr_rows(),
            ],
        )
        a = assess_hemochromatosis(panel, sample_engine)
        assert a.sex_used == "XY"
        assert a.calls[0].detail["sex_used"] == "XY"
        assert "56.4%" in a.calls[0].finding_text  # male figure emphasised


class TestNegativeAndCombinations:
    def test_homozygous_reference_no_finding(self, panel, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine,
            [
                {"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "GG"},
                {"rsid": "rs1799945", "chrom": "6", "pos": 26091179, "genotype": "CC"},
            ],
        )
        a = assess_hemochromatosis(panel, sample_engine)
        assert a.calls == []  # carriage/negative gate
        assert a.indeterminate_loci == []  # both typed, just reference

    def test_compound_heterozygous(self, panel, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine,
            [
                {"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "AG"},
                {"rsid": "rs1799945", "chrom": "6", "pos": 26091179, "genotype": "CG"},
            ],
        )
        a = assess_hemochromatosis(panel, sample_engine)
        assert len(a.calls) == 1
        assert (
            a.calls[0].risk_classification == "Compound heterozygous (C282Y/H63D), phase-inferred"
        )
        assert a.calls[0].evidence_stars == 2
        assert (
            "low-penetrance" in a.calls[0].finding_text.lower()
            or "low penetrance" in a.calls[0].finding_text.lower()
        )

    def test_compound_heterozygous_is_phase_inferred(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        """Unphased C282Y/H63D het+het cannot prove trans, so the call is phase-inferred.

        SNP-array genotypes are unphased; the same observed genotype arises whether
        C282Y and H63D are in trans (true compound het) or the rare cis configuration
        (documented by family genotyping, Best et al. 2001, PMID 11531973). The call
        must carry a phase-inference caveat rather than a definitive label.
        Regression for issue #101.
        """
        _seed(
            sample_engine,
            [
                {"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "AG"},
                {"rsid": "rs1799945", "chrom": "6", "pos": 26091179, "genotype": "CG"},
            ],
        )
        a = assess_hemochromatosis(panel, sample_engine)
        assert len(a.calls) == 1
        call = a.calls[0]
        # The classification label alone is qualified — a consumer reading only
        # risk_classification must not see an unqualified compound-het call.
        assert "phase-inferred" in call.risk_classification.lower()
        # Phase caveat travels with the user-facing finding text…
        assert "phase-inferred" in call.finding_text.lower()
        assert "trans" in call.finding_text.lower()
        # …and is present as a structured caveat in the detail JSON.
        caveats_text = " ".join(call.detail["caveats"]).lower()
        assert "unphased" in caveats_text
        assert "cis" in caveats_text

    def test_c282y_single_heterozygote(self, panel, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine,
            [
                {"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "AG"},
                {"rsid": "rs1799945", "chrom": "6", "pos": 26091179, "genotype": "CC"},
            ],
        )
        a = assess_hemochromatosis(panel, sample_engine)
        assert len(a.calls) == 1
        assert a.calls[0].risk_classification == "C282Y heterozygous (carrier)"
        assert a.calls[0].evidence_stars == 2


class TestIndeterminate:
    def test_off_chip_c282y_is_indeterminate(self, panel, sample_engine: sa.Engine) -> None:
        # rs1800562 absent (off-chip); H63D hom-ref → no positive finding, and
        # C282Y must be flagged indeterminate, not a false "clear".
        _seed(
            sample_engine, [{"rsid": "rs1799945", "chrom": "6", "pos": 26091179, "genotype": "CC"}]
        )
        a = assess_hemochromatosis(panel, sample_engine)
        assert a.calls == []
        assert "rs1800562" in a.indeterminate_loci

    def test_no_call_c282y_is_indeterminate(self, panel, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine, [{"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "--"}]
        )
        a = assess_hemochromatosis(panel, sample_engine)
        assert a.calls == []
        assert "rs1800562" in a.indeterminate_loci


class TestStorage:
    def test_findings_stored_with_module_and_category(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        _seed(
            sample_engine, [{"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "AA"}]
        )
        a = assess_hemochromatosis(panel, sample_engine)
        count = store_hemochromatosis_findings(a, sample_engine)
        assert count == 1

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.module == "hemochromatosis")
            ).fetchone()
        assert row.module == "hemochromatosis"
        assert row.category == "risk_genotype"
        assert row.gene_symbol == "HFE"
        assert row.clinvar_significance is None
        detail = json.loads(row.detail_json)
        assert detail["genotype_calls"]["rs1800562"] == "AA"
        assert "rs1799945" in detail["indeterminate_loci"]  # H63D not seeded

    def test_rerun_idempotent(self, panel, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine, [{"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "AA"}]
        )
        a = assess_hemochromatosis(panel, sample_engine)
        store_hemochromatosis_findings(a, sample_engine)
        store_hemochromatosis_findings(a, sample_engine)
        with sample_engine.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "hemochromatosis")
            ).scalar()
        assert count == 1
