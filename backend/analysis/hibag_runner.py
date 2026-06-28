"""HIBAG HLA-imputation engine seam (Wave D / SW-D1, roadmap #17).

Stands up the HIBAG HLA classifier as an **operator-installed R/Bioconductor
runtime** driven through a GPL-isolated ``Rscript`` subprocess: the R code lives in
a standalone script (``r/hibag_predict.R``) invoked **by path**, never imported, so
the GPL-3 HIBAG package never links into the MIT app — the same isolation the
Beagle / GLIMPSE2 / IMPUTE5 seams use (*[ext-strategy]* §HIBAG, verified
2026-06-17). Given a sample's PLINK genotypes and a **BYO, user-fetched,
ancestry-specific pre-fit model** (never bundled — per-publication, often
non-commercial terms), it predicts 2-field/4-digit HLA alleles at the classical
loci with HIBAG's posterior call probability.

**Availability is never fatal.** When ``Rscript`` (or the model, or the HIBAG
package at runtime) is absent the engine reports itself **unavailable**
(:func:`hibag_runtime_status` / :func:`hibag_available`) and callers fall back to
the existing single-tag HLA proxy (in ``backend/analysis/allergy.py`` /
``sleep.py``); nothing crashes. This module supersedes the proxy *when the runtime
is provisioned* and keeps it as the fallback otherwise. It runs and parses the
classifier; wiring HLA calls into clinical reports is Wave D SW-D2–D5 (and the
sample → PLINK input prep is a sibling glue slice, mirroring the SW-C1/2 imputation
input prep).

**Accuracy / calibration (Zheng et al. 2014, Pharmacogenomics J, PMID:23712092,
DOI:10.1038/tpj.2013.18):** accuracy is **ancestry-dependent and lowest for
African ancestry** (per-locus accuracy roughly EUR 92–99% / Asian 88–97% /
Hispanic 75–96% / African 77–92%; HLA-B is the weakest locus). The recommended
posterior-probability call gate is **``prob >= 0.5``** (verbatim in Zheng 2014);
calls below threshold are flagged ``low_confidence`` rather than dropped, and a
**stricter threshold is advisable for African/admixed** samples. ``matching`` is a
QC signal (SNP-profile match to the training haplotypes), **not** a confidence —
do not treat it as one. Pre-fit models are already 2-field only, so there is no
"cap to 2-field"; honesty about confidence (and ancestry-appropriate model choice)
is the mitigation. (accessed 2026-06-28)

Real HLA-call validation is deferred to a run with R + the Bioconductor HIBAG
package + a BYO model provisioned (mirrors the SW-C2 deferred-runtime note); this
seam is unit-tested with the ``Rscript`` subprocess mocked.
"""

from __future__ import annotations

import csv
import math
import os
import re
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from backend.analysis.imputation_engine import resolve_binary

logger = structlog.get_logger(__name__)

# HLA prediction can take a while per locus; default generously.
DEFAULT_TIMEOUT = 1800.0
RSCRIPT_BIN = "Rscript"

# Classical loci HIBAG pre-fit models cover, at 2-field/4-digit resolution
# (Zheng 2014, PMID:23712092). A model may not contain every locus; the R script
# selects whichever the model provides and errors on a locus it lacks.
HLA_LOCI: tuple[str, ...] = ("A", "B", "C", "DRB1", "DQA1", "DQB1", "DPB1")

# Recommended posterior-probability call gate (Zheng 2014, verbatim "0.5"). Calls
# below it are flagged low-confidence, not dropped; use a stricter value for
# African/admixed samples given the lower baseline accuracy.
RECOMMENDED_PROB_THRESHOLD = 0.5

# Ancestry-specific pre-fit model basenames in the HIBAG model repository
# (HLARES index). BYO / user-supplied — never bundled.
KNOWN_ANCESTRIES: tuple[str, ...] = ("European", "Asian", "Hispanic", "African")

