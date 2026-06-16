"""Tests for the export results backend (P4-05).

Covers CSV, TSV, JSON, and VCF export from both the query builder
and raw SQL console endpoints.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, reference_metadata, samples

# ── Test data ────────────────────────────────────────────────────────

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
        "pos": 43091983,
        "ref": "CTC",
        "alt": "C",
        "genotype": "TC",
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


# ── Fixtures ─────────────────────────────────────────────────────────


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


# #1000: a result set that exceeds the old 10,000-row SQL-export cap, so a
# regression to a capped fetchmany silently drops the tail.
_LARGE_ROW_COUNT = 10_500


@pytest.fixture
def large_client(tmp_data_dir: Path):
    variants = [{"rsid": f"rs{i}", "chrom": "1", "pos": i + 1} for i in range(_LARGE_ROW_COUNT)]
    yield from _setup_client(tmp_data_dir, variants)


# ── Helper ───────────────────────────────────────────────────────────

# Reusable filter that matches all rows.
ALL_FILTER = {"combinator": "and", "rules": []}

# Filter matching only Pathogenic variants.
PATHOGENIC_FILTER = {
    "combinator": "and",
    "rules": [
        {"field": "clinvar_significance", "operator": "=", "value": "Pathogenic"},
    ],
}


# ══════════════════════════════════════════════════════════════════════
# Query builder export tests
# ══════════════════════════════════════════════════════════════════════


class TestExportQueryCSV:
    """POST /api/export/query with format=csv."""

    def test_csv_export(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/query",
            json={"sample_id": sid, "filter": ALL_FILTER, "format": "csv"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "attachment" in resp.headers["content-disposition"]
        assert ".csv" in resp.headers["content-disposition"]

        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == 3
        assert "rsid" in reader.fieldnames
        assert "chrom" in reader.fieldnames

    def test_csv_export_filtered(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/query",
            json={"sample_id": sid, "filter": PATHOGENIC_FILTER, "format": "csv"},
        )
        assert resp.status_code == 200
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["gene_symbol"] == "BRCA1"


class TestExportQueryTSV:
    """POST /api/export/query with format=tsv."""

    def test_tsv_export(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/query",
            json={"sample_id": sid, "filter": ALL_FILTER, "format": "tsv"},
        )
        assert resp.status_code == 200
        assert "tab-separated" in resp.headers["content-type"]
        assert ".tsv" in resp.headers["content-disposition"]

        reader = csv.DictReader(io.StringIO(resp.text), delimiter="\t")
        rows = list(reader)
        assert len(rows) == 3


class TestExportQueryJSON:
    """POST /api/export/query with format=json."""

    def test_json_export(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/query",
            json={"sample_id": sid, "filter": ALL_FILTER, "format": "json"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        assert ".json" in resp.headers["content-disposition"]

        data = json.loads(resp.text)
        assert isinstance(data, list)
        assert len(data) == 3
        # Verify structure
        assert "rsid" in data[0]
        assert "chrom" in data[0]

    def test_json_export_filtered(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/query",
            json={"sample_id": sid, "filter": PATHOGENIC_FILTER, "format": "json"},
        )
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert len(data) == 1
        assert data[0]["gene_symbol"] == "BRCA1"


class TestExportQueryVCF:
    """POST /api/export/query with format=vcf."""

    def test_vcf_export(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/query",
            json={"sample_id": sid, "filter": ALL_FILTER, "format": "vcf"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert ".vcf" in resp.headers["content-disposition"]

        lines = resp.text.strip().split("\n")
        # Should have meta lines starting with ##
        meta_lines = [ln for ln in lines if ln.startswith("##")]
        assert len(meta_lines) > 0
        assert any("fileformat=VCFv4.2" in ln for ln in meta_lines)

        # Should have a header line
        header_lines = [ln for ln in lines if ln.startswith("#CHROM")]
        assert len(header_lines) == 1

        # Data lines: assert structure + REF/ALT/GT, not just presence — a
        # dropped variant, swapped REF↔ALT, or mis-encoded GT would otherwise
        # slip through a bare "len >= 1" check.
        data_lines = [ln for ln in lines if not ln.startswith("#")]
        assert len(data_lines) == 3  # all three seeded variants exported (none dropped)

        rows: dict[str, tuple[str, str, str, str, str]] = {}
        for ln in data_lines:
            cols = ln.split("\t")
            assert len(cols) == 10, f"VCF data line is not 10 columns: {ln!r}"
            chrom, pos, vid, ref, alt, _qual, _filt, _info, fmt, gt = cols
            # FORMAT declares GT; the sample column carries a valid GT encoding.
            assert fmt == "GT", f"FORMAT column must be GT: {ln!r}"
            assert gt in {"0/0", "0/1", "1/1", "0", "1", "1/2", "./."}, (
                f"unexpected GT encoding: {gt!r}"
            )
            rows[vid] = (chrom, pos, ref, alt, gt)

        # No variant silently dropped.
        assert set(rows) == {"rs429358", "rs80357906", "rs1801133"}
        # #560: REF/ALT/GT are REFERENCE-ALIGNED from the annotation-resolved
        # ref/alt + zygosity, NOT inferred from the raw genotype call. GT is
        # allele-indexed against REF (0=REF), so a swapped REF↔ALT or mis-encoded
        # GT is caught here.
        assert rows["rs429358"][2:] == ("T", "C", "0/1")  # ref=T alt=C, het
        # rs1801133: annotation ref=G alt=A — REF/ALT follow the reference, NOT
        # the raw call's "AG" string order (which previously gave a wrong A/G).
        assert rows["rs1801133"][2:] == ("G", "A", "0/1")
        # rs80357906: a frameshift indel — emit the annotated indel ref/alt
        # (CTC/C), not the call-inferred single bases (the old T/C bug).
        assert rows["rs80357906"][2:] == ("CTC", "C", "0/1")
        # CHROM/POS round-trip for one variant.
        assert rows["rs429358"][:2] == ("19", "44908684")


# ══════════════════════════════════════════════════════════════════════
# SQL console export tests
# ══════════════════════════════════════════════════════════════════════


class TestExportSqlCSV:
    """POST /api/export/sql with format=csv."""

    def test_csv_export(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT rsid, chrom, pos FROM annotated_variants",
                "format": "csv",
            },
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")

        reader = csv.reader(io.StringIO(resp.text))
        rows = list(reader)
        # Header + 3 data rows
        assert len(rows) == 4
        assert rows[0] == ["rsid", "chrom", "pos"]


class TestExportSqlTSV:
    """POST /api/export/sql with format=tsv."""

    def test_tsv_export(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT rsid, gene_symbol FROM annotated_variants",
                "format": "tsv",
            },
        )
        assert resp.status_code == 200
        assert "tab-separated" in resp.headers["content-type"]

        reader = csv.reader(io.StringIO(resp.text), delimiter="\t")
        rows = list(reader)
        assert len(rows) == 4  # Header + 3 data rows
        assert rows[0] == ["rsid", "gene_symbol"]


class TestExportSqlJSON:
    """POST /api/export/sql with format=json."""

    def test_json_export(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT rsid, chrom, pos FROM annotated_variants",
                "format": "json",
            },
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")

        data = json.loads(resp.text)
        assert isinstance(data, list)
        assert len(data) == 3
        assert "rsid" in data[0]
        assert "chrom" in data[0]


# ══════════════════════════════════════════════════════════════════════
# Security & error tests
# ══════════════════════════════════════════════════════════════════════


class TestExportSecurity:
    """Security tests for export endpoints."""

    def test_sql_rejects_write_operations(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/sql",
            json={
                "sample_id": sid,
                "sql": "DROP TABLE annotated_variants",
                "format": "csv",
            },
        )
        assert resp.status_code == 403
        assert "write" in resp.json()["detail"].lower()

    def test_sql_rejects_insert(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/sql",
            json={
                "sample_id": sid,
                "sql": "INSERT INTO annotated_variants (rsid) VALUES ('evil')",
                "format": "csv",
            },
        )
        assert resp.status_code == 403

    def test_sql_rejects_delete(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/sql",
            json={
                "sample_id": sid,
                "sql": "DELETE FROM annotated_variants",
                "format": "csv",
            },
        )
        assert resp.status_code == 403


class TestExportErrors:
    """Error handling tests."""

    def test_invalid_format_query(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/query",
            json={"sample_id": sid, "filter": ALL_FILTER, "format": "pdf"},
        )
        assert resp.status_code == 422

    def test_invalid_format_sql(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT 1",
                "format": "vcf",
            },
        )
        assert resp.status_code == 422

    def test_missing_sample_query(self, client) -> None:
        tc, _ = client
        resp = tc.post(
            "/api/export/query",
            json={"sample_id": 999, "filter": ALL_FILTER, "format": "csv"},
        )
        assert resp.status_code == 404

    def test_missing_sample_sql(self, client) -> None:
        tc, _ = client
        resp = tc.post(
            "/api/export/sql",
            json={
                "sample_id": 999,
                "sql": "SELECT 1",
                "format": "csv",
            },
        )
        assert resp.status_code == 404

    def test_no_annotated_variants_query(self, empty_client) -> None:
        tc, sid = empty_client
        resp = tc.post(
            "/api/export/query",
            json={"sample_id": sid, "filter": ALL_FILTER, "format": "csv"},
        )
        assert resp.status_code == 422
        assert "annotated variants" in resp.json()["detail"].lower()


class TestExportEmptyResults:
    """Export with filters that match zero rows should still produce valid files."""

    def test_empty_csv(self, client) -> None:
        tc, sid = client
        no_match_filter = {
            "combinator": "and",
            "rules": [
                {"field": "gene_symbol", "operator": "=", "value": "NONEXISTENT_GENE"},
            ],
        }
        resp = tc.post(
            "/api/export/query",
            json={"sample_id": sid, "filter": no_match_filter, "format": "csv"},
        )
        assert resp.status_code == 200
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == 0
        # Headers should still be present
        assert "rsid" in reader.fieldnames

    def test_empty_json(self, client) -> None:
        tc, sid = client
        no_match_filter = {
            "combinator": "and",
            "rules": [
                {"field": "gene_symbol", "operator": "=", "value": "NONEXISTENT_GENE"},
            ],
        }
        resp = tc.post(
            "/api/export/query",
            json={"sample_id": sid, "filter": no_match_filter, "format": "json"},
        )
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert data == []

    def test_empty_tsv(self, client) -> None:
        tc, sid = client
        no_match_filter = {
            "combinator": "and",
            "rules": [
                {"field": "gene_symbol", "operator": "=", "value": "NONEXISTENT_GENE"},
            ],
        }
        resp = tc.post(
            "/api/export/query",
            json={"sample_id": sid, "filter": no_match_filter, "format": "tsv"},
        )
        assert resp.status_code == 200
        reader = csv.DictReader(io.StringIO(resp.text), delimiter="\t")
        rows = list(reader)
        assert len(rows) == 0
        assert "rsid" in reader.fieldnames

    def test_empty_sql_csv(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT rsid, chrom FROM annotated_variants WHERE 1=0",
                "format": "csv",
            },
        )
        assert resp.status_code == 200
        reader = csv.reader(io.StringIO(resp.text))
        rows = list(reader)
        # Should have header row only
        assert len(rows) == 1
        assert rows[0] == ["rsid", "chrom"]

    def test_empty_sql_json(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT rsid FROM annotated_variants WHERE 1=0",
                "format": "json",
            },
        )
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert data == []


# ══════════════════════════════════════════════════════════════════════
# #1000: SQL export streams the complete result set (no silent row cap)
# ══════════════════════════════════════════════════════════════════════


class TestExportSqlUncapped:
    """/api/export/sql must stream every row, not silently truncate at 10,000.

    The old `fetchmany(SQL_EXPORT_MAX_ROWS)` capped the download at 10,000 rows
    with no warning/error/log — a console-truncated user funneled here for the
    full data silently lost the tail. The export now streams uncapped, mirroring
    /api/export/query (whose 'No variant silently dropped' invariant this matches).
    """

    def test_csv_export_streams_all_rows_beyond_old_cap(self, large_client) -> None:
        tc, sid = large_client
        resp = tc.post(
            "/api/export/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT rsid, chrom, pos FROM annotated_variants",
                "format": "csv",
            },
        )
        assert resp.status_code == 200
        rows = list(csv.reader(io.StringIO(resp.text)))
        assert rows[0] == ["rsid", "chrom", "pos"]
        # 1 header + every data row; a re-introduced 10,000 cap would truncate.
        assert len(rows) - 1 == _LARGE_ROW_COUNT
        assert len(rows) - 1 > 10_000  # explicitly past the retired cap

    def test_json_export_streams_all_rows_beyond_old_cap(self, large_client) -> None:
        tc, sid = large_client
        resp = tc.post(
            "/api/export/sql",
            json={
                "sample_id": sid,
                "sql": "SELECT rsid FROM annotated_variants",
                "format": "json",
            },
        )
        assert resp.status_code == 200
        data = json.loads(resp.text)
        assert len(data) == _LARGE_ROW_COUNT
        assert len(data) > 10_000
