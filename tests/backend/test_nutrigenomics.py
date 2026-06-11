"""Tests for the curated nutrigenomics SNP panel (P3-08).

Covers:
  - Panel JSON loading and validation
  - Genotype scoring with evidence-level gating
  - Pathway-level determination (highest category)
  - MTHFR C677T TT → Elevated folate metabolism (T3-06)
  - LCT rs4988235 CC → lactose intolerance (T3-07)
  - ★☆ evidence hard-cap at Moderate
  - Findings storage to sample DB
  - GWAS lookup integration for annotation_coverage
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.nutrigenomics import (
    ELEVATED,
    MODERATE,
    STANDARD,
    NutrigenomicsPanel,
    NutrigenomicsResult,
    PanelSNP,
    PathwayResult,
    SNPResult,
    _determine_pathway_level,
    _normalize_genotype,
    _score_snp,
    load_nutrigenomics_panel,
    score_nutrigenomics_pathways,
    store_nutrigenomics_findings,
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
    / "nutrigenomics_panel.json"
)


@pytest.fixture()
def panel() -> NutrigenomicsPanel:
    """Load the actual curated panel."""
    return load_nutrigenomics_panel(PANEL_PATH)


@pytest.fixture()
def sample_engine(tmp_path: Path) -> sa.Engine:
    """Create an in-memory sample DB with raw_variants and findings tables."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'sample.db'}")
    sample_metadata_obj.create_all(engine)
    return engine


@pytest.fixture()
def reference_engine(tmp_path: Path) -> sa.Engine:
    """Create an in-memory reference DB with gwas_associations table."""
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
    def test_load_panel_succeeds(self, panel: NutrigenomicsPanel) -> None:
        assert panel.module == "nutrigenomics"
        assert panel.version == "1.0.0"

    def test_panel_has_six_pathways(self, panel: NutrigenomicsPanel) -> None:
        assert len(panel.pathways) == 6
        pathway_ids = {p.id for p in panel.pathways}
        assert pathway_ids == {
            "folate_metabolism",
            "vitamin_d",
            "vitamin_b12",
            "omega_3",
            "iron",
            "lactose",
        }

    def test_panel_all_rsids(self, panel: NutrigenomicsPanel) -> None:
        rsids = panel.all_rsids()
        assert len(rsids) > 0
        # Key SNPs must be present
        assert "rs1801133" in rsids  # MTHFR C677T
        assert "rs4988235" in rsids  # LCT
        assert "rs2282679" in rsids  # GC/VDR
        assert "rs174547" in rsids  # FADS1
        assert "rs1800562" in rsids  # HFE C282Y

    def test_panel_snps_have_genotype_effects(self, panel: NutrigenomicsPanel) -> None:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                assert len(snp.genotype_effects) > 0, f"{snp.rsid} has no genotype effects"
                for gt, effect in snp.genotype_effects.items():
                    assert "category" in effect
                    assert "effect_summary" in effect
                    assert effect["category"] in (ELEVATED, MODERATE, STANDARD)

    def test_panel_snps_have_required_fields(self, panel: NutrigenomicsPanel) -> None:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                assert snp.rsid.startswith("rs")
                assert snp.gene
                assert snp.evidence_level in (1, 2, 3, 4)
                assert isinstance(snp.pmids, list)

    def test_load_nonexistent_panel_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_nutrigenomics_panel(Path("/nonexistent/panel.json"))

    def test_panel_json_is_valid(self) -> None:
        """Validate the raw JSON structure."""
        with open(PANEL_PATH) as f:
            data = json.load(f)
        assert data["module"] == "nutrigenomics"
        assert "pathways" in data
        assert "scoring_rules" in data
        assert data["scoring_rules"]["star_1_cap"] == "Moderate"


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


# ── SNP scoring tests ────────────────────────────────────────────────────


