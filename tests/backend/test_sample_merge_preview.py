"""Backend route tests for ``POST /api/individuals/{id}/merge/preview``
(Step 73 / MRG-03a; Plan §10.6, §15.1).

Step 67 / MRG-03 shipped two thin smoke surfaces for this route:

* ``tests/backend/test_individuals_routes.py::TestMergePreviewRoute`` covered
  routing plumbing (404 on unknown individual, pydantic 422 rejections, basic
  membership 422).
* ``tests/backend/test_sample_merge.py::TestPreviewMerge`` covered the
  service-level helper-split contract — that preview observes the same
  ``_compute_merge_plan`` semantics the commit path writes into
  ``merge_provenance.concordance_summary`` and that the dry-run writes no
  artefacts.

This file is the route's PR-blocking surface per Plan §15.1 MRG-03a's
exhaustive list — every case exercises the live FastAPI route via
``TestClient``, not the service directly:

  (i)   Happy path: ``{concordance_summary, est_duration_seconds}`` with
        bucket counts matching the seven-locus dual fixture (including
        ``collapsed_rsid``); no ``samples`` rows or per-sample DB files
        written (snapshot pre/post).
  (ii)  Stale source → HTTP 423 with the Plan §7.5 payload shape
        (``error`` / ``stale_sample_ids`` / ``message`` / ``reannotate_url``).
  (iii) Mismatched individual (sample linked to a different individual) →
        422 with "not linked" in the detail string.
  (iv)  Invalid strategy → 422 (FastAPI pydantic Literal rejection).
  (v)   Only one source provided → 422 (pydantic ``min_length=2`` rejection).
  (vi)  Source with ``status != 'complete'`` → 422 with the sample id and
        the offending status in the detail string (the route surfaces the
        service's exception text verbatim — the wizard parses sample id +
        status from this string to render the "annotation still running"
        banner).
  (vii) Source whose per-sample DB has no ``annotation_state`` row → per
        Plan §7.4 missing-state fallback, treated as ``v1.0.0`` and routed
        through the stale-source path → 423 with the ``reannotate_url`` key.
        Note from Plan §15.1: "the rsid-collapse tiebreaker reads the VEP
        bundle directly (not ``annotation_state``), so missing
        annotation_state never blocks the tiebreaker mechanics — only the
        staleness gate."

The hand-curated ``expected_concordance.json`` from Plan §15.1 MRG-03a
lands with the dual-upload fixture in Step 75 / MRG-08. Until then this
file uses the same seven-locus synthetic dual fixture
``test_sample_merge.py`` and ``test_merge_routes.py`` lock — the bucket
shape (``match=2``, ``filled_nocall=2``, ``discordant=1``, ``unique_S1=1``,
``unique_S2=1``, ``collapsed_rsid=1``) is identical to what Step 75's
hand-curated fixture will produce, so the assertion stays valid and Step
76 only needs to re-point the data source.

The Huey enqueue at the tail of ``merge_samples`` is no-op'd via
``monkeypatch`` so the test never fires the actual annotation pipeline —
preview never calls the enqueue path, but the fixture stubs it
proactively for symmetry with ``test_merge_routes.py`` and so the
stale-source 423 case can confirm the gate fires before any side effect.
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
    individuals,
    jobs,
    raw_variants,
    reference_metadata,
    samples,
)

# ── Variant batches mirroring test_sample_merge.py ─────────────────────
#
# Same seven-locus shape: every §10.2 concordance bucket is exercised
# (match / filled_nocall ×2 / discordant / unique_S1 / unique_S2) plus a
# different-rsid-same-coordinate locus that forces the rsid-collapse
# tiebreaker (collapsed_rsid=1). Step 75 will replace this with the
# hand-curated dual-upload fixture; this file's assertions are written
# against the bucket counts, which match by design.


def _v(rsid: str, chrom: str, pos: int, genotype: str) -> dict:
    return {"rsid": rsid, "chrom": chrom, "pos": pos, "genotype": genotype}


S1_VARIANTS = [
    _v("rs100", "1", 100, "AG"),
    _v("rs200", "1", 200, "CT"),
    _v("rs300", "1", 300, "--"),
    _v("rs400", "1", 400, "AA"),
    _v("rs500", "2", 500, "GG"),
    _v("rs700_s1", "3", 700, "CT"),
]

S2_VARIANTS = [
    _v("rs100", "1", 100, "AG"),
    _v("rs200", "1", 200, "--"),
    _v("rs300", "1", 300, "GG"),
    _v("rs400", "1", 400, "GG"),
    _v("rs600", "2", 600, "AT"),
    _v("rs700_s2", "3", 700, "CT"),
]

_EXPECTED_SUMMARY = {
    "match": 2,
    "filled_nocall": 2,
    "discordant": 1,
    "unique_S1": 1,
    "unique_S2": 1,
    "collapsed_rsid": 1,
}


# ── Fixture helpers ────────────────────────────────────────────────────


def _noop_annotation_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the merge service's Huey imports so they never touch the queue.

    The preview route does not enqueue annotation — it's a dry-run — so
    this is a defensive symmetry with ``test_merge_routes.py``: should a
    test class call the commit route (e.g. to set up state) the no-op
    keeps the test fully self-contained.
    """
    import backend.tasks.huey_tasks as huey_tasks

    monkeypatch.setattr(huey_tasks, "create_annotation_job", lambda _sid: "noop-job")
    monkeypatch.setattr(huey_tasks, "run_annotation_task", lambda *_a, **_k: None)


