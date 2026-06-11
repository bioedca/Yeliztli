"""CYP2C9 warfarin/phenytoin panel — production-CSV-backed regression (issue #14).

These tests load the REAL production CPIC tables (``backend/data/cpic/*.csv``)
rather than a hand-built in-memory fixture, so they validate the shipped
diplotype→phenotype mapping that the pharmacogenomics caller and the
prescribing-alert generator actually consume in production.

Regression guard for issue #14: ``cpic_alleles.csv`` defines CYP2C9 ``*5``, ``*6``,
``*8`` and ``*11`` as callable reduced/no-function alleles, but
``cpic_diplotypes.csv`` only mapped ``*1``/``*2``/``*3`` combinations. A sample
carrying e.g. ``*1/*5`` therefore resolved to ``phenotype=None`` and
``generate_prescribing_alerts`` silently skipped CYP2C9 warfarin/phenytoin
guidance — the same "dropped diplotype" defect fixed for TPMT (#5) and DPYD.

Phenotypes follow the CPIC CYP2C9 activity-score scheme (Normal function ``*1``=1,
decreased ``*2``/``*8``/``*11``=0.5, ``*3``/``*5``/``*6`` contribute 0 to the
phenotype-determining score): activity score 2 → Normal Metabolizer, 1.0–1.5 →
Intermediate Metabolizer, 0–0.5 → Poor Metabolizer. The ``activity_score`` CSV
column uses the same allele-value sum, so ``*3`` contributes 0 in both phenotype
translation and reported activity scores.

CYP2C9 ``*6`` caveat — rs9332131 is a single-base deletion. The caller can type
the allele when raw data represents the site with D/I tokens (``DI`` ->
``*1/*6``, ``DD`` -> ``*6/*6``, ``II`` -> observed reference). If rs9332131 is
absent or present in an unsupported base-coded form, ``*6`` remains
indeterminate and CYP2C9 is PARTIAL confidence (``*6`` cannot be excluded).
Alerts still fire for PARTIAL calls; only INSUFFICIENT is suppressed. All
genotypes below are GRCh37 plus/forward strand; star-allele calling is keyed on
rsid, so the chrom/pos are realistic but not load-bearing.
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

# CYP2C9 defining variants on the GRCh37 plus strand (chr10q23.33, plus strand,
# so alt is the base a carrier of the star allele has). rsid -> (chrom, pos, ref,
# alt). Positions are realistic GRCh37 coordinates but not load-bearing — calling
# is keyed on rsid. rs9332131 (*6) is a single-base deletion (GA>G).
_CYP2C9_SNP = {
    "rs1799853": ("10", 96702047, "C", "T"),  # *2   decreased function
    "rs1057910": ("10", 96741053, "A", "C"),  # *3   loss of function
    "rs28371686": ("10", 96741058, "C", "G"),  # *5   no function
    "rs7900194": ("10", 96702066, "G", "A"),  # *8   decreased function
    "rs28371685": ("10", 96740981, "C", "T"),  # *11  decreased function
}
_CYP2C9_INDEL = {"rs9332131": ("10", 96709038, "GA", "G")}  # *6 deletion

# Every diplotype row added for issue #14 -> (expected phenotype, activity_score).
# activity_score is the CPIC allele-value sum (*1=1, *2/*8/*11=0.5, *3/*5/*6=0).
_NEW_DIPLOTYPES = {
    "*1/*5": ("Intermediate Metabolizer", 1.0),
    "*2/*5": ("Poor Metabolizer", 0.5),
    "*3/*5": ("Poor Metabolizer", 0.0),
    "*5/*5": ("Poor Metabolizer", 0.0),
    "*5/*6": ("Poor Metabolizer", 0.0),
    "*5/*8": ("Poor Metabolizer", 0.5),
    "*5/*11": ("Poor Metabolizer", 0.5),
    "*1/*6": ("Intermediate Metabolizer", 1.0),
    "*2/*6": ("Poor Metabolizer", 0.5),
    "*3/*6": ("Poor Metabolizer", 0.0),
    "*6/*6": ("Poor Metabolizer", 0.0),
    "*6/*8": ("Poor Metabolizer", 0.5),
    "*6/*11": ("Poor Metabolizer", 0.5),
    "*1/*8": ("Intermediate Metabolizer", 1.5),
    "*2/*8": ("Intermediate Metabolizer", 1.0),
    "*3/*8": ("Poor Metabolizer", 0.5),
    "*8/*8": ("Intermediate Metabolizer", 1.0),
    "*8/*11": ("Intermediate Metabolizer", 1.0),
    "*1/*11": ("Intermediate Metabolizer", 1.5),
    "*2/*11": ("Intermediate Metabolizer", 1.0),
    "*3/*11": ("Poor Metabolizer", 0.5),
    "*11/*11": ("Intermediate Metabolizer", 1.0),
}

_STAR3_DIPLOTYPES = {
    **{
        diplotype: expected for diplotype, expected in _NEW_DIPLOTYPES.items() if "*3" in diplotype
    },
    "*1/*3": ("Intermediate Metabolizer", 1.0),
    "*2/*3": ("Poor Metabolizer", 0.5),
    "*3/*3": ("Poor Metabolizer", 0.0),
}


def _cyp2c9_genotypes(**overrides: str) -> dict[str, str]:
    """Plus-strand CYP2C9 SNP genotypes; defaults to homozygous reference.

    Only the five SNP positions are included by default. Pass e.g.
    ``rs28371686="CG"`` for a *5 heterozygote, or ``rs9332131="DI"`` for a
    D/I-encoded *6 deletion heterozygote.
    """
    geno = {rsid: ref * 2 for rsid, (_c, _p, ref, _a) in _CYP2C9_SNP.items()}
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


def _call_cyp2c9(reference_engine: sa.Engine, genotypes: dict[str, str]):
    alleles = _fetch_alleles_for_gene("CYP2C9", reference_engine)
    return call_star_alleles_for_gene("CYP2C9", alleles, genotypes, reference_engine)


def _make_sample(genotypes: dict[str, str]) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    positions = {**_CYP2C9_SNP, **_CYP2C9_INDEL}
    rows = [
        {"rsid": rsid, "chrom": positions[rsid][0], "pos": positions[rsid][1], "genotype": g}
        for rsid, g in genotypes.items()
    ]
    with engine.begin() as conn:
        conn.execute(raw_variants.insert(), rows)
    return engine


@pytest.mark.parametrize(("diplotype", "expected"), sorted(_NEW_DIPLOTYPES.items()))
def test_new_diplotype_rows_resolve_to_expected_phenotype(
    reference_engine: sa.Engine, diplotype: str, expected: tuple[str, float]
) -> None:
    """Every issue-#14 diplotype now resolves to a non-null CPIC phenotype.

    Before the fix these rows were absent from production cpic_diplotypes.csv, so
    the lookup returned None. This is the data-level guard covering the homozygous,
    compound-het and *6-indel combinations the array caller cannot synthesize but
    that sequencing/VCF-derived diplotypes can produce.
    """
    expected_phenotype, expected_activity = expected
    row = _fetch_diplotype_phenotype("CYP2C9", diplotype, reference_engine)
    assert row is not None, f"CYP2C9 {diplotype} has no diplotype→phenotype row"
    assert row["phenotype"] == expected_phenotype
    assert row["activity_score"] == expected_activity
    assert row["ehr_notation"] == f"CYP2C9 {expected_phenotype}"


def test_star3_allele_is_no_function(reference_engine: sa.Engine) -> None:
    """CYP2C9*3 contributes 0 to activity-score calculations."""
    star3 = next(
        allele
        for allele in _fetch_alleles_for_gene("CYP2C9", reference_engine)
        if allele["allele_name"] == "*3"
    )

    assert star3["function"] == "No function"
    assert star3["activity_score"] == 0.0


@pytest.mark.parametrize(("diplotype", "expected"), sorted(_STAR3_DIPLOTYPES.items()))
def test_star3_diplotype_rows_use_no_function_activity(
    reference_engine: sa.Engine, diplotype: str, expected: tuple[str, float]
) -> None:
    """Every shipped CYP2C9*3 diplotype uses the *3=0 activity value."""
    expected_phenotype, expected_activity = expected
    row = _fetch_diplotype_phenotype("CYP2C9", diplotype, reference_engine)

    assert row is not None, f"CYP2C9 {diplotype} has no diplotype→phenotype row"
    assert row["phenotype"] == expected_phenotype
    assert row["activity_score"] == expected_activity
    assert row["ehr_notation"] == f"CYP2C9 {expected_phenotype}"


def test_reference_is_normal_metabolizer(reference_engine: sa.Engine) -> None:
    """A plus-strand homozygous-reference CYP2C9 sample is *1/*1 Normal.

    Confidence is PARTIAL (not COMPLETE) when rs9332131 is absent, because the
    *6 deletion cannot be excluded from the observed data.
    """
    result = _call_cyp2c9(reference_engine, _cyp2c9_genotypes())
    assert result.diplotype == "*1/*1"
    assert result.phenotype == "Normal Metabolizer"
    assert result.call_confidence == CallConfidence.PARTIAL
    assert "*6" in result.indeterminate_alleles


@pytest.mark.parametrize(
    ("genotype", "diplotype", "phenotype"),
    [
        ("II", "*1/*1", "Normal Metabolizer"),
        ("DI", "*1/*6", "Intermediate Metabolizer"),
        ("DD", "*6/*6", "Poor Metabolizer"),
    ],
)
def test_star6_di_encoded_indel_is_callable(
    reference_engine: sa.Engine,
    genotype: str,
    diplotype: str,
    phenotype: str,
) -> None:
    """A D/I-encoded rs9332131 deletion is typed instead of forced indeterminate."""
    result = _call_cyp2c9(reference_engine, _cyp2c9_genotypes(rs9332131=genotype))

    assert result.diplotype == diplotype
    assert result.phenotype == phenotype
    assert result.call_confidence == CallConfidence.COMPLETE
    assert "*6" not in result.indeterminate_alleles
    if "*6" in diplotype:
        assert "rs9332131" in result.involved_rsids


@pytest.mark.parametrize(
    ("overrides", "diplotype", "phenotype", "activity_score"),
    [
        ({"rs1057910": "AC"}, "*1/*3", "Intermediate Metabolizer", 1.0),
        (
            {"rs1799853": "CT", "rs1057910": "AC"},
            "*2/*3",
            "Poor Metabolizer",
            0.5,
        ),
        ({"rs1057910": "CC"}, "*3/*3", "Poor Metabolizer", 0.0),
    ],
)
def test_star3_calls_use_no_function_activity(
    reference_engine: sa.Engine,
    overrides: dict[str, str],
    diplotype: str,
    phenotype: str,
    activity_score: float,
) -> None:
    """Caller and alert payloads report CYP2C9*3 as a no-function allele."""
    result = _call_cyp2c9(reference_engine, _cyp2c9_genotypes(**overrides))
    assert result.diplotype == diplotype
    assert result.phenotype == phenotype
    assert result.activity_score == activity_score

    sample = _make_sample(_cyp2c9_genotypes(**overrides))
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"CYP2C9"}))
    alerts = generate_prescribing_alerts(results, reference_engine)

    cyp2c9_alerts = [a for a in alerts if a.gene == "CYP2C9"]
    assert cyp2c9_alerts
    assert {a.drug for a in cyp2c9_alerts} == {"warfarin", "phenytoin"}
    for alert in cyp2c9_alerts:
        assert alert.diplotype == diplotype
        assert alert.phenotype == phenotype
        assert alert.activity_score == activity_score


@pytest.mark.parametrize(
    ("rsid", "diplotype"),
    [
        ("rs28371686", "*1/*5"),
        ("rs7900194", "*1/*8"),
        ("rs28371685", "*1/*11"),
    ],
)
def test_single_reduced_function_het_is_intermediate(
    reference_engine: sa.Engine, rsid: str, diplotype: str
) -> None:
    """Het at one SNP-typable reduced/no-function allele -> *1/*N Intermediate.

    Before issue #14 these resolved to phenotype=None despite the alleles being
    defined and callable.
    """
    ref, alt = _CYP2C9_SNP[rsid][2], _CYP2C9_SNP[rsid][3]
    result = _call_cyp2c9(reference_engine, _cyp2c9_genotypes(**{rsid: ref + alt}))
    assert result.diplotype == diplotype
    assert result.phenotype == "Intermediate Metabolizer"
    assert result.call_confidence != CallConfidence.INSUFFICIENT


def test_compound_het_star2_star5_is_poor(reference_engine: sa.Engine) -> None:
    """Het *2 (rs1799853) + het *5 (rs28371686) -> *2/*5 Poor Metabolizer."""
    result = _call_cyp2c9(
        reference_engine,
        _cyp2c9_genotypes(rs1799853="CT", rs28371686="CG"),
    )
    assert result.diplotype == "*2/*5"
    assert result.phenotype == "Poor Metabolizer"
    assert result.activity_score == 0.5
    assert result.call_confidence != CallConfidence.INSUFFICIENT


@pytest.mark.parametrize(
    ("overrides", "diplotype", "phenotype"),
    [
        ({"rs28371686": "CG"}, "*1/*5", "Intermediate Metabolizer"),
        ({"rs9332131": "DI"}, "*1/*6", "Intermediate Metabolizer"),
        ({"rs7900194": "GA"}, "*1/*8", "Intermediate Metabolizer"),
        ({"rs28371685": "CT"}, "*1/*11", "Intermediate Metabolizer"),
        ({"rs1799853": "CT", "rs28371686": "CG"}, "*2/*5", "Poor Metabolizer"),
    ],
)
def test_reduced_function_carriers_emit_warfarin_phenytoin_alerts(
    reference_engine: sa.Engine,
    overrides: dict[str, str],
    diplotype: str,
    phenotype: str,
) -> None:
    """End-to-end patient-safety guard: CYP2C9 carriers get warfarin + phenytoin
    alerts instead of being silently skipped (the issue-#14 defect)."""
    sample = _make_sample(_cyp2c9_genotypes(**overrides))
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"CYP2C9"}))
    alerts = generate_prescribing_alerts(results, reference_engine)

    cyp2c9_alerts = [a for a in alerts if a.gene == "CYP2C9"]
    assert cyp2c9_alerts, f"expected CYP2C9 alerts for {diplotype} {phenotype}"
    drugs = {a.drug for a in cyp2c9_alerts}
    assert {"warfarin", "phenytoin"} <= drugs
    expected_confidence = (
        CallConfidence.COMPLETE if "rs9332131" in overrides else CallConfidence.PARTIAL
    )
    for alert in cyp2c9_alerts:
        assert alert.diplotype == diplotype
        assert alert.phenotype == phenotype
        assert alert.call_confidence == expected_confidence
        if "rs9332131" in overrides:
            assert "*6" not in alert.indeterminate_alleles
        else:
            assert "*6" in alert.indeterminate_alleles


def test_star6_base_coded_indel_is_uncalled_and_indeterminate(
    reference_engine: sa.Engine,
) -> None:
    """Unsupported base-coded rs9332131 genotypes remain indeterminate.

    Simple deletion calls must use D/I tokens. A raw ``GG`` at the GA>G deletion
    cannot distinguish an observed reference allele from an unsupported encoding,
    so the caller flags *6 as "cannot exclude" rather than assigning it.
    """
    result = _call_cyp2c9(
        reference_engine,
        _cyp2c9_genotypes() | {"rs9332131": "GG"},
    )
    assert "*6" not in result.diplotype
    assert "*6" in result.indeterminate_alleles
    assert result.call_confidence == CallConfidence.PARTIAL
