"""Individuals API endpoints (AncestryDNA Plan §9.2, §9.3, §10.6; Steps 47, 67, 68).

An ``individuals`` row groups one or more ``samples`` rows under a single
biological subject (e.g., a 23andMe export plus an AncestryDNA export from
the same person). Linking is enforced on the ``samples.individual_id`` FK
column added by migration 009; each sample belongs to at most one
individual at a time.

Routes
------
GET    /api/individuals                    — List with sample_count, vendors, last_activity
POST   /api/individuals                    — Create
GET    /api/individuals/{id}               — Detail: fields + linked samples + finding count
PATCH  /api/individuals/{id}               — Edit display_name / notes / biological_sex
DELETE /api/individuals/{id}               — Null out linked samples.individual_id, then delete
POST   /api/individuals/{id}/link-sample   — Body {sample_id}; 409 if linked elsewhere
POST   /api/individuals/{id}/unlink-sample — Body {sample_id}
POST   /api/individuals/{id}/merge/preview — Plan §10.6 dry-run summary + duration estimate
POST   /api/individuals/{id}/merge         — Plan §10.6 commit — materialise the merged sample

The DELETE handler explicitly nulls ``samples.individual_id`` before
deleting the parent row. This makes the SET-NULL behavior independent of
``PRAGMA foreign_keys`` (which is OFF by default on SQLite), so the
"orphaned samples survive the delete" contract from Plan §9.2 holds in
every environment.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.api.gating import gated_modules_to_hide
from backend.db.connection import get_registry
from backend.db.tables import findings as findings_table
from backend.db.tables import individuals, jobs, samples
from backend.services.sample_merge import (
    InvalidMergeRequestError,
    MergeStrategy,
    StaleSourceError,
    merge_samples,
    preview_merge,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/individuals", tags=["individuals"])


_BIOLOGICAL_SEX = Literal["XX", "XY"]
_MERGED_FILE_FORMAT = "merged_v1"


# ── Request / response models ────────────────────────────────────────


class IndividualCreate(BaseModel):
    display_name: str = Field(..., min_length=1)
    notes: str | None = None
    biological_sex: _BIOLOGICAL_SEX | None = None


class IndividualUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1)
    notes: str | None = None
    biological_sex: _BIOLOGICAL_SEX | None = None


class LinkSampleRequest(BaseModel):
    sample_id: int


class LinkedSample(BaseModel):
    id: int
    name: str
    file_format: str | None = None
    vendor: str | None = None
    is_merged: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class IndividualSummary(BaseModel):
    id: int
    display_name: str
    notes: str | None = None
    biological_sex: _BIOLOGICAL_SEX | None = None
    created_at: str | None = None
    updated_at: str | None = None
    sample_count: int
    vendors: list[str]
    last_activity: str | None = None


class IndividualDetail(BaseModel):
    id: int
    display_name: str
    notes: str | None = None
    biological_sex: _BIOLOGICAL_SEX | None = None
    created_at: str | None = None
    updated_at: str | None = None
    linked_samples: list[LinkedSample]
    aggregated_findings_count: int


class LinkConflictDetail(BaseModel):
    """Body shape for the 409 returned when a sample is already linked."""

    sample_id: int
    individual_id: int
    individual_display_name: str
    message: str


# Plan §10.3: the three merge strategies the wizard picks between. Mirrored
# from :class:`backend.services.sample_merge.MergeStrategy` as a string
# Literal so FastAPI's auto-validation rejects unknown values with 422
# before the request reaches the service.
_MERGE_STRATEGY = Literal["prefer_23andme", "prefer_ancestrydna", "flag_only"]


class MergePreviewRequest(BaseModel):
    """Body for ``POST /api/individuals/{id}/merge/preview`` (Plan §10.6)."""

    source_sample_ids: list[int] = Field(..., min_length=2, max_length=2)
    strategy: _MERGE_STRATEGY


class MergePreviewResponse(BaseModel):
    """Wizard-facing payload returned by the preview route (Plan §10.6).

    ``concordance_summary`` is the §10.4 (c) ``concordance_summary`` shape
    that :class:`~backend.services.sample_merge._ConcordanceSummary` writes
    into ``merge_provenance.concordance_summary``; ``est_duration_seconds``
    is the wizard's "this will take ~N seconds" hint on the confirm step.
    """

    concordance_summary: dict[str, int]
    est_duration_seconds: int


class MergeCommitRequest(BaseModel):
    """Body for ``POST /api/individuals/{id}/merge`` (Plan §10.6).

    Extends :class:`MergePreviewRequest` with ``display_name``, which the
    wizard collects on the confirm step and persists on the new
    ``samples`` row created by :func:`backend.services.sample_merge.merge_samples`.
    """

    source_sample_ids: list[int] = Field(..., min_length=2, max_length=2)
    strategy: _MERGE_STRATEGY
    display_name: str = Field(..., min_length=1)


class MergeCommitResponse(BaseModel):
    """Payload returned by the commit route (Plan §10.6).

    ``merged_sample_id`` is the new ``samples.id``; ``job_id`` mirrors the
    annotation job enqueued by the service (Plan §10.5 step 8). The frontend
    polls ``GET /api/annotation/status/{job_id}`` for SSE progress.
    """

    merged_sample_id: int
    job_id: str


# ── Helpers ──────────────────────────────────────────────────────────


def _vendor_from_file_format(file_format: str | None) -> str | None:
    """Extract the leading vendor token from ``file_format`` (e.g. ``"23andme"``)."""
    if not file_format:
        return None
    return file_format.split("_", 1)[0].lower()


def _is_merged_file_format(file_format: str | None) -> bool:
    return file_format == _MERGED_FILE_FORMAT


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _linked_sample_rows(conn: sa.Connection, individual_id: int) -> list[sa.Row]:
    return conn.execute(
        sa.select(samples)
        .where(samples.c.individual_id == individual_id)
        .order_by(samples.c.created_at.asc().nullslast(), samples.c.id.asc())
    ).fetchall()


def _aggregate_findings_count(linked: list[sa.Row]) -> int:
    """Count distinct high-confidence findings across the linked samples.

    "High confidence" = ``findings.evidence_level >= 3``. Findings with a
    non-NULL ``rsid`` are deduplicated by ``rsid`` (Plan §9.5: "union
    across linked samples, deduplicated by rsid"); findings without an
    rsid (e.g. haplogroup / pathway-level) count individually so per-
    sample categorical findings still surface in the aggregate.
    """
    registry = get_registry()
    settings = registry.settings

    seen_rsids: set[str] = set()
    rsid_null_count = 0

    for row in linked:
        sample_db_path = settings.data_dir / row.db_path
        if not sample_db_path.exists():
            continue
        try:
            sample_engine = registry.get_sample_engine(sample_db_path)
        except Exception:  # noqa: BLE001
            logger.warning(
                "individuals.aggregate_findings.engine_open_failed sample_id=%s",
                row.id,
            )
            continue
        try:
            # Withhold opt-in-gated modules whose gate is unacknowledged for THIS
            # sample (gate ack is per-sample), mirroring /api/analysis/findings and
            # the dedicated routes — otherwise a gated high-confidence finding
            # (APOE #222, sex-aneuploidy #299, Parkinson's #298) shifts this
            # aggregate count and re-opens the disclosure as a count delta (#388).
            hidden_modules = gated_modules_to_hide(sample_engine)
            stmt = sa.select(findings_table.c.rsid).where(findings_table.c.evidence_level >= 3)
            if hidden_modules:
                stmt = stmt.where(findings_table.c.module.not_in(hidden_modules))
            with sample_engine.connect() as sample_conn:
                result = sample_conn.execute(stmt).fetchall()
        except sa.exc.OperationalError:
            # findings/gate tables may not exist on a freshly-created sample DB
            # that has never finished annotation; that simply means zero
            # high-confidence findings have been written yet.
            continue
        for finding in result:
            rsid = finding.rsid
            if rsid:
                seen_rsids.add(rsid)
            else:
                rsid_null_count += 1

    return len(seen_rsids) + rsid_null_count


def _linked_sample_payload(row: sa.Row) -> LinkedSample:
    return LinkedSample(
        id=row.id,
        name=row.name,
        file_format=row.file_format,
        vendor=_vendor_from_file_format(row.file_format),
        is_merged=_is_merged_file_format(row.file_format),
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
    )


def _require_individual(conn: sa.Connection, individual_id: int) -> sa.Row:
    row = conn.execute(sa.select(individuals).where(individuals.c.id == individual_id)).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Individual {individual_id} not found.",
        )
    return row


def _require_sample(conn: sa.Connection, sample_id: int) -> sa.Row:
    row = conn.execute(sa.select(samples).where(samples.c.id == sample_id)).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Sample {sample_id} not found.",
        )
    return row


# ── List + Create ────────────────────────────────────────────────────


@router.get("")
def list_individuals() -> list[IndividualSummary]:
    """List individuals with summary (sample count, vendors, last activity)."""
    registry = get_registry()
    summaries: list[IndividualSummary] = []
    with registry.reference_engine.connect() as conn:
        ind_rows = conn.execute(
            sa.select(individuals).order_by(individuals.c.created_at.desc())
        ).fetchall()
        for ind in ind_rows:
            linked = _linked_sample_rows(conn, ind.id)
            vendors_seen: list[str] = []
            for sample_row in linked:
                vendor = _vendor_from_file_format(sample_row.file_format)
                if vendor and vendor not in vendors_seen:
                    vendors_seen.append(vendor)
            last_activity_dt = max(
                (
                    sample_row.updated_at or sample_row.created_at
                    for sample_row in linked
                    if (sample_row.updated_at or sample_row.created_at) is not None
                ),
                default=None,
            )
            summaries.append(
                IndividualSummary(
                    id=ind.id,
                    display_name=ind.display_name,
                    notes=ind.notes if ind.notes else None,
                    biological_sex=ind.biological_sex,
                    created_at=_iso(ind.created_at),
                    updated_at=_iso(ind.updated_at),
                    sample_count=len(linked),
                    vendors=vendors_seen,
                    last_activity=_iso(last_activity_dt),
                )
            )
    return summaries


@router.post("", status_code=201)
def create_individual(body: IndividualCreate) -> IndividualDetail:
    """Create a new individual."""
    registry = get_registry()
    now = datetime.now(UTC)
    insert_values = {
        "display_name": body.display_name,
        "notes": body.notes or "",
        "biological_sex": body.biological_sex,
        "updated_at": now,
    }
    with registry.reference_engine.begin() as conn:
        result = conn.execute(individuals.insert().values(**insert_values))
        new_id = result.inserted_primary_key[0]
        row = conn.execute(sa.select(individuals).where(individuals.c.id == new_id)).fetchone()
        linked = _linked_sample_rows(conn, new_id)
    return IndividualDetail(
        id=row.id,
        display_name=row.display_name,
        notes=row.notes if row.notes else None,
        biological_sex=row.biological_sex,
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
        linked_samples=[_linked_sample_payload(s) for s in linked],
        aggregated_findings_count=_aggregate_findings_count(linked),
    )


# ── Detail / Patch / Delete ──────────────────────────────────────────


@router.get("/{individual_id}")
def get_individual(individual_id: int) -> IndividualDetail:
    """Return full detail for a single individual."""
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = _require_individual(conn, individual_id)
        linked = _linked_sample_rows(conn, individual_id)
    return IndividualDetail(
        id=row.id,
        display_name=row.display_name,
        notes=row.notes if row.notes else None,
        biological_sex=row.biological_sex,
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
        linked_samples=[_linked_sample_payload(s) for s in linked],
        aggregated_findings_count=_aggregate_findings_count(linked),
    )


@router.patch("/{individual_id}")
def update_individual(individual_id: int, body: IndividualUpdate) -> IndividualDetail:
    """Edit display_name / notes / biological_sex."""
    # Use ``model_fields_set`` so a field explicitly sent as ``null`` clears
    # the nullable column, while an omitted field is left untouched. (A plain
    # ``is not None`` check can't distinguish "omitted" from "cleared".)
    fields_set = body.model_fields_set
    update_values: dict = {}
    if "display_name" in fields_set:
        if body.display_name is None:
            raise HTTPException(
                status_code=422,
                detail="display_name cannot be null.",
            )
        update_values["display_name"] = body.display_name
    if "notes" in fields_set:
        update_values["notes"] = body.notes or ""
    if "biological_sex" in fields_set:
        update_values["biological_sex"] = body.biological_sex

    if not update_values:
        # No fields supplied — surface 422 rather than silently no-op so
        # the client sees the bug.
        raise HTTPException(
            status_code=422,
            detail="At least one field must be provided.",
        )

    update_values["updated_at"] = datetime.now(UTC)

    registry = get_registry()
    with registry.reference_engine.begin() as conn:
        _require_individual(conn, individual_id)
        conn.execute(
            individuals.update().where(individuals.c.id == individual_id).values(**update_values)
        )
        row = conn.execute(
            sa.select(individuals).where(individuals.c.id == individual_id)
        ).fetchone()
        linked = _linked_sample_rows(conn, individual_id)

    return IndividualDetail(
        id=row.id,
        display_name=row.display_name,
        notes=row.notes if row.notes else None,
        biological_sex=row.biological_sex,
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
        linked_samples=[_linked_sample_payload(s) for s in linked],
        aggregated_findings_count=_aggregate_findings_count(linked),
    )


@router.delete("/{individual_id}", status_code=204)
def delete_individual(individual_id: int) -> None:
    """Unassign linked samples (set ``individual_id = NULL``) then delete.

    No sample row is deleted; no per-sample DB file is touched.
    """
    registry = get_registry()
    now = datetime.now(UTC)
    with registry.reference_engine.begin() as conn:
        _require_individual(conn, individual_id)
        conn.execute(
            samples.update()
            .where(samples.c.individual_id == individual_id)
            .values(individual_id=None, updated_at=now)
        )
        conn.execute(individuals.delete().where(individuals.c.id == individual_id))
    logger.info("individuals.delete id=%s", individual_id)


# ── Link / Unlink ────────────────────────────────────────────────────


@router.post("/{individual_id}/link-sample")
def link_sample(individual_id: int, body: LinkSampleRequest) -> IndividualDetail:
    """Link a sample to this individual.

    Returns 409 with the existing link in the body when the sample is
    already linked to a different individual. Re-linking to the same
    individual is a no-op (returns the current detail).
    """
    registry = get_registry()
    now = datetime.now(UTC)
    with registry.reference_engine.begin() as conn:
        _require_individual(conn, individual_id)
        sample_row = _require_sample(conn, body.sample_id)

        if sample_row.individual_id is not None and sample_row.individual_id != individual_id:
            existing = conn.execute(
                sa.select(individuals).where(individuals.c.id == sample_row.individual_id)
            ).fetchone()
            existing_display = existing.display_name if existing else ""
            raise HTTPException(
                status_code=409,
                detail=LinkConflictDetail(
                    sample_id=body.sample_id,
                    individual_id=sample_row.individual_id,
                    individual_display_name=existing_display,
                    message=(
                        f"Sample {body.sample_id} is already linked to "
                        f"individual {sample_row.individual_id}."
                    ),
                ).model_dump(),
            )

        if sample_row.individual_id is None:
            conn.execute(
                samples.update()
                .where(samples.c.id == body.sample_id)
                .values(individual_id=individual_id, updated_at=now)
            )
            conn.execute(
                individuals.update()
                .where(individuals.c.id == individual_id)
                .values(updated_at=now)
            )

        row = conn.execute(
            sa.select(individuals).where(individuals.c.id == individual_id)
        ).fetchone()
        linked = _linked_sample_rows(conn, individual_id)

    return IndividualDetail(
        id=row.id,
        display_name=row.display_name,
        notes=row.notes if row.notes else None,
        biological_sex=row.biological_sex,
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
        linked_samples=[_linked_sample_payload(s) for s in linked],
        aggregated_findings_count=_aggregate_findings_count(linked),
    )


@router.post("/{individual_id}/unlink-sample")
def unlink_sample(individual_id: int, body: LinkSampleRequest) -> IndividualDetail:
    """Unlink a sample from this individual.

    422 when the sample exists but is linked to a different individual
    (clients must call ``link-sample`` on the correct individual or
    confirm intent explicitly). 404 when the sample doesn't exist.
    Unlinking an already-unlinked sample is a no-op.
    """
    registry = get_registry()
    now = datetime.now(UTC)
    with registry.reference_engine.begin() as conn:
        _require_individual(conn, individual_id)
        sample_row = _require_sample(conn, body.sample_id)

        if sample_row.individual_id is not None and sample_row.individual_id != individual_id:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Sample {body.sample_id} is linked to a different "
                    f"individual ({sample_row.individual_id})."
                ),
            )

        if sample_row.individual_id == individual_id:
            conn.execute(
                samples.update()
                .where(samples.c.id == body.sample_id)
                .values(individual_id=None, updated_at=now)
            )
            conn.execute(
                individuals.update()
                .where(individuals.c.id == individual_id)
                .values(updated_at=now)
            )

        row = conn.execute(
            sa.select(individuals).where(individuals.c.id == individual_id)
        ).fetchone()
        linked = _linked_sample_rows(conn, individual_id)

    return IndividualDetail(
        id=row.id,
        display_name=row.display_name,
        notes=row.notes if row.notes else None,
        biological_sex=row.biological_sex,
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
        linked_samples=[_linked_sample_payload(s) for s in linked],
        aggregated_findings_count=_aggregate_findings_count(linked),
    )


# ── Merge preview (Plan §10.6; Step 67 / MRG-03) ─────────────────────


@router.post("/{individual_id}/merge/preview")
def preview_merge_endpoint(individual_id: int, body: MergePreviewRequest) -> MergePreviewResponse:
    """Dry-run the §10.2 / §10.3 merge semantics and return the wizard payload.

    Validation surface is identical to ``POST /api/individuals/{id}/merge``
    (Step 68) — same membership / status / staleness checks — but nothing
    is written: no new ``samples`` row, no per-sample DB, no
    ``merge_provenance``. Fed by :func:`backend.services.sample_merge.preview_merge`,
    which calls the shared ``_compute_merge_plan`` pipeline.

    Error surface:

    * 404 — individual does not exist.
    * 422 — :class:`InvalidMergeRequestError` (shape, membership, or
      annotation-status failure).
    * 423 — :class:`StaleSourceError`; body carries the structured payload
      Plan §7.5 declares for ``require_fresh_sample``.
    """
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        _require_individual(conn, individual_id)

    try:
        result = preview_merge(
            registry,
            source_sample_ids=body.source_sample_ids,
            individual_id=individual_id,
            strategy=MergeStrategy(body.strategy),
        )
    except InvalidMergeRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StaleSourceError as exc:
        raise HTTPException(status_code=423, detail=exc.detail) from exc

    return MergePreviewResponse(**result)


# ── Merge commit (Plan §10.6; Step 68 / MRG-04) ──────────────────────


def _latest_annotation_job_id(conn: sa.Connection, sample_id: int) -> str:
    """Return the most recently enqueued annotation job_id for ``sample_id``.

    Plan §10.6 surfaces ``{merged_sample_id, job_id}``; the job is enqueued
    inside :func:`backend.services.sample_merge.merge_samples` (Plan §10.5
    step 8) which does not bubble the id out. Reading it back from the
    ``jobs`` table preserves the service's signature and tolerates the
    "enqueue failed → no job row" branch by returning ``''`` so the wizard
    can render a fallback rather than crash.
    """
    row = conn.execute(
        sa.select(jobs.c.job_id)
        .where(jobs.c.sample_id == sample_id)
        .where(jobs.c.job_type == "annotation")
        .order_by(jobs.c.created_at.desc())
        .limit(1)
    ).fetchone()
    return row.job_id if row else ""


@router.post("/{individual_id}/merge", status_code=201)
def commit_merge_endpoint(individual_id: int, body: MergeCommitRequest) -> MergeCommitResponse:
    """Materialise the merged sample DB and enqueue annotation (Plan §10.6).

    Body: ``{source_sample_ids: [a, b], strategy, display_name}``.
    Response: ``{merged_sample_id, job_id}``.

    Shares the §10.5 step-1–4 validation pipeline with
    :func:`preview_merge_endpoint`, then performs steps 5–8 (write
    ``samples`` row, materialise the per-sample DB with the merged-sample
    layout, write the single ``merge_provenance`` row, enqueue the
    annotation job).

    Error surface:

    * 404 — individual does not exist.
    * 422 — :class:`InvalidMergeRequestError` (shape / membership /
      annotation-status failure, or empty ``display_name``).
    * 423 — :class:`StaleSourceError`; body carries the structured payload
      Plan §7.5 declares for ``require_fresh_sample``.
    """
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        _require_individual(conn, individual_id)

    try:
        merged_sample_id = merge_samples(
            registry,
            source_sample_ids=body.source_sample_ids,
            individual_id=individual_id,
            strategy=MergeStrategy(body.strategy),
            display_name=body.display_name,
        )
    except InvalidMergeRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StaleSourceError as exc:
        raise HTTPException(status_code=423, detail=exc.detail) from exc

    with registry.reference_engine.connect() as conn:
        job_id = _latest_annotation_job_id(conn, merged_sample_id)

    return MergeCommitResponse(merged_sample_id=merged_sample_id, job_id=job_id)
