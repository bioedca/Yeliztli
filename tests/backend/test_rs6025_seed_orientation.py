"""Source-backed GRCh37 fixture identity at rs6025 (#2541), not clinical validation."""

import csv
import json
import shutil
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.annotation.clinvar import (
    _normalize_clinvar_significance,
    _review_status_to_stars,
    lookup_clinvar_by_positions,
    lookup_clinvar_by_rsids,
)
from backend.annotation.gnomad import lookup_gnomad_by_positions
from backend.annotation.vep_bundle import lookup_vep_by_positions
from backend.db.reference_schema import ensure_reference_schema_current
from scripts.build_vep_seed_bundle_snapshot import MIRRORED_FIELDS

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES = _ROOT / "tests/fixtures"
_EVIDENCE = _ROOT / "data/science-evidence/2026-11-08-rs6025-orientation-2541"
_IDENTITY = ("1", 169519049, "T", "C")
_POPULATIONS = {
    "afr": "afr",
    "amr": "amr",
    "asj": "asj",
    "eas": "eas",
    "eur": "nfe",
    "fin": "fin",
    "sas": "sas",
}


def _raw(name: str) -> dict:
    return json.loads((_EVIDENCE / "raw" / name).read_text())


def _seed(name: str) -> dict[str, str]:
    with (_FIXTURES / "seed_csvs" / f"{name}_seed.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["rsid"] == "rs6025"]
    assert len(rows) == 1
    return rows[0]


def _clinvar_identity(number: int) -> tuple[str, int, str, str]:
    records = _raw(f"clinvar-{number}-identity-derived.json")["records"]
    assert len(records) == 1
    assert records[0]["archive"]["Accession"] == f"VCV{number:09d}"
    locations = [item for item in records[0]["sequence_locations"] if item["Assembly"] == "GRCh37"]
    assert len(locations) == 1
    row = locations[0]
    return (
        row["Chr"],
        int(row["positionVCF"]),
        row["referenceAlleleVCF"],
        row["alternateAlleleVCF"],
    )


def test_sources_distinguish_the_reference_allele_from_the_selected_alt() -> None:
    assert _raw("ensembl37-base-rs6025.json")["seq"] == "T"
    mappings = _raw("ensembl37-rs6025.json")["mappings"]
    primary = [
        row
        for row in mappings
        if row["seq_region_name"] == "1" and row["assembly_name"] == "GRCh37"
    ]
    assert len(primary) == 1
    assert (primary[0]["start"], primary[0]["strand"], primary[0]["allele_string"]) == (
        169519049,
        1,
        "T/C/A",
    )
    assert _clinvar_identity(642) == ("1", 169519049, "T", "T")
    assert _clinvar_identity(226007) == _IDENTITY


@pytest.mark.parametrize("seed", ["vep", "clinvar", "gnomad"])
def test_rs6025_seeds_select_the_verified_grch37_alt(seed: str) -> None:
    row = _seed(seed)
    assert (row["chrom"], int(row["pos"]), row["ref"], row["alt"]) == _IDENTITY
    exceptions = json.loads((_FIXTURES / "seed_allele_known_exceptions.json").read_text())
    assert all(item["rsid"] != "rs6025" for item in exceptions["rows"])


def test_clinvar_metadata_belongs_to_the_selected_alt_not_the_leiden_record() -> None:
    row = _seed("clinvar")
    assert row["accession"] == "VCV000226007"
    assert row["variation_id"] == "226007"
    assert _clinvar_identity(int(row["variation_id"])) == _IDENTITY
    source = json.loads((_EVIDENCE / "raw/clinvar-search-rs6025.json").read_text())
    assert source[0] == len(source[1]) == 2
    fields = source[2]
    index = fields["VariationID"].index(row["variation_id"])
    assert fields["dbSNP"][index] == row["rsid"] == "rs6025"
    assert fields["GeneSymbol"][index] == row["gene_symbol"] == "F5"
    assert row["significance"] == _normalize_clinvar_significance(
        fields["ClinicalSignificance"][index]
    )
    assert int(row["review_stars"]) == _review_status_to_stars(fields["ReviewStatus"][index])
    assert row["conditions"] == fields["PhenotypeList"][index]
    snapshot = json.loads((_FIXTURES / "clinvar_seed_snapshot.json").read_text())
    assert "VCV000000642" not in snapshot["records"]
    record = snapshot["records"][row["accession"]]
    assert record["name"] == fields["Name"][index]
    assert record["clinical_significance"] == fields["ClinicalSignificance"][index]
    assert record["review_status"] == fields["ReviewStatus"][index]
    assert record["phenotype_list"] == fields["PhenotypeList"][index]
    provenance = snapshot["_provenance"]["record_overrides"][row["accession"]]
    assert provenance["accessed"] == "2026-11-08"
    assert _ROOT / provenance["raw_evidence_path"] == _EVIDENCE / "raw/clinvar-search-rs6025.json"
    assert (
        _ROOT / provenance["identity_evidence_path"]
        == _EVIDENCE / "raw/clinvar-226007-identity-derived.json"
    )


def test_vep_annotation_and_production_lookup_follow_the_selected_alt() -> None:
    row = _seed("vep")
    matches = [
        candidate
        for candidate in _raw("pinned-vep-snapshot-derived.json")["rows"]["rs6025"]
        if candidate["alt"] == "C"
    ]
    assert len(matches) == 1
    expected = matches[0]
    assert {key: row[key] for key in MIRRORED_FIELDS} == {
        key: expected[key] for key in MIRRORED_FIELDS
    }
    assert row["hgvs_coding"] == "c.1601A>G"
    assert row["hgvs_protein"] == "p.Gln534Arg"
    engine = sa.create_engine(f"sqlite:///{_FIXTURES / 'mini_vep_bundle.db'}")
    try:
        result = lookup_vep_by_positions(
            [("1", 169519049, "rs6025")], engine, allele_by_rsid={"rs6025": _IDENTITY}
        )
        assert set(result) == {"rs6025"}
        annotation = result["rs6025"]
        assert (annotation.ref, annotation.alt) == ("T", "C")
        assert annotation.hgvs_coding == expected["hgvs_coding"]
        assert annotation.hgvs_protein == expected["hgvs_protein"]
        assert annotation.exon_number == int(expected["exon_number"])
    finally:
        engine.dispose()


def _frequencies() -> tuple[dict[str, float], int]:
    variant = _raw("gnomad-r2_1-rs6025.json")["data"]["variant"]
    assert variant["variant_id"] == "1-169519049-T-C"
    assert variant["reference_genome"] == "GRCh37" and "rs6025" in variant["rsids"]
    source = variant["exome"]
    assert source and source["an"] > 0 and source["filters"] == []
    expected = {"af_global": source["ac"] / source["an"]}
    populations = {item["id"]: item for item in source["populations"]}
    assert set(_POPULATIONS.values()) <= populations.keys()
    for suffix, name in _POPULATIONS.items():
        counts = populations[name]
        assert counts["an"] > 0
        expected[f"af_{suffix}"] = counts["ac"] / counts["an"]
    return expected, source["homozygote_count"]


def test_every_frequency_and_homozygote_count_comes_from_the_exact_exome_allele() -> None:
    row = _seed("gnomad")
    expected, homozygotes = _frequencies()
    assert len(expected) == 8
    for key, value in expected.items():
        assert float(row[key]) == pytest.approx(value, rel=1e-12)
    assert int(row["homozygous_count"]) == homozygotes
    provenance = json.loads(
        (_FIXTURES / "seed_csvs/gnomad_seed.af_asj.provenance.json").read_text()
    )
    record = provenance["records"]["rs6025"]
    assert record["gnomad_variant_id"] == "1-169519049-T-C"
    assert record["dataset"] == "gnomad_r2_1" and record["sequencing_type"] == "exome"
    assert record["orientation"] == "same"
    assert record["af_asj"] == pytest.approx(expected["af_asj"], rel=1e-12)
    assert record["af_asj"] == record["asj_ac"] / record["asj_an"]
    assert "rs6025" not in provenance["withheld_rsids"]["rsids"]


def test_gnomad_production_lookup_rejects_the_old_pair_and_the_other_alt() -> None:
    expected, homozygotes = _frequencies()
    engine = sa.create_engine(f"sqlite:///{_FIXTURES / 'mini_gnomad_af.db'}")
    try:
        result = lookup_gnomad_by_positions(
            [_IDENTITY, ("1", 169519049, "C", "T"), ("1", 169519049, "T", "A")], engine
        )
        assert set(result) == {_IDENTITY}
        annotation = result[_IDENTITY]
        for key, value in expected.items():
            assert getattr(annotation, key) == pytest.approx(value, rel=1e-12)
        assert annotation.homozygous_count == homozygotes
    finally:
        engine.dispose()


@pytest.mark.parametrize("lookup", ["rsid", "position"])
def test_clinvar_production_lookup_preserves_the_selected_record(
    lookup: str, tmp_path: Path
) -> None:
    # Installed reference bundles receive the production schema upgrade before
    # lookup. Upgrade a private copy; never mutate the checked-in mini database.
    database = tmp_path / "reference.db"
    shutil.copyfile(_FIXTURES / "mini_reference.db", database)
    engine = sa.create_engine(f"sqlite:///{database}")
    try:
        ensure_reference_schema_current(engine)
        result = (
            lookup_clinvar_by_rsids(["rs6025"], engine)
            if lookup == "rsid"
            else lookup_clinvar_by_positions([("1", 169519049, "rs6025")], engine)
        )
        assert set(result) == {"rs6025"}
        annotation = result["rs6025"]
        assert (annotation.ref, annotation.alt) == ("T", "C")
        assert annotation.clinvar_accession == "VCV000226007"
        row = _seed("clinvar")
        assert annotation.clinvar_significance == row["significance"]
        assert annotation.clinvar_review_stars == int(row["review_stars"])
        assert annotation.clinvar_conditions == row["conditions"]
    finally:
        engine.dispose()
