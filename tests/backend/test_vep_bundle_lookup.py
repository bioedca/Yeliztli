"""Tests for VEP bundle lookup client (P2-02, P2-03).

Covers:
- lookup_vep_by_rsids: batch rsid matching against VEP bundle
- lookup_vep_by_positions: (chrom, pos) fallback matching
- annotate_sample_vep: full end-to-end annotation pipeline
- MANE Select preference and consequence severity ranking
- Bitmask OR logic for annotation_coverage (bit 0)
- Edge cases: empty inputs, no matches, re-annotation
- T2-01: Known variant correctness (rs1801133 / MTHFR C677T)
- T2-06: Performance baseline (1000 variants < 5 seconds)
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.annotation.vep_bundle import (
    CONSEQUENCE_SEVERITY,
    VEP_BITMASK,
    VEPAnnotationResult,
    annotate_sample_vep,
    consequence_severity,
    lookup_vep_by_positions,
    lookup_vep_by_rsids,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, raw_variants

# ── Fixtures ────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
VEP_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "vep_seed.csv"
MINI_VEP_DB = FIXTURES_DIR / "mini_vep_bundle.db"


@pytest.fixture
def vep_engine() -> sa.Engine:
    """SQLAlchemy engine for the mini VEP bundle fixture DB."""
    assert MINI_VEP_DB.exists(), f"Missing fixture: {MINI_VEP_DB}"
    engine = sa.create_engine(f"sqlite:///{MINI_VEP_DB}")
    yield engine
    engine.dispose()


@pytest.fixture
def vep_engine_inmemory() -> sa.Engine:
    """In-memory VEP bundle loaded from the seed CSV for fast tests."""
    engine = sa.create_engine("sqlite://")
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
        conn.execute(sa.text("CREATE INDEX idx_vep_chrom_pos ON vep_annotations(chrom, pos)"))
        # Load seed CSV
        with open(VEP_SEED_CSV) as f:
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
def sample_engine() -> sa.Engine:
    """In-memory sample engine with tables created."""
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    return engine


SEED_RAW_VARIANTS = [
    {"rsid": "rs429358", "chrom": "19", "pos": 44908684, "genotype": "TC"},
    {"rsid": "rs7412", "chrom": "19", "pos": 44908822, "genotype": "CC"},
    {"rsid": "rs1801133", "chrom": "1", "pos": 11856378, "genotype": "AG"},
    {"rsid": "rs4680", "chrom": "22", "pos": 19963748, "genotype": "AG"},
    {
        "rsid": "rs80357906",
        "chrom": "17",
        "pos": 43091983,
        "genotype": "CT",
    },
    {
        "rsid": "rs113993960",
        "chrom": "7",
        "pos": 117559590,
        "genotype": "AA",
    },
    {"rsid": "rs12345", "chrom": "1", "pos": 100000, "genotype": "AA"},
    {
        "rsid": "rs12913832",
        "chrom": "15",
        "pos": 28365618,
        "genotype": "GG",
    },
    {
        "rsid": "rs7903146",
        "chrom": "10",
        "pos": 114758349,
        "genotype": "CT",
    },
]


def _insert_vep_rows(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
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
            rows,
        )


@pytest.fixture
def sample_with_variants(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine pre-loaded with known raw variants."""
    with sample_engine.begin() as conn:
        conn.execute(raw_variants.insert(), SEED_RAW_VARIANTS)
    return sample_engine


# ═══════════════════════════════════════════════════════════════════════
# Consequence severity ranking
# ═══════════════════════════════════════════════════════════════════════


class TestConsequenceSeverity:
    """Tests for the consequence severity helper."""

    def test_stop_gained_more_severe_than_missense(self) -> None:
        assert consequence_severity("stop_gained") > consequence_severity("missense_variant")

    def test_missense_more_severe_than_synonymous(self) -> None:
        assert consequence_severity("missense_variant") > consequence_severity(
            "synonymous_variant"
        )

    def test_compound_consequence_uses_max(self) -> None:
        compound = "missense_variant&splice_region_variant"
        assert consequence_severity(compound) == CONSEQUENCE_SEVERITY["missense_variant"]

    def test_none_returns_negative(self) -> None:
        assert consequence_severity(None) == -1

    def test_empty_string_returns_negative(self) -> None:
        assert consequence_severity("") == -1

    def test_unknown_term_returns_zero(self) -> None:
        assert consequence_severity("totally_unknown_term") == 0


