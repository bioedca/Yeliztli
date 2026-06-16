"""Tests for gnomAD AF-only SQLite index builder (P2-08) and rare variant flagging (P2-10).

Covers:
- T2-09: gnomAD loader ingests subset, lookup returns correct AF for rs7412 (APOE)
- T2-10: Rare variant flag correctly set for AF < 0.01 and < 0.001
- VCF line parsing (valid, multiallelic, no rsid, invalid chrom)
- CSV loading into gnomad_af table
- Batch lookup by rsid and by (chrom, pos, ref, alt)
- Table creation and index creation
- Version recording in database_versions
- Download function structure
- P2-10: compute_rare_flags() utilities
- P2-10: Rare flag boundary values, NULL AF handling, position-based flagging
- P2-10: Database indexes on rare_flag and ultra_rare_flag columns
"""

from __future__ import annotations

import gzip
import textwrap
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from backend.annotation.gnomad import (
    GNOMAD_BITMASK,
    LOOKUP_BATCH_SIZE,
    RARE_AF_THRESHOLD,
    ULTRA_RARE_AF_THRESHOLD,
    GnomADAnnotation,
    LoadStats,
    _create_gnomad_indexes,
    _create_gnomad_table,
    compute_af_popmax,
    compute_rare_flags,
    iter_gnomad_vcf,
    load_gnomad_from_csv,
    load_gnomad_from_vcf,
    lookup_gnomad_by_positions,
    lookup_gnomad_by_rsids,
    parse_gnomad_vcf_line,
    record_gnomad_version,
)
from backend.db.tables import database_versions, reference_metadata, sample_metadata_obj

# ── Fixtures ────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
GNOMAD_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "gnomad_seed.csv"


@pytest.fixture
def gnomad_engine() -> sa.Engine:
    """In-memory gnomAD engine with tables created."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _create_gnomad_table(engine)
    _create_gnomad_indexes(engine)
    return engine


@pytest.fixture
def gnomad_engine_with_data(gnomad_engine: sa.Engine) -> sa.Engine:
    """gnomAD engine loaded from seed CSV."""
    load_gnomad_from_csv(GNOMAD_SEED_CSV, gnomad_engine, clear_existing=False)
    return gnomad_engine


@pytest.fixture
def reference_engine() -> sa.Engine:
    """In-memory reference engine for version tracking."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    reference_metadata.create_all(engine)
    return engine


# ── VCF line parsing tests ──────────────────────────────────────────────


