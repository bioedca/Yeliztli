"""Cancer predisposition findings API (P3-13, P3-15, P3-17).

ClinVar P/LP extraction results from the 28-gene cancer panel — monogenic
pathogenic variants with accession, review stars, syndrome, and inheritance.
Cancer PRS (breast, prostate, colorectal, melanoma) with bootstrap CI gauges.
Module-specific disclaimer text (P3-17).

GET  /api/analysis/cancer/disclaimer                         — Cancer module disclaimer
GET  /api/analysis/cancer/variants?sample_id=N               — All cancer P/LP findings
GET  /api/analysis/cancer/gene/{gene_symbol}?sample_id=N     — Findings for a single gene
GET  /api/analysis/cancer/prs?sample_id=N                    — Cancer PRS results
POST /api/analysis/cancer/run?sample_id=N                    — Run/re-run extraction + PRS
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
from backend.disclaimers import CANCER_DISCLAIMER_TEXT, CANCER_DISCLAIMER_TITLE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis/cancer", tags=["cancer"])


# ── Response models ──────────────────────────────────────────────────


class CancerVariantResponse(BaseModel):
    """A single P/LP variant in the cancer panel."""

    rsid: str
    gene_symbol: str
    genotype: str | None = None
    zygosity: str | None = None
    clinvar_significance: str
    clinvar_accession: str | None = None
    clinvar_review_stars: int = 0
    clinvar_conditions: str | None = None
    syndromes: list[str] = []
    cancer_types: list[str] = []
    inheritance: str = "AD"
    evidence_level: int = 1
    cross_links: list[str] = []
    pmids: list[str] = []
    clinvar_low_penetrance_or_risk_allele: bool = False


class CancerVariantsListResponse(BaseModel):
    """All cancer P/LP findings for a sample."""

    items: list[CancerVariantResponse]
    total: int


class CancerPRSResponse(BaseModel):
    """A single cancer PRS result."""

    trait: str
    name: str
    calibrated: bool = True
    percentile: float | None = None
    z_score: float | None = None
    bootstrap_ci_lower: float | None = None
    bootstrap_ci_upper: float | None = None
    bootstrap_iterations: int = 0
    snps_used: int = 0
    snps_total: int = 0
    coverage_fraction: float = 0.0
    is_sufficient: bool = False
    source_ancestry: str = "EUR"
    source_study: str = ""
    source_pmid: str = ""
    sample_size: int = 0
    ancestry_mismatch: bool = False
    ancestry_warning_text: str | None = None
    evidence_level: int = 1
    research_use_only: bool = True
    # Per-PGS provenance + monogenic exclusion (SW-B3).
    pgs_id: str | None = None
    pgs_license: str | None = None
    development_method: str | None = None
    genome_build: str | None = None
    variants_number: int | None = None
    source_url: str | None = None
    monogenic_genes: list[str] = []
    monogenic_carrier_genes: list[str] = []
    monogenic_note: str | None = None


class CancerPRSListResponse(BaseModel):
    """All cancer PRS results for a sample."""

    items: list[CancerPRSResponse]
    total: int
    sufficient_count: int
    insufficient_traits: list[str]


class CancerDisclaimerResponse(BaseModel):
    """Cancer module disclaimer text (P3-17)."""

    title: str
    text: str


class CancerRunResponse(BaseModel):
    """Result of running cancer predisposition extraction + PRS."""

    findings_count: int
    panel_genes_checked: int
    variants_in_panel_genes: int
    prs_findings_count: int = 0
    prs_traits_computed: int = 0


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


def _fetch_cancer_findings(
    sample_engine: sa.Engine,
    gene_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch monogenic cancer variant findings from the sample DB.

    Scoped to ``category == "monogenic_variant"`` so PRS findings — which
    share ``module == "cancer"`` (category ``"prs"``) but carry no
    ``gene_symbol`` / ``rsid`` / ``clinvar_significance`` — never leak into
    the monogenic variant cards, where they would render as blank cards.
    PRS findings have their own endpoint (:func:`list_cancer_prs`).
    """
    with sample_engine.connect() as conn:
        stmt = (
            sa.select(findings)
            .where(
                findings.c.module == "cancer",
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
                "syndromes": detail.get("syndromes", []),
                "cancer_types": detail.get("cancer_types", []),
                "inheritance": detail.get("inheritance", "AD"),
                "evidence_level": row.evidence_level or 1,
                "cross_links": detail.get("cross_links", []),
                "pmids": pmids,
                "clinvar_low_penetrance_or_risk_allele": detail.get(
                    "clinvar_low_penetrance_or_risk_allele", False
                ),
            }
        )

    return result


