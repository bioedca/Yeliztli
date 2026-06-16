"""Tests for Huey annotation background task + API routes (P2-05).

Covers:
- T2-05: Background annotation job reports progress via SSE in 10k-variant
  batches, completes without error
- Job creation with duplicate-run guard
- Progress callback updates the jobs table
- Error handling (failed task, missing sample)
- Cancel endpoint
- SSE status endpoint
- API route: POST /api/annotation/{sample_id} returns 202
"""

from __future__ import annotations

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
    clinvar_variants,
    database_versions,
    jobs,
    raw_variants,
    reference_metadata,
    samples,
)
from backend.tasks.huey_tasks import (
    _get_sample_db_path,
    _update_job,
    create_annotation_job,
    run_annotation_task,
)

# ── Seed data ──────────────────────────────────────────────────────────

SEED_RAW_VARIANTS = [
    {"rsid": "rs429358", "chrom": "19", "pos": 44908684, "genotype": "TC"},
    {"rsid": "rs7412", "chrom": "19", "pos": 44908822, "genotype": "CC"},
    {"rsid": "rs1801133", "chrom": "1", "pos": 11856378, "genotype": "AG"},
]

SEED_CLINVAR = [
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
]


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def annotation_env(tmp_data_dir: Path):
    """Set up a complete annotation environment with patched registry.

    Creates reference.db with tables and seed data, a sample DB with
    raw variants, and patches all settings references.
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    # Pre-create reference.db with tables and seed data
    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="abc123",
            )
        )
        conn.execute(clinvar_variants.insert(), SEED_CLINVAR)
    ref_engine.dispose()

    # Create sample DB with raw variants
    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_db_path.parent.mkdir(parents=True, exist_ok=True)
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)
    with sample_engine.begin() as conn:
        conn.execute(raw_variants.insert(), SEED_RAW_VARIANTS)
    sample_engine.dispose()

    with (
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.tasks.huey_tasks.get_settings", return_value=settings),
        patch("backend.main.get_settings", return_value=settings),
    ):
        reset_registry()
        yield {
            "settings": settings,
            "sample_id": 1,
            "tmp_dir": tmp_data_dir,
        }
        reset_registry()


@pytest.fixture
def annotation_client(annotation_env: dict) -> TestClient:
    """FastAPI TestClient wired to the annotation environment."""
    from backend.tasks import huey_tasks

    # Patch huey instance directly (module already loaded at import time)
    original_immediate = huey_tasks.huey.immediate
    huey_tasks.huey.immediate = True
    try:
        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc
    finally:
        huey_tasks.huey.immediate = original_immediate


# ═══════════════════════════════════════════════════════════════════════
# create_annotation_job()
# ═══════════════════════════════════════════════════════════════════════


class TestCreateAnnotationJob:
    def test_creates_job_record(self, annotation_env: dict) -> None:
        """Job record is created with pending status."""
        from backend.db.connection import get_registry

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(jobs).where(jobs.c.job_id == job_id)).fetchone()

        assert row is not None
        assert row.status == "pending"
        assert row.job_type == "annotation"
        assert row.sample_id == sample_id
        assert row.progress_pct == 0.0

    def test_rejects_duplicate_running_job(self, annotation_env: dict) -> None:
        """Cannot create a second annotation job while one is running."""
        sample_id = annotation_env["sample_id"]
        create_annotation_job(sample_id)

        with pytest.raises(ValueError, match="already in progress"):
            create_annotation_job(sample_id)

    def test_allows_new_job_after_completion(self, annotation_env: dict) -> None:
        """Can create a new job after the previous one completed."""
        from backend.db.connection import get_registry

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)

        # Mark first job as complete
        registry = get_registry()
        with registry.reference_engine.begin() as conn:
            conn.execute(jobs.update().where(jobs.c.job_id == job_id).values(status="complete"))

        # Should succeed now
        job_id2 = create_annotation_job(sample_id)
        assert job_id2 != job_id


# ═══════════════════════════════════════════════════════════════════════
# _update_job()
# ═══════════════════════════════════════════════════════════════════════


class TestUpdateJob:
    def test_updates_status_and_progress(self, annotation_env: dict) -> None:
        from backend.db.connection import get_registry

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)

        _update_job(
            job_id,
            status="running",
            progress_pct=50.0,
            message="Halfway there",
        )

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(jobs).where(jobs.c.job_id == job_id)).fetchone()

        assert row.status == "running"
        assert row.progress_pct == 50.0
        assert row.message == "Halfway there"

    def test_updates_error_field(self, annotation_env: dict) -> None:
        from backend.db.connection import get_registry

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)

        _update_job(job_id, status="failed", error="something broke")

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(jobs).where(jobs.c.job_id == job_id)).fetchone()

        assert row.status == "failed"
        assert row.error == "something broke"


# ═══════════════════════════════════════════════════════════════════════
# _get_sample_db_path()
# ═══════════════════════════════════════════════════════════════════════


class TestGetSampleDbPath:
    def test_returns_db_path(self, annotation_env: dict) -> None:
        path = _get_sample_db_path(1)
        assert path == "samples/sample_1.db"

    def test_raises_for_missing_sample(self, annotation_env: dict) -> None:
        with pytest.raises(ValueError, match="Sample 999 not found"):
            _get_sample_db_path(999)


# ═══════════════════════════════════════════════════════════════════════
# run_annotation_task() — synchronous execution
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.slow  # nightly tier: drives the real run_annotation_task E2E per test
class TestRunAnnotationTask:
    def test_task_completes_and_updates_job(self, annotation_env: dict) -> None:
        """Task runs annotation and marks job as complete."""
        from backend.db.connection import get_registry

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)

        # Call task function directly (not through Huey)
        run_annotation_task.call_local(sample_id, job_id)

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(jobs).where(jobs.c.job_id == job_id)).fetchone()

        assert row.status == "complete"
        assert row.progress_pct == 100.0
        assert "Annotated" in row.message

    def test_task_populates_annotated_variants(self, annotation_env: dict) -> None:
        """After task completes, annotated_variants has rows."""
        from backend.db.connection import get_registry

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)
        run_annotation_task.call_local(sample_id, job_id)

        registry = get_registry()
        sample_db = registry.settings.data_dir / "samples" / "sample_1.db"
        sample_engine = registry.get_sample_engine(sample_db)
        with sample_engine.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(annotated_variants)
            ).scalar()

        # At least the ClinVar-matched variant should be annotated
        assert count >= 1

    def test_task_reports_progress(self, annotation_env: dict) -> None:
        """Task updates progress_pct during execution."""

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)

        # Track progress updates
        progress_updates: list[float] = []
        original_update = _update_job

        def tracking_update(jid, *, status, progress_pct=0.0, **kwargs):
            progress_updates.append(progress_pct)
            original_update(jid, status=status, progress_pct=progress_pct, **kwargs)

        with patch("backend.tasks.huey_tasks._update_job", side_effect=tracking_update):
            run_annotation_task.call_local(sample_id, job_id)

        # Should have at least "running" + progress + "complete" updates
        assert len(progress_updates) >= 2
        assert progress_updates[-1] == 100.0

    def test_task_handles_failure(self, annotation_env: dict) -> None:
        """Task marks job as failed when annotation raises."""
        from backend.db.connection import get_registry

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)

        with patch(
            "backend.annotation.engine.run_annotation",
            side_effect=RuntimeError("test error"),
        ):
            run_annotation_task.call_local(sample_id, job_id)

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(jobs).where(jobs.c.job_id == job_id)).fetchone()

        assert row.status == "failed"
        assert "test error" in row.error

    def test_task_handles_missing_sample(self, annotation_env: dict) -> None:
        """Task marks job as failed when sample doesn't exist."""
        # Create job record manually for non-existent sample
        from datetime import UTC, datetime

        from backend.db.connection import get_registry

        registry = get_registry()
        job_id = "test-missing-sample"
        with registry.reference_engine.begin() as conn:
            conn.execute(
                jobs.insert().values(
                    job_id=job_id,
                    sample_id=999,
                    job_type="annotation",
                    status="pending",
                    progress_pct=0.0,
                    message="Test",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )

        run_annotation_task.call_local(999, job_id)

        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(jobs).where(jobs.c.job_id == job_id)).fetchone()

        assert row.status == "failed"
        assert "not found" in row.error

    def test_task_respects_cancellation(self, annotation_env: dict) -> None:
        """Task stops and preserves cancelled status when cancelled mid-run."""
        from backend.db.connection import get_registry

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)

        # Mark job as cancelled before the progress callback fires
        with patch("backend.tasks.huey_tasks._is_job_cancelled", return_value=True):
            run_annotation_task.call_local(sample_id, job_id)

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(jobs).where(jobs.c.job_id == job_id)).fetchone()

        # Should remain "cancelled", not overwritten to "complete"
        assert row.status in ("cancelled", "running")


