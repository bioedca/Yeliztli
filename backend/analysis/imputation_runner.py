"""Local Beagle 5.x phase+impute runtime (SW-C2 / roadmap #2).

Runs genotype imputation locally with Beagle 5.x against the 1000G Phase 3 v5a
reference panel shipped by SW-C1 (:mod:`backend.annotation.imputation_panel`),
reusing the **same self-contained Beagle JAR already vendored in the LAI bundle**
(invoked via ``subprocess``, never imported — the GPL boundary never reaches the
MIT app code, identical to :mod:`backend.analysis.lai_runner`). For each
chromosome it imputes the sample's typed genotypes up to the panel markers and
records Beagle's per-variant **DR2 (dosage R²)** imputation-quality metric, which
the SW-C3 firewall combines with MAF to quarantine unreliable imputed rare
variants.

**Verified Beagle output (5.5, real run 2026-06-26):** the imputed VCF carries
``DR2`` (Number=A dosage R², 0-1), ``AF`` (Number=A), and the ``IMP`` flag on
markers present only in the reference (i.e. imputed, not genotyped); FORMAT is
``GT:DS``. Imputation runs automatically when ``ref=`` has markers absent from
``gt=``; a genetic map is supplied for accurate recombination rates.

**Runtime measurement.** :meth:`ImputationRunner.impute_chromosome` times each
Beagle invocation (wall-clock) and ``scripts/run_imputation.py`` reports the
total — so the per-laptop runtime the Wave C plan calls for is measured on
whatever machine actually runs it.

This module runs and parses imputation; it does not yet wire results into the
per-sample DB or the annotation pipeline (a following slice), and it does not
itself gate findings (that is SW-C3).
"""

from __future__ import annotations

import gzip
import math
import re
import subprocess
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

import structlog

from backend.annotation.imputation_panel import panel_bref3_path, panel_map_path

logger = structlog.get_logger(__name__)

DEFAULT_JAVA_MEM = "8g"
# Per-chromosome wall-clock ceiling. Imputation is heavier than LAI phasing
# (lai_runner uses 600s); a genome-wide chromosome against the full panel can run
# minutes-to-tens-of-minutes on a laptop, so default generously.
DEFAULT_TIMEOUT = 3600.0

# DR2 (dosage R²) is Beagle's imputation-quality metric in [0, 1]. The widely-used
# "well-imputed" cutoff is DR2 >= 0.8; SW-C3 will combine this with MAF to decide
# which imputed variants are reliable enough to surface. Defined here as the shared
# constant so the runtime summary and the firewall agree.
WELL_IMPUTED_DR2 = 0.8

_DR2_RE = re.compile(r"(?:^|;)DR2=([^;]+)")
_AF_RE = re.compile(r"(?:^|;)AF=([^;]+)")
_IMP_RE = re.compile(r"(?:^|;)IMP(?:;|$|=)")

# Accepted chromosome tokens (autosomes + X). The panel ships 1-22 + X; this also
# guards path construction — chrom is interpolated into panel/output paths, so a
# token with a path separator or ``..`` must never reach those paths.
_CHROM_RE = re.compile(r"^(?:[1-9]|1[0-9]|2[0-2]|X)$")


def _normalize_chrom(chrom: str) -> str:
    """Normalize + validate a chromosome token to ``1``..``22`` / ``X``.

    Raises:
        ValueError: the token is not a supported chromosome (also blocks a token
            carrying a path separator or ``..`` from reaching a file path).
    """
    c = chrom.strip()
    if c[:3].lower() == "chr":
        c = c[3:]
    c = c.upper()
    if not _CHROM_RE.fullmatch(c):
        raise ValueError(f"unsupported chromosome token: {chrom!r}")
    return c


def beagle_jar_path(lai_bundle_dir: Path) -> Path:
    """Path to the Beagle JAR vendored in the LAI bundle (reused for imputation)."""
    return Path(lai_bundle_dir) / "beagle" / "beagle.jar"


@dataclass(frozen=True)
class ImputedVariant:
    """One ALT allele of a marker in Beagle's imputed output VCF."""

    chrom: str
    pos: int
    ref: str
    alt: str
    dr2: float | None  # dosage R² imputation quality (0-1)
    af: float | None  # estimated ALT allele frequency
    imputed: bool  # True = Beagle IMP flag (ref-only marker); False = genotyped


