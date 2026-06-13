"""Tests for the curated Gene Fitness SNP panel (P3-45).

Covers:
  - Panel JSON loading and structural validation
  - All 8 curated SNPs present with correct genes
  - 4 pathway cards (Endurance, Power, Recovery & Injury, Training Response)
  - ACTN3 R577X three-state calling metadata (RR/RX/XX)
  - ACE I/D proxy with coverage note
  - Genotype effects categories are valid (Elevated/Moderate/Standard)
  - Evidence levels within expected range
  - Scoring rules match project conventions
  - GWAS EFO fitness terms included
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PANEL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "fitness_panel.json"
)

VALID_CATEGORIES = {"Elevated", "Moderate", "Standard"}

EXPECTED_RSIDS = {
    "rs1815739",  # ACTN3 R577X
    "rs4341",  # ACE I/D proxy
    "rs8192678",  # PPARGC1A Gly482Ser
    "rs17602729",  # AMPD1 Gln12Ter
    "rs12722",  # COL5A1
    "rs1800012",  # COL1A1
    "rs1049434",  # MCT1
    "rs9939609",  # FTO
}

EXPECTED_PATHWAYS = {"endurance", "power", "recovery_injury", "training_response"}

EXPECTED_GENES = {"ACTN3", "ACE", "PPARGC1A", "AMPD1", "COL5A1", "COL1A1", "MCT1", "FTO"}


@pytest.fixture()
def panel_data() -> dict:
    """Load the raw panel JSON."""
    with open(PANEL_PATH, encoding="utf-8") as f:
        return json.load(f)


# ── Panel structure tests ────────────────────────────────────────────────


class TestPanelStructure:
    def test_panel_file_exists(self) -> None:
        assert PANEL_PATH.exists(), f"Panel file not found: {PANEL_PATH}"

    def test_panel_is_valid_json(self, panel_data: dict) -> None:
        assert isinstance(panel_data, dict)

    def test_panel_module_name(self, panel_data: dict) -> None:
        assert panel_data["module"] == "fitness"

    def test_panel_version(self, panel_data: dict) -> None:
        assert panel_data["version"] == "1.0.0"

    def test_panel_has_description(self, panel_data: dict) -> None:
        assert "description" in panel_data
        assert len(panel_data["description"]) > 0

    def test_panel_has_four_pathways(self, panel_data: dict) -> None:
        assert len(panel_data["pathways"]) == 4

    def test_pathway_ids(self, panel_data: dict) -> None:
        pathway_ids = {p["id"] for p in panel_data["pathways"]}
        assert pathway_ids == EXPECTED_PATHWAYS

    def test_pathway_names(self, panel_data: dict) -> None:
        pathway_names = {p["name"] for p in panel_data["pathways"]}
        assert "Endurance" in pathway_names
        assert "Power" in pathway_names
        assert "Recovery & Injury" in pathway_names
        assert "Training Response" in pathway_names


# ── SNP coverage tests ──────────────────────────────────────────────────


class TestSNPCoverage:
    def test_all_expected_rsids_present(self, panel_data: dict) -> None:
        """All 8 curated SNPs from the PRD must be present."""
        all_rsids = set()
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                all_rsids.add(snp["rsid"])
        assert all_rsids == EXPECTED_RSIDS

    def test_all_expected_genes_present(self, panel_data: dict) -> None:
        all_genes = set()
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                all_genes.add(snp["gene"])
        assert all_genes == EXPECTED_GENES

    def test_total_snp_count(self, panel_data: dict) -> None:
        """8 curated SNPs total across all pathways."""
        count = sum(len(p["snps"]) for p in panel_data["pathways"])
        assert count == 8


# ── SNP field validation tests ──────────────────────────────────────────


class TestSNPFields:
    def test_snps_have_required_fields(self, panel_data: dict) -> None:
        required_fields = {
            "rsid",
            "gene",
            "variant_name",
            "risk_allele",
            "ref_allele",
            "genotype_effects",
            "evidence_level",
            "pmids",
            "recommendation_text",
        }
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                for field in required_fields:
                    assert field in snp, f"{snp['rsid']} missing field: {field}"

    def test_rsids_start_with_rs(self, panel_data: dict) -> None:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                assert snp["rsid"].startswith("rs"), f"Invalid rsid: {snp['rsid']}"

    def test_evidence_levels_valid(self, panel_data: dict) -> None:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                assert snp["evidence_level"] in (1, 2, 3, 4), (
                    f"{snp['rsid']} has invalid evidence_level: {snp['evidence_level']}"
                )

    def test_pmids_are_nonempty_lists(self, panel_data: dict) -> None:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                assert isinstance(snp["pmids"], list)
                assert len(snp["pmids"]) > 0, f"{snp['rsid']} has no PMIDs"
                for pmid in snp["pmids"]:
                    assert pmid.isdigit(), f"{snp['rsid']} has non-numeric PMID: {pmid}"


# ── Genotype effects validation ─────────────────────────────────────────


class TestGenotypeEffects:
    def test_genotype_effects_have_valid_categories(self, panel_data: dict) -> None:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                for gt, effect in snp["genotype_effects"].items():
                    assert "category" in effect, f"{snp['rsid']}:{gt} missing category"
                    assert effect["category"] in VALID_CATEGORIES, (
                        f"{snp['rsid']}:{gt} invalid category: {effect['category']}"
                    )

    def test_genotype_effects_have_effect_summary(self, panel_data: dict) -> None:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                for gt, effect in snp["genotype_effects"].items():
                    assert "effect_summary" in effect, f"{snp['rsid']}:{gt} missing effect_summary"
                    assert len(effect["effect_summary"]) > 0

    def test_each_snp_has_standard_category(self, panel_data: dict) -> None:
        """Every SNP must have at least one Standard genotype."""
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                categories = {e["category"] for e in snp["genotype_effects"].values()}
                assert "Standard" in categories, f"{snp['rsid']} has no Standard genotype category"

    def test_genotypes_are_two_char(self, panel_data: dict) -> None:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                for gt in snp["genotype_effects"]:
                    assert len(gt) == 2, f"{snp['rsid']} has invalid genotype length: {gt}"
                    assert gt.isalpha(), f"{snp['rsid']} has non-alpha genotype: {gt}"


# ── ACTN3 R577X three-state calling tests ────────────────────────────────


class TestACTN3ThreeState:
    """T3-48 precursor: validate ACTN3 three-state calling metadata in panel."""

    def _get_actn3(self, panel_data: dict) -> dict:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == "rs1815739":
                    return snp
        pytest.fail("ACTN3 rs1815739 not found in panel")

    def test_actn3_has_three_state_calling(self, panel_data: dict) -> None:
        actn3 = self._get_actn3(panel_data)
        assert "three_state_calling" in actn3

    def test_actn3_three_state_mapping(self, panel_data: dict) -> None:
        actn3 = self._get_actn3(panel_data)
        mapping = actn3["three_state_calling"]
        assert mapping["CC"] == "RR"
        assert mapping["CT"] == "RX"
        assert mapping["TC"] == "RX"
        assert mapping["TT"] == "XX"

    def test_actn3_cc_standard_power(self, panel_data: dict) -> None:
        """RR genotype (CC) → Standard category (power-oriented)."""
        actn3 = self._get_actn3(panel_data)
        effect = actn3["genotype_effects"]["CC"]
        assert effect["category"] == "Standard"
        summary = effect["effect_summary"].lower()
        assert "power" in summary or "fast-twitch" in summary

    def test_actn3_ct_moderate_mixed(self, panel_data: dict) -> None:
        """RX genotype (CT) → Moderate category (mixed profile)."""
        actn3 = self._get_actn3(panel_data)
        effect = actn3["genotype_effects"]["CT"]
        assert effect["category"] == "Moderate"
        assert "mixed" in effect["effect_summary"].lower()

    def test_actn3_tc_moderate_mixed(self, panel_data: dict) -> None:
        """RX genotype (TC) → Moderate category (mixed profile), same as CT."""
        actn3 = self._get_actn3(panel_data)
        effect = actn3["genotype_effects"]["TC"]
        assert effect["category"] == "Moderate"
        assert "mixed" in effect["effect_summary"].lower()

    def test_actn3_tt_standard_context_only(self, panel_data: dict) -> None:
        """XX genotype (TT) is context-only (Standard), not an Elevated endurance
        call — the human XX endurance advantage is not established (gh #182)."""
        actn3 = self._get_actn3(panel_data)
        effect = actn3["genotype_effects"]["TT"]
        assert effect["category"] == "Standard"
        assert "not established" in effect["effect_summary"].lower()

    def test_actn3_evidence_level(self, panel_data: dict) -> None:
        actn3 = self._get_actn3(panel_data)
        assert actn3["evidence_level"] == 2  # Well-replicated GWAS

    def test_actn3_in_special_calling(self, panel_data: dict) -> None:
        assert "special_calling" in panel_data
        assert "ACTN3_R577X" in panel_data["special_calling"]
        sc = panel_data["special_calling"]["ACTN3_R577X"]
        assert sc["rsid"] == "rs1815739"
        assert "RR" in sc["states"]
        assert "RX" in sc["states"]
        assert "XX" in sc["states"]
        # RX state documents both heterozygous genotype orientations
        assert set(sc["states"]["RX"]["genotypes"]) == {"CT", "TC"}


# ── ACE I/D proxy tests ─────────────────────────────────────────────────


class TestACEProxy:
    """T3-49 precursor: validate ACE I/D proxy metadata in panel."""

    def _get_ace(self, panel_data: dict) -> dict:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == "rs4341":
                    return snp
        pytest.fail("ACE rs4341 not found in panel")

    def test_ace_has_coverage_note(self, panel_data: dict) -> None:
        ace = self._get_ace(panel_data)
        assert "coverage_note" in ace
        assert "proxy" in ace["coverage_note"].lower()
        assert "linkage disequilibrium" in ace["coverage_note"].lower()

    def test_ace_gg_elevated_power(self, panel_data: dict) -> None:
        """DD proxy (GG) → Elevated category (power)."""
        ace = self._get_ace(panel_data)
        effect = ace["genotype_effects"]["GG"]
        assert effect["category"] == "Elevated"
        summary = effect["effect_summary"].lower()
        assert "power" in summary or "sprint" in summary

    def test_ace_aa_standard_endurance(self, panel_data: dict) -> None:
        """II proxy (AA) → Standard category (endurance)."""
        ace = self._get_ace(panel_data)
        effect = ace["genotype_effects"]["AA"]
        assert effect["category"] == "Standard"
        assert "endurance" in effect["effect_summary"].lower()

    def test_ace_in_special_calling(self, panel_data: dict) -> None:
        assert "ACE_ID_proxy" in panel_data["special_calling"]
        sc = panel_data["special_calling"]["ACE_ID_proxy"]
        assert sc["rsid"] == "rs4341"
        assert "proxy_accuracy_note" in sc

    def test_ace_evidence_level(self, panel_data: dict) -> None:
        ace = self._get_ace(panel_data)
        assert ace["evidence_level"] == 2  # Moderate, heterogeneous meta-analytic evidence

    def test_ace_pmids_support_proxy_and_sports_evidence(self, panel_data: dict) -> None:
        ace = self._get_ace(panel_data)
        pmids = set(ace["pmids"])
        assert {"12733698", "18622756"}.issubset(pmids)  # rs4341 proxy/genotyping
        assert {"39595706", "38760851"}.issubset(pmids)  # current sports meta-analyses
        assert not {"10694420", "15563880", "18043716"} & pmids

    def test_ace_text_carries_heterogeneous_evidence_caveat(self, panel_data: dict) -> None:
        ace = self._get_ace(panel_data)
        effect_text = " ".join(
            effect["effect_summary"].lower() for effect in ace["genotype_effects"].values()
        )
        recommendation = ace["recommendation_text"].lower()
        assert "heterogeneous" in effect_text
        assert "not deterministic" in effect_text
        assert "do not make deterministic training recommendations" in recommendation


# ── Scoring rules tests ─────────────────────────────────────────────────


class TestScoringRules:
    def test_scoring_rules_present(self, panel_data: dict) -> None:
        assert "scoring_rules" in panel_data

    def test_star_1_cap(self, panel_data: dict) -> None:
        """★☆ evidence hard-caps at Moderate (project convention)."""
        assert panel_data["scoring_rules"]["star_1_cap"] == "Moderate"

    def test_elevated_requires_min_stars(self, panel_data: dict) -> None:
        assert panel_data["scoring_rules"]["elevated_requires_min_stars"] == 2

    def test_pathway_level_determination(self, panel_data: dict) -> None:
        rules = panel_data["scoring_rules"]
        assert rules["pathway_level_determination"] == "highest_category_across_snps"

    def test_valid_categories_listed(self, panel_data: dict) -> None:
        cats = panel_data["scoring_rules"]["categories"]
        assert set(cats) == VALID_CATEGORIES


# ── GWAS EFO terms tests ────────────────────────────────────────────────


class TestGWASEFOTerms:
    def test_gwas_efo_terms_present(self, panel_data: dict) -> None:
        assert "gwas_efo_terms" in panel_data
        terms = panel_data["gwas_efo_terms"]
        assert isinstance(terms, list)
        assert len(terms) > 0

    def test_key_fitness_efo_terms_included(self, panel_data: dict) -> None:
        terms = set(panel_data["gwas_efo_terms"])
        assert "muscle" in terms
        assert "exercise" in terms
        assert "endurance" in terms
        assert "power" in terms
        assert "vo2max" in terms
        assert "grip strength" in terms
        assert "bone mineral density" in terms
        assert "lactate" in terms

    def test_gwas_efo_terms_match_gwas_loader(self, panel_data: dict) -> None:
        """Panel EFO terms should match the _FITNESS_TERMS in gwas.py."""
        from backend.annotation.gwas import _FITNESS_TERMS

        panel_terms = frozenset(panel_data["gwas_efo_terms"])
        assert panel_terms == _FITNESS_TERMS


# ── Pathway-specific SNP allocation tests ────────────────────────────────


class TestPathwayAllocation:
    def _get_pathway(self, panel_data: dict, pathway_id: str) -> dict:
        for p in panel_data["pathways"]:
            if p["id"] == pathway_id:
                return p
        pytest.fail(f"Pathway {pathway_id} not found")

    def test_endurance_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "endurance")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs1815739" in rsids  # ACTN3
        assert "rs8192678" in rsids  # PPARGC1A
        assert "rs17602729" in rsids  # AMPD1

    def test_power_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "power")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs4341" in rsids  # ACE
        assert "rs1049434" in rsids  # MCT1

    def test_recovery_injury_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "recovery_injury")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs12722" in rsids  # COL5A1
        assert "rs1800012" in rsids  # COL1A1

    def test_training_response_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "training_response")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs9939609" in rsids  # FTO
