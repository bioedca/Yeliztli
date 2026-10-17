"""Offline reproducibility and runtime-safety guards for breast PRS77 (#1934)."""

from __future__ import annotations

import copy
import csv
import hashlib
import importlib.util
import io
import json
import math
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
_PANEL = _REPO / "backend" / "data" / "panels" / "cancer_prs_weights.json"
_SOURCES = _REPO / "backend" / "data" / "sources" / "breast_prs77"
_SUPPLEMENT = _SOURCES / "mavaddat_2015_supplementary_table_4.tsv"
_HARMONIZED = _SOURCES / "pgs000001_harmonized_grch37.tsv"
_ENSEMBL = _SOURCES / "ensembl_primary_grch38_2026-07-16.json"
_ENSEMBL_RELEASE_CHECK = _SOURCES / "ensembl_release_check_2026-10-17.json"
_GENERATOR = _REPO / "scripts" / "build_breast_prs77.py"
_COMPLEMENT = str.maketrans("ACGT", "TGCA")


def _read_tsv(path: Path) -> list[dict[str, str]]:
    lines = [line for line in path.read_text().splitlines() if not line.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(lines)), delimiter="\t"))


def _compact_projection_sha256(rows: list[list[str]]) -> str:
    payload = json.dumps(rows, separators=(",", ":")) + "\n"
    return hashlib.sha256(payload.encode()).hexdigest()


def test_published_rows_and_grch37_projection_are_exact() -> None:
    source = _read_tsv(_SUPPLEMENT)
    harmonized = _read_tsv(_HARMONIZED)

    assert len(source) == len({row["rsid"] for row in source}) == 77
    assert [int(row["source_row"]) for row in source] == list(range(1, 78))
    assert len(harmonized) == len({row["rsid"] for row in harmonized}) == 77
    assert [row["rsid"] for row in harmonized] == [row["rsid"] for row in source]
    assert all(
        math.isclose(
            float(row["pgs_effect_weight"]),
            math.log(float(row["overall_or"])),
            rel_tol=0.0,
            abs_tol=5e-16,
        )
        for row in source
    )

    source_projection = [
        [
            row["rsid"],
            row["source_effect_allele"],
            row["source_other_allele"],
            row["pgs_effect_weight"],
        ]
        for row in source
    ]
    assert _compact_projection_sha256(source_projection) == (
        "db7811c2e526e58a54524f5096c2af4bc92c4afa4cd114a45112c9208075059d"
    )
    full_source_projection = [
        [
            row["rsid"],
            row["chromosome"],
            row["source_effect_allele"],
            row["source_other_allele"],
            row["pgs_effect_weight"],
            row["locus_name"],
            row["overall_or"],
        ]
        for row in source
    ]
    assert _compact_projection_sha256(full_source_projection) == (
        "d365caed0ebe7a8ae2d496ae5840ac47876f0577c5ebd07ab487684b4111bac9"
    )

    harmonized_by_rsid = {row["rsid"]: row for row in harmonized}
    runtime_projection = [
        [
            row["rsid"],
            harmonized_by_rsid[row["rsid"]]["hm_chr"],
            harmonized_by_rsid[row["rsid"]]["hm_pos"],
            row["source_effect_allele"],
            row["source_other_allele"],
            row["pgs_effect_weight"],
        ]
        for row in source
    ]
    assert _compact_projection_sha256(runtime_projection) == (
        "6e014bab4d8fcb147677492719f7bb8d919cd51efafa85ef66c877369ae55a85"
    )
    full_harmonized_projection = [
        source_row
        + [
            harmonized_by_rsid[source_row[0]]["hm_source"],
            harmonized_by_rsid[source_row[0]]["hm_rsid"],
            harmonized_by_rsid[source_row[0]]["hm_chr"],
            harmonized_by_rsid[source_row[0]]["hm_pos"],
            "",
        ]
        for source_row in full_source_projection
    ]
    assert _compact_projection_sha256(full_harmonized_projection) == (
        "9ee7f5435fbcc5a9312c56b08d0160869a3ad9189066785278c2731bd67b31bb"
    )


