#!/usr/bin/env python3
"""Build the offline panel rsID -> GRCh37 coordinate fixture (#742).

Generates ``tests/fixtures/panel_rsid_coordinates.json``: a checked-in map of
every well-formed dbSNP rsID curated in ``backend/data/panels/*.json`` to a
standard-chromosome GRCh37 coordinate. Tests consume this fixture offline, so CI
can catch withdrawn/unplaced panel rsIDs without live network access.

Usage::

    python scripts/build_panel_rsid_coordinates.py --accessed YYYY-MM-DD

Source: Ensembl GRCh37 REST ``/variation/human/{rsid}``. Be polite: one request
per rsID with a short pause between requests.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_PANELS = _REPO / "backend" / "data" / "panels"
_FIXTURE = _REPO / "tests" / "fixtures" / "panel_rsid_coordinates.json"
_ENSEMBL_GRCH37 = "https://grch37.rest.ensembl.org/variation/human"
_RSID_RE = re.compile(r"^rs\d+$")
_RS_FIELDS = ("rsid",)
_RS_LIST_FIELDS = ("expected_clinvar_rsids",)
_STANDARD_CHROMS = tuple(str(i) for i in range(1, 23)) + ("X", "Y", "MT")
_CHROM_ORDER = {chrom: i for i, chrom in enumerate(_STANDARD_CHROMS)}
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


def _fetch_variation(rsid: str) -> dict[str, Any]:
    quoted = urllib.parse.quote(rsid)
    url = f"{_ENSEMBL_GRCH37}/{quoted}?content-type=application/json"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Yeliztli panel-rsid-coordinate fixture builder",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed host)
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{rsid}: Ensembl returned HTTP {exc.code}") from exc
    except OSError as exc:
        raise RuntimeError(f"{rsid}: Ensembl request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{rsid}: Ensembl returned non-JSON: {exc}") from exc


def _primary_mapping(record: dict[str, Any]) -> dict[str, Any] | None:
    mappings = record.get("mappings")
    if not isinstance(mappings, list):
        return None

    candidates: list[dict[str, Any]] = []
    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        assembly = mapping.get("assembly_name")
        if assembly not in (None, "GRCh37"):
            continue
        chrom = str(mapping.get("seq_region_name") or "")
        if chrom not in _CHROM_ORDER:
            continue
        if mapping.get("start") is None or mapping.get("end") is None:
            continue
        candidates.append(mapping)

    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda m: (_CHROM_ORDER[str(m["seq_region_name"])], int(m["start"]), int(m["end"])),
    )[0]


def _coordinate_record(rsid: str, record: dict[str, Any]) -> dict[str, Any]:
    mapping = _primary_mapping(record)
    if mapping is None:
        raise RuntimeError(f"{rsid}: no standard-chromosome GRCh37 mapping")

    chrom = str(mapping["seq_region_name"])
    start = int(mapping["start"])
    end = int(mapping["end"])
    allele_string = str(mapping.get("allele_string") or record.get("allele_string") or "")
    if not allele_string:
        raise RuntimeError(f"{rsid}: missing allele_string")

    location = f"{chrom}:{start}" if start == end else f"{chrom}:{start}-{end}"
    return {
        "assembly": "GRCh37",
        "chrom": chrom,
        "start": start,
        "end": end,
        "location": location,
        "strand": int(mapping.get("strand", 1)),
        "allele_string": allele_string,
        "variant_class": record.get("var_class") or "",
        "source": f"{_ENSEMBL_GRCH37}/{rsid}",
    }


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
        help="Access date (YYYY-MM-DD) recorded in fixture provenance.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_FIXTURE,
        help="Fixture path to write.",
    )
    args = parser.parse_args()

    rsids = _collect_panel_rsids()
    if not rsids:
        raise SystemExit("ERROR: no panel rsIDs discovered")
    print(f"Collected {len(rsids)} well-formed panel rsIDs", file=sys.stderr)

    coordinates: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for idx, rsid in enumerate(rsids, start=1):
        try:
            coordinates[rsid] = _coordinate_record(rsid, _fetch_variation(rsid))
            print(f"  resolved {idx}/{len(rsids)} {rsid}", file=sys.stderr)
        except (RuntimeError, KeyError, TypeError, ValueError) as exc:
            errors.append(str(exc))
            print(f"  failed {idx}/{len(rsids)} {rsid}", file=sys.stderr)
        time.sleep(0.12)

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(f"ERROR: {len(errors)} panel rsID(s) did not resolve")

    fixture = {
        "_provenance": {
            "source": "Ensembl GRCh37 REST /variation/human/{rsid}",
            "assembly": "GRCh37",
            "accessed": args.accessed,
            "generator": "scripts/build_panel_rsid_coordinates.py",
            "panel_rsid_count": len(coordinates),
            "excluded_panel_files": _EXCLUDED_PANEL_FILES,
            "note": (
                "Committed offline reference for the panel rsID coordinate guard "
                "(#742). Regenerate deliberately; tests never fetch this at runtime."
            ),
        },
        "rsids": {rsid: coordinates[rsid] for rsid in rsids},
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n")
    print(
        f"Wrote {len(coordinates)} entries -> {_display_output_path(args.output)}", file=sys.stderr
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
