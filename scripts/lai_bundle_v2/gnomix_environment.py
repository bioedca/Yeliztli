#!/usr/bin/env python3
"""Identify and verify the immutable Conda environment used to train Gnomix."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Hashable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

ENVIRONMENT_SCHEMA_VERSION = 1
DEFAULT_PLATFORM = "linux-64"
ARTIFACT_KIND = "conda-lock"

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_MAX_LOCK_BYTES = 64 << 20
_AMBIENT_INJECTION_VARIABLES = frozenset(
    {
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONINSPECT",
        "PYTHONUSERBASE",
        "PYTHONSAFEPATH",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
    }
)


class EnvironmentVerificationError(ValueError):
    """Raised when a training-environment lock or runtime environment is invalid."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Construct safe YAML values without silently replacing duplicate keys."""

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


@dataclass(frozen=True)
class _LockedPackage:
    name: str
    version: str
    build: str
    url: str
    sha256: str


@dataclass(frozen=True)
class _LockSnapshot:
    raw: bytes
    sha256: str
    platform: str
    packages: tuple[_LockedPackage, ...]

    @property
    def identity(self) -> dict[str, object]:
        return {
            "schema_version": ENVIRONMENT_SCHEMA_VERSION,
            "artifact_kind": ARTIFACT_KIND,
            "platform": self.platform,
            "lock_sha256": self.sha256,
        }


def _read_lock_bytes(lock_path: Path) -> bytes:
    try:
        raw = lock_path.read_bytes()
    except OSError as exc:
        raise EnvironmentVerificationError(
            f"unable to read Gnomix training environment lock {lock_path}: {exc}"
        ) from exc
    if not raw:
        raise EnvironmentVerificationError(
            f"Gnomix training environment lock is missing or empty: {lock_path}"
        )
    if len(raw) > _MAX_LOCK_BYTES:
        raise EnvironmentVerificationError(
            f"Gnomix training environment lock is unexpectedly large: {lock_path}"
        )
    return raw


def _normalize_expected_sha256(expected: str | None) -> str | None:
    if expected is None:
        return None
    if not _SHA256_RE.fullmatch(expected):
        raise EnvironmentVerificationError(
            "expected lock SHA256 must be exactly 64 lowercase hexadecimal characters"
        )
    return expected


def _require_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise EnvironmentVerificationError(f"{label} must be a non-empty, trimmed string")
    return value


def _archive_build(url: str, *, name: str, version: str, label: str) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise EnvironmentVerificationError(f"{label} has a malformed URL: {url!r}") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port is not None
        and not 1 <= port <= 65535
    ):
        raise EnvironmentVerificationError(
            f"{label} URL must be an immutable HTTPS package URL without credentials, "
            f"query, or fragment: {url!r}"
        )

    filename = PurePosixPath(unquote(parsed.path)).name
    suffix = next(
        (candidate for candidate in (".tar.bz2", ".conda") if filename.endswith(candidate)),
        None,
    )
    prefix = f"{name}-{version}-"
    if suffix is None or not filename.startswith(prefix):
        raise EnvironmentVerificationError(
            f"{label} URL does not identify the locked package name and version: {url!r}"
        )
    build = filename[len(prefix) : -len(suffix)]
    if not build or build != build.strip():
        raise EnvironmentVerificationError(f"{label} has a missing package build in its URL")
    return build


