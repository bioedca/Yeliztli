"""Tests for rare variant finder module (P3-28).

Covers:
  - T3-28: Rare variant finder returns only variants matching all filter criteria
  - AF threshold filtering (rare, ultra-rare, novel)
  - Gene panel filtering
  - Consequence type filtering
  - ClinVar significance filtering
  - Combined filter logic (AND)
  - Evidence level assignment
  - Findings storage (module='rare_variants')
  - Empty results
  - Sorting order (ClinVar P/LP first, then AF ascending)
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from _carriage_fixtures import het_pathogenic_row, hom_ref_pathogenic_row

from backend.analysis.rare_variant_finder import (
    DEFAULT_AF_THRESHOLD,
    HIGH_IMPACT_CONSEQUENCES,
    RareVariantFilter,
    RareVariantFinderResult,
    RareVariantResult,
    _assign_evidence_level,
    find_rare_variants,
    store_rare_variant_findings,
)
from backend.annotation.vep_bundle import CONSEQUENCE_SEVERITY
from backend.db.tables import annotated_variants, findings

# ── Test variant fixtures ─────────────────────────────────────────────────


_DEFAULTS = {
    "rsid": None,
    "chrom": None,
    "pos": None,
    "genotype": None,
    "zygosity": None,
    "gene_symbol": None,
    "consequence": None,
    "hgvs_coding": None,
    "hgvs_protein": None,
    "gnomad_af_global": None,
    "gnomad_af_afr": None,
    "gnomad_af_amr": None,
    "gnomad_af_eas": None,
    "gnomad_af_eur": None,
    "gnomad_af_fin": None,
    "gnomad_af_sas": None,
    "gnomad_af_popmax": None,
    "clinvar_significance": None,
    "clinvar_review_stars": None,
    "clinvar_accession": None,
    "clinvar_conditions": None,
    "cadd_phred": None,
    "revel": None,
    "ensemble_pathogenic": False,
    "evidence_conflict": False,
    "annotation_coverage": 0,
    "disease_name": None,
    "inheritance_pattern": None,
}


def _v(**overrides: object) -> dict:
    """Create a variant dict with defaults filled in."""
    return {**_DEFAULTS, **overrides}


RARE_VARIANT_FIXTURES = [
    # 1) ClinVar Pathogenic, rare (AF=0.0005), missense — should match default
    _v(
        rsid="rs100001",
        chrom="17",
        pos=43091983,
        genotype="CT",
        zygosity="het",
        gene_symbol="BRCA1",
        consequence="missense_variant",
        hgvs_coding="c.5123A>G",
        hgvs_protein="p.Asp1708Gly",
        gnomad_af_global=0.0005,
        clinvar_significance="Pathogenic",
        clinvar_review_stars=3,
        clinvar_accession="VCV000017661",
        clinvar_conditions="Hereditary breast and ovarian cancer",
        cadd_phred=35.0,
        revel=0.95,
        ensemble_pathogenic=True,
        annotation_coverage=15,
        disease_name="Hereditary breast and ovarian cancer syndrome",
        inheritance_pattern="AD",
    ),
    # 2) Ultra-rare, stop_gained, no ClinVar — should match default
    _v(
        rsid="rs100002",
        chrom="7",
        pos=55191822,
        genotype="AG",
        zygosity="het",
        gene_symbol="EGFR",
        consequence="stop_gained",
        gnomad_af_global=0.00001,
        ensemble_pathogenic=True,
        annotation_coverage=12,
        cadd_phred=40.0,
        revel=0.98,
    ),
    # 3) Novel variant (no gnomAD), frameshift — should match with include_novel
    _v(
        rsid="rs100003",
        chrom="3",
        pos=37053568,
        genotype="AG",
        zygosity="het",
        gene_symbol="MLH1",
        consequence="frameshift_variant",
        clinvar_significance="Likely pathogenic",
        clinvar_review_stars=1,
        clinvar_accession="VCV000036555",
        clinvar_conditions="Lynch syndrome",
        annotation_coverage=10,
        cadd_phred=33.0,
    ),
    # 4) Common variant (AF=0.15) — should NOT match default AF filter
    _v(
        rsid="rs100004",
        chrom="1",
        pos=11856378,
        genotype="AG",
        zygosity="het",
        gene_symbol="MTHFR",
        consequence="missense_variant",
        gnomad_af_global=0.15,
        clinvar_significance="Benign",
        clinvar_review_stars=2,
        annotation_coverage=15,
    ),
    # 5) Low-frequency (AF=0.03) — should NOT match at default 0.01 threshold
    _v(
        rsid="rs100005",
        chrom="22",
        pos=19963748,
        genotype="AG",
        zygosity="het",
        gene_symbol="COMT",
        consequence="synonymous_variant",
        gnomad_af_global=0.03,
        annotation_coverage=12,
    ),
    # 6) Rare intronic variant — should match AF filter but not if consequence filtered
    _v(
        rsid="rs100006",
        chrom="11",
        pos=108098576,
        genotype="CT",
        zygosity="het",
        gene_symbol="ATM",
        consequence="intron_variant",
        gnomad_af_global=0.005,
        annotation_coverage=4,
    ),
    # 7) Rare VUS — should match AF but only with appropriate ClinVar filter
    _v(
        rsid="rs100007",
        chrom="13",
        pos=32913055,
        genotype="AG",
        zygosity="hom_alt",
        gene_symbol="BRCA2",
        consequence="missense_variant",
        gnomad_af_global=0.002,
        clinvar_significance="Uncertain_significance",
        clinvar_review_stars=1,
        clinvar_accession="VCV000099999",
        clinvar_conditions="not specified",
        evidence_conflict=True,
        annotation_coverage=15,
    ),
    # 8) Rare splice variant, ClinVar LP, 0 stars
    _v(
        rsid="rs100008",
        chrom="5",
        pos=112175770,
        genotype="TG",
        zygosity="het",
        gene_symbol="APC",
        consequence="splice_acceptor_variant",
        gnomad_af_global=0.0001,
        clinvar_significance="Likely pathogenic",
        clinvar_review_stars=0,
        clinvar_accession="VCV000012999",
        clinvar_conditions="Familial adenomatous polyposis",
        ensemble_pathogenic=True,
        annotation_coverage=15,
    ),
]


@pytest.fixture()
def sample_with_rare_variants(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine with annotated variants for rare variant finder testing."""
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(annotated_variants), RARE_VARIANT_FIXTURES)
    return sample_engine