def _seed_installed_vep_bundle(reference_engine: sa.Engine, version: str = "v2.0.0") -> None:
    """Seed ``database_versions['vep_bundle']`` so staleness can compare."""
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


def _seed_source_sample(
    registry,
    *,
    individual_id: int | None,
    name: str,
    file_format: str,
    file_hash: str,
    variants: list[dict],
    bundle_version: str | None = "v2.0.0",
    annotation_status: str = "complete",
) -> int:
    """Create one source sample (samples row + per-sample DB + jobs row).

    ``bundle_version=None`` skips the ``annotation_state`` write, which is
    case (vii)'s setup: a per-sample DB whose ``annotation_state`` row is
    absent. The Plan §7.4 missing-state fallback then treats it as
    ``v1.0.0`` and the staleness gate fires.
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
                job_id=f"job-src-{sample_id}",
                sample_id=sample_id,
                job_type="annotation",
                status=annotation_status,
                progress_pct=100.0 if annotation_status == "complete" else 0.0,
                message="",
                created_at=now,
                updated_at=now,
            )
        )

    sample_db_path = registry.settings.data_dir / db_path
    sample_db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = registry.get_sample_engine(sample_db_path)
    create_sample_tables(engine, is_merged_sample=False)
    with engine.begin() as conn:
        if variants:
            conn.execute(raw_variants.insert(), variants)
        if bundle_version is not None:
            conn.execute(
                annotation_state.insert().values(
                    key="vep_bundle_version",
                    value=bundle_version,
                    updated_at=now,
                )
            )
    return sample_id


@pytest.fixture
def preview_client(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """TestClient backed by a temp data dir + pre-seeded individual + two sources.

    Stashes onto the client:

    * ``individual_id`` — the owning individual; both source samples
      below are linked to it.
    * ``s1_id`` / ``s2_id`` — two source samples carrying the seven-locus
      dual fixture; both have ``status='complete'`` annotation jobs and
      ``annotation_state.vep_bundle_version='v2.0.0'`` so they are
      eligible for merging by default.
    * ``settings`` / ``registry`` — the temp-dir Settings and the
      DBRegistry instance, so tests that need to mutate per-sample state
      (case ii's stale rewrite, case vii's annotation_state delete) can do
      so without re-deriving them.

    The installed bundle is seeded at v2.0.0 so the staleness comparison
    has both sides of the inequality to work with.
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
        _seed_installed_vep_bundle(registry.reference_engine, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)

        with registry.reference_engine.begin() as conn:
            result = conn.execute(
                individuals.insert().values(
                    display_name="Jane Doe",
                    notes="",
                    updated_at=datetime.now(UTC),
                )
            )
            individual_id = int(result.inserted_primary_key[0])

        s1_id = _seed_source_sample(
            registry,
            individual_id=individual_id,
            name="jane_23andme.txt",
            file_format="23andme_v5",
            file_hash="hash_s1",
            variants=S1_VARIANTS,
        )
        s2_id = _seed_source_sample(
            registry,
            individual_id=individual_id,
            name="jane_ancestrydna.txt",
            file_format="ancestrydna_v2.0",
            file_hash="hash_s2",
            variants=S2_VARIANTS,
        )

        app = create_app()
        with TestClient(app) as tc:
            tc.individual_id = individual_id  # type: ignore[attr-defined]
            tc.s1_id = s1_id  # type: ignore[attr-defined]
            tc.s2_id = s2_id  # type: ignore[attr-defined]
            tc.settings = settings  # type: ignore[attr-defined]
            tc.registry = registry  # type: ignore[attr-defined]
            yield tc

        reset_registry()