# ═══════════════════════════════════════════════════════════════════════
# Step 10 (Plan §7.3) — deferred annotation_state upsert
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.slow  # nightly tier: runs real annotation to exercise the state gate
class TestAnnotationStateGate:
    """Plan §7.3 — both reserved kv keys are written iff run_all_analyses succeeds.

    Locks the staleness-gate contract:
      * Success path → both ``vep_bundle_version`` and
        ``annotation_bundle_coverage_json`` land in one transaction.
      * Raise from ``run_all_analyses`` → ``annotation_state`` is left
        untouched, so the gate stays up and the user can retry.
    """

    def _read_state(self, annotation_env: dict) -> dict[str, str]:
        from backend.db.connection import get_registry

        registry = get_registry()
        sample_db = registry.settings.data_dir / "samples" / "sample_1.db"
        sample_engine = registry.get_sample_engine(sample_db)
        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(annotation_state.c.key, annotation_state.c.value)
            ).fetchall()
        return {r.key: r.value for r in rows}

    def _seed_bundle_version(self, annotation_env: dict, version: str) -> None:
        """Seed reference.db so the engine telemetry surfaces a known bundle version."""
        from datetime import UTC, datetime

        from backend.db.connection import get_registry

        registry = get_registry()
        with registry.reference_engine.begin() as conn:
            conn.execute(
                database_versions.insert().values(
                    db_name="vep_bundle",
                    version=version,
                    downloaded_at=datetime.now(UTC),
                )
            )

    def test_success_path_lifts_gate(self, annotation_env: dict) -> None:
        """Happy path: both reserved keys are upserted on the success path."""
        self._seed_bundle_version(annotation_env, "v2.0.0")

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)
        run_annotation_task.call_local(sample_id, job_id)

        state = self._read_state(annotation_env)
        assert state.get("vep_bundle_version") == "v2.0.0"
        coverage_json = state.get("annotation_bundle_coverage_json")
        assert coverage_json is not None
        import json as _json

        coverage = _json.loads(coverage_json)
        assert coverage["bundle_version"] == "v2.0.0"
        assert coverage["total_variants"] == len(SEED_RAW_VARIANTS)
        # Plan §5.6 — unmerged sample → single-key by_source with counts that
        # sum to the top-level rollup. Vendor derivation is exercised in
        # tests/backend/test_annotation_engine.py; here we only lock the shape.
        assert isinstance(coverage["by_source"], dict)
        assert len(coverage["by_source"]) == 1
        only_source = next(iter(coverage["by_source"].values()))
        assert only_source["vep_bundle_rsid_hits"] == coverage["vep_bundle_rsid_hits"]
        assert (
            only_source["vep_bundle_coord_fallback_hits"]
            == coverage["vep_bundle_coord_fallback_hits"]
        )
        assert only_source["vep_misses"] == coverage["vep_misses"]

    def test_missing_bundle_row_falls_back_to_v1(self, annotation_env: dict) -> None:
        """Defensive fallback when database_versions has no vep_bundle row."""
        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)
        run_annotation_task.call_local(sample_id, job_id)

        state = self._read_state(annotation_env)
        # Plan §7.3 — value is the installed_version (None → "v1.0.0" fallback).
        assert state.get("vep_bundle_version") == "v1.0.0"
        assert "annotation_bundle_coverage_json" in state

    def test_raise_from_run_all_analyses_leaves_gate_up(self, annotation_env: dict) -> None:
        """A raise from run_all_analyses bypasses the upsert (gate stays up)."""
        from backend.db.connection import get_registry

        self._seed_bundle_version(annotation_env, "v2.0.0")

        # Pre-seed annotation_state with a stale value; the failing run must
        # leave it untouched so is_sample_stale() still returns True.
        registry = get_registry()
        sample_db = registry.settings.data_dir / "samples" / "sample_1.db"
        sample_engine = registry.get_sample_engine(sample_db)
        with sample_engine.begin() as conn:
            conn.execute(
                annotation_state.insert().values(
                    key="vep_bundle_version",
                    value="v1.0.0",
                )
            )

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)

        with patch(
            "backend.analysis.run_all.run_all_analyses",
            side_effect=RuntimeError("analysis exploded"),
        ):
            run_annotation_task.call_local(sample_id, job_id)

        state = self._read_state(annotation_env)
        # Pre-existing row preserved; no fresh upsert fired.
        assert state.get("vep_bundle_version") == "v1.0.0"
        assert "annotation_bundle_coverage_json" not in state

        # Job itself still marks complete — analysis is best-effort (Plan §7.3).
        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(jobs).where(jobs.c.job_id == job_id)).fetchone()
        assert row.status == "complete"

    def test_two_phase_sse_progress_messages(self, annotation_env: dict) -> None:
        """SSE emits a two-phase progress arc: 'Annotating…' → 'Analyzing…'."""
        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)

        messages: list[str] = []
        from backend.tasks.huey_tasks import _update_job as _real_update_job

        def capture(jid, *, status, progress_pct=0.0, message="", **kwargs):
            messages.append(message)
            _real_update_job(
                jid, status=status, progress_pct=progress_pct, message=message, **kwargs
            )

        with patch("backend.tasks.huey_tasks._update_job", side_effect=capture):
            run_annotation_task.call_local(sample_id, job_id)

        assert "Annotating…" in messages
        assert "Analyzing…" in messages
        assert messages.index("Annotating…") < messages.index("Analyzing…")


