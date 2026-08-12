"""M5 — Reference-data quality gates (live path).

Catches the reference-data and column-mapping defects that internal-consistency
checks miss. The headline is the **F31 column-mapping regression**: dbNSFP rows
are loaded from a genuine-header TSV through the production
``parse_dbnsfp_tsv_line``, so a wrong ``_FIELD_MAP`` key (``MutPred_score`` vs the
real ``MutPred2_score``) shows up as a 100 %-NULL column — exactly the bug a
pre-normalised CSV fixture hides.
"""

from __future__ import annotations

import pytest

from backend.annotation.engine import GENE_PHENOTYPE_BIT
from backend.annotation.gnomad import compute_rare_flags
from tests.backend.annotation_validation.conftest import clinvar_row

# A dbNSFP row keyed by the *real* dbNSFP TSV column names.
_DBNSFP_ROW = {
    "#chr": "7",
    "pos(1-based)": "100",
    "ref": "G",
    "alt": "A",
    "rs_dbSNP": "rs_scored",
    "CADD_phred": "31.0",
    "SIFT4G_score": "0.01",
    "SIFT4G_pred": "D",
    "Polyphen2_HVAR_score": "0.95",
    "Polyphen2_HVAR_pred": "D",
    "REVEL_score": "0.9",
    "MutPred2_score": "0.88",  # real dbNSFP header — loader currently reads MutPred_score
    "VEST4_score": "0.8",
    "MetaSVM_score": "0.7",
    "MetaLR_score": "0.7",
    "GERP++_RS": "5.0",
    "phyloP100way_vertebrate": "7.0",
    "MPC_score": "1.5",
    "PrimateAI_score": "0.8",
}


def _scored_run(build_live_run):
    return build_live_run(
        variants=[{"rsid": "rs_scored", "chrom": "7", "pos": 100, "genotype": "GA"}],
        clinvar=[clinvar_row("rs_scored", "7", 100, "G", "A", "Likely pathogenic", 2)],
        dbnsfp_rows=[_DBNSFP_ROW],
        run_analyses=False,
    )


def test_correctly_mapped_dbnsfp_scores_populate(build_live_run) -> None:
    """Sanity: correctly-mapped in-silico scores load via the real TSV path.

    Proves the harness exercises the production loader — if these were NULL the
    F31 assertion below would be meaningless.
    """
    run = _scored_run(build_live_run)
    row = run.annotated_by_rsid("rs_scored")
    assert row is not None
    assert row.cadd_phred == pytest.approx(31.0)
    assert row.revel == pytest.approx(0.9)


def test_mutpred2_column_has_coverage(build_live_run) -> None:
    """MutPred2 must populate from the genuine ``MutPred2_score`` header."""
    run = _scored_run(build_live_run)
    row = run.annotated_by_rsid("rs_scored")
    assert row is not None
    # Lock to the fixture's MutPred2_score (0.88): a wrong field-map key that
    # happened to surface a non-null fallback would otherwise slip through.
    assert row.mutpred2 == pytest.approx(0.88), (
        f"mutpred2 is {row.mutpred2!r} (expected 0.88 from MutPred2_score) — wrong field-map key"
    )


# ── F21: obsolete MONDO terms must not reach the user ─────────────────────


def test_obsolete_mondo_terms_filtered(build_live_run) -> None:
    run = build_live_run(
        variants=[{"rsid": "rs_tp53", "chrom": "17", "pos": 7_676_000, "genotype": "GA"}],
        clinvar=[clinvar_row("rs_tp53", "17", 7_676_000, "G", "A", "Pathogenic", 3, gene="TP53")],
        vep=[
            {
                "rsid": "rs_tp53",
                "chrom": "17",
                "pos": 7_676_000,
                "ref": "G",
                "alt": "A",
                "gene_symbol": "TP53",
                "consequence": "missense_variant",
            }
        ],
        gene_phenotype_rows=[
            {
                "gene_symbol": "TP53",
                "disease_name": "obsolete Li-Fraumeni syndrome 1",
                "disease_id": "MONDO:0018875",
                "hpo_terms": "[]",
                "source": "mondo_hpo",
                "inheritance": "Autosomal dominant",
            }
        ],
        run_analyses=False,
    )
    row = run.annotated_by_rsid("rs_tp53")
    assert row is not None
    label = (row.disease_name or "").lower()
    assert not label.startswith("obsolete"), f"obsolete term surfaced: {row.disease_name!r}"


# ── Disease-scoped inheritance must not be overwritten by gene ────────────


def test_source_disease_inheritance_is_not_overridden(build_live_run) -> None:
    run = build_live_run(
        variants=[{"rsid": "rs_brca1", "chrom": "17", "pos": 43_094_000, "genotype": "GA"}],
        clinvar=[
            clinvar_row("rs_brca1", "17", 43_094_000, "G", "A", "Pathogenic", 3, gene="BRCA1")
        ],
        vep=[
            {
                "rsid": "rs_brca1",
                "chrom": "17",
                "pos": 43_094_000,
                "ref": "G",
                "alt": "A",
                "gene_symbol": "BRCA1",
                "consequence": "stop_gained",
            }
        ],
        gene_phenotype_rows=[
            {
                "gene_symbol": "BRCA1",
                "disease_name": "Hereditary breast and ovarian cancer syndrome",
                "disease_id": "MONDO:0011450",
                "hpo_terms": "[]",
                "source": "mondo_hpo",
                # This synthetic source value must reach the disease row as-is;
                # a gene-wide override could be wrong for another BRCA1 disease.
                "inheritance": "Autosomal recessive",
            }
        ],
        run_analyses=False,
    )
    row = run.annotated_by_rsid("rs_brca1")
    assert row is not None
    assert row.inheritance_pattern == "Autosomal recessive"


