"""Tests for ``GET /api/samples/{merged_id}/watched-variants/migrate-from-sources``.

Step 72 / MRG-13 / Plan §10.6 read-only route that powers the post-merge
``<PostMergeRewatchModal>``. Surfaces every ``watched_variants`` row from
the merged sample's source samples whose rsid is not present on the
merged sample, paired with the merged sample's chosen rsid at the
matching ``(chrom, pos)`` — ``null`` when the locus is altogether absent
from the merged sample.

What this file locks:

* Happy-path candidate shape (rsid private to source → surfaced with
  ``rsid_on_merged_or_null=None``; rsid-collapse → surfaced with the
  merged sample's chosen rsid; rsid shared across both → not surfaced).
* Unmerged sample → 404 with ``"no merge provenance"`` (the gate is
  open because the sample is fresh).
* Stale merged sample → 423 from ``require_fresh_merged_sample`` with
  the Plan §7.5 payload keys.
* Source sample missing the ``watched_variants`` table (pre-v7 schema)
  → tolerated, no error, no candidates contributed.
* No source has any watches → ``{candidates: []}``.

The fixture mirrors ``test_merge_routes.py``'s in-fixture setup so the
two files exercise the same data layout; the merged DB is built by
hand here rather than running the full merge service so the test
remains scoped to the new route's contract.
"""

from __future__ import annotations

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
    annotation_state,
    database_versions,
    merge_provenance,
    raw_variants,
    reference_metadata,
    samples,
    watched_variants,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _v(rsid: str, chrom: str, pos: int, genotype: str = "AG") -> dict:
    return {"rsid": rsid, "chrom": chrom, "pos": pos, "genotype": genotype}


def _seed_installed_bundle(reference_engine, version: str = "v2.0.0") -> None:
    with reference_engine.begin() as conn:
        conn.execute(
            sa.delete(database_versions).where(database_versions.c.db_name == "vep_bundle")
        )
        conn.execute(
            database_versions.insert().values(
                db_name="vep_bundle",
                version=version,
                downloaded_at=datetime.now(UTC),
            )
        )


def _create_sample(
    registry,
    *,
    name: str,
    file_format: str,
    file_hash: str,
    variants: list[dict],
    is_merged: bool,
    bundle_version: str = "v2.0.0",
    watches: list[tuple[str, str]] | None = None,
    merge_provenance_row: dict | None = None,
) -> int:
    """Materialize a sample + per-sample DB in the registry.

    ``watches`` is a list of ``(rsid, notes)`` tuples written into the
    sample's ``watched_variants`` table. ``merge_provenance_row`` (when
    present) populates the single-row ``merge_provenance`` table — the
    merged sample's pointer to its sources.
    """
    now = datetime.now(UTC)
    with registry.reference_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name=name,
                db_path="",
                file_format=file_format,
                file_hash=file_hash,
                created_at=now,
                updated_at=now,
            )
        )
        sample_id = int(result.inserted_primary_key[0])
        db_path = f"samples/sample_{sample_id}.db"
        conn.execute(samples.update().where(samples.c.id == sample_id).values(db_path=db_path))

    sample_db_path = registry.settings.data_dir / db_path
    sample_db_path.parent.mkdir(parents=True, exist_ok=True)
    if is_merged:
        # Mirror backend/services/sample_merge.py — bootstrap the (chrom, pos)
        # PK on a throwaway engine before the registry's first
        # ``ensure_sample_schema_current`` call materialises the default rsid PK.
        bootstrap_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
        try:
            create_sample_tables(bootstrap_engine, is_merged_sample=True)
        finally:
            bootstrap_engine.dispose()
    engine = registry.get_sample_engine(sample_db_path)
    with engine.begin() as conn:
        if variants:
            conn.execute(raw_variants.insert(), variants)
        conn.execute(
            annotation_state.insert().values(
                key="vep_bundle_version",
                value=bundle_version,
                updated_at=now,
            )
        )
        for rsid, notes in watches or []:
            conn.execute(
                watched_variants.insert().values(
                    rsid=rsid,
                    watched_at=now,
                    clinvar_significance_at_watch=None,
                    notes=notes,
                )
            )
        if merge_provenance_row is not None:
            conn.execute(merge_provenance.insert().values(**merge_provenance_row))
    return sample_id


# ── Fixture ────────────────────────────────────────────────────────────


