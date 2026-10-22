import json
import sys
import time
from pathlib import Path

import httpx

D = Path(sys.argv[1]) / "raw" / "citation"; D.mkdir(parents=True, exist_ok=True)
c = httpx.Client(timeout=120); E = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
def get(path, **params):
    r = c.get(f"{E}/{path}", params=params); r.raise_for_status(); time.sleep(0.5); return r
(D / "pubmed_esearch_ensembl2015.json").write_bytes(get("esearch.fcgi", db="pubmed", term="Ensembl 2015[ti] AND Nucleic Acids Res[ta]", retmode="json").content)
(D / "pubmed_esearch_dbsnp2001.json").write_bytes(get("esearch.fcgi", db="pubmed", term="dbSNP the NCBI database of genetic variation[ti]", retmode="json").content)
ids = set(json.loads((D / "pubmed_esearch_ensembl2015.json").read_text())["esearchresult"]["idlist"]) | set(json.loads((D / "pubmed_esearch_dbsnp2001.json").read_text())["esearchresult"]["idlist"]) | {"25236461"}
idl = ",".join(sorted(ids))
(D / "pubmed_esummary.json").write_bytes(get("esummary.fcgi", db="pubmed", id=idl, retmode="json").content)
(D / "pubmed_efetch.xml").write_bytes(get("efetch.fcgi", db="pubmed", id=idl, retmode="xml").content)
for acc in ("GCF_000001405.25", "GCF_000001405.40"):
    s = get("esearch.fcgi", db="assembly", term=f"{acc}[Assembly Accession]", retmode="json")
    (D / f"ncbi_assembly_esearch_{acc}.json").write_bytes(s.content)
    uid = json.loads(s.text)["esearchresult"]["idlist"]
    (D / f"ncbi_assembly_esummary_{acc}.json").write_bytes(get("esummary.fcgi", db="assembly", id=",".join(uid), retmode="json").content)
print("ok", idl)
