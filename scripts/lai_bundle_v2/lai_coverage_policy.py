"""Parse a frozen LAI final-confirmation policy artifact.

The calibration sweep and the final-confirmation evaluation are deliberately
separate operations.  This module defines the narrow JSON contract that binds
the policy chosen from calibration observations to the exact code, data,
matrix, and sealed final-confirmation split on which it may be evaluated.

Decimal thresholds are represented as canonical, plain-decimal JSON strings.
JSON numbers are intentionally rejected so a producer cannot silently round a
threshold before it reaches this parser.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_EXPECTED_JOB_COUNT = 1_000_000
MAX_POLICY_BYTES = 4 * 1024 * 1024

SUPPORTED_INPUT_MASKS = (
    "twentythreeandme_derived_mask",
    "ancestrydna_empirical_mask",
    "synthetic_merged_derived_masks",
)

ENDPOINT_OPERATORS = {
    "assignment_completeness": ">=",
    "local_diplotype_accuracy": ">=",
    "local_haplotype_accuracy_best_orientation": ">=",
    "global_ancestry_total_variation": "<=",
    "per_truth_class.assignment_completeness": ">=",
    "per_truth_diplotype.local_diplotype_accuracy": ">=",
}

AGGREGATION_DIMENSIONS = (
    "simulation_iid",
    "input_mask",
    "validation_stratum",
    "chromosome_drop_scenario",
    "fraction",
    "seed",
)

COVERAGE_PREDICATE_OPERATORS = {
    "emitted_markers.total": ">=",
    "model_markers.aggregate.matched": ">=",
    "model_markers.aggregate.match_rate": ">=",
    "phased_autosomes.count": ">=",
    "analyzed_autosomes.count": ">=",
    "haplotype_windows.valid_assigned": ">=",
    "haplotype_windows.assignment_rate": ">=",
}

_RATE_PREDICATE_FIELDS = frozenset(
    {
        "model_markers.aggregate.match_rate",
        "haplotype_windows.assignment_rate",
    }
)
_COUNT_PREDICATE_FIELDS = frozenset(COVERAGE_PREDICATE_OPERATORS) - _RATE_PREDICATE_FIELDS
_AUTOSOME_COUNT_PREDICATE_FIELDS = frozenset(
    {
        "phased_autosomes.count",
        "analyzed_autosomes.count",
    }
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_GIT_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]*\Z")
_SCENARIO_NAME_RE = re.compile(r"[a-z][a-z0-9_-]*\Z")
_CANONICAL_NONNEGATIVE_DECIMAL_RE = re.compile(
    r"(?:0|0\.[0-9]*[1-9]|[1-9][0-9]*(?:\.[0-9]*[1-9])?)\Z"
)
_AUTOSOMES = tuple(str(chrom) for chrom in range(1, 23))
_AUTOSOME_SET = frozenset(_AUTOSOMES)
_MIN_SIGNED_64 = -(2**63)
_MAX_SIGNED_64 = 2**63 - 1


@dataclass(frozen=True, slots=True)
class CalibrationPlanIdentity:
    """Identity and cardinality of the complete calibration job plan."""

    configuration_sha256: str
    job_plan_sha256: str
    expected_job_count: int


@dataclass(frozen=True, slots=True)
class SelectionProvenance:
    """Code and report that selected the frozen policy."""

    script_sha256: str
    report_sha256: str
    code_revision: str


@dataclass(frozen=True, slots=True)
class PolicyEndpoint:
    """One predeclared truth-based acceptance endpoint."""

    name: str
    op: str
    value: Decimal


@dataclass(frozen=True, slots=True)
class AggregationPolicy:
    """Fail-closed policy for strata and biological replicates."""

    dimensions: tuple[str, ...]
    all_cells_pass: bool
    average_biological_replicates: bool


@dataclass(frozen=True, slots=True)
class CoveragePredicate:
    """One minimum-data predicate over schema-v1 production telemetry."""

    field: str
    op: str
    value: Decimal


@dataclass(frozen=True, slots=True)
class ChromosomeDropScenario:
    """A named, structured set of deliberately absent autosomes."""

    name: str
    dropped_autosomes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConfirmationMatrix:
    """The exact sealed matrix on which the policy may be confirmed once."""

    input_masks: tuple[str, ...]
    fractions: tuple[Decimal, ...]
    seeds: tuple[int, ...]
    drop_scenarios: tuple[ChromosomeDropScenario, ...]


@dataclass(frozen=True, slots=True)
class ConfirmationPolicy:
    """A validated frozen policy bound to one final-confirmation split."""

    path: Path
    sha256: str
    schema_version: int
    policy_id: str
    frozen: bool
    dataset_id: str
    bundle_artifact_sha256: str
    simulation_manifest_sha256: str
    code_revision: str
    calibration_plan: CalibrationPlanIdentity
    calibration_observation_sha256: str
    selection_provenance: SelectionProvenance
    endpoints: tuple[PolicyEndpoint, ...]
    aggregation: AggregationPolicy
    coverage_predicates: tuple[CoveragePredicate, ...]
    confirmation_matrix: ConfirmationMatrix
    confirmation_commitment: str


def confirmation_policy_provenance(policy: ConfirmationPolicy) -> dict[str, object]:
    """Return the compact identity that plans and result rows must repeat."""
    return {
        "schema_version": policy.schema_version,
        "filename": policy.path.name,
        "sha256": policy.sha256,
        "policy_id": policy.policy_id,
        "frozen": policy.frozen,
        "dataset_id": policy.dataset_id,
        "bundle_artifact_sha256": policy.bundle_artifact_sha256,
        "simulation_manifest_sha256": policy.simulation_manifest_sha256,
        "code_revision": policy.code_revision,
        "confirmation_commitment": policy.confirmation_commitment,
        "calibration_plan": {
            "configuration_sha256": policy.calibration_plan.configuration_sha256,
            "job_plan_sha256": policy.calibration_plan.job_plan_sha256,
            "expected_job_count": policy.calibration_plan.expected_job_count,
        },
        "calibration_observation_sha256": policy.calibration_observation_sha256,
        "selection_provenance": {
            "script_sha256": policy.selection_provenance.script_sha256,
            "report_sha256": policy.selection_provenance.report_sha256,
            "code_revision": policy.selection_provenance.code_revision,
        },
    }


def _exact_keys(value: object, expected: set[str], description: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{description} must contain exactly {sorted(expected)!r}")
    return value


def _full_sha256(value: object, description: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{description} must be a full lowercase SHA-256")
    return value


def _full_git_revision(value: object, description: str) -> str:
    if not isinstance(value, str) or _GIT_REVISION_RE.fullmatch(value) is None:
        raise ValueError(f"{description} must be a full lowercase 40-hex revision")
    return value


def _identifier(value: object, description: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{description} must be a nonempty canonical identifier")
    return value


def _canonical_nonnegative_decimal(value: object, description: str) -> Decimal:
    if not isinstance(value, str) or _CANONICAL_NONNEGATIVE_DECIMAL_RE.fullmatch(value) is None:
        raise ValueError(f"{description} must be a canonical nonnegative decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{description} must be finite and nonnegative")
    return parsed


def _unit_decimal(value: object, description: str, *, zero_allowed: bool) -> Decimal:
    parsed = _canonical_nonnegative_decimal(value, description)
    if parsed > 1 or (not zero_allowed and parsed == 0):
        lower_bound = "[0" if zero_allowed else "(0"
        raise ValueError(f"{description} must be in {lower_bound}, 1]")
    return parsed


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r} is not permitted")


def _stat_signature(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_stable_regular_file(path: Path) -> tuple[bytes, str]:
    """Read and hash one unchanged regular file without following a final symlink."""
    try:
        path_before = path.lstat()
    except OSError as exc:
        raise ValueError(f"{path}: cannot stat confirmation policy: {exc}") from exc
    if stat.S_ISLNK(path_before.st_mode):
        raise ValueError(f"{path}: confirmation policy must not be a symlink")
    if not stat.S_ISREG(path_before.st_mode):
        raise ValueError(f"{path}: confirmation policy must be a regular file")
    if path_before.st_size > MAX_POLICY_BYTES:
        raise ValueError(
            f"{path}: confirmation policy exceeds the {MAX_POLICY_BYTES}-byte safety limit"
        )

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        file_descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{path}: cannot open confirmation policy: {exc}") from exc

    try:
        opened_before = os.fstat(file_descriptor)
        if not stat.S_ISREG(opened_before.st_mode):
            raise ValueError(f"{path}: confirmation policy must be a regular file")
        if _stat_signature(opened_before) != _stat_signature(path_before):
            raise ValueError(f"{path}: confirmation policy changed before it was opened")

        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        opened_after = os.fstat(file_descriptor)
    finally:
        os.close(file_descriptor)

    if _stat_signature(opened_before) != _stat_signature(opened_after):
        raise ValueError(f"{path}: confirmation policy changed while it was read")
    try:
        path_after = path.lstat()
    except OSError as exc:
        raise ValueError(f"{path}: confirmation policy changed after it was read") from exc
    if _stat_signature(opened_after) != _stat_signature(path_after):
        raise ValueError(f"{path}: confirmation policy changed while it was read")

    payload = b"".join(chunks)
    return payload, hashlib.sha256(payload).hexdigest()


def _parse_calibration_plan(value: object) -> CalibrationPlanIdentity:
    raw = _exact_keys(
        value,
        {"configuration_sha256", "job_plan_sha256", "expected_job_count"},
        "calibration_plan",
    )
    count = raw["expected_job_count"]
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or not 0 < count <= MAX_EXPECTED_JOB_COUNT
    ):
        raise ValueError(
            "calibration_plan.expected_job_count must be a positive integer "
            f"no greater than {MAX_EXPECTED_JOB_COUNT}"
        )
    return CalibrationPlanIdentity(
        configuration_sha256=_full_sha256(
            raw["configuration_sha256"], "calibration_plan.configuration_sha256"
        ),
        job_plan_sha256=_full_sha256(raw["job_plan_sha256"], "calibration_plan.job_plan_sha256"),
        expected_job_count=count,
    )


def _parse_selection_provenance(value: object, *, code_revision: str) -> SelectionProvenance:
    raw = _exact_keys(
        value,
        {"script_sha256", "report_sha256", "code_revision"},
        "selection_provenance",
    )
    revision = _full_git_revision(raw["code_revision"], "selection_provenance.code_revision")
    if revision != code_revision:
        raise ValueError("selection_provenance.code_revision does not match code_revision")
    return SelectionProvenance(
        script_sha256=_full_sha256(raw["script_sha256"], "selection_provenance.script_sha256"),
        report_sha256=_full_sha256(raw["report_sha256"], "selection_provenance.report_sha256"),
        code_revision=revision,
    )


def _parse_endpoints(value: object) -> tuple[PolicyEndpoint, ...]:
    if not isinstance(value, list) or len(value) != len(ENDPOINT_OPERATORS):
        raise ValueError(f"endpoints must contain exactly {len(ENDPOINT_OPERATORS)} entries")

    endpoints: list[PolicyEndpoint] = []
    names: set[str] = set()
    for index, item in enumerate(value):
        raw = _exact_keys(item, {"name", "op", "value"}, f"endpoints[{index}]")
        name = raw["name"]
        if not isinstance(name, str) or name not in ENDPOINT_OPERATORS:
            raise ValueError(f"endpoints[{index}].name is not a supported endpoint")
        if name in names:
            raise ValueError(f"endpoints contains duplicate endpoint {name!r}")
        names.add(name)
        expected_op = ENDPOINT_OPERATORS[name]
        if raw["op"] != expected_op:
            raise ValueError(f"endpoint {name!r} must use operator {expected_op!r}")
        endpoints.append(
            PolicyEndpoint(
                name=name,
                op=expected_op,
                value=_unit_decimal(raw["value"], f"endpoints[{index}].value", zero_allowed=True),
            )
        )
        if (expected_op == ">=" and endpoints[-1].value <= 0) or (
            expected_op == "<=" and endpoints[-1].value >= 1
        ):
            raise ValueError(f"endpoint {name!r} must impose a non-vacuous target")

    missing = set(ENDPOINT_OPERATORS) - names
    if missing:
        raise ValueError(f"endpoints are missing required names {sorted(missing)!r}")
    return tuple(endpoints)


def _parse_aggregation(value: object) -> AggregationPolicy:
    raw = _exact_keys(
        value,
        {"dimensions", "all_cells_pass", "average_biological_replicates"},
        "aggregation",
    )
    if raw["dimensions"] != list(AGGREGATION_DIMENSIONS):
        raise ValueError(
            "aggregation.dimensions must exactly match the frozen aggregation dimensions"
        )
    if raw["all_cells_pass"] is not True:
        raise ValueError("aggregation.all_cells_pass must be true")
    if raw["average_biological_replicates"] is not False:
        raise ValueError("aggregation.average_biological_replicates must be false")
    return AggregationPolicy(
        dimensions=AGGREGATION_DIMENSIONS,
        all_cells_pass=True,
        average_biological_replicates=False,
    )


def _parse_coverage_predicates(value: object) -> tuple[CoveragePredicate, ...]:
    if not isinstance(value, list) or len(value) != len(COVERAGE_PREDICATE_OPERATORS):
        raise ValueError(
            "coverage_predicates must contain every required production telemetry field"
        )
    predicates: list[CoveragePredicate] = []
    fields: set[str] = set()
    has_positive_requirement = False
    for index, item in enumerate(value):
        raw = _exact_keys(
            item,
            {"field", "op", "value"},
            f"coverage_predicates[{index}]",
        )
        field = raw["field"]
        if not isinstance(field, str) or field not in COVERAGE_PREDICATE_OPERATORS:
            raise ValueError(f"coverage_predicates[{index}].field is not supported")
        if field in fields:
            raise ValueError(f"coverage_predicates contains duplicate field {field!r}")
        fields.add(field)
        expected_op = COVERAGE_PREDICATE_OPERATORS[field]
        if raw["op"] != expected_op:
            raise ValueError(f"coverage predicate {field!r} must use operator {expected_op!r}")
        predicate_value = _canonical_nonnegative_decimal(
            raw["value"], f"coverage_predicates[{index}].value"
        )
        if field in _RATE_PREDICATE_FIELDS and predicate_value > 1:
            raise ValueError(f"coverage predicate rate {field!r} must be in [0, 1]")
        if field in _COUNT_PREDICATE_FIELDS and predicate_value != predicate_value.to_integral():
            raise ValueError(f"coverage predicate count {field!r} must be integral")
        if field in _AUTOSOME_COUNT_PREDICATE_FIELDS and predicate_value > 22:
            raise ValueError(f"coverage predicate autosome count {field!r} must not exceed 22")
        has_positive_requirement |= predicate_value > 0
        predicates.append(
            CoveragePredicate(
                field=field,
                op=expected_op,
                value=predicate_value,
            )
        )
    if not has_positive_requirement:
        raise ValueError("coverage_predicates must include a meaningful positive requirement")
    missing = set(COVERAGE_PREDICATE_OPERATORS) - fields
    if missing:
        raise ValueError(f"coverage_predicates are missing required fields {sorted(missing)!r}")
    if any(predicate.value <= 0 for predicate in predicates):
        raise ValueError("every coverage predicate must impose a positive production minimum")
    return tuple(predicates)


def _parse_fractions(value: object) -> tuple[Decimal, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("confirmation_matrix.fractions must be a nonempty list")
    fractions = tuple(
        _unit_decimal(
            item,
            f"confirmation_matrix.fractions[{index}]",
            zero_allowed=False,
        )
        for index, item in enumerate(value)
    )
    if len(fractions) != len(set(fractions)):
        raise ValueError("confirmation_matrix.fractions must be unique")
    if tuple(sorted(fractions)) != fractions:
        raise ValueError("confirmation_matrix.fractions must be strictly increasing")
    return fractions


def _parse_seeds(value: object) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("confirmation_matrix.seeds must be a nonempty list")
    seeds: list[int] = []
    for index, seed in enumerate(value):
        if (
            not isinstance(seed, int)
            or isinstance(seed, bool)
            or not _MIN_SIGNED_64 <= seed <= _MAX_SIGNED_64
        ):
            raise ValueError(f"confirmation_matrix.seeds[{index}] must be a signed 64-bit integer")
        seeds.append(seed)
    if len(seeds) != len(set(seeds)):
        raise ValueError("confirmation_matrix.seeds must be unique")
    return tuple(seeds)


def _parse_drop_scenarios(value: object) -> tuple[ChromosomeDropScenario, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("confirmation_matrix.drop_scenarios must be a nonempty list")

    scenarios: list[ChromosomeDropScenario] = []
    names: set[str] = set()
    dropped_sets: set[tuple[str, ...]] = set()
    for index, item in enumerate(value):
        raw = _exact_keys(
            item,
            {"name", "dropped_autosomes"},
            f"confirmation_matrix.drop_scenarios[{index}]",
        )
        name = raw["name"]
        if not isinstance(name, str) or _SCENARIO_NAME_RE.fullmatch(name) is None:
            raise ValueError(f"confirmation_matrix.drop_scenarios[{index}].name is not canonical")
        if name in names:
            raise ValueError(f"confirmation_matrix.drop_scenarios duplicates name {name!r}")
        names.add(name)

        raw_autosomes = raw["dropped_autosomes"]
        if not isinstance(raw_autosomes, list) or any(
            not isinstance(chrom, str) or chrom not in _AUTOSOME_SET for chrom in raw_autosomes
        ):
            raise ValueError(
                f"confirmation_matrix.drop_scenarios[{index}].dropped_autosomes "
                "must contain only canonical autosomes 1..22"
            )
        dropped = tuple(raw_autosomes)
        if len(dropped) != len(set(dropped)) or tuple(sorted(dropped, key=int)) != dropped:
            raise ValueError(
                f"confirmation_matrix.drop_scenarios[{index}].dropped_autosomes "
                "must be unique and numerically sorted"
            )
        if (name == "none") != (not dropped):
            raise ValueError("only the 'none' drop scenario may have no dropped autosomes")
        if dropped in dropped_sets:
            raise ValueError("confirmation_matrix.drop_scenarios duplicates an autosome set")
        dropped_sets.add(dropped)
        scenarios.append(ChromosomeDropScenario(name=name, dropped_autosomes=dropped))

    if "none" not in names:
        raise ValueError("confirmation_matrix.drop_scenarios must include 'none'")
    return tuple(scenarios)


def _parse_confirmation_matrix(value: object) -> ConfirmationMatrix:
    raw = _exact_keys(
        value,
        {"input_masks", "fractions", "seeds", "drop_scenarios"},
        "confirmation_matrix",
    )
    if raw["input_masks"] != list(SUPPORTED_INPUT_MASKS):
        raise ValueError(
            "confirmation_matrix.input_masks must exactly match the three supported masks"
        )
    return ConfirmationMatrix(
        input_masks=SUPPORTED_INPUT_MASKS,
        fractions=_parse_fractions(raw["fractions"]),
        seeds=_parse_seeds(raw["seeds"]),
        drop_scenarios=_parse_drop_scenarios(raw["drop_scenarios"]),
    )


def read_confirmation_policy(
    path: str | os.PathLike[str],
    *,
    dataset_id: str,
    bundle_artifact_sha256: str,
    simulation_manifest_sha256: str,
    code_revision: str,
    final_confirmation_split_commitment_sha256: str,
    expected_confirmation_policy_sha256: str,
) -> ConfirmationPolicy:
    """Read and authenticate a frozen schema-v1 confirmation policy.

    Every external identity is supplied independently by the caller.  The
    policy is rejected if any identity differs, including the commitment of the
    still-sealed final-confirmation split.
    """
    expected_dataset_id = _identifier(dataset_id, "expected dataset_id")
    expected_bundle_sha256 = _full_sha256(
        bundle_artifact_sha256, "expected bundle_artifact_sha256"
    )
    expected_simulation_sha256 = _full_sha256(
        simulation_manifest_sha256, "expected simulation_manifest_sha256"
    )
    expected_code_revision = _full_git_revision(code_revision, "expected code_revision")
    expected_commitment = _full_sha256(
        final_confirmation_split_commitment_sha256,
        "expected final_confirmation_split_commitment_sha256",
    )
    expected_policy_sha256 = _full_sha256(
        expected_confirmation_policy_sha256,
        "expected confirmation policy SHA-256",
    )

    policy_path = Path(path)
    payload, payload_sha256 = _read_stable_regular_file(policy_path)
    if payload_sha256 != expected_policy_sha256:
        raise ValueError("confirmation policy SHA-256 does not match expected identity")
    try:
        raw_value = json.loads(
            payload,
            object_pairs_hook=_reject_duplicate_object_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{policy_path}: invalid confirmation policy JSON: {exc}") from exc

    raw = _exact_keys(
        raw_value,
        {
            "schema_version",
            "policy_id",
            "frozen",
            "dataset_id",
            "bundle_artifact_sha256",
            "simulation_manifest_sha256",
            "code_revision",
            "calibration_plan",
            "calibration_observation_sha256",
            "selection_provenance",
            "endpoints",
            "aggregation",
            "coverage_predicates",
            "confirmation_matrix",
            "confirmation_commitment",
        },
        "confirmation policy",
    )

    if not isinstance(raw["schema_version"], int) or isinstance(raw["schema_version"], bool):
        raise ValueError("schema_version must be integer 1")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported confirmation policy schema {raw['schema_version']!r}")
    policy_id = _identifier(raw["policy_id"], "policy_id")
    if raw["frozen"] is not True:
        raise ValueError("confirmation policy must set frozen to true")

    observed_dataset_id = _identifier(raw["dataset_id"], "dataset_id")
    observed_bundle_sha256 = _full_sha256(raw["bundle_artifact_sha256"], "bundle_artifact_sha256")
    observed_simulation_sha256 = _full_sha256(
        raw["simulation_manifest_sha256"], "simulation_manifest_sha256"
    )
    observed_revision = _full_git_revision(raw["code_revision"], "code_revision")
    observed_commitment = _full_sha256(raw["confirmation_commitment"], "confirmation_commitment")

    identities = (
        ("dataset_id", observed_dataset_id, expected_dataset_id),
        ("bundle_artifact_sha256", observed_bundle_sha256, expected_bundle_sha256),
        (
            "simulation_manifest_sha256",
            observed_simulation_sha256,
            expected_simulation_sha256,
        ),
        ("code_revision", observed_revision, expected_code_revision),
        ("confirmation_commitment", observed_commitment, expected_commitment),
    )
    for description, observed, expected in identities:
        if observed != expected:
            raise ValueError(f"confirmation policy {description} does not match expected identity")

    return ConfirmationPolicy(
        path=policy_path,
        sha256=payload_sha256,
        schema_version=SCHEMA_VERSION,
        policy_id=policy_id,
        frozen=True,
        dataset_id=observed_dataset_id,
        bundle_artifact_sha256=observed_bundle_sha256,
        simulation_manifest_sha256=observed_simulation_sha256,
        code_revision=observed_revision,
        calibration_plan=_parse_calibration_plan(raw["calibration_plan"]),
        calibration_observation_sha256=_full_sha256(
            raw["calibration_observation_sha256"], "calibration_observation_sha256"
        ),
        selection_provenance=_parse_selection_provenance(
            raw["selection_provenance"], code_revision=observed_revision
        ),
        endpoints=_parse_endpoints(raw["endpoints"]),
        aggregation=_parse_aggregation(raw["aggregation"]),
        coverage_predicates=_parse_coverage_predicates(raw["coverage_predicates"]),
        confirmation_matrix=_parse_confirmation_matrix(raw["confirmation_matrix"]),
        confirmation_commitment=observed_commitment,
    )
