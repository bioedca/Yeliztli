"""Tests for ``backend.db.sqlite_engine.make_sqlite_engine``.

Locks in the fix for the "database is locked" setup-wizard install failures:
every backend SQLite engine must apply ``busy_timeout`` so a contended writer
*waits* for the lock instead of failing outright. A raw
``sa.create_engine("sqlite:///…")`` inherits Python sqlite3's 5 s default; the
concurrent install needs the 30 s that ``DBRegistry`` uses. See the module
docstring in ``backend/db/sqlite_engine.py``.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import NullPool

from backend.db import sqlite_engine
from backend.db.sqlite_engine import DEFAULT_BUSY_TIMEOUT_MS, make_sqlite_engine


def _busy_timeout(engine: sa.Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(sa.text("PRAGMA busy_timeout")).scalar()


def _journal_mode(engine: sa.Engine) -> str:
    with engine.connect() as conn:
        return conn.execute(sa.text("PRAGMA journal_mode")).scalar()


def _synchronous(engine: sa.Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(sa.text("PRAGMA synchronous")).scalar()


# ── PRAGMA application ────────────────────────────────────────────────


def test_busy_timeout_is_30s_by_default(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path / "t.db")
    try:
        assert _busy_timeout(engine) == DEFAULT_BUSY_TIMEOUT_MS == 30_000
    finally:
        engine.dispose()


def test_busy_timeout_applies_to_every_pooled_connection(tmp_path: Path) -> None:
    # The connect listener must fire for every DBAPI connection, not just the
    # first one checked out of the pool.
    engine = make_sqlite_engine(tmp_path / "t.db")
    try:
        for _ in range(3):
            assert _busy_timeout(engine) == 30_000
    finally:
        engine.dispose()


def test_custom_busy_timeout_respected(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path / "t.db", busy_timeout_ms=1234)
    try:
        assert _busy_timeout(engine) == 1234
    finally:
        engine.dispose()


def test_wal_true_enables_wal(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path / "t.db", wal=True)
    try:
        assert _journal_mode(engine) == "wal"
    finally:
        engine.dispose()


def test_wal_true_preserves_sqlite_default_synchronous_full(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path / "t.db", wal=True)
    try:
        assert _synchronous(engine) == 2
    finally:
        engine.dispose()


def test_wal_can_opt_into_synchronous_normal(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path / "t.db", wal=True, synchronous="NORMAL")
    try:
        assert _journal_mode(engine) == "wal"
        assert _synchronous(engine) == 1
    finally:
        engine.dispose()


def test_invalid_synchronous_mode_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="synchronous must be one of"):
        make_sqlite_engine(tmp_path / "t.db", synchronous="FAST")  # type: ignore[arg-type]


def test_dbregistry_reference_engine_opts_into_synchronous_normal(tmp_path: Path) -> None:
    from backend.config import Settings
    from backend.db.connection import DBRegistry

    registry = DBRegistry(Settings(data_dir=tmp_path, wal_mode=True))
    try:
        assert _journal_mode(registry.reference_engine) == "wal"
        assert _synchronous(registry.reference_engine) == 1
    finally:
        registry.dispose_all()


def test_dbregistry_reference_engine_keeps_default_synchronous_when_wal_disabled(
    tmp_path: Path,
) -> None:
    from backend.config import Settings
    from backend.db.connection import DBRegistry

    registry = DBRegistry(Settings(data_dir=tmp_path, wal_mode=False))
    try:
        assert registry._wal_synchronous is None  # noqa: SLF001
        assert _journal_mode(registry.reference_engine) == "delete"
        assert _synchronous(registry.reference_engine) == 2
    finally:
        registry.dispose_all()


def test_dbregistry_sample_engine_keeps_default_synchronous_full(tmp_path: Path) -> None:
    from backend.config import Settings
    from backend.db.connection import DBRegistry

    sample_db = tmp_path / "samples" / "sample_1.db"
    sample_db.parent.mkdir()
    registry = DBRegistry(Settings(data_dir=tmp_path, wal_mode=True))
    try:
        sample_engine = registry.get_sample_engine(sample_db)
        assert _journal_mode(sample_engine) == "wal"
        assert _synchronous(sample_engine) == 2
    finally:
        registry.dispose_all()


def test_wal_false_does_not_convert_journal_mode(tmp_path: Path) -> None:
    # A read-only probe (wal=False) must not rewrite a foreign file's journal
    # mode on connect, but must still apply busy_timeout.
    db = tmp_path / "delete_mode.db"
    raw = sqlite3.connect(db)
    raw.execute("PRAGMA journal_mode=DELETE")
    raw.execute("CREATE TABLE t(x)")
    raw.close()

    engine = make_sqlite_engine(db, wal=False)
    try:
        assert _journal_mode(engine) == "delete"  # unchanged by the probe
        assert _busy_timeout(engine) == 30_000  # but busy_timeout still set
    finally:
        engine.dispose()


def test_read_optimized_pragmas(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path / "t.db", read_optimized=True)
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("PRAGMA cache_size")).scalar() == -65536
            assert conn.execute(sa.text("PRAGMA mmap_size")).scalar() == 268435456
    finally:
        engine.dispose()


def test_poolclass_forwarded_and_pragmas_still_applied(tmp_path: Path) -> None:
    engine = make_sqlite_engine(tmp_path / "t.db", poolclass=NullPool)
    try:
        assert isinstance(engine.pool, NullPool)
        assert _busy_timeout(engine) == 30_000
    finally:
        engine.dispose()


# ── Concurrency behaviour (the actual regression) ─────────────────────


def _make_wal_table(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t(x INTEGER)")
    conn.commit()
    conn.close()


def _hold_write_lock(db: str, acquired: threading.Event, release: threading.Event) -> None:
    """Hold reference.db's WAL write lock until ``release`` is set."""
    conn = sqlite3.connect(db, timeout=30)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO t(x) VALUES (1)")
        acquired.set()
        release.wait(5.0)
        conn.commit()
    finally:
        conn.close()


