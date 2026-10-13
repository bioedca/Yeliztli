"""Derive the gene-name facts this packet relies on from an extracted GTEx v8 eQTL tar.

Usage: python derive_gene_name_summary.py <extracted GTEx_Analysis_v8_eQTL dir>
Prints JSON. Reads only the open-access per-tissue *.v8.egenes.txt.gz and
*.v8.signif_variant_gene_pairs.txt.gz files.
"""

import gzip
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
names: dict[str, set[str]] = {}
egenes = sorted(root.glob("*.v8.egenes.txt.gz"))
for path in egenes:
    with gzip.open(path, "rt") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        i_id, i_name = header.index("gene_id"), header.index("gene_name")
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            names.setdefault(parts[i_id], set()).add(parts[i_name])
signif_genes: set[str] = set()
signif = sorted(root.glob("*.v8.signif_variant_gene_pairs.txt.gz"))
for path in signif:
    with gzip.open(path, "rt") as fh:
        i_gene = fh.readline().rstrip("\n").split("\t").index("gene_id")
        signif_genes.update(line.split("\t")[i_gene] for line in fh)
placeholders = sum(
    1
    for gid, ns in names.items()
    for n in ns
    if n in {".", "NA", ""} or n == re.sub(r"\.\d+$", "", gid)
)
print(
    json.dumps(
        {
            "egenes_files": len(egenes),
            "signif_pairs_files": len(signif),
            "egenes_columns_include": ["gene_id", "gene_name"],
            "distinct_gene_ids": len(names),
            "gene_ids_with_conflicting_names": sum(1 for ns in names.values() if len(ns) > 1),
            "placeholder_names": placeholders,
            "signif_pair_gene_ids": len(signif_genes),
            "signif_pair_gene_ids_without_name": len(signif_genes - names.keys()),
            "example": {"ENSG00000177000.10": sorted(names.get("ENSG00000177000.10", []))},
        },
        indent=2,
    )
)
