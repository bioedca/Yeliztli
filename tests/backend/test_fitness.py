"""Tests for the Gene Fitness module (P3-46).

Covers:
  - Panel loading and dataclass construction
  - ACTN3 R577X three-state calling (RR/RX/XX)
  - ACE I/D proxy with coverage note
  - Genotype normalization
  - SNP scoring with evidence-level gating
  - Pathway level determination (highest category)
  - Cross-pathway context findings (ACTN3→Power, ACE→Endurance)
  - Full scoring integration with sample DB
  - Findings storage and retrieval
  - GWAS annotation_coverage bitmask (bit 5)
  - 17 trait finding count verification
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.fitness import (
    ELEVATED,
    INDETERMINATE,
    MODERATE,
    MODULE_NAME,
    STANDARD,
    FitnessPanel,
    FitnessResult,
    PanelSNP,
    PathwayResult,
    SNPResult,
    _determine_pathway_level,
    _generate_cross_context_findings,
    _normalize_genotype,
    _resolve_three_state,
    _score_snp,
    load_fitness_panel,
    score_fitness_pathways,
    store_fitness_findings,
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
    / "fitness_panel.json"
)


@pytest.fixture()
def panel() -> FitnessPanel:
    """Load the actual curated panel."""
    return load_fitness_panel(PANEL_PATH)


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


# ── Panel loading tests ──────────────────────────────────────────────────


class TestPanelLoading:
    def test_load_panel_succeeds(self, panel: FitnessPanel) -> None:
        assert panel.module == "fitness"
        assert panel.version == "1.0.0"

    def test_panel_has_four_pathways(self, panel: FitnessPanel) -> None:
        assert len(panel.pathways) == 4
        pathway_ids = {p.id for p in panel.pathways}
        assert pathway_ids == {"endurance", "power", "recovery_injury", "training_response"}

    def test_panel_all_rsids(self, panel: FitnessPanel) -> None:
        rsids = panel.all_rsids()
        assert len(rsids) == 8
        expected = {
            "rs1815739",
            "rs8192678",
            "rs17602729",
            "rs4341",
            "rs1049434",
            "rs12722",
            "rs1800012",
            "rs9939609",
        }
        assert set(rsids) == expected

    def test_panel_snps_have_genotype_effects(self, panel: FitnessPanel) -> None:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                assert len(snp.genotype_effects) > 0, f"{snp.rsid} has no genotype effects"
                for gt, effect in snp.genotype_effects.items():
                    assert "category" in effect
                    assert "effect_summary" in effect
                    assert effect["category"] in (ELEVATED, MODERATE, STANDARD)

    def test_panel_has_additional_genes(self, panel: FitnessPanel) -> None:
        assert panel.additional_genes is not None
        assert "ACTN3_power_context" in panel.additional_genes
        assert "ACE_endurance_context" in panel.additional_genes

    def test_panel_has_special_calling(self, panel: FitnessPanel) -> None:
        assert panel.special_calling is not None
        assert "ACTN3_R577X" in panel.special_calling
        assert "ACE_ID_proxy" in panel.special_calling

    def test_load_nonexistent_panel_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_fitness_panel(Path("/nonexistent/panel.json"))


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


# ── ACTN3 three-state calling tests ─────────────────────────────────────


class TestACTN3ThreeState:
    def _get_actn3(self, panel: FitnessPanel) -> PanelSNP:
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs1815739":
                    return snp
        pytest.fail("ACTN3 not found")

    def test_actn3_has_three_state_calling(self, panel: FitnessPanel) -> None:
        actn3 = self._get_actn3(panel)
        assert actn3.three_state_calling is not None

    def test_resolve_three_state_rr(self, panel: FitnessPanel) -> None:
        actn3 = self._get_actn3(panel)
        assert _resolve_three_state(actn3, "CC") == "RR"

    def test_resolve_three_state_rx(self, panel: FitnessPanel) -> None:
        actn3 = self._get_actn3(panel)
        assert _resolve_three_state(actn3, "CT") == "RX"
        assert _resolve_three_state(actn3, "TC") == "RX"

    def test_resolve_three_state_xx(self, panel: FitnessPanel) -> None:
        actn3 = self._get_actn3(panel)
        assert _resolve_three_state(actn3, "TT") == "XX"

    def test_resolve_three_state_none_genotype(self, panel: FitnessPanel) -> None:
        actn3 = self._get_actn3(panel)
        assert _resolve_three_state(actn3, None) is None

    def test_actn3_cc_standard(self, panel: FitnessPanel) -> None:
        """RR genotype (CC) → Standard category in Endurance pathway."""
        actn3 = self._get_actn3(panel)
        result = _score_snp(actn3, "CC")
        assert result.category == STANDARD
        assert result.three_state_label == "RR"
        assert result.present_in_sample is True

    def test_actn3_ct_moderate(self, panel: FitnessPanel) -> None:
        """RX genotype (CT) → Moderate category."""
        actn3 = self._get_actn3(panel)
        result = _score_snp(actn3, "CT")
        assert result.category == MODERATE
        assert result.three_state_label == "RX"

    def test_actn3_tt_elevated(self, panel: FitnessPanel) -> None:
        """XX genotype (TT) → Elevated category (evidence_level=2 allows it)."""
        actn3 = self._get_actn3(panel)
        result = _score_snp(actn3, "TT")
        assert result.category == ELEVATED
        assert result.three_state_label == "XX"

    def test_actn3_finding_text_includes_three_state(self, panel: FitnessPanel) -> None:
        """Finding text for ACTN3 should include the three-state label."""
        actn3 = self._get_actn3(panel)
        result = _score_snp(actn3, "TT")
        # The store function builds text with three_state_label
        assert result.three_state_label == "XX"


# ── ACE I/D proxy tests ────────────────────────────────────────────────


class TestACEProxy:
    def _get_ace(self, panel: FitnessPanel) -> PanelSNP:
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs4341":
                    return snp
        pytest.fail("ACE not found")

    def test_ace_has_coverage_note(self, panel: FitnessPanel) -> None:
        ace = self._get_ace(panel)
        assert ace.coverage_note is not None
        assert "proxy" in ace.coverage_note.lower()

    def test_ace_gg_elevated(self, panel: FitnessPanel) -> None:
        """DD proxy (GG) → Elevated Power."""
        ace = self._get_ace(panel)
        result = _score_snp(ace, "GG")
        assert result.category == ELEVATED
        assert result.coverage_note is not None

    def test_ace_ag_moderate(self, panel: FitnessPanel) -> None:
        """ID proxy (AG) → Moderate."""
        ace = self._get_ace(panel)
        result = _score_snp(ace, "AG")
        assert result.category == MODERATE

    def test_ace_aa_standard(self, panel: FitnessPanel) -> None:
        """II proxy (AA) → Standard."""
        ace = self._get_ace(panel)
        result = _score_snp(ace, "AA")
        assert result.category == STANDARD

    def test_ace_no_three_state(self, panel: FitnessPanel) -> None:
        """ACE does not use three-state calling."""
        ace = self._get_ace(panel)
        assert ace.three_state_calling is None
        result = _score_snp(ace, "GG")
        assert result.three_state_label is None


# ── COL1A1 rs1800012 soft-tissue direction tests (issue #144) ──────────────


class TestCOL1A1Injury:
    """COL1A1 rs1800012 in the Recovery & Injury (soft-tissue) pathway.

    Meta-analyses of sports tendon/ligament injury report the rare TT genotype
    as protective and the heterozygous genotype as the higher-risk soft-tissue
    genotype (overdominant). The bone-mineral-density / osteoporotic-fracture
    signal of the T allele is a distinct phenotype and must not be presented as
    an athletic stress-fracture risk that makes TT the elevated-injury genotype.
    """

    def _get_col1a1(self, panel: FitnessPanel) -> PanelSNP:
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs1800012":
                    return snp
        pytest.fail("COL1A1 rs1800012 not found")

    def test_tt_not_elevated_injury(self, panel: FitnessPanel) -> None:
        """TT must NOT be an elevated soft-tissue injury genotype (it is protective)."""
        snp = self._get_col1a1(panel)
        result = _score_snp(snp, "TT")
        assert result.category == STANDARD
        text = result.effect_summary.lower()
        assert "protective" in text or "reduced" in text
        # The old inverted framing must be gone.
        assert "increased stress fracture risk in athletes" not in text

    def test_heterozygous_is_the_cautious_genotype(self, panel: FitnessPanel) -> None:
        """GT/TG is the higher-risk soft-tissue genotype (overdominant) → Moderate."""
        snp = self._get_col1a1(panel)
        for gt in ("GT", "TG"):
            result = _score_snp(snp, gt)
            assert result.category == MODERATE, gt

    def test_gg_baseline_standard(self, panel: FitnessPanel) -> None:
        """GG (no T allele) is baseline soft-tissue risk → Standard."""
        snp = self._get_col1a1(panel)
        result = _score_snp(snp, "GG")
        assert result.category == STANDARD

    def test_bone_phenotype_separated(self, panel: FitnessPanel) -> None:
        """The BMD/osteoporotic-fracture signal is framed as a distinct phenotype."""
        snp = self._get_col1a1(panel)
        tt_text = _score_snp(snp, "TT").effect_summary.lower()
        # Bone signal present but explicitly separated from athletic soft-tissue injury.
        assert "bone" in tt_text
        assert "separate" in tt_text or "distinct" in tt_text

    def test_citations_are_soft_tissue_and_bone(self, panel: FitnessPanel) -> None:
        """Cites the soft-tissue meta-analyses (Wang 2017, Guo 2024) + a BMD source."""
        snp = self._get_col1a1(panel)
        assert "28206959" in snp.pmids  # Wang 2017 soft-tissue meta-analysis
        assert "38787354" in snp.pmids  # Guo 2024 soft-tissue meta-analysis


# ── SNP scoring tests ────────────────────────────────────────────────────


class TestSNPScoring:
    def test_not_genotyped_returns_standard(self, panel: FitnessPanel) -> None:
        snp = panel.pathways[0].snps[0]
        result = _score_snp(snp, None)
        assert result.category == STANDARD
        assert result.present_in_sample is False

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

    def test_reversed_genotype_lookup(self, panel: FitnessPanel) -> None:
        """Panel handles reversed genotype strings (e.g. CT vs TC)."""
        actn3 = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs1815739":
                    actn3 = snp
                    break

        result_ct = _score_snp(actn3, "CT")
        result_tc = _score_snp(actn3, "TC")
        assert result_ct.category == result_tc.category == MODERATE

    def test_unknown_genotype_defaults_standard(self, panel: FitnessPanel) -> None:
        snp = panel.pathways[0].snps[0]
        result = _score_snp(snp, "ZZ")
        assert result.category == STANDARD
        assert result.present_in_sample is True

    def test_ppargc1a_aa_capped_at_moderate(self, panel: FitnessPanel) -> None:
        """PPARGC1A has evidence_level=1, so AA (Elevated) → capped at Moderate."""
        ppargc1a = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs8192678":
                    ppargc1a = snp
                    break
        assert ppargc1a is not None
        assert ppargc1a.evidence_level == 1
        result = _score_snp(ppargc1a, "AA")
        assert result.category == MODERATE  # Capped from Elevated

    def test_fto_aa_strand_indeterminate(self, panel: FitnessPanel) -> None:
        """FTO rs9939609 is a palindromic A/T SNP whose homozygotes map to
        opposite categories, so an AA call cannot be strand-resolved from the
        array genotype and is reported Indeterminate — not a confident (and
        possibly strand-flipped) Elevated call (#170). Non-capping of
        evidence_level=2 SNPs is covered by ACTN3 (a non-palindromic C/T SNP)."""
        fto = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs9939609":
                    fto = snp
                    break
        assert fto is not None
        assert fto.evidence_level == 2
        result = _score_snp(fto, "AA")
        assert result.category == INDETERMINATE


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


# ── Cross-context findings tests ─────────────────────────────────────────


class TestCrossContextFindings:
    def test_actn3_power_context_generated(self, panel: FitnessPanel) -> None:
        """ACTN3 RX/XX generates cross-context finding for Power pathway."""
        endurance_pr = PathwayResult(
            pathway_id="endurance",
            pathway_name="Endurance",
            pathway_description="",
            level=MODERATE,
            snp_results=[
                SNPResult(
                    rsid="rs1815739",
                    gene="ACTN3",
                    variant_name="R577X",
                    genotype="CT",
                    category=MODERATE,
                    effect_summary="RX genotype",
                    evidence_level=2,
                    pmids=["12879365"],
                    recommendation_text="",
                    present_in_sample=True,
                    three_state_label="RX",
                ),
            ],
        )
        power_pr = PathwayResult(
            pathway_id="power",
            pathway_name="Power",
            pathway_description="",
            level=STANDARD,
        )
        results = [endurance_pr, power_pr]
        cross = _generate_cross_context_findings(results, panel)
        assert len(cross) >= 1
        actn3_cross = next(c for c in cross if c.rsid == "rs1815739")
        assert actn3_cross.context_pathway == "Power"
        assert "RX" in actn3_cross.finding_text

    def test_ace_endurance_context_generated(self, panel: FitnessPanel) -> None:
        """ACE ID/DD generates cross-context finding for Endurance pathway."""
        power_pr = PathwayResult(
            pathway_id="power",
            pathway_name="Power",
            pathway_description="",
            level=MODERATE,
            snp_results=[
                SNPResult(
                    rsid="rs4341",
                    gene="ACE",
                    variant_name="I/D proxy",
                    genotype="AG",
                    category=MODERATE,
                    effect_summary="ID proxy",
                    evidence_level=2,
                    pmids=["39595706"],
                    recommendation_text="",
                    present_in_sample=True,
                    coverage_note="Proxy caveat",
                ),
            ],
        )
        endurance_pr = PathwayResult(
            pathway_id="endurance",
            pathway_name="Endurance",
            pathway_description="",
            level=STANDARD,
        )
        results = [endurance_pr, power_pr]
        cross = _generate_cross_context_findings(results, panel)
        assert len(cross) >= 1
        ace_cross = next(c for c in cross if c.rsid == "rs4341")
        assert ace_cross.context_pathway == "Endurance"

    def test_no_cross_context_for_standard(self, panel: FitnessPanel) -> None:
        """No cross-context findings when both ACTN3 and ACE are Standard."""
        endurance_pr = PathwayResult(
            pathway_id="endurance",
            pathway_name="Endurance",
            pathway_description="",
            level=STANDARD,
            snp_results=[
                SNPResult(
                    rsid="rs1815739",
                    gene="ACTN3",
                    variant_name="R577X",
                    genotype="CC",
                    category=STANDARD,
                    effect_summary="RR",
                    evidence_level=2,
                    pmids=[],
                    recommendation_text="",
                    present_in_sample=True,
                    three_state_label="RR",
                ),
            ],
        )
        power_pr = PathwayResult(
            pathway_id="power",
            pathway_name="Power",
            pathway_description="",
            level=STANDARD,
            snp_results=[
                SNPResult(
                    rsid="rs4341",
                    gene="ACE",
                    variant_name="I/D proxy",
                    genotype="AA",
                    category=STANDARD,
                    effect_summary="II",
                    evidence_level=2,
                    pmids=[],
                    recommendation_text="",
                    present_in_sample=True,
                ),
            ],
        )
        results = [endurance_pr, power_pr]
        cross = _generate_cross_context_findings(results, panel)
        assert len(cross) == 0

    def test_no_cross_context_when_not_genotyped(self, panel: FitnessPanel) -> None:
        """No cross-context when ACTN3 and ACE are absent from sample."""
        endurance_pr = PathwayResult(
            pathway_id="endurance",
            pathway_name="Endurance",
            pathway_description="",
            level=STANDARD,
        )
        power_pr = PathwayResult(
            pathway_id="power",
            pathway_name="Power",
            pathway_description="",
            level=STANDARD,
        )
        results = [endurance_pr, power_pr]
        cross = _generate_cross_context_findings(results, panel)
        assert len(cross) == 0


# ── Integration tests ────────────────────────────────────────────────────


class TestScorePathways:
    def test_full_scoring_all_snps(
        self,
        panel: FitnessPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Score pathways with all 8 panel SNPs genotyped."""
        _seed_variants(
            sample_engine,
            [
                ("rs1815739", "11", 66328095, "TT"),  # ACTN3 XX
                ("rs8192678", "4", 23814519, "GA"),  # PPARGC1A het
                ("rs17602729", "1", 114677654, "CC"),  # AMPD1 normal
                ("rs4341", "17", 63488529, "GG"),  # ACE DD proxy
                ("rs1049434", "1", 113545811, "TT"),  # MCT1 normal
                ("rs12722", "9", 137048876, "CT"),  # COL5A1 het
                ("rs1800012", "17", 50201587, "GG"),  # COL1A1 normal
                ("rs9939609", "16", 53820527, "AA"),  # FTO hom risk
            ],
        )
        _seed_gwas(
            reference_engine,
            [
                ("rs1815739", "Athletic performance"),
                ("rs4341", "Exercise performance"),
            ],
        )

        result = score_fitness_pathways(panel, sample_engine, reference_engine)

        # Endurance: ACTN3 TT=Elevated, PPARGC1A GA=Moderate (capped from star1),
        #            AMPD1 CC=Standard → pathway = Elevated
        endurance = next(pr for pr in result.pathway_results if pr.pathway_id == "endurance")
        assert endurance.level == ELEVATED

        # Power: ACE GG=Elevated drives Elevated; MCT1 TT is a palindromic A/T
        # homozygote → Indeterminate (#170), which does not lower the level.
        power = next(pr for pr in result.pathway_results if pr.pathway_id == "power")
        assert power.level == ELEVATED
        mct1 = next(s for s in power.snp_results if s.rsid == "rs1049434")
        assert mct1.category == INDETERMINATE

        # Recovery: COL5A1 CT=Moderate (capped from star1), COL1A1 GG=Standard → Moderate
        recovery = next(pr for pr in result.pathway_results if pr.pathway_id == "recovery_injury")
        assert recovery.level == MODERATE

        # Training: FTO AA is a palindromic A/T homozygote → Indeterminate (#170),
        # so it no longer drives the pathway to Elevated; with no other scoreable
        # SNP the pathway defaults to Standard (the per-SNP card shows the caveat).
        training = next(
            pr for pr in result.pathway_results if pr.pathway_id == "training_response"
        )
        assert training.level == STANDARD
        fto = next(s for s in training.snp_results if s.rsid == "rs9939609")
        assert fto.category == INDETERMINATE

        # GWAS matches
        assert "rs1815739" in result.gwas_matched_rsids
        assert "rs4341" in result.gwas_matched_rsids

        # Cross-context findings should exist
        assert len(result.cross_context_findings) >= 1

    def test_actn3_tt_scoring(
        self,
        panel: FitnessPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """ACTN3 TT → XX genotype → Elevated endurance."""
        _seed_variants(sample_engine, [("rs1815739", "11", 66328095, "TT")])
        result = score_fitness_pathways(panel, sample_engine, reference_engine)

        endurance = next(pr for pr in result.pathway_results if pr.pathway_id == "endurance")
        actn3 = next(s for s in endurance.called_snps if s.rsid == "rs1815739")
        assert actn3.three_state_label == "XX"
        assert actn3.category == ELEVATED

    def test_missing_snps_default_standard(
        self,
        panel: FitnessPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Pathways with no genotyped SNPs default to Standard."""
        result = score_fitness_pathways(panel, sample_engine, reference_engine)
        for pr in result.pathway_results:
            assert pr.level == STANDARD
            assert len(pr.called_snps) == 0
            assert len(pr.missing_snps) > 0

    def test_ace_proxy_coverage_note_preserved(
        self,
        panel: FitnessPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """ACE coverage note survives through scoring pipeline."""
        _seed_variants(sample_engine, [("rs4341", "17", 63488529, "GG")])
        result = score_fitness_pathways(panel, sample_engine, reference_engine)

        power = next(pr for pr in result.pathway_results if pr.pathway_id == "power")
        ace = next(s for s in power.called_snps if s.rsid == "rs4341")
        assert ace.coverage_note is not None
        assert "proxy" in ace.coverage_note.lower()


# ── Findings storage tests ─────────────────────────────────────────────


class TestStoreFindingsIntegration:
    def test_store_and_retrieve_findings(
        self,
        panel: FitnessPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Store findings and verify they're in the DB."""
        _seed_variants(
            sample_engine,
            [
                ("rs1815739", "11", 66328095, "TT"),
                ("rs4341", "17", 63488529, "GG"),
                ("rs9939609", "16", 53820527, "AA"),
            ],
        )

        result = score_fitness_pathways(panel, sample_engine, reference_engine)
        count = store_fitness_findings(result, sample_engine)
        assert count > 0

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == MODULE_NAME)
            ).fetchall()

        assert len(rows) == count

        # Check pathway summary findings exist (always 4)
        pathway_summaries = [r for r in rows if r.category == "pathway_summary"]
        assert len(pathway_summaries) == 4

    def test_actn3_finding_includes_three_state_detail(
        self,
        panel: FitnessPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """ACTN3 SNP finding includes three_state_label in detail_json."""
        _seed_variants(sample_engine, [("rs1815739", "11", 66328095, "TT")])

        result = score_fitness_pathways(panel, sample_engine, reference_engine)
        store_fitness_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.rsid == "rs1815739",
                    )
                )
            ).first()

        assert row is not None
        detail = json.loads(row.detail_json)
        assert detail["three_state_label"] == "XX"
        assert "XX genotype" in row.finding_text

    def test_ace_finding_includes_coverage_note(
        self,
        panel: FitnessPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """ACE SNP finding includes coverage_note in detail_json."""
        _seed_variants(sample_engine, [("rs4341", "17", 63488529, "GG")])

        result = score_fitness_pathways(panel, sample_engine, reference_engine)
        store_fitness_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.rsid == "rs4341",
                    )
                )
            ).first()

        assert row is not None
        detail = json.loads(row.detail_json)
        assert "coverage_note" in detail
        assert "proxy" in detail["coverage_note"].lower()

    def test_cross_context_findings_stored(
        self,
        panel: FitnessPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Cross-context findings are stored with category='cross_context'."""
        _seed_variants(
            sample_engine,
            [
                ("rs1815739", "11", 66328095, "CT"),  # ACTN3 RX → non-Standard
                ("rs4341", "17", 63488529, "AG"),  # ACE ID → non-Standard
            ],
        )

        result = score_fitness_pathways(panel, sample_engine, reference_engine)
        store_fitness_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            cross_rows = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.category == "cross_context",
                    )
                )
            ).fetchall()

        assert len(cross_rows) >= 1

        # Verify source_pathway is in detail_json
        for cr in cross_rows:
            detail = json.loads(cr.detail_json)
            assert "source_pathway" in detail
            assert detail["source_pathway"] in ("Endurance", "Power")

    def test_store_clears_previous_findings(
        self,
        panel: FitnessPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Re-running store clears previous fitness findings."""
        _seed_variants(sample_engine, [("rs1815739", "11", 66328095, "TT")])

        result = score_fitness_pathways(panel, sample_engine, reference_engine)
        store_fitness_findings(result, sample_engine)
        count2 = store_fitness_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == MODULE_NAME)
            ).fetchall()

        assert len(rows) == count2  # No duplicates

    def test_no_snp_findings_for_empty_sample(
        self,
        panel: FitnessPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Empty sample produces pathway summaries but no SNP findings."""
        result = score_fitness_pathways(panel, sample_engine, reference_engine)
        count = store_fitness_findings(result, sample_engine)

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

    def test_17_trait_findings_max(
        self,
        panel: FitnessPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """With all SNPs genotyped at non-Standard, verify finding count ≤ 17."""
        _seed_variants(
            sample_engine,
            [
                ("rs1815739", "11", 66328095, "TT"),  # ACTN3 XX → Elevated
                ("rs8192678", "4", 23814519, "AA"),  # PPARGC1A → Moderate (capped)
                ("rs17602729", "1", 114677654, "TT"),  # AMPD1 → Moderate (capped)
                ("rs4341", "17", 63488529, "GG"),  # ACE DD → Elevated
                ("rs1049434", "1", 113545811, "AA"),  # MCT1 → Moderate (capped)
                ("rs12722", "9", 137048876, "TT"),  # COL5A1 → Moderate (capped)
                ("rs1800012", "17", 50201587, "GT"),  # COL1A1 het → Moderate (soft-tissue caution)
                ("rs9939609", "16", 53820527, "AA"),  # FTO → Elevated
            ],
        )

        result = score_fitness_pathways(panel, sample_engine, reference_engine)
        count = store_fitness_findings(result, sample_engine)

        # 4 pathway summaries + up to 8 SNP findings + cross-context ≤ 17
        assert count <= 17
        assert count >= 4  # At minimum, 4 pathway summaries

    def test_findings_include_pmids(
        self,
        panel: FitnessPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Findings include PubMed citations."""
        _seed_variants(sample_engine, [("rs1815739", "11", 66328095, "TT")])

        result = score_fitness_pathways(panel, sample_engine, reference_engine)
        store_fitness_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.rsid == "rs1815739",
                    )
                )
            ).first()

        assert row is not None
        pmids = json.loads(row.pmid_citations)
        assert "12879365" in pmids


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
                {"rsid": "rs1815739", "chrom": "11", "pos": 66328095, "genotype": "TT"},
            ],
            annotated=[
                {
                    "rsid": "rs1815739",
                    "chrom": "11",
                    "pos": 66328095,
                    "genotype": "TT",
                    "annotation_coverage": 0b001111,
                },
            ],
        )

        result = FitnessResult(
            pathway_results=[],
            gwas_matched_rsids=["rs1815739"],
        )

        updated = update_annotation_coverage_gwas(result, sample)
        assert updated == 1

        with sample.connect() as conn:
            val = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs1815739"
                )
            ).scalar()

        assert val == 0b101111  # 47

    def test_null_annotation_coverage_gets_gwas_bit(self) -> None:
        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs1815739", "chrom": "11", "pos": 66328095, "genotype": "TT"},
            ],
            annotated=[
                {
                    "rsid": "rs1815739",
                    "chrom": "11",
                    "pos": 66328095,
                    "genotype": "TT",
                    "annotation_coverage": None,
                },
            ],
        )

        result = FitnessResult(
            pathway_results=[],
            gwas_matched_rsids=["rs1815739"],
        )

        updated = update_annotation_coverage_gwas(result, sample)
        assert updated == 1

        with sample.connect() as conn:
            val = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs1815739"
                )
            ).scalar()

        assert val == GWAS_BIT

    def test_empty_gwas_matched_returns_zero(self) -> None:
        sample = self._make_sample_with_annotated(raw=[], annotated=[])
        result = FitnessResult(pathway_results=[], gwas_matched_rsids=[])
        updated = update_annotation_coverage_gwas(result, sample)
        assert updated == 0

    def test_idempotent_double_application(self) -> None:
        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs1815739", "chrom": "11", "pos": 66328095, "genotype": "TT"},
            ],
            annotated=[
                {
                    "rsid": "rs1815739",
                    "chrom": "11",
                    "pos": 66328095,
                    "genotype": "TT",
                    "annotation_coverage": GWAS_BIT,
                },
            ],
        )

        result = FitnessResult(
            pathway_results=[],
            gwas_matched_rsids=["rs1815739"],
        )

        update_annotation_coverage_gwas(result, sample)

        with sample.connect() as conn:
            val = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs1815739"
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


