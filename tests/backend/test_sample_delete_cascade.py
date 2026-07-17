"""Source-deletion cascade tests (Step 66 / MRG-02a + Step 82 / MRG-08g; AncestryDNA Plan §10.8).

Exercises :mod:`backend.services.sample_delete` plus the DELETE route +
``GET /api/samples/{id}/merged-children`` preview surface.

Two test surfaces are split by class group:

* **Step 66 / MRG-02a — contract isolation.** The original suites
  (``TestListMergedChildren``, ``TestDeleteSampleWithCascade``,
  ``TestMergedChildrenRoute``, ``TestDeleteRouteCascade``) build merged
  children by hand — a ``samples`` row plus a per-sample DB carrying a
  hand-written ``merge_provenance`` payload — so the cascade walk is
  exercised in isolation from the full merge orchestration in Step 65.
* **Step 82 / MRG-08g — end-to-end cascade.** ``TestMergeThenDeleteCascade``
  drives the same DELETE route after running real :func:`merge_samples`
  (Plan §10.5), so the provenance row + merged DB the cascade walks are
  the ones the production merge service writes, not hand-rolled fixtures.
  The negative case (delete a sample that is *not* a source of any merge)
  asserts the cascade path stays dormant — no merged children listed, no
  unrelated sample touched.
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
from backend.db.connection import DBRegistry, get_registry, reset_registry
from backend.db.sample_schema import (
    CYP2C9_PHENYTOIN_LEGACY_GUIDANCE_VERSION,
    create_sample_tables,
)
from backend.db.tables import (
    merge_provenance,
    raw_variants,
    reannotation_prompts,
    reference_metadata,
    sample_metadata_table,
    samples,
)
from backend.services.sample_delete import (
    delete_sample_with_cascade,
    list_merged_children,
)

# ── Test-scoped registry mirroring test_sample_merge.py ──────────────


@pytest.fixture
def cascade_registry(tmp_data_dir: Path):
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    ref_engine.dispose()

    with patch("backend.db.connection.get_settings", return_value=settings):
        reset_registry()
        registry = get_registry()
        try:
            yield registry
        finally:
            registry.dispose_all()
            reset_registry()


# ── Helpers — build samples + per-sample DBs by hand ─────────────────


def _make_source_sample(
    registry: DBRegistry,
    *,
    name: str,
    file_format: str = "23andme_v5",
    file_hash: str = "deadbeef",
    is_merged: bool = False,
) -> int:
    """Insert a samples row and materialise its per-sample DB."""
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
    bootstrap = sa.create_engine(f"sqlite:///{sample_db_path}")
    try:
        create_sample_tables(bootstrap, is_merged_sample=is_merged)
    finally:
        bootstrap.dispose()

    engine = registry.get_sample_engine(sample_db_path)
    with engine.begin() as conn:
        conn.execute(
            sample_metadata_table.insert().values(
                id=1,
                name=name,
                file_format=file_format,
                file_hash=file_hash,
                created_at=now,
                updated_at=now,
            )
        )
        # Drop in one row so the file isn't empty.
        conn.execute(
            raw_variants.insert(),
            [{"rsid": "rs1", "chrom": "1", "pos": 100, "genotype": "AG"}],
        )
    return sample_id


def _make_merged_child(
    registry: DBRegistry,
    *,
    name: str,
    source_ids: list[int],
    strategy: str = "flag_only",
    file_hash: str = "mergedhash",
) -> int:
    """Insert a samples row with ``file_format='merged_v1'`` and a merge_provenance row."""
    merged_id = _make_source_sample(
        registry,
        name=name,
        file_format="merged_v1",
        file_hash=file_hash,
        is_merged=True,
    )
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == merged_id)
        ).fetchone()
    assert row is not None
    sample_db_path = registry.settings.data_dir / row.db_path
    engine = registry.get_sample_engine(sample_db_path)
    with engine.begin() as conn:
        conn.execute(
            merge_provenance.insert().values(
                id=1,
                merged_at=datetime.now(UTC),
                strategy=strategy,
                source_sample_ids=json.dumps(source_ids),
                source_file_hashes=json.dumps(["a", "b"]),
                concordance_summary=json.dumps(
                    {
                        "match": 1,
                        "filled_nocall": 0,
                        "discordant": 0,
                        "unique_S1": 0,
                        "unique_S2": 0,
                        "collapsed_rsid": 0,
                    }
                ),
            )
        )
    return merged_id


def _sample_db_path(registry: DBRegistry, sample_id: int) -> Path:
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    assert row is not None
    return registry.settings.data_dir / row.db_path


# ── list_merged_children ─────────────────────────────────────────────


class TestListMergedChildren:
    def test_no_merged_children_when_sample_never_merged(
        self, cascade_registry: DBRegistry
    ) -> None:
        sid = _make_source_sample(cascade_registry, name="alice.txt")
        assert list_merged_children(cascade_registry, sid) == []

    def test_lists_merged_child_referencing_source(self, cascade_registry: DBRegistry) -> None:
        s1 = _make_source_sample(cascade_registry, name="alice_23andme.txt")
        s2 = _make_source_sample(cascade_registry, name="alice_ancestrydna.txt")
        merged = _make_merged_child(
            cascade_registry,
            name="alice (merged)",
            source_ids=[s1, s2],
        )
        children = list_merged_children(cascade_registry, s1)
        assert [c.id for c in children] == [merged]
        assert children[0].name == "alice (merged)"

    def test_lists_both_sources_pointing_to_same_merged(
        self, cascade_registry: DBRegistry
    ) -> None:
        s1 = _make_source_sample(cascade_registry, name="a.txt")
        s2 = _make_source_sample(cascade_registry, name="b.txt")
        merged = _make_merged_child(cascade_registry, name="ab (merged)", source_ids=[s1, s2])
        assert [c.id for c in list_merged_children(cascade_registry, s1)] == [merged]
        assert [c.id for c in list_merged_children(cascade_registry, s2)] == [merged]

    def test_skips_merged_with_missing_db_file(
        self, cascade_registry: DBRegistry, caplog: pytest.LogCaptureFixture
    ) -> None:
        s1 = _make_source_sample(cascade_registry, name="a.txt")
        s2 = _make_source_sample(cascade_registry, name="b.txt")
        merged = _make_merged_child(cascade_registry, name="m.txt", source_ids=[s1, s2])
        # Drop the merged DB file but keep the reference row — half-broken
        # install. The walk should skip + log, not raise.
        merged_db = _sample_db_path(cascade_registry, merged)
        cascade_registry.dispose_sample_engine(merged_db)
        merged_db.unlink()

        with caplog.at_level("WARNING"):
            children = list_merged_children(cascade_registry, s1)
        assert children == []
        assert any(r.message == "merged_sample_db_missing" for r in caplog.records)

    def test_skips_merged_with_malformed_provenance(
        self, cascade_registry: DBRegistry, caplog: pytest.LogCaptureFixture
    ) -> None:
        s1 = _make_source_sample(cascade_registry, name="a.txt")
        s2 = _make_source_sample(cascade_registry, name="b.txt")
        merged = _make_merged_child(cascade_registry, name="m.txt", source_ids=[s1, s2])
        merged_db = _sample_db_path(cascade_registry, merged)
        engine = cascade_registry.get_sample_engine(merged_db)
        with engine.begin() as conn:
            conn.execute(
                merge_provenance.update()
                .where(merge_provenance.c.id == 1)
                .values(source_sample_ids="not-json")
            )

        with caplog.at_level("WARNING"):
            children = list_merged_children(cascade_registry, s1)
        assert children == []
        assert any(r.message == "merged_provenance_malformed" for r in caplog.records)


# ── delete_sample_with_cascade ───────────────────────────────────────


class TestDeleteSampleWithCascade:
    def test_deletes_unmerged_sample_returns_no_children(
        self, cascade_registry: DBRegistry
    ) -> None:
        sid = _make_source_sample(cascade_registry, name="alone.txt")
        db_path = _sample_db_path(cascade_registry, sid)
        assert db_path.exists()

        result = delete_sample_with_cascade(cascade_registry, sid)
        assert result is not None
        assert result.deleted_sample_id == sid
        assert result.deleted_merged_children == []
        assert not db_path.exists()
        with cascade_registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(samples).where(samples.c.id == sid)).fetchone()
        assert row is None

    def test_cascade_removes_merged_child_when_source_deleted(
        self, cascade_registry: DBRegistry
    ) -> None:
        s1 = _make_source_sample(cascade_registry, name="s1.txt")
        s2 = _make_source_sample(cascade_registry, name="s2.txt")
        merged = _make_merged_child(cascade_registry, name="m.txt", source_ids=[s1, s2])

        merged_db = _sample_db_path(cascade_registry, merged)
        s1_db = _sample_db_path(cascade_registry, s1)
        s2_db = _sample_db_path(cascade_registry, s2)
        assert merged_db.exists() and s1_db.exists() and s2_db.exists()

        result = delete_sample_with_cascade(cascade_registry, s1)
        assert result is not None
        assert result.deleted_sample_id == s1
        assert [c.id for c in result.deleted_merged_children] == [merged]

        # Source + merged gone; other source survives.
        with cascade_registry.reference_engine.connect() as conn:
            ids = [r.id for r in conn.execute(sa.select(samples.c.id))]
        assert s1 not in ids
        assert merged not in ids
        assert s2 in ids
        assert not s1_db.exists()
        assert not merged_db.exists()
        assert s2_db.exists()

    def test_cascade_removes_prompts_before_sample_ids_can_be_reused(
        self, cascade_registry: DBRegistry
    ) -> None:
        s1 = _make_source_sample(cascade_registry, name="s1.txt")
        s2 = _make_source_sample(cascade_registry, name="s2.txt")
        merged = _make_merged_child(cascade_registry, name="m.txt", source_ids=[s1, s2])
        correction = json.dumps(
            [
                {
                    "db_name": "cpic",
                    "recorded_version": CYP2C9_PHENYTOIN_LEGACY_GUIDANCE_VERSION,
                    "current_version": "v1.58.0",
                }
            ]
        )
        with cascade_registry.reference_engine.begin() as conn:
            conn.execute(
                reannotation_prompts.insert(),
                [
                    {
                        "sample_id": sample_id,
                        "db_name": "reference_data",
                        "db_version": "multiple",
                        "prompt_type": "version_staleness",
                        "stale_databases": correction,
                        "dismissed": True,
                    }
                    for sample_id in (s1, merged, s2)
                ],
            )

        delete_sample_with_cascade(cascade_registry, s1)

        with cascade_registry.reference_engine.connect() as conn:
            prompt_sample_ids = (
                conn.execute(
                    sa.select(reannotation_prompts.c.sample_id).order_by(
                        reannotation_prompts.c.sample_id
                    )
                )
                .scalars()
                .all()
            )
        assert prompt_sample_ids == [s2]

    def test_delete_recovers_when_partial_registry_lacks_prompt_table(
        self, cascade_registry: DBRegistry
    ) -> None:
        sample_id = _make_source_sample(cascade_registry, name="legacy.txt")
        sample_db_path = _sample_db_path(cascade_registry, sample_id)
        with cascade_registry.reference_engine.begin() as conn:
            conn.execute(sa.text("DROP TABLE reannotation_prompts"))

        result = delete_sample_with_cascade(cascade_registry, sample_id)

        assert result is not None
        assert sample_db_path.exists() is False
        with cascade_registry.reference_engine.connect() as conn:
            remaining = conn.execute(
                sa.select(sa.func.count()).select_from(samples).where(samples.c.id == sample_id)
            ).scalar_one()
        assert remaining == 0

    def test_returns_none_for_missing_sample(self, cascade_registry: DBRegistry) -> None:
        assert delete_sample_with_cascade(cascade_registry, 99999) is None

    def test_cascade_logged_with_child_ids(
        self,
        cascade_registry: DBRegistry,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        s1 = _make_source_sample(cascade_registry, name="s1.txt")
        s2 = _make_source_sample(cascade_registry, name="s2.txt")
        merged = _make_merged_child(cascade_registry, name="merged.txt", source_ids=[s1, s2])

        with caplog.at_level("INFO"):
            delete_sample_with_cascade(cascade_registry, s1)
        cascade_logs = [r for r in caplog.records if r.message == "sample_delete_cascade"]
        assert len(cascade_logs) == 1
        # structlog-style ``extra`` lands as attributes on the LogRecord.
        record = cascade_logs[0]
        assert record.deleted_sample_id == s1
        assert record.deleted_merged_children == [{"id": merged, "name": "merged.txt"}]

    def test_unmerged_delete_has_empty_cascade_in_log(
        self,
        cascade_registry: DBRegistry,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        sid = _make_source_sample(cascade_registry, name="alone.txt")
        with caplog.at_level("INFO"):
            delete_sample_with_cascade(cascade_registry, sid)
        cascade_logs = [r for r in caplog.records if r.message == "sample_delete_cascade"]
        assert len(cascade_logs) == 1
        assert cascade_logs[0].deleted_merged_children == []


# ── DELETE + preview routes ──────────────────────────────────────────


@pytest.fixture
def client(tmp_data_dir: Path):
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    ref_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc, get_registry()
        reset_registry()


class TestMergedChildrenRoute:
    def test_empty_list_for_unmerged_sample(self, client) -> None:
        tc, registry = client
        sid = _make_source_sample(registry, name="solo.txt")
        resp = tc.get(f"/api/samples/{sid}/merged-children")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_lists_merged_children_for_source(self, client) -> None:
        tc, registry = client
        s1 = _make_source_sample(registry, name="s1.txt")
        s2 = _make_source_sample(registry, name="s2.txt")
        merged = _make_merged_child(registry, name="merged.txt", source_ids=[s1, s2])
        resp = tc.get(f"/api/samples/{s1}/merged-children")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload == [{"id": merged, "name": "merged.txt"}]

    def test_returns_404_when_sample_missing(self, client) -> None:
        tc, _ = client
        resp = tc.get("/api/samples/99999/merged-children")
        assert resp.status_code == 404


class TestDeleteRouteCascade:
    def test_delete_cascades_via_route(self, client) -> None:
        tc, registry = client
        s1 = _make_source_sample(registry, name="s1.txt")
        s2 = _make_source_sample(registry, name="s2.txt")
        merged = _make_merged_child(registry, name="merged.txt", source_ids=[s1, s2])

        resp = tc.delete(f"/api/samples/{s1}")
        assert resp.status_code == 204

        with registry.reference_engine.connect() as conn:
            ids = [r.id for r in conn.execute(sa.select(samples.c.id))]
        assert s1 not in ids
        assert merged not in ids
        assert s2 in ids

    def test_delete_unmerged_sample_returns_204(self, client) -> None:
        tc, registry = client
        sid = _make_source_sample(registry, name="solo.txt")
        resp = tc.delete(f"/api/samples/{sid}")
        assert resp.status_code == 204
        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(samples).where(samples.c.id == sid)).fetchone()
        assert row is None

    def test_delete_missing_sample_returns_404(self, client) -> None:
        tc, _ = client
        resp = tc.delete("/api/samples/99999")
        assert resp.status_code == 404


# ── Step 82 / MRG-08g — end-to-end via real merge_samples ────────────


from backend.db.tables import (  # noqa: E402  (positioned alongside Step 82 surface)
    annotation_state,
    database_versions,
    individuals,
    jobs,
)
from backend.services.sample_merge import (  # noqa: E402
    MergeStrategy,
    merge_samples,
)


def _create_individual_e2e(registry: DBRegistry, display_name: str) -> int:
    """Mirror of ``test_sample_merge.py::_create_individual``."""
    with registry.reference_engine.begin() as conn:
        result = conn.execute(
            individuals.insert().values(
                display_name=display_name,
                notes="",
                updated_at=datetime.now(UTC),
            )
        )
    return int(result.inserted_primary_key[0])


def _seed_installed_vep_bundle(registry: DBRegistry, version: str = "v2.0.0") -> None:
    """Seed ``database_versions['vep_bundle']`` so staleness compares cleanly."""
    with registry.reference_engine.begin() as conn:
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


def _make_mergeable_source(
    registry: DBRegistry,
    *,
    individual_id: int,
    name: str,
    file_format: str,
    file_hash: str,
    variants: list[dict],
    bundle_version: str = "v2.0.0",
) -> int:
    """Insert a source sample that satisfies every Plan §10.5 step 1 guard.

    Adds the ``annotation_state['vep_bundle_version']`` row + a completed
    ``jobs`` row so :func:`backend.services.sample_merge.merge_samples`'s
    membership / status / freshness checks all pass.
    """
    now = datetime.now(UTC)
    with registry.reference_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name=name,
                db_path="",
                file_format=file_format,
                file_hash=file_hash,
                individual_id=individual_id,
                created_at=now,
                updated_at=now,
            )
        )
        sample_id = int(result.inserted_primary_key[0])
        db_path = f"samples/sample_{sample_id}.db"
        conn.execute(samples.update().where(samples.c.id == sample_id).values(db_path=db_path))
        conn.execute(
            jobs.insert().values(
                job_id=f"job-{sample_id}",
                sample_id=sample_id,
                job_type="annotation",
                status="complete",
                progress_pct=100.0,
                message="",
                created_at=now,
                updated_at=now,
            )
        )

    sample_db_path = registry.settings.data_dir / db_path
    sample_db_path.parent.mkdir(parents=True, exist_ok=True)
    bootstrap = sa.create_engine(f"sqlite:///{sample_db_path}")
    try:
        create_sample_tables(bootstrap, is_merged_sample=False)
    finally:
        bootstrap.dispose()

    engine = registry.get_sample_engine(sample_db_path)
    with engine.begin() as conn:
        conn.execute(
            sample_metadata_table.insert().values(
                id=1,
                name=name,
                file_format=file_format,
                file_hash=file_hash,
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(raw_variants.insert(), variants)
        conn.execute(
            annotation_state.insert().values(
                key="vep_bundle_version",
                value=bundle_version,
                updated_at=now,
            )
        )
    return sample_id


def _noop_annotation_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the Huey enqueue at the tail of ``merge_samples`` with a no-op.

    The cascade contract is independent of whether the merged sample's
    annotation job actually ran; keeping the enqueue inert keeps the test
    fast and isolated from the Huey worker plumbing.
    """
    import backend.tasks.huey_tasks as huey_tasks

    monkeypatch.setattr(huey_tasks, "create_annotation_job", lambda _sid: "noop-job")
    monkeypatch.setattr(huey_tasks, "run_annotation_task", lambda *_args, **_kw: None)


