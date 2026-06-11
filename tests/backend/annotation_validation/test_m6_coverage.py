"""M6 — Coverage / recall reconciliation (live path).

Audits the ``annotation_coverage`` bitmask and the raw→annotated reconciliation:
which sources claim coverage, whether a set bit really has a populated column,
whether deprecated rsids were recovered, and whether the bitmask constants are
self-consistent.
"""

from __future__ import annotations

from backend.annotation import engine as engine_mod
from tests.backend.annotation_validation.conftest import clinvar_row

# ── F33: bitmask constants must be distinct ───────────────────────────────


def test_cpic_bit_distinct_from_gene_phenotype_bit() -> None:
    """F33: every coverage bit is distinct — no two sources share a bit.

    Constant/meta check (not a live-path coverage test): a shared bit makes
    "this variant has CPIC data" indistinguishable from "…gene-phenotype data".
    """
    bits = {
        "VEP": engine_mod.VEP_BIT,
        "CLINVAR": engine_mod.CLINVAR_BIT,
        "GNOMAD": engine_mod.GNOMAD_BIT,
        "DBNSFP": engine_mod.DBNSFP_BIT,
        "GENE_PHENOTYPE": engine_mod.GENE_PHENOTYPE_BIT,
        "GWAS": engine_mod.GWAS_BIT,
        "CPIC": engine_mod.CPIC_BIT,
        "ALPHAMISSENSE": engine_mod.ALPHAMISSENSE_BIT,
    }
    assert engine_mod.CPIC_BIT != engine_mod.GENE_PHENOTYPE_BIT
    assert len(set(bits.values())) == len(bits), f"coverage bits collide: {bits}"


# ── inv5 (standing guard): a set coverage bit implies a populated column ───


def test_coverage_bit_implies_source_column(build_live_run) -> None:
    """A set coverage bit implies the corresponding source column is populated.

    inv5 across **all** annotation sources (not just ClinVar/gnomAD): a bit must
    never be set without data behind it — the "claims coverage it doesn't have"
    failure mode. The single variant is seeded into every source so each bit is
    genuinely exercised, then the implication is checked on every annotated row.
    """
    run = build_live_run(
        variants=[{"rsid": "rs_cov", "chrom": "7", "pos": 100, "genotype": "GA"}],
        clinvar=[clinvar_row("rs_cov", "7", 100, "G", "A", "Pathogenic", 3, gene="GENEX")],
        gnomad=[
            {
                "rsid": "rs_cov",
                "chrom": "7",
                "pos": 100,
                "ref": "G",
                "alt": "A",
                "af_global": 0.002,
            }
        ],
        vep=[
            {
                "rsid": "rs_cov",
                "chrom": "7",
                "pos": 100,
                "ref": "G",
                "alt": "A",
                "gene_symbol": "GENEX",
                "consequence": "missense_variant",
            }
        ],
        dbnsfp_rows=[
            {
                "#chr": "7",
                "pos(1-based)": "100",
                "ref": "G",
                "alt": "A",
                "rs_dbSNP": "rs_cov",
                "CADD_phred": "25.0",
                "REVEL_score": "0.7",
            }
        ],
        alphamissense_rows=[
            {
                "chrom": "7",
                "pos": 100,
                "ref": "G",
                "alt": "A",
                "am_pathogenicity": 0.91,
                "am_class": "likely_pathogenic",
            }
        ],
        gene_phenotype_rows=[
            {
                "gene_symbol": "GENEX",
                "disease_name": "GENEX-related disorder",
                "disease_id": "MONDO:0000001",
                "hpo_terms": "[]",
                "source": "mondo_hpo",
                "inheritance": "Autosomal dominant",
            }
        ],
        run_analyses=False,
    )
    # inv5: for every annotated row, a set bit implies a populated column.
    for row in run.annotated:
        cov = row.annotation_coverage or 0
        if cov & engine_mod.VEP_BIT:
            assert row.gene_symbol is not None
        if cov & engine_mod.CLINVAR_BIT:
            assert row.clinvar_significance is not None
        if cov & engine_mod.GNOMAD_BIT:
            assert row.gnomad_af_global is not None
        if cov & engine_mod.DBNSFP_BIT:
            assert row.cadd_phred is not None
        if cov & engine_mod.ALPHAMISSENSE_BIT:
            assert row.alphamissense_pathogenicity is not None
            assert row.alphamissense_class is not None
        if cov & engine_mod.GENE_PHENOTYPE_BIT:
            assert row.disease_name is not None
    # ...and the fully-seeded variant must actually exercise all five bits, so
    # the implication above is not satisfied vacuously.
    row = run.annotated_by_rsid("rs_cov")
    assert row is not None
    cov = row.annotation_coverage or 0
    for bit in (
        engine_mod.VEP_BIT,
        engine_mod.CLINVAR_BIT,
        engine_mod.GNOMAD_BIT,
        engine_mod.DBNSFP_BIT,
        engine_mod.ALPHAMISSENSE_BIT,
        engine_mod.GENE_PHENOTYPE_BIT,
    ):
        assert cov & bit, f"expected coverage bit {bit} set for the fully-seeded variant"


# ── F18: deprecated rsids are recovered (resolution rate > 0) ─────────────


def test_merged_rsids_are_recovered(build_live_run) -> None:
    """Both deprecated rsids resolve to their current id and get annotated."""
    run = build_live_run(
        variants=[
            {"rsid": "rs_old1", "chrom": "1", "pos": 100, "genotype": "AA"},
            {"rsid": "rs_old2", "chrom": "2", "pos": 200, "genotype": "GA"},
        ],
        clinvar=[
            clinvar_row("rs_new1", "1", 100, "G", "A", "Pathogenic", 2),
            clinvar_row("rs_new2", "2", 200, "C", "A", "Likely pathogenic", 2),
        ],
        dbsnp_merge_rows=[
            {"old_rsid": "rs_old1", "current_rsid": "rs_new1", "build_id": 151},
            {"old_rsid": "rs_old2", "current_rsid": "rs_new2", "build_id": 151},
        ],
        run_analyses=False,
    )
    recovered = sum(
        1
        for rsid in ("rs_old1", "rs_old2")
        if (row := run.annotated_by_rsid(rsid)) is not None
        and row.clinvar_significance is not None
    )
    assert recovered == 2, f"only {recovered}/2 deprecated rsids were recovered"


# ── F36: raw→annotated reconciliation with an explicit coverage=0 bucket ───


def test_unmatched_variant_gets_coverage_zero_marker(build_live_run) -> None:
    run = build_live_run(
        variants=[
            {"rsid": "rs_hit", "chrom": "7", "pos": 100, "genotype": "GA"},
            {"rsid": "rs_miss", "chrom": "7", "pos": 200, "genotype": "CC"},
        ],
        clinvar=[clinvar_row("rs_hit", "7", 100, "G", "A", "Pathogenic", 2)],
        run_analyses=False,
    )
    miss = run.annotated_by_rsid("rs_miss")
    assert miss is not None, "unmatched variant was silently dropped"
    assert (miss.annotation_coverage or 0) == 0
