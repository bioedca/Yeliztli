"""Tests for gnomAD gene-constraint lookup + badge, and its non-mutating
integration into cancer findings (context only, never auto-upgrades ACMG)."""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend.analysis.cancer import (
    extract_cancer_variants,
    load_cancer_panel,
    store_cancer_findings,
)
from backend.analysis.gene_constraint import (
    is_lof_constrained,
    lookup_gene_constraints,
)
from backend.db.tables import (
    annotated_variants,
    findings,
    gnomad_gene_constraint,
    reference_metadata,
)

# APC is in the cancer panel and is genuinely LoF-constrained (LOEUF ~0.16).
_APC_RSID = "rs137854568"


@pytest.fixture()
def reference_engine() -> sa.Engine:
    eng = sa.create_engine("sqlite://")
    reference_metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(
            sa.insert(gnomad_gene_constraint),
            [
                {"gene_symbol": "APC", "loeuf": 0.16, "pli": 1.0, "mis_z": 3.06},
                {"gene_symbol": "PCSK9", "loeuf": 0.66, "pli": 0.0, "mis_z": 1.10},
            ],
        )
    return eng


class TestThreshold:
    def test_loeuf_below_threshold(self) -> None:
        assert is_lof_constrained(0.16, 0.0) is True

    def test_pli_above_threshold(self) -> None:
        assert is_lof_constrained(0.50, 0.95) is True

    def test_neither(self) -> None:
        assert is_lof_constrained(0.66, 0.0) is False

    def test_both_none(self) -> None:
        assert is_lof_constrained(None, None) is False

    # Bracket the LOEUF_CONSTRAINED_MAX (0.35) cutoff itself (#1220). The cases
    # above only reach 0.16 (below) and 0.50/0.66 (above), leaving the whole
    # [0.16, 0.50) interval — which contains 0.35 — unprobed, so a drifted cutoff
    # (e.g. 0.20) or a `<` → `<=` slip ships green. pli is None here so the PLI
    # branch short-circuits and only the LOEUF comparison decides.
    def test_loeuf_just_below_cutoff_is_constrained(self) -> None:
        # Flips to False the moment the cutoff drifts below 0.34 — kills value drift.
        assert is_lof_constrained(0.34, None) is True

    def test_loeuf_at_cutoff_is_not_constrained(self) -> None:
        # Strict `<`: exactly 0.35 is NOT constrained — flips to True under `<=`.
        assert is_lof_constrained(0.35, None) is False

    def test_loeuf_just_above_cutoff_is_not_constrained(self) -> None:
        assert is_lof_constrained(0.40, None) is False

    # Bracket the PLI_CONSTRAINED_MIN (0.9) cutoff with LOEUF absent so only the
    # pLI comparison decides. The broad cases above hit 0.95 and 0.0, but not the
    # cutoff itself or nearby values that catch threshold drift.
    def test_pli_just_above_cutoff_is_constrained(self) -> None:
        assert is_lof_constrained(None, 0.9001) is True

    def test_pli_at_cutoff_is_not_constrained(self) -> None:
        assert is_lof_constrained(None, 0.9) is False

    def test_pli_just_below_cutoff_is_not_constrained(self) -> None:
        assert is_lof_constrained(None, 0.8999) is False


class TestLookup:
    def test_constrained_gene(self, reference_engine: sa.Engine) -> None:
        info = lookup_gene_constraints(reference_engine, ["APC"])["APC"]
        assert info["lof_constrained"] is True
        assert "LoF-constrained" in info["badge"]
        assert "0.16" in info["badge"]
        assert info["context_only"] is True
        assert "does not change" in info["note"].lower()

    def test_unconstrained_gene(self, reference_engine: sa.Engine) -> None:
        info = lookup_gene_constraints(reference_engine, ["PCSK9"])["PCSK9"]
        assert info["lof_constrained"] is False
        assert info["badge"] is None

    def test_unknown_gene_is_omitted(self, reference_engine: sa.Engine) -> None:
        assert lookup_gene_constraints(reference_engine, ["NOT_A_GENE"]) == {}

    def test_ignores_duplicates_and_falsy_symbols(self, reference_engine: sa.Engine) -> None:
        info = lookup_gene_constraints(reference_engine, ["APC", "APC", "", None])
        assert list(info) == ["APC"]

    def test_empty_input_returns_empty_mapping(self, reference_engine: sa.Engine) -> None:
        assert lookup_gene_constraints(reference_engine, []) == {}


def _seed_apc_variant(sample_engine: sa.Engine) -> None:
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(annotated_variants),
            [
                {
                    "rsid": _APC_RSID,
                    "chrom": "5",
                    "pos": 112175770,
                    "genotype": "CT",
                    "zygosity": "het",
                    "gene_symbol": "APC",
                    "clinvar_significance": "Pathogenic",
                    "clinvar_review_stars": 2,
                    "clinvar_accession": "VCV000000123",
                    "clinvar_conditions": "Familial adenomatous polyposis",
                    "annotation_coverage": 2,
                }
            ],
        )


class TestCancerIntegration:
    def test_constraint_context_attached_without_mutation(
        self, sample_engine: sa.Engine, reference_engine: sa.Engine
    ) -> None:
        _seed_apc_variant(sample_engine)
        panel = load_cancer_panel()
        result = extract_cancer_variants(panel, sample_engine)
        expected_evidence = result.variants[0].evidence_level
        store_cancer_findings(result, sample_engine, reference_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.gene_symbol == "APC")
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert detail["gene_constraint"]["lof_constrained"] is True
        # Context only — classification + evidence untouched.
        assert row.clinvar_significance == "Pathogenic"
        assert row.evidence_level == expected_evidence

    def test_no_constraint_key_without_reference_engine(self, sample_engine: sa.Engine) -> None:
        _seed_apc_variant(sample_engine)
        panel = load_cancer_panel()
        result = extract_cancer_variants(panel, sample_engine)
        store_cancer_findings(result, sample_engine)  # no reference_engine

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.gene_symbol == "APC")
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert "gene_constraint" not in detail  # back-compatible omission