class TestSNPScoring:
    def test_mthfr_c677t_tt_elevated(self, panel: NutrigenomicsPanel) -> None:
        """T3-06: MTHFR C677T TT → Elevated folate metabolism finding."""
        mthfr_snp = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs1801133":
                    mthfr_snp = snp
                    break

        assert mthfr_snp is not None
        result = _score_snp(mthfr_snp, "AA")
        assert result.category == ELEVATED
        assert result.present_in_sample is True
        lowered = result.effect_summary.lower()
        assert "reduced" in lowered or "significantly" in lowered
        assert "23824729" in result.pmids

    def test_mthfr_c677t_ct_moderate(self, panel: NutrigenomicsPanel) -> None:
        mthfr_snp = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs1801133":
                    mthfr_snp = snp
                    break

        result = _score_snp(mthfr_snp, "GA")
        assert result.category == MODERATE

    def test_mthfr_c677t_cc_standard(self, panel: NutrigenomicsPanel) -> None:
        mthfr_snp = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs1801133":
                    mthfr_snp = snp
                    break

        result = _score_snp(mthfr_snp, "GG")
        assert result.category == STANDARD

    def test_lct_cc_elevated(self, panel: NutrigenomicsPanel) -> None:
        """T3-07: LCT rs4988235 CC → lactose intolerance finding."""
        lct_snp = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs4988235":
                    lct_snp = snp
                    break

        assert lct_snp is not None
        # GG = non-persistent (risk)
        result = _score_snp(lct_snp, "GG")
        assert result.category == ELEVATED
        assert "lactase non-persistent" in result.effect_summary.lower()

    def test_lct_aa_standard(self, panel: NutrigenomicsPanel) -> None:
        lct_snp = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs4988235":
                    lct_snp = snp
                    break

        result = _score_snp(lct_snp, "AA")
        assert result.category == STANDARD

    def test_not_genotyped_returns_standard(self, panel: NutrigenomicsPanel) -> None:
        snp = panel.pathways[0].snps[0]
        result = _score_snp(snp, None)
        assert result.category == STANDARD
        assert result.present_in_sample is False

    def test_evidence_gating_caps_at_moderate(self) -> None:
        """★☆ evidence hard-caps pathway at Moderate (key rule)."""
        snp = _make_test_snp(evidence_level=1, genotype_category=ELEVATED)
        result = _score_snp(snp, "AA")
        assert result.category == MODERATE  # Capped from Elevated

    def test_evidence_level_2_allows_elevated(self) -> None:
        """★★ evidence allows Elevated when genotype warrants it."""
        snp = _make_test_snp(evidence_level=2, genotype_category=ELEVATED)
        result = _score_snp(snp, "AA")
        assert result.category == ELEVATED

    def test_reversed_genotype_lookup(self, panel: NutrigenomicsPanel) -> None:
        """Panel handles reversed genotype strings (e.g. CT vs TC)."""
        mthfr_snp = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs1801133":
                    mthfr_snp = snp
                    break

        # GA and AG should both map to Moderate
        result_ga = _score_snp(mthfr_snp, "GA")
        result_ag = _score_snp(mthfr_snp, "AG")
        assert result_ga.category == result_ag.category == MODERATE

    def test_fads1_rs174547_c_carrier_direction(self, panel: NutrigenomicsPanel) -> None:
        """FADS1 rs174547 C carriers are the lower-desaturase risk direction."""
        fads1_snp = next(snp for pw in panel.pathways for snp in pw.snps if snp.rsid == "rs174547")

        tt = _score_snp(fads1_snp, "TT")
        tc = _score_snp(fads1_snp, "TC")
        ct = _score_snp(fads1_snp, "CT")
        cc = _score_snp(fads1_snp, "CC")

        assert fads1_snp.risk_allele == "C"
        assert tt.category == STANDARD
        assert tc.category == ct.category == MODERATE
        assert cc.category == ELEVATED
        assert "lower estimated" in cc.effect_summary.lower()
        assert "33331250" in cc.pmids


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


# ── Integration tests ────────────────────────────────────────────────────


