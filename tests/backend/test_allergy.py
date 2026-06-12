"""Tests for the Gene Allergy & Immune Sensitivities module (P3-60).

Covers:
  - Panel loading and dataclass construction
  - HLA proxy calling with r²/ancestry display
  - Celiac DQ2/DQ8 combined assessment (4 states)
  - Histamine metabolism combined assessment (de-emphasize flag)
  - Genotype normalization
  - SNP scoring with evidence-level gating
  - Pathway level determination (highest category)
  - Cross-module reference findings (PGx, Skin, Nutrigenomics)
  - Abacavir/HLA-B*57:01 bi-directional cross-link
  - Full scoring integration with sample DB
  - Findings storage and retrieval
  - Panel coverage tracking
  - GWAS annotation_coverage bitmask (bit 5)
  - ~30 trait finding count verification
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.allergy import (
    ELEVATED,
    MODERATE,
    MODULE_NAME,
    STANDARD,
    AllergyPanel,
    PanelSNP,
    SNPResult,
    _determine_pathway_level,
    _normalize_genotype,
    _score_snp,
    load_allergy_panel,
    score_allergy_pathways,
    store_allergy_findings,
    update_annotation_coverage_gwas,
)
from backend.annotation.engine import GWAS_BIT
from backend.db.tables import (
    annotated_variants,
    findings,
    gwas_associations,
    hla_proxy_lookup,
    panel_coverage,
    raw_variants,
    reference_metadata,
    sample_metadata_obj,
)

# ── Fixtures ──────────────────────────────────────────────────────────────

PANEL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "allergy_panel.json"
)


@pytest.fixture()
def panel() -> AllergyPanel:
    """Load the actual curated panel."""
    return load_allergy_panel(PANEL_PATH)


@pytest.fixture()
def sample_engine(tmp_path: Path) -> sa.Engine:
    """Create a sample DB with raw_variants, findings, and panel_coverage tables."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'sample.db'}")
    sample_metadata_obj.create_all(engine)
    return engine


@pytest.fixture()
def reference_engine(tmp_path: Path) -> sa.Engine:
    """Create a reference DB with gwas_associations and hla_proxy_lookup tables."""
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


def _seed_hla_proxies(engine: sa.Engine) -> None:
    """Seed the hla_proxy_lookup table with test data."""
    entries = [
        {
            "hla_allele": "HLA-B*57:01",
            "proxy_rsid": "rs2395029",
            "r_squared": 0.97,
            "ancestry_pop": "EUR",
            "clinical_context": "Abacavir hypersensitivity",
            "pmid": "18196153",
        },
        {
            "hla_allele": "HLA-B*57:01",
            "proxy_rsid": "rs2395029",
            "r_squared": 0.85,
            "ancestry_pop": "AFR",
            "clinical_context": "Abacavir hypersensitivity",
            "pmid": "18196153",
        },
        {
            "hla_allele": "HLA-B*15:02",
            "proxy_rsid": "rs144012689",
            "r_squared": 0.93,
            "ancestry_pop": "EAS",
            "clinical_context": "Carbamazepine-induced SJS/TEN",
            "pmid": "21248726",
        },
        {
            "hla_allele": "HLA-B*58:01",
            "proxy_rsid": "rs9263726",
            "r_squared": 0.91,
            "ancestry_pop": "EUR",
            "clinical_context": "Allopurinol hypersensitivity (SJS/TEN)",
            "pmid": "22286173",
        },
        {
            "hla_allele": "HLA-B*58:01",
            "proxy_rsid": "rs9263726",
            "r_squared": 0.87,
            "ancestry_pop": "EAS",
            "clinical_context": "Allopurinol hypersensitivity (SJS/TEN)",
            "pmid": "22286173",
        },
        {
            "hla_allele": "HLA-B*58:01",
            "proxy_rsid": "rs9263726",
            "r_squared": 0.78,
            "ancestry_pop": "AFR",
            "clinical_context": "Allopurinol hypersensitivity (SJS/TEN)",
            "pmid": "22286173",
        },
        {
            "hla_allele": "HLA-DQ2",
            "proxy_rsid": "rs2187668",
            "r_squared": 0.95,
            "ancestry_pop": "EUR",
            "clinical_context": "Celiac disease susceptibility (HLA-DQ2.5 haplotype)",
            "pmid": "18311140",
        },
        {
            "hla_allele": "HLA-DQ8",
            "proxy_rsid": "rs7775228",
            "r_squared": 0.89,
            "ancestry_pop": "EUR",
            "clinical_context": "Celiac disease susceptibility (HLA-DQ8 haplotype)",
            "pmid": "18311140",
        },
    ]
    with engine.begin() as conn:
        conn.execute(sa.insert(hla_proxy_lookup), entries)


