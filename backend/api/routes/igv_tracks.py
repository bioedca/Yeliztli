"""IGV.js track data endpoints (P2-17).

Serves genomic data for IGV.js tracks via range-based API queries.
Tracks: ClinVar variants, user sample variants, gnomAD allele frequencies,
ENCODE cCREs (adapter to existing endpoint).

All endpoints use ``sourceType: "service"`` or ``sourceType: "custom"``
URL template variables ($CHR, $START, $END) consumed by IGV.js.
"""

from __future__ import annotations

import json
from pathlib import Path as FilePath

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.analysis.zygosity import (
    ZYG_HET,
    ZYG_HOM_ALT,
    ZYG_HOM_REF,
    is_no_call,
)
from backend.api.dependencies import require_fresh_sample
from backend.config import Settings, get_settings
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


# ── Local GRCh37 reference bundle for IGV.js ─────────────────────────


class IgvReferenceConfig(BaseModel):
    """IGV.js reference object for a locally served GRCh37 FASTA."""

    id: str
    name: str
    fastaURL: str
    indexURL: str


class IgvReferenceTrackConfig(BaseModel):
    """IGV.js track object for the locally served RefSeq annotation BED."""

    name: str
    type: str
    format: str
    url: str
    displayMode: str
    height: int
    color: str


class GenomeBrowserReferenceStatus(BaseModel):
    """Whether the optional local Genome Browser reference bundle is usable."""

    available: bool
    mode: str
    reference: IgvReferenceConfig | None
    tracks: list[IgvReferenceTrackConfig]
    missing: list[str]


_REFERENCE_FASTA_URL = "/api/igv-tracks/reference/fasta"
_REFERENCE_FASTA_INDEX_URL = "/api/igv-tracks/reference/fasta.fai"
_REFERENCE_REFSEQ_URL = "/api/igv-tracks/reference/refseq.bed"
_REFERENCE_MANIFEST_NAME = "genome_browser_reference_manifest.json"
_REFERENCE_MANIFEST_BUNDLE_NAME = "genome_browser_reference_grch37_hg19"
_UCSC_HG19_FASTA_URL = "https://hgdownload.soe.ucsc.edu/goldenPath/hg19/bigZips/hg19.fa.gz"
_UCSC_HG19_REFGENE_URL = "https://hgdownload.soe.ucsc.edu/goldenPath/hg19/database/refGene.txt.gz"
_GRCH37_HG19_SENTINEL_LENGTHS = {
    "chr1": 249250621,
    "chr2": 243199373,
    "chr10": 135534747,
    "chrX": 155270560,
    "chrY": 59373566,
    "chrM": 16571,
}


def _fasta_index_path(fasta_path: FilePath) -> FilePath:
    """Return the samtools faidx path for an uncompressed FASTA."""
    return FilePath(f"{fasta_path}.fai")


def _read_fasta_index_lengths(index_path: FilePath) -> dict[str, int]:
    lengths: dict[str, int] = {}
    with index_path.open(encoding="ascii") as handle:
        for line_number, line in enumerate(handle, start=1):
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 5:
                raise ValueError(f"line {line_number} has fewer than 5 faidx columns")
            name = fields[0]
            if not name:
                raise ValueError(f"line {line_number} is missing a sequence name")
            if name in lengths:
                raise ValueError(f"duplicate sequence name {name!r}")
            try:
                length = int(fields[1])
            except ValueError as exc:
                raise ValueError(f"line {line_number} has a non-integer length") from exc
            if length <= 0:
                raise ValueError(f"line {line_number} has a non-positive length")
            lengths[name] = length
    if not lengths:
        raise ValueError("index contains no sequences")
    return lengths


def _validate_grch37_fasta_index(index_path: FilePath) -> list[str]:
    try:
        lengths = _read_fasta_index_lengths(index_path)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return [f"GRCh37 FASTA index (grch37.fa.fai) is not a valid faidx file: {exc}"]

    problems: list[str] = []
    for contig, expected_length in _GRCH37_HG19_SENTINEL_LENGTHS.items():
        observed_length = lengths.get(contig)
        if observed_length is None:
            problems.append(f"{contig} missing")
        elif observed_length != expected_length:
            problems.append(f"{contig}={observed_length:,} expected {expected_length:,}")
    if not problems:
        return []

    return [
        "GRCh37 FASTA index (grch37.fa.fai) does not match hg19 sentinel contig "
        f"lengths: {'; '.join(problems)}"
    ]


