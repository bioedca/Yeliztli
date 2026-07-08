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

import time
from contextlib import contextmanager
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
        with conn.begin():
            conn.execute(statement, batch)

    retry_on_locked(_do, max_retries=BUSY_TIMEOUT_BACKSTOP_RETRIES)


def execute_write(conn: sa.Connection, statement: sa.TextClause) -> None:
    """Execute a single write statement in its own transaction, retrying on lock."""

    def _do() -> None:
        with conn.begin():
            conn.execute(statement)

    retry_on_locked(_do, max_retries=BUSY_TIMEOUT_BACKSTOP_RETRIES)
