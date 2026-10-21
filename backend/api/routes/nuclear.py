"""Nuclear Delete API endpoint (P4-21).

Single operation that wipes ALL data — all per-sample SQLite databases,
reference.db, vep_bundle.db, gnomad_af.db, dbnsfp.db, cached downloads,
literature cache. Full reset to fresh install / setup wizard state.

Removes the entire contents of the configured data directory
(``~/.yeliztli/`` by default).

    DELETE /api/data/nuclear — Wipe all data and return to setup wizard
"""

from __future__ import annotations

import logging
import shutil

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.connection import get_registry, reset_registry
from backend.services.data_wipe_fence import exclusive_wipe
from backend.services.sample_operation_lock import SampleOperationConflictError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/data", tags=["data"])


class NuclearDeleteResponse(BaseModel):
    """Response after successful nuclear delete."""

    deleted: bool
    message: str


def _wipe_data_dir() -> None:
    """Remove everything inside the data directory and recreate it empty."""
    registry = get_registry()
    settings = registry.settings
    data_dir = settings.data_dir

    if not data_dir.exists():
        raise HTTPException(status_code=404, detail="Data directory does not exist.")

    try:
        # 1. Dispose all cached SQLite engines so file handles are released
        reset_registry()

        # 2. Remove everything inside data_dir (symlink-safe)
        for child in data_dir.iterdir():
            if child.is_symlink():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink(missing_ok=True)

        # 3. Recreate the empty data_dir structure expected by lifespan
        data_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Nuclear delete completed: all data wiped from %s", data_dir)
    except Exception:
        logger.exception("Nuclear delete failed")
        raise HTTPException(
            status_code=500,
            detail="Nuclear delete failed. Check server logs for details.",
        )


# TODO(P4-21a): Gate behind PIN/password auth when auth system lands (P4-21a).
@router.delete("/nuclear", response_model=NuclearDeleteResponse)
async def nuclear_delete() -> NuclearDeleteResponse:
    """Wipe ALL data and reset to fresh-install state.

    Deletes every file inside the configured ``data_dir`` — sample databases,
    reference databases, downloaded bundles, logs, caches, config, and the
    disclaimer marker. After completion the application redirects to the
    setup wizard on next page load.
    """
    # An upload running in the threadpool would recreate its sample database
    # after the wipe (#2574). Refuse rather than wait, as other busy-sample
    # operations do.
    try:
        with exclusive_wipe():
            _wipe_data_dir()
    except SampleOperationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return NuclearDeleteResponse(
        deleted=True,
        message="All data has been deleted. The application will return to the setup wizard.",
    )