class TestParseGnomadVcfLine:
    """Test VCF line parsing."""

    def test_valid_line_with_rsid(self):
        """Parse a standard gnomAD VCF line with rsid and AF fields."""
        line = (
            "19\t44908684\trs429358\tT\tC\t.\tPASS\t"
            "AF=0.1387;AF_afr=0.2650;AF_amr=0.1100;AF_eas=0.0890;"
            "AF_nfe=0.1510;AF_fin=0.1630;AF_sas=0.0880;nhomalt=2543"
        )
        record, skip = parse_gnomad_vcf_line(line)

        assert skip is None
        assert record is not None
        assert record.rsid == "rs429358"
        assert record.chrom == "19"
        assert record.pos == 44908684
        assert record.ref == "T"
        assert record.alt == "C"
        assert record.af_global == pytest.approx(0.1387)
        assert record.af_afr == pytest.approx(0.2650)
        assert record.af_amr == pytest.approx(0.1100)
        assert record.af_eas == pytest.approx(0.0890)
        assert record.af_eur == pytest.approx(0.1510)  # AF_nfe → af_eur
        assert record.af_fin == pytest.approx(0.1630)
        assert record.af_sas == pytest.approx(0.0880)
        assert record.homozygous_count == 2543

    def test_chr_prefix_normalization(self):
        """Chromosome names with 'chr' prefix are normalized."""
        line = "chr1\t100\trs12345\tA\tG\t.\tPASS\tAF=0.05"
        record, skip = parse_gnomad_vcf_line(line)

        assert skip is None
        assert record is not None
        assert record.chrom == "1"

    def test_no_rsid_skipped(self):
        """Lines without an rsid are skipped."""
        line = "1\t100\t.\tA\tG\t.\tPASS\tAF=0.05"
        record, skip = parse_gnomad_vcf_line(line)

        assert record is None
        assert skip == "no_rsid"

    def test_multiallelic_skipped(self):
        """Multi-allelic ALT fields are skipped."""
        line = "1\t100\trs12345\tA\tG,T\t.\tPASS\tAF=0.05"
        record, skip = parse_gnomad_vcf_line(line)

        assert record is None
        assert skip == "multiallelic"

    def test_invalid_chrom_skipped(self):
        """Invalid chromosomes are skipped."""
        line = "chrUn_gl000220\t100\trs12345\tA\tG\t.\tPASS\tAF=0.05"
        record, skip = parse_gnomad_vcf_line(line)

        assert record is None
        assert skip == "invalid_chrom"

    def test_malformed_line(self):
        """Lines with too few columns are skipped."""
        line = "1\t100\trs12345"
        record, skip = parse_gnomad_vcf_line(line)

        assert record is None
        assert skip == "malformed"

    def test_missing_af_fields_are_none(self):
        """Missing AF fields result in None values."""
        line = "1\t100\trs12345\tA\tG\t.\tPASS\tAF=0.05"
        record, skip = parse_gnomad_vcf_line(line)

        assert skip is None
        assert record is not None
        assert record.af_global == pytest.approx(0.05)
        assert record.af_afr is None
        assert record.homozygous_count == 0

    def test_multiple_ids_picks_rsid(self):
        """When ID column has multiple IDs, picks the one starting with rs."""
        line = "1\t100\tvar123;rs99999\tA\tG\t.\tPASS\tAF=0.05"
        record, skip = parse_gnomad_vcf_line(line)

        assert skip is None
        assert record is not None
        assert record.rsid == "rs99999"

    def test_x_chromosome(self):
        """X chromosome is accepted."""
        line = "X\t1000\trs55555\tC\tT\t.\tPASS\tAF=0.02"
        record, skip = parse_gnomad_vcf_line(line)

        assert skip is None
        assert record is not None
        assert record.chrom == "X"


# ── VCF iteration tests ────────────────────────────────────────────────


class TestIterGnomadVcf:
    """Test VCF file iteration."""

    def test_iterate_gzipped_vcf(self, tmp_path: Path):
        """Iterate over a gzipped VCF file."""
        vcf_content = textwrap.dedent("""\
            ##fileformat=VCFv4.2
            #CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
            1\t100\trs111\tA\tG\t.\tPASS\tAF=0.05;AF_afr=0.03;nhomalt=10
            1\t200\t.\tC\tT\t.\tPASS\tAF=0.10
            2\t300\trs222\tG\tA\t.\tPASS\tAF=0.20;AF_nfe=0.25;nhomalt=50
        """)
        vcf_path = tmp_path / "test.vcf.gz"
        with gzip.open(vcf_path, "wt") as f:
            f.write(vcf_content)

        rows = []
        stats = LoadStats()
        for row, stats in iter_gnomad_vcf(vcf_path):
            rows.append(row)

        assert len(rows) == 2  # rs111 and rs222 (no rsid skipped)
        assert stats.total_lines == 3
        assert stats.variants_loaded == 2
        assert stats.skipped_no_rsid == 1

        # Verify first row
        assert rows[0]["rsid"] == "rs111"
        assert rows[0]["af_global"] == pytest.approx(0.05)
        assert rows[0]["af_afr"] == pytest.approx(0.03)
        assert rows[0]["homozygous_count"] == 10

    def test_progress_callback(self, tmp_path: Path):
        """Progress callback is called at intervals."""
        # Create a file with enough lines to trigger callback
        lines = ["##fileformat=VCFv4.2\n", "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"]
        # Callback fires at 100k lines, so just verify it doesn't crash
        lines.append("1\t100\trs111\tA\tG\t.\tPASS\tAF=0.05\n")

        vcf_path = tmp_path / "test.vcf.gz"
        with gzip.open(vcf_path, "wt") as f:
            f.writelines(lines)

        callback_calls = []
        for _, _ in iter_gnomad_vcf(vcf_path, progress_callback=callback_calls.append):
            pass

        # Only 1 data line, so callback won't fire (fires at 100k intervals)
        assert callback_calls == []


# ── Table creation tests ────────────────────────────────────────────────


