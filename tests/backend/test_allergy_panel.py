"""Tests for the curated Gene Allergy & Immune Sensitivities SNP panel (P3-59).

Covers:
  - Panel JSON loading and structural validation
  - All 11 curated SNPs present with correct genes (7 direct + 4 HLA drug proxies)
  - 4 pathway cards (Atopic Conditions, Drug Hypersensitivity,
    Food Sensitivity, Histamine Metabolism)
  - HLA proxy calling metadata (r², ancestry, confirmatory_test_required)
  - Celiac DQ2/DQ8 combined assessment and high NPV framing
  - Drug hypersensitivity HLA proxies with cross-module PGx links
  - Histamine metabolism SNPs (AOC1, HNMT) capped at Moderate (★☆)
  - Genotype effects categories are valid (Elevated/Moderate/Standard)
  - Evidence levels within expected range
  - Scoring rules match project conventions
  - GWAS EFO allergy/immune terms included
  - Cross-module links (PGx, Skin, Nutrigenomics)
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
    / "allergy_panel.json"
)

VALID_CATEGORIES = {"Elevated", "Moderate", "Standard"}

# 11 SNPs: 3 atopic + 4 drug HLA + 2 celiac HLA + 2 histamine
EXPECTED_RSIDS = {
    "rs20541",  # IL13 R130Q
    "rs8076131",  # ORMDL3 asthma
    "rs324011",  # STAT6 atopic
    "rs2395029",  # HLA-B*57:01 abacavir
    "rs144012689",  # HLA-B*15:02 carbamazepine SJS/TEN
    "rs1061235",  # HLA-A*31:01 carbamazepine DRESS
    "rs9263726",  # HLA-B*58:01 allopurinol
    "rs2187668",  # HLA-DQ2 celiac
    "rs7775228",  # HLA-DQ8 celiac
    "rs10156191",  # AOC1 DAO histamine
    "rs11558538",  # HNMT histamine
}

EXPECTED_PATHWAYS = {
    "atopic_conditions",
    "drug_hypersensitivity",
    "food_sensitivity",
    "histamine_metabolism",
}

EXPECTED_GENES = {
    "IL13",
    "ORMDL3",
    "STAT6",
    "HLA-B",
    "HLA-A",
    "HLA-DQA1",
    "HLA-DQB1",
    "AOC1",
    "HNMT",
}


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
        assert panel_data["module"] == "allergy"

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
        assert "Atopic Conditions" in pathway_names
        assert "Drug Hypersensitivity" in pathway_names
        assert "Food Sensitivity" in pathway_names
        assert "Histamine Metabolism" in pathway_names


# ── SNP coverage tests ──────────────────────────────────────────────────


class TestSNPCoverage:
    def test_all_expected_rsids_present(self, panel_data: dict) -> None:
        """All 11 curated SNPs from the PRD must be present."""
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
        """11 curated SNPs total across all pathways."""
        count = sum(len(p["snps"]) for p in panel_data["pathways"])
        assert count == 11


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


# ── HLA proxy calling tests ─────────────────────────────────────────────


class TestHLAProxyCalling:
    """Validate HLA proxy metadata for drug hypersensitivity and celiac SNPs."""

    HLA_PROXY_RSIDS = {
        "rs2395029",
        "rs144012689",
        "rs1061235",
        "rs9263726",
        "rs2187668",
        "rs7775228",
    }
    DRUG_PROXY_RSIDS = {"rs2395029", "rs144012689", "rs1061235", "rs9263726"}
    CELIAC_PROXY_RSIDS = {"rs2187668", "rs7775228"}

    def _get_hla_snps(self, panel_data: dict) -> list[dict]:
        snps = []
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] in self.HLA_PROXY_RSIDS:
                    snps.append(snp)
        return snps

    def test_all_six_hla_proxy_snps_present(self, panel_data: dict) -> None:
        found = {s["rsid"] for s in self._get_hla_snps(panel_data)}
        assert found == self.HLA_PROXY_RSIDS

    def test_hla_snps_have_proxy_metadata(self, panel_data: dict) -> None:
        for snp in self._get_hla_snps(panel_data):
            assert "hla_proxy" in snp, f"{snp['rsid']} missing hla_proxy metadata"
            proxy = snp["hla_proxy"]
            assert "hla_allele" in proxy
            assert "confirmatory_test_required" in proxy
            assert proxy["confirmatory_test_required"] is True

    def test_drug_proxies_clinical_grade(self, panel_data: dict) -> None:
        """Drug HLA proxy clinical-grade metadata is scoped to LD strength."""
        for snp in self._get_hla_snps(panel_data):
            if snp["rsid"] in self.DRUG_PROXY_RSIDS:
                if snp["rsid"] == "rs9263726":
                    assert snp["hla_proxy"]["clinical_grade"] is False
                    assert snp["hla_proxy"]["r_squared_afr"] < 0.85
                    assert "clinical_grade_context" in snp["hla_proxy"]
                else:
                    assert snp["hla_proxy"]["clinical_grade"] is True, (
                        f"{snp['rsid']} drug proxy should be clinical_grade=true"
                    )

    def test_drug_proxies_evidence_level_3_or_4(self, panel_data: dict) -> None:
        """Drug HLA proxies have strong clinical evidence (★★★ or ★★★★)."""
        for snp in self._get_hla_snps(panel_data):
            if snp["rsid"] in self.DRUG_PROXY_RSIDS:
                assert snp["evidence_level"] in (3, 4), (
                    f"{snp['rsid']} drug proxy should have evidence_level 3 or 4"
                )

    def test_drug_proxies_carrier_elevated(self, panel_data: dict) -> None:
        """Any carrier of a drug hypersensitivity HLA proxy → Elevated."""
        for snp in self._get_hla_snps(panel_data):
            if snp["rsid"] in self.DRUG_PROXY_RSIDS:
                non_standard = [
                    (gt, e)
                    for gt, e in snp["genotype_effects"].items()
                    if e["category"] != "Standard"
                ]
                for gt, effect in non_standard:
                    assert effect["category"] == "Elevated", (
                        f"{snp['rsid']}:{gt} drug proxy carrier should be Elevated"
                    )

    def test_celiac_proxies_evidence_level_3(self, panel_data: dict) -> None:
        """Celiac DQ2/DQ8 proxies at ★★★☆ per PRD."""
        for snp in self._get_hla_snps(panel_data):
            if snp["rsid"] in self.CELIAC_PROXY_RSIDS:
                assert snp["evidence_level"] == 3, (
                    f"{snp['rsid']} celiac proxy should have evidence_level 3"
                )

    def test_celiac_proxies_not_clinical_grade(self, panel_data: dict) -> None:
        """Celiac proxies are not clinical-grade (lower positive predictive value)."""
        for snp in self._get_hla_snps(panel_data):
            if snp["rsid"] in self.CELIAC_PROXY_RSIDS:
                assert snp["hla_proxy"]["clinical_grade"] is False

    def test_celiac_heterozygous_moderate(self, panel_data: dict) -> None:
        """Celiac proxies: heterozygous → Moderate."""
        for snp in self._get_hla_snps(panel_data):
            if snp["rsid"] in self.CELIAC_PROXY_RSIDS:
                for gt, effect in snp["genotype_effects"].items():
                    if len(set(gt)) > 1:  # Heterozygous
                        assert effect["category"] == "Moderate", (
                            f"{snp['rsid']}:{gt} celiac het should be Moderate"
                        )

    def test_celiac_homozygous_elevated(self, panel_data: dict) -> None:
        """Celiac proxies: homozygous risk → Elevated."""
        for snp in self._get_hla_snps(panel_data):
            if snp["rsid"] in self.CELIAC_PROXY_RSIDS:
                risk = snp["risk_allele"]
                hom_gt = risk + risk
                assert snp["genotype_effects"][hom_gt]["category"] == "Elevated", (
                    f"{snp['rsid']} celiac hom risk should be Elevated"
                )

    def test_special_calling_hla_section(self, panel_data: dict) -> None:
        assert "special_calling" in panel_data
        assert "HLA_proxy_calling" in panel_data["special_calling"]
        sc = panel_data["special_calling"]["HLA_proxy_calling"]
        assert set(sc["proxy_rsids"]) == self.HLA_PROXY_RSIDS
        assert sc["confirmatory_test_required"] is True

    def test_special_calling_drug_proxies(self, panel_data: dict) -> None:
        sc = panel_data["special_calling"]["HLA_proxy_calling"]
        assert "drug_hypersensitivity_proxies" in sc
        drug = sc["drug_hypersensitivity_proxies"]
        assert set(drug.keys()) == self.DRUG_PROXY_RSIDS

    def test_special_calling_celiac_proxies(self, panel_data: dict) -> None:
        sc = panel_data["special_calling"]["HLA_proxy_calling"]
        assert "celiac_proxies" in sc
        celiac = sc["celiac_proxies"]
        assert set(celiac.keys()) == self.CELIAC_PROXY_RSIDS


# ── Celiac DQ2/DQ8 combined assessment tests ────────────────────────────


class TestCeliacCombined:
    """Validate celiac DQ2/DQ8 combined risk assessment metadata."""

    def test_celiac_combined_section_exists(self, panel_data: dict) -> None:
        assert "celiac_DQ2_DQ8_combined" in panel_data["special_calling"]

    def test_celiac_combined_states(self, panel_data: dict) -> None:
        sc = panel_data["special_calling"]["celiac_DQ2_DQ8_combined"]
        states = sc["combined_states"]
        assert "neither" in states
        assert "dq2_only" in states
        assert "dq8_only" in states
        assert "both" in states

    def test_celiac_neither_high_npv(self, panel_data: dict) -> None:
        """Neither DQ2 nor DQ8 → emphasize high NPV."""
        sc = panel_data["special_calling"]["celiac_DQ2_DQ8_combined"]
        neither = sc["combined_states"]["neither"]
        desc_lower = neither["description"].lower()
        assert "npv" in desc_lower or "99%" in desc_lower

    def test_celiac_combined_rsids(self, panel_data: dict) -> None:
        sc = panel_data["special_calling"]["celiac_DQ2_DQ8_combined"]
        assert set(sc["rsids"]) == {"rs2187668", "rs7775228"}


# ── Abacavir/HLA-B*57:01 bi-directional cross-link tests ───────────────


class TestAbacavirCrossLink:
    """P3-60 requirement: abacavir/HLA-B*57:01 bi-directional PGx cross-link."""

    def _get_abacavir_snp(self, panel_data: dict) -> dict:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == "rs2395029":
                    return snp
        pytest.fail("rs2395029 (HLA-B*57:01 proxy) not found")

    def test_abacavir_pgx_cross_link(self, panel_data: dict) -> None:
        snp = self._get_abacavir_snp(panel_data)
        assert "cross_module" in snp
        assert snp["cross_module"]["module"] == "pharmacogenomics"
        assert "abacavir" in snp["cross_module"]["note"].lower()

    def test_abacavir_bidirectional_note(self, panel_data: dict) -> None:
        snp = self._get_abacavir_snp(panel_data)
        assert "bi-directional" in snp["cross_module"]["note"].lower()

    def test_abacavir_evidence_level_4(self, panel_data: dict) -> None:
        """Abacavir/HLA-B*57:01 is CPIC Level A → ★★★★."""
        snp = self._get_abacavir_snp(panel_data)
        assert snp["evidence_level"] == 4

    def test_abacavir_proxy_r_squared(self, panel_data: dict) -> None:
        snp = self._get_abacavir_snp(panel_data)
        assert snp["hla_proxy"]["r_squared_eur"] == 0.97


# ── Drug hypersensitivity PGx cross-links ───────────────────────────────


class TestDrugPGxCrossLinks:
    """All drug hypersensitivity HLA proxies should cross-link to PGx."""

    DRUG_RSIDS = {"rs2395029", "rs144012689", "rs1061235", "rs9263726"}

    def test_all_drug_proxies_pgx_cross_link(self, panel_data: dict) -> None:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] in self.DRUG_RSIDS:
                    assert "cross_module" in snp, f"{snp['rsid']} missing PGx cross_module"
                    assert snp["cross_module"]["module"] == "pharmacogenomics"


# ── Histamine metabolism tests ──────────────────────────────────────────


class TestHistamineMetabolism:
    """Validate AOC1 and HNMT histamine catabolism SNPs."""

    HISTAMINE_RSIDS = {"rs10156191", "rs11558538"}

    def _get_histamine_snps(self, panel_data: dict) -> list[dict]:
        snps = []
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] in self.HISTAMINE_RSIDS:
                    snps.append(snp)
        return snps

    def test_both_histamine_snps_present(self, panel_data: dict) -> None:
        found = {s["rsid"] for s in self._get_histamine_snps(panel_data)}
        assert found == self.HISTAMINE_RSIDS

    def test_histamine_evidence_level_1(self, panel_data: dict) -> None:
        """Both histamine SNPs are candidate gene level (★☆)."""
        for snp in self._get_histamine_snps(panel_data):
            assert snp["evidence_level"] == 1, (
                f"{snp['rsid']} histamine should be evidence_level 1"
            )

    def test_histamine_homozygous_capped_at_moderate(self, panel_data: dict) -> None:
        """★☆ SNPs cannot have Elevated category (star_1_cap rule)."""
        for snp in self._get_histamine_snps(panel_data):
            for gt, effect in snp["genotype_effects"].items():
                assert effect["category"] != "Elevated", (
                    f"{snp['rsid']}:{gt} star-1 SNP should cap at Moderate"
                )

    def test_histamine_in_correct_pathway(self, panel_data: dict) -> None:
        for pathway in panel_data["pathways"]:
            if pathway["id"] == "histamine_metabolism":
                rsids = {s["rsid"] for s in pathway["snps"]}
                assert self.HISTAMINE_RSIDS.issubset(rsids)
                return
        pytest.fail("histamine_metabolism pathway not found")

    def test_histamine_de_emphasis_flag(self, panel_data: dict) -> None:
        """Histamine combined assessment should flag de-emphasis in UI."""
        sc = panel_data["special_calling"]["histamine_combined_assessment"]
        assert sc["de_emphasize_in_ui"] is True

    def test_aoc1_has_hgvs(self, panel_data: dict) -> None:
        for snp in self._get_histamine_snps(panel_data):
            if snp["rsid"] == "rs10156191":
                assert snp["hgvs_protein"] == "p.Thr16Met"

    def test_hnmt_has_hgvs(self, panel_data: dict) -> None:
        for snp in self._get_histamine_snps(panel_data):
            if snp["rsid"] == "rs11558538":
                assert snp["hgvs_protein"] == "p.Thr105Ile"


# ── Atopic conditions cross-module tests ─────────────────────────────────


class TestAtopicCrossModule:
    """IL13 should cross-link to Skin module (atopic dermatitis)."""

    def test_il13_skin_cross_link(self, panel_data: dict) -> None:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == "rs20541":
                    assert "cross_module" in snp
                    assert snp["cross_module"]["module"] == "skin"
                    return
        pytest.fail("rs20541 (IL13) not found")


# ── Celiac nutrigenomics cross-links ────────────────────────────────────


class TestCeliacNutrigenomicsCrossLink:
    """Celiac DQ2/DQ8 should cross-link to Nutrigenomics."""

    CELIAC_RSIDS = {"rs2187668", "rs7775228"}

    def test_celiac_nutrigenomics_cross_link(self, panel_data: dict) -> None:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] in self.CELIAC_RSIDS:
                    assert "cross_module" in snp, (
                        f"{snp['rsid']} missing nutrigenomics cross_module"
                    )
                    assert snp["cross_module"]["module"] == "nutrigenomics"


# ── Scoring rules tests ─────────────────────────────────────────────────


class TestScoringRules:
    def test_scoring_rules_present(self, panel_data: dict) -> None:
        assert "scoring_rules" in panel_data

    def test_star_1_cap(self, panel_data: dict) -> None:
        """star-1 evidence hard-caps at Moderate (project convention)."""
        assert panel_data["scoring_rules"]["star_1_cap"] == "Moderate"

    def test_elevated_requires_min_stars(self, panel_data: dict) -> None:
        assert panel_data["scoring_rules"]["elevated_requires_min_stars"] == 2

    def test_pathway_level_determination(self, panel_data: dict) -> None:
        rules = panel_data["scoring_rules"]
        assert rules["pathway_level_determination"] == "highest_category_across_snps"

    def test_valid_categories_listed(self, panel_data: dict) -> None:
        cats = panel_data["scoring_rules"]["categories"]
        assert set(cats) == VALID_CATEGORIES

    def test_hla_proxy_rule_documented(self, panel_data: dict) -> None:
        assert "hla_proxy_rule" in panel_data["scoring_rules"]
        rule = panel_data["scoring_rules"]["hla_proxy_rule"]
        assert "confirmatory" in rule.lower()

    def test_histamine_de_emphasis_rule(self, panel_data: dict) -> None:
        assert "histamine_de_emphasis" in panel_data["scoring_rules"]


# ── GWAS EFO terms tests ────────────────────────────────────────────────


class TestGWASEFOTerms:
    def test_gwas_efo_terms_present(self, panel_data: dict) -> None:
        assert "gwas_efo_terms" in panel_data
        terms = panel_data["gwas_efo_terms"]
        assert isinstance(terms, list)
        assert len(terms) > 0

    def test_key_allergy_efo_terms_included(self, panel_data: dict) -> None:
        terms = set(panel_data["gwas_efo_terms"])
        assert "allergy" in terms
        assert "allergic" in terms
        assert "asthma" in terms
        assert "atopic" in terms
        assert "ige" in terms
        assert "rhinitis" in terms
        assert "urticaria" in terms
        assert "drug hypersensitivity" in terms
        assert "food allergy" in terms
        assert "histamine" in terms
        assert "celiac disease" in terms

    def test_gwas_efo_terms_match_gwas_loader(self, panel_data: dict) -> None:
        """Panel EFO terms should match the _ALLERGY_TERMS in gwas.py."""
        from backend.annotation.gwas import EFO_MODULES

        panel_terms = frozenset(panel_data["gwas_efo_terms"])
        assert panel_terms == EFO_MODULES["allergy"]


# ── Pathway-specific SNP allocation tests ────────────────────────────────


class TestPathwayAllocation:
    def _get_pathway(self, panel_data: dict, pathway_id: str) -> dict:
        for p in panel_data["pathways"]:
            if p["id"] == pathway_id:
                return p
        pytest.fail(f"Pathway {pathway_id} not found")

    def test_atopic_conditions_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "atopic_conditions")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs20541" in rsids  # IL13
        assert "rs8076131" in rsids  # ORMDL3
        assert "rs324011" in rsids  # STAT6

    def test_drug_hypersensitivity_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "drug_hypersensitivity")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs2395029" in rsids  # HLA-B*57:01
        assert "rs144012689" in rsids  # HLA-B*15:02
        assert "rs1061235" in rsids  # HLA-A*31:01
        assert "rs9263726" in rsids  # HLA-B*58:01

    def test_food_sensitivity_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "food_sensitivity")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs2187668" in rsids  # HLA-DQ2
        assert "rs7775228" in rsids  # HLA-DQ8

    def test_histamine_metabolism_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "histamine_metabolism")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs10156191" in rsids  # AOC1
        assert "rs11558538" in rsids  # HNMT