def _manifest_path_for(fasta_path: FilePath, refseq_path: FilePath) -> FilePath | None:
    if fasta_path.parent != refseq_path.parent:
        return None
    return fasta_path.parent / _REFERENCE_MANIFEST_NAME


def _manifest_path_value(payload: object, *keys: str) -> str | None:
    cursor = payload
    for key in keys:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(key)
    return cursor if isinstance(cursor, str) else None


def _validate_reference_manifest(
    manifest_path: FilePath | None,
    *,
    fasta_path: FilePath,
    index_path: FilePath,
    refseq_path: FilePath,
) -> list[str]:
    if manifest_path is None:
        return ["GRCh37 reference manifest must live beside grch37.fa and grch37_refseq.bed"]
    if not manifest_path.is_file():
        return [f"GRCh37 reference manifest ({_REFERENCE_MANIFEST_NAME}) is missing"]

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"GRCh37 reference manifest ({_REFERENCE_MANIFEST_NAME}) is invalid: {exc}"]
    if not isinstance(payload, dict):
        return [f"GRCh37 reference manifest ({_REFERENCE_MANIFEST_NAME}) is not an object"]

    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if payload.get("name") != _REFERENCE_MANIFEST_BUNDLE_NAME:
        errors.append(f"name must be {_REFERENCE_MANIFEST_BUNDLE_NAME!r}")

    runtime_files = payload.get("runtime_files")
    if not isinstance(runtime_files, list) or not all(
        isinstance(item, str) for item in runtime_files
    ):
        errors.append("runtime_files must list grch37.fa, grch37.fa.fai, and grch37_refseq.bed")
    else:
        expected_runtime_files = {fasta_path.name, index_path.name, refseq_path.name}
        missing_runtime_files = expected_runtime_files.difference(runtime_files)
        if missing_runtime_files:
            missing = ", ".join(sorted(missing_runtime_files))
            errors.append(f"runtime_files is missing {missing}")

    expected_sources = {
        ("sources", "fasta", "url"): _UCSC_HG19_FASTA_URL,
        ("sources", "refgene", "url"): _UCSC_HG19_REFGENE_URL,
    }
    for keys, expected_value in expected_sources.items():
        observed_value = _manifest_path_value(payload, *keys)
        if observed_value != expected_value:
            dotted = ".".join(keys)
            errors.append(f"{dotted} must be {expected_value}")

    expected_outputs = {
        ("outputs", "fasta", "path"): fasta_path.name,
        ("outputs", "fasta_index", "path"): index_path.name,
        ("outputs", "refseq_bed", "path"): refseq_path.name,
    }
    for keys, expected_value in expected_outputs.items():
        observed_value = _manifest_path_value(payload, *keys)
        if observed_value != expected_value:
            dotted = ".".join(keys)
            errors.append(f"{dotted} must be {expected_value!r}")

    if not errors:
        return []
    return [
        f"GRCh37 reference manifest ({_REFERENCE_MANIFEST_NAME}) does not describe "
        f"the expected UCSC hg19 FASTA/refGene bundle: {'; '.join(errors)}"
    ]


def _validate_reference_bundle(
    fasta_path: FilePath, index_path: FilePath, refseq_path: FilePath
) -> list[str]:
    return [
        *_validate_reference_manifest(
            _manifest_path_for(fasta_path, refseq_path),
            fasta_path=fasta_path,
            index_path=index_path,
            refseq_path=refseq_path,
        ),
        *_validate_grch37_fasta_index(index_path),
    ]