class TestCreateGnomadTables:
    """Test gnomad_af table and index creation."""

    def test_creates_table(self, gnomad_engine: sa.Engine):
        """Table gnomad_af exists after creation."""
        with gnomad_engine.connect() as conn:
            result = conn.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name='gnomad_af'")
            ).fetchone()
        assert result is not None

    def test_creates_indexes(self, gnomad_engine: sa.Engine):
        """Indexes are created on the gnomad_af table."""
        with gnomad_engine.connect() as conn:
            indexes = conn.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='gnomad_af'"
                )
            ).fetchall()
        index_names = {r[0] for r in indexes}
        assert "idx_gnomad_chrom_pos" in index_names
        assert "idx_gnomad_chrom_pos_ref_alt" in index_names

    def test_idempotent(self, gnomad_engine: sa.Engine):
        """Calling table + index creation twice doesn't error."""
        _create_gnomad_table(gnomad_engine)  # second call
        _create_gnomad_indexes(gnomad_engine)
        with gnomad_engine.connect() as conn:
            result = conn.execute(sa.text("SELECT COUNT(*) FROM gnomad_af")).scalar()
        assert result == 0


# ── CSV loading tests ───────────────────────────────────────────────────


class TestLoadGnomadFromCsv:
    """Test loading gnomAD data from CSV seed files."""

    def test_loads_all_rows(self, gnomad_engine: sa.Engine):
        """All rows from the seed CSV are loaded."""
        stats = load_gnomad_from_csv(GNOMAD_SEED_CSV, gnomad_engine)

        with gnomad_engine.connect() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM gnomad_af")).scalar()

        assert count == stats.variants_loaded
        assert stats.variants_loaded > 0

    def test_correct_af_values(self, gnomad_engine_with_data: sa.Engine):
        """Specific AF values match the seed data."""
        with gnomad_engine_with_data.connect() as conn:
            row = conn.execute(sa.text("SELECT * FROM gnomad_af WHERE rsid = 'rs7412'")).fetchone()

        assert row is not None
        assert row.chrom == "19"
        assert row.pos == 44908822
        assert row.af_global == pytest.approx(0.0781)
        assert row.af_afr == pytest.approx(0.1130)
        assert row.homozygous_count == 874

    def test_clear_existing(self, gnomad_engine: sa.Engine):
        """clear_existing=True removes existing rows before loading."""
        # Load twice
        load_gnomad_from_csv(GNOMAD_SEED_CSV, gnomad_engine)
        stats = load_gnomad_from_csv(GNOMAD_SEED_CSV, gnomad_engine, clear_existing=True)

        with gnomad_engine.connect() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM gnomad_af")).scalar()

        # Should have exactly one copy, not two
        assert count == stats.variants_loaded


# ── VCF loading tests ───────────────────────────────────────────────────


class TestLoadGnomadFromVcf:
    """Test loading gnomAD data from VCF files."""

    def test_loads_from_vcf(self, gnomad_engine: sa.Engine, tmp_path: Path):
        """Load gnomAD data from a gzipped VCF."""
        vcf_content = textwrap.dedent("""\
            ##fileformat=VCFv4.2
            #CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
            19\t44908684\trs429358\tT\tC\t.\tPASS\tAF=0.1387;AF_afr=0.2650;AF_amr=0.1100;AF_eas=0.0890;AF_nfe=0.1510;AF_fin=0.1630;AF_sas=0.0880;nhomalt=2543
            19\t44908822\trs7412\tC\tT\t.\tPASS\tAF=0.0781;AF_afr=0.1130;AF_amr=0.0560;AF_eas=0.0980;AF_nfe=0.0730;AF_fin=0.0410;AF_sas=0.0650;nhomalt=874
        """)
        vcf_path = tmp_path / "gnomad.vcf.gz"
        with gzip.open(vcf_path, "wt") as f:
            f.write(vcf_content)

        stats = load_gnomad_from_vcf(vcf_path, gnomad_engine)

        assert stats.variants_loaded == 2
        assert stats.total_lines == 2

        with gnomad_engine.connect() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM gnomad_af")).scalar()
        assert count == 2

    def test_skips_no_rsid_and_multiallelic(self, gnomad_engine: sa.Engine, tmp_path: Path):
        """Variants without rsid or with multiallelic ALT are skipped."""
        vcf_content = textwrap.dedent("""\
            ##fileformat=VCFv4.2
            #CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
            1\t100\trs111\tA\tG\t.\tPASS\tAF=0.05
            1\t200\t.\tC\tT\t.\tPASS\tAF=0.10
            1\t300\trs333\tG\tA,T\t.\tPASS\tAF=0.20
        """)
        vcf_path = tmp_path / "gnomad.vcf.gz"
        with gzip.open(vcf_path, "wt") as f:
            f.write(vcf_content)

        stats = load_gnomad_from_vcf(vcf_path, gnomad_engine)

        assert stats.variants_loaded == 1
        assert stats.skipped_no_rsid == 1
        assert stats.skipped_multiallelic == 1


