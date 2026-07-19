"""Offline contract guard for the synthetic GWAS membership fixture (#1948/#2011).

``gwas_seed.csv`` exists only to put representative rsIDs into
``gwas_associations`` so integration tests can exercise presence matching through
``gwas_matched_rsids``.  It is not a scientific reference dataset.  In particular,
it must not attach real-looking coordinates, effects, alleles, studies, or citations
to values that were created for tests.

The adjacent machine-readable contract records that decision.  These tests enforce
it without network access and verify that the checked-in mini database has no stale
pre-contract association metadata.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SEED = _ROOT / "tests" / "fixtures" / "seed_csvs" / "gwas_seed.csv"
_CONTRACT = _ROOT / "tests" / "fixtures" / "seed_csvs" / "gwas_seed.contract.json"
_MINI_REFERENCE = _ROOT / "tests" / "fixtures" / "mini_reference.db"

_TRAIT_SENTINEL = "Synthetic GWAS membership fixture"
_RSID_SET_SHA256 = "7a2d74e0049c4d02e6c04fb61133b94eb0acbade3b1e84410b3c08dea2634a19"
_EMPTY_FIELDS = (
    "chrom",
    "pos",
    "p_value",
    "odds_ratio",
    "beta",
    "risk_allele",
    "pubmed_id",
    "study",
    "sample_size",
)
_DB_COLUMNS = (
    "rsid",
    "chrom",
    "pos",
    "trait",
    "p_value",
    "odds_ratio",
    "beta",
    "risk_allele",
    "pubmed_id",
    "study",
    "sample_size",
)


def _rows() -> list[dict[str, str]]:
    with _SEED.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(_DB_COLUMNS), (
            "gwas_seed.csv columns changed outside the reviewed synthetic contract"
        )
        return list(reader)


def test_contract_explicitly_declares_synthetic_membership_only() -> None:
    contract = json.loads(_CONTRACT.read_text(encoding="utf-8"))

    assert contract["contract_version"] == 1
    assert contract["fixture_model"] == "synthetic_rsid_membership"
    assert contract["consumer"] == "backend.annotation.gwas.gwas_matched_rsids"
    assert contract["row_count"] == 65
    assert contract["rsid_set_sha256"] == _RSID_SET_SHA256
    assert contract["populated_fields"] == ["rsid", "trait"]
    assert contract["trait_sentinel"] == _TRAIT_SENTINEL
    assert contract["fields_must_be_empty"] == list(_EMPTY_FIELDS)
    assert contract["scientific_association_claims"] is False
    assert contract["paper_provenance"] is False


def test_rows_follow_the_synthetic_contract() -> None:
    rows = _rows()
    assert len(rows) == 65, "membership coverage changed without a contract-version update"

    rsids = [row["rsid"] for row in rows]
    assert len(rsids) == len(set(rsids)), "membership fixture must have one row per rsID"
    assert all(re.fullmatch(r"rs\d+", rsid) for rsid in rsids)
    digest_input = "\n".join(sorted(rsids)) + "\n"
    assert hashlib.sha256(digest_input.encode()).hexdigest() == _RSID_SET_SHA256, (
        "GWAS membership set changed; update the reviewed contract and its version intentionally"
    )

    violations: list[str] = []
    for row in rows:
        if row["trait"] != _TRAIT_SENTINEL:
            violations.append(f"{row['rsid']}: trait={row['trait']!r}")
        populated = {field: row[field] for field in _EMPTY_FIELDS if row[field]}
        if populated:
            violations.append(f"{row['rsid']}: prohibited metadata={populated}")
    assert not violations, (
        "gwas_seed.csv is synthetic membership data; scientific/effect/provenance "
        "fields must stay empty:\n" + "\n".join(violations)
    )


def test_checked_in_database_exactly_matches_the_seed() -> None:
    assert _MINI_REFERENCE.is_file(), "checked-in mini_reference.db is missing"
    expected = [
        tuple(None if row[column] == "" else row[column] for column in _DB_COLUMNS)
        for row in _rows()
    ]
    query = f"SELECT {', '.join(_DB_COLUMNS)} FROM gwas_associations ORDER BY id"
    with sqlite3.connect(_MINI_REFERENCE) as connection:
        actual = connection.execute(query).fetchall()

    assert actual == expected, (
        "tests/fixtures/mini_reference.db is stale relative to the synthetic GWAS seed; "
        "run python scripts/regenerate_fixtures.py"
    )
