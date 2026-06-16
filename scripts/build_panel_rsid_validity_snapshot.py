#!/usr/bin/env python3
"""Build the offline panel rsID validity snapshot (#787).

Generates ``tests/fixtures/panel_rsid_validity_snapshot.json``: a checked-in map
of every well-formed dbSNP rsID curated in ``backend/data/panels/*.json`` to its
authoritative dbSNP merge status, so CI can catch a *well-formed but
dbSNP-merged/withdrawn* rsID landing in a panel — the defect class behind #645
(VHL rs28940299, dbSNP-merged) that the #786 denylist + #742 coordinate fixture
do not catch (a merged rsID still *resolves* to a coordinate).

Authoritative source: **dbSNP refsnp v2** (``api.ncbi.nlm.nih.gov/variation/v0``).
A refSNP record carries ``merged_snapshot_data.merged_into`` when dbSNP has merged
it into another refSNP (→ status ``merged``; the panel should switch to the
current id), ``primary_snapshot_data`` when it is the current record (→ ``current``),
and neither / no record (HTTP 404) when withdrawn/unsupported (→ ``withdrawn``).
dbSNP — not the frozen Ensembl GRCh37 mirror (#742's coordinate source, which
lags current dbSNP and disagrees on merge targets) — is the merge authority.

Tests consume this snapshot offline (never fetched at runtime). Regenerate it
deliberately when adding/changing panel rsIDs — the coverage test fails until the
snapshot covers every panel rsID, forcing a refresh that picks up new merges.

Usage::

    python scripts/build_panel_rsid_validity_snapshot.py --accessed YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_PANELS = _REPO / "backend" / "data" / "panels"
_FIXTURE = _REPO / "tests" / "fixtures" / "panel_rsid_validity_snapshot.json"
_DBSNP_REFSNP = "https://api.ncbi.nlm.nih.gov/variation/v0/refsnp"
_RSID_RE = re.compile(r"^rs\d+$")
_RS_FIELDS = ("rsid",)
_RS_LIST_FIELDS = ("expected_clinvar_rsids",)
# Mirror the coordinate builder / guard: the haplogroup bundle mixes dbSNP rsIDs
# with synthetic probe IDs and phylogenetic Y/mt marker names, audited separately
# (#805). dbSNP-merge resolution does not apply to non-dbSNP marker naming.
_EXCLUDED_PANEL_FILES = {
    "haplogroup_bundle.json": (
        "Haplogroup tree markers mix dbSNP rsIDs, synthetic array probe IDs, and "
        "phylogenetic Y/mt marker naming; tree-marker identity is audited separately "
        "(see issue #805)."
    )
}


def _walk_rsids(obj: object) -> list[str]:
    """Recursively gather rsIDs from panel fields that intentionally carry them."""
    found: list[str] = []
    if isinstance(obj, dict):
        for field in _RS_FIELDS:
            value = obj.get(field)
            if isinstance(value, str):
                found.append(value)
        for field in _RS_LIST_FIELDS:
            value = obj.get(field)
            if isinstance(value, list):
                found.extend(item for item in value if isinstance(item, str))
        for value in obj.values():
            found.extend(_walk_rsids(value))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_walk_rsids(item))
    return found


def _collect_panel_rsids() -> list[str]:
    rsids: set[str] = set()
    for path in sorted(_PANELS.glob("*.json")):
        if path.name in _EXCLUDED_PANEL_FILES:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        rsids.update(rsid for rsid in _walk_rsids(data) if _RSID_RE.match(rsid))
    return sorted(rsids, key=lambda rsid: int(rsid[2:]))


def _fetch_refsnp(rsid: str, *, retries: int = 3) -> dict[str, Any] | None:
    """dbSNP refsnp v2 record, or None when dbSNP has no such refSNP (HTTP 404)."""
    url = f"{_DBSNP_REFSNP}/{rsid[2:]}"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Yeliztli panel-rsid-validity"},
    )
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed host)
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None  # No refSNP record — withdrawn / never existed.
            last_exc = exc
        except (OSError, json.JSONDecodeError) as exc:
            last_exc = exc
        time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"{rsid}: dbSNP refsnp request failed: {last_exc}")


def _validity_record(rsid: str, record: dict[str, Any] | None) -> dict[str, Any]:
    source = f"{_DBSNP_REFSNP}/{rsid[2:]}"
    if record is None:
        return {"status": "withdrawn", "merged_into": None, "source": source}
    merged = record.get("merged_snapshot_data")
    if isinstance(merged, dict) and merged.get("merged_into"):
        targets = [str(t) for t in merged["merged_into"]]
        return {
            "status": "merged",
            "merged_into": [f"rs{t}" for t in targets],
            "source": source,
        }
    if record.get("primary_snapshot_data"):
        return {"status": "current", "merged_into": None, "source": source}
    # A record with neither primary nor merge data (e.g. unsupported/withdrawn).
    return {"status": "withdrawn", "merged_into": None, "source": source}


def _display_output_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_REPO))
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--accessed",
        required=True,
        help="Access date (YYYY-MM-DD) recorded in snapshot provenance.",
    )
    parser.add_argument("--output", type=Path, default=_FIXTURE, help="Snapshot path to write.")
    args = parser.parse_args()

    rsids = _collect_panel_rsids()
    if not rsids:
        raise SystemExit("ERROR: no panel rsIDs discovered")
    print(f"Collected {len(rsids)} well-formed panel rsIDs", file=sys.stderr)

    records: dict[str, dict[str, Any]] = {}
    for idx, rsid in enumerate(rsids, start=1):
        records[rsid] = _validity_record(rsid, _fetch_refsnp(rsid))
        print(f"  {idx}/{len(rsids)} {rsid} -> {records[rsid]['status']}", file=sys.stderr)
        time.sleep(0.34)  # NCBI: ≤3 req/s without an API key.

    bad = {r: rec for r, rec in records.items() if rec["status"] != "current"}
    if bad:
        print(f"WARNING: {len(bad)} non-current rsID(s):", file=sys.stderr)
        for r, rec in sorted(bad.items()):
            print(f"  {r}: {rec['status']} -> {rec['merged_into']}", file=sys.stderr)

    snapshot = {
        "_provenance": {
            "source": "dbSNP refsnp v2: api.ncbi.nlm.nih.gov/variation/v0/refsnp/{id}",
            "accessed": args.accessed,
            "generator": "scripts/build_panel_rsid_validity_snapshot.py",
            "panel_rsid_count": len(records),
            "excluded_panel_files": _EXCLUDED_PANEL_FILES,
            "note": (
                "Committed offline reference for the panel rsID *validity* guard (#787): "
                "status=merged means dbSNP retired this id into merged_into (switch the panel "
                "to it); status=withdrawn means dbSNP has no refSNP record. Authoritative source "
                "is dbSNP refsnp v2 (not the frozen Ensembl GRCh37 mirror, which lags current "
                "dbSNP). Regenerate deliberately; tests never fetch this at runtime."
            ),
        },
        "rsids": {rsid: records[rsid] for rsid in rsids},
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(records)} entries -> {_display_output_path(args.output)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
