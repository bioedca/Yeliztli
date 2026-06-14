"""Process-global guard serializing concurrent builds of the same database.

Two builds of the same SQLite file running at once — a duplicate setup-wizard
download (the in-flight dedup in ``trigger_download`` has a check-then-act gap),
a wizard build racing an auto-update (which builds standalone DBs on its own
engine, bypassing the wizard's path), or an orphaned build thread after a
restart — open two independent write connections through the shared engine
pool.  On a multi-GB load they contend for the WAL write lock long enough that
``busy_timeout`` expires and one batch ``INSERT`` fails with
``OperationalError: database is locked``.

:func:`build_lock` serializes builds **per database name** with a blocking
lock, so only one writer is ever active for a given DB while different DBs
still build in parallel.  Callers should re-check whether the DB is already
present after acquiring (a concurrent build may have just finished it) to avoid
a redundant rebuild.
"""

from __future__ import annotations

import errno
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX (e.g. native Windows)
    fcntl = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from collections.abc import Iterator

# errnos a non-blocking flock raises when another holder owns the lock.
_FLOCK_HELD_ERRNOS = frozenset({errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK})

# Guards the ``_locks`` registry itself (NOT held during a build).
_registry_lock = threading.Lock()
# Per-DB locks are *reentrant*: a single thread that holds the build slot (e.g.
# the clean path, which acquires it then calls health, which probes the lock via
# is_build_locked) must not deadlock or self-report a phantom build. Reentrancy
# is same-thread only — cross-thread mutual exclusion is identical to a plain Lock.
_locks: dict[str, threading.RLock] = {}


def _lock_for(db_name: str) -> threading.RLock:
    """Return the (lazily created) per-database reentrant lock for ``db_name``."""
    with _registry_lock:
        lock = _locks.get(db_name)
        if lock is None:
            lock = threading.RLock()
            _locks[db_name] = lock
        return lock


@contextmanager
def build_lock(db_name: str) -> Iterator[None]:
    """Block until this thread owns the build slot for ``db_name``, then release.

    Same-DB builds run one at a time; different DBs are unaffected and keep
    building concurrently.
    """
    lock = _lock_for(db_name)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def is_build_locked(db_name: str) -> bool:
    """Best-effort check of whether a build is currently running for ``db_name``.

    Probes the per-DB lock without blocking: if it cannot be acquired, a builder
    holds it (a build is in flight). Used by health reporting to surface an
    in-progress build even when no session job links it (e.g. an update-manager
    rebuild). A builder holds the lock for the whole build, so a transient
    between-operations false negative is not possible mid-build.
    """
    lock = _lock_for(db_name)
    acquired = lock.acquire(blocking=False)
    if acquired:
        lock.release()
    return not acquired


@contextmanager
def try_acquire_build_lock(db_name: str) -> Iterator[bool]:
    """Non-blocking variant of :func:`build_lock` for mutually-exclusive callers.

    Yields ``True`` if this thread acquired the build slot (and releases it on
    exit), or ``False`` immediately if a build already holds it. Used by the
    "clean" path so removing a partial/corrupt artifact can never race a build
    of the same database.
    """
    lock = _lock_for(db_name)
    acquired = lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()


# ── Cross-process claim ──────────────────────────────────────────────
#
# The per-DB ``threading`` locks above serialize builds within ONE process.
# But the setup wizard builds in the API process while the scheduler / manual
# updates build in the Huey worker process, so two builds of the SAME SQLite
# file can still run at once across processes — the exact "database is locked"
# / corruption hazard ``build_lock`` was created to prevent, just one layer up.
#
# An OS advisory lock (``flock``) closes that gap: ``flock`` conflicts across
# independent open file descriptions — including descriptions held by different
# processes — and the kernel releases it automatically when the holding process
# exits, even on a crash. That gives cross-process mutual exclusion with no
# claim table, heartbeat, TTL, or orphan sweep to get wrong: a builder that dies
# mid-build never leaves a stuck claim.


def claims_dir(data_dir: Path) -> Path:
    """Directory holding per-DB cross-process claim lockfiles."""
    return data_dir / ".claims"


def _claim_path(data_dir: Path, db_name: str) -> Path:
    return claims_dir(data_dir) / f"{db_name}.claim"


@contextmanager
def cross_process_build_claim(db_name: str, data_dir: Path) -> Iterator[bool]:
    """Try to claim ``db_name`` for a build/download across processes.

    Yields ``True`` if this process won an exclusive, non-blocking ``flock`` on
    the per-DB claim file (released on exit), or ``False`` immediately if
    another process already holds it — the caller should then skip, because the
    other process is provisioning this database.

    On a platform without :mod:`fcntl` (native Windows) this degrades to a
    no-op that yields ``True``: the in-process :func:`build_lock` still applies,
    only the cross-process guarantee is unavailable there. The supported
    deployment targets (Linux/macOS, incl. WSL) all provide ``flock``.
    """
    if fcntl is None:  # pragma: no cover - exercised only on non-POSIX
        yield True
        return

    claims_dir(data_dir).mkdir(parents=True, exist_ok=True)
    # O_CLOEXEC so a child process (e.g. a subprocess spawned mid-build) does
    # not inherit the descriptor and accidentally extend the claim's lifetime.
    fd = os.open(_claim_path(data_dir, db_name), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in _FLOCK_HELD_ERRNOS:
                yield False
                return
            raise
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def is_cross_process_build_claimed(db_name: str, data_dir: Path) -> bool:
    """Best-effort probe: ``True`` if any process currently holds the claim.

    Used by the trigger/resume routes to fail fast (HTTP 409) instead of
    queueing a build that would immediately no-op. Probing opens a fresh
    descriptor and tries the lock non-blocking; a held lock (by this or any
    other process) denies it. The actual race protection is the claim acquired
    inside the build entrypoint — this probe is only a fast-path UX check, so a
    benign probe/acquire window is acceptable.
    """
    if fcntl is None:  # pragma: no cover - exercised only on non-POSIX
        return False
    path = _claim_path(data_dir, db_name)
    if not path.exists():
        return False
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in _FLOCK_HELD_ERRNOS:
            return True
        raise
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)