# ── Snapshot helpers (the "no rows written" assertion) ────────────────


def _snapshot_samples_row_count(registry) -> int:
    with registry.reference_engine.connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(samples)).scalar() or 0


def _snapshot_per_sample_db_files(registry) -> list[Path]:
    return sorted((registry.settings.data_dir / "samples").glob("sample_*.db"))


# ══════════════════════════════════════════════════════════════════════
# (i) Happy path
# ══════════════════════════════════════════════════════════════════════


class TestHappyPath:
    """Plan §15.1 (i): ``{concordance_summary, est_duration_seconds}`` shape.

    The dry-run returns the §10.4 (c) bucket counts for the seven-locus
    dual fixture, surfaces the §10.6 duration estimate, and writes no
    artefacts to the reference DB or the on-disk per-sample directory.
    """

    def test_returns_summary_and_estimate(self, preview_client: TestClient) -> None:
        resp = preview_client.post(
            f"/api/individuals/{preview_client.individual_id}/merge/preview",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    preview_client.s1_id,  # type: ignore[attr-defined]
                    preview_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "flag_only",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Plan §10.4 (c) full key set + counts from the fixture.
        assert body["concordance_summary"] == _EXPECTED_SUMMARY
        # 7 merged rows → baseline 5 + 7 // 25_000 = 5.
        assert body["est_duration_seconds"] == 5

    def test_writes_no_artefacts(self, preview_client: TestClient) -> None:
        registry = preview_client.registry  # type: ignore[attr-defined]
        # Snapshot pre — both ``samples`` row count and per-sample DB files
        # on disk. Plan §10.6 declares the dry-run contract; this proves it.
        pre_count = _snapshot_samples_row_count(registry)
        pre_files = _snapshot_per_sample_db_files(registry)

        resp = preview_client.post(
            f"/api/individuals/{preview_client.individual_id}/merge/preview",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    preview_client.s1_id,  # type: ignore[attr-defined]
                    preview_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "flag_only",
            },
        )
        assert resp.status_code == 200, resp.text

        post_count = _snapshot_samples_row_count(registry)
        post_files = _snapshot_per_sample_db_files(registry)
        assert post_count == pre_count
        assert post_files == pre_files

    def test_includes_collapsed_rsid_in_summary(self, preview_client: TestClient) -> None:
        """Plan §15.1 (i) explicit callout: ``collapsed_rsid`` is reported.

        The seven-locus fixture's ``rs700_s1`` / ``rs700_s2`` pair lands at
        ``(3, 700)`` — same coordinate, different rsids — so the §10.2
        step-2 tiebreaker collapses them and ``collapsed_rsid`` increments.
        Locking this separately from the bucket counts so a future helper
        refactor can't silently drop the additive marker.
        """
        resp = preview_client.post(
            f"/api/individuals/{preview_client.individual_id}/merge/preview",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    preview_client.s1_id,  # type: ignore[attr-defined]
                    preview_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "flag_only",
            },
        )
        assert resp.status_code == 200
        summary = resp.json()["concordance_summary"]
        assert "collapsed_rsid" in summary
        assert summary["collapsed_rsid"] == 1

    def test_all_strategies_return_same_summary(self, preview_client: TestClient) -> None:
        """Strategy only affects discordant-locus *resolution*, not bucket counts.

        Plan §10.2 step 3 / §10.3: the strategy decides which call wins at
        a discordant locus; the bucket counts (``match``,
        ``filled_nocall``, ``discordant``, ``unique_S1``, ``unique_S2``,
        ``collapsed_rsid``) are derived from the data and identical across
        all strategies. Preview's payload must reflect that
        invariant — otherwise the wizard's strategy radio would be
        load-bearing on the preview summary, breaking Plan §10.7 step 2's
        "preview computed without writing" contract.
        """
        seen: list[dict] = []
        for strategy in (
            "flag_only",
            "prefer_s1",
            "prefer_s2",
            "prefer_23andme",
            "prefer_ancestrydna",
        ):
            resp = preview_client.post(
                f"/api/individuals/{preview_client.individual_id}/merge/preview",  # type: ignore[attr-defined]
                json={
                    "source_sample_ids": [
                        preview_client.s1_id,  # type: ignore[attr-defined]
                        preview_client.s2_id,  # type: ignore[attr-defined]
                    ],
                    "strategy": strategy,
                },
            )
            assert resp.status_code == 200, resp.text
            seen.append(resp.json()["concordance_summary"])
        assert len(seen) == 5
        assert all(summary == _EXPECTED_SUMMARY for summary in seen)


