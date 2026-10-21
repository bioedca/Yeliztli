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
    jobs,
    merge_provenance,
    raw_variants,
    reannotation_prompts,
    reference_metadata,
    sample_metadata_table,
    samples,
)
from backend.services import sample_delete as sample_delete_module
from backend.services.sample_delete import (
    delete_sample_with_cascade,
    list_merged_children,
)
from backend.services.sample_operation_lock import (
    SAMPLE_DELETE_JOB_TYPE,
    SAMPLE_EXPORT_JOB_TYPE,
    SampleOperationConflictError,
    reserve_annotation_job,
    reserve_lai_job,
    sample_delete_lease,
    sample_export_lease,
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

    @pytest.mark.parametrize(
        "raw_source_ids",
        [
            pytest.param("not-json", id="syntax"),
            pytest.param("[" + "1" * 5_000 + ",2]", id="oversized-integer"),
            pytest.param("[" * 10_000 + "0" + "]" * 10_000, id="deep-nesting"),
        ],
    )
    def test_skips_merged_with_malformed_provenance(
        self,
        cascade_registry: DBRegistry,
        caplog: pytest.LogCaptureFixture,
        raw_source_ids: str,
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
                .values(source_sample_ids=raw_source_ids)
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

    def test_cascade_removes_transitive_legacy_merged_descendants(
        self, cascade_registry: DBRegistry
    ) -> None:
        """A pre-#2330 nested merge cannot outlive its deleted leaf source."""
        s1 = _make_source_sample(cascade_registry, name="s1.txt")
        s2 = _make_source_sample(cascade_registry, name="s2.txt")
        merged = _make_merged_child(
            cascade_registry,
            name="first generation",
            source_ids=[s1, s2],
        )
        nested = _make_merged_child(
            cascade_registry,
            name="legacy nested generation",
            source_ids=[s2, merged],
        )
        paths = {
            sample_id: _sample_db_path(cascade_registry, sample_id)
            for sample_id in (s1, s2, merged, nested)
        }

        # Deepest-first is both the preview and the deletion order: the nested
        # child must disappear before the merged sample it names as a source.
        assert [child.id for child in list_merged_children(cascade_registry, s1)] == [
            nested,
            merged,
        ]

        result = delete_sample_with_cascade(cascade_registry, s1)

        assert result is not None
        assert [child.id for child in result.deleted_merged_children] == [nested, merged]
        with cascade_registry.reference_engine.connect() as conn:
            surviving_ids = set(conn.execute(sa.select(samples.c.id)).scalars())
        assert surviving_ids == {s2}
        assert paths[s2].exists()
        assert all(not paths[sample_id].exists() for sample_id in (s1, merged, nested))

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


# ── #2314: deletion excludes every operation holding a sample open ───


def _insert_job(registry: DBRegistry, sample_id: int, job_type: str, status: str) -> str:
    """Stand in for an operation holding ``sample_id`` with the given status."""
    now = datetime.now(UTC)
    job_id = f"{job_type}:{status}:{sample_id}"
    with registry.reference_engine.begin() as conn:
        conn.execute(
            jobs.insert().values(
                job_id=job_id,
                sample_id=sample_id,
                job_type=job_type,
                status=status,
                progress_pct=0.0,
                message="in flight",
                created_at=now,
                updated_at=now,
            )
        )
    return job_id


def _finish_job(registry: DBRegistry, job_id: str) -> None:
    with registry.reference_engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.job_id == job_id).values(status="complete"))


def _sample_ids(registry: DBRegistry) -> list[int]:
    with registry.reference_engine.connect() as conn:
        return [int(r.id) for r in conn.execute(sa.select(samples.c.id))]


def _reserve_annotation(registry: DBRegistry, sample_id: int) -> None:
    reserve_annotation_job(
        registry.reference_engine,
        sample_id=sample_id,
        job_id="late-annotation",
        created_at=datetime.now(UTC),
    )


def _reserve_lai(registry: DBRegistry, sample_id: int) -> None:
    reserve_lai_job(
        registry.reference_engine,
        sample_id=sample_id,
        job_id="late-lai",
        created_at=datetime.now(UTC),
    )


def _hold_export(registry: DBRegistry, sample_id: int) -> None:
    with sample_export_lease(registry.reference_engine, sample_id, operation="grch38_liftover"):
        pass


