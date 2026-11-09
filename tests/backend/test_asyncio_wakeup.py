"""The real backend session diagnoses denied wakeup sockets before collection."""

from __future__ import annotations

import errno
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from _asyncio_wakeup import check_asyncio_wakeup

_ROOT = Path(__file__).resolve().parents[2]
_CHILD = r"""
import errno
import json
import socket
import sys
import tempfile
from pathlib import Path

import pytest

denied = sys.argv[1] == "denied"
late = sys.argv[2] == "late"
real_send = socket.socket.send
sent = []
collected = []
modules = []
homes = []
real_mkdtemp = tempfile.mkdtemp

def mkdtemp(*args, **kwargs):
    path = real_mkdtemp(*args, **kwargs)
    if kwargs.get("prefix") == "yeliztli-test-homes-":
        homes.append(Path(path))
    return path

tempfile.mkdtemp = mkdtemp

def send(sock, data, *args, **kwargs):
    sent.append({"byte": data.hex(), "blocking": sock.getblocking()})
    if denied:
        raise PermissionError(errno.EPERM, "synthetic wakeup denial")
    return real_send(sock, data, *args, **kwargs)

socket.socket.send = send

class ObserveCollection:
    def pytest_ignore_collect(self, collection_path):
        # Let pytest discover the real backend conftest after session startup
        # without recursively collecting the entire backend suite in this test.
        if collection_path.is_dir():
            return collection_path.name != "backend"
        return (
            collection_path.name.startswith("test_")
            and collection_path.name != "test_no_network.py"
        )

    def pytest_pycollect_makemodule(self, module_path):
        modules.append(module_path.name)

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, items):
        collected.extend(item.nodeid for item in items)

code = pytest.main([
    "tests" if late else "tests/backend/test_no_network.py",
    "-k", "test_the_session_pins_the_committed_manifest",
    "-q", "-o", "addopts=", "-p", "no:cacheprovider",
], plugins=[ObserveCollection()])
print("WAKEUP_OBSERVATION=" + json.dumps({
    "sent": sent, "collected": collected, "modules": modules,
    "homes_created": len(homes), "homes_remaining": sum(home.exists() for home in homes),
}))
sys.exit(code)
"""


@pytest.mark.parametrize("denied", [True, False], ids=["eperm", "healthy"])
@pytest.mark.parametrize("late", [False, True], ids=["initial-conftest", "late-conftest"])
def test_real_backend_startup_checks_wakeup_before_collection(denied: bool, late: bool) -> None:
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _CHILD,
            "denied" if denied else "healthy",
            "late" if late else "initial",
        ],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    output = result.stdout + result.stderr
    observations = [
        line.removeprefix("WAKEUP_OBSERVATION=")
        for line in result.stdout.splitlines()
        if line.startswith("WAKEUP_OBSERVATION=")
    ]
    assert len(observations) == 1, output
    observed = json.loads(observations[0])
    assert observed["homes_created"] > 0
    assert observed["homes_remaining"] == 0
    if denied:
        assert result.returncode == 2, output
        assert "ASYNCIO_WAKEUP_BLOCKED" in output
        assert "EPERM" in output
        assert "outside" in output and "sandbox" in output
        assert observed["collected"] == []
        assert observed["modules"] == []
    else:
        assert result.returncode == 0, output
        assert "1 passed" in output
        assert "ASYNCIO_WAKEUP_BLOCKED" not in output
        assert observed["collected"] == [
            "tests/backend/test_no_network.py::test_the_session_pins_the_committed_manifest"
        ]
        assert observed["modules"] == ["test_no_network.py"]
    assert observed["sent"] == [{"byte": "00", "blocking": False}]


@pytest.fixture
def wakeup_pair(monkeypatch: pytest.MonkeyPatch):
    pair = socket.socketpair()
    monkeypatch.setattr(socket, "socketpair", lambda: pair)
    try:
        yield pair
    finally:
        # Also clean up if the implementation or a regression assertion fails.
        for sock in pair:
            sock.close()


def test_real_socketpair_passes_and_both_sockets_close(wakeup_pair) -> None:
    check_asyncio_wakeup()
    assert [sock.fileno() for sock in wakeup_pair] == [-1, -1]


@pytest.mark.parametrize("number", [errno.EPERM, errno.EACCES])
def test_denied_send_exits_after_closing_both_sockets(
    monkeypatch: pytest.MonkeyPatch, wakeup_pair, number: int
) -> None:
    def denied(sock, data):
        assert sock is wakeup_pair[1]
        assert data == b"\0"
        assert [item.getblocking() for item in wakeup_pair] == [False, False]
        raise PermissionError(number, "synthetic denial")

    monkeypatch.setattr(socket.socket, "send", denied)
    with pytest.raises(pytest.exit.Exception, match="ASYNCIO_WAKEUP_BLOCKED") as caught:
        check_asyncio_wakeup()
    assert caught.value.returncode == 2
    assert errno.errorcode[number] in str(caught.value)
    assert [sock.fileno() for sock in wakeup_pair] == [-1, -1]


@pytest.mark.parametrize("number", [errno.EPERM, errno.EACCES])
def test_denied_socketpair_creation_also_names_the_environment(
    monkeypatch: pytest.MonkeyPatch, number: int
) -> None:
    def denied():
        raise PermissionError(number, "synthetic creation denial")

    monkeypatch.setattr(socket, "socketpair", denied)
    with pytest.raises(pytest.exit.Exception, match="ASYNCIO_WAKEUP_BLOCKED") as caught:
        check_asyncio_wakeup()
    assert caught.value.returncode == 2
    assert errno.errorcode[number] in str(caught.value)


@pytest.mark.parametrize(
    "error",
    [
        OSError(errno.EIO, "synthetic IO failure"),
        BlockingIOError(errno.EAGAIN, "synthetic would-block"),
        TimeoutError(errno.ETIMEDOUT, "synthetic timeout"),
        TimeoutError("synthetic timeout without errno"),
        PermissionError("synthetic permission error without errno"),
    ],
    ids=["io", "would-block", "timeout", "timeout-no-errno", "permission-no-errno"],
)
def test_other_send_errors_propagate_without_a_sandbox_diagnosis(
    monkeypatch: pytest.MonkeyPatch, wakeup_pair, error: OSError
) -> None:
    def failed(sock, data):
        raise error

    monkeypatch.setattr(socket.socket, "send", failed)
    with pytest.raises(OSError) as caught:
        check_asyncio_wakeup()
    assert caught.value is error
    assert [sock.fileno() for sock in wakeup_pair] == [-1, -1]


def test_a_zero_byte_send_fails_without_a_sandbox_diagnosis(
    monkeypatch: pytest.MonkeyPatch, wakeup_pair
) -> None:
    monkeypatch.setattr(socket.socket, "send", lambda sock, data: 0)
    with pytest.raises(OSError, match="did not send its byte") as caught:
        check_asyncio_wakeup()
    assert caught.value.errno == errno.EIO
    assert [sock.fileno() for sock in wakeup_pair] == [-1, -1]
