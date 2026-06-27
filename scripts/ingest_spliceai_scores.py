"""Ingest a BYO SpliceAI precomputed-scores VCF into a standalone ``spliceai.db``.

SpliceAI precomputed scores are Illumina **non-commercial** data distributed
behind a BaseSpace login (https://basespace.illumina.com/s/otSPW8hnhaZR), so this
app never bundles or auto-downloads them (owner posture (A) — see
``docs/external-inputs-strategy.md``). Obtain the file yourself, then run this
once against a configured install:

    python scripts/ingest_spliceai_scores.py \
        ~/Downloads/spliceai_scores.raw.snv.hg19.vcf.gz \
        --version 1.3

You may pass several VCFs (e.g. SNVs + indels) — the DB is cleared once, then all
files are appended:

    python scripts/ingest_spliceai_scores.py snv.hg19.vcf.gz indel.hg19.vcf.gz

Use the **hg19 (GRCh37)** files — they match the app's coordinate system; the
hg38 files would need a liftover this path does not perform. The version is
recorded in reference.db so Database Stats / the Update Manager surface a row.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import structlog

from backend.annotation.spliceai import (
    DEFAULT_MIN_DS,
    SPLICEAI_VERSION,
    ingest_spliceai_vcf,
    record_spliceai_version,
)
from backend.db.connection import get_registry

logger = structlog.get_logger(__name__)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "vcf",
        nargs="+",
        type=Path,
        help="SpliceAI precomputed-scores VCF(s) (.vcf or .vcf.gz), GRCh37/hg19.",
    )
    parser.add_argument(
        "--min-ds",
        type=float,
        default=DEFAULT_MIN_DS,
        help=(
            f"Only store rows with max delta score >= this (default {DEFAULT_MIN_DS}, "
            "the lowest published operating point). Pass 0 to keep every scored row."
        ),
    )
    parser.add_argument(
        "--version",
        default=SPLICEAI_VERSION,
        help=f"Version label recorded in database_versions (default {SPLICEAI_VERSION}).",
    )
    args = parser.parse_args(argv)

    for p in args.vcf:
        if not p.exists():
            parser.error(f"file not found: {p}")

    registry = get_registry()
    db_path = registry.settings.spliceai_db_path
    engine = registry.spliceai_engine

    total = 0
    for i, p in enumerate(args.vcf):
        logger.info("spliceai_ingest_start", file=str(p), min_ds=args.min_ds)
        stats = ingest_spliceai_vcf(p, engine, min_ds=args.min_ds, clear_existing=(i == 0))
        total += stats.loaded
        logger.info(
            "spliceai_ingest_file_done",
            file=str(p),
            loaded=stats.loaded,
            skipped_below_threshold=stats.skipped_below_threshold,
            skipped_bad_row=stats.skipped_bad_row,
        )

    # Record the version in reference.db (visible to the Update Manager / Stats).
    last = args.vcf[-1]
    record_spliceai_version(
        registry.reference_engine,
        version=args.version,
        file_path=str(last),
        file_size_bytes=last.stat().st_size,
        checksum=_sha256(last),
    )

    logger.info(
        "spliceai_ingest_complete",
        db_path=str(db_path),
        rows=total,
        files=len(args.vcf),
        version=args.version,
    )
    print(f"SpliceAI ingest complete: {total} rows → {db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
