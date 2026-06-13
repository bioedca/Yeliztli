"""Tests for the cardiovascular gene panel and analysis module (P3-19, P3-20).

Covers:
  - Panel JSON loading and validation
  - All 16 genes present across 4 cardiovascular categories
  - Gene lookup by symbol, category, and condition
  - FH gene grouping (LDLR, PCSK9, APOB)
  - Expected ClinVar rsids are populated
  - Evidence levels are valid (1-4)
  - Inheritance patterns are valid (AD/AR)
  - ClinVar P/LP extraction from annotated variants
  - Evidence level assignment based on ClinVar review stars
  - Findings storage (module='cardiovascular', category='monogenic_variant')
  - Category-based variant grouping (FH, cardiomyopathy, channelopathy)
  - T3-19: LDLR pathogenic variant → cardiovascular finding with ★★★★
  - P3-20: FH variant status reporting (Positive/Negative determination)
  - FH status summary finding storage (category='fh_status')
  - FH status with heterozygous and homozygous variants
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.cardiovascular import (
    CATEGORY_CARDIOMYOPATHY,
    CATEGORY_CHANNELOPATHY,
    CATEGORY_FH,
    CATEGORY_LIPID,
    DISEASE_AFFECTED,
    DISEASE_CARRIER,
    DISEASE_POSSIBLE_BIALLELIC,
    FH_STATUS_NEGATIVE,
    FH_STATUS_POSITIVE,
    CardiovascularAnalysisResult,
    CardiovascularPanel,
    CardiovascularVariantResult,
    FHStatus,
    _assign_evidence_level,
    classify_disease_status,
    determine_fh_status,
    extract_cardiovascular_variants,
    load_cardiovascular_panel,
    store_cardiovascular_findings,
    store_fh_status_finding,
)
from backend.db.tables import annotated_variants, findings

# ── Fixtures ──────────────────────────────────────────────────────────────

PANEL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "cardiovascular_panel.json"
)


@pytest.fixture()
def panel() -> CardiovascularPanel:
    """Load the curated cardiovascular panel from the real JSON file."""
    return load_cardiovascular_panel(PANEL_PATH)


@pytest.fixture()
def sample_with_cv_variants(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine with annotated variants including cardiovascular panel P/LP hits."""
    variants = [
        # LDLR Pathogenic — 3-star review (FH gene, golden fixture)
        {
            "rsid": "rs28942078",
            "chrom": "19",
            "pos": 11200089,
            "genotype": "CT",
            "zygosity": "het",
            "gene_symbol": "LDLR",
            "clinvar_significance": "Pathogenic",
            "clinvar_review_stars": 3,
            "clinvar_accession": "VCV000018390",
            "clinvar_conditions": "Familial hypercholesterolemia",
            "annotation_coverage": 2,
        },
        # MYBPC3 Likely pathogenic — 2-star review (cardiomyopathy)
        {
            "rsid": "rs121912485",
            "chrom": "11",
            "pos": 47354029,
            "genotype": "AG",
            "zygosity": "het",
            "gene_symbol": "MYBPC3",
            "clinvar_significance": "Likely pathogenic",
            "clinvar_review_stars": 2,
            "clinvar_accession": "VCV000042574",
            "clinvar_conditions": "Hypertrophic cardiomyopathy",
            "annotation_coverage": 2,
        },
        # KCNQ1 Pathogenic — 1-star review (channelopathy)
        {
            "rsid": "rs120074175",
            "chrom": "11",
            "pos": 2570317,
            "genotype": "AG",
            "zygosity": "het",
            "gene_symbol": "KCNQ1",
            "clinvar_significance": "Pathogenic",
            "clinvar_review_stars": 1,
            "clinvar_accession": "VCV000003336",
            "clinvar_conditions": "Long QT syndrome 1",
            "annotation_coverage": 2,
        },
        # SCN5A Likely pathogenic — 1-star review (channelopathy → evidence 3)
        {
            "rsid": "rs28937318",
            "chrom": "3",
            "pos": 38589553,
            "genotype": "CT",
            "zygosity": "het",
            "gene_symbol": "SCN5A",
            "clinvar_significance": "Likely pathogenic",
            "clinvar_review_stars": 1,
            "clinvar_accession": "VCV000003821",
            "clinvar_conditions": "Long QT syndrome 3",
            "annotation_coverage": 2,
        },
        # PCSK9 Pathogenic — 0-star review (FH, low confidence → capped)
        {
            "rsid": "rs28362286",
            "chrom": "1",
            "pos": 55505647,
            "genotype": "AG",
            "zygosity": "het",
            "gene_symbol": "PCSK9",
            "clinvar_significance": "Pathogenic",
            "clinvar_review_stars": 0,
            "clinvar_accession": "VCV000038333",
            "clinvar_conditions": "Familial hypercholesterolemia",
            "annotation_coverage": 2,
        },
        # LDLR Benign — should NOT appear in results
        {
            "rsid": "rs2228671",
            "chrom": "19",
            "pos": 11210912,
            "genotype": "CT",
            "zygosity": "het",
            "gene_symbol": "LDLR",
            "clinvar_significance": "Benign",
            "clinvar_review_stars": 2,
            "clinvar_accession": "VCV000018391",
            "clinvar_conditions": "not specified",
            "annotation_coverage": 2,
        },
        # Non-panel gene Pathogenic — should NOT appear in results
        {
            "rsid": "rs80357906",
            "chrom": "17",
            "pos": 43091983,
            "genotype": "CT",
            "zygosity": "het",
            "gene_symbol": "BRCA1",
            "clinvar_significance": "Pathogenic",
            "clinvar_review_stars": 3,
            "clinvar_accession": "VCV000017661",
            "clinvar_conditions": "Hereditary breast and ovarian cancer syndrome",
            "annotation_coverage": 2,
        },
        # VUS in panel gene — should NOT appear in results
        {
            "rsid": "rs999777",
            "chrom": "19",
            "pos": 11200100,
            "genotype": "AG",
            "zygosity": "het",
            "gene_symbol": "LDLR",
            "clinvar_significance": "Uncertain_significance",
            "clinvar_review_stars": 1,
            "clinvar_accession": "VCV000099888",
            "clinvar_conditions": "not specified",
            "annotation_coverage": 2,
        },
    ]
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(annotated_variants), variants)
    return sample_engine