# ═══════════════════════════════════════════════════════════════════════
# lookup_vep_by_rsids
# ═══════════════════════════════════════════════════════════════════════


class TestLookupByRsids:
    """Tests for rsid-based VEP bundle lookup."""

    def test_single_rsid_match(self, vep_engine_inmemory: sa.Engine) -> None:
        result = lookup_vep_by_rsids(["rs429358"], vep_engine_inmemory)
        assert len(result) == 1
        annot = result["rs429358"]
        assert annot.gene_symbol == "APOE"
        assert annot.consequence == "missense_variant"
        assert annot.matched_by == "rsid"

    def test_multiple_rsids(self, vep_engine_inmemory: sa.Engine) -> None:
        rsids = ["rs429358", "rs7412", "rs1801133", "rs4680"]
        result = lookup_vep_by_rsids(rsids, vep_engine_inmemory)
        assert len(result) == 4
        assert all(r.matched_by == "rsid" for r in result.values())

    def test_no_match(self, vep_engine_inmemory: sa.Engine) -> None:
        result = lookup_vep_by_rsids(["rs999999999"], vep_engine_inmemory)
        assert len(result) == 0

    def test_partial_match(self, vep_engine_inmemory: sa.Engine) -> None:
        rsids = ["rs429358", "rs_nonexistent"]
        result = lookup_vep_by_rsids(rsids, vep_engine_inmemory)
        assert len(result) == 1
        assert "rs429358" in result

    def test_empty_input(self, vep_engine_inmemory: sa.Engine) -> None:
        result = lookup_vep_by_rsids([], vep_engine_inmemory)
        assert len(result) == 0

    def test_known_variant_rs1801133_mthfr(self, vep_engine_inmemory: sa.Engine) -> None:
        """T2-01: VEP bundle returns correct fields for rs1801133 (MTHFR C677T)."""
        result = lookup_vep_by_rsids(["rs1801133"], vep_engine_inmemory)
        assert "rs1801133" in result
        annot = result["rs1801133"]
        assert annot.gene_symbol == "MTHFR"
        assert annot.transcript_id == "ENST00000376592"
        assert annot.consequence == "missense_variant"
        assert annot.hgvs_coding == "c.665C>T"
        assert annot.hgvs_protein == "p.Ala222Val"
        assert annot.strand == "-"
        assert annot.exon_number == 5
        assert annot.mane_select is True

    def test_apoe_rs429358(self, vep_engine_inmemory: sa.Engine) -> None:
        """APOE rs429358 returns correct annotation data."""
        result = lookup_vep_by_rsids(["rs429358"], vep_engine_inmemory)
        annot = result["rs429358"]
        assert annot.gene_symbol == "APOE"
        assert annot.transcript_id == "ENST00000252486"
        assert annot.hgvs_coding == "c.388T>C"
        assert annot.hgvs_protein == "p.Cys130Arg"
        assert annot.strand == "+"
        assert annot.exon_number == 4
        assert annot.mane_select is True

    def test_frameshift_variant(self, vep_engine_inmemory: sa.Engine) -> None:
        """BRCA1 frameshift returns correct consequence."""
        result = lookup_vep_by_rsids(["rs80357906"], vep_engine_inmemory)
        assert "rs80357906" in result
        annot = result["rs80357906"]
        assert annot.consequence == "frameshift_variant"
        assert annot.gene_symbol == "BRCA1"

    def test_intron_variant(self, vep_engine_inmemory: sa.Engine) -> None:
        """Intron variant has intron_number populated."""
        result = lookup_vep_by_rsids(["rs12913832"], vep_engine_inmemory)
        assert "rs12913832" in result
        annot = result["rs12913832"]
        assert annot.consequence == "intron_variant"
        assert annot.intron_number == 15
        assert annot.exon_number is None

    def test_large_batch_exceeding_sqlite_limit(self, vep_engine_inmemory: sa.Engine) -> None:
        """Test with >500 rsids triggers multi-batch processing."""
        rsids = [f"rs{i}" for i in range(600)]
        # Add known rsids to verify they're found
        rsids.extend(["rs429358", "rs7412"])
        result = lookup_vep_by_rsids(rsids, vep_engine_inmemory)
        assert "rs429358" in result
        assert "rs7412" in result

    def test_multiallelic_rsid_uses_carried_alt(self, vep_engine_inmemory: sa.Engine) -> None:
        """A multi-ALT rsID must not pick a non-carried severe consequence."""
        _insert_vep_rows(
            vep_engine_inmemory,
            [
                {
                    "rsid": "rs_multi",
                    "chrom": "1",
                    "pos": 101,
                    "ref": "A",
                    "alt": "C",
                    "gene_symbol": "GENE",
                    "transcript_id": "ENST_C",
                    "consequence": "synonymous_variant",
                    "hgvs_coding": "c.1A>C",
                    "hgvs_protein": "p.=",
                    "strand": "+",
                    "exon_number": 1,
                    "intron_number": None,
                    "mane_select": 0,
                },
                {
                    "rsid": "rs_multi",
                    "chrom": "1",
                    "pos": 101,
                    "ref": "A",
                    "alt": "G",
                    "gene_symbol": "GENE",
                    "transcript_id": "ENST_G",
                    "consequence": "stop_gained",
                    "hgvs_coding": "c.1A>G",
                    "hgvs_protein": "p.Ter",
                    "strand": "+",
                    "exon_number": 1,
                    "intron_number": None,
                    "mane_select": 0,
                },
            ],
        )

        result = lookup_vep_by_rsids(
            ["rs_multi"],
            vep_engine_inmemory,
            genotype_by_rsid={"rs_multi": "AC"},
        )

        assert result["rs_multi"].transcript_id == "ENST_C"
        assert result["rs_multi"].consequence == "synonymous_variant"
        assert result["rs_multi"].alt == "C"

    def test_multiallelic_rsid_without_carried_alt_is_unmatched(
        self,
        vep_engine_inmemory: sa.Engine,
    ) -> None:
        """Ambiguous multi-ALT rsIDs must not fall back to severity-only."""
        _insert_vep_rows(
            vep_engine_inmemory,
            [
                {
                    "rsid": "rs_multi_ref",
                    "chrom": "1",
                    "pos": 102,
                    "ref": "A",
                    "alt": "C",
                    "gene_symbol": "GENE",
                    "transcript_id": "ENST_C",
                    "consequence": "synonymous_variant",
                    "hgvs_coding": "c.2A>C",
                    "hgvs_protein": "p.=",
                    "strand": "+",
                    "exon_number": 1,
                    "intron_number": None,
                    "mane_select": 0,
                },
                {
                    "rsid": "rs_multi_ref",
                    "chrom": "1",
                    "pos": 102,
                    "ref": "A",
                    "alt": "G",
                    "gene_symbol": "GENE",
                    "transcript_id": "ENST_G",
                    "consequence": "stop_gained",
                    "hgvs_coding": "c.2A>G",
                    "hgvs_protein": "p.Ter",
                    "strand": "+",
                    "exon_number": 1,
                    "intron_number": None,
                    "mane_select": 0,
                },
            ],
        )

        result = lookup_vep_by_rsids(
            ["rs_multi_ref"],
            vep_engine_inmemory,
            genotype_by_rsid={"rs_multi_ref": "AA"},
        )

        assert result == {}