def test_second_writer_waits_out_the_lock(tmp_path: Path) -> None:
    """A contended writer BLOCKS on a held write lock and then succeeds instead
    of failing fast — the wait-don't-fail behaviour ``busy_timeout`` buys.

    This proves *that* a helper engine waits (the qualitative guarantee), not
    the specific 30 s value — a raw 5 s engine would also wait out this
    sub-second hold. The 30 s window itself is pinned by
    ``test_busy_timeout_is_30s_by_default``, and that a near-zero timeout fails
    fast is shown by ``test_tiny_busy_timeout_still_raises_under_contention``."""
    db = tmp_path / "contended.db"
    _make_wal_table(db)

    acquired = threading.Event()
    release = threading.Event()
    holder = threading.Thread(target=_hold_write_lock, args=(str(db), acquired, release))
    holder.start()
    try:
        assert acquired.wait(2.0), "lock holder never acquired the write lock"

        engine = make_sqlite_engine(db, wal=False)
        try:
            # Free the holder after 0.5 s; the writer must block until then.
            releaser = threading.Timer(0.5, release.set)
            releaser.start()
            start = time.monotonic()
            with engine.begin() as conn:
                conn.execute(sa.text("INSERT INTO t(x) VALUES (2)"))
            elapsed = time.monotonic() - start
        finally:
            engine.dispose()
    finally:
        release.set()
        holder.join(5.0)

    # The insert could only commit after the holder released its lock (~0.5 s),
    # proving the writer waited instead of failing fast.
    assert elapsed >= 0.3, f"writer did not actually wait (elapsed={elapsed:.3f}s)"


def test_tiny_busy_timeout_still_raises_under_contention(tmp_path: Path) -> None:
    """Control for the test above: with a 1 ms busy_timeout the *same* held-lock
    scenario DOES raise — confirming the contention is real and busy_timeout is
    the lever that turns the failure into a wait."""
    db = tmp_path / "contended_tiny.db"
    _make_wal_table(db)

    acquired = threading.Event()
    release = threading.Event()
    holder = threading.Thread(target=_hold_write_lock, args=(str(db), acquired, release))
    holder.start()
    try:
        assert acquired.wait(2.0)

        engine = make_sqlite_engine(db, wal=False, busy_timeout_ms=1)
        try:
            with pytest.raises(sa.exc.OperationalError, match="database is locked"):
                with engine.begin() as conn:
                    conn.execute(sa.text("INSERT INTO t(x) VALUES (2)"))
        finally:
            engine.dispose()
    finally:
        release.set()
        holder.join(5.0)


@pytest.fixture
def wal_connect_fault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Inject immediate SQLite errors in the real engine's WAL connect pragma."""
    engines = []
    connections = []

    def create(*, error_code: int, failures: int | None, timeout_ms: int = 25):
        state = SimpleNamespace(attempts=0, elapsed=0.0, sleeps=[])

        def sleep(delay: float) -> None:
            state.sleeps.append(delay)
            state.elapsed += delay

        monkeypatch.setattr(
            sqlite_engine,
            "time",
            SimpleNamespace(monotonic=lambda: state.elapsed, sleep=sleep),
            raising=False,
        )

        class FaultCursor(sqlite3.Cursor):
            def execute(self, sql, parameters=()):
                if sql == "PRAGMA journal_mode=WAL":
                    state.attempts += 1
                    if failures is None or state.attempts <= failures:
                        error = sqlite3.OperationalError("synthetic WAL connect failure")
                        error.sqlite_errorcode = error_code
                        raise error
                return super().execute(sql, parameters)

        class FaultConnection(sqlite3.Connection):
            def cursor(self, *args, **kwargs):
                return super().cursor(*args, factory=FaultCursor, **kwargs)

        def connect(dialect, record, args, kwargs):
            conn = sqlite3.connect(*args, factory=FaultConnection, **kwargs)
            connections.append(conn)
            return conn

        engine = make_sqlite_engine(tmp_path / "wal-fault.db", busy_timeout_ms=timeout_ms)
        sa.event.listen(engine, "do_connect", connect)
        engines.append(engine)
        return engine, state

    yield create
    for engine in engines:
        engine.dispose()
    for conn in connections:
        conn.close()