# ── Lookup by rsid tests ────────────────────────────────────────────────


class TestLookupGnomadByRsids:
    """Test gnomAD lookup by rsid (T2-09)."""

    def test_returns_correct_af_for_apoe(self, gnomad_engine_with_data: sa.Engine):
        """T2-09: Lookup returns correct AF for rs7412 (APOE)."""
        results = lookup_gnomad_by_rsids(["rs7412"], gnomad_engine_with_data)

        assert "rs7412" in results
        annot = results["rs7412"]
        assert annot.af_global == pytest.approx(0.0781)
        assert annot.af_afr == pytest.approx(0.1130)
        assert annot.af_amr == pytest.approx(0.0560)
        assert annot.af_eas == pytest.approx(0.0980)
        assert annot.af_eur == pytest.approx(0.0730)
        assert annot.af_fin == pytest.approx(0.0410)
        assert annot.af_sas == pytest.approx(0.0650)
        assert annot.homozygous_count == 874

    def test_batch_lookup_multiple(self, gnomad_engine_with_data: sa.Engine):
        """Batch lookup returns data for multiple rsids."""
        results = lookup_gnomad_by_rsids(
            ["rs429358", "rs7412", "rs1801133"], gnomad_engine_with_data
        )

        assert len(results) == 3
        assert results["rs429358"].af_global == pytest.approx(0.1387)
        assert results["rs1801133"].af_global == pytest.approx(0.2465)

    def test_unmatched_rsids_excluded(self, gnomad_engine_with_data: sa.Engine):
        """Unmatched rsids are not in the results."""
        results = lookup_gnomad_by_rsids(["rs7412", "rs_nonexistent"], gnomad_engine_with_data)

        assert "rs7412" in results
        assert "rs_nonexistent" not in results

    def test_empty_input(self, gnomad_engine_with_data: sa.Engine):
        """Empty input returns empty dict."""
        results = lookup_gnomad_by_rsids([], gnomad_engine_with_data)
        assert results == {}

    def test_large_batch_splits(self, gnomad_engine_with_data: sa.Engine):
        """Batches larger than LOOKUP_BATCH_SIZE are split correctly."""
        # Create a list larger than batch size with some valid rsids
        rsids = [f"rs_fake_{i}" for i in range(LOOKUP_BATCH_SIZE + 100)]
        rsids[0] = "rs429358"
        rsids[LOOKUP_BATCH_SIZE] = "rs7412"

        results = lookup_gnomad_by_rsids(rsids, gnomad_engine_with_data)

        assert "rs429358" in results
        assert "rs7412" in results


# ── Lookup by position tests ────────────────────────────────────────────


class TestLookupGnomadByPositions:
    """Test gnomAD lookup by (chrom, pos, ref, alt)."""

    def test_returns_match(self, gnomad_engine_with_data: sa.Engine):
        """Position-based lookup returns matching variant."""
        positions = [("19", 44908822, "C", "T")]  # rs7412
        results = lookup_gnomad_by_positions(positions, gnomad_engine_with_data)

        key = ("19", 44908822, "C", "T")
        assert key in results
        assert results[key].af_global == pytest.approx(0.0781)

    def test_empty_input(self, gnomad_engine_with_data: sa.Engine):
        """Empty input returns empty dict."""
        results = lookup_gnomad_by_positions([], gnomad_engine_with_data)
        assert results == {}

    def test_no_match(self, gnomad_engine_with_data: sa.Engine):
        """Non-existent position returns empty."""
        positions = [("99", 1, "A", "G")]
        results = lookup_gnomad_by_positions(positions, gnomad_engine_with_data)
        assert len(results) == 0


