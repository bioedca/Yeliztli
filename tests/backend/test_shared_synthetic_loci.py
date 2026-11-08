"""The complete #2345 behavior-fixture contract, with no biological assertions."""

import csv
import json
import re
import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.annotation.clinvar import (
    SkipReason,
    annotate_sample_clinvar,
    iter_clinvar_vcf,
    load_clinvar_from_iter,
    lookup_clinvar_by_positions,
    lookup_clinvar_by_rsids,
    parse_clinvar_vcf_line,
)
from backend.annotation.gnomad import lookup_gnomad_by_rsids
from backend.annotation.vep_bundle import lookup_vep_by_rsids
from backend.db.tables import annotated_variants, raw_variants
from tests.backend.conftest import SEED_CLINVAR, SEED_RAW_VARIANTS

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RSID = "rsYELIZTLI2345001"


def _contract() -> dict:
    contract = json.loads((_FIXTURES / "shared_synthetic_loci.json").read_text())
    assert contract["scientific_variant_claims"] is False
    assert set(contract["shared_loci"]) == {_RSID}
    return contract


def _at_shared_locus(row: dict) -> bool:
    return (str(row.get("chrom")), str(row.get("pos"))) == ("1", "100000")


def test_complete_shared_seed_set_has_one_reserved_identity() -> None:
    shared = _contract()["shared_loci"]
    for source in ("vep", "gnomad"):
        with (_FIXTURES / f"seed_csvs/{source}_seed.csv").open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) >= 97
        selected = [r for r in rows if _at_shared_locus(r) or r["rsid"] in shared]
        assert selected == [shared[_RSID][source]]
        assert not any(r["rsid"] == "rs12345" for r in rows)
        assert all(re.fullmatch(r"rsYELIZTLI\d+", r["rsid"]) for r in selected)

    for source, rows in (("raw", SEED_RAW_VARIANTS), ("clinvar", SEED_CLINVAR)):
        assert rows
        selected = [r for r in rows if _at_shared_locus(r) or r["rsid"] in shared]
        assert selected == [shared[_RSID][source]]
        assert not any(r["rsid"] == "rs12345" for r in rows)


