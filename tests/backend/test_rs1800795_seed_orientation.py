"""Source-backed rs1800795 fixture identity (#2563), not clinical validation."""

import csv
import json
import re
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
_EVIDENCE = _ROOT / "data/science-evidence/2026-11-08-rs1800795-orientation-2563"
_IDENTITY = ("7", 22766645, "C", "G")
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
        rows = [row for row in csv.DictReader(handle) if row["rsid"] == "rs1800795"]
    assert len(rows) == 1
    return rows[0]


def _clinvar_identity() -> tuple[str, int, str, str]:
    records = _raw("clinvar-14718-identity-derived.json")["records"]
    assert len(records) == 1
    assert records[0]["archive"]["Accession"] == "VCV000014718"
    locations = [item for item in records[0]["sequence_locations"] if item["Assembly"] == "GRCh37"]
    assert len(locations) == 1
    row = locations[0]
    return (
        row["Chr"],
        int(row["positionVCF"]),
        row["referenceAlleleVCF"],
        row["alternateAlleleVCF"],
    )


def test_sources_agree_on_the_selected_genomic_allele() -> None:
    assert _raw("ensembl37-base-rs1800795.json")["seq"] == "C"
    mappings = _raw("ensembl37-rs1800795.json")["mappings"]
    primary = [
        row
        for row in mappings
        if row["seq_region_name"] == "7" and row["assembly_name"] == "GRCh37"
    ]
    assert len(primary) == 1
    assert (primary[0]["start"], primary[0]["strand"], primary[0]["allele_string"]) == (
        22766645,
        1,
        "C/G/T",
    )
    assert _clinvar_identity() == _IDENTITY


@pytest.mark.parametrize("seed", ["vep", "clinvar", "gnomad"])
def test_rs1800795_seeds_select_the_verified_grch37_alt(seed: str) -> None:
    row = _seed(seed)
    assert (row["chrom"], int(row["pos"]), row["ref"], row["alt"]) == _IDENTITY
    exceptions = json.loads((_FIXTURES / "seed_allele_known_exceptions.json").read_text())
    assert all(item["rsid"] != "rs1800795" for item in exceptions["rows"])


def test_clinvar_metadata_and_provenance_belong_to_the_selected_alt() -> None:
    row = _seed("clinvar")
    assert row["accession"] == "VCV000014718" and row["variation_id"] == "14718"
    assert (row["chrom"], int(row["pos"]), row["ref"], row["alt"]) == _clinvar_identity()
    source = json.loads((_EVIDENCE / "raw/clinvar-search-rs1800795.json").read_text())
    assert source[0] == len(source[1]) == 1
    fields = source[2]
    index = fields["VariationID"].index(row["variation_id"])
    assert fields["dbSNP"][index] == row["rsid"] == "rs1800795"
    assert fields["GeneSymbol"][index] == row["gene_symbol"] == "IL6"
    assert row["significance"] == _normalize_clinvar_significance(
        re.sub(r"\s*;\s*", "|", fields["ClinicalSignificance"][index])
    )
    assert int(row["review_stars"]) == _review_status_to_stars(fields["ReviewStatus"][index])
    assert row["conditions"] == fields["PhenotypeList"][index]
    snapshot = json.loads((_FIXTURES / "clinvar_seed_snapshot.json").read_text())
    record = snapshot["records"][row["accession"]]
    assert record["name"] == fields["Name"][index]
    assert record["clinical_significance"] == fields["ClinicalSignificance"][index]
    assert record["review_status"] == fields["ReviewStatus"][index]
    assert record["phenotype_list"] == fields["PhenotypeList"][index]
    provenance = snapshot["_provenance"]["record_overrides"][row["accession"]]
    assert provenance["accessed"] == "2026-11-08"
    assert (
        _ROOT / provenance["raw_evidence_path"] == _EVIDENCE / "raw/clinvar-search-rs1800795.json"
    )
    assert (
        _ROOT / provenance["identity_evidence_path"]
        == _EVIDENCE / "raw/clinvar-14718-identity-derived.json"
    )


