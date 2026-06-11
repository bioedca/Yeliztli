"""Tests for the annotation engine orchestrator (P2-04, P2-09, P2-12, P2-13, P2-15).

Covers:
- T2-04: Annotation engine processes 1000 variants end-to-end, all fields
  populated in annotated_variants
- P2-09: gnomAD annotation lookup integrated into engine — rsid primary,
  position-based fallback, correct rare/ultra-rare thresholds
- P2-12: dbNSFP annotation integrated into engine — rsid primary,
  position-based fallback, all 14 score fields, deleterious_count
- P2-15: Gene-phenotype annotation via MONDO/HPO lookup, joined by gene symbol
- Concurrent lookup orchestration across VEP, ClinVar, gnomAD, dbNSFP
- Bitmask computation (annotation_coverage)
- Crash recovery (delete partial, re-run)
- Graceful degradation when sources are unavailable
- Progress callback
- Merge logic across sources
"""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from backend.annotation.alphamissense import create_alphamissense_table
from backend.annotation.dbnsfp import (
    DbNSFPAnnotation,
    create_dbnsfp_tables,
    load_dbnsfp_from_csv,
    lookup_dbnsfp_by_rsids,
)
from backend.annotation.engine import (
    ALPHAMISSENSE_BIT,
    CLINVAR_BIT,
    DBNSFP_BIT,
    GENE_PHENOTYPE_BIT,
    GNOMAD_BIT,
    VEP_BIT,
    AnnotationEngineResult,
    _annot_to_dict,
    _bulk_upsert,
    _dbnsfp_annot_to_dict,
    _delete_all_annotations,
    _lookup_alphamissense,
    _lookup_clinvar,
    _lookup_dbnsfp,
    _lookup_gene_phenotype,
    _lookup_gnomad,
    _lookup_vep,
    _merge_annotations,
    run_annotation,
)
from backend.annotation.gnomad import (
    RARE_AF_THRESHOLD,
    ULTRA_RARE_AF_THRESHOLD,
    GnomADAnnotation,
    create_gnomad_tables,
    lookup_gnomad_by_rsids,
)
from backend.annotation.mondo_hpo import load_mondo_hpo_from_csv
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    annotation_state,
    clinvar_variants,
    database_versions,
    raw_variants,
    reference_metadata,
    sample_metadata_table,
    update_history,
)

# ── Fixtures ────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
VEP_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "vep_seed.csv"
GNOMAD_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "gnomad_seed.csv"
DBNSFP_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "dbnsfp_seed.csv"
GENE_PHENOTYPE_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "gene_phenotype_seed.csv"

SEED_CLINVAR = [
    {
        "rsid": "rs429358",
        "chrom": "19",
        "pos": 44908684,
        "ref": "T",
        "alt": "C",
        "significance": "risk_factor",
        "review_stars": 3,
        "accession": "VCV000017864",
        "conditions": "Alzheimer disease",
        "gene_symbol": "APOE",
        "variation_id": 17864,
    },
    {
        "rsid": "rs1801133",
        "chrom": "1",
        "pos": 11856378,
        "ref": "G",
        "alt": "A",
        "significance": "drug_response",
        "review_stars": 2,
        "accession": "VCV000003520",
        "conditions": "Homocysteinemia",
        "gene_symbol": "MTHFR",
        "variation_id": 3520,
    },
    {
        "rsid": "rs80357906",
        "chrom": "17",
        "pos": 43091983,
        "ref": "CTC",
        "alt": "C",
        "significance": "Pathogenic",
        "review_stars": 3,
        "accession": "VCV000017661",
        "conditions": "Hereditary breast and ovarian cancer syndrome",
        "gene_symbol": "BRCA1",
        "variation_id": 17661,
    },
]

SEED_RAW_VARIANTS = [
    {"rsid": "rs429358", "chrom": "19", "pos": 44908684, "genotype": "TC"},
    {"rsid": "rs7412", "chrom": "19", "pos": 44908822, "genotype": "CC"},
    {"rsid": "rs1801133", "chrom": "1", "pos": 11856378, "genotype": "AG"},
    {"rsid": "rs4680", "chrom": "22", "pos": 19963748, "genotype": "AG"},
    {"rsid": "rs80357906", "chrom": "17", "pos": 43091983, "genotype": "CT"},
    {"rsid": "rs12913832", "chrom": "15", "pos": 28365618, "genotype": "GG"},
    {"rsid": "rs7903146", "chrom": "10", "pos": 114758349, "genotype": "CT"},
    {"rsid": "rs_nomatch", "chrom": "99", "pos": 1, "genotype": "AA"},
]


@pytest.fixture
def sample_engine() -> sa.Engine:
    """In-memory sample engine with tables created."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_sample_tables(engine)
    return engine


@pytest.fixture
def sample_with_variants(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine pre-loaded with known raw variants."""
    with sample_engine.begin() as conn:
        conn.execute(raw_variants.insert(), SEED_RAW_VARIANTS)
    return sample_engine


@pytest.fixture
def vep_engine_inmemory() -> sa.Engine:
    """In-memory VEP bundle loaded from seed CSV."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE vep_annotations ("
                "  rsid TEXT, chrom TEXT, pos INTEGER,"
                "  ref TEXT, alt TEXT, gene_symbol TEXT,"
                "  transcript_id TEXT, consequence TEXT,"
                "  hgvs_coding TEXT, hgvs_protein TEXT,"
                "  strand TEXT, exon_number INTEGER,"
                "  intron_number INTEGER, mane_select INTEGER"
                ")"
            )
        )
        conn.execute(sa.text("CREATE INDEX idx_vep_rsid ON vep_annotations(rsid)"))
        with open(VEP_SEED_CSV, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                conn.execute(
                    sa.text(
                        "INSERT INTO vep_annotations "
                        "(rsid, chrom, pos, ref, alt, gene_symbol, "
                        "transcript_id, consequence, hgvs_coding, "
                        "hgvs_protein, strand, exon_number, "
                        "intron_number, mane_select) "
                        "VALUES (:rsid, :chrom, :pos, :ref, :alt, "
                        ":gene_symbol, :transcript_id, :consequence, "
                        ":hgvs_coding, :hgvs_protein, :strand, "
                        ":exon_number, :intron_number, :mane_select)"
                    ),
                    {
                        "rsid": row["rsid"],
                        "chrom": row["chrom"],
                        "pos": int(row["pos"]),
                        "ref": row["ref"],
                        "alt": row["alt"],
                        "gene_symbol": row["gene_symbol"],
                        "transcript_id": row["transcript_id"],
                        "consequence": row["consequence"],
                        "hgvs_coding": row["hgvs_coding"] or None,
                        "hgvs_protein": row["hgvs_protein"] or None,
                        "strand": row["strand"],
                        "exon_number": (int(row["exon_number"]) if row["exon_number"] else None),
                        "intron_number": (
                            int(row["intron_number"]) if row["intron_number"] else None
                        ),
                        "mane_select": int(row["mane_select"]),
                    },
                )
    return engine


@pytest.fixture
def reference_engine() -> sa.Engine:
    """In-memory reference engine with ClinVar and gene-phenotype data."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    reference_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(clinvar_variants.insert(), SEED_CLINVAR)
    # Load gene-phenotype seed data
    load_mondo_hpo_from_csv(GENE_PHENOTYPE_SEED_CSV, engine)
    return engine


@pytest.fixture
def gnomad_engine() -> sa.Engine:
    """In-memory gnomAD engine loaded from seed CSV with proper indexes."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Use the module's create_gnomad_tables for proper table + indexes
    create_gnomad_tables(engine)
    with engine.begin() as conn:
        with open(GNOMAD_SEED_CSV, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                conn.execute(
                    sa.text(
                        "INSERT INTO gnomad_af VALUES "
                        "(:rsid, :chrom, :pos, :ref, :alt, :af_global, "
                        ":af_afr, :af_amr, :af_eas, :af_eur, :af_fin, "
                        ":af_sas, :homozygous_count)"
                    ),
                    {
                        "rsid": row["rsid"],
                        "chrom": row["chrom"],
                        "pos": int(row["pos"]),
                        "ref": row["ref"],
                        "alt": row["alt"],
                        "af_global": float(row["af_global"]),
                        "af_afr": float(row["af_afr"]),
                        "af_amr": float(row["af_amr"]),
                        "af_eas": float(row["af_eas"]),
                        "af_eur": float(row["af_eur"]),
                        "af_fin": float(row["af_fin"]),
                        "af_sas": float(row["af_sas"]),
                        "homozygous_count": int(row["homozygous_count"]),
                    },
                )
    return engine


@pytest.fixture
def dbnsfp_engine() -> sa.Engine:
    """In-memory dbNSFP engine loaded from seed CSV using dbnsfp.py functions."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_dbnsfp_tables(engine)
    load_dbnsfp_from_csv(DBNSFP_SEED_CSV, engine, clear_existing=False)
    return engine


