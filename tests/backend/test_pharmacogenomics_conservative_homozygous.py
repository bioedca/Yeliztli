"""Regressions for both reference-filled chromosomes with an untyped marker (#2453).

The production CPIC rows supply every expected classification. Synthetic calls
exercise existing conservative policy; they do not establish a patient's genotype.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.pharmacogenomics import (
    CallConfidence,
    _fetch_alleles_for_gene,
    _infer_conservative_phenotype,
    call_star_alleles_for_gene,
    generate_prescribing_alerts,
    store_prescribing_alerts,
)
from backend.annotation.cpic import load_cpic_from_csvs
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import cpic_diplotypes, cpic_guidelines, findings, reference_metadata

_CPIC = Path(__file__).resolve().parents[2] / "backend/data/cpic"
_CASES = [
    ("DPYD", "*2A", "*2A/*2A", 0.0),
    ("CYP2B6", "*18", "*18/*18", 0.0),
    ("CYP2C9", "*3", "*3/*3", 0.0),
    ("UGT1A1", "*28", "*28/*28", 1.0),
]


@pytest.fixture
def reference_engine():
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    load_cpic_from_csvs(
        _CPIC / "cpic_alleles.csv",
        _CPIC / "cpic_diplotypes.csv",
        _CPIC / "cpic_guidelines.csv",
        engine,
    )
    yield engine
    engine.dispose()


def _call(reference_engine, gene, omitted=(), overrides=None):
    alleles = _fetch_alleles_for_gene(gene, reference_engine)
    assert alleles
    variants = [v for allele in alleles for v in allele["defining_variants"]]
    genotypes = {v["rsid"]: v["ref"] * 2 for v in variants if len(v["ref"]) == len(v["alt"]) == 1}
    assert genotypes
    if gene == "CYP2C9":
        # The existing caller/fixtures use II for the reference state of *6's
        # callable deletion marker. Keep this independent locus constrained.
        genotypes["rs9332131"] = "II"
    for allele in alleles:
        if allele["allele_name"] in omitted:
            for variant in allele["defining_variants"]:
                genotypes.pop(variant["rsid"], None)
    genotypes.update(overrides or {})
    return call_star_alleles_for_gene(gene, alleles, genotypes, reference_engine)


@pytest.mark.parametrize("gene,untyped,diplotype,score", _CASES)
def test_both_reference_fills_consider_the_shipped_homozygote(
    reference_engine, gene, untyped, diplotype, score
):
    result = _call(reference_engine, gene, omitted=[untyped])
    assert result.diplotype == "*1/*1"
    assert result.phenotype == "Normal Metabolizer"
    assert result.call_confidence == CallConfidence.PARTIAL
    assert untyped in result.indeterminate_alleles
    assert result.conservative_diplotype == diplotype
    assert result.conservative_phenotype == "Poor Metabolizer"
    assert result.conservative_activity_score == score

    alerts = generate_prescribing_alerts([result], reference_engine)
    assert alerts
    with reference_engine.connect() as conn:
        expected = dict(
            conn.execute(
                sa.select(cpic_guidelines.c.drug, cpic_guidelines.c.recommendation).where(
                    cpic_guidelines.c.gene == gene,
                    cpic_guidelines.c.phenotype == "Poor Metabolizer",
                    sa.or_(
                        cpic_guidelines.c.activity_score == score,
                        cpic_guidelines.c.activity_score.is_(None),
                    ),
                )
            ).all()
        )
    assert expected
    assert {a.drug: a.recommendation for a in alerts} == expected
    for alert in alerts:
        assert alert.conservative_alert is True
        assert alert.conservative_diplotype == diplotype
        assert alert.phenotype == "Poor Metabolizer"
        assert alert.activity_score == score
        assert alert.called_phenotype == "Normal Metabolizer"
        assert alert.diplotype == "*1/*1"
        assert "could make " + diplotype in alert.confidence_note
    if gene == "DPYD":
        assert {a.drug for a in alerts} == {"fluorouracil", "capecitabine"}
        assert all("avoid" in a.recommendation.lower() for a in alerts)


@pytest.mark.parametrize("gene,untyped,diplotype,score", _CASES)
def test_stored_alert_preserves_direct_call_and_conservative_basis(
    reference_engine, gene, untyped, diplotype, score
):
    result = _call(reference_engine, gene, omitted=[untyped])
    alerts = generate_prescribing_alerts([result], reference_engine)
    assert alerts
    sample = sa.create_engine("sqlite://")
    try:
        create_sample_tables(sample)
        assert store_prescribing_alerts(alerts, sample) == len(alerts)
        with sample.connect() as conn:
            rows = conn.execute(
                sa.select(findings.c.metabolizer_status, findings.c.detail_json).where(
                    findings.c.module == "pharmacogenomics",
                    findings.c.category == "prescribing_alert",
                    findings.c.gene_symbol == gene,
                )
            ).all()
        assert len(rows) == len(alerts)
        for row in rows:
            detail = json.loads(row.detail_json)
            assert row.metabolizer_status == "Poor Metabolizer"
            assert detail["called_phenotype"] == "Normal Metabolizer"
            assert detail["conservative_diplotype"] == diplotype
            assert detail["conservative_activity_score"] == score
            assert detail["conservative_alert"] is True
    finally:
        sample.dispose()


@pytest.mark.parametrize("gene", ["DPYD", "CYP2B6", "CYP2C9"])
def test_fully_assayed_hom_ref_control_keeps_the_direct_call(reference_engine, gene):
    result = _call(reference_engine, gene)
    assert result.diplotype == "*1/*1"
    assert result.phenotype == "Normal Metabolizer"
    assert not result.indeterminate_alleles
    assert result.conservative_diplotype is None
    alerts = generate_prescribing_alerts([result], reference_engine)
    assert alerts
    assert all(a.phenotype == "Normal Metabolizer" and not a.conservative_alert for a in alerts)


def test_one_reference_fill_cannot_replace_the_observed_allele(reference_engine):
    result = _call(reference_engine, "DPYD", omitted=["*2A"], overrides={"rs67376798": "TA"})
    assert result.diplotype == "*1/c.2846A>T"
    assert result.conservative_diplotype == "*2A/c.2846A>T"
    assert result.conservative_activity_score == 0.5
    assert result.conservative_diplotype != "*2A/*2A"


def test_two_observed_alleles_cannot_be_replaced(reference_engine):
    result = _call(reference_engine, "DPYD", omitted=["*2A"], overrides={"rs67376798": "AA"})
    assert result.diplotype == "c.2846A>T/c.2846A>T"
    assert result.conservative_diplotype is None


def test_missing_homozygote_row_does_not_synthesize_a_score(reference_engine):
    with reference_engine.begin() as conn:
        conn.execute(
            cpic_diplotypes.delete().where(
                cpic_diplotypes.c.gene == "DPYD", cpic_diplotypes.c.diplotype == "*2A/*2A"
            )
        )
    result = _call(reference_engine, "DPYD", omitted=["*2A"])
    assert result.conservative_diplotype == "*1/*2A"
    assert result.conservative_activity_score == 1.0
    assert result.conservative_phenotype == "Intermediate Metabolizer"


def test_tied_candidates_have_stable_selection(reference_engine):
    result = _call(reference_engine, "DPYD", omitted=["*2A", "*13"])
    assert result.conservative_diplotype == "*13/*13"
    assert result.conservative_activity_score == 0.0
    reverse = replace(result, indeterminate_alleles=list(reversed(result.indeterminate_alleles)))
    assert _infer_conservative_phenotype(
        reverse, reference_engine
    ) == _infer_conservative_phenotype(result, reference_engine)


def test_tied_compounds_preserve_the_observed_allele(reference_engine):
    result = _call(
        reference_engine,
        "CYP2C9",
        omitted=["*3", "*6"],
        overrides={"rs1799853": "CT"},
    )
    assert result.diplotype == "*1/*2"
    assert result.indeterminate_alleles == ["*3", "*6"]
    assert result.conservative_diplotype == "*2/*3"
    assert result.conservative_activity_score == 0.5
    reverse = replace(result, indeterminate_alleles=list(reversed(result.indeterminate_alleles)))
    assert _infer_conservative_phenotype(
        reverse, reference_engine
    ) == _infer_conservative_phenotype(result, reference_engine)


def test_same_phenotype_band_keeps_the_lowest_supported_score(reference_engine):
    result = _call(reference_engine, "DPYD", omitted=["c.557A>G"])
    assert result.conservative_diplotype == "c.557A>G/c.557A>G"
    assert result.conservative_phenotype == "Intermediate Metabolizer"
    assert result.conservative_activity_score == 1.0
    alerts = generate_prescribing_alerts([result], reference_engine)
    assert {a.drug for a in alerts} == {"fluorouracil", "capecitabine"}
    assert all(a.activity_score == 1.0 for a in alerts)
    assert all("Reduce starting dose by 50%" in a.recommendation for a in alerts)
