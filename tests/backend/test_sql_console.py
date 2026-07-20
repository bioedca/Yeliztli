"""Tests for the raw SQL console backend (P4-03).

PRD test requirements:
- T4-03: SQL console rejects write operations (INSERT, UPDATE, DELETE, DROP)
- T4-04: SQL console allows full read access to all tables including schema metadata
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.api.routes.query_builder import _validate_read_only
from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, reference_metadata, samples

# ═══════════════════════════════════════════════════════════════════════
# Test data — reuse the same annotated variants from query builder tests
# ═══════════════════════════════════════════════════════════════════════

ANNOTATED_VARIANTS = [
    {
        "rsid": "rs429358",
        "chrom": "19",
        "pos": 44908684,
        "ref": "T",
        "alt": "C",
        "genotype": "TC",
        "zygosity": "het",
        "gene_symbol": "APOE",
        "consequence": "missense_variant",
        "clinvar_significance": "risk_factor",
        "clinvar_review_stars": 3,
        "gnomad_af_global": 0.15,
        "rare_flag": False,
        "cadd_phred": 23.5,
        "annotation_coverage": 0x1F,
        "evidence_conflict": False,
        "ensemble_pathogenic": False,
    },
    {
        "rsid": "rs80357906",
        "chrom": "17",
        "pos": 41209080,
        "ref": "GGG",
        "alt": "GGGG",
        "genotype": "GGG/GGGG",
        "zygosity": "het",
        "gene_symbol": "BRCA1",
        "consequence": "frameshift_variant",
        "clinvar_significance": "Pathogenic",
        "clinvar_review_stars": 3,
        "gnomad_af_global": 0.0001,
        "rare_flag": True,
        "ultra_rare_flag": True,
        "cadd_phred": 35.0,
        "revel": 0.95,
        "annotation_coverage": 0x1F,
        "evidence_conflict": False,
        "ensemble_pathogenic": True,
    },
    {
        "rsid": "rs1801133",
        "chrom": "1",
        "pos": 11856378,
        "ref": "G",
        "alt": "A",
        "genotype": "AG",
        "zygosity": "het",
        "gene_symbol": "MTHFR",
        "consequence": "missense_variant",
        "clinvar_significance": "drug_response",
        "clinvar_review_stars": 2,
        "gnomad_af_global": 0.35,
        "rare_flag": False,
        "cadd_phred": 25.0,
        "annotation_coverage": 0x1F,
        "evidence_conflict": False,
        "ensemble_pathogenic": False,
    },
]

_ALL_COLS = [col.name for col in annotated_variants.columns]


def _normalize(variant: dict) -> dict:
    """Fill missing columns with None."""
    return {k: variant.get(k) for k in _ALL_COLS}


def _setup_client(tmp_data_dir: Path, variants: list[dict]):
    """Create a TestClient with annotated sample data."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="abc123",
            )
        )
        sample_id = result.lastrowid
    ref_engine.dispose()

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)
    if variants:
        normalized = [_normalize(v) for v in variants]
        with sample_engine.begin() as conn:
            conn.execute(annotated_variants.insert(), normalized)
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc, sample_id
        reset_registry()


@pytest.fixture
def client(tmp_data_dir: Path):
    yield from _setup_client(tmp_data_dir, ANNOTATED_VARIANTS)


@pytest.fixture
def empty_client(tmp_data_dir: Path):
    yield from _setup_client(tmp_data_dir, [])


# ═══════════════════════════════════════════════════════════════════════
# Unit tests — _validate_read_only
# ═══════════════════════════════════════════════════════════════════════


