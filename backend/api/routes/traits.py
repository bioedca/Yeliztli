"""Traits & Personality findings API (P3-63).

PRS-primary module with evidence-gated individual SNPs. All findings
hard-capped at ★★☆☆. Associative language only. Research Use Only
labels on PRS outputs.

GET  /api/analysis/traits/pathways?sample_id=N             — All pathway results
GET  /api/analysis/traits/pathway/{pathway_id}?sample_id=N — Single pathway detail
GET  /api/analysis/traits/prs?sample_id=N                  — PRS results
GET  /api/analysis/traits/disclaimer                       — Module disclaimer
POST /api/analysis/traits/run?sample_id=N                  — Run/re-run scoring
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

router = APIRouter(prefix="/analysis/traits", tags=["traits"])


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
    trait_domain: str | None = None
    recommendation: str | None = None
    pmids: list[str] = []
    coverage_note: str | None = None
    cross_module: dict | None = None


class PathwaySummary(BaseModel):
    """Summary of a single traits pathway."""

    pathway_id: str
    pathway_name: str
    level: str  # Elevated / Moderate / Standard
    evidence_level: int
    prs_primary: bool = False
    called_snps: int
    total_snps: int
    missing_snps: list[str] = []
    pmids: list[str] = []


class PRSFinding(BaseModel):
    """PRS finding for a trait."""

    trait: str
    name: str
    percentile: float | None = None
    z_score: float | None = None
    bootstrap_ci_lower: float | None = None
    bootstrap_ci_upper: float | None = None
    source_ancestry: str
    source_study: str
    snps_used: int
    snps_total: int
    coverage_fraction: float
    ancestry_mismatch: bool = False
    ancestry_warning_text: str | None = None
    is_sufficient: bool = True
    calibrated: bool = True
    research_use_only: bool = True
    evidence_level: int = 1


class CrossModuleItem(BaseModel):
    """Cross-module link finding."""

    rsid: str
    gene: str
    from_trait: str
    to_module: str
    link_type: str
    finding_text: str
    evidence_level: int
    pmids: list[str] = []


class PathwaysResponse(BaseModel):
    """All pathway results for a sample."""

    items: list[PathwaySummary]
    total: int
    cross_module: list[CrossModuleItem] = []
    module_disclaimer: str = ""


class PathwayDetailResponse(BaseModel):
    """Full pathway detail with per-SNP breakdown."""

    pathway_id: str
    pathway_name: str
    level: str
    evidence_level: int
    prs_primary: bool = False
    called_snps: int
    total_snps: int
    missing_snps: list[str] = []
    pmids: list[str] = []
    snp_details: list[SNPDetail] = []


class PRSResponse(BaseModel):
    """PRS results for the traits module."""

    items: list[PRSFinding]
    total: int
    module_disclaimer: str = ""


class DisclaimerResponse(BaseModel):
    """Module-level disclaimer text."""

    disclaimer: str
    evidence_cap: int
    research_use_only: bool


class RunResponse(BaseModel):
    """Result of running traits scoring."""

    findings_count: int
    pathways_scored: int
    prs_computed: int


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


def _fetch_traits_findings(
    sample_engine: sa.Engine,
) -> list[dict[str, Any]]:
    """Fetch all traits findings from the sample DB."""
    with sample_engine.connect() as conn:
        stmt = sa.select(findings).where(findings.c.module == "traits").order_by(findings.c.id)
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
                "prs_percentile": row.prs_percentile,
                "pmids": pmids,
                "detail": detail,
            }
        )

    return result


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/pathways", dependencies=[Depends(require_fresh_sample)])
def list_pathways(
    sample_id: int = Query(..., description="Sample ID"),
) -> PathwaysResponse:
    """List all traits pathway results for a sample.

    Returns each of the three traits pathways with their categorical
    level (Elevated / Moderate / Standard), evidence level, and SNP counts.

    Example: ``GET /api/analysis/traits/pathways?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)
    all_findings = _fetch_traits_findings(sample_engine)

    # Filter to pathway_summary findings only
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
                prs_primary=detail.get("prs_primary", False),
                called_snps=detail.get("called_snps", 0),
                total_snps=detail.get("total_snps", 0),
                missing_snps=detail.get("missing_snps", []),
                pmids=ps["pmids"],
            )
        )

    # Cross-module findings
    cross_module_findings = [f for f in all_findings if f["category"] == "cross_module"]
    cross_items: list[CrossModuleItem] = []
    for cf in cross_module_findings:
        detail = cf["detail"]
        cross_items.append(
            CrossModuleItem(
                rsid=cf["rsid"] or "",
                gene=cf["gene_symbol"] or "",
                from_trait=detail.get("trait_domain", ""),
                to_module=detail.get("to_module", ""),
                link_type=detail.get("link_type", ""),
                finding_text=cf["finding_text"] or "",
                evidence_level=cf["evidence_level"] if cf["evidence_level"] is not None else 1,
                pmids=cf["pmids"],
            )
        )

    # Load disclaimer from panel
    from backend.analysis.traits import load_traits_panel

    panel = load_traits_panel()

    return PathwaysResponse(
        items=items,
        total=len(items),
        cross_module=cross_items,
        module_disclaimer=panel.module_disclaimer,
    )


