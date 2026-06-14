"""Tests for the Gene Health expansion module (P3-65).

Covers:
  - Panel loading and dataclass construction
  - Genotype normalization
  - SNP scoring with evidence-level gating
  - Pathway level determination (highest category)
  - Cross-module reference findings (APOE, Nutrigenomics, Methylation, Traits, Allergy)
  - Full scoring integration with sample DB
  - Findings storage and retrieval
  - Panel coverage tracking
  - GWAS annotation_coverage bitmask (bit 5)
  - 42 SNP finding count verification across 4 pathways
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.gene_health import (
    ELEVATED,
    INDETERMINATE,
    MODERATE,
    MODULE_NAME,
    STANDARD,
    GeneHealthPanel,
    GeneHealthResult,
    PanelSNP,
    Pathway,
    PathwayResult,
    SNPResult,
    _determine_pathway_level,
    _generate_cross_module_findings,
    _map_indel_genotype,
    _normalize_genotype,
    _score_snp,
    load_gene_health_panel,
    score_gene_health_pathways,
    store_gene_health_findings,
    update_annotation_coverage_gwas,
)
from backend.annotation.engine import GWAS_BIT
from backend.db.tables import (
    annotated_variants,
    findings,
    gwas_associations,
    panel_coverage,
    raw_variants,
    reference_metadata,
    sample_metadata_obj,
)

# -- Fixtures -----------------------------------------------------------------

PANEL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "gene_health_panel.json"
)
PARKINSONS_PANEL_PATH = PANEL_PATH.with_name("parkinsons_panel.json")


@pytest.fixture()
def panel() -> GeneHealthPanel:
    """Load the actual curated panel."""
    return load_gene_health_panel(PANEL_PATH)


@pytest.fixture()
def sample_engine(tmp_path: Path) -> sa.Engine:
    """Create a sample DB with raw_variants, findings, and panel_coverage tables."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'sample.db'}")
    sample_metadata_obj.create_all(engine)
    return engine


