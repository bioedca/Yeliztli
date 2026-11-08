"""Claim storage rejects pathname substitutions and preserves inode ownership."""

from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

import pytest

from backend.db import build_guard as guard


def _layout(root):
    claims = root / ".claims"
    claims.mkdir(mode=0o700)
    anchors = claims / ".anchors"
    anchors.mkdir(mode=0o700)
    marker = claims / "clinvar.claim"
    marker.write_bytes(b"synthetic marker contents")
    marker.chmod(0o600)
    anchor = anchors / marker.name
    anchor.hardlink_to(marker)
    return claims, anchors, marker, anchor


def _operate(operation, root):
    if operation == "probe":
        return guard.is_cross_process_build_claimed("clinvar", root)
    with guard.cross_process_build_claim("clinvar", root) as acquired:
        return acquired


@pytest.mark.parametrize("operation", ["acquire", "probe"])
@pytest.mark.parametrize("existing", [False, True])
def test_marker_symlink_rejected(operation, existing, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    claims = data / ".claims"
    claims.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    if existing:
        outside.write_bytes(b"preserve")
    (claims / "clinvar.claim").symlink_to(outside)
    with pytest.raises(OSError):
        _operate(operation, data)
    assert outside.read_bytes() == b"preserve" if existing else not outside.exists()


@pytest.mark.parametrize("operation", ["acquire", "probe"])
@pytest.mark.parametrize("directory", [".claims", ".anchors"])
def test_directory_symlink_rejected(operation, directory, tmp_path):
    data, outside = tmp_path / "data", tmp_path / "outside"
    data.mkdir()
    outside.mkdir()
    (outside / "clinvar.claim").write_bytes(b"preserve")
    if directory == ".claims":
        (data / directory).symlink_to(outside, target_is_directory=True)
    else:
        claims = data / ".claims"
        claims.mkdir(mode=0o700)
        (claims / directory).symlink_to(outside, target_is_directory=True)
    before = {p.name: p.read_bytes() for p in outside.iterdir()}
    with pytest.raises(OSError):
        _operate(operation, data)
    assert {p.name: p.read_bytes() for p in outside.iterdir()} == before


@pytest.mark.parametrize("operation", ["acquire", "probe"])
@pytest.mark.parametrize("directory", ["root", ".claims", ".anchors"])
def test_other_writable_storage_rejected(operation, directory, tmp_path):
    claims, anchors, _, _ = _layout(tmp_path)
    unsafe = {"root": tmp_path, ".claims": claims, ".anchors": anchors}[directory]
    unsafe.chmod(0o777)
    try:
        with pytest.raises(OSError):
            _operate(operation, tmp_path)
    finally:
        unsafe.chmod(0o700)


@pytest.mark.parametrize("operation", ["acquire", "probe"])
def test_non_sticky_shared_ancestor_rejected(operation, tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir()
    data = parent / "data"
    data.mkdir()
    _layout(data)
    parent.chmod(0o777)
    try:
        with pytest.raises(OSError):
            _operate(operation, data)
    finally:
        parent.chmod(0o700)


@pytest.mark.parametrize("umask", [0o002, 0o000])
def test_missing_storage_ancestors_are_private_on_every_acquisition(umask, tmp_path):
    root = tmp_path / "first" / "second" / "data"
    program = """
import os, sys
from pathlib import Path
from backend.db.build_guard import cross_process_build_claim
root = Path(sys.argv[1])
os.umask(int(sys.argv[2], 8))
for attempt in range(2):
    with cross_process_build_claim('clinvar', root) as acquired:
        assert acquired
        modes = [p.stat().st_mode & 0o777 for p in (root.parent.parent, root.parent, root)]
        assert modes == [0o700, 0o700, 0o700], modes
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(root), oct(umask)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert [p.stat().st_mode & 0o777 for p in (root.parent.parent, root.parent, root)] == [
        0o700,
        0o700,
        0o700,
    ]


@pytest.mark.parametrize("operation", ["acquire", "probe"])
def test_foreign_owned_storage_rejected(operation, tmp_path, monkeypatch):
    _layout(tmp_path)
    monkeypatch.setattr(guard.os, "geteuid", lambda: os.getuid() + 1)
    with pytest.raises(OSError):
        _operate(operation, tmp_path)


@pytest.mark.parametrize("operation", ["acquire", "probe"])
def test_root_metadata_does_not_authenticate_a_foreign_ancestor(operation, tmp_path, monkeypatch):
    parent = tmp_path / "foreign-parent"
    parent.mkdir()
    data = parent / "data"
    data.mkdir()
    _layout(data)
    foreign_uid = os.geteuid() + 10000
    real_stat = os.stat

    def foreign_owner(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if (
            kwargs.get("dir_fd") is None
            and isinstance(path, (str, Path))
            and Path(path) in (Path("/"), parent)
        ):
            fields = list(info)
            fields[4] = foreign_uid
            return os.stat_result(fields)
        return info

    monkeypatch.setattr(guard.os, "stat", foreign_owner)
    with pytest.raises(OSError):
        _operate(operation, data)


@pytest.mark.parametrize("operation", ["acquire", "probe"])
@pytest.mark.parametrize("kind", ["fifo", "directory", "writable", "hardlink"])
def test_unsafe_marker_rejected(operation, kind, tmp_path):
    claims = tmp_path / ".claims"
    claims.mkdir(mode=0o700)
    marker = claims / "clinvar.claim"
    outside = tmp_path / "outside"
    outside.write_bytes(b"preserve")
    if kind == "fifo":
        os.mkfifo(marker, 0o600)
    elif kind == "directory":
        marker.mkdir()
    elif kind == "hardlink":
        marker.hardlink_to(outside)
    else:
        marker.touch(mode=0o666)
        marker.chmod(0o666)
    with pytest.raises(OSError):
        _operate(operation, tmp_path)
    assert outside.read_bytes() == b"preserve"


def test_probe_open_never_creates(tmp_path, monkeypatch):
    _layout(tmp_path)
    real_open, calls = os.open, []

    def observe(path, flags, *args, **kwargs):
        calls.append(flags)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(guard.os, "open", observe)
    assert not guard.is_cross_process_build_claimed("clinvar", tmp_path)
    assert calls and all(flags & os.O_CREAT == 0 for flags in calls)


def test_concurrent_anchor_publication_is_not_a_storage_error(tmp_path, monkeypatch):
    _, anchors, marker, anchor = _layout(tmp_path)
    anchor.unlink()
    real_stat, observations = guard._named_stat, []

    def publish(name, parent_fd):
        result = real_stat(name, parent_fd)
        if (
            name == marker.name
            and parent_fd is not None
            and os.fstat(parent_fd).st_ino == anchors.stat().st_ino
            and result is None
        ):
            observations.append(True)
            if len(observations) == 2:
                # Another normal initializer publishes the same inode between
                # this observer's directory stat and descriptor fstat.
                anchor.hardlink_to(marker)
        return result

    monkeypatch.setattr(guard, "_named_stat", publish)
    with guard.cross_process_build_claim("clinvar", tmp_path) as acquired:
        assert acquired
    assert len(observations) == 2 and marker.samefile(anchor)


@pytest.mark.parametrize(("operation", "expected"), [("acquire", False), ("probe", True)])
def test_observer_waits_for_in_progress_anchor_publication(
    operation, expected, tmp_path, monkeypatch
):
    _, anchors, marker, anchor = _layout(tmp_path)
    started, finish = threading.Event(), threading.Event()
    publisher = []
    real_link, real_stat, real_flock = os.link, guard._named_stat, guard.fcntl.flock
    anchors_identity = anchors.stat()

    def publishing_link(*args, **kwargs):
        real_link(*args, **kwargs)
        publisher.append(threading.get_ident())
        started.set()
        assert finish.wait(10)

    def named_stat(name, parent_fd):
        # Model the observed filesystem interval: link count has increased,
        # but a concurrent lookup still cannot see the unpublished name.
        if (
            started.is_set()
            and not finish.is_set()
            and name == marker.name
            and parent_fd is not None
            and guard._same_inode(os.fstat(parent_fd), anchors_identity)
        ):
            return None
        return real_stat(name, parent_fd)

    def flock(fd, flags):
        if (
            started.is_set()
            and threading.get_ident() != publisher[0]
            and guard._same_inode(os.fstat(fd), anchors_identity)
        ):
            # Let publication finish when the observer reaches the namespace
            # lock. The real flock still arbitrates the two open descriptors.
            finish.set()
        return real_flock(fd, flags)

    with guard.cross_process_build_claim("clinvar", tmp_path) as held:
        assert held
        anchor.unlink()
        with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=2) as workers:
            patch.setattr(guard.os, "link", publishing_link)
            patch.setattr(guard, "_named_stat", named_stat)
            patch.setattr(guard.fcntl, "flock", flock)
            initializer = workers.submit(_operate, "acquire", tmp_path)
            try:
                assert started.wait(10)
                observer = workers.submit(_operate, operation, tmp_path)
                assert observer.result(timeout=10) is expected
            finally:
                finish.set()
            assert initializer.result(timeout=10) is False
        assert len(publisher) == 1
        assert marker.samefile(anchor) and marker.stat().st_nlink == 2
    assert not guard.is_cross_process_build_claimed("clinvar", tmp_path)


@pytest.mark.parametrize("operation", ["acquire", "probe"])
def test_published_claim_rejects_an_extra_hardlink(operation, tmp_path):
    _, _, marker, _ = _layout(tmp_path)
    outside = tmp_path / "unexpected-link"
    outside.hardlink_to(marker)
    with pytest.raises(OSError):
        _operate(operation, tmp_path)
    assert outside.read_bytes() == b"synthetic marker contents"
    assert marker.stat().st_nlink == 3


def test_absent_probe_has_no_side_effects(tmp_path):
    data = tmp_path / "absent"
    assert not guard.is_cross_process_build_claimed("clinvar", data)
    assert not data.exists()
    assert not guard.is_cross_process_build_claimed("clinvar", tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("other_key", "publication_phase", "expected"),
    [
        ("clinvar", "open", True),
        ("cpic", "open", False),
        ("cpic", "locked", False),
        ("clinvar", "link-count", True),
    ],
)
def test_probe_retries_concurrent_anchor_directory_publication(
    other_key, publication_phase, expected, tmp_path, monkeypatch
):
    claims = tmp_path / ".claims"
    claims.mkdir(mode=0o700)
    marker = claims / "clinvar.claim"
    marker.write_bytes(b"legacy marker")
    marker.chmod(0o600)
    published, verifications = [], []
    original_open, original_verify = guard._open_marker, guard._ClaimFile.verify
    original_stat, state = guard._named_stat, {"verifying": False}
    with ExitStack() as holders, monkeypatch.context() as patch:

        def publish():
            published.append(True)
            assert holders.enter_context(guard.cross_process_build_claim(other_key, tmp_path))

        def open_marker(name, parent_fd, *, create):
            fd = original_open(name, parent_fd, create=create)
            if (
                name == marker.name
                and fd is not None
                and not published
                and publication_phase == "open"
            ):
                publish()
            return fd

        def verify(claim):
            if claim.name == marker.name and not published and publication_phase == "locked":
                verifications.append(True)
                if len(verifications) == 2:
                    publish()
            previous = state["verifying"]
            state["verifying"] = claim.name == marker.name
            try:
                return original_verify(claim)
            finally:
                state["verifying"] = previous

        def named_stat(name, parent_fd):
            info = original_stat(name, parent_fd)
            if (
                name == marker.name
                and state["verifying"]
                and not published
                and publication_phase == "link-count"
            ):
                publish()
            return info

        patch.setattr(guard, "_open_marker", open_marker)
        patch.setattr(guard._ClaimFile, "verify", verify)
        patch.setattr(guard, "_named_stat", named_stat)
        assert guard.is_cross_process_build_claimed("clinvar", tmp_path) is expected
        assert published == [True]
        if other_key == "cpic":
            assert not (claims / ".anchors" / marker.name).exists()
    assert marker.read_bytes() == b"legacy marker"
    assert not guard.is_cross_process_build_claimed("clinvar", tmp_path)


def test_probe_retry_rejects_a_new_symlinked_anchor_directory(tmp_path, monkeypatch):
    claims = tmp_path / ".claims"
    claims.mkdir(mode=0o700)
    marker = claims / "clinvar.claim"
    marker.touch(mode=0o600)
    outside = tmp_path / "outside"
    outside.mkdir()
    original, published = guard._open_marker, []

    def open_marker(name, parent_fd, *, create):
        fd = original(name, parent_fd, create=create)
        if name == marker.name and fd is not None and not published:
            (claims / ".anchors").symlink_to(outside, target_is_directory=True)
            published.append(True)
        return fd

    with monkeypatch.context() as patch:
        patch.setattr(guard, "_open_marker", open_marker)
        with pytest.raises(OSError):
            guard.is_cross_process_build_claimed("clinvar", tmp_path)
    assert published == [True] and list(outside.iterdir()) == []


def test_probe_marker_swap_cannot_create_external_file(tmp_path, monkeypatch):
    claims, anchors, _, _ = _layout(tmp_path)
    outside = tmp_path / "outside"
    real_open, swapped = os.open, []

    def swap(path, flags, *args, **kwargs):
        if Path(path).name == "clinvar.claim" and not swapped:
            parent = claims
            if kwargs.get("dir_fd") is not None:
                opened = os.fstat(kwargs["dir_fd"])
                if opened.st_ino == anchors.stat().st_ino:
                    parent = anchors
            target = parent / "clinvar.claim"
            target.unlink()
            target.symlink_to(outside)
            swapped.append(True)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(guard.os, "open", swap)
    with pytest.raises(OSError):
        guard.is_cross_process_build_claimed("clinvar", tmp_path)
    assert swapped and not outside.exists()


@pytest.mark.parametrize("operation", ["acquire", "probe"])
def test_directory_swap_is_detected(operation, tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    claims, _, _, _ = _layout(data)
    outside = tmp_path / "outside"
    outside.mkdir()
    real_open, swapped = os.open, []

    def swap(path, flags, *args, **kwargs):
        name = Path(path).name
        if not swapped and name in (".claims", "clinvar.claim"):
            fd = real_open(path, flags, *args, **kwargs) if name == ".claims" else None
            claims.rename(data / "original-claims")
            claims.symlink_to(outside, target_is_directory=True)
            swapped.append(True)
            if fd is not None:
                return fd
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(guard.os, "open", swap)
    with pytest.raises(OSError):
        _operate(operation, data)
    assert swapped and list(outside.iterdir()) == []


@pytest.mark.parametrize("removed", ["marker", "anchor"])
def test_single_unlink_does_not_split_a_held_claim(removed, tmp_path):
    _, _, marker, anchor = _layout(tmp_path)
    with guard.cross_process_build_claim("clinvar", tmp_path) as first:
        assert first
        {"marker": marker, "anchor": anchor}[removed].unlink()
        assert guard.is_cross_process_build_claimed("clinvar", tmp_path)
        with guard.cross_process_build_claim("clinvar", tmp_path) as second:
            assert not second
    with guard.cross_process_build_claim("clinvar", tmp_path) as recovered:
        assert recovered
        assert marker.samefile(anchor)
        assert marker.stat().st_nlink == 2
    assert marker.read_bytes() == b"synthetic marker contents"


@pytest.mark.parametrize("replaced", ["marker", "anchor"])
def test_replaced_alias_is_rejected(replaced, tmp_path):
    _, _, marker, anchor = _layout(tmp_path)
    with guard.cross_process_build_claim("clinvar", tmp_path) as first:
        assert first
        target = {"marker": marker, "anchor": anchor}[replaced]
        target.unlink()
        target.touch(mode=0o600)
        with pytest.raises(OSError):
            _operate("acquire", tmp_path)
        with pytest.raises(OSError):
            _operate("probe", tmp_path)


def test_reentrancy_is_scoped_to_storage_root(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    with guard.cross_process_build_claim("clinvar", second) as held:
        assert held
        with guard.build_claim("clinvar", first) as outer:
            assert outer
            with guard.build_claim("clinvar", second) as inner:
                assert not inner


def test_selected_root_symlink_preserves_reentrancy(tmp_path):
    root, alias = tmp_path / "root", tmp_path / "alias"
    root.mkdir()
    alias.symlink_to(root, target_is_directory=True)
    with guard.build_claim("clinvar", root) as first:
        assert first
        with guard.build_claim("clinvar", alias) as nested:
            assert nested


def _first_claim_contender(root, start, release, results):
    try:
        os.umask(0o002)
        assert start.wait(10)
        with guard.cross_process_build_claim("clinvar", Path(root)) as acquired:
            results.put(acquired)
            if acquired:
                assert release.wait(10)
    except Exception as exc:
        results.put(type(exc).__name__)


@pytest.mark.parametrize("iteration", range(8))
def test_concurrent_first_creation_has_one_holder(tmp_path, iteration):
    root = tmp_path / f"case-{iteration}" / "nested" / "data"
    context = multiprocessing.get_context("fork")
    start, release, results = context.Event(), context.Event(), context.Queue()
    workers = [
        context.Process(target=_first_claim_contender, args=(str(root), start, release, results))
        for _ in range(8)
    ]
    for worker in workers:
        worker.start()
    try:
        start.set()
        observed = [results.get(timeout=10) for _ in workers]
        assert observed.count(True) == 1
        assert observed.count(False) == len(workers) - 1
    finally:
        release.set()
        for worker in workers:
            worker.join(10)
        results.close()
    assert all(worker.exitcode == 0 for worker in workers)