@dataclass
class ImputationChromResult:
    """Outcome of imputing one chromosome."""

    chrom: str
    output_vcf: Path | None
    runtime_seconds: float
    return_ok: bool
    n_total: int = 0
    n_imputed: int = 0
    stderr_tail: str = ""


@dataclass
class ImputationSummary:
    """Aggregate quality summary over a set of imputed variants."""

    n_total: int = 0
    n_imputed: int = 0
    n_well_imputed: int = 0  # imputed AND dr2 >= WELL_IMPUTED_DR2
    mean_imputed_dr2: float | None = None
    chrom_runtimes: dict[str, float] = field(default_factory=dict)

    @property
    def total_runtime_seconds(self) -> float:
        return sum(self.chrom_runtimes.values())

    @property
    def frac_well_imputed(self) -> float | None:
        """Fraction of *imputed* markers that clear the DR2 cutoff (None if none)."""
        if self.n_imputed == 0:
            return None
        return self.n_well_imputed / self.n_imputed


def _open_maybe_gzip(path: Path) -> IO[str]:
    p = Path(path)
    if p.suffix == ".gz":
        return gzip.open(p, "rt", encoding="utf-8")
    return p.open("r", encoding="utf-8")


def _info_floats(info: str, regex: re.Pattern[str]) -> list[float | None]:
    """Parse a per-ALT (Number=A) comma-separated float INFO field → list."""
    m = regex.search(info)
    if not m:
        return []
    out: list[float | None] = []
    for tok in m.group(1).split(","):
        tok = tok.strip()
        try:
            val = float(tok)
        except ValueError:
            out.append(None)
            continue
        # DR2 and AF are both bounded in [0, 1]; drop nan/inf AND finite
        # out-of-range values so a malformed entry (e.g. DR2=1.2, AF=-0.1) can't
        # poison the quality summary or wrongly clear the well-imputed cutoff.
        out.append(val if math.isfinite(val) and 0.0 <= val <= 1.0 else None)
    return out


def parse_imputed_vcf(vcf_path: Path) -> Iterator[ImputedVariant]:
    """Yield :class:`ImputedVariant` per ALT from a Beagle imputed VCF (gz or plain).

    Extracts the per-ALT ``DR2`` / ``AF`` and the ``IMP`` flag (present only on
    imputed, ref-only markers). Multi-allelic markers yield one record per ALT,
    each paired with its aligned DR2/AF entry.
    """
    with _open_maybe_gzip(vcf_path) as fh:
        for line in fh:
            if not line or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 8:
                continue
            chrom, pos_raw, _id, ref, alt, _qual, _filter, info = parts[:8]
            try:
                pos = int(pos_raw)
            except ValueError:
                continue
            imputed = bool(_IMP_RE.search(info))
            dr2 = _info_floats(info, _DR2_RE)
            af = _info_floats(info, _AF_RE)
            ref_u = ref.strip().upper()
            for i, a in enumerate(alt.split(",")):
                yield ImputedVariant(
                    chrom=chrom.strip(),
                    pos=pos,
                    ref=ref_u,
                    alt=a.strip().upper(),
                    dr2=dr2[i] if i < len(dr2) else None,
                    af=af[i] if i < len(af) else None,
                    imputed=imputed,
                )


def summarize_dr2(variants: Iterable[ImputedVariant]) -> ImputationSummary:
    """Summarize imputation quality over ``variants`` (counts + mean imputed DR2)."""
    summary = ImputationSummary()
    dr2_sum = 0.0
    dr2_n = 0
    for v in variants:
        summary.n_total += 1
        if not v.imputed:
            continue
        summary.n_imputed += 1
        if v.dr2 is not None:
            dr2_sum += v.dr2
            dr2_n += 1
            if v.dr2 >= WELL_IMPUTED_DR2:
                summary.n_well_imputed += 1
    summary.mean_imputed_dr2 = (dr2_sum / dr2_n) if dr2_n else None
    return summary