@pytest.fixture()
def reference_engine(tmp_path: Path) -> sa.Engine:
    """Create a reference DB with gwas_associations table."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)
    return engine


def _seed_variants(
    engine: sa.Engine,
    variants: list[tuple[str, str, int, str]],
) -> None:
    """Insert raw_variants rows: (rsid, chrom, pos, genotype)."""
    with engine.begin() as conn:
        conn.execute(
            sa.insert(raw_variants),
            [
                {"rsid": rsid, "chrom": chrom, "pos": pos, "genotype": gt}
                for rsid, chrom, pos, gt in variants
            ],
        )


def _seed_gwas(
    engine: sa.Engine,
    associations: list[tuple[str, str]],
) -> None:
    """Insert gwas_associations rows: (rsid, trait)."""
    with engine.begin() as conn:
        conn.execute(
            sa.insert(gwas_associations),
            [
                {
                    "rsid": rsid,
                    "trait": trait,
                    "p_value": 1e-10,
                    "chrom": "1",
                    "pos": 0,
                }
                for rsid, trait in associations
            ],
        )


# All 40 panel SNPs with chromosome positions and representative genotypes.
# APOE rs429358 (ε4, #329) and LRRK2 rs34637584 (G2019S, #404) are deliberately
# excluded — each gated opt-in module (APOE / Parkinson's) owns its disclosure, so
# Gene Health must not score them (mirrors the GBA1 exclusion).
ALL_GENE_HEALTH_VARIANTS = [
    # --- Neurological (10 SNPs) ---
    ("rs3764650", "19", 1046520, "TG"),  # ABCA7 het -> Moderate
    ("rs11136000", "8", 27464519, "CT"),  # CLU het -> Moderate
    ("rs356219", "4", 90626111, "AG"),  # SNCA het -> Moderate
    ("rs3135388", "6", 32408274, "GA"),  # HLA-DRB1*15:01 proxy het -> Elevated
    ("rs6897932", "5", 35874575, "TC"),  # IL7R T244I het -> Moderate
    ("rs2104286", "10", 6072697, "GA"),  # IL2RA het -> Moderate
    ("rs747302", "11", 637339, "CG"),  # DRD4 VNTR proxy het -> Moderate
    ("rs3746544", "20", 10202976, "GT"),  # SNAP25 het -> Moderate
    ("rs1801133", "1", 11856378, "GA"),  # MTHFR C677T het -> Moderate
    ("rs10166942", "2", 234825093, "CT"),  # TRPM8 het -> Moderate
    # --- Metabolic (10 SNPs) ---
    ("rs7903146", "10", 112998590, "CT"),  # TCF7L2 het -> Moderate
    ("rs1801282", "3", 12393125, "CG"),  # PPARG Pro12Ala het (protective Ala12) -> Standard
    ("rs5219", "11", 17409572, "CT"),  # KCNJ11 E23K het -> Moderate
    ("rs13266634", "8", 117172544, "TC"),  # SLC30A8 R325W het -> Moderate
    ("rs9939609", "16", 53820527, "TA"),  # FTO het -> Moderate
    ("rs17782313", "18", 60183864, "TC"),  # MC4R het -> Moderate
    ("rs2231142", "4", 88231392, "GT"),  # ABCG2 Q141K het -> Moderate
    ("rs12498742", "4", 9968925, "GA"),  # SLC2A9 het -> Moderate
    ("rs738409", "22", 44324727, "CG"),  # PNPLA3 I148M het -> Moderate
    ("rs58542926", "19", 19268740, "CT"),  # TM6SF2 E167K het -> Moderate
    # --- Autoimmune (11 SNPs) ---
    ("rs6910071", "6", 32574073, "GA"),  # HLA-DRB1 shared epitope het -> Elevated
    ("rs2476601", "1", 114377568, "GA"),  # PTPN22 R620W het -> Moderate
    ("rs7574865", "2", 191964633, "GT"),  # STAT4 het -> Moderate
    ("rs9273363", "6", 32658525, "TC"),  # HLA-DQB1 T1D proxy het -> Elevated
    ("rs689", "11", 2159842, "TA"),  # INS VNTR proxy het -> Moderate
    ("rs2066844", "16", 50745926, "CT"),  # NOD2 R702W het -> Moderate
    ("rs11209026", "1", 67705958, "GA"),  # IL23R R381Q het -> Standard/protective
    ("rs2241880", "2", 233274722, "AG"),  # ATG16L1 T300A het -> Moderate
    ("rs6822844", "4", 123372626, "TG"),  # IL2/IL21 het (protective T) -> Standard
    ("rs2004640", "7", 128941096, "GT"),  # IRF5 het -> Moderate
    ("rs1143679", "16", 31193489, "GA"),  # ITGAM R77H het -> Moderate
    # --- Sensory (9 SNPs) ---
    ("rs1061170", "1", 196642233, "TC"),  # CFH Y402H het -> Moderate
    ("rs10490924", "10", 122454932, "GT"),  # ARMS2 A69S het -> Moderate
    ("rs2230199", "19", 6669387, "CG"),  # C3 R102G het -> Moderate
    ("rs74315329", "1", 171605519, "GA"),  # MYOC Q368X het -> Elevated
    ("rs4236601", "7", 116162729, "AG"),  # CAV1/CAV2 het (real G/A) -> Moderate (#538)
    ("rs2157719", "9", 22033366, "CT"),  # CDKN2B-AS1 het (protective C minor allele) -> Standard
    ("rs80338939", "13", 20763612, "GG"),  # GJB2 35delG ref -> Standard (special)
    ("rs111033313", "7", 107683453, "GA"),  # SLC26A4 het -> Standard carrier context
    ("rs10955255", "8", 102508925, "GA"),  # GRHL2 het -> Moderate
]


# -- Panel loading tests ------------------------------------------------------


class TestPanelLoading:
    def test_load_panel_succeeds(self, panel: GeneHealthPanel) -> None:
        assert panel.module == "gene_health"
        assert panel.version == "1.0.0"

    def test_panel_has_four_pathways(self, panel: GeneHealthPanel) -> None:
        assert len(panel.pathways) == 4
        pathway_ids = {p.id for p in panel.pathways}
        assert pathway_ids == {
            "neurological",
            "metabolic",
            "autoimmune",
            "sensory",
        }

    def test_panel_all_rsids(self, panel: GeneHealthPanel) -> None:
        rsids = panel.all_rsids()
        assert len(rsids) == 40
        # Spot-check a few from each pathway
        assert "rs356219" in rsids  # neurological (SNCA)
        assert "rs7903146" in rsids  # metabolic
        assert "rs2476601" in rsids  # autoimmune
        assert "rs1061170" in rsids  # sensory
        # Gated-module-owned variants are never scored in Gene Health: APOE ε4
        # (rs429358, #329) and LRRK2 G2019S (rs34637584, #404).
        assert "rs429358" not in rsids
        assert "rs34637584" not in rsids

    def test_gba1_n370s_absent_to_match_parkinsons_suppression(self) -> None:
        """Gene Health must not independently report array-based GBA1 PD risk."""
        gene_health = json.loads(PANEL_PATH.read_text(encoding="utf-8"))
        parkinsons = json.loads(PARKINSONS_PANEL_PATH.read_text(encoding="utf-8"))
        rsids = {snp["rsid"] for pathway in gene_health["pathways"] for snp in pathway["snps"]}

        assert "rs76763715" not in rsids
        assert "GBA1 is DELIBERATELY EXCLUDED" in parkinsons["description"]
        assert "GBAP1 pseudogene" in parkinsons["description"]

    def test_apoe_e4_absent_to_match_apoe_gate(self) -> None:
        """Gene Health must not score APOE ε4 (rs429358); the gated APOE opt-in
        module owns ε4 disclosure (#329). Mirrors the GBA1 exclusion above."""
        gene_health = json.loads(PANEL_PATH.read_text(encoding="utf-8"))
        rsids = {snp["rsid"] for pathway in gene_health["pathways"] for snp in pathway["snps"]}
        assert "rs429358" not in rsids
        assert "deliberately NOT scored here" in gene_health["description"]

    def test_lrrk2_g2019s_absent_to_match_parkinsons_gate(self) -> None:
        """Gene Health must not score LRRK2 G2019S (rs34637584); the gated Parkinson's
        opt-in module owns that disclosure (#404, sibling of #329). Mirrors the GBA1
        and APOE exclusions above."""
        gene_health = json.loads(PANEL_PATH.read_text(encoding="utf-8"))
        parkinsons = json.loads(PARKINSONS_PANEL_PATH.read_text(encoding="utf-8"))
        rsids = {snp["rsid"] for pathway in gene_health["pathways"] for snp in pathway["snps"]}
        assert "rs34637584" not in rsids
        # The dedicated (gated) Parkinson's module owns LRRK2 G2019S — no coverage lost.
        assert any(m["primary_rsid"] == "rs34637584" for m in parkinsons["genotype_models"])
        # No stale top-level cross-link to the gated Parkinson's module remains.
        assert all(link["to_module"] != "parkinsons" for link in gene_health["cross_module_links"])

    def test_panel_snps_have_genotype_effects(self, panel: GeneHealthPanel) -> None:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                assert len(snp.genotype_effects) > 0, f"{snp.rsid} has no genotype effects"
                for gt, effect in snp.genotype_effects.items():
                    assert "category" in effect
                    assert "effect_summary" in effect
                    assert effect["category"] in (ELEVATED, MODERATE, STANDARD)

    def test_cross_module_links_present(self, panel: GeneHealthPanel) -> None:
        """Cross-links include dedicated modules for shared interpretations."""
        cross_modules: dict[str, str] = {}
        for pathway in panel.pathways:
            for snp in pathway.snps:
                if snp.cross_module:
                    cross_modules[snp.rsid] = snp.cross_module["module"]

        # Gated-module variants are NOT cross-linked from Gene Health: APOE ε4
        # (rs429358, #329) and LRRK2 G2019S (rs34637584, #404).
        assert "rs429358" not in cross_modules
        assert "rs34637584" not in cross_modules
        assert cross_modules.get("rs9939609") == "nutrigenomics"
        assert cross_modules.get("rs1801133") == "methylation"
        assert cross_modules.get("rs747302") == "traits"
        assert cross_modules.get("rs6822844") == "allergy"

    def test_amd_recommendations_do_not_trigger_areds2_from_genotype_alone(
        self,
        panel: GeneHealthPanel,
    ) -> None:
        """AMD risk SNPs must defer AREDS2 decisions to ophthalmic staging."""
        amd_rsids = {"rs1061170", "rs10490924"}
        amd_snps = [
            snp for pathway in panel.pathways for snp in pathway.snps if snp.rsid in amd_rsids
        ]

        assert {snp.rsid for snp in amd_snps} == amd_rsids
        for snp in amd_snps:
            text = snp.recommendation_text.lower()
            assert "eye-exam-based amd staging" in text
            assert "intermediate amd" in text
            assert "advanced amd in one eye" in text
            assert "not from genotype alone" in text
            assert "consider areds2 supplementation" not in text
            assert "discuss areds2 supplementation" not in text
            assert {"11594942", "23644932", "24974817"}.issubset(snp.pmids)

    def test_slc26a4_carrier_framing_does_not_imply_subclinical_eva(self) -> None:
        """Single-allele SLC26A4 calls must stay carrier-context only."""
        gene_health = json.loads(PANEL_PATH.read_text(encoding="utf-8"))
        slc26a4 = next(
            snp
            for pathway in gene_health["pathways"]
            for snp in pathway["snps"]
            if snp["rsid"] == "rs111033313"
        )

        assert slc26a4["rsid"] == "rs111033313"
        assert slc26a4["ref_allele"] == "A"
        assert slc26a4["risk_allele"] == "G"
        assert slc26a4["genotype_effects"]["AA"]["category"] == STANDARD
        assert slc26a4["genotype_effects"]["GG"]["category"] == ELEVATED
        assert {"34345941", "22116369", "23151025"}.issubset(slc26a4["pmids"])
        assert "single heterozygous c.919-2A>G call" in slc26a4["recommendation_text"]
        assert "carrier information" in slc26a4["recommendation_text"]

        for genotype in ("GA", "AG"):
            effect = slc26a4["genotype_effects"][genotype]
            text = effect["effect_summary"]
            assert effect["category"] == STANDARD
            assert "Carrier of a recessive Pendred syndrome / DFNB4 pathogenic variant" in text
            assert "does not by itself imply enlarged vestibular aqueduct" in text
            assert "sequencing/CNV/regulatory follow-up" in text
            assert "subclinical" not in text.lower()
            assert "May have enlarged vestibular aqueduct" not in text

    def test_gjb2_35delg_cites_curated_pmids(self) -> None:
        """#350: the GJB2 35delG (rs80338939) row must cite DFNB1/connexin-26 deafness
        evidence, not the unrelated PMIDs attached in error — 9462742 (mouse itchy
        locus), 10090481 (presenilin/Alzheimer's), 21280143 (EXT/exostoses)."""
        gene_health = json.loads(PANEL_PATH.read_text(encoding="utf-8"))
        gjb2 = next(
            snp
            for pathway in gene_health["pathways"]
            for snp in pathway["snps"]
            if snp["rsid"] == "rs80338939"
        )
        # 9285800 Zelante 1997 (Hum Mol Genet, DFNB1 connexin-26 35delG); 9139825
        # Kelsell 1997 (Nature, connexin-26 non-syndromic deafness).
        assert gjb2["pmids"] == ["9285800", "9139825"]
        assert {"9462742", "10090481", "21280143"}.isdisjoint(gjb2["pmids"])

    def test_slc26a4_cites_curated_pmids(self) -> None:
        """#406: the SLC26A4 (rs111033313) Pendred/DFNB4 hearing-loss row must cite
        SLC26A4 evidence, not the three papers attached in error (NCBI-verified):
        9002654 (mouse preimplantation-embryo growth factors), 12657976 (abdominal
        compartment syndrome), and 21280143 (EXT/exostoses array-CGH — the same
        misattribution scrubbed from the GJB2 row in #350)."""
        gene_health = json.loads(PANEL_PATH.read_text(encoding="utf-8"))
        slc26a4 = next(
            snp
            for pathway in gene_health["pathways"]
            for snp in pathway["snps"]
            if snp["rsid"] == "rs111033313"
        )
        # 9398842 Everett 1997 (Nat Genet, PDS/SLC26A4 cloning — Pendred syndrome);
        # 22116369 SLC26A4 EVA genotype-phenotype; 23151025 SLC26A4 c.919-2A>G
        # compound heterozygosity; 34345941 SLC26A4 genetic architecture/landscape.
        assert slc26a4["pmids"] == ["9398842", "22116369", "23151025", "34345941"]
        assert {"9002654", "12657976", "21280143"}.isdisjoint(slc26a4["pmids"])

    def test_load_nonexistent_panel_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_gene_health_panel(Path("/nonexistent/panel.json"))


# -- Genotype normalization tests ---------------------------------------------


class TestGenotypeNormalization:
    def test_normal_genotype(self) -> None:
        assert _normalize_genotype("CT") == "CT"
        assert _normalize_genotype("AA") == "AA"

    def test_nocall(self) -> None:
        assert _normalize_genotype("--") is None
        assert _normalize_genotype("") is None
        assert _normalize_genotype(None) is None

    def test_whitespace(self) -> None:
        assert _normalize_genotype("  CT  ") == "CT"

    def test_indel_markers(self) -> None:
        assert _normalize_genotype("II") is None
        assert _normalize_genotype("DD") is None

    def test_lowercase(self) -> None:
        assert _normalize_genotype("ct") == "CT"


# -- SNP scoring tests -------------------------------------------------------


class TestSNPScoring:
    def _get_snp(self, panel: GeneHealthPanel, rsid: str) -> PanelSNP:
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == rsid:
                    return snp
        pytest.fail(f"SNP {rsid} not found")

    def test_tcf7l2_het_moderate(self, panel: GeneHealthPanel) -> None:
        """TCF7L2 het (CT) -> Moderate, evidence_level=3."""
        snp = self._get_snp(panel, "rs7903146")
        assert snp.evidence_level == 3
        result = _score_snp(snp, "CT")
        assert result.category == MODERATE
        assert result.present_in_sample is True

    def test_tcf7l2_hom_elevated(self, panel: GeneHealthPanel) -> None:
        """TCF7L2 hom (TT) -> Elevated."""
        snp = self._get_snp(panel, "rs7903146")
        result = _score_snp(snp, "TT")
        assert result.category == ELEVATED

    def test_tcf7l2_ref_standard(self, panel: GeneHealthPanel) -> None:
        """TCF7L2 ref (CC) -> Standard."""
        snp = self._get_snp(panel, "rs7903146")
        result = _score_snp(snp, "CC")
        assert result.category == STANDARD

    def test_clu_rs11136000_direction(self, panel: GeneHealthPanel) -> None:
        """#581: CLU rs11136000 — C is the AD risk allele, T is protective.

        Literature is unanimous (the panel-cited Harold 2009 GWAS gives the T
        minor allele OR ~0.86; Braskie 2011: the C allele confers 1.16 greater
        odds than T): CC is the highest-risk genotype, TT the lowest (protective).
        Regression for the inverted entry that had ``risk_allele="T"`` and
        scored the protective TT homozygote (a real Harvard PGP genotype) as a
        "homozygous T risk allele" with "increased Alzheimer's risk".
        """
        snp = self._get_snp(panel, "rs11136000")
        assert snp.risk_allele == "C"
        assert snp.ref_allele == "T"

        # The protective T homozygote must NOT be flagged as risk-elevated.
        assert _score_snp(snp, "TT").category == STANDARD
        # One / two copies of the C risk allele.
        assert _score_snp(snp, "CT").category == MODERATE
        assert _score_snp(snp, "TC").category == MODERATE
        assert _score_snp(snp, "CC").category == MODERATE

        # Effect summaries point the right way (T protective, C risk).
        assert "protective" in snp.genotype_effects["TT"]["effect_summary"].lower()
        assert "risk allele" in snp.genotype_effects["CC"]["effect_summary"].lower()
        assert "protective" not in snp.genotype_effects["CC"]["effect_summary"].lower()

    def test_pparg_ala12_genotypes_not_risk_elevating(self, panel: GeneHealthPanel) -> None:
        """PPARG Pro12Ala (rs1801282): the protective Ala12 (G) allele must not be
        scored as risk. All genotypes map to Standard so a protective genotype
        cannot inflate the metabolic pathway level (gh #11).
        """
        snp = self._get_snp(panel, "rs1801282")
        # C/Pro12 is the higher-risk direction; G/Ala12 is protective.
        assert snp.risk_allele == "C"
        for genotype in ("GG", "GC", "CG", "CC"):
            result = _score_snp(snp, genotype)
            assert result.category == STANDARD, f"{genotype} should be Standard, not risk"

    def test_il23r_r381q_genotypes_not_risk_elevating(self, panel: GeneHealthPanel) -> None:
        """IL23R R381Q (rs11209026): A/Gln381 is protective for IBD.

        A-carrier genotypes must not inflate the autoimmune pathway level
        (gh #26).
        """
        snp = self._get_snp(panel, "rs11209026")
        assert snp.risk_allele == "G"
        for genotype in ("AA", "AG", "GA", "GG"):
            result = _score_snp(snp, genotype)
            assert result.category == STANDARD, f"{genotype} should be Standard, not risk"

    def test_cdkn2b_as1_plus_strand_and_protective_direction(self, panel: GeneHealthPanel) -> None:
        """CDKN2B-AS1/9p21 (rs2157719): the entry must be on the plus strand (C/T,
        per Ensembl GRCh37) AND in the correct, protective direction — the minor
        allele C is PROTECTIVE for primary open-angle glaucoma (replicated per-allele
        OR ~0.69-0.71; Wiggs 2012 PMID 22570617, Li 2015, Pasquale 2012), so no
        genotype is risk-elevating. Guards against #491, where the entry was on the
        wrong strand (G/T) AND inverted (it scored the protective minor allele as a
        'Moderate' glaucoma-risk genotype, and a real plus-strand C/T het silently
        no-matched -> Standard for the wrong reason).
        """
        snp = self._get_snp(panel, "rs2157719")
        # Plus strand: alleles are drawn from {C, T} only — never the minus-strand G.
        assert snp.risk_allele == "T"  # major allele = the higher-risk/baseline direction
        assert snp.ref_allele == "C"  # minor allele = protective
        assert set(snp.genotype_effects) == {"CC", "CT", "TC", "TT"}
        # Protective variant: every genotype (incl. the real plus-strand het CT, which
        # pre-fix silently no-matched) scores Standard — the minor allele never inflates
        # the sensory pathway level.
        for genotype in ("CC", "CT", "TC", "TT"):
            result = _score_snp(snp, genotype)
            assert result.category == STANDARD, f"{genotype} should be Standard, not risk"
            assert result.present_in_sample is True

    def test_hla_drb1_ra_proxy_uses_g_risk_allele(self, panel: GeneHealthPanel) -> None:
        """RA shared-epitope proxy rs6910071 uses the catalogued G risk allele."""
        snp = self._get_snp(panel, "rs6910071")
        assert snp.risk_allele == "G"
        assert snp.ref_allele == "A"

        expected_categories = {
            "GG": ELEVATED,
            "GA": ELEVATED,
            "AG": ELEVATED,
            "AA": STANDARD,
        }
        for genotype, expected in expected_categories.items():
            result = _score_snp(snp, genotype)
            assert result.category == expected

        assert "rs6910071-G" in _score_snp(snp, "GG").effect_summary

    def test_il2_il21_rs6822844_genotypes_not_risk_elevating(self, panel: GeneHealthPanel) -> None:
        """IL2/IL21 rs6822844: the minor T allele is protective across autoimmune
        diseases (T vs G OR ~0.72; carrying any T lowers risk vs GG). No genotype
        may inflate the autoimmune pathway level, so every genotype maps to
        Standard (gh #117).
        """
        snp = self._get_snp(panel, "rs6822844")
        # G is the common/risk-direction allele; T is the protective minor allele.
        assert snp.risk_allele == "G"
        for genotype in ("TT", "TG", "GT", "GG"):
            result = _score_snp(snp, genotype)
            assert result.category == STANDARD, f"{genotype} should be Standard, not risk"

    def test_lrrk2_g2019s_not_scored_in_gene_health(self, panel: GeneHealthPanel) -> None:
        """#404: LRRK2 G2019S (rs34637584) is no longer a Gene Health SNP — it is
        disclosed only through the gated Parkinson's opt-in module (sibling of #329)."""
        assert all(snp.rsid != "rs34637584" for pathway in panel.pathways for snp in pathway.snps)

    def test_not_genotyped_returns_standard(self, panel: GeneHealthPanel) -> None:
        """Missing genotype -> Standard with not present flag."""
        snp = self._get_snp(panel, "rs7903146")
        result = _score_snp(snp, None)
        assert result.category == STANDARD
        assert result.present_in_sample is False

    def test_unknown_genotype_returns_standard(self, panel: GeneHealthPanel) -> None:
        """Unknown genotype -> Standard."""
        snp = self._get_snp(panel, "rs7903146")
        result = _score_snp(snp, "XY")
        assert result.category == STANDARD
        assert result.present_in_sample is True

    def test_reversed_genotype_lookup(self, panel: GeneHealthPanel) -> None:
        """Reversed genotype (TC vs CT) still works."""
        snp = self._get_snp(panel, "rs7903146")
        result = _score_snp(snp, "TC")
        assert result.category == MODERATE

    def test_drd4_palindromic_guard_precedes_evidence_gating(self, panel: GeneHealthPanel) -> None:
        """DRD4 rs747302 is C/G palindromic (CC=Standard, GG=Moderate). The strand
        guard returns INDETERMINATE before any evidence-level gating runs, so a
        strand-ambiguous homozygote is never reported as a (possibly flipped)
        confident category (#269)."""
        snp = self._get_snp(panel, "rs747302")
        assert snp.evidence_level == 1
        result = _score_snp(snp, "GG")
        assert result.category == INDETERMINATE

    def test_drd4_uses_c_g_allele_model(self, panel: GeneHealthPanel) -> None:
        snp = self._get_snp(panel, "rs747302")
        assert snp.ref_allele == "C"
        assert snp.risk_allele == "G"
        assert set(snp.genotype_effects) == {"CC", "CG", "GC", "GG"}

    def test_drd4_scores_published_c_g_genotypes(self, panel: GeneHealthPanel) -> None:
        snp = self._get_snp(panel, "rs747302")
        # Heterozygotes are strand-resolvable, so they keep their curated category.
        assert _score_snp(snp, "CG").category == MODERATE
        assert _score_snp(snp, "GC").category == MODERATE
        # GG is a C/G palindromic homozygote (CC=Standard, GG=Moderate) → its strand
        # cannot be resolved from the genotype, so it is withheld as Indeterminate (#269).
        assert _score_snp(snp, "GG").category == INDETERMINATE

    def test_palindromic_homozygote_withheld_as_indeterminate(
        self, panel: GeneHealthPanel
    ) -> None:
        """#269: FTO rs9939609 is A/T palindromic with strand-differing categories
        (TT=Standard, AA=Elevated), so both homozygotes are withheld as
        Indeterminate with a strand caveat; the heterozygote stays resolvable."""
        snp = self._get_snp(panel, "rs9939609")
        for homozygote in ("AA", "TT"):
            result = _score_snp(snp, homozygote)
            assert result.category == INDETERMINATE, homozygote
            assert result.present_in_sample is True
            assert "palindromic" in result.effect_summary.lower()
            assert "strand" in (result.coverage_note or "").lower()
        # Heterozygote is strand-resolvable and keeps its curated category.
        assert _score_snp(snp, "AT").category == MODERATE
        assert _score_snp(snp, "TA").category == MODERATE

    def test_drd4_t_containing_genotype_is_indeterminate(self, panel: GeneHealthPanel) -> None:
        """DRD4 rs747302 models the C/G contrast; an observed ``CT`` carries a ``T``
        the locus does not model, so it is withheld as Indeterminate rather than
        silently scored Standard (which would hide a carrier as 'no effect') — #608."""
        snp = self._get_snp(panel, "rs747302")
        for gt in ("CT", "TC"):
            result = _score_snp(snp, gt)
            assert result.category == INDETERMINATE, gt
            assert result.present_in_sample is True
            assert "does not model" in result.effect_summary, gt
        # A non-nucleotide no-call is not an unmodeled allele — it stays Standard.
        assert _score_snp(snp, "--").category == STANDARD

    def test_gjb2_het_moderate(self, panel: GeneHealthPanel) -> None:
        """GJB2 35delG het (G/delG) -> Moderate."""
        snp = self._get_snp(panel, "rs80338939")
        result = _score_snp(snp, "G/delG")
        assert result.category == MODERATE
        assert result.present_in_sample is True

    def test_gjb2_hom_elevated(self, panel: GeneHealthPanel) -> None:
        """GJB2 35delG hom (delG/delG) -> Elevated."""
        snp = self._get_snp(panel, "rs80338939")
        result = _score_snp(snp, "delG/delG")
        assert result.category == ELEVATED

    def test_gjb2_reversed_slash_genotype(self, panel: GeneHealthPanel) -> None:
        """GJB2 reversed slash genotype (delG/G) -> matches G/delG -> Moderate."""
        snp = self._get_snp(panel, "rs80338939")
        result = _score_snp(snp, "delG/G")
        assert result.category == MODERATE
        assert result.present_in_sample is True

    def test_gjb2_indel_map_translates_vendor_id_codes(self, panel: GeneHealthPanel) -> None:
        """Vendor sorted-pair I/D codes map to the curated GJB2 keys (issue #159)."""
        snp = self._get_snp(panel, "rs80338939")
        assert _map_indel_genotype(snp, "DD") == "delG/delG"
        assert _map_indel_genotype(snp, "DI") == "G/delG"
        assert _map_indel_genotype(snp, "ID") == "G/delG"
        assert _map_indel_genotype(snp, "II") == "GG"
        # Lowercase from a chip is handled; non-indel/ACGT calls are left alone.
        assert _map_indel_genotype(snp, "dd") == "delG/delG"
        assert _map_indel_genotype(snp, "GG") is None
        assert _map_indel_genotype(snp, "--") is None
        assert _map_indel_genotype(snp, None) is None

    def test_indel_map_is_noop_for_non_indel_snp(self, panel: GeneHealthPanel) -> None:
        """SNPs without an indel_genotype_map are never indel-translated."""
        snp = self._get_snp(panel, "rs7903146")  # TCF7L2, ordinary ACGT locus
        assert snp.indel_genotype_map is None
        assert _map_indel_genotype(snp, "DD") is None
        assert _map_indel_genotype(snp, "CT") is None

    def test_slc26a4_heterozygotes_are_carrier_context_not_pathway_risk(
        self,
        panel: GeneHealthPanel,
    ) -> None:
        """SLC26A4 c.919-2A>G hets should not imply EVA from one allele."""
        snp = self._get_snp(panel, "rs111033313")
        assert snp.ref_allele == "A"
        assert snp.risk_allele == "G"
        assert _score_snp(snp, "AA").category == STANDARD
        assert _score_snp(snp, "GG").category == ELEVATED

        for genotype in ("GA", "AG"):
            result = _score_snp(snp, genotype)
            assert result.category == STANDARD
            assert result.present_in_sample is True
            assert "Carrier of a recessive" in result.effect_summary
            assert "does not by itself imply enlarged vestibular aqueduct" in result.effect_summary
            assert "subclinical" not in result.effect_summary.lower()


# -- GJB2 35delG indel reachability (end-to-end from parsed I/D codes) --------


class TestGJB2IndelEndToEnd:
    """Regression for issue #159: a GJB2 35delG call parsed as a vendor I/D code
    must reach the carrier/homozygous model, not be discarded as a no-call.

    These start from the parser's canonical sorted-pair genotypes ("DD"/"DI"/"II"
    in raw_variants), exercising the full score_gene_health_pathways path — not
    only direct _score_snp("G/delG") calls.
    """

    def _gjb2(self, result: GeneHealthResult) -> SNPResult:
        for pr in result.pathway_results:
            for s in pr.snp_results:
                if s.rsid == "rs80338939":
                    return s
        raise AssertionError("GJB2 rs80338939 not scored")

    def test_homozygous_deletion_dd_is_elevated(
        self, panel: GeneHealthPanel, sample_engine: sa.Engine, reference_engine: sa.Engine
    ) -> None:
        _seed_variants(sample_engine, [("rs80338939", "13", 20763612, "DD")])
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        gjb2 = self._gjb2(result)
        assert gjb2.present_in_sample is True
        assert gjb2.category == ELEVATED
        assert "homozygous" in gjb2.effect_summary.lower()

    def test_heterozygous_di_is_carrier_moderate(
        self, panel: GeneHealthPanel, sample_engine: sa.Engine, reference_engine: sa.Engine
    ) -> None:
        _seed_variants(sample_engine, [("rs80338939", "13", 20763612, "DI")])
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        gjb2 = self._gjb2(result)
        assert gjb2.present_in_sample is True
        assert gjb2.category == MODERATE
        assert "carrier" in gjb2.effect_summary.lower()

    def test_homozygous_reference_ii_is_standard_present(
        self, panel: GeneHealthPanel, sample_engine: sa.Engine, reference_engine: sa.Engine
    ) -> None:
        # II (no deletion) is a confident reference call, not a no-call.
        _seed_variants(sample_engine, [("rs80338939", "13", 20763612, "II")])
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        gjb2 = self._gjb2(result)
        assert gjb2.present_in_sample is True
        assert gjb2.category == STANDARD

    def test_indel_coverage_status_matches_scoring(
        self, panel: GeneHealthPanel, sample_engine: sa.Engine, reference_engine: sa.Engine
    ) -> None:
        # A scored I/D call must be 'called' in panel coverage, not 'no_call'
        # (coverage must agree with scoring — see #159 review).
        _seed_variants(sample_engine, [("rs80338939", "13", 20763612, "DD")])
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        cov = next(r for r in result.panel_coverage_rows if r["rsid"] == "rs80338939")
        assert cov["coverage_status"] == "called"


# -- Pathway level determination tests ----------------------------------------


class TestPathwayLevel:
    def test_all_standard(self) -> None:
        results = [
            SNPResult(
                rsid="rs1",
                gene="G1",
                variant_name="V1",
                genotype="AA",
                category=STANDARD,
                effect_summary="",
                evidence_level=2,
                pmids=[],
                recommendation_text="",
                present_in_sample=True,
            ),
        ]
        assert _determine_pathway_level(results) == STANDARD

    def test_elevated_wins(self) -> None:
        results = [
            SNPResult(
                rsid="rs1",
                gene="G1",
                variant_name="V1",
                genotype="AA",
                category=MODERATE,
                effect_summary="",
                evidence_level=2,
                pmids=[],
                recommendation_text="",
                present_in_sample=True,
            ),
            SNPResult(
                rsid="rs2",
                gene="G2",
                variant_name="V2",
                genotype="BB",
                category=ELEVATED,
                effect_summary="",
                evidence_level=3,
                pmids=[],
                recommendation_text="",
                present_in_sample=True,
            ),
        ]
        assert _determine_pathway_level(results) == ELEVATED

    def test_no_called_snps(self) -> None:
        results = [
            SNPResult(
                rsid="rs1",
                gene="G1",
                variant_name="V1",
                genotype=None,
                category=STANDARD,
                effect_summary="",
                evidence_level=2,
                pmids=[],
                recommendation_text="",
                present_in_sample=False,
            ),
        ]
        assert _determine_pathway_level(results) == STANDARD


# -- Cross-module findings tests -----------------------------------------------


class TestCrossModuleFindings:
    def test_apoe_e4_not_disclosed_via_gene_health(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """#329: an APOE ε4 carrier gets NO ε4 / Alzheimer's disclosure from Gene
        Health — no scored finding, no cross-link, nothing in stored findings (the
        source for the generic /api/analysis/findings aggregator). ε4 is disclosed
        only through the gated APOE opt-in module."""
        _seed_variants(sample_engine, [("rs429358", "19", 44908684, "TC")])  # ε4 het
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)

        # rs429358 is not in the panel → never scored, never cross-linked.
        scored = {s.rsid for pr in result.pathway_results for s in pr.snp_results}
        assert "rs429358" not in scored
        assert [f for f in result.cross_module_findings if f.target_module == "apoe"] == []

        # No ε4 / Alzheimer's text in any in-memory scored or cross-module finding.
        in_memory = " ".join(
            [s.effect_summary for pr in result.pathway_results for s in pr.snp_results]
            + [f.finding_text for f in result.cross_module_findings]
        ).lower()
        assert "epsilon" not in in_memory
        assert "alzheimer" not in in_memory

        # Storage layer (feeds the generic aggregator): no ε4 leak persisted.
        store_gene_health_findings(result, sample_engine)
        with sample_engine.connect() as conn:
            persisted = " ".join(
                r.finding_text
                for r in conn.execute(
                    sa.select(findings.c.finding_text).where(findings.c.module == MODULE_NAME)
                )
            ).lower()
            # Direct column check: no gene_health finding or panel_coverage row is
            # keyed to the APOE ε4 SNP at all.
            apoe_findings = conn.execute(
                sa.select(findings.c.id).where(
                    findings.c.module == MODULE_NAME, findings.c.rsid == "rs429358"
                )
            ).fetchall()
            apoe_coverage = conn.execute(
                sa.select(panel_coverage.c.rsid).where(
                    panel_coverage.c.module == MODULE_NAME,
                    panel_coverage.c.rsid == "rs429358",
                )
            ).fetchall()
        assert "epsilon" not in persisted
        assert "alzheimer" not in persisted
        assert apoe_findings == []
        assert apoe_coverage == []

    def test_fto_nutrigenomics_cross_link(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """rs9939609 carrier -> nutrigenomics cross-link."""
        _seed_variants(
            sample_engine,
            [("rs9939609", "16", 53820527, "TA")],
        )
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        nutri_links = [
            f for f in result.cross_module_findings if f.target_module == "nutrigenomics"
        ]
        assert len(nutri_links) >= 1

    def test_standard_genotype_no_cross_link(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Ref genotype -> no cross-module findings for that SNP."""
        # rs1801133 (MTHFR, G/A — non-palindromic) GG is homozygous-ref → Standard,
        # so it produces no methylation cross-link. (A palindromic SNP's homozygote
        # would be withheld as Indeterminate rather than Standard — see #269 — and
        # so would still cross-link; pick a non-palindromic SNP for this check.)
        _seed_variants(
            sample_engine,
            [("rs1801133", "1", 11856378, "GG")],  # MTHFR ref (Standard)
        )
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        methyl_links = [
            f for f in result.cross_module_findings if f.target_module == "methylation"
        ]
        assert len(methyl_links) == 0

    def test_cross_module_deduplication(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Distinct genes -> distinct cross-links (each target appears once).

        Every panel cross-link maps a unique (gene, module), so each target
        module surfaces exactly once. Variant-granular dedup (see
        ``test_cross_module_dedup_keys_on_rsid_not_gene``) does not change this.
        """
        _seed_variants(
            sample_engine,
            [
                ("rs747302", "11", 637339, "CG"),  # DRD4 -> traits
                ("rs9939609", "16", 53820527, "TA"),  # FTO -> nutrigenomics
                ("rs1801133", "1", 11856378, "GA"),  # MTHFR -> methylation
            ],
        )
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        # Each cross-module target should appear exactly once
        targets = [f.target_module for f in result.cross_module_findings]
        assert targets.count("traits") == 1
        assert targets.count("nutrigenomics") == 1
        assert targets.count("methylation") == 1

    def test_cross_module_dedup_keys_on_rsid_not_gene(self) -> None:
        """Two distinct SNPs under one gene each keep their cross-link (#315).

        Cross-module dedup is keyed on (rsid, target_module), not gene-only, so
        a second cross-link SNP added under an existing gene cannot be silently
        dropped — the VDR FokI/BsmI collapse fixed for Skin in #205/#309 and
        Allergy in #197/#92. Latent in gene_health today (every (gene, module)
        is single-rsid), so this exercises ``_generate_cross_module_findings``
        directly with two synthetic VDR SNPs sharing one gene+module. Fails
        under the old (gene, target_module) dedup key.
        """
        cross_meta = {"module": "nutrigenomics", "note": "vitamin D cross-reference"}

        def _panel_snp(rsid: str) -> PanelSNP:
            return PanelSNP(
                rsid=rsid,
                gene="VDR",
                variant_name=rsid,
                hgvs_protein=None,
                risk_allele="A",
                ref_allele="G",
                genotype_effects={},
                evidence_level=2,
                pmids=[],
                recommendation_text="",
                cross_module=cross_meta,
            )

        def _snp_result(rsid: str) -> SNPResult:
            return SNPResult(
                rsid=rsid,
                gene="VDR",
                variant_name=rsid,
                genotype="AA",
                category=MODERATE,
                effect_summary="",
                evidence_level=2,
                pmids=[],
                recommendation_text="",
                present_in_sample=True,
            )

        panel = GeneHealthPanel(
            module="gene_health",
            version="test",
            pathways=[
                Pathway(
                    id="p1",
                    name="P1",
                    description="",
                    snps=[_panel_snp("rs2228570"), _panel_snp("rs1544410")],
                )
            ],
        )
        pathway_results = [
            PathwayResult(
                pathway_id="p1",
                pathway_name="P1",
                level=MODERATE,
                snp_results=[_snp_result("rs2228570"), _snp_result("rs1544410")],
            )
        ]

        cross = _generate_cross_module_findings(pathway_results, panel)
        vdr_links = [c for c in cross if c.gene == "VDR" and c.target_module == "nutrigenomics"]
        assert len(vdr_links) == 2
        assert {c.rsid for c in vdr_links} == {"rs2228570", "rs1544410"}


# -- Full scoring integration tests -------------------------------------------


class TestFullScoring:
    def test_all_variants_scored(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """All 40 panel SNPs are scored when present."""
        _seed_variants(sample_engine, ALL_GENE_HEALTH_VARIANTS)
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        total_snps = sum(len(pr.snp_results) for pr in result.pathway_results)
        assert total_snps == 40

    def test_four_pathways_scored(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        _seed_variants(sample_engine, ALL_GENE_HEALTH_VARIANTS)
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        assert len(result.pathway_results) == 4

    def test_empty_sample_all_standard(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """No genotypes -> all pathways Standard."""
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        for pr in result.pathway_results:
            assert pr.level == STANDARD

    def test_drd4_cc_palindromic_is_indeterminate_not_standard(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """rs747302 CC is a C/G palindromic homozygote: it is surfaced as
        Indeterminate (strand-ambiguous) rather than a confident Standard call, and
        it must not raise the neurological pathway level (#269)."""
        _seed_variants(sample_engine, [("rs747302", "11", 637339, "CC")])

        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        neurological = next(pr for pr in result.pathway_results if pr.pathway_id == "neurological")
        drd4 = next(snp for snp in neurological.snp_results if snp.rsid == "rs747302")

        assert drd4.category == INDETERMINATE
        assert "strand" in (drd4.coverage_note or "").lower()
        # Withheld from aggregation: a strand-ambiguous call neither raises nor
        # lowers the pathway level.
        assert neurological.level == STANDARD

        store_gene_health_findings(result, sample_engine)
        with sample_engine.connect() as conn:
            neurological_summary = conn.execute(
                sa.select(findings.c.pathway_level).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.category == "pathway_summary",
                    findings.c.pathway == neurological.pathway_name,
                )
            ).scalar_one()

        assert neurological_summary == STANDARD

    def test_il23r_r381q_alone_does_not_raise_autoimmune_pathway(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """A protective IL23R R381Q call must not create risk findings (gh #26)."""
        _seed_variants(sample_engine, [("rs11209026", "1", 67705958, "GA")])

        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        autoimmune = next(pr for pr in result.pathway_results if pr.pathway_id == "autoimmune")
        il23r = next(snp for snp in autoimmune.snp_results if snp.rsid == "rs11209026")

        assert il23r.category == STANDARD
        assert autoimmune.level == STANDARD

        store_gene_health_findings(result, sample_engine)
        with sample_engine.connect() as conn:
            il23r_findings = conn.execute(
                sa.select(findings).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.rsid == "rs11209026",
                )
            ).fetchall()
            autoimmune_summary = conn.execute(
                sa.select(findings.c.pathway_level).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.category == "pathway_summary",
                    findings.c.pathway == autoimmune.pathway_name,
                )
            ).scalar_one()

        assert il23r_findings == []
        assert autoimmune_summary == STANDARD

    def test_hla_drb1_ra_proxy_hom_ref_does_not_emit_snp_finding(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """All-reference rs6910071 AA must stay absent from stored SNP findings."""
        _seed_variants(sample_engine, [("rs6910071", "6", 32574073, "AA")])

        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        autoimmune = next(pr for pr in result.pathway_results if pr.pathway_id == "autoimmune")
        hla_drb1 = next(snp for snp in autoimmune.snp_results if snp.rsid == "rs6910071")

        assert hla_drb1.category == STANDARD
        assert autoimmune.level == STANDARD

        store_gene_health_findings(result, sample_engine)
        with sample_engine.connect() as conn:
            hla_findings = conn.execute(
                sa.select(findings).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.rsid == "rs6910071",
                    findings.c.category == "snp_finding",
                )
            ).fetchall()
            autoimmune_summary = conn.execute(
                sa.select(findings.c.pathway_level).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.category == "pathway_summary",
                    findings.c.pathway == autoimmune.pathway_name,
                )
            ).scalar_one()

        assert hla_findings == []
        assert autoimmune_summary == STANDARD

    def test_il2_il21_rs6822844_tt_alone_does_not_raise_autoimmune_pathway(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """A protective IL2/IL21 rs6822844 TT call must not create risk findings
        or raise the autoimmune pathway above Standard (gh #117)."""
        _seed_variants(sample_engine, [("rs6822844", "4", 123372626, "TT")])

        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        autoimmune = next(pr for pr in result.pathway_results if pr.pathway_id == "autoimmune")
        il2il21 = next(snp for snp in autoimmune.snp_results if snp.rsid == "rs6822844")

        assert il2il21.category == STANDARD
        assert autoimmune.level == STANDARD

        store_gene_health_findings(result, sample_engine)
        with sample_engine.connect() as conn:
            il2il21_findings = conn.execute(
                sa.select(findings).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.rsid == "rs6822844",
                )
            ).fetchall()
            autoimmune_summary = conn.execute(
                sa.select(findings.c.pathway_level).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.category == "pathway_summary",
                    findings.c.pathway == autoimmune.pathway_name,
                )
            ).scalar_one()

        assert il2il21_findings == []
        assert autoimmune_summary == STANDARD

    # GG = hom-ref non-carrier, GA = het carrier, AA = hom-risk (the strongest
    # disclosure trigger if it were still scored). None may disclose anything.
    @pytest.mark.parametrize("genotype", ["GG", "GA", "AA"])
    def test_lrrk2_g2019s_not_disclosed_via_gene_health(
        self,
        genotype: str,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """#404: an LRRK2 G2019S sample (non-carrier, het, or hom-risk) gets NO
        Parkinson's disclosure from Gene Health — no scored finding, no parkinsons
        cross-link, no panel_coverage row, and nothing in stored findings (the source
        for the generic /api/analysis/findings aggregator). LRRK2 G2019S is disclosed
        only through the gated Parkinson's opt-in module."""
        _seed_variants(sample_engine, [("rs34637584", "12", 40340400, genotype)])
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)

        # rs34637584 is not in the panel → never scored, never cross-linked.
        scored = {s.rsid for pr in result.pathway_results for s in pr.snp_results}
        assert "rs34637584" not in scored
        assert [f for f in result.cross_module_findings if f.target_module == "parkinsons"] == []

        # No Parkinson's / LRRK2-G2019S text in any in-memory scored or cross finding.
        in_memory = " ".join(
            [s.effect_summary for pr in result.pathway_results for s in pr.snp_results]
            + [f.finding_text for f in result.cross_module_findings]
        ).lower()
        assert "parkinson" not in in_memory
        assert "g2019s" not in in_memory

        # Storage layer (feeds the generic aggregator): no leak persisted, and no
        # findings or panel_coverage row is keyed to rs34637584.
        store_gene_health_findings(result, sample_engine)
        with sample_engine.connect() as conn:
            persisted = " ".join(
                r.finding_text
                for r in conn.execute(
                    sa.select(findings.c.finding_text).where(findings.c.module == MODULE_NAME)
                )
            ).lower()
            lrrk2_finding_rows = conn.execute(
                sa.select(findings.c.id).where(
                    findings.c.module == MODULE_NAME, findings.c.rsid == "rs34637584"
                )
            ).fetchall()
            lrrk2_coverage_rows = conn.execute(
                sa.select(panel_coverage.c.rsid).where(
                    panel_coverage.c.module == MODULE_NAME,
                    panel_coverage.c.rsid == "rs34637584",
                )
            ).fetchall()
        assert "parkinson" not in persisted
        assert "g2019s" not in persisted
        assert lrrk2_finding_rows == []
        assert lrrk2_coverage_rows == []

    def test_gba1_n370s_alone_does_not_surface_gene_health_risk(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Array-based GBA1 N370S must stay suppressed in ungated Gene Health (#71)."""
        _seed_variants(sample_engine, [("rs76763715", "1", 155205634, "CT")])

        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        neurological = next(pr for pr in result.pathway_results if pr.pathway_id == "neurological")

        assert neurological.snp_results
        assert all(snp.rsid != "rs76763715" for snp in neurological.snp_results)
        assert neurological.level == STANDARD
        assert result.cross_module_findings == []
        assert all(row["rsid"] != "rs76763715" for row in result.panel_coverage_rows)

        store_gene_health_findings(result, sample_engine)
        with sample_engine.connect() as conn:
            gba_rows = conn.execute(
                sa.select(findings).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.rsid == "rs76763715",
                )
            ).fetchall()
            neurological_summary = conn.execute(
                sa.select(findings.c.pathway_level).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.category == "pathway_summary",
                    findings.c.pathway == neurological.pathway_name,
                )
            ).scalar_one()

        assert gba_rows == []
        assert neurological_summary == STANDARD

    def test_slc26a4_single_carrier_does_not_raise_sensory_pathway(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """One SLC26A4 c.919-2A>G allele is carrier context, not an EVA finding."""
        _seed_variants(sample_engine, [("rs111033313", "7", 107683453, "GA")])

        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        sensory = next(pr for pr in result.pathway_results if pr.pathway_id == "sensory")
        slc26a4 = next(snp for snp in sensory.snp_results if snp.rsid == "rs111033313")

        assert slc26a4.category == STANDARD
        assert "does not by itself imply enlarged vestibular aqueduct" in slc26a4.effect_summary
        assert sensory.level == STANDARD

        store_gene_health_findings(result, sample_engine)
        with sample_engine.connect() as conn:
            slc26a4_findings = conn.execute(
                sa.select(findings).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.rsid == "rs111033313",
                    findings.c.category == "snp_finding",
                )
            ).fetchall()
            slc26a4_context = conn.execute(
                sa.select(findings).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.rsid == "rs111033313",
                    findings.c.category == "carrier_context",
                )
            ).fetchone()
            sensory_summary = conn.execute(
                sa.select(findings.c.pathway_level, findings.c.detail_json).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.category == "pathway_summary",
                    findings.c.pathway == sensory.pathway_name,
                )
            ).one()

        assert slc26a4_findings == []
        assert slc26a4_context is not None
        assert slc26a4_context.pathway_level == STANDARD
        assert "Carrier of a recessive" in slc26a4_context.finding_text
        assert "subclinical" not in slc26a4_context.finding_text.lower()
        context_detail = json.loads(slc26a4_context.detail_json)
        assert context_detail["carrier_context"] is True
        assert "second allele" in context_detail["recommendation"]

        assert sensory_summary.pathway_level == STANDARD
        summary_detail = json.loads(sensory_summary.detail_json)
        slc26a4_detail = next(
            snp for snp in summary_detail["snp_details"] if snp["rsid"] == "rs111033313"
        )
        assert "recommendation" not in slc26a4_detail
        assert "pmids" not in slc26a4_detail

    def test_slc26a4_reference_call_does_not_emit_carrier_context(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Reference SLC26A4 AA must not surface heterozygote carrier guidance."""
        _seed_variants(sample_engine, [("rs111033313", "7", 107683453, "AA")])

        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        sensory = next(pr for pr in result.pathway_results if pr.pathway_id == "sensory")
        slc26a4 = next(snp for snp in sensory.snp_results if snp.rsid == "rs111033313")

        assert slc26a4.category == STANDARD
        assert sensory.level == STANDARD

        store_gene_health_findings(result, sample_engine)
        with sample_engine.connect() as conn:
            slc26a4_rows = conn.execute(
                sa.select(findings).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.rsid == "rs111033313",
                )
            ).fetchall()

        assert slc26a4_rows == []


# -- Findings storage tests ---------------------------------------------------


class TestFindingsStorage:
    def test_findings_stored(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Findings are stored in the sample database."""
        _seed_variants(sample_engine, ALL_GENE_HEALTH_VARIANTS)
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        count = store_gene_health_findings(result, sample_engine)
        assert count > 0

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == MODULE_NAME)
            ).fetchall()
        assert len(rows) == count

    def test_pathway_summaries_stored(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """4 pathway summary findings are stored."""
        _seed_variants(sample_engine, ALL_GENE_HEALTH_VARIANTS)
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        store_gene_health_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            summaries = conn.execute(
                sa.select(findings).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.category == "pathway_summary",
                )
            ).fetchall()
        assert len(summaries) == 4

    def test_rerun_clears_previous(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Re-running scoring clears previous findings."""
        _seed_variants(sample_engine, ALL_GENE_HEALTH_VARIANTS)

        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        count1 = store_gene_health_findings(result, sample_engine)

        # Re-run
        result2 = score_gene_health_pathways(panel, sample_engine, reference_engine)
        count2 = store_gene_health_findings(result2, sample_engine)

        assert count1 == count2
        with sample_engine.connect() as conn:
            total = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == MODULE_NAME)
            ).scalar()
        assert total == count2

    def test_cross_module_findings_stored(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Cross-module findings are stored."""
        _seed_variants(sample_engine, ALL_GENE_HEALTH_VARIANTS)
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        store_gene_health_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            cross = conn.execute(
                sa.select(findings).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.category == "cross_module",
                )
            ).fetchall()
        assert len(cross) > 0

    def test_no_same_category_duplicate_findings(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """No two stored findings of the same category share a variant.

        Fast, CI-visible guard for the invariant checked by the slow
        ``test_no_exact_duplicate_findings`` integration test. A cross-linked
        SNP legitimately yields both an ``snp_finding`` and a ``cross_module``
        row for the same variant, so the identity key must include
        ``category``; a genuine bug (the same category emitted twice for one
        variant) would still be caught.
        """
        _seed_variants(sample_engine, ALL_GENE_HEALTH_VARIANTS)
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        store_gene_health_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == MODULE_NAME)
            ).fetchall()

        seen: set[tuple] = set()
        for row in rows:
            if row.rsid is None:  # pathway summaries carry no variant
                continue
            key = (row.category, row.rsid, row.gene_symbol)
            assert key not in seen, f"Duplicate finding: {key}"
            seen.add(key)

    def test_cross_linked_variant_emitted_under_both_categories(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """A cross-linked SNP appears as both snp_finding and cross_module.

        Locks the intended dual emission so a future de-dup "fix" cannot
        silently drop the per-SNP finding or the cross-module pointer. Uses
        MTHFR rs1801133 (-> Methylation), the variant originally reported in
        issue #13.
        """
        _seed_variants(sample_engine, [("rs1801133", "1", 11856378, "GA")])
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        store_gene_health_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            categories = {
                row.category
                for row in conn.execute(
                    sa.select(findings).where(
                        findings.c.module == MODULE_NAME,
                        findings.c.rsid == "rs1801133",
                        findings.c.gene_symbol == "MTHFR",
                    )
                ).fetchall()
            }
        assert categories == {"snp_finding", "cross_module"}


# -- Panel coverage tests ----------------------------------------------------


class TestPanelCoverage:
    def test_coverage_stored(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Panel coverage rows are stored for all 40 SNPs."""
        _seed_variants(sample_engine, ALL_GENE_HEALTH_VARIANTS)
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        store_gene_health_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(panel_coverage).where(panel_coverage.c.module == MODULE_NAME)
            ).fetchall()
        assert len(rows) == 40

    def test_called_status(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Genotyped SNPs have 'called' status."""
        _seed_variants(sample_engine, ALL_GENE_HEALTH_VARIANTS)
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        store_gene_health_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(panel_coverage).where(
                    panel_coverage.c.module == MODULE_NAME,
                    panel_coverage.c.rsid == "rs7903146",
                )
            ).fetchone()
        assert row is not None
        assert row.coverage_status == "called"

    def test_not_on_array_status(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Missing SNPs have 'not_on_array' status."""
        # Only seed one variant
        _seed_variants(sample_engine, [("rs7903146", "10", 112998590, "CT")])
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        store_gene_health_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(panel_coverage).where(
                    panel_coverage.c.module == MODULE_NAME,
                    panel_coverage.c.rsid == "rs3764650",
                )
            ).fetchone()
        assert row is not None
        assert row.coverage_status == "not_on_array"

    def test_no_call_status(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """No-call genotypes have 'no_call' status."""
        _seed_variants(sample_engine, [("rs7903146", "10", 112998590, "--")])
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        store_gene_health_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(panel_coverage).where(
                    panel_coverage.c.module == MODULE_NAME,
                    panel_coverage.c.rsid == "rs7903146",
                )
            ).fetchone()
        assert row is not None
        assert row.coverage_status == "no_call"


# -- GWAS annotation_coverage bitmask tests -----------------------------------


class TestAnnotationCoverage:
    def test_gwas_bitmask_set(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """GWAS-matched variants get annotation_coverage bit 5 set."""
        _seed_variants(sample_engine, ALL_GENE_HEALTH_VARIANTS)

        # Seed annotated_variants for one rsid
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rs7903146",
                        "chrom": "10",
                        "pos": 112998590,
                        "annotation_coverage": 0,
                    }
                ],
            )

        # Seed GWAS association
        _seed_gwas(reference_engine, [("rs7903146", "type 2 diabetes")])

        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        updated = update_annotation_coverage_gwas(result, sample_engine)
        assert updated == 1

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs7903146"
                )
            ).fetchone()
        assert row is not None
        assert (row.annotation_coverage & GWAS_BIT) == GWAS_BIT

    def test_gwas_bitmask_or_preserves_existing(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """GWAS bitmask OR preserves existing bits."""
        _seed_variants(sample_engine, ALL_GENE_HEALTH_VARIANTS)

        # Seed with existing bitmask
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rs7903146",
                        "chrom": "10",
                        "pos": 112998590,
                        "annotation_coverage": 3,  # VEP + ClinVar
                    }
                ],
            )

        _seed_gwas(reference_engine, [("rs7903146", "type 2 diabetes")])

        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        update_annotation_coverage_gwas(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs7903146"
                )
            ).fetchone()
        assert row is not None
        assert (row.annotation_coverage & GWAS_BIT) == GWAS_BIT
        assert (row.annotation_coverage & 3) == 3  # existing bits preserved

    def test_no_gwas_matches_zero_updates(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """No GWAS matches -> zero updates."""
        _seed_variants(sample_engine, ALL_GENE_HEALTH_VARIANTS)
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        updated = update_annotation_coverage_gwas(result, sample_engine)
        assert updated == 0


class TestMTHFRCitationProvenance:
    """Guard the MTHFR C677T evidence links (issue #200).

    The MTHFR rs1801133 row previously cited three papers that do not support
    its migraine-with-aura claim: 15592354 (a non-resolving/invalid PubMed ID),
    20689844 (Mediterranean Sea biodiversity), and 25904306 (a macrophage /
    nanocarbon-dispersant study). Pin the row to verified MTHFR C677T
    migraine-with-aura references so those off-topic PMIDs cannot reappear.
    """

    # MTHFR C677T migraine-with-aura references verified on PubMed + Consensus.
    _MTHFR_PMIDS = frozenset(
        {
            "17714520",  # Rubino 2009, Cephalalgia — C677T/migraine meta-analysis (aura only)
            "16365871",  # Scher 2006, Ann Neurol — MTHFR C677T in a population-based sample
            "15053827",  # Lea 2004, BMC Med — C677T influences migraine-with-aura susceptibility
        }
    )
    # Off-topic / invalid PMIDs wrongly cited by the MTHFR row before the fix.
    _BANNED_PMIDS = frozenset({"15592354", "20689844", "25904306"})

    def _get_mthfr(self, panel: GeneHealthPanel) -> PanelSNP:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                if snp.rsid == "rs1801133":
                    return snp
        raise AssertionError("MTHFR rs1801133 not found in panel")

    def test_mthfr_cites_verified_migraine_aura_refs(self, panel: GeneHealthPanel) -> None:
        assert set(self._get_mthfr(panel).pmids) == self._MTHFR_PMIDS

    def test_banned_pmids_absent_from_panel(self, panel: GeneHealthPanel) -> None:
        # All three banned PMIDs were exclusive to the MTHFR row, so none should
        # appear anywhere in the gene-health panel after the fix.
        for pathway in panel.pathways:
            for snp in pathway.snps:
                leaked = self._BANNED_PMIDS & set(snp.pmids)
                assert not leaked, f"{snp.rsid} cites unrelated PMID(s) {sorted(leaked)}"


class TestSTAT4CitationProvenance:
    """Guard the STAT4 rs7574865 evidence links (issue #226).

    The STAT4 row previously cited three papers that resolve to other loci, not
    STAT4: 17804836 (Plenge 2007, the TRAF1-C5 RA GWAS), 19503088 (Gregersen
    2009, the REL/NF-kappaB RA locus), and 22286173 (Low 2012, an intracranial-
    aneurysm EDNRA GWAS). Pin the row to verified STAT4 rs7574865 RA/SLE
    references so those off-topic PMIDs cannot silently reappear.

    NB: 17804836 (TRAF1-C5) is *also* cited by other gene-health rows (PTPN22
    rs2476601 and one more) where it is a separate concern, so it is banned only
    from the STAT4 row, not panel-wide. 19503088 and 22286173 were exclusive to
    the STAT4 row and are banned across the whole panel.
    """

    # Verified STAT4 rs7574865 RA/SLE references (NCBI + Consensus):
    _STAT4_PMIDS = frozenset(
        {
            "17804842",  # Remmers 2007, NEJM - STAT4 rs7574865 intron-3 RA/SLE haplotype
            "19479340",  # Ji 2010, Mol Biol Rep - STAT4 rs7574865 RA/SLE meta-analysis
        }
    )
    # Off-topic PMIDs that were exclusive to the STAT4 row -> safe to ban panel-wide.
    _STAT4_EXCLUSIVE_BANNED = frozenset({"19503088", "22286173"})
    # Off-topic for STAT4, but other rows cite 17804836 -> ban the trio from the STAT4 row only.
    _STAT4_ROW_BANNED = frozenset({"17804836", "19503088", "22286173"})

    def _get_stat4(self, panel: GeneHealthPanel) -> PanelSNP:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                if snp.rsid == "rs7574865":
                    return snp
        raise AssertionError("STAT4 rs7574865 not found in panel")

    def test_stat4_cites_verified_refs(self, panel: GeneHealthPanel) -> None:
        assert set(self._get_stat4(panel).pmids) == self._STAT4_PMIDS

    def test_stat4_row_drops_all_unrelated_pmids(self, panel: GeneHealthPanel) -> None:
        leaked = self._STAT4_ROW_BANNED & set(self._get_stat4(panel).pmids)
        assert not leaked, f"STAT4 row still cites unrelated PMID(s) {sorted(leaked)}"

    def test_stat4_exclusive_banned_pmids_absent_from_panel(self, panel: GeneHealthPanel) -> None:
        # 19503088 and 22286173 were exclusive to the STAT4 row, so they must not
        # appear anywhere in the gene-health panel after the fix. (17804836 is
        # intentionally NOT asserted panel-wide: PTPN22 and another row retain it.)
        for pathway in panel.pathways:
            for snp in pathway.snps:
                leaked = self._STAT4_EXCLUSIVE_BANNED & set(snp.pmids)
                assert not leaked, f"{snp.rsid} cites STAT4-misattributed PMID(s) {sorted(leaked)}"


class TestGoutRowsAreRiskModifiers:
    """#287 — the legacy Gene Health gout rows (ABCG2 Q141K rs2231142, SLC2A9
    rs12498742) must NOT give genotype-triggered dietary/treatment prescriptions.
    They are risk modifiers, aligned with the dedicated gout module
    (`tests/backend/test_gout.py` denylist). Evidence: diet explains very little
    serum-urate variance versus genetics (Major et al. 2018, BMJ, PMID 30305269)
    and has a weak/minor causal role (Topless et al. 2021, PMID 33663556);
    urate-lowering therapy is assessed in people *with* gout, not triggered by
    genotype. Genotype alone does not validate purine/fructose restriction or
    ULT advice."""

    # Phrases that would turn an educational urate-risk readout into an
    # unvalidated dietary/treatment prescription. Mirrors test_gout.py's
    # _PRESCRIPTION_DENYLIST, plus fructose / hydration / urate-lowering-therapy.
    _PRESCRIPTION_DENYLIST = (
        "purine",
        "fructose",
        "alcohol",
        "low-purine",
        "urate-lowering",
        "hydrat",
        "lose weight",
        "cherry",
        "diet",
    )
    _GOUT_RSIDS = ("rs2231142", "rs12498742")  # ABCG2 Q141K, SLC2A9

    def _snp(self, panel: GeneHealthPanel, rsid: str) -> PanelSNP:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                if snp.rsid == rsid:
                    return snp
        raise AssertionError(f"gout SNP {rsid} not found in panel")

    def test_gout_rows_have_no_prescriptive_language(self, panel: GeneHealthPanel) -> None:
        for rsid in self._GOUT_RSIDS:
            snp = self._snp(panel, rsid)
            blob = " ".join(
                [
                    snp.recommendation_text,
                    *(e["effect_summary"] for e in snp.genotype_effects.values()),
                ]
            ).lower()
            for term in self._PRESCRIPTION_DENYLIST:
                assert term not in blob, (
                    f"{snp.gene} {rsid} surfaces prescriptive term {term!r}; "
                    "genotype alone does not warrant diet/treatment advice (#287)"
                )

    def test_gout_rows_refer_to_dedicated_module(self, panel: GeneHealthPanel) -> None:
        for rsid in self._GOUT_RSIDS:
            snp = self._snp(panel, rsid)
            assert "gout module" in snp.recommendation_text.lower(), (
                f"{snp.gene} {rsid} should point users to the dedicated gout module"
            )


class TestPPARGCitationProvenance:
    """Guard the PPARG Pro12Ala (rs1801282) evidence links (issue #285).

    The PPARG row previously cited two papers unrelated to PPARG/T2D: 10862766
    (an MLK-3/SPRK kinase-oligomerization study) and 16702423 (a Haley-Knott QTL
    statistical-methods paper). Pin the row to verified PPARG Pro12Ala / T2D
    references so those off-topic PMIDs cannot silently reappear.
    """

    # PPARG Pro12Ala / type-2-diabetes references verified on NCBI ESummary.
    _PPARG_PMIDS = frozenset(
        {
            "17463246",  # Saxena 2007, Science (DGI) — T2D GWAS confirming loci incl. PPARG
            "20179158",  # Gouda 2010, Am J Epidemiol — PPARG2 Pro12Ala/T2D HuGE meta-analysis
            "32728045",  # 2020, Sci Rep — PPARG Pro12Ala & T2DM systematic review/meta-analysis
        }
    )
    # Off-topic PMIDs wrongly cited by the PPARG row before the fix. Both were
    # exclusive to this row; 16702423 (QTL-methods) is also locked repo-wide via
    # test_citation_provenance_guard.BANNED_OFF_TOPIC_PMIDS, while 10862766 (names
    # the human gene MAP3K11/MLK-3) is kept gene-scoped here.
    _BANNED_PMIDS = frozenset({"10862766", "16702423"})

    def _get_pparg(self, panel: GeneHealthPanel) -> PanelSNP:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                if snp.rsid == "rs1801282":
                    return snp
        raise AssertionError("PPARG rs1801282 not found in panel")

    def test_pparg_cites_verified_t2d_refs(self, panel: GeneHealthPanel) -> None:
        assert set(self._get_pparg(panel).pmids) == self._PPARG_PMIDS

    def test_banned_pmids_absent_from_panel(self, panel: GeneHealthPanel) -> None:
        # Both banned PMIDs were exclusive to the PPARG row, so neither should
        # appear anywhere in the gene-health panel after the fix.
        for pathway in panel.pathways:
            for snp in pathway.snps:
                leaked = self._BANNED_PMIDS & set(snp.pmids)
                assert not leaked, f"{snp.rsid} cites unrelated PMID(s) {sorted(leaked)}"


class TestSNAP25CitationProvenance:
    """Guard the SNAP25 rs3746544 evidence links (issue #326).

    The SNAP25 3'-UTR row previously cited three papers with no connection to
    SNAP25 or ADHD: 12402218 (a bioethics-of-genetic-engineering essay),
    17965720 (a Pkd1 kidney-cyst study), and 20584310 (a thioredoxin-reductase
    breast-cancer prognosis study). Pin the row to verified SNAP25 rs3746544 /
    ADHD references so those off-topic PMIDs cannot silently reappear.
    """

    # SNAP25 rs3746544 / ADHD references verified on NCBI ESummary + Consensus.
    _SNAP25_PMIDS = frozenset(
        {
            "10889551",  # Barr 2000, Mol Psychiatry — original SNAP-25/ADHD 3'-UTR linkage
            "16088329",  # Feng 2005, Mol Psychiatry — SNAP25 as ADHD susceptibility gene
            "26941099",  # Liu 2017, Mol Neurobiol — SNAP25/ADHD meta-analysis (rs3746544 OR 1.14)
        }
    )
    # Off-topic PMIDs wrongly cited by the SNAP25 row; all three were exclusive to
    # this row, so none may appear anywhere in the panel after the fix.
    _BANNED_PMIDS = frozenset({"12402218", "17965720", "20584310"})

    def _get_snap25(self, panel: GeneHealthPanel) -> PanelSNP:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                if snp.rsid == "rs3746544":
                    return snp
        raise AssertionError("SNAP25 rs3746544 not found in panel")

    def test_snap25_cites_verified_adhd_refs(self, panel: GeneHealthPanel) -> None:
        assert set(self._get_snap25(panel).pmids) == self._SNAP25_PMIDS

    def test_banned_pmids_absent_from_panel(self, panel: GeneHealthPanel) -> None:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                leaked = self._BANNED_PMIDS & set(snp.pmids)
                assert not leaked, f"{snp.rsid} cites unrelated PMID(s) {sorted(leaked)}"


class TestIRF5CitationProvenance:
    """Guard the IRF5 rs2004640 evidence links (issue #326).

    The IRF5 exon-1B splice-site row previously cited three papers unrelated to
    IRF5/SLE: 16429160 (a mouse Nalp1b/anthrax-toxin study), 17620523 (a
    maternal/social-origins-of-hypertension review), and 25533199 (a histone-H3
    K79-dimethylation mitosis study). Pin the row to verified IRF5 rs2004640 /
    SLE references so those off-topic PMIDs cannot silently reappear.

    NB: 25533199 is *also* cited by two other gene-health rows (a separate
    concern tracked by #326's umbrella), so it is banned only from the IRF5 row,
    not panel-wide. 16429160 and 17620523 were exclusive to the IRF5 row and are
    banned across the whole panel.
    """

    # Verified IRF5 rs2004640 / SLE references (NCBI ESummary + Consensus):
    _IRF5_PMIDS = frozenset(
        {
            "16642019",  # Graham 2006, Nat Genet — rs2004640 T creates exon-1B splice donor (SLE)
            "17166181",  # Demirci 2007, Ann Hum Genet — rs2004640/SLE replication (OR 1.68)
            "31018759",  # Bae 2019, Lupus — rs2004640/SLE updated meta-analysis (OR 1.47)
        }
    )
    # Off-topic PMIDs exclusive to the IRF5 row -> safe to ban panel-wide.
    _IRF5_EXCLUSIVE_BANNED = frozenset({"16429160", "17620523"})
    # 25533199 is shared with other rows -> ban from the IRF5 row only.
    _IRF5_ROW_BANNED = frozenset({"16429160", "17620523", "25533199"})

    def _get_irf5(self, panel: GeneHealthPanel) -> PanelSNP:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                if snp.rsid == "rs2004640":
                    return snp
        raise AssertionError("IRF5 rs2004640 not found in panel")

    def test_irf5_cites_verified_sle_refs(self, panel: GeneHealthPanel) -> None:
        assert set(self._get_irf5(panel).pmids) == self._IRF5_PMIDS

    def test_irf5_row_drops_all_unrelated_pmids(self, panel: GeneHealthPanel) -> None:
        leaked = self._IRF5_ROW_BANNED & set(self._get_irf5(panel).pmids)
        assert not leaked, f"IRF5 row still cites unrelated PMID(s) {sorted(leaked)}"

    def test_irf5_exclusive_banned_pmids_absent_from_panel(self, panel: GeneHealthPanel) -> None:
        # 16429160 and 17620523 were exclusive to the IRF5 row, so they must not
        # appear anywhere in the panel. (25533199 is intentionally NOT asserted
        # panel-wide: two other rows retain it pending their own #326 fix.)
        for pathway in panel.pathways:
            for snp in pathway.snps:
                leaked = self._IRF5_EXCLUSIVE_BANNED & set(snp.pmids)
                assert not leaked, f"{snp.rsid} cites IRF5-misattributed PMID(s) {sorted(leaked)}"


class TestMYOCCitationProvenance:
    """Guard the MYOC Q368X (rs74315329) evidence links (issue #326).

    The MYOC Q368X row previously cited three papers unrelated to MYOC/glaucoma:
    9671762 (a von Hippel-Lindau gene-product study), 24507775 (an LDL-cholesterol
    coding-variant exome study), and 29785011 (an asthma/allergic-disease cross-
    trait GWAS). Pin the row to verified MYOC p.Gln368Ter / primary-open-angle-
    glaucoma references so those off-topic PMIDs cannot silently reappear.

    NB: 29785011 is *also* cited by one other gene-health row (a separate concern
    tracked by #326's umbrella), so it is banned only from the MYOC row, not
    panel-wide. 9671762 and 24507775 were exclusive to the MYOC row and are
    banned across the whole panel.
    """

    # Verified MYOC p.Gln368Ter / POAG references (NCBI ESummary + Consensus):
    _MYOC_PMIDS = frozenset(
        {
            "10196380",  # Fingert 1999, Hum Mol Genet — Q368X most common MYOC mutation (1703 pts)
            "23029558",  # Cheng 2012, PLoS One — myocilin/POAG meta-analysis (Q368X OR 4.68)
            "30267046",  # Souzeau 2019, JAMA Ophthalmol — rs74315329 penetrance (pop.+registry)
        }
    )
    # Off-topic PMIDs exclusive to the MYOC row -> safe to ban panel-wide.
    _MYOC_EXCLUSIVE_BANNED = frozenset({"9671762", "24507775"})
    # 29785011 is shared with another row -> ban from the MYOC row only.
    _MYOC_ROW_BANNED = frozenset({"9671762", "24507775", "29785011"})

    def _get_myoc(self, panel: GeneHealthPanel) -> PanelSNP:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                if snp.rsid == "rs74315329":
                    return snp
        raise AssertionError("MYOC rs74315329 not found in panel")

    def test_myoc_cites_verified_glaucoma_refs(self, panel: GeneHealthPanel) -> None:
        assert set(self._get_myoc(panel).pmids) == self._MYOC_PMIDS

    def test_myoc_row_drops_all_unrelated_pmids(self, panel: GeneHealthPanel) -> None:
        leaked = self._MYOC_ROW_BANNED & set(self._get_myoc(panel).pmids)
        assert not leaked, f"MYOC row still cites unrelated PMID(s) {sorted(leaked)}"

    def test_myoc_exclusive_banned_pmids_absent_from_panel(self, panel: GeneHealthPanel) -> None:
        # 9671762 and 24507775 were exclusive to the MYOC row, so they must not
        # appear anywhere in the panel. (29785011 is intentionally NOT asserted
        # panel-wide: one other row retains it pending its own #326 fix.)
        for pathway in panel.pathways:
            for snp in pathway.snps:
                leaked = self._MYOC_EXCLUSIVE_BANNED & set(snp.pmids)
                assert not leaked, f"{snp.rsid} cites MYOC-misattributed PMID(s) {sorted(leaked)}"


class TestGRHL2CitationProvenance:
    """Guard the GRHL2 rs10955255 evidence links (issue #326).

    The GRHL2 intron row previously cited three papers with no connection to
    GRHL2 or age-related hearing loss: 19578363 (a basal-cell-carcinoma GWAS),
    25006839 (a physics-of-hearing fluid-mechanics review), and 30068587 (a
    small-RNA northern-hybridization lab protocol). Pin the row to verified
    GRHL2 rs10955255 / age-related-hearing-impairment references so those
    off-topic PMIDs cannot silently reappear.

    Two references suffice here (discovery + meta-analysis): both directly test
    rs10955255 against ARHI, whereas later large hearing GWAS implicate other
    SNPs/genes. This mirrors the verified-pair precedent for STAT4 above.
    """

    # Verified GRHL2 rs10955255 / ARHI references (NCBI ESummary + Consensus):
    _GRHL2_PMIDS = frozenset(
        {
            "17921507",  # Van Laer 2008, Hum Mol Genet — GRHL2/rs10955255 ARHI discovery
            "31232964",  # Han 2019, Medicine — rs10955255/ARHI meta-analysis (OR 1.26-1.33)
        }
    )
    # Off-topic PMIDs wrongly cited by the GRHL2 row; all three were exclusive to
    # this row, so none may appear anywhere in the panel after the fix.
    _BANNED_PMIDS = frozenset({"19578363", "25006839", "30068587"})

    def _get_grhl2(self, panel: GeneHealthPanel) -> PanelSNP:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                if snp.rsid == "rs10955255":
                    return snp
        raise AssertionError("GRHL2 rs10955255 not found in panel")

    def test_grhl2_cites_verified_arhi_refs(self, panel: GeneHealthPanel) -> None:
        assert set(self._get_grhl2(panel).pmids) == self._GRHL2_PMIDS

    def test_banned_pmids_absent_from_panel(self, panel: GeneHealthPanel) -> None:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                leaked = self._BANNED_PMIDS & set(snp.pmids)
                assert not leaked, f"{snp.rsid} cites unrelated PMID(s) {sorted(leaked)}"


# ── Panel-wide citation remediation (issue #326) ─────────────────────────


def _all_gene_health_pmids(panel: GeneHealthPanel):
    """Yield every PMID cited by any SNP in the panel."""
    for pathway in panel.pathways:
        for snp in pathway.snps:
            yield from snp.pmids


class TestGeneHealthCitationRemediation:
    """Pin the ~20 rows remediated in #326 to verified, on-topic citations.

    The #326 audit found ~23 of 42 gene_health rows mixed in PMIDs resolving to
    unrelated topics (e.g. PNPLA3 cited an SF-1 hypothalamus paper + a carbon-
    aerogel paper; ITGAM a Hog1-MAPK arsenite paper; C3 a hepatitis-C epidemic
    paper). Already-fixed rows (STAT4 #226, MTHFR #200, PPARG #285, SNAP25, IRF5,
    MYOC, GRHL2) are locked above; this completes the panel. Each row below was
    re-cited with verified gene/variant/disease references — every PMID title
    checked via NCBI esummary and the association verified with the Consensus
    connector (e.g. Gloyn 2003 KCNJ11 E23K, Woodward 2009 ABCG2 urate-transporter
    functional, ITGAM R77H complement-receptor functional). KCNJ11 and ABCG2 also
    drop the two dead PMIDs 12874175 / 27457907 (#417).
    """

    # rsid -> exact verified on-topic PMID set the row must cite:
    _REMEDIATED: dict[str, set[str]] = {
        "rs3135388": {"17660530", "21833088"},  # HLA-DRB1 MS (drop COPD)
        "rs6897932": {"17660530", "21833088", "27188999"},  # IL7R MS
        "rs747302": {"20732625", "32075956", "15909295"},  # DRD4 ADHD
        "rs10166942": {"23793025", "27322543", "20802479"},  # TRPM8 migraine
        "rs7903146": {"16415884", "17463246", "29514658"},  # TCF7L2 T2D
        "rs5219": {
            "17463246",
            "22101970",
            "12540637",
        },  # KCNJ11 E23K T2D; drops dead 12874175 (#417)
        "rs2231142": {"18834626", "19506252"},  # ABCG2 Q141K gout; drops dead 27457907 (#417)
        "rs12498742": {"18327257", "19503597"},  # SLC2A9 urate
        "rs738409": {"18820647", "21381068", "36897668"},  # PNPLA3 I148M NAFLD
        "rs58542926": {"24531328", "24978903", "26331730"},  # TM6SF2 E167K NAFLD
        "rs2476601": {"15208781", "17804836", "17259401"},  # PTPN22 R620W autoimmune
        "rs689": {"17554260", "19430480", "9054945"},  # INS T1D
        "rs2066844": {"11385577", "11385576", "11875755"},  # NOD2 Crohn
        "rs11209026": {"17435756", "21297633", "17068223"},  # IL23R IBD
        "rs2241880": {"17435756", "17200669", "24553140"},  # ATG16L1 T300A Crohn
        "rs1143679": {"18204098", "18204448", "22586164"},  # ITGAM R77H SLE
        "rs2230199": {"23455636", "17634448", "18325906"},  # C3 R102G AMD
        "rs10490924": {
            "16174643",
            "23455636",
            "11594942",
            "23644932",
            "24974817",
        },  # ARMS2 A69S AMD (keeps AREDS context refs)
        "rs4236601": {"21532571", "20835238", "24572674"},  # CAV1/CAV2 glaucoma
        "rs2157719": {"21532571", "27064256", "22570617"},  # CDKN2B-AS1 glaucoma
    }

    # Off-topic / dead PMIDs removed by this remediation; none may reappear
    # anywhere in the gene_health panel. (17804842 — the Remmers STAT4 NEJM
    # paper — is off-topic on NOD2/IL23R but LEGITIMATE on the STAT4 row, so it
    # is row-scoped below, not panel-banned.)
    _PANEL_BANNED: frozenset[str] = frozenset(
        {
            "11244489",
            "12145745",
            "12874175",
            "16999713",
            "17170444",
            "18191106",
            "18341684",
            "18385739",
            "18552285",
            "20418890",
            "21111025",
            "22884227",
            "22977957",
            "24076671",
            "24141364",
            "25129146",
            "25533199",
            "26752085",
            "27184023",
            "27457907",
            "29785011",
            "29904014",
            "9399903",
        }
    )

    def test_each_remediated_row_cites_verified_refs(self, panel: GeneHealthPanel) -> None:
        by_rsid = {snp.rsid: snp for pathway in panel.pathways for snp in pathway.snps}
        for rsid, allow in self._REMEDIATED.items():
            assert rsid in by_rsid, f"{rsid} missing from gene_health panel"
            snp = by_rsid[rsid]
            assert set(snp.pmids) == allow, (
                f"{rsid} ({snp.gene}) cites {snp.pmids}, expected {sorted(allow)}"
            )

    def test_off_topic_pmids_absent_from_panel(self, panel: GeneHealthPanel) -> None:
        leaked = self._PANEL_BANNED & set(_all_gene_health_pmids(panel))
        assert not leaked, f"off-topic PMID(s) {sorted(leaked)} still present in gene_health panel"

    def test_stat4_paper_scoped_to_stat4_row(self, panel: GeneHealthPanel) -> None:
        # 17804842 (Remmers STAT4 NEJM) is correct for STAT4 but was off-topic on
        # NOD2/IL23R; after #326 it must appear ONLY on the STAT4 rs7574865 row.
        citing = {
            snp.rsid
            for pathway in panel.pathways
            for snp in pathway.snps
            if "17804842" in snp.pmids
        }
        assert citing == {"rs7574865"}, (
            f"17804842 should be only on STAT4 rs7574865, found on {sorted(citing)}"
        )


class TestEvidenceGatingCap:
    """#473: drive a synthetic Elevated ★☆ (evidence_level=1) SNP through the
    _score_snp evidence cap so the Elevated→Moderate hard-cap is actually exercised
    (companion to #470 for the gene_health module)."""

    @staticmethod
    def _snp(evidence_level: int, aa_category: str) -> PanelSNP:
        return PanelSNP(
            rsid="rs99999991",
            gene="TEST",
            variant_name="synthetic cap probe",
            hgvs_protein=None,
            risk_allele="A",
            ref_allele="G",
            genotype_effects={
                "GG": {"category": STANDARD, "effect_summary": "Normal."},
                "AG": {"category": MODERATE, "effect_summary": "Moderate."},
                "GA": {"category": MODERATE, "effect_summary": "Moderate."},
                "AA": {"category": aa_category, "effect_summary": "Risk genotype."},
            },
            evidence_level=evidence_level,
            pmids=["12345678"],
            recommendation_text="Test.",
        )

    def test_low_evidence_caps_elevated_to_moderate(self) -> None:
        result = _score_snp(self._snp(evidence_level=1, aa_category=ELEVATED), "AA")
        assert result.category == MODERATE  # ★☆ caps Elevated -> Moderate

    def test_evidence_level_2_allows_elevated(self) -> None:
        result = _score_snp(self._snp(evidence_level=2, aa_category=ELEVATED), "AA")
        assert result.category == ELEVATED
