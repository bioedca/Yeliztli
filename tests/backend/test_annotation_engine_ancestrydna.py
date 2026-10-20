"""PR-blocking annotation pipeline regression test (ADNA-09, Step 40).

Runs the full annotation engine against an AncestryDNA-sourced sample DB and
locks the three contracts from
:doc:`docs/AncestryDNA_Integration_Plan.md` §13.1:

1. **Hit-rate metric.** With the regenerated mini VEP bundle covering every
   rsID in ``tests/fixtures/sample_ancestrydna_v2.txt`` except the defensive
   ``kgp*`` rows, ``rsid_hits / total_variants >= (1 - kgp_count/total_variants)``.
   The ``kgp*`` rows resolve via the (chrom, pos) coordinate-fallback path
   (Plan §5.1); ``vep_bundle_coord_fallback_hits >= kgp_count`` and together
   ``(rsid_hits + coord_fallback_hits) / total_variants == 1.0``.
2. **annotation_coverage bitmask propagation.** Every annotated row carries
   ``VEP_BIT``; the count of rows with the bit set equals ``rows_written``.
3. **Telemetry shape (Plan §5.6).** ``coverage_stats["bundle_version"] ==
   "v2.0.0"`` and ``by_source`` is single-key ``"ancestrydna"`` (lowercase)
   with per-source counts summing to ``total_variants``.

The test loads the AncestryDNA fixture via the production dispatcher, writes
the variants into an in-memory per-sample DB, builds an in-memory VEP bundle
from ``tests/fixtures/seed_csvs/vep_seed.csv`` (the same source the
regenerated mini bundle is built from) augmented with two bundle rows at the
kgp* coordinates so the coord-fallback path has something to match.
"""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from backend.annotation.engine import VEP_BIT, run_annotation
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    database_versions,
    raw_variants,
    reference_metadata,
    sample_metadata_table,
)
from backend.ingestion.base import SourceVendor
from backend.ingestion.dispatcher import parse as parse_raw

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
ANCESTRYDNA_FIXTURE = FIXTURES_DIR / "sample_ancestrydna_v2.txt"
VEP_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "vep_seed.csv"

# Coordinates of the two defensive `kgp*` rows in the fixture. Step 39 keeps
# these rsIDs out of the mini bundle so the engine's coord-fallback path runs;
# the test seeds the in-memory bundle at the matching (chrom, pos) with
# non-kgp rsIDs so the fallback resolves them.
_KGP_COORDS: dict[str, tuple[str, int]] = {
    "kgp12345678": ("1", 2000000),
    "kgp98765432": ("2", 3000000),
}


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def parsed_ancestrydna():
    """Parse the AncestryDNA fixture once per module via the dispatcher."""
    result = parse_raw(ANCESTRYDNA_FIXTURE)
    assert result.vendor is SourceVendor.ANCESTRYDNA
    assert result.version == "v2.0"
    assert result.variants, "fixture parsed zero variants — check fixture"
    return result


@pytest.fixture
def ancestrydna_sample_engine(parsed_ancestrydna) -> sa.Engine:
    """In-memory per-sample DB primed with parsed AncestryDNA variants."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_sample_tables(engine)
    file_format = f"{parsed_ancestrydna.vendor.value}_{parsed_ancestrydna.version}"
    rows = [
        {"rsid": v.rsid, "chrom": v.chrom, "pos": v.pos, "genotype": v.genotype}
        for v in parsed_ancestrydna.variants
    ]
    with engine.begin() as conn:
        conn.execute(
            sample_metadata_table.insert().values(
                id=1,
                name="ancestrydna-fixture-sample",
                file_format=file_format,
            )
        )
        conn.execute(raw_variants.insert(), rows)
    return engine


def _insert_vep_row(
    conn: sa.Connection,
    *,
    rsid: str,
    chrom: str,
    pos: int,
    gene_symbol: str = "FIXTURE_GENE",
    consequence: str = "intron_variant",
) -> None:
    """Minimal helper for the two kgp coord-fallback bundle rows."""
    conn.execute(
        sa.text(
            "INSERT INTO vep_annotations "
            "(rsid, chrom, pos, ref, alt, gene_symbol, transcript_id, "
            "consequence, hgvs_coding, hgvs_protein, strand, exon_number, "
            "intron_number, mane_select) "
            "VALUES (:rsid, :chrom, :pos, 'A', 'G', :gene, "
            "'ENST00000000000', :cons, NULL, NULL, '+', NULL, NULL, 1)"
        ),
        {
            "rsid": rsid,
            "chrom": chrom,
            "pos": pos,
            "gene": gene_symbol,
            "cons": consequence,
        },
    )


@pytest.fixture
def vep_engine_ancestrydna() -> sa.Engine:
    """In-memory VEP bundle: seed CSV rows + two kgp coord-fallback rows."""
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
        conn.execute(sa.text("CREATE INDEX idx_vep_chrom_pos ON vep_annotations(chrom, pos)"))

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
                        "gene_symbol": row["gene_symbol"] or None,
                        "transcript_id": row["transcript_id"] or None,
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

        # Coord-fallback rows for the two defensive kgp* fixture entries.
        # These rsIDs are NOT kgp* — the bundle stores production-style rsIDs
        # at the kgp coordinates; the engine matches them via (chrom, pos).
        for i, (chrom, pos) in enumerate(_KGP_COORDS.values()):
            _insert_vep_row(
                conn,
                rsid=f"rs_coord_fallback_{i}",
                chrom=chrom,
                pos=pos,
                gene_symbol=f"COORD_GENE_{i}",
            )
    return engine


@pytest.fixture
def reference_engine_with_bundle_version() -> sa.Engine:
    """In-memory reference DB with `vep_bundle` v2.0.0 stamped."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    reference_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(database_versions.insert().values(db_name="vep_bundle", version="v2.0.0"))
    return engine