@pytest.mark.parametrize("error_code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_BUSY_RECOVERY])
def test_wal_setup_retries_immediate_busy_and_restores_query_timeout(
    wal_connect_fault, error_code: int
) -> None:
    engine, state = wal_connect_fault(error_code=error_code, failures=1)
    assert _journal_mode(engine) == "wal"
    assert _busy_timeout(engine) == 25
    assert _synchronous(engine) == 2
    assert state.attempts == 2
    assert 0 < sum(state.sleeps) < 0.025


def test_wal_setup_busy_exhausts_one_bounded_timeout(wal_connect_fault) -> None:
    engine, state = wal_connect_fault(error_code=sqlite3.SQLITE_BUSY, failures=None)
    with pytest.raises(sa.exc.OperationalError) as caught:
        engine.connect()
    assert caught.value.orig.sqlite_errorcode == sqlite3.SQLITE_BUSY
    assert state.attempts > 1
    assert sum(state.sleeps) == pytest.approx(0.025)


def test_wal_setup_zero_timeout_does_not_retry(wal_connect_fault) -> None:
    engine, state = wal_connect_fault(error_code=sqlite3.SQLITE_BUSY, failures=None, timeout_ms=0)
    with pytest.raises(sa.exc.OperationalError) as caught:
        engine.connect()
    assert caught.value.orig.sqlite_errorcode == sqlite3.SQLITE_BUSY
    assert state.attempts == 1
    assert state.sleeps == []


@pytest.mark.parametrize("error_code", [sqlite3.SQLITE_READONLY, sqlite3.SQLITE_LOCKED])
def test_wal_setup_does_not_retry_other_errors(wal_connect_fault, error_code: int) -> None:
    engine, state = wal_connect_fault(error_code=error_code, failures=None)
    with pytest.raises(sa.exc.OperationalError) as caught:
        engine.connect()
    assert caught.value.orig.sqlite_errorcode == error_code
    assert state.attempts == 1
    assert state.sleeps == []


# ── Repo guard: no raw file-backed sqlite engines outside the factory ─


_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
# Only the factory itself may call create_engine on a sqlite file URL.
_ALLOWED_FILENAMES = {"sqlite_engine.py"}
_RAW_SQLITE_ENGINE = re.compile(r"""create_engine\(\s*f?["']sqlite:///""")


def test_no_raw_sqlite_file_engine_outside_helper() -> None:
    """Every ``sqlite:///`` file-URL engine must go through ``make_sqlite_engine``
    so ``busy_timeout`` is always applied. A raw ``sa.create_engine("sqlite:///…")``
    silently keeps Python's 5 s default and re-opens the concurrent-install
    "database is locked" hole this fix closed.

    Scope: this guards the ``sqlite:///`` URL form. The two read-only per-sample
    readers (``export.py`` / ``query_builder.py``) that build an engine via
    ``create_engine("sqlite://", creator=lambda: sqlite3.connect("file:…?mode=ro"))``
    are intentionally exempt — read-only, per-sample, never the contended
    reference.db — and are not matched by the URL-form regex below."""
    scanned = list(_BACKEND_DIR.rglob("*.py"))
    # Fail loudly instead of vacuously passing if _BACKEND_DIR ever mis-resolves
    # (a future move/refactor) and the glob silently scans nothing.
    assert len(scanned) > 10, (
        f"expected to scan backend/*.py files, found {len(scanned)} under "
        f"{_BACKEND_DIR} — path resolution broke, making this guard vacuous"
    )
    offenders = [
        str(path.relative_to(_BACKEND_DIR.parent))
        for path in scanned
        if path.name not in _ALLOWED_FILENAMES
        and _RAW_SQLITE_ENGINE.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        'Raw sa.create_engine("sqlite:///…") found — route these through '
        "backend.db.sqlite_engine.make_sqlite_engine so busy_timeout is set:\n  "
        + "\n  ".join(offenders)
    )
