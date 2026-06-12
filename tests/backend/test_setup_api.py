"""Tests for the setup wizard API (P1-19a, P1-19b, P1-19e).

Covers:
- GET /api/setup/status — first-launch detection
- GET /api/setup/disclaimer — disclaimer text retrieval
- POST /api/setup/accept-disclaimer — disclaimer acceptance persistence
- GET /api/setup/detect-existing — auto-detect existing installation
- POST /api/setup/import-backup — import from .tar.gz archive
- Edge cases: already accepted, data dir creation, bad archives
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.tables import reference_metadata
from backend.disclaimers import (
    CANCER_DISCLAIMER_TEXT,
    CANCER_DISCLAIMER_TITLE,
    GLOBAL_DISCLAIMER_ACCEPT_LABEL,
    GLOBAL_DISCLAIMER_TEXT,
    GLOBAL_DISCLAIMER_TITLE,
)


@pytest.fixture
def setup_client(tmp_data_dir: Path) -> TestClient:
    """FastAPI TestClient with patched settings for setup API tests."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    # Create reference.db so the registry can initialize
    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.api.routes.setup.get_settings", return_value=settings),
        patch("backend.api.routes.databases.get_settings", return_value=settings),
    ):
        reset_registry()

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc

        reset_registry()


@pytest.fixture
def setup_settings(tmp_data_dir: Path) -> Settings:
    """Settings instance for direct inspection."""
    return Settings(data_dir=tmp_data_dir, wal_mode=False)


# ═══════════════════════════════════════════════════════════════════════
# GET /api/setup/status
# ═══════════════════════════════════════════════════════════════════════


