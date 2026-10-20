"""Fold the assembly-flank comparison into the #2493 derived records.

Usage: python -I flank2493.py <packet dir> <pca npz>
For each row with fetched flanks, +/-25 bp of GRCh37 sequence at the recorded
position is compared with the GRCh38 sequence at the fixture coordinate, as-is
and reverse-complemented (centre base excluded). Settles orientation where the
identity and projection routes did not both decide it, and records sequence
evidence for every single-route position.
"""

import gzip
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

D, npz = Path(sys.argv[1]), Path(sys.argv[2])
COMPLEMENT = str.maketrans("ACGTN", "TGCAN")


def revcomp(seq: str) -> str:
    return seq.translate(COMPLEMENT)[::-1]


def mismatches(a: str, b: str) -> int:
    return sum(1 for i, (x, y) in enumerate(zip(a, b, strict=True)) if i != 25 and x != y)


seq: dict[tuple[str, str], str] = {}
for line in gzip.decompress((D / "raw" / "ensembl_flanks" / "flanks.jsonl.gz").read_bytes()).decode().splitlines():
    rec = json.loads(line)
    assert rec["status"] == 200, rec["url"]
    seq[(rec["id"], rec["build"])] = json.loads(rec["body"])["seq"].upper()

pca = np.load(npz)
aim = {
    str(pca["aim_rsids_23andme"][i]): (str(pca["aim_a2"][i]), str(pca["aim_a1"][i]))
    for i in range(len(pca["aim_rsids_23andme"]))
}

path = D / "derived" / "grch37_positions.json.gz"
records = json.loads(gzip.decompress(path.read_bytes()))
tally = Counter()
for rec in records:
    rid = rec["id"]
    if (rid, "GRCh38") in seq:
        s38, s37 = seq[(rid, "GRCh38")], seq[(rid, "GRCh37")]
        same, rev = mismatches(s37, s38), mismatches(s37, revcomp(s38))
        flank = 1 if same <= 3 and rev > 10 else -1 if rev <= 3 and same > 10 else None
        assert flank is not None, (rid, same, rev)
        rec["flank_mismatches"] = {"same": same, "reverse_complement": rev}
        rec["orientation_flank"] = flank
        prior = rec["orientation"]
        assert prior in (None, flank), (rid, prior, flank)
        rec["orientation"] = flank
        rec["orientation_basis"] = (
            "flank" if prior is None else f"{rec['orientation_basis']}+flank"
        )
        rec["grch37_centre_base"] = s37[25]
    pair = aim[rid]
    if rec["orientation"] == -1:
        pair = tuple(a.translate(COMPLEMENT) for a in pair)
    rec["grch37_alleles"] = list(pair)
    if "grch37_centre_base" in rec:
        tally[("centre base in GRCh37 allele pair", rec["grch37_centre_base"] in pair)] += 1
    tally[(rec["orientation_basis"], rec["orientation"])] += 1

path.write_bytes(gzip.compress((json.dumps(records, indent=0) + "\n").encode(), mtime=0))
for key, n in sorted(tally.items(), key=lambda kv: -kv[1]):
    print(key, n)
