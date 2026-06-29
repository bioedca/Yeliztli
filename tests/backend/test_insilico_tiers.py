"""Tests for calibrated in-silico ACMG/AMP tiers (Pejaver 2022, REVEL-only).

Exact-threshold unit coverage plus integration: the tier is attached as an
additive DRAFT ``detail_json['insilico']`` block on existing cancer/
cardiovascular findings and must NEVER mutate ``evidence_level`` or
``clinvar_significance``.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend.analysis.cancer import (
    extract_cancer_variants,
    load_cancer_panel,
    store_cancer_findings,
)
from backend.analysis.insilico_tiers import (
    insilico_block,
    is_missense_consequence,
    revel_to_acmg_tier,
)
from backend.db.tables import annotated_variants, findings


class TestRevelThresholds:
    @pytest.mark.parametrize(
        ("revel", "tier", "criterion", "strength"),
        [
            (0.95, "PP3_Strong", "PP3", "Strong"),
            (0.932, "PP3_Strong", "PP3", "Strong"),  # closed lower bound
            (0.80, "PP3_Moderate", "PP3", "Moderate"),
            (0.773, "PP3_Moderate", "PP3", "Moderate"),  # closed lower bound
            (0.70, "PP3_Supporting", "PP3", "Supporting"),
            (0.644, "PP3_Supporting", "PP3", "Supporting"),  # closed lower bound
            (0.50, None, None, None),  # indeterminate gap
            (0.30, None, None, None),  # still gap (>0.290)
            (0.290, "BP4_Supporting", "BP4", "Supporting"),  # closed upper bound
            (0.25, "BP4_Supporting", "BP4", "Supporting"),
            (0.183, "BP4_Moderate", "BP4", "Moderate"),  # closed upper bound
            (0.10, "BP4_Moderate", "BP4", "Moderate"),
            (0.016, "BP4_Strong", "BP4", "Strong"),  # closed upper bound
            (0.010, "BP4_Strong", "BP4", "Strong"),
            (0.003, "BP4_VeryStrong", "BP4", "Very Strong"),  # closed upper bound
            (0.002, "BP4_VeryStrong", "BP4", "Very Strong"),
        ],
    )
    def test_missense_thresholds(
        self,
        revel: float,
        tier: str | None,
        criterion: str | None,
        strength: str | None,
    ) -> None:
        result = revel_to_acmg_tier(revel, is_missense=True)
        actual = (result.tier, result.criterion, result.strength) if result else (None, None, None)
        assert actual == (tier, criterion, strength)

    def test_non_missense_returns_none(self) -> None:
        assert revel_to_acmg_tier(0.95, is_missense=False) is None

    def test_missing_revel_returns_none(self) -> None:
        assert revel_to_acmg_tier(None, is_missense=True) is None


class TestMissenseConsequence:
    def test_plain_missense(self) -> None:
        assert is_missense_consequence("missense_variant") is True

    def test_compound_missense(self) -> None:
        assert is_missense_consequence("missense_variant&splice_region_variant") is True

    def test_non_missense(self) -> None:
        assert is_missense_consequence("synonymous_variant") is False
        assert is_missense_consequence("frameshift_variant") is False

    def test_none_and_empty(self) -> None:
        assert is_missense_consequence(None) is False
        assert is_missense_consequence("") is False


class TestInsilicoBlock:
    def test_block_for_missense(self) -> None:
        block = insilico_block(0.95, "missense_variant")
        assert block["tier"] == "PP3_Strong"
        assert block["is_draft"] is True
        assert block["predictor"] == "REVEL"
        assert "not a clinical classification" in block["note"].lower()

    def test_no_block_for_non_missense(self) -> None:
        assert insilico_block(0.95, "synonymous_variant") is None

    def test_no_block_in_gap(self) -> None:
        assert insilico_block(0.50, "missense_variant") is None


def _seed_cancer_variant(engine: sa.Engine, *, revel, consequence) -> None:
    """Seed one carried TP53 (cancer-panel) P/LP missense-or-not variant."""
    with engine.begin() as conn:
        conn.execute(
            sa.insert(annotated_variants),
            [
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
                    "revel": revel,
                    "consequence": consequence,
                    "annotation_coverage": 2,
                }
            ],
        )


class TestCancerIntegration:
    def test_missense_attaches_draft_tier_without_mutation(self, sample_engine: sa.Engine) -> None:
        _seed_cancer_variant(sample_engine, revel=0.95, consequence="missense_variant")
        panel = load_cancer_panel()
        result = extract_cancer_variants(panel, sample_engine)
        # The evidence_level is decided by ClinVar significance/stars alone.
        expected_evidence = result.variants[0].evidence_level
        store_cancer_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.gene_symbol == "TP53")
            ).fetchone()
        assert row is not None
        detail = json.loads(row.detail_json)
        assert detail["insilico"]["tier"] == "PP3_Strong"
        assert detail["insilico"]["is_draft"] is True
        # The DRAFT tag must not touch the clinical fields.
        assert row.clinvar_significance == "Pathogenic"
        assert row.evidence_level == expected_evidence  # unchanged by insilico

    def test_non_missense_has_no_tier(self, sample_engine: sa.Engine) -> None:
        _seed_cancer_variant(sample_engine, revel=0.95, consequence="frameshift_variant")
        panel = load_cancer_panel()
        result = extract_cancer_variants(panel, sample_engine)
        store_cancer_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.gene_symbol == "TP53")
            ).fetchone()
        assert row is not None
        detail = json.loads(row.detail_json)
        assert detail["insilico"] is None
        assert row.clinvar_significance == "Pathogenic"
