"""Tests for the curated Gene Sleep SNP panel (P3-48/P3-49).

Covers:
  - Panel JSON loading and structural validation
  - All 5 curated SNPs present with correct genes
  - 3 pathway cards (Caffeine & Sleep, Sleep Quality, Sleep Disorders);
    the PER3 chronotype_circadian pathway was removed (#615, dead VNTR marker)
  - CYP1A2 metabolizer status special calling (rapid/intermediate/slow)
  - rs2858884 HLA-DQ region marker (informational, not a DQB1*06:02 proxy)
  - Genotype effects categories are valid (Elevated/Moderate/Standard)
  - Evidence levels within expected range
  - CYP1A2 cross-module reference to Pharmacogenomics
  - Scoring rules match project conventions
  - GWAS EFO sleep/circadian terms included
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
    / "sleep_panel.json"
)

VALID_CATEGORIES = {"Elevated", "Moderate", "Standard"}

EXPECTED_RSIDS = {
    "rs762551",  # CYP1A2 caffeine metabolism marker
    "rs5751876",  # ADORA2A
    "rs2300478",  # MEIS1
    "rs9357271",  # BTBD9
    "rs2858884",  # HLA-DQ region marker (not a DQB1*06:02 proxy)
}

# The PER3 chronotype_circadian pathway was removed (#615): its sole marker
# rs57875989 IS the PER3 54-bp VNTR (deprecated/unplaced, not array-typeable) and
# no validated array-typeable tag SNP for the VNTR exists, so it could never fire.
EXPECTED_PATHWAYS = {
    "caffeine_sleep",
    "sleep_quality",
    "sleep_disorders",
}

EXPECTED_GENES = {"CYP1A2", "ADORA2A", "MEIS1", "BTBD9", "HLA-DQB1"}


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
        assert panel_data["module"] == "sleep"

    def test_panel_version(self, panel_data: dict) -> None:
        assert panel_data["version"] == "1.0.0"

    def test_panel_has_description(self, panel_data: dict) -> None:
        assert "description" in panel_data
        assert len(panel_data["description"]) > 0

    def test_panel_has_three_pathways(self, panel_data: dict) -> None:
        # chronotype_circadian removed (#615) — see EXPECTED_PATHWAYS note.
        assert len(panel_data["pathways"]) == 3

    def test_pathway_ids(self, panel_data: dict) -> None:
        pathway_ids = {p["id"] for p in panel_data["pathways"]}
        assert pathway_ids == EXPECTED_PATHWAYS

    def test_pathway_names(self, panel_data: dict) -> None:
        pathway_names = {p["name"] for p in panel_data["pathways"]}
        assert "Caffeine & Sleep" in pathway_names
        assert "Sleep Quality" in pathway_names
        assert "Sleep Disorders" in pathway_names


# ── SNP coverage tests ──────────────────────────────────────────────────


class TestSNPCoverage:
    def test_all_expected_rsids_present(self, panel_data: dict) -> None:
        """All 5 curated SNPs must be present."""
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
        """5 curated SNPs total across all pathways (PER3 rs57875989 removed, #615)."""
        count = sum(len(p["snps"]) for p in panel_data["pathways"])
        assert count == 5


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

    def test_adora2a_cites_caffeine_evidence(self, panel_data: dict) -> None:
        """ADORA2A rs5751876 must cite real ADORA2A/caffeine-sensitivity evidence, not
        the unrelated Salmonella-surveillance (15657627) / invertebrate-methylation
        (22232607) / brain-stimulation (25979839) PMIDs attached in error (gh #187).
        Locked to the verified set:
          - 17329997  Rétey 2007, Clin Pharmacol Ther (ADORA2A & caffeine effects on sleep)
          - 18305461  Childs 2008, Neuropsychopharmacology (ADORA2A & caffeine-induced anxiety)
          - 31817803  Erblang 2019, Genes (ADORA2A & caffeine/sleep)
        """
        adora2a = next(
            s for pw in panel_data["pathways"] for s in pw["snps"] if s["rsid"] == "rs5751876"
        )
        cited = set(adora2a["pmids"])
        unrelated = {"15657627", "22232607", "25979839"}
        assert cited.isdisjoint(unrelated), f"ADORA2A cites unrelated PMIDs {cited & unrelated}"
        assert cited == {"17329997", "18305461", "31817803"}, f"unexpected ADORA2A PMIDs: {cited}"

    def test_cyp1a2_cites_caffeine_metabolism_evidence(self, panel_data: dict) -> None:
        """CYP1A2 rs762551 must cite real CYP1A2/caffeine/sleep evidence, not the
        unrelated p53 structural-biology paper (18391200) attached in error (gh
        #190). Locked to the verified set:
          - 16522833  Cornelis 2006, JAMA (coffee, CYP1A2 genotype)
          - 26378246  Burke 2015, Sci Transl Med (caffeine & the human circadian clock)
          - 37029915  Kapellou 2023, Nutr Rev (caffeine genetics & brain outcomes incl. sleep)
          - 29282363  Koonrungsesomboon 2018, Pharmacogenomics J (CYP1A2 activity meta-analysis)
          - 41992662  Monostory 2026, Clin Pharmacol Ther (PharmVar CYP1A2 GeneFocus)
        """
        cyp = next(
            s for pw in panel_data["pathways"] for s in pw["snps"] if s["rsid"] == "rs762551"
        )
        cited = set(cyp["pmids"])
        assert "18391200" not in cited, "CYP1A2 still cites the unrelated p53 PMID 18391200"
        assert cited == {"16522833", "26378246", "37029915", "29282363", "41992662"}, (
            f"unexpected CYP1A2 PMIDs: {cited}"
        )


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


# ── CYP1A2 metabolizer status tests ─────────────────────────────────────


class TestCYP1A2Metabolizer:
    """Validate CYP1A2 caffeine metabolizer special calling metadata."""

    def _get_cyp1a2(self, panel_data: dict) -> dict:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == "rs762551":
                    return snp
        pytest.fail("CYP1A2 rs762551 not found in panel")

    def test_cyp1a2_aa_standard_rapid(self, panel_data: dict) -> None:
        """AA A-allele homozygote -> Standard category (rapid metabolizer)."""
        cyp = self._get_cyp1a2(panel_data)
        effect = cyp["genotype_effects"]["AA"]
        assert effect["category"] == "Standard"
        summary = effect["effect_summary"].lower()
        assert "rapid" in summary or "fast" in summary
        assert "rs762551 aa" in summary

    def test_cyp1a2_ac_moderate(self, panel_data: dict) -> None:
        """AC heterozygote -> Moderate category (intermediate)."""
        cyp = self._get_cyp1a2(panel_data)
        effect = cyp["genotype_effects"]["AC"]
        assert effect["category"] == "Moderate"
        summary = effect["effect_summary"].lower()
        assert "intermediate" in summary
        assert "rs762551 ac" in summary

    def test_cyp1a2_ca_moderate(self, panel_data: dict) -> None:
        """CA heterozygote -> Moderate category, same as AC."""
        cyp = self._get_cyp1a2(panel_data)
        effect = cyp["genotype_effects"]["CA"]
        assert effect["category"] == "Moderate"
        summary = effect["effect_summary"].lower()
        assert "intermediate" in summary
        assert "rs762551 ca" in summary

    def test_cyp1a2_cc_elevated_slow(self, panel_data: dict) -> None:
        """CC C-allele homozygote -> Elevated category (slow metabolizer)."""
        cyp = self._get_cyp1a2(panel_data)
        effect = cyp["genotype_effects"]["CC"]
        assert effect["category"] == "Elevated"
        summary = effect["effect_summary"].lower()
        assert "slow" in summary
        assert "rs762551 cc" in summary

    def test_cyp1a2_evidence_level(self, panel_data: dict) -> None:
        cyp = self._get_cyp1a2(panel_data)
        assert cyp["evidence_level"] == 2  # Well-replicated

    def test_cyp1a2_has_cross_module(self, panel_data: dict) -> None:
        """CYP1A2 must reference Pharmacogenomics cross-module."""
        cyp = self._get_cyp1a2(panel_data)
        assert "cross_module" in cyp
        assert cyp["cross_module"]["module"] == "pharmacogenomics"

    def test_cyp1a2_metadata_does_not_emit_star_diplotypes(self, panel_data: dict) -> None:
        cyp = self._get_cyp1a2(panel_data)
        sc = panel_data["special_calling"]["CYP1A2_metabolizer"]
        # Serialize the nested metadata so the guard covers every user-facing string.
        metadata = json.dumps({"snp": cyp, "special_calling": sc})
        assert "*1A/*1A" not in metadata
        assert "*1A/*1F" not in metadata
        assert "*1F/*1F" not in metadata
        assert cyp["variant_name"] == "-163C>A (rs762551; caffeine clearance)"
        assert {"29282363", "41992662"}.issubset(cyp["pmids"])
        assert "CYP1A2*30" in cyp["coverage_note"]
        assert "not a full CYP1A2 star-allele caller" in cyp["coverage_note"]

    def test_cyp1a2_in_special_calling(self, panel_data: dict) -> None:
        assert "special_calling" in panel_data
        assert "CYP1A2_metabolizer" in panel_data["special_calling"]
        sc = panel_data["special_calling"]["CYP1A2_metabolizer"]
        assert sc["rsid"] == "rs762551"
        assert "star-allele call" in sc["description"]
        assert "rapid" in sc["states"]
        assert "intermediate" in sc["states"]
        assert "slow" in sc["states"]
        # Intermediate state documents both heterozygous genotype orientations
        assert set(sc["states"]["intermediate"]["genotypes"]) == {"AC", "CA"}


# ── ADORA2A tests ────────────────────────────────────────────────────────


class TestADORA2A:
    """Validate ADORA2A caffeine sensitivity SNP."""

    def _get_adora2a(self, panel_data: dict) -> dict:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == "rs5751876":
                    return snp
        pytest.fail("ADORA2A rs5751876 not found in panel")

    def test_adora2a_tt_elevated(self, panel_data: dict) -> None:
        """TT → Elevated caffeine sensitivity."""
        snp = self._get_adora2a(panel_data)
        assert snp["genotype_effects"]["TT"]["category"] == "Elevated"

    def test_adora2a_cc_standard(self, panel_data: dict) -> None:
        """CC → Standard caffeine sensitivity."""
        snp = self._get_adora2a(panel_data)
        assert snp["genotype_effects"]["CC"]["category"] == "Standard"

    def test_adora2a_evidence_level(self, panel_data: dict) -> None:
        snp = self._get_adora2a(panel_data)
        assert snp["evidence_level"] == 1


# ── RLS SNPs tests (MEIS1 + BTBD9) ──────────────────────────────────────


class TestRLSSNPs:
    """Validate restless legs syndrome SNPs."""

    def _get_snp(self, panel_data: dict, rsid: str) -> dict:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == rsid:
                    return snp
        pytest.fail(f"{rsid} not found in panel")

    def test_meis1_gg_elevated(self, panel_data: dict) -> None:
        """MEIS1 GG → Elevated RLS risk."""
        snp = self._get_snp(panel_data, "rs2300478")
        assert snp["genotype_effects"]["GG"]["category"] == "Elevated"
        assert "restless legs" in snp["genotype_effects"]["GG"]["effect_summary"].lower()

    def test_meis1_tt_standard(self, panel_data: dict) -> None:
        snp = self._get_snp(panel_data, "rs2300478")
        assert snp["genotype_effects"]["TT"]["category"] == "Standard"

    def test_meis1_evidence_level(self, panel_data: dict) -> None:
        snp = self._get_snp(panel_data, "rs2300478")
        assert snp["evidence_level"] == 2  # Well-replicated GWAS

    def test_btbd9_tt_elevated(self, panel_data: dict) -> None:
        """BTBD9 TT → Elevated PLMS risk."""
        snp = self._get_snp(panel_data, "rs9357271")
        assert snp["genotype_effects"]["TT"]["category"] == "Elevated"

    def test_btbd9_cc_standard(self, panel_data: dict) -> None:
        snp = self._get_snp(panel_data, "rs9357271")
        assert snp["genotype_effects"]["CC"]["category"] == "Standard"

    def test_btbd9_evidence_level(self, panel_data: dict) -> None:
        snp = self._get_snp(panel_data, "rs9357271")
        assert snp["evidence_level"] == 1

    # ── Citation provenance (issue #208) ─────────────────────────────────
    # The MEIS1 (rs2300478) and BTBD9 (rs9357271) rows previously cited
    # unrelated papers: 24839885 (preschool internalizing-problems GWAS) on
    # the MEIS1 row, and 19081515 (variant Creutzfeldt-Jakob disease GWAS) +
    # 23340844 (FOXO3 transcription-regulation) on the BTBD9 row. Pin both
    # rows to verified RLS/PLMS references for those exact loci — two of each
    # row's citations name the panel's own variant — so the off-topic PMIDs
    # cannot silently reappear.
    _MEIS1_PMIDS = {
        "26498236",  # Winkelman 2015, Sleep Med — MrOS: rs2300478[G] (MEIS1) → PLMS
        "26703954",  # Haba-Rubio 2016, Ann Neurol — HypnoLaus: rs2300478 (MEIS1) → PLMS
        "29029846",  # Schormair 2017, Lancet Neurol — MEIS1 confirmed strongest RLS locus
    }
    _BTBD9_PMIDS = {
        "17634447",  # Stefánsson 2007, NEJM — BTBD9 intron variant → PLMS + ferritin
        "26498236",  # Winkelman 2015, Sleep Med — MrOS: rs9357271[T] (BTBD9) → PLMS
        "28329290",  # Gen Li 2017, Sleep — rs9357271 (BTBD9) → RLS (Chinese / Asian meta)
    }
    _BANNED_PMIDS = {"24839885", "19081515", "23340844"}

    def test_meis1_cites_verified_rls_refs(self, panel_data: dict) -> None:
        snp = self._get_snp(panel_data, "rs2300478")
        assert set(snp["pmids"]) == self._MEIS1_PMIDS

    def test_btbd9_cites_verified_rls_refs(self, panel_data: dict) -> None:
        snp = self._get_snp(panel_data, "rs9357271")
        assert set(snp["pmids"]) == self._BTBD9_PMIDS

    def test_rls_rows_drop_unrelated_pmids(self, panel_data: dict) -> None:
        for rsid in ("rs2300478", "rs9357271"):
            snp = self._get_snp(panel_data, rsid)
            leaked = self._BANNED_PMIDS & set(snp["pmids"])
            assert not leaked, f"{rsid} still cites unrelated PMID(s) {sorted(leaked)}"

    def test_rls_unrelated_pmids_absent_from_whole_sleep_panel(self, panel_data: dict) -> None:
        # All three banned PMIDs were exclusive to the MEIS1/BTBD9 rows, so none
        # should appear anywhere in the sleep panel after the fix.
        def _all_pmids(obj: object):
            if isinstance(obj, dict):
                if isinstance(obj.get("pmids"), list):
                    yield from obj["pmids"]
                for value in obj.values():
                    yield from _all_pmids(value)
            elif isinstance(obj, list):
                for item in obj:
                    yield from _all_pmids(item)

        leaked = self._BANNED_PMIDS & set(_all_pmids(panel_data))
        assert not leaked, f"unrelated PMID(s) still in sleep panel: {sorted(leaked)}"


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

    def test_key_sleep_efo_terms_included(self, panel_data: dict) -> None:
        terms = set(panel_data["gwas_efo_terms"])
        assert "sleep" in terms
        assert "insomnia" in terms
        assert "chronotype" in terms
        assert "circadian" in terms
        assert "restless legs" in terms
        assert "sleep duration" in terms
        assert "melatonin" in terms
        assert "narcolepsy" in terms
        assert "morningness" in terms
        assert "eveningness" in terms

    def test_gwas_efo_terms_match_gwas_loader(self, panel_data: dict) -> None:
        """Panel EFO terms should match the _SLEEP_TERMS in gwas.py."""
        from backend.annotation.gwas import _SLEEP_TERMS

        panel_terms = frozenset(panel_data["gwas_efo_terms"])
        assert panel_terms == _SLEEP_TERMS


# ── Pathway-specific SNP allocation tests ────────────────────────────────


class TestPathwayAllocation:
    def _get_pathway(self, panel_data: dict, pathway_id: str) -> dict:
        for p in panel_data["pathways"]:
            if p["id"] == pathway_id:
                return p
        pytest.fail(f"Pathway {pathway_id} not found")

    def test_caffeine_sleep_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "caffeine_sleep")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs762551" in rsids  # CYP1A2
        assert "rs5751876" in rsids  # ADORA2A

    def test_sleep_quality_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "sleep_quality")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs2300478" in rsids  # MEIS1
        assert "rs9357271" in rsids  # BTBD9

    def test_sleep_disorders_snps(self, panel_data: dict) -> None:
        pw = self._get_pathway(panel_data, "sleep_disorders")
        rsids = {s["rsid"] for s in pw["snps"]}
        assert "rs2858884" in rsids  # HLA-DQ region marker (not a DQB1*06:02 proxy)