class TestSetupStatus:
    """Tests for the setup status endpoint."""

    def test_fresh_install_needs_setup(self, setup_client: TestClient) -> None:
        """Fresh install (no disclaimer, no DBs) should need setup."""
        resp = setup_client.get("/api/setup/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["needs_setup"] is True
        assert data["disclaimer_accepted"] is False
        assert data["has_databases"] is False
        assert data["has_samples"] is False

    def test_disclaimer_accepted_still_needs_setup_without_dbs(
        self, setup_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """After disclaimer accepted but no DBs, still needs setup."""
        flag_path = tmp_data_dir / ".disclaimer_accepted"
        flag_path.write_text('{"accepted_at": "2026-01-01T00:00:00", "version": "1.0"}')

        resp = setup_client.get("/api/setup/status")
        data = resp.json()
        assert data["disclaimer_accepted"] is True
        assert data["has_databases"] is False
        assert data["needs_setup"] is True

    def test_complete_setup_no_longer_needed(
        self, setup_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """With disclaimer accepted and DBs present, setup is complete."""
        flag_path = tmp_data_dir / ".disclaimer_accepted"
        flag_path.write_text('{"accepted_at": "2026-01-01T00:00:00", "version": "1.0"}')
        # Use a standalone DB file (gnomad) to mark as having databases
        (tmp_data_dir / "gnomad_af.db").write_text("fake")

        resp = setup_client.get("/api/setup/status")
        data = resp.json()
        assert data["disclaimer_accepted"] is True
        assert data["has_databases"] is True
        assert data["needs_setup"] is False

    def test_complete_setup_with_reference_db_versions(
        self, setup_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """With disclaimer accepted and database_versions entries, setup is complete."""
        flag_path = tmp_data_dir / ".disclaimer_accepted"
        flag_path.write_text('{"accepted_at": "2026-01-01T00:00:00", "version": "1.0"}')

        # Insert a database_versions entry for a reference.db-resident DB
        from backend.db.tables import database_versions

        ref_path = tmp_data_dir / "reference.db"
        engine = sa.create_engine(f"sqlite:///{ref_path}")
        with engine.begin() as conn:
            conn.execute(database_versions.insert().values(db_name="clinvar", version="20260101"))
        engine.dispose()

        resp = setup_client.get("/api/setup/status")
        data = resp.json()
        assert data["disclaimer_accepted"] is True
        assert data["has_databases"] is True
        assert data["needs_setup"] is False

    def test_has_samples_detection(self, setup_client: TestClient, tmp_data_dir: Path) -> None:
        """Detect existing sample databases."""
        samples_dir = tmp_data_dir / "samples"
        samples_dir.mkdir(exist_ok=True)
        (samples_dir / "sample_abc123.db").write_text("fake")

        resp = setup_client.get("/api/setup/status")
        data = resp.json()
        assert data["has_samples"] is True

    def test_data_dir_in_response(self, setup_client: TestClient, tmp_data_dir: Path) -> None:
        """Response includes the data directory path."""
        resp = setup_client.get("/api/setup/status")
        data = resp.json()
        assert data["data_dir"] == str(tmp_data_dir)


# ═══════════════════════════════════════════════════════════════════════
# GET /api/setup/disclaimer
# ═══════════════════════════════════════════════════════════════════════


class TestGetDisclaimer:
    """Tests for the disclaimer text endpoint."""

    def test_returns_disclaimer_text(self, setup_client: TestClient) -> None:
        """Should return the full disclaimer content."""
        resp = setup_client.get("/api/setup/disclaimer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == GLOBAL_DISCLAIMER_TITLE
        assert data["text"] == GLOBAL_DISCLAIMER_TEXT
        assert data["accept_label"] == GLOBAL_DISCLAIMER_ACCEPT_LABEL

    def test_disclaimer_text_not_empty(self, setup_client: TestClient) -> None:
        """Disclaimer text should be substantial."""
        resp = setup_client.get("/api/setup/disclaimer")
        data = resp.json()
        assert len(data["text"]) > 500
        assert "educational" in data["text"].lower()
        assert "research" in data["text"].lower()


# ═══════════════════════════════════════════════════════════════════════
# POST /api/setup/accept-disclaimer
# ═══════════════════════════════════════════════════════════════════════


class TestAcceptDisclaimer:
    """Tests for the disclaimer acceptance endpoint."""

    def test_accept_creates_flag_file(self, setup_client: TestClient, tmp_data_dir: Path) -> None:
        """Accepting the disclaimer should persist a flag file."""
        resp = setup_client.post("/api/setup/accept-disclaimer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] is True
        assert "accepted_at" in data

        flag_path = tmp_data_dir / ".disclaimer_accepted"
        assert flag_path.exists()
        flag_data = json.loads(flag_path.read_text())
        assert "accepted_at" in flag_data
        assert flag_data["version"] == "1.0"

    def test_accept_idempotent(
        self,
        setup_client: TestClient,
    ) -> None:
        """Accepting twice should succeed (overwrite the flag)."""
        resp1 = setup_client.post("/api/setup/accept-disclaimer")
        assert resp1.status_code == 200

        resp2 = setup_client.post("/api/setup/accept-disclaimer")
        assert resp2.status_code == 200

    def test_accept_changes_status(
        self,
        setup_client: TestClient,
    ) -> None:
        """After accepting, status should reflect disclaimer_accepted=True."""
        resp = setup_client.get("/api/setup/status")
        assert resp.json()["disclaimer_accepted"] is False

        setup_client.post("/api/setup/accept-disclaimer")

        resp = setup_client.get("/api/setup/status")
        assert resp.json()["disclaimer_accepted"] is True

    def test_accept_creates_data_dir_if_missing(
        self,
        tmp_data_dir: Path,
    ) -> None:
        """Accept should create data_dir if it doesn't exist yet."""
        import asyncio

        from backend.api.routes.setup import accept_disclaimer

        # Use a sub-directory that doesn't exist yet
        new_data_dir = tmp_data_dir / "nonexistent_subdir"
        settings = Settings(data_dir=new_data_dir, wal_mode=False)

        with patch("backend.api.routes.setup.get_settings", return_value=settings):
            result = asyncio.run(accept_disclaimer())
            assert result.accepted is True
            assert new_data_dir.exists()
            assert (new_data_dir / ".disclaimer_accepted").exists()


# ═══════════════════════════════════════════════════════════════════════
# Unit tests for disclaimers module
# ═══════════════════════════════════════════════════════════════════════


class TestDisclaimersModule:
    """Tests for the hardcoded disclaimer text."""

    def test_global_disclaimer_mentions_key_topics(self) -> None:
        """Global disclaimer should cover essential topics."""
        text = GLOBAL_DISCLAIMER_TEXT.lower()
        assert "not a diagnostic tool" in text
        assert "healthcare provider" in text or "genetic counselor" in text
        assert "educational" in text
        assert "research" in text
        assert "privacy" in text

    def test_global_disclaimer_title_not_empty(self) -> None:
        assert len(GLOBAL_DISCLAIMER_TITLE) > 0

    def test_accept_label_not_empty(self) -> None:
        assert len(GLOBAL_DISCLAIMER_ACCEPT_LABEL) > 0

    def test_cancer_disclaimer_mentions_key_topics(self) -> None:
        """Cancer disclaimer should cover essential cancer-specific topics (P3-17)."""
        text = CANCER_DISCLAIMER_TEXT.lower()
        assert "predisposition" in text
        assert "genetic counselor" in text or "medical geneticist" in text
        assert "polygenic risk" in text or "prs" in text
        assert "clinical" in text
        assert "cancer" in text

    def test_cancer_disclaimer_title_not_empty(self) -> None:
        """Cancer disclaimer title should be non-empty (P3-17)."""
        assert len(CANCER_DISCLAIMER_TITLE) > 0

    def test_cancer_disclaimer_substantial_length(self) -> None:
        """Cancer disclaimer should be substantial (P3-17)."""
        assert len(CANCER_DISCLAIMER_TEXT) > 500

    def test_cancer_disclaimer_includes_resources(self) -> None:
        """Cancer disclaimer should include professional resource links (P3-17)."""
        text = CANCER_DISCLAIMER_TEXT
        assert "cancer.gov" in text
        assert "nsgc.org" in text or "findageneticcounselor" in text
        assert "facingourrisk.org" in text


# ═══════════════════════════════════════════════════════════════════════
# Helpers for P1-19b tests
# ═══════════════════════════════════════════════════════════════════════


def _create_backup_archive(
    tmp_path: Path,
    *,
    include_config: bool = False,
    include_disclaimer: bool = False,
    num_samples: int = 2,
    extra_entries: list[tuple[str, bytes]] | None = None,
) -> Path:
    """Create a valid .tar.gz backup archive for testing."""
    archive_path = tmp_path / "backup.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tf:
        # samples directory
        samples_info = tarfile.TarInfo(name="samples")
        samples_info.type = tarfile.DIRTYPE
        samples_info.mode = 0o755
        tf.addfile(samples_info)

        for i in range(num_samples):
            content = f"sample_db_content_{i}".encode()
            info = tarfile.TarInfo(name=f"samples/sample_{i:03d}.db")
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))

        if include_config:
            config_content = b'[yeliztli]\ntheme = "dark"\n'
            info = tarfile.TarInfo(name="config.toml")
            info.size = len(config_content)
            tf.addfile(info, io.BytesIO(config_content))

        if include_disclaimer:
            disc_content = b'{"accepted_at": "2026-01-01T00:00:00", "version": "1.0"}'
            info = tarfile.TarInfo(name=".disclaimer_accepted")
            info.size = len(disc_content)
            tf.addfile(info, io.BytesIO(disc_content))

        if extra_entries:
            for name, content in extra_entries:
                info = tarfile.TarInfo(name=name)
                info.size = len(content)
                tf.addfile(info, io.BytesIO(content))

    return archive_path


# ═══════════════════════════════════════════════════════════════════════
# GET /api/setup/detect-existing
# ═══════════════════════════════════════════════════════════════════════


class TestDetectExisting:
    """Tests for the auto-detect existing installation endpoint."""

    def test_fresh_install_no_existing(self, setup_client: TestClient) -> None:
        """Fresh install should not detect existing installation."""
        resp = setup_client.get("/api/setup/detect-existing")
        assert resp.status_code == 200
        data = resp.json()
        assert data["existing_found"] is False
        assert data["has_config"] is False
        assert data["has_samples"] is False
        assert data["has_databases"] is False

    def test_detect_config_toml(self, setup_client: TestClient, tmp_data_dir: Path) -> None:
        """Should detect existing config.toml."""
        (tmp_data_dir / "config.toml").write_text("[yeliztli]")

        resp = setup_client.get("/api/setup/detect-existing")
        data = resp.json()
        assert data["existing_found"] is True
        assert data["has_config"] is True

    def test_detect_samples(self, setup_client: TestClient, tmp_data_dir: Path) -> None:
        """Should detect existing sample databases."""
        samples_dir = tmp_data_dir / "samples"
        samples_dir.mkdir(exist_ok=True)
        (samples_dir / "sample_test.db").write_text("fake")

        resp = setup_client.get("/api/setup/detect-existing")
        data = resp.json()
        assert data["existing_found"] is True
        assert data["has_samples"] is True

    def test_detect_databases(self, setup_client: TestClient, tmp_data_dir: Path) -> None:
        """Should detect existing reference databases."""
        (tmp_data_dir / "gnomad_af.db").write_text("fake")

        resp = setup_client.get("/api/setup/detect-existing")
        data = resp.json()
        assert data["existing_found"] is True
        assert data["has_databases"] is True

    def test_detect_full_install(self, setup_client: TestClient, tmp_data_dir: Path) -> None:
        """Should detect a complete existing installation."""
        (tmp_data_dir / "config.toml").write_text("[yeliztli]")
        (tmp_data_dir / "gnomad_af.db").write_text("fake")
        samples_dir = tmp_data_dir / "samples"
        samples_dir.mkdir(exist_ok=True)
        (samples_dir / "sample_abc.db").write_text("fake")

        resp = setup_client.get("/api/setup/detect-existing")
        data = resp.json()
        assert data["existing_found"] is True
        assert data["has_config"] is True
        assert data["has_samples"] is True
        assert data["has_databases"] is True

    def test_response_includes_data_dir(
        self, setup_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """Response should include data directory path."""
        resp = setup_client.get("/api/setup/detect-existing")
        data = resp.json()
        assert data["data_dir"] == str(tmp_data_dir)


# ═══════════════════════════════════════════════════════════════════════
# POST /api/setup/import-backup
# ═══════════════════════════════════════════════════════════════════════


class TestImportBackup:
    """Tests for the backup import endpoint."""

    def test_import_valid_archive(
        self, setup_client: TestClient, tmp_data_dir: Path, tmp_path: Path
    ) -> None:
        """Should successfully import a valid .tar.gz archive."""
        archive = _create_backup_archive(tmp_path, num_samples=2)

        with archive.open("rb") as f:
            resp = setup_client.post(
                "/api/setup/import-backup",
                files={"file": ("backup.tar.gz", f, "application/gzip")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["samples_restored"] == 2
        assert data["config_restored"] is False

        # Verify files were extracted
        assert (tmp_data_dir / "samples" / "sample_000.db").exists()
        assert (tmp_data_dir / "samples" / "sample_001.db").exists()

    def test_import_with_config(
        self, setup_client: TestClient, tmp_data_dir: Path, tmp_path: Path
    ) -> None:
        """Should restore config.toml if included."""
        archive = _create_backup_archive(tmp_path, include_config=True, num_samples=1)

        with archive.open("rb") as f:
            resp = setup_client.post(
                "/api/setup/import-backup",
                files={"file": ("backup.tar.gz", f, "application/gzip")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["config_restored"] is True
        assert (tmp_data_dir / "config.toml").exists()

    def test_import_with_disclaimer(
        self, setup_client: TestClient, tmp_data_dir: Path, tmp_path: Path
    ) -> None:
        """Should restore disclaimer accepted flag if included."""
        archive = _create_backup_archive(tmp_path, include_disclaimer=True, num_samples=1)

        with archive.open("rb") as f:
            resp = setup_client.post(
                "/api/setup/import-backup",
                files={"file": ("backup.tar.gz", f, "application/gzip")},
            )

        assert resp.status_code == 200
        assert (tmp_data_dir / ".disclaimer_accepted").exists()

    def test_reject_non_tar_gz(self, setup_client: TestClient) -> None:
        """Should reject non-.tar.gz files."""
        resp = setup_client.post(
            "/api/setup/import-backup",
            files={"file": ("data.zip", b"fake", "application/zip")},
        )
        assert resp.status_code == 400
        assert "tar.gz" in resp.json()["detail"].lower()

    def test_reject_empty_file(self, setup_client: TestClient, tmp_path: Path) -> None:
        """Should reject an empty file."""
        empty_file = tmp_path / "empty.tar.gz"
        empty_file.write_bytes(b"")

        with empty_file.open("rb") as f:
            resp = setup_client.post(
                "/api/setup/import-backup",
                files={"file": ("empty.tar.gz", f, "application/gzip")},
            )
        assert resp.status_code == 400

    def test_reject_corrupt_archive(self, setup_client: TestClient) -> None:
        """Should reject corrupt archive data."""
        resp = setup_client.post(
            "/api/setup/import-backup",
            files={"file": ("bad.tar.gz", b"not a tar file", "application/gzip")},
        )
        assert resp.status_code == 400
        assert "failed to read" in resp.json()["detail"].lower()

    def test_reject_archive_without_samples(
        self, setup_client: TestClient, tmp_path: Path
    ) -> None:
        """Should reject archives that don't contain a samples directory."""
        archive_path = tmp_path / "no_samples.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            content = b"[yeliztli]"
            info = tarfile.TarInfo(name="config.toml")
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))

        with archive_path.open("rb") as f:
            resp = setup_client.post(
                "/api/setup/import-backup",
                files={"file": ("no_samples.tar.gz", f, "application/gzip")},
            )
        assert resp.status_code == 400
        assert "samples" in resp.json()["detail"].lower()

    def test_reject_archive_with_path_traversal(
        self, setup_client: TestClient, tmp_path: Path
    ) -> None:
        """Should reject archives with path traversal attempts."""
        archive_path = tmp_path / "evil.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            # Add samples dir so structure validation passes
            samples_info = tarfile.TarInfo(name="samples")
            samples_info.type = tarfile.DIRTYPE
            samples_info.mode = 0o755
            tf.addfile(samples_info)

            content = b"evil"
            info = tarfile.TarInfo(name="samples/../../../etc/passwd")
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))

        with archive_path.open("rb") as f:
            resp = setup_client.post(
                "/api/setup/import-backup",
                files={"file": ("evil.tar.gz", f, "application/gzip")},
            )
        assert resp.status_code == 400
        assert "unsafe" in resp.json()["detail"].lower()

    def test_reject_unexpected_top_level_entries(
        self, setup_client: TestClient, tmp_path: Path
    ) -> None:
        """Should reject archives with unexpected top-level directories."""
        archive = _create_backup_archive(
            tmp_path,
            num_samples=1,
            extra_entries=[("malware/evil.sh", b"#!/bin/bash\nrm -rf /")],
        )

        with archive.open("rb") as f:
            resp = setup_client.post(
                "/api/setup/import-backup",
                files={"file": ("bad.tar.gz", f, "application/gzip")},
            )
        assert resp.status_code == 400
        assert "unexpected" in resp.json()["detail"].lower()

    def test_import_message_format(self, setup_client: TestClient, tmp_path: Path) -> None:
        """Message should describe what was restored."""
        archive = _create_backup_archive(tmp_path, include_config=True, num_samples=3)

        with archive.open("rb") as f:
            resp = setup_client.post(
                "/api/setup/import-backup",
                files={"file": ("backup.tar.gz", f, "application/gzip")},
            )

        data = resp.json()
        assert "3 sample(s)" in data["message"]
        assert "configuration" in data["message"]

    def test_temp_file_cleaned_up(
        self, setup_client: TestClient, tmp_data_dir: Path, tmp_path: Path
    ) -> None:
        """Temp archive file should be cleaned up after import."""
        archive = _create_backup_archive(tmp_path, num_samples=1)

        with archive.open("rb") as f:
            setup_client.post(
                "/api/setup/import-backup",
                files={"file": ("backup.tar.gz", f, "application/gzip")},
            )

        assert not (tmp_data_dir / ".import_backup_tmp.tar.gz").exists()

    def test_tgz_extension_accepted(self, setup_client: TestClient, tmp_path: Path) -> None:
        """Should accept .tgz extension as well."""
        archive = _create_backup_archive(tmp_path, num_samples=1)

        with archive.open("rb") as f:
            resp = setup_client.post(
                "/api/setup/import-backup",
                files={"file": ("backup.tgz", f, "application/gzip")},
            )
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# P1-19c: GET /api/setup/storage-info
# ═══════════════════════════════════════════════════════════════════════


class TestStorageInfo:
    """Tests for the storage info endpoint."""

    def test_returns_storage_info(self, setup_client: TestClient) -> None:
        """Should return storage path and disk space info."""
        resp = setup_client.get("/api/setup/storage-info")
        assert resp.status_code == 200
        data = resp.json()
        assert "data_dir" in data
        assert "free_space_bytes" in data
        assert "free_space_gb" in data
        assert "total_space_bytes" in data
        assert "total_space_gb" in data
        assert data["status"] in ("ok", "warning", "blocked")
        assert "message" in data
        assert isinstance(data["path_exists"], bool)
        assert isinstance(data["path_writable"], bool)

    def test_free_space_positive(self, setup_client: TestClient) -> None:
        """Free and total space should be positive values."""
        resp = setup_client.get("/api/setup/storage-info")
        data = resp.json()
        assert data["free_space_bytes"] > 0
        assert data["total_space_bytes"] > 0
        assert data["free_space_gb"] > 0
        assert data["total_space_gb"] > 0

    def test_path_exists_and_writable(self, setup_client: TestClient, tmp_data_dir: Path) -> None:
        """Temp data dir should exist and be writable."""
        resp = setup_client.get("/api/setup/storage-info")
        data = resp.json()
        assert data["path_exists"] is True
        assert data["path_writable"] is True

    def test_data_dir_matches_settings(self, setup_client: TestClient, tmp_data_dir: Path) -> None:
        """Returned data_dir should match the configured path."""
        resp = setup_client.get("/api/setup/storage-info")
        data = resp.json()
        assert data["data_dir"] == str(tmp_data_dir)

    def test_blocked_on_low_space(self, setup_client: TestClient) -> None:
        """Should report 'blocked' when disk space < 5 GB."""
        # Mock shutil.disk_usage to return very low space
        low_usage = type("Usage", (), {"free": 2 * 1024**3, "total": 10 * 1024**3})()
        with patch("backend.api.routes.setup.shutil.disk_usage", return_value=low_usage):
            resp = setup_client.get("/api/setup/storage-info")
        data = resp.json()
        assert data["status"] == "blocked"
        assert "insufficient" in data["message"].lower()

    def test_warning_on_moderate_space(self, setup_client: TestClient) -> None:
        """Should report 'warning' when disk space between 5–10 GB."""
        mid_usage = type("Usage", (), {"free": 7 * 1024**3, "total": 20 * 1024**3})()
        with patch("backend.api.routes.setup.shutil.disk_usage", return_value=mid_usage):
            resp = setup_client.get("/api/setup/storage-info")
        data = resp.json()
        assert data["status"] == "warning"
        assert "low" in data["message"].lower()

    def test_ok_on_sufficient_space(self, setup_client: TestClient) -> None:
        """Should report 'ok' when disk space >= 10 GB."""
        ok_usage = type("Usage", (), {"free": 50 * 1024**3, "total": 100 * 1024**3})()
        with patch("backend.api.routes.setup.shutil.disk_usage", return_value=ok_usage):
            resp = setup_client.get("/api/setup/storage-info")
        data = resp.json()
        assert data["status"] == "ok"
        assert "sufficient" in data["message"].lower()


# ═══════════════════════════════════════════════════════════════════════
# P1-19c: POST /api/setup/set-storage-path
# ═══════════════════════════════════════════════════════════════════════


class TestSetStoragePath:
    """Tests for the set-storage-path endpoint."""

    def test_set_valid_path(self, setup_client: TestClient, tmp_path: Path) -> None:
        """Should successfully set a valid storage path."""
        new_path = tmp_path / "new_yeliztli"
        resp = setup_client.post(
            "/api/setup/set-storage-path",
            json={"path": str(new_path)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data_dir"] == str(new_path)
        assert data["free_space_gb"] > 0
        assert data["status"] in ("ok", "warning", "blocked")

    def test_creates_directory_structure(self, setup_client: TestClient, tmp_path: Path) -> None:
        """Should create data dir with samples, downloads, logs subdirs."""
        new_path = tmp_path / "gi_data"
        setup_client.post(
            "/api/setup/set-storage-path",
            json={"path": str(new_path)},
        )
        assert (new_path / "samples").is_dir()
        assert (new_path / "downloads").is_dir()
        assert (new_path / "logs").is_dir()

    def test_does_not_write_data_dir_to_config_toml(self, tmp_path: Path) -> None:
        """Storage path selection must not persist the loader-ignored data_dir key."""
        import asyncio

        from backend.api.routes.setup import SetStoragePathRequest, set_storage_path

        new_path = tmp_path / "gi_config_test"
        asyncio.run(set_storage_path(SetStoragePathRequest(path=str(new_path))))

        config_path = new_path / "config.toml"
        assert not config_path.exists()

    def test_preserves_existing_config(self, tmp_path: Path) -> None:
        """Should preserve other settings in existing config.toml."""
        import asyncio

        from backend.api.routes.setup import SetStoragePathRequest, set_storage_path

        new_path = tmp_path / "gi_preserve"
        new_path.mkdir(parents=True)
        config_path = new_path / "config.toml"
        config_path.write_text('[yeliztli]\ntheme = "dark"\nlog_level = "DEBUG"\n')

        asyncio.run(set_storage_path(SetStoragePathRequest(path=str(new_path))))

        content = config_path.read_text()
        assert 'theme = "dark"' in content
        assert 'log_level = "DEBUG"' in content
        assert "data_dir" not in content
        assert str(new_path) not in content

    def test_tilde_expansion(
        self,
        setup_client: TestClient,
    ) -> None:
        """Should expand ~ in the path."""
        resp = setup_client.post(
            "/api/setup/set-storage-path",
            json={"path": "~/.yeliztli_test_tilde"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Path should be expanded (no ~)
        assert "~" not in data["data_dir"]
        assert data["success"] is True
        # Clean up
        expanded = Path(data["data_dir"])
        if expanded.exists():
            import shutil

            shutil.rmtree(expanded)

    def test_reject_unwritable_path(
        self,
        setup_client: TestClient,
    ) -> None:
        """Should reject paths that can't be written to."""
        resp = setup_client.post(
            "/api/setup/set-storage-path",
            json={"path": "/root/yeliztli_no_perms"},
        )
        # Should fail with 400 (permission denied)
        assert resp.status_code == 400
        detail = resp.json()["detail"].lower()
        assert "permission" in detail or "cannot" in detail

    def test_idempotent_set(self, setup_client: TestClient, tmp_path: Path) -> None:
        """Setting the same path twice should succeed."""
        new_path = tmp_path / "gi_idempotent"
        resp1 = setup_client.post(
            "/api/setup/set-storage-path",
            json={"path": str(new_path)},
        )
        resp2 = setup_client.post(
            "/api/setup/set-storage-path",
            json={"path": str(new_path)},
        )
        assert resp1.status_code == 200
        assert resp2.status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# P1-19e: GET /api/setup/credentials
# ═══════════════════════════════════════════════════════════════════════


class TestGetCredentials:
    """Tests for the credentials retrieval endpoint."""

    def test_returns_empty_credentials_by_default(self, setup_client: TestClient) -> None:
        """Fresh install should return empty credential strings."""
        resp = setup_client.get("/api/setup/credentials")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pubmed_email"] == ""
        assert data["ncbi_api_key"] == ""
        assert data["omim_api_key"] == ""

    def test_returns_all_credential_fields(self, setup_client: TestClient) -> None:
        """Response should contain all three credential fields."""
        resp = setup_client.get("/api/setup/credentials")
        data = resp.json()
        assert "pubmed_email" in data
        assert "ncbi_api_key" in data
        assert "omim_api_key" in data


# ═══════════════════════════════════════════════════════════════════════
# P1-19e: POST /api/setup/credentials
# ═══════════════════════════════════════════════════════════════════════


class TestSaveCredentials:
    """Tests for the credentials save endpoint."""

    def test_save_credentials(self, setup_client: TestClient, tmp_data_dir: Path) -> None:
        """Should successfully save credentials to config.toml."""
        resp = setup_client.post(
            "/api/setup/credentials",
            json={
                "pubmed_email": "test@example.com",
                "ncbi_api_key": "abc123",
                "omim_api_key": "xyz789",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "saved" in data["message"].lower()

        # Verify config.toml was written
        config_path = tmp_data_dir / "config.toml"
        assert config_path.exists()
        content = config_path.read_text()
        assert 'pubmed_email = "test@example.com"' in content
        assert 'pubmed_api_key = "abc123"' in content
        assert 'omim_api_key = "xyz789"' in content

    def test_save_only_email(self, setup_client: TestClient, tmp_data_dir: Path) -> None:
        """Should save with only the required email."""
        resp = setup_client.post(
            "/api/setup/credentials",
            json={"pubmed_email": "user@domain.com"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

        config_path = tmp_data_dir / "config.toml"
        content = config_path.read_text()
        assert 'pubmed_email = "user@domain.com"' in content

    def test_save_preserves_existing_config(
        self, setup_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """Should preserve existing config entries when saving credentials."""
        config_path = tmp_data_dir / "config.toml"
        config_path.write_text('[yeliztli]\ntheme = "dark"\ndata_dir = "/some/path"\n')

        resp = setup_client.post(
            "/api/setup/credentials",
            json={"pubmed_email": "preserve@test.com"},
        )
        assert resp.status_code == 200

        content = config_path.read_text()
        assert 'theme = "dark"' in content
        assert 'data_dir = "/some/path"' in content
        assert 'pubmed_email = "preserve@test.com"' in content

    def test_save_overwrites_existing_credentials(
        self, setup_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """Should overwrite previously saved credentials."""
        # Save first set
        setup_client.post(
            "/api/setup/credentials",
            json={
                "pubmed_email": "first@test.com",
                "ncbi_api_key": "key1",
                "omim_api_key": "omim1",
            },
        )

        # Save second set
        setup_client.post(
            "/api/setup/credentials",
            json={
                "pubmed_email": "second@test.com",
                "ncbi_api_key": "key2",
                "omim_api_key": "",
            },
        )

        config_path = tmp_data_dir / "config.toml"
        content = config_path.read_text()
        assert 'pubmed_email = "second@test.com"' in content
        assert 'pubmed_api_key = "key2"' in content
        assert 'omim_api_key = ""' in content

    def test_save_creates_data_dir_if_missing(
        self, setup_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """Should create data_dir if it doesn't exist yet."""
        import shutil

        # Remove the data dir (setup_client creates it, so remove it after)
        shutil.rmtree(tmp_data_dir, ignore_errors=True)

        resp = setup_client.post(
            "/api/setup/credentials",
            json={"pubmed_email": "newdir@test.com"},
        )
        assert resp.status_code == 200
        assert tmp_data_dir.exists()
        assert (tmp_data_dir / "config.toml").exists()
