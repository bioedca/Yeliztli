"""Tests for backend.db.build_guard (per-database build serialization)."""

from __future__ import annotations

import multiprocessing
import os
import threading
import time
from pathlib import Path

from backend.db.build_guard import (
    build_claim,
    build_lock,
    claims_dir,
    cross_process_build_claim,
    is_cross_process_build_claimed,
)

# Module-level child targets (must be importable/picklable for spawn safety).


def _child_hold_until(data_dir_str: str, acquired_evt, release_evt) -> None:
    """Acquire the claim, signal, hold until released, then exit normally."""
    with cross_process_build_claim("clinvar", Path(data_dir_str)) as got:
        if got:
            acquired_evt.set()
            release_evt.wait(timeout=10)


def _child_acquire_and_die(data_dir_str: str, acquired_evt) -> None:
    """Acquire the claim, signal, then die WITHOUT releasing (simulated crash).

    ``os._exit`` skips the context manager's ``__exit__`` (no explicit
    ``flock(LOCK_UN)``), so only the kernel's on-death release can free it.
    """
    with cross_process_build_claim("clinvar", Path(data_dir_str)) as got:
        if got:
            acquired_evt.set()
        os._exit(0)


class TestBuildLock:
    def test_serializes_same_database(self) -> None:
        """A second build of the same DB blocks until the first releases."""
        events: list[str] = []
        first_holding = threading.Event()
        release_first = threading.Event()

        def first() -> None:
            with build_lock("dbnsfp"):
                events.append("first-acquired")
                first_holding.set()
                release_first.wait(timeout=5)
                events.append("first-releasing")

        def second() -> None:
            first_holding.wait(timeout=5)  # ensure first holds the lock
            events.append("second-trying")
            with build_lock("dbnsfp"):
                events.append("second-acquired")

        t1 = threading.Thread(target=first)
        t2 = threading.Thread(target=second)
        t1.start()
        t2.start()

        # Give the second thread time to block on the lock.
        time.sleep(0.2)
        assert "second-trying" in events
        assert "second-acquired" not in events  # still blocked

        release_first.set()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # second must only acquire after first released.
        assert events.index("first-releasing") < events.index("second-acquired")

    def test_different_databases_do_not_block(self) -> None:
        """Holding the lock for one DB must not block a different DB."""
        gnomad_acquired = threading.Event()
        hold_dbnsfp = threading.Event()

        def hold_dbnsfp_lock() -> None:
            with build_lock("dbnsfp"):
                hold_dbnsfp.wait(timeout=5)

        def acquire_gnomad() -> None:
            with build_lock("gnomad"):
                gnomad_acquired.set()

        t1 = threading.Thread(target=hold_dbnsfp_lock)
        t2 = threading.Thread(target=acquire_gnomad)
        t1.start()
        t2.start()

        # gnomad should acquire promptly despite dbnsfp being held.
        assert gnomad_acquired.wait(timeout=5)

        hold_dbnsfp.set()
        t1.join(timeout=5)
        t2.join(timeout=5)

    def test_lock_released_on_exception(self) -> None:
        """An exception inside the guard still releases the lock."""
        try:
            with build_lock("cpic"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        # Re-acquiring must not deadlock.
        acquired = threading.Event()

        def reacquire() -> None:
            with build_lock("cpic"):
                acquired.set()

        t = threading.Thread(target=reacquire)
        t.start()
        assert acquired.wait(timeout=5)
        t.join(timeout=5)


class TestCrossProcessBuildClaim:
    """The flock-backed cross-process claim layered over the in-process lock.

    flock conflicts across *independent open file descriptions* — including
    descriptions held by different processes — so two ``os.open`` descriptions
    in one test process exercise the very same kernel mutual-exclusion that
    spans the API and Huey processes in production. The multiprocessing tests
    then prove the end-to-end cross-process behaviour, including the kernel's
    automatic release when a holder dies.
    """

    def test_second_acquirer_blocked_while_held(self, tmp_path: Path) -> None:
        with cross_process_build_claim("clinvar", tmp_path) as first:
            assert first is True
            # A second, independent description cannot acquire while the first
            # holds it (this is exactly the cross-process denial).
            with cross_process_build_claim("clinvar", tmp_path) as second:
                assert second is False
        # Released on exit → a fresh claim succeeds.
        with cross_process_build_claim("clinvar", tmp_path) as third:
            assert third is True

    def test_different_dbs_do_not_conflict(self, tmp_path: Path) -> None:
        with cross_process_build_claim("clinvar", tmp_path) as a:
            with cross_process_build_claim("cpic", tmp_path) as b:
                assert a is True
                assert b is True

    def test_probe_reflects_claim_state(self, tmp_path: Path) -> None:
        assert is_cross_process_build_claimed("clinvar", tmp_path) is False
        with cross_process_build_claim("clinvar", tmp_path):
            assert is_cross_process_build_claimed("clinvar", tmp_path) is True
            # A different DB is independent.
            assert is_cross_process_build_claimed("cpic", tmp_path) is False
        assert is_cross_process_build_claimed("clinvar", tmp_path) is False

    def test_claim_file_lives_under_claims_dir(self, tmp_path: Path) -> None:
        with cross_process_build_claim("clinvar", tmp_path):
            assert (claims_dir(tmp_path) / "clinvar.claim").exists()

    def test_claim_file_is_owner_only(self, tmp_path: Path) -> None:
        # The empty flock marker holds no data and is this user's private lock,
        # so it must not be group/world readable (CodeQL py/overly-permissive-
        # file). The file is opened 0o600; O_CREAT honours umask, which can only
        # clear bits, so group/other must stay unset regardless of umask.
        with cross_process_build_claim("clinvar", tmp_path):
            mode = (claims_dir(tmp_path) / "clinvar.claim").stat().st_mode & 0o777
            assert mode & 0o077 == 0, f"claim file is group/world accessible: {oct(mode)}"

    def test_cross_process_mutual_exclusion(self, tmp_path: Path) -> None:
        ctx = multiprocessing.get_context("fork")
        acquired, release = ctx.Event(), ctx.Event()
        proc = ctx.Process(target=_child_hold_until, args=(str(tmp_path), acquired, release))
        proc.start()
        try:
            assert acquired.wait(timeout=10), "child never acquired the claim"
            # While the child holds it, this process cannot claim or sees it held.
            with cross_process_build_claim("clinvar", tmp_path) as got:
                assert got is False
            assert is_cross_process_build_claimed("clinvar", tmp_path) is True
        finally:
            release.set()
            proc.join(timeout=10)
        assert proc.exitcode == 0
        # After the child releases and exits, the claim is free again.
        with cross_process_build_claim("clinvar", tmp_path) as got:
            assert got is True

    def test_os_releases_claim_on_holder_death(self, tmp_path: Path) -> None:
        ctx = multiprocessing.get_context("fork")
        acquired = ctx.Event()
        proc = ctx.Process(target=_child_acquire_and_die, args=(str(tmp_path), acquired))
        proc.start()
        assert acquired.wait(timeout=10), "child never acquired the claim"
        proc.join(timeout=10)
        assert proc.exitcode == 0
        # The child died holding the claim and never ran an explicit unlock;
        # the kernel must have released the flock on process death.
        with cross_process_build_claim("clinvar", tmp_path) as got:
            assert got is True


class TestBuildClaim:
    """The reentrant combinator wrapping build_lock + the cross-process flock.

    Reentrancy (mirroring build_lock's RLock) is what makes it safe to wrap an
    orchestrator *and* a leaf it calls synchronously: only the outermost
    per-thread acquisition takes the flock, nested calls reuse it instead of
    self-denying on a fresh fd.
    """

    def test_acquires_then_frees_for_other_holders(self, tmp_path: Path) -> None:
        with build_claim("clinvar", tmp_path) as got:
            assert got is True
        # Released on exit → the raw flock is free again.
        with cross_process_build_claim("clinvar", tmp_path) as raw:
            assert raw is True

    def test_reentrant_same_thread(self, tmp_path: Path) -> None:
        with build_claim("clinvar", tmp_path) as outer:
            assert outer is True
            # A nested claim of the SAME db on the SAME thread reuses the hold
            # rather than self-denying on a new fd.
            with build_claim("clinvar", tmp_path) as inner:
                assert inner is True

    def test_blocked_when_flock_held_elsewhere(self, tmp_path: Path) -> None:
        # A raw flock held on another description (stands in for another
        # process) denies build_claim, which surfaces as a False yield.
        with cross_process_build_claim("clinvar", tmp_path):
            with build_claim("clinvar", tmp_path) as got:
                assert got is False

    def test_different_dbs_independent(self, tmp_path: Path) -> None:
        with build_claim("clinvar", tmp_path) as a:
            with build_claim("cpic", tmp_path) as b:
                assert a is True
                assert b is True

    def test_download_claim_independent_of_global_build_lock(self, tmp_path: Path) -> None:
        """The download path's per-data_dir flock must not depend on the
        process-global, db-name-keyed build_lock.

        Regression guard: a held build_lock for a DB (e.g. a slow/failing
        build of it elsewhere) must NOT block a download claim for the same DB
        in a different data_dir. ``_run_download``/``_run_bundle_install`` use
        ``cross_process_build_claim`` (this flock) precisely so a stalled build
        can't wedge an unrelated download.
        """
        with build_lock("clinvar"):
            with cross_process_build_claim("clinvar", tmp_path) as got:
                assert got is True
