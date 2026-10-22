import gzip, json, sys, time
from pathlib import Path
import httpx
D = Path(sys.argv[1]); dl = Path(sys.argv[2]); fixture = Path(sys.argv[3])
rows = [l.split("\t") for l in fixture.read_text().splitlines() if l and not l.startswith("#")]
ids = [r[0] for r in rows if r[0].startswith("rs")]
c = httpx.Client(timeout=180, headers={"Content-Type": "application/json", "Accept": "application/json"})
def post(url, body):
    for attempt in range(6):
        r = c.post(url, content=json.dumps(body))
        if r.status_code in (429,) or r.status_code >= 500:
            print("retry", url, r.status_code); time.sleep(2 + 3 * attempt); continue
        r.raise_for_status(); return r
    r.raise_for_status()
for n, i in enumerate(range(0, len(ids), 200)):
    r = post("https://rest.ensembl.org/variation/homo_sapiens", {"ids": ids[i:i + 200]})
    (D / "raw" / "ensembl_grch38" / f"variation_batch_{n:02d}.json.gz").write_bytes(gzip.compress(r.content, mtime=0)); time.sleep(0.4)
# UCSC liftOver chain + directory README (license)
for url, name in (("https://hgdownload.soe.ucsc.edu/goldenPath/hg38/liftOver/hg38ToHg19.over.chain.gz", "hg38ToHg19.over.chain.gz"),
                  ("https://hgdownload.soe.ucsc.edu/goldenPath/hg38/liftOver/README.txt", "README.txt"),
                  ("https://hgdownload.soe.ucsc.edu/goldenPath/hg38/liftOver/md5sum.txt", "md5sum.txt")):
    r = c.get(url); r.raise_for_status(); (dl / name).write_bytes(r.content)
(D / "raw" / "ucsc" / "liftOver_README.txt").write_bytes((dl / "README.txt").read_bytes())
(D / "raw" / "ucsc" / "liftOver_md5sum.txt").write_bytes((dl / "md5sum.txt").read_bytes())
# Ensembl assembly map GRCh38 -> GRCh37 for rows without an Ensembl Variation record
need = [r for r in rows if not r[0].startswith("rs") or r[0] in {"rs62532020", "rs28596778", "rs78569271", "rs115540404", "rs12018528"}]
for rid, chrom, pos, _ in need:
    for attempt in range(6):
        r = c.get(f"https://rest.ensembl.org/map/human/GRCh38/{chrom}:{pos}..{pos}:1/GRCh37?content-type=application/json")
        if r.status_code == 429 or r.status_code >= 500: time.sleep(2 + 3 * attempt); continue
        r.raise_for_status(); break
    (D / "raw" / "ensembl_map" / f"{rid}.grch38_to_grch37.json").write_bytes(r.content); time.sleep(0.2)
print("ok", len(need))
