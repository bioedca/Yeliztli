"""Tests for backup/restore API routes (P4-21c).

Covers:
- GET  /api/backup/estimate
- POST /api/backup/export
- GET  /api/backup/status/{job_id}
- GET  /api/backup/download/{filename}
- Round-trip: export → import
"""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_schema import (
    CYP2C9_PHENYTOIN_LEGACY_GUIDANCE_VERSION,
    CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY,
    create_sample_tables,
)
from backend.db.tables import (
    annotation_state,
    findings,
    individuals,
    merge_provenance,
    reannotation_prompts,
    reference_metadata,
    sample_metadata_table,
    samples,
)
from backend.services.sample_delete import delete_sample_with_cascade, list_merged_children

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

_PATCHES = (
    "backend.main.get_settings",
    "backend.db.connection.get_settings",
    "backend.api.routes.backup.get_settings",
    "backend.tasks.huey_tasks.get_settings",
    "backend.api.routes.setup.get_settings",
)


def _make_client(settings: Settings):
    """Return an ExitStack context manager that patches get_settings everywhere."""
    from contextlib import ExitStack

    stack = ExitStack()
    for target in _PATCHES:
        stack.enter_context(patch(target, return_value=settings))
    # config.toml is read/written at config_toml_path() (DEFAULT_DATA_DIR); pin it
    # to the temp data dir so backup never touches the real ~/.yeliztli.
    stack.enter_context(patch("backend.config.DEFAULT_DATA_DIR", settings.data_dir))
    return stack


def _seed_data_dir(tmp_data_dir: Path, settings: Settings) -> None:
    """Create config, disclaimer, and sample files in tmp_data_dir."""
    # reference.db
    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    try:
        reference_metadata.create_all(engine)
        now = datetime.now(UTC)
        with engine.begin() as conn:
            conn.execute(
                individuals.insert().values(
                    id=1,
                    display_name="Test Individual",
                    notes="Grouped by backup test",
                    biological_sex="XX",
                    created_at=now,
                )
            )
            conn.execute(
                samples.insert(),
                [
                    {
                        "id": 1,
                        "name": "Custom sample one",
                        "db_path": "samples/sample_1.db",
                        "file_format": "23andme_v5",
                        "file_hash": "hash-one",
                        "individual_id": 1,
                        "created_at": now,
                    },
                    {
                        "id": 2,
                        "name": "Custom sample two",
                        "db_path": "samples/sample_2.db",
                        "file_format": "ancestrydna_v2",
                        "file_hash": "hash-two",
                        "individual_id": 1,
                        "created_at": now,
                    },
                ],
            )
    finally:
        engine.dispose()

    # config.toml
    (tmp_data_dir / "config.toml").write_text(
        '[yeliztli]\ndata_dir = "/tmp/test"\npubmed_email = "test@test.com"\n',
        encoding="utf-8",
    )

    # disclaimer
    (tmp_data_dir / ".disclaimer_accepted").write_text(
        '{"accepted_at": "2025-01-01T00:00:00Z", "version": "1.0"}',
        encoding="utf-8",
    )

    # sample DBs
    samples_dir = tmp_data_dir / "samples"
    (samples_dir / "sample_1.db").write_bytes(b"sample1_data" * 100)
    (samples_dir / "sample_2.db").write_bytes(b"sample2_data" * 200)


def _run_export(settings: Settings, include_refs: bool = False):
    """Run export task synchronously and return (job_id, filename)."""
    from backend.tasks.huey_tasks import create_backup_job, run_backup_export_task

    job_id = create_backup_job()
    # Call the underlying function directly (bypasses Huey queue)
    run_backup_export_task.call_local(job_id, include_refs)

    # Read job status to get filename
    from backend.db.connection import get_registry
    from backend.db.tables import jobs

    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(sa.select(jobs.c.message).where(jobs.c.job_id == job_id)).fetchone()

    prefix = "Backup complete: "
    filename = row.message[len(prefix) :] if row.message.startswith(prefix) else None
    return job_id, filename


class _UploadBytes:
    """Minimal async upload object for direct import_backup route tests."""

    def __init__(self, filename: str, content: bytes) -> None:
        self.filename = filename
        self._file = io.BytesIO(content)

    async def read(self, size: int = -1) -> bytes:
        return self._file.read(size)


# ═══════════════════════════════════════════════════════════════════════
# GET /api/backup/estimate
# ═══════════════════════════════════════════════════════════════════════


class TestBackupEstimate:
    def test_estimate_returns_sizes(self, tmp_data_dir: Path) -> None:
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        _seed_data_dir(tmp_data_dir, settings)

        with _make_client(settings):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                resp = tc.get("/api/backup/estimate")
            reset_registry()

        assert resp.status_code == 200
        data = resp.json()
        assert data["sample_count"] == 2
        assert data["sample_bytes"] > 0
        assert data["config_bytes"] > 0
        assert data["total_without_ref_bytes"] == data["sample_bytes"] + data["config_bytes"]

    def test_estimate_with_reference_dbs(self, tmp_data_dir: Path) -> None:
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        _seed_data_dir(tmp_data_dir, settings)
        (tmp_data_dir / "gnomad_af.db").write_bytes(b"gnomad_data" * 500)
        (tmp_data_dir / "vep_bundle.db").write_bytes(b"vep_data" * 1000)

        with _make_client(settings):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                resp = tc.get("/api/backup/estimate")
            reset_registry()

        data = resp.json()
        assert data["reference_bytes"] > 0
        assert data["reference_db_count"] >= 2
        assert data["total_with_ref_bytes"] > data["total_without_ref_bytes"]

    def test_estimate_empty_data_dir(self, tmp_data_dir: Path) -> None:
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        ref_path = settings.reference_db_path
        engine = sa.create_engine(f"sqlite:///{ref_path}")
        reference_metadata.create_all(engine)
        engine.dispose()

        with _make_client(settings):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                resp = tc.get("/api/backup/estimate")
            reset_registry()

        assert resp.status_code == 200
        data = resp.json()
        assert data["sample_count"] == 0
        assert data["sample_bytes"] == 0


# ═══════════════════════════════════════════════════════════════════════
# POST /api/backup/export + GET /api/backup/status + download
# ═══════════════════════════════════════════════════════════════════════