@pytest.fixture()
def empty_sample(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine with no annotated variants."""
    return sample_engine


# ── Panel loading tests ──────────────────────────────────────────────────


class TestPanelLoading:
    """Test panel JSON loading and basic structure."""

    def test_panel_loads_successfully(self, panel: CardiovascularPanel) -> None:
        assert panel is not None
        assert panel.module == "cardiovascular"
        assert panel.version == "1.0.0"

    def test_panel_has_description(self, panel: CardiovascularPanel) -> None:
        assert panel.description
        assert "cardiovascular" in panel.description.lower()

    def test_panel_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_cardiovascular_panel(tmp_path / "nonexistent.json")

    def test_panel_malformed_json(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_cardiovascular_panel(bad_file)

    def test_panel_missing_required_field(self, tmp_path: Path) -> None:
        """Missing required field raises ValueError with gene context."""
        bad_panel = tmp_path / "bad_panel.json"
        bad_panel.write_text(
            json.dumps(
                {
                    "module": "cardiovascular",
                    "version": "1.0.0",
                    "description": "test",
                    "genes": [{"gene_symbol": "TEST"}],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Missing required field.*TEST"):
            load_cardiovascular_panel(bad_panel)


# ── Gene count and completeness ──────────────────────────────────────────


class TestGeneCompleteness:
    """Verify all PRD-specified genes are present."""

    EXPECTED_GENES = [
        "LDLR",
        "PCSK9",
        "APOB",
        "LPA",
        "ABCG5",
        "ABCG8",
        "KCNQ1",
        "SCN5A",
        "MYBPC3",
        "MYH7",
        "TNNT2",
        "LMNA",
        "DSP",
        "PKP2",
        "KCNH2",
        "RYR2",
    ]

    def test_gene_count(self, panel: CardiovascularPanel) -> None:
        assert len(panel.genes) == 16

    def test_all_expected_genes_present(self, panel: CardiovascularPanel) -> None:
        panel_symbols = set(panel.all_gene_symbols())
        for gene in self.EXPECTED_GENES:
            assert gene in panel_symbols, f"Missing gene: {gene}"

    def test_no_unexpected_genes(self, panel: CardiovascularPanel) -> None:
        panel_symbols = set(panel.all_gene_symbols())
        expected = set(self.EXPECTED_GENES)
        unexpected = panel_symbols - expected
        assert not unexpected, f"Unexpected genes: {unexpected}"


# ── Gene lookup ──────────────────────────────────────────────────────────


class TestGeneLookup:
    """Test gene lookup methods."""

    def test_get_gene_by_symbol(self, panel: CardiovascularPanel) -> None:
        ldlr = panel.get_gene("LDLR")
        assert ldlr is not None
        assert ldlr.gene_symbol == "LDLR"

    def test_get_gene_case_insensitive(self, panel: CardiovascularPanel) -> None:
        ldlr = panel.get_gene("ldlr")
        assert ldlr is not None
        assert ldlr.gene_symbol == "LDLR"

    def test_get_gene_not_found(self, panel: CardiovascularPanel) -> None:
        result = panel.get_gene("NONEXISTENT")
        assert result is None

    def test_genes_by_category_fh(self, panel: CardiovascularPanel) -> None:
        fh_genes = panel.genes_by_category(CATEGORY_FH)
        symbols = {g.gene_symbol for g in fh_genes}
        assert symbols == {"LDLR", "PCSK9", "APOB"}

    def test_genes_by_category_cardiomyopathy(self, panel: CardiovascularPanel) -> None:
        cm_genes = panel.genes_by_category(CATEGORY_CARDIOMYOPATHY)
        symbols = {g.gene_symbol for g in cm_genes}
        assert {"MYBPC3", "MYH7", "TNNT2", "LMNA", "DSP", "PKP2"} == symbols

    def test_genes_by_category_channelopathy(self, panel: CardiovascularPanel) -> None:
        ch_genes = panel.genes_by_category(CATEGORY_CHANNELOPATHY)
        symbols = {g.gene_symbol for g in ch_genes}
        assert {"KCNQ1", "SCN5A", "KCNH2", "RYR2"} == symbols

    def test_genes_by_category_lipid(self, panel: CardiovascularPanel) -> None:
        lm_genes = panel.genes_by_category(CATEGORY_LIPID)
        symbols = {g.gene_symbol for g in lm_genes}
        assert {"LPA", "ABCG5", "ABCG8"} == symbols

    def test_fh_genes_shortcut(self, panel: CardiovascularPanel) -> None:
        fh = panel.fh_genes()
        symbols = {g.gene_symbol for g in fh}
        assert symbols == {"LDLR", "PCSK9", "APOB"}

    def test_genes_by_condition_hcm(self, panel: CardiovascularPanel) -> None:
        hcm_genes = panel.genes_by_condition("Hypertrophic Cardiomyopathy")
        symbols = {g.gene_symbol for g in hcm_genes}
        assert "MYBPC3" in symbols
        assert "MYH7" in symbols
        assert "TNNT2" in symbols

    def test_genes_by_condition_long_qt(self, panel: CardiovascularPanel) -> None:
        lqt_genes = panel.genes_by_condition("Long QT")
        symbols = {g.gene_symbol for g in lqt_genes}
        assert "KCNQ1" in symbols
        assert "SCN5A" in symbols
        assert "KCNH2" in symbols


# ── Expected ClinVar rsids ───────────────────────────────────────────────


class TestExpectedClinVarRsids:
    """Test expected ClinVar P/LP rsid entries."""

    def test_all_genes_have_expected_rsids(self, panel: CardiovascularPanel) -> None:
        for gene in panel.genes:
            assert len(gene.expected_clinvar_rsids) > 0, (
                f"{gene.gene_symbol} has no expected ClinVar rsids"
            )

    def test_rsids_are_valid_format(self, panel: CardiovascularPanel) -> None:
        for gene in panel.genes:
            for rsid in gene.expected_clinvar_rsids:
                assert rsid.startswith("rs"), f"Invalid rsid format: {rsid} in {gene.gene_symbol}"
                assert rsid[2:].isdigit(), (
                    f"Invalid rsid numeric part: {rsid} in {gene.gene_symbol}"
                )

    def test_no_duplicate_rsids_within_gene(self, panel: CardiovascularPanel) -> None:
        for gene in panel.genes:
            rsids = gene.expected_clinvar_rsids
            assert len(rsids) == len(set(rsids)), f"Duplicate rsids in {gene.gene_symbol}"

    def test_total_expected_rsids(self, panel: CardiovascularPanel) -> None:
        """Panel should have a substantial number of expected rsids."""
        all_rsids = panel.all_expected_rsids()
        assert len(all_rsids) >= 80  # At least 80 across all genes

    def test_ldlr_rs28942078_present(self, panel: CardiovascularPanel) -> None:
        """LDLR rs28942078 must be in expected rsids (golden fixture)."""
        ldlr = panel.get_gene("LDLR")
        assert ldlr is not None
        assert "rs28942078" in ldlr.expected_clinvar_rsids


# ── Evidence levels ──────────────────────────────────────────────────────


class TestEvidenceLevels:
    """Test evidence level assignments."""

    def test_evidence_levels_valid_range(self, panel: CardiovascularPanel) -> None:
        for gene in panel.genes:
            assert 1 <= gene.evidence_level <= 4, (
                f"{gene.gene_symbol} has invalid evidence level: {gene.evidence_level}"
            )

    def test_fh_genes_high_evidence(self, panel: CardiovascularPanel) -> None:
        """LDLR, PCSK9, APOB should be 4-star."""
        for symbol in ["LDLR", "PCSK9", "APOB"]:
            gene = panel.get_gene(symbol)
            assert gene is not None
            assert gene.evidence_level == 4, (
                f"{symbol} should be 4-star evidence, got {gene.evidence_level}"
            )

    def test_cardiomyopathy_genes_high_evidence(self, panel: CardiovascularPanel) -> None:
        """MYBPC3, MYH7 should be 4-star."""
        for symbol in ["MYBPC3", "MYH7"]:
            gene = panel.get_gene(symbol)
            assert gene is not None
            assert gene.evidence_level == 4

    def test_lpa_moderate_evidence(self, panel: CardiovascularPanel) -> None:
        """LPA should be 3-star (GWAS-driven, not purely monogenic)."""
        lpa = panel.get_gene("LPA")
        assert lpa is not None
        assert lpa.evidence_level == 3


# ── Inheritance patterns ─────────────────────────────────────────────────


class TestInheritance:
    """Test inheritance pattern assignments."""

    def test_inheritance_values_valid(self, panel: CardiovascularPanel) -> None:
        valid_patterns = {"AD", "AR"}
        for gene in panel.genes:
            assert gene.inheritance in valid_patterns, (
                f"{gene.gene_symbol} has invalid inheritance: {gene.inheritance}"
            )

    def test_abcg5_abcg8_are_ar(self, panel: CardiovascularPanel) -> None:
        """ABCG5/8 sitosterolemia is autosomal recessive."""
        for symbol in ["ABCG5", "ABCG8"]:
            gene = panel.get_gene(symbol)
            assert gene is not None
            assert gene.inheritance == "AR"

    def test_most_genes_are_ad(self, panel: CardiovascularPanel) -> None:
        """Most cardiovascular genes are AD."""
        ad_count = sum(1 for g in panel.genes if g.inheritance == "AD")
        assert ad_count >= 14  # 14 of 16 should be AD


# ── PubMed citations ─────────────────────────────────────────────────────


class TestPMIDs:
    """Test PubMed citation data."""

    def test_all_genes_have_pmids(self, panel: CardiovascularPanel) -> None:
        for gene in panel.genes:
            assert len(gene.pmids) > 0, f"{gene.gene_symbol} has no PubMed citations"

    def test_pmids_are_numeric(self, panel: CardiovascularPanel) -> None:
        for gene in panel.genes:
            for pmid in gene.pmids:
                assert pmid.isdigit(), f"Invalid PMID: {pmid} in {gene.gene_symbol}"

    def test_lpa_cites_lpa_specific_evidence(self, panel: CardiovascularPanel) -> None:
        """The LPA row must cite real LPA/Lp(a)-CAD genetics, not the unrelated
        retired-LGMD-GeneReviews (20301582) / withdrawn-PSMA-PET (30580001) PMIDs
        that were attached in error (gh #178). Locked to the verified set:
          - 20032323  Clarke 2009, NEJM (rs10455872/rs3798220 → Lp(a) & coronary disease)
          - 32681934  Page 2020, Clin Chim Acta (rs3798220/rs10455872 & CAD in FH)
        """
        lpa = panel.get_gene("LPA")
        assert lpa is not None
        cited = set(lpa.pmids)
        unrelated = {"20301582", "30580001"}
        assert cited.isdisjoint(unrelated), f"LPA cites unrelated PMIDs {cited & unrelated}"
        assert cited == {"20032323", "32681934"}, f"unexpected LPA PMIDs: {cited}"

    def test_fh_entries_cite_verified_fh_sources(self, panel: CardiovascularPanel) -> None:
        """#177 — LDLR/PCSK9/APOB must cite FH-specific literature.

        Locks the verified citation sets so the previously-transposed unrelated
        PMIDs cannot return. The originals all resolved (NCBI eutils) to
        non-FH topics: 20301462 = retired Charcot-Marie-Tooth GeneReviews,
        27914672 = "Superficial basal cell carcinoma", 26547463 = a generic
        JAMA editorial, and 29581339 = a non-existent PMID (HTTP 404). Each
        entry now carries a shared FH overview plus a gene-specific defining
        paper (all NCBI-title-verified):
          - 32479201 = Khera & Hegele 2020, Circulation — FH overview (shared)
          - 1301956  = Hobbs/Brown/Goldstein 1992, Hum Mutat — LDL receptor in FH
          - 12730697 = Abifadel 2003, Nat Genet — PCSK9 causes ADH
          - 2563166  = Soria 1989, PNAS — familial defective apolipoprotein B-100
        """
        assert panel.get_gene("LDLR").pmids == ["32479201", "1301956"]
        assert panel.get_gene("PCSK9").pmids == ["32479201", "12730697"]
        assert panel.get_gene("APOB").pmids == ["32479201", "2563166"]

    def test_no_unrelated_transposed_pmids(self, panel: CardiovascularPanel) -> None:
        """None of the verified-unrelated PMIDs may appear in any panel entry."""
        unrelated = {"20301462", "29581339", "26547463", "27914672"}
        for gene in panel.genes:
            stray = unrelated.intersection(gene.pmids)
            assert not stray, f"{gene.gene_symbol} cites unrelated PMID(s): {stray}"


# ── Gene metadata ────────────────────────────────────────────────────────


class TestGeneMetadata:
    """Test gene metadata completeness."""

    def test_all_genes_have_conditions(self, panel: CardiovascularPanel) -> None:
        for gene in panel.genes:
            assert len(gene.conditions) > 0, f"{gene.gene_symbol} has no conditions"

    def test_all_genes_have_valid_category(self, panel: CardiovascularPanel) -> None:
        from backend.analysis.cardiovascular import VALID_CATEGORIES

        for gene in panel.genes:
            assert gene.cardiovascular_category in VALID_CATEGORIES, (
                f"{gene.gene_symbol} has invalid category: {gene.cardiovascular_category}"
            )

    def test_all_genes_have_chromosome(self, panel: CardiovascularPanel) -> None:
        valid_chroms = {str(i) for i in range(1, 23)} | {"X", "Y"}
        for gene in panel.genes:
            assert gene.chromosome in valid_chroms, (
                f"{gene.gene_symbol} has invalid chromosome: {gene.chromosome}"
            )

    def test_all_genes_have_name(self, panel: CardiovascularPanel) -> None:
        for gene in panel.genes:
            assert gene.name, f"{gene.gene_symbol} has no name"

    def test_all_genes_have_notes(self, panel: CardiovascularPanel) -> None:
        for gene in panel.genes:
            assert gene.notes, f"{gene.gene_symbol} has no notes"


# ── Evidence level assignment tests ──────────────────────────────────────


class TestEvidenceLevelAssignment:
    """Test _assign_evidence_level based on ClinVar review stars."""

    def test_pathogenic_2_plus_stars_gives_4(self) -> None:
        assert _assign_evidence_level("Pathogenic", 2, 4) == 4

    def test_pathogenic_3_stars_gives_4(self) -> None:
        assert _assign_evidence_level("Pathogenic", 3, 4) == 4

    def test_likely_pathogenic_2_stars_gives_4(self) -> None:
        assert _assign_evidence_level("Likely pathogenic", 2, 3) == 4

    def test_pathogenic_1_star_gives_4(self) -> None:
        assert _assign_evidence_level("Pathogenic", 1, 4) == 4

    def test_likely_pathogenic_1_star_gives_3(self) -> None:
        assert _assign_evidence_level("Likely pathogenic", 1, 3) == 3

    def test_pathogenic_0_stars_capped_at_2(self) -> None:
        assert _assign_evidence_level("Pathogenic", 0, 4) == 2

    def test_pathogenic_0_stars_low_gene_evidence(self) -> None:
        assert _assign_evidence_level("Pathogenic", 0, 1) == 1


# ── Extraction tests ─────────────────────────────────────────────────────


class TestExtractCardiovascularVariants:
    """Test ClinVar P/LP extraction from annotated variants."""

    def test_extracts_pathogenic_variants(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        assert result.pathogenic_count == 5

    def test_excludes_benign_variants(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        rsids = {v.rsid for v in result.variants}
        assert "rs2228671" not in rsids  # LDLR Benign

    def test_excludes_non_panel_genes(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        rsids = {v.rsid for v in result.variants}
        assert "rs80357906" not in rsids  # BRCA1 not in cardiovascular panel

    def test_excludes_vus(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        rsids = {v.rsid for v in result.variants}
        assert "rs999777" not in rsids  # VUS

    def test_excludes_non_carried_zygosity(
        self, panel: CardiovascularPanel, sample_engine: sa.Engine
    ) -> None:
        """A P/LP variant in a panel gene must NOT be reported when the
        individual does not carry the ALT allele (carriage-bug fix)."""
        variants = [
            {
                "rsid": "rs_ldlr_het",
                "chrom": "19",
                "pos": 11200000,
                "ref": "C",
                "alt": "T",
                "genotype": "CT",
                "zygosity": "het",
                "gene_symbol": "LDLR",
                "clinvar_significance": "Pathogenic",
                "clinvar_review_stars": 3,
                "clinvar_conditions": "Familial hypercholesterolemia",
                "annotation_coverage": 2,
            },
            {
                "rsid": "rs_ldlr_homref",
                "chrom": "19",
                "pos": 11200100,
                "ref": "C",
                "alt": "T",
                "genotype": "CC",
                "zygosity": "hom_ref",
                "gene_symbol": "LDLR",
                "clinvar_significance": "Pathogenic",
                "clinvar_review_stars": 4,
                "clinvar_conditions": "Familial hypercholesterolemia",
                "annotation_coverage": 2,
            },
            {
                "rsid": "rs_myh7_indel",
                "chrom": "14",
                "pos": 23900000,
                "ref": "CTC",
                "alt": "C",
                "genotype": "II",
                "zygosity": None,
                "gene_symbol": "MYH7",
                "clinvar_significance": "Pathogenic",
                "clinvar_review_stars": 3,
                "clinvar_conditions": "Hypertrophic cardiomyopathy",
                "annotation_coverage": 2,
            },
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(annotated_variants), variants)

        result = extract_cardiovascular_variants(panel, sample_engine)
        kept = {v.rsid for v in result.variants}
        assert kept == {"rs_ldlr_het"}

    def test_fh_status_negative_when_only_homozygous_reference(
        self, panel: CardiovascularPanel, sample_engine: sa.Engine
    ) -> None:
        """The dangerous false-positive: hom-ref LDLR/APOB/PCSK9 probes must
        NOT yield an FH 'Positive' status."""
        variants = [
            {
                "rsid": "rs_ldlr_homref_1",
                "chrom": "19",
                "pos": 11210000,
                "ref": "G",
                "alt": "A",
                "genotype": "GG",
                "zygosity": "hom_ref",
                "gene_symbol": "LDLR",
                "clinvar_significance": "Pathogenic",
                "clinvar_review_stars": 4,
                "clinvar_conditions": "Familial hypercholesterolemia",
                "annotation_coverage": 2,
            },
            {
                "rsid": "rs_apob_homref",
                "chrom": "2",
                "pos": 21000000,
                "ref": "C",
                "alt": "T",
                "genotype": "CC",
                "zygosity": "hom_ref",
                "gene_symbol": "APOB",
                "clinvar_significance": "Likely pathogenic",
                "clinvar_review_stars": 2,
                "clinvar_conditions": "Familial hypercholesterolemia",
                "annotation_coverage": 2,
            },
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(annotated_variants), variants)

        result = extract_cardiovascular_variants(panel, sample_engine)
        fh = determine_fh_status(result)
        assert result.pathogenic_count == 0
        assert fh.status == FH_STATUS_NEGATIVE
        assert fh.is_positive is False

    def test_ldlr_golden_fixture(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        """T3-19: LDLR rs28942078 must be Pathogenic with ★★★★."""
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        ldlr_variants = [v for v in result.variants if v.rsid == "rs28942078"]
        assert len(ldlr_variants) == 1
        v = ldlr_variants[0]
        assert v.gene_symbol == "LDLR"
        assert v.clinvar_significance == "Pathogenic"
        assert v.evidence_level == 4
        assert v.clinvar_review_stars == 3
        assert v.cardiovascular_category == CATEGORY_FH

    def test_ldlr_has_condition_enrichment(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        ldlr = [v for v in result.variants if v.rsid == "rs28942078"][0]
        assert len(ldlr.conditions) > 0
        assert any("Hypercholesterolemia" in c for c in ldlr.conditions)

    def test_fh_variants_grouped(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        fh = result.fh_variants
        genes = {v.gene_symbol for v in fh}
        assert genes == {"LDLR", "PCSK9"}

    def test_cardiomyopathy_variants_grouped(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        cm = result.cardiomyopathy_variants
        genes = {v.gene_symbol for v in cm}
        assert genes == {"MYBPC3"}

    def test_channelopathy_variants_grouped(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        ch = result.channelopathy_variants
        genes = {v.gene_symbol for v in ch}
        assert genes == {"KCNQ1", "SCN5A"}

    def test_scn5a_evidence_level_3(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        """SCN5A Likely pathogenic with 1-star → evidence level 3."""
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        scn5a = [v for v in result.variants if v.gene_symbol == "SCN5A"]
        assert len(scn5a) == 1
        assert scn5a[0].evidence_level == 3

    def test_pcsk9_0_star_capped(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        """PCSK9 Pathogenic with 0-star → evidence capped at 2."""
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        pcsk9 = [v for v in result.variants if v.gene_symbol == "PCSK9"]
        assert len(pcsk9) == 1
        assert pcsk9[0].evidence_level == 2

    def test_panel_genes_checked_count(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        assert result.panel_genes_checked == 16

    def test_variants_in_panel_genes_count(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        # LDLR (x2 incl benign + VUS), MYBPC3, KCNQ1, SCN5A, PCSK9 = 7 in panel genes
        # (BRCA1 is not in panel)
        assert result.variants_in_panel_genes == 7

    def test_inheritance_pattern_enrichment(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        ldlr = [v for v in result.variants if v.rsid == "rs28942078"][0]
        assert ldlr.inheritance == "AD"

    def test_pmids_populated(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        ldlr = [v for v in result.variants if v.rsid == "rs28942078"][0]
        assert len(ldlr.pmids) > 0

    def test_empty_sample_returns_no_variants(
        self, panel: CardiovascularPanel, empty_sample: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, empty_sample)
        assert result.pathogenic_count == 0
        assert result.variants == []
        assert result.panel_genes_checked == 16


# ── Findings storage tests ───────────────────────────────────────────────


class TestStoreCardiovascularFindings:
    """Test cardiovascular findings storage in the sample database."""

    def test_stores_correct_count(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        count = store_cardiovascular_findings(result, sample_with_cv_variants)
        assert count == 5

    def test_findings_have_module_cardiovascular(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        store_cardiovascular_findings(result, sample_with_cv_variants)

        with sample_with_cv_variants.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == "cardiovascular")
            ).fetchall()
        assert len(rows) == 5
        for row in rows:
            assert row.module == "cardiovascular"
            assert row.category == "monogenic_variant"

    def test_finding_text_contains_gene_and_rsid(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        store_cardiovascular_findings(result, sample_with_cv_variants)

        with sample_with_cv_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs28942078")
            ).fetchone()
        assert row is not None
        assert "LDLR" in row.finding_text
        assert "rs28942078" in row.finding_text
        assert "Pathogenic" in row.finding_text

    def test_detail_json_has_clinvar_data(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        store_cardiovascular_findings(result, sample_with_cv_variants)

        with sample_with_cv_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs28942078")
            ).fetchone()
        assert row is not None
        detail = json.loads(row.detail_json)
        assert detail["clinvar_accession"] == "VCV000018390"
        assert detail["clinvar_review_stars"] == 3
        assert detail["inheritance"] == "AD"
        assert detail["cardiovascular_category"] == CATEGORY_FH
        assert len(detail["conditions"]) > 0

    def test_pmid_citations_stored_as_json(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        store_cardiovascular_findings(result, sample_with_cv_variants)

        with sample_with_cv_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs28942078")
            ).fetchone()
        pmids = json.loads(row.pmid_citations)
        assert isinstance(pmids, list)
        assert len(pmids) > 0

    def test_clinvar_significance_stored(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        store_cardiovascular_findings(result, sample_with_cv_variants)

        with sample_with_cv_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs28942078")
            ).fetchone()
        assert row.clinvar_significance == "Pathogenic"

    def test_zygosity_stored(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        store_cardiovascular_findings(result, sample_with_cv_variants)

        with sample_with_cv_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs28942078")
            ).fetchone()
        assert row.zygosity == "het"

    def test_clears_previous_findings_on_rerun(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        store_cardiovascular_findings(result, sample_with_cv_variants)
        # Run again
        store_cardiovascular_findings(result, sample_with_cv_variants)

        with sample_with_cv_variants.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "cardiovascular")
            ).scalar()
        assert count == 5  # Not 10 — previous cleared

    def test_empty_result_stores_nothing(
        self, panel: CardiovascularPanel, empty_sample: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, empty_sample)
        count = store_cardiovascular_findings(result, empty_sample)
        assert count == 0

    def test_clears_previous_findings_when_result_becomes_empty(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        """Previous findings should be cleared if new analysis finds no variants."""
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        store_cardiovascular_findings(result, sample_with_cv_variants)

        # Second run with empty result (simulating variant reclassification)
        empty_result = CardiovascularAnalysisResult()
        store_cardiovascular_findings(empty_result, sample_with_cv_variants)

        with sample_with_cv_variants.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "cardiovascular")
            ).scalar()
        assert count == 0

    def test_evidence_levels_stored_correctly(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        store_cardiovascular_findings(result, sample_with_cv_variants)

        with sample_with_cv_variants.connect() as conn:
            rows = conn.execute(
                sa.select(findings.c.rsid, findings.c.evidence_level).where(
                    findings.c.module == "cardiovascular"
                )
            ).fetchall()
        evidence_map = {row.rsid: row.evidence_level for row in rows}

        assert evidence_map["rs28942078"] == 4  # LDLR Pathogenic 3-star
        assert evidence_map["rs121912485"] == 4  # MYBPC3 LP 2-star
        assert evidence_map["rs120074175"] == 4  # KCNQ1 Pathogenic 1-star
        assert evidence_map["rs28937318"] == 3  # SCN5A LP 1-star
        assert evidence_map["rs28362286"] == 2  # PCSK9 Pathogenic 0-star


# ── Result dataclass tests ───────────────────────────────────────────────


class TestCardiovascularAnalysisResult:
    """Test CardiovascularAnalysisResult dataclass properties."""

    def _make_variant(self, gene: str, category: str = CATEGORY_FH) -> CardiovascularVariantResult:
        return CardiovascularVariantResult(
            rsid="rs1",
            gene_symbol=gene,
            genotype="CT",
            zygosity="het",
            clinvar_significance="Pathogenic",
            clinvar_review_stars=3,
            clinvar_accession=None,
            clinvar_conditions=None,
            conditions=[],
            cardiovascular_category=category,
            inheritance="AD",
            evidence_level=4,
            cross_links=[],
            pmids=[],
        )

    def test_pathogenic_count(self) -> None:
        result = CardiovascularAnalysisResult(variants=[self._make_variant("LDLR")])
        assert result.pathogenic_count == 1

    def test_fh_variants(self) -> None:
        result = CardiovascularAnalysisResult(
            variants=[
                self._make_variant("LDLR", CATEGORY_FH),
                self._make_variant("MYBPC3", CATEGORY_CARDIOMYOPATHY),
            ]
        )
        assert len(result.fh_variants) == 1
        assert result.fh_variants[0].gene_symbol == "LDLR"

    def test_cardiomyopathy_variants(self) -> None:
        result = CardiovascularAnalysisResult(
            variants=[self._make_variant("MYBPC3", CATEGORY_CARDIOMYOPATHY)]
        )
        assert len(result.cardiomyopathy_variants) == 1

    def test_channelopathy_variants(self) -> None:
        result = CardiovascularAnalysisResult(
            variants=[self._make_variant("KCNQ1", CATEGORY_CHANNELOPATHY)]
        )
        assert len(result.channelopathy_variants) == 1

    def test_lipid_variants(self) -> None:
        result = CardiovascularAnalysisResult(variants=[self._make_variant("LPA", CATEGORY_LIPID)])
        assert len(result.lipid_variants) == 1

    def test_empty_result(self) -> None:
        result = CardiovascularAnalysisResult()
        assert result.pathogenic_count == 0
        assert result.fh_variants == []
        assert result.cardiomyopathy_variants == []
        assert result.channelopathy_variants == []
        assert result.lipid_variants == []


# ── P3-20: FH status determination tests ───────────────────────────


class TestDetermineFHStatus:
    """Test FH variant status reporting (P3-20)."""

    def _make_fh_variant(
        self,
        gene: str = "LDLR",
        rsid: str = "rs28942078",
        zygosity: str = "het",
        evidence: int = 4,
        significance: str = "Pathogenic",
        review_stars: int = 3,
    ) -> CardiovascularVariantResult:
        return CardiovascularVariantResult(
            rsid=rsid,
            gene_symbol=gene,
            genotype="CT",
            zygosity=zygosity,
            clinvar_significance=significance,
            clinvar_review_stars=review_stars,
            clinvar_accession="VCV000018390",
            clinvar_conditions="Familial hypercholesterolemia",
            conditions=["Familial Hypercholesterolemia"],
            cardiovascular_category=CATEGORY_FH,
            inheritance="AD",
            evidence_level=evidence,
            cross_links=[],
            pmids=["25487149"],
        )

    def test_positive_with_fh_variants(self) -> None:
        result = CardiovascularAnalysisResult(variants=[self._make_fh_variant()])
        fh = determine_fh_status(result)
        assert fh.status == FH_STATUS_POSITIVE
        assert fh.is_positive is True

    def test_negative_with_no_fh_variants(self) -> None:
        """Non-FH cardiovascular variants do not trigger FH positive."""
        cm_variant = CardiovascularVariantResult(
            rsid="rs121912485",
            gene_symbol="MYBPC3",
            genotype="AG",
            zygosity="het",
            clinvar_significance="Pathogenic",
            clinvar_review_stars=3,
            clinvar_accession=None,
            clinvar_conditions=None,
            conditions=["Hypertrophic Cardiomyopathy"],
            cardiovascular_category=CATEGORY_CARDIOMYOPATHY,
            inheritance="AD",
            evidence_level=4,
            cross_links=[],
            pmids=[],
        )
        result = CardiovascularAnalysisResult(variants=[cm_variant])
        fh = determine_fh_status(result)
        assert fh.status == FH_STATUS_NEGATIVE
        assert fh.is_positive is False

    def test_negative_with_empty_result(self) -> None:
        result = CardiovascularAnalysisResult()
        fh = determine_fh_status(result)
        assert fh.status == FH_STATUS_NEGATIVE
        assert fh.variant_count == 0
        assert fh.affected_genes == []

    def test_affected_genes_listed(self) -> None:
        result = CardiovascularAnalysisResult(
            variants=[
                self._make_fh_variant(gene="LDLR", rsid="rs1"),
                self._make_fh_variant(gene="PCSK9", rsid="rs2"),
            ]
        )
        fh = determine_fh_status(result)
        assert fh.affected_genes == ["LDLR", "PCSK9"]

    def test_variant_count(self) -> None:
        result = CardiovascularAnalysisResult(
            variants=[
                self._make_fh_variant(gene="LDLR", rsid="rs1"),
                self._make_fh_variant(gene="LDLR", rsid="rs2"),
            ]
        )
        fh = determine_fh_status(result)
        assert fh.variant_count == 2

    def test_heterozygous_flag(self) -> None:
        result = CardiovascularAnalysisResult(variants=[self._make_fh_variant(zygosity="het")])
        fh = determine_fh_status(result)
        assert fh.has_homozygous is False

    def test_homozygous_flag(self) -> None:
        result = CardiovascularAnalysisResult(variants=[self._make_fh_variant(zygosity="hom_alt")])
        fh = determine_fh_status(result)
        assert fh.has_homozygous is True

    def test_highest_evidence_level(self) -> None:
        result = CardiovascularAnalysisResult(
            variants=[
                self._make_fh_variant(gene="LDLR", rsid="rs1", evidence=4),
                self._make_fh_variant(gene="PCSK9", rsid="rs2", evidence=2),
            ]
        )
        fh = determine_fh_status(result)
        assert fh.highest_evidence_level == 4

    def test_summary_text_positive(self) -> None:
        result = CardiovascularAnalysisResult(variants=[self._make_fh_variant()])
        fh = determine_fh_status(result)
        assert "Familial Hypercholesterolemia" in fh.summary_text
        assert "LDLR" in fh.summary_text

    def test_summary_text_negative(self) -> None:
        result = CardiovascularAnalysisResult()
        fh = determine_fh_status(result)
        assert "No pathogenic" in fh.summary_text
        assert "LDLR" in fh.summary_text

    def test_summary_text_homozygous_note(self) -> None:
        result = CardiovascularAnalysisResult(variants=[self._make_fh_variant(zygosity="hom_alt")])
        fh = determine_fh_status(result)
        assert "homozygous" in fh.summary_text

    def test_fh_from_full_extraction(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        """Integration: extract cardiovascular variants → determine FH status."""
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        fh = determine_fh_status(result)
        assert fh.status == FH_STATUS_POSITIVE
        assert "LDLR" in fh.affected_genes
        assert "PCSK9" in fh.affected_genes
        assert fh.variant_count == 2

    def test_fh_from_empty_extraction(
        self, panel: CardiovascularPanel, empty_sample: sa.Engine
    ) -> None:
        result = extract_cardiovascular_variants(panel, empty_sample)
        fh = determine_fh_status(result)
        assert fh.status == FH_STATUS_NEGATIVE


# ── P3-20: FH status finding storage tests ────────────────────────


class TestStoreFHStatusFinding:
    """Test FH status summary finding storage."""

    def _make_fh_variant(
        self,
        gene: str = "LDLR",
        rsid: str = "rs28942078",
        evidence: int = 4,
    ) -> CardiovascularVariantResult:
        return CardiovascularVariantResult(
            rsid=rsid,
            gene_symbol=gene,
            genotype="CT",
            zygosity="het",
            clinvar_significance="Pathogenic",
            clinvar_review_stars=3,
            clinvar_accession="VCV000018390",
            clinvar_conditions="Familial hypercholesterolemia",
            conditions=["Familial Hypercholesterolemia"],
            cardiovascular_category=CATEGORY_FH,
            inheritance="AD",
            evidence_level=evidence,
            cross_links=[],
            pmids=["25487149"],
        )

    def test_stores_positive_finding(self, sample_engine: sa.Engine) -> None:
        fh = FHStatus(
            status=FH_STATUS_POSITIVE,
            affected_genes=["LDLR"],
            variant_count=1,
            variants=[self._make_fh_variant()],
            has_homozygous=False,
            highest_evidence_level=4,
        )
        count = store_fh_status_finding(fh, sample_engine)
        assert count == 1

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "cardiovascular",
                    findings.c.category == "fh_status",
                )
            ).fetchone()
        assert row is not None
        assert "Familial Hypercholesterolemia" in row.finding_text
        assert row.evidence_level == 4
        assert row.conditions == "Familial Hypercholesterolemia"

    def test_stores_negative_finding(self, sample_engine: sa.Engine) -> None:
        fh = FHStatus(
            status=FH_STATUS_NEGATIVE,
            affected_genes=[],
            variant_count=0,
            variants=[],
            has_homozygous=False,
            highest_evidence_level=0,
        )
        count = store_fh_status_finding(fh, sample_engine)
        assert count == 1

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "cardiovascular",
                    findings.c.category == "fh_status",
                )
            ).fetchone()
        assert row is not None
        assert "No pathogenic" in row.finding_text
        assert row.evidence_level is None
        assert row.conditions is None

    def test_detail_json_has_fh_data(self, sample_engine: sa.Engine) -> None:
        fh = FHStatus(
            status=FH_STATUS_POSITIVE,
            affected_genes=["LDLR", "PCSK9"],
            variant_count=2,
            variants=[
                self._make_fh_variant(gene="LDLR", rsid="rs1"),
                self._make_fh_variant(gene="PCSK9", rsid="rs2"),
            ],
            has_homozygous=False,
            highest_evidence_level=4,
        )
        store_fh_status_finding(fh, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.category == "fh_status")
            ).fetchone()

        detail = json.loads(row.detail_json)
        assert detail["status"] == FH_STATUS_POSITIVE
        assert detail["affected_genes"] == ["LDLR", "PCSK9"]
        assert detail["variant_count"] == 2
        assert detail["has_homozygous"] is False
        assert len(detail["fh_variants"]) == 2

    def test_fh_variant_detail_fields(self, sample_engine: sa.Engine) -> None:
        fh = FHStatus(
            status=FH_STATUS_POSITIVE,
            affected_genes=["LDLR"],
            variant_count=1,
            variants=[self._make_fh_variant()],
            has_homozygous=False,
            highest_evidence_level=4,
        )
        store_fh_status_finding(fh, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.category == "fh_status")
            ).fetchone()

        detail = json.loads(row.detail_json)
        v = detail["fh_variants"][0]
        assert v["rsid"] == "rs28942078"
        assert v["gene_symbol"] == "LDLR"
        assert v["clinvar_significance"] == "Pathogenic"
        assert v["clinvar_review_stars"] == 3
        assert v["evidence_level"] == 4

    def test_clears_previous_fh_status_on_rerun(self, sample_engine: sa.Engine) -> None:
        fh_pos = FHStatus(
            status=FH_STATUS_POSITIVE,
            affected_genes=["LDLR"],
            variant_count=1,
            variants=[self._make_fh_variant()],
            has_homozygous=False,
            highest_evidence_level=4,
        )
        store_fh_status_finding(fh_pos, sample_engine)

        # Second run with negative status
        fh_neg = FHStatus(
            status=FH_STATUS_NEGATIVE,
            affected_genes=[],
            variant_count=0,
            variants=[],
            has_homozygous=False,
            highest_evidence_level=0,
        )
        store_fh_status_finding(fh_neg, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.category == "fh_status")
            ).fetchall()
        assert len(rows) == 1
        assert "No pathogenic" in rows[0].finding_text

    def test_does_not_affect_monogenic_findings(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        """FH status finding is separate from monogenic_variant findings."""
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        store_cardiovascular_findings(result, sample_with_cv_variants)

        fh = determine_fh_status(result)
        store_fh_status_finding(fh, sample_with_cv_variants)

        with sample_with_cv_variants.connect() as conn:
            monogenic_count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(
                    findings.c.module == "cardiovascular",
                    findings.c.category == "monogenic_variant",
                )
            ).scalar()
            fh_count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(
                    findings.c.module == "cardiovascular",
                    findings.c.category == "fh_status",
                )
            ).scalar()

        assert monogenic_count == 5  # Unchanged
        assert fh_count == 1

    def test_full_pipeline_integration(
        self, panel: CardiovascularPanel, sample_with_cv_variants: sa.Engine
    ) -> None:
        """Full pipeline: extract → store monogenic → determine FH → store FH status."""
        result = extract_cardiovascular_variants(panel, sample_with_cv_variants)
        store_cardiovascular_findings(result, sample_with_cv_variants)
        fh = determine_fh_status(result)
        store_fh_status_finding(fh, sample_with_cv_variants)

        assert fh.status == FH_STATUS_POSITIVE
        assert fh.variant_count == 2  # LDLR + PCSK9

        with sample_with_cv_variants.connect() as conn:
            fh_row = conn.execute(
                sa.select(findings).where(findings.c.category == "fh_status")
            ).fetchone()

        assert fh_row is not None
        detail = json.loads(fh_row.detail_json)
        assert detail["status"] == FH_STATUS_POSITIVE
        assert "LDLR" in detail["affected_genes"]
        assert "PCSK9" in detail["affected_genes"]


# ── Autosomal-recessive (ABCG5/ABCG8 sitosterolemia) inheritance gating (#36) ──


def _abcg_variant(rsid: str, pos: int, genotype: str, zygosity: str) -> dict:
    """An ABCG5 ClinVar P/LP annotated_variants row for AR-gating tests."""
    return {
        "rsid": rsid,
        "chrom": "2",
        "pos": pos,
        "genotype": genotype,
        "zygosity": zygosity,
        "gene_symbol": "ABCG5",
        "clinvar_significance": "Pathogenic",
        "clinvar_review_stars": 2,
        "clinvar_accession": "VCV000000001",
        "clinvar_conditions": "Sitosterolemia",
        "annotation_coverage": 2,
    }


def _store_and_fetch(
    panel: CardiovascularPanel, engine: sa.Engine, rows: list[dict]
) -> tuple[CardiovascularAnalysisResult, list]:
    with engine.begin() as conn:
        conn.execute(sa.insert(annotated_variants), rows)
    result = extract_cardiovascular_variants(panel, engine)
    store_cardiovascular_findings(result, engine)
    with engine.connect() as conn:
        finding_rows = (
            conn.execute(sa.select(findings).where(findings.c.module == "cardiovascular"))
            .mappings()
            .all()
        )
    return result, finding_rows


class TestRecessiveInheritanceGating:
    """A single heterozygous ABCG5/ABCG8 P/LP allele is a carrier, not affected (#36)."""

    def test_classify_single_het_ar_is_carrier(self) -> None:
        v = CardiovascularVariantResult(
            rsid="rs1",
            gene_symbol="ABCG5",
            genotype="CT",
            zygosity="het",
            clinvar_significance="Pathogenic",
            clinvar_review_stars=2,
            clinvar_accession="VCV1",
            clinvar_conditions="Sitosterolemia",
            conditions=["Sitosterolemia"],
            cardiovascular_category="lipid_metabolism",
            inheritance="AR",
            evidence_level=3,
            cross_links=[],
            pmids=[],
        )
        assert classify_disease_status(v, [v]) == DISEASE_CARRIER

    def test_classify_homozygous_ar_is_affected(self) -> None:
        v = CardiovascularVariantResult(
            rsid="rs1",
            gene_symbol="ABCG5",
            genotype="TT",
            zygosity="hom_alt",
            clinvar_significance="Pathogenic",
            clinvar_review_stars=2,
            clinvar_accession="VCV1",
            clinvar_conditions="Sitosterolemia",
            conditions=["Sitosterolemia"],
            cardiovascular_category="lipid_metabolism",
            inheritance="AR",
            evidence_level=3,
            cross_links=[],
            pmids=[],
        )
        assert classify_disease_status(v, [v]) == DISEASE_AFFECTED

    def test_classify_ad_het_is_affected(self) -> None:
        v = CardiovascularVariantResult(
            rsid="rs2",
            gene_symbol="LDLR",
            genotype="CT",
            zygosity="het",
            clinvar_significance="Pathogenic",
            clinvar_review_stars=3,
            clinvar_accession="VCV2",
            clinvar_conditions="Familial hypercholesterolemia",
            conditions=["Familial hypercholesterolemia"],
            cardiovascular_category="fh",
            inheritance="AD",
            evidence_level=4,
            cross_links=[],
            pmids=[],
        )
        assert classify_disease_status(v, [v]) == DISEASE_AFFECTED

    def test_single_het_abcg5_finding_is_carrier_not_affected(
        self, panel: CardiovascularPanel, sample_engine: sa.Engine
    ) -> None:
        """The issue's core case: one het ABCG5 P/LP must NOT read as affected disease."""
        _, rows = _store_and_fetch(
            panel, sample_engine, [_abcg_variant("rs1", 44039611, "CT", "het")]
        )
        assert len(rows) == 1
        text = rows[0]["finding_text"]
        assert "carrier" in text.lower()
        assert "autosomal recessive" in text.lower()
        # Must NOT assert the affected-disease phrasing for a single allele.
        assert "Pathogenic for Sitosterolemia" not in text
        assert json.loads(rows[0]["detail_json"])["disease_status"] == DISEASE_CARRIER

    def test_homozygous_abcg5_finding_is_affected(
        self, panel: CardiovascularPanel, sample_engine: sa.Engine
    ) -> None:
        """A homozygous (biallelic) ABCG5 genotype still reads as affected disease."""
        _, rows = _store_and_fetch(
            panel, sample_engine, [_abcg_variant("rs1", 44039611, "TT", "hom_alt")]
        )
        assert len(rows) == 1
        text = rows[0]["finding_text"]
        assert "Pathogenic for Sitosterolemia" in text
        assert json.loads(rows[0]["detail_json"])["disease_status"] == DISEASE_AFFECTED

    def test_two_het_abcg5_is_possible_biallelic(
        self, panel: CardiovascularPanel, sample_engine: sa.Engine
    ) -> None:
        """Two het P/LP loci in one AR gene → possible compound het, flagged unconfirmed."""
        _, rows = _store_and_fetch(
            panel,
            sample_engine,
            [
                _abcg_variant("rs1", 44039611, "CT", "het"),
                _abcg_variant("rs2", 44042000, "AG", "het"),
            ],
        )
        assert len(rows) == 2
        for row in rows:
            text = row["finding_text"]
            assert "Pathogenic for Sitosterolemia" not in text
            assert json.loads(row["detail_json"])["disease_status"] == DISEASE_POSSIBLE_BIALLELIC
            assert "compound" in text.lower() or "unconfirmed" in text.lower()
