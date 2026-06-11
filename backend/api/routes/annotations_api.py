"""Annotated variants API (P2-19).

Dedicated endpoints for querying annotated variants with full annotation
fields, rich filtering (ClinVar significance, consequence, AF threshold,
evidence conflict), and cursor-based keyset pagination.

GET  /api/annotations              — Paginated annotated variants
GET  /api/annotations/count        — Total count (async, separate query)
GET  /api/annotations/chromosomes  — Per-chromosome counts
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.analysis.alphamissense import alphamissense_badge_for_variant
from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import annotated_variants, samples

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/annotations",
    tags=["annotations"],
    dependencies=[Depends(require_fresh_sample)],
)

# Canonical chromosome sort order — matches VCF export and /api/variants.
CHROM_ORDER: dict[str, int] = {
    **{str(i): i for i in range(1, 23)},
    "X": 23,
    "Y": 24,
    "MT": 25,
}


# ── Response models ──────────────────────────────────────────────────


class AnnotatedVariantRow(BaseModel):
    """Full annotated variant with all annotation fields."""

    # Core
    rsid: str
    chrom: str
    pos: int
    ref: str | None = None
    alt: str | None = None
    genotype: str | None = None
    zygosity: str | None = None

    # VEP (bitmask bit 0)
    gene_symbol: str | None = None
    transcript_id: str | None = None
    consequence: str | None = None
    hgvs_coding: str | None = None
    hgvs_protein: str | None = None
    strand: str | None = None
    exon_number: int | None = None
    intron_number: int | None = None
    mane_select: bool | None = None

    # ClinVar (bitmask bit 1)
    clinvar_significance: str | None = None
    clinvar_review_stars: int | None = None
    clinvar_accession: str | None = None
    clinvar_conditions: str | None = None

    # gnomAD (bitmask bit 2)
    gnomad_af_global: float | None = None
    gnomad_af_afr: float | None = None
    gnomad_af_amr: float | None = None
    gnomad_af_eas: float | None = None
    gnomad_af_eur: float | None = None
    gnomad_af_fin: float | None = None
    gnomad_af_sas: float | None = None
    gnomad_homozygous_count: int | None = None
    rare_flag: bool | None = None
    ultra_rare_flag: bool | None = None

    # dbNSFP (bitmask bit 3)
    cadd_phred: float | None = None
    sift_score: float | None = None
    sift_pred: str | None = None
    polyphen2_hsvar_score: float | None = None
    polyphen2_hsvar_pred: str | None = None
    revel: float | None = None
    mutpred2: float | None = None
    vest4: float | None = None
    metasvm: float | None = None
    metalr: float | None = None
    gerp_rs: float | None = None
    phylop: float | None = None
    mpc: float | None = None
    primateai: float | None = None

    # AlphaMissense (context-only complement to REVEL)
    alphamissense_pathogenicity: float | None = None
    alphamissense_class: str | None = None
    alphamissense_badge: dict[str, Any] | None = None

    # dbSNP
    dbsnp_build: int | None = None
    dbsnp_rsid_current: str | None = None
    dbsnp_validation: str | None = None

    # Gene-phenotype (bitmask bit 4)
    disease_name: str | None = None
    disease_id: str | None = None
    phenotype_source: str | None = None
    hpo_terms: str | None = None
    inheritance_pattern: str | None = None

    # Ensemble / conflict
    deleterious_count: int | None = None
    evidence_conflict: bool | None = None
    ensemble_pathogenic: bool | None = None
    annotation_coverage: int | None = None


class AnnotatedVariantPage(BaseModel):
    """Paginated response for annotated variant listing."""

    items: list[AnnotatedVariantRow]
    next_cursor_chrom: str | None = None
    next_cursor_pos: int | None = None
    has_more: bool = False
    limit: int


class AnnotatedVariantCount(BaseModel):
    """Response for the async total count endpoint."""

    total: int
    filtered: bool = False


class AnnotatedChromosomeSummary(BaseModel):
    """Per-chromosome annotated variant count."""

    chrom: str
    count: int


# ── Helpers ──────────────────────────────────────────────────────────

_TABLE = annotated_variants


def _get_sample_engine(sample_id: int) -> sa.Engine:
    """Resolve sample_id to a per-sample DB engine."""
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


def _require_annotated(engine: sa.Engine) -> None:
    """Raise 404 if annotated_variants table is empty (annotation not run)."""
    with engine.connect() as conn:
        has_rows = conn.execute(sa.select(sa.literal(1)).select_from(_TABLE).limit(1)).fetchone()
    if has_rows is None:
        raise HTTPException(
            status_code=404,
            detail="No annotated variants found. Run annotation first.",
        )


def _chrom_sort_key(chrom: str) -> int:
    return CHROM_ORDER.get(chrom, 99)


def _chrom_order_expr() -> sa.Case:
    return sa.case(
        *[(_TABLE.c.chrom == k, v) for k, v in CHROM_ORDER.items()],
        else_=99,
    )


def _build_cursor_clause(
    cursor_chrom: str | None,
    cursor_pos: int | None,
) -> sa.ColumnElement | None:
    if cursor_chrom is None or cursor_pos is None:
        return None
    cursor_order = _chrom_sort_key(cursor_chrom)
    expr = _chrom_order_expr()
    return sa.or_(
        expr > cursor_order,
        sa.and_(expr == cursor_order, _TABLE.c.pos > cursor_pos),
    )


def _build_order_by() -> list:
    return [_chrom_order_expr().asc(), _TABLE.c.pos.asc()]


def _build_filters(
    *,
    chrom: str | None,
    gene_symbol: str | None,
    consequence: str | None,
    clinvar: str | None,
    af_max: float | None,
    af_min: float | None,
    rare: bool | None,
    ultra_rare: bool | None,
    evidence_conflict: bool | None,
    ensemble_pathogenic: bool | None,
    zygosity: str | None,
    mane_select: bool | None,
) -> list[sa.ColumnElement]:
    """Build WHERE clauses from named query params."""
    clauses: list[sa.ColumnElement] = []

    if chrom is not None:
        clauses.append(_TABLE.c.chrom == chrom)
    if gene_symbol is not None:
        clauses.append(_TABLE.c.gene_symbol == gene_symbol)
    if consequence is not None:
        clauses.append(_TABLE.c.consequence == consequence)
    if clinvar is not None:
        # Case-insensitive match: "pathogenic" matches "Pathogenic",
        # "Pathogenic/Likely pathogenic", etc.
        clauses.append(sa.func.lower(_TABLE.c.clinvar_significance).contains(clinvar.lower()))
    if af_max is not None:
        clauses.append(_TABLE.c.gnomad_af_global <= af_max)
    if af_min is not None:
        clauses.append(_TABLE.c.gnomad_af_global >= af_min)
    if rare is not None:
        clauses.append(_TABLE.c.rare_flag == rare)
    if ultra_rare is not None:
        clauses.append(_TABLE.c.ultra_rare_flag == ultra_rare)
    if evidence_conflict is not None:
        clauses.append(_TABLE.c.evidence_conflict == evidence_conflict)
    if ensemble_pathogenic is not None:
        clauses.append(_TABLE.c.ensemble_pathogenic == ensemble_pathogenic)
    if zygosity is not None:
        clauses.append(_TABLE.c.zygosity == zygosity)
    if mane_select is not None:
        clauses.append(_TABLE.c.mane_select == mane_select)

    return clauses


def _row_to_annotated_variant(row: sa.Row) -> AnnotatedVariantRow:
    """Convert a SQLAlchemy Row to AnnotatedVariantRow."""
    data: dict[str, Any] = {}
    for col in _TABLE.c:
        data[col.name] = getattr(row, col.name, None)
    data["alphamissense_badge"] = alphamissense_badge_for_variant(
        data.get("alphamissense_pathogenicity"),
        data.get("alphamissense_class"),
        revel=data.get("revel"),
        consequence=data.get("consequence"),
    )
    return AnnotatedVariantRow(**data)


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("")
def list_annotated_variants(
    sample_id: int = Query(..., description="Sample ID"),
    cursor_chrom: str | None = Query(None, description="Cursor chromosome"),
    cursor_pos: int | None = Query(None, description="Cursor position"),
    limit: int = Query(50, ge=1, le=500, description="Page size"),
    # Filters
    chrom: str | None = Query(None, description="Chromosome filter"),
    gene_symbol: str | None = Query(None, description="Gene symbol (exact match)"),
    consequence: str | None = Query(None, description="SO consequence term (exact match)"),
    clinvar: str | None = Query(
        None, description="ClinVar significance (case-insensitive substring match)"
    ),
    af_max: float | None = Query(None, ge=0.0, le=1.0, description="Max gnomAD global AF (<=)"),
    af_min: float | None = Query(None, ge=0.0, le=1.0, description="Min gnomAD global AF (>=)"),
    rare: bool | None = Query(None, description="Rare variant flag"),
    ultra_rare: bool | None = Query(None, description="Ultra-rare variant flag"),
    evidence_conflict: bool | None = Query(None, description="Evidence conflict flag"),
    ensemble_pathogenic: bool | None = Query(None, description="Ensemble pathogenic flag"),
    zygosity: str | None = Query(None, description="Zygosity filter (het, hom_alt, hom_ref)"),
    mane_select: bool | None = Query(None, description="MANE Select transcript only"),
) -> AnnotatedVariantPage:
    """Return a page of annotated variants with full annotation fields.

    Cursor-based keyset pagination on (chrom, pos). Supports filtering by
    ClinVar significance, consequence type, AF threshold, evidence conflict,
    and more.

    Example: ``GET /api/annotations?sample_id=1&clinvar=pathogenic&af_max=0.01``
    """
    sample_engine = _get_sample_engine(sample_id)
    _require_annotated(sample_engine)

    query = sa.select(_TABLE)

    filter_clauses = _build_filters(
        chrom=chrom,
        gene_symbol=gene_symbol,
        consequence=consequence,
        clinvar=clinvar,
        af_max=af_max,
        af_min=af_min,
        rare=rare,
        ultra_rare=ultra_rare,
        evidence_conflict=evidence_conflict,
        ensemble_pathogenic=ensemble_pathogenic,
        zygosity=zygosity,
        mane_select=mane_select,
    )
    if filter_clauses:
        query = query.where(sa.and_(*filter_clauses))

    cursor_clause = _build_cursor_clause(cursor_chrom, cursor_pos)
    if cursor_clause is not None:
        query = query.where(cursor_clause)

    query = query.order_by(*_build_order_by()).limit(limit + 1)

    with sample_engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    has_more = len(rows) > limit
    result_rows = rows[:limit]
    items = [_row_to_annotated_variant(row) for row in result_rows]

    next_chrom: str | None = None
    next_pos: int | None = None
    if has_more and result_rows:
        last = result_rows[-1]
        next_chrom = last.chrom
        next_pos = last.pos

    return AnnotatedVariantPage(
        items=items,
        next_cursor_chrom=next_chrom,
        next_cursor_pos=next_pos,
        has_more=has_more,
        limit=limit,
    )


@router.get("/count")
def annotated_variant_count(
    sample_id: int = Query(..., description="Sample ID"),
    chrom: str | None = Query(None),
    gene_symbol: str | None = Query(None),
    consequence: str | None = Query(None),
    clinvar: str | None = Query(None),
    af_max: float | None = Query(None, ge=0.0, le=1.0),
    af_min: float | None = Query(None, ge=0.0, le=1.0),
    rare: bool | None = Query(None),
    ultra_rare: bool | None = Query(None),
    evidence_conflict: bool | None = Query(None),
    ensemble_pathogenic: bool | None = Query(None),
    zygosity: str | None = Query(None),
    mane_select: bool | None = Query(None),
) -> AnnotatedVariantCount:
    """Return total annotated variant count, optionally filtered.

    Designed for async loading after the first page is displayed.
    """
    sample_engine = _get_sample_engine(sample_id)
    _require_annotated(sample_engine)

    query = sa.select(sa.func.count()).select_from(_TABLE)

    filter_clauses = _build_filters(
        chrom=chrom,
        gene_symbol=gene_symbol,
        consequence=consequence,
        clinvar=clinvar,
        af_max=af_max,
        af_min=af_min,
        rare=rare,
        ultra_rare=ultra_rare,
        evidence_conflict=evidence_conflict,
        ensemble_pathogenic=ensemble_pathogenic,
        zygosity=zygosity,
        mane_select=mane_select,
    )
    if filter_clauses:
        query = query.where(sa.and_(*filter_clauses))

    with sample_engine.connect() as conn:
        total = conn.execute(query).scalar() or 0

    return AnnotatedVariantCount(total=total, filtered=bool(filter_clauses))


@router.get("/chromosomes")
def annotated_chromosome_counts(
    sample_id: int = Query(..., description="Sample ID"),
    chrom: str | None = Query(None),
    gene_symbol: str | None = Query(None),
    consequence: str | None = Query(None),
    clinvar: str | None = Query(None),
    af_max: float | None = Query(None, ge=0.0, le=1.0),
    af_min: float | None = Query(None, ge=0.0, le=1.0),
    rare: bool | None = Query(None),
    ultra_rare: bool | None = Query(None),
    evidence_conflict: bool | None = Query(None),
    ensemble_pathogenic: bool | None = Query(None),
    zygosity: str | None = Query(None),
    mane_select: bool | None = Query(None),
) -> list[AnnotatedChromosomeSummary]:
    """Return per-chromosome annotated variant counts in canonical order."""
    sample_engine = _get_sample_engine(sample_id)
    _require_annotated(sample_engine)

    query = (
        sa.select(_TABLE.c.chrom, sa.func.count().label("count"))
        .select_from(_TABLE)
        .group_by(_TABLE.c.chrom)
    )

    filter_clauses = _build_filters(
        chrom=chrom,
        gene_symbol=gene_symbol,
        consequence=consequence,
        clinvar=clinvar,
        af_max=af_max,
        af_min=af_min,
        rare=rare,
        ultra_rare=ultra_rare,
        evidence_conflict=evidence_conflict,
        ensemble_pathogenic=ensemble_pathogenic,
        zygosity=zygosity,
        mane_select=mane_select,
    )
    if filter_clauses:
        query = query.where(sa.and_(*filter_clauses))

    with sample_engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    summaries = [AnnotatedChromosomeSummary(chrom=row.chrom, count=row.count) for row in rows]
    summaries.sort(key=lambda s: _chrom_sort_key(s.chrom))
    return summaries
