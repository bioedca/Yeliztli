"""An observed non-reference allele the called diplotype leaves out is surfaced (#2113).

The greedy star-allele assigner fills unused slots with the reference allele.
An observed alt copy that no called allele consumed used to vanish silently.

- More non-reference alleles than the two slots: the call cannot be right, so
  it is withheld (Insufficient) and issues no prescribing guidance.
- A leftover that belongs only to multi-variant alleles whose other components
  are absent: either a known partial haplotype (SLCO1B1 g.-11187G>A without
  c.521C is deliberately *1B, #110) or an allele the table lacks (CYP2B6
  785A>G before *4, #1985). The engine cannot tell those apart, so the call
  stands and a structured warning records the leftover.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from structlog.testing import capture_logs

from backend.analysis.pharmacogenomics import (
    CallConfidence,
    _fetch_alleles_for_gene,
    call_star_alleles_for_gene,
    generate_prescribing_alerts,
)
from backend.annotation.cpic import load_cpic_from_csvs
from backend.db.tables import reference_metadata

_CPIC_DIR = Path(__file__).resolve().parents[2] / "backend" / "data" / "cpic"

# CYP2B6 *1 reference bases (plus strand): 516G>T, 785A>G, 983T>C.
_CYP2B6_REF = {"rs3745274": "G", "rs2279343": "A", "rs28399499": "T"}
# NAT2 *4 reference bases: *14, *5, *6, *7 defining SNPs.
_NAT2_REF = {"rs1801279": "G", "rs1801280": "T", "rs1799930": "G", "rs1799931": "G"}

CROWDED = "More non-reference alleles were observed than a two-allele diplotype holds"


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


def _geno(reference: dict[str, str], **overrides: str) -> dict[str, str]:
    geno = {rsid: base * 2 for rsid, base in reference.items()}
    geno.update(overrides)
    return geno


def _call(gene, alleles, genotypes, reference_engine):
    return call_star_alleles_for_gene(gene, alleles, genotypes, reference_engine)


def _residual_events(logs: list[dict]) -> list[dict]:
    return [e for e in logs if e.get("event") == "pgx_residual_alt_discarded"]


def test_a_leftover_no_bundled_allele_explains_is_logged_not_withheld(
    reference_engine: sa.Engine,
) -> None:
    # The allele table as it was before CYP2B6*4 (785A>G alone) was bundled.
    alleles = [
        a for a in _fetch_alleles_for_gene("CYP2B6", reference_engine) if a["allele_name"] != "*4"
    ]
    assert alleles, "CYP2B6 allele table should load"
    genotypes = _geno(_CYP2B6_REF, rs2279343="AG")
    with_all = _call(
        "CYP2B6",
        _fetch_alleles_for_gene("CYP2B6", reference_engine),
        _geno(_CYP2B6_REF),
        reference_engine,
    )

    with capture_logs() as logs:
        result = _call("CYP2B6", alleles, genotypes, reference_engine)

    events = _residual_events(logs)
    assert len(events) == 1
    assert events[0]["gene"] == "CYP2B6"
    assert events[0]["residual"] == {"rs2279343:A>G": 1}
    assert events[0]["crowded_out"] == []
    assert events[0]["log_level"] == "warning"
    # Not crowded out, so the call is not withheld: it stands as the reference
    # sample's does.
    assert result.call_confidence == with_all.call_confidence
    assert CROWDED not in result.confidence_note
    assert "rs2279343" not in result.involved_rsids


def test_the_same_genotype_is_not_flagged_when_an_allele_explains_it(
    reference_engine: sa.Engine,
) -> None:
    alleles = _fetch_alleles_for_gene("CYP2B6", reference_engine)
    assert any(a["allele_name"] == "*4" for a in alleles)

    with capture_logs() as logs:
        result = _call("CYP2B6", alleles, _geno(_CYP2B6_REF, rs2279343="AG"), reference_engine)

    assert "*4" in result.diplotype
    assert "rs2279343" in result.involved_rsids
    assert _residual_events(logs) == []
    assert CROWDED not in result.confidence_note
    assert result.call_confidence != CallConfidence.INSUFFICIENT


def test_a_reference_sample_is_not_flagged(reference_engine: sa.Engine) -> None:
    alleles = _fetch_alleles_for_gene("CYP2B6", reference_engine)

    with capture_logs() as logs:
        result = _call("CYP2B6", alleles, _geno(_CYP2B6_REF), reference_engine)

    assert result.diplotype == "*1/*1"
    assert _residual_events(logs) == []
    assert CROWDED not in result.confidence_note
    assert result.call_confidence != CallConfidence.INSUFFICIENT


def test_more_non_reference_alleles_than_slots_is_flagged(reference_engine: sa.Engine) -> None:
    """Gene-agnostic: three distinct single-SNP alleles cannot fit two slots."""
    alleles = _fetch_alleles_for_gene("NAT2", reference_engine)
    genotypes = _geno(_NAT2_REF, rs1801280="TC", rs1799930="GA", rs1799931="GA")

    with capture_logs() as logs:
        result = _call("NAT2", alleles, genotypes, reference_engine)

    assert result.call_confidence == CallConfidence.INSUFFICIENT
    assert CROWDED in result.confidence_note
    events = _residual_events(logs)
    assert len(events) == 1
    assert events[0]["crowded_out"], "the allele left without a slot is named"
    # An insufficient call issues no prescribing guidance.
    alerts = generate_prescribing_alerts([result], reference_engine)
    assert [a for a in alerts if a.gene == "NAT2"] == []


def test_slco1b1_partial_star17_haplotype_keeps_its_deliberate_call(
    reference_engine: sa.Engine,
) -> None:
    """g.-11187G>A without c.521C is deliberately *1B/*1B, Complete (#110)."""
    alleles = _fetch_alleles_for_gene("SLCO1B1", reference_engine)
    genotypes = {"rs2306283": "GG", "rs4149015": "AA", "rs4149056": "TT"}

    with capture_logs() as logs:
        result = _call("SLCO1B1", alleles, genotypes, reference_engine)

    assert result.diplotype == "*1B/*1B"
    assert result.call_confidence == CallConfidence.COMPLETE
    assert CROWDED not in result.confidence_note
    events = _residual_events(logs)
    assert len(events) == 1
    assert events[0]["crowded_out"] == []
