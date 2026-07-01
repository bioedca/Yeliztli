"""Local Beagle 5.x phase+impute runtime (SW-C2 / roadmap #2).

Runs genotype imputation locally with Beagle 5.x against the 1000G Phase 3 v5a
reference panel shipped by SW-C1 (:mod:`backend.annotation.imputation_panel`),
reusing the **same self-contained Beagle JAR already vendored in the LAI bundle**
(invoked via ``subprocess``, never imported — the GPL boundary never reaches the
MIT app code, identical to :mod:`backend.analysis.lai_runner`). For each
chromosome it imputes the sample's typed genotypes up to the panel markers and
records Beagle's per-variant **DR2 (dosage R²)** imputation-quality metric. The
SW-C3 firewall also needs population/reference-panel MAF to quarantine unreliable
imputed rare variants; Beagle's output ``AF`` is target-sample AF, so this wrapper
does not map it into the firewall's population-AF slot.

**Verified Beagle output (5.5, real run 2026-06-26):** the imputed VCF carries
``DR2`` (Number=A dosage R², 0-1), target-sample ``AF`` (Number=A), and the
``IMP`` flag on markers present only in the reference (i.e. imputed, not
genotyped); FORMAT is ``GT:DS``. Imputation runs automatically when ``ref=`` has
markers absent from ``gt=``; a genetic map is supplied for accurate recombination
rates.

The per-ALT result model (:class:`ImputedVariant`), the chromosome-token guard,
and the VCF parser are the engine-agnostic ones in
:mod:`backend.analysis.imputation_vcf` (shared with the SW-C7 GLIMPSE2 / IMPUTE5
engines); :func:`parse_imputed_vcf` is the Beagle-specialised wrapper.

**Runtime measurement.** :meth:`ImputationRunner.impute_chromosome` times each
Beagle invocation (wall-clock) and ``scripts/run_imputation.py`` reports the
total — so the per-laptop runtime the Wave C plan calls for is measured on
whatever machine actually runs it.

This module runs and parses imputation; it does not itself gate findings (that is
SW-C3, :mod:`backend.analysis.imputation_firewall`).
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from backend.analysis.imputation_vcf import ImputedVariant, parse_engine_vcf
from backend.analysis.imputation_vcf import normalize_chrom as _normalize_chrom
from backend.annotation.imputation_panel import panel_bref3_path, panel_map_path

__all__ = [
    "DEFAULT_JAVA_MEM",
    "DEFAULT_TIMEOUT",
    "WELL_IMPUTED_DR2",
    "ImputationChromResult",
    "ImputationRunner",
    "ImputationSummary",
    "ImputedVariant",
    "beagle_jar_path",
    "parse_imputed_vcf",
    "summarize_dr2",
]

logger = structlog.get_logger(__name__)

DEFAULT_JAVA_MEM = "8g"
# Per-chromosome wall-clock ceiling. Imputation is heavier than LAI phasing
# (lai_runner uses 600s); a genome-wide chromosome against the full panel can run
# minutes-to-tens-of-minutes on a laptop, so default generously.
DEFAULT_TIMEOUT = 3600.0
_OUTPUT_LABEL_RE = re.compile(r"^(?:[1-9]|1[0-9]|2[0-2]|X(?:_(?:PAR[12]|NONPAR[123]))?)$")

# DR2 (dosage R²) is Beagle's imputation-quality metric in [0, 1]. DR2 >= 0.8 is a
# deliberately conservative "well-imputed" cutoff (the looser GWAS convention is
# r²/info ≈ 0.3–0.5; rigorous practice uses MAF-dependent thresholds — Naj 2019,
# DOI:10.1002/cphg.84). The SW-C3 firewall (backend.analysis.imputation_firewall)
# combines it with a MAF floor: Beagle's estimated DR2 over-states quality at low
# MAF (winner's curse), so DR2 >= 0.8 is necessary-but-not-sufficient for rare
# variants. Shared constant so the runtime summary, the firewall, and the SW-C7
# advanced engines all reuse the same conservative cutoff *value* — note the
# GLIMPSE2/IMPUTE5 IMPUTE info score fills the same QC role but is a distinct
# (information-theoretic) metric, not a literal DR2 (see imputation_vcf docstring).
WELL_IMPUTED_DR2 = 0.8


def beagle_jar_path(lai_bundle_dir: Path) -> Path:
    """Path to the Beagle JAR vendored in the LAI bundle (reused for imputation)."""
    return Path(lai_bundle_dir) / "beagle" / "beagle.jar"


def parse_imputed_vcf(vcf_path: Path) -> Iterator[ImputedVariant]:
    """Yield :class:`ImputedVariant` per ALT from a Beagle imputed VCF (gz or plain).

    Beagle-specialised wrapper over
    :func:`backend.analysis.imputation_vcf.parse_engine_vcf`: quality ``DR2``,
    no population/reference-panel AF key (Beagle ``AF`` is target-sample AF), and
    imputed markers flagged by ``IMP``.
    """
    return parse_engine_vcf(vcf_path, quality_key="DR2", af_key=None, imputed_flag_key="IMP")


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

    def _build_command(
        self,
        chrom: str,
        input_vcf: Path,
        out_prefix: Path,
        *,
        region: str | None = None,
    ) -> list[str]:
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
        if region is not None:
            cmd.append(f"chrom={region}")
        if self.nthreads is not None:
            cmd.append(f"nthreads={self.nthreads}")
        return cmd

    def impute_chromosome(
        self,
        chrom: str,
        input_vcf: Path,
        out_dir: Path,
        *,
        region: str | None = None,
        output_label: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> ImputationChromResult:
        """Impute one chromosome; return the result with wall-clock runtime.

        Requires the panel's ``bref3`` + genetic map for ``chrom`` to be installed
        (SW-C1). ``region`` is passed as Beagle's ``chrom=`` interval for split
        X PAR/non-PAR runs. On Beagle failure / timeout / missing output,
        ``return_ok`` is False and the parse is skipped.
        """
        chrom = _normalize_chrom(chrom)
        label = output_label or chrom
        if not _OUTPUT_LABEL_RE.fullmatch(label):
            raise ValueError(f"unsupported output label: {label!r}")
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
        out_prefix = out_dir / f"imputed_chr{label}"
        cmd = self._build_command(chrom, Path(input_vcf), out_prefix, region=region)

        start = time.monotonic()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: S603
        except subprocess.TimeoutExpired:
            runtime = time.monotonic() - start
            logger.error("imputation_timeout", chrom=label, timeout=timeout)
            return ImputationChromResult(
                chrom=label,
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
                chrom=label,
                returncode=proc.returncode,
                stderr_chars=len(stderr_tail),
            )
            return ImputationChromResult(
                chrom=label,
                output_vcf=None,
                runtime_seconds=runtime,
                return_ok=False,
                stderr_tail=stderr_tail,
            )

        out_vcf = Path(f"{out_prefix}.vcf.gz")
        if not (out_vcf.exists() and out_vcf.stat().st_size > 0):
            logger.error("imputation_no_output", chrom=label)
            return ImputationChromResult(
                chrom=label,
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
            chrom=label,
            runtime_seconds=round(runtime, 1),
            n_total=n_total,
            n_imputed=n_imputed,
        )
        return ImputationChromResult(
            chrom=label,
            output_vcf=out_vcf,
            runtime_seconds=runtime,
            return_ok=True,
            n_total=n_total,
            n_imputed=n_imputed,
        )
