"""Tests for cancer predisposition module (P3-13).

Covers:
  - ClinVar P/LP extraction from annotated variants in cancer panel genes
  - Evidence level assignment based on ClinVar review stars
  - Findings storage (module='cancer', category='monogenic_variant')
  - BRCA1/2 dual-role cross-links
  - Empty results when no P/LP variants exist
  - T3-12 golden fixture: BRCA1 rs80357906 → Pathogenic, ★★★★
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.cancer import (
    CancerAnalysisResult,
    CancerPanel,
    CancerVariantResult,
    _assign_evidence_level,
    extract_cancer_variants,
    load_cancer_panel,
    store_cancer_findings,
)
from backend.db.tables import annotated_variants, findings

# ── Fixtures ──────────────────────────────────────────────────────────────

PANEL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "cancer_panel.json"
)


@pytest.fixture()
def panel() -> CancerPanel:
    """Load the curated cancer panel from the real JSON file."""
    return load_cancer_panel(PANEL_PATH)


@pytest.fixture()
def sample_with_cancer_variants(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine with annotated variants including cancer panel P/LP hits."""
    variants = [
        # BRCA1 Pathogenic (T3-12 golden fixture) — 3-star review
        {
            "rsid": "rs80357906",
            "chrom": "17",
            "pos": 43091983,
            "genotype": "CT",
            "zygosity": "het",
            "gene_symbol": "BRCA1",
            "clinvar_significance": "Pathogenic",
            "clinvar_review_stars": 3,
            "clinvar_accession": "VCV000017661",
            "clinvar_conditions": "Hereditary breast and ovarian cancer syndrome",
            "annotation_coverage": 2,
        },
        # TP53 Likely pathogenic — 2-star review
        {
            "rsid": "rs28934578",
            "chrom": "17",
            "pos": 7577538,
            "genotype": "CG",
            "zygosity": "het",
            "gene_symbol": "TP53",
            "clinvar_significance": "Likely pathogenic",
            "clinvar_review_stars": 2,
            "clinvar_accession": "VCV000012347",
            "clinvar_conditions": "Li-Fraumeni syndrome",
            "annotation_coverage": 2,
        },
        # MLH1 Pathogenic — 1-star review
        {
            "rsid": "rs63751710",
            "chrom": "3",
            "pos": 37053568,
            "genotype": "AG",
            "zygosity": "het",
            "gene_symbol": "MLH1",
            "clinvar_significance": "Pathogenic",
            "clinvar_review_stars": 1,
            "clinvar_accession": "VCV000036555",
            "clinvar_conditions": "Lynch syndrome",
            "annotation_coverage": 2,
        },
        # ATM Likely pathogenic — 1-star review (→ 3 stars evidence)
        {
            "rsid": "rs587779317",
            "chrom": "11",
            "pos": 108098576,
            "genotype": "CT",
            "zygosity": "het",
            "gene_symbol": "ATM",
            "clinvar_significance": "Likely pathogenic",
            "clinvar_review_stars": 1,
            "clinvar_accession": "VCV000127345",
            "clinvar_conditions": "Ataxia-telangiectasia",
            "annotation_coverage": 2,
        },
        # BRCA2 Pathogenic — 0-star review (low confidence → capped)
        {
            "rsid": "rs80359550",
            "chrom": "13",
            "pos": 32913055,
            "genotype": "AG",
            "zygosity": "het",
            "gene_symbol": "BRCA2",
            "clinvar_significance": "Pathogenic",
            "clinvar_review_stars": 0,
            "clinvar_accession": "VCV000038060",
            "clinvar_conditions": "Hereditary breast and ovarian cancer syndrome",
            "annotation_coverage": 2,
        },
        # PMS2 Pathogenic — suppressed because PMS2CL can confound localization
        {
            "rsid": "rs_pms2_pseudogene_confounded",
            "chrom": "7",
            "pos": 60487632,
            "genotype": "AG",
            "zygosity": "het",
            "gene_symbol": "PMS2",
            "clinvar_significance": "Pathogenic",
            "clinvar_review_stars": 2,
            "clinvar_accession": "VCV000PMS2",
            "clinvar_conditions": "Lynch syndrome",
            "annotation_coverage": 2,
        },
        # APC Benign — should NOT appear in results
        {
            "rsid": "rs1801155",
            "chrom": "5",
            "pos": 112175770,
            "genotype": "TG",
            "zygosity": "het",
            "gene_symbol": "APC",
            "clinvar_significance": "Benign",
            "clinvar_review_stars": 2,
            "clinvar_accession": "VCV000012999",
            "clinvar_conditions": "Familial adenomatous polyposis",
            "annotation_coverage": 2,
        },
        # Non-panel gene Pathogenic — should NOT appear in results
        {
            "rsid": "rs113993960",
            "chrom": "7",
            "pos": 117559590,
            "genotype": "CT",
            "zygosity": "het",
            "gene_symbol": "CFTR",
            "clinvar_significance": "Pathogenic",
            "clinvar_review_stars": 3,
            "clinvar_accession": "VCV000007105",
            "clinvar_conditions": "Cystic fibrosis",
            "annotation_coverage": 2,
        },
        # VUS in panel gene — should NOT appear in results
        {
            "rsid": "rs999888",
            "chrom": "17",
            "pos": 43092000,
            "genotype": "AG",
            "zygosity": "het",
            "gene_symbol": "BRCA1",
            "clinvar_significance": "Uncertain_significance",
            "clinvar_review_stars": 1,
            "clinvar_accession": "VCV000099999",
            "clinvar_conditions": "not specified",
            "annotation_coverage": 2,
        },
    ]
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(annotated_variants), variants)
    return sample_engine


