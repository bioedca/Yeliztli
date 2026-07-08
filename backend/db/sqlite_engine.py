"""Factory for SQLite engines with concurrency-safe connect-time PRAGMAs.

Every SQLite engine in the backend applies ``PRAGMA busy_timeout`` so a
connection that finds the database write-locked *waits* (up to the timeout) for
the current writer to finish instead of raising
``OperationalError: database is locked``.

The subtlety this fixes: a raw ``sa.create_engine("sqlite:///…")`` does **not**
get a zero busy_timeout — Python's :mod:`sqlite3` defaults a new connection's
``timeout`` to 5.0 s, i.e. ``busy_timeout=5000``. ``DBRegistry`` deliberately
raised that to **30 000 ms** for the engines it owns, but the bundle-update /
version-record / build paths in ``update_manager``, ``database_registry`` and
friends built raw engines that silently kept the 5 s default.

Why 5 s is not enough here: a setup-wizard install fans out up to eight
downloads/builds (``ThreadPoolExecutor`` in ``backend.api.routes.databases``),
all writing the shared ``reference.db`` concurrently — download progress /
status checkpoints (``downloads`` / ``jobs``) racing reference-resident bulk
loads (the ~247k-row GWAS catalog, dbSNP ``RsMergeArch``, ClinGen, CPIC…). Under
WAL only one writer holds the lock at a time, and a short metadata write can
lose that race for longer than 5 s under sustained contention → "database is
locked", which fails the whole bundle install and paints the database red on a
fresh startover.

Routing every ``sqlite:///`` file-URL engine through :func:`make_sqlite_engine`
gives them all the same 30 s window (``retry_on_locked`` stays as a secondary
backstop), and ``DBRegistry._create_engine`` now delegates here so the owned
and standalone engines cannot drift. (The two read-only, per-sample SQL-console
/ export readers that open ``file:…?mode=ro`` through a ``creator=`` callback
keep sqlite3's 5 s default by design — they never touch the contended
reference.db, so they are intentionally exempt.)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, get_args

import sqlalchemy as sa
from sqlalchemy import event

if TYPE_CHECKING:
    from sqlalchemy.pool import Pool

# Milliseconds a contended connection waits for the write lock before raising
# "database is locked". The value DBRegistry has always used for its engines —
# 6x Python sqlite3's 5 s connection default, sized for the concurrent install.
DEFAULT_BUSY_TIMEOUT_MS = 30_000

SQLiteSynchronousMode = Literal["OFF", "NORMAL", "FULL", "EXTRA"]
_VALID_SYNCHRONOUS_MODES = frozenset(get_args(SQLiteSynchronousMode))


def make_sqlite_engine(
    db_path: str | Path,
    *,
    wal: bool = True,
    synchronous: SQLiteSynchronousMode | None = None,
    read_optimized: bool = False,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    poolclass: type[Pool] | None = None,
    echo: bool = False,
) -> sa.Engine:
    """Create a SQLite engine that applies concurrency-safe PRAGMAs on connect.

    The connect-event listener runs for *every* new DBAPI connection (not just
    the first pooled one), so the PRAGMAs hold across the pool's lifetime.

    Args:
        db_path: Path to the SQLite file.
        wal: (Re)assert WAL journal mode on connect. Pass ``True`` only when this
            engine OWNS the database's lifecycle — ``DBRegistry`` (via
            ``settings.wal_mode``) and standalone builds that create the file.
            Re-openers of an already-provisioned DB and read-only probes pass
            ``False`` so they inherit the DB's existing journal mode and never
            convert it (e.g. flipping a ``wal_mode=False`` rollback reference.db
            to WAL behind the user's back); ``busy_timeout`` is applied either way.
        synchronous: Optional SQLite ``PRAGMA synchronous`` mode to apply on each
            connection. Leave ``None`` to preserve SQLite's default durability
            policy. Reference/rebuildable WAL engines may opt into ``NORMAL`` for
            fewer fsyncs; per-sample engines should keep the default ``FULL``.
        read_optimized: Apply aggressive read-performance PRAGMAs (larger page
            cache, mmap, in-memory temp store) for large read-only reference DBs.
        busy_timeout_ms: Milliseconds to wait for a contended write lock before
            raising "database is locked".
        poolclass: Optional SQLAlchemy pool class (e.g. ``NullPool`` for a
            throwaway probe engine). When omitted, the default pool is used with
            ``pool_pre_ping`` enabled.
        echo: Forwarded to ``create_engine`` for SQL logging.

    Returns:
        Configured SQLAlchemy Engine.
    """
    if synchronous is not None and synchronous not in _VALID_SYNCHRONOUS_MODES:
        allowed = ", ".join(sorted(_VALID_SYNCHRONOUS_MODES))
        raise ValueError(f"synchronous must be one of {allowed}; got {synchronous!r}")

    kwargs: dict = {"echo": echo}
    if poolclass is not None:
        kwargs["poolclass"] = poolclass
    else:
        kwargs["pool_pre_ping"] = True

    engine = sa.create_engine(f"sqlite:///{db_path}", **kwargs)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            # Always first: make contended writers wait instead of failing.
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            if wal:
                cursor.execute("PRAGMA journal_mode=WAL")
            if synchronous is not None:
                cursor.execute(f"PRAGMA synchronous={synchronous}")
            if read_optimized:
                # 64 MB page cache (negative = KiB).
                cursor.execute("PRAGMA cache_size=-65536")
                # 256 MB memory-mapped I/O for large reference DBs.
                cursor.execute("PRAGMA mmap_size=268435456")
                # Keep temp tables/indexes in memory.
                cursor.execute("PRAGMA temp_store=MEMORY")
        finally:
            cursor.close()

    return engine
