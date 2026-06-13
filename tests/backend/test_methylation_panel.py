"""Tests for the curated MTHFR & Methylation SNP panel (P3-51).

Covers:
  - Panel JSON loading and structural validation
  - ~35 curated SNPs present across 5 sub-pathways
  - 5 pathway cards (Folate & MTHFR, Methionine Cycle, Transsulfuration,
    BH4 & Neurotransmitter Synthesis, Choline & Betaine)
  - MTHFR C677T and A1298C as flagship variants
  - CBS rs234706 proxy with coverage caveat
  - COMT Val158Met framed as catecholamine clearance only
  - MTHFR compound heterozygosity special calling
  - Genotype effects categories are valid (Elevated/Moderate/Standard)
  - Evidence levels within expected range
  - Nutrigenomics migration note for MTHFR
  - Scoring rules match project conventions for multiple Moderate findings
  - GWAS EFO methylation terms included
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
    / "methylation_panel.json"
)

VALID_CATEGORIES = {"Elevated", "Moderate", "Standard"}

EXPECTED_PATHWAYS = {
    "folate_mthfr",
    "methionine_cycle",
    "transsulfuration",
    "bh4_neurotransmitter",
    "choline_betaine",
}

# All expected rsids across the 5 pathways (~35 SNPs)
EXPECTED_RSIDS = {
    # Folate & MTHFR (8)
    "rs1801133",  # MTHFR C677T
    "rs1801131",  # MTHFR A1298C
    "rs70991108",  # DHFR 19bp del
    "rs1051266",  # SLC19A1
    "rs202676",  # FOLH1
    "rs1801198",  # TCN2
    "rs3758149",  # GGH
    "rs1979277",  # SHMT1
    # Methionine Cycle (7)
    "rs1805087",  # MTR
    "rs1801394",  # MTRR
    "rs10887718",  # MAT1A
    "rs819147",  # AHCY
    "rs3733890",  # BHMT
    "rs2228611",  # DNMT1
    "rs2424913",  # DNMT3B
    # Transsulfuration (7)
    "rs234706",  # CBS proxy
    "rs1021737",  # CTH
    "rs17883901",  # GCLC
    "rs41303970",  # GCLM
    "rs1050450",  # GPX1
    "rs4880",  # SOD2
    "rs3761144",  # GSS
    # BH4 & Neurotransmitter (7)
    "rs4680",  # COMT Val158Met
    "rs2228570",  # VDR FokI
    "rs1544410",  # VDR BsmI
    "rs2236225",  # MTHFD1
    "rs6495446",  # MTHFS
    "rs8007267",  # GCH1
    "rs1677693",  # QDPR
    # Choline & Betaine (6)
    "rs12325817",  # PEMT
    "rs9001",  # CHDH
    "rs585800",  # BHMT2
    "rs3199966",  # SLC44A1
    "rs2266782",  # FMO3
    "rs7639752",  # PCYT1A
}

EXPECTED_GENES = {
    "MTHFR",
    "DHFR",
    "SLC19A1",
    "FOLH1",
    "TCN2",
    "GGH",
    "SHMT1",
    "MTR",
    "MTRR",
    "MAT1A",
    "AHCY",
    "BHMT",
    "DNMT1",
    "DNMT3B",
    "CBS",
    "CTH",
    "GCLC",
    "GCLM",
    "GPX1",
    "SOD2",
    "GSS",
    "COMT",
    "VDR",
    "MTHFD1",
    "MTHFS",
    "GCH1",
    "QDPR",
    "PEMT",
    "CHDH",
    "BHMT2",
    "SLC44A1",
    "FMO3",
    "PCYT1A",
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
        assert panel_data["module"] == "methylation"

    def test_panel_version(self, panel_data: dict) -> None:
        assert panel_data["version"] == "1.0.0"

    def test_panel_has_description(self, panel_data: dict) -> None:
        assert "description" in panel_data
        assert len(panel_data["description"]) > 0

    def test_panel_has_five_pathways(self, panel_data: dict) -> None:
        assert len(panel_data["pathways"]) == 5

    def test_pathway_ids(self, panel_data: dict) -> None:
        pathway_ids = {p["id"] for p in panel_data["pathways"]}
        assert pathway_ids == EXPECTED_PATHWAYS

    def test_pathway_names(self, panel_data: dict) -> None:
        pathway_names = {p["name"] for p in panel_data["pathways"]}
        assert "Folate & MTHFR" in pathway_names
        assert "Methionine Cycle" in pathway_names
        assert "Transsulfuration" in pathway_names
        assert "BH4 & Neurotransmitter Synthesis" in pathway_names
        assert "Choline & Betaine" in pathway_names


# ── SNP coverage tests ──────────────────────────────────────────────────


class TestSNPCoverage:
    def test_all_expected_rsids_present(self, panel_data: dict) -> None:
        """All ~35 curated SNPs from the PRD must be present."""
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
        """~35 curated SNPs total across all pathways."""
        count = sum(len(p["snps"]) for p in panel_data["pathways"])
        assert count == 35

    def test_folate_mthfr_snp_count(self, panel_data: dict) -> None:
        pw = next(p for p in panel_data["pathways"] if p["id"] == "folate_mthfr")
        assert len(pw["snps"]) == 8

    def test_methionine_cycle_snp_count(self, panel_data: dict) -> None:
        pw = next(p for p in panel_data["pathways"] if p["id"] == "methionine_cycle")
        assert len(pw["snps"]) == 7

    def test_transsulfuration_snp_count(self, panel_data: dict) -> None:
        pw = next(p for p in panel_data["pathways"] if p["id"] == "transsulfuration")
        assert len(pw["snps"]) == 7

    def test_bh4_neurotransmitter_snp_count(self, panel_data: dict) -> None:
        pw = next(p for p in panel_data["pathways"] if p["id"] == "bh4_neurotransmitter")
        assert len(pw["snps"]) == 7

    def test_choline_betaine_snp_count(self, panel_data: dict) -> None:
        pw = next(p for p in panel_data["pathways"] if p["id"] == "choline_betaine")
        assert len(pw["snps"]) == 6


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

    def test_evidence_gating_star_1_no_elevated(self, panel_data: dict) -> None:
        """SNPs with evidence_level=1 must NOT have Elevated category (star_1_cap)."""
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["evidence_level"] == 1:
                    for gt, effect in snp["genotype_effects"].items():
                        assert effect["category"] != "Elevated", (
                            f"{snp['rsid']}:{gt} has Elevated with evidence_level=1 "
                            f"(violates star_1_cap=Moderate)"
                        )


# ── MTHFR flagship variant tests ────────────────────────────────────────


class TestMTHFRFlagship:
    """Validate MTHFR C677T and A1298C as flagship methylation variants."""

    def _get_snp(self, panel_data: dict, rsid: str) -> dict:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == rsid:
                    return snp
        pytest.fail(f"{rsid} not found in panel")

    def test_c677t_in_folate_pathway(self, panel_data: dict) -> None:
        pw = next(p for p in panel_data["pathways"] if p["id"] == "folate_mthfr")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs1801133" in rsids

    def test_a1298c_in_folate_pathway(self, panel_data: dict) -> None:
        pw = next(p for p in panel_data["pathways"] if p["id"] == "folate_mthfr")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs1801131" in rsids

    def test_c677t_aa_elevated(self, panel_data: dict) -> None:
        """C677T TT (AA on plus strand) → Elevated (~30% residual activity)."""
        snp = self._get_snp(panel_data, "rs1801133")
        assert snp["genotype_effects"]["AA"]["category"] == "Elevated"
        summary = snp["genotype_effects"]["AA"]["effect_summary"].lower()
        assert "30%" in summary or "significantly reduced" in summary

    def test_c677t_gg_standard(self, panel_data: dict) -> None:
        """C677T CC (GG on plus strand) → Standard."""
        snp = self._get_snp(panel_data, "rs1801133")
        assert snp["genotype_effects"]["GG"]["category"] == "Standard"

    def test_c677t_evidence_level(self, panel_data: dict) -> None:
        snp = self._get_snp(panel_data, "rs1801133")
        assert snp["evidence_level"] == 2

    def test_a1298c_cc_moderate(self, panel_data: dict) -> None:
        """A1298C CC → Moderate (milder than C677T)."""
        snp = self._get_snp(panel_data, "rs1801131")
        assert snp["genotype_effects"]["CC"]["category"] == "Moderate"

    def test_a1298c_aa_standard(self, panel_data: dict) -> None:
        snp = self._get_snp(panel_data, "rs1801131")
        assert snp["genotype_effects"]["AA"]["category"] == "Standard"

    def test_a1298c_evidence_level(self, panel_data: dict) -> None:
        snp = self._get_snp(panel_data, "rs1801131")
        assert snp["evidence_level"] == 2

    def test_c677t_has_hgvs_protein(self, panel_data: dict) -> None:
        snp = self._get_snp(panel_data, "rs1801133")
        assert snp["hgvs_protein"] == "p.Ala222Val"

    def test_a1298c_has_hgvs_protein(self, panel_data: dict) -> None:
        snp = self._get_snp(panel_data, "rs1801131")
        assert snp["hgvs_protein"] == "p.Glu429Ala"


# ── CBS proxy tests ─────────────────────────────────────────────────────


class TestCBSProxy:
    """Validate CBS rs234706 proxy SNP with coverage caveat."""

    def _get_cbs(self, panel_data: dict) -> dict:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == "rs234706":
                    return snp
        pytest.fail("CBS rs234706 not found in panel")

    def test_cbs_in_transsulfuration(self, panel_data: dict) -> None:
        pw = next(p for p in panel_data["pathways"] if p["id"] == "transsulfuration")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs234706" in rsids

    def test_cbs_has_coverage_note(self, panel_data: dict) -> None:
        cbs = self._get_cbs(panel_data)
        assert "coverage_note" in cbs
        assert "proxy" in cbs["coverage_note"].lower()
        assert "synonymous" in cbs["coverage_note"].lower()

    def test_cbs_coverage_note_mentions_ancestry(self, panel_data: dict) -> None:
        cbs = self._get_cbs(panel_data)
        assert "ancestry" in cbs["coverage_note"].lower()

    def test_cbs_cc_standard(self, panel_data: dict) -> None:
        cbs = self._get_cbs(panel_data)
        assert cbs["genotype_effects"]["CC"]["category"] == "Standard"

    def test_cbs_evidence_level(self, panel_data: dict) -> None:
        """CBS proxy → evidence_level 1 (proxy, not fully characterized)."""
        cbs = self._get_cbs(panel_data)
        assert cbs["evidence_level"] == 1

    def test_cbs_in_special_calling(self, panel_data: dict) -> None:
        assert "CBS_proxy_note" in panel_data["special_calling"]
        sc = panel_data["special_calling"]["CBS_proxy_note"]
        assert sc["rsid"] == "rs234706"
        assert "proxy_accuracy_note" in sc

    def test_cbs_cites_relevant_evidence_not_unrelated(self, panel_data: dict) -> None:
        """#211 — the CBS row must cite the directly-relevant association study,
        not the CLN6 lipofuscinosis (12815591), Crisponi (15637710), or
        mouse-brain GSM (18175331) papers it carried in error. 12529702 =
        Lievers 2003, Eur J Hum Genet (CBS 699C>T & hyperhomocysteinaemia: no
        association)."""
        cbs = self._get_cbs(panel_data)
        assert cbs["pmids"] == ["12529702"], cbs["pmids"]
        assert {"12815591", "15637710", "18175331"}.isdisjoint(cbs["pmids"])

    def test_cbs_not_reported_as_actionable(self, panel_data: dict) -> None:
        """#211 — rs234706 is a synonymous proxy with no demonstrated homocysteine
        association, so no genotype may be an actionable (non-Standard) finding."""
        cbs = self._get_cbs(panel_data)
        for gt, effect in cbs["genotype_effects"].items():
            assert effect["category"] == "Standard", (
                f"{gt} is {effect['category']}, expected Standard"
            )

    def test_cbs_effect_text_states_no_association(self, panel_data: dict) -> None:
        """The TT effect text must convey the null/no-association framing rather
        than the previous 'upregulated transsulfuration' overstatement."""
        cbs = self._get_cbs(panel_data)
        tt = cbs["genotype_effects"]["TT"]["effect_summary"].lower()
        assert "no association" in tt
        assert "upregulated transsulfuration" not in tt


# ── COMT catecholamine framing tests ────────────────────────────────────


class TestCOMTFraming:
    """Validate COMT Val158Met is framed as catecholamine clearance only."""

    def _get_comt(self, panel_data: dict) -> dict:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == "rs4680":
                    return snp
        pytest.fail("COMT rs4680 not found in panel")

    def test_comt_in_bh4_pathway(self, panel_data: dict) -> None:
        pw = next(p for p in panel_data["pathways"] if p["id"] == "bh4_neurotransmitter")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs4680" in rsids

    def test_comt_aa_elevated(self, panel_data: dict) -> None:
        """Met/Met (AA) → Elevated (slower catecholamine clearance)."""
        comt = self._get_comt(panel_data)
        assert comt["genotype_effects"]["AA"]["category"] == "Elevated"

    def test_comt_gg_standard(self, panel_data: dict) -> None:
        """Val/Val (GG) → Standard."""
        comt = self._get_comt(panel_data)
        assert comt["genotype_effects"]["GG"]["category"] == "Standard"

    def test_comt_framing_mentions_catecholamine(self, panel_data: dict) -> None:
        comt = self._get_comt(panel_data)
        summary = comt["genotype_effects"]["AA"]["effect_summary"].lower()
        assert "catecholamine" in summary

    def test_comt_framing_no_psychiatric(self, panel_data: dict) -> None:
        """COMT must NOT be framed in psychiatric/warrior-worrier terms."""
        comt = self._get_comt(panel_data)
        for gt, effect in comt["genotype_effects"].items():
            summary_lower = effect["effect_summary"].lower()
            assert "warrior" not in summary_lower, f"COMT {gt} uses warrior framing"
            assert "worrier" not in summary_lower, f"COMT {gt} uses worrier framing"
            assert "psychiatric" not in summary_lower, f"COMT {gt} uses psychiatric framing"

    def test_comt_evidence_level(self, panel_data: dict) -> None:
        comt = self._get_comt(panel_data)
        assert comt["evidence_level"] == 2

    def test_comt_in_special_calling(self, panel_data: dict) -> None:
        assert "COMT_catecholamine_framing" in panel_data["special_calling"]
        sc = panel_data["special_calling"]["COMT_catecholamine_framing"]
        assert sc["rsid"] == "rs4680"
        assert sc["framing_restriction"] == "catecholamine_clearance_only"

    def test_comt_has_hgvs_protein(self, panel_data: dict) -> None:
        comt = self._get_comt(panel_data)
        assert comt["hgvs_protein"] == "p.Val158Met"


# ── MTHFR compound heterozygosity special calling tests ──────────────────


class TestMTHFRCompoundHet:
    """Validate MTHFR compound heterozygosity special calling metadata."""

    def test_compound_het_in_special_calling(self, panel_data: dict) -> None:
        assert "MTHFR_compound_heterozygosity" in panel_data["special_calling"]

    def test_compound_het_rsids(self, panel_data: dict) -> None:
        sc = panel_data["special_calling"]["MTHFR_compound_heterozygosity"]
        assert set(sc["rsids"]) == {"rs1801133", "rs1801131"}

    def test_compound_het_states(self, panel_data: dict) -> None:
        sc = panel_data["special_calling"]["MTHFR_compound_heterozygosity"]
        assert "compound_het" in sc["states"]
        assert "double_homozygous" in sc["states"]

    def test_compound_het_genotypes(self, panel_data: dict) -> None:
        state = panel_data["special_calling"]["MTHFR_compound_heterozygosity"]["states"][
            "compound_het"
        ]
        assert set(state["c677t_genotypes"]) == {"GA", "AG"}
        assert set(state["a1298c_genotypes"]) == {"AC", "CA"}


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

    def test_multiple_moderate_findings_note(self, panel_data: dict) -> None:
        """Multiple Moderate findings are contextual, not pathway escalation."""
        rules = panel_data["scoring_rules"]
        assert "multiple_moderate_findings_note" in rules
        note = rules["multiple_moderate_findings_note"].lower()
        assert "contextual" in note
        assert "do not promote" in note
        assert "elevated" in note


# ── GWAS EFO terms tests ────────────────────────────────────────────────


class TestGWASEFOTerms:
    def test_gwas_efo_terms_present(self, panel_data: dict) -> None:
        assert "gwas_efo_terms" in panel_data
        terms = panel_data["gwas_efo_terms"]
        assert isinstance(terms, list)
        assert len(terms) > 0

    def test_key_methylation_efo_terms_included(self, panel_data: dict) -> None:
        terms = set(panel_data["gwas_efo_terms"])
        assert "methylation" in terms
        assert "homocysteine" in terms
        assert "folate" in terms
        assert "methionine" in terms
        assert "glutathione" in terms
        assert "choline" in terms
        assert "betaine" in terms

    def test_gwas_efo_terms_match_gwas_loader(self, panel_data: dict) -> None:
        """Panel EFO terms should match the _METHYLATION_TERMS in gwas.py."""
        from backend.annotation.gwas import _METHYLATION_TERMS

        panel_terms = frozenset(panel_data["gwas_efo_terms"])
        assert panel_terms == _METHYLATION_TERMS


# ── Pathway-specific SNP allocation tests ────────────────────────────────


class TestPathwayAllocation:
    def _get_pathway(self, panel_data: dict, pathway_id: str) -> dict:
        for p in panel_data["pathways"]:
            if p["id"] == pathway_id:
                return p
        pytest.fail(f"Pathway {pathway_id} not found")

    def test_folate_mthfr_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "folate_mthfr")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs1801133" in rsids  # MTHFR C677T
        assert "rs1801131" in rsids  # MTHFR A1298C
        assert "rs70991108" in rsids  # DHFR
        assert "rs1051266" in rsids  # SLC19A1
        assert "rs202676" in rsids  # FOLH1
        assert "rs1801198" in rsids  # TCN2
        assert "rs3758149" in rsids  # GGH
        assert "rs1979277" in rsids  # SHMT1

    def test_methionine_cycle_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "methionine_cycle")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs1805087" in rsids  # MTR
        assert "rs1801394" in rsids  # MTRR
        assert "rs10887718" in rsids  # MAT1A
        assert "rs819147" in rsids  # AHCY
        assert "rs3733890" in rsids  # BHMT
        assert "rs2228611" in rsids  # DNMT1
        assert "rs2424913" in rsids  # DNMT3B

    def test_transsulfuration_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "transsulfuration")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs234706" in rsids  # CBS
        assert "rs1021737" in rsids  # CTH
        assert "rs17883901" in rsids  # GCLC
        assert "rs41303970" in rsids  # GCLM
        assert "rs1050450" in rsids  # GPX1
        assert "rs4880" in rsids  # SOD2
        assert "rs3761144" in rsids  # GSS

    def test_bh4_neurotransmitter_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "bh4_neurotransmitter")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs4680" in rsids  # COMT
        assert "rs2228570" in rsids  # VDR FokI
        assert "rs1544410" in rsids  # VDR BsmI
        assert "rs2236225" in rsids  # MTHFD1
        assert "rs6495446" in rsids  # MTHFS
        assert "rs8007267" in rsids  # GCH1
        assert "rs1677693" in rsids  # QDPR

    def test_choline_betaine_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "choline_betaine")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs12325817" in rsids  # PEMT
        assert "rs9001" in rsids  # CHDH
        assert "rs585800" in rsids  # BHMT2
        assert "rs3199966" in rsids  # SLC44A1
        assert "rs2266782" in rsids  # FMO3
        assert "rs7639752" in rsids  # PCYT1A


# ── Nutrigenomics migration note tests ──────────────────────────────────


class TestNutrigenomicsMigration:
    """Validate migration metadata for MTHFR from Nutrigenomics."""

    def test_additional_genes_has_migration_note(self, panel_data: dict) -> None:
        assert "additional_genes" in panel_data
        assert "nutrigenomics_migration" in panel_data["additional_genes"]

    def test_migration_rsids(self, panel_data: dict) -> None:
        migration = panel_data["additional_genes"]["nutrigenomics_migration"]
        assert set(migration["rsids"]) == {"rs1801133", "rs1801131", "rs1801394"}

    def test_migration_note_content(self, panel_data: dict) -> None:
        migration = panel_data["additional_genes"]["nutrigenomics_migration"]
        assert "nutrigenomics" in migration["note"].lower()
        assert "migrate" in migration["note"].lower() or "migration" in migration["note"].lower()


# ── DHFR coverage note test ─────────────────────────────────────────────


class TestDHFRCoverage:
    """Validate DHFR 19bp deletion coverage note."""

    def _get_dhfr(self, panel_data: dict) -> dict:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == "rs70991108":
                    return snp
        pytest.fail("DHFR rs70991108 not found in panel")

    def test_dhfr_has_coverage_note(self, panel_data: dict) -> None:
        dhfr = self._get_dhfr(panel_data)
        assert "coverage_note" in dhfr
        note = dhfr["coverage_note"].lower()
        assert "19bp" in note or "deletion" in note


# ── MTRR citation provenance (issue #206) ────────────────────────────────


def _all_pmids(obj: object):
    """Recursively yield every PMID string under any ``pmids`` list."""
    if isinstance(obj, dict):
        if isinstance(obj.get("pmids"), list):
            yield from obj["pmids"]
        for value in obj.values():
            yield from _all_pmids(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _all_pmids(item)


class TestMTRRCitationProvenance:
    """Validate MTRR rs1801394 A66G cites on-topic homocysteine evidence.

    The MTRR A66G row previously cited PMID 12181445, which resolves to an
    unrelated fludarabine / Chk1-Cdc25A S-phase-checkpoint paper (Sampath et
    al., Mol Pharmacol 2002) and does not support MTRR / methionine-synthase-
    reductase / homocysteine-remethylation biology. It was replaced with two
    verified MTRR A66G / homocysteine references. Pin the row so the off-topic
    PMID cannot silently reappear anywhere in the panel.
    """

    # Verified on-topic citations for the MTRR rs1801394 A66G row:
    _MTRR_PMIDS = {
        "23824729",  # van Meurs 2013, AJCN — homocysteine GWAS (pre-existing, valid)
        "11472746",  # Gaughan 2001, Atherosclerosis — MTRR A66G determines plasma homocysteine
        "15514263",  # Vaughn 2004, J Nutr — MTRR 66A>G + MTHFR 677TT elevate homocysteine
    }
    _BANNED_PMID = "12181445"  # unrelated fludarabine / Chk1-Cdc25A paper

    def _get_mtrr(self, panel_data: dict) -> dict:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == "rs1801394":
                    return snp
        pytest.fail("MTRR rs1801394 not found in panel")

    def test_mtrr_cites_verified_homocysteine_refs(self, panel_data: dict) -> None:
        mtrr = self._get_mtrr(panel_data)
        assert set(mtrr["pmids"]) == self._MTRR_PMIDS

    def test_mtrr_drops_unrelated_pmid(self, panel_data: dict) -> None:
        mtrr = self._get_mtrr(panel_data)
        assert self._BANNED_PMID not in mtrr["pmids"], (
            f"MTRR still cites the unrelated PMID {self._BANNED_PMID}"
        )

    def test_unrelated_pmid_absent_from_whole_panel(self, panel_data: dict) -> None:
        # The banned PMID was exclusive to the MTRR row, so it should not
        # appear anywhere in the methylation panel after the fix.
        leaked = self._BANNED_PMID in set(_all_pmids(panel_data))
        assert not leaked, (
            f"unrelated PMID {self._BANNED_PMID} still present somewhere in methylation panel"
        )

    def test_mtrr_evidence_level_stays_supportive(self, panel_data: dict) -> None:
        # MTRR A66G alone is a weak/inconsistent homocysteine determinant; the
        # row stays evidence_level 1 (supportive, no Elevated) and is framed as
        # relevant in combination with MTHFR — matching the cited evidence.
        mtrr = self._get_mtrr(panel_data)
        assert mtrr["evidence_level"] == 1
        categories = {e["category"] for e in mtrr["genotype_effects"].values()}
        assert "Elevated" not in categories


# ── DHFR citation provenance (issue #261) ────────────────────────────────


class TestDHFRCitationProvenance:
    """Validate DHFR rs70991108 19bp del cites on-topic folate evidence.

    The DHFR 19bp-deletion row previously cited PMID 18175331 (a 1800 MHz GSM
    mouse-brain transcription paper) and PMID 20162554 (an IL-10-secreting Treg
    immunology paper), neither of which supports DHFR / rs70991108 / folic-acid
    metabolism. They were replaced with DHFR 19bp-deletion references reflecting
    the genuinely mixed evidence base. Pin the row so the off-topic PMIDs cannot
    silently reappear.
    """

    # Verified DHFR 19bp del/ins (rs70991108) references (NCBI + Consensus):
    _DHFR_PMIDS = frozenset(
        {
            "19022952",  # Kalmbach 2008, J Nutr - del/del raises unmetabolized folic acid
            "26269242",  # Ozaki 2015, J Nutr - largest cohort, null association (mixed evidence)
        }
    )
    # Off-topic PMIDs that were exclusive to the DHFR row -> safe to ban panel-wide.
    _DHFR_EXCLUSIVE_BANNED = frozenset({"20162554"})
    # Off-topic for DHFR, but BHMT/BHMT2 rows also cite 18175331 -> ban from the DHFR row only.
    _DHFR_ROW_BANNED = frozenset({"18175331", "20162554"})

    def _get_dhfr(self, panel_data: dict) -> dict:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == "rs70991108":
                    return snp
        pytest.fail("DHFR rs70991108 not found in panel")

    def test_dhfr_cites_verified_folate_refs(self, panel_data: dict) -> None:
        assert set(self._get_dhfr(panel_data)["pmids"]) == self._DHFR_PMIDS

    def test_dhfr_row_drops_unrelated_pmids(self, panel_data: dict) -> None:
        leaked = self._DHFR_ROW_BANNED & set(self._get_dhfr(panel_data)["pmids"])
        assert not leaked, f"DHFR row still cites unrelated PMID(s) {sorted(leaked)}"

    def test_dhfr_exclusive_banned_pmid_absent_from_panel(self, panel_data: dict) -> None:
        # 20162554 was exclusive to the DHFR row -> must not appear anywhere in
        # the panel. (18175331 is NOT asserted panel-wide: BHMT/BHMT2 rows still
        # carry it as a separate concern tracked by #314.)
        leaked = self._DHFR_EXCLUSIVE_BANNED & set(_all_pmids(panel_data))
        assert not leaked, f"DHFR-exclusive unrelated PMID(s) still in panel: {sorted(leaked)}"

    def test_dhfr_evidence_level_stays_supportive(self, panel_data: dict) -> None:
        # DHFR 19bp del evidence is mixed (Kalmbach functional vs Ozaki null), so
        # the row stays evidence_level 1 (supportive, no Elevated) with cautious
        # "may" wording.
        dhfr = self._get_dhfr(panel_data)
        assert dhfr["evidence_level"] == 1
        categories = {e["category"] for e in dhfr["genotype_effects"].values()}
        assert "Elevated" not in categories


# ── Glutathione/antioxidant citation provenance (issue #314, pathway-group slice)


class TestGlutathioneAntioxidantCitationProvenance:
    """Validate the glutathione/antioxidant rows cite on-topic evidence (#314).

    The #314 audit found these four rows cited zero on-topic papers (e.g. GCLC &
    GCLM both cited 10662760 "Who owns your DNA?"; SOD2 cited a cerebral-ischemia
    NOS-2 paper; GPX1 cited a sphingosine-1-phosphate paper). Each row's PMIDs were
    replaced with gene/variant-specific references — every title verified via NCBI
    esummary, the functional direction verified with the Consensus connector — and
    pinned here so the off-topic PMIDs cannot silently reappear. This is the
    glutathione/antioxidant pathway-group slice of the panel-wide #314 remediation.
    """

    # Verified, on-topic citations per row (NCBI esummary + Consensus):
    _ROW_PMIDS = {
        # SOD2 rs4880 Val16Ala — Sutton import functional x2 + Bresciani review
        "rs4880": {"12618592", "15864132", "23952573"},
        # GPX1 rs1050450 Pro198Leu — Ravn-Haren + Jablonska activity/Se + Zhao review
        "rs1050450": {"16287877", "19415410", "35626163"},
        # GCLC -129C>T rs17883901 — Koide functional/MI + Azarova (names rs17883901)
        "rs17883901": {"12598062", "32715377"},
        # GCLM -588C>T rs41303970 — Nakamura functional/MI + Azarova (names rs41303970)
        "rs41303970": {"12081989", "32715377"},
    }

    # Off-topic PMIDs that were EXCLUSIVE to these four rows -> after the fix they
    # must not appear anywhere in the methylation panel.
    _EXCLUSIVE_BANNED = frozenset(
        {
            "10662760",  # "Who owns your DNA?" (was on GCLC + GCLM)
            "15277203",  # aorta SOD gene transfer / endotoxin (was on GCLC)
            "16217669",  # antimalarial methylene blue (was on GCLM)
            "15504747",  # sphingosine-1-phosphate cardiovascular (was on GPX1)
            "17709398",  # Swi/Snf histone eviction (was on GPX1)
            "10085127",  # cerebral-ischemia NOS-2 promoter (was on SOD2)
        }
    )
    # 15509580 (celastrols / heat-shock) was off-topic on SOD2 but the VDR
    # rs1544410 row still cites it (tracked by #314) -> ban from the SOD2 row only.
    _SOD2_ROW_BANNED = frozenset({"15509580"})

    def _get(self, panel_data: dict, rsid: str) -> dict:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == rsid:
                    return snp
        pytest.fail(f"{rsid} not found in methylation panel")

    def test_rows_cite_verified_refs(self, panel_data: dict) -> None:
        for rsid, expected in self._ROW_PMIDS.items():
            assert set(self._get(panel_data, rsid)["pmids"]) == expected, (
                f"{rsid} does not cite exactly its verified reference set"
            )

    def test_sod2_row_drops_shared_off_topic_pmid(self, panel_data: dict) -> None:
        leaked = self._SOD2_ROW_BANNED & set(self._get(panel_data, "rs4880")["pmids"])
        assert not leaked, f"SOD2 row still cites off-topic PMID(s) {sorted(leaked)}"

    def test_exclusive_off_topic_pmids_absent_from_panel(self, panel_data: dict) -> None:
        leaked = self._EXCLUSIVE_BANNED & set(_all_pmids(panel_data))
        assert not leaked, (
            f"off-topic PMID(s) exclusive to the glutathione rows still in panel: {sorted(leaked)}"
        )


# ── Panel-wide citation remediation (issue #314, remaining rows) ──────


class TestMethylationCitationRemediation:
    """Pin the remaining ~25 rows remediated in #314 to verified, on-topic citations.

    The #314 audit found ~30 of 35 methylation rows cited unrelated placeholder
    PMIDs (e.g. AHCY cited a malignant-obstructive-jaundice paper; FMO3 a Lassa-virus
    epitope paper). The glutathione/antioxidant slice (GCLC, GCLM, GPX1, SOD2) is
    locked above; this completes the panel. Every row below was re-cited with
    gene/variant-specific one-carbon / folate / transsulfuration / BH4 /
    choline-betaine / vitamin-D references — each PMID title verified via NCBI
    esummary and the association verified with the Consensus connector. rsIDs with
    little/no PubMed footprint (MTHFS rs6495446, MAT1A rs10887718, AHCY rs819147,
    GSS rs3761144, QDPR rs1677693, BHMT2 rs585800) cite the gene's
    functional/discovery papers rather than a fabricated variant-specific one. The
    TCN2 row also drops the dead PMID 19187342 (#417).
    """

    # rsid -> exact verified on-topic PMID set the row must cite:
    _REMEDIATED: dict[str, set[str]] = {
        "rs1051266": {"33935279", "16750224", "24597986"},  # SLC19A1 RFC1 G80A
        "rs202676": {"22918695", "30120883"},  # FOLH1 folate hydrolase
        "rs3758149": {"31739835", "14597182", "15564880"},  # GGH -401C>T
        "rs1979277": {"16137637", "17446168", "11386852"},  # SHMT1 L474F
        "rs2236225": {"18767138", "12384833", "24977710"},  # MTHFD1 R653Q
        "rs6495446": {"16365037", "22303332", "30031689"},  # MTHFS (gene-level)
        "rs1801198": {
            "28814397",
            "20808328",
            "12911562",
        },  # TCN2 776C>G; drops dead 19187342 (#417)
        "rs1805087": {"32722923", "19826453", "30559146"},  # MTR A2756G
        "rs10887718": {
            "20335551",
            "21185701",
            "22807109",
        },  # MAT1A (gene-level; rsID has 0 PubMed hits)
        "rs819147": {"19619139", "15241484"},  # AHCY (gene-level; rsID has 0 PubMed hits)
        "rs2228611": {"28473984", "33854407", "37833704"},  # DNMT1
        "rs2424913": {"21854760", "36980848", "27789275"},  # DNMT3B -149C>T
        "rs1021737": {"15151507", "18476726", "19428278"},  # CTH S403I
        "rs3761144": {"33888803", "15717202"},  # GSS (gene-level)
        "rs8007267": {"24136375", "18598896"},  # GCH1
        "rs1677693": {"20615890", "16917893"},  # QDPR (gene-level)
        "rs2228570": {"9797477", "17274004", "15899948"},  # VDR FokI
        "rs1544410": {"23134477", "33238893"},  # VDR BsmI
        "rs3733890": {"18457970", "27578989", "12818402"},  # BHMT R239Q
        "rs585800": {"18457970", "20662904", "15887275"},  # BHMT2 (gene-level)
        "rs12325817": {"16816108", "20861172", "21059658"},  # PEMT
        "rs9001": {"16816108", "28134761", "24671709"},  # CHDH A119S
        "rs3199966": {"24671709", "28134761", "22483272"},  # SLC44A1
        "rs7639752": {"30055775", "24671709", "28134761"},  # PCYT1A
        "rs2266782": {"10640514", "31317802", "12052141"},  # FMO3 E158K
    }

    # Unrelated/placeholder PMIDs removed by this remediation; none may reappear
    # anywhere in the methylation panel.
    _BANNED: frozenset[str] = frozenset(
        {
            "10666248",
            "11595027",
            "11745004",
            "12161596",
            "12815591",
            "15289165",
            "15477547",
            "15509580",
            "15637710",
            "15701835",
            "15950375",
            "16159893",
            "16200083",
            "16207938",
            "16234067",
            "16962000",
            "17190769",
            "17445041",
            "17522615",
            "18175331",
            "18404103",
            "19064519",
            "19187342",
            "19190234",
            "20299362",
            "20860029",
            "21212450",
            "21399649",
            "21680034",
            "22012967",
        }
    )

    def test_each_remediated_row_cites_verified_refs(self, panel_data: dict) -> None:
        by_rsid = {
            snp["rsid"]: snp for pathway in panel_data["pathways"] for snp in pathway["snps"]
        }
        for rsid, allow in self._REMEDIATED.items():
            assert rsid in by_rsid, f"{rsid} missing from methylation panel"
            assert set(by_rsid[rsid]["pmids"]) == allow, (
                f"{rsid} ({by_rsid[rsid]['gene']}) cites {by_rsid[rsid]['pmids']}, "
                f"expected {sorted(allow)}"
            )

    def test_unrelated_pmids_absent_from_panel(self, panel_data: dict) -> None:
        leaked = self._BANNED & set(_all_pmids(panel_data))
        assert not leaked, (
            f"unrelated PMID(s) {sorted(leaked)} still present in the methylation panel"
        )
