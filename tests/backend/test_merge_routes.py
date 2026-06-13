"""Tests for the Step 68 / MRG-04 merge + concordance routes (Plan §10.6).

Three routes ship in Step 68 alongside the preview route added in Step 67:

* ``POST /api/individuals/{id}/merge`` — commits a merge and surfaces
  ``{merged_sample_id, job_id}``. Shares the §10.5 validation pipeline
  with the preview route, so the route-level assertions here focus on the
  commit-only surface: 404 / 422 / 423 plumbing and that the response
  carries a non-empty ``merged_sample_id`` plus a ``job_id`` string that
  matches the ``jobs`` row written by the service's enqueue.
* ``GET /api/samples/{sample_id}/merge-provenance`` — returns the
  ``merge_provenance`` row Plan §10.4 (c) writes on the merged sample,
  with the three JSON columns decoded; 404 when the sample exists but
  carries no provenance row (i.e. unmerged).
* ``GET /api/samples/{sample_id}/concordance-report?limit=N&offset=K`` —
  paginated discordant-loci report with gene context from the LEFT-JOIN
  against ``annotated_variants``. Default limit 50; max 500 (422 on 501);
  ordered by ``(chrom, pos)``; ``total_discordant`` independent of the
  page window.

The Step 12 drift guard verifies that the two new ``samples`` subroutes
declare ``Depends(require_fresh_sample)``; this file only asserts that
the gate fires when the sample is stale (HTTP 423 with the Plan §7.5
payload) so the wiring carries through to a runtime test client.

The Huey enqueue at the tail of ``merge_samples`` is no-op'd via a
``monkeypatch`` on ``backend.tasks.huey_tasks.create_annotation_job`` so
the test never actually fires the annotation pipeline — the route's
``job_id`` field is populated by reading the row the no-op writes back
through the merge service's local import.
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
    annotated_variants,
    annotation_state,
    database_versions,
    individuals,
    jobs,
    raw_variants,
    reference_metadata,
    samples,
)

# ── Variant batches mirroring test_sample_merge.py ─────────────────────


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


# rs400 / rs600 carry annotation rows so the LEFT-JOIN in the concordance
# report surfaces gene + consequence + ClinVar context. rs500 (unique to
# S1, not discordant) is intentionally omitted to confirm the JOIN tolerates
# missing annotation rows for non-discordant loci without affecting the
# discordant page.
_ANNOTATIONS = [
    {
        "rsid": "rs400",
        "chrom": "1",
        "pos": 400,
        "gene_symbol": "FOO1",
        "consequence": "missense_variant",
        "clinvar_significance": "Likely_pathogenic",
    },
]


# ── Fixtures ───────────────────────────────────────────────────────────


def _noop_annotation_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub Huey so merge_samples writes a deterministic ``jobs`` row +
    bootstraps the merged sample's ``annotation_state`` to the installed
    bundle version.

    The real ``create_annotation_job`` writes into ``jobs`` with a fresh
    UUID; the real ``run_annotation_task`` upserts
    ``annotation_state.vep_bundle_version`` (Plan §7.4) after the
    annotation cascade completes. Stubbing both lets the route tests
    exercise the gated reads without firing the actual pipeline, while
    still ending up with a non-stale merged sample.
    """
    import backend.tasks.huey_tasks as huey_tasks

    def _stub_create_annotation_job(sample_id: int) -> str:
        from backend.db.connection import get_registry

        registry = get_registry()
        job_id = f"job-merged-{sample_id}"
        with registry.reference_engine.begin() as conn:
            conn.execute(
                jobs.insert().values(
                    job_id=job_id,
                    sample_id=sample_id,
                    job_type="annotation",
                    status="queued",
                    progress_pct=0.0,
                    message="",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
        return job_id

    def _stub_run_annotation_task(sample_id: int, _job_id: str) -> None:
        from backend.db.connection import get_registry

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(samples.c.db_path).where(samples.c.id == sample_id)
            ).fetchone()
        if row is None:
            return
        engine = registry.get_sample_engine(registry.settings.data_dir / row.db_path)
        with engine.begin() as conn:
            conn.execute(
                annotation_state.insert().values(
                    key="vep_bundle_version",
                    value="v2.0.0",
                    updated_at=datetime.now(UTC),
                )
            )
            # Mirror the real engine: write annotated_variants for the
            # merged sample's loci so the concordance report's LEFT-JOIN
            # surfaces gene context.
            for ann in _ANNOTATIONS:
                conn.execute(annotated_variants.insert().values(**ann))

    monkeypatch.setattr(huey_tasks, "create_annotation_job", _stub_create_annotation_job)
    monkeypatch.setattr(huey_tasks, "run_annotation_task", _stub_run_annotation_task)


def _seed_source_sample(
    registry,
    *,
    individual_id: int | None,
    name: str,
    file_format: str,
    file_hash: str,
    variants: list[dict],
    annotations: list[dict] | None = None,
    bundle_version: str = "v2.0.0",
    annotation_status: str = "complete",
) -> int:
    """Create one source sample (reference row + per-sample DB + jobs row + raw_variants)."""
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
        if annotations:
            conn.execute(annotated_variants.insert(), annotations)
        conn.execute(
            annotation_state.insert().values(
                key="vep_bundle_version",
                value=bundle_version,
                updated_at=now,
            )
        )
    return sample_id


def _seed_installed_vep_bundle(reference_engine, version: str = "v2.0.0") -> None:
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


@pytest.fixture
def merge_client(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """TestClient backed by a temp data dir + pre-seeded merge fixture.

    Stashes onto the client:

    * ``individual_id`` — the owning individual.
    * ``s1_id`` / ``s2_id`` — two source samples linked to the individual,
      pre-seeded with the §10.2-bucket variants from ``test_sample_merge``.
    * ``settings`` — the temp-dir Settings (tests use it to seed extra
      state and rebuild the registry path).
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

        # Create individual + two source samples.
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
            annotations=_ANNOTATIONS,
        )
        s2_id = _seed_source_sample(
            registry,
            individual_id=individual_id,
            name="jane_ancestrydna.txt",
            file_format="ancestrydna_v2.0",
            file_hash="hash_s2",
            variants=S2_VARIANTS,
            annotations=_ANNOTATIONS,
        )

        _noop_annotation_enqueue(monkeypatch)

        app = create_app()
        with TestClient(app) as tc:
            tc.individual_id = individual_id  # type: ignore[attr-defined]
            tc.s1_id = s1_id  # type: ignore[attr-defined]
            tc.s2_id = s2_id  # type: ignore[attr-defined]
            tc.settings = settings  # type: ignore[attr-defined]
            yield tc

        reset_registry()


# ── POST /api/individuals/{id}/merge ───────────────────────────────────


class TestMergeCommitRoute:
    """Plan §10.6 commit route: ``POST /api/individuals/{id}/merge``."""

    def test_happy_path_returns_201_with_merged_sample_id_and_job_id(
        self, merge_client: TestClient
    ) -> None:
        resp = merge_client.post(
            f"/api/individuals/{merge_client.individual_id}/merge",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [merge_client.s1_id, merge_client.s2_id],  # type: ignore[attr-defined]
                "strategy": "flag_only",
                "display_name": "Jane Doe (merged)",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["merged_sample_id"] not in (
            merge_client.s1_id,  # type: ignore[attr-defined]
            merge_client.s2_id,  # type: ignore[attr-defined]
        )
        assert body["job_id"] == f"job-merged-{body['merged_sample_id']}"

    def test_nonexistent_individual_returns_404(self, merge_client: TestClient) -> None:
        resp = merge_client.post(
            "/api/individuals/9999/merge",
            json={
                "source_sample_ids": [
                    merge_client.s1_id,  # type: ignore[attr-defined]
                    merge_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "flag_only",
                "display_name": "Jane Doe (merged)",
            },
        )
        assert resp.status_code == 404
        assert "9999" in resp.json()["detail"]

    def test_invalid_strategy_returns_422(self, merge_client: TestClient) -> None:
        resp = merge_client.post(
            f"/api/individuals/{merge_client.individual_id}/merge",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    merge_client.s1_id,  # type: ignore[attr-defined]
                    merge_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "bogus",
                "display_name": "Jane Doe (merged)",
            },
        )
        assert resp.status_code == 422

    def test_missing_display_name_returns_422(self, merge_client: TestClient) -> None:
        resp = merge_client.post(
            f"/api/individuals/{merge_client.individual_id}/merge",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    merge_client.s1_id,  # type: ignore[attr-defined]
                    merge_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "flag_only",
                "display_name": "",
            },
        )
        # Pydantic ``min_length=1`` rejects the empty string before the
        # service runs.
        assert resp.status_code == 422

    def test_unlinked_samples_return_422(self, merge_client: TestClient) -> None:
        # Unlink one sample then attempt the merge. The §10.5 step-1
        # membership check fires.
        resp = merge_client.post(
            f"/api/individuals/{merge_client.individual_id}/unlink-sample",  # type: ignore[attr-defined]
            json={"sample_id": merge_client.s1_id},  # type: ignore[attr-defined]
        )
        assert resp.status_code == 200

        resp = merge_client.post(
            f"/api/individuals/{merge_client.individual_id}/merge",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    merge_client.s1_id,  # type: ignore[attr-defined]
                    merge_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "flag_only",
                "display_name": "Jane Doe (merged)",
            },
        )
        assert resp.status_code == 422
        assert "not linked" in resp.json()["detail"]

    def test_stale_source_returns_423(
        self, merge_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Mark s1 stale by rewriting its annotation_state.
        from backend.db.connection import get_registry

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(samples.c.db_path).where(
                    samples.c.id == merge_client.s1_id  # type: ignore[attr-defined]
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

        resp = merge_client.post(
            f"/api/individuals/{merge_client.individual_id}/merge",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    merge_client.s1_id,  # type: ignore[attr-defined]
                    merge_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "flag_only",
                "display_name": "Jane Doe (merged)",
            },
        )
        assert resp.status_code == 423
        detail = resp.json()["detail"]
        assert detail["error"] == "stale_source_sample"
        assert merge_client.s1_id in detail["stale_sample_ids"]  # type: ignore[attr-defined]


# ── GET /api/samples/{id}/merge-provenance ─────────────────────────────


class TestMergeProvenanceRoute:
    """Plan §10.6 merge-provenance read route."""

    def _commit_merge(self, merge_client: TestClient, strategy: str = "flag_only") -> int:
        resp = merge_client.post(
            f"/api/individuals/{merge_client.individual_id}/merge",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    merge_client.s1_id,  # type: ignore[attr-defined]
                    merge_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": strategy,
                "display_name": "Jane Doe (merged)",
            },
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["merged_sample_id"]

    def test_returns_provenance_for_merged_sample(self, merge_client: TestClient) -> None:
        merged_id = self._commit_merge(merge_client)

        resp = merge_client.get(f"/api/samples/{merged_id}/merge-provenance")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["strategy"] == "flag_only"
        assert body["source_sample_ids"] == [
            merge_client.s1_id,  # type: ignore[attr-defined]
            merge_client.s2_id,  # type: ignore[attr-defined]
        ]
        assert body["source_file_hashes"] == ["hash_s1", "hash_s2"]
        # Plan §10.4 (c) summary keys.
        summary = body["concordance_summary"]
        assert set(summary.keys()) >= {
            "match",
            "filled_nocall",
            "discordant",
            "unique_S1",
            "unique_S2",
            "collapsed_rsid",
        }
        # Bucket counts from the fixture.
        assert summary == {
            "match": 2,
            "filled_nocall": 2,
            "discordant": 1,
            "unique_S1": 1,
            "unique_S2": 1,
            "collapsed_rsid": 1,
        }

    def test_unmerged_sample_returns_404(self, merge_client: TestClient) -> None:
        resp = merge_client.get(
            f"/api/samples/{merge_client.s1_id}/merge-provenance"  # type: ignore[attr-defined]
        )
        assert resp.status_code == 404
        assert "no merge provenance" in resp.json()["detail"]

    def test_nonexistent_sample_returns_404(self, merge_client: TestClient) -> None:
        # #453 — ``Depends(require_fresh_sample)`` checks existence before
        # staleness, so a missing ``samples`` row answers 404 deterministically
        # (not 423 by bundle state), uniformly across analysis and merge/migrate
        # routes. Supersedes the prior "423 to avoid leaking existence" contract:
        # a single-principal, self-hosted app leaks nothing cross-user.
        resp = merge_client.get("/api/samples/9999/merge-provenance")
        assert resp.status_code == 404


# ── GET /api/samples/{id}/concordance-report ───────────────────────────


class TestConcordanceReportRoute:
    """Plan §10.6 concordance-report route with pagination."""

    def _commit_merge(self, merge_client: TestClient) -> int:
        resp = merge_client.post(
            f"/api/individuals/{merge_client.individual_id}/merge",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    merge_client.s1_id,  # type: ignore[attr-defined]
                    merge_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "flag_only",
                "display_name": "Jane Doe (merged)",
            },
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["merged_sample_id"]

    def test_default_page_returns_all_discordant_rows_with_gene_context(
        self, merge_client: TestClient
    ) -> None:
        merged_id = self._commit_merge(merge_client)

        resp = merge_client.get(f"/api/samples/{merged_id}/concordance-report")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Plan §10.6 default limit.
        assert body["limit"] == 50
        assert body["offset"] == 0
        # Fixture has exactly one discordant locus (rs400).
        assert body["total_discordant"] == 1
        assert len(body["discordant_loci"]) == 1
        locus = body["discordant_loci"][0]
        assert locus["rsid"] == "rs400"
        assert locus["chrom"] == "1"
        assert locus["pos"] == 400
        # flag_only writes the no-call sentinel + paired alt encoding.
        assert locus["genotype"] == "??"
        assert locus["discordant_alt_genotype"] == "S1=AA;S2=GG"
        # Gene context joined from annotated_variants (seeded above).
        assert locus["gene_symbol"] == "FOO1"
        assert locus["consequence"] == "missense_variant"
        assert locus["clinvar_significance"] == "Likely_pathogenic"

    def test_summary_field_matches_provenance(self, merge_client: TestClient) -> None:
        merged_id = self._commit_merge(merge_client)
        report = merge_client.get(f"/api/samples/{merged_id}/concordance-report").json()
        prov = merge_client.get(f"/api/samples/{merged_id}/merge-provenance").json()
        assert report["concordance_summary"] == prov["concordance_summary"]

    def test_explicit_limit_and_offset_paginate(self, merge_client: TestClient) -> None:
        merged_id = self._commit_merge(merge_client)
        # offset past the only discordant row → empty page, total still 1.
        resp = merge_client.get(f"/api/samples/{merged_id}/concordance-report?limit=10&offset=10")
        assert resp.status_code == 200
        body = resp.json()
        assert body["limit"] == 10
        assert body["offset"] == 10
        assert body["total_discordant"] == 1
        assert body["discordant_loci"] == []

    def test_limit_above_max_returns_422(self, merge_client: TestClient) -> None:
        merged_id = self._commit_merge(merge_client)
        resp = merge_client.get(f"/api/samples/{merged_id}/concordance-report?limit=501")
        assert resp.status_code == 422

    def test_limit_below_min_returns_422(self, merge_client: TestClient) -> None:
        merged_id = self._commit_merge(merge_client)
        resp = merge_client.get(f"/api/samples/{merged_id}/concordance-report?limit=0")
        assert resp.status_code == 422

    def test_negative_offset_returns_422(self, merge_client: TestClient) -> None:
        merged_id = self._commit_merge(merge_client)
        resp = merge_client.get(f"/api/samples/{merged_id}/concordance-report?offset=-1")
        assert resp.status_code == 422

    def test_unmerged_sample_returns_404(self, merge_client: TestClient) -> None:
        resp = merge_client.get(
            f"/api/samples/{merge_client.s1_id}/concordance-report"  # type: ignore[attr-defined]
        )
        assert resp.status_code == 404

    def test_nonexistent_sample_returns_404(self, merge_client: TestClient) -> None:
        # See ``TestMergeProvenanceRoute.test_nonexistent_sample_returns_404``
        # for the rationale — the existence-first gate answers 404 (#453).
        resp = merge_client.get("/api/samples/9999/concordance-report")
        assert resp.status_code == 404


# ── require_fresh_sample wiring on the new read routes ─────────────────


class TestReadRoutesGated:
    """Plan §7.5: both new read routes declare ``Depends(require_fresh_sample)``.

    The Step 12 drift guard locks the static declaration; this asserts the
    gate actually fires when the merged sample is stale and that the 423
    payload carries the Plan §7.5 keys so a frontend banner can render.
    """

    def _commit_and_mark_stale(self, merge_client: TestClient) -> int:
        from backend.db.connection import get_registry

        resp = merge_client.post(
            f"/api/individuals/{merge_client.individual_id}/merge",  # type: ignore[attr-defined]
            json={
                "source_sample_ids": [
                    merge_client.s1_id,  # type: ignore[attr-defined]
                    merge_client.s2_id,  # type: ignore[attr-defined]
                ],
                "strategy": "flag_only",
                "display_name": "Jane Doe (merged)",
            },
        )
        assert resp.status_code == 201, resp.text
        merged_id = resp.json()["merged_sample_id"]

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(samples.c.db_path).where(samples.c.id == merged_id)
            ).fetchone()
        assert row is not None
        engine = registry.get_sample_engine(registry.settings.data_dir / row.db_path)
        # Service's enqueue is no-op'd so annotation_state is left at v2.0.0
        # by the schema bootstrap — we write a v1 row to force a major
        # downgrade against the v2 bundle.
        with engine.begin() as conn:
            existing = conn.execute(
                sa.select(annotation_state.c.value).where(
                    annotation_state.c.key == "vep_bundle_version"
                )
            ).fetchone()
            if existing is None:
                conn.execute(
                    annotation_state.insert().values(
                        key="vep_bundle_version",
                        value="v1.0.0",
                        updated_at=datetime.now(UTC),
                    )
                )
            else:
                conn.execute(
                    sa.update(annotation_state)
                    .where(annotation_state.c.key == "vep_bundle_version")
                    .values(value="v1.0.0")
                )
        return merged_id

    def test_merge_provenance_blocks_stale_sample_with_423(self, merge_client: TestClient) -> None:
        merged_id = self._commit_and_mark_stale(merge_client)
        resp = merge_client.get(f"/api/samples/{merged_id}/merge-provenance")
        assert resp.status_code == 423
        detail = resp.json()["detail"]
        assert {
            "installed_version",
            "required_version",
            "update_url",
            "reannotate_url",
        } <= set(detail.keys())

    def test_concordance_report_blocks_stale_sample_with_423(
        self, merge_client: TestClient
    ) -> None:
        merged_id = self._commit_and_mark_stale(merge_client)
        resp = merge_client.get(f"/api/samples/{merged_id}/concordance-report")
        assert resp.status_code == 423
