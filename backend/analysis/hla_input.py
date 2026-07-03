"""Sample ``annotated_variants`` → PLINK binary input for HIBAG (Wave D glue).

The SW-D1 HIBAG seam (:mod:`backend.analysis.hibag_runner`) predicts classical
HLA alleles from a sample's **PLINK genotypes** (``.bed``/``.bim``/``.fam``): its
R script calls ``hlaBED2Geno(assembly="hg19")`` and then matches the sample's
SNPs to the pre-fit model by GRCh37 position + allele. This module is the missing
producer of that PLINK fileset — the HLA counterpart of the Wave C imputation
input-prep (:mod:`backend.analysis.imputation_input`), which writes per-chromosome
VCFs for Beagle. It reads the sample's ``annotated_variants`` and writes a single
``<prefix>.bed/.bim/.fam`` over the extended-MHC region on chromosome 6.

**Why ``annotated_variants`` (not the raw genotype string).** HIBAG matches
sample SNPs to the model by GRCh37 position **and allele**, so a record must carry
the true plus-strand GRCh37 ``REF``/``ALT``. The ``annotated_variants`` table
already holds the annotation-resolved ``ref``/``alt`` and a strand-aware
``zygosity`` (``hom_ref``/``het``/``hom_alt`` from
:func:`backend.analysis.zygosity.classify_zygosity`), the same reference-aligned
contract the imputation input-prep consumes. No liftover is needed — these
coordinates are native GRCh37/hg19, which is exactly what ``hlaBED2Geno(assembly=
"hg19")`` expects.

**xMHC window.** Only chromosome-6 records inside the extended MHC (xMHC) are
emitted; HIBAG's classical-locus models only use SNPs from this region (Horton et
al. 2004, *Nat Rev Genet*, PMID:15372022, DOI:10.1038/nrg1489, accessed
2026-07-02). The default window (:data:`XMHC_GRCH37`, chr6:25–34 Mb) is a generous
superset of the extended MHC (~chr6:25.7–33.4 Mb) that brackets every classical
locus HIBAG calls (HLA-A/B/C/DRB1/DQA1/DQB1/DPB1); HIBAG intersects it down to its
own model SNP set, so an over-wide window is harmless while a too-narrow one would
starve the model. The window is configurable.

**Conservative SNP-only filter.** Only biallelic single-nucleotide records with a
resolved ``hom_ref``/``het``/``hom_alt`` zygosity are emitted. Indels,
no-calls/unresolved zygosity, unresolved-reference (``REF=N``) sites, and
ambiguous same-position marker representations are **dropped**, never guessed —
HIBAG cannot match a non-SNP, an ``N`` allele, or a contradictory physical-site
encoding to its SNP model. We never strand-flip (ingest already normalises to the
plus strand); a residual allele mismatch is dropped, not flipped.

**PLINK binary layout** (variant-major .bed, per the PLINK 1 spec): magic bytes
``0x6c 0x1b 0x01``; one block of ``ceil(n_samples/4)`` bytes per SNP; the
low-order two bits hold the first sample. With ``A1=REF``/``A2=ALT`` in the .bim,
the 2-bit codes are ``00`` = hom-ref (homozygous A1), ``10`` = het, ``11`` =
hom-alt (homozygous A2); ``01`` (missing) is never emitted because unresolved
sites are dropped instead. This is a single-sample fileset (the app's per-sample
model), but the packer handles any sample count.

This module only *produces input*; it neither runs Rscript/HIBAG nor surfaces HLA
calls (that is :mod:`backend.analysis.hibag_runner` and the Wave D SW-D2–D5 report
layers).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa
import structlog

from backend.analysis.zygosity import ZYG_HET, ZYG_HOM_ALT, ZYG_HOM_REF

logger = structlog.get_logger(__name__)

# PLINK 1 .bed magic prefix: [0x6c, 0x1b] identify the format, 0x01 selects
# variant-major mode (SNPs in rows, samples in columns).
_BED_MAGIC = bytes((0x6C, 0x1B, 0x01))

_SNP_BASES = frozenset("ACGT")

# 2-bit .bed codes with the .bim convention A1=REF (clear bits), A2=ALT (set
# bits). hom_ref → homozygous A1 (00), het → 10, hom_alt → homozygous A2 (11).
# A no-call/unresolved zygosity yields None and the site is dropped (we never
# emit the 01 "missing" code — a dropped SNP simply isn't in the .bim).
_ZYG_TO_BED_CODE: dict[str, int] = {
    ZYG_HOM_REF: 0b00,
    ZYG_HET: 0b10,
    ZYG_HOM_ALT: 0b11,
}


@dataclass(frozen=True)
class MHCRegion:
    """A GRCh37 chromosome-6 base-pair window to export for HLA imputation."""

    chrom: str = "6"
    start: int = 25_000_000
    end: int = 34_000_000

    def contains(self, chrom: str, pos: int) -> bool:
        # Normalize both sides so a configured ``chr6`` window still matches the
        # bare ``6`` tokens collect_plink_snps compares against (and vice versa).
        return (
            _normalize_chrom_token(chrom) == _normalize_chrom_token(self.chrom)
            and self.start <= pos <= self.end
        )


# Default export window — a generous superset of the extended MHC (Horton 2004),
# brackets every classical HLA locus; HIBAG narrows it to its own model SNPs.
XMHC_GRCH37 = MHCRegion()


@dataclass(frozen=True)
class PlinkSnp:
    """One biallelic-SNP record bound for the PLINK fileset."""

    pos: int
    snp_id: str
    ref: str  # .bim allele 1 (A1, clear bits)
    alt: str  # .bim allele 2 (A2, set bits)
    code: int  # 2-bit .bed genotype code for the single sample


@dataclass
class HibagInputResult:
    """Outcome of writing a sample's HIBAG PLINK input fileset."""

    plink_prefix: Path | None = None  # None when no usable SNP was emitted
    bed_path: Path | None = None
    bim_path: Path | None = None
    fam_path: Path | None = None
    n_total: int = 0  # annotated rows seen
    n_emitted: int = 0  # SNPs written to the fileset

    @property
    def n_dropped(self) -> int:
        """Rows not written (off-region, indel, no-call, unresolved REF)."""
        return self.n_total - self.n_emitted


