"""Shared FastAPI plumbing for risk-genotype modules.

The expansion-wave risk modules (thrombophilia, alpha-1, AMD, APOL1, gout) all
read from the unified ``findings`` table with ``category='risk_genotype'`` and
expose the same disclaimer / findings / run surface. :func:`make_risk_router`
builds that router from a module name, its disclaimer text, and a runner
callable, so each module's route file stays a few lines.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import findings, samples

logger = logging.getLogger(__name__)


class RiskFindingResponse(BaseModel):
    """A single risk-genotype finding (uniform across risk modules)."""

    rsid: str
    gene_symbol: str
    risk_classification: str
    zygosity: str | None = None
    evidence_level: int = 1
    finding_text: str
    genotype_calls: dict[str, str | None] = {}
    odds_ratio: str | None = None
    penetrance_text: str = ""
    absolute_risk_context: str | None = None
    caveats: list[str] = []
    indeterminate_loci: list[str] = []
    indeterminate_reasons: dict[str, str] = {}
    phase_inferred: bool = False
    call_confidence: str | None = None
    confidence_note: str | None = None
    pmids: list[str] = []


class RiskFindingsListResponse(BaseModel):
    items: list[RiskFindingResponse]
    total: int


class RiskDisclaimerResponse(BaseModel):
    title: str
    text: str


class RiskRunResponse(BaseModel):
    findings_count: int
    indeterminate_loci: list[str]
    indeterminate_reasons: dict[str, str] = {}


def resolve_sample_engine(sample_id: int) -> sa.Engine:
    """Resolve a sample_id to its per-sample DB engine (or 404)."""
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


def fetch_risk_findings(sample_engine: sa.Engine, module: str) -> list[dict[str, Any]]:
    """Read stored risk-genotype findings for ``module`` into response dicts."""
    with sample_engine.connect() as conn:
        stmt = (
            sa.select(findings)
            .where(
                findings.c.module == module,
                findings.c.category == "risk_genotype",
            )
            .order_by(findings.c.evidence_level.desc())
        )
        rows = conn.execute(stmt).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        detail: dict[str, Any] = {}
        if row.detail_json:
            try:
                detail = json.loads(row.detail_json)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Failed to parse detail_json for finding id=%s", row.id)
        pmids: list[str] = []
        if row.pmid_citations:
            try:
                pmids = json.loads(row.pmid_citations)
            except (json.JSONDecodeError, TypeError):
                pmids = []
        result.append(
            {
                "rsid": row.rsid or "",
                "gene_symbol": row.gene_symbol or "",
                "risk_classification": row.conditions or "",
                "zygosity": row.zygosity,
                "evidence_level": row.evidence_level or 1,
                "finding_text": row.finding_text or "",
                "genotype_calls": detail.get("genotype_calls", {}),
                "odds_ratio": detail.get("odds_ratio"),
                "penetrance_text": detail.get("penetrance_text", ""),
                "absolute_risk_context": detail.get("absolute_risk_context"),
                "caveats": detail.get("caveats", []),
                "indeterminate_loci": detail.get("indeterminate_loci", []),
                "indeterminate_reasons": detail.get("indeterminate_reasons", {}),
                "phase_inferred": detail.get("phase_inferred", False),
                "call_confidence": detail.get("call_confidence"),
                "confidence_note": detail.get("confidence_note"),
                "pmids": pmids,
            }
        )
    return result


def make_risk_router(
    *,
    module: str,
    prefix: str,
    tags: list[str],
    disclaimer_title: str,
    disclaimer_text: str,
    runner: Callable[[sa.Engine], tuple[int, list[str]] | tuple[int, list[str], dict[str, str]]],
) -> APIRouter:
    """Build a disclaimer / findings / run router for a risk-genotype module.

    ``runner(sample_engine)`` performs the load → assess → store for the module
    and returns either ``(findings_count, indeterminate_loci)`` or
    ``(findings_count, indeterminate_loci, indeterminate_reasons)``.
    """
    router = APIRouter(prefix=prefix, tags=tags)

    @router.get("/disclaimer")
    def get_disclaimer() -> RiskDisclaimerResponse:
        return RiskDisclaimerResponse(title=disclaimer_title, text=disclaimer_text)

    @router.get("/findings", dependencies=[Depends(require_fresh_sample)])
    def list_findings(
        sample_id: int = Query(..., description="Sample ID"),
    ) -> RiskFindingsListResponse:
        engine = resolve_sample_engine(sample_id)
        raw = fetch_risk_findings(engine, module)
        items = [RiskFindingResponse(**f) for f in raw]
        return RiskFindingsListResponse(items=items, total=len(items))

    @router.post("/run", dependencies=[Depends(require_fresh_sample)])
    def run(sample_id: int = Query(..., description="Sample ID")) -> RiskRunResponse:
        engine = resolve_sample_engine(sample_id)
        result = runner(engine)
        if len(result) == 3:
            count, indeterminate, indeterminate_reasons = result
        elif len(result) == 2:
            count, indeterminate = result
            indeterminate_reasons = {}
        else:
            raise HTTPException(
                status_code=500,
                detail="Invalid runner response shape; expected 2 or 3 tuple items.",
            )
        return RiskRunResponse(
            findings_count=count,
            indeterminate_loci=indeterminate,
            indeterminate_reasons=indeterminate_reasons,
        )

    return router
