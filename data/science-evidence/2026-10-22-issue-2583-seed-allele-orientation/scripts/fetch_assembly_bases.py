"""Fetch the GRCh37 assembly base at every located single-base seed position (#2583).

Public reference sequence only: the request carries chromosome and position,
never a genotype. Two sources per position:

* Ensembl GRCh37 REST ``POST /sequence/region/human`` for every chromosome;
* UCSC hg19 ``getData/sequence`` for 1-22, X and Y. UCSC hg19 ``chrM`` is not
  the rCRS that GRCh37 ``MT`` uses, so MT is checked against NCBI's rCRS record
  NC_012920.1 instead.

Usage: python -I fetch_assembly_bases.py <repo-root> <evidence-dir>
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

SEEDS = ("vep_seed", "clinvar_seed", "gnomad_seed")
ENSEMBL = "https://grch37.rest.ensembl.org/sequence/region/human"
UCSC = "https://api.genome.ucsc.edu/getData/sequence?genome=hg19;chrom=chr{chrom};start={start};end={end}"
NCBI_RCRS = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    "?db=nuccore&id=NC_012920.1&rettype=fasta&retmode=text"
)


def _get(url: str, data: bytes | None = None) -> bytes:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception:  # noqa: BLE001 - retried, then re-raised
            if attempt == 4:
                raise
            time.sleep(2 + attempt * 3)
    raise AssertionError("unreachable")


def located_positions(repo: Path) -> dict[str, tuple[str, int, str, str]]:
    oracle = json.loads(
        (repo / "tests/fixtures/seed_rsid_grch37_coordinates.json").read_text(encoding="utf-8")
    )["rsids"]
    positions: dict[str, tuple[str, int, str, str]] = {}
    for table in SEEDS:
        with (repo / f"tests/fixtures/seed_csvs/{table}.csv").open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                mapping = oracle.get(row["rsid"])
                if not mapping or str(mapping["chrom"]) != row["chrom"]:
                    continue
                if int(mapping["start"]) != int(row["pos"]):
                    continue
                alleles = mapping["allele_string"].split("/")
                if all(len(a) == 1 for a in alleles) and "-" not in alleles:
                    positions[row["rsid"]] = (
                        str(mapping["chrom"]),
                        int(mapping["start"]),
                        alleles[0],
                        mapping["allele_string"],
                    )
    return positions


def main() -> None:
    repo, out = Path(sys.argv[1]), Path(sys.argv[2])
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    positions = located_positions(repo)
    rsids = sorted(positions, key=lambda r: (positions[r][0], positions[r][1], r))

    ensembl: dict[str, str] = {}
    batches = []
    for i in range(0, len(rsids), 50):
        chunk = rsids[i : i + 50]
        regions = [f"{positions[r][0]}:{positions[r][1]}..{positions[r][1]}:1" for r in chunk]
        body = json.dumps({"regions": regions}).encode()
        payload = json.loads(_get(ENSEMBL, body))
        batches.append({"regions": regions, "response": payload})
        by_region = {item["query"]: item["seq"].upper() for item in payload}
        for rsid, region in zip(chunk, regions, strict=True):
            ensembl[rsid] = by_region[region]
        time.sleep(0.5)
    (raw / "ensembl_grch37_sequence_region.json").write_text(
        json.dumps(batches, indent=1, sort_keys=True), encoding="utf-8"
    )

    second: dict[str, tuple[str, str]] = {}
    ucsc_payloads = {}
    for rsid in rsids:
        chrom, pos, _first, _string = positions[rsid]
        if chrom == "MT":
            continue
        url = UCSC.format(chrom=chrom, start=pos - 1, end=pos)
        payload = json.loads(_get(url))
        ucsc_payloads[rsid] = {"url": url, "response": payload}
        second[rsid] = ("ucsc_hg19", payload["dna"].upper())
        time.sleep(0.25)
    (raw / "ucsc_hg19_sequence.json").write_text(
        json.dumps(ucsc_payloads, indent=1, sort_keys=True), encoding="utf-8"
    )

    fasta = _get(NCBI_RCRS).decode()
    (raw / "ncbi_NC_012920.1.fasta").write_text(fasta, encoding="utf-8")
    rcrs = "".join(line.strip() for line in fasta.splitlines() if not line.startswith(">"))
    for rsid in rsids:
        chrom, pos, _first, _string = positions[rsid]
        if chrom == "MT":
            second[rsid] = ("ncbi_NC_012920.1", rcrs[pos - 1].upper())

    bases = {
        rsid: {
            "chrom": positions[rsid][0],
            "pos": positions[rsid][1],
            "allele_string": positions[rsid][3],
            "oracle_first_allele": positions[rsid][2],
            "ensembl_grch37": ensembl[rsid],
            "second_source": second[rsid][0],
            "second_source_base": second[rsid][1],
        }
        for rsid in rsids
    }
    (out / "assembly_bases.json").write_text(
        json.dumps(bases, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(raw.iterdir())}
    (out / "raw_hashes.json").write_text(json.dumps(hashes, indent=1) + "\n", encoding="utf-8")
    agree = sum(1 for b in bases.values() if b["ensembl_grch37"] == b["second_source_base"])
    first = sum(1 for b in bases.values() if b["oracle_first_allele"] == b["ensembl_grch37"])
    print(f"positions={len(bases)} sources_agree={agree} oracle_first_equals_assembly={first}")


if __name__ == "__main__":
    main()
