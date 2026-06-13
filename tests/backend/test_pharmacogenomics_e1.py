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
_NUDT15_R139H = "rs147390019"  # *4 c.416G>A No function; ref=G alt=A (#39)
_NUDT15_V18I = "rs186364861"  # *5 c.52G>A Decreased function; ref=G alt=A
_NUDT15_NON_SNV = "rs746071566"  # *3.002/*6 insertion and *9 deletion
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


def _nudt15_genotypes(**overrides: str) -> dict[str, str]:
    # Defaults represent a SNP-array-style NUDT15 sample: all SNV-defined alleles
    # are typed as reference, while the rs746071566 non-SNV alleles are absent and
    # therefore remain indeterminate.
    geno = {
        _NUDT15_RS: "CC",
        _NUDT15_R139H: "GG",
        _NUDT15_V18I: "GG",
    }
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
    # All SNV-defined positions are reference, so the SNP-callable diplotype is
    # *1/*1. The rs746071566 non-SNV alleles still cannot be excluded from array
    # data, so the result remains provisional.
    result = _call("NUDT15", _nudt15_genotypes(), reference_engine)
    assert result.diplotype == "*1/*1"
    assert result.phenotype == "Normal Metabolizer"
    assert result.call_confidence == CallConfidence.PARTIAL
    assert set(result.indeterminate_alleles) == {"*3.002", "*6", "*9"}


def test_nudt15_non_carrier_alerts_are_reference_only(reference_engine: sa.Engine) -> None:
    sample = _make_sample(_nudt15_genotypes())
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"NUDT15"}))
    nudt15 = next(r for r in results if r.gene == "NUDT15")
    assert nudt15.diplotype == "*1/*1"
    assert nudt15.phenotype == "Normal Metabolizer"

    alerts = generate_prescribing_alerts(results, reference_engine)
    nudt15_alerts = [a for a in alerts if a.gene == "NUDT15"]
    # Thiopurine alerts now cover all three CPIC thiopurines incl. thioguanine (#224).
    assert {a.drug for a in nudt15_alerts} == {"azathioprine", "mercaptopurine", "thioguanine"}
    for alert in nudt15_alerts:
        assert alert.diplotype == "*1/*1"
        assert alert.phenotype == "Normal Metabolizer"
        assert alert.recommendation == "Use label-recommended dosing."


def test_nudt15_normal_cannot_exclude_star4_when_unassayed(reference_engine: sa.Engine) -> None:
    # rs116855232 reference but the *4 defining variant (rs147390019) is off-chip:
    # the *1 fill is an assumption, so *4 must be flagged indeterminate and the
    # call downgraded — never a silent confident Normal. Regression for issue #39.
    result = _call("NUDT15", {_NUDT15_RS: "CC"}, reference_engine)
    assert result.diplotype == "*1/*1"
    assert "*4" in result.indeterminate_alleles
    assert result.call_confidence != CallConfidence.COMPLETE


def test_nudt15_het_is_intermediate(reference_engine: sa.Engine) -> None:
    result = _call(
        "NUDT15",
        _nudt15_genotypes(**{_NUDT15_RS: "CT"}),
        reference_engine,
    )
    assert result.diplotype == "*1/*3"
    assert result.phenotype == "Intermediate Metabolizer"


def test_nudt15_hom_is_poor_with_thiopurine_alerts(reference_engine: sa.Engine) -> None:
    sample = _make_sample(_nudt15_genotypes(**{_NUDT15_RS: "TT"}))
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"NUDT15"}))
    nudt15 = next(r for r in results if r.gene == "NUDT15")
    assert nudt15.diplotype == "*3/*3"
    assert nudt15.phenotype == "Poor Metabolizer"

    alerts = generate_prescribing_alerts(results, reference_engine)
    drugs = {a.drug for a in alerts if a.gene == "NUDT15"}
    assert {"azathioprine", "mercaptopurine"} <= drugs