class TestBackupExport:
    def test_export_starts_job(self, tmp_data_dir: Path) -> None:
        """Export creates a job and returns job_id."""
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        _seed_data_dir(tmp_data_dir, settings)

        with _make_client(settings):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                with patch("backend.tasks.huey_tasks.run_backup_export_task") as mock_task:
                    resp = tc.post(
                        "/api/backup/export",
                        json={"include_reference_dbs": False},
                    )
            reset_registry()

        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert data["message"] == "Backup export started."
        mock_task.assert_called_once()

    def test_export_rejected_when_backup_already_running(self, tmp_data_dir: Path) -> None:
        """A second export while one is pending/running is rejected with 409."""
        from contextlib import ExitStack

        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        _seed_data_dir(tmp_data_dir, settings)

        with ExitStack() as stack:
            for target in (
                "backend.db.connection.get_settings",
                "backend.api.routes.backup.get_settings",
                "backend.tasks.huey_tasks.get_settings",
            ):
                stack.enter_context(patch(target, return_value=settings))
            stack.enter_context(patch("backend.config.DEFAULT_DATA_DIR", settings.data_dir))
            reset_registry()
            from backend.api.routes.backup import BackupExportRequest, backup_export
            from backend.tasks.huey_tasks import create_backup_job

            try:
                create_backup_job()
                with patch("backend.tasks.huey_tasks.run_backup_export_task") as mock_task:
                    with pytest.raises(HTTPException) as exc_info:
                        asyncio.run(
                            backup_export(BackupExportRequest(include_reference_dbs=False))
                        )
            finally:
                reset_registry()

        assert exc_info.value.status_code == 409
        assert "already in progress" in exc_info.value.detail
        mock_task.assert_not_called()

    def test_export_and_status_and_download(self, tmp_data_dir: Path) -> None:
        """Full flow: export → poll status → download archive."""
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        _seed_data_dir(tmp_data_dir, settings)

        with _make_client(settings):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                job_id, filename = _run_export(settings, include_refs=False)

                # Check status via API
                resp = tc.get(f"/api/backup/status/{job_id}")
                assert resp.status_code == 200
                status_data = resp.json()
                assert status_data["status"] == "complete"
                assert status_data["progress_pct"] == 100.0
                assert status_data["download_filename"] == filename
                assert filename.startswith("yeliztli_backup_")
                assert filename.endswith(".tar.gz")

                # Download via API
                resp = tc.get(f"/api/backup/download/{filename}")
                assert resp.status_code == 200
                assert len(resp.content) > 0

            reset_registry()

        # Verify archive contents by reading file directly
        archive_path = settings.downloads_dir / filename
        with tarfile.open(archive_path, "r:gz") as tf:
            names = tf.getnames()

        assert "config.toml" in names
        assert ".disclaimer_accepted" in names
        assert "sample_registry.json" in names
        assert "samples/sample_1.db" in names
        assert "samples/sample_2.db" in names
        assert "clinvar.db" not in names

        with tarfile.open(archive_path, "r:gz") as tf:
            manifest = json.loads(tf.extractfile("sample_registry.json").read().decode())
        assert [sample["name"] for sample in manifest["samples"]] == [
            "Custom sample one",
            "Custom sample two",
        ]

    def test_export_with_reference_dbs(self, tmp_data_dir: Path) -> None:
        """Export with include_reference_dbs includes standalone reference files."""
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        _seed_data_dir(tmp_data_dir, settings)
        (tmp_data_dir / "gnomad_af.db").write_bytes(b"gnomad_data" * 10)
        (tmp_data_dir / "clinvar.db").write_bytes(b"clinvar_data" * 10)

        with _make_client(settings):
            reset_registry()
            job_id, filename = _run_export(settings, include_refs=True)
            reset_registry()

        archive_path = settings.downloads_dir / filename
        with tarfile.open(archive_path, "r:gz") as tf:
            names = tf.getnames()

        assert "gnomad_af.db" in names
        assert "clinvar.db" not in names
        assert "reference.db" not in names
        assert "sample_registry.json" in names

    def test_export_registry_manifest_includes_wal_rows(self, tmp_data_dir: Path) -> None:
        """Registry manifest is queried live, so WAL-mode rows are included."""
        settings = Settings(data_dir=tmp_data_dir, wal_mode=True)
        samples_dir = tmp_data_dir / "samples"
        samples_dir.mkdir(parents=True, exist_ok=True)
        (tmp_data_dir / "downloads").mkdir(exist_ok=True)
        (samples_dir / "sample_1.db").write_bytes(b"sample1_data")

        with _make_client(settings):
            reset_registry()
            from backend.db.connection import get_registry

            registry = get_registry()
            reference_metadata.create_all(registry.reference_engine)
            with registry.reference_engine.begin() as conn:
                conn.execute(
                    samples.insert().values(
                        id=1,
                        name="WAL sample",
                        db_path="samples/sample_1.db",
                        file_format="23andme_v5",
                        file_hash="wal-hash",
                    )
                )
            _job_id, filename = _run_export(settings, include_refs=False)
            reset_registry()

        archive_path = settings.downloads_dir / filename
        with tarfile.open(archive_path, "r:gz") as tf:
            manifest = json.loads(tf.extractfile("sample_registry.json").read().decode())

        assert manifest["samples"][0]["name"] == "WAL sample"

    def test_export_registry_manifest_failure_is_fatal(
        self, tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A backup with sample DBs must not silently drop registry metadata."""
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        (tmp_data_dir / "samples").mkdir(parents=True, exist_ok=True)
        (tmp_data_dir / "samples" / "sample_1.db").write_bytes(b"sample1_data")

        def _boom():
            raise sa.exc.SQLAlchemyError("registry unavailable")

        monkeypatch.setattr("backend.db.connection.get_registry", _boom)

        with _make_client(settings):
            from backend.api.routes.backup import (
                BackupRegistryManifestError,
                build_sample_registry_manifest,
            )

            with pytest.raises(BackupRegistryManifestError):
                build_sample_registry_manifest(tmp_data_dir)


# ═══════════════════════════════════════════════════════════════════════
# GET /api/backup/status — error cases
# ═══════════════════════════════════════════════════════════════════════


class TestBackupStatus:
    def test_status_not_found(self, tmp_data_dir: Path) -> None:
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        ref_path = settings.reference_db_path
        engine = sa.create_engine(f"sqlite:///{ref_path}")
        reference_metadata.create_all(engine)
        engine.dispose()

        with _make_client(settings):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                resp = tc.get("/api/backup/status/nonexistent-job-id")
            reset_registry()

        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# GET /api/backup/download — error cases
# ═══════════════════════════════════════════════════════════════════════


class TestBackupDownload:
    def _make_test_client(self, tmp_data_dir: Path):
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        ref_path = settings.reference_db_path
        engine = sa.create_engine(f"sqlite:///{ref_path}")
        reference_metadata.create_all(engine)
        engine.dispose()
        return settings

    def test_download_invalid_filename(self, tmp_data_dir: Path) -> None:
        settings = self._make_test_client(tmp_data_dir)
        with _make_client(settings):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                resp = tc.get("/api/backup/download/evil.txt")
            reset_registry()
        assert resp.status_code == 400

    def test_download_path_traversal_blocked(self, tmp_data_dir: Path) -> None:
        """A filename containing '..' is rejected by the traversal guard (400).

        Regression: the previous version requested a *clean* filename and
        asserted 404 (file-not-found), so the ``".." in filename`` guard in
        ``backup_download`` was never exercised — a removed guard would still
        have passed. The '..' sits mid-segment (no slashes) so it reaches the
        handler intact instead of being normalized away by the HTTP router.
        """
        settings = self._make_test_client(tmp_data_dir)
        with _make_client(settings):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                resp = tc.get("/api/backup/download/yeliztli_backup_..config.tar.gz")
            reset_registry()
        # Traversal guard fires → 400 "Invalid filename." (not a 404 fall-through).
        assert resp.status_code == 400

    def test_download_not_found(self, tmp_data_dir: Path) -> None:
        settings = self._make_test_client(tmp_data_dir)
        with _make_client(settings):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                resp = tc.get("/api/backup/download/yeliztli_backup_20250101_000000.tar.gz")
            reset_registry()
        assert resp.status_code == 404

    def test_download_accepts_legacy_prefix(self, tmp_data_dir: Path) -> None:
        """Back-compat (R3): a legacy genomeinsight_backup_*.tar.gz archive still downloads.

        The producer now emits ``yeliztli_backup_*``, but the download
        validator accepts BOTH prefixes for one release so users' pre-rebrand
        archives are not stranded (restore is already filename-agnostic). A
        real legacy-named file is placed in downloads_dir; the validator must
        pass it through (200), not reject it as an invalid backup filename.
        """
        settings = self._make_test_client(tmp_data_dir)
        legacy_name = "genomeinsight_backup_20250101_000000.tar.gz"
        settings.downloads_dir.mkdir(parents=True, exist_ok=True)
        (settings.downloads_dir / legacy_name).write_bytes(b"legacy-archive-bytes")
        with _make_client(settings):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                resp = tc.get(f"/api/backup/download/{legacy_name}")
            reset_registry()
        assert resp.status_code == 200
        assert resp.content == b"legacy-archive-bytes"


# ═══════════════════════════════════════════════════════════════════════
# Round-trip: export → import
# ═══════════════════════════════════════════════════════════════════════


class TestBackupRoundTrip:
    def test_legacy_archive_without_registry_uses_local_identity_for_reanalysis_prompt(
        self,
        tmp_data_dir: Path,
        tmp_path: Path,
    ) -> None:
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        created_at = datetime(2025, 4, 3, 2, 1, tzinfo=UTC)
        legacy_db = tmp_path / "sample_7.db"
        legacy_engine = sa.create_engine(f"sqlite:///{legacy_db}")
        create_sample_tables(legacy_engine)
        with legacy_engine.begin() as conn:
            conn.execute(
                sample_metadata_table.insert(),
                {
                    "id": 1,
                    "name": "Legacy local sample",
                    "file_format": "23andme_v5",
                    "file_hash": "legacy-local-hash",
                    "created_at": created_at,
                },
            )
            conn.execute(
                findings.insert(),
                {
                    "module": "pharmacogenomics",
                    "category": "prescribing_alert",
                    "evidence_level": 4,
                    "gene_symbol": "CYP2C9",
                    "drug": "phenytoin",
                    "finding_text": "Legacy phenotype-only recommendation",
                },
            )
            conn.execute(sa.text("PRAGMA user_version = 20"))
        legacy_engine.dispose()

        archive_buffer = io.BytesIO()
        with tarfile.open(fileobj=archive_buffer, mode="w:gz") as tf:
            tf.add(legacy_db, arcname="samples/sample_7.db")

        with _make_client(settings):
            reset_registry()
            from backend.api.routes.setup import import_backup
            from backend.db.connection import get_registry

            registry = get_registry()
            reference_metadata.create_all(registry.reference_engine)
            with registry.reference_engine.begin() as conn:
                conn.execute(
                    reannotation_prompts.insert(),
                    {
                        "sample_id": 7,
                        "db_name": "clinvar",
                        "db_version": "20260101",
                        "prompt_type": "reclassification",
                        "stale_databases": "[]",
                        "dismissed": True,
                        # Newer than the restored sample below: timestamp-only
                        # cleanup cannot determine that this belongs to a deleted
                        # prior occupant of ID 7.
                        "created_at": datetime(2026, 1, 2, tzinfo=UTC),
                    },
                )
            result = asyncio.run(
                import_backup(
                    _UploadBytes(
                        filename="legacy-no-registry.tar.gz",
                        content=archive_buffer.getvalue(),
                    )
                )
            )
            restored_path = settings.data_dir / "samples" / "sample_7.db"
            restored_engine = registry.get_sample_engine(restored_path)
            with restored_engine.connect() as conn:
                marker = json.loads(
                    conn.execute(
                        sa.select(annotation_state.c.value).where(
                            annotation_state.c.key == CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY
                        )
                    ).scalar_one()
                )
            with registry.reference_engine.connect() as conn:
                registry_row = (
                    conn.execute(sa.select(samples).where(samples.c.id == 7)).mappings().one()
                )
                prompts = conn.execute(sa.select(reannotation_prompts)).mappings().all()
            reset_registry()

        assert result.samples_restored == 1
        assert registry_row["name"] == "Legacy local sample"
        assert registry_row["file_format"] == "23andme_v5"
        assert registry_row["file_hash"] == "legacy-local-hash"
        assert marker["prompted"] is True
        assert len(prompts) == 1
        prompt = prompts[0]
        assert prompt["db_name"] == "reference_data"
        assert prompt["prompt_type"] == "version_staleness"
        assert json.loads(prompt["stale_databases"])[0]["recorded_version"] == (
            CYP2C9_PHENYTOIN_LEGACY_GUIDANCE_VERSION
        )

    def test_export_then_import_restores_sample_registry(
        self, tmp_data_dir: Path, tmp_path: Path
    ) -> None:
        """Export/import preserves visible samples and individual groupings."""
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        _seed_data_dir(tmp_data_dir, settings)

        # Step 1: Export
        with _make_client(settings):
            reset_registry()
            _job_id, filename = _run_export(settings, include_refs=False)
            reset_registry()

        # Read the archive from disk
        archive_path = settings.downloads_dir / filename
        archive_content = archive_path.read_bytes()

        # Step 2: Import into a fresh data directory
        fresh_dir = tmp_path / "fresh_install"
        fresh_dir.mkdir()
        (fresh_dir / "samples").mkdir()
        (fresh_dir / "downloads").mkdir()
        (fresh_dir / "logs").mkdir()
        fresh_settings = Settings(data_dir=fresh_dir, wal_mode=False)

        ref_path = fresh_settings.reference_db_path
        engine = sa.create_engine(f"sqlite:///{ref_path}")
        reference_metadata.create_all(engine)
        engine.dispose()

        with _make_client(fresh_settings):
            reset_registry()
            from backend.api.routes.samples import list_samples
            from backend.api.routes.setup import import_backup

            import_result = asyncio.run(
                import_backup(_UploadBytes(filename=filename, content=archive_content))
            )
            listed_samples = asyncio.run(list_samples())
            reset_registry()

        assert import_result.success is True
        assert import_result.samples_restored == 2
        assert import_result.config_restored is True

        # Verify files exist in fresh dir
        assert (fresh_dir / "config.toml").exists()
        assert (fresh_dir / "samples" / "sample_1.db").exists()
        assert (fresh_dir / "samples" / "sample_2.db").exists()

        assert sorted(sample.name for sample in listed_samples) == [
            "Custom sample one",
            "Custom sample two",
        ]
        assert {sample.db_path for sample in listed_samples} == {
            "samples/sample_1.db",
            "samples/sample_2.db",
        }

        engine = sa.create_engine(f"sqlite:///{fresh_settings.reference_db_path}")
        try:
            with engine.connect() as conn:
                grouped = conn.execute(
                    sa.select(
                        individuals.c.display_name,
                        individuals.c.biological_sex,
                        samples.c.name,
                    )
                    .join(samples, samples.c.individual_id == individuals.c.id)
                    .order_by(samples.c.id.asc())
                ).fetchall()
        finally:
            engine.dispose()

        assert [(row.display_name, row.biological_sex, row.name) for row in grouped] == [
            ("Test Individual", "XX", "Custom sample one"),
            ("Test Individual", "XX", "Custom sample two"),
        ]

    def test_import_allocates_new_sample_paths_on_existing_install(
        self, tmp_data_dir: Path, tmp_path: Path
    ) -> None:
        """Restoring sample_1.db into an install with sample_1.db keeps both."""
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        _seed_data_dir(tmp_data_dir, settings)

        with _make_client(settings):
            reset_registry()
            _job_id, filename = _run_export(settings, include_refs=False)
            reset_registry()
        archive_content = (settings.downloads_dir / filename).read_bytes()

        existing_dir = tmp_path / "existing_install"
        existing_dir.mkdir()
        (existing_dir / "samples").mkdir()
        (existing_dir / "downloads").mkdir()
        (existing_dir / "logs").mkdir()
        existing_settings = Settings(data_dir=existing_dir, wal_mode=False)
        existing_sample = existing_dir / "samples" / "sample_1.db"
        existing_sample.write_bytes(b"local-sample-one")
        engine = sa.create_engine(f"sqlite:///{existing_settings.reference_db_path}")
        try:
            reference_metadata.create_all(engine)
            with engine.begin() as conn:
                conn.execute(
                    samples.insert().values(
                        id=1,
                        name="Local sample one",
                        db_path="samples/sample_1.db",
                        file_format="23andme_v5",
                        file_hash="local-hash",
                    )
                )
        finally:
            engine.dispose()

        with _make_client(existing_settings):
            reset_registry()
            from backend.db.connection import get_registry

            registry = get_registry()
            with registry.reference_engine.begin() as conn:
                conn.execute(
                    reannotation_prompts.insert(),
                    [
                        {
                            "sample_id": sample_id,
                            "db_name": "clinvar",
                            "db_version": "20260101",
                            "prompt_type": "reclassification",
                            "stale_databases": "[]",
                            "dismissed": True,
                        }
                        for sample_id in (1, 2, 3)
                    ],
                )
            from backend.api.routes.setup import import_backup

            result = asyncio.run(
                import_backup(_UploadBytes(filename=filename, content=archive_content))
            )
            reset_registry()

        assert result.samples_restored == 2
        assert existing_sample.read_bytes() == b"local-sample-one"
        assert (existing_dir / "samples" / "sample_2.db").exists()
        assert (existing_dir / "samples" / "sample_3.db").exists()

        engine = sa.create_engine(f"sqlite:///{existing_settings.reference_db_path}")
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    sa.select(samples.c.id, samples.c.name, samples.c.db_path).order_by(
                        samples.c.id.asc()
                    )
                ).fetchall()
                prompt_sample_ids = (
                    conn.execute(
                        sa.select(reannotation_prompts.c.sample_id).order_by(
                            reannotation_prompts.c.sample_id.asc()
                        )
                    )
                    .scalars()
                    .all()
                )
        finally:
            engine.dispose()

        assert [(row.id, row.name, row.db_path) for row in rows] == [
            (1, "Local sample one", "samples/sample_1.db"),
            (2, "Custom sample one", "samples/sample_2.db"),
            (3, "Custom sample two", "samples/sample_3.db"),
        ]
        assert prompt_sample_ids == [1]

    def test_import_remaps_merged_sources_before_registry_publication(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A colliding destination ID must never capture restored provenance.

        The archive's source IDs are 1 and 2. The destination already owns ID
        1, so restore reallocates the first source. The merged database must be
        rewritten before publication: deleting destination ID 1 must leave it
        alone, while deleting the remapped source must cascade to it.
        """
        source_dir = tmp_path / "provenance_source"
        source_dir.mkdir()
        (source_dir / "samples").mkdir()
        source_settings = Settings(data_dir=source_dir, wal_mode=False)
        merged_name = f"merged_{'c' * 32}.db"
        nested_name = f"merged_{'d' * 32}.db"

        def create_archived_db(
            path: Path,
            *,
            merged: bool = False,
            source_ids: tuple[int, int] = (1, 2),
            source_hashes: tuple[str, str] = ("source-one", "source-two"),
        ) -> None:
            engine = sa.create_engine(f"sqlite:///{path}")
            try:
                create_sample_tables(engine, is_merged_sample=merged)
                if merged:
                    with engine.begin() as conn:
                        conn.execute(
                            merge_provenance.insert().values(
                                id=1,
                                strategy="flag_only",
                                source_sample_ids=json.dumps(source_ids),
                                source_file_hashes=json.dumps(source_hashes),
                                concordance_summary="{}",
                            )
                        )
            finally:
                engine.dispose()

        create_archived_db(source_dir / "samples" / "sample_1.db")
        create_archived_db(source_dir / "samples" / "sample_2.db")
        create_archived_db(source_dir / "samples" / merged_name, merged=True)
        # Before #2330 a direct API caller could merge an already-merged sample.
        # Current exports can therefore contain this historical dependency even
        # though new merge requests reject it.
        create_archived_db(
            source_dir / "samples" / nested_name,
            merged=True,
            source_ids=(2, 3),
            source_hashes=("source-two", "merged-child"),
        )

        with _make_client(source_settings):
            reset_registry()
            from backend.db.connection import get_registry

            source_registry = get_registry()
            reference_metadata.create_all(source_registry.reference_engine)
            with source_registry.reference_engine.begin() as conn:
                conn.execute(
                    samples.insert(),
                    [
                        {
                            "id": 1,
                            "name": "Restored source one",
                            "db_path": "samples/sample_1.db",
                            "file_format": "23andme_v5",
                            "file_hash": "source-one",
                        },
                        {
                            "id": 2,
                            "name": "Restored source two",
                            "db_path": "samples/sample_2.db",
                            "file_format": "ancestrydna_v2",
                            "file_hash": "source-two",
                        },
                        {
                            "id": 3,
                            "name": "Restored merged child",
                            "db_path": f"samples/{merged_name}",
                            "file_format": "merged_v1",
                            "file_hash": "merged-child",
                        },
                        {
                            "id": 4,
                            "name": "Restored legacy nested child",
                            "db_path": f"samples/{nested_name}",
                            "file_format": "merged_v1",
                            "file_hash": "nested-child",
                        },
                    ],
                )
            _job_id, filename = _run_export(source_settings, include_refs=False)
            archive_content = (source_settings.downloads_dir / filename).read_bytes()
            reset_registry()

        def repack_archive(
            *,
            drop_source: bool = False,
            hide_merged: bool = False,
            duplicate_source_ids: bool = False,
            swap_source_hashes: bool = False,
            noncanonical_provenance_id: bool = False,
            mutate_provenance_after_update: bool = False,
            malformed_hidden_provenance: bool = False,
            invalid_utf8_column: str | None = None,
            pathological_json: tuple[str, str] | None = None,
            cyclic_nested_sources: bool = False,
        ) -> bytes:
            repacked = io.BytesIO()
            with (
                tarfile.open(fileobj=io.BytesIO(archive_content), mode="r:gz") as source_tf,
                tarfile.open(fileobj=repacked, mode="w:gz") as target_tf,
            ):
                for member in source_tf.getmembers():
                    if drop_source and member.name == "samples/sample_2.db":
                        continue
                    payload = source_tf.extractfile(member) if member.isfile() else None
                    db_mutations = (
                        noncanonical_provenance_id,
                        mutate_provenance_after_update,
                        malformed_hidden_provenance,
                        invalid_utf8_column is not None,
                        pathological_json is not None,
                        cyclic_nested_sources,
                    )
                    assert sum(db_mutations) <= 1
                    if any(db_mutations) and member.name == f"samples/{merged_name}":
                        assert payload is not None
                        if noncanonical_provenance_id:
                            mutation_name = "noncanonical-provenance-id"
                        elif mutate_provenance_after_update:
                            mutation_name = "mutate-provenance-trigger"
                        elif malformed_hidden_provenance:
                            mutation_name = "malformed-hidden-provenance"
                        elif invalid_utf8_column is not None:
                            assert invalid_utf8_column in {
                                "source_sample_ids",
                                "source_file_hashes",
                            }
                            mutation_name = f"invalid-utf8-{invalid_utf8_column}"
                        elif pathological_json is not None:
                            pathological_column, _ = pathological_json
                            assert pathological_column in {
                                "source_sample_ids",
                                "source_file_hashes",
                            }
                            mutation_name = f"pathological-json-{pathological_column}"
                        else:
                            mutation_name = "cyclic-nested-sources"
                        malformed_db = tmp_path / f"{mutation_name}.db"
                        malformed_db.write_bytes(payload.read())
                        conn = sqlite3.connect(malformed_db)
                        try:
                            conn.execute("PRAGMA journal_mode=DELETE")
                            if noncanonical_provenance_id:
                                conn.executescript(
                                    """
                                    ALTER TABLE merge_provenance
                                        RENAME TO merge_provenance_canonical;
                                    CREATE TABLE merge_provenance (
                                        id INTEGER NOT NULL PRIMARY KEY,
                                        merged_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                                        strategy TEXT NOT NULL,
                                        source_sample_ids TEXT NOT NULL,
                                        source_file_hashes TEXT NOT NULL,
                                        concordance_summary TEXT NOT NULL
                                    );
                                    INSERT INTO merge_provenance (
                                        id,
                                        merged_at,
                                        strategy,
                                        source_sample_ids,
                                        source_file_hashes,
                                        concordance_summary
                                    )
                                    SELECT
                                        2,
                                        merged_at,
                                        strategy,
                                        source_sample_ids,
                                        source_file_hashes,
                                        concordance_summary
                                    FROM merge_provenance_canonical;
                                    DROP TABLE merge_provenance_canonical;
                                    """
                                )
                            elif mutate_provenance_after_update:
                                conn.executescript(
                                    """
                                    CREATE TRIGGER corrupt_unchecked_provenance
                                    AFTER UPDATE OF source_sample_ids ON merge_provenance
                                    BEGIN
                                        UPDATE merge_provenance
                                        SET concordance_summary = '{"match":999999}'
                                        WHERE id = NEW.id;
                                    END;
                                    """
                                )
                            elif malformed_hidden_provenance:
                                conn.executescript(
                                    """
                                    ALTER TABLE merge_provenance
                                        RENAME TO merge_provenance_complete;
                                    CREATE TABLE merge_provenance (
                                        id INTEGER NOT NULL PRIMARY KEY,
                                        merged_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                                        strategy TEXT NOT NULL,
                                        source_sample_ids TEXT NOT NULL,
                                        concordance_summary TEXT NOT NULL
                                    );
                                    INSERT INTO merge_provenance (
                                        id,
                                        merged_at,
                                        strategy,
                                        source_sample_ids,
                                        concordance_summary
                                    )
                                    SELECT
                                        id,
                                        merged_at,
                                        strategy,
                                        source_sample_ids,
                                        concordance_summary
                                    FROM merge_provenance_complete;
                                    DROP TABLE merge_provenance_complete;
                                    """
                                )
                            elif invalid_utf8_column == "source_sample_ids":
                                conn.execute(
                                    "UPDATE merge_provenance SET source_sample_ids = X'80'"
                                )
                                assert conn.execute(
                                    "SELECT typeof(source_sample_ids), hex(source_sample_ids) "
                                    "FROM merge_provenance WHERE id = 1"
                                ).fetchone() == ("blob", "80")
                            elif invalid_utf8_column == "source_file_hashes":
                                conn.execute(
                                    "UPDATE merge_provenance SET source_file_hashes = X'80'"
                                )
                                assert conn.execute(
                                    "SELECT typeof(source_file_hashes), hex(source_file_hashes) "
                                    "FROM merge_provenance WHERE id = 1"
                                ).fetchone() == ("blob", "80")
                            elif pathological_json is not None:
                                pathological_column, raw_json = pathological_json
                                if pathological_column == "source_sample_ids":
                                    conn.execute(
                                        "UPDATE merge_provenance SET source_sample_ids = ?",
                                        (raw_json,),
                                    )
                                else:
                                    conn.execute(
                                        "UPDATE merge_provenance SET source_file_hashes = ?",
                                        (raw_json,),
                                    )
                            else:
                                # The first child now names the nested child, while
                                # that nested child already names the first: a forged
                                # cycle that must not be published.
                                conn.execute(
                                    "UPDATE merge_provenance "
                                    "SET source_sample_ids = '[4,1]', "
                                    'source_file_hashes = \'["nested-child","source-one"]\''
                                )
                            conn.commit()
                        finally:
                            conn.close()
                        encoded = malformed_db.read_bytes()
                        member.size = len(encoded)
                        payload = io.BytesIO(encoded)
                    if (
                        hide_merged
                        or malformed_hidden_provenance
                        or duplicate_source_ids
                        or swap_source_hashes
                    ) and member.name == "sample_registry.json":
                        assert payload is not None
                        manifest = json.loads(payload.read().decode())
                        rows_by_name = {row["name"]: row for row in manifest["samples"]}
                        if hide_merged or malformed_hidden_provenance:
                            rows_by_name["Restored merged child"]["file_format"] = "23andme_v5"
                        if duplicate_source_ids:
                            rows_by_name["Restored source two"]["id"] = rows_by_name[
                                "Restored source one"
                            ]["id"]
                        if swap_source_hashes:
                            source_one = rows_by_name["Restored source one"]
                            source_two = rows_by_name["Restored source two"]
                            source_one["file_hash"], source_two["file_hash"] = (
                                source_two["file_hash"],
                                source_one["file_hash"],
                            )
                        encoded = json.dumps(manifest).encode()
                        member.size = len(encoded)
                        payload = io.BytesIO(encoded)
                    target_tf.addfile(member, payload)
            return repacked.getvalue()

        incomplete_archive = repack_archive(drop_source=True)
        hidden_merge_archive = repack_archive(hide_merged=True)
        duplicate_ids_archive = repack_archive(duplicate_source_ids=True)
        mismatched_source_hash_archive = repack_archive(swap_source_hashes=True)
        noncanonical_provenance_archive = repack_archive(noncanonical_provenance_id=True)
        provenance_trigger_archive = repack_archive(mutate_provenance_after_update=True)
        malformed_hidden_provenance_archive = repack_archive(malformed_hidden_provenance=True)
        invalid_utf8_archives = {
            column: repack_archive(invalid_utf8_column=column)
            for column in ("source_sample_ids", "source_file_hashes")
        }
        pathological_json_archives = {
            "oversized-integer-source-ids": repack_archive(
                pathological_json=(
                    "source_sample_ids",
                    "[" + "1" * 5_000 + ",2]",
                )
            ),
            "deeply-nested-source-hashes": repack_archive(
                pathological_json=(
                    "source_file_hashes",
                    "[" * 10_000 + '"hash"' + "]" * 10_000,
                )
            ),
        }
        cyclic_nested_archive = repack_archive(cyclic_nested_sources=True)

        target_dir = tmp_path / "provenance_target"
        target_dir.mkdir()
        (target_dir / "samples").mkdir()
        target_settings = Settings(data_dir=target_dir, wal_mode=False)
        local_db = target_dir / "samples" / "sample_1.db"
        create_archived_db(local_db)
        target_registry = DBRegistry(target_settings)
        try:
            reference_metadata.create_all(target_registry.reference_engine)
            with target_registry.reference_engine.begin() as conn:
                conn.execute(
                    samples.insert().values(
                        id=1,
                        name="Unrelated local sample",
                        db_path="samples/sample_1.db",
                        file_format="23andme_v5",
                        file_hash="local-one",
                    )
                )

            from backend.api.routes import setup as setup_routes

            monkeypatch.setattr(setup_routes, "get_settings", lambda: target_settings)
            monkeypatch.setattr(setup_routes, "get_registry", lambda: target_registry)
            monkeypatch.setattr(
                setup_routes,
                "config_toml_path",
                lambda: target_dir / "config.toml",
            )

            def assert_target_unchanged() -> None:
                with target_registry.reference_engine.connect() as conn:
                    assert (
                        conn.execute(sa.select(sa.func.count()).select_from(samples)).scalar_one()
                        == 1
                    )
                assert sorted(path.name for path in (target_dir / "samples").iterdir()) == [
                    "sample_1.db"
                ]

            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(
                    setup_routes.import_backup(
                        _UploadBytes(
                            filename="incomplete-merged-sources.tar.gz",
                            content=incomplete_archive,
                        )
                    )
                )
            assert exc_info.value.status_code == 400
            assert exc_info.value.detail == (
                "Invalid backup archive: merged-sample provenance cannot be bound "
                "to its restored sources."
            )
            assert_target_unchanged()

            with pytest.raises(HTTPException) as hidden_exc_info:
                asyncio.run(
                    setup_routes.import_backup(
                        _UploadBytes(
                            filename="hidden-merged-provenance.tar.gz",
                            content=hidden_merge_archive,
                        )
                    )
                )
            assert hidden_exc_info.value.status_code == 400
            assert hidden_exc_info.value.detail == exc_info.value.detail
            assert_target_unchanged()

            with pytest.raises(HTTPException) as duplicate_exc_info:
                asyncio.run(
                    setup_routes.import_backup(
                        _UploadBytes(
                            filename="duplicate-archived-sample-ids.tar.gz",
                            content=duplicate_ids_archive,
                        )
                    )
                )
            assert duplicate_exc_info.value.status_code == 400
            assert duplicate_exc_info.value.detail == exc_info.value.detail
            assert_target_unchanged()

            with pytest.raises(HTTPException) as hash_exc_info:
                asyncio.run(
                    setup_routes.import_backup(
                        _UploadBytes(
                            filename="mismatched-merged-source-hashes.tar.gz",
                            content=mismatched_source_hash_archive,
                        )
                    )
                )
            assert hash_exc_info.value.status_code == 400
            assert hash_exc_info.value.detail == exc_info.value.detail
            assert_target_unchanged()

            with pytest.raises(HTTPException) as provenance_id_exc_info:
                asyncio.run(
                    setup_routes.import_backup(
                        _UploadBytes(
                            filename="noncanonical-merged-provenance-id.tar.gz",
                            content=noncanonical_provenance_archive,
                        )
                    )
                )
            assert provenance_id_exc_info.value.status_code == 400
            assert provenance_id_exc_info.value.detail == exc_info.value.detail
            assert_target_unchanged()

            with pytest.raises(HTTPException) as provenance_trigger_exc_info:
                asyncio.run(
                    setup_routes.import_backup(
                        _UploadBytes(
                            filename="provenance-update-trigger.tar.gz",
                            content=provenance_trigger_archive,
                        )
                    )
                )
            assert provenance_trigger_exc_info.value.status_code == 400
            assert provenance_trigger_exc_info.value.detail == exc_info.value.detail
            assert_target_unchanged()

            with pytest.raises(HTTPException) as malformed_hidden_exc_info:
                asyncio.run(
                    setup_routes.import_backup(
                        _UploadBytes(
                            filename="malformed-hidden-provenance.tar.gz",
                            content=malformed_hidden_provenance_archive,
                        )
                    )
                )
            assert malformed_hidden_exc_info.value.status_code == 400
            assert malformed_hidden_exc_info.value.detail == exc_info.value.detail
            assert_target_unchanged()

            for column, invalid_utf8_archive in invalid_utf8_archives.items():
                with pytest.raises(HTTPException) as invalid_utf8_exc_info:
                    asyncio.run(
                        setup_routes.import_backup(
                            _UploadBytes(
                                filename=f"invalid-utf8-{column}.tar.gz",
                                content=invalid_utf8_archive,
                            )
                        )
                    )
                assert invalid_utf8_exc_info.value.status_code == 400
                assert invalid_utf8_exc_info.value.detail == exc_info.value.detail
                assert_target_unchanged()

            for case_name, pathological_archive in pathological_json_archives.items():
                with pytest.raises(HTTPException) as pathological_exc_info:
                    asyncio.run(
                        setup_routes.import_backup(
                            _UploadBytes(
                                filename=f"{case_name}.tar.gz",
                                content=pathological_archive,
                            )
                        )
                    )
                assert pathological_exc_info.value.status_code == 400
                assert pathological_exc_info.value.detail == exc_info.value.detail
                assert_target_unchanged()

            with pytest.raises(HTTPException) as cyclic_nested_exc_info:
                asyncio.run(
                    setup_routes.import_backup(
                        _UploadBytes(
                            filename="cyclic-nested-provenance.tar.gz",
                            content=cyclic_nested_archive,
                        )
                    )
                )
            assert cyclic_nested_exc_info.value.status_code == 400
            assert cyclic_nested_exc_info.value.detail == exc_info.value.detail
            assert_target_unchanged()

            result = asyncio.run(
                setup_routes.import_backup(
                    _UploadBytes(filename=filename, content=archive_content)
                )
            )

            with target_registry.reference_engine.connect() as conn:
                restored = {
                    row.name: row
                    for row in conn.execute(
                        sa.select(samples.c.id, samples.c.name, samples.c.db_path)
                    ).all()
                }
            source_one = restored["Restored source one"]
            source_two = restored["Restored source two"]
            merged = restored["Restored merged child"]
            nested = restored["Restored legacy nested child"]
            merged_path = target_dir / merged.db_path
            nested_path = target_dir / nested.db_path
            merged_engine = target_registry.get_sample_engine(merged_path)
            with merged_engine.connect() as conn:
                restored_provenance = conn.execute(
                    sa.select(
                        merge_provenance.c.source_sample_ids,
                        merge_provenance.c.source_file_hashes,
                    )
                ).one()
                remapped_sources = json.loads(restored_provenance.source_sample_ids)
                restored_source_hashes = json.loads(restored_provenance.source_file_hashes)
            nested_engine = target_registry.get_sample_engine(nested_path)
            with nested_engine.connect() as conn:
                nested_provenance = conn.execute(
                    sa.select(
                        merge_provenance.c.source_sample_ids,
                        merge_provenance.c.source_file_hashes,
                    )
                ).one()
                remapped_nested_sources = json.loads(nested_provenance.source_sample_ids)
                restored_nested_hashes = json.loads(nested_provenance.source_file_hashes)

            assert result.samples_restored == 4
            assert remapped_sources == [source_one.id, source_two.id]
            assert restored_source_hashes == ["source-one", "source-two"]
            assert remapped_nested_sources == [source_two.id, merged.id]
            assert restored_nested_hashes == ["source-two", "merged-child"]
            assert 1 not in remapped_sources
            assert 1 not in remapped_nested_sources
            assert list_merged_children(target_registry, 1) == []
            assert [child.id for child in list_merged_children(target_registry, merged.id)] == [
                nested.id
            ]
            assert [
                child.id for child in list_merged_children(target_registry, source_one.id)
            ] == [
                nested.id,
                merged.id,
            ]

            local_delete = delete_sample_with_cascade(target_registry, 1)
            assert local_delete is not None
            assert local_delete.deleted_merged_children == []
            assert merged_path.exists()
            assert nested_path.exists()

            restored_source_delete = delete_sample_with_cascade(target_registry, source_one.id)
            assert restored_source_delete is not None
            assert [child.id for child in restored_source_delete.deleted_merged_children] == [
                nested.id,
                merged.id,
            ]
            assert not merged_path.exists()
            assert not nested_path.exists()
            with target_registry.reference_engine.connect() as conn:
                surviving_ids = set(conn.execute(sa.select(samples.c.id)).scalars())
            assert source_two.id in surviving_ids
            assert merged.id not in surviving_ids
            assert nested.id not in surviving_ids
        finally:
            target_registry.dispose_all()

    def test_merged_dependency_validation_handles_deep_acyclic_archive(self) -> None:
        """Archive-controlled nesting depth must not consume Python stack frames."""
        from backend.api.routes.setup import _validate_merged_dependency_graph

        dependency_graph = {
            sample_id: ([] if sample_id == 0 else [sample_id - 1]) for sample_id in range(1_500)
        }

        _validate_merged_dependency_graph(dependency_graph)

    def test_registry_insert_rolls_back_prompt_cleanup_on_later_failure(
        self, tmp_data_dir: Path
    ) -> None:
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        reference_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
        reference_metadata.create_all(reference_engine)
        reference_engine.dispose()

        with _make_client(settings):
            reset_registry()
            from backend.api.routes.setup import _insert_registry_rows
            from backend.db.connection import get_registry

            registry = get_registry()
            with registry.reference_engine.begin() as conn:
                conn.execute(
                    reannotation_prompts.insert(),
                    {
                        "sample_id": 7,
                        "db_name": "clinvar",
                        "db_version": "20260101",
                        "prompt_type": "reclassification",
                        "stale_databases": "[]",
                        "dismissed": True,
                    },
                )

            planned_samples = [
                (
                    "samples/sample_7.db",
                    {
                        "id": 7,
                        "name": "First restored sample",
                        "db_path": "samples/colliding.db",
                    },
                    None,
                ),
                (
                    "samples/sample_8.db",
                    {
                        "id": 8,
                        "name": "Second restored sample",
                        "db_path": "samples/colliding.db",
                    },
                    None,
                ),
            ]
            with pytest.raises(sa.exc.IntegrityError):
                _insert_registry_rows(
                    source_individuals=[],
                    planned_samples=planned_samples,
                )

            with registry.reference_engine.connect() as conn:
                prompt_sample_ids = (
                    conn.execute(sa.select(reannotation_prompts.c.sample_id)).scalars().all()
                )
                restored_count = conn.execute(
                    sa.select(sa.func.count()).select_from(samples).where(samples.c.id.in_([7, 8]))
                ).scalar_one()
            reset_registry()

        assert prompt_sample_ids == [7]
        assert restored_count == 0

    def test_import_cleans_sample_files_when_registry_insert_fails(
        self, tmp_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Moved sample DB files are removed if registry rows cannot be inserted."""
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        _seed_data_dir(tmp_data_dir, settings)

        with _make_client(settings):
            reset_registry()
            _job_id, filename = _run_export(settings, include_refs=False)
            reset_registry()
        archive_content = (settings.downloads_dir / filename).read_bytes()

        fresh_dir = tmp_path / "failed_restore"
        fresh_dir.mkdir()
        (fresh_dir / "samples").mkdir()
        (fresh_dir / "downloads").mkdir()
        (fresh_dir / "logs").mkdir()
        fresh_settings = Settings(data_dir=fresh_dir, wal_mode=False)

        engine = sa.create_engine(f"sqlite:///{fresh_settings.reference_db_path}")
        reference_metadata.create_all(engine)
        engine.dispose()

        def _boom(**_kwargs):
            raise sa.exc.IntegrityError("insert", {}, Exception("simulated failure"))

        monkeypatch.setattr("backend.api.routes.setup._insert_registry_rows", _boom)

        with _make_client(fresh_settings):
            reset_registry()
            from backend.api.routes.setup import import_backup

            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(
                    import_backup(_UploadBytes(filename=filename, content=archive_content))
                )
            reset_registry()

        assert exc_info.value.status_code == 500
        assert not (fresh_dir / "samples" / "sample_1.db").exists()
        assert not (fresh_dir / "samples" / "sample_2.db").exists()

        engine = sa.create_engine(f"sqlite:///{fresh_settings.reference_db_path}")
        try:
            with engine.connect() as conn:
                sample_count = conn.execute(
                    sa.select(sa.func.count()).select_from(samples)
                ).scalar_one()
        finally:
            engine.dispose()

        assert sample_count == 0


def test_backup_includes_home_config_for_relocated_install(tmp_path: Path) -> None:
    """A relocated data_dir backup still pulls config.toml from the home dir.

    Regression: backup read data_dir/config.toml, but config.toml lives at
    config_toml_path() (DEFAULT_DATA_DIR, the home dir). For a relocated install
    (data_dir != home) the user's config would be silently omitted from the
    archive — and lost on restore.
    """
    home = tmp_path / "home"
    home.mkdir()
    relocated = tmp_path / "store"
    (relocated / "samples").mkdir(parents=True)
    (relocated / "downloads").mkdir(parents=True)

    settings = Settings(data_dir=relocated, wal_mode=False)
    eng = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(eng)
    eng.dispose()

    # config.toml at HOME; the sample under the relocated data_dir.
    (home / "config.toml").write_text('[yeliztli]\npubmed_email = "x@y.com"\n', encoding="utf-8")
    (relocated / "samples" / "sample_1.db").write_bytes(b"s" * 100)

    with (
        patch("backend.config.DEFAULT_DATA_DIR", home),
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.tasks.huey_tasks.get_settings", return_value=settings),
        patch("backend.api.routes.backup.get_settings", return_value=settings),
    ):
        reset_registry()
        try:
            _, filename = _run_export(settings)
            assert filename is not None
            with tarfile.open(settings.downloads_dir / filename, "r:gz") as tf:
                names = tf.getnames()
            assert "config.toml" in names  # pulled from home, not the relocated data_dir
            assert any(n.startswith("samples/") for n in names)
        finally:
            reset_registry()
