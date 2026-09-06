"""Rebuild the committed Ensembl GRCh37 gene-strand snapshot for the vep_seed guard.

``tests/fixtures/seed_csvs/vep_seed.csv`` carried a ``strand`` column that was
wrong for 19 of its 57 annotated genes, four of them internally contradictory --
the same gene stamped ``+`` on one row and ``-`` on another, which is impossible
(#2045). A gene's genomic strand is invariant, so it can be snapshotted once and
checked offline forever after.

Writes ``tests/fixtures/vep_gene_strand_snapshot.json`` **and** the unedited
per-symbol responses it was reduced from. The evidence path is mandatory: a
snapshot that names no source-native payload cannot be re-derived or audited,
which is how the first revision of this oracle passed every guard while its
responses had been discarded (#2364 review). The snapshot's ``_provenance``
block records the repo-relative evidence path and its sha256, and
``tests/backend/test_vep_seed_strand_provenance.py`` checks that the committed
snapshot is exactly the reduction of the committed evidence.

Run it when ``vep_seed.csv`` gains a gene; the guard fails on an uncovered gene
rather than skipping it, so a new gene cannot slip through unchecked::

    python scripts/build_vep_strand_snapshot.py \\
        --evidence-out tests/fixtures/vep_gene_strand_snapshot.evidence.json

To rebuild from responses that were already retained (an evidence packet, for
instance) instead of fetching live, replay them and state when they were
fetched, so ``accessed`` stays the date of the responses rather than of the run::

    python scripts/build_vep_strand_snapshot.py \\
        --evidence-out tests/fixtures/vep_gene_strand_snapshot.evidence.json \\
        --responses-from <retained verbatim responses>.json --accessed YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SEED = _ROOT / "tests" / "fixtures" / "seed_csvs" / "vep_seed.csv"
_OUT = _ROOT / "tests" / "fixtures" / "vep_gene_strand_snapshot.json"
_URL = "https://grch37.rest.ensembl.org/lookup/symbol/homo_sapiens/{gene}?content-type=application/json"
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The seed carries one deliberately synthetic locus (rs12345 / ENST00000999999 on
# ``GENE1``), used by several suites as a neutral stand-in. It has no Ensembl
# record, and this generator fails closed on any unresolved symbol, so submitting
# it made the documented rebuild impossible (#2045 review). The guard in
# ``tests/backend/test_vep_seed_strand_provenance.py`` imports this same constant
# for its exemption, so the two cannot drift apart.
SYNTHETIC_GENES = frozenset({"GENE1"})

Fetcher = Callable[[str], dict | None]


def _genes() -> list[str]:
    with _SEED.open(encoding="utf-8") as handle:
        return sorted(
            {row["gene_symbol"] for row in csv.DictReader(handle) if row.get("gene_symbol")}
            - SYNTHETIC_GENES
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


def _repo_relative(path: Path) -> str:
    """Name a path relative to the repository root when it lies inside it."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _replay_fetcher(retained: Path) -> Fetcher:
    responses = json.loads(retained.read_text(encoding="utf-8"))["responses"]
    return lambda gene: responses.get(gene)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild tests/fixtures/vep_gene_strand_snapshot.json from Ensembl GRCh37 "
            "lookup/symbol, retaining the unedited responses it is reduced from."
        )
    )
    parser.add_argument(
        "--evidence-out",
        type=Path,
        required=True,
        metavar="PATH",
        help=(
            "where to write the verbatim per-symbol responses (required: a snapshot "
            "that names no source-native payload cannot be audited)"
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_OUT,
        metavar="PATH",
        help=f"snapshot to write (default: {_repo_relative(_OUT)})",
    )
    parser.add_argument(
        "--responses-from",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "replay retained verbatim responses (same shape as --evidence-out writes) "
            "instead of fetching live; requires --accessed"
        ),
    )
    parser.add_argument(
        "--accessed",
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "date the responses were fetched; required with --responses-from, "
            "defaults to today for a live fetch"
        ),
    )
    return parser


