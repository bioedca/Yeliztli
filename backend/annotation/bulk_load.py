"""Shared helpers for resilient, fast SQLite bulk loads (dbNSFP, gnomAD, …).

A reference-DB build opens many write transactions against a single SQLite
file.  If that file is briefly contended for the WAL write lock — e.g. a second
build of the same DB, an orphaned build thread left over from a restart, or a
checkpoint — SQLite raises ``OperationalError: database is locked`` once
``busy_timeout`` expires.  The reference.db job writers already retry this way
(``_update_job`` in ``backend.api.routes.databases`` /
``backend.db.download_manager``); :func:`retry_on_locked` gives the standalone
bulk loaders the same resilience.

:func:`bulk_write_connection` hands the loader a single write connection tuned
for a one-shot, rebuildable load: ``synchronous=OFF`` plus a large page cache so
batch commits don't fsync per transaction.  Dropping durability is safe here
because the crash-recovery model for these reference caches is delete-and-
rebuild (a half-written ``dbnsfp.db`` is discarded and the build re-run), and
reusing one connection avoids re-checking-out a pooled connection — and
re-running the engine's connect-time PRAGMA block — on every batch.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
import structlog

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = structlog.get_logger(__name__)

# General retry budget for callers that have not already waited inside SQLite.
DEFAULT_MAX_RETRIES = 5

# Engines created by ``make_sqlite_engine`` already wait up to 30 s inside each
# write attempt via SQLite's busy_timeout. Keep only a small retry backstop for
# truly stuck locks so failure surfaces in about a minute instead of ~150 s.
BUSY_TIMEOUT_BACKSTOP_RETRIES = 2

# Page cache for the bulk-load write connection (negative ⇒ KiB ⇒ 256 MiB).
_BULK_CACHE_SIZE = -262_144


# ── In-process write serialization ────────────────────────────────────
#
# A setup-wizard install fans out up to eight worker threads
# (``ThreadPoolExecutor`` in ``backend.api.routes.databases``), and they all
# write the *same* ``reference.db``: download progress / job checkpoints racing
# the reference-resident bulk loads (dbSNP ``RsMergeArch``, ClinVar, the GWAS
# catalog, ClinGen, CPIC…). SQLite permits exactly one writer per file at a
# time, and its write-lock hand-off is not fair, so a heavy batch writer can be
# *starved* past ``busy_timeout`` while a churn of tiny checkpoint writers keeps
# winning the lock — surfacing as ``OperationalError: database is locked`` that
# aborts the whole build (the checkpoints tolerate it; a build does not).
#
# :func:`serialized_write` converts that in-process SQLite lock-fight into an
# orderly Python queue: every writer to a given file first acquires that file's
# process-wide lock, so at most one thread ever holds the SQLite write lock and
# ``SQLITE_BUSY`` cannot arise between same-process writers. The lock is keyed by
# the *resolved file path* (not the engine object), so the many engines that all
# point at ``reference.db`` share one lock while writers to distinct files
# (gnomAD / dbNSFP standalone DBs) stay fully concurrent. It is held only for
# the duration of a single write transaction — never across a network fetch or a
# parse — so a long build still lets checkpoints interleave between its batches.
#
# This is an *in-process* guard. Cross-process writers (e.g. the Huey bundle
# consumer) still rely on ``busy_timeout`` + :func:`retry_on_locked`, which stay
# in place as the secondary, cross-process backstop.

_write_locks: dict[str, threading.RLock] = {}
_write_locks_guard = threading.Lock()


def _db_write_key(engine: sa.Engine) -> str | None:
    """Return a stable per-file lock key for ``engine``, or ``None`` if unlocked.

    File-backed SQLite engines key on the resolved absolute path so every engine
    pointing at the same file shares one lock. In-memory / anonymous engines
    (tests) return ``None`` — each is its own private database with no
    cross-thread file contention, so serialization would be pointless.
    """
    database = engine.url.database
    if not database or database == ":memory:":
        return None
    try:
        return str(Path(database).resolve())
    except OSError:  # pragma: no cover - resolve() failure ⇒ fall back to raw path
        return str(database)


def _db_write_lock(engine: sa.Engine) -> threading.RLock | None:
    """Get (creating once) the shared write lock for ``engine``'s file, or None."""
    key = _db_write_key(engine)
    if key is None:
        return None
    with _write_locks_guard:
        lock = _write_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _write_locks[key] = lock
        return lock


@contextmanager
def serialized_write(engine: sa.Engine) -> Iterator[None]:
    """Serialize write transactions to ``engine``'s SQLite file across threads.

    Acquire this around a *single* write transaction so concurrent in-process
    writers to the same file queue instead of racing SQLite's write lock (see
    the module note above). Re-entrant (``RLock``) so a wrapped write nested
    inside another on the same thread cannot self-deadlock. A no-op for
    in-memory engines. Keep the body limited to the DB write — never wrap a
    network fetch or parse in it, or a long build would block every checkpoint.
    """
    lock = _db_write_lock(engine)
    if lock is None:
        yield
        return
    with lock:
        yield


