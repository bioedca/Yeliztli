"""Rebuild the committed Ensembl GRCh37 gene-strand snapshot for the vep_seed guard.

``tests/fixtures/seed_csvs/vep_seed.csv`` carried a ``strand`` column that was
wrong for 19 of its 57 annotated genes, four of them internally contradictory --
the same gene stamped ``+`` on one row and ``-`` on another, which is impossible
(#2045). A gene's genomic strand is invariant, so it can be snapshotted once and
checked offline forever after.

Writes ``tests/fixtures/vep_gene_strand_snapshot.json``. Run it when
``vep_seed.csv`` gains a gene; the guard fails on an uncovered gene rather than
skipping it, so a new gene cannot slip through unchecked.
"""

from __future__ import annotations

import csv
import json
import sys
import time
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SEED = _ROOT / "tests" / "fixtures" / "seed_csvs" / "vep_seed.csv"
_OUT = _ROOT / "tests" / "fixtures" / "vep_gene_strand_snapshot.json"
_URL = "https://grch37.rest.ensembl.org/lookup/symbol/homo_sapiens/{gene}?content-type=application/json"


def _genes() -> list[str]:
    with _SEED.open(encoding="utf-8") as handle:
        return sorted(
            {row["gene_symbol"] for row in csv.DictReader(handle) if row.get("gene_symbol")}
        )


def _lookup(gene: str) -> dict | None:
    """Fetch one gene, retrying: the GRCh37 REST endpoint returns sporadic 5xx."""
    for attempt in range(4):
        try:
            with urllib.request.urlopen(_URL.format(gene=gene), timeout=30) as response:
                return json.load(response)
        except Exception:  # noqa: BLE001 - retried, then reported
            if attempt == 3:
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def main() -> int:
    strands: dict[str, dict[str, str]] = {}
    failures: list[str] = []
    genes = _genes()
    print(f"Resolving {len(genes)} genes cited by vep_seed.csv", file=sys.stderr)
    for gene in genes:
        payload = _lookup(gene)
        if payload is None:
            failures.append(gene)
            continue
        strands[gene] = {
            "strand": "+" if payload["strand"] == 1 else "-",
            "ensembl_gene_id": payload.get("id"),
        }
        time.sleep(0.12)

    if failures:
        # Fail closed: a partial snapshot would silently drop genes from the guard.
        print(f"ERROR: could not resolve {len(failures)} genes: {failures}", file=sys.stderr)
        return 1

    _OUT.write_text(
        json.dumps(
            {
                "_provenance": {
                    "source": "Ensembl GRCh37 REST (lookup/symbol)",
                    "assembly": "GRCh37",
                    "accessed": time.strftime("%Y-%m-%d"),
                    "gene_count": len(strands),
                    "fetch_failures": 0,
                    "generator": "scripts/build_vep_strand_snapshot.py",
                },
                "strands": dict(sorted(strands.items())),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {_OUT} ({len(strands)} genes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
