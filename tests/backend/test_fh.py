"""Tests for SW-B6: familial hypercholesterolemia (FH) view.

Covers:
  - Monogenic FH detection (LDLR/APOB/PCSK9 P/LP from stored findings).
  - APOB rs5742904 (FDB) genotype resolution + pathogenic classification.
  - LDL-C PRS via the bridge (graceful when score DB absent).
  - store_fh_findings persists the LDL PRS (low-coverage) + APOB FDB finding.
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.analysis.fh import (
    APOB_FDB_RSID,
    assess_fh,
    detect_apob_fdb,
    detect_fh_monogenic,
    store_fh_findings,
)
from backend.annotation.pgs_catalog import (
    create_pgs_tables,
    pgs_score_metadata,
    pgs_score_weights,
)
from backend.db.tables import annotated_variants, findings


def _pgs_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_pgs_tables(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(pgs_score_metadata),
            [
                {
                    "pgs_id": "PGS000688",
                    "pgs_name": "LDL",
                    "trait_reported": "LDL",
                    "trait_efo": "EFO_0004611",
                    "genome_build": "GRCh37",
                    "variants_number": 3,
                    "weight_type": "beta",
                    "license": "CC-BY-4.0",
                    "license_bundle_ok": 1,
                    "citation": "c",
                    "pgp_id": None,
                }
            ],
        )
        conn.execute(
            sa.insert(pgs_score_weights),
            [
                {
                    "pgs_id": "PGS000688",
                    "rsid": f"rsL{i}",
                    "chrom": "1",
                    "pos": 500 + i,
                    "effect_allele": "A",
                    "other_allele": "G",
                    "effect_weight": 0.1,
                }
                for i in range(3)
            ],
        )
    return engine


def _insert_monogenic(engine: sa.Engine, gene: str, *, sig: str, zyg: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.insert(findings),
            [
                {
                    "module": "cardiovascular",
                    "category": "monogenic_variant",
                    "gene_symbol": gene,
                    "clinvar_significance": sig,
                    "zygosity": zyg,
                    "evidence_level": 4,
                    "finding_text": f"{gene} P/LP",
                }
            ],
        )


def _insert_apob_fdb(
    engine: sa.Engine, *, genotype: str, sig: str | None, zygosity: str = "het"
) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.insert(annotated_variants),
            [
                {
                    "rsid": APOB_FDB_RSID,
                    "chrom": "2",
                    "pos": 21229160,
                    "genotype": genotype,
                    "zygosity": zygosity,
                    "clinvar_significance": sig,
                    "annotation_coverage": 0,
                }
            ],
        )


class TestMonogenicDetection:
    def test_detects_fh_genes(self, sample_engine: sa.Engine) -> None:
        _insert_monogenic(sample_engine, "LDLR", sig="Pathogenic", zyg="het")
        _insert_monogenic(sample_engine, "MYH7", sig="Pathogenic", zyg="het")  # not an FH gene
        mono = detect_fh_monogenic(sample_engine)
        assert [m.gene for m in mono] == ["LDLR"]

    def test_excludes_non_carrier_zygosity(self, sample_engine: sa.Engine) -> None:
        _insert_monogenic(sample_engine, "APOB", sig="Pathogenic", zyg="hom_ref")
        assert detect_fh_monogenic(sample_engine) == []


class TestApobFdb:
    def test_pathogenic_carrier(self, sample_engine: sa.Engine) -> None:
        _insert_apob_fdb(sample_engine, genotype="CT", sig="Pathogenic", zygosity="het")
        fdb = detect_apob_fdb(sample_engine)
        assert fdb.present is True
        assert fdb.is_carrier is True
        assert fdb.genotype == "CT"
        assert fdb.gene == "APOB"

    def test_typed_non_carrier_excluded(self, sample_engine: sa.Engine) -> None:
        # Negative control: variant typed but homozygous-reference (non-carrier),
        # even when ClinVar-classified Pathogenic, must not be flagged as a carrier.
        _insert_apob_fdb(sample_engine, genotype="CC", sig="Pathogenic", zygosity="hom_ref")
        fdb = detect_apob_fdb(sample_engine)
        assert fdb.present is True  # site is typed
        assert fdb.is_carrier is False  # but not a carrier

    def test_absent_when_not_typed(self, sample_engine: sa.Engine) -> None:
        fdb = detect_apob_fdb(sample_engine)
        assert fdb.present is False
        assert fdb.is_carrier is False
        assert fdb.genotype is None


class TestAssessAndStore:
    def test_assess_without_score_db(self, sample_engine: sa.Engine) -> None:
        _insert_monogenic(sample_engine, "LDLR", sig="Pathogenic", zyg="het")
        a = assess_fh(sample_engine, None, inferred_ancestry="EUR")
        assert a.has_monogenic is True
        assert a.ldl_prs is None  # no score DB → no PRS

    def test_store_persists_prs_and_fdb(self, sample_engine: sa.Engine) -> None:
        _insert_apob_fdb(sample_engine, genotype="CT", sig="Pathogenic")
        # Cover 1 of 3 LDL score variants (low coverage → stored for transparency).
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rsL0",
                        "chrom": "1",
                        "pos": 500,
                        "genotype": "AA",
                        "gnomad_af_global": 0.2,
                        "annotation_coverage": 0,
                    }
                ],
            )
        a = assess_fh(sample_engine, _pgs_engine(), inferred_ancestry="EUR")
        n = store_fh_findings(a, sample_engine)
        assert n == 2  # LDL PRS + APOB FDB
        with sample_engine.connect() as conn:
            prs = conn.execute(
                sa.select(findings).where(findings.c.module == "fh", findings.c.category == "prs")
            ).fetchall()
            fdb = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "fh", findings.c.category == "fdb_variant"
                )
            ).fetchone()
        assert len(prs) == 1
        assert fdb is not None and fdb.gene_symbol == "APOB"

    def test_non_carrier_fdb_not_stored(self, sample_engine: sa.Engine) -> None:
        _insert_apob_fdb(sample_engine, genotype="CC", sig="Pathogenic", zygosity="hom_ref")
        a = assess_fh(sample_engine, None, inferred_ancestry="EUR")
        store_fh_findings(a, sample_engine)
        with sample_engine.connect() as conn:
            fdb = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "fh", findings.c.category == "fdb_variant"
                )
            ).fetchall()
        assert fdb == []  # non-carrier → no FDB finding

    def test_rerun_replaces_fdb(self, sample_engine: sa.Engine) -> None:
        _insert_apob_fdb(sample_engine, genotype="CT", sig="Pathogenic")
        a = assess_fh(sample_engine, None, inferred_ancestry="EUR")
        store_fh_findings(a, sample_engine)
        store_fh_findings(a, sample_engine)
        with sample_engine.connect() as conn:
            fdb = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "fh", findings.c.category == "fdb_variant"
                )
            ).fetchall()
        assert len(fdb) == 1
