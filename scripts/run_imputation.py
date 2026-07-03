"""Run local Beagle imputation against the 1000G panel + report runtime (SW-C2).

Imputes per-chromosome input VCFs against the installed SW-C1 reference panel and
prints a per-chromosome + total **wall-clock runtime** and DR2 imputation-quality
summary. This is the tool to run on a target laptop to measure imputation runtime
(the Wave C plan's per-laptop measurement) — the timing reflects whatever machine
runs it.

    python scripts/run_imputation.py --input-dir /path/to/per_chrom_vcfs

The input directory must contain bgzipped VCFs from
``scripts/prepare_imputation_input.py``: autosomes as ``chr{N}.vcf.gz`` and X as
split PAR/non-PAR region files (for example ``chrX_PAR1.vcf.gz``). Restrict to
specific chromosomes with ``--chrom`` (repeatable). Prerequisites: the imputation
panel (``scripts/fetch_imputation_panel.py``) and the LAI bundle (its vendored
Beagle JAR is reused). Producing the input VCFs from a sample DB is a separate
step; this CLI consumes ready VCFs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.analysis.imputation_firewall import FirewallSummary, summarize_firewall
from backend.analysis.imputation_input import input_unit_specs_for_chromosomes
from backend.analysis.imputation_runner import (
    ImputationRunner,
    ImputationSummary,
    parse_imputed_vcf,
    summarize_dr2,
)
from backend.annotation.imputation_panel import PANEL_CHROMOSOMES
from backend.annotation.imputation_panel_af import PanelAfLookup
from backend.db.connection import get_registry


def _accumulate_firewall(total: FirewallSummary, part: FirewallSummary) -> None:
    """Fold one chromosome's firewall summary into the running total."""
    total.n_imputed += part.n_imputed
    total.n_reportable += part.n_reportable
    total.n_quarantined += part.n_quarantined
    for reason, count in part.quarantine_reasons.items():
        total.quarantine_reasons[reason] = total.quarantine_reasons.get(reason, 0) + count


def _format_reasons(reasons: dict[str, int]) -> str:
    """Render quarantine reason counts as a stable, sorted ``reason=count`` string."""
    return ", ".join(f"{reason}={count}" for reason, count in sorted(reasons.items()))


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

    requested = tuple(args.chrom) if args.chrom else PANEL_CHROMOSOMES
    all_units = input_unit_specs_for_chromosomes(requested)
    units = (
        list(all_units)
        if args.chrom
        else [unit for unit in all_units if (input_dir / unit.filename).exists()]
    )
    if not units:
        parser.error(f"no imputation input VCFs found in {input_dir}")

    registry = get_registry()
    runner = ImputationRunner.from_settings(
        registry.settings, java_mem=args.java_mem, nthreads=args.nthreads
    )
    panel_af = PanelAfLookup(registry.settings.imputation_panel_dir)

    total = ImputationSummary()
    fw_total = FirewallSummary()
    failures: list[str] = []
    for unit in units:
        inp = input_dir / unit.filename
        if not inp.exists():
            print(f"chr{unit.key}: SKIP (no {inp.name})", flush=True)
            continue
        res = runner.impute_chromosome(
            unit.chrom,
            inp,
            out_dir,
            region=unit.beagle_region,
            output_label=unit.key,
            timeout=args.timeout,
        )
        if not res.return_ok or res.output_vcf is None:
            print(f"chr{unit.key}: FAILED ({res.stderr_tail})", file=sys.stderr, flush=True)
            failures.append(unit.key)
            continue
        # Materialize once: both the DR2 quality summary and the SW-C3 firewall
        # consume the same per-ALT records.
        variants = list(parse_imputed_vcf(res.output_vcf, panel_af=panel_af))
        s = summarize_dr2(variants)
        fw = summarize_firewall(variants)
        total.n_total += s.n_total
        total.n_imputed += s.n_imputed
        total.n_well_imputed += s.n_well_imputed
        total.chrom_runtimes[unit.key] = res.runtime_seconds
        _accumulate_firewall(fw_total, fw)
        frac = s.frac_well_imputed
        frac_str = f"{frac:.1%}" if frac is not None else "n/a"
        rep_frac = fw.frac_reportable
        rep_str = f"{rep_frac:.1%}" if rep_frac is not None else "n/a"
        print(
            f"chr{unit.key}: {res.runtime_seconds:.1f}s  "
            f"{s.n_total} markers ({s.n_imputed} imputed, {frac_str} DR2>=0.8; "
            f"firewall: {fw.n_reportable} reportable / {fw.n_quarantined} quarantined, "
            f"{rep_str} pass)",
            flush=True,
        )

    grand_frac = total.frac_well_imputed
    grand_frac_str = f"{grand_frac:.1%}" if grand_frac is not None else "n/a"
    grand_rep = fw_total.frac_reportable
    grand_rep_str = f"{grand_rep:.1%}" if grand_rep is not None else "n/a"
    print(
        f"\nTOTAL: {total.total_runtime_seconds:.1f}s wall-clock over "
        f"{len(total.chrom_runtimes)} chromosome(s); {total.n_total} markers, "
        f"{total.n_imputed} imputed, {grand_frac_str} with DR2>=0.8."
    )
    reasons_tail = (
        f" — {_format_reasons(fw_total.quarantine_reasons)}."
        if fw_total.quarantine_reasons
        else "."
    )
    print(
        f"FIREWALL (SW-C3): {fw_total.n_reportable}/{fw_total.n_imputed} imputed markers "
        f"clear the MAF/r² firewall ({grand_rep_str}); "
        f"{fw_total.n_quarantined} quarantined from P/LP/carrier/monogenic calls" + reasons_tail
    )
    if failures:
        print(f"Failed chromosomes: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
