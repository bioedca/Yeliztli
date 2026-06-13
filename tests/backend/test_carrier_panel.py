"""Tests for the curated carrier status gene panel (P3-35).

Covers:
  - Panel JSON loading and validation
  - All 7 genes present (CFTR, HBB, GBA, HEXA, BRCA1, BRCA2, SMN1)
  - Gene lookup by symbol
  - Condition queries
  - BRCA1/2 dual-role cross-links to cancer module
  - Expected ClinVar rsids are populated
  - Evidence levels are valid (1-4)
  - Inheritance patterns (AR for most, AD for BRCA1/2)
  - Panel structure integrity
  - Autosomal recessive gene filtering
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.analysis.carrier_status import (
    CarrierGene,
    CarrierPanel,
    load_carrier_panel,
)

# ── Fixtures ──────────────────────────────────────────────────────────────

PANEL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "carrier_panel.json"
)


@pytest.fixture()
def panel() -> CarrierPanel:
    """Load the curated carrier panel from the real JSON file."""
    return load_carrier_panel(PANEL_PATH)


# ── Panel loading tests ──────────────────────────────────────────────────


class TestPanelLoading:
    """Test panel JSON loading and basic structure."""

    def test_panel_loads_successfully(self, panel: CarrierPanel) -> None:
        assert panel is not None
        assert panel.module == "carrier"
        assert panel.version == "1.0.0"

    def test_panel_has_description(self, panel: CarrierPanel) -> None:
        assert panel.description
        assert "carrier" in panel.description.lower()

    def test_panel_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_carrier_panel(tmp_path / "nonexistent.json")

    def test_panel_malformed_json(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_carrier_panel(bad_file)

    def test_panel_missing_required_field(self, tmp_path: Path) -> None:
        """Missing required field raises ValueError with gene context."""
        bad_panel = tmp_path / "bad_panel.json"
        bad_panel.write_text(
            json.dumps(
                {
                    "module": "carrier",
                    "version": "1.0.0",
                    "description": "test",
                    "genes": [{"gene_symbol": "TEST"}],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Missing required field.*TEST"):
            load_carrier_panel(bad_panel)

    @pytest.mark.parametrize("missing_key", ["module", "version", "description"])
    def test_panel_missing_top_level_field(self, tmp_path: Path, missing_key: str) -> None:
        """Missing top-level panel field raises ValueError."""
        data = {
            "module": "carrier",
            "version": "1.0.0",
            "description": "test",
            "genes": [],
        }
        del data[missing_key]
        bad_panel = tmp_path / "bad_panel.json"
        bad_panel.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="Missing required panel field"):
            load_carrier_panel(bad_panel)


# ── Gene count and completeness ──────────────────────────────────────────


class TestGeneCompleteness:
    """Verify all PRD-specified genes are present."""

    EXPECTED_GENES = [
        "CFTR",
        "HBB",
        "GBA",
        "HEXA",
        "BRCA1",
        "BRCA2",
        "SMN1",
    ]

    def test_gene_count(self, panel: CarrierPanel) -> None:
        assert len(panel.genes) == 7

    def test_all_expected_genes_present(self, panel: CarrierPanel) -> None:
        panel_symbols = set(panel.all_gene_symbols())
        for gene in self.EXPECTED_GENES:
            assert gene in panel_symbols, f"Missing gene: {gene}"

    def test_no_unexpected_genes(self, panel: CarrierPanel) -> None:
        panel_symbols = set(panel.all_gene_symbols())
        expected = set(self.EXPECTED_GENES)
        unexpected = panel_symbols - expected
        assert not unexpected, f"Unexpected genes: {unexpected}"


# ── Gene lookup ──────────────────────────────────────────────────────────


class TestGeneLookup:
    """Test gene lookup methods."""

    def test_get_gene_by_symbol(self, panel: CarrierPanel) -> None:
        cftr = panel.get_gene("CFTR")
        assert cftr is not None
        assert cftr.gene_symbol == "CFTR"

    def test_get_gene_case_insensitive(self, panel: CarrierPanel) -> None:
        cftr = panel.get_gene("cftr")
        assert cftr is not None
        assert cftr.gene_symbol == "CFTR"

    def test_get_gene_not_found(self, panel: CarrierPanel) -> None:
        result = panel.get_gene("NONEXISTENT")
        assert result is None

    def test_genes_by_condition_cystic_fibrosis(self, panel: CarrierPanel) -> None:
        cf_genes = panel.genes_by_condition("Cystic Fibrosis")
        symbols = {g.gene_symbol for g in cf_genes}
        assert symbols == {"CFTR"}

    def test_genes_by_condition_sickle_cell(self, panel: CarrierPanel) -> None:
        sc_genes = panel.genes_by_condition("Sickle Cell")
        symbols = {g.gene_symbol for g in sc_genes}
        assert "HBB" in symbols

    def test_genes_by_condition_breast_ovarian(self, panel: CarrierPanel) -> None:
        brca_genes = panel.genes_by_condition("Breast")
        symbols = {g.gene_symbol for g in brca_genes}
        assert "BRCA1" in symbols
        assert "BRCA2" in symbols


# ── Cross-links and dual-role genes ──────────────────────────────────────


class TestCrossLinks:
    """Test BRCA1/2 dual-role cross-links to cancer module."""

    def test_brca1_has_cancer_cross_link(self, panel: CarrierPanel) -> None:
        brca1 = panel.get_gene("BRCA1")
        assert brca1 is not None
        assert "cancer" in brca1.cross_links
        assert brca1.is_dual_role

    def test_brca2_has_cancer_cross_link(self, panel: CarrierPanel) -> None:
        brca2 = panel.get_gene("BRCA2")
        assert brca2 is not None
        assert "cancer" in brca2.cross_links
        assert brca2.is_dual_role

    def test_dual_role_genes_are_brca_only(self, panel: CarrierPanel) -> None:
        dual = panel.dual_role_genes()
        symbols = {g.gene_symbol for g in dual}
        assert symbols == {"BRCA1", "BRCA2"}

    def test_non_brca_genes_have_no_cross_links(self, panel: CarrierPanel) -> None:
        cftr = panel.get_gene("CFTR")
        assert cftr is not None
        assert not cftr.is_dual_role
        assert cftr.cross_links == []

    def test_all_ar_genes_have_no_cross_links(self, panel: CarrierPanel) -> None:
        for gene in panel.autosomal_recessive_genes():
            assert gene.cross_links == [], f"{gene.gene_symbol} (AR) should have no cross-links"


# ── Autosomal recessive filtering ────────────────────────────────────────


class TestAutosomalRecessiveFiltering:
    """Test AR gene subset filtering."""

    def test_ar_gene_count(self, panel: CarrierPanel) -> None:
        ar_genes = panel.autosomal_recessive_genes()
        assert len(ar_genes) == 5  # CFTR, HBB, GBA, HEXA, SMN1

    def test_ar_genes_exclude_brca(self, panel: CarrierPanel) -> None:
        ar_symbols = {g.gene_symbol for g in panel.autosomal_recessive_genes()}
        assert "BRCA1" not in ar_symbols
        assert "BRCA2" not in ar_symbols

    def test_ar_genes_include_expected(self, panel: CarrierPanel) -> None:
        ar_symbols = {g.gene_symbol for g in panel.autosomal_recessive_genes()}
        assert ar_symbols == {"CFTR", "HBB", "GBA", "HEXA", "SMN1"}


# ── Expected ClinVar rsids ───────────────────────────────────────────────


class TestExpectedClinVarRsids:
    """Test expected ClinVar P/LP rsid entries."""

    def test_all_genes_have_expected_rsids(self, panel: CarrierPanel) -> None:
        for gene in panel.genes:
            assert len(gene.expected_clinvar_rsids) > 0, (
                f"{gene.gene_symbol} has no expected ClinVar rsids"
            )

    def test_rsids_are_valid_format(self, panel: CarrierPanel) -> None:
        for gene in panel.genes:
            for rsid in gene.expected_clinvar_rsids:
                assert rsid.startswith("rs"), f"Invalid rsid format: {rsid} in {gene.gene_symbol}"
                assert rsid[2:].isdigit(), (
                    f"Invalid rsid numeric part: {rsid} in {gene.gene_symbol}"
                )

    def test_no_duplicate_rsids_within_gene(self, panel: CarrierPanel) -> None:
        for gene in panel.genes:
            rsids = gene.expected_clinvar_rsids
            assert len(rsids) == len(set(rsids)), f"Duplicate rsids in {gene.gene_symbol}"

    def test_total_expected_rsids(self, panel: CarrierPanel) -> None:
        """Panel should have a substantial number of expected rsids."""
        all_rsids = panel.all_expected_rsids()
        assert len(all_rsids) >= 50  # At least 50 across 7 genes

    def test_cftr_f508del_present(self, panel: CarrierPanel) -> None:
        """CFTR F508del (rs113993960) must be in expected rsids."""
        cftr = panel.get_gene("CFTR")
        assert cftr is not None
        assert "rs113993960" in cftr.expected_clinvar_rsids

    def test_hbb_sickle_cell_variant_present(self, panel: CarrierPanel) -> None:
        """HBB HbS (rs334) must be in expected rsids."""
        hbb = panel.get_gene("HBB")
        assert hbb is not None
        assert "rs334" in hbb.expected_clinvar_rsids

    def test_brca1_rsids_match_cancer_panel(self, panel: CarrierPanel) -> None:
        """BRCA1 expected rsids should include key variants from cancer panel."""
        brca1 = panel.get_gene("BRCA1")
        assert brca1 is not None
        assert "rs80357906" in brca1.expected_clinvar_rsids


# ── Evidence levels ──────────────────────────────────────────────────────


class TestEvidenceLevels:
    """Test evidence level assignments."""

    def test_evidence_levels_valid_range(self, panel: CarrierPanel) -> None:
        for gene in panel.genes:
            assert 1 <= gene.evidence_level <= 4, (
                f"{gene.gene_symbol} has invalid evidence level: {gene.evidence_level}"
            )

    def test_all_genes_are_high_evidence(self, panel: CarrierPanel) -> None:
        """All carrier panel genes should be 4-star (well-established conditions)."""
        for gene in panel.genes:
            assert gene.evidence_level == 4, (
                f"{gene.gene_symbol} should be 4-star evidence, got {gene.evidence_level}"
            )


# ── Inheritance patterns ─────────────────────────────────────────────────


class TestInheritance:
    """Test inheritance pattern assignments."""

    def test_inheritance_values_valid(self, panel: CarrierPanel) -> None:
        valid_patterns = {"AD", "AR"}
        for gene in panel.genes:
            assert gene.inheritance in valid_patterns, (
                f"{gene.gene_symbol} has invalid inheritance: {gene.inheritance}"
            )

    def test_cftr_is_autosomal_recessive(self, panel: CarrierPanel) -> None:
        cftr = panel.get_gene("CFTR")
        assert cftr is not None
        assert cftr.inheritance == "AR"

    def test_hbb_is_autosomal_recessive(self, panel: CarrierPanel) -> None:
        hbb = panel.get_gene("HBB")
        assert hbb is not None
        assert hbb.inheritance == "AR"

    def test_gba_is_autosomal_recessive(self, panel: CarrierPanel) -> None:
        gba = panel.get_gene("GBA")
        assert gba is not None
        assert gba.inheritance == "AR"

    def test_hexa_is_autosomal_recessive(self, panel: CarrierPanel) -> None:
        hexa = panel.get_gene("HEXA")
        assert hexa is not None
        assert hexa.inheritance == "AR"

    def test_smn1_is_autosomal_recessive(self, panel: CarrierPanel) -> None:
        smn1 = panel.get_gene("SMN1")
        assert smn1 is not None
        assert smn1.inheritance == "AR"

    def test_brca1_is_autosomal_dominant(self, panel: CarrierPanel) -> None:
        brca1 = panel.get_gene("BRCA1")
        assert brca1 is not None
        assert brca1.inheritance == "AD"

    def test_brca2_is_autosomal_dominant(self, panel: CarrierPanel) -> None:
        brca2 = panel.get_gene("BRCA2")
        assert brca2 is not None
        assert brca2.inheritance == "AD"


# ── PubMed citations ─────────────────────────────────────────────────────


class TestPMIDs:
    """Test PubMed citation data."""

    def test_all_genes_have_pmids(self, panel: CarrierPanel) -> None:
        for gene in panel.genes:
            assert len(gene.pmids) > 0, f"{gene.gene_symbol} has no PubMed citations"

    def test_pmids_are_numeric(self, panel: CarrierPanel) -> None:
        for gene in panel.genes:
            for pmid in gene.pmids:
                assert pmid.isdigit(), f"Invalid PMID: {pmid} in {gene.gene_symbol}"

    def test_gba_cites_verified_gaucher_sources(self, panel: CarrierPanel) -> None:
        """#173 — the GBA carrier entry must cite Gaucher/GBA-specific sources.

        Locks the verified citation set so an unrelated PMID can't be
        transposed back in (PMID 29241104 was an unrelated Drug & Alcohol
        Dependence paper on self-compassion / substance-use risk):
          - 20301446 = GeneReviews "Gaucher Disease"
          - 20672374 = Scott et al. 2010, Hum Mutat — AJ carrier screening,
            Gaucher carrier frequency ~1:15 (backs the panel note)
          - 10464619 = Grabowski 1997, Genet Test — N370S/L444P gene frequencies
        """
        gba = panel.get_gene("GBA")
        assert gba is not None
        assert gba.pmids == ["20301446", "20672374", "10464619"]

    def test_no_unrelated_self_compassion_pmid(self, panel: CarrierPanel) -> None:
        """The transposed unrelated PMID must not reappear in any panel entry."""
        for gene in panel.genes:
            assert "29241104" not in gene.pmids, (
                f"{gene.gene_symbol} cites unrelated PMID 29241104"
            )

    def test_cftr_cites_verified_cf_sources(self, panel: CarrierPanel) -> None:
        """#440 — the CFTR carrier entry must cite CFTR/CF-screening sources.

        Locks the verified set so the unrelated febrile-infant PMID can't be
        transposed back in (PMID 31283134 was "Points & Pearls: Evaluation and
        management of the febrile young infant in the emergency department",
        Pediatr Emerg Med Pract 2019 — no CFTR/cystic-fibrosis relevance):
          - 20301428 = GeneReviews "Cystic Fibrosis"
          - 12007216 = Bobadilla 2002, Hum Mutat — worldwide CFTR mutation analysis
            & screening
          - 37310422 = ACMG 2023, Genet Med — updated CFTR carrier-screening
            position statement
        """
        cftr = panel.get_gene("CFTR")
        assert cftr is not None
        assert cftr.pmids == ["20301428", "12007216", "37310422"]

    def test_no_unrelated_febrile_infant_pmid(self, panel: CarrierPanel) -> None:
        """The transposed febrile-infant ED PMID must not reappear in any entry."""
        for gene in panel.genes:
            assert "31283134" not in gene.pmids, (
                f"{gene.gene_symbol} cites unrelated PMID 31283134"
            )


# ── Gene metadata ────────────────────────────────────────────────────────


class TestGeneMetadata:
    """Test gene metadata completeness."""

    def test_all_genes_have_conditions(self, panel: CarrierPanel) -> None:
        for gene in panel.genes:
            assert len(gene.conditions) > 0, f"{gene.gene_symbol} has no conditions"

    def test_all_genes_have_chromosome(self, panel: CarrierPanel) -> None:
        valid_chroms = {str(i) for i in range(1, 23)} | {"X", "Y"}
        for gene in panel.genes:
            assert gene.chromosome in valid_chroms, (
                f"{gene.gene_symbol} has invalid chromosome: {gene.chromosome}"
            )

    def test_all_genes_have_name(self, panel: CarrierPanel) -> None:
        for gene in panel.genes:
            assert gene.name, f"{gene.gene_symbol} has no name"

    def test_all_genes_have_notes(self, panel: CarrierPanel) -> None:
        for gene in panel.genes:
            assert gene.notes, f"{gene.gene_symbol} has no notes"

    def test_smn1_notes_mention_coverage(self, panel: CarrierPanel) -> None:
        """SMN1 notes should mention coverage limitations for SNP arrays."""
        smn1 = panel.get_gene("SMN1")
        assert smn1 is not None
        assert "insufficient" in smn1.notes.lower() or "coverage" in smn1.notes.lower()


# ── Dataclass properties ─────────────────────────────────────────────────


class TestDataclassProperties:
    """Test CarrierGene dataclass properties."""

    def test_is_dual_role_true(self) -> None:
        gene = CarrierGene(
            gene_symbol="TEST",
            name="Test Gene",
            chromosome="1",
            conditions=["Test Condition"],
            inheritance="AD",
            evidence_level=4,
            cross_links=["cancer"],
            expected_clinvar_rsids=["rs123"],
            pmids=["12345"],
            notes="Test note",
        )
        assert gene.is_dual_role is True

    def test_is_dual_role_false(self) -> None:
        gene = CarrierGene(
            gene_symbol="TEST",
            name="Test Gene",
            chromosome="1",
            conditions=["Test Condition"],
            inheritance="AR",
            evidence_level=4,
            cross_links=[],
            expected_clinvar_rsids=["rs123"],
            pmids=["12345"],
            notes="Test note",
        )
        assert gene.is_dual_role is False