# ══════════════════════════════════════════════════════════════════════
# (ii) Stale source → 423
# ══════════════════════════════════════════════════════════════════════


class TestStaleSource:
    """Plan §15.1 (ii): stale source returns HTTP 423 with the §7.5 payload.

    The route catches ``StaleSourceError`` and propagates the structured
    detail dict — the wizard renders the re-annotate banner with the same
    shape it does for ``require_fresh_sample`` dependency drift.
    """

    def _mark_stale(self, preview_client: TestClient, sample_id: int) -> None:
        """Rewrite the sample's ``annotation_state`` to v1.0.0 (forces stale)."""
        registry = preview_client.registry  # type: ignore[attr-defined]
        with registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(samples.c.db_path).where(samples.c.id == sample_id)
            ).fetchone()
        assert row is not None
        engine = registry.get_sample_engine(registry.settings.data_dir / row.db_path)
        with engine.begin() as conn:
            conn.execute(
                sa.update(annotation_state)
                .where(annotation_state.c.key == "vep_bundle_version")
                .values(value="v1.0.0")
            )

    def test_stale_source_returns_423(self, preview_client: TestClient) -> None:
        self._mark_stale(preview_client, preview_client.s1_id)  # type: ignore[attr-defined]
        resp = preview_client.post(
            f"/api/individuals/{preview_client.individual_id}/merge/preview",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    preview_client.s1_id,  # type: ignore[attr-defined]
                    preview_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "flag_only",
            },
        )
        assert resp.status_code == 423, resp.text

    def test_stale_payload_has_plan_7_5_shape(self, preview_client: TestClient) -> None:
        """Plan §15.1 (ii): payload carries the structured detail dict.

        The dict mirrors ``backend.api.dependencies.require_fresh_sample``'s
        413 payload so the wizard can render one banner for either path.
        """
        self._mark_stale(preview_client, preview_client.s2_id)  # type: ignore[attr-defined]
        resp = preview_client.post(
            f"/api/individuals/{preview_client.individual_id}/merge/preview",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    preview_client.s1_id,  # type: ignore[attr-defined]
                    preview_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "flag_only",
            },
        )
        assert resp.status_code == 423
        detail = resp.json()["detail"]
        assert detail["error"] == "stale_source_sample"
        assert preview_client.s2_id in detail["stale_sample_ids"]  # type: ignore[attr-defined]
        assert "Re-annotate" in detail["message"]
        assert detail["reannotate_url"] == "/api/annotation/{sample_id}"

    def test_stale_source_writes_no_artefacts(self, preview_client: TestClient) -> None:
        """Stale-source 423 must short-circuit before any side-effect.

        Locked as a separate test (not folded into the happy-path
        no-write assertion) so a future refactor that re-orders the
        validation pass surfaces an explicit failure here.
        """
        registry = preview_client.registry  # type: ignore[attr-defined]
        self._mark_stale(preview_client, preview_client.s1_id)  # type: ignore[attr-defined]
        pre_count = _snapshot_samples_row_count(registry)
        pre_files = _snapshot_per_sample_db_files(registry)
        resp = preview_client.post(
            f"/api/individuals/{preview_client.individual_id}/merge/preview",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    preview_client.s1_id,  # type: ignore[attr-defined]
                    preview_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "flag_only",
            },
        )
        assert resp.status_code == 423
        assert _snapshot_samples_row_count(registry) == pre_count
        assert _snapshot_per_sample_db_files(registry) == pre_files


# ══════════════════════════════════════════════════════════════════════
# (iii) Mismatched individual → 422
# ══════════════════════════════════════════════════════════════════════


