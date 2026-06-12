"""Metabolic disease PRS API — type 2 diabetes & obesity/BMI (SW-B5).

Genome-wide PGS Catalog scores (T2D PGS000713; multi-ancestry BMI PGS005198)
with honest coverage reporting + an ancestry-mismatch warning, plus established
anchor SNPs (TCF7L2, FTO, MC4R). Polygenic percentiles are withheld on
un-imputed array data (coverage too low); coverage is reported instead.

GET  /api/analysis/metabolic/prs?sample_id=N      — T2D & BMI PRS (coverage)
GET  /api/analysis/metabolic/anchors?sample_id=N  — anchor SNP results
POST /api/analysis/metabolic/run?sample_id=N      — run/re-run scoring
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.analysis.metabolic_prs import COVERAGE_CONTEXT
from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import findings, samples

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/analysis/metabolic",
    tags=["metabolic"],
    dependencies=[Depends(require_fresh_sample)],
)


# ── Response models ──────────────────────────────────────────────────


class MetabolicPRSResponse(BaseModel):
    """A single metabolic PRS result (percentile withheld — coverage reported)."""

    trait: str
    name: str
    calibrated: bool = False
    percentile: float | None = None
    snps_used: int = 0
    snps_total: int = 0
    coverage_fraction: float = 0.0
    is_sufficient: bool = False
    source_ancestry: str = ""
    source_study: str = ""
    source_pmid: str = ""
    sample_size: int = 0
    ancestry_mismatch: bool = False
    ancestry_warning_text: str | None = None
    evidence_level: int = 1
    research_use_only: bool = True
    pgs_id: str | None = None
    pgs_license: str | None = None
    development_method: str | None = None
    genome_build: str | None = None
    variants_number: int | None = None
    source_url: str | None = None


class MetabolicPRSListResponse(BaseModel):
    """All metabolic PRS results for a sample."""

    items: list[MetabolicPRSResponse]
    total: int
    coverage_context: str = COVERAGE_CONTEXT


class MetabolicAnchorResponse(BaseModel):
    """A single anchor-SNP result."""

    trait: str
    trait_label: str
    gene: str
    rsid: str
    effect_allele: str
    genotype: str | None = None
    dosage: int | None = 0
    indeterminate: bool = False
    summary: str = ""
    evidence_level: int = 2
    pmids: list[str] = []


class MetabolicAnchorListResponse(BaseModel):
    """All anchor-SNP results for a sample."""

    items: list[MetabolicAnchorResponse]
    total: int


class MetabolicRunResponse(BaseModel):
    """Result of running metabolic scoring."""

    findings_count: int
    prs_traits_computed: int
    anchors_typed: int


# ── Helpers ──────────────────────────────────────────────────────────


def _get_sample_engine(sample_id: int) -> sa.Engine:
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
            status_code=404, detail=f"Sample database file not found for sample {sample_id}."
        )
    return registry.get_sample_engine(sample_db_path)


def _parse_detail(row: sa.Row) -> dict[str, Any]:
    if not row.detail_json:
        return {}
    try:
        return json.loads(row.detail_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse detail_json for finding id=%s", row.id)
        return {}


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/prs")
def list_metabolic_prs(
    sample_id: int = Query(..., description="Sample ID"),
) -> MetabolicPRSListResponse:
    """List T2D & BMI PRS results (coverage reported, percentile withheld)."""
    sample_engine = _get_sample_engine(sample_id)
    with sample_engine.connect() as conn:
        rows = conn.execute(
            sa.select(findings)
            .where(findings.c.module == "metabolic", findings.c.category == "prs")
            .order_by(findings.c.id)
        ).fetchall()

    items: list[MetabolicPRSResponse] = []
    for row in rows:
        d = _parse_detail(row)
        items.append(
            MetabolicPRSResponse(
                trait=d.get("trait", ""),
                name=d.get("name", ""),
                calibrated=d.get("calibrated", False),
                percentile=row.prs_percentile,
                snps_used=d.get("snps_used", 0),
                snps_total=d.get("snps_total", 0),
                coverage_fraction=d.get("coverage_fraction", 0.0),
                is_sufficient=d.get("is_sufficient", False),
                source_ancestry=d.get("source_ancestry", ""),
                source_study=d.get("source_study", ""),
                source_pmid=d.get("source_pmid", ""),
                sample_size=d.get("sample_size", 0),
                ancestry_mismatch=d.get("ancestry_mismatch", False),
                ancestry_warning_text=d.get("ancestry_warning_text"),
                evidence_level=row.evidence_level or 1,
                research_use_only=True,
                pgs_id=d.get("pgs_id"),
                pgs_license=d.get("pgs_license"),
                development_method=d.get("development_method"),
                genome_build=d.get("genome_build"),
                variants_number=d.get("variants_number"),
                source_url=d.get("source_url"),
            )
        )
    return MetabolicPRSListResponse(items=items, total=len(items))


@router.get("/anchors")
def list_metabolic_anchors(
    sample_id: int = Query(..., description="Sample ID"),
) -> MetabolicAnchorListResponse:
    """List established anchor-SNP results (TCF7L2, FTO, MC4R)."""
    sample_engine = _get_sample_engine(sample_id)
    with sample_engine.connect() as conn:
        rows = conn.execute(
            sa.select(findings)
            .where(findings.c.module == "metabolic", findings.c.category == "anchor_snp")
            .order_by(findings.c.gene_symbol)
        ).fetchall()

    items: list[MetabolicAnchorResponse] = []
    for row in rows:
        d = _parse_detail(row)
        pmids: list[str] = []
        if row.pmid_citations:
            try:
                pmids = json.loads(row.pmid_citations)
            except (json.JSONDecodeError, TypeError):
                pmids = []
        items.append(
            MetabolicAnchorResponse(
                trait=d.get("trait", ""),
                trait_label=d.get("trait_label", ""),
                gene=d.get("gene", row.gene_symbol or ""),
                rsid=d.get("rsid", row.rsid or ""),
                effect_allele=d.get("effect_allele", ""),
                genotype=d.get("genotype"),
                dosage=d.get("dosage"),
                indeterminate=d.get("indeterminate", False),
                summary=d.get("summary", ""),
                evidence_level=row.evidence_level or 2,
                pmids=pmids,
            )
        )
    return MetabolicAnchorListResponse(items=items, total=len(items))


@router.post("/run")
def run_metabolic_analysis(
    sample_id: int = Query(..., description="Sample ID"),
) -> MetabolicRunResponse:
    """Run or re-run T2D & BMI PRS + anchor scoring for a sample."""
    from backend.analysis.ancestry import get_inferred_ancestry, get_top_ancestry_fraction
    from backend.analysis.metabolic_prs import run_metabolic_prs, store_metabolic_findings
    from backend.analysis.pgs_bridge import get_pgs_scores_engine

    sample_engine = _get_sample_engine(sample_id)
    pgs_engine = get_pgs_scores_engine()
    try:
        inferred = get_inferred_ancestry(sample_engine)
        top_fraction = get_top_ancestry_fraction(sample_engine)
        result = run_metabolic_prs(sample_engine, pgs_engine, inferred, top_fraction)
        count = store_metabolic_findings(result, sample_engine)
    finally:
        if pgs_engine is not None:
            pgs_engine.dispose()

    typed_anchors = sum(1 for a in result.anchors if a.reportable)
    return MetabolicRunResponse(
        findings_count=count,
        prs_traits_computed=len(result.prs_results),
        anchors_typed=typed_anchors,
    )
