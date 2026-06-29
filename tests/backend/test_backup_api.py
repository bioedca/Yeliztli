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
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.tables import individuals, reference_metadata, samples

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
        finally:
            engine.dispose()

        assert [(row.id, row.name, row.db_path) for row in rows] == [
            (1, "Local sample one", "samples/sample_1.db"),
            (2, "Custom sample one", "samples/sample_2.db"),
            (3, "Custom sample two", "samples/sample_3.db"),
        ]

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
