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

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from huey.signals import SIGNAL_INTERRUPTED

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.download_manager import HUEY_DOWNLOAD_JOB_TYPE
from backend.db.sample_schema import (
    CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY,
    create_sample_tables,
)
from backend.db.tables import (
    annotated_variants,
    annotation_state,
    clinvar_variants,
    database_versions,
    findings,
    jobs,
    raw_variants,
    reference_metadata,
    samples,
)
from backend.services.sample_operation_lock import SAMPLE_EXPORT_JOB_TYPE
from backend.tasks.huey_tasks import (
    HUEY_PENDING_ENQUEUE_GRACE,
    AnnotationEnqueueError,
    _finalize_annotation_job,
    _get_sample_db_path,
    _recover_jobs_on_worker_startup,
    _update_job,
    _worker_job_from_task,
    create_annotation_job,
    enqueue_annotation_job,
    huey,
    recover_orphaned_jobs,
    recover_worker_orphaned_jobs,
    run_annotation_task,
    run_backup_export_task,
    run_database_update_task,
    run_lai_task,
    run_update_check_task,
)
from tests.backend.vep_bundle_test_utils import seed_embedded_vep_bundle_version

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

    def test_rejects_annotation_while_export_lease_is_held(
        self,
        annotation_env: dict,
    ) -> None:
        from backend.db.connection import get_registry
        from backend.services.sample_operation_lock import sample_export_lease

        sample_id = annotation_env["sample_id"]
        registry = get_registry()

        with sample_export_lease(
            registry.reference_engine,
            sample_id,
            operation="test export",
        ):
            with pytest.raises(ValueError, match="export is in progress"):
                create_annotation_job(sample_id)

        job_id = create_annotation_job(sample_id)
        assert job_id


