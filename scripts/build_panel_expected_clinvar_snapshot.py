#!/usr/bin/env python3
"""Build the offline expected ClinVar rsID -> gene snapshot (#1539).

Generates ``tests/fixtures/panel_expected_clinvar_snapshot.json``: a checked-in
map of gene-level ``expected_clinvar_rsids`` from the cancer, cardiovascular,
and carrier panels to ClinVar Clinical Tables rows. Tests consume this fixture
offline so CI can catch valid ClinVar rsIDs curated under the wrong panel gene.

Usage::

    python scripts/build_panel_expected_clinvar_snapshot.py --accessed YYYY-MM-DD
    python scripts/build_panel_expected_clinvar_snapshot.py \
        --accessed YYYY-MM-DD \
        --raw-evidence-dir data/science-evidence/YYYY-MM-DD-concern
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
_FIXTURE = _REPO / "tests" / "fixtures" / "panel_expected_clinvar_snapshot.json"
_CLINICAL_TABLES_URL = "https://clinicaltables.nlm.nih.gov/api/variants/v4/search"
_PANEL_FILES = ("cancer_panel.json", "cardiovascular_panel.json", "carrier_panel.json")
_FIELDS = ("VariationID", "Name", "GeneSymbol", "ClinicalSignificance", "dbSNP")
_RSID_RE = re.compile(r"^rs\d+$")
_CLINVAR_RAW_GLOB = "clinvar-search-*-fields.json"
_GENE_ALIASES = {
    # ClinVar uses the current GBA1 symbol for the gene historically labeled GBA
    # in the carrier panel.
    "GBA": ["GBA1"],
}


def _collect_expected_rsids() -> dict[str, dict[str, Any]]:
    rsids: dict[str, dict[str, Any]] = {}
    for panel_name in _PANEL_FILES:
        data = json.loads((_PANELS / panel_name).read_text(encoding="utf-8"))
        genes = data.get("genes")
        if not isinstance(genes, list):
            raise RuntimeError(f"{panel_name}: missing top-level genes list")
        for gene in genes:
            if not isinstance(gene, dict):
                raise RuntimeError(f"{panel_name}: malformed gene entry")
            symbol = gene.get("gene_symbol")
            expected = gene.get("expected_clinvar_rsids")
            if not isinstance(symbol, str) or not symbol:
                raise RuntimeError(f"{panel_name}: gene missing gene_symbol")
            if not isinstance(expected, list):
                raise RuntimeError(f"{panel_name}: {symbol}: missing expected_clinvar_rsids")
            for rsid in expected:
                if not isinstance(rsid, str) or not _RSID_RE.match(rsid):
                    raise RuntimeError(f"{panel_name}: {symbol}: malformed rsID {rsid!r}")
                rec = rsids.setdefault(rsid, {"panel_genes": []})
                rec["panel_genes"].append({"panel": panel_name, "gene_symbol": symbol})
    return dict(sorted(rsids.items(), key=lambda item: int(item[0][2:])))


def _split_symbols(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,;/|]", value) if part.strip()]


def _split_rsids(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,;/|]", value) if _RSID_RE.match(part.strip())]


def _fetch_clinvar_rows(rsid: str, *, max_list: int = 500) -> list[dict[str, Any]]:
    params = {
        "terms": rsid,
        "maxList": str(max_list),
        "df": ",".join(_FIELDS),
        "ef": ",".join(_FIELDS),
    }
    url = f"{_CLINICAL_TABLES_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Yeliztli panel-expected-clinvar-snapshot",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed host)
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{rsid}: ClinVar Clinical Tables returned HTTP {exc.code}") from exc
    except OSError as exc:
        raise RuntimeError(f"{rsid}: ClinVar Clinical Tables request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{rsid}: ClinVar Clinical Tables returned non-JSON: {exc}") from exc

    if not isinstance(data, list) or len(data) < 3 or not isinstance(data[2], dict):
        raise RuntimeError(f"{rsid}: unexpected Clinical Tables response shape")
    extra = data[2]
    rows: list[dict[str, Any]] = []
    for variation_id, name, gene_symbol, significance, dbsnp in zip(
        extra.get("VariationID", []),
        extra.get("Name", []),
        extra.get("GeneSymbol", []),
        extra.get("ClinicalSignificance", []),
        extra.get("dbSNP", []),
        strict=False,
    ):
        row_rsids = _split_rsids(str(dbsnp))
        if rsid not in row_rsids:
            continue
        rows.append(
            {
                "variation_id": str(variation_id),
                "name": str(name),
                "gene_symbols": _split_symbols(str(gene_symbol)),
                "clinical_significance": str(significance),
                "dbsnp": row_rsids,
            }
        )
    return rows


def _raw_clinical_table_rows(path: Path) -> list[list[Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or len(data) < 4 or not isinstance(data[3], list):
        raise RuntimeError(f"{path}: unexpected Clinical Tables raw response shape")
    return [row for row in data[3] if isinstance(row, list) and len(row) >= len(_FIELDS)]


def _rows_from_raw_evidence(raw_evidence_dir: Path, rsids: dict[str, dict[str, Any]]) -> None:
    """Populate rsID rows from saved gene-level Clinical Tables payloads."""
    row_count = 0
    for path in sorted(raw_evidence_dir.glob(_CLINVAR_RAW_GLOB)):
        for (
            variation_id,
            name,
            gene_symbol,
            significance,
            dbsnp,
            *_rest,
        ) in _raw_clinical_table_rows(path):
            for rsid in _split_rsids(str(dbsnp)):
                if rsid not in rsids:
                    continue
                rsids[rsid].setdefault("clinvar_rows", []).append(
                    {
                        "variation_id": str(variation_id),
                        "name": str(name),
                        "gene_symbols": _split_symbols(str(gene_symbol)),
                        "clinical_significance": str(significance),
                        "dbsnp": _split_rsids(str(dbsnp)),
                    }
                )
                row_count += 1

    for rsid in list(rsids):
        rows = rsids[rsid].get("clinvar_rows") or []
        if not rows:
            del rsids[rsid]
            continue
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for row in rows:
            key = json.dumps(row, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        rsids[rsid]["clinvar_rows"] = deduped

    if not row_count:
        raise RuntimeError(f"{raw_evidence_dir}: no exact dbSNP rows matched panel rsIDs")


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
    parser.add_argument(
        "--raw-evidence-dir",
        type=Path,
        help=(
            "Use saved ClinVar Clinical Tables raw payloads from this directory instead "
            "of fetching live per-rsID rows. This is intended for reproducing evidence "
            "captured during issue work when network access is unavailable."
        ),
    )
    args = parser.parse_args()

    rsids = _collect_expected_rsids()
    if not rsids:
        raise SystemExit("ERROR: no expected ClinVar rsIDs discovered")
    print(f"Collected {len(rsids)} unique expected ClinVar rsIDs", file=sys.stderr)

    evidence_mode = "live_per_rsid"
    if args.raw_evidence_dir is not None:
        evidence_mode = "saved_gene_search_payloads"
        _rows_from_raw_evidence(args.raw_evidence_dir, rsids)
        print(
            f"Loaded {len(rsids)} expected ClinVar rsIDs from saved raw evidence",
            file=sys.stderr,
        )
    else:
        errors: list[str] = []
        for idx, (rsid, rec) in enumerate(rsids.items(), start=1):
            try:
                rows = _fetch_clinvar_rows(rsid)
            except RuntimeError as exc:
                errors.append(str(exc))
                rows = []
            rec["clinvar_rows"] = rows
            print(f"  resolved {idx}/{len(rsids)} {rsid} -> {len(rows)} row(s)", file=sys.stderr)
            if not rows:
                errors.append(f"{rsid}: no exact dbSNP rows returned by ClinVar Clinical Tables")
            time.sleep(0.12)

        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            raise SystemExit(f"ERROR: {len(errors)} expected ClinVar rsID issue(s)")

    fixture = {
        "_provenance": {
            "source": "ClinVar Clinical Tables variants v4 search",
            "accessed": args.accessed,
            "generator": "scripts/build_panel_expected_clinvar_snapshot.py",
            "panel_files": list(_PANEL_FILES),
            "panel_rsid_count": len(rsids),
            "gene_aliases": _GENE_ALIASES,
            "evidence_mode": evidence_mode,
            "raw_evidence_dir": (
                _display_output_path(args.raw_evidence_dir) if args.raw_evidence_dir else None
            ),
            "note": (
                "Committed offline reference for the gene-level expected ClinVar rsID guard "
                "(#1539). Saved-evidence mode covers the expected rsIDs present in the "
                "captured raw ClinVar payloads; tests never fetch this at runtime."
            ),
        },
        "rsids": rsids,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(rsids)} entries -> {_display_output_path(args.output)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
