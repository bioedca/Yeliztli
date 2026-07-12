from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from scripts.lai_bundle_v2 import lai_coverage_policy as policy_module
from scripts.lai_bundle_v2.lai_coverage_policy import (
    AGGREGATION_DIMENSIONS,
    COVERAGE_PREDICATE_OPERATORS,
    ENDPOINT_OPERATORS,
    SUPPORTED_INPUT_MASKS,
    ConfirmationPolicy,
    confirmation_policy_provenance,
    read_confirmation_policy,
)

DATASET_ID = "lai-validation-v1"
BUNDLE_SHA256 = "a" * 64
SIMULATION_SHA256 = "b" * 64
CODE_REVISION = "c" * 40
CONFIRMATION_COMMITMENT = "d" * 64


def _valid_policy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "policy_id": "lai-coverage-policy-v1",
        "frozen": True,
        "dataset_id": DATASET_ID,
        "bundle_artifact_sha256": BUNDLE_SHA256,
        "simulation_manifest_sha256": SIMULATION_SHA256,
        "code_revision": CODE_REVISION,
        "calibration_plan": {
            "configuration_sha256": "e" * 64,
            "job_plan_sha256": "f" * 64,
            "expected_job_count": 504,
        },
        "calibration_observation_sha256": "1" * 64,
        "selection_provenance": {
            "script_sha256": "2" * 64,
            "report_sha256": "3" * 64,
            "code_revision": CODE_REVISION,
        },
        "endpoints": [
            {
                "name": name,
                "op": operator,
                "value": "0.1" if operator == "<=" else "0.9",
            }
            for name, operator in ENDPOINT_OPERATORS.items()
        ],
        "aggregation": {
            "dimensions": list(AGGREGATION_DIMENSIONS),
            "all_cells_pass": True,
            "average_biological_replicates": False,
        },
        "coverage_predicates": [
            {
                "field": field,
                "op": operator,
                "value": (
                    "0.5"
                    if field.endswith("match_rate") or field.endswith("assignment_rate")
                    else "1"
                ),
            }
            for field, operator in COVERAGE_PREDICATE_OPERATORS.items()
        ],
        "confirmation_matrix": {
            "input_masks": list(SUPPORTED_INPUT_MASKS),
            "fractions": ["0.25", "0.5", "1"],
            "seeds": [-(2**63), 0, 2**63 - 1],
            "drop_scenarios": [
                {"name": "none", "dropped_autosomes": []},
                {"name": "drop_chr1", "dropped_autosomes": ["1"]},
                {"name": "drop_chr10_22", "dropped_autosomes": ["10", "22"]},
            ],
        },
        "confirmation_commitment": CONFIRMATION_COMMITMENT,
    }


def _write_policy(tmp_path: Path, payload: object | None = None) -> Path:
    path = tmp_path / "confirmation-policy.json"
    value = _valid_policy() if payload is None else payload
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _read(path: Path, **overrides: object) -> ConfirmationPolicy:
    expected_policy_sha256 = (
        hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_file() and not path.is_symlink()
        else "0" * 64
    )
    arguments: dict[str, object] = {
        "dataset_id": DATASET_ID,
        "bundle_artifact_sha256": BUNDLE_SHA256,
        "simulation_manifest_sha256": SIMULATION_SHA256,
        "code_revision": CODE_REVISION,
        "final_confirmation_split_commitment_sha256": CONFIRMATION_COMMITMENT,
        "expected_confirmation_policy_sha256": expected_policy_sha256,
    }
    arguments.update(overrides)
    return read_confirmation_policy(path, **arguments)  # type: ignore[arg-type]


