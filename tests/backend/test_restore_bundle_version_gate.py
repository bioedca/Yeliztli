"""Tests for the backup-restore bundle-version gate (Plan §7.6, ADNA-00f).

Locks the contract on ``POST /api/setup/import-backup``:

* Pre-flight inspection reads each archived per-sample DB's recorded
  ``annotation_state.vep_bundle_version`` (or treats a missing table as
  ``v1.0.0``) and compares against the installed
  ``database_versions['vep_bundle'].version``.
* A major-version mismatch in **either direction** halts the restore
  with HTTP 409 — nothing is written to ``data_dir``.
* On a successful restore, every per-sample DB receives the three-step
  idempotent upgrade: ``_add_missing_columns`` → ``create_all`` →
  ``INSERT OR IGNORE vep_bundle_version='v1.0.0'``.
"""

from __future__ import annotations

import io
import tarfile
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
    reference_metadata,
)

_PATCHES = (
    "backend.main.get_settings",
    "backend.db.connection.get_settings",
    "backend.api.routes.backup.get_settings",
    "backend.tasks.huey_tasks.get_settings",
    "backend.api.routes.setup.get_settings",
)


def _make_client_ctx(settings: Settings):
    from contextlib import ExitStack

    stack = ExitStack()
    for target in _PATCHES:
        stack.enter_context(patch(target, return_value=settings))
    # Restore extracts config.toml to config_toml_path() (DEFAULT_DATA_DIR); pin
    # it to the temp data dir so these tests never write the real ~/.yeliztli.
    stack.enter_context(patch("backend.config.DEFAULT_DATA_DIR", settings.data_dir))
    return stack


def _seed_reference_db(settings: Settings, vep_version: str | None) -> None:
    """Create reference.db; seed vep_bundle row only when ``vep_version``."""
    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    try:
        reference_metadata.create_all(engine)
        if vep_version is not None:
            with engine.begin() as conn:
                conn.execute(
                    database_versions.insert().values(
                        db_name="vep_bundle",
                        version=vep_version,
                        downloaded_at=datetime.now(UTC),
                    )
                )
    finally:
        engine.dispose()


def _build_sample_db(
    tmp_path: Path,
    name: str,
    *,
    bundle_version: str | None,
    include_state_table: bool = True,
) -> Path:
    """Materialise a per-sample SQLite file with the given recorded version.

    ``bundle_version=None`` + ``include_state_table=True`` → table exists
    but the ``vep_bundle_version`` row is absent.

    ``include_state_table=False`` → emulates a pre-Phase-0 backup; the
    ``annotation_state`` table does not exist at all.
    """
    db_path = tmp_path / name
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        if include_state_table:
            create_sample_tables(engine)
            if bundle_version is not None:
                with engine.begin() as conn:
                    conn.execute(
                        annotation_state.insert().values(
                            key="vep_bundle_version",
                            value=bundle_version,
                        )
                    )
        else:
            # Pre-Phase-0 shape: a real SQLite DB without ``annotation_state``.
            with engine.begin() as conn:
                conn.execute(sa.text("CREATE TABLE _placeholder (id INTEGER)"))
    finally:
        engine.dispose()
    return db_path


def _build_archive(sample_paths: list[Path], include_config: bool = True) -> bytes:
    """Pack the supplied sample DBs into a .tar.gz suitable for restore."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        if include_config:
            cfg = b'[yeliztli]\ndata_dir = "/tmp/test"\n'
            info = tarfile.TarInfo(name="config.toml")
            info.size = len(cfg)
            tf.addfile(info, io.BytesIO(cfg))
        for path in sample_paths:
            tf.add(path, arcname=f"samples/{path.name}")
    return buf.getvalue()


def _post_import(tc: TestClient, archive_bytes: bytes, filename: str = "backup.tar.gz"):
    return tc.post(
        "/api/setup/import-backup",
        files={
            "file": (
                filename,
                io.BytesIO(archive_bytes),
                "application/gzip",
            )
        },
    )


@pytest.fixture
def restore_env(tmp_data_dir: Path, tmp_path: Path):
    """Yield a configured settings + helper-paths for restore tests."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    src_dir = tmp_path / "backup_src"
    src_dir.mkdir()
    yield {"settings": settings, "src_dir": src_dir, "tmp_data_dir": tmp_data_dir}


# ─── Pre-flight gate ─────────────────────────────────────────────────


