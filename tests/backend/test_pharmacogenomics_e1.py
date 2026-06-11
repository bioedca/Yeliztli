"""SW-E1 panel expansion and PGx regressions, production-CSV-backed.

Validates the shipped ``backend/data/cpic/*.csv`` definitions the caller and
prescribing-alert generator actually consume: NUDT15 thiopurine metabolizer
calls, UGT1A1 irinotecan/atazanavir calls, CYP3A5 tacrolimus calls, and the
explicit *indeterminate* flag for the UGT1A1*28 TATA-box TA-repeat (which a SNP
array cannot type). All genotypes are GRCh37 plus/forward strand (as real
23andMe data is); simple indels use the parser's canonical D/I tokens.
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
from backend.annotation.cpic import (
    CPIC_GENES,
    download_and_load_cpic,
    load_cpic_from_csvs,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants, reference_metadata

_CPIC_DIR = Path(__file__).resolve().parents[2] / "backend" / "data" / "cpic"

# Plus-strand defining variants (match cpic_alleles.csv + test_cpic_allele_strand).
_NUDT15_RS = "rs116855232"  # *3 c.415C>T No function; ref=C alt=T
_UGT1A1_6 = "rs4148323"  # *6 c.211G>A Decreased; ref=G alt=A
_UGT1A1_28 = "rs8175347"  # *28 TA-repeat (non-SNV) — not array-typeable
_CYP3A5 = {
    "rs776746": "T",  # *3
    "rs10264272": "C",  # *6
    "rs41303343": "I",  # *7 deletion, reference allele is insertion/present sequence
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


def _call(gene: str, genotypes: dict[str, str], reference_engine: sa.Engine):
    alleles = _fetch_alleles_for_gene(gene, reference_engine)
    return call_star_alleles_for_gene(gene, alleles, genotypes, reference_engine)


def _cyp3a5_genotypes(**overrides: str) -> dict[str, str]:
    geno = {rsid: ref * 2 for rsid, ref in _CYP3A5.items()}
    geno.update(overrides)
    return geno


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


# ── NUDT15 (thiopurines) ──────────────────────────────────────────────────────


def test_nudt15_added_to_panel() -> None:
    assert "NUDT15" in CPIC_GENES


def test_nudt15_reference_is_normal(reference_engine: sa.Engine) -> None:
    result = _call("NUDT15", {_NUDT15_RS: "CC"}, reference_engine)
    assert result.diplotype == "*1/*1"
    assert result.phenotype == "Normal Metabolizer"
    assert result.call_confidence == CallConfidence.COMPLETE


def test_nudt15_het_is_intermediate(reference_engine: sa.Engine) -> None:
    result = _call("NUDT15", {_NUDT15_RS: "CT"}, reference_engine)
    assert result.diplotype == "*1/*3"
    assert result.phenotype == "Intermediate Metabolizer"


def test_nudt15_hom_is_poor_with_thiopurine_alerts(reference_engine: sa.Engine) -> None:
    sample = _make_sample({_NUDT15_RS: "TT"})
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"NUDT15"}))
    nudt15 = next(r for r in results if r.gene == "NUDT15")
    assert nudt15.diplotype == "*3/*3"
    assert nudt15.phenotype == "Poor Metabolizer"

    alerts = generate_prescribing_alerts(results, reference_engine)
    drugs = {a.drug for a in alerts if a.gene == "NUDT15"}
    assert {"azathioprine", "mercaptopurine"} <= drugs


# ── UGT1A1 (irinotecan / atazanavir) + explicit indeterminate flag ────────────


def test_ugt1a1_star6_is_intermediate(reference_engine: sa.Engine) -> None:
    # rs4148323 het, *28 TA-repeat not assayed → *1/*6 Intermediate.
    result = _call("UGT1A1", {_UGT1A1_6: "GA"}, reference_engine)
    assert result.diplotype == "*1/*6"
    assert result.phenotype == "Intermediate Metabolizer"


def test_ugt1a1_star28_is_indeterminate_when_unassayed(reference_engine: sa.Engine) -> None:
    # The TA-repeat *28 cannot be typed from a SNP array: it must be flagged
    # indeterminate (cannot be excluded), not silently called as reference.
    result = _call("UGT1A1", {_UGT1A1_6: "GG"}, reference_engine)  # *28 omitted
    assert "*28" in result.indeterminate_alleles
    assert "*28" in result.confidence_note
    assert "Cannot exclude" in result.confidence_note
    # SNP-typeable part is still reference, but the call is provisional.
    assert result.call_confidence == CallConfidence.PARTIAL


def test_ugt1a1_uncallable_repeat_genotype_is_indeterminate(reference_engine: sa.Engine) -> None:
    # Even if the array reports *something* at the repeat, a multi-base/indel
    # genotype is uncallable → *28 still indeterminate (not a confident exclusion).
    result = _call("UGT1A1", {_UGT1A1_6: "GG", _UGT1A1_28: "TA6TA7"}, reference_engine)
    assert "*28" in result.indeterminate_alleles


def test_ugt1a1_star6_hom_is_poor_with_irinotecan_alert(reference_engine: sa.Engine) -> None:
    sample = _make_sample({_UGT1A1_6: "AA"})
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"UGT1A1"}))
    ugt = next(r for r in results if r.gene == "UGT1A1")
    assert ugt.diplotype == "*6/*6"
    assert ugt.phenotype == "Poor Metabolizer"
    assert "*28" in ugt.indeterminate_alleles  # repeat still unassessed

    alerts = generate_prescribing_alerts(results, reference_engine)
    ugt_drugs = {a.drug for a in alerts if a.gene == "UGT1A1"}
    assert {"irinotecan", "atazanavir"} <= ugt_drugs
    # The structured indeterminate flag propagates to the prescribing alert.
    for a in alerts:
        if a.gene == "UGT1A1":
            assert "*28" in a.indeterminate_alleles


# ── CYP3A5 (tacrolimus) + typed *7 deletion ──────────────────────────────────


def test_cyp3a5_star7_het_is_intermediate(reference_engine: sa.Engine) -> None:
    result = _call(
        "CYP3A5",
        _cyp3a5_genotypes(rs41303343="DI"),
        reference_engine,
    )
    assert result.diplotype == "*1/*7"
    assert result.phenotype == "Intermediate Metabolizer"
    assert result.call_confidence == CallConfidence.COMPLETE
    assert "rs41303343" in result.involved_rsids
    assert "rs41303343" not in result.uncalled_rsids


@pytest.mark.parametrize(
    ("overrides", "diplotype", "phenotype"),
    [
        ({"rs776746": "TC", "rs41303343": "DI"}, "*3/*7", "Poor Metabolizer"),
        ({"rs10264272": "TT"}, "*6/*6", "Poor Metabolizer"),
        ({"rs10264272": "CT", "rs41303343": "DI"}, "*6/*7", "Poor Metabolizer"),
        ({"rs41303343": "DD"}, "*7/*7", "Poor Metabolizer"),
    ],
)
def test_cyp3a5_no_function_star7_diplotypes_are_mapped(
    reference_engine: sa.Engine,
    overrides: dict[str, str],
    diplotype: str,
    phenotype: str,
) -> None:
    result = _call("CYP3A5", _cyp3a5_genotypes(**overrides), reference_engine)
    assert result.diplotype == diplotype
    assert result.phenotype == phenotype


def test_cyp3a5_star7_het_emits_tacrolimus_alert(reference_engine: sa.Engine) -> None:
    sample = _make_sample(_cyp3a5_genotypes(rs41303343="DI"))
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"CYP3A5"}))
    cyp3a5 = next(r for r in results if r.gene == "CYP3A5")
    assert cyp3a5.diplotype == "*1/*7"
    assert cyp3a5.phenotype == "Intermediate Metabolizer"

    alerts = generate_prescribing_alerts(results, reference_engine)
    tac = [a for a in alerts if a.gene == "CYP3A5" and a.drug == "tacrolimus"]
    assert tac
    assert tac[0].diplotype == "*1/*7"
    assert tac[0].phenotype == "Intermediate Metabolizer"


def test_cyp3a5_star7_non_carrier_does_not_emit_star7_alert(
    reference_engine: sa.Engine,
) -> None:
    sample = _make_sample(_cyp3a5_genotypes(rs41303343="II"))
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"CYP3A5"}))
    cyp3a5 = next(r for r in results if r.gene == "CYP3A5")
    assert cyp3a5.diplotype == "*1/*1"
    assert "*7" not in cyp3a5.diplotype

    alerts = generate_prescribing_alerts(results, reference_engine)
    tac = [a for a in alerts if a.gene == "CYP3A5" and a.drug == "tacrolimus"]
    assert tac
    assert all("*7" not in a.diplotype for a in tac)


# ── PharmVar versioning ───────────────────────────────────────────────────────


def test_pharmvar_version_recorded_on_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from backend.db.manifest import reset_cache
    from backend.db.update_manager import get_current_version

    # Point the manifest pin lookup at the in-repo manifest (offline/deterministic).
    repo_manifest = Path(__file__).resolve().parents[2] / "bundles" / "manifest.json"
    expected_pharmvar = json.loads(repo_manifest.read_text())["pipeline_pins"]["pharmvar"][
        "last_known_version"
    ]
    monkeypatch.setenv("YELIZTLI_MANIFEST_PATH", str(repo_manifest))
    reset_cache()
    try:
        engine = sa.create_engine("sqlite://")
        reference_metadata.create_all(engine)
        download_and_load_cpic(engine, tmp_path)
        # Both CPIC and the PharmVar definition-source version (from the manifest pin)
        # are tracked in database_versions.
        assert get_current_version(engine, "cpic") is not None
        assert get_current_version(engine, "pharmvar") == expected_pharmvar
    finally:
        reset_cache()
