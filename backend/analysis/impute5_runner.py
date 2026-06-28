"""IMPUTE5 array/sequencing imputation engine (SW-C7 / roadmap #53).

Wave C advanced engine. IMPUTE5 fills the **same role as Beagle** (SW-C2,
:mod:`backend.analysis.imputation_runner`) — imputing a sample's genotypes up to a
reference panel — but via the Positional Burrows-Wheeler Transform, and with one
hard prerequisite: **the target genotypes must already be phased** (e.g. with
SHAPEIT5). Beagle phases *and* imputes in a single call; IMPUTE5 imputes
pre-phased input only, so an upstream phasing step is the operator's
responsibility. Output markers flow through the **same SW-C3 firewall / persist /
findings pipeline** as Beagle/GLIMPSE2 output via the engine-agnostic
:func:`backend.analysis.imputation_vcf.parse_engine_vcf`.

IMPUTE5 is **academic-use-only, binary-only (Dropbox-gated), with no third-party
redistribution grant** (*[ext-strategy]* §IMPUTE5, verified 2026-06-17) → it is
**BYO / user-fetched and never bundled**. Like the other engines it is invoked
**only via ``subprocess``, never imported**, and resolved from
``settings.impute5_bin_dir`` or ``PATH``; when absent the engine is simply
**unavailable** (:func:`impute5_available`), never fatal.

**Output / quality metric:** the per-variant ``INFO`` INFO key is the IMPUTE-family
info score (information-theoretic, ~[0, 1]; the same family as GLIMPSE2's, *not* a
Beagle-style dosage r² — see :mod:`backend.analysis.imputation_vcf`); ``AF`` is the
ALT allele frequency; FORMAT is ``GT:DS`` (``GP`` added with ``--out-gp-field``).
Provenance: IMPUTE info origin PMID:22384356; IMPUTE5
DOI:10.1371/journal.pgen.1009049 (PMC7704051). (accessed 2026-06-28) *Whether IMPUTE5 flags
typed-vs-imputed markers in its native output is unconfirmed without the binary, so
every output marker is conservatively treated as imputed (the firewall only ever
*withholds*, never fabricates); real-run output-shape validation — including the
exact ``INFO``/``AF`` header and any typed flag — is deferred to a cluster run once
a build is provisioned, mirroring SW-C2.*

**Workflow:** the reference panel is a pre-built ``.imp5`` (from ``imp5Converter``)
or an indexed VCF/BCF, supplied by the operator; this seam runs the single
``impute5`` command per region (``--h`` ref, ``--g`` phased target, ``--m`` map,
``--r`` region, ``--o`` output).
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import structlog

from backend.analysis.imputation_engine import missing_binaries as _missing_binaries
from backend.analysis.imputation_engine import resolve_binary
from backend.analysis.imputation_vcf import (
    ImputedVariant,
    normalize_chrom,
    parse_engine_vcf,
)

logger = structlog.get_logger(__name__)

# Per-region wall-clock ceiling; default generously (same order as Beagle).
DEFAULT_TIMEOUT = 3600.0

# imp5Converter (building the .imp5 panel) is a one-time operator step, so the only
# binary this seam *requires* is impute5 itself (it can read an indexed VCF/BCF ref
# directly). Operators with versioned binaries (impute5_v1.2.0_static) should
# symlink them to this canonical name or point settings.impute5_bin_dir at them.
IMPUTE5_BIN = "impute5"
REQUIRED_BINARIES: tuple[str, ...] = (IMPUTE5_BIN,)

# IMPUTE5 INFO keys mapped into ImputedVariant: quality = the IMPUTE info score;
# frequency = AF.
_QUALITY_KEY = "INFO"
_AF_KEY = "AF"

# A genomic region token (``contig`` or ``contig:start-end``). IMPUTE5 ``--r`` /
# ``--buffer-region`` take such a string; this guards an operator-supplied value to
# a sane shape (defence-in-depth — it is passed as an argv element, never a shell
# string or a path).
_REGION_RE = re.compile(r"^[0-9A-Za-z._]+(?::\d+(?:-\d+)?)?$")


def missing_binaries(bin_dir: Path | None = None) -> list[str]:
    """Names of the required IMPUTE5 binaries that cannot be resolved."""
    return _missing_binaries(REQUIRED_BINARIES, bin_dir)


def impute5_available(bin_dir: Path | None = None) -> bool:
    """True iff the IMPUTE5 binary resolves (never raises)."""
    return not missing_binaries(bin_dir)


def parse_impute5_vcf(vcf_path: Path) -> Iterator[ImputedVariant]:
    """Yield :class:`ImputedVariant` per ALT from an IMPUTE5 output VCF.

    IMPUTE5-specialised wrapper over
    :func:`backend.analysis.imputation_vcf.parse_engine_vcf`: quality ``INFO``
    (IMPUTE info score → ``dr2`` slot), frequency ``AF``, and every marker treated
    as imputed (typed-vs-imputed flagging is unconfirmed → conservative).
    """
    return parse_engine_vcf(
        vcf_path, quality_key=_QUALITY_KEY, af_key=_AF_KEY, imputed_flag_key=None
    )


def _validate_region(region: str, what: str) -> str:
    """Validate an IMPUTE5 region token; return it stripped.

    Checks both the ``contig`` / ``contig:start[-end]`` shape and the coordinate
    bounds (``start >= 1`` and, when an end is given, ``end >= start``) so a
    zero-length or reversed interval fails fast here rather than at the engine.

    Raises:
        ValueError: the token is malformed or the interval is invalid.
    """
    token = region.strip()
    if not _REGION_RE.fullmatch(token):
        raise ValueError(f"invalid {what} region token: {region!r}")
    if ":" in token:
        coords = token.split(":", 1)[1]
        start_s, _, end_s = coords.partition("-")
        start = int(start_s)
        if start < 1:
            raise ValueError(f"invalid {what} region interval (start < 1): {region!r}")
        if end_s and int(end_s) < start:
            raise ValueError(f"invalid {what} region interval (end < start): {region!r}")
    return token


def _region_contig(region: str) -> str:
    """Normalized contig of a region token (``22:1-100`` → ``22``)."""
    return normalize_chrom(region.split(":", 1)[0])


@dataclass
class Impute5RegionResult:
    """Outcome of imputing one region with IMPUTE5."""

    chrom: str
    region: str
    output_vcf: Path | None
    runtime_seconds: float
    return_ok: bool
    n_total: int = 0
    n_imputed: int = 0
    stderr_tail: str = ""


class Impute5Runner:
    """Drives a single ``impute5`` invocation per region against a reference panel."""

    def __init__(self, *, bin_dir: Path | None = None, nthreads: int | None = None) -> None:
        self.bin_dir = Path(bin_dir) if bin_dir is not None else None
        self.nthreads = nthreads
        missing = missing_binaries(self.bin_dir)
        if missing:
            where = f" in {self.bin_dir}" if self.bin_dir is not None else " on PATH"
            raise FileNotFoundError(
                f"IMPUTE5 binary not found{where}: {missing} — IMPUTE5 is academic-use-"
                f"only / BYO; fetch it and put it on PATH or set settings.impute5_bin_dir."
            )
        self._impute5: Path = resolve_binary(IMPUTE5_BIN, self.bin_dir)  # type: ignore[assignment]

    @classmethod
    def from_settings(cls, settings, **kwargs) -> Impute5Runner:  # noqa: ANN001
        """Build a runner from app settings (``impute5_bin_dir`` → PATH fallback)."""
        return cls(bin_dir=settings.impute5_bin_dir, **kwargs)

    def _run(self, cmd: list[str], timeout: float) -> tuple[bool, str, float]:
        """Run the impute5 subprocess; return (ok, stderr_tail, runtime_seconds)."""
        start = time.monotonic()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: S603
        except subprocess.TimeoutExpired:
            runtime = time.monotonic() - start
            logger.error("impute5_timeout", timeout=timeout)
            return False, "timeout", runtime
        except OSError as exc:
            # Launch failures (non-executable, exec-format, vanished, permission).
            runtime = time.monotonic() - start
            logger.error("impute5_exec_failed", error_type=type(exc).__name__)
            return False, type(exc).__name__, runtime
        runtime = time.monotonic() - start
        if proc.returncode != 0:
            stderr_tail = proc.stderr[-500:]
            # stderr can carry local paths / sample IDs — log only its size.
            logger.error(
                "impute5_failed",
                returncode=proc.returncode,
                stderr_chars=len(stderr_tail),
            )
            return False, stderr_tail, runtime
        return True, "", runtime

    def impute_region(
        self,
        chrom: str,
        target: Path,
        reference: Path,
        gmap: Path,
        out_dir: Path,
        *,
        region: str | None = None,
        buffer_region: str | None = None,
        buffer_kb: int | None = None,
        out_gp_field: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> Impute5RegionResult:
        """Impute one region with IMPUTE5.

        Args:
            chrom: chromosome token (``1``..``22`` / ``X``); used in the output
                path (validated against path-traversal) and as the default region.
            target: the sample's **phased** genotypes (VCF/BCF) — IMPUTE5 does not
                phase; an upstream phasing step is required.
            reference: a ``.imp5`` panel (from ``imp5Converter``) or an indexed
                phased VCF/BCF.
            gmap: recombination genetic map for ``chrom``.
            out_dir: output directory (created if absent).
            region: imputation region (``contig`` or ``contig:start-end``);
                defaults to the whole chromosome (``chrom``).
            buffer_region / buffer_kb: optional flanking buffer (``--buffer-region``
                takes precedence over ``--b`` kb when both are given).
            out_gp_field: emit FORMAT ``GP`` (``--out-gp-field``).
            timeout: wall-clock ceiling (seconds).

        Returns an :class:`Impute5RegionResult`; an engine/IO runtime failure (a
        non-zero exit, timeout, launch error, or an unparseable output VCF) sets
        ``return_ok`` False rather than raising. Raises only for a caller/setup
        error: an invalid ``chrom``/region token (``ValueError``) or a missing
        required input file (``FileNotFoundError``).
        """
        chrom = normalize_chrom(chrom)
        region_tok = _validate_region(region, "imputation") if region else chrom
        buf_tok = _validate_region(buffer_region, "buffer") if buffer_region else None
        # The output is named/labelled by chrom, so the region (and buffer) must be
        # on that same contig — otherwise the result would be mislabelled.
        if region is not None and _region_contig(region_tok) != chrom:
            raise ValueError(f"imputation region {region_tok!r} is not on chr{chrom}")
        if buf_tok is not None and _region_contig(buf_tok) != chrom:
            raise ValueError(f"buffer region {buf_tok!r} is not on chr{chrom}")
        target = Path(target)
        reference = Path(reference)
        gmap = Path(gmap)
        missing = [str(p) for p in (target, reference, gmap) if not p.exists()]
        if missing:
            raise FileNotFoundError(f"IMPUTE5 inputs missing for chr{chrom}: {missing}")

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        # Key the output filename by the region too, so several region-level runs of
        # the same chromosome into one out_dir don't overwrite each other.
        if region is None:
            out_vcf = out_dir / f"imputed_chr{chrom}.vcf.gz"
        else:
            region_suffix = re.sub(r"[^0-9A-Za-z._-]+", "_", region_tok)
            out_vcf = out_dir / f"imputed_chr{chrom}_{region_suffix}.vcf.gz"
        cmd = [
            str(self._impute5),
            "--h",
            str(reference),
            "--g",
            str(target),
            "--m",
            str(gmap),
            "--r",
            region_tok,
            "--o",
            str(out_vcf),
        ]
        if buf_tok is not None:
            cmd += ["--buffer-region", buf_tok]
        elif buffer_kb is not None:
            cmd += ["--b", str(buffer_kb)]
        if out_gp_field:
            cmd.append("--out-gp-field")
        if self.nthreads is not None:
            cmd += ["--threads", str(self.nthreads)]

        ok, stderr_tail, runtime = self._run(cmd, timeout)
        if not ok or not (out_vcf.exists() and out_vcf.stat().st_size > 0):
            return Impute5RegionResult(
                chrom=chrom,
                region=region_tok,
                output_vcf=None,
                runtime_seconds=runtime,
                return_ok=False,
                stderr_tail=stderr_tail or "no output VCF produced",
            )

        try:
            n_total = sum(1 for _ in parse_impute5_vcf(out_vcf))
        except (OSError, ValueError) as exc:
            logger.error("impute5_parse_failed", chrom=chrom, error_type=type(exc).__name__)
            return Impute5RegionResult(
                chrom=chrom,
                region=region_tok,
                output_vcf=None,
                runtime_seconds=runtime,
                return_ok=False,
                stderr_tail=f"failed to parse output VCF: {exc}",
            )
        logger.info(
            "impute5_region_complete",
            chrom=chrom,
            region=region_tok,
            runtime_seconds=round(runtime, 1),
            n_total=n_total,
        )
        return Impute5RegionResult(
            chrom=chrom,
            region=region_tok,
            output_vcf=out_vcf,
            runtime_seconds=runtime,
            return_ok=True,
            n_total=n_total,
            n_imputed=n_total,  # typed/imputed flagging unconfirmed → conservative
        )
