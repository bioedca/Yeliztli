"""Drug lookup API (P3-05).

Given a drug name, returns relevant pharmacogenes with the user's genotype
effect: star allele calls, metabolizer phenotype, call confidence state,
CPIC classification level, and prescribing recommendation.

GET  /api/analysis/pharma/drugs           — List all CPIC drugs
GET  /api/analysis/pharma/drug/{drug_name} — Drug detail with user genotype
GET  /api/analysis/pharma/genes?sample_id=N — Per-gene star-allele results (metabolizer cards)
GET  /api/analysis/pharma/report?sample_id=N — Consolidated medication-safety report (SW-E4)
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.analysis.pharmacogenomics import classify_actionability
from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import cpic_guidelines, findings, samples
from backend.disclaimers import MEDICATION_SAFETY_REFERENCE_BIAS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis/pharma", tags=["pharmacogenomics"])


# ── Response models ──────────────────────────────────────────────────


class DrugListItem(BaseModel):
    """Summary of a drug in the CPIC database."""

    drug: str
    genes: list[str]
    classification: str | None = None  # best (min) CPIC level across genes


class DrugListResponse(BaseModel):
    """List of all CPIC drugs with associated genes."""

    items: list[DrugListItem]
    total: int


class GeneEffect(BaseModel):
    """Per-gene genotype effect for a specific drug."""

    gene: str
    diplotype: str | None = None
    metabolizer_status: str | None = None
    recommendation: str | None = None
    classification: str | None = None  # CPIC level: A, B, C, D
    guideline_url: str | None = None
    call_confidence: str | None = None  # Complete / Partial / Insufficient
    confidence_note: str | None = None
    evidence_level: int | None = None  # 1-4 stars
    activity_score: float | None = None
    ehr_notation: str | None = None
    involved_rsids: list[str] = []
    gene_caveat: str | None = None  # interpretive caveat (e.g. DPYD fatal-toxicity)


class DrugLookupResponse(BaseModel):
    """Full drug detail with per-gene genotype effects for a sample."""

    drug: str
    gene_effects: list[GeneEffect]


class GeneSummary(BaseModel):
    """Per-gene star-allele result for metabolizer phenotype cards."""

    gene: str
    diplotype: str | None = None
    phenotype: str | None = None
    call_confidence: str | None = None
    confidence_note: str | None = None
    activity_score: float | None = None
    ehr_notation: str | None = None
    evidence_level: int | None = None
    involved_rsids: list[str] = []
    drugs: list[str] = []
    gene_caveat: str | None = None  # interpretive caveat (e.g. DPYD fatal-toxicity)


class GeneSummaryResponse(BaseModel):
    """List of per-gene star-allele results for a sample."""

    items: list[GeneSummary]
    total: int


# ── Medication-safety report models (SW-E4) ──────────────────────────


class CoverageInfo(BaseModel):
    """SNP defining-position coverage for a pharmacogene.

    ``assessed`` of ``total`` defining array positions were genotyped and called.
    This is SNP-level coverage only — it cannot reflect copy-number or
    gene-conversion alleles (see the report-level reference-bias disclosure).
    """

    assessed: int
    total: int


class ReportGeneEffect(BaseModel):
    """A single gene's effect on a drug within the medication-safety report."""

    gene: str
    diplotype: str | None = None
    phenotype: str | None = None  # CPIC-standard phenotype term
    recommendation: str | None = None
    classification: str | None = None  # CPIC level: A, B, C, D
    guideline_url: str | None = None
    call_confidence: str | None = None  # Complete / Partial / Insufficient
    confidence_note: str | None = None
    evidence_level: int | None = None  # 1-4 stars
    activity_score: float | None = None
    ehr_notation: str | None = None
    coverage: CoverageInfo | None = None
    actionability: str  # actionable / routine / indeterminate
    gene_caveat: str | None = None


class DrugSafetyEntry(BaseModel):
    """All gene effects for one drug, with a drug-level actionability flag."""

    drug: str
    actionable: bool  # any gene effect is actionable
    gene_effects: list[ReportGeneEffect]


