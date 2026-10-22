"""Project every fixture row's current (GRCh38) coordinate to GRCh37 with Ensembl's assembly map (#2493)."""
import gzip
import json
import sys
import time
from pathlib import Path

import httpx

D = Path(sys.argv[1]); fixture = Path(sys.argv[2])
rows = [l.split("\t") for l in fixture.read_text().splitlines() if l and not l.startswith("#")]
c = httpx.Client(timeout=60, headers={"Accept": "application/json"})
log = []
batch, n = [], 0
def flush():
    global batch, n
    if batch:
        (D / "raw" / "ensembl_map" / f"map_batch_{n:02d}.jsonl.gz").write_bytes(
            gzip.compress(("\n".join(json.dumps(x, sort_keys=True) for x in batch) + "\n").encode(), mtime=0))
        batch, n = [], n + 1
for rid, chrom, pos, _ in rows:
    url = f"https://rest.ensembl.org/map/human/GRCh38/{chrom}:{pos}..{pos}:1/GRCh37?content-type=application/json"
    for attempt in range(8):
        try:
            r = c.get(url)
        except httpx.HTTPError as exc:
            log.append({"id": rid, "attempt": attempt, "error": type(exc).__name__}); time.sleep(3 + 3 * attempt); continue
        if r.status_code == 429 or r.status_code >= 500:
            log.append({"id": rid, "attempt": attempt, "status": r.status_code})
            time.sleep(float(r.headers.get("Retry-After", 2 + 3 * attempt))); continue
        break
    batch.append({"id": rid, "url": url, "status": r.status_code, "body": r.text})
    if len(batch) == 200: flush()
    time.sleep(0.07)
flush()
(D / "raw" / "ensembl_map" / "transient_failures.json").write_text(json.dumps(log, indent=1) + "\n")
print("done", len(rows), "transient", len(log))
