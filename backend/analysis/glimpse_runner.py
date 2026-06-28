"""GLIMPSE2 low-coverage-sequencing imputation engine (SW-C7 / roadmap #53).

Wave C advanced engine. Where Beagle (SW-C2,
:mod:`backend.analysis.imputation_runner`) imputes a **SNP-array** sample's hard
genotypes against the panel, **GLIMPSE2 solves a different problem**: it imputes
and phases **low-coverage whole-genome sequencing** data from genotype
*likelihoods* (a BAM/CRAM, or a VCF/BCF carrying ``FORMAT/GL`` or ``FORMAT/PL``).
It is therefore **not** part of the SNP-array imputation path — it is the engine
for a future low-coverage-WGS input modality. Every marker GLIMPSE2 emits is an
imputed posterior (low-coverage reads give no directly-typed hard calls), so all
parsed variants are ``imputed=True`` and pass through the **same SW-C3 firewall /
persist / findings pipeline** as Beagle output, via the engine-agnostic
:func:`backend.analysis.imputation_vcf.parse_engine_vcf`.

GLIMPSE2 is **MIT-licensed** (Rubinacci & Delaneau; *[ext-strategy]* §GLIMPSE) →
freely redistributable, but it is invoked here **only via ``subprocess``, never
imported** (identical isolation to the Beagle/LAI seam). The binaries are resolved
from ``settings.glimpse_bin_dir`` or ``PATH``; when absent the engine is simply
**unavailable** (:func:`glimpse_available`) — never fatal. It is operator-installed
for now (a candidate to co-vendor later, once a build is pinned).

**Output / quality metric** (verified from GLIMPSE2 source
``phase/src/io/genotype_writer.cpp``): the per-variant ``INFO`` INFO key is the
**IMPUTE-family info score** (information-theoretic, ~[0, 1]; *not* a Beagle-style
dosage r² — see :mod:`backend.analysis.imputation_vcf`); ``RAF`` is the
reference-panel ALT frequency (the population frequency the firewall's rarity gate
needs — GLIMPSE's per-sample ``AF`` is degenerate for a single sample); FORMAT is
``GT:DS:GP``. Provenance: IMPUTE2 info origin PMID:22384356; GLIMPSE2
PMID:37386250 / DOI:10.1038/s41588-023-01438-3. (accessed 2026-06-28)

**Workflow** (3 binaries): ``GLIMPSE2_chunk`` splits a chromosome into overlapping
imputation chunks; ``GLIMPSE2_phase`` imputes each chunk against the reference
(a phased VCF/BCF, or a GLIMPSE binary ``.bin`` — *not* the Beagle ``bref3``);
``GLIMPSE2_ligate`` concatenates the chunks into one per-chromosome VCF. The
optional ``GLIMPSE2_split_reference`` speed optimisation is left to the operator
(``--reference`` reads a plain VCF/BCF directly). **Real-run output-shape
validation is deferred to a cluster run once a GLIMPSE2 build is provisioned**
(this seam is unit-tested with the subprocess mocked, mirroring the SW-C2
approach).
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import structlog

from backend.analysis.imputation_engine import (
    missing_binaries as _missing_binaries,
)
from backend.analysis.imputation_engine import resolve_binary
from backend.analysis.imputation_vcf import (
    ImputedVariant,
    normalize_chrom,
    parse_engine_vcf,
)

__all__ = [
    "DEFAULT_TIMEOUT",
    "GLIMPSE_CHUNK",
    "GLIMPSE_LIGATE",
    "GLIMPSE_PHASE",
    "REQUIRED_BINARIES",
    "GlimpseChromResult",
    "GlimpseChunk",
    "GlimpseRunner",
    "glimpse_available",
    "missing_binaries",
    "parse_chunk_file",
    "parse_glimpse_vcf",
    "resolve_binary",
]

logger = structlog.get_logger(__name__)

# Per-step wall-clock ceiling (one chunk's phase, or chunk/ligate). Low-coverage
# WGS imputation is heavy; default generously (same order as the Beagle runtime).
DEFAULT_TIMEOUT = 3600.0

# The three GLIMPSE2 binaries this seam drives, in workflow order. Resolved by
# canonical name from settings.glimpse_bin_dir or PATH; operators with versioned
# binaries (e.g. GLIMPSE2_phase_static) should symlink them to these names.
GLIMPSE_CHUNK = "GLIMPSE2_chunk"
GLIMPSE_PHASE = "GLIMPSE2_phase"
GLIMPSE_LIGATE = "GLIMPSE2_ligate"
REQUIRED_BINARIES: tuple[str, ...] = (GLIMPSE_CHUNK, GLIMPSE_PHASE, GLIMPSE_LIGATE)

# GLIMPSE2 INFO keys mapped into ImputedVariant: quality = the IMPUTE info score;
# frequency = the reference-panel AF (RAF), NOT the single-sample AF.
_QUALITY_KEY = "INFO"
_AF_KEY = "RAF"


def missing_binaries(bin_dir: Path | None = None) -> list[str]:
    """Names of the required GLIMPSE2 binaries that cannot be resolved."""
    return _missing_binaries(REQUIRED_BINARIES, bin_dir)


def glimpse_available(bin_dir: Path | None = None) -> bool:
    """True iff every required GLIMPSE2 binary resolves (never raises)."""
    return not missing_binaries(bin_dir)


@dataclass(frozen=True)
class GlimpseChunk:
    """One imputation chunk from GLIMPSE2_chunk: input (buffered) + output region."""

    input_region: str  # IRG — region with buffer (phase --input-region)
    output_region: str  # ORG — region without buffer (phase --output-region)


@dataclass
class GlimpseChromResult:
    """Outcome of imputing one chromosome with GLIMPSE2."""

    chrom: str
    output_vcf: Path | None
    runtime_seconds: float
    return_ok: bool
    n_total: int = 0
    n_imputed: int = 0
    n_chunks: int = 0
    stderr_tail: str = ""


def parse_glimpse_vcf(vcf_path: Path) -> Iterator[ImputedVariant]:
    """Yield :class:`ImputedVariant` per ALT from a ligated GLIMPSE2 VCF.

    GLIMPSE-specialised wrapper over
    :func:`backend.analysis.imputation_vcf.parse_engine_vcf`: quality ``INFO``
    (IMPUTE info score → ``dr2`` slot), frequency ``RAF`` (panel AF), and every
    marker treated as imputed (no per-marker imputed flag).
    """
    return parse_engine_vcf(
        vcf_path, quality_key=_QUALITY_KEY, af_key=_AF_KEY, imputed_flag_key=None
    )


def parse_chunk_file(chunks_file: Path) -> list[GlimpseChunk]:
    """Parse a ``GLIMPSE2_chunk`` output file into ordered chunks.

    The chunk file is tab-separated with (at least) ``index, contig,
    input_region, output_region`` columns; only the input/output regions are
    needed to drive ``GLIMPSE2_phase``. Blank and ``#``-comment lines are skipped.

    Raises:
        ValueError: a non-comment line has fewer than 4 columns.
    """
    chunks: list[GlimpseChunk] = []
    with Path(chunks_file).open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 4:
                raise ValueError(f"malformed GLIMPSE2 chunk line (need >=4 columns): {line!r}")
            chunks.append(
                GlimpseChunk(input_region=cols[2].strip(), output_region=cols[3].strip())
            )
    return chunks


class GlimpseRunner:
    """Drives the GLIMPSE2 chunk → phase → ligate workflow for one chromosome."""

    def __init__(self, *, bin_dir: Path | None = None, nthreads: int | None = None) -> None:
        self.bin_dir = Path(bin_dir) if bin_dir is not None else None
        self.nthreads = nthreads
        missing = missing_binaries(self.bin_dir)
        if missing:
            where = f" in {self.bin_dir}" if self.bin_dir is not None else " on PATH"
            raise FileNotFoundError(
                f"GLIMPSE2 binaries not found{where}: {missing} — install GLIMPSE2 "
                f"(MIT) and put them on PATH or set settings.glimpse_bin_dir."
            )
        # Resolved once; safe because __init__ already verified all are present.
        self._bin: dict[str, Path] = {
            n: resolve_binary(n, self.bin_dir)
            for n in REQUIRED_BINARIES  # type: ignore[misc]
        }

    @classmethod
    def from_settings(cls, settings, **kwargs) -> GlimpseRunner:  # noqa: ANN001
        """Build a runner from app settings (``glimpse_bin_dir`` → PATH fallback)."""
        return cls(bin_dir=settings.glimpse_bin_dir, **kwargs)

    def _run(self, cmd: list[str], timeout: float) -> tuple[bool, str, float]:
        """Run one subprocess; return (ok, stderr_tail, runtime_seconds)."""
        start = time.monotonic()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: S603
        except subprocess.TimeoutExpired:
            runtime = time.monotonic() - start
            logger.error("glimpse_timeout", binary=cmd[0], timeout=timeout)
            return False, "timeout", runtime
        except OSError as exc:
            # Launch failures (non-executable, exec-format, vanished binary,
            # permission denied) must become a structured step failure, not an
            # uncaught exception.
            runtime = time.monotonic() - start
            logger.error(
                "glimpse_exec_failed",
                binary=Path(cmd[0]).name,
                error_type=type(exc).__name__,
            )
            return False, type(exc).__name__, runtime
        runtime = time.monotonic() - start
        if proc.returncode != 0:
            stderr_tail = proc.stderr[-500:]
            # stderr can carry local paths / sample IDs — log only its size.
            logger.error(
                "glimpse_step_failed",
                binary=Path(cmd[0]).name,
                returncode=proc.returncode,
                stderr_chars=len(stderr_tail),
            )
            return False, stderr_tail, runtime
        return True, "", runtime

    def _threads_args(self) -> list[str]:
        return ["--threads", str(self.nthreads)] if self.nthreads is not None else []

    def impute_chromosome(
        self,
        chrom: str,
        input_gl: Path,
        reference: Path,
        gmap: Path,
        out_dir: Path,
        *,
        ref_sites: Path | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> GlimpseChromResult:
        """Impute one chromosome with GLIMPSE2 (chunk → phase per chunk → ligate).

        Args:
            chrom: chromosome token (``1``..``22`` / ``X``); used as the GLIMPSE2
                ``--region`` and validated against path-traversal.
            input_gl: the sample's genotype-likelihood VCF/BCF (``FORMAT/GL`` or
                ``FORMAT/PL``) for this chromosome — *not* array hard calls.
            reference: a **phased** reference panel as VCF/BCF (or a GLIMPSE
                ``.bin``) — *not* the Beagle ``bref3``.
            gmap: recombination genetic map for ``chrom``.
            out_dir: output directory (created if absent).
            ref_sites: optional sites-only VCF/BCF for chunking (defaults to
                ``reference``).
            timeout: per-step wall-clock ceiling (seconds).

        Returns a :class:`GlimpseChromResult`; engine/IO runtime failures (a step
        non-zero exit, timeout, launch error, missing chunks, or an unparseable
        ligated VCF) set ``return_ok`` False rather than raising. Raises only for a
        caller/setup error: an invalid ``chrom`` token (``ValueError``) or a
        missing required input file (``FileNotFoundError``, mirroring
        :meth:`backend.analysis.imputation_runner.ImputationRunner.impute_chromosome`).
        """
        chrom = normalize_chrom(chrom)
        input_gl = Path(input_gl)
        reference = Path(reference)
        gmap = Path(gmap)
        sites = Path(ref_sites) if ref_sites is not None else reference
        missing = [str(p) for p in (input_gl, reference, gmap, sites) if not p.exists()]
        if missing:
            raise FileNotFoundError(f"GLIMPSE2 inputs missing for chr{chrom}: {missing}")

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        total_runtime = 0.0

        # 1) Chunk the chromosome into overlapping imputation windows.
        chunks_file = out_dir / f"chunks_chr{chrom}.txt"
        chunk_cmd = [
            str(self._bin[GLIMPSE_CHUNK]),
            "--input",
            str(sites),
            "--region",
            chrom,
            "--map",
            str(gmap),
            "--output",
            str(chunks_file),
            *self._threads_args(),
        ]
        ok, stderr_tail, runtime = self._run(chunk_cmd, timeout)
        total_runtime += runtime
        if not ok or not (chunks_file.exists() and chunks_file.stat().st_size > 0):
            return GlimpseChromResult(
                chrom=chrom,
                output_vcf=None,
                runtime_seconds=total_runtime,
                return_ok=False,
                stderr_tail=stderr_tail or "no chunk file produced",
            )
        try:
            chunks = parse_chunk_file(chunks_file)
        except ValueError as exc:
            return GlimpseChromResult(
                chrom=chrom,
                output_vcf=None,
                runtime_seconds=total_runtime,
                return_ok=False,
                stderr_tail=str(exc),
            )
        if not chunks:
            return GlimpseChromResult(
                chrom=chrom,
                output_vcf=None,
                runtime_seconds=total_runtime,
                return_ok=False,
                stderr_tail="chunk file had no chunks",
            )

        # 2) Phase+impute each chunk against the reference.
        chunk_outputs: list[Path] = []
        for i, chunk in enumerate(chunks):
            chunk_out = out_dir / f"imputed_chr{chrom}_chunk{i}.bcf"
            phase_cmd = [
                str(self._bin[GLIMPSE_PHASE]),
                "--input-gl",
                str(input_gl),
                "--reference",
                str(reference),
                "--map",
                str(gmap),
                "--input-region",
                chunk.input_region,
                "--output-region",
                chunk.output_region,
                "--output",
                str(chunk_out),
                *self._threads_args(),
            ]
            ok, stderr_tail, runtime = self._run(phase_cmd, timeout)
            total_runtime += runtime
            if not ok or not chunk_out.exists():
                return GlimpseChromResult(
                    chrom=chrom,
                    output_vcf=None,
                    runtime_seconds=total_runtime,
                    return_ok=False,
                    n_chunks=len(chunks),
                    stderr_tail=stderr_tail or f"chunk {i} produced no output",
                )
            chunk_outputs.append(chunk_out)

        # 3) Ligate the per-chunk outputs into one per-chromosome VCF.
        list_file = out_dir / f"ligate_chr{chrom}.txt"
        list_file.write_text("".join(f"{p}\n" for p in chunk_outputs), encoding="utf-8")
        out_vcf = out_dir / f"imputed_chr{chrom}.vcf.gz"
        ligate_cmd = [
            str(self._bin[GLIMPSE_LIGATE]),
            "--input",
            str(list_file),
            "--output",
            str(out_vcf),
            *self._threads_args(),
        ]
        ok, stderr_tail, runtime = self._run(ligate_cmd, timeout)
        total_runtime += runtime
        if not ok or not (out_vcf.exists() and out_vcf.stat().st_size > 0):
            return GlimpseChromResult(
                chrom=chrom,
                output_vcf=None,
                runtime_seconds=total_runtime,
                return_ok=False,
                n_chunks=len(chunks),
                stderr_tail=stderr_tail or "no ligated VCF produced",
            )

        try:
            n_total = sum(1 for _ in parse_glimpse_vcf(out_vcf))
        except (OSError, ValueError) as exc:
            # A non-zero exit isn't the only failure mode: GLIMPSE2 can exit 0 yet
            # leave a truncated/unparseable ligated VCF. Treat that as a failed run.
            logger.error("glimpse_parse_failed", chrom=chrom, error_type=type(exc).__name__)
            return GlimpseChromResult(
                chrom=chrom,
                output_vcf=None,
                runtime_seconds=total_runtime,
                return_ok=False,
                n_chunks=len(chunks),
                stderr_tail=f"failed to parse ligated VCF: {exc}",
            )
        logger.info(
            "glimpse_chrom_complete",
            chrom=chrom,
            runtime_seconds=round(total_runtime, 1),
            n_chunks=len(chunks),
            n_total=n_total,
        )
        return GlimpseChromResult(
            chrom=chrom,
            output_vcf=out_vcf,
            runtime_seconds=total_runtime,
            return_ok=True,
            n_total=n_total,
            n_imputed=n_total,  # every GLIMPSE2 marker is an imputed posterior
            n_chunks=len(chunks),
        )