# Every operation that holds a sample's database for its whole run, at every
# status in which it may still be touching that database.
_HOLDING_OPERATIONS = [
    ("annotation", "pending", "being annotated"),
    ("annotation", "running", "being annotated"),
    ("annotation", "cancelling", "being annotated"),
    (SAMPLE_EXPORT_JOB_TYPE, "running", "export, report or liftover"),
    ("lai_analysis", "pending", "local ancestry inference"),
    ("lai_analysis", "running", "local ancestry inference"),
    ("lai_analysis", "cancelling", "local ancestry inference"),
]


class TestDeleteExcludesSampleOperations:
    @pytest.mark.parametrize(("job_type", "status", "named"), _HOLDING_OPERATIONS)
    def test_delete_is_refused_while_an_operation_holds_the_sample(
        self, cascade_registry: DBRegistry, job_type: str, status: str, named: str
    ) -> None:
        sid = _make_source_sample(cascade_registry, name="busy.txt")
        db_path = _sample_db_path(cascade_registry, sid)
        job_id = _insert_job(cascade_registry, sid, job_type, status)

        with pytest.raises(SampleOperationConflictError, match=named):
            delete_sample_with_cascade(cascade_registry, sid)

        # Refused means untouched: the database and the registry row survive.
        assert db_path.exists()
        assert sid in _sample_ids(cascade_registry)

        # Once the operation finishes, deletion proceeds and leaves nothing.
        _finish_job(cascade_registry, job_id)
        assert delete_sample_with_cascade(cascade_registry, sid) is not None
        assert not db_path.exists()
        assert sid not in _sample_ids(cascade_registry)

    def test_finished_operations_do_not_block_deletion(self, cascade_registry: DBRegistry) -> None:
        """Only active rows hold a sample; history must not make it undeletable."""
        sid = _make_source_sample(cascade_registry, name="done.txt")
        for job_type in ("annotation", SAMPLE_EXPORT_JOB_TYPE, "lai_analysis"):
            for status in ("complete", "failed", "cancelled"):
                _insert_job(cascade_registry, sid, job_type, status)

        assert delete_sample_with_cascade(cascade_registry, sid) is not None
        assert not _sample_db_path_for_deleted(cascade_registry, sid).exists()

    @pytest.mark.parametrize(
        ("acquire", "job_type"),
        [
            (_reserve_annotation, "annotation"),
            (_reserve_lai, "lai_analysis"),
            (_hold_export, SAMPLE_EXPORT_JOB_TYPE),
        ],
    )
    def test_an_operation_starting_after_the_delete_reservation_is_refused(
        self, cascade_registry: DBRegistry, acquire, job_type: str
    ) -> None:
        """The case a read-only pre-check cannot cover.

        Deletion has already checked and reserved the sample; an operation that
        tries to start now must lose rather than reopen the database the
        cascade is about to unlink.
        """
        sid = _make_source_sample(cascade_registry, name="racing.txt")

        with sample_delete_lease(
            cascade_registry.reference_engine, [sid], operation="Sample delete"
        ):
            with pytest.raises(SampleOperationConflictError, match="being deleted"):
                acquire(cascade_registry, sid)

        with cascade_registry.reference_engine.connect() as conn:
            rows = conn.execute(
                sa.select(jobs.c.job_id)
                .where(jobs.c.sample_id == sid)
                .where(jobs.c.job_type == job_type)
            ).fetchall()
        assert rows == [], "a refused acquirer must not leave a lease row behind"

        # Released on exit, so the same operation now starts normally.
        acquire(cascade_registry, sid)

    def test_operation_racing_the_unlink_cannot_recreate_the_database(
        self, cascade_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Drive an acquirer at the exact moment the cascade unlinks the file.

        Before #2314 an annotation or export could reserve here and then
        reconnect, recreating ``sample_<id>.db`` after the DELETE returned.
        """
        sid = _make_source_sample(cascade_registry, name="unlinking.txt")
        db_path = _sample_db_path(cascade_registry, sid)
        refused: list[str] = []
        real_delete_files = sample_delete_module._delete_sample_files

        def racing_delete_files(registry: DBRegistry, path: str | None) -> None:
            for name, acquire in (
                ("annotation", _reserve_annotation),
                ("lai", _reserve_lai),
                ("export", _hold_export),
            ):
                try:
                    acquire(registry, sid)
                except SampleOperationConflictError:
                    refused.append(name)
            real_delete_files(registry, path)

        monkeypatch.setattr(sample_delete_module, "_delete_sample_files", racing_delete_files)

        assert delete_sample_with_cascade(cascade_registry, sid) is not None
        assert refused == ["annotation", "lai", "export"]
        assert not db_path.exists()
        assert not Path(f"{db_path}-wal").exists()

    def test_a_busy_merged_child_blocks_the_whole_cascade(
        self, cascade_registry: DBRegistry
    ) -> None:
        """The cascade destroys children, so a child in use refuses the delete.

        Nothing may be unlinked first: refusing after the child is gone but
        before the source would leave half a cascade behind.
        """
        s1 = _make_source_sample(cascade_registry, name="s1.txt")
        s2 = _make_source_sample(cascade_registry, name="s2.txt")
        merged = _make_merged_child(cascade_registry, name="m.txt", source_ids=[s1, s2])
        paths = {sid: _sample_db_path(cascade_registry, sid) for sid in (s1, s2, merged)}
        job_id = _insert_job(cascade_registry, merged, SAMPLE_EXPORT_JOB_TYPE, "running")

        with pytest.raises(SampleOperationConflictError, match=f"Sample {merged} is busy"):
            delete_sample_with_cascade(cascade_registry, s1)

        assert all(path.exists() for path in paths.values())
        assert {s1, s2, merged} <= set(_sample_ids(cascade_registry))

        _finish_job(cascade_registry, job_id)
        result = delete_sample_with_cascade(cascade_registry, s1)
        assert result is not None
        assert [c.id for c in result.deleted_merged_children] == [merged]
        assert not paths[s1].exists()
        assert not paths[merged].exists()
        assert paths[s2].exists()

    def test_merged_children_are_reserved_for_the_whole_cascade(
        self, cascade_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s1 = _make_source_sample(cascade_registry, name="s1.txt")
        s2 = _make_source_sample(cascade_registry, name="s2.txt")
        merged = _make_merged_child(cascade_registry, name="m.txt", source_ids=[s1, s2])
        outcomes: list[str] = []
        real_delete_files = sample_delete_module._delete_sample_files

        def probing_delete_files(registry: DBRegistry, path: str | None) -> None:
            try:
                _hold_export(registry, merged)
            except SampleOperationConflictError as exc:
                outcomes.append(str(exc))
            real_delete_files(registry, path)

        monkeypatch.setattr(sample_delete_module, "_delete_sample_files", probing_delete_files)

        assert delete_sample_with_cascade(cascade_registry, s1) is not None
        # Once for the child's unlink and once for the source's.
        assert len(outcomes) == 2
        assert all("being deleted" in message for message in outcomes)

    def test_no_delete_lease_rows_survive_a_cascade(self, cascade_registry: DBRegistry) -> None:
        s1 = _make_source_sample(cascade_registry, name="s1.txt")
        s2 = _make_source_sample(cascade_registry, name="s2.txt")
        _make_merged_child(cascade_registry, name="m.txt", source_ids=[s1, s2])

        assert delete_sample_with_cascade(cascade_registry, s1) is not None

        with cascade_registry.reference_engine.connect() as conn:
            leftover = conn.execute(
                sa.select(sa.func.count())
                .select_from(jobs)
                .where(jobs.c.job_type == SAMPLE_DELETE_JOB_TYPE)
            ).scalar_one()
        assert leftover == 0

    def test_delete_route_answers_409_while_annotation_runs(self, client) -> None:
        tc, registry = client
        sid = _make_source_sample(registry, name="annotating.txt")
        _insert_job(registry, sid, "annotation", "running")

        resp = tc.delete(f"/api/samples/{sid}")

        assert resp.status_code == 409
        assert "being annotated" in resp.json()["detail"]
        assert sid in _sample_ids(registry)
        assert _sample_db_path(registry, sid).exists()


def _sample_db_path_for_deleted(registry: DBRegistry, sample_id: int) -> Path:
    """Where a now-deleted sample's database lived (the helper path recipe)."""
    return registry.settings.data_dir / f"samples/sample_{sample_id}.db"


# ── Step 82 / MRG-08g — end-to-end via real merge_samples ────────────


from backend.db.tables import (  # noqa: E402  (positioned alongside Step 82 surface)
    annotation_state,
    database_versions,
    individuals,
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