# The GPL-isolated R script, invoked by path (never imported).
_R_SCRIPT = Path(__file__).resolve().parent / "r" / "hibag_predict.R"

# Locus tokens are joined into a single ``--loci`` argv element; guard them to
# alphanumerics (defence-in-depth — argv, never a shell string or a path).
_LOCUS_RE = re.compile(r"^[A-Za-z0-9]+$")


def _model_filename(ancestry: str) -> str:
    return f"{ancestry}-HLA4.RData"


def detect_rscript(rscript: Path | None = None) -> Path | None:
    """Resolve the ``Rscript`` executable from a setting or ``PATH``.

    ``rscript`` may be the executable itself, a directory containing it, or
    ``None`` (resolve ``Rscript`` on ``PATH``). Returns the path, or ``None`` when
    it cannot be found (so callers report the engine unavailable rather than
    crash). Executability is required.
    """
    if rscript is not None:
        p = Path(rscript)
        if p.is_dir():
            return resolve_binary(RSCRIPT_BIN, p)
        if p.is_file() and os.access(p, os.X_OK):
            return p
        return None
    return resolve_binary(RSCRIPT_BIN, None)


def available_ancestry_models(model_dir: Path | None) -> list[str]:
    """Ancestries whose pre-fit model file is present in ``model_dir`` (sorted)."""
    if model_dir is None:
        return []
    d = Path(model_dir)
    if not d.is_dir():
        return []
    return [a for a in KNOWN_ANCESTRIES if (d / _model_filename(a)).is_file()]


def resolve_model(model_dir: Path | None, ancestry: str) -> Path | None:
    """Path to the ``{ancestry}-HLA4.RData`` model, or ``None`` if absent.

    ``ancestry`` must be one of :data:`KNOWN_ANCESTRIES` — this both matches
    :func:`available_ancestry_models` and prevents a free-form value (e.g.
    ``../../etc``) from steering the path outside ``model_dir``.
    """
    if model_dir is None or ancestry not in KNOWN_ANCESTRIES:
        return None
    candidate = Path(model_dir) / _model_filename(ancestry)
    return candidate if candidate.is_file() else None


@dataclass(frozen=True)
class HibagRuntimeStatus:
    """Whether the HIBAG runtime is usable, for the status surface."""

    rscript_available: bool
    model_dir: str | None
    ancestry_models: list[str]
    available: bool  # rscript present AND at least one model present


def hibag_runtime_status(
    rscript: Path | None = None, model_dir: Path | None = None
) -> HibagRuntimeStatus:
    """Report HIBAG runtime availability (never raises)."""
    rs = detect_rscript(rscript)
    models = available_ancestry_models(model_dir)
    return HibagRuntimeStatus(
        rscript_available=rs is not None,
        model_dir=str(model_dir) if model_dir is not None else None,
        ancestry_models=models,
        available=rs is not None and bool(models),
    )


def hibag_available(rscript: Path | None = None, model_dir: Path | None = None) -> bool:
    """True iff Rscript resolves AND at least one ancestry model is present."""
    return hibag_runtime_status(rscript, model_dir).available


@dataclass(frozen=True)
class HLACall:
    """One predicted HLA genotype at a locus."""

    locus: str
    sample_id: str
    allele1: str
    allele2: str
    prob: float | None  # HIBAG posterior call probability (the confidence)
    matching: float | None  # QC signal (SNP-profile match), NOT a confidence
    low_confidence: bool  # prob is None or below the call threshold


@dataclass
class HibagResult:
    """Outcome of a HIBAG prediction run."""

    return_ok: bool
    calls: list[HLACall] = field(default_factory=list)
    runtime_seconds: float = 0.0
    output_tsv: Path | None = None
    stderr_tail: str = ""


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


# The column contract the R script writes; the parser fails closed if the header
# does not carry all of these (a malformed/empty TSV must not read as success).
_REQUIRED_TSV_COLUMNS = ("locus", "sample.id", "allele1", "allele2", "prob", "matching")


