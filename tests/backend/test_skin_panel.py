"""Tests for the curated Gene Skin SNP panel (P3-54).

Covers:
  - Panel JSON loading and structural validation
  - All 10 curated SNPs present with correct genes
  - 4 pathway cards (Pigmentation & UV Response, Skin Barrier & Inflammation,
    Oxidative Stress & Aging, Skin Micronutrients)
  - MC1R multi-allele haplotype-aware calling metadata (R/r allele classes)
  - FLG R501X limited-coverage note and Insufficient Data flag
  - Genotype effects categories are valid (Elevated/Moderate/Standard)
  - Evidence levels within expected range
  - Scoring rules match project conventions
  - GWAS EFO skin/pigmentation terms included
  - Cross-module links (Cancer, Nutrigenomics, Allergy)
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
    / "skin_panel.json"
)
CANCER_PRS_WEIGHTS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "cancer_prs_weights.json"
)

VALID_CATEGORIES = {"Elevated", "Moderate", "Standard"}

EXPECTED_RSIDS = {
    "rs1805007",  # MC1R R151C
    "rs1805008",  # MC1R R160W
    "rs1805009",  # MC1R D294H
    "rs885479",  # MC1R R163Q
    "rs61816761",  # FLG R501X
    "rs1695",  # GSTP1 Ile105Val
    "rs1799750",  # MMP1 1G/2G
    "rs4880",  # SOD2 Val16Ala
    "rs2228570",  # VDR FokI
    "rs1544410",  # VDR BsmI
}

EXPECTED_PATHWAYS = {
    "pigmentation_uv",
    "skin_barrier_inflammation",
    "oxidative_stress_aging",
    "skin_micronutrients",
}

EXPECTED_GENES = {"MC1R", "FLG", "GSTP1", "MMP1", "SOD2", "VDR"}


@pytest.fixture()
def panel_data() -> dict:
    """Load the raw panel JSON."""
    with open(PANEL_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture()
def cancer_prs_weights() -> dict:
    """Load the bundled cancer PRS weights JSON."""
    with open(CANCER_PRS_WEIGHTS_PATH, encoding="utf-8") as f:
        return json.load(f)


# ── Panel structure tests ────────────────────────────────────────────────


class TestPanelStructure:
    def test_panel_file_exists(self) -> None:
        assert PANEL_PATH.exists(), f"Panel file not found: {PANEL_PATH}"

    def test_panel_is_valid_json(self, panel_data: dict) -> None:
        assert isinstance(panel_data, dict)

    def test_panel_module_name(self, panel_data: dict) -> None:
        assert panel_data["module"] == "skin"

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
        assert "Pigmentation & UV Response" in pathway_names
        assert "Skin Barrier & Inflammation" in pathway_names
        assert "Oxidative Stress & Aging" in pathway_names
        assert "Skin Micronutrients" in pathway_names


# ── SNP coverage tests ──────────────────────────────────────────────────


class TestSNPCoverage:
    def test_all_expected_rsids_present(self, panel_data: dict) -> None:
        """All 10 curated SNPs from the PRD must be present."""
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
        """10 curated SNPs total across all pathways."""
        count = sum(len(p["snps"]) for p in panel_data["pathways"])
        assert count == 10


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


# ── MC1R multi-allele calling tests ─────────────────────────────────────


class TestMC1RMultiAllele:
    """T3-52 precursor: validate MC1R multi-allele calling metadata."""

    MC1R_RSIDS = {"rs1805007", "rs1805008", "rs1805009", "rs885479"}
    R_ALLELES = {"rs1805007", "rs1805008", "rs1805009"}
    r_ALLELES = {"rs885479"}

    def _get_mc1r_snps(self, panel_data: dict) -> list[dict]:
        snps = []
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["gene"] == "MC1R":
                    snps.append(snp)
        return snps

    def test_all_four_mc1r_variants_present(self, panel_data: dict) -> None:
        mc1r_rsids = {s["rsid"] for s in self._get_mc1r_snps(panel_data)}
        assert mc1r_rsids == self.MC1R_RSIDS

    def test_mc1r_rows_cite_verified_melanoma_pmids(self, panel_data: dict) -> None:
        # Every MC1R pigmentation/melanoma row (R151C, R160W, D294H, R163Q) must
        # cite MC1R-specific evidence (#359):
        #   18366057 — Raimondi 2008, Int J Cancer — meta-analysis of MC1R variants
        #              (incl. R151C/R160W/R163Q/D294H), melanoma + red-hair phenotype
        #   21128237 — Williams 2011, Int J Cancer — MC1R + cutaneous-melanoma
        #              meta-analysis (population burden)
        # This replaces four globally off-topic PMIDs (IL-1β pain / European
        # population substructure / mouse colonic epigenetics / FBN1 dysplasia) and
        # an ASIP/TYR — not MC1R — pigmentation paper (18488027).
        verified = {"18366057", "21128237"}
        misattributed = {"11260714", "17044734", "20197410", "21683322", "18488027"}
        for snp in self._get_mc1r_snps(panel_data):
            pmids = set(snp["pmids"])
            assert pmids == verified, (
                f"{snp['rsid']} MC1R pmids {sorted(pmids)} != {sorted(verified)}"
            )
            assert not (pmids & misattributed), (
                f"{snp['rsid']} still cites misattributed PMID(s): {sorted(pmids & misattributed)}"
            )

    def test_d294h_uses_c_allele_consistent_with_melanoma_prs(
        self,
        panel_data: dict,
        cancer_prs_weights: dict,
    ) -> None:
        """MC1R D294H is the C-bearing rs1805009 allele, not G>A (issue #148)."""
        d294h = next(s for s in self._get_mc1r_snps(panel_data) if s["rsid"] == "rs1805009")
        assert d294h["ref_allele"] == "G"
        assert d294h["risk_allele"] == "C"
        assert set(d294h["genotype_effects"]) == {"GG", "GC", "CG", "CC"}

        melanoma = next(
            score for score in cancer_prs_weights["weight_sets"] if score["trait"] == "melanoma"
        )
        prs_weight = next(
            weight for weight in melanoma["weights"] if weight["rsid"] == "rs1805009"
        )
        assert prs_weight["effect_allele"] == d294h["risk_allele"]

    def test_mc1r_allele_class_annotations(self, panel_data: dict) -> None:
        """R alleles: R151C, R160W, D294H. r allele: R163Q."""
        for snp in self._get_mc1r_snps(panel_data):
            assert "mc1r_allele_class" in snp, f"{snp['rsid']} missing mc1r_allele_class"
            if snp["rsid"] in self.R_ALLELES:
                assert snp["mc1r_allele_class"] == "R"
            elif snp["rsid"] in self.r_ALLELES:
                assert snp["mc1r_allele_class"] == "r"

    def test_mc1r_r_alleles_evidence_level_3(self, panel_data: dict) -> None:
        """Strong R alleles (R151C, R160W, D294H) are well-replicated."""
        for snp in self._get_mc1r_snps(panel_data):
            if snp["rsid"] in self.R_ALLELES:
                assert snp["evidence_level"] == 3, f"{snp['rsid']} should have evidence_level 3"

    def test_mc1r_r163q_evidence_level_2(self, panel_data: dict) -> None:
        """Mild r allele (R163Q) has lower evidence."""
        for snp in self._get_mc1r_snps(panel_data):
            if snp["rsid"] == "rs885479":
                assert snp["evidence_level"] == 2

    def test_mc1r_r_alleles_have_elevated_homozygous(self, panel_data: dict) -> None:
        """Strong R alleles homozygous → Elevated."""
        for snp in self._get_mc1r_snps(panel_data):
            if snp["rsid"] in self.R_ALLELES:
                risk_hom = snp["risk_allele"] * 2
                assert snp["genotype_effects"].get(risk_hom, {}).get("category") == "Elevated", (
                    f"{snp['rsid']} should have Elevated for homozygous risk"
                )

    def test_mc1r_r163q_capped_at_moderate(self, panel_data: dict) -> None:
        """R163Q (mild r allele) homozygous caps at Moderate, not Elevated."""
        for snp in self._get_mc1r_snps(panel_data):
            if snp["rsid"] == "rs885479":
                for gt, effect in snp["genotype_effects"].items():
                    assert effect["category"] != "Elevated", (
                        f"R163Q {gt} should not be Elevated (mild r allele)"
                    )

    def test_mc1r_special_calling_section(self, panel_data: dict) -> None:
        assert "special_calling" in panel_data
        assert "MC1R_multi_allele" in panel_data["special_calling"]
        sc = panel_data["special_calling"]["MC1R_multi_allele"]
        assert set(sc["rsids"]) == self.MC1R_RSIDS

    def test_mc1r_allele_class_map_in_special_calling(self, panel_data: dict) -> None:
        sc = panel_data["special_calling"]["MC1R_multi_allele"]
        assert "allele_classes" in sc
        for rsid in self.R_ALLELES:
            assert sc["allele_classes"][rsid] == "R"
        assert sc["allele_classes"]["rs885479"] == "r"

    def test_mc1r_risk_states(self, panel_data: dict) -> None:
        sc = panel_data["special_calling"]["MC1R_multi_allele"]
        assert "risk_states" in sc
        assert "0_R_alleles" in sc["risk_states"]
        assert "mild_r_allele" in sc["risk_states"]
        assert "1_R_allele" in sc["risk_states"]
        assert "2_R_alleles" in sc["risk_states"]
        assert "baseline" not in sc["risk_states"]["mild_r_allele"]["description"].lower()

    def test_mc1r_cancer_cross_links(self, panel_data: dict) -> None:
        """MC1R R alleles should cross-link to Cancer module (melanoma)."""
        for snp in self._get_mc1r_snps(panel_data):
            if snp["rsid"] in self.R_ALLELES:
                assert "cross_module" in snp, f"{snp['rsid']} missing cancer cross_module"
                assert snp["cross_module"]["module"] == "cancer"
                assert "melanoma" in snp["cross_module"]["note"].lower()


# ── FLG R501X limited-coverage tests ────────────────────────────────────


class TestFLGLimitedCoverage:
    """T3-53 precursor: validate FLG R501X limited-coverage metadata."""

    def _get_flg(self, panel_data: dict) -> dict:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == "rs61816761":
                    return snp
        pytest.fail("FLG rs61816761 not found in panel")

    def test_flg_has_coverage_note(self, panel_data: dict) -> None:
        flg = self._get_flg(panel_data)
        assert "coverage_note" in flg
        assert "R501X" in flg["coverage_note"]
        assert "2282del4" in flg["coverage_note"]

    def test_flg_insufficient_data_flag(self, panel_data: dict) -> None:
        """FLG R501X must be flagged as Insufficient Data."""
        flg = self._get_flg(panel_data)
        assert flg.get("insufficient_data_flag") is True

    def test_flg_variant_identity(self, panel_data: dict) -> None:
        flg = self._get_flg(panel_data)
        assert flg["variant_name"] == "R501X"
        assert flg["hgvs_protein"] == "p.Arg501Ter"

    def test_flg_in_skin_barrier_pathway(self, panel_data: dict) -> None:
        for pathway in panel_data["pathways"]:
            if pathway["id"] == "skin_barrier_inflammation":
                rsids = {s["rsid"] for s in pathway["snps"]}
                assert "rs61816761" in rsids
                return
        pytest.fail("skin_barrier_inflammation pathway not found")

    def test_flg_evidence_level(self, panel_data: dict) -> None:
        flg = self._get_flg(panel_data)
        assert flg["evidence_level"] == 2  # Well-replicated

    def test_flg_special_calling_section(self, panel_data: dict) -> None:
        assert "FLG_R501X_limited_coverage" in panel_data["special_calling"]
        sc = panel_data["special_calling"]["FLG_R501X_limited_coverage"]
        assert sc["rsid"] == "rs61816761"
        assert sc["observed_variant"] == "FLG R501X (p.Arg501Ter)"
        assert "insufficient_data_reason" in sc
        assert "2282del4" in sc["insufficient_data_reason"]

    def test_flg_allergy_cross_link(self, panel_data: dict) -> None:
        """FLG should cross-link to Allergy module (atopic march)."""
        flg = self._get_flg(panel_data)
        assert "cross_module" in flg
        assert flg["cross_module"]["module"] == "allergy"

    def test_flg_heterozygous_moderate(self, panel_data: dict) -> None:
        flg = self._get_flg(panel_data)
        for gt in ("GA", "AG"):
            assert flg["genotype_effects"][gt]["category"] == "Moderate"

    def test_flg_homozygous_elevated(self, panel_data: dict) -> None:
        flg = self._get_flg(panel_data)
        assert flg["genotype_effects"]["AA"]["category"] == "Elevated"

    def test_flg_cites_filaggrin_pmids(self, panel_data: dict) -> None:
        # FLG R501X citations must be filaggrin loss-of-function evidence (#189):
        #   16550169 — Palmer 2006 (FLG LOF → atopic dermatitis, Nat Genet)
        #   16444271 — Smith 2006 (FLG LOF → ichthyosis vulgaris, Nat Genet)
        #   16815158 — Weidinger 2006 (FLG LOF → AD + allergic sensitization, JACI)
        flg = self._get_flg(panel_data)
        assert flg["pmids"] == ["16550169", "16444271", "16815158"], flg["pmids"]

    def test_no_unrelated_pmids_in_panel(self, panel_data: dict) -> None:
        # Guard against misattributed citations that must not reappear anywhere
        # in the skin panel. From the FLG row (#189): 17597076 (protein-Neddylation)
        # and 21714652 (myelodysplastic-syndrome review). From the MC1R rows (#359),
        # four globally off-topic PMIDs: 11260714 (IL-1β/Cox-2 inflammatory pain),
        # 17044734 (European population substructure), 20197410 (mouse colonic
        # epigenetics), 21683322 (FBN1 acromicric/geleophysic dysplasia). None
        # concerns skin/MC1R biology. From the SOD2 row (#390): 10071056 (MPZ /
        # Charcot-Marie-Tooth), 18466508 (family-based association methods),
        # 23090862 (photodissociable-ligand chemistry) — none concerns SOD2/MnSOD.
        # From the VDR FokI/BsmI rows (#437): 12773612 (veterans compensation care),
        # 21575918 (SIRT1/body-fat/BP), 26199118 (anti-tubercular nitroimidazooxazines),
        # 23796876 (Withania somnifera / seminal plasma) — none concerns VDR/psoriasis.
        banned = {
            "17597076",
            "21714652",
            "11260714",
            "17044734",
            "20197410",
            "21683322",
            "10071056",
            "18466508",
            "23090862",
            "12773612",
            "21575918",
            "26199118",
            "23796876",
        }
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                offending = banned & set(snp["pmids"])
                assert not offending, f"{snp['rsid']} cites unrelated PMID(s): {offending}"

    def test_sod2_cites_verified_mnsod_pmids(self, panel_data: dict) -> None:
        # SOD2 rs4880 (Val16Ala) must cite MnSOD Val16Ala functional evidence (#390):
        #   15864132 — Sutton 2005 (Ala16Val modulates MnSOD mitochondrial import + mRNA)
        #   12618592 — Sutton 2003 (Ala16Val modulates MnSOD import into mitochondria)
        #   23952573 — Bresciani 2013 (MnSOD Ala16Val SNP review, human disease)
        sod2 = next(s for p in panel_data["pathways"] for s in p["snps"] if s["rsid"] == "rs4880")
        assert sod2["gene"] == "SOD2"
        assert sod2["pmids"] == ["15864132", "12618592", "23952573"], sod2["pmids"]

    def test_vdr_rows_verified_and_not_overstated(self, panel_data: dict) -> None:
        # VDR FokI (rs2228570) / BsmI (rs1544410): the cited PMIDs must be VDR/psoriasis
        # (or functional-VDR) evidence, and the AA "Elevated psoriasis" call is
        # overstated — VDR FokI/BsmI psoriasis associations are mixed/null (Lee 2018
        # PMID 30474246 = no FokI/BsmI association; Lee 2012 PMID 22290287 = mixed).
        # So AA is downgraded Elevated→Moderate and the row asserts no firm psoriasis
        # risk (#437).
        expected = {
            "rs2228570": ["9169350", "30474246", "22290287"],  # Arai functional + Lee 2018/2012
            "rs1544410": ["30474246", "22290287"],  # Lee 2018/2012 psoriasis meta-analyses
        }
        vdr = {
            s["rsid"]: s for p in panel_data["pathways"] for s in p["snps"] if s["gene"] == "VDR"
        }
        for rsid, pmids in expected.items():
            row = vdr[rsid]
            assert row["pmids"] == pmids, f"{rsid} pmids {row['pmids']} != {pmids}"
            cats = {e["category"] for e in row["genotype_effects"].values()}
            assert "Elevated" not in cats, f"{rsid} still has an Elevated genotype (overstated)"
            aa = row["genotype_effects"]["AA"]["effect_summary"].lower()
            assert "no confident skin-risk call" in aa, f"{rsid} AA must hedge psoriasis"
            assert "increased susceptibility to psoriasis" not in aa
            assert "increased psoriasis susceptibility" not in aa


# ── VDR cross-module tests ─────────────────────────────────────────────


class TestVDRCrossModule:
    """VDR variants should cross-link to Nutrigenomics module."""

    VDR_RSIDS = {"rs2228570", "rs1544410"}

    def _get_vdr_snps(self, panel_data: dict) -> list[dict]:
        snps = []
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["gene"] == "VDR":
                    snps.append(snp)
        return snps

    def test_two_vdr_variants_present(self, panel_data: dict) -> None:
        vdr_rsids = {s["rsid"] for s in self._get_vdr_snps(panel_data)}
        assert vdr_rsids == self.VDR_RSIDS

    def test_vdr_nutrigenomics_cross_link(self, panel_data: dict) -> None:
        for snp in self._get_vdr_snps(panel_data):
            assert "cross_module" in snp, f"{snp['rsid']} missing nutrigenomics cross_module"
            assert snp["cross_module"]["module"] == "nutrigenomics"

    def test_vdr_in_skin_micronutrients_pathway(self, panel_data: dict) -> None:
        for pathway in panel_data["pathways"]:
            if pathway["id"] == "skin_micronutrients":
                rsids = {s["rsid"] for s in pathway["snps"]}
                assert self.VDR_RSIDS.issubset(rsids)
                return
        pytest.fail("skin_micronutrients pathway not found")


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

    def test_mc1r_aggregate_rule_documented(self, panel_data: dict) -> None:
        """MC1R-specific aggregate rule must be in scoring_rules."""
        assert "mc1r_aggregate_rule" in panel_data["scoring_rules"]
        rule = panel_data["scoring_rules"]["mc1r_aggregate_rule"]
        assert "R allele" in rule


# ── GWAS EFO terms tests ────────────────────────────────────────────────


class TestGWASEFOTerms:
    def test_gwas_efo_terms_present(self, panel_data: dict) -> None:
        assert "gwas_efo_terms" in panel_data
        terms = panel_data["gwas_efo_terms"]
        assert isinstance(terms, list)
        assert len(terms) > 0

    def test_key_skin_efo_terms_included(self, panel_data: dict) -> None:
        terms = set(panel_data["gwas_efo_terms"])
        assert "skin" in terms
        assert "pigmentation" in terms
        assert "melanoma" in terms
        assert "freckling" in terms
        assert "sun sensitivity" in terms
        assert "psoriasis" in terms
        assert "eczema" in terms
        assert "dermatitis" in terms
        assert "collagen" in terms
        assert "vitiligo" in terms

    def test_gwas_efo_terms_match_gwas_loader(self, panel_data: dict) -> None:
        """Panel EFO terms should match the _SKIN_TERMS in gwas.py."""
        from backend.annotation.gwas import _SKIN_TERMS

        panel_terms = frozenset(panel_data["gwas_efo_terms"])
        assert panel_terms == _SKIN_TERMS


# ── Pathway-specific SNP allocation tests ────────────────────────────────


class TestPathwayAllocation:
    def _get_pathway(self, panel_data: dict, pathway_id: str) -> dict:
        for p in panel_data["pathways"]:
            if p["id"] == pathway_id:
                return p
        pytest.fail(f"Pathway {pathway_id} not found")

    def test_pigmentation_uv_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "pigmentation_uv")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs1805007" in rsids  # MC1R R151C
        assert "rs1805008" in rsids  # MC1R R160W
        assert "rs1805009" in rsids  # MC1R D294H
        assert "rs885479" in rsids  # MC1R R163Q

    def test_skin_barrier_inflammation_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "skin_barrier_inflammation")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs61816761" in rsids  # FLG R501X

    def test_oxidative_stress_aging_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "oxidative_stress_aging")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs1695" in rsids  # GSTP1
        assert "rs1799750" in rsids  # MMP1
        assert "rs4880" in rsids  # SOD2

    def test_skin_micronutrients_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "skin_micronutrients")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs2228570" in rsids  # VDR FokI
        assert "rs1544410" in rsids  # VDR BsmI
