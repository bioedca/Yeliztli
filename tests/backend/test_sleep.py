"""Tests for the Gene Sleep module (P3-49).

Covers:
  - Panel loading and dataclass construction
  - CYP1A2 caffeine metabolizer calling (rapid/intermediate/slow)
  - rs2858884 HLA-DQ region marker (informational, not a DQB1*06:02 proxy)
  - Genotype normalization
  - SNP scoring with evidence-level gating
  - Pathway level determination (highest category)
  - CYP1A2 cross-module reference to Pharmacogenomics (read, not re-compute)
  - Full scoring integration with sample DB
  - Findings storage and retrieval
  - GWAS annotation_coverage bitmask (bit 5)
  - 14 trait finding count verification
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.sleep import (
    ELEVATED,
    MODERATE,
    MODULE_NAME,
    STANDARD,
    PanelSNP,
    PathwayResult,
    SleepPanel,
    SleepResult,
    SNPResult,
    _determine_pathway_level,
    _generate_cross_module_findings,
    _normalize_genotype,
    _resolve_metabolizer_state,
    _score_snp,
    load_sleep_panel,
    score_sleep_pathways,
    store_sleep_findings,
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
    / "sleep_panel.json"
)


@pytest.fixture()
def panel() -> SleepPanel:
    """Load the actual curated panel."""
    return load_sleep_panel(PANEL_PATH)


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
    def test_load_panel_succeeds(self, panel: SleepPanel) -> None:
        assert panel.module == "sleep"
        assert panel.version == "1.0.0"

    def test_panel_has_three_pathways(self, panel: SleepPanel) -> None:
        # chronotype_circadian removed (#615): its sole marker rs57875989 is the
        # PER3 54-bp VNTR (deprecated/unplaced, not array-typeable), no proxy exists.
        assert len(panel.pathways) == 3
        pathway_ids = {p.id for p in panel.pathways}
        assert pathway_ids == {
            "caffeine_sleep",
            "sleep_quality",
            "sleep_disorders",
        }

    def test_panel_all_rsids(self, panel: SleepPanel) -> None:
        rsids = panel.all_rsids()
        assert len(rsids) == 5
        expected = {
            "rs762551",
            "rs5751876",
            "rs2300478",
            "rs9357271",
            "rs2858884",
        }
        assert set(rsids) == expected

    def test_panel_snps_have_genotype_effects(self, panel: SleepPanel) -> None:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                assert len(snp.genotype_effects) > 0, f"{snp.rsid} has no genotype effects"
                for gt, effect in snp.genotype_effects.items():
                    assert "category" in effect
                    assert "effect_summary" in effect
                    assert effect["category"] in (ELEVATED, MODERATE, STANDARD)

    def test_panel_has_additional_genes(self, panel: SleepPanel) -> None:
        assert panel.additional_genes is not None
        assert "CYP1A2_pgx_context" in panel.additional_genes

    def test_panel_has_special_calling(self, panel: SleepPanel) -> None:
        assert panel.special_calling is not None
        assert "CYP1A2_metabolizer" in panel.special_calling
        assert "HLA_DQ_region_marker" in panel.special_calling
        # PER3_VNTR_proxy removed (#615): the marker was the dead PER3 VNTR.
        assert "PER3_VNTR_proxy" not in panel.special_calling

    def test_load_nonexistent_panel_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_sleep_panel(Path("/nonexistent/panel.json"))


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


# ── CYP1A2 metabolizer calling tests ──────────────────────────────────────


class TestCYP1A2Metabolizer:
    def _get_cyp1a2(self, panel: SleepPanel) -> PanelSNP:
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs762551":
                    return snp
        pytest.fail("CYP1A2 not found")

    def test_resolve_metabolizer_rapid(self, panel: SleepPanel) -> None:
        assert _resolve_metabolizer_state(panel, "AA") == "Rapid metabolizer"

    def test_resolve_metabolizer_intermediate(self, panel: SleepPanel) -> None:
        assert _resolve_metabolizer_state(panel, "AC") == "Intermediate metabolizer"
        assert _resolve_metabolizer_state(panel, "CA") == "Intermediate metabolizer"

    def test_resolve_metabolizer_slow(self, panel: SleepPanel) -> None:
        assert _resolve_metabolizer_state(panel, "CC") == "Slow metabolizer"

    def test_resolve_metabolizer_none_genotype(self, panel: SleepPanel) -> None:
        assert _resolve_metabolizer_state(panel, None) is None

    # ── Strand harmonization (#585) ──────────────────────────────────────
    # rs762551 is curated plus-strand (Ensembl GRCh37 C/A), but a vendor may
    # report the complement (design) strand. These complement-strand calls must
    # resolve to the same metabolizer state; before #585 the raw ==/in test
    # silently dropped them to None.

    def test_resolve_metabolizer_rapid_complement_strand(self, panel: SleepPanel) -> None:
        # TT is the complement of AA (Rapid). Fails on the old raw == test.
        assert _resolve_metabolizer_state(panel, "TT") == "Rapid metabolizer"

    def test_resolve_metabolizer_intermediate_complement_strand(self, panel: SleepPanel) -> None:
        # TG / GT are the complement-strand forms of AC / CA (Intermediate).
        assert _resolve_metabolizer_state(panel, "TG") == "Intermediate metabolizer"
        assert _resolve_metabolizer_state(panel, "GT") == "Intermediate metabolizer"

    def test_resolve_metabolizer_slow_complement_strand(self, panel: SleepPanel) -> None:
        # GG is the complement of CC (Slow). Fails on the old raw == test.
        assert _resolve_metabolizer_state(panel, "GG") == "Slow metabolizer"

    def test_resolve_metabolizer_unrelated_genotype_is_none(self, panel: SleepPanel) -> None:
        # A genotype with no plus- or complement-strand match stays None.
        assert _resolve_metabolizer_state(panel, "AT") is None

    def test_cyp1a2_aa_standard(self, panel: SleepPanel) -> None:
        """Rapid metabolizer (AA) → Standard category."""
        cyp = self._get_cyp1a2(panel)
        result = _score_snp(cyp, "AA", panel)
        assert result.category == STANDARD
        assert result.metabolizer_state == "Rapid metabolizer"
        assert result.present_in_sample is True

    def test_cyp1a2_ac_moderate(self, panel: SleepPanel) -> None:
        """Intermediate metabolizer (AC) → Moderate category."""
        cyp = self._get_cyp1a2(panel)
        result = _score_snp(cyp, "AC", panel)
        assert result.category == MODERATE
        assert result.metabolizer_state == "Intermediate metabolizer"

    def test_cyp1a2_cc_elevated(self, panel: SleepPanel) -> None:
        """Slow metabolizer (CC) → Elevated category (evidence_level=2 allows it)."""
        cyp = self._get_cyp1a2(panel)
        result = _score_snp(cyp, "CC", panel)
        assert result.category == ELEVATED
        assert result.metabolizer_state == "Slow metabolizer"

    def test_cyp1a2_has_cross_module(self, panel: SleepPanel) -> None:
        """CYP1A2 must have cross-module reference to pharmacogenomics."""
        cyp = self._get_cyp1a2(panel)
        assert cyp.cross_module is not None
        assert cyp.cross_module["module"] == "pharmacogenomics"


# ── rs2858884 HLA-DQ region marker tests ────────────────────────────────


class TestHLAProxy:
    def _get_hla(self, panel: SleepPanel) -> PanelSNP:
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs2858884":
                    return snp
        pytest.fail("rs2858884 not found")

    def test_hla_has_coverage_note(self, panel: SleepPanel) -> None:
        hla = self._get_hla(panel)
        assert hla.coverage_note is not None
        # The marker must be explicitly flagged as NOT a valid proxy.
        assert "not" in hla.coverage_note.lower()
        assert "proxy" in hla.coverage_note.lower()

    def test_hla_coverage_note_explains_misclassification(self, panel: SleepPanel) -> None:
        """Coverage note must explain why rs2858884 is not a DQB1*06:02 proxy."""
        hla = self._get_hla(panel)
        assert hla.coverage_note is not None
        note = hla.coverage_note.lower()
        assert "dqb1*06:02" in note
        assert "matched" in note  # GWAS matched cases/controls on DQB1*06:02
        assert "protective" in note

    def test_hla_all_genotypes_standard(self, panel: SleepPanel) -> None:
        """No genotype yields a narcolepsy risk call — all map to Standard."""
        hla = self._get_hla(panel)
        for genotype in ("CC", "CT", "TC", "TT"):
            result = _score_snp(hla, genotype, panel)
            assert result.category == STANDARD, f"{genotype} should be Standard"
            assert result.coverage_note is not None

    def test_hla_no_risk_allele(self, panel: SleepPanel) -> None:
        """No risk allele is asserted for this informational marker."""
        hla = self._get_hla(panel)
        assert hla.risk_allele is None

    def test_hla_no_metabolizer_state(self, panel: SleepPanel) -> None:
        """The HLA marker should not have a metabolizer state."""
        hla = self._get_hla(panel)
        result = _score_snp(hla, "TT", panel)
        assert result.metabolizer_state is None


# ── SNP scoring tests ────────────────────────────────────────────────────


class TestSNPScoring:
    def test_not_genotyped_returns_standard(self, panel: SleepPanel) -> None:
        snp = panel.pathways[0].snps[0]
        result = _score_snp(snp, None, panel)
        assert result.category == STANDARD
        assert result.present_in_sample is False

    def test_evidence_gating_caps_at_moderate(self, panel: SleepPanel) -> None:
        """★☆ evidence hard-caps at Moderate (key rule)."""
        snp = _make_test_snp(evidence_level=1, genotype_category=ELEVATED)
        result = _score_snp(snp, "AA", panel)
        assert result.category == MODERATE

    def test_evidence_level_2_allows_elevated(self, panel: SleepPanel) -> None:
        """★★ evidence allows Elevated when genotype warrants it."""
        snp = _make_test_snp(evidence_level=2, genotype_category=ELEVATED)
        result = _score_snp(snp, "AA", panel)
        assert result.category == ELEVATED

    def test_reversed_genotype_lookup(self, panel: SleepPanel) -> None:
        """Panel handles reversed genotype strings (e.g. CT vs TC)."""
        adora2a = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs5751876":
                    adora2a = snp
                    break
        assert adora2a is not None

        result_ct = _score_snp(adora2a, "CT", panel)
        result_tc = _score_snp(adora2a, "TC", panel)
        assert result_ct.category == result_tc.category == MODERATE

    def test_unknown_genotype_defaults_standard(self, panel: SleepPanel) -> None:
        snp = panel.pathways[0].snps[0]
        result = _score_snp(snp, "ZZ", panel)
        assert result.category == STANDARD
        assert result.present_in_sample is True

    def test_adora2a_tt_capped_at_moderate(self, panel: SleepPanel) -> None:
        """ADORA2A has evidence_level=1, so TT (Elevated) → capped at Moderate."""
        adora2a = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs5751876":
                    adora2a = snp
                    break
        assert adora2a is not None
        assert adora2a.evidence_level == 1
        result = _score_snp(adora2a, "TT", panel)
        assert result.category == MODERATE  # Capped from Elevated

    def test_meis1_gg_elevated(self, panel: SleepPanel) -> None:
        """MEIS1 has evidence_level=2, so GG (Elevated) → Elevated."""
        meis1 = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs2300478":
                    meis1 = snp
                    break
        assert meis1 is not None
        assert meis1.evidence_level == 2
        result = _score_snp(meis1, "GG", panel)
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


# ── Cross-module reference tests ─────────────────────────────────────────


class TestCrossModuleFindings:
    def test_cyp1a2_pgx_cross_reference_generated(self, panel: SleepPanel) -> None:
        """T3-50: CYP1A2 cross-reference reads PGx finding without re-computing."""
        caffeine_pr = PathwayResult(
            pathway_id="caffeine_sleep",
            pathway_name="Caffeine & Sleep",
            level=ELEVATED,
            snp_results=[
                SNPResult(
                    rsid="rs762551",
                    gene="CYP1A2",
                    variant_name="*1F (-163C>A)",
                    genotype="CC",
                    category=ELEVATED,
                    effect_summary="Slow metabolizer",
                    evidence_level=2,
                    pmids=["16522833"],
                    recommendation_text="",
                    present_in_sample=True,
                    metabolizer_state="Slow metabolizer",
                ),
            ],
        )
        results = [caffeine_pr]
        cross = _generate_cross_module_findings(results, panel)
        assert len(cross) == 1
        assert cross[0].rsid == "rs762551"
        assert cross[0].target_module == "pharmacogenomics"
        assert cross[0].source_module == "sleep"
        assert "pharmacogenomics" in cross[0].finding_text.lower()
        assert "Slow metabolizer" in cross[0].finding_text

    def test_no_cross_reference_when_cyp1a2_not_genotyped(self, panel: SleepPanel) -> None:
        """No cross-module reference when CYP1A2 is not in sample."""
        caffeine_pr = PathwayResult(
            pathway_id="caffeine_sleep",
            pathway_name="Caffeine & Sleep",
            level=STANDARD,
        )
        results = [caffeine_pr]
        cross = _generate_cross_module_findings(results, panel)
        assert len(cross) == 0

    def test_cross_reference_includes_metabolizer_state(self, panel: SleepPanel) -> None:
        """Cross-module finding detail includes metabolizer state."""
        caffeine_pr = PathwayResult(
            pathway_id="caffeine_sleep",
            pathway_name="Caffeine & Sleep",
            level=MODERATE,
            snp_results=[
                SNPResult(
                    rsid="rs762551",
                    gene="CYP1A2",
                    variant_name="*1F",
                    genotype="AC",
                    category=MODERATE,
                    effect_summary="Intermediate",
                    evidence_level=2,
                    pmids=["16522833"],
                    recommendation_text="",
                    present_in_sample=True,
                    metabolizer_state="Intermediate metabolizer",
                ),
            ],
        )
        cross = _generate_cross_module_findings([caffeine_pr], panel)
        assert len(cross) == 1
        assert cross[0].detail["metabolizer_state"] == "Intermediate metabolizer"


# ── Integration tests ────────────────────────────────────────────────────


class TestScorePathways:
    def test_full_scoring_all_snps(
        self,
        panel: SleepPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Score pathways with all 5 panel SNPs genotyped."""
        _seed_variants(
            sample_engine,
            [
                ("rs762551", "15", 75041917, "CC"),  # CYP1A2 slow metabolizer
                ("rs5751876", "22", 24825044, "TT"),  # ADORA2A increased sensitivity
                ("rs2300478", "2", 66662600, "GG"),  # MEIS1 RLS risk
                ("rs9357271", "6", 38165204, "TT"),  # BTBD9 PLMS risk
                ("rs2858884", "6", 32632760, "TT"),  # HLA-DQ marker (informational)
            ],
        )
        _seed_gwas(
            reference_engine,
            [
                ("rs762551", "Caffeine metabolism"),
                ("rs2300478", "Restless legs syndrome"),
            ],
        )

        result = score_sleep_pathways(panel, sample_engine, reference_engine)

        # Caffeine & Sleep: CYP1A2 CC=Elevated, ADORA2A TT=Moderate (capped)
        #   → pathway = Elevated
        caffeine = next(pr for pr in result.pathway_results if pr.pathway_id == "caffeine_sleep")
        assert caffeine.level == ELEVATED

        # Sleep Quality: MEIS1 GG=Elevated (star2), BTBD9 TT=Moderate (capped, star1)
        #   → pathway = Elevated
        quality = next(pr for pr in result.pathway_results if pr.pathway_id == "sleep_quality")
        assert quality.level == ELEVATED

        # Sleep Disorders: rs2858884 is an informational HLA-DQ marker (Standard)
        #   → pathway = Standard (no narcolepsy risk inferred)
        disorders = next(pr for pr in result.pathway_results if pr.pathway_id == "sleep_disorders")
        assert disorders.level == STANDARD

        # GWAS matches
        assert "rs762551" in result.gwas_matched_rsids
        assert "rs2300478" in result.gwas_matched_rsids

        # Metabolizer state
        assert result.metabolizer_state == "Slow metabolizer"

        # Cross-module findings should exist
        assert len(result.cross_module_findings) == 1
        assert result.cross_module_findings[0].target_module == "pharmacogenomics"

    def test_cyp1a2_metabolizer_state(
        self,
        panel: SleepPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """CYP1A2 CC → Slow metabolizer state tracked in result."""
        _seed_variants(sample_engine, [("rs762551", "15", 75041917, "CC")])
        result = score_sleep_pathways(panel, sample_engine, reference_engine)

        caffeine = next(pr for pr in result.pathway_results if pr.pathway_id == "caffeine_sleep")
        cyp = next(s for s in caffeine.called_snps if s.rsid == "rs762551")
        assert cyp.metabolizer_state == "Slow metabolizer"
        assert cyp.category == ELEVATED
        assert result.metabolizer_state == "Slow metabolizer"

    def test_hla_proxy_finding_with_caveat(
        self,
        panel: SleepPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """rs2858884 is scored Standard (no narcolepsy risk) and keeps its caveat."""
        _seed_variants(sample_engine, [("rs2858884", "6", 32632760, "TT")])
        result = score_sleep_pathways(panel, sample_engine, reference_engine)

        disorders = next(pr for pr in result.pathway_results if pr.pathway_id == "sleep_disorders")
        hla = next(s for s in disorders.called_snps if s.rsid == "rs2858884")
        assert hla.category == STANDARD
        assert hla.coverage_note is not None
        assert "not" in hla.coverage_note.lower()
        assert "proxy" in hla.coverage_note.lower()

    def test_missing_snps_default_standard(
        self,
        panel: SleepPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Pathways with no genotyped SNPs default to Standard."""
        result = score_sleep_pathways(panel, sample_engine, reference_engine)
        for pr in result.pathway_results:
            assert pr.level == STANDARD
            assert len(pr.called_snps) == 0
            assert len(pr.missing_snps) > 0


# ── Findings storage tests ─────────────────────────────────────────────


class TestStoreFindingsIntegration:
    def test_store_and_retrieve_findings(
        self,
        panel: SleepPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Store findings and verify they're in the DB."""
        _seed_variants(
            sample_engine,
            [
                ("rs762551", "15", 75041917, "CC"),
                ("rs2300478", "2", 66662600, "GG"),
                ("rs2858884", "6", 32632760, "TT"),
            ],
        )

        result = score_sleep_pathways(panel, sample_engine, reference_engine)
        count = store_sleep_findings(result, sample_engine)
        assert count > 0

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == MODULE_NAME)
            ).fetchall()

        assert len(rows) == count

        # Check pathway summary findings exist (always 3 since #615 removed the
        # dead PER3 chronotype pathway)
        pathway_summaries = [r for r in rows if r.category == "pathway_summary"]
        assert len(pathway_summaries) == 3

    def test_metabolizer_state_finding_stored(
        self,
        panel: SleepPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """CYP1A2 metabolizer state generates its own finding."""
        _seed_variants(sample_engine, [("rs762551", "15", 75041917, "CC")])

        result = score_sleep_pathways(panel, sample_engine, reference_engine)
        store_sleep_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            met_rows = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.category == "metabolizer_state",
                    )
                )
            ).fetchall()

        assert len(met_rows) == 1
        assert "Slow metabolizer" in met_rows[0].finding_text

    def test_cross_module_finding_stored(
        self,
        panel: SleepPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """CYP1A2 PGx cross-module reference is stored."""
        _seed_variants(sample_engine, [("rs762551", "15", 75041917, "CC")])

        result = score_sleep_pathways(panel, sample_engine, reference_engine)
        store_sleep_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            cross_rows = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.category == "cross_module",
                    )
                )
            ).fetchall()

        assert len(cross_rows) == 1
        assert "pharmacogenomics" in cross_rows[0].finding_text.lower()
        detail = json.loads(cross_rows[0].detail_json)
        assert detail["target_module"] == "pharmacogenomics"

    def test_hla_finding_includes_coverage_note(
        self,
        panel: SleepPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """rs2858884 coverage_note surfaces in the Sleep Disorders pathway detail.

        The marker is scored Standard (no narcolepsy risk), so it produces no
        standalone snp_finding; its caveat rides along in the pathway summary.
        """
        _seed_variants(sample_engine, [("rs2858884", "6", 32632760, "TT")])

        result = score_sleep_pathways(panel, sample_engine, reference_engine)
        store_sleep_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.category == "pathway_summary",
                        findings.c.pathway == "Sleep Disorders",
                    )
                )
            ).first()

        assert row is not None
        detail = json.loads(row.detail_json)
        hla_detail = next(s for s in detail["snp_details"] if s["rsid"] == "rs2858884")
        assert hla_detail["category"] == "Standard"
        assert hla_detail["coverage_note"] is not None
        assert "not" in hla_detail["coverage_note"].lower()
        assert "proxy" in hla_detail["coverage_note"].lower()

    def test_store_clears_previous_findings(
        self,
        panel: SleepPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Re-running store clears previous sleep findings."""
        _seed_variants(sample_engine, [("rs762551", "15", 75041917, "CC")])

        result = score_sleep_pathways(panel, sample_engine, reference_engine)
        store_sleep_findings(result, sample_engine)
        count2 = store_sleep_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == MODULE_NAME)
            ).fetchall()

        assert len(rows) == count2  # No duplicates

    def test_no_snp_findings_for_empty_sample(
        self,
        panel: SleepPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Empty sample produces pathway summaries but no SNP findings."""
        result = score_sleep_pathways(panel, sample_engine, reference_engine)
        count = store_sleep_findings(result, sample_engine)

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
        assert count == 3  # 3 pathway summaries, all Standard (#615)

    def test_14_trait_findings_max(
        self,
        panel: SleepPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """With all SNPs genotyped at non-Standard, verify finding count ≤ 14."""
        _seed_variants(
            sample_engine,
            [
                ("rs762551", "15", 75041917, "CC"),  # CYP1A2 slow → Elevated
                ("rs5751876", "22", 24825044, "TT"),  # ADORA2A → Moderate (capped)
                ("rs2300478", "2", 66662600, "GG"),  # MEIS1 → Elevated
                ("rs9357271", "6", 38165204, "TT"),  # BTBD9 → Moderate (capped)
                ("rs2858884", "6", 32632760, "TT"),  # HLA-DQ marker → Standard (informational)
            ],
        )

        result = score_sleep_pathways(panel, sample_engine, reference_engine)
        count = store_sleep_findings(result, sample_engine)

        # 3 pathway summaries + up to 5 SNP findings + 1 metabolizer
        # + 1 cross-module ≤ 14
        assert count <= 14
        assert count >= 3  # At minimum, 3 pathway summaries (#615)

    def test_findings_include_pmids(
        self,
        panel: SleepPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Findings include PubMed citations."""
        _seed_variants(sample_engine, [("rs762551", "15", 75041917, "CC")])

        result = score_sleep_pathways(panel, sample_engine, reference_engine)
        store_sleep_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.rsid == "rs762551",
                    )
                )
            ).first()

        assert row is not None
        pmids = json.loads(row.pmid_citations)
        assert "16522833" in pmids


# ── PathwayResult properties ────────────────────────────────────────────


class TestPathwayResultProperties:
    def test_called_and_missing_snps(self) -> None:
        pr = PathwayResult(
            pathway_id="test",
            pathway_name="Test",
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
                {"rsid": "rs762551", "chrom": "15", "pos": 75041917, "genotype": "CC"},
            ],
            annotated=[
                {
                    "rsid": "rs762551",
                    "chrom": "15",
                    "pos": 75041917,
                    "genotype": "CC",
                    "annotation_coverage": 0b001111,
                },
            ],
        )

        result = SleepResult(
            pathway_results=[],
            gwas_matched_rsids=["rs762551"],
        )

        updated = update_annotation_coverage_gwas(result, sample)
        assert updated == 1

        with sample.connect() as conn:
            val = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs762551"
                )
            ).scalar()

        assert val == 0b101111  # 47

    def test_null_annotation_coverage_gets_gwas_bit(self) -> None:
        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs762551", "chrom": "15", "pos": 75041917, "genotype": "CC"},
            ],
            annotated=[
                {
                    "rsid": "rs762551",
                    "chrom": "15",
                    "pos": 75041917,
                    "genotype": "CC",
                    "annotation_coverage": None,
                },
            ],
        )

        result = SleepResult(
            pathway_results=[],
            gwas_matched_rsids=["rs762551"],
        )

        updated = update_annotation_coverage_gwas(result, sample)
        assert updated == 1

        with sample.connect() as conn:
            val = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs762551"
                )
            ).scalar()

        assert val == GWAS_BIT

    def test_empty_gwas_matched_returns_zero(self) -> None:
        sample = self._make_sample_with_annotated(raw=[], annotated=[])
        result = SleepResult(pathway_results=[], gwas_matched_rsids=[])
        updated = update_annotation_coverage_gwas(result, sample)
        assert updated == 0

    def test_idempotent_double_application(self) -> None:
        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs762551", "chrom": "15", "pos": 75041917, "genotype": "CC"},
            ],
            annotated=[
                {
                    "rsid": "rs762551",
                    "chrom": "15",
                    "pos": 75041917,
                    "genotype": "CC",
                    "annotation_coverage": GWAS_BIT,
                },
            ],
        )

        result = SleepResult(
            pathway_results=[],
            gwas_matched_rsids=["rs762551"],
        )

        update_annotation_coverage_gwas(result, sample)

        with sample.connect() as conn:
            val = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs762551"
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