# ── Rare variant flag tests ────────────────────────────────────────────


class TestRareVariantFlags:
    """Test rare and ultra-rare variant flagging (T2-10)."""

    def test_rare_flag_threshold(self, gnomad_engine_with_data: sa.Engine):
        """T2-10: Variants with AF < 0.01 get rare_flag=True."""
        # rs80357906 has af_global=0.00004 (ultra-rare)
        results = lookup_gnomad_by_rsids(["rs80357906"], gnomad_engine_with_data)

        assert "rs80357906" in results
        annot = results["rs80357906"]
        assert annot.af_global == pytest.approx(0.00004)
        assert annot.rare_flag is True
        assert annot.ultra_rare_flag is True

    def test_not_rare_above_threshold(self, gnomad_engine_with_data: sa.Engine):
        """Variants with AF >= 0.01 are NOT flagged as rare."""
        # rs429358 has af_global=0.1387 (common)
        results = lookup_gnomad_by_rsids(["rs429358"], gnomad_engine_with_data)

        annot = results["rs429358"]
        assert annot.rare_flag is False
        assert annot.ultra_rare_flag is False

    def test_rare_but_not_ultra_rare(self, gnomad_engine_with_data: sa.Engine):
        """Variants with 0.001 <= popmax < 0.01 are rare but not ultra-rare (F15)."""
        # rs5030862: global=0.0041, popmax (afr)=0.006 — rare in every population.
        results = lookup_gnomad_by_rsids(["rs5030862"], gnomad_engine_with_data)

        annot = results["rs5030862"]
        assert annot.af_popmax == pytest.approx(0.006)
        assert annot.rare_flag is True
        assert annot.ultra_rare_flag is False

    def test_ancestry_common_variant_not_flagged_rare(self, gnomad_engine_with_data: sa.Engine):
        """F15: a variant rare globally but common in one ancestry is NOT rare.

        rs28897696 sits at af_global=0.0052 (rare) yet af_afr=0.018 (>1% in AFR),
        so its popmax is 0.018 and global-AF rarity would mislabel it "rare".
        """
        results = lookup_gnomad_by_rsids(["rs28897696"], gnomad_engine_with_data)

        annot = results["rs28897696"]
        assert annot.af_global == pytest.approx(0.0052)
        assert annot.af_popmax == pytest.approx(0.018)
        assert annot.rare_flag is False
        assert annot.ultra_rare_flag is False

    def test_thresholds_match_constants(self):
        """Threshold constants match PRD specs."""
        assert RARE_AF_THRESHOLD == 0.01
        assert ULTRA_RARE_AF_THRESHOLD == 0.001

    def test_position_lookup_returns_rare_flags(self, gnomad_engine_with_data: sa.Engine):
        """Position-based lookup also computes rare flags correctly."""
        # rs80357906 at chrom=17, pos=43093449 (BRCA1 ultra-rare)
        with gnomad_engine_with_data.connect() as conn:
            row = conn.execute(
                sa.text("SELECT chrom, pos, ref, alt FROM gnomad_af WHERE rsid = 'rs80357906'")
            ).fetchone()
        assert row is not None

        positions = [(row.chrom, row.pos, row.ref, row.alt)]
        results = lookup_gnomad_by_positions(positions, gnomad_engine_with_data)
        key = (row.chrom, row.pos, row.ref, row.alt)

        assert key in results
        annot = results[key]
        assert annot.rare_flag is True
        assert annot.ultra_rare_flag is True

    def test_boundary_exactly_at_rare_threshold(self, gnomad_engine: sa.Engine):
        """AF exactly at 0.01 is NOT rare (strict less-than)."""
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af (rsid, chrom, pos, ref, alt, af_global) "
                    "VALUES ('rs_boundary', '1', 100, 'A', 'G', 0.01)"
                )
            )
        results = lookup_gnomad_by_rsids(["rs_boundary"], gnomad_engine)
        annot = results["rs_boundary"]
        assert annot.rare_flag is False
        assert annot.ultra_rare_flag is False

    def test_boundary_exactly_at_ultra_rare_threshold(self, gnomad_engine: sa.Engine):
        """AF exactly at 0.001 is rare but NOT ultra-rare (strict less-than)."""
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af (rsid, chrom, pos, ref, alt, af_global) "
                    "VALUES ('rs_boundary2', '1', 200, 'A', 'G', 0.001)"
                )
            )
        results = lookup_gnomad_by_rsids(["rs_boundary2"], gnomad_engine)
        annot = results["rs_boundary2"]
        assert annot.rare_flag is True
        assert annot.ultra_rare_flag is False

    def test_zero_af_is_not_rare(self, gnomad_engine: sa.Engine):
        """AF of 0.0 is monomorphic reference, not observed-rare/ultra-rare (F26)."""
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af (rsid, chrom, pos, ref, alt, af_global) "
                    "VALUES ('rs_zero', '1', 300, 'A', 'G', 0.0)"
                )
            )
        results = lookup_gnomad_by_rsids(["rs_zero"], gnomad_engine)
        annot = results["rs_zero"]
        assert annot.rare_flag is False
        assert annot.ultra_rare_flag is False

    def test_null_af_is_not_flagged(self, gnomad_engine: sa.Engine):
        """NULL AF (no frequency data) produces no rare flags."""
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af (rsid, chrom, pos, ref, alt, af_global) "
                    "VALUES ('rs_null', '1', 400, 'A', 'G', NULL)"
                )
            )
        results = lookup_gnomad_by_rsids(["rs_null"], gnomad_engine)
        annot = results["rs_null"]
        assert annot.rare_flag is False
        assert annot.ultra_rare_flag is False


