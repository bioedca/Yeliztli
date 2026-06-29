"""Prepare per-chromosome GRCh37 imputation-input VCFs from a sample DB (Wave C).

Reads a sample's ``annotated_variants`` and writes bgzipped, tabix-indexed,
coordinate-sorted input VCFs into ``--out-dir`` — the exact input
``scripts/run_imputation.py`` consumes as Beagle ``gt=``. Only reference-aligned
biallelic SNPs are emitted; indels, no-calls, multi-allelic and
unresolved-reference sites are dropped (see ``backend.analysis.imputation_input``).
Autosomes are produced by default. Chromosome X can be requested with ``--chrom X``
and requires ``--biological-sex XX|XY`` so PAR/non-PAR ploidy is encoded correctly.

    python scripts/prepare_imputation_input.py --sample-db sample.db --out-dir vcfs/
    python scripts/run_imputation.py --input-dir vcfs/

The sample DB must already be annotated (``annotated_variants`` populated).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.analysis.imputation_input import (
    DEFAULT_INPUT_CHROMOSOMES,
    INPUT_CHROMOSOMES,
    write_imputation_input_vcfs,
)
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
        "--out-dir",
        type=Path,
        required=True,
        help="Directory to write chr{N}.vcf.gz into (created if missing).",
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
    parser.add_argument(
        "--sample-name",
        default="SAMPLE",
        help="Name for the VCF sample column (default: SAMPLE).",
    )
    args = parser.parse_args(argv)

    sample_db: Path = args.sample_db
    if not sample_db.is_file():
        parser.error(f"sample DB not found: {sample_db}")

    chroms = tuple(args.chrom) if args.chrom else DEFAULT_INPUT_CHROMOSOMES
    if "X" in chroms and args.biological_sex is None:
        parser.error("--biological-sex XX|XY is required when --chrom X is requested")

    registry = get_registry()
    engine = registry.get_sample_engine(sample_db)
    result = write_imputation_input_vcfs(
        engine,
        args.out_dir,
        chromosomes=chroms,
        biological_sex=args.biological_sex,
        sample_name=args.sample_name,
    )

    for unit in result.units:
        n = result.per_chrom_emitted[unit.key]
        print(f"chr{unit.key}: {n} sites -> {unit.path.name}", flush=True)
    print(
        f"\nWrote {len(result.vcf_paths)} chromosome VCF(s) to {args.out_dir}; "
        f"{result.n_emitted}/{result.n_total} variants emitted "
        f"({result.n_dropped} dropped: out-of-scope chrom / indel / no-call / unresolved REF)."
    )
    if not result.vcf_paths:
        print("No imputation-input VCFs produced (no usable requested SNPs).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
