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
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.analysis.pharmacogenomics import (
    classify_actionability,
    is_patient_presentable_finding_payload,
    is_patient_presentable_response_payload,
    is_prescribing_alert_withheld,
    patient_visible_finding_clause,
)
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
    # True when every gene-drug row for this drug is audit-only and held from
    # patient-specific prescribing output. Such a row intentionally has no
    # active CPIC tier in this response.
    prescribing_guidance_withheld: bool = False


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
    # ``withheld`` is a clinical-evidence hold, not an uncallable or
    # evaluated-as-normal result. Keeping it separate from ``not_assessed``
    # prevents a deliberately suppressed recommendation from being misread as
    # an array coverage failure.
    recommendation_status: Literal["available", "not_assessed", "withheld"] = "available"
    # True only for ``recommendation_status='not_assessed'``: the call was
    # Insufficient (uncallable on the array) or annotation has not run. A
    # withheld recommendation is deliberately a separate state, even when its
    # sample-specific fields are null.
    not_assessed: bool = False


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
                    patient_visible_finding_clause(findings.c),
                )
            )
            .order_by(findings.c.gene_symbol)
        )
        rows = conn.execute(stmt).fetchall()

    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not is_patient_presentable_finding_payload(row._mapping):
            continue
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


def _gene_summary_values(
    row: Any, detail: dict[str, Any]
) -> tuple[str | None, float | None, str | None]:
    """Return gene-level phenotype fields without leaking conservative alerts.

    Stored prescribing-alert rows intentionally keep the conservative phenotype
    because drug-specific recommendations are keyed to that safer phenotype. Gene
    cards and report coverage, however, summarize the called gene result itself.
    """
    phenotype = row.metabolizer_status
    activity_score = detail.get("activity_score")
    ehr_notation = detail.get("ehr_notation")

    if detail.get("conservative_alert") is True:
        phenotype = detail.get("called_phenotype") or phenotype
        called_activity_score = detail.get("called_activity_score")
        if called_activity_score is not None:
            activity_score = called_activity_score
        ehr_notation = detail.get("called_ehr_notation")
        if ehr_notation is None:
            ehr_notation = (
                f"{row.gene_symbol} {phenotype}" if row.gene_symbol and phenotype else None
            )

    return phenotype, activity_score, ehr_notation


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

    # Group by drug. A source classification is patient-facing only when at
    # least one gene-drug pair remains eligible for prescribing output; an
    # audit-only held pair cannot lend the drug an active CPIC tier.
    drugs: dict[str, dict[str, Any]] = {}
    for row in rows:
        drug = row.drug
        if drug not in drugs:
            drugs[drug] = {
                "genes": [],
                "classification": None,
                "has_available_guidance": False,
            }
        drugs[drug]["genes"].append(row.gene)
        if is_prescribing_alert_withheld(row.gene, row.drug):
            continue

        drugs[drug]["has_available_guidance"] = True
        # Track best (min) classification
        current = drugs[drug]["classification"]
        if row.classification and (current is None or row.classification < current):
            drugs[drug]["classification"] = row.classification

    items = [
        DrugListItem(
            drug=drug,
            genes=info["genes"],
            classification=info["classification"],
            prescribing_guidance_withheld=not info["has_available_guidance"],
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
        if is_prescribing_alert_withheld(gene, canonical_drug):
            # The reference row is audit provenance only. Check this before
            # selecting any sample finding so even an unmigrated legacy row
            # cannot leak a patient-specific recommendation through this API.
            gene_effects.append(
                GeneEffect(
                    gene=gene,
                    recommendation_status="withheld",
                )
            )
            continue

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
                    recommendation_status="available",
                )
            )
        else:
            # No sample finding — return guideline info only, flagged not_assessed
            # so the UI shows an explicit "not assessed" state rather than a bare
            # "CPIC Level {x}" card that reads as evaluated-and-normal. This happens
            # when the gene call was Insufficient (uncallable on the array) or
            # annotation hasn't run yet (#905).
            gene_info = gene_set[gene]
            gene_effects.append(
                GeneEffect(
                    gene=gene,
                    classification=gene_info["classification"],
                    guideline_url=gene_info["guideline_url"],
                    recommendation_status="not_assessed",
                    not_assessed=True,
                )
            )

    response = DrugLookupResponse(
        drug=canonical_drug,
        gene_effects=gene_effects,
    )
    # A tamoxifen lookup intentionally exposes a neutral ``withheld`` state,
    # rather than guidance, so retain that established association contract.
    # Other drug responses join source findings into one patient-facing DTO and
    # must fail closed if their decoded fields form a held pair.
    if not any(effect.recommendation_status == "withheld" for effect in response.gene_effects):
        dynamic_records = []
        for effect in response.gene_effects:
            record = effect.model_dump(mode="json", exclude_none=True)
            record["drug"] = canonical_drug
            dynamic_records.append(record)
        if not is_patient_presentable_response_payload(dynamic_records):
            return DrugLookupResponse(drug=canonical_drug, gene_effects=[])
    return response