# ── Version recording tests ──────────────────────────────────────────────


class TestRecordGnomadVersion:
    """Test version tracking in database_versions."""

    def test_insert_new_version(self, reference_engine: sa.Engine):
        """New version is inserted into database_versions."""
        record_gnomad_version(
            reference_engine,
            version="r2.1.1",
            file_path="/data/gnomad.vcf.bgz",
            file_size_bytes=50_000_000_000,
            checksum="abc123",
        )

        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "gnomad")
            ).fetchone()

        assert row is not None
        assert row.version == "r2.1.1"
        assert row.file_size_bytes == 50_000_000_000
        assert row.checksum_sha256 == "abc123"

    def test_update_existing_version(self, reference_engine: sa.Engine):
        """Existing version is updated, not duplicated."""
        record_gnomad_version(reference_engine, version="r2.1.0")
        record_gnomad_version(reference_engine, version="r2.1.1")

        with reference_engine.connect() as conn:
            rows = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "gnomad")
            ).fetchall()

        assert len(rows) == 1
        assert rows[0].version == "r2.1.1"


# ── Bitmask constant tests ──────────────────────────────────────────────


class TestGnomadBitmask:
    """Test gnomAD bitmask constant."""

    def test_bitmask_value(self):
        """gnomAD bitmask is bit 2 (value 4)."""
        assert GNOMAD_BITMASK == 0b000100
        assert GNOMAD_BITMASK == 4


# ── Data class tests ────────────────────────────────────────────────────


class TestGnomADAnnotation:
    """Test GnomADAnnotation dataclass."""

    def test_from_lookup(self, gnomad_engine_with_data: sa.Engine):
        """GnomADAnnotation has all expected fields."""
        results = lookup_gnomad_by_rsids(["rs429358"], gnomad_engine_with_data)
        annot = results["rs429358"]

        assert isinstance(annot, GnomADAnnotation)
        assert annot.rsid == "rs429358"
        assert annot.af_global is not None
        assert annot.af_afr is not None
        assert annot.af_amr is not None
        assert annot.af_eas is not None
        assert annot.af_eur is not None
        assert annot.af_fin is not None
        assert annot.af_sas is not None
        assert isinstance(annot.homozygous_count, int)
        assert isinstance(annot.rare_flag, bool)
        assert isinstance(annot.ultra_rare_flag, bool)


# ── compute_rare_flags tests (P2-10) ─────────────────────────────────────