def parse_hibag_tsv(
    tsv_path: Path, *, prob_threshold: float = RECOMMENDED_PROB_THRESHOLD
) -> list[HLACall]:
    """Parse the R script's TSV (``locus sample.id allele1 allele2 prob matching``).

    A call is flagged ``low_confidence`` when ``prob`` is missing or below
    ``prob_threshold``.

    Raises:
        ValueError: the header is missing a required column (broken contract).
    """
    calls: list[HLACall] = []
    with Path(tsv_path).open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        header = set(reader.fieldnames or ())
        missing = [c for c in _REQUIRED_TSV_COLUMNS if c not in header]
        if missing:
            raise ValueError(f"HIBAG output missing columns: {missing}")
        for row in reader:
            locus = (row.get("locus") or "").strip()
            if not locus:
                continue
            prob = _to_float(row.get("prob"))
            calls.append(
                HLACall(
                    locus=locus,
                    sample_id=(row.get("sample.id") or "").strip(),
                    allele1=(row.get("allele1") or "").strip(),
                    allele2=(row.get("allele2") or "").strip(),
                    prob=prob,
                    matching=_to_float(row.get("matching")),
                    low_confidence=prob is None or prob < prob_threshold,
                )
            )
    return calls


class HibagRunner:
    """Drives ``Rscript hibag_predict.R`` to call HLA alleles from PLINK genotypes."""

    def __init__(
        self,
        *,
        rscript: Path | None = None,
        model_dir: Path | None = None,
        r_script_path: Path | None = None,
    ) -> None:
        rs = detect_rscript(rscript)
        if rs is None:
            where = f" ({rscript})" if rscript is not None else " on PATH"
            raise FileNotFoundError(
                f"Rscript not found{where} — install R (>= 4) + the Bioconductor "
                f"HIBAG package, then set settings.hibag_rscript or put Rscript on PATH."
            )
        self.rscript = rs
        self.model_dir = Path(model_dir) if model_dir is not None else None
        self.r_script = Path(r_script_path) if r_script_path is not None else _R_SCRIPT
        if not self.r_script.is_file():
            raise FileNotFoundError(f"HIBAG R script not found at {self.r_script}")

    @classmethod
    def from_settings(cls, settings, **kwargs) -> HibagRunner:  # noqa: ANN001
        """Build a runner from app settings (``hibag_rscript`` / ``hibag_model_dir``)."""
        return cls(rscript=settings.hibag_rscript, model_dir=settings.hibag_model_dir, **kwargs)

    def _run(self, cmd: list[str], timeout: float) -> tuple[bool, str, float]:
        """Run the Rscript subprocess; return (ok, stderr_tail, runtime_seconds)."""
        start = time.monotonic()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: S603
        except subprocess.TimeoutExpired:
            runtime = time.monotonic() - start
            logger.error("hibag_timeout", timeout=timeout)
            return False, "timeout", runtime
        except OSError as exc:
            runtime = time.monotonic() - start
            logger.error("hibag_exec_failed", error_type=type(exc).__name__)
            return False, type(exc).__name__, runtime
        runtime = time.monotonic() - start
        if proc.returncode != 0:
            stderr_tail = proc.stderr[-500:]
            # stderr can carry local paths / sample IDs — log only its size.
            logger.error("hibag_failed", returncode=proc.returncode, stderr_chars=len(stderr_tail))
            return False, stderr_tail, runtime
        return True, "", runtime

    def predict(
        self,
        plink_prefix: Path,
        model: Path,
        out_dir: Path,
        *,
        loci: Sequence[str] = HLA_LOCI,
        prob_threshold: float = RECOMMENDED_PROB_THRESHOLD,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> HibagResult:
        """Predict HLA alleles for ``plink_prefix`` against ``model``.

        Args:
            plink_prefix: PLINK fileset prefix (``<prefix>.bed/.bim/.fam``).
            model: a BYO pre-fit HIBAG ``.RData`` (ancestry-specific).
            out_dir: output directory (created if absent).
            loci: HLA loci to call (default the seven classical loci).
            prob_threshold: posterior-probability gate for ``low_confidence``.
            timeout: wall-clock ceiling (seconds).

        Returns a :class:`HibagResult`; an engine/IO runtime failure (Rscript
        non-zero exit, timeout, launch error, or unparseable output) sets
        ``return_ok`` False rather than raising. Raises only for a caller/setup
        error: an invalid locus token (``ValueError``) or a missing PLINK /
        model input (``FileNotFoundError``).
        """
        loci = [str(loc_).strip() for loc_ in loci]
        bad = [loc_ for loc_ in loci if not _LOCUS_RE.fullmatch(loc_)]
        if bad:
            raise ValueError(f"invalid HLA locus token(s): {bad}")
        if not loci:
            raise ValueError("no HLA loci requested")

        plink_prefix = Path(plink_prefix)
        model = Path(model)
        # APPEND the PLINK extensions (a prefix may contain dots, e.g. a cohort
        # name); ``with_suffix`` would replace the trailing ``.foo`` and mismatch
        # the R script, which uses ``paste0(prefix, ".bed")``.
        plink_files = [Path(f"{plink_prefix}{ext}") for ext in (".bed", ".bim", ".fam")]
        missing = [str(p) for p in (*plink_files, model) if not p.exists()]
        if missing:
            raise FileNotFoundError(f"HIBAG inputs missing: {missing}")

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_tsv = out_dir / "hla_calls.tsv"
        cmd = [
            str(self.rscript),
            str(self.r_script),
            "--plink",
            str(plink_prefix),
            "--model",
            str(model),
            "--loci",
            ",".join(loci),
            "--out",
            str(out_tsv),
        ]
        ok, stderr_tail, runtime = self._run(cmd, timeout)
        if not ok or not (out_tsv.exists() and out_tsv.stat().st_size > 0):
            return HibagResult(
                return_ok=False,
                runtime_seconds=runtime,
                stderr_tail=stderr_tail or "no HLA-call output produced",
            )
        try:
            calls = parse_hibag_tsv(out_tsv, prob_threshold=prob_threshold)
        except (OSError, ValueError) as exc:
            logger.error("hibag_parse_failed", error_type=type(exc).__name__)
            return HibagResult(
                return_ok=False,
                runtime_seconds=runtime,
                stderr_tail=f"failed to parse HLA-call output: {exc}",
            )
        if not calls:
            # A well-formed but empty result means nothing was called — fail closed
            # rather than returning a successful run with zero HLA calls.
            return HibagResult(
                return_ok=False,
                runtime_seconds=runtime,
                stderr_tail="HIBAG produced no HLA calls",
            )
        logger.info(
            "hibag_predict_complete", runtime_seconds=round(runtime, 1), n_calls=len(calls)
        )
        return HibagResult(
            return_ok=True,
            calls=calls,
            runtime_seconds=runtime,
            output_tsv=out_tsv,
        )

    def predict_for_ancestry(
        self,
        plink_prefix: Path,
        ancestry: str,
        out_dir: Path,
        **kwargs,
    ) -> HibagResult:
        """Resolve the ``ancestry`` model from ``model_dir`` and :meth:`predict`.

        Raises:
            FileNotFoundError: no model directory is configured, or no
                ``{ancestry}-HLA4.RData`` is present.
        """
        model = resolve_model(self.model_dir, ancestry)
        if model is None:
            raise FileNotFoundError(
                f"no HIBAG model for ancestry {ancestry!r} in {self.model_dir} — "
                f"fetch {_model_filename(ancestry)} (BYO; never bundled)."
            )
        return self.predict(plink_prefix, model, out_dir, **kwargs)
