"""Carrier status findings API (P3-36).

Heterozygous ClinVar P/LP variants from the 7-gene carrier panel with
reproductive framing.  BRCA1/2 dual-role cross-links to cancer module.

GET  /api/analysis/carrier/disclaimer                         — Carrier disclaimer
GET  /api/analysis/carrier/variants?sample_id=N               — All carrier findings
GET  /api/analysis/carrier/gene/{gene_symbol}?sample_id=N     — Findings for a gene
POST /api/analysis/carrier/run?sample_id=N                    — Run/re-run extraction
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
from backend.disclaimers import (
    CARRIER_GENE_NOTES,
    CARRIER_STATUS_DISCLAIMER_TEXT,
    CARRIER_STATUS_DISCLAIMER_TITLE,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis/carrier", tags=["carrier"])


# ── Response models ──────────────────────────────────────────────────


class CarrierVariantResponse(BaseModel):
    """A single heterozygous P/LP variant in the carrier panel."""

    rsid: str
    gene_symbol: str
    genotype: str | None = None
    zygosity: str = "het"
    clinvar_significance: str
    clinvar_accession: str | None = None
    clinvar_review_stars: int = 0
    clinvar_conditions: str | None = None
    conditions: list[str] = []
    inheritance: str = "AR"
    clinvar_low_penetrance_or_risk_allele: bool = False
    evidence_level: int = 1
    cross_links: list[str] = []
    pmids: list[str] = []
    notes: str = ""


class CarrierVariantsListResponse(BaseModel):
    """All carrier findings for a sample."""

    items: list[CarrierVariantResponse]
    total: int
    genes_with_findings: list[str]


class CarrierDisclaimerResponse(BaseModel):
    """Carrier status disclaimer text with per-gene notes."""

    title: str
    text: str
    gene_notes: dict[str, str] = {}


class CarrierRunResponse(BaseModel):
    """Result of running carrier status extraction."""

    findings_count: int
    panel_genes_checked: int
    variants_in_panel_genes: int
    homozygous_plp_skipped: int


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


def _fetch_carrier_findings(
    sample_engine: sa.Engine,
    gene_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch carrier findings from the sample DB."""
    with sample_engine.connect() as conn:
        stmt = (
            sa.select(findings)
            .where(findings.c.module == "carrier")
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
                "zygosity": row.zygosity or "het",
                "clinvar_significance": row.clinvar_significance or "",
                "clinvar_accession": detail.get("clinvar_accession"),
                "clinvar_review_stars": detail.get("clinvar_review_stars", 0),
                "clinvar_conditions": row.conditions,
                "conditions": detail.get("conditions", []),
                "inheritance": detail.get("inheritance", "AR"),
                "clinvar_low_penetrance_or_risk_allele": bool(
                    detail.get("clinvar_low_penetrance_or_risk_allele", False)
                ),
                "evidence_level": row.evidence_level or 1,
                "cross_links": detail.get("cross_links", []),
                "pmids": pmids,
                "notes": detail.get("notes", ""),
            }
        )

    return result


def _findings_to_response(
    finding_rows: list[dict[str, Any]],
) -> list[CarrierVariantResponse]:
    """Convert raw finding dicts to response models."""
    return [CarrierVariantResponse(**f) for f in finding_rows]


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/disclaimer")
def get_carrier_disclaimer() -> CarrierDisclaimerResponse:
    """Return carrier status disclaimer text.

    Module-specific disclaimer for the carrier status section,
    covering reproductive framing and genotyping chip limitations.

    Example: ``GET /api/analysis/carrier/disclaimer``
    """
    return CarrierDisclaimerResponse(
        title=CARRIER_STATUS_DISCLAIMER_TITLE,
        text=CARRIER_STATUS_DISCLAIMER_TEXT,
        gene_notes=CARRIER_GENE_NOTES,
    )


@router.get("/variants", dependencies=[Depends(require_fresh_sample)])
def list_carrier_variants(
    sample_id: int = Query(..., description="Sample ID"),
) -> CarrierVariantsListResponse:
    """List all carrier status findings for a sample.

    Returns heterozygous Pathogenic and Likely pathogenic variants in the
    7-gene carrier panel, sorted by evidence level (highest first).
    Homozygous P/LP variants are excluded (affected, not carrier).

    Example: ``GET /api/analysis/carrier/variants?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)
    raw = _fetch_carrier_findings(sample_engine)
    items = _findings_to_response(raw)
    genes = sorted(set(item.gene_symbol for item in items))
    return CarrierVariantsListResponse(items=items, total=len(items), genes_with_findings=genes)


@router.get("/gene/{gene_symbol}", dependencies=[Depends(require_fresh_sample)])
def carrier_gene_detail(
    gene_symbol: str,
    sample_id: int = Query(..., description="Sample ID"),
) -> CarrierVariantsListResponse:
    """Get carrier findings for a specific gene.

    Example: ``GET /api/analysis/carrier/gene/CFTR?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)
    raw = _fetch_carrier_findings(sample_engine, gene_filter=gene_symbol)
    items = _findings_to_response(raw)
    genes = sorted(set(item.gene_symbol for item in items))
    return CarrierVariantsListResponse(items=items, total=len(items), genes_with_findings=genes)


@router.post("/run", dependencies=[Depends(require_fresh_sample)])
def run_carrier_analysis(
    sample_id: int = Query(..., description="Sample ID"),
) -> CarrierRunResponse:
    """Run or re-run carrier status extraction for a sample.

    Loads the curated carrier panel, extracts heterozygous ClinVar P/LP
    variants from annotated_variants, and stores findings with
    reproductive framing.

    Example: ``POST /api/analysis/carrier/run?sample_id=1``
    """
    from backend.analysis.carrier_status import (
        extract_carrier_variants,
        load_carrier_panel,
        store_carrier_findings,
    )

    sample_engine = _get_sample_engine(sample_id)

    panel = load_carrier_panel()
    result = extract_carrier_variants(panel, sample_engine)
    count = store_carrier_findings(result, sample_engine)

    return CarrierRunResponse(
        findings_count=count,
        panel_genes_checked=result.panel_genes_checked,
        variants_in_panel_genes=result.variants_in_panel_genes,
        homozygous_plp_skipped=result.homozygous_plp_skipped,
    )
