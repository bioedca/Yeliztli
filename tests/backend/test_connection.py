"""Tests for database engine cache invalidation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import sqlalchemy as sa

from backend.config import Settings
from backend.db.connection import DBRegistry


def _raw_marker(path: Path) -> str:
    """Read the marker via an independent sqlite3 connection (not the registry)."""
    with sqlite3.connect(str(path)) as conn:
        return conn.execute("SELECT value FROM marker").fetchone()[0]


def _write_marker_db(path: Path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        conn.execute("INSERT INTO marker (value) VALUES (?)", (marker,))


def _read_marker(engine: sa.Engine) -> str:
    with engine.connect() as conn:
        return conn.execute(sa.text("SELECT value FROM marker")).scalar_one()


def test_encode_ccres_engine_reopens_after_atomic_file_replace(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, wal_mode=False)
    _write_marker_db(settings.encode_ccres_db_path, "old")

    registry = DBRegistry(settings)
    try:
        old_engine = registry.encode_ccres_engine
        assert _read_marker(old_engine) == "old"

        replacement = tmp_path / "replacement_encode_ccres.db"
        _write_marker_db(replacement, "new")
        replacement.replace(settings.encode_ccres_db_path)

        new_engine = registry.encode_ccres_engine

        assert new_engine is not old_engine
        assert _read_marker(new_engine) == "new"
    finally:
        registry.dispose_all()


def test_vep_engine_reopens_after_atomic_file_replace(tmp_path: Path) -> None:
    """A warmed pooled VEP connection must not survive a bundle swap (#1953).

    ``run_vep_bundle_update`` replaces ``vep_bundle.db`` atomically. Before the
    fingerprint guard, the cached ``vep_engine`` kept a warmed pooled connection
    on the unlinked old inode, so an annotation could keep reading stale data
    even though a fresh connection and the registry version described the new
    file. This proves a subsequent query uses the replacement.
    """
    settings = Settings(data_dir=tmp_path, wal_mode=False)
    _write_marker_db(settings.vep_bundle_db_path, "old")

    registry = DBRegistry(settings)
    try:
        old_engine = registry.vep_engine
        # Hold a live connection across the swap so the old inode stays open —
        # the exact condition that let a cached engine keep reading stale data.
        with old_engine.connect() as warm:
            assert warm.execute(sa.text("SELECT value FROM marker")).scalar_one() == "old"

            replacement = tmp_path / "replacement_vep.db"
            _write_marker_db(replacement, "new")
            replacement.replace(settings.vep_bundle_db_path)

            new_engine = registry.vep_engine
            assert new_engine is not old_engine
            assert _read_marker(new_engine) == "new"
    finally:
        registry.dispose_all()


def test_vep_engine_refresh_does_not_mutate_bundle_or_leak(tmp_path: Path) -> None:
    """Refresh + disposal read only, never mutate the file, and rebuild cleanly (#1953).

    Acceptance criterion: "Engine disposal and refresh do not mutate the
    installed bundle or leak pooled connections."
    """
    settings = Settings(data_dir=tmp_path, wal_mode=False)
    _write_marker_db(settings.vep_bundle_db_path, "old")

    registry = DBRegistry(settings)
    try:
        first = registry.vep_engine
        with first.connect() as warm:  # warm a held connection on the old inode
            assert warm.execute(sa.text("SELECT value FROM marker")).scalar_one() == "old"

            replacement = tmp_path / "replacement_vep.db"
            _write_marker_db(replacement, "new")
            replacement.replace(settings.vep_bundle_db_path)

            refreshed = registry.vep_engine  # fingerprint change → dispose old, open new
            # Precondition: the refresh actually happened (the behaviour under test).
            assert refreshed is not first

        assert _read_marker(refreshed) == "new"
        # The installed bundle's data is intact — the refresh only reads it.
        assert _raw_marker(settings.vep_bundle_db_path) == "new"

        # dispose_all clears the engine *and* its fingerprint so a later access
        # rebuilds a fresh pool rather than handing back a disposed engine.
        registry.dispose_all()
        assert registry._vep_engine is None
        assert registry._vep_fingerprint is None

        rebuilt = registry.vep_engine
        assert rebuilt is not refreshed
        assert _read_marker(rebuilt) == "new"
    finally:
        registry.dispose_all()
