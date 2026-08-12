"""Variant detail API (P2-20, P3-26).

Single variant endpoint returning all annotations, all VEP transcripts,
gene-phenotype records (including OMIM links), and evidence conflict details.

P3-26: Includes ``ancestry_matched_af`` and ``ancestry_matched_population``
fields based on the sample's inferred ancestry from PCA projection.

GET  /api/variants/{rsid}  — Full variant detail with all annotations
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.analysis.allelic_state import allelic_state_label, norm_chrom_label
from backend.analysis.alphamissense import alphamissense_badge_for_variant
from backend.analysis.ancestry import get_ancestry_matched_af_column, get_inferred_ancestry
from backend.analysis.gtex import eqtl_regulatory_context
from backend.analysis.spliceai import spliceai_splice_context
from backend.annotation.gtex_eqtl import lookup_eqtls_by_rsids
from backend.annotation.insilico_axes import assess_insilico_axes, deleterious_predictor_names
from backend.annotation.mondo_hpo import lookup_gene_phenotypes, mondo_hpo_install_is_serviceable
from backend.annotation.spliceai import lookup_spliceai_by_variant
from backend.annotation.vep_bundle import _filter_rows_for_sample_allele
from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import annotated_variants, samples
from backend.services.sex_inference import (
    get_recorded_biological_sex,
    infer_biological_sex,
    resolve_biological_sex,
)
from backend.services.staleness import read_recorded_reference_versions

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/variants",
    tags=["variant-detail"],
    dependencies=[Depends(require_fresh_sample)],
)

# ── Response models ──────────────────────────────────────────────────


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
    # The allele this annotation belongs to. After allele filtering (#2002) these
    # are the sample's carried allele, but surfacing them lets the UI label the
    # allele and disambiguate any future multi-allele rendering.
    ref: str | None = None
    alt: str | None = None


class HpoTermRecord(BaseModel):
    """HPO identifier with its optional human-readable label."""

    id: str
    name: str | None = None


class GenePhenotypeRecord(BaseModel):
    """Gene-phenotype association from MONDO/HPO or OMIM."""

    gene_symbol: str
    disease_name: str
    disease_id: str | None = None
    source: str  # "mondo_hpo" or "omim"
    hpo_terms: list[str] | None = None
    hpo_term_details: list[HpoTermRecord] | None = None
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
    zygosity_label: str | None = None

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
    gnomad_af_asj: float | None = None
    gnomad_af_eas: float | None = None
    gnomad_af_eur: float | None = None
    gnomad_af_fin: float | None = None
    gnomad_af_sas: float | None = None
    gnomad_source_status: str | None = None
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

    # GTEx eQTL regulatory context (context-only; association, never ACMG evidence)
    gtex_eqtl_badge: dict[str, Any] | None = None

    # SpliceAI splice-effect prediction (context-only; in-silico, never ACMG evidence)
    spliceai_badge: dict[str, Any] | None = None

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
    "rsid, chrom, pos, ref, alt, gene_symbol, transcript_id, consequence, "
    "hgvs_coding, hgvs_protein, strand, exon_number, "
    "intron_number, mane_select"
)


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


def _fetch_all_transcripts(
    rsid: str,
    *,
    allele_identity: tuple[str, int, str, str] | None = None,
    genotype: str | None = None,
) -> list[TranscriptAnnotation]:
    """Fetch the VEP transcripts for the allele the sample actually carries.

    At a multi-allelic rsID the VEP bundle holds one row per alternate allele.
    Returning them all lists other people's substitutions on the carrier's page
    (#2002). This filters to the sample's allele with the same helper the
    annotation pipeline uses (#1411): the exact ref/alt identity when the sample
    row has one, else genotype carriage; single-allele rsIDs keep the marker
    annotation. Returns an empty list if the VEP bundle is unavailable.
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

    rows, _ = _filter_rows_for_sample_allele(
        list(rows), allele_identity=allele_identity, genotype=genotype
    )

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
            ref=row.ref,
            alt=row.alt,
        )
        for row in rows
    ]


