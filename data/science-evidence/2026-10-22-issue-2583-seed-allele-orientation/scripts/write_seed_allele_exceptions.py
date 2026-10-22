"""Write the tracked exception list for the seed allele guard (#2583).

Imports the guard's own classifier so the list is exactly the set of rows the guard
rejects today. Each entry names the issue that owns its correction: rs6025's Factor V
Leiden rows are #2541, rs1800795's are #2563, and the CYP2D6/ABCB1 rows are #2591.
#2590 corrected every unannotated filler row.

Usage (from the repository root): python -I <this script> <repo-root>
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

TRACKED_ELSEWHERE = {
    "rs6025": "#2541",
    "rs1800795": "#2563",
    # Transcript-strand pharmacogenomic rows that need annotation and frequency
    # re-sourcing, split out of #2590.
    "rs1065852": "#2591",
    "rs2032582": "#2591",
}


def main() -> None:
    repo = Path(sys.argv[1]).resolve()
    spec = importlib.util.spec_from_file_location(
        "seed_allele_guard", repo / "tests" / "backend" / "test_seed_grch37_alleles.py"
    )
    assert spec is not None and spec.loader is not None
    guard = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = guard
    spec.loader.exec_module(guard)

    rows = [
        {
            "seed": seed,
            "rsid": rsid,
            "ref": ref,
            "alt": alt,
            "violation": violation,
            "tracked_by": TRACKED_ELSEWHERE.get(rsid, "#2583"),
        }
        for seed, rsid, ref, alt, violation in guard._violations()
    ]
    document = {
        "_provenance": {
            "origin": "#2583",
            "oracle": "tests/fixtures/seed_rsid_grch37_coordinates.json",
            "evidence_packet": "data/science-evidence/2026-10-22-issue-2583-seed-allele-orientation",
            "generator": (
                "data/science-evidence/2026-10-22-issue-2583-seed-allele-orientation/"
                "scripts/write_seed_allele_exceptions.py"
            ),
            "note": (
                "Rows that break the plus-strand, reference-first GRCh37 allele contract "
                "today. The guard requires this list to match exactly, so it can only "
                "shrink as rows are corrected."
            ),
        },
        "rows": rows,
    }
    out = repo / "tests" / "fixtures" / "seed_allele_known_exceptions.json"
    out.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["violation"]] = counts.get(row["violation"], 0) + 1
    print(len(rows), counts)


if __name__ == "__main__":
    main()