class TestComputeRareFlags:
    """Test the compute_rare_flags utility function (P2-10)."""

    def test_none_af(self):
        """None AF → (False, False)."""
        assert compute_rare_flags(None) == (False, False)

    def test_common_variant(self):
        """Common AF → (False, False)."""
        assert compute_rare_flags(0.15) == (False, False)

    def test_rare_variant(self):
        """Rare AF → (True, False)."""
        assert compute_rare_flags(0.005) == (True, False)

    def test_ultra_rare_variant(self):
        """Ultra-rare AF → (True, True)."""
        assert compute_rare_flags(0.00004) == (True, True)

    def test_boundary_at_rare_threshold(self):
        """AF exactly at 0.01 → (False, False)."""
        assert compute_rare_flags(0.01) == (False, False)

    def test_boundary_at_ultra_rare_threshold(self):
        """AF exactly at 0.001 → (True, False)."""
        assert compute_rare_flags(0.001) == (True, False)

    def test_zero_af(self):
        """AF of 0.0 → (False, False): monomorphic reference, not ultra-rare (F26)."""
        assert compute_rare_flags(0.0) == (False, False)


class TestComputeAfPopmax:
    """compute_af_popmax: rarity denominator is the most-common ancestry (F15)."""

    def test_max_over_populations(self):
        # Global rare, but common in AFR → popmax is the ancestry max.
        assert compute_af_popmax(0.0052, 0.018, 0.0025, 0.0001, 0.0003, 0.0001, 0.002) == 0.018

    def test_all_none_is_none(self):
        assert compute_af_popmax(None, None, None, None, None, None, None) is None

    def test_ignores_nulls(self):
        # Only global + one ancestry present; popmax is the larger of the two.
        assert compute_af_popmax(0.002, None, 0.007, None, None, None, None) == 0.007

    def test_popmax_at_least_global(self):
        assert compute_af_popmax(0.03) == 0.03

    def test_popmax_drives_rare_flag(self):
        # The F15 wiring: an ancestry-common variant is NOT rare by popmax.
        popmax = compute_af_popmax(0.0052, 0.018)
        assert compute_rare_flags(popmax) == (False, False)
        # …while a variant rare in every population is rare-not-ultra.
        popmax_rare = compute_af_popmax(0.0041, 0.006, 0.003)
        assert compute_rare_flags(popmax_rare) == (True, False)


# ── Database index tests for rare flags (P2-10) ─────────────────────────


class TestRareFlagIndexes:
    """Test that rare_flag and ultra_rare_flag indexes exist in sample DB (P2-10)."""

    @pytest.fixture
    def sample_engine(self) -> sa.Engine:
        """In-memory sample engine with all tables created."""
        engine = sa.create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        sample_metadata_obj.create_all(engine)
        return engine

    @pytest.fixture
    def index_names(self, sample_engine: sa.Engine) -> set[str]:
        """All index names on annotated_variants."""
        with sample_engine.connect() as conn:
            indexes = conn.execute(
                sa.text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='annotated_variants'"
                )
            ).fetchall()
        return {r[0] for r in indexes}

    def test_rare_flag_index_exists(self, index_names: set[str]):
        """Index on rare_flag column exists in annotated_variants."""
        assert "idx_annot_rare_flag" in index_names

    def test_ultra_rare_flag_index_exists(self, index_names: set[str]):
        """Index on ultra_rare_flag column exists in annotated_variants."""
        assert "idx_annot_ultra_rare_flag" in index_names

    def test_gnomad_af_global_index_exists(self, index_names: set[str]):
        """Index on gnomad_af_global column exists for AF range queries."""
        assert "idx_annot_gnomad_af" in index_names


class TestIndexAfterLoad:
    """The load path builds indexes AFTER the bulk insert (speed + smaller lock window)."""

    def test_load_on_fresh_engine_creates_indexes_and_data(self) -> None:
        engine = sa.create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        stats = load_gnomad_from_csv(GNOMAD_SEED_CSV, engine)
        assert stats.variants_loaded > 0

        with engine.connect() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM gnomad_af")).scalar()
            indexes = conn.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='gnomad_af'"
                )
            ).fetchall()
        index_names = {r[0] for r in indexes}
        assert count == stats.variants_loaded
        assert "idx_gnomad_chrom_pos" in index_names
        assert "idx_gnomad_chrom_pos_ref_alt" in index_names
