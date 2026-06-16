"""Variant tagging API endpoints (P4-12b).

Tag CRUD and variant ↔ tag association management for per-sample databases.

GET    /api/tags                — List all tags for a sample
POST   /api/tags                — Create custom tag
PUT    /api/tags/{tag_id}       — Update custom tag
DELETE /api/tags/{tag_id}       — Delete custom tag
POST   /api/tags/variant        — Add tag to variant
DELETE /api/tags/variant        — Remove tag from variant
GET    /api/tags/variant/{rsid} — Get all tags for a variant
GET    /api/tags/variants       — Get all rsids with a specific tag
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import samples, tags, variant_tags

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tags", tags=["tags"])


# ── Response / Request models ─────────────────────────────────────


class TagResponse(BaseModel):
    """Single tag returned by the API."""

    id: int
    name: str
    color: str
    is_predefined: bool
    created_at: str | None = None
    variant_count: int | None = None


class TagCreate(BaseModel):
    """Request body for creating a custom tag."""

    sample_id: int
    name: str
    color: str = Field(default="#6B7280", pattern=r"^#[0-9A-Fa-f]{6}$")


class TagUpdate(BaseModel):
    """Request body for updating a custom tag."""

    sample_id: int
    name: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class VariantTagAction(BaseModel):
    """Request body for adding a tag to a variant."""

    sample_id: int
    rsid: str
    tag_id: int


# ── Helpers ───────────────────────────────────────────────────────


def _get_sample_engine(sample_id: int) -> sa.Engine:
    """Resolve sample_id to a per-sample DB engine.

    Raises HTTPException(404) if the sample doesn't exist.
    """
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Sample {sample_id} not found.")

    sample_db_path = registry.settings.data_dir / row.db_path
    if not sample_db_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Sample database file not found for sample {sample_id}.",
        )
    return registry.get_sample_engine(sample_db_path)


# ── Tag CRUD ──────────────────────────────────────────────────────


@router.get("", dependencies=[Depends(require_fresh_sample)])
def list_tags(
    sample_id: int = Query(..., description="Sample ID"),
) -> list[TagResponse]:
    """List all tags for a sample with variant counts."""
    engine = _get_sample_engine(sample_id)

    query = (
        sa.select(
            tags.c.id,
            tags.c.name,
            tags.c.color,
            tags.c.is_predefined,
            tags.c.created_at,
            sa.func.count(variant_tags.c.rsid).label("variant_count"),
        )
        .select_from(tags.outerjoin(variant_tags, tags.c.id == variant_tags.c.tag_id))
        .group_by(tags.c.id)
        .order_by(tags.c.id)
    )

    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    return [
        TagResponse(
            id=row.id,
            name=row.name,
            color=row.color,
            is_predefined=bool(row.is_predefined),
            created_at=str(row.created_at) if row.created_at else None,
            variant_count=row.variant_count,
        )
        for row in rows
    ]


@router.post("", status_code=201)
def create_tag(body: TagCreate) -> TagResponse:
    """Create a custom tag."""
    require_fresh_sample(body.sample_id)
    if not body.name or not body.name.strip():
        raise HTTPException(status_code=422, detail="Tag name cannot be empty.")

    engine = _get_sample_engine(body.sample_id)

    with engine.begin() as conn:
        # Check for duplicate name
        existing = conn.execute(
            sa.select(tags.c.id).where(tags.c.name == body.name.strip())
        ).fetchone()
        if existing is not None:
            raise HTTPException(
                status_code=409, detail=f"Tag '{body.name.strip()}' already exists."
            )

        result = conn.execute(
            tags.insert().values(
                name=body.name.strip(),
                color=body.color,
                is_predefined=False,
            )
        )
        tag_id = result.lastrowid

        row = conn.execute(sa.select(tags).where(tags.c.id == tag_id)).fetchone()

    return TagResponse(
        id=row.id,
        name=row.name,
        color=row.color,
        is_predefined=bool(row.is_predefined),
        created_at=str(row.created_at) if row.created_at else None,
        variant_count=0,
    )


# ── Variant Tagging ──────────────────────────────────────────────
# NOTE: These literal-path routes (/variant, /variant/{rsid}, /variants)
# must be registered BEFORE the parameterised /{tag_id} routes so that
# Starlette's first-match routing doesn't capture "variant" as a tag_id.


@router.post("/variant")
def add_variant_tag(body: VariantTagAction) -> dict[str, str]:
    """Add a tag to a variant (INSERT OR IGNORE)."""
    require_fresh_sample(body.sample_id)
    engine = _get_sample_engine(body.sample_id)

    with engine.begin() as conn:
        # Verify tag exists
        tag_row = conn.execute(sa.select(tags.c.id).where(tags.c.id == body.tag_id)).fetchone()
        if tag_row is None:
            raise HTTPException(status_code=404, detail=f"Tag {body.tag_id} not found.")

        conn.execute(
            sa.text("INSERT OR IGNORE INTO variant_tags (rsid, tag_id) VALUES (:rsid, :tag_id)"),
            {"rsid": body.rsid, "tag_id": body.tag_id},
        )

    return {"status": "ok"}


@router.delete("/variant", dependencies=[Depends(require_fresh_sample)])
def remove_variant_tag(
    sample_id: int = Query(..., description="Sample ID"),
    rsid: str = Query(..., description="Variant rsid"),
    tag_id: int = Query(..., description="Tag ID"),
) -> dict[str, str]:
    """Remove a tag from a variant."""
    engine = _get_sample_engine(sample_id)

    with engine.begin() as conn:
        conn.execute(
            variant_tags.delete().where(
                sa.and_(
                    variant_tags.c.rsid == rsid,
                    variant_tags.c.tag_id == tag_id,
                )
            )
        )

    return {"status": "ok"}


@router.get("/variant/{rsid}", dependencies=[Depends(require_fresh_sample)])
def get_variant_tags(
    rsid: str,
    sample_id: int = Query(..., description="Sample ID"),
) -> list[TagResponse]:
    """Get all tags for a specific variant."""
    engine = _get_sample_engine(sample_id)

    query = (
        sa.select(
            tags.c.id,
            tags.c.name,
            tags.c.color,
            tags.c.is_predefined,
            tags.c.created_at,
        )
        .select_from(variant_tags.join(tags, variant_tags.c.tag_id == tags.c.id))
        .where(variant_tags.c.rsid == rsid)
        .order_by(tags.c.id)
    )

    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    return [
        TagResponse(
            id=row.id,
            name=row.name,
            color=row.color,
            is_predefined=bool(row.is_predefined),
            created_at=str(row.created_at) if row.created_at else None,
        )
        for row in rows
    ]


@router.get("/variants", dependencies=[Depends(require_fresh_sample)])
def get_variants_by_tag(
    sample_id: int = Query(..., description="Sample ID"),
    tag_id: int = Query(..., description="Tag ID"),
) -> list[str]:
    """Get all rsids that have a specific tag."""
    engine = _get_sample_engine(sample_id)

    query = (
        sa.select(variant_tags.c.rsid)
        .where(variant_tags.c.tag_id == tag_id)
        .order_by(variant_tags.c.rsid)
    )

    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    return [row.rsid for row in rows]


# ── Tag CRUD (parameterised /{tag_id}) ───────────────────────────


@router.put("/{tag_id}")
def update_tag(tag_id: int, body: TagUpdate) -> TagResponse:
    """Update a custom tag (name and/or color)."""
    require_fresh_sample(body.sample_id)
    engine = _get_sample_engine(body.sample_id)

    with engine.begin() as conn:
        row = conn.execute(sa.select(tags).where(tags.c.id == tag_id)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Tag {tag_id} not found.")
        if row.is_predefined:
            raise HTTPException(status_code=403, detail="Cannot update predefined tags.")

        updates: dict = {}
        new_name = body.name.strip() if body.name else None
        if new_name:
            # Check for name conflict
            conflict = conn.execute(
                sa.select(tags.c.id).where(sa.and_(tags.c.name == new_name, tags.c.id != tag_id))
            ).fetchone()
            if conflict is not None:
                raise HTTPException(status_code=409, detail=f"Tag '{new_name}' already exists.")
            updates["name"] = new_name
        if body.color is not None:
            updates["color"] = body.color

        if updates:
            conn.execute(tags.update().where(tags.c.id == tag_id).values(**updates))

        updated = conn.execute(sa.select(tags).where(tags.c.id == tag_id)).fetchone()

    # Get variant count
    with engine.connect() as conn:
        vcount = (
            conn.execute(
                sa.select(sa.func.count())
                .select_from(variant_tags)
                .where(variant_tags.c.tag_id == tag_id)
            ).scalar()
            or 0
        )

    return TagResponse(
        id=updated.id,
        name=updated.name,
        color=updated.color,
        is_predefined=bool(updated.is_predefined),
        created_at=str(updated.created_at) if updated.created_at else None,
        variant_count=vcount,
    )


@router.delete(
    "/{tag_id}",
    status_code=204,
    dependencies=[Depends(require_fresh_sample)],
)
def delete_tag(
    tag_id: int,
    sample_id: int = Query(..., description="Sample ID"),
) -> None:
    """Delete a custom tag and its variant associations."""
    engine = _get_sample_engine(sample_id)

    with engine.begin() as conn:
        row = conn.execute(
            sa.select(tags.c.id, tags.c.is_predefined).where(tags.c.id == tag_id)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Tag {tag_id} not found.")
        if row.is_predefined:
            raise HTTPException(status_code=403, detail="Cannot delete predefined tags.")

        conn.execute(variant_tags.delete().where(variant_tags.c.tag_id == tag_id))
        conn.execute(tags.delete().where(tags.c.id == tag_id))
