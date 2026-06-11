"""Tests for database schemas (reference.db + sample DB)."""

import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from backend.db.sample_schema import (
    SAMPLE_SCHEMA_VERSION,
    create_sample_tables,
    ensure_sample_schema_current,
)


def _run_alembic_upgrade(db_path: Path) -> None:
    """Run Alembic upgrade to head on a SQLite database."""
    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")


def _get_tables(db_path: Path) -> set[str]:
    """Return table names in a SQLite database."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'alembic_%'"
    )
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()
    return tables


def _get_columns(db_path: Path, table: str) -> list[str]:
    """Return column names for a table."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(f"PRAGMA table_info({table})")  # noqa: S608
    columns = [row[1] for row in cursor.fetchall()]
    conn.close()
    return columns


def _get_indexes(db_path: Path) -> set[str]:
    """Return index names in a SQLite database."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    )
    indexes = {row[0] for row in cursor.fetchall()}
    conn.close()
    return indexes


# ── Reference DB Tests ──────────────────────────────────────────────


class TestReferenceSchema:
    """Test that Alembic migration creates all reference.db tables."""

    def test_alembic_creates_all_tables(self, tmp_path):
        db_path = tmp_path / "reference.db"
        _run_alembic_upgrade(db_path)
        tables = _get_tables(db_path)
        expected = {
            "samples",
            "jobs",
            "database_versions",
            "update_history",
            "downloads",
            "clinvar_variants",
            "gene_phenotype",
            "cpic_alleles",
            "cpic_diplotypes",
            "cpic_guidelines",
            "literature_cache",
            "uniprot_cache",
            "log_entries",
            "reannotation_prompts",
            "gwas_associations",
        }
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"

    def test_samples_table_columns(self, tmp_path):
        db_path = tmp_path / "reference.db"
        _run_alembic_upgrade(db_path)
        cols = _get_columns(db_path, "samples")
        assert "id" in cols
        assert "name" in cols
        assert "db_path" in cols
        assert "created_at" in cols

    def test_jobs_table_columns(self, tmp_path):
        db_path = tmp_path / "reference.db"
        _run_alembic_upgrade(db_path)
        cols = _get_columns(db_path, "jobs")
        assert "job_id" in cols
        assert "status" in cols
        assert "progress_pct" in cols
        assert "message" in cols
        assert "job_type" in cols

    def test_clinvar_table_has_indexes(self, tmp_path):
        db_path = tmp_path / "reference.db"
        _run_alembic_upgrade(db_path)
        indexes = _get_indexes(db_path)
        assert "idx_clinvar_chrom_pos" in indexes
        assert "ix_clinvar_variants_rsid" in indexes

    def test_update_history_columns(self, tmp_path):
        db_path = tmp_path / "reference.db"
        _run_alembic_upgrade(db_path)
        cols = _get_columns(db_path, "update_history")
        assert "db_name" in cols
        assert "previous_version" in cols
        assert "new_version" in cols
        assert "variants_reclassified" in cols
        assert "download_size_bytes" in cols

    def test_alembic_downgrade(self, tmp_path):
        db_path = tmp_path / "reference.db"
        _run_alembic_upgrade(db_path)
        cfg = Config()
        cfg.set_main_option("script_location", "alembic")
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
        command.downgrade(cfg, "base")
        tables = _get_tables(db_path)
        assert len(tables) == 0


# ── Sample DB Tests ─────────────────────────────────────────────────


class TestSampleSchema:
    """Test that create_sample_tables() creates all per-sample tables."""

    def _create_sample_db(self, db_path: Path) -> sa.Engine:
        engine = sa.create_engine(f"sqlite:///{db_path}")
        create_sample_tables(engine)
        return engine

    def test_creates_all_tables(self, tmp_path):
        db_path = tmp_path / "sample_001.db"
        self._create_sample_db(db_path)
        tables = _get_tables(db_path)
        expected = {
            "raw_variants",
            "annotated_variants",
            "findings",
            "qc_metrics",
            "sample_metadata",
            "apoe_gate",
            "tags",
            "variant_tags",
            "haplogroup_assignments",
            "watched_variants",
        }
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"

    def test_raw_variants_columns(self, tmp_path):
        db_path = tmp_path / "sample_001.db"
        self._create_sample_db(db_path)
        cols = _get_columns(db_path, "raw_variants")
        # Core identity + provenance columns added in v8 (AncestryDNA Plan §10.4b).
        assert cols == [
            "rsid",
            "chrom",
            "pos",
            "genotype",
            "source",
            "concordance",
            "discordant_alt_genotype",
            "alt_rsid",
        ]

    def test_annotated_variants_has_30_plus_columns(self, tmp_path):
        db_path = tmp_path / "sample_001.db"
        self._create_sample_db(db_path)
        cols = _get_columns(db_path, "annotated_variants")
        assert len(cols) >= 30, f"Only {len(cols)} columns, expected 30+"

    def test_annotated_variants_key_columns(self, tmp_path):
        db_path = tmp_path / "sample_001.db"
        self._create_sample_db(db_path)
        cols = _get_columns(db_path, "annotated_variants")
        # Core identity
        for col in ["rsid", "chrom", "pos", "ref", "alt", "genotype", "zygosity"]:
            assert col in cols, f"Missing column: {col}"
        # VEP
        for col in ["gene_symbol", "consequence", "hgvs_protein", "mane_select"]:
            assert col in cols, f"Missing column: {col}"
        # ClinVar
        for col in ["clinvar_significance", "clinvar_review_stars"]:
            assert col in cols, f"Missing column: {col}"
        # gnomAD
        for col in ["gnomad_af_global", "gnomad_af_eur", "rare_flag"]:
            assert col in cols, f"Missing column: {col}"
        # dbNSFP
        for col in ["cadd_phred", "sift_score", "revel"]:
            assert col in cols, f"Missing column: {col}"
        # AlphaMissense
        for col in ["alphamissense_pathogenicity", "alphamissense_class"]:
            assert col in cols, f"Missing column: {col}"
        # Bitmask
        assert "annotation_coverage" in cols

    def test_sample_has_indexes(self, tmp_path):
        db_path = tmp_path / "sample_001.db"
        self._create_sample_db(db_path)
        indexes = _get_indexes(db_path)
        assert "idx_raw_chrom_pos" in indexes
        assert "idx_annot_chrom_pos" in indexes
        assert "idx_annot_gene" in indexes
        assert "idx_annot_coverage" in indexes

    def test_predefined_tags_seeded(self, tmp_path):
        db_path = tmp_path / "sample_001.db"
        self._create_sample_db(db_path)
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT name FROM tags WHERE is_predefined = 1")
        tags = {row[0] for row in cursor.fetchall()}
        conn.close()
        expected = {
            "Review later",
            "Discuss with clinician",
            "False positive",
            "Actionable",
            "Benign override",
        }
        assert expected == tags

    def test_wal_mode_enabled(self, tmp_path):
        db_path = tmp_path / "sample_001.db"
        self._create_sample_db(db_path)
        conn = sqlite3.connect(str(db_path))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"

    def test_findings_table_columns(self, tmp_path):
        db_path = tmp_path / "sample_001.db"
        self._create_sample_db(db_path)
        cols = _get_columns(db_path, "findings")
        for col in [
            "module",
            "evidence_level",
            "gene_symbol",
            "finding_text",
            "diplotype",
            "prs_score",
            "pathway",
            "svg_path",
            "provenance",
            "related_module",
            "related_finding_id",
        ]:
            assert col in cols, f"Missing column: {col}"

    def test_watched_variants_columns(self, tmp_path):
        db_path = tmp_path / "sample_001.db"
        self._create_sample_db(db_path)
        cols = _get_columns(db_path, "watched_variants")
        assert "rsid" in cols
        assert "clinvar_significance_at_watch" in cols

    def test_sample_metadata_single_row(self, tmp_path):
        """sample_metadata uses CHECK(id=1) for single-row enforcement."""
        db_path = tmp_path / "sample_001.db"
        self._create_sample_db(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute("INSERT INTO sample_metadata (id, name) VALUES (1, 'Test Sample')")
        try:
            conn.execute("INSERT INTO sample_metadata (id, name) VALUES (2, 'Another')")
            conn.commit()
            pytest.fail("Should have raised constraint error")
        except sqlite3.IntegrityError:
            pass
        conn.close()

    def test_idempotent_creation(self, tmp_path):
        """create_sample_tables can be called multiple times safely."""
        db_path = tmp_path / "sample_001.db"
        engine = sa.create_engine(f"sqlite:///{db_path}")
        create_sample_tables(engine)
        create_sample_tables(engine)  # Should not raise
        tables = _get_tables(db_path)
        assert "raw_variants" in tables

    def test_findings_has_related_module_index(self, tmp_path):
        db_path = tmp_path / "sample_001.db"
        self._create_sample_db(db_path)
        indexes = _get_indexes(db_path)
        assert "idx_findings_related_module" in indexes


# ── Schema Migration Tests ─────────────────────────────────────────


class TestSchemaMigration:
    """Test that ensure_sample_schema_current() upgrades older DBs."""

    def _create_v3_sample_db(self, db_path: Path) -> sa.Engine:
        """Create a sample DB that simulates a v3 schema (without cross-link columns)."""
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            conn.execute(sa.text("PRAGMA journal_mode=WAL"))
            # Create a minimal findings table without the new columns
            conn.execute(
                sa.text(
                    """CREATE TABLE findings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        module TEXT NOT NULL,
                        category TEXT,
                        evidence_level INTEGER,
                        gene_symbol TEXT,
                        rsid TEXT,
                        finding_text TEXT NOT NULL,
                        phenotype TEXT,
                        conditions TEXT,
                        zygosity TEXT,
                        clinvar_significance TEXT,
                        diplotype TEXT,
                        metabolizer_status TEXT,
                        drug TEXT,
                        haplogroup TEXT,
                        prs_score REAL,
                        prs_percentile REAL,
                        pathway TEXT,
                        pathway_level TEXT,
                        svg_path TEXT,
                        pmid_citations TEXT,
                        detail_json TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )"""
                )
            )
            conn.execute(sa.text("PRAGMA user_version = 3"))
            conn.commit()
        return engine

    def test_upgrade_adds_cross_link_columns(self, tmp_path):
        """v3 → v4 adds related_module and related_finding_id columns."""
        db_path = tmp_path / "sample_001.db"
        engine = self._create_v3_sample_db(db_path)

        # Verify columns are missing before upgrade
        cols_before = _get_columns(db_path, "findings")
        assert "related_module" not in cols_before
        assert "related_finding_id" not in cols_before

        # Run migration
        updated = ensure_sample_schema_current(engine)
        assert updated is True

        # Verify columns exist after upgrade
        cols_after = _get_columns(db_path, "findings")
        assert "related_module" in cols_after
        assert "related_finding_id" in cols_after

    def test_upgrade_creates_related_module_index(self, tmp_path):
        db_path = tmp_path / "sample_001.db"
        engine = self._create_v3_sample_db(db_path)
        ensure_sample_schema_current(engine)
        indexes = _get_indexes(db_path)
        assert "idx_findings_related_module" in indexes

    def test_upgrade_stamps_v4(self, tmp_path):
        db_path = tmp_path / "sample_001.db"
        engine = self._create_v3_sample_db(db_path)
        ensure_sample_schema_current(engine)
        conn = sqlite3.connect(str(db_path))
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert version == SAMPLE_SCHEMA_VERSION

    def test_upgrade_preserves_existing_data(self, tmp_path):
        """Existing findings are preserved during column addition."""
        db_path = tmp_path / "sample_001.db"
        engine = self._create_v3_sample_db(db_path)

        # Insert a finding before upgrade
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO findings (module, finding_text, evidence_level) "
                    "VALUES ('cancer', 'BRCA1 Pathogenic', 4)"
                )
            )

        ensure_sample_schema_current(engine)

        # Verify data preserved with new columns defaulting to NULL
        with engine.connect() as conn:
            row = conn.execute(sa.text("SELECT * FROM findings WHERE id = 1")).fetchone()
            assert row is not None
            # Access by column index — related_module and related_finding_id
            # should be NULL
            col_names = _get_columns(db_path, "findings")
            rm_idx = col_names.index("related_module")
            rf_idx = col_names.index("related_finding_id")
            assert row[rm_idx] is None
            assert row[rf_idx] is None

    def test_upgrade_idempotent(self, tmp_path):
        """Running ensure_sample_schema_current twice is safe."""
        db_path = tmp_path / "sample_001.db"
        engine = self._create_v3_sample_db(db_path)
        ensure_sample_schema_current(engine)
        # Second call should detect version is current and return False
        updated = ensure_sample_schema_current(engine)
        assert updated is False

    def _create_v5_sample_db(self, db_path: Path) -> sa.Engine:
        """Create a sample DB at v5 (has findings cross-links but no liftover columns)."""
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            conn.execute(sa.text("PRAGMA journal_mode=WAL"))
            conn.execute(
                sa.text(
                    """CREATE TABLE annotated_variants (
                        rsid TEXT PRIMARY KEY,
                        chrom TEXT NOT NULL,
                        pos INTEGER NOT NULL,
                        ref TEXT,
                        alt TEXT,
                        genotype TEXT,
                        annotation_coverage INTEGER
                    )"""
                )
            )
            conn.execute(sa.text("PRAGMA user_version = 5"))
            conn.commit()
        return engine

    def test_upgrade_v5_adds_liftover_columns(self, tmp_path):
        """v5 → v6 adds chrom_grch38 and pos_grch38 columns to annotated_variants."""
        db_path = tmp_path / "sample_001.db"
        engine = self._create_v5_sample_db(db_path)

        cols_before = _get_columns(db_path, "annotated_variants")
        assert "chrom_grch38" not in cols_before
        assert "pos_grch38" not in cols_before

        updated = ensure_sample_schema_current(engine)
        assert updated is True

        cols_after = _get_columns(db_path, "annotated_variants")
        assert "chrom_grch38" in cols_after
        assert "pos_grch38" in cols_after

    def _create_v6_sample_db(self, db_path: Path) -> sa.Engine:
        """Create a sample DB at v6 (has liftover columns but no watched_variants table)."""
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            conn.execute(sa.text("PRAGMA journal_mode=WAL"))
            conn.execute(
                sa.text(
                    """CREATE TABLE annotated_variants (
                        rsid TEXT PRIMARY KEY,
                        chrom TEXT NOT NULL,
                        pos INTEGER NOT NULL,
                        ref TEXT,
                        alt TEXT,
                        genotype TEXT,
                        annotation_coverage INTEGER,
                        chrom_grch38 TEXT,
                        pos_grch38 INTEGER
                    )"""
                )
            )
            conn.execute(sa.text("PRAGMA user_version = 6"))
            conn.commit()
        return engine

    def test_upgrade_v6_adds_watched_variants_table(self, tmp_path):
        """v6 → v7 adds watched_variants table for VUS tracking (P4-21g)."""
        db_path = tmp_path / "sample_001.db"
        engine = self._create_v6_sample_db(db_path)

        tables_before = _get_tables(db_path)
        assert "watched_variants" not in tables_before

        updated = ensure_sample_schema_current(engine)
        assert updated is True

        tables_after = _get_tables(db_path)
        assert "watched_variants" in tables_after

    def test_upgrade_v6_watched_variants_columns(self, tmp_path):
        """watched_variants table has correct columns after v6 → v7 upgrade."""
        db_path = tmp_path / "sample_001.db"
        engine = self._create_v6_sample_db(db_path)

        ensure_sample_schema_current(engine)

        cols = _get_columns(db_path, "watched_variants")
        assert "rsid" in cols
        assert "watched_at" in cols
        assert "clinvar_significance_at_watch" in cols
        assert "notes" in cols

    def test_upgrade_v6_stamps_current(self, tmp_path):
        """Schema version is bumped to current after upgrading a v6 DB."""
        db_path = tmp_path / "sample_001.db"
        engine = self._create_v6_sample_db(db_path)

        ensure_sample_schema_current(engine)

        conn = sqlite3.connect(str(db_path))
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert version == SAMPLE_SCHEMA_VERSION

    def test_watched_variants_insert_and_read(self, tmp_path):
        """Can insert and query watched_variants after migration."""
        db_path = tmp_path / "sample_001.db"
        engine = self._create_v6_sample_db(db_path)
        ensure_sample_schema_current(engine)

        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO watched_variants (rsid, clinvar_significance_at_watch, notes) "
                    "VALUES (:rsid, :sig, :notes)"
                ),
                {"rsid": "rs80357906", "sig": "Uncertain significance", "notes": "BRCA2 VUS"},
            )

        with engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT rsid, clinvar_significance_at_watch, notes, watched_at "
                    "FROM watched_variants"
                )
            ).fetchone()
            assert row[0] == "rs80357906"
            assert row[1] == "Uncertain significance"
            assert row[2] == "BRCA2 VUS"
            assert row[3] is not None, "watched_at should have a default timestamp"

    def _create_v10_sample_db(self, db_path: Path) -> sa.Engine:
        """Create a sample DB at v10 (findings table without the provenance column)."""
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            conn.execute(sa.text("PRAGMA journal_mode=WAL"))
            conn.execute(
                sa.text(
                    """CREATE TABLE findings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        module TEXT NOT NULL,
                        category TEXT,
                        finding_text TEXT NOT NULL,
                        rsid TEXT,
                        detail_json TEXT,
                        related_module TEXT,
                        related_finding_id INTEGER
                    )"""
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO findings (module, finding_text, rsid) "
                    "VALUES ('cancer', 'BRCA1 Pathogenic', 'rs80357906')"
                )
            )
            conn.execute(sa.text("PRAGMA user_version = 10"))
            conn.commit()
        return engine

    def test_upgrade_v10_adds_provenance_column(self, tmp_path):
        """v10 → v11 adds the provenance column to findings."""
        db_path = tmp_path / "sample_001.db"
        engine = self._create_v10_sample_db(db_path)

        assert "provenance" not in _get_columns(db_path, "findings")

        updated = ensure_sample_schema_current(engine)
        assert updated is True
        assert "provenance" in _get_columns(db_path, "findings")

    def test_upgrade_v10_preserves_existing_findings(self, tmp_path):
        """The v10 → v11 upgrade keeps existing finding rows, NULLing provenance."""
        db_path = tmp_path / "sample_001.db"
        engine = self._create_v10_sample_db(db_path)
        ensure_sample_schema_current(engine)

        with engine.connect() as conn:
            row = conn.execute(sa.text("SELECT module, rsid, provenance FROM findings")).fetchone()
        assert row[0] == "cancer"
        assert row[1] == "rs80357906"
        assert row[2] is None, "pre-existing rows have NULL provenance until re-annotation"

    def _create_v11_sample_db(self, db_path: Path) -> sa.Engine:
        """Create a sample DB at v11 without AlphaMissense columns."""
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            conn.execute(sa.text("PRAGMA journal_mode=WAL"))
            conn.execute(
                sa.text(
                    """CREATE TABLE annotated_variants (
                        rsid TEXT PRIMARY KEY,
                        chrom TEXT NOT NULL,
                        pos INTEGER NOT NULL,
                        ref TEXT,
                        alt TEXT,
                        genotype TEXT,
                        revel REAL,
                        annotation_coverage INTEGER
                    )"""
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO annotated_variants "
                    "(rsid, chrom, pos, ref, alt, genotype, revel, annotation_coverage) "
                    "VALUES ('rs1801133', '1', 11856378, 'G', 'A', 'AG', 0.75, 15)"
                )
            )
            conn.execute(sa.text("PRAGMA user_version = 11"))
            conn.commit()
        return engine

    def test_upgrade_v11_adds_alphamissense_columns(self, tmp_path):
        """v11 → v12 adds AlphaMissense context columns to annotated_variants."""
        db_path = tmp_path / "sample_001.db"
        engine = self._create_v11_sample_db(db_path)

        cols_before = _get_columns(db_path, "annotated_variants")
        assert "alphamissense_pathogenicity" not in cols_before
        assert "alphamissense_class" not in cols_before

        updated = ensure_sample_schema_current(engine)
        assert updated is True

        cols_after = _get_columns(db_path, "annotated_variants")
        assert "alphamissense_pathogenicity" in cols_after
        assert "alphamissense_class" in cols_after

    def test_upgrade_v11_preserves_existing_annotated_rows(self, tmp_path):
        """The v11 → v12 upgrade preserves annotated rows and NULLs AlphaMissense fields."""
        db_path = tmp_path / "sample_001.db"
        engine = self._create_v11_sample_db(db_path)
        ensure_sample_schema_current(engine)

        with engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT rsid, revel, alphamissense_pathogenicity, alphamissense_class "
                    "FROM annotated_variants"
                )
            ).fetchone()
        assert row[0] == "rs1801133"
        assert row[1] == 0.75
        assert row[2] is None
        assert row[3] is None
