"""Fetch +/-25 bp of GRCh38 and GRCh37 assembly sequence around single-route rows (#2493)."""
import gzip
import json
import sys
import time
from pathlib import Path

import httpx

D = Path(sys.argv[1])
records = json.loads(gzip.decompress((D / "derived" / "grch37_positions.json.gz").read_bytes()))
todo = [r for r in records if r.get("orientation_basis") != "identity+projection"]
c = httpx.Client(timeout=60, headers={"Accept": "application/json"})
out, log = [], []
def get(url):
    for attempt in range(8):
        try:
            r = c.get(url)
        except httpx.HTTPError as exc:
            log.append({"url": url, "attempt": attempt, "error": type(exc).__name__}); time.sleep(3 + 3 * attempt); continue
        if r.status_code == 429 or r.status_code >= 500:
            log.append({"url": url, "attempt": attempt, "status": r.status_code}); time.sleep(3 + 3 * attempt); continue
        return r
    raise SystemExit(f"gave up on {url}")
for rec in todo:
    ch, p38, p37 = rec["chromosome"], rec["grch38_position"], rec["grch37_position"]
    for build, host, pos in (("GRCh38", "https://rest.ensembl.org", p38), ("GRCh37", "https://grch37.rest.ensembl.org", p37)):
        url = f"{host}/sequence/region/human/{ch}:{pos - 25}..{pos + 25}:1?content-type=application/json"
        r = get(url)
        out.append({"id": rec["id"], "build": build, "url": url, "status": r.status_code, "body": r.text})
        time.sleep(0.15)
(D / "raw" / "ensembl_flanks" / "flanks.jsonl.gz").write_bytes(gzip.compress(("\n".join(json.dumps(x, sort_keys=True) for x in out) + "\n").encode(), mtime=0))
(D / "raw" / "ensembl_flanks" / "transient_failures.json").write_text(json.dumps(log, indent=1) + "\n")
print("rows", len(todo), "requests", len(out), "transient", len(log))
