#!/usr/bin/env python3
"""Check each corrected GRCh37 locus against assembly sequence (#2375).

For every mismatched seed rsID, the GRCh37 assembly sequence at its Ensembl primary mapping
(raw/ensembl_sequence/) must equal the reference allele of Ensembl's allele string, i.e. the
first allele.  An insertion (start > end, reference "-") has no reference base to compare
and is recorded as "insertion".  Writes derived/reference_base_check.json.
"""

from __future__ import annotations

import glob
import gzip
import json
from pathlib import Path

PACKET = Path(__file__).resolve().parents[1]
OUT_OF_SCOPE = {"rs12345"}


def main() -> None:
    with gzip.open(PACKET / "derived" / "seed_row_comparison.json.gz", "rt") as fh:
        rows = json.load(fh)
    seq: dict[str, str] = {}
    for path in sorted(glob.glob(str(PACKET / "raw" / "ensembl_sequence" / "batch_*.json.gz"))):
        with gzip.open(path, "rt") as fh:
            for item in json.load(fh):
                seq[item["query"]] = item["seq"].upper()
    result: dict[str, dict] = {}
    for row in rows:
        rsid = row["rsid"]
        if row["class"] not in {"same_chrom_other_pos", "other_chrom"} or rsid in OUT_OF_SCOPE:
            continue
        (mapping,) = row["primary"]
        chrom, start = mapping["locus"].split(":")
        region = f"{chrom}:{start}..{mapping['end']}:1"
        alleles = mapping["allele_string"].split("/")
        reference = alleles[0]
        assembly = seq.get(region, "")
        if reference == "-":
            verdict = "insertion"
        elif assembly == reference:
            verdict = "reference_matches_assembly"
        else:
            verdict = "MISMATCH"
        result[rsid] = {
            "locus": mapping["locus"],
            "allele_string": mapping["allele_string"],
            "assembly_sequence": assembly,
            "verdict": verdict,
        }
    (PACKET / "derived" / "reference_base_check.json").write_text(
        json.dumps(dict(sorted(result.items())), indent=1) + "\n"
    )


if __name__ == "__main__":
    main()
