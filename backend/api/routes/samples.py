"""Sample management API endpoints (P1-13, P4-21f).

- ``GET    /api/samples`` — list all samples.
- ``GET    /api/samples/{sample_id}`` — single sample + full metadata.
- ``GET    /api/samples/{sample_id}/merged-children`` — merged samples
  referencing this row (Step 66 / Plan §10.8).
- ``GET    /api/samples/{sample_id}/merge-provenance`` — merge_provenance row
  for a merged sample (Step 68 / Plan §10.6).
- ``GET    /api/samples/{sample_id}/concordance-report`` — paginated discordant
  loci with gene context (Step 68 / Plan §10.6).
- ``GET    /api/samples/{merged_id}/watched-variants/migrate-from-sources`` —
  post-merge re-watch candidates (Step 72 / Plan §10.6, §10.7).
- ``PATCH  /api/samples/{sample_id}`` — update sample metadata.
- ``DELETE /api/samples/{sample_id}`` — delete + cascade to merged children
  (Step 66 / Plan §10.8).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator

from backend.api.dependencies import require_fresh_merged_sample, require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import (
    annotated_variants,
    merge_provenance,
    raw_variants,
    sample_metadata_table,
    samples,
    watched_variants,
)
from backend.services.sample_delete import (
    delete_sample_with_cascade,
    list_merged_children,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/samples", tags=["samples"])


class SampleResponse(BaseModel):
    id: int
    name: str
    db_path: str
    file_format: str | None = None
    file_hash: str | None = None
    notes: str | None = None
    date_collected: str | None = None
    source: str | None = None
    extra: dict | None = None
    created_at: str | None = None
    updated_at: str | None = None


class SampleUpdate(BaseModel):
    name: str | None = None
    notes: str | None = None
    date_collected: str | None = None
    source: str | None = None
    extra: dict | None = None

    @field_validator("extra", mode="before")
    @classmethod
    def validate_extra(cls, v: object) -> object:
        if v is None:
            return v
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError("extra must be valid JSON") from exc
            if not isinstance(parsed, dict):
                raise ValueError("extra must be a JSON object")
            return parsed
        if not isinstance(v, dict):
            raise ValueError("extra must be a JSON object")
        return v


def _row_to_response(row: sa.Row) -> SampleResponse:
    """Convert a SQLAlchemy Row from reference.db to a SampleResponse."""
    return SampleResponse(
        id=row.id,
        name=row.name,
        db_path=row.db_path,
        file_format=row.file_format,
        file_hash=row.file_hash,
        created_at=str(row.created_at) if row.created_at else None,
        updated_at=str(row.updated_at) if row.updated_at else None,
    )


def _enrich_with_sample_metadata(response: SampleResponse, registry: object) -> SampleResponse:
    """Read per-sample DB metadata and merge into the response."""
    settings = registry.settings  # type: ignore[attr-defined]
    sample_db_path = settings.data_dir / response.db_path
    if not sample_db_path.exists():
        return response

    sample_engine = registry.get_sample_engine(sample_db_path)  # type: ignore[attr-defined]
    with sample_engine.connect() as conn:
        meta_row = conn.execute(
            sa.select(sample_metadata_table).where(sample_metadata_table.c.id == 1)
        ).fetchone()

    if meta_row is None:
        return response

    # Parse extra JSON
    extra_raw = meta_row.extra
    extra: dict = {}
    if extra_raw:
        try:
            extra = json.loads(extra_raw) if isinstance(extra_raw, str) else extra_raw
        except (json.JSONDecodeError, TypeError):
            extra = {}

    return response.model_copy(
        update={
            "notes": meta_row.notes if meta_row.notes else None,
            "date_collected": str(meta_row.date_collected) if meta_row.date_collected else None,
            "source": meta_row.source if meta_row.source else None,
            "extra": extra,
        }
    )


@router.get("")
async def list_samples() -> list[SampleResponse]:
    """List all registered samples."""
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        rows = conn.execute(sa.select(samples).order_by(samples.c.created_at.desc())).fetchall()
    return [_row_to_response(row) for row in rows]


@router.get("/{sample_id}")
async def get_sample(sample_id: int) -> SampleResponse:
    """Get a single sample by ID with full metadata from sample DB."""
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(sa.select(samples).where(samples.c.id == sample_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Sample {sample_id} not found.")
    response = _row_to_response(row)
    return _enrich_with_sample_metadata(response, registry)


@router.patch("/{sample_id}")
async def update_sample(sample_id: int, body: SampleUpdate) -> SampleResponse:
    """Update sample metadata (rename, notes, date, source, extra JSON)."""
    registry = get_registry()
    settings = registry.settings

    # Build update values from non-None fields
    update_values: dict = {}
    if body.name is not None:
        update_values["name"] = body.name

    now = datetime.now(UTC)
    update_values["updated_at"] = now

    with registry.reference_engine.begin() as conn:
        # Check sample exists
        row = conn.execute(sa.select(samples).where(samples.c.id == sample_id)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Sample {sample_id} not found.")

        # Update the sample registry
        conn.execute(samples.update().where(samples.c.id == sample_id).values(**update_values))

    # Also update per-sample metadata table if applicable
    sample_db_path = settings.data_dir / row.db_path
    if sample_db_path.exists():
        sample_engine = registry.get_sample_engine(sample_db_path)
        meta_updates: dict = {}
        if body.name is not None:
            meta_updates["name"] = body.name
        if body.notes is not None:
            meta_updates["notes"] = body.notes
        if body.date_collected is not None:
            try:
                meta_updates["date_collected"] = date.fromisoformat(body.date_collected)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid date format: {body.date_collected}. Expected YYYY-MM-DD.",
                ) from exc
        if body.source is not None:
            meta_updates["source"] = body.source
        if body.extra is not None:
            meta_updates["extra"] = json.dumps(body.extra)
        if meta_updates:
            meta_updates["updated_at"] = now
            with sample_engine.begin() as conn:
                conn.execute(
                    sample_metadata_table.update()
                    .where(sample_metadata_table.c.id == 1)
                    .values(**meta_updates)
                )

    # Return updated record with full metadata
    with registry.reference_engine.connect() as conn:
        updated_row = conn.execute(sa.select(samples).where(samples.c.id == sample_id)).fetchone()
    if updated_row is None:
        raise HTTPException(status_code=404, detail=f"Sample {sample_id} not found.")
    response = _row_to_response(updated_row)
    return _enrich_with_sample_metadata(response, registry)


class MergedChildResponse(BaseModel):
    id: int
    name: str


@router.get("/{sample_id}/merged-children")
async def list_sample_merged_children(sample_id: int) -> list[MergedChildResponse]:
    """List merged samples that reference this sample as a source.

    Frontend uses this to surface the cascade impact on the per-row delete
    confirmation (AncestryDNA Plan §10.8; Step 66 / MRG-02a). Returns ``[]``
    when the sample has never been merged.
    """
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(sa.select(samples.c.id).where(samples.c.id == sample_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Sample {sample_id} not found.")
    children = list_merged_children(registry, sample_id)
    return [MergedChildResponse(id=c.id, name=c.name) for c in children]


class MergeProvenanceResponse(BaseModel):
    """``merge_provenance`` row as returned by ``GET .../merge-provenance``.

    Plan §10.4 (c) shape — ``source_sample_ids`` / ``source_file_hashes`` /
    ``concordance_summary`` are decoded from their on-disk JSON strings so
    the wizard can consume the response directly.
    """

    merged_at: str
    strategy: str
    source_sample_ids: list[int]
    source_file_hashes: list[str]
    concordance_summary: dict[str, int]


class DiscordantLocus(BaseModel):
    """One row in the concordance-report's paginated ``discordant_loci`` array."""

    rsid: str
    chrom: str
    pos: int
    genotype: str
    discordant_alt_genotype: str
    alt_rsid: str
    gene_symbol: str | None = None
    consequence: str | None = None
    clinvar_significance: str | None = None