def _set_nested(payload: dict[str, Any], path: tuple[str | int, ...], value: object) -> None:
    target: Any = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def test_reads_valid_policy_with_stable_source_hash(tmp_path: Path) -> None:
    path = _write_policy(tmp_path)

    parsed = _read(path)

    assert parsed.path == path
    assert parsed.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert parsed.schema_version == 1
    assert parsed.policy_id == "lai-coverage-policy-v1"
    assert parsed.frozen is True
    assert parsed.dataset_id == DATASET_ID
    assert parsed.bundle_artifact_sha256 == BUNDLE_SHA256
    assert parsed.simulation_manifest_sha256 == SIMULATION_SHA256
    assert parsed.code_revision == CODE_REVISION
    assert parsed.calibration_plan.expected_job_count == 504
    assert parsed.selection_provenance.code_revision == CODE_REVISION
    assert {endpoint.name for endpoint in parsed.endpoints} == set(ENDPOINT_OPERATORS)
    assert all(isinstance(endpoint.value, Decimal) for endpoint in parsed.endpoints)
    assert parsed.aggregation.dimensions == AGGREGATION_DIMENSIONS
    assert parsed.aggregation.all_cells_pass is True
    assert parsed.aggregation.average_biological_replicates is False
    assert {predicate.field: predicate.value for predicate in parsed.coverage_predicates} == {
        field: Decimal(
            "0.5" if field.endswith("match_rate") or field.endswith("assignment_rate") else "1"
        )
        for field in COVERAGE_PREDICATE_OPERATORS
    }
    assert parsed.confirmation_matrix.input_masks == SUPPORTED_INPUT_MASKS
    assert parsed.confirmation_matrix.fractions == (
        Decimal("0.25"),
        Decimal("0.5"),
        Decimal("1"),
    )
    assert parsed.confirmation_matrix.seeds == (-(2**63), 0, 2**63 - 1)
    assert parsed.confirmation_matrix.drop_scenarios[0].name == "none"
    assert parsed.confirmation_commitment == CONFIRMATION_COMMITMENT
    with pytest.raises(FrozenInstanceError):
        parsed.policy_id = "mutable"  # type: ignore[misc]


def test_policy_reader_enforces_artifact_size_limit(tmp_path: Path, monkeypatch) -> None:
    path = _write_policy(tmp_path)
    monkeypatch.setattr(policy_module, "MAX_POLICY_BYTES", 1)

    with pytest.raises(ValueError, match="1-byte safety limit"):
        _read(path)


