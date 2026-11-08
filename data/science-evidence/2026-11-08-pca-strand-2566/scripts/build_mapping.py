"""Reproduce the shipped PCA mapping from recorded public reference evidence.

Run with --check (default) or --write from any working directory. No network,
sample genotypes, fixture oracle, or changes to the training NPZ are involved.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
PACKET = Path(__file__).resolve().parents[1]
HISTORICAL = ROOT / "data/science-evidence/2026-10-20-issue-2493-fixture-grch37-positions"
BUNDLE = ROOT / "backend/data/panels/ancestry_pca_bundle.npz"
COMPLEMENT = str.maketrans("ACGT", "TGCA")
COLUMNS = (
    "rsid",
    "chrom",
    "grch38_position",
    "grch38_ref",
    "grch38_alt",
    "grch37_position",
    "grch37_strand",
    "grch37_ref",
    "grch37_alt",
    "evidence",
)


def build() -> tuple[bytes, bytes]:
    historical_manifest = json.loads((HISTORICAL / "queries.json").read_text())
    assert len(historical_manifest["payloads"]) == historical_manifest["payload_count"] == 109
    for relative, digest in historical_manifest["payloads"].items():
        assert hashlib.sha256((HISTORICAL / relative).read_bytes()).hexdigest() == digest, relative

    # An explicit dbSNP alignment direction resolves the one quad-allelic AIM
    # whose allele membership is uninformative and whose single-base map is empty.
    refsnp = json.loads((PACKET / "raw/refsnp-rs2270662.json").read_text())
    assert refsnp["refsnp_id"] == "2270662"
    placements = refsnp["primary_snapshot_data"]["placements_with_allele"]
    selected = {}
    for assembly, accession, sequence, position in (
        ("GRCh38.p14", "GCF_000001405.40", "NC_000017.11", 36861944),
        ("GRCh37.p13", "GCF_000001405.25", "NC_000017.10", 35219205),
    ):
        matches = [p for p in placements if p["seq_id"] == sequence]
        assert len(matches) == 1
        p = matches[0]
        annot = p["placement_annot"]
        assert any(
            a["assembly_name"] == assembly
            and a["assembly_accession"] == accession
            and a["is_chromosome"]
            and not a["is_alt"]
            and not a["is_patch"]
            for a in annot["seq_id_traits_by_assembly"]
        )
        assert {a["allele"]["spdi"]["position"] + 1 for a in p["alleles"]} == {position}
        assert annot["is_aln_opposite_orientation"] is False
        selected[assembly] = p
    assert selected["GRCh38.p14"]["is_ptlp"] is True

    records = json.loads(
        gzip.decompress((HISTORICAL / "derived/grch37_positions.json.gz").read_bytes())
    )
    assert len(records) == 5000
    assert len({r["id"] for r in records}) == len(records)
    out = io.StringIO(newline="")
    writer = csv.writer(out, delimiter="\t", lineterminator="\n")
    writer.writerow(COLUMNS)
    with np.load(BUNDLE, allow_pickle=False) as data:
        markers = zip(
            data["aim_rsids_23andme"],
            data["aim_chroms"],
            data["aim_positions_grch38"],
            data["aim_a2"],
            data["aim_a1"],
            strict=True,
        )
        for r, (rsid, chrom, pos, ref, alt) in zip(records, markers, strict=True):
            assert (r["id"], r["chromosome"], r["grch38_position"]) == (rsid, chrom, pos)
            strand = r["orientation"]
            assert strand in (-1, 1)
            votes = [r.get("orientation_" + k) for k in ("identity", "projection", "flank")]
            votes = [v for v in votes if v in (-1, 1)]
            basis = r["orientation_basis"]
            if rsid == "rs2270662":
                assert r["grch37_position"] == 35219205 and strand == 1
                assert r["flank_mismatches"] == {"same": 0, "reverse_complement": 34}
                votes.append(1)  # Both assembly placements align forward to the PTLP.
                basis = "dbsnp-alignment+flank"
            assert len(votes) >= 2 and set(votes) == {strand}, rsid
            sample_pair = [a.translate(COMPLEMENT) if strand == -1 else str(a) for a in (ref, alt)]
            assert sample_pair == r["grch37_alleles"], rsid
            assert set(sample_pair) <= set("ACGT") and len(set(sample_pair)) == 2
            assert r["grch37_position"] > 0
            writer.writerow(
                (rsid, chrom, pos, ref, alt, r["grch37_position"], strand, *sample_pair, basis)
            )
    table = out.getvalue().encode()
    metadata = {
        "schema_version": 1,
        "mapping_version": "grch37-pca-v1",
        "training_build": "GRCh38",
        "sample_build": "GRCh37",
        "sample_strand": "+",
        "marker_count": len(records),
        "bundle_sha256": hashlib.sha256(BUNDLE.read_bytes()).hexdigest(),
        "mapping_sha256": hashlib.sha256(table).hexdigest(),
        "evidence_packet": str(PACKET.relative_to(ROOT)),
        "historical_evidence_packet": str(HISTORICAL.relative_to(ROOT)),
    }
    return table, (json.dumps(metadata, indent=2) + "\n").encode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    table, metadata = build()
    for suffix, contents in ((".grch37.tsv", table), (".grch37.json", metadata)):
        path = BUNDLE.with_suffix(suffix)
        if args.write:
            path.write_bytes(contents)
        else:
            assert path.read_bytes() == contents, f"Mapping is not reproducible: {path.name}"
    print(
        "Verified all 5,000 AIMs, 109 historical payload hashes, and the explicit dbSNP alignment."
    )


if __name__ == "__main__":
    main()
