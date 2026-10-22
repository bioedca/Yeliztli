"""Every sample-database removal takes its SQLite sidecars with it (#2585).

A writer killed in rollback-journal mode leaves a hot ``-journal`` beside the
database. If deletion removes the database but not the journal, SQLite replays
that journal into the next *complete* database placed at the same path -- a
restored sample -- and the file reads as malformed. (A fresh database created
by SQLite at the path is safe: with no file present it discards the journal.)

All data here is synthetic.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from _sqlite_journal import leave_hot_journal

from backend.db.sample_schema import SAMPLE_DB_SIDECAR_SUFFIXES, sample_db_sidecars
from backend.services.sample_delete import _delete_sample_files


def _registry(data_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        settings=SimpleNamespace(data_dir=data_dir),
        dispose_sample_engine=lambda _path: None,
    )


def test_the_sidecar_list_covers_the_rollback_journal_and_the_wal_pair() -> None:
    assert set(SAMPLE_DB_SIDECAR_SUFFIXES) == {"-journal", "-wal", "-shm"}
    assert sample_db_sidecars(Path("/d/samples/sample_7.db")) == (
        Path("/d/samples/sample_7.db-journal"),
        Path("/d/samples/sample_7.db-wal"),
        Path("/d/samples/sample_7.db-shm"),
    )


def test_deleting_a_sample_removes_the_database_and_every_sidecar(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    db = samples / "sample_1.db"
    for path in (db, *sample_db_sidecars(db)):
        path.write_bytes(b"synthetic")

    _delete_sample_files(_registry(tmp_path), "samples/sample_1.db")

    assert sorted(samples.iterdir()) == []


def test_a_database_placed_after_a_delete_is_not_corrupted_by_the_old_journal(
    tmp_path: Path,
) -> None:
    """The end-to-end consequence: a complete file copied into place opens intact."""
    samples = tmp_path / "samples"
    samples.mkdir()
    db = samples / "sample_1.db"
    leave_hot_journal(db)

    _delete_sample_files(_registry(tmp_path), "samples/sample_1.db")

    replacement = tmp_path / "restored.db"
    with sqlite3.connect(replacement, isolation_level=None) as conn:
        conn.execute("CREATE TABLE raw_variants (rsid TEXT PRIMARY KEY, genotype TEXT)")
        conn.executemany(
            "INSERT INTO raw_variants VALUES (?, 'RESTORED')", ((f"r{i}",) for i in range(5000))
        )
    conn.close()
    shutil.copyfile(replacement, db)

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute(
            "SELECT genotype, count(*) FROM raw_variants GROUP BY genotype"
        ).fetchall() == [("RESTORED", 5000)]
    finally:
        conn.close()
