"""Tests for scripts/build_vep_bundle.py.

Verifies the VEP bundle build script:
- Parses VEP VCF output correctly
- Loads seed CSV correctly
- Creates valid SQLite database with correct schema
- Indexes are created
- WAL mode enabled
- Bundle metadata stored
- Most-severe consequence selection works
- MANE Select transcript flagging
- Dry run does not create files
- Idempotent builds
- Coverage report generation
- Known variant data integrity (rs1801133 / MTHFR C677T)

Test IDs: T2-01 (partial), T2-02 (partial), T2-03 (partial)
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

# Import build script functions directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from build_vep_bundle import (
    BuildStats,
    VEPRecord,
    _is_mane_select,
    build_bundle_db,
    consequence_severity,
    coverage_report,
    load_seed_csv,
    parse_vep_vcf,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
SEED_DIR = FIXTURES_DIR / "seed_csvs"
VEP_SEED_CSV = SEED_DIR / "vep_seed.csv"
SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "build_vep_bundle.py"


# ── Consequence Severity ─────────────────────────────────────


class TestConsequenceSeverity:
    """Test the consequence severity ranking."""

    def test_stop_gained_more_severe_than_missense(self) -> None:
        assert consequence_severity("stop_gained") > consequence_severity("missense_variant")

    def test_missense_more_severe_than_synonymous(self) -> None:
        missense = consequence_severity("missense_variant")
        synonymous = consequence_severity("synonymous_variant")
        assert missense > synonymous

    def test_synonymous_more_severe_than_intron(self) -> None:
        synonymous = consequence_severity("synonymous_variant")
        intron = consequence_severity("intron_variant")
        assert synonymous > intron

    def test_frameshift_more_severe_than_missense(self) -> None:
        frameshift = consequence_severity("frameshift_variant")
        missense = consequence_severity("missense_variant")
        assert frameshift > missense

    def test_splice_acceptor_very_severe(self) -> None:
        splice = consequence_severity("splice_acceptor_variant")
        stop = consequence_severity("stop_gained")
        assert splice > stop

    def test_intergenic_least_severe(self) -> None:
        assert consequence_severity("intergenic_variant") == 0

    def test_unknown_consequence_returns_zero(self) -> None:
        assert consequence_severity("completely_unknown_term") == 0

    def test_compound_consequence_uses_max(self) -> None:
        """VEP uses & to join multiple consequence terms."""
        compound = "missense_variant&splice_region_variant"
        assert consequence_severity(compound) == consequence_severity("missense_variant")


# ── VEPRecord ─────────────────────────────────────────────


class TestVEPRecord:
    def test_to_dict_basic(self) -> None:
        r = VEPRecord(
            rsid="rs1801133",
            chrom="1",
            pos=11856378,
            ref="G",
            alt="A",
            gene_symbol="MTHFR",
            consequence="missense_variant",
            mane_select=1,
        )
        d = r.to_dict()
        assert d["rsid"] == "rs1801133"
        assert d["chrom"] == "1"
        assert d["pos"] == 11856378
        assert d["gene_symbol"] == "MTHFR"
        assert d["mane_select"] == 1

    def test_to_dict_nullable_fields(self) -> None:
        r = VEPRecord(rsid="rs12345", chrom="1", pos=100, ref="A", alt="G")
        d = r.to_dict()
        assert d["gene_symbol"] is None
        assert d["hgvs_coding"] is None
        assert d["exon_number"] is None
        assert d["mane_select"] == 0


# ── Seed CSV Loading ───────────────────────────────────────


class TestLoadSeedCSV:
    def test_loads_vep_seed(self) -> None:
        stats = BuildStats()
        rows = load_seed_csv(VEP_SEED_CSV, stats)
        assert len(rows) >= 50, f"Expected >=50 rows, got {len(rows)}"

    def test_known_variant_rs1801133(self) -> None:
        """T2-01: VEP bundle contains correct fields for rs1801133 / MTHFR C677T."""
        stats = BuildStats()
        rows = load_seed_csv(VEP_SEED_CSV, stats)
        mthfr = [r for r in rows if r["rsid"] == "rs1801133"]
        assert len(mthfr) >= 1, "rs1801133 not found in VEP seed"
        row = mthfr[0]
        assert row["chrom"] == "1"
        assert row["pos"] == 11856378
        assert row["gene_symbol"] == "MTHFR"
        assert row["consequence"] == "missense_variant"
        assert row["hgvs_coding"] == "c.665C>T"
        assert row["hgvs_protein"] == "p.Ala222Val"
        assert row["transcript_id"] == "ENST00000376592"
        assert row["mane_select"] == 1

    def test_known_variant_rs429358_apoe(self) -> None:
        """APOE rs429358 is correctly represented."""
        stats = BuildStats()
        rows = load_seed_csv(VEP_SEED_CSV, stats)
        apoe = [r for r in rows if r["rsid"] == "rs429358"]
        assert len(apoe) >= 1
        row = apoe[0]
        assert row["chrom"] == "19"
        assert row["pos"] == 44908684
        assert row["gene_symbol"] == "APOE"

    def test_frameshift_variant_present(self) -> None:
        stats = BuildStats()
        rows = load_seed_csv(VEP_SEED_CSV, stats)
        frameshift = [r for r in rows if r.get("consequence") == "frameshift_variant"]
        assert len(frameshift) >= 1, "No frameshift variants found"

    def test_stats_populated(self) -> None:
        stats = BuildStats()
        rows = load_seed_csv(VEP_SEED_CSV, stats)
        assert stats.variants_stored == len(rows)
        assert len(stats.unique_genes) > 0
        assert len(stats.consequence_counts) > 0
        assert stats.mane_select_count > 0

    def test_mane_select_flagged(self) -> None:
        """T2-03: MANE Select transcript is flagged when present."""
        stats = BuildStats()
        rows = load_seed_csv(VEP_SEED_CSV, stats)
        mane_rows = [r for r in rows if r["mane_select"] == 1]
        assert len(mane_rows) > 0, "No MANE Select transcripts found"
        # Specific known MANE Select: MTHFR ENST00000376592
        mthfr_mane = [r for r in mane_rows if r["transcript_id"] == "ENST00000376592"]
        assert len(mthfr_mane) >= 1, "MTHFR MANE Select not flagged"


# ── VEP VCF Parsing ──────────────────────────────────────


class TestParseVepVCF:
    """Test VEP VCF parsing with synthetic VCF data."""

    @pytest.fixture
    def sample_vep_vcf(self, tmp_path: Path) -> Path:
        """Create a minimal VEP-annotated VCF for testing."""
        csq_hdr = (
            "Allele|Consequence|IMPACT|SYMBOL|Gene"
            "|Feature_type|Feature|BIOTYPE|EXON|INTRON"
            "|HGVSc|HGVSp|STRAND|FLAGS|MANE_SELECT"
        )
        meta = f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: {csq_hdr}">'
        lines = [
            "##fileformat=VCFv4.2",
            meta,
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO",
            (
                "1\t11856378\trs1801133\tG\tA\t.\t.\t"
                "CSQ=A|missense_variant|MODERATE|MTHFR|4524"
                "|Transcript|ENST00000376592|protein_coding"
                "|5/11||ENST00000376592:c.665C>T"
                "|ENST00000376592:p.Ala222Val|-1||NM_005957.5"
            ),
            (
                "19\t44908684\trs429358\tT\tC\t.\t.\t"
                "CSQ=C|missense_variant|MODERATE|APOE|348"
                "|Transcript|ENST00000252486|protein_coding"
                "|4/4||ENST00000252486:c.388T>C"
                "|ENST00000252486:p.Cys130Arg|1|mane_select"
                "|NM_000041.4"
            ),
            (
                "7\t117559590\trs113993960\tATCT\tA\t.\t.\t"
                "CSQ=A|inframe_deletion|MODERATE|CFTR|1080"
                "|Transcript|ENST00000003084|protein_coding"
                "|11/27||ENST00000003084:c.1521_1523del"
                "|ENST00000003084:p.Phe508del|1||NM_000492.4"
            ),
        ]
        vcf_path = tmp_path / "test_vep.vcf"
        vcf_path.write_text("\n".join(lines) + "\n")
        return vcf_path

    @pytest.fixture
    def multi_transcript_vcf(self, tmp_path: Path) -> Path:
        """VCF with multiple transcripts per variant for severity testing."""
        csq_hdr = (
            "Allele|Consequence|IMPACT|SYMBOL|Gene"
            "|Feature_type|Feature|BIOTYPE|EXON|INTRON"
            "|HGVSc|HGVSp|STRAND|FLAGS|MANE_SELECT"
        )
        meta = f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: {csq_hdr}">'
        csq1 = (
            "G|synonymous_variant|LOW|GENE1|123"
            "|Transcript|ENST00000001|protein_coding"
            "|2/5||ENST00000001:c.123A>G||1||"
        )
        csq2 = (
            "G|missense_variant|MODERATE|GENE1|123"
            "|Transcript|ENST00000002|protein_coding"
            "|2/5||ENST00000002:c.123A>G"
            "|ENST00000002:p.Leu41Met|1||NM_001.1"
        )
        lines = [
            "##fileformat=VCFv4.2",
            meta,
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO",
            f"1\t100000\trs99999\tA\tG\t.\t.\tCSQ={csq1},{csq2}",
        ]
        vcf_path = tmp_path / "multi_transcript.vcf"
        vcf_path.write_text("\n".join(lines) + "\n")
        return vcf_path

    def test_parses_basic_vcf(self, sample_vep_vcf: Path) -> None:
        stats = BuildStats()
        rows = parse_vep_vcf(sample_vep_vcf, stats)
        assert len(rows) == 3

    def test_extracts_rsid(self, sample_vep_vcf: Path) -> None:
        stats = BuildStats()
        rows = parse_vep_vcf(sample_vep_vcf, stats)
        rsids = {r["rsid"] for r in rows}
        assert "rs1801133" in rsids
        assert "rs429358" in rsids

    def test_extracts_gene_symbol(self, sample_vep_vcf: Path) -> None:
        stats = BuildStats()
        rows = parse_vep_vcf(sample_vep_vcf, stats)
        mthfr = [r for r in rows if r["rsid"] == "rs1801133"]
        assert mthfr[0]["gene_symbol"] == "MTHFR"

    def test_extracts_consequence(self, sample_vep_vcf: Path) -> None:
        stats = BuildStats()
        rows = parse_vep_vcf(sample_vep_vcf, stats)
        rs113 = [r for r in rows if r["rsid"] == "rs113993960"]
        assert rs113[0]["consequence"] == "inframe_deletion"

    def test_strips_hgvs_prefix(self, sample_vep_vcf: Path) -> None:
        stats = BuildStats()
        rows = parse_vep_vcf(sample_vep_vcf, stats)
        mthfr = [r for r in rows if r["rsid"] == "rs1801133"]
        assert mthfr[0]["hgvs_coding"] == "c.665C>T"
        assert mthfr[0]["hgvs_protein"] == "p.Ala222Val"

    def test_parses_strand(self, sample_vep_vcf: Path) -> None:
        stats = BuildStats()
        rows = parse_vep_vcf(sample_vep_vcf, stats)
        mthfr = [r for r in rows if r["rsid"] == "rs1801133"]
        assert mthfr[0]["strand"] == "-"
        apoe = [r for r in rows if r["rsid"] == "rs429358"]
        assert apoe[0]["strand"] == "+"

    def test_mane_select_detection(self, sample_vep_vcf: Path) -> None:
        """T2-03: MANE Select detected from FLAGS or MANE_SELECT field."""
        stats = BuildStats()
        rows = parse_vep_vcf(sample_vep_vcf, stats)
        # rs429358 has mane_select in FLAGS
        apoe = [r for r in rows if r["rsid"] == "rs429358"]
        assert apoe[0]["mane_select"] == 1
        # rs1801133 has NM_ in MANE_SELECT field
        mthfr = [r for r in rows if r["rsid"] == "rs1801133"]
        assert mthfr[0]["mane_select"] == 1

    def test_most_severe_consequence_selected(self, multi_transcript_vcf: Path) -> None:
        """T2-02: Most-severe consequence correctly selected (missense > synonymous)."""
        stats = BuildStats()
        rows = parse_vep_vcf(multi_transcript_vcf, stats)
        assert len(rows) == 1
        # Should pick missense_variant (more severe) over synonymous_variant
        assert rows[0]["consequence"] == "missense_variant"
        assert rows[0]["transcript_id"] == "ENST00000002"

    def _make_vcf(self, tmp_path: Path, name: str, data_line: str) -> Path:
        """Helper to create a VCF with a single data line."""
        csq_hdr = (
            "Allele|Consequence|IMPACT|SYMBOL|Gene"
            "|Feature_type|Feature|BIOTYPE|EXON|INTRON"
            "|HGVSc|HGVSp|STRAND|FLAGS|MANE_SELECT"
        )
        meta = f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: {csq_hdr}">'
        lines = [
            "##fileformat=VCFv4.2",
            meta,
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO",
            data_line,
        ]
        vcf_path = tmp_path / name
        vcf_path.write_text("\n".join(lines) + "\n")
        return vcf_path

    def test_skips_invalid_chromosome(self, tmp_path: Path) -> None:
        csq = "G|intron_variant|LOW|GENE|1|Transcript|ENST1|coding|||||1||"
        vcf_path = self._make_vcf(
            tmp_path,
            "bad_chrom.vcf",
            f"GL000220.1\t100\trs999\tA\tG\t.\t.\tCSQ={csq}",
        )
        stats = BuildStats()
        rows = parse_vep_vcf(vcf_path, stats)
        assert len(rows) == 0
        assert stats.skipped_invalid_chrom == 1

    def test_skips_no_rsid(self, tmp_path: Path) -> None:
        csq = "G|intron_variant|LOW|GENE|1|Transcript|ENST1|coding|||||1||"
        vcf_path = self._make_vcf(
            tmp_path,
            "no_rsid.vcf",
            f"1\t100\t.\tA\tG\t.\t.\tCSQ={csq}",
        )
        stats = BuildStats()
        rows = parse_vep_vcf(vcf_path, stats)
        assert len(rows) == 0
        assert stats.skipped_no_rsid == 1

    def test_stats_updated(self, sample_vep_vcf: Path) -> None:
        stats = BuildStats()
        parse_vep_vcf(sample_vep_vcf, stats)
        assert stats.total_input_lines == 3
        assert stats.variants_stored == 3
        assert len(stats.unique_genes) >= 3

    def test_union_input_no_duplicates(self, tmp_path: Path) -> None:
        """Step 2: when the union-VCF input from step 1 contains the same
        (rsid, alt) twice — overlap between 23andMe and AncestryDNA catalogs —
        the bundle stores one row per (rsid, alt), not two."""
        csq_hdr = (
            "Allele|Consequence|IMPACT|SYMBOL|Gene"
            "|Feature_type|Feature|BIOTYPE|EXON|INTRON"
            "|HGVSc|HGVSp|STRAND|FLAGS|MANE_SELECT"
        )
        meta = f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: {csq_hdr}">'
        csq = (
            "A|missense_variant|MODERATE|MTHFR|4524"
            "|Transcript|ENST00000376592|protein_coding"
            "|5/11||ENST00000376592:c.665C>T"
            "|ENST00000376592:p.Ala222Val|-1||NM_005957.5"
        )
        apoe_csq = (
            "C|missense_variant|MODERATE|APOE|348"
            "|Transcript|ENST00000252486|protein_coding"
            "|4/4||ENST00000252486:c.388T>C"
            "|ENST00000252486:p.Cys130Arg|1|mane_select|NM_000041.4"
        )
        # Same (rsid, alt) appears twice (simulating union overlap) with
        # differing non-key fields (CSQ transcript); a unique rsid appears
        # once. Expected: 2 rows total, not 3 — dedup is keyed by (rsid, alt),
        # not by full-row equality.
        csq_variant = csq.replace("ENST00000376592", "ENST00000376593")
        lines = [
            "##fileformat=VCFv4.2",
            meta,
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO",
            f"1\t11856378\trs1801133\tG\tA\t.\t.\tCSQ={csq}",
            f"1\t11856378\trs1801133\tG\tA\t.\t.\tCSQ={csq_variant}",
            f"19\t44908684\trs429358\tT\tC\t.\t.\tCSQ={apoe_csq}",
        ]
        vcf_path = tmp_path / "union.vcf"
        vcf_path.write_text("\n".join(lines) + "\n")

        stats = BuildStats()
        rows = parse_vep_vcf(vcf_path, stats)

        rsids = sorted(r["rsid"] for r in rows)
        assert rsids == ["rs1801133", "rs429358"]
        assert len(rows) == 2, f"expected 2 unique rows, got {len(rows)}"
        assert stats.total_input_lines == 3
        assert stats.variants_stored == 2

        # And the built database carries the union row count (no duplicates).
        db_path = tmp_path / "union.db"
        build_bundle_db(
            rows,
            db_path,
            ensembl_version="112",
            bundle_version="v2.0.0",
        )
        with sqlite3.connect(str(db_path)) as conn:
            db_count = conn.execute("SELECT count(*) FROM vep_annotations").fetchone()[0]
            meta_rows = dict(conn.execute("SELECT key, value FROM bundle_metadata").fetchall())
        assert db_count == 2
        assert int(meta_rows["variant_count"]) == 2
        assert meta_rows["bundle_version"] == "v2.0.0"


# ── Database Building ──────────────────────────────────────


class TestBuildBundleDB:
    """Test SQLite database creation."""

    @pytest.fixture
    def seed_rows(self) -> list[dict]:
        stats = BuildStats()
        return load_seed_csv(VEP_SEED_CSV, stats)

    def test_creates_database(self, tmp_path: Path, seed_rows: list[dict]) -> None:
        db_path = tmp_path / "test_vep.db"
        build_bundle_db(seed_rows, db_path, ensembl_version="112")
        assert db_path.exists()

    def test_schema_correct(self, tmp_path: Path, seed_rows: list[dict]) -> None:
        db_path = tmp_path / "test_vep.db"
        build_bundle_db(seed_rows, db_path, ensembl_version="112")

        with sqlite3.connect(str(db_path)) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "vep_annotations" in tables
        assert "bundle_metadata" in tables

    def test_row_count(self, tmp_path: Path, seed_rows: list[dict]) -> None:
        db_path = tmp_path / "test_vep.db"
        build_bundle_db(seed_rows, db_path, ensembl_version="112")

        with sqlite3.connect(str(db_path)) as conn:
            count = conn.execute("SELECT count(*) FROM vep_annotations").fetchone()[0]
        assert count == len(seed_rows)

    def test_wal_mode(self, tmp_path: Path, seed_rows: list[dict]) -> None:
        db_path = tmp_path / "test_vep.db"
        build_bundle_db(seed_rows, db_path, ensembl_version="112")

        with sqlite3.connect(str(db_path)) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_indexes_created(self, tmp_path: Path, seed_rows: list[dict]) -> None:
        db_path = tmp_path / "test_vep.db"
        build_bundle_db(seed_rows, db_path, ensembl_version="112")

        with sqlite3.connect(str(db_path)) as conn:
            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
        assert "idx_vep_rsid" in indexes
        assert "idx_vep_chrom_pos" in indexes

    def test_metadata_stored(self, tmp_path: Path, seed_rows: list[dict]) -> None:
        db_path = tmp_path / "test_vep.db"
        build_bundle_db(seed_rows, db_path, ensembl_version="112")

        with sqlite3.connect(str(db_path)) as conn:
            meta = dict(conn.execute("SELECT key, value FROM bundle_metadata").fetchall())
        assert meta["ensembl_version"] == "112"
        assert meta["schema_version"] == "1"
        assert int(meta["variant_count"]) == len(seed_rows)

    def test_bundle_version_round_trips_to_metadata(
        self, tmp_path: Path, seed_rows: list[dict]
    ) -> None:
        """Step 2: --bundle-version writes to bundle_metadata.bundle_version
        alongside ensembl_version/build_date/variant_count/schema_version
        (Plan §5.5)."""
        db_path = tmp_path / "test_vep.db"
        build_bundle_db(
            seed_rows,
            db_path,
            ensembl_version="112",
            bundle_version="v2.0.0",
        )

        with sqlite3.connect(str(db_path)) as conn:
            meta = dict(conn.execute("SELECT key, value FROM bundle_metadata").fetchall())
        assert meta["bundle_version"] == "v2.0.0"
        assert meta["ensembl_version"] == "112"
        assert meta["schema_version"] == "1"
        assert "build_date" in meta
        assert int(meta["variant_count"]) == len(seed_rows)

    def test_bundle_version_omitted_when_none(self, tmp_path: Path, seed_rows: list[dict]) -> None:
        """bundle_version key is absent when the arg is not supplied —
        bundles built before v2.0.0 (Plan §5.5 contract clause 3)."""
        db_path = tmp_path / "test_vep.db"
        build_bundle_db(seed_rows, db_path, ensembl_version="112")

        with sqlite3.connect(str(db_path)) as conn:
            meta = dict(conn.execute("SELECT key, value FROM bundle_metadata").fetchall())
        assert "bundle_version" not in meta

    def test_returns_sha256(self, tmp_path: Path, seed_rows: list[dict]) -> None:
        db_path = tmp_path / "test_vep.db"
        sha = build_bundle_db(seed_rows, db_path, ensembl_version="112")
        assert len(sha) == 64  # SHA-256 hex digest length
        assert all(c in "0123456789abcdef" for c in sha)

    def test_data_integrity_rs1801133(self, tmp_path: Path, seed_rows: list[dict]) -> None:
        """T2-01: Verify rs1801133 / MTHFR C677T is correctly stored in DB."""
        db_path = tmp_path / "test_vep.db"
        build_bundle_db(seed_rows, db_path, ensembl_version="112")

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM vep_annotations WHERE rsid = 'rs1801133'").fetchone()

        assert row is not None, "rs1801133 not found in database"
        assert row["chrom"] == "1"
        assert row["pos"] == 11856378
        assert row["gene_symbol"] == "MTHFR"
        assert row["consequence"] == "missense_variant"
        assert row["hgvs_coding"] == "c.665C>T"
        assert row["hgvs_protein"] == "p.Ala222Val"
        assert row["transcript_id"] == "ENST00000376592"
        assert row["mane_select"] == 1

    def test_idempotent_build(self, tmp_path: Path, seed_rows: list[dict]) -> None:
        """Running build twice produces identical results."""
        db_path = tmp_path / "test_vep.db"

        sha1 = build_bundle_db(seed_rows, db_path, ensembl_version="112", build_date="2024-01-01")
        with sqlite3.connect(str(db_path)) as conn:
            count1 = conn.execute("SELECT count(*) FROM vep_annotations").fetchone()[0]

        sha2 = build_bundle_db(seed_rows, db_path, ensembl_version="112", build_date="2024-01-01")
        with sqlite3.connect(str(db_path)) as conn:
            count2 = conn.execute("SELECT count(*) FROM vep_annotations").fetchone()[0]

        assert count1 == count2
        assert sha1 == sha2

    def test_lookup_by_rsid(self, tmp_path: Path, seed_rows: list[dict]) -> None:
        """Verify rsid index enables fast lookups."""
        db_path = tmp_path / "test_vep.db"
        build_bundle_db(seed_rows, db_path, ensembl_version="112")

        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute(
                "SELECT rsid, gene_symbol FROM vep_annotations WHERE rsid IN (?, ?, ?)",
                ("rs429358", "rs7412", "rs1801133"),
            ).fetchall()

        found_rsids = {r[0] for r in rows}
        assert "rs429358" in found_rsids
        assert "rs7412" in found_rsids
        assert "rs1801133" in found_rsids

    def test_lookup_by_chrom_pos(self, tmp_path: Path, seed_rows: list[dict]) -> None:
        """Verify chrom+pos index enables position-based lookups."""
        db_path = tmp_path / "test_vep.db"
        build_bundle_db(seed_rows, db_path, ensembl_version="112")

        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT rsid, gene_symbol FROM vep_annotations WHERE chrom = ? AND pos = ?",
                ("19", 44908684),
            ).fetchone()

        assert row is not None
        assert row[0] == "rs429358"
        assert row[1] == "APOE"


# ── Coverage Report ────────────────────────────────────────


class TestCoverageReport:
    def test_basic_report(self) -> None:
        rows = [
            {"rsid": "rs1", "consequence": "missense_variant"},
            {"rsid": "rs2", "consequence": "intron_variant"},
            {"rsid": "rs3", "consequence": None},
        ]
        report = coverage_report(rows)
        assert report["total_variants"] == 3
        assert report["unique_rsids"] == 3
        assert report["annotated_with_consequence"] == 2

    def test_with_catalog(self, tmp_path: Path) -> None:
        catalog = tmp_path / "catalog.txt"
        catalog.write_text("rs1\nrs2\nrs3\nrs4\nrs5\n")
        rows = [
            {"rsid": "rs1", "consequence": "missense_variant"},
            {"rsid": "rs2", "consequence": "intron_variant"},
            {"rsid": "rs99", "consequence": "synonymous_variant"},
        ]
        report = coverage_report(rows, catalog)
        assert report["catalog_size"] == 5
        assert report["catalog_covered"] == 2
        assert report["coverage_percent"] == 40.0


# ── CLI / Script Integration ───────────────────────────────────


class TestCLI:
    def test_dry_run_seed_csv(self, tmp_path: Path) -> None:
        """Dry run does not create files."""
        output = tmp_path / "vep_bundle.db"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--seed-csv",
                str(VEP_SEED_CSV),
                "--output",
                str(output),
                "--dry-run",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert not output.exists()
        assert "DRY RUN" in result.stdout

    def test_build_from_seed_csv(self, tmp_path: Path) -> None:
        """Full build from seed CSV creates valid database."""
        output = tmp_path / "vep_bundle.db"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--seed-csv",
                str(VEP_SEED_CSV),
                "--output",
                str(output),
                "--ensembl-version",
                "112",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed:\n{result.stderr}"
        assert output.exists()

        # Verify the database
        with sqlite3.connect(str(output)) as conn:
            count = conn.execute("SELECT count(*) FROM vep_annotations").fetchone()[0]
        assert count >= 50

    def test_cli_bundle_version_round_trips(self, tmp_path: Path) -> None:
        """Step 2: --bundle-version on the CLI round-trips into bundle_metadata."""
        output = tmp_path / "vep_bundle.db"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--seed-csv",
                str(VEP_SEED_CSV),
                "--output",
                str(output),
                "--ensembl-version",
                "112",
                "--bundle-version",
                "v2.0.0",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed:\n{result.stderr}"
        with sqlite3.connect(str(output)) as conn:
            meta = dict(conn.execute("SELECT key, value FROM bundle_metadata").fetchall())
        assert meta["bundle_version"] == "v2.0.0"
        assert meta["ensembl_version"] == "112"

    def test_write_stats_json(self, tmp_path: Path) -> None:
        """--write-stats produces a JSON stats file."""
        output = tmp_path / "vep_bundle.db"
        stats_file = tmp_path / "stats.json"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--seed-csv",
                str(VEP_SEED_CSV),
                "--output",
                str(output),
                "--write-stats",
                str(stats_file),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert stats_file.exists()

        import json

        stats = json.loads(stats_file.read_text())
        assert stats["variants_stored"] >= 50
        assert stats["unique_genes"] > 0

    def test_nonexistent_input(self, tmp_path: Path) -> None:
        """Script exits with error for missing input file."""
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--vep-vcf",
                str(tmp_path / "nonexistent.vcf"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0

    def test_mutually_exclusive_inputs(self) -> None:
        """Cannot specify both --vep-vcf and --seed-csv."""
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--vep-vcf",
                "a.vcf",
                "--seed-csv",
                "b.csv",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0


# ── BuildStats ────────────────────────────────────────────


class TestBuildStats:
    def test_summary_output(self) -> None:
        stats = BuildStats(
            total_input_lines=1000,
            variants_stored=950,
            skipped_no_rsid=30,
            skipped_invalid_chrom=10,
            skipped_malformed=10,
            elapsed_seconds=5.5,
        )
        stats.unique_genes = {"BRCA1", "APOE", "MTHFR"}
        stats.mane_select_count = 500

        summary = stats.summary()
        assert "1,000" in summary
        assert "950" in summary
        assert "5.5s" in summary


class TestCanonicalTiebreak:
    """CANONICAL fallback for transcript selection on GRCh37 (#1295).

    MANE_SELECT is GRCh38-only, so on the GRCh37 bundle build the MANE tiebreak
    never fires and `_pick_best` collapses to pure most-severe-consequence —
    surfacing a minor isoform's consequence/HGVS over the canonical transcript's.
    GRCh37 VEP emits CANONICAL, used here as the MANE-equivalent tiebreak.
    """

    def test_is_mane_select_canonical_fallback(self) -> None:
        # CANONICAL="YES" with no MANE_SELECT → preferred (the GRCh37 case).
        assert _is_mane_select({"CANONICAL": "YES"}) is True
        assert _is_mane_select({"CANONICAL": "1"}) is True
        # Not canonical and no MANE → not preferred.
        assert _is_mane_select({"CANONICAL": ""}) is False
        assert _is_mane_select({"CANONICAL": "-", "FLAGS": "", "MANE_SELECT": ""}) is False
        # MANE_SELECT still wins when present (GRCh38).
        assert _is_mane_select({"MANE_SELECT": "NM_000041.4", "CANONICAL": ""}) is True

    def test_parse_picks_canonical_over_more_severe_noncanonical(self, tmp_path: Path) -> None:
        # AGTRAP rs771467011-style: the canonical transcript is synonymous (silent)
        # while a non-canonical isoform is stop_gained. Without the CANONICAL
        # fallback the bundle surfaced the stop_gained (LoF) call; it must now pick
        # the canonical synonymous transcript (#1295).
        csq_hdr = (
            "Allele|Consequence|IMPACT|SYMBOL|Gene"
            "|Feature_type|Feature|BIOTYPE|EXON|INTRON"
            "|HGVSc|HGVSp|STRAND|FLAGS|MANE_SELECT|CANONICAL"
        )
        meta = f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: {csq_hdr}">'
        # Non-canonical, more-severe stop_gained listed FIRST (so neither first-seen
        # nor severity would pick the canonical one without the fallback).
        noncanonical = (
            "A|stop_gained|HIGH|AGTRAP|57|Transcript|ENST00000510878|protein_coding"
            "|2/4||ENST00000510878:c.188C>A|ENST00000510878:p.Ser63Ter|1|||"
        )
        canonical = (
            "A|synonymous_variant|LOW|AGTRAP|57|Transcript|ENST00000314340|protein_coding"
            "|3/5||ENST00000314340:c.294C>A|ENST00000314340:p.Leu98=|1|||YES"
        )
        lines = [
            "##fileformat=VCFv4.2",
            meta,
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO",
            f"1\t11790000\trs771467011\tC\tA\t.\t.\tCSQ={noncanonical},{canonical}",
        ]
        vcf_path = tmp_path / "canonical_tiebreak.vcf"
        vcf_path.write_text("\n".join(lines) + "\n")

        rows = parse_vep_vcf(vcf_path, BuildStats())
        agtrap = [r for r in rows if r["rsid"] == "rs771467011"]
        assert len(agtrap) == 1
        row = agtrap[0]
        assert row["transcript_id"] == "ENST00000314340"
        assert row["consequence"] == "synonymous_variant"
        assert row["hgvs_protein"] == "p.Leu98="
        assert row["mane_select"] == 1
