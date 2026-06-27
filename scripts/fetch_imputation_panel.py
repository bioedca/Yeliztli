"""Fetch the 1000 Genomes Phase 3 v5a imputation reference panel (SW-C1).

The Wave C imputation runtime (SW-C2) imputes array genotypes against this phased
panel. The panel is open/public (1000 Genomes; no redistribution restriction) and
natively GRCh37, but it is ~8.5 GB across 23 per-chromosome ``bref3`` files, so it
is **not** fetched during default setup — run this once when you want to enable
imputation:

    python scripts/fetch_imputation_panel.py

It downloads each ``chr{N}.1kg.phase3.v5a.b37.bref3`` file + the PLINK genetic map
into ``settings.imputation_panel_dir`` and verifies every file against the SHA-256
recorded in ``bundles/manifest.json``. Already-present, valid files are skipped, so
re-running resumes a partial install. Restrict to specific chromosomes with
``--chrom`` (repeatable), e.g. ``--chrom 21 --chrom 22`` for a quick smoke test.

Heavy download → if you are provisioning this centrally, prefer the SLURM cluster
(see CLAUDE.md) and copy the verified directory into ``data_dir/imputation_panel``.
"""

from __future__ import annotations

import argparse
import sys

from backend.annotation.imputation_panel import (
    PANEL_CHROMOSOMES,
    PANEL_VERSION,
    fetch_panel,
    record_panel_version,
    validate_panel,
)
from backend.db.connection import get_registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--chrom",
        action="append",
        choices=PANEL_CHROMOSOMES,
        metavar="CHROM",
        help="Restrict to specific chromosome(s) (repeatable). Default: all.",
    )
    args = parser.parse_args(argv)
    chroms = tuple(args.chrom) if args.chrom else None

    registry = get_registry()
    dest = registry.settings.imputation_panel_dir

    def _progress(key: str, i: int, total: int) -> None:
        print(f"[{i}/{total}] {key}", flush=True)

    downloaded = fetch_panel(dest, chromosomes=chroms, progress=_progress)

    if not validate_panel(dest, chromosomes=chroms):
        print("ERROR: panel failed post-fetch validation.", file=sys.stderr)
        return 1

    # Record provenance only for a full-panel install (a partial --chrom run is a
    # smoke test, not a complete reference).
    if chroms is None:
        record_panel_version(registry.reference_engine, version=PANEL_VERSION)

    print(
        f"Imputation panel ready in {dest} "
        f"({len(downloaded)} file(s) downloaded; "
        f"{'all chromosomes' if chroms is None else 'chroms ' + ','.join(chroms)})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
