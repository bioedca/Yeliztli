"""Tests for ENCODE cCREs data loader (P2-27).

Covers:
- BED line parsing (valid records, skip reasons)
- Table creation and indexing
- Bulk loading from BED file
- Region-based queries (overlap logic)
- Summary counts by classification
- is_loaded check
- Edge cases (empty file, comment lines, gzipped input)
- API route tests (region, summary, status endpoints)
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from backend.annotation.encode_ccres import (
    CCRE_CLASSIFICATIONS,
    CCREResult,
    _normalize_chrom,
    _parse_ccre_bed_line,
    create_encode_ccres_tables,
    get_ccre_summary,
    is_loaded,
    iter_ccre_bed,
    load_encode_ccres,
    query_ccres_by_region,
)

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def ccres_engine() -> sa.Engine:
    """In-memory SQLite engine for ENCODE cCREs."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_encode_ccres_tables(engine)
    return engine


SAMPLE_BED_LINES = [
    "chr1\t10000\t10500\tEH38E1000001\t.\t.\t.\t.\t.\tPLS\n",
    "chr1\t20000\t20800\tEH38E1000002\t.\t.\t.\t.\t.\tpELS\n",
    "chr1\t30000\t30600\tEH38E1000003\t.\t.\t.\t.\t.\tdELS\n",
    "chr2\t50000\t50400\tEH38E1000004\t.\t.\t.\t.\t.\tCTCF-only\n",
    "chrX\t100000\t100300\tEH38E1000005\t.\t.\t.\t.\t.\tDNase-H3K4me3\n",
]


@pytest.fixture
def sample_bed_file(tmp_path: Path) -> Path:
    """Create a sample BED file for testing."""
    bed_path = tmp_path / "test_ccres.bed"
    content = "# ENCODE cCREs test data\ntrack name=cCREs\n" + "".join(SAMPLE_BED_LINES)
    bed_path.write_text(content)
    return bed_path


@pytest.fixture
def sample_bed_gz(tmp_path: Path) -> Path:
    """Create a gzipped sample BED file."""
    gz_path = tmp_path / "test_ccres.bed.gz"
    content = "".join(SAMPLE_BED_LINES)
    with gzip.open(gz_path, "wt") as f:
        f.write(content)
    return gz_path


@pytest.fixture
def loaded_engine(ccres_engine: sa.Engine, sample_bed_file: Path) -> sa.Engine:
    """Engine with sample cCREs loaded."""
    load_encode_ccres(sample_bed_file, ccres_engine)
    return ccres_engine


# ── Tests: Chromosome normalization ─────────────────────────────────


class TestNormalizeChrom:
    def test_strip_chr_prefix(self):
        assert _normalize_chrom("chr1") == "1"

    def test_uppercase(self):
        assert _normalize_chrom("chrx") == "X"

    def test_no_prefix(self):
        assert _normalize_chrom("22") == "22"

    def test_invalid_chrom(self):
        assert _normalize_chrom("chrM") is None
        assert _normalize_chrom("chr0") is None
        assert _normalize_chrom("chrUn") is None

    def test_mt_invalid(self):
        # MT not in ENCODE cCREs valid chroms
        assert _normalize_chrom("MT") is None


# ── Tests: BED line parsing ──────────────────────────────────────────


