"""VCF 4.2 export.

VCF genotype (``GT``) fields are allele-indexed against ``REF``/``ALT`` (``0`` =
REF), so a SNP-array call can only be represented correctly once its observed
alleles are placed against the genome reference. When a caller supplies the
annotation-resolved ``ref``/``alt`` and a strand-aware ``zygosity``
(``hom_ref``/``het``/``hom_alt`` from :func:`backend.analysis.zygosity.classify_zygosity`),
each record is emitted reference-aligned, so a true homozygous-alternate call
shows ``GT=1/1`` (never a false ``0/0``) and the heterozygous ``REF``/``ALT``
follow biology rather than raw allele-string order.

When the reference allele is unresolved (no annotation, or no source-supplied
allele identity), the export does **not** fabricate a reference-genome ``0/0``:
``REF`` is set to ``N`` and the observed bases are emitted as ``ALT``, so
alternate-allele carriage is never hidden. No-call genotypes ('--') are skipped
by default.

Reference: VCF 4.2 specification
  https://samtools.github.io/hts-specs/VCFv4.2.pdf
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Sequence
from datetime import date
from pathlib import Path
from typing import TextIO

from backend.analysis.zygosity import ZYG_HET, ZYG_HOM_ALT, ZYG_HOM_REF
from backend.ingestion.chrom_order import CHROM_ORDER as _CHROM_ORDER

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VCF_VERSION = "VCFv4.2"
_SOURCE = "Yeliztli"
_REFERENCE = "GRCh37"

# VCF header column names.
_VCF_COLUMNS = (
    "#CHROM",
    "POS",
    "ID",
    "REF",
    "ALT",
    "QUAL",
    "FILTER",
    "INFO",
    "FORMAT",
    "SAMPLE",
)

# Valid nucleotide bases for VCF allele fields.
_VALID_BASES: frozenset[str] = frozenset("ACGT")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _chrom_sort_key(chrom: str) -> int:
    """Return an integer sort key for a chromosome string."""
    return _CHROM_ORDER.get(chrom, 99)


def _resolve_vcf_fields(
    genotype: str | None,
    ref: str | None = None,
    alt: str | None = None,
    zygosity: str | None = None,
) -> tuple[str, str, str] | None:
    """Resolve VCF ``(REF, ALT, GT)`` for one variant.

    Returns ``None`` for no-call genotypes ('--'), empty strings, and genotypes
    with no nucleotide bases (e.g. D/I indel codes from 23andMe v3), so the
    caller can skip them.

    Reference-aligned path — when ``ref``/``alt`` are present and ``zygosity`` is
    one of ``hom_ref``/``het``/``hom_alt`` (resolved by ``classify_zygosity``
    against the plus-strand reference) — emit a standard reference-aligned record
    so a homozygous-alternate call is ``GT=1/1`` (never a false ``0/0``) and the
    heterozygous ``REF``/``ALT`` follow biology, not raw allele-string order.

    Honest fallback — when the reference allele is unresolved — set ``REF=N`` and
    emit every distinct observed base as an ``ALT``, never claiming an observed
    allele is the reference. Haploid calls (Y/MT) use haploid ``GT`` notation.
    """
    if not genotype or genotype == "--":
        return None
    gt = genotype.strip().upper()
    if not gt:
        return None
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

    # Honest fallback: reference base unknown. Emit observed bases as ALT against
    # REF=N; never claim a reference-genome 0/0. Non-nucleotide calls (indel
    # codes) have no observed bases → skipped.
    observed: list[str] = []
    for base in gt:
        if base in _VALID_BASES and base not in observed:
            observed.append(base)
    if not observed:
        return None
    alt_field = ",".join(observed)
    if haploid:
        gt_field = "1"
    elif len(observed) == 1:
        gt_field = "1/1"  # homozygous observed (e.g. CC) vs unknown reference
    else:
        gt_field = "1/2"  # heterozygous observed (e.g. AG) — two distinct ALTs
    return "N", alt_field, gt_field


def _build_header_lines(
    sample_name: str = "SAMPLE",
    file_date: date | None = None,
) -> list[str]:
    """Build VCF meta-information and header lines."""
    if file_date is None:
        file_date = date.today()

    # Sanitize sample name: strip tabs, newlines, control characters.
    safe_name = (
        "".join(c for c in sample_name if c.isprintable() and c not in "\t\n\r") or "SAMPLE"
    )

    lines = [
        f"##fileformat={_VCF_VERSION}",
        f"##fileDate={file_date.strftime('%Y%m%d')}",
        f"##source={_SOURCE}",
        f"##reference={_REFERENCE}",
        (
            "##Yeliztli_note=REF/ALT/GT are reference-aligned (GT indexed against "
            "the annotation-resolved reference allele) when annotation resolved "
            "the locus. Where the reference allele is unresolved, REF=N and the "
            "observed bases are emitted as ALT — never a fabricated reference 0/0."
        ),
        '##FILTER=<ID=PASS,Description="All filters passed">',
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
    ]

    # Contig lines for standard chromosomes.
    for chrom in sorted(_CHROM_ORDER, key=_chrom_sort_key):
        lines.append(f"##contig=<ID={chrom}>")

    # Column header — replace "SAMPLE" with sanitized sample name.
    cols = list(_VCF_COLUMNS)
    cols[-1] = safe_name
    lines.append("\t".join(cols))

    return lines


# ---------------------------------------------------------------------------
# Data row type
# ---------------------------------------------------------------------------


class _VariantRow:
    """Lightweight container for a variant to be exported.

    ``ref``/``alt``/``zygosity`` are the annotation-resolved, reference-aligned
    fields (from ``annotated_variants``); they are ``None`` when the caller only
    has the raw genotype, in which case the export uses the honest ``REF=N``
    fallback rather than inferring REF/ALT from the genotype string.
    """

    __slots__ = ("rsid", "chrom", "pos", "genotype", "ref", "alt", "zygosity")

    def __init__(
        self,
        rsid: str,
        chrom: str,
        pos: int,
        genotype: str,
        ref: str | None = None,
        alt: str | None = None,
        zygosity: str | None = None,
    ) -> None:
        self.rsid = rsid
        self.chrom = chrom
        self.pos = pos
        self.genotype = genotype
        self.ref = ref
        self.alt = alt
        self.zygosity = zygosity


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def export_vcf_from_rows(
    variants: Iterable[Sequence],
    dest: str | Path | TextIO | None = None,
    *,
    sample_name: str = "SAMPLE",
    skip_nocalls: bool = True,
    file_date: date | None = None,
) -> str:
    """Export variant rows to VCF 4.2 format.

    Parameters
    ----------
    variants:
        Iterable of per-variant sequences. The first four elements are
        ``(rsid, chrom, pos, genotype)``. Optional trailing elements supply the
        annotation-resolved, reference-aligned fields ``(ref, alt, zygosity)``;
        when present, the record is emitted reference-aligned (``GT`` indexed
        against ``ref``). When absent (a 4-element row), the export uses the
        honest ``REF=N`` fallback rather than inferring REF/ALT from the genotype
        string. Rows are sorted by (chrom, pos) in canonical order internally.
    dest:
        Destination — a file path (str/Path), a writable text stream, or
        ``None`` to return the VCF content as a string.
    sample_name:
        Name used in the VCF sample column header.
    skip_nocalls:
        If True (default), skip variants with '--' or empty genotype.
    file_date:
        Date for the ``##fileDate`` header. Defaults to today.

    Returns
    -------
    str
        The VCF content as a string. If *dest* is a file path, the string
        is also written to that file.
    """
    # Materialise and sort. Rows are length-4 (rsid, chrom, pos, genotype) or
    # length-7 with trailing (ref, alt, zygosity).
    rows = [
        _VariantRow(
            v[0],
            v[1],
            v[2],
            v[3],
            v[4] if len(v) > 4 else None,
            v[5] if len(v) > 5 else None,
            v[6] if len(v) > 6 else None,
        )
        for v in variants
    ]
    rows.sort(key=lambda r: (_chrom_sort_key(r.chrom), r.pos))

    header_lines = _build_header_lines(sample_name=sample_name, file_date=file_date)

    buf = io.StringIO()
    for line in header_lines:
        buf.write(line)
        buf.write("\n")

    for row in rows:
        fields = _resolve_vcf_fields(row.genotype, row.ref, row.alt, row.zygosity)
        if fields is None:
            if skip_nocalls:
                continue
            # Emit no-call with missing GT.
            ref, alt, gt = "N", ".", "./."
        else:
            ref, alt, gt = fields

        data_line = "\t".join(
            [
                row.chrom,
                str(row.pos),
                row.rsid,
                ref,
                alt,
                ".",  # QUAL
                "PASS",  # FILTER
                ".",  # INFO
                "GT",  # FORMAT
                gt,  # sample genotype
            ]
        )
        buf.write(data_line)
        buf.write("\n")

    content = buf.getvalue()

    # Write to destination if provided.
    if dest is not None:
        if isinstance(dest, (str, Path)):
            Path(dest).write_text(content, encoding="utf-8")
        else:
            dest.write(content)

    return content
