"""SW-E1b — NAT2 acetylator + CYP2B6 efavirenz, production-CSV-backed.

NAT2 uses the Hein-2011 4-SNP panel (rs1801279/*14, rs1801280/*5, rs1799930/*6,
rs1799931/*7; *4 = rapid reference), which infers acetylator phenotype at ~98%
accuracy and avoids the strand-trap rs1208. CYP2B6 *6/*9 (rs3745274 +/- rs2279343)
drives efavirenz exposure; CYP2B6 is a structural-variant gene, so calls are
provisional (PARTIAL). All genotypes are GRCh37 plus/forward strand.
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
from backend.annotation.cpic import CPIC_GENES, load_cpic_from_csvs
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants, reference_metadata

_CPIC_DIR = Path(__file__).resolve().parents[2] / "backend" / "data" / "cpic"

# NAT2 4-SNP panel, plus-strand (ref = NAT2*4 rapid base).
_NAT2 = {
    "rs1801279": "G",  # *14
    "rs1801280": "T",  # *5
    "rs1799930": "G",  # *6
    "rs1799931": "G",  # *7
}
_CYP2B6 = {"rs3745274": "G", "rs2279343": "A"}  # *1 reference bases


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


def _nat2_geno(**overrides: str) -> dict[str, str]:
    geno = {rsid: ref * 2 for rsid, ref in _NAT2.items()}
    geno.update(overrides)
    return geno


def _cyp2b6_geno(**overrides: str) -> dict[str, str]:
    geno = {rsid: ref * 2 for rsid, ref in _CYP2B6.items()}
    geno.update(overrides)
    return geno


def _call(gene: str, genotypes: dict[str, str], reference_engine: sa.Engine):
    alleles = _fetch_alleles_for_gene(gene, reference_engine)
    return call_star_alleles_for_gene(gene, alleles, genotypes, reference_engine)


def _make_sample(genotypes: dict[str, str]) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    rows = [
        {"rsid": rsid, "chrom": "1", "pos": 1000 + i, "genotype": g}
        for i, (rsid, g) in enumerate(genotypes.items())
    ]
    with engine.begin() as conn:
        conn.execute(raw_variants.insert(), rows)
    return engine


def test_genes_added_to_panel() -> None:
    assert {"NAT2", "CYP2B6"} <= CPIC_GENES


# ── NAT2 acetylator ───────────────────────────────────────────────────────────


def test_nat2_reference_is_rapid(reference_engine: sa.Engine) -> None:
    result = _call("NAT2", _nat2_geno(), reference_engine)
    assert result.diplotype == "*4/*4"
    assert result.phenotype == "Rapid Acetylator"
    assert result.call_confidence == CallConfidence.COMPLETE


def test_nat2_single_slow_is_intermediate_complete(reference_engine: sa.Engine) -> None:
    # One heterozygous slow marker → *4/*5; the slow variant is unambiguously
    # opposite NAT2*4, so the call is COMPLETE (no phase ambiguity).
    result = _call("NAT2", _nat2_geno(rs1801280="TC"), reference_engine)
    assert result.diplotype == "*4/*5"
    assert result.phenotype == "Intermediate Acetylator"
    assert result.call_confidence == CallConfidence.COMPLETE


def test_nat2_two_slow_in_trans_is_slow_but_phase_ambiguous(
    reference_engine: sa.Engine,
) -> None:
    # Het *5 (341T>C) + het *6 (590G>A) → *5/*6 Slow under the unphased trans
    # assumption, but the all-cis alternative (*4 / [*5+*6]) → Intermediate cannot
    # be excluded without phasing (#40), so the call is flagged PARTIAL.
    result = _call("NAT2", _nat2_geno(rs1801280="TC", rs1799930="GA"), reference_engine)
    assert result.diplotype == "*5/*6"
    assert result.phenotype == "Slow Acetylator"
    assert result.call_confidence == CallConfidence.PARTIAL
    assert "phase-ambiguous" in result.confidence_note.lower()
    assert "intermediate acetylator" in result.confidence_note.lower()


def test_nat2_homozygous_slow_is_slow_unambiguous(reference_engine: sa.Engine) -> None:
    # A homozygous slow marker fixes a slow variant on BOTH haplotypes, so the
    # Slow call is phase-unambiguous (no PARTIAL downgrade, no phase note).
    result = _call("NAT2", _nat2_geno(rs1799930="AA"), reference_engine)
    assert result.diplotype == "*6/*6"
    assert result.phenotype == "Slow Acetylator"
    assert result.call_confidence == CallConfidence.COMPLETE
    assert "phase-ambiguous" not in result.confidence_note.lower()


def test_nat2_hom_slow_plus_het_slow_is_unambiguous(reference_engine: sa.Engine) -> None:
    # Hom *6 + het *5: the homozygous slow marker already guarantees both
    # haplotypes are slow, so adding a het slow marker stays unambiguous Slow.
    result = _call("NAT2", _nat2_geno(rs1799930="AA", rs1801280="TC"), reference_engine)
    assert result.phenotype == "Slow Acetylator"
    assert "phase-ambiguous" not in result.confidence_note.lower()


def test_nat2_slow_emits_isoniazid_alert(reference_engine: sa.Engine) -> None:
    sample = _make_sample(_nat2_geno(rs1799930="AA"))
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"NAT2"}))
    alerts = generate_prescribing_alerts(results, reference_engine)
    iso = [a for a in alerts if a.gene == "NAT2" and a.drug == "isoniazid"]
    assert iso and iso[0].phenotype == "Slow Acetylator"


def test_nat2_phase_ambiguous_alert_carries_caveat(reference_engine: sa.Engine) -> None:
    # The phase ambiguity must propagate to the isoniazid alert (PARTIAL + note),
    # so the hepatotoxicity advice is not presented as an unqualified Slow call.
    sample = _make_sample(_nat2_geno(rs1801280="TC", rs1799930="GA"))
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"NAT2"}))
    alerts = generate_prescribing_alerts(results, reference_engine)
    iso = [a for a in alerts if a.gene == "NAT2" and a.drug == "isoniazid"]
    assert iso
    assert iso[0].phenotype == "Slow Acetylator"
    assert iso[0].call_confidence == CallConfidence.PARTIAL
    assert "phase-ambiguous" in iso[0].confidence_note.lower()


# ── CYP2B6 efavirenz (structural-variant gene → PARTIAL) ──────────────────────


def test_cyp2b6_reference_is_normal_but_partial(reference_engine: sa.Engine) -> None:
    result = _call("CYP2B6", _cyp2b6_geno(), reference_engine)
    assert result.diplotype == "*1/*1"
    assert result.phenotype == "Normal Metabolizer"
    # CYP2B6 carries copy-number/structural complexity → never fully COMPLETE.
    assert result.call_confidence == CallConfidence.PARTIAL


def test_cyp2b6_star6_is_intermediate(reference_engine: sa.Engine) -> None:
    # 516 het + 785 het → *1/*6.
    result = _call("CYP2B6", _cyp2b6_geno(rs3745274="GT", rs2279343="AG"), reference_engine)
    assert result.diplotype == "*1/*6"
    assert result.phenotype == "Intermediate Metabolizer"


def test_cyp2b6_star9_is_intermediate(reference_engine: sa.Engine) -> None:
    # 516 het only → *1/*9.
    result = _call("CYP2B6", _cyp2b6_geno(rs3745274="GT"), reference_engine)
    assert result.diplotype == "*1/*9"
    assert result.phenotype == "Intermediate Metabolizer"


def test_cyp2b6_star6_hom_is_poor_with_efavirenz_alert(reference_engine: sa.Engine) -> None:
    sample = _make_sample(_cyp2b6_geno(rs3745274="TT", rs2279343="GG"))
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"CYP2B6"}))
    cyp = next(r for r in results if r.gene == "CYP2B6")
    assert cyp.diplotype == "*6/*6"
    assert cyp.phenotype == "Poor Metabolizer"

    alerts = generate_prescribing_alerts(results, reference_engine)
    efv = [a for a in alerts if a.gene == "CYP2B6" and a.drug == "efavirenz"]
    assert efv and efv[0].phenotype == "Poor Metabolizer"
