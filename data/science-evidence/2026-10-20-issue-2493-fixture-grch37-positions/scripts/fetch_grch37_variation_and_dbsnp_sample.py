"""Fetch public GRCh37/GRCh38 coordinate evidence for the synthetic fixture's rsIDs (#2493)."""
import gzip
import json
import random
import sys
import time
from pathlib import Path

import httpx

out = Path(sys.argv[1]); fixture = Path(sys.argv[2])
ids = []
for line in fixture.read_text().splitlines():
    if line and not line.startswith("#"):
        rsid = line.split("\t")[0]
        if rsid.startswith("rs"):
            ids.append(rsid)
print("public rsIDs:", len(ids))
client = httpx.Client(timeout=120, headers={"Content-Type": "application/json", "Accept": "application/json"})

def save(path, content):
    path.write_bytes(content)

for host, sub in (("https://grch37.rest.ensembl.org", "ensembl_grch37"), ("https://rest.ensembl.org", "ensembl_grch38")):
    r = client.get(f"{host}/info/data/?content-type=application/json"); r.raise_for_status()
    save(out / "raw" / sub / "info_data.json", r.content)
    r = client.get(f"{host}/info/assembly/homo_sapiens?content-type=application/json"); r.raise_for_status()
    save(out / "raw" / sub / "info_assembly.json", r.content)

# Full GRCh37 mapping, batches of 200 (the endpoint's documented POST limit).
for n, i in enumerate(range(0, len(ids), 200)):
    batch = ids[i:i + 200]
    for attempt in range(5):
        r = client.post("https://grch37.rest.ensembl.org/variation/homo_sapiens", content=json.dumps({"ids": batch}))
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 + attempt * 3); continue
        r.raise_for_status(); break
    save(out / "raw" / "ensembl_grch37" / f"variation_batch_{n:02d}.json.gz", gzip.compress(r.content, mtime=0))
    time.sleep(0.4)

# GRCh38 confirmation sample: what the fixture currently carries.
rng = random.Random(2493)
sample = sorted(rng.sample(ids, 100))
r = client.post("https://rest.ensembl.org/variation/homo_sapiens", content=json.dumps({"ids": sample})); r.raise_for_status()
save(out / "raw" / "ensembl_grch38" / "variation_sample_100.json", r.content)
(out / "raw" / "sample_rsids.json").write_text(json.dumps({"seed": 2493, "rsids": sample}, indent=1) + "\n")
print("done")