# ═══════════════════════════════════════════════════════════════════════
# lookup_vep_by_positions
# ═══════════════════════════════════════════════════════════════════════


class TestLookupByPositions:
    """Tests for (chrom, pos) fallback VEP lookup."""

    def test_single_position_match(self, vep_engine_inmemory: sa.Engine) -> None:
        positions = [("19", 44908684, "i_custom_rsid")]
        result = lookup_vep_by_positions(positions, vep_engine_inmemory)
        assert len(result) == 1
        assert "i_custom_rsid" in result
        annot = result["i_custom_rsid"]
        assert annot.gene_symbol == "APOE"
        assert annot.matched_by == "chrom_pos"

    def test_multiple_positions(self, vep_engine_inmemory: sa.Engine) -> None:
        positions = [
            ("19", 44908684, "custom1"),
            ("1", 11856378, "custom2"),
        ]
        result = lookup_vep_by_positions(positions, vep_engine_inmemory)
        assert len(result) == 2

    def test_no_match(self, vep_engine_inmemory: sa.Engine) -> None:
        positions = [("99", 999999999, "no_match")]
        result = lookup_vep_by_positions(positions, vep_engine_inmemory)
        assert len(result) == 0

    def test_empty_input(self, vep_engine_inmemory: sa.Engine) -> None:
        result = lookup_vep_by_positions([], vep_engine_inmemory)
        assert len(result) == 0

    def test_preserves_sample_rsid_as_key(self, vep_engine_inmemory: sa.Engine) -> None:
        """The result is keyed by the sample's rsid, not the bundle's."""
        positions = [("19", 44908684, "i12345")]
        result = lookup_vep_by_positions(positions, vep_engine_inmemory)
        assert "i12345" in result
        annot = result["i12345"]
        assert annot.rsid == "i12345"

    def test_sqlite_bind_limit(self, vep_engine_inmemory: sa.Engine) -> None:
        """500+ positions triggers multi-batch processing without bind error."""
        # Mix unique positions with known ones to exercise batching
        positions = [(f"chr{n}", n, f"i{n}") for n in range(500)]
        # Add a known position to verify at least one match
        positions.append(("19", 44908684, "i_known"))
        result = lookup_vep_by_positions(positions, vep_engine_inmemory)
        # Should complete without SQLite bind error
        assert "i_known" in result
        assert result["i_known"].gene_symbol == "APOE"

    def test_ambiguous_position_skipped(self) -> None:
        """Multiple bundle rsids at same (chrom, pos) are skipped."""
        engine = sa.create_engine("sqlite://")
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
            # Two different rsids at the same (chrom, pos)
            conn.execute(
                sa.text(
                    "INSERT INTO vep_annotations VALUES "
                    "('rs_a', '1', 100, 'A', 'G', 'GENE_A', "
                    "'ENST_A', 'missense_variant', NULL, NULL, "
                    "'+', NULL, NULL, 1)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO vep_annotations VALUES "
                    "('rs_b', '1', 100, 'C', 'T', 'GENE_B', "
                    "'ENST_B', 'synonymous_variant', NULL, NULL, "
                    "'+', NULL, NULL, 0)"
                )
            )
        positions = [("1", 100, "i_sample")]
        result = lookup_vep_by_positions(positions, engine)
        # Ambiguous: multiple rsids at same position, should be skipped
        assert len(result) == 0

    def test_multiallelic_position_uses_carried_alt(
        self,
        vep_engine_inmemory: sa.Engine,
    ) -> None:
        """Coordinate fallback also filters same-rsid multi-ALT rows by genotype."""
        _insert_vep_rows(
            vep_engine_inmemory,
            [
                {
                    "rsid": "rs_bundle_multi",
                    "chrom": "1",
                    "pos": 103,
                    "ref": "A",
                    "alt": "C",
                    "gene_symbol": "GENE",
                    "transcript_id": "ENST_C",
                    "consequence": "synonymous_variant",
                    "hgvs_coding": "c.3A>C",
                    "hgvs_protein": "p.=",
                    "strand": "+",
                    "exon_number": 1,
                    "intron_number": None,
                    "mane_select": 0,
                },
                {
                    "rsid": "rs_bundle_multi",
                    "chrom": "1",
                    "pos": 103,
                    "ref": "A",
                    "alt": "G",
                    "gene_symbol": "GENE",
                    "transcript_id": "ENST_G",
                    "consequence": "stop_gained",
                    "hgvs_coding": "c.3A>G",
                    "hgvs_protein": "p.Ter",
                    "strand": "+",
                    "exon_number": 1,
                    "intron_number": None,
                    "mane_select": 0,
                },
            ],
        )

        result = lookup_vep_by_positions(
            [("1", 103, "internal_probe")],
            vep_engine_inmemory,
            genotype_by_rsid={"internal_probe": "AC"},
        )

        assert result["internal_probe"].transcript_id == "ENST_C"
        assert result["internal_probe"].consequence == "synonymous_variant"
        assert result["internal_probe"].alt == "C"