class ConcordanceReportResponse(BaseModel):
    """Paginated concordance-report payload (Plan §10.6)."""

    concordance_summary: dict[str, int]
    total_discordant: int
    limit: int
    offset: int
    discordant_loci: list[DiscordantLocus]


_CONCORDANCE_REPORT_DEFAULT_LIMIT = 50
_CONCORDANCE_REPORT_MAX_LIMIT = 500


def _read_merge_provenance(registry: object, sample_id: int) -> sa.Row | None:
    """Open the per-sample DB read-only and fetch the single ``merge_provenance`` row.

    Returns ``None`` when the sample exists but has no provenance row (i.e.
    not a merged sample) — the route maps that to HTTP 404 per Plan §10.6.
    Raises :class:`HTTPException` (404) when the registered per-sample DB
    file is missing on disk, consistent with the other merge-aware routes.
    """
    settings = registry.settings  # type: ignore[attr-defined]
    with registry.reference_engine.connect() as conn:  # type: ignore[attr-defined]
        sample_row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    if sample_row is None:
        return None
    sample_db_path = settings.data_dir / sample_row.db_path
    if not sample_db_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Sample database file not found for sample {sample_id}.",
        )
    engine = registry.get_sample_engine(sample_db_path)  # type: ignore[attr-defined]
    with engine.connect() as conn:
        try:
            return conn.execute(sa.select(merge_provenance)).fetchone()
        except sa.exc.OperationalError:
            # ``merge_provenance`` table was added in schema v8; very old
            # per-sample DBs that have not yet been upgraded won't carry
            # it, in which case "no provenance" is the correct answer.
            return None


