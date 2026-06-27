"""Per-chromosome GRCh37 imputation-input VCFs from a sample DB (Wave C glue).

The SW-C2 runtime (:mod:`backend.analysis.imputation_runner`) imputes a sample's
typed genotypes against the 1000G Phase 3 v5a panel (SW-C1) by handing Beagle a
per-chromosome ``gt=`` VCF; ``scripts/run_imputation.py`` consumes ready
``chr{N}.vcf.gz`` files. This module is the missing producer of those files — it
reads the sample's ``annotated_variants`` and writes one bgzipped, tabix-indexed,
coordinate-sorted VCF per autosome.

**Why ``annotated_variants`` (not the raw genotype string).** Imputation needs the
sample's calls **reference-aligned** to the panel: Beagle indexes ``GT`` against
``REF``/``ALT`` and matches markers to the bref3 panel by position **and** allele,
so a record must carry the true plus-strand GRCh37 ``REF``/``ALT``. The
``annotated_variants`` table already holds the annotation-resolved ``ref``/``alt``
and a strand-aware ``zygosity`` (``hom_ref``/``het``/``hom_alt`` from
:func:`backend.analysis.zygosity.classify_zygosity`), so a homozygous-alternate
call becomes ``GT=1/1`` (never a false ``0/0``) — the same reference-aligned
contract :mod:`backend.ingestion.vcf_export` ships. No liftover is needed: the
panel and these coordinates are both native GRCh37 (unlike the LAI path, which
lifts to GRCh38).

**Conservative SNP-only filter.** Only biallelic single-nucleotide records with a
resolved ``hom_ref``/``het``/``hom_alt`` zygosity are emitted. Anything else —
indels, no-calls, multi-allelic alts, or a site whose reference allele annotation
never resolved (the ``REF=N`` honest-fallback case) — is **dropped**, never
guessed: an allele that does not match the panel would be excluded by Beagle
anyway, and emitting ``REF=N`` cannot align to the panel at all. We never
strand-flip; ingest already normalises alleles to the plus strand
(:mod:`backend.ingestion.liftover`), so a residual mismatch is dropped, not
flipped (flipping a palindromic SNP would be unsafe).

**Scope (v1): autosomes 1-22.** Chromosome X is in the panel but needs
ploidy-aware handling (haploid male non-PAR X, the pseudo-autosomal regions);
producing correct X input is a deliberate follow-up. ``run_imputation.py`` simply
finds no ``chrX.vcf.gz`` and skips it.

This module only *produces input*; it neither runs Beagle nor gates findings
(SW-C2 / SW-C3 respectively).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
import structlog

from backend.analysis.zygosity import ZYG_HET, ZYG_HOM_ALT, ZYG_HOM_REF
from backend.annotation.imputation_panel import PANEL_BUILD

logger = structlog.get_logger(__name__)

# Autosomes only for v1 (X deferred — see module docstring). Kept independent of
# PANEL_CHROMOSOMES (which includes X) so the input scope is explicit.
INPUT_CHROMOSOMES: tuple[str, ...] = tuple(str(i) for i in range(1, 23))

# Reference-aligned, diploid GT per resolved zygosity. A zygosity outside this map
# (None / no-call / anything classify_zygosity could not resolve) drops the site.
_ZYG_TO_GT: dict[str, str] = {
    ZYG_HOM_REF: "0/0",
    ZYG_HET: "0/1",
    ZYG_HOM_ALT: "1/1",
}

_SNP_BASES = frozenset("ACGT")

_VCF_SOURCE = "Yeliztli"


@dataclass(frozen=True)
class InputSite:
    """One reference-aligned biallelic-SNP record bound for the imputation input VCF."""

    pos: int
    rsid: str
    ref: str
    alt: str
    gt: str


@dataclass
class ImputationInputResult:
    """Outcome of writing a sample's per-chromosome imputation-input VCFs."""

    vcf_paths: dict[str, Path] = field(default_factory=dict)  # chrom -> written file
    per_chrom_emitted: dict[str, int] = field(default_factory=dict)
    n_total: int = 0  # annotated rows seen
    n_emitted: int = 0  # rows written across all chromosomes

    @property
    def n_dropped(self) -> int:
        """Rows not written (out-of-scope chrom, indel, no-call, unresolved REF)."""
        return self.n_total - self.n_emitted


def _is_snp_allele(allele: str | None) -> bool:
    """True iff ``allele`` is a single A/C/G/T base (a SNP allele the panel carries)."""
    return allele is not None and len(allele) == 1 and allele.upper() in _SNP_BASES


def encode_input_gt(ref: str | None, alt: str | None, zygosity: str | None) -> str | None:
    """Reference-aligned diploid ``GT`` for a biallelic SNP, or ``None`` to drop.

    Drops anything that cannot be aligned to the SNP panel: a non-SNP ref/alt
    (indel, multi-allelic, ``REF=N`` unresolved), ref == alt, or a zygosity that
    did not resolve to ``hom_ref``/``het``/``hom_alt`` (e.g. a no-call).
    """
    if not _is_snp_allele(ref) or not _is_snp_allele(alt):
        return None
    if (ref or "").upper() == (alt or "").upper():
        return None
    return _ZYG_TO_GT.get(zygosity or "")