class TestParseBedLine:
    def test_valid_10_column_line(self):
        line = "chr1\t10000\t10500\tEH38E1000001\t.\t.\t.\t.\t.\tPLS"
        record, skip = _parse_ccre_bed_line(line)
        assert skip is None
        assert record is not None
        assert record.chrom == "1"
        assert record.start_pos == 10000
        assert record.end_pos == 10500
        assert record.accession == "EH38E1000001"
        assert record.ccre_class == "PLS"

    def test_all_classifications(self):
        for cls in CCRE_CLASSIFICATIONS:
            line = f"chr1\t100\t200\tEH38E0000001\t.\t.\t.\t.\t.\t{cls}"
            record, skip = _parse_ccre_bed_line(line)
            assert record is not None, f"Failed for class {cls}"
            assert record.ccre_class == cls

    def test_invalid_chrom(self):
        line = "chrM\t100\t200\tEH38E0000001\t.\t.\t.\t.\t.\tPLS"
        record, skip = _parse_ccre_bed_line(line)
        assert record is None
        assert skip == "invalid_chrom"

    def test_malformed_too_few_columns(self):
        line = "chr1\t100\t200\tEH38E0000001"
        record, skip = _parse_ccre_bed_line(line)
        assert record is None
        assert skip == "malformed"

    def test_malformed_bad_coordinates(self):
        line = "chr1\tabc\t200\tEH38E0000001\t.\t.\t.\t.\t.\tPLS"
        record, skip = _parse_ccre_bed_line(line)
        assert record is None
        assert skip == "malformed"

    def test_unknown_class(self):
        line = "chr1\t100\t200\tEH38E0000001\t.\t.\t.\t.\t.\tUNKNOWN"
        record, skip = _parse_ccre_bed_line(line)
        assert record is None
        assert skip == "unknown_class"

    def test_comma_accession_class(self):
        """Test accession,class format (e.g., EH38E1234567,PLS)."""
        line = "chr1\t100\t200\tEH38E0000001,dELS\t.\t.\t.\t.\t.\t"
        record, skip = _parse_ccre_bed_line(line)
        # Column 9 is empty string, so should fall through to comma parsing
        assert record is not None
        assert record.accession == "EH38E0000001"
        assert record.ccre_class == "dELS"

    def test_5_column_format(self):
        """Test simplified 5-column BED with class in column 4."""
        line = "chr1\t100\t200\tEH38E0000001\tPLS"
        record, skip = _parse_ccre_bed_line(line)
        assert record is not None
        assert record.ccre_class == "PLS"

    def test_v3_6_column_format_uses_ccre_accession(self):
        """V3 rows store rDHS in column 4 and the cCRE accession in column 5."""
        line = "chr1\t104896\t105048\tEH38D4327509\tEH38E2776520\tCTCF-only,CTCF-bound"
        record, skip = _parse_ccre_bed_line(line)
        assert skip is None
        assert record is not None
        assert record.chrom == "1"
        assert record.start_pos == 104896
        assert record.end_pos == 105048
        assert record.accession == "EH38E2776520"
        assert record.ccre_class == "CTCF-only"


# ── Tests: BED file iteration ────────────────────────────────────────


class TestIterCcreBed:
    def test_iter_basic(self, sample_bed_file: Path):
        rows = list(iter_ccre_bed(sample_bed_file))
        assert len(rows) == 5
        # First record
        row, stats = rows[0]
        assert row["accession"] == "EH38E1000001"
        assert row["chrom"] == "1"
        assert row["ccre_class"] == "PLS"

    def test_stats_tracking(self, sample_bed_file: Path):
        rows = list(iter_ccre_bed(sample_bed_file))
        _, stats = rows[-1]
        assert stats.records_loaded == 5
        # 2 header lines (comment + track) + 5 data = 7 total
        assert stats.total_lines == 7

    def test_gzipped_file(self, sample_bed_gz: Path):
        rows = list(iter_ccre_bed(sample_bed_gz))
        assert len(rows) == 5

    def test_empty_file(self, tmp_path: Path):
        empty = tmp_path / "empty.bed"
        empty.write_text("")
        rows = list(iter_ccre_bed(empty))
        assert len(rows) == 0

    def test_skip_comments_and_blanks(self, tmp_path: Path):
        bed = tmp_path / "comments.bed"
        bed.write_text(
            "# comment\n"
            "browser position chr1:1-100\n"
            "\n"
            "chr1\t100\t200\tEH38E0000001\t.\t.\t.\t.\t.\tPLS\n"
        )
        rows = list(iter_ccre_bed(bed))
        assert len(rows) == 1


# ── Tests: Table creation ────────────────────────────────────────────


class TestTableCreation:
    def test_tables_exist(self, ccres_engine: sa.Engine):
        insp = sa.inspect(ccres_engine)
        assert "encode_ccres" in insp.get_table_names()
        assert "encode_ccres_version" in insp.get_table_names()

    def test_indexes_exist(self, ccres_engine: sa.Engine):
        insp = sa.inspect(ccres_engine)
        indexes = insp.get_indexes("encode_ccres")
        idx_names = {idx["name"] for idx in indexes}
        assert "idx_ccres_region" in idx_names
        assert "idx_ccres_class" in idx_names


