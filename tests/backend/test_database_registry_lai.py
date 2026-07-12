"""Tests for ``_extract_lai_bundle`` version recording (Step 5).

After successful extraction, ``_extract_lai_bundle`` must upsert a row into
``database_versions`` so the Update Manager treats the LAI bundle as
installed.  Manifest-driven version/sha256 is preferred; a manifest fetch
failure falls back to ``version="unknown-pre-manifest"``.
"""

from __future__ import annotations

import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.db import manifest as manifest_module
from backend.db.database_registry import DATABASES, _extract_lai_bundle
from backend.db.tables import database_versions, reference_metadata

# The canonical LAI v1.1 bundle SHA-256.  Phase 0i (PR-0z) reset the
# registry-side ``DATABASES["lai_bundle"].sha256`` to ``None`` because that
# leftover value never matched the manifest placeholder; Phase D/Step 32
# (PR-0c) then re-set it to the real v2.0.0 SHA (see
# ``test_lai_bundle_registry_sha256_set_for_phase_d_pr0c``).  This constant only
# documents the value the v1.1 manifest fixtures below carry — it is NOT the
# registry SHA.
LAI_V1_1_SHA256 = "959ed0fd9ebe2ad8fa542776a59ce73072d928c7ce59839ea81d0f1e78a5c18e"
_MINIMAL_LIFTOVER = b"rs1\tchr1\t100\n"

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _build_minimal_lai_tarball(tarball: Path) -> None:
    """Create a tarball with the model and liftover layout the validator expects."""
    with tarfile.open(tarball, "w:gz") as tf:
        for chrom in range(1, 23):
            for fname in ("base_coefs.npz", "metadata.npz", "smoother.json"):
                fpath = f"gnomix_models/chr{chrom}/{fname}"
                info = tarfile.TarInfo(name=fpath)
                payload = b"test"
                info.size = len(payload)
                tf.addfile(info, fileobj=io.BytesIO(payload))
        info = tarfile.TarInfo(name="liftover/array_site_mapping.tsv")
        info.size = len(_MINIMAL_LIFTOVER)
        tf.addfile(info, fileobj=io.BytesIO(_MINIMAL_LIFTOVER))


