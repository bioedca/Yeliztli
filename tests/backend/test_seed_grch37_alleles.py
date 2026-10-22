"""Every located seed row carries its rsID's plus-strand GRCh37 alleles, reference first (#2583).

``tests/fixtures/seed_csvs/README.md`` makes ``ref``/``alt`` the plus-strand GRCh37 alleles at
the row's mapping, with ``ref`` the assembly base. The REF pin in
``test_regenerate_fixtures.py`` (#2344) reaches only the 38 single-base rsIDs the panel
oracle covers, and ``test_seed_grch37_coordinates.py`` checks loci only, so the alleles of
every other correctly placed seed row went unchecked: hundreds carried alleles outside their
rsID's allele string or wrote the alternate allele as REF.

This guard reads the seed-wide oracle offline. For a row at its rsID's GRCh37 locus whose
allele string is single-base, ``{ref, alt}`` must be drawn from it and ``ref`` must be its
first allele, which is the GRCh37 assembly base at every position this guard pins (checked
against two sources in the evidence packet below). An SNV row at an indel rsID is flagged too.

Rows that break this today are listed in ``seed_allele_known_exceptions.json``. The list is
exact in both directions: a new violation fails, and so does a listed row that has been
corrected, until its entry is removed. Correcting the rows themselves is separate work --
``gnomad_seed`` frequencies are orientation-dependent and must be re-sourced, not flipped.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SEED_DIR = _ROOT / "tests" / "fixtures" / "seed_csvs"
_ORACLE = _ROOT / "tests" / "fixtures" / "seed_rsid_grch37_coordinates.json"
_EXCEPTIONS = _ROOT / "tests" / "fixtures" / "seed_allele_known_exceptions.json"
_EVIDENCE = _ROOT / "data" / "science-evidence" / "2026-10-22-issue-2583-seed-allele-orientation"
# The allele-bearing coordinate seeds; gwas_seed carries a risk allele, not ref/alt.
_SEEDS = ("vep_seed", "clinvar_seed", "gnomad_seed")


def _oracle() -> dict:
    return json.loads(_ORACLE.read_text(encoding="utf-8"))["rsids"]


def _is_single_base(allele_string: str) -> bool:
    alleles = allele_string.split("/")
    return "-" not in alleles and all(len(allele) == 1 for allele in alleles)


def _allele_violation(row: dict[str, str], allele_string: str) -> str | None:
    """How a located row's alleles break the contract, or None if they hold."""
    alleles = allele_string.split("/")
    ref, alt = row["ref"], row["alt"]
    snv_row = len(ref) == 1 and len(alt) == 1 and "-" not in (ref, alt)
    if not _is_single_base(allele_string):
        # Indel rows are placed by the locus guard; only an SNV posing as one is wrong here.
        return "snv_at_indel" if snv_row else None
    if not snv_row:
        return "indel_at_snv"
    if not {ref, alt} <= set(alleles):
        return "outside_allele_string"
    if ref != alleles[0]:
        return "not_reference_first"
    return None


def _located_rows() -> list[tuple[str, dict[str, str], dict]]:
    """Seed rows at their rsID's GRCh37 locus (the locus guard owns the rest)."""
    oracle = _oracle()
    located = []
    for seed in _SEEDS:
        with open(_SEED_DIR / f"{seed}.csv", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                mapping = oracle.get(row["rsid"])
                if mapping is None or str(mapping["chrom"]) != row["chrom"]:
                    continue
                if not _is_single_base(mapping["allele_string"]):
                    # Indel positions follow the locus guard's padding rule; an SNV row
                    # claiming one sits at the mapping start.
                    if int(row["pos"]) not in (mapping["start"], mapping["start"] - 1):
                        continue
                elif int(row["pos"]) != mapping["start"]:
                    continue
                located.append((seed, row, mapping))
    return located


def _violations() -> list[tuple[str, str, str, str, str]]:
    found = []
    for seed, row, mapping in _located_rows():
        violation = _allele_violation(row, mapping["allele_string"])
        if violation is not None:
            found.append((seed, row["rsid"], row["ref"], row["alt"], violation))
    return sorted(found)


def _known_exceptions() -> list[tuple[str, str, str, str, str]]:
    rows = json.loads(_EXCEPTIONS.read_text(encoding="utf-8"))["rows"]
    return sorted(
        (entry["seed"], entry["rsid"], entry["ref"], entry["alt"], entry["violation"])
        for entry in rows
    )


def test_every_located_seed_row_has_reference_first_grch37_alleles() -> None:
    located = _located_rows()
    # Three seeds' located rows; a reader that silently read nothing would pass vacuously.
    assert len(located) > 800, f"only {len(located)} located seed rows read"
    checked = [r for _, r, m in located if _is_single_base(m["allele_string"])]
    assert len(checked) > 600, f"only {len(checked)} single-base rows checked"

    found = set(_violations())
    known = set(_known_exceptions())
    unexpected = sorted(found - known)
    assert unexpected == [], f"seed rows breaking the GRCh37 allele contract: {unexpected}"
    corrected = sorted(known - found)
    assert corrected == [], (
        "listed rows now hold the contract (or changed); drop them from "
        f"seed_allele_known_exceptions.json: {corrected}"
    )


def test_the_exception_list_is_tracked_and_has_no_duplicates() -> None:
    document = json.loads(_EXCEPTIONS.read_text(encoding="utf-8"))
    assert document["_provenance"]["origin"] == "#2583"
    entries = _known_exceptions()
    assert len(entries) == len(set(entries)), "duplicate exception entries"
    for entry in document["rows"]:
        assert entry["tracked_by"].startswith("#"), entry


def test_the_ref_pin_is_the_assembly_base_at_every_pinned_position() -> None:
    """The first allele is only a valid REF pin because it is the assembly base."""
    bases = json.loads((_EVIDENCE / "assembly_bases.json").read_text(encoding="utf-8"))
    pinned = {
        row["rsid"]: mapping
        for _, row, mapping in _located_rows()
        if _is_single_base(mapping["allele_string"])
    }
    missing = sorted(set(pinned) - set(bases))
    assert missing == [], f"pinned positions without a verified assembly base: {missing}"
    for rsid, mapping in pinned.items():
        record = bases[rsid]
        assert (record["chrom"], record["pos"]) == (str(mapping["chrom"]), mapping["start"])
        first = mapping["allele_string"].split("/")[0]
        assert record["ensembl_grch37"] == record["second_source_base"] == first, (
            f"{rsid}: oracle first allele {first}, Ensembl GRCh37 "
            f"{record['ensembl_grch37']}, {record['second_source']} "
            f"{record['second_source_base']}"
        )


@pytest.mark.parametrize(
    ("ref", "alt", "allele_string", "violation"),
    [
        ("C", "T", "C/T", None),
        ("T", "C", "C/T", "not_reference_first"),
        # rs1065852 as the seed carries it, against Ensembl's G/A/C.
        ("C", "T", "G/A/C", "outside_allele_string"),
        ("G", "A", "G/A/C", None),
        # rs199474672: an SNV written for a "-/T" insertion.
        ("T", "C", "-/T", "snv_at_indel"),
        ("C", "CT", "-/T", None),
        ("CA", "C", "C/A", "indel_at_snv"),
    ],
)
def test_allele_check_discriminates(
    ref: str, alt: str, allele_string: str, violation: str | None
) -> None:
    assert _allele_violation({"ref": ref, "alt": alt}, allele_string) == violation
