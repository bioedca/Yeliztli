"""Tests for the curated cancer gene panel (P3-12).

Covers:
  - Panel JSON loading and validation
  - All 28 genes present (22 gene groups per PRD)
  - Gene lookup by symbol
  - Syndrome and cancer type queries
  - BRCA1/2 dual-role cross-links to carrier module
  - Expected ClinVar rsids are populated
  - Evidence levels are valid (1-4)
  - Inheritance patterns are valid (AD/AR)
  - Panel structure integrity
  - T3-12 prerequisite: BRCA1 rs80357906 is in expected rsids
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.cancer import (
    CancerAnalysisResult,
    CancerGene,
    CancerPanel,
    CancerVariantResult,
    extract_cancer_variants,
    load_cancer_panel,
    store_cancer_findings,
)
from backend.analysis.inheritance import (
    DISEASE_AFFECTED,
    DISEASE_CARRIER,
    DISEASE_POSSIBLE_BIALLELIC,
    classify_disease_status,
)
from backend.db.tables import annotated_variants, findings

# ── Fixtures ──────────────────────────────────────────────────────────────

PANEL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "cancer_panel.json"
)


@pytest.fixture()
def panel() -> CancerPanel:
    """Load the curated cancer panel from the real JSON file."""
    return load_cancer_panel(PANEL_PATH)


# ── Panel loading tests ──────────────────────────────────────────────────


class TestPanelLoading:
    """Test panel JSON loading and basic structure."""

    def test_panel_loads_successfully(self, panel: CancerPanel) -> None:
        assert panel is not None
        assert panel.module == "cancer"
        assert panel.version == "1.0.0"

    def test_panel_has_description(self, panel: CancerPanel) -> None:
        assert panel.description
        assert "cancer" in panel.description.lower()

    def test_panel_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_cancer_panel(tmp_path / "nonexistent.json")

    def test_panel_malformed_json(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_cancer_panel(bad_file)

    def test_panel_missing_required_field(self, tmp_path: Path) -> None:
        """Missing required field raises ValueError with gene context."""
        bad_panel = tmp_path / "bad_panel.json"
        bad_panel.write_text(
            json.dumps(
                {
                    "module": "cancer",
                    "version": "1.0.0",
                    "description": "test",
                    "genes": [{"gene_symbol": "TEST"}],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Missing required field.*TEST"):
            load_cancer_panel(bad_panel)


# ── Gene count and completeness ──────────────────────────────────────────


class TestGeneCompleteness:
    """Verify all PRD-specified genes are present."""

    # The 22 gene groups from the PRD (expanded to individual genes)
    EXPECTED_GENES = [
        "BRCA1",
        "BRCA2",  # BRCA1/2
        "TP53",
        "PALB2",
        "ATM",
        "CHEK2",
        "RAD51C",
        "RAD51D",  # RAD51C/D
        "MLH1",
        "MSH2",
        "MSH6",  # MSH2/6
        "PMS2",
        "APC",
        "MUTYH",
        "VHL",
        "RET",
        "PTEN",
        "STK11",
        "CDH1",
        "NF1",
        "NF2",  # NF1/2
        "MEN1",
        "SDHA",
        "SDHB",
        "SDHC",
        "SDHD",  # SDHA/B/C/D
        "BAP1",
        "CDKN2A",
    ]

    def test_gene_count(self, panel: CancerPanel) -> None:
        assert len(panel.genes) == 28

    def test_all_expected_genes_present(self, panel: CancerPanel) -> None:
        panel_symbols = set(panel.all_gene_symbols())
        for gene in self.EXPECTED_GENES:
            assert gene in panel_symbols, f"Missing gene: {gene}"

    def test_no_unexpected_genes(self, panel: CancerPanel) -> None:
        panel_symbols = set(panel.all_gene_symbols())
        expected = set(self.EXPECTED_GENES)
        unexpected = panel_symbols - expected
        assert not unexpected, f"Unexpected genes: {unexpected}"


# ── Gene lookup ──────────────────────────────────────────────────────────


class TestGeneLookup:
    """Test gene lookup methods."""

    def test_get_gene_by_symbol(self, panel: CancerPanel) -> None:
        brca1 = panel.get_gene("BRCA1")
        assert brca1 is not None
        assert brca1.gene_symbol == "BRCA1"

    def test_get_gene_case_insensitive(self, panel: CancerPanel) -> None:
        brca1 = panel.get_gene("brca1")
        assert brca1 is not None
        assert brca1.gene_symbol == "BRCA1"

    def test_get_gene_not_found(self, panel: CancerPanel) -> None:
        result = panel.get_gene("NONEXISTENT")
        assert result is None

    def test_genes_by_syndrome_lynch(self, panel: CancerPanel) -> None:
        lynch_genes = panel.genes_by_syndrome("Lynch")
        symbols = {g.gene_symbol for g in lynch_genes}
        assert {"MLH1", "MSH2", "MSH6", "PMS2"} == symbols

    def test_genes_by_cancer_type_breast(self, panel: CancerPanel) -> None:
        breast_genes = panel.genes_by_cancer_type("Breast")
        symbols = {g.gene_symbol for g in breast_genes}
        assert "BRCA1" in symbols
        assert "BRCA2" in symbols
        assert "TP53" in symbols
        assert "PALB2" in symbols

    def test_genes_by_cancer_type_colorectal(self, panel: CancerPanel) -> None:
        crc_genes = panel.genes_by_cancer_type("Colorectal")
        symbols = {g.gene_symbol for g in crc_genes}
        assert "APC" in symbols
        assert "MLH1" in symbols
        assert "MSH2" in symbols


class TestCHEK2ColorectalConditional:
    """CHEK2's colorectal-cancer association is variant-specific (1100delC/I157T)
    and family-history-dependent — recent large cohorts find no overall CRC risk
    in unselected carriers (Bychkovsky 2022; Mundt 2023). So 'Colorectal' is not an
    unconditional cancer_type, the conditional rationale is kept in notes, and the
    generic colorectal query no longer returns CHEK2 (#324)."""

    def test_chek2_cancer_types_exclude_colorectal(self, panel: CancerPanel) -> None:
        chek2 = panel.get_gene("CHEK2")
        assert chek2 is not None
        assert "Colorectal" not in chek2.cancer_types
        assert "Breast" in chek2.cancer_types  # the established association remains

    def test_chek2_not_returned_for_colorectal_query(self, panel: CancerPanel) -> None:
        symbols = {g.gene_symbol for g in panel.genes_by_cancer_type("Colorectal")}
        assert "CHEK2" not in symbols
        # the established colorectal-cancer genes are unaffected
        assert {"APC", "MLH1", "MSH2"} <= symbols

    def test_chek2_notes_document_conditional_colorectal(self, panel: CancerPanel) -> None:
        # The rationale is documented (not silently dropped), so it can't be
        # re-added as an unconditional type without the caveat.
        notes = panel.get_gene("CHEK2").notes.lower()
        assert "colorectal" in notes
        assert "1100delc" in notes  # variant-specific
        assert "family history" in notes or "familial" in notes  # conditional framing


class TestCHEK2CancerTypes:
    """CHEK2 cancer_types audited against current large-cohort evidence (#393).

    Breast is established. Prostate and kidney are confirmed by population-scale
    genomic-ascertainment cohorts not selected on family history (Kim 2025 JAMA
    Netw Open, PMID 41396600; Mukhtar 2024 J Med Genet, PMID 39209703). Thyroid
    (OR 1.63) and kidney (OR 2.57) are significant in Bychkovsky 2022 (JAMA Oncol,
    PMID 36136322). The common attenuated missense alleles (I157T/S428F/T476M)
    carry NO non-breast association — documented in notes."""

    def test_chek2_cancer_types_audited_set(self, panel: CancerPanel) -> None:
        chek2 = panel.get_gene("CHEK2")
        assert chek2 is not None
        assert set(chek2.cancer_types) == {"Breast", "Prostate", "Thyroid", "Kidney"}

    def test_chek2_returned_for_thyroid_and_kidney_queries(self, panel: CancerPanel) -> None:
        for cancer_type in ("Thyroid", "Kidney", "Prostate"):
            symbols = {g.gene_symbol for g in panel.genes_by_cancer_type(cancer_type)}
            assert "CHEK2" in symbols, f"CHEK2 missing from {cancer_type} query"

    def test_chek2_cites_audit_evidence(self, panel: CancerPanel) -> None:
        # The large-cohort evidence that adds thyroid/kidney and confirms prostate.
        pmids = panel.get_gene("CHEK2").pmids
        for pmid in ("36136322", "41396600", "39209703"):
            assert pmid in pmids, f"CHEK2 missing audit PMID {pmid}"

    def test_chek2_notes_document_attenuated_variant_caveat(self, panel: CancerPanel) -> None:
        # The non-breast associations do NOT apply to the common attenuated
        # missense alleles — this caveat must survive in notes so the broader
        # cancer_types can't be read as variant-agnostic.
        notes = panel.get_gene("CHEK2").notes.lower()
        assert "i157t" in notes
        assert "s428f" in notes
        assert "t476m" in notes
        assert "non-breast" in notes or "nonbreast" in notes


# ── Citation provenance ──────────────────────────────────────────────────


class TestLynchCitations:
    """Lynch syndrome (mismatch-repair) rows must cite Lynch/MMR literature, not
    the unrelated environmental-chemistry paper that was attached (#180)."""

    def test_lynch_genes_drop_unrelated_chemistry_pmid(self, panel: CancerPanel) -> None:
        # 28774630 = 'The chlorination transformation characteristics of
        # benzophenone-4...' (J Environ Sci 2017) — unrelated to Lynch syndrome
        # or DNA mismatch repair. It must not appear on any Lynch (MMR) gene.
        for gene in panel.genes_by_syndrome("Lynch"):
            assert "28774630" not in gene.pmids, (
                f"{gene.gene_symbol} cites unrelated chemistry PMID 28774630"
            )

    def test_mmr_genes_cite_curated_pmids(self, panel: CancerPanel) -> None:
        # All four MMR Lynch genes cite the Lynch GeneReviews overview (20301390)
        # plus the gene-specific cancer-risk evidence from the Prospective Lynch
        # Syndrome Database (31337882, Dominguez-Valentin et al., Genet Med 2020,
        # which reports risks for MLH1/MSH2/MSH6 *and* PMS2). PMS2 previously
        # cited the unrelated CHEK2/papillary-thyroid paper 25583358 (#283).
        for symbol in ("MLH1", "MSH2", "MSH6", "PMS2"):
            gene = panel.get_gene(symbol)
            assert gene is not None
            assert gene.pmids == ["20301390", "31337882"], (symbol, gene.pmids)

    def test_lynch_genes_drop_unrelated_chek2_thyroid_pmid(self, panel: CancerPanel) -> None:
        # 25583358 = 'CHEK2 mutations and the risk of papillary thyroid cancer'
        # (Int J Cancer 2015) — CHEK2 is not a mismatch-repair gene and papillary
        # thyroid cancer is not Lynch. It must not appear on any Lynch (MMR) gene
        # (#283). (RAD51C's separate 25583358 mis-cite is tracked in #284.)
        for gene in panel.genes_by_syndrome("Lynch"):
            assert "25583358" not in gene.pmids, (
                f"{gene.gene_symbol} cites unrelated CHEK2/thyroid PMID 25583358"
            )


# ── Cross-links and dual-role genes ──────────────────────────────────────


class TestCrossLinks:
    """Test BRCA1/2 dual-role cross-links."""

    def test_brca1_has_carrier_cross_link(self, panel: CancerPanel) -> None:
        brca1 = panel.get_gene("BRCA1")
        assert brca1 is not None
        assert "carrier" in brca1.cross_links
        assert brca1.is_dual_role

    def test_brca2_has_carrier_cross_link(self, panel: CancerPanel) -> None:
        brca2 = panel.get_gene("BRCA2")
        assert brca2 is not None
        assert "carrier" in brca2.cross_links
        assert brca2.is_dual_role

    def test_dual_role_genes_are_brca_only(self, panel: CancerPanel) -> None:
        dual = panel.dual_role_genes()
        symbols = {g.gene_symbol for g in dual}
        assert symbols == {"BRCA1", "BRCA2"}

    def test_non_brca_genes_have_no_cross_links(self, panel: CancerPanel) -> None:
        tp53 = panel.get_gene("TP53")
        assert tp53 is not None
        assert not tp53.is_dual_role
        assert tp53.cross_links == []


# ── Expected ClinVar rsids ───────────────────────────────────────────────


class TestExpectedClinVarRsids:
    """Test expected ClinVar P/LP rsid entries."""

    def test_all_genes_have_expected_rsids(self, panel: CancerPanel) -> None:
        for gene in panel.genes:
            assert len(gene.expected_clinvar_rsids) > 0, (
                f"{gene.gene_symbol} has no expected ClinVar rsids"
            )

    def test_rsids_are_valid_format(self, panel: CancerPanel) -> None:
        for gene in panel.genes:
            for rsid in gene.expected_clinvar_rsids:
                assert rsid.startswith("rs"), f"Invalid rsid format: {rsid} in {gene.gene_symbol}"
                # Ensure the numeric part is valid
                assert rsid[2:].isdigit(), (
                    f"Invalid rsid numeric part: {rsid} in {gene.gene_symbol}"
                )

    def test_no_duplicate_rsids_within_gene(self, panel: CancerPanel) -> None:
        for gene in panel.genes:
            rsids = gene.expected_clinvar_rsids
            assert len(rsids) == len(set(rsids)), f"Duplicate rsids in {gene.gene_symbol}"

    def test_total_expected_rsids(self, panel: CancerPanel) -> None:
        """Panel should have a substantial number of expected rsids."""
        all_rsids = panel.all_expected_rsids()
        assert len(all_rsids) >= 100  # At least 100 across all genes

    def test_brca1_rs80357906_present(self, panel: CancerPanel) -> None:
        """T3-12 prerequisite: BRCA1 rs80357906 must be in expected rsids."""
        brca1 = panel.get_gene("BRCA1")
        assert brca1 is not None
        assert "rs80357906" in brca1.expected_clinvar_rsids


# ── Evidence levels ──────────────────────────────────────────────────────


class TestEvidenceLevels:
    """Test evidence level assignments."""

    def test_evidence_levels_valid_range(self, panel: CancerPanel) -> None:
        for gene in panel.genes:
            assert 1 <= gene.evidence_level <= 4, (
                f"{gene.gene_symbol} has invalid evidence level: {gene.evidence_level}"
            )

    def test_high_evidence_genes(self, panel: CancerPanel) -> None:
        """BRCA1/2, TP53, MLH1, MSH2, APC should be 4-star."""
        four_star_genes = ["BRCA1", "BRCA2", "TP53", "MLH1", "MSH2", "APC"]
        for symbol in four_star_genes:
            gene = panel.get_gene(symbol)
            assert gene is not None
            assert gene.evidence_level == 4, (
                f"{symbol} should be 4-star evidence, got {gene.evidence_level}"
            )

    def test_moderate_evidence_genes(self, panel: CancerPanel) -> None:
        """ATM, CHEK2 should be 3-star (moderate penetrance)."""
        three_star_genes = ["ATM", "CHEK2"]
        for symbol in three_star_genes:
            gene = panel.get_gene(symbol)
            assert gene is not None
            assert gene.evidence_level == 3, (
                f"{symbol} should be 3-star evidence, got {gene.evidence_level}"
            )


# ── Inheritance patterns ─────────────────────────────────────────────────


class TestInheritance:
    """Test inheritance pattern assignments."""

    def test_inheritance_values_valid(self, panel: CancerPanel) -> None:
        valid_patterns = {"AD", "AR"}
        for gene in panel.genes:
            assert gene.inheritance in valid_patterns, (
                f"{gene.gene_symbol} has invalid inheritance: {gene.inheritance}"
            )

    def test_mutyh_is_autosomal_recessive(self, panel: CancerPanel) -> None:
        """MUTYH-Associated Polyposis is AR."""
        mutyh = panel.get_gene("MUTYH")
        assert mutyh is not None
        assert mutyh.inheritance == "AR"

    def test_most_genes_are_autosomal_dominant(self, panel: CancerPanel) -> None:
        """Most cancer predisposition genes are AD."""
        ad_count = sum(1 for g in panel.genes if g.inheritance == "AD")
        assert ad_count >= 27  # All except MUTYH should be AD


# ── PubMed citations ─────────────────────────────────────────────────────


class TestPMIDs:
    """Test PubMed citation data."""

    def test_all_genes_have_pmids(self, panel: CancerPanel) -> None:
        for gene in panel.genes:
            assert len(gene.pmids) > 0, f"{gene.gene_symbol} has no PubMed citations"

    def test_pmids_are_numeric(self, panel: CancerPanel) -> None:
        for gene in panel.genes:
            for pmid in gene.pmids:
                assert pmid.isdigit(), f"Invalid PMID: {pmid} in {gene.gene_symbol}"


# ── Gene metadata ────────────────────────────────────────────────────────


class TestGeneMetadata:
    """Test gene metadata completeness."""

    def test_all_genes_have_syndromes(self, panel: CancerPanel) -> None:
        for gene in panel.genes:
            assert len(gene.syndromes) > 0, f"{gene.gene_symbol} has no syndromes"

    def test_all_genes_have_cancer_types(self, panel: CancerPanel) -> None:
        for gene in panel.genes:
            assert len(gene.cancer_types) > 0, f"{gene.gene_symbol} has no cancer types"

    def test_all_genes_have_chromosome(self, panel: CancerPanel) -> None:
        valid_chroms = {str(i) for i in range(1, 23)} | {"X", "Y"}
        for gene in panel.genes:
            assert gene.chromosome in valid_chroms, (
                f"{gene.gene_symbol} has invalid chromosome: {gene.chromosome}"
            )

    def test_all_genes_have_name(self, panel: CancerPanel) -> None:
        for gene in panel.genes:
            assert gene.name, f"{gene.gene_symbol} has no name"

    def test_all_genes_have_notes(self, panel: CancerPanel) -> None:
        for gene in panel.genes:
            assert gene.notes, f"{gene.gene_symbol} has no notes"


# ── Dataclass properties ─────────────────────────────────────────────────


class TestDataclassProperties:
    """Test CancerGene dataclass properties."""

    def test_is_dual_role_true(self) -> None:
        gene = CancerGene(
            gene_symbol="TEST",
            name="Test Gene",
            chromosome="1",
            syndromes=["Test Syndrome"],
            cancer_types=["Test Cancer"],
            inheritance="AD",
            evidence_level=4,
            cross_links=["carrier"],
            expected_clinvar_rsids=["rs123"],
            pmids=["12345"],
            notes="Test note",
        )
        assert gene.is_dual_role is True

    def test_is_dual_role_false(self) -> None:
        gene = CancerGene(
            gene_symbol="TEST",
            name="Test Gene",
            chromosome="1",
            syndromes=["Test Syndrome"],
            cancer_types=["Test Cancer"],
            inheritance="AD",
            evidence_level=3,
            cross_links=[],
            expected_clinvar_rsids=["rs123"],
            pmids=["12345"],
            notes="Test note",
        )
        assert gene.is_dual_role is False


# ── AR-gating (MUTYH / MAP) regression — issue #86 ────────────────────────


def _mutyh_variant(rsid: str, pos: int, genotype: str, zygosity: str) -> dict:
    """A MUTYH ClinVar P/LP annotated_variants row for AR-gating tests."""
    return {
        "rsid": rsid,
        "chrom": "1",
        "pos": pos,
        "genotype": genotype,
        "zygosity": zygosity,
        "gene_symbol": "MUTYH",
        "clinvar_significance": "Pathogenic",
        "clinvar_review_stars": 2,
        "clinvar_accession": "VCV000000001",
        "clinvar_conditions": "MUTYH-associated polyposis",
        "annotation_coverage": 2,
    }


def _store_and_fetch(
    panel: CancerPanel, engine: sa.Engine, rows: list[dict]
) -> tuple[CancerAnalysisResult, list]:
    with engine.begin() as conn:
        conn.execute(sa.insert(annotated_variants), rows)
    result = extract_cancer_variants(panel, engine)
    store_cancer_findings(result, engine)
    with engine.connect() as conn:
        finding_rows = (
            conn.execute(sa.select(findings).where(findings.c.module == "cancer")).mappings().all()
        )
    return result, finding_rows


def _mutyh_result_variant(rsid: str, genotype: str, zygosity: str) -> CancerVariantResult:
    """A MUTYH CancerVariantResult for unit-testing classify_disease_status."""
    return CancerVariantResult(
        rsid=rsid,
        gene_symbol="MUTYH",
        genotype=genotype,
        zygosity=zygosity,
        clinvar_significance="Pathogenic",
        clinvar_review_stars=2,
        clinvar_accession="VCV1",
        clinvar_conditions="MUTYH-associated polyposis",
        syndromes=["MUTYH-Associated Polyposis (MAP)"],
        cancer_types=["Colorectal"],
        inheritance="AR",
        evidence_level=4,
        cross_links=[],
        pmids=[],
    )


class TestRecessiveInheritanceGating:
    """A single heterozygous MUTYH P/LP allele is a carrier, not MAP-affected (#86)."""

    def test_classify_single_het_ar_is_carrier(self) -> None:
        v = _mutyh_result_variant("rs1", "CT", "het")
        assert classify_disease_status(v, [v]) == DISEASE_CARRIER

    def test_classify_homozygous_ar_is_affected(self) -> None:
        v = _mutyh_result_variant("rs1", "TT", "hom_alt")
        assert classify_disease_status(v, [v]) == DISEASE_AFFECTED

    def test_classify_two_het_ar_is_possible_biallelic(self) -> None:
        v1 = _mutyh_result_variant("rs1", "CT", "het")
        v2 = _mutyh_result_variant("rs2", "AG", "het")
        assert classify_disease_status(v1, [v1, v2]) == DISEASE_POSSIBLE_BIALLELIC

    def test_classify_ad_het_is_affected(self) -> None:
        v = CancerVariantResult(
            rsid="rs2",
            gene_symbol="BRCA1",
            genotype="CT",
            zygosity="het",
            clinvar_significance="Pathogenic",
            clinvar_review_stars=3,
            clinvar_accession="VCV2",
            clinvar_conditions="Hereditary breast and ovarian cancer",
            syndromes=["Hereditary Breast and Ovarian Cancer (HBOC)"],
            cancer_types=["Breast", "Ovarian"],
            inheritance="AD",
            evidence_level=4,
            cross_links=["carrier"],
            pmids=[],
        )
        assert classify_disease_status(v, [v]) == DISEASE_AFFECTED

    def test_single_het_mutyh_finding_is_carrier_not_affected(
        self, panel: CancerPanel, sample_engine: sa.Engine
    ) -> None:
        """The issue's core case: one het MUTYH P/LP must NOT read as MAP-affected."""
        _, rows = _store_and_fetch(
            panel, sample_engine, [_mutyh_variant("rs1", 45330000, "CT", "het")]
        )
        assert len(rows) == 1
        text = rows[0]["finding_text"]
        assert "carrier" in text.lower()
        assert "autosomal recessive" in text.lower()
        # Must NOT assert the affected-disease phrasing for a single allele.
        assert "Pathogenic for MUTYH-Associated Polyposis" not in text
        assert json.loads(rows[0]["detail_json"])["disease_status"] == DISEASE_CARRIER

    def test_homozygous_mutyh_finding_is_affected(
        self, panel: CancerPanel, sample_engine: sa.Engine
    ) -> None:
        """A homozygous (biallelic) MUTYH genotype still reads as MAP-affected."""
        _, rows = _store_and_fetch(
            panel, sample_engine, [_mutyh_variant("rs1", 45330000, "TT", "hom_alt")]
        )
        assert len(rows) == 1
        text = rows[0]["finding_text"]
        assert "Pathogenic for MUTYH-Associated Polyposis (MAP)" in text
        assert json.loads(rows[0]["detail_json"])["disease_status"] == DISEASE_AFFECTED

    def test_two_het_mutyh_is_possible_biallelic(
        self, panel: CancerPanel, sample_engine: sa.Engine
    ) -> None:
        """Two het P/LP loci in MUTYH → possible compound het, flagged unconfirmed."""
        _, rows = _store_and_fetch(
            panel,
            sample_engine,
            [
                _mutyh_variant("rs1", 45330000, "CT", "het"),
                _mutyh_variant("rs2", 45335000, "AG", "het"),
            ],
        )
        assert len(rows) == 2
        for row in rows:
            text = row["finding_text"]
            assert "Pathogenic for MUTYH-Associated Polyposis" not in text
            assert json.loads(row["detail_json"])["disease_status"] == DISEASE_POSSIBLE_BIALLELIC
            assert "compound" in text.lower() or "unconfirmed" in text.lower()

    def test_ad_gene_het_finding_unchanged(
        self, panel: CancerPanel, sample_engine: sa.Engine
    ) -> None:
        """An AD gene (BRCA1) het P/LP still reports the affected-disease phrasing."""
        row = {
            "rsid": "rs80357906",
            "chrom": "17",
            "pos": 43000000,
            "genotype": "CT",
            "zygosity": "het",
            "gene_symbol": "BRCA1",
            "clinvar_significance": "Pathogenic",
            "clinvar_review_stars": 3,
            "clinvar_accession": "VCV000000002",
            "clinvar_conditions": "Hereditary breast and ovarian cancer",
            "annotation_coverage": 2,
        }
        _, rows = _store_and_fetch(panel, sample_engine, [row])
        assert len(rows) == 1
        text = rows[0]["finding_text"]
        assert " — Pathogenic for " in text
        assert "carrier" not in text.lower()
        assert json.loads(rows[0]["detail_json"])["disease_status"] == DISEASE_AFFECTED


class TestStoreCancerFindingsClearsStale:
    """Issue #252: store_cancer_findings() must not leave a stale hereditary-
    cancer P/LP finding in place when a rerun has no current reportable
    variants."""

    def test_empty_rerun_clears_stale_cancer_findings(
        self, panel: CancerPanel, sample_engine: sa.Engine
    ) -> None:
        # First run: a homozygous MUTYH P/LP variant stores one cancer finding.
        _, rows = _store_and_fetch(
            panel, sample_engine, [_mutyh_variant("rs1", 45330000, "TT", "hom_alt")]
        )
        assert len(rows) == 1

        # Simulate a replaced/rerun sample with no reportable cancer variants.
        with sample_engine.begin() as conn:
            conn.execute(sa.delete(annotated_variants))
        empty = extract_cancer_variants(panel, sample_engine)
        assert store_cancer_findings(empty, sample_engine) == 0

        # The previously-stored cancer finding must be cleared, not left stale.
        with sample_engine.connect() as conn:
            remaining = (
                conn.execute(sa.select(findings).where(findings.c.module == "cancer"))
                .mappings()
                .all()
            )
        assert remaining == []


class TestMUTYHCitationProvenance:
    """Guard the MUTYH evidence links (issue #179).

    The MUTYH row previously cited two unrelated papers — PMID 20301569 (a
    retired Hereditary Neuralgic Amyotrophy GeneReviews chapter) and 28774630
    (a benzophenone-4 chlorination chemistry paper). This pins the row to
    verified MUTYH-Associated-Polyposis / colorectal-cancer references so those
    off-topic PMIDs cannot silently reappear.
    """

    # MUTYH/MAP colorectal-cancer references verified on PubMed + Consensus.
    _MUTYH_PMIDS = frozenset(
        {
            "21063410",  # Theodoratou 2010, BJC — MUTYH CRC risk meta-analysis
            "19620482",  # Lubbe 2009, JCO — biallelic MUTYH CRC risk
        }
    )
    # Unrelated PMIDs wrongly cited by the MUTYH row before the fix.
    _BANNED_FROM_MUTYH = frozenset({"20301569", "28774630"})

    def test_mutyh_cites_verified_map_references(self, panel: CancerPanel) -> None:
        gene = panel.get_gene("MUTYH")
        assert gene is not None
        assert set(gene.pmids) == self._MUTYH_PMIDS

    def test_mutyh_drops_unrelated_pmids(self, panel: CancerPanel) -> None:
        gene = panel.get_gene("MUTYH")
        assert gene is not None
        leaked = self._BANNED_FROM_MUTYH & set(gene.pmids)
        assert not leaked, f"MUTYH still cites unrelated PMID(s) {sorted(leaked)}"

    def test_retired_neuralgic_amyotrophy_pmid_absent_from_panel(self, panel: CancerPanel) -> None:
        # 20301569 is the retired HNA GeneReviews chapter — not a cancer
        # reference and unique to the old MUTYH row, so it must appear nowhere.
        for gene in panel.genes:
            assert "20301569" not in gene.pmids, (
                f"{gene.gene_symbol} cites the unrelated neuralgic-amyotrophy PMID 20301569"
            )


class TestCHEK2CitationProvenance:
    """Guard the CHEK2 evidence links (issue #212).

    The CHEK2 row previously cited two unrelated papers — PMID 20301661 (the
    PTEN Hamartoma Tumor Syndrome GeneReviews chapter) and 29848605 (a Cancer
    Discovery trial of the FGFR1-3 inhibitor BGJ398 in advanced urothelial
    carcinoma). This pins the row to verified CHEK2 hereditary-cancer
    references so those off-topic PMIDs cannot silently reappear.
    """

    # CHEK2 hereditary-cancer references verified on PubMed (esummary/efetch)
    # and cross-checked against the literature (Consensus).
    _CHEK2_PMIDS = frozenset(
        {
            "37490054",  # Hanson 2023, Genet Med — ACMG CHEK2 management practice resource
            "27269948",  # Schmidt 2016, JCO — age/subtype-specific risk for CHEK2*1100delC
            "15492928",  # Cybulski 2004, AJHG — "CHEK2 is a multiorgan cancer susceptibility gene"
            "36136322",  # Bychkovsky 2022 JAMA Oncol: thyroid/kidney + attenuated caveat (#393)
            "41396600",  # Kim 2025 JAMA Netw Open: genomic-ascertainment prostate/kidney (#393)
            "39209703",  # Mukhtar 2024 J Med Genet: UKB WES prostate/kidney (#393)
        }
    )
    # Unrelated PMIDs wrongly cited by the CHEK2 row before the fix. NOTE:
    # 20301661 (PTEN Hamartoma Tumor Syndrome GeneReviews) is legitimately
    # cited by the PTEN row, so it is banned from CHEK2 ONLY, not panel-wide
    # (see test_pten_still_cites_genereviews_pmid).
    _BANNED_FROM_CHEK2 = frozenset({"20301661", "29848605"})

    def test_chek2_cites_verified_references(self, panel: CancerPanel) -> None:
        gene = panel.get_gene("CHEK2")
        assert gene is not None
        assert set(gene.pmids) == self._CHEK2_PMIDS

    def test_chek2_drops_unrelated_pmids(self, panel: CancerPanel) -> None:
        gene = panel.get_gene("CHEK2")
        assert gene is not None
        leaked = self._BANNED_FROM_CHEK2 & set(gene.pmids)
        assert not leaked, f"CHEK2 still cites unrelated PMID(s) {sorted(leaked)}"

    def test_pten_still_cites_genereviews_pmid(self, panel: CancerPanel) -> None:
        # 20301661 (PTEN Hamartoma Tumor Syndrome GeneReviews) is the correct
        # reference for PTEN; removing it from the CHEK2 row must not disturb
        # the legitimate PTEN citation.
        gene = panel.get_gene("PTEN")
        assert gene is not None
        assert "20301661" in gene.pmids


class TestRAD51CCitationProvenance:
    """Guard the RAD51C evidence links (issue #284).

    The RAD51C row previously cited two unrelated papers — PMID 20301399 (the
    Tuberous Sclerosis Complex GeneReviews chapter, no TSC gene exists in this
    panel) and 25583358 (Siolek et al., "CHEK2 mutations and the risk of
    papillary thyroid cancer"). This pins the row to verified RAD51C
    hereditary breast/ovarian-cancer references so those off-topic PMIDs cannot
    silently reappear.
    """

    # RAD51C hereditary breast/ovarian-cancer references verified on PubMed
    # (esummary/efetch) and cross-checked against the literature (Consensus).
    _RAD51C_PMIDS = frozenset(
        {
            "32107557",  # Yang 2020, JNCI — RAD51C/RAD51D tubo-ovarian & breast cancer risks
            "32359370",  # Suszynska 2020, J Ovarian Res — RAD51C ovarian-cancer meta-analysis
        }
    )
    # Unrelated PMIDs wrongly cited by the RAD51C row before the fix. NOTE:
    # 25583358 (a CHEK2/thyroid paper) is ALSO wrongly cited by a Lynch (MMR)
    # row tracked separately in #283, so it is banned from RAD51C ONLY, not
    # panel-wide.
    _BANNED_FROM_RAD51C = frozenset({"20301399", "25583358"})

    def test_rad51c_cites_verified_references(self, panel: CancerPanel) -> None:
        gene = panel.get_gene("RAD51C")
        assert gene is not None
        assert set(gene.pmids) == self._RAD51C_PMIDS

    def test_rad51c_drops_unrelated_pmids(self, panel: CancerPanel) -> None:
        gene = panel.get_gene("RAD51C")
        assert gene is not None
        leaked = self._BANNED_FROM_RAD51C & set(gene.pmids)
        assert not leaked, f"RAD51C still cites unrelated PMID(s) {sorted(leaked)}"

    def test_tuberous_sclerosis_genereviews_absent_from_panel(self, panel: CancerPanel) -> None:
        # 20301399 is the Tuberous Sclerosis Complex GeneReviews chapter; no TSC
        # gene exists in the cancer panel, so this off-topic PMID (unique to the
        # old RAD51C row) must appear nowhere.
        for gene in panel.genes:
            assert "20301399" not in gene.pmids, (
                f"{gene.gene_symbol} cites the unrelated Tuberous-Sclerosis PMID 20301399"
            )


class TestHereditaryCancerSyndromeCitationProvenance:
    """Guard the TP53 / ATM / APC / VHL evidence links (issue #441).

    Each row previously cited a globally off-topic paper (verified via NCBI
    esummary): TP53 -> 31170274 (Botswana oncology disparities); ATM -> 31785142
    (non-Hodgkin lymphoma of the knee); APC -> 30844766 (platelet-lysate hydrogel
    sutures); VHL -> 31280893 (a meta-analysis commentary). The ATM row *also*
    carried 20301670, which resolves to the **MECP2 Disorders** GeneReviews
    chapter (Rett syndrome) — wrong gene — so both ATM PMIDs were replaced. Pin
    each row to verified gene/syndrome references (NCBI + Consensus).
    """

    # Verified per-gene citation sets: the correct GeneReviews/cancer-predisposition
    # framework + a syndrome-specific guideline/penetrance source.
    _VERIFIED: dict[str, frozenset[str]] = {
        # Li-Fraumeni GeneReviews + Frébourg 2020 TP53 guidelines
        "TP53": frozenset({"20301488", "32457520"}),
        # ATM-Related Cancer Predisposition GeneReviews + Marabelli 2016 BC penetrance
        "ATM": frozenset({"42258614", "27112364"}),
        # APC Polyposis GeneReviews + Zaffaroni 2024 FAP guidelines
        "APC": frozenset({"20301519", "38722804"}),
        # VHL GeneReviews + Binderup 2022 VHL diagnosis/surveillance
        "VHL": frozenset({"20301636", "35709961"}),
    }
    # Off-topic PMIDs removed; each was exclusive to its row and is off-topic for
    # the whole cancer panel (oncology-disparities / NHL-knee / hydrogel-sutures /
    # meta-commentary / MECP2-neuro GeneReviews) → asserted absent panel-wide.
    _BANNED = frozenset({"31170274", "31785142", "30844766", "31280893", "20301670"})

    def test_genes_cite_verified_references(self, panel: CancerPanel) -> None:
        for symbol, expected in self._VERIFIED.items():
            gene = panel.get_gene(symbol)
            assert gene is not None, symbol
            assert set(gene.pmids) == expected, (symbol, gene.pmids)

    def test_unrelated_pmids_absent_from_panel(self, panel: CancerPanel) -> None:
        for gene in panel.genes:
            leaked = self._BANNED & set(gene.pmids)
            assert not leaked, f"{gene.gene_symbol} cites unrelated PMID(s) {sorted(leaked)}"