def _parse_lock(raw: bytes, *, platform: str) -> _LockSnapshot:
    if platform != DEFAULT_PLATFORM:
        raise EnvironmentVerificationError(
            f"unsupported Gnomix training environment platform {platform!r}; "
            f"expected {DEFAULT_PLATFORM!r}"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EnvironmentVerificationError(
            f"Gnomix training environment lock must be UTF-8: {exc}"
        ) from exc
    if text.startswith("\ufeff"):
        raise EnvironmentVerificationError(
            "Gnomix training environment lock must not contain a UTF-8 BOM"
        )
    try:
        document = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise EnvironmentVerificationError(
            f"invalid Gnomix training environment lock YAML: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise EnvironmentVerificationError("training environment lock root must be a mapping")
    if set(document) != {"version", "metadata", "package"}:
        raise EnvironmentVerificationError(
            "training environment lock must contain exactly version, metadata, and package"
        )
    if type(document["version"]) is not int or document["version"] != 1:
        raise EnvironmentVerificationError(
            f"unsupported training environment lock schema version: {document['version']!r}"
        )

    metadata = document["metadata"]
    if not isinstance(metadata, dict):
        raise EnvironmentVerificationError("training environment lock metadata must be a mapping")
    platforms = metadata.get("platforms")
    if platforms != [DEFAULT_PLATFORM]:
        raise EnvironmentVerificationError(
            "training environment lock must select exactly the supported platform "
            f"{DEFAULT_PLATFORM!r}"
        )

    raw_packages = document["package"]
    if not isinstance(raw_packages, list) or not raw_packages:
        raise EnvironmentVerificationError(
            "training environment lock package inventory must be a non-empty list"
        )

    packages: list[_LockedPackage] = []
    seen_names: set[str] = set()
    seen_urls: set[str] = set()
    for index, record in enumerate(raw_packages):
        label = f"training environment lock package[{index}]"
        if not isinstance(record, dict):
            raise EnvironmentVerificationError(f"{label} must be a mapping")
        name = _require_text(record.get("name"), label=f"{label}.name")
        version = _require_text(record.get("version"), label=f"{label}.version")
        if record.get("manager") != "conda":
            raise EnvironmentVerificationError(
                f"{label} must use manager='conda'; pip/PyPI lock entries are unsupported"
            )
        if record.get("platform") != DEFAULT_PLATFORM:
            raise EnvironmentVerificationError(
                f"{label} must target platform {DEFAULT_PLATFORM!r}"
            )
        url = _require_text(record.get("url"), label=f"{label}.url")
        build = _archive_build(url, name=name, version=version, label=label)
        hashes = record.get("hash")
        sha256 = hashes.get("sha256") if isinstance(hashes, dict) else None
        if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
            raise EnvironmentVerificationError(
                f"{label} must contain a lowercase SHA256 package hash"
            )
        if name in seen_names:
            raise EnvironmentVerificationError(
                f"training environment lock contains duplicate package name {name!r}"
            )
        if url in seen_urls:
            raise EnvironmentVerificationError(
                f"training environment lock contains duplicate package URL {url!r}"
            )
        seen_names.add(name)
        seen_urls.add(url)
        packages.append(
            _LockedPackage(
                name=name,
                version=version,
                build=build,
                url=url,
                sha256=sha256,
            )
        )

    return _LockSnapshot(
        raw=raw,
        sha256=hashlib.sha256(raw).hexdigest(),
        platform=platform,
        packages=tuple(packages),
    )


def _load_snapshot(
    lock_path: Path,
    *,
    platform: str,
    expected_lock_sha256: str | None,
) -> _LockSnapshot:
    expected = _normalize_expected_sha256(expected_lock_sha256)
    snapshot = _parse_lock(_read_lock_bytes(lock_path), platform=platform)
    if expected is not None and snapshot.sha256 != expected:
        raise EnvironmentVerificationError(
            "Gnomix training environment lock digest drift: "
            f"expected {expected}, observed {snapshot.sha256}"
        )
    return snapshot


def load_lock_identity(
    lock_path: Path,
    *,
    platform: str,
    expected_lock_sha256: str | None = None,
) -> dict[str, object]:
    """Validate a conda-lock artifact and return its immutable identity."""
    return _load_snapshot(
        Path(lock_path),
        platform=platform,
        expected_lock_sha256=expected_lock_sha256,
    ).identity


def _run_conda(conda_executable: str, arguments: list[str]) -> str:
    clean_environment = {
        key: value for key, value in os.environ.items() if key not in _AMBIENT_INJECTION_VARIABLES
    }
    try:
        result = subprocess.run(
            [conda_executable, *arguments],
            check=False,
            capture_output=True,
            text=True,
            env=clean_environment,
        )
    except OSError as exc:
        raise EnvironmentVerificationError(
            f"unable to execute Conda environment verification: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise EnvironmentVerificationError(
            f"Conda environment verification command failed ({result.returncode}): {detail}"
        )
    return result.stdout


def _parse_explicit_artifacts(output: str) -> dict[str, str]:
    marker_seen = False
    artifacts: dict[str, str] = {}
    for line_number, raw_line in enumerate(output.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "@EXPLICIT":
            if marker_seen or artifacts:
                raise EnvironmentVerificationError(
                    "Conda explicit inventory contains a misplaced or duplicate @EXPLICIT marker"
                )
            marker_seen = True
            continue
        if not marker_seen:
            raise EnvironmentVerificationError(
                f"Conda explicit inventory line {line_number} precedes @EXPLICIT"
            )
        # The lock-specific name/version/build validation happened during lock parsing.
        # Runtime URLs and package digests are compared byte-for-byte below.
        try:
            parsed = urlsplit(line)
            port = parsed.port
        except ValueError as exc:
            raise EnvironmentVerificationError(
                f"Conda explicit inventory contains malformed URL {line!r}"
            ) from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or not _SHA256_RE.fullmatch(parsed.fragment)
            or port is not None
            and not 1 <= port <= 65535
        ):
            raise EnvironmentVerificationError(
                "Conda explicit inventory must contain immutable HTTPS package URLs "
                f"with lowercase SHA256 fragments: {line!r}"
            )
        url = parsed._replace(fragment="").geturl()
        if url in artifacts:
            raise EnvironmentVerificationError(
                f"Conda explicit inventory contains duplicate package URL {url!r}"
            )
        artifacts[url] = parsed.fragment
    if not marker_seen or not artifacts:
        raise EnvironmentVerificationError(
            "Conda explicit inventory must contain @EXPLICIT and at least one package URL"
        )
    return artifacts


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EnvironmentVerificationError(
                f"Conda JSON inventory contains duplicate key {key!r}"
            )
        result[key] = value
    return result


def _parse_json_inventory(output: str) -> dict[str, tuple[str, str]]:
    try:
        payload = json.loads(output, object_pairs_hook=_unique_json_object)
    except EnvironmentVerificationError:
        raise
    except json.JSONDecodeError as exc:
        raise EnvironmentVerificationError(f"invalid Conda JSON inventory: {exc}") from exc
    if not isinstance(payload, list):
        raise EnvironmentVerificationError("Conda JSON inventory root must be a list")

    inventory: dict[str, tuple[str, str]] = {}
    for index, record in enumerate(payload):
        label = f"Conda JSON inventory package[{index}]"
        if not isinstance(record, dict):
            raise EnvironmentVerificationError(f"{label} must be an object")
        name = _require_text(record.get("name"), label=f"{label}.name")
        channel = record.get("channel")
        package_platform = record.get("platform")
        if (
            isinstance(channel, str)
            and channel.casefold() == "pypi"
            or isinstance(package_platform, str)
            and package_platform.casefold() == "pypi"
        ):
            raise EnvironmentVerificationError(
                f"{label} {name!r} is a pip/PyPI package; training must use only "
                "locked Conda packages"
            )
        if package_platform not in {DEFAULT_PLATFORM, "noarch"}:
            raise EnvironmentVerificationError(
                f"{label} {name!r} has unsupported platform {package_platform!r}"
            )
        version = _require_text(record.get("version"), label=f"{label}.version")
        build_string = record.get("build_string", record.get("build"))
        build = _require_text(build_string, label=f"{label}.build_string")
        if name in inventory:
            raise EnvironmentVerificationError(
                f"Conda JSON inventory contains duplicate package name {name!r}"
            )
        inventory[name] = (version, build)
    return inventory


def _preview(values: set[str]) -> str:
    ordered = sorted(values)
    suffix = "" if len(ordered) <= 5 else f", ... ({len(ordered)} total)"
    return ", ".join(repr(value) for value in ordered[:5]) + suffix


def verify_environment(
    lock_path: Path,
    *,
    platform: str,
    conda_env: str,
    expected_lock_sha256: str | None = None,
    conda_executable: str = "conda",
) -> dict[str, object]:
    """Verify one named Conda environment exactly matches the immutable lock."""
    conda_env = _require_text(conda_env, label="Conda environment name")
    conda_executable = _require_text(conda_executable, label="Conda executable")
    lock_path = Path(lock_path)
    snapshot = _load_snapshot(
        lock_path,
        platform=platform,
        expected_lock_sha256=expected_lock_sha256,
    )

    explicit_output = _run_conda(
        conda_executable,
        ["list", "-n", conda_env, "--explicit", "--sha256"],
    )
    json_output = _run_conda(
        conda_executable,
        ["list", "-n", conda_env, "--json"],
    )

    after_raw = _read_lock_bytes(lock_path)
    after_sha256 = hashlib.sha256(after_raw).hexdigest()
    if after_raw != snapshot.raw or after_sha256 != snapshot.sha256:
        raise EnvironmentVerificationError(
            "Gnomix training environment lock changed during runtime verification; retry "
            f"with a stable lock (before {snapshot.sha256}, after {after_sha256})"
        )

    locked_artifacts = {package.url: package.sha256 for package in snapshot.packages}
    explicit_artifacts = _parse_explicit_artifacts(explicit_output)
    if explicit_artifacts != locked_artifacts:
        locked_urls = set(locked_artifacts)
        explicit_urls = set(explicit_artifacts)
        missing = locked_urls - explicit_urls
        extra = explicit_urls - locked_urls
        digest_drift = {
            url
            for url in locked_urls & explicit_urls
            if explicit_artifacts[url] != locked_artifacts[url]
        }
        details: list[str] = []
        if missing:
            details.append(f"missing {_preview(missing)}")
        if extra:
            details.append(f"extra {_preview(extra)}")
        if digest_drift:
            details.append(f"SHA256 drift {_preview(digest_drift)}")
        raise EnvironmentVerificationError(
            "Conda explicit package inventory drift: " + "; ".join(details)
        )

    runtime_inventory = _parse_json_inventory(json_output)
    locked_inventory = {
        package.name: (package.version, package.build) for package in snapshot.packages
    }
    if runtime_inventory != locked_inventory:
        missing_names = set(locked_inventory) - set(runtime_inventory)
        extra_names = set(runtime_inventory) - set(locked_inventory)
        changed_names = {
            name
            for name in set(runtime_inventory) & set(locked_inventory)
            if runtime_inventory[name] != locked_inventory[name]
        }
        details = []
        if missing_names:
            details.append(f"missing {_preview(missing_names)}")
        if extra_names:
            details.append(f"extra {_preview(extra_names)}")
        if changed_names:
            details.append(f"version/build drift {_preview(changed_names)}")
        raise EnvironmentVerificationError(
            "Conda JSON package inventory drift: " + "; ".join(details)
        )
    return snapshot.identity


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Identify or verify the immutable Gnomix training environment",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lock", required=True, type=Path)
    common.add_argument("--platform", default=DEFAULT_PLATFORM)
    common.add_argument("--expected-lock-sha256")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("identity", parents=[common])
    verify_parser = subparsers.add_parser("verify", parents=[common])
    verify_parser.add_argument("--conda-env", required=True)
    verify_parser.add_argument("--conda-executable", default="conda")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "identity":
            identity = load_lock_identity(
                args.lock,
                platform=args.platform,
                expected_lock_sha256=args.expected_lock_sha256,
            )
        else:
            identity = verify_environment(
                args.lock,
                platform=args.platform,
                conda_env=args.conda_env,
                expected_lock_sha256=args.expected_lock_sha256,
                conda_executable=args.conda_executable,
            )
    except EnvironmentVerificationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(identity, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
