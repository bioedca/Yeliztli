"""Tests for the shared effective VEP bundle version resolver."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.db.tables import database_versions, reference_metadata
from backend.db.vep_version import (
    VERSIONLESS_VEP_BUNDLE_BASELINE,
    resolve_effective_vep_bundle_version,
)


def _reference_engine(version: str | None = None) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            database_versions.insert().values(
                db_name="aaa_decoy",
                version="v99.0.0",
            )
        )
        if version is not None:
            conn.execute(
                database_versions.insert().values(
                    db_name="vep_bundle",
                    version=version,
                )
            )
    return engine


def _vep_db_path(tmp_path: Path, version: str | None = None) -> Path:
    db_path = tmp_path / "VEP bundle #1?.db"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("CREATE TABLE bundle_metadata (key TEXT PRIMARY KEY, value TEXT)")
        if version is not None:
            conn.execute(
                "INSERT INTO bundle_metadata (key, value) VALUES ('bundle_version', ?)",
                (version,),
            )
        conn.commit()
    return db_path


def test_explicit_version_row_takes_precedence_over_embedded_metadata(tmp_path: Path) -> None:
    reference_engine = _reference_engine("v2.0.0")
    vep_db_path = _vep_db_path(tmp_path, "v9.0.0")

    assert resolve_effective_vep_bundle_version(reference_engine, vep_db_path) == "v2.0.0"


def test_embedded_version_is_used_when_explicit_row_is_absent(tmp_path: Path) -> None:
    reference_engine = _reference_engine()
    vep_db_path = _vep_db_path(tmp_path, "v3.0.0")

    assert resolve_effective_vep_bundle_version(reference_engine, vep_db_path) == "v3.0.0"
    with reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(database_versions.c.version).where(
                database_versions.c.db_name == "vep_bundle"
            )
        ).fetchone()
    assert row is None


@pytest.mark.parametrize("embedded_version", [None, ""])
def test_missing_or_empty_embedded_version_uses_committed_fixture_baseline(
    tmp_path: Path,
    embedded_version: str | None,
) -> None:
    reference_engine = _reference_engine()
    vep_db_path = _vep_db_path(tmp_path, embedded_version)

    assert (
        resolve_effective_vep_bundle_version(reference_engine, vep_db_path)
        == VERSIONLESS_VEP_BUNDLE_BASELINE
        == "v1.0.0"
    )


@pytest.mark.parametrize(
    ("recorded_version", "expected"),
    [(None, "v1.0.0"), ("v2.0.0", "v2.0.0")],
)
def test_missing_vep_path_uses_reference_precedence(
    recorded_version: str | None,
    expected: str,
) -> None:
    reference_engine = _reference_engine(recorded_version)

    assert resolve_effective_vep_bundle_version(reference_engine, None) == expected


def test_read_only_probe_does_not_materialize_absent_or_partial_files(tmp_path: Path) -> None:
    reference_engine = _reference_engine()
    absent_path = tmp_path / "absent bundle #?.db"
    partial_path = tmp_path / "partial bundle #?.db"
    partial_path.touch()

    assert resolve_effective_vep_bundle_version(reference_engine, absent_path) == "v1.0.0"
    assert resolve_effective_vep_bundle_version(reference_engine, partial_path) == "v1.0.0"

    assert not absent_path.exists()
    assert partial_path.stat().st_size == 0
    for db_path in (absent_path, partial_path):
        assert not Path(f"{db_path}-wal").exists()
        assert not Path(f"{db_path}-shm").exists()


def test_read_only_probe_preserves_delete_journal_mode(tmp_path: Path) -> None:
    reference_engine = _reference_engine()
    vep_db_path = _vep_db_path(tmp_path, "v3.0.0")
    with closing(sqlite3.connect(vep_db_path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"

    assert resolve_effective_vep_bundle_version(reference_engine, vep_db_path) == "v3.0.0"

    with closing(sqlite3.connect(vep_db_path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert not Path(f"{vep_db_path}-wal").exists()
    assert not Path(f"{vep_db_path}-shm").exists()


def test_path_resolution_failure_uses_baseline(tmp_path: Path) -> None:
    reference_engine = _reference_engine()
    symlink_loop = tmp_path / "vep-loop.db"
    symlink_loop.symlink_to(symlink_loop)

    assert resolve_effective_vep_bundle_version(reference_engine, symlink_loop) == "v1.0.0"


def test_uncheckpointed_wal_metadata_is_treated_as_unreadable(tmp_path: Path) -> None:
    reference_engine = _reference_engine()
    vep_db_path = tmp_path / "active-wal-vep.db"
    wal_path = Path(f"{vep_db_path}-wal")
    shm_path = Path(f"{vep_db_path}-shm")
    writer = sqlite3.connect(vep_db_path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE bundle_metadata (key TEXT PRIMARY KEY, value TEXT)")
        writer.execute(
            "INSERT INTO bundle_metadata (key, value) VALUES ('bundle_version', 'v9.0.0')"
        )
        writer.commit()
        assert wal_path.is_file()
        assert shm_path.is_file()
        sidecars_before = {
            path: (path.stat().st_size, path.stat().st_mtime_ns) for path in (wal_path, shm_path)
        }

        assert resolve_effective_vep_bundle_version(reference_engine, vep_db_path) == "v1.0.0"
        assert {
            path: (path.stat().st_size, path.stat().st_mtime_ns) for path in (wal_path, shm_path)
        } == sidecars_before
    finally:
        writer.close()
