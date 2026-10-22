"""In-process exclusion between uploads and the full-data wipe (#2574).

The wipe (``DELETE /api/data/nuclear``) resets the registry and removes the
whole data directory. An upload that is still running holds its own registry
and parsed genotypes, so if the wipe ran underneath it the upload would go on
to recreate ``samples/sample_<id>.db`` -- genotypes on disk after the wipe
reported that everything was deleted.

A lease row in ``jobs`` cannot fence this: the wipe deletes ``reference.db``
itself, and an upload that has parsed its file but not yet allocated an id has
no row to find. Both run in the API process, so an in-process fence is exact.
Each side refuses rather than waits, matching how every other sample operation
answers "busy".
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from backend.services.sample_operation_lock import SampleOperationConflictError

_lock = threading.Lock()
_active_uploads = 0
_wiping = False


@contextmanager
def upload_hold() -> Iterator[None]:
    """Hold for an upload's whole run; refused while a wipe is in progress."""
    global _active_uploads
    with _lock:
        if _wiping:
            raise SampleOperationConflictError(
                "All data is being deleted; the upload was not started."
            )
        _active_uploads += 1
    try:
        yield
    finally:
        with _lock:
            _active_uploads -= 1


@contextmanager
def exclusive_wipe() -> Iterator[None]:
    """Hold for the wipe; refused while any upload is still running."""
    global _wiping
    with _lock:
        if _active_uploads:
            raise SampleOperationConflictError(
                "An upload is still being imported; retry once it completes."
            )
        if _wiping:
            raise SampleOperationConflictError("All data is already being deleted.")
        _wiping = True
    try:
        yield
    finally:
        with _lock:
            _wiping = False
