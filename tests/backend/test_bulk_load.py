"""Tests for backend.annotation.bulk_load (resilient SQLite bulk-load helpers)."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from backend.annotation.bulk_load import (
    BUSY_TIMEOUT_BACKSTOP_RETRIES,
    bulk_write_connection,
    execute_write,
    insert_batch,
    retry_on_locked,
)


def _locked_error() -> sa.exc.OperationalError:
    """Build an OperationalError mirroring sqlite3's 'database is locked'."""
    return sa.exc.OperationalError("INSERT ...", {}, Exception("database is locked"))


class TestRetryOnLocked:
    def test_returns_after_transient_locks(self) -> None:
        calls = {"n": 0}

        def flaky() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise _locked_error()
            return "ok"

        result = retry_on_locked(flaky, sleep=lambda _s: None)
        assert result == "ok"
        assert calls["n"] == 3

    def test_succeeds_first_try_without_sleeping(self) -> None:
        slept: list[float] = []
        result = retry_on_locked(lambda: 42, sleep=slept.append)
        assert result == 42
        assert slept == []

    def test_raises_after_exhausting_retries(self) -> None:
        calls = {"n": 0}

        def always_locked() -> None:
            calls["n"] += 1
            raise _locked_error()

        with pytest.raises(sa.exc.OperationalError):
            retry_on_locked(always_locked, max_retries=4, sleep=lambda _s: None)
        assert calls["n"] == 4

    def test_busy_timeout_backstop_budget_caps_attempts(self) -> None:
        calls = {"n": 0}
        slept: list[float] = []

        def always_locked() -> None:
            calls["n"] += 1
            raise _locked_error()

        with pytest.raises(sa.exc.OperationalError):
            retry_on_locked(
                always_locked,
                max_retries=BUSY_TIMEOUT_BACKSTOP_RETRIES,
                sleep=slept.append,
            )
        assert calls["n"] == BUSY_TIMEOUT_BACKSTOP_RETRIES == 2
        assert slept == [0.1]

    def test_does_not_swallow_non_operational_errors(self) -> None:
        def boom() -> None:
            raise ValueError("not a lock")

        with pytest.raises(ValueError, match="not a lock"):
            retry_on_locked(boom, sleep=lambda _s: None)


_CREATE = sa.text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
_INSERT = sa.text("INSERT OR REPLACE INTO t (id, v) VALUES (:id, :v)")


def _memory_engine() -> sa.Engine:
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(_CREATE)
    return engine


class TestBulkWriteConnection:
    def test_inserts_via_yielded_connection_in_memory(self) -> None:
        engine = _memory_engine()
        with bulk_write_connection(engine) as conn:
            insert_batch(conn, _INSERT, [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}])
            insert_batch(conn, _INSERT, [])  # empty batch is a no-op
        with engine.connect() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM t")).scalar()
        assert count == 2

    def test_sets_and_restores_pragmas_for_file_engine(self, tmp_path) -> None:
        db_path = tmp_path / "bulk.db"
        # StaticPool: one shared connection, so the restore assertion below is
        # checked on the same connection the bulk load tuned.
        engine = sa.create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        with engine.begin() as conn:
            conn.execute(_CREATE)

        def _synchronous(conn: sa.Connection) -> int:
            # Read via the raw DBAPI cursor so we don't autobegin a SQLAlchemy
            # transaction (which would collide with insert_batch's conn.begin()).
            return conn.connection.dbapi_connection.execute("PRAGMA synchronous").fetchone()[0]

        with bulk_write_connection(engine) as conn:
            assert _synchronous(conn) == 0  # OFF during the bulk load
            insert_batch(conn, _INSERT, [{"id": 1, "v": "x"}])
            execute_write(conn, sa.text("INSERT INTO t (id, v) VALUES (99, 'y')"))

        # synchronous restored to NORMAL (1) on the (pooled) connection
        with engine.connect() as conn:
            assert _synchronous(conn) == 1
            count = conn.execute(sa.text("SELECT COUNT(*) FROM t")).scalar()
        assert count == 2
        engine.dispose()