# ═══════════════════════════════════════════════════════════════════════
# T2-05: Integration — SSE progress streaming
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.slow  # nightly tier: drives real annotation + SSE streaming
class TestAnnotationSSEIntegration:
    """T2-05: Background annotation job reports progress via SSE."""

    def test_sse_reports_complete(self, annotation_env: dict) -> None:
        """SSE stream reports complete status after annotation finishes."""
        from backend.api.sse import get_job_progress
        from backend.db.connection import get_registry

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)
        run_annotation_task.call_local(sample_id, job_id)

        registry = get_registry()
        status = get_job_progress(registry.reference_engine, job_id)

        assert status is not None
        assert status.status == "complete"
        assert status.progress_pct == 100.0
        assert "Annotated" in status.message

    def test_sse_reports_failure(self, annotation_env: dict) -> None:
        """SSE stream reports failed status when task errors."""
        from backend.api.sse import get_job_progress
        from backend.db.connection import get_registry

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)

        with patch(
            "backend.annotation.engine.run_annotation",
            side_effect=RuntimeError("boom"),
        ):
            run_annotation_task.call_local(sample_id, job_id)

        registry = get_registry()
        status = get_job_progress(registry.reference_engine, job_id)

        assert status is not None
        assert status.status == "failed"
        assert "boom" in status.error


