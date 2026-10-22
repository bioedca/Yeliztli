"""Every coordinate-bearing seed row sits at its rsID's GRCh37 locus (#2375).

``tests/fixtures/seed_csvs/README.md`` requires ``chrom``/``pos`` to be the rsID's 1-based
GRCh37 primary-chromosome position. The panel oracle (``panel_rsid_coordinates.json``) only
reaches seed rsIDs that are panel members, so 75 rsIDs outside it drifted to coordinates from
another assembly or another locus, and the mini databases built from the seeds tested a
coordinate-mismatch state nobody knew about.

``seed_rsid_grch37_coordinates.json`` is the committed, seed-wide oracle: each seed rsID's
single Ensembl GRCh37 primary mapping, written from the evidence packet named in its
provenance. These tests read it offline and never fetch.

Only loci are checked here. Allele orientation is a separate guard; ``rs6025`` (#2541) is an
example of a correctly placed row whose alleles are tracked on their own.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SEED_DIR = _ROOT / "tests" / "fixtures" / "seed_csvs"
_ORACLE = _ROOT / "tests" / "fixtures" / "seed_rsid_grch37_coordinates.json"
_SEEDS = ("vep_seed", "clinvar_seed", "gnomad_seed", "gwas_seed")

# #2345's synthetic GENE1 row borrows the real dbSNP ID rs12345; that issue owns it.
_OUT_OF_SCOPE = {"rs12345"}

# Rows that mirror the vendor parser fixtures (sample_ancestrydna_v2.txt and its CRLF twin):
# the rs9999xxx placeholder family, the PAR/MT/Y scenarios and the IDs with no GRCh37 record.
# Correcting them changes those fixtures and the parser tests that rely on their synthetic
# loci, so they are a separate change, filed as #2375's follow-up. This set can only shrink.
# (rs199474672 and rs794728091, both "-/T" insertions, already sit at their VCF anchor; only
# their alleles are wrong, which is the allele guard's concern, not this one.)
_PARSER_FIXTURE_ROWS = {
    "rs12124819",
    "rs17842875",
    "rs2032597",
    "rs28736870",
    "rs369202065",
    "rs397514462",
    "rs5778923",
    "rs6655587",
    "rs7205",
    "rs786203760",
    "rs78907894",
    "rs8176318",
    "rs9999001",
    "rs9999003",
    "rs9999004",
    "rs9999005",
    "rs9999006",
    "rs9999007",
    "rs9999008",
    "rs9999009",
    "rs9999010",
    "rs9999011",
    "rs9999012",
    "rs9999050",
}


def _oracle() -> dict:
    return json.loads(_ORACLE.read_text(encoding="utf-8"))


def _seed_rows() -> list[tuple[str, dict[str, str]]]:
    rows = []
    for name in _SEEDS:
        with open(_SEED_DIR / f"{name}.csv", newline="", encoding="utf-8") as fh:
            rows.extend((name, row) for row in csv.DictReader(fh))
    return rows


def _expected_pos(row: dict[str, str], mapping: dict) -> int:
    # An allele string without the shared base ("A/-", "-/T") is an un-padded indel. A row
    # that writes it the same way ("T" > "-", as rs80359550 does) sits at Ensembl's start; a
    # row in VCF form ("CA" > "C", as rs5030655 does) starts at the preceding base. Padded
    # strings ("TCTT/T") already start at that base.
    unpadded_mapping = "-" in mapping["allele_string"].split("/")
    unpadded_row = "-" in (row.get("ref"), row.get("alt"))
    if unpadded_mapping and not unpadded_row:
        return mapping["start"] - 1
    return mapping["start"]


def _misplaced(row: dict[str, str], mapping: dict | None) -> str | None:
    """Why a seed row is not at its rsID's GRCh37 locus, or None if it is."""
    if mapping is None:
        return "no GRCh37 record"
    expected = f"{mapping['chrom']}:{_expected_pos(row, mapping)}"
    actual = f"{row['chrom']}:{row['pos']}"
    return None if actual == expected else f"at {actual}, GRCh37 {expected}"


def _located_rows() -> list[tuple[str, dict[str, str]]]:
    return [
        (seed, row)
        for seed, row in _seed_rows()
        if row["rsid"].startswith("rs") and row["rsid"][2:].isdigit() and row["chrom"]
    ]


