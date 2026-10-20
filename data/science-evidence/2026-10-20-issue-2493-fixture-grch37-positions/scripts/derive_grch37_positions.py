"""Derive GRCh37 positions for the synthetic EUR fixture from the #2493 evidence packet.

Usage: python -I derive2493.py <packet dir> <template> <pca npz> <vep bundle> <out json>

Two routes per row, both required to agree:
  identity  — the rsID's own GRCh37 placement (Ensembl GRCh37 Variation; dbSNP
              GRCh37 for the five rsIDs Ensembl does not carry). Vendor i-IDs
              have no public identity record.
  projection — the row's current GRCh38 coordinate projected to GRCh37 by
              Ensembl's assembly map, which never consults a variant record.
"""

import gzip
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import numpy as np

D, template, npz, bundle, out = (Path(a) for a in sys.argv[1:6])
PRIMARY = {str(i) for i in range(1, 23)} | {"X", "Y", "MT"}

rows = [
    line.split("\t")
    for line in template.read_text().splitlines()
    if line and not line.startswith("#")
]

identity: dict[str, set[tuple[str, int]]] = {}
for f in sorted((D / "raw" / "ensembl_grch37").glob("variation_batch_*.json.gz")):
    for key, rec in json.loads(gzip.decompress(f.read_bytes())).items():
        identity[key] = {
            (m["seq_region_name"], int(m["start"]))
            for m in rec.get("mappings", [])
            if m.get("assembly_name") == "GRCh37" and m.get("seq_region_name") in PRIMARY
        }
identity_source = dict.fromkeys(identity, "ensembl_grch37_variation")

dbsnp = json.loads((D / "raw" / "dbsnp" / "esummary_ensembl_unknown_5.json").read_text())["result"]
for uid in dbsnp["uids"]:
    chrom, _, pos = dbsnp[uid]["chrpos_prev_assm"].partition(":")
    identity[f"rs{uid}"] = {(chrom, int(pos))}
    identity_source[f"rs{uid}"] = "dbsnp_grch37"

projection: dict[str, set[tuple[str, int]]] = {}
for f in sorted((D / "raw" / "ensembl_map").glob("map_batch_*.jsonl.gz")):
    for line in gzip.decompress(f.read_bytes()).decode().splitlines():
        rec = json.loads(line)
        assert rec["status"] == 200, rec
        loci = set()
        for m in json.loads(rec["body"])["mappings"]:
            mapped = m["mapped"]
            if (
                mapped["assembly"] == "GRCh37"
                and mapped["start"] == mapped["end"]
                and mapped["seq_region_name"] in PRIMARY
            ):
                loci.add((mapped["seq_region_name"], int(mapped["start"])))
        projection[rec["id"]] = loci

pca = np.load(npz)
aim = {
    str(pca["aim_rsids_23andme"][i]): (
        str(pca["aim_chroms"][i]),
        int(pca["aim_positions_grch38"][i]),
        str(pca["aim_a2"][i]),
        str(pca["aim_a1"][i]),
    )
    for i in range(len(pca["aim_rsids_23andme"]))
}

con = sqlite3.connect(f"file:{bundle}?mode=ro", uri=True)


def bundle_alleles_at(chrom: str, pos: int) -> set[str]:
    found = set()
    for rsid, ref, alt in con.execute(
        "SELECT DISTINCT rsid, ref, alt FROM vep_annotations WHERE chrom = ? AND pos = ?",
        (chrom, pos),
    ):
        found.add(f"{rsid}:{ref}>{alt}")
    return found


records = []
verdicts = Counter()
for rid, chrom, pos, _genotype in rows:
    pos = int(pos)
    assert aim[rid][:2] == (chrom, pos), rid
    proj = projection.get(rid, set())
    ident = identity.get(rid) if rid.startswith("rs") else None
    rec = {
        "id": rid,
        "chromosome": chrom,
        "grch38_position": pos,
        "projection": sorted(proj),
        "identity": sorted(ident) if ident is not None else None,
        "identity_source": identity_source.get(rid),
    }
    if ident is not None and len(ident) == 1 and proj != ident:
        # Regions restructured between builds: the assembly map has no 1:1
        # alignment, or aligns the GRCh38 base to another copy. The fixture is
        # labelled by rsID, so it takes the rsID's own GRCh37 placement.
        (_c, ip), = ident
        rec["verdict"] = (
            "identity only: no single-base projection"
            if len(proj) != 1
            else "identity only: projection lands on another copy"
        )
        rec["grch37_position"] = ip
    elif len(proj) != 1:
        rec["verdict"] = "withheld: projection is not a single GRCh37 base"
    elif ident is None:
        (pc, pp), = proj
        alleles = bundle_alleles_at(pc, pp)
        rec["bundle_alleles_at_projection"] = sorted(alleles)
        rec["aim_alleles"] = [aim[rid][2], aim[rid][3]]
        rec["verdict"] = "projection only"
        rec["grch37_position"] = pp
    elif len(ident) != 1:
        rec["verdict"] = "withheld: identity route is not a single GRCh37 locus"
    elif ident != proj:
        rec["verdict"] = "withheld: routes disagree"
    else:
        (_c, pp), = proj
        rec["verdict"] = "both routes agree"
        rec["grch37_position"] = pp
    verdicts[rec["verdict"]] += 1
    records.append(rec)

out.write_text(json.dumps(records, indent=0) + "\n")
print(dict(verdicts))
print("identity sources:", Counter(r["identity_source"] for r in records))
print("chromosome changes:", sum(1 for r in records if r.get("projection") and r["projection"][0][0] != r["chromosome"]))
print("moved:", sum(1 for r in records if r.get("grch37_position") not in (None, r["grch38_position"])))
for r in records:
    if r["verdict"] != "both routes agree":
        print(r)
