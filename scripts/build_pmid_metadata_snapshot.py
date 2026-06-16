#!/usr/bin/env python3
"""Build the offline PMID → metadata snapshot for the citation-provenance guard (#365).

Generates ``tests/fixtures/pmid_metadata_snapshot.json``: a checked-in map of
every panel-cited PMID → ``{title, journal, year}``, used by the *offline
topic-consistency* half of the citation-provenance guard (the complement to the
#358 denylist). The snapshot is **committed data**, never fetched at test time —
regenerate it deliberately by running this script (it needs network).

It reuses the guard's PMID-extraction path (``all_panel_pmids`` /
``all_proxy_pmids`` / ``all_indel_polarity_pmids`` in
``tests/backend/test_citation_provenance_guard.py``) so the snapshot and the
offline check cover exactly the same PMIDs (per #277/#365/#673).

Usage::

    python scripts/build_pmid_metadata_snapshot.py [--accessed YYYY-MM-DD]

Source: NCBI E-utilities ``esummary`` (db=pubmed). Be polite: batches of 200,
brief pauses between requests.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_TESTS = _REPO / "tests" / "backend"
_SNAPSHOT = _REPO / "tests" / "fixtures" / "pmid_metadata_snapshot.json"
_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
_BATCH = 200


def _collect_all_pmids() -> set[str]:
    """All panel + HLA-proxy + indel-polarity PMIDs, via shared collectors."""
    sys.path.insert(0, str(_TESTS))
    from test_citation_provenance_guard import (
        all_indel_polarity_pmids,
        all_panel_pmids,
        all_proxy_pmids,
    )

    pmids: set[str] = set(all_proxy_pmids())
    for panel_pmids in all_panel_pmids().values():
        pmids |= panel_pmids
    for indel_pmids in all_indel_polarity_pmids().values():
        pmids |= indel_pmids
    # Only real, numeric PubMed IDs can be resolved.
    return {p for p in pmids if p.isdigit()}


def _fetch_batch(pmids: list[str]) -> dict[str, dict[str, str]]:
    data = urllib.parse.urlencode(
        {"db": "pubmed", "id": ",".join(pmids), "retmode": "json"}
    ).encode()
    req = urllib.request.Request(_ESUMMARY, data=data)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed NCBI host)
            payload = json.loads(resp.read().decode())
    except OSError as exc:
        # OSError is the superset of urllib URLError/HTTPError, socket read-phase
        # TimeoutError, and connection errors — so a stalled response can't crash
        # unhandled despite the 60s timeout.
        raise SystemExit(f"ERROR: NCBI esummary request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"ERROR: NCBI esummary returned non-JSON for {len(pmids)} ids: {exc}"
        ) from exc
    result = payload.get("result", {})
    out: dict[str, dict[str, str]] = {}
    for pmid in result.get("uids", []):
        rec = result[pmid]
        title = (rec.get("title") or "").strip()
        if not title:
            # esummary returns an empty record for invalid/withdrawn PMIDs; omit
            # them so the snapshot only holds resolvable citations (the caller
            # records the gap in provenance.unresolved_pmids).
            continue
        out[pmid] = {
            "title": title,
            "journal": (rec.get("source") or "").strip(),
            "year": (rec.get("pubdate") or "")[:4].strip(),
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--accessed",
        required=True,
        help="Access date (YYYY-MM-DD) recorded in the snapshot provenance.",
    )
    args = parser.parse_args()

    pmids = sorted(_collect_all_pmids(), key=int)
    print(f"Collected {len(pmids)} numeric panel/proxy/indel PMIDs", file=sys.stderr)

    metadata: dict[str, dict[str, str]] = {}
    for i in range(0, len(pmids), _BATCH):
        batch = pmids[i : i + _BATCH]
        metadata.update(_fetch_batch(batch))
        print(f"  fetched {min(i + _BATCH, len(pmids))}/{len(pmids)}", file=sys.stderr)
        time.sleep(1)

    unresolved = sorted(set(pmids) - set(metadata), key=int)
    if unresolved:
        print(
            f"WARNING: {len(unresolved)} PMIDs did not resolve to a title "
            f"(invalid/withdrawn?): {unresolved}",
            file=sys.stderr,
        )

    snapshot = {
        "_provenance": {
            "source": "NCBI E-utilities esummary (db=pubmed)",
            "accessed": args.accessed,
            "pmid_count": len(metadata),
            "generator": "scripts/build_pmid_metadata_snapshot.py",
            "note": (
                "Committed offline reference for the citation topic-consistency "
                "guard (#365). Regenerate deliberately; never fetched at test time."
            ),
            # PMIDs cited by panels that esummary has no title for (invalid/withdrawn);
            # omitted from `pmids` so the snapshot only holds resolvable citations.
            "unresolved_pmids": unresolved,
        },
        "pmids": {pmid: metadata[pmid] for pmid in sorted(metadata, key=int)},
    }

    _SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    _SNAPSHOT.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(metadata)} entries → {_SNAPSHOT.relative_to(_REPO)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
