"""Measure public HPO/MONDO releases with the production loader's scope join."""

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.annotation import mondo_hpo as loader  # noqa: E402


def verified_file(cache, source):
    path = cache / source["sha256"]
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != source["sha256"] or path.stat().st_size != source["bytes"]:
        raise ValueError(f"Input identity mismatch for {source['resource']} {source['version']}")
    return path


def measure(snapshot, paths, seed):
    with gzip.open(paths[snapshot["kg"]], "rt", encoding="utf-8") as stream:
        genes, _ = loader._parse_mondo_gene_disease_tsv_reader(stream)
    with paths[snapshot["hpo"]].open(encoding="utf-8") as stream:
        scopes = loader._parse_hpo_genes_to_phenotype_reader(stream)
    with paths[snapshot["mondo"]].open(encoding="utf-8") as stream:
        exact_map = loader._parse_mondo_sssom_reader(stream)

    def count(data, mapping):
        matched, unmatched = loader._count_scoped_hpo_matches(genes, data, mapping)
        return {"matched": matched, "unmatched": unmatched}

    # Use the same identifiers in each synthetic loss across releases.
    buckets = {
        key: int.from_bytes(hashlib.sha256(f"{seed}:{key}".encode()).digest()[:8], "big") % 100
        for key in exact_map
    }
    scenarios = {
        "intact": lambda key: True,
        "omit_omim": lambda key: not key.startswith("OMIM:"),
        "omit_orphanet": lambda key: not key.startswith("Orphanet:"),
        "half_orphanet": lambda key: not key.startswith("Orphanet:") or buckets[key] < 50,
        "retain_75pct": lambda key: buckets[key] < 75,
        "retain_90pct": lambda key: buckets[key] < 90,
    }
    by_namespace = {}
    for gene, disease_scopes in scopes.items():
        for disease, terms in disease_scopes.items():
            namespace = disease.partition(":")[0]
            by_namespace.setdefault(namespace, {}).setdefault(gene, {})[disease] = terms
    cases = {}
    for name, keep in scenarios.items():
        mapping = {key: value for key, value in exact_map.items() if keep(key)}
        total = count(scopes, mapping)
        breakdown = {key: count(data, mapping) for key, data in sorted(by_namespace.items())}
        for field in ("matched", "unmatched"):
            if sum(row[field] for row in breakdown.values()) != total[field]:
                raise ValueError("Namespace counts disagree with production join")
        cases[name] = {**total, "exact_xrefs": len(mapping), "namespaces": breakdown}
    return {"release": snapshot["release"], "cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path, help="public input files named by SHA-256")
    parser.add_argument(
        "--index", type=Path, default=Path(__file__).parent / "raw" / "sources.json"
    )
    args = parser.parse_args()
    index = json.loads(args.index.read_text(encoding="utf-8"))
    paths = {name: verified_file(args.cache, entry) for name, entry in index["files"].items()}
    rows = [measure(row, paths, index["synthetic_seed"]) for row in index["snapshots"]]
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
