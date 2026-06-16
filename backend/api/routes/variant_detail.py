"""Variant detail API (P2-20, P3-26).

Single variant endpoint returning all annotations, all VEP transcripts,
gene-phenotype records (including OMIM links), and evidence conflict details.

P3-26: Includes ``ancestry_matched_af`` and ``ancestry_matched_population``
fields based on the sample's inferred ancestry from PCA projection.

GET  /api/variants/{rsid}  — Full variant detail with all annotations
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.analysis.alphamissense import alphamissense_badge_for_variant
from backend.analysis.ancestry import get_ancestry_matched_af_column, get_inferred_ancestry
from backend.annotation.mondo_hpo import lookup_gene_phenotypes
from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import annotated_variants, samples

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/variants",
    tags=["variant-detail"],
    dependencies=[Depends(require_fresh_sample)],
)

# ── Response models ──────────────────────────────────────────────────

# In-silico tools used for evidence conflict assessment.
# Numeric thresholds mirror the canonical dbnsfp._meta_axis convention so the
# displayed "N of M tools predict deleterious" list cannot drift from the
# ensemble scorer: REVEL ≥ 0.5, MetaSVM > 0, MetaLR > 0.5.  MetaSVM is a signed
# SVM decision score (deleterious > 0); MetaLR is a [0,1] logistic-regression
# probability (deleterious > 0.5) — they do NOT share a threshold.
_INSILICO_TOOLS = ["sift_pred", "polyphen2_hsvar_pred", "metasvm", "metalr", "revel"]
_DELETERIOUS_PREDS = {"D", "probably_damaging"}
_REVEL_THRESHOLD = 0.5
_METALR_THRESHOLD = 0.5


class TranscriptAnnotation(BaseModel):
    """VEP annotation for a single transcript."""

    transcript_id: str | None = None
    gene_symbol: str | None = None
    consequence: str | None = None
    hgvs_coding: str | None = None
    hgvs_protein: str | None = None
    strand: str | None = None
    exon_number: int | None = None
    intron_number: int | None = None
    mane_select: bool = False


class GenePhenotypeRecord(BaseModel):
    """Gene-phenotype association from MONDO/HPO or OMIM."""

    gene_symbol: str
    disease_name: str
    disease_id: str | None = None
    source: str  # "mondo_hpo" or "omim"
    hpo_terms: list[str] | None = None
    inheritance: str | None = None
    omim_link: str | None = None


class EvidenceConflictDetail(BaseModel):
    """Detailed evidence conflict information for UI rendering."""

    has_conflict: bool = False
    clinvar_significance: str | None = None
    clinvar_review_stars: int | None = None
    clinvar_accession: str | None = None
    deleterious_count: int | None = None
    total_tools_assessed: int = 0
    deleterious_tools: list[str] = []
    cadd_phred: float | None = None
    summary: str | None = None


class VariantDetailResponse(BaseModel):
    """Full variant detail with all annotations, transcripts, and phenotypes."""

    # Core
    rsid: str
    chrom: str
    pos: int
    ref: str | None = None
    alt: str | None = None
    genotype: str | None = None
    zygosity: str | None = None

    # VEP (best transcript — stored in annotated_variants)
    gene_symbol: str | None = None
    transcript_id: str | None = None
    consequence: str | None = None
    hgvs_coding: str | None = None
    hgvs_protein: str | None = None
    strand: str | None = None
    exon_number: int | None = None
    intron_number: int | None = None
    mane_select: bool | None = None

    # ClinVar
    clinvar_significance: str | None = None
    clinvar_review_stars: int | None = None
    clinvar_accession: str | None = None
    clinvar_conditions: str | None = None

    # gnomAD
    gnomad_af_global: float | None = None
    gnomad_af_afr: float | None = None
    gnomad_af_amr: float | None = None
    gnomad_af_eas: float | None = None
    gnomad_af_eur: float | None = None
    gnomad_af_fin: float | None = None
    gnomad_af_sas: float | None = None
    gnomad_af_popmax: float | None = None
    gnomad_homozygous_count: int | None = None
    rare_flag: bool | None = None
    ultra_rare_flag: bool | None = None
    # P3-26: Ancestry-matched allele frequency
    ancestry_matched_af: float | None = None
    ancestry_matched_population: str | None = None

    # dbNSFP
    cadd_phred: float | None = None
    sift_score: float | None = None
    sift_pred: str | None = None
    polyphen2_hsvar_score: float | None = None
    polyphen2_hsvar_pred: str | None = None
    revel: float | None = None
    mutpred2: float | None = None
    vest4: float | None = None
    metasvm: float | None = None
    metalr: float | None = None
    gerp_rs: float | None = None
    phylop: float | None = None
    mpc: float | None = None
    primateai: float | None = None

    # AlphaMissense (context-only complement to REVEL)
    alphamissense_pathogenicity: float | None = None
    alphamissense_class: str | None = None
    alphamissense_badge: dict[str, Any] | None = None

    # dbSNP
    dbsnp_build: int | None = None
    dbsnp_rsid_current: str | None = None
    dbsnp_validation: str | None = None

    # Gene-phenotype (from annotated_variants)
    disease_name: str | None = None
    disease_id: str | None = None
    phenotype_source: str | None = None
    hpo_terms: str | None = None
    inheritance_pattern: str | None = None

    # Ensemble / conflict
    deleterious_count: int | None = None
    deleterious_total_assessed: int | None = None
    evidence_conflict: bool | None = None
    ensemble_pathogenic: bool | None = None
    annotation_coverage: int | None = None

    # P4-19: GRCh38 liftover coordinates
    chrom_grch38: str | None = None
    pos_grch38: int | None = None

    # ── Extended detail fields (P2-20) ────────────────────────────────
    transcripts: list[TranscriptAnnotation] = []
    gene_phenotypes: list[GenePhenotypeRecord] = []
    evidence_conflict_detail: EvidenceConflictDetail | None = None


# ── Helpers ──────────────────────────────────────────────────────────

_TABLE = annotated_variants

_VEP_COLS = (
    "rsid, gene_symbol, transcript_id, consequence, "
    "hgvs_coding, hgvs_protein, strand, exon_number, "
    "intron_number, mane_select"
)

# Batch size for VEP IN clause
_IN_BATCH_SIZE = 500


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


def _fetch_all_transcripts(rsid: str) -> list[TranscriptAnnotation]:
    """Fetch all VEP transcripts for an rsid from the VEP bundle DB.

    Returns an empty list if the VEP bundle is unavailable.
    """
    registry = get_registry()
    try:
        vep_engine = registry.vep_engine
    except Exception as exc:
        logger.debug("VEP bundle not available for transcript lookup: %s", exc)
        return []

    try:
        with vep_engine.connect() as conn:
            stmt = sa.text(
                f"SELECT {_VEP_COLS} FROM vep_annotations "  # noqa: S608
                f"WHERE rsid = :rsid"
            )
            rows = conn.execute(stmt, {"rsid": rsid}).fetchall()
    except Exception as exc:
        # Strip CR/LF so a crafted rsid (a URL path param) cannot forge log
        # lines (CodeQL py/log-injection).
        safe_rsid = rsid.replace("\r", "").replace("\n", "")
        logger.debug("VEP bundle query failed for %s: %s", safe_rsid, exc)
        return []

    return [
        TranscriptAnnotation(
            transcript_id=row.transcript_id,
            gene_symbol=row.gene_symbol,
            consequence=row.consequence,
            hgvs_coding=row.hgvs_coding,
            hgvs_protein=row.hgvs_protein,
            strand=row.strand,
            exon_number=row.exon_number,
            intron_number=row.intron_number,
            mane_select=bool(row.mane_select),
        )
        for row in rows
    ]


def _fetch_gene_phenotypes(gene_symbol: str | None) -> list[GenePhenotypeRecord]:
    """Fetch all gene-phenotype associations for a gene from reference.db.

    Routes through :func:`lookup_gene_phenotypes` rather than reading the
    ``gene_phenotype`` table directly so the full-page list inherits the same
    reference-data hygiene the annotation engine applies (F23): obsolete MONDO
    terms are dropped (F21), gene-level inheritance is corrected for the
    known-mislabelled dominant genes (F14), and records come back
    deterministically ordered. A raw ``SELECT *`` leaked obsolete labels and the
    wrong inheritance that the stored single-disease summary already filters out.
    """
    if not gene_symbol:
        return []

    registry = get_registry()
    annots_by_gene = lookup_gene_phenotypes([gene_symbol], registry.reference_engine)

    results: list[GenePhenotypeRecord] = []
    for annot in annots_by_gene.get(gene_symbol, []):
        # Build OMIM link if the disease_id is an OMIM ID
        omim_link: str | None = None
        if annot.disease_id and annot.disease_id.startswith("OMIM:"):
            omim_id = annot.disease_id.replace("OMIM:", "")
            omim_link = f"https://omim.org/entry/{omim_id}"

        results.append(
            GenePhenotypeRecord(
                gene_symbol=annot.gene_symbol,
                disease_name=annot.disease_name,
                disease_id=annot.disease_id,
                source=annot.source,
                # lookup_gene_phenotypes always returns a list; normalize the
                # empty case back to None to match the response contract.
                hpo_terms=annot.hpo_terms or None,
                inheritance=annot.inheritance,
                omim_link=omim_link,
            )
        )

    return results


def _build_evidence_conflict_detail(
    row: sa.Row,
) -> EvidenceConflictDetail:
    """Build structured evidence conflict detail from an annotated variant row."""
    clinvar_sig = getattr(row, "clinvar_significance", None)
    clinvar_stars = getattr(row, "clinvar_review_stars", None)
    clinvar_acc = getattr(row, "clinvar_accession", None)
    cadd = getattr(row, "cadd_phred", None)
    has_conflict = bool(getattr(row, "evidence_conflict", False))
    deleterious_count = getattr(row, "deleterious_count", None) or 0

    # Determine which in-silico tools predicted deleterious
    deleterious_tools: list[str] = []
    total_assessed = 0

    tool_display = {
        "sift_pred": "SIFT",
        "polyphen2_hsvar_pred": "PolyPhen-2",
        "metasvm": "MetaSVM",
        "metalr": "MetaLR",
        "revel": "REVEL",
    }

    for tool_col in _INSILICO_TOOLS:
        val = getattr(row, tool_col, None)
        if val is None:
            continue
        total_assessed += 1
        if tool_col == "revel":
            # REVEL is a [0,1] ensemble score; ≥ 0.5 = deleterious.
            try:
                if float(val) >= _REVEL_THRESHOLD:
                    deleterious_tools.append(tool_display[tool_col])
            except (ValueError, TypeError):
                pass
        elif tool_col == "metasvm":
            # MetaSVM is a signed SVM decision score (can be negative); > 0 =
            # deleterious.
            try:
                if float(val) > 0:
                    deleterious_tools.append(tool_display[tool_col])
            except (ValueError, TypeError):
                pass
        elif tool_col == "metalr":
            # MetaLR is a [0,1] logistic-regression probability — NOT signed like
            # MetaSVM — so its deleterious cutoff is > 0.5, not > 0.  Treating it
            # like MetaSVM (> 0) counts it deleterious for nearly every scored
            # variant, since MetaLR scores are essentially never negative.
            try:
                if float(val) > _METALR_THRESHOLD:
                    deleterious_tools.append(tool_display[tool_col])
            except (ValueError, TypeError):
                pass
        else:
            # Categorical predictions (D = deleterious)
            if str(val) in _DELETERIOUS_PREDS:
                deleterious_tools.append(tool_display[tool_col])

    # Build summary text for the evidence conflict section
    summary: str | None = None
    if has_conflict:
        sig_text = clinvar_sig or "unknown"
        stars_text = f" ({clinvar_stars}-star review)" if clinvar_stars is not None else ""
        n_del = deleterious_count if deleterious_count else len(deleterious_tools)
        tools_text = f"{n_del} of {total_assessed} in-silico tools predict deleterious"
        cadd_text = f" (CADD: {cadd})" if cadd is not None else ""
        summary = (
            f"ClinVar classifies this variant as {sig_text}{stars_text}. "
            f"{tools_text}{cadd_text}. "
            "This may reflect a variant under active clinical investigation."
        )

    return EvidenceConflictDetail(
        has_conflict=has_conflict,
        clinvar_significance=clinvar_sig,
        clinvar_review_stars=clinvar_stars,
        clinvar_accession=clinvar_acc,
        deleterious_count=deleterious_count,
        total_tools_assessed=total_assessed,
        deleterious_tools=deleterious_tools,
        cadd_phred=cadd,
        summary=summary,
    )


def _attach_alphamissense_badge(data: dict[str, Any]) -> None:
    """Attach the context-only AlphaMissense badge to a response payload."""
    data["alphamissense_badge"] = alphamissense_badge_for_variant(
        data.get("alphamissense_pathogenicity"),
        data.get("alphamissense_class"),
        revel=data.get("revel"),
        consequence=data.get("consequence"),
    )


# ── Endpoint ─────────────────────────────────────────────────────────


@router.get("/{rsid}")
def get_variant_detail(
    rsid: str,
    sample_id: int = Query(..., description="Sample ID"),
) -> VariantDetailResponse:
    """Return full detail for a single variant by rsid.

    Includes all annotation fields from the annotated_variants table,
    all VEP transcripts from the VEP bundle, gene-phenotype records
    (with OMIM links), and a structured evidence conflict section.

    Example: ``GET /api/variants/rs80357906?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)

    # 1. Fetch the variant from annotated_variants
    with sample_engine.connect() as conn:
        row = conn.execute(sa.select(_TABLE).where(_TABLE.c.rsid == rsid)).fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Variant {rsid} not found in sample {sample_id}.",
        )

    # 2. Build base response from annotated_variants columns
    data: dict[str, Any] = {}
    for col in _TABLE.c:
        data[col.name] = getattr(row, col.name, None)

    # 3. P3-26: Ancestry-matched AF display
    ancestry_population = get_inferred_ancestry(sample_engine)
    if ancestry_population:
        af_col = get_ancestry_matched_af_column(ancestry_population)
        data["ancestry_matched_af"] = getattr(row, af_col, None)
        data["ancestry_matched_population"] = ancestry_population

    # 4. Fetch all VEP transcripts
    transcripts = _fetch_all_transcripts(rsid)

    # 5. Fetch gene-phenotype records (including OMIM links)
    gene_phenotypes = _fetch_gene_phenotypes(data.get("gene_symbol"))

    # 6. Build evidence conflict detail
    evidence_conflict_detail = _build_evidence_conflict_detail(row)

    _attach_alphamissense_badge(data)

    return VariantDetailResponse(
        **data,
        transcripts=transcripts,
        gene_phenotypes=gene_phenotypes,
        evidence_conflict_detail=evidence_conflict_detail,
    )
