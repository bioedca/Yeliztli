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

Every path must lie inside the repository (so the committed oracle never names
a host-specific location), the three paths must be distinct, and every response
is validated -- symbol, assembly, strand, gene id -- before it is retained or
reduced. Any usage or validation error exits non-zero and writes nothing.

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
import datetime as dt
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
_ASSEMBLY = "GRCh37"

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


# A complete Ensembl human gene identifier: the ENSG prefix and exactly eleven
# digits, with an optional version suffix. A bare prefix such as "ENSG_NOT_A_GENE"
# is not an identifier and must not be retained or reduced.
_ENSEMBL_GENE_ID = re.compile(r"ENSG\d{11}(?:\.\d+)?")


def validate_response(gene: str, payload: object) -> dict:
    """Return ``payload`` if it is a GRCh37 lookup/symbol record for ``gene``.

    Raises ``ValueError`` naming the symbol and the offending field otherwise.
    Nothing is coerced: a strand outside {1, -1}, a foreign assembly, a record for
    another symbol or a malformed gene id is refused rather than reduced (#2364 review).
    """
    if not isinstance(payload, dict):
        raise ValueError(f"{gene}: response is {type(payload).__name__}, not a JSON object")
    if payload.get("display_name") != gene:
        raise ValueError(f"{gene}: display_name is {payload.get('display_name')!r}, not {gene!r}")
    if payload.get("assembly_name") != _ASSEMBLY:
        raise ValueError(
            f"{gene}: assembly_name is {payload.get('assembly_name')!r}, not {_ASSEMBLY!r}"
        )
    strand = payload.get("strand")
    if isinstance(strand, bool) or strand not in (1, -1):
        raise ValueError(f"{gene}: strand is {strand!r}, not 1 or -1")
    gene_id = payload.get("id")
    if not isinstance(gene_id, str) or not _ENSEMBL_GENE_ID.fullmatch(gene_id):
        raise ValueError(f"{gene}: id is {gene_id!r}, not a complete Ensembl gene identifier")
    return payload


def reduce_response(payload: dict) -> dict[str, str]:
    """The snapshot entry for one validated response: strand sign and gene id."""
    return {"strand": "+" if payload["strand"] == 1 else "-", "ensembl_gene_id": payload["id"]}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _in_repo(parser: argparse.ArgumentParser, flag: str, path: Path, root: Path) -> str:
    """Repo-relative POSIX form of ``path``; a usage error when it lies outside."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        parser.error(f"{flag} must lie inside the repository root {root}; got {path}")
    raise AssertionError("unreachable")  # pragma: no cover - parser.error exits


def _replay_fetcher(parser: argparse.ArgumentParser, retained: Path) -> Fetcher:
    try:
        responses = json.loads(retained.read_text(encoding="utf-8"))["responses"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(f"--responses-from {retained} is not a retained-responses file: {exc}")
    if not isinstance(responses, dict):
        parser.error(f"--responses-from {retained}: `responses` is not an object")
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
            "that names no source-native payload cannot be audited); must lie inside "
            "the repository"
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_OUT,
        metavar="PATH",
        help="snapshot to write (default: tests/fixtures/vep_gene_strand_snapshot.json)",
    )
    parser.add_argument(
        "--responses-from",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "replay retained verbatim responses (same shape as --evidence-out writes) "
            "instead of fetching live; must lie inside the repository; requires --accessed"
        ),
    )
    parser.add_argument(
        "--accessed",
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "date the replayed responses were fetched; valid only with --responses-from "
            "(a live fetch records today's UTC date itself)"
        ),
    )
    return parser


def _validated_accessed(parser: argparse.ArgumentParser, value: str, today: dt.date) -> str:
    if not _ISO_DATE.match(value):
        parser.error("--accessed must be YYYY-MM-DD")
    try:
        accessed = dt.date.fromisoformat(value)
    except ValueError:
        parser.error(f"--accessed {value} is not a calendar date")
    if accessed > today:
        parser.error(f"--accessed {value} is in the future (today is {today.isoformat()} UTC)")
    return accessed.isoformat()


def main(argv: list[str] | None = None, *, fetch: Fetcher = _lookup, root: Path = _ROOT) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    today = dt.datetime.now(dt.UTC).date()

    # Validate everything before touching the filesystem: a usage error writes nothing.
    if args.accessed is not None and args.responses_from is None:
        parser.error("--accessed is only valid with --responses-from; a live fetch dates itself")
    if args.responses_from is not None and args.accessed is None:
        parser.error(
            "--responses-from requires --accessed (the date those responses were fetched)"
        )
    evidence_rel = _in_repo(parser, "--evidence-out", args.evidence_out, root)
    replay_rel = (
        _in_repo(parser, "--responses-from", args.responses_from, root)
        if args.responses_from is not None
        else None
    )
    resolved = {"--out": args.out.resolve(), "--evidence-out": args.evidence_out.resolve()}
    if args.responses_from is not None:
        resolved["--responses-from"] = args.responses_from.resolve()
    if len(set(resolved.values())) != len(resolved):
        parser.error(
            "output and input paths must be distinct, got "
            + ", ".join(f"{flag}={path}" for flag, path in resolved.items())
        )
    if args.responses_from is not None:
        accessed = _validated_accessed(parser, args.accessed, today)
        fetch = _replay_fetcher(parser, args.responses_from)
    else:
        accessed = today.isoformat()
    live = fetch is _lookup

    strands: dict[str, dict[str, str]] = {}
    responses: dict[str, dict] = {}
    failures: list[str] = []
    invalid: list[str] = []
    genes = _genes()
    print(
        f"Resolving {len(genes)} genes cited by vep_seed.csv "
        f"(excluding synthetic {sorted(SYNTHETIC_GENES)})"
        + (f" by replaying {replay_rel}" if replay_rel else ""),
        file=sys.stderr,
    )
    for gene in genes:
        payload = fetch(gene)
        if payload is None:
            failures.append(gene)
            continue
        try:
            responses[gene] = validate_response(gene, payload)
        except ValueError as exc:
            invalid.append(str(exc))
            continue
        strands[gene] = reduce_response(responses[gene])
        if live:
            time.sleep(0.12)

    if failures or invalid:
        # Fail closed: a partial or coerced snapshot would silently misstate the guard.
        if failures:
            print(f"ERROR: could not resolve {len(failures)} genes: {failures}", file=sys.stderr)
        for message in invalid:
            print(f"ERROR: invalid response for {message}", file=sys.stderr)
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
            f" Replayed from {replay_rel} (sha256 {_sha256(args.responses_from)}) "
            "rather than fetched live."
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
        "assembly": _ASSEMBLY,
        "accessed": accessed,
        "gene_count": len(strands),
        "fetch_failures": 0,
        "synthetic_excluded": sorted(SYNTHETIC_GENES),
        "generator": "scripts/build_vep_strand_snapshot.py",
        "evidence_path": evidence_rel,
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
            "path": replay_rel,
            "sha256": _sha256(args.responses_from),
        }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"_provenance": provenance, "strands": dict(sorted(strands.items()))}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {args.out} ({len(strands)} genes) and {evidence_rel} ({len(responses)} responses)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