# ── F26: AF=0 (monomorphic) is not the same as observed-ultra-rare ────────


def test_af_zero_is_not_rare_or_ultra_rare() -> None:
    """AF=0 (never observed in gnomAD) is monomorphic-reference, not rare.

    Both flags must be False — asserting only ``ultra_rare`` would miss a
    regression that re-flagged AF=0 as ``rare``.
    """
    rare, ultra_rare = compute_rare_flags(0.0)
    assert rare is False
    assert ultra_rare is False


# ── F20: 0-star ClinVar P/LP → low-confidence sub-tier, not the headline ──


def test_zero_star_pathogenic_routed_to_low_confidence_subtier(build_live_run) -> None:
    """A 0-star P/LP (no assertion criteria) must not inflate clinvar_pathogenic.

    It surfaces in the distinct ``clinvar_pathogenic_low_confidence`` sub-tier at
    evidence_level < 3 (so it never reaches the high-confidence card), while a
    well-supported (>=2-star) P/LP stays in the headline category.
    """
    run = build_live_run(
        variants=[
            {"rsid": "rs_0star", "chrom": "7", "pos": 100, "genotype": "GA"},
            {"rsid": "rs_2star", "chrom": "7", "pos": 200, "genotype": "GA"},
        ],
        clinvar=[
            clinvar_row("rs_0star", "7", 100, "G", "A", "Pathogenic", 0),
            clinvar_row("rs_2star", "7", 200, "G", "A", "Pathogenic", 2),
        ],
    )
    cats_0 = {f.category for f in run.findings_for_rsid("rs_0star")}
    cats_2 = {f.category for f in run.findings_for_rsid("rs_2star")}
    assert "clinvar_pathogenic_low_confidence" in cats_0
    assert "clinvar_pathogenic" not in cats_0
    # The 0-star finding must stay below the high-confidence (evidence>=3) card.
    assert all(
        (f.evidence_level or 0) < 3
        for f in run.findings_for_rsid("rs_0star")
        if f.category == "clinvar_pathogenic_low_confidence"
    )
    # A >=2-star P/LP keeps the headline category.
    assert "clinvar_pathogenic" in cats_2


# ── F22: gene→disease label must be gated on pathogenicity ────────────────


def test_benign_variant_does_not_inherit_gene_disease_label(build_live_run) -> None:
    """A benign variant must not carry its gene's disease association (F22).

    gene→phenotype is a *gene-level* mapping applied per-variant. Without a
    pathogenicity gate every variant in the gene — benign included — inherits
    the disease label (2,632 BRCA2 variants all labelled "breast-ovarian cancer
    susceptibility 2"), which falsely implies that specific variant is causal in
    the variant-detail drawer. A confidently-benign call must drop the label
    *and* leave the gene-phenotype coverage bit clear; a pathogenic variant in
    the same gene (positive control) must keep it.
    """
    run = build_live_run(
        variants=[
            {"rsid": "rs_benign", "chrom": "13", "pos": 32_300_000, "genotype": "GA"},
            {"rsid": "rs_path", "chrom": "13", "pos": 32_400_000, "genotype": "GA"},
        ],
        clinvar=[
            clinvar_row("rs_benign", "13", 32_300_000, "G", "A", "Benign", 2, gene="BRCA2"),
            clinvar_row("rs_path", "13", 32_400_000, "G", "A", "Pathogenic", 3, gene="BRCA2"),
        ],
        vep=[
            {
                "rsid": "rs_benign",
                "chrom": "13",
                "pos": 32_300_000,
                "ref": "G",
                "alt": "A",
                "gene_symbol": "BRCA2",
                "consequence": "synonymous_variant",
            },
            {
                "rsid": "rs_path",
                "chrom": "13",
                "pos": 32_400_000,
                "ref": "G",
                "alt": "A",
                "gene_symbol": "BRCA2",
                "consequence": "stop_gained",
            },
        ],
        gene_phenotype_rows=[
            {
                "gene_symbol": "BRCA2",
                "disease_name": "Breast-ovarian cancer, familial, susceptibility to, 2",
                "disease_id": "MONDO:0012933",
                "hpo_terms": "[]",
                "source": "mondo_hpo",
                "inheritance": "Autosomal dominant",
            }
        ],
        run_analyses=False,
    )

    benign = run.annotated_by_rsid("rs_benign")
    assert benign is not None
    assert benign.disease_name is None, (
        f"benign variant inherited gene-disease label {benign.disease_name!r} (F22)"
    )
    assert benign.disease_id is None
    assert benign.annotation_coverage & GENE_PHENOTYPE_BIT == 0

    # Positive control: a pathogenic variant in the same gene keeps the label.
    path = run.annotated_by_rsid("rs_path")
    assert path is not None
    assert path.disease_name == "Breast-ovarian cancer, familial, susceptibility to, 2"
    assert path.annotation_coverage & GENE_PHENOTYPE_BIT == GENE_PHENOTYPE_BIT
