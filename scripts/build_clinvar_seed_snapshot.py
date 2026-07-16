#!/usr/bin/env python3
"""Build the offline ClinVar snapshot for ``clinvar_seed.csv`` (#1968).

The seed is a checked-in test oracle, so every non-synthetic accession must resolve to
the row's own dbSNP rsID and gene.  This script captures that primary ClinVar metadata
once; CI consumes the committed snapshot without making network requests.

Usage::

    python scripts/build_clinvar_seed_snapshot.py --accessed YYYY-MM-DD
    python scripts/build_clinvar_seed_snapshot.py --accessed YYYY-MM-DD \
        --raw-evidence-dir data/science-evidence/YYYY-MM-DD-concern

Source: NLM Clinical Tables ClinVar variants v4 search (GRCh37/``na`` records),
built by NLM from ClinVar's ``variant_summary.txt.gz`` source data.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_SEED = _REPO / "tests" / "fixtures" / "seed_csvs" / "clinvar_seed.csv"
_SNAPSHOT = _REPO / "tests" / "fixtures" / "clinvar_seed_snapshot.json"
_CLINVAR_SEARCH = "https://clinicaltables.nlm.nih.gov/api/variants/v4/search"
_FIELDS = (
    "VariationID",
    "Name",
    "GeneSymbol",
    "ClinicalSignificance",
    "ReviewStatus",
    "PhenotypeList",
    "dbSNP",
)
_RSID_RE = re.compile(r"rs\d+")
_GENE_RE = re.compile(r"[A-Za-z0-9_-]+")
_GENE_ALIASES = {"GBA": {"GBA", "GBA1"}}


def _iso_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--accessed must be YYYY-MM-DD, got {value!r}") from exc


def _seed_rows() -> list[dict[str, str]]:
    with _SEED.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _fetch_exact_rsid(rsid: str) -> list[Any]:
    fields = ",".join(_FIELDS)
    query = urllib.parse.urlencode(
        {
            "terms": rsid,
            "sf": "dbSNP",
            "q": f"dbSNP:{rsid}",
            "maxList": "500",
            "df": fields,
            "ef": fields,
        }
    )
    request = urllib.request.Request(
        f"{_CLINVAR_SEARCH}?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": "Yeliztli clinvar-seed-provenance-snapshot",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            payload = json.load(response)
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{rsid}: ClinVar Clinical Tables request failed: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError(f"{rsid}: ClinVar returned {type(payload).__name__}, expected list")
    return payload


def _load_saved(raw_evidence_dir: Path, rsid: str) -> list[Any]:
    path = raw_evidence_dir / f"clinvar-search-{rsid}.json"
    if not path.is_file():
        raise RuntimeError(f"{rsid}: missing saved evidence {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{rsid}: malformed saved evidence {path}: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError(f"{rsid}: saved evidence {path} is not a Clinical Tables list")
    return payload


def _records(payload: list[Any], rsid: str) -> list[dict[str, str]]:
    if len(payload) < 3 or not isinstance(payload[2], dict):
        raise RuntimeError(f"{rsid}: unexpected Clinical Tables response shape")
    extra = payload[2]
    missing = set(_FIELDS) - set(extra)
    if missing:
        raise RuntimeError(f"{rsid}: response missing fields {sorted(missing)}")
    columns = [extra[field] for field in _FIELDS]
    if any(not isinstance(column, list) for column in columns):
        raise RuntimeError(f"{rsid}: response field is not an array")
    lengths = {len(column) for column in columns}
    if len(lengths) != 1:
        raise RuntimeError(f"{rsid}: response field lengths differ: {sorted(lengths)}")
    return [
        {field: str(value) for field, value in zip(_FIELDS, values, strict=True)}
        for values in zip(*columns, strict=True)
    ]


def _dbsnp_ids(value: str) -> list[str]:
    return sorted(set(_RSID_RE.findall(value)), key=lambda rsid: int(rsid[2:]))


def _gene_symbols(value: str) -> list[str]:
    return sorted(set(_GENE_RE.findall(value)))


def _select_record(
    row: dict[str, str],
    payload: list[Any],
) -> dict[str, str]:
    variation_id = row["variation_id"]
    matches = [
        record
        for record in _records(payload, row["rsid"])
        if record["VariationID"] == variation_id
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"{row['rsid']}: expected one VariationID {variation_id} record, got {len(matches)}"
        )
    record = matches[0]
    dbsnp = _dbsnp_ids(record["dbSNP"])
    if row["rsid"] not in dbsnp:
        raise RuntimeError(f"{row['rsid']}: VariationID {variation_id} dbSNP xrefs are {dbsnp}")
    wanted_genes = _GENE_ALIASES.get(row["gene_symbol"], {row["gene_symbol"]})
    record_genes = set(_gene_symbols(record["GeneSymbol"]))
    if not (wanted_genes & record_genes):
        raise RuntimeError(
            f"{row['rsid']}: seed gene {row['gene_symbol']} but VariationID {variation_id} "
            f"genes are {sorted(record_genes)}"
        )
    return record


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(_REPO))
    except ValueError:
        parts = resolved.parts
        for index in range(len(parts) - 1):
            if parts[index : index + 2] == ("data", "science-evidence"):
                return str(Path(*parts[index:]))
        return str(resolved)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accessed", required=True, type=_iso_date)
    parser.add_argument(
        "--raw-evidence-dir",
        type=Path,
        help="Read saved clinvar-search-rs*.json payloads instead of querying live.",
    )
    parser.add_argument("--output", type=Path, default=_SNAPSHOT)
    args = parser.parse_args()

    rows = _seed_rows()
    synthetic = [row for row in rows if not row["accession"] and not row["variation_id"]]
    real_rows = [row for row in rows if row["accession"] or row["variation_id"]]
    malformed = [
        row["rsid"]
        for row in real_rows
        if not row["accession"] or not row["variation_id"].isdigit()
    ]
    if malformed:
        print(f"ERROR: partially specified ClinVar identifiers: {malformed}", file=sys.stderr)
        return 1
    incoherent_accessions = [
        row["rsid"]
        for row in real_rows
        if row["accession"] != f"VCV{int(row['variation_id']):09d}"
    ]
    if incoherent_accessions:
        print(
            f"ERROR: accessions do not encode their VariationIDs: {incoherent_accessions}",
            file=sys.stderr,
        )
        return 1

    evidence_mode = "live_exact_rsid_searches"
    if args.raw_evidence_dir is not None:
        evidence_mode = "saved_exact_rsid_searches"

    records: dict[str, dict[str, object]] = {}
    for index, row in enumerate(real_rows, start=1):
        try:
            payload = (
                _load_saved(args.raw_evidence_dir, row["rsid"])
                if args.raw_evidence_dir is not None
                else _fetch_exact_rsid(row["rsid"])
            )
            record = _select_record(row, payload)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        accession = row["accession"]
        if accession in records:
            print(f"ERROR: duplicate seed accession {accession}", file=sys.stderr)
            return 1
        records[accession] = {
            "variation_id": int(record["VariationID"]),
            "dbsnp": _dbsnp_ids(record["dbSNP"]),
            "gene_symbols": _gene_symbols(record["GeneSymbol"]),
            "name": record["Name"],
            "clinical_significance": record["ClinicalSignificance"],
            "review_status": record["ReviewStatus"],
            "phenotype_list": record["PhenotypeList"],
        }
        print(f"  resolved {index}/{len(real_rows)} {row['rsid']} -> {accession}", file=sys.stderr)
        if args.raw_evidence_dir is None:
            time.sleep(0.05)

    ordered = dict(sorted(records.items(), key=lambda item: int(item[1]["variation_id"])))
    snapshot = {
        "_provenance": {
            "source": "NLM Clinical Tables ClinVar variants v4 search",
            "source_url": _CLINVAR_SEARCH,
            "accessed": args.accessed,
            "generator": "scripts/build_clinvar_seed_snapshot.py",
            "evidence_mode": evidence_mode,
            "raw_evidence_dir": (
                _display_path(args.raw_evidence_dir) if args.raw_evidence_dir is not None else None
            ),
            "record_count": len(ordered),
            "synthetic_rows_without_accession": [
                f"{row['rsid']}:{row['ref']}>{row['alt']}" for row in synthetic
            ],
            "note": (
                "Committed offline accession-to-rsID/gene reference for clinvar_seed.csv "
                "(#1968). Regenerate whenever the seed adds or changes a ClinVar accession; "
                "the guard fails on uncovered, orphaned, or incoherent records."
            ),
        },
        "records": ordered,
    }
    args.output.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(ordered)} records -> {_display_path(args.output)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