# All 11 panel SNPs with their chromosome positions
ALL_ALLERGY_VARIANTS = [
    # Atopic Conditions
    ("rs20541", "5", 131995964, "GA"),  # IL13 R130Q het
    ("rs8076131", "17", 38075680, "AG"),  # ORMDL3 het
    ("rs324011", "12", 57527283, "CT"),  # STAT6 het
    # Drug Hypersensitivity
    ("rs2395029", "6", 31431272, "TG"),  # HLA-B*57:01 proxy het
    ("rs144012689", "6", 31356397, "CT"),  # HLA-B*15:02 proxy het
    ("rs1061235", "6", 29910670, "GA"),  # HLA-A*31:01 proxy het
    ("rs9263726", "6", 31355848, "CT"),  # HLA-B*58:01 proxy het
    # Food Sensitivity
    ("rs2187668", "6", 32605884, "CT"),  # HLA-DQ2 proxy het
    ("rs7775228", "6", 32713862, "CC"),  # HLA-DQ8 proxy ref
    # Histamine Metabolism
    ("rs10156191", "7", 150554592, "CT"),  # AOC1 het
    ("rs11558538", "2", 138759649, "CT"),  # HNMT het
]


# ── Panel loading tests ──────────────────────────────────────────────────


class TestPanelLoading:
    def test_load_panel_succeeds(self, panel: AllergyPanel) -> None:
        assert panel.module == "allergy"
        assert panel.version == "1.0.0"

    def test_panel_has_four_pathways(self, panel: AllergyPanel) -> None:
        assert len(panel.pathways) == 4
        pathway_ids = {p.id for p in panel.pathways}
        assert pathway_ids == {
            "atopic_conditions",
            "drug_hypersensitivity",
            "food_sensitivity",
            "histamine_metabolism",
        }

    def test_panel_all_rsids(self, panel: AllergyPanel) -> None:
        rsids = panel.all_rsids()
        assert len(rsids) == 11
        expected = {
            "rs20541",
            "rs8076131",
            "rs324011",
            "rs2395029",
            "rs144012689",
            "rs1061235",
            "rs9263726",
            "rs2187668",
            "rs7775228",
            "rs10156191",
            "rs11558538",
        }
        assert set(rsids) == expected

    def test_panel_snps_have_genotype_effects(self, panel: AllergyPanel) -> None:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                assert len(snp.genotype_effects) > 0, f"{snp.rsid} has no genotype effects"
                for gt, effect in snp.genotype_effects.items():
                    assert "category" in effect
                    assert "effect_summary" in effect
                    assert effect["category"] in (ELEVATED, MODERATE, STANDARD)

    def test_panel_has_special_calling(self, panel: AllergyPanel) -> None:
        assert panel.special_calling is not None
        assert "HLA_proxy_calling" in panel.special_calling
        assert "celiac_DQ2_DQ8_combined" in panel.special_calling
        assert "histamine_combined_assessment" in panel.special_calling

    def test_load_nonexistent_panel_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_allergy_panel(Path("/nonexistent/panel.json"))

    def test_hla_proxy_snps_have_metadata(self, panel: AllergyPanel) -> None:
        """Drug hypersensitivity and food sensitivity SNPs have hla_proxy metadata."""
        hla_snps = []
        for pathway in panel.pathways:
            if pathway.id in ("drug_hypersensitivity", "food_sensitivity"):
                for snp in pathway.snps:
                    assert snp.hla_proxy is not None, f"{snp.rsid} missing hla_proxy"
                    assert "hla_allele" in snp.hla_proxy
                    hla_snps.append(snp.rsid)
        assert len(hla_snps) == 6  # 4 drug + 2 celiac

    def test_cross_module_links_present(self, panel: AllergyPanel) -> None:
        """Cross-links: abacavir→PGx, IL13→skin, celiac→nutrigenomics."""
        cross_modules: dict[str, str] = {}
        for pathway in panel.pathways:
            for snp in pathway.snps:
                if snp.cross_module:
                    cross_modules[snp.rsid] = snp.cross_module["module"]

        assert cross_modules.get("rs2395029") == "pharmacogenomics"  # abacavir
        assert cross_modules.get("rs20541") == "skin"  # IL13 R130Q
        assert cross_modules.get("rs2187668") == "nutrigenomics"  # celiac DQ2

    def test_histamine_snps_are_star_1(self, panel: AllergyPanel) -> None:
        """Histamine metabolism SNPs are ★☆ evidence (candidate gene level)."""
        for pathway in panel.pathways:
            if pathway.id == "histamine_metabolism":
                for snp in pathway.snps:
                    assert snp.evidence_level == 1, f"{snp.rsid} should be evidence_level=1"

    def test_drug_hypersensitivity_evidence_levels(self, panel: AllergyPanel) -> None:
        """Drug hypersensitivity HLA proxies have ★★★-★★★★ evidence."""
        for pathway in panel.pathways:
            if pathway.id == "drug_hypersensitivity":
                for snp in pathway.snps:
                    assert snp.evidence_level >= 3, f"{snp.rsid} should have evidence_level >= 3"


