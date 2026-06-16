"""MTHFR & Methylation findings API (P3-52).

5 pathway-level summaries with additive scoring, MTHFR compound
heterozygosity calling, CBS proxy caveats, COMT catecholamine framing.

GET  /api/analysis/methylation/pathways?sample_id=N             — All pathway results
GET  /api/analysis/methylation/pathway/{pathway_id}?sample_id=N — Single pathway detail
POST /api/analysis/methylation/run?sample_id=N                  — Run/re-run scoring
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
    prefix="/analysis/methylation",
    tags=["methylation"],
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
    coverage_note: str | None = None


class PathwaySummary(BaseModel):
    """Summary of a single methylation pathway."""

    pathway_id: str
    pathway_name: str
    level: str  # Elevated / Moderate / Standard
    evidence_level: int
    called_snps: int
    total_snps: int
    missing_snps: list[str] = []
    no_call_snps: list[str] = []
    pmids: list[str] = []
    additive_promoted: bool = False


class CompoundHetInfo(BaseModel):
    """MTHFR compound heterozygosity info."""

    is_compound_het: bool = False
    is_double_homozygous: bool = False
    label: str | None = None
    c677t_genotype: str | None = None
    a1298c_genotype: str | None = None
    finding_text: str | None = None


class PathwaysResponse(BaseModel):
    """All pathway results for a sample."""

    items: list[PathwaySummary]
    total: int
    compound_het: CompoundHetInfo | None = None


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
    additive_promoted: bool = False
    snp_details: list[SNPDetail] = []


class RunResponse(BaseModel):
    """Result of running methylation scoring."""

    findings_count: int
    pathways_scored: int
    compound_het_detected: bool = False


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


def _fetch_methylation_findings(
    sample_engine: sa.Engine,
) -> list[dict[str, Any]]:
    """Fetch all methylation findings from the sample DB."""
    with sample_engine.connect() as conn:
        stmt = (
            sa.select(findings).where(findings.c.module == "methylation").order_by(findings.c.id)
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
    """List all methylation pathway results for a sample.

    Returns each of the five methylation pathways with their categorical
    level (Elevated / Moderate / Standard), evidence level, SNP counts,
    and MTHFR compound heterozygosity info.

    Example: ``GET /api/analysis/methylation/pathways?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)
    all_findings = _fetch_methylation_findings(sample_engine)

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
                called_snps=detail.get("called_snps", 0),
                total_snps=detail.get("total_snps", 0),
                missing_snps=detail.get("missing_snps", []),
                no_call_snps=detail.get("no_call_snps", []),
                pmids=ps["pmids"],
                additive_promoted=detail.get("additive_promoted", False),
            )
        )

    # Compound heterozygosity finding
    compound_het_findings = [f for f in all_findings if f["category"] == "compound_het"]
    compound_het = None
    if compound_het_findings:
        ch_f = compound_het_findings[0]
        ch_detail = ch_f["detail"]
        compound_het = CompoundHetInfo(
            is_compound_het=ch_detail.get("is_compound_het", False),
            is_double_homozygous=ch_detail.get("is_double_homozygous", False),
            label=ch_f.get("gene_symbol"),
            c677t_genotype=ch_detail.get("c677t_genotype"),
            a1298c_genotype=ch_detail.get("a1298c_genotype"),
            finding_text=ch_f["finding_text"],
        )

    return PathwaysResponse(items=items, total=len(items), compound_het=compound_het)


@router.get("/pathway/{pathway_id}")
def pathway_detail(
    pathway_id: str,
    sample_id: int = Query(..., description="Sample ID"),
) -> PathwayDetailResponse:
    """Get detailed results for a single methylation pathway.

    Returns the pathway-level summary plus per-SNP breakdown with
    genotype, category, effect summary, and coverage notes.

    Example: ``GET /api/analysis/methylation/pathway/folate_mthfr?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)
    all_findings = _fetch_methylation_findings(sample_engine)

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
                coverage_note=sd.get("coverage_note"),
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
        additive_promoted=detail.get("additive_promoted", False),
        snp_details=snp_details,
    )


@router.post("/run")
def run_methylation(
    sample_id: int = Query(..., description="Sample ID"),
) -> RunResponse:
    """Run or re-run methylation scoring for a sample.

    Loads the curated panel, scores all 5 pathways using the sample's
    genotypes, assesses MTHFR compound heterozygosity, migrates MTHFR
    findings from Nutrigenomics, and stores findings.

    Example: ``POST /api/analysis/methylation/run?sample_id=1``
    """
    from backend.analysis.methylation import (
        load_methylation_panel,
        score_methylation_pathways,
        store_methylation_findings,
        update_annotation_coverage_gwas,
    )

    registry = get_registry()
    sample_engine = _get_sample_engine(sample_id)

    panel = load_methylation_panel()
    result = score_methylation_pathways(panel, sample_engine, registry.reference_engine)
    count = store_methylation_findings(result, sample_engine)

    # Set annotation_coverage bitmask bit 5 for GWAS-matched variants
    gwas_updated = update_annotation_coverage_gwas(result, sample_engine)
    logger.info("Methylation GWAS annotation_coverage updated for %d variants", gwas_updated)

    compound_het_detected = result.compound_het is not None and (
        result.compound_het.is_compound_het or result.compound_het.is_double_homozygous
    )

    return RunResponse(
        findings_count=count,
        pathways_scored=len(result.pathway_results),
        compound_het_detected=compound_het_detected,
    )
