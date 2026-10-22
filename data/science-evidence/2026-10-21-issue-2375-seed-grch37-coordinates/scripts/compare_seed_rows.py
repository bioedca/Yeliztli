#!/usr/bin/env python3
"""Compare every coordinate-bearing seed row with its rsID's Ensembl GRCh37 primary mapping (#2375).

Writes derived/seed_row_comparison.json.gz: one entry per seed row with the seed locus, the
primary-chromosome mappings Ensembl returned (patch/alt contigs listed separately), and a class.
"""

from __future__ import annotations

import csv
import glob
import gzip
import json
from pathlib import Path

PACKET = Path(__file__).resolve().parents[1]
ROOT = PACKET.parents[2]
SEEDS = ("vep_seed", "clinvar_seed", "gnomad_seed", "gwas_seed")
PRIMARY = {str(i) for i in range(1, 23)} | {"X", "Y", "MT"}


def load_records() -> dict:
    records: dict = {}
    for path in sorted(glob.glob(str(PACKET / "raw" / "ensembl_grch37" / "batch_*.json.gz"))):
        with gzip.open(path, "rt") as fh:
            records.update(json.load(fh))
    return records


def main() -> None:
    records = load_records()
    out = []
    for name in SEEDS:
        with open(ROOT / "tests" / "fixtures" / "seed_csvs" / f"{name}.csv", newline="") as fh:
            for line, row in enumerate(csv.DictReader(fh), start=2):
                rsid = (row.get("rsid") or "").strip()
                chrom = (row.get("chrom") or "").strip()
                pos = (row.get("pos") or "").strip()
                rec = records.get(rsid)
                entry = {
                    "seed": name,
                    "line": line,
                    "rsid": rsid,
                    "seed_locus": f"{chrom}:{pos}" if chrom and pos else None,
                    "seed_ref": row.get("ref"),
                    "seed_alt": row.get("alt"),
                }
                if not (rsid.startswith("rs") and rsid[2:].isdigit()):
                    entry["class"] = "non_dbsnp_identifier"
                elif rec is None:
                    entry["class"] = "no_grch37_record"
                else:
                    maps = rec.get("mappings", [])
                    primary = [m for m in maps if m["seq_region_name"] in PRIMARY]
                    entry["record_name"] = rec.get("name")
                    entry["primary"] = [
                        {"locus": f"{m['seq_region_name']}:{m['start']}", "end": m["end"],
                         "allele_string": m["allele_string"], "strand": m["strand"]}
                        for m in primary
                    ]
                    entry["non_primary"] = [
                        f"{m['seq_region_name']}:{m['start']}" for m in maps if m not in primary
                    ]
                    loci = {p["locus"] for p in entry["primary"]}
                    if entry["seed_locus"] is None:
                        entry["class"] = "seed_has_no_locus"
                    elif not primary:
                        entry["class"] = "no_primary_mapping"
                    elif len(loci) > 1:
                        entry["class"] = "multiple_primary_mappings"
                    elif entry["seed_locus"] in loci:
                        entry["class"] = "match"
                    elif entry["seed_locus"].split(":")[0] == next(iter(loci)).split(":")[0]:
                        entry["class"] = "same_chrom_other_pos"
                    else:
                        entry["class"] = "other_chrom"
                out.append(entry)
    (PACKET / "derived").mkdir(exist_ok=True)
    with gzip.open(PACKET / "derived" / "seed_row_comparison.json.gz", "wt") as fh:
        fh.write(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