class GeneCoverageSummary(BaseModel):
    """Per-gene coverage / call-confidence summary for the report header."""

    gene: str
    diplotype: str | None = None
    phenotype: str | None = None
    call_confidence: str | None = None
    confidence_note: str | None = None
    coverage: CoverageInfo | None = None
    activity_score: float | None = None
    ehr_notation: str | None = None
    evidence_level: int | None = None
    gene_caveat: str | None = None
    # Star alleles that could not be excluded — a defining variant was not assayed
    # on the array (e.g. the UGT1A1*28 TA-repeat). SW-E1.
    indeterminate_alleles: list[str] = []


class MedicationSafetyReportResponse(BaseModel):
    """Consolidated drug-centric medication-safety report for a sample (SW-E4)."""

    reference_bias_disclosure: str
    genes_assessed: int
    drugs_assessed: int
    actionable_drug_count: int
    gene_coverage: list[GeneCoverageSummary]
    drugs: list[DrugSafetyEntry]


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


def _fetch_drug_guidelines(drug_name: str) -> list[dict[str, Any]]:
    """Fetch all CPIC guideline rows for a drug (case-insensitive)."""
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        stmt = (
            sa.select(
                cpic_guidelines.c.gene,
                cpic_guidelines.c.drug,
                cpic_guidelines.c.phenotype,
                cpic_guidelines.c.recommendation,
                cpic_guidelines.c.classification,
                cpic_guidelines.c.guideline_url,
            )
            .where(sa.func.lower(cpic_guidelines.c.drug) == drug_name.lower())
            .order_by(cpic_guidelines.c.gene, cpic_guidelines.c.phenotype)
        )
        rows = conn.execute(stmt).fetchall()

    return [
        {
            "gene": row.gene,
            "drug": row.drug,
            "phenotype": row.phenotype,
            "recommendation": row.recommendation,
            "classification": row.classification,
            "guideline_url": row.guideline_url,
        }
        for row in rows
    ]


def _fetch_sample_findings(sample_engine: sa.Engine, drug_name: str) -> dict[str, dict[str, Any]]:
    """Fetch pharmacogenomics findings for a drug from the sample DB.

    Returns a dict keyed by gene_symbol with the finding data.
    """
    with sample_engine.connect() as conn:
        stmt = (
            sa.select(findings)
            .where(
                sa.and_(
                    findings.c.module == "pharmacogenomics",
                    findings.c.category == "prescribing_alert",
                    sa.func.lower(findings.c.drug) == drug_name.lower(),
                )
            )
            .order_by(findings.c.gene_symbol)
        )
        rows = conn.execute(stmt).fetchall()

    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        gene = row.gene_symbol
        detail: dict[str, Any] = {}
        if row.detail_json:
            try:
                detail = json.loads(row.detail_json)
            except (json.JSONDecodeError, TypeError):
                pass

        result[gene] = {
            "diplotype": row.diplotype,
            "metabolizer_status": row.metabolizer_status,
            "evidence_level": row.evidence_level,
            "recommendation": detail.get("recommendation"),
            "classification": detail.get("classification"),
            "guideline_url": detail.get("guideline_url"),
            "call_confidence": detail.get("call_confidence"),
            "confidence_note": detail.get("confidence_note"),
            "activity_score": detail.get("activity_score"),
            "ehr_notation": detail.get("ehr_notation"),
            "involved_rsids": detail.get("involved_rsids", []),
            "gene_caveat": detail.get("gene_caveat"),
        }

    return result


