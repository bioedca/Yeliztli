#!/usr/bin/env python3
"""Fetch gnomAD v4.1 joint (exome+genome) frequencies for the gnomad_seed rows #2375 re-oriented (#2579 review).

Scope: gnomad_seed rows whose allele pair the rewrite reversed (the two assemblies' reference
bases differ) or whose rsID turned out to name a different variant from the one the old
coordinate described (vep_seed gene changed). Their unsourced frequency columns cannot be
trusted under the new orientation or identity, so they are re-sourced by rsID, as
gnomad_seed.af_asj.provenance.json already does. Responses are stored verbatim (gzip).
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

PACKET = Path(__file__).resolve().parents[1]
OUT = PACKET / "raw" / "gnomad_r4"
URL = "https://gnomad.broadinstitute.org/api"
QUERY = (
    "query($rsid:String!){ variant(rsid:$rsid, dataset: gnomad_r4){ variant_id rsids "
    "joint{ ac an homozygote_count populations{ id ac an homozygote_count } } } }"
)
REVERSED = ["rs104894396", "rs121912443", "rs16947", "rs1799950", "rs2060793", "rs25531",
            "rs5882", "rs757210", "rs776746"]
IDENTITY_CHANGED = ["rs104894396", "rs121434569", "rs121912443", "rs121913529", "rs28897727",
                    "rs63750066", "rs72552267", "rs7294", "rs76151636", "rs80358981"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rsids = sorted(set(REVERSED) | set(IDENTITY_CHANGED), key=lambda r: int(r[2:]))
    (OUT / "request.json").write_text(json.dumps(
        {"query": QUERY, "reversed": REVERSED, "identity_changed": IDENTITY_CHANGED,
         "rsids": rsids, "accessed": "2026-10-22"}, indent=1) + "\n")
    for rsid in rsids:
        target = OUT / f"{rsid}.json.gz"
        if target.exists():  # resumable: the API rate-limits (HTTP 429)
            continue
        body = json.dumps({"query": QUERY, "variables": {"rsid": rsid}}).encode()
        for attempt in range(6):
            req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    payload = resp.read()
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == 5:
                    raise
                time.sleep(30 * (attempt + 1))
        with gzip.open(target, "wb") as fh:
            fh.write(payload)
        time.sleep(10)


if __name__ == "__main__":
    main()