def test_primary_grch38_audit_exposes_multiallelic_runtime_blocker() -> None:
    source = _read_tsv(_SUPPLEMENT)
    source_by_rsid = {row["rsid"]: row for row in source}
    snapshot = json.loads(_ENSEMBL.read_text(encoding="utf-8"))
    records = {record["rsid"]: record for record in snapshot["records"]}

    assert snapshot["mapping_policy"].startswith("Select only the primary")
    assert len(records) == 77
    assert sum(len(record["alleles"]) > 2 for record in records.values()) == 39
    assert records["rs10771399"]["alleles"] == ["A", "G"]

    palindromic: set[str] = set()
    complement_only: set[str] = set()
    for rsid, row in source_by_rsid.items():
        pair = {row["source_effect_allele"], row["source_other_allele"]}
        complement = {allele.translate(_COMPLEMENT) for allele in pair}
        current = set(records[rsid]["alleles"])
        if pair == complement:
            palindromic.add(rsid)
        if not pair <= current and complement <= current:
            complement_only.add(rsid)

    assert palindromic == {
        "rs1045485",
        "rs11075995",
        "rs11571833",
        "rs12422552",
        "rs12493607",
        "rs527616",
        "rs554219",
    }
    assert complement_only == {"rs1432679", "rs17529111"}


def test_generated_model_is_exact_but_fail_closed() -> None:
    panel = json.loads(_PANEL.read_text(encoding="utf-8"))
    breast = next(model for model in panel["weight_sets"] if model["trait"] == "breast_cancer")
    source = _read_tsv(_SUPPLEMENT)
    harmonized = _read_tsv(_HARMONIZED)
    ensembl = {
        record["rsid"]: record
        for record in json.loads(_ENSEMBL.read_text(encoding="utf-8"))["records"]
    }

    assert breast["model_status"] == "source_verified_runtime_blocked"
    assert {
        model["trait"]: model["model_status"]
        for model in panel["weight_sets"]
        if model["trait"] != "breast_cancer"
    } == {
        "prostate_cancer": "active",
        "colorectal_cancer": "active",
        "melanoma": "active",
    }
    assert breast["source_pmid"] == "25855707"
    assert breast["sample_size"] == 67054
    assert breast["pgs_id"] == "PGS000001"
    assert breast["variants_number"] == len(breast["weights"]) == 77
    assert breast["scoring_enabled"] is False
    assert breast["calibrated"] is False
    assert breast["calibration_eligible"] is False
    assert breast["legacy_audit_record_sha256"] == (
        "01e47f0ca15eb6cb51774f31719f6bb79a836792744c85925f95f4c33ce29fe4"
    )
    assert len(breast["legacy_audit_record"]["weights"]) == 25

    groups = Counter(weight["allele_mapping_class"] for weight in breast["weights"])
    assert groups == {
        "biallelic_forward_non_palindromic": 34,
        "biallelic_forward_palindromic": 2,
        "biallelic_reverse_complement_non_palindromic": 2,
        "multiallelic_forward_non_palindromic": 34,
        "multiallelic_forward_palindromic": 5,
    }
    blocked = [weight for weight in breast["weights"] if not weight["runtime_scoring_eligible"]]
    assert len(blocked) == 41
    assert all(weight["imputation_eligible"] is False for weight in blocked)
    multiallelic = [
        weight for weight in breast["weights"] if len(weight["grch38_current_alleles"]) > 2
    ]
    assert len(multiallelic) == 39
    assert all("other_allele" not in weight for weight in multiallelic)
    assert all(weight["runtime_scoring_eligible"] is False for weight in multiallelic)
    assert {
        weight["rsid"] for weight in blocked if len(weight["grch38_current_alleles"]) == 2
    } == {"rs11571833", "rs12422552"}
    assert breast["model_provenance"]["current_allele_audit"]["runtime_blocked_loci"] == 41
    assert (
        sum(
            weight["orientation_ambiguous_in_current_multiallelic_set"]
            for weight in breast["weights"]
        )
        == 11
    )
    assert all(weight["chrom"] and weight["pos"] > 0 for weight in breast["weights"])

    for source_row, harmonized_row, emitted in zip(
        source, harmonized, breast["weights"], strict=True
    ):
        assert emitted["source_row"] == int(source_row["source_row"])
        assert emitted["rsid"] == source_row["rsid"] == harmonized_row["rsid"]
        assert emitted["effect_allele"] == source_row["source_effect_allele"]
        assert emitted["source_other_allele"] == source_row["source_other_allele"]
        assert emitted["weight"] == float(source_row["pgs_effect_weight"])
        assert emitted["source_or"] == float(source_row["overall_or"])
        assert emitted["chrom"] == harmonized_row["hm_chr"]
        assert emitted["pos"] == int(harmonized_row["hm_pos"])
        pair = {
            source_row["source_effect_allele"],
            source_row["source_other_allele"],
        }
        palindromic = pair == {allele.translate(_COMPLEMENT) for allele in pair}
        if len(ensembl[emitted["rsid"]]["alleles"]) > 2:
            assert "other_allele" not in emitted
            assert emitted["runtime_scoring_eligible"] is False
        else:
            assert emitted["other_allele"] == source_row["source_other_allele"]
            assert emitted["runtime_scoring_eligible"] is (not palindromic)


