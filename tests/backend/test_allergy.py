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
    INDETERMINATE,
    MODERATE,
    MODULE_NAME,
    STANDARD,
    AllergyPanel,
    PanelSNP,
    SNPResult,
    _determine_pathway_level,
    _normalize_genotype,
    _positive_hla_proxy_specificity_caveat,
    _score_snp,
    _stored_snp_detail,
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


def _hla_proxy_seed_entries() -> list[dict]:
    """HLA proxy lookup seed rows; PMIDs mirror the curated production provenance."""
    return [
        {
            "hla_allele": "HLA-B*57:01",
            "proxy_rsid": "rs2395029",
            "r_squared": 0.97,
            "ancestry_pop": "EUR",
            "clinical_context": "Abacavir hypersensitivity",
            "pmid": "18256392",
        },
        {
            "hla_allele": "HLA-B*57:01",
            "proxy_rsid": "rs2395029",
            "r_squared": 0.85,
            "ancestry_pop": "AFR",
            "clinical_context": "Abacavir hypersensitivity",
            "pmid": "18256392",
        },
        {
            "hla_allele": "HLA-B*15:02",
            "proxy_rsid": "rs144012689",
            "r_squared": 0.93,
            "ancestry_pop": "EAS",
            "clinical_context": "Carbamazepine-induced SJS/TEN",
            "pmid": "15057820",
        },
        {
            "hla_allele": "HLA-B*58:01",
            "proxy_rsid": "rs9263726",
            "r_squared": 0.886,
            "ancestry_pop": "Han Chinese",
            "clinical_context": "Allopurinol hypersensitivity (SJS/TEN)",
            "pmid": "30080910",
        },
        {
            "hla_allele": "HLA-B*58:01",
            "proxy_rsid": "rs9263726",
            "r_squared": 0.606,
            "ancestry_pop": "Tibetan",
            "clinical_context": "Allopurinol hypersensitivity (SJS/TEN)",
            "pmid": "30080910",
        },
        {
            "hla_allele": "HLA-B*58:01",
            "proxy_rsid": "rs9263726",
            "r_squared": 0.622,
            "ancestry_pop": "Hui",
            "clinical_context": "Allopurinol hypersensitivity (SJS/TEN)",
            "pmid": "30080910",
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


def _seed_hla_proxies(engine: sa.Engine) -> None:
    """Seed the hla_proxy_lookup table with test data."""
    with engine.begin() as conn:
        conn.execute(sa.insert(hla_proxy_lookup), _hla_proxy_seed_entries())


# All 14 panel SNPs with their chromosome positions
ALL_ALLERGY_VARIANTS = [
    # Atopic Conditions
    ("rs20541", "5", 131995964, "GA"),  # IL13 R130Q het
    ("rs8076131", "17", 38075680, "AG"),  # ORMDL3 het
    ("rs324011", "12", 57527283, "CT"),  # STAT6 het
    # Drug Hypersensitivity
    ("rs2395029", "6", 31431272, "TG"),  # HLA-B*57:01 proxy het
    ("rs144012689", "6", 31356397, "CT"),  # HLA-B*15:02 proxy het
    ("rs1061235", "6", 29910670, "AT"),  # HLA-A*31:01 proxy het (A/T SNP, T=risk; #545)
    ("rs9263726", "6", 31355848, "CT"),  # HLA-B*58:01 proxy het
    # Food Sensitivity
    ("rs2187668", "6", 32605884, "CT"),  # HLA-DQ2 proxy het
    ("rs7775228", "6", 32713862, "CC"),  # HLA-DQ8 proxy ref
    # Histamine Metabolism
    ("rs10156191", "7", 150554592, "CT"),  # AOC1 Thr16Met het
    ("rs1049742", "7", 150554553, "CT"),  # AOC1 Ser332Phe het (#386)
    ("rs2052129", "7", 150548972, "GT"),  # AOC1 c.-691G>T promoter het (#386)
    ("rs1049793", "7", 150557665, "CG"),  # AOC1 His664Asp het (palindromic C/G, #436)
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
        assert len(rsids) == 14
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
            "rs1049742",
            "rs2052129",
            "rs1049793",
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

    def test_unmodeled_allele_genotype_withheld_as_indeterminate(
        self, panel: AllergyPanel
    ) -> None:
        """#608: a present, real-nucleotide genotype carrying an allele the locus
        does not model (IL13 rs20541 is A/G; observed ``GT`` carries an unmodeled
        ``T``) is withheld as Indeterminate, not silently scored Standard (which
        would hide the carrier as 'no effect'). A non-nucleotide no-call is not an
        unmodeled allele and still falls through to Standard."""
        snp = self._get_snp(panel, "rs20541")
        for gt in ("GT", "TG"):
            result = _score_snp(snp, gt)
            assert result.category == INDETERMINATE, gt
            assert result.present_in_sample is True
            assert "does not model" in result.effect_summary, gt
        assert _score_snp(snp, "--").category == STANDARD

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
        """Both ref → neutral coverage text, NOT a 'standard catabolism' reassurance.

        Absence of the tagged AOC1/HNMT risk genotypes must not be framed as
        normal/expected histamine catabolism: this panel covers all four main
        AOC1 DAO-deficiency variants (#386/#436) and DAO genotypes alone do not
        establish or exclude histamine intolerance (Maintz 2011, PMID 21488903;
        van Odijk 2023, PMID 37447214). See #307.
        """
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
        text = result.histamine_combined.combined_text
        assert "does not rule out" in text
        # The overstated reassurance phrasings must be gone.
        assert "Standard histamine catabolism expected" not in text
        assert "No histamine metabolism variants detected" not in text

    def test_new_aoc1_snps_present_with_verified_alleles(self, panel: AllergyPanel) -> None:
        """The two AOC1 DAO-deficiency SNPs added in #386 carry verified alleles."""
        histamine = next(p for p in panel.pathways if p.id == "histamine_metabolism")
        by_rsid = {s.rsid: s for s in histamine.snps}

        assert by_rsid["rs1049742"].gene == "AOC1"
        assert (by_rsid["rs1049742"].risk_allele, by_rsid["rs1049742"].ref_allele) == ("T", "C")
        assert by_rsid["rs2052129"].gene == "AOC1"
        assert (by_rsid["rs2052129"].risk_allele, by_rsid["rs2052129"].ref_allele) == ("T", "G")
        # Both are candidate-gene (★☆) and cap at Moderate (no Elevated genotype).
        for rsid in ("rs1049742", "rs2052129"):
            snp = by_rsid[rsid]
            assert snp.evidence_level == 1
            cats = {e["category"] for e in snp.genotype_effects.values()}
            assert cats <= {"Standard", "Moderate"}
            assert "21488903" in snp.pmids  # Maintz 2011 DAO-activity SNP study

    def test_rs1049793_present_with_verified_alleles(self, panel: AllergyPanel) -> None:
        """#436: the 4th AOC1 DAO-deficiency SNP rs1049793 (His664Asp) is present,
        plus-strand risk = minor allele G (Ensembl GRCh37 chr7:150557665)."""
        histamine = next(p for p in panel.pathways if p.id == "histamine_metabolism")
        snp = next(s for s in histamine.snps if s.rsid == "rs1049793")
        assert snp.gene == "AOC1"
        assert (snp.risk_allele, snp.ref_allele) == ("G", "C")
        assert snp.evidence_level == 1
        # Pin the curated, NCBI+Consensus-verified DAO-deficiency citation set:
        # 19450133 García-Martín 2009 (highlights rs1049793 His664Asp for its
        # functional effect), 37359379 Okutan 2023 (4-SNP cumulative load), 40004469
        # Fortes Marin 2025 (4-SNP newborn prevalence).
        assert snp.pmids == ["19450133", "37359379", "40004469"]

    def test_rs1049793_homozygotes_are_strand_indeterminate(self, panel: AllergyPanel) -> None:
        """#436: rs1049793 is a C/G palindromic SNP, so BOTH homozygotes (CC and GG)
        are strand-unresolvable from array data → withheld as Indeterminate, never a
        possibly strand-flipped call."""
        histamine = next(p for p in panel.pathways if p.id == "histamine_metabolism")
        snp = next(s for s in histamine.snps if s.rsid == "rs1049793")
        for gt in ("CC", "GG"):
            result = _score_snp(snp, gt)
            assert result.category == INDETERMINATE, gt
            assert "palindromic" in result.effect_summary.lower()
            assert result.present_in_sample is True

    def test_rs1049793_heterozygote_scores_normally(self, panel: AllergyPanel) -> None:
        """#436: the CG heterozygote is strand-resolvable (one C + one G regardless
        of strand), so it scores its curated Moderate category, not Indeterminate."""
        histamine = next(p for p in panel.pathways if p.id == "histamine_metabolism")
        snp = next(s for s in histamine.snps if s.rsid == "rs1049793")
        result = _score_snp(snp, "CG")
        assert result.category == MODERATE
        assert result.category != INDETERMINATE

    def test_strand_indeterminate_excluded_from_pathway_level(self, panel: AllergyPanel) -> None:
        """#436: a pathway whose only called SNP is strand-indeterminate stays
        Standard (the Indeterminate runtime category must not become a pathway level)."""
        histamine = next(p for p in panel.pathways if p.id == "histamine_metabolism")
        snp = next(s for s in histamine.snps if s.rsid == "rs1049793")
        indeterminate = _score_snp(snp, "GG")
        assert indeterminate.category == INDETERMINATE
        assert _determine_pathway_level([indeterminate]) == STANDARD

    def test_indeterminate_aoc1_excluded_from_cumulative_load(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """#436: a strand-indeterminate rs1049793 homozygote has an unresolvable
        dosage, so it must NOT contribute to the cumulative AOC1 risk-allele load."""
        _seed_variants(sample_engine, [("rs1049793", "7", 150557665, "GG")])
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        assert result.histamine_combined is not None
        # The GG call is withheld, so no AOC1 risk alleles are counted.
        assert result.histamine_combined.aoc1_risk_allele_count == 0
        assert result.histamine_combined.aoc1_snps_assessed == 0

    def test_cumulative_aoc1_load(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Cumulative AOC1 risk-allele + homozygous load is tallied across the 3 SNPs (#386)."""
        _seed_variants(
            sample_engine,
            [
                ("rs10156191", "7", 150554592, "TT"),  # homozygous risk → 2 alleles
                ("rs1049742", "7", 150554553, "CT"),  # 1 allele
                ("rs2052129", "7", 150548972, "GT"),  # 1 allele
                ("rs11558538", "2", 138759649, "CC"),  # HNMT ref
            ],
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        hc = result.histamine_combined
        assert hc is not None
        assert hc.aoc1_snps_assessed == 3
        assert hc.aoc1_risk_allele_count == 4  # 2 + 1 + 1
        assert hc.aoc1_homozygous_risk_count == 1  # rs10156191 TT
        assert "Cumulative AOC1 load: 4 DAO-deficiency risk allele(s)" in hc.combined_text
        assert "1 homozygous" in hc.combined_text
        # AOC1-only (HNMT ref): mentions AOC1, not HNMT.
        assert "AOC1" in hc.combined_text and "HNMT" not in hc.combined_text


# ── HLA-A*31:01 proxy allele-direction regression (#545) ────────────────────


class TestHLAA31ProxyAlleleDirection:
    """#545: rs1061235 (HLA-A*31:01 / carbamazepine proxy) was encoded with the
    risk allele inverted and a non-existent ``G`` allele (risk=A/ref=G, keyed
    GG/GA/AG/AA), so the common homozygote was falsely flagged a carbamazepine
    contraindication and true heterozygous carriers were silently dropped.

    rs1061235 is an **A/T** SNP (Ensembl GRCh37 6:29913298; ancestral/common A,
    minor T MAF 0.085). The **T** allele tags HLA-A*31:01 (Thorstensen 2014:
    homozygous-A = non-carrier, carriers are heterozygous). Because A/T is
    palindromic, array strand cannot be resolved for the homozygotes, so the
    correct, safe behavior is: het carriers Elevated; both homozygotes withheld
    as strand-Indeterminate (never a flipped call). The dangerous false-positive
    is gone either way.
    """

    def _snp(self, panel: AllergyPanel) -> PanelSNP:
        return next(s for pw in panel.pathways for s in pw.snps if s.rsid == "rs1061235")

    def test_encoded_as_real_at_snp_with_correct_direction(self, panel: AllergyPanel) -> None:
        snp = self._snp(panel)
        assert snp.gene == "HLA-A"
        # Real A/T alleles, T = HLA-A*31:01 tag (risk), A = common (ref). No G.
        assert (snp.risk_allele, snp.ref_allele) == ("T", "A")
        assert set(snp.genotype_effects) == {"AA", "AT", "TA", "TT"}
        assert "G" not in "".join(snp.genotype_effects)

    def test_common_homozygote_is_not_a_false_carbamazepine_flag(
        self, panel: AllergyPanel
    ) -> None:
        """The common AA homozygote (~84% of people) must NOT be flagged Elevated
        with a carbamazepine contraindication. As a palindromic A/T homozygote it
        is withheld as strand-Indeterminate, not the old inverted Elevated."""
        result = _score_snp(self._snp(panel), "AA")
        assert result.category == INDETERMINATE
        assert result.category != ELEVATED
        assert "should not be prescribed" not in result.effect_summary.lower()
        assert "palindromic" in result.effect_summary.lower()

    def test_heterozygous_carrier_is_elevated(self, panel: AllergyPanel) -> None:
        """The true HLA-A*31:01 carrier is heterozygous (A/T); het is strand-
        resolvable, so it is correctly flagged Elevated (was previously dropped)."""
        for gt in ("AT", "TA"):
            result = _score_snp(self._snp(panel), gt)
            assert result.category == ELEVATED, gt
            assert "carrier" in result.effect_summary.lower()

    def test_variant_homozygote_withheld_as_indeterminate(self, panel: AllergyPanel) -> None:
        """The rare TT homozygote is also a palindromic homozygote → withheld
        (cannot distinguish plus-strand TT carrier from minus-strand AA)."""
        result = _score_snp(self._snp(panel), "TT")
        assert result.category == INDETERMINATE


# ── HLA-A*31:01 proxy specificity caveat (#611) ─────────────────────────────


class TestHLAA31ProxySpecificityCaveat:
    """#611 (follow-up to #545): rs1061235 is an imperfect HLA-A*31:01 tag SNP — it
    cross-reacts with HLA-A*33 (false positives) and is less reliable in Native
    American ancestry. With the direction fixed (#545), surface that specificity
    caveat alongside a positive (carrier) call so it isn't read as a confirmed
    HLA-A*31:01 result.
    """

    def _snp(self, panel: AllergyPanel) -> PanelSNP:
        return next(s for pw in panel.pathways for s in pw.snps if s.rsid == "rs1061235")

    def test_panel_records_specificity_metadata(self, panel: AllergyPanel) -> None:
        proxy = self._snp(panel).hla_proxy
        assert proxy is not None
        assert proxy["false_positive_mechanism"] == "HLA-A*33 cross-reactivity"
        assert proxy["reduced_ancestry"] == "Native American"
        assert proxy["better_proxy_for_reduced_ancestry"] == "rs17179220"

    def test_coverage_note_surfaces_caveat_with_citations(self, panel: AllergyPanel) -> None:
        note = (self._snp(panel).coverage_note or "").lower()
        assert "hla-a*33" in note
        assert "native american" in note
        assert "confirmatory" in note
        # The scientific claim carries its citation with it (user-facing).
        for cite in ("krause", "buchner", "fernandes"):
            assert cite in note, cite

    def test_caveat_surfaced_for_heterozygous_carrier(self, panel: AllergyPanel) -> None:
        carrier = _score_snp(self._snp(panel), "AT")
        assert carrier.category == ELEVATED
        caveat = _positive_hla_proxy_specificity_caveat(carrier)
        assert caveat is not None
        assert "HLA-A*33 cross-reactivity" in caveat
        assert "confirmatory" in caveat.lower()
        assert "Native American" in caveat
        # And it lands in the stored SNP detail JSON.
        detail = _stored_snp_detail(carrier, {})
        assert detail["hla_proxy_specificity_caveat"] == caveat

    def test_caveat_not_surfaced_for_indeterminate_homozygote(self, panel: AllergyPanel) -> None:
        # The palindromic AA/TT homozygotes are withheld (Indeterminate), not a
        # positive carrier call — they must NOT get a "this positive result" caveat.
        for gt in ("AA", "TT"):
            result = _score_snp(self._snp(panel), gt)
            assert result.category == INDETERMINATE, gt
            assert _positive_hla_proxy_specificity_caveat(result) is None, gt
            assert "hla_proxy_specificity_caveat" not in _stored_snp_detail(result, {}), gt

    def test_caveat_not_surfaced_for_proxy_without_mechanism(self) -> None:
        # A positive HLA-proxy call whose panel records no false_positive_mechanism
        # (e.g. the clinical-grade HLA-B*57:01 proxy) gets no specificity caveat.
        result = SNPResult(
            rsid="rs2395029",
            gene="HLA-B",
            variant_name="HLA-B*57:01 proxy",
            genotype="GT",
            category=ELEVATED,
            effect_summary="carrier",
            evidence_level=4,
            pmids=[],
            recommendation_text="",
            present_in_sample=True,
            hla_proxy={"hla_allele": "HLA-B*57:01", "clinical_grade": True},
        )
        assert _positive_hla_proxy_specificity_caveat(result) is None


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

    def test_hla_proxy_seed_mirrors_production_provenance(self) -> None:
        """The HLA proxy seed fixture must cite the same curated PMIDs as the
        production hla_proxy_lookup table, so it can't drift back to the
        misattributed citations scrubbed from the panel + proxy JSON by
        #176/#194/#232 (#278): HLA-B*15:02 -> 15057820 (not 21248726),
        HLA-B*58:01 -> 29392141 (not 22286173)."""
        proxy_path = (
            Path(__file__).resolve().parents[2]
            / "backend"
            / "data"
            / "panels"
            / "hla_proxy_lookup.json"
        )
        production = json.loads(proxy_path.read_text(encoding="utf-8"))
        prod_pmids_by_allele: dict[str, set[str]] = {}
        for entry in production["entries"]:
            prod_pmids_by_allele.setdefault(entry["hla_allele"], set()).add(entry["pmid"])

        # PMIDs that were attached to these alleles in error (#176/#194/#232) and
        # must never reappear in the seed fixture.
        banned = {"21248726", "22286173", "22177658", "26092464"}
        seed_entries = _hla_proxy_seed_entries()
        assert seed_entries, "HLA proxy seed must not be empty"
        for entry in seed_entries:
            allele = entry["hla_allele"]
            assert allele in prod_pmids_by_allele, (
                f"seed allele {allele} is absent from the production proxy lookup"
            )
            assert entry["pmid"] in prod_pmids_by_allele[allele], (
                f"seed {allele} cites {entry['pmid']}, not in production "
                f"{sorted(prod_pmids_by_allele[allele])}"
            )
            assert entry["pmid"] not in banned, (
                f"seed {allele} re-introduces misattributed PMID {entry['pmid']}"
            )


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
        """Distinct alleles/genes each get their own PGx cross-link."""
        _seed_variants(
            sample_engine,
            [
                ("rs144012689", "6", 31356397, "CT"),  # HLA-B*15:02 → pgx
                ("rs1061235", "6", 29910670, "AT"),  # HLA-A*31:01 het carrier → pgx (#545)
            ],
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        pgx_links = [
            f for f in result.cross_module_findings if f.target_module == "pharmacogenomics"
        ]
        # HLA-B*15:02 and HLA-A*31:01 are distinct alleles → two cross-links.
        genes = {f.gene for f in pgx_links}
        assert "HLA-B" in genes
        assert "HLA-A" in genes

    def test_distinct_hla_b_alleles_keep_separate_pgx_links(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Two HLA-B alleles (same gene) must not collapse to one PGx link.

        HLA-B*15:02 (carbamazepine SJS/TEN) and HLA-B*58:01 (allopurinol
        SCAR) are different drug-safety handoffs that share gene "HLA-B".
        Gene-only dedup hid one of them; allele-level dedup keeps both.
        Regression for issue #92.
        """
        _seed_variants(
            sample_engine,
            [
                ("rs144012689", "6", 31356397, "CT"),  # HLA-B*15:02 → carbamazepine
                ("rs9263726", "6", 31355848, "CT"),  # HLA-B*58:01 → allopurinol
            ],
        )
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        pgx_links = [
            f for f in result.cross_module_findings if f.target_module == "pharmacogenomics"
        ]
        # Both HLA-B alleles share gene "HLA-B" but represent distinct drug
        # contexts, so both cross-links must be present.
        assert len(pgx_links) == 2
        alleles = " ".join(f.finding_text for f in pgx_links)
        assert "HLA-B*15:02" in alleles
        assert "HLA-B*58:01" in alleles


# ── Full scoring integration tests ──────────────────────────────────────


class TestFullScoring:
    def test_all_variants_scored(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """All 14 panel SNPs are scored when present."""
        _seed_variants(sample_engine, ALL_ALLERGY_VARIANTS)
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        total_snps = sum(len(pr.snp_results) for pr in result.pathway_results)
        assert total_snps == 14

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

    def test_dq8_only_pathway_detail_does_not_rule_out_from_dq2_negative(
        self,
        panel: AllergyPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """DQ2-negative/DQ8-positive detail must not carry single-proxy rule-out text."""
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
        store_allergy_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            summaries = conn.execute(
                sa.select(findings.c.detail_json).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.category == "pathway_summary",
                )
            ).fetchall()

        food_detail = next(
            detail
            for detail in (json.loads(row.detail_json) for row in summaries)
            if detail["pathway_id"] == "food_sensitivity"
        )
        celiac_details = {
            detail["rsid"]: detail
            for detail in food_detail["snp_details"]
            if detail["rsid"] in {"rs2187668", "rs7775228"}
        }
        assert set(celiac_details) == {"rs2187668", "rs7775228"}

        dq2_text = celiac_details["rs2187668"]["effect_summary"]
        assert "does not rule out celiac disease" in dq2_text
        assert "combined DQ2/DQ8 assessment" in dq2_text
        assert "negative predictive value" not in dq2_text.lower()
        assert ">99%" not in dq2_text
        assert "extremely unlikely" not in dq2_text.lower()

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
        # Source-matched population-specific LD (Zhang 2018), not fabricated bins (#333).
        by_pop = rs926_detail["hla_proxy_lookup"]["r_squared_by_pop"]
        assert by_pop["Tibetan"] == pytest.approx(0.606)
        assert "AFR" not in by_pop

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
        """Panel coverage rows are stored for all 13 SNPs."""
        _seed_variants(sample_engine, ALL_ALLERGY_VARIANTS)
        _seed_hla_proxies(reference_engine)
        result = score_allergy_pathways(panel, sample_engine, reference_engine)
        store_allergy_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(panel_coverage).where(panel_coverage.c.module == MODULE_NAME)
            ).fetchall()
        assert len(rows) == 14

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


class TestEvidenceGatingCap:
    """#470: drive a synthetic Elevated ★☆ (evidence_level=1) SNP through the
    _score_snp evidence cap so the Elevated→Moderate hard-cap is actually exercised
    (the pre-existing 'cap' tests used already-Moderate genotypes and never hit it)."""

    @staticmethod
    def _snp(evidence_level: int, aa_category: str) -> PanelSNP:
        # A/G is non-palindromic, so the strand-ambiguity guard does not intercept.
        return PanelSNP(
            rsid="rs99999990",
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
