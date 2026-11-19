"""Reproduce the historical rsID lookup at two positions using an invented call."""

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
from probe_release_projection import read_only

from backend.annotation.engine import _lookup_vep
from backend.annotation.vep_bundle import lookup_vep_by_rsids


def observe(release: Path) -> dict:
    marker = "rs11431378"
    synthetic_call = "TC"
    release_uri = read_only(
        release, "c8e5f162e1872ecd0ff94408c66ce0874b79dea5f4f645c758d2c45c3b6cc4d3"
    )
    legacy_uri = read_only(
        Path("bundles/vep_bundle.db"),
        "1786b5bc1a6f5a0440239f40d5f5ac69d15ce213015a9cbf11affa05bbedfff0",
    )
    measured = {}
    engine = sa.create_engine("sqlite://", creator=lambda: sqlite3.connect(release_uri, uri=True))
    try:
        for name, source in [("legacy", legacy_uri), ("v4", release_uri)]:
            with closing(sqlite3.connect(source, uri=True)) as conn:
                (chrom_pos,) = conn.execute(
                    "SELECT DISTINCT chrom,pos FROM vep_annotations WHERE rsid=?", (marker,)
                ).fetchall()
            chrom, pos = chrom_pos
            call = SimpleNamespace(chrom=chrom, pos=pos, genotype=synthetic_call)
            lower = lookup_vep_by_rsids(
                [marker],
                engine,
                genotype_by_rsid={marker: synthetic_call},
                coordinate_by_rsid={marker: chrom_pos},
            )
            adapted = _lookup_vep([marker], {marker: call}, engine)
            if set(lower) != {marker} or set(adapted) != {marker}:
                raise ValueError("Unexpected marker set in historical lookup")
            annotation = lower[marker]
            row = adapted[marker]
            measured[name] = {
                "input": {"chrom": chrom, "pos": pos, "synthetic_genotype": synthetic_call},
                "lookup_vep_by_rsids": {
                    "matched_by": annotation.matched_by,
                    "allele_unambiguous": annotation.allele_unambiguous,
                    "chrom": annotation.chrom,
                    "pos": annotation.pos,
                    "ref": annotation.ref,
                    "alt": annotation.alt,
                    "hgvs_coding": annotation.hgvs_coding,
                },
                "engine_adapter": {
                    "allele_identity": row["vep_allele_identity"],
                    "gene_symbol": row["gene_symbol"],
                    "consequence": row["consequence"],
                    "hgvs_coding": row["hgvs_coding"],
                },
            }
    finally:
        engine.dispose()
    result = {"rsid": marker, "measurements": measured}
    packet = json.loads((Path(__file__).parent / "raw/release-asset.json").read_text())
    if result != packet["synthetic_rsid_lookup_at_two_positions"]:
        raise ValueError("One or both lookup layers differ from the historic measurement")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    print(json.dumps(observe(parser.parse_args().release), indent=2))