def test_oracle_cites_a_committed_evidence_packet() -> None:
    provenance = _oracle()["_provenance"]
    assert provenance["assembly"] == "GRCh37"
    assert (_ROOT / provenance["evidence_packet"] / "README.md").is_file()
    assert (_ROOT / provenance["generator"]).is_file()


def test_oracle_covers_every_seed_rsid() -> None:
    oracle = _oracle()
    seed_rsids = {
        row["rsid"]
        for _, row in _seed_rows()
        if row["rsid"].startswith("rs") and row["rsid"][2:].isdigit()
    }
    known = set(oracle["rsids"]) | set(oracle["_provenance"]["no_grch37_record"])
    assert seed_rsids, "no seed rsIDs read"
    assert seed_rsids <= known, (
        f"seed rsIDs the oracle has never seen: {sorted(seed_rsids - known)}"
    )


def test_every_seed_row_sits_at_its_grch37_locus() -> None:
    oracle = _oracle()["rsids"]
    located = _located_rows()
    # 3 seeds x their located rows; a reader that silently read nothing would pass vacuously.
    assert len(located) > 800, f"only {len(located)} located seed rows read"

    misplaced: dict[str, list[str]] = {}
    for seed, row in located:
        reason = _misplaced(row, oracle.get(row["rsid"]))
        if reason is not None:
            misplaced.setdefault(row["rsid"], []).append(f"{seed}: {reason}")

    excepted = _OUT_OF_SCOPE | _PARSER_FIXTURE_ROWS
    unexpected = {rsid: why for rsid, why in misplaced.items() if rsid not in excepted}
    assert unexpected == {}, f"seed rows not at their rsID's GRCh37 locus: {unexpected}"
    now_placed = sorted(_PARSER_FIXTURE_ROWS - set(misplaced))
    assert now_placed == [], (
        f"now at their GRCh37 locus; drop from _PARSER_FIXTURE_ROWS: {now_placed}"
    )


@pytest.mark.parametrize(
    ("row", "mapping", "misplaced"),
    [
        # The CYP2C19*2 row as it stood: a GRCh38 coordinate.
        (
            {"chrom": "10", "pos": "94781858"},
            {"chrom": "10", "start": 96541616, "end": 96541616, "allele_string": "G/A/C/T"},
            True,
        ),
        (
            {"chrom": "10", "pos": "96541616"},
            {"chrom": "10", "start": 96541616, "end": 96541616, "allele_string": "G/A/C/T"},
            False,
        ),
        # Another chromosome altogether (rs63750066 was a BRCA2 row on 13).
        (
            {"chrom": "13", "pos": "27264108"},
            {"chrom": "21", "start": 27264108, "end": 27264108, "allele_string": "C/T"},
            True,
        ),
        # An un-padded deletion is written in VCF form one base earlier (rs5030655).
        (
            {"chrom": "22", "pos": "42525085", "ref": "CA", "alt": "C"},
            {"chrom": "22", "start": 42525086, "end": 42525086, "allele_string": "A/-"},
            False,
        ),
        (
            {"chrom": "22", "pos": "42525086", "ref": "CA", "alt": "C"},
            {"chrom": "22", "start": 42525086, "end": 42525086, "allele_string": "A/-"},
            True,
        ),
        # The same deletion written un-padded sits at Ensembl's start (rs80359550).
        (
            {"chrom": "13", "pos": "32914438", "ref": "T", "alt": "-"},
            {"chrom": "13", "start": 32914438, "end": 32914438, "allele_string": "T/-"},
            False,
        ),
        (
            {"chrom": "13", "pos": "32914437", "ref": "T", "alt": "-"},
            {"chrom": "13", "start": 32914438, "end": 32914438, "allele_string": "T/-"},
            True,
        ),
        # A padded allele string already starts at the shared base (rs113993960).
        (
            {"chrom": "7", "pos": "117199645"},
            {"chrom": "7", "start": 117199645, "end": 117199648, "allele_string": "TCTT/T"},
            False,
        ),
        ({"chrom": "1", "pos": "1000300"}, None, True),
    ],
)
def test_locus_check_discriminates(row: dict, mapping: dict | None, misplaced: bool) -> None:
    assert (_misplaced(row, mapping) is not None) is misplaced