def _findings_to_response(
    finding_rows: list[dict[str, Any]],
) -> list[CancerVariantResponse]:
    """Convert raw finding dicts to response models."""
    return [CancerVariantResponse(**f) for f in finding_rows]


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/disclaimer")
def get_cancer_disclaimer() -> CancerDisclaimerResponse:
    """Return cancer module disclaimer text (P3-17).

    Module-specific disclaimer for the cancer predisposition section,
    covering monogenic findings and PRS limitations.

    Example: ``GET /api/analysis/cancer/disclaimer``
    """
    return CancerDisclaimerResponse(
        title=CANCER_DISCLAIMER_TITLE,
        text=CANCER_DISCLAIMER_TEXT,
    )


@router.get("/variants", dependencies=[Depends(require_fresh_sample)])
def list_cancer_variants(
    sample_id: int = Query(..., description="Sample ID"),
) -> CancerVariantsListResponse:
    """List all cancer P/LP variant findings for a sample.

    Returns ClinVar Pathogenic and Likely pathogenic variants in the
    28-gene cancer predisposition panel, sorted by evidence level
    (highest first).

    Example: ``GET /api/analysis/cancer/variants?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)
    raw = _fetch_cancer_findings(sample_engine)
    items = _findings_to_response(raw)
    return CancerVariantsListResponse(items=items, total=len(items))


@router.get("/gene/{gene_symbol}", dependencies=[Depends(require_fresh_sample)])
def cancer_gene_detail(
    gene_symbol: str,
    sample_id: int = Query(..., description="Sample ID"),
) -> CancerVariantsListResponse:
    """Get cancer findings for a specific gene.

    Example: ``GET /api/analysis/cancer/gene/BRCA1?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)
    raw = _fetch_cancer_findings(sample_engine, gene_filter=gene_symbol)
    items = _findings_to_response(raw)
    return CancerVariantsListResponse(items=items, total=len(items))


