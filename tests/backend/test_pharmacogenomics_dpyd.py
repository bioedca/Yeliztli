"""DPYD fluoropyrimidine panel — activity-score phenotypes + safety caveat (SW-E5).

These tests load the REAL production CPIC tables (``backend/data/cpic/*.csv``) so
they validate the shipped, plus-strand-corrected DPYD allele definitions, the
enumerated activity-score diplotypes (incl. compound heterozygotes that were
previously dropped), and the absent-allele / fatal-toxicity caveat attached to
DPYD prescribing alerts.

DPYD uses CPIC's gene Activity Score (sum of the two allele activity values):
AS 2.0 -> Normal, 1.0/1.5 -> Intermediate, 0.0/0.5 -> Poor Metabolizer.
All genotypes below are GRCh37 plus/forward strand (as real 23andMe data is).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.pharmacogenomics import (
    CallConfidence,
    call_all_star_alleles,
    call_star_alleles_for_gene,
    generate_prescribing_alerts,
    store_prescribing_alerts,
    update_annotation_coverage_cpic,
)
from backend.annotation.cpic import load_cpic_from_csvs
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings, raw_variants, reference_metadata

_CPIC_DIR = Path(__file__).resolve().parents[2] / "backend" / "data" / "cpic"

# DPYD defining variants on the GRCh37 plus strand (matches cpic_alleles.csv).
# rsid -> (chrom, pos, ref, alt)
_DPYD = {
    "rs3918290": ("1", 97915614, "C", "T"),  # *2A  No function
    "rs55886062": ("1", 97981343, "A", "C"),  # *13  No function
    "rs67376798": ("1", 97547947, "T", "A"),  # c.2846A>T  Decreased
    "rs75017182": ("1", 98045449, "G", "C"),  # HapB3  Decreased
}


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


def _dpyd_genotypes(**overrides: str) -> dict[str, str]:
    """Plus-strand DPYD genotypes; defaults to homozygous reference (*1/*1).

    Pass e.g. rs3918290="CT" to make that locus heterozygous-variant.
    """
    geno = {rsid: ref * 2 for rsid, (_c, _p, ref, _a) in _DPYD.items()}
    geno.update(overrides)
    return geno


def _call_dpyd(reference_engine: sa.Engine, genotypes: dict[str, str]):
    from backend.analysis.pharmacogenomics import _fetch_alleles_for_gene

    alleles = _fetch_alleles_for_gene("DPYD", reference_engine)
    return call_star_alleles_for_gene("DPYD", alleles, genotypes, reference_engine)


def _sample_from_rows(rows: list[dict]) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    if rows:
        with engine.begin() as conn:
            conn.execute(raw_variants.insert(), rows)
    return engine


def _make_sample(genotypes: dict[str, str]) -> sa.Engine:
    rows = [
        {"rsid": rsid, "chrom": _DPYD[rsid][0], "pos": _DPYD[rsid][1], "genotype": g}
        for rsid, g in genotypes.items()
    ]
    return _sample_from_rows(rows)


# ── Activity-score phenotypes ────────────────────────────────────────────────


def test_reference_is_normal_not_poor(reference_engine: sa.Engine) -> None:
    """A plus-strand homozygous-reference sample is *1/*1 Normal — NEVER Poor.

    This is the patient-safety guard for the strand fix: with the corrected
    plus-strand alleles, a person carrying the reference allele at every DPYD
    locus must call *1/*1 Normal Metabolizer with COMPLETE confidence, not be
    mis-called a no-function homozygote.
    """
    result = _call_dpyd(reference_engine, _dpyd_genotypes())
    assert result.diplotype == "*1/*1"
    assert result.phenotype == "Normal Metabolizer"
    assert result.call_confidence == CallConfidence.COMPLETE


@pytest.mark.parametrize(
    "overrides,expected_diplotype,expected_phenotype",
    [
        ({"rs3918290": "CT"}, "*1/*2A", "Intermediate Metabolizer"),  # AS 1.0
        ({"rs3918290": "TT"}, "*2A/*2A", "Poor Metabolizer"),  # AS 0.0
        ({"rs67376798": "TA"}, "*1/c.2846A>T", "Intermediate Metabolizer"),  # AS 1.5
        (
            {"rs75017182": "GC"},
            "*1/c.1129-5923C>G",
            "Intermediate Metabolizer",
        ),  # AS 1.5 (HapB3 het)
        ({"rs67376798": "AA"}, "c.2846A>T/c.2846A>T", "Intermediate Metabolizer"),  # AS 1.0
    ],
)
def test_dpyd_diplotype_phenotypes(
    reference_engine: sa.Engine,
    overrides: dict[str, str],
    expected_diplotype: str,
    expected_phenotype: str,
) -> None:
    result = _call_dpyd(reference_engine, _dpyd_genotypes(**overrides))
    assert result.diplotype == expected_diplotype
    assert result.phenotype == expected_phenotype
    assert result.call_confidence == CallConfidence.COMPLETE


def test_compound_het_poor_metabolizer_not_dropped(reference_engine: sa.Engine) -> None:
    """A *2A / c.2846A>T compound het is a Poor Metabolizer — previously dropped.

    Before SW-E5 the diplotype *2A/c.2846A>T was absent from cpic_diplotypes.csv,
    so phenotype resolved to None and NO finding was produced for a high-toxicity
    individual. With the enumerated activity-score diplotypes it now resolves to
    Poor Metabolizer (AS 0.5).
    """
    result = _call_dpyd(reference_engine, _dpyd_genotypes(rs3918290="CT", rs67376798="TA"))
    assert result.diplotype == "*2A/c.2846A>T"
    assert result.phenotype == "Poor Metabolizer"
    assert result.call_confidence == CallConfidence.PARTIAL
    assert "unphased" in result.confidence_note


# ── Absent-allele / fatal-toxicity caveat ────────────────────────────────────


def test_dpyd_alert_carries_fluoropyrimidine_caveat(reference_engine: sa.Engine) -> None:
    """Every stored DPYD prescribing alert carries the absent-allele caveat."""
    sample = _make_sample(_dpyd_genotypes(rs3918290="CT"))  # *1/*2A Intermediate
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"DPYD"}))
    alerts = generate_prescribing_alerts(results, reference_engine)

    dpyd_alerts = [a for a in alerts if a.gene == "DPYD"]
    assert dpyd_alerts, "expected DPYD prescribing alerts for an Intermediate Metabolizer"
    drugs = {a.drug for a in dpyd_alerts}
    assert {"fluorouracil", "capecitabine"} <= drugs

    store_prescribing_alerts(alerts, sample)
    with sample.connect() as conn:
        rows = conn.execute(
            sa.select(findings.c.detail_json, findings.c.metabolizer_status).where(
                findings.c.gene_symbol == "DPYD"
            )
        ).fetchall()
    assert rows
    for detail_json, metabolizer in rows:
        detail = json.loads(detail_json)
        caveat = detail.get("gene_caveat")
        assert caveat, "DPYD finding missing gene_caveat"
        lower = caveat.lower()
        assert "dpd" in lower and "fluoropyrimidine" in lower
        assert "does not" in lower or "not rule out" in lower
        # Caveat is context-only: it must not have altered the metabolizer status.
        assert metabolizer == "Intermediate Metabolizer"


def test_non_dpyd_alert_has_no_gene_caveat(reference_engine: sa.Engine) -> None:
    """The gene caveat is DPYD-specific — CYP2C19 alerts carry none."""
    # CYP2C19 *1/*2 (rs4244285 plus-strand G>A, het) -> Intermediate, has a guideline.
    # Include the other CYP2C19 defining rsids as reference so the call is COMPLETE
    # (not Insufficient) and an alert is produced.
    sample = _sample_from_rows(
        [
            {"rsid": "rs4244285", "chrom": "10", "pos": 96541616, "genotype": "GA"},
            {"rsid": "rs4986893", "chrom": "10", "pos": 96540410, "genotype": "GG"},
            {"rsid": "rs28399504", "chrom": "10", "pos": 96522463, "genotype": "AA"},
            {"rsid": "rs12248560", "chrom": "10", "pos": 96521657, "genotype": "CC"},
        ]
    )
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"CYP2C19"}))
    alerts = generate_prescribing_alerts(results, reference_engine)
    store_prescribing_alerts(alerts, sample)
    with sample.connect() as conn:
        rows = conn.execute(
            sa.select(findings.c.detail_json).where(findings.c.gene_symbol == "CYP2C19")
        ).fetchall()
    assert rows
    for (detail_json,) in rows:
        assert json.loads(detail_json).get("gene_caveat") is None


def test_update_annotation_coverage_runs(reference_engine: sa.Engine) -> None:
    """Smoke: the CPIC coverage bitmask update tolerates DPYD involvement."""
    sample = _make_sample(_dpyd_genotypes(rs3918290="CT"))
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"DPYD"}))
    # annotated_variants is empty here, so 0 rows update — must not raise.
    assert update_annotation_coverage_cpic(results, sample) >= 0


# ── Activity-score-keyed dosing (#1993) ──────────────────────────────────
#
# CPIC keys DPYD fluoropyrimidine dosing on the activity score, splitting BOTH
# phenotype bands. Before #1993 cpic_guidelines keyed on the phenotype label, so
# the two Intermediate scores (AS 1.5 / 1.0) and the two Poor scores (AS 0.5 /
# 0.0) each collapsed to one recommendation — under-reducing the c.2846A>T
# homozygote and dropping the AS-0.5 conditional fallback. Ground truth:
# api.cpicpgx.org/v1/recommendation (accessed 2026-07-16); primary guideline
# Amstutz et al. 2018, PMID:29152729 / DOI:10.1002/cpt.911.


def _dpyd_recommendations(
    reference_engine: sa.Engine, genotypes: dict[str, str]
) -> dict[str, str]:
    """gene-drug recommendation strings for a DPYD genotype, via the shipped path."""
    sample = _make_sample(genotypes)
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"DPYD"}))
    alerts = [
        a for a in generate_prescribing_alerts(results, reference_engine) if a.gene == "DPYD"
    ]
    recommendations: dict[str, str] = {}
    for alert in alerts:
        # A duplicate drug alert would silently overwrite the first — fail loudly
        # so a two-row AS match (a lookup regression) can't hide behind the dict.
        assert alert.drug not in recommendations, f"duplicate DPYD alert for {alert.drug}"
        recommendations[alert.drug] = alert.recommendation or ""
    return recommendations


def test_as_1_0_homozygote_differs_from_as_1_5_het(reference_engine: sa.Engine) -> None:
    """c.2846A>T/c.2846A>T (AS 1.0) must carry CPIC's ">50% reduction" caveat and
    differ from the *1/c.2846A>T (AS 1.5) recommendation — the sharpest #1993
    defect (both were byte-identical "Reduce dose by 50%")."""
    homozygote = _dpyd_recommendations(reference_engine, _dpyd_genotypes(rs67376798="AA"))
    het = _dpyd_recommendations(reference_engine, _dpyd_genotypes(rs67376798="TA"))

    assert homozygote and het
    for drug in ("fluorouracil", "capecitabine"):
        assert ">50%" in homozygote[drug], f"{drug} AS-1.0 lost the >50% caveat"
        assert "c.2846A>T/c.2846A>T" in homozygote[drug]
        assert ">50%" not in het[drug], f"{drug} AS-1.5 wrongly carries the >50% caveat"
        assert homozygote[drug] != het[drug], f"{drug}: AS 1.0 and AS 1.5 collapsed again"


def test_as_0_5_pm_keeps_avoid_headline_and_tdm_fallback(reference_engine: sa.Engine) -> None:
    """*2A/c.2846A>T (AS 0.5) keeps "Avoid" as the headline but restores CPIC's
    conditional reduced-dose + early-TDM fallback, and differs from *2A/*2A
    (AS 0.0)'s flat avoid. AS 0.5 must NOT read as simply "reduced dose"."""
    as_05 = _dpyd_recommendations(
        reference_engine, _dpyd_genotypes(rs3918290="CT", rs67376798="TA")
    )
    as_00 = _dpyd_recommendations(reference_engine, _dpyd_genotypes(rs3918290="TT"))

    assert as_05 and as_00
    for drug in ("fluorouracil", "capecitabine"):
        assert as_05[drug].lower().startswith("avoid"), f"{drug} AS-0.5 lost the avoid headline"
        assert "therapeutic drug monitoring" in as_05[drug].lower(), (
            f"{drug} AS-0.5 dropped the conditional TDM fallback"
        )
        assert "therapeutic drug monitoring" not in as_00[drug].lower(), (
            f"{drug} AS-0.0 wrongly carries the TDM fallback"
        )
        assert as_05[drug] != as_00[drug], f"{drug}: AS 0.5 and AS 0.0 collapsed"


def test_no_two_distinct_activity_scores_share_a_recommendation() -> None:
    """Guard: within one (gene, drug), no two distinct activity scores may map to
    the same recommendation string — that is the collapse #1993 fixed. Checks
    both the bundled and seed CPIC guideline CSVs directly."""
    import csv
    from collections import defaultdict

    seed = (
        Path(__file__).resolve().parents[1] / "fixtures" / "seed_csvs" / "cpic_guidelines_seed.csv"
    )
    for path in (_CPIC_DIR / "cpic_guidelines.csv", seed):
        by_pair: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        activity_score_rows = 0
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                score = (row.get("activity_score") or "").strip()
                if not score:  # phenotype-keyed genes are exempt
                    continue
                activity_score_rows += 1
                by_pair[(row["gene"], row["drug"])][row["recommendation"]].add(score)
        # Guard against the guard silently no-op'ing: if the AS rows ever vanish
        # (a regression back to phenotype-keying), there are no collisions to find
        # and this would pass vacuously. Fail instead.
        assert activity_score_rows, f"{path.name} has no activity-score-keyed rows"
        collisions = [
            f"{path.name}: {gene}/{drug} — scores {sorted(scores)} share one recommendation"
            for (gene, drug), recs in by_pair.items()
            for rec, scores in recs.items()
            if len(scores) > 1
        ]
        assert not collisions, "distinct activity scores collapsed to one recommendation:\n  " + (
            "\n  ".join(collisions)
        )