# ═══════════════════════════════════════════════════════════════════════
# MANE Select & severity preference
# ═══════════════════════════════════════════════════════════════════════


class TestDeduplication:
    """Tests for MANE Select and consequence severity preference."""

    def test_prefers_mane_select(self) -> None:
        """When multiple transcripts exist, MANE Select is preferred."""
        engine = sa.create_engine("sqlite://")
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
            # Non-MANE with more severe consequence
            conn.execute(
                sa.text(
                    "INSERT INTO vep_annotations VALUES "
                    "('rs_test', '1', 100, 'A', 'G', 'GENE', "
                    "'ENST_OTHER', 'stop_gained', NULL, NULL, "
                    "'+', NULL, NULL, 0)"
                )
            )
            # MANE Select with less severe consequence
            conn.execute(
                sa.text(
                    "INSERT INTO vep_annotations VALUES "
                    "('rs_test', '1', 100, 'A', 'G', 'GENE', "
                    "'ENST_MANE', 'missense_variant', NULL, NULL, "
                    "'+', NULL, NULL, 1)"
                )
            )

        result = lookup_vep_by_rsids(["rs_test"], engine)
        assert result["rs_test"].transcript_id == "ENST_MANE"
        assert result["rs_test"].mane_select is True

    def test_prefers_most_severe_when_same_mane(self) -> None:
        """When MANE status is the same, most severe consequence wins."""
        engine = sa.create_engine("sqlite://")
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
            conn.execute(
                sa.text(
                    "INSERT INTO vep_annotations VALUES "
                    "('rs_test', '1', 100, 'A', 'G', 'GENE', "
                    "'ENST_1', 'synonymous_variant', NULL, NULL, "
                    "'+', NULL, NULL, 0)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO vep_annotations VALUES "
                    "('rs_test', '1', 100, 'A', 'G', 'GENE', "
                    "'ENST_2', 'missense_variant', NULL, NULL, "
                    "'+', NULL, NULL, 0)"
                )
            )

        result = lookup_vep_by_rsids(["rs_test"], engine)
        assert result["rs_test"].transcript_id == "ENST_2"
        assert result["rs_test"].consequence == "missense_variant"


