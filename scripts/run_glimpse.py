"""Run GLIMPSE2 low-coverage-WGS imputation + report runtime/quality (SW-C7).

Drives the GLIMPSE2 chunk → phase → ligate workflow per chromosome and prints a
per-chromosome + total **wall-clock runtime**, the IMPUTE info-score quality
summary, and the SW-C3 firewall outcome. This is the tool to run on a cluster (or
laptop) to validate GLIMPSE2's output shape and measure runtime once a GLIMPSE2
build is provisioned.

    python scripts/run_glimpse.py --check               # report engine availability
    python scripts/run_glimpse.py --chrom 22 \
        --input-gl-dir GL --reference-dir REF --map-dir MAPS

For each ``--chrom``, the per-chromosome inputs are resolved from the given
directories using filename templates (``{chrom}`` is substituted):

    GL  : --input-gl-template   (default chr{chrom}.gl.vcf.gz)  — FORMAT/GL or /PL
    REF : --reference-template  (default chr{chrom}.reference.vcf.gz) — phased VCF/BCF
    MAP : --map-template        (default chr{chrom}.gmap.gz)

GLIMPSE2 imputes low-coverage **sequencing** genotype likelihoods — it is NOT the
SNP-array path (that is Beagle / ``scripts/run_imputation.py``). Prerequisite: the
GLIMPSE2 binaries on PATH or via ``--bin-dir`` / ``settings.glimpse_bin_dir``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.analysis.glimpse_runner import (
    GlimpseRunner,
    glimpse_available,
    missing_binaries,
    parse_glimpse_vcf,
)
from backend.analysis.imputation_firewall import FirewallSummary, summarize_firewall
from backend.analysis.imputation_runner import ImputationSummary, summarize_dr2
from backend.annotation.imputation_panel import PANEL_CHROMOSOMES


def _accumulate_firewall(total: FirewallSummary, part: FirewallSummary) -> None:
    """Fold one chromosome's firewall summary into the running total."""
    total.n_imputed += part.n_imputed
    total.n_reportable += part.n_reportable
    total.n_quarantined += part.n_quarantined
    for reason, count in part.quarantine_reasons.items():
        total.quarantine_reasons[reason] = total.quarantine_reasons.get(reason, 0) + count


def _format_reasons(reasons: dict[str, int]) -> str:
    return ", ".join(f"{reason}={count}" for reason, count in sorted(reasons.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report GLIMPSE2 binary availability and exit (0 if all present).",
    )
    parser.add_argument(
        "--chrom",
        action="append",
        choices=PANEL_CHROMOSOMES,
        metavar="CHROM",
        help="Chromosome(s) to impute (repeatable).",
    )
    parser.add_argument("--input-gl-dir", type=Path, help="Directory of per-chrom GL VCFs.")
    parser.add_argument(
        "--reference-dir", type=Path, help="Directory of per-chrom reference VCF/BCF."
    )
    parser.add_argument("--map-dir", type=Path, help="Directory of per-chrom genetic maps.")
    parser.add_argument("--input-gl-template", default="chr{chrom}.gl.vcf.gz")
    parser.add_argument("--reference-template", default="chr{chrom}.reference.vcf.gz")
    parser.add_argument("--map-template", default="chr{chrom}.gmap.gz")
    parser.add_argument(
        "--out-dir", type=Path, default=None, help="Output dir (default ./glimpse_out)."
    )
    parser.add_argument(
        "--bin-dir", type=Path, default=None, help="GLIMPSE2 binaries dir (else PATH)."
    )
    parser.add_argument("--nthreads", type=int, default=None, help="GLIMPSE2 --threads.")
    parser.add_argument("--timeout", type=float, default=3600.0, help="Per-step seconds.")
    args = parser.parse_args(argv)

    if args.check:
        if glimpse_available(args.bin_dir):
            print("GLIMPSE2: available (all binaries resolved).")
            return 0
        print(f"GLIMPSE2: UNAVAILABLE — missing {missing_binaries(args.bin_dir)}", file=sys.stderr)
        return 1

    if not args.chrom:
        parser.error("at least one --chrom is required (or use --check)")
    for needed in ("input_gl_dir", "reference_dir", "map_dir"):
        if getattr(args, needed) is None:
            parser.error(f"--{needed.replace('_', '-')} is required")
    if not glimpse_available(args.bin_dir):
        parser.error(f"GLIMPSE2 unavailable — missing {missing_binaries(args.bin_dir)}")

    out_dir = args.out_dir or Path("glimpse_out")
    runner = GlimpseRunner(bin_dir=args.bin_dir, nthreads=args.nthreads)

    total = ImputationSummary()
    fw_total = FirewallSummary()
    failures: list[str] = []
    # Dedupe (preserve order) so a repeated --chrom can't double-count / overwrite.
    for c in dict.fromkeys(args.chrom):
        input_gl = args.input_gl_dir / args.input_gl_template.format(chrom=c)
        reference = args.reference_dir / args.reference_template.format(chrom=c)
        gmap = args.map_dir / args.map_template.format(chrom=c)
        missing = [str(p) for p in (input_gl, reference, gmap) if not p.exists()]
        if missing:
            print(f"chr{c}: SKIP (missing {missing})", file=sys.stderr, flush=True)
            failures.append(c)
            continue
        res = runner.impute_chromosome(c, input_gl, reference, gmap, out_dir, timeout=args.timeout)
        if not res.return_ok or res.output_vcf is None:
            print(f"chr{c}: FAILED ({res.stderr_tail})", file=sys.stderr, flush=True)
            failures.append(c)
            continue
        # Two streaming passes (O(1) memory) rather than materializing every marker
        # of a whole chromosome into a list.
        s = summarize_dr2(parse_glimpse_vcf(res.output_vcf))
        fw = summarize_firewall(parse_glimpse_vcf(res.output_vcf))
        total.n_total += s.n_total
        total.n_imputed += s.n_imputed
        total.n_well_imputed += s.n_well_imputed
        total.chrom_runtimes[c] = res.runtime_seconds
        _accumulate_firewall(fw_total, fw)
        frac = s.frac_well_imputed
        frac_str = f"{frac:.1%}" if frac is not None else "n/a"
        rep_frac = fw.frac_reportable
        rep_str = f"{rep_frac:.1%}" if rep_frac is not None else "n/a"
        print(
            f"chr{c}: {res.runtime_seconds:.1f}s  {res.n_chunks} chunks  "
            f"{s.n_total} markers ({frac_str} info>=0.8; "
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
        f"{grand_frac_str} with info>=0.8 (IMPUTE info score)."
    )
    reasons_tail = (
        f" — {_format_reasons(fw_total.quarantine_reasons)}."
        if fw_total.quarantine_reasons
        else "."
    )
    print(
        f"FIREWALL (SW-C3): {fw_total.n_reportable}/{fw_total.n_imputed} imputed markers "
        f"clear the MAF/info firewall ({grand_rep_str}); "
        f"{fw_total.n_quarantined} quarantined from P/LP/carrier/monogenic calls" + reasons_tail
    )
    if failures:
        print(f"Failed/skipped chromosomes: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
