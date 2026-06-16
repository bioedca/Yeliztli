"""Tests for reference-DB additive-column backfill.

The reference database is bootstrapped via
``reference_metadata.create_all(checkfirst=True)`` (see ``backend/main.py``),
not Alembic at runtime. ``create_all`` creates missing *tables* but never adds
*columns* to pre-existing tables. So a ``samples`` table created before Alembic
009 (which added ``samples.individual_id``) is left without the column, and
every ``SELECT`` against ``samples`` fails with ``no such column``.

``ensure_reference_schema_current()`` backfills such additive columns. These
tests build the exact pre-009 drift (a ``samples`` table missing
``individual_id``, with the ``individuals`` table already present, mirroring a
post-``create_all`` state) and assert the column + index are added, that the
function is idempotent, and that a fresh schema is left untouched.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from backend.db.reference_schema import ensure_reference_schema_current
from backend.db.tables import reference_metadata


def _columns(engine: sa.Engine, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns(table)}


def _make_pre009_samples(engine: sa.Engine) -> None:
    """Create a ``samples`` table lacking ``individual_id`` (pre-Alembic-009)."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE samples ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  name TEXT NOT NULL,"
                "  db_path TEXT NOT NULL UNIQUE,"
                "  file_format TEXT,"
                "  file_hash TEXT,"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  updated_at DATETIME"
                ")"
            )
        )
        # The individuals table exists already, as create_all would have made
        # it: a new table create_all *can* add, unlike a new column.
        conn.execute(
            sa.text(
                "CREATE TABLE individuals ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  display_name TEXT NOT NULL,"
                "  notes TEXT DEFAULT '',"
                "  biological_sex TEXT,"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  updated_at DATETIME"
                ")"
            )
        )


def test_backfills_missing_individual_id(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    _make_pre009_samples(engine)

    assert "individual_id" not in _columns(engine, "samples")

    changed = ensure_reference_schema_current(engine)

    assert changed is True
    assert "individual_id" in _columns(engine, "samples")
    # The index that backs the two-level sample selector is created too.
    indexes = {ix["name"] for ix in sa.inspect(engine).get_indexes("samples")}
    assert "ix_samples_individual_id" in indexes


def test_select_with_individual_id_works_after_backfill(tmp_path: Path) -> None:
    """The crash repro: SELECT ... samples.individual_id must stop 500-ing."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    _make_pre009_samples(engine)
    ensure_reference_schema_current(engine)

    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT id, name, individual_id FROM samples")).fetchall()
    assert rows == []


def test_idempotent_on_second_run(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    _make_pre009_samples(engine)

    assert ensure_reference_schema_current(engine) is True
    # Second run finds nothing to do.
    assert ensure_reference_schema_current(engine) is False


def test_noop_on_fresh_create_all_schema(tmp_path: Path) -> None:
    """A schema built fresh from current metadata already has the column."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)

    assert "individual_id" in _columns(engine, "samples")
    assert "genome_build" in _columns(engine, "database_versions")
    assert "validator" in _columns(engine, "downloads")
    assert ensure_reference_schema_current(engine) is False


def _make_pre011_database_versions(engine: sa.Engine) -> None:
    """Create a ``database_versions`` table lacking ``genome_build`` (pre-011)."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE database_versions ("
                "  db_name TEXT PRIMARY KEY,"
                "  version TEXT NOT NULL,"
                "  file_path TEXT,"
                "  file_size_bytes INTEGER,"
                "  downloaded_at DATETIME,"
                "  checksum_sha256 TEXT"
                ")"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO database_versions (db_name, version) VALUES ('clinvar', '20260101')"
            )
        )


def test_backfills_missing_genome_build(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    _make_pre011_database_versions(engine)

    assert "genome_build" not in _columns(engine, "database_versions")

    changed = ensure_reference_schema_current(engine)

    assert changed is True
    assert "genome_build" in _columns(engine, "database_versions")
    # The pre-existing row survives and reads NULL for the new column.
    with engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT version, genome_build FROM database_versions WHERE db_name='clinvar'")
        ).fetchone()
    assert row == ("20260101", None)


def test_genome_build_backfill_idempotent(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    _make_pre011_database_versions(engine)

    assert ensure_reference_schema_current(engine) is True
    assert ensure_reference_schema_current(engine) is False


def _make_pre_validator_downloads(engine: sa.Engine) -> None:
    """Create a ``downloads`` table lacking ``validator`` (pre-PR-15)."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE downloads ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  url TEXT NOT NULL,"
                "  dest_path TEXT NOT NULL,"
                "  total_bytes INTEGER,"
                "  downloaded_bytes INTEGER DEFAULT 0,"
                "  checksum_sha256 TEXT,"
                "  status TEXT DEFAULT 'pending',"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  updated_at DATETIME"
                ")"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO downloads (url, dest_path, downloaded_bytes, status) "
                "VALUES ('http://x/clinvar.db', '/d/clinvar.db', 4096, 'downloading')"
            )
        )


def test_backfills_missing_downloads_validator(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    _make_pre_validator_downloads(engine)

    assert "validator" not in _columns(engine, "downloads")

    changed = ensure_reference_schema_current(engine)

    assert changed is True
    assert "validator" in _columns(engine, "downloads")
    # An in-flight partial survives and reads NULL for the new validator, so a
    # SELECT that seeds If-Range on resume cannot crash on the missing column.
    with engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT downloaded_bytes, validator FROM downloads LIMIT 1")
        ).fetchone()
    assert row == (4096, None)


def test_downloads_validator_backfill_idempotent(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    _make_pre_validator_downloads(engine)

    assert ensure_reference_schema_current(engine) is True
    assert ensure_reference_schema_current(engine) is False
