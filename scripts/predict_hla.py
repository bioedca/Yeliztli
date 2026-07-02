"""Predict + persist a sample's classical HLA alleles with HIBAG (Wave D).

End-to-end driver: prepares the sample's HLA-region PLINK input, runs HIBAG against
a BYO ancestry model, and stores the per-locus calls into the sample DB's
``hla_calls`` table (read by the Wave D SW-D2–D5 report layers).

    python scripts/predict_hla.py --sample-db sample.db --work-dir hla/ --ancestry European

Requires an operator-installed R + Bioconductor HIBAG runtime and a BYO
``{ancestry}-HLA4.RData`` model (never bundled). Rscript / model dir default to the
app settings (``hibag_rscript`` / ``hibag_model_dir``) and can be overridden. The
driver never crashes on a missing runtime/model — it reports ``unavailable`` and
persists nothing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.analysis.hibag_runner import KNOWN_ANCESTRIES
from backend.analysis.hla_persist import STATUS_OK, predict_and_persist_hla_calls
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
        "--work-dir",
        type=Path,
        required=True,
        help="Scratch directory for the PLINK input + HIBAG output (created if missing).",
    )
    parser.add_argument(
        "--ancestry",
        required=True,
        choices=KNOWN_ANCESTRIES,
        help="Ancestry of the BYO HIBAG model to use ({ancestry}-HLA4.RData).",
    )
    parser.add_argument(
        "--rscript",
        type=Path,
        default=None,
        help="Override Rscript path (default: settings.hibag_rscript / PATH).",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=None,
        help="Override HIBAG model directory (default: settings.hibag_model_dir).",
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

    registry = get_registry()
    settings = registry.settings
    rscript = args.rscript if args.rscript is not None else settings.hibag_rscript
    model_dir = args.model_dir if args.model_dir is not None else settings.hibag_model_dir

    engine = registry.get_sample_engine(sample_db)
    result = predict_and_persist_hla_calls(
        engine,
        args.work_dir,
        rscript=rscript,
        model_dir=model_dir,
        ancestry=args.ancestry,
        sample_name=args.sample_name,
    )

    print(
        f"status={result.status} ancestry={result.ancestry} "
        f"input_snps={result.n_input_snps} calls={result.n_calls} "
        f"persisted={result.n_persisted}"
    )
    if result.detail:
        print(result.detail, file=sys.stderr)
    return 0 if result.status == STATUS_OK else 1


if __name__ == "__main__":
    sys.exit(main())
