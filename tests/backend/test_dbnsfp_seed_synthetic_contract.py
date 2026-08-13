"""Offline synthetic-data contract for the compact dbNSFP behavior fixture (#2035).

dbNSFP predictor values are provider-fetched, non-redistributed runtime data.
The shared mini fixture therefore exercises parsing, lookup, null handling, and
ensemble boundaries with explicit synthetic identities instead of attaching
invented scores to real variants.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from backend.annotation.dbnsfp import (
    is_ensemble_pathogenic,
    load_dbnsfp_from_csv,
    lookup_dbnsfp_by_rsids,
)

_ROOT = Path(__file__).resolve().parents[2]
_SEED = _ROOT / "tests" / "fixtures" / "seed_csvs" / "dbnsfp_seed.csv"
_CONTRACT = _ROOT / "tests" / "fixtures" / "seed_csvs" / "dbnsfp_seed.contract.json"
_MINI_DBNSFP = _ROOT / "tests" / "fixtures" / "mini_dbnsfp.db"
_EVIDENCE = _ROOT / "data" / "science-evidence" / "2026-08-12-dbnsfp-seed-2035"

_DB_COLUMNS = (
    "rsid",
    "chrom",
    "pos",
    "ref",
    "alt",
    "cadd_phred",
    "sift_score",
    "sift_pred",
    "polyphen2_hsvar_score",
    "polyphen2_hsvar_pred",
    "revel",
    "mutpred2",
    "vest4",
    "metasvm",
    "metalr",
    "gerp_rs",
    "phylop",
    "mpc",
    "primateai",
)


def _contract() -> dict:
    return json.loads(_CONTRACT.read_text(encoding="utf-8"))


def _rows() -> list[dict[str, str]]:
    with _SEED.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(_DB_COLUMNS), (
            "dbnsfp_seed.csv columns changed outside the reviewed synthetic contract"
        )
        return list(reader)


def test_contract_forbids_scientific_variant_or_upstream_score_claims() -> None:
    contract = _contract()

    assert contract["contract_version"] == 1
    assert contract["fixture_model"] == "synthetic_predictor_behavior_scenarios"
    assert contract["consumer"] == "backend.annotation.dbnsfp.load_dbnsfp_from_csv"
    assert contract["rsid_namespace"] == "rsYELIZTLI"
    assert contract["coordinate_model"] == "synthetic_grch38_lookup_keys"
    assert contract["scientific_variant_claims"] is False
    assert contract["upstream_score_values"] is False


def test_seed_contains_exactly_the_reviewed_synthetic_scenarios() -> None:
    contract = _contract()
    rows = _rows()
    rsids = [row["rsid"] for row in rows]

    assert len(rows) == contract["row_count"]
    assert len(rsids) == len(set(rsids))
    assert set(rsids) == set(contract["scenarios"])
    assert all(re.fullmatch(r"rsYELIZTLI\d{4}", rsid) for rsid in rsids)
    assert not any(re.fullmatch(r"rs\d+", rsid) for rsid in rsids)


def test_sift_and_polyphen_categories_are_consistent_with_scores() -> None:
    violations: list[str] = []

    for row in _rows():
        sift_score = row["sift_score"]
        sift_pred = row["sift_pred"]
        if bool(sift_score) != bool(sift_pred):
            violations.append(f"{row['rsid']}: incomplete SIFT score/prediction pair")
        elif sift_score:
            expected_sift = "D" if float(sift_score) < 0.05 else "T"
            if sift_pred != expected_sift:
                violations.append(
                    f"{row['rsid']}: SIFT {sift_score}/{sift_pred}, expected {expected_sift}"
                )

        polyphen_score = row["polyphen2_hsvar_score"]
        polyphen_pred = row["polyphen2_hsvar_pred"]
        if bool(polyphen_score) != bool(polyphen_pred):
            violations.append(f"{row['rsid']}: incomplete PolyPhen score/prediction pair")
        elif polyphen_score:
            score = float(polyphen_score)
            expected_polyphen = "B" if score <= 0.446 else "P" if score <= 0.908 else "D"
            if polyphen_pred != expected_polyphen:
                violations.append(
                    f"{row['rsid']}: PolyPhen {polyphen_score}/{polyphen_pred}, "
                    f"expected {expected_polyphen}"
                )

    assert not violations, "incoherent synthetic predictor categories:\n" + "\n".join(violations)


def test_scenarios_have_the_exact_reviewed_production_outcomes() -> None:
    contract = _contract()
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        load_dbnsfp_from_csv(_SEED, engine)
        annotations = lookup_dbnsfp_by_rsids(list(contract["scenarios"]), engine)
    finally:
        engine.dispose()

    assert set(annotations) == set(contract["scenarios"])
    for rsid, expected in contract["scenarios"].items():
        annotation = annotations[rsid]
        assert annotation.deleterious_count == expected["deleterious_count"], rsid
        assert annotation.deleterious_total_assessed == expected["total_assessed"], rsid
        assert is_ensemble_pathogenic(annotation) is expected["ensemble_pathogenic"], rsid


def test_checked_in_database_exactly_matches_the_synthetic_seed() -> None:
    expected = [
        tuple(None if row[column] == "" else row[column] for column in _DB_COLUMNS)
        for row in _rows()
    ]
    query = f"SELECT {', '.join(_DB_COLUMNS)} FROM dbnsfp_scores ORDER BY rowid"

    uri = f"file:{_MINI_DBNSFP.resolve()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        actual = connection.execute(query).fetchall()

    normalized = [
        tuple(str(value) if value is not None else None for value in row) for row in actual
    ]
    assert normalized == expected, (
        "mini_dbnsfp.db is stale relative to the synthetic seed; "
        "run python scripts/regenerate_fixtures.py"
    )


def test_scientific_evidence_manifest_binds_retained_source_payloads() -> None:
    manifest = json.loads((_EVIDENCE / "source-manifest.json").read_text(encoding="utf-8"))
    artifacts = {artifact["path"]: artifact for artifact in manifest["retained_artifacts"]}
    readme = (_EVIDENCE / "README.md").read_text(encoding="utf-8")
    defined_claims = set(re.findall(r"^\| (C\d+) \|", readme, flags=re.MULTILINE))

    assert defined_claims == {"C1", "C2", "C3", "C4", "C5"}

    for source in manifest["sources"]:
        assert source["claim_ids"], f"{source['id']} has no claim mapping"
        assert set(source["claim_ids"]) <= defined_claims, (
            f"{source['id']} references an undefined claim"
        )
        retained_path = source["retained_path"]
        assert retained_path in artifacts, (
            f"{source['id']} retained payload is not in the manifest"
        )

    for relative_path, artifact in artifacts.items():
        path = _ROOT / relative_path
        assert path.is_file(), f"retained evidence payload is missing: {relative_path}"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == artifact["sha256"], f"retained payload hash drift: {relative_path}"
