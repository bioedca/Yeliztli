"""Offline reproducibility and runtime-safety guards for breast PRS77 (#1934)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import subprocess
import sys
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_PANEL = _REPO / "backend" / "data" / "panels" / "cancer_prs_weights.json"
_SOURCES = _REPO / "backend" / "data" / "sources" / "breast_prs77"
_SUPPLEMENT = _SOURCES / "mavaddat_2015_supplementary_table_4.tsv"
_HARMONIZED = _SOURCES / "pgs000001_harmonized_grch37.tsv"
_ENSEMBL = _SOURCES / "ensembl_primary_grch38_2026-07-16.json"
_GENERATOR = _REPO / "scripts" / "build_breast_prs77.py"
_COMPLEMENT = str.maketrans("ACGT", "TGCA")


def _read_tsv(path: Path) -> list[dict[str, str]]:
    lines = [line for line in path.read_text().splitlines() if not line.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(lines)), delimiter="\t"))


def _compact_projection_sha256(rows: list[list[str]]) -> str:
    payload = json.dumps(rows, separators=(",", ":")) + "\n"
    return hashlib.sha256(payload.encode()).hexdigest()


def _breast_payload() -> dict:
    panel = json.loads(_PANEL.read_text(encoding="utf-8"))
    return next(model for model in panel["weight_sets"] if model["trait"] == "breast_cancer")


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
    breast = _breast_payload()
    source = _read_tsv(_SUPPLEMENT)
    harmonized = _read_tsv(_HARMONIZED)
    ensembl = {
        record["rsid"]: record
        for record in json.loads(_ENSEMBL.read_text(encoding="utf-8"))["records"]
    }

    assert breast["model_status"] == "source_verified_runtime_blocked"
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