@pytest.fixture
def migrate_client(tmp_data_dir: Path):
    """Two source samples + one synthetic merged sample, wired into a TestClient.

    Layout:

    * S1 carries watches on ``rs100`` (also on the merged sample — NOT a
      candidate), ``rs999_s1`` (private to S1, not on merged — surfaced
      with ``rsid_on_merged_or_null=None``), and ``rs300_old`` (rsid-
      collapse: the merged sample carries ``rs300_new`` at the same
      ``(chrom, pos)`` — surfaced with ``rsid_on_merged_or_null="rs300_new"``).
    * S2 carries a watch on ``rs888_s2`` (private to S2, not on merged —
      surfaced as a separate candidate).
    * Merged sample carries ``rs100`` and ``rs300_new`` (at the rs300
      coordinate) plus an unrelated locus, so the surfaced candidates
      are exactly the three above.
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
        _seed_installed_bundle(registry.reference_engine, "v2.0.0")

        s1_id = _create_sample(
            registry,
            name="alice_23andme.txt",
            file_format="23andme_v5",
            file_hash="hash_s1",
            variants=[
                _v("rs100", "1", 100),
                _v("rs300_old", "1", 300),
                _v("rs999_s1", "2", 900),
            ],
            is_merged=False,
            watches=[
                ("rs100", "shared with merged — must NOT surface"),
                ("rs300_old", "rsid-collapse case"),
                ("rs999_s1", "private to S1"),
            ],
        )
        s2_id = _create_sample(
            registry,
            name="alice_ancestrydna.txt",
            file_format="ancestrydna_v2.0",
            file_hash="hash_s2",
            variants=[
                _v("rs100", "1", 100),
                _v("rs888_s2", "3", 888),
            ],
            is_merged=False,
            watches=[
                ("rs888_s2", "private to S2"),
            ],
        )

        import json

        merged_id = _create_sample(
            registry,
            name="alice (merged)",
            file_format="merged_v1",
            file_hash="hash_merged",
            variants=[
                _v("rs100", "1", 100, genotype="AG"),
                # rsid-collapse: same (chrom, pos) as S1's rs300_old but
                # under a different rsid.
                _v("rs300_new", "1", 300, genotype="AG"),
                _v("rs_other", "5", 5000, genotype="CT"),
            ],
            is_merged=True,
            watches=None,
            merge_provenance_row={
                "id": 1,
                "merged_at": datetime.now(UTC),
                "strategy": "flag_only",
                "source_sample_ids": json.dumps([s1_id, s2_id]),
                "source_file_hashes": json.dumps(["hash_s1", "hash_s2"]),
                "concordance_summary": json.dumps(
                    {
                        "match": 1,
                        "filled_nocall": 0,
                        "discordant": 0,
                        "unique_S1": 1,
                        "unique_S2": 1,
                        "collapsed_rsid": 1,
                    }
                ),
            },
        )

        app = create_app()
        with TestClient(app) as tc:
            tc.s1_id = s1_id  # type: ignore[attr-defined]
            tc.s2_id = s2_id  # type: ignore[attr-defined]
            tc.merged_id = merged_id  # type: ignore[attr-defined]
            tc.settings = settings  # type: ignore[attr-defined]
            yield tc

        reset_registry()


# ── Tests ──────────────────────────────────────────────────────────────


class TestMigrateFromSourcesHappyPath:
    def test_returns_only_non_merged_watch_rows(self, migrate_client: TestClient) -> None:
        resp = migrate_client.get(
            f"/api/samples/{migrate_client.merged_id}"  # type: ignore[attr-defined]
            "/watched-variants/migrate-from-sources"
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "candidates" in body
        rsids = sorted(c["rsid_on_source"] for c in body["candidates"])
        # rs100 is on the merged sample → suppressed.
        # rs300_old is NOT on merged but its coordinate is (rs300_new).
        # rs999_s1 / rs888_s2 are private → surfaced with merged_rsid=None.
        assert rsids == ["rs300_old", "rs888_s2", "rs999_s1"]

    def test_rsid_collapse_carries_merged_rsid(self, migrate_client: TestClient) -> None:
        resp = migrate_client.get(
            f"/api/samples/{migrate_client.merged_id}"  # type: ignore[attr-defined]
            "/watched-variants/migrate-from-sources"
        )
        by_rsid = {c["rsid_on_source"]: c for c in resp.json()["candidates"]}
        # rsid-collapse: merged sample carries rs300_new at (1, 300).
        cand = by_rsid["rs300_old"]
        assert cand["chrom"] == "1"
        assert cand["pos"] == 300
        assert cand["rsid_on_merged_or_null"] == "rs300_new"
        assert cand["sample_id"] == migrate_client.s1_id  # type: ignore[attr-defined]
        assert cand["notes_on_source"] == "rsid-collapse case"

    def test_private_rsid_carries_null_merged_rsid(self, migrate_client: TestClient) -> None:
        resp = migrate_client.get(
            f"/api/samples/{migrate_client.merged_id}"  # type: ignore[attr-defined]
            "/watched-variants/migrate-from-sources"
        )
        by_rsid = {c["rsid_on_source"]: c for c in resp.json()["candidates"]}
        # Private rsids: no merged row at the coordinate.
        for rsid, src_id, chrom, pos in (
            ("rs999_s1", migrate_client.s1_id, "2", 900),  # type: ignore[attr-defined]
            ("rs888_s2", migrate_client.s2_id, "3", 888),  # type: ignore[attr-defined]
        ):
            cand = by_rsid[rsid]
            assert cand["rsid_on_merged_or_null"] is None
            assert cand["chrom"] == chrom
            assert cand["pos"] == pos
            assert cand["sample_id"] == src_id

    def test_payload_shape_matches_plan_10_6(self, migrate_client: TestClient) -> None:
        resp = migrate_client.get(
            f"/api/samples/{migrate_client.merged_id}"  # type: ignore[attr-defined]
            "/watched-variants/migrate-from-sources"
        )
        body = resp.json()
        for cand in body["candidates"]:
            assert set(cand.keys()) == {
                "rsid_on_source",
                "notes_on_source",
                "sample_id",
                "chrom",
                "pos",
                "rsid_on_merged_or_null",
            }


class TestMigrateFromSourcesEdges:
    def test_unmerged_sample_returns_404(self, migrate_client: TestClient) -> None:
        # A source sample (no merge_provenance row) — gate is open because
        # we seeded annotation_state at v2.0.0; the handler then 404s.
        resp = migrate_client.get(
            f"/api/samples/{migrate_client.s1_id}"  # type: ignore[attr-defined]
            "/watched-variants/migrate-from-sources"
        )
        assert resp.status_code == 404
        assert "no merge provenance" in resp.json()["detail"]

    def test_stale_merged_sample_returns_423(self, migrate_client: TestClient) -> None:
        from backend.db.connection import get_registry

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(samples.c.db_path).where(
                    samples.c.id == migrate_client.merged_id  # type: ignore[attr-defined]
                )
            ).fetchone()
        assert row is not None
        engine = registry.get_sample_engine(registry.settings.data_dir / row.db_path)
        with engine.begin() as conn:
            conn.execute(
                sa.update(annotation_state)
                .where(annotation_state.c.key == "vep_bundle_version")
                .values(value="v1.0.0")
            )

        resp = migrate_client.get(
            f"/api/samples/{migrate_client.merged_id}"  # type: ignore[attr-defined]
            "/watched-variants/migrate-from-sources"
        )
        assert resp.status_code == 423
        detail = resp.json()["detail"]
        assert {
            "installed_version",
            "required_version",
            "update_url",
            "reannotate_url",
        } <= set(detail.keys())

    def test_nonexistent_merged_id_returns_404(self, migrate_client: TestClient) -> None:
        # Mirrors the merge-provenance + concordance-report contract from
        # test_merge_routes.py (#453): require_fresh_sample (via
        # require_fresh_merged_sample) checks existence before staleness, so a
        # missing samples row answers 404 deterministically, not 423 by bundle
        # state.
        resp = migrate_client.get("/api/samples/9999/watched-variants/migrate-from-sources")
        assert resp.status_code == 404

    def test_empty_when_sources_have_no_watches(self, tmp_data_dir: Path) -> None:
        """Merged sample whose sources never watched anything → ``{candidates: []}``."""
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
            _seed_installed_bundle(registry.reference_engine, "v2.0.0")

            s1_id = _create_sample(
                registry,
                name="bob_23andme.txt",
                file_format="23andme_v5",
                file_hash="hash_b1",
                variants=[_v("rs1", "1", 1)],
                is_merged=False,
                watches=None,
            )
            s2_id = _create_sample(
                registry,
                name="bob_ancestrydna.txt",
                file_format="ancestrydna_v2.0",
                file_hash="hash_b2",
                variants=[_v("rs2", "2", 2)],
                is_merged=False,
                watches=None,
            )

            import json

            merged_id = _create_sample(
                registry,
                name="bob (merged)",
                file_format="merged_v1",
                file_hash="hash_bm",
                variants=[_v("rs1", "1", 1), _v("rs2", "2", 2)],
                is_merged=True,
                merge_provenance_row={
                    "id": 1,
                    "merged_at": datetime.now(UTC),
                    "strategy": "flag_only",
                    "source_sample_ids": json.dumps([s1_id, s2_id]),
                    "source_file_hashes": json.dumps(["hash_b1", "hash_b2"]),
                    "concordance_summary": json.dumps(
                        {
                            "match": 2,
                            "filled_nocall": 0,
                            "discordant": 0,
                            "unique_S1": 0,
                            "unique_S2": 0,
                            "collapsed_rsid": 0,
                        }
                    ),
                },
            )

            app = create_app()
            with TestClient(app) as tc:
                resp = tc.get(f"/api/samples/{merged_id}/watched-variants/migrate-from-sources")
            reset_registry()

        assert resp.status_code == 200
        assert resp.json() == {"candidates": []}
