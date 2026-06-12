#!/usr/bin/env python3
"""Sex-inference threshold validation script (Plan §9.4).

Reports chrY non-no-call rate, non-PAR chrX heterozygous-call rate, and the
classification the Plan §9.4 algorithm produces at the literature-default
thresholds for a single raw export.

Local-only by design: the real export is never committed, so this script is
run by the bio-validator against a private file on disk. CI runs the script
only against the synthetic fixtures under ``tests/fixtures/sex_inference_synthetic/``
(XX, XY, manual_review). The fixture parses through the regular dispatcher,
so any vendor the dispatcher recognises (23andMe or AncestryDNA) works as
input — no vendor-specific code path here.

The output is aggregate counts and rates only — no genotype rows are
emitted — so its stdout is safe to paste into the bio-validator's
attestation (`docs/sex_inference_threshold_validation.md`, Step 53).

Plan §9.4 thresholds:

- ``_THRESHOLD_XY_CONFIRM`` default ``0.30`` — chrY non-no-call rate above
  which a candidate XY is confirmed.
- ``_THRESHOLD_PAR_NOISE`` default ``0.10`` — chrY non-no-call rate above
  which a candidate XY without confirmation is flagged for manual review.

GRCh37 PAR coordinates (load-bearing — PAR sites carry no sex signal and
must be excluded from the chrX zygosity check):

- PAR1: ``chrX:60001 – 2699520``
- PAR2: ``chrX:154931044 – 155260560``

Usage::

    python scripts/validate_sex_thresholds.py <path-to-raw-export>
    python scripts/validate_sex_thresholds.py <path> --json
    python scripts/validate_sex_thresholds.py <path> --xy-threshold 0.25 --par-noise 0.08
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# Allow ``python scripts/validate_sex_thresholds.py …`` without an editable install.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.ingestion.base import ParserError, ParseResult  # noqa: E402
from backend.ingestion.dispatcher import parse as dispatch_parse  # noqa: E402

# ---------------------------------------------------------------------------
# Constants — mirror the values that land in backend/services/sex_inference.py
# at Step 54. Keep these in sync if either side moves.
# ---------------------------------------------------------------------------

PAR1: tuple[int, int] = (60001, 2_699_520)
PAR2: tuple[int, int] = (154_931_044, 155_260_560)
DEFAULT_XY_CONFIRM: float = 0.30
DEFAULT_PAR_NOISE: float = 0.10
NO_CALL: str = "--"


@dataclass
class Report:
    """Aggregate output of one validation run.

    Counts only — never genotype rows — so this dataclass can be serialised
    straight into the bio-validator attestation document.
    """

    file_path: str
    vendor: str
    version: str
    build: str
    variant_count: int
    x_total: int
    x_par_count: int
    x_nonpar_typed: int
    x_nonpar_nocall: int
    x_nonpar_het: int
    x_nonpar_hom: int
    x_nonpar_het_rate: float
    y_total: int
    y_typed: int
    y_rate: float
    xy_confirm_threshold: float
    par_noise_threshold: float
    classification: str


def _is_par(pos: int) -> bool:
    return PAR1[0] <= pos <= PAR1[1] or PAR2[0] <= pos <= PAR2[1]


def _is_no_call(genotype: str) -> bool:
    # ``--`` is the canonical no-call sentinel emitted by both vendor parsers.
    # ``"00"`` survives only in the rare case where a downstream caller bypasses
    # the parser; tolerate it for forensic robustness against ad-hoc inputs.
    return genotype in {NO_CALL, "00", "", "0"}


def _is_het(genotype: str) -> bool:
    return len(genotype) == 2 and genotype[0] != genotype[1] and not _is_no_call(genotype)


def _is_hom(genotype: str) -> bool:
    return len(genotype) == 2 and genotype[0] == genotype[1] and not _is_no_call(genotype)


def classify(
    *,
    x_nonpar_het: int,
    x_nonpar_typed: int,
    x_nonpar_hom: int,
    y_rate: float,
    xy_confirm: float,
    par_noise: float,
) -> str:
    """Apply the Plan §9.4 algorithm to pre-tabulated counts.

    Order is load-bearing:

    1. ``≥1`` heterozygous non-PAR chrX call supports XX only when chrY
       evidence is at/below the PAR-noise floor. Above that floor, the
       X/Y signals are discordant and require manual review.
    2. Otherwise, if at least one non-PAR chrX SNP was typed and every
       typed call is homozygous, the sample is a *candidate* XY that needs
       chrY confirmation.
    3. chrY non-no-call rate above ``xy_confirm`` confirms XY; above
       ``par_noise`` but at/below ``xy_confirm`` flags for manual review;
       at/below ``par_noise`` falls back to ``unknown`` rather than auto-
       assigning.
    """
    if x_nonpar_het >= 1:
        if y_rate > par_noise:
            return "manual_review"
        return "XX"
    if x_nonpar_typed > 0 and x_nonpar_hom == x_nonpar_typed:
        if y_rate > xy_confirm:
            return "XY"
        if y_rate > par_noise:
            return "manual_review"
    return "unknown"


def build_report(
    path: Path,
    *,
    xy_confirm: float = DEFAULT_XY_CONFIRM,
    par_noise: float = DEFAULT_PAR_NOISE,
) -> Report:
    """Parse *path* via the vendor dispatcher and tabulate sex-inference inputs."""
    result: ParseResult = dispatch_parse(path)

    x_total = 0
    x_par_count = 0
    x_nonpar_typed = 0
    x_nonpar_nocall = 0
    x_nonpar_het = 0
    x_nonpar_hom = 0
    y_total = 0
    y_typed = 0

    for variant in result.variants:
        if variant.chrom == "X":
            x_total += 1
            if _is_par(variant.pos):
                x_par_count += 1
                continue
            if _is_no_call(variant.genotype):
                x_nonpar_nocall += 1
            elif _is_het(variant.genotype):
                x_nonpar_het += 1
                x_nonpar_typed += 1
            elif _is_hom(variant.genotype):
                x_nonpar_hom += 1
                x_nonpar_typed += 1
        elif variant.chrom == "Y":
            y_total += 1
            if not _is_no_call(variant.genotype):
                y_typed += 1

    y_rate = (y_typed / y_total) if y_total else 0.0
    x_nonpar_het_rate = (x_nonpar_het / x_nonpar_typed) if x_nonpar_typed else 0.0

    return Report(
        file_path=str(path),
        vendor=result.vendor.value,
        version=result.version,
        build=result.build,
        variant_count=len(result.variants),
        x_total=x_total,
        x_par_count=x_par_count,
        x_nonpar_typed=x_nonpar_typed,
        x_nonpar_nocall=x_nonpar_nocall,
        x_nonpar_het=x_nonpar_het,
        x_nonpar_hom=x_nonpar_hom,
        x_nonpar_het_rate=x_nonpar_het_rate,
        y_total=y_total,
        y_typed=y_typed,
        y_rate=y_rate,
        xy_confirm_threshold=xy_confirm,
        par_noise_threshold=par_noise,
        classification=classify(
            x_nonpar_het=x_nonpar_het,
            x_nonpar_typed=x_nonpar_typed,
            x_nonpar_hom=x_nonpar_hom,
            y_rate=y_rate,
            xy_confirm=xy_confirm,
            par_noise=par_noise,
        ),
    )


def _format_text(report: Report) -> str:
    return "\n".join(
        [
            f"file                      : {report.file_path}",
            f"vendor / version / build  : {report.vendor} {report.version} {report.build}",
            f"total variants            : {report.variant_count}",
            "",
            f"chrX calls (total)        : {report.x_total}",
            f"  PAR (pre-filtered)      : {report.x_par_count}",
            f"  non-PAR typed           : {report.x_nonpar_typed}",
            f"    heterozygous          : {report.x_nonpar_het}",
            f"    homozygous            : {report.x_nonpar_hom}",
            f"  non-PAR no-call         : {report.x_nonpar_nocall}",
            f"  non-PAR het rate        : {report.x_nonpar_het_rate:.3f}",
            "",
            f"chrY calls (total)        : {report.y_total}",
            f"  non-no-call             : {report.y_typed}",
            f"  non-no-call rate        : {report.y_rate:.3f}",
            "",
            f"thresholds (XY-confirm / PAR-noise): "
            f"{report.xy_confirm_threshold} / {report.par_noise_threshold}",
            f"classification            : {report.classification}",
        ]
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report chrY non-no-call rate, non-PAR chrX heterozygous-call rate, "
            "and the Plan §9.4 sex-inference classification for a raw export."
        ),
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Path to a raw 23andMe or AncestryDNA export.",
    )
    parser.add_argument(
        "--xy-threshold",
        type=float,
        default=DEFAULT_XY_CONFIRM,
        help=(
            "Override _THRESHOLD_XY_CONFIRM (chrY non-no-call rate above which "
            f"a candidate XY is confirmed). Default {DEFAULT_XY_CONFIRM}."
        ),
    )
    parser.add_argument(
        "--par-noise",
        type=float,
        default=DEFAULT_PAR_NOISE,
        help=(
            "Override _THRESHOLD_PAR_NOISE (chrY non-no-call rate above which "
            f"an unconfirmed candidate XY is flagged for manual review). "
            f"Default {DEFAULT_PAR_NOISE}."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON report instead of the text summary.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if not args.path.exists():
        sys.stderr.write(f"error: file not found: {args.path}\n")
        return 2

    if not (0.0 <= args.xy_threshold <= 1.0):
        sys.stderr.write("error: --xy-threshold must be in [0.0, 1.0]\n")
        return 2
    if not (0.0 <= args.par_noise <= 1.0):
        sys.stderr.write("error: --par-noise must be in [0.0, 1.0]\n")
        return 2
    if args.par_noise > args.xy_threshold:
        sys.stderr.write("error: --par-noise must be <= --xy-threshold\n")
        return 2

    try:
        report = build_report(
            args.path,
            xy_confirm=args.xy_threshold,
            par_noise=args.par_noise,
        )
    except ParserError as exc:
        sys.stderr.write(f"error: failed to parse {args.path}: {exc}\n")
        return 1

    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print(_format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