# ═══════════════════════════════════════════════════════════════════════
# API routes
# ═══════════════════════════════════════════════════════════════════════


class TestAnnotationAPI:
    def test_start_annotation_returns_202(
        self, annotation_client: TestClient, annotation_env: dict
    ) -> None:
        """POST /api/annotation/{sample_id} returns 202 with job_id."""
        with patch("backend.api.routes.annotation.run_annotation_task") as mock_run:
            resp = annotation_client.post("/api/annotation/1")

        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["sample_id"] == 1
        assert data["status"] == "pending"
        mock_run.assert_called_once_with(1, data["job_id"])

    def test_start_annotation_duplicate_returns_409(
        self, annotation_client: TestClient, annotation_env: dict
    ) -> None:
        """POST /api/annotation/{sample_id} returns 409 if already running."""
        with patch("backend.api.routes.annotation.run_annotation_task") as mock_run:
            resp1 = annotation_client.post("/api/annotation/1")
            assert resp1.status_code == 202
            job_id = resp1.json()["job_id"]

            resp2 = annotation_client.post("/api/annotation/1")
            assert resp2.status_code == 409

        mock_run.assert_called_once_with(1, job_id)

    def test_status_endpoint_returns_sse(
        self, annotation_client: TestClient, annotation_env: dict
    ) -> None:
        """GET /api/annotation/status/{job_id} returns SSE content type."""
        # Create a completed job first
        from datetime import UTC, datetime

        from backend.db.connection import get_registry

        registry = get_registry()
        job_id = "test-sse-job"
        with registry.reference_engine.begin() as conn:
            conn.execute(
                jobs.insert().values(
                    job_id=job_id,
                    sample_id=1,
                    job_type="annotation",
                    status="complete",
                    progress_pct=100.0,
                    message="Done",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )

        resp = annotation_client.get(f"/api/annotation/status/{job_id}")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        assert "complete" in resp.text

    def test_cancel_annotation(self, annotation_client: TestClient, annotation_env: dict) -> None:
        """POST /api/annotation/cancel/{job_id} cancels a running job."""
        with patch("backend.api.routes.annotation.run_annotation_task") as mock_run:
            resp = annotation_client.post("/api/annotation/1")
            job_id = resp.json()["job_id"]

        mock_run.assert_called_once_with(1, job_id)

        resp = annotation_client.post(f"/api/annotation/cancel/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cancel_nonexistent_returns_404(
        self, annotation_client: TestClient, annotation_env: dict
    ) -> None:
        """POST /api/annotation/cancel/{job_id} returns 404 for unknown job."""
        resp = annotation_client.post("/api/annotation/cancel/nonexistent")
        assert resp.status_code == 404

    def test_cancel_completed_returns_409(
        self, annotation_client: TestClient, annotation_env: dict
    ) -> None:
        """POST /api/annotation/cancel/{job_id} returns 409 for terminal job."""
        from datetime import UTC, datetime

        from backend.db.connection import get_registry

        registry = get_registry()
        with registry.reference_engine.begin() as conn:
            conn.execute(
                jobs.insert().values(
                    job_id="done-job",
                    sample_id=1,
                    job_type="annotation",
                    status="complete",
                    progress_pct=100.0,
                    message="Done",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )

        resp = annotation_client.post("/api/annotation/cancel/done-job")
        assert resp.status_code == 409
