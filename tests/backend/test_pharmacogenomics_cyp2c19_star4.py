"""CYP2C19*4 production-CSV-backed phenotype regression (issue #19).

These tests load the real CPIC CSVs so they validate the shipped
diplotype-to-phenotype mappings consumed by pharmacogenomics calling and
prescribing-alert generation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.pharmacogenomics import (
    CallConfidence,
    _fetch_alleles_for_gene,
    _fetch_diplotype_phenotype,
    call_all_star_alleles,
    call_star_alleles_for_gene,
    generate_prescribing_alerts,
)
from backend.annotation.cpic import load_cpic_from_csvs
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants, reference_metadata

_CPIC_DIR = Path(__file__).resolve().parents[2] / "backend" / "data" / "cpic"

# CYP2C19 defining variants on the GRCh37 plus strand. Positions are realistic
# but not load-bearing; star-allele calling is keyed on rsid.
_CYP2C19 = {
    "rs4244285": ("10", 96541616, "G", "A"),  # *2  no function
    "rs4986893": ("10", 96540410, "G", "A"),  # *3  no function
    "rs28399504": ("10", 96522463, "A", "G"),  # *4  no function
    "rs12248560": ("10", 96521657, "C", "T"),  # *17 increased function
}

_STAR4_DIPLOTYPES = {
    "*1/*4": ("Intermediate Metabolizer", 1.0),
    "*2/*4": ("Poor Metabolizer", 0.0),
    "*3/*4": ("Poor Metabolizer", 0.0),
    "*4/*4": ("Poor Metabolizer", 0.0),
    "*4/*17": ("Intermediate Metabolizer", 1.5),
}

_ISSUE59_STAR3_DIPLOTYPES = {
    "*3/*3": ("Poor Metabolizer", 0.0),
    "*3/*17": ("Intermediate Metabolizer", 1.5),
}


@pytest.fixture(scope="module")
def reference_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    load_cpic_from_csvs(
        _CPIC_DIR / "cpic_alleles.csv",
        _CPIC_DIR / "cpic_diplotypes.csv",
        _CPIC_DIR / "cpic_guidelines.csv",
        engine,
    )
    return engine


def _cyp2c19_genotypes(**overrides: str) -> dict[str, str]:
    geno = {rsid: ref * 2 for rsid, (_chrom, _pos, ref, _alt) in _CYP2C19.items()}
    geno.update(overrides)
    return geno


def _call_cyp2c19(reference_engine: sa.Engine, genotypes: dict[str, str]):
    alleles = _fetch_alleles_for_gene("CYP2C19", reference_engine)
    return call_star_alleles_for_gene("CYP2C19", alleles, genotypes, reference_engine)


def _make_sample(genotypes: dict[str, str]) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    rows = [
        {"rsid": rsid, "chrom": _CYP2C19[rsid][0], "pos": _CYP2C19[rsid][1], "genotype": g}
        for rsid, g in genotypes.items()
    ]
    with engine.begin() as conn:
        conn.execute(raw_variants.insert(), rows)
    return engine


@pytest.mark.parametrize(("diplotype", "expected"), sorted(_STAR4_DIPLOTYPES.items()))
def test_star4_diplotype_rows_resolve_to_expected_phenotype(
    reference_engine: sa.Engine, diplotype: str, expected: tuple[str, float]
) -> None:
    expected_phenotype, expected_activity = expected

    row = _fetch_diplotype_phenotype("CYP2C19", diplotype, reference_engine)

    assert row is not None, f"CYP2C19 {diplotype} has no diplotype-to-phenotype row"
    assert row["phenotype"] == expected_phenotype
    assert row["activity_score"] == expected_activity
    assert row["ehr_notation"] == f"CYP2C19 {expected_phenotype}"


@pytest.mark.parametrize(("diplotype", "expected"), sorted(_ISSUE59_STAR3_DIPLOTYPES.items()))
def test_issue59_star3_gap_diplotype_rows_resolve_to_expected_phenotype(
    reference_engine: sa.Engine, diplotype: str, expected: tuple[str, float]
) -> None:
    """CYP2C19 *3 callable diplotypes must not resolve to phenotype=None."""
    expected_phenotype, expected_activity = expected

    row = _fetch_diplotype_phenotype("CYP2C19", diplotype, reference_engine)

    assert row is not None, f"CYP2C19 {diplotype} has no diplotype-to-phenotype row"
    assert row["phenotype"] == expected_phenotype
    assert row["activity_score"] == expected_activity
    assert row["ehr_notation"] == f"CYP2C19 {expected_phenotype}"


@pytest.mark.parametrize(
    ("overrides", "expected_diplotype", "expected_phenotype", "expected_activity"),
    [
        ({"rs28399504": "AG"}, "*1/*4", "Intermediate Metabolizer", 1.0),
        ({"rs4244285": "GA", "rs28399504": "AG"}, "*2/*4", "Poor Metabolizer", 0.0),
        ({"rs4986893": "GA", "rs28399504": "AG"}, "*3/*4", "Poor Metabolizer", 0.0),
        ({"rs28399504": "GG"}, "*4/*4", "Poor Metabolizer", 0.0),
        (
            {"rs28399504": "AG", "rs12248560": "CT"},
            "*4/*17",
            "Intermediate Metabolizer",
            1.5,
        ),
    ],
)
def test_star4_calls_resolve_to_cpic_phenotypes(
    reference_engine: sa.Engine,
    overrides: dict[str, str],
    expected_diplotype: str,
    expected_phenotype: str,
    expected_activity: float,
) -> None:
    result = _call_cyp2c19(reference_engine, _cyp2c19_genotypes(**overrides))

    assert result.diplotype == expected_diplotype
    assert result.phenotype == expected_phenotype
    assert result.activity_score == expected_activity
    assert result.call_confidence == CallConfidence.COMPLETE


@pytest.mark.parametrize(
    ("overrides", "expected_diplotype", "expected_phenotype", "expected_activity"),
    [
        ({"rs4986893": "AA"}, "*3/*3", "Poor Metabolizer", 0.0),
        (
            {"rs4986893": "GA", "rs12248560": "CT"},
            "*3/*17",
            "Intermediate Metabolizer",
            1.5,
        ),
    ],
)
def test_issue59_star3_gap_calls_resolve_to_cpic_phenotypes(
    reference_engine: sa.Engine,
    overrides: dict[str, str],
    expected_diplotype: str,
    expected_phenotype: str,
    expected_activity: float,
) -> None:
    result = _call_cyp2c19(reference_engine, _cyp2c19_genotypes(**overrides))

    assert result.diplotype == expected_diplotype
    assert result.phenotype == expected_phenotype
    assert result.activity_score == expected_activity
    assert result.call_confidence == CallConfidence.COMPLETE


def test_star1_star1_emits_only_normal_metabolizer_guidance(
    reference_engine: sa.Engine,
) -> None:
    sample = _make_sample(_cyp2c19_genotypes())

    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"CYP2C19"}))
    alerts = generate_prescribing_alerts(results, reference_engine)

    cyp2c19_alerts = [a for a in alerts if a.gene == "CYP2C19"]
    assert cyp2c19_alerts, "expected CYP2C19 alerts for *1/*1 Normal Metabolizer"
    assert {a.drug for a in cyp2c19_alerts} == {"clopidogrel", "voriconazole"}
    for alert in cyp2c19_alerts:
        assert alert.diplotype == "*1/*1"
        assert alert.phenotype == "Normal Metabolizer"
        assert alert.recommendation == "Use label-recommended dosing."


def test_star1_star4_emits_clopidogrel_and_voriconazole_alerts(
    reference_engine: sa.Engine,
) -> None:
    sample = _make_sample(_cyp2c19_genotypes(rs28399504="AG"))

    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"CYP2C19"}))
    alerts = generate_prescribing_alerts(results, reference_engine)

    cyp2c19_alerts = [a for a in alerts if a.gene == "CYP2C19"]
    assert cyp2c19_alerts, "expected CYP2C19 alerts for *1/*4 Intermediate Metabolizer"
    # voriconazole IM coverage added in issue #23 (CPIC: standard dosing + TDM),
    # closing the silent gap where IM got a clopidogrel alert but no voriconazole one.
    assert {a.drug for a in cyp2c19_alerts} == {"clopidogrel", "voriconazole"}
    for alert in cyp2c19_alerts:
        assert alert.diplotype == "*1/*4"
        assert alert.phenotype == "Intermediate Metabolizer"


def test_star4_poor_metabolizer_emits_clopidogrel_and_voriconazole_alerts(
    reference_engine: sa.Engine,
) -> None:
    sample = _make_sample(_cyp2c19_genotypes(rs28399504="GG"))

    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"CYP2C19"}))
    alerts = generate_prescribing_alerts(results, reference_engine)

    cyp2c19_alerts = [a for a in alerts if a.gene == "CYP2C19"]
    assert cyp2c19_alerts, "expected CYP2C19 alerts for *4/*4 Poor Metabolizer"
    assert {a.drug for a in cyp2c19_alerts} == {"clopidogrel", "voriconazole"}
    for alert in cyp2c19_alerts:
        assert alert.diplotype == "*4/*4"
        assert alert.phenotype == "Poor Metabolizer"
