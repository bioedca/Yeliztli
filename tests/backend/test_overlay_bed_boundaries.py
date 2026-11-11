"""Synthetic point-matching regressions for BED overlays (issue #2470)."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from backend.annotation.vcfanno_runner import (
    apply_overlay,
    get_overlay_results,
    parse_bed_overlay,
    parse_vcf_overlay,
)
from backend.db.tables import annotated_variants, raw_variants


@pytest.mark.parametrize("table", [annotated_variants, raw_variants], ids=["annotated", "raw"])
@pytest.mark.parametrize("chrom,bed_chrom", [("17", "chr17"), ("MT", "chrM")])
@pytest.mark.parametrize(
    "start,end,positions,expected",
    [
        (100, 200, [100, 101, 150, 200, 201], [101, 150, 200]),
        (199, 200, [199, 200, 201], [200]),
        (0, 1, [1, 2], [1]),
    ],
    ids=["both-edges", "single-base", "first-base"],
)
def test_bed_matches_one_based_positions(
    sample_engine: sa.Engine,
    table: sa.Table,
    chrom: str,
    bed_chrom: str,
    start: int,
    end: int,
    positions: list[int],
    expected: list[int],
) -> None:
    """Parse/apply/read includes both covered edges and excludes their neighbors."""
    rows = [
        {"rsid": f"synthetic_pos_{pos}", "chrom": chrom, "pos": pos, "genotype": "AA"}
        for pos in positions
    ]
    rows.append({"rsid": "synthetic_other_chrom", "chrom": "18", "pos": end, "genotype": "AA"})
    if table is annotated_variants:
        for row in rows:
            row.update(ref="A", alt="G", annotation_coverage=0)
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(table), rows)

    parsed = parse_bed_overlay(f"#chrom\tstart\tend\tlabel\n{bed_chrom}\t{start}\t{end}\tinside\n")
    assert len(parsed.records) == 1
    assert (parsed.records[0].start, parsed.records[0].end) == (start, end)
    applied = apply_overlay(parsed, 1, "Synthetic boundary", sample_engine)
    results = get_overlay_results(1, sample_engine)

    assert results
    assert sorted(results, key=lambda row: row["rsid"]) == sorted(
        [{"rsid": f"synthetic_pos_{pos}", "overlay_id": 1, "label": "inside"} for pos in expected],
        key=lambda row: row["rsid"],
    )
    assert applied.records_checked == 1
    assert applied.variants_matched == len(expected)


@pytest.mark.parametrize("table", [annotated_variants, raw_variants], ids=["annotated", "raw"])
def test_overlapping_bed_records_keep_first_matching_annotations(
    sample_engine: sa.Engine, table: sa.Table
) -> None:
    """Adjacent boundaries and overlapping records preserve first-match precedence."""
    rows = [
        {"rsid": f"synthetic_pos_{pos}", "chrom": "17", "pos": pos, "genotype": "AA"}
        for pos in (100, 101, 200, 201, 202, 203)
    ]
    if table is annotated_variants:
        for row in rows:
            row.update(ref="A", alt="G", annotation_coverage=0)
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(table), rows)
    parsed = parse_bed_overlay(
        "#chrom\tstart\tend\tlabel\n"
        "chr17\t100\t200\tfirst\n"
        "chr17\t199\t201\tsecond\n"
        "chr17\t201\t202\tadjacent\n"
    )
    applied = apply_overlay(parsed, 2, "Synthetic overlap", sample_engine)
    results = get_overlay_results(2, sample_engine)
    assert len(results) == applied.variants_matched == 4
    assert applied.records_checked == 3
    assert {row["rsid"]: row["label"] for row in results} == {
        "synthetic_pos_101": "first",
        "synthetic_pos_200": "first",
        "synthetic_pos_201": "second",
        "synthetic_pos_202": "adjacent",
    }


def test_bed_keeps_point_matching_for_multibase_ref(sample_engine: sa.Engine) -> None:
    """A REF span reaching the region cannot make an outside anchor match."""
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(annotated_variants),
            [
                {
                    "rsid": "synthetic_outside_anchor",
                    "chrom": "17",
                    "pos": 99,
                    "ref": "AAAA",
                    "alt": "A",
                    "genotype": "AA",
                    "annotation_coverage": 0,
                },
                {
                    "rsid": "synthetic_inside_anchor",
                    "chrom": "17",
                    "pos": 101,
                    "ref": "AAAA",
                    "alt": "A",
                    "genotype": "AA",
                    "annotation_coverage": 0,
                },
            ],
        )
    parsed = parse_bed_overlay("chr17\t100\t102\tpoint\n")
    applied = apply_overlay(parsed, 3, "Synthetic point matching", sample_engine)
    assert applied.variants_matched == 1
    assert get_overlay_results(3, sample_engine) == [
        {"rsid": "synthetic_inside_anchor", "overlay_id": 3, "bed_col_4": "point"}
    ]


@pytest.mark.parametrize("table", [annotated_variants, raw_variants], ids=["annotated", "raw"])
def test_vcf_keeps_exact_one_based_position(sample_engine: sa.Engine, table: sa.Table) -> None:
    """BED conversion must never shift VCF coordinates or its raw fallback."""
    rows = [
        {"rsid": f"synthetic_pos_{pos}", "chrom": "17", "pos": pos, "genotype": "AG"}
        for pos in (199, 200, 201)
    ]
    if table is annotated_variants:
        for row in rows:
            row.update(ref="A", alt="G", annotation_coverage=0)
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(table), rows)
    parsed = parse_vcf_overlay(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "17\t200\t.\tA\tG\t.\tPASS\tLABEL=exact\n"
    )
    applied = apply_overlay(parsed, 4, "Synthetic VCF control", sample_engine)
    assert applied.variants_matched == 1
    assert get_overlay_results(4, sample_engine) == [
        {"rsid": "synthetic_pos_200", "overlay_id": 4, "LABEL": "exact"}
    ]