def test_vep_annotation_and_production_lookup_follow_the_pinned_transcript() -> None:
    row = _seed("vep")
    matches = [
        candidate
        for candidate in _raw("pinned-vep-snapshot-derived.json")["rows"]["rs1800795"]
        if candidate["alt"] == "G"
    ]
    assert len(matches) == 1
    expected = matches[0]
    assert {key: row[key] for key in MIRRORED_FIELDS} == {
        key: expected[key] for key in MIRRORED_FIELDS
    }
    assert row["gene_symbol"] == "AC073072.5"
    assert row["transcript_id"] == "ENST00000325042"
    assert row["hgvs_coding"] == "n.54-321G>C"
    engine = sa.create_engine(f"sqlite:///{_FIXTURES / 'mini_vep_bundle.db'}")
    try:
        result = lookup_vep_by_positions(
            [("7", 22766645, "rs1800795")], engine, allele_by_rsid={"rs1800795": _IDENTITY}
        )
        assert set(result) == {"rs1800795"}
        annotation = result["rs1800795"]
        assert (annotation.ref, annotation.alt) == ("C", "G")
        assert annotation.gene_symbol == expected["gene_symbol"]
        assert annotation.transcript_id == expected["transcript_id"]
        assert annotation.consequence == expected["consequence"]
        assert annotation.hgvs_coding == expected["hgvs_coding"]
        assert annotation.intron_number == int(expected["intron_number"])
    finally:
        engine.dispose()


def _frequencies() -> tuple[dict[str, float | None], int]:
    variant = _raw("gnomad-r2_1-rs1800795.json")["data"]["variant"]
    assert variant["variant_id"] == "7-22766645-C-G"
    assert variant["reference_genome"] == "GRCh37" and "rs1800795" in variant["rsids"]
    assert variant["exome"] is None
    source = variant["genome"]
    assert source and source["an"] > 0 and source["filters"] == []
    expected: dict[str, float | None] = {"af_global": source["ac"] / source["an"]}
    populations = {item["id"]: item for item in source["populations"]}
    assert set(_POPULATIONS.values()) <= populations.keys()
    for suffix, name in _POPULATIONS.items():
        counts = populations[name]
        if name == "sas":
            assert counts["ac"] == counts["an"] == 0
            expected[f"af_{suffix}"] = None
        else:
            assert counts["an"] > 0
            expected[f"af_{suffix}"] = counts["ac"] / counts["an"]
    return expected, source["homozygote_count"]


def test_frequencies_use_exact_genome_counts_and_preserve_missing_sas() -> None:
    row = _seed("gnomad")
    expected, homozygotes = _frequencies()
    assert len(expected) == 8
    for key, value in expected.items():
        if value is None:
            assert row[key] == ""
        else:
            assert float(row[key]) == pytest.approx(value, rel=1e-12)
    assert int(row["homozygous_count"]) == homozygotes
    provenance = json.loads(
        (_FIXTURES / "seed_csvs/gnomad_seed.af_asj.provenance.json").read_text()
    )
    record = provenance["records"]["rs1800795"]
    assert record["gnomad_variant_id"] == "7-22766645-C-G"
    assert record["dataset"] == "gnomad_r2_1" and record["sequencing_type"] == "genome"
    assert record["orientation"] == "same"
    assert record["af_asj"] == pytest.approx(expected["af_asj"], rel=1e-12)
    assert record["af_asj"] == record["asj_ac"] / record["asj_an"]
    assert "rs1800795" not in provenance["withheld_rsids"]["rsids"]


def test_gnomad_production_lookup_rejects_the_old_pair_and_other_alt() -> None:
    expected, homozygotes = _frequencies()
    engine = sa.create_engine(f"sqlite:///{_FIXTURES / 'mini_gnomad_af.db'}")
    try:
        result = lookup_gnomad_by_positions(
            [_IDENTITY, ("7", 22766645, "G", "C"), ("7", 22766645, "C", "T")], engine
        )
        assert set(result) == {_IDENTITY}
        annotation = result[_IDENTITY]
        for key, value in expected.items():
            if value is None:
                assert getattr(annotation, key) is None
            else:
                assert getattr(annotation, key) == pytest.approx(value, rel=1e-12)
        assert annotation.homozygous_count == homozygotes
    finally:
        engine.dispose()


@pytest.mark.parametrize("lookup", ["rsid", "position"])
def test_clinvar_production_lookup_preserves_the_selected_record(
    lookup: str, tmp_path: Path
) -> None:
    database = tmp_path / "reference.db"
    shutil.copyfile(_FIXTURES / "mini_reference.db", database)
    engine = sa.create_engine(f"sqlite:///{database}")
    try:
        ensure_reference_schema_current(engine)
        result = (
            lookup_clinvar_by_rsids(["rs1800795"], engine)
            if lookup == "rsid"
            else lookup_clinvar_by_positions([("7", 22766645, "rs1800795")], engine)
        )
        assert set(result) == {"rs1800795"}
        annotation = result["rs1800795"]
        assert (annotation.ref, annotation.alt) == ("C", "G")
        assert annotation.clinvar_accession == "VCV000014718"
        row = _seed("clinvar")
        assert annotation.clinvar_significance == row["significance"]
        assert annotation.clinvar_review_stars == int(row["review_stars"])
        assert annotation.clinvar_conditions == row["conditions"]
    finally:
        engine.dispose()