def test_generator_check_and_write_are_idempotent(tmp_path: Path) -> None:
    check = subprocess.run(
        [sys.executable, str(_GENERATOR), "--check"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stderr

    panel_copy = tmp_path / "cancer_prs_weights.json"
    panel_copy.write_text(_PANEL.read_text(encoding="utf-8"), encoding="utf-8")
    command = [sys.executable, str(_GENERATOR), "--write", "--panel", str(panel_copy)]
    subprocess.run(command, cwd=_REPO, capture_output=True, text=True, check=True)
    first = panel_copy.read_bytes()
    subprocess.run(command, cwd=_REPO, capture_output=True, text=True, check=True)
    assert panel_copy.read_bytes() == first


def test_generator_rejects_mutated_active_model_status(tmp_path: Path) -> None:
    original = _PANEL.read_text(encoding="utf-8")
    mutated = original.replace(
        '      "model_status": "active",',
        '      "model_status": "source_verified_runtime_blocked",',
        1,
    )
    assert mutated != original
    panel_copy = tmp_path / "mutated-active-status.json"
    panel_copy.write_text(mutated, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_GENERATOR), "--check", "--panel", str(panel_copy)],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert '"model_status": "active"' in result.stderr


def test_generator_rejects_mutated_nested_legacy_record(tmp_path: Path) -> None:
    payload = json.loads(_PANEL.read_text(encoding="utf-8"))
    breast = next(model for model in payload["weight_sets"] if model["trait"] == "breast_cancer")
    breast["legacy_audit_record"]["weights"][0]["weight"] += 0.001
    panel_copy = tmp_path / "mutated-legacy.json"
    panel_copy.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_GENERATOR), "--check", "--panel", str(panel_copy)],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "legacy audit record failed pinned" in result.stderr


def _load_generator():
    spec = importlib.util.spec_from_file_location("build_breast_prs77", _GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_allele_audit_names_the_ensembl_release_it_was_confirmed_against() -> None:
    """The audit is tied to a named Ensembl release without back-filling one (#2246)."""
    snapshot = json.loads(_ENSEMBL.read_text(encoding="utf-8"))
    check = json.loads(_ENSEMBL_RELEASE_CHECK.read_text(encoding="utf-8"))
    panel = json.loads(_PANEL.read_text(encoding="utf-8"))
    breast = next(model for model in panel["weight_sets"] if model["trait"] == "breast_cancer")
    audit = breast["model_provenance"]["current_allele_audit"]
    reproducibility = breast["model_provenance"]["reproducibility"]

    # The 2026-07-16 snapshot still names no release: nothing was back-filled into it.
    assert not any("release" in key or "version" in key for key in snapshot)
    assert "snapshot_ensembl_release" in audit and audit["snapshot_ensembl_release"] is None
    assert "snapshot_ensembl_release" in check and check["snapshot_ensembl_release"] is None

    release = check["ensembl_release"]
    assert isinstance(release, int) and not isinstance(release, bool) and release > 0
    assert check["snapshot"] == _ENSEMBL.name
    assert check["variation_source_versions"]["dbSNP"]
    # Equal records are what let the release stand for the snapshot's classification.
    assert check["records"] == snapshot["records"]
    assert sum(len(record["alleles"]) > 2 for record in check["records"]) == 39

    assert audit["release_verification"] == {
        "ensembl_release": release,
        "checked_on": check["checked_on"],
        "endpoint": check["endpoint"],
        "records_identical_to_snapshot": True,
        "artifact": _ENSEMBL_RELEASE_CHECK.name,
    }
    assert reproducibility["checked_in_snapshot_sha256"][_ENSEMBL_RELEASE_CHECK.name] == (
        hashlib.sha256(_ENSEMBL_RELEASE_CHECK.read_bytes()).hexdigest()
    )
    assert (
        reproducibility["source_artifact_sha256"]["ensembl_release_check_raw_records"]
        == (check["raw_payload_sha256"])
    )


def _drop(key: str):
    def mutate(check: dict[str, Any]) -> None:
        del check[key]

    return mutate


def _set(key: str, value: object):
    def mutate(check: dict[str, Any]) -> None:
        check[key] = value

    return mutate


def _change_first_allele(check: dict[str, Any]) -> None:
    check["records"][0]["alleles"] = ["A", "C", "T"]


def _drop_last_record(check: dict[str, Any]) -> None:
    check["records"].pop()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_change_first_allele, "records differ"),
        (_drop_last_record, "records differ"),
        (_drop("ensembl_release"), "positive integer"),
        (_set("ensembl_release", None), "positive integer"),
        (_set("ensembl_release", "116"), "positive integer"),
        (_set("ensembl_release", True), "positive integer"),
        (_set("ensembl_release", 115), "not the pinned release"),
        (_set("snapshot_ensembl_release", 116), "explicitly null"),
        (_drop("snapshot_ensembl_release"), "explicitly null"),
        (_set("snapshot", "some_other_snapshot.json"), "does not name the snapshot"),
        (_set("mapping_policy", "all mappings"), "mapping_policy differs"),
        (_set("raw_payload_sha256", "0" * 64), "raw response-set SHA-256"),
        (_set("variation_source_versions", {}), "dbSNP source version"),
        (_set("variation_source_versions", {"dbSNP": ""}), "dbSNP source version"),
        (_set("variation_source_versions", {"dbSNP": "  "}), "dbSNP source version"),
        (_set("variation_source_versions", None), "dbSNP source version"),
    ],
)
def test_release_check_must_reproduce_the_snapshot_and_name_its_release(
    mutate, message: str
) -> None:
    generator = _load_generator()
    snapshot = json.loads(_ENSEMBL.read_text(encoding="utf-8"))
    committed = json.loads(_ENSEMBL_RELEASE_CHECK.read_text(encoding="utf-8"))
    # Positive control: the committed pair is accepted, so a rejection below is the mutation's.
    assert generator._validate_ensembl_release_check(snapshot, committed) is committed

    mutated = copy.deepcopy(committed)
    mutate(mutated)
    with pytest.raises(ValueError, match=message):
        generator._validate_ensembl_release_check(snapshot, mutated)


