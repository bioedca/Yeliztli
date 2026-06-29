"""Per-chromosome GRCh37 imputation-input VCFs from a sample DB (Wave C glue).

The SW-C2 runtime (:mod:`backend.analysis.imputation_runner`) imputes a sample's
typed genotypes against the 1000G Phase 3 v5a panel (SW-C1) by handing Beagle a
per-chromosome ``gt=`` VCF; ``scripts/run_imputation.py`` consumes ready
``chr{N}.vcf.gz`` files. This module is the missing producer of those files — it
reads the sample's ``annotated_variants`` and writes one bgzipped, tabix-indexed,
coordinate-sorted VCF per requested autosome and, when resolved biological sex is
supplied, per chromosome-X region.

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

**Chromosome X.** X input is deliberately region-aware. Beagle requires
pseudoautosomal and non-pseudoautosomal X genotypes to be analysed separately
unless male haploid genotypes are flattened to homozygous diploid calls. We keep
the real ploidy instead: PAR1/PAR2 are emitted as diploid for both XX and XY
samples, while XY non-PAR X is emitted as haploid (``0``/``1``; impossible/noisy
male non-PAR heterozygotes are dropped). X is only produced when the caller
provides resolved ``biological_sex`` (``XX`` or ``XY``); autosomes remain the
default no-sex-required path.

This module only *produces input*; it neither runs Beagle nor gates findings
(SW-C2 / SW-C3 respectively).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import sqlalchemy as sa
import structlog

from backend.analysis.zygosity import ZYG_HET, ZYG_HOM_ALT, ZYG_HOM_REF
from backend.annotation.imputation_panel import PANEL_BUILD
from backend.services.sex_inference import GRCH37_X_PAR1, GRCH37_X_PAR2

logger = structlog.get_logger(__name__)

AUTOSOMAL_INPUT_CHROMOSOMES: tuple[str, ...] = tuple(str(i) for i in range(1, 23))

# Supported input chromosome choices. Autosomes remain the default; X must be
# explicitly requested with resolved biological sex because its GT ploidy depends
# on PAR/non-PAR context.
INPUT_CHROMOSOMES: tuple[str, ...] = (*AUTOSOMAL_INPUT_CHROMOSOMES, "X")
DEFAULT_INPUT_CHROMOSOMES: tuple[str, ...] = AUTOSOMAL_INPUT_CHROMOSOMES

# Reference-aligned, diploid GT per resolved zygosity. A zygosity outside this map
# (None / no-call / anything classify_zygosity could not resolve) drops the site.
_ZYG_TO_GT: dict[str, str] = {
    ZYG_HOM_REF: "0/0",
    ZYG_HET: "0/1",
    ZYG_HOM_ALT: "1/1",
}
_ZYG_TO_HAPLOID_GT: dict[str, str] = {
    ZYG_HOM_REF: "0",
    ZYG_HOM_ALT: "1",
}

_SNP_BASES = frozenset("ACGT")

_VCF_SOURCE = "Yeliztli"

Ploidy = Literal["diploid", "haploid"]


@dataclass(frozen=True)
class InputUnitSpec:
    """One Beagle input/execution unit produced by this module."""

    key: str  # "1".."22" or an X region key such as "X_PAR1"
    chrom: str  # VCF #CHROM / panel chromosome token ("1".."22" / "X")
    filename: str
    beagle_region: str | None = None  # Beagle chrom= interval for split X units


@dataclass(frozen=True)
class ImputationInputUnit:
    """A written input VCF plus the Beagle interval it must be run against."""

    key: str
    chrom: str
    path: Path
    beagle_region: str | None = None


_AUTOSOME_UNIT_SPECS: dict[str, InputUnitSpec] = {
    chrom: InputUnitSpec(key=chrom, chrom=chrom, filename=f"chr{chrom}.vcf.gz")
    for chrom in AUTOSOMAL_INPUT_CHROMOSOMES
}

_X_NONPAR1_END = GRCH37_X_PAR1[0] - 1
_X_NONPAR2_START = GRCH37_X_PAR1[1] + 1
_X_NONPAR2_END = GRCH37_X_PAR2[0] - 1
_X_NONPAR3_START = GRCH37_X_PAR2[1] + 1

_X_UNIT_SPECS: tuple[InputUnitSpec, ...] = (
    InputUnitSpec("X_NONPAR1", "X", "chrX_NONPAR1.vcf.gz", f"X:-{_X_NONPAR1_END}"),
    InputUnitSpec(
        "X_PAR1",
        "X",
        "chrX_PAR1.vcf.gz",
        f"X:{GRCH37_X_PAR1[0]}-{GRCH37_X_PAR1[1]}",
    ),
    InputUnitSpec(
        "X_NONPAR2",
        "X",
        "chrX_NONPAR2.vcf.gz",
        f"X:{_X_NONPAR2_START}-{_X_NONPAR2_END}",
    ),
    InputUnitSpec(
        "X_PAR2",
        "X",
        "chrX_PAR2.vcf.gz",
        f"X:{GRCH37_X_PAR2[0]}-{GRCH37_X_PAR2[1]}",
    ),
    InputUnitSpec("X_NONPAR3", "X", "chrX_NONPAR3.vcf.gz", f"X:{_X_NONPAR3_START}-"),
)
_X_UNIT_BY_KEY: dict[str, InputUnitSpec] = {spec.key: spec for spec in _X_UNIT_SPECS}


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

    vcf_paths: dict[str, Path] = field(default_factory=dict)  # input unit key -> written file
    per_chrom_emitted: dict[str, int] = field(default_factory=dict)  # input unit key -> rows
    units: list[ImputationInputUnit] = field(default_factory=list)
    n_total: int = 0  # annotated rows seen
    n_emitted: int = 0  # rows written across all chromosomes

    @property
    def n_dropped(self) -> int:
        """Rows not written (out-of-scope chrom, indel, no-call, unresolved REF)."""
        return self.n_total - self.n_emitted


def _is_snp_allele(allele: str | None) -> bool:
    """True iff ``allele`` is a single A/C/G/T base (a SNP allele the panel carries)."""
    return allele is not None and len(allele) == 1 and allele.upper() in _SNP_BASES


def _normalize_chrom_token(chrom: str | None) -> str | None:
    """Normalize chromosome labels for input-scope matching."""
    if chrom is None:
        return None
    c = str(chrom).strip()
    if c[:3].lower() == "chr":
        c = c[3:]
    return c.upper()


def _normalize_requested_chromosomes(chromosomes: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize and validate requested input chromosomes, preserving order."""
    out: list[str] = []
    seen: set[str] = set()
    supported = set(INPUT_CHROMOSOMES)
    for chrom in chromosomes:
        c = _normalize_chrom_token(chrom)
        if c not in supported:
            raise ValueError(f"unsupported imputation-input chromosome: {chrom!r}")
        if c not in seen:
            out.append(c)
            seen.add(c)
    return tuple(out)


