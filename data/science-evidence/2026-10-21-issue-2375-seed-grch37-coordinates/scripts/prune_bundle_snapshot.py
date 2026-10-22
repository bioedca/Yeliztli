#!/usr/bin/env python3
"""Drop bundle-snapshot entries for rsIDs that are no longer annotated in vep_seed (#2375).

Mirroring the pinned bundle onto the moved rows left rs10811661 and rs1111875 intergenic
(the bundle's own annotation at their GRCh37 loci), so they leave the snapshot's scope,
"every bundle row for each rsID with a gene_symbol in vep_seed.csv".  Entries are only
removed, never edited, so the snapshot still describes the pinned bundle it names.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SEED = ROOT / "tests" / "fixtures" / "seed_csvs" / "vep_seed.csv"
SNAPSHOT = ROOT / "tests" / "fixtures" / "vep_seed_bundle_snapshot.json"


def main() -> None:
    with open(SEED, newline="") as fh:
        annotated = {row["rsid"] for row in csv.DictReader(fh) if row["gene_symbol"]}
    snapshot = json.loads(SNAPSHOT.read_text())
    gone = sorted(set(snapshot["rows"]) - annotated)
    missing = sorted(annotated - set(snapshot["rows"]))
    if missing:
        raise SystemExit(f"annotated rsIDs the snapshot lacks (needs the bundle): {missing}")
    for rsid in gone:
        del snapshot["rows"][rsid]
    SNAPSHOT.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    print(f"removed {gone}")


if __name__ == "__main__":
    main()