@router.get(
    "/{sample_id}/merge-provenance",
    dependencies=[Depends(require_fresh_sample)],
)
async def get_merge_provenance(sample_id: int) -> MergeProvenanceResponse:
    """Return the ``merge_provenance`` row if this sample was merged, else 404.

    Plan §10.6: the merged-sample artefact is queryable independently of
    the originating individual, so the route lives under ``/api/samples``
    rather than ``/api/individuals``. Read-only; gated by
    :func:`backend.api.dependencies.require_fresh_sample` per Plan §7.5.
    """
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        sample_row = conn.execute(
            sa.select(samples.c.id).where(samples.c.id == sample_id)
        ).fetchone()
    if sample_row is None:
        raise HTTPException(status_code=404, detail=f"Sample {sample_id} not found.")

    prov_row = _read_merge_provenance(registry, sample_id)
    if prov_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Sample {sample_id} has no merge provenance.",
        )

    try:
        source_sample_ids = json.loads(prov_row.source_sample_ids)
        source_file_hashes = json.loads(prov_row.source_file_hashes)
        concordance_summary = json.loads(prov_row.concordance_summary)
    except (json.JSONDecodeError, TypeError) as exc:
        # On-disk corruption — surface 500 rather than crash with a raw
        # JSONDecodeError. Plan §10.4 (c) treats the JSON columns as
        # written-by-merge-service only, so this should be unreachable.
        raise HTTPException(
            status_code=500,
            detail=f"merge_provenance JSON malformed for sample {sample_id}.",
        ) from exc

    return MergeProvenanceResponse(
        merged_at=str(prov_row.merged_at) if prov_row.merged_at else "",
        strategy=prov_row.strategy,
        source_sample_ids=source_sample_ids,
        source_file_hashes=source_file_hashes,
        concordance_summary=concordance_summary,
    )


