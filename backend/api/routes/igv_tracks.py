"""IGV.js track data endpoints (P2-17).

Serves genomic data for IGV.js tracks via range-based API queries.
Tracks: ClinVar variants, user sample variants, gnomAD allele frequencies,
ENCODE cCREs (adapter to existing endpoint).

All endpoints use ``sourceType: "service"`` or ``sourceType: "custom"``
URL template variables ($CHR, $START, $END) consumed by IGV.js.
"""

from __future__ import annotations

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel

from backend.analysis.zygosity import (
    ZYG_HET,
    ZYG_HOM_ALT,
    ZYG_HOM_REF,
    is_no_call,
)
from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import (
    annotated_variants,
    clinvar_variants,
    raw_variants,
    samples,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/igv-tracks", tags=["igv-tracks"])


# ── Helpers ──────────────────────────────────────────────────────────


def _normalize_chrom(chrom: str) -> str:
    """Strip 'chr' prefix for DB lookup (our DBs store '1', 'X', etc.)."""
    return chrom.removeprefix("chr")


def _get_sample_engine(sample_id: int) -> sa.Engine:
    """Resolve sample_id → per-sample SQLite engine."""
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


# ── ClinVar VCF track (sourceType: "service", format: "vcf") ────────


VCF_HEADER = """\
##fileformat=VCFv4.2
##source=Yeliztli-ClinVar
##INFO=<ID=CLNSIG,Number=.,Type=String,Description="Clinical significance">
##INFO=<ID=CLNREVSTAT,Number=.,Type=String,Description="Review stars (0-4)">
##INFO=<ID=GENEINFO,Number=.,Type=String,Description="Gene symbol">
##INFO=<ID=CLNACC,Number=.,Type=String,Description="ClinVar accession">
##INFO=<ID=CLNDN,Number=.,Type=String,Description="Condition/disease name">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO"""


def _clinvar_row_to_vcf_line(row: sa.Row) -> str:
    """Convert a clinvar_variants DB row to a VCF text line."""
    chrom = f"chr{row.chrom}"
    info_parts = []
    if row.significance:
        info_parts.append(f"CLNSIG={row.significance}")
    if row.review_stars is not None:
        info_parts.append(f"CLNREVSTAT={row.review_stars}")
    if row.gene_symbol:
        info_parts.append(f"GENEINFO={row.gene_symbol}")
    if row.accession:
        info_parts.append(f"CLNACC={row.accession}")
    if row.conditions:
        # Escape semicolons in condition names for VCF INFO field
        info_parts.append(f"CLNDN={row.conditions.replace(';', '%3B')}")
    info_str = ";".join(info_parts) if info_parts else "."
    rsid = row.rsid if row.rsid else "."
    return f"{chrom}\t{row.pos}\t{rsid}\t{row.ref}\t{row.alt}\t.\t.\t{info_str}"


@router.get("/clinvar/header")
async def clinvar_vcf_header() -> Response:
    """Return VCF header for ClinVar track (used by IGV headerURL)."""
    return Response(content=VCF_HEADER + "\n", media_type="text/plain")


@router.get("/clinvar")
async def clinvar_vcf_region(
    chr: str = Query(..., description="Chromosome (e.g., 'chr1', '1')"),
    start: int = Query(..., ge=0, description="Region start (0-based)"),
    end: int = Query(..., gt=0, description="Region end"),
) -> Response:
    """Return ClinVar variants in VCF format for a genomic region.

    Used by IGV.js ``sourceType: "service"`` with ``format: "vcf"``.
    """
    chrom = _normalize_chrom(chr)
    registry = get_registry()

    query = (
        sa.select(clinvar_variants)
        .where(
            clinvar_variants.c.chrom == chrom,
            clinvar_variants.c.pos >= start,
            clinvar_variants.c.pos <= end,
        )
        .order_by(clinvar_variants.c.pos)
    )

    with registry.reference_engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    lines = [VCF_HEADER]
    for row in rows:
        lines.append(_clinvar_row_to_vcf_line(row))

    return Response(content="\n".join(lines) + "\n", media_type="text/plain")


# ── User sample VCF track (sourceType: "service", format: "vcf") ───


# Built as a single VCF line each (no embedded newlines); split across source
# lines via implicit string concatenation only to stay within the line limit.
_USER_VCF_NOTE = (
    "##Yeliztli_note=Variants with an annotation-resolved reference allele use "
    "true reference-aligned REF/ALT and a GT derived from zygosity vs the "
    "plus-strand GRCh37 reference. Where the reference allele is unresolved "
    "(sample not yet annotated, or no source supplied allele identity), REF is "
    "set to N and observed bases are emitted as ALT so alternate-allele carriage "
    "is never hidden and no allele is assumed to be the reference."
)
_USER_VCF_INFO_OBS = (
    '##INFO=<ID=OBS,Number=1,Type=String,Description="Observed genotyping-array '
    "call (vendor design strand); REF/ALT are reference-aligned only when "
    'annotation resolved them, otherwise REF=N">'
)
USER_VCF_HEADER = "\n".join(
    [
        "##fileformat=VCFv4.2",
        "##source=Yeliztli-UserVariants",
        "##reference=GRCh37",
        _USER_VCF_NOTE,
        _USER_VCF_INFO_OBS,
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE",
    ]
)


def _resolve_vcf_fields(
    genotype: str | None,
    ref: str | None,
    alt: str | None,
    zygosity: str | None,
) -> tuple[str, str, str]:
    """Resolve VCF (REF, ALT, GT) for one sample variant.

    VCF genotype fields are allele-indexed against REF/ALT (``0`` = REF,
    positive integers = ALT), so a SNP-array call can only be represented
    correctly once its alleles are placed against the genome reference.

    Reference-aligned path — when the variant carries an annotation-resolved
    ``ref``/``alt`` and a resolved ``zygosity`` (``hom_ref``/``het``/``hom_alt``
    computed by :func:`backend.analysis.zygosity.classify_zygosity` against the
    plus-strand reference) — emit a standard reference-aligned record so a
    homozygous-alternate call shows ``GT=1/1`` (never a false ``0/0``) and the
    heterozygous REF/ALT follow biology rather than raw allele-string order.

    Honest fallback — when the reference allele is unresolved (sample not yet
    annotated, no source supplied allele identity, or carriage indeterminate) —
    do not fabricate a reference-genome ``0/0``. ``REF`` is set to ``N`` (the
    IUPAC unknown base) and every distinct observed base is emitted as an ALT,
    so alternate-allele carriage is never hidden and no observed allele is
    asserted to be the reference.
    """
    if is_no_call(genotype):
        return "N", ".", "./."
    gt = genotype.strip().upper()  # type: ignore[union-attr]  # is_no_call rules out None/empty
    haploid = len(gt) == 1

    # Reference-aligned path: trust the annotation-resolved ref/alt + zygosity.
    if ref and alt and zygosity in (ZYG_HOM_REF, ZYG_HET, ZYG_HOM_ALT):
        if zygosity == ZYG_HOM_REF:
            gt_field = "0" if haploid else "0/0"
        elif zygosity == ZYG_HOM_ALT:
            gt_field = "1" if haploid else "1/1"
        else:  # het — always diploid (one ref, one alt)
            gt_field = "0/1"
        return ref, alt, gt_field

    # Honest fallback: reference base unknown. Emit observed bases as ALT
    # against REF=N; never claim reference-genome 0/0.
    observed: list[str] = []
    for base in gt:
        if base in "ACGT" and base not in observed:
            observed.append(base)
    if not observed:
        # Non-nucleotide call (e.g. a stray indel code) — unscoreable.
        return "N", ".", "./."
    alt_field = ",".join(observed)
    if haploid:
        gt_field = "1"
    elif len(observed) == 1:
        gt_field = "1/1"  # homozygous observed (e.g. CC) vs unknown reference
    else:
        gt_field = "1/2"  # heterozygous observed (e.g. AG) — two distinct ALTs
    return "N", alt_field, gt_field


@router.get("/sample/{sample_id}/header", dependencies=[Depends(require_fresh_sample)])
async def sample_vcf_header(
    sample_id: int = Path(..., description="Sample ID"),
) -> Response:
    """Return VCF header for user sample track."""
    # Validate sample exists
    _get_sample_engine(sample_id)
    return Response(content=USER_VCF_HEADER + "\n", media_type="text/plain")


@router.get("/sample/{sample_id}/variants", dependencies=[Depends(require_fresh_sample)])
async def sample_vcf_region(
    sample_id: int = Path(..., description="Sample ID"),
    chr: str = Query(..., description="Chromosome (e.g., 'chr1', '1')"),
    start: int = Query(..., ge=0, description="Region start (0-based)"),
    end: int = Query(..., gt=0, description="Region end"),
) -> Response:
    """Return user sample variants in VCF format for a region.

    Used by IGV.js ``sourceType: "service"`` with ``format: "vcf"``.
    """
    chrom = _normalize_chrom(chr)
    sample_engine = _get_sample_engine(sample_id)

    # LEFT JOIN the per-sample annotated_variants (reference-resolved ref/alt +
    # zygosity, one row per raw variant once annotation has run) so each call
    # can be emitted with a true reference-aligned REF/ALT/GT. raw_variants is
    # the spine: a never-annotated sample (allowed past require_fresh_sample)
    # simply has NULL ref/alt/zygosity and falls back to the honest REF=N path.
    query = (
        sa.select(
            raw_variants.c.rsid,
            raw_variants.c.chrom,
            raw_variants.c.pos,
            raw_variants.c.genotype,
            annotated_variants.c.ref,
            annotated_variants.c.alt,
            annotated_variants.c.zygosity,
        )
        .select_from(
            raw_variants.outerjoin(
                annotated_variants,
                sa.and_(
                    raw_variants.c.rsid == annotated_variants.c.rsid,
                    raw_variants.c.chrom == annotated_variants.c.chrom,
                    raw_variants.c.pos == annotated_variants.c.pos,
                ),
            )
        )
        .where(
            raw_variants.c.chrom == chrom,
            raw_variants.c.pos >= start,
            raw_variants.c.pos <= end,
        )
        .order_by(raw_variants.c.pos)
    )

    with sample_engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    lines = [USER_VCF_HEADER]
    for row in rows:
        ref, alt, gt = _resolve_vcf_fields(row.genotype, row.ref, row.alt, row.zygosity)
        rsid = row.rsid if row.rsid else "."
        info = f"OBS={row.genotype}" if row.genotype else "."
        lines.append(f"chr{row.chrom}\t{row.pos}\t{rsid}\t{ref}\t{alt}\t.\t.\t{info}\tGT\t{gt}")

    return Response(content="\n".join(lines) + "\n", media_type="text/plain")


# ── gnomAD AF track (sourceType: "custom", JSON features) ───────────


class GnomadFeature(BaseModel):
    """A single gnomAD AF feature for IGV.js annotation track."""

    chr: str
    start: int
    end: int
    name: str
    score: float
    af_global: float
    af_afr: float | None = None
    af_amr: float | None = None
    af_eas: float | None = None
    af_eur: float | None = None


@router.get("/gnomad")
async def gnomad_region(
    chr: str = Query(..., description="Chromosome (e.g., 'chr1', '1')"),
    start: int = Query(..., ge=0, description="Region start (0-based)"),
    end: int = Query(..., gt=0, description="Region end"),
) -> list[GnomadFeature]:
    """Return gnomAD allele frequencies as JSON features for a region.

    Used by IGV.js ``sourceType: "custom"`` annotation track.
    """
    chrom = _normalize_chrom(chr)
    registry = get_registry()

    try:
        engine = registry.gnomad_engine
    except Exception as exc:
        logger.debug("gnomad_engine_unavailable", error=str(exc))
        return []

    query = sa.text(
        "SELECT rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_eas, af_eur "
        "FROM gnomad_af "
        "WHERE chrom = :chrom AND pos >= :start AND pos <= :end "
        "ORDER BY pos "
        "LIMIT 5000"
    )

    try:
        with engine.connect() as conn:
            rows = conn.execute(query, {"chrom": chrom, "start": start, "end": end}).fetchall()
    except Exception as exc:
        logger.debug("gnomad_query_failed", error=str(exc))
        return []

    features = []
    for row in rows:
        af = row.af_global if row.af_global is not None else 0.0
        label = f"{row.rsid or '.'} AF={af:.4f}"
        features.append(
            GnomadFeature(
                chr=f"chr{row.chrom}",
                start=row.pos,
                end=row.pos + 1,
                name=label,
                score=af,
                af_global=af,
                af_afr=row.af_afr,
                af_amr=row.af_amr,
                af_eas=row.af_eas,
                af_eur=row.af_eur,
            )
        )

    return features


# ── ENCODE cCREs track (sourceType: "custom", JSON features) ────────


class CCREFeature(BaseModel):
    """A single ENCODE cCRE feature for IGV.js annotation track."""

    chr: str
    start: int
    end: int
    name: str
    color: str


# Color palette for cCRE classification types
CCRE_COLORS: dict[str, str] = {
    "PLS": "rgb(255,0,0)",  # Promoter-like — red
    "pELS": "rgb(255,205,0)",  # Proximal enhancer-like — orange/yellow
    "dELS": "rgb(255,205,0)",  # Distal enhancer-like — orange/yellow
    "CTCF-only": "rgb(0,176,240)",  # CTCF-bound — blue
    "DNase-H3K4me3": "rgb(102,205,170)",  # DNase-H3K4me3 — teal
}


@router.get("/encode-ccres")
async def encode_ccres_region(
    chr: str = Query(..., description="Chromosome (e.g., 'chr1', '1')"),
    start: int = Query(..., ge=0, description="Region start (0-based)"),
    end: int = Query(..., gt=0, description="Region end"),
) -> list[CCREFeature]:
    """Return ENCODE cCREs as JSON features for a region.

    Thin adapter over the existing ENCODE cCREs data for IGV.js custom source.
    """
    from backend.annotation.encode_ccres import is_loaded, query_ccres_by_region

    chrom = _normalize_chrom(chr)
    registry = get_registry()

    try:
        engine = registry.encode_ccres_engine
    except Exception as exc:
        logger.debug("encode_ccres_engine_unavailable", error=str(exc))
        return []

    if not is_loaded(engine):
        return []

    results = query_ccres_by_region(chrom, start, end, engine)

    return [
        CCREFeature(
            chr=f"chr{r.chrom}",
            start=r.start_pos,
            end=r.end_pos,
            name=f"{r.accession} ({r.ccre_class})",
            color=CCRE_COLORS.get(r.ccre_class, "rgb(128,128,128)"),
        )
        for r in results
    ]