def test_nudt15_poor_emits_thioguanine_alert(reference_engine: sa.Engine) -> None:
    """A NUDT15 *3/*3 Poor Metabolizer surfaces a thioguanine alert (issue #224).

    CPIC's thiopurine/NUDT15 guideline (Relling et al. Clin Pharmacol Ther 2019,
    PMID 30447069) covers thioguanine alongside azathioprine and mercaptopurine.
    Before #224 the shipped cpic_guidelines.csv had no NUDT15 thioguanine rows, so
    a NUDT15-deficient patient prescribed thioguanine got no dose-reduction
    warning. The alert must fire exactly once and carry its CPIC text verbatim.
    """
    sample = _make_sample(_nudt15_genotypes(**{_NUDT15_RS: "TT"}))
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"NUDT15"}))
    nudt15 = next(r for r in results if r.gene == "NUDT15")
    assert nudt15.diplotype == "*3/*3"
    assert nudt15.phenotype == "Poor Metabolizer"

    alerts = generate_prescribing_alerts(results, reference_engine)
    thioguanine = [a for a in alerts if a.gene == "NUDT15" and a.drug == "thioguanine"]
    assert len(thioguanine) == 1, "expected one NUDT15 thioguanine alert for *3/*3"
    alert = thioguanine[0]
    assert alert.diplotype == "*3/*3"
    assert alert.phenotype == "Poor Metabolizer"
    assert alert.recommendation == (
        "Drastically reduce starting dose or select an alternative agent; "
        "monitor for myelosuppression."
    )


def test_nudt15_star4_het_is_intermediate(reference_engine: sa.Engine) -> None:
    # rs147390019 het (*4 R139H, No function) with rs116855232 reference → *1/*4 IM.
    # Regression for issue #39: a non-*3 actionable allele is now callable rather
    # than silently reported as *1/*1 Normal.
    result = _call(
        "NUDT15",
        _nudt15_genotypes(**{_NUDT15_R139H: "GA"}),
        reference_engine,
    )
    assert result.diplotype == "*1/*4"
    assert result.phenotype == "Intermediate Metabolizer"
    assert result.call_confidence == CallConfidence.PARTIAL


def test_nudt15_star4_hom_is_poor_with_thiopurine_alerts(reference_engine: sa.Engine) -> None:
    sample = _make_sample(_nudt15_genotypes(**{_NUDT15_R139H: "AA"}))
    results = call_all_star_alleles(reference_engine, sample, genes=frozenset({"NUDT15"}))
    nudt15 = next(r for r in results if r.gene == "NUDT15")
    assert nudt15.diplotype == "*4/*4"
    assert nudt15.phenotype == "Poor Metabolizer"

    alerts = generate_prescribing_alerts(results, reference_engine)
    drugs = {a.drug for a in alerts if a.gene == "NUDT15"}
    assert {"azathioprine", "mercaptopurine"} <= drugs


def test_nudt15_star3_star4_compound_het_is_poor(reference_engine: sa.Engine) -> None:
    # No-function / no-function across two distinct alleles → Poor Metabolizer.
    result = _call(
        "NUDT15",
        _nudt15_genotypes(**{_NUDT15_RS: "CT", _NUDT15_R139H: "GA"}),
        reference_engine,
    )
    assert result.diplotype == "*3/*4"
    assert result.phenotype == "Poor Metabolizer"
    assert result.call_confidence == CallConfidence.PARTIAL
    assert "unphased" in result.confidence_note


def test_nudt15_remaining_pharmvar_alleles_are_bundled(
    reference_engine: sa.Engine,
) -> None:
    alleles = {a["allele_name"]: a for a in _fetch_alleles_for_gene("NUDT15", reference_engine)}
    assert {"*3.002", "*5", "*6", "*9"} <= set(alleles)
    assert alleles["*3.002"]["function"] == "No function"
    assert alleles["*3.002"]["defining_variants"] == [
        {"rsid": _NUDT15_NON_SNV, "ref": "G", "alt": "GGAGTCG"},
        {"rsid": _NUDT15_RS, "ref": "C", "alt": "T"},
    ]
    assert alleles["*5"]["function"] == "Decreased function"
    assert alleles["*6"]["function"] == "No function"
    assert alleles["*9"]["function"] == "No function"