@pytest.fixture()
def empty_sample(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine with no annotated variants."""
    return sample_engine


class TestPopmaxRarity:
    """F15: the finder judges rarity on popmax, falling back to global AF."""

    def _insert(self, engine: sa.Engine, rows: list[dict]) -> None:
        with engine.begin() as conn:
            conn.execute(sa.insert(annotated_variants), rows)

    def test_ancestry_common_variant_excluded(self, sample_engine: sa.Engine) -> None:
        self._insert(
            sample_engine,
            [
                # Rare globally (0.0005) but common in one ancestry (popmax 0.02) → excluded.
                _v(
                    rsid="rs_anc_common",
                    chrom="1",
                    pos=1000,
                    genotype="AG",
                    zygosity="het",
                    gene_symbol="GENEA",
                    consequence="missense_variant",
                    gnomad_af_global=0.0005,
                    gnomad_af_popmax=0.02,
                    annotation_coverage=4,
                ),
                # Rare in every population (popmax 0.0005) → included.
                _v(
                    rsid="rs_truly_rare",
                    chrom="1",
                    pos=2000,
                    genotype="AG",
                    zygosity="het",
                    gene_symbol="GENEB",
                    consequence="missense_variant",
                    gnomad_af_global=0.0005,
                    gnomad_af_popmax=0.0005,
                    annotation_coverage=4,
                ),
            ],
        )
        result = find_rare_variants(RareVariantFilter(), sample_engine)
        rsids = {v.rsid for v in result.variants}
        assert "rs_truly_rare" in rsids
        assert "rs_anc_common" not in rsids

    def test_null_popmax_falls_back_to_global(self, sample_engine: sa.Engine) -> None:
        # Un-reannotated row: popmax NULL but global rare → still surfaced.
        self._insert(
            sample_engine,
            [
                _v(
                    rsid="rs_legacy_rare",
                    chrom="1",
                    pos=3000,
                    genotype="AG",
                    zygosity="het",
                    gene_symbol="GENEC",
                    consequence="missense_variant",
                    gnomad_af_global=0.0005,
                    gnomad_af_popmax=None,
                    annotation_coverage=4,
                ),
            ],
        )
        result = find_rare_variants(RareVariantFilter(), sample_engine)
        assert "rs_legacy_rare" in {v.rsid for v in result.variants}


# ── Default filter tests ──────────────────────────────────────────────────


class TestDefaultFilter:
    """Test rare variant finder with default filters (AF < 0.01, include novel)."""

    def test_finds_rare_variants(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter()
        result = find_rare_variants(filters, sample_with_rare_variants)
        # Should find: rs100001 (0.0005), rs100002 (0.00001), rs100003 (novel),
        #   rs100006 (0.005), rs100007 (0.002), rs100008 (0.0001)
        # Should NOT find: rs100004 (0.15), rs100005 (0.03)
        assert result.count == 6

    def test_carried_only_excludes_hom_ref_pathogenic(self, sample_engine: sa.Engine) -> None:
        """A hom_ref (non-carrier) Pathogenic variant is not surfaced.

        Negative control locking the carriage gate (PR #320): with
        ``carried_only=True`` the het carrier is returned and the
        homozygous-reference non-carrier is suppressed.
        """
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [het_pathogenic_row(), hom_ref_pathogenic_row()],
            )

        result = find_rare_variants(RareVariantFilter(carried_only=True), sample_engine)
        rsids = {v.rsid for v in result.variants}
        assert "rs_het_carrier" in rsids
        assert "rs_hom_ref_pathogenic" not in rsids

    def test_excludes_common_variants(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter()
        result = find_rare_variants(filters, sample_with_rare_variants)
        rsids = {v.rsid for v in result.variants}
        assert "rs100004" not in rsids  # AF=0.15
        assert "rs100005" not in rsids  # AF=0.03

    def test_includes_novel_variants(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter()
        result = find_rare_variants(filters, sample_with_rare_variants)
        rsids = {v.rsid for v in result.variants}
        assert "rs100003" in rsids  # Novel (no gnomAD)

    def test_total_variants_scanned(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter()
        result = find_rare_variants(filters, sample_with_rare_variants)
        assert result.total_variants_scanned == 8

    def test_novel_count(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter()
        result = find_rare_variants(filters, sample_with_rare_variants)
        # F12: the only AF-null variant (rs100003) is a ClinVar-catalogued
        # Likely-pathogenic MLH1 frameshift — catalogued, so NOT novel. Absence
        # from the exome-biased gnomAD bundle alone does not make it novel.
        assert result.novel_count == 0


# ── Sex/chromosome finding gate (F8, #711) ───────────────────────────────


class TestSexChromosomeGate:
    """End-to-end coverage for the F8 sex/chromosome gate through
    ``find_rare_variants`` — the gate's only production wiring.

    The gate (``is_surfaceable``) drops a Y-chromosome finding on a confident
    ``"XX"`` sample (biologically impossible). Before #711 nothing exercised it:
    no test set ``inferred_sex`` (so the caller's ``is not None`` guard was always
    false) and every fixture was autosomal, so the one discriminating row of the
    truth table (chrY + XX) was never built. Defeating the gate left the suite
    fully green. These tests construct that exact row.
    """

    def _chr_y_pathogenic_row(self) -> dict:
        # A rare, carried (het), ClinVar-Pathogenic Y-chromosome variant — the
        # kind a chip can report at a Y probe for an XX individual. Passes the
        # normal rare-variant filter, so only the sex gate can drop it.
        return _v(
            rsid="rs_y_pathogenic",
            chrom="Y",
            pos=2787000,
            genotype="AG",
            zygosity="het",
            gene_symbol="SRY",
            consequence="missense_variant",
            gnomad_af_global=0.0005,
            clinvar_significance="Pathogenic",
            clinvar_review_stars=2,
            clinvar_accession="VCV000000001",
            ensemble_pathogenic=True,
            annotation_coverage=15,
        )

    def test_chr_y_pathogenic_dropped_for_xx_sample(self, sample_engine: sa.Engine) -> None:
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(annotated_variants), [self._chr_y_pathogenic_row()])

        result = find_rare_variants(RareVariantFilter(inferred_sex="XX"), sample_engine)
        rsids = {v.rsid for v in result.variants}
        # Biologically impossible on an XX sample → suppressed by the F8 gate.
        assert "rs_y_pathogenic" not in rsids

    def test_chr_y_pathogenic_kept_for_xy_sample(self, sample_engine: sa.Engine) -> None:
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(annotated_variants), [self._chr_y_pathogenic_row()])

        result = find_rare_variants(RareVariantFilter(inferred_sex="XY"), sample_engine)
        rsids = {v.rsid for v in result.variants}
        # A Y finding on an XY sample is real — the gate must NOT drop it. This is
        # the counterpart that proves the XX suppression is sex-specific, not a
        # blanket drop of all chrY rows.
        assert "rs_y_pathogenic" in rsids

    def test_chr_y_pathogenic_kept_when_sex_unknown(self, sample_engine: sa.Engine) -> None:
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(annotated_variants), [self._chr_y_pathogenic_row()])

        # Sex not confidently known → nothing is dropped (a false drop would hide
        # a real finding). Also covers the caller's ``inferred_sex is not None``
        # guard firing while the gate itself returns True.
        result = find_rare_variants(RareVariantFilter(inferred_sex="unknown"), sample_engine)
        assert "rs_y_pathogenic" in {v.rsid for v in result.variants}

    def test_autosomal_pathogenic_kept_for_xx_sample(self, sample_engine: sa.Engine) -> None:
        # Control: the gate only touches chrY — an autosomal Pathogenic variant on
        # an XX sample must still surface, so the gate isn't over-suppressing.
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [_v(**{**self._chr_y_pathogenic_row(), "rsid": "rs_autosomal", "chrom": "17"})],
            )

        result = find_rare_variants(RareVariantFilter(inferred_sex="XX"), sample_engine)
        assert "rs_autosomal" in {v.rsid for v in result.variants}


# ── AF threshold tests ───────────────────────────────────────────────────


class TestAFThreshold:
    """Test allele frequency threshold filtering."""

    def test_custom_threshold(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter(af_threshold=0.001)
        result = find_rare_variants(filters, sample_with_rare_variants)
        # AF < 0.001: rs100001 (0.0005), rs100002 (0.00001), rs100003 (novel), rs100008 (0.0001)
        assert result.count == 4

    def test_very_strict_threshold(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter(af_threshold=0.00005)
        result = find_rare_variants(filters, sample_with_rare_variants)
        # AF < 0.00005: rs100002 (0.00001), rs100003 (novel)
        assert result.count == 2

    def test_exclude_novel(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter(include_novel=False)
        result = find_rare_variants(filters, sample_with_rare_variants)
        rsids = {v.rsid for v in result.variants}
        assert "rs100003" not in rsids  # Novel excluded

    def test_permissive_threshold(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter(af_threshold=0.05)
        result = find_rare_variants(filters, sample_with_rare_variants)
        # Includes rs100005 (0.03) too
        assert result.count == 7


# ── Gene panel filter tests ──────────────────────────────────────────────


class TestGeneFilter:
    """Test gene panel filtering."""

    def test_single_gene(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter(gene_symbols=["BRCA1"])
        result = find_rare_variants(filters, sample_with_rare_variants)
        assert result.count == 1
        assert result.variants[0].rsid == "rs100001"

    def test_multiple_genes(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter(gene_symbols=["BRCA1", "EGFR", "MLH1"])
        result = find_rare_variants(filters, sample_with_rare_variants)
        assert result.count == 3
        rsids = {v.rsid for v in result.variants}
        assert rsids == {"rs100001", "rs100002", "rs100003"}

    def test_case_insensitive(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter(gene_symbols=["brca1"])
        result = find_rare_variants(filters, sample_with_rare_variants)
        assert result.count == 1

    def test_nonexistent_gene(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter(gene_symbols=["NONEXISTENT"])
        result = find_rare_variants(filters, sample_with_rare_variants)
        assert result.count == 0

    def test_genes_with_findings(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter()
        result = find_rare_variants(filters, sample_with_rare_variants)
        genes = result.genes_with_findings
        assert "BRCA1" in genes
        assert "EGFR" in genes
        assert "MTHFR" not in genes  # Common variant excluded


# ── Consequence filter tests ─────────────────────────────────────────────


class TestConsequenceFilter:
    """Test consequence type filtering."""

    def test_single_consequence(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter(consequences=["stop_gained"])
        result = find_rare_variants(filters, sample_with_rare_variants)
        assert result.count == 1
        assert result.variants[0].rsid == "rs100002"

    def test_multiple_consequences(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter(consequences=["missense_variant", "frameshift_variant"])
        result = find_rare_variants(filters, sample_with_rare_variants)
        rsids = {v.rsid for v in result.variants}
        assert "rs100001" in rsids  # missense
        assert "rs100003" in rsids  # frameshift
        assert "rs100007" in rsids  # missense + rare

    def test_excludes_non_matching_consequences(
        self, sample_with_rare_variants: sa.Engine
    ) -> None:
        filters = RareVariantFilter(consequences=["missense_variant"])
        result = find_rare_variants(filters, sample_with_rare_variants)
        rsids = {v.rsid for v in result.variants}
        assert "rs100006" not in rsids  # intron_variant


# ── ClinVar filter tests ────────────────────────────────────────────────


class TestClinVarFilter:
    """Test ClinVar significance filtering."""

    def test_pathogenic_only(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter(clinvar_significance=["Pathogenic", "Likely pathogenic"])
        result = find_rare_variants(filters, sample_with_rare_variants)
        rsids = {v.rsid for v in result.variants}
        assert "rs100001" in rsids  # Pathogenic
        assert "rs100003" in rsids  # LP
        assert "rs100008" in rsids  # LP
        assert "rs100007" not in rsids  # VUS

    def test_vus_filter(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter(clinvar_significance=["Uncertain_significance"])
        result = find_rare_variants(filters, sample_with_rare_variants)
        assert result.count == 1
        assert result.variants[0].rsid == "rs100007"


# ── Combined filter tests ───────────────────────────────────────────────


class TestCombinedFilters:
    """T3-28: Rare variant finder returns only variants matching ALL filter criteria."""

    def test_gene_plus_consequence(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter(
            gene_symbols=["BRCA1", "BRCA2"],
            consequences=["missense_variant"],
        )
        result = find_rare_variants(filters, sample_with_rare_variants)
        rsids = {v.rsid for v in result.variants}
        assert rsids == {"rs100001", "rs100007"}

    def test_gene_plus_clinvar(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter(
            gene_symbols=["BRCA1", "BRCA2", "MLH1"],
            clinvar_significance=["Pathogenic"],
        )
        result = find_rare_variants(filters, sample_with_rare_variants)
        assert result.count == 1
        assert result.variants[0].rsid == "rs100001"

    def test_af_plus_consequence_plus_clinvar(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter(
            af_threshold=0.001,
            consequences=["missense_variant"],
            clinvar_significance=["Pathogenic"],
        )
        result = find_rare_variants(filters, sample_with_rare_variants)
        assert result.count == 1
        assert result.variants[0].rsid == "rs100001"

    def test_zygosity_filter(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter(zygosity="hom_alt")
        result = find_rare_variants(filters, sample_with_rare_variants)
        assert result.count == 1
        assert result.variants[0].rsid == "rs100007"

    def test_all_filters_combined(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter(
            gene_symbols=["BRCA1"],
            af_threshold=0.01,
            consequences=["missense_variant"],
            clinvar_significance=["Pathogenic"],
            zygosity="het",
        )
        result = find_rare_variants(filters, sample_with_rare_variants)
        assert result.count == 1
        assert result.variants[0].rsid == "rs100001"
        assert result.variants[0].gene_symbol == "BRCA1"


# ── Evidence level assignment tests ──────────────────────────────────────


class TestEvidenceLevelAssignment:
    """Test evidence level assignment for rare variants."""

    def _make_variant(self, **kwargs) -> RareVariantResult:
        """Helper to create a RareVariantResult with defaults."""
        defaults = dict(
            rsid="rs1",
            chrom="1",
            pos=100,
            ref="A",
            alt="G",
            genotype="AG",
            zygosity="het",
            gene_symbol="GENE1",
            consequence="missense_variant",
            hgvs_coding=None,
            hgvs_protein=None,
            gnomad_af_global=0.001,
            gnomad_af_afr=None,
            gnomad_af_amr=None,
            gnomad_af_eas=None,
            gnomad_af_eur=None,
            gnomad_af_fin=None,
            gnomad_af_sas=None,
            clinvar_significance=None,
            clinvar_review_stars=None,
            clinvar_accession=None,
            clinvar_conditions=None,
            cadd_phred=None,
            revel=None,
            ensemble_pathogenic=False,
            evidence_conflict=False,
            evidence_level=1,
            disease_name=None,
            inheritance_pattern=None,
        )
        defaults.update(kwargs)
        return RareVariantResult(**defaults)

    def test_pathogenic_2_plus_stars_gives_4(self) -> None:
        v = self._make_variant(clinvar_significance="Pathogenic", clinvar_review_stars=2)
        assert _assign_evidence_level(v) == 4

    def test_pathogenic_3_stars_gives_4(self) -> None:
        v = self._make_variant(clinvar_significance="Pathogenic", clinvar_review_stars=3)
        assert _assign_evidence_level(v) == 4

    def test_likely_pathogenic_2_stars_gives_4(self) -> None:
        v = self._make_variant(clinvar_significance="Likely pathogenic", clinvar_review_stars=2)
        assert _assign_evidence_level(v) == 4

    def test_pathogenic_1_star_gives_4(self) -> None:
        v = self._make_variant(clinvar_significance="Pathogenic", clinvar_review_stars=1)
        assert _assign_evidence_level(v) == 4

    def test_likely_pathogenic_1_star_gives_3(self) -> None:
        v = self._make_variant(clinvar_significance="Likely pathogenic", clinvar_review_stars=1)
        assert _assign_evidence_level(v) == 3

    def test_pathogenic_0_stars_gives_2(self) -> None:
        v = self._make_variant(clinvar_significance="Pathogenic", clinvar_review_stars=0)
        assert _assign_evidence_level(v) == 2

    def test_likely_pathogenic_0_stars_gives_2(self) -> None:
        v = self._make_variant(clinvar_significance="Likely pathogenic", clinvar_review_stars=0)
        assert _assign_evidence_level(v) == 2

    def test_ensemble_pathogenic_no_clinvar_gives_1(self) -> None:
        # F19: in-silico ensemble support alone is PRELIMINARY (★), not ★★ —
        # ★★ MODERATE is reserved for functional/clinical evidence.
        v = self._make_variant(ensemble_pathogenic=True)
        assert _assign_evidence_level(v) == 1

    def test_rare_no_clinvar_no_ensemble_gives_1(self) -> None:
        v = self._make_variant()
        assert _assign_evidence_level(v) == 1


# ── Sorting tests ─────────────────────────────────────────────────────────


class TestSorting:
    """Test that results are sorted by clinical relevance."""

    def test_clinvar_plp_first(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter()
        result = find_rare_variants(filters, sample_with_rare_variants)
        # First variants should be ClinVar P/LP
        clinvar_indices = [i for i, v in enumerate(result.variants) if v.is_clinvar_pathogenic]
        non_clinvar_indices = [
            i for i, v in enumerate(result.variants) if not v.is_clinvar_pathogenic
        ]
        if clinvar_indices and non_clinvar_indices:
            assert max(clinvar_indices) < min(non_clinvar_indices)

    def test_compound_pathogenic_sorts_and_tiers_as_clinvar_pathogenic(
        self, sample_engine: sa.Engine
    ) -> None:
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    _v(
                        rsid="rs_compound_plp",
                        chrom="1",
                        pos=1000,
                        genotype="AG",
                        zygosity="het",
                        gene_symbol="CFTR",
                        consequence="missense_variant",
                        gnomad_af_global=0.005,
                        clinvar_significance="Pathogenic|drug response",
                        clinvar_review_stars=2,
                        annotation_coverage=15,
                    ),
                    _v(
                        rsid="rs_lower_af_no_clinvar",
                        chrom="1",
                        pos=1001,
                        genotype="AG",
                        zygosity="het",
                        gene_symbol="GENE2",
                        consequence="missense_variant",
                        gnomad_af_global=0.000001,
                        annotation_coverage=15,
                    ),
                ],
            )

        result = find_rare_variants(RareVariantFilter(), sample_engine)

        assert result.variants[0].rsid == "rs_compound_plp"
        compound = result.variants[0]
        assert compound.is_clinvar_pathogenic is True
        assert compound.evidence_level == 4

        store_rare_variant_findings(result, sample_engine)
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings.c.category, findings.c.evidence_level).where(
                    findings.c.rsid == "rs_compound_plp"
                )
            ).one()
        assert row.category == "clinvar_pathogenic"
        assert row.evidence_level == 4


# ── Findings storage tests ───────────────────────────────────────────────


class TestStoreRareVariantFindings:
    """Test rare variant findings storage in the sample database."""

    def test_stores_correct_count(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter()
        result = find_rare_variants(filters, sample_with_rare_variants)
        count = store_rare_variant_findings(result, sample_with_rare_variants)
        assert count == 6

    def test_findings_have_module_rare_variants(
        self, sample_with_rare_variants: sa.Engine
    ) -> None:
        filters = RareVariantFilter()
        result = find_rare_variants(filters, sample_with_rare_variants)
        store_rare_variant_findings(result, sample_with_rare_variants)

        with sample_with_rare_variants.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == "rare_variants")
            ).fetchall()
        assert len(rows) == 6
        for row in rows:
            assert row.module == "rare_variants"

    def test_category_assignment(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter()
        result = find_rare_variants(filters, sample_with_rare_variants)
        store_rare_variant_findings(result, sample_with_rare_variants)

        with sample_with_rare_variants.connect() as conn:
            rows = conn.execute(
                sa.select(findings.c.rsid, findings.c.category).where(
                    findings.c.module == "rare_variants"
                )
            ).fetchall()
        cat_map = {row.rsid: row.category for row in rows}
        assert cat_map["rs100001"] == "clinvar_pathogenic"  # Pathogenic, 3 stars
        assert cat_map["rs100002"] == "ensemble_pathogenic"
        assert cat_map["rs100003"] == "clinvar_pathogenic"  # LP, 1 star
        # F20: a 0-star P/LP (no assertion criteria) routes to the distinct
        # low-confidence sub-tier, not the headline clinvar_pathogenic category.
        assert cat_map["rs100008"] == "clinvar_pathogenic_low_confidence"  # LP, 0 stars

    def test_finding_text_contains_gene_and_rsid(
        self, sample_with_rare_variants: sa.Engine
    ) -> None:
        filters = RareVariantFilter()
        result = find_rare_variants(filters, sample_with_rare_variants)
        store_rare_variant_findings(result, sample_with_rare_variants)

        with sample_with_rare_variants.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.rsid == "rs100001")).fetchone()
        assert row is not None
        assert "BRCA1" in row.finding_text
        assert "rs100001" in row.finding_text
        assert "Pathogenic" in row.finding_text

    def test_detail_json_has_af_data(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter()
        result = find_rare_variants(filters, sample_with_rare_variants)
        store_rare_variant_findings(result, sample_with_rare_variants)

        with sample_with_rare_variants.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.rsid == "rs100001")).fetchone()
        detail = json.loads(row.detail_json)
        assert detail["af_global"] == 0.0005
        assert "af_populations" in detail
        assert detail["consequence"] == "missense_variant"

    def test_clears_previous_findings_on_rerun(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter()
        result = find_rare_variants(filters, sample_with_rare_variants)
        store_rare_variant_findings(result, sample_with_rare_variants)
        store_rare_variant_findings(result, sample_with_rare_variants)

        with sample_with_rare_variants.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "rare_variants")
            ).scalar()
        assert count == 6  # Not doubled

    def test_empty_result_stores_nothing(self, empty_sample: sa.Engine) -> None:
        filters = RareVariantFilter()
        result = find_rare_variants(filters, empty_sample)
        count = store_rare_variant_findings(result, empty_sample)
        assert count == 0

    def test_evidence_levels_stored_correctly(self, sample_with_rare_variants: sa.Engine) -> None:
        filters = RareVariantFilter()
        result = find_rare_variants(filters, sample_with_rare_variants)
        store_rare_variant_findings(result, sample_with_rare_variants)

        with sample_with_rare_variants.connect() as conn:
            rows = conn.execute(
                sa.select(findings.c.rsid, findings.c.evidence_level).where(
                    findings.c.module == "rare_variants"
                )
            ).fetchall()
        evidence_map = {row.rsid: row.evidence_level for row in rows}
        assert evidence_map["rs100001"] == 4  # Pathogenic 3-star
        assert evidence_map["rs100002"] == 1  # Ensemble pathogenic (F19: in-silico → ★, not ★★)
        assert evidence_map["rs100003"] == 3  # LP 1-star
        assert evidence_map["rs100008"] == 2  # LP 0-star (F20 sub-tier, evidence MODERATE)


# ── Dataclass property tests ────────────────────────────────────────────


class TestRareVariantResultProperties:
    """Test RareVariantResult dataclass properties."""

    @staticmethod
    def _result(**overrides: object) -> RareVariantResult:
        """Build a RareVariantResult with sane defaults for novelty tests."""
        defaults: dict = dict(
            rsid="i5004332",  # genuinely uncatalogued internal probe id by default
            chrom="1",
            pos=100,
            ref="A",
            alt="G",
            genotype="AG",
            zygosity="het",
            gene_symbol="GENE1",
            consequence="missense_variant",
            hgvs_coding=None,
            hgvs_protein=None,
            gnomad_af_global=None,
            gnomad_af_afr=None,
            gnomad_af_amr=None,
            gnomad_af_eas=None,
            gnomad_af_eur=None,
            gnomad_af_fin=None,
            gnomad_af_sas=None,
            clinvar_significance=None,
            clinvar_review_stars=None,
            clinvar_accession=None,
            clinvar_conditions=None,
            cadd_phred=None,
            revel=None,
            ensemble_pathogenic=False,
            evidence_conflict=False,
            evidence_level=1,
            disease_name=None,
            inheritance_pattern=None,
        )
        defaults.update(overrides)
        return RareVariantResult(**defaults)

    def test_is_novel_when_uncatalogued(self) -> None:
        """F12: AF-null AND uncatalogued (no rs id, no ClinVar) → genuinely novel."""
        assert self._result(rsid="i5004332", gnomad_af_global=None).is_novel is True

    def test_not_novel_when_af_present(self) -> None:
        assert self._result(gnomad_af_global=0.001).is_novel is False

    @pytest.mark.parametrize(
        ("overrides", "why"),
        [
            ({"rsid": "rs3131972"}, "dbSNP rs id"),
            ({"rsid": "i5004332", "clinvar_significance": "Benign"}, "ClinVar significance"),
            ({"rsid": "i5004332", "clinvar_accession": "VCV000000001"}, "ClinVar accession"),
        ],
    )
    def test_af_null_but_catalogued_is_not_novel(self, overrides: dict, why: str) -> None:
        """F12: absence from gnomAD is not novelty when the variant is catalogued."""
        v = self._result(gnomad_af_global=None, **overrides)
        assert v.is_novel is False, why
        assert v.is_catalogued is True

    def test_is_clinvar_pathogenic(self) -> None:
        v = RareVariantResult(
            rsid="rs1",
            chrom="1",
            pos=100,
            ref="A",
            alt="G",
            genotype="AG",
            zygosity="het",
            gene_symbol="GENE1",
            consequence="missense_variant",
            hgvs_coding=None,
            hgvs_protein=None,
            gnomad_af_global=0.001,
            gnomad_af_afr=None,
            gnomad_af_amr=None,
            gnomad_af_eas=None,
            gnomad_af_eur=None,
            gnomad_af_fin=None,
            gnomad_af_sas=None,
            clinvar_significance="Pathogenic",
            clinvar_review_stars=3,
            clinvar_accession=None,
            clinvar_conditions=None,
            cadd_phred=None,
            revel=None,
            ensemble_pathogenic=False,
            evidence_conflict=False,
            evidence_level=4,
            disease_name=None,
            inheritance_pattern=None,
        )
        assert v.is_clinvar_pathogenic is True

    def test_compound_clinvar_primary_is_pathogenic(self) -> None:
        v = self._result(clinvar_significance="Pathogenic, low penetrance")
        assert v.is_clinvar_pathogenic is True

    def test_consequence_severity_score(self) -> None:
        v = RareVariantResult(
            rsid="rs1",
            chrom="1",
            pos=100,
            ref="A",
            alt="G",
            genotype="AG",
            zygosity="het",
            gene_symbol="GENE1",
            consequence="stop_gained",
            hgvs_coding=None,
            hgvs_protein=None,
            gnomad_af_global=0.001,
            gnomad_af_afr=None,
            gnomad_af_amr=None,
            gnomad_af_eas=None,
            gnomad_af_eur=None,
            gnomad_af_fin=None,
            gnomad_af_sas=None,
            clinvar_significance=None,
            clinvar_review_stars=None,
            clinvar_accession=None,
            clinvar_conditions=None,
            cadd_phred=None,
            revel=None,
            ensemble_pathogenic=False,
            evidence_conflict=False,
            evidence_level=1,
            disease_name=None,
            inheritance_pattern=None,
        )
        assert v.consequence_severity_score == CONSEQUENCE_SEVERITY["stop_gained"]


class TestRareVariantFinderResultProperties:
    """Test RareVariantFinderResult dataclass properties."""

    def test_empty_result(self) -> None:
        result = RareVariantFinderResult()
        assert result.count == 0
        assert result.novel_count == 0
        assert result.pathogenic_count == 0
        assert result.genes_with_findings == []

    def test_default_af_threshold(self) -> None:
        assert DEFAULT_AF_THRESHOLD == 0.01

    def test_high_impact_consequences(self) -> None:
        assert "stop_gained" in HIGH_IMPACT_CONSEQUENCES
        assert "missense_variant" in HIGH_IMPACT_CONSEQUENCES
        assert "frameshift_variant" in HIGH_IMPACT_CONSEQUENCES
        assert "intron_variant" not in HIGH_IMPACT_CONSEQUENCES
