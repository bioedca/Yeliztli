"""TPMT thiopurine panel — production-CSV-backed regression for *1/*3B (issue #5).

These tests load the REAL production CPIC tables (``backend/data/cpic/*.csv``)
rather than a hand-built in-memory fixture, so they validate the shipped
diplotype→phenotype mapping that the pharmacogenomics caller and the
prescribing-alert generator actually consume in production.

Regression guard for issue #5: ``cpic_alleles.csv`` defines TPMT*3B as a
no-function allele (rs1800460, plus-strand C>T), so a sample heterozygous at
rs1800460 with reference rs1142345 is called ``*1/*3B``. Before the fix,
``cpic_diplotypes.csv`` had no ``TPMT,*1/*3B`` row, so the phenotype resolved to
``None`` and ``generate_prescribing_alerts`` silently skipped thiopurine
(azathioprine / mercaptopurine) guidance for that Intermediate Metabolizer — the
same class of "dropped diplotype" defect fixed for DPYD in SW-E5. All genotypes
below are GRCh37 plus/forward strand (as real 23andMe data is); star-allele
calling is keyed on rsid, so the chrom/pos are realistic but not load-bearing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.pharmacogenomics import (
    CallConfidence,
    _fetch_alleles_for_gene,
    call_all_star_alleles,
    call_star_alleles_for_gene,
    generate_prescribing_alerts,
)
from backend.annotation.cpic import load_cpic_from_csvs
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants, reference_metadata

_CPIC_DIR = Path(__file__).resolve().parents[2] / "backend" / "data" / "cpic"

# TPMT defining variants on the GRCh37 plus strand (matches cpic_alleles.csv and
# the strand guard in test_cpic_allele_strand.py). rsid -> (chrom, pos, ref, alt);
# TPMT is minus-strand, so alt is the plus-strand base a carrier of the allele has.
_TPMT = {
    "rs1800462": ("6", 18139228, "C", "G"),  # *2   c.238G>C  No function
    "rs1800460": ("6", 18130918, "C", "T"),  # *3B  c.460G>A  No function
    "rs1142345": ("6", 18130687, "T", "C"),  # *3C  c.719A>G  No function
}


def _tpmt_genotypes(**overrides: str) -> dict[str, str]:
    """Plus-strand TPMT genotypes; defaults to homozygous reference (*1/*1).

    Pass e.g. rs1800460="CT" to make that locus heterozygous-variant.
    """
    geno = {rsid: ref * 2 for rsid, (_c, _p, ref, _a) in _TPMT.items()}
    geno.update(overrides)
    return geno


@pytest.fixture(scope="module")
def reference_engine() -> sa.Engine:
    """Reference engine loaded from the real production CPIC CSVs."""
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    load_cpic_from_csvs(
        _CPIC_DIR / "cpic_alleles.csv",
        _CPIC_DIR / "cpic_diplotypes.csv",
        _CPIC_DIR / "cpic_guidelines.csv",
        engine,
    )
    return engine


def _call_tpmt(reference_engine: sa.Engine, genotypes: dict[str, str]):
    alleles = _fetch_alleles_for_gene("TPMT", reference_engine)
    return call_star_alleles_for_gene("TPMT", alleles, genotypes, reference_engine)


def _make_sample(genotypes: dict[str, str]) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    rows = [
        {"rsid": rsid, "chrom": _TPMT[rsid][0], "pos": _TPMT[rsid][1], "genotype": g}
        for rsid, g in genotypes.items()
    ]
    with engine.begin() as conn:
        conn.execute(raw_variants.insert(), rows)
    return engine


def test_reference_is_normal_metabolizer(reference_engine: sa.Engine) -> None:
    """A plus-strand homozygous-reference TPMT sample is *1/*1 Normal."""
    result = _call_tpmt(reference_engine, _tpmt_genotypes())
    assert result.diplotype == "*1/*1"
    assert result.phenotype == "Normal Metabolizer"
    assert result.call_confidence == CallConfidence.COMPLETE


def test_star1_star3b_is_intermediate_metabolizer(reference_engine: sa.Engine) -> None:
    """Het rs1800460 / ref rs1142345 -> *1/*3B -> Intermediate Metabolizer (issue #5).

    Before adding the ``TPMT,*1/*3B`` row to the production cpic_diplotypes.csv,
    this resolved to phenotype=None despite *3B being a defined no-function allele.
    """
    result = _call_tpmt(reference_engine, _tpmt_genotypes(rs1800460="CT"))
    assert result.diplotype == "*1/*3B"
    assert result.phenotype == "Intermediate Metabolizer"
    assert result.activity_score == 1.0
    assert result.call_confidence == CallConfidence.COMPLETE


def test_star1_star3b_emits_thiopurine_alerts(reference_engine: sa.Engine) -> None:
    """A *1/*3B Intermediate Metabolizer gets azathioprine + mercaptopurine alerts.

    This is the end-to-end patient-safety guard: the missing diplotype row
    previously caused ``generate_prescribing_alerts`` to skip this gene entirely.
    """
    sample = _make_sample(_tpmt_genotypes(rs1800460="CT"))
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"TPMT"}))
    alerts = generate_prescribing_alerts(results, reference_engine)

    tpmt_alerts = [a for a in alerts if a.gene == "TPMT"]
    assert tpmt_alerts, "expected TPMT prescribing alerts for an Intermediate Metabolizer"
    drugs = {a.drug for a in tpmt_alerts}
    assert {"azathioprine", "mercaptopurine"} <= drugs
    for alert in tpmt_alerts:
        assert alert.diplotype == "*1/*3B"
        assert alert.phenotype == "Intermediate Metabolizer"