class TestRestoreBundleVersionGate:
    def test_explicit_v1_backup_against_installed_v2_blocks(self, restore_env):
        """Backup recorded as v1.0.0, installed v2.0.0 → 409 mismatch."""
        settings = restore_env["settings"]
        _seed_reference_db(settings, vep_version="v2.0.0")
        sample_db = _build_sample_db(
            restore_env["src_dir"], "sample_1.db", bundle_version="v1.0.0"
        )
        archive = _build_archive([sample_db])

        with _make_client_ctx(settings):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                resp = _post_import(tc, archive)
            reset_registry()

        assert resp.status_code == 409
        body = resp.json()
        # FastAPI nests structured dict bodies under ``detail``.
        detail = body.get("detail", body)
        assert detail["error"] == "bundle_version_mismatch"
        assert detail["installed_version"] == "v2.0.0"
        assert detail["backup_version"] == "v1.0.0"
        assert detail["direction"] == "backup_below_installed"

        # Critical Plan §7.6 invariant: nothing written to data_dir on a
        # mismatch — the restore path must be transactional w.r.t. the
        # extraction step.
        restored_samples = list((settings.data_dir / "samples").glob("sample_*.db"))
        assert restored_samples == []
        assert not (settings.data_dir / "config.toml").exists()

    def test_backup_v2_against_installed_v1_blocks_opposite_direction(self, restore_env):
        """Backup v2.0.0 against installed v1.0.0 → 409, ``backup_above_installed``."""
        settings = restore_env["settings"]
        _seed_reference_db(settings, vep_version="v1.0.0")
        sample_db = _build_sample_db(
            restore_env["src_dir"], "sample_1.db", bundle_version="v2.0.0"
        )
        archive = _build_archive([sample_db])

        with _make_client_ctx(settings):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                resp = _post_import(tc, archive)
            reset_registry()

        assert resp.status_code == 409
        detail = resp.json().get("detail")
        assert detail["direction"] == "backup_above_installed"

    def test_match_success_runs_three_step_upgrade(self, restore_env):
        """Backup v2.0.0 + installed v2.0.0 → 200; post-restore upgrade fires."""
        settings = restore_env["settings"]
        _seed_reference_db(settings, vep_version="v2.0.0")
        sample_db = _build_sample_db(
            restore_env["src_dir"], "sample_1.db", bundle_version="v2.0.0"
        )
        archive = _build_archive([sample_db])

        with _make_client_ctx(settings):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                resp = _post_import(tc, archive)
            reset_registry()

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["samples_restored"] == 1
        assert body["config_restored"] is True

        restored_db = settings.data_dir / "samples" / "sample_1.db"
        assert restored_db.exists()
        # Three-step upgrade leaves annotation_state populated.
        engine = sa.create_engine(f"sqlite:///{restored_db}")
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    sa.text("SELECT value FROM annotation_state WHERE key = 'vep_bundle_version'")
                ).fetchone()
        finally:
            engine.dispose()
        # Existing v2.0.0 row is preserved (INSERT OR IGNORE is idempotent).
        assert row is not None
        assert row[0] == "v2.0.0"

    def test_pre_phase0_backup_against_installed_v1_succeeds_and_backfills(self, restore_env):
        """Pre-Phase-0 backup (no ``annotation_state``) + installed v1.0.0 →
        falls back to v1.0.0, restore succeeds, post-restore upgrade adds the
        table and seeds the bundle-version row.
        """
        settings = restore_env["settings"]
        _seed_reference_db(settings, vep_version="v1.0.0")
        sample_db = _build_sample_db(
            restore_env["src_dir"],
            "sample_1.db",
            bundle_version=None,
            include_state_table=False,
        )
        archive = _build_archive([sample_db])

        with _make_client_ctx(settings):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                resp = _post_import(tc, archive)
            reset_registry()

        assert resp.status_code == 200
        restored_db = settings.data_dir / "samples" / "sample_1.db"
        assert restored_db.exists()

        engine = sa.create_engine(f"sqlite:///{restored_db}")
        try:
            inspector = sa.inspect(engine)
            assert "annotation_state" in inspector.get_table_names()
            with engine.connect() as conn:
                row = conn.execute(
                    sa.text("SELECT value FROM annotation_state WHERE key = 'vep_bundle_version'")
                ).fetchone()
        finally:
            engine.dispose()

        assert row is not None
        assert row[0] == "v1.0.0"  # backfilled by migration-008 semantics

    def test_pre_phase0_backup_against_installed_v2_blocks(self, restore_env):
        """Missing ``annotation_state`` is treated as v1.0.0 → 409 against v2."""
        settings = restore_env["settings"]
        _seed_reference_db(settings, vep_version="v2.0.0")
        sample_db = _build_sample_db(
            restore_env["src_dir"],
            "sample_1.db",
            bundle_version=None,
            include_state_table=False,
        )
        archive = _build_archive([sample_db])

        with _make_client_ctx(settings):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                resp = _post_import(tc, archive)
            reset_registry()

        assert resp.status_code == 409
        detail = resp.json().get("detail")
        assert detail["backup_version"] == "v1.0.0"
        assert detail["direction"] == "backup_below_installed"

    def test_no_installed_bundle_allows_any_backup(self, restore_env):
        """Fresh install (no ``database_versions['vep_bundle']`` row) skips
        the comparison — the bundle-download step happens later in the
        wizard.
        """
        settings = restore_env["settings"]
        _seed_reference_db(settings, vep_version=None)
        sample_db = _build_sample_db(
            restore_env["src_dir"], "sample_1.db", bundle_version="v1.0.0"
        )
        archive = _build_archive([sample_db])

        with _make_client_ctx(settings):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                resp = _post_import(tc, archive)
            reset_registry()

        assert resp.status_code == 200
        assert resp.json()["samples_restored"] == 1

    def test_idempotent_three_step_upgrade_on_repeat_restore(self, restore_env):
        """Re-running the upgrade on an already-upgraded DB is a no-op."""
        settings = restore_env["settings"]
        _seed_reference_db(settings, vep_version="v2.0.0")
        sample_db = _build_sample_db(
            restore_env["src_dir"], "sample_1.db", bundle_version="v2.0.0"
        )
        archive = _build_archive([sample_db])

        with _make_client_ctx(settings):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                first = _post_import(tc, archive)
                second = _post_import(tc, archive)
            reset_registry()

        assert first.status_code == 200
        assert second.status_code == 200

        restored_db = settings.data_dir / "samples" / "sample_1.db"
        engine = sa.create_engine(f"sqlite:///{restored_db}")
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    sa.text("SELECT value FROM annotation_state WHERE key = 'vep_bundle_version'")
                ).fetchall()
        finally:
            engine.dispose()
        # INSERT OR IGNORE prevents duplicates regardless of restore count.
        assert len(rows) == 1
        assert rows[0][0] == "v2.0.0"
