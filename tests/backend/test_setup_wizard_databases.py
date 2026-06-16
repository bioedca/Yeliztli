"""Tests for setup wizard manifest-overrides plumbing in the databases API.

Covers Step 8 of `docs/setup-update-steps.md`: when a download-mode DB has
no SHA-256 in the registry, the dispatch path overrides URL/SHA-256/size
from the bundle manifest. Manifest unreachable → registry defaults survive.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.api.routes import databases as databases_routes
from backend.config import Settings
from backend.db.database_registry import DatabaseInfo, get_database
from backend.db.download_manager import DownloadResult
from backend.db.manifest import BundleManifestEntry
from backend.db.tables import jobs, reference_metadata

# ── Helpers ──────────────────────────────────────────────────────────


def _make_entry(
    *,
    url: str = "https://manifest.example.com/bundle.bed",
    sha256: str = "a" * 64,
    size_bytes: int = 12_345,
) -> BundleManifestEntry:
    return BundleManifestEntry(
        version="v9.9",
        build_date="2026-05-10",
        url=url,
        sha256=sha256,
        size_bytes=size_bytes,
    )


def _make_download_db(*, sha256: str | None = None) -> DatabaseInfo:
    return DatabaseInfo(
        name="encode_ccres",
        display_name="ENCODE cCREs",
        description="test fixture",
        url="https://registry.example.com/old.bed",
        filename="encode_ccres.db",
        expected_size_bytes=1,
        sha256=sha256,
        build_mode="download",
        target_db="standalone",
    )


# ── _apply_manifest_overrides ────────────────────────────────────────


class TestApplyManifestOverrides:
    """Unit tests for `_apply_manifest_overrides` (Step 8)."""

    def test_overrides_applied_when_registry_sha_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = _make_download_db(sha256=None)
        entry = _make_entry(
            url="https://manifest.example.com/new.bed",
            sha256="b" * 64,
            size_bytes=987_654,
        )
        monkeypatch.setattr(databases_routes, "get_bundle_info", lambda name: entry)

        result = databases_routes._apply_manifest_overrides(db)

        assert result.url == "https://manifest.example.com/new.bed"
        assert result.sha256 == "b" * 64
        assert result.expected_size_bytes == 987_654
        # Untouched fields
        assert result.name == db.name
        assert result.filename == db.filename
        assert result.build_mode == "download"
        assert result.target_db == db.target_db

    def test_no_override_when_registry_has_sha(self, monkeypatch: pytest.MonkeyPatch) -> None:
        db = _make_download_db(sha256="c" * 64)
        called: list[str] = []

        def fake_fetch(name: str) -> BundleManifestEntry | None:
            called.append(name)
            return _make_entry()

        monkeypatch.setattr(databases_routes, "get_bundle_info", fake_fetch)

        result = databases_routes._apply_manifest_overrides(db)

        # Identity preserved + manifest never queried (short-circuit)
        assert result is db
        assert called == []

    def test_no_override_when_manifest_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        db = _make_download_db(sha256=None)
        monkeypatch.setattr(databases_routes, "get_bundle_info", lambda name: None)

        result = databases_routes._apply_manifest_overrides(db)

        assert result is db

    def test_no_override_for_pipeline_build_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # dbnsfp is the remaining standalone pipeline DB (gnomad now ships as a
        # bundle), so it keeps this pipeline short-circuit assertion meaningful.
        dbnsfp = get_database("dbnsfp")
        assert dbnsfp is not None
        assert dbnsfp.build_mode == "pipeline"

        called: list[str] = []

        def fake_fetch(name: str) -> BundleManifestEntry | None:
            called.append(name)
            return _make_entry()

        monkeypatch.setattr(databases_routes, "get_bundle_info", fake_fetch)

        result = databases_routes._apply_manifest_overrides(dbnsfp)

        assert result is dbnsfp
        assert called == []

    def test_keeps_registry_url_when_manifest_url_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Bundles like ancestry_pca may ship without a download URL in the manifest;
        # we still want the SHA/size override but should keep the registry URL.
        db = _make_download_db(sha256=None)
        entry = _make_entry(url="", sha256="d" * 64, size_bytes=42)
        monkeypatch.setattr(databases_routes, "get_bundle_info", lambda name: entry)

        result = databases_routes._apply_manifest_overrides(db)

        assert result.url == db.url
        assert result.sha256 == "d" * 64
        assert result.expected_size_bytes == 42


# ── _run_download dispatch integration ───────────────────────────────


class _StartCapture:
    """Stand-in for ``DownloadManager`` recording the args ``start`` is called with."""

    def __init__(self, dest_path: Path) -> None:
        self.dest_path = dest_path
        self.captured: dict[str, object] = {}

    def start(
        self,
        *,
        url: str,
        filename: str,
        expected_sha256: str | None,
        progress_callback: object = None,
    ) -> DownloadResult:
        self.captured = {
            "url": url,
            "filename": filename,
            "expected_sha256": expected_sha256,
            "progress_callback": progress_callback,
        }
        # Simulate a successful download landing at dest_path
        self.dest_path.parent.mkdir(parents=True, exist_ok=True)
        self.dest_path.write_bytes(b"fake-download")
        return DownloadResult(
            download_id=1,
            job_id="dl-test",
            dest_path=self.dest_path,
            total_bytes=len(b"fake-download"),
            sha256=None,
            verified=True,
            error=None,
        )


class TestRunDownloadDispatch:
    """`_run_download` must pass manifest-derived URL/SHA into ``dm.start``."""

    @pytest.fixture
    def engine_with_job(self, tmp_data_dir: Path) -> sa.Engine:
        ref_path = tmp_data_dir / "reference.db"
        engine = sa.create_engine(f"sqlite:///{ref_path}")
        reference_metadata.create_all(engine)
        now = datetime.now(UTC)
        with engine.begin() as conn:
            conn.execute(
                jobs.insert().values(
                    job_id="job-1",
                    sample_id=None,
                    job_type="database_download",
                    status="pending",
                    progress_pct=0.0,
                    message="",
                    created_at=now,
                    updated_at=now,
                )
            )
        yield engine
        engine.dispose()

    def test_run_download_uses_manifest_overrides(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_data_dir: Path,
        engine_with_job: sa.Engine,
    ) -> None:
        db = _make_download_db(sha256=None)
        # Skip the encode_ccres post_download (it would try to load BED into SQLite)
        db = replace(db, post_download=lambda src, dst: dst.write_bytes(b"built"))
        manifest_entry = _make_entry(
            url="https://manifest.example.com/file.bed",
            sha256="e" * 64,
            size_bytes=10,
        )
        monkeypatch.setattr(databases_routes, "get_bundle_info", lambda name: manifest_entry)

        downloads_dir = tmp_data_dir / "downloads"
        capture = _StartCapture(downloads_dir / db.filename)
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

        databases_routes._run_download(
            dm=capture,
            db_info=db,
            job_id="job-1",
            engine=engine_with_job,
            settings=settings,
        )

        assert capture.captured["url"] == "https://manifest.example.com/file.bed"
        assert capture.captured["expected_sha256"] == "e" * 64
        assert capture.captured["filename"] == db.filename

        # Job marked complete
        with engine_with_job.connect() as conn:
            row = conn.execute(sa.select(jobs.c.status).where(jobs.c.job_id == "job-1")).fetchone()
        assert row is not None
        assert row.status == "complete"

    def test_run_download_keeps_registry_sha_when_present(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_data_dir: Path,
        engine_with_job: sa.Engine,
    ) -> None:
        db = _make_download_db(sha256="f" * 64)
        db = replace(db, post_download=lambda src, dst: dst.write_bytes(b"built"))

        # Manifest fetch must NOT be called when registry already has a SHA.
        called: list[str] = []

        def fake_fetch(name: str) -> BundleManifestEntry | None:
            called.append(name)
            return _make_entry(sha256="0" * 64)

        monkeypatch.setattr(databases_routes, "get_bundle_info", fake_fetch)

        downloads_dir = tmp_data_dir / "downloads"
        capture = _StartCapture(downloads_dir / db.filename)
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

        databases_routes._run_download(
            dm=capture,
            db_info=db,
            job_id="job-1",
            engine=engine_with_job,
            settings=settings,
        )

        assert called == []
        assert capture.captured["url"] == db.url
        assert capture.captured["expected_sha256"] == "f" * 64
