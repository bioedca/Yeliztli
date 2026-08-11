"""Unified findings API (P3-39).

Aggregates findings from all analysis modules stored in the per-sample
``findings`` table.  Supports filtering by module, evidence level, and
category.  Returns findings sorted by evidence level (highest first).

GET  /api/analysis/findings?sample_id=N                   — All findings
GET  /api/analysis/findings?sample_id=N&module=cancer     — By module
GET  /api/analysis/findings?sample_id=N&min_stars=3       — High-evidence
GET  /api/analysis/findings/summary?sample_id=N           — Per-module counts
GET  /api/analysis/findings/{finding_id}/svg?sample_id=N  — SVG image
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from backend.analysis.cross_module_links import normalize_cross_module_row
from backend.analysis.pharmacogenomics import (
    is_patient_presentable_finding_payload,
    is_patient_presentable_response_payload,
    patient_visible_finding_clause,
)
from backend.analysis.roh import normalize_legacy_finding_text, normalize_legacy_row
from backend.analysis.svg_renderer import is_safe_svg_marker, render_finding_svg
from backend.api.dependencies import require_fresh_sample
from backend.api.gating import gated_modules_to_hide
from backend.db.connection import get_registry
from backend.db.tables import findings, samples
from backend.services.lai_production_coverage import policy_qualified_finding_clause

logger = logging.getLogger(__name__)

_PAYLOAD_FILTER_BATCH_SIZE = 500


router = APIRouter(
    prefix="/analysis/findings",
    tags=["findings"],
    dependencies=[Depends(require_fresh_sample)],
)


# ── Response models ──────────────────────────────────────────────────


class FindingResponse(BaseModel):
    """A single finding from any analysis module."""

    id: int
    module: str
    category: str | None = None
    evidence_level: int | None = None
    gene_symbol: str | None = None
    rsid: str | None = None
    finding_text: str
    phenotype: str | None = None
    conditions: str | None = None
    zygosity: str | None = None
    clinvar_significance: str | None = None
    diplotype: str | None = None
    metabolizer_status: str | None = None
    drug: str | None = None
    haplogroup: str | None = None
    prs_score: float | None = None
    prs_percentile: float | None = None
    pathway: str | None = None
    pathway_level: str | None = None
    svg_path: str | None = None
    pmid_citations: list[str] = []
    detail: dict | None = None
    provenance: dict | None = None
    related_module: str | None = None
    related_finding_id: int | None = None
    created_at: str | None = None


class FindingEvidenceLevelCount(BaseModel):
    """True finding count for one normalized evidence level."""

    evidence_level: int
    count: int


class FindingSummaryItem(BaseModel):
    """Per-module finding count and top evidence level."""

    module: str
    count: int
    max_evidence_level: int | None = None
    top_finding_text: str | None = None
    evidence_level_counts: list[FindingEvidenceLevelCount]


class FindingsSummaryResponse(BaseModel):
    """Summary of findings across all modules."""

    total_findings: int
    modules: list[FindingSummaryItem]
    evidence_level_counts: list[FindingEvidenceLevelCount]
    high_confidence_findings: list[FindingResponse]


# ── Helpers ──────────────────────────────────────────────────────────


def _get_sample_engine_and_dir(sample_id: int) -> tuple[sa.Engine, Path]:
    """Look up sample and return its engine + sample directory."""
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Sample not found")
    sample_db_full = registry.settings.data_dir / row.db_path
    if not sample_db_full.exists():
        raise HTTPException(status_code=404, detail="Sample database file not found")
    return registry.get_sample_engine(sample_db_full), sample_db_full.parent


def _get_sample_engine(sample_id: int) -> sa.Engine:
    """Look up sample and return its engine."""
    engine, _ = _get_sample_engine_and_dir(sample_id)
    return engine


def _parse_detail_blob(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_live_cross_module_row(row: sa.Row) -> bool:
    """Whether a stored cross-module row still names a link the panel declares.

    The aggregator renders persisted rows, so a handoff retired from a panel
    would keep appearing here long after the module's own page stopped showing
    it — the generic-aggregator bypass this repository has hit before (#2021).
    """
    return (
        normalize_cross_module_row(
            row.module,
            row.category,
            row.rsid,
            row.finding_text,
            _parse_detail_blob(row.detail_json),
        )
        is not None
    )


def _is_patient_presentable_row(row: sa.Row) -> bool:
    """Apply the structured-payload presentation gate to one database row."""
    return is_patient_presentable_finding_payload(row._mapping) and _is_live_cross_module_row(row)


def _row_to_response(row: sa.Row, sample_engine: sa.Engine | None = None) -> FindingResponse:
    """Convert a findings table row to a FindingResponse."""
    pmids: list[str] = []
    raw_pmids = row.pmid_citations
    if raw_pmids:
        try:
            pmids = json.loads(raw_pmids)
        except (json.JSONDecodeError, TypeError):
            pass

    detail: dict | None = None
    raw_detail = row.detail_json
    if raw_detail:
        try:
            detail = json.loads(raw_detail)
        except (json.JSONDecodeError, TypeError):
            pass
    # A pre-gate ROH blob still carries the measured froh: 0.0 the narrative
    # withholds; correct both from ONE evaluability read, since evaluating
    # twice means two full autosomal scans for a legacy row (#2177).
    corrected_text, detail = normalize_legacy_row(
        row.module, row.category, row.finding_text, detail, sample_engine
    )
    # A cross-module row's target and note are panel data frozen at scoring
    # time; resolve them against the panel that is loaded (#2021). Retired
    # links are filtered out by `_is_patient_presentable_row` before this.
    resolved = normalize_cross_module_row(
        row.module, row.category, row.rsid, corrected_text, detail
    )
    if resolved is not None:
        corrected_text, detail = resolved

    provenance: dict | None = None
    raw_provenance = row.provenance
    if raw_provenance:
        try:
            provenance = json.loads(raw_provenance)
        except (json.JSONDecodeError, TypeError):
            pass

    created = None
    if row.created_at is not None:
        created = str(row.created_at)

    return FindingResponse(
        id=row.id,
        module=row.module,
        category=row.category,
        evidence_level=row.evidence_level,
        gene_symbol=row.gene_symbol,
        rsid=row.rsid,
        finding_text=corrected_text,
        phenotype=row.phenotype,
        conditions=row.conditions,
        zygosity=row.zygosity,
        clinvar_significance=row.clinvar_significance,
        diplotype=row.diplotype,
        metabolizer_status=row.metabolizer_status,
        drug=row.drug,
        haplogroup=row.haplogroup,
        prs_score=row.prs_score,
        prs_percentile=row.prs_percentile,
        pathway=row.pathway,
        pathway_level=row.pathway_level,
        svg_path=row.svg_path,
        pmid_citations=pmids,
        detail=detail,
        provenance=provenance,
        related_module=row.related_module,
        related_finding_id=row.related_finding_id,
        created_at=created,
    )


def _responses_are_patient_presentable(responses: list[FindingResponse]) -> bool:
    """Check an assembled findings collection without conflating null fields."""
    return is_patient_presentable_response_payload(
        [response.model_dump(mode="json", exclude_none=True) for response in responses]
    )


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("", response_model=list[FindingResponse])
# Declared sync, not async: these read a sample DB synchronously, and an ROH row
# triggers a coverage scan measured at 0.6-1.6 s for a dense array. On an
# `async def` path operation that runs ON the event loop and stalls every other
# request; as plain `def`, FastAPI offloads it to a threadpool.
def list_findings(
    sample_id: int = Query(..., description="Sample ID"),
    module: str | None = Query(None, description="Filter by module"),
    category: str | None = Query(None, description="Filter by category"),
    min_stars: int | None = Query(None, ge=1, le=4, description="Minimum evidence level"),
    limit: int | None = Query(
        None,
        ge=1,
        le=5000,
        description="Max findings to return (pagination). Omit for all (legacy).",
    ),
    offset: int = Query(0, ge=0, description="Pagination offset (used with limit)."),
) -> list[FindingResponse]:
    """List findings for a sample, optionally filtered and paginated.

    A typical sample has tens of thousands of rare-variant (evidence level 1)
    findings, so callers should pass ``limit`` to bound the response — findings
    are ordered highest-evidence-first, so a bounded page returns the most
    meaningful ones (#1303). ``limit`` omitted preserves the legacy
    return-everything behaviour for non-UI callers.
    """
    engine = _get_sample_engine(sample_id)

    clauses = [
        policy_qualified_finding_clause(findings.c.category),
        patient_visible_finding_clause(findings.c),
    ]
    if module:
        clauses.append(findings.c.module == module)
    if category:
        clauses.append(findings.c.category == category)
    if min_stars is not None:
        clauses.append(findings.c.evidence_level >= min_stars)

    # Withhold opt-in-gated modules (APOE #222, sex-aneuploidy #299) until each
    # gate is acknowledged — the dedicated module routes gate them, and this
    # module-agnostic aggregator must too or it re-opens the disclosure via a side
    # route (leaking e.g. the APOE diplotype or the possible-XXY screen text).
    # Even an explicit module=<gated> filter yields an empty list pre-ack (not a
    # 403, which would itself confirm the gated data exists).
    hidden_modules = gated_modules_to_hide(engine)
    if hidden_modules:
        clauses.append(findings.c.module.not_in(hidden_modules))

    stmt = sa.select(findings)
    if clauses:
        stmt = stmt.where(sa.and_(*clauses))

    # Sort by evidence level descending (highest first), then module
    stmt = stmt.order_by(
        sa.desc(sa.func.coalesce(findings.c.evidence_level, 0)),
        findings.c.module,
        findings.c.id,
    )

    if limit is None:
        with engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()
        response = [
            _row_to_response(row, engine) for row in rows if _is_patient_presentable_row(row)
        ]
        return response if _responses_are_patient_presentable(response) else []

    # Apply limit/offset after the payload gate. A raw SQL window can contain
    # quarantined legacy rows, so slicing it first would produce short pages and
    # make callers falsely infer that no later presentable rows exist.
    visible_rows: list[sa.Row] = []
    raw_offset = 0
    required_visible_rows = offset + limit
    with engine.connect() as conn:
        while len(visible_rows) < required_visible_rows:
            batch = conn.execute(
                stmt.limit(_PAYLOAD_FILTER_BATCH_SIZE).offset(raw_offset)
            ).fetchall()
            if not batch:
                break
            visible_rows.extend(row for row in batch if _is_patient_presentable_row(row))
            raw_offset += len(batch)
            if len(batch) < _PAYLOAD_FILTER_BATCH_SIZE:
                break

    page = visible_rows[offset:required_visible_rows]
    response = [_row_to_response(row, engine) for row in page]
    return response if _responses_are_patient_presentable(response) else []


@router.get("/summary", response_model=FindingsSummaryResponse)
def findings_summary(
    sample_id: int = Query(..., description="Sample ID"),
) -> FindingsSummaryResponse:
    """Per-module finding summary with counts and top findings."""
    engine = _get_sample_engine(sample_id)

    # Mirror list_findings: the opt-in disclosure gates also cover this summary,
    # whose per-module counts, top_finding_text, and high_confidence_findings
    # would otherwise surface a gated finding's narrative pre-gate (APOE #222,
    # sex-aneuploidy #299). Drop each unacknowledged gated module from every query.
    hidden_modules = gated_modules_to_hide(engine)

    with engine.connect() as conn:
        # The complete row set is already needed for each module's top finding.
        # Aggregate it below after the Python payload gate so counts cannot
        # disclose an unsafe legacy payload that SQL scalar columns cannot see.
        all_stmt = (
            sa.select(findings)
            .where(
                policy_qualified_finding_clause(findings.c.category),
                patient_visible_finding_clause(findings.c),
            )
            .order_by(
                sa.desc(sa.func.coalesce(findings.c.evidence_level, 0)),
                findings.c.module,
            )
        )
        if hidden_modules:
            all_stmt = all_stmt.where(findings.c.module.not_in(hidden_modules))
        all_rows = conn.execute(all_stmt).fetchall()

    all_rows = [row for row in all_rows if _is_patient_presentable_row(row)]

    # Build per-module and evidence summaries from the same payload-safe rows.
    module_counts: dict[str, int] = {}
    module_max_evidence: dict[str, int | None] = {}
    evidence_counts: dict[int, int] = {}
    evidence_counts_by_module: dict[str, dict[int, int]] = {}
    top_by_module: dict[str, str] = {}

    for r in all_rows:
        module = r.module
        module_counts[module] = module_counts.get(module, 0) + 1
        if (
            module not in module_max_evidence
            or r.evidence_level is not None
            and (
                module_max_evidence[module] is None
                or r.evidence_level > module_max_evidence[module]
            )
        ):
            module_max_evidence[module] = r.evidence_level

        level = int(r.evidence_level or 0)
        evidence_counts[level] = evidence_counts.get(level, 0) + 1
        per_module = evidence_counts_by_module.setdefault(module, {})
        per_module[level] = per_module.get(level, 0) + 1
        if r.module not in top_by_module:
            # ReportBuilder renders this preview, so an uncorrected pre-gate ROH
            # row would keep showing "typical result" on the module card while
            # the list and dedicated endpoints report the estimate withheld.
            detail_blob: dict | None = None
            if r.detail_json:
                try:
                    detail_blob = json.loads(r.detail_json)
                except (json.JSONDecodeError, TypeError):
                    detail_blob = None
            preview = normalize_legacy_finding_text(
                r.module, r.category, r.finding_text, detail_blob, engine
            )
            resolved_preview = normalize_cross_module_row(
                r.module, r.category, r.rsid, preview, detail_blob
            )
            if resolved_preview is not None:
                preview = resolved_preview[0]
            top_by_module[r.module] = preview

    modules = []
    for module in sorted(
        module_counts,
        key=lambda current: (-(module_max_evidence[current] or 0), current),
    ):
        modules.append(
            FindingSummaryItem(
                module=module,
                count=module_counts[module],
                max_evidence_level=module_max_evidence[module],
                top_finding_text=top_by_module.get(module),
                evidence_level_counts=[
                    FindingEvidenceLevelCount(evidence_level=level, count=count)
                    for level, count in sorted(
                        evidence_counts_by_module[module].items(), reverse=True
                    )
                ],
            )
        )

    # High-confidence: top 5 findings with >=3 stars
    high_conf = [_row_to_response(r, engine) for r in all_rows if (r.evidence_level or 0) >= 3][:5]

    response = FindingsSummaryResponse(
        total_findings=sum(module_counts.values()),
        modules=modules,
        evidence_level_counts=[
            FindingEvidenceLevelCount(evidence_level=level, count=count)
            for level, count in sorted(evidence_counts.items(), reverse=True)
        ],
        high_confidence_findings=high_conf,
    )
    if not is_patient_presentable_response_payload(
        response.model_dump(mode="json", exclude_none=True)
    ):
        return FindingsSummaryResponse(
            total_findings=0,
            modules=[],
            evidence_level_counts=[],
            high_confidence_findings=[],
        )
    return response


@router.get("/{finding_id}/svg")
def get_finding_svg(
    finding_id: int,
    sample_id: int = Query(..., description="Sample ID"),
) -> Response:
    """Return a freshly rendered SVG card for a presentable finding."""
    engine, _sample_dir = _get_sample_engine_and_dir(sample_id)

    with engine.connect() as conn:
        row = conn.execute(
            sa.select(findings).where(
                findings.c.id == finding_id,
                policy_qualified_finding_clause(findings.c.category),
                patient_visible_finding_clause(findings.c),
            )
        ).fetchone()

    if row is None or not _is_patient_presentable_row(row):
        raise HTTPException(status_code=404, detail="Finding not found")

    # A gated module's SVG card (e.g. the APOE ε4/Alzheimer card) renders the same
    # disclosure list_findings/summary withhold (#222, #299). Finding ids are
    # small, enumerable integers, so without this check a gated card leaks via this
    # by-id side route. 404 (not 403) to match the no-leak posture used elsewhere:
    # do not confirm a gated finding exists before its gate is acknowledged.
    if row.module in gated_modules_to_hide(engine):
        raise HTTPException(status_code=404, detail="Finding not found")

    svg_path_str = row.svg_path
    if not is_safe_svg_marker(svg_path_str):
        raise HTTPException(status_code=404, detail="No SVG available for this finding")

    # Persisted SVGs are untrusted legacy artifacts. Regenerate this card from
    # the already-gated structured finding instead of serving a mutable file.
    svg_content = render_finding_svg(dict(row._mapping))
    if svg_content is None:
        raise HTTPException(status_code=404, detail="No SVG available for this finding")
    return Response(content=svg_content, media_type="image/svg+xml")
