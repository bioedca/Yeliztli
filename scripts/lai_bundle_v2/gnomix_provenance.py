#!/usr/bin/env python3
"""Create and verify immutable provenance for Gnomix-trained models."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Hashable
from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from gnomix_training_manifests import (  # noqa: E402
    INPUT_SCHEMA_VERSION,
    SPLIT_SCHEMA_VERSION,
    LoadedManifest,
    ManifestError,
    load_input_manifest,
    load_split_manifest,
)

MODEL_RECORD_SCHEMA_VERSION = 2
AGGREGATE_SCHEMA_VERSION = 2
# Backwards-compatible name for callers that treated the model-record schema as
# the only provenance schema before aggregate schema v2 was introduced.
SCHEMA_VERSION = MODEL_RECORD_SCHEMA_VERSION
GNOMIX_REPOSITORY = "https://github.com/AI-sandbox/gnomix"
TRAINING_INPUT_BUNDLE_PATH = "metadata/gnomix_training_inputs.json"
TRAINING_SPLIT_BUNDLE_PATH = "metadata/gnomix_training_splits.json"
TRAINING_PROVENANCE_BUNDLE_PATH = "metadata/gnomix_training_provenance.json"
_COMMIT_RE = re.compile(r"[0-9a-fA-F]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_OFFICIAL_REMOTE_RE = re.compile(
    r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"ai-sandbox/gnomix(?:\.git)?/?",
    re.IGNORECASE,
)
_MODEL_RECORD_KEYS = {
    "schema_version",
    "chromosome",
    "gnomix_repository",
    "gnomix_git_commit",
    "gnomix_checkout_clean",
    "simulation_run",
    "effective_config_sha256",
    "genetic_map_sha256",
    "model_filename",
    "model_sha256",
    "training_input_manifest",
    "training_split_manifest",
}
_AGGREGATE_KEYS = {
    "schema_version",
    "gnomix_repository",
    "gnomix_git_commit",
    "gnomix_checkout_clean",
    "simulation_run",
    "effective_config_sha256",
    "training_input_manifest",
    "training_split_manifest",
    "models",
}
_AGGREGATE_MODEL_KEYS = {
    "chromosome",
    "model_filename",
    "model_sha256",
    "genetic_map_sha256",
    "provenance_file",
}
_AUTOSOMES = tuple(f"chr{number}" for number in range(1, 23))


class ProvenanceError(ValueError):
    """Raised when Gnomix provenance is missing, malformed, or stale."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Use PyYAML semantics without code execution or silent key replacement."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        if not isinstance(node, MappingNode):
            raise ConstructorError(
                None,
                None,
                f"expected a mapping node, but found {node.id}",
                node.start_mark,
            )
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, Hashable):
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable key",
                    key_node.start_mark,
                )
            if key in mapping:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Construct one JSON object without silently collapsing duplicate keys."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProvenanceError(f"Gnomix provenance JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite_json(value: str) -> None:
    raise ProvenanceError(f"Gnomix provenance JSON contains non-finite value {value}")


def _load_strict_json(raw: bytes, *, label: str) -> object:
    """Parse one UTF-8 JSON snapshot with unambiguous object semantics."""
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_nonfinite_json,
        )
    except ProvenanceError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"invalid {label} JSON: {exc}") from exc


def normalize_commit(value: str) -> str:
    """Return a canonical full Git commit, rejecting symbolic/short refs."""
    if not _COMMIT_RE.fullmatch(value):
        raise ProvenanceError(
            "GNOMIX_EXPECTED_COMMIT must be an explicitly selected full "
            "40-character hexadecimal commit SHA"
        )
    return value.lower()


def normalize_chromosome(value: str) -> str:
    """Return an autosome label in canonical ``chrN`` form."""
    number_text = value[3:] if value.startswith("chr") else value
    if not number_text.isdigit() or not 1 <= int(number_text) <= 22:
        raise ProvenanceError(f"invalid autosome label: {value!r}")
    return f"chr{int(number_text)}"


