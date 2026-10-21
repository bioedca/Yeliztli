#!/usr/bin/env python3
"""Fetch GRCh37 assembly sequence at each mismatched seed rsID's Ensembl primary mapping (#2375).

For every rsID whose seed row is not at its GRCh37 locus (derived/seed_row_comparison.json.gz),
POSTs the mapped region to https://grch37.rest.ensembl.org/sequence/region/human in batches
of 50 and writes the responses verbatim (gzip) to raw/ensembl_sequence/batch_NNN.json.gz.
This route reads assembly sequence only, never a variant record.
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.request
from pathlib import Path

PACKET = Path(__file__).resolve().parents[1]
URL = "https://grch37.rest.ensembl.org/sequence/region/human"
OUT = PACKET / "raw" / "ensembl_sequence"
OUT_OF_SCOPE = {"rs12345"}  # #2345's synthetic GENE1 row


def regions() -> list[str]:
    with gzip.open(PACKET / "derived" / "seed_row_comparison.json.gz", "rt") as fh:
        rows = json.load(fh)
    wanted: dict[str, str] = {}
    for row in rows:
        if row["class"] in {"same_chrom_other_pos", "other_chrom"} and row["rsid"] not in OUT_OF_SCOPE:
            (mapping,) = row["primary"]
            chrom, start = mapping["locus"].split(":")
            wanted[row["rsid"]] = f"{chrom}:{start}..{mapping['end']}:1"
    return sorted(set(wanted.values()))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    todo = regions()
    (OUT / "request_regions.json").write_text(json.dumps(todo, indent=1) + "\n")
    for i in range(0, len(todo), 50):
        body = json.dumps({"regions": todo[i : i + 50], "coord_system_version": "GRCh37"}).encode()
        req = urllib.request.Request(
            URL, data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = resp.read()
        with gzip.open(OUT / f"batch_{i // 50:03d}.json.gz", "wb") as fh:
            fh.write(payload)
        time.sleep(1)


if __name__ == "__main__":
    main()
