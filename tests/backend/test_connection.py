"""Tests for database engine cache invalidation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import sqlalchemy as sa

from backend.config import Settings
from backend.db.connection import DBRegistry


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
