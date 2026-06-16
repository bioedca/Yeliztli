"""Update manager API routes (P4-16, P4-21d).

Endpoints for checking database updates, triggering updates,
viewing update history, managing re-annotation prompts,
and checking for app updates via GitHub Releases API.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.db.build_guard import is_cross_process_build_claimed
from backend.db.connection import get_registry
from backend.db.database_registry import DATABASES, get_build_fn, get_database_status
from backend.db.update_manager import (
    AUTO_UPDATE_DEFAULTS,
    check_all_updates,
    dismiss_prompt,
    format_version_display,
    get_active_prompts,
    get_all_version_stamps,
    get_auto_update,
    get_update_history,
    set_auto_update,
    should_download_now,
)

router = APIRouter(prefix="/updates", tags=["updates"])


# ── Response models ──────────────────────────────────────────────────


class UpdateAvailable(BaseModel):
    db_name: str
    latest_version: str
    download_size_bytes: int
    release_date: str | None = None


class UpdateCheckResponse(BaseModel):
    available: list[UpdateAvailable]
    up_to_date: list[str]
    errors: list[str]
    checked_at: str


class UpdateHistoryEntry(BaseModel):
    id: int
    db_name: str
    previous_version: str | None
    new_version: str
    updated_at: str | None
    variants_added: int | None
    variants_reclassified: int | None
    download_size_bytes: int | None
    duration_seconds: int | None


class WatchedReclassification(BaseModel):
    rsid: str
    gene_symbol: str | None = None
    old_significance: str
    new_significance: str


class ReannotationPrompt(BaseModel):
    id: int
    sample_id: int
    db_name: str
    db_version: str
    candidate_count: int
    watched_count: int = 0
    watched_details: list[WatchedReclassification] = []
    created_at: str | None


# Finding-level change diff (SW-A4b / #8) — the deferred second half of SW-A4.


class FindingFieldChange(BaseModel):
    field: str
    before: str | None = None
    after: str | None = None


class ReleaseDelta(BaseModel):
    db_name: str
    before: str | None = None
    after: str | None = None


class ChangedFinding(BaseModel):
    module: str
    category: str | None = None
    gene_symbol: str | None = None
    rsid: str | None = None
    drug: str | None = None
    diplotype: str | None = None
    finding_text: str
    changes: list[FindingFieldChange] = []


class DiffFinding(BaseModel):
    module: str
    category: str | None = None
    gene_symbol: str | None = None
    rsid: str | None = None
    drug: str | None = None
    diplotype: str | None = None
    finding_text: str
    clinvar_significance: str | None = None
    evidence_level: int | None = None
    metabolizer_status: str | None = None
    pathway_level: str | None = None


class FindingChangesResponse(BaseModel):
    """What changed at the finding level since the prior analysis.

    ``available`` is False when there is no undismissed diff with changes — the
    common case (first analysis, nothing changed, or the user dismissed it).
    """

    available: bool
    generated_at: str | None = None
    release_deltas: list[ReleaseDelta] = []
    changed: list[ChangedFinding] = []
    added: list[DiffFinding] = []
    removed: list[DiffFinding] = []
    counts: dict[str, int] = {}


def _filter_hidden_modules_from_finding_diff(
    diff: dict[str, Any], hidden_modules: list[str]
) -> dict[str, Any]:
    """Drop unacknowledged gated modules from the displayed finding diff.

    The stored diff remains complete for post-acknowledgment display, but the
    aggregate update banner is itself a finding surface. Its visible counts must
    be recomputed after filtering so pre-acknowledgment callers cannot infer that
    a gated finding exists from a nonzero bucket count.
    """
    if not hidden_modules:
        return diff

    hidden = set(hidden_modules)

    def visible(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [entry for entry in entries if entry.get("module") not in hidden]

    changed = visible(diff.get("changed", []))
    added = visible(diff.get("added", []))
    removed = visible(diff.get("removed", []))
    filtered = dict(diff)
    filtered["changed"] = changed
    filtered["added"] = added
    filtered["removed"] = removed
    filtered["counts"] = {
        "changed": len(changed),
        "added": len(added),
        "removed": len(removed),
    }
    return filtered


class DatabaseStatus(BaseModel):
    db_name: str
    display_name: str
    current_version: str | None
    version_display: str | None
    downloaded_at: str | None
    file_size_bytes: int | None
    auto_update: bool
    update_available: bool


class TriggerUpdateRequest(BaseModel):
    db_name: str


class TriggerUpdateResponse(BaseModel):
    job_id: str
    db_name: str
    message: str


class AutoUpdateRequest(BaseModel):
    db_name: str
    enabled: bool


class AutoUpdateResponse(BaseModel):
    db_name: str
    enabled: bool


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/check", response_model=UpdateCheckResponse)
async def check_for_updates() -> UpdateCheckResponse:
    """Check all databases for available updates."""
    registry = get_registry()
    result = check_all_updates(registry.reference_engine, settings=registry.settings)

    return UpdateCheckResponse(
        available=[
            UpdateAvailable(
                db_name=v.db_name,
                latest_version=v.latest_version,
                download_size_bytes=v.download_size_bytes,
                release_date=v.release_date,
            )
            for v in result.available
        ],
        up_to_date=result.up_to_date,
        errors=result.errors,
        checked_at=result.checked_at.isoformat(),
    )


@router.post("/trigger", response_model=TriggerUpdateResponse, status_code=202)
async def trigger_update(req: TriggerUpdateRequest) -> TriggerUpdateResponse:
    """Trigger an update for a specific database.

    Enqueues the update as a background Huey task and returns
    the job_id for progress tracking via SSE.
    """
    from backend.db.update_manager import _BUNDLE_DBS
    from backend.tasks.huey_tasks import (
        create_database_update_job,
        run_database_update_task,
    )

    # Any database with a build function, or a manifest-driven bundle
    # (vep_bundle / lai_bundle / ancestry_pca), can be updated. The bundle set
    # must mirror the scheduler's _dispatch_auto_update and huey's
    # run_database_update_task so the UI never offers an update the backend
    # rejects (previously lai_bundle/ancestry_pca 400'd here).
    db_info = DATABASES.get(req.db_name)
    build_fn = get_build_fn(req.db_name) if db_info else None
    is_bundle = req.db_name in _BUNDLE_DBS
    if build_fn is None and not is_bundle:
        supported = sorted(
            {k for k in DATABASES if get_build_fn(k) is not None} | set(_BUNDLE_DBS)
        )
        raise HTTPException(
            status_code=400,
            detail=f"Update not supported for '{req.db_name}'. Supported: {supported}",
        )

    # Check bandwidth window — look up actual expected download size
    registry = get_registry()
    settings = registry.settings
    estimated_size = db_info.expected_size_bytes if db_info else 0
    if not should_download_now(estimated_size, settings.update_download_window):
        raise HTTPException(
            status_code=409,
            detail=f"Outside bandwidth window ({settings.update_download_window}).",
        )

    # Don't queue an update that would immediately no-op: another process (the
    # wizard or a concurrent update) is already building this DB's file.
    if is_cross_process_build_claimed(req.db_name, settings.data_dir):
        raise HTTPException(
            status_code=409,
            detail=f"A build/update for '{req.db_name}' is already in progress.",
        )

    job_id = create_database_update_job(req.db_name)
    run_database_update_task(job_id, req.db_name)

    return TriggerUpdateResponse(
        job_id=job_id,
        db_name=req.db_name,
        message=f"Update queued for {req.db_name}",
    )


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress_pct: float
    message: str
    error: str | None = None


@router.get("/job/{job_id}", response_model=JobStatusResponse)
async def get_update_job_status(job_id: str) -> JobStatusResponse:
    """Poll the status of a database update job."""
    from backend.api.sse import get_job_progress

    registry = get_registry()
    status = get_job_progress(registry.reference_engine, job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(
        job_id=status.job_id,
        status=status.status,
        progress_pct=status.progress_pct,
        message=status.message,
        error=status.error,
    )


@router.get("/history", response_model=list[UpdateHistoryEntry])
async def list_update_history(
    db_name: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[UpdateHistoryEntry]:
    """Get update history, optionally filtered by database name."""
    registry = get_registry()
    rows = get_update_history(registry.reference_engine, db_name=db_name, limit=limit)
    return [UpdateHistoryEntry(**r) for r in rows]


@router.get("/status", response_model=list[DatabaseStatus])
async def get_database_statuses() -> list[DatabaseStatus]:
    """Get current version and display info for all tracked databases.

    Note: ``update_available`` is always False here. Call
    ``GET /updates/check`` for actual update availability (network call).
    """
    registry = get_registry()
    engine = registry.reference_engine
    settings = registry.settings

    # Fetch all version stamps in one query
    stamps = {s["db_name"]: s for s in get_all_version_stamps(engine)}

    # Check which databases have updates available (cached, no network call)
    # We only mark update_available based on whether we have a version at all;
    # actual update checks are done via GET /updates/check
    result = []
    for db_name in AUTO_UPDATE_DEFAULTS:
        auto_update = get_auto_update(engine, db_name)
        stamp = stamps.get(db_name)
        version = stamp["version"] if stamp else None
        downloaded_at = stamp["downloaded_at"] if stamp else None
        file_size_bytes = stamp["file_size_bytes"] if stamp else None
        db_info = DATABASES.get(db_name)
        display_name = db_info.display_name if db_info else db_name

        # If no version stamp or file size, check on-disk status as fallback.
        # Databases may exist on disk without a database_versions entry
        # (e.g. after a build that didn't record its version).
        on_disk = None
        if (version is None or file_size_bytes is None) and db_info is not None:
            on_disk = get_database_status(db_info, settings)

        if version is None and on_disk is not None and on_disk["downloaded"]:
            version = "installed"
            if on_disk.get("file_size_bytes"):
                file_size_bytes = on_disk["file_size_bytes"]

        if file_size_bytes is None and on_disk is not None and on_disk.get("file_size_bytes"):
            file_size_bytes = on_disk["file_size_bytes"]

        result.append(
            DatabaseStatus(
                db_name=db_name,
                display_name=display_name,
                current_version=version,
                version_display=format_version_display(version, db_name),
                downloaded_at=downloaded_at,
                file_size_bytes=file_size_bytes,
                auto_update=auto_update,
                update_available=False,  # Set by GET /updates/check
            )
        )
    return result


@router.get("/prompts", response_model=list[ReannotationPrompt])
async def list_reannotation_prompts(
    sample_id: int | None = None,
) -> list[ReannotationPrompt]:
    """Get active (undismissed) re-annotation prompts."""
    registry = get_registry()
    rows = get_active_prompts(registry.reference_engine, sample_id=sample_id)
    return [ReannotationPrompt(**r) for r in rows]


class AppUpdateResponse(BaseModel):
    update_available: bool
    current_version: str
    latest_version: str | None = None
    release_url: str | None = None
    release_notes: str | None = None
    error: str | None = None


@router.get("/app-update", response_model=AppUpdateResponse)
async def check_app_update() -> AppUpdateResponse:
    """Check GitHub Releases API for a newer Yeliztli version."""
    from backend.utils.update_checker import check_app_update as _check

    info = await _check()
    return AppUpdateResponse(
        update_available=info.update_available,
        current_version=info.current_version,
        latest_version=info.latest_version,
        release_url=info.release_url,
        release_notes=info.release_notes,
        error=info.error,
    )


@router.post("/prompts/{prompt_id}/dismiss")
async def dismiss_reannotation_prompt(prompt_id: int) -> dict:
    """Dismiss a re-annotation prompt."""
    registry = get_registry()
    ok = dismiss_prompt(registry.reference_engine, prompt_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return {"status": "dismissed", "prompt_id": prompt_id}


@router.get("/finding-changes", response_model=FindingChangesResponse)
async def get_finding_changes(
    sample_id: int = Query(..., description="Sample ID"),
) -> FindingChangesResponse:
    """Return the finding-level change diff for a sample's latest re-annotation.

    Disclosure only (SW-A4b): reports which findings were added / removed /
    meaning-shifted since the prior analysis and the source-release delta that
    explains it. Responds ``available=False`` when there is no undismissed diff
    with changes.
    """
    from backend.analysis.finding_diff import has_changes, read_finding_diff
    from backend.api.gating import gated_modules_to_hide
    from backend.api.routes.risk_common import resolve_sample_engine

    engine = resolve_sample_engine(sample_id)
    diff = read_finding_diff(engine)
    if diff is None or diff.get("dismissed"):
        return FindingChangesResponse(available=False)

    diff = _filter_hidden_modules_from_finding_diff(diff, gated_modules_to_hide(engine))
    if not has_changes(diff):
        return FindingChangesResponse(available=False)

    return FindingChangesResponse(
        available=True,
        generated_at=diff.get("generated_at"),
        release_deltas=[ReleaseDelta(**d) for d in diff.get("release_deltas", [])],
        changed=[ChangedFinding(**c) for c in diff.get("changed", [])],
        added=[DiffFinding(**f) for f in diff.get("added", [])],
        removed=[DiffFinding(**f) for f in diff.get("removed", [])],
        counts=diff.get("counts", {}),
    )


@router.post("/finding-changes/dismiss")
async def dismiss_finding_changes(
    sample_id: int = Query(..., description="Sample ID"),
) -> dict:
    """Dismiss the stored finding-level change diff for a sample."""
    from backend.analysis.finding_diff import dismiss_finding_diff
    from backend.api.routes.risk_common import resolve_sample_engine

    engine = resolve_sample_engine(sample_id)
    ok = dismiss_finding_diff(engine)
    if not ok:
        raise HTTPException(status_code=404, detail="No finding-change diff to dismiss.")
    return {"status": "dismissed", "sample_id": sample_id}


@router.post("/auto-update", response_model=AutoUpdateResponse)
async def toggle_auto_update(req: AutoUpdateRequest) -> AutoUpdateResponse:
    """Persist the per-database auto-update toggle.

    The set of valid ``db_name`` values is the union of the database
    registry and ``AUTO_UPDATE_DEFAULTS``. Unknown databases yield 404.
    """
    known = set(DATABASES) | set(AUTO_UPDATE_DEFAULTS)
    if req.db_name not in known:
        raise HTTPException(status_code=404, detail=f"Unknown database: {req.db_name}")

    registry = get_registry()
    set_auto_update(registry.reference_engine, req.db_name, req.enabled)
    return AutoUpdateResponse(db_name=req.db_name, enabled=req.enabled)
