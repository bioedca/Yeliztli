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
import re
import stat
import threading
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
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

_CLAIM_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}", re.ASCII)


def validate_claim_key(db_name: str) -> None:
    """Reject malformed keys before creating locks or touching the filesystem.

    This is a filename-component contract, not a database registry: fixed
    internal keys such as ``mondo_hpo_source_bundles`` also need claims. Worker
    entrypoints separately validate which database operations they support.
    """
    if not isinstance(db_name, str) or _CLAIM_KEY.fullmatch(db_name) is None:
        raise ValueError("Invalid database claim key")


def _lock_for(db_name: str) -> threading.RLock:
    """Return the (lazily created) per-database reentrant lock for ``db_name``."""
    validate_claim_key(db_name)
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
    validate_claim_key(db_name)
    return claims_dir(data_dir) / f"{db_name}.claim"


def _storage_error() -> OSError:
    return OSError(errno.EPERM, "Unsafe or changed build-claim storage; see the storage contract")


def _same_inode(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _check_root_ancestors(root: Path) -> None:
    # A sticky shared ancestor (/tmp) protects each user-owned child against
    # other users' renames. A non-sticky shared ancestor does not.
    trusted_owners = {0, os.geteuid(), os.stat("/").st_uid}
    for parent in root.parents:
        try:
            info = parent.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in trusted_owners
            or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)
        ):
            raise _storage_error()


def _named_stat(name: str | Path, parent_fd: int | None) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _check_directory(name: str | Path, fd: int, parent_fd: int | None) -> None:
    opened, named = os.fstat(fd), _named_stat(name, parent_fd)
    if (
        named is None
        or not _same_inode(opened, named)
        or not stat.S_ISDIR(named.st_mode)
        or opened.st_uid != os.geteuid()
        or opened.st_mode & 0o022
    ):
        raise _storage_error()


@contextmanager
def _directory_fd(name: str | Path, parent_fd: int | None, *, create: bool):
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
    try:
        fd = os.open(
            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd
        )
    except FileNotFoundError:
        if create:
            raise
        yield None
        return
    try:
        _check_directory(name, fd, parent_fd)
        yield fd
    finally:
        os.close(fd)


