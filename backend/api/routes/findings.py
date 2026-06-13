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

from backend.api.dependencies import require_fresh_sample
from backend.api.gating import is_apoe_gate_acknowledged
from backend.db.connection import get_registry
from backend.db.tables import findings, samples

logger = logging.getLogger(__name__)

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


class FindingSummaryItem(BaseModel):
    """Per-module finding count and top evidence level."""

    module: str
    count: int
    max_evidence_level: int | None = None
    top_finding_text: str | None = None


class FindingsSummaryResponse(BaseModel):
    """Summary of findings across all modules."""

    total_findings: int
    modules: list[FindingSummaryItem]
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


def _row_to_response(row: sa.Row) -> FindingResponse:
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
        finding_text=row.finding_text,
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


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("", response_model=list[FindingResponse])
async def list_findings(
    sample_id: int = Query(..., description="Sample ID"),
    module: str | None = Query(None, description="Filter by module"),
    category: str | None = Query(None, description="Filter by category"),
    min_stars: int | None = Query(None, ge=1, le=4, description="Minimum evidence level"),
) -> list[FindingResponse]:
    """List all findings for a sample, optionally filtered."""
    engine = _get_sample_engine(sample_id)

    clauses = []
    if module:
        clauses.append(findings.c.module == module)
    if category:
        clauses.append(findings.c.category == category)
    if min_stars is not None:
        clauses.append(findings.c.evidence_level >= min_stars)

    # APOE ε4 is the Alzheimer-risk disclosure the opt-in gate exists to protect.
    # The dedicated APOE routes gate it; this module-agnostic aggregator must too,
    # or it re-opens the disclosure via a side route — leaking the diplotype and
    # the full clinical narrative (issue #222). Withhold module="apoe" findings
    # until the gate is acknowledged. With an explicit module=apoe filter this
    # yields an empty list pre-acknowledgment (not a 403, which would itself
    # confirm APOE data exists).
    if not is_apoe_gate_acknowledged(engine):
        clauses.append(findings.c.module != "apoe")

    stmt = sa.select(findings)
    if clauses:
        stmt = stmt.where(sa.and_(*clauses))

    # Sort by evidence level descending (highest first), then module
    stmt = stmt.order_by(
        sa.desc(sa.func.coalesce(findings.c.evidence_level, 0)),
        findings.c.module,
        findings.c.id,
    )

    with engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()

    return [_row_to_response(r) for r in rows]


@router.get("/summary", response_model=FindingsSummaryResponse)
async def findings_summary(
    sample_id: int = Query(..., description="Sample ID"),
) -> FindingsSummaryResponse:
    """Per-module finding summary with counts and top findings."""
    engine = _get_sample_engine(sample_id)

    # Mirror list_findings: the APOE disclosure gate also covers this summary,
    # whose per-module counts, top_finding_text, and high_confidence_findings
    # would otherwise surface the APOE diplotype/Alzheimer's narrative pre-gate
    # (issue #222). Drop module="apoe" from every query until acknowledged.
    apoe_visible = is_apoe_gate_acknowledged(engine)

    with engine.connect() as conn:
        # Per-module aggregation
        agg_stmt = (
            sa.select(
                findings.c.module,
                sa.func.count().label("cnt"),
                sa.func.max(findings.c.evidence_level).label("max_ev"),
            )
            .group_by(findings.c.module)
            .order_by(sa.desc("max_ev"))
        )
        if not apoe_visible:
            agg_stmt = agg_stmt.where(findings.c.module != "apoe")
        agg_rows = conn.execute(agg_stmt).fetchall()

        # All findings for top finding per module
        all_stmt = sa.select(findings).order_by(
            sa.desc(sa.func.coalesce(findings.c.evidence_level, 0)),
            findings.c.module,
        )
        if not apoe_visible:
            all_stmt = all_stmt.where(findings.c.module != "apoe")
        all_rows = conn.execute(all_stmt).fetchall()

    # Build per-module summary
    total = 0
    modules: list[FindingSummaryItem] = []
    # Index findings by module for top finding lookup
    top_by_module: dict[str, str] = {}
    for r in all_rows:
        if r.module not in top_by_module:
            top_by_module[r.module] = r.finding_text

    for agg in agg_rows:
        total += agg.cnt
        modules.append(
            FindingSummaryItem(
                module=agg.module,
                count=agg.cnt,
                max_evidence_level=agg.max_ev,
                top_finding_text=top_by_module.get(agg.module),
            )
        )

    # High-confidence: top 5 findings with >=3 stars
    high_conf = [_row_to_response(r) for r in all_rows if (r.evidence_level or 0) >= 3][:5]

    return FindingsSummaryResponse(
        total_findings=total,
        modules=modules,
        high_confidence_findings=high_conf,
    )


@router.get("/{finding_id}/svg")
async def get_finding_svg(
    finding_id: int,
    sample_id: int = Query(..., description="Sample ID"),
) -> Response:
    """Return the pre-rendered SVG for a finding."""
    engine, sample_dir = _get_sample_engine_and_dir(sample_id)

    with engine.connect() as conn:
        row = conn.execute(
            sa.select(findings.c.module, findings.c.svg_path).where(findings.c.id == finding_id)
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Finding not found")

    # The APOE SVG card renders the ε4 diplotype and an Alzheimer-risk label — the
    # same disclosure list_findings/summary withhold (issue #222). Finding ids are
    # small, enumerable integers, so without this check an APOE card leaks via this
    # by-id side route. 404 (not 403) to match the no-leak posture used elsewhere:
    # do not confirm an APOE finding exists before the gate is acknowledged.
    if row.module == "apoe" and not is_apoe_gate_acknowledged(engine):
        raise HTTPException(status_code=404, detail="Finding not found")

    svg_path_str = row.svg_path
    if not svg_path_str:
        raise HTTPException(status_code=404, detail="No SVG available for this finding")

    # svg_path is stored relative to the sample directory for portability
    svg_file = sample_dir / svg_path_str
    if not svg_file.exists():
        raise HTTPException(status_code=404, detail="SVG file not found on disk")

    svg_content = svg_file.read_text(encoding="utf-8")
    return Response(content=svg_content, media_type="image/svg+xml")
