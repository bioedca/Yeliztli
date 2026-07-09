"""Tests for the in-process write-serialization lock (:func:`serialized_write`).

Regression coverage for the setup-wizard failure where up to eight concurrent
worker threads writing the shared ``reference.db`` starved the dbSNP bulk-load
past ``busy_timeout`` and aborted the build with
``OperationalError: database is locked`` (see ``backend/annotation/bulk_load.py``).

The lock is keyed by the *resolved file path*, held for a single write
transaction, and layered on top of ``busy_timeout`` + ``retry_on_locked``.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager

import sqlalchemy as sa

from backend.annotation.bulk_load import serialized_write
from backend.db.sqlite_engine import make_sqlite_engine


def _run_occupancy_probe(engines: list[sa.Engine], *, iterations: int = 25) -> int:
    """Run one worker per engine hammering ``serialized_write`` and return the
    maximum number of workers observed *inside* their locked region at once.

    A per-iteration sleep inside the region guarantees overlap would be observed
    if the lock did not exclude — so ``max_inside == 1`` proves mutual exclusion
    and ``max_inside >= 2`` proves genuine concurrency.
    """
    n = len(engines)
    inside = 0
    max_inside = 0
    counter_lock = threading.Lock()
    barrier = threading.Barrier(n)

    def worker(engine: sa.Engine) -> None:
        nonlocal inside, max_inside
        barrier.wait()
        for _ in range(iterations):
            with serialized_write(engine):
                with counter_lock:
                    inside += 1
                    max_inside = max(max_inside, inside)
                time.sleep(0.001)
                with counter_lock:
                    inside -= 1

    threads = [threading.Thread(target=worker, args=(e,)) for e in engines]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return max_inside


def test_same_file_writers_are_mutually_exclusive(tmp_path):
    """Multiple threads writing the same file never overlap under the lock."""
    db = tmp_path / "reference.db"
    engines = [make_sqlite_engine(db) for _ in range(6)]
    assert _run_occupancy_probe(engines) == 1


def test_distinct_engine_instances_same_path_share_one_lock(tmp_path):
    """Different Engine objects for the SAME file still serialize.

    This is the load-bearing property: the wizard opens many engines (registry +
    standalone build engines) at one ``reference.db``; keying on the resolved
    path — not engine identity or URL spelling — makes them share a single lock.
    """
    db = tmp_path / "reference.db"
    # Two distinct engine objects, plus a relative-vs-absolute URL spelling that
    # must still resolve to the same key.
    e_abs = make_sqlite_engine(db)
    e_abs2 = make_sqlite_engine(db.resolve())
    assert _run_occupancy_probe([e_abs, e_abs2, e_abs, e_abs2]) == 1


def test_distinct_files_run_concurrently(tmp_path):
    """Writers to different files are not serialized against each other."""
    engines = [make_sqlite_engine(tmp_path / f"db_{i}.db") for i in range(4)]
    # Path-keyed: distinct files ⇒ distinct locks ⇒ real concurrency.
    assert _run_occupancy_probe(engines) >= 2


def test_in_memory_engine_is_a_noop(tmp_path):
    """In-memory engines bypass the lock (no shared file to contend for)."""
    engines = [make_sqlite_engine(":memory:") for _ in range(4)]
    # No-op ⇒ workers overlap freely.
    assert _run_occupancy_probe(engines) >= 2


def test_reentrant_same_thread_does_not_deadlock(tmp_path):
    """Nested ``serialized_write`` on one thread must not self-deadlock (RLock)."""
    engine = make_sqlite_engine(tmp_path / "reference.db")
    done = threading.Event()

    def worker() -> None:
        with serialized_write(engine):
            with serialized_write(engine):
                done.set()

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=5)
    assert done.is_set(), "reentrant serialized_write deadlocked (needs an RLock)"


# ── Integration: the lock eliminates the real "database is locked" failure ──

_probe = sa.Table(
    "serialize_probe",
    sa.MetaData(),
    sa.Column("k", sa.String, primary_key=True),
    sa.Column("v", sa.Integer),
)


def _hammer_writes(
    engine: sa.Engine,
    conns: list[sa.Connection],
    *,
    serialize: bool,
    iterations: int,
    hold_s: float,
) -> tuple[list[str], int]:
    """Drive ``len(conns)`` threads, each doing ``iterations`` write txns on its
    own (pre-opened) connection, and return ``(errors, rows_written)``.

    Each transaction holds the write lock for ``hold_s`` seconds before commit,
    so with ``busy_timeout=0`` a *second* concurrent writer is guaranteed to hit
    ``SQLITE_BUSY`` — unless ``serialize`` funnels them through the lock first.
    """
    n = len(conns)
    errors: list[str] = []
    errors_lock = threading.Lock()
    barrier = threading.Barrier(n)

    def worker(tid: int) -> None:
        conn = conns[tid]
        barrier.wait()
        for i in range(iterations):
            stmt = _probe.insert().values(k=f"{tid}-{i}", v=tid)
            try:
                if serialize:
                    with serialized_write(engine), conn.begin():
                        conn.execute(stmt)
                        if hold_s:
                            time.sleep(hold_s)
                else:
                    with conn.begin():
                        conn.execute(stmt)
                        if hold_s:
                            time.sleep(hold_s)
            except sa.exc.OperationalError as exc:
                with errors_lock:
                    errors.append(str(exc))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with engine.connect() as conn:
        rows = conn.execute(sa.select(sa.func.count()).select_from(_probe)).scalar_one()
    return errors, rows


def test_contention_control_without_lock_raises_database_is_locked(tmp_path):
    """Control: without the lock, concurrent writers DO hit 'database is locked'.

    Guards against a false-green integration test — proves the contention the
    fix targets is real under these conditions.
    """
    db = tmp_path / "reference.db"
    engine = make_sqlite_engine(db, busy_timeout_ms=0)
    _probe.metadata.create_all(engine)
    conns = [engine.connect() for _ in range(4)]
    try:
        errors, _ = _hammer_writes(engine, conns, serialize=False, iterations=5, hold_s=0.02)
    finally:
        for c in conns:
            c.close()
    assert errors, "expected 'database is locked' without serialization"
    assert all("locked" in e for e in errors)


def test_serialized_writes_never_hit_database_is_locked(tmp_path):
    """With the lock, the same brutal contention produces zero errors and loses
    no rows — the exact guarantee the dbSNP build needed."""
    db = tmp_path / "reference.db"
    engine = make_sqlite_engine(db, busy_timeout_ms=0)
    _probe.metadata.create_all(engine)
    n_threads, iterations = 4, 5
    conns = [engine.connect() for _ in range(n_threads)]
    try:
        errors, rows = _hammer_writes(
            engine, conns, serialize=True, iterations=iterations, hold_s=0.02
        )
    finally:
        for c in conns:
            c.close()
    assert errors == [], f"serialized writes should never lock, got: {errors[:3]}"
    assert rows == n_threads * iterations


# ── Routing: the actual dbSNP build path goes through the lock ──


def test_dbsnp_load_routes_writes_through_serialized_write(tmp_path, monkeypatch):
    """The dbSNP merge load — the writer that failed in the field — acquires the
    per-file lock for its batch upserts and checkpoint (would fail if a future
    edit dropped the wrap)."""
    from backend.annotation import dbsnp
    from backend.db.tables import dbsnp_merges

    engine = make_sqlite_engine(tmp_path / "reference.db")
    dbsnp_merges.create(engine)

    entered: list[object] = []
    real = dbsnp.serialized_write

    @contextmanager
    def spy(engine_arg):
        entered.append(engine_arg)
        with real(engine_arg):
            yield

    monkeypatch.setattr(dbsnp, "serialized_write", spy)

    rows = [
        {"old_rsid": "rs1", "current_rsid": "rs2", "build_id": 126},
        {"old_rsid": "rs3", "current_rsid": "rs4", "build_id": 131},
    ]
    dbsnp.load_rsmerge_into_db(rows, engine, clear_existing=False)

    assert entered, "dbSNP load did not route its writes through serialized_write"
    with engine.connect() as conn:
        count = conn.execute(sa.select(sa.func.count()).select_from(dbsnp_merges)).scalar_one()
    assert count == 2


def test_download_status_write_routes_through_serialized_write(tmp_path, monkeypatch):
    """The download-manager status/checkpoint writers — the tiny writers that
    *starved* the dbSNP build by repeatedly winning the write lock — also acquire
    the per-file lock (would fail if a future edit dropped the wrap)."""
    from backend.db import download_manager as dm
    from backend.db.tables import downloads

    engine = make_sqlite_engine(tmp_path / "reference.db")
    downloads.create(engine)

    entered: list[object] = []
    real = dm.serialized_write

    @contextmanager
    def spy(engine_arg):
        entered.append(engine_arg)
        with real(engine_arg):
            yield

    monkeypatch.setattr(dm, "serialized_write", spy)

    manager = dm.DownloadManager(engine, tmp_path / "dl", sleep=lambda _s: None)
    manager._update_download_status(download_id=1, status="downloading")

    assert entered, "download status write did not route through serialized_write"
