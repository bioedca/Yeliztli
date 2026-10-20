"""Background downloads must not outlive the test that started them (#2534, #2543).

``POST /api/databases/download`` hands each download to the module-level
executor in ``backend.api.routes.databases`` and returns at once, and the app
lifespan drops that executor with ``shutdown(wait=False)``. A test that does not
wait leaves the download running into later tests. There it retries with
backoff, logs through the registry singleton into whatever data home is
current, and trips whichever later test checks for threads.

``tests/backend/conftest.py`` fails any test that leaves a thread inside
``_run_download`` (:func:`threads_running_downloads`). A test that starts
downloads calls :func:`wait_for_downloads` while its own patches are still
active.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

_RUN_DOWNLOAD_FILE = str(Path("routes") / "databases.py")


def threads_running_downloads() -> list[str]:
    """Names of the live threads currently inside ``databases._run_download``."""
    frames = sys._current_frames()
    names = []
    for thread in threading.enumerate():
        frame = frames.get(thread.ident) if thread.ident is not None else None
        while frame is not None:
            code = frame.f_code
            if code.co_name == "_run_download" and code.co_filename.endswith(_RUN_DOWNLOAD_FILE):
                names.append(thread.name)
                break
            frame = frame.f_back
    return names


def wait_for_downloads() -> None:
    """Let every download on the module-level executor finish, then drop it."""
    from backend.api.routes import databases

    executor = databases._download_executor
    if executor is not None:
        executor.shutdown(wait=True)
        databases._download_executor = None
