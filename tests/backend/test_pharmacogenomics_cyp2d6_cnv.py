"""CYP2D6 structural-variant / copy-number guardrails (SW-E3).

CYP2D6 is the canonical structural-variant pharmacogene: whole-gene
duplications/multiplications, the CYP2D6*5 deletion, and CYP2D6-CYP2D7 hybrids
cannot be resolved from SNP-array data. These tests load the REAL production CPIC
tables and assert that every CYP2D6 prescribing alert carries the copy-number
caveat (context only — it never changes the metabolizer status), and that the
call retains "Partial" confidence via STRUCTURAL_VARIANT_GENES.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.pharmacogenomics import (
    STRUCTURAL_UNCALLABLE_ALLELES,
    STRUCTURAL_VARIANT_GENES,
    CallConfidence,
    call_all_star_alleles,
    generate_prescribing_alerts,
    store_prescribing_alerts,
)
from backend.annotation.cpic import load_cpic_from_csvs
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings, raw_variants, reference_metadata
from backend.disclaimers import CYP2D6_CNV_CAVEAT

_CPIC_DIR = Path(__file__).resolve().parents[2] / "backend" / "data" / "cpic"

# CYP2D6 SNV defining variants on the GRCh37 plus strand (matches cpic_alleles.csv).
# rsid -> (chrom, pos, ref, alt). The *3/*6/*9 indel rsids are intentionally
# omitted (array data cannot call them; they stay "missing" but < 50%).
_CYP2D6 = {
    "rs16947": ("22", 42523943, "G", "A"),  # *2
    "rs3892097": ("22", 42524947, "C", "T"),  # *4  No function
    "rs1065852": ("22", 42526694, "G", "A"),  # *10
    "rs28371706": ("22", 42525772, "G", "A"),  # *17
    "rs59421388": ("22", 42523610, "C", "T"),  # *29
    "rs28371725": ("22", 42523805, "C", "T"),  # *41
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


def _sample(**overrides: str) -> sa.Engine:
    """CYP2D6 sample; defaults to homozygous reference, override per rsid."""
    geno = {rsid: ref * 2 for rsid, (_c, _p, ref, _a) in _CYP2D6.items()}
    geno.update(overrides)
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    rows = [
        {"rsid": rsid, "chrom": _CYP2D6[rsid][0], "pos": _CYP2D6[rsid][1], "genotype": g}
        for rsid, g in geno.items()
    ]
    with engine.begin() as conn:
        conn.execute(raw_variants.insert(), rows)
    return engine


def test_cyp2d6_is_a_structural_variant_gene() -> None:
    assert "CYP2D6" in STRUCTURAL_VARIANT_GENES
    assert STRUCTURAL_UNCALLABLE_ALLELES["CYP2D6"] == ("*5",)


def test_cyp2d6_star5_is_structurally_indeterminate_not_reference(
    reference_engine: sa.Engine,
) -> None:
    """A reference-looking array call cannot exclude the CYP2D6*5 deletion."""
    sample = _sample()
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"CYP2D6"}))
    (result,) = results

    assert result.diplotype == "*1/*1"
    assert result.phenotype == "Normal Metabolizer"
    assert result.call_confidence == CallConfidence.PARTIAL
    assert "*5" not in result.diplotype
    assert "*5" in result.indeterminate_alleles
    assert "Cannot exclude" in result.confidence_note
    assert "*5" in result.confidence_note
    assert "copy-number" in result.confidence_note or "copy number" in result.confidence_note


def test_cyp2d6_alert_carries_cnv_caveat(reference_engine: sa.Engine) -> None:
    """A *1/*4 CYP2D6 call (rs3892097 het) produces an alert with the CNV caveat."""
    sample = _sample(rs3892097="CT")  # *1/*4 Intermediate Metabolizer
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"CYP2D6"}))
    (result,) = results
    assert result.diplotype == "*1/*4"
    # Structural-variant gene is always Partial (CNV cannot be excluded), never Complete.
    assert result.call_confidence == CallConfidence.PARTIAL
    assert "*5" in result.indeterminate_alleles

    alerts = generate_prescribing_alerts(results, reference_engine)
    assert alerts, "expected CYP2D6 prescribing alerts (e.g. codeine)"
    assert all("*5" in alert.indeterminate_alleles for alert in alerts)
    store_prescribing_alerts(alerts, sample)

    with sample.connect() as conn:
        rows = conn.execute(
            sa.select(findings.c.detail_json, findings.c.metabolizer_status).where(
                findings.c.gene_symbol == "CYP2D6"
            )
        ).fetchall()
    assert rows
    for detail_json, metabolizer in rows:
        detail = json.loads(detail_json)
        caveat = detail.get("gene_caveat")
        assert caveat == CYP2D6_CNV_CAVEAT
        assert "*5" in detail["indeterminate_alleles"]
        assert "Cannot exclude" in detail["confidence_note"]
        assert "*5" in detail["confidence_note"]
        lower = caveat.lower()
        assert "copy-number" in lower or "copy number" in lower
        assert "duplicat" in lower and "deletion" in lower
        # Context only: caveat must not have altered the metabolizer status.
        assert metabolizer == "Intermediate Metabolizer"


def test_cyp2d6_caveat_directional_band_text() -> None:
    """The caveat conveys the activity-score band: higher (UM) or lower (PM)."""
    text = CYP2D6_CNV_CAVEAT.lower()
    assert "ultrarapid" in text  # functional-allele duplication -> higher activity
    assert "poor metabolizer" in text  # *5 gene deletion -> lower activity
    assert "*5" in text and "cyp2d7" in text  # deletion + hybrid alleles named