@router.get(
    "/{sample_id}/concordance-report",
    dependencies=[Depends(require_fresh_sample)],
)
async def get_concordance_report(
    sample_id: int,
    limit: int = Query(
        _CONCORDANCE_REPORT_DEFAULT_LIMIT,
        ge=1,
        le=_CONCORDANCE_REPORT_MAX_LIMIT,
        description="Page size (1–500, default 50).",
    ),
    offset: int = Query(0, ge=0, description="Page offset (default 0)."),
) -> ConcordanceReportResponse:
    """Paginated discordant-loci report with gene context (Plan §10.6).

    Returns ``concordance_summary`` (from ``merge_provenance``), the total
    number of discordant rows (for client-side pagination), and a page of
    ``discordant_loci`` rows ordered by ``(chrom, pos)`` ascending. Each
    row LEFT-JOINs ``annotated_variants`` so the table can show gene +
    consequence + ClinVar significance alongside the conflict.

    Default ``limit`` is 50; max is 500 (FastAPI's ``Query(le=500)``
    surfaces 422 for ``limit=501`` per Plan §10.6).
    """
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        sample_row = conn.execute(
            sa.select(samples.c.id, samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    if sample_row is None:
        raise HTTPException(status_code=404, detail=f"Sample {sample_id} not found.")

    prov_row = _read_merge_provenance(registry, sample_id)
    if prov_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Sample {sample_id} has no merge provenance.",
        )

    try:
        concordance_summary = json.loads(prov_row.concordance_summary)
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"merge_provenance JSON malformed for sample {sample_id}.",
        ) from exc

    sample_db_path = registry.settings.data_dir / sample_row.db_path
    if not sample_db_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Sample database file not found for sample {sample_id}.",
        )
    engine = registry.get_sample_engine(sample_db_path)

    discordant_filter = raw_variants.c.concordance == "discordant"
    with engine.connect() as conn:
        total_discordant = conn.execute(
            sa.select(sa.func.count()).select_from(raw_variants).where(discordant_filter)
        ).scalar_one()

        # LEFT JOIN so loci absent from annotated_variants still appear (gene
        # context simply renders as null on the wizard's table).
        loci_query = (
            sa.select(
                raw_variants.c.rsid,
                raw_variants.c.chrom,
                raw_variants.c.pos,
                raw_variants.c.genotype,
                raw_variants.c.discordant_alt_genotype,
                raw_variants.c.alt_rsid,
                annotated_variants.c.gene_symbol,
                annotated_variants.c.consequence,
                annotated_variants.c.clinvar_significance,
            )
            .select_from(
                raw_variants.outerjoin(
                    annotated_variants,
                    raw_variants.c.rsid == annotated_variants.c.rsid,
                )
            )
            .where(discordant_filter)
            .order_by(raw_variants.c.chrom.asc(), raw_variants.c.pos.asc())
            .limit(limit)
            .offset(offset)
        )
        loci_rows = conn.execute(loci_query).fetchall()

    return ConcordanceReportResponse(
        concordance_summary=concordance_summary,
        total_discordant=int(total_discordant),
        limit=limit,
        offset=offset,
        discordant_loci=[
            DiscordantLocus(
                rsid=row.rsid,
                chrom=row.chrom,
                pos=int(row.pos),
                genotype=row.genotype,
                discordant_alt_genotype=row.discordant_alt_genotype,
                alt_rsid=row.alt_rsid,
                gene_symbol=row.gene_symbol,
                consequence=row.consequence,
                clinvar_significance=row.clinvar_significance,
            )
            for row in loci_rows
        ],
    )


