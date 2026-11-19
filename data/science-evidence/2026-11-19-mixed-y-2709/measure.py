"""Compare synthetic inputs on the unchanged assignment path. Run from the repository root."""

import io
import json
from pathlib import Path

import sqlalchemy as sa
import structlog

from backend.analysis.ancestry import assign_haplogroups, load_haplogroup_bundle
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, raw_variants
from backend.ingestion.parser_23andme import parse_23andme
from backend.ingestion.parser_ancestrydna import parse_ancestrydna

SOURCES = Path("scripts/y_haplogroup_source.json")
BUNDLE = Path("backend/data/panels/haplogroup_bundle.json")
# A leaf, a two-marker internal node, and an audited missing-marker pass-through.
CASES = [
    ("R1b1a1a", "R1b1a1a", "rs9786153"),
    ("R1b1a1a", "R1b1a", "rs7893073"),
    ("B2", "B", "rs2032599"),
]


def path_rows(nodes, target):
    lineage = []
    while target in nodes:
        node = nodes[target]
        lineage.append(node)
        target = node["parent"]
    return [
        dict(chrom="Y", rsid=m["rsid"], pos=m["pos"], genotype=m["allele"] * 2)
        for node in reversed(lineage)
        for m in node["markers"]
    ]


def parser_outputs(marker, mixed):
    results = {}
    for version, header in [
        ("v3", "# build 36\n"),
        ("v4", "# build 37\n"),
        ("v5", "# build 37\n" + "# synthetic header\n" * 14),
    ]:
        for genotype in (mixed, marker["allele"], marker["allele"] * 2, "--"):
            # Arbitrary parser-only position. No build-36 coordinate is used for scoring.
            stream = io.StringIO(
                header
                + "# rsid\tchromosome\tposition\tgenotype\n"
                + f"synthetic_parser_marker\tY\t1000000\t{genotype}\n"
            )
            parsed = parse_23andme(stream)
            assert parsed.version == version
            [row] = parsed.variants
            results[f"23andMe {version} Y {genotype}"] = [row.chrom, row.genotype]
    for chrom, a1, a2 in [
        ("24", *mixed),
        ("25", *mixed),
        ("24", marker["allele"], marker["allele"]),
        ("24", marker["allele"], "0"),
        ("24", "0", "0"),
    ]:
        stream = io.StringIO(
            "#AncestryDNA raw data download\nrsid\tchromosome\tposition\tallele1\tallele2\n"
            f"{marker['rsid']}\t{chrom}\t{marker['pos']}\t{a1}\t{a2}\n"
        )
        [row] = parse_ancestrydna(stream).variants
        results[f"AncestryDNA {chrom} {a1}/{a2}"] = [row.chrom, row.genotype]
    return results


def run_case(bundle, rows, table):
    # These unrelated synthetic rows only meet the production raw-table sex-evidence floor.
    padding = [
        dict(chrom="X", pos=50_000_001 + i, rsid=f"x_pad_{i}", genotype="GG") for i in range(120)
    ] + [dict(chrom="Y", pos=3_000_000 + i, rsid=f"y_pad_{i}", genotype="AA") for i in range(60)]
    engine = sa.create_engine("sqlite://")
    try:
        create_sample_tables(engine)
        with engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), padding)
            conn.execute(sa.insert(table), rows)
        [result] = [r for r in assign_haplogroups(bundle, engine) if r.tree_type == "Y"]
        return [
            result.haplogroup,
            result.defining_snps_present,
            result.defining_snps_total,
            result.confidence,
        ]
    finally:
        engine.dispose()


def main():
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(50))
    nodes = json.loads(SOURCES.read_text())["nodes"]
    bundle = load_haplogroup_bundle(BUNDLE)
    measurements = []
    for target, node_name, marker_id in CASES:
        [marker] = [m for m in nodes[node_name]["markers"] if m["rsid"] == marker_id]
        derived, ancestral = marker["allele"], marker["ancestral_allele"]
        assert derived in "ACGT" and ancestral in "ACGT" and derived != ancestral
        mixed = "".join(sorted((derived, ancestral)))
        calls = {
            "derived_single": derived,
            "derived_doubled": derived * 2,
            "ancestral_doubled": ancestral * 2,
            "mixed": mixed,
            "no_call": "--",
            "absent": None,
        }
        if marker_id == "rs9786153":
            # The issue's AA control is a non-derived base. The source identifies T as ancestral.
            calls.update(issue_control_AA="AA", issue_mixed_AC="AC")
        witness = {"target": target, "node": node_name, "marker": marker_id, "controls": {}}
        original = path_rows(nodes, target)
        assert sum(r["rsid"] == marker_id for r in original) == 1
        for label, call in calls.items():
            rows = [
                r if r["rsid"] != marker_id else {**r, "genotype": call}
                for r in original
                if r["rsid"] != marker_id or call is not None
            ]
            raw = run_case(bundle, rows, raw_variants)
            assert run_case(bundle, rows, annotated_variants) == raw
            witness["controls"][label] = raw
        measurements.append(witness)
    marker = next(m for m in nodes["R1b1a1a"]["markers"] if m["rsid"] == "rs9786153")
    source_checks = {}
    rows = path_rows(nodes, "R1b1a1a")
    for chrom, pos in [("X", marker["pos"]), ("Y", 20000), ("Y", marker["pos"] + 1)]:
        changed = [
            {**r, "chrom": chrom, "pos": pos} if r["rsid"] == marker["rsid"] else r for r in rows
        ]
        raw = run_case(bundle, changed, raw_variants)
        assert run_case(bundle, changed, annotated_variants) == raw
        source_checks[f"{chrom}:{pos}"] = raw
    print(
        json.dumps(
            {
                "bundle": bundle.version,
                "build": bundle.build,
                "witnesses": measurements,
                "parser_controls": parser_outputs(marker, "CT"),
                "same_id_other_locus": source_checks,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