# ═══════════════════════════════════════════════════════════════════════
# annotate_sample_vep (end-to-end)
# ═══════════════════════════════════════════════════════════════════════


class TestAnnotateSampleVep:
    """Tests for the full annotation pipeline."""

    def test_basic_annotation(
        self,
        sample_with_variants: sa.Engine,
        vep_engine_inmemory: sa.Engine,
    ) -> None:
        result = annotate_sample_vep(sample_with_variants, vep_engine_inmemory)
        assert result.total_variants == len(SEED_RAW_VARIANTS)
        assert result.total_matched > 0
        assert result.rows_written == result.total_matched

    def test_annotated_variants_populated(
        self,
        sample_with_variants: sa.Engine,
        vep_engine_inmemory: sa.Engine,
    ) -> None:
        annotate_sample_vep(sample_with_variants, vep_engine_inmemory)
        with sample_with_variants.connect() as conn:
            rows = conn.execute(sa.select(annotated_variants)).fetchall()
        assert len(rows) > 0

    def test_vep_columns_correct(
        self,
        sample_with_variants: sa.Engine,
        vep_engine_inmemory: sa.Engine,
    ) -> None:
        """Verify VEP columns are written correctly for a known variant."""
        annotate_sample_vep(sample_with_variants, vep_engine_inmemory)
        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs1801133")
            ).fetchone()
        assert row is not None
        assert row.gene_symbol == "MTHFR"
        assert row.transcript_id == "ENST00000376592"
        assert row.consequence == "missense_variant"
        assert row.hgvs_coding == "c.665C>T"
        assert row.hgvs_protein == "p.Ala222Val"
        assert row.mane_select is True or row.mane_select == 1

    def test_annotation_coverage_bitmask(
        self,
        sample_with_variants: sa.Engine,
        vep_engine_inmemory: sa.Engine,
    ) -> None:
        """VEP annotation sets bit 0 in annotation_coverage."""
        annotate_sample_vep(sample_with_variants, vep_engine_inmemory)
        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs429358"
                )
            ).fetchone()
        assert row is not None
        assert row.annotation_coverage & VEP_BITMASK == VEP_BITMASK

    def test_bitmask_or_preserves_existing_bits(
        self,
        sample_with_variants: sa.Engine,
        vep_engine_inmemory: sa.Engine,
    ) -> None:
        """Running VEP after ClinVar preserves ClinVar's bit."""
        clinvar_bitmask = 0b000010  # bit 1
        # Pre-insert a row with ClinVar bit set
        with sample_with_variants.begin() as conn:
            conn.execute(
                annotated_variants.insert().values(
                    rsid="rs429358",
                    chrom="19",
                    pos=44908684,
                    genotype="TC",
                    annotation_coverage=clinvar_bitmask,
                )
            )

        annotate_sample_vep(sample_with_variants, vep_engine_inmemory)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs429358"
                )
            ).fetchone()
        assert row is not None
        # Both ClinVar (bit 1) and VEP (bit 0) should be set
        assert row.annotation_coverage & VEP_BITMASK == VEP_BITMASK
        assert row.annotation_coverage & clinvar_bitmask == clinvar_bitmask

    def test_empty_sample(
        self,
        sample_engine: sa.Engine,
        vep_engine_inmemory: sa.Engine,
    ) -> None:
        """Empty sample returns zeros with no errors."""
        result = annotate_sample_vep(sample_engine, vep_engine_inmemory)
        assert result.total_variants == 0
        assert result.total_matched == 0
        assert result.rows_written == 0

    def test_no_vep_data(self, sample_with_variants: sa.Engine) -> None:
        """Sample with variants but empty VEP bundle yields no matches."""
        empty_vep = sa.create_engine("sqlite://")
        with empty_vep.begin() as conn:
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
        result = annotate_sample_vep(sample_with_variants, empty_vep)
        assert result.total_variants == len(SEED_RAW_VARIANTS)
        assert result.total_matched == 0
        assert result.not_matched == len(SEED_RAW_VARIANTS)

    def test_chrom_pos_fallback(
        self, sample_engine: sa.Engine, vep_engine_inmemory: sa.Engine
    ) -> None:
        """Variants with non-matching rsids fall back to chrom/pos."""
        # Insert variant with i-prefixed rsid at a known VEP position
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert().values(
                    rsid="i12345_custom",
                    chrom="19",
                    pos=44908684,  # Same position as rs429358 (APOE)
                    genotype="TC",
                )
            )

        result = annotate_sample_vep(sample_engine, vep_engine_inmemory)
        assert result.matched_by_position >= 1

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "i12345_custom")
            ).fetchone()
        assert row is not None
        assert row.gene_symbol == "APOE"

    def test_re_annotation_updates_existing(
        self,
        sample_with_variants: sa.Engine,
        vep_engine_inmemory: sa.Engine,
    ) -> None:
        """Running annotation twice updates existing rows without error."""
        result1 = annotate_sample_vep(sample_with_variants, vep_engine_inmemory)
        result2 = annotate_sample_vep(sample_with_variants, vep_engine_inmemory)
        assert result1.rows_written == result2.rows_written

        # Verify no duplicate rows
        with sample_with_variants.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(annotated_variants)
            ).scalar()
        assert count == result2.rows_written

    def test_genotype_preserved(
        self,
        sample_with_variants: sa.Engine,
        vep_engine_inmemory: sa.Engine,
    ) -> None:
        """Genotype from raw_variants is carried into annotated_variants."""
        annotate_sample_vep(sample_with_variants, vep_engine_inmemory)
        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs429358")
            ).fetchone()
        assert row is not None
        assert row.genotype == "TC"


