"""Export results API endpoints (P4-05, P4-12a).

POST /api/export/query  — Export query builder results as VCF/TSV/JSON/CSV.
POST /api/export/sql    — Export raw SQL console results as TSV/JSON/CSV.
POST /api/export/fhir   — Export FHIR R4 Bundle (DiagnosticReport + Observations).

All formats use StreamingResponse to avoid holding large result sets in
memory.  Content-Disposition headers trigger a download in browsers.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import annotated_variants, samples
from backend.ingestion.vcf_export import export_vcf_from_rows
from backend.query.translator import TranslationError, translate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/export", tags=["export"])

# ── Constants ────────────────────────────────────────────────────────

# Canonical chromosome sort order.
_CHROM_ORDER: dict[str, int] = {
    **{str(i): i for i in range(1, 23)},
    "X": 23,
    "Y": 24,
    "MT": 25,
}

# Maximum rows for SQL export (10x the console limit).
SQL_EXPORT_MAX_ROWS = 10_000

# SQL console timeout in seconds.
SQL_EXPORT_TIMEOUT = 60

# Regex for write-operation detection (same as query_builder.py).
_WRITE_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|REINDEX"
    r"|ATTACH|DETACH|VACUUM|PRAGMA\s+\w+\s*=)"
    r"\b",
    re.IGNORECASE,
)

# Content-type mapping.
_CONTENT_TYPES: dict[str, str] = {
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "json": "application/json",
    "vcf": "text/plain",
}


# ── Request models ───────────────────────────────────────────────────


class RuleModel(BaseModel):
    """Single rule from react-querybuilder."""

    field: str
    operator: str
    value: Any = None
    disabled: bool | None = None


class RuleGroupModel(BaseModel):
    """Recursive rule group from react-querybuilder (RuleGroupType)."""

    combinator: str = "and"
    rules: list[RuleGroupModel | RuleModel | dict] = Field(default_factory=list)
    not_: bool | None = Field(default=None, alias="not")

    model_config = {"populate_by_name": True}


class ExportQueryRequest(BaseModel):
    """POST /api/export/query request body."""

    sample_id: int
    filter: RuleGroupModel
    format: Literal["vcf", "tsv", "json", "csv"]


class ExportSqlRequest(BaseModel):
    """POST /api/export/sql request body."""

    sample_id: int
    sql: str = Field(..., min_length=1, max_length=10_000)
    format: Literal["tsv", "json", "csv"]


class ExportFhirRequest(BaseModel):
    """POST /api/export/fhir request body."""

    sample_id: int
    include_all: bool = Field(
        True,
        description=(
            "If true, include all annotated variants. "
            "If false, only include variants with ClinVar annotations."
        ),
    )


# ── Helpers ──────────────────────────────────────────────────────────


def _resolve_sample_db_path(sample_id: int) -> Path:
    """Resolve sample_id to the absolute Path for the sample DB file."""
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
    return sample_db_path


def _get_sample_engine(sample_id: int) -> sa.Engine:
    """Resolve sample_id to a per-sample DB engine."""
    sample_db_path = _resolve_sample_db_path(sample_id)
    registry = get_registry()
    return registry.get_sample_engine(sample_db_path)


def _has_annotated_variants(engine: sa.Engine) -> bool:
    """Check if annotated_variants table has data."""
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(sa.literal(1)).select_from(annotated_variants).limit(1)
        ).fetchone()
    return row is not None


def _chrom_order_expr() -> sa.Case:
    """CASE expression for canonical chromosome ordering."""
    return sa.case(
        *[(annotated_variants.c.chrom == k, v) for k, v in _CHROM_ORDER.items()],
        else_=99,
    )


def _row_to_dict(row: sa.Row) -> dict[str, Any]:
    """Convert a Row to a dict with all annotated_variants columns."""
    return {col.name: getattr(row, col.name, None) for col in annotated_variants.columns}


def _validate_read_only(sql: str) -> None:
    """Raise HTTPException if the SQL contains write operations."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        raise HTTPException(status_code=422, detail="Empty SQL statement.")

    if _WRITE_PATTERN.search(stripped):
        raise HTTPException(
            status_code=403,
            detail=(
                "Write operations are not allowed. "
                "Only SELECT and read-only statements are permitted."
            ),
        )