def _normalize_biological_sex(biological_sex: str | None) -> str | None:
    if biological_sex is None:
        return None
    sex = biological_sex.strip().upper()
    return sex if sex in {"XX", "XY"} else None


def _require_resolved_sex_for_x(
    chromosomes: tuple[str, ...], biological_sex: str | None
) -> str | None:
    sex = _normalize_biological_sex(biological_sex)
    if "X" in chromosomes and sex is None:
        raise ValueError(
            "biological_sex must be 'XX' or 'XY' when chromosome X imputation input is requested"
        )
    return sex


def input_unit_specs_for_chromosomes(
    chromosomes: tuple[str, ...] = DEFAULT_INPUT_CHROMOSOMES,
) -> tuple[InputUnitSpec, ...]:
    """Return Beagle input/execution units for requested chromosomes."""
    specs: list[InputUnitSpec] = []
    for chrom in _normalize_requested_chromosomes(chromosomes):
        if chrom == "X":
            specs.extend(_X_UNIT_SPECS)
        else:
            specs.append(_AUTOSOME_UNIT_SPECS[chrom])
    return tuple(specs)


def _x_unit_for_pos(pos: int) -> InputUnitSpec:
    """Return the X execution unit containing one GRCh37 chrX position."""
    if pos <= _X_NONPAR1_END:
        return _X_UNIT_BY_KEY["X_NONPAR1"]
    if GRCH37_X_PAR1[0] <= pos <= GRCH37_X_PAR1[1]:
        return _X_UNIT_BY_KEY["X_PAR1"]
    if _X_NONPAR2_START <= pos <= _X_NONPAR2_END:
        return _X_UNIT_BY_KEY["X_NONPAR2"]
    if GRCH37_X_PAR2[0] <= pos <= GRCH37_X_PAR2[1]:
        return _X_UNIT_BY_KEY["X_PAR2"]
    return _X_UNIT_BY_KEY["X_NONPAR3"]


def _ploidy_for_unit(spec: InputUnitSpec, biological_sex: str | None) -> Ploidy:
    """Input GT ploidy for one execution unit and resolved sex."""
    if spec.chrom != "X" or spec.key in {"X_PAR1", "X_PAR2"}:
        return "diploid"
    return "haploid" if biological_sex == "XY" else "diploid"


