#!/usr/bin/env python3
"""Build the offline PMID → metadata snapshot for the gwas_seed provenance guard (#1948).

Generates ``tests/fixtures/gwas_seed_pmid_snapshot.json``: a committed map of every
PMID cited by ``tests/fixtures/seed_csvs/gwas_seed.csv`` → ``{author, year, title}``.
``tests/backend/test_gwas_seed_provenance.py`` reads it to check each row's ``study``
label against the paper it cites **without touching the network at test time**.

Deliberately NOT folded into ``build_pmid_metadata_snapshot.py``: that snapshot covers
panel JSON citations and its guard *skips* un-snapshotted PMIDs, which let it rot
undetected (#1983). This guard fails loudly on an uncovered PMID instead, so the two
must not share a coverage model.

Usage::

    PYTHONPATH=. python scripts/build_gwas_seed_pmid_snapshot.py [--accessed YYYY-MM-DD]

Source: NCBI E-utilities ``esummary`` (db=pubmed). Batched; no API key required.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SEED = _REPO / "tests" / "fixtures" / "seed_csvs" / "gwas_seed.csv"
_SNAPSHOT = _REPO / "tests" / "fixtures" / "gwas_seed_pmid_snapshot.json"
_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
_BATCH = 200


def _cited_pmids() -> list[str]:
    with open(_SEED, encoding="utf-8") as f:
        pmids = {row["pubmed_id"].strip() for row in csv.DictReader(f) if row["pubmed_id"].strip()}
    return sorted(pmids, key=int)


def _fetch(pmids: list[str]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for start in range(0, len(pmids), _BATCH):
        batch = pmids[start : start + _BATCH]
        url = f"{_ESUMMARY}?db=pubmed&id={','.join(batch)}&retmode=json"
        with urllib.request.urlopen(url) as resp:  # noqa: S310 - fixed NCBI endpoint
            payload = json.load(resp)["result"]
        for uid in payload.get("uids", []):
            rec = payload[uid]
            out[uid] = {
                "author": rec.get("sortfirstauthor", ""),
                "year": (rec.get("pubdate", "") or "")[:4],
                "title": rec.get("title", ""),
            }
        print(f"  fetched {min(start + _BATCH, len(pmids))}/{len(pmids)}", file=sys.stderr)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accessed", required=True, help="regen date, YYYY-MM-DD")
    opts = parser.parse_args()

    pmids = _cited_pmids()
    print(f"Collected {len(pmids)} PMIDs cited by gwas_seed.csv", file=sys.stderr)
    metadata = _fetch(pmids)

    unresolved = sorted(set(pmids) - set(metadata), key=int)
    if unresolved:
        # Refuse to write rather than emit a snapshot known to be bad. A PMID that does
        # not resolve cites nothing real, so the seed row is the thing to fix — writing
        # the file anyway would just hand the guard a fixture that cannot pass.
        print(
            f"ERROR: {len(unresolved)} cited PMID(s) did not resolve on PubMed: {unresolved}\n"
            f"       Fix the citing row(s) in {_SEED.relative_to(_REPO)}; snapshot NOT written.",
            file=sys.stderr,
        )
        return 1

    snapshot = {
        "_provenance": {
            "source": "NCBI E-utilities esummary (db=pubmed)",
            "accessed": opts.accessed,
            "pmid_count": len(metadata),
            "generator": "scripts/build_gwas_seed_pmid_snapshot.py",
            "note": (
                "Committed offline reference for the gwas_seed provenance guard (#1948). "
                "Regenerate whenever gwas_seed.csv gains or changes a pubmed_id — the guard "
                "FAILS on an uncovered PMID rather than skipping it."
            ),
            "unresolved_pmids": unresolved,
        },
        "pmids": {p: metadata[p] for p in sorted(metadata, key=int)},
    }
    _SNAPSHOT.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(metadata)} entries → {_SNAPSHOT.relative_to(_REPO)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