def test_compact_provenance_binds_selection_and_calibration_identity(tmp_path: Path) -> None:
    parsed = _read(_write_policy(tmp_path))

    assert confirmation_policy_provenance(parsed) == {
        "schema_version": 1,
        "filename": "confirmation-policy.json",
        "sha256": parsed.sha256,
        "policy_id": "lai-coverage-policy-v1",
        "frozen": True,
        "dataset_id": DATASET_ID,
        "bundle_artifact_sha256": BUNDLE_SHA256,
        "simulation_manifest_sha256": SIMULATION_SHA256,
        "code_revision": CODE_REVISION,
        "confirmation_commitment": CONFIRMATION_COMMITMENT,
        "calibration_plan": {
            "configuration_sha256": "e" * 64,
            "job_plan_sha256": "f" * 64,
            "expected_job_count": 504,
        },
        "calibration_observation_sha256": "1" * 64,
        "selection_provenance": {
            "script_sha256": "2" * 64,
            "report_sha256": "3" * 64,
            "code_revision": CODE_REVISION,
        },
    }


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_rejects_symlinks_and_nonregular_files(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "target"
    if kind == "symlink":
        target.write_text("{}", encoding="utf-8")
        path = tmp_path / "policy-link"
        path.symlink_to(target)
        match = "must not be a symlink"
    elif kind == "directory":
        path = tmp_path / "policy-directory"
        path.mkdir()
        match = "must be a regular file"
    else:
        path = tmp_path / "policy-fifo"
        os.mkfifo(path)
        match = "must be a regular file"

    with pytest.raises(ValueError, match=match):
        _read(path)


def test_rejects_duplicate_json_object_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate JSON object key 'schema_version'"):
        _read(path)


def test_rejects_policy_bytes_that_do_not_match_independent_expected_hash(
    tmp_path: Path,
) -> None:
    path = _write_policy(tmp_path)

    with pytest.raises(ValueError, match="policy SHA-256 does not match expected identity"):
        _read(path, expected_confirmation_policy_sha256="0" * 64)


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_rejects_nonexact_top_level_schema(tmp_path: Path, change: str) -> None:
    payload = _valid_policy()
    if change == "missing":
        payload.pop("policy_id")
    else:
        payload["unexpected"] = True

    with pytest.raises(ValueError, match="confirmation policy must contain exactly"):
        _read(_write_policy(tmp_path, payload))


@pytest.mark.parametrize(
    ("path", "key", "value", "description"),
    [
        (("calibration_plan",), "configuration_sha256", "", "calibration_plan"),
        (("selection_provenance",), "unexpected", "x", "selection_provenance"),
        (("endpoints", 0), "unexpected", "x", r"endpoints\[0\]"),
        (("aggregation",), "unexpected", "x", "aggregation"),
        (("coverage_predicates", 0), "unexpected", "x", r"coverage_predicates\[0\]"),
        (("confirmation_matrix",), "unexpected", "x", "confirmation_matrix"),
        (
            ("confirmation_matrix", "drop_scenarios", 0),
            "unexpected",
            "x",
            r"drop_scenarios\[0\]",
        ),
    ],
)
def test_rejects_nonexact_nested_schemas(
    tmp_path: Path,
    path: tuple[str | int, ...],
    key: str,
    value: object,
    description: str,
) -> None:
    payload = _valid_policy()
    target: Any = payload
    for part in path:
        target = target[part]
    target[key] = value

    with pytest.raises(ValueError, match=description):
        _read(_write_policy(tmp_path, payload))


@pytest.mark.parametrize(
    ("field", "artifact_value", "argument_name", "argument_value"),
    [
        ("dataset_id", "lai-validation-v2", None, None),
        ("bundle_artifact_sha256", "4" * 64, None, None),
        ("simulation_manifest_sha256", "5" * 64, None, None),
        ("code_revision", "6" * 40, None, None),
        ("confirmation_commitment", "7" * 64, None, None),
        ("dataset_id", DATASET_ID, "dataset_id", "lai-validation-v2"),
        (
            "bundle_artifact_sha256",
            BUNDLE_SHA256,
            "bundle_artifact_sha256",
            "4" * 64,
        ),
        (
            "simulation_manifest_sha256",
            SIMULATION_SHA256,
            "simulation_manifest_sha256",
            "5" * 64,
        ),
        ("code_revision", CODE_REVISION, "code_revision", "6" * 40),
        (
            "confirmation_commitment",
            CONFIRMATION_COMMITMENT,
            "final_confirmation_split_commitment_sha256",
            "7" * 64,
        ),
    ],
)
def test_rejects_every_external_identity_mismatch(
    tmp_path: Path,
    field: str,
    artifact_value: str,
    argument_name: str | None,
    argument_value: str | None,
) -> None:
    payload = _valid_policy()
    payload[field] = artifact_value
    overrides = {argument_name: argument_value} if argument_name is not None else {}

    with pytest.raises(ValueError, match=f"{field} does not match expected identity"):
        _read(_write_policy(tmp_path, payload), **overrides)


@pytest.mark.parametrize(
    "path",
    [
        ("bundle_artifact_sha256",),
        ("simulation_manifest_sha256",),
        ("calibration_plan", "configuration_sha256"),
        ("calibration_plan", "job_plan_sha256"),
        ("calibration_observation_sha256",),
        ("selection_provenance", "script_sha256"),
        ("selection_provenance", "report_sha256"),
        ("confirmation_commitment",),
    ],
)
@pytest.mark.parametrize("bad_hash", ["a" * 63, "A" * 64, "g" * 64, 7])
def test_rejects_malformed_policy_hashes(
    tmp_path: Path, path: tuple[str, ...], bad_hash: object
) -> None:
    payload = _valid_policy()
    _set_nested(payload, path, bad_hash)

    with pytest.raises(ValueError, match="full lowercase SHA-256"):
        _read(_write_policy(tmp_path, payload))


@pytest.mark.parametrize(
    ("argument", "value", "message"),
    [
        ("bundle_artifact_sha256", "A" * 64, "expected bundle_artifact_sha256"),
        ("simulation_manifest_sha256", "b" * 63, "expected simulation_manifest_sha256"),
        (
            "final_confirmation_split_commitment_sha256",
            "z" * 64,
            "expected final_confirmation_split_commitment_sha256",
        ),
        ("code_revision", "C" * 40, "expected code_revision"),
        ("dataset_id", "", "expected dataset_id"),
        (
            "expected_confirmation_policy_sha256",
            "A" * 64,
            "expected confirmation policy SHA-256",
        ),
    ],
)
def test_rejects_malformed_expected_identities(
    tmp_path: Path, argument: str, value: object, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _read(_write_policy(tmp_path), **{argument: value})


@pytest.mark.parametrize("bad_revision", ["c" * 39, "C" * 40, "z" * 40, 9])
@pytest.mark.parametrize("path", [("code_revision",), ("selection_provenance", "code_revision")])
def test_rejects_malformed_policy_revisions(
    tmp_path: Path, path: tuple[str, ...], bad_revision: object
) -> None:
    payload = _valid_policy()
    _set_nested(payload, path, bad_revision)

    with pytest.raises(ValueError, match="full lowercase 40-hex revision"):
        _read(_write_policy(tmp_path, payload))


def test_rejects_selection_revision_different_from_policy_revision(tmp_path: Path) -> None:
    payload = _valid_policy()
    payload["selection_provenance"]["code_revision"] = "4" * 40

    with pytest.raises(ValueError, match="selection_provenance.code_revision does not match"):
        _read(_write_policy(tmp_path, payload))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", 2, "unsupported confirmation policy schema"),
        ("schema_version", True, "schema_version must be integer 1"),
        ("policy_id", "", "policy_id must be a nonempty canonical identifier"),
        ("policy_id", " whitespace ", "policy_id must be a nonempty canonical identifier"),
        ("frozen", False, "must set frozen to true"),
        ("frozen", 1, "must set frozen to true"),
    ],
)
def test_rejects_unfrozen_or_malformed_policy_header(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    payload = _valid_policy()
    payload[field] = value

    with pytest.raises(ValueError, match=message):
        _read(_write_policy(tmp_path, payload))


@pytest.mark.parametrize("count", [0, -1, True, "504", 1.0])
def test_rejects_invalid_calibration_plan_count(tmp_path: Path, count: object) -> None:
    payload = _valid_policy()
    payload["calibration_plan"]["expected_job_count"] = count

    with pytest.raises(ValueError, match="expected_job_count must be a positive integer"):
        _read(_write_policy(tmp_path, payload))


def test_rejects_calibration_plan_count_above_hard_plan_limit(tmp_path: Path) -> None:
    payload = _valid_policy()
    payload["calibration_plan"]["expected_job_count"] = 1_000_001

    with pytest.raises(ValueError, match="no greater than 1000000"):
        _read(_write_policy(tmp_path, payload))


def test_rejects_wrong_endpoint_count(tmp_path: Path) -> None:
    payload = _valid_policy()
    payload["endpoints"].pop()

    with pytest.raises(ValueError, match="endpoints must contain exactly 6 entries"):
        _read(_write_policy(tmp_path, payload))


def test_rejects_duplicate_endpoint_and_missing_required_endpoint(tmp_path: Path) -> None:
    payload = _valid_policy()
    payload["endpoints"][-1]["name"] = payload["endpoints"][0]["name"]

    with pytest.raises(ValueError, match="duplicate endpoint"):
        _read(_write_policy(tmp_path, payload))


def test_rejects_unknown_endpoint(tmp_path: Path) -> None:
    payload = _valid_policy()
    payload["endpoints"][0]["name"] = "pooled_mean_accuracy"

    with pytest.raises(ValueError, match="not a supported endpoint"):
        _read(_write_policy(tmp_path, payload))


def test_rejects_wrong_endpoint_operator(tmp_path: Path) -> None:
    payload = _valid_policy()
    payload["endpoints"][0]["op"] = "<="

    with pytest.raises(ValueError, match="must use operator"):
        _read(_write_policy(tmp_path, payload))


@pytest.mark.parametrize(
    "value",
    ["0.90", "1.0", "00", "1e-1", "NaN", "Infinity", "-0", "-0.1", "1.01", 0.9],
)
def test_rejects_noncanonical_or_out_of_range_endpoint_value(
    tmp_path: Path, value: object
) -> None:
    payload = _valid_policy()
    payload["endpoints"][0]["value"] = value

    with pytest.raises(ValueError, match=r"endpoints\[0\].value"):
        _read(_write_policy(tmp_path, payload))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("dimensions", ["input_mask"], "dimensions must exactly match"),
        ("dimensions", list(reversed(AGGREGATION_DIMENSIONS)), "dimensions must exactly match"),
        ("all_cells_pass", False, "all_cells_pass must be true"),
        ("all_cells_pass", 1, "all_cells_pass must be true"),
        ("average_biological_replicates", True, "must be false"),
        ("average_biological_replicates", 0, "must be false"),
    ],
)
def test_rejects_non_fail_closed_aggregation(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    payload = _valid_policy()
    payload["aggregation"][field] = value

    with pytest.raises(ValueError, match=message):
        _read(_write_policy(tmp_path, payload))


def test_rejects_incomplete_coverage_predicates(tmp_path: Path) -> None:
    payload = _valid_policy()
    payload["coverage_predicates"].pop()

    with pytest.raises(
        ValueError,
        match="coverage_predicates must contain every required production telemetry field",
    ):
        _read(_write_policy(tmp_path, payload))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("field", "unknown.total", "field is not supported"),
        ("op", ">", "must use operator"),
        ("value", "1.0", "canonical nonnegative decimal string"),
        ("value", "-1", "canonical nonnegative decimal string"),
        ("value", "NaN", "canonical nonnegative decimal string"),
        ("value", 1, "canonical nonnegative decimal string"),
    ],
)
def test_rejects_malformed_coverage_predicate(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    payload = _valid_policy()
    payload["coverage_predicates"][0][field] = value

    with pytest.raises(ValueError, match=message):
        _read(_write_policy(tmp_path, payload))


def test_rejects_duplicate_coverage_predicate_fields(tmp_path: Path) -> None:
    payload = _valid_policy()
    payload["coverage_predicates"][1]["field"] = payload["coverage_predicates"][0]["field"]
    payload["coverage_predicates"][1]["value"] = "2000"

    with pytest.raises(ValueError, match="duplicate field"):
        _read(_write_policy(tmp_path, payload))


def test_rejects_all_zero_noop_coverage_predicates(tmp_path: Path) -> None:
    payload = _valid_policy()
    for predicate in payload["coverage_predicates"]:
        predicate["value"] = "0"

    with pytest.raises(ValueError, match="meaningful positive requirement"):
        _read(_write_policy(tmp_path, payload))


def test_rejects_any_zero_coverage_predicate(tmp_path: Path) -> None:
    payload = _valid_policy()
    payload["coverage_predicates"][0]["value"] = "0"

    with pytest.raises(ValueError, match="every coverage predicate must impose a positive"):
        _read(_write_policy(tmp_path, payload))


def test_rejects_rate_predicate_above_one(tmp_path: Path) -> None:
    payload = _valid_policy()
    rate_predicate = next(
        predicate
        for predicate in payload["coverage_predicates"]
        if predicate["field"] == "model_markers.aggregate.match_rate"
    )
    rate_predicate["value"] = "1.01"

    with pytest.raises(ValueError, match=r"rate .* must be in \[0, 1\]"):
        _read(_write_policy(tmp_path, payload))


@pytest.mark.parametrize(
    ("endpoint_name", "value"),
    [
        ("assignment_completeness", "0"),
        ("global_ancestry_total_variation", "1"),
    ],
)
def test_rejects_vacuous_endpoint_thresholds(
    tmp_path: Path,
    endpoint_name: str,
    value: str,
) -> None:
    payload = _valid_policy()
    endpoint = next(
        endpoint for endpoint in payload["endpoints"] if endpoint["name"] == endpoint_name
    )
    endpoint["value"] = value

    with pytest.raises(ValueError, match="must impose a non-vacuous target"):
        _read(_write_policy(tmp_path, payload))


def test_rejects_fractional_count_predicate(tmp_path: Path) -> None:
    payload = _valid_policy()
    payload["coverage_predicates"][0]["value"] = "1000.5"

    with pytest.raises(ValueError, match="count .* must be integral"):
        _read(_write_policy(tmp_path, payload))


def test_rejects_autosome_count_above_22(tmp_path: Path) -> None:
    payload = _valid_policy()
    payload["coverage_predicates"][0] = {
        "field": "analyzed_autosomes.count",
        "op": ">=",
        "value": "23",
    }

    with pytest.raises(ValueError, match="autosome count .* must not exceed 22"):
        _read(_write_policy(tmp_path, payload))


@pytest.mark.parametrize(
    "masks",
    [
        list(SUPPORTED_INPUT_MASKS[:-1]),
        [*SUPPORTED_INPUT_MASKS[:-1], SUPPORTED_INPUT_MASKS[0]],
        list(reversed(SUPPORTED_INPUT_MASKS)),
        [*SUPPORTED_INPUT_MASKS[:-1], "unknown_mask"],
    ],
)
def test_rejects_confirmation_matrix_without_exact_supported_masks(
    tmp_path: Path, masks: list[str]
) -> None:
    payload = _valid_policy()
    payload["confirmation_matrix"]["input_masks"] = masks

    with pytest.raises(ValueError, match="exactly match the three supported masks"):
        _read(_write_policy(tmp_path, payload))


@pytest.mark.parametrize(
    "fractions",
    [
        [],
        ["0.25", "0.25", "1"],
        ["0.5", "0.25", "1"],
        ["0", "1"],
        ["0.25", "1.01"],
        ["0.25", "1.0"],
        ["0.25", "1e0"],
        ["0.25", "NaN"],
        ["0.25", 1],
    ],
)
def test_rejects_malformed_confirmation_fractions(tmp_path: Path, fractions: list[object]) -> None:
    payload = _valid_policy()
    payload["confirmation_matrix"]["fractions"] = fractions

    with pytest.raises(ValueError, match="confirmation_matrix.fractions"):
        _read(_write_policy(tmp_path, payload))


@pytest.mark.parametrize(
    "seeds",
    [
        [],
        [1, 1],
        [True],
        [1.0],
        [-(2**63) - 1],
        [2**63],
    ],
)
def test_rejects_malformed_confirmation_seeds(tmp_path: Path, seeds: list[object]) -> None:
    payload = _valid_policy()
    payload["confirmation_matrix"]["seeds"] = seeds

    with pytest.raises(ValueError, match="confirmation_matrix.seeds"):
        _read(_write_policy(tmp_path, payload))


@pytest.mark.parametrize(
    "scenarios",
    [
        [],
        [{"name": "drop_chr1", "dropped_autosomes": ["1"]}],
        [
            {"name": "none", "dropped_autosomes": []},
            {"name": "none", "dropped_autosomes": []},
        ],
        [
            {"name": "none", "dropped_autosomes": []},
            {"name": "drop_chr1", "dropped_autosomes": ["1"]},
            {"name": "also_drop_chr1", "dropped_autosomes": ["1"]},
        ],
        [{"name": "none", "dropped_autosomes": ["1"]}],
        [
            {"name": "none", "dropped_autosomes": []},
            {"name": "empty", "dropped_autosomes": []},
        ],
        [
            {"name": "none", "dropped_autosomes": []},
            {"name": "Drop Chr 1", "dropped_autosomes": ["1"]},
        ],
        [
            {"name": "none", "dropped_autosomes": []},
            {"name": "drop_chr1", "dropped_autosomes": [1]},
        ],
        [
            {"name": "none", "dropped_autosomes": []},
            {"name": "drop_chr23", "dropped_autosomes": ["23"]},
        ],
        [
            {"name": "none", "dropped_autosomes": []},
            {"name": "drop_duplicate", "dropped_autosomes": ["1", "1"]},
        ],
        [
            {"name": "none", "dropped_autosomes": []},
            {"name": "drop_unsorted", "dropped_autosomes": ["10", "2"]},
        ],
    ],
)
def test_rejects_malformed_structured_drop_scenarios(
    tmp_path: Path, scenarios: list[dict[str, object]]
) -> None:
    payload = _valid_policy()
    payload["confirmation_matrix"]["drop_scenarios"] = copy.deepcopy(scenarios)

    with pytest.raises(ValueError, match="confirmation_matrix|drop scenario"):
        _read(_write_policy(tmp_path, payload))


def test_rejects_nonfinite_json_constant_before_schema_validation(tmp_path: Path) -> None:
    path = tmp_path / "constant.json"
    path.write_text('{"value":NaN}', encoding="utf-8")

    with pytest.raises(ValueError, match="non-finite JSON constant"):
        _read(path)


def test_detects_policy_change_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_policy(tmp_path)
    real_signature = policy_module._stat_signature
    calls = 0

    def drifting_signature(value: os.stat_result) -> tuple[int, ...]:
        nonlocal calls
        calls += 1
        signature = real_signature(value)
        if calls == 3:
            return (*signature[:-1], signature[-1] + 1)
        return signature

    monkeypatch.setattr(policy_module, "_stat_signature", drifting_signature)

    with pytest.raises(ValueError, match="changed while it was read"):
        _read(path)