# Same per-source variant payloads as ``test_sample_merge.py`` so the
# merged sample produced here matches the well-exercised happy-path shape
# — keeps this file focused on the cascade invariant.
_E2E_S1_VARIANTS = [
    {"rsid": "rs100", "chrom": "1", "pos": 100, "genotype": "AG"},
    {"rsid": "rs200", "chrom": "1", "pos": 200, "genotype": "CT"},
    {"rsid": "rs500", "chrom": "2", "pos": 500, "genotype": "GG"},
]
_E2E_S2_VARIANTS = [
    {"rsid": "rs100", "chrom": "1", "pos": 100, "genotype": "AG"},
    {"rsid": "rs200", "chrom": "1", "pos": 200, "genotype": "CT"},
    {"rsid": "rs600", "chrom": "2", "pos": 600, "genotype": "AT"},
]


class TestMergeThenDeleteCascade:
    """Plan §10.8 end-to-end: real ``merge_samples`` → DELETE → cascade.

    Step 66's classes above hand-build the merged child so the cascade walk
    is exercised in isolation; Step 82 covers the production path —
    :func:`merge_samples` writes the ``samples`` row + per-sample DB +
    ``merge_provenance`` payload, and the DELETE route then has to walk the
    same artefacts the production merge service emits.
    """

    def test_delete_source_cascades_to_real_merged_child(
        self,
        client,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        tc, registry = client
        _seed_installed_vep_bundle(registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual_e2e(registry, "Jane Doe")
        s1_id = _make_mergeable_source(
            registry,
            individual_id=individual_id,
            name="jane_23andme.txt",
            file_format="23andme_v5",
            file_hash="hash_s1",
            variants=_E2E_S1_VARIANTS,
        )
        s2_id = _make_mergeable_source(
            registry,
            individual_id=individual_id,
            name="jane_ancestrydna.txt",
            file_format="ancestrydna_v2.0",
            file_hash="hash_s2",
            variants=_E2E_S2_VARIANTS,
        )

        merged_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (merged)",
        )
        assert merged_id not in (s1_id, s2_id)
        merged_db = _sample_db_path(registry, merged_id)
        s1_db = _sample_db_path(registry, s1_id)
        s2_db = _sample_db_path(registry, s2_id)
        assert merged_db.exists() and s1_db.exists() and s2_db.exists()

        # The cascade must dispose any cached engine on the merged DB before
        # unlinking; subscribe to the structured log so the assertion surface
        # locks both the merged-child id and the source id.
        with caplog.at_level("INFO"):
            resp = tc.delete(f"/api/samples/{s1_id}")
        assert resp.status_code == 204

        # Reference-DB rows: source + merged gone, other source survives.
        with registry.reference_engine.connect() as conn:
            remaining_ids = {int(r.id) for r in conn.execute(sa.select(samples.c.id))}
        assert s1_id not in remaining_ids
        assert merged_id not in remaining_ids
        assert s2_id in remaining_ids

        # On-disk DB files mirror the reference rows.
        assert not s1_db.exists()
        assert not merged_db.exists()
        assert s2_db.exists()

        cascade_logs = [r for r in caplog.records if r.message == "sample_delete_cascade"]
        assert len(cascade_logs) == 1
        record = cascade_logs[0]
        assert record.deleted_sample_id == s1_id
        assert record.deleted_merged_children == [{"id": merged_id, "name": "Jane Doe (merged)"}]

    def test_delete_other_source_also_cascades(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Symmetric coverage — both source IDs appear in
        # ``merge_provenance.source_sample_ids``, so deleting *either* one
        # must reap the merged child. Locks the contract that the cascade
        # walk doesn't accidentally key off the first-position source only.
        tc, registry = client
        _seed_installed_vep_bundle(registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual_e2e(registry, "Owner")
        s1_id = _make_mergeable_source(
            registry,
            individual_id=individual_id,
            name="a.txt",
            file_format="23andme_v5",
            file_hash="h1",
            variants=_E2E_S1_VARIANTS,
        )
        s2_id = _make_mergeable_source(
            registry,
            individual_id=individual_id,
            name="b.txt",
            file_format="ancestrydna_v2.0",
            file_hash="h2",
            variants=_E2E_S2_VARIANTS,
        )
        merged_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="m.txt",
        )

        resp = tc.delete(f"/api/samples/{s2_id}")
        assert resp.status_code == 204

        with registry.reference_engine.connect() as conn:
            remaining_ids = {int(r.id) for r in conn.execute(sa.select(samples.c.id))}
        assert s2_id not in remaining_ids
        assert merged_id not in remaining_ids
        assert s1_id in remaining_ids

    def test_delete_unrelated_sample_does_not_cascade(
        self,
        client,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Negative half of MRG-08g: a sample that is not listed in any
        # ``merge_provenance.source_sample_ids`` must NOT trigger cascade
        # behaviour — neither the merged sample nor the unrelated sample's
        # paired source can be touched, and the cascade log line must record
        # an empty children list.
        tc, registry = client
        _seed_installed_vep_bundle(registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual_e2e(registry, "Owner")
        s1_id = _make_mergeable_source(
            registry,
            individual_id=individual_id,
            name="a.txt",
            file_format="23andme_v5",
            file_hash="h1",
            variants=_E2E_S1_VARIANTS,
        )
        s2_id = _make_mergeable_source(
            registry,
            individual_id=individual_id,
            name="b.txt",
            file_format="ancestrydna_v2.0",
            file_hash="h2",
            variants=_E2E_S2_VARIANTS,
        )
        merged_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="m.txt",
        )

        # An unrelated, unmerged sample under a different individual — its
        # id never appears in any ``merge_provenance.source_sample_ids``.
        other_individual = _create_individual_e2e(registry, "Other")
        loner_id = _make_mergeable_source(
            registry,
            individual_id=other_individual,
            name="solo.txt",
            file_format="23andme_v5",
            file_hash="h3",
            variants=_E2E_S1_VARIANTS,
        )
        loner_db = _sample_db_path(registry, loner_id)
        merged_db = _sample_db_path(registry, merged_id)
        s1_db = _sample_db_path(registry, s1_id)
        s2_db = _sample_db_path(registry, s2_id)

        with caplog.at_level("INFO"):
            resp = tc.delete(f"/api/samples/{loner_id}")
        assert resp.status_code == 204

        # Only the unrelated sample is gone; the merged child + both
        # original sources are untouched on disk and in the reference DB.
        with registry.reference_engine.connect() as conn:
            remaining_ids = {int(r.id) for r in conn.execute(sa.select(samples.c.id))}
        assert loner_id not in remaining_ids
        assert {s1_id, s2_id, merged_id}.issubset(remaining_ids)
        assert not loner_db.exists()
        assert merged_db.exists() and s1_db.exists() and s2_db.exists()

        cascade_logs = [r for r in caplog.records if r.message == "sample_delete_cascade"]
        assert len(cascade_logs) == 1
        record = cascade_logs[0]
        assert record.deleted_sample_id == loner_id
        # Empty list locks the contract that the cascade walk fires its
        # ``logger.info`` once per DELETE but lists zero children when the
        # deleted row was never a merge source.
        assert record.deleted_merged_children == []

    def test_merged_children_route_lists_real_merged_child(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The preview surface (``GET /api/samples/{id}/merged-children``)
        # backs the per-row delete confirmation in Settings → Samples; this
        # asserts it observes the real ``merge_provenance`` row written by
        # ``merge_samples`` (Step 66's coverage uses hand-built provenance).
        tc, registry = client
        _seed_installed_vep_bundle(registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual_e2e(registry, "Owner")
        s1_id = _make_mergeable_source(
            registry,
            individual_id=individual_id,
            name="a.txt",
            file_format="23andme_v5",
            file_hash="h1",
            variants=_E2E_S1_VARIANTS,
        )
        s2_id = _make_mergeable_source(
            registry,
            individual_id=individual_id,
            name="b.txt",
            file_format="ancestrydna_v2.0",
            file_hash="h2",
            variants=_E2E_S2_VARIANTS,
        )
        merged_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="merged.txt",
        )

        resp_s1 = tc.get(f"/api/samples/{s1_id}/merged-children")
        assert resp_s1.status_code == 200
        assert resp_s1.json() == [{"id": merged_id, "name": "merged.txt"}]

        resp_s2 = tc.get(f"/api/samples/{s2_id}/merged-children")
        assert resp_s2.status_code == 200
        assert resp_s2.json() == [{"id": merged_id, "name": "merged.txt"}]

        # The merged sample itself has no entry — it is the *child* of the
        # merge, not the source of one.
        resp_merged = tc.get(f"/api/samples/{merged_id}/merged-children")
        assert resp_merged.status_code == 200
        assert resp_merged.json() == []
