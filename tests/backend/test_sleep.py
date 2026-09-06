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
import re
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.pathway_coverage import variant_label
from backend.analysis.sleep import (
    ELEVATED,
    INDETERMINATE,
    MODERATE,
    MODULE_NAME,
    NO_CALL,
    NOT_ON_ARRAY,
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
from tests.backend._gene_label_fixtures import (
    gene_prefixed_loci,
    raw_variant_rows,
    renders_gene_twice,
)

# ── Fixtures ──────────────────────────────────────────────────────────────

PANEL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "sleep_panel.json"
)
#: The panel's allele-neutral label for rs762551. It is deliberately NOT a star
#: allele: the same intron-1 site sits on several CYP1A2 haplotypes (*1F, *1J,
#: *1K under the historical nomenclature; PMID:12920202), and PharmVar has since
#: reassigned the former *1F-defining core variant to the *30 group
#: (PMID:41992662), so this single marker cannot resolve a star allele. The panel
#: coverage note says the module makes no such call, and ``variant_name`` reaches
#: patient-facing finding text, so a "*1F" fixture would make the suite normalise
#: exactly the inference the application disclaims.
CYP1A2_LOCUS_LABEL = "-163C>A (rs762551; caffeine clearance)"


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

    def test_panel_declares_no_cross_module_links(self, panel: SleepPanel) -> None:
        """Sleep's only link was CYP1A2 -> Pharmacogenomics, retired in #2024.

        ``additional_genes`` went with it: its sole entry, ``CYP1A2_pgx_context``,
        existed to drive that finding and its note asserted PGx coverage that
        does not exist. Discriminating on the panel still being loaded — the SNP
        it described is still scored, it just no longer points anywhere.
        """
        assert panel.additional_genes is None
        assert all(snp.cross_module is None for pathway in panel.pathways for snp in pathway.snps)
        assert any(snp.rsid == "rs762551" for pathway in panel.pathways for snp in pathway.snps)

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

    def test_the_fixture_label_is_the_panel_label(self, panel: SleepPanel) -> None:
        """Ties the fixtures to the panel so the label cannot drift apart.

        ``variant_name`` is reference data, not a test string: it is
        interpolated into patient-facing finding text. If the panel's label
        changes, these fixtures must follow it rather than keep asserting a
        stale one — and neither may become a star allele this single marker
        cannot resolve.
        """
        cyp = self._get_cyp1a2(panel)
        assert cyp.variant_name == CYP1A2_LOCUS_LABEL
        assert "*" not in cyp.variant_name

    def test_cyp1a2_has_no_cross_module(self, panel: SleepPanel) -> None:
        """CYP1A2 must NOT cross-link to Pharmacogenomics (#2024).

        That module covers no CYP1A2 and cpic_guidelines has no clozapine or
        theophylline row, so the card promised a "full drug interaction profile
        and star-allele interpretation" that does not exist. The caffeine
        interpretation this module is actually for is unaffected.
        """
        cyp = self._get_cyp1a2(panel)
        assert cyp.cross_module is None
        assert "caffeine half-life" in cyp.recommendation_text

    def test_cyp1a2_unmodeled_acgt_genotype_has_no_metabolizer_state(
        self, panel: SleepPanel
    ) -> None:
        """A real but unmodeled CYP1A2 genotype is withheld, not metabolizer-called."""
        cyp = self._get_cyp1a2(panel)
        result = _score_snp(cyp, "AT", panel)
        assert result.category == INDETERMINATE
        assert result.metabolizer_state is None
        assert result.present_in_sample is True


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
        for genotype in ("CC", "CA", "AC", "AA"):
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

    def test_non_nucleotide_unknown_genotype_defaults_standard(self, panel: SleepPanel) -> None:
        snp = panel.pathways[0].snps[0]
        result = _score_snp(snp, "ZZ", panel)
        assert result.category == STANDARD
        assert result.present_in_sample is True

    def test_unmodeled_acgt_genotype_withheld_as_indeterminate(self, panel: SleepPanel) -> None:
        """A real nucleotide genotype outside the model is not scored baseline."""
        snp = _make_test_snp()
        result = _score_snp(snp, "AC", panel)
        assert result.category == INDETERMINATE
        assert result.present_in_sample is True
        assert result.genotype == "AC"
        assert result.coverage_note is not None
        assert "outside this locus" in result.coverage_note
        assert "indeterminate" in result.effect_summary.lower()

    def test_palindromic_homozygote_withheld_as_indeterminate(self, panel: SleepPanel) -> None:
        """A future A/T sleep SNP must not emit a strand-flipped homozygote call."""
        snp = PanelSNP(
            rsid="rs_sleep_palindrome",
            gene="CLOCK",
            variant_name="synthetic palindromic SNP",
            hgvs_protein=None,
            risk_allele="A",
            ref_allele="T",
            genotype_effects={
                "AA": {"category": ELEVATED, "effect_summary": "A-strand homozygote"},
                "AT": {"category": MODERATE, "effect_summary": "heterozygote"},
                "TA": {"category": MODERATE, "effect_summary": "heterozygote"},
                "TT": {"effect_summary": "T-strand homozygote defaults Standard"},
            },
            evidence_level=2,
            pmids=["25495213"],
            recommendation_text="Synthetic test row.",
        )

        for homozygote in ("AA", "TT"):
            result = _score_snp(snp, homozygote, panel)
            assert result.category == INDETERMINATE, homozygote
            assert result.present_in_sample is True
            assert "palindromic" in result.effect_summary.lower()
            assert "strand" in (result.coverage_note or "").lower()

        assert _score_snp(snp, "AT", panel).category == MODERATE
        assert _score_snp(snp, "TA", panel).category == MODERATE

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

    def test_indeterminate_snp_does_not_drive_pathway_level(self) -> None:
        results = [
            _make_snp_result(INDETERMINATE),
            _make_snp_result(STANDARD),
        ]
        assert _determine_pathway_level(results) == STANDARD

    def test_all_indeterminate_pathway_defaults_standard(self) -> None:
        assert _determine_pathway_level([_make_snp_result(INDETERMINATE)]) == STANDARD


