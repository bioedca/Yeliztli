"""Cardiovascular findings API (P3-19, P3-20).

ClinVar P/LP extraction results from the 16-gene cardiovascular panel —
monogenic pathogenic variants with accession, review stars, conditions,
and inheritance. FH variant status reporting (P3-20).

GET  /api/analysis/cardiovascular/disclaimer                     — Cardiovascular module disclaimer
GET  /api/analysis/cardiovascular/variants?sample_id=N           — All cardiovascular P/LP findings
GET  /api/analysis/cardiovascular/gene/{gene_symbol}?sample_id=N — Findings for a single gene
GET  /api/analysis/cardiovascular/fh-status?sample_id=N          — FH status summary
POST /api/analysis/cardiovascular/run?sample_id=N                — Run/re-run extraction
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.analysis.clinvar_significance import LOWER_PENETRANCE_RISK_ALLELE_CATEGORY
from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import findings, samples
from backend.disclaimers import (
    CARDIOVASCULAR_DISCLAIMER_TEXT,
    CARDIOVASCULAR_DISCLAIMER_TITLE,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis/cardiovascular", tags=["cardiovascular"])


# ── Response models ──────────────────────────────────────────────


class CardiovascularVariantResponse(BaseModel):
    """A single P/LP variant in the cardiovascular panel."""

    rsid: str
    gene_symbol: str
    genotype: str | None = None
    zygosity: str | None = None
    clinvar_significance: str
    clinvar_accession: str | None = None
    clinvar_review_stars: int = 0
    clinvar_conditions: str | None = None
    conditions: list[str] = []
    cardiovascular_category: str = ""
    clinvar_low_penetrance_or_risk_allele: bool = False
    inheritance: str = "AD"
    evidence_level: int = 1
    cross_links: list[str] = []
    pmids: list[str] = []


class CardiovascularVariantsListResponse(BaseModel):
    """All cardiovascular P/LP findings for a sample."""

    items: list[CardiovascularVariantResponse]
    total: int


class FHVariantSummary(BaseModel):
    """Summary of a single FH variant within the FH status response."""

    rsid: str
    gene_symbol: str
    genotype: str | None = None
    zygosity: str | None = None
    clinvar_significance: str
    clinvar_review_stars: int = 0
    clinvar_accession: str | None = None
    evidence_level: int = 1


class FHStatusResponse(BaseModel):
    """FH status determination for a sample (P3-20)."""

    status: str  # Positive or Negative
    summary_text: str
    affected_genes: list[str] = []
    variant_count: int = 0
    has_homozygous: bool = False
    highest_evidence_level: int = 0
    variants: list[FHVariantSummary] = []


class CardiovascularDisclaimerResponse(BaseModel):
    """Cardiovascular module disclaimer text."""

    title: str
    text: str


class CardiovascularRunResponse(BaseModel):
    """Result of running cardiovascular extraction + FH status."""

    findings_count: int
    panel_genes_checked: int
    variants_in_panel_genes: int
    fh_status: str
    fh_variant_count: int


# ── Helpers ──────────────────────────────────────────────────────


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


def _fetch_cardiovascular_findings(
    sample_engine: sa.Engine,
    gene_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch cardiovascular monogenic findings from the sample DB."""
    with sample_engine.connect() as conn:
        stmt = (
            sa.select(findings)
            .where(
                findings.c.module == "cardiovascular",
                findings.c.category.in_(
                    ["monogenic_variant", LOWER_PENETRANCE_RISK_ALLELE_CATEGORY]
                ),
            )
            .order_by(findings.c.evidence_level.desc(), findings.c.gene_symbol)
        )
        if gene_filter:
            stmt = stmt.where(findings.c.gene_symbol == gene_filter.upper())

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
                "rsid": row.rsid or "",
                "gene_symbol": row.gene_symbol or "",
                "genotype": detail.get("genotype"),
                "zygosity": row.zygosity,
                "clinvar_significance": row.clinvar_significance or "",
                "clinvar_accession": detail.get("clinvar_accession"),
                "clinvar_review_stars": detail.get("clinvar_review_stars", 0),
                "clinvar_conditions": row.conditions,
                "conditions": detail.get("conditions", []),
                "cardiovascular_category": detail.get("cardiovascular_category", ""),
                "clinvar_low_penetrance_or_risk_allele": bool(
                    detail.get("clinvar_low_penetrance_or_risk_allele", False)
                ),
                "inheritance": detail.get("inheritance", "AD"),
                "evidence_level": row.evidence_level or 1,
                "cross_links": detail.get("cross_links", []),
                "pmids": pmids,
            }
        )

    return result


# ── Endpoints ────────────────────────────────────────────────────