def main(argv: list[str] | None = None, *, fetch: Fetcher = _lookup) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.accessed is not None and not _ISO_DATE.match(args.accessed):
        parser.error("--accessed must be YYYY-MM-DD")
    if args.responses_from is not None:
        if args.accessed is None:
            parser.error(
                "--responses-from requires --accessed (the date those responses were fetched)"
            )
        fetch = _replay_fetcher(args.responses_from)
    live = fetch is _lookup
    accessed = args.accessed or time.strftime("%Y-%m-%d")

    strands: dict[str, dict[str, str]] = {}
    responses: dict[str, dict] = {}
    failures: list[str] = []
    genes = _genes()
    print(
        f"Resolving {len(genes)} genes cited by vep_seed.csv "
        f"(excluding synthetic {sorted(SYNTHETIC_GENES)})"
        + (f" by replaying {args.responses_from}" if args.responses_from else ""),
        file=sys.stderr,
    )
    for gene in genes:
        payload = fetch(gene)
        if payload is None:
            failures.append(gene)
            continue
        responses[gene] = payload
        strands[gene] = {
            "strand": "+" if payload["strand"] == 1 else "-",
            "ensembl_gene_id": payload.get("id"),
        }
        if live:
            time.sleep(0.12)

    if failures:
        # Fail closed: a partial snapshot would silently drop genes from the guard.
        print(f"ERROR: could not resolve {len(failures)} genes: {failures}", file=sys.stderr)
        return 1

    # Retain the source-native payloads first, then name them from the snapshot.
    note = (
        "Verbatim Ensembl GRCh37 REST lookup/symbol responses retained by "
        "scripts/build_vep_strand_snapshot.py; the only additions are this note and the "
        "`responses` wrapper keyed by the queried symbol. Each value is the parsed JSON body "
        f"of GET {_URL} (re-serialised; whitespace only, key order and values preserved), "
        f"fetched {accessed}. tests/fixtures/vep_gene_strand_snapshot.json is the reduction of "
        "this file to strand and id per gene."
    )
    if args.responses_from is not None:
        note += (
            f" Replayed from {_repo_relative(args.responses_from)} "
            f"(sha256 {_sha256(args.responses_from)}) rather than fetched live."
        )
    args.evidence_out.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_out.write_text(
        json.dumps({"_artifact_note": note, "responses": responses}, indent=1) + "\n",
        encoding="utf-8",
    )

    provenance: dict[str, object] = {
        "source": (
            "Ensembl GRCh37 REST (grch37.rest.ensembl.org/lookup/symbol/homo_sapiens/<gene>)"
        ),
        "assembly": "GRCh37",
        "accessed": accessed,
        "gene_count": len(strands),
        "fetch_failures": 0,
        "synthetic_excluded": sorted(SYNTHETIC_GENES),
        "generator": "scripts/build_vep_strand_snapshot.py",
        "evidence_path": _repo_relative(args.evidence_out),
        "evidence_sha256": _sha256(args.evidence_out),
        "evidence_bytes": args.evidence_out.stat().st_size,
        "note": (
            "Committed offline reference for the vep_seed strand guard (#2045). A gene's "
            "genomic strand is invariant, so this needs no version pinning beyond the "
            "assembly. Regenerate whenever vep_seed.csv gains a gene -- the guard FAILS on an "
            "uncovered gene rather than skipping it. GENE1 is a deliberately synthetic locus "
            "with no Ensembl record and is exempt in the guard, pinned by symbol. The strands "
            "here are exactly the reduction of the evidence file named above, which the guard "
            "re-derives."
        ),
    }
    if args.responses_from is not None:
        provenance["evidence_replayed_from"] = {
            "path": _repo_relative(args.responses_from),
            "sha256": _sha256(args.responses_from),
        }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"_provenance": provenance, "strands": dict(sorted(strands.items()))}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {args.out} ({len(strands)} genes) and {args.evidence_out} "
        f"({len(responses)} responses)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