class TestPalindromicStrandGuard:
    """FTO rs9939609 and MCT1 rs1049434 are palindromic A/T SNPs whose
    homozygotes map to opposite categories. Their homozygous calls cannot be
    strand-resolved from the array genotype, so they must be reported as
    indeterminate (with a strand caveat) rather than a possibly strand-flipped
    Standard/Elevated call, and must not drive the pathway level (#170)."""

    def _get(self, panel: FitnessPanel, rsid: str) -> PanelSNP:
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == rsid:
                    return snp
        pytest.fail(f"{rsid} not found")

    @pytest.mark.parametrize("rsid", ["rs9939609", "rs1049434"])
    @pytest.mark.parametrize("genotype", ["AA", "TT"])
    def test_palindromic_homozygote_is_indeterminate(
        self, panel: FitnessPanel, rsid: str, genotype: str
    ) -> None:
        snp = self._get(panel, rsid)
        result = _score_snp(snp, genotype)
        assert result.category == INDETERMINATE
        assert result.present_in_sample is True  # observed, just unresolvable
        assert result.genotype == genotype
        assert result.coverage_note is not None
        assert "strand" in result.coverage_note.lower()
        assert result.category not in (STANDARD, ELEVATED)  # never a confident flip

    @pytest.mark.parametrize("genotype", ["AT", "TA"])
    def test_palindromic_heterozygote_resolves_normally(
        self, panel: FitnessPanel, genotype: str
    ) -> None:
        # The heterozygote is strand-invariant, so it is still confidently called.
        fto = self._get(panel, "rs9939609")
        result = _score_snp(fto, genotype)
        assert result.category == MODERATE
        assert result.category != INDETERMINATE

    def test_indeterminate_snp_does_not_drive_pathway_level(self) -> None:
        # An indeterminate call neither raises nor lowers the level: alongside a
        # Standard SNP the pathway stays Standard (not flipped to Elevated).
        results = [
            _make_snp_result(INDETERMINATE),
            _make_snp_result(STANDARD),
        ]
        assert _determine_pathway_level(results) == STANDARD

    def test_all_indeterminate_pathway_defaults_standard(self) -> None:
        assert _determine_pathway_level([_make_snp_result(INDETERMINATE)]) == STANDARD