@pytest.fixture
def registry(
    vep_engine_ancestrydna: sa.Engine,
    reference_engine_with_bundle_version: sa.Engine,
) -> MagicMock:
    """Mock DBRegistry exposing VEP + reference engines; gnomAD/dbNSFP absent.

    The hit-rate contract only depends on the VEP bundle. Leaving the other
    sources unavailable mirrors a fresh install where the optional databases
    have not yet been downloaded — the engine should still complete and
    report VEP coverage cleanly.
    """
    reg = MagicMock()
    reg.reference_engine = reference_engine_with_bundle_version
    type(reg).vep_engine = property(lambda self: vep_engine_ancestrydna)

    def _unavailable(self):
        raise RuntimeError("source unavailable for ADNA-09 regression test")

    type(reg).gnomad_engine = property(_unavailable)
    type(reg).dbnsfp_engine = property(_unavailable)
    return reg


# ── Helpers ───────────────────────────────────────────────────────────────


def _kgp_count(parsed) -> int:
    return sum(1 for v in parsed.variants if v.rsid.startswith("kgp"))


# ── ADNA-09 contract assertions ───────────────────────────────────────────


class TestAncestryDNAAnnotationPipeline:
    """Plan §13.1 ADNA-09: PR-blocking AncestryDNA annotation regression."""

    def test_hit_rate_and_coord_fallback(
        self,
        parsed_ancestrydna,
        ancestrydna_sample_engine: sa.Engine,
        registry: MagicMock,
    ) -> None:
        """Plan §5.6 hit-rate + coord-fallback contract."""
        result = run_annotation(ancestrydna_sample_engine, registry)
        stats = result.coverage_stats

        total = stats["total_variants"]
        rsid_hits = stats["vep_bundle_rsid_hits"]
        coord_hits = stats["vep_bundle_coord_fallback_hits"]
        kgp_count = _kgp_count(parsed_ancestrydna)

        assert total == len(parsed_ancestrydna.variants)
        assert kgp_count > 0, "fixture lost its defensive kgp* rows"

        hit_rate = rsid_hits / total
        floor = 1.0 - (kgp_count / total)
        assert hit_rate >= floor, (
            f"rsid hit-rate {hit_rate:.4f} < floor {floor:.4f} "
            f"({rsid_hits}/{total} with {kgp_count} kgp rows expected to miss)"
        )

        assert coord_hits >= kgp_count, (
            f"coord-fallback hits ({coord_hits}) did not cover the {kgp_count} defensive kgp* rows"
        )

        # Combined rsid + coord coverage hits every variant in the fixture.
        assert rsid_hits + coord_hits == total
        assert stats["vep_misses"] == 0

    def test_annotation_coverage_bitmask_propagation(
        self,
        ancestrydna_sample_engine: sa.Engine,
        registry: MagicMock,
    ) -> None:
        """Every annotated row sets VEP_BIT; the bit count equals rows_written."""
        result = run_annotation(ancestrydna_sample_engine, registry)
        assert result.rows_written > 0

        with ancestrydna_sample_engine.connect() as conn:
            coverage_rows = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage)
            ).fetchall()

        assert len(coverage_rows) == result.rows_written
        vep_bit_count = sum(1 for row in coverage_rows if (row.annotation_coverage or 0) & VEP_BIT)
        assert vep_bit_count == result.rows_written, (
            "every annotated row must carry the VEP bit when only VEP "
            "is available, but "
            f"{result.rows_written - vep_bit_count} rows were missing it"
        )

    def test_telemetry_shape(
        self,
        parsed_ancestrydna,
        ancestrydna_sample_engine: sa.Engine,
        registry: MagicMock,
    ) -> None:
        """Plan §5.6: bundle version + single-key by_source for unmerged AncestryDNA."""
        result = run_annotation(ancestrydna_sample_engine, registry)
        stats = result.coverage_stats

        assert stats["bundle_version"] == "v2.0.0"
        assert list(stats["by_source"].keys()) == ["ancestrydna"]

        per_source = stats["by_source"]["ancestrydna"]
        assert set(per_source.keys()) == {
            "vep_bundle_rsid_hits",
            "vep_bundle_coord_fallback_hits",
            "vep_bundle_allele_ambiguous",
            "vep_misses",
        }
        per_source_sum = (
            per_source["vep_bundle_rsid_hits"]
            + per_source["vep_bundle_coord_fallback_hits"]
            + per_source["vep_bundle_allele_ambiguous"]
            + per_source["vep_misses"]
        )
        assert per_source_sum == stats["total_variants"]
        # Single-key payload mirrors the top-level rollup.
        assert per_source["vep_bundle_rsid_hits"] == stats["vep_bundle_rsid_hits"]
        assert (
            per_source["vep_bundle_coord_fallback_hits"] == stats["vep_bundle_coord_fallback_hits"]
        )
        assert per_source["vep_misses"] == stats["vep_misses"]