def test_nudt15_star5_het_uses_current_cpic_normal_label(
    reference_engine: sa.Engine,
) -> None:
    # Current CPIC publishes NUDT15 *1/*5 as Normal Metabolizer even though *5
    # itself is a decreased-function allele.
    result = _call(
        "NUDT15",
        _nudt15_genotypes(**{_NUDT15_V18I: "GA"}),
        reference_engine,
    )
    assert result.diplotype == "*1/*5"
    assert result.phenotype == "Normal Metabolizer"
    assert result.activity_score == 1.5
    assert result.call_confidence == CallConfidence.PARTIAL
    assert {"*3.002", "*6", "*9"} <= set(result.indeterminate_alleles)


def test_nudt15_star5_hom_is_intermediate(reference_engine: sa.Engine) -> None:
    result = _call(
        "NUDT15",
        _nudt15_genotypes(**{_NUDT15_V18I: "AA"}),
        reference_engine,
    )
    assert result.diplotype == "*5/*5"
    assert result.phenotype == "Intermediate Metabolizer"
    assert result.activity_score == 1.0


def test_nudt15_star3_star5_is_poor(reference_engine: sa.Engine) -> None:
    result = _call(
        "NUDT15",
        _nudt15_genotypes(**{_NUDT15_RS: "CT", _NUDT15_V18I: "GA"}),
        reference_engine,
    )
    assert result.diplotype == "*3/*5"
    assert result.phenotype == "Poor Metabolizer"
    assert result.activity_score == 0.5


def test_nudt15_legacy_star2_marker_is_phase_caveated(
    reference_engine: sa.Engine,
) -> None:
    # PharmVar's legacy *2 haplotype is now NUDT15*3.002. With unphased array
    # data, heterozygous rs116855232 plus heterozygous rs746071566 cannot
    # distinguish cis *1/*3.002 from trans *3/*6.
    result = _call(
        "NUDT15",
        _nudt15_genotypes(**{_NUDT15_RS: "CT", _NUDT15_NON_SNV: "DI"}),
        reference_engine,
    )
    assert result.diplotype == "*1/*3"
    assert result.phenotype == "Intermediate Metabolizer"
    assert result.call_confidence == CallConfidence.PARTIAL
    assert {"*3.002", "*6", "*9"} <= set(result.indeterminate_alleles)
    assert "NUDT15*3.002" in result.confidence_note
    assert "legacy *2" in result.confidence_note
    assert "*3/*6" in result.confidence_note
    assert "Poor Metabolizer" in result.confidence_note


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
    ("overrides", "diplotype", "phenotype", "expected_confidence"),
    [
        (
            {"rs776746": "TC", "rs41303343": "DI"},
            "*3/*7",
            "Poor Metabolizer",
            CallConfidence.PARTIAL,
        ),
        ({"rs10264272": "TT"}, "*6/*6", "Poor Metabolizer", CallConfidence.COMPLETE),
        (
            {"rs10264272": "CT", "rs41303343": "DI"},
            "*6/*7",
            "Poor Metabolizer",
            CallConfidence.PARTIAL,
        ),
        ({"rs41303343": "DD"}, "*7/*7", "Poor Metabolizer", CallConfidence.COMPLETE),
    ],
)
def test_cyp3a5_no_function_star7_diplotypes_are_mapped(
    reference_engine: sa.Engine,
    overrides: dict[str, str],
    diplotype: str,
    phenotype: str,
    expected_confidence: CallConfidence,
) -> None:
    result = _call("CYP3A5", _cyp3a5_genotypes(**overrides), reference_engine)
    assert result.diplotype == diplotype
    assert result.phenotype == phenotype
    assert result.call_confidence == expected_confidence
    if expected_confidence == CallConfidence.PARTIAL:
        assert "unphased" in result.confidence_note


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