def _fetch_gene_phenotypes(gene_symbol: str | None) -> list[GenePhenotypeRecord]:
    """Fetch all gene-phenotype associations for a gene from reference.db.

    Routes through :func:`lookup_gene_phenotypes` rather than reading the
    ``gene_phenotype`` table directly so the full-page list inherits the same
    reference-data hygiene the annotation engine applies (F23): obsolete MONDO
    terms are dropped (F21), disease-scoped inheritance is kept with its source
    disease, and records come back deterministically ordered. A raw ``SELECT *``
    leaked obsolete labels and bypassed the shared disease-scoped lookup.
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
                hpo_term_details=(
                    [HpoTermRecord(id=term.id, name=term.name) for term in annot.hpo_term_details]
                    or None
                ),
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
    fallback_count, fallback_total = assess_insilico_axes(row)
    stored_count = getattr(row, "deleterious_count", None)
    stored_total = getattr(row, "deleterious_total_assessed", None)
    deleterious_count = stored_count if stored_count is not None else fallback_count
    total_assessed = stored_total if stored_total is not None else fallback_total
    deleterious_tools = deleterious_predictor_names(row)

    # Build summary text for the evidence conflict section
    summary: str | None = None
    if has_conflict:
        sig_text = clinvar_sig or "unknown"
        stars_text = f" ({clinvar_stars}-star review)" if clinvar_stars is not None else ""
        n_del = deleterious_count if deleterious_count is not None else len(deleterious_tools)
        tools_text = f"{n_del} of {total_assessed} independent in-silico axes predict deleterious"
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


def _attach_gtex_eqtl_badge(data: dict[str, Any], registry: Any) -> None:
    """Attach the context-only GTEx eQTL regulatory badge (optional layer).

    No-op when the optional ``gtex_eqtl.db`` is not installed (the engine is
    existence-guarded so we never create an empty DB file or 500 the endpoint).
    The badge is regulatory association only — never ACMG evidence (no PP3/PS3).
    """
    data["gtex_eqtl_badge"] = None
    rsid = data.get("rsid")
    if not rsid:
        return

    settings = getattr(registry, "settings", None)
    db_path = getattr(settings, "gtex_eqtl_db_path", None)
    if isinstance(db_path, Path) and not db_path.exists():
        return  # optional DB not installed → no badge

    try:
        hits = lookup_eqtls_by_rsids([rsid], registry.gtex_eqtl_engine)
    except sa.exc.SQLAlchemyError:
        logger.debug("gtex_eqtl_badge_lookup_failed", exc_info=True)
        return
    data["gtex_eqtl_badge"] = eqtl_regulatory_context(rsid, hits.get(rsid, []))


def _attach_spliceai_badge(data: dict[str, Any], registry: Any) -> None:
    """Attach the context-only SpliceAI splice-prediction badge (optional BYO layer).

    No-op when the optional ``spliceai.db`` is not installed (existence-guarded so
    we never create an empty DB file or 500 the endpoint). SpliceAI is keyed by
    GRCh37 position (chrom/pos/ref/alt), not rsID. The badge is an in-silico
    prediction only — never ACMG evidence (no PVS1/PP3/PS3).
    """
    data["spliceai_badge"] = None
    chrom = data.get("chrom")
    pos = data.get("pos")
    ref = data.get("ref")
    alt = data.get("alt")
    if not chrom or pos is None or not ref or not alt:
        return

    settings = getattr(registry, "settings", None)
    db_path = getattr(settings, "spliceai_db_path", None)
    if isinstance(db_path, Path) and not db_path.exists():
        return  # optional BYO DB not installed → no badge

    try:
        row = lookup_spliceai_by_variant(chrom, pos, ref, alt, registry.spliceai_engine)
    except sa.exc.SQLAlchemyError:
        logger.debug("spliceai_badge_lookup_failed", exc_info=True)
        return
    data["spliceai_badge"] = spliceai_splice_context(row)


# ── Endpoint ─────────────────────────────────────────────────────────


def _sample_phenotype_scope_is_current(sample_engine: sa.Engine) -> bool:
    """Whether this sample's stored phenotype columns came from a scoped install.

    Reads the sample's own recorded reference snapshot rather than the current
    reference install: the columns were copied in at annotation time, so what
    matters is the MONDO/HPO revision that run used, not what is installed now.

    Fails closed. A sample with no recorded snapshot predates the mechanism, so
    its provenance cannot be established and its pooled gene-wide terms are the
    ones this gate exists to withhold.
    """
    versions = read_recorded_reference_versions(sample_engine)
    if not versions:
        return False
    return mondo_hpo_install_is_serviceable(versions.get("mondo_hpo"))


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

    # A sample annotated before the disease-scope migration holds gene-wide
    # phenotype columns: every disease's terms pooled onto the gene, which is
    # the cross-disease annotation this change exists to withhold. The live
    # reference gate does not reach them, because these were copied into the
    # sample at annotation time and are served straight from its own table -
    # so rebuilding the reference install alone does not stop an upgraded user
    # seeing them. Withhold until the sample is re-annotated under a scoped
    # install; the sample's own recorded snapshot is what says which it was.
    # Scoped to mondo_hpo-sourced summaries only. OMIM enrichment writes these
    # same columns from a separate loader with its own phenotype-specific
    # disease ID and inheritance; it never went through the gene-wide MONDO/HPO
    # merge, so clearing it would cost a user valid context for a problem it
    # does not have.
    if data.get("phenotype_source") == "mondo_hpo" and not _sample_phenotype_scope_is_current(
        sample_engine
    ):
        for field in ("disease_name", "disease_id", "phenotype_source", "hpo_terms"):
            data[field] = None
        data["inheritance_pattern"] = None

    registry = get_registry()
    biological_sex: str | None = None
    if norm_chrom_label(data.get("chrom")) in ("X", "Y"):
        biological_sex = resolve_biological_sex(
            recorded_sex=get_recorded_biological_sex(registry.reference_engine, sample_id),
            inferred_sex=infer_biological_sex(sample_engine),
        ).sex
    data["zygosity_label"] = allelic_state_label(data, biological_sex)

    # 3. P3-26: Ancestry-matched AF display
    ancestry_population = get_inferred_ancestry(sample_engine)
    if ancestry_population:
        af_col = get_ancestry_matched_af_column(ancestry_population)
        data["ancestry_matched_af"] = getattr(row, af_col, None)
        data["ancestry_matched_population"] = ancestry_population

    # 4. Fetch the VEP transcripts for the allele the sample carries (#2002).
    # Use the exact ref/alt identity when the row has one (variant calls), else
    # fall back to genotype carriage (e.g. a hom-ref call whose ref/alt are NULL).
    v_chrom, v_pos, v_ref, v_alt = (
        data.get("chrom"),
        data.get("pos"),
        data.get("ref"),
        data.get("alt"),
    )
    allele_identity = (
        (v_chrom, v_pos, v_ref, v_alt)
        if v_chrom and v_pos is not None and v_ref and v_alt
        else None
    )
    transcripts = _fetch_all_transcripts(
        rsid, allele_identity=allele_identity, genotype=data.get("genotype")
    )

    # 5. Fetch gene-phenotype records (including OMIM links)
    gene_phenotypes = _fetch_gene_phenotypes(data.get("gene_symbol"))

    # 6. Build evidence conflict detail
    evidence_conflict_detail = _build_evidence_conflict_detail(row)

    _attach_alphamissense_badge(data)
    _attach_gtex_eqtl_badge(data, registry)
    _attach_spliceai_badge(data, registry)

    return VariantDetailResponse(
        **data,
        transcripts=transcripts,
        gene_phenotypes=gene_phenotypes,
        evidence_conflict_detail=evidence_conflict_detail,
    )
