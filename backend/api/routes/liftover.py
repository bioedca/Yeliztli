"""Liftover API endpoints (P4-19).

Provides GRCh38 lifted coordinates for sample variants. Coordinates are
computed on demand and cached in the ``annotated_variants`` table's
``chrom_grch38`` / ``pos_grch38`` columns.

POST /api/liftover/{sample_id}          — Batch liftover for a sample
GET  /api/liftover/convert              — Convert a single coordinate
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.api.dependencies import require_fresh_sample, sample_export_guard
from backend.db.connection import get_registry
from backend.db.tables import annotated_variants, samples
from backend.ingestion.liftover import batch_convert, convert_coordinate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/liftover", tags=["liftover"])


# ── Response models ──────────────────────────────────────────────────


class SingleLiftoverResult(BaseModel):
    """Result of converting a single coordinate."""

    chrom_grch37: str
    pos_grch37: int
    chrom_grch38: str | None = None
    pos_grch38: int | None = None
    success: bool = False


class BatchLiftoverStats(BaseModel):
    """Statistics from a batch liftover operation."""

    total: int
    converted: int
    failed: int
    already_lifted: int


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/convert", response_model=SingleLiftoverResult)
def convert_single(
    chrom: str = Query(..., description="Chromosome (e.g. '1', 'X', 'MT')"),
    pos: int = Query(..., gt=0, description="1-based GRCh37 position"),
) -> SingleLiftoverResult:
    """Convert a single GRCh37 coordinate to GRCh38."""
    result = convert_coordinate(chrom, pos)
    if result is None:
        return SingleLiftoverResult(
            chrom_grch37=chrom,
            pos_grch37=pos,
            success=False,
        )
    return SingleLiftoverResult(
        chrom_grch37=chrom,
        pos_grch37=pos,
        chrom_grch38=result[0],
        pos_grch38=result[1],
        success=True,
    )


@router.post(
    "/{sample_id}",
    response_model=BatchLiftoverStats,
    dependencies=[Depends(require_fresh_sample)],
)
def batch_liftover_sample(
    sample_id: int,
) -> BatchLiftoverStats:
    """Compute and store GRCh38 coordinates for all annotated variants in a sample.

    Skips variants that already have lifted coordinates. Unmapped variants
    get NULL values for chrom_grch38/pos_grch38.
    """
    registry = get_registry()

    # Verify sample exists
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.id, samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Sample {sample_id} not found")

    # get_sample_engine takes the sample DB *path*, not the sample id — passing
    # the int raised TypeError inside Path() and made every call 500 (#2029).
    # Nothing invoked this route, so no test or user ever reached the failure.
    sample_engine = registry.get_sample_engine(registry.settings.data_dir / row.db_path)

    # Hold the per-sample operation lease across the whole read → convert →
    # write span. Annotation swaps the annotated_variants contents wholesale, so
    # without this an initial annotation could make the batch observe zero rows
    # and report success, and a re-annotation could either discard what the
    # batch wrote or have coordinates computed from pre-swap positions applied
    # by rsid to post-swap rows. The lease is the same one export and report
    # rendering take, and it refuses to start while annotation is active (409).
    with sample_export_guard(sample_id, operation="grch38_liftover"):
        return _run_batch_liftover(sample_engine)


def _run_batch_liftover(sample_engine: sa.Engine) -> BatchLiftoverStats:
    """Read the unlifted rows, convert them, and write the coordinates back."""
    # Find variants that need liftover (no chrom_grch38 yet)
    with sample_engine.connect() as conn:
        # Check if the columns exist (schema may not be upgraded yet)
        inspector = sa.inspect(sample_engine)
        existing_cols = {c["name"] for c in inspector.get_columns("annotated_variants")}
        if "chrom_grch38" not in existing_cols:
            raise HTTPException(
                status_code=409,
                detail="Sample database schema needs upgrade for liftover columns",
            )

        already_lifted = (
            conn.execute(
                sa.select(sa.func.count())
                .select_from(annotated_variants)
                .where(annotated_variants.c.chrom_grch38.isnot(None))
            ).scalar()
            or 0
        )

        rows = conn.execute(
            sa.select(
                annotated_variants.c.rsid,
                annotated_variants.c.chrom,
                annotated_variants.c.pos,
            ).where(annotated_variants.c.chrom_grch38.is_(None))
        ).fetchall()

    if not rows:
        # Count total to report
        with sample_engine.connect() as conn:
            total = (
                conn.execute(sa.select(sa.func.count()).select_from(annotated_variants)).scalar()
                or 0
            )
        return BatchLiftoverStats(
            total=total,
            converted=0,
            failed=0,
            already_lifted=already_lifted,
        )

    # Run liftover
    variants = [(r.rsid, r.chrom, r.pos) for r in rows]
    lift_results = batch_convert(variants)

    # Write results back using batched executemany
    converted = 0
    failed = 0
    updates: list[dict[str, str | int]] = []
    for rsid, result in lift_results.items():
        if result is not None:
            updates.append({"_rsid": rsid, "chrom_grch38": result[0], "pos_grch38": result[1]})
            converted += 1
        else:
            failed += 1

    if updates:
        stmt = (
            annotated_variants.update()
            .where(annotated_variants.c.rsid == sa.bindparam("_rsid"))
            .values(
                chrom_grch38=sa.bindparam("chrom_grch38"),
                pos_grch38=sa.bindparam("pos_grch38"),
            )
        )
        with sample_engine.begin() as conn:
            conn.execute(stmt, updates)

    with sample_engine.connect() as conn:
        total = (
            conn.execute(sa.select(sa.func.count()).select_from(annotated_variants)).scalar() or 0
        )

    return BatchLiftoverStats(
        total=total,
        converted=converted,
        failed=failed,
        already_lifted=already_lifted,
    )
