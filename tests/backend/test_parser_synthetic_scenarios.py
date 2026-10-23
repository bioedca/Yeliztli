"""The #2582 parser scenarios use reviewed synthetic identities, not dbSNP aliases."""

import csv
import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.annotation.vep_bundle import lookup_vep_by_rsids
from backend.ingestion.parser_23andme import parse_23andme
from backend.ingestion.parser_ancestrydna import parse_ancestrydna

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_CONTRACT = _FIXTURES / "parser_synthetic_scenarios.json"
_COMPANIONS = (
    "sample_ancestrydna_v2.txt",
    "sample_ancestrydna_crlf.txt",
    "sample_ancestrydna_non_utf8_byte.txt",
)


def _scenarios() -> dict:
    scenarios = json.loads(_CONTRACT.read_text())["scenarios"]
    assert set(scenarios) == {f"rsYELIZTLI{index}" for index in range(2001, 2025)}
    previous = [scenario["previous_rsid"] for scenario in scenarios.values()]
    assert len(set(previous)) == len(previous) == 24
    return scenarios


@pytest.mark.parametrize("filename", _COMPANIONS)
def test_companions_preserve_all_reviewed_synthetic_parser_scenarios(filename: str) -> None:
    scenarios = _scenarios()
    path = _FIXTURES / filename
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        raw = {
            row["rsid"]: row
            for row in csv.DictReader(
                (line for line in handle if not line.startswith("#")), delimiter="\t"
            )
        }
    assert len(raw) == 589
    assert {rsid for rsid in raw if rsid.startswith("rsYELIZTLI")} == set(scenarios)
    assert not {scenario["previous_rsid"] for scenario in scenarios.values()} & set(raw)
    result = parse_ancestrydna(path)
    parsed = {variant.rsid: variant for variant in result.variants}
    assert len(parsed) == len(result.variants) == 589
    assert result.nocall_count == 2
    for rsid, scenario in scenarios.items():
        row = raw[rsid]
        expected = scenario["raw"]
        assert (row["chromosome"], int(row["position"]), row["allele1"], row["allele2"]) == (
            expected["chromosome"],
            expected["position"],
            expected["allele1"],
            expected["allele2"],
        )
        variant = parsed[rsid]
        expected = scenario["parsed"]
        assert (variant.chrom, variant.pos, variant.genotype) == (
            expected["chrom"],
            expected["pos"],
            expected["genotype"],
        )


def test_companion_newlines_and_invalid_byte_still_exercise_the_parser() -> None:
    lf, crlf, invalid = (_FIXTURES / filename for filename in _COMPANIONS)
    lf_bytes = lf.read_bytes()
    assert b"\r\n" not in lf_bytes
    assert crlf.read_bytes() == lf_bytes.replace(b"\n", b"\r\n")
    with pytest.raises(UnicodeDecodeError):
        invalid.read_bytes().decode("utf-8")
    expected = parse_ancestrydna(lf)
    assert len(expected.variants) == 589
    for path in (crlf, invalid):
        actual = parse_ancestrydna(path)
        assert actual.variants == expected.variants
        assert (actual.vendor, actual.version, actual.build, actual.nocall_count) == (
            expected.vendor,
            expected.version,
            expected.build,
            expected.nocall_count,
        )


@pytest.mark.parametrize(
    "filename",
    (
        "sample_23andme_v3.txt",
        "sample_23andme_v4.txt",
        "sample_23andme_v5.txt",
        "sample_not_23andme.vcf",
    ),
)
def test_other_parser_counterparts_keep_their_builds_and_non_identifier_fields(
    filename: str,
) -> None:
    counterpart = json.loads(_CONTRACT.read_text())["counterparts"][filename]
    column = counterpart["identifier_column"]
    rows = [
        line.split("\t")
        for line in (_FIXTURES / filename).read_text().splitlines()
        if line and not line.startswith("#")
    ]
    assert rows
    by_id = {row[column]: row for row in rows if len(row) > column}
    assert counterpart["rows"]
    for entry in counterpart["rows"]:
        expected = list(entry["columns"])
        expected[column] = entry["rsid"]
        assert by_id[entry["rsid"]] == expected
        assert entry["previous_rsid"] not in by_id
    if "parsed" in counterpart:
        result = parse_23andme(_FIXTURES / filename)
        assert result.build == counterpart["build"]
        parsed = {variant.rsid: variant for variant in result.variants}
        for rsid, expected in counterpart["parsed"].items():
            actual = parsed[rsid]
            assert (actual.chrom, actual.pos, actual.genotype) == (
                expected["chrom"],
                expected["pos"],
                expected["genotype"],
            )


def test_vep_seed_has_exactly_the_reviewed_synthetic_scenarios() -> None:
    scenarios = _scenarios()
    with (_FIXTURES / "seed_csvs/vep_seed.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) > 600
    synthetic = {
        row["rsid"]: row
        for row in rows
        if not (row["rsid"].startswith("rs") and row["rsid"][2:].isdigit())
    }
    assert set(synthetic) == set(scenarios), "unreviewed synthetic VEP rows or missing scenarios"
    old = {scenario["previous_rsid"] for scenario in scenarios.values()}
    assert not old & {row["rsid"] for row in rows}
    for rsid, scenario in scenarios.items():
        row = synthetic[rsid]
        assert {key: value for key, value in row.items() if key != "rsid"} == scenario["vep"]
        assert all(
            row[field] == ""
            for field in ("gene_symbol", "transcript_id", "hgvs_coding", "hgvs_protein")
        )


def test_mini_vep_production_rsid_lookup_covers_the_renamed_parser_rows() -> None:
    scenarios = _scenarios()
    old = [scenario["previous_rsid"] for scenario in scenarios.values()]
    engine = sa.create_engine(f"sqlite:///{_FIXTURES / 'mini_vep_bundle.db'}")
    try:
        found = lookup_vep_by_rsids([*scenarios, *old], engine)
        assert set(found) == set(scenarios)
        for rsid, scenario in scenarios.items():
            row = found[rsid]
            assert row.matched_by == "rsid"
            assert (row.ref, row.alt) == (scenario["vep"]["ref"], scenario["vep"]["alt"])
            assert row.gene_symbol is None
    finally:
        engine.dispose()