@pytest.fixture
def alphamissense_engine() -> sa.Engine:
    """In-memory AlphaMissense DB with one missense prediction."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_alphamissense_table(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO alphamissense_scores "
                "(chrom, pos, ref, alt, am_pathogenicity, am_class) "
                "VALUES ('1', 11856378, 'G', 'A', 0.91, 'likely_pathogenic')"
            )
        )
    return engine


@pytest.fixture
def mock_registry(
    reference_engine: sa.Engine,
    vep_engine_inmemory: sa.Engine,
    gnomad_engine: sa.Engine,
    dbnsfp_engine: sa.Engine,
    alphamissense_engine: sa.Engine,
) -> MagicMock:
    """Mock DBRegistry with all annotation source engines."""
    registry = MagicMock()
    registry.reference_engine = reference_engine
    type(registry).vep_engine = property(lambda self: vep_engine_inmemory)
    type(registry).gnomad_engine = property(lambda self: gnomad_engine)
    type(registry).dbnsfp_engine = property(lambda self: dbnsfp_engine)
    registry.alphamissense_engine = alphamissense_engine
    return registry


# ═══════════════════════════════════════════════════════════════════════
# AnnotationEngineResult
# ═══════════════════════════════════════════════════════════════════════


class TestAnnotationEngineResult:
    def test_defaults(self) -> None:
        r = AnnotationEngineResult()
        assert r.total_variants == 0
        assert r.total_matched == 0
        assert r.errors == []
        assert r.alphamissense_matched == 0
        # §5.6 coverage telemetry — empty by default; only populated by run_annotation.
        assert r.coverage_stats == {}

    def test_total_matched_equals_rows_written(self) -> None:
        r = AnnotationEngineResult(rows_written=42)
        assert r.total_matched == 42


# ═══════════════════════════════════════════════════════════════════════
# Individual source lookups
# ═══════════════════════════════════════════════════════════════════════


class TestLookupVep:
    def test_returns_vep_fields(self, vep_engine_inmemory: sa.Engine) -> None:
        result = _lookup_vep(["rs429358"], {}, vep_engine_inmemory)
        assert "rs429358" in result
        assert result["rs429358"]["gene_symbol"] == "APOE"
        assert result["rs429358"]["consequence"] == "missense_variant"

    def test_empty_rsids(self, vep_engine_inmemory: sa.Engine) -> None:
        result = _lookup_vep([], {}, vep_engine_inmemory)
        assert len(result) == 0


class TestLookupClinvar:
    def test_returns_clinvar_fields(self, reference_engine: sa.Engine) -> None:
        result = _lookup_clinvar(["rs429358"], {}, reference_engine)
        assert "rs429358" in result
        assert result["rs429358"]["clinvar_significance"] == "risk_factor"
        assert result["rs429358"]["clinvar_review_stars"] == 3


class TestLookupGnomad:
    def test_returns_gnomad_fields(self, gnomad_engine: sa.Engine) -> None:
        result = _lookup_gnomad(["rs429358"], {}, gnomad_engine)
        assert "rs429358" in result
        data = result["rs429358"]
        assert data["gnomad_af_global"] == pytest.approx(0.1387)
        assert isinstance(data["rare_flag"], bool)
        assert data["rare_flag"] is False  # 0.1387 > 0.01

    def test_rare_flag(self, gnomad_engine: sa.Engine) -> None:
        # Insert a rare variant
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af VALUES "
                    "('rs_rare', '1', 1, 'A', 'G', 0.005, "
                    "0.003, 0.004, 0.006, 0.005, 0.002, 0.007, 5)"
                )
            )
        result = _lookup_gnomad(["rs_rare"], {}, gnomad_engine)
        assert result["rs_rare"]["rare_flag"] is True
        assert result["rs_rare"]["ultra_rare_flag"] is False

    def test_ultra_rare_flag(self, gnomad_engine: sa.Engine) -> None:
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af VALUES "
                    "('rs_ultrarare', '1', 2, 'A', 'G', 0.00005, "
                    "0.00003, 0.00004, 0.00006, 0.00005, 0.00002, 0.00007, 1)"
                )
            )
        result = _lookup_gnomad(["rs_ultrarare"], {}, gnomad_engine)
        assert result["rs_ultrarare"]["rare_flag"] is True
        assert result["rs_ultrarare"]["ultra_rare_flag"] is True

    def test_empty_rsids(self, gnomad_engine: sa.Engine) -> None:
        result = _lookup_gnomad([], {}, gnomad_engine)
        assert len(result) == 0


class TestLookupDbnsfp:
    def test_returns_dbnsfp_fields(self, dbnsfp_engine: sa.Engine) -> None:
        result = _lookup_dbnsfp(["rs429358"], {}, dbnsfp_engine)
        assert "rs429358" in result
        data = result["rs429358"]
        assert data["cadd_phred"] == pytest.approx(28.3)
        assert data["sift_pred"] == "D"
        assert data["polyphen2_hsvar_pred"] == "D"

    def test_empty_rsids(self, dbnsfp_engine: sa.Engine) -> None:
        result = _lookup_dbnsfp([], {}, dbnsfp_engine)
        assert len(result) == 0


class TestLookupAlphaMissense:
    def test_returns_context_for_missense_position(
        self,
        sample_with_variants: sa.Engine,
        vep_engine_inmemory: sa.Engine,
        alphamissense_engine: sa.Engine,
    ) -> None:
        with sample_with_variants.connect() as conn:
            raw_rows = conn.execute(sa.select(raw_variants)).fetchall()
        raw_by_rsid = {r.rsid: r for r in raw_rows}
        vep = _lookup_vep(["rs1801133"], raw_by_rsid, vep_engine_inmemory)

        result = _lookup_alphamissense(
            ["rs1801133"],
            raw_by_rsid,
            vep,
            {"rs1801133": {"ref": "G", "alt": "A"}},
            alphamissense_engine,
        )

        assert result["rs1801133"]["alphamissense_pathogenicity"] == pytest.approx(0.91)
        assert result["rs1801133"]["alphamissense_class"] == "likely_pathogenic"

    def test_skips_non_missense(
        self,
        sample_with_variants: sa.Engine,
        vep_engine_inmemory: sa.Engine,
        alphamissense_engine: sa.Engine,
    ) -> None:
        with sample_with_variants.connect() as conn:
            raw_rows = conn.execute(sa.select(raw_variants)).fetchall()
        raw_by_rsid = {r.rsid: r for r in raw_rows}
        vep = {
            "rs1801133": {"consequence": "synonymous_variant", "_vep_ref": "G", "_vep_alt": "A"}
        }

        assert (
            _lookup_alphamissense(
                ["rs1801133"],
                raw_by_rsid,
                vep,
                {},
                alphamissense_engine,
            )
            == {}
        )


# ═══════════════════════════════════════════════════════════════════════
# Merge + bitmask
# ═══════════════════════════════════════════════════════════════════════


class TestMergeAnnotations:
    def test_merge_all_sources(self) -> None:
        """Merging data from all 4 sources produces correct bitmask."""
        # Create a fake raw row
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, genotype TEXT)")
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs1', '1', 100, 'AG')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        vep = {"rs1": {"gene_symbol": "GENE1", "consequence": "missense_variant"}}
        clinvar = {"rs1": {"clinvar_significance": "Pathogenic"}}
        gnomad = {"rs1": {"gnomad_af_global": 0.01}}
        dbnsfp = {"rs1": {"cadd_phred": 25.0}}

        merged = _merge_annotations([row], vep, clinvar, gnomad, dbnsfp)
        assert len(merged) == 1
        assert merged[0]["annotation_coverage"] == VEP_BIT | CLINVAR_BIT | GNOMAD_BIT | DBNSFP_BIT
        assert merged[0]["gene_symbol"] == "GENE1"
        assert merged[0]["clinvar_significance"] == "Pathogenic"
        assert merged[0]["gnomad_af_global"] == 0.01
        assert merged[0]["cadd_phred"] == 25.0

    def test_partial_sources(self) -> None:
        """Variant matched by only VEP has only VEP bit set."""
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, genotype TEXT)")
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs1', '1', 100, 'AG')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        vep = {"rs1": {"gene_symbol": "GENE1"}}
        merged = _merge_annotations([row], vep, {}, {}, {})
        assert len(merged) == 1
        assert merged[0]["annotation_coverage"] == VEP_BIT

    def test_no_match_gets_coverage_zero(self) -> None:
        """Variants with no source match are emitted with annotation_coverage=0.

        F36: a processed-but-unmatched variant must leave an explicit
        ``coverage=0`` marker, not be dropped (which made it indistinguishable
        from a variant that never entered the pipeline).
        """
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, genotype TEXT)")
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs_none', '1', 100, 'AA')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        merged = _merge_annotations([row], {}, {}, {}, {})
        assert len(merged) == 1
        assert merged[0]["rsid"] == "rs_none"
        assert merged[0]["annotation_coverage"] == 0

    def test_alphamissense_sets_context_and_coverage_bit(self) -> None:
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, genotype TEXT)")
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs1', '1', 100, 'AG')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        alpha = {
            "rs1": {
                "alphamissense_pathogenicity": 0.91,
                "alphamissense_class": "likely_pathogenic",
            }
        }
        merged = _merge_annotations([row], {}, {}, {}, {}, alphamissense_data=alpha)

        assert merged[0]["alphamissense_pathogenicity"] == pytest.approx(0.91)
        assert merged[0]["alphamissense_class"] == "likely_pathogenic"
        assert merged[0]["annotation_coverage"] == ALPHAMISSENSE_BIT


# ═══════════════════════════════════════════════════════════════════════
# Crash recovery
# ═══════════════════════════════════════════════════════════════════════


class TestCrashRecovery:
    def test_delete_all_annotations(self, sample_with_variants: sa.Engine) -> None:
        """Deleting annotations clears the table."""
        # Insert some annotations
        with sample_with_variants.begin() as conn:
            conn.execute(
                annotated_variants.insert().values(
                    rsid="rs429358", chrom="19", pos=44908684, genotype="TC"
                )
            )
        _delete_all_annotations(sample_with_variants)
        with sample_with_variants.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(annotated_variants)
            ).scalar()
        assert count == 0


# ═══════════════════════════════════════════════════════════════════════
# Bulk upsert
# ═══════════════════════════════════════════════════════════════════════


class TestBulkUpsert:
    def test_upsert_writes_rows(self, sample_engine: sa.Engine) -> None:
        rows = [
            {
                "rsid": "rs1",
                "chrom": "1",
                "pos": 100,
                "genotype": "AG",
                "gene_symbol": "GENE1",
                "annotation_coverage": VEP_BIT,
            }
        ]
        written = _bulk_upsert(sample_engine, rows)
        assert written == 1

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs1")
            ).fetchone()
        assert row is not None
        assert row.gene_symbol == "GENE1"
        assert row.annotation_coverage == VEP_BIT

    def test_upsert_ors_bitmask(self, sample_engine: sa.Engine) -> None:
        """Second upsert ORs the bitmask with existing."""
        # First insert with VEP bit
        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert().values(
                    rsid="rs1",
                    chrom="1",
                    pos=100,
                    genotype="AG",
                    annotation_coverage=VEP_BIT,
                )
            )
        # Upsert with ClinVar bit
        rows = [
            {
                "rsid": "rs1",
                "chrom": "1",
                "pos": 100,
                "genotype": "AG",
                "clinvar_significance": "Pathogenic",
                "annotation_coverage": CLINVAR_BIT,
            }
        ]
        _bulk_upsert(sample_engine, rows)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs1"
                )
            ).fetchone()
        assert row.annotation_coverage == VEP_BIT | CLINVAR_BIT

    def test_empty_rows(self, sample_engine: sa.Engine) -> None:
        assert _bulk_upsert(sample_engine, []) == 0


# ═══════════════════════════════════════════════════════════════════════
# Full orchestration: run_annotation
# ═══════════════════════════════════════════════════════════════════════


class TestRunAnnotation:
    def test_full_annotation(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Full annotation populates annotated_variants with data from all sources."""
        result = run_annotation(sample_with_variants, mock_registry)

        assert result.total_variants == len(SEED_RAW_VARIANTS)
        assert result.rows_written > 0
        assert result.vep_matched > 0
        assert result.clinvar_matched > 0
        assert result.gnomad_matched > 0
        assert result.dbnsfp_matched > 0
        assert result.alphamissense_matched > 0
        assert result.batches_processed >= 1
        assert result.errors == []

    def test_all_fields_populated(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """T2-04: Known variant has VEP + ClinVar + gnomAD + dbNSFP fields."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs429358")
            ).fetchone()

        assert row is not None
        # VEP fields
        assert row.gene_symbol == "APOE"
        assert row.consequence == "missense_variant"
        assert row.mane_select in (True, 1)
        # ClinVar fields
        assert row.clinvar_significance == "risk_factor"
        assert row.clinvar_review_stars == 3
        # gnomAD fields
        assert row.gnomad_af_global is not None
        assert row.gnomad_af_global == pytest.approx(0.1387)
        # dbNSFP fields
        assert row.cadd_phred is not None
        assert row.cadd_phred == pytest.approx(28.3)
        assert row.sift_pred == "D"
        # Bitmask: all 5 sources (APOE is in gene_phenotype seed)
        assert row.annotation_coverage == (
            VEP_BIT | CLINVAR_BIT | GNOMAD_BIT | DBNSFP_BIT | GENE_PHENOTYPE_BIT
        )

    def test_engine_populates_zygosity(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """The engine computes carriage (zygosity) from genotype vs ClinVar ref/alt.

        Locks the live-engine carriage wiring (PR #320): run_annotation writes a
        non-NULL zygosity for matched SNVs so downstream carriage gates can fire.
        """
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            zygs = conn.execute(sa.select(annotated_variants.c.zygosity)).fetchall()

        assert any(z.zygosity is not None for z in zygs), (
            "engine wrote no zygosity — the carriage column is NULL for every variant"
        )

    def test_bitmask_partial_coverage(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Variants matched by fewer sources have partial bitmask.

        Matched variants carry a non-zero bitmask of exactly the sources that
        hit; an unmatched variant (e.g. ``rs_nomatch``) carries the explicit
        ``coverage=0`` marker (F36) rather than being dropped.
        """
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            rows = conn.execute(sa.select(annotated_variants)).fetchall()

        all_source_bits = VEP_BIT | CLINVAR_BIT | GNOMAD_BIT | DBNSFP_BIT | GENE_PHENOTYPE_BIT
        for row in rows:
            coverage = row.annotation_coverage
            assert coverage is not None  # always set (0 for unmatched)
            if row.rsid == "rs_nomatch":
                assert coverage == 0
            else:
                # Matched variants: at least one source bit set.
                assert coverage & all_source_bits > 0

    def test_unmatched_variants_marked_coverage_zero(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Variants matching no source are present with annotation_coverage=0 (F36).

        Previously dropped, which made "processed but unmatched" indistinguishable
        from "never processed" and broke raw↔annotated reconciliation.
        """
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs_nomatch")
            ).fetchone()
        assert row is not None
        assert row.annotation_coverage == 0
        # ...and an unmatched variant has no source-derived data.
        assert row.clinvar_significance is None
        assert row.zygosity is None

    def test_alphamissense_context_attached_without_mutating_core_evidence(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs1801133")
            ).fetchone()

        assert row is not None
        assert row.alphamissense_pathogenicity == pytest.approx(0.91)
        assert row.alphamissense_class == "likely_pathogenic"
        assert (row.annotation_coverage & ALPHAMISSENSE_BIT) == ALPHAMISSENSE_BIT
        assert row.revel is not None
        assert row.clinvar_significance == "drug_response"

    def test_crash_recovery_clears_previous(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Re-running annotation deletes previous results first."""
        result1 = run_annotation(sample_with_variants, mock_registry)
        result2 = run_annotation(sample_with_variants, mock_registry)

        # Same number of rows written
        assert result1.rows_written == result2.rows_written

        # No duplicates
        with sample_with_variants.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(annotated_variants)
            ).scalar()
        assert count == result2.rows_written

    def test_empty_sample(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Empty sample returns zeros."""
        result = run_annotation(sample_engine, mock_registry)
        assert result.total_variants == 0
        assert result.rows_written == 0

    def test_progress_callback(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Progress callback is invoked at least once."""
        calls: list[tuple[int, int]] = []
        run_annotation(
            sample_with_variants,
            mock_registry,
            progress_callback=lambda done, total: calls.append((done, total)),
        )
        assert len(calls) >= 1
        # Final call should indicate all variants processed
        last_done, last_total = calls[-1]
        assert last_done == last_total

    def test_graceful_degradation_missing_vep(
        self,
        sample_with_variants: sa.Engine,
        reference_engine: sa.Engine,
        gnomad_engine: sa.Engine,
        dbnsfp_engine: sa.Engine,
    ) -> None:
        """Annotation proceeds when VEP engine is unavailable."""
        registry = MagicMock()
        registry.reference_engine = reference_engine
        # VEP engine raises an exception when accessed
        type(registry).vep_engine = property(
            lambda self: (_ for _ in ()).throw(FileNotFoundError("no VEP"))
        )
        type(registry).gnomad_engine = property(lambda self: gnomad_engine)
        type(registry).dbnsfp_engine = property(lambda self: dbnsfp_engine)

        result = run_annotation(sample_with_variants, registry)
        assert result.vep_matched == 0
        assert result.clinvar_matched > 0  # ClinVar should still work
        assert result.rows_written > 0

    def test_genotype_preserved(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Genotype from raw_variants is carried into annotated_variants."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs429358")
            ).fetchone()
        assert row is not None
        assert row.genotype == "TC"

    def test_custom_batch_size(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Custom batch size of 3 processes multiple batches."""
        result = run_annotation(sample_with_variants, mock_registry, batch_size=3)
        assert result.batches_processed >= 2
        assert result.rows_written > 0


# ═══════════════════════════════════════════════════════════════════════
# Step 9 / Plan §5.6: AnnotationEngineResult.coverage_stats payload shape
# ═══════════════════════════════════════════════════════════════════════


def _stamp_bundle_version(reference_engine: sa.Engine, version: str) -> None:
    """Insert a `vep_bundle` row into the reference DB's `database_versions`."""
    with reference_engine.begin() as conn:
        conn.execute(database_versions.insert().values(db_name="vep_bundle", version=version))


def _stamp_sample_metadata(sample_engine: sa.Engine, *, file_format: str | None) -> None:
    """Insert the single sample_metadata row with a chosen file_format."""
    with sample_engine.begin() as conn:
        conn.execute(
            sample_metadata_table.insert().values(
                id=1,
                name="fixture-sample",
                file_format=file_format,
            )
        )


class TestCoverageStatsPayload:
    """Plan §5.6: `AnnotationEngineResult.coverage_stats` shape + content."""

    _REQUIRED_TOP_KEYS = {
        "bundle_version",
        "total_variants",
        "vep_bundle_rsid_hits",
        "vep_bundle_coord_fallback_hits",
        "vep_misses",
        "by_source",
    }
    _REQUIRED_PER_SOURCE_KEYS = {
        "vep_bundle_rsid_hits",
        "vep_bundle_coord_fallback_hits",
        "vep_misses",
    }

    def test_payload_shape_23andme(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Unmerged 23andMe sample: single-key by_source under `"23andme"`."""
        _stamp_bundle_version(mock_registry.reference_engine, "v2.0.0")
        _stamp_sample_metadata(sample_with_variants, file_format="23andme_v5")

        result = run_annotation(sample_with_variants, mock_registry)
        stats = result.coverage_stats

        assert set(stats.keys()) == self._REQUIRED_TOP_KEYS
        assert stats["bundle_version"] == "v2.0.0"
        assert stats["total_variants"] == result.total_variants
        assert stats["vep_bundle_rsid_hits"] == result.vep_matched
        assert stats["vep_bundle_coord_fallback_hits"] == 0
        expected_misses = result.total_variants - result.vep_matched
        assert stats["vep_misses"] == expected_misses

        assert list(stats["by_source"].keys()) == ["23andme"]
        per_source = stats["by_source"]["23andme"]
        assert set(per_source.keys()) == self._REQUIRED_PER_SOURCE_KEYS
        assert per_source["vep_bundle_rsid_hits"] == stats["vep_bundle_rsid_hits"]
        assert per_source["vep_bundle_coord_fallback_hits"] == 0
        assert per_source["vep_misses"] == stats["vep_misses"]

    def test_payload_shape_ancestrydna(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Unmerged AncestryDNA sample: single-key by_source under `"ancestrydna"`."""
        _stamp_bundle_version(mock_registry.reference_engine, "v2.0.0")
        _stamp_sample_metadata(sample_with_variants, file_format="ancestrydna_v2.0")

        result = run_annotation(sample_with_variants, mock_registry)
        stats = result.coverage_stats

        assert list(stats["by_source"].keys()) == ["ancestrydna"]
        assert stats["bundle_version"] == "v2.0.0"
        # Rollup sums match the (single) per-source entry.
        per_source = stats["by_source"]["ancestrydna"]
        assert per_source["vep_bundle_rsid_hits"] == stats["vep_bundle_rsid_hits"]
        assert per_source["vep_misses"] == stats["vep_misses"]

    def test_rollup_consistency(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Top-level rollup equals the sum across by_source per Plan §5.6."""
        _stamp_bundle_version(mock_registry.reference_engine, "v2.0.0")
        _stamp_sample_metadata(sample_with_variants, file_format="23andme_v5")

        result = run_annotation(sample_with_variants, mock_registry)
        stats = result.coverage_stats

        rollup_rsid = sum(s["vep_bundle_rsid_hits"] for s in stats["by_source"].values())
        rollup_coord = sum(
            s["vep_bundle_coord_fallback_hits"] for s in stats["by_source"].values()
        )
        rollup_misses = sum(s["vep_misses"] for s in stats["by_source"].values())
        assert rollup_rsid == stats["vep_bundle_rsid_hits"]
        assert rollup_coord == stats["vep_bundle_coord_fallback_hits"]
        assert rollup_misses == stats["vep_misses"]
        assert (
            stats["vep_bundle_rsid_hits"]
            + stats["vep_bundle_coord_fallback_hits"]
            + stats["vep_misses"]
            == stats["total_variants"]
        )

    def test_missing_bundle_version_is_none(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """No `database_versions` row → `bundle_version` is None, payload still emitted."""
        _stamp_sample_metadata(sample_with_variants, file_format="23andme_v5")

        result = run_annotation(sample_with_variants, mock_registry)
        stats = result.coverage_stats

        assert stats["bundle_version"] is None
        # Payload shape stays intact even when bundle version is unknown.
        assert set(stats.keys()) == self._REQUIRED_TOP_KEYS
        assert list(stats["by_source"].keys()) == ["23andme"]

    def test_missing_file_format_yields_unknown_vendor(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """No sample_metadata row → vendor key defaults to `"unknown"`."""
        _stamp_bundle_version(mock_registry.reference_engine, "v2.0.0")
        # Intentionally do NOT insert sample_metadata.

        result = run_annotation(sample_with_variants, mock_registry)
        stats = result.coverage_stats

        assert list(stats["by_source"].keys()) == ["unknown"]
        assert stats["bundle_version"] == "v2.0.0"

    def test_empty_sample_leaves_stats_empty(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Empty samples short-circuit before telemetry; coverage_stats stays `{}`."""
        _stamp_bundle_version(mock_registry.reference_engine, "v2.0.0")
        _stamp_sample_metadata(sample_engine, file_format="23andme_v5")

        result = run_annotation(sample_engine, mock_registry)
        assert result.total_variants == 0
        assert result.coverage_stats == {}


class TestCoverageStatsSideEffects:
    """Phase 0 closure (Step 18) / Plan §5.6 + §16.6 negative-side assertions.

    `run_annotation` returns coverage telemetry on the result dataclass but
    must NOT side-effect any reference- or per-sample-DB state. Provenance
    is written by `huey_tasks.run_annotation_task` only after
    `run_all_analyses` returns (Plan §5.6, §7.3, §7.4). These assertions lock
    the contract so a future refactor can't quietly push provenance writes
    back into the engine and re-introduce the half-fresh-gate failure mode.
    """

    def test_update_history_row_count_unchanged(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """`run_annotation` never writes to `update_history`."""
        _stamp_bundle_version(mock_registry.reference_engine, "v2.0.0")
        _stamp_sample_metadata(sample_with_variants, file_format="23andme_v5")

        ref_engine = mock_registry.reference_engine
        with ref_engine.connect() as conn:
            before = conn.execute(
                sa.select(sa.func.count()).select_from(update_history)
            ).scalar_one()

        result = run_annotation(sample_with_variants, mock_registry)
        assert result.total_variants > 0

        with ref_engine.connect() as conn:
            after = conn.execute(
                sa.select(sa.func.count()).select_from(update_history)
            ).scalar_one()
        assert after == before == 0

    def test_database_versions_vep_bundle_row_unchanged(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """`run_annotation` never mutates the `vep_bundle` row in `database_versions`."""
        _stamp_bundle_version(mock_registry.reference_engine, "v2.0.0")
        _stamp_sample_metadata(sample_with_variants, file_format="23andme_v5")

        ref_engine = mock_registry.reference_engine
        with ref_engine.connect() as conn:
            before_rows = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "vep_bundle")
            ).fetchall()

        result = run_annotation(sample_with_variants, mock_registry)
        assert result.coverage_stats["bundle_version"] == "v2.0.0"

        with ref_engine.connect() as conn:
            after_rows = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "vep_bundle")
            ).fetchall()

        # Same row count, same version string, same downloaded_at timestamp —
        # the engine read but did not write.
        assert len(after_rows) == len(before_rows) == 1
        assert before_rows[0].version == after_rows[0].version == "v2.0.0"
        assert before_rows[0].downloaded_at == after_rows[0].downloaded_at

    def test_annotation_state_untouched_by_engine(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Per-sample `annotation_state` has zero rows touched by `run_annotation` alone.

        The Huey-task wrapper is responsible for upserting provenance after
        analysis returns; the engine itself must leave the table empty.
        """
        _stamp_bundle_version(mock_registry.reference_engine, "v2.0.0")
        _stamp_sample_metadata(sample_with_variants, file_format="23andme_v5")

        with sample_with_variants.connect() as conn:
            before = conn.execute(
                sa.select(sa.func.count()).select_from(annotation_state)
            ).scalar_one()
        assert before == 0

        result = run_annotation(sample_with_variants, mock_registry)
        assert result.coverage_stats != {}

        with sample_with_variants.connect() as conn:
            after_rows = conn.execute(sa.select(annotation_state)).fetchall()
        assert after_rows == []


# ═══════════════════════════════════════════════════════════════════════
# T2-04: Integration test - 1000 variants end-to-end
# ═══════════════════════════════════════════════════════════════════════


class TestIntegration1000Variants:
    """T2-04: Annotation engine processes 1000 variants end-to-end."""

    def test_1000_variants_all_fields(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Generate 1000 variants, annotate, verify fields populated."""
        # Insert 1000 raw variants (mix of known + synthetic)
        known = SEED_RAW_VARIANTS[:5]
        synthetic = [
            {"rsid": f"rs_synth_{i}", "chrom": "1", "pos": 200000 + i, "genotype": "AG"}
            for i in range(1000 - len(known))
        ]
        all_variants = known + synthetic

        with sample_engine.begin() as conn:
            conn.execute(raw_variants.insert(), all_variants)

        result = run_annotation(sample_engine, mock_registry)

        assert result.total_variants == 1000
        assert result.rows_written > 0
        # Known variants should have annotations
        assert result.vep_matched >= 3  # at least some known rsids match

        # Verify a known variant has all fields
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs429358")
            ).fetchone()

        assert row is not None
        assert row.gene_symbol == "APOE"
        assert row.clinvar_significance is not None
        assert row.gnomad_af_global is not None
        assert row.cadd_phred is not None
        assert row.annotation_coverage == (
            VEP_BIT | CLINVAR_BIT | GNOMAD_BIT | DBNSFP_BIT | GENE_PHENOTYPE_BIT
        )


# ═══════════════════════════════════════════════════════════════════════
# P2-09: gnomAD annotation lookup integration
# ═══════════════════════════════════════════════════════════════════════


class TestGnomadAnnotationLookupIntegration:
    """P2-09: gnomAD annotation lookup integrated into annotation engine.

    Verifies:
    - rsid-based primary lookup delegates to gnomad.py
    - Position-based fallback for unmatched rsids with ref/alt
    - Correct rare/ultra-rare thresholds (0.01 / 0.001)
    - All population AF fields returned (AFR/AMR/EAS/EUR/SAS)
    - Homozygous count returned
    - _annot_to_dict conversion preserves all fields
    """

    def test_annot_to_dict_preserves_fields(self) -> None:
        """_annot_to_dict converts GnomADAnnotation to engine dict."""
        annot = GnomADAnnotation(
            rsid="rs7412",
            af_global=0.0781,
            af_afr=0.1130,
            af_amr=0.0560,
            af_eas=0.0980,
            af_eur=0.0730,
            af_fin=0.0410,
            af_sas=0.0650,
            homozygous_count=874,
            rare_flag=False,
            ultra_rare_flag=False,
        )
        d = _annot_to_dict(annot)

        assert d["gnomad_af_global"] == pytest.approx(0.0781)
        assert d["gnomad_af_afr"] == pytest.approx(0.1130)
        assert d["gnomad_af_amr"] == pytest.approx(0.0560)
        assert d["gnomad_af_eas"] == pytest.approx(0.0980)
        assert d["gnomad_af_eur"] == pytest.approx(0.0730)
        assert d["gnomad_af_fin"] == pytest.approx(0.0410)
        assert d["gnomad_af_sas"] == pytest.approx(0.0650)
        assert d["gnomad_homozygous_count"] == 874
        assert d["rare_flag"] is False
        assert d["ultra_rare_flag"] is False

    def test_rsid_lookup_returns_all_population_afs(self, gnomad_engine: sa.Engine) -> None:
        """P2-09: Lookup returns global AF and per-population AF."""
        result = _lookup_gnomad(["rs7412"], {}, gnomad_engine)

        assert "rs7412" in result
        data = result["rs7412"]
        assert data["gnomad_af_global"] == pytest.approx(0.0781)
        assert data["gnomad_af_afr"] == pytest.approx(0.1130)
        assert data["gnomad_af_amr"] == pytest.approx(0.0560)
        assert data["gnomad_af_eas"] == pytest.approx(0.0980)
        assert data["gnomad_af_eur"] == pytest.approx(0.0730)
        assert data["gnomad_af_fin"] == pytest.approx(0.0410)
        assert data["gnomad_af_sas"] == pytest.approx(0.0650)

    def test_rsid_lookup_returns_homozygous_count(self, gnomad_engine: sa.Engine) -> None:
        """P2-09: Lookup returns homozygous count."""
        result = _lookup_gnomad(["rs7412"], {}, gnomad_engine)
        assert result["rs7412"]["gnomad_homozygous_count"] == 874

    def test_rare_threshold_correct(self, gnomad_engine: sa.Engine) -> None:
        """P2-09 / F15: rare_flag uses the 0.01 threshold on popmax (not global)."""
        # rs5030862: popmax (afr)=0.006 — rare in every population.
        result = _lookup_gnomad(["rs5030862"], {}, gnomad_engine)
        assert result["rs5030862"]["rare_flag"] is True
        assert result["rs5030862"]["ultra_rare_flag"] is False
        assert result["rs5030862"]["gnomad_af_popmax"] == pytest.approx(0.006)

    def test_ancestry_common_not_rare(self, gnomad_engine: sa.Engine) -> None:
        """F15: rare globally (0.0052) but common in AFR (0.018) → not rare by popmax."""
        result = _lookup_gnomad(["rs28897696"], {}, gnomad_engine)
        assert result["rs28897696"]["gnomad_af_popmax"] == pytest.approx(0.018)
        assert result["rs28897696"]["rare_flag"] is False
        assert result["rs28897696"]["ultra_rare_flag"] is False

    def test_ultra_rare_threshold_correct(self, gnomad_engine: sa.Engine) -> None:
        """P2-09: ultra_rare_flag uses 0.001 threshold (bug fix from 0.0001)."""
        # rs80357906 has af_global=0.00004 — ultra-rare
        result = _lookup_gnomad(["rs80357906"], {}, gnomad_engine)
        assert result["rs80357906"]["rare_flag"] is True
        assert result["rs80357906"]["ultra_rare_flag"] is True

        # Verify threshold constants match PRD
        assert RARE_AF_THRESHOLD == 0.01
        assert ULTRA_RARE_AF_THRESHOLD == 0.001

    def test_ultra_rare_boundary(self, gnomad_engine: sa.Engine) -> None:
        """AF exactly at 0.001 is NOT ultra-rare (strict less-than)."""
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af VALUES "
                    "('rs_boundary', '1', 999, 'A', 'G', 0.001, "
                    "0.001, 0.001, 0.001, 0.001, 0.001, 0.001, 2)"
                )
            )
        result = _lookup_gnomad(["rs_boundary"], {}, gnomad_engine)
        assert result["rs_boundary"]["rare_flag"] is True
        assert result["rs_boundary"]["ultra_rare_flag"] is False

    def test_position_fallback_with_ref_alt(self, gnomad_engine: sa.Engine) -> None:
        """Position-based fallback matches when rsid differs but coords match."""
        # Insert variant under different rsid but same position
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af VALUES "
                    "('rs_gnomad_id', '5', 500, 'C', 'T', 0.02, "
                    "0.03, 0.01, 0.02, 0.025, 0.015, 0.018, 30)"
                )
            )

        # Create a fake raw row with ref/alt for position fallback
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, "
                    "genotype TEXT, ref TEXT, alt TEXT)"
                )
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs_user_id', '5', 500, 'CT', 'C', 'T')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        raw_by_rsid = {"rs_user_id": row}
        result = _lookup_gnomad(["rs_user_id"], raw_by_rsid, gnomad_engine)

        assert "rs_user_id" in result
        assert result["rs_user_id"]["gnomad_af_global"] == pytest.approx(0.02)

    def test_position_fallback_skipped_without_ref_alt(self, gnomad_engine: sa.Engine) -> None:
        """Fallback is skipped when raw variant lacks ref/alt columns."""
        # Create a raw row without ref/alt (like 23andMe data)
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, genotype TEXT)")
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs_no_match', '1', 100, 'AG')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        raw_by_rsid = {"rs_no_match": row}
        result = _lookup_gnomad(["rs_no_match"], raw_by_rsid, gnomad_engine)

        # No match by rsid and no ref/alt for position fallback
        assert "rs_no_match" not in result

    def test_gnomad_fields_in_annotated_variants(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-09: Full pipeline writes all gnomAD fields to annotated_variants."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs7412")
            ).fetchone()

        assert row is not None
        # Global AF
        assert row.gnomad_af_global == pytest.approx(0.0781)
        # Per-population AFs (AFR/AMR/EAS/EUR/SAS per PRD)
        assert row.gnomad_af_afr == pytest.approx(0.1130)
        assert row.gnomad_af_amr == pytest.approx(0.0560)
        assert row.gnomad_af_eas == pytest.approx(0.0980)
        assert row.gnomad_af_eur == pytest.approx(0.0730)
        assert row.gnomad_af_sas == pytest.approx(0.0650)
        # Homozygous count
        assert row.gnomad_homozygous_count == 874
        # Rare flags
        assert row.rare_flag in (False, 0)
        assert row.ultra_rare_flag in (False, 0)
        # Bitmask has gnomAD bit set
        assert row.annotation_coverage & GNOMAD_BIT == GNOMAD_BIT

    def test_gnomad_rare_variant_in_pipeline(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-09: Ultra-rare variant flags flow through full pipeline."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs80357906")
            ).fetchone()

        assert row is not None
        assert row.gnomad_af_global == pytest.approx(0.00004)
        assert row.rare_flag in (True, 1)
        assert row.ultra_rare_flag in (True, 1)

    def test_delegates_to_gnomad_module(self, gnomad_engine: sa.Engine) -> None:
        """Engine uses gnomad.py lookup functions (not duplicated SQL)."""
        # Verify that the module-level lookup and engine lookup produce
        # identical results, proving delegation
        module_result = lookup_gnomad_by_rsids(["rs429358"], gnomad_engine)
        engine_result = _lookup_gnomad(["rs429358"], {}, gnomad_engine)

        module_annot = module_result["rs429358"]
        engine_data = engine_result["rs429358"]

        assert engine_data["gnomad_af_global"] == module_annot.af_global
        assert engine_data["gnomad_af_afr"] == module_annot.af_afr
        assert engine_data["gnomad_af_amr"] == module_annot.af_amr
        assert engine_data["gnomad_af_eas"] == module_annot.af_eas
        assert engine_data["gnomad_af_eur"] == module_annot.af_eur
        assert engine_data["gnomad_af_sas"] == module_annot.af_sas
        assert engine_data["gnomad_homozygous_count"] == module_annot.homozygous_count
        assert engine_data["rare_flag"] == module_annot.rare_flag
        assert engine_data["ultra_rare_flag"] == module_annot.ultra_rare_flag