@router.get("/prs", dependencies=[Depends(require_fresh_sample)])
def list_cancer_prs(
    sample_id: int = Query(..., description="Sample ID"),
) -> CancerPRSListResponse:
    """List cancer PRS results for a sample.

    Returns PRS findings (breast, prostate, colorectal, melanoma) with
    percentile, z-score, bootstrap CI, and ancestry mismatch status.
    Results are in the "Research Use Only" tier.

    Example: ``GET /api/analysis/cancer/prs?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)

    with sample_engine.connect() as conn:
        rows = conn.execute(
            sa.select(findings)
            .where(
                findings.c.module == "cancer",
                findings.c.category == "prs",
            )
            .order_by(findings.c.id)
        ).fetchall()

    items: list[CancerPRSResponse] = []
    for row in rows:
        detail: dict[str, Any] = {}
        if row.detail_json:
            try:
                detail = json.loads(row.detail_json)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Failed to parse detail_json for PRS finding id=%s", row.id)

        items.append(
            CancerPRSResponse(
                trait=detail.get("trait", ""),
                name=detail.get("name", ""),
                calibrated=detail.get("calibrated", True),
                percentile=row.prs_percentile,
                z_score=detail.get("z_score"),
                bootstrap_ci_lower=detail.get("bootstrap_ci_lower"),
                bootstrap_ci_upper=detail.get("bootstrap_ci_upper"),
                bootstrap_iterations=detail.get("bootstrap_iterations", 0),
                snps_used=detail.get("snps_used", 0),
                snps_total=detail.get("snps_total", 0),
                coverage_fraction=detail.get("coverage_fraction", 0.0),
                is_sufficient=detail.get("is_sufficient", False),
                source_ancestry=detail.get("source_ancestry", "EUR"),
                source_study=detail.get("source_study", ""),
                source_pmid=detail.get("source_pmid", ""),
                sample_size=detail.get("sample_size", 0),
                ancestry_mismatch=detail.get("ancestry_mismatch", False),
                ancestry_warning_text=detail.get("ancestry_warning_text"),
                evidence_level=row.evidence_level or 1,
                research_use_only=True,
                pgs_id=detail.get("pgs_id"),
                pgs_license=detail.get("pgs_license"),
                development_method=detail.get("development_method"),
                genome_build=detail.get("genome_build"),
                variants_number=detail.get("variants_number"),
                source_url=detail.get("source_url"),
                monogenic_genes=detail.get("monogenic_genes", []),
                monogenic_carrier_genes=detail.get("monogenic_carrier_genes", []),
                monogenic_note=detail.get("monogenic_note"),
            )
        )

    sufficient = [i for i in items if i.is_sufficient]
    insufficient = [i.trait for i in items if not i.is_sufficient]

    return CancerPRSListResponse(
        items=items,
        total=len(items),
        sufficient_count=len(sufficient),
        insufficient_traits=insufficient,
    )


@router.post("/run", dependencies=[Depends(require_fresh_sample)])
def run_cancer_analysis(
    sample_id: int = Query(..., description="Sample ID"),
) -> CancerRunResponse:
    """Run or re-run cancer predisposition extraction + PRS for a sample.

    Loads the curated panel, extracts ClinVar P/LP variants from
    annotated_variants, runs cancer PRS (breast, prostate, colorectal,
    melanoma), and stores all findings.

    Example: ``POST /api/analysis/cancer/run?sample_id=1``
    """
    from backend.analysis.cancer import (
        extract_cancer_variants,
        load_cancer_panel,
        store_cancer_findings,
    )
    from backend.analysis.cancer_prs import (
        load_cancer_prs_weights,
        resolve_cancer_prs_sex_context,
        run_cancer_prs,
        store_cancer_prs_findings,
    )

    sample_engine = _get_sample_engine(sample_id)
    reference_engine = get_registry().reference_engine

    # Monogenic extraction (P3-13)
    panel = load_cancer_panel()
    result = extract_cancer_variants(panel, sample_engine)
    # Pass the reference engine so findings gain the gnomAD gene-constraint
    # context badge (roadmap #12), matching the run_all dashboard path.
    count = store_cancer_findings(result, sample_engine, reference_engine)

    # PRS computation (P3-15) with ancestry mismatch check (P3-16)
    from backend.analysis.ancestry import get_inferred_ancestry, get_top_ancestry_fraction

    weight_sets = load_cancer_prs_weights()
    inferred_ancestry = get_inferred_ancestry(sample_engine)
    top_fraction = get_top_ancestry_fraction(sample_engine)

    sex_context = resolve_cancer_prs_sex_context(
        sample_engine,
        reference_engine=reference_engine,
        sample_id=sample_id,
    )
    prs_result = run_cancer_prs(
        weight_sets,
        sample_engine,
        inferred_ancestry=inferred_ancestry,
        top_ancestry_fraction=top_fraction,
        inferred_sex=sex_context,
    )
    prs_count = store_cancer_prs_findings(prs_result, sample_engine)

    return CancerRunResponse(
        findings_count=count,
        panel_genes_checked=result.panel_genes_checked,
        variants_in_panel_genes=result.variants_in_panel_genes,
        prs_findings_count=prs_count,
        prs_traits_computed=len(prs_result.results),
    )


# ── Breast absolute-risk overlay (SW-B8, opt-in) ─────────────────────


class AbsoluteRiskConsentResponse(BaseModel):
    """Result of setting the breast absolute-risk opt-in consent."""

    consented: bool


@router.post("/absolute-risk/consent", dependencies=[Depends(require_fresh_sample)])
def set_absolute_risk_consent(
    sample_id: int = Query(..., description="Sample ID"),
    consented: bool = Query(..., description="Opt in (true) or out (false)"),
) -> AbsoluteRiskConsentResponse:
    """Record opt-in/opt-out for the breast absolute-risk overlay (SW-B8).

    Example: ``POST /api/analysis/cancer/absolute-risk/consent?sample_id=1&consented=true``
    """
    from backend.analysis.breast_absolute_risk import set_consent

    # Validate the sample exists (and is fresh, via the dependency).
    _get_sample_engine(sample_id)
    set_consent(get_registry().reference_engine, sample_id, consented)
    return AbsoluteRiskConsentResponse(consented=consented)


@router.get("/absolute-risk", dependencies=[Depends(require_fresh_sample)])
def get_absolute_risk(
    sample_id: int = Query(..., description="Sample ID"),
) -> dict[str, Any]:
    """Return the opt-in breast absolute-risk overlay (SW-B8).

    Before opt-in this returns only the consent prompt + disclaimer (no risk
    figures). After opt-in the figures are sex-gated on the sample's inferred
    biological sex: female samples get the SEER female baseline + female BRCA
    penetrance, male samples get male-specific BRCA framing (the female figures are
    suppressed), and sex-unresolved samples get no numeric sex-specific figures —
    only a CanRisk / clinical-genetics handoff (gh #151).

    Example: ``GET /api/analysis/cancer/absolute-risk?sample_id=1``
    """
    from backend.analysis.breast_absolute_risk import (
        build_breast_absolute_risk,
        get_consent,
    )
    from backend.services.sex_inference import (
        get_recorded_biological_sex,
        infer_biological_sex,
    )

    sample_engine = _get_sample_engine(sample_id)
    reference_engine = get_registry().reference_engine
    consented = get_consent(reference_engine, sample_id)
    inferred_sex = infer_biological_sex(sample_engine)
    # Prefer the user-recorded biological sex over array inference (gh #254).
    recorded_sex = get_recorded_biological_sex(reference_engine, sample_id)
    return build_breast_absolute_risk(
        sample_engine,
        consented=consented,
        inferred_sex=inferred_sex,
        recorded_sex=recorded_sex,
    )