def encode_input_gt(
    ref: str | None,
    alt: str | None,
    zygosity: str | None,
    *,
    ploidy: Ploidy = "diploid",
) -> str | None:
    """Reference-aligned ``GT`` for a biallelic SNP, or ``None`` to drop.

    Drops anything that cannot be aligned to the SNP panel: a non-SNP ref/alt
    (indel, multi-allelic, ``REF=N`` unresolved), ref == alt, or a zygosity that
    did not resolve to ``hom_ref``/``het``/``hom_alt`` (e.g. a no-call).
    ``ploidy='haploid'`` is used only for resolved XY non-PAR chromosome X: male
    non-PAR heterozygotes are biologically inconsistent/noisy and are dropped.
    """
    if not _is_snp_allele(ref) or not _is_snp_allele(alt):
        return None
    if (ref or "").upper() == (alt or "").upper():
        return None
    if ploidy == "diploid":
        return _ZYG_TO_GT.get(zygosity or "")
    if ploidy == "haploid":
        return _ZYG_TO_HAPLOID_GT.get(zygosity or "")
    raise ValueError(f"unsupported input ploidy: {ploidy!r}")


def collect_input_sites(
    rows: Iterable[tuple[str, str, int, str | None, str | None, str | None]],
    chromosomes: tuple[str, ...] = DEFAULT_INPUT_CHROMOSOMES,
    *,
    biological_sex: str | None = None,
) -> tuple[dict[str, list[InputSite]], int, int]:
    """Group annotated rows into Beagle input-unit reference-aligned SNP sites.

    Each row is ``(rsid, chrom, pos, ref, alt, zygosity)``. Returns
    ``(sites_by_unit, n_total, n_emitted)``; sites for each input unit are returned
    in input order (the writer sorts them by position). When X is requested,
    ``biological_sex`` must be resolved to ``XX`` or ``XY``.
    """
    chromosomes = _normalize_requested_chromosomes(chromosomes)
    sex = _require_resolved_sex_for_x(chromosomes, biological_sex)
    wanted = set(chromosomes)
    specs = input_unit_specs_for_chromosomes(chromosomes)
    by_unit: dict[str, list[InputSite]] = {spec.key: [] for spec in specs}
    n_total = 0
    n_emitted = 0
    for rsid, chrom, pos, ref, alt, zygosity in rows:
        n_total += 1
        chrom_s = _normalize_chrom_token(chrom)
        if chrom_s not in wanted:
            continue
        pos_i = int(pos)
        spec = _x_unit_for_pos(pos_i) if chrom_s == "X" else _AUTOSOME_UNIT_SPECS[chrom_s]
        gt = encode_input_gt(ref, alt, zygosity, ploidy=_ploidy_for_unit(spec, sex))
        if gt is None:
            continue
        by_unit[spec.key].append(
            InputSite(
                pos=pos_i, rsid=rsid, ref=(ref or "").upper(), alt=(alt or "").upper(), gt=gt
            )
        )
        n_emitted += 1
    return by_unit, n_total, n_emitted


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
    chromosomes: tuple[str, ...] = DEFAULT_INPUT_CHROMOSOMES,
    biological_sex: str | None = None,
    sample_name: str = "SAMPLE",
) -> ImputationInputResult:
    """Write imputation-input VCFs for a sample.

    Reads the sample's ``annotated_variants``, keeps reference-aligned biallelic
    SNPs on the requested chromosomes, and writes one coordinate-sorted, bgzipped,
    tabix-indexed VCF per Beagle input unit that has at least one emitted site
    (chromosomes/regions with no usable sites produce no file). Autosomes write
    ``chr{N}.vcf.gz``; X writes split region files such as ``chrX_PAR1.vcf.gz``
    and requires resolved ``biological_sex``. The output directory is created if
    needed.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    chromosomes = _normalize_requested_chromosomes(chromosomes)
    specs = input_unit_specs_for_chromosomes(chromosomes)

    by_unit, n_total, n_emitted = collect_input_sites(
        _read_annotated_variants(sample_engine),
        chromosomes,
        biological_sex=biological_sex,
    )

    result = ImputationInputResult(n_total=n_total, n_emitted=n_emitted)
    for spec in specs:
        sites = by_unit.get(spec.key, [])
        if not sites:
            continue
        text = build_chrom_vcf_text(spec.chrom, sites, sample_name=sample_name)
        path = out_dir / spec.filename
        _write_bgzipped_indexed(path, text)
        result.vcf_paths[spec.key] = path
        result.per_chrom_emitted[spec.key] = len(sites)
        result.units.append(
            ImputationInputUnit(
                key=spec.key,
                chrom=spec.chrom,
                path=path,
                beagle_region=spec.beagle_region,
            )
        )

    logger.info(
        "imputation_input_written",
        out_dir=str(out_dir),
        chromosomes=len(result.vcf_paths),
        n_total=n_total,
        n_emitted=n_emitted,
        n_dropped=result.n_dropped,
    )
    return result