# ── Genotype normalization tests ─────────────────────────────────────────


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


# ── SNP scoring tests ────────────────────────────────────────────────────


class TestSNPScoring:
    def _get_snp(self, panel: AllergyPanel, rsid: str) -> PanelSNP:
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == rsid:
                    return snp
        pytest.fail(f"SNP {rsid} not found")

    def test_il13_het_moderate(self, panel: AllergyPanel) -> None:
        """IL13 R130Q het (GA) → Moderate."""
        snp = self._get_snp(panel, "rs20541")
        result = _score_snp(snp, "GA")
        assert result.category == MODERATE
        assert result.present_in_sample is True

    def test_il13_hom_elevated(self, panel: AllergyPanel) -> None:
        """IL13 R130Q hom (AA) → Elevated (evidence_level=2 ≥ 2)."""
        snp = self._get_snp(panel, "rs20541")
        result = _score_snp(snp, "AA")
        assert result.category == ELEVATED

    def test_il13_ref_standard(self, panel: AllergyPanel) -> None:
        """IL13 R130Q ref (GG) → Standard."""
        snp = self._get_snp(panel, "rs20541")
        result = _score_snp(snp, "GG")
        assert result.category == STANDARD

    def test_hla_b5701_proxy_het_elevated(self, panel: AllergyPanel) -> None:
        """HLA-B*57:01 proxy het (TG) → Elevated (evidence_level=4)."""
        snp = self._get_snp(panel, "rs2395029")
        result = _score_snp(snp, "TG")
        assert result.category == ELEVATED
        assert result.hla_proxy is not None
        assert result.hla_proxy["hla_allele"] == "HLA-B*57:01"

    def test_hla_b5701_proxy_ref_standard(self, panel: AllergyPanel) -> None:
        """HLA-B*57:01 proxy ref (TT) → Standard."""
        snp = self._get_snp(panel, "rs2395029")
        result = _score_snp(snp, "TT")
        assert result.category == STANDARD

    def test_celiac_dq2_het_moderate(self, panel: AllergyPanel) -> None:
        """Celiac DQ2 proxy het (CT) → Moderate."""
        snp = self._get_snp(panel, "rs2187668")
        result = _score_snp(snp, "CT")
        assert result.category == MODERATE

    def test_celiac_dq2_hom_elevated(self, panel: AllergyPanel) -> None:
        """Celiac DQ2 proxy hom (TT) → Elevated."""
        snp = self._get_snp(panel, "rs2187668")
        result = _score_snp(snp, "TT")
        assert result.category == ELEVATED

    def test_histamine_aoc1_hom_moderate_capped(self, panel: AllergyPanel) -> None:
        """AOC1 Thr16Met hom (TT) → Moderate (★☆ caps at Moderate)."""
        snp = self._get_snp(panel, "rs10156191")
        result = _score_snp(snp, "TT")
        assert result.category == MODERATE  # capped from panel definition

    def test_not_genotyped_returns_standard(self, panel: AllergyPanel) -> None:
        """Missing genotype → Standard with not present flag."""
        snp = self._get_snp(panel, "rs20541")
        result = _score_snp(snp, None)
        assert result.category == STANDARD
        assert result.present_in_sample is False

    def test_unknown_genotype_returns_standard(self, panel: AllergyPanel) -> None:
        """Unknown genotype → Standard."""
        snp = self._get_snp(panel, "rs20541")
        result = _score_snp(snp, "XY")
        assert result.category == STANDARD
        assert result.present_in_sample is True

    def test_reversed_genotype_lookup(self, panel: AllergyPanel) -> None:
        """Reversed genotype (AG vs GA) still works."""
        snp = self._get_snp(panel, "rs20541")
        result = _score_snp(snp, "AG")
        assert result.category == MODERATE