@pytest.fixture()
def empty_sample(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine with no annotated variants."""
    return sample_engine


# ── Evidence level assignment tests ──────────────────────────────────────


class TestEvidenceLevelAssignment:
    """Test _assign_evidence_level based on ClinVar review stars."""

    def test_pathogenic_2_plus_stars_gives_4(self) -> None:
        assert _assign_evidence_level("Pathogenic", 2, 4) == 4

    def test_pathogenic_3_stars_gives_4(self) -> None:
        assert _assign_evidence_level("Pathogenic", 3, 4) == 4

    def test_pathogenic_4_stars_gives_4(self) -> None:
        assert _assign_evidence_level("Pathogenic", 4, 4) == 4

    def test_likely_pathogenic_2_stars_gives_4(self) -> None:
        assert _assign_evidence_level("Likely pathogenic", 2, 3) == 4

    def test_pathogenic_1_star_gives_4(self) -> None:
        assert _assign_evidence_level("Pathogenic", 1, 4) == 4

    def test_likely_pathogenic_1_star_gives_3(self) -> None:
        assert _assign_evidence_level("Likely pathogenic", 1, 3) == 3

    def test_pathogenic_0_stars_capped_at_2(self) -> None:
        assert _assign_evidence_level("Pathogenic", 0, 4) == 2

    def test_pathogenic_0_stars_low_gene_evidence(self) -> None:
        assert _assign_evidence_level("Pathogenic", 0, 1) == 1


# ── Extraction tests ─────────────────────────────────────────────────────


class TestExtractCancerVariants:
    """Test ClinVar P/LP extraction from annotated variants."""

    def test_extracts_pathogenic_variants(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        assert result.pathogenic_count == 5

    def test_excludes_benign_variants(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        rsids = {v.rsid for v in result.variants}
        assert "rs1801155" not in rsids  # APC Benign

    def test_excludes_non_panel_genes(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        rsids = {v.rsid for v in result.variants}
        assert "rs113993960" not in rsids  # CFTR not in cancer panel

    def test_excludes_vus(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        rsids = {v.rsid for v in result.variants}
        assert "rs999888" not in rsids  # VUS

    def test_suppresses_pms2_pseudogene_confounded_calls(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        rsids = {v.rsid for v in result.variants}
        assert "rs_pms2_pseudogene_confounded" not in rsids
        assert result.pseudogene_suppressed == 1

    def test_brca1_golden_fixture(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        """T3-12: BRCA1 rs80357906 must be Pathogenic with ★★★★."""
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        brca1_variants = [v for v in result.variants if v.rsid == "rs80357906"]
        assert len(brca1_variants) == 1
        v = brca1_variants[0]
        assert v.gene_symbol == "BRCA1"
        assert v.clinvar_significance == "Pathogenic"
        assert v.evidence_level == 4
        assert v.clinvar_review_stars == 3
        assert "Hereditary" in (v.clinvar_conditions or "")

    def test_brca1_has_syndrome_enrichment(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        brca1 = [v for v in result.variants if v.rsid == "rs80357906"][0]
        assert len(brca1.syndromes) > 0
        assert any("Breast" in s or "Ovarian" in s for s in brca1.syndromes)

    def test_brca1_has_cross_links(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        brca1 = [v for v in result.variants if v.rsid == "rs80357906"][0]
        assert "carrier" in brca1.cross_links

    def test_brca2_has_cross_links(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        brca2 = [v for v in result.variants if v.gene_symbol == "BRCA2"]
        assert len(brca2) == 1
        assert "carrier" in brca2[0].cross_links

    def test_dual_role_variants(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        dual = result.dual_role_variants
        genes = {v.gene_symbol for v in dual}
        assert genes == {"BRCA1", "BRCA2"}

    def test_atm_evidence_level_3(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        """ATM Likely pathogenic with 1-star → evidence level 3."""
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        atm = [v for v in result.variants if v.gene_symbol == "ATM"]
        assert len(atm) == 1
        assert atm[0].evidence_level == 3

    def test_brca2_0_star_capped(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        """BRCA2 Pathogenic with 0-star → evidence capped at 2."""
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        brca2 = [v for v in result.variants if v.gene_symbol == "BRCA2"]
        assert len(brca2) == 1
        assert brca2[0].evidence_level == 2

    def test_panel_genes_checked_count(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        assert result.panel_genes_checked == 28

    def test_variants_in_panel_genes_count(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        # BRCA1 (x2 incl VUS), TP53, MLH1, ATM, BRCA2, PMS2, APC = 8 panel rows
        assert result.variants_in_panel_genes == 8

    def test_inheritance_pattern_enrichment(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        brca1 = [v for v in result.variants if v.rsid == "rs80357906"][0]
        assert brca1.inheritance == "AD"

    def test_pmids_populated(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        brca1 = [v for v in result.variants if v.rsid == "rs80357906"][0]
        assert len(brca1.pmids) > 0

    def test_empty_sample_returns_no_variants(
        self, panel: CancerPanel, empty_sample: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, empty_sample)
        assert result.pathogenic_count == 0
        assert result.variants == []
        assert result.panel_genes_checked == 28
        assert result.pseudogene_suppressed == 0

    def test_excludes_non_carried_zygosity(
        self, panel: CancerPanel, sample_engine: sa.Engine
    ) -> None:
        """A P/LP variant in a panel gene must NOT be reported when the
        individual does not carry the ALT allele (carriage-bug fix).

        A 23andMe chip genotypes every probe regardless of carriage, so only
        het / hom_alt rows are clinically relevant; hom_ref and unscoreable
        (NULL zygosity) rows are excluded.
        """
        variants = [
            # APC Pathogenic — carrier (het) → kept
            {
                "rsid": "rs_apc_het",
                "chrom": "5",
                "pos": 112175000,
                "ref": "C",
                "alt": "T",
                "genotype": "CT",
                "zygosity": "het",
                "gene_symbol": "APC",
                "clinvar_significance": "Pathogenic",
                "clinvar_review_stars": 2,
                "clinvar_conditions": "Familial adenomatous polyposis",
                "annotation_coverage": 2,
            },
            # APC Pathogenic — homozygous reference (NOT carried) → excluded
            {
                "rsid": "rs_apc_homref",
                "chrom": "5",
                "pos": 112175100,
                "ref": "C",
                "alt": "T",
                "genotype": "CC",
                "zygosity": "hom_ref",
                "gene_symbol": "APC",
                "clinvar_significance": "Pathogenic",
                "clinvar_review_stars": 4,
                "clinvar_conditions": "Familial adenomatous polyposis",
                "annotation_coverage": 2,
            },
            # APC Pathogenic — unscoreable indel (NULL zygosity) → excluded
            {
                "rsid": "rs_apc_indel",
                "chrom": "5",
                "pos": 112175200,
                "ref": "CTC",
                "alt": "C",
                "genotype": "II",
                "zygosity": None,
                "gene_symbol": "APC",
                "clinvar_significance": "Pathogenic",
                "clinvar_review_stars": 3,
                "clinvar_conditions": "Familial adenomatous polyposis",
                "annotation_coverage": 2,
            },
            # MUTYH hom_alt — affected homozygote → kept
            {
                "rsid": "rs_mutyh_homalt",
                "chrom": "1",
                "pos": 45797000,
                "ref": "G",
                "alt": "A",
                "genotype": "AA",
                "zygosity": "hom_alt",
                "gene_symbol": "MUTYH",
                "clinvar_significance": "Pathogenic",
                "clinvar_review_stars": 2,
                "clinvar_conditions": "Familial adenomatous polyposis 2",
                "annotation_coverage": 2,
            },
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(annotated_variants), variants)

        result = extract_cancer_variants(panel, sample_engine)

        kept = {v.rsid for v in result.variants}
        assert kept == {"rs_apc_het", "rs_mutyh_homalt"}
        assert "rs_apc_homref" not in kept
        assert "rs_apc_indel" not in kept


# ── Findings storage tests ───────────────────────────────────────────────


class TestStoreCancerFindings:
    """Test cancer findings storage in the sample database."""

    def test_stores_correct_count(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        count = store_cancer_findings(result, sample_with_cancer_variants)
        assert count == 5

    def test_findings_have_module_cancer(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        store_cancer_findings(result, sample_with_cancer_variants)

        with sample_with_cancer_variants.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == "cancer")
            ).fetchall()
        assert len(rows) == 5
        for row in rows:
            assert row.module == "cancer"
            assert row.category == "monogenic_variant"

    def test_finding_text_contains_gene_and_rsid(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        store_cancer_findings(result, sample_with_cancer_variants)

        with sample_with_cancer_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs80357906")
            ).fetchone()
        assert row is not None
        assert "BRCA1" in row.finding_text
        assert "rs80357906" in row.finding_text
        assert "Pathogenic" in row.finding_text

    def test_detail_json_has_clinvar_data(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        store_cancer_findings(result, sample_with_cancer_variants)

        with sample_with_cancer_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs80357906")
            ).fetchone()
        assert row is not None
        detail = json.loads(row.detail_json)
        assert detail["clinvar_accession"] == "VCV000017661"
        assert detail["clinvar_review_stars"] == 3
        assert "AD" == detail["inheritance"]
        assert len(detail["syndromes"]) > 0
        assert len(detail["cancer_types"]) > 0

    def test_detail_json_has_cross_links_for_brca(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        store_cancer_findings(result, sample_with_cancer_variants)

        with sample_with_cancer_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs80357906")
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert "carrier" in detail["cross_links"]

    def test_pmid_citations_stored_as_json(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        store_cancer_findings(result, sample_with_cancer_variants)

        with sample_with_cancer_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs80357906")
            ).fetchone()
        pmids = json.loads(row.pmid_citations)
        assert isinstance(pmids, list)
        assert len(pmids) > 0

    def test_clinvar_significance_stored(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        store_cancer_findings(result, sample_with_cancer_variants)

        with sample_with_cancer_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs80357906")
            ).fetchone()
        assert row.clinvar_significance == "Pathogenic"

    def test_zygosity_stored(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        store_cancer_findings(result, sample_with_cancer_variants)

        with sample_with_cancer_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs80357906")
            ).fetchone()
        assert row.zygosity == "het"

    def test_detail_json_has_genotype(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        # The variant card renders genotype from detail_json["genotype"];
        # store_cancer_findings must persist it (regression: it was omitted,
        # so the card's genotype line never rendered).
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        store_cancer_findings(result, sample_with_cancer_variants)

        with sample_with_cancer_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs80357906")
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert detail["genotype"] == "CT"

    def test_clears_previous_findings_on_rerun(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        store_cancer_findings(result, sample_with_cancer_variants)
        # Run again
        store_cancer_findings(result, sample_with_cancer_variants)

        with sample_with_cancer_variants.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "cancer")
            ).scalar()
        assert count == 5  # Not 10 — previous cleared

    def test_empty_result_stores_nothing(
        self, panel: CancerPanel, empty_sample: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, empty_sample)
        count = store_cancer_findings(result, empty_sample)
        assert count == 0

    def test_evidence_levels_stored_correctly(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        store_cancer_findings(result, sample_with_cancer_variants)

        with sample_with_cancer_variants.connect() as conn:
            rows = conn.execute(
                sa.select(findings.c.rsid, findings.c.evidence_level).where(
                    findings.c.module == "cancer"
                )
            ).fetchall()
        evidence_map = {row.rsid: row.evidence_level for row in rows}

        assert evidence_map["rs80357906"] == 4  # BRCA1 Pathogenic 3-star
        assert evidence_map["rs28934578"] == 4  # TP53 LP 2-star
        assert evidence_map["rs63751710"] == 4  # MLH1 Pathogenic 1-star
        assert evidence_map["rs587779317"] == 3  # ATM LP 1-star
        assert evidence_map["rs80359550"] == 2  # BRCA2 Pathogenic 0-star


# ── Result dataclass tests ───────────────────────────────────────────────


class TestCancerAnalysisResult:
    """Test CancerAnalysisResult dataclass properties."""

    def test_pathogenic_count(self) -> None:
        result = CancerAnalysisResult(
            variants=[
                CancerVariantResult(
                    rsid="rs1",
                    gene_symbol="BRCA1",
                    genotype="CT",
                    zygosity="het",
                    clinvar_significance="Pathogenic",
                    clinvar_review_stars=3,
                    clinvar_accession=None,
                    clinvar_conditions=None,
                    syndromes=[],
                    cancer_types=[],
                    inheritance="AD",
                    evidence_level=4,
                    cross_links=[],
                    pmids=[],
                )
            ]
        )
        assert result.pathogenic_count == 1

    def test_dual_role_variants_empty(self) -> None:
        result = CancerAnalysisResult(
            variants=[
                CancerVariantResult(
                    rsid="rs1",
                    gene_symbol="TP53",
                    genotype="CT",
                    zygosity="het",
                    clinvar_significance="Pathogenic",
                    clinvar_review_stars=3,
                    clinvar_accession=None,
                    clinvar_conditions=None,
                    syndromes=[],
                    cancer_types=[],
                    inheritance="AD",
                    evidence_level=4,
                    cross_links=[],
                    pmids=[],
                )
            ]
        )
        assert result.dual_role_variants == []

    def test_dual_role_variants_with_brca(self) -> None:
        result = CancerAnalysisResult(
            variants=[
                CancerVariantResult(
                    rsid="rs1",
                    gene_symbol="BRCA1",
                    genotype="CT",
                    zygosity="het",
                    clinvar_significance="Pathogenic",
                    clinvar_review_stars=3,
                    clinvar_accession=None,
                    clinvar_conditions=None,
                    syndromes=[],
                    cancer_types=[],
                    inheritance="AD",
                    evidence_level=4,
                    cross_links=["carrier"],
                    pmids=[],
                )
            ]
        )
        assert len(result.dual_role_variants) == 1


# ── Variant-list fetch (route helper) ─────────────────────────────────────


class TestFetchCancerFindingsExcludesPRS:
    """``_fetch_cancer_findings`` must return only monogenic variant findings.

    PRS findings share ``module == "cancer"`` (category ``"prs"``) but have
    no ``gene_symbol`` / ``rsid`` / ``clinvar_significance``. If they leak
    into the variants endpoint they render as blank cards in the monogenic
    grid (the reported bug). The fetch helper is category-scoped to prevent
    that.
    """

    def test_prs_findings_excluded_from_variants(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        from backend.api.routes.cancer import _fetch_cancer_findings

        # Store the monogenic findings, then add a PRS finding the way
        # store_cancer_prs_findings would (module=cancer, category=prs,
        # no gene/rsid/significance columns).
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        store_cancer_findings(result, sample_with_cancer_variants)
        with sample_with_cancer_variants.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "cancer",
                        "category": "prs",
                        "evidence_level": 1,
                        "finding_text": "Breast cancer PRS: 80th percentile",
                        "prs_percentile": 80.0,
                        "detail_json": json.dumps({"trait": "breast", "name": "Breast"}),
                    }
                ],
            )

        rows = _fetch_cancer_findings(sample_with_cancer_variants)

        # Only the 5 monogenic P/LP variants — the PRS row is filtered out.
        assert len(rows) == 5
        # Every returned card has the content the UI needs (non-blank): the
        # leaked PRS rows would have empty gene_symbol/rsid/significance.
        assert all(r["gene_symbol"] and r["rsid"] and r["clinvar_significance"] for r in rows)