@router.get("/disclaimer")
def get_cardiovascular_disclaimer() -> CardiovascularDisclaimerResponse:
    """Return cardiovascular module disclaimer text.

    Example: ``GET /api/analysis/cardiovascular/disclaimer``
    """
    return CardiovascularDisclaimerResponse(
        title=CARDIOVASCULAR_DISCLAIMER_TITLE,
        text=CARDIOVASCULAR_DISCLAIMER_TEXT,
    )


@router.get("/variants", dependencies=[Depends(require_fresh_sample)])
def list_cardiovascular_variants(
    sample_id: int = Query(..., description="Sample ID"),
) -> CardiovascularVariantsListResponse:
    """List all cardiovascular P/LP variant findings for a sample.

    Returns ClinVar Pathogenic and Likely pathogenic variants in the
    16-gene cardiovascular panel, sorted by evidence level (highest first).

    Example: ``GET /api/analysis/cardiovascular/variants?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)
    raw = _fetch_cardiovascular_findings(sample_engine)
    items = [CardiovascularVariantResponse(**f) for f in raw]
    return CardiovascularVariantsListResponse(items=items, total=len(items))


@router.get("/gene/{gene_symbol}", dependencies=[Depends(require_fresh_sample)])
def cardiovascular_gene_detail(
    gene_symbol: str,
    sample_id: int = Query(..., description="Sample ID"),
) -> CardiovascularVariantsListResponse:
    """Get cardiovascular findings for a specific gene.

    Example: ``GET /api/analysis/cardiovascular/gene/LDLR?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)
    raw = _fetch_cardiovascular_findings(sample_engine, gene_filter=gene_symbol)
    items = [CardiovascularVariantResponse(**f) for f in raw]
    return CardiovascularVariantsListResponse(items=items, total=len(items))


@router.get("/fh-status", dependencies=[Depends(require_fresh_sample)])
def get_fh_status(
    sample_id: int = Query(..., description="Sample ID"),
) -> FHStatusResponse:
    """Get FH status determination for a sample (P3-20).

    Returns whether the sample has FH-associated P/LP variants in
    LDLR, PCSK9, or APOB, with a summary status (Positive/Negative)
    and detailed variant information.

    Example: ``GET /api/analysis/cardiovascular/fh-status?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)

    with sample_engine.connect() as conn:
        row = conn.execute(
            sa.select(findings).where(
                findings.c.module == "cardiovascular",
                findings.c.category == "fh_status",
            )
        ).fetchone()

    if row is None:
        return FHStatusResponse(
            status="Negative",
            summary_text=(
                "No pathogenic or likely pathogenic variants identified in "
                "FH-associated genes (LDLR, PCSK9, APOB)."
            ),
        )

    detail: dict[str, Any] = {}
    if row.detail_json:
        try:
            detail = json.loads(row.detail_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse FH status detail_json")

    fh_variants = [FHVariantSummary(**v) for v in detail.get("fh_variants", [])]

    return FHStatusResponse(
        status=detail.get("status", "Negative"),
        summary_text=row.finding_text or "",
        affected_genes=detail.get("affected_genes", []),
        variant_count=detail.get("variant_count", 0),
        has_homozygous=detail.get("has_homozygous", False),
        highest_evidence_level=detail.get("highest_evidence_level", 0),
        variants=fh_variants,
    )


@router.post("/run", dependencies=[Depends(require_fresh_sample)])
def run_cardiovascular_analysis(
    sample_id: int = Query(..., description="Sample ID"),
) -> CardiovascularRunResponse:
    """Run or re-run cardiovascular extraction + FH status for a sample.

    Loads the curated panel, extracts ClinVar P/LP variants from
    annotated_variants, determines FH status, and stores all findings.

    Example: ``POST /api/analysis/cardiovascular/run?sample_id=1``
    """
    from backend.analysis.cardiovascular import (
        determine_fh_status,
        extract_cardiovascular_variants,
        load_cardiovascular_panel,
        store_cardiovascular_findings,
        store_fh_status_finding,
    )

    sample_engine = _get_sample_engine(sample_id)

    # Monogenic extraction (P3-19)
    panel = load_cardiovascular_panel()
    result = extract_cardiovascular_variants(panel, sample_engine)
    # Pass the reference engine so findings gain the gnomAD gene-constraint
    # context badge (roadmap #12), matching the run_all dashboard path.
    count = store_cardiovascular_findings(result, sample_engine, get_registry().reference_engine)

    # FH status reporting (P3-20)
    fh_status = determine_fh_status(result)
    store_fh_status_finding(fh_status, sample_engine)

    return CardiovascularRunResponse(
        findings_count=count,
        panel_genes_checked=result.panel_genes_checked,
        variants_in_panel_genes=result.variants_in_panel_genes,
        fh_status=fh_status.status,
        fh_variant_count=fh_status.variant_count,
    )