# ── Tests: Bulk loading ──────────────────────────────────────────────


class TestLoadEncodeCcres:
    def test_load_basic(self, ccres_engine: sa.Engine, sample_bed_file: Path):
        stats = load_encode_ccres(sample_bed_file, ccres_engine)
        assert stats.records_loaded == 5
        assert stats.skipped_invalid_chrom == 0
        assert stats.skipped_malformed == 0
        assert stats.sha256 is not None

        # Verify data in table
        with ccres_engine.connect() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM encode_ccres")).scalar()
            assert count == 5

    def test_load_v3_rows_store_ccre_accession(self, ccres_engine: sa.Engine, tmp_path: Path):
        bed = tmp_path / "v3_ccres.bed"
        bed.write_text("chr1\t104896\t105048\tEH38D4327509\tEH38E2776520\tCTCF-only,CTCF-bound\n")

        stats = load_encode_ccres(bed, ccres_engine)
        assert stats.records_loaded == 1

        with ccres_engine.connect() as conn:
            stored = conn.execute(
                sa.text("SELECT accession, ccre_class FROM encode_ccres")
            ).fetchone()
            assert stored is not None
            assert stored[0] == "EH38E2776520"
            assert stored[1] == "CTCF-only"

            rdhs_count = conn.execute(
                sa.text("SELECT COUNT(*) FROM encode_ccres WHERE accession = 'EH38D4327509'")
            ).scalar()
            assert rdhs_count == 0

    def test_load_with_skips(self, ccres_engine: sa.Engine, tmp_path: Path):
        bed = tmp_path / "mixed.bed"
        bed.write_text(
            "chr1\t100\t200\tEH38E0000001\t.\t.\t.\t.\t.\tPLS\n"
            "chrM\t100\t200\tEH38E0000002\t.\t.\t.\t.\t.\tPLS\n"  # invalid chrom
            "chr1\t300\t400\tEH38E0000003\t.\t.\t.\t.\t.\tUNKNOWN\n"  # unknown class
            "chr1\t500\n"  # malformed
        )
        stats = load_encode_ccres(bed, ccres_engine)
        assert stats.records_loaded == 1
        assert stats.skipped_invalid_chrom == 1
        assert stats.skipped_unknown_class == 1
        assert stats.skipped_malformed == 1

    def test_idempotent_load(self, ccres_engine: sa.Engine, sample_bed_file: Path):
        """Loading twice should not duplicate records (INSERT OR IGNORE)."""
        load_encode_ccres(sample_bed_file, ccres_engine)
        load_encode_ccres(sample_bed_file, ccres_engine)

        with ccres_engine.connect() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM encode_ccres")).scalar()
            assert count == 5

    def test_version_recorded(self, ccres_engine: sa.Engine, sample_bed_file: Path):
        load_encode_ccres(sample_bed_file, ccres_engine)

        with ccres_engine.connect() as conn:
            row = (
                conn.execute(
                    sa.text(
                        "SELECT loaded_at, source_url, record_count, sha256 "
                        "FROM encode_ccres_version ORDER BY rowid DESC LIMIT 1"
                    )
                )
                .mappings()
                .fetchone()
            )
            assert row is not None
            assert row["record_count"] == 5

    def test_progress_callback(self, ccres_engine: sa.Engine, tmp_path: Path):
        """Verify progress callback is called for large files."""
        # Create a file with 50001 lines to trigger callback
        bed = tmp_path / "large.bed"
        lines = []
        for i in range(50_001):
            lines.append(f"chr1\t{i * 100}\t{i * 100 + 50}\tEH38E{i:07d}\t.\t.\t.\t.\t.\tPLS\n")
        bed.write_text("".join(lines))

        callbacks = []
        load_encode_ccres(bed, ccres_engine, progress_callback=callbacks.append)
        assert len(callbacks) >= 1


# ── Tests: Region queries ────────────────────────────────────────────