def _is_snp_allele(allele: str | None) -> bool:
    """True iff ``allele`` is a single A/C/G/T base (a SNP allele HIBAG can match)."""
    return allele is not None and len(allele) == 1 and allele.upper() in _SNP_BASES


def _normalize_chrom_token(chrom: str | None) -> str | None:
    """Normalize a chromosome label to the bare GRCh37 token (drop a ``chr`` prefix)."""
    if chrom is None:
        return None
    c = str(chrom).strip()
    if c[:3].lower() == "chr":
        c = c[3:]
    return c.upper()


def bed_code(ref: str | None, alt: str | None, zygosity: str | None) -> int | None:
    """2-bit .bed genotype code for a biallelic SNP, or ``None`` to drop the site.

    Uses the .bim convention A1=REF, A2=ALT. Drops anything HIBAG cannot align to
    its SNP model: a non-SNP ref/alt (indel, ``REF=N`` unresolved), ref == alt, or
    a zygosity that did not resolve to ``hom_ref``/``het``/``hom_alt``.
    """
    if not _is_snp_allele(ref) or not _is_snp_allele(alt):
        return None
    if (ref or "").upper() == (alt or "").upper():
        return None
    return _ZYG_TO_BED_CODE.get(zygosity or "")


def _drop_ambiguous_same_position_snps(
    snps: Sequence[PlinkSnp],
) -> tuple[list[PlinkSnp], int, int]:
    """Collapse exact duplicate markers and drop discordant same-position markers."""
    by_pos: dict[int, list[PlinkSnp]] = {}
    for snp in snps:
        by_pos.setdefault(snp.pos, []).append(snp)

    resolved: list[PlinkSnp] = []
    ambiguous_positions = 0
    ambiguous_rows = 0
    for pos in sorted(by_pos):
        candidates = by_pos[pos]
        if len(candidates) == 1:
            resolved.append(candidates[0])
            continue

        identities = {(s.snp_id, s.ref, s.alt, s.code) for s in candidates}
        if len(identities) == 1:
            resolved.append(candidates[0])
            continue

        ambiguous_positions += 1
        ambiguous_rows += len(candidates)

    return resolved, ambiguous_positions, ambiguous_rows


def collect_plink_snps(
    rows: Iterable[tuple[str, str, int, str | None, str | None, str | None]],
    region: MHCRegion = XMHC_GRCH37,
) -> tuple[list[PlinkSnp], int, int]:
    """Filter annotated rows to in-region biallelic SNPs, sorted by position.

    Each row is ``(rsid, chrom, pos, ref, alt, zygosity)``. Returns
    ``(snps, n_total, n_emitted)``. A SNP's ``snp_id`` is its rsID when present,
    else a synthetic ``{chrom}:{pos}`` (HIBAG matches on position + allele, not the
    variant ID, so the label only needs to be unique/non-empty). If multiple
    passing rows share a physical coordinate, exact duplicate representations are
    collapsed and discordant rsID/ref/alt/zygosity representations are dropped.
    """
    snps: list[PlinkSnp] = []
    n_total = 0
    for rsid, chrom, pos, ref, alt, zygosity in rows:
        n_total += 1
        chrom_s = _normalize_chrom_token(chrom)
        if chrom_s is None:
            continue
        pos_i = int(pos)
        if not region.contains(chrom_s, pos_i):
            continue
        code = bed_code(ref, alt, zygosity)
        if code is None:
            continue
        snp_id = (rsid or "").strip() or f"{chrom_s}:{pos_i}"
        snps.append(
            PlinkSnp(
                pos=pos_i,
                snp_id=snp_id,
                ref=(ref or "").upper(),
                alt=(alt or "").upper(),
                code=code,
            )
        )
    snps, ambiguous_positions, ambiguous_rows = _drop_ambiguous_same_position_snps(snps)
    if ambiguous_positions:
        logger.info(
            "hibag_input_ambiguous_same_position_markers_dropped",
            n_positions=ambiguous_positions,
            n_rows=ambiguous_rows,
        )
    return snps, n_total, len(snps)


