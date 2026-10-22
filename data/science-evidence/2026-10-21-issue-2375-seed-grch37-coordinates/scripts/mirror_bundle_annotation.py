#!/usr/bin/env python3
"""Mirror the pinned bundle's annotation onto the moved vep_seed rows (#2375).

Runs #2562's ``apply_bundle`` from scripts/build_vep_seed_bundle_snapshot.py, unchanged,
against the committed snapshot (tests/fixtures/vep_seed_bundle_snapshot.json), whose
provenance pins the v4.0.0 bundle by sha256.  The local bundle on the machine this was run
on has a different sha256, so the generator rightly refused it; the snapshot is that pinned
bundle's rows for every annotated rsID, so mirroring from it writes what --apply would.
"""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

PACKET = Path(__file__).resolve().parents[1]
ROOT = PACKET.parents[2]
SEED = ROOT / "tests" / "fixtures" / "seed_csvs" / "vep_seed.csv"
SNAPSHOT = ROOT / "tests" / "fixtures" / "vep_seed_bundle_snapshot.json"


def _generator():
    spec = importlib.util.spec_from_file_location(
        "build_vep_seed_bundle_snapshot", ROOT / "scripts" / "build_vep_seed_bundle_snapshot.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    generator = _generator()
    with open(SEED, newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames
        rows = list(reader)
    changed = generator.apply_bundle(rows, json.loads(SNAPSHOT.read_text())["rows"])
    with open(SEED, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (PACKET / "derived" / "mirrored_rsids.json").write_text(json.dumps(sorted(set(changed)), indent=1) + "\n")
    print(f"mirrored {len(changed)} rows")


if __name__ == "__main__":
    main()
