"""Variant table API endpoints (P1-14, P1-15d, P2-23, P2-25, P2-26, P3-26).

Cursor-based keyset pagination on (chrom, pos) for raw_variants and
annotated_variants tables in per-sample databases.

GET  /api/variants                    — Paginated variant list
GET  /api/variants/count              — Total count (async, separate query)
GET  /api/variants/chromosomes        — Per-chromosome counts
GET  /api/variants/density            — Per 1 Mb bin density by consequence tier (P2-23)
GET  /api/variants/consequence-summary — Per-consequence-type counts (P2-25)
GET  /api/variants/clinvar-summary    — ClinVar significance breakdown (P2-26)

P3-26: Variant rows include ``ancestry_matched_af`` — the gnomAD allele
frequency for the user's inferred ancestry population — and
``ancestry_matched_population`` indicating which population was matched.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.analysis.ancestry import get_ancestry_matched_af_column, get_inferred_ancestry
from backend.analysis.zygosity import is_no_call
from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import annotated_variants, raw_variants, samples, tags, variant_tags

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/variants",
    tags=["variants"],
    dependencies=[Depends(require_fresh_sample)],
)

# Canonical chromosome sort order — same as VCF export.
CHROM_ORDER: dict[str, int] = {
    **{str(i): i for i in range(1, 23)},
    "X": 23,
    "Y": 24,
    "MT": 25,
}

# Autosomes (chr1-22). The overall heterozygosity rate is, by convention, an
# autosomal quantity — sex chromosomes carry sex-dependent heterozygosity (a
# male's hemizygous single-char X/Y calls bucket as homozygous) and are QC'd
# separately. Mirrors ``backend.analysis.qc._AUTOSOMES``; the two definitions are
# locked equal by test_qc_stats (qc-stats het rate == compute_qc_metrics) (#513).
_AUTOSOMES = frozenset(str(n) for n in range(1, 23))

# Merge-provenance filter values per AncestryDNA Plan §10.4/§10.7 (Step 71).
# These columns live on ``raw_variants`` (server_default ''); when a merged
# sample is being read through ``annotated_variants`` we LEFT-JOIN to surface
# them. Filter values are validated against the closed enum sets below so a
# stray ``source:bogus`` query is silently dropped rather than 0-rowing the
# response by accident.
_MERGE_PROV_FILTER_COLS = frozenset({"source", "concordance"})
_VALID_SOURCE_VALUES = frozenset({"S1", "S2", "both"})
_VALID_CONCORDANCE_VALUES = frozenset({"match", "filled_nocall", "discordant", "unique"})

# Columns allowed as filter keys on raw_variants.
_RAW_FILTER_COLS = frozenset({"chrom", "genotype"}) | _MERGE_PROV_FILTER_COLS

# Columns allowed as filter keys on annotated_variants.
_ANNOTATED_FILTER_COLS = (
    frozenset(
        {
            "chrom",
            "genotype",
            "gene_symbol",
            "consequence",
            "clinvar_significance",
            "rare_flag",
            "ultra_rare_flag",
            "evidence_conflict",
            "ensemble_pathogenic",
            "zygosity",
            "annotation_coverage",
        }
    )
    | _MERGE_PROV_FILTER_COLS
)

# Columns that support special IS NULL / IS NOT NULL filtering.
# Filter values: "notnull" → IS NOT NULL, "null" → IS NULL.
_NULLABLE_FILTER_COLS = frozenset({"annotation_coverage"})


# ── Response models ──────────────────────────────────────────────────


class VariantRow(BaseModel):
    """Single variant row returned by the paginated endpoint."""

    rsid: str
    chrom: str
    pos: int
    genotype: str
    # Annotation fields (None when reading from raw_variants)
    ref: str | None = None
    alt: str | None = None
    zygosity: str | None = None
    gene_symbol: str | None = None
    consequence: str | None = None
    clinvar_significance: str | None = None
    clinvar_review_stars: int | None = None
    gnomad_af_global: float | None = None
    rare_flag: bool | None = None
    cadd_phred: float | None = None
    sift_score: float | None = None
    sift_pred: str | None = None
    polyphen2_hsvar_score: float | None = None
    polyphen2_hsvar_pred: str | None = None
    revel: float | None = None
    annotation_coverage: int | None = None
    evidence_conflict: bool | None = None
    ensemble_pathogenic: bool | None = None
    # P3-26: Ancestry-matched allele frequency
    ancestry_matched_af: float | None = None
    ancestry_matched_population: str | None = None
    # P4-12b: Variant tags
    tags: list[str] | None = None
    # P4-19: GRCh38 liftover coordinates
    chrom_grch38: str | None = None
    pos_grch38: int | None = None
    # AncestryDNA Plan §10.4 / §10.7 (Step 71): per-row merge provenance.
    # Carried verbatim from ``raw_variants`` (server_default ''); on unmerged
    # samples every row reports empty strings. ``alt_rsid`` surfaces the
    # rejected rsid at a different-rsid-same-coordinate collapse so the
    # variant detail panel can link it back.
    source: str = ""
    concordance: str = ""
    alt_rsid: str = ""


class VariantPage(BaseModel):
    """Paginated response for variant listing."""

    items: list[VariantRow]
    next_cursor_chrom: str | None = None
    next_cursor_pos: int | None = None
    has_more: bool = False
    limit: int


class VariantCount(BaseModel):
    """Response for the async total count endpoint."""

    total: int
    filtered: bool = False


class ChromosomeSummary(BaseModel):
    """Per-chromosome variant count for the chromosome nav bar."""

    chrom: str
    count: int


# ── Helpers ──────────────────────────────────────────────────────────


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


def _chrom_sort_key(chrom: str) -> int:
    """Return an integer sort key for a chromosome string."""
    return CHROM_ORDER.get(chrom, 99)


def _select_table(sample_engine: sa.Engine) -> sa.Table:
    """Choose annotated_variants if populated, else raw_variants."""
    with sample_engine.connect() as conn:
        has_rows = conn.execute(
            sa.select(sa.literal(1)).select_from(annotated_variants).limit(1)
        ).fetchone()
    if has_rows is not None:
        return annotated_variants
    return raw_variants


def _parse_filters(filter_str: str | None, table: sa.Table) -> list[sa.ColumnElement]:
    """Parse filter query param into SQLAlchemy WHERE clauses.

    Filter format: ``key:value`` pairs separated by commas.
    Example: ``chrom:1,gene_symbol:BRCA1,rare_flag:1``

    Returns a list of SQLAlchemy column conditions. The merge-provenance
    keys ``source`` / ``concordance`` (Step 71 / Plan §10.7) always resolve
    to ``raw_variants`` regardless of which table the list endpoint reads
    from, because those columns only live on ``raw_variants``; the
    ``list_variants`` LEFT-JOIN keeps the reference valid when the primary
    table is ``annotated_variants``.
    """
    if not filter_str:
        return []

    allowed_cols = _ANNOTATED_FILTER_COLS if table is annotated_variants else _RAW_FILTER_COLS

    clauses: list[sa.ColumnElement] = []
    for part in filter_str.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            continue
        key, _, value = part.partition(":")
        key = key.strip()
        value = value.strip()

        if key not in allowed_cols:
            continue

        # Merge-provenance filters always resolve to raw_variants and
        # validate against the closed enum sets from Plan §10.4.
        if key == "source":
            if value not in _VALID_SOURCE_VALUES:
                continue
            clauses.append(raw_variants.c.source == value)
            continue
        if key == "concordance":
            if value not in _VALID_CONCORDANCE_VALUES:
                continue
            clauses.append(raw_variants.c.concordance == value)
            continue

        if not hasattr(table.c, key):
            continue

        col = getattr(table.c, key)
        # Nullable columns: accept notnull/null for IS NOT NULL / IS NULL
        if key in _NULLABLE_FILTER_COLS and value.lower() in ("notnull", "null"):
            if value.lower() == "notnull":
                clauses.append(col.isnot(None))
            else:
                clauses.append(col.is_(None))
        # Boolean columns: accept 0/1/true/false
        elif key in ("rare_flag", "ultra_rare_flag", "evidence_conflict", "ensemble_pathogenic"):
            bool_val = value.lower() in ("1", "true", "yes")
            clauses.append(col == bool_val)
        else:
            clauses.append(col == value)

    return clauses


def _filter_requires_raw_join(filter_str: str | None) -> bool:
    """Return True when filter_str references a merge-provenance column.

    Used by ``list_variants`` / ``variant_count`` / ``chromosome_counts`` to
    decide whether to LEFT-JOIN ``raw_variants`` so the ``source`` /
    ``concordance`` filters from Plan §10.7 can resolve against it even when
    the primary table is ``annotated_variants``. Cheap text-scan; the
    canonical validation still lives in :func:`_parse_filters`.
    """
    if not filter_str:
        return False
    for part in filter_str.split(","):
        key, _, _ = part.strip().partition(":")
        if key.strip() in _MERGE_PROV_FILTER_COLS:
            return True
    return False


def _chrom_order_expr(table: sa.Table) -> sa.Case:
    """Build CASE expression mapping chrom text to canonical sort integer."""
    return sa.case(
        *[(table.c.chrom == k, v) for k, v in CHROM_ORDER.items()],
        else_=99,
    )


def _build_cursor_clause(
    table: sa.Table,
    cursor_chrom: str | None,
    cursor_pos: int | None,
) -> sa.ColumnElement | None:
    """Build the WHERE clause for keyset cursor pagination.

    The cursor is on (chrom_sort_order, pos). Since SQLite doesn't have a
    native array comparison, we use the standard two-part OR:

        (chrom_order > cursor_chrom_order)
        OR (chrom_order = cursor_chrom_order AND pos > cursor_pos)

    Because chrom is stored as text (e.g. "1", "X"), we compare using
    CHROM_ORDER integer mapping via a CASE expression.
    """
    if cursor_chrom is None or cursor_pos is None:
        return None

    cursor_order = _chrom_sort_key(cursor_chrom)
    expr = _chrom_order_expr(table)

    return sa.or_(
        expr > cursor_order,
        sa.and_(expr == cursor_order, table.c.pos > cursor_pos),
    )


def _build_order_by(table: sa.Table) -> list:
    """Build ORDER BY clause: chrom (canonical order), then pos."""
    return [_chrom_order_expr(table).asc(), table.c.pos.asc()]


def _row_to_variant(
    row: sa.Row,
    table: sa.Table,
    ancestry_af_col: str | None = None,
    ancestry_population: str | None = None,
) -> VariantRow:
    """Convert a SQLAlchemy Row to a VariantRow response model.

    Args:
        row: Database result row.
        table: The source table (raw_variants or annotated_variants).
        ancestry_af_col: gnomAD AF column name matching inferred ancestry (P3-26).
        ancestry_population: Inferred ancestry population code (P3-26).
    """
    data: dict[str, Any] = {
        "rsid": row.rsid,
        "chrom": row.chrom,
        "pos": row.pos,
        "genotype": row.genotype,
    }

    if table is annotated_variants:
        for field in (
            "ref",
            "alt",
            "zygosity",
            "gene_symbol",
            "consequence",
            "clinvar_significance",
            "clinvar_review_stars",
            "gnomad_af_global",
            "rare_flag",
            "cadd_phred",
            "sift_score",
            "sift_pred",
            "polyphen2_hsvar_score",
            "polyphen2_hsvar_pred",
            "revel",
            "annotation_coverage",
            "evidence_conflict",
            "ensemble_pathogenic",
            "chrom_grch38",
            "pos_grch38",
        ):
            data[field] = getattr(row, field, None)

        # P3-26: Ancestry-matched AF
        if ancestry_af_col:
            data["ancestry_matched_af"] = getattr(row, ancestry_af_col, None)
            data["ancestry_matched_population"] = ancestry_population

    # AncestryDNA Plan §10.4 / §10.7 (Step 71): provenance columns ride along
    # whenever they're present in the row (set by ``list_variants`` via
    # LEFT JOIN raw_variants when reading annotated_variants, or selected
    # directly when reading raw_variants). Default to '' for older rows.
    for field in ("source", "concordance", "alt_rsid"):
        value = getattr(row, field, None)
        if value is not None:
            data[field] = value

    return VariantRow(**data)


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("")
def list_variants(
    sample_id: int = Query(..., description="Sample ID to query variants for"),
    cursor_chrom: str | None = Query(None, description="Cursor chromosome"),
    cursor_pos: int | None = Query(None, description="Cursor position"),
    limit: int = Query(50, ge=1, le=500, description="Page size"),
    filter: str | None = Query(None, description="Filters as key:value,key:value"),
    tag: str | None = Query(None, description="Filter by tag name"),
) -> VariantPage:
    """Return a page of variants using cursor-based keyset pagination.

    Pagination is on ``(chrom, pos)`` using canonical chromosome order
    (1-22, X, Y, MT). Performance is O(1) at any depth - no OFFSET.
    """
    sample_engine = _get_sample_engine(sample_id)
    table = _select_table(sample_engine)

    # P3-26: Look up inferred ancestry for ancestry-matched AF display
    ancestry_af_col: str | None = None
    ancestry_population: str | None = None
    if table is annotated_variants:
        ancestry_population = get_inferred_ancestry(sample_engine)
        if ancestry_population:
            ancestry_af_col = get_ancestry_matched_af_column(ancestry_population)

    # AncestryDNA Plan §10.7 (Step 71): always carry the merge-provenance
    # columns through to the page payload so the variant table can render
    # the Source / Concordance columns + filter chips for merged samples.
    # When the primary table is ``annotated_variants`` we LEFT JOIN against
    # ``raw_variants`` (rsid PK on both) so source/concordance/alt_rsid ride
    # along even though they only live on ``raw_variants``. Unmerged samples
    # carry the server-default '' values verbatim.
    if table is annotated_variants:
        source_select = [
            annotated_variants,
            raw_variants.c.source.label("source"),
            raw_variants.c.concordance.label("concordance"),
            raw_variants.c.alt_rsid.label("alt_rsid"),
        ]
        from_clause = annotated_variants.outerjoin(
            raw_variants, annotated_variants.c.rsid == raw_variants.c.rsid
        )
    else:
        source_select = [raw_variants]
        from_clause = raw_variants

    query = sa.select(*source_select).select_from(from_clause)

    # Apply filters
    filter_clauses = _parse_filters(filter, table)
    if filter_clauses:
        query = query.where(sa.and_(*filter_clauses))

    # P4-12b: Filter by tag name
    if tag:
        tag_subq = (
            sa.select(variant_tags.c.rsid)
            .join(tags, variant_tags.c.tag_id == tags.c.id)
            .where(tags.c.name == tag)
        )
        query = query.where(table.c.rsid.in_(tag_subq))

    # Apply cursor
    cursor_clause = _build_cursor_clause(table, cursor_chrom, cursor_pos)
    if cursor_clause is not None:
        query = query.where(cursor_clause)

    # Order + limit (fetch limit+1 to detect has_more)
    query = query.order_by(*_build_order_by(table)).limit(limit + 1)

    with sample_engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    has_more = len(rows) > limit
    result_rows = rows[:limit]

    items = [
        _row_to_variant(row, table, ancestry_af_col, ancestry_population) for row in result_rows
    ]

    # P4-12b: Batch-lookup tags for all rsids in the page
    if items:
        rsid_list = [item.rsid for item in items]
        tag_query = (
            sa.select(variant_tags.c.rsid, tags.c.name)
            .join(tags, variant_tags.c.tag_id == tags.c.id)
            .where(variant_tags.c.rsid.in_(rsid_list))
        )
        with sample_engine.connect() as conn:
            tag_rows = conn.execute(tag_query).fetchall()
        tag_map: dict[str, list[str]] = {}
        for tr in tag_rows:
            tag_map.setdefault(tr.rsid, []).append(tr.name)
        for item in items:
            item.tags = tag_map.get(item.rsid)

    next_chrom: str | None = None
    next_pos: int | None = None
    if has_more and result_rows:
        last = result_rows[-1]
        next_chrom = last.chrom
        next_pos = last.pos

    return VariantPage(
        items=items,
        next_cursor_chrom=next_chrom,
        next_cursor_pos=next_pos,
        has_more=has_more,
        limit=limit,
    )


@router.get("/count")
def variant_count(
    sample_id: int = Query(..., description="Sample ID to count variants for"),
    filter: str | None = Query(None, description="Filters as key:value,key:value"),
    tag: str | None = Query(None, description="Filter by tag name"),
) -> VariantCount:
    """Return the total variant count, optionally filtered.

    This endpoint is designed to be called asynchronously after the first
    page of variants has loaded, so the UI can show the count separately.
    """
    sample_engine = _get_sample_engine(sample_id)
    table = _select_table(sample_engine)

    # AncestryDNA Plan §10.7 (Step 71): mirror ``list_variants`` and LEFT JOIN
    # ``raw_variants`` whenever the filter references a merge-provenance
    # column so source / concordance filter chips report the correct count.
    if table is annotated_variants and _filter_requires_raw_join(filter):
        from_clause = annotated_variants.outerjoin(
            raw_variants, annotated_variants.c.rsid == raw_variants.c.rsid
        )
    else:
        from_clause = table

    query = sa.select(sa.func.count()).select_from(from_clause)

    filter_clauses = _parse_filters(filter, table)
    if filter_clauses:
        query = query.where(sa.and_(*filter_clauses))

    # P4-12b: Filter by tag name
    if tag:
        tag_subq = (
            sa.select(variant_tags.c.rsid)
            .join(tags, variant_tags.c.tag_id == tags.c.id)
            .where(tags.c.name == tag)
        )
        query = query.where(table.c.rsid.in_(tag_subq))

    with sample_engine.connect() as conn:
        total = conn.execute(query).scalar() or 0

    return VariantCount(total=total, filtered=bool(filter_clauses or tag))


@router.get("/chromosomes")
def chromosome_counts(
    sample_id: int = Query(..., description="Sample ID to get chromosome counts for"),
    filter: str | None = Query(None, description="Filters as key:value,key:value"),
) -> list[ChromosomeSummary]:
    """Return per-chromosome variant counts in canonical order.

    Used by the chromosome navigation bar to show which chromosomes have
    data and their relative sizes.
    """
    sample_engine = _get_sample_engine(sample_id)
    table = _select_table(sample_engine)

    # AncestryDNA Plan §10.7 (Step 71): mirror ``list_variants`` /
    # ``variant_count`` and LEFT JOIN ``raw_variants`` whenever the filter
    # references a merge-provenance column, since ``_parse_filters`` emits
    # predicates against ``raw_variants.c.source`` / ``.c.concordance`` even
    # when the primary table is ``annotated_variants``. Without the join the
    # FROM would be invalid and per-chromosome counts wrong.
    if table is annotated_variants and _filter_requires_raw_join(filter):
        from_clause = annotated_variants.outerjoin(
            raw_variants, annotated_variants.c.rsid == raw_variants.c.rsid
        )
    else:
        from_clause = table

    query = (
        sa.select(table.c.chrom, sa.func.count().label("count"))
        .select_from(from_clause)
        .group_by(table.c.chrom)
    )

    filter_clauses = _parse_filters(filter, table)
    if filter_clauses:
        query = query.where(sa.and_(*filter_clauses))

    with sample_engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    # Sort by canonical chromosome order and return
    summaries = [ChromosomeSummary(chrom=row.chrom, count=row.count) for row in rows]
    summaries.sort(key=lambda s: _chrom_sort_key(s.chrom))
    return summaries


# ── QC stats (P1-21) ──────────────────────────────────────────────────


class ChromosomeQCStats(BaseModel):
    """Per-chromosome QC breakdown for charts."""

    chrom: str
    total: int
    het_count: int
    hom_count: int
    nocall_count: int


class QCStatsResponse(BaseModel):
    """Aggregate QC statistics for a sample."""

    total_variants: int
    called_variants: int
    nocall_variants: int
    het_count: int
    hom_count: int
    call_rate: float
    heterozygosity_rate: float
    per_chromosome: list[ChromosomeQCStats]


def _classify_genotype(genotype: str | None) -> str:
    """Classify a genotype string as het, hom, or nocall.

    Routes recognition through the shared
    :func:`backend.analysis.zygosity.is_no_call` helper so that AncestryDNA's
    ``"00"`` rows and the indel codes ``"DD"`` / ``"II"`` / ``"DI"`` / ``"ID"``
    count toward the QC no-call bucket (Plan §11.3) rather than inflating the
    homozygous / heterozygous denominators. Pre-Phase-3 these codes were
    silently bucketed as ``hom`` (``"DD"`` / ``"II"``) or ``het`` (``"DI"`` /
    ``"ID"``) and AncestryDNA ``"00"`` was ``het`` — wrong for every flavor
    of QC interpretation. This is the single QC site held to a non-byte-
    identical contract by the MRG-01a sweep.

    Remaining classification (after the no-call filter):
      - Single base call (``"A"``, ``"D"``, ``"I"``) → haploid, bucketed as ``hom``
      - Two identical chars (``"AA"``) → homozygous
      - Two different chars (``"AG"``) → heterozygous
    """
    if is_no_call(genotype):
        return "nocall"
    if len(genotype) == 1:
        return "hom"
    if genotype[0] == genotype[1]:
        return "hom"
    return "het"


@router.get("/qc-stats")
def qc_stats(
    sample_id: int = Query(..., description="Sample ID to compute QC stats for"),
) -> QCStatsResponse:
    """Compute QC statistics from variant genotype data.

    Returns overall call rate, heterozygosity rate, and per-chromosome
    breakdowns for QC chart rendering (P1-21).
    """
    sample_engine = _get_sample_engine(sample_id)
    table = _select_table(sample_engine)

    # SQL-level aggregation: GROUP BY (chrom, genotype) reduces ~600K rows
    # to ~100-200 unique combinations, avoiding loading all rows into memory.
    query = (
        sa.select(
            table.c.chrom,
            table.c.genotype,
            sa.func.count().label("cnt"),
        )
        .select_from(table)
        .group_by(table.c.chrom, table.c.genotype)
    )

    with sample_engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    # Accumulate per-chromosome stats from grouped counts
    chrom_stats: dict[str, dict[str, int]] = {}
    for row in rows:
        chrom = row.chrom
        if chrom not in chrom_stats:
            chrom_stats[chrom] = {"total": 0, "het": 0, "hom": 0, "nocall": 0}
        bucket = chrom_stats[chrom]
        classification = _classify_genotype(row.genotype)
        bucket["total"] += row.cnt
        bucket[classification] += row.cnt

    # Build per-chromosome list
    per_chrom = [
        ChromosomeQCStats(
            chrom=chrom,
            total=stats["total"],
            het_count=stats["het"],
            hom_count=stats["hom"],
            nocall_count=stats["nocall"],
        )
        for chrom, stats in chrom_stats.items()
    ]
    per_chrom.sort(key=lambda s: _chrom_sort_key(s.chrom))

    # Aggregate totals
    total = sum(s.total for s in per_chrom)
    nocall = sum(s.nocall_count for s in per_chrom)
    het = sum(s.het_count for s in per_chrom)
    hom = sum(s.hom_count for s in per_chrom)
    called = total - nocall

    call_rate = called / total if total > 0 else 0.0

    # Heterozygosity rate is AUTOSOMAL-only so the dashboard value equals the
    # canonical compute_qc_metrics() rate that het_outlier_zscore flags against,
    # and is not depressed by a male's hemizygous (single-char → "hom") X/Y/MT
    # calls. Counts and call_rate above stay genome-wide; the per-chromosome
    # het/hom bars (incl. X/Y/MT) are unchanged for display (#513).
    auto_het = sum(s.het_count for s in per_chrom if s.chrom in _AUTOSOMES)
    auto_hom = sum(s.hom_count for s in per_chrom if s.chrom in _AUTOSOMES)
    auto_called = auto_het + auto_hom
    het_rate = auto_het / auto_called if auto_called > 0 else 0.0

    return QCStatsResponse(
        total_variants=total,
        called_variants=called,
        nocall_variants=nocall,
        het_count=het,
        hom_count=hom,
        call_rate=round(call_rate, 6),
        heterozygosity_rate=round(het_rate, 6),
        per_chromosome=per_chrom,
    )


# ── Variant density (P2-23) ─────────────────────────────────────────

# VEP consequence → tier mapping (Sequence Ontology impact).
_CONSEQUENCE_TIER: dict[str, str] = {
    # HIGH
    "transcript_ablation": "HIGH",
    "splice_acceptor_variant": "HIGH",
    "splice_donor_variant": "HIGH",
    "stop_gained": "HIGH",
    "frameshift_variant": "HIGH",
    "stop_lost": "HIGH",
    "start_lost": "HIGH",
    "transcript_amplification": "HIGH",
    # MODERATE
    "missense_variant": "MODERATE",
    "inframe_insertion": "MODERATE",
    "inframe_deletion": "MODERATE",
    "protein_altering_variant": "MODERATE",
    # LOW
    "synonymous_variant": "LOW",
    "splice_region_variant": "LOW",
    "start_retained_variant": "LOW",
    "stop_retained_variant": "LOW",
    "incomplete_terminal_codon_variant": "LOW",
    "coding_sequence_variant": "LOW",
}

# Anything not listed above (intron_variant, intergenic, upstream, downstream, etc.)
# is classified as MODIFIER.

_TIER_ORDER = {"HIGH": 0, "MODERATE": 1, "LOW": 2, "MODIFIER": 3}

# 1 Mb bin size in base pairs.
BIN_SIZE = 1_000_000


class DensityBin(BaseModel):
    """Single genomic bin in the density histogram."""

    chrom: str
    bin_start: int
    bin_end: int
    high: int = 0
    moderate: int = 0
    low: int = 0
    modifier: int = 0
    total: int = 0


class DensityResponse(BaseModel):
    """Variant density per 1 Mb bin, colored by consequence tier (P2-23)."""

    bins: list[DensityBin]
    bin_size: int = BIN_SIZE


def _consequence_to_tier(consequence: str | None) -> str:
    """Map a VEP SO consequence term to its impact tier."""
    if not consequence:
        return "MODIFIER"
    return _CONSEQUENCE_TIER.get(consequence, "MODIFIER")


@router.get("/density")
def variant_density(
    sample_id: int = Query(..., description="Sample ID"),
) -> DensityResponse:
    """Return variant counts per 1 Mb genomic bin, grouped by consequence tier.

    Bins are computed as ``pos // 1_000_000 * 1_000_000``. Each bin carries
    counts for HIGH / MODERATE / LOW / MODIFIER consequence tiers plus total.
    Only returns bins that contain at least one variant.
    """
    sample_engine = _get_sample_engine(sample_id)
    table = _select_table(sample_engine)

    # SQL-level aggregation: GROUP BY (chrom, bin_start, consequence).
    # For raw_variants (no consequence column), everything is MODIFIER.
    has_consequence = hasattr(table.c, "consequence")

    bin_expr = sa.func.floor(table.c.pos / BIN_SIZE).cast(sa.Integer) * BIN_SIZE

    if has_consequence:
        query = (
            sa.select(
                table.c.chrom,
                bin_expr.label("bin_start"),
                table.c.consequence,
                sa.func.count().label("cnt"),
            )
            .select_from(table)
            .group_by(table.c.chrom, bin_expr, table.c.consequence)
        )
    else:
        query = (
            sa.select(
                table.c.chrom,
                bin_expr.label("bin_start"),
                sa.literal(None).label("consequence"),
                sa.func.count().label("cnt"),
            )
            .select_from(table)
            .group_by(table.c.chrom, bin_expr)
        )

    with sample_engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    # Accumulate into bins keyed by (chrom, bin_start).
    bin_map: dict[tuple[str, int], DensityBin] = {}
    for row in rows:
        key = (row.chrom, row.bin_start)
        if key not in bin_map:
            bin_map[key] = DensityBin(
                chrom=row.chrom,
                bin_start=row.bin_start,
                bin_end=row.bin_start + BIN_SIZE,
            )
        b = bin_map[key]
        tier = _consequence_to_tier(row.consequence)
        tier_lower = tier.lower()
        setattr(b, tier_lower, getattr(b, tier_lower) + row.cnt)
        b.total += row.cnt

    # Sort bins by canonical chrom order then bin_start.
    bins = sorted(
        bin_map.values(),
        key=lambda b: (_chrom_sort_key(b.chrom), b.bin_start),
    )

    return DensityResponse(bins=bins)


# ── Consequence summary (P2-25) ──────────────────────────────────


class ConsequenceCount(BaseModel):
    """Single consequence type with its count and tier."""

    consequence: str
    count: int
    tier: str


class ConsequenceSummaryResponse(BaseModel):
    """Per-consequence-type variant counts for the donut chart (P2-25)."""

    items: list[ConsequenceCount]
    total: int


@router.get("/consequence-summary")
def consequence_summary(
    sample_id: int = Query(..., description="Sample ID"),
) -> ConsequenceSummaryResponse:
    """Return variant counts grouped by VEP consequence type.

    Each item includes the SO consequence term, its count, and impact tier
    (HIGH / MODERATE / LOW / MODIFIER). Used by the consequence donut chart.
    Results are sorted by count descending.
    """
    sample_engine = _get_sample_engine(sample_id)
    table = _select_table(sample_engine)

    has_consequence = hasattr(table.c, "consequence")

    if has_consequence:
        query = (
            sa.select(
                table.c.consequence,
                sa.func.count().label("cnt"),
            )
            .select_from(table)
            .group_by(table.c.consequence)
        )
    else:
        # raw_variants: no consequence column, everything is unknown
        query = sa.select(
            sa.literal(None).label("consequence"),
            sa.func.count().label("cnt"),
        ).select_from(table)

    with sample_engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    items: list[ConsequenceCount] = []
    total = 0
    for row in rows:
        consequence = row.consequence or "unknown"
        tier = _consequence_to_tier(row.consequence)
        items.append(ConsequenceCount(consequence=consequence, count=row.cnt, tier=tier))
        total += row.cnt

    # Sort by count descending
    items.sort(key=lambda x: x.count, reverse=True)

    return ConsequenceSummaryResponse(items=items, total=total)


# ── ClinVar significance breakdown (P2-26) ──────────────────────


class ClinvarSignificanceCount(BaseModel):
    """Single ClinVar significance category with its count."""

    significance: str
    count: int


class ClinvarSummaryResponse(BaseModel):
    """ClinVar significance breakdown for bar chart (P2-26)."""

    items: list[ClinvarSignificanceCount]
    total: int


@router.get("/clinvar-summary")
def clinvar_summary(
    sample_id: int = Query(..., description="Sample ID"),
) -> ClinvarSummaryResponse:
    """Return variant counts grouped by ClinVar significance.

    Each item includes the ClinVar significance string and its count.
    Used by the ClinVar significance breakdown bar chart.
    Results are sorted by count descending.
    """
    sample_engine = _get_sample_engine(sample_id)
    table = _select_table(sample_engine)

    has_clinvar = hasattr(table.c, "clinvar_significance")

    if not has_clinvar:
        # raw_variants table has no clinvar column
        return ClinvarSummaryResponse(items=[], total=0)

    query = (
        sa.select(
            table.c.clinvar_significance,
            sa.func.count().label("cnt"),
        )
        .select_from(table)
        .where(table.c.clinvar_significance.isnot(None))
        .group_by(table.c.clinvar_significance)
    )

    with sample_engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    items: list[ClinvarSignificanceCount] = []
    total = 0
    for row in rows:
        items.append(
            ClinvarSignificanceCount(
                significance=row.clinvar_significance,
                count=row.cnt,
            )
        )
        total += row.cnt

    # Sort by count descending
    items.sort(key=lambda x: x.count, reverse=True)

    return ClinvarSummaryResponse(items=items, total=total)


# ── Variant search (P4-26e) ──────────────────────────────────────


class VariantSearchResult(BaseModel):
    """Lightweight result for the command palette search."""

    rsid: str
    chrom: str
    pos: int
    gene_symbol: str | None = None
    clinvar_significance: str | None = None


def _escape_like(value: str) -> str:
    """Escape SQL LIKE metacharacters so they are treated as literals."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Gene symbol pattern: starts with a letter, then letters/digits/hyphens.
