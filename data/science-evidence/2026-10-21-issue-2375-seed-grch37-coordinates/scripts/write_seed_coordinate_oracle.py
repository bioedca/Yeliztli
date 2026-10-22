#!/usr/bin/env python3
"""Write tests/fixtures/seed_rsid_grch37_coordinates.json from this packet's raw payloads (#2375).

One entry per seed rsID with a GRCh37 record: its single primary-chromosome mapping (chrom,
start, end, allele_string).  Tests read it offline; regenerate it only from a re-fetched
packet.
"""

from __future__ import annotations

import glob
import gzip
import json
from pathlib import Path

PACKET = Path(__file__).resolve().parents[1]
ROOT = PACKET.parents[2]
ORACLE = ROOT / "tests" / "fixtures" / "seed_rsid_grch37_coordinates.json"
PRIMARY = {str(i) for i in range(1, 23)} | {"X", "Y", "MT"}


def main() -> None:
    records: dict = {}
    for path in sorted(glob.glob(str(PACKET / "raw" / "ensembl_grch37" / "batch_*.json.gz"))):
        with gzip.open(path, "rt") as fh:
            records.update(json.load(fh))
    requested = json.loads((PACKET / "raw" / "ensembl_grch37" / "request_rsids.json").read_text())
    rsids: dict[str, dict] = {}
    for rsid in requested:
        record = records.get(rsid)
        if record is None:
            continue
        primary = [m for m in record["mappings"] if m["seq_region_name"] in PRIMARY]
        if len(primary) != 1:
            raise SystemExit(f"{rsid}: expected one primary mapping, got {len(primary)}")
        (m,) = primary
        rsids[rsid] = {
            "chrom": m["seq_region_name"],
            "start": m["start"],
            "end": m["end"],
            "allele_string": m["allele_string"],
        }
    oracle = {
        "_provenance": {
            "source": "Ensembl GRCh37 REST POST /variation/homo_sapiens",
            "assembly": "GRCh37",
            "accessed": "2026-10-21",
            "evidence_packet": str(PACKET.relative_to(ROOT)),
            "generator": str(Path(__file__).relative_to(ROOT)),
            "scope": "every dbSNP-form rsID in vep_seed, clinvar_seed, gnomad_seed and gwas_seed",
            "no_grch37_record": sorted(set(requested) - set(rsids)),
            "note": "Committed offline oracle for the seed coordinate guard; tests never fetch.",
        },
        "rsids": dict(sorted(rsids.items(), key=lambda kv: int(kv[0][2:]))),
    }
    ORACLE.write_text(json.dumps(oracle, indent=1) + "\n")
    print(f"{len(rsids)} rsIDs written")


if __name__ == "__main__":
    main()