def _resolve_reference_bundle(
    settings: Settings,
) -> tuple[FilePath | None, FilePath | None, FilePath | None, list[str]]:
    """Resolve the local Genome Browser reference assets and missing labels."""
    missing: list[str] = []

    fasta_path = settings.resolved_grch37_fasta_path
    if fasta_path is None or not fasta_path.is_file():
        missing.append("GRCh37 FASTA (grch37.fa)")
        fasta_path = None

    index_path: FilePath | None = None
    if fasta_path is not None:
        candidate = _fasta_index_path(fasta_path)
        if candidate.is_file():
            index_path = candidate
        else:
            missing.append("GRCh37 FASTA index (grch37.fa.fai)")

    refseq_path = settings.resolved_genome_browser_refseq_track_path
    if refseq_path is None or not refseq_path.is_file():
        missing.append("RefSeq BED track (grch37_refseq.bed)")
        refseq_path = None

    if fasta_path is not None and index_path is not None and refseq_path is not None:
        validation_errors = _validate_reference_bundle(fasta_path, index_path, refseq_path)
        if validation_errors:
            missing.extend(validation_errors)
            fasta_path = None
            index_path = None
            refseq_path = None

    return fasta_path, index_path, refseq_path, missing


def _local_reference_status(settings: Settings) -> GenomeBrowserReferenceStatus:
    fasta_path, index_path, refseq_path, missing = _resolve_reference_bundle(settings)
    available = fasta_path is not None and index_path is not None and refseq_path is not None
    if not available:
        return GenomeBrowserReferenceStatus(
            available=False,
            mode="remote",
            reference=None,
            tracks=[],
            missing=missing,
        )

    return GenomeBrowserReferenceStatus(
        available=True,
        mode="local",
        reference=IgvReferenceConfig(
            id="hg19-local",
            name="GRCh37/hg19 (local)",
            fastaURL=_REFERENCE_FASTA_URL,
            indexURL=_REFERENCE_FASTA_INDEX_URL,
        ),
        tracks=[
            IgvReferenceTrackConfig(
                name="RefSeq Genes",
                type="annotation",
                format="bed",
                url=_REFERENCE_REFSEQ_URL,
                displayMode="expanded",
                height=80,
                color="#334155",
            )
        ],
        missing=[],
    )


def _serve_reference_asset(path: FilePath | None, *, label: str) -> FileResponse:
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail=f"{label} is not installed.")
    return FileResponse(path, media_type="text/plain")


@router.get("/reference/status", response_model=GenomeBrowserReferenceStatus)
async def genome_browser_reference_status() -> GenomeBrowserReferenceStatus:
    """Report whether local GRCh37 FASTA + RefSeq assets are installed."""
    return _local_reference_status(get_settings())


@router.get("/reference/fasta", response_class=FileResponse)
async def genome_browser_reference_fasta() -> FileResponse:
    """Serve the local GRCh37 FASTA for IGV.js reference range requests."""
    fasta_path, _, _, _ = _resolve_reference_bundle(get_settings())
    return _serve_reference_asset(fasta_path, label="GRCh37 FASTA")


@router.get("/reference/fasta.fai", response_class=FileResponse)
async def genome_browser_reference_fasta_index() -> FileResponse:
    """Serve the local GRCh37 FASTA index for IGV.js."""
    _, index_path, _, _ = _resolve_reference_bundle(get_settings())
    return _serve_reference_asset(index_path, label="GRCh37 FASTA index")


@router.get("/reference/refseq.bed", response_class=FileResponse)
async def genome_browser_reference_refseq_track() -> FileResponse:
    """Serve the local RefSeq BED annotation track for IGV.js."""
    _, _, refseq_path, _ = _resolve_reference_bundle(get_settings())
    return _serve_reference_asset(refseq_path, label="RefSeq BED track")


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

    # gnomad_af.pos is a VCF POS value (1-based); IGV annotation features use
    # BED-like 0-based half-open intervals, so POS p overlaps [start, end) when
    # p > start and p <= end.
    query = sa.text(
        "SELECT rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_eas, af_eur "
        "FROM gnomad_af "
        "WHERE chrom = :chrom AND pos > :start AND pos <= :end "
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
                start=row.pos - 1,
                end=row.pos,
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