# ═══════════════════════════════════════════════════════════════════════
# P2-12: dbNSFP annotation integration
# ═══════════════════════════════════════════════════════════════════════


class TestDbnsfpAnnotationIntegration:
    """P2-12: dbNSFP annotation integrated into annotation engine.

    Verifies:
    - Delegation to dbnsfp.py lookup functions (not duplicated SQL)
    - All 14 score fields flow through pipeline into annotated_variants
    - deleterious_count is computed and stored
    - Position-based fallback for unmatched rsids with ref/alt
    - _dbnsfp_annot_to_dict conversion preserves all fields
    """

    def test_dbnsfp_annot_to_dict_preserves_fields(self) -> None:
        """_dbnsfp_annot_to_dict converts DbNSFPAnnotation to engine dict."""
        annot = DbNSFPAnnotation(
            rsid="rs429358",
            chrom="19",
            pos=44908684,
            ref="T",
            alt="C",
            cadd_phred=28.3,
            sift_score=0.001,
            sift_pred="D",
            polyphen2_hsvar_score=0.998,
            polyphen2_hsvar_pred="D",
            revel=0.812,
            mutpred2=0.780,
            vest4=0.891,
            metasvm=0.920,
            metalr=0.885,
            gerp_rs=5.48,
            phylop=7.92,
            mpc=1.85,
            primateai=0.91,
        )
        d = _dbnsfp_annot_to_dict(annot)

        assert d["cadd_phred"] == pytest.approx(28.3)
        assert d["sift_score"] == pytest.approx(0.001)
        assert d["sift_pred"] == "D"
        assert d["polyphen2_hsvar_score"] == pytest.approx(0.998)
        assert d["polyphen2_hsvar_pred"] == "D"
        assert d["revel"] == pytest.approx(0.812)
        assert d["mutpred2"] == pytest.approx(0.780)
        assert d["vest4"] == pytest.approx(0.891)
        assert d["metasvm"] == pytest.approx(0.920)
        assert d["metalr"] == pytest.approx(0.885)
        assert d["gerp_rs"] == pytest.approx(5.48)
        assert d["phylop"] == pytest.approx(7.92)
        assert d["mpc"] == pytest.approx(1.85)
        assert d["primateai"] == pytest.approx(0.91)
        # All 4 independent axes deleterious (META = REVEL/MetaSVM/MetaLR, F24)
        assert d["deleterious_count"] == 4

    def test_dbnsfp_annot_to_dict_null_scores(self) -> None:
        """_dbnsfp_annot_to_dict handles all-null scores."""
        annot = DbNSFPAnnotation(
            rsid="rs1",
            chrom="1",
            pos=100,
            ref="A",
            alt="G",
        )
        d = _dbnsfp_annot_to_dict(annot)

        assert d["cadd_phred"] is None
        assert d["sift_score"] is None
        assert d["revel"] is None
        assert d["deleterious_count"] == 0

    def test_rsid_lookup_returns_all_score_fields(self, dbnsfp_engine: sa.Engine) -> None:
        """P2-12: Lookup returns all 14 dbNSFP score fields."""
        result = _lookup_dbnsfp(["rs429358"], {}, dbnsfp_engine)

        assert "rs429358" in result
        data = result["rs429358"]
        assert data["cadd_phred"] == pytest.approx(28.3)
        assert data["sift_score"] == pytest.approx(0.001)
        assert data["sift_pred"] == "D"
        assert data["polyphen2_hsvar_score"] == pytest.approx(0.998)
        assert data["polyphen2_hsvar_pred"] == "D"
        assert data["revel"] == pytest.approx(0.812)
        assert data["mutpred2"] == pytest.approx(0.780)
        assert data["vest4"] == pytest.approx(0.891)
        assert data["metasvm"] == pytest.approx(0.920)
        assert data["metalr"] == pytest.approx(0.885)
        assert data["gerp_rs"] == pytest.approx(5.48)
        assert data["phylop"] == pytest.approx(7.92)
        assert data["mpc"] == pytest.approx(1.85)
        assert data["primateai"] == pytest.approx(0.91)

    def test_rsid_lookup_returns_deleterious_count(self, dbnsfp_engine: sa.Engine) -> None:
        """P2-12: Lookup computes and returns deleterious_count."""
        result = _lookup_dbnsfp(["rs429358"], {}, dbnsfp_engine)
        data = result["rs429358"]
        # rs429358: SIFT(D), PP2(D), CADD(D), META=REVEL/MetaSVM/MetaLR(D) → 4 axes (F24)
        assert data["deleterious_count"] == 4

    def test_delegates_to_dbnsfp_module(self, dbnsfp_engine: sa.Engine) -> None:
        """Engine uses dbnsfp.py lookup functions (not duplicated SQL)."""
        module_result = lookup_dbnsfp_by_rsids(["rs429358"], dbnsfp_engine)
        engine_result = _lookup_dbnsfp(["rs429358"], {}, dbnsfp_engine)

        module_annot = module_result["rs429358"]
        engine_data = engine_result["rs429358"]

        assert engine_data["cadd_phred"] == module_annot.cadd_phred
        assert engine_data["sift_score"] == module_annot.sift_score
        assert engine_data["sift_pred"] == module_annot.sift_pred
        assert engine_data["polyphen2_hsvar_score"] == module_annot.polyphen2_hsvar_score
        assert engine_data["polyphen2_hsvar_pred"] == module_annot.polyphen2_hsvar_pred
        assert engine_data["revel"] == module_annot.revel
        assert engine_data["mutpred2"] == module_annot.mutpred2
        assert engine_data["vest4"] == module_annot.vest4
        assert engine_data["metasvm"] == module_annot.metasvm
        assert engine_data["metalr"] == module_annot.metalr
        assert engine_data["gerp_rs"] == module_annot.gerp_rs
        assert engine_data["phylop"] == module_annot.phylop
        assert engine_data["mpc"] == module_annot.mpc
        assert engine_data["primateai"] == module_annot.primateai
        assert engine_data["deleterious_count"] == module_annot.deleterious_count

    def test_position_fallback_skipped_cross_build(self, dbnsfp_engine: sa.Engine) -> None:
        """F35: the GRCh37 position fallback is skipped against GRCh38 dbNSFP.

        Even when a raw row carries ref/alt (a future VCF/WGS input), the
        ``(chrom, pos, ref, alt)`` fallback is a GRCh37→GRCh38 cross-build join,
        so ``lookup_dbnsfp_by_positions`` declines it and the engine produces no
        position-based match. The rsid path (build-agnostic) remains the only
        live match. (The skip warning is asserted in test_dbnsfp.py.)
        """
        # Create a fake raw row with ref/alt — would have triggered the (now
        # guarded) position fallback before F35.
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, "
                    "genotype TEXT, ref TEXT, alt TEXT)"
                )
            )
            conn.execute(
                sa.text("INSERT INTO t VALUES ('rs_user_id', '19', 44908684, 'TC', 'T', 'C')")
            )
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        raw_by_rsid = {"rs_user_id": row}
        result = _lookup_dbnsfp(["rs_user_id"], raw_by_rsid, dbnsfp_engine)

        # rsid "rs_user_id" is not in the dbNSFP DB and the position fallback is
        # cross-build → no match at all.
        assert "rs_user_id" not in result

    def test_position_fallback_skipped_without_ref_alt(self, dbnsfp_engine: sa.Engine) -> None:
        """Fallback is skipped when raw variant lacks ref/alt columns."""
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, genotype TEXT)")
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs_no_match', '1', 100, 'AG')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        raw_by_rsid = {"rs_no_match": row}
        result = _lookup_dbnsfp(["rs_no_match"], raw_by_rsid, dbnsfp_engine)

        assert "rs_no_match" not in result

    def test_dbnsfp_fields_in_annotated_variants(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-12: Full pipeline writes all dbNSFP fields to annotated_variants."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs429358")
            ).fetchone()

        assert row is not None
        # All 14 score fields
        assert row.cadd_phred == pytest.approx(28.3)
        assert row.sift_score == pytest.approx(0.001)
        assert row.sift_pred == "D"
        assert row.polyphen2_hsvar_score == pytest.approx(0.998)
        assert row.polyphen2_hsvar_pred == "D"
        assert row.revel == pytest.approx(0.812)
        assert row.mutpred2 == pytest.approx(0.780)
        assert row.vest4 == pytest.approx(0.891)
        assert row.metasvm == pytest.approx(0.920)
        assert row.metalr == pytest.approx(0.885)
        assert row.gerp_rs == pytest.approx(5.48)
        assert row.phylop == pytest.approx(7.92)
        assert row.mpc == pytest.approx(1.85)
        assert row.primateai == pytest.approx(0.91)
        # Deleterious count: 4 independent axes (SIFT, PolyPhen, CADD, collapsed
        # META = REVEL/MetaSVM/MetaLR), all deleterious (F24).
        assert row.deleterious_count == 4
        assert row.deleterious_total_assessed == 4
        # Bitmask has dbNSFP bit set
        assert row.annotation_coverage & DBNSFP_BIT == DBNSFP_BIT

    def test_deleterious_count_in_pipeline(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-12: deleterious_count flows through full pipeline correctly."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            rows = conn.execute(
                sa.select(annotated_variants).where(
                    annotated_variants.c.annotation_coverage.op("&")(DBNSFP_BIT) == DBNSFP_BIT
                )
            ).fetchall()

        # All dbNSFP-matched variants should have deleterious_count
        for row in rows:
            assert row.deleterious_count is not None
            # 4 independent axes after the meta-predictor collapse (F24).
            assert 0 <= row.deleterious_count <= 4

    def test_partial_scores_deleterious_count(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-12: Variant with partial scores has correct deleterious count."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs1801133")
            ).fetchone()

        # rs1801133 (MTHFR C677T) is in seed data for all sources
        assert row is not None
        assert row.deleterious_count is not None
        assert 0 <= row.deleterious_count <= 4

    def test_known_variant_rs1801133_scores(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """T2-11 via engine: rs1801133 CADD and REVEL scores flow through pipeline."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs1801133")
            ).fetchone()

        assert row is not None
        assert row.cadd_phred == pytest.approx(24.8)
        assert row.revel == pytest.approx(0.689)

    def test_empty_rsids(self, dbnsfp_engine: sa.Engine) -> None:
        """Empty rsid list returns empty dict."""
        result = _lookup_dbnsfp([], {}, dbnsfp_engine)
        assert len(result) == 0


# ═══════════════════════════════════════════════════════════════════════
# P2-13 / F24/F25: Ensemble pathogenicity flag (majority of present axes)
# ═══════════════════════════════════════════════════════════════════════


class TestEnsemblePathogenicIntegration:
    """P2-13 / F24/F25: ensemble flag = strict majority of *present* independent axes.

    Verifies:
    - _dbnsfp_annot_to_dict includes ensemble_pathogenic + deleterious_total_assessed
    - apply_ensemble_pathogenic sets flag on merged dicts via the k-of-present rule
    - Flag and denominator flow through the full pipeline into annotated_variants
    - The four axes are SIFT, PolyPhen, CADD and the collapsed META family (F24)
    - The denominator is the axes actually assessed, not a fixed 5 (F25)
    """

    def test_dbnsfp_annot_to_dict_includes_ensemble_flag(self) -> None:
        """_dbnsfp_annot_to_dict carries the vote counts and a True flag when a majority agree."""
        annot = DbNSFPAnnotation(
            rsid="rs1",
            chrom="1",
            pos=100,
            ref="A",
            alt="G",
            cadd_phred=28.0,
            sift_score=0.001,
            sift_pred="D",
            polyphen2_hsvar_score=0.998,
            polyphen2_hsvar_pred="D",
            revel=0.8,
            metasvm=0.9,
        )
        d = _dbnsfp_annot_to_dict(annot)
        # SIFT, PolyPhen, CADD + collapsed META (REVEL/MetaSVM) = 4 axes, all del.
        assert d["deleterious_count"] == 4
        assert d["deleterious_total_assessed"] == 4
        assert d["ensemble_pathogenic"] is True

    def test_dbnsfp_annot_to_dict_not_pathogenic_under_threshold(self) -> None:
        """_dbnsfp_annot_to_dict returns ensemble_pathogenic=False when <3 deleterious."""
        annot = DbNSFPAnnotation(
            rsid="rs2",
            chrom="1",
            pos=200,
            ref="A",
            alt="G",
            sift_score=0.001,
            sift_pred="D",
            polyphen2_hsvar_score=0.95,  # > 0.909 "probably damaging" (F38)
            polyphen2_hsvar_pred="D",
            # Only 2 of 4 axes deleterious (SIFT + PolyPhen); CADD and the META
            # axis (REVEL+MetaSVM both tolerated) vote not-deleterious.
            cadd_phred=10.0,  # Below 20 threshold
            revel=0.3,  # Below 0.5 threshold
            metasvm=-0.5,  # Below 0 threshold
        )
        d = _dbnsfp_annot_to_dict(annot)
        assert d["deleterious_count"] == 2
        assert d["deleterious_total_assessed"] == 4
        # 2 of 4 is not a strict majority → not flagged.
        assert d["ensemble_pathogenic"] is False

    def test_dbnsfp_annot_to_dict_null_scores_not_pathogenic(self) -> None:
        """All-null scores yield ensemble_pathogenic=False."""
        annot = DbNSFPAnnotation(
            rsid="rs3",
            chrom="1",
            pos=300,
            ref="A",
            alt="G",
        )
        d = _dbnsfp_annot_to_dict(annot)
        assert d["deleterious_count"] == 0
        assert d["ensemble_pathogenic"] is False

    def test_apply_ensemble_pathogenic_on_merged(self) -> None:
        """apply_ensemble_pathogenic sets the flag via the k-of-present rule (F24/F25)."""
        from backend.annotation.engine import apply_ensemble_pathogenic

        merged = [
            # 3 of 4 → strict majority → flagged
            {"rsid": "rs1", "deleterious_count": 3, "deleterious_total_assessed": 4},
            # 2 of 4 → not a majority → not flagged
            {"rsid": "rs2", "deleterious_count": 2, "deleterious_total_assessed": 4},
            # 2 of 2 present → majority → flagged (unreachable under the old fixed-3)
            {"rsid": "rs3", "deleterious_count": 2, "deleterious_total_assessed": 2},
            # 1 of 1 present → too few axes → not flagged
            {"rsid": "rs4", "deleterious_count": 1, "deleterious_total_assessed": 1},
            {"rsid": "rs5"},  # No vote counts at all → flag left unset
        ]
        apply_ensemble_pathogenic(merged)

        assert merged[0]["ensemble_pathogenic"] is True
        assert merged[1]["ensemble_pathogenic"] is False
        assert merged[2]["ensemble_pathogenic"] is True
        assert merged[3]["ensemble_pathogenic"] is False
        assert "ensemble_pathogenic" not in merged[4]

    def test_apply_ensemble_pathogenic_does_not_overwrite(self) -> None:
        """apply_ensemble_pathogenic skips dicts that already have the key."""
        from backend.annotation.engine import apply_ensemble_pathogenic

        merged = [
            {
                "rsid": "rs1",
                "deleterious_count": 1,
                "deleterious_total_assessed": 4,
                "ensemble_pathogenic": True,
            },
        ]
        apply_ensemble_pathogenic(merged)
        # Pre-set flag is preserved even though 1 of 4 would not flag on its own.
        assert merged[0]["ensemble_pathogenic"] is True

    def test_ensemble_flag_in_annotated_variants_true(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-13: Variant with ≥3 deleterious tools has ensemble_pathogenic=True in DB."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            # rs429358: SIFT=D, PP2=D, CADD=D, META(REVEL/MetaSVM/MetaLR)=D
            # → all 4 independent axes deleterious (F24).
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs429358")
            ).fetchone()

        assert row is not None
        assert row.deleterious_count == 4
        assert row.deleterious_total_assessed == 4
        assert row.ensemble_pathogenic in (True, 1)

    def test_ensemble_flag_in_annotated_variants_false(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-13: Variant with <3 deleterious tools has ensemble_pathogenic=False."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            # rs4680: SIFT=0.082(T), PP2=0.451(T), CADD=15.2(T), and the META axis
            # is tolerated — REVEL=0.312(T) and MetaLR=0.420(T) outvote the lone
            # MetaSVM=0.380(D), so the collapsed family contributes no vote (F24).
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs4680")
            ).fetchone()

        assert row is not None
        assert row.deleterious_count == 0
        assert row.deleterious_total_assessed == 4
        assert row.ensemble_pathogenic in (False, 0)

    def test_ensemble_flag_exactly_three(
        self,
        sample_engine: sa.Engine,
        dbnsfp_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """P2-13: Variant with exactly 3 deleterious tools is flagged."""
        # Insert a custom variant with exactly 3 deleterious predictions:
        # SIFT=0.01(D), PP2=0.95(D>0.909), CADD=25(D≥20), REVEL=0.3(<0.5), MetaSVM=-0.5(<0)
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert().values(
                    rsid="rs_three_del", chrom="1", pos=99999, genotype="AG"
                )
            )
        with dbnsfp_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO dbnsfp_scores "
                    "(rsid, chrom, pos, ref, alt, cadd_phred, sift_score, sift_pred, "
                    "polyphen2_hsvar_score, polyphen2_hsvar_pred, revel, metasvm) "
                    "VALUES ('rs_three_del', '1', 99999, 'A', 'G', 25.0, 0.01, 'D', "
                    "0.95, 'D', 0.3, -0.5)"
                )
            )

        registry = MagicMock()
        registry.reference_engine = reference_engine
        type(registry).vep_engine = property(
            lambda self: (_ for _ in ()).throw(FileNotFoundError("no VEP"))
        )
        type(registry).gnomad_engine = property(
            lambda self: (_ for _ in ()).throw(FileNotFoundError("no gnomAD"))
        )
        type(registry).dbnsfp_engine = property(lambda s: dbnsfp_engine)

        run_annotation(sample_engine, registry)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs_three_del")
            ).fetchone()

        assert row is not None
        assert row.deleterious_count == 3
        assert row.ensemble_pathogenic in (True, 1)

    def test_all_dbnsfp_variants_have_ensemble_flag(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-13: Every variant with dbNSFP data has ensemble_pathogenic set."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            rows = conn.execute(
                sa.select(annotated_variants).where(
                    annotated_variants.c.annotation_coverage.op("&")(DBNSFP_BIT) == DBNSFP_BIT
                )
            ).fetchall()

        assert len(rows) > 0
        for row in rows:
            assert row.ensemble_pathogenic is not None
            assert row.deleterious_count is not None
            assert row.deleterious_total_assessed is not None
            # Flag matches the k-of-present rule: a strict majority of the
            # assessed axes, with at least 2 axes present (F24/F25).
            expected = (
                row.deleterious_total_assessed >= 2
                and row.deleterious_count * 2 > row.deleterious_total_assessed
            )
            assert bool(row.ensemble_pathogenic) is expected

    def test_ensemble_pathogenic_in_upsert_columns(self) -> None:
        """ensemble_pathogenic is in _UPSERT_COLUMNS list."""
        from backend.annotation.engine import _UPSERT_COLUMNS

        assert "ensemble_pathogenic" in _UPSERT_COLUMNS


# ═══════════════════════════════════════════════════════════════════════
# P2-15: Gene-phenotype annotation
# ═══════════════════════════════════════════════════════════════════════


class TestGenePhenotypeAnnotation:
    """P2-15: Gene-phenotype annotation via MONDO/HPO + optional OMIM.

    Verifies:
    - _lookup_gene_phenotype maps VEP gene symbols to phenotype records
    - Gene-phenotype data flows through _merge_annotations with bitmask bit 4
    - Full pipeline writes disease_name, disease_id, hpo_terms, phenotype_source,
      inheritance_pattern to annotated_variants
    - Variants without gene_symbol (no VEP match) get no phenotype data
    - gene_phenotype_matched count is tracked in AnnotationEngineResult
    """

    def test_lookup_gene_phenotype_returns_fields(self, reference_engine: sa.Engine) -> None:
        """_lookup_gene_phenotype returns phenotype fields keyed by rsid."""
        vep_data = {
            "rs1": {"gene_symbol": "BRCA1", "consequence": "missense_variant"},
            "rs2": {"gene_symbol": "CFTR", "consequence": "missense_variant"},
        }
        result = _lookup_gene_phenotype(vep_data, reference_engine)

        assert "rs1" in result
        assert result["rs1"]["disease_name"] == "Hereditary breast and ovarian cancer syndrome"
        assert result["rs1"]["disease_id"] == "MONDO:0011450"
        assert result["rs1"]["phenotype_source"] == "mondo_hpo"
        assert result["rs1"]["inheritance_pattern"] == "Autosomal dominant"
        assert result["rs1"]["hpo_terms"] is not None  # JSON string

        assert "rs2" in result
        assert result["rs2"]["disease_name"] == "Cystic fibrosis"
        assert result["rs2"]["inheritance_pattern"] == "Autosomal recessive"

    def test_lookup_gene_phenotype_no_gene_symbol(self, reference_engine: sa.Engine) -> None:
        """Variants without gene_symbol in VEP data are skipped."""
        vep_data = {"rs1": {"consequence": "intergenic_variant"}}
        result = _lookup_gene_phenotype(vep_data, reference_engine)
        assert len(result) == 0

    def test_lookup_gene_phenotype_unmatched_gene(self, reference_engine: sa.Engine) -> None:
        """Gene not in gene_phenotype table returns no result."""
        vep_data = {"rs1": {"gene_symbol": "NONEXISTENT_GENE"}}
        result = _lookup_gene_phenotype(vep_data, reference_engine)
        assert "rs1" not in result

    def test_lookup_gene_phenotype_empty_vep(self, reference_engine: sa.Engine) -> None:
        """Empty VEP data returns empty dict."""
        result = _lookup_gene_phenotype({}, reference_engine)
        assert len(result) == 0

    def test_merge_includes_gene_phenotype_bit(self) -> None:
        """Gene-phenotype data in merge produces GENE_PHENOTYPE_BIT in bitmask."""
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, genotype TEXT)")
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs1', '1', 100, 'AG')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        vep = {"rs1": {"gene_symbol": "BRCA1"}}
        gp = {"rs1": {"disease_name": "HBOC", "phenotype_source": "mondo_hpo"}}

        merged = _merge_annotations([row], vep, {}, {}, {}, gp)
        assert len(merged) == 1
        assert merged[0]["annotation_coverage"] == VEP_BIT | GENE_PHENOTYPE_BIT
        assert merged[0]["disease_name"] == "HBOC"

    @pytest.mark.parametrize(
        ("significance", "label_expected"),
        [
            ("Pathogenic", True),
            ("Likely pathogenic", True),
            ("Uncertain significance", True),  # VUS keeps gene context
            ("risk_factor", True),
            (None, True),  # unclassified keeps gene context
            ("Benign", False),
            ("Likely benign", False),
            ("Benign/Likely benign", False),
            ("Likely_benign", False),  # raw ClinVar VCF spelling
            ("Benign/Likely_benign", False),  # underscore combined form
            ("benign", False),  # lowercase fixture form
            ("likely_benign", False),  # lowercase + underscore
        ],
    )
    def test_merge_gene_phenotype_gated_on_pathogenicity(
        self, significance: str | None, label_expected: bool
    ) -> None:
        """F22: a benign variant must not inherit its gene's disease label."""
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, genotype TEXT)")
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs1', '13', 100, 'GA')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        vep = {"rs1": {"gene_symbol": "BRCA2"}}
        clinvar = {"rs1": {"clinvar_significance": significance}} if significance else {}
        gp = {"rs1": {"disease_name": "breast-ovarian cancer susceptibility 2"}}

        merged = _merge_annotations([row], vep, clinvar, {}, {}, gp)
        assert len(merged) == 1
        has_bit = bool(merged[0]["annotation_coverage"] & GENE_PHENOTYPE_BIT)
        if label_expected:
            assert merged[0].get("disease_name") == "breast-ovarian cancer susceptibility 2"
            assert has_bit
        else:
            assert merged[0].get("disease_name") is None
            assert not has_bit

    def test_gene_phenotype_fields_in_annotated_variants(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-15: Full pipeline writes gene-phenotype fields for known gene."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs429358")
            ).fetchone()

        assert row is not None
        # rs429358 → APOE gene → "Alzheimer disease susceptibility" in seed CSV
        assert row.disease_name == "Alzheimer disease susceptibility"
        assert row.disease_id == "MONDO:0004975"
        assert row.phenotype_source == "mondo_hpo"
        assert row.hpo_terms is not None
        # APOE has no inheritance in seed CSV
        # Bitmask has GENE_PHENOTYPE_BIT set
        assert row.annotation_coverage & GENE_PHENOTYPE_BIT == GENE_PHENOTYPE_BIT

    def test_brca1_phenotype_in_pipeline(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-15: BRCA1 variant gets correct disease name and inheritance."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs80357906")
            ).fetchone()

        assert row is not None
        assert row.gene_symbol == "BRCA1"
        assert row.disease_name == "Hereditary breast and ovarian cancer syndrome"
        assert row.inheritance_pattern == "Autosomal dominant"

    def test_mthfr_phenotype_in_pipeline(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-15: MTHFR variant gets correct phenotype data."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs1801133")
            ).fetchone()

        assert row is not None
        assert row.disease_name is not None
        assert row.phenotype_source == "mondo_hpo"
        assert row.inheritance_pattern == "Autosomal recessive"

    def test_gene_phenotype_matched_count(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-15: gene_phenotype_matched is tracked in result."""
        result = run_annotation(sample_with_variants, mock_registry)
        assert result.gene_phenotype_matched > 0

    def test_gene_phenotype_columns_in_upsert(self) -> None:
        """P2-15: Gene-phenotype columns are in _UPSERT_COLUMNS."""
        from backend.annotation.engine import _UPSERT_COLUMNS

        assert "disease_name" in _UPSERT_COLUMNS
        assert "disease_id" in _UPSERT_COLUMNS
        assert "phenotype_source" in _UPSERT_COLUMNS
        assert "hpo_terms" in _UPSERT_COLUMNS
        assert "inheritance_pattern" in _UPSERT_COLUMNS

    def test_gene_phenotype_bit_constant(self) -> None:
        """P2-15: GENE_PHENOTYPE_BIT is bit 4 (value 16)."""
        assert GENE_PHENOTYPE_BIT == 0b010000
        assert GENE_PHENOTYPE_BIT == 16

    def test_no_phenotype_for_unmatched_gene(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Variants whose gene is not in gene_phenotype table get no phenotype data."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            rows = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.disease_name.is_(None))
            ).fetchall()

        # Some variants should have no phenotype data (gene not in seed CSV
        # or no VEP gene_symbol)
        # At minimum, rs_nomatch should not appear at all (no annotations)
        # but rs12913832 (HERC2) IS in seed CSV, so most VEP-matched variants
        # will have gene-phenotype data
        for row in rows:
            assert row.annotation_coverage & GENE_PHENOTYPE_BIT == 0
