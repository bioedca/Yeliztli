#!/usr/bin/env python3
"""Fetch Ensembl GRCh37 Variation records for every rsID in the coordinate-bearing seeds (#2375).

Reads the rsIDs of vep_seed, clinvar_seed, gnomad_seed and gwas_seed, POSTs them to
https://grch37.rest.ensembl.org/variation/homo_sapiens in batches of 200, and writes each
response verbatim to raw/ensembl_grch37/batch_NNN.json (gzip -9 afterwards) plus the request list to
raw/ensembl_grch37/request_rsids.json.  Only public dbSNP identifiers are sent.
"""

from __future__ import annotations

import csv
import json
import sys
import time
import urllib.request
from pathlib import Path

PACKET = Path(__file__).resolve().parents[1]
ROOT = PACKET.parents[2]
SEEDS = ("vep_seed", "clinvar_seed", "gnomad_seed", "gwas_seed")
URL = "https://grch37.rest.ensembl.org/variation/homo_sapiens"
OUT = PACKET / "raw" / "ensembl_grch37"


def seed_rsids() -> list[str]:
    rsids: set[str] = set()
    for name in SEEDS:
        with open(ROOT / "tests" / "fixtures" / "seed_csvs" / f"{name}.csv", newline="") as fh:
            for row in csv.DictReader(fh):
                rsid = (row.get("rsid") or "").strip()
                if rsid.startswith("rs") and rsid[2:].isdigit():
                    rsids.add(rsid)
    return sorted(rsids, key=lambda r: int(r[2:]))


def main() -> int:
    rsids = seed_rsids()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "request_rsids.json").write_text(json.dumps(rsids, indent=1) + "\n")
    for i in range(0, len(rsids), 200):
        body = json.dumps({"ids": rsids[i : i + 200]}).encode()
        req = urllib.request.Request(
            URL,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = resp.read()
        (OUT / f"batch_{i // 200:03d}.json").write_bytes(payload)
        time.sleep(1)
    print(f"requested {len(rsids)} rsIDs", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