@router.get("/pathway/{pathway_id}", dependencies=[Depends(require_fresh_sample)])
def pathway_detail(
    pathway_id: str,
    sample_id: int = Query(..., description="Sample ID"),
) -> PathwayDetailResponse:
    """Get detailed results for a single traits pathway.

    Returns the pathway-level summary plus per-SNP breakdown with
    genotype, category, effect summary, trait domain, and coverage notes.

    Example: ``GET /api/analysis/traits/pathway/personality_big_five?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)
    all_findings = _fetch_traits_findings(sample_engine)

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
                trait_domain=sd.get("trait_domain"),
                recommendation=recommendation,
                pmids=pmids,
                coverage_note=sd.get("coverage_note"),
                cross_module=snp_finding_detail.get("cross_module"),
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
        prs_primary=detail.get("prs_primary", False),
        called_snps=detail.get("called_snps", 0),
        total_snps=detail.get("total_snps", 0),
        missing_snps=detail.get("missing_snps", []),
        pmids=pathway_summary["pmids"],
        snp_details=snp_details,
    )


@router.get("/prs", dependencies=[Depends(require_fresh_sample)])
def list_prs(
    sample_id: int = Query(..., description="Sample ID"),
) -> PRSResponse:
    """List PRS results for the traits module.

    Returns educational attainment and cognitive ability PRS percentiles
    with bootstrap CI, ancestry mismatch warnings, and "Research Use Only"
    labels.

    Example: ``GET /api/analysis/traits/prs?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)
    all_findings = _fetch_traits_findings(sample_engine)

    prs_findings = [f for f in all_findings if f["category"] == "prs"]

    items: list[PRSFinding] = []
    for pf in prs_findings:
        detail = pf["detail"]
        items.append(
            PRSFinding(
                trait=detail.get("trait", ""),
                name=detail.get("name", ""),
                percentile=detail.get("percentile", pf.get("prs_percentile")),
                z_score=detail.get("z_score"),
                bootstrap_ci_lower=detail.get("bootstrap_ci_lower"),
                bootstrap_ci_upper=detail.get("bootstrap_ci_upper"),
                source_ancestry=detail.get("source_ancestry", ""),
                source_study=detail.get("source_study", ""),
                snps_used=detail.get("snps_used", 0),
                snps_total=detail.get("snps_total", 0),
                coverage_fraction=detail.get("coverage_fraction", 0.0),
                ancestry_mismatch=detail.get("ancestry_mismatch", False),
                ancestry_warning_text=detail.get("ancestry_warning_text"),
                is_sufficient=detail.get("is_sufficient", True),
                calibrated=detail.get("calibrated", True),
                research_use_only=detail.get("research_use_only", True),
                evidence_level=pf["evidence_level"] or 1,
            )
        )

    from backend.analysis.traits import load_traits_panel

    panel = load_traits_panel()

    return PRSResponse(
        items=items,
        total=len(items),
        module_disclaimer=panel.module_disclaimer,
    )


@router.get("/disclaimer")
def get_disclaimer() -> DisclaimerResponse:
    """Get the module-level disclaimer text.

    Returns the mandatory disclaimer for traits & personality findings,
    the evidence cap level, and the research use only flag.

    Example: ``GET /api/analysis/traits/disclaimer``
    """
    from backend.analysis.traits import load_traits_panel

    panel = load_traits_panel()
    return DisclaimerResponse(
        disclaimer=panel.module_disclaimer,
        evidence_cap=panel.evidence_cap,
        research_use_only=True,
    )


@router.post("/run", dependencies=[Depends(require_fresh_sample)])
def run_traits(
    sample_id: int = Query(..., description="Sample ID"),
) -> RunResponse:
    """Run or re-run traits scoring for a sample.

    Loads the curated panel, scores all pathways using the sample's
    genotypes, runs PRS computation, generates cross-module findings,
    looks up GWAS associations, and stores findings.

    Example: ``POST /api/analysis/traits/run?sample_id=1``
    """
    from backend.analysis.traits import (
        load_traits_panel,
        score_traits_pathways,
        store_traits_findings,
        update_annotation_coverage_gwas,
    )

    registry = get_registry()
    sample_engine = _get_sample_engine(sample_id)

    panel = load_traits_panel()
    result = score_traits_pathways(panel, sample_engine, registry.reference_engine)
    count = store_traits_findings(result, sample_engine)

    # Set annotation_coverage bitmask bit 5 for GWAS-matched variants
    gwas_updated = update_annotation_coverage_gwas(result, sample_engine)
    logger.info("Traits GWAS annotation_coverage updated for %d variants", gwas_updated)

    return RunResponse(
        findings_count=count,
        pathways_scored=len(result.pathway_results),
        prs_computed=len(result.prs_results),
    )
