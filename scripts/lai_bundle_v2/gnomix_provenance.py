#!/usr/bin/env python3
"""Create and verify immutable provenance for Gnomix-trained models."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GNOMIX_REPOSITORY = "https://github.com/AI-sandbox/gnomix"
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
    "effective_config_sha256",
    "genetic_map_sha256",
    "model_filename",
    "model_sha256",
}


class ProvenanceError(ValueError):
    """Raised when Gnomix provenance is missing, malformed, or stale."""


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


def load_model_record(path: Path) -> dict[str, Any]:
    """Load and validate the schema-only portion of a model record."""
    if not path.is_file() or path.stat().st_size == 0:
        raise ProvenanceError(f"Gnomix model provenance is missing or empty: {path}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ProvenanceError(f"invalid Gnomix model provenance JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProvenanceError(f"Gnomix model provenance must be a JSON object: {path}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ProvenanceError(
            f"unsupported Gnomix model provenance schema in {path}: {data.get('schema_version')!r}"
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
    return data


def verify_model_record(
    record_path: Path,
    *,
    chromosome: str,
    expected_commit: str,
    genetic_map: Path,
    model: Path,
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
        actual_hashes["effective_config_sha256"] = sha256_file(config)
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
) -> dict[str, Any]:
    """Atomically publish the authoritative completion record for one model."""
    data: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "chromosome": normalize_chromosome(chromosome),
        "gnomix_repository": GNOMIX_REPOSITORY,
        "gnomix_git_commit": normalize_commit(expected_commit),
        "gnomix_checkout_clean": True,
        "effective_config_sha256": sha256_file(config),
        "genetic_map_sha256": sha256_file(genetic_map),
        "model_filename": model.name,
        "model_sha256": sha256_file(model),
    }
    _write_json_atomic(output, data)
    return data


def aggregate_records(
    record_paths: list[Path], expected_commit: str, output: Path
) -> dict[str, Any]:
    """Reject mixed generations and publish one bundle-level manifest."""
    if not record_paths:
        raise ProvenanceError("at least one Gnomix model provenance record is required")
    expected = normalize_commit(expected_commit)
    by_chromosome: dict[str, dict[str, Any]] = {}
    config_hashes: set[str] = set()
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
    if len(config_hashes) != 1:
        raise ProvenanceError(
            "mixed effective Gnomix configuration hashes across chromosome models"
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
        "schema_version": SCHEMA_VERSION,
        "gnomix_repository": GNOMIX_REPOSITORY,
        "gnomix_git_commit": expected,
        "gnomix_checkout_clean": True,
        "effective_config_sha256": next(iter(config_hashes)),
        "models": models,
    }
    _write_json_atomic(output, manifest)
    return manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    checkout = commands.add_parser("verify-checkout")
    checkout.add_argument("--gnomix-dir", required=True, type=Path)
    checkout.add_argument("--expected-commit", required=True)

    write = commands.add_parser("write-record")
    write.add_argument("--output", required=True, type=Path)
    write.add_argument("--chromosome", required=True)
    write.add_argument("--expected-commit", required=True)
    write.add_argument("--gnomix-dir", required=True, type=Path)
    write.add_argument("--config", required=True, type=Path)
    write.add_argument("--genetic-map", required=True, type=Path)
    write.add_argument("--model", required=True, type=Path)

    verify = commands.add_parser("verify-record")
    verify.add_argument("--record", required=True, type=Path)
    verify.add_argument("--chromosome", required=True)
    verify.add_argument("--expected-commit", required=True)
    verify.add_argument("--config", type=Path)
    verify.add_argument("--genetic-map", required=True, type=Path)
    verify.add_argument("--model", required=True, type=Path)

    aggregate = commands.add_parser("aggregate")
    aggregate.add_argument("--record", action="append", required=True, type=Path)
    aggregate.add_argument("--expected-commit", required=True)
    aggregate.add_argument("--output", required=True, type=Path)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        if args.command == "verify-checkout":
            print(verify_checkout(args.gnomix_dir, args.expected_commit))
        elif args.command == "write-record":
            verify_checkout(args.gnomix_dir, args.expected_commit)
            write_model_record(
                args.output,
                chromosome=args.chromosome,
                expected_commit=args.expected_commit,
                config=args.config,
                genetic_map=args.genetic_map,
                model=args.model,
            )
        elif args.command == "verify-record":
            verify_model_record(
                args.record,
                chromosome=args.chromosome,
                expected_commit=args.expected_commit,
                config=args.config,
                genetic_map=args.genetic_map,
                model=args.model,
            )
        elif args.command == "aggregate":
            aggregate_records(args.record, args.expected_commit, args.output)
    except (OSError, ProvenanceError) as exc:
        print(f"Gnomix provenance error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
