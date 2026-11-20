"""Admission and rollback fixtures use independent, disposable installations."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend import diagnostic_cutover as policy


def test_fresh_native_start_and_concurrent_services(tmp_path):
    for directory in ("samples", "downloads", "logs"):
        (tmp_path / directory).mkdir()
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(policy.admit, [tmp_path] * 2)) == [None, None]
    (tmp_path / "reference.db").touch()
    policy.admit(tmp_path)
    assert (tmp_path / "reference.db").is_file()


def test_existing_queue_waits_for_confirmed_cutover_and_compatible_runtime(tmp_path, monkeypatch):
    queue = tmp_path / "huey.db"
    queue.write_bytes(b"synthetic queue snapshot")
    with pytest.raises(policy.CutoverPending):
        policy.admit(tmp_path)
    assert queue.read_bytes() == b"synthetic queue snapshot"
    policy.admit(tmp_path, operator_verified=True)
    policy.admit(tmp_path)
    monkeypatch.setattr(policy, "LOG_ENTRY_PRESENTATION_POLICY_VERSION", 2)
    with pytest.raises(policy.CutoverPending):
        policy.admit(tmp_path, operator_verified=True)
    assert queue.read_bytes() == b"synthetic queue snapshot"


def test_unrecognized_receipt_never_attests_an_installation(tmp_path: Path):
    (tmp_path / policy.RECEIPT).write_bytes(b"unknown")
    with pytest.raises(policy.CutoverPending):
        policy.admit(tmp_path, operator_verified=True)
