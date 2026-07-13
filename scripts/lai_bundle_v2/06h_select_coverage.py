#!/usr/bin/env python3
"""Select and confirm a preregistered fail-closed LAI coverage policy.

``select`` authenticates a complete calibration plan and its one-record JSONL
outputs, applies a selection design that was hash-bound into that plan before
the sweep ran, and either publishes a deterministic frozen policy or records a
deterministic refusal.  ``confirm`` independently reconstructs that selection
lineage before applying the frozen predicates and truth endpoints once to the
complete, founder-disjoint final-confirmation matrix.

No command in this module changes production LAI behavior.  A positive policy
is an evidence artifact; production enforcement is a separate release step.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import lai_coverage_metrics  # noqa: E402
import lai_coverage_plan  # noqa: E402
from lai_coverage_policy import (  # noqa: E402
    AGGREGATION_DIMENSIONS,
    COVERAGE_PREDICATE_OPERATORS,
    ENDPOINT_OPERATORS,
    SUPPORTED_INPUT_MASKS,
    AggregationPolicy,
    ChromosomeDropScenario,
    ConfirmationMatrix,
    ConfirmationPolicy,
    PolicyEndpoint,
    confirmation_policy_provenance,
    read_confirmation_policy,
)

SCHEMA_VERSION = 1
SELECTION_ALGORITHM = "predeclared_complete_region_componentwise_minimum_v1"
OBSERVATION_HASH_DOMAIN = b"yeliztli:lai-coverage-observations:v1\x00"
MAX_DESIGN_BYTES = 4 * 1024 * 1024
MAX_REPORT_BYTES = 16 * 1024 * 1024
MAX_OBSERVATION_BYTES = 16 * 1024 * 1024
MAX_FAILURE_EXAMPLES = 100
NATIVE_MASK = "native_unmasked"
SUPERPOPULATIONS = ("AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE")

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_GIT_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]*\Z")
_SCENARIO_RE = re.compile(r"[a-z][a-z0-9_-]*\Z")
_CANONICAL_DECIMAL_RE = re.compile(r"(?:0|0\.[0-9]*[1-9]|[1-9][0-9]*(?:\.[0-9]*[1-9])?)\Z")
_AUTOSOMES = tuple(str(chromosome) for chromosome in range(1, 23))
_AUTOSOME_SET = frozenset(_AUTOSOMES)
_COUNT_TELEMETRY = frozenset(
    {
        "emitted_markers.total",
        "model_markers.aggregate.matched",
        "phased_autosomes.count",
        "analyzed_autosomes.count",
        "haplotype_windows.valid_assigned",
    }
)
_AUTOSOME_TELEMETRY = frozenset({"phased_autosomes.count", "analyzed_autosomes.count"})


@dataclass(frozen=True, slots=True)
class StableRegionRule:
    algorithm: str
    minimum_fraction_levels: int
    require_zero_false_accepts: bool
    require_full_density_no_drop_acceptance: bool


@dataclass(frozen=True, slots=True)
class SelectionDesign:
    path: Path
    sha256: str
    policy_id: str
    dataset_id: str
    bundle_artifact_sha256: str
    simulation_manifest_sha256: str
    code_revision: str
    endpoints: tuple[PolicyEndpoint, ...]
    aggregation: AggregationPolicy
    rule: StableRegionRule
    confirmation_matrix: ConfirmationMatrix
    confirmation_commitment: str


@dataclass(frozen=True, slots=True)
class CellEvaluation:
    endpoint_pass: bool
    endpoint_values: dict[str, Decimal]
    endpoint_details: dict[str, str]
    telemetry: dict[str, Decimal] | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObservationScan:
    observation_sha256: str
    row_count: int
    status_counts: dict[str, int]
    blocking_counts: dict[str, int]
    blocking_examples: tuple[dict[str, object], ...]
    stable_row_count: int
    stable_failure_count: int
    stable_failure_examples: tuple[dict[str, object], ...]
    stable_minima: dict[str, Decimal]
    worst_endpoints: dict[str, dict[str, object]]
    unsafe_row_count: int
    false_accept_count: int
    false_accept_examples: tuple[dict[str, object], ...]
    safe_row_count: int
    safe_accept_count: int
    safe_reject_examples: tuple[dict[str, object], ...]
    safe_acceptance_by_group: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class SelectionArtifacts:
    report: dict[str, object]
    report_bytes: bytes
    policy: dict[str, object] | None
    policy_bytes: bytes | None


@dataclass(frozen=True, slots=True)
class _StableFile:
    path: Path
    payload: bytes
    sha256: str
    signature: tuple[int, ...]


def _exact_mapping(value: object, keys: set[str], description: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{description} must contain exactly {sorted(keys)!r}")
    return value


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r} is not permitted")


def _stat_signature(observed: os.stat_result) -> tuple[int, ...]:
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_mode,
        observed.st_nlink,
        observed.st_uid,
        observed.st_gid,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
    )


def _read_stable_regular_file(
    path: Path,
    description: str,
    *,
    maximum_bytes: int,
) -> _StableFile:
    try:
        before_path = path.lstat()
    except OSError as exc:
        raise ValueError(f"{path}: cannot stat {description}: {exc}") from exc
    if stat.S_ISLNK(before_path.st_mode) or not stat.S_ISREG(before_path.st_mode):
        raise ValueError(f"{path}: {description} must be a non-symlink regular file")
    if before_path.st_size > maximum_bytes:
        raise ValueError(f"{path}: {description} exceeds {maximum_bytes} bytes")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{path}: cannot safely open {description}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or _stat_signature(before) != _stat_signature(
            before_path
        ):
            raise ValueError(f"{path}: {description} changed while it was opened")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            total += len(chunk)
            if total > maximum_bytes:
                raise ValueError(f"{path}: {description} exceeds {maximum_bytes} bytes")
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if _stat_signature(before) != _stat_signature(after):
        raise ValueError(f"{path}: {description} changed while it was read")
    try:
        after_path = path.lstat()
    except OSError as exc:
        raise ValueError(f"{path}: {description} changed after it was read") from exc
    if _stat_signature(after) != _stat_signature(after_path):
        raise ValueError(f"{path}: {description} pathname changed while it was read")
    payload = b"".join(chunks)
    return _StableFile(
        path=path,
        payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        signature=_stat_signature(after),
    )


def _read_canonical_json_file(
    path: Path,
    description: str,
    *,
    maximum_bytes: int,
) -> tuple[Mapping[str, object], _StableFile]:
    stable = _read_stable_regular_file(path, description, maximum_bytes=maximum_bytes)
    try:
        value = json.loads(
            stable.payload,
            object_pairs_hook=_reject_duplicate_object_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{path}: invalid {description} JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"{path}: {description} must be a JSON object")
    if lai_coverage_plan.canonical_json_bytes(value) != stable.payload:
        raise ValueError(f"{path}: {description} must use canonical JSON bytes")
    return value, stable


def _sha256(value: object, description: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{description} must be a full lowercase SHA-256")
    return value


def _git_revision(value: object, description: str) -> str:
    if not isinstance(value, str) or _GIT_REVISION_RE.fullmatch(value) is None:
        raise ValueError(f"{description} must be a full lowercase 40-hex revision")
    return value


def _identifier(value: object, description: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{description} must be a nonempty canonical identifier")
    return value


def _canonical_decimal(value: object, description: str) -> Decimal:
    if not isinstance(value, str) or _CANONICAL_DECIMAL_RE.fullmatch(value) is None:
        raise ValueError(f"{description} must be a canonical nonnegative decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"{description} is not a decimal") from None
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{description} must be finite and nonnegative")
    return parsed


def _unit_decimal(
    value: object,
    description: str,
    *,
    zero_allowed: bool,
) -> Decimal:
    parsed = _canonical_decimal(value, description)
    if parsed > 1 or (not zero_allowed and parsed == 0):
        raise ValueError(f"{description} must be in {'[0' if zero_allowed else '(0'}, 1]")
    return parsed


def _decimal_string(value: Decimal) -> str:
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _parse_endpoints(value: object) -> tuple[PolicyEndpoint, ...]:
    if not isinstance(value, list) or len(value) != len(ENDPOINT_OPERATORS):
        raise ValueError(f"endpoints must contain exactly {len(ENDPOINT_OPERATORS)} entries")
    parsed: list[PolicyEndpoint] = []
    expected_names = tuple(ENDPOINT_OPERATORS)
    for index, (item, expected_name) in enumerate(zip(value, expected_names, strict=True)):
        raw = _exact_mapping(item, {"name", "op", "value"}, f"endpoints[{index}]")
        if raw["name"] != expected_name:
            raise ValueError("endpoints must use the frozen canonical order")
        expected_operator = ENDPOINT_OPERATORS[expected_name]
        if raw["op"] != expected_operator:
            raise ValueError(f"endpoint {expected_name!r} must use {expected_operator!r}")
        threshold = _unit_decimal(
            raw["value"],
            f"endpoints[{index}].value",
            zero_allowed=True,
        )
        if (expected_operator == ">=" and threshold <= 0) or (
            expected_operator == "<=" and threshold >= 1
        ):
            raise ValueError(f"endpoint {expected_name!r} must impose a non-vacuous target")
        parsed.append(PolicyEndpoint(expected_name, expected_operator, threshold))
    return tuple(parsed)


def _parse_aggregation(value: object) -> AggregationPolicy:
    raw = _exact_mapping(
        value,
        {"dimensions", "all_cells_pass", "average_biological_replicates"},
        "aggregation",
    )
    if raw["dimensions"] != list(AGGREGATION_DIMENSIONS):
        raise ValueError("aggregation dimensions do not match the frozen contract")
    if raw["all_cells_pass"] is not True or raw["average_biological_replicates"] is not False:
        raise ValueError("aggregation must require all cells without biological averaging")
    return AggregationPolicy(AGGREGATION_DIMENSIONS, True, False)


def _parse_rule(value: object) -> StableRegionRule:
    raw = _exact_mapping(
        value,
        {
            "algorithm",
            "minimum_fraction_levels",
            "require_zero_false_accepts",
            "require_full_density_no_drop_acceptance",
        },
        "stable_region_rule",
    )
    if raw["algorithm"] != SELECTION_ALGORITHM:
        raise ValueError(f"stable_region_rule.algorithm must be {SELECTION_ALGORITHM!r}")
    levels = raw["minimum_fraction_levels"]
    if not isinstance(levels, int) or isinstance(levels, bool) or levels < 2:
        raise ValueError("stable_region_rule.minimum_fraction_levels must be an integer >= 2")
    if raw["require_zero_false_accepts"] is not True:
        raise ValueError("stable_region_rule must require zero false accepts")
    if raw["require_full_density_no_drop_acceptance"] is not True:
        raise ValueError("stable_region_rule must require full-density/no-drop acceptance")
    return StableRegionRule(SELECTION_ALGORITHM, levels, True, True)


def _parse_confirmation_matrix(value: object) -> ConfirmationMatrix:
    raw = _exact_mapping(
        value,
        {"input_masks", "fractions", "seeds", "drop_scenarios"},
        "confirmation_matrix",
    )
    if raw["input_masks"] != list(SUPPORTED_INPUT_MASKS):
        raise ValueError("confirmation_matrix must contain the three production masks in order")
    raw_fractions = raw["fractions"]
    if not isinstance(raw_fractions, list) or len(raw_fractions) < 2:
        raise ValueError("confirmation_matrix.fractions must contain at least two levels")
    fractions = tuple(
        _unit_decimal(item, f"confirmation_matrix.fractions[{index}]", zero_allowed=False)
        for index, item in enumerate(raw_fractions)
    )
    if tuple(sorted(set(fractions))) != fractions:
        raise ValueError("confirmation_matrix.fractions must be unique and increasing")
    raw_seeds = raw["seeds"]
    if not isinstance(raw_seeds, list) or not raw_seeds:
        raise ValueError("confirmation_matrix.seeds must be nonempty")
    seeds: list[int] = []
    for index, seed in enumerate(raw_seeds):
        if (
            not isinstance(seed, int)
            or isinstance(seed, bool)
            or not -(2**63) <= seed <= 2**63 - 1
        ):
            raise ValueError(f"confirmation_matrix.seeds[{index}] is not signed 64-bit")
        seeds.append(seed)
    if len(set(seeds)) != len(seeds):
        raise ValueError("confirmation_matrix.seeds must be unique")
    raw_scenarios = raw["drop_scenarios"]
    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        raise ValueError("confirmation_matrix.drop_scenarios must be nonempty")
    scenarios: list[ChromosomeDropScenario] = []
    seen_names: set[str] = set()
    seen_drops: set[tuple[str, ...]] = set()
    for index, item in enumerate(raw_scenarios):
        entry = _exact_mapping(
            item,
            {"name", "dropped_autosomes"},
            f"confirmation_matrix.drop_scenarios[{index}]",
        )
        name = entry["name"]
        if not isinstance(name, str) or _SCENARIO_RE.fullmatch(name) is None:
            raise ValueError(f"confirmation_matrix.drop_scenarios[{index}].name is invalid")
        dropped_raw = entry["dropped_autosomes"]
        if not isinstance(dropped_raw, list) or any(
            not isinstance(chromosome, str) or chromosome not in _AUTOSOME_SET
            for chromosome in dropped_raw
        ):
            raise ValueError("drop scenarios may contain only canonical autosomes 1..22")
        dropped = tuple(dropped_raw)
        if tuple(sorted(set(dropped), key=int)) != dropped:
            raise ValueError("drop-scenario autosomes must be unique and numerically sorted")
        if (name == "none") != (not dropped):
            raise ValueError("only the 'none' drop scenario may be empty")
        if name in seen_names or dropped in seen_drops:
            raise ValueError("confirmation_matrix.drop_scenarios contains a duplicate")
        seen_names.add(name)
        seen_drops.add(dropped)
        scenarios.append(ChromosomeDropScenario(name, dropped))
    if "none" not in seen_names:
        raise ValueError("confirmation_matrix.drop_scenarios must include 'none'")
    return ConfirmationMatrix(
        SUPPORTED_INPUT_MASKS,
        fractions,
        tuple(seeds),
        tuple(scenarios),
    )


def read_selection_design(
    path: Path,
    *,
    expected_sha256: str,
) -> SelectionDesign:
    raw, stable = _read_canonical_json_file(
        Path(path),
        "selection design",
        maximum_bytes=MAX_DESIGN_BYTES,
    )
    expected_digest = _sha256(expected_sha256, "expected selection-design SHA-256")
    if stable.sha256 != expected_digest:
        raise ValueError("selection-design SHA-256 does not match its independent identity")
    raw = _exact_mapping(
        raw,
        {
            "schema_version",
            "policy_id",
            "frozen",
            "dataset_id",
            "bundle_artifact_sha256",
            "simulation_manifest_sha256",
            "code_revision",
            "endpoints",
            "aggregation",
            "stable_region_rule",
            "confirmation_matrix",
            "final_confirmation_split_commitment_sha256",
        },
        "selection design",
    )
    if (
        not isinstance(raw["schema_version"], int)
        or isinstance(raw["schema_version"], bool)
        or raw["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError("selection design must use schema version 1")
    if raw["frozen"] is not True:
        raise ValueError("selection design must set frozen to true")
    rule = _parse_rule(raw["stable_region_rule"])
    matrix = _parse_confirmation_matrix(raw["confirmation_matrix"])
    if len(matrix.fractions) < rule.minimum_fraction_levels:
        raise ValueError("confirmation matrix is shorter than the frozen stable-region minimum")
    if matrix.fractions[-1] != Decimal("1"):
        raise ValueError("confirmation matrix must include full density as its highest fraction")
    if not any(scenario.name == "none" for scenario in matrix.drop_scenarios):
        raise ValueError("confirmation matrix must include the no-drop scenario")
    return SelectionDesign(
        path=Path(path),
        sha256=stable.sha256,
        policy_id=_identifier(raw["policy_id"], "policy_id"),
        dataset_id=_identifier(raw["dataset_id"], "dataset_id"),
        bundle_artifact_sha256=_sha256(raw["bundle_artifact_sha256"], "bundle_artifact_sha256"),
        simulation_manifest_sha256=_sha256(
            raw["simulation_manifest_sha256"], "simulation_manifest_sha256"
        ),
        code_revision=_git_revision(raw["code_revision"], "code_revision"),
        endpoints=_parse_endpoints(raw["endpoints"]),
        aggregation=_parse_aggregation(raw["aggregation"]),
        rule=rule,
        confirmation_matrix=matrix,
        confirmation_commitment=_sha256(
            raw["final_confirmation_split_commitment_sha256"],
            "final_confirmation_split_commitment_sha256",
        ),
    )


def _required_mapping(value: object, description: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{description} must be a JSON object")
    return value


def _positive_integer(value: object, description: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{description} must be a positive integer")
    return value


def _validate_evaluation_coverage(configuration: Mapping[str, object]) -> None:
    inputs = _required_mapping(configuration.get("inputs"), "plan inputs")
    simulation = _required_mapping(
        inputs.get("simulation_manifest"), "simulation-manifest identity"
    )
    protocol = _required_mapping(simulation.get("simulation_protocol"), "simulation protocol")
    minimums = _required_mapping(protocol.get("minimums"), "simulation minimums")
    required_founders = _positive_integer(
        minimums.get("founders_per_class_per_split"),
        "founders_per_class_per_split",
    )
    required_simulations = _positive_integer(
        minimums.get("simulations_per_class_per_split"),
        "simulations_per_class_per_split",
    )
    required_truth = _positive_integer(
        minimums.get("truth_haplotype_windows_per_class_per_split"),
        "truth_haplotype_windows_per_class_per_split",
    )
    coverage = _required_mapping(inputs.get("evaluation_coverage"), "evaluation coverage")
    required_tables = {
        "founders_by_class": required_founders,
        "modal_truth_founders_by_class": required_founders,
        "simulations_by_class": required_simulations,
        "truth_haplotype_windows_by_class": required_truth,
    }
    for field, minimum in required_tables.items():
        table = _required_mapping(coverage.get(field), f"evaluation_coverage.{field}")
        if set(table) != set(SUPERPOPULATIONS):
            raise ValueError(f"evaluation_coverage.{field} must cover all seven classes")
        for population in SUPERPOPULATIONS:
            observed = table[population]
            if not isinstance(observed, int) or isinstance(observed, bool) or observed < minimum:
                raise ValueError(
                    f"evaluation_coverage.{field}.{population} is below the frozen minimum"
                )
    strata = _required_mapping(
        coverage.get("by_validation_stratum"),
        "evaluation_coverage.by_validation_stratum",
    )
    if not strata:
        raise ValueError("evaluation coverage has no validation strata")
    for stratum, raw_classes in strata.items():
        if not isinstance(stratum, str) or not stratum:
            raise ValueError("evaluation coverage has an invalid validation stratum")
        classes = _required_mapping(raw_classes, f"validation stratum {stratum!r}")
        if set(classes) != set(SUPERPOPULATIONS):
            raise ValueError(f"validation stratum {stratum!r} lacks a required class")
        for population, raw_counts in classes.items():
            counts = _required_mapping(
                raw_counts,
                f"validation stratum {stratum!r} class {population!r}",
            )
            if (
                _positive_integer(
                    counts.get("simulations"),
                    f"validation stratum {stratum!r} {population} simulations",
                )
                <= 0
                or _positive_integer(
                    counts.get("truth_haplotype_windows"),
                    f"validation stratum {stratum!r} {population} truth windows",
                )
                <= 0
            ):
                raise AssertionError("positive-integer validation returned a nonpositive value")


def validate_calibration_plan(
    plan: lai_coverage_plan.JobPlanSummary,
    design: SelectionDesign,
    *,
    expected_job_plan_sha256: str,
) -> None:
    expected_plan_digest = _sha256(expected_job_plan_sha256, "expected job-plan SHA-256")
    if plan.plan_sha256 != expected_plan_digest:
        raise ValueError("calibration job-plan SHA-256 does not match its independent identity")
    configuration = plan.configuration
    if configuration.get("dataset_split") != "calibration":
        raise ValueError("selection requires a calibration job plan")
    if configuration.get("dataset_id") != design.dataset_id:
        raise ValueError("selection design dataset_id differs from the calibration plan")
    if configuration.get("coverage_enforcement") != "disabled_for_diagnostics":
        raise ValueError("calibration plan did not disable production coverage enforcement")
    if configuration.get("threshold_selected") is not None:
        raise ValueError("calibration plan must not have a selected threshold")
    inputs = _required_mapping(configuration.get("inputs"), "plan inputs")
    if inputs.get("bundle_artifact_sha256") != design.bundle_artifact_sha256:
        raise ValueError("selection design bundle identity differs from the calibration plan")
    simulation = _required_mapping(
        inputs.get("simulation_manifest"), "simulation-manifest identity"
    )
    if simulation.get("sha256") != design.simulation_manifest_sha256:
        raise ValueError("selection design simulation identity differs from the plan")
    if inputs.get("code_revision") != design.code_revision:
        raise ValueError("selection design code revision differs from the calibration plan")
    planned_design = _exact_mapping(
        inputs.get("selection_design"),
        {"filename", "sha256"},
        "plan selection-design identity",
    )
    if planned_design != {
        "filename": design.path.name,
        "sha256": design.sha256,
    }:
        raise ValueError("live selection design differs from the preregistered plan identity")
    verification = _required_mapping(
        plan.input_verification.get("selection_design"),
        "selection-design input verification",
    )
    if verification.get("sha256") != design.sha256:
        raise ValueError("selection-design fingerprint differs from the preregistered digest")
    _validate_evaluation_coverage(configuration)

    matrix = plan.matrix
    if matrix.dataset_split != "calibration":
        raise ValueError("calibration plan matrix has the wrong split")
    expected_masks = {NATIVE_MASK, *SUPPORTED_INPUT_MASKS}
    for iid, masks in matrix.fixture_masks:
        if set(masks) != expected_masks or len(masks) != len(expected_masks):
            raise ValueError(
                f"calibration fixture {iid!r} must contain native plus all production masks"
            )
    stable_fractions = tuple(
        _decimal_string(value) for value in design.confirmation_matrix.fractions
    )
    if (
        len(stable_fractions) > len(matrix.fractions)
        or matrix.fractions[-len(stable_fractions) :] != stable_fractions
    ):
        raise ValueError(
            "predeclared confirmation fractions must be a contiguous high-density "
            "suffix of the calibration fractions"
        )
    if not set(design.confirmation_matrix.seeds) <= set(matrix.seeds):
        raise ValueError("predeclared confirmation seeds are absent from the calibration plan")
    design_scenarios = {
        scenario.name: scenario for scenario in design.confirmation_matrix.drop_scenarios
    }
    if not set(design_scenarios) <= set(matrix.drop_scenarios):
        raise ValueError("predeclared drop scenarios are absent from the calibration plan")
    planned_scenarios = configuration.get("chromosome_drop_scenarios")
    if not isinstance(planned_scenarios, list):
        raise ValueError("calibration plan lacks chromosome-drop definitions")
    planned_by_name: dict[str, object] = {}
    for item in planned_scenarios:
        entry = _exact_mapping(item, {"name", "dropped_autosomes"}, "drop scenario")
        name = entry["name"]
        if not isinstance(name, str) or name in planned_by_name:
            raise ValueError("calibration plan has invalid or duplicate drop scenarios")
        planned_by_name[name] = entry["dropped_autosomes"]
    for name, scenario in design_scenarios.items():
        if planned_by_name.get(name) != list(scenario.dropped_autosomes):
            raise ValueError(f"drop scenario {name!r} differs from the preregistered design")


def validate_final_plan(
    plan: lai_coverage_plan.JobPlanSummary,
    policy: ConfirmationPolicy,
    *,
    expected_job_plan_sha256: str,
) -> None:
    if plan.plan_sha256 != _sha256(expected_job_plan_sha256, "expected final job-plan SHA-256"):
        raise ValueError("final job-plan SHA-256 does not match its independent identity")
    configuration = plan.configuration
    if configuration.get("dataset_split") != "final_confirmation":
        raise ValueError("confirmation requires a final_confirmation job plan")
    if configuration.get("dataset_id") != policy.dataset_id:
        raise ValueError("final plan dataset identity differs from the policy")
    inputs = _required_mapping(configuration.get("inputs"), "final plan inputs")
    if inputs.get("bundle_artifact_sha256") != policy.bundle_artifact_sha256:
        raise ValueError("final plan bundle identity differs from the policy")
    simulation = _required_mapping(
        inputs.get("simulation_manifest"), "final simulation-manifest identity"
    )
    if simulation.get("sha256") != policy.simulation_manifest_sha256:
        raise ValueError("final plan simulation identity differs from the policy")
    if inputs.get("code_revision") != policy.code_revision:
        raise ValueError("final plan code revision differs from the policy")
    if inputs.get("confirmation_policy") != confirmation_policy_provenance(policy):
        raise ValueError("final plan confirmation-policy provenance differs from the policy")
    if configuration.get("threshold_selected") != policy.policy_id:
        raise ValueError("final plan selected-threshold identity differs from the policy")
    _validate_evaluation_coverage(configuration)

    matrix = plan.matrix
    for iid, masks in matrix.fixture_masks:
        if masks != policy.confirmation_matrix.input_masks:
            raise ValueError(f"final fixture {iid!r} matrix masks differ from the policy")
    if matrix.fractions != tuple(
        _decimal_string(value) for value in policy.confirmation_matrix.fractions
    ):
        raise ValueError("final matrix fractions differ from the policy")
    if matrix.seeds != policy.confirmation_matrix.seeds:
        raise ValueError("final matrix seeds differ from the policy")
    expected_drop_names = tuple(
        scenario.name for scenario in policy.confirmation_matrix.drop_scenarios
    )
    if matrix.drop_scenarios != expected_drop_names:
        raise ValueError("final matrix drop scenarios differ from the policy")
    planned_scenarios = configuration.get("chromosome_drop_scenarios")
    expected_scenarios = [
        {"name": scenario.name, "dropped_autosomes": list(scenario.dropped_autosomes)}
        for scenario in policy.confirmation_matrix.drop_scenarios
    ]
    if planned_scenarios != expected_scenarios:
        raise ValueError("final drop-scenario definitions differ from the policy")


class ObservationDirectory:
    """Descriptor-pinned, lock-aware reader for one-record job outputs."""

    def __init__(self, path: Path, expected_count: int) -> None:
        self.path = Path(path)
        self.expected_count = expected_count
        try:
            before = self.path.lstat()
        except OSError as exc:
            raise ValueError(f"cannot stat observation directory {self.path}: {exc}") from exc
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
            raise ValueError("observations must be a non-symlink directory")
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        self._descriptor = os.open(self.path, flags)
        opened = os.fstat(self._descriptor)
        if _stat_signature(opened) != _stat_signature(before):
            os.close(self._descriptor)
            raise ValueError("observation directory changed while it was opened")
        self._signature = _stat_signature(opened)

    def __enter__(self) -> ObservationDirectory:
        return self

    def __exit__(self, *_args: object) -> None:
        os.close(self._descriptor)

    def _assert_path(self) -> None:
        if _stat_signature(os.fstat(self._descriptor)) != self._signature:
            raise ValueError("open observation directory metadata changed")
        try:
            observed = self.path.lstat()
        except OSError as exc:
            raise ValueError("observation directory pathname disappeared") from exc
        if _stat_signature(observed) != self._signature:
            raise ValueError("observation directory pathname changed during evaluation")

    def validate_entries(self) -> None:
        self._assert_path()
        outputs = 0
        locks = 0
        with os.scandir(self._descriptor) as entries:
            for entry in entries:
                name = entry.name
                is_lock = name.startswith(".") and name.endswith(".jsonl.lock")
                index_text = (
                    name[1:-11] if is_lock else name[:-6] if name.endswith(".jsonl") else ""
                )
                if (
                    not index_text
                    or not index_text.isascii()
                    or not index_text.isdigit()
                    or (len(index_text) > 1 and index_text.startswith("0"))
                ):
                    raise ValueError(f"unexpected observation-directory entry {name!r}")
                index = int(index_text)
                if index >= self.expected_count:
                    raise ValueError(f"observation entry {name!r} is outside the job plan")
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    raise ValueError(f"observation entry {name!r} is not a regular file")
                metadata = entry.stat(follow_symlinks=False)
                if metadata.st_nlink != 1 or metadata.st_uid != os.getuid():
                    raise ValueError(f"observation entry {name!r} has unsafe ownership or links")
                if is_lock:
                    locks += 1
                else:
                    outputs += 1
        if outputs != self.expected_count or locks != self.expected_count:
            raise ValueError(
                "observation directory must contain exactly one result and lock per planned job"
            )
        self._assert_path()

    def read_record(self, index: int) -> tuple[Mapping[str, object], bytes]:
        result_name = f"{index}.jsonl"
        lock_name = f".{result_name}.lock"
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        lock_descriptor = os.open(lock_name, flags, dir_fd=self._descriptor)
        try:
            lock_metadata = os.fstat(lock_descriptor)
            if (
                not stat.S_ISREG(lock_metadata.st_mode)
                or lock_metadata.st_nlink != 1
                or lock_metadata.st_uid != os.getuid()
            ):
                raise ValueError(f"unsafe observation lock {lock_name!r}")
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError(f"observation job {index} is still being written") from None
            descriptor = os.open(result_name, flags, dir_fd=self._descriptor)
            try:
                before = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_nlink != 1
                    or before.st_uid != os.getuid()
                    or before.st_size > MAX_OBSERVATION_BYTES
                ):
                    raise ValueError(f"unsafe or oversized observation {result_name!r}")
                chunks: list[bytes] = []
                total = 0
                while chunk := os.read(descriptor, 1024 * 1024):
                    total += len(chunk)
                    if total > MAX_OBSERVATION_BYTES:
                        raise ValueError(f"observation {result_name!r} is oversized")
                    chunks.append(chunk)
                after = os.fstat(descriptor)
                if _stat_signature(before) != _stat_signature(after):
                    raise ValueError(f"observation {result_name!r} changed while read")
            finally:
                os.close(descriptor)
        finally:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
        payload = b"".join(chunks)
        if not payload.endswith(b"\n") or b"\n" in payload[:-1]:
            raise ValueError(f"observation {result_name!r} must contain exactly one JSON line")
        encoded = payload[:-1]
        try:
            value = json.loads(
                encoded,
                object_pairs_hook=_reject_duplicate_object_keys,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"observation {result_name!r} has invalid JSON: {exc}") from exc
        if not isinstance(value, Mapping):
            raise ValueError(f"observation {result_name!r} must be a JSON object")
        if lai_coverage_plan.canonical_json_bytes(value) != encoded:
            raise ValueError(f"observation {result_name!r} is not canonical JSONL")
        return value, encoded


def _finite_decimal_number(
    value: object,
    description: str,
    *,
    minimum: Decimal = Decimal("0"),
    maximum: Decimal | None = None,
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{description} must be a finite JSON number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{description} must be finite")
    parsed = Decimal(str(value))
    if not parsed.is_finite() or parsed < minimum or (maximum is not None and parsed > maximum):
        raise ValueError(f"{description} is outside its permitted range")
    return parsed


def _nonnegative_integer(value: object, description: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{description} must be a nonnegative integer")
    return value


def _autosome_counts(value: object, description: str) -> dict[str, int]:
    counts = _required_mapping(value, description)
    if set(counts) != _AUTOSOME_SET:
        raise ValueError(f"{description} must contain autosomes 1..22 exactly")
    return {
        chrom: _nonnegative_integer(counts[chrom], f"{description}.{chrom}")
        for chrom in _AUTOSOMES
    }


def _ratio_matches(
    observed: Decimal,
    numerator: int,
    denominator: int,
    description: str,
) -> None:
    expected = Decimal(numerator) / Decimal(denominator)
    if abs(observed - expected) > Decimal("1e-12"):
        raise ValueError(f"{description} does not reconcile with its counts")


def _dotted_value(value: object, field: str) -> object:
    current = value
    for part in field.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise ValueError(f"coverage telemetry is missing {field!r}")
        current = current[part]
    return current


def extract_telemetry(metrics: object, *, allow_incomplete: bool) -> dict[str, Decimal] | None:
    if not isinstance(metrics, Mapping):
        if allow_incomplete:
            return None
        raise ValueError("coverage telemetry must be a JSON object")
    schema_version = metrics.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        if allow_incomplete:
            return None
        raise ValueError("coverage telemetry must use schema version 1")
    values: dict[str, Decimal] = {}
    for field in COVERAGE_PREDICATE_OPERATORS:
        try:
            raw = _dotted_value(metrics, field)
            if field in _COUNT_TELEMETRY:
                count = _nonnegative_integer(raw, f"coverage telemetry {field}")
                if field in _AUTOSOME_TELEMETRY and count > 22:
                    raise ValueError(f"coverage telemetry {field} cannot exceed 22")
                values[field] = Decimal(count)
            else:
                values[field] = _finite_decimal_number(
                    raw,
                    f"coverage telemetry {field}",
                    maximum=Decimal("1"),
                )
        except ValueError:
            if allow_incomplete:
                return None
            raise
    return values


def _evaluate_accuracy(
    record: Mapping[str, object],
    endpoints: Sequence[PolicyEndpoint],
) -> tuple[dict[str, Decimal], dict[str, str]]:
    endpoint_by_name = {endpoint.name: endpoint for endpoint in endpoints}
    if set(endpoint_by_name) != set(ENDPOINT_OPERATORS):
        raise ValueError("accuracy endpoint set is incomplete")
    accuracy = _required_mapping(record.get("accuracy"), "observation accuracy")
    expected_windows = _positive_integer(
        accuracy.get("truth_windows_expected"), "truth_windows_expected"
    )
    assigned_windows = _nonnegative_integer(accuracy.get("windows_assigned"), "windows_assigned")
    if assigned_windows > expected_windows:
        raise ValueError("windows_assigned exceeds truth_windows_expected")
    assigned_by_autosome = _autosome_counts(
        accuracy.get("windows_assigned_by_autosome"),
        "accuracy.windows_assigned_by_autosome",
    )
    if sum(assigned_by_autosome.values()) != assigned_windows:
        raise ValueError("windows_assigned_by_autosome does not reconcile with windows_assigned")
    expected_haplotypes = _positive_integer(
        accuracy.get("haplotype_calls_expected"), "haplotype_calls_expected"
    )
    if expected_haplotypes != 2 * expected_windows:
        raise ValueError("haplotype_calls_expected does not reconcile with truth windows")
    correct_diplotypes = _nonnegative_integer(
        accuracy.get("diplotype_windows_correct"), "diplotype_windows_correct"
    )
    correct_haplotypes = _nonnegative_integer(
        accuracy.get("haplotype_calls_correct_best_orientation"),
        "haplotype_calls_correct_best_orientation",
    )
    if correct_diplotypes > expected_windows or correct_haplotypes > expected_haplotypes:
        raise ValueError("accuracy correct-count exceeds its denominator")

    coverage = _required_mapping(record.get("coverage_metadata"), "coverage metadata")
    metrics = _required_mapping(
        coverage.get("final_lai_coverage_metrics"),
        "final LAI coverage metrics",
    )
    window_metrics = _required_mapping(
        metrics.get("haplotype_windows"),
        "coverage telemetry haplotype_windows",
    )
    telemetry_expected = _nonnegative_integer(
        window_metrics.get("expected"),
        "coverage telemetry haplotype_windows.expected",
    )
    telemetry_valid = _nonnegative_integer(
        window_metrics.get("valid_assigned"),
        "coverage telemetry haplotype_windows.valid_assigned",
    )
    telemetry_valid_by_autosome = _autosome_counts(
        window_metrics.get("valid_assigned_by_autosome"),
        "coverage telemetry haplotype_windows.valid_assigned_by_autosome",
    )
    if telemetry_expected != expected_haplotypes:
        raise ValueError("coverage telemetry expected windows differ from truth accuracy")
    if telemetry_valid != 2 * assigned_windows:
        raise ValueError("coverage telemetry assigned windows differ from truth accuracy")
    expected_valid_by_autosome = {chrom: 2 * assigned_by_autosome[chrom] for chrom in _AUTOSOMES}
    if telemetry_valid_by_autosome != expected_valid_by_autosome:
        raise ValueError(
            "per-autosome coverage telemetry assigned windows differ from truth accuracy"
        )

    overall = {
        "assignment_completeness": _finite_decimal_number(
            record.get("assignment_completeness"),
            "assignment_completeness",
            maximum=Decimal("1"),
        ),
        "local_diplotype_accuracy": _finite_decimal_number(
            record.get("local_diplotype_accuracy"),
            "local_diplotype_accuracy",
            maximum=Decimal("1"),
        ),
        "local_haplotype_accuracy_best_orientation": _finite_decimal_number(
            record.get("local_haplotype_accuracy_best_orientation"),
            "local_haplotype_accuracy_best_orientation",
            maximum=Decimal("1"),
        ),
        "global_ancestry_total_variation": _finite_decimal_number(
            record.get("global_ancestry_total_variation"),
            "global_ancestry_total_variation",
            maximum=Decimal("1"),
        ),
    }
    _ratio_matches(
        overall["assignment_completeness"],
        assigned_windows,
        expected_windows,
        "assignment_completeness",
    )
    _ratio_matches(
        overall["local_diplotype_accuracy"],
        correct_diplotypes,
        expected_windows,
        "local_diplotype_accuracy",
    )
    _ratio_matches(
        overall["local_haplotype_accuracy_best_orientation"],
        correct_haplotypes,
        expected_haplotypes,
        "local_haplotype_accuracy_best_orientation",
    )
    for name, observed in overall.items():
        nested = accuracy.get(name)
        if (
            nested is not None
            and _finite_decimal_number(
                nested,
                f"accuracy.{name}",
                maximum=Decimal("1"),
            )
            != observed
        ):
            raise ValueError(f"top-level {name} differs from accuracy.{name}")

    raw_classes = _required_mapping(accuracy.get("per_truth_class"), "accuracy.per_truth_class")
    if set(raw_classes) != set(SUPERPOPULATIONS):
        raise ValueError("accuracy.per_truth_class must contain all seven ancestry classes")
    class_worst: Decimal | None = None
    class_worst_name = ""
    class_expected_total = 0
    class_assigned_total = 0
    class_correct_total = 0
    for population in SUPERPOPULATIONS:
        entry = _required_mapping(
            raw_classes[population],
            f"accuracy.per_truth_class.{population}",
        )
        expected = _nonnegative_integer(
            entry.get("truth_haplotype_calls_expected"),
            f"{population} truth_haplotype_calls_expected",
        )
        assigned = _nonnegative_integer(
            entry.get("assigned_haplotype_calls"),
            f"{population} assigned_haplotype_calls",
        )
        correct = _nonnegative_integer(
            entry.get("correct_haplotype_calls_best_orientation"),
            f"{population} correct_haplotype_calls_best_orientation",
        )
        if assigned > expected or correct > assigned:
            raise ValueError(f"per-class counts do not reconcile for {population}")
        if expected == 0:
            if (
                assigned != 0
                or correct != 0
                or entry.get("assignment_completeness") is not None
                or entry.get("local_haplotype_accuracy_best_orientation") is not None
            ):
                raise ValueError(
                    f"zero-denominator per-class values are inconsistent for {population}"
                )
            continue
        completeness = _finite_decimal_number(
            entry.get("assignment_completeness"),
            f"{population} assignment_completeness",
            maximum=Decimal("1"),
        )
        haplotype_accuracy = _finite_decimal_number(
            entry.get("local_haplotype_accuracy_best_orientation"),
            f"{population} local_haplotype_accuracy_best_orientation",
            maximum=Decimal("1"),
        )
        _ratio_matches(completeness, assigned, expected, f"{population} completeness")
        _ratio_matches(haplotype_accuracy, correct, expected, f"{population} accuracy")
        class_expected_total += expected
        class_assigned_total += assigned
        class_correct_total += correct
        if class_worst is None or completeness < class_worst:
            class_worst = completeness
            class_worst_name = population
    if (
        class_expected_total != expected_haplotypes
        or class_assigned_total != 2 * assigned_windows
        or class_correct_total != correct_haplotypes
    ):
        raise ValueError("per-class accuracy totals do not reconcile with overall counts")
    if class_worst is None:
        raise ValueError("observation has no represented truth ancestry class")

    raw_diplotypes = _required_mapping(
        accuracy.get("per_truth_diplotype"), "accuracy.per_truth_diplotype"
    )
    if not raw_diplotypes:
        raise ValueError("accuracy.per_truth_diplotype must be nonempty")
    diplotype_worst: Decimal | None = None
    diplotype_worst_name = ""
    diplotype_expected_total = 0
    diplotype_assigned_total = 0
    diplotype_correct_total = 0
    for diplotype in sorted(raw_diplotypes):
        if not isinstance(diplotype, str) or not diplotype:
            raise ValueError("accuracy.per_truth_diplotype has an invalid key")
        entry = _required_mapping(
            raw_diplotypes[diplotype],
            f"accuracy.per_truth_diplotype.{diplotype}",
        )
        expected = _positive_integer(
            entry.get("windows_expected"), f"{diplotype} windows_expected"
        )
        assigned = _nonnegative_integer(
            entry.get("windows_assigned"), f"{diplotype} windows_assigned"
        )
        correct = _nonnegative_integer(
            entry.get("windows_correct"), f"{diplotype} windows_correct"
        )
        if assigned > expected or correct > assigned:
            raise ValueError(f"per-diplotype counts do not reconcile for {diplotype}")
        completeness = _finite_decimal_number(
            entry.get("assignment_completeness"),
            f"{diplotype} assignment_completeness",
            maximum=Decimal("1"),
        )
        accuracy_value = _finite_decimal_number(
            entry.get("local_diplotype_accuracy"),
            f"{diplotype} local_diplotype_accuracy",
            maximum=Decimal("1"),
        )
        _ratio_matches(completeness, assigned, expected, f"{diplotype} completeness")
        _ratio_matches(accuracy_value, correct, expected, f"{diplotype} accuracy")
        diplotype_expected_total += expected
        diplotype_assigned_total += assigned
        diplotype_correct_total += correct
        if diplotype_worst is None or accuracy_value < diplotype_worst:
            diplotype_worst = accuracy_value
            diplotype_worst_name = diplotype
    if (
        diplotype_expected_total != expected_windows
        or diplotype_assigned_total != assigned_windows
        or diplotype_correct_total != correct_diplotypes
    ):
        raise ValueError("per-diplotype totals do not reconcile with overall counts")
    assert diplotype_worst is not None
    values = {
        **overall,
        "per_truth_class.assignment_completeness": class_worst,
        "per_truth_diplotype.local_diplotype_accuracy": diplotype_worst,
    }
    details = {
        "per_truth_class.assignment_completeness": class_worst_name,
        "per_truth_diplotype.local_diplotype_accuracy": diplotype_worst_name,
    }
    return values, details


def evaluate_observation(
    record: Mapping[str, object],
    endpoints: Sequence[PolicyEndpoint],
) -> CellEvaluation:
    status = record.get("status")
    eligible = record.get("calibration_eligible")
    if status != "ok" or eligible is not True:
        metrics: object = None
        if status == "coverage_failure":
            coverage = record.get("coverage_metadata")
            if isinstance(coverage, Mapping):
                metrics = coverage.get("last_progressive_snapshot")
        telemetry = extract_telemetry(metrics, allow_incomplete=True)
        return CellEvaluation(
            endpoint_pass=False,
            endpoint_values={},
            endpoint_details={},
            telemetry=telemetry,
            reasons=(f"status:{status}",),
        )
    values, details = _evaluate_accuracy(record, endpoints)
    reasons: list[str] = []
    for endpoint in endpoints:
        observed = values[endpoint.name]
        if (endpoint.op == ">=" and observed < endpoint.value) or (
            endpoint.op == "<=" and observed > endpoint.value
        ):
            suffix = f":{details[endpoint.name]}" if endpoint.name in details else ""
            reasons.append(f"endpoint:{endpoint.name}{suffix}")
    coverage = _required_mapping(record.get("coverage_metadata"), "coverage metadata")
    telemetry = extract_telemetry(
        coverage.get("final_lai_coverage_metrics"),
        allow_incomplete=False,
    )
    assert telemetry is not None
    return CellEvaluation(
        endpoint_pass=not reasons,
        endpoint_values=values,
        endpoint_details=details,
        telemetry=telemetry,
        reasons=tuple(reasons),
    )


_OBSERVATION_KEYS = {
    "schema_version",
    "job_index",
    "dataset_id",
    "dataset_split",
    "sample",
    "mask",
    "chromosome_drop_scenario",
    "downsampling",
    "input_coverage",
    "provenance",
    "status",
    "coverage_metadata",
    "local_diplotype_accuracy",
    "local_haplotype_accuracy_best_orientation",
    "assignment_completeness",
    "global_ancestry_total_variation",
    "accuracy",
    "calibration_eligible",
    "calibration_exclusion",
    "error",
}


def _expected_mask_provenance(
    configuration: Mapping[str, object],
    iid: str,
    mask_name: str,
) -> tuple[Mapping[str, object], dict[str, str]]:
    mask_scenarios = _required_mapping(configuration.get("mask_scenarios"), "plan mask scenarios")
    raw_iid_masks = mask_scenarios.get(iid)
    if not isinstance(raw_iid_masks, list):
        raise ValueError(f"plan mask scenarios omit fixture {iid!r}")
    matches = [
        entry
        for entry in raw_iid_masks
        if isinstance(entry, Mapping) and entry.get("name") == mask_name
    ]
    if len(matches) != 1:
        raise ValueError(f"plan mask scenarios do not uniquely define {iid!r}/{mask_name!r}")
    mask_config = matches[0]
    manifest_names = mask_config.get("manifest_names")
    if not isinstance(manifest_names, list) or any(
        not isinstance(name, str) for name in manifest_names
    ):
        raise ValueError(f"plan mask {mask_name!r} has invalid manifest provenance")
    inputs = _required_mapping(configuration.get("inputs"), "plan inputs")
    site_masks = _required_mapping(
        inputs.get("privacy_safe_site_masks"), "privacy-safe site masks"
    )
    expected_hashes: dict[str, str] = {}
    for name in manifest_names:
        site = _required_mapping(site_masks.get(name), f"site mask {name!r}")
        expected_hashes[name] = _sha256(site.get("sha256"), f"site mask {name!r} SHA-256")
    return mask_config, expected_hashes


def _validate_input_coverage(
    record: Mapping[str, object],
    mask: Mapping[str, object],
    dropped_autosomes: Sequence[object],
    fraction: Decimal,
) -> tuple[int, dict[str, int], dict[str, int]]:
    coverage = _exact_mapping(
        record.get("input_coverage"),
        {
            "markers_before_downsampling",
            "markers_after_downsampling_before_chromosome_drop",
            "markers_after_chromosome_drop",
            "markers_selected",
            "selected_by_autosome",
            "selected_by_source",
            "autosomes_present",
        },
        "observation input_coverage",
    )
    before = _nonnegative_integer(
        coverage.get("markers_before_downsampling"),
        "input_coverage.markers_before_downsampling",
    )
    after_downsampling = _nonnegative_integer(
        coverage.get("markers_after_downsampling_before_chromosome_drop"),
        "input_coverage.markers_after_downsampling_before_chromosome_drop",
    )
    after_drop = _nonnegative_integer(
        coverage.get("markers_after_chromosome_drop"),
        "input_coverage.markers_after_chromosome_drop",
    )
    selected = _nonnegative_integer(
        coverage.get("markers_selected"),
        "input_coverage.markers_selected",
    )
    realized = _nonnegative_integer(
        mask.get("realized_fixture_markers"),
        "observation mask.realized_fixture_markers",
    )
    if before != realized:
        raise ValueError("input coverage differs from the authenticated mask marker count")
    if after_downsampling > before or after_drop > after_downsampling or selected != after_drop:
        raise ValueError("input coverage stage counts do not reconcile")
    if fraction == Decimal("1") and after_downsampling != before:
        raise ValueError("full-density input coverage unexpectedly removed markers")

    selected_by_autosome = _autosome_counts(
        coverage.get("selected_by_autosome"),
        "input_coverage.selected_by_autosome",
    )
    if sum(selected_by_autosome.values()) != selected:
        raise ValueError("selected_by_autosome does not reconcile with markers_selected")
    dropped = {str(chrom) for chrom in dropped_autosomes}
    if any(selected_by_autosome.get(chrom, 0) for chrom in dropped):
        raise ValueError("input coverage contains markers on a dropped autosome")
    if not dropped and after_drop != after_downsampling:
        raise ValueError("no-drop input coverage changed after chromosome filtering")

    raw_sources = _required_mapping(
        coverage.get("selected_by_source"),
        "input_coverage.selected_by_source",
    )
    selected_by_source: dict[str, int] = {}
    for source, value in raw_sources.items():
        if not isinstance(source, str) or not source:
            raise ValueError("input_coverage.selected_by_source has an invalid source")
        selected_by_source[source] = _nonnegative_integer(
            value,
            f"input_coverage.selected_by_source.{source}",
        )
    if sum(selected_by_source.values()) != selected:
        raise ValueError("selected_by_source does not reconcile with markers_selected")
    if selected and not selected_by_source:
        raise ValueError("selected markers have no source accounting")

    present = coverage.get("autosomes_present")
    expected_present = [chrom for chrom in _AUTOSOMES if selected_by_autosome[chrom] > 0]
    if present != expected_present:
        raise ValueError("autosomes_present does not reconcile with selected_by_autosome")
    return selected, selected_by_autosome, selected_by_source


def _validate_record_against_plan(
    record: Mapping[str, object],
    expected_row: Mapping[str, object],
    configuration: Mapping[str, object],
    configuration_sha256: str,
    *,
    policy: ConfirmationPolicy | None,
) -> None:
    schema_version = record.get("schema_version")
    if (
        set(record) != _OBSERVATION_KEYS
        or not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        raise ValueError("observation has an invalid schema")
    observed_job_index = record.get("job_index")
    if (
        not isinstance(observed_job_index, int)
        or isinstance(observed_job_index, bool)
        or observed_job_index != expected_row["job_index"]
    ):
        raise ValueError("observation job_index differs from its authenticated plan row")
    if record.get("dataset_id") != configuration.get("dataset_id"):
        raise ValueError("observation dataset_id differs from the plan")
    if record.get("dataset_split") != expected_row["dataset_split"]:
        raise ValueError("observation dataset split differs from its plan row")

    iid = str(expected_row["iid"])
    sample = _exact_mapping(
        record.get("sample"),
        {
            "iid",
            "validation_stratum",
            "fixture_path",
            "fixture_parsing",
            "local_truth_path",
            "local_truth_windows",
            "marker_truth_path",
            "marker_truth_rows",
        },
        "observation sample",
    )
    if sample.get("iid") != iid:
        raise ValueError("observation IID differs from its plan row")
    inputs = _required_mapping(configuration.get("inputs"), "plan inputs")
    fixtures = _required_mapping(inputs.get("fixtures"), "plan fixtures")
    fixture = _required_mapping(fixtures.get(iid), f"plan fixture {iid!r}")
    truth_windows_total = _positive_integer(
        fixture.get("local_truth_windows"),
        f"plan fixture {iid!r} local_truth_windows",
    )
    truth_windows_by_autosome = _autosome_counts(
        fixture.get("local_truth_windows_by_autosome"),
        f"plan fixture {iid!r} local_truth_windows_by_autosome",
    )
    if sum(truth_windows_by_autosome.values()) != truth_windows_total:
        raise ValueError("plan fixture local-truth window counts do not reconcile")
    truth_haplotype_windows_by_autosome = {
        chrom: 2 * truth_windows_by_autosome[chrom] for chrom in _AUTOSOMES
    }
    sample_expectations = (
        ("validation_stratum", fixture.get("validation_stratum")),
        ("local_truth_windows", truth_windows_total),
        ("marker_truth_rows", fixture.get("marker_truth_rows")),
    )
    for field, expected in sample_expectations:
        if sample.get(field) != expected:
            raise ValueError(f"observation sample.{field} differs from the plan")
    if Path(str(sample.get("fixture_path"))).name != fixture.get("filename"):
        raise ValueError("observation fixture filename differs from the plan")
    if Path(str(sample.get("local_truth_path"))).name != fixture.get("local_truth_filename"):
        raise ValueError("observation local-truth filename differs from the plan")
    marker_truth_path = sample.get("marker_truth_path")
    if not isinstance(marker_truth_path, str) or Path(marker_truth_path).name != fixture.get(
        "marker_truth_filename"
    ):
        raise ValueError("observation marker-truth filename differs from the plan")

    mask_name = str(expected_row["mask"])
    mask = _exact_mapping(
        record.get("mask"),
        {
            "name",
            "kind",
            "file_format",
            "manifest_provenance",
            "realized_fixture_markers",
            "source_counts",
        },
        "observation mask",
    )
    if mask.get("name") != mask_name:
        raise ValueError("observation mask differs from its plan row")
    mask_config, expected_mask_hashes = _expected_mask_provenance(configuration, iid, mask_name)
    for field in ("kind", "file_format", "realized_fixture_markers"):
        if mask.get(field) != mask_config.get(field):
            raise ValueError(f"observation mask.{field} differs from the plan")
    manifest_provenance = _required_mapping(
        mask.get("manifest_provenance"), "observation mask manifest provenance"
    )
    if set(manifest_provenance) != set(expected_mask_hashes):
        raise ValueError("observation mask manifest set differs from the plan")
    for name, expected_hash in expected_mask_hashes.items():
        entry = _required_mapping(manifest_provenance[name], f"observation manifest {name!r}")
        if entry.get("sha256") != expected_hash:
            raise ValueError(f"observation manifest {name!r} hash differs from the plan")

    drop = _exact_mapping(
        record.get("chromosome_drop_scenario"),
        {"name", "dropped_autosomes"},
        "observation chromosome-drop scenario",
    )
    if drop.get("name") != expected_row["chromosome_drop_scenario"]:
        raise ValueError("observation chromosome-drop scenario differs from its plan row")
    planned_scenarios = configuration.get("chromosome_drop_scenarios")
    if not isinstance(planned_scenarios, list):
        raise ValueError("plan has no chromosome-drop definitions")
    expected_drops = [
        item.get("dropped_autosomes")
        for item in planned_scenarios
        if isinstance(item, Mapping) and item.get("name") == drop.get("name")
    ]
    if len(expected_drops) != 1 or drop.get("dropped_autosomes") != expected_drops[0]:
        raise ValueError("observation dropped autosomes differ from the plan")

    downsampling = _exact_mapping(
        record.get("downsampling"),
        {"fraction", "fraction_canonical", "seed"},
        "observation downsampling",
    )
    observed_seed = downsampling.get("seed")
    if (
        downsampling.get("fraction_canonical") != expected_row["fraction"]
        or not isinstance(observed_seed, int)
        or isinstance(observed_seed, bool)
        or observed_seed != expected_row["seed"]
    ):
        raise ValueError("observation downsampling axes differ from the plan row")
    observed_fraction = _finite_decimal_number(
        downsampling.get("fraction"),
        "observation downsampling fraction",
        minimum=Decimal("0"),
        maximum=Decimal("1"),
    )
    if observed_fraction != Decimal(str(expected_row["fraction"])):
        raise ValueError("observation numeric fraction differs from its canonical axis")

    selected_markers, selected_by_autosome, selected_by_source = _validate_input_coverage(
        record,
        mask,
        drop["dropped_autosomes"],
        observed_fraction,
    )

    provenance = _required_mapping(record.get("provenance"), "observation provenance")
    expected_provenance_keys = {
        "bundle_metadata_sha256",
        "bundle_artifact_sha256",
        "code_revision",
        "harness_script_sha256",
        "runtime_environment",
        "fixture_sha256",
        "local_truth_sha256",
        "marker_truth_sha256",
        "labels_sha256",
        "mask_sha256",
        "configuration_sha256",
    }
    if policy is not None:
        expected_provenance_keys.add("confirmation_policy")
    if set(provenance) != expected_provenance_keys:
        raise ValueError("observation provenance has an invalid schema")
    bundle_metadata = _required_mapping(inputs.get("bundle_metadata"), "bundle metadata")
    labels = _required_mapping(inputs.get("labels"), "labels provenance")
    expected_values: dict[str, object] = {
        "bundle_metadata_sha256": bundle_metadata.get("sha256"),
        "bundle_artifact_sha256": inputs.get("bundle_artifact_sha256"),
        "code_revision": inputs.get("code_revision"),
        "harness_script_sha256": inputs.get("harness_script_sha256"),
        "runtime_environment": inputs.get("runtime_environment"),
        "fixture_sha256": fixture.get("sha256"),
        "local_truth_sha256": fixture.get("local_truth_sha256"),
        "marker_truth_sha256": fixture.get("marker_truth_sha256"),
        "labels_sha256": labels.get("sha256"),
        "mask_sha256": expected_mask_hashes,
        "configuration_sha256": configuration_sha256,
    }
    if policy is not None:
        expected_values["confirmation_policy"] = confirmation_policy_provenance(policy)
    for field, expected in expected_values.items():
        if provenance.get(field) != expected:
            raise ValueError(f"observation provenance.{field} differs from the plan")

    status = record.get("status")
    if status not in {"ok", "coverage_failure", "operational_error", "invalid"}:
        raise ValueError("observation has an unsupported status")
    if status == "ok":
        if (
            record.get("calibration_eligible") is not True
            or record.get("calibration_exclusion") is not None
            or record.get("error") is not None
        ):
            raise ValueError("ok observation has inconsistent eligibility fields")
        coverage = _required_mapping(record.get("coverage_metadata"), "coverage metadata")
        metrics = coverage.get("final_lai_coverage_metrics")
        accuracy = _required_mapping(record.get("accuracy"), "observation accuracy")
        if accuracy.get("truth_windows_expected") != truth_windows_total:
            raise ValueError(
                "accuracy truth-window denominator differs from the authenticated plan"
            )
        expected_model_markers = _autosome_counts(
            fixture.get("model_marker_counts_by_autosome"),
            f"plan fixture {iid!r} model_marker_counts_by_autosome",
        )
        if any(count <= 0 for count in expected_model_markers.values()):
            raise ValueError("plan model-marker denominators must be positive on every autosome")
        telemetry_exclusion = lai_coverage_metrics.coverage_metrics_calibration_exclusion(
            metrics,
            expected_truth_haplotype_windows_by_autosome=(truth_haplotype_windows_by_autosome),
            expected_input_markers=selected_markers,
            expected_input_markers_by_autosome=selected_by_autosome,
            expected_input_markers_by_source=selected_by_source,
            expected_model_markers_by_autosome=expected_model_markers,
        )
        if telemetry_exclusion is not None:
            kind = telemetry_exclusion.get("type", "invalid_lai_coverage_metrics")
            message = telemetry_exclusion.get("message", "coverage telemetry is inconsistent")
            raise ValueError(
                f"ok observation has inconsistent coverage telemetry ({kind}): {message}"
            )
    else:
        if record.get("calibration_eligible") is not False:
            raise ValueError("non-ok observation cannot be calibration eligible")
        exclusion = _required_mapping(record.get("calibration_exclusion"), "calibration exclusion")
        if exclusion.get("type") != status and status != "invalid":
            raise ValueError("observation status and exclusion type disagree")
        if status in {"coverage_failure", "operational_error"} and any(
            record.get(field) is not None
            for field in (
                "accuracy",
                "assignment_completeness",
                "local_diplotype_accuracy",
                "local_haplotype_accuracy_best_orientation",
                "global_ancestry_total_variation",
            )
        ):
            raise ValueError("failed observation contains truth accuracy values")


def _row_context(
    row: Mapping[str, object],
    configuration: Mapping[str, object],
) -> dict[str, object]:
    iid = str(row["iid"])
    fixtures = _required_mapping(
        _required_mapping(configuration.get("inputs"), "plan inputs").get("fixtures"),
        "plan fixtures",
    )
    fixture = _required_mapping(fixtures.get(iid), f"plan fixture {iid!r}")
    return {
        "job_index": row["job_index"],
        "simulation_iid": iid,
        "input_mask": row["mask"],
        "validation_stratum": fixture.get("validation_stratum"),
        "chromosome_drop_scenario": row["chromosome_drop_scenario"],
        "fraction": row["fraction"],
        "seed": row["seed"],
    }


def _is_stable_row(row: Mapping[str, object], design: SelectionDesign) -> bool:
    matrix = design.confirmation_matrix
    return (
        row["mask"] in matrix.input_masks
        and Decimal(str(row["fraction"])) in matrix.fractions
        and row["seed"] in matrix.seeds
        and row["chromosome_drop_scenario"]
        in {scenario.name for scenario in matrix.drop_scenarios}
    )


def _predicates_pass(
    telemetry: Mapping[str, Decimal] | None,
    predicates: Mapping[str, Decimal],
) -> bool:
    return telemetry is not None and all(
        telemetry.get(field, Decimal("-1")) >= threshold for field, threshold in predicates.items()
    )


def _update_worst_endpoints(
    worst: dict[str, dict[str, object]],
    evaluation: CellEvaluation,
    context: Mapping[str, object],
    endpoints: Sequence[PolicyEndpoint],
) -> None:
    for endpoint in endpoints:
        if endpoint.name not in evaluation.endpoint_values:
            continue
        value = evaluation.endpoint_values[endpoint.name]
        current = worst.get(endpoint.name)
        worse = (
            current is None
            or (endpoint.op == ">=" and value < Decimal(str(current["value"])))
            or (endpoint.op == "<=" and value > Decimal(str(current["value"])))
        )
        if worse:
            worst[endpoint.name] = {
                "value": _decimal_string(value),
                "detail": evaluation.endpoint_details.get(endpoint.name),
                "cell": dict(context),
            }


def _append_example(
    examples: list[dict[str, object]],
    context: Mapping[str, object],
    reasons: Sequence[str],
) -> None:
    if len(examples) < MAX_FAILURE_EXAMPLES:
        examples.append({**context, "reasons": list(reasons)})


def _assert_plan_still_same(plan: lai_coverage_plan.JobPlanSummary) -> None:
    live = lai_coverage_plan.read_job_plan_summary(
        plan.path,
        plan.configuration_sha256,
    )
    if (
        live.plan_sha256 != plan.plan_sha256
        or live.matrix != plan.matrix
        or live.merkle_root_sha256 != plan.merkle_root_sha256
    ):
        raise ValueError("job plan changed during observation evaluation")


def scan_observations(
    plan: lai_coverage_plan.JobPlanSummary,
    observations_path: Path,
    endpoints: Sequence[PolicyEndpoint],
    *,
    design: SelectionDesign | None,
    policy: ConfirmationPolicy | None,
    predicates: Mapping[str, Decimal] | None,
    authenticate_shards: bool,
) -> ObservationScan:
    if (design is None) == (policy is None):
        raise ValueError("observation scan requires exactly one selection phase")
    configuration = plan.configuration
    digest = hashlib.sha256(OBSERVATION_HASH_DOMAIN)
    status_counts: Counter[str] = Counter()
    blocking_counts: Counter[str] = Counter()
    blocking_examples: list[dict[str, object]] = []
    stable_failure_examples: list[dict[str, object]] = []
    false_accept_examples: list[dict[str, object]] = []
    stable_row_count = 0
    stable_failure_count = 0
    stable_minima: dict[str, Decimal] = {}
    worst_endpoints: dict[str, dict[str, object]] = {}
    unsafe_row_count = 0
    false_accept_count = 0
    safe_row_count = 0
    safe_accept_count = 0
    safe_reject_examples: list[dict[str, object]] = []
    safe_group_totals: Counter[tuple[str, str, str]] = Counter()
    safe_group_accepted: Counter[tuple[str, str, str]] = Counter()

    with ObservationDirectory(observations_path, plan.matrix.row_count) as observations:
        observations.validate_entries()
        for index in range(plan.matrix.row_count):
            expected_row = (
                lai_coverage_plan.read_job_plan_row(plan, index)
                if authenticate_shards
                else plan.matrix.row_at(index)
            )
            record, encoded = observations.read_record(index)
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
            _validate_record_against_plan(
                record,
                expected_row,
                configuration,
                plan.configuration_sha256,
                policy=policy,
            )
            context = _row_context(expected_row, configuration)
            status = str(record["status"])
            status_counts[status] += 1
            if status in {"operational_error", "invalid"}:
                blocking_counts[status] += 1
                _append_example(blocking_examples, context, (f"status:{status}",))
            evaluation = evaluate_observation(record, endpoints)
            if status == "ok" and expected_row["mask"] in SUPPORTED_INPUT_MASKS:
                _update_worst_endpoints(worst_endpoints, evaluation, context, endpoints)

            stable = policy is not None or (
                design is not None and _is_stable_row(expected_row, design)
            )
            if stable:
                stable_row_count += 1
                stable_reasons = list(evaluation.reasons)
                if predicates is not None and not _predicates_pass(
                    evaluation.telemetry, predicates
                ):
                    stable_reasons.append("coverage_predicates")
                if stable_reasons:
                    stable_failure_count += 1
                    _append_example(stable_failure_examples, context, stable_reasons)
                elif evaluation.telemetry is not None:
                    for field, value in evaluation.telemetry.items():
                        if field not in stable_minima or value < stable_minima[field]:
                            stable_minima[field] = value

            if expected_row["mask"] in SUPPORTED_INPUT_MASKS and not evaluation.endpoint_pass:
                unsafe_row_count += 1
                if predicates is not None and _predicates_pass(evaluation.telemetry, predicates):
                    false_accept_count += 1
                    _append_example(
                        false_accept_examples,
                        context,
                        (*evaluation.reasons, "accepted_by_coverage_predicates"),
                    )
            elif expected_row["mask"] in SUPPORTED_INPUT_MASKS and evaluation.endpoint_pass:
                safe_row_count += 1
                group = (
                    str(context["input_mask"]),
                    str(context["validation_stratum"]),
                    str(context["chromosome_drop_scenario"]),
                )
                safe_group_totals[group] += 1
                if predicates is not None:
                    if _predicates_pass(evaluation.telemetry, predicates):
                        safe_accept_count += 1
                        safe_group_accepted[group] += 1
                    else:
                        _append_example(
                            safe_reject_examples,
                            context,
                            ("safe_row_rejected_by_coverage_predicates",),
                        )
        observations.validate_entries()
    _assert_plan_still_same(plan)
    return ObservationScan(
        observation_sha256=digest.hexdigest(),
        row_count=plan.matrix.row_count,
        status_counts=dict(sorted(status_counts.items())),
        blocking_counts=dict(sorted(blocking_counts.items())),
        blocking_examples=tuple(blocking_examples),
        stable_row_count=stable_row_count,
        stable_failure_count=stable_failure_count,
        stable_failure_examples=tuple(stable_failure_examples),
        stable_minima=stable_minima,
        worst_endpoints=worst_endpoints,
        unsafe_row_count=unsafe_row_count,
        false_accept_count=false_accept_count,
        false_accept_examples=tuple(false_accept_examples),
        safe_row_count=safe_row_count,
        safe_accept_count=safe_accept_count,
        safe_reject_examples=tuple(safe_reject_examples),
        safe_acceptance_by_group=tuple(
            {
                "input_mask": group[0],
                "validation_stratum": group[1],
                "chromosome_drop_scenario": group[2],
                "accepted": safe_group_accepted[group] if predicates is not None else None,
                "total": total,
                "acceptance_rate": (
                    _decimal_string(Decimal(safe_group_accepted[group]) / Decimal(total))
                    if predicates is not None
                    else None
                ),
            }
            for group, total in sorted(safe_group_totals.items())
        ),
    )


def observation_ledger_sha256(
    observations_path: Path,
    *,
    expected_count: int,
) -> str:
    """Re-hash an unchanged canonical ledger immediately before publication."""
    digest = hashlib.sha256(OBSERVATION_HASH_DOMAIN)
    with ObservationDirectory(observations_path, expected_count) as observations:
        observations.validate_entries()
        for index in range(expected_count):
            _record, encoded = observations.read_record(index)
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        observations.validate_entries()
    return digest.hexdigest()


def _expected_stable_row_count(
    plan: lai_coverage_plan.JobPlanSummary,
    matrix: ConfirmationMatrix,
) -> int:
    return (
        len(plan.matrix.fixture_masks)
        * len(matrix.input_masks)
        * len(matrix.drop_scenarios)
        * len(matrix.fractions)
        * len(matrix.seeds)
    )


def _endpoint_json(endpoints: Sequence[PolicyEndpoint]) -> list[dict[str, str]]:
    return [
        {"name": endpoint.name, "op": endpoint.op, "value": _decimal_string(endpoint.value)}
        for endpoint in endpoints
    ]


def _aggregation_json(aggregation: AggregationPolicy) -> dict[str, object]:
    return {
        "dimensions": list(aggregation.dimensions),
        "all_cells_pass": aggregation.all_cells_pass,
        "average_biological_replicates": aggregation.average_biological_replicates,
    }


def _matrix_json(matrix: ConfirmationMatrix) -> dict[str, object]:
    return {
        "input_masks": list(matrix.input_masks),
        "fractions": [_decimal_string(value) for value in matrix.fractions],
        "seeds": list(matrix.seeds),
        "drop_scenarios": [
            {
                "name": scenario.name,
                "dropped_autosomes": list(scenario.dropped_autosomes),
            }
            for scenario in matrix.drop_scenarios
        ],
    }


def _predicate_json(predicates: Mapping[str, Decimal]) -> list[dict[str, str]]:
    return [
        {
            "field": field,
            "op": COVERAGE_PREDICATE_OPERATORS[field],
            "value": _decimal_string(predicates[field]),
        }
        for field in COVERAGE_PREDICATE_OPERATORS
    ]


def _uncertainty_json(plan: lai_coverage_plan.JobPlanSummary) -> dict[str, object]:
    configuration = plan.configuration
    inputs = _required_mapping(configuration.get("inputs"), "plan inputs")
    fixtures = _required_mapping(inputs.get("fixtures"), "plan fixtures")
    by_stratum: Counter[str] = Counter()
    for iid, _masks in plan.matrix.fixture_masks:
        fixture = _required_mapping(fixtures.get(iid), f"plan fixture {iid!r}")
        stratum = fixture.get("validation_stratum")
        if not isinstance(stratum, str) or not stratum:
            raise ValueError(f"plan fixture {iid!r} has no validation stratum")
        by_stratum[stratum] += 1
    relationships = inputs.get("validation_relationships")
    relationship_component_count: int | None = None
    if isinstance(relationships, Mapping):
        raw_count = relationships.get("component_count")
        if isinstance(raw_count, int) and not isinstance(raw_count, bool) and raw_count > 0:
            relationship_component_count = raw_count
    return {
        "status": "not_estimable_from_dependent_simulation_sweep",
        "confidence_interval": None,
        "reason": (
            "Simulated mosaics can reuse founder components; marker-removal fractions, "
            "seeds, masks, windows, and chromosome-loss scenarios are correlated "
            "sensitivity observations rather than independent biological replicates."
        ),
        "simulation_iid_count": len(plan.matrix.fixture_masks),
        "simulation_iids_by_validation_stratum": dict(sorted(by_stratum.items())),
        "declared_relationship_component_count": relationship_component_count,
        "marker_removal_seed_count": len(plan.matrix.seeds),
        "marker_removal_seeds_are_independent_biological_replicates": False,
        "aggregation_uses_raw_all_cell_pass": True,
    }


def build_selection_artifacts(
    plan: lai_coverage_plan.JobPlanSummary,
    observations_path: Path,
    design: SelectionDesign,
    *,
    selector_script_sha256: str,
) -> SelectionArtifacts:
    script_sha256 = _sha256(selector_script_sha256, "selector script SHA-256")
    first = scan_observations(
        plan,
        observations_path,
        design.endpoints,
        design=design,
        policy=None,
        predicates=None,
        authenticate_shards=True,
    )
    expected_stable = _expected_stable_row_count(plan, design.confirmation_matrix)
    refusal_reasons: list[str] = []
    if first.blocking_counts:
        refusal_reasons.append("unresolved_operational_or_invalid_observations")
    if first.stable_row_count != expected_stable:
        refusal_reasons.append("incomplete_predeclared_stable_region")
    if first.stable_failure_count:
        refusal_reasons.append("predeclared_stable_region_has_unsafe_cells")
    expected_predicate_fields = set(COVERAGE_PREDICATE_OPERATORS)
    predicates: dict[str, Decimal] | None = None
    if (
        not refusal_reasons
        and set(first.stable_minima) == expected_predicate_fields
        and all(value > 0 for value in first.stable_minima.values())
    ):
        predicates = {field: first.stable_minima[field] for field in COVERAGE_PREDICATE_OPERATORS}
    elif not refusal_reasons:
        refusal_reasons.append("stable_region_does_not_define_positive_complete_telemetry")

    second = scan_observations(
        plan,
        observations_path,
        design.endpoints,
        design=design,
        policy=None,
        predicates=predicates,
        authenticate_shards=False,
    )
    if (
        second.observation_sha256 != first.observation_sha256
        or second.status_counts != first.status_counts
        or second.blocking_counts != first.blocking_counts
        or second.stable_row_count != first.stable_row_count
        or second.stable_minima != first.stable_minima
    ):
        raise ValueError("observation ledger changed between deterministic passes")
    if predicates is not None and second.false_accept_count:
        refusal_reasons.append("coverage_predicates_false_accept_unsafe_rows")
    selected = not refusal_reasons and predicates is not None
    report: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "lai_coverage_policy_selection",
        "selection_status": "selected" if selected else "refused",
        "refusal_reasons": refusal_reasons,
        "selection_design": {
            "filename": design.path.name,
            "sha256": design.sha256,
            "policy_id": design.policy_id,
            "frozen": True,
        },
        "identities": {
            "dataset_id": design.dataset_id,
            "bundle_artifact_sha256": design.bundle_artifact_sha256,
            "simulation_manifest_sha256": design.simulation_manifest_sha256,
            "code_revision": design.code_revision,
            "final_confirmation_split_commitment_sha256": (design.confirmation_commitment),
        },
        "calibration_plan": {
            "filename": plan.path.name,
            "job_plan_sha256": plan.plan_sha256,
            "configuration_sha256": plan.configuration_sha256,
            "merkle_root_sha256": plan.merkle_root_sha256,
            "expected_job_count": plan.matrix.row_count,
        },
        "calibration_observations": {
            "ledger_algorithm": "job_index_ordered_canonical_json_length_prefix_v1",
            "sha256": first.observation_sha256,
            "row_count": first.row_count,
            "status_counts": first.status_counts,
        },
        "selector": {
            "algorithm": design.rule.algorithm,
            "script_sha256": script_sha256,
            "minimum_fraction_levels": design.rule.minimum_fraction_levels,
            "require_zero_false_accepts": design.rule.require_zero_false_accepts,
            "require_full_density_no_drop_acceptance": (
                design.rule.require_full_density_no_drop_acceptance
            ),
        },
        "endpoints": _endpoint_json(design.endpoints),
        "aggregation": _aggregation_json(design.aggregation),
        "uncertainty": _uncertainty_json(plan),
        "confirmation_matrix": _matrix_json(design.confirmation_matrix),
        "evaluation": {
            "expected_stable_rows": expected_stable,
            "observed_stable_rows": first.stable_row_count,
            "stable_failure_count": first.stable_failure_count,
            "stable_failure_examples": list(first.stable_failure_examples),
            "blocking_counts": first.blocking_counts,
            "blocking_examples": list(first.blocking_examples),
            "unsafe_production_mask_rows": second.unsafe_row_count,
            "false_accept_count": second.false_accept_count,
            "false_accept_rate": (
                _decimal_string(
                    Decimal(second.false_accept_count) / Decimal(second.unsafe_row_count)
                )
                if second.unsafe_row_count
                else "0"
            ),
            "false_accept_examples": list(second.false_accept_examples),
            "endpoint_safe_production_mask_rows": second.safe_row_count,
            "safe_accept_count": second.safe_accept_count,
            "safe_acceptance_rate": (
                _decimal_string(Decimal(second.safe_accept_count) / Decimal(second.safe_row_count))
                if second.safe_row_count
                else None
            ),
            "safe_reject_examples": list(second.safe_reject_examples),
            "safe_acceptance_by_mask_stratum_scenario": list(second.safe_acceptance_by_group),
            "worst_endpoints": first.worst_endpoints,
        },
        "selected_coverage_predicates": (
            _predicate_json(predicates) if selected and predicates is not None else None
        ),
    }
    report_bytes = lai_coverage_plan.canonical_json_bytes(report)
    if len(report_bytes) > MAX_REPORT_BYTES:
        raise ValueError("selection report exceeds its safety limit")
    if not selected or predicates is None:
        return SelectionArtifacts(report, report_bytes, None, None)
    report_sha256 = hashlib.sha256(report_bytes).hexdigest()
    policy_payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": design.policy_id,
        "frozen": True,
        "dataset_id": design.dataset_id,
        "bundle_artifact_sha256": design.bundle_artifact_sha256,
        "simulation_manifest_sha256": design.simulation_manifest_sha256,
        "code_revision": design.code_revision,
        "calibration_plan": {
            "configuration_sha256": plan.configuration_sha256,
            "job_plan_sha256": plan.plan_sha256,
            "expected_job_count": plan.matrix.row_count,
        },
        "calibration_observation_sha256": first.observation_sha256,
        "selection_provenance": {
            "script_sha256": script_sha256,
            "report_sha256": report_sha256,
            "code_revision": design.code_revision,
        },
        "endpoints": _endpoint_json(design.endpoints),
        "aggregation": _aggregation_json(design.aggregation),
        "coverage_predicates": _predicate_json(predicates),
        "confirmation_matrix": _matrix_json(design.confirmation_matrix),
        "confirmation_commitment": design.confirmation_commitment,
    }
    policy_bytes = lai_coverage_plan.canonical_json_bytes(policy_payload)
    return SelectionArtifacts(report, report_bytes, policy_payload, policy_bytes)


def _selector_script_sha256(code_revision: str) -> tuple[str, _StableFile]:
    script_path = Path(__file__).resolve()
    live = _read_stable_regular_file(
        script_path,
        "selector script",
        maximum_bytes=MAX_REPORT_BYTES,
    )
    try:
        relative = script_path.relative_to(REPO_ROOT).as_posix()
    except ValueError as exc:
        raise ValueError("selector script is outside the repository") from exc
    completed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"{code_revision}:{relative}"],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ValueError("selector script is not tracked at the declared code revision")
    tracked_sha256 = hashlib.sha256(completed.stdout).hexdigest()
    if tracked_sha256 != live.sha256:
        raise ValueError("live selector script differs from the declared code revision")
    pinned_paths = (
        relative,
        "scripts/lai_bundle_v2/lai_coverage_metrics.py",
        "scripts/lai_bundle_v2/lai_coverage_plan.py",
        "scripts/lai_bundle_v2/lai_coverage_policy.py",
    )
    head = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if head.returncode != 0 or head.stdout.strip().lower() != code_revision:
        raise ValueError("selector must run from the declared Git revision")
    tracked = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch", "--", *pinned_paths],
        check=False,
        capture_output=True,
        text=True,
    )
    if tracked.returncode != 0:
        raise ValueError("selector code and policy/plan modules must be tracked by Git")
    status = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            *pinned_paths,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0 or status.stdout.strip():
        raise ValueError("selector code or policy/plan modules differ from the code revision")
    return live.sha256, live


def _assert_stable_file(stable: _StableFile, description: str) -> None:
    current = _read_stable_regular_file(
        stable.path,
        description,
        maximum_bytes=max(len(stable.payload), 1),
    )
    if current.signature != stable.signature or current.sha256 != stable.sha256:
        raise ValueError(f"{description} changed during evaluation")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_new_file(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def publish_selection_artifacts(output_dir: Path, artifacts: SelectionArtifacts) -> None:
    destination = Path(os.path.abspath(output_dir))
    parent = destination.parent
    try:
        parent_metadata = parent.lstat()
    except OSError as exc:
        raise ValueError(f"cannot stat selection output parent: {exc}") from exc
    if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(parent_metadata.st_mode):
        raise ValueError("selection output parent must be a non-symlink directory")
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError("selection output directory already exists")
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=parent))
    published = False
    try:
        os.chmod(temporary, 0o700)
        _write_new_file(temporary / "selection-report.json", artifacts.report_bytes)
        if artifacts.policy_bytes is not None:
            _write_new_file(temporary / "confirmation-policy.json", artifacts.policy_bytes)
        _fsync_directory(temporary)
        if _stat_signature(parent.lstat()) != _stat_signature(parent_metadata):
            raise ValueError("selection output parent changed during publication")
        os.replace(temporary, destination)
        _fsync_directory(parent)
        published = True
    finally:
        if not published:
            shutil.rmtree(temporary, ignore_errors=True)


def _round_trip_policy(
    policy_bytes: bytes,
    design: SelectionDesign,
) -> ConfirmationPolicy:
    with tempfile.TemporaryDirectory(prefix="yeliztli-lai-policy-check-") as temporary:
        path = Path(temporary) / "confirmation-policy.json"
        _write_new_file(path, policy_bytes)
        digest = hashlib.sha256(policy_bytes).hexdigest()
        return read_confirmation_policy(
            path,
            dataset_id=design.dataset_id,
            bundle_artifact_sha256=design.bundle_artifact_sha256,
            simulation_manifest_sha256=design.simulation_manifest_sha256,
            code_revision=design.code_revision,
            final_confirmation_split_commitment_sha256=design.confirmation_commitment,
            expected_confirmation_policy_sha256=digest,
        )


def _policy_predicates(policy: ConfirmationPolicy) -> dict[str, Decimal]:
    predicates = {predicate.field: predicate.value for predicate in policy.coverage_predicates}
    if set(predicates) != set(COVERAGE_PREDICATE_OPERATORS):
        raise ValueError("confirmation policy coverage predicates are incomplete")
    return predicates


def build_confirmation_report(
    plan: lai_coverage_plan.JobPlanSummary,
    observations_path: Path,
    policy: ConfirmationPolicy,
) -> tuple[dict[str, object], bytes, bool]:
    predicates = _policy_predicates(policy)
    first = scan_observations(
        plan,
        observations_path,
        policy.endpoints,
        design=None,
        policy=policy,
        predicates=predicates,
        authenticate_shards=True,
    )
    second = scan_observations(
        plan,
        observations_path,
        policy.endpoints,
        design=None,
        policy=policy,
        predicates=predicates,
        authenticate_shards=False,
    )
    if (
        first.observation_sha256 != second.observation_sha256
        or first.status_counts != second.status_counts
        or first.blocking_counts != second.blocking_counts
        or first.stable_failure_count != second.stable_failure_count
        or first.worst_endpoints != second.worst_endpoints
    ):
        raise ValueError("final observation ledger changed between deterministic passes")
    overall_pass = (
        first.row_count == plan.matrix.row_count
        and first.stable_row_count == plan.matrix.row_count
        and not first.blocking_counts
        and first.stable_failure_count == 0
    )
    report: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "lai_coverage_final_confirmation",
        "overall_pass": overall_pass,
        "policy": {
            "filename": policy.path.name,
            "sha256": policy.sha256,
            "policy_id": policy.policy_id,
            "selection_provenance": {
                "script_sha256": policy.selection_provenance.script_sha256,
                "report_sha256": policy.selection_provenance.report_sha256,
                "code_revision": policy.selection_provenance.code_revision,
            },
        },
        "identities": {
            "dataset_id": policy.dataset_id,
            "bundle_artifact_sha256": policy.bundle_artifact_sha256,
            "simulation_manifest_sha256": policy.simulation_manifest_sha256,
            "code_revision": policy.code_revision,
            "final_confirmation_split_commitment_sha256": (policy.confirmation_commitment),
        },
        "final_plan": {
            "filename": plan.path.name,
            "job_plan_sha256": plan.plan_sha256,
            "configuration_sha256": plan.configuration_sha256,
            "merkle_root_sha256": plan.merkle_root_sha256,
            "expected_job_count": plan.matrix.row_count,
        },
        "final_observations": {
            "ledger_algorithm": "job_index_ordered_canonical_json_length_prefix_v1",
            "sha256": first.observation_sha256,
            "row_count": first.row_count,
            "status_counts": first.status_counts,
        },
        "endpoints": _endpoint_json(policy.endpoints),
        "aggregation": _aggregation_json(policy.aggregation),
        "uncertainty": _uncertainty_json(plan),
        "coverage_predicates": _predicate_json(predicates),
        "confirmation_matrix": _matrix_json(policy.confirmation_matrix),
        "evaluation": {
            "failure_count": first.stable_failure_count,
            "failure_examples": list(first.stable_failure_examples),
            "blocking_counts": first.blocking_counts,
            "blocking_examples": list(first.blocking_examples),
            "worst_endpoints": first.worst_endpoints,
        },
    }
    encoded = lai_coverage_plan.canonical_json_bytes(report)
    if len(encoded) > MAX_REPORT_BYTES:
        raise ValueError("final confirmation report exceeds its safety limit")
    return report, encoded, overall_pass


def publish_confirmation_report(output_path: Path, payload: bytes) -> None:
    destination = Path(os.path.abspath(output_path))
    parent = destination.parent
    try:
        parent_metadata = parent.lstat()
    except OSError as exc:
        raise ValueError(f"cannot stat confirmation-report parent: {exc}") from exc
    if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(parent_metadata.st_mode):
        raise ValueError("confirmation-report parent must be a non-symlink directory")
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError("confirmation report already exists")
    temporary = parent / f".{destination.name}.{os.getpid()}.tmp"
    _write_new_file(temporary, payload)
    published = False
    try:
        if _stat_signature(parent.lstat()) != _stat_signature(parent_metadata):
            raise ValueError("confirmation-report parent changed during publication")
        os.replace(temporary, destination)
        _fsync_directory(parent)
        published = True
    finally:
        if not published:
            temporary.unlink(missing_ok=True)


def _verify_selection_lineage(
    *,
    policy: ConfirmationPolicy,
    design: SelectionDesign,
    calibration_plan: lai_coverage_plan.JobPlanSummary,
    calibration_observations: Path,
    selection_report_path: Path,
    selector_script_sha256: str,
) -> None:
    if policy.selection_provenance.script_sha256 != selector_script_sha256:
        raise ValueError("policy selector-script hash differs from the tracked live selector")
    if policy.calibration_plan.configuration_sha256 != calibration_plan.configuration_sha256:
        raise ValueError("policy calibration configuration hash differs from the archive")
    if policy.calibration_plan.job_plan_sha256 != calibration_plan.plan_sha256:
        raise ValueError("policy calibration job-plan hash differs from the archive")
    if policy.calibration_plan.expected_job_count != calibration_plan.matrix.row_count:
        raise ValueError("policy calibration job count differs from the archive")
    recomputed = build_selection_artifacts(
        calibration_plan,
        calibration_observations,
        design,
        selector_script_sha256=selector_script_sha256,
    )
    if recomputed.policy_bytes is None:
        raise ValueError("archived calibration no longer produces a selectable policy")
    report, stable_report = _read_canonical_json_file(
        selection_report_path,
        "selection report",
        maximum_bytes=MAX_REPORT_BYTES,
    )
    del report
    if stable_report.sha256 != policy.selection_provenance.report_sha256:
        raise ValueError("selection-report hash differs from the policy")
    if stable_report.payload != recomputed.report_bytes:
        raise ValueError("archived selection report differs from deterministic recomputation")
    live_policy = _read_stable_regular_file(
        policy.path,
        "confirmation policy",
        maximum_bytes=MAX_DESIGN_BYTES,
    )
    if live_policy.payload != recomputed.policy_bytes:
        raise ValueError("confirmation policy differs from deterministic recomputation")
    if (
        policy.calibration_observation_sha256
        != recomputed.policy["calibration_observation_sha256"]
    ):
        raise ValueError("policy calibration-observation hash differs from the archive")


def _parse_sha256_argument(value: str) -> str:
    try:
        return _sha256(value, "SHA-256 argument")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _parse_revision_argument(value: str) -> str:
    try:
        return _git_revision(value, "code revision")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    select = subparsers.add_parser("select", help="select or refuse a frozen policy")
    select.add_argument("--selection-design", required=True, type=Path)
    select.add_argument(
        "--expected-selection-design-sha256", required=True, type=_parse_sha256_argument
    )
    select.add_argument("--job-plan", required=True, type=Path)
    select.add_argument("--expected-job-plan-sha256", required=True, type=_parse_sha256_argument)
    select.add_argument(
        "--expected-configuration-sha256", required=True, type=_parse_sha256_argument
    )
    select.add_argument("--observations-dir", required=True, type=Path)
    select.add_argument("--output-dir", required=True, type=Path)

    confirm = subparsers.add_parser(
        "confirm", help="evaluate one frozen policy on the sealed final split"
    )
    confirm.add_argument("--confirmation-policy", required=True, type=Path)
    confirm.add_argument(
        "--expected-confirmation-policy-sha256", required=True, type=_parse_sha256_argument
    )
    confirm.add_argument("--dataset-id", required=True)
    confirm.add_argument("--bundle-artifact-sha256", required=True, type=_parse_sha256_argument)
    confirm.add_argument(
        "--simulation-manifest-sha256", required=True, type=_parse_sha256_argument
    )
    confirm.add_argument("--code-revision", required=True, type=_parse_revision_argument)
    confirm.add_argument(
        "--final-confirmation-split-commitment-sha256",
        required=True,
        type=_parse_sha256_argument,
    )
    confirm.add_argument("--selection-design", required=True, type=Path)
    confirm.add_argument(
        "--expected-selection-design-sha256", required=True, type=_parse_sha256_argument
    )
    confirm.add_argument("--calibration-job-plan", required=True, type=Path)
    confirm.add_argument(
        "--expected-calibration-job-plan-sha256",
        required=True,
        type=_parse_sha256_argument,
    )
    confirm.add_argument(
        "--expected-calibration-configuration-sha256",
        required=True,
        type=_parse_sha256_argument,
    )
    confirm.add_argument("--calibration-observations-dir", required=True, type=Path)
    confirm.add_argument("--selection-report", required=True, type=Path)
    confirm.add_argument("--final-job-plan", required=True, type=Path)
    confirm.add_argument(
        "--expected-final-job-plan-sha256", required=True, type=_parse_sha256_argument
    )
    confirm.add_argument(
        "--expected-final-configuration-sha256",
        required=True,
        type=_parse_sha256_argument,
    )
    confirm.add_argument("--final-observations-dir", required=True, type=Path)
    confirm.add_argument("--output", required=True, type=Path)
    return parser


def _lexical_absolute(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path.expanduser())))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"input/output path contains a symlink component: {current}")
    return absolute


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _reject_output_overlap(output: Path, forbidden_directories: Sequence[Path]) -> None:
    absolute = _lexical_absolute(output)
    for directory in forbidden_directories:
        forbidden = _lexical_absolute(directory)
        if _is_within(absolute, forbidden) or _is_within(forbidden, absolute):
            raise ValueError(f"output path overlaps authenticated input directory {forbidden}")


def _run_select(args: argparse.Namespace) -> int:
    design_path = _lexical_absolute(args.selection_design)
    plan_path = _lexical_absolute(args.job_plan)
    observations_path = _lexical_absolute(args.observations_dir)
    output_dir = _lexical_absolute(args.output_dir)
    _reject_output_overlap(
        output_dir,
        (observations_path,),
    )
    design = read_selection_design(
        design_path,
        expected_sha256=args.expected_selection_design_sha256,
    )
    plan = lai_coverage_plan.read_job_plan_summary(
        plan_path,
        args.expected_configuration_sha256,
    )
    validate_calibration_plan(
        plan,
        design,
        expected_job_plan_sha256=args.expected_job_plan_sha256,
    )
    _reject_output_overlap(output_dir, (plan.shards_directory,))
    selector_sha256, selector_snapshot = _selector_script_sha256(design.code_revision)
    artifacts = build_selection_artifacts(
        plan,
        observations_path,
        design,
        selector_script_sha256=selector_sha256,
    )
    if artifacts.policy_bytes is not None:
        _round_trip_policy(artifacts.policy_bytes, design)
    reread_design = read_selection_design(
        design_path,
        expected_sha256=args.expected_selection_design_sha256,
    )
    if reread_design != design:
        raise ValueError("selection design changed during evaluation")
    _assert_stable_file(selector_snapshot, "selector script")
    _assert_plan_still_same(plan)
    expected_observation_digest = _required_mapping(
        artifacts.report["calibration_observations"],
        "selection-report observation identity",
    )["sha256"]
    if (
        observation_ledger_sha256(
            observations_path,
            expected_count=plan.matrix.row_count,
        )
        != expected_observation_digest
    ):
        raise ValueError("calibration observation ledger changed before publication")
    publish_selection_artifacts(output_dir, artifacts)
    if artifacts.policy_bytes is None:
        print(
            f"selection refused; report written to {output_dir / 'selection-report.json'}",
            file=sys.stderr,
        )
        return 2
    policy_sha256 = hashlib.sha256(artifacts.policy_bytes).hexdigest()
    print(f"confirmation_policy_sha256={policy_sha256}")
    print(f"wrote frozen policy and selection report to {output_dir}", file=sys.stderr)
    return 0


def _run_confirm(args: argparse.Namespace) -> int:
    policy_path = _lexical_absolute(args.confirmation_policy)
    design_path = _lexical_absolute(args.selection_design)
    calibration_plan_path = _lexical_absolute(args.calibration_job_plan)
    calibration_observations = _lexical_absolute(args.calibration_observations_dir)
    selection_report_path = _lexical_absolute(args.selection_report)
    final_plan_path = _lexical_absolute(args.final_job_plan)
    final_observations = _lexical_absolute(args.final_observations_dir)
    output_path = _lexical_absolute(args.output)
    _reject_output_overlap(
        output_path,
        (calibration_observations, final_observations),
    )
    policy = read_confirmation_policy(
        policy_path,
        dataset_id=args.dataset_id,
        bundle_artifact_sha256=args.bundle_artifact_sha256,
        simulation_manifest_sha256=args.simulation_manifest_sha256,
        code_revision=args.code_revision,
        final_confirmation_split_commitment_sha256=(
            args.final_confirmation_split_commitment_sha256
        ),
        expected_confirmation_policy_sha256=args.expected_confirmation_policy_sha256,
    )
    design = read_selection_design(
        design_path,
        expected_sha256=args.expected_selection_design_sha256,
    )
    if (
        design.policy_id != policy.policy_id
        or design.dataset_id != policy.dataset_id
        or design.bundle_artifact_sha256 != policy.bundle_artifact_sha256
        or design.simulation_manifest_sha256 != policy.simulation_manifest_sha256
        or design.code_revision != policy.code_revision
        or design.confirmation_commitment != policy.confirmation_commitment
        or design.endpoints != policy.endpoints
        or design.aggregation != policy.aggregation
        or design.confirmation_matrix != policy.confirmation_matrix
    ):
        raise ValueError("selection design differs from the frozen confirmation policy")
    selector_sha256, selector_snapshot = _selector_script_sha256(policy.code_revision)
    calibration_plan = lai_coverage_plan.read_job_plan_summary(
        calibration_plan_path,
        args.expected_calibration_configuration_sha256,
    )
    validate_calibration_plan(
        calibration_plan,
        design,
        expected_job_plan_sha256=args.expected_calibration_job_plan_sha256,
    )
    _verify_selection_lineage(
        policy=policy,
        design=design,
        calibration_plan=calibration_plan,
        calibration_observations=calibration_observations,
        selection_report_path=selection_report_path,
        selector_script_sha256=selector_sha256,
    )
    final_plan = lai_coverage_plan.read_job_plan_summary(
        final_plan_path,
        args.expected_final_configuration_sha256,
    )
    validate_final_plan(
        final_plan,
        policy,
        expected_job_plan_sha256=args.expected_final_job_plan_sha256,
    )
    _reject_output_overlap(
        output_path,
        (calibration_plan.shards_directory, final_plan.shards_directory),
    )
    report, encoded, overall_pass = build_confirmation_report(
        final_plan,
        final_observations,
        policy,
    )
    reread_policy = read_confirmation_policy(
        policy_path,
        dataset_id=args.dataset_id,
        bundle_artifact_sha256=args.bundle_artifact_sha256,
        simulation_manifest_sha256=args.simulation_manifest_sha256,
        code_revision=args.code_revision,
        final_confirmation_split_commitment_sha256=(
            args.final_confirmation_split_commitment_sha256
        ),
        expected_confirmation_policy_sha256=args.expected_confirmation_policy_sha256,
    )
    if reread_policy.sha256 != policy.sha256:
        raise ValueError("confirmation policy changed during final evaluation")
    reread_design = read_selection_design(
        design_path,
        expected_sha256=args.expected_selection_design_sha256,
    )
    if reread_design != design:
        raise ValueError("selection design changed during final evaluation")
    _assert_stable_file(selector_snapshot, "selector script")
    _assert_plan_still_same(calibration_plan)
    _assert_plan_still_same(final_plan)
    if (
        observation_ledger_sha256(
            calibration_observations,
            expected_count=calibration_plan.matrix.row_count,
        )
        != policy.calibration_observation_sha256
    ):
        raise ValueError("calibration observation ledger changed before final publication")
    expected_final_digest = _required_mapping(
        report["final_observations"],
        "final-report observation identity",
    )["sha256"]
    if (
        observation_ledger_sha256(
            final_observations,
            expected_count=final_plan.matrix.row_count,
        )
        != expected_final_digest
    ):
        raise ValueError("final observation ledger changed before publication")
    publish_confirmation_report(output_path, encoded)
    report_sha256 = hashlib.sha256(encoded).hexdigest()
    print(f"confirmation_report_sha256={report_sha256}")
    if not overall_pass:
        print(f"final confirmation failed; report written to {output_path}", file=sys.stderr)
        return 2
    print(f"final confirmation passed; report written to {output_path}", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "select":
            return _run_select(args)
        if args.command == "confirm":
            return _run_confirm(args)
        raise AssertionError("argparse accepted an unknown command")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    raise AssertionError("argparse.error did not exit")


if __name__ == "__main__":
    raise SystemExit(main())