def sha256_file(path: Path) -> str:
    """Hash one required, non-empty provenance input."""
    if not path.is_file() or path.stat().st_size == 0:
        raise ProvenanceError(f"required provenance input is missing or empty: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_simulation_config(path: Path) -> bool:
    """Require Gnomix to generate fresh simulations for this training run.

    Gnomix also accepts ``simulation.run: false`` and then consumes arrays from
    ``simulation.path``. The schema-v1 training-input manifest deliberately does
    not inventory those arrays, so accepting that mode would leave an unauthenticated
    training input. Match PyYAML's standard YAML semantics while using SafeLoader
    and rejecting duplicate constructed keys, including plain/quoted/tagged aliases
    that Gnomix's UnsafeLoader would otherwise silently replace. The release contract
    also requires the release's exact train1, train2, and val ratios because all
    three emitted founder-map splits are authenticated downstream, plus the native
    model basename that the release scripts package.
    """
    if not path.is_file() or path.stat().st_size == 0:
        raise ProvenanceError(f"required Gnomix config is missing or empty: {path}")
    if path.stat().st_size > 1 << 20:
        raise ProvenanceError(f"Gnomix config is unexpectedly large: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ProvenanceError(f"Gnomix config must be UTF-8: {path}: {exc}") from exc
    if text.startswith("\ufeff"):
        raise ProvenanceError(f"Gnomix config must not contain a UTF-8 BOM: {path}")

    try:
        config = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise ProvenanceError(f"invalid Gnomix config YAML: {exc}") from exc
    if not isinstance(config, dict):
        raise ProvenanceError("Gnomix config root must be a mapping")
    simulation = config.get("simulation")
    if not isinstance(simulation, dict):
        raise ProvenanceError("Gnomix config simulation must be a mapping")
    run = simulation.get("run")
    if run is False:
        raise ProvenanceError(
            "simulation.run=false is unsupported because pre-simulated arrays are not "
            "inventoried by the training-input manifest; set simulation.run=true and retrain"
        )
    if run is not True:
        raise ProvenanceError("Gnomix config simulation.run must be the boolean true")

    splits = simulation.get("splits")
    if not isinstance(splits, dict):
        raise ProvenanceError("Gnomix config simulation.splits must be a mapping")
    ratios = splits.get("ratios")
    if not isinstance(ratios, dict):
        raise ProvenanceError("Gnomix config simulation.splits.ratios must be a mapping")
    required_ratios = {"train1": 0.8, "train2": 0.15, "val": 0.05}
    if set(ratios) != set(required_ratios):
        raise ProvenanceError(
            "Gnomix config simulation.splits.ratios must contain exactly train1, train2, and val"
        )
    if tuple(ratios) != tuple(required_ratios):
        raise ProvenanceError(
            "Gnomix config simulation.splits.ratios must be ordered train1, train2, val "
            "to preserve the release split rounding contract"
        )
    for split_name, expected_ratio in required_ratios.items():
        value = ratios[split_name]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ProvenanceError(
                f"Gnomix config simulation.splits.ratios.{split_name} must be "
                "a finite number greater than zero"
            )
        if value != expected_ratio:
            raise ProvenanceError(
                f"Gnomix config simulation.splits.ratios.{split_name} must equal "
                f"the release value {expected_ratio}"
            )

    model = config.get("model")
    if not isinstance(model, dict):
        raise ProvenanceError("Gnomix config model must be a mapping")
    model_name = model.get("name")
    if model_name != "model":
        raise ProvenanceError(
            "Gnomix config model.name must be 'model' so the native model path "
            "matches the release contract"
        )
    inference = model.get("inference")
    if inference not in (None, "", "default"):
        raise ProvenanceError(
            "Gnomix config model.inference must resolve to 'default'; the release "
            "exporter does not support alternate model architectures"
        )
    calibrate = model.get("calibrate")
    if calibrate is not None and calibrate is not False:
        raise ProvenanceError(
            "Gnomix config model.calibrate must be false or null; the release "
            "exporter cannot preserve a trained calibrator"
        )
    if config.get("verbose") is not True:
        raise ProvenanceError(
            "Gnomix config verbose must be the boolean true so the required validation "
            "accuracy is emitted to the training log"
        )
    return True


def _require_viable_training_founders(input_payload: dict[str, Any]) -> int:
    training_count = input_payload["sample_mappings"]["counts"]["training"]
    if type(training_count) is not int or training_count <= 25:
        raise ProvenanceError(
            "Gnomix training-input manifest must contain more than 25 training founders "
            "so upstream does not delete the required validation split"
        )
    founders_by_superpopulation: dict[str, int] = {}
    for member in input_payload["sample_mappings"]["members"]:
        if member["role"] != "training":
            continue
        superpopulation = str(member["superpopulation"])
        founders_by_superpopulation[superpopulation] = (
            founders_by_superpopulation.get(superpopulation, 0) + 1
        )
    required_superpopulations = {"AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE"}
    if set(founders_by_superpopulation) != required_superpopulations:
        raise ProvenanceError(
            "Gnomix training-input manifest must contain exactly the release "
            f"superpopulations {sorted(required_superpopulations)}; found "
            f"{sorted(founders_by_superpopulation)}"
        )
    undersized = {name: count for name, count in founders_by_superpopulation.items() if count < 11}
    if undersized:
        raise ProvenanceError(
            "Gnomix training-input manifest needs at least 11 training founders per "
            f"superpopulation for a nonempty 0.05 validation split: {undersized}"
        )
    return training_count


def verify_training_input_viability(path: Path) -> int:
    """Require enough authenticated founders for Gnomix to retain ``val``."""
    try:
        contract = load_input_manifest(path)
    except (ManifestError, OSError) as exc:
        raise ProvenanceError(f"invalid Gnomix training-input manifest: {exc}") from exc
    return _require_viable_training_founders(contract.payload)


def _training_manifest_fields_from_contracts(
    input_contract: LoadedManifest,
    split_contract: LoadedManifest,
) -> dict[str, Any]:
    """Return record identities from already captured canonical manifests."""
    _require_viable_training_founders(input_contract.payload)
    referenced_input = split_contract.payload["training_input_manifest"]
    if (
        referenced_input["schema_version"] != INPUT_SCHEMA_VERSION
        or referenced_input["sha256"] != input_contract.sha256
    ):
        raise ProvenanceError(
            "Gnomix training-split manifest does not bind the supplied training-input manifest"
        )
    return {
        "training_input_manifest": {
            "schema_version": INPUT_SCHEMA_VERSION,
            "sha256": input_contract.sha256,
        },
        "training_split_manifest": {
            "schema_version": SPLIT_SCHEMA_VERSION,
            "sha256": split_contract.sha256,
        },
    }


def _training_manifest_fields(
    training_input_manifest: Path,
    training_split_manifest: Path,
) -> dict[str, Any]:
    """Load canonical manifests once and return their record-level identities."""
    try:
        input_contract = load_input_manifest(training_input_manifest)
        split_contract = load_split_manifest(training_split_manifest)
    except (ManifestError, OSError) as exc:
        raise ProvenanceError(f"invalid Gnomix training manifest: {exc}") from exc
    return _training_manifest_fields_from_contracts(input_contract, split_contract)


def _git(checkout: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ProvenanceError("git is required to verify the Gnomix checkout") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ProvenanceError(
            f"unable to inspect Gnomix checkout {checkout}: {detail or 'git failed'}"
        )
    return result.stdout.strip()


def verify_checkout(checkout: Path, expected_commit: str) -> str:
    """Verify an exact, clean Gnomix repository checkout."""
    expected = normalize_commit(expected_commit)
    if not checkout.is_dir():
        raise ProvenanceError(f"Gnomix checkout directory does not exist: {checkout}")

    checkout_root = checkout.resolve()
    top_level = Path(_git(checkout, "rev-parse", "--show-toplevel")).resolve()
    if top_level != checkout_root:
        raise ProvenanceError(
            f"GNOMIX_DIR_INSTALL must be the Gnomix repository root: "
            f"expected {checkout_root}, git reported {top_level}"
        )

    try:
        origin_url = _git(checkout, "config", "--get", "remote.origin.url")
    except ProvenanceError as exc:
        raise ProvenanceError(
            f"Gnomix checkout must configure origin as {GNOMIX_REPOSITORY}"
        ) from exc
    if not _OFFICIAL_REMOTE_RE.fullmatch(origin_url):
        raise ProvenanceError(
            f"Gnomix origin mismatch: expected {GNOMIX_REPOSITORY}, found {origin_url}"
        )

    actual = _git(checkout, "rev-parse", "--verify", "HEAD^{commit}").lower()
    if actual != expected:
        raise ProvenanceError(
            f"Gnomix checkout commit mismatch: expected {expected}, found {actual}"
        )
    containing_remote_refs = _git(
        checkout,
        "for-each-ref",
        f"--contains={expected}",
        "--format=%(refname)",
        "refs/remotes/origin/",
    )
    if not containing_remote_refs:
        raise ProvenanceError(
            f"Gnomix commit {expected} is not contained in a fetched origin/* ref; "
            "fetch the official origin before training"
        )

    status = _git(checkout, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        first_change = status.splitlines()[0]
        raise ProvenanceError(
            f"Gnomix checkout is dirty; commit provenance would be false ({first_change})"
        )
    return actual


def _require_sha256(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ProvenanceError(f"invalid or missing {field} in Gnomix provenance")
    return value


def _normalize_sha256(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ProvenanceError(f"{label} must be a lowercase SHA-256")
    return value


def _validate_manifest_identity(
    value: object,
    *,
    label: str,
    expected_schema: int,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "sha256"}:
        raise ProvenanceError(f"{label} identity has unexpected fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != expected_schema:
        raise ProvenanceError(f"{label} identity uses an unsupported schema")
    sha256 = value["sha256"]
    if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
        raise ProvenanceError(f"{label} identity has an invalid SHA-256")
    return value


def load_model_record(path: Path) -> dict[str, Any]:
    """Load and validate the schema-only portion of a model record."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise ProvenanceError(f"Gnomix model provenance is missing or empty: {path}") from exc
    except OSError as exc:
        raise ProvenanceError(f"cannot read Gnomix model provenance {path}: {exc}") from exc
    if not raw:
        raise ProvenanceError(f"Gnomix model provenance is missing or empty: {path}")
    data = _load_strict_json(raw, label=f"Gnomix model provenance {path}")
    if not isinstance(data, dict):
        raise ProvenanceError(f"Gnomix model provenance must be a JSON object: {path}")
    schema_version = data.get("schema_version")
    if type(schema_version) is not int or schema_version != MODEL_RECORD_SCHEMA_VERSION:
        raise ProvenanceError(
            f"unsupported Gnomix model provenance schema in {path}: {schema_version!r}"
        )
    if set(data) != _MODEL_RECORD_KEYS:
        missing = sorted(_MODEL_RECORD_KEYS - set(data))
        extra = sorted(set(data) - _MODEL_RECORD_KEYS)
        raise ProvenanceError(
            f"unexpected Gnomix model provenance fields in {path}: "
            f"missing={missing}, extra={extra}"
        )
    if data.get("gnomix_repository") != GNOMIX_REPOSITORY:
        raise ProvenanceError(f"unexpected Gnomix repository identity in {path}")
    if data.get("gnomix_checkout_clean") is not True:
        raise ProvenanceError(f"Gnomix provenance does not attest a clean checkout: {path}")
    if data.get("simulation_run") is not True:
        raise ProvenanceError(f"Gnomix provenance does not attest fresh simulation: {path}")
    chromosome = data.get("chromosome")
    canonical_chromosome = normalize_chromosome(str(chromosome or ""))
    if chromosome != canonical_chromosome:
        raise ProvenanceError(f"non-canonical chromosome in Gnomix provenance: {path}")
    commit = data.get("gnomix_git_commit")
    canonical_commit = normalize_commit(str(commit or ""))
    if commit != canonical_commit:
        raise ProvenanceError(f"non-canonical Gnomix commit in provenance: {path}")
    model_filename = data.get("model_filename")
    if (
        not isinstance(model_filename, str)
        or not model_filename
        or Path(model_filename).name != model_filename
        or "\\" in model_filename
    ):
        raise ProvenanceError(f"invalid model_filename in Gnomix provenance: {path}")
    if model_filename != f"model_chm_{canonical_chromosome}.pkl":
        raise ProvenanceError(
            f"model_filename does not match {canonical_chromosome} in provenance: {path}"
        )
    for field in (
        "effective_config_sha256",
        "genetic_map_sha256",
        "model_sha256",
    ):
        _require_sha256(data, field)
    _validate_manifest_identity(
        data["training_input_manifest"],
        label="training-input manifest",
        expected_schema=INPUT_SCHEMA_VERSION,
    )
    _validate_manifest_identity(
        data["training_split_manifest"],
        label="training-split manifest",
        expected_schema=SPLIT_SCHEMA_VERSION,
    )
    return data


def verify_model_record(
    record_path: Path,
    *,
    chromosome: str,
    expected_commit: str,
    genetic_map: Path,
    model: Path,
    training_input_manifest: Path,
    training_split_manifest: Path,
    config: Path | None = None,
) -> dict[str, Any]:
    """Verify a record against the exact model and available build inputs."""
    data = load_model_record(record_path)
    expected_chromosome = normalize_chromosome(chromosome)
    if data["chromosome"] != expected_chromosome:
        raise ProvenanceError(
            f"Gnomix provenance chromosome mismatch in {record_path}: "
            f"expected {expected_chromosome}, found {data['chromosome']}"
        )
    expected = normalize_commit(expected_commit)
    if data["gnomix_git_commit"] != expected:
        raise ProvenanceError(
            f"Gnomix provenance commit mismatch in {record_path}: "
            f"expected {expected}, found {data['gnomix_git_commit']}"
        )
    if data["model_filename"] != model.name:
        raise ProvenanceError(
            f"Gnomix provenance model filename mismatch in {record_path}: "
            f"expected {model.name}, found {data['model_filename']}"
        )

    actual_hashes = {
        "genetic_map_sha256": sha256_file(genetic_map),
        "model_sha256": sha256_file(model),
    }
    if config is not None:
        verify_simulation_config(config)
        actual_hashes["effective_config_sha256"] = sha256_file(config)
    actual_hashes.update(
        _training_manifest_fields(training_input_manifest, training_split_manifest)
    )
    for field, actual in actual_hashes.items():
        if data[field] != actual:
            raise ProvenanceError(
                f"{field} mismatch in {record_path}: expected {data[field]}, found {actual}"
            )
    return data


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def write_model_record(
    output: Path,
    *,
    chromosome: str,
    expected_commit: str,
    config: Path,
    genetic_map: Path,
    model: Path,
    training_input_manifest: Path,
    training_split_manifest: Path,
    expected_training_input_sha256: str,
    expected_training_split_sha256: str,
) -> dict[str, Any]:
    """Atomically publish the authoritative completion record for one model."""
    verify_simulation_config(config)
    manifest_fields = _training_manifest_fields(
        training_input_manifest,
        training_split_manifest,
    )
    expected_input = _normalize_sha256(
        expected_training_input_sha256,
        label="expected training-input manifest identity",
    )
    expected_split = _normalize_sha256(
        expected_training_split_sha256,
        label="expected training-split manifest identity",
    )
    if manifest_fields["training_input_manifest"]["sha256"] != expected_input:
        raise ProvenanceError(
            "training-input manifest changed after its post-training verification"
        )
    if manifest_fields["training_split_manifest"]["sha256"] != expected_split:
        raise ProvenanceError(
            "training-split manifest changed after its post-training verification"
        )
    data: dict[str, Any] = {
        "schema_version": MODEL_RECORD_SCHEMA_VERSION,
        "chromosome": normalize_chromosome(chromosome),
        "gnomix_repository": GNOMIX_REPOSITORY,
        "gnomix_git_commit": normalize_commit(expected_commit),
        "gnomix_checkout_clean": True,
        "simulation_run": True,
        "effective_config_sha256": sha256_file(config),
        "genetic_map_sha256": sha256_file(genetic_map),
        "model_filename": model.name,
        "model_sha256": sha256_file(model),
        **manifest_fields,
    }
    _write_json_atomic(output, data)
    return data


def aggregate_records(
    record_paths: list[Path],
    expected_commit: str,
    output: Path,
    *,
    training_input_manifest: Path,
    training_split_manifest: Path,
) -> dict[str, Any]:
    """Reject mixed generations and publish one bundle-level manifest."""
    if not record_paths:
        raise ProvenanceError("at least one Gnomix model provenance record is required")
    expected = normalize_commit(expected_commit)
    by_chromosome: dict[str, dict[str, Any]] = {}
    config_hashes: set[str] = set()
    input_identities: set[tuple[int, str]] = set()
    split_identities: set[tuple[int, str]] = set()
    for path in record_paths:
        record = load_model_record(path)
        chromosome = record["chromosome"]
        if chromosome in by_chromosome:
            raise ProvenanceError(f"duplicate Gnomix provenance for {chromosome}")
        if record["gnomix_git_commit"] != expected:
            raise ProvenanceError(
                f"mixed or unexpected Gnomix commits: {path} records "
                f"{record['gnomix_git_commit']}, expected {expected}"
            )
        by_chromosome[chromosome] = record
        config_hashes.add(record["effective_config_sha256"])
        input_identities.add(
            (
                record["training_input_manifest"]["schema_version"],
                record["training_input_manifest"]["sha256"],
            )
        )
        split_identities.add(
            (
                record["training_split_manifest"]["schema_version"],
                record["training_split_manifest"]["sha256"],
            )
        )
    if len(config_hashes) != 1:
        raise ProvenanceError(
            "mixed effective Gnomix configuration hashes across chromosome models"
        )
    if len(input_identities) != 1:
        raise ProvenanceError(
            "mixed Gnomix training-input manifest identities across chromosome models"
        )
    if len(split_identities) != 1:
        raise ProvenanceError(
            "mixed Gnomix training-split manifest identities across chromosome models"
        )
    supplied_identities = _training_manifest_fields(
        training_input_manifest,
        training_split_manifest,
    )
    input_schema, input_sha256 = next(iter(input_identities))
    split_schema, split_sha256 = next(iter(split_identities))
    if supplied_identities["training_input_manifest"] != {
        "schema_version": input_schema,
        "sha256": input_sha256,
    }:
        raise ProvenanceError(
            "supplied Gnomix training-input manifest does not match model provenance"
        )
    if supplied_identities["training_split_manifest"] != {
        "schema_version": split_schema,
        "sha256": split_sha256,
    }:
        raise ProvenanceError(
            "supplied Gnomix training-split manifest does not match model provenance"
        )

    def chromosome_number(label: str) -> int:
        return int(label.removeprefix("chr"))

    models = []
    for chromosome in sorted(by_chromosome, key=chromosome_number):
        record = by_chromosome[chromosome]
        models.append(
            {
                "chromosome": chromosome,
                "model_filename": record["model_filename"],
                "model_sha256": record["model_sha256"],
                "genetic_map_sha256": record["genetic_map_sha256"],
                "provenance_file": (f"metadata/gnomix_model_{chromosome}.provenance.json"),
            }
        )
    manifest: dict[str, Any] = {
        "schema_version": AGGREGATE_SCHEMA_VERSION,
        "gnomix_repository": GNOMIX_REPOSITORY,
        "gnomix_git_commit": expected,
        "gnomix_checkout_clean": True,
        "simulation_run": True,
        "effective_config_sha256": next(iter(config_hashes)),
        "training_input_manifest": {
            "schema_version": input_schema,
            "sha256": input_sha256,
        },
        "training_split_manifest": {
            "schema_version": split_schema,
            "sha256": split_sha256,
        },
        "models": models,
    }
    _validate_aggregate_payload(manifest, require_complete=False)
    _write_json_atomic(output, manifest)
    return manifest


def _validate_aggregate_identity(
    value: object,
    *,
    label: str,
    expected_schema: int,
) -> dict[str, Any]:
    return _validate_manifest_identity(
        value,
        label=label,
        expected_schema=expected_schema,
    )


def _validate_aggregate_payload(
    data: object,
    *,
    require_complete: bool,
) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != _AGGREGATE_KEYS:
        raise ProvenanceError("Gnomix aggregate provenance has unexpected fields")
    if (
        type(data["schema_version"]) is not int
        or data["schema_version"] != AGGREGATE_SCHEMA_VERSION
    ):
        raise ProvenanceError("unsupported Gnomix aggregate provenance schema")
    if data["gnomix_repository"] != GNOMIX_REPOSITORY:
        raise ProvenanceError("unexpected Gnomix repository in aggregate provenance")
    if data["gnomix_checkout_clean"] is not True:
        raise ProvenanceError("aggregate provenance does not attest a clean checkout")
    if data["simulation_run"] is not True:
        raise ProvenanceError("aggregate provenance does not attest fresh simulation")
    commit = data["gnomix_git_commit"]
    if not isinstance(commit, str) or commit != normalize_commit(commit):
        raise ProvenanceError("aggregate provenance has a non-canonical Gnomix commit")
    _require_sha256(data, "effective_config_sha256")
    _validate_aggregate_identity(
        data["training_input_manifest"],
        label="training-input manifest",
        expected_schema=INPUT_SCHEMA_VERSION,
    )
    _validate_aggregate_identity(
        data["training_split_manifest"],
        label="training-split manifest",
        expected_schema=SPLIT_SCHEMA_VERSION,
    )

    models = data["models"]
    if not isinstance(models, list) or not models:
        raise ProvenanceError("Gnomix aggregate provenance must contain model entries")
    observed: list[str] = []
    for entry in models:
        if not isinstance(entry, dict) or set(entry) != _AGGREGATE_MODEL_KEYS:
            raise ProvenanceError("Gnomix aggregate model entry has unexpected fields")
        chromosome = normalize_chromosome(str(entry["chromosome"]))
        if entry["chromosome"] != chromosome or chromosome in observed:
            raise ProvenanceError("Gnomix aggregate has duplicate/non-canonical chromosomes")
        observed.append(chromosome)
        if entry["model_filename"] != f"model_chm_{chromosome}.pkl":
            raise ProvenanceError(f"unexpected model filename for {chromosome}")
        expected_record = f"metadata/gnomix_model_{chromosome}.provenance.json"
        if entry["provenance_file"] != expected_record:
            raise ProvenanceError(f"unexpected provenance path for {chromosome}")
        for field in ("model_sha256", "genetic_map_sha256"):
            value = entry[field]
            if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
                raise ProvenanceError(f"invalid {field} for {chromosome}")
    expected_order = sorted(observed, key=lambda value: int(value.removeprefix("chr")))
    if observed != expected_order:
        raise ProvenanceError("Gnomix aggregate model entries are not in chromosome order")
    if require_complete and tuple(observed) != _AUTOSOMES:
        raise ProvenanceError("Gnomix aggregate must contain exactly chr1 through chr22")
    return data


def load_aggregate_manifest_snapshot(
    path: Path,
    *,
    require_complete: bool = False,
) -> tuple[dict[str, Any], str]:
    """Read an aggregate once and return its validated payload and raw SHA-256."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ProvenanceError(f"cannot read Gnomix aggregate provenance {path}: {exc}") from exc
    if not raw:
        raise ProvenanceError(f"Gnomix aggregate provenance is missing or empty: {path}")
    data = _load_strict_json(raw, label=f"Gnomix aggregate provenance {path}")
    validated = _validate_aggregate_payload(data, require_complete=require_complete)
    return validated, hashlib.sha256(raw).hexdigest()


def load_aggregate_manifest(path: Path, *, require_complete: bool = False) -> dict[str, Any]:
    """Load and validate a bundle-level provenance manifest."""
    return load_aggregate_manifest_snapshot(path, require_complete=require_complete)[0]


def verify_aggregate_snapshot(
    path: Path,
    *,
    training_input_manifest: Path,
    training_split_manifest: Path,
    require_complete: bool = False,
) -> tuple[dict[str, Any], str, dict[str, object]]:
    """Authenticate one captured aggregate/input/split generation."""
    data, aggregate_sha256 = load_aggregate_manifest_snapshot(
        path,
        require_complete=require_complete,
    )
    try:
        input_contract = load_input_manifest(training_input_manifest)
        split_contract = load_split_manifest(training_split_manifest)
    except (ManifestError, OSError) as exc:
        raise ProvenanceError(f"invalid Gnomix training manifest: {exc}") from exc
    identities = _training_manifest_fields_from_contracts(input_contract, split_contract)
    expected = {
        "training_input_manifest": data["training_input_manifest"],
        "training_split_manifest": data["training_split_manifest"],
    }
    if identities != expected:
        raise ProvenanceError(
            "published Gnomix training manifests do not match aggregate provenance"
        )
    return data, aggregate_sha256, input_contract.payload


def verify_published_model_records(
    bundle_dir: Path,
    aggregate: dict[str, Any],
) -> list[dict[str, Any]]:
    """Require every aggregate model entry to match its published schema-v2 record."""
    _validate_aggregate_payload(aggregate, require_complete=True)
    records: list[dict[str, Any]] = []
    common = {
        "gnomix_repository": aggregate["gnomix_repository"],
        "gnomix_git_commit": aggregate["gnomix_git_commit"],
        "gnomix_checkout_clean": aggregate["gnomix_checkout_clean"],
        "simulation_run": aggregate["simulation_run"],
        "effective_config_sha256": aggregate["effective_config_sha256"],
        "training_input_manifest": aggregate["training_input_manifest"],
        "training_split_manifest": aggregate["training_split_manifest"],
    }
    for model in aggregate["models"]:
        record_path = bundle_dir / model["provenance_file"]
        record = load_model_record(record_path)
        expected = {
            **common,
            "chromosome": model["chromosome"],
            "model_filename": model["model_filename"],
            "model_sha256": model["model_sha256"],
            "genetic_map_sha256": model["genetic_map_sha256"],
        }
        mismatches = sorted(field for field, value in expected.items() if record[field] != value)
        if mismatches:
            raise ProvenanceError(
                f"published Gnomix model record does not match aggregate: "
                f"{record_path}: fields={mismatches}"
            )
        records.append(record)
    return records


def verify_aggregate_manifest(
    path: Path,
    *,
    training_input_manifest: Path,
    training_split_manifest: Path,
    require_complete: bool = False,
) -> dict[str, Any]:
    """Authenticate the published manifests against aggregate provenance."""
    return verify_aggregate_snapshot(
        path,
        training_input_manifest=training_input_manifest,
        training_split_manifest=training_split_manifest,
        require_complete=require_complete,
    )[0]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    checkout = commands.add_parser("verify-checkout")
    checkout.add_argument("--gnomix-dir", required=True, type=Path)
    checkout.add_argument("--expected-commit", required=True)

    config = commands.add_parser("verify-config")
    config.add_argument("--config", required=True, type=Path)
    config.add_argument("--training-input-manifest", required=True, type=Path)

    write = commands.add_parser("write-record")
    write.add_argument("--output", required=True, type=Path)
    write.add_argument("--chromosome", required=True)
    write.add_argument("--expected-commit", required=True)
    write.add_argument("--gnomix-dir", required=True, type=Path)
    write.add_argument("--config", required=True, type=Path)
    write.add_argument("--genetic-map", required=True, type=Path)
    write.add_argument("--model", required=True, type=Path)
    write.add_argument("--training-input-manifest", required=True, type=Path)
    write.add_argument("--training-split-manifest", required=True, type=Path)
    write.add_argument("--expected-training-input-sha256", required=True)
    write.add_argument("--expected-training-split-sha256", required=True)

    verify = commands.add_parser("verify-record")
    verify.add_argument("--record", required=True, type=Path)
    verify.add_argument("--chromosome", required=True)
    verify.add_argument("--expected-commit", required=True)
    verify.add_argument("--config", type=Path)
    verify.add_argument("--genetic-map", required=True, type=Path)
    verify.add_argument("--model", required=True, type=Path)
    verify.add_argument("--training-input-manifest", required=True, type=Path)
    verify.add_argument("--training-split-manifest", required=True, type=Path)

    aggregate = commands.add_parser("aggregate")
    aggregate.add_argument("--record", action="append", required=True, type=Path)
    aggregate.add_argument("--expected-commit", required=True)
    aggregate.add_argument("--output", required=True, type=Path)
    aggregate.add_argument("--training-input-manifest", required=True, type=Path)
    aggregate.add_argument("--training-split-manifest", required=True, type=Path)

    verify_aggregate = commands.add_parser("verify-aggregate")
    verify_aggregate.add_argument("--manifest", required=True, type=Path)
    verify_aggregate.add_argument("--training-input-manifest", required=True, type=Path)
    verify_aggregate.add_argument("--training-split-manifest", required=True, type=Path)
    verify_aggregate.add_argument("--require-complete", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        if args.command == "verify-checkout":
            print(verify_checkout(args.gnomix_dir, args.expected_commit))
        elif args.command == "verify-config":
            verify_simulation_config(args.config)
            verify_training_input_viability(args.training_input_manifest)
        elif args.command == "write-record":
            verify_checkout(args.gnomix_dir, args.expected_commit)
            write_model_record(
                args.output,
                chromosome=args.chromosome,
                expected_commit=args.expected_commit,
                config=args.config,
                genetic_map=args.genetic_map,
                model=args.model,
                training_input_manifest=args.training_input_manifest,
                training_split_manifest=args.training_split_manifest,
                expected_training_input_sha256=args.expected_training_input_sha256,
                expected_training_split_sha256=args.expected_training_split_sha256,
            )
        elif args.command == "verify-record":
            verify_model_record(
                args.record,
                chromosome=args.chromosome,
                expected_commit=args.expected_commit,
                config=args.config,
                genetic_map=args.genetic_map,
                model=args.model,
                training_input_manifest=args.training_input_manifest,
                training_split_manifest=args.training_split_manifest,
            )
        elif args.command == "aggregate":
            aggregate_records(
                args.record,
                args.expected_commit,
                args.output,
                training_input_manifest=args.training_input_manifest,
                training_split_manifest=args.training_split_manifest,
            )
        elif args.command == "verify-aggregate":
            verify_aggregate_manifest(
                args.manifest,
                training_input_manifest=args.training_input_manifest,
                training_split_manifest=args.training_split_manifest,
                require_complete=args.require_complete,
            )
    except (OSError, ProvenanceError) as exc:
        print(f"Gnomix provenance error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
