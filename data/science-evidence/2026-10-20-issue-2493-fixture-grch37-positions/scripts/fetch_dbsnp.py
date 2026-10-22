import json
import sys
import time
from pathlib import Path

import httpx

D = Path(sys.argv[1])
sample = json.loads((D / "raw" / "sample_rsids.json").read_text())["rsids"]
unknown = ["rs62532020", "rs28596778", "rs78569271", "rs115540404", "rs12018528"]
c = httpx.Client(timeout=120)
base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
r = c.get(f"{base}/einfo.fcgi", params={"db": "snp", "retmode": "json"}); r.raise_for_status()
(D / "raw" / "dbsnp" / "einfo_snp.json").write_bytes(r.content); time.sleep(0.5)
for name, ids in (("sample_100", sample), ("ensembl_unknown_5", unknown)):
    r = c.get(f"{base}/esummary.fcgi", params={"db": "snp", "id": ",".join(i[2:] for i in ids), "retmode": "json"})
    r.raise_for_status()
    (D / "raw" / "dbsnp" / f"esummary_{name}.json").write_bytes(r.content); time.sleep(0.5)
print("ok")
