"""Tests for SW-B5: metabolic disease PRS (T2D & obesity) + anchor SNPs.

Covers:
  - Anchor-SNP scoring (TCF7L2 / FTO / MC4R) dosage resolution.
  - run_metabolic_prs with and without the score DB (graceful degradation).
  - store_metabolic_findings: PRS findings stored even below the coverage
    threshold (transparency), anchor findings stored + replaced on re-run.
  - get_pgs_scores_engine returns None when the DB is absent.
  - store_prs_findings(store_insufficient=True) surfaces low-coverage scores.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from backend.analysis.metabolic_prs import (
    run_metabolic_prs,
    score_anchor_snps,
    store_metabolic_findings,
)
from backend.analysis.pgs_bridge import get_pgs_scores_engine
from backend.analysis.prs import PRSResult, store_prs_findings
from backend.annotation.pgs_catalog import (
    create_pgs_tables,
    pgs_score_metadata,
    pgs_score_weights,
)
from backend.db.tables import annotated_variants, findings


def _pgs_engine() -> sa.Engine:
    """Fixture pgs_scores.db with the registry's T2D + BMI scores (4 weights each)."""
    engine = sa.create_engine("sqlite://")
    create_pgs_tables(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(pgs_score_metadata),
            [
                {
                    "pgs_id": "PGS000713",
                    "pgs_name": "T2D",
                    "trait_reported": "T2D",
                    "trait_efo": "MONDO_0005148",
                    "genome_build": "GRCh37",
                    "variants_number": 4,
                    "weight_type": "beta",
                    "license": "CC-BY-4.0",
                    "license_bundle_ok": 1,
                    "citation": "c",
                    "pgp_id": None,
                },
                {
                    "pgs_id": "PGS005198",
                    "pgs_name": "BMI",
                    "trait_reported": "BMI",
                    "trait_efo": "EFO_0004340",
                    "genome_build": "GRCh37",
                    "variants_number": 4,
                    "weight_type": "beta",
                    "license": "CC-BY-4.0",
                    "license_bundle_ok": 1,
                    "citation": "c",
                    "pgp_id": None,
                },
            ],
        )
        rows = []
        for i in range(4):
            rows.append(
                {
                    "pgs_id": "PGS000713",
                    "rsid": f"rsT{i}",
                    "chrom": "1",
                    "pos": 1000 + i,
                    "effect_allele": "A",
                    "other_allele": "G",
                    "effect_weight": 0.1,
                }
            )
            rows.append(
                {
                    "pgs_id": "PGS005198",
                    "rsid": None,
                    "chrom": "2",
                    "pos": 2000 + i,
                    "effect_allele": "T",
                    "other_allele": "C",
                    "effect_weight": 0.1,
                }
            )
        conn.execute(sa.insert(pgs_score_weights), rows)
    return engine


def _seed_sample(engine: sa.Engine) -> None:
    """Anchor SNPs + a couple of score variants (partial coverage)."""
    with engine.begin() as conn:
        conn.execute(
            sa.insert(annotated_variants),
            [
                # Anchors
                {
                    "rsid": "rs7903146",
                    "chrom": "10",
                    "pos": 114758349,
                    "genotype": "TT",
                    "gnomad_af_global": 0.3,
                    "annotation_coverage": 0,
                },
                {
                    "rsid": "rs9939609",
                    "chrom": "16",
                    "pos": 53786615,
                    "genotype": "AT",
                    "gnomad_af_global": 0.4,
                    "annotation_coverage": 0,
                },
                # rs17782313 (MC4R) intentionally absent → not typed
                # Partial score coverage: 1 of 4 T2D, 1 of 4 BMI
                {
                    "rsid": "rsT0",
                    "chrom": "1",
                    "pos": 1000,
                    "genotype": "AA",
                    "gnomad_af_global": 0.2,
                    "annotation_coverage": 0,
                },
                {
                    "rsid": "rsBMI",
                    "chrom": "2",
                    "pos": 2000,
                    "genotype": "TC",
                    "gnomad_af_global": 0.2,
                    "annotation_coverage": 0,
                },
            ],
        )


