"""Annotation-engine cross-PK invariance (MRG-08c, Step 78; Plan §10.4(a), §15.1).

Locks the contract from the AncestryDNA Integration Plan §10.4(a) closing
paragraph and §15.1 (MRG-08c row):

  > The annotation engine treats the merged sample exactly like any other —
  > no special branches. […] The PK divergence is invisible to annotation
  > because the engine reads ``raw_variants`` by SELECT, not by PK lookup.

The merged-sample ``raw_variants`` PK is ``(chrom, pos)`` (created by
``create_sample_tables(engine, is_merged_sample=True)`` per Step 64); every
other sample DB keeps the historical ``rsid`` PK. Both shapes must feed
``run_annotation`` to byte-identical ``annotated_variants`` content given
the same raw rows and the same annotation sources — otherwise downstream
analysis modules (cancer, cardiovascular, carrier_status, rare-variant
finder, …) that read ``annotated_variants`` could see different findings on
a merged sample purely because of the on-disk PK swap, which would be a
silent data-integrity regression (Risk Register R-12).

The test wires two sibling sample DBs with the same raw rows inserted in
the same order, runs the production ``run_annotation`` against each with
the same in-memory VEP bundle + ClinVar-seeded reference DB (multiple
sources so the ``annotation_coverage`` bitmask actually varies across
rows), and asserts:

  * Same ``rows_written`` / per-source ``*_matched`` counts on the
    :class:`AnnotationEngineResult`.
  * Same ``coverage_stats`` payload (Plan §5.6) — including the derived
    ``by_source`` vendor key — because both samples carry the same
    ``file_format`` in ``sample_metadata``.
  * ``annotated_variants`` is row-for-row, column-for-column identical
    when read back ordered by rsid.

The PK shape itself is sanity-asserted up front so the test fails loudly
if a future refactor accidentally collapses the divergence (which would
make the byte-identical assertion trivially pass).
"""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from backend.annotation.engine import (
    CLINVAR_BIT,
    VEP_BIT,
    AnnotationEngineResult,
    run_annotation,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    clinvar_variants,
    database_versions,
    raw_variants,
    reference_metadata,
    sample_metadata_table,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
VEP_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "vep_seed.csv"

# Raw rows chosen so that:
#   * Every rsid is present in vep_seed.csv → VEP_BIT on every annotated row.
#   * Two of them (rs429358, rs1801133) also hit the seeded ClinVar rows below,
#     so the bitmask actually varies (VEP_BIT vs. VEP_BIT | CLINVAR_BIT) and
#     the cross-PK comparison exercises the merge path, not just a single bit.
#   * The order is intentionally NOT sorted by rsid — it forces the on-disk
#     row layout to diverge between the rsid-PK and (chrom, pos)-PK shapes
#     under the engine's unordered ``sa.select(...)`` scan.
_RAW_VARIANTS: tuple[dict, ...] = (
    {"rsid": "rs429358", "chrom": "19", "pos": 44908684, "genotype": "TC"},
    {"rsid": "rs7412", "chrom": "19", "pos": 44908822, "genotype": "CC"},
    {"rsid": "rs1801133", "chrom": "1", "pos": 11856378, "genotype": "AG"},
    {"rsid": "rs4680", "chrom": "22", "pos": 19951271, "genotype": "AG"},
    {"rsid": "rs12913832", "chrom": "15", "pos": 28365618, "genotype": "GG"},
    {"rsid": "rs7903146", "chrom": "10", "pos": 114758349, "genotype": "CT"},
    {"rsid": "rs1805007", "chrom": "16", "pos": 89986117, "genotype": "CC"},
)

_SEED_CLINVAR = (
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
)

# Same value for both samples so the Plan §5.6 ``by_source`` vendor key
# derived from ``sample_metadata.file_format`` matches across DBs and the
# ``coverage_stats`` equality assertion isn't trivially defeated by metadata.
_FILE_FORMAT = "23andme_v5"


# ── Helpers ───────────────────────────────────────────────────────────────


def _new_engine() -> sa.Engine:
    """In-memory SQLite engine wired for the engine's ThreadPoolExecutor."""
    return sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _seed_sample(engine: sa.Engine) -> None:
    """Insert sample_metadata + raw_variants rows in the canonical order."""
    with engine.begin() as conn:
        conn.execute(
            sample_metadata_table.insert().values(
                id=1,
                name="cross-pk-fixture",
                file_format=_FILE_FORMAT,
            )
        )
        # SQLAlchemy Core stripping fixed dicts to a list keeps the order
        # explicit — the engine reads via unordered ``sa.select`` so any
        # divergence in row-iteration order between the two PK shapes is
        # exposed by the read-back below.
        conn.execute(raw_variants.insert(), list(_RAW_VARIANTS))


def _read_annotated_rows(engine: sa.Engine) -> list[dict]:
    """Return every ``annotated_variants`` row as a dict ordered by rsid.

    Ordering is deterministic on read so the comparison is on row content,
    not on physical layout — the byte-identical contract from MRG-08c is
    about *what's stored*, not *where it lives on disk*.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(annotated_variants).order_by(annotated_variants.c.rsid)
        ).fetchall()
    return [dict(row._mapping) for row in rows]


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def unmerged_engine() -> sa.Engine:
    """Default per-sample DB: ``raw_variants`` PK on ``rsid`` (Plan §10.4a)."""
    engine = _new_engine()
    create_sample_tables(engine, is_merged_sample=False)
    _seed_sample(engine)
    return engine


@pytest.fixture
def merged_engine() -> sa.Engine:
    """Merged-sample per-sample DB: ``raw_variants`` PK on ``(chrom, pos)``."""
    engine = _new_engine()
    create_sample_tables(engine, is_merged_sample=True)
    _seed_sample(engine)
    return engine


@pytest.fixture
def vep_engine() -> sa.Engine:
    """In-memory VEP bundle materialised from the canonical seed CSV."""
    engine = _new_engine()
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
            for row in csv.DictReader(f):
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
                        "exon_number": int(row["exon_number"]) if row["exon_number"] else None,
                        "intron_number": (
                            int(row["intron_number"]) if row["intron_number"] else None
                        ),
                        "mane_select": int(row["mane_select"]),
                    },
                )
    return engine


@pytest.fixture
def reference_engine() -> sa.Engine:
    """Reference DB with the seeded ClinVar rows + ``vep_bundle`` stamped.

    The bundle-version row makes the coverage telemetry equality assertion
    meaningful — without it, ``coverage_stats["bundle_version"]`` would be
    ``None`` on both runs and the equality would be vacuous.
    """
    engine = _new_engine()
    reference_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(clinvar_variants.insert(), list(_SEED_CLINVAR))
        conn.execute(database_versions.insert().values(db_name="vep_bundle", version="v2.0.0"))
    return engine


@pytest.fixture
def registry(vep_engine: sa.Engine, reference_engine: sa.Engine) -> MagicMock:
    """Mock DBRegistry exposing only VEP + reference; gnomAD/dbNSFP absent.

    Two sources is enough to exercise the bitmask merge path
    (``VEP_BIT | CLINVAR_BIT`` on the two overlapping rsids vs. ``VEP_BIT``
    alone on the rest); leaving the optional DBs out mirrors a clean
    install and keeps the fixture surface minimal.
    """
    reg = MagicMock()
    reg.reference_engine = reference_engine
    type(reg).vep_engine = property(lambda self: vep_engine)

    def _unavailable(self):
        raise RuntimeError("source intentionally unavailable for MRG-08c test")

    type(reg).gnomad_engine = property(_unavailable)
    type(reg).dbnsfp_engine = property(_unavailable)
    return reg


# ── PK-shape sanity checks ────────────────────────────────────────────────


class TestPKDivergenceSanityChecks:
    """Guard rail: the byte-identical assertion is only meaningful if the
    two sample DBs really do carry different ``raw_variants`` PKs.

    A future refactor that accidentally collapses the merged-vs-unmerged
    branch in ``create_sample_tables`` would make every cross-PK test
    pass trivially; these two cases fail loudly in that scenario.
    """

    def test_unmerged_has_rsid_pk(self, unmerged_engine: sa.Engine) -> None:
        pk = sa.inspect(unmerged_engine).get_pk_constraint("raw_variants")
        assert pk["constrained_columns"] == ["rsid"]

    def test_merged_has_chrom_pos_pk(self, merged_engine: sa.Engine) -> None:
        pk = sa.inspect(merged_engine).get_pk_constraint("raw_variants")
        assert pk["constrained_columns"] == ["chrom", "pos"]


# ── MRG-08c contract: byte-identical annotation across PK shapes ──────────


class TestAnnotationCrossPKByteIdentical:
    """Plan §10.4(a) closing paragraph + §15.1 MRG-08c contract."""

    def test_annotated_variants_rows_are_byte_identical(
        self,
        merged_engine: sa.Engine,
        unmerged_engine: sa.Engine,
        registry: MagicMock,
    ) -> None:
        """``annotated_variants`` is row-for-row, column-for-column identical
        when the engine runs against both PK shapes over the same input."""
        run_annotation(unmerged_engine, registry)
        run_annotation(merged_engine, registry)

        unmerged_rows = _read_annotated_rows(unmerged_engine)
        merged_rows = _read_annotated_rows(merged_engine)

        # Pre-condition: both runs actually produced annotations — otherwise
        # equality of two empty lists would silently pass.
        assert len(unmerged_rows) == len(_RAW_VARIANTS)
        assert len(merged_rows) == len(_RAW_VARIANTS)

        # The byte-identical contract from MRG-08c.
        assert merged_rows == unmerged_rows

    def test_engine_result_counts_match(
        self,
        merged_engine: sa.Engine,
        unmerged_engine: sa.Engine,
        registry: MagicMock,
    ) -> None:
        """Per-source ``*_matched`` counts + ``rows_written`` agree across PKs.

        The engine result drives both the SSE progress payload and the
        Huey task's coverage rollup; a divergence here would surface as
        inconsistent telemetry between merged and unmerged samples even
        if the on-disk rows happened to match.
        """
        unmerged_result: AnnotationEngineResult = run_annotation(unmerged_engine, registry)
        merged_result: AnnotationEngineResult = run_annotation(merged_engine, registry)

        assert merged_result.total_variants == unmerged_result.total_variants
        assert merged_result.rows_written == unmerged_result.rows_written
        assert merged_result.vep_matched == unmerged_result.vep_matched
        assert merged_result.clinvar_matched == unmerged_result.clinvar_matched
        assert merged_result.gnomad_matched == unmerged_result.gnomad_matched
        assert merged_result.dbnsfp_matched == unmerged_result.dbnsfp_matched
        assert merged_result.gene_phenotype_matched == unmerged_result.gene_phenotype_matched
        assert merged_result.vep_coord_fallback_matched == (
            unmerged_result.vep_coord_fallback_matched
        )
        # Neither run should have hit an annotation-source error.
        assert unmerged_result.errors == []
        assert merged_result.errors == []

    def test_coverage_stats_match(
        self,
        merged_engine: sa.Engine,
        unmerged_engine: sa.Engine,
        registry: MagicMock,
    ) -> None:
        """Plan §5.6 telemetry payload is identical across PK shapes.

        Same ``file_format`` on both samples → same vendor key in
        ``by_source``; same VEP bundle + reference DB → same hit counts.
        A regression here would mean the Huey task wrote a different
        ``annotation_bundle_coverage_json`` for a merged sample purely
        because of the raw_variants PK swap.
        """
        unmerged_result = run_annotation(unmerged_engine, registry)
        merged_result = run_annotation(merged_engine, registry)

        assert merged_result.coverage_stats == unmerged_result.coverage_stats
        # Anchor the payload shape so a future refactor of
        # ``_build_coverage_stats`` doesn't silently change what we're
        # claiming "byte-identical" about.
        assert merged_result.coverage_stats["bundle_version"] == "v2.0.0"
        assert merged_result.coverage_stats["total_variants"] == len(_RAW_VARIANTS)
        assert list(merged_result.coverage_stats["by_source"].keys()) == ["23andme"]

    def test_bitmask_actually_varies(
        self,
        merged_engine: sa.Engine,
        unmerged_engine: sa.Engine,
        registry: MagicMock,
    ) -> None:
        """The byte-identical assertion would be weak if every annotated row
        carried the same bitmask — the cross-PK comparison would reduce to
        equality on a constant. This test confirms the fixture exercises
        both ``VEP_BIT`` and ``VEP_BIT | CLINVAR_BIT`` paths so the
        equality in :meth:`test_annotated_variants_rows_are_byte_identical`
        is doing real merge-path comparison work, not constant-folding.
        """
        run_annotation(unmerged_engine, registry)
        run_annotation(merged_engine, registry)

        unmerged_masks = {
            r["rsid"]: r["annotation_coverage"] for r in _read_annotated_rows(unmerged_engine)
        }
        merged_masks = {
            r["rsid"]: r["annotation_coverage"] for r in _read_annotated_rows(merged_engine)
        }

        clinvar_rsids = {row["rsid"] for row in _SEED_CLINVAR}
        for rsid in clinvar_rsids:
            assert unmerged_masks[rsid] == VEP_BIT | CLINVAR_BIT
            assert merged_masks[rsid] == VEP_BIT | CLINVAR_BIT
        vep_only_rsids = {row["rsid"] for row in _RAW_VARIANTS} - clinvar_rsids
        assert vep_only_rsids, "fixture lost its VEP-only rows"
        for rsid in vep_only_rsids:
            assert unmerged_masks[rsid] == VEP_BIT
            assert merged_masks[rsid] == VEP_BIT