@router.get("/genes", dependencies=[Depends(require_fresh_sample)])
def gene_results(
    sample_id: int = Query(..., description="Sample ID"),
) -> GeneSummaryResponse:
    """Return all pharmacogenomics gene results for a sample.

    Groups patient-visible findings by gene_symbol (taking the first finding
    per gene for diplotype / phenotype / confidence) and fetches associated
    drugs from CPIC guidelines. Intended for metabolizer phenotype cards on
    the pharmacogenomics overview page.

    Example: ``GET /api/analysis/pharma/genes?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)

    # 1. Fetch all pharmacogenomics findings for this sample
    with sample_engine.connect() as conn:
        stmt = (
            sa.select(findings)
            .where(
                findings.c.module == "pharmacogenomics",
                patient_visible_finding_clause(findings.c),
            )
            .order_by(findings.c.gene_symbol, findings.c.id)
        )
        rows = conn.execute(stmt).fetchall()

    # 2. Group by gene_symbol — first finding per gene wins
    gene_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not is_patient_presentable_finding_payload(row._mapping):
            continue
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

        phenotype, activity_score, ehr_notation = _gene_summary_values(row, detail)
        gene_map[gene] = {
            "diplotype": row.diplotype,
            "phenotype": phenotype,
            "call_confidence": detail.get("call_confidence"),
            "confidence_note": detail.get("confidence_note"),
            "activity_score": activity_score,
            "ehr_notation": ehr_notation,
            "evidence_level": row.evidence_level,
            "involved_rsids": detail.get("involved_rsids", []),
            "gene_caveat": detail.get("gene_caveat"),
            "source_drug": row.drug,
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
            if is_prescribing_alert_withheld(dr.gene, dr.drug):
                continue
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

    dynamic_records: list[dict[str, Any]] = []
    drug_associations: list[dict[str, str]] = []
    for item in items:
        record = item.model_dump(mode="json", exclude_none=True)
        record.pop("drugs", None)
        source_drug = gene_map[item.gene]["source_drug"]
        if source_drug:
            record["drug"] = source_drug
        dynamic_records.append(record)
        drug_associations.extend({"gene": item.gene, "drug": drug} for drug in item.drugs)
    if not is_patient_presentable_response_payload(
        {"records": dynamic_records, "drug_associations": drug_associations}
    ):
        return GeneSummaryResponse(items=[], total=0)
    return GeneSummaryResponse(items=items, total=len(items))


@router.get("/report", dependencies=[Depends(require_fresh_sample)])
def medication_safety_report(
    sample_id: int = Query(..., description="Sample ID"),
) -> MedicationSafetyReportResponse:
    """Consolidated drug-centric medication-safety report for a sample (SW-E4).

    Aggregates every active pharmacogenomics prescribing alert into a single
    report organized by drug, with CPIC-standard phenotype terms, per-gene
    coverage / call-confidence, a coarse actionability flag (attention-worthy
    results first), and a report-level reference-bias disclosure. Pairs held
    from clinical output are excluded even when an older stored alert remains.

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
                    patient_visible_finding_clause(findings.c),
                )
            )
            .order_by(findings.c.gene_symbol, findings.c.drug, findings.c.id)
        )
        rows = conn.execute(stmt).fetchall()

    # 2. Walk findings once, building per-gene coverage summaries and grouping
    #    gene effects by drug.
    gene_summaries: dict[str, GeneCoverageSummary] = {}
    gene_summary_drugs: dict[str, str] = {}
    drug_groups: dict[str, dict[str, Any]] = {}

    for row in rows:
        if not is_patient_presentable_finding_payload(row._mapping):
            continue
        gene = row.gene_symbol
        drug = row.drug
        if gene is None or drug is None:
            continue
        # A clinical-evidence hold is an output policy, not merely a migration
        # detail. Filter stale or otherwise retained target rows here so the
        # consolidated report cannot re-expose a withheld recommendation.
        if is_prescribing_alert_withheld(gene, drug):
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
            phenotype, activity_score, ehr_notation = _gene_summary_values(row, detail)
            gene_summaries[gene] = GeneCoverageSummary(
                gene=gene,
                diplotype=row.diplotype,
                phenotype=phenotype,
                call_confidence=detail.get("call_confidence"),
                confidence_note=detail.get("confidence_note"),
                coverage=coverage,
                activity_score=activity_score,
                ehr_notation=ehr_notation,
                evidence_level=row.evidence_level,
                gene_caveat=detail.get("gene_caveat"),
                indeterminate_alleles=detail.get("indeterminate_alleles", []),
            )
            gene_summary_drugs[gene] = drug

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

    response = MedicationSafetyReportResponse(
        reference_bias_disclosure=MEDICATION_SAFETY_REFERENCE_BIAS,
        genes_assessed=len(gene_coverage),
        drugs_assessed=len(drug_entries),
        actionable_drug_count=actionable_drug_count,
        gene_coverage=gene_coverage,
        drugs=drug_entries,
    )
    # The report joins independently safe findings into coverage and drug
    # sections. Evaluate only that dynamic finding-derived content; the static
    # reference-bias disclosure intentionally discusses pharmacogenes and is
    # not patient-specific prescribing evidence.
    dynamic_records: list[dict[str, Any]] = []
    for item in response.gene_coverage:
        record = item.model_dump(mode="json", exclude_none=True)
        record["drug"] = gene_summary_drugs[item.gene]
        dynamic_records.append(record)
    for drug_entry in response.drugs:
        for effect in drug_entry.gene_effects:
            record = effect.model_dump(mode="json", exclude_none=True)
            record["drug"] = drug_entry.drug
            dynamic_records.append(record)

    if not is_patient_presentable_response_payload(dynamic_records):
        return MedicationSafetyReportResponse(
            reference_bias_disclosure=MEDICATION_SAFETY_REFERENCE_BIAS,
            genes_assessed=0,
            drugs_assessed=0,
            actionable_drug_count=0,
            gene_coverage=[],
            drugs=[],
        )
    return response