# ═══════════════════════════════════════════════════════════════════════
# VEPAnnotationResult
# ═══════════════════════════════════════════════════════════════════════


class TestAnnotationResult:
    def test_total_matched_property(self) -> None:
        r = VEPAnnotationResult(matched_by_rsid=10, matched_by_position=5)
        assert r.total_matched == 15

    def test_defaults(self) -> None:
        r = VEPAnnotationResult()
        assert r.total_variants == 0
        assert r.total_matched == 0
        assert r.rows_written == 0


# ═══════════════════════════════════════════════════════════════════════
# Performance baseline (T2-06)
# ═══════════════════════════════════════════════════════════════════════


class TestPerformance:
    @pytest.mark.slow
    def test_1000_variant_lookup_under_5_seconds(self, vep_engine_inmemory: sa.Engine) -> None:
        """T2-06: 1000 variants looked up in < 5 seconds."""
        # Generate 1000 rsids — most won't match, but the lookup
        # should still complete quickly.
        rsids = [f"rs{i}" for i in range(1000)]
        # Add a few known ones to verify correctness
        rsids[:5] = [
            "rs429358",
            "rs7412",
            "rs1801133",
            "rs4680",
            "rs80357906",
        ]

        start = time.monotonic()
        result = lookup_vep_by_rsids(rsids, vep_engine_inmemory)
        elapsed = time.monotonic() - start

        assert elapsed < 5.0, f"Lookup took {elapsed:.2f}s (> 5s limit)"
        assert len(result) >= 5  # At least the 5 known rsids matched


# ═══════════════════════════════════════════════════════════════════════
# Mini VEP fixture DB tests (uses on-disk fixture)
# ═══════════════════════════════════════════════════════════════════════


class TestMiniVEPFixtureDB:
    """Integration tests against the on-disk mini_vep_bundle.db fixture."""

    def test_fixture_db_has_data(self, vep_engine: sa.Engine) -> None:
        with vep_engine.connect() as conn:
            count = conn.execute(sa.text("SELECT count(*) FROM vep_annotations")).scalar()
        assert count > 0

    def test_lookup_from_fixture_db(self, vep_engine: sa.Engine) -> None:
        result = lookup_vep_by_rsids(["rs429358"], vep_engine)
        assert "rs429358" in result
        assert result["rs429358"].gene_symbol == "APOE"