class TestMismatchedIndividual:
    """Plan §15.1 (iii): a source linked to a different individual → 422."""

    def test_sample_linked_to_other_individual_returns_422(
        self, preview_client: TestClient
    ) -> None:
        registry = preview_client.registry  # type: ignore[attr-defined]

        # Create a second individual and relink S2 to them.
        with registry.reference_engine.begin() as conn:
            result = conn.execute(
                individuals.insert().values(
                    display_name="Other Person",
                    notes="",
                    updated_at=datetime.now(UTC),
                )
            )
            other_id = int(result.inserted_primary_key[0])
            conn.execute(
                samples.update()
                .where(samples.c.id == preview_client.s2_id)  # type: ignore[attr-defined]
                .values(individual_id=other_id)
            )

        # Preview from S1's owner — S2 now belongs elsewhere.
        resp = preview_client.post(
            f"/api/individuals/{preview_client.individual_id}/merge/preview",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    preview_client.s1_id,  # type: ignore[attr-defined]
                    preview_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "flag_only",
            },
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        # Service's message carries the offending sample id and the
        # individual it was actually requested against — both surface
        # verbatim so the wizard can format the user-facing copy.
        assert "not linked to individual" in detail
        assert str(preview_client.s2_id) in detail  # type: ignore[attr-defined]
        assert str(preview_client.individual_id) in detail  # type: ignore[attr-defined]


# ══════════════════════════════════════════════════════════════════════
# (iv) Invalid strategy → 422
# ══════════════════════════════════════════════════════════════════════


class TestInvalidStrategy:
    """Plan §15.1 (iv): pydantic Literal rejection before the service runs."""

    def test_unknown_strategy_returns_422(self, preview_client: TestClient) -> None:
        resp = preview_client.post(
            f"/api/individuals/{preview_client.individual_id}/merge/preview",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    preview_client.s1_id,  # type: ignore[attr-defined]
                    preview_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "bogus_strategy",
            },
        )
        # FastAPI surfaces pydantic Literal failures as 422 with a
        # standard validation-error envelope.
        assert resp.status_code == 422
        body = resp.json()
        # Pydantic's detail is a list of error dicts; the rejected value
        # is on the offending loc.
        assert any(err.get("loc", [])[-1] == "strategy" for err in body["detail"]), body


# ══════════════════════════════════════════════════════════════════════
# (v) Only one source → 422
# ══════════════════════════════════════════════════════════════════════


class TestSingleSource:
    """Plan §15.1 (v): pydantic ``min_length=2`` rejection."""

    def test_single_source_id_returns_422(self, preview_client: TestClient) -> None:
        resp = preview_client.post(
            f"/api/individuals/{preview_client.individual_id}/merge/preview",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [preview_client.s1_id],  # type: ignore[attr-defined]
                "strategy": "flag_only",
            },
        )
        assert resp.status_code == 422
        body = resp.json()
        # Pydantic's loc points at ``source_sample_ids``; the error type
        # is the closed-set ``too_short`` from min_length=2.
        assert any(
            err.get("loc", [])[-1] == "source_sample_ids" and err.get("type", "") == "too_short"
            for err in body["detail"]
        ), body

    def test_three_source_ids_returns_422(self, preview_client: TestClient) -> None:
        """``max_length=2`` is the other end of the pydantic bound — Plan §10.6
        contract names exactly two sources, so a three-id request is rejected
        before the service ever sees it."""
        resp = preview_client.post(
            f"/api/individuals/{preview_client.individual_id}/merge/preview",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    preview_client.s1_id,  # type: ignore[attr-defined]
                    preview_client.s2_id,  # type: ignore[attr-defined]
                    9999,
                ],
                "strategy": "flag_only",
            },
        )
        assert resp.status_code == 422
        body = resp.json()
        assert any(
            err.get("loc", [])[-1] == "source_sample_ids" and err.get("type", "") == "too_long"
            for err in body["detail"]
        ), body


# ══════════════════════════════════════════════════════════════════════
# (vi) Source status != complete → 422 with sample id + status
# ══════════════════════════════════════════════════════════════════════