def test_shared_identity_does_not_collide_with_other_reserved_contracts() -> None:
    shared = set(_contract()["shared_loci"])
    for path in (
        "parser_synthetic_scenarios.json",
        "seed_csvs/dbnsfp_seed.contract.json",
    ):
        scenarios = json.loads((_FIXTURES / path).read_text())["scenarios"]
        assert scenarios
        assert shared.isdisjoint(scenarios)
    # The table API's generated fixtures occupy 0000..1999; pathway tests use 0/1.
    generated = {f"rsYELIZTLI{i:04d}" for i in range(2000)} | {"rsYELIZTLI0", "rsYELIZTLI1"}
    assert shared.isdisjoint(generated)
    with (_FIXTURES / "seed_csvs/dbnsfp_seed.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert shared.isdisjoint(row["rsid"] for row in rows)


def test_shared_gnomad_provenance_remains_explicitly_withheld() -> None:
    provenance = json.loads(
        (_FIXTURES / "seed_csvs/gnomad_seed.af_asj.provenance.json").read_text()
    )
    withheld = provenance["withheld_rsids"]
    assert len(withheld["rsids"]) == withheld["count"] > 0
    assert _RSID in withheld["rsids"]
    assert "rs12345" not in withheld["rsids"]
    assert _contract()["shared_loci"][_RSID]["gnomad"]["af_asj"] == ""


def test_every_synthetic_clinvar_row_is_reviewed_and_has_no_dbsnp_id() -> None:
    rows = [
        line.split("\t")
        for line in (_FIXTURES / "mini_clinvar.vcf").read_text().splitlines()
        if line and not line.startswith("#")
    ]
    synthetic = [row for row in rows if "SYNTHETIC" in row[7].split(";")]
    assert len(synthetic) == 2
    assert synthetic == _contract()["synthetic_clinvar_rows"]
    assert len({tuple(row[:5]) for row in synthetic}) == len(synthetic)
    real = [row for row in rows if row not in synthetic]
    assert real
    assert {tuple(row[:2]) for row in synthetic}.isdisjoint(tuple(row[:2]) for row in real)
    for row in synthetic:
        assert not any(field.startswith("RS=") for field in row[7].split(";"))
        record, reason = parse_clinvar_vcf_line("\t".join(row))
        assert reason is None
        assert record is not None and record.rsid is None
        assert (record.chrom, record.pos, record.ref, record.alt) == (
            row[0],
            int(row[1]),
            row[3],
            row[4],
        )


@pytest.mark.parametrize(
    ("filename", "table", "source"),
    [
        ("mini_vep_bundle.db", "vep_annotations", "vep"),
        ("mini_gnomad_af.db", "gnomad_af", "gnomad"),
    ],
)
def test_generated_shared_rows_match_the_complete_contract(
    filename: str, table: str, source: str
) -> None:
    expected = _contract()["shared_loci"][_RSID][source]
    with sqlite3.connect(f"file:{_FIXTURES / filename}?mode=ro&immutable=1", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
    assert len(rows) >= 97
    selected = [r for r in rows if _at_shared_locus(r) or r["rsid"] == _RSID]
    assert len(selected) == 1
    actual = selected[0]
    for key, value in expected.items():
        if actual[key] is None:
            assert value == ""
        elif isinstance(actual[key], float):
            assert actual[key] == float(value)
        else:
            assert str(actual[key]) == value
    assert not any(r["rsid"] == "rs12345" for r in rows)


def test_reserved_id_uses_production_vep_and_gnomad_lookups() -> None:
    for filename, lookup in (
        ("mini_vep_bundle.db", lookup_vep_by_rsids),
        ("mini_gnomad_af.db", lookup_gnomad_by_rsids),
    ):
        engine = sa.create_engine(f"sqlite:///{_FIXTURES / filename}")
        try:
            found = lookup([_RSID, "rs12345", "rs429358"], engine)
            assert set(found) == {_RSID, "rs429358"}
            row = found[_RSID]
            assert (row.ref, row.alt) == ("A", "G")
            if filename == "mini_vep_bundle.db":
                assert row.matched_by == "rsid"
                assert (row.chrom, row.pos, row.gene_symbol) == ("1", 100000, "GENE1")
            else:
                assert row.af_global == 0.021
                assert row.af_asj is None
        finally:
            engine.dispose()


@pytest.mark.parametrize(("genotype", "zygosity"), [("AG", "het"), ("AA", "hom_ref")])
def test_synthetic_clinvar_coordinate_join_and_independent_real_rsid_lookup(
    reference_engine: sa.Engine, sample_engine: sa.Engine, genotype: str, zygosity: str
) -> None:
    stats = load_clinvar_from_iter(
        iter_clinvar_vcf(_FIXTURES / "mini_clinvar.vcf"), reference_engine
    )
    assert stats.variants_loaded == 12
    by_id = lookup_clinvar_by_rsids([_RSID, "rs12345", "rs429358"], reference_engine)
    assert set(by_id) == {"rs429358"}
    assert by_id["rs429358"].matched_by == "rsid"
    by_position = lookup_clinvar_by_positions(
        [("1", 100000, _RSID), ("1", 100001, "rsYELIZTLI2345002")], reference_engine
    )
    assert set(by_position) == {_RSID}
    assert by_position[_RSID].matched_by == "chrom_pos"
    assert by_position[_RSID].clinvar_significance == "Uncertain significance"
    with sample_engine.begin() as conn:
        conn.execute(
            raw_variants.insert(),
            [
                {"rsid": _RSID, "chrom": "1", "pos": 100000, "genotype": genotype},
                {"rsid": "rs429358", "chrom": "19", "pos": 45411941, "genotype": "TC"},
            ],
        )
    result = annotate_sample_clinvar(sample_engine, reference_engine)
    assert (result.total_matched, result.matched_by_rsid, result.matched_by_position) == (2, 1, 1)
    with sample_engine.connect() as conn:
        rows = {row.rsid: row for row in conn.execute(sa.select(annotated_variants))}
    assert set(rows) == {_RSID, "rs429358"}
    assert rows[_RSID].zygosity == zygosity
    assert (rows[_RSID].ref, rows[_RSID].alt) == ("A", "G")
    assert rows[_RSID].clinvar_significance == "Uncertain significance"


@pytest.mark.parametrize("rs_value", [_RSID, "YELIZTLI2345001", "0", "-1"])
def test_reserved_identity_does_not_relax_clinvar_numeric_rs_contract(rs_value: str) -> None:
    columns = _contract()["synthetic_clinvar_rows"][0]
    record, reason = parse_clinvar_vcf_line("\t".join(columns) + f";RS={rs_value}")
    assert record is None
    assert reason == SkipReason.MALFORMED


def test_bundle_snapshot_queries_real_ids_only() -> None:
    from scripts.build_vep_seed_bundle_snapshot import annotated_rsids

    assert annotated_rsids(
        [
            {"rsid": "rs429358", "gene_symbol": "APOE"},
            {"rsid": _RSID, "gene_symbol": "GENE1"},
            {"rsid": "rs7412", "gene_symbol": ""},
        ]
    ) == ["rs429358"]