# ── Cross-module reference tests ─────────────────────────────────────────


class TestCrossModuleFindings:
    def test_cyp1a2_generates_no_cross_reference(self, panel: SleepPanel) -> None:
        """A scored CYP1A2 result no longer produces a PGx handoff (#2024).

        This is the half the panel edit alone did not reach. The generator was
        keyed on ``additional_genes["CYP1A2_pgx_context"]`` rather than on the
        SNP's ``cross_module`` block, so deleting the block left the finding
        still being written on every fresh run and only filtered at read time.
        The SNP result below is fully populated, so an empty list here is the
        generator declining to link, not the pathway failing to score.
        """
        caffeine_pr = PathwayResult(
            pathway_id="caffeine_sleep",
            pathway_name="Caffeine & Sleep",
            level=ELEVATED,
            snp_results=[
                SNPResult(
                    rsid="rs762551",
                    gene="CYP1A2",
                    variant_name=CYP1A2_LOCUS_LABEL,
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
        assert caffeine_pr.called_snps, "fixture must present a called SNP to be meaningful"
        assert _generate_cross_module_findings([caffeine_pr], panel) == []

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

    def test_a_declared_link_would_still_be_honoured(self, panel: SleepPanel) -> None:
        """The generator is panel-driven now, not hardcoded off (#2024).

        Without this, "no cross-module findings" could equally mean the
        generator was gutted. Injecting a link into a copy of the panel proves
        it still resolves one when the panel declares it — which is what makes
        the empty result above a statement about the panel, not the code.
        """
        import copy

        patched = copy.deepcopy(panel)
        target_snp = next(
            snp for pathway in patched.pathways for snp in pathway.snps if snp.rsid == "rs762551"
        )
        target_snp.cross_module = {"module": "traits", "note": "probe"}

        caffeine_pr = PathwayResult(
            pathway_id="caffeine_sleep",
            pathway_name="Caffeine & Sleep",
            level=MODERATE,
            snp_results=[
                SNPResult(
                    rsid="rs762551",
                    gene="CYP1A2",
                    variant_name=CYP1A2_LOCUS_LABEL,
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
        cross = _generate_cross_module_findings([caffeine_pr], patched)
        assert len(cross) == 1
        assert cross[0].target_module == "traits"
        assert cross[0].detail["cross_module_note"] == "probe"
        # A cross-module finding must record where it came from, not just where
        # it points. This was the only read of the field anywhere in the repo,
        # so dropping it turned CrossModuleFinding.source_module write-only and
        # Vulture flagged it in all eight producers.
        assert cross[0].source_module == MODULE_NAME
        # rs762551 alone cannot resolve a CYP1A2 star allele, and this text is
        # patient-facing, so the finding must carry the locus label the panel
        # uses and never a star-allele token.
        assert CYP1A2_LOCUS_LABEL in cross[0].finding_text
        assert "*1F" not in cross[0].finding_text
        assert not re.search(r"CYP1A2\s*\*\d", cross[0].finding_text)

    def test_a_standard_hom_ref_call_gets_no_cross_module_card(self, panel: SleepPanel) -> None:
        """Required non-carrier negative control for a carriage-gated card.

        A Standard call is hom-ref. Advertising a carrier-specific destination
        to someone who does not carry the variant is the defect this whole issue
        is about, pointed the other way, so the generator skips Standard exactly
        as the Skin, Allergy, Gene Health and Traits generators do. Paired with
        the carrier case above — same injected link, same SNP, only the category
        differs — so this discriminates rather than passing vacuously.
        """
        import copy

        patched = copy.deepcopy(panel)
        target_snp = next(
            snp for pathway in patched.pathways for snp in pathway.snps if snp.rsid == "rs762551"
        )
        target_snp.cross_module = {"module": "traits", "note": "probe"}

        hom_ref_pr = PathwayResult(
            pathway_id="caffeine_sleep",
            pathway_name="Caffeine & Sleep",
            level=STANDARD,
            snp_results=[
                SNPResult(
                    rsid="rs762551",
                    gene="CYP1A2",
                    variant_name=CYP1A2_LOCUS_LABEL,
                    genotype="AA",
                    category=STANDARD,
                    effect_summary="Rapid metabolizer",
                    evidence_level=2,
                    pmids=["16522833"],
                    recommendation_text="",
                    present_in_sample=True,
                    metabolizer_state="Rapid metabolizer",
                ),
            ],
        )
        assert hom_ref_pr.called_snps, "fixture must present a called SNP to be meaningful"
        assert _generate_cross_module_findings([hom_ref_pr], patched) == []

    def test_an_indeterminate_call_gets_no_cross_module_card(self, panel: SleepPanel) -> None:
        """The scorer withheld this genotype as uninterpretable; so must the card.

        ``called_snps`` filters on ``present_in_sample`` alone, so a
        strand-ambiguous homozygote or unmodelled allele reaches the generator
        even though ``_score_snp`` marked it Indeterminate. Pointing such a user
        at a carrier-specific destination asserts a call the module refused to
        make.
        """
        import copy

        patched = copy.deepcopy(panel)
        target_snp = next(
            snp for pathway in patched.pathways for snp in pathway.snps if snp.rsid == "rs762551"
        )
        target_snp.cross_module = {"module": "traits", "note": "probe"}

        indeterminate_pr = PathwayResult(
            pathway_id="caffeine_sleep",
            pathway_name="Caffeine & Sleep",
            level=STANDARD,
            snp_results=[
                SNPResult(
                    rsid="rs762551",
                    gene="CYP1A2",
                    variant_name=CYP1A2_LOCUS_LABEL,
                    genotype="AT",
                    category=INDETERMINATE,
                    effect_summary="Unmodelled genotype",
                    evidence_level=2,
                    pmids=["16522833"],
                    recommendation_text="",
                    present_in_sample=True,
                    metabolizer_state=None,
                ),
            ],
        )
        assert indeterminate_pr.called_snps, "called_snps must still include it to be meaningful"
        assert _generate_cross_module_findings([indeterminate_pr], patched) == []


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

        # No cross-module findings: Sleep declares no links since #2024. The
        # metabolizer assertion above is what keeps this discriminating — the
        # same CYP1A2 row is still scored, it just no longer hands off.
        assert result.cross_module_findings == []

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

    def test_no_call_and_not_on_array_coverage_status(
        self,
        panel: SleepPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """On-array no-calls are tracked separately from off-chip missing SNPs."""
        _seed_variants(sample_engine, [("rs762551", "15", 75041917, "--")])

        result = score_sleep_pathways(panel, sample_engine, reference_engine)

        caffeine = next(pr for pr in result.pathway_results if pr.pathway_id == "caffeine_sleep")
        cyp1a2 = next(s for s in caffeine.missing_snps if s.rsid == "rs762551")
        adora2a = next(s for s in caffeine.missing_snps if s.rsid == "rs5751876")
        assert cyp1a2.coverage_status == NO_CALL
        assert adora2a.coverage_status == NOT_ON_ARRAY


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

    def test_no_cross_module_finding_is_stored(
        self,
        panel: SleepPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """A fresh run writes no CYP1A2 handoff row at all (#2024).

        Retiring the link at read time keeps it off the page, but the row was
        still being written on every scoring run — so any future reader that
        forgot to normalise would resurface the dead promise. Nothing is
        written now. The sibling metabolizer row proves the sample scored, so
        an empty cross-module set is a real absence.
        """
        _seed_variants(sample_engine, [("rs762551", "15", 75041917, "CC")])

        result = score_sleep_pathways(panel, sample_engine, reference_engine)
        store_sleep_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == MODULE_NAME)
            ).fetchall()

        assert rows, "sample stored no sleep findings at all — the check is vacuous"
        assert [row for row in rows if row.category == "cross_module"] == []
        assert any("metabolizer" in (row.finding_text or "").lower() for row in rows)

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

    def test_pathway_detail_splits_no_call_from_not_on_array(
        self,
        panel: SleepPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Stored pathway detail keeps missing_snps union and adds no_call_snps subset."""
        _seed_variants(sample_engine, [("rs762551", "15", 75041917, "--")])

        result = score_sleep_pathways(panel, sample_engine, reference_engine)
        store_sleep_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.category == "pathway_summary",
                        findings.c.pathway == "Caffeine & Sleep",
                    )
                )
            ).first()

        assert row is not None
        detail = json.loads(row.detail_json)
        assert "rs762551" in detail["missing_snps"]
        assert "rs5751876" in detail["missing_snps"]
        assert detail["no_call_snps"] == ["rs762551"]
        assert "rs5751876" not in detail["no_call_snps"]

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


# ── Stored finding text prints the gene once (#2044) ───────────────────


class TestStoredFindingTextGeneLabel:
    """Production path for #2044: seed → score → store → read ``findings`` back.

    ``test_finding_text_gene_doubling.py`` guards the formatter and the panels;
    this drives ``store_sleep_findings`` itself, so a call site that rebuilds the
    label by concatenation, ``.format()`` or swapped arguments is caught on the
    persisted ``finding_text``.
    """

    def test_gene_prefixed_loci_persist_without_doubling(
        self,
        panel: SleepPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Every curated locus whose ``variant_name`` already leads with its gene.

        Covers the plain text branch: the gene-prefixed sleep loci (MEIS1,
        BTBD9) carry no metabolizer state. CYP1A2 rs762551 is the only
        metabolizer-state locus and its name is descriptor-only, so that
        branch is pinned separately below in its prepend shape.
        """
        loci = gene_prefixed_loci(panel)
        assert loci, "sleep panel has no gene-prefixed locus; this guard would be vacuous"
        _seed_variants(sample_engine, raw_variant_rows(loci))

        result = score_sleep_pathways(panel, sample_engine, reference_engine)
        store_sleep_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == MODULE_NAME)
            ).fetchall()
        assert rows
        stored = {r.rsid: r.finding_text for r in rows if r.category == "snp_finding"}
        for locus in loci:
            text = stored.get(locus.rsid)
            assert text is not None, f"{locus.rsid} ({locus.gene}) stored no snp_finding row"
            # The gene is already the first word of the variant name, so the
            # persisted card must open with the variant name itself...
            assert text.startswith(f"{locus.variant_name} ("), text
            # ...which is exactly what the shared formatter renders.
            assert text.startswith(f"{variant_label(locus.gene, locus.variant_name)} ("), text
        doubled = [r.finding_text for r in rows if renders_gene_twice(r.finding_text)]
        assert doubled == [], f"stored sleep text prints a gene twice: {doubled}"

    def test_metabolizer_branch_uses_the_shared_label(
        self,
        panel: SleepPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """CYP1A2 rs762551 CC takes the metabolizer-state text branch.

        Its curated name is descriptor-only, so the label is the prepend shape
        and the naive template would read the same; this pins the branch to the
        shared formatter (and its argument order) on the persisted text.
        """
        _seed_variants(sample_engine, [("rs762551", "15", 75041917, "CC")])
        result = score_sleep_pathways(panel, sample_engine, reference_engine)
        cyp1a2 = next(
            s for pr in result.pathway_results for s in pr.called_snps if s.rsid == "rs762551"
        )
        assert cyp1a2.metabolizer_state
        assert cyp1a2.category != STANDARD

        store_sleep_findings(result, sample_engine)
        with sample_engine.connect() as conn:
            text = conn.execute(
                sa.select(findings.c.finding_text).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.category == "snp_finding",
                    findings.c.rsid == "rs762551",
                )
            ).scalar_one()
        assert text == (
            f"{variant_label(cyp1a2.gene, cyp1a2.variant_name)} (CC) — "
            f"{cyp1a2.metabolizer_state}; {cyp1a2.effect_summary}"
        )
        assert text.startswith("CYP1A2 -163C>A")
        assert not renders_gene_twice(text)


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
