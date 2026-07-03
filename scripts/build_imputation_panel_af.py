"""Build Beagle 1000G Phase 3 v5a reference-panel AF TSVs from panel VCFs.

The Beagle imputation firewall must not use Beagle output ``AF`` because that is
the target-sample frequency. This script derives per-ALT reference-panel AF from
the Beagle-distributed sibling VCFs:

    python scripts/build_imputation_panel_af.py --vcf-dir /path/to/b37.vcf --chrom 22

The output files are written to ``settings.imputation_panel_dir`` by default:
``chrN.1kg.phase3.v5a.b37.af.tsv.gz``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.annotation.imputation_panel import PANEL_CHROMOSOMES
from backend.annotation.imputation_panel_af import (
    build_panel_af_index,
    panel_af_path,
    panel_vcf_path,
)
from backend.db.connection import get_registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--vcf-dir",
        type=Path,
        required=True,
        help="Directory containing chrN.1kg.phase3.v5a.vcf.gz panel VCFs.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: configured imputation_panel_dir).",
    )
    parser.add_argument(
        "--chrom",
        action="append",
        choices=PANEL_CHROMOSOMES,
        metavar="CHROM",
        help="Restrict to chromosome(s), repeatable. Default: all chromosomes.",
    )
    args = parser.parse_args(argv)

    registry = get_registry()
    out_dir = args.out_dir or registry.settings.imputation_panel_dir
    chroms = tuple(args.chrom) if args.chrom else PANEL_CHROMOSOMES

    missing: list[Path] = []
    total = 0
    for chrom in chroms:
        vcf = panel_vcf_path(args.vcf_dir, chrom)
        if not vcf.exists():
            missing.append(vcf)
            continue
        out = panel_af_path(out_dir, chrom)
        n = build_panel_af_index(vcf, out)
        total += n
        print(f"chr{chrom}: wrote {n} AF rows to {out}", flush=True)

    if missing:
        for path in missing:
            print(f"missing panel VCF: {path}", file=sys.stderr)
        return 1
    print(f"Built {total} panel AF rows across {len(chroms)} chromosome(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
