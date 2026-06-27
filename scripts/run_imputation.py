"""Run local Beagle imputation against the 1000G panel + report runtime (SW-C2).

Imputes per-chromosome input VCFs against the installed SW-C1 reference panel and
prints a per-chromosome + total **wall-clock runtime** and DR2 imputation-quality
summary. This is the tool to run on a target laptop to measure imputation runtime
(the Wave C plan's per-laptop measurement) — the timing reflects whatever machine
runs it.

    python scripts/run_imputation.py --input-dir /path/to/per_chrom_vcfs

The input directory must contain per-chromosome bgzipped VCFs named
``chr{N}.vcf.gz`` (the sample's typed genotypes for that chromosome). Restrict to
specific chromosomes with ``--chrom`` (repeatable). Prerequisites: the imputation
panel (``scripts/fetch_imputation_panel.py``) and the LAI bundle (its vendored
Beagle JAR is reused). Producing the per-chromosome input VCFs from a sample DB is
a separate step (next Wave C slice); this CLI consumes ready VCFs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.analysis.imputation_runner import (
    ImputationRunner,
    ImputationSummary,
    parse_imputed_vcf,
    summarize_dr2,
)
from backend.annotation.imputation_panel import PANEL_CHROMOSOMES
from backend.db.connection import get_registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help="Directory of per-chromosome input VCFs named chr{N}.vcf.gz.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory for imputed VCFs (default: <input-dir>/imputed).",
    )
    parser.add_argument(
        "--chrom",
        action="append",
        choices=PANEL_CHROMOSOMES,
        metavar="CHROM",
        help="Restrict to specific chromosome(s) (repeatable). Default: all present.",
    )
    parser.add_argument("--java-mem", default="8g", help="Beagle JVM heap (default 8g).")
    parser.add_argument("--nthreads", type=int, default=None, help="Beagle nthreads.")
    parser.add_argument("--timeout", type=float, default=3600.0, help="Per-chrom seconds.")
    args = parser.parse_args(argv)

    input_dir: Path = args.input_dir
    if not input_dir.is_dir():
        parser.error(f"input dir not found: {input_dir}")
    out_dir = args.out_dir or (input_dir / "imputed")

    chroms = args.chrom or [
        c for c in PANEL_CHROMOSOMES if (input_dir / f"chr{c}.vcf.gz").exists()
    ]
    if not chroms:
        parser.error(f"no per-chromosome input VCFs (chr{{N}}.vcf.gz) found in {input_dir}")

    registry = get_registry()
    runner = ImputationRunner.from_settings(
        registry.settings, java_mem=args.java_mem, nthreads=args.nthreads
    )

    total = ImputationSummary()
    failures: list[str] = []
    for c in chroms:
        inp = input_dir / f"chr{c}.vcf.gz"
        if not inp.exists():
            print(f"chr{c}: SKIP (no {inp.name})", flush=True)
            continue
        res = runner.impute_chromosome(c, inp, out_dir, timeout=args.timeout)
        if not res.return_ok or res.output_vcf is None:
            print(f"chr{c}: FAILED ({res.stderr_tail})", file=sys.stderr, flush=True)
            failures.append(c)
            continue
        s = summarize_dr2(parse_imputed_vcf(res.output_vcf))
        total.n_total += s.n_total
        total.n_imputed += s.n_imputed
        total.n_well_imputed += s.n_well_imputed
        total.chrom_runtimes[c] = res.runtime_seconds
        frac = s.frac_well_imputed
        frac_str = f"{frac:.1%}" if frac is not None else "n/a"
        print(
            f"chr{c}: {res.runtime_seconds:.1f}s  "
            f"{s.n_total} markers ({s.n_imputed} imputed, {frac_str} DR2>=0.8)",
            flush=True,
        )

    grand_frac = total.frac_well_imputed
    grand_frac_str = f"{grand_frac:.1%}" if grand_frac is not None else "n/a"
    print(
        f"\nTOTAL: {total.total_runtime_seconds:.1f}s wall-clock over "
        f"{len(total.chrom_runtimes)} chromosome(s); {total.n_total} markers, "
        f"{total.n_imputed} imputed, {grand_frac_str} with DR2>=0.8."
    )
    if failures:
        print(f"Failed chromosomes: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