class TestRecoverOrphanedJobs:
    def test_recovers_api_exports_without_releasing_live_huey_jobs(
        self,
        annotation_env: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from backend.db.connection import get_registry
        from backend.tasks.huey_tasks import huey

        sample_id = annotation_env["sample_id"]
        annotation_job_id = create_annotation_job(sample_id)
        registry = get_registry()
        monkeypatch.setattr(huey, "immediate", False)
        with registry.reference_engine.begin() as conn:
            conn.execute(
                jobs.insert(),
                [
                    {
                        "job_id": "api-export-lease",
                        "sample_id": sample_id,
                        "job_type": SAMPLE_EXPORT_JOB_TYPE,
                        "status": "running",
                    },
                    {
                        "job_id": "api-database-download",
                        "sample_id": None,
                        "job_type": "database_download",
                        "status": "pending",
                    },
                    {
                        "job_id": "huey-lai",
                        "sample_id": sample_id,
                        "job_type": "lai_analysis",
                        "status": "running",
                    },
                    {
                        "job_id": "api-download",
                        "sample_id": None,
                        "job_type": "download",
                        "status": "running",
                    },
                    {
                        "job_id": "huey-download",
                        "sample_id": None,
                        "job_type": HUEY_DOWNLOAD_JOB_TYPE,
                        "status": "running",
                    },
                ],
            )

        assert recover_orphaned_jobs(registry.reference_engine) == 3

        with registry.reference_engine.connect() as conn:
            statuses = dict(
                conn.execute(
                    sa.select(jobs.c.job_id, jobs.c.status).where(
                        jobs.c.job_id.in_(
                            (
                                annotation_job_id,
                                "api-export-lease",
                                "api-database-download",
                                "huey-lai",
                                "api-download",
                                "huey-download",
                            )
                        )
                    )
                ).all()
            )
        assert statuses == {
            annotation_job_id: "pending",
            "api-export-lease": "failed",
            "api-database-download": "failed",
            "huey-lai": "running",
            "api-download": "failed",
            "huey-download": "running",
        }

    def test_immediate_mode_recovers_huey_jobs_with_the_api_process(
        self,
        annotation_env: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from backend.db.connection import get_registry
        from backend.tasks.huey_tasks import huey

        sample_id = annotation_env["sample_id"]
        registry = get_registry()
        monkeypatch.setattr(huey, "immediate", True)
        with registry.reference_engine.begin() as conn:
            conn.execute(
                jobs.insert(),
                [
                    {
                        "job_id": "immediate-annotation",
                        "sample_id": sample_id,
                        "job_type": "annotation",
                        "status": "running",
                    },
                    {
                        "job_id": "immediate-lai",
                        "sample_id": sample_id,
                        "job_type": "lai_analysis",
                        "status": "pending",
                    },
                    {
                        "job_id": "immediate-download",
                        "sample_id": None,
                        "job_type": HUEY_DOWNLOAD_JOB_TYPE,
                        "status": "cancelling",
                    },
                    {
                        "job_id": "unowned-job",
                        "sample_id": None,
                        "job_type": "external",
                        "status": "running",
                    },
                ],
            )

        assert recover_orphaned_jobs(registry.reference_engine) == 3

        with registry.reference_engine.connect() as conn:
            statuses = dict(
                conn.execute(
                    sa.select(jobs.c.job_id, jobs.c.status).where(
                        jobs.c.job_id.in_(
                            (
                                "immediate-annotation",
                                "immediate-lai",
                                "immediate-download",
                                "unowned-job",
                            )
                        )
                    )
                ).all()
            )
        assert statuses == {
            "immediate-annotation": "failed",
            "immediate-lai": "failed",
            "immediate-download": "failed",
            "unowned-job": "running",
        }

    def test_worker_startup_reconciles_every_proven_huey_orphan(
        self,
        annotation_env: dict,
    ) -> None:
        from backend.db.connection import get_registry

        sample_id = annotation_env["sample_id"]
        recovery_time = datetime.now(UTC)
        stale = recovery_time - HUEY_PENDING_ENQUEUE_GRACE - timedelta(seconds=1)
        fresh = recovery_time - HUEY_PENDING_ENQUEUE_GRACE + timedelta(seconds=1)
        registry = get_registry()
        with registry.reference_engine.begin() as conn:
            conn.execute(
                jobs.insert(),
                [
                    {
                        "job_id": "queued-pending",
                        "sample_id": sample_id,
                        "job_type": "annotation",
                        "status": "pending",
                        "created_at": stale,
                        "updated_at": stale,
                    },
                    {
                        "job_id": "orphaned-lai-pending",
                        "sample_id": sample_id,
                        "job_type": "lai_analysis",
                        "status": "pending",
                        "created_at": stale,
                        "updated_at": stale,
                    },
                    {
                        "job_id": "fresh-pending",
                        "sample_id": None,
                        "job_type": "update_check",
                        "status": "pending",
                        "created_at": fresh,
                        "updated_at": fresh,
                    },
                    {
                        "job_id": "orphaned-database-update",
                        "sample_id": None,
                        "job_type": "database_update",
                        "status": "running",
                        "created_at": stale,
                        "updated_at": stale,
                    },
                    {
                        "job_id": "orphaned-backup",
                        "sample_id": None,
                        "job_type": "backup_export",
                        "status": "running",
                        "created_at": stale,
                        "updated_at": stale,
                    },
                    {
                        "job_id": "orphaned-huey-download",
                        "sample_id": None,
                        "job_type": HUEY_DOWNLOAD_JOB_TYPE,
                        "status": "running",
                        "created_at": stale,
                        "updated_at": stale,
                    },
                    {
                        "job_id": "orphaned-cancelling",
                        "sample_id": sample_id,
                        "job_type": "annotation",
                        "status": "cancelling",
                        "created_at": stale,
                        "updated_at": stale,
                    },
                    {
                        "job_id": "live-api-download",
                        "sample_id": None,
                        "job_type": "database_download",
                        "status": "running",
                        "created_at": stale,
                        "updated_at": stale,
                    },
                ],
            )

        assert (
            recover_worker_orphaned_jobs(
                registry.reference_engine,
                queued_job_ids={"queued-pending"},
                recovery_time=recovery_time,
                recover_running=True,
            )
            == 5
        )

        with registry.reference_engine.connect() as conn:
            statuses = dict(
                conn.execute(
                    sa.select(jobs.c.job_id, jobs.c.status).where(
                        jobs.c.job_id.in_(
                            (
                                "queued-pending",
                                "orphaned-lai-pending",
                                "fresh-pending",
                                "orphaned-database-update",
                                "orphaned-backup",
                                "orphaned-huey-download",
                                "orphaned-cancelling",
                                "live-api-download",
                            )
                        )
                    )
                ).all()
            )
        assert statuses == {
            "queued-pending": "pending",
            "orphaned-lai-pending": "failed",
            "fresh-pending": "pending",
            "orphaned-database-update": "failed",
            "orphaned-backup": "failed",
            "orphaned-huey-download": "failed",
            "orphaned-cancelling": "cancelled",
            "live-api-download": "running",
        }

    def test_worker_queue_snapshot_extracts_every_durable_job_type(
        self,
        annotation_env: dict,
    ) -> None:
        tasks = [
            run_annotation_task.s(1, "annotation-job"),
            run_lai_task.s(1, "lai-job"),
            run_update_check_task.s("check-job"),
            run_database_update_task.s("database-job", "clinvar"),
            run_backup_export_task.s("backup-job", False),
        ]

        assert [_worker_job_from_task(task) for task in tasks] == [
            ("annotation", "annotation-job"),
            ("lai_analysis", "lai-job"),
            ("update_check", "check-job"),
            ("database_update", "database-job"),
            ("backup_export", "backup-job"),
        ]

        with (
            patch.object(huey, "pending", return_value=tasks),
            patch.object(huey, "scheduled", return_value=[]),
            patch("backend.tasks.huey_tasks.recover_worker_orphaned_jobs") as recover_jobs,
        ):
            _recover_jobs_on_worker_startup()

        assert recover_jobs.call_args.kwargs["queued_job_ids"] == {
            "annotation-job",
            "lai-job",
            "check-job",
            "database-job",
            "backup-job",
        }
        assert recover_jobs.call_args.kwargs["recover_running"] is True

    def test_periodic_sweep_leaves_a_live_worker_cancelling_row_alone(
        self,
        annotation_env: dict,
    ) -> None:
        """The `cancelling` sweep must be gated exactly like `running`.

        `cancelling` is an ACTIVE state: it holds the annotation/export interlock
        until the worker acknowledges the cancel. The periodic sweep runs every
        five minutes with ``recover_running=False``, and an ungated sweep flipped
        every cancelling row to `cancelled` -- releasing the interlock while the
        live worker was still inside ``run_annotation()`` writing the sample
        database, which is the invariant the state exists to protect (#2232).
        """
        from backend.db.connection import get_registry
        from backend.tasks.huey_tasks import worker_lease_identity

        owner_id, owner_epoch = worker_lease_identity()
        recovery_time = datetime.now(UTC)
        registry = get_registry()
        with registry.reference_engine.begin() as conn:
            conn.execute(
                jobs.insert(),
                [
                    {
                        "job_id": "cancelling-live",
                        "sample_id": annotation_env["sample_id"],
                        "job_type": "annotation",
                        "status": "cancelling",
                        "created_at": recovery_time,
                        "updated_at": recovery_time,
                        "owner_id": owner_id,
                        "owner_epoch": owner_epoch,
                        "heartbeat_at": recovery_time,
                    }
                ],
            )

        assert (
            recover_worker_orphaned_jobs(
                registry.reference_engine,
                queued_job_ids={"cancelling-live"},
                recovery_time=recovery_time,
                recover_running=False,
                owner_id=owner_id,
                owner_epoch=owner_epoch,
            )
            == 0
        )
        with registry.reference_engine.connect() as conn:
            status = conn.scalar(
                sa.select(jobs.c.status).where(jobs.c.job_id == "cancelling-live")
            )
        assert status == "cancelling"

    def test_periodic_sweep_releases_a_cancelling_row_whose_owner_is_gone(
        self,
        annotation_env: dict,
    ) -> None:
        """The other half: a dead owner's lease must not be held forever.

        Without an owner and a heartbeat the API could not tell this row from the
        live one above, which is why recovery had to choose between releasing a
        live lease and leaving a dead one active indefinitely.
        """
        from backend.db.connection import get_registry
        from backend.tasks.huey_tasks import HUEY_HEARTBEAT_GRACE, worker_lease_identity

        owner_id, owner_epoch = worker_lease_identity()
        recovery_time = datetime.now(UTC)
        dead_heartbeat = recovery_time - HUEY_HEARTBEAT_GRACE - timedelta(seconds=1)
        registry = get_registry()
        with registry.reference_engine.begin() as conn:
            conn.execute(
                jobs.insert(),
                [
                    {
                        "job_id": "cancelling-dead",
                        "sample_id": annotation_env["sample_id"],
                        "job_type": "annotation",
                        "status": "cancelling",
                        "created_at": dead_heartbeat,
                        "updated_at": dead_heartbeat,
                        "owner_id": "huey:a-previous-worker",
                        "owner_epoch": "1:a-previous-run",
                        "heartbeat_at": dead_heartbeat,
                    }
                ],
            )

        assert (
            recover_worker_orphaned_jobs(
                registry.reference_engine,
                queued_job_ids=set(),
                recovery_time=recovery_time,
                recover_running=False,
                owner_id=owner_id,
                owner_epoch=owner_epoch,
            )
            == 1
        )
        with registry.reference_engine.connect() as conn:
            status = conn.scalar(
                sa.select(jobs.c.status).where(jobs.c.job_id == "cancelling-dead")
            )
        assert status == "cancelled"

    def test_periodic_sweep_preserves_a_running_lease_with_a_live_heartbeat(
        self,
        annotation_env: dict,
    ) -> None:
        """An API-only restart must not release a lease the worker still holds."""
        from backend.db.connection import get_registry
        from backend.tasks.huey_tasks import worker_lease_identity

        owner_id, owner_epoch = worker_lease_identity()
        recovery_time = datetime.now(UTC)
        registry = get_registry()
        with registry.reference_engine.begin() as conn:
            conn.execute(
                jobs.insert(),
                [
                    {
                        "job_id": "running-live",
                        "sample_id": annotation_env["sample_id"],
                        "job_type": "annotation",
                        "status": "running",
                        "created_at": recovery_time,
                        "updated_at": recovery_time,
                        "owner_id": owner_id,
                        "owner_epoch": owner_epoch,
                        "heartbeat_at": recovery_time,
                    }
                ],
            )

        assert (
            recover_worker_orphaned_jobs(
                registry.reference_engine,
                queued_job_ids={"running-live"},
                recovery_time=recovery_time,
                recover_running=False,
                owner_id=owner_id,
                owner_epoch=owner_epoch,
            )
            == 0
        )
        with registry.reference_engine.connect() as conn:
            status = conn.scalar(sa.select(jobs.c.status).where(jobs.c.job_id == "running-live"))
        assert status == "running"

    def test_claiming_a_job_records_the_lease(self, annotation_env: dict) -> None:
        """A claimed row must name its holder, or death cannot be proven later."""
        from backend.db.connection import get_registry
        from backend.tasks.huey_tasks import _claim_annotation_job, worker_lease_identity

        job_id = create_annotation_job(annotation_env["sample_id"])
        assert _claim_annotation_job(job_id) is True

        owner_id, owner_epoch = worker_lease_identity()
        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(jobs.c.owner_id, jobs.c.owner_epoch, jobs.c.heartbeat_at).where(
                    jobs.c.job_id == job_id
                )
            ).one()
        assert row.owner_id == owner_id
        assert row.owner_epoch == owner_epoch
        assert row.heartbeat_at is not None

    def test_enqueue_grace_is_revisited_after_it_expires(
        self,
        annotation_env: dict,
    ) -> None:
        from backend.db.connection import get_registry

        recovery_time = datetime.now(UTC)
        job_id = create_annotation_job(annotation_env["sample_id"])
        registry = get_registry()

        assert (
            recover_worker_orphaned_jobs(
                registry.reference_engine,
                queued_job_ids=set(),
                recovery_time=recovery_time,
                recover_running=False,
            )
            == 0
        )
        assert (
            recover_worker_orphaned_jobs(
                registry.reference_engine,
                queued_job_ids=set(),
                recovery_time=recovery_time + HUEY_PENDING_ENQUEUE_GRACE + timedelta(seconds=1),
                recover_running=False,
            )
            == 1
        )
        with registry.reference_engine.connect() as conn:
            status = conn.scalar(sa.select(jobs.c.status).where(jobs.c.job_id == job_id))
        assert status == "failed"

    def test_enqueue_failure_releases_annotation_reservation(
        self,
        annotation_env: dict,
    ) -> None:
        from backend.db.connection import get_registry

        sample_id = annotation_env["sample_id"]
        with (
            patch(
                "backend.tasks.huey_tasks.run_annotation_task",
                side_effect=OSError("huey queue unavailable"),
            ),
            pytest.raises(AnnotationEnqueueError, match="Unable to queue annotation"),
        ):
            enqueue_annotation_job(sample_id)

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(jobs.c.status, jobs.c.error)
                .where(jobs.c.sample_id == sample_id)
                .where(jobs.c.job_type == "annotation")
            ).fetchone()
        assert row is not None
        assert row.status == "failed"
        assert row.error == "huey queue unavailable"
        assert create_annotation_job(sample_id)

    def test_interrupted_signal_keeps_annotation_lease_until_worker_restart(
        self,
        annotation_env: dict,
    ) -> None:
        from backend.db.connection import get_registry

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)
        registry = get_registry()
        with registry.reference_engine.begin() as conn:
            conn.execute(jobs.update().where(jobs.c.job_id == job_id).values(status="running"))

        task = run_annotation_task.s(sample_id, job_id)
        huey._signal.send(SIGNAL_INTERRUPTED, task)

        with registry.reference_engine.connect() as conn:
            status = conn.scalar(sa.select(jobs.c.status).where(jobs.c.job_id == job_id))
        assert status == "running"

        recover_worker_orphaned_jobs(
            registry.reference_engine,
            queued_job_ids=set(),
            recovery_time=datetime.now(UTC),
            recover_running=True,
        )
        with registry.reference_engine.connect() as conn:
            status = conn.scalar(sa.select(jobs.c.status).where(jobs.c.job_id == job_id))
        assert status == "failed"


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

    def test_worker_update_cannot_overwrite_cancellation_request(
        self,
        annotation_env: dict,
    ) -> None:
        from backend.db.connection import get_registry

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)
        registry = get_registry()
        with registry.reference_engine.begin() as conn:
            conn.execute(jobs.update().where(jobs.c.job_id == job_id).values(status="cancelling"))

        _update_job(job_id, status="running", message="Worker started")

        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(jobs.c.status).where(jobs.c.job_id == job_id)).fetchone()
        assert row is not None
        assert row.status == "cancelling"

    def test_skipped_worker_update_logs_after_write_transaction_closes(
        self,
        annotation_env: dict,
    ) -> None:
        import backend.tasks.huey_tasks as huey_tasks
        from backend.db.connection import get_registry

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)
        registry = get_registry()
        with registry.reference_engine.begin() as conn:
            conn.execute(jobs.update().where(jobs.c.job_id == job_id).values(status="cancelling"))

        def write_from_warning(*_args, **_kwargs) -> None:
            with registry.reference_engine.begin() as conn:
                conn.exec_driver_sql("PRAGMA busy_timeout = 50")
                conn.execute(
                    jobs.update()
                    .where(jobs.c.job_id == job_id)
                    .values(message="warning emitted after commit")
                )

        with patch.object(huey_tasks.logger, "warning", side_effect=write_from_warning) as warning:
            _update_job(job_id, status="running", message="Worker update")

        warning.assert_called_once()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(jobs.c.status, jobs.c.message).where(jobs.c.job_id == job_id)
            ).fetchone()
        assert row is not None
        assert row.status == "cancelling"
        assert row.message == "warning emitted after commit"

    def test_finalization_acknowledges_a_racing_cancellation(
        self,
        annotation_env: dict,
    ) -> None:
        from backend.db.connection import get_registry

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)
        registry = get_registry()
        with registry.reference_engine.begin() as conn:
            conn.execute(
                jobs.update()
                .where(jobs.c.job_id == job_id)
                .values(status="cancelling", progress_pct=99.0)
            )

        resolved_status = _finalize_annotation_job(
            job_id,
            status="complete",
            progress_pct=100.0,
            message="Annotation complete",
        )

        with registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(jobs.c.status, jobs.c.progress_pct, jobs.c.message).where(
                    jobs.c.job_id == job_id
                )
            ).fetchone()
        assert resolved_status == "cancelled"
        assert row is not None
        assert row.status == "cancelled"
        assert row.progress_pct == 99.0
        assert row.message == "Cancelled by user"


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

    def test_task_marks_source_failures_partial(self, annotation_env: dict) -> None:
        """Unreadable annotation sources downgrade the job status to partial."""
        from backend.annotation.engine import AnnotationEngineResult
        from backend.db.connection import get_registry

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)
        result = AnnotationEngineResult(
            total_variants=5,
            rows_written=5,
            vep_matched=4,
            clinvar_matched=3,
            gnomad_matched=2,
            dbnsfp_matched=0,
            alphamissense_matched=1,
            gene_phenotype_matched=1,
            coverage_stats={"bundle_version": "test-bundle"},
            source_failures={"dbnsfp": "database is locked"},
        )

        with (
            patch("backend.annotation.engine.run_annotation", return_value=result),
            patch("backend.analysis.finding_diff.snapshot_findings", return_value=[]),
            patch("backend.analysis.run_all.run_all_analyses", return_value={}),
            patch(
                "backend.analysis.provenance.stamp_findings_provenance",
                return_value=0,
            ),
            patch(
                "backend.analysis.finding_diff.compute_and_store_finding_diff",
                return_value=None,
            ),
            patch(
                "backend.analysis.svg_renderer.generate_svgs_for_sample",
                return_value=0,
            ),
        ):
            run_annotation_task.call_local(sample_id, job_id)

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(jobs).where(jobs.c.job_id == job_id)).fetchone()

        assert row.status == "partial"
        assert row.progress_pct == 100.0
        assert row.error is None
        assert "Annotated 5 variants" in row.message
        assert "source(s) unavailable" in row.message
        assert "dbnsfp" in row.message

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

        # Intermediate writes still report progress; atomic finalization owns
        # the terminal 100% write so cancellation cannot race it.
        assert progress_updates
        assert any(0.0 < progress < 100.0 for progress in progress_updates)
        from backend.db.connection import get_registry

        with get_registry().reference_engine.connect() as conn:
            final_progress = conn.execute(
                sa.select(jobs.c.progress_pct).where(jobs.c.job_id == job_id)
            ).scalar_one()
        assert final_progress == 100.0

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

        # A cancellation request made before worker pickup must prevent the
        # mutating annotation engine from starting at all.
        with (
            patch("backend.tasks.huey_tasks._is_job_cancelled", return_value=True),
            patch("backend.annotation.engine.run_annotation") as run_annotation,
        ):
            run_annotation_task.call_local(sample_id, job_id)
        run_annotation.assert_not_called()

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(jobs).where(jobs.c.job_id == job_id)).fetchone()

        # Worker acknowledgement is terminal and cannot be overwritten by success.
        assert row.status == "cancelled"