def test_generator_rejects_a_mutated_release_verification(tmp_path: Path) -> None:
    payload = json.loads(_PANEL.read_text(encoding="utf-8"))
    breast = next(model for model in payload["weight_sets"] if model["trait"] == "breast_cancer")
    breast["model_provenance"]["current_allele_audit"]["release_verification"][
        "ensembl_release"
    ] = 115
    panel_copy = tmp_path / "mutated-release.json"
    panel_copy.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_GENERATOR), "--check", "--panel", str(panel_copy)],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert '"ensembl_release": 116' in result.stderr


_RELEASE_EVIDENCE = (
    _REPO / "data" / "science-evidence" / "2026-10-17-breast-prs77-ensembl-release-2246"
)


def _primary_grch38_records(raw_response: dict[str, Any]) -> list[dict[str, Any]]:
    """Re-derive snapshot records from a raw ``POST /variation/homo_sapiens`` body."""
    records = []
    for rsid, variant in raw_response.items():
        primary = [
            mapping
            for mapping in variant["mappings"]
            if mapping.get("coord_system") == "chromosome"
            and mapping.get("assembly_name") == "GRCh38"
        ]
        assert len(primary) == 1, f"{rsid}: expected one primary GRCh38 mapping"
        records.append(
            {
                "alleles": sorted(primary[0]["allele_string"].split("/")),
                "location": primary[0]["location"],
                "rsid": rsid,
                "strand": primary[0]["strand"],
            }
        )
    return sorted(records, key=lambda record: record["rsid"])


def test_both_ensembl_raw_responses_are_retained_and_reproduce_their_records() -> None:
    """Each artifact's ``raw_payload_sha256`` resolves to a retained response (#2246).

    Without the payloads the digests are claims nobody can check; with them, the
    records each artifact carries can be re-derived from what Ensembl returned.
    """
    manifest = json.loads((_RELEASE_EVIDENCE / "queries.json").read_text(encoding="utf-8"))
    entries = manifest["queries"]
    assert entries, "the release-check packet must list its retained payloads"
    for entry in entries:
        payload = _REPO / entry["raw_payload"]
        assert payload.is_relative_to(_RELEASE_EVIDENCE), entry["raw_payload"]
        assert hashlib.sha256(payload.read_bytes()).hexdigest() == entry["raw_sha256"], (
            f"{entry['raw_payload']} no longer matches its recorded digest"
        )
    assert len(entries) == len(list((_RELEASE_EVIDENCE / "raw").iterdir()))

    retained = {
        hashlib.sha256((_REPO / entry["raw_payload"]).read_bytes()).hexdigest(): entry
        for entry in entries
    }
    for artifact in (_ENSEMBL, _ENSEMBL_RELEASE_CHECK):
        data = json.loads(artifact.read_text(encoding="utf-8"))
        entry = retained.get(data["raw_payload_sha256"])
        assert entry is not None, f"{artifact.name}: its raw response is not retained"
        raw_response = json.loads((_REPO / entry["raw_payload"]).read_text(encoding="utf-8"))
        assert _primary_grch38_records(raw_response) == data["records"], (
            f"{artifact.name}: records do not re-derive from its raw response"
        )
