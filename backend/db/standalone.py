"""Build privately and publish standalone reference databases behind read leases."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import TYPE_CHECKING
from uuid import uuid4

import sqlalchemy as sa

from backend.db.build_guard import cross_process_build_claim, database_access
from backend.db.sqlite_engine import REPLACEABLE_DATABASES, make_sqlite_engine

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from backend.config import Settings
    from backend.db.database_registry import DatabaseInfo


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _record_published_version(reference_engine: sa.Engine, row: Mapping) -> None:
    from sqlalchemy.dialects.sqlite import insert

    from backend.annotation.bulk_load import serialized_write
    from backend.db.tables import database_versions

    statement = insert(database_versions).values(dict(row))
    statement = statement.on_conflict_do_update(
        index_elements=[database_versions.c.db_name],
        set_={key: statement.excluded[key] for key in row if key != "db_name"},
    )
    with serialized_write(reference_engine), reference_engine.begin() as connection:
        connection.execute(statement)


def _read_version(reference_engine: sa.Engine, db_name: str) -> dict | None:
    from backend.db.tables import database_versions

    with reference_engine.connect() as connection:
        row = (
            connection.execute(
                sa.select(database_versions).where(database_versions.c.db_name == db_name)
            )
            .mappings()
            .one_or_none()
        )
    return dict(row) if row is not None else None


def _journal_path(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.publication.json")


def _write_journal(destination: Path, digest: str, row: Mapping) -> None:
    values = dict(row)
    values["downloaded_at"] = values["downloaded_at"].isoformat()
    # Publish a complete recovery record without following an existing symlink.
    with NamedTemporaryFile(mode="w", dir=destination.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            json.dump({"schema": 1, "sha256": digest, "version": values}, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
            os.replace(temporary_path, _journal_path(destination))
            _sync_directory(destination.parent)
        finally:
            temporary_path.unlink(missing_ok=True)


def recover_standalone_versions(settings: Settings, reference_engine: sa.Engine) -> None:
    """Complete the version stamp if a process died between rename and commit.

    The journal names no arbitrary path: each receipt is bound to a fixed
    installed filename and the exact bytes that passed validation. A different
    destination means publication never happened, so its version stays intact.
    All normal startups without an interrupted publication only check filenames.
    """
    from backend.db.tables import database_versions

    for filename, name in REPLACEABLE_DATABASES.items():
        destination = settings.data_dir / filename
        journal = _journal_path(destination)
        if not journal.exists() and not journal.is_symlink():
            continue
        with database_access(name, settings.data_dir, publish=True):
            if not journal.exists() and not journal.is_symlink():
                continue
            _check_private_entry(journal)
            with os.fdopen(os.open(journal, os.O_RDONLY | os.O_NOFOLLOW)) as handle:
                record = json.load(handle)
            row = record["version"]
            if (
                set(record) != {"schema", "sha256", "version"}
                or record["schema"] != 1
                or row["db_name"] != name
                or set(row) != set(database_versions.c.keys())
                or not isinstance(record["sha256"], str)
                or len(record["sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in record["sha256"])
            ):
                raise ValueError("Invalid standalone publication journal")
            if destination.is_symlink():
                raise ValueError("Standalone recovery refuses a symbolic-link destination")
            if destination.exists() and _digest(destination) == record["sha256"]:
                row["downloaded_at"] = datetime.fromisoformat(row["downloaded_at"])
                _record_published_version(reference_engine, row)
            journal.unlink()
            _sync_directory(settings.data_dir)


def publish_standalone(
    db_name: str,
    staged: Path,
    destination: Path,
    *,
    reference_engine: sa.Engine,
    version_row: Mapping,
) -> None:
    """Validate a closed artifact, drain readers, then publish data and version.

    The previous inode remains linked privately until metadata commits. Ordinary
    failure rolls back the file while readers remain fenced. A killed process
    leaves a durable receipt for startup recovery. Never unlink an old SQLite
    journal: it may contain the last usable committed data.
    """
    from backend.db.db_health import validate_standalone_sqlite_file

    if REPLACEABLE_DATABASES.get(destination.name) != db_name or version_row["db_name"] != db_name:
        raise ValueError("Standalone publication target does not match its version")
    result = validate_standalone_sqlite_file(db_name, staged, deep=True)
    if not result.ok:
        raise ValueError(f"Invalid {db_name} replacement: {result.detail}")
    suffixes = ("-wal", "-shm", "-journal")
    if any(Path(f"{staged}{suffix}").exists() for suffix in suffixes):
        raise RuntimeError("Replacement database still has an open journal")
    digest = _digest(staged)
    with staged.open("rb") as handle:
        os.fsync(handle.fileno())
        staged_identity = os.fstat(handle.fileno())
    with database_access(db_name, destination.parent, publish=True):
        if _journal_path(destination).exists():
            raise RuntimeError("Database publication was interrupted; restart to recover")
        if destination.is_symlink():
            raise ValueError("Standalone publication refuses a symbolic-link destination")
        sidecars = [Path(f"{destination}{suffix}") for suffix in suffixes]
        if any(path.exists() for path in sidecars):
            with closing(sqlite3.connect(destination, timeout=0)) as connection:
                checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint is not None and checkpoint[0] != 0:
                    raise RuntimeError("Installed database still has active SQLite readers")
        if any(path.exists() for path in sidecars):
            raise RuntimeError("Installed database journals could not be safely closed")
        # Keep the backup outside TemporaryDirectory: if rollback itself fails,
        # automatic staging cleanup must not erase the last usable artifact.
        previous = destination.with_name(f".{destination.name}.previous-{uuid4().hex}")
        existed = destination.exists()
        previous_identity = destination.stat() if existed else None
        previous_version = _read_version(reference_engine, db_name)
        if existed:
            os.link(destination, previous)
        safe_to_remove_previous = False
        try:
            _write_journal(destination, digest, version_row)
            os.replace(staged, destination)
            _sync_directory(destination.parent)
            _record_published_version(reference_engine, version_row)
        except BaseException:
            # A signal can arrive immediately after rename or COMMIT, before
            # Python advances to the next statement. Inspect durable state;
            # a boolean set after the syscall cannot prove whether it happened.
            installed = destination.stat() if destination.exists() else None
            if installed is not None and os.path.samestat(staged_identity, installed):
                if _read_version(reference_engine, db_name) != previous_version:
                    # Metadata may already have committed. Keep the new file,
                    # receipt and backup; startup can finish safely. Never put
                    # old bytes behind a newly committed version stamp.
                    raise
                if not staged.exists():
                    # A resumable build keeps its completed private artifact
                    # when publication fails before the metadata commit.
                    os.link(destination, staged)
                if existed:
                    os.replace(previous, destination)
                else:
                    destination.unlink()
                _sync_directory(destination.parent)
            elif (installed is None) != (previous_identity is None) or (
                installed is not None and not os.path.samestat(installed, previous_identity)
            ):
                # An unexpected filesystem change makes rollback ambiguous.
                # Keep the receipt and backup instead of declaring recovery done.
                raise
            _journal_path(destination).unlink(missing_ok=True)
            safe_to_remove_previous = True
            raise
        else:
            safe_to_remove_previous = True
            _journal_path(destination).unlink()
            _sync_directory(destination.parent)
        finally:
            if safe_to_remove_previous:
                previous.unlink(missing_ok=True)


def _check_private_entry(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not expected_type(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & (0o077 if directory else 0o022)
    ):
        raise ValueError("Unsafe standalone staging storage")


def _seed_dbnsfp_staging(staged: Path, destination: Path) -> None:
    """Copy legacy resumable progress without modifying the installed inode."""
    with database_access("dbnsfp", destination.parent):
        if not destination.exists():
            return
        if destination.is_symlink():
            raise ValueError("Standalone staging refuses a symbolic-link source")
        try:
            with (
                closing(
                    sqlite3.connect(f"{destination.resolve().as_uri()}?mode=ro", uri=True)
                ) as source,
                TemporaryDirectory(prefix=".seed-", dir=staged.parent) as directory,
            ):
                tables = {
                    row[0]
                    for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if not {"dbnsfp_scores", "dbnsfp_load_progress"} <= tables:
                    return
                candidate = Path(directory) / "seed.db"
                with closing(sqlite3.connect(candidate)) as target:
                    source.backup(target)
                os.replace(candidate, staged)
        except sqlite3.DatabaseError as exc:
            if getattr(exc, "sqlite_errorcode", 0) & 0xFF not in (
                sqlite3.SQLITE_CORRUPT,
                sqlite3.SQLITE_NOTADB,
            ):
                raise
            # Corrupt installed data stays untouched; build a new private file.


def _prepare_dbnsfp_staging(directory: Path, destination: Path) -> None:
    """Keep a private resume cache without ever writing through a live alias."""
    directory.mkdir(mode=0o700, exist_ok=True)
    _check_private_entry(directory, directory=True)
    marker = directory / "owner.json"
    expected = {"schema": 1, "database": "dbnsfp", "purpose": "standalone-rebuild"}
    if not marker.exists():
        if any(directory.iterdir()):
            raise ValueError("Unrecognized standalone staging directory")
        with marker.open("x") as handle:
            json.dump(expected, handle)
            handle.flush()
            os.fsync(handle.fileno())
        _sync_directory(directory)
    _check_private_entry(marker)
    if json.loads(marker.read_text()) != expected:
        raise ValueError("Unrecognized standalone staging owner")
    staged = directory / "replacement.db"
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = Path(f"{staged}{suffix}")
        if path.exists() or path.is_symlink():
            _check_private_entry(path)
    if not staged.exists():
        _seed_dbnsfp_staging(staged, destination)
    if not staged.exists():
        return
    if destination.exists() and staged.samefile(destination):
        # A signal interrupted rollback after re-linking the staged artifact.
        # Startup recovery may have finished its publication. Never resume an
        # import through that second name for the installed inode.
        if any(Path(f"{staged}{suffix}").exists() for suffix in ("-wal", "-shm", "-journal")):
            raise RuntimeError("Published staging alias still has SQLite journals")
        staged.unlink()
        return
    if staged.stat().st_nlink != 1:
        raise ValueError("Standalone staging has an unexpected hard-link alias")
    try:
        with closing(sqlite3.connect(staged, timeout=0)) as connection:
            corrupt = connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]
    except sqlite3.DatabaseError as exc:
        code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
        if code not in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
            raise
        corrupt = True
    if corrupt:
        # Preserve damaged progress for inspection; only the owned cache moves.
        quarantine = directory / f"corrupt-{uuid4().hex}.db"
        for suffix in ("", "-wal", "-shm", "-journal"):
            path = Path(f"{staged}{suffix}")
            if path.exists():
                os.replace(path, Path(f"{quarantine}{suffix}"))
        _sync_directory(directory)


@contextmanager
def _staging_workspace(db_info: DatabaseInfo, settings: Settings) -> Iterator[Path]:
    if db_info.name != "dbnsfp":
        with TemporaryDirectory(
            prefix=f".{db_info.name}-build-", dir=settings.data_dir
        ) as directory:
            yield Path(directory)
        return
    # Separate from the caller's build claim so direct helper callers cannot
    # overlap mutable resume state. The loader itself binds progress to the
    # archive SHA-256 and resets every row if that source token changes.
    with cross_process_build_claim("dbnsfp_staging", settings.data_dir) as acquired:
        if not acquired:
            raise RuntimeError("Another process is using the dbNSFP rebuild cache")
        directory = settings.data_dir / ".dbnsfp-rebuild"
        _prepare_dbnsfp_staging(directory, db_info.dest_path(settings))
        yield directory


@contextmanager
def standalone_build(
    db_info: DatabaseInfo, settings: Settings, reference_engine: sa.Engine
) -> Iterator[tuple[sa.Engine, sa.Engine]]:
    """Yield isolated data and version engines; publish only a successful build."""
    from backend.db.tables import database_versions

    destination = db_info.dest_path(settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _journal_path(destination).exists():
        raise RuntimeError("Database publication was interrupted; restart to recover")
    with (
        _staging_workspace(db_info, settings) as directory,
        TemporaryDirectory(prefix=".version-", dir=directory) as version_directory,
    ):
        staged = directory / "replacement.db"
        target = make_sqlite_engine(staged, wal=db_info.name == "dbnsfp")
        versions = make_sqlite_engine(Path(version_directory) / "version.db", wal=False)
        try:
            database_versions.create(versions)
            yield target, versions
            with versions.connect() as connection:
                row = (
                    connection.execute(
                        sa.select(database_versions).where(
                            database_versions.c.db_name == db_info.name
                        )
                    )
                    .mappings()
                    .one()
                )
        finally:
            target.dispose()
            versions.dispose()
        publish_standalone(
            db_info.name, staged, destination, reference_engine=reference_engine, version_row=row
        )
