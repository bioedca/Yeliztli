"""Familial hypercholesterolemia (FH) view API (SW-B6).

Composes monogenic FH variants (LDLR/APOB/PCSK9), the APOB R3527Q (rs5742904)
familial-defective-apoB variant, and an LDL-C polygenic score, framed against the
Dutch Lipid Clinic Network / Simon Broome criteria — explicitly NOT a clinical
FH diagnosis.

GET  /api/analysis/fh/assessment?sample_id=N   — composed FH view
POST /api/analysis/fh/run?sample_id=N          — compute LDL-C PRS + APOB FDB
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.analysis.fh import FH_CRITERIA_CONTEXT, detect_fh_monogenic_with_status
from backend.analysis.pharmacogenomics import (
    is_patient_presentable_finding_payload,
    is_patient_presentable_response_payload,
)
from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import findings, samples

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/analysis/fh",
    tags=["fh"],
    dependencies=[Depends(require_fresh_sample)],
)


# ── Response models ──────────────────────────────────────────────────


class FhMonogenicResponse(BaseModel):
    gene: str
    rsid: str | None = None
    clinvar_significance: str | None = None
    zygosity: str | None = None
    evidence_level: int = 0


class ApobFdbResponse(BaseModel):
    rsid: str
    gene: str = "APOB"
    protein: str = ""
    genotype: str | None = None
    clinvar_significance: str | None = None
    is_pathogenic: bool = False


class FhLdlPrsResponse(BaseModel):
    name: str = ""
    calibrated: bool = False
    percentile: float | None = None
    snps_used: int = 0
    snps_used_imputed: int = 0  # subset of snps_used scored from imputation (SW-C5)
    snps_total: int = 0
    coverage_fraction: float = 0.0
    coverage_tier: str = "typed_only"  # "typed_only" | "imputed"
    is_sufficient: bool = False
    source_study: str = ""
    source_pmid: str = ""
    pgs_id: str | None = None
    pgs_license: str | None = None
    development_method: str | None = None
    ancestry_mismatch: bool = False
    ancestry_warning_text: str | None = None
    evidence_level: int = 1


class FhAssessmentResponse(BaseModel):
    """Composed FH view."""

    assessment_status: Literal["available", "unavailable"] = "available"
    has_monogenic: bool
    monogenic: list[FhMonogenicResponse] = []
    apob_fdb: ApobFdbResponse | None = None
    ldl_prs: FhLdlPrsResponse | None = None
    criteria_context: dict[str, str] = {}
    research_use_only: bool = True


class FhRunResponse(BaseModel):
    findings_count: int
    ldl_prs_computed: bool
    apob_fdb_typed: bool


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
        return {}


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/assessment")
def get_fh_assessment(
    sample_id: int = Query(..., description="Sample ID"),
) -> FhAssessmentResponse:
    """Composed FH view: monogenic + APOB FDB + LDL-C PRS + criteria framing."""
    sample_engine = _get_sample_engine(sample_id)
    with sample_engine.connect() as conn:
        fdb_rows = conn.execute(
            sa.select(findings)
            .where(findings.c.module == "fh", findings.c.category == "fdb_variant")
            .order_by(findings.c.id)
        ).fetchall()
        prs_rows = conn.execute(
            sa.select(findings)
            .where(findings.c.module == "fh", findings.c.category == "prs")
            .order_by(findings.c.id)
        ).fetchall()

    fdb_source_withheld = any(
        not is_patient_presentable_finding_payload(row._mapping) for row in fdb_rows
    )
    prs_source_withheld = any(
        not is_patient_presentable_finding_payload(row._mapping) for row in prs_rows
    )
    fdb_row = next(
        (row for row in fdb_rows if is_patient_presentable_finding_payload(row._mapping)), None
    )
    prs_row = next(
        (row for row in prs_rows if is_patient_presentable_finding_payload(row._mapping)), None
    )

    monogenic_detection = detect_fh_monogenic_with_status(sample_engine)
    monogenic = [
        FhMonogenicResponse(
            gene=v.gene,
            rsid=v.rsid,
            clinvar_significance=v.clinvar_significance,
            zygosity=v.zygosity,
            evidence_level=v.evidence_level,
        )
        for v in monogenic_detection.variants
    ]

    apob_fdb = None
    if fdb_row is not None:
        d = _parse_detail(fdb_row)
        apob_fdb = ApobFdbResponse(
            rsid=d.get("rsid", "rs5742904"),
            protein=d.get("protein", ""),
            genotype=d.get("genotype"),
            clinvar_significance=d.get("clinvar_significance"),
            is_pathogenic=d.get("is_pathogenic", False),
        )

    ldl_prs = None
    if prs_row is not None:
        d = _parse_detail(prs_row)
        ldl_prs = FhLdlPrsResponse(
            name=d.get("name", ""),
            calibrated=d.get("calibrated", False),
            percentile=prs_row.prs_percentile,
            snps_used=d.get("snps_used", 0),
            snps_used_imputed=d.get("snps_used_imputed", 0),
            snps_total=d.get("snps_total", 0),
            coverage_fraction=d.get("coverage_fraction", 0.0),
            coverage_tier=d.get("coverage_tier", "typed_only"),
            is_sufficient=d.get("is_sufficient", False),
            source_study=d.get("source_study", ""),
            source_pmid=d.get("source_pmid", ""),
            pgs_id=d.get("pgs_id"),
            pgs_license=d.get("pgs_license"),
            development_method=d.get("development_method"),
            ancestry_mismatch=d.get("ancestry_mismatch", False),
            ancestry_warning_text=d.get("ancestry_warning_text"),
            evidence_level=prs_row.evidence_level or 1,
        )

    response = FhAssessmentResponse(
        has_monogenic=len(monogenic) > 0,
        monogenic=monogenic,
        apob_fdb=apob_fdb,
        ldl_prs=ldl_prs,
        criteria_context=FH_CRITERIA_CONTEXT,
    )
    dynamic_payload = {
        "monogenic": [item.model_dump(mode="json") for item in response.monogenic],
        "apob_fdb": response.apob_fdb.model_dump(mode="json") if response.apob_fdb else None,
        "ldl_prs": response.ldl_prs.model_dump(mode="json") if response.ldl_prs else None,
    }
    if (
        monogenic_detection.source_withheld
        or fdb_source_withheld
        or prs_source_withheld
        or not is_patient_presentable_response_payload(dynamic_payload)
    ):
        # Do not turn an unsafe combination into a clinical negative. The
        # assessment stays structurally valid but withholds all dynamic results
        # until the source records can be reviewed independently.
        return FhAssessmentResponse(
            assessment_status="unavailable",
            has_monogenic=False,
        )
    return response


@router.post("/run")
def run_fh_analysis(
    sample_id: int = Query(..., description="Sample ID"),
) -> FhRunResponse:
    """Compute + store the LDL-C PRS and APOB FDB findings for a sample."""
    from backend.analysis.ancestry import get_inferred_ancestry, get_top_ancestry_fraction
    from backend.analysis.fh import assess_fh, store_fh_findings
    from backend.analysis.pgs_bridge import get_pgs_scores_engine

    sample_engine = _get_sample_engine(sample_id)
    pgs_engine = get_pgs_scores_engine()
    try:
        inferred = get_inferred_ancestry(sample_engine)
        top_fraction = get_top_ancestry_fraction(sample_engine)
        assessment = assess_fh(
            sample_engine,
            pgs_engine,
            inferred,
            top_fraction,
            reference_engine=get_registry().reference_engine,
        )
        count = store_fh_findings(assessment, sample_engine)
    finally:
        if pgs_engine is not None:
            pgs_engine.dispose()

    return FhRunResponse(
        findings_count=count,
        ldl_prs_computed=assessment.ldl_prs is not None,
        apob_fdb_typed=assessment.apob_fdb is not None and assessment.apob_fdb.present,
    )