# ═══════════════════════════════════════════════════════════════════════
# Step 10 (Plan §7.3) — deferred annotation_state upsert
# ═══════════════════════════════════════════════════════════════════════


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

    def _read_finding_provenance(self, annotation_env: dict) -> list[dict]:
        """Return every non-null provenance block from the sample DB."""
        from backend.db.connection import get_registry

        registry = get_registry()
        sample_db = registry.settings.data_dir / "samples" / "sample_1.db"
        sample_engine = registry.get_sample_engine(sample_db)
        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings.c.provenance).where(findings.c.provenance.is_not(None))
            ).fetchall()
        return [json.loads(row.provenance) for row in rows]

    def _seed_test_finding(self, annotation_env: dict, category: str) -> None:
        """Seed one finding so mocked task runs can exercise provenance."""
        from backend.db.connection import get_registry

        registry = get_registry()
        sample_db = registry.settings.data_dir / "samples" / "sample_1.db"
        sample_engine = registry.get_sample_engine(sample_db)
        with sample_engine.begin() as conn:
            conn.execute(
                findings.insert().values(
                    module="test",
                    category=category,
                    finding_text="Retain provenance through registry failure",
                )
            )

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

    def _seed_embedded_bundle_version(self, annotation_env: dict, version: str) -> None:
        """Write self-describing metadata without stamping reference.db."""
        seed_embedded_vep_bundle_version(
            annotation_env["settings"].vep_bundle_db_path,
            version,
        )

    def _run_task_with_result(
        self,
        sample_id: int,
        result: object,
        *,
        analysis_results: dict[str, int | str] | None = None,
    ) -> MagicMock:
        if analysis_results is None:
            analysis_results = {"pharmacogenomics": 0}
        with (
            patch("backend.annotation.engine.run_annotation", return_value=result),
            patch("backend.analysis.finding_diff.snapshot_findings", return_value=[]),
            patch(
                "backend.analysis.run_all.run_all_analyses",
                return_value=analysis_results,
            ),
            patch(
                "backend.analysis.finding_diff.compute_and_store_finding_diff",
                return_value=None,
            ) as compute_diff,
            patch(
                "backend.analysis.svg_renderer.generate_svgs_for_sample",
                return_value=0,
            ),
        ):
            run_annotation_task.call_local(sample_id, create_annotation_job(sample_id))
        return compute_diff

    @pytest.mark.slow
    def test_success_path_lifts_gate(self, annotation_env: dict) -> None:
        """Happy path: both reserved keys are upserted on the success path."""
        from backend.db.connection import get_registry

        self._seed_embedded_bundle_version(annotation_env, "v9.0.0")
        self._seed_bundle_version(annotation_env, "v2.0.0")

        registry = get_registry()
        sample_db = registry.settings.data_dir / "samples" / "sample_1.db"
        with registry.get_sample_engine(sample_db).begin() as conn:
            conn.execute(
                annotation_state.insert(),
                {
                    "key": CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY,
                    "value": json.dumps({"prompted": True}),
                },
            )

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
        assert _json.loads(state["reference_versions_json"])["vep_bundle"] == "v2.0.0"
        assert CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY not in state
        provenances = self._read_finding_provenance(annotation_env)
        assert provenances
        assert {provenance["sources"]["vep_bundle"]["version"] for provenance in provenances} == {
            "v2.0.0"
        }
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

    @pytest.mark.slow
    def test_embedded_bundle_version_is_stamped_without_registry_row(
        self,
        annotation_env: dict,
    ) -> None:
        """Self-described status copies drive both telemetry and the state stamp."""
        self._seed_embedded_bundle_version(annotation_env, "v3.0.0")

        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)
        run_annotation_task.call_local(sample_id, job_id)

        state = self._read_state(annotation_env)
        assert state.get("vep_bundle_version") == "v3.0.0"
        coverage_json = state.get("annotation_bundle_coverage_json")
        assert coverage_json is not None
        import json as _json

        assert _json.loads(coverage_json)["bundle_version"] == "v3.0.0"
        assert _json.loads(state["reference_versions_json"])["vep_bundle"] == "v3.0.0"
        provenances = self._read_finding_provenance(annotation_env)
        assert provenances
        assert {provenance["sources"]["vep_bundle"]["version"] for provenance in provenances} == {
            "v3.0.0"
        }

        from backend.db.connection import get_registry

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions.c.version).where(
                    database_versions.c.db_name == "vep_bundle"
                )
            ).fetchone()
        assert row is None

    def test_empty_coverage_resolves_embedded_version_for_state_stamp(
        self,
        annotation_env: dict,
    ) -> None:
        """A telemetry-free annotation still stamps the effective version."""
        from backend.annotation.engine import AnnotationEngineResult

        self._seed_embedded_bundle_version(annotation_env, "v3.0.0")
        sample_id = annotation_env["sample_id"]
        result = AnnotationEngineResult(coverage_stats={})

        self._run_task_with_result(sample_id, result)

        state = self._read_state(annotation_env)
        assert state.get("vep_bundle_version") == "v3.0.0"
        assert json.loads(state["annotation_bundle_coverage_json"])["bundle_version"] == "v3.0.0"

    def test_unreadable_version_table_uses_task_baseline(
        self,
        annotation_env: dict,
    ) -> None:
        """A transient registry read failure preserves the successful state stamp."""
        from structlog.testing import capture_logs

        from backend.annotation.engine import AnnotationEngineResult
        from backend.db.connection import get_registry

        sample_id = annotation_env["sample_id"]
        registry = get_registry()
        self._seed_test_finding(annotation_env, "unreadable_registry")
        self._seed_embedded_bundle_version(annotation_env, "v9.0.0")
        bundle_before = annotation_env["settings"].vep_bundle_db_path.read_bytes()
        database_versions.drop(registry.reference_engine)

        with capture_logs() as cap_logs:
            self._run_task_with_result(
                sample_id,
                AnnotationEngineResult(coverage_stats={"bundle_version": None}),
            )

        state = self._read_state(annotation_env)
        assert state.get("vep_bundle_version") == "v1.0.0"
        assert json.loads(state["annotation_bundle_coverage_json"])["bundle_version"] == "v1.0.0"
        assert json.loads(state["reference_versions_json"]) == {"vep_bundle": "v1.0.0"}
        provenances = self._read_finding_provenance(annotation_env)
        assert len(provenances) == 1
        assert provenances[0]["sources"]["vep_bundle"]["version"] == "v1.0.0"
        assert annotation_env["settings"].vep_bundle_db_path.read_bytes() == bundle_before
        assert any(
            entry.get("event") == "vep_bundle_version_resolution_failed" for entry in cap_logs
        )

    def test_run_resolved_version_precedes_later_readable_registry_state(
        self,
        annotation_env: dict,
    ) -> None:
        """One run keeps its captured VEP release across every persisted record."""
        from backend.annotation.engine import AnnotationEngineResult

        self._seed_test_finding(annotation_env, "run_resolved_precedence")
        self._seed_embedded_bundle_version(annotation_env, "v8.0.0")
        self._seed_bundle_version(annotation_env, "v9.0.0")

        compute_diff = self._run_task_with_result(
            annotation_env["sample_id"],
            AnnotationEngineResult(coverage_stats={"bundle_version": "v2.0.0"}),
        )

        state = self._read_state(annotation_env)
        assert state["vep_bundle_version"] == "v2.0.0"
        assert json.loads(state["annotation_bundle_coverage_json"])["bundle_version"] == "v2.0.0"
        assert json.loads(state["reference_versions_json"])["vep_bundle"] == "v2.0.0"
        provenances = self._read_finding_provenance(annotation_env)
        assert len(provenances) == 1
        assert provenances[0]["sources"]["vep_bundle"]["version"] == "v2.0.0"
        assert (
            compute_diff.call_args.kwargs["reference_snapshot"]["vep_bundle"]["version"]
            == "v2.0.0"
        )

    def test_unreadable_version_table_retains_run_resolved_version(
        self,
        annotation_env: dict,
    ) -> None:
        """A version captured by annotation remains identical in every snapshot."""
        from backend.annotation.engine import AnnotationEngineResult
        from backend.db.connection import get_registry

        self._seed_test_finding(annotation_env, "resolved_before_registry_failure")
        database_versions.drop(get_registry().reference_engine)

        self._run_task_with_result(
            annotation_env["sample_id"],
            AnnotationEngineResult(coverage_stats={"bundle_version": "v3.0.0"}),
        )

        state = self._read_state(annotation_env)
        assert state["vep_bundle_version"] == "v3.0.0"
        assert json.loads(state["annotation_bundle_coverage_json"])["bundle_version"] == "v3.0.0"
        assert json.loads(state["reference_versions_json"])["vep_bundle"] == "v3.0.0"
        provenances = self._read_finding_provenance(annotation_env)
        assert len(provenances) == 1
        assert provenances[0]["sources"]["vep_bundle"]["version"] == "v3.0.0"

    @pytest.mark.slow
    def test_missing_bundle_row_falls_back_to_v1(self, annotation_env: dict) -> None:
        """Defensive fallback when database_versions has no vep_bundle row."""
        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)
        run_annotation_task.call_local(sample_id, job_id)

        state = self._read_state(annotation_env)
        # Plan §7.3 — the versionless committed fixture resolves to the v1 baseline.
        assert state.get("vep_bundle_version") == "v1.0.0"
        coverage_json = state.get("annotation_bundle_coverage_json")
        assert coverage_json is not None
        import json as _json

        assert _json.loads(coverage_json)["bundle_version"] == "v1.0.0"
        assert _json.loads(state["reference_versions_json"])["vep_bundle"] == "v1.0.0"
        provenances = self._read_finding_provenance(annotation_env)
        assert provenances
        assert {provenance["sources"]["vep_bundle"]["version"] for provenance in provenances} == {
            "v1.0.0"
        }

    @pytest.mark.slow
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
                annotation_state.insert(),
                [
                    {"key": "vep_bundle_version", "value": "v1.0.0"},
                    {
                        "key": CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY,
                        "value": json.dumps({"prompted": True}),
                    },
                ],
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
        assert CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY in state

        # Job itself still marks complete — analysis is best-effort (Plan §7.3).
        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(jobs).where(jobs.c.job_id == job_id)).fetchone()
        assert row.status == "complete"

    def test_pharmacogenomics_module_error_preserves_reanalysis_marker(
        self,
        annotation_env: dict,
    ) -> None:
        from backend.annotation.engine import AnnotationEngineResult
        from backend.db.connection import get_registry

        registry = get_registry()
        sample_db = registry.settings.data_dir / "samples" / "sample_1.db"
        with registry.get_sample_engine(sample_db).begin() as conn:
            conn.execute(
                annotation_state.insert(),
                {
                    "key": CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY,
                    "value": json.dumps({"prompted": True}),
                },
            )

        self._run_task_with_result(
            annotation_env["sample_id"],
            AnnotationEngineResult(coverage_stats={"bundle_version": "v1.0.0"}),
            analysis_results={"pharmacogenomics": "error"},
        )

        state = self._read_state(annotation_env)
        assert CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY in state

    @pytest.mark.slow
    def test_two_phase_sse_progress_messages(self, annotation_env: dict) -> None:
        """SSE emits a two-phase progress arc: 'Annotating…' → 'Analyzing…'."""
        sample_id = annotation_env["sample_id"]
        job_id = create_annotation_job(sample_id)

        messages: list[str] = []
        from backend.db.connection import get_registry
        from backend.tasks.huey_tasks import (
            _claim_annotation_job as _real_claim_annotation_job,
        )
        from backend.tasks.huey_tasks import (
            _update_job as _real_update_job,
        )

        def capture_claim(jid: str) -> bool:
            claimed = _real_claim_annotation_job(jid)
            if claimed:
                with get_registry().reference_engine.connect() as conn:
                    message = conn.execute(
                        sa.select(jobs.c.message).where(jobs.c.job_id == jid)
                    ).scalar_one()
                messages.append(message)
            return claimed

        def capture(jid, *, status, progress_pct=0.0, message="", **kwargs):
            messages.append(message)
            _real_update_job(
                jid, status=status, progress_pct=progress_pct, message=message, **kwargs
            )

        with (
            patch(
                "backend.tasks.huey_tasks._claim_annotation_job",
                side_effect=capture_claim,
            ),
            patch("backend.tasks.huey_tasks._update_job", side_effect=capture),
        ):
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
        with patch("backend.tasks.huey_tasks.run_annotation_task") as mock_run:
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
        with patch("backend.tasks.huey_tasks.run_annotation_task") as mock_run:
            resp1 = annotation_client.post("/api/annotation/1")
            assert resp1.status_code == 202
            job_id = resp1.json()["job_id"]

            resp2 = annotation_client.post("/api/annotation/1")
            assert resp2.status_code == 409

        mock_run.assert_called_once_with(1, job_id)

    def test_start_annotation_enqueue_failure_returns_503_and_releases_reservation(
        self,
        annotation_client: TestClient,
        annotation_env: dict,
    ) -> None:
        from backend.db.connection import get_registry

        with patch(
            "backend.tasks.huey_tasks.run_annotation_task",
            side_effect=OSError("huey queue unavailable"),
        ):
            resp = annotation_client.post("/api/annotation/1")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Unable to queue annotation; retry the request."
        with get_registry().reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(jobs.c.status)
                .where(jobs.c.sample_id == 1)
                .where(jobs.c.job_type == "annotation")
            ).fetchone()
        assert row is not None
        assert row.status == "failed"

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
        """Cancellation remains active until the worker acknowledges it."""
        with patch("backend.tasks.huey_tasks.run_annotation_task") as mock_run:
            resp = annotation_client.post("/api/annotation/1")
            job_id = resp.json()["job_id"]

        mock_run.assert_called_once_with(1, job_id)

        resp = annotation_client.post(f"/api/annotation/cancel/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelling"

        from backend.db.connection import get_registry

        with get_registry().reference_engine.connect() as conn:
            row = conn.execute(sa.select(jobs.c.status).where(jobs.c.job_id == job_id)).fetchone()
        assert row is not None
        assert row.status == "cancelling"

        active = annotation_client.get("/api/annotation/active/1")
        assert active.status_code == 200
        assert active.json()["job_id"] == job_id
        assert active.json()["status"] == "cancelling"

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
