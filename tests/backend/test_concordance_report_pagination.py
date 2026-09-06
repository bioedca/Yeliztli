"""Step 74 / MRG-04a — pagination contract for
``GET /api/samples/{id}/concordance-report?limit=N&offset=K`` (Plan §15.1).

``tests/backend/test_merge_routes.py`` already exercises the route's
happy path end-to-end through ``backend.services.sample_merge`` against a
single-discordant-locus fixture. This file isolates the pagination
contract Plan §15.1 (MRG-04a) calls out by seeding a merged sample DB
directly with a controlled, oversized discordant set so each clause has
meaningful data to drive:

* default ``limit=50`` when the query string omits it,
* ``limit=500`` is the documented cap (501 → 422 with the cap value
  discoverable in the error body),
* results are ordered by ``(chrom, pos)`` ascending regardless of
  ``offset`` (rows are inserted in reverse, so the route's ORDER BY is
  the only thing that can surface the expected sequence),
* ``offset`` past ``total_discordant`` returns an empty array with the
  correct total,
* ``total_discordant`` equals the actual count of
  ``concordance='discordant'`` rows (non-discordant distractors do not
  inflate the total),
* each ``discordant_loci`` row LEFT-JOINs ``annotated_variants`` so the
  gene-context fields are populated when an annotation row exists and
  ``None`` (not omitted) when it does not.

Seeding goes through SQLAlchemy Core inserts rather than the merge
service so we can drive ≥60 discordant loci plus a mixed
annotated/un-annotated set without rebuilding hand-curated fixtures.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    annotation_state,
    database_versions,
    merge_provenance,
    raw_variants,
    reference_metadata,
    samples,
)
from backend.ingestion.chrom_order import chrom_sort_key

# Discordant rows are split across three chromosomes whose lexicographic
# (TEXT) order differs from the canonical genomic order: SQLite's text sort
# yields "10" < "2" < "X", the canonical order is 2, 10, X. That is the
# discrimination the ordering assertions need — a raw ``chrom.asc()``
# ORDER BY fails them and the canonical CASE ordering passes (issue #2053).
# 60 discordant rows > the default ``limit=50``, which gives the default-
# limit, offset-past-total, and ordering-with-offset cases all enough
# data to drive a non-trivial slice.
_CHROMS = ("2", "10", "X")
_POSITIONS_PER_CHROM = tuple(range(100, 2100, 100))  # 20 positions / chrom
_DISCORDANT_TOTAL = len(_CHROMS) * len(_POSITIONS_PER_CHROM)
assert _DISCORDANT_TOTAL == 60
# The fixture must stay discriminating: chromosomes whose text order equals
# their canonical order would let the ordering tests pass under either sort.
assert sorted(_CHROMS) != sorted(_CHROMS, key=chrom_sort_key)

# Every other discordant locus carries an ``annotated_variants`` row so
# the LEFT JOIN's populated and NULL branches both fire on a known half
# of the discordant page.
_ANNOTATED_EVERY = 2


def _build_discordant_rows() -> list[dict]:
    rows: list[dict] = []
    for chrom in _CHROMS:
        for pos in _POSITIONS_PER_CHROM:
            rows.append(
                {
                    "rsid": f"rs_disc_{chrom}_{pos}",
                    "chrom": chrom,
                    "pos": pos,
                    "genotype": "??",
                    "source": "both",
                    "concordance": "discordant",
                    "discordant_alt_genotype": "S1=AA;S2=GG",
                    "alt_rsid": "",
                }
            )
    # Reverse insertion order so the SQLite rowid sequence is the
    # opposite of the (chrom, pos)-ascending order the route promises.
    # If a future refactor drops the ORDER BY, the very first
    # ``discordant_loci[0]`` will fail the ordering assertion below.
    rows.reverse()
    return rows


def _build_non_discordant_rows() -> list[dict]:
    """Concordant + filled-nocall + unique rows that ``total_discordant``
    must exclude. Kept on a separate chrom so they cannot interleave the
    ordering assertion even if the filter regressed.
    """
    return [
        {
            "rsid": "rs_match_1",
            "chrom": "4",
            "pos": 100,
            "genotype": "AG",
            "source": "both",
            "concordance": "match",
            "discordant_alt_genotype": "",
            "alt_rsid": "",
        },
        {
            "rsid": "rs_filled_1",
            "chrom": "4",
            "pos": 200,
            "genotype": "AA",
            "source": "S1",
            "concordance": "filled_nocall",
            "discordant_alt_genotype": "",
            "alt_rsid": "",
        },
        {
            "rsid": "rs_unique_1",
            "chrom": "4",
            "pos": 300,
            "genotype": "CC",
            "source": "S2",
            "concordance": "unique",
            "discordant_alt_genotype": "",
            "alt_rsid": "",
        },
    ]


def _build_annotation_rows(discordant_rows: list[dict]) -> list[dict]:
    """Annotate every other discordant locus (sorted in the route's order)
    so the LEFT JOIN's populated branch hits a deterministic, known
    subset of the response page.
    """
    ordered = sorted(discordant_rows, key=lambda r: (r["chrom"], r["pos"]))
    annotated: list[dict] = []
    for i, row in enumerate(ordered):
        if i % _ANNOTATED_EVERY != 0:
            continue
        annotated.append(
            {
                "rsid": row["rsid"],
                "chrom": row["chrom"],
                "pos": row["pos"],
                "gene_symbol": f"GENE_{row['chrom']}_{row['pos']}",
                "consequence": "missense_variant",
                "clinvar_significance": "Likely_pathogenic",
            }
        )
    return annotated


_DISCORDANT_ROWS = _build_discordant_rows()
_NON_DISCORDANT_ROWS = _build_non_discordant_rows()
_ANNOTATION_ROWS = _build_annotation_rows(_DISCORDANT_ROWS)

_CONCORDANCE_SUMMARY = {
    "match": 1,
    "filled_nocall": 1,
    "discordant": _DISCORDANT_TOTAL,
    "unique_S1": 0,
    "unique_S2": 1,
    "collapsed_rsid": 0,
}


@pytest.fixture
def pagination_client(tmp_data_dir: Path):
    """TestClient with a single hand-seeded merged sample.

    Stashes ``merged_id`` and ``discordant_total`` on the client so tests
    can address the route directly without re-deriving them from the
    (single-row) samples table.
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    ref_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        from backend.db.connection import get_registry
        from backend.main import create_app

        registry = get_registry()
        now = datetime.now(UTC)

        # Pin the installed VEP bundle to v2.0.0 and seed the merged
        # sample's ``annotation_state`` to the same value, so
        # ``require_fresh_sample`` lets reads through.
        with registry.reference_engine.begin() as conn:
            conn.execute(
                database_versions.insert().values(
                    db_name="vep_bundle",
                    version="v2.0.0",
                    downloaded_at=now,
                )
            )
            result = conn.execute(
                samples.insert().values(
                    name="merged_pagination.txt",
                    db_path="",
                    file_format="merged_v1",
                    file_hash="hash_merged_pagination",
                    individual_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            merged_id = int(result.inserted_primary_key[0])
            db_path = f"samples/sample_{merged_id}.db"
            conn.execute(samples.update().where(samples.c.id == merged_id).values(db_path=db_path))

        sample_db_path = registry.settings.data_dir / db_path
        sample_db_path.parent.mkdir(parents=True, exist_ok=True)
        # Materialise the merged-sample DB ourselves (composite (chrom, pos)
        # PK on ``raw_variants``) BEFORE the registry sees the file —
        # ``get_sample_engine`` lazily runs ``ensure_sample_schema_current``
        # on first access, which would otherwise auto-create the regular
        # rsid-PK raw_variants and our ``create_sample_tables`` call would
        # collide with the existing table.
        engine = sa.create_engine(f"sqlite:///{sample_db_path}", future=True)
        create_sample_tables(engine, is_merged_sample=True)
        with engine.begin() as conn:
            conn.execute(raw_variants.insert(), _DISCORDANT_ROWS)
            conn.execute(raw_variants.insert(), _NON_DISCORDANT_ROWS)
            if _ANNOTATION_ROWS:
                conn.execute(annotated_variants.insert(), _ANNOTATION_ROWS)
            conn.execute(
                merge_provenance.insert().values(
                    id=1,
                    merged_at=now,
                    strategy="flag_only",
                    source_sample_ids=json.dumps([101, 202]),
                    source_file_hashes=json.dumps(["hash_s1_pagination", "hash_s2_pagination"]),
                    concordance_summary=json.dumps(_CONCORDANCE_SUMMARY),
                )
            )
            conn.execute(
                annotation_state.insert().values(
                    key="vep_bundle_version",
                    value="v2.0.0",
                    updated_at=now,
                )
            )
        engine.dispose()

        app = create_app()
        with TestClient(app) as tc:
            tc.merged_id = merged_id  # type: ignore[attr-defined]
            tc.discordant_total = _DISCORDANT_TOTAL  # type: ignore[attr-defined]
            yield tc

        reset_registry()


def _expected_ordered_keys() -> list[tuple[str, int]]:
    """The (chrom, pos) pairs the route must return, in canonical genomic order."""
    return sorted(
        ((r["chrom"], r["pos"]) for r in _DISCORDANT_ROWS),
        key=lambda k: (chrom_sort_key(k[0]), k[1]),
    )


# ── (i) default ``limit=50`` when omitted ──────────────────────────────


class TestDefaultLimit:
    def test_default_limit_is_50_when_query_param_omitted(
        self, pagination_client: TestClient
    ) -> None:
        resp = pagination_client.get(
            f"/api/samples/{pagination_client.merged_id}/concordance-report"  # type: ignore[attr-defined]
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["limit"] == 50
        assert body["offset"] == 0
        # 60 > 50 ⇒ exactly the page size is returned.
        assert len(body["discordant_loci"]) == 50


# ── (ii) cap at 500 (501 → 422 with cap stated) ────────────────────────


class TestMaxLimitCap:
    def test_limit_at_max_returns_every_available_row(self, pagination_client: TestClient) -> None:
        """``limit=500`` is the documented cap and must succeed. With 60
        discordant rows seeded the response carries all 60 in one page.
        """
        resp = pagination_client.get(
            f"/api/samples/{pagination_client.merged_id}/concordance-report?limit=500"  # type: ignore[attr-defined]
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["limit"] == 500
        assert len(body["discordant_loci"]) == pagination_client.discordant_total  # type: ignore[attr-defined]

    def test_limit_above_max_returns_422_with_cap_stated(
        self, pagination_client: TestClient
    ) -> None:
        resp = pagination_client.get(
            f"/api/samples/{pagination_client.merged_id}/concordance-report?limit=501"  # type: ignore[attr-defined]
        )
        assert resp.status_code == 422
        # FastAPI surfaces ``le=500`` in the validation-error body
        # (``ctx.le=500`` plus an "Input should be less than or equal to
        # 500" message). MRG-04a calls for the cap value to be
        # discoverable from the error; ``500`` must appear somewhere
        # in the serialized response.
        assert "500" in resp.text


# ── (iii) ordering by (chrom, pos) ascending regardless of offset ──────


class TestOrdering:
    def test_results_ordered_by_chrom_pos_at_offset_zero(
        self, pagination_client: TestClient
    ) -> None:
        resp = pagination_client.get(
            f"/api/samples/{pagination_client.merged_id}/concordance-report?limit=500"  # type: ignore[attr-defined]
        )
        assert resp.status_code == 200, resp.text
        got = [(row["chrom"], row["pos"]) for row in resp.json()["discordant_loci"]]
        assert got == _expected_ordered_keys()
        # Canonical order is not TEXT order: chr2 precedes chr10, and X comes last.
        assert got != sorted(got)
        chroms = [chrom for chrom, _ in got]
        assert chroms.index("2") < chroms.index("10") < chroms.index("X")

    def test_ordering_holds_for_a_mid_window_offset(self, pagination_client: TestClient) -> None:
        """The ORDER BY must apply BEFORE LIMIT/OFFSET, so the second
        page is a contiguous ascending slice of the canonical sequence —
        not a locally-sorted slice of an arbitrary subset.
        """
        full = pagination_client.get(
            f"/api/samples/{pagination_client.merged_id}/concordance-report?limit=500"  # type: ignore[attr-defined]
        ).json()["discordant_loci"]
        page = pagination_client.get(
            f"/api/samples/{pagination_client.merged_id}/concordance-report?limit=10&offset=20"  # type: ignore[attr-defined]
        ).json()["discordant_loci"]
        assert len(page) == 10
        expected_slice = [(row["chrom"], row["pos"]) for row in full[20:30]]
        got = [(row["chrom"], row["pos"]) for row in page]
        assert got == expected_slice


# ── (iv) offset past total → empty + correct total ─────────────────────


class TestOffsetPastTotal:
    def test_offset_past_total_returns_empty_with_correct_total(
        self, pagination_client: TestClient
    ) -> None:
        total = pagination_client.discordant_total  # type: ignore[attr-defined]
        resp = pagination_client.get(
            f"/api/samples/{pagination_client.merged_id}/concordance-report"  # type: ignore[attr-defined]
            f"?limit=50&offset={total + 50}"
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["discordant_loci"] == []
        assert body["total_discordant"] == total
        assert body["limit"] == 50
        assert body["offset"] == total + 50


# ── (v) total_discordant matches actual discordant count ───────────────


class TestTotalDiscordant:
    def test_total_excludes_non_discordant_rows(self, pagination_client: TestClient) -> None:
        """The non-discordant seed rows (``match`` / ``filled_nocall`` /
        ``unique``) must NOT be counted in ``total_discordant``. Locks
        that the route filters on ``concordance='discordant'`` rather
        than counting every ``raw_variants`` row.
        """
        resp = pagination_client.get(
            f"/api/samples/{pagination_client.merged_id}/concordance-report?limit=500"  # type: ignore[attr-defined]
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_discordant"] == pagination_client.discordant_total  # type: ignore[attr-defined]
        # Sanity: returned rows are themselves all discordant (none of
        # the distractors leaked in).
        for row in body["discordant_loci"]:
            assert row["rsid"].startswith("rs_disc_")


# ── (vi) JOIN against annotated_variants surfaces gene context ─────────


class TestAnnotatedVariantsJoin:
    def test_left_join_populates_gene_context_when_annotation_exists(
        self, pagination_client: TestClient
    ) -> None:
        """Every other discordant locus carries an ``annotated_variants``
        row. The LEFT JOIN must (a) populate gene_symbol / consequence /
        clinvar_significance for those rows and (b) return ``None`` (not
        omit the row) for the un-annotated half.
        """
        resp = pagination_client.get(
            f"/api/samples/{pagination_client.merged_id}/concordance-report?limit=500"  # type: ignore[attr-defined]
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        annotated_rsids = {row["rsid"] for row in _ANNOTATION_ROWS}

        annotated_seen = 0
        unannotated_seen = 0
        for row in body["discordant_loci"]:
            if row["rsid"] in annotated_rsids:
                assert row["gene_symbol"] == f"GENE_{row['chrom']}_{row['pos']}"
                assert row["consequence"] == "missense_variant"
                assert row["clinvar_significance"] == "Likely_pathogenic"
                annotated_seen += 1
            else:
                assert row["gene_symbol"] is None
                assert row["consequence"] is None
                assert row["clinvar_significance"] is None
                unannotated_seen += 1

        # Locks that both LEFT-JOIN branches actually fired in the same
        # page (i.e. not all rows fell accidentally into one bucket).
        assert annotated_seen == len(_ANNOTATION_ROWS)
        assert (
            unannotated_seen == pagination_client.discordant_total - annotated_seen  # type: ignore[attr-defined]
        )