def _parse_coverage(detail: dict[str, Any]) -> CoverageInfo | None:
    """Build CoverageInfo from a finding's detail_json, tolerating older findings.

    Returns None when the finding predates SW-E4 coverage persistence or the
    coverage block is malformed, so the report degrades gracefully.
    """
    cov = detail.get("coverage")
    if not isinstance(cov, dict):
        return None
    assessed = cov.get("assessed")
    total = cov.get("total")
    if not isinstance(assessed, int) or not isinstance(total, int):
        return None
    return CoverageInfo(assessed=assessed, total=total)


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/drugs")
def list_drugs() -> DrugListResponse:
    """List all drugs with CPIC guidelines.

    Returns each drug with its associated genes and the best (lowest)
    CPIC classification level.

    Example: ``GET /api/analysis/pharma/drugs``
    """
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        stmt = (
            sa.select(
                cpic_guidelines.c.drug,
                cpic_guidelines.c.gene,
                cpic_guidelines.c.classification,
            )
            .group_by(cpic_guidelines.c.drug, cpic_guidelines.c.gene)
            .order_by(cpic_guidelines.c.drug, cpic_guidelines.c.gene)
        )
        rows = conn.execute(stmt).fetchall()

    # Group by drug
    drugs: dict[str, dict[str, Any]] = {}
    for row in rows:
        drug = row.drug
        if drug not in drugs:
            drugs[drug] = {"genes": [], "classification": row.classification}
        drugs[drug]["genes"].append(row.gene)
        # Track best (min) classification
        current = drugs[drug]["classification"]
        if row.classification and (current is None or row.classification < current):
            drugs[drug]["classification"] = row.classification

    items = [
        DrugListItem(
            drug=drug,
            genes=info["genes"],
            classification=info["classification"],
        )
        for drug, info in sorted(drugs.items())
    ]

    return DrugListResponse(items=items, total=len(items))


@router.get("/drug/{drug_name}", dependencies=[Depends(require_fresh_sample)])
def drug_lookup(
    drug_name: str,
    sample_id: int = Query(..., description="Sample ID"),
) -> DrugLookupResponse:
    """Look up a drug and return relevant pharmacogenes with user genotype effect.

    For each gene associated with the drug in CPIC guidelines, returns the
    user's star-allele diplotype, metabolizer phenotype, call confidence state,
    CPIC classification, and prescribing recommendation.

    The response combines CPIC reference data (guidelines) with per-sample
    findings (star-allele calls stored by the pharmacogenomics module).

    Example: ``GET /api/analysis/pharma/drug/clopidogrel?sample_id=1``
    """
    # 1. Look up drug in CPIC guidelines (reference.db)
    guidelines = _fetch_drug_guidelines(drug_name)
    if not guidelines:
        raise HTTPException(
            status_code=404,
            detail=f"No CPIC guidelines found for drug '{drug_name}'.",
        )

    # Canonical drug name from DB (preserves case)
    canonical_drug = guidelines[0]["drug"]

    # Collect unique genes for this drug
    gene_set: dict[str, dict[str, Any]] = {}
    for g in guidelines:
        gene = g["gene"]
        if gene not in gene_set:
            gene_set[gene] = {
                "classification": g["classification"],
                "guideline_url": g["guideline_url"],
            }

    # 2. Look up sample-specific findings
    sample_engine = _get_sample_engine(sample_id)
    sample_findings = _fetch_sample_findings(sample_engine, drug_name)

    # 3. Build per-gene effects
    gene_effects: list[GeneEffect] = []
    for gene in sorted(gene_set):
        finding = sample_findings.get(gene)

        if finding:
            # User has a finding for this gene-drug pair
            gene_effects.append(
                GeneEffect(
                    gene=gene,
                    diplotype=finding["diplotype"],
                    metabolizer_status=finding["metabolizer_status"],
                    recommendation=finding["recommendation"],
                    classification=finding["classification"],
                    guideline_url=finding["guideline_url"],
                    call_confidence=finding["call_confidence"],
                    confidence_note=finding["confidence_note"],
                    evidence_level=finding["evidence_level"],
                    activity_score=finding["activity_score"],
                    ehr_notation=finding["ehr_notation"],
                    involved_rsids=finding["involved_rsids"],
                    gene_caveat=finding["gene_caveat"],
                )
            )
        else:
            # No sample finding — return gene info from guidelines only
            # This happens when the gene call was Insufficient or annotation
            # hasn't been run yet
            gene_info = gene_set[gene]

            # Try to find a matching guideline recommendation for this gene
            # using the default/normal phenotype
            gene_effects.append(
                GeneEffect(
                    gene=gene,
                    classification=gene_info["classification"],
                    guideline_url=gene_info["guideline_url"],
                )
            )

    return DrugLookupResponse(
        drug=canonical_drug,
        gene_effects=gene_effects,
    )