def retry_on_locked[T](
    fn: Callable[[], T],
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``fn`` retrying on SQLite ``OperationalError`` with exponential backoff.

    Uses ``0.1·2**attempt`` seconds of Python-side backoff, re-raising on the
    final attempt so a genuinely stuck database still fails the build loudly
    instead of silently dropping a batch.  Call sites that already use SQLite's
    30 s ``busy_timeout`` should pass ``BUSY_TIMEOUT_BACKSTOP_RETRIES`` so the
    timeout provides the main wait and this helper stays a short backstop. Only
    :class:`sqlalchemy.exc.OperationalError` is caught — schema/data errors
    propagate immediately.
    """
    for attempt in range(max_retries):
        try:
            return fn()
        except sa.exc.OperationalError:
            if attempt < max_retries - 1:
                sleep(0.1 * (2**attempt))
            else:
                raise
    raise AssertionError("unreachable")  # pragma: no cover — loop always returns/raises


def _is_file_backed(engine: sa.Engine) -> bool:
    """Whether ``engine`` points at an on-disk SQLite file (vs. in-memory)."""
    url = str(engine.url)
    return url != "sqlite://" and ":memory:" not in url


@contextmanager
def bulk_write_connection(engine: sa.Engine) -> Iterator[sa.Connection]:
    """Yield one write connection tuned for a one-shot rebuildable bulk load.

    For file-backed engines this sets ``synchronous=OFF`` + a large page cache +
    ``temp_store=MEMORY`` on the raw DBAPI connection (outside any transaction,
    exactly as the engine's connect-event listener does), then restores
    ``synchronous=NORMAL`` on exit so a pooled connection isn't left
    non-durable for later reuse.  No-op tuning for in-memory engines (used in
    tests).  The caller drives its own per-batch transactions on the yielded
    connection (see :func:`insert_batch`).
    """
    file_backed = _is_file_backed(engine)
    with engine.connect() as conn:
        if file_backed:
            _exec_pragmas(
                conn,
                "PRAGMA synchronous=OFF",
                f"PRAGMA cache_size={_BULK_CACHE_SIZE}",
                "PRAGMA temp_store=MEMORY",
            )
        try:
            yield conn
        finally:
            if file_backed:
                # Best-effort restore — must not mask a load error.
                try:
                    _exec_pragmas(conn, "PRAGMA synchronous=NORMAL")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("bulk_write_pragma_reset_failed", error=str(exc))


def _exec_pragmas(conn: sa.Connection, *pragmas: str) -> None:
    """Run PRAGMA statements on the raw DBAPI connection (no SQLAlchemy txn).

    PRAGMAs like ``synchronous`` only take effect outside a transaction, so we
    issue them through a raw cursor — the same technique the engine's
    connect-event listener uses — to avoid SQLAlchemy's autobegin opening one.
    """
    dbapi = conn.connection.dbapi_connection
    cur = dbapi.cursor()
    try:
        for pragma in pragmas:
            cur.execute(pragma)
    finally:
        cur.close()


def insert_batch(conn: sa.Connection, statement: sa.TextClause, batch: list[dict]) -> None:
    """Execute one ``executemany`` batch in its own transaction, retrying on lock.

    Each batch is its own ``conn.begin()`` transaction so a lock retry cleanly
    rolls back and re-runs only that batch (``INSERT OR REPLACE`` is idempotent,
    so a re-run is safe).
    """
    if not batch:
        return

    def _do() -> None:
        with serialized_write(conn.engine), conn.begin():
            conn.execute(statement, batch)

    retry_on_locked(_do, max_retries=BUSY_TIMEOUT_BACKSTOP_RETRIES)


def execute_write(conn: sa.Connection, statement: sa.TextClause) -> None:
    """Execute a single write statement in its own transaction, retrying on lock."""

    def _do() -> None:
        with serialized_write(conn.engine), conn.begin():
            conn.execute(statement)

    retry_on_locked(_do, max_retries=BUSY_TIMEOUT_BACKSTOP_RETRIES)


def delete_table_in_batches(
    engine: sa.Engine,
    table: sa.Table,
    *,
    batch_size: int,
) -> int:
    """Delete every row from ``table`` in bounded transactions.

    SQLite does not have a portable SQLAlchemy Core ``DELETE ... LIMIT`` form, so
    select a bounded set of primary-key values and delete those rows per
    transaction. The helper is intended for reference-DB tables with a single
    primary key.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    primary_key_columns = list(table.primary_key.columns)
    if len(primary_key_columns) != 1:
        raise ValueError(f"{table.name} must have exactly one primary key column")

    pk_col = primary_key_columns[0]
    delete_batch_stmt = table.delete().where(
        pk_col.in_(sa.select(pk_col).select_from(table).limit(batch_size))
    )

    def _delete_one_batch() -> int:
        with serialized_write(engine), engine.begin() as conn:
            result = conn.execute(delete_batch_stmt)
            rowcount = result.rowcount
            if rowcount is None or rowcount < 0:
                raise RuntimeError(f"Could not determine deleted row count for {table.name}")
            return rowcount

    total_deleted = 0
    while True:
        deleted = retry_on_locked(_delete_one_batch, max_retries=BUSY_TIMEOUT_BACKSTOP_RETRIES)
        total_deleted += deleted
        if deleted < batch_size:
            return total_deleted
