"""Tests for carrier status module (P3-36).

Covers:
  - Het P/LP extraction from annotated variants in carrier panel genes
  - Homozygous P/LP exclusion (disease, not carrier)
  - Evidence level assignment based on ClinVar review stars
  - Findings storage (module='carrier', category split by carrier context)
  - BRCA1/2 dual-role cross-links to cancer module
  - Reproductive framing in finding text
  - Empty results when no P/LP variants exist
  - T3-36: Het CFTR P/LP → carrier finding
  - T3-37: Homozygous CFTR P/LP → NO carrier finding
  - T3-38: BRCA1 het P/LP → both cancer AND carrier findings
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.carrier_status import (
    CarrierAnalysisResult,
    CarrierPanel,
    CarrierVariantResult,
    _assign_carrier_evidence_level,
    _carrier_finding_text,
    extract_carrier_variants,
    load_carrier_panel,
    store_carrier_findings,
)
from backend.db.tables import annotated_variants, findings

# ── Fixtures ──────────────────────────────────────────────────────────────

PANEL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "carrier_panel.json"
)


@pytest.fixture()
def panel() -> CarrierPanel:
    """Load the curated carrier panel from the real JSON file."""
    return load_carrier_panel(PANEL_PATH)


@pytest.fixture()
def sample_with_carrier_variants(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine with annotated variants including carrier panel het P/LP hits."""
    variants = [
        # T3-36: CFTR het Pathogenic — should produce carrier finding
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
        # HBB het Likely pathogenic — should produce carrier finding
        {
            "rsid": "rs334",
            "chrom": "11",
            "pos": 5248232,
            "genotype": "AT",
            "zygosity": "het",
            "gene_symbol": "HBB",
            "clinvar_significance": "Likely pathogenic",
            "clinvar_review_stars": 2,
            "clinvar_accession": "VCV000015333",
            "clinvar_conditions": "Sickle cell disease",
            "annotation_coverage": 2,
        },
        # T3-37: CFTR homozygous Pathogenic — should NOT produce carrier finding
        {
            "rsid": "rs75961395",
            "chrom": "7",
            "pos": 117559600,
            "genotype": "TT",
            "zygosity": "hom",
            "gene_symbol": "CFTR",
            "clinvar_significance": "Pathogenic",
            "clinvar_review_stars": 2,
            "clinvar_accession": "VCV000007106",
            "clinvar_conditions": "Cystic fibrosis",
            "annotation_coverage": 2,
        },
        # T3-38: BRCA1 het Pathogenic — dual-role (cancer + carrier)
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
        # BRCA2 het Pathogenic — dual-role, 0-star review
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
        # GBA het Likely pathogenic — 1-star review
        {
            "rsid": "rs76763715",
            "chrom": "1",
            "pos": 155240283,
            "genotype": "AG",
            "zygosity": "het",
            "gene_symbol": "GBA",
            "clinvar_significance": "Likely pathogenic",
            "clinvar_review_stars": 1,
            "clinvar_accession": "VCV000004288",
            "clinvar_conditions": "Gaucher disease",
            "annotation_coverage": 2,
        },
        # Non-panel gene Pathogenic — should NOT appear (not in carrier panel)
        {
            "rsid": "rs28934578",
            "chrom": "17",
            "pos": 7577538,
            "genotype": "CG",
            "zygosity": "het",
            "gene_symbol": "TP53",
            "clinvar_significance": "Pathogenic",
            "clinvar_review_stars": 2,
            "clinvar_accession": "VCV000012347",
            "clinvar_conditions": "Li-Fraumeni syndrome",
            "annotation_coverage": 2,
        },
        # CFTR Benign — should NOT appear
        {
            "rsid": "rs1800073",
            "chrom": "7",
            "pos": 117559700,
            "genotype": "AG",
            "zygosity": "het",
            "gene_symbol": "CFTR",
            "clinvar_significance": "Benign",
            "clinvar_review_stars": 2,
            "clinvar_accession": "VCV000099999",
            "clinvar_conditions": "not specified",
            "annotation_coverage": 2,
        },
        # VUS in panel gene — should NOT appear
        {
            "rsid": "rs999777",
            "chrom": "15",
            "pos": 72640000,
            "genotype": "AG",
            "zygosity": "het",
            "gene_symbol": "HEXA",
            "clinvar_significance": "Uncertain_significance",
            "clinvar_review_stars": 1,
            "clinvar_accession": "VCV000088888",
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


class TestCarrierEvidenceLevelAssignment:
    """Test _assign_carrier_evidence_level based on ClinVar review stars."""

    def test_pathogenic_2_plus_stars_gives_4(self) -> None:
        assert _assign_carrier_evidence_level("Pathogenic", 2, 4) == 4

    def test_pathogenic_3_stars_gives_4(self) -> None:
        assert _assign_carrier_evidence_level("Pathogenic", 3, 4) == 4

    def test_likely_pathogenic_2_stars_gives_4(self) -> None:
        assert _assign_carrier_evidence_level("Likely pathogenic", 2, 3) == 4

    def test_pathogenic_1_star_gives_4(self) -> None:
        assert _assign_carrier_evidence_level("Pathogenic", 1, 4) == 4

    def test_likely_pathogenic_1_star_gives_3(self) -> None:
        assert _assign_carrier_evidence_level("Likely pathogenic", 1, 3) == 3

    def test_pathogenic_0_stars_capped_at_2(self) -> None:
        assert _assign_carrier_evidence_level("Pathogenic", 0, 4) == 2

    def test_pathogenic_0_stars_low_gene_evidence(self) -> None:
        assert _assign_carrier_evidence_level("Pathogenic", 0, 1) == 1


# ── Extraction tests ─────────────────────────────────────────────────────


class TestExtractCarrierVariants:
    """Test het P/LP extraction from annotated variants (P3-36)."""

    def test_extracts_het_plp_variants(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        """Should find 5 het P/LP variants (CFTR, HBB, BRCA1, BRCA2, GBA)."""
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        assert result.carrier_count == 5

    def test_t3_36_cftr_het_produces_carrier_finding(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        """T3-36: Het CFTR P/LP → carrier finding (not disease)."""
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        cftr = [v for v in result.variants if v.rsid == "rs113993960"]
        assert len(cftr) == 1
        assert cftr[0].gene_symbol == "CFTR"
        assert cftr[0].zygosity == "het"
        assert cftr[0].clinvar_significance == "Pathogenic"

    def test_t3_37_hom_plp_excluded(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        """T3-37: Homozygous CFTR P/LP → NO carrier finding (disease)."""
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        rsids = {v.rsid for v in result.variants}
        assert "rs75961395" not in rsids
        assert result.homozygous_plp_skipped == 1

    def test_hom_ref_pathogenic_excluded(
        self, panel: CarrierPanel, sample_engine: sa.Engine
    ) -> None:
        """hom_ref (non-carrier) Pathogenic in a panel gene → NO carrier finding.

        The flagship genotype-agnostic-annotation guard (audit §1.1): a chip
        reports a call at every probe regardless of carriage, so a ClinVar
        Pathogenic record where the individual carries ZERO copies of the ALT
        (homozygous reference) must be suppressed entirely. This is distinct from
        T3-37, which excludes the homozygous-ALT (affected) case. A real het
        carrier in the same gene is seeded alongside as a positive control, so the
        test proves the suppression is carriage-specific rather than a blanket
        drop — and that it holds through storage, not just extraction.
        """
        variants = [
            {
                "rsid": "rs_cftr_hom_ref",
                "chrom": "7",
                "pos": 117559620,
                "genotype": "CC",  # homozygous reference — ALT not carried
                "zygosity": "hom_ref",
                "gene_symbol": "CFTR",
                "clinvar_significance": "Pathogenic",
                "clinvar_review_stars": 3,
                "clinvar_accession": "VCV000007107",
                "clinvar_conditions": "Cystic fibrosis",
                "annotation_coverage": 2,
            },
            {
                "rsid": "rs113993960",  # F508del het — positive control
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
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(annotated_variants), variants)

        result = extract_carrier_variants(panel, sample_engine)
        kept = {v.rsid for v in result.variants}
        assert kept == {"rs113993960"}  # het carrier kept, hom_ref suppressed
        assert "rs_cftr_hom_ref" not in kept
        assert result.carrier_count == 1

        # Suppression must survive storage, not just extraction.
        store_carrier_findings(result, sample_engine)
        with sample_engine.connect() as conn:
            stored = {
                r.rsid
                for r in conn.execute(
                    sa.select(findings.c.rsid).where(findings.c.module == "carrier")
                )
            }
        assert stored == {"rs113993960"}
        assert "rs_cftr_hom_ref" not in stored

    @pytest.mark.parametrize("genotype", ["AT", "TA", "DI", "ID"])
    def test_cftr_f508del_indel_carrier_call_resolved(
        self,
        panel: CarrierPanel,
        sample_engine: sa.Engine,
        genotype: str,
    ) -> None:
        """Supported F508del indel encodings resolve to carrier findings."""
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                {
                    "rsid": "rs113993960",
                    "chrom": "7",
                    "pos": 117559590,
                    "ref": "ATCT",
                    "alt": "A",
                    "genotype": genotype,
                    "zygosity": None,
                    "gene_symbol": "CFTR",
                    "clinvar_significance": "Pathogenic",
                    "clinvar_review_stars": 3,
                    "clinvar_accession": "VCV000007105",
                    "clinvar_conditions": "Cystic fibrosis",
                    "annotation_coverage": 2,
                },
            )

        result = extract_carrier_variants(panel, sample_engine)
        assert result.carrier_count == 1
        carrier = result.variants[0]
        assert carrier.rsid == "rs113993960"
        assert carrier.genotype == genotype
        assert carrier.zygosity == "het"

    def test_cftr_f508del_unknown_indel_call_stays_unresolved(
        self,
        panel: CarrierPanel,
        sample_engine: sa.Engine,
    ) -> None:
        """Unsupported F508del raw calls are not guessed as carriers."""
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                {
                    "rsid": "rs113993960",
                    "chrom": "7",
                    "pos": 117559590,
                    "ref": "ATCT",
                    "alt": "A",
                    "genotype": "CC",
                    "zygosity": None,
                    "gene_symbol": "CFTR",
                    "clinvar_significance": "Pathogenic",
                    "clinvar_review_stars": 3,
                    "clinvar_accession": "VCV000007105",
                    "clinvar_conditions": "Cystic fibrosis",
                    "annotation_coverage": 2,
                },
            )

        result = extract_carrier_variants(panel, sample_engine)
        assert result.carrier_count == 0

    def test_t3_38_brca1_dual_role(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        """T3-38: BRCA1 het P/LP → carrier finding with cross-link to cancer."""
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        brca1 = [v for v in result.variants if v.rsid == "rs80357906"]
        assert len(brca1) == 1
        assert brca1[0].gene_symbol == "BRCA1"
        assert "cancer" in brca1[0].cross_links

    def test_excludes_non_panel_genes(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        rsids = {v.rsid for v in result.variants}
        assert "rs28934578" not in rsids  # TP53 not in carrier panel

    def test_excludes_benign_variants(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        rsids = {v.rsid for v in result.variants}
        assert "rs1800073" not in rsids  # CFTR Benign

    def test_excludes_vus(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        rsids = {v.rsid for v in result.variants}
        assert "rs999777" not in rsids  # HEXA VUS

    def test_brca2_dual_role(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        brca2 = [v for v in result.variants if v.gene_symbol == "BRCA2"]
        assert len(brca2) == 1
        assert "cancer" in brca2[0].cross_links

    def test_dual_role_variants_property(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        dual = result.dual_role_variants
        genes = {v.gene_symbol for v in dual}
        assert genes == {"BRCA1", "BRCA2"}

    def test_genes_with_findings(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        assert sorted(result.genes_with_findings) == ["BRCA1", "BRCA2", "CFTR", "GBA", "HBB"]

    def test_panel_genes_checked_count(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        assert result.panel_genes_checked == 7

    def test_gba_evidence_level_3(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        """GBA Likely pathogenic with 1-star → evidence level 3."""
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        gba = [v for v in result.variants if v.gene_symbol == "GBA"]
        assert len(gba) == 1
        assert gba[0].evidence_level == 3

    def test_brca2_0_star_capped(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        """BRCA2 Pathogenic with 0-star → evidence capped at 2."""
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        brca2 = [v for v in result.variants if v.gene_symbol == "BRCA2"]
        assert len(brca2) == 1
        assert brca2[0].evidence_level == 2

    def test_conditions_enrichment(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        cftr = [v for v in result.variants if v.gene_symbol == "CFTR"][0]
        assert "Cystic Fibrosis" in cftr.conditions

    def test_inheritance_enrichment(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        cftr = [v for v in result.variants if v.gene_symbol == "CFTR"][0]
        assert cftr.inheritance == "AR"
        brca1 = [v for v in result.variants if v.gene_symbol == "BRCA1"][0]
        assert brca1.inheritance == "AD"

    def test_pmids_populated(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        cftr = [v for v in result.variants if v.gene_symbol == "CFTR"][0]
        assert len(cftr.pmids) > 0

    def test_empty_sample_returns_no_variants(
        self, panel: CarrierPanel, empty_sample: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, empty_sample)
        assert result.carrier_count == 0
        assert result.variants == []
        assert result.panel_genes_checked == 7
        assert result.homozygous_plp_skipped == 0


# ── Findings storage tests ───────────────────────────────────────────────


class TestStoreCarrierFindings:
    """Test carrier findings storage in the sample database."""

    def test_stores_correct_count(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        count = store_carrier_findings(result, sample_with_carrier_variants)
        assert count == 5

    def test_findings_have_module_carrier(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        store_carrier_findings(result, sample_with_carrier_variants)

        with sample_with_carrier_variants.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == "carrier")
            ).fetchall()
        assert len(rows) == 5
        for row in rows:
            assert row.module == "carrier"
            if row.gene_symbol in {"BRCA1", "BRCA2"}:
                assert row.category == "autosomal_dominant_dual_role_carrier"
            else:
                assert row.category == "autosomal_recessive_carrier"

    def test_all_findings_have_het_zygosity(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        store_carrier_findings(result, sample_with_carrier_variants)

        with sample_with_carrier_variants.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == "carrier")
            ).fetchall()
        for row in rows:
            assert row.zygosity == "het"

    def test_finding_text_reproductive_framing(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        """Finding text must use reproductive framing language."""
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        store_carrier_findings(result, sample_with_carrier_variants)

        with sample_with_carrier_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs113993960")
            ).fetchone()
        assert row is not None
        assert "CFTR" in row.finding_text
        assert "rs113993960" in row.finding_text
        assert "carry one copy" in row.finding_text
        assert "family planning" in row.finding_text
        assert "typically unaffected" in row.finding_text

    def test_hbb_hbs_finding_text_uses_trait_specific_context(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        """HBB HbS carrier text must not overstate carrier status as unaffected."""
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        store_carrier_findings(result, sample_with_carrier_variants)

        with sample_with_carrier_variants.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.rsid == "rs334")).fetchone()
        assert row is not None
        assert row.category == "autosomal_recessive_carrier"
        assert "HBB" in row.finding_text
        assert "rs334" in row.finding_text
        assert "sickle-cell trait" in row.finding_text
        assert "not sickle-cell disease" in row.finding_text
        assert "usually asymptomatic" in row.finding_text
        assert "kidney" in row.finding_text
        assert "exertional-stress" in row.finding_text
        assert "family planning" in row.finding_text
        assert "typically unaffected" not in row.finding_text

        pmids = json.loads(row.pmid_citations)
        assert "30383109" in pmids
        assert "25393378" in pmids

    def test_hbb_hbs_finding_text_normalizes_rsid(self) -> None:
        """HBB HbS trait wording should tolerate casing and whitespace in rsid."""
        variant = CarrierVariantResult(
            rsid=" RS334 ",
            gene_symbol="HBB",
            genotype="AT",
            zygosity="het",
            clinvar_significance="Likely pathogenic",
            clinvar_review_stars=2,
            clinvar_accession="VCV000015333",
            clinvar_conditions="Sickle cell disease",
            conditions=["Sickle Cell Disease"],
            inheritance="AR",
            evidence_level=4,
            cross_links=[],
            pmids=["30383109", "25393378"],
            notes="Synthetic HBB HbS carrier-panel example.",
        )

        text = _carrier_finding_text(variant)

        assert "sickle-cell trait" in text
        assert "typically unaffected" not in text

    def test_brca_finding_text_uses_dual_role_framing(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        """BRCA carrier findings must not use classic AR unaffected framing."""
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        store_carrier_findings(result, sample_with_carrier_variants)

        with sample_with_carrier_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs80357906")
            ).fetchone()
        assert row is not None
        assert row.category == "autosomal_dominant_dual_role_carrier"
        assert "BRCA1" in row.finding_text
        assert "rs80357906" in row.finding_text
        assert "family planning" in row.finding_text
        assert "typically unaffected" not in row.finding_text
        assert "personal hereditary cancer risk" in row.finding_text
        assert "Cancer module" in row.finding_text

        detail = json.loads(row.detail_json)
        assert "cancer" in detail["cross_links"]
        pmids = json.loads(row.pmid_citations)
        assert "28632866" in pmids
        assert "33406487" in pmids

    def test_ad_non_cancer_finding_text_does_not_use_cancer_module(
        self, sample_engine: sa.Engine
    ) -> None:
        """AD carrier findings without a cancer cross-link must avoid BRCA wording."""
        result = CarrierAnalysisResult(
            variants=[
                CarrierVariantResult(
                    rsid="rs_ad_example",
                    gene_symbol="ADGENE",
                    genotype="AG",
                    zygosity="het",
                    clinvar_significance="Pathogenic",
                    clinvar_review_stars=2,
                    clinvar_accession="VCV000000001",
                    clinvar_conditions="Example autosomal dominant condition",
                    conditions=["Example Autosomal Dominant Condition"],
                    inheritance="AD",
                    evidence_level=3,
                    cross_links=[],
                    pmids=[],
                    notes="Synthetic AD non-cancer carrier-panel example.",
                )
            ]
        )
        count = store_carrier_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs_ad_example")
            ).fetchone()
        assert count == 1
        assert row is not None
        assert row.category == "autosomal_dominant_dual_role_carrier"
        assert "family planning" in row.finding_text
        assert "typically unaffected" not in row.finding_text
        assert "Cancer module" not in row.finding_text
        assert "cancer-predisposition" not in row.finding_text
        assert "genetics professional" in row.finding_text

    def test_detail_json_has_clinvar_data(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        store_carrier_findings(result, sample_with_carrier_variants)

        with sample_with_carrier_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs113993960")
            ).fetchone()
        assert row is not None
        detail = json.loads(row.detail_json)
        assert detail["clinvar_accession"] == "VCV000007105"
        assert detail["clinvar_review_stars"] == 3
        assert detail["inheritance"] == "AR"
        assert "Cystic Fibrosis" in detail["conditions"]

    def test_detail_json_has_cross_links_for_brca(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        store_carrier_findings(result, sample_with_carrier_variants)

        with sample_with_carrier_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs80357906")
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert "cancer" in detail["cross_links"]

    def test_pmid_citations_stored_as_json(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        store_carrier_findings(result, sample_with_carrier_variants)

        with sample_with_carrier_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs113993960")
            ).fetchone()
        pmids = json.loads(row.pmid_citations)
        assert isinstance(pmids, list)
        assert len(pmids) > 0

    def test_clinvar_significance_stored(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        store_carrier_findings(result, sample_with_carrier_variants)

        with sample_with_carrier_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs113993960")
            ).fetchone()
        assert row.clinvar_significance == "Pathogenic"

    def test_clears_previous_findings_on_rerun(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        store_carrier_findings(result, sample_with_carrier_variants)
        # Run again
        store_carrier_findings(result, sample_with_carrier_variants)

        with sample_with_carrier_variants.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "carrier")
            ).scalar()
        assert count == 5  # Not 10 — previous cleared

    def test_empty_result_stores_nothing(
        self, panel: CarrierPanel, empty_sample: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, empty_sample)
        count = store_carrier_findings(result, empty_sample)
        assert count == 0

    def test_evidence_levels_stored_correctly(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        store_carrier_findings(result, sample_with_carrier_variants)

        with sample_with_carrier_variants.connect() as conn:
            rows = conn.execute(
                sa.select(findings.c.rsid, findings.c.evidence_level).where(
                    findings.c.module == "carrier"
                )
            ).fetchall()
        evidence_map = {row.rsid: row.evidence_level for row in rows}

        assert evidence_map["rs113993960"] == 4  # CFTR Pathogenic 3-star
        assert evidence_map["rs334"] == 4  # HBB LP 2-star
        assert evidence_map["rs80357906"] == 4  # BRCA1 Pathogenic 3-star
        assert evidence_map["rs76763715"] == 3  # GBA LP 1-star
        assert evidence_map["rs80359550"] == 2  # BRCA2 Pathogenic 0-star

    def test_does_not_interfere_with_cancer_findings(
        self, panel: CarrierPanel, sample_with_carrier_variants: sa.Engine
    ) -> None:
        """Carrier findings should not touch cancer module findings."""
        # Insert a cancer finding first
        with sample_with_carrier_variants.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "cancer",
                    "category": "monogenic_variant",
                    "evidence_level": 4,
                    "gene_symbol": "BRCA1",
                    "rsid": "rs80357906",
                    "finding_text": "Cancer finding",
                    "zygosity": "het",
                    "clinvar_significance": "Pathogenic",
                },
            )

        result = extract_carrier_variants(panel, sample_with_carrier_variants)
        store_carrier_findings(result, sample_with_carrier_variants)

        with sample_with_carrier_variants.connect() as conn:
            cancer_count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "cancer")
            ).scalar()
            carrier_count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "carrier")
            ).scalar()
        assert cancer_count == 1  # Cancer finding preserved
        assert carrier_count == 5  # Carrier findings stored independently


# ── Result dataclass tests ───────────────────────────────────────────────


class TestCarrierAnalysisResult:
    """Test CarrierAnalysisResult dataclass properties."""

    def test_carrier_count(self) -> None:
        result = CarrierAnalysisResult(
            variants=[
                CarrierVariantResult(
                    rsid="rs1",
                    gene_symbol="CFTR",
                    genotype="CT",
                    zygosity="het",
                    clinvar_significance="Pathogenic",
                    clinvar_review_stars=3,
                    clinvar_accession=None,
                    clinvar_conditions=None,
                    conditions=["Cystic Fibrosis"],
                    inheritance="AR",
                    evidence_level=4,
                    cross_links=[],
                    pmids=[],
                    notes="",
                )
            ]
        )
        assert result.carrier_count == 1

    def test_dual_role_variants_empty(self) -> None:
        result = CarrierAnalysisResult(
            variants=[
                CarrierVariantResult(
                    rsid="rs1",
                    gene_symbol="CFTR",
                    genotype="CT",
                    zygosity="het",
                    clinvar_significance="Pathogenic",
                    clinvar_review_stars=3,
                    clinvar_accession=None,
                    clinvar_conditions=None,
                    conditions=["Cystic Fibrosis"],
                    inheritance="AR",
                    evidence_level=4,
                    cross_links=[],
                    pmids=[],
                    notes="",
                )
            ]
        )
        assert result.dual_role_variants == []

    def test_dual_role_variants_with_brca(self) -> None:
        result = CarrierAnalysisResult(
            variants=[
                CarrierVariantResult(
                    rsid="rs1",
                    gene_symbol="BRCA1",
                    genotype="CT",
                    zygosity="het",
                    clinvar_significance="Pathogenic",
                    clinvar_review_stars=3,
                    clinvar_accession=None,
                    clinvar_conditions=None,
                    conditions=["Hereditary Breast and Ovarian Cancer Syndrome"],
                    inheritance="AD",
                    evidence_level=4,
                    cross_links=["cancer"],
                    pmids=[],
                    notes="",
                )
            ]
        )
        assert len(result.dual_role_variants) == 1

    def test_genes_with_findings(self) -> None:
        result = CarrierAnalysisResult(
            variants=[
                CarrierVariantResult(
                    rsid="rs1",
                    gene_symbol="CFTR",
                    genotype="CT",
                    zygosity="het",
                    clinvar_significance="Pathogenic",
                    clinvar_review_stars=3,
                    clinvar_accession=None,
                    clinvar_conditions=None,
                    conditions=[],
                    inheritance="AR",
                    evidence_level=4,
                    cross_links=[],
                    pmids=[],
                    notes="",
                ),
                CarrierVariantResult(
                    rsid="rs2",
                    gene_symbol="HBB",
                    genotype="AT",
                    zygosity="het",
                    clinvar_significance="Pathogenic",
                    clinvar_review_stars=3,
                    clinvar_accession=None,
                    clinvar_conditions=None,
                    conditions=[],
                    inheritance="AR",
                    evidence_level=4,
                    cross_links=[],
                    pmids=[],
                    notes="",
                ),
            ]
        )
        assert result.genes_with_findings == ["CFTR", "HBB"]