class TestSourceAnnotationNotComplete:
    """Plan §15.1 (vi): source annotation must be ``complete``.

    The service raises ``InvalidMergeRequestError`` with a message that
    embeds both the offending sample id and its actual status; the route
    surfaces this verbatim as the ``detail`` string of a 422 response.
    The wizard parses both fields back out to render "Sample {id}'s
    annotation is still {status} — wait for it to finish before merging".
    """

    def test_running_status_returns_422_with_sample_id_and_status(
        self, preview_client: TestClient
    ) -> None:
        registry = preview_client.registry  # type: ignore[attr-defined]
        with registry.reference_engine.begin() as conn:
            conn.execute(
                sa.update(jobs)
                .where(jobs.c.sample_id == preview_client.s2_id)  # type: ignore[attr-defined]
                .values(status="running", progress_pct=42.0)
            )

        resp = preview_client.post(
            f"/api/individuals/{preview_client.individual_id}/merge/preview",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    preview_client.s1_id,  # type: ignore[attr-defined]
                    preview_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "flag_only",
            },
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        # Plan §15.1 (vi): "422 with sample id + status in payload".
        assert "not complete" in detail
        assert str(preview_client.s2_id) in detail  # type: ignore[attr-defined]
        assert "running" in detail

    def test_failed_status_returns_422(self, preview_client: TestClient) -> None:
        """Any non-``complete`` status surfaces the same gate."""
        registry = preview_client.registry  # type: ignore[attr-defined]
        with registry.reference_engine.begin() as conn:
            conn.execute(
                sa.update(jobs)
                .where(jobs.c.sample_id == preview_client.s1_id)  # type: ignore[attr-defined]
                .values(status="failed")
            )

        resp = preview_client.post(
            f"/api/individuals/{preview_client.individual_id}/merge/preview",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    preview_client.s1_id,  # type: ignore[attr-defined]
                    preview_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "flag_only",
            },
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "failed" in detail
        assert str(preview_client.s1_id) in detail  # type: ignore[attr-defined]


# ══════════════════════════════════════════════════════════════════════
# (vii) Missing annotation_state row → 423 with reannotate_url
# ══════════════════════════════════════════════════════════════════════


class TestMissingAnnotationStateFallback:
    """Plan §15.1 (vii) / §7.4: missing ``annotation_state`` → ``v1.0.0`` fallback.

    The staleness service (``backend/services/staleness.py``) emits a
    structured ``annotation_state_missing`` warning and treats the
    sample as having been annotated against v1.0.0; the merge service
    then routes the request through the stale-source path. The route
    answers 423 with the ``reannotate_url`` key so the wizard can guide
    the user to the recovery surface — important for backup-restored
    samples from pre-Phase-0 installs whose per-sample DBs predate the
    ``annotation_state`` table.

    The "tiebreaker independent of annotation_state" note from Plan §15.1
    (vii) is a property of the rsid-collapse helper, not the gate — the
    gate is unconditional whenever annotation_state is missing AND the
    installed bundle's major is > v1.
    """

    def _delete_annotation_state(self, preview_client: TestClient, sample_id: int) -> None:
        """Drop the ``annotation_state`` row that ``_seed_source_sample`` wrote.

        Mimics a backup-restored pre-Phase-0 sample whose per-sample DB
        was created before the ``annotation_state`` table existed; the
        Plan §7.4 fallback is the recovery path.
        """
        registry = preview_client.registry  # type: ignore[attr-defined]
        with registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(samples.c.db_path).where(samples.c.id == sample_id)
            ).fetchone()
        assert row is not None
        engine = registry.get_sample_engine(registry.settings.data_dir / row.db_path)
        with engine.begin() as conn:
            conn.execute(
                sa.delete(annotation_state).where(annotation_state.c.key == "vep_bundle_version")
            )

    def test_missing_state_routes_through_stale_source_path(
        self, preview_client: TestClient
    ) -> None:
        self._delete_annotation_state(
            preview_client,
            preview_client.s1_id,  # type: ignore[attr-defined]
        )
        resp = preview_client.post(
            f"/api/individuals/{preview_client.individual_id}/merge/preview",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    preview_client.s1_id,  # type: ignore[attr-defined]
                    preview_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "flag_only",
            },
        )
        assert resp.status_code == 423, resp.text
        detail = resp.json()["detail"]
        # Plan §15.1 (vii) explicit callout: payload carries ``reannotate_url``.
        assert detail["error"] == "stale_source_sample"
        assert preview_client.s1_id in detail["stale_sample_ids"]  # type: ignore[attr-defined]
        assert detail["reannotate_url"] == "/api/annotation/{sample_id}"

    def test_missing_state_writes_no_artefacts(self, preview_client: TestClient) -> None:
        registry = preview_client.registry  # type: ignore[attr-defined]
        self._delete_annotation_state(
            preview_client,
            preview_client.s2_id,  # type: ignore[attr-defined]
        )
        pre_count = _snapshot_samples_row_count(registry)
        pre_files = _snapshot_per_sample_db_files(registry)
        resp = preview_client.post(
            f"/api/individuals/{preview_client.individual_id}/merge/preview",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    preview_client.s1_id,  # type: ignore[attr-defined]
                    preview_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "flag_only",
            },
        )
        assert resp.status_code == 423
        assert _snapshot_samples_row_count(registry) == pre_count
        assert _snapshot_per_sample_db_files(registry) == pre_files
