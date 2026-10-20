#!/usr/bin/env python3
"""Snapshot the production VEP bundle rows behind ``vep_seed.csv`` (#2363).

``vep_seed.csv`` seeds ``mini_vep_bundle.db``, the test stand-in for the
production VEP bundle, so its annotated rows should say what production says.
The real bundle is not available in CI, so this script records, for every
annotated rsID in the seed, the bundle's rows for that rsID together with the
bundle's identity, in ``tests/fixtures/vep_seed_bundle_snapshot.json``. The
tests compare the seed against that snapshot offline.

It refuses any bundle other than the one ``bundles/manifest.json`` pins, so the
snapshot always describes the bundle users are given.

Usage::

    python scripts/build_vep_seed_bundle_snapshot.py --bundle ~/.yeliztli/vep_bundle.db
    python scripts/build_vep_seed_bundle_snapshot.py --bundle ... --apply

``--apply`` also rewrites each annotated seed row that sits at exactly one of its
rsID's bundle positions and alleles so its annotation matches that bundle row.
Rows anywhere else are left alone: their coordinates are the problem (#2375).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SEED = _ROOT / "tests" / "fixtures" / "seed_csvs" / "vep_seed.csv"
_SNAPSHOT = _ROOT / "tests" / "fixtures" / "vep_seed_bundle_snapshot.json"
_MANIFEST = _ROOT / "bundles" / "manifest.json"

# The seed columns a bundle row decides; coordinates and alleles are matched, not copied.
MIRRORED_FIELDS = (
    "gene_symbol",
    "transcript_id",
    "consequence",
    "hgvs_coding",
    "hgvs_protein",
    "strand",
    "exon_number",
    "intron_number",
    "mane_select",
)
_LOCUS = ("chrom", "pos", "ref", "alt")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _text(value: object) -> str:
    return "" if value is None else str(value)


def bundle_rows(bundle: Path, rsids: list[str]) -> dict[str, list[dict[str, str]]]:
    """Every bundle row for each rsID, as seed-shaped strings (``[]`` when absent)."""
    conn = sqlite3.connect(f"file:{bundle}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        out: dict[str, list[dict[str, str]]] = {}
        for rsid in rsids:
            rows = conn.execute(
                "SELECT * FROM vep_annotations WHERE rsid = ? ORDER BY chrom, pos, ref, alt",
                (rsid,),
            ).fetchall()
            out[rsid] = [
                {field: _text(row[field]) for field in (*_LOCUS, *MIRRORED_FIELDS)} for row in rows
            ]
        return out
    finally:
        conn.close()


def bundle_identity(bundle: Path, manifest: Path) -> dict[str, str]:
    """The bundle's identity, refusing one the manifest does not pin."""
    pinned = json.loads(manifest.read_text(encoding="utf-8"))["bundles"]["vep_bundle"]
    sha256 = _sha256(bundle)
    if sha256 != pinned["sha256"]:
        raise SystemExit(
            f"{bundle} has sha256 {sha256}, not the {pinned['version']} bundle the manifest "
            f"pins ({pinned['sha256']}); refusing to snapshot it"
        )
    conn = sqlite3.connect(f"file:{bundle}?mode=ro", uri=True)
    try:
        metadata = dict(conn.execute("SELECT key, value FROM bundle_metadata").fetchall())
    finally:
        conn.close()
    if metadata.get("bundle_version") != pinned["version"]:
        raise SystemExit(
            f"{bundle} says it is {metadata.get('bundle_version')!r}, the manifest pins "
            f"{pinned['version']!r}; refusing to snapshot it"
        )
    return {
        "bundle_version": pinned["version"],
        "ensembl_version": metadata.get("ensembl_version", ""),
        "build_date": metadata.get("build_date", ""),
        "sha256": sha256,
    }


def annotated_rsids(seed_rows: list[dict[str, str]]) -> list[str]:
    return sorted({row["rsid"] for row in seed_rows if row.get("gene_symbol")})


def apply_bundle(
    seed_rows: list[dict[str, str]], snapshot_rows: dict[str, list[dict[str, str]]]
) -> list[str]:
    """Mirror each annotated row that sits at exactly one bundle row; return what changed."""
    changed = []
    for row in seed_rows:
        if not row.get("gene_symbol"):
            continue
        matches = [
            candidate
            for candidate in snapshot_rows.get(row["rsid"], [])
            if all(candidate[key] == row[key] for key in _LOCUS)
        ]
        if len(matches) != 1:
            continue
        (match,) = matches
        if any(row[field] != match[field] for field in MIRRORED_FIELDS):
            row.update({field: match[field] for field in MIRRORED_FIELDS})
            changed.append(row["rsid"])
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bundle", type=Path, required=True, help="the production vep_bundle.db")
    parser.add_argument("--seed", type=Path, default=_SEED)
    parser.add_argument("--snapshot", type=Path, default=_SNAPSHOT)
    parser.add_argument("--manifest", type=Path, default=_MANIFEST)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="also mirror the bundle into seed rows already at a bundle position",
    )
    args = parser.parse_args(argv)

    identity = bundle_identity(args.bundle, args.manifest)
    raw = args.seed.read_text(encoding="utf-8")
    seed_rows = list(csv.DictReader(io.StringIO(raw)))
    rows = bundle_rows(args.bundle, annotated_rsids(seed_rows))
    snapshot = {
        "_provenance": {
            "source": "production VEP bundle pinned by bundles/manifest.json",
            **identity,
            "generated": dt.datetime.now(dt.UTC).date().isoformat(),
            "generator": "scripts/build_vep_seed_bundle_snapshot.py",
            "scope": "every bundle row for each rsID with a gene_symbol in vep_seed.csv",
        },
        "rows": rows,
    }
    args.snapshot.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.snapshot} ({len(rows)} rsIDs)", file=sys.stderr)

    if args.apply:
        changed = apply_bundle(seed_rows, rows)
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=list(seed_rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(seed_rows)
        args.seed.write_text(out.getvalue(), encoding="utf-8")
        print(f"mirrored {len(changed)} rows: {', '.join(changed)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
