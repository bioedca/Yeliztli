"""Tests for the MTHFR & Methylation module (P3-52).

Covers:
  - Panel loading and dataclass construction
  - MTHFR compound heterozygosity calling (compound het + double homozygous)
  - CBS proxy coverage caveat
  - COMT Val158Met catecholamine-only framing
  - Genotype normalization
  - SNP scoring with evidence-level gating
  - Multiple Moderate findings remain Moderate
  - Pathway level determination (highest category)
  - MTHFR migration from Nutrigenomics
  - Full scoring integration with sample DB
  - Findings storage and retrieval
  - GWAS annotation_coverage bitmask (bit 5)
  - 5 pathway summary findings
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.methylation import (
    ELEVATED,
    INDETERMINATE,
    MODERATE,
    MODULE_NAME,
    STANDARD,
    MethylationPanel,
    MethylationResult,
    PanelSNP,
    PathwayResult,
    SNPResult,
    _assess_compound_heterozygosity,
    _determine_pathway_level,
    _normalize_genotype,
    _score_snp,
    load_methylation_panel,
    migrate_mthfr_from_nutrigenomics,
    score_methylation_pathways,
    store_methylation_findings,
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
    / "methylation_panel.json"
)


@pytest.fixture()
def panel() -> MethylationPanel:
    """Load the actual curated panel."""
    return load_methylation_panel(PANEL_PATH)


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
    def test_load_panel_succeeds(self, panel: MethylationPanel) -> None:
        assert panel.module == "methylation"
        assert panel.version == "1.0.0"

    def test_panel_has_five_pathways(self, panel: MethylationPanel) -> None:
        assert len(panel.pathways) == 5
        pathway_ids = {p.id for p in panel.pathways}
        assert pathway_ids == {
            "folate_mthfr",
            "methionine_cycle",
            "transsulfuration",
            "bh4_neurotransmitter",
            "choline_betaine",
        }

    def test_panel_all_rsids_count(self, panel: MethylationPanel) -> None:
        rsids = panel.all_rsids()
        # ~35 curated SNPs across 5 pathways
        assert len(rsids) >= 30

    def test_panel_snps_have_genotype_effects(self, panel: MethylationPanel) -> None:
        for pathway in panel.pathways:
            for snp in pathway.snps:
                assert len(snp.genotype_effects) > 0, f"{snp.rsid} has no genotype effects"
                for gt, effect in snp.genotype_effects.items():
                    assert "category" in effect
                    assert "effect_summary" in effect
                    assert effect["category"] in (ELEVATED, MODERATE, STANDARD)

    def test_panel_has_special_calling(self, panel: MethylationPanel) -> None:
        assert panel.special_calling is not None
        assert "MTHFR_compound_heterozygosity" in panel.special_calling
        assert "CBS_proxy_note" in panel.special_calling
        assert "COMT_catecholamine_framing" in panel.special_calling

    def test_panel_has_additional_genes(self, panel: MethylationPanel) -> None:
        assert panel.additional_genes is not None
        assert "nutrigenomics_migration" in panel.additional_genes

    def test_panel_has_scoring_rules(self, panel: MethylationPanel) -> None:
        assert panel.scoring_rules is not None
        assert panel.scoring_rules["star_1_cap"] == "Moderate"
        assert panel.scoring_rules["elevated_requires_min_stars"] == 2

    def test_load_nonexistent_panel_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_methylation_panel(Path("/nonexistent/panel.json"))

    def test_mthfr_c677t_present(self, panel: MethylationPanel) -> None:
        """MTHFR C677T (rs1801133) must be in folate_mthfr pathway."""
        folate = next(p for p in panel.pathways if p.id == "folate_mthfr")
        rsids = {s.rsid for s in folate.snps}
        assert "rs1801133" in rsids

    def test_mthfr_a1298c_present(self, panel: MethylationPanel) -> None:
        """MTHFR A1298C (rs1801131) must be in folate_mthfr pathway."""
        folate = next(p for p in panel.pathways if p.id == "folate_mthfr")
        rsids = {s.rsid for s in folate.snps}
        assert "rs1801131" in rsids

    def test_comt_present(self, panel: MethylationPanel) -> None:
        """COMT Val158Met (rs4680) must be in bh4_neurotransmitter pathway."""
        bh4 = next(p for p in panel.pathways if p.id == "bh4_neurotransmitter")
        rsids = {s.rsid for s in bh4.snps}
        assert "rs4680" in rsids

    def test_cbs_present(self, panel: MethylationPanel) -> None:
        """CBS rs234706 must be in transsulfuration pathway."""
        trans = next(p for p in panel.pathways if p.id == "transsulfuration")
        rsids = {s.rsid for s in trans.snps}
        assert "rs234706" in rsids


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

    def test_indel_markers_are_no_call_without_panel_context(self) -> None:
        assert _normalize_genotype("II") is None
        assert _normalize_genotype("DD") is None
        assert _normalize_genotype("DI") is None
        assert _normalize_genotype("ID") is None

    def test_panel_defined_indel_markers_are_preserved(self) -> None:
        scorable_genotypes = {"II", "ID", "DI", "DD"}
        assert _normalize_genotype("II", scorable_genotypes=scorable_genotypes) == "II"
        assert _normalize_genotype("DD", scorable_genotypes=scorable_genotypes) == "DD"
        assert _normalize_genotype("DI", scorable_genotypes=scorable_genotypes) == "DI"
        assert _normalize_genotype("ID", scorable_genotypes=scorable_genotypes) == "ID"

    def test_lowercase(self) -> None:
        assert _normalize_genotype("ct") == "CT"


# ── MTHFR compound heterozygosity tests ─────────────────────────────────


class TestCompoundHeterozygosity:
    def test_compound_het_detected(self, panel: MethylationPanel) -> None:
        """C677T het + A1298C het = compound heterozygote."""
        genotypes = {"rs1801133": "GA", "rs1801131": "AC"}
        result = _assess_compound_heterozygosity(panel, genotypes)
        assert result.is_compound_het is True
        assert result.is_double_homozygous is False
        assert result.label is not None
        assert "compound" in result.label.lower()

    def test_compound_het_reversed_genotypes(self, panel: MethylationPanel) -> None:
        """AG + CA should also detect compound het."""
        genotypes = {"rs1801133": "AG", "rs1801131": "CA"}
        result = _assess_compound_heterozygosity(panel, genotypes)
        assert result.is_compound_het is True

    def test_double_homozygous(self, panel: MethylationPanel) -> None:
        """C677T hom + A1298C hom = double homozygous (very rare)."""
        genotypes = {"rs1801133": "AA", "rs1801131": "CC"}
        result = _assess_compound_heterozygosity(panel, genotypes)
        assert result.is_compound_het is False
        assert result.is_double_homozygous is True
        assert result.label is not None

    def test_no_compound_het_when_both_wild_type(self, panel: MethylationPanel) -> None:
        """GG + AA = both wild type, no compound het."""
        genotypes = {"rs1801133": "GG", "rs1801131": "AA"}
        result = _assess_compound_heterozygosity(panel, genotypes)
        assert result.is_compound_het is False
        assert result.is_double_homozygous is False

    def test_no_compound_het_when_c677t_missing(self, panel: MethylationPanel) -> None:
        """Missing C677T → no compound het assessment."""
        genotypes = {"rs1801131": "AC"}
        result = _assess_compound_heterozygosity(panel, genotypes)
        assert result.is_compound_het is False

    def test_no_compound_het_when_a1298c_missing(self, panel: MethylationPanel) -> None:
        """Missing A1298C → no compound het assessment."""
        genotypes = {"rs1801133": "GA"}
        result = _assess_compound_heterozygosity(panel, genotypes)
        assert result.is_compound_het is False

    def test_c677t_hom_only_not_compound_het(self, panel: MethylationPanel) -> None:
        """C677T homozygous + A1298C wild type = not compound het."""
        genotypes = {"rs1801133": "AA", "rs1801131": "AA"}
        result = _assess_compound_heterozygosity(panel, genotypes)
        assert result.is_compound_het is False
        assert result.is_double_homozygous is False

    # ── Plus-strand vendor data (strand-harmonization regression, #528) ──
    #
    # 23andMe / AncestryDNA report on the array design (plus) strand. Ensembl
    # GRCh37 gives rs1801131 (A1298C) allele_string = "T/G", so a real A1298C
    # heterozygote reads as "GT"/"TG" — NOT the cDNA/complement "AC"/"CA" the
    # panel curates. Before #528 the raw ``in`` membership test missed these, so
    # the compound-het / double-variant label never fired for real array data.

    def test_compound_het_plus_strand_ancestrydna_repro(self, panel: MethylationPanel) -> None:
        """Real AncestryDNA compound-het repro (PGP user_file/download/4190): C677T
        het ``AG`` (plus) + A1298C het ``GT`` (plus, Ensembl T/G) must be detected.

        Fails before #528 because ``"GT" in ["AC", "CA"]`` is False.
        """
        genotypes = {"rs1801133": "AG", "rs1801131": "GT"}
        result = _assess_compound_heterozygosity(panel, genotypes)
        assert result.is_compound_het is True
        assert result.is_double_homozygous is False
        assert result.label is not None
        assert "compound" in result.label.lower()

    def test_compound_het_a1298c_plus_strand_TG_order(self, panel: MethylationPanel) -> None:
        """A1298C plus-strand het in the reversed allele order (``TG``) also resolves."""
        genotypes = {"rs1801133": "GA", "rs1801131": "TG"}
        result = _assess_compound_heterozygosity(panel, genotypes)
        assert result.is_compound_het is True

    def test_double_homozygous_plus_strand(self, panel: MethylationPanel) -> None:
        """Plus-strand double variant: C677T ``AA`` + A1298C ``GG`` (plus-strand
        1298CC) must be flagged as double homozygous.

        Fails before #528 because ``"GG" in ["CC"]`` is False.
        """
        genotypes = {"rs1801133": "AA", "rs1801131": "GG"}
        result = _assess_compound_heterozygosity(panel, genotypes)
        assert result.is_compound_het is False
        assert result.is_double_homozygous is True
        assert result.label is not None

    def test_no_compound_het_plus_strand_both_wild_type(self, panel: MethylationPanel) -> None:
        """Plus-strand wild types stay negative: C677T ``GG`` + A1298C ``TT``."""
        genotypes = {"rs1801133": "GG", "rs1801131": "TT"}
        result = _assess_compound_heterozygosity(panel, genotypes)
        assert result.is_compound_het is False
        assert result.is_double_homozygous is False


# ── CBS proxy tests ──────────────────────────────────────────────────────


class TestCBSProxy:
    def _get_cbs(self, panel: MethylationPanel) -> PanelSNP:
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs234706":
                    return snp
        pytest.fail("CBS rs234706 not found")

    def test_cbs_has_coverage_note(self, panel: MethylationPanel) -> None:
        cbs = self._get_cbs(panel)
        assert cbs.coverage_note is not None
        assert "proxy" in cbs.coverage_note.lower()

    def test_cbs_proxy_note_in_special_calling(self, panel: MethylationPanel) -> None:
        assert panel.special_calling is not None
        cbs_note = panel.special_calling["CBS_proxy_note"]
        assert cbs_note["rsid"] == "rs234706"
        assert "synonymous" in cbs_note["proxy_accuracy_note"].lower()

    def test_cbs_tt_standard_not_actionable(self, panel: MethylationPanel) -> None:
        # #211: rs234706 is a synonymous proxy whose best directly-relevant
        # evidence found no homocysteine association, so TT is no longer reported
        # as an actionable (Moderate) transsulfuration finding — it is Standard
        # (informational), but still carries its proxy coverage_note.
        cbs = self._get_cbs(panel)
        result = _score_snp(cbs, "TT")
        assert result.category == STANDARD
        assert result.coverage_note is not None
        assert "no association" in result.effect_summary.lower()

    def test_cbs_cc_standard(self, panel: MethylationPanel) -> None:
        cbs = self._get_cbs(panel)
        result = _score_snp(cbs, "CC")
        assert result.category == STANDARD


class TestQDPRThirdAllele:
    """rs1677693 (QDPR) is tri-allelic in Ensembl (G/A/T); the panel models only
    the G/A contrast. A genotype carrying the unmodeled third allele (the issue's
    observed ``GT``) must be withheld as Indeterminate, not silently scored Standard
    — which would hide the carrier as baseline 'no effect' (#608)."""

    def _get_qdpr(self, panel: MethylationPanel) -> PanelSNP:
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs1677693":
                    return snp
        pytest.fail("QDPR rs1677693 not found")

    def test_third_allele_genotype_indeterminate(self, panel: MethylationPanel) -> None:
        qdpr = self._get_qdpr(panel)
        for gt in ("GT", "TG"):
            result = _score_snp(qdpr, gt)
            assert result.category == INDETERMINATE, gt
            assert result.present_in_sample is True
            assert "does not model" in result.effect_summary, gt

    def test_modeled_genotypes_unaffected(self, panel: MethylationPanel) -> None:
        """Sanity: the modeled G/A genotypes are untouched by the #608 change."""
        qdpr = self._get_qdpr(panel)
        assert _score_snp(qdpr, "GG").category == STANDARD
        assert _score_snp(qdpr, "GA").category == MODERATE
        assert _score_snp(qdpr, "AA").category == MODERATE

    def test_no_call_still_standard_not_indeterminate(self, panel: MethylationPanel) -> None:
        """A non-nucleotide no-call must NOT become Indeterminate via the #608 path —
        it is handled as not-present/baseline, since the Indeterminate branch is
        gated on a real A/C/G/T genotype."""
        qdpr = self._get_qdpr(panel)
        result = _score_snp(qdpr, "--")
        assert result.category != INDETERMINATE


class TestFOLH1Direction:
    """rs202676 (FOLH1) risk direction must not be inverted (#750).

    Ensembl GRCh37 (plus strand): A/G, minor_allele=G (MAF ~0.39; EUR A 0.77 /
    G 0.23) — so A is the reference *major* allele and G is the variant. The
    panel previously labelled A the risk allele, scoring the homozygous-reference
    AA (~55%+ of EUR) as "Homozygous FOLH1 variant — reduced folate absorption"
    and the true variant GG as Standard — the exact opposite of the panel's own
    primary citation (Guo 2012, PMID 22918695: the GG genotype has lower folate /
    higher homocysteine). These assertions lock the corrected direction.
    """

    def _get_folh1(self, panel: MethylationPanel) -> PanelSNP:
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs202676":
                    return snp
        pytest.fail("FOLH1 rs202676 not found")

    def test_panel_alleles_not_inverted(self, panel: MethylationPanel) -> None:
        folh1 = self._get_folh1(panel)
        # G (the Ensembl minor allele) is the variant; A (major/reference) is not.
        assert folh1.risk_allele == "G"
        assert folh1.ref_allele == "A"
        # variant_name corrected from the spurious Thr→Ser to the real Tyr→His.
        assert folh1.variant_name == "Y60H"

    def test_homozygous_reference_AA_is_standard(self, panel: MethylationPanel) -> None:
        # AA is homozygous REFERENCE (the EUR majority) — it must read as normal,
        # never "Homozygous FOLH1 variant".
        result = _score_snp(self._get_folh1(panel), "AA")
        assert result.category == STANDARD
        assert "variant" not in result.effect_summary.lower()
        assert "normal" in result.effect_summary.lower()

    def test_homozygous_variant_GG_is_the_reduced_state(self, panel: MethylationPanel) -> None:
        # GG is the homozygous VARIANT (Guo 2012: lower folate) — capped Moderate
        # at evidence_level 1.
        result = _score_snp(self._get_folh1(panel), "GG")
        assert result.category == MODERATE
        assert "homozygous" in result.effect_summary.lower()
        assert "reduced" in result.effect_summary.lower()

    def test_heterozygous_is_moderate(self, panel: MethylationPanel) -> None:
        folh1 = self._get_folh1(panel)
        assert _score_snp(folh1, "GA").category == MODERATE
        assert _score_snp(folh1, "AG").category == MODERATE


class TestCholineBetaineAlleleFrames:
    """Issue #717: BHMT/SLC44A1 genotype keys must match real variant alleles."""

    def _get_snp(self, panel: MethylationPanel, rsid: str) -> PanelSNP:
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == rsid:
                    return snp
        pytest.fail(f"{rsid} not found")

    def test_rs585800_real_heterozygotes_resolve(self, panel: MethylationPanel) -> None:
        bhmt = self._get_snp(panel, "rs585800")
        for genotype in ("AT", "TA"):
            result = _score_snp(bhmt, genotype)
            assert result.category == MODERATE, genotype
            assert result.present_in_sample is True
            assert "does not model" not in result.effect_summary

    def test_rs585800_palindromic_homozygotes_withheld(self, panel: MethylationPanel) -> None:
        bhmt = self._get_snp(panel, "rs585800")
        for genotype in ("AA", "TT"):
            result = _score_snp(bhmt, genotype)
            assert result.category == INDETERMINATE, genotype
            assert result.present_in_sample is True
            assert "palindromic" in result.effect_summary.lower()
            assert "strand" in (result.coverage_note or "").lower()

    def test_rs3199966_real_genotypes_resolve(self, panel: MethylationPanel) -> None:
        slc44a1 = self._get_snp(panel, "rs3199966")
        expected = {
            "GG": STANDARD,
            "GT": MODERATE,
            "TG": MODERATE,
            "TT": MODERATE,
        }
        for genotype, category in expected.items():
            result = _score_snp(slc44a1, genotype)
            assert result.category == category, genotype
            assert result.present_in_sample is True
            assert "does not model" not in result.effect_summary


# ── COMT catecholamine framing tests ─────────────────────────────────────


class TestCOMTFraming:
    def _get_comt(self, panel: MethylationPanel) -> PanelSNP:
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs4680":
                    return snp
        pytest.fail("COMT rs4680 not found")

    def test_comt_framing_restriction(self, panel: MethylationPanel) -> None:
        assert panel.special_calling is not None
        framing = panel.special_calling["COMT_catecholamine_framing"]
        assert framing["framing_restriction"] == "catecholamine_clearance_only"

    def test_comt_effect_text_catecholamine(self, panel: MethylationPanel) -> None:
        """COMT effect summaries should mention catecholamine/SAM, not psychiatric."""
        comt = self._get_comt(panel)
        for gt, effect in comt.genotype_effects.items():
            text = effect["effect_summary"].lower()
            # Should not contain psychiatric/warrior/worrier framing
            assert "warrior" not in text
            assert "worrier" not in text
            assert "psychiatric" not in text

    def test_comt_aa_scored(self, panel: MethylationPanel) -> None:
        comt = self._get_comt(panel)
        result = _score_snp(comt, "AA")
        assert result.present_in_sample is True
        assert result.category in (MODERATE, ELEVATED, STANDARD)


# ── SNP scoring tests ────────────────────────────────────────────────────


class TestSNPScoring:
    def test_not_genotyped_returns_standard(self, panel: MethylationPanel) -> None:
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

    def test_palindromic_homozygote_withheld_as_indeterminate(
        self, panel: MethylationPanel
    ) -> None:
        """#269: TCN2 rs1801198 is C/G palindromic (CC=Standard, GG=Elevated), so
        both homozygotes are withheld as Indeterminate with a strand caveat; the
        heterozygote stays resolvable."""
        snp = next(s for pw in panel.pathways for s in pw.snps if s.rsid == "rs1801198")
        for homozygote in ("CC", "GG"):
            result = _score_snp(snp, homozygote)
            assert result.category == INDETERMINATE, homozygote
            assert result.present_in_sample is True
            assert "palindromic" in result.effect_summary.lower()
            assert "strand" in (result.coverage_note or "").lower()
        assert _score_snp(snp, "CG").category == MODERATE
        assert _score_snp(snp, "GC").category == MODERATE

    def test_reversed_genotype_lookup(self, panel: MethylationPanel) -> None:
        """Panel handles reversed genotype strings (e.g. GA vs AG)."""
        mthfr = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs1801133":
                    mthfr = snp
                    break
        assert mthfr is not None

        result_ga = _score_snp(mthfr, "GA")
        result_ag = _score_snp(mthfr, "AG")
        assert result_ga.category == result_ag.category == MODERATE

    def test_unknown_genotype_defaults_standard(self, panel: MethylationPanel) -> None:
        snp = panel.pathways[0].snps[0]
        result = _score_snp(snp, "ZZ")
        assert result.category == STANDARD
        assert result.present_in_sample is True

    def test_mthfr_c677t_aa_elevated(self, panel: MethylationPanel) -> None:
        """MTHFR C677T has evidence_level=2, so AA → Elevated."""
        mthfr = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs1801133":
                    mthfr = snp
                    break
        assert mthfr is not None
        assert mthfr.evidence_level == 2
        result = _score_snp(mthfr, "AA")
        assert result.category == ELEVATED

    def test_mthfr_c677t_gg_standard(self, panel: MethylationPanel) -> None:
        """MTHFR C677T GG → Standard (wild type)."""
        mthfr = None
        for pw in panel.pathways:
            for snp in pw.snps:
                if snp.rsid == "rs1801133":
                    mthfr = snp
                    break
        assert mthfr is not None
        result = _score_snp(mthfr, "GG")
        assert result.category == STANDARD


# ── Pathway level determination tests ────────────────────────────────────


class TestPathwayLevel:
    def test_elevated_wins(self) -> None:
        results = [
            _make_snp_result(STANDARD, present=True),
            _make_snp_result(ELEVATED, present=True, evidence=2),
            _make_snp_result(MODERATE, present=True),
        ]
        level, promoted = _determine_pathway_level(results)
        assert level == ELEVATED
        assert promoted is False

    def test_moderate_when_no_elevated(self) -> None:
        results = [
            _make_snp_result(STANDARD, present=True),
            _make_snp_result(MODERATE, present=True),
        ]
        level, promoted = _determine_pathway_level(results)
        assert level == MODERATE
        assert promoted is False

    def test_standard_when_all_standard(self) -> None:
        results = [
            _make_snp_result(STANDARD, present=True),
            _make_snp_result(STANDARD, present=True),
        ]
        level, promoted = _determine_pathway_level(results)
        assert level == STANDARD
        assert promoted is False

    def test_empty_results(self) -> None:
        level, promoted = _determine_pathway_level([])
        assert level == STANDARD
        assert promoted is False

    def test_only_missing_snps_gives_standard(self) -> None:
        results = [
            _make_snp_result(ELEVATED, present=False),
            _make_snp_result(MODERATE, present=False),
        ]
        level, promoted = _determine_pathway_level(results)
        assert level == STANDARD
        assert promoted is False

    def test_multiple_moderates_do_not_promote_to_elevated(self) -> None:
        """Multiple Moderate SNPs do not create a generic Elevated pathway call."""
        results = [
            _make_snp_result(MODERATE, present=True, evidence=2),
            _make_snp_result(MODERATE, present=True, evidence=1),
            _make_snp_result(MODERATE, present=True, evidence=1),
        ]
        level, promoted = _determine_pathway_level(results)
        assert level == MODERATE
        assert promoted is False

    def test_three_star1_moderates_stay_moderate(self) -> None:
        """Three ★☆ Moderate SNPs stay Moderate."""
        results = [
            _make_snp_result(MODERATE, present=True, evidence=1),
            _make_snp_result(MODERATE, present=True, evidence=1),
            _make_snp_result(MODERATE, present=True, evidence=1),
        ]
        level, promoted = _determine_pathway_level(results)
        assert level == MODERATE
        assert promoted is False

    def test_two_moderates_stay_moderate(self) -> None:
        """Only 2 Moderate SNPs stay Moderate."""
        results = [
            _make_snp_result(MODERATE, present=True, evidence=2),
            _make_snp_result(MODERATE, present=True, evidence=2),
            _make_snp_result(STANDARD, present=True),
        ]
        level, promoted = _determine_pathway_level(results)
        assert level == MODERATE
        assert promoted is False

    def test_actual_elevated_snp_still_drives_elevated_pathway(self) -> None:
        """An actual Elevated SNP still drives an Elevated pathway."""
        results = [
            _make_snp_result(ELEVATED, present=True, evidence=2),
            _make_snp_result(MODERATE, present=True, evidence=2),
            _make_snp_result(MODERATE, present=True, evidence=1),
            _make_snp_result(MODERATE, present=True, evidence=1),
        ]
        level, promoted = _determine_pathway_level(results)
        assert level == ELEVATED
        assert promoted is False  # Not promoted, already Elevated


# ── Integration tests ────────────────────────────────────────────────────


class TestScorePathways:
    @pytest.mark.parametrize(
        ("genotype", "expected_category"),
        [
            ("II", STANDARD),
            ("ID", MODERATE),
            ("DI", MODERATE),
            ("DD", MODERATE),
        ],
    )
    def test_dhfr_indel_genotypes_score_through_pipeline(
        self,
        genotype: str,
        expected_category: str,
        panel: MethylationPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """DHFR 19 bp I/D calls are curated panel genotypes, not no-calls."""
        _seed_variants(sample_engine, [("rs70991108", "5", 79950755, genotype)])

        result = score_methylation_pathways(panel, sample_engine, reference_engine)

        folate = next(pr for pr in result.pathway_results if pr.pathway_id == "folate_mthfr")
        dhfr = next((s for s in folate.snp_results if s.rsid == "rs70991108"), None)

        assert dhfr is not None
        assert dhfr.present_in_sample is True
        assert dhfr.genotype == genotype
        assert dhfr.category == expected_category

    def test_dhfr_19bp_indel_located_in_intron_1_not_promoter(self) -> None:
        """#351: the DHFR rs70991108 19 bp del/ins is in intron 1, not the
        promoter (Johnson 2004 PMID 14735580 'intron-1'; Kalmbach 2008 PMID
        19022952 'maps to intron 1'). Its effect summaries and coverage note
        must say 'intron 1', not 'promoter'."""
        data = json.loads(PANEL_PATH.read_text(encoding="utf-8"))
        dhfr = next(s for pw in data["pathways"] for s in pw["snps"] if s["rsid"] == "rs70991108")
        effects = dhfr["genotype_effects"]
        # No text may mislabel the variant as promoter-located.
        all_texts = [e["effect_summary"] for e in effects.values()] + [dhfr["coverage_note"]]
        for text in all_texts:
            assert "promoter" not in text.lower(), text
        # The coverage note and each deletion-bearing genotype place it in intron 1.
        assert "intron 1" in dhfr["coverage_note"].lower()
        for gt in ("ID", "DI", "DD"):
            assert "intron 1" in effects[gt]["effect_summary"].lower(), gt

    def test_full_scoring_mthfr_variants(
        self,
        panel: MethylationPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Score pathways with MTHFR C677T and A1298C genotyped."""
        _seed_variants(
            sample_engine,
            [
                ("rs1801133", "1", 11856378, "AA"),  # C677T homozygous → Elevated
                ("rs1801131", "1", 11854476, "AC"),  # A1298C het → Moderate
            ],
        )
        _seed_gwas(
            reference_engine,
            [("rs1801133", "Homocysteine levels")],
        )

        result = score_methylation_pathways(panel, sample_engine, reference_engine)

        # Folate & MTHFR: C677T AA=Elevated, A1298C AC=Moderate → Elevated
        folate = next(pr for pr in result.pathway_results if pr.pathway_id == "folate_mthfr")
        assert folate.level == ELEVATED

        # GWAS matches
        assert "rs1801133" in result.gwas_matched_rsids

    def test_compound_het_detected_in_scoring(
        self,
        panel: MethylationPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Compound het: C677T het + A1298C het."""
        _seed_variants(
            sample_engine,
            [
                ("rs1801133", "1", 11856378, "GA"),  # C677T het
                ("rs1801131", "1", 11854476, "AC"),  # A1298C het
            ],
        )

        result = score_methylation_pathways(panel, sample_engine, reference_engine)
        assert result.compound_het is not None
        assert result.compound_het.is_compound_het is True

    def test_missing_snps_default_standard(
        self,
        panel: MethylationPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Pathways with no genotyped SNPs default to Standard."""
        result = score_methylation_pathways(panel, sample_engine, reference_engine)
        for pr in result.pathway_results:
            assert pr.level == STANDARD
            assert len(pr.called_snps) == 0
            assert len(pr.missing_snps) > 0

    def test_cbs_proxy_coverage_note_preserved(
        self,
        panel: MethylationPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """CBS coverage note survives through scoring pipeline."""
        _seed_variants(sample_engine, [("rs234706", "21", 44483228, "TT")])
        result = score_methylation_pathways(panel, sample_engine, reference_engine)

        trans = next(pr for pr in result.pathway_results if pr.pathway_id == "transsulfuration")
        cbs = next((s for s in trans.called_snps if s.rsid == "rs234706"), None)
        assert cbs is not None
        assert cbs.coverage_note is not None
        assert "proxy" in cbs.coverage_note.lower()

    def test_five_pathways_always_returned(
        self,
        panel: MethylationPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Scoring always returns exactly 5 pathway results."""
        result = score_methylation_pathways(panel, sample_engine, reference_engine)
        assert len(result.pathway_results) == 5


# ── MTHFR migration tests ────────────────────────────────────────────────


class TestMTHFRMigration:
    def test_migration_deletes_nutrigenomics_mthfr(self, sample_engine: sa.Engine) -> None:
        """Migration removes MTHFR findings from nutrigenomics module."""
        # Seed nutrigenomics MTHFR findings
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "nutrigenomics",
                        "category": "snp_finding",
                        "evidence_level": 2,
                        "gene_symbol": "MTHFR",
                        "rsid": "rs1801133",
                        "finding_text": "MTHFR C677T from nutrigenomics",
                        "pathway": "Folate Metabolism",
                        "pathway_level": "Elevated",
                        "detail_json": "{}",
                    },
                    {
                        "module": "nutrigenomics",
                        "category": "snp_finding",
                        "evidence_level": 2,
                        "gene_symbol": "MTHFR",
                        "rsid": "rs1801131",
                        "finding_text": "MTHFR A1298C from nutrigenomics",
                        "pathway": "Folate Metabolism",
                        "pathway_level": "Moderate",
                        "detail_json": "{}",
                    },
                    {
                        "module": "nutrigenomics",
                        "category": "snp_finding",
                        "evidence_level": 1,
                        "gene_symbol": "VDR",
                        "rsid": "rs2228570",
                        "finding_text": "VDR from nutrigenomics (should NOT be deleted)",
                        "pathway": "Vitamin D",
                        "pathway_level": "Moderate",
                        "detail_json": "{}",
                    },
                ],
            )

        deleted = migrate_mthfr_from_nutrigenomics(sample_engine)
        assert deleted == 2  # Only MTHFR rsids deleted

        # VDR finding should remain
        with sample_engine.connect() as conn:
            remaining = conn.execute(
                sa.select(findings).where(findings.c.module == "nutrigenomics")
            ).fetchall()
        assert len(remaining) == 1
        assert remaining[0].rsid == "rs2228570"

    def test_migration_idempotent(self, sample_engine: sa.Engine) -> None:
        """Running migration twice doesn't cause errors."""
        deleted1 = migrate_mthfr_from_nutrigenomics(sample_engine)
        deleted2 = migrate_mthfr_from_nutrigenomics(sample_engine)
        assert deleted1 == 0
        assert deleted2 == 0


# ── Findings storage tests ─────────────────────────────────────────────


class TestStoreFindingsIntegration:
    def test_store_and_retrieve_findings(
        self,
        panel: MethylationPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Store findings and verify they're in the DB."""
        _seed_variants(
            sample_engine,
            [
                ("rs1801133", "1", 11856378, "AA"),  # MTHFR C677T → Elevated
                ("rs1801131", "1", 11854476, "AC"),  # MTHFR A1298C → Moderate
            ],
        )

        result = score_methylation_pathways(panel, sample_engine, reference_engine)
        count = store_methylation_findings(result, sample_engine)
        assert count > 0

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == MODULE_NAME)
            ).fetchall()

        assert len(rows) == count

        # Check pathway summary findings exist (always 5)
        pathway_summaries = [r for r in rows if r.category == "pathway_summary"]
        assert len(pathway_summaries) == 5

    def test_compound_het_finding_stored(
        self,
        panel: MethylationPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Compound het generates its own finding."""
        _seed_variants(
            sample_engine,
            [
                ("rs1801133", "1", 11856378, "GA"),  # C677T het
                ("rs1801131", "1", 11854476, "AC"),  # A1298C het
            ],
        )

        result = score_methylation_pathways(panel, sample_engine, reference_engine)
        store_methylation_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            ch_rows = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.category == "compound_het",
                    )
                )
            ).fetchall()

        assert len(ch_rows) == 1
        assert "compound" in ch_rows[0].finding_text.lower()
        detail = json.loads(ch_rows[0].detail_json)
        assert detail["is_compound_het"] is True

    def test_cbs_finding_includes_coverage_note(
        self,
        panel: MethylationPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """CBS coverage_note is surfaced via the transsulfuration pathway-summary
        snp_details. #211: rs234706 is now Standard (informational), so it no
        longer emits a standalone snp_finding, but its proxy coverage_note still
        travels with the pathway summary."""
        _seed_variants(sample_engine, [("rs234706", "21", 44483228, "TT")])

        result = score_methylation_pathways(panel, sample_engine, reference_engine)
        store_methylation_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            # The now-Standard CBS variant must NOT produce a standalone finding.
            standalone = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.rsid == "rs234706",
                    )
                )
            ).first()
            assert standalone is None
            summaries = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.category == "pathway_summary",
                    )
                )
            ).fetchall()

        cbs_detail = next(
            s
            for r in summaries
            for s in json.loads(r.detail_json)["snp_details"]
            if s["rsid"] == "rs234706"
        )
        assert cbs_detail["category"] == "Standard"
        assert "proxy" in cbs_detail["coverage_note"].lower()

    def test_store_clears_previous_findings(
        self,
        panel: MethylationPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Re-running store clears previous methylation findings."""
        _seed_variants(sample_engine, [("rs1801133", "1", 11856378, "AA")])

        result = score_methylation_pathways(panel, sample_engine, reference_engine)
        store_methylation_findings(result, sample_engine)
        count2 = store_methylation_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == MODULE_NAME)
            ).fetchall()

        assert len(rows) == count2  # No duplicates

    def test_no_snp_findings_for_empty_sample(
        self,
        panel: MethylationPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Empty sample produces pathway summaries but no SNP findings."""
        result = score_methylation_pathways(panel, sample_engine, reference_engine)
        count = store_methylation_findings(result, sample_engine)

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
        assert count == 5  # 5 pathway summaries, all Standard

    def test_findings_include_pmids(
        self,
        panel: MethylationPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Findings include PubMed citations."""
        _seed_variants(sample_engine, [("rs1801133", "1", 11856378, "AA")])

        result = score_methylation_pathways(panel, sample_engine, reference_engine)
        store_methylation_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.rsid == "rs1801133",
                    )
                )
            ).first()

        assert row is not None
        pmids = json.loads(row.pmid_citations)
        assert "23824729" in pmids

    def test_multiple_moderate_pathway_stored_without_additive_promotion(
        self,
        panel: MethylationPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Multiple Moderate folate findings stay Moderate and are marked as context."""
        # Seed the historical additive-promotion case: 4 Moderate SNPs including ★★.
        _seed_variants(
            sample_engine,
            [
                ("rs1801133", "1", 11856378, "GA"),  # C677T het → Moderate (★★)
                ("rs1801131", "1", 11854476, "AC"),  # A1298C het → Moderate (★★)
                ("rs1051266", "21", 46957794, "AA"),  # SLC19A1 hom → Moderate (★☆)
                ("rs202676", "11", 49175363, "GG"),  # FOLH1 hom variant → Moderate (★☆) (#750)
            ],
        )

        result = score_methylation_pathways(panel, sample_engine, reference_engine)
        folate = next(pr for pr in result.pathway_results if pr.pathway_id == "folate_mthfr")

        assert folate.level == MODERATE
        assert folate.additive_promoted is False

        store_methylation_findings(result, sample_engine)
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.category == "pathway_summary",
                        findings.c.pathway == "Folate & MTHFR",
                    )
                )
            ).first()
        assert row is not None
        assert row.pathway_level == MODERATE
        assert (
            row.finding_text
            == "Folate & MTHFR — Moderate consideration (multiple moderate findings)"
        )
        detail = json.loads(row.detail_json)
        assert detail["additive_promoted"] is False
        assert detail["moderate_snp_count"] == 4
        assert detail["multiple_moderate_findings"] is True

    def test_reference_folate_genotypes_do_not_store_multiple_moderate_marker(
        self,
        panel: MethylationPanel,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Reference folate genotypes stay Standard and do not get the context marker."""
        _seed_variants(
            sample_engine,
            [
                ("rs1801133", "1", 11856378, "GG"),  # C677T reference
                ("rs1801131", "1", 11854476, "AA"),  # A1298C reference
                ("rs1051266", "21", 46957794, "GG"),  # SLC19A1 reference
                ("rs202676", "11", 49175363, "AA"),  # FOLH1 reference (AA = major allele, #750)
            ],
        )

        result = score_methylation_pathways(panel, sample_engine, reference_engine)
        folate = next(pr for pr in result.pathway_results if pr.pathway_id == "folate_mthfr")

        assert folate.level == STANDARD
        assert folate.additive_promoted is False

        store_methylation_findings(result, sample_engine)
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    sa.and_(
                        findings.c.module == MODULE_NAME,
                        findings.c.category == "pathway_summary",
                        findings.c.pathway == "Folate & MTHFR",
                    )
                )
            ).first()
        assert row is not None
        assert row.pathway_level == STANDARD
        assert row.finding_text == "Folate & MTHFR — Standard (no variants of concern)"
        detail = json.loads(row.detail_json)
        assert detail["additive_promoted"] is False
        assert detail["moderate_snp_count"] == 0
        assert detail["multiple_moderate_findings"] is False


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
                {"rsid": "rs1801133", "chrom": "1", "pos": 11856378, "genotype": "AA"},
            ],
            annotated=[
                {
                    "rsid": "rs1801133",
                    "chrom": "1",
                    "pos": 11856378,
                    "genotype": "AA",
                    "annotation_coverage": 0b001111,
                },
            ],
        )

        result = MethylationResult(
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

        assert val == 0b101111  # 47

    def test_null_annotation_coverage_gets_gwas_bit(self) -> None:
        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs1801133", "chrom": "1", "pos": 11856378, "genotype": "AA"},
            ],
            annotated=[
                {
                    "rsid": "rs1801133",
                    "chrom": "1",
                    "pos": 11856378,
                    "genotype": "AA",
                    "annotation_coverage": None,
                },
            ],
        )

        result = MethylationResult(
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

        assert val == GWAS_BIT

    def test_empty_gwas_matched_returns_zero(self) -> None:
        sample = self._make_sample_with_annotated(raw=[], annotated=[])
        result = MethylationResult(pathway_results=[], gwas_matched_rsids=[])
        updated = update_annotation_coverage_gwas(result, sample)
        assert updated == 0

    def test_idempotent_double_application(self) -> None:
        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs1801133", "chrom": "1", "pos": 11856378, "genotype": "AA"},
            ],
            annotated=[
                {
                    "rsid": "rs1801133",
                    "chrom": "1",
                    "pos": 11856378,
                    "genotype": "AA",
                    "annotation_coverage": GWAS_BIT,
                },
            ],
        )

        result = MethylationResult(
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
    evidence: int = 2,
) -> SNPResult:
    return SNPResult(
        rsid="rs0000001",
        gene="TEST",
        variant_name="Test",
        genotype="AA" if present else None,
        category=category,
        effect_summary="Test effect.",
        evidence_level=evidence,
        pmids=[],
        recommendation_text="Test.",
        present_in_sample=present,
    )