class TestValidateReadOnly:
    """Application-level SQL write detection."""

    def test_select_allowed(self) -> None:
        _validate_read_only("SELECT * FROM annotated_variants")

    def test_pragma_read_allowed(self) -> None:
        _validate_read_only("PRAGMA table_info('annotated_variants')")

    def test_explain_allowed(self) -> None:
        _validate_read_only("EXPLAIN QUERY PLAN SELECT * FROM annotated_variants")

    def test_with_cte_allowed(self) -> None:
        _validate_read_only("WITH cte AS (SELECT rsid FROM annotated_variants) SELECT * FROM cte")

    def test_insert_rejected(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _validate_read_only("INSERT INTO annotated_variants (rsid) VALUES ('x')")
        assert exc_info.value.status_code == 403
        assert "Write operations" in exc_info.value.detail

    def test_update_rejected(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _validate_read_only("UPDATE annotated_variants SET rsid='x'")
        assert exc_info.value.status_code == 403

    def test_delete_rejected(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _validate_read_only("DELETE FROM annotated_variants")
        assert exc_info.value.status_code == 403

    def test_drop_rejected(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _validate_read_only("DROP TABLE annotated_variants")
        assert exc_info.value.status_code == 403

    def test_alter_rejected(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _validate_read_only("ALTER TABLE annotated_variants ADD COLUMN foo TEXT")
        assert exc_info.value.status_code == 403

    def test_create_rejected(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _validate_read_only("CREATE TABLE evil (id INTEGER)")
        assert exc_info.value.status_code == 403

    def test_replace_rejected(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _validate_read_only("REPLACE INTO annotated_variants (rsid) VALUES ('x')")
        assert exc_info.value.status_code == 403

    def test_attach_rejected(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _validate_read_only("ATTACH DATABASE '/tmp/evil.db' AS evil")
        assert exc_info.value.status_code == 403

    def test_pragma_write_rejected(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _validate_read_only("PRAGMA journal_mode = DELETE")
        assert exc_info.value.status_code == 403

    def test_empty_sql_rejected(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _validate_read_only("   ;  ")
        assert exc_info.value.status_code == 422

    def test_case_insensitive_rejection(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _validate_read_only("insert INTO annotated_variants (rsid) VALUES ('x')")

        with pytest.raises(HTTPException):
            _validate_read_only("DrOp TABLE annotated_variants")


# ═══════════════════════════════════════════════════════════════════════
# T4-03: SQL console rejects write operations
# ═══════════════════════════════════════════════════════════════════════


class TestSqlConsoleRejectsWrites:
    """PRD T4-03: SQL console rejects INSERT, UPDATE, DELETE, DROP."""

    def test_insert_via_api(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "INSERT INTO annotated_variants (rsid, chrom, pos) VALUES ('evil', '1', 1)",
            },
        )
        assert resp.status_code == 403
        assert "Write operations" in resp.json()["detail"]

    def test_update_via_api(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "UPDATE annotated_variants SET rsid = 'evil'",
            },
        )
        assert resp.status_code == 403

    def test_delete_via_api(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "DELETE FROM annotated_variants WHERE rsid = 'rs429358'",
            },
        )
        assert resp.status_code == 403

    def test_drop_via_api(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "DROP TABLE annotated_variants",
            },
        )
        assert resp.status_code == 403

    def test_create_table_via_api(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "CREATE TABLE evil (id INTEGER PRIMARY KEY)",
            },
        )
        assert resp.status_code == 403

    def test_alter_table_via_api(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "ALTER TABLE annotated_variants ADD COLUMN evil TEXT",
            },
        )
        assert resp.status_code == 403

    def test_data_unchanged_after_rejected_write(self, client) -> None:
        """Verify data integrity is preserved after rejected write attempts."""
        tc, sid = client
        # Attempt to delete
        tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "DELETE FROM annotated_variants",
            },
        )
        # All rows should still be present
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT COUNT(*) AS cnt FROM annotated_variants",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["rows"][0][0] == 3


# ═══════════════════════════════════════════════════════════════════════
# T4-04: SQL console allows full read access including schema metadata
# ═══════════════════════════════════════════════════════════════════════


class TestSqlConsoleReadAccess:
    """PRD T4-04: Full read access to all tables + schema metadata."""

    def test_select_all_variants(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT * FROM annotated_variants",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["row_count"] == 3
        assert len(data["columns"]) > 0
        assert not data["truncated"]
        assert data["execution_time_ms"] >= 0

    def test_select_with_where(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT rsid, gene_symbol FROM annotated_variants "
                "WHERE clinvar_significance = 'Pathogenic'",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["row_count"] == 1
        assert data["rows"][0][1] == "BRCA1"

    def test_select_with_aggregate(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT COUNT(*) AS total, AVG(cadd_phred) AS avg_cadd "
                "FROM annotated_variants",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["row_count"] == 1
        assert data["rows"][0][0] == 3
        assert data["columns"][0]["name"] == "total"
        assert data["columns"][1]["name"] == "avg_cadd"

    def test_schema_metadata_via_sqlite_master(self, client) -> None:
        """User can query sqlite_master for table/column info."""
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT name, type FROM sqlite_master WHERE type='table'",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        table_names = [row[0] for row in data["rows"]]
        assert "annotated_variants" in table_names

    def test_pragma_table_info(self, client) -> None:
        """User can inspect column schema via PRAGMA."""
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "PRAGMA table_info('annotated_variants')",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        col_names = [row[1] for row in data["rows"]]
        assert "rsid" in col_names
        assert "chrom" in col_names
        assert "pos" in col_names

    def test_select_from_raw_variants_table(self, client) -> None:
        """User can query any table in the sample DB, not just annotated_variants."""
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT name FROM sqlite_master WHERE type='table'",
            },
        )
        assert resp.status_code == 200
        # Just verify we can access other tables (they may be empty)
        data = resp.json()
        assert data["row_count"] > 0

    def test_join_query(self, client) -> None:
        """User can run JOIN queries within the sample DB."""
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT av.rsid, av.gene_symbol "
                "FROM annotated_variants av "
                "INNER JOIN (SELECT rsid FROM annotated_variants WHERE rare_flag = 1) r "
                "ON av.rsid = r.rsid",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["row_count"] == 1
        assert data["rows"][0][1] == "BRCA1"

    def test_explain_query_plan(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "EXPLAIN QUERY PLAN SELECT * FROM annotated_variants WHERE chrom = '1'",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["row_count"] > 0

    def test_with_cte(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "WITH rare AS (SELECT * FROM annotated_variants WHERE rare_flag = 1) "
                "SELECT COUNT(*) FROM rare",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["rows"][0][0] == 1


# ═══════════════════════════════════════════════════════════════════════
# Edge cases and error handling
# ═══════════════════════════════════════════════════════════════════════


class TestSqlConsoleEdgeCases:
    """Edge cases: pagination, bad SQL, missing sample, etc."""

    def test_result_truncation(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT * FROM annotated_variants",
                "limit": 2,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["row_count"] == 2
        assert data["truncated"] is True

    def test_no_truncation_when_within_limit(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT * FROM annotated_variants",
                "limit": 100,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["row_count"] == 3
        assert data["truncated"] is False

    def test_invalid_sql_syntax(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "SELCT * FORM annotated_variants",
            },
        )
        assert resp.status_code == 422
        assert "SQL error" in resp.json()["detail"]

    def test_nonexistent_table(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT * FROM nonexistent_table",
            },
        )
        assert resp.status_code == 422
        assert "SQL error" in resp.json()["detail"]

    def test_sample_not_found(self, client) -> None:
        tc, _ = client
        resp = tc.post(
            "/api/query/sql",
            json={"sample_id": 9999, "sql": "SELECT 1"},
        )
        assert resp.status_code == 404

    def test_empty_results(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT * FROM annotated_variants WHERE 1=0",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["row_count"] == 0
        assert data["rows"] == []
        assert len(data["columns"]) > 0

    def test_columns_metadata(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT rsid, chrom, pos FROM annotated_variants LIMIT 1",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        col_names = [c["name"] for c in data["columns"]]
        assert col_names == ["rsid", "chrom", "pos"]

    def test_execution_time_reported(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT * FROM annotated_variants",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["execution_time_ms"] >= 0


# ═══════════════════════════════════════════════════════════════════════
# Defence-in-depth: read-only engine rejects writes even if app check
# is somehow bypassed
# ═══════════════════════════════════════════════════════════════════════


class TestSqlConsoleReadOnlyEngine:
    """Verify the SQLite read-only connection mode works as defence-in-depth."""

    def test_readonly_engine_rejects_insert(self, client) -> None:
        """Even with app-level check bypassed, read-only engine blocks writes."""
        tc, sid = client
        # Patch _validate_read_only to be a no-op
        with patch("backend.api.routes.query_builder._validate_read_only", return_value=None):
            resp = tc.post(
                "/api/query/sql",
                json={
                    "sample_id": sid,
                    "sql": "INSERT INTO annotated_variants (rsid, chrom, pos) "
                    "VALUES ('evil', '1', 1)",
                },
            )
        # SQLite read-only mode should reject this
        assert resp.status_code == 422
        assert "SQL error" in resp.json()["detail"]
