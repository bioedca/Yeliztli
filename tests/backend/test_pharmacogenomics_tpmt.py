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
    """A *1/*3B Intermediate Metabolizer gets all three thiopurine alerts.

    This is the end-to-end patient-safety guard: the missing diplotype row
    previously caused ``generate_prescribing_alerts`` to skip this gene entirely.
    Thioguanine (issue #224) is the third CPIC thiopurine alongside azathioprine
    and mercaptopurine and must fire its own reduced-dose alert.
    """
    sample = _make_sample(_tpmt_genotypes(rs1800460="CT"))
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"TPMT"}))
    alerts = generate_prescribing_alerts(results, reference_engine)

    tpmt_alerts = [a for a in alerts if a.gene == "TPMT"]
    assert tpmt_alerts, "expected TPMT prescribing alerts for an Intermediate Metabolizer"
    drugs = {a.drug for a in tpmt_alerts}
    assert {"azathioprine", "mercaptopurine", "thioguanine"} <= drugs
    for alert in tpmt_alerts:
        assert alert.diplotype == "*1/*3B"
        assert alert.phenotype == "Intermediate Metabolizer"
    # Issue #224: the thioguanine alert exists and carries IM reduced-dose guidance.
    thioguanine = [a for a in tpmt_alerts if a.drug == "thioguanine"]
    assert len(thioguanine) == 1
    assert thioguanine[0].recommendation == (
        "Start with reduced doses (reduce by 30-50% of target) and titrate based on tolerance."
    )


@pytest.mark.parametrize(
    "expected_diplotype,overrides,phenotype,recommendation",
    [
        (
            "*1/*3B",
            {"rs1800460": "CT"},
            "Intermediate Metabolizer",
            "Start with reduced doses (reduce by 30-50% of target) and titrate "
            "based on tolerance.",
        ),
        (
            "*3B/*3B",
            {"rs1800460": "TT"},
            "Poor Metabolizer",
            "Start with drastically reduced doses (reduce by 50-75%) and titrate "
            "based on myelosuppression; for nonmalignant conditions consider an "
            "alternative agent.",
        ),
    ],
)
def test_actionable_tpmt_emits_thioguanine_alert(
    reference_engine: sa.Engine,
    expected_diplotype: str,
    overrides: dict[str, str],
    phenotype: str,
    recommendation: str,
) -> None:
    """An actionable TPMT phenotype surfaces a thioguanine alert (issue #224).

    CPIC's thiopurine/TPMT guideline (Relling et al. Clin Pharmacol Ther 2019,
    PMID 30447069) covers thioguanine alongside azathioprine and mercaptopurine.
    Before #224 the shipped cpic_guidelines.csv had no TPMT thioguanine rows, so
    a TPMT-deficient patient prescribed thioguanine got no dose-reduction warning
    at all — the same silent-drop defect this file guards for the other two
    thiopurines. Each phenotype must emit exactly one thioguanine alert carrying
    its CPIC recommendation verbatim.
    """
    sample = _make_sample(_tpmt_genotypes(**overrides))
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"TPMT"}))
    alerts = generate_prescribing_alerts(results, reference_engine)

    thioguanine = [a for a in alerts if a.gene == "TPMT" and a.drug == "thioguanine"]
    assert len(thioguanine) == 1, f"expected one TPMT thioguanine alert for {expected_diplotype}"
    alert = thioguanine[0]
    assert alert.diplotype == expected_diplotype
    assert alert.phenotype == phenotype
    assert alert.recommendation == recommendation


def test_star3a_double_het_is_phase_ambiguous(reference_engine: sa.Engine) -> None:
    """Double-het TPMT*3 markers are not an unambiguous *1/*3A call (issue #60)."""
    result = _call_tpmt(
        reference_engine,
        _tpmt_genotypes(rs1800460="CT", rs1142345="TC"),
    )
    assert result.diplotype == "*1/*3A"
    assert result.phenotype == "Intermediate Metabolizer"
    assert result.call_confidence == CallConfidence.PARTIAL
    assert "*3B/*3C" in result.confidence_note
    assert "Poor Metabolizer" in result.confidence_note
    assert "unphased" in result.confidence_note


def test_star3a_double_het_alert_carries_phase_caveat(reference_engine: sa.Engine) -> None:
    """The thiopurine alert keeps firing, but no longer as a Complete *1/*3A call."""
    sample = _make_sample(_tpmt_genotypes(rs1800460="CT", rs1142345="TC"))
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"TPMT"}))
    alerts = generate_prescribing_alerts(results, reference_engine)

    tpmt_alerts = [a for a in alerts if a.gene == "TPMT"]
    assert tpmt_alerts, "expected TPMT alerts for phase-ambiguous *3 double het"
    drugs = {a.drug for a in tpmt_alerts}
    assert {"azathioprine", "mercaptopurine"} <= drugs
    for alert in tpmt_alerts:
        assert alert.diplotype == "*1/*3A"
        assert alert.phenotype == "Intermediate Metabolizer"
        assert alert.call_confidence == CallConfidence.PARTIAL
        assert "*3B/*3C" in alert.confidence_note
        assert "Poor Metabolizer" in alert.confidence_note