class ImputationRunner:
    """Drives Beagle imputation of a sample's per-chromosome VCFs against the panel."""

    def __init__(
        self,
        panel_dir: Path,
        beagle_jar: Path,
        *,
        java_mem: str = DEFAULT_JAVA_MEM,
        nthreads: int | None = None,
    ) -> None:
        self.panel_dir = Path(panel_dir)
        self.beagle_jar = Path(beagle_jar)
        self.java_mem = java_mem
        self.nthreads = nthreads
        if not self.beagle_jar.exists():
            raise FileNotFoundError(
                f"Beagle JAR not found at {self.beagle_jar} — install the LAI bundle "
                f"(its vendored beagle.jar is reused for imputation)."
            )

    @classmethod
    def from_settings(cls, settings, **kwargs) -> ImputationRunner:  # noqa: ANN001
        """Build a runner from app settings (panel dir + LAI-bundle Beagle JAR)."""
        return cls(
            settings.imputation_panel_dir,
            beagle_jar_path(settings.resolved_lai_bundle_path),
            **kwargs,
        )

    def _build_command(self, chrom: str, input_vcf: Path, out_prefix: Path) -> list[str]:
        chrom = _normalize_chrom(chrom)
        cmd = [
            "java",
            f"-Xmx{self.java_mem}",
            "-jar",
            str(self.beagle_jar),
            f"gt={input_vcf}",
            f"ref={panel_bref3_path(self.panel_dir, chrom)}",
            f"map={panel_map_path(self.panel_dir, chrom)}",
            f"out={out_prefix}",
        ]
        if self.nthreads is not None:
            cmd.append(f"nthreads={self.nthreads}")
        return cmd

    def impute_chromosome(
        self,
        chrom: str,
        input_vcf: Path,
        out_dir: Path,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> ImputationChromResult:
        """Impute one chromosome; return the result with wall-clock runtime.

        Requires the panel's ``bref3`` + genetic map for ``chrom`` to be installed
        (SW-C1). On Beagle failure / timeout / missing output, ``return_ok`` is
        False and the parse is skipped.
        """
        chrom = _normalize_chrom(chrom)
        bref3 = panel_bref3_path(self.panel_dir, chrom)
        gen_map = panel_map_path(self.panel_dir, chrom)
        missing = [str(p) for p in (bref3, gen_map) if not p.exists()]
        if missing:
            raise FileNotFoundError(
                f"imputation panel files missing for chr{chrom}: {missing} — run "
                f"scripts/fetch_imputation_panel.py first."
            )

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_prefix = out_dir / f"imputed_chr{chrom}"
        cmd = self._build_command(chrom, Path(input_vcf), out_prefix)

        start = time.monotonic()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: S603
        except subprocess.TimeoutExpired:
            runtime = time.monotonic() - start
            logger.error("imputation_timeout", chrom=chrom, timeout=timeout)
            return ImputationChromResult(
                chrom=chrom,
                output_vcf=None,
                runtime_seconds=runtime,
                return_ok=False,
                stderr_tail="timeout",
            )
        runtime = time.monotonic() - start

        if proc.returncode != 0:
            stderr_tail = proc.stderr[-500:]
            # Beagle stderr can carry local paths / sample identifiers — log only its
            # size, and keep the tail on the returned result for the caller to use.
            logger.error(
                "imputation_failed",
                chrom=chrom,
                returncode=proc.returncode,
                stderr_chars=len(stderr_tail),
            )
            return ImputationChromResult(
                chrom=chrom,
                output_vcf=None,
                runtime_seconds=runtime,
                return_ok=False,
                stderr_tail=stderr_tail,
            )

        out_vcf = Path(f"{out_prefix}.vcf.gz")
        if not (out_vcf.exists() and out_vcf.stat().st_size > 0):
            logger.error("imputation_no_output", chrom=chrom)
            return ImputationChromResult(
                chrom=chrom,
                output_vcf=None,
                runtime_seconds=runtime,
                return_ok=False,
                stderr_tail="no output VCF produced",
            )

        n_total = 0
        n_imputed = 0
        for v in parse_imputed_vcf(out_vcf):
            n_total += 1
            if v.imputed:
                n_imputed += 1
        logger.info(
            "imputation_chrom_complete",
            chrom=chrom,
            runtime_seconds=round(runtime, 1),
            n_total=n_total,
            n_imputed=n_imputed,
        )
        return ImputationChromResult(
            chrom=chrom,
            output_vcf=out_vcf,
            runtime_seconds=runtime,
            return_ok=True,
            n_total=n_total,
            n_imputed=n_imputed,
        )