def pack_bed_snp_block(codes: Sequence[int]) -> bytes:
    """Pack one SNP's per-sample 2-bit codes into a variant-major .bed block.

    Samples fill each byte low-order bits first, four per byte; the block is
    ``ceil(len(codes)/4)`` bytes with the unused high bits of the final byte zero.
    """
    out = bytearray()
    for i in range(0, len(codes), 4):
        byte = 0
        for j, code in enumerate(codes[i : i + 4]):
            byte |= (code & 0b11) << (2 * j)
        out.append(byte)
    return bytes(out)


def build_bed_bytes(snps: Sequence[PlinkSnp]) -> bytes:
    """Render the full single-sample .bed payload (magic + one block per SNP)."""
    payload = bytearray(_BED_MAGIC)
    for snp in snps:
        payload += pack_bed_snp_block((snp.code,))
    return bytes(payload)


def build_bim_text(snps: Sequence[PlinkSnp], *, chrom: str = "6") -> str:
    """Render the .bim (chrom, id, cM=0, bp, A1=REF, A2=ALT), one line per SNP."""
    lines = ["\t".join((chrom, snp.snp_id, "0", str(snp.pos), snp.ref, snp.alt)) for snp in snps]
    return "\n".join(lines) + "\n" if lines else ""


def _sanitize_sample_id(sample_name: str) -> str:
    """PLINK .fam is whitespace-delimited — collapse whitespace/control chars."""
    cleaned = "".join(("_" if (c.isspace() or not c.isprintable()) else c) for c in sample_name)
    return cleaned or "SAMPLE"


def build_fam_text(sample_name: str = "SAMPLE") -> str:
    """Render a single-sample .fam line (FID=IID, no parents, unknown sex/pheno)."""
    iid = _sanitize_sample_id(sample_name)
    # Sex 0 = unknown (HLA prediction is sex-independent), phenotype -9 = missing.
    return "\t".join((iid, iid, "0", "0", "0", "-9")) + "\n"


def _read_annotated_variants(
    engine: sa.Engine,
) -> Iterator[tuple[str, str, int, str | None, str | None, str | None]]:
    """Yield ``(rsid, chrom, pos, ref, alt, zygosity)`` from ``annotated_variants``."""
    stmt = sa.text("SELECT rsid, chrom, pos, ref, alt, zygosity FROM annotated_variants")
    with engine.connect() as conn:
        for r in conn.execute(stmt).fetchall():
            yield (r.rsid, r.chrom, r.pos, r.ref, r.alt, r.zygosity)


def write_hibag_plink_input(
    sample_engine: sa.Engine,
    out_prefix: Path,
    *,
    region: MHCRegion = XMHC_GRCH37,
    sample_name: str = "SAMPLE",
) -> HibagInputResult:
    """Write a sample's HIBAG PLINK fileset (``<prefix>.bed/.bim/.fam``).

    Reads the sample's ``annotated_variants``, keeps reference-aligned biallelic
    SNPs inside ``region`` on chromosome 6, and writes the three PLINK files that
    ``hibag_runner.HibagRunner.predict`` consumes. When no usable SNP is present,
    **no files are written** and the result carries a ``None`` prefix (graceful —
    the caller reports the sample unreachable rather than handing HIBAG an empty
    fileset). The parent directory is created if needed.
    """
    out_prefix = Path(out_prefix)
    snps, n_total, n_emitted = collect_plink_snps(_read_annotated_variants(sample_engine), region)
    result = HibagInputResult(n_total=n_total, n_emitted=n_emitted)
    if not snps:
        logger.info(
            "hibag_input_empty",
            out_prefix=str(out_prefix),
            n_total=n_total,
            region=f"{region.chrom}:{region.start}-{region.end}",
        )
        return result

    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    # APPEND the extensions (never with_suffix): a prefix may contain dots (e.g.
    # ``sample.hla``), and ``hibag_runner.HibagRunner.predict`` / the R script's
    # ``paste0(prefix, ".bed")`` both append — with_suffix would replace the
    # trailing ``.hla`` and write files HIBAG then cannot find.
    bed_path = Path(f"{out_prefix}.bed")
    bim_path = Path(f"{out_prefix}.bim")
    fam_path = Path(f"{out_prefix}.fam")
    bed_path.write_bytes(build_bed_bytes(snps))
    bim_path.write_text(build_bim_text(snps, chrom=region.chrom), encoding="utf-8")
    fam_path.write_text(build_fam_text(sample_name), encoding="utf-8")

    result.plink_prefix = out_prefix
    result.bed_path = bed_path
    result.bim_path = bim_path
    result.fam_path = fam_path
    logger.info(
        "hibag_input_written",
        out_prefix=str(out_prefix),
        n_total=n_total,
        n_emitted=n_emitted,
        n_dropped=result.n_dropped,
    )
    return result