def collect_input_sites(
    rows: Iterable[tuple[str, str, int, str | None, str | None, str | None]],
    chromosomes: tuple[str, ...] = INPUT_CHROMOSOMES,
) -> tuple[dict[str, list[InputSite]], int, int]:
    """Group annotated rows into per-chromosome reference-aligned SNP sites.

    Each row is ``(rsid, chrom, pos, ref, alt, zygosity)``. Returns
    ``(sites_by_chrom, n_total, n_emitted)``; sites for each chromosome are
    returned in input order (the writer sorts them by position).
    """
    wanted = set(chromosomes)
    by_chrom: dict[str, list[InputSite]] = {c: [] for c in chromosomes}
    n_total = 0
    n_emitted = 0
    for rsid, chrom, pos, ref, alt, zygosity in rows:
        n_total += 1
        chrom_s = str(chrom)
        if chrom_s not in wanted:
            continue
        gt = encode_input_gt(ref, alt, zygosity)
        if gt is None:
            continue
        by_chrom[chrom_s].append(
            InputSite(
                pos=int(pos), rsid=rsid, ref=(ref or "").upper(), alt=(alt or "").upper(), gt=gt
            )
        )
        n_emitted += 1
    return by_chrom, n_total, n_emitted


def _sanitize_sample_name(sample_name: str) -> str:
    """Strip tabs/newlines/control chars from the VCF sample column (mirrors vcf_export)."""
    cleaned = "".join(c for c in sample_name if c.isprintable() and c not in "\t\n\r")
    return cleaned or "SAMPLE"


def build_chrom_vcf_text(
    chrom: str, sites: Iterable[InputSite], *, sample_name: str = "SAMPLE"
) -> str:
    """Render a coordinate-sorted, single-contig VCF 4.2 for one chromosome.

    Records are sorted by position (required for bgzip+tabix). The internal
    ``#CHROM`` value is the bare GRCh37 token (e.g. ``22``) so it matches the b37
    panel's contig naming, even though the file is named ``chr{N}.vcf.gz``.
    """
    safe_name = _sanitize_sample_name(sample_name)
    lines = [
        "##fileformat=VCFv4.2",
        f"##source={_VCF_SOURCE}",
        f"##reference={PANEL_BUILD}",
        f"##contig=<ID={chrom}>",
        '##FILTER=<ID=PASS,Description="All filters passed">',
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        "\t".join(
            ("#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT", safe_name)
        ),
    ]
    for s in sorted(sites, key=lambda site: site.pos):
        lines.append(
            "\t".join((chrom, str(s.pos), s.rsid, s.ref, s.alt, ".", "PASS", ".", "GT", s.gt))
        )
    return "\n".join(lines) + "\n"


def _write_bgzipped_indexed(path: Path, text: str) -> None:
    """Write ``text`` as a bgzipped, tabix-indexed VCF at ``path`` (via pysam)."""
    import pysam

    with pysam.BGZFile(str(path), "wb") as bgz:
        bgz.write(text.encode("utf-8"))
    pysam.tabix_index(str(path), preset="vcf", force=True)


def _read_annotated_variants(
    engine: sa.Engine,
) -> Iterator[tuple[str, str, int, str | None, str | None, str | None]]:
    """Yield ``(rsid, chrom, pos, ref, alt, zygosity)`` from ``annotated_variants``."""
    stmt = sa.text("SELECT rsid, chrom, pos, ref, alt, zygosity FROM annotated_variants")
    with engine.connect() as conn:
        for r in conn.execute(stmt).fetchall():
            yield (r.rsid, r.chrom, r.pos, r.ref, r.alt, r.zygosity)


def write_imputation_input_vcfs(
    sample_engine: sa.Engine,
    out_dir: Path,
    *,
    chromosomes: tuple[str, ...] = INPUT_CHROMOSOMES,
    sample_name: str = "SAMPLE",
) -> ImputationInputResult:
    """Write per-chromosome imputation-input VCFs for a sample.

    Reads the sample's ``annotated_variants``, keeps reference-aligned biallelic
    SNPs on the requested autosomes, and writes one coordinate-sorted, bgzipped,
    tabix-indexed ``chr{N}.vcf.gz`` per chromosome that has at least one emitted
    site (chromosomes with no usable sites produce no file). The output directory
    is created if needed.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    by_chrom, n_total, n_emitted = collect_input_sites(
        _read_annotated_variants(sample_engine), chromosomes
    )

    result = ImputationInputResult(n_total=n_total, n_emitted=n_emitted)
    for chrom in chromosomes:
        sites = by_chrom.get(chrom, [])
        if not sites:
            continue
        text = build_chrom_vcf_text(chrom, sites, sample_name=sample_name)
        path = out_dir / f"chr{chrom}.vcf.gz"
        _write_bgzipped_indexed(path, text)
        result.vcf_paths[chrom] = path
        result.per_chrom_emitted[chrom] = len(sites)

    logger.info(
        "imputation_input_written",
        out_dir=str(out_dir),
        chromosomes=len(result.vcf_paths),
        n_total=n_total,
        n_emitted=n_emitted,
        n_dropped=result.n_dropped,
    )
    return result