@router.get("/genes", dependencies=[Depends(require_fresh_sample)])
def gene_results(
    sample_id: int = Query(..., description="Sample ID"),
) -> GeneSummaryResponse:
    """Return all pharmacogenomics gene results for a sample.

    Groups findings by gene_symbol (taking the first finding per gene for
    diplotype / phenotype / confidence) and fetches associated drugs from
    CPIC guidelines.  Intended for metabolizer phenotype cards on the
    pharmacogenomics overview page.

    Example: ``GET /api/analysis/pharma/genes?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)

    # 1. Fetch all pharmacogenomics findings for this sample
    with sample_engine.connect() as conn:
        stmt = (
            sa.select(findings)
            .where(findings.c.module == "pharmacogenomics")
            .order_by(findings.c.gene_symbol, findings.c.id)
        )
        rows = conn.execute(stmt).fetchall()

    # 2. Group by gene_symbol — first finding per gene wins
    gene_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        gene = row.gene_symbol
        if gene is None:
            continue
        if gene in gene_map:
            continue

        detail: dict[str, Any] = {}
        if row.detail_json:
            try:
                detail = json.loads(row.detail_json)
            except (json.JSONDecodeError, TypeError):
                pass

        gene_map[gene] = {
            "diplotype": row.diplotype,
            "phenotype": row.metabolizer_status,
            "call_confidence": detail.get("call_confidence"),
            "confidence_note": detail.get("confidence_note"),
            "activity_score": detail.get("activity_score"),
            "ehr_notation": detail.get("ehr_notation"),
            "evidence_level": row.evidence_level,
            "involved_rsids": detail.get("involved_rsids", []),
            "gene_caveat": detail.get("gene_caveat"),
        }

    # 3. Fetch drugs for each gene from CPIC guidelines
    if gene_map:
        registry = get_registry()
        gene_list = list(gene_map.keys())
        with registry.reference_engine.connect() as conn:
            stmt = (
                sa.select(
                    cpic_guidelines.c.gene,
                    cpic_guidelines.c.drug,
                )
                .where(cpic_guidelines.c.gene.in_(gene_list))
                .distinct()
                .order_by(cpic_guidelines.c.gene, cpic_guidelines.c.drug)
            )
            drug_rows = conn.execute(stmt).fetchall()

        gene_drugs: dict[str, list[str]] = {}
        for dr in drug_rows:
            gene_drugs.setdefault(dr.gene, []).append(dr.drug)
    else:
        gene_drugs = {}

    # 4. Build response
    items: list[GeneSummary] = []
    for gene in sorted(gene_map):
        info = gene_map[gene]
        items.append(
            GeneSummary(
                gene=gene,
                diplotype=info["diplotype"],
                phenotype=info["phenotype"],
                call_confidence=info["call_confidence"],
                confidence_note=info["confidence_note"],
                activity_score=info["activity_score"],
                ehr_notation=info["ehr_notation"],
                evidence_level=info["evidence_level"],
                involved_rsids=info["involved_rsids"],
                drugs=gene_drugs.get(gene, []),
                gene_caveat=info["gene_caveat"],
            )
        )

    return GeneSummaryResponse(items=items, total=len(items))


@router.get("/report", dependencies=[Depends(require_fresh_sample)])
def medication_safety_report(
    sample_id: int = Query(..., description="Sample ID"),
) -> MedicationSafetyReportResponse:
    """Consolidated drug-centric medication-safety report for a sample (SW-E4).

    Aggregates every stored pharmacogenomics prescribing alert into a single
    report organized by drug, with CPIC-standard phenotype terms, per-gene
    coverage / call-confidence, a coarse actionability flag (attention-worthy
    results first), and a report-level reference-bias disclosure.

    This endpoint is a read-only re-presentation of existing findings — it never
    creates findings or changes any phenotype / evidence level / recommendation.

    Example: ``GET /api/analysis/pharma/report?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)

    # 1. Fetch all stored prescribing-alert findings for this sample.
    with sample_engine.connect() as conn:
        stmt = (
            sa.select(findings)
            .where(
                sa.and_(
                    findings.c.module == "pharmacogenomics",
                    findings.c.category == "prescribing_alert",
                )
            )
            .order_by(findings.c.gene_symbol, findings.c.drug, findings.c.id)
        )
        rows = conn.execute(stmt).fetchall()

    # 2. Walk findings once, building per-gene coverage summaries and grouping
    #    gene effects by drug.
    gene_summaries: dict[str, GeneCoverageSummary] = {}
    drug_groups: dict[str, dict[str, Any]] = {}

    for row in rows:
        gene = row.gene_symbol
        drug = row.drug
        if gene is None or drug is None:
            continue

        detail: dict[str, Any] = {}
        if row.detail_json:
            try:
                detail = json.loads(row.detail_json)
            except (json.JSONDecodeError, TypeError):
                detail = {}

        coverage = _parse_coverage(detail)

        # First finding per gene wins for the coverage summary (mirrors /genes).
        if gene not in gene_summaries:
            gene_summaries[gene] = GeneCoverageSummary(
                gene=gene,
                diplotype=row.diplotype,
                phenotype=row.metabolizer_status,
                call_confidence=detail.get("call_confidence"),
                confidence_note=detail.get("confidence_note"),
                coverage=coverage,
                activity_score=detail.get("activity_score"),
                ehr_notation=detail.get("ehr_notation"),
                evidence_level=row.evidence_level,
                gene_caveat=detail.get("gene_caveat"),
                indeterminate_alleles=detail.get("indeterminate_alleles", []),
            )

        recommendation = detail.get("recommendation")
        effect = ReportGeneEffect(
            gene=gene,
            diplotype=row.diplotype,
            phenotype=row.metabolizer_status,
            recommendation=recommendation,
            classification=detail.get("classification"),
            guideline_url=detail.get("guideline_url"),
            call_confidence=detail.get("call_confidence"),
            confidence_note=detail.get("confidence_note"),
            evidence_level=row.evidence_level,
            activity_score=detail.get("activity_score"),
            ehr_notation=detail.get("ehr_notation"),
            coverage=coverage,
            actionability=classify_actionability(recommendation),
            gene_caveat=detail.get("gene_caveat"),
        )

        # Group by drug (case-insensitive key; keep first-seen canonical name).
        key = drug.lower()
        group = drug_groups.get(key)
        if group is None:
            group = {"drug": drug, "effects": {}}
            drug_groups[key] = group
        # One effect per gene per drug (first finding wins on duplicates).
        group["effects"].setdefault(gene, effect)

    # 3. Assemble drug entries; sort actionable-first, then by drug name.
    drug_entries: list[DrugSafetyEntry] = []
    for group in drug_groups.values():
        gene_effects = [group["effects"][g] for g in sorted(group["effects"])]
        actionable = any(e.actionability == "actionable" for e in gene_effects)
        drug_entries.append(
            DrugSafetyEntry(
                drug=group["drug"],
                actionable=actionable,
                gene_effects=gene_effects,
            )
        )
    drug_entries.sort(key=lambda d: (not d.actionable, d.drug.lower()))

    gene_coverage = [gene_summaries[g] for g in sorted(gene_summaries)]
    actionable_drug_count = sum(1 for d in drug_entries if d.actionable)

    return MedicationSafetyReportResponse(
        reference_bias_disclosure=MEDICATION_SAFETY_REFERENCE_BIAS,
        genes_assessed=len(gene_coverage),
        drugs_assessed=len(drug_entries),
        actionable_drug_count=actionable_drug_count,
        gene_coverage=gene_coverage,
        drugs=drug_entries,
    )