# ── Pathway level determination tests ────────────────────────────────────


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


# ── Celiac DQ2/DQ8 combined assessment tests ─────────────────────────────


class TestCeliacCombined:
    def test_neither_dq2_nor_dq8(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Both ref genotypes → 'neither' → Low Celiac Risk."""
        _seed_variants(
            sample_engine,
            [
                ("rs2187668", "6", 32605884, "CC"),  # DQ2 ref
                ("rs7775228", "6", 32713862, "CC"),  # DQ8 ref
            ],
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        assert result.celiac_combined is not None
        assert result.celiac_combined.state == "neither"
        assert "Low Celiac Risk" in result.celiac_combined.label

    def test_dq2_only(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """DQ2 het + DQ8 ref → 'dq2_only'."""
        _seed_variants(
            sample_engine,
            [
                ("rs2187668", "6", 32605884, "CT"),  # DQ2 het
                ("rs7775228", "6", 32713862, "CC"),  # DQ8 ref
            ],
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        assert result.celiac_combined is not None
        assert result.celiac_combined.state == "dq2_only"

    def test_dq8_only(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """DQ2 ref + DQ8 het → 'dq8_only'."""
        _seed_variants(
            sample_engine,
            [
                ("rs2187668", "6", 32605884, "CC"),  # DQ2 ref
                ("rs7775228", "6", 32713862, "CT"),  # DQ8 het
            ],
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        assert result.celiac_combined is not None
        assert result.celiac_combined.state == "dq8_only"

    def test_both_dq2_and_dq8(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """DQ2 het + DQ8 het → 'both'."""
        _seed_variants(
            sample_engine,
            [
                ("rs2187668", "6", 32605884, "CT"),  # DQ2 het
                ("rs7775228", "6", 32713862, "CT"),  # DQ8 het
            ],
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        assert result.celiac_combined is not None
        assert result.celiac_combined.state == "both"

    def test_celiac_evidence_level_3(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Celiac combined assessment is at ★★★☆."""
        _seed_variants(sample_engine, [("rs2187668", "6", 32605884, "CT")])
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        assert result.celiac_combined is not None
        assert result.celiac_combined.evidence_level == 3

    def test_neither_proxy_genotyped_is_indeterminate(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Both proxies untyped → 'indeterminate', NOT the 'neither' rule-out (issue #27).

        A consumer array that types neither rs2187668 nor rs7775228 has no celiac
        HLA coverage. Missing data is not a negative result, so the >99% NPV
        "Low Celiac Risk" framing must be withheld to avoid false reassurance.
        """
        _seed_variants(sample_engine, [("rs1801133", "1", 11856378, "GG")])  # unrelated SNP
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        assert result.celiac_combined is not None
        assert result.celiac_combined.state == "indeterminate"
        assert "Low Celiac Risk" not in result.celiac_combined.label
        assert "Undetermined" in result.celiac_combined.label

    def test_one_proxy_missing_is_indeterminate(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """DQ8 reference but DQ2 untyped → 'indeterminate' (cannot rule out via one proxy)."""
        _seed_variants(sample_engine, [("rs7775228", "6", 32713862, "CC")])  # DQ8 ref only
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        assert result.celiac_combined is not None
        assert result.celiac_combined.state == "indeterminate"

    def test_no_call_proxy_is_indeterminate(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """A no-call ('--') at a proxy is untyped → 'indeterminate', not 'neither'."""
        _seed_variants(
            sample_engine,
            [
                ("rs2187668", "6", 32605884, "CC"),  # DQ2 ref
                ("rs7775228", "6", 32713862, "--"),  # DQ8 no-call
            ],
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        assert result.celiac_combined is not None
        assert result.celiac_combined.state == "indeterminate"


# ── Histamine combined assessment tests ──────────────────────────────────


class TestHistamineCombined:
    def test_both_variants_detected(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Both AOC1 and HNMT het → combined reduction text."""
        _seed_variants(
            sample_engine,
            [
                ("rs10156191", "7", 150554592, "CT"),
                ("rs11558538", "2", 138759649, "CT"),
            ],
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        assert result.histamine_combined is not None
        assert "Both AOC1" in result.histamine_combined.combined_text
        assert result.histamine_combined.de_emphasize is True

    def test_aoc1_only(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """AOC1 het only → AOC1-only text."""
        _seed_variants(
            sample_engine,
            [
                ("rs10156191", "7", 150554592, "CT"),
                ("rs11558538", "2", 138759649, "CC"),
            ],
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        assert result.histamine_combined is not None
        assert "AOC1" in result.histamine_combined.combined_text
        assert "HNMT" not in result.histamine_combined.combined_text

    def test_neither_variant(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Both ref → standard text."""
        _seed_variants(
            sample_engine,
            [
                ("rs10156191", "7", 150554592, "CC"),
                ("rs11558538", "2", 138759649, "CC"),
            ],
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        assert result.histamine_combined is not None
        assert "No histamine" in result.histamine_combined.combined_text


# ── HLA proxy lookup tests ──────────────────────────────────────────────


class TestHLAProxyLookup:
    def test_hla_proxy_info_fetched(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """HLA proxy info is fetched from reference DB."""
        _seed_variants(sample_engine, ALL_ALLERGY_VARIANTS)
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        assert "rs2395029" in result.hla_proxy_info
        info = result.hla_proxy_info["rs2395029"]
        assert info.hla_allele == "HLA-B*57:01"
        assert "EUR" in info.r_squared_by_pop
        assert info.r_squared_by_pop["EUR"] == pytest.approx(0.97)

    def test_hla_proxy_multiple_ancestries(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """HLA proxy info contains multiple ancestry r² values."""
        _seed_variants(sample_engine, ALL_ALLERGY_VARIANTS)
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        info = result.hla_proxy_info["rs2395029"]
        assert "AFR" in info.r_squared_by_pop
        assert info.r_squared_by_pop["AFR"] == pytest.approx(0.85)


# ── Cross-module findings tests ──────────────────────────────────────────


class TestCrossModuleFindings:
    def test_abacavir_pgx_cross_link(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """HLA-B*57:01 proxy carrier → PGx cross-link."""
        _seed_variants(
            sample_engine,
            [("rs2395029", "6", 31431272, "TG")],
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        pgx_links = [
            f for f in result.cross_module_findings if f.target_module == "pharmacogenomics"
        ]
        assert len(pgx_links) >= 1
        assert "HLA-B*57:01" in pgx_links[0].finding_text

    def test_il13_skin_cross_link(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """IL13 R130Q carrier → Skin cross-link."""
        _seed_variants(
            sample_engine,
            [("rs20541", "5", 131995964, "GA")],
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        skin_links = [f for f in result.cross_module_findings if f.target_module == "skin"]
        assert len(skin_links) == 1
        assert "IL13" in skin_links[0].finding_text

    def test_celiac_nutrigenomics_cross_link(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Celiac DQ2 carrier → Nutrigenomics cross-link."""
        _seed_variants(
            sample_engine,
            [("rs2187668", "6", 32605884, "CT")],
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        nutri_links = [
            f for f in result.cross_module_findings if f.target_module == "nutrigenomics"
        ]
        assert len(nutri_links) >= 1

    def test_standard_genotype_no_cross_link(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Ref genotype → no cross-module findings."""
        _seed_variants(
            sample_engine,
            [("rs20541", "5", 131995964, "GG")],  # ref
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        skin_links = [f for f in result.cross_module_findings if f.target_module == "skin"]
        assert len(skin_links) == 0

    def test_cross_module_deduplication(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Only one cross-link per gene+target combination."""
        _seed_variants(
            sample_engine,
            [
                ("rs144012689", "6", 31356397, "CT"),  # HLA-B*15:02 → pgx
                ("rs1061235", "6", 29910670, "GA"),  # HLA-A*31:01 → pgx
            ],
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        pgx_links = [
            f for f in result.cross_module_findings if f.target_module == "pharmacogenomics"
        ]
        # HLA-B and HLA-A are different genes, so two cross-links are expected
        genes = {f.gene for f in pgx_links}
        assert "HLA-B" in genes
        assert "HLA-A" in genes


# ── Full scoring integration tests ──────────────────────────────────────


class TestFullScoring:
    def test_all_variants_scored(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """All 11 panel SNPs are scored when present."""
        _seed_variants(sample_engine, ALL_ALLERGY_VARIANTS)
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        total_snps = sum(len(pr.snp_results) for pr in result.pathway_results)
        assert total_snps == 11

    def test_four_pathways_scored(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        _seed_variants(sample_engine, ALL_ALLERGY_VARIANTS)
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        assert len(result.pathway_results) == 4

    def test_drug_hypersensitivity_elevated(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Drug hypersensitivity pathway with carrier genotypes → Elevated."""
        _seed_variants(sample_engine, ALL_ALLERGY_VARIANTS)
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        drug_pr = next(
            pr for pr in result.pathway_results if pr.pathway_id == "drug_hypersensitivity"
        )
        assert drug_pr.level == ELEVATED

    def test_histamine_pathway_capped_at_moderate(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Histamine metabolism ★☆ evidence → pathway level ≤ Moderate."""
        _seed_variants(
            sample_engine,
            [
                ("rs10156191", "7", 150554592, "TT"),  # AOC1 hom
                ("rs11558538", "2", 138759649, "TT"),  # HNMT hom
            ],
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        hist_pr = next(
            pr for pr in result.pathway_results if pr.pathway_id == "histamine_metabolism"
        )
        assert hist_pr.level == MODERATE  # capped by ★☆ evidence

    def test_empty_sample_all_standard(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """No genotypes → all pathways Standard."""
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        for pr in result.pathway_results:
            assert pr.level == STANDARD


# ── Findings storage tests ──────────────────────────────────────────────


class TestFindingsStorage:
    def test_findings_stored(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Findings are stored in the sample database."""
        _seed_variants(sample_engine, ALL_ALLERGY_VARIANTS)
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        count = store_allergy_findings(result, sample_engine)
        assert count > 0

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == MODULE_NAME)
            ).fetchall()
        assert len(rows) == count

    def test_pathway_summaries_stored(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """4 pathway summary findings are stored."""
        _seed_variants(sample_engine, ALL_ALLERGY_VARIANTS)
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        store_allergy_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            summaries = conn.execute(
                sa.select(findings).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.category == "pathway_summary",
                )
            ).fetchall()
        assert len(summaries) == 4

    def test_celiac_combined_finding_stored(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Celiac combined assessment finding is stored."""
        _seed_variants(sample_engine, ALL_ALLERGY_VARIANTS)
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        store_allergy_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            celiac = conn.execute(
                sa.select(findings).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.category == "celiac_combined",
                )
            ).fetchall()
        assert len(celiac) == 1
        detail = json.loads(celiac[0].detail_json)
        assert "state" in detail

    def test_histamine_combined_finding_stored(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Histamine combined assessment finding is stored."""
        _seed_variants(sample_engine, ALL_ALLERGY_VARIANTS)
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        store_allergy_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            histamine = conn.execute(
                sa.select(findings).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.category == "histamine_combined",
                )
            ).fetchall()
        assert len(histamine) == 1
        detail = json.loads(histamine[0].detail_json)
        assert detail.get("de_emphasize") is True

    def test_hla_proxy_lookup_in_findings(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """HLA proxy r²/ancestry data is embedded in SNP finding detail_json."""
        _seed_variants(
            sample_engine,
            [("rs2395029", "6", 31431272, "TG")],
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        store_allergy_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            snp_findings = conn.execute(
                sa.select(findings).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.rsid == "rs2395029",
                    findings.c.category == "snp_finding",
                )
            ).fetchall()
        assert len(snp_findings) == 1
        detail = json.loads(snp_findings[0].detail_json)
        assert "hla_proxy_lookup" in detail
        assert detail["hla_proxy_lookup"]["hla_allele"] == "HLA-B*57:01"
        assert "EUR" in detail["hla_proxy_lookup"]["r_squared_by_pop"]

    def test_hla_b5801_negative_proxy_caveat_in_pathway_detail(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Negative rs9263726 calls keep the HLA-B*58:01 proxy limitation visible."""
        _seed_variants(
            sample_engine,
            [("rs9263726", "6", 31355848, "CC")],
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        store_allergy_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            drug_summary = conn.execute(
                sa.select(findings).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.category == "pathway_summary",
                    findings.c.pathway == "Drug Hypersensitivity",
                )
            ).fetchone()
            positive_proxy_finding = conn.execute(
                sa.select(findings).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.category == "snp_finding",
                    findings.c.rsid == "rs9263726",
                )
            ).fetchone()

        assert drug_summary is not None
        assert positive_proxy_finding is None
        detail = json.loads(drug_summary.detail_json)
        rs926_detail = next(d for d in detail["snp_details"] if d["rsid"] == "rs9263726")
        assert rs926_detail["category"] == STANDARD
        assert "Low risk of allopurinol" not in rs926_detail["effect_summary"]
        assert "does not rule out HLA-B*58:01" in rs926_detail["effect_summary"]
        assert "does not exclude the HLA allele" in rs926_detail["hla_proxy_caveat"]
        assert rs926_detail["hla_proxy_lookup"]["r_squared_by_pop"]["AFR"] == pytest.approx(0.78)

    def test_rerun_clears_previous(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Re-running scoring clears previous findings."""
        _seed_variants(sample_engine, ALL_ALLERGY_VARIANTS)
        _seed_hla_proxies(reference_engine)

        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        count1 = store_allergy_findings(result, sample_engine)

        # Re-run
        result2 = score_allergy_pathways(panel, sample_engine, reference_engine)
        count2 = store_allergy_findings(result2, sample_engine)

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
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Cross-module findings are stored."""
        _seed_variants(sample_engine, ALL_ALLERGY_VARIANTS)
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        store_allergy_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            cross = conn.execute(
                sa.select(findings).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.category == "cross_module",
                )
            ).fetchall()
        assert len(cross) > 0


# ── Panel coverage tests ────────────────────────────────────────────────


class TestPanelCoverage:
    def test_coverage_stored(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Panel coverage rows are stored for all 11 SNPs."""
        _seed_variants(sample_engine, ALL_ALLERGY_VARIANTS)
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        store_allergy_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(panel_coverage).where(panel_coverage.c.module == MODULE_NAME)
            ).fetchall()
        assert len(rows) == 11

    def test_called_status(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Genotyped SNPs have 'called' status."""
        _seed_variants(sample_engine, ALL_ALLERGY_VARIANTS)
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        store_allergy_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(panel_coverage).where(
                    panel_coverage.c.module == MODULE_NAME,
                    panel_coverage.c.rsid == "rs20541",
                )
            ).fetchone()
        assert row is not None
        assert row.coverage_status == "called"

    def test_not_on_array_status(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Missing SNPs have 'not_on_array' status."""
        # Only seed one variant
        _seed_variants(sample_engine, [("rs20541", "5", 131995964, "GA")])
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        store_allergy_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(panel_coverage).where(
                    panel_coverage.c.module == MODULE_NAME,
                    panel_coverage.c.rsid == "rs8076131",
                )
            ).fetchone()
        assert row is not None
        assert row.coverage_status == "not_on_array"

    def test_no_call_status(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """No-call genotypes have 'no_call' status."""
        _seed_variants(sample_engine, [("rs20541", "5", 131995964, "--")])
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        store_allergy_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(panel_coverage).where(
                    panel_coverage.c.module == MODULE_NAME,
                    panel_coverage.c.rsid == "rs20541",
                )
            ).fetchone()
        assert row is not None
        assert row.coverage_status == "no_call"


# ── GWAS annotation_coverage bitmask tests ──────────────────────────────


class TestAnnotationCoverage:
    def test_gwas_bitmask_set(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """GWAS-matched variants get annotation_coverage bit 5 set."""
        _seed_variants(sample_engine, ALL_ALLERGY_VARIANTS)
        _seed_hla_proxies(reference_engine)

        # Seed annotated_variants for one rsid
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rs20541",
                        "chrom": "5",
                        "pos": 131995964,
                        "annotation_coverage": 0,
                    }
                ],
            )

        # Seed GWAS association
        _seed_gwas(reference_engine, [("rs20541", "asthma")])

        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        updated = update_annotation_coverage_gwas(result, sample_engine)
        assert updated == 1

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs20541"
                )
            ).fetchone()
        assert row is not None
        assert (row.annotation_coverage & GWAS_BIT) == GWAS_BIT

    def test_gwas_bitmask_or_preserves_existing(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """GWAS bitmask OR preserves existing bits."""
        _seed_variants(sample_engine, ALL_ALLERGY_VARIANTS)
        _seed_hla_proxies(reference_engine)

        # Seed with existing bitmask
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rs20541",
                        "chrom": "5",
                        "pos": 131995964,
                        "annotation_coverage": 3,  # VEP + ClinVar
                    }
                ],
            )

        _seed_gwas(reference_engine, [("rs20541", "asthma")])

        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        update_annotation_coverage_gwas(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs20541"
                )
            ).fetchone()
        assert row is not None
        assert (row.annotation_coverage & GWAS_BIT) == GWAS_BIT
        assert (row.annotation_coverage & 3) == 3  # existing bits preserved

    def test_no_gwas_matches_zero_updates(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """No GWAS matches → zero updates."""
        _seed_variants(sample_engine, ALL_ALLERGY_VARIANTS)
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        updated = update_annotation_coverage_gwas(result, sample_engine)
        assert updated == 0
