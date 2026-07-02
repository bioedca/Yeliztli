"""Prepare a HIBAG PLINK input fileset from a sample DB (Wave D glue).

Reads a sample's ``annotated_variants`` and writes ``<prefix>.bed/.bim/.fam`` —
the exact PLINK fileset ``backend.analysis.hibag_runner.HibagRunner.predict``
(and its R script's ``hlaBED2Geno(assembly="hg19")``) consumes to call classical
HLA alleles. Only reference-aligned biallelic SNPs inside the extended-MHC window
on chromosome 6 are emitted; indels, no-calls and unresolved-reference sites are
dropped (see ``backend.analysis.hla_input``). Coordinates are native GRCh37/hg19,
so no liftover is performed.

    python scripts/prepare_hla_input.py --sample-db sample.db --out-prefix hla/sample
    Rscript backend/analysis/r/hibag_predict.R --plink hla/sample --model European-HLA4.RData ...

The sample DB must already be annotated (``annotated_variants`` populated); HIBAG
also needs a BYO ancestry model, which is never bundled.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.analysis.hla_input import XMHC_GRCH37, MHCRegion, write_hibag_plink_input
from backend.db.connection import get_registry


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
        "--out-prefix",
        type=Path,
        required=True,
        help="PLINK output prefix; writes <prefix>.bed/.bim/.fam (parent created if missing).",
    )
    parser.add_argument(
        "--region-start",
        type=int,
        default=XMHC_GRCH37.start,
        help=f"GRCh37 chr6 window start (default: {XMHC_GRCH37.start}).",
    )
    parser.add_argument(
        "--region-end",
        type=int,
        default=XMHC_GRCH37.end,
        help=f"GRCh37 chr6 window end (default: {XMHC_GRCH37.end}).",
    )
    parser.add_argument(
        "--sample-name",
        default="SAMPLE",
        help="Name for the PLINK .fam sample column (default: SAMPLE).",
    )
    args = parser.parse_args(argv)

    sample_db: Path = args.sample_db
    if not sample_db.is_file():
        parser.error(f"sample DB not found: {sample_db}")
    if args.region_start > args.region_end:
        parser.error("--region-start must not exceed --region-end")

    region = MHCRegion(chrom="6", start=args.region_start, end=args.region_end)
    registry = get_registry()
    engine = registry.get_sample_engine(sample_db)
    result = write_hibag_plink_input(
        engine,
        args.out_prefix,
        region=region,
        sample_name=args.sample_name,
    )

    if result.plink_prefix is None:
        print(
            f"No HLA-region SNPs found in the xMHC window "
            f"6:{region.start}-{region.end} ({result.n_total} variants scanned) — "
            f"no PLINK fileset written.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Wrote {result.n_emitted} SNP(s) to {result.plink_prefix}.bed/.bim/.fam "
        f"({result.n_emitted}/{result.n_total} variants emitted; "
        f"{result.n_dropped} dropped: off-region / indel / no-call / unresolved REF)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