def _make_data_dir_with_reference(tmp_path: Path) -> Path:
    """Build a tmp data dir containing an empty reference.db with all tables."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    ref_path = data_dir / "reference.db"
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()
    return data_dir


def _write_local_manifest(tmp_path: Path) -> Path:
    """Write a local manifest fixture with a known LAI bundle entry."""
    manifest_path = tmp_path / "manifest.json"
    payload = {
        "schema_version": 1,
        "generated_at": "2026-05-08T00:00:00Z",
        "bundles": {
            "lai_bundle": {
                "version": "v1.1",
                "build_date": "2026-04-07",
                "url": "https://example.test/genomeinsight_lai_bundle_v1.1.tar.gz",
                "sha256": LAI_V1_1_SHA256,
                "size_bytes": 523_801_111,
            },
        },
        "pipeline_pins": {},
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    return manifest_path


@pytest.fixture(autouse=True)
def _clear_manifest_cache():
    """Ensure each test sees an empty in-memory manifest cache."""
    manifest_module.reset_cache()
    yield
    manifest_module.reset_cache()


# ──────────────────────────────────────────────────────────────────────
# Manifest-success path: row uses manifest version + sha256
# ──────────────────────────────────────────────────────────────────────


def test_extract_records_version_from_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = _make_data_dir_with_reference(tmp_path)
    manifest_path = _write_local_manifest(tmp_path)
    monkeypatch.setenv(manifest_module.MANIFEST_PATH_ENV, str(manifest_path))

    tarball = tmp_path / "lai_bundle_src.tar.gz"
    _build_minimal_lai_tarball(tarball)

    dest_path = data_dir / "lai_bundle.tar.gz"
    shutil.copy2(tarball, dest_path)

    _extract_lai_bundle(dest_path, dest_path)

    # Tarball removed; bundle directory present.
    assert not dest_path.exists()
    bundle_dir = data_dir / "lai_bundle"
    assert bundle_dir.is_dir()
    assert (bundle_dir / "gnomix_models" / "chr22" / "smoother.json").exists()

    # Version row written using manifest values.
    engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    try:
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "lai_bundle")
            ).fetchone()
    finally:
        engine.dispose()

    assert row is not None
    assert row.version == "v1.1"
    assert row.checksum_sha256 == LAI_V1_1_SHA256
    # 22 chroms × 3 files × 4 bytes each, plus the minimal liftover mapping.
    assert row.file_size_bytes == 22 * 3 * 4 + len(_MINIMAL_LIFTOVER)
    assert row.downloaded_at is not None


# ──────────────────────────────────────────────────────────────────────
# Manifest-failure path: row records version="unknown-pre-manifest"
# ──────────────────────────────────────────────────────────────────────


def test_extract_records_unknown_when_manifest_unreachable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = _make_data_dir_with_reference(tmp_path)
    # Ensure no env override so the manifest module attempts a remote fetch.
    monkeypatch.delenv(manifest_module.MANIFEST_PATH_ENV, raising=False)

    def _raise(*args, **kwargs):
        raise manifest_module.ManifestFetchError("simulated network failure")

    monkeypatch.setattr(manifest_module, "fetch_manifest", _raise)

    tarball = tmp_path / "lai_bundle_src.tar.gz"
    _build_minimal_lai_tarball(tarball)

    dest_path = data_dir / "lai_bundle.tar.gz"
    shutil.copy2(tarball, dest_path)

    _extract_lai_bundle(dest_path, dest_path)

    engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    try:
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "lai_bundle")
            ).fetchone()
    finally:
        engine.dispose()

    assert row is not None
    assert row.version == "unknown-pre-manifest"
    assert row.checksum_sha256 is None
    assert row.file_size_bytes == 22 * 3 * 4 + len(_MINIMAL_LIFTOVER)


# ──────────────────────────────────────────────────────────────────────
# Re-extracting upserts (no duplicate rows; values refresh)
# ──────────────────────────────────────────────────────────────────────


def test_extract_upserts_existing_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = _make_data_dir_with_reference(tmp_path)

    # First pass: simulate an older recording on disk.
    engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    try:
        from datetime import UTC, datetime

        with engine.begin() as conn:
            conn.execute(
                database_versions.insert().values(
                    db_name="lai_bundle",
                    version="unknown-pre-manifest",
                    file_size_bytes=1,
                    downloaded_at=datetime(2020, 1, 1, tzinfo=UTC),
                    checksum_sha256=None,
                )
            )
    finally:
        engine.dispose()

    manifest_path = _write_local_manifest(tmp_path)
    monkeypatch.setenv(manifest_module.MANIFEST_PATH_ENV, str(manifest_path))

    tarball = tmp_path / "lai_bundle_src.tar.gz"
    _build_minimal_lai_tarball(tarball)

    dest_path = data_dir / "lai_bundle.tar.gz"
    shutil.copy2(tarball, dest_path)

    _extract_lai_bundle(dest_path, dest_path)

    engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "lai_bundle")
            ).fetchall()
    finally:
        engine.dispose()

    assert len(rows) == 1
    row = rows[0]
    assert row.version == "v1.1"
    assert row.checksum_sha256 == LAI_V1_1_SHA256
    assert row.file_size_bytes == 22 * 3 * 4 + len(_MINIMAL_LIFTOVER)


# ──────────────────────────────────────────────────────────────────────
# Missing reference.db: extraction succeeds, recording is best-effort
# ──────────────────────────────────────────────────────────────────────


def test_extract_succeeds_when_reference_db_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No reference.db (e.g. unit tests in isolation): extraction must not fail."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.delenv(manifest_module.MANIFEST_PATH_ENV, raising=False)
    monkeypatch.setattr(
        manifest_module,
        "fetch_manifest",
        lambda *a, **kw: (_ for _ in ()).throw(manifest_module.ManifestFetchError("offline")),
    )

    tarball = tmp_path / "lai_bundle_src.tar.gz"
    _build_minimal_lai_tarball(tarball)

    dest_path = data_dir / "lai_bundle.tar.gz"
    shutil.copy2(tarball, dest_path)

    # Should not raise even though reference.db has no schema.
    _extract_lai_bundle(dest_path, dest_path)

    bundle_dir = data_dir / "lai_bundle"
    assert bundle_dir.is_dir()


# ──────────────────────────────────────────────────────────────────────
# Phase D (PR-0c): LAI bundle registry sha256 set to the real v2.0.0 SHA
# ──────────────────────────────────────────────────────────────────────

# The published LAI bundle v2.0.0 SHA-256 (registry side). Must byte-match
# bundles.lai_bundle.sha256 — see ``test_lai_bundle_registry_sha_matches_manifest``.
LAI_V2_0_0_SHA256 = "36abb5f2ed95011aff1227c894f52597ef5c31adb5a132fafdf0830eabf14bff"


def test_lai_bundle_registry_sha256_set_for_phase_d_pr0c() -> None:
    """Phase D/Step 32 (PR-0c) re-sets ``DATABASES["lai_bundle"].sha256``.

    Phase 0i (PR-0z) had reset it to ``None`` because the old ``LAI_V1_1_SHA256``
    leftover never matched the manifest placeholder. PR-0c now restores the real
    v2.0.0 SHA-256 once the ``lai-bundle-v2.0.0`` tarball is published, so the
    registry and manifest stay byte-locked.
    """
    entry = DATABASES["lai_bundle"]
    assert entry.sha256 == LAI_V2_0_0_SHA256
    assert entry.build_mode == "download"
    assert entry.filename == "lai_bundle.tar.gz"


def test_lai_bundle_registry_sha_matches_manifest() -> None:
    """Plan §9 Done criterion #4: the registry SHA-256 must byte-match the
    committed ``bundles/manifest.json`` ``lai_bundle.sha256`` exactly."""
    repo_manifest = Path(__file__).resolve().parents[2] / "bundles" / "manifest.json"
    if not repo_manifest.is_file():
        pytest.skip("bundles/manifest.json not present in this checkout")
    payload = json.loads(repo_manifest.read_text(encoding="utf-8"))
    manifest_sha = payload["bundles"]["lai_bundle"]["sha256"]
    assert DATABASES["lai_bundle"].sha256 == manifest_sha
