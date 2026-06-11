"""Tests for SW-B3: per-PGS provenance + monogenic-exclusion on PRS findings.

Covers:
  - annotate_monogenic_exclusion: no-op when no monogenic_genes; base
    disclosure when genes set but no carrier; escalated note + carrier list
    when the sample carries a monogenic finding.
  - APOE is named in the disclosure but NEVER carrier-checked (preserves the
    APOE non-disclosure gate).
  - Per-PGS provenance fields flow weight set → result → stored detail_json.
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from backend.analysis.prs import (
    PRSResult,
    PRSSNPWeight,
    PRSWeightSet,
    annotate_monogenic_exclusion,
    run_prs,
    store_prs_findings,
)
from backend.db.tables import annotated_variants, findings


def _insert_monogenic(engine: sa.Engine, *, module: str, gene: str, zygosity: str) -> None:
    """Insert a monogenic_variant finding row for carrier-overlap tests."""
    with engine.begin() as conn:
        conn.execute(
            sa.insert(findings),
            [
                {
                    "module": module,
                    "category": "monogenic_variant",
                    "gene_symbol": gene,
                    "zygosity": zygosity,
                    "finding_text": f"{gene} pathogenic variant",
                    "evidence_level": 4,
                }
            ],
        )


class TestAnnotateMonogenicExclusion:
    """annotate_monogenic_exclusion disclosure + carrier cross-reference."""

    def test_no_monogenic_genes_is_noop(self, sample_engine: sa.Engine) -> None:
        result = PRSResult(
            weight_set_name="x",
            trait="breast_cancer",
            module="cancer",
            source_ancestry="EUR",
            source_study="s",
            source_pmid="1",
            sample_size=1,
            raw_score=0.0,
        )
        out = annotate_monogenic_exclusion(result, sample_engine)
        assert out.monogenic_note is None
        assert out.monogenic_carrier_genes == []

    def test_base_disclosure_without_carrier(self, sample_engine: sa.Engine) -> None:
        result = PRSResult(
            weight_set_name="x",
            trait="breast_cancer",
            module="cancer",
            source_ancestry="EUR",
            source_study="s",
            source_pmid="1",
            sample_size=1,
            raw_score=0.0,
            monogenic_genes=["BRCA1", "BRCA2"],
        )
        out = annotate_monogenic_exclusion(result, sample_engine)
        assert out.monogenic_carrier_genes == []
        assert out.monogenic_note is not None
        assert "common-variant burden only" in out.monogenic_note
        assert "BRCA1, BRCA2" in out.monogenic_note
        # No carrier → no "You carry" escalation.
        assert "You carry" not in out.monogenic_note

    def test_escalated_note_when_carrier(self, sample_engine: sa.Engine) -> None:
        _insert_monogenic(sample_engine, module="cancer", gene="BRCA2", zygosity="het")
        result = PRSResult(
            weight_set_name="x",
            trait="breast_cancer",
            module="cancer",
            source_ancestry="EUR",
            source_study="s",
            source_pmid="1",
            sample_size=1,
            raw_score=0.0,
            monogenic_genes=["BRCA1", "BRCA2", "PALB2"],
        )
        out = annotate_monogenic_exclusion(result, sample_engine)
        assert out.monogenic_carrier_genes == ["BRCA2"]
        assert "You carry a reportable monogenic finding in BRCA2" in out.monogenic_note
        assert "dominant result" in out.monogenic_note

    def test_homref_carrier_excluded(self, sample_engine: sa.Engine) -> None:
        # A non-carrier zygosity must not count as a carrier overlap.
        _insert_monogenic(sample_engine, module="cancer", gene="BRCA1", zygosity="hom_ref")
        result = PRSResult(
            weight_set_name="x",
            trait="breast_cancer",
            module="cancer",
            source_ancestry="EUR",
            source_study="s",
            source_pmid="1",
            sample_size=1,
            raw_score=0.0,
            monogenic_genes=["BRCA1"],
        )
        out = annotate_monogenic_exclusion(result, sample_engine)
        assert out.monogenic_carrier_genes == []

    def test_apoe_named_but_never_carrier_checked(self, sample_engine: sa.Engine) -> None:
        # Even if an APOE monogenic_variant row exists, APOE is gated: it must be
        # named in the disclosure but never appear as a carrier (gate preserved).
        _insert_monogenic(sample_engine, module="apoe", gene="APOE", zygosity="het")
        result = PRSResult(
            weight_set_name="x",
            trait="alzheimers",
            module="neuro",
            source_ancestry="EUR",
            source_study="s",
            source_pmid="1",
            sample_size=1,
            raw_score=0.0,
            monogenic_genes=["APOE"],
        )
        out = annotate_monogenic_exclusion(result, sample_engine)
        assert out.monogenic_carrier_genes == []
        assert "APOE" in out.monogenic_note
        assert "You carry" not in out.monogenic_note


class TestProvenancePassthrough:
    """Per-PGS provenance flows weight set → result → stored detail_json."""

    def _weight_set(self) -> PRSWeightSet:
        return PRSWeightSet(
            name="T2D (PGS000713)",
            trait="type_2_diabetes",
            module="metabolic",
            source_ancestry="EUR",
            source_study="Sinnott-Armstrong 2021",
            source_pmid="33462484",
            sample_size=400000,
            weights=[
                PRSSNPWeight(rsid="rs2001", effect_allele="A", weight=0.1, other_allele="G"),
                PRSSNPWeight(rsid="rs2002", effect_allele="T", weight=0.2, other_allele="C"),
            ],
            reference_mean=0.0,
            reference_std=1.0,
            calibrated=False,
            pgs_id="PGS000713",
            pgs_license="CC-BY-4.0",
            development_method="snpnet",
            genome_build="GRCh37",
            variants_number=183830,
            source_url="https://www.pgscatalog.org/score/PGS000713/",
            monogenic_genes=[],
        )

    def test_run_prs_carries_provenance(self, sample_engine: sa.Engine) -> None:
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rs2001",
                        "chrom": "1",
                        "pos": 1,
                        "genotype": "AA",
                        "annotation_coverage": 0,
                    },
                    {
                        "rsid": "rs2002",
                        "chrom": "2",
                        "pos": 2,
                        "genotype": "TC",
                        "annotation_coverage": 0,
                    },
                ],
            )
        result = run_prs(self._weight_set(), sample_engine)
        assert result.pgs_id == "PGS000713"
        assert result.pgs_license == "CC-BY-4.0"
        assert result.development_method == "snpnet"
        assert result.genome_build == "GRCh37"
        assert result.variants_number == 183830
        assert result.source_url.endswith("PGS000713/")

    def test_store_prs_findings_persists_provenance(self, sample_engine: sa.Engine) -> None:
        result = run_prs(self._weight_set(), sample_engine)
        # Force sufficiency so the finding is stored even with the tiny weight set.
        result.coverage_fraction = 1.0
        result.snps_used = 2
        result.snps_total = 2
        n = store_prs_findings([result], sample_engine, module="metabolic")
        assert n == 1
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings.c.detail_json).where(findings.c.category == "prs")
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert detail["pgs_id"] == "PGS000713"
        assert detail["pgs_license"] == "CC-BY-4.0"
        assert detail["development_method"] == "snpnet"
        assert detail["variants_number"] == 183830
        assert "monogenic_genes" in detail