class TestQueryByRegion:
    def test_basic_query(self, loaded_engine: sa.Engine):
        results = query_ccres_by_region("1", 0, 50000, loaded_engine)
        # Should find the three chr1 records (10000-10500, 20000-20800, 30000-30600)
        assert len(results) == 3

    def test_partial_overlap(self, loaded_engine: sa.Engine):
        # Query range that partially overlaps first record only
        results = query_ccres_by_region("1", 10400, 10600, loaded_engine)
        assert len(results) == 1
        assert results[0].accession == "EH38E1000001"

    def test_no_overlap(self, loaded_engine: sa.Engine):
        results = query_ccres_by_region("1", 40000, 45000, loaded_engine)
        assert len(results) == 0

    def test_different_chrom(self, loaded_engine: sa.Engine):
        results = query_ccres_by_region("2", 0, 100000, loaded_engine)
        assert len(results) == 1
        assert results[0].ccre_class == "CTCF-only"

    def test_chrx(self, loaded_engine: sa.Engine):
        results = query_ccres_by_region("X", 0, 200000, loaded_engine)
        assert len(results) == 1

    def test_chr_prefix_normalization(self, loaded_engine: sa.Engine):
        """Query with 'chr' prefix should still work."""
        results = query_ccres_by_region("chr1", 0, 50000, loaded_engine)
        assert len(results) == 3

    def test_empty_region(self, loaded_engine: sa.Engine):
        results = query_ccres_by_region("3", 0, 1000000, loaded_engine)
        assert len(results) == 0

    def test_result_type(self, loaded_engine: sa.Engine):
        results = query_ccres_by_region("1", 10000, 10500, loaded_engine)
        assert len(results) == 1
        r = results[0]
        assert isinstance(r, CCREResult)
        assert r.chrom == "1"
        assert r.start_pos == 10000
        assert r.end_pos == 10500

    def test_ordered_by_start(self, loaded_engine: sa.Engine):
        results = query_ccres_by_region("1", 0, 50000, loaded_engine)
        positions = [r.start_pos for r in results]
        assert positions == sorted(positions)


# ── Tests: Summary ───────────────────────────────────────────────────


class TestSummary:
    def test_summary(self, loaded_engine: sa.Engine):
        summary = get_ccre_summary(loaded_engine)
        assert summary["PLS"] == 1
        assert summary["pELS"] == 1
        assert summary["dELS"] == 1
        assert summary["CTCF-only"] == 1
        assert summary["DNase-H3K4me3"] == 1

    def test_summary_empty(self, ccres_engine: sa.Engine):
        summary = get_ccre_summary(ccres_engine)
        assert len(summary) == 0


# ── Tests: is_loaded ─────────────────────────────────────────────────


class TestIsLoaded:
    def test_not_loaded(self, ccres_engine: sa.Engine):
        assert is_loaded(ccres_engine) is False

    def test_loaded(self, loaded_engine: sa.Engine):
        assert is_loaded(loaded_engine) is True

    def test_no_table(self):
        """is_loaded should return False if table doesn't exist."""
        engine = sa.create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        assert is_loaded(engine) is False


# ── Tests: API routes ────────────────────────────────────────────────


class TestAPIRoutes:
    """Test the FastAPI route handlers."""

    @pytest.fixture
    def client(self, loaded_engine: sa.Engine):
        """Create a test client with mocked registry."""
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from backend.main import create_app

        app = create_app()

        class MockRegistry:
            encode_ccres_engine = loaded_engine

        with patch("backend.api.routes.encode_ccres.get_registry", return_value=MockRegistry()):
            yield TestClient(app)

    def test_region_endpoint(self, client):
        resp = client.get("/api/encode-ccres/region?chrom=1&start=0&end=50000")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        assert len(data["ccres"]) == 3

    def test_region_empty(self, client):
        resp = client.get("/api/encode-ccres/region?chrom=3&start=0&end=1000")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_region_missing_params(self, client):
        resp = client.get("/api/encode-ccres/region?chrom=1")
        assert resp.status_code == 422  # validation error

    def test_region_start_ge_end(self, client):
        resp = client.get("/api/encode-ccres/region?chrom=1&start=500&end=500")
        assert resp.status_code == 400

        resp = client.get("/api/encode-ccres/region?chrom=1&start=1000&end=500")
        assert resp.status_code == 400

    def test_summary_endpoint(self, client):
        resp = client.get("/api/encode-ccres/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert "PLS" in data["by_class"]

    def test_status_endpoint(self, client):
        resp = client.get("/api/encode-ccres/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["loaded"] is True
        assert data["record_count"] == 5
