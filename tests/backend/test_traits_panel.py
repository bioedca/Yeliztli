"""Tests for the curated Traits & Personality SNP panel (P3-62).

Covers:
  - Panel JSON loading and structural validation
  - All 7 curated individual SNPs present with correct genes
  - 3 pathways (Cognitive Ability & Educational Attainment [PRS-primary],
    Personality Dimensions, Behavioral Traits)
  - 2 PRS weight sets (educational attainment Okbay 2022, cognitive ability
    Savage 2018)
  - DRD4 rs747302 proxy with coverage caveat
  - Evidence hard cap at ★★☆☆ (2) on all SNPs
  - Genotype effects categories are valid (Elevated/Moderate/Standard)
  - Scoring rules enforce PRS-primary, research_use_only, associative language
  - Module-level disclaimer present
  - GWAS EFO traits terms included
  - Cross-module links (Gene Health for ADHD, Sleep for chronotype)
  - Associative language flag on all SNPs
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
    / "traits_panel.json"
)

VALID_CATEGORIES = {"Elevated", "Moderate", "Standard"}

# 7 individual SNPs across personality + behavioral pathways
EXPECTED_RSIDS = {
    "rs1396862",  # CRHR1 neuroticism
    "rs2164273",  # WSCD2 extraversion
    "rs2572431",  # CTNNA2 openness
    "rs9611519",  # 5q14.3 agreeableness
    "rs2389621",  # KATNAL2 conscientiousness
    "rs993137",  # CADM2 risk tolerance
    "rs747302",  # DRD4 VNTR proxy
}

EXPECTED_PATHWAYS = {
    "cognitive_ability",
    "personality_big_five",
    "behavioral_traits",
}

EXPECTED_GENES = {
    "CRHR1",
    "WSCD2",
    "CTNNA2",
    "LOC101928162",
    "KATNAL2",
    "CADM2",
    "DRD4",
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
        assert panel_data["module"] == "traits"

    def test_panel_version(self, panel_data: dict) -> None:
        assert panel_data["version"] == "1.0.0"

    def test_panel_has_description(self, panel_data: dict) -> None:
        assert "description" in panel_data
        assert len(panel_data["description"]) > 0

    def test_panel_has_three_pathways(self, panel_data: dict) -> None:
        assert len(panel_data["pathways"]) == 3

    def test_pathway_ids(self, panel_data: dict) -> None:
        pathway_ids = {p["id"] for p in panel_data["pathways"]}
        assert pathway_ids == EXPECTED_PATHWAYS

    def test_pathway_names(self, panel_data: dict) -> None:
        pathway_names = {p["name"] for p in panel_data["pathways"]}
        assert "Cognitive Ability & Educational Attainment" in pathway_names
        assert "Personality Dimensions (Big Five)" in pathway_names
        assert "Behavioral Traits" in pathway_names

    def test_module_disclaimer_present(self, panel_data: dict) -> None:
        """Module-level disclaimer is required per PRD."""
        assert "module_disclaimer" in panel_data
        disclaimer = panel_data["module_disclaimer"]
        assert len(disclaimer) > 50
        assert "research" in disclaimer.lower() or "educational" in disclaimer.lower()
        assert "do not predict" in disclaimer.lower()


# ── SNP coverage tests ──────────────────────────────────────────────────


class TestSNPCoverage:
    def test_all_expected_rsids_present(self, panel_data: dict) -> None:
        """All 7 curated individual SNPs from the PRD must be present."""
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

    def test_total_individual_snp_count(self, panel_data: dict) -> None:
        """7 curated individual SNPs total across personality + behavioral pathways."""
        count = sum(len(p["snps"]) for p in panel_data["pathways"])
        assert count == 7


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

    def test_evidence_levels_capped_at_2(self, panel_data: dict) -> None:
        """All individual SNPs must have evidence_level ≤ 2 (★★☆☆ cap)."""
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                assert snp["evidence_level"] <= 2, (
                    f"{snp['rsid']} has evidence_level {snp['evidence_level']} "
                    f"which exceeds the ★★☆☆ cap"
                )

    def test_evidence_levels_valid(self, panel_data: dict) -> None:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                assert snp["evidence_level"] in (1, 2), (
                    f"{snp['rsid']} has invalid evidence_level: {snp['evidence_level']}"
                )

    def test_pmids_are_nonempty_lists(self, panel_data: dict) -> None:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                assert isinstance(snp["pmids"], list)
                assert len(snp["pmids"]) > 0, f"{snp['rsid']} has no PMIDs"
                for pmid in snp["pmids"]:
                    assert pmid.isdigit(), f"{snp['rsid']} has non-numeric PMID: {pmid}"

    def test_all_snps_have_associative_language_flag(self, panel_data: dict) -> None:
        """All trait SNPs must be flagged for associative language only."""
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                assert snp.get("associative_language") is True, (
                    f"{snp['rsid']} missing associative_language=true flag"
                )

    def test_all_snps_have_trait_domain(self, panel_data: dict) -> None:
        """Every individual SNP must declare which trait domain it belongs to."""
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                assert "trait_domain" in snp, f"{snp['rsid']} missing trait_domain"
                assert len(snp["trait_domain"]) > 0


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

    def test_no_elevated_on_star_1_snps(self, panel_data: dict) -> None:
        """★☆ evidence SNPs cannot have Elevated category (star_1_cap rule)."""
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["evidence_level"] == 1:
                    for gt, effect in snp["genotype_effects"].items():
                        assert effect["category"] != "Elevated", (
                            f"{snp['rsid']}:{gt} star-1 SNP should cap at Moderate"
                        )


# ── PRS weight sets tests ──────────────────────────────────────────────


class TestPRSWeightSets:
    """Validate the PRS weight set definitions for traits module."""

    def test_prs_weight_sets_present(self, panel_data: dict) -> None:
        assert "prs_weight_sets" in panel_data
        assert isinstance(panel_data["prs_weight_sets"], list)
        assert len(panel_data["prs_weight_sets"]) == 2

    def test_educational_attainment_weight_set(self, panel_data: dict) -> None:
        ws = panel_data["prs_weight_sets"][0]
        assert ws["trait"] == "educational_attainment"
        assert "Okbay" in ws["source_study"]
        assert "2022" in ws["source_study"]
        assert ws["source_pmid"] == "35361970"
        assert ws["source_ancestry"] == "EUR"
        assert ws["sample_size"] > 1000000  # >1M participants

    def test_cognitive_ability_weight_set(self, panel_data: dict) -> None:
        ws = panel_data["prs_weight_sets"][1]
        assert ws["trait"] == "cognitive_ability"
        assert "Savage" in ws["source_study"]
        assert "2018" in ws["source_study"]
        assert ws["source_pmid"] == "29942086"
        assert ws["source_ancestry"] == "EUR"
        assert ws["sample_size"] > 100000

    def test_weight_sets_have_required_fields(self, panel_data: dict) -> None:
        required = {
            "name",
            "trait",
            "source_ancestry",
            "source_study",
            "source_pmid",
            "sample_size",
            "reference_mean",
            "reference_std",
            "calibrated",
            "weights",
        }
        for ws in panel_data["prs_weight_sets"]:
            for field in required:
                assert field in ws, f"Weight set '{ws.get('name')}' missing field: {field}"

    def test_weight_entries_have_required_fields(self, panel_data: dict) -> None:
        for ws in panel_data["prs_weight_sets"]:
            for w in ws["weights"]:
                assert "rsid" in w
                assert w["rsid"].startswith("rs")
                assert "effect_allele" in w
                assert len(w["effect_allele"]) == 1
                assert "weight" in w
                assert isinstance(w["weight"], (int, float))

    def test_educational_attainment_has_sufficient_snps(self, panel_data: dict) -> None:
        ws = panel_data["prs_weight_sets"][0]
        assert len(ws["weights"]) >= 15, "Educational attainment PRS needs ≥15 SNPs"

    def test_cognitive_ability_has_sufficient_snps(self, panel_data: dict) -> None:
        ws = panel_data["prs_weight_sets"][1]
        assert len(ws["weights"]) >= 10, "Cognitive ability PRS needs ≥10 SNPs"

    def test_weight_sets_research_use_only(self, panel_data: dict) -> None:
        """All PRS weight sets must be flagged as research use only."""
        for ws in panel_data["prs_weight_sets"]:
            assert ws.get("research_use_only") is True, (
                f"Weight set '{ws['name']}' missing research_use_only=true"
            )

    def test_weight_sets_are_uncalibrated_until_reference_distribution_exists(
        self, panel_data: dict
    ) -> None:
        """Bundled traits PRS reference params are placeholders, so percentile
        output must stay withheld until calibrated distributions are supplied."""
        assert panel_data["prs_weight_sets"]
        for ws in panel_data["prs_weight_sets"]:
            assert ws["calibrated"] is False, (
                f"Weight set '{ws['name']}' must not expose placeholder percentiles"
            )
            assert "marked uncalibrated" in ws["calibration_note"]

    def test_weight_sets_evidence_capped(self, panel_data: dict) -> None:
        """PRS weight sets must respect the ★★☆☆ evidence cap."""
        for ws in panel_data["prs_weight_sets"]:
            assert ws.get("evidence_cap", 99) <= 2, (
                f"Weight set '{ws['name']}' evidence_cap exceeds ★★☆☆"
            )

    def test_no_duplicate_rsids_within_weight_set(self, panel_data: dict) -> None:
        for ws in panel_data["prs_weight_sets"]:
            rsids = [w["rsid"] for w in ws["weights"]]
            assert len(rsids) == len(set(rsids)), f"Weight set '{ws['name']}' has duplicate rsids"


# ── Cognitive pathway PRS-primary tests ────────────────────────────────


class TestCognitivePathway:
    """The cognitive ability pathway is PRS-primary with no individual SNPs."""

    def test_cognitive_pathway_is_prs_primary(self, panel_data: dict) -> None:
        for pw in panel_data["pathways"]:
            if pw["id"] == "cognitive_ability":
                assert pw.get("prs_primary") is True
                return
        pytest.fail("cognitive_ability pathway not found")

    def test_cognitive_pathway_has_no_individual_snps(self, panel_data: dict) -> None:
        """PRS-primary pathways have no individual SNPs — scoring is via PRS engine."""
        for pw in panel_data["pathways"]:
            if pw["id"] == "cognitive_ability":
                assert len(pw["snps"]) == 0
                return
        pytest.fail("cognitive_ability pathway not found")


# ── DRD4 proxy tests ──────────────────────────────────────────────────


class TestDRD4Proxy:
    """Validate DRD4 rs747302 tag SNP proxy for exon III VNTR."""

    def _get_drd4(self, panel_data: dict) -> dict:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == "rs747302":
                    return snp
        pytest.fail("DRD4 rs747302 not found in panel")

    def test_drd4_has_coverage_note(self, panel_data: dict) -> None:
        drd4 = self._get_drd4(panel_data)
        assert "coverage_note" in drd4
        assert "proxy" in drd4["coverage_note"].lower()
        assert "vntr" in drd4["coverage_note"].lower()

    def test_drd4_evidence_level_1(self, panel_data: dict) -> None:
        """DRD4 VNTR proxy is candidate gene level (★☆)."""
        drd4 = self._get_drd4(panel_data)
        assert drd4["evidence_level"] == 1

    def test_drd4_capped_at_moderate(self, panel_data: dict) -> None:
        """★☆ evidence means no Elevated category allowed."""
        drd4 = self._get_drd4(panel_data)
        for gt, effect in drd4["genotype_effects"].items():
            assert effect["category"] != "Elevated", (
                f"DRD4 {gt} should cap at Moderate (star-1 SNP)"
            )

    def test_drd4_in_behavioral_traits_pathway(self, panel_data: dict) -> None:
        for pathway in panel_data["pathways"]:
            if pathway["id"] == "behavioral_traits":
                rsids = {s["rsid"] for s in pathway["snps"]}
                assert "rs747302" in rsids
                return
        pytest.fail("behavioral_traits pathway not found")

    def test_drd4_special_calling_section(self, panel_data: dict) -> None:
        assert "special_calling" in panel_data
        assert "DRD4_proxy" in panel_data["special_calling"]
        sc = panel_data["special_calling"]["DRD4_proxy"]
        assert sc["rsid"] == "rs747302"
        assert sc["coverage_caveat_required"] is True
        assert "VNTR" in sc["proxy_target"]

    def test_drd4_gene_health_cross_link(self, panel_data: dict) -> None:
        """DRD4 should cross-link to Gene Health module (ADHD)."""
        drd4 = self._get_drd4(panel_data)
        assert "cross_module" in drd4
        assert drd4["cross_module"]["module"] == "gene_health"
        assert "adhd" in drd4["cross_module"]["note"].lower()

    def test_drd4_trait_domain_novelty_seeking(self, panel_data: dict) -> None:
        drd4 = self._get_drd4(panel_data)
        assert drd4["trait_domain"] == "novelty_seeking"


# ── Big Five personality SNPs tests ───────────────────────────────────


class TestBigFivePersonality:
    """Validate Big Five personality dimension SNPs."""

    BIG_FIVE_DOMAINS = {
        "neuroticism",
        "extraversion",
        "openness",
        "agreeableness",
        "conscientiousness",
    }

    def _get_personality_snps(self, panel_data: dict) -> list[dict]:
        for pw in panel_data["pathways"]:
            if pw["id"] == "personality_big_five":
                return pw["snps"]
        pytest.fail("personality_big_five pathway not found")

    def test_five_personality_snps(self, panel_data: dict) -> None:
        snps = self._get_personality_snps(panel_data)
        assert len(snps) == 5

    def test_all_big_five_domains_covered(self, panel_data: dict) -> None:
        """Each Big Five dimension should have at least one SNP."""
        snps = self._get_personality_snps(panel_data)
        domains = {s["trait_domain"] for s in snps}
        assert domains == self.BIG_FIVE_DOMAINS

    def test_neuroticism_snp_present(self, panel_data: dict) -> None:
        snps = self._get_personality_snps(panel_data)
        neuro = [s for s in snps if s["trait_domain"] == "neuroticism"]
        assert len(neuro) == 1
        assert neuro[0]["rsid"] == "rs1396862"
        assert neuro[0]["gene"] == "CRHR1"

    def test_extraversion_snp_present(self, panel_data: dict) -> None:
        snps = self._get_personality_snps(panel_data)
        extra = [s for s in snps if s["trait_domain"] == "extraversion"]
        assert len(extra) == 1
        assert extra[0]["rsid"] == "rs2164273"
        assert extra[0]["gene"] == "WSCD2"


# ── Risk tolerance tests ──────────────────────────────────────────────


class TestRiskTolerance:
    """Validate risk tolerance SNP (CADM2 rs993137)."""

    def _get_risk_snp(self, panel_data: dict) -> dict:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == "rs993137":
                    return snp
        pytest.fail("CADM2 rs993137 not found")

    def test_risk_tolerance_evidence_level_2(self, panel_data: dict) -> None:
        snp = self._get_risk_snp(panel_data)
        assert snp["evidence_level"] == 2

    def test_risk_tolerance_trait_domain(self, panel_data: dict) -> None:
        snp = self._get_risk_snp(panel_data)
        assert snp["trait_domain"] == "risk_tolerance"

    def test_risk_tolerance_in_behavioral_pathway(self, panel_data: dict) -> None:
        for pw in panel_data["pathways"]:
            if pw["id"] == "behavioral_traits":
                rsids = {s["rsid"] for s in pw["snps"]}
                assert "rs993137" in rsids
                return
        pytest.fail("behavioral_traits pathway not found")


# ── Scoring rules tests ─────────────────────────────────────────────────


class TestScoringRules:
    def test_scoring_rules_present(self, panel_data: dict) -> None:
        assert "scoring_rules" in panel_data

    def test_evidence_cap_is_2(self, panel_data: dict) -> None:
        """Hard cap at ★★☆☆ per PRD §3.4b."""
        assert panel_data["scoring_rules"]["evidence_cap"] == 2

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

    def test_prs_primary_flag(self, panel_data: dict) -> None:
        assert panel_data["scoring_rules"]["prs_primary"] is True

    def test_research_use_only_flag(self, panel_data: dict) -> None:
        assert panel_data["scoring_rules"]["research_use_only"] is True

    def test_associative_language_rule(self, panel_data: dict) -> None:
        assert "associative_language_rule" in panel_data["scoring_rules"]
        rule = panel_data["scoring_rules"]["associative_language_rule"]
        assert "associative" in rule.lower()

    def test_drd4_proxy_rule_documented(self, panel_data: dict) -> None:
        assert "drd4_proxy_rule" in panel_data["scoring_rules"]
        rule = panel_data["scoring_rules"]["drd4_proxy_rule"]
        assert "proxy" in rule.lower()
        assert "rs747302" in rule


# ── Cross-module links tests ──────────────────────────────────────────


class TestCrossModuleLinks:
    def test_cross_module_links_present(self, panel_data: dict) -> None:
        assert "cross_module_links" in panel_data
        assert isinstance(panel_data["cross_module_links"], list)

    def test_adhd_gene_health_link(self, panel_data: dict) -> None:
        """ADHD cross-link to Gene Health module."""
        links = panel_data["cross_module_links"]
        adhd_links = [lk for lk in links if lk["link_type"] == "ADHD"]
        assert len(adhd_links) == 1
        assert adhd_links[0]["to_module"] == "gene_health"


# ── GWAS EFO terms tests ────────────────────────────────────────────────


class TestGWASEFOTerms:
    def test_gwas_efo_terms_present(self, panel_data: dict) -> None:
        assert "gwas_efo_terms" in panel_data
        terms = panel_data["gwas_efo_terms"]
        assert isinstance(terms, list)
        assert len(terms) > 0

    def test_key_traits_efo_terms_included(self, panel_data: dict) -> None:
        terms = set(panel_data["gwas_efo_terms"])
        assert "educational attainment" in terms
        assert "cognitive" in terms
        assert "intelligence" in terms
        assert "neuroticism" in terms
        assert "extraversion" in terms
        assert "risk tolerance" in terms
        assert "personality" in terms
        assert "adhd" in terms
        assert "depression" in terms
        assert "memory" in terms

    def test_gwas_efo_terms_match_gwas_loader(self, panel_data: dict) -> None:
        """Panel EFO terms should match the _TRAITS_TERMS in gwas.py."""
        from backend.annotation.gwas import EFO_MODULES

        panel_terms = frozenset(panel_data["gwas_efo_terms"])
        assert panel_terms == EFO_MODULES["traits"]


# ── Pathway-specific SNP allocation tests ────────────────────────────────


class TestPathwayAllocation:
    def _get_pathway(self, panel_data: dict, pathway_id: str) -> dict:
        for p in panel_data["pathways"]:
            if p["id"] == pathway_id:
                return p
        pytest.fail(f"Pathway {pathway_id} not found")

    def test_cognitive_ability_prs_only(self, panel_data: dict) -> None:
        """Cognitive pathway uses PRS only — no individual SNPs."""
        pw = self._get_pathway(panel_data, "cognitive_ability")
        assert len(pw["snps"]) == 0
        assert pw.get("prs_primary") is True

    def test_personality_big_five_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "personality_big_five")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs1396862" in rsids  # CRHR1 neuroticism
        assert "rs2164273" in rsids  # WSCD2 extraversion
        assert "rs2572431" in rsids  # CTNNA2 openness
        assert "rs9611519" in rsids  # agreeableness
        assert "rs2389621" in rsids  # KATNAL2 conscientiousness

    def test_behavioral_traits_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "behavioral_traits")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs993137" in rsids  # CADM2 risk tolerance
        assert "rs747302" in rsids  # DRD4 VNTR proxy


# ── Evidence cap enforcement tests ───────────────────────────────────


class TestEvidenceCapEnforcement:
    """T3-60 precursor: all findings must respect ★★☆☆ cap."""

    def test_module_level_evidence_cap(self, panel_data: dict) -> None:
        assert panel_data.get("evidence_cap") == 2

    def test_all_snp_evidence_at_or_below_cap(self, panel_data: dict) -> None:
        cap = panel_data["evidence_cap"]
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                assert snp["evidence_level"] <= cap, (
                    f"{snp['rsid']} evidence {snp['evidence_level']} exceeds cap {cap}"
                )

    def test_prs_weight_sets_respect_cap(self, panel_data: dict) -> None:
        cap = panel_data["evidence_cap"]
        for ws in panel_data["prs_weight_sets"]:
            assert ws.get("evidence_cap", 99) <= cap, (
                f"Weight set '{ws['name']}' evidence_cap exceeds module cap"
            )
