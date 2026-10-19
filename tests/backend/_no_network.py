"""Keep the backend suite off the real network (#2538).

Nothing in the suite may reach a non-loopback host. Test outcomes must not
depend on whether the runner is online, or on what GitHub currently serves. The
bundle manifest was the case in point: without ``YELIZTLI_MANIFEST_PATH``, every
``get_bundle_info()`` call downloaded it from ``raw.githubusercontent.com``. So
whether a ``require_fresh_sample`` route answered 423 depended on the manifest
published on ``main``, and only the first such test in a process fetched it
(the rest got the 1 h cache).

:func:`install` replaces ``socket.getaddrinfo`` and ``socket.socket.connect``
(and ``connect_ex``) once, when ``tests/backend/conftest.py`` is imported. Only
while a test runs under :func:`begin`, a lookup or connect to a non-loopback
host is refused and recorded, and :func:`end` returns what was recorded so the
test fails. Refusing alone is not enough: the code under test usually catches
the error and silently takes its offline path, and the test would still pass.
"""

from __future__ import annotations

import errno
import socket
import threading
from typing import Any

_LOOPBACK = ("127.", "::1", "localhost", "0.0.0.0")

_state: dict[str, Any] = {"installed": False, "enforce": False, "violations": []}
_lock = threading.Lock()


def _is_local(host: object) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    text = str(host)
    return text == "" or text.startswith(_LOOPBACK)


def _refuse(action: str, host: object) -> bool:
    """Record and refuse ``action`` on ``host`` when enforcing; else allow it."""
    if not _state["enforce"] or _is_local(host):
        return False
    with _lock:
        _state["violations"].append(f"{action} {host} (thread {threading.current_thread().name})")
    return True


def install() -> None:
    """Wrap the socket entry points once; they stay permissive until :func:`begin`."""
    if _state["installed"]:
        return
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if _refuse("DNS lookup of", host):
            raise socket.gaierror(socket.EAI_NONAME, f"network blocked in tests: {host}")
        return real_getaddrinfo(host, *args, **kwargs)

    def connect(self: socket.socket, address: Any) -> None:
        # AF_UNIX addresses are paths, not (host, port) tuples.
        host = address[0] if isinstance(address, tuple) else None
        if _refuse("connect to", host):
            raise OSError(errno.ENETUNREACH, f"network blocked in tests: {host}")
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        host = address[0] if isinstance(address, tuple) else None
        if _refuse("connect to", host):
            return errno.ENETUNREACH
        return real_connect_ex(self, address)

    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    _state["installed"] = True


def begin(*, enforce: bool) -> None:
    """Start a test: refuse non-loopback access when ``enforce``, with a clean record."""
    with _lock:
        _state["violations"] = []
    _state["enforce"] = enforce


def end() -> list[str]:
    """Stop refusing and return what the test tried to reach."""
    _state["enforce"] = False
    with _lock:
        return list(_state["violations"])
