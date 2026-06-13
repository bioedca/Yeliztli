"""Tests for the Gene Skin module (P3-55).

Covers:
  - Panel loading and dataclass construction
  - MC1R multi-allele haplotype-aware calling (0/1/2 R alleles)
  - FLG R501X flagged as Insufficient Data
  - Genotype normalization
  - SNP scoring with evidence-level gating
  - Pathway level determination (highest category)
  - Cross-module reference findings (Cancer, Nutrigenomics, Allergy)
  - Full scoring integration with sample DB
  - Findings storage and retrieval
  - GWAS annotation_coverage bitmask (bit 5)
  - 20 trait finding count verification
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.skin import (
    ELEVATED,
    INDETERMINATE,
    MODERATE,
    MODULE_NAME,
    STANDARD,
    PanelSNP,
    PathwayResult,
    SkinPanel,
    SkinResult,
    SNPResult,
    _determine_pathway_level,
    _normalize_genotype,
    _r_allele_dosage,
    _score_snp,
    load_skin_panel,
    score_skin_pathways,
    store_skin_findings,
    update_annotation_coverage_gwas,
)
from backend.annotation.engine import GWAS_BIT
from backend.db.tables import (
    annotated_variants,
    findings,
    gwas_associations,
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
    / "skin_panel.json"
)


@pytest.fixture()
def panel() -> SkinPanel:
    """Load the actual curated panel."""
    return load_skin_panel(PANEL_PATH)


@pytest.fixture()
def sample_engine(tmp_path: Path) -> sa.Engine:
    """Create a sample DB with raw_variants and findings tables."""
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


# All 10 panel SNPs with their chromosome positions
ALL_SKIN_VARIANTS = [
    ("rs1805007", "16", 89919736, "CT"),  # MC1R R151C het
    ("rs1805008", "16", 89919746, "CC"),  # MC1R R160W ref
    ("rs1805009", "16", 89919709, "GG"),  # MC1R D294H ref
    ("rs885479", "16", 89919722, "GA"),  # MC1R R163Q het
    ("rs61816761", "1", 152285861, "GA"),  # FLG het
    ("rs1695", "11", 67585218, "AG"),  # GSTP1 het
    ("rs1799750", "11", 102799717, "GGG"),  # MMP1 1G/2G het
    ("rs4880", "6", 160113872, "CT"),  # SOD2 het
    ("rs2228570", "12", 48272895, "GA"),  # VDR FokI het
    ("rs1544410", "12", 48239835, "GA"),  # VDR BsmI het
]


# ── Panel loading tests ──────────────────────────────────────────────────


class TestPanelLoading:
    def test_load_panel_succeeds(self, panel: SkinPanel) -> None:
        assert panel.module == "skin"
        assert panel.version == "1.0.0"

    def test_panel_has_four_pathways(self, panel: SkinPanel) -> None:
        assert len(panel.pathways) == 4
        pathway_ids = {p.id for p in panel.pathways}
        assert pathway_ids == {
            "pigmentation_uv",
            "skin_barrier_inflammation",
            "oxidative_stress_aging",
            "skin_micronutrients",
        }

    def test_panel_all_rsids(self, panel: SkinPanel) -> None:
        rsids = panel.all_rsids()
        assert len(rsids) == 10
        expected = {
            "rs1805007",
            "rs1805008",
            "rs1805009",
            "rs885479",
            "rs61816761",
            "rs1695",
            "rs1799750",
            "rs4880",
            "rs2228570",
            "rs1544410",
        }
        assert set(rsids) == expected

    def test_panel_snps_have_genotype_effects(self, panel: SkinPanel) -> None:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                assert len(snp.genotype_effects) > 0, f"{snp.rsid} has no genotype effects"
                for gt, effect in snp.genotype_effects.items():
                    assert "category" in effect
                    assert "effect_summary" in effect
                    assert effect["category"] in (ELEVATED, MODERATE, STANDARD)

    def test_panel_has_special_calling(self, panel: SkinPanel) -> None:
        assert panel.special_calling is not None
        assert "MC1R_multi_allele" in panel.special_calling
        assert "FLG_R501X_limited_coverage" in panel.special_calling

    def test_load_nonexistent_panel_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_skin_panel(Path("/nonexistent/panel.json"))

    def test_mc1r_snps_have_allele_class(self, panel: SkinPanel) -> None:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                if snp.gene == "MC1R":
                    assert snp.mc1r_allele_class in ("R", "r"), (
                        f"{snp.rsid} missing mc1r_allele_class"
                    )

    def test_flg_has_insufficient_data_flag(self, panel: SkinPanel) -> None:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                if snp.rsid == "rs61816761":
                    assert snp.variant_name == "R501X"
                    assert snp.hgvs_protein == "p.Arg501Ter"
                    assert snp.insufficient_data_flag is True
                    assert snp.coverage_note is not None
                    assert "R501X" in snp.coverage_note
                    assert "2282del4" in snp.coverage_note

    def test_cross_module_links_present(self, panel: SkinPanel) -> None:
        """MC1R R alleles → cancer, FLG → allergy, VDR → nutrigenomics."""
        cross_modules = {}
        for pathway in panel.pathways:
            for snp in pathway.snps:
                if snp.cross_module:
                    cross_modules[snp.gene] = snp.cross_module["module"]

        assert cross_modules.get("MC1R") == "cancer"
        assert cross_modules.get("FLG") == "allergy"
        assert cross_modules.get("VDR") == "nutrigenomics"


class TestMMP1CitationProvenance:
    """The MMP1 rs1799750 (1G/2G) row must cite real MMP1/skin-aging evidence, not
    the unrelated obstetrics/psychopharmacology/andrology papers it previously
    carried (#345). All PMIDs verified via NCBI esummary."""

    # 2G -> skin/lung aging (Vierkötter 2015, SALIA, rs1799750-specific); 2G ETS-site
    # greater transcription (Tower 2003); MMP1 -> dermal collagen aging (Quan 2023).
    _CURATED = frozenset({"25599395", "14519134", "36914001"})
    # Unrelated papers previously attached: twin-transfusion / drug-preference /
    # sperm-donor-ethics. Scoped to the MMP1 row (the repo-wide ban lives in
    # test_citation_provenance_guard.py::BANNED_OFF_TOPIC_PMIDS).
    _BANNED = frozenset({"11251926", "16826401", "20622888"})

    def _mmp1(self, panel: SkinPanel) -> PanelSNP:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                if snp.rsid == "rs1799750":
                    return snp
        raise AssertionError("rs1799750 (MMP1) row not found in skin panel")

    def test_mmp1_cites_curated_pmids(self, panel: SkinPanel) -> None:
        assert set(self._mmp1(panel).pmids) == self._CURATED

    def test_mmp1_drops_unrelated_pmids(self, panel: SkinPanel) -> None:
        leaked = self._BANNED & set(self._mmp1(panel).pmids)
        assert not leaked, f"MMP1 row still cites unrelated PMID(s) {sorted(leaked)}"


class TestGSTP1CitationProvenance:
    """The GSTP1 rs1695 (Ile105Val) row must cite real GSTP1/skin oxidative-stress
    evidence, not the unrelated RCC-antigen / FGFR4-NFκB / pediatric-sleep papers
    it previously carried (#391). All PMIDs verified via NCBI esummary + Consensus.
    """

    # 105Val -> reduced GST enzyme activity (Watson 1998, the functional paper);
    # GSTP1 Val105 -> atopic dermatitis risk + lower erythrocyte glutathione
    # (Chung 2009); GSTP1 Ile105Val -> skin-cancer/melanoma meta (Zhou 2015).
    _CURATED = frozenset({"9498276", "19842992", "26044055"})
    # Unrelated papers previously attached: renal-cell-carcinoma antigens /
    # FGFR4-NFκB signaling / pediatric sleep-obesity meta. Scoped to the GSTP1
    # row (FGFR4 names a real gene → same-field wrong-gene, deliberately not in
    # the repo-wide test_citation_provenance_guard.py::BANNED_OFF_TOPIC_PMIDS).
    _BANNED = frozenset({"10508479", "21203561", "25589359"})

    def _gstp1(self, panel: SkinPanel) -> PanelSNP:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                if snp.rsid == "rs1695":
                    return snp
        raise AssertionError("rs1695 (GSTP1) row not found in skin panel")

    def test_gstp1_cites_curated_pmids(self, panel: SkinPanel) -> None:
        assert set(self._gstp1(panel).pmids) == self._CURATED

    def test_gstp1_drops_unrelated_pmids(self, panel: SkinPanel) -> None:
        leaked = self._BANNED & set(self._gstp1(panel).pmids)
        assert not leaked, f"GSTP1 row still cites unrelated PMID(s) {sorted(leaked)}"


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
        assert _normalize_genotype("DI") is None
        assert _normalize_genotype("ID") is None

    def test_lowercase(self) -> None:
        assert _normalize_genotype("ct") == "CT"


# ── MC1R multi-allele calling tests ──────────────────────────────────────


class TestMC1RMultiAllele:
    def _get_mc1r_snp(self, panel: SkinPanel, rsid: str) -> PanelSNP:
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == rsid:
                    return snp
        pytest.fail(f"MC1R SNP {rsid} not found")

    def test_mc1r_r151c_het_moderate(self, panel: SkinPanel) -> None:
        """MC1R R151C het (CT) → Moderate."""
        snp = self._get_mc1r_snp(panel, "rs1805007")
        result = _score_snp(snp, "CT")
        assert result.category == MODERATE
        assert result.mc1r_allele_class == "R"

    def test_mc1r_r151c_hom_elevated(self, panel: SkinPanel) -> None:
        """MC1R R151C hom (TT) → Elevated (evidence_level=3 ≥ 2)."""
        snp = self._get_mc1r_snp(panel, "rs1805007")
        result = _score_snp(snp, "TT")
        assert result.category == ELEVATED

    def test_mc1r_r163q_hom_moderate(self, panel: SkinPanel) -> None:
        """MC1R R163Q hom (AA) → Moderate (mild r allele, caps at Moderate)."""
        snp = self._get_mc1r_snp(panel, "rs885479")
        result = _score_snp(snp, "AA")
        assert result.category == MODERATE

    def test_mc1r_r163q_allele_class_r(self, panel: SkinPanel) -> None:
        snp = self._get_mc1r_snp(panel, "rs885479")
        assert snp.mc1r_allele_class == "r"

    def test_mc1r_aggregate_0_r_alleles(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """All MC1R variants ref → 0 R alleles → Low UV Sensitivity."""
        _seed_variants(
            sample_engine,
            [
                ("rs1805007", "16", 89919736, "CC"),
                ("rs1805008", "16", 89919746, "CC"),
                ("rs1805009", "16", 89919709, "GG"),
                ("rs885479", "16", 89919722, "GG"),
            ],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        assert result.mc1r_aggregate is not None
        assert result.mc1r_aggregate.r_allele_count == 0
        assert result.mc1r_aggregate.risk_label == "Low UV Sensitivity"

    def test_mc1r_aggregate_1_r_allele(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """One MC1R R allele het → 1 R allele → Moderate UV Sensitivity."""
        _seed_variants(
            sample_engine,
            [
                ("rs1805007", "16", 89919736, "CT"),  # 1 R allele
                ("rs1805008", "16", 89919746, "CC"),
                ("rs1805009", "16", 89919709, "GG"),
                ("rs885479", "16", 89919722, "GG"),
            ],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        assert result.mc1r_aggregate is not None
        assert result.mc1r_aggregate.r_allele_count == 1
        assert result.mc1r_aggregate.risk_label == "Moderate UV Sensitivity"

    def test_mc1r_aggregate_2_r_alleles_compound_het(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Two different MC1R R alleles het → 2 R alleles → High UV Sensitivity."""
        _seed_variants(
            sample_engine,
            [
                ("rs1805007", "16", 89919736, "CT"),  # 1 R allele
                ("rs1805008", "16", 89919746, "CT"),  # 1 R allele
                ("rs1805009", "16", 89919709, "GG"),
                ("rs885479", "16", 89919722, "GG"),
            ],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        assert result.mc1r_aggregate is not None
        assert result.mc1r_aggregate.r_allele_count == 2
        assert result.mc1r_aggregate.risk_label == "High UV Sensitivity"

    def test_mc1r_aggregate_2_r_alleles_homozygous(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """MC1R R151C homozygous (TT) → 2 R alleles → High UV Sensitivity."""
        _seed_variants(
            sample_engine,
            [
                ("rs1805007", "16", 89919736, "TT"),  # 2 R alleles
                ("rs1805008", "16", 89919746, "CC"),
                ("rs1805009", "16", 89919709, "GG"),
                ("rs885479", "16", 89919722, "GG"),
            ],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        assert result.mc1r_aggregate is not None
        assert result.mc1r_aggregate.r_allele_count == 2
        assert result.mc1r_aggregate.risk_label == "High UV Sensitivity"

    def test_mc1r_aggregate_counts_reverse_strand_het(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Reverse-strand R151C het (``AG`` ≡ ``CT``) counts as 1 R allele (issue #24).

        ``_score_snp`` scores ``"AG"`` as Moderate via the strand-complement
        fallback, so the aggregate must agree — previously ``genotype.count("T")``
        returned 0 and produced an internally inconsistent "0 R alleles" /
        "Low UV Sensitivity" summary for a scored R-allele carrier.
        """
        _seed_variants(
            sample_engine,
            [
                ("rs1805007", "16", 89919736, "AG"),  # reverse-strand of CT
                ("rs1805008", "16", 89919746, "CC"),
                ("rs1805009", "16", 89919709, "GG"),
                ("rs885479", "16", 89919722, "GG"),
            ],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)

        # The individual SNP is scored as an R-allele carrier (Moderate)...
        pigmentation = next(
            pr for pr in result.pathway_results if pr.pathway_id == "pigmentation_uv"
        )
        r151c = next(s for s in pigmentation.snp_results if s.rsid == "rs1805007")
        assert r151c.category == "Moderate"

        # ...so the aggregate must count it, not drop it to 0.
        assert result.mc1r_aggregate is not None
        assert result.mc1r_aggregate.r_allele_count == 1
        assert "rs1805007" in result.mc1r_aggregate.r_allele_rsids
        assert result.mc1r_aggregate.risk_label == "Moderate UV Sensitivity"

    def test_mc1r_aggregate_counts_reverse_strand_homozygous(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Reverse-strand R151C hom (``AA`` ≡ ``TT``) counts as 2 R alleles (issue #24)."""
        _seed_variants(
            sample_engine,
            [
                ("rs1805007", "16", 89919736, "AA"),  # reverse-strand of TT
                ("rs1805008", "16", 89919746, "CC"),
                ("rs1805009", "16", 89919709, "GG"),
                ("rs885479", "16", 89919722, "GG"),
            ],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        assert result.mc1r_aggregate is not None
        assert result.mc1r_aggregate.r_allele_count == 2
        assert result.mc1r_aggregate.risk_label == "High UV Sensitivity"

    def test_r_allele_dosage_is_strand_and_order_aware(self) -> None:
        """``_r_allele_dosage`` mirrors ``lookup_by_genotype``'s harmonization."""
        # rs1805007 R151C: ref C, risk T (C/T SNP — not strand-ambiguous).
        assert _r_allele_dosage("CC", "T", "C") == 0
        assert _r_allele_dosage("CT", "T", "C") == 1
        assert _r_allele_dosage("TC", "T", "C") == 1  # allele order
        assert _r_allele_dosage("TT", "T", "C") == 2
        assert _r_allele_dosage("AG", "T", "C") == 1  # reverse strand of CT
        assert _r_allele_dosage("AA", "T", "C") == 2  # reverse strand of TT
        # rs1805009 D294H: ref G, risk C (MC1R c.880G>C / p.Asp294His).
        assert _r_allele_dosage("GC", "C", "G") == 1
        assert _r_allele_dosage("CG", "C", "G") == 1  # allele order
        assert _r_allele_dosage("CC", "C", "G") == 2
        assert _r_allele_dosage("GA", "C", "G") is None  # G>A is not D294H
        # Off-panel / triallelic call cannot be resolved → None (left uncounted).
        assert _r_allele_dosage("GG", "T", "C") == 0  # ref hom on complement frame
        assert _r_allele_dosage("AC", "T", "C") is None

    def test_mc1r_r_allele_does_not_count_mild(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """R163Q (mild r allele) does NOT count toward R allele aggregate."""
        _seed_variants(
            sample_engine,
            [
                ("rs1805007", "16", 89919736, "CC"),
                ("rs1805008", "16", 89919746, "CC"),
                ("rs1805009", "16", 89919709, "GG"),
                ("rs885479", "16", 89919722, "AA"),  # Homozygous mild r
            ],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        assert result.mc1r_aggregate is not None
        assert result.mc1r_aggregate.r_allele_count == 0
        assert result.mc1r_aggregate.risk_label == "Low UV Sensitivity"

    def test_mc1r_aggregate_none_when_no_mc1r_genotyped(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """No MC1R variants in sample → mc1r_aggregate is None."""
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        assert result.mc1r_aggregate is None


# ── FLG Insufficient Data tests ──────────────────────────────────────────


class TestFLGInsufficientData:
    def test_flg_insufficient_data_flag_set(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """FLG genotyped → flg_insufficient_data is True."""
        _seed_variants(
            sample_engine,
            [("rs61816761", "1", 152285861, "GA")],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        assert result.flg_insufficient_data is True

    def test_flg_insufficient_data_flag_not_set_when_absent(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """FLG not in sample → flg_insufficient_data is False."""
        _seed_variants(
            sample_engine,
            [("rs1805007", "16", 89919736, "CT")],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        assert result.flg_insufficient_data is False

    def test_flg_finding_stored_as_insufficient_data(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """FLG generates an 'insufficient_data' category finding."""
        _seed_variants(
            sample_engine,
            [("rs61816761", "1", 152285861, "GA")],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        store_skin_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.category == "insufficient_data",
                    )
                )
            ).first()

        assert row is not None
        assert "FLG" in row.finding_text
        assert "R501X" in row.finding_text
        assert "2282del4" in row.finding_text
        assert "Insufficient Data" in row.finding_text
        assert row.gene_symbol == "FLG"

        detail = json.loads(row.detail_json)
        assert detail["observed_variant"] == "FLG R501X (p.Arg501Ter)"
        assert detail["proxy_target"] is None
        assert "proxy_target" in detail
        assert "insufficient_data_reason" in detail
        assert "2282del4" in detail["insufficient_data_reason"]


# ── SNP scoring tests ────────────────────────────────────────────────────


class TestSNPScoring:
    def test_not_genotyped_returns_standard(self, panel: SkinPanel) -> None:
        snp = panel.pathways[0].snps[0]
        result = _score_snp(snp, None)
        assert result.category == STANDARD
        assert result.present_in_sample is False

    def test_palindromic_homozygote_withheld_as_indeterminate(self, panel: SkinPanel) -> None:
        """#269: MC1R rs1805009 is C/G palindromic (GG=Standard, CC=Elevated), so
        both homozygotes are withheld as Indeterminate with a strand caveat; the
        heterozygote stays resolvable."""
        snp = next(s for pw in panel.pathways for s in pw.snps if s.rsid == "rs1805009")
        for homozygote in ("CC", "GG"):
            result = _score_snp(snp, homozygote)
            assert result.category == INDETERMINATE, homozygote
            assert result.present_in_sample is True
            assert "palindromic" in result.effect_summary.lower()
            assert "strand" in (result.coverage_note or "").lower()
        # Heterozygote is strand-resolvable and keeps its curated category.
        assert _score_snp(snp, "CG").category == MODERATE
        assert _score_snp(snp, "GC").category == MODERATE

    def test_evidence_gating_caps_at_moderate(self) -> None:
        """★☆ evidence hard-caps at Moderate (key rule)."""
        snp = _make_test_snp(evidence_level=1, genotype_category=ELEVATED)
        result = _score_snp(snp, "AA")
        assert result.category == MODERATE

    def test_evidence_level_2_allows_elevated(self) -> None:
        """★★ evidence allows Elevated when genotype warrants it."""
        snp = _make_test_snp(evidence_level=2, genotype_category=ELEVATED)
        result = _score_snp(snp, "AA")
        assert result.category == ELEVATED

    def test_evidence_level_3_allows_elevated(self) -> None:
        """★★★ evidence (MC1R R alleles) allows Elevated."""
        snp = _make_test_snp(evidence_level=3, genotype_category=ELEVATED)
        result = _score_snp(snp, "AA")
        assert result.category == ELEVATED

    def test_reversed_genotype_lookup(self, panel: SkinPanel) -> None:
        """Panel handles reversed genotype strings (e.g. CT vs TC)."""
        mc1r = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs1805007":
                    mc1r = snp
                    break
        result_ct = _score_snp(mc1r, "CT")
        result_tc = _score_snp(mc1r, "TC")
        assert result_ct.category == result_tc.category == MODERATE

    def test_unknown_genotype_defaults_standard(self, panel: SkinPanel) -> None:
        snp = panel.pathways[0].snps[0]
        result = _score_snp(snp, "ZZ")
        assert result.category == STANDARD
        assert result.present_in_sample is True

    def test_gstp1_gg_capped_at_moderate(self, panel: SkinPanel) -> None:
        """GSTP1 has evidence_level=1, so GG (Elevated) → capped at Moderate."""
        gstp1 = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs1695":
                    gstp1 = snp
                    break
        assert gstp1 is not None
        assert gstp1.evidence_level == 1
        result = _score_snp(gstp1, "GG")
        assert result.category == MODERATE  # Capped from Elevated

    def test_sod2_tt_capped_at_moderate(self, panel: SkinPanel) -> None:
        """SOD2 has evidence_level=1, so TT (Elevated) → capped at Moderate."""
        sod2 = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs4880":
                    sod2 = snp
                    break
        assert sod2 is not None
        assert sod2.evidence_level == 1
        result = _score_snp(sod2, "TT")
        assert result.category == MODERATE

    def test_flg_het_moderate(self, panel: SkinPanel) -> None:
        """FLG R501X het → Moderate (evidence_level=2)."""
        flg = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs61816761":
                    flg = snp
                    break
        assert flg is not None
        assert flg.variant_name == "R501X"
        result = _score_snp(flg, "GA")
        assert result.category == MODERATE
        assert result.insufficient_data_flag is True

    def test_flg_hom_elevated(self, panel: SkinPanel) -> None:
        """FLG R501X homozygous → Elevated (evidence_level=2 allows it)."""
        flg = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs61816761":
                    flg = snp
                    break
        result = _score_snp(flg, "AA")
        assert result.category == ELEVATED


# ── Pathway level determination tests ────────────────────────────────────


class TestPathwayLevel:
    def test_elevated_wins(self) -> None:
        results = [
            _make_snp_result(STANDARD, present=True),
            _make_snp_result(ELEVATED, present=True),
            _make_snp_result(MODERATE, present=True),
        ]
        assert _determine_pathway_level(results) == ELEVATED

    def test_moderate_when_no_elevated(self) -> None:
        results = [
            _make_snp_result(STANDARD, present=True),
            _make_snp_result(MODERATE, present=True),
        ]
        assert _determine_pathway_level(results) == MODERATE

    def test_standard_when_all_standard(self) -> None:
        results = [
            _make_snp_result(STANDARD, present=True),
            _make_snp_result(STANDARD, present=True),
        ]
        assert _determine_pathway_level(results) == STANDARD

    def test_empty_results(self) -> None:
        assert _determine_pathway_level([]) == STANDARD

    def test_only_missing_snps_gives_standard(self) -> None:
        results = [
            _make_snp_result(ELEVATED, present=False),
            _make_snp_result(MODERATE, present=False),
        ]
        assert _determine_pathway_level(results) == STANDARD


# ── Cross-module findings tests ──────────────────────────────────────────


class TestCrossModuleFindings:
    def test_mc1r_cancer_cross_link(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """MC1R non-Standard → cross-link to Cancer module."""
        _seed_variants(
            sample_engine,
            [("rs1805007", "16", 89919736, "CT")],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        mc1r_cross = [c for c in result.cross_module_findings if c.gene == "MC1R"]
        assert len(mc1r_cross) == 1
        assert mc1r_cross[0].target_module == "cancer"
        assert "melanoma" in mc1r_cross[0].finding_text.lower()

    def test_flg_allergy_cross_link(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """FLG non-Standard → cross-link to Allergy module."""
        _seed_variants(
            sample_engine,
            [("rs61816761", "1", 152285861, "GA")],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        flg_cross = [c for c in result.cross_module_findings if c.gene == "FLG"]
        assert len(flg_cross) == 1
        assert flg_cross[0].target_module == "allergy"

    def test_vdr_nutrigenomics_cross_link(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """VDR non-Standard → cross-link to Nutrigenomics module."""
        _seed_variants(
            sample_engine,
            [("rs2228570", "12", 48272895, "AA")],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        vdr_cross = [c for c in result.cross_module_findings if c.gene == "VDR"]
        assert len(vdr_cross) == 1
        assert vdr_cross[0].target_module == "nutrigenomics"

    def test_no_cross_module_for_standard(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """No cross-module findings when all SNPs are Standard."""
        _seed_variants(
            sample_engine,
            [
                ("rs1805007", "16", 89919736, "CC"),  # ref
                ("rs61816761", "1", 152285861, "GG"),  # ref
                ("rs2228570", "12", 48272895, "GG"),  # ref
            ],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        assert len(result.cross_module_findings) == 0

    def test_deduplicated_cross_module_per_gene(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """MC1R's RHC alleles aggregate into a single MC1R → Cancer link.

        Dedup is at variant granularity (rsid + target) everywhere except the
        MC1R aggregate path, where the RHC 'R' alleles intentionally collapse
        into one MC1R → Cancer cross-link reporting the total R-allele count.
        """
        _seed_variants(
            sample_engine,
            [
                ("rs1805007", "16", 89919736, "CT"),  # MC1R R151C het
                ("rs1805008", "16", 89919746, "CT"),  # MC1R R160W het
            ],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        mc1r_cross = [c for c in result.cross_module_findings if c.gene == "MC1R"]
        assert len(mc1r_cross) == 1  # Deduplicated

    def test_distinct_vdr_polymorphisms_keep_separate_nutrigenomics_links(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Distinct VDR SNPs (FokI, BsmI) → two Nutrigenomics cross-links.

        FokI (rs2228570) and BsmI (rs1544410) share gene "VDR" but are
        different vitamin-D signals — opposite-direction melanoma risk, and
        only FokI modulates the response to supplementation — so they must not
        collapse into one VDR → Nutrigenomics cross-link. Regression for #205;
        fails under the old gene-only dedup.
        """
        _seed_variants(
            sample_engine,
            [
                ("rs2228570", "12", 48272895, "AA"),  # VDR FokI hom
                ("rs1544410", "12", 48239835, "AA"),  # VDR BsmI hom
            ],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        vdr_cross = [
            c
            for c in result.cross_module_findings
            if c.gene == "VDR" and c.target_module == "nutrigenomics"
        ]
        assert len(vdr_cross) == 2
        assert {c.rsid for c in vdr_cross} == {"rs2228570", "rs1544410"}


# ── Integration tests ────────────────────────────────────────────────────


class TestScorePathways:
    def test_full_scoring_all_snps(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Score pathways with all 10 panel SNPs genotyped."""
        _seed_variants(sample_engine, ALL_SKIN_VARIANTS)
        _seed_gwas(
            reference_engine,
            [
                ("rs1805007", "Skin pigmentation"),
                ("rs4880", "Oxidative stress"),
            ],
        )

        result = score_skin_pathways(panel, sample_engine, reference_engine)

        # Pigmentation: MC1R R151C het=Moderate → pathway = Moderate
        pigmentation = next(
            pr for pr in result.pathway_results if pr.pathway_id == "pigmentation_uv"
        )
        assert pigmentation.level == MODERATE

        # Skin barrier: FLG het=Moderate → pathway = Moderate
        barrier = next(
            pr for pr in result.pathway_results if pr.pathway_id == "skin_barrier_inflammation"
        )
        assert barrier.level == MODERATE

        # Oxidative stress: all star-1 SNPs, capped at Moderate
        oxidative = next(
            pr for pr in result.pathway_results if pr.pathway_id == "oxidative_stress_aging"
        )
        assert oxidative.level == MODERATE

        # Micronutrients: VDR star-1 SNPs, capped at Moderate
        micronutrients = next(
            pr for pr in result.pathway_results if pr.pathway_id == "skin_micronutrients"
        )
        assert micronutrients.level == MODERATE

        # GWAS matches
        assert "rs1805007" in result.gwas_matched_rsids
        assert "rs4880" in result.gwas_matched_rsids

        # MC1R aggregate should be present
        assert result.mc1r_aggregate is not None
        assert result.mc1r_aggregate.r_allele_count == 1

        # Cross-module findings should exist
        assert len(result.cross_module_findings) >= 1

    def test_missing_snps_default_standard(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Pathways with no genotyped SNPs default to Standard."""
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        for pr in result.pathway_results:
            assert pr.level == STANDARD
            assert len(pr.called_snps) == 0
            assert len(pr.missing_snps) > 0

    def test_flg_coverage_note_preserved(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """FLG coverage note survives through scoring pipeline."""
        _seed_variants(
            sample_engine,
            [("rs61816761", "1", 152285861, "GA")],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)

        barrier = next(
            pr for pr in result.pathway_results if pr.pathway_id == "skin_barrier_inflammation"
        )
        flg = next(s for s in barrier.called_snps if s.rsid == "rs61816761")
        assert flg.coverage_note is not None
        assert "R501X" in flg.coverage_note
        assert "2282del4" in flg.coverage_note


# ── Findings storage tests ─────────────────────────────────────────────


class TestStoreFindingsIntegration:
    def test_store_and_retrieve_findings(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Store findings and verify they're in the DB."""
        _seed_variants(sample_engine, ALL_SKIN_VARIANTS)

        result = score_skin_pathways(panel, sample_engine, reference_engine)
        count = store_skin_findings(result, sample_engine)
        assert count > 0

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == MODULE_NAME)
            ).fetchall()

        assert len(rows) == count

        # Check pathway summary findings exist (always 4)
        pathway_summaries = [r for r in rows if r.category == "pathway_summary"]
        assert len(pathway_summaries) == 4

    def test_mc1r_aggregate_finding_stored(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """MC1R aggregate summary finding is stored."""
        _seed_variants(
            sample_engine,
            [
                ("rs1805007", "16", 89919736, "CT"),
                ("rs1805008", "16", 89919746, "CC"),
                ("rs1805009", "16", 89919709, "GG"),
                ("rs885479", "16", 89919722, "GG"),
            ],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        store_skin_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.category == "mc1r_aggregate",
                    )
                )
            ).first()

        assert row is not None
        assert row.gene_symbol == "MC1R"
        assert "multi-allele" in row.finding_text.lower()
        detail = json.loads(row.detail_json)
        assert "r_allele_count" in detail
        assert "risk_label" in detail

    def test_cross_module_findings_stored(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Cross-module findings are stored with category='cross_module'."""
        _seed_variants(
            sample_engine,
            [
                ("rs1805007", "16", 89919736, "CT"),  # MC1R → cancer
                ("rs61816761", "1", 152285861, "GA"),  # FLG → allergy
                ("rs2228570", "12", 48272895, "AA"),  # VDR → nutrigenomics
            ],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        store_skin_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            cross_rows = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.category == "cross_module",
                    )
                )
            ).fetchall()

        assert len(cross_rows) >= 1

        for cr in cross_rows:
            detail = json.loads(cr.detail_json)
            assert "target_module" in detail

    def test_store_clears_previous_findings(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Re-running store clears previous skin findings."""
        _seed_variants(
            sample_engine,
            [("rs1805007", "16", 89919736, "CT")],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        store_skin_findings(result, sample_engine)
        count2 = store_skin_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == MODULE_NAME)
            ).fetchall()

        assert len(rows) == count2  # No duplicates

    def test_no_snp_findings_for_empty_sample(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Empty sample produces pathway summaries but no SNP findings."""
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        count = store_skin_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            snp_findings = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.category == "snp_finding",
                    )
                )
            ).fetchall()

        assert len(snp_findings) == 0
        assert count == 4  # 4 pathway summaries, all Standard

    def test_20_trait_findings_max(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """With all SNPs genotyped at non-Standard, verify finding count ≤ 20."""
        # Use genotypes that produce non-Standard categories for all SNPs
        _seed_variants(
            sample_engine,
            [
                ("rs1805007", "16", 89919736, "TT"),  # MC1R R151C hom → Elevated
                ("rs1805008", "16", 89919746, "CT"),  # MC1R R160W het → Moderate
                ("rs1805009", "16", 89919709, "GC"),  # MC1R D294H het → Moderate
                ("rs885479", "16", 89919722, "GA"),  # MC1R R163Q het → Moderate
                ("rs61816761", "1", 152285861, "GA"),  # FLG het → Moderate
                ("rs1695", "11", 67585218, "GG"),  # GSTP1 hom → Moderate (capped)
                ("rs1799750", "11", 102799717, "GGG"),  # MMP1 het → Moderate
                ("rs4880", "6", 160113872, "TT"),  # SOD2 hom → Moderate (capped)
                ("rs2228570", "12", 48272895, "AA"),  # VDR FokI hom → Moderate (capped)
                ("rs1544410", "12", 48239835, "AA"),  # VDR BsmI hom → Moderate (capped)
            ],
        )

        result = score_skin_pathways(panel, sample_engine, reference_engine)
        count = store_skin_findings(result, sample_engine)

        # 4 pathway summaries + up to 10 SNP findings + 1 MC1R aggregate
        # + 1 FLG insufficient data + up to 4 cross-module ≤ 20
        assert count <= 20
        assert count >= 4  # At minimum, 4 pathway summaries

    def test_findings_include_pmids(
        self,
        panel: SkinPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Findings include PubMed citations."""
        _seed_variants(
            sample_engine,
            [("rs1805007", "16", 89919736, "CT")],
        )
        result = score_skin_pathways(panel, sample_engine, reference_engine)
        store_skin_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.rsid == "rs1805007",
                    )
                )
            ).first()

        assert row is not None
        pmids = json.loads(row.pmid_citations)
        # rs1805007 (MC1R R151C) now cites the verified MC1R melanoma meta-analysis
        # (Raimondi 2008) instead of the previously misattributed PMIDs (#359).
        assert "18366057" in pmids


# ── PathwayResult properties ────────────────────────────────────────────


class TestPathwayResultProperties:
    def test_called_and_missing_snps(self) -> None:
        pr = PathwayResult(
            pathway_id="test",
            pathway_name="Test",
            pathway_description="Test pathway",
            level=MODERATE,
            snp_results=[
                _make_snp_result(MODERATE, present=True),
                _make_snp_result(STANDARD, present=False),
            ],
        )
        assert len(pr.called_snps) == 1
        assert len(pr.missing_snps) == 1


# ── Annotation coverage bitmask tests ────────────────────────────────────


class TestUpdateAnnotationCoverageGwas:
    """Test that GWAS bitmask bit 5 (value 32) is ORed into annotation_coverage."""

    def _make_sample_with_annotated(
        self,
        raw: list[dict],
        annotated: list[dict],
    ) -> sa.Engine:
        engine = sa.create_engine("sqlite://")
        sample_metadata_obj.create_all(engine)
        if raw:
            with engine.begin() as conn:
                conn.execute(raw_variants.insert(), raw)
        if annotated:
            with engine.begin() as conn:
                conn.execute(annotated_variants.insert(), annotated)
        return engine

    def test_sets_bit5_on_gwas_matched_variants(self) -> None:
        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs1805007", "chrom": "16", "pos": 89919736, "genotype": "CT"},
            ],
            annotated=[
                {
                    "rsid": "rs1805007",
                    "chrom": "16",
                    "pos": 89919736,
                    "genotype": "CT",
                    "annotation_coverage": 0b001111,
                },
            ],
        )

        result = SkinResult(
            pathway_results=[],
            gwas_matched_rsids=["rs1805007"],
        )

        updated = update_annotation_coverage_gwas(result, sample)
        assert updated == 1

        with sample.connect() as conn:
            val = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs1805007"
                )
            ).scalar()

        assert val == 0b101111  # 47

    def test_null_annotation_coverage_gets_gwas_bit(self) -> None:
        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs1805007", "chrom": "16", "pos": 89919736, "genotype": "CT"},
            ],
            annotated=[
                {
                    "rsid": "rs1805007",
                    "chrom": "16",
                    "pos": 89919736,
                    "genotype": "CT",
                    "annotation_coverage": None,
                },
            ],
        )

        result = SkinResult(
            pathway_results=[],
            gwas_matched_rsids=["rs1805007"],
        )

        updated = update_annotation_coverage_gwas(result, sample)
        assert updated == 1

        with sample.connect() as conn:
            val = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs1805007"
                )
            ).scalar()

        assert val == GWAS_BIT

    def test_empty_gwas_matched_returns_zero(self) -> None:
        sample = self._make_sample_with_annotated(raw=[], annotated=[])
        result = SkinResult(pathway_results=[], gwas_matched_rsids=[])
        updated = update_annotation_coverage_gwas(result, sample)
        assert updated == 0

    def test_idempotent_double_application(self) -> None:
        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs1805007", "chrom": "16", "pos": 89919736, "genotype": "CT"},
            ],
            annotated=[
                {
                    "rsid": "rs1805007",
                    "chrom": "16",
                    "pos": 89919736,
                    "genotype": "CT",
                    "annotation_coverage": GWAS_BIT,
                },
            ],
        )

        result = SkinResult(
            pathway_results=[],
            gwas_matched_rsids=["rs1805007"],
        )

        update_annotation_coverage_gwas(result, sample)

        with sample.connect() as conn:
            val = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs1805007"
                )
            ).scalar()

        assert val == GWAS_BIT


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_test_snp(
    evidence_level: int = 2,
    genotype_category: str = ELEVATED,
) -> PanelSNP:
    """Create a test PanelSNP with configurable evidence and category."""
    return PanelSNP(
        rsid="rs9999999",
        gene="TEST",
        variant_name="Test variant",
        hgvs_protein=None,
        risk_allele="A",
        ref_allele="G",
        genotype_effects={
            "GG": {"category": STANDARD, "effect_summary": "Normal."},
            "GA": {"category": MODERATE, "effect_summary": "Moderate effect."},
            "AG": {"category": MODERATE, "effect_summary": "Moderate effect."},
            "AA": {"category": genotype_category, "effect_summary": "Risk genotype."},
        },
        evidence_level=evidence_level,
        pmids=["12345678"],
        recommendation_text="Test recommendation.",
    )


def _make_snp_result(
    category: str,
    present: bool = True,
) -> SNPResult:
    return SNPResult(
        rsid="rs0000001",
        gene="TEST",
        variant_name="Test",
        genotype="AA" if present else None,
        category=category,
        effect_summary="Test effect.",
        evidence_level=2,
        pmids=[],
        recommendation_text="Test.",
        present_in_sample=present,
    )
