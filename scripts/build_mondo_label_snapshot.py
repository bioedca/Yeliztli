#!/usr/bin/env python3
"""Build the offline MONDO label snapshot for the gene_phenotype provenance guard (#1959).

Generates ``tests/fixtures/mondo_label_snapshot.json``: a committed map of every
MONDO ``disease_id`` cited by ``tests/fixtures/seed_csvs/gene_phenotype_seed.csv`` →
``{label, obsolete}``, resolved once against EBI OLS4 (the MONDO ontology itself).
``tests/backend/test_gene_phenotype_provenance.py`` reads it to check that each row's
``disease_id`` is current, non-obsolete, and topically consistent with its
``disease_name`` — **without touching the network at test time**.

#1959 found 40 of 59 rows carried a ``disease_id`` for an unrelated disease (a whole
16-ID block was a fabricated sequential counter: Tay-Sachs → Mounier-Kuhn, etc.), and
10 pointed at ``obsolete`` MONDO terms that the F21 label filter could not catch
because the row's *name* looked clean. This snapshot makes both regress loudly.

Usage::

    python scripts/build_mondo_label_snapshot.py [--accessed YYYY-MM-DD]

Source: EBI OLS4 (``ebi.ac.uk/ols4/api``), ontology=mondo. Never fetched at test time.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SEED = _REPO / "tests" / "fixtures" / "seed_csvs" / "gene_phenotype_seed.csv"
_SNAPSHOT = _REPO / "tests" / "fixtures" / "mondo_label_snapshot.json"
_OLS_TERM = "https://www.ebi.ac.uk/ols4/api/ontologies/mondo/terms"
_OLS_ONTOLOGY = "https://www.ebi.ac.uk/ols4/api/ontologies/mondo"


def _iso_date(value: str) -> str:
    """argparse type: accept only a real ``YYYY-MM-DD`` calendar date."""
    from datetime import date

    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--accessed must be YYYY-MM-DD, got {value!r}") from exc


def _mondo_version() -> str:
    """The MONDO release the snapshot was resolved against (for reproducibility)."""
    with urllib.request.urlopen(_OLS_ONTOLOGY, timeout=30) as resp:  # noqa: S310 - fixed EBI endpoint
        cfg = json.load(resp).get("config", {})
    return str(cfg.get("version") or cfg.get("versionIri") or "unknown")


def _cited_ids() -> list[str]:
    with open(_SEED, encoding="utf-8") as f:
        ids = {row["disease_id"].strip() for row in csv.DictReader(f) if row["disease_id"].strip()}
    return sorted(ids)


def _resolve(mondo_id: str) -> dict[str, object] | None:
    iri = urllib.parse.quote(
        f"http://purl.obolibrary.org/obo/{mondo_id.replace(':', '_')}", safe=""
    )
    url = f"{_OLS_TERM}?iri={iri}"
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - fixed EBI endpoint
        terms = json.load(resp).get("_embedded", {}).get("terms", [])
    if not terms:
        return None
    t = terms[0]
    return {"label": t.get("label", ""), "obsolete": bool(t.get("is_obsolete"))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accessed", required=True, type=_iso_date, help="regen date, YYYY-MM-DD")
    opts = parser.parse_args()

    ids = _cited_ids()
    print(f"Resolving {len(ids)} MONDO ids cited by gene_phenotype_seed.csv", file=sys.stderr)
    labels: dict[str, dict[str, object]] = {}
    unresolved: list[str] = []
    for mid in ids:
        meta = _resolve(mid)
        if meta is None:
            unresolved.append(mid)
            print(f"  WARNING: {mid} did not resolve", file=sys.stderr)
        else:
            labels[mid] = meta
        time.sleep(0.05)

    if unresolved:
        # A cited id that resolves to nothing is not a real MONDO term. Refuse to write
        # a snapshot the guard cannot pass — fix the citing row instead.
        print(
            f"ERROR: {len(unresolved)} cited MONDO id(s) did not resolve: {unresolved}\n"
            f"       Fix {_SEED.relative_to(_REPO)}; snapshot NOT written.",
            file=sys.stderr,
        )
        return 1

    snapshot = {
        "_provenance": {
            "source": "EBI OLS4 (ebi.ac.uk/ols4/api, ontology=mondo)",
            "mondo_version": _mondo_version(),
            "accessed": opts.accessed,
            "id_count": len(labels),
            "generator": "scripts/build_mondo_label_snapshot.py",
            "note": (
                "Committed offline reference for the gene_phenotype provenance guard (#1959). "
                "Regenerate whenever gene_phenotype_seed.csv gains or changes a disease_id — the "
                "guard FAILS on an uncovered id rather than skipping it."
            ),
        },
        "labels": {mid: labels[mid] for mid in sorted(labels)},
    }
    _SNAPSHOT.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(labels)} entries → {_SNAPSHOT.relative_to(_REPO)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
