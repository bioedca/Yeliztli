"""Decide each synthetic EUR row's GRCh38->GRCh37 strand orientation (#2493).

Usage: python -I orient2493.py <packet dir> <pca npz> <derived json.gz>
Adds "orientation" (+1 same, -1 reversed) and its basis to every derived record.

  identity   the rsID's Ensembl allele strings in the two builds: GRCh37 equal to
             GRCh38 is the same orientation, equal to its complement is reversed
             (order kept, then as sets). Without a GRCh38 record, a non-palindromic
             AIM pair (PCA bundle) is tested against the GRCh37 allele set; a
             palindromic pair (A/T, C/G) cannot be decided that way.
  projection the strand of Ensembl's assembly-map projection of the GRCh38 base.
"""

import gzip
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

D, npz, derived = (Path(a) for a in sys.argv[1:4])
PRIMARY = {str(i) for i in range(1, 23)} | {"X", "Y", "MT"}
COMPLEMENT = str.maketrans("ACGT", "TGCA")

def build_alleles(sub: str, assembly: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in sorted((D / "raw" / sub).glob("variation_batch_*.json.gz")):
        for key, rec in json.loads(gzip.decompress(f.read_bytes())).items():
            maps = [
                m
                for m in rec.get("mappings", [])
                if m.get("assembly_name") == assembly and m.get("seq_region_name") in PRIMARY
            ]
            if len(maps) == 1:
                out[key] = maps[0]["allele_string"].split("/")
    return out


grch37_alleles = build_alleles("ensembl_grch37", "GRCh37")
grch38_alleles = build_alleles("ensembl_grch38", "GRCh38")

projection_strand: dict[str, int] = {}
for f in sorted((D / "raw" / "ensembl_map").glob("map_batch_*.jsonl.gz")):
    for line in gzip.decompress(f.read_bytes()).decode().splitlines():
        rec = json.loads(line)
        strands = {
            int(m["mapped"]["strand"])
            for m in json.loads(rec["body"])["mappings"]
            if m["mapped"]["assembly"] == "GRCh37"
            and m["mapped"]["start"] == m["mapped"]["end"]
            and m["mapped"]["seq_region_name"] in PRIMARY
        }
        if len(strands) == 1:
            projection_strand[rec["id"]] = strands.pop()

pca = np.load(npz)
aim = {
    str(pca["aim_rsids_23andme"][i]): (str(pca["aim_a2"][i]), str(pca["aim_a1"][i]))
    for i in range(len(pca["aim_rsids_23andme"]))
}


def _decide(same: bool, reversed_: bool) -> int | None:
    if same and not reversed_:
        return 1
    if reversed_ and not same:
        return -1
    return None


def identity_orientation(rid: str) -> int | None:
    l37 = grch37_alleles.get(rid)
    if l37 is None:
        return None
    l38 = grch38_alleles.get(rid)
    if l38 is not None:
        c38 = [a.translate(COMPLEMENT) for a in l38]
        decided = _decide(l37 == l38, l37 == c38)
        if decided is None:
            decided = _decide(set(l37) == set(l38), set(l37) == set(c38))
        if decided is not None:
            return decided
    ref38, alt38 = aim[rid]
    pair = {ref38, alt38}
    flipped = {a.translate(COMPLEMENT) for a in pair}
    if pair == flipped:
        return 0  # palindromic: the allele set cannot tell the orientations apart
    decided = _decide(pair <= set(l37), flipped <= set(l37))
    return 0 if decided is None else decided


records = json.loads(gzip.decompress(derived.read_bytes()))
tally = Counter()
for rec in records:
    rid = rec["id"]
    ident = identity_orientation(rid)
    # The projection's strand is only meaningful where the projection is the
    # position that was kept (both routes agree, or projection only).
    proj = projection_strand.get(rid) if rec["verdict"] in ("both routes agree", "projection only") else None
    if ident in (1, -1) and proj in (1, -1):
        rec["orientation"] = ident if ident == proj else None
        rec["orientation_basis"] = "identity+projection" if ident == proj else "conflict"
    elif ident in (1, -1):
        rec["orientation"], rec["orientation_basis"] = ident, "identity"
    elif proj in (1, -1):
        rec["orientation"], rec["orientation_basis"] = proj, "projection"
    else:
        rec["orientation"], rec["orientation_basis"] = None, "undecided"
    rec["orientation_identity"], rec["orientation_projection"] = ident, proj
    tally[(rec["orientation_basis"], rec["orientation"])] += 1

derived.write_bytes(gzip.compress((json.dumps(records, indent=0) + "\n").encode(), mtime=0))
for key, n in sorted(tally.items(), key=lambda kv: -kv[1]):
    print(key, n)
for rec in records:
    if rec["orientation"] != 1:
        print(rec["id"], rec["verdict"], rec["orientation_basis"], rec["orientation"],
              "ident", rec["orientation_identity"], "proj", rec["orientation_projection"],
              "aim", aim[rec["id"]], "g37", grch37_alleles.get(rec["id"]), "g38", grch38_alleles.get(rec["id"]))