# ── rs2858884 HLA-DQ region marker tests ─────────────────────────────────


class TestHLAProxy:
    """Validate rs2858884 is curated as an informational HLA-DQ region marker.

    rs2858884 is NOT a valid proxy for HLA-DQB1*06:02 carriage or narcolepsy
    risk (the defining GWAS matched cases/controls on DQB1*06:02; the signal is
    protective and conditional). No genotype may yield a narcolepsy risk call.
    """

    def _get_hla(self, panel_data: dict) -> dict:
        for pathway in panel_data["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == "rs2858884":
                    return snp
        pytest.fail("rs2858884 not found in panel")

    def test_hla_has_coverage_note(self, panel_data: dict) -> None:
        hla = self._get_hla(panel_data)
        assert "coverage_note" in hla
        assert "proxy" in hla["coverage_note"].lower()
        assert "hla-dqb1" in hla["coverage_note"].lower()

    def test_hla_coverage_note_explains_misclassification(self, panel_data: dict) -> None:
        """Coverage note must explain why this is not a DQB1*06:02 proxy."""
        hla = self._get_hla(panel_data)
        note = hla["coverage_note"].lower()
        assert "not" in note
        assert "dqb1*06:02" in note
        assert "matched" in note  # GWAS matched cases/controls on DQB1*06:02
        assert "protective" in note

    def test_hla_all_genotypes_standard(self, panel_data: dict) -> None:
        """No genotype yields a narcolepsy risk call — all map to Standard.

        Keyed on the real Ensembl GRCh37 plus-strand A/C alleles; #608 re-keyed this
        from the mis-stranded C/T frame (``T`` is not a real allele here, it was the
        complement of the real ``A``).
        """
        hla = self._get_hla(panel_data)
        effects = hla["genotype_effects"]
        # Real plus-strand frame is A/C — there must be no T-bearing keys.
        assert set(effects) == {"CC", "CA", "AC", "AA"}
        for genotype in effects:
            assert effects[genotype]["category"] == "Standard"

    def test_hla_no_risk_allele(self, panel_data: dict) -> None:
        """No risk allele is asserted for this informational marker."""
        hla = self._get_hla(panel_data)
        assert hla["risk_allele"] is None

    def test_hla_evidence_level(self, panel_data: dict) -> None:
        hla = self._get_hla(panel_data)
        assert hla["evidence_level"] == 2

    def test_hla_in_special_calling(self, panel_data: dict) -> None:
        assert "HLA_DQ_region_marker" in panel_data["special_calling"]
        sc = panel_data["special_calling"]["HLA_DQ_region_marker"]
        assert sc["rsid"] == "rs2858884"
        assert "proxy_accuracy_note" in sc
        assert sc["proxy_target"] is None