@router.delete("/{sample_id}", status_code=204)
async def delete_sample(sample_id: int) -> None:
    """Delete a sample and cascade to any merged children referencing it.

    AncestryDNA Plan §10.8 / Step 66: a single-confirmation cascade removes
    every ``file_format='merged_v1'`` sample whose ``merge_provenance``
    lists this row in ``source_sample_ids`` before tearing down the source.
    """
    registry = get_registry()
    result = delete_sample_with_cascade(registry, sample_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Sample {sample_id} not found.")


# ── Post-merge VUS re-watch (Plan §10.6 / Step 72 / MRG-13) ────────────


class MigrateCandidate(BaseModel):
    """One row from a source sample's ``watched_variants`` not carried over
    to the merged sample (Plan §10.6, §10.7).

    The merged sample DB is built fresh during merge; its ``watched_variants``
    table starts empty. ``rsid_on_merged_or_null`` is the rsid the merged
    sample carries at the same ``(chrom, pos)`` — populated when the locus
    survives the merge under a different rsid (the rsid-collapse case), and
    ``None`` when the locus is absent from the merged sample altogether
    (private to the source).
    """

    rsid_on_source: str
    notes_on_source: str
    sample_id: int
    chrom: str
    pos: int
    rsid_on_merged_or_null: str | None = None


class MigrateFromSourcesResponse(BaseModel):
    """Plan §10.6 payload — `{candidates: [...]}`."""

    candidates: list[MigrateCandidate]


@router.get(
    "/{merged_id}/watched-variants/migrate-from-sources",
    dependencies=[Depends(require_fresh_merged_sample)],
)
async def list_migrate_from_sources(merged_id: int) -> MigrateFromSourcesResponse:
    """Return source-sample ``watched_variants`` rows not present on the merged sample.

    AncestryDNA Plan §10.6 / §10.7 (MRG-13). Tags & watches do not propagate
    across merges (the four rsid-PK tables are independent across sample
    DBs), so the dashboard surfaces a `<PostMergeRewatchModal>` on the
    new sample's first render. The modal drives the actual re-watch via
    the existing ``POST /api/watches`` route — this read-only endpoint
    only enumerates the candidates.

    Gated by :func:`backend.api.dependencies.require_fresh_merged_sample`
    (the Plan §7.5 dependency, aliased for the ``{merged_id}`` path). The
    drift guard in ``tests/backend/test_stale_sample_dependency.py``
    keeps the route classified.
    """
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        merged_row = conn.execute(
            sa.select(samples.c.id, samples.c.db_path).where(samples.c.id == merged_id)
        ).fetchone()
    if merged_row is None:
        raise HTTPException(status_code=404, detail=f"Sample {merged_id} not found.")

    prov_row = _read_merge_provenance(registry, merged_id)
    if prov_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Sample {merged_id} has no merge provenance.",
        )

    try:
        source_sample_ids = json.loads(prov_row.source_sample_ids)
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"merge_provenance JSON malformed for sample {merged_id}.",
        ) from exc

    merged_db_path = registry.settings.data_dir / merged_row.db_path
    if not merged_db_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Sample database file not found for sample {merged_id}.",
        )
    merged_engine = registry.get_sample_engine(merged_db_path)

    # Pull the merged sample's full rsid set + coordinate→rsid map once so
    # the per-candidate lookups are pure dict access.
    with merged_engine.connect() as conn:
        merged_rsids: set[str] = {
            row.rsid for row in conn.execute(sa.select(raw_variants.c.rsid)).fetchall()
        }
        merged_coord_to_rsid: dict[tuple[str, int], str] = {
            (row.chrom, int(row.pos)): row.rsid
            for row in conn.execute(
                sa.select(
                    raw_variants.c.chrom,
                    raw_variants.c.pos,
                    raw_variants.c.rsid,
                )
            ).fetchall()
        }

    candidates: list[MigrateCandidate] = []

    for src_id_raw in source_sample_ids:
        try:
            src_id = int(src_id_raw)
        except (TypeError, ValueError):
            logger.warning(
                "merge_provenance carries non-integer source id %r for "
                "merged sample %s — skipping.",
                src_id_raw,
                merged_id,
            )
            continue

        with registry.reference_engine.connect() as conn:
            src_row = conn.execute(
                sa.select(samples.c.db_path).where(samples.c.id == src_id)
            ).fetchone()
        if src_row is None:
            logger.warning(
                "merge_provenance references missing source sample %d "
                "for merged sample %d — skipping.",
                src_id,
                merged_id,
            )
            continue

        src_db_path = registry.settings.data_dir / src_row.db_path
        if not src_db_path.exists():
            logger.warning(
                "Source sample %d DB file missing at %s — skipping.",
                src_id,
                src_db_path,
            )
            continue

        src_engine = registry.get_sample_engine(src_db_path)
        try:
            with src_engine.connect() as conn:
                # LEFT JOIN watched_variants → raw_variants so we capture
                # (chrom, pos) of the watched rsid in a single query.
                rows = conn.execute(
                    sa.select(
                        watched_variants.c.rsid,
                        watched_variants.c.notes,
                        raw_variants.c.chrom,
                        raw_variants.c.pos,
                    ).select_from(
                        watched_variants.outerjoin(
                            raw_variants,
                            watched_variants.c.rsid == raw_variants.c.rsid,
                        )
                    )
                ).fetchall()
        except sa.exc.OperationalError:
            # Pre-v7 source DB without ``watched_variants`` — no candidates
            # to surface.
            continue

        for row in rows:
            if row.rsid in merged_rsids:
                continue
            if row.chrom is None or row.pos is None:
                # Watched rsid has no raw_variants row on the source — we
                # cannot resolve a coordinate, so no migration path exists.
                # Skip silently; the watch lingers on the source sample.
                continue
            chrom = str(row.chrom)
            pos = int(row.pos)
            rsid_on_merged = merged_coord_to_rsid.get((chrom, pos))
            candidates.append(
                MigrateCandidate(
                    rsid_on_source=row.rsid,
                    notes_on_source=row.notes or "",
                    sample_id=src_id,
                    chrom=chrom,
                    pos=pos,
                    rsid_on_merged_or_null=rsid_on_merged,
                )
            )

    return MigrateFromSourcesResponse(candidates=candidates)