class TestAnchorSnps:
    def test_typed_anchor_dosage(self, sample_engine: sa.Engine) -> None:
        _seed_sample(sample_engine)
        anchors = score_anchor_snps(sample_engine, "type_2_diabetes")
        tcf = next(a for a in anchors if a.gene == "TCF7L2")
        assert tcf.genotype == "TT"
        assert tcf.dosage == 2  # two T effect alleles

    def test_untyped_anchor_reports_none(self, sample_engine: sa.Engine) -> None:
        _seed_sample(sample_engine)
        anchors = score_anchor_snps(sample_engine, "body_mass_index")
        mc4r = next(a for a in anchors if a.gene == "MC4R")
        assert mc4r.genotype is None


class TestRunMetabolic:
    def test_with_score_db(self, sample_engine: sa.Engine) -> None:
        _seed_sample(sample_engine)
        result = run_metabolic_prs(sample_engine, _pgs_engine(), inferred_ancestry="EUR")
        traits = {r.trait for r in result.prs_results}
        assert traits == {"type_2_diabetes", "body_mass_index"}
        # 4 anchors defined (1 T2D + 2 BMI); all resolved (genotype may be None).
        assert len(result.anchors) == 3
        # Partial coverage → below threshold.
        t2d = next(r for r in result.prs_results if r.trait == "type_2_diabetes")
        assert t2d.snps_total == 4 and t2d.snps_used == 1

    def test_without_score_db(self, sample_engine: sa.Engine) -> None:
        _seed_sample(sample_engine)
        result = run_metabolic_prs(sample_engine, None, inferred_ancestry="EUR")
        assert result.prs_results == []  # no PRS without the DB
        assert len(result.anchors) == 3  # anchors still resolved


class TestStoreMetabolic:
    def test_stores_low_coverage_prs_and_anchors(self, sample_engine: sa.Engine) -> None:
        _seed_sample(sample_engine)
        result = run_metabolic_prs(sample_engine, _pgs_engine(), inferred_ancestry="EUR")
        n = store_metabolic_findings(result, sample_engine)
        assert n > 0
        with sample_engine.connect() as conn:
            prs = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "metabolic", findings.c.category == "prs"
                )
            ).fetchall()
            anchors = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "metabolic", findings.c.category == "anchor_snp"
                )
            ).fetchall()
        # Both T2D + BMI PRS stored despite low coverage (transparency).
        assert len(prs) == 2
        assert all("coverage too low" in r.finding_text for r in prs)
        # 2 anchors typed (TCF7L2, FTO); MC4R untyped → not stored.
        assert {r.gene_symbol for r in anchors} == {"TCF7L2", "FTO"}

    def test_rerun_replaces_anchors(self, sample_engine: sa.Engine) -> None:
        _seed_sample(sample_engine)
        eng = _pgs_engine()
        store_metabolic_findings(run_metabolic_prs(sample_engine, eng, "EUR"), sample_engine)
        store_metabolic_findings(run_metabolic_prs(sample_engine, eng, "EUR"), sample_engine)
        with sample_engine.connect() as conn:
            anchors = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "metabolic", findings.c.category == "anchor_snp"
                )
            ).fetchall()
        assert len(anchors) == 2  # not duplicated


class TestScoreDbAbsence:
    def test_engine_none_when_absent(self, tmp_path: Path) -> None:
        assert get_pgs_scores_engine(tmp_path) is None


class TestStoreInsufficientFlag:
    def test_store_insufficient_surfaces_low_coverage(self, sample_engine: sa.Engine) -> None:
        r = PRSResult(
            weight_set_name="X",
            trait="t",
            module="metabolic",
            source_ancestry="EUR",
            source_study="s",
            source_pmid="1",
            sample_size=1,
            raw_score=0.0,
            snps_used=1,
            snps_total=10,
            coverage_fraction=0.1,
            calibrated=False,
        )
        # Default: skipped.
        assert store_prs_findings([r], sample_engine, module="metabolic") == 0
        # With store_insufficient: emitted.
        assert (
            store_prs_findings([r], sample_engine, module="metabolic", store_insufficient=True)
            == 1
        )
