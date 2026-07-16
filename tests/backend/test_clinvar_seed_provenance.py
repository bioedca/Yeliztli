"""Offline accession provenance guard for the ClinVar seed fixture (#1968).

``clinvar_seed.csv`` populates the checked-in ``mini_reference.db`` and acts as a
test oracle for the evidence pointers shown to users.  #1968 found that 95/100 rows
cited a different ClinVar record: most of VariationIDs 98801--98865 were a copied
RPGR/RPE65 block relabelled as unrelated pharmacogenes, while Factor V Leiden cited
a PEPD prolidase-deficiency record.

The committed ``clinvar_seed_snapshot.json`` records what each retained VCV accession
actually identifies in NLM ClinVar.  These tests fail loudly if a seed accession is
uncovered, points at another rsID/gene, disagrees with the record metadata, or is not
propagated into ``mini_reference.db``.  CI never calls NCBI.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from backend.analysis.clinvar_significance import primary_pathogenic_classification
from backend.annotation.clinvar import (
    _normalize_clinvar_significance,
    _review_status_to_stars,
)

_ROOT = Path(__file__).resolve().parents[2]
_SEED = _ROOT / "tests" / "fixtures" / "seed_csvs" / "clinvar_seed.csv"
_SNAPSHOT_PATH = _ROOT / "tests" / "fixtures" / "clinvar_seed_snapshot.json"
_MINI_REFERENCE = _ROOT / "tests" / "fixtures" / "mini_reference.db"
_VCV_RE = re.compile(r"VCV\d{9}")
_MAX_CONSECUTIVE_RUN = 3
_GENE_ALIASES = {"GBA": {"GBA", "GBA1"}}
_DB_COLUMNS = (
    "rsid",
    "chrom",
    "pos",
    "ref",
    "alt",
    "significance",
    "review_stars",
    "accession",
    "conditions",
    "gene_symbol",
    "variation_id",
)


def _rows() -> list[dict[str, str]]:
    with _SEED.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _snapshot_data() -> dict[str, object]:
    return json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _snapshot() -> dict[str, dict[str, object]]:
    return _snapshot_data()["records"]  # type: ignore[return-value]


def _real_rows() -> list[dict[str, str]]:
    return [row for row in _rows() if row["accession"] or row["variation_id"]]


def _row_key(row: dict[str, str]) -> str:
    return f"{row['rsid']}:{row['ref']}>{row['alt']}"


def _stored_significance(raw_clinical_tables_value: object) -> str:
    """Translate Clinical Tables separators through production VCF normalization."""
    vcf_value = re.sub(r"\s*;\s*", "|", str(raw_clinical_tables_value))
    normalized = _normalize_clinvar_significance(vcf_value)
    assert normalized is not None
    return normalized


def test_snapshot_is_well_formed() -> None:
    data = _snapshot_data()
    provenance = data["_provenance"]
    records = data["records"]
    assert isinstance(provenance, dict)
    assert provenance["source"] == "NLM Clinical Tables ClinVar variants v4 search"
    assert provenance["source_url"] == (
        "https://clinicaltables.nlm.nih.gov/api/variants/v4/search"
    )
    date.fromisoformat(str(provenance["accessed"]))
    assert provenance["generator"] == "scripts/build_clinvar_seed_snapshot.py"
    assert provenance["record_count"] == len(records)
    assert records, "ClinVar seed snapshot is empty"

    assert isinstance(records, dict)
    for accession, record in records.items():
        assert _VCV_RE.fullmatch(accession), f"malformed snapshot accession {accession!r}"
        assert isinstance(record, dict)
        assert record["variation_id"] == int(accession[3:])
        assert record["dbsnp"], f"{accession}: missing dbSNP xref"
        assert record["gene_symbols"], f"{accession}: missing gene symbol"
        assert record["name"], f"{accession}: missing ClinVar name"


def test_snapshot_exactly_covers_every_real_seed_accession() -> None:
    rows = _real_rows()
    partial = [_row_key(row) for row in rows if not row["accession"] or not row["variation_id"]]
    assert not partial, f"partially specified ClinVar identifiers: {partial}"
    cited = {row["accession"] for row in rows}
    assert len(cited) == len(rows), "clinvar_seed.csv contains duplicate accessions"
    snapshotted = set(_snapshot())
    assert cited == snapshotted, (
        "ClinVar snapshot coverage differs from clinvar_seed.csv; regenerate with "
        "python scripts/build_clinvar_seed_snapshot.py --accessed <today>. "
        f"missing={sorted(cited - snapshotted)}, orphaned={sorted(snapshotted - cited)}"
    )


def test_every_accession_identifies_its_own_rsid_gene_and_metadata() -> None:
    snapshot = _snapshot()
    failures: list[str] = []
    for row in _real_rows():
        accession = row["accession"]
        variation_id = int(row["variation_id"])
        expected_accession = f"VCV{variation_id:09d}"
        record = snapshot[accession]
        wanted_genes = _GENE_ALIASES.get(row["gene_symbol"], {row["gene_symbol"]})
        record_genes = set(record["gene_symbols"])
        mismatches: list[str] = []
        if accession != expected_accession:
            mismatches.append(f"accession should be {expected_accession}")
        if record["variation_id"] != variation_id:
            mismatches.append(f"snapshot VariationID is {record['variation_id']}")
        if row["rsid"] not in record["dbsnp"]:
            mismatches.append(f"dbSNP xrefs are {record['dbsnp']}")
        if not (wanted_genes & record_genes):
            mismatches.append(f"ClinVar genes are {sorted(record_genes)}")
        expected_significance = _stored_significance(record["clinical_significance"])
        if row["significance"] != expected_significance:
            mismatches.append(
                "clinical significance differs from production-normalized ClinVar "
                f"({expected_significance!r})"
            )
        expected_stars = _review_status_to_stars(str(record["review_status"]))
        if int(row["review_stars"]) != expected_stars:
            mismatches.append(f"review stars are {row['review_stars']}, expected {expected_stars}")
        if row["conditions"] != record["phenotype_list"]:
            mismatches.append("conditions differ from ClinVar phenotype list")
        if mismatches:
            failures.append(f"{_row_key(row)} {accession}: " + "; ".join(mismatches))
    assert not failures, "ClinVar seed provenance mismatches:\n" + "\n".join(failures)


def test_every_seed_row_has_a_real_clinvar_accession() -> None:
    missing = [_row_key(row) for row in _rows() if not row["accession"] or not row["variation_id"]]
    assert not missing, f"rows without a real ClinVar identifier: {missing}"
    assert "rs12345" not in {row["rsid"] for row in _rows()}, (
        "the old GENE1 placeholder has no ClinVar record and must stay withheld"
    )
    provenance = _snapshot_data()["_provenance"]
    assert provenance["synthetic_rows_without_accession"] == []


def test_retinal_counter_block_and_long_consecutive_runs_are_absent() -> None:
    rows = _real_rows()
    variation_ids = {int(row["variation_id"]) for row in rows}
    retinal_block = sorted(variation_ids & set(range(98_801, 98_866)))
    assert not retinal_block, f"fabricated RPGR/RPE65 VariationID block returned: {retinal_block}"

    numbered = sorted((int(row["variation_id"]), row["gene_symbol"]) for row in rows)
    runs: list[list[tuple[int, str]]] = []
    run: list[tuple[int, str]] = []
    for variation_id, gene in numbered:
        if run and variation_id == run[-1][0] + 1:
            run.append((variation_id, gene))
        else:
            if run:
                runs.append(run)
            run = [(variation_id, gene)]
    if run:
        runs.append(run)
    cross_gene_runs = [item for item in runs if len({gene for _, gene in item}) > 1]
    longest = max(cross_gene_runs, key=len, default=[])
    assert len(longest) <= _MAX_CONSECUTIVE_RUN, (
        f"found {len(longest)} consecutive ClinVar IDs across distinct genes "
        f"(counter signature): {longest}"
    )


@pytest.mark.parametrize(
    ("rsid", "variation_id"),
    [
        ("rs429358", 17_864),
        ("rs1801133", 3_520),
        ("rs113993960", 7_105),
        ("rs6025", 642),
        ("rs1799963", 13_310),
        ("rs78655421", 53_765),
        ("rs213950", 7_130),
    ],
)
def test_headline_repairs_keep_verified_accessions(rsid: str, variation_id: int) -> None:
    row = next((item for item in _real_rows() if item["rsid"] == rsid), None)
    assert row is not None, f"{rsid} missing from clinvar_seed.csv"
    assert row["variation_id"] == str(variation_id)
    assert row["accession"] == f"VCV{variation_id:09d}"


def test_pathogenic_slash_compound_uses_production_storage_semantics() -> None:
    row = next(item for item in _real_rows() if item["rsid"] == "rs78655421")
    assert row["significance"] == "Pathogenic"
    assert primary_pathogenic_classification(row["significance"]) == "Pathogenic"


def _db_value(column: str, value: str) -> object:
    if value == "":
        return None
    if column in {"pos", "review_stars", "variation_id"}:
        return int(value)
    return value


def test_checked_in_database_matches_the_seed() -> None:
    expected = [tuple(_db_value(column, row[column]) for column in _DB_COLUMNS) for row in _rows()]
    query = f"SELECT {', '.join(_DB_COLUMNS)} FROM clinvar_variants ORDER BY id"
    with sqlite3.connect(_MINI_REFERENCE) as connection:
        actual = connection.execute(query).fetchall()
    assert actual == expected, (
        "tests/fixtures/mini_reference.db is stale relative to clinvar_seed.csv; run "
        "python scripts/regenerate_fixtures.py"
    )
