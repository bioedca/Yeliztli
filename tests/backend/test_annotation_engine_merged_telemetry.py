"""Coverage telemetry shape for merged samples (Step 84 / MRG-09b; Plan §5.6, §15.1).

Locks the AncestryDNA Integration Plan §15.1 (MRG-09b row) contract for
the ``annotation_bundle_coverage_json.by_source`` payload:

  > Telemetry shape test: ``annotation_bundle_coverage.by_source`` is
  > single-key for unmerged samples (vendor name lowercase), three-key
  > (``"S1"`` / ``"S2"`` / ``"both"``, uppercase) for merged samples;
  > counts sum to ``total_variants``; uppercase keys match
  > ``raw_variants.source`` values + ``merge_provenance.concordance_summary``
  > ``unique_S1`` / ``unique_S2`` suffix tokens.

Cross-references:

* Plan §5.6 — top-level rollup equals the sum across ``by_source`` keys;
  per-source value shape is ``{vep_bundle_rsid_hits, vep_bundle_coord_fallback_hits,
  vep_misses}``.
* Plan §10.4(b) — ``raw_variants.source`` is the canonical enum
  ``S1`` / ``S2`` / ``both`` on merged samples (empty-string default on
  unmerged samples).
* Plan §10.5 step 5 — merged samples carry ``file_format='merged_v1'``;
  the coverage-stats branch keys off that.

The fixture wires a synthetic merged sample (per-sample DB created with
``is_merged_sample=True`` so ``raw_variants`` carries the ``(chrom, pos)`` PK
from Plan §10.4a) plus the matching ``merge_provenance`` row so the
suffix-token parity assertion is concrete rather than incidental.

Negative regression: a sibling test re-asserts the unmerged-sample shape
contract (single-key vendor) so the merged-sample branch can't silently
take over the unmerged code path.
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from backend.annotation.engine import run_annotation
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    database_versions,
    merge_provenance,
    raw_variants,
    reference_metadata,
    sample_metadata_table,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
VEP_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "vep_seed.csv"

# Canonical ``raw_variants.source`` enum on merged samples (Plan §10.4b).
_MERGED_SOURCE_KEYS: tuple[str, ...] = ("S1", "S2", "both")

# Per-source bucket value-keys (Plan §5.6).
_PER_SOURCE_KEYS = frozenset(
    {"vep_bundle_rsid_hits", "vep_bundle_coord_fallback_hits", "vep_misses"}
)


# ── Fixture rows ──────────────────────────────────────────────────────────
#
# Each row is annotated with its ``source`` value (Plan §10.4b) and an
# expected VEP-bundle outcome. ``rs_nomatch`` is intentionally bucketed as
# ``vep_misses`` so the merged-sample branch can demonstrate non-zero
# misses for the ``both`` slot (matching the no-call ``concordance='match'``
# row the merge service emits when both sides agree but the rsid happens
# not to be in the bundle).
#
# Distribution chosen so every bucket has at least one row and the
# concordance summary's ``unique_S1`` / ``unique_S2`` counters are non-zero,
# which makes the suffix-token parity assertion below meaningful.

_MERGED_RAW_ROWS: tuple[dict, ...] = (
    # Two S1-only rows that hit the VEP bundle by rsid
    {
        "rsid": "rs429358",
        "chrom": "19",
        "pos": 44908684,
        "genotype": "TC",
        "source": "S1",
        "concordance": "unique",
        "discordant_alt_genotype": "",
        "alt_rsid": "",
    },
    {
        "rsid": "rs4680",
        "chrom": "22",
        "pos": 19951271,
        "genotype": "AG",
        "source": "S1",
        "concordance": "unique",
        "discordant_alt_genotype": "",
        "alt_rsid": "",
    },
    # Two S2-only rows that hit the VEP bundle by rsid
    {
        "rsid": "rs7412",
        "chrom": "19",
        "pos": 44908822,
        "genotype": "CC",
        "source": "S2",
        "concordance": "unique",
        "discordant_alt_genotype": "",
        "alt_rsid": "",
    },
    {
        "rsid": "rs7903146",
        "chrom": "10",
        "pos": 114758349,
        "genotype": "CT",
        "source": "S2",
        "concordance": "unique",
        "discordant_alt_genotype": "",
        "alt_rsid": "",
    },
    # Two `both` rows that hit the VEP bundle by rsid (one match, one
    # discordant resolved via flag_only — both keep ``source='both'``).
    {
        "rsid": "rs1801133",
        "chrom": "1",
        "pos": 11856378,
        "genotype": "AG",
        "source": "both",
        "concordance": "match",
        "discordant_alt_genotype": "",
        "alt_rsid": "",
    },
    {
        "rsid": "rs12913832",
        "chrom": "15",
        "pos": 28365618,
        "genotype": "??",
        "source": "both",
        "concordance": "discordant",
        "discordant_alt_genotype": "S1=GG;S2=AG",
        "alt_rsid": "",
    },
    # One `both` row that misses the VEP bundle so the ``both`` slot has a
    # non-zero ``vep_misses`` count (counters_sum assertion would otherwise
    # be satisfied trivially).
    {
        "rsid": "rs_unknown_locus",
        "chrom": "99",
        "pos": 1,
        "genotype": "AA",
        "source": "both",
        "concordance": "match",
        "discordant_alt_genotype": "",
        "alt_rsid": "",
    },
)

# Concordance summary that matches the row distribution above. Two unique-S1
# rows + two unique-S2 rows + two ``both``-match rows + one discordant + one
# unique-coordinate-no-collapse → unique_S1=2, unique_S2=2, match=3,
# discordant=1, filled_nocall=0, collapsed_rsid=0. The actual numeric
# distribution doesn't drive any telemetry assertion — only the
# *suffix tokens* (``unique_S1`` / ``unique_S2``) do — but recording a
# realistic shape keeps the fixture self-documenting.
_CONCORDANCE_SUMMARY: dict[str, int] = {
    "match": 3,
    "filled_nocall": 0,
    "discordant": 1,
    "unique_S1": 2,
    "unique_S2": 2,
    "collapsed_rsid": 0,
}


# ── In-memory engine helpers ──────────────────────────────────────────────


def _new_engine() -> sa.Engine:
    return sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _seed_vep_bundle(engine: sa.Engine) -> None:
    """Materialise the canonical seed VEP bundle in-memory."""
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


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def vep_engine() -> sa.Engine:
    engine = _new_engine()
    _seed_vep_bundle(engine)
    return engine


@pytest.fixture
def reference_engine() -> sa.Engine:
    """Reference DB with the ``vep_bundle`` version stamped.

    The stamped row makes ``coverage_stats['bundle_version']`` meaningful
    rather than ``None``.
    """
    engine = _new_engine()
    reference_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(database_versions.insert().values(db_name="vep_bundle", version="v2.0.0"))
    return engine


@pytest.fixture
def registry(vep_engine: sa.Engine, reference_engine: sa.Engine) -> MagicMock:
    """Minimal DBRegistry — VEP + reference only, gnomAD/dbNSFP absent.

    Keeps the fixture surface tight; the merged-sample telemetry contract
    is independent of which optional sources happen to be installed.
    """
    reg = MagicMock()
    reg.reference_engine = reference_engine
    type(reg).vep_engine = property(lambda self: vep_engine)

    def _unavailable(self):
        raise RuntimeError("source intentionally unavailable for MRG-09b test")

    type(reg).gnomad_engine = property(_unavailable)
    type(reg).dbnsfp_engine = property(_unavailable)
    return reg


@pytest.fixture
def merged_sample_engine() -> sa.Engine:
    """Merged-sample per-sample DB primed with the Plan §10.4 layout.

    ``is_merged_sample=True`` selects the ``(chrom, pos)`` PK on
    ``raw_variants`` (Plan §10.4a); the seeded ``sample_metadata`` row
    carries ``file_format='merged_v1'`` (Plan §10.5 step 5) so the
    coverage-stats branch picks the merged-sample shape; the seeded
    ``merge_provenance`` row anchors the suffix-token parity assertion.
    """
    engine = _new_engine()
    create_sample_tables(engine, is_merged_sample=True)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            sample_metadata_table.insert().values(
                id=1,
                name="merged-fixture",
                file_format="merged_v1",
                file_hash="merged-fixture-hash",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            merge_provenance.insert().values(
                id=1,
                merged_at=now,
                strategy="flag_only",
                source_sample_ids=json.dumps([1, 2]),
                source_file_hashes=json.dumps(["s1-hash", "s2-hash"]),
                concordance_summary=json.dumps(_CONCORDANCE_SUMMARY),
            )
        )
        conn.execute(raw_variants.insert(), list(_MERGED_RAW_ROWS))
    return engine


@pytest.fixture
def unmerged_sample_engine() -> sa.Engine:
    """Sibling unmerged sample DB for the regression branch.

    Uses the same raw rsids but strips ``source`` so every row carries the
    empty-string default. Annotates against the same VEP bundle so the
    rollup counts are comparable to the merged case row-for-row.
    """
    engine = _new_engine()
    create_sample_tables(engine, is_merged_sample=False)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            sample_metadata_table.insert().values(
                id=1,
                name="unmerged-fixture",
                file_format="23andme_v5",
                file_hash="unmerged-fixture-hash",
                created_at=now,
                updated_at=now,
            )
        )
        plain_rows = [
            {
                "rsid": r["rsid"],
                "chrom": r["chrom"],
                "pos": r["pos"],
                "genotype": r["genotype"],
            }
            for r in _MERGED_RAW_ROWS
        ]
        conn.execute(raw_variants.insert(), plain_rows)
    return engine


# ── Contract assertions ───────────────────────────────────────────────────


class TestMergedSampleTelemetryShape:
    """Plan §15.1 MRG-09b contract for the merged-sample ``by_source`` payload."""

    def test_by_source_is_three_key_uppercase(
        self,
        merged_sample_engine: sa.Engine,
        registry: MagicMock,
    ) -> None:
        """``by_source`` keys are exactly ``{"S1", "S2", "both"}`` (uppercase).

        Per Plan §5.6, every slot is emitted on every merged sample — even
        ones whose bucket count happens to be zero — so downstream
        consumers can read a stable shape.
        """
        result = run_annotation(merged_sample_engine, registry)
        stats = result.coverage_stats

        assert set(stats["by_source"].keys()) == set(_MERGED_SOURCE_KEYS)
        # Each slot is the per-source value dict from Plan §5.6.
        for source_key in _MERGED_SOURCE_KEYS:
            per_source = stats["by_source"][source_key]
            assert set(per_source.keys()) == _PER_SOURCE_KEYS

    def test_by_source_counts_sum_to_total_variants(
        self,
        merged_sample_engine: sa.Engine,
        registry: MagicMock,
    ) -> None:
        """Per-source bucket counts sum to ``total_variants`` across all sources.

        Plan §5.6: "the top-level rollup is the sum across all ``by_source``
        keys". The per-source bucket counts therefore must each sum to the
        same total so the rollup itself can equal ``total_variants``.
        """
        result = run_annotation(merged_sample_engine, registry)
        stats = result.coverage_stats

        total_rows_across_sources = 0
        for source_key in _MERGED_SOURCE_KEYS:
            per_source = stats["by_source"][source_key]
            total_rows_across_sources += (
                per_source["vep_bundle_rsid_hits"]
                + per_source["vep_bundle_coord_fallback_hits"]
                + per_source["vep_misses"]
            )
        assert total_rows_across_sources == stats["total_variants"]
        assert stats["total_variants"] == len(_MERGED_RAW_ROWS)

    def test_top_level_rollup_equals_sum_across_by_source(
        self,
        merged_sample_engine: sa.Engine,
        registry: MagicMock,
    ) -> None:
        """Top-level rollup equals the sum across by_source per Plan §5.6."""
        result = run_annotation(merged_sample_engine, registry)
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

    def test_by_source_keys_match_raw_variants_source_values(
        self,
        merged_sample_engine: sa.Engine,
        registry: MagicMock,
    ) -> None:
        """``by_source`` keys are exactly the distinct ``raw_variants.source`` values.

        Anchors the Plan §10.4(b) enum to the §5.6 telemetry payload so a
        future refactor that introduced a new source token (or renamed an
        existing one) would surface here as an immediate mismatch rather
        than as a silent telemetry drift.
        """
        with merged_sample_engine.connect() as conn:
            distinct_sources = {
                row.source
                for row in conn.execute(sa.select(raw_variants.c.source.distinct())).fetchall()
            }

        result = run_annotation(merged_sample_engine, registry)
        stats = result.coverage_stats

        assert distinct_sources == set(_MERGED_SOURCE_KEYS)
        assert set(stats["by_source"].keys()) == distinct_sources

    def test_by_source_keys_match_concordance_summary_suffix_tokens(
        self,
        merged_sample_engine: sa.Engine,
        registry: MagicMock,
    ) -> None:
        """Uppercase keys match ``concordance_summary.unique_S1`` / ``unique_S2`` suffix tokens.

        Plan §15.1 MRG-09b: the uppercase ``S1`` / ``S2`` keys in
        ``by_source`` must be the same canonical tokens the merge service
        writes as the suffix on the ``unique_S1`` / ``unique_S2`` rows of
        ``merge_provenance.concordance_summary``. The two surfaces share a
        single enum (Plan §10.4b) — assertions both ways guard against
        either side drifting independently.
        """
        with merged_sample_engine.connect() as conn:
            row = conn.execute(sa.select(merge_provenance.c.concordance_summary)).fetchone()
        assert row is not None
        summary = json.loads(row.concordance_summary)

        suffix_tokens = {key.split("_", 1)[1] for key in summary if key.startswith("unique_")}
        assert suffix_tokens == {"S1", "S2"}

        result = run_annotation(merged_sample_engine, registry)
        stats = result.coverage_stats

        # Suffix tokens are a strict subset of the by_source keys (S1/S2
        # appear in both; ``both`` appears only in by_source because it has
        # no analogue in the unique_* counters).
        assert suffix_tokens.issubset(set(stats["by_source"].keys()))
        # And vice-versa: the by_source keys subsume the suffix tokens
        # plus the ``both`` slot.
        assert set(stats["by_source"].keys()) == suffix_tokens | {"both"}

    def test_per_source_bucket_attribution_matches_rows(
        self,
        merged_sample_engine: sa.Engine,
        registry: MagicMock,
    ) -> None:
        """Per-source counters bucket rows according to their VEP outcome.

        Every fixture row except ``rs_unknown_locus`` hits the seeded VEP
        bundle by rsid; ``rs_unknown_locus`` (``source='both'``) misses both
        the rsid and coord-fallback paths. Asserts the bucket counts
        deposit exactly where the fixture predicts so a future change to
        the engine's ``_bump_source`` placement (or to the snapshot of
        rsid-hit keys before the coord-fallback) is caught here, not in a
        downstream surface that consumes the rollup totals.
        """
        result = run_annotation(merged_sample_engine, registry)
        by_source = result.coverage_stats["by_source"]

        # S1: two rsid hits, no fallback, no misses.
        assert by_source["S1"] == {
            "vep_bundle_rsid_hits": 2,
            "vep_bundle_coord_fallback_hits": 0,
            "vep_misses": 0,
        }
        # S2: two rsid hits, no fallback, no misses.
        assert by_source["S2"] == {
            "vep_bundle_rsid_hits": 2,
            "vep_bundle_coord_fallback_hits": 0,
            "vep_misses": 0,
        }
        # both: two rsid hits, no fallback, one miss (rs_unknown_locus).
        assert by_source["both"] == {
            "vep_bundle_rsid_hits": 2,
            "vep_bundle_coord_fallback_hits": 0,
            "vep_misses": 1,
        }

    def test_bundle_version_recorded(
        self,
        merged_sample_engine: sa.Engine,
        registry: MagicMock,
    ) -> None:
        """Merged-sample payload still records the stamped bundle version.

        The shape branch (merged vs. unmerged) only swaps ``by_source``;
        the top-level keys, including ``bundle_version``, are unchanged.
        """
        result = run_annotation(merged_sample_engine, registry)
        assert result.coverage_stats["bundle_version"] == "v2.0.0"


class TestUnmergedSampleShapeRegression:
    """Negative branch: unmerged samples still emit the single-key vendor shape.

    The merged-sample dispatch reads ``sample_metadata.file_format`` and
    branches on the ``"merged_v1"`` token (Plan §10.5 step 5). A future
    refactor that conflated the two paths would silently change the
    payload shape on every existing single-vendor sample; this test
    re-asserts the existing contract so that regression surfaces here.
    """

    def test_unmerged_emits_single_key_vendor(
        self,
        unmerged_sample_engine: sa.Engine,
        registry: MagicMock,
    ) -> None:
        result = run_annotation(unmerged_sample_engine, registry)
        stats = result.coverage_stats

        assert list(stats["by_source"].keys()) == ["23andme"]
        per_source = stats["by_source"]["23andme"]
        assert set(per_source.keys()) == _PER_SOURCE_KEYS
        # Rollup parity (the merged-sample rollup-equality test above
        # exercises the merged branch; this one locks the unmerged branch).
        assert per_source["vep_bundle_rsid_hits"] == stats["vep_bundle_rsid_hits"]
        assert (
            per_source["vep_bundle_coord_fallback_hits"] == stats["vep_bundle_coord_fallback_hits"]
        )
        assert per_source["vep_misses"] == stats["vep_misses"]
