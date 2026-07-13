"""Focused contract tests for deterministic LAI coverage-policy selection."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "lai_bundle_v2" / "06h_select_coverage.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("lai_coverage_selection", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


selector = _load_script_module()
plan_module = selector.lai_coverage_plan

DATASET_ID = "public-simulation-v1"
BUNDLE_SHA256 = "a" * 64
SIMULATION_SHA256 = "b" * 64
CODE_REVISION = "d" * 40
COMMITMENT_SHA256 = "c" * 64
SELECTOR_SHA256 = "e" * 64
NATIVE_MASK = "native_unmasked"
PRODUCTION_MASKS = tuple(selector.SUPPORTED_INPUT_MASKS)
CALIBRATION_MASKS = (NATIVE_MASK, *PRODUCTION_MASKS)
FRACTIONS = ("0.25", "0.5", "1")
CONFIRMATION_FRACTIONS = ("0.5", "1")
SEEDS = (42,)
DROP_SCENARIOS = ("none",)
POPULATIONS = tuple(selector.SUPERPOPULATIONS)
MODEL_MARKERS_BY_AUTOSOME = {str(chrom): 1 for chrom in range(1, 23)}
SELECTED_BY_AUTOSOME = {str(chrom): 5 if chrom <= 12 else 4 for chrom in range(1, 23)}
ASSIGNED_WINDOWS_BY_AUTOSOME = {str(chrom): 1 if chrom <= 7 else 0 for chrom in range(1, 23)}
TRUTH_WINDOWS_BY_AUTOSOME = dict(ASSIGNED_WINDOWS_BY_AUTOSOME)


def _canonical_write(path: Path, value: object, *, jsonl: bool = False) -> None:
    payload = plan_module.canonical_json_bytes(value)
    path.write_bytes(payload + (b"\n" if jsonl else b""))


def _selection_design_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "policy_id": "lai-coverage-public-v1",
        "frozen": True,
        "dataset_id": DATASET_ID,
        "bundle_artifact_sha256": BUNDLE_SHA256,
        "simulation_manifest_sha256": SIMULATION_SHA256,
        "code_revision": CODE_REVISION,
        "endpoints": [
            {"name": "assignment_completeness", "op": ">=", "value": "0.9"},
            {"name": "local_diplotype_accuracy", "op": ">=", "value": "0.9"},
            {
                "name": "local_haplotype_accuracy_best_orientation",
                "op": ">=",
                "value": "0.9",
            },
            {
                "name": "global_ancestry_total_variation",
                "op": "<=",
                "value": "0.1",
            },
            {
                "name": "per_truth_class.assignment_completeness",
                "op": ">=",
                "value": "0.9",
            },
            {
                "name": "per_truth_diplotype.local_diplotype_accuracy",
                "op": ">=",
                "value": "0.9",
            },
        ],
        "aggregation": {
            "dimensions": list(selector.AGGREGATION_DIMENSIONS),
            "all_cells_pass": True,
            "average_biological_replicates": False,
        },
        "stable_region_rule": {
            "algorithm": selector.SELECTION_ALGORITHM,
            "minimum_fraction_levels": 2,
            "require_zero_false_accepts": True,
            "require_full_density_no_drop_acceptance": True,
        },
        "confirmation_matrix": {
            "input_masks": list(PRODUCTION_MASKS),
            "fractions": list(CONFIRMATION_FRACTIONS),
            "seeds": list(SEEDS),
            "drop_scenarios": [{"name": "none", "dropped_autosomes": []}],
        },
        "final_confirmation_split_commitment_sha256": COMMITMENT_SHA256,
    }


def _write_design(tmp_path: Path):
    path = tmp_path / "selection-design.json"
    _canonical_write(path, _selection_design_payload())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, digest, selector.read_selection_design(path, expected_sha256=digest)


def _evaluation_coverage() -> dict[str, object]:
    complete = {population: 2 for population in POPULATIONS}
    return {
        "founders_by_class": dict(complete),
        "modal_truth_founders_by_class": dict(complete),
        "simulations_by_class": {population: 1 for population in POPULATIONS},
        "truth_haplotype_windows_by_class": {population: 1 for population in POPULATIONS},
        "by_validation_stratum": {
            "mosaic": {
                population: {"simulations": 1, "truth_haplotype_windows": 2}
                for population in POPULATIONS
            }
        },
    }


def _mask_scenarios(masks: tuple[str, ...]) -> dict[str, list[dict[str, object]]]:
    return {
        "SIM1": [
            {
                "name": mask,
                "kind": "native" if mask == NATIVE_MASK else "derived",
                "file_format": "fixture_tsv",
                "manifest_names": [],
                "realized_fixture_markers": 100,
            }
            for mask in masks
        ]
    }


def _base_inputs() -> dict[str, object]:
    return {
        "bundle_artifact_sha256": BUNDLE_SHA256,
        "bundle_metadata": {"sha256": "1" * 64},
        "simulation_manifest": {
            "sha256": SIMULATION_SHA256,
            "simulation_protocol": {
                "minimums": {
                    "founders_per_class_per_split": 2,
                    "simulations_per_class_per_split": 1,
                    "truth_haplotype_windows_per_class_per_split": 1,
                }
            },
        },
        "code_revision": CODE_REVISION,
        "harness_script_sha256": "2" * 64,
        "runtime_environment": {"python": {"version": "3.13.5"}},
        "labels": {"sha256": "3" * 64},
        "fixtures": {
            "SIM1": {
                "filename": "SIM1.tsv",
                "sha256": "4" * 64,
                "validation_stratum": "mosaic",
                "local_truth_filename": "SIM1.truth.json",
                "local_truth_sha256": "5" * 64,
                "local_truth_windows": 7,
                "local_truth_windows_by_autosome": dict(TRUTH_WINDOWS_BY_AUTOSOME),
                "marker_truth_filename": "SIM1.marker-truth.tsv",
                "marker_truth_sha256": "6" * 64,
                "marker_truth_rows": 14,
                "model_marker_counts_by_autosome": dict(MODEL_MARKERS_BY_AUTOSOME),
            }
        },
        "privacy_safe_site_masks": {},
        "evaluation_coverage": _evaluation_coverage(),
    }


def _calibration_configuration(design_path: Path, design_sha256: str) -> dict[str, object]:
    inputs = _base_inputs()
    inputs["selection_design"] = {
        "filename": design_path.name,
        "sha256": design_sha256,
    }
    return {
        "dataset_id": DATASET_ID,
        "dataset_split": "calibration",
        "coverage_enforcement": "disabled_for_diagnostics",
        "threshold_selected": None,
        "chromosome_drop_scenarios": [{"name": "none", "dropped_autosomes": []}],
        "mask_scenarios": _mask_scenarios(CALIBRATION_MASKS),
        "inputs": inputs,
    }


def _build_plan(
    tmp_path: Path,
    *,
    name: str,
    configuration: dict[str, object],
    input_verification: dict[str, object],
    dataset_split: str,
    masks: tuple[str, ...],
    fractions: tuple[str, ...],
):
    path = tmp_path / f"{name}.json"
    result = plan_module.build_job_plan(
        path,
        configuration=configuration,
        input_verification=input_verification,
        dataset_split=dataset_split,
        fixture_masks=(("SIM1", masks),),
        drop_scenarios=DROP_SCENARIOS,
        fractions=fractions,
        seeds=SEEDS,
        disk_reserve_bytes=0,
    )
    summary = plan_module.read_job_plan_summary(path, result.configuration_sha256)
    return result, summary


def _telemetry(*, low: bool = False) -> dict[str, object]:
    model_by_autosome = {
        str(chrom): {
            "matched": 0 if low and chrom > 11 else 1,
            "total": 1,
            "allele_mismatch": 0,
            "match_rate": 0.0 if low and chrom > 11 else 1.0,
        }
        for chrom in range(1, 23)
    }
    matched = sum(entry["matched"] for entry in model_by_autosome.values())
    expected_windows_by_autosome = {
        chrom: 2 * count for chrom, count in ASSIGNED_WINDOWS_BY_AUTOSOME.items()
    }
    return {
        "schema_version": 1,
        "model_denominators": {"complete": True, "unreadable_autosomes": []},
        "emitted_markers": {
            "total": sum(SELECTED_BY_AUTOSOME.values()),
            "by_autosome": dict(SELECTED_BY_AUTOSOME),
        },
        "model_markers": {
            "aggregate": {
                "matched": matched,
                "total": 22,
                "allele_mismatch": 0,
                "match_rate": round(matched / 22, 6),
            },
            "by_autosome": model_by_autosome,
        },
        "phased_autosomes": {"count": 22, "identities": list(range(1, 23))},
        "analyzed_autosomes": {"count": 7, "identities": list(range(1, 8))},
        "haplotype_windows": {
            "expected": 14,
            "valid_assigned": 14,
            "assignment_rate": 1.0,
            "expected_by_autosome": expected_windows_by_autosome,
            "valid_assigned_by_autosome": dict(expected_windows_by_autosome),
        },
        "per_source": {"fixture": {"hits": 100, "drops": 0}},
    }


def _accuracy(*, endpoint_failure: bool = False) -> dict[str, object]:
    correct_diplotypes = 6 if endpoint_failure else 7
    diplotype_accuracy = correct_diplotypes / 7
    return {
        "truth_windows_expected": 7,
        "windows_assigned": 7,
        "windows_assigned_by_autosome": dict(ASSIGNED_WINDOWS_BY_AUTOSOME),
        "haplotype_calls_expected": 14,
        "diplotype_windows_correct": correct_diplotypes,
        "haplotype_calls_correct_best_orientation": 14,
        "assignment_completeness": 1.0,
        "local_diplotype_accuracy": diplotype_accuracy,
        "local_haplotype_accuracy_best_orientation": 1.0,
        "global_ancestry_total_variation": 0.0,
        "per_truth_class": {
            population: {
                "truth_haplotype_calls_expected": 2,
                "assigned_haplotype_calls": 2,
                "correct_haplotype_calls_best_orientation": 2,
                "assignment_completeness": 1.0,
                "local_haplotype_accuracy_best_orientation": 1.0,
            }
            for population in POPULATIONS
        },
        "per_truth_diplotype": {
            "AFR/EUR": {
                "windows_expected": 7,
                "windows_assigned": 7,
                "windows_correct": correct_diplotypes,
                "assignment_completeness": 1.0,
                "local_diplotype_accuracy": diplotype_accuracy,
            }
        },
    }


def _observation(
    summary,
    index: int,
    *,
    policy=None,
    status: str = "ok",
    low_telemetry: bool = False,
    endpoint_failure: bool = False,
) -> dict[str, object]:
    configuration = summary.configuration
    inputs = configuration["inputs"]
    row = summary.matrix.row_at(index)
    iid = row["iid"]
    fixture = inputs["fixtures"][iid]
    mask_config = next(
        item for item in configuration["mask_scenarios"][iid] if item["name"] == row["mask"]
    )
    metrics = _telemetry(low=low_telemetry)
    manifest_provenance: dict[str, object] = {}
    provenance: dict[str, object] = {
        "bundle_metadata_sha256": inputs["bundle_metadata"]["sha256"],
        "bundle_artifact_sha256": inputs["bundle_artifact_sha256"],
        "code_revision": inputs["code_revision"],
        "harness_script_sha256": inputs["harness_script_sha256"],
        "runtime_environment": inputs["runtime_environment"],
        "fixture_sha256": fixture["sha256"],
        "local_truth_sha256": fixture["local_truth_sha256"],
        "marker_truth_sha256": fixture["marker_truth_sha256"],
        "labels_sha256": inputs["labels"]["sha256"],
        "mask_sha256": {},
        "configuration_sha256": summary.configuration_sha256,
    }
    if policy is not None:
        provenance["confirmation_policy"] = selector.confirmation_policy_provenance(policy)
    record: dict[str, object] = {
        "schema_version": 1,
        "job_index": index,
        "dataset_id": configuration["dataset_id"],
        "dataset_split": row["dataset_split"],
        "sample": {
            "iid": iid,
            "validation_stratum": fixture["validation_stratum"],
            "fixture_path": f"/sealed/{fixture['filename']}",
            "fixture_parsing": {"schema_version": 1},
            "local_truth_path": f"/sealed/{fixture['local_truth_filename']}",
            "local_truth_windows": fixture["local_truth_windows"],
            "marker_truth_path": f"/sealed/{fixture['marker_truth_filename']}",
            "marker_truth_rows": fixture["marker_truth_rows"],
        },
        "mask": {
            "name": row["mask"],
            "kind": mask_config["kind"],
            "file_format": mask_config["file_format"],
            "manifest_provenance": manifest_provenance,
            "realized_fixture_markers": mask_config["realized_fixture_markers"],
            "source_counts": {},
        },
        "chromosome_drop_scenario": {"name": "none", "dropped_autosomes": []},
        "downsampling": {
            "fraction": float(row["fraction"]),
            "fraction_canonical": row["fraction"],
            "seed": row["seed"],
        },
        "input_coverage": {
            "markers_before_downsampling": 100,
            "markers_after_downsampling_before_chromosome_drop": 100,
            "markers_after_chromosome_drop": 100,
            "markers_selected": 100,
            "selected_by_autosome": dict(SELECTED_BY_AUTOSOME),
            "selected_by_source": {"fixture": 100},
            "autosomes_present": list(SELECTED_BY_AUTOSOME),
        },
        "provenance": provenance,
        "status": status,
        "coverage_metadata": (
            {"final_lai_coverage_metrics": metrics}
            if status == "ok"
            else {"last_progressive_snapshot": metrics}
        ),
        "local_diplotype_accuracy": None,
        "local_haplotype_accuracy_best_orientation": None,
        "assignment_completeness": None,
        "global_ancestry_total_variation": None,
        "accuracy": None,
        "calibration_eligible": status == "ok",
        "calibration_exclusion": None if status == "ok" else {"type": status},
        "error": None if status == "ok" else {"type": status},
    }
    if status == "ok":
        accuracy = _accuracy(endpoint_failure=endpoint_failure)
        record.update(
            {
                "local_diplotype_accuracy": accuracy["local_diplotype_accuracy"],
                "local_haplotype_accuracy_best_orientation": accuracy[
                    "local_haplotype_accuracy_best_orientation"
                ],
                "assignment_completeness": accuracy["assignment_completeness"],
                "global_ancestry_total_variation": accuracy["global_ancestry_total_variation"],
                "accuracy": accuracy,
            }
        )
    return record


def _write_observations(summary, path: Path, *, policy=None, mutate=None) -> list[dict]:
    path.mkdir()
    records = []
    for index in range(summary.matrix.row_count):
        record = _observation(summary, index, policy=policy)
        if mutate is not None:
            mutate(record, summary.matrix.row_at(index))
        records.append(record)
        _canonical_write(path / f"{index}.jsonl", record, jsonl=True)
        (path / f".{index}.jsonl.lock").touch()
    return records


def _calibration_case(tmp_path: Path, *, mutate=None):
    design_path, design_sha256, design = _write_design(tmp_path)
    configuration = _calibration_configuration(design_path, design_sha256)
    result, summary = _build_plan(
        tmp_path,
        name="calibration-plan",
        configuration=configuration,
        input_verification={
            "selection_design": {"sha256": design_sha256, "size_bytes": design_path.stat().st_size}
        },
        dataset_split="calibration",
        masks=CALIBRATION_MASKS,
        fractions=FRACTIONS,
    )
    selector.validate_calibration_plan(
        summary,
        design,
        expected_job_plan_sha256=summary.plan_sha256,
    )
    observations = tmp_path / "calibration-observations"
    records = _write_observations(summary, observations, mutate=mutate)
    return SimpleNamespace(
        design_path=design_path,
        design_sha256=design_sha256,
        design=design,
        result=result,
        summary=summary,
        observations=observations,
        records=records,
    )


def _select(case):
    return selector.build_selection_artifacts(
        case.summary,
        case.observations,
        case.design,
        selector_script_sha256=SELECTOR_SHA256,
    )


def _write_and_read_policy(tmp_path: Path, case, artifacts):
    assert artifacts.policy_bytes is not None
    path = tmp_path / "confirmation-policy.json"
    path.write_bytes(artifacts.policy_bytes)
    digest = hashlib.sha256(artifacts.policy_bytes).hexdigest()
    policy = selector.read_confirmation_policy(
        path,
        dataset_id=DATASET_ID,
        bundle_artifact_sha256=BUNDLE_SHA256,
        simulation_manifest_sha256=SIMULATION_SHA256,
        code_revision=CODE_REVISION,
        final_confirmation_split_commitment_sha256=COMMITMENT_SHA256,
        expected_confirmation_policy_sha256=digest,
    )
    return path, policy


def _final_configuration(policy) -> dict[str, object]:
    inputs = _base_inputs()
    inputs["confirmation_policy"] = selector.confirmation_policy_provenance(policy)
    return {
        "dataset_id": DATASET_ID,
        "dataset_split": "final_confirmation",
        "coverage_enforcement": "disabled_for_diagnostics",
        "threshold_selected": policy.policy_id,
        "chromosome_drop_scenarios": [{"name": "none", "dropped_autosomes": []}],
        "mask_scenarios": _mask_scenarios(PRODUCTION_MASKS),
        "inputs": inputs,
    }


def _final_case(tmp_path: Path, policy, *, name: str = "final"):
    result, summary = _build_plan(
        tmp_path,
        name=f"{name}-plan",
        configuration=_final_configuration(policy),
        input_verification={},
        dataset_split="final_confirmation",
        masks=PRODUCTION_MASKS,
        fractions=CONFIRMATION_FRACTIONS,
    )
    selector.validate_final_plan(
        summary,
        policy,
        expected_job_plan_sha256=summary.plan_sha256,
    )
    observations = tmp_path / f"{name}-observations"
    records = _write_observations(summary, observations, policy=policy)
    return SimpleNamespace(
        result=result,
        summary=summary,
        observations=observations,
        records=records,
    )


def test_valid_selection_design_parser_is_hash_bound(tmp_path):
    path, digest, design = _write_design(tmp_path)

    assert design.sha256 == digest
    assert design.confirmation_matrix.fractions == tuple(
        selector.Decimal(value) for value in CONFIRMATION_FRACTIONS
    )
    with pytest.raises(ValueError, match="independent identity"):
        selector.read_selection_design(path, expected_sha256="0" * 64)


def test_selection_design_rejects_boolean_schema_version(tmp_path):
    payload = _selection_design_payload()
    payload["schema_version"] = True
    path = tmp_path / "selection-design.json"
    _canonical_write(path, payload)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="schema version 1"):
        selector.read_selection_design(path, expected_sha256=digest)


def test_complete_calibration_selects_round_trippable_deterministic_policy(tmp_path):
    case = _calibration_case(tmp_path)

    first = _select(case)
    second = _select(case)

    assert first.report["selection_status"] == "selected"
    assert first.policy_bytes is not None
    assert first.report_bytes == second.report_bytes
    assert first.policy_bytes == second.policy_bytes
    _path, policy = _write_and_read_policy(tmp_path, case, first)
    assert policy.policy_id == case.design.policy_id
    assert tuple(predicate.field for predicate in policy.coverage_predicates) == tuple(
        selector.COVERAGE_PREDICATE_OPERATORS
    )
    assert first.report["uncertainty"] == {
        "status": "not_estimable_from_dependent_simulation_sweep",
        "confidence_interval": None,
        "reason": first.report["uncertainty"]["reason"],
        "simulation_iid_count": 1,
        "simulation_iids_by_validation_stratum": {"mosaic": 1},
        "declared_relationship_component_count": None,
        "marker_removal_seed_count": 1,
        "marker_removal_seeds_are_independent_biological_replicates": False,
        "aggregation_uses_raw_all_cell_pass": True,
    }


def test_cli_paths_reject_symlinked_ancestor(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink component"):
        selector._lexical_absolute(alias / "output")


def test_observation_reader_rejects_raced_fifo_without_blocking(tmp_path):
    case = _calibration_case(tmp_path)
    result_path = case.observations / "0.jsonl"
    result_path.unlink()
    os.mkfifo(result_path)

    with selector.ObservationDirectory(
        case.observations, case.summary.matrix.row_count
    ) as observations:
        with pytest.raises(ValueError, match="unsafe or oversized observation"):
            observations.read_record(0)


def test_operational_error_blocks_selection(tmp_path):
    def mutate(record, row):
        if row["mask"] == NATIVE_MASK and row["fraction"] == "0.25":
            record.update(_failed_fields("operational_error", low=True))

    artifacts = _select(_calibration_case(tmp_path, mutate=mutate))

    assert artifacts.policy is None
    assert artifacts.report["refusal_reasons"] == [
        "unresolved_operational_or_invalid_observations"
    ]


def _failed_fields(status: str, *, low: bool) -> dict[str, object]:
    return {
        "status": status,
        "coverage_metadata": {"last_progressive_snapshot": _telemetry(low=low)},
        "local_diplotype_accuracy": None,
        "local_haplotype_accuracy_best_orientation": None,
        "assignment_completeness": None,
        "global_ancestry_total_variation": None,
        "accuracy": None,
        "calibration_eligible": False,
        "calibration_exclusion": {"type": status},
        "error": {"type": status},
    }


def test_low_density_coverage_failure_is_an_allowed_rejected_negative(tmp_path):
    def mutate(record, row):
        if row["mask"] == PRODUCTION_MASKS[0] and row["fraction"] == "0.25":
            record.update(_failed_fields("coverage_failure", low=True))

    artifacts = _select(_calibration_case(tmp_path, mutate=mutate))

    assert artifacts.policy is not None
    evaluation = artifacts.report["evaluation"]
    assert evaluation["unsafe_production_mask_rows"] == 1
    assert evaluation["false_accept_count"] == 0


def test_ok_observation_rejects_internally_inconsistent_coverage_telemetry(tmp_path):
    def mutate(record, row):
        if row["mask"] == PRODUCTION_MASKS[0] and row["fraction"] == "0.25":
            aggregate = record["coverage_metadata"]["final_lai_coverage_metrics"]["model_markers"][
                "aggregate"
            ]
            aggregate["matched"] -= 1

    case = _calibration_case(tmp_path, mutate=mutate)

    with pytest.raises(ValueError, match="inconsistent coverage telemetry.*invalid_model_markers"):
        _select(case)


def test_ok_observation_cannot_reduce_authenticated_truth_denominators(tmp_path):
    def mutate(record, row):
        if row["mask"] != PRODUCTION_MASKS[0] or row["fraction"] != "0.25":
            return
        accuracy = record["accuracy"]
        accuracy.update(
            {
                "truth_windows_expected": 6,
                "windows_assigned": 6,
                "windows_assigned_by_autosome": {
                    chrom: count if chrom != "7" else 0
                    for chrom, count in ASSIGNED_WINDOWS_BY_AUTOSOME.items()
                },
                "haplotype_calls_expected": 12,
                "diplotype_windows_correct": 6,
                "haplotype_calls_correct_best_orientation": 12,
            }
        )
        accuracy["per_truth_class"]["OCE"] = {
            "truth_haplotype_calls_expected": 0,
            "assigned_haplotype_calls": 0,
            "correct_haplotype_calls_best_orientation": 0,
            "assignment_completeness": None,
            "local_haplotype_accuracy_best_orientation": None,
        }
        accuracy["per_truth_diplotype"]["AFR/EUR"].update(
            {"windows_expected": 6, "windows_assigned": 6, "windows_correct": 6}
        )
        windows = record["coverage_metadata"]["final_lai_coverage_metrics"]["haplotype_windows"]
        reduced_by_autosome = {
            chrom: count if chrom != "7" else 0
            for chrom, count in windows["expected_by_autosome"].items()
        }
        windows.update(
            {
                "expected": 12,
                "valid_assigned": 12,
                "expected_by_autosome": reduced_by_autosome,
                "valid_assigned_by_autosome": dict(reduced_by_autosome),
            }
        )

    case = _calibration_case(tmp_path, mutate=mutate)

    with pytest.raises(ValueError, match="denominator differs from the authenticated plan"):
        _select(case)


def test_stable_region_endpoint_failure_refuses_policy(tmp_path):
    def mutate(record, row):
        if row["mask"] == PRODUCTION_MASKS[0] and row["fraction"] == "0.5":
            accuracy = _accuracy(endpoint_failure=True)
            record["local_diplotype_accuracy"] = accuracy["local_diplotype_accuracy"]
            record["accuracy"] = accuracy

    artifacts = _select(_calibration_case(tmp_path, mutate=mutate))

    assert artifacts.policy is None
    assert "predeclared_stable_region_has_unsafe_cells" in artifacts.report["refusal_reasons"]


def test_explicit_zero_class_denominator_is_skipped_with_plan_level_coverage(tmp_path):
    case = _calibration_case(tmp_path)
    record = copy.deepcopy(case.records[0])
    classes = record["accuracy"]["per_truth_class"]
    classes["OCE"] = {
        "truth_haplotype_calls_expected": 0,
        "assigned_haplotype_calls": 0,
        "correct_haplotype_calls_best_orientation": 0,
        "assignment_completeness": None,
        "local_haplotype_accuracy_best_orientation": None,
    }
    classes["AFR"] = {
        "truth_haplotype_calls_expected": 4,
        "assigned_haplotype_calls": 4,
        "correct_haplotype_calls_best_orientation": 4,
        "assignment_completeness": 1.0,
        "local_haplotype_accuracy_best_orientation": 1.0,
    }

    evaluation = selector.evaluate_observation(record, case.design.endpoints)

    assert evaluation.endpoint_pass is True
    assert evaluation.endpoint_details["per_truth_class.assignment_completeness"] == "AFR"


def test_false_accept_outside_stable_region_refuses_policy(tmp_path):
    def mutate(record, row):
        if row["mask"] == PRODUCTION_MASKS[0] and row["fraction"] == "0.25":
            record.update(_failed_fields("coverage_failure", low=False))

    artifacts = _select(_calibration_case(tmp_path, mutate=mutate))

    assert artifacts.policy is None
    assert "coverage_predicates_false_accept_unsafe_rows" in artifacts.report["refusal_reasons"]
    assert artifacts.report["evaluation"]["false_accept_count"] == 1


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "extra", "noncanonical"])
def test_observation_ledger_rejects_incomplete_or_ambiguous_rows(tmp_path, corruption):
    case = _calibration_case(tmp_path)
    if corruption == "missing":
        (case.observations / "0.jsonl").unlink()
        match = "exactly one result and lock"
    elif corruption == "duplicate":
        duplicate = copy.deepcopy(case.records[1])
        duplicate["job_index"] = 0
        _canonical_write(case.observations / "1.jsonl", duplicate, jsonl=True)
        match = "job_index differs"
    elif corruption == "extra":
        (case.observations / "unexpected.txt").touch()
        match = "unexpected observation-directory entry"
    else:
        (case.observations / "0.jsonl").write_text(
            json.dumps(case.records[0]) + "\n", encoding="utf-8"
        )
        match = "not canonical JSONL"

    with pytest.raises(ValueError, match=match):
        _select(case)


def test_final_plan_must_repeat_exact_policy_provenance(tmp_path):
    calibration = _calibration_case(tmp_path)
    artifacts = _select(calibration)
    _path, policy = _write_and_read_policy(tmp_path, calibration, artifacts)
    configuration = _final_configuration(policy)
    configuration["inputs"]["confirmation_policy"]["sha256"] = "f" * 64
    _result, summary = _build_plan(
        tmp_path,
        name="bad-final-plan",
        configuration=configuration,
        input_verification={},
        dataset_split="final_confirmation",
        masks=PRODUCTION_MASKS,
        fractions=CONFIRMATION_FRACTIONS,
    )

    with pytest.raises(ValueError, match="provenance differs"):
        selector.validate_final_plan(
            summary,
            policy,
            expected_job_plan_sha256=summary.plan_sha256,
        )


@pytest.mark.parametrize("failure", ["endpoint", "predicate"])
def test_final_confirmation_fails_closed_on_endpoint_or_predicate(tmp_path, failure):
    calibration = _calibration_case(tmp_path)
    artifacts = _select(calibration)
    _path, policy = _write_and_read_policy(tmp_path, calibration, artifacts)
    final = _final_case(tmp_path, policy)

    report, _encoded, passed = selector.build_confirmation_report(
        final.summary,
        final.observations,
        policy,
    )
    assert passed is True
    assert report["evaluation"]["failure_count"] == 0

    record = copy.deepcopy(final.records[0])
    if failure == "endpoint":
        accuracy = _accuracy(endpoint_failure=True)
        record["local_diplotype_accuracy"] = accuracy["local_diplotype_accuracy"]
        record["accuracy"] = accuracy
    else:
        record["coverage_metadata"]["final_lai_coverage_metrics"] = _telemetry(low=True)
    _canonical_write(final.observations / "0.jsonl", record, jsonl=True)

    report, _encoded, passed = selector.build_confirmation_report(
        final.summary,
        final.observations,
        policy,
    )
    assert passed is False
    assert report["evaluation"]["failure_count"] == 1


def test_final_observation_must_repeat_exact_policy_provenance(tmp_path):
    calibration = _calibration_case(tmp_path)
    artifacts = _select(calibration)
    _path, policy = _write_and_read_policy(tmp_path, calibration, artifacts)
    final = _final_case(tmp_path, policy)
    record = copy.deepcopy(final.records[0])
    record["provenance"]["confirmation_policy"]["sha256"] = "f" * 64
    _canonical_write(final.observations / "0.jsonl", record, jsonl=True)

    with pytest.raises(ValueError, match="confirmation_policy differs"):
        selector.build_confirmation_report(final.summary, final.observations, policy)
