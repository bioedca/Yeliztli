"""Write the #2493 oracle and the GRCh37 template from the derived per-row records.

Usage: python -I materialize2493.py <derived json.gz> <template> <oracle tsv>
Refuses to write anything if a single row lacks a GRCh37 position or orientation.
Genotypes at loci reversed between builds are complemented onto the GRCh37 plus strand.
"""

import gzip
import json
import sys
from pathlib import Path

derived, template, oracle_path = (Path(a) for a in sys.argv[1:4])
records = {r["id"]: r for r in json.loads(gzip.decompress(derived.read_bytes()))}
missing = [r["id"] for r in records.values() if "grch37_position" not in r]
if missing:
    sys.exit(f"refusing: {len(missing)} rows have no GRCh37 position: {missing[:10]}")
unoriented = [r["id"] for r in records.values() if r.get("orientation") not in (1, -1)]
if unoriented:
    sys.exit(f"refusing: {len(unoriented)} rows have no orientation: {unoriented[:10]}")
COMPLEMENT = str.maketrans("ACGT", "TGCA")

BASIS = {
    ("both routes agree", "ensembl_grch37_variation"): "variation+map",
    ("both routes agree", "dbsnp_grch37"): "dbsnp+map",
    ("projection only", None): "map+flank",
    ("identity only: no single-base projection", "ensembl_grch37_variation"): "variation+flank",
    ("identity only: projection lands on another copy", "ensembl_grch37_variation"): "variation+flank",
}

lines = template.read_text(encoding="utf-8").splitlines()
header, body = [], []
for line in lines:
    (header if line.startswith("#") else body).append(line)

assert header[-1] == "# rsid\tchromosome\tposition\tgenotype", header[-1]
assert "GRCh37" not in "\n".join(header), "template already declares a build"
new_header = header[:-2] + [
    "#",
    "# Coordinates are GRCh37 (build 37), the repository's storage build, and calls",
    "# are on the GRCh37 plus strand. They were lifted from the PCA bundle's GRCh38",
    "# AIM positions; see tests/fixtures/synthetic_eur_aim_coordinates.tsv and #2493.",
] + header[-2:]

oracle_lines = [
    "# GRCh37 positions and strand for the synthetic EUR fixture's 5,000 AIMs (#2493).",
    "# Each GRCh38 coordinate is the PCA bundle's aim_positions_grch38. A GRCh37",
    "# position is recorded only where the two routes in `basis` agree:",
    "#   variation      Ensembl GRCh37 Variation placement of the rsID",
    "#   dbsnp          dbSNP GRCh37 placement (rsID absent from Ensembl)",
    "#   map            Ensembl's GRCh38->GRCh37 assembly-map projection",
    "#   flank          +/-25 bp of GRCh37 assembly sequence matching the GRCh38",
    "#                  flank, as-is or reverse-complemented (vendor i probes, and",
    "#                  regions restructured between builds)",
    "# `strand` is - where the locus is reversed between builds; `alleles_grch37` is",
    "# the AIM's GRCh38 allele pair on the GRCh37 plus strand, which every fixture",
    "# call must draw from.",
    "# Evidence: data/science-evidence/2026-10-20-issue-2493-fixture-grch37-positions/",
    "# id\tchromosome\tgrch38_position\tgrch37_position\tbasis\tstrand\talleles_grch37",
]
new_body = []
for line in body:
    rid, chrom, pos, genotype = line.split("\t")
    rec = records[rid]
    assert (rec["chromosome"], rec["grch38_position"]) == (chrom, int(pos)), rid
    pair = rec["grch37_alleles"]
    if rec["orientation"] == -1:
        genotype = genotype.translate(COMPLEMENT)
    assert set(genotype) <= set(pair), (rid, genotype, pair)
    new_body.append(f"{rid}\t{chrom}\t{rec['grch37_position']}\t{genotype}")
    basis = BASIS[(rec["verdict"], rec["identity_source"])]
    strand = "+" if rec["orientation"] == 1 else "-"
    oracle_lines.append(
        f"{rid}\t{chrom}\t{pos}\t{rec['grch37_position']}\t{basis}\t{strand}\t{'/'.join(pair)}"
    )

template.write_text("\n".join(new_header + new_body) + "\n", encoding="utf-8")
oracle_path.write_text("\n".join(oracle_lines) + "\n", encoding="utf-8")
print("wrote", len(new_body), "rows")
