"""Audit the recorded public endpoint refresh without network access."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PACKET = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/science-evidence/2026-10-20-issue-2493-fixture-grch37-positions"
COMPLEMENT = str.maketrans("ACGT", "TGCA")


def audit() -> dict:
    """Verify successful response hashes and compare current reference records."""
    if not __debug__:
        raise RuntimeError("Evidence validation requires enabled assertions; omit -O/-OO")
    records = json.loads(gzip.decompress((OLD / "derived/grch37_positions.json.gz").read_bytes()))
    by_id = {r["id"]: r for r in records}
    queries = [
        json.loads(line)
        for name in ("refresh-queries.jsonl", "refresh-retry-queries.jsonl")
        for line in (PACKET / name).read_text().splitlines()
    ]
    successes = {}
    for query in queries:
        if query["ok"]:
            path = ROOT / query["payload_path"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == query["sha256"]
            successes[query["url"]] = json.loads(path.read_text())
    assert len(queries) == 61 and len(successes) == 36
    for base, assembly in (
        ("https://rest.ensembl.org", "GRCh38.p14"),
        ("https://grch37.rest.ensembl.org", "GRCh37.p13"),
    ):
        assert successes[base + "/info/data"]["releases"] == [116]
        assert successes[base + "/info/assembly/homo_sapiens"]["assembly_name"] == assembly
    results = []
    for rsid in (
        "rs11638659",
        "rs75536403",
        "rs28493303",
        "rs11901495",
        "rs2270662",
        "rs28689766",
        "rs199898426",
    ):
        old = by_id[rsid]
        chrom, p37, p38 = old["chromosome"], old["grch37_position"], old["grch38_position"]
        v37 = successes[f"https://grch37.rest.ensembl.org/variation/human/{rsid}"]
        matching = [
            m
            for m in v37["mappings"]
            if m["assembly_name"] == "GRCh37"
            and m["seq_region_name"] == chrom
            and m["start"] == m["end"] == p37
        ]
        assert len(matching) == 1 and matching[0]["strand"] == 1
        assert set(old["grch37_alleles"]) <= set(matching[0]["allele_string"].split("/"))
        mapping = successes[
            f"https://rest.ensembl.org/map/human/GRCh38/{chrom}:{p38}..{p38}:1/GRCh37"
        ]
        # Match the historical derivation's primary-chromosome filter. Patch
        # placements are retained in raw evidence but are not sample coordinates.
        primary = {str(i) for i in range(1, 23)} | {"X", "Y", "MT"}
        projections = sorted(
            [
                [m["mapped"]["seq_region_name"], m["mapped"]["start"]]
                for m in mapping["mappings"]
                if m["mapped"]["start"] == m["mapped"]["end"]
                and m["mapped"]["seq_region_name"] in primary
            ]
        )
        assert projections == old["projection"], rsid
        for m in mapping["mappings"]:
            if m["mapped"]["seq_region_name"] == chrom and m["mapped"]["start"] == p37:
                assert m["mapped"]["strand"] == old["orientation"]
        flank37 = successes[
            "https://grch37.rest.ensembl.org/sequence/region/human/"
            f"{chrom}:{p37 - 25}..{p37 + 25}:1"
        ]["seq"].upper()
        flank38 = successes.get(
            f"https://rest.ensembl.org/sequence/region/human/{chrom}:{p38 - 25}..{p38 + 25}:1"
        )
        mismatches = None
        if flank38:
            seq38 = flank38["seq"].upper()
            assert len(seq38) == len(flank37) == 51
            mismatches = {
                label: sum(
                    a != b
                    for i, (a, b) in enumerate(zip(seq38, candidate, strict=True))
                    if i != 25
                )
                for label, candidate in (
                    ("same", flank37),
                    ("reverse_complement", flank37.translate(COMPLEMENT)[::-1]),
                )
            }
            expected = "same" if old["orientation"] == 1 else "reverse_complement"
            other = "reverse_complement" if expected == "same" else "same"
            assert mismatches[expected] == 0 and mismatches[other] > 10, rsid
        results.append(
            {
                "id": rsid,
                "grch37_identity_matches": True,
                "assembly_map_matches_recorded_payload": True,
                "current_flank_mismatches": mismatches,
            }
        )
    unavailable = sorted({q["url"] for q in queries} - set(successes))
    return {
        "request_attempts": len(queries),
        "successful_distinct_urls": len(successes),
        "unavailable_urls": unavailable,
        "markers": results,
    }


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2))
