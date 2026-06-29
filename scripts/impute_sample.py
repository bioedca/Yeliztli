"""Impute a sample end-to-end and persist its firewall-cleared variants (Wave C).

One command for the whole Wave C per-sample flow: prepare per-chromosome input
VCFs from the sample's ``annotated_variants``, impute each against the installed
1000G panel with Beagle, apply the SW-C3 MAF/r² firewall, and store the imputed
variants that clear it into the sample DB's ``imputed_variants`` table.

    python scripts/impute_sample.py --sample-db sample.db --work-dir /tmp/imp

Prerequisites: the imputation panel (``scripts/fetch_imputation_panel.py``) and the
LAI bundle (its vendored Beagle JAR is reused). The sample DB must already be
annotated. Restrict to specific chromosomes with ``--chrom`` (repeatable).
Autosomes are the default. Chromosome X can be requested with ``--chrom X`` and
requires ``--biological-sex XX|XY`` so PAR/non-PAR ploidy is encoded correctly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.analysis.imputation_input import DEFAULT_INPUT_CHROMOSOMES, INPUT_CHROMOSOMES
from backend.analysis.imputation_persist import impute_and_persist_sample
from backend.analysis.imputation_runner import beagle_jar_path
from backend.db.connection import get_registry


def _positive_int(value: str) -> int:
    n = int(value)
    if n <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return n


def _positive_float(value: str) -> float:
    f = float(value)
    if f <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive number, got {value}")
    return f


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--sample-db",
        type=Path,
        required=True,
        help="Path to the per-sample SQLite DB (annotated_variants must be populated).",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        required=True,
        help="Scratch directory for intermediate input/imputed VCFs.",
    )
    parser.add_argument(
        "--chrom",
        action="append",
        choices=INPUT_CHROMOSOMES,
        metavar="CHROM",
        help=(
            "Restrict to specific chromosome(s), including X (repeatable). "
            "Default: all autosomes 1-22."
        ),
    )
    parser.add_argument(
        "--biological-sex",
        choices=("XX", "XY"),
        default=None,
        help="Required when --chrom X is requested; controls chromosome-X ploidy.",
    )
    parser.add_argument("--java-mem", default="8g", help="Beagle JVM heap (default 8g).")
    parser.add_argument("--nthreads", type=_positive_int, default=None, help="Beagle nthreads.")
    parser.add_argument(
        "--timeout", type=_positive_float, default=3600.0, help="Per-chrom seconds."
    )
    args = parser.parse_args(argv)

    sample_db: Path = args.sample_db
    if not sample_db.is_file():
        parser.error(f"sample DB not found: {sample_db}")

    chroms = tuple(args.chrom) if args.chrom else DEFAULT_INPUT_CHROMOSOMES
    if "X" in chroms and args.biological_sex is None:
        parser.error("--biological-sex XX|XY is required when --chrom X is requested")

    registry = get_registry()
    settings = registry.settings
    engine = registry.get_sample_engine(sample_db)

    result = impute_and_persist_sample(
        engine,
        args.work_dir,
        panel_dir=settings.imputation_panel_dir,
        beagle_jar=beagle_jar_path(settings.resolved_lai_bundle_path),
        chromosomes=chroms,
        biological_sex=args.biological_sex,
        java_mem=args.java_mem,
        nthreads=args.nthreads,
        timeout=args.timeout,
    )

    failures = [r.chrom for r in result.chrom_results if not r.return_ok]
    for r in result.chrom_results:
        status = "ok" if r.return_ok else f"FAILED ({r.stderr_tail})"
        print(
            f"chr{r.chrom}: {r.runtime_seconds:.1f}s  {r.n_total} markers "
            f"({r.n_imputed} imputed) — {status}",
            flush=True,
        )
    rep = result.firewall.frac_reportable
    rep_str = f"{rep:.1%}" if rep is not None else "n/a"
    print(
        f"\n{result.n_input_sites} typed sites imputed → {result.n_imputed} imputed markers; "
        f"firewall cleared {result.firewall.n_reportable} ({rep_str}), "
        f"persisted {result.n_persisted} to imputed_variants.",
    )
    if failures:
        print(f"Failed chromosomes: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
