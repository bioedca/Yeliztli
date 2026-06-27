"""CYP2D6 activity-score / phenotype guards (issues #47 and #1077).

``CYP2D6*4`` is a no-function allele (activity score 0.0) and ``CYP2D6*10`` is
decreased-function (activity score 0.25, per the CPIC-revised value), so a
``*4/*10`` carrier has a diplotype activity score of 0.25. The CPIC/DPWG
standardized genotype-to-phenotype translation bins activity score **0** as Poor
Metabolizer, **0 < AS < 1.25** as Intermediate Metabolizer, and
**1.25 <= AS <= 2.25** as Normal Metabolizer; nonzero decreased-function
activity is explicitly separated from the Poor Metabolizer group (AS = 0), while
AS 1.25/1.5 diplotypes remain Normal Metabolizer:

  - Caudle et al. 2019, *Clin Transl Sci* (PMID 31647186) — consensus
    standardized CYP2D6 genotype-to-phenotype translation.
  - Gaedigk et al. 2016, *Genet Med* (PMID 27388693) — poor metabolizer status
    is AS = 0; nonzero decreased-function bins are kept separate.
  - Hongkaew et al. 2021, *Sci Rep* — intermediate metabolizers defined as
    AS 0.25-0.75; revised ``*10`` activity value of 0.25.

The production ``cpic_diplotypes.csv`` previously labeled ``CYP2D6 *4/*10`` as
Poor Metabolizer (AS 0.25), which overstated loss of CYP2D6 activity and routed
carriers to Poor Metabolizer recommendations (e.g. *avoid codeine*, *avoid
tamoxifen*) instead of the Intermediate Metabolizer guidance. These tests load
the REAL production CPIC tables and lock the corrected mapping, plus the
invariants that CYP2D6 phenotypes follow the consensus activity-score bins.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.pharmacogenomics import (
    CallConfidence,
    call_all_star_alleles,
    generate_prescribing_alerts,
)
from backend.annotation.cpic import load_cpic_from_csvs
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants, reference_metadata

_CPIC_DIR = Path(__file__).resolve().parents[2] / "backend" / "data" / "cpic"
_DIPLOTYPES_CSV = _CPIC_DIR / "cpic_diplotypes.csv"

# CYP2D6 SNV defining variants on the GRCh37 plus strand (matches cpic_alleles.csv).
# rsid -> (chrom, pos, ref, alt). Indel-defining rsids (*3/*6/*9) are intentionally
# omitted (array data cannot call them; they stay "missing" but < 50%).
_CYP2D6 = {
    "rs16947": ("22", 42523943, "G", "A"),  # *2
    "rs3892097": ("22", 42524947, "C", "T"),  # *4  No function
    "rs1065852": ("22", 42526694, "G", "A"),  # *10 Decreased function
    "rs28371706": ("22", 42525772, "G", "A"),  # *17
    "rs59421388": ("22", 42523610, "C", "T"),  # *29
    "rs28371725": ("22", 42523805, "C", "T"),  # *41
}

_CYP2D6_CONSENSUS_NORMAL_DIPLOTYPES = [
    ("*1/*10", {"rs1065852": "GA"}, 1.25),
    ("*1/*17", {"rs28371706": "GA"}, 1.5),
    ("*1/*29", {"rs59421388": "CT"}, 1.5),
    ("*1/*41", {"rs28371725": "CT"}, 1.5),
]


def _expected_cyp2d6_phenotype(score: float) -> str:
    if score == 0.0:
        return "Poor Metabolizer"
    if 0.0 < score < 1.25:
        return "Intermediate Metabolizer"
    if 1.25 <= score <= 2.25:
        return "Normal Metabolizer"
    return "Ultrarapid Metabolizer"


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


def test_cyp2d6_star4_star10_is_intermediate_not_poor(reference_engine: sa.Engine) -> None:
    """rs3892097=CT (*4) + rs1065852=GA (*10) -> *4/*10, Intermediate (AS 0.25)."""
    sample = _sample(rs3892097="CT", rs1065852="GA")
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"CYP2D6"}))
    (result,) = results

    assert result.diplotype == "*4/*10"
    assert result.activity_score == 0.25
    # The bug: AS 0.25 (> 0) was labeled Poor Metabolizer.
    assert result.phenotype == "Intermediate Metabolizer"
    assert result.phenotype != "Poor Metabolizer"
    # CYP2D6 is a structural-variant gene, so calls stay Partial (CNV cannot be
    # excluded) — the activity-score fix does not change that.
    assert result.call_confidence == CallConfidence.PARTIAL


def test_cyp2d6_star4_star10_routes_to_intermediate_codeine_guidance(
    reference_engine: sa.Engine,
) -> None:
    """The IM phenotype must surface IM codeine guidance, not 'avoid codeine'."""
    sample = _sample(rs3892097="CT", rs1065852="GA")
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"CYP2D6"}))
    alerts = generate_prescribing_alerts(results, reference_engine)

    codeine = [a for a in alerts if a.drug == "codeine"]
    assert codeine, "expected a CYP2D6 codeine prescribing alert"
    for alert in codeine:
        assert alert.phenotype == "Intermediate Metabolizer"
        assert "avoid" not in alert.recommendation.lower()


def test_cyp2d6_poor_metabolizer_requires_zero_activity_score() -> None:
    """Production invariant: a CYP2D6 Poor Metabolizer row has activity score 0.

    Poor Metabolizer corresponds to activity score 0 only; any nonzero
    decreased-function diplotype belongs in a higher bin (Intermediate or above).
    This biconditional guard catches the *4/*10 regression and any future
    nonzero-AS row mislabeled Poor Metabolizer.
    """
    with _DIPLOTYPES_CSV.open(newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["gene"] == "CYP2D6"]
    assert rows, "expected CYP2D6 diplotype rows in production CSV"

    for row in rows:
        score = float(row["activity_score"])
        is_poor = row["phenotype"] == "Poor Metabolizer"
        assert is_poor == (score == 0.0), (
            f"CYP2D6 {row['diplotype']}: phenotype={row['phenotype']!r} with "
            f"activity_score={score} violates Poor Metabolizer <=> AS 0"
        )


def test_cyp2d6_production_rows_follow_consensus_activity_score_bins() -> None:
    """Production invariant: CYP2D6 phenotype is deterministic from activity score.

    Caudle et al. 2019 CPIC/DPWG consensus Table 3 defines the contiguous CYP2D6
    bins as PM = 0, IM = 0 < AS < 1.25, NM = 1.25 <= AS <= 2.25, and UM > 2.25.
    This catches both the old *4/*10 PM/IM drift and the AS 1.25/1.5 IM/NM drift.
    """
    with _DIPLOTYPES_CSV.open(newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["gene"] == "CYP2D6"]
    assert rows, "expected CYP2D6 diplotype rows in production CSV"

    for row in rows:
        score = float(row["activity_score"])
        expected = _expected_cyp2d6_phenotype(score)
        assert row["phenotype"] == expected, (
            f"CYP2D6 {row['diplotype']}: phenotype={row['phenotype']!r} with "
            f"activity_score={score} violates CPIC/DPWG consensus bin {expected!r}"
        )


@pytest.mark.parametrize(
    ("expected_diplotype", "overrides", "expected_score"),
    _CYP2D6_CONSENSUS_NORMAL_DIPLOTYPES,
    ids=[diplotype for diplotype, _overrides, _score in _CYP2D6_CONSENSUS_NORMAL_DIPLOTYPES],
)
def test_cyp2d6_as_1_25_and_1_5_diplotypes_are_normal_metabolizers(
    reference_engine: sa.Engine,
    expected_diplotype: str,
    overrides: dict[str, str],
    expected_score: float,
) -> None:
    """AS 1.25/1.5 diplotypes must use Normal Metabolizer guidance (issue #1077)."""
    sample = _sample(**overrides)
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"CYP2D6"}))
    (result,) = results

    assert result.diplotype == expected_diplotype
    assert result.activity_score == expected_score
    assert result.phenotype == "Normal Metabolizer"
    assert result.phenotype != "Intermediate Metabolizer"
    assert result.call_confidence == CallConfidence.PARTIAL

    alerts = generate_prescribing_alerts(results, reference_engine)
    assert alerts, f"expected CYP2D6 prescribing alerts for {expected_diplotype}"
    assert {alert.phenotype for alert in alerts} == {"Normal Metabolizer"}

    tamoxifen = [alert for alert in alerts if alert.drug == "tamoxifen"]
    assert len(tamoxifen) == 1
    assert tamoxifen[0].recommendation == "Use label-recommended dosing."
    assert "alternative therapy" not in tamoxifen[0].recommendation.lower()