class TestScorePathways:
    def test_full_scoring_with_mthfr_ct(
        self,
        panel: NutrigenomicsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Score pathways with MTHFR C677T CT genotype (from v5 fixture)."""
        _seed_variants(
            sample_engine,
            [
                ("rs1801133", "1", 11856378, "CT"),
                ("rs1801131", "1", 11854476, "AC"),
            ],
        )
        _seed_gwas(
            reference_engine,
            [
                ("rs1801133", "Homocysteine levels"),
            ],
        )

        result = score_nutrigenomics_pathways(panel, sample_engine, reference_engine)

        # Folate pathway should be Moderate (CT = het)
        folate = next(pr for pr in result.pathway_results if pr.pathway_id == "folate_metabolism")
        assert folate.level == MODERATE

        # MTHFR C677T het should be Moderate
        mthfr_result = next(s for s in folate.snp_results if s.rsid == "rs1801133")
        assert mthfr_result.present_in_sample is True
        # CT maps to GA in ref/alt terms, but panel handles original genotype
        # The test fixture uses CT which doesn't directly match panel GA/AG
        # But the panel should have entries for the actual observed genotype

        # GWAS match for MTHFR
        assert "rs1801133" in result.gwas_matched_rsids

    def test_mthfr_c677t_ct_scored_moderate(
        self,
        panel: NutrigenomicsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """A real MTHFR C677T heterozygote ('CT') scores Moderate on its own.

        Locks the SNP's own category instead of the folate pathway max(),
        which is otherwise dominated by the co-seeded A1298C 'AC' call (the
        masking that let the original test pass without scoring C677T).

        23andMe reports rs1801133 as C/T, but the panel keys genotype_effects on
        the G/A (Watson–Crick complement) strand. ``_score_snp`` now harmonizes
        strand via the shared ``genotype_lookup.lookup_by_genotype`` (chip "CT" →
        panel "GA"), so a real CT heterozygote resolves to Moderate not STANDARD.
        """
        _seed_variants(sample_engine, [("rs1801133", "1", 11856378, "CT")])

        result = score_nutrigenomics_pathways(panel, sample_engine, reference_engine)
        folate = next(pr for pr in result.pathway_results if pr.pathway_id == "folate_metabolism")
        mthfr = next(s for s in folate.snp_results if s.rsid == "rs1801133")
        assert mthfr.present_in_sample is True
        assert mthfr.category == MODERATE

    def test_full_scoring_with_mthfr_homozygous_risk(
        self,
        panel: NutrigenomicsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """MTHFR C677T TT (AA in ref/alt) → Elevated folate pathway."""
        _seed_variants(
            sample_engine,
            [
                ("rs1801133", "1", 11856378, "AA"),
            ],
        )

        result = score_nutrigenomics_pathways(panel, sample_engine, reference_engine)
        folate = next(pr for pr in result.pathway_results if pr.pathway_id == "folate_metabolism")
        assert folate.level == ELEVATED

    def test_missing_snps_reported(
        self,
        panel: NutrigenomicsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Pathways with no genotyped SNPs default to Standard."""
        # Don't seed any variants
        result = score_nutrigenomics_pathways(panel, sample_engine, reference_engine)

        for pr in result.pathway_results:
            assert pr.level == STANDARD
            assert len(pr.called_snps) == 0
            assert len(pr.missing_snps) > 0

    def test_lactose_non_persistent(
        self,
        panel: NutrigenomicsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """LCT GG → Elevated lactose pathway."""
        _seed_variants(
            sample_engine,
            [
                ("rs4988235", "2", 135851076, "GG"),
            ],
        )
        _seed_gwas(
            reference_engine,
            [
                ("rs4988235", "Lactase persistence"),
            ],
        )

        result = score_nutrigenomics_pathways(panel, sample_engine, reference_engine)
        lactose = next(pr for pr in result.pathway_results if pr.pathway_id == "lactose")
        assert lactose.level == ELEVATED
        assert "rs4988235" in result.gwas_matched_rsids

    def test_fads1_rs174547_cc_drives_omega3_elevated(
        self,
        panel: NutrigenomicsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Runtime scoring must not treat rs174547 CC as normal/standard."""
        _seed_variants(sample_engine, [("rs174547", "11", 61597212, "CC")])

        result = score_nutrigenomics_pathways(panel, sample_engine, reference_engine)
        omega3 = next(pr for pr in result.pathway_results if pr.pathway_id == "omega_3")
        fads1 = next(s for s in omega3.snp_results if s.rsid == "rs174547")

        assert omega3.level == ELEVATED
        assert fads1.present_in_sample is True
        assert fads1.category == ELEVATED

    def test_fads1_rs174547_tt_keeps_omega3_standard(
        self,
        panel: NutrigenomicsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Runtime scoring must not elevate rs174547 non-carriers."""
        _seed_variants(sample_engine, [("rs174547", "11", 61597212, "TT")])

        result = score_nutrigenomics_pathways(panel, sample_engine, reference_engine)
        omega3 = next(pr for pr in result.pathway_results if pr.pathway_id == "omega_3")
        fads1 = next(s for s in omega3.snp_results if s.rsid == "rs174547")

        assert omega3.level == STANDARD
        assert fads1.present_in_sample is True
        assert fads1.category == STANDARD


class TestStoreFindingsIntegration:
    def test_store_and_retrieve_findings(
        self,
        panel: NutrigenomicsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Store findings and verify they're in the DB."""
        _seed_variants(
            sample_engine,
            [
                ("rs1801133", "1", 11856378, "AA"),
                ("rs4988235", "2", 135851076, "GG"),
            ],
        )

        result = score_nutrigenomics_pathways(panel, sample_engine, reference_engine)
        count = store_nutrigenomics_findings(result, sample_engine)
        assert count > 0

        # Verify findings in DB
        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == "nutrigenomics")
            ).fetchall()

        assert len(rows) == count

        # Check pathway summary findings exist
        pathway_summaries = [r for r in rows if r.category == "pathway_summary"]
        assert len(pathway_summaries) == 6  # One per pathway

        # Check SNP findings for MTHFR
        snp_findings = [r for r in rows if r.category == "snp_finding" and r.rsid == "rs1801133"]
        assert len(snp_findings) == 1
        assert snp_findings[0].pathway_level == ELEVATED

    def test_findings_include_pmids(
        self,
        panel: NutrigenomicsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Findings include PubMed citations."""
        _seed_variants(
            sample_engine,
            [
                ("rs1801133", "1", 11856378, "AA"),
            ],
        )

        result = score_nutrigenomics_pathways(panel, sample_engine, reference_engine)
        store_nutrigenomics_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == "nutrigenomics",
                        findings.c.rsid == "rs1801133",
                    )
                )
            ).first()

        assert row is not None
        pmids = json.loads(row.pmid_citations)
        assert "23824729" in pmids

    def test_store_clears_previous_findings(
        self,
        panel: NutrigenomicsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Re-running store clears previous nutrigenomics findings."""
        _seed_variants(
            sample_engine,
            [
                ("rs1801133", "1", 11856378, "AA"),
            ],
        )

        result = score_nutrigenomics_pathways(panel, sample_engine, reference_engine)
        store_nutrigenomics_findings(result, sample_engine)
        count1 = store_nutrigenomics_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == "nutrigenomics")
            ).fetchall()

        assert len(rows) == count1  # No duplicates

    def test_no_findings_for_empty_sample(
        self,
        panel: NutrigenomicsPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Empty sample produces pathway summaries but no SNP findings."""
        result = score_nutrigenomics_pathways(panel, sample_engine, reference_engine)
        count = store_nutrigenomics_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            snp_findings = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == "nutrigenomics",
                        findings.c.category == "snp_finding",
                    )
                )
            ).fetchall()

        assert len(snp_findings) == 0
        # But pathway summaries should exist
        assert count == 6  # 6 pathway summaries, all Standard


class TestPathwayResultProperties:
    def test_called_snps(self) -> None:
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


# ═══════════════════════════════════════════════════════════════════════
# update_annotation_coverage_gwas (P3-09a)
# ═══════════════════════════════════════════════════════════════════════


class TestUpdateAnnotationCoverageGwas:
    """Test that GWAS bitmask bit 5 (value 32) is ORed into annotation_coverage."""

    def _make_sample_with_annotated(
        self,
        raw: list[dict],
        annotated: list[dict],
    ) -> sa.Engine:
        """Create sample engine with raw_variants and pre-populated annotated_variants."""
        engine = sa.create_engine("sqlite://")
        sample_metadata_obj.create_all(engine)
        if raw:
            with engine.begin() as conn:
                conn.execute(raw_variants.insert(), raw)
        if annotated:
            with engine.begin() as conn:
                conn.execute(annotated_variants.insert(), annotated)
        return engine

    def test_sets_bit5_on_gwas_matched_variants(self):
        """Variants in gwas_matched_rsids get bit 5 (32) set."""
        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs1801133", "chrom": "1", "pos": 11856378, "genotype": "CT"},
                {"rsid": "rs9999999", "chrom": "1", "pos": 100, "genotype": "CC"},
            ],
            annotated=[
                {
                    "rsid": "rs1801133",
                    "chrom": "1",
                    "pos": 11856378,
                    "genotype": "CT",
                    "annotation_coverage": 0b001111,
                },  # bits 0-3 set
                {
                    "rsid": "rs9999999",
                    "chrom": "1",
                    "pos": 100,
                    "genotype": "CC",
                    "annotation_coverage": 0b000011,
                },  # bits 0-1 set
            ],
        )

        result = NutrigenomicsResult(
            pathway_results=[],
            gwas_matched_rsids=["rs1801133"],
        )

        updated = update_annotation_coverage_gwas(result, sample)
        assert updated == 1

        with sample.connect() as conn:
            rows = {
                r.rsid: r.annotation_coverage
                for r in conn.execute(
                    sa.select(
                        annotated_variants.c.rsid,
                        annotated_variants.c.annotation_coverage,
                    )
                ).fetchall()
            }

        # rs1801133: 0b001111 | 0b100000 = 0b101111 = 47
        assert rows["rs1801133"] == 0b101111
        # rs9999999: unchanged (not GWAS-matched)
        assert rows["rs9999999"] == 0b000011

    def test_null_annotation_coverage_gets_gwas_bit(self):
        """Variant with NULL annotation_coverage gets GWAS bit set to 32."""
        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs1801133", "chrom": "1", "pos": 11856378, "genotype": "CT"},
            ],
            annotated=[
                {
                    "rsid": "rs1801133",
                    "chrom": "1",
                    "pos": 11856378,
                    "genotype": "CT",
                    "annotation_coverage": None,
                },
            ],
        )

        result = NutrigenomicsResult(
            pathway_results=[],
            gwas_matched_rsids=["rs1801133"],
        )

        updated = update_annotation_coverage_gwas(result, sample)
        assert updated == 1

        with sample.connect() as conn:
            val = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs1801133"
                )
            ).scalar()

        assert val == GWAS_BIT  # 32

    def test_no_match_in_annotated_returns_zero(self):
        """GWAS-matched rsid not in annotated_variants → 0 updates."""
        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs1801133", "chrom": "1", "pos": 11856378, "genotype": "CT"},
            ],
            annotated=[],
        )

        result = NutrigenomicsResult(
            pathway_results=[],
            gwas_matched_rsids=["rs1801133"],
        )

        updated = update_annotation_coverage_gwas(result, sample)
        assert updated == 0

    def test_empty_gwas_matched_returns_zero(self):
        """Empty gwas_matched_rsids list → 0 updates."""
        sample = self._make_sample_with_annotated(raw=[], annotated=[])
        result = NutrigenomicsResult(pathway_results=[], gwas_matched_rsids=[])
        updated = update_annotation_coverage_gwas(result, sample)
        assert updated == 0

    def test_multiple_gwas_matched_rsids(self):
        """Multiple GWAS-matched rsids get bit 5 set."""
        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs1801133", "chrom": "1", "pos": 11856378, "genotype": "CT"},
                {"rsid": "rs4988235", "chrom": "2", "pos": 136608646, "genotype": "CC"},
            ],
            annotated=[
                {
                    "rsid": "rs1801133",
                    "chrom": "1",
                    "pos": 11856378,
                    "genotype": "CT",
                    "annotation_coverage": 0b000001,
                },
                {
                    "rsid": "rs4988235",
                    "chrom": "2",
                    "pos": 136608646,
                    "genotype": "CC",
                    "annotation_coverage": 0b000011,
                },
            ],
        )

        result = NutrigenomicsResult(
            pathway_results=[],
            gwas_matched_rsids=["rs1801133", "rs4988235"],
        )

        updated = update_annotation_coverage_gwas(result, sample)
        assert updated == 2

        with sample.connect() as conn:
            rows = {
                r.rsid: r.annotation_coverage
                for r in conn.execute(
                    sa.select(
                        annotated_variants.c.rsid,
                        annotated_variants.c.annotation_coverage,
                    )
                ).fetchall()
            }

        assert rows["rs1801133"] == 0b100001  # 33
        assert rows["rs4988235"] == 0b100011  # 35

    def test_idempotent_double_application(self):
        """Applying GWAS bit twice does not change the value."""
        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs1801133", "chrom": "1", "pos": 11856378, "genotype": "CT"},
            ],
            annotated=[
                {
                    "rsid": "rs1801133",
                    "chrom": "1",
                    "pos": 11856378,
                    "genotype": "CT",
                    "annotation_coverage": GWAS_BIT,
                },
            ],
        )

        result = NutrigenomicsResult(
            pathway_results=[],
            gwas_matched_rsids=["rs1801133"],
        )

        update_annotation_coverage_gwas(result, sample)

        with sample.connect() as conn:
            val = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs1801133"
                )
            ).scalar()

        assert val == GWAS_BIT  # Still 32, no extra bits
