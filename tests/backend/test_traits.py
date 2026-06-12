"""Tests for the Traits & Personality module (P3-63).

Covers:
  - Panel loading and dataclass construction
  - DRD4 rs747302 proxy with coverage caveat
  - Genotype normalization
  - SNP scoring with evidence-level gating
  - ★★☆☆ hard cap on all findings
  - Pathway level determination (highest category)
  - Cross-module link findings (DRD4 → Gene Health ADHD)
  - PRS integration (weight set loading, result storage)
  - Full scoring integration with sample DB
  - Findings storage and retrieval
  - GWAS annotation_coverage bitmask (bit 5)
  - Module disclaimer presence
  - Research Use Only enforcement
  - Associative language enforcement
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.evidence import TRAITS_EVIDENCE_CAP
from backend.analysis.prs import run_prs
from backend.analysis.traits import (
    ELEVATED,
    MODERATE,
    MODULE_NAME,
    STANDARD,
    PanelSNP,
    PathwayResult,
    SNPResult,
    TraitsPanel,
    TraitsResult,
    _determine_pathway_level,
    _generate_cross_module_findings,
    _load_prs_weight_sets,
    _normalize_genotype,
    _score_snp,
    load_traits_panel,
    score_traits_pathways,
    store_traits_findings,
    update_annotation_coverage_gwas,
)
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
    / "traits_panel.json"
)


@pytest.fixture()
def panel() -> TraitsPanel:
    """Load the actual curated panel."""
    return load_traits_panel(PANEL_PATH)


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
    def test_load_panel_succeeds(self, panel: TraitsPanel) -> None:
        assert panel.module == "traits"
        assert panel.version == "1.0.0"

    def test_panel_has_three_pathways(self, panel: TraitsPanel) -> None:
        assert len(panel.pathways) == 3
        pathway_ids = {p.id for p in panel.pathways}
        assert pathway_ids == {"cognitive_ability", "personality_big_five", "behavioral_traits"}

    def test_panel_all_rsids(self, panel: TraitsPanel) -> None:
        rsids = panel.all_rsids()
        assert len(rsids) == 7
        expected = {
            "rs1396862",
            "rs2164273",
            "rs2572431",
            "rs9611519",
            "rs2389621",
            "rs993137",
            "rs747302",
        }
        assert set(rsids) == expected

    def test_panel_has_prs_weight_sets(self, panel: TraitsPanel) -> None:
        assert len(panel.prs_weight_sets) == 2

    def test_panel_evidence_cap(self, panel: TraitsPanel) -> None:
        assert panel.evidence_cap == 2

    def test_panel_module_disclaimer(self, panel: TraitsPanel) -> None:
        assert len(panel.module_disclaimer) > 50
        assert "research" in panel.module_disclaimer.lower()

    def test_cognitive_pathway_prs_primary(self, panel: TraitsPanel) -> None:
        cog = next(p for p in panel.pathways if p.id == "cognitive_ability")
        assert cog.prs_primary is True
        assert len(cog.snps) == 0

    def test_panel_has_cross_module_links(self, panel: TraitsPanel) -> None:
        assert len(panel.cross_module_links) >= 1
        adhd_link = next(lk for lk in panel.cross_module_links if lk["link_type"] == "ADHD")
        assert adhd_link["to_module"] == "gene_health"

    def test_panel_snps_have_genotype_effects(self, panel: TraitsPanel) -> None:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                assert len(snp.genotype_effects) > 0, f"{snp.rsid} has no genotype effects"
                for gt, effect in snp.genotype_effects.items():
                    assert "category" in effect
                    assert "effect_summary" in effect
                    assert effect["category"] in (ELEVATED, MODERATE, STANDARD)

    def test_load_nonexistent_panel_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_traits_panel(Path("/nonexistent/panel.json"))

    def test_panel_snps_have_trait_domain(self, panel: TraitsPanel) -> None:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                assert snp.trait_domain is not None, f"{snp.rsid} missing trait_domain"

    def test_panel_snps_have_associative_language(self, panel: TraitsPanel) -> None:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                assert snp.associative_language is True, (
                    f"{snp.rsid} missing associative_language=true"
                )


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


# ── DRD4 proxy tests ──────────────────────────────────────────────────


class TestDRD4Proxy:
    def _get_drd4(self, panel: TraitsPanel) -> PanelSNP:
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs747302":
                    return snp
        pytest.fail("DRD4 not found")

    def test_drd4_has_coverage_note(self, panel: TraitsPanel) -> None:
        drd4 = self._get_drd4(panel)
        assert drd4.coverage_note is not None
        assert "proxy" in drd4.coverage_note.lower()
        assert "vntr" in drd4.coverage_note.lower()

    def test_drd4_evidence_level_1(self, panel: TraitsPanel) -> None:
        drd4 = self._get_drd4(panel)
        assert drd4.evidence_level == 1

    def test_drd4_capped_at_moderate(self, panel: TraitsPanel) -> None:
        """★☆ evidence means no Elevated category allowed."""
        drd4 = self._get_drd4(panel)
        result = _score_snp(drd4, "GG")
        assert result.category != ELEVATED
        assert result.category == MODERATE

    def test_drd4_scores_published_c_g_genotypes(self, panel: TraitsPanel) -> None:
        drd4 = self._get_drd4(panel)
        assert drd4.ref_allele == "C"
        assert drd4.risk_allele == "G"
        assert _score_snp(drd4, "CG").category == MODERATE
        assert _score_snp(drd4, "GC").category == MODERATE
        assert _score_snp(drd4, "GG").category == MODERATE

    def test_drd4_has_cross_module(self, panel: TraitsPanel) -> None:
        drd4 = self._get_drd4(panel)
        assert drd4.cross_module is not None
        assert drd4.cross_module["module"] == "gene_health"

    def test_drd4_t_containing_genotype_is_not_curated(self, panel: TraitsPanel) -> None:
        drd4 = self._get_drd4(panel)
        result = _score_snp(drd4, "CT")
        assert result.category == STANDARD
        assert result.present_in_sample is True
        assert "not in curated panel definitions" in result.effect_summary

    def test_drd4_finding_preserves_coverage_note(self, panel: TraitsPanel) -> None:
        drd4 = self._get_drd4(panel)
        result = _score_snp(drd4, "CG")
        assert result.coverage_note is not None
        assert "proxy" in result.coverage_note.lower()


# ── SNP scoring tests ────────────────────────────────────────────────────


class TestSNPScoring:
    def test_not_genotyped_returns_standard(self, panel: TraitsPanel) -> None:
        snp = panel.pathways[1].snps[0]  # First personality SNP
        result = _score_snp(snp, None)
        assert result.category == STANDARD
        assert result.present_in_sample is False

    def test_evidence_gating_caps_at_moderate(self) -> None:
        """★☆ evidence hard-caps at Moderate."""
        snp = _make_test_snp(evidence_level=1, genotype_category=ELEVATED)
        result = _score_snp(snp, "AA")
        assert result.category == MODERATE

    def test_evidence_level_2_allows_elevated(self) -> None:
        """★★ evidence allows Elevated when genotype warrants it."""
        snp = _make_test_snp(evidence_level=2, genotype_category=ELEVATED)
        result = _score_snp(snp, "AA")
        assert result.category == ELEVATED

    def test_reversed_genotype_lookup(self, panel: TraitsPanel) -> None:
        """Panel handles reversed genotype strings."""
        snp = panel.pathways[1].snps[0]  # CRHR1 neuroticism
        result_ct = _score_snp(snp, "CT")
        result_tc = _score_snp(snp, "TC")
        assert result_ct.category == result_tc.category

    def test_unknown_genotype_defaults_standard(self, panel: TraitsPanel) -> None:
        snp = panel.pathways[1].snps[0]
        result = _score_snp(snp, "ZZ")
        assert result.category == STANDARD
        assert result.present_in_sample is True

    def test_neuroticism_tt_moderate(self, panel: TraitsPanel) -> None:
        """CRHR1 neuroticism TT → Moderate (★★ evidence)."""
        crhr1 = next(s for pw in panel.pathways for s in pw.snps if s.rsid == "rs1396862")
        result = _score_snp(crhr1, "TT")
        assert result.category == MODERATE
        assert result.trait_domain == "neuroticism"

    def test_risk_tolerance_cc_moderate(self, panel: TraitsPanel) -> None:
        """CADM2 risk tolerance CC → Moderate (★★ evidence)."""
        cadm2 = next(s for pw in panel.pathways for s in pw.snps if s.rsid == "rs993137")
        result = _score_snp(cadm2, "CC")
        assert result.category == MODERATE
        assert result.trait_domain == "risk_tolerance"

    def test_openness_star1_capped(self, panel: TraitsPanel) -> None:
        """CTNNA2 openness is ★☆ — should be capped at Moderate max."""
        ctnna2 = next(s for pw in panel.pathways for s in pw.snps if s.rsid == "rs2572431")
        assert ctnna2.evidence_level == 1
        # CC is Moderate in panel, but even if it was Elevated, it'd be capped
        result = _score_snp(ctnna2, "CC")
        assert result.category in (MODERATE, STANDARD)


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


# ── PRS integration tests ────────────────────────────────────────────────


class TestPRSIntegration:
    def test_load_prs_weight_sets(self, panel: TraitsPanel) -> None:
        weight_sets = _load_prs_weight_sets(panel)
        assert len(weight_sets) == 2

        traits = {ws.trait for ws in weight_sets}
        assert "educational_attainment" in traits
        assert "cognitive_ability" in traits

    def test_prs_weight_sets_module_is_traits(self, panel: TraitsPanel) -> None:
        weight_sets = _load_prs_weight_sets(panel)
        for ws in weight_sets:
            assert ws.module == "traits"

    def test_prs_weight_sets_have_snps(self, panel: TraitsPanel) -> None:
        weight_sets = _load_prs_weight_sets(panel)
        for ws in weight_sets:
            assert ws.snp_count >= 10

    def test_prs_weight_sets_ancestry(self, panel: TraitsPanel) -> None:
        weight_sets = _load_prs_weight_sets(panel)
        for ws in weight_sets:
            assert ws.source_ancestry == "EUR"

    def test_bundled_prs_weight_sets_are_uncalibrated(self, panel: TraitsPanel) -> None:
        weight_sets = _load_prs_weight_sets(panel)
        assert weight_sets
        for ws in weight_sets:
            assert ws.calibrated is False

    def test_uncalibrated_traits_prs_withholds_percentile(
        self, panel: TraitsPanel, sample_engine: sa.Engine
    ) -> None:
        ws = _load_prs_weight_sets(panel)[0]
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": weight.rsid,
                        "chrom": "1",
                        "pos": idx,
                        "genotype": weight.effect_allele * 2,
                    }
                    for idx, weight in enumerate(ws.weights, start=1)
                ],
            )

        result = run_prs(ws, sample_engine, n_bootstrap=25, rng_seed=1)

        assert result.is_sufficient is True
        assert result.calibrated is False
        assert result.raw_score > 0
        assert result.percentile is None
        assert result.z_score is None
        assert result.has_bootstrap_ci is False


# ── Cross-module finding tests ───────────────────────────────────────────


class TestCrossModuleFindings:
    def test_drd4_generates_cross_module_finding(self, panel: TraitsPanel) -> None:
        """DRD4 non-Standard generates cross-module finding for Gene Health."""
        behavioral_pr = PathwayResult(
            pathway_id="behavioral_traits",
            pathway_name="Behavioral Traits",
            pathway_description="",
            level=MODERATE,
            snp_results=[
                SNPResult(
                    rsid="rs747302",
                    gene="DRD4",
                    variant_name="DRD4 exon III VNTR proxy",
                    genotype="CG",
                    category=MODERATE,
                    effect_summary="One copy of proxy allele.",
                    evidence_level=1,
                    pmids=["8776587"],
                    recommendation_text="",
                    present_in_sample=True,
                    trait_domain="novelty_seeking",
                    coverage_note="proxy caveat",
                    cross_module={"module": "gene_health", "note": "ADHD"},
                ),
            ],
        )
        results = [behavioral_pr]
        cross = _generate_cross_module_findings(results, panel)
        assert len(cross) == 1
        assert cross[0].to_module == "gene_health"
        assert cross[0].link_type == "ADHD"
        assert "Gene Health" in cross[0].finding_text

    def test_no_cross_module_for_standard(self, panel: TraitsPanel) -> None:
        """Standard DRD4 does not generate cross-module finding."""
        behavioral_pr = PathwayResult(
            pathway_id="behavioral_traits",
            pathway_name="Behavioral Traits",
            pathway_description="",
            level=STANDARD,
            snp_results=[
                SNPResult(
                    rsid="rs747302",
                    gene="DRD4",
                    variant_name="DRD4 exon III VNTR proxy",
                    genotype="CC",
                    category=STANDARD,
                    effect_summary="No proxy signal.",
                    evidence_level=1,
                    pmids=[],
                    recommendation_text="",
                    present_in_sample=True,
                    trait_domain="novelty_seeking",
                    cross_module={"module": "gene_health", "note": "ADHD"},
                ),
            ],
        )
        results = [behavioral_pr]
        cross = _generate_cross_module_findings(results, panel)
        assert len(cross) == 0

    def test_no_cross_module_when_not_genotyped(self, panel: TraitsPanel) -> None:
        behavioral_pr = PathwayResult(
            pathway_id="behavioral_traits",
            pathway_name="Behavioral Traits",
            pathway_description="",
            level=STANDARD,
        )
        results = [behavioral_pr]
        cross = _generate_cross_module_findings(results, panel)
        assert len(cross) == 0


# ── Integration tests ────────────────────────────────────────────────────


class TestScorePathways:
    def test_full_scoring_all_snps(
        self,
        panel: TraitsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Score pathways with all 7 individual SNPs genotyped."""
        _seed_variants(
            sample_engine,
            [
                ("rs1396862", "17", 43895396, "TT"),  # CRHR1 neuroticism hom risk
                ("rs2164273", "12", 108620485, "AA"),  # WSCD2 extraversion hom risk
                ("rs2572431", "2", 80626556, "CC"),  # CTNNA2 openness hom
                ("rs9611519", "5", 87854363, "CC"),  # agreeableness hom
                ("rs2389621", "18", 44744620, "TT"),  # KATNAL2 conscientiousness hom
                ("rs993137", "3", 85011544, "CC"),  # CADM2 risk tolerance hom risk
                ("rs747302", "11", 637371, "GG"),  # DRD4 VNTR proxy hom
            ],
        )
        _seed_gwas(
            reference_engine,
            [
                ("rs1396862", "Neuroticism"),
                ("rs993137", "Risk tolerance"),
            ],
        )

        result = score_traits_pathways(panel, sample_engine, reference_engine)

        # Personality: neuroticism TT=Moderate (★★), extraversion AA=Moderate,
        #   openness CC=Moderate (star1→capped), etc. → Moderate
        personality = next(
            pr for pr in result.pathway_results if pr.pathway_id == "personality_big_five"
        )
        assert personality.level == MODERATE

        # Behavioral: risk tolerance CC=Moderate, DRD4 GG=Moderate (star1→capped) → Moderate
        behavioral = next(
            pr for pr in result.pathway_results if pr.pathway_id == "behavioral_traits"
        )
        assert behavioral.level == MODERATE

        # GWAS matches
        assert "rs1396862" in result.gwas_matched_rsids
        assert "rs993137" in result.gwas_matched_rsids

        # Cross-module findings should exist (DRD4 GG → Gene Health ADHD)
        assert len(result.cross_module_findings) >= 1

        # Module disclaimer preserved
        assert len(result.module_disclaimer) > 0

    def test_missing_snps_default_standard(
        self,
        panel: TraitsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Pathways with no genotyped SNPs default to Standard."""
        result = score_traits_pathways(panel, sample_engine, reference_engine)
        for pr in result.pathway_results:
            if not pr.prs_primary:
                assert pr.level == STANDARD
                assert len(pr.called_snps) == 0

    def test_drd4_coverage_note_preserved_in_scoring(
        self,
        panel: TraitsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """DRD4 coverage note survives through scoring pipeline."""
        _seed_variants(sample_engine, [("rs747302", "11", 637371, "CG")])
        result = score_traits_pathways(panel, sample_engine, reference_engine)

        behavioral = next(
            pr for pr in result.pathway_results if pr.pathway_id == "behavioral_traits"
        )
        drd4 = next(s for s in behavioral.called_snps if s.rsid == "rs747302")
        assert drd4.coverage_note is not None
        assert "proxy" in drd4.coverage_note.lower()


# ── Findings storage tests ─────────────────────────────────────────────


class TestStoreFindingsIntegration:
    def test_store_and_retrieve_findings(
        self,
        panel: TraitsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Store findings and verify they're in the DB."""
        _seed_variants(
            sample_engine,
            [
                ("rs1396862", "17", 43895396, "TT"),
                ("rs993137", "3", 85011544, "CC"),
                ("rs747302", "11", 637371, "CG"),
            ],
        )

        result = score_traits_pathways(panel, sample_engine, reference_engine)
        count = store_traits_findings(result, sample_engine)
        assert count > 0

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == MODULE_NAME)
            ).fetchall()

        assert len(rows) == count

    def test_pathway_summaries_created(
        self,
        panel: TraitsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Pathway summaries created for personality + behavioral pathways."""
        result = score_traits_pathways(panel, sample_engine, reference_engine)
        store_traits_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            pathway_rows = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.category == "pathway_summary",
                    )
                )
            ).fetchall()

        # personality_big_five + behavioral_traits = 2 summaries
        # (cognitive_ability is PRS-primary with no SNPs, so skipped)
        assert len(pathway_rows) == 2

    def test_drd4_finding_includes_coverage_note(
        self,
        panel: TraitsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """DRD4 SNP finding includes coverage_note in detail_json."""
        _seed_variants(sample_engine, [("rs747302", "11", 637371, "CG")])

        result = score_traits_pathways(panel, sample_engine, reference_engine)
        store_traits_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.rsid == "rs747302",
                        findings.c.category == "snp_finding",
                    )
                )
            ).first()

        assert row is not None
        detail = json.loads(row.detail_json)
        assert "coverage_note" in detail
        assert "proxy" in detail["coverage_note"].lower()

    def test_cross_module_findings_stored(
        self,
        panel: TraitsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Cross-module findings are stored with category='cross_module'."""
        _seed_variants(sample_engine, [("rs747302", "11", 637371, "CG")])

        result = score_traits_pathways(panel, sample_engine, reference_engine)
        store_traits_findings(result, sample_engine)

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
            assert "to_module" in detail
            assert detail["to_module"] == "gene_health"

    def test_store_clears_previous_findings(
        self,
        panel: TraitsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Re-running store clears previous traits findings."""
        _seed_variants(sample_engine, [("rs1396862", "17", 43895396, "TT")])

        result = score_traits_pathways(panel, sample_engine, reference_engine)
        store_traits_findings(result, sample_engine)
        count2 = store_traits_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == MODULE_NAME)
            ).fetchall()

        assert len(rows) == count2  # No duplicates

    def test_evidence_cap_enforced_in_storage(
        self,
        panel: TraitsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """All stored findings respect ★★☆☆ evidence cap."""
        _seed_variants(
            sample_engine,
            [
                ("rs1396862", "17", 43895396, "TT"),
                ("rs993137", "3", 85011544, "CC"),
            ],
        )

        result = score_traits_pathways(panel, sample_engine, reference_engine)
        store_traits_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == MODULE_NAME)
            ).fetchall()

        for row in rows:
            assert row.evidence_level <= TRAITS_EVIDENCE_CAP, (
                f"Finding '{row.finding_text}' has evidence_level "
                f"{row.evidence_level} > cap {TRAITS_EVIDENCE_CAP}"
            )

    def test_no_snp_findings_for_empty_sample(
        self,
        panel: TraitsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Empty sample produces pathway summaries but no SNP findings."""
        result = score_traits_pathways(panel, sample_engine, reference_engine)
        store_traits_findings(result, sample_engine)

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

    def test_findings_include_research_use_only(
        self,
        panel: TraitsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """SNP findings include research_use_only flag in detail_json."""
        _seed_variants(sample_engine, [("rs993137", "3", 85011544, "CC")])

        result = score_traits_pathways(panel, sample_engine, reference_engine)
        store_traits_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.rsid == "rs993137",
                        findings.c.category == "snp_finding",
                    )
                )
            ).first()

        assert row is not None
        detail = json.loads(row.detail_json)
        assert detail.get("research_use_only") is True
        assert detail.get("associative_language") is True

    def test_findings_include_pmids(
        self,
        panel: TraitsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Findings include PubMed citations."""
        _seed_variants(sample_engine, [("rs1396862", "17", 43895396, "TT")])

        result = score_traits_pathways(panel, sample_engine, reference_engine)
        store_traits_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.rsid == "rs1396862",
                    )
                )
            ).first()

        assert row is not None
        pmids = json.loads(row.pmid_citations)
        assert "29942085" in pmids

    def test_uncalibrated_prs_findings_store_no_percentile(
        self,
        panel: TraitsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        ws = _load_prs_weight_sets(panel)[0]
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": weight.rsid,
                        "chrom": "1",
                        "pos": idx,
                        "genotype": weight.effect_allele * 2,
                    }
                    for idx, weight in enumerate(ws.weights, start=1)
                ],
            )

        result = score_traits_pathways(panel, sample_engine, reference_engine)
        store_traits_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.category == "prs",
                        findings.c.prs_score.is_not(None),
                    )
                )
            ).first()

        assert row is not None
        assert row.prs_percentile is None
        assert "uncalibrated" in row.finding_text.lower()
        detail = json.loads(row.detail_json)
        assert detail["calibrated"] is False
        assert detail["percentile"] is None
        assert detail["z_score"] is None


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
                {"rsid": "rs1396862", "chrom": "17", "pos": 43895396, "genotype": "TT"},
            ],
            annotated=[
                {
                    "rsid": "rs1396862",
                    "chrom": "17",
                    "pos": 43895396,
                    "genotype": "TT",
                    "annotation_coverage": 0b001111,
                },
            ],
        )

        result = TraitsResult(
            pathway_results=[],
            gwas_matched_rsids=["rs1396862"],
        )

        updated = update_annotation_coverage_gwas(result, sample)
        assert updated == 1

        with sample.connect() as conn:
            val = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs1396862"
                )
            ).scalar()

        assert val == 0b101111  # 47

    def test_empty_gwas_matched_returns_zero(self) -> None:
        sample = self._make_sample_with_annotated(raw=[], annotated=[])
        result = TraitsResult(pathway_results=[], gwas_matched_rsids=[])
        updated = update_annotation_coverage_gwas(result, sample)
        assert updated == 0


# ── Module-level evidence cap tests ──────────────────────────────────────


class TestEvidenceCapEnforcement:
    def test_all_panel_snps_at_or_below_cap(self, panel: TraitsPanel) -> None:
        """All panel SNPs have evidence_level ≤ 2."""
        for pathway in panel.pathways:
            for snp in pathway.snps:
                assert snp.evidence_level <= panel.evidence_cap, (
                    f"{snp.rsid} evidence {snp.evidence_level} exceeds cap {panel.evidence_cap}"
                )

    def test_module_name_is_traits(self) -> None:
        assert MODULE_NAME == "traits"

    def test_evidence_cap_constant(self) -> None:
        assert TRAITS_EVIDENCE_CAP == 2


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
        trait_domain="test",
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
