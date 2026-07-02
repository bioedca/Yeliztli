#!/usr/bin/env python3
"""Build the prebuilt gnomAD allele-frequency bundle (``gnomad_af.db``).

A thin maintainer CLI over the retained build tooling in
``backend.annotation.gnomad``. It downloads the gnomAD r2.1.1 exomes sites VCF
(GRCh37 — matching the rest of the app) and loads the allele-frequency,
allele-number, and homozygous-count columns into an indexed SQLite database. The
resulting ``gnomad_af.db`` is uploaded as a GitHub Release asset and pinned in
``bundles/manifest.json`` (see docs/release-notes/gnomad-bundle-v1.1.0.md and
GNOMAD_BUNDLE_PLAN.md §4).

Only allele frequencies, observed allele counts, and homozygous counts are stored — no
SpliceAI / CADD / REVEL / SIFT / PolyPhen or any academic-license-restricted predictor
columns (those live in dbNSFP, which stays a pipeline build and is NOT redistributed).
gnomAD primary AF data is CC0, so redistributing this derived file is permitted.

Usage::

    # Download the r2.1.1 exomes VCF (~63 GB) and build the bundle (~2.85 GB):
    python scripts/build_gnomad_bundle.py --out gnomad_af.db --work-dir /tmp/gnomad

    # Build from an already-downloaded VCF (skip the heavy download):
    python scripts/build_gnomad_bundle.py --out gnomad_af.db --vcf /tmp/gnomad/gnomad.vcf.bgz

After building, capture the integrity values for the manifest + release notes::

    sha256sum gnomad_af.db
    stat -c %s gnomad_af.db
    gzip -c -9 gnomad_af.db > gnomad_af.db.gz
    sha256sum gnomad_af.db.gz    # -> 64-hex sha256 for bundles/manifest.json
    stat -c %s gnomad_af.db.gz   # -> size_bytes (integer)

Do NOT commit the ``.db`` or ``.db.gz`` to the repo — ship the compressed
``gnomad_af.db.gz`` as a release asset. The app installs it as ``gnomad_af.db``.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import sqlalchemy as sa

from backend.annotation.gnomad import (
    GNOMAD_VCF_URL,
    download_gnomad_vcf,
    load_gnomad_from_vcf,
)


def _compute_sha256(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file (streamed, constant memory)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build the prebuilt gnomAD allele-frequency bundle (gnomad_af.db).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("gnomad_af.db"),
        help="Output SQLite file path (default: gnomad_af.db).",
    )
    parser.add_argument(
        "--vcf",
        type=Path,
        default=None,
        help="Path to an already-downloaded gnomAD sites VCF (skips the download).",
    )
    parser.add_argument(
        "--url",
        default=GNOMAD_VCF_URL,
        help="gnomAD VCF download URL (default: r2.1.1 exomes sites, GRCh37).",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("."),
        help="Directory for the downloaded VCF when --vcf is not given.",
    )
    args = parser.parse_args(argv)

    print("=" * 60)
    print("Yeliztli gnomAD Bundle Builder")
    print(f"  Output:   {args.out}")
    if args.vcf:
        print(f"  VCF:      {args.vcf} (pre-downloaded)")
    else:
        print(f"  URL:      {args.url}")
        print(f"  Work dir: {args.work_dir}")
    print("=" * 60)
    print()

    if args.vcf:
        vcf_path = args.vcf
        if not vcf_path.exists():
            raise SystemExit(f"Error: --vcf file not found: {vcf_path}")
    else:
        print("Downloading gnomAD sites VCF (this is large — ~63 GB)...")
        vcf_path = download_gnomad_vcf(args.work_dir, url=args.url)

    print(f"Loading {vcf_path} into {args.out}...")
    engine = sa.create_engine(f"sqlite:///{args.out}")
    try:
        stats = load_gnomad_from_vcf(vcf_path, engine)
    finally:
        engine.dispose()

    file_size = args.out.stat().st_size
    sha256 = _compute_sha256(args.out)
    size_mb = file_size / (1024 * 1024)

    print()
    print(f"Built {args.out}")
    print(f"  Variants loaded:        {stats.variants_loaded:,}")
    print(f"  Skipped (no rsid):      {stats.skipped_no_rsid:,}")
    print(f"  Skipped (invalid chr):  {stats.skipped_invalid_chrom:,}")
    print(f"  Skipped (multiallelic): {stats.skipped_multiallelic:,}")
    print(f"  File size:              {size_mb:.1f} MB ({file_size} bytes)")
    print(f"  SHA-256:                {sha256}")
    print()
    print("Fill these into bundles/manifest.json -> bundles.gnomad:")
    print(f'  "sha256": "{sha256}",')
    print(f'  "size_bytes": {file_size},')


if __name__ == "__main__":
    main()