# Issue #12: no-function / no-function diplotypes that the greedy star-allele
# caller can reach but which had no cpic_diplotypes.csv row, so they resolved to
# phenotype=None and were silently skipped by generate_prescribing_alerts(). Two
# no-function TPMT alleles = Poor Metabolizer (CPIC thiopurine guideline, Relling
# et al. Clin Pharmacol Ther 2019, PMID 30447069), the highest-toxicity group.
# Each tuple is (expected diplotype, plus-strand genotype overrides, expected
# confidence) and was verified to be produced by call_star_alleles_for_gene over
# the production CSVs.
# *3B/*3C is absent from this reachable-diplotype list because the caller assigns
# the 2-variant *3A first; the resulting *1/*3A call is phase-flagged above.
_TPMT_POOR_METABOLIZERS = [
    ("*2/*2", {"rs1800462": "GG"}, CallConfidence.COMPLETE),
    (
        "*2/*3A",
        {"rs1800462": "CG", "rs1800460": "CT", "rs1142345": "TC"},
        CallConfidence.PARTIAL,
    ),
    ("*2/*3B", {"rs1800462": "CG", "rs1800460": "CT"}, CallConfidence.PARTIAL),
    ("*2/*3C", {"rs1800462": "CG", "rs1142345": "TC"}, CallConfidence.PARTIAL),
    ("*3A/*3B", {"rs1800460": "TT", "rs1142345": "TC"}, CallConfidence.COMPLETE),
    ("*3A/*3C", {"rs1800460": "CT", "rs1142345": "CC"}, CallConfidence.COMPLETE),
    ("*3B/*3B", {"rs1800460": "TT"}, CallConfidence.COMPLETE),
    ("*3C/*3C", {"rs1142345": "CC"}, CallConfidence.COMPLETE),
]


@pytest.mark.parametrize(
    "expected_diplotype,overrides,expected_confidence", _TPMT_POOR_METABOLIZERS
)
def test_no_function_diplotypes_are_poor_metabolizers(
    reference_engine: sa.Engine,
    expected_diplotype: str,
    overrides: dict[str, str],
    expected_confidence: CallConfidence,
) -> None:
    """Each callable no-fn/no-fn TPMT diplotype maps to Poor Metabolizer (issue #12).

    Before the fix these resolved to phenotype=None at Complete confidence, so a
    TPMT Poor Metabolizer — the group at highest risk of thiopurine-induced
    myelosuppression — received no azathioprine/mercaptopurine warning at all.
    """
    result = _call_tpmt(reference_engine, _tpmt_genotypes(**overrides))
    assert result.diplotype == expected_diplotype
    assert result.phenotype == "Poor Metabolizer"
    assert result.activity_score == 0.0
    assert result.call_confidence == expected_confidence
    if expected_confidence == CallConfidence.PARTIAL:
        assert "unphased" in result.confidence_note


@pytest.mark.parametrize(
    "expected_diplotype,overrides,expected_confidence", _TPMT_POOR_METABOLIZERS
)
def test_poor_metabolizers_emit_thiopurine_alerts(
    reference_engine: sa.Engine,
    expected_diplotype: str,
    overrides: dict[str, str],
    expected_confidence: CallConfidence,
) -> None:
    """A TPMT Poor Metabolizer gets azathioprine + mercaptopurine alerts (issue #12).

    End-to-end patient-safety guard: the missing diplotype rows previously made
    generate_prescribing_alerts() skip the gene for the highest-toxicity group.
    """
    sample = _make_sample(_tpmt_genotypes(**overrides))
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"TPMT"}))
    alerts = generate_prescribing_alerts(results, reference_engine)

    tpmt_alerts = [a for a in alerts if a.gene == "TPMT"]
    assert tpmt_alerts, f"expected TPMT alerts for Poor Metabolizer {expected_diplotype}"
    drugs = {a.drug for a in tpmt_alerts}
    assert {"azathioprine", "mercaptopurine"} <= drugs
    for alert in tpmt_alerts:
        assert alert.diplotype == expected_diplotype
        assert alert.phenotype == "Poor Metabolizer"
        assert alert.call_confidence == expected_confidence
        if expected_confidence == CallConfidence.PARTIAL:
            assert "unphased" in alert.confidence_note


def test_every_callable_tpmt_diplotype_has_a_phenotype(reference_engine: sa.Engine) -> None:
    """No greedily-callable TPMT diplotype resolves to phenotype=None (issue #12).

    Drives the caller over every {ref, het, hom} combination of the three TPMT
    defining loci. Any call made at Complete confidence (i.e. all defining
    variants observed) must map to a phenotype — otherwise it would be silently
    dropped by the prescribing-alert generator. This locks the whole TPMT
    diplotype space, not just the eight rows added for this issue.
    """
    states = {
        "rs1800462": ["CC", "CG", "GG"],  # *2  ref C / alt G
        "rs1800460": ["CC", "CT", "TT"],  # *3B ref C / alt T
        "rs1142345": ["TT", "TC", "CC"],  # *3C ref T / alt C
    }
    unmapped: list[str] = []
    for g2 in states["rs1800462"]:
        for g3b in states["rs1800460"]:
            for g3c in states["rs1142345"]:
                geno = {"rs1800462": g2, "rs1800460": g3b, "rs1142345": g3c}
                result = _call_tpmt(reference_engine, geno)
                if result.call_confidence == CallConfidence.COMPLETE and result.phenotype is None:
                    unmapped.append(f"{result.diplotype} from {geno}")
    assert not unmapped, "callable TPMT diplotypes with no phenotype mapping: " + "; ".join(
        unmapped
    )
