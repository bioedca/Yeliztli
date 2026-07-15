#!/usr/bin/env python3
"""Create and verify immutable Gnomix training-input and split manifests.

The input manifest is written before training and binds the exact reference
panel, selected markers, source metadata, sample labels, and outer
training/production-holdout partition.  The split manifest is written after
Gnomix materializes its internal ``train1``, ``train2``, and ``val`` founder
maps.  Both formats are strict, canonical, path-independent JSON; the raw file
SHA-256 is therefore their stable identity.

This helper is deliberately standard-library-only so it can run in the LAI
build environment without importing Gnomix or duplicating its random split
algorithm.

The rebuild workspace is assumed to be operator-controlled. Inputs and outputs
reject symlinks and aliases and are rechecked for drift, but this is not a
sandbox for paths concurrently manipulated by a hostile local user.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import re
import stat
import tempfile
import unicodedata
import zlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import urlsplit

INPUT_MANIFEST_TYPE = "yeliztli_gnomix_training_inputs"
SPLIT_MANIFEST_TYPE = "yeliztli_gnomix_training_splits"
INPUT_SCHEMA_VERSION = 1
SPLIT_SCHEMA_VERSION = 1
# Schema v1 is publication-safe only because the supported gnomAD HGDP+1KG
# identifiers are already public. Private cohorts require a separately designed
# non-sensitive projection rather than silently reusing this policy.
SAMPLE_IDENTIFIER_POLICY = "public_reference_panel_ids"
AUTOSOMES = tuple(f"chr{number}" for number in range(1, 23))
AUTOSOME_SET = frozenset(AUTOSOMES)
SPLIT_NAMES = ("train1", "train2", "val")

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_LABEL_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_VCF_FIXED_HEADER = (
    "#CHROM",
    "POS",
    "ID",
    "REF",
    "ALT",
    "QUAL",
    "FILTER",
    "INFO",
    "FORMAT",
)
_ALLOWED_SOURCE_SCHEMES = frozenset({"gs", "https", "s3"})
_READ_CHUNK_BYTES = 1024 * 1024
_MAX_MANIFEST_BYTES = 64 * 1024 * 1024
_MAX_TEXT_ARTIFACT_BYTES = 512 * 1024 * 1024
_MAX_VCF_LINE_BYTES = 64 * 1024 * 1024
# These per-chromosome ceilings are deliberately far above the current LAI
# catalog (~840,000 sites across all autosomes) while bounding gzip expansion
# and the marker structures retained in memory.
_MAX_VCF_DECOMPRESSED_BYTES = 16 * 1024 * 1024 * 1024
_MAX_VCF_MARKERS_PER_CHROMOSOME = 2_000_000


class ManifestError(ValueError):
    """Raised when an input cannot be represented or verified safely."""


class LoadedManifest:
    """A validated canonical manifest and its raw-byte identity."""

    __slots__ = ("payload", "sha256")

    def __init__(self, payload: dict[str, object], sha256: str) -> None:
        self.payload = payload
        self.sha256 = sha256


class _FileSnapshot:
    __slots__ = ("path", "sha256", "signature", "size_bytes")

    def __init__(
        self,
        path: Path,
        sha256: str,
        signature: tuple[int, int, int, int, int],
        size_bytes: int,
    ) -> None:
        self.path = path
        self.sha256 = sha256
        self.signature = signature
        self.size_bytes = size_bytes

    def artifact(self) -> dict[str, object]:
        return {
            "filename": self.path.name,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


_CapturedSignature = tuple[Path, tuple[int, int, int, int, int]]


def _signature(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _absolute_without_symlinks(path: Path, *, label: str) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path.expanduser())))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError as exc:
            raise ManifestError(f"{label}: file does not exist: {absolute}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ManifestError(f"{label}: symlink path component is not allowed: {current}")
    return absolute


def _open_regular(path: Path, *, label: str) -> tuple[int, Path, tuple[int, int, int, int, int]]:
    absolute = _absolute_without_symlinks(path, label=label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(absolute, flags)
    except OSError as exc:
        raise ManifestError(f"{label}: cannot open regular file {absolute}: {exc}") from exc
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise ManifestError(f"{label}: expected a regular file: {absolute}")
    if metadata.st_size <= 0:
        os.close(descriptor)
        raise ManifestError(f"{label}: file is empty: {absolute}")
    return descriptor, absolute, _signature(metadata)


def _assert_path_signature(
    path: Path,
    expected: tuple[int, int, int, int, int],
    *,
    label: str,
) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ManifestError(f"{label}: file disappeared while it was read: {path}") from exc
    if _signature(metadata) != expected or not stat.S_ISREG(metadata.st_mode):
        raise ManifestError(f"{label}: file changed while it was read: {path}")


def _capture_signatures(paths: Sequence[Path]) -> list[_CapturedSignature]:
    captured: list[_CapturedSignature] = []
    for index, path in enumerate(paths):
        descriptor, absolute, signature = _open_regular(
            Path(path), label=f"manifest input {index + 1}"
        )
        os.close(descriptor)
        captured.append((absolute, signature))
    return captured


def _assert_captured_signatures(captured: Sequence[_CapturedSignature]) -> None:
    for index, (path, signature) in enumerate(captured):
        _assert_path_signature(path, signature, label=f"manifest input {index + 1}")


def _snapshot_file(path: Path, *, label: str) -> _FileSnapshot:
    descriptor, absolute, before = _open_regular(path, label=label)
    digest = hashlib.sha256()
    try:
        while chunk := os.read(descriptor, _READ_CHUNK_BYTES):
            digest.update(chunk)
        after = _signature(os.fstat(descriptor))
    except OSError as exc:
        raise ManifestError(f"{label}: failed while hashing {absolute}: {exc}") from exc
    finally:
        os.close(descriptor)
    if after != before:
        raise ManifestError(f"{label}: file changed while it was hashed: {absolute}")
    _assert_path_signature(absolute, before, label=label)
    return _FileSnapshot(absolute, digest.hexdigest(), before, before[2])


def _read_snapshot_bytes(
    path: Path,
    *,
    label: str,
    max_bytes: int = _MAX_TEXT_ARTIFACT_BYTES,
) -> tuple[_FileSnapshot, bytes]:
    descriptor, absolute, before = _open_regular(path, label=label)
    if before[2] > max_bytes:
        os.close(descriptor)
        raise ManifestError(f"{label}: file exceeds the {max_bytes}-byte safety limit")
    chunks: list[bytes] = []
    digest = hashlib.sha256()
    total = 0
    try:
        while chunk := os.read(descriptor, _READ_CHUNK_BYTES):
            total += len(chunk)
            if total > max_bytes:
                raise ManifestError(f"{label}: file exceeds the {max_bytes}-byte safety limit")
            chunks.append(chunk)
            digest.update(chunk)
        after = _signature(os.fstat(descriptor))
    except OSError as exc:
        raise ManifestError(f"{label}: failed while reading {absolute}: {exc}") from exc
    finally:
        os.close(descriptor)
    if after != before:
        raise ManifestError(f"{label}: file changed while it was read: {absolute}")
    _assert_path_signature(absolute, before, label=label)
    return (
        _FileSnapshot(absolute, digest.hexdigest(), before, before[2]),
        b"".join(chunks),
    )


def _decode_utf8(data: bytes, *, label: str) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError(f"{label}: expected UTF-8 text: {exc}") from exc
    if text.startswith("\ufeff"):
        raise ManifestError(f"{label}: UTF-8 BOM is not allowed")
    return text


def _canonical_bytes(payload: object) -> bytes:
    try:
        return json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ManifestError(f"manifest cannot be encoded canonically: {exc}") from exc


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
    parent = _absolute_without_symlinks(path.parent, label="manifest output directory")
    if not parent.is_dir():
        raise ManifestError(f"manifest output directory is not a directory: {parent}")
    destination = parent / path.name
    try:
        metadata = destination.lstat()
    except FileNotFoundError:
        metadata = None
    if metadata is not None:
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ManifestError(f"manifest output must be a regular file: {destination}")
    encoded = _canonical_bytes(payload)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _reject_output_aliases(output: Path, inputs: Sequence[Path]) -> None:
    parent = _absolute_without_symlinks(output.parent, label="manifest output directory")
    destination = parent / output.name
    try:
        metadata = destination.lstat()
    except FileNotFoundError:
        metadata = None
    if metadata is not None:
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ManifestError(f"manifest output must be a regular file: {destination}")
    for raw_input in inputs:
        input_path = _absolute_without_symlinks(Path(raw_input), label="manifest input")
        aliases = destination == input_path
        if metadata is not None and not aliases:
            try:
                aliases = os.path.samefile(destination, input_path)
            except OSError:
                aliases = False
        if aliases:
            raise ManifestError(f"manifest output aliases required input: {input_path}")


def _pairs_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError(f"manifest JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def _load_canonical(path: Path, *, manifest_type: str) -> LoadedManifest:
    _snapshot, raw = _read_snapshot_bytes(
        path,
        label=f"{manifest_type} manifest",
        max_bytes=_MAX_MANIFEST_BYTES,
    )
    try:
        payload = json.loads(
            _decode_utf8(raw, label=f"{manifest_type} manifest"),
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ManifestError(f"manifest JSON contains non-finite value {value}")
            ),
        )
    except ManifestError:
        raise
    except json.JSONDecodeError as exc:
        raise ManifestError(f"invalid manifest JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ManifestError("manifest root must be an object")
    if payload.get("manifest_type") != manifest_type:
        raise ManifestError(f"unexpected manifest_type; expected {manifest_type!r}")
    if _canonical_bytes(payload) != raw:
        raise ManifestError("manifest JSON is not in canonical byte representation")
    return LoadedManifest(payload, hashlib.sha256(raw).hexdigest())


def _exact_keys(value: object, expected: set[str], *, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ManifestError(f"{label}: unexpected or missing fields")
    return value


def _positive_int(value: object, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ManifestError(f"{label}: expected a positive integer")
    return value


def _safe_token(value: object, *, label: str, max_length: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ManifestError(f"{label}: expected a non-empty string")
    if value != value.strip() or value != unicodedata.normalize("NFC", value):
        raise ManifestError(f"{label}: whitespace or non-canonical Unicode is not allowed")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ManifestError(f"{label}: control characters are not allowed")
    return value


def _safe_filename(value: object, *, label: str) -> str:
    filename = _safe_token(value, label=label, max_length=255)
    if filename in {".", ".."} or Path(filename).name != filename or "/" in filename:
        raise ManifestError(f"{label}: expected a logical filename without directories")
    return filename


def _safe_source_uri(value: object, *, label: str) -> str:
    uri = _safe_token(value, label=label, max_length=4096)
    try:
        parsed = urlsplit(uri)
        _ = parsed.port
    except ValueError as exc:
        raise ManifestError(f"{label}: invalid source URI: {exc}") from exc
    if parsed.scheme not in _ALLOWED_SOURCE_SCHEMES or not parsed.netloc:
        raise ManifestError(f"{label}: source URI must use public https, gs, or s3")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ManifestError(f"{label}: credentials, query strings, and fragments are forbidden")
    return uri


def _normalize_chromosome(value: str) -> str:
    token = value[3:] if value.lower().startswith("chr") else value
    if not token.isdigit() or not 1 <= int(token) <= 22:
        raise ManifestError(f"invalid autosome {value!r}")
    return f"chr{int(token)}"


def _normalized_chromosome_files(
    chromosome_files: Mapping[str, tuple[Path, Path]],
) -> dict[str, tuple[Path, Path]]:
    if not chromosome_files:
        raise ManifestError("chromosome_files must not be empty")
    normalized: dict[str, tuple[Path, Path]] = {}
    for raw_chromosome, raw_paths in chromosome_files.items():
        chromosome = _normalize_chromosome(raw_chromosome)
        if chromosome in normalized:
            raise ManifestError(f"duplicate chromosome mapping for {chromosome}")
        if not isinstance(raw_paths, tuple) or len(raw_paths) != 2:
            raise ManifestError(f"{chromosome}: expected (VCF, index) paths")
        normalized[chromosome] = (Path(raw_paths[0]), Path(raw_paths[1]))
    return normalized


def _parse_tsv(data: bytes, *, label: str) -> list[list[str]]:
    text = _decode_utf8(data, label=label)
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter="\t", strict=True))
    except csv.Error as exc:
        raise ManifestError(f"{label}: invalid TSV: {exc}") from exc
    if not rows or any(not row or all(value == "" for value in row) for row in rows):
        raise ManifestError(f"{label}: empty rows are not allowed")
    return rows


def _parse_selected_samples(data: bytes) -> tuple[list[str], dict[str, tuple[str, str]]]:
    rows = _parse_tsv(data, label="selected samples")
    header = rows[0]
    if len(header) != len(set(header)):
        raise ManifestError("selected samples: duplicate header field")
    required = ("IID", "population", "genetic_region")
    missing = [field for field in required if field not in header]
    if missing:
        raise ManifestError(f"selected samples: missing required field(s): {', '.join(missing)}")
    indices = {field: header.index(field) for field in required}
    order: list[str] = []
    selected: dict[str, tuple[str, str]] = {}
    for line_number, row in enumerate(rows[1:], start=2):
        if len(row) != len(header):
            raise ManifestError(f"selected samples:{line_number}: wrong column count")
        iid = _safe_token(row[indices["IID"]], label=f"selected samples:{line_number}: IID")
        population = _safe_token(
            row[indices["population"]], label=f"selected samples:{line_number}: population"
        )
        superpopulation = _safe_token(
            row[indices["genetic_region"]],
            label=f"selected samples:{line_number}: genetic_region",
        )
        if iid in selected:
            raise ManifestError(f"selected samples:{line_number}: duplicate IID {iid!r}")
        selected[iid] = (population, superpopulation)
        order.append(iid)
    if not selected:
        raise ManifestError("selected samples: no data rows")
    return order, selected


def _parse_reference_metadata(data: bytes) -> dict[str, tuple[str, str]]:
    rows = _parse_tsv(data, label="reference metadata")
    header = rows[0]
    if len(header) != len(set(header)):
        raise ManifestError("reference metadata: duplicate header field")
    required = (
        "s",
        "hgdp_tgp_meta.Population",
        "hgdp_tgp_meta.Genetic.region",
    )
    missing = [field for field in required if field not in header]
    if missing:
        raise ManifestError(f"reference metadata: missing required field(s): {', '.join(missing)}")
    indices = {field: header.index(field) for field in required}
    metadata: dict[str, tuple[str, str]] = {}
    for line_number, row in enumerate(rows[1:], start=2):
        if len(row) != len(header):
            raise ManifestError(f"reference metadata:{line_number}: wrong column count")
        iid = _safe_token(row[indices["s"]], label=f"reference metadata:{line_number}: s")
        population = _safe_token(
            row[indices["hgdp_tgp_meta.Population"]],
            label=f"reference metadata:{line_number}: population",
        )
        superpopulation = _safe_token(
            row[indices["hgdp_tgp_meta.Genetic.region"]],
            label=f"reference metadata:{line_number}: genetic region",
        )
        if iid in metadata:
            raise ManifestError(f"reference metadata:{line_number}: duplicate sample {iid!r}")
        metadata[iid] = (population, superpopulation)
    if not metadata:
        raise ManifestError("reference metadata: no data rows")
    return metadata


def _parse_lifted_regions(data: bytes) -> frozenset[tuple[str, int]]:
    rows = _parse_tsv(data, label="lifted marker regions")
    coordinates: set[tuple[str, int]] = set()
    for line_number, row in enumerate(rows, start=1):
        if len(row) != 3:
            raise ManifestError(
                f"lifted marker regions:{line_number}: expected chromosome, begin, end"
            )
        chromosome = _normalize_chromosome(row[0])
        try:
            begin, end = int(row[1]), int(row[2])
        except ValueError as exc:
            raise ManifestError(
                f"lifted marker regions:{line_number}: positions must be integers"
            ) from exc
        if begin <= 0 or begin != end:
            raise ManifestError(
                f"lifted marker regions:{line_number}: expected one positive 1-based site"
            )
        coordinates.add((chromosome, begin))
    if not coordinates:
        raise ManifestError("lifted marker regions: no marker coordinates")
    return frozenset(coordinates)


def _parse_sample_map(
    data: bytes,
    *,
    label: str,
    header: tuple[str, str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    rows = _parse_tsv(data, label=label)
    if header is not None:
        if tuple(rows[0]) != header:
            raise ManifestError(f"{label}: expected exact header {header!r}")
        rows = rows[1:]
    if not rows:
        raise ManifestError(f"{label}: no data rows")
    order: list[str] = []
    mapping: dict[str, str] = {}
    for line_number, row in enumerate(rows, start=2 if header is not None else 1):
        if len(row) != 2:
            raise ManifestError(f"{label}:{line_number}: expected exactly two columns")
        iid = _safe_token(row[0], label=f"{label}:{line_number}: IID")
        superpopulation = _safe_token(row[1], label=f"{label}:{line_number}: superpopulation")
        if iid in mapping:
            raise ManifestError(f"{label}:{line_number}: duplicate IID {iid!r}")
        mapping[iid] = superpopulation
        order.append(iid)
    return order, mapping


def _mapping_payload(
    *,
    selected_samples_path: Path,
    full_sample_map_path: Path,
    training_sample_map_path: Path,
    heldout_sample_map_path: Path,
) -> tuple[dict[str, object], frozenset[str], dict[str, tuple[str, str]]]:
    selected_snapshot, selected_raw = _read_snapshot_bytes(
        selected_samples_path, label="selected samples"
    )
    full_snapshot, full_raw = _read_snapshot_bytes(full_sample_map_path, label="full sample map")
    training_snapshot, training_raw = _read_snapshot_bytes(
        training_sample_map_path, label="training sample map"
    )
    heldout_snapshot, heldout_raw = _read_snapshot_bytes(
        heldout_sample_map_path, label="heldout sample map"
    )
    _selected_order, selected = _parse_selected_samples(selected_raw)
    full_order, full = _parse_sample_map(full_raw, label="full sample map")
    training_order, training = _parse_sample_map(training_raw, label="training sample map")
    heldout_order, heldout = _parse_sample_map(
        heldout_raw,
        label="heldout sample map",
        header=("IID", "genetic_region"),
    )
    if set(full) != set(selected):
        missing = sorted(set(selected) - set(full))
        unknown = sorted(set(full) - set(selected))
        raise ManifestError(
            "full sample map does not match selected samples; "
            f"missing={missing}, unknown={unknown}"
        )
    for iid, superpopulation in full.items():
        if selected[iid][1] != superpopulation:
            raise ManifestError(f"full sample map superpopulation label mismatch for {iid}")
    overlap = set(training) & set(heldout)
    if overlap:
        raise ManifestError(f"training and heldout partitions overlap: {sorted(overlap)}")
    if set(training) | set(heldout) != set(full):
        missing = sorted(set(full) - (set(training) | set(heldout)))
        unknown = sorted((set(training) | set(heldout)) - set(full))
        raise ManifestError(
            f"training/heldout partition does not account for full map; "
            f"missing={missing}, unknown={unknown}"
        )
    for label, mapping in (("training", training), ("heldout", heldout)):
        for iid, superpopulation in mapping.items():
            if selected[iid][1] != superpopulation:
                raise ManifestError(f"{label} sample map superpopulation label mismatch for {iid}")

    members = [
        {
            "population": selected[iid][0],
            "role": "training" if iid in training else "heldout_test",
            "sample_id": iid,
            "superpopulation": selected[iid][1],
        }
        for iid in full_order
    ]
    return (
        {
            "artifacts": {
                "full_sample_map": full_snapshot.artifact(),
                "heldout_sample_map": heldout_snapshot.artifact(),
                "selected_samples": selected_snapshot.artifact(),
                "training_sample_map": training_snapshot.artifact(),
            },
            "counts": {
                "full": len(full),
                "heldout_test": len(heldout),
                "training": len(training),
            },
            "heldout_order_sha256": _sha256_json(heldout_order),
            "members": members,
            "training_order_sha256": _sha256_json(training_order),
        },
        frozenset(full),
        selected,
    )


def _parse_vcf(
    snapshot: _FileSnapshot,
    *,
    chromosome: str,
    required_samples: frozenset[str],
    selected_coordinates: frozenset[tuple[str, int]],
) -> tuple[list[str], list[list[object]]]:
    descriptor, absolute, before = _open_regular(snapshot.path, label=f"{chromosome} VCF")
    if before != snapshot.signature:
        os.close(descriptor)
        raise ManifestError(f"{chromosome} VCF changed between hashing and parsing")
    samples: list[str] | None = None
    markers: list[list[object]] = []
    marker_keys: set[tuple[object, ...]] = set()
    previous_position = 0
    decompressed_bytes = 0
    try:
        with os.fdopen(descriptor, "rb") as raw_handle:
            try:
                with gzip.GzipFile(fileobj=raw_handle, mode="rb") as vcf_handle:
                    bounded_lines = iter(lambda: vcf_handle.readline(_MAX_VCF_LINE_BYTES + 1), b"")
                    for line_number, raw_line in enumerate(bounded_lines, start=1):
                        decompressed_bytes += len(raw_line)
                        if decompressed_bytes > _MAX_VCF_DECOMPRESSED_BYTES:
                            raise ManifestError(
                                f"{chromosome} VCF: decompressed data exceeds safety limit"
                            )
                        if len(raw_line) > _MAX_VCF_LINE_BYTES:
                            raise ManifestError(
                                f"{chromosome} VCF:{line_number}: line exceeds safety limit"
                            )
                        try:
                            line = raw_line.decode("utf-8").rstrip("\r\n")
                        except UnicodeDecodeError as exc:
                            raise ManifestError(
                                f"{chromosome} VCF:{line_number}: invalid UTF-8"
                            ) from exc
                        if not line:
                            raise ManifestError(f"{chromosome} VCF:{line_number}: empty line")
                        if line.startswith("##"):
                            continue
                        fields = line.split("\t")
                        if line.startswith("#CHROM"):
                            if samples is not None or tuple(fields[:9]) != _VCF_FIXED_HEADER:
                                raise ManifestError(
                                    f"{chromosome} VCF: invalid or duplicate header"
                                )
                            samples = [
                                _safe_token(
                                    sample,
                                    label=f"{chromosome} VCF header sample",
                                )
                                for sample in fields[9:]
                            ]
                            if not samples or len(samples) != len(set(samples)):
                                raise ManifestError(
                                    f"{chromosome} VCF: samples must be non-empty and unique"
                                )
                            continue
                        if line.startswith("#"):
                            raise ManifestError(f"{chromosome} VCF:{line_number}: unknown header")
                        if samples is None:
                            raise ManifestError(f"{chromosome} VCF: data precedes #CHROM header")
                        if len(fields) != 9 + len(samples):
                            raise ManifestError(
                                f"{chromosome} VCF:{line_number}: row/header column mismatch"
                            )
                        if fields[0] != chromosome:
                            raise ManifestError(
                                f"{chromosome} VCF:{line_number}: "
                                f"unexpected chromosome {fields[0]!r}"
                            )
                        try:
                            position = int(fields[1])
                        except ValueError as exc:
                            raise ManifestError(
                                f"{chromosome} VCF:{line_number}: invalid position"
                            ) from exc
                        if position <= 0 or position < previous_position:
                            raise ManifestError(
                                f"{chromosome} VCF:{line_number}: positions are not sorted"
                            )
                        if (chromosome, position) not in selected_coordinates:
                            raise ManifestError(
                                f"{chromosome} VCF:{line_number}: marker is absent from "
                                "the lifted marker-selection regions"
                            )
                        marker_id = _safe_token(
                            fields[2], label=f"{chromosome} VCF:{line_number}: ID"
                        )
                        reference = _safe_token(
                            fields[3], label=f"{chromosome} VCF:{line_number}: REF"
                        )
                        alternate = _safe_token(
                            fields[4], label=f"{chromosome} VCF:{line_number}: ALT"
                        )
                        if alternate == "." or "," in alternate:
                            raise ManifestError(
                                f"{chromosome} VCF:{line_number}: expected one alternate allele"
                            )
                        marker = [chromosome, position, marker_id, reference, alternate]
                        marker_key = tuple(marker)
                        if marker_key in marker_keys:
                            raise ManifestError(
                                f"{chromosome} VCF:{line_number}: duplicate marker tuple"
                            )
                        if len(markers) >= _MAX_VCF_MARKERS_PER_CHROMOSOME:
                            raise ManifestError(
                                f"{chromosome} VCF: marker count exceeds safety limit"
                            )
                        marker_keys.add(marker_key)
                        markers.append(marker)
                        previous_position = position
            except (gzip.BadGzipFile, EOFError, OSError, zlib.error) as exc:
                raise ManifestError(f"{chromosome} VCF: invalid gzip/BGZF stream: {exc}") from exc
            after = _signature(os.fstat(raw_handle.fileno()))
    except OSError as exc:
        raise ManifestError(f"{chromosome} VCF: failed while parsing {absolute}: {exc}") from exc
    if after != snapshot.signature:
        raise ManifestError(f"{chromosome} VCF changed while it was parsed")
    _assert_path_signature(absolute, snapshot.signature, label=f"{chromosome} VCF")
    if samples is None or not markers:
        raise ManifestError(f"{chromosome} VCF must contain a header and markers")
    missing = sorted(required_samples - set(samples))
    if missing:
        raise ManifestError(f"{chromosome} VCF is missing selected reference samples: {missing}")
    return samples, markers


def _chromosome_payload(
    chromosome: str,
    vcf_path: Path,
    index_path: Path,
    *,
    required_samples: frozenset[str],
    selected_coordinates: frozenset[tuple[str, int]],
) -> dict[str, object]:
    vcf_snapshot = _snapshot_file(vcf_path, label=f"{chromosome} VCF")
    index_snapshot = _snapshot_file(index_path, label=f"{chromosome} VCF index")
    samples, markers = _parse_vcf(
        vcf_snapshot,
        chromosome=chromosome,
        required_samples=required_samples,
        selected_coordinates=selected_coordinates,
    )
    return {
        "chromosome": chromosome,
        "index": index_snapshot.artifact(),
        "markers": {
            "count": len(markers),
            "ordered_sha256": _sha256_json(markers),
        },
        "samples": {
            "count": len(samples),
            "ordered_sha256": _sha256_json(samples),
        },
        "vcf": vcf_snapshot.artifact(),
    }


def _shared_input_payload(
    *,
    reference_build: str,
    reference_panel_name: str,
    reference_panel_source: str,
    metadata_path: Path,
    metadata_source: str,
    selected_samples_path: Path,
    full_sample_map_path: Path,
    training_sample_map_path: Path,
    heldout_sample_map_path: Path,
    marker_sources: Mapping[str, Path],
) -> tuple[
    dict[str, object],
    frozenset[str],
    frozenset[tuple[str, int]],
]:
    build = _safe_token(reference_build, label="reference build")
    if build != "GRCh38":
        raise ManifestError("reference build must be exactly GRCh38")
    panel_name = _safe_token(reference_panel_name, label="reference panel name")
    panel_source = _safe_source_uri(reference_panel_source, label="reference panel source")
    metadata_uri = _safe_source_uri(metadata_source, label="metadata source")
    metadata_snapshot, metadata_raw = _read_snapshot_bytes(
        metadata_path, label="reference metadata"
    )
    reference_metadata = _parse_reference_metadata(metadata_raw)
    if not marker_sources:
        raise ManifestError("marker_sources must not be empty")
    required_marker_sources = {"lifted_regions", "union_catalog"}
    if not required_marker_sources.issubset(marker_sources):
        missing = sorted(required_marker_sources - set(marker_sources))
        raise ManifestError(f"marker_sources is missing required source(s): {missing}")
    marker_artifacts: list[dict[str, object]] = []
    seen_labels: set[str] = set()
    selected_coordinates: frozenset[tuple[str, int]] | None = None
    for raw_label, path in sorted(marker_sources.items()):
        if not isinstance(raw_label, str) or not _LABEL_RE.fullmatch(raw_label):
            raise ManifestError(f"invalid marker-source label {raw_label!r}")
        if raw_label in seen_labels:
            raise ManifestError(f"duplicate marker-source label {raw_label!r}")
        seen_labels.add(raw_label)
        if raw_label == "lifted_regions":
            marker_snapshot, marker_raw = _read_snapshot_bytes(
                Path(path), label="marker source lifted_regions"
            )
            selected_coordinates = _parse_lifted_regions(marker_raw)
        else:
            marker_snapshot = _snapshot_file(Path(path), label=f"marker source {raw_label}")
        marker_artifacts.append(
            {
                "artifact": marker_snapshot.artifact(),
                "name": raw_label,
            }
        )
    sample_mappings, required_samples, selected = _mapping_payload(
        selected_samples_path=selected_samples_path,
        full_sample_map_path=full_sample_map_path,
        training_sample_map_path=training_sample_map_path,
        heldout_sample_map_path=heldout_sample_map_path,
    )
    for iid, labels in selected.items():
        if iid not in reference_metadata:
            raise ManifestError(f"selected sample {iid!r} is absent from reference metadata")
        if reference_metadata[iid] != labels:
            raise ManifestError(
                f"selected sample {iid!r} population/superpopulation disagrees with metadata"
            )
    assert selected_coordinates is not None
    return (
        {
            "reference_build": build,
            "reference_panel": {"name": panel_name, "source": panel_source},
            "sample_mappings": sample_mappings,
            "source_artifacts": {
                "marker_selection": marker_artifacts,
                "metadata": {
                    "artifact": metadata_snapshot.artifact(),
                    "source": metadata_uri,
                },
            },
        },
        required_samples,
        selected_coordinates,
    )


def write_input_manifest(
    output: Path,
    *,
    reference_build: str,
    reference_panel_name: str,
    reference_panel_source: str,
    metadata_path: Path,
    metadata_source: str,
    selected_samples_path: Path,
    full_sample_map_path: Path,
    training_sample_map_path: Path,
    heldout_sample_map_path: Path,
    marker_sources: Mapping[str, Path],
    chromosome_files: Mapping[str, tuple[Path, Path]],
) -> dict[str, object]:
    """Validate all declared inputs and atomically write schema-v1 JSON."""
    normalized = _normalized_chromosome_files(chromosome_files)
    if set(normalized) != AUTOSOME_SET:
        missing = sorted(AUTOSOME_SET - set(normalized), key=lambda value: int(value[3:]))
        unexpected = sorted(set(normalized) - AUTOSOME_SET)
        raise ManifestError(
            "training-input manifest requires exactly chr1-chr22; "
            f"missing={missing}, unexpected={unexpected}"
        )
    input_paths = [
        Path(metadata_path),
        Path(selected_samples_path),
        Path(full_sample_map_path),
        Path(training_sample_map_path),
        Path(heldout_sample_map_path),
        *(Path(path) for path in marker_sources.values()),
        *(path for paths in normalized.values() for path in paths),
    ]
    _reject_output_aliases(
        Path(output),
        input_paths,
    )
    captured = _capture_signatures(input_paths)
    shared, required_samples, selected_coordinates = _shared_input_payload(
        reference_build=reference_build,
        reference_panel_name=reference_panel_name,
        reference_panel_source=reference_panel_source,
        metadata_path=metadata_path,
        metadata_source=metadata_source,
        selected_samples_path=selected_samples_path,
        full_sample_map_path=full_sample_map_path,
        training_sample_map_path=training_sample_map_path,
        heldout_sample_map_path=heldout_sample_map_path,
        marker_sources=marker_sources,
    )
    chromosomes = [
        _chromosome_payload(
            chromosome,
            *normalized[chromosome],
            required_samples=required_samples,
            selected_coordinates=selected_coordinates,
        )
        for chromosome in sorted(normalized, key=lambda value: int(value[3:]))
    ]
    payload: dict[str, object] = {
        "chromosomes": chromosomes,
        "manifest_type": INPUT_MANIFEST_TYPE,
        **shared,
        "sample_identifier_policy": SAMPLE_IDENTIFIER_POLICY,
        "schema_version": INPUT_SCHEMA_VERSION,
    }
    _validate_input_payload(payload)
    _assert_captured_signatures(captured)
    _atomic_write(Path(output), payload)
    return payload


def _validate_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ManifestError(f"{label}: invalid SHA-256 checksum")
    return value


def _validate_artifact(value: object, *, label: str) -> dict[str, object]:
    artifact = _exact_keys(value, {"filename", "sha256", "size_bytes"}, label=label)
    _safe_filename(artifact["filename"], label=f"{label}.filename")
    _validate_sha256(artifact["sha256"], label=f"{label}.sha256")
    _positive_int(artifact["size_bytes"], label=f"{label}.size_bytes")
    return artifact


def _validate_digest_count(value: object, *, label: str) -> dict[str, object]:
    digest = _exact_keys(value, {"count", "ordered_sha256"}, label=label)
    _positive_int(digest["count"], label=f"{label}.count")
    _validate_sha256(digest["ordered_sha256"], label=f"{label}.ordered_sha256")
    return digest


def _validate_input_payload(payload: dict[str, object]) -> None:
    root = _exact_keys(
        payload,
        {
            "chromosomes",
            "manifest_type",
            "reference_build",
            "reference_panel",
            "sample_identifier_policy",
            "sample_mappings",
            "schema_version",
            "source_artifacts",
        },
        label="training-input manifest",
    )
    if root["manifest_type"] != INPUT_MANIFEST_TYPE:
        raise ManifestError("training-input manifest has unexpected manifest_type")
    if type(root["schema_version"]) is not int or root["schema_version"] != INPUT_SCHEMA_VERSION:
        raise ManifestError("unsupported training-input manifest schema")
    if root["reference_build"] != "GRCh38":
        raise ManifestError("training-input manifest reference_build must be GRCh38")
    if root["sample_identifier_policy"] != SAMPLE_IDENTIFIER_POLICY:
        raise ManifestError("unsupported sample_identifier_policy")
    reference_panel = _exact_keys(
        root["reference_panel"], {"name", "source"}, label="reference_panel"
    )
    _safe_token(reference_panel["name"], label="reference_panel.name")
    _safe_source_uri(reference_panel["source"], label="reference_panel.source")

    source_artifacts = _exact_keys(
        root["source_artifacts"], {"marker_selection", "metadata"}, label="source_artifacts"
    )
    metadata = _exact_keys(
        source_artifacts["metadata"], {"artifact", "source"}, label="source_artifacts.metadata"
    )
    _validate_artifact(metadata["artifact"], label="source_artifacts.metadata.artifact")
    _safe_source_uri(metadata["source"], label="source_artifacts.metadata.source")
    marker_selection = source_artifacts["marker_selection"]
    if not isinstance(marker_selection, list) or not marker_selection:
        raise ManifestError("source_artifacts.marker_selection must be a non-empty list")
    marker_names: list[str] = []
    for index, raw_entry in enumerate(marker_selection):
        entry = _exact_keys(raw_entry, {"artifact", "name"}, label=f"marker_selection[{index}]")
        name = _safe_token(entry["name"], label=f"marker_selection[{index}].name")
        if not _LABEL_RE.fullmatch(name):
            raise ManifestError(f"marker_selection[{index}].name is invalid")
        marker_names.append(name)
        _validate_artifact(entry["artifact"], label=f"marker_selection[{index}].artifact")
    if marker_names != sorted(set(marker_names)):
        raise ManifestError("marker-selection names must be unique and sorted")
    required_marker_names = {"lifted_regions", "union_catalog"}
    if not required_marker_names.issubset(marker_names):
        raise ManifestError("marker selection must include lifted_regions and union_catalog")

    chromosomes = root["chromosomes"]
    if not isinstance(chromosomes, list) or not chromosomes:
        raise ManifestError("chromosomes must be a non-empty list")
    observed_chromosomes: list[str] = []
    chromosome_sample_counts: list[int] = []
    for index, raw_entry in enumerate(chromosomes):
        entry = _exact_keys(
            raw_entry,
            {"chromosome", "index", "markers", "samples", "vcf"},
            label=f"chromosomes[{index}]",
        )
        chromosome = entry["chromosome"]
        if not isinstance(chromosome, str) or chromosome not in AUTOSOME_SET:
            raise ManifestError(f"chromosomes[{index}].chromosome is not an autosome")
        observed_chromosomes.append(chromosome)
        _validate_artifact(entry["vcf"], label=f"chromosomes[{index}].vcf")
        _validate_artifact(entry["index"], label=f"chromosomes[{index}].index")
        samples = _validate_digest_count(entry["samples"], label=f"chromosomes[{index}].samples")
        chromosome_sample_counts.append(int(samples["count"]))
        _validate_digest_count(entry["markers"], label=f"chromosomes[{index}].markers")
    if observed_chromosomes != list(AUTOSOMES):
        raise ManifestError("chromosome entries must contain exactly chr1-chr22 in order")

    sample_mappings = _exact_keys(
        root["sample_mappings"],
        {
            "artifacts",
            "counts",
            "heldout_order_sha256",
            "members",
            "training_order_sha256",
        },
        label="sample_mappings",
    )
    artifacts = _exact_keys(
        sample_mappings["artifacts"],
        {
            "full_sample_map",
            "heldout_sample_map",
            "selected_samples",
            "training_sample_map",
        },
        label="sample_mappings.artifacts",
    )
    for name, artifact in artifacts.items():
        _validate_artifact(artifact, label=f"sample_mappings.artifacts.{name}")
    _validate_sha256(
        sample_mappings["training_order_sha256"],
        label="sample_mappings.training_order_sha256",
    )
    _validate_sha256(
        sample_mappings["heldout_order_sha256"],
        label="sample_mappings.heldout_order_sha256",
    )
    counts = _exact_keys(
        sample_mappings["counts"],
        {"full", "heldout_test", "training"},
        label="sample_mappings.counts",
    )
    full_count = _positive_int(counts["full"], label="sample_mappings.counts.full")
    training_count = _positive_int(counts["training"], label="sample_mappings.counts.training")
    heldout_count = _positive_int(
        counts["heldout_test"], label="sample_mappings.counts.heldout_test"
    )
    if full_count != training_count + heldout_count:
        raise ManifestError("sample_mappings counts do not form an exact partition")
    if any(sample_count < full_count for sample_count in chromosome_sample_counts):
        raise ManifestError("chromosome sample count is smaller than full mapped-sample count")
    members = sample_mappings["members"]
    if not isinstance(members, list) or len(members) != full_count:
        raise ManifestError("sample_mappings.members count does not match full count")
    seen_members: set[str] = set()
    observed_counts = {"training": 0, "heldout_test": 0}
    for index, raw_member in enumerate(members):
        member = _exact_keys(
            raw_member,
            {"population", "role", "sample_id", "superpopulation"},
            label=f"sample_mappings.members[{index}]",
        )
        iid = _safe_token(member["sample_id"], label=f"members[{index}].sample_id")
        _safe_token(member["population"], label=f"members[{index}].population")
        _safe_token(member["superpopulation"], label=f"members[{index}].superpopulation")
        role = member["role"]
        if not isinstance(role, str):
            raise ManifestError(f"members[{index}].role is invalid")
        if role not in observed_counts:
            raise ManifestError(f"members[{index}].role is invalid")
        if iid in seen_members:
            raise ManifestError(f"duplicate sample_mappings member {iid!r}")
        seen_members.add(iid)
        observed_counts[role] += 1
    if observed_counts != {"training": training_count, "heldout_test": heldout_count}:
        raise ManifestError("sample_mappings member roles do not match declared counts")


def load_input_manifest(path: Path) -> LoadedManifest:
    """Load strict canonical schema-v1 training-input JSON."""
    contract = _load_canonical(Path(path), manifest_type=INPUT_MANIFEST_TYPE)
    _validate_input_payload(contract.payload)
    return contract


def verify_input_manifest(
    path: Path,
    *,
    reference_build: str,
    reference_panel_name: str,
    reference_panel_source: str,
    metadata_path: Path,
    metadata_source: str,
    selected_samples_path: Path,
    full_sample_map_path: Path,
    training_sample_map_path: Path,
    heldout_sample_map_path: Path,
    marker_sources: Mapping[str, Path],
    chromosome_files: Mapping[str, tuple[Path, Path]],
    chromosomes: Sequence[str] | None = None,
) -> LoadedManifest:
    """Recompute current inputs and compare them with a published manifest.

    ``chromosomes`` limits only the large VCF/index rehash. Shared mappings and
    sources are always verified. Phase 07 omits it to verify the full set.
    """
    manifest_signature = _capture_signatures([Path(path)])
    contract = load_input_manifest(Path(path))
    declared_entries = {
        str(entry["chromosome"]): entry for entry in contract.payload["chromosomes"]
    }
    normalized_files = _normalized_chromosome_files(chromosome_files)
    if chromosomes is None:
        requested = list(declared_entries)
        if set(normalized_files) != set(declared_entries):
            raise ManifestError(
                "chromosome_files do not exactly match the manifest chromosome set"
            )
    else:
        if isinstance(chromosomes, (str, bytes)) or not chromosomes:
            raise ManifestError("chromosomes subset must be a non-empty sequence")
        requested = [_normalize_chromosome(str(value)) for value in chromosomes]
        if len(requested) != len(set(requested)):
            raise ManifestError("chromosomes subset contains duplicates")
        unknown = sorted(set(requested) - set(declared_entries))
        missing_paths = sorted(set(requested) - set(normalized_files))
        if unknown:
            raise ManifestError(f"chromosomes subset is absent from manifest: {unknown}")
        if missing_paths:
            raise ManifestError(f"chromosomes subset lacks current file paths: {missing_paths}")
    input_paths = [
        Path(metadata_path),
        Path(selected_samples_path),
        Path(full_sample_map_path),
        Path(training_sample_map_path),
        Path(heldout_sample_map_path),
        *(Path(marker_path) for marker_path in marker_sources.values()),
        *(file_path for chromosome in requested for file_path in normalized_files[chromosome]),
    ]
    captured = _capture_signatures(input_paths)
    shared, required_samples, selected_coordinates = _shared_input_payload(
        reference_build=reference_build,
        reference_panel_name=reference_panel_name,
        reference_panel_source=reference_panel_source,
        metadata_path=metadata_path,
        metadata_source=metadata_source,
        selected_samples_path=selected_samples_path,
        full_sample_map_path=full_sample_map_path,
        training_sample_map_path=training_sample_map_path,
        heldout_sample_map_path=heldout_sample_map_path,
        marker_sources=marker_sources,
    )
    for key, value in shared.items():
        if contract.payload[key] != value:
            raise ManifestError(f"training-input manifest {key} does not match current inputs")
    for chromosome in requested:
        current = _chromosome_payload(
            chromosome,
            *normalized_files[chromosome],
            required_samples=required_samples,
            selected_coordinates=selected_coordinates,
        )
        if current != declared_entries[chromosome]:
            raise ManifestError(
                f"{chromosome}: training-input manifest does not match current files"
            )
    _assert_captured_signatures([*manifest_signature, *captured])
    return contract


def _training_and_holdout_members(
    input_contract: LoadedManifest,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    members = input_contract.payload["sample_mappings"]["members"]
    training: dict[str, str] = {}
    heldout: list[dict[str, str]] = []
    for raw_member in members:
        iid = str(raw_member["sample_id"])
        superpopulation = str(raw_member["superpopulation"])
        if raw_member["role"] == "training":
            training[iid] = superpopulation
        else:
            heldout.append({"sample_id": iid, "superpopulation": superpopulation})
    return training, heldout


def _normalized_split_files(split_files: Mapping[str, Path]) -> dict[str, Path]:
    if set(split_files) != set(SPLIT_NAMES):
        raise ManifestError("split_files must contain exactly train1, train2, and val")
    normalized = {name: Path(split_files[name]) for name in SPLIT_NAMES}
    absolute_paths = [
        _absolute_without_symlinks(path, label=f"{name} split map")
        for name, path in normalized.items()
    ]
    identities: list[tuple[int, int]] = []
    for path in absolute_paths:
        metadata = path.lstat()
        identities.append((metadata.st_dev, metadata.st_ino))
    if len(set(identities)) != len(identities):
        raise ManifestError("train1, train2, and val must be different split-map files")
    return normalized


def _build_split_payload(
    *,
    input_contract: LoadedManifest,
    split_files: Mapping[str, Path],
) -> dict[str, object]:
    training, heldout = _training_and_holdout_members(input_contract)
    normalized = _normalized_split_files(split_files)
    internal_splits: list[dict[str, object]] = []
    memberships: dict[str, set[str]] = {}
    for name in SPLIT_NAMES:
        snapshot, raw = _read_snapshot_bytes(normalized[name], label=f"{name} split map")
        order, mapping = _parse_sample_map(raw, label=f"{name} split map")
        unknown = sorted(set(mapping) - set(training))
        if unknown:
            heldout_leaks = sorted(set(unknown) & {member["sample_id"] for member in heldout})
            if heldout_leaks:
                raise ManifestError(f"{name} split leaks heldout test sample(s): {heldout_leaks}")
            raise ManifestError(f"{name} split contains unknown/non-training samples: {unknown}")
        for iid, superpopulation in mapping.items():
            if training[iid] != superpopulation:
                raise ManifestError(f"{name} split superpopulation label mismatch for {iid}")
        memberships[name] = set(mapping)
        internal_splits.append(
            {
                "artifact": snapshot.artifact(),
                "member_count": len(order),
                "members": [{"sample_id": iid, "superpopulation": mapping[iid]} for iid in order],
                "name": name,
            }
        )
    if memberships["val"] & (memberships["train1"] | memberships["train2"]):
        raise ManifestError("val split must not overlap train1 or train2")
    covered = memberships["train1"] | memberships["train2"] | memberships["val"]
    if covered != set(training):
        raise ManifestError(
            f"internal splits do not cover every training member; "
            f"missing={sorted(set(training) - covered)}"
        )
    return {
        "external_test": {
            "member_count": len(heldout),
            "members": heldout,
            "source_sha256": input_contract.payload["sample_mappings"]["artifacts"][
                "heldout_sample_map"
            ]["sha256"],
        },
        "internal_splits": internal_splits,
        "manifest_type": SPLIT_MANIFEST_TYPE,
        "schema_version": SPLIT_SCHEMA_VERSION,
        "train1_train2_overlap": sorted(memberships["train1"] & memberships["train2"]),
        "training_input_manifest": {
            "schema_version": INPUT_SCHEMA_VERSION,
            "sha256": input_contract.sha256,
        },
    }


def write_split_manifest(
    output: Path,
    *,
    input_manifest_path: Path,
    split_files: Mapping[str, Path],
) -> dict[str, object]:
    """Validate actual emitted Gnomix founder maps and write canonical JSON."""
    input_paths = [Path(input_manifest_path), *(Path(path) for path in split_files.values())]
    _reject_output_aliases(
        Path(output),
        input_paths,
    )
    captured = _capture_signatures(input_paths)
    payload = _build_split_payload(
        input_contract=load_input_manifest(Path(input_manifest_path)),
        split_files=split_files,
    )
    _validate_split_payload(payload)
    _assert_captured_signatures(captured)
    _atomic_write(Path(output), payload)
    return payload


def _validate_split_payload(payload: dict[str, object]) -> None:
    root = _exact_keys(
        payload,
        {
            "external_test",
            "internal_splits",
            "manifest_type",
            "schema_version",
            "train1_train2_overlap",
            "training_input_manifest",
        },
        label="training-split manifest",
    )
    if root["manifest_type"] != SPLIT_MANIFEST_TYPE:
        raise ManifestError("training-split manifest has unexpected manifest_type")
    if type(root["schema_version"]) is not int or root["schema_version"] != SPLIT_SCHEMA_VERSION:
        raise ManifestError("unsupported training-split manifest schema")
    input_identity = _exact_keys(
        root["training_input_manifest"],
        {"schema_version", "sha256"},
        label="training_input_manifest",
    )
    if (
        type(input_identity["schema_version"]) is not int
        or input_identity["schema_version"] != INPUT_SCHEMA_VERSION
    ):
        raise ManifestError("training-split manifest references unsupported input schema")
    _validate_sha256(input_identity["sha256"], label="training_input_manifest.sha256")

    internal = root["internal_splits"]
    if not isinstance(internal, list) or len(internal) != len(SPLIT_NAMES):
        raise ManifestError("internal_splits must contain train1, train2, and val")
    memberships: dict[str, set[str]] = {}
    membership_labels: dict[str, dict[str, str]] = {}
    for index, raw_entry in enumerate(internal):
        entry = _exact_keys(
            raw_entry,
            {"artifact", "member_count", "members", "name"},
            label=f"internal_splits[{index}]",
        )
        if entry["name"] != SPLIT_NAMES[index]:
            raise ManifestError("internal_splits must be ordered train1, train2, val")
        name = SPLIT_NAMES[index]
        _validate_artifact(entry["artifact"], label=f"internal_splits[{index}].artifact")
        count = _positive_int(
            entry["member_count"], label=f"internal_splits[{index}].member_count"
        )
        members = entry["members"]
        if not isinstance(members, list) or len(members) != count:
            raise ManifestError(f"{name} members do not match member_count")
        ids: set[str] = set()
        labels: dict[str, str] = {}
        for member_index, raw_member in enumerate(members):
            member = _exact_keys(
                raw_member,
                {"sample_id", "superpopulation"},
                label=f"{name}.members[{member_index}]",
            )
            iid = _safe_token(
                member["sample_id"], label=f"{name}.members[{member_index}].sample_id"
            )
            superpopulation = _safe_token(
                member["superpopulation"],
                label=f"{name}.members[{member_index}].superpopulation",
            )
            if iid in ids:
                raise ManifestError(f"{name} contains duplicate member {iid!r}")
            ids.add(iid)
            labels[iid] = superpopulation
        memberships[name] = ids
        membership_labels[name] = labels
    if memberships["val"] & (memberships["train1"] | memberships["train2"]):
        raise ManifestError("val split must not overlap train1 or train2")

    overlap = root["train1_train2_overlap"]
    if not isinstance(overlap, list) or any(not isinstance(iid, str) for iid in overlap):
        raise ManifestError("train1_train2_overlap must be a list of sample IDs")
    expected_overlap = sorted(memberships["train1"] & memberships["train2"])
    if overlap != expected_overlap:
        raise ManifestError("train1_train2_overlap does not match internal memberships")
    if any(
        membership_labels["train1"][iid] != membership_labels["train2"][iid]
        for iid in expected_overlap
    ):
        raise ManifestError("train1/train2 overlap has inconsistent superpopulation labels")

    external = _exact_keys(
        root["external_test"],
        {"member_count", "members", "source_sha256"},
        label="external_test",
    )
    external_count = _positive_int(external["member_count"], label="external_test.member_count")
    _validate_sha256(external["source_sha256"], label="external_test.source_sha256")
    external_members = external["members"]
    if not isinstance(external_members, list) or len(external_members) != external_count:
        raise ManifestError("external_test members do not match member_count")
    external_ids: set[str] = set()
    for index, raw_member in enumerate(external_members):
        member = _exact_keys(
            raw_member,
            {"sample_id", "superpopulation"},
            label=f"external_test.members[{index}]",
        )
        iid = _safe_token(member["sample_id"], label=f"external_test.members[{index}].sample_id")
        _safe_token(
            member["superpopulation"],
            label=f"external_test.members[{index}].superpopulation",
        )
        if iid in external_ids:
            raise ManifestError(f"external_test contains duplicate member {iid!r}")
        external_ids.add(iid)
    if external_ids & set().union(*memberships.values()):
        raise ManifestError("external heldout test members leak into internal splits")


def load_split_manifest(path: Path) -> LoadedManifest:
    """Load strict canonical schema-v1 Gnomix split JSON."""
    contract = _load_canonical(Path(path), manifest_type=SPLIT_MANIFEST_TYPE)
    _validate_split_payload(contract.payload)
    return contract


def verify_split_manifest(
    path: Path,
    *,
    input_manifest_path: Path,
    split_files: Mapping[str, Path],
) -> LoadedManifest:
    """Verify split JSON against the current input manifest and emitted maps."""
    captured = _capture_signatures(
        [
            Path(path),
            Path(input_manifest_path),
            *(Path(split_path) for split_path in split_files.values()),
        ]
    )
    contract = load_split_manifest(Path(path))
    current = _build_split_payload(
        input_contract=load_input_manifest(Path(input_manifest_path)),
        split_files=split_files,
    )
    if current != contract.payload:
        raise ManifestError("training-split manifest does not match current inputs or split maps")
    _assert_captured_signatures(captured)
    return contract


def _parse_named_paths(values: Sequence[str], *, option: str) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for raw in values:
        name, separator, raw_path = raw.partition("=")
        if not separator or not name or not raw_path:
            raise ManifestError(f"{option}: expected NAME=PATH, got {raw!r}")
        if name in parsed:
            raise ManifestError(f"{option}: duplicate name {name!r}")
        parsed[name] = Path(raw_path)
    return parsed


def _parse_chromosome_paths(values: Sequence[str]) -> dict[str, tuple[Path, Path]]:
    parsed: dict[str, tuple[Path, Path]] = {}
    for raw in values:
        chromosome, separator, paths = raw.partition("=")
        raw_vcf, comma, raw_index = paths.partition(",")
        if not separator or not chromosome or not comma or not raw_vcf or not raw_index:
            raise ManifestError(f"--chromosome-file: expected CHROM=VCF,INDEX, got {raw!r}")
        if chromosome in parsed:
            raise ManifestError(f"--chromosome-file: duplicate key {chromosome!r}")
        parsed[chromosome] = (Path(raw_vcf), Path(raw_index))
    return parsed


def _add_input_artifact_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--reference-build", required=True)
    parser.add_argument("--reference-panel-name", required=True)
    parser.add_argument("--reference-panel-source", required=True)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--metadata-source", required=True)
    parser.add_argument("--selected-samples", required=True, type=Path)
    parser.add_argument("--full-sample-map", required=True, type=Path)
    parser.add_argument("--training-sample-map", required=True, type=Path)
    parser.add_argument("--heldout-sample-map", required=True, type=Path)
    parser.add_argument(
        "--marker-source",
        required=True,
        action="append",
        metavar="NAME=PATH",
    )
    parser.add_argument(
        "--chromosome-file",
        required=True,
        action="append",
        metavar="CHROM=VCF,INDEX",
    )


def _input_kwargs_from_args(args: argparse.Namespace) -> dict[str, object]:
    return {
        "chromosome_files": _parse_chromosome_paths(args.chromosome_file),
        "full_sample_map_path": args.full_sample_map,
        "heldout_sample_map_path": args.heldout_sample_map,
        "marker_sources": _parse_named_paths(args.marker_source, option="--marker-source"),
        "metadata_path": args.metadata,
        "metadata_source": args.metadata_source,
        "reference_build": args.reference_build,
        "reference_panel_name": args.reference_panel_name,
        "reference_panel_source": args.reference_panel_source,
        "selected_samples_path": args.selected_samples,
        "training_sample_map_path": args.training_sample_map,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_input = subparsers.add_parser("create-input", help="write training-input JSON")
    create_input.add_argument("--output", required=True, type=Path)
    _add_input_artifact_arguments(create_input)

    verify_input = subparsers.add_parser("verify-input", help="verify training-input JSON")
    verify_input.add_argument("--manifest", required=True, type=Path)
    verify_input.add_argument("--chromosomes", nargs="+")
    _add_input_artifact_arguments(verify_input)

    create_splits = subparsers.add_parser("create-splits", help="write actual split JSON")
    create_splits.add_argument("--output", required=True, type=Path)
    create_splits.add_argument("--input-manifest", required=True, type=Path)
    create_splits.add_argument("--split-file", required=True, action="append", metavar="NAME=PATH")

    verify_splits = subparsers.add_parser("verify-splits", help="verify actual split JSON")
    verify_splits.add_argument("--manifest", required=True, type=Path)
    verify_splits.add_argument("--input-manifest", required=True, type=Path)
    verify_splits.add_argument("--split-file", required=True, action="append", metavar="NAME=PATH")
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    try:
        if args.command == "create-input":
            write_input_manifest(args.output, **_input_kwargs_from_args(args))
            contract = load_input_manifest(args.output)
        elif args.command == "verify-input":
            contract = verify_input_manifest(
                args.manifest,
                chromosomes=args.chromosomes,
                **_input_kwargs_from_args(args),
            )
        elif args.command == "create-splits":
            split_files = _parse_named_paths(args.split_file, option="--split-file")
            write_split_manifest(
                args.output,
                input_manifest_path=args.input_manifest,
                split_files=split_files,
            )
            contract = load_split_manifest(args.output)
        else:
            contract = verify_split_manifest(
                args.manifest,
                input_manifest_path=args.input_manifest,
                split_files=_parse_named_paths(args.split_file, option="--split-file"),
            )
    except (ManifestError, OSError) as exc:
        parser.exit(1, f"error: {exc}\n")
    print(f"verified {contract.payload['manifest_type']} sha256={contract.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
