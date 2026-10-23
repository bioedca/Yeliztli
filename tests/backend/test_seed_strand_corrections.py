"""The five #2591 rows select source-backed alleles, annotations and frequencies.

These are offline fixture contracts, not assertions about a patient's phenotype.
Raw public source metadata and the explicit multiallelic selection are retained in
data/science-evidence/2026-10-23-issue-2591-seed-strand/.
"""

import csv
import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.annotation.gnomad import lookup_gnomad_by_positions
from backend.annotation.vep_bundle import lookup_vep_by_positions
from scripts.build_vep_seed_bundle_snapshot import MIRRORED_FIELDS

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_EVIDENCE = _FIXTURES.parents[1] / "data/science-evidence/2026-10-23-issue-2591-seed-strand"
_SELECTED = {
    "rs1065852": ("22", 42526694, "G", "A"),
    "rs2032582": ("7", 87160618, "A", "C"),
}
_POPULATIONS = {
    "afr": "afr",
    "amr": "amr",
    "asj": "asj",
    "eas": "eas",
    "eur": "nfe",
    "fin": "fin",
    "sas": "sas",
}


def _seed(name: str, rsid: str) -> dict[str, str]:
    with (_FIXTURES / "seed_csvs" / f"{name}_seed.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["rsid"] == rsid]
    assert len(rows) == 1
    return rows[0]


def _frequency_source(rsid: str) -> dict:
    raw = _EVIDENCE / "raw" / f"gnomad-r2_1-{rsid}.json"
    variant = json.loads(raw.read_text())["data"]["variant"]
    chrom, pos, ref, alt = _SELECTED[rsid]
    assert variant["variant_id"] == f"{chrom}-{pos}-{ref}-{alt}"
    assert variant["reference_genome"] == "GRCh37"
    assert rsid in variant["rsids"]
    source = variant["exome"]
    assert source and source["filters"] == [] and source["an"] > 0
    return source


@pytest.mark.parametrize(
    ("seed", "rsid"),
    [(seed, rsid) for rsid in _SELECTED for seed in ("vep", "gnomad")]
    + [("clinvar", "rs1065852")],
)
def test_corrected_seeds_select_the_documented_genomic_allele(seed: str, rsid: str) -> None:
    row = _seed(seed, rsid)
    assert (row["chrom"], int(row["pos"]), row["ref"], row["alt"]) == _SELECTED[rsid]
    exceptions = json.loads((_FIXTURES / "seed_allele_known_exceptions.json").read_text())
    assert all(entry["rsid"] != rsid for entry in exceptions["rows"])


def test_cyp2d6_seed_keeps_its_clinvar_identity_on_the_genomic_strand() -> None:
    row = _seed("clinvar", "rs1065852")
    record = json.loads((_EVIDENCE / "raw/VCV000016893-identity-derived.json").read_text())
    assert record["record_status"] == "current"
    assert row["accession"] == record["archive"]["Accession"] == "VCV000016893"
    assert row["variation_id"] == record["archive"]["VariationID"] == "16893"
    locations = [location for location in record["locations"] if location["Assembly"] == "GRCh37"]
    assert len(locations) == 1
    location = locations[0]
    assert (
        location["Chr"],
        int(location["positionVCF"]),
        location["referenceAlleleVCF"],
        location["alternateAlleleVCF"],
    ) == _SELECTED["rs1065852"]


@pytest.mark.parametrize("rsid", _SELECTED)
def test_corrected_vep_rows_match_the_pinned_bundle_and_production_lookup(rsid: str) -> None:
    row = _seed("vep", rsid)
    snapshot = json.loads((_FIXTURES / "vep_seed_bundle_snapshot.json").read_text())
    chrom, pos, ref, alt = _SELECTED[rsid]
    matches = [
        candidate
        for candidate in snapshot["rows"][rsid]
        if (candidate["chrom"], int(candidate["pos"]), candidate["ref"], candidate["alt"])
        == (chrom, pos, ref, alt)
    ]
    assert len(matches) == 1
    assert {field: row[field] for field in MIRRORED_FIELDS} == {
        field: matches[0][field] for field in MIRRORED_FIELDS
    }
    engine = sa.create_engine(f"sqlite:///{_FIXTURES / 'mini_vep_bundle.db'}")
    try:
        result = lookup_vep_by_positions(
            [(chrom, pos, rsid)], engine, allele_by_rsid={rsid: (chrom, pos, ref, alt)}
        )
        assert set(result) == {rsid}
        annotation = result[rsid]
        assert (annotation.ref, annotation.alt) == (ref, alt)
        assert annotation.hgvs_coding == matches[0]["hgvs_coding"]
        assert annotation.hgvs_protein == matches[0]["hgvs_protein"]
        assert annotation.exon_number == int(matches[0]["exon_number"])
    finally:
        engine.dispose()


@pytest.mark.parametrize("rsid", _SELECTED)
def test_all_corrected_frequencies_come_from_the_exact_exome_allele(rsid: str) -> None:
    row = _seed("gnomad", rsid)
    source = _frequency_source(rsid)
    populations = {population["id"]: population for population in source["populations"]}
    expected = {"af_global": source["ac"] / source["an"]}
    for suffix, population in _POPULATIONS.items():
        counts = populations[population]
        assert counts["an"] > 0
        expected[f"af_{suffix}"] = counts["ac"] / counts["an"]
    for field, value in expected.items():
        assert float(row[field]) == pytest.approx(value, rel=1e-12)
    assert int(row["homozygous_count"]) == source["homozygote_count"]

    provenance = json.loads(
        (_FIXTURES / "seed_csvs/gnomad_seed.af_asj.provenance.json").read_text()
    )
    record = provenance["records"][rsid]
    assert record["dataset"] == "gnomad_r2_1"
    assert record["sequencing_type"] == "exome"
    assert record["orientation"] == "same"
    assert record["af_asj"] == pytest.approx(expected["af_asj"], rel=1e-12)
    assert rsid not in provenance["withheld_rsids"]["rsids"]

    identity = _SELECTED[rsid]
    chrom, pos, ref, alt = identity
    # The old transcript-strand pair and the other public ALT are not this allele.
    old = (chrom, pos, "C", "T") if rsid == "rs1065852" else (chrom, pos, "G", "T")
    other = (chrom, pos, ref, "C" if rsid == "rs1065852" else "T")
    engine = sa.create_engine(f"sqlite:///{_FIXTURES / 'mini_gnomad_af.db'}")
    try:
        result = lookup_gnomad_by_positions([identity, old, other], engine)
        assert set(result) == {identity}
        annotation = result[identity]
        for field, value in expected.items():
            assert getattr(annotation, field) == pytest.approx(value, rel=1e-12)
        assert annotation.homozygous_count == source["homozygote_count"]
    finally:
        engine.dispose()
