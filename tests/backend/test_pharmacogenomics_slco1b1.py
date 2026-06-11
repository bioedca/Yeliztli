"""SLCO1B1 statin panel — production-CSV-backed regression for *15 diplotypes (issue #45).

These tests load the REAL production CPIC tables (``backend/data/cpic/*.csv``)
rather than a hand-built in-memory fixture, so they validate the shipped
diplotype→phenotype mapping that the pharmacogenomics caller and the
prescribing-alert generator actually consume in production.

Regression guard for issue #45: ``cpic_alleles.csv`` defines the SLCO1B1
``*15`` haplotype (rs2306283 c.388A>G **plus** rs4149056 c.521T>C) and ``*17``
(rs2306283 c.388A>G plus rs4149015), so the greedy caller can produce complete
``*15``/``*17``-containing diplotypes — ``*15/*15``, ``*1B/*15``, ``*5/*15``,
``*1B/*1B``, ``*1A/*17``, ``*1B/*17``, ``*15/*17``, ``*17/*17`` — that had no row
in ``cpic_diplotypes.csv``. Before the fix they resolved to ``phenotype=None`` at
Complete confidence, so ``generate_prescribing_alerts`` silently skipped
simvastatin guidance for a carrier of the rs4149056 c.521C decreased-function
allele — exactly the same class of "dropped diplotype" defect fixed for TPMT
(issue #12) and DPYD (SW-E5).

Phenotype assignments follow the CPIC OATP1B1 function scale (poor < decreased <
normal), in which two decreased-function (c.521C-bearing) alleles give a Poor
function phenotype and one decreased-function allele gives Decreased function
(Cooper-DeHoff et al. 2022 CPIC guideline, PMID 35152405; Link et al. SEARCH
2008, PMID 18650507). The specific diplotype calls are corroborated in the
literature: ``*5/*5`` and ``*15/*15`` are Poor function and ``*1/*15`` is
Decreased function (Naushad et al. 2025, Pharmacol Rep), and the decreased
function phenotype comprises ``*1b/*5``/``*1b/*15`` (Tipnoppanon et al. 2026,
Clin Transl Sci).

All genotypes below are GRCh37 plus/forward strand (as real 23andMe data is);
star-allele calling is keyed on rsid, so the chrom/pos are realistic but not
load-bearing. NOTE: the production ``*17`` allele definition omits rs4149056
(tracked separately); the diplotype→phenotype rows asserted here remain correct
once that definition is completed, because c.521C-bearing ``*17`` is a
decreased-function allele either way.
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

# SLCO1B1 defining variants on the GRCh37 plus strand (matches cpic_alleles.csv).
# SLCO1B1 is a plus-strand gene, so alt is the base a carrier of the allele has.
# rsid -> (chrom, pos, ref, alt).
_SLCO1B1 = {
    "rs2306283": ("12", 21329738, "A", "G"),  # *1B  c.388A>G
    "rs4149056": ("12", 21331549, "T", "C"),  # *5   c.521T>C  (decreased function)
    "rs4149015": ("12", 21284124, "G", "A"),  # part of *17
}


def _slco1b1_genotypes(**overrides: str) -> dict[str, str]:
    """Plus-strand SLCO1B1 genotypes; defaults to homozygous reference (*1A/*1A).

    Pass e.g. rs4149056="CC" to make that locus homozygous-variant.
    """
    geno = {rsid: ref * 2 for rsid, (_c, _p, ref, _a) in _SLCO1B1.items()}
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


def _call_slco1b1(reference_engine: sa.Engine, genotypes: dict[str, str]):
    alleles = _fetch_alleles_for_gene("SLCO1B1", reference_engine)
    return call_star_alleles_for_gene("SLCO1B1", alleles, genotypes, reference_engine)


def _make_sample(genotypes: dict[str, str]) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    rows = [
        {"rsid": rsid, "chrom": _SLCO1B1[rsid][0], "pos": _SLCO1B1[rsid][1], "genotype": g}
        for rsid, g in genotypes.items()
    ]
    with engine.begin() as conn:
        conn.execute(raw_variants.insert(), rows)
    return engine


def test_reference_is_normal_function(reference_engine: sa.Engine) -> None:
    """A plus-strand homozygous-reference SLCO1B1 sample is *1A/*1A Normal function."""
    result = _call_slco1b1(reference_engine, _slco1b1_genotypes())
    assert result.diplotype == "*1A/*1A"
    assert result.phenotype == "Normal function"
    assert result.call_confidence == CallConfidence.COMPLETE


# (expected diplotype, plus-strand genotype overrides, expected activity score).
# Each was verified to be produced by call_star_alleles_for_gene over the
# production CSVs. Two decreased-function (c.521C-bearing) alleles -> Poor
# function — the OATP1B1 group with the highest simvastatin myopathy risk
# (Link et al. SEARCH 2008, PMID 18650507; Naushad et al. 2025).
_POOR_FUNCTION = [
    ("*5/*15", {"rs2306283": "AG", "rs4149056": "CC"}, 0.75),
    ("*15/*15", {"rs2306283": "GG", "rs4149056": "CC"}, 0.5),
    ("*15/*17", {"rs2306283": "GG", "rs4149056": "TC", "rs4149015": "GA"}, 0.75),
    ("*17/*17", {"rs2306283": "GG", "rs4149056": "TT", "rs4149015": "AA"}, 1.0),
]

# One decreased-function allele over a normal allele -> Decreased function
# (Tipnoppanon et al. 2026: decreased-function phenotype = *1b/*5 or *1b/*15).
_DECREASED_FUNCTION = [
    ("*1B/*15", {"rs2306283": "GG", "rs4149056": "TC"}, 1.0),
    ("*1A/*17", {"rs2306283": "AG", "rs4149056": "TT", "rs4149015": "GA"}, 1.5),
    ("*1B/*17", {"rs2306283": "GG", "rs4149056": "TT", "rs4149015": "GA"}, 1.25),
]

# Two normal-function alleles (c.388A>G is a normal-function allele) -> Normal.
_NORMAL_FUNCTION = [
    ("*1B/*1B", {"rs2306283": "GG", "rs4149056": "TT", "rs4149015": "GG"}, 1.5),
]


@pytest.mark.parametrize(
    "expected_diplotype,overrides,activity_score,expected_phenotype",
    [(d, o, a, "Poor function") for d, o, a in _POOR_FUNCTION]
    + [(d, o, a, "Decreased function") for d, o, a in _DECREASED_FUNCTION]
    + [(d, o, a, "Normal function") for d, o, a in _NORMAL_FUNCTION],
)
def test_newly_mapped_diplotypes_resolve_to_a_phenotype(
    reference_engine: sa.Engine,
    expected_diplotype: str,
    overrides: dict[str, str],
    activity_score: float,
    expected_phenotype: str,
) -> None:
    """Each callable *15/*17-containing SLCO1B1 diplotype maps to a phenotype (issue #45).

    Before the fix these resolved to phenotype=None at Complete confidence, so a
    carrier of the rs4149056 c.521C decreased-function allele received no
    SLCO1B1 statin-safety alert at all.
    """
    result = _call_slco1b1(reference_engine, _slco1b1_genotypes(**overrides))
    assert result.diplotype == expected_diplotype
    assert result.phenotype == expected_phenotype
    assert result.activity_score == activity_score
    assert result.call_confidence == CallConfidence.COMPLETE


@pytest.mark.parametrize(
    "expected_diplotype,overrides,recommendation_fragment",
    [(d, o, "Avoid simvastatin") for d, o, _a in _POOR_FUNCTION]
    + [(d, o, "lower dose or alternative statin") for d, o, _a in _DECREASED_FUNCTION],
)
def test_actionable_diplotypes_emit_simvastatin_alert(
    reference_engine: sa.Engine,
    expected_diplotype: str,
    overrides: dict[str, str],
    recommendation_fragment: str,
) -> None:
    """A decreased/poor-function SLCO1B1 call gets a simvastatin alert (issue #45).

    End-to-end patient-safety guard: the missing diplotype rows previously made
    generate_prescribing_alerts() skip the gene for carriers of the c.521C
    myopathy-risk allele.
    """
    sample = _make_sample(_slco1b1_genotypes(**overrides))
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"SLCO1B1"}))
    alerts = generate_prescribing_alerts(results, reference_engine)

    slco_alerts = [a for a in alerts if a.gene == "SLCO1B1"]
    assert slco_alerts, f"expected SLCO1B1 simvastatin alert for {expected_diplotype}"
    drugs = {a.drug for a in slco_alerts}
    assert "simvastatin" in drugs
    for alert in slco_alerts:
        assert alert.diplotype == expected_diplotype
        assert recommendation_fragment in alert.recommendation


def test_every_callable_slco1b1_diplotype_has_a_phenotype(
    reference_engine: sa.Engine,
) -> None:
    """No greedily-callable SLCO1B1 diplotype resolves to phenotype=None (issue #45).

    Drives the caller over every {ref, het, hom} combination of the three
    SLCO1B1 defining loci. Any call made at Complete confidence (i.e. all
    defining variants observed) must map to a phenotype — otherwise it would be
    silently dropped by the prescribing-alert generator. This locks the whole
    SLCO1B1 diplotype space, not just the eight rows added for this issue.
    """
    states = {
        "rs2306283": ["AA", "AG", "GG"],  # *1B ref A / alt G
        "rs4149056": ["TT", "TC", "CC"],  # *5  ref T / alt C
        "rs4149015": ["GG", "GA", "AA"],  # *17 ref G / alt A
    }
    unmapped: list[str] = []
    for g1b in states["rs2306283"]:
        for g5 in states["rs4149056"]:
            for g17 in states["rs4149015"]:
                geno = {"rs2306283": g1b, "rs4149056": g5, "rs4149015": g17}
                result = _call_slco1b1(reference_engine, geno)
                if result.call_confidence == CallConfidence.COMPLETE and result.phenotype is None:
                    unmapped.append(f"{result.diplotype} from {geno}")
    assert not unmapped, "callable SLCO1B1 diplotypes with no phenotype mapping: " + "; ".join(
        unmapped
    )