def _make_filename(ext: str) -> str:
    """Generate an export filename with a timestamp."""
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"yeliztli_export_{ts}.{ext}"


# ── Streaming generators ─────────────────────────────────────────────


def _stream_csv_from_columns(
    columns: list[str],
    rows: list[list[Any]],
    delimiter: str = ",",
):
    """Yield CSV/TSV content from column names + row arrays (SQL results)."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter)
    writer.writerow(columns)
    yield buf.getvalue()

    for row in rows:
        buf.seek(0)
        buf.truncate()
        writer.writerow(row)
        yield buf.getvalue()


def _stream_csv_iter(rows_iter, delimiter: str = ","):
    """Yield CSV/TSV from an iterator of dicts, streaming row by row."""
    buf = io.StringIO()
    first = True
    fieldnames = None
    writer = None
    for row in rows_iter:
        if first:
            fieldnames = list(row.keys())
            writer = csv.DictWriter(buf, fieldnames=fieldnames, delimiter=delimiter)
            writer.writeheader()
            yield buf.getvalue()
            first = False
        buf.seek(0)
        buf.truncate()
        writer.writerow(row)
        yield buf.getvalue()
    if first:
        # No rows — yield header only
        fieldnames = [col.name for col in annotated_variants.columns]
        writer = csv.DictWriter(buf, fieldnames=fieldnames, delimiter=delimiter)
        writer.writeheader()
        yield buf.getvalue()


def _stream_json_iter(rows_iter):
    """Yield JSON array from an iterator of dicts."""
    yield "["
    first = True
    for row in rows_iter:
        if not first:
            yield ","
        yield json.dumps(row, default=str)
        first = False
    yield "]"


def _stream_json_from_columns(columns: list[str], rows: list[list[Any]]):
    """Yield JSON array content from column names + row arrays."""
    yield "["
    for i, row in enumerate(rows):
        if i > 0:
            yield ","
        obj = dict(zip(columns, row))
        yield json.dumps(obj, default=str)
    yield "]"


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("/query")
def export_query(body: ExportQueryRequest) -> StreamingResponse:
    """Export query builder results in the requested format.

    Executes the filter tree against annotated_variants WITHOUT pagination
    (fetches all matching rows) and streams the result in the chosen format.
    """
    require_fresh_sample(body.sample_id)
    sample_engine = _get_sample_engine(body.sample_id)

    if not _has_annotated_variants(sample_engine):
        raise HTTPException(
            status_code=422,
            detail="Sample has no annotated variants. Run annotation first.",
        )

    # Translate filter tree
    try:
        filter_tree = body.filter.model_dump(by_alias=True)
        where_clause = translate(filter_tree)
    except TranslationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Build query — NO pagination, fetch all
    query = sa.select(annotated_variants).where(where_clause)
    chrom_expr = _chrom_order_expr()
    query = query.order_by(chrom_expr.asc(), annotated_variants.c.pos.asc())

    ext = body.format
    filename = _make_filename(ext)
    content_type = _CONTENT_TYPES[ext]

    if ext == "vcf":
        # VCF export needs all rows in memory (export_vcf_from_rows sorts).
        with sample_engine.connect() as conn:
            rows_raw = conn.execute(query).fetchall()
        rows = [_row_to_dict(r) for r in rows_raw]
        # Thread the annotation-resolved ref/alt/zygosity through so the VCF is
        # emitted reference-aligned (GT indexed against the reference allele),
        # not inferred from the raw genotype string — a true hom-alt call must
        # not be written as hom-ref 0/0 (#560, same class as #471).
        variants = [
            (
                r["rsid"],
                r["chrom"],
                r["pos"],
                r.get("genotype") or "",
                r.get("ref"),
                r.get("alt"),
                r.get("zygosity"),
            )
            for r in rows
        ]
        vcf_content = export_vcf_from_rows(variants)
        return StreamingResponse(
            iter([vcf_content]),
            media_type=content_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    # CSV/TSV/JSON — stream from cursor in batches to avoid OOM.
    def _generate_rows():
        with sample_engine.connect() as conn:
            result = conn.execute(query)
            while True:
                batch = result.fetchmany(5000)
                if not batch:
                    break
                for r in batch:
                    yield _row_to_dict(r)

    if ext == "json":
        return StreamingResponse(
            _stream_json_iter(_generate_rows()),
            media_type=content_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    else:
        delimiter = "\t" if ext == "tsv" else ","
        return StreamingResponse(
            _stream_csv_iter(_generate_rows(), delimiter=delimiter),
            media_type=content_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )


@router.post("/sql")
def export_sql(body: ExportSqlRequest) -> StreamingResponse:
    """Export raw SQL console results in the requested format.

    Executes user-provided SQL against a read-only SQLite connection
    and streams the result. Maximum of 10,000 rows.
    VCF is not supported for SQL exports (arbitrary column schemas).
    """
    require_fresh_sample(body.sample_id)
    _validate_read_only(body.sql)

    db_path = str(_resolve_sample_db_path(body.sample_id))

    deadline = time.monotonic() + SQL_EXPORT_TIMEOUT

    def _progress_handler() -> int:
        return 1 if time.monotonic() >= deadline else 0

    ro_engine = sa.create_engine(
        "sqlite://",
        creator=lambda: sqlite3.connect(f"file:{db_path}?mode=ro", uri=True),
    )

    try:
        with ro_engine.connect() as conn:
            raw_conn = conn.connection.dbapi_connection
            raw_conn.set_progress_handler(_progress_handler, 10_000)
            try:
                result = conn.execute(sa.text(body.sql))

                if result.returns_rows:
                    columns = [col[0] for col in result.cursor.description]
                    rows_raw = result.fetchmany(SQL_EXPORT_MAX_ROWS)
                    rows = [list(r) for r in rows_raw]
                else:
                    columns = []
                    rows = []
            finally:
                raw_conn.set_progress_handler(None, 0)
    except sa.exc.OperationalError as exc:
        msg = str(exc.orig) if exc.orig else str(exc)
        if "interrupted" in msg.lower():
            raise HTTPException(
                status_code=408,
                detail=f"Query timed out after {SQL_EXPORT_TIMEOUT} seconds.",
            ) from exc
        raise HTTPException(status_code=422, detail=f"SQL error: {msg}") from exc
    finally:
        ro_engine.dispose()

    ext = body.format
    filename = _make_filename(ext)
    content_type = _CONTENT_TYPES[ext]

    if ext == "json":
        return StreamingResponse(
            _stream_json_from_columns(columns, rows),
            media_type=content_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    else:
        delimiter = "\t" if ext == "tsv" else ","
        return StreamingResponse(
            _stream_csv_from_columns(columns, rows, delimiter=delimiter),
            media_type=content_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )


# ── FHIR R4 export (P4-12a) ────────────────────────────────────────


@router.post("/fhir")
def export_fhir(body: ExportFhirRequest) -> StreamingResponse:
    """Export a FHIR R4 Bundle (DiagnosticReport + Observations).

    Returns a FHIR R4 Bundle as JSON containing one DiagnosticReport and
    one Observation per annotated variant.  Scope is limited to genomic
    core resources — no Condition or MedicationStatement (R-17 mitigation).
    """
    require_fresh_sample(body.sample_id)
    from backend.reports.fhir_export import build_fhir_bundle

    try:
        bundle = build_fhir_bundle(
            sample_id=body.sample_id,
            include_all=body.include_all,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    filename = _make_filename("fhir.json")
    content = json.dumps(bundle, indent=2, default=str)

    return StreamingResponse(
        iter([content]),
        media_type="application/fhir+json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
