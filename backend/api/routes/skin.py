"""Gene Skin findings API (P3-56).

Categorical pathway scoring results — Elevated / Moderate / Standard
per skin pathway with MC1R multi-allele summary, FLG insufficient data
caveat, per-SNP drill-down, and cross-module links.

GET  /api/analysis/skin/pathways?sample_id=N            — All pathway results
GET  /api/analysis/skin/pathway/{pathway_id}?sample_id=N — Single pathway detail
POST /api/analysis/skin/run?sample_id=N                  — Run/re-run scoring
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import findings, samples

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/analysis/skin",
    tags=["skin"],
    dependencies=[Depends(require_fresh_sample)],
)


# ── Response models ──────────────────────────────────────────────────


class SNPDetail(BaseModel):
    """Per-SNP result within a pathway."""

    rsid: str
    gene: str
    variant_name: str
    genotype: str | None = None
    category: str  # Elevated / Moderate / Standard
    effect_summary: str
    evidence_level: int
    recommendation: str | None = None
    pmids: list[str] = []
    mc1r_allele_class: str | None = None
    coverage_note: str | None = None
    insufficient_data_flag: bool = False


class PathwaySummary(BaseModel):
    """Summary of a single skin pathway."""

    pathway_id: str
    pathway_name: str
    level: str  # Elevated / Moderate / Standard
    evidence_level: int
    called_snps: int
    total_snps: int
    missing_snps: list[str] = []
    no_call_snps: list[str] = []
    pmids: list[str] = []


class MC1RAggregateItem(BaseModel):
    """MC1R multi-allele aggregate result."""

    r_allele_count: int
    r_allele_rsids: list[str]
    total_mc1r_called: int
    risk_label: str
    risk_description: str
    evidence_level: int
    pmids: list[str] = []


class CrossModuleItem(BaseModel):
    """Cross-module reference finding (e.g. MC1R→Cancer, VDR→Nutrigenomics)."""

    rsid: str
    gene: str
    source_module: str
    target_module: str
    finding_text: str
    evidence_level: int
    pmids: list[str] = []


class InsufficientDataItem(BaseModel):
    """Insufficient data caveat for limited assay coverage."""

    rsid: str
    gene: str
    finding_text: str
    evidence_level: int
    pathway: str
    proxy_target: str | None = None
    reason: str | None = None
    pmids: list[str] = []


class PathwaysResponse(BaseModel):
    """All pathway results for a sample."""

    items: list[PathwaySummary]
    total: int
    mc1r_aggregate: MC1RAggregateItem | None = None
    cross_module: list[CrossModuleItem] = []
    insufficient_data: list[InsufficientDataItem] = []


class PathwayDetailResponse(BaseModel):
    """Full pathway detail with per-SNP breakdown."""

    pathway_id: str
    pathway_name: str
    level: str
    evidence_level: int
    called_snps: int
    total_snps: int
    missing_snps: list[str] = []
    no_call_snps: list[str] = []
    pmids: list[str] = []
    snp_details: list[SNPDetail] = []


class RunResponse(BaseModel):
    """Result of running skin scoring."""

    findings_count: int
    pathways_scored: int


# ── Helpers ──────────────────────────────────────────────────────────


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


def _fetch_skin_findings(
    sample_engine: sa.Engine,
) -> list[dict[str, Any]]:
    """Fetch all skin findings from the sample DB."""
    with sample_engine.connect() as conn:
        stmt = sa.select(findings).where(findings.c.module == "skin").order_by(findings.c.id)
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
                logger.warning("Failed to parse pmid_citations for finding id=%s", row.id)

        result.append(
            {
                "category": row.category,
                "evidence_level": row.evidence_level,
                "gene_symbol": row.gene_symbol,
                "rsid": row.rsid,
                "finding_text": row.finding_text,
                "pathway": row.pathway,
                "pathway_level": row.pathway_level,
                "pmids": pmids,
                "detail": detail,
            }
        )

    return result


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/pathways")
def list_pathways(
    sample_id: int = Query(..., description="Sample ID"),
) -> PathwaysResponse:
    """List all skin pathway results for a sample.

    Returns each of the four skin pathways with their categorical
    level (Elevated / Moderate / Standard), MC1R aggregate, cross-module
    findings, and FLG insufficient data caveats.

    Example: ``GET /api/analysis/skin/pathways?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)
    all_findings = _fetch_skin_findings(sample_engine)

    # Pathway summaries
    pathway_summaries = [f for f in all_findings if f["category"] == "pathway_summary"]

    items: list[PathwaySummary] = []
    for ps in pathway_summaries:
        detail = ps["detail"]
        items.append(
            PathwaySummary(
                pathway_id=detail.get("pathway_id", ""),
                pathway_name=ps["pathway"] or "",
                level=ps["pathway_level"] if ps["pathway_level"] is not None else "Standard",
                evidence_level=ps["evidence_level"] if ps["evidence_level"] is not None else 1,
                called_snps=detail.get("called_snps", 0),
                total_snps=detail.get("total_snps", 0),
                missing_snps=detail.get("missing_snps", []),
                no_call_snps=detail.get("no_call_snps", []),
                pmids=ps["pmids"],
            )
        )

    # MC1R aggregate
    mc1r_aggregate: MC1RAggregateItem | None = None
    mc1r_findings = [f for f in all_findings if f["category"] == "mc1r_aggregate"]
    if mc1r_findings:
        mc = mc1r_findings[0]
        detail = mc["detail"]
        mc1r_aggregate = MC1RAggregateItem(
            r_allele_count=detail.get("r_allele_count", 0),
            r_allele_rsids=detail.get("r_allele_rsids", []),
            total_mc1r_called=detail.get("total_mc1r_called", 0),
            risk_label=detail.get("risk_label", ""),
            risk_description=detail.get("risk_description", ""),
            evidence_level=mc["evidence_level"] if mc["evidence_level"] is not None else 2,
            pmids=mc["pmids"],
        )

    # Cross-module findings
    cross_findings = [f for f in all_findings if f["category"] == "cross_module"]
    cross_items: list[CrossModuleItem] = []
    for cf in cross_findings:
        detail = cf["detail"]
        cross_items.append(
            CrossModuleItem(
                rsid=cf["rsid"] or "",
                gene=cf["gene_symbol"] or "",
                source_module=detail.get("source_module", "skin"),
                target_module=detail.get("target_module", ""),
                finding_text=cf["finding_text"] or "",
                evidence_level=cf["evidence_level"] if cf["evidence_level"] is not None else 1,
                pmids=cf["pmids"],
            )
        )

    # Insufficient data caveats
    insuf_findings = [f for f in all_findings if f["category"] == "insufficient_data"]
    insuf_items: list[InsufficientDataItem] = []
    for inf in insuf_findings:
        detail = inf["detail"]
        insuf_items.append(
            InsufficientDataItem(
                rsid=inf["rsid"] or "",
                gene=inf["gene_symbol"] or "",
                finding_text=inf["finding_text"] or "",
                evidence_level=inf["evidence_level"] if inf["evidence_level"] is not None else 1,
                pathway=inf["pathway"] or "",
                proxy_target=detail.get("proxy_target"),
                reason=detail.get("insufficient_data_reason"),
                pmids=inf["pmids"],
            )
        )

    return PathwaysResponse(
        items=items,
        total=len(items),
        mc1r_aggregate=mc1r_aggregate,
        cross_module=cross_items,
        insufficient_data=insuf_items,
    )


