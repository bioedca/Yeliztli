"""Diagnose permission-denied asyncio wakeup sockets without starting a loop."""

from __future__ import annotations

import errno
import socket

import pytest


def check_asyncio_wakeup() -> None:
    """Probe the selector loop's nonblocking self-pipe send (#2486).

    Python 3.12/3.13's ``BaseSelectorEventLoop._write_to_self`` catches
    ``OSError`` from this send. A sandbox-denied byte can therefore leave an
    AnyIO portal waiting forever. Probe directly so that denial stays visible;
    do not start a thread, wait for a timeout, or modify the event loop.
    """
    try:
        reader, writer = socket.socketpair()
        with reader, writer:
            reader.setblocking(False)
            writer.setblocking(False)
            if writer.send(b"\0") != 1:
                raise OSError(errno.EIO, "asyncio wakeup probe did not send its byte")
    except OSError as error:
        if error.errno not in {errno.EPERM, errno.EACCES}:
            raise
        pytest.exit(
            "ASYNCIO_WAKEUP_BLOCKED: socketpair wakeup permission denied "
            f"({errno.errorcode[error.errno]}). Cross-thread event-loop wakeups "
            "are blocked in this environment; TestClient/AnyIO portal tests "
            "can hang. This is a known restricted-sandbox limitation, not a "
            "Yeliztli or Python 3.13 defect (#1892, #2233). "
            "Run the backend suite outside the restricted sandbox.",
            returncode=2,
        )