_GENE_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9-]{0,19}$")


@router.get("/search")
def search_variants(
    sample_id: int = Query(
        ...,
        description="Sample ID to search variants for",
    ),
    q: str = Query(
        ...,
        min_length=1,
        max_length=100,
        description="Search query (rsid prefix or gene symbol)",
    ),
    limit: int = Query(10, ge=1, le=50, description="Max results"),
) -> list[VariantSearchResult]:
    """Search variants by rsid prefix or gene symbol for the command palette.

    Returns a lightweight list of matching variants (max ``limit``).
    Supports prefix matching on rsid (e.g., "rs429") and exact gene symbol
    matching (e.g., "BRCA1", "HLA-A").
    """
    sample_engine = _get_sample_engine(sample_id)
    table = _select_table(sample_engine)

    q_stripped = q.strip()
    if not q_stripped:
        return []

    has_gene = hasattr(table.c, "gene_symbol")
    has_clinvar = hasattr(table.c, "clinvar_significance")

    # Build select columns
    cols = [table.c.rsid, table.c.chrom, table.c.pos]
    if has_gene:
        cols.append(table.c.gene_symbol)
    if has_clinvar:
        cols.append(table.c.clinvar_significance)

    escaped = _escape_like(q_stripped)

    # rsid prefix search (e.g., "rs429")
    if q_stripped.lower().startswith("rs"):
        query = (
            sa.select(*cols)
            .select_from(table)
            .where(table.c.rsid.like(f"{escaped}%", escape="\\"))
            .order_by(table.c.rsid)
            .limit(limit)
        )
    elif has_gene and _GENE_SYMBOL_RE.match(q_stripped.upper()):
        # Gene symbol exact match (e.g., "BRCA1", "HLA-A")
        query = (
            sa.select(*cols)
            .select_from(table)
            .where(table.c.gene_symbol == q_stripped.upper())
            .order_by(*_build_order_by(table))
            .limit(limit)
        )
    else:
        # General rsid prefix search
        query = (
            sa.select(*cols)
            .select_from(table)
            .where(table.c.rsid.like(f"{escaped}%", escape="\\"))
            .order_by(table.c.rsid)
            .limit(limit)
        )

    with sample_engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    results = []
    for row in rows:
        results.append(
            VariantSearchResult(
                rsid=row.rsid,
                chrom=row.chrom,
                pos=row.pos,
                gene_symbol=getattr(row, "gene_symbol", None) if has_gene else None,
                clinvar_significance=(
                    getattr(row, "clinvar_significance", None) if has_clinvar else None
                ),
            )
        )
    return results