@router.get("/pathway/{pathway_id}")
def pathway_detail(
    pathway_id: str,
    sample_id: int = Query(..., description="Sample ID"),
) -> PathwayDetailResponse:
    """Get detailed results for a single skin pathway.

    Returns the pathway-level summary plus per-SNP breakdown with
    genotype, category, effect summary, MC1R allele class, and
    coverage notes.

    Example: ``GET /api/analysis/skin/pathway/pigmentation_uv?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)
    all_findings = _fetch_skin_findings(sample_engine)

    # Find the pathway summary finding
    pathway_summary = None
    for f in all_findings:
        if f["category"] == "pathway_summary" and f["detail"].get("pathway_id") == pathway_id:
            pathway_summary = f
            break

    if pathway_summary is None:
        raise HTTPException(
            status_code=404,
            detail=f"Pathway '{pathway_id}' not found for sample.",
        )

    detail = pathway_summary["detail"]
    pathway_name = pathway_summary["pathway"] or ""

    # Build SNP details from snp_details in the pathway summary detail_json
    snp_details_data = detail.get("snp_details", [])

    # Collect per-SNP findings for recommendations
    snp_findings_map: dict[str, dict[str, Any]] = {}
    for f in all_findings:
        if f["category"] == "snp_finding" and f["pathway"] == pathway_name:
            rsid = f["rsid"]
            if rsid:
                snp_findings_map[rsid] = f

    snp_details: list[SNPDetail] = []
    for sd in snp_details_data:
        rsid = sd.get("rsid", "")
        snp_finding = snp_findings_map.get(rsid, {})
        snp_finding_detail = snp_finding.get("detail", {})
        recommendation = snp_finding_detail.get("recommendation")
        pmids = snp_finding.get("pmids", [])

        snp_details.append(
            SNPDetail(
                rsid=rsid,
                gene=sd.get("gene", ""),
                variant_name=sd.get("variant_name", ""),
                genotype=sd.get("genotype"),
                category=sd.get("category", "Standard"),
                effect_summary=sd.get("effect_summary", ""),
                evidence_level=sd.get("evidence_level", 1),
                recommendation=recommendation,
                pmids=pmids,
                mc1r_allele_class=sd.get("mc1r_allele_class"),
                coverage_note=sd.get("coverage_note"),
                insufficient_data_flag=sd.get("insufficient_data_flag", False),
            )
        )

    return PathwayDetailResponse(
        pathway_id=pathway_id,
        pathway_name=pathway_name,
        level=(
            pathway_summary["pathway_level"]
            if pathway_summary["pathway_level"] is not None
            else "Standard"
        ),
        evidence_level=(
            pathway_summary["evidence_level"]
            if pathway_summary["evidence_level"] is not None
            else 1
        ),
        called_snps=detail.get("called_snps", 0),
        total_snps=detail.get("total_snps", 0),
        missing_snps=detail.get("missing_snps", []),
        no_call_snps=detail.get("no_call_snps", []),
        pmids=pathway_summary["pmids"],
        snp_details=snp_details,
    )


@router.post("/run")
def run_skin(
    sample_id: int = Query(..., description="Sample ID"),
) -> RunResponse:
    """Run or re-run skin scoring for a sample.

    Loads the curated panel, scores all pathways using the sample's
    genotypes, generates MC1R aggregate and cross-module findings,
    and stores findings.

    Example: ``POST /api/analysis/skin/run?sample_id=1``
    """
    from backend.analysis.skin import (
        load_skin_panel,
        score_skin_pathways,
        store_skin_findings,
        update_annotation_coverage_gwas,
    )

    registry = get_registry()
    sample_engine = _get_sample_engine(sample_id)

    panel = load_skin_panel()
    result = score_skin_pathways(panel, sample_engine, registry.reference_engine)
    count = store_skin_findings(result, sample_engine)

    # Set annotation_coverage bitmask bit 5 for GWAS-matched variants
    gwas_updated = update_annotation_coverage_gwas(result, sample_engine)
    logger.info("Skin GWAS annotation_coverage updated for %d variants", gwas_updated)

    return RunResponse(
        findings_count=count,
        pathways_scored=len(result.pathway_results),
    )
