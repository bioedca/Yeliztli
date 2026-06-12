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
    MODERATE,
    MODULE_NAME,
    STANDARD,
    GeneHealthPanel,
    PanelSNP,
    SNPResult,
    _determine_pathway_level,
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


# All 42 panel SNPs with chromosome positions and representative genotypes.
ALL_GENE_HEALTH_VARIANTS = [
    # --- Neurological (13 SNPs) ---
    ("rs429358", "19", 44908684, "TC"),  # APOE e4 det het -> Elevated
    ("rs3764650", "19", 1046520, "TG"),  # ABCA7 het -> Moderate
    ("rs11136000", "8", 27464519, "CT"),  # CLU het -> Moderate
    ("rs34637584", "12", 40340400, "GA"),  # LRRK2 G2019S het -> Moderate
    ("rs356219", "4", 90626111, "AG"),  # SNCA het -> Moderate
    ("rs3135388", "6", 32408274, "GA"),  # HLA-DRB1*15:01 proxy het -> Elevated
    ("rs6897932", "5", 35874575, "TC"),  # IL7R T244I het -> Moderate
    ("rs2104286", "10", 6072697, "GA"),  # IL2RA het -> Moderate
    ("rs747302", "11", 637339, "CT"),  # DRD4 VNTR proxy het -> Moderate
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
    ("rs6822844", "4", 123372626, "TG"),  # IL2/IL21 het -> Moderate
    ("rs2004640", "7", 128941096, "GT"),  # IRF5 het -> Moderate
    ("rs1143679", "16", 31193489, "GA"),  # ITGAM R77H het -> Moderate
    # --- Sensory (9 SNPs) ---
    ("rs1061170", "1", 196642233, "TC"),  # CFH Y402H het -> Moderate
    ("rs10490924", "10", 122454932, "GT"),  # ARMS2 A69S het -> Moderate
    ("rs2230199", "19", 6669387, "CG"),  # C3 R102G het -> Moderate
    ("rs74315329", "1", 171605519, "GA"),  # MYOC Q368X het -> Elevated
    ("rs4236601", "7", 116165018, "CA"),  # CAV1/CAV2 het -> Moderate
    ("rs2157719", "9", 22003367, "TG"),  # CDKN2B-AS1 het -> Moderate
    ("rs80338939", "13", 20763612, "GG"),  # GJB2 35delG ref -> Standard (special)
    ("rs111033253", "7", 107301080, "GA"),  # SLC26A4 het -> Moderate
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
        assert len(rsids) == 42
        # Spot-check a few from each pathway
        assert "rs429358" in rsids  # neurological
        assert "rs7903146" in rsids  # metabolic
        assert "rs2476601" in rsids  # autoimmune
        assert "rs1061170" in rsids  # sensory

    def test_gba1_n370s_absent_to_match_parkinsons_suppression(self) -> None:
        """Gene Health must not independently report array-based GBA1 PD risk."""
        gene_health = json.loads(PANEL_PATH.read_text(encoding="utf-8"))
        parkinsons = json.loads(PARKINSONS_PANEL_PATH.read_text(encoding="utf-8"))
        rsids = {snp["rsid"] for pathway in gene_health["pathways"] for snp in pathway["snps"]}

        assert "rs76763715" not in rsids
        assert "GBA1 is DELIBERATELY EXCLUDED" in parkinsons["description"]
        assert "GBAP1 pseudogene" in parkinsons["description"]

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

        assert cross_modules.get("rs429358") == "apoe"
        assert cross_modules.get("rs34637584") == "parkinsons"
        assert cross_modules.get("rs9939609") == "nutrigenomics"
        assert cross_modules.get("rs1801133") == "methylation"
        assert cross_modules.get("rs747302") == "traits"
        assert cross_modules.get("rs6822844") == "allergy"

    def test_lrrk2_gene_health_matches_parkinsons_reduced_penetrance_source(self) -> None:
        """Gene Health must not drift from the dedicated Parkinson's LRRK2 framing."""
        gene_health = json.loads(PANEL_PATH.read_text(encoding="utf-8"))
        parkinsons = json.loads(PARKINSONS_PANEL_PATH.read_text(encoding="utf-8"))
        lrrk2 = next(
            snp
            for pathway in gene_health["pathways"]
            for snp in pathway["snps"]
            if snp["rsid"] == "rs34637584"
        )
        parkinsons_model = parkinsons["genotype_models"][0]
        shared_risk_sentence = (
            "Published age-80 estimates vary by cohort design, ancestry, and modifier burden"
        )

        assert lrrk2["evidence_level"] == parkinsons_model["evidence_stars"] == 2
        assert lrrk2["cross_module"]["module"] == "parkinsons"
        assert any(link["to_module"] == "parkinsons" for link in gene_health["cross_module_links"])
        assert shared_risk_sentence in parkinsons_model["finding_text"]
        assert lrrk2["pmids"] == parkinsons_model["pmids"]

        for genotype in ("GA", "AG", "AA"):
            effect = lrrk2["genotype_effects"][genotype]
            assert effect["category"] == MODERATE
            assert "24-49%" in effect["effect_summary"]
            assert "25-42.5%" in effect["effect_summary"]
            assert shared_risk_sentence in effect["effect_summary"]
            assert "28% by age 59" not in effect["effect_summary"]
            assert "51% by age 69" not in effect["effect_summary"]
            assert "74% by age 79" not in effect["effect_summary"]

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

    def test_lrrk2_g2019s_uses_reduced_penetrance_parkinsons_framing(
        self,
        panel: GeneHealthPanel,
    ) -> None:
        """LRRK2 G2019S should be reduced-penetrance and two-star in Gene Health."""
        snp = self._get_snp(panel, "rs34637584")
        assert snp.evidence_level == 2
        assert snp.cross_module is not None
        assert snp.cross_module["module"] == "parkinsons"

        for genotype in ("GA", "AG", "AA"):
            result = _score_snp(snp, genotype)
            assert result.category == MODERATE
            assert result.evidence_level == 2
            assert "reduced-penetrance" in result.effect_summary
            assert "24-49%" in result.effect_summary
            assert "25-42.5%" in result.effect_summary
            assert "28% by age 59" not in result.effect_summary
            assert "51% by age 69" not in result.effect_summary
            assert "74% by age 79" not in result.effect_summary

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

    def test_evidence_gating_star1_caps_moderate(self, panel: GeneHealthPanel) -> None:
        """DRD4 rs747302 has evidence_level=1. Even if panel says Moderate for TT,
        evidence_level=1 hard-caps at Moderate (can never reach Elevated)."""
        snp = self._get_snp(panel, "rs747302")
        assert snp.evidence_level == 1
        # TT is the highest-risk genotype for DRD4, panel says Moderate
        result = _score_snp(snp, "TT")
        assert result.category == MODERATE  # capped at Moderate by star-1

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
    def test_apoe_cross_link(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """rs429358 carrier -> apoe cross-link."""
        _seed_variants(
            sample_engine,
            [("rs429358", "19", 44908684, "TC")],
        )
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        apoe_links = [f for f in result.cross_module_findings if f.target_module == "apoe"]
        assert len(apoe_links) >= 1
        assert "APOE" in apoe_links[0].finding_text

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
        _seed_variants(
            sample_engine,
            [("rs429358", "19", 44908684, "TT")],  # ref
        )
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        apoe_links = [f for f in result.cross_module_findings if f.target_module == "apoe"]
        assert len(apoe_links) == 0

    def test_cross_module_deduplication(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Only one cross-link per gene+target combination."""
        _seed_variants(
            sample_engine,
            [
                ("rs429358", "19", 44908684, "TC"),  # APOE -> apoe
                ("rs9939609", "16", 53820527, "TA"),  # FTO -> nutrigenomics
                ("rs1801133", "1", 11856378, "GA"),  # MTHFR -> methylation
            ],
        )
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        # Each cross-module target should appear exactly once
        targets = [f.target_module for f in result.cross_module_findings]
        assert targets.count("apoe") == 1
        assert targets.count("nutrigenomics") == 1
        assert targets.count("methylation") == 1


# -- Full scoring integration tests -------------------------------------------


class TestFullScoring:
    def test_all_variants_scored(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """All 42 panel SNPs are scored when present."""
        _seed_variants(sample_engine, ALL_GENE_HEALTH_VARIANTS)
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        total_snps = sum(len(pr.snp_results) for pr in result.pathway_results)
        assert total_snps == 42

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

    def test_lrrk2_g2019s_alone_is_moderate_not_high_confidence(
        self,
        panel: GeneHealthPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """LRRK2 G2019S alone must not surface as high-confidence Gene Health risk."""
        _seed_variants(sample_engine, [("rs34637584", "12", 40340400, "GA")])

        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        neurological = next(pr for pr in result.pathway_results if pr.pathway_id == "neurological")
        lrrk2 = next(snp for snp in neurological.snp_results if snp.rsid == "rs34637584")

        assert lrrk2.category == MODERATE
        assert lrrk2.evidence_level == 2
        assert neurological.level == MODERATE

        parkinsons_links = [
            finding
            for finding in result.cross_module_findings
            if finding.target_module == "parkinsons"
        ]
        assert len(parkinsons_links) == 1

        store_gene_health_findings(result, sample_engine)
        with sample_engine.connect() as conn:
            lrrk2_row = conn.execute(
                sa.select(findings).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.rsid == "rs34637584",
                    findings.c.category == "snp_finding",
                )
            ).fetchone()
            neurological_summary = conn.execute(
                sa.select(findings.c.pathway_level).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.category == "pathway_summary",
                    findings.c.pathway == neurological.pathway_name,
                )
            ).scalar_one()

        assert lrrk2_row is not None
        assert lrrk2_row.evidence_level == 2
        assert lrrk2_row.pathway_level == MODERATE
        assert "24-49%" in lrrk2_row.finding_text
        assert "74% by age 79" not in lrrk2_row.finding_text
        assert neurological_summary == MODERATE

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
        """Panel coverage rows are stored for all 42 SNPs."""
        _seed_variants(sample_engine, ALL_GENE_HEALTH_VARIANTS)
        result = score_gene_health_pathways(panel, sample_engine, reference_engine)
        store_gene_health_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(panel_coverage).where(panel_coverage.c.module == MODULE_NAME)
            ).fetchall()
        assert len(rows) == 42

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
                    panel_coverage.c.rsid == "rs429358",
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