def _check_marker(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise _storage_error()


def _open_marker(name: str, parent_fd: int, *, create: bool) -> int | None:
    named = _named_stat(name, parent_fd)
    if named is not None:
        _check_marker(named)
    elif not create:
        return None
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    if named is None:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(name, flags, 0o600, dir_fd=parent_fd)
    except FileExistsError:
        # A concurrent first acquirer created the marker. Never overwrite it.
        return _open_marker(name, parent_fd, create=False)
    except FileNotFoundError:
        if create:
            raise
        return None
    try:
        opened, current = os.fstat(fd), _named_stat(name, parent_fd)
        _check_marker(opened)
        if current is None or not _same_inode(opened, current):
            raise _storage_error()
        _check_marker(current)
        return fd
    except BaseException:
        os.close(fd)
        raise


@dataclass(frozen=True)
class _ClaimFile:
    root: Path
    root_fd: int
    claims_fd: int
    anchors_fd: int | None
    name: str
    fd: int

    def verify(self) -> tuple[os.stat_result | None, os.stat_result | None]:
        _check_directory(self.root, self.root_fd, None)
        _check_directory(".claims", self.claims_fd, self.root_fd)
        if self.anchors_fd is not None:
            _check_directory(".anchors", self.anchors_fd, self.claims_fd)
        elif _named_stat(".anchors", self.claims_fd) is not None:
            raise _storage_error()
        for _ in range(2):
            marker = _named_stat(self.name, self.claims_fd)
            anchor = (
                _named_stat(self.name, self.anchors_fd) if self.anchors_fd is not None else None
            )
            opened = os.fstat(self.fd)
            _check_marker(opened)
            present = [entry for entry in (marker, anchor) if entry is not None]
            for entry in present:
                _check_marker(entry)
                if not _same_inode(opened, entry):
                    raise _storage_error()
            if present and opened.st_nlink == len(present):
                return marker, anchor
            # Another first acquirer may publish the second name between stat
            # and fstat. Re-read once, still requiring exactly the internal links.
        raise _storage_error()

    def link_missing_anchor(self) -> None:
        marker, anchor = self.verify()
        if anchor is None:
            if marker is None or self.anchors_fd is None:
                raise _storage_error()
            try:
                os.link(
                    self.name,
                    self.name,
                    src_dir_fd=self.claims_fd,
                    dst_dir_fd=self.anchors_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                pass
            self.verify()

    def restore_marker(self) -> None:
        marker, anchor = self.verify()
        if marker is None:
            if anchor is None or self.anchors_fd is None:
                raise _storage_error()
            try:
                os.link(
                    self.name,
                    self.name,
                    src_dir_fd=self.anchors_fd,
                    dst_dir_fd=self.claims_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                pass
            self.verify()


@contextmanager
def _claim_file(db_name: str, data_dir: Path, *, create: bool):
    # The operator-selected root may itself be a symlink. Its physical target
    # and ancestors are trusted; links inside the claims namespace are not.
    root = data_dir.resolve()
    _check_root_ancestors(root)
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with ExitStack() as stack:
        root_fd = stack.enter_context(_directory_fd(root, None, create=False))
        if root_fd is None:
            yield None
            return
        claims_fd = stack.enter_context(_directory_fd(".claims", root_fd, create=create))
        if claims_fd is None:
            yield None
            return
        anchors_fd = stack.enter_context(_directory_fd(".anchors", claims_fd, create=create))
        name = f"{db_name}.claim"
        fd = _open_marker(name, anchors_fd, create=False) if anchors_fd is not None else None
        if fd is None:
            fd = _open_marker(name, claims_fd, create=create)
        if fd is None:
            if create:
                raise _storage_error()
            yield None
            return
        stack.callback(os.close, fd)
        claim = _ClaimFile(root, root_fd, claims_fd, anchors_fd, name, fd)
        claim.verify()
        if create:
            claim.link_missing_anchor()
        yield claim


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
    validate_claim_key(db_name)
    if fcntl is None:  # pragma: no cover - exercised only on non-POSIX
        yield True
        return

    with _claim_file(db_name, data_dir, create=True) as claim:
        assert claim is not None
        try:
            fcntl.flock(claim.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in _FLOCK_HELD_ERRNOS:
                yield False
                return
            raise
        try:
            claim.restore_marker()
            claim.verify()
            yield True
        finally:
            fcntl.flock(claim.fd, fcntl.LOCK_UN)


def is_cross_process_build_claimed(db_name: str, data_dir: Path) -> bool:
    """Best-effort probe: ``True`` if any process currently holds the claim.

    Used by the trigger/resume routes to fail fast (HTTP 409) instead of
    queueing a build that would immediately no-op. Probing opens a fresh
    descriptor and tries the lock non-blocking; a held lock (by this or any
    other process) denies it. The actual race protection is the claim acquired
    inside the build entrypoint — this probe is only a fast-path UX check, so a
    benign probe/acquire window is acceptable.
    """
    validate_claim_key(db_name)
    if fcntl is None:  # pragma: no cover - exercised only on non-POSIX
        return False
    with _claim_file(db_name, data_dir, create=False) as claim:
        if claim is None:
            return False
        try:
            fcntl.flock(claim.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in _FLOCK_HELD_ERRNOS:
                claim.verify()
                return True
            raise
        else:
            try:
                claim.verify()
                return False
            finally:
                fcntl.flock(claim.fd, fcntl.LOCK_UN)


# Per-thread set of (physical storage root, db_name) claims this thread already
# holds. ``cross_process_build_claim`` opens a fresh fd per call, and flock
# conflicts across fds *even within one thread* — so a nested (reentrant)
# build_claim would self-deny. Tracking holds per-thread lets nested calls
# reuse the outermost hold instead of re-flocking.
_held_claims = threading.local()


@contextmanager
def build_claim(db_name: str, data_dir: Path) -> Iterator[bool]:
    """Acquire both the in-process build slot and the cross-process claim.

    The single guard to wrap a build/download with. It is reentrant within a
    thread — mirroring :func:`build_lock`'s ``RLock`` — so it is safe to wrap an
    orchestrator (e.g. ``run_database_update_task``) *and* a leaf it calls
    synchronously; only the outermost acquisition takes the cross-process
    ``flock``, nested calls reuse it.

    Yields ``True`` if the work should proceed, or ``False`` if **another
    process** already owns the claim — the caller should then skip, because that
    process is provisioning this database. Acquiring :func:`build_lock` first
    means same-process builds of one DB are still serialized by the reentrant
    lock (only the holding thread ever reaches the ``flock``), so the
    ``flock`` arbitrates purely cross-process contention.
    """
    validate_claim_key(db_name)
    identity = (data_dir.resolve(), db_name)
    held = getattr(_held_claims, "names", None)
    if held is None:
        held = set()
        _held_claims.names = held

    with build_lock(db_name):
        if identity in held:
            # This thread already holds the cross-process claim further up the
            # call stack; reuse it rather than re-flock a fresh fd (self-deny).
            yield True
            return
        with cross_process_build_claim(db_name, data_dir) as acquired:
            if not acquired:
                yield False
                return
            held.add(identity)
            try:
                yield True
            finally:
                held.discard(identity)
