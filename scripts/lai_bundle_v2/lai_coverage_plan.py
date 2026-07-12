"""Bounded-memory job-plan construction for LAI coverage calibration.

The plan is a compact description of Cartesian axes.  Individual jobs are
stored in immutable, Merkle-authenticated shards so an array task can read one
row without loading the whole matrix.  Plan schema version 3 deliberately uses
domain-separated leaf and internal-node hashes.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

PLAN_SCHEMA_VERSION = 3
SHARD_SCHEMA_VERSION = 2
ORDERING = "iid-mask-drop-fraction-seed-v1"
DEFAULT_MAX_JOBS = 100_000
HARD_MAX_JOBS = 1_000_000
DEFAULT_DISK_RESERVE_BYTES = 16 * 1024 * 1024
MAX_JSON_ARTIFACT_BYTES = 64 * 1024 * 1024

LEAF_HASH_DOMAIN = b"yeliztli:lai-coverage-plan:v3:leaf\x00"
NODE_HASH_DOMAIN = b"yeliztli:lai-coverage-plan:v3:node\x00"

_SIGNED_64_MIN = -(1 << 63)
_SIGNED_64_MAX = (1 << 63) - 1
_DIGEST_BYTES = 32
_DIGEST_HEX_LENGTH = 64
_DIRECTORY_METADATA_BYTES_PER_SHARD = 512
_MINIMUM_METADATA_ALLOWANCE = 1024 * 1024


def canonical_json_bytes(value: object) -> bytes:
    """Return the repository's deterministic JSON byte representation."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value is not canonical-JSON serializable: {exc}") from exc


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _DIGEST_HEX_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _axis_string(value: object, description: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{description} must be a non-empty canonical string")
    return value


def _axis_sequence(values: object, description: str) -> tuple[object, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{description} must be a non-empty sequence")
    result = tuple(values)
    if not result:
        raise ValueError(f"{description} must be a non-empty sequence")
    return result


def _unique_strings(values: object, description: str) -> tuple[str, ...]:
    normalized = tuple(
        _axis_string(value, f"{description} entry")
        for value in _axis_sequence(values, description)
    )
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{description} entries must be unique")
    return normalized


def _canonical_fraction(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, Decimal)):
        raise ValueError("fractions must be canonical decimal strings or Decimal values")
    raw = str(value)
    try:
        fraction = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"invalid fraction {raw!r}") from None
    if not fraction.is_finite():
        raise ValueError("fractions must be finite, > 0, and <= 1")
    canonical = str(fraction.normalize())
    if not Decimal(0) < fraction <= Decimal(1):
        raise ValueError("fractions must be finite, > 0, and <= 1")
    if isinstance(value, str) and value != canonical:
        raise ValueError(f"fraction {value!r} is not canonical; use {canonical!r}")
    return canonical


def _canonical_fractions(values: object) -> tuple[str, ...]:
    normalized = tuple(_canonical_fraction(value) for value in _axis_sequence(values, "fractions"))
    if len(set(normalized)) != len(normalized):
        raise ValueError("fractions must be unique after canonicalization")
    return normalized


def _canonical_seeds(values: object) -> tuple[int, ...]:
    raw_values = _axis_sequence(values, "seeds")
    seeds: list[int] = []
    for value in raw_values:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not _SIGNED_64_MIN <= value <= _SIGNED_64_MAX
        ):
            raise ValueError("seeds must be unique signed 64-bit integers")
        seeds.append(value)
    if len(set(seeds)) != len(seeds):
        raise ValueError("seeds must be unique signed 64-bit integers")
    return tuple(seeds)


def _canonical_fixture_masks(
    fixture_masks: object,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if isinstance(fixture_masks, Mapping):
        raw_pairs: Sequence[object] = tuple(fixture_masks.items())
    else:
        raw_pairs = _axis_sequence(fixture_masks, "fixture masks")
    if not raw_pairs:
        raise ValueError("fixture masks must be non-empty")

    normalized: list[tuple[str, tuple[str, ...]]] = []
    for index, raw_pair in enumerate(raw_pairs):
        if (
            isinstance(raw_pair, (str, bytes))
            or not isinstance(raw_pair, Sequence)
            or len(raw_pair) != 2
        ):
            raise ValueError(f"fixture masks entry {index} must be an IID/masks pair")
        iid = _axis_string(raw_pair[0], f"fixture masks entry {index} IID")
        masks = _unique_strings(raw_pair[1], f"masks for IID {iid!r}")
        normalized.append((iid, masks))
    iids = [iid for iid, _masks in normalized]
    if len(set(iids)) != len(iids):
        raise ValueError("fixture IIDs must be unique")
    return tuple(normalized)


def _validate_max_jobs(max_jobs: object) -> int:
    if (
        not isinstance(max_jobs, int)
        or isinstance(max_jobs, bool)
        or not 1 <= max_jobs <= HARD_MAX_JOBS
    ):
        raise ValueError(f"max_jobs must be between 1 and {HARD_MAX_JOBS}")
    return max_jobs


def _checked_row_count(
    fixture_masks: Sequence[tuple[str, Sequence[str]]],
    drops: Sequence[str],
    fractions: Sequence[str],
    seeds: Sequence[int],
    max_jobs: int,
) -> int:
    mask_cells = sum(len(masks) for _iid, masks in fixture_masks)
    count = mask_cells
    for width in (len(drops), len(fractions), len(seeds)):
        count *= width
        if count > max_jobs:
            raise ValueError(f"job matrix has {count} rows, exceeding max_jobs={max_jobs}")
    if count <= 0:
        raise ValueError("job matrix must contain at least one row")
    return count


@dataclass(frozen=True, slots=True)
class JobMatrix:
    """Validated Cartesian axes with deterministic row arithmetic."""

    dataset_split: str
    fixture_masks: tuple[tuple[str, tuple[str, ...]], ...]
    drop_scenarios: tuple[str, ...]
    fractions: tuple[str, ...]
    seeds: tuple[int, ...]
    max_jobs: int
    _row_count: int

    @classmethod
    def create(
        cls,
        *,
        dataset_split: object,
        fixture_masks: object,
        drop_scenarios: object,
        fractions: object,
        seeds: object,
        max_jobs: object = DEFAULT_MAX_JOBS,
    ) -> JobMatrix:
        split = _axis_string(dataset_split, "dataset split")
        normalized_fixture_masks = _canonical_fixture_masks(fixture_masks)
        normalized_drops = _unique_strings(drop_scenarios, "chromosome-drop scenarios")
        normalized_fractions = _canonical_fractions(fractions)
        normalized_seeds = _canonical_seeds(seeds)
        normalized_max_jobs = _validate_max_jobs(max_jobs)
        count = _checked_row_count(
            normalized_fixture_masks,
            normalized_drops,
            normalized_fractions,
            normalized_seeds,
            normalized_max_jobs,
        )
        return cls(
            dataset_split=split,
            fixture_masks=normalized_fixture_masks,
            drop_scenarios=normalized_drops,
            fractions=normalized_fractions,
            seeds=normalized_seeds,
            max_jobs=normalized_max_jobs,
            _row_count=count,
        )

    @classmethod
    def from_json(cls, raw: object, *, max_jobs: object) -> JobMatrix:
        expected_keys = {
            "dataset_split",
            "fixture_masks",
            "chromosome_drop_scenarios",
            "fractions",
            "seeds",
        }
        if not isinstance(raw, Mapping) or set(raw) != expected_keys:
            raise ValueError("job-matrix axes have an invalid schema")
        raw_fixture_masks = raw["fixture_masks"]
        if not isinstance(raw_fixture_masks, list) or not raw_fixture_masks:
            raise ValueError("job-matrix fixture masks have an invalid schema")
        fixture_pairs: list[tuple[object, object]] = []
        for raw_entry in raw_fixture_masks:
            if (
                not isinstance(raw_entry, Mapping)
                or set(raw_entry) != {"iid", "masks"}
                or not isinstance(raw_entry["masks"], list)
            ):
                raise ValueError("job-matrix fixture masks have an invalid schema")
            fixture_pairs.append((raw_entry["iid"], raw_entry["masks"]))
        for field in ("chromosome_drop_scenarios", "fractions", "seeds"):
            if not isinstance(raw[field], list):
                raise ValueError(f"job-matrix axis {field!r} must be a JSON array")
        return cls.create(
            dataset_split=raw["dataset_split"],
            fixture_masks=fixture_pairs,
            drop_scenarios=raw["chromosome_drop_scenarios"],
            fractions=raw["fractions"],
            seeds=raw["seeds"],
            max_jobs=max_jobs,
        )

    @property
    def row_count(self) -> int:
        return self._row_count

    def to_json(self) -> dict[str, object]:
        return {
            "dataset_split": self.dataset_split,
            "fixture_masks": [
                {"iid": iid, "masks": list(masks)} for iid, masks in self.fixture_masks
            ],
            "chromosome_drop_scenarios": list(self.drop_scenarios),
            "fractions": list(self.fractions),
            "seeds": list(self.seeds),
        }

    def iter_rows(self) -> Iterator[dict[str, object]]:
        index = 0
        for iid, masks in self.fixture_masks:
            for mask in masks:
                for drop in self.drop_scenarios:
                    for fraction in self.fractions:
                        for seed in self.seeds:
                            yield _job_row(
                                index=index,
                                iid=iid,
                                dataset_split=self.dataset_split,
                                mask=mask,
                                drop=drop,
                                fraction=fraction,
                                seed=seed,
                            )
                            index += 1
        if index != self.row_count:
            raise AssertionError("validated job-matrix count changed during iteration")

    def row_at(self, index: object) -> dict[str, object]:
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < self.row_count
        ):
            raise ValueError(f"job index must be between 0 and {self.row_count - 1}")

        seed_width = len(self.seeds)
        fraction_width = len(self.fractions) * seed_width
        drop_width = len(self.drop_scenarios) * fraction_width
        remaining = index
        selected_iid: str | None = None
        selected_masks: tuple[str, ...] | None = None
        for iid, masks in self.fixture_masks:
            fixture_width = len(masks) * drop_width
            if remaining < fixture_width:
                selected_iid = iid
                selected_masks = masks
                break
            remaining -= fixture_width
        if selected_iid is None or selected_masks is None:
            raise AssertionError("validated job index could not be mapped to fixture axes")

        mask_index, remaining = divmod(remaining, drop_width)
        drop_index, remaining = divmod(remaining, fraction_width)
        fraction_index, seed_index = divmod(remaining, seed_width)
        return _job_row(
            index=index,
            iid=selected_iid,
            dataset_split=self.dataset_split,
            mask=selected_masks[mask_index],
            drop=self.drop_scenarios[drop_index],
            fraction=self.fractions[fraction_index],
            seed=self.seeds[seed_index],
        )


def _job_row(
    *,
    index: int,
    iid: str,
    dataset_split: str,
    mask: str,
    drop: str,
    fraction: str,
    seed: int,
) -> dict[str, object]:
    return {
        "job_index": index,
        "iid": iid,
        "dataset_split": dataset_split,
        "mask": mask,
        "chromosome_drop_scenario": drop,
        "fraction": fraction,
        "seed": seed,
    }


def row_count(
    fixture_masks: object,
    drop_scenarios: object,
    fractions: object,
    seeds: object,
    *,
    max_jobs: object = DEFAULT_MAX_JOBS,
) -> int:
    return JobMatrix.create(
        dataset_split="row-count",
        fixture_masks=fixture_masks,
        drop_scenarios=drop_scenarios,
        fractions=fractions,
        seeds=seeds,
        max_jobs=max_jobs,
    ).row_count


def iter_rows(
    *,
    dataset_split: object,
    fixture_masks: object,
    drop_scenarios: object,
    fractions: object,
    seeds: object,
    max_jobs: object = DEFAULT_MAX_JOBS,
) -> Iterator[dict[str, object]]:
    matrix = JobMatrix.create(
        dataset_split=dataset_split,
        fixture_masks=fixture_masks,
        drop_scenarios=drop_scenarios,
        fractions=fractions,
        seeds=seeds,
        max_jobs=max_jobs,
    )
    yield from matrix.iter_rows()


def row_at(
    index: object,
    *,
    dataset_split: object,
    fixture_masks: object,
    drop_scenarios: object,
    fractions: object,
    seeds: object,
    max_jobs: object = DEFAULT_MAX_JOBS,
) -> dict[str, object]:
    return JobMatrix.create(
        dataset_split=dataset_split,
        fixture_masks=fixture_masks,
        drop_scenarios=drop_scenarios,
        fractions=fractions,
        seeds=seeds,
        max_jobs=max_jobs,
    ).row_at(index)


def hash_job_leaf(row: Mapping[str, object]) -> bytes:
    payload = canonical_json_bytes(row)
    return hashlib.sha256(LEAF_HASH_DOMAIN + len(payload).to_bytes(8, "big") + payload).digest()


def hash_merkle_node(left: bytes, right: bytes) -> bytes:
    if len(left) != _DIGEST_BYTES or len(right) != _DIGEST_BYTES:
        raise ValueError("Merkle child hashes must each contain exactly 32 bytes")
    return hashlib.sha256(NODE_HASH_DOMAIN + left + right).digest()


def _proof_shape(leaf_index: int, leaf_count: int) -> tuple[tuple[str, int, bool], ...]:
    if leaf_count <= 0 or not 0 <= leaf_index < leaf_count:
        raise ValueError("invalid Merkle leaf index/count")
    shape: list[tuple[str, int, bool]] = []
    index = leaf_index
    count = leaf_count
    while count > 1:
        sibling_index = index ^ 1
        duplicate = sibling_index >= count
        if duplicate:
            sibling_index = index
        side = "left" if sibling_index < index else "right"
        shape.append((side, sibling_index, duplicate))
        index //= 2
        count = (count + 1) // 2
    return tuple(shape)


def verify_merkle_proof(
    row: Mapping[str, object],
    *,
    leaf_index: object,
    leaf_count: object,
    proof: object,
    expected_root_sha256: object,
) -> bool:
    if (
        not isinstance(leaf_index, int)
        or isinstance(leaf_index, bool)
        or not isinstance(leaf_count, int)
        or isinstance(leaf_count, bool)
        or leaf_count <= 0
        or not 0 <= leaf_index < leaf_count
        or not isinstance(proof, list)
        or not _is_sha256(expected_root_sha256)
    ):
        return False
    shape = _proof_shape(leaf_index, leaf_count)
    if len(proof) != len(shape):
        return False

    current = hash_job_leaf(row)
    for raw_step, (expected_side, _sibling_index, duplicate) in zip(proof, shape, strict=True):
        if (
            not isinstance(raw_step, Mapping)
            or set(raw_step) != {"side", "sha256"}
            or raw_step.get("side") != expected_side
            or not _is_sha256(raw_step.get("sha256"))
        ):
            return False
        sibling = bytes.fromhex(str(raw_step["sha256"]))
        if duplicate and sibling != current:
            return False
        if expected_side == "left":
            current = hash_merkle_node(sibling, current)
        else:
            current = hash_merkle_node(current, sibling)
    return current.hex() == expected_root_sha256


def _round_up(value: int, block_size: int) -> int:
    return ((value + block_size - 1) // block_size) * block_size


def _maximum_shard_payload_size(matrix: JobMatrix) -> int:
    def encoded_string_size(value: str) -> int:
        return len(canonical_json_bytes(value))

    iid, mask = max(
        ((iid, mask) for iid, masks in matrix.fixture_masks for mask in masks),
        key=lambda pair: encoded_string_size(pair[0]) + encoded_string_size(pair[1]),
    )
    drop = max(matrix.drop_scenarios, key=encoded_string_size)
    fraction = max(matrix.fractions, key=encoded_string_size)
    seed = max(matrix.seeds, key=lambda value: len(str(value)))
    row = _job_row(
        index=matrix.row_count - 1,
        iid=iid,
        dataset_split=matrix.dataset_split,
        mask=mask,
        drop=drop,
        fraction=fraction,
        seed=seed,
    )
    proof = [
        {"side": "right", "sha256": "f" * _DIGEST_HEX_LENGTH}
        for _step in _proof_shape(0, matrix.row_count)
    ]
    return len(
        canonical_json_bytes(
            {
                "schema_version": SHARD_SCHEMA_VERSION,
                "leaf_index": matrix.row_count - 1,
                "leaf_count": matrix.row_count,
                "job": row,
                "merkle_proof": proof,
            }
        )
    )


def estimate_build_disk_bytes(
    matrix: JobMatrix,
    *,
    plan_payload_bytes: int = 0,
    block_size: int = 4096,
) -> int:
    """Conservatively estimate peak temporary bytes for levels, shards, and plan."""
    if (
        not isinstance(plan_payload_bytes, int)
        or isinstance(plan_payload_bytes, bool)
        or plan_payload_bytes < 0
        or not isinstance(block_size, int)
        or isinstance(block_size, bool)
        or block_size <= 0
    ):
        raise ValueError("disk-estimate sizes must be non-negative integers")

    level_bytes = 0
    level_count = matrix.row_count
    while True:
        level_bytes += _round_up(level_count * _DIGEST_BYTES, block_size)
        if level_count == 1:
            break
        level_count = (level_count + 1) // 2
    shard_allocation = _round_up(_maximum_shard_payload_size(matrix), block_size)
    metadata_allowance = max(
        _MINIMUM_METADATA_ALLOWANCE,
        matrix.row_count * _DIRECTORY_METADATA_BYTES_PER_SHARD,
    )
    return (
        level_bytes
        + matrix.row_count * shard_allocation
        + _round_up(plan_payload_bytes, block_size)
        + metadata_allowance
    )


def _filesystem_block_size(path: Path) -> int:
    observed = os.statvfs(path).f_frsize
    return observed if observed > 0 else 4096


def _copy_json_mapping(value: object, description: str) -> dict[str, object]:
    encoded = canonical_json_bytes(value)
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ValueError(f"{description} must be a JSON object")
    return decoded


def _matrix_descriptor(
    matrix: JobMatrix,
    *,
    root_sha256: str,
    shards_directory: str,
) -> dict[str, object]:
    return {
        "ordering": ORDERING,
        "count": matrix.row_count,
        "max_jobs": matrix.max_jobs,
        "merkle_root_sha256": root_sha256,
        "shards_directory": shards_directory,
        "axes": matrix.to_json(),
    }


def _shards_directory_name(plan_path: Path, root_sha256: str) -> str:
    return f"{plan_path.name}.{root_sha256}.jobs"


def _write_new_bytes(path: Path, payload: bytes) -> None:
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


def _write_level(path: Path, digests: Iterator[bytes]) -> int:
    count = 0
    with path.open("xb") as handle:
        for digest in digests:
            if len(digest) != _DIGEST_BYTES:
                raise AssertionError("internal Merkle digest has an invalid width")
            handle.write(digest)
            count += 1
        handle.flush()
        os.fsync(handle.fileno())
    return count


def _read_exact_digest(handle: Any, index: int) -> bytes:
    handle.seek(index * _DIGEST_BYTES)
    digest = handle.read(_DIGEST_BYTES)
    if len(digest) != _DIGEST_BYTES:
        raise ValueError("temporary Merkle level is truncated")
    return digest


def _build_merkle_levels(
    matrix: JobMatrix,
    levels_directory: Path,
) -> tuple[tuple[Path, ...], tuple[int, ...], bytes]:
    levels_directory.mkdir(mode=0o700)
    level_paths: list[Path] = []
    level_counts: list[int] = []

    leaf_path = levels_directory / "level-00000000.bin"
    leaf_count = _write_level(
        leaf_path,
        (hash_job_leaf(row) for row in matrix.iter_rows()),
    )
    if leaf_count != matrix.row_count:
        raise AssertionError("job iterator did not produce the validated row count")
    level_paths.append(leaf_path)
    level_counts.append(leaf_count)

    while level_counts[-1] > 1:
        source_path = level_paths[-1]
        source_count = level_counts[-1]
        destination_path = levels_directory / f"level-{len(level_paths):08d}.bin"

        def parents() -> Iterator[bytes]:
            with source_path.open("rb") as source:
                if os.fstat(source.fileno()).st_size != source_count * _DIGEST_BYTES:
                    raise ValueError("temporary Merkle level has an invalid byte count")
                for index in range(0, source_count, 2):
                    left = _read_exact_digest(source, index)
                    right = (
                        _read_exact_digest(source, index + 1) if index + 1 < source_count else left
                    )
                    yield hash_merkle_node(left, right)

        observed_count = _write_level(destination_path, parents())
        expected_count = (source_count + 1) // 2
        if observed_count != expected_count:
            raise AssertionError("Merkle level did not produce the expected parent count")
        level_paths.append(destination_path)
        level_counts.append(observed_count)

    with level_paths[-1].open("rb") as root_handle:
        root = root_handle.read()
    if len(root) != _DIGEST_BYTES:
        raise ValueError("temporary Merkle root level is malformed")
    return tuple(level_paths), tuple(level_counts), root


def _proof_from_levels(
    row: Mapping[str, object],
    leaf_index: int,
    level_handles: Sequence[Any],
    level_counts: Sequence[int],
    expected_root: bytes,
) -> list[dict[str, str]]:
    current = hash_job_leaf(row)
    proof: list[dict[str, str]] = []
    index = leaf_index
    for handle, count in zip(level_handles, level_counts[:-1], strict=True):
        expected_size = count * _DIGEST_BYTES
        if os.fstat(handle.fileno()).st_size != expected_size:
            raise ValueError("temporary Merkle level changed during shard creation")
        sibling_index = index ^ 1
        if sibling_index >= count:
            sibling_index = index
        sibling = _read_exact_digest(handle, sibling_index)
        side = "left" if sibling_index < index else "right"
        if sibling_index == index and sibling != current:
            raise ValueError("temporary odd-leaf duplication is inconsistent")
        proof.append({"side": side, "sha256": sibling.hex()})
        current = (
            hash_merkle_node(sibling, current)
            if side == "left"
            else hash_merkle_node(current, sibling)
        )
        index //= 2
    if current != expected_root:
        raise ValueError("temporary Merkle levels do not authenticate a generated row")
    return proof


def _write_shards(
    matrix: JobMatrix,
    destination: Path,
    level_paths: Sequence[Path],
    level_counts: Sequence[int],
    root: bytes,
) -> None:
    destination.mkdir(mode=0o700)
    with ExitStack() as stack:
        handles = tuple(stack.enter_context(path.open("rb")) for path in level_paths[:-1])
        observed_count = 0
        for index, row in enumerate(matrix.iter_rows()):
            proof = _proof_from_levels(row, index, handles, level_counts, root)
            payload = {
                "schema_version": SHARD_SCHEMA_VERSION,
                "leaf_index": index,
                "leaf_count": matrix.row_count,
                "job": row,
                "merkle_proof": proof,
            }
            _write_new_bytes(
                destination / f"{index:08d}.json",
                canonical_json_bytes(payload),
            )
            observed_count += 1
        if observed_count != matrix.row_count:
            raise AssertionError("job iterator did not produce the validated row count")


def _lstat(path: Path, description: str) -> os.stat_result:
    try:
        return path.lstat()
    except FileNotFoundError:
        raise ValueError(f"{description} does not exist: {path}") from None


def _require_safe_directory(path: Path, description: str) -> None:
    observed = _lstat(path, description)
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISDIR(observed.st_mode):
        raise ValueError(f"{description} must be a non-symlink directory: {path}")


def _reject_unsafe_existing_plan(path: Path) -> None:
    try:
        observed = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
        raise ValueError(f"job plan must be a non-symlink regular file: {path}")


_FileSnapshot = tuple[int, int, int, int, int]


def _snapshot(observed: os.stat_result) -> _FileSnapshot:
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
    )


def _read_regular_bytes(path: Path, description: str) -> tuple[bytes, _FileSnapshot]:
    before_lstat = _lstat(path, description)
    if stat.S_ISLNK(before_lstat.st_mode) or not stat.S_ISREG(before_lstat.st_mode):
        raise ValueError(f"{description} must be a non-symlink regular file: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"could not safely open {description}: {path}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or _snapshot(before) != _snapshot(before_lstat):
            raise ValueError(f"{description} changed while being opened: {path}")
        if before.st_size > MAX_JSON_ARTIFACT_BYTES:
            raise ValueError(
                f"{description} exceeds the {MAX_JSON_ARTIFACT_BYTES}-byte safety limit: {path}"
            )
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if _snapshot(after) != _snapshot(before):
            raise ValueError(f"{description} changed while being read: {path}")
        payload = b"".join(chunks)
        if len(payload) != before.st_size:
            raise ValueError(f"{description} was truncated while being read: {path}")
    finally:
        os.close(descriptor)
    snapshot = _snapshot(before_lstat)
    if _snapshot(_lstat(path, description)) != snapshot:
        raise ValueError(f"{description} changed after being read: {path}")
    return payload, snapshot


def _read_canonical_json(
    path: Path,
    description: str,
) -> tuple[object, _FileSnapshot]:
    encoded, snapshot = _read_regular_bytes(path, description)
    try:
        payload = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{description} is not valid JSON: {path}: {exc}") from exc
    if canonical_json_bytes(payload) != encoded:
        raise ValueError(f"{description} is not canonical JSON: {path}")
    return payload, snapshot


def _assert_unchanged(path: Path, snapshot: _FileSnapshot, description: str) -> None:
    if _snapshot(_lstat(path, description)) != snapshot:
        raise ValueError(f"{description} changed during authentication: {path}")


def _files_identical(left: Path, right: Path) -> bool:
    left_payload, _left_snapshot = _read_regular_bytes(left, "temporary job shard")
    right_payload, _right_snapshot = _read_regular_bytes(right, "existing job shard")
    return left_payload == right_payload


def _existing_shards_match(
    temporary: Path,
    destination: Path,
    count: int,
) -> bool:
    _require_safe_directory(destination, "existing job-shard directory")
    observed_count = 0
    with os.scandir(destination) as entries:
        for entry in entries:
            stem, suffix = os.path.splitext(entry.name)
            if (
                suffix != ".json"
                or len(stem) != 8
                or not stem.isascii()
                or not stem.isdigit()
                or int(stem) >= count
                or entry.is_symlink()
                or not entry.is_file(follow_symlinks=False)
            ):
                return False
            observed_count += 1
    if observed_count != count:
        return False
    return all(
        _files_identical(
            temporary / f"{index:08d}.json",
            destination / f"{index:08d}.json",
        )
        for index in range(count)
    )


def _publish(source: Path, destination: Path) -> None:
    if source.is_dir():
        _fsync_directory(source)
    os.replace(source, destination)
    _fsync_directory(destination.parent)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class PlanBuildResult:
    configuration: dict[str, object]
    configuration_sha256: str
    merkle_root_sha256: str
    row_count: int
    shards_directory: str
    estimated_disk_bytes: int


@dataclass(frozen=True, slots=True)
class _PlanDestinationAuthority:
    parent: Path
    directory_fd: int
    device: int
    inode: int
    plan_name: str

    @property
    def pinned_parent(self) -> Path:
        return Path(f"/proc/self/fd/{self.directory_fd}")


def _assert_plan_parent(authority: _PlanDestinationAuthority) -> None:
    """Require the public parent pathname to retain the locked directory inode."""
    descriptor_metadata = os.fstat(authority.directory_fd)
    try:
        path_metadata = authority.parent.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ValueError("job-plan parent disappeared during publication") from exc
    expected = (authority.device, authority.inode)
    if (descriptor_metadata.st_dev, descriptor_metadata.st_ino) != expected or (
        path_metadata.st_dev,
        path_metadata.st_ino,
    ) != expected:
        raise ValueError("job-plan parent changed during publication")


@contextmanager
def _locked_plan_destination(plan_path: Path) -> Iterator[_PlanDestinationAuthority]:
    parent_metadata = plan_path.parent.lstat()
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    directory_fd = os.open(plan_path.parent, directory_flags)
    opened_parent = os.fstat(directory_fd)
    if (
        stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or (parent_metadata.st_dev, parent_metadata.st_ino)
        != (opened_parent.st_dev, opened_parent.st_ino)
    ):
        os.close(directory_fd)
        raise ValueError(f"job-plan parent changed while it was opened: {plan_path.parent}")
    authority = _PlanDestinationAuthority(
        parent=plan_path.parent,
        directory_fd=directory_fd,
        device=opened_parent.st_dev,
        inode=opened_parent.st_ino,
        plan_name=plan_path.name,
    )
    lock_name = f".{plan_path.name}.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_name, flags, 0o600, dir_fd=directory_fd)
    except Exception:
        os.close(directory_fd)
        raise
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        os.close(descriptor)
        os.close(directory_fd)
        raise ValueError(
            f"job-plan lock is not a private regular file: {plan_path.parent / lock_name}"
        )
    handle = os.fdopen(descriptor, "a+b", closefd=True)
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(f"job-plan destination is already locked: {plan_path}") from None
        _assert_plan_parent(authority)
        yield authority
    finally:
        handle.close()
        os.close(directory_fd)


def build_job_plan(
    path: Path,
    *,
    configuration: Mapping[str, object],
    input_verification: Mapping[str, object],
    dataset_split: object,
    fixture_masks: object,
    drop_scenarios: object,
    fractions: object,
    seeds: object,
    max_jobs: object = DEFAULT_MAX_JOBS,
    disk_reserve_bytes: object = DEFAULT_DISK_RESERVE_BYTES,
) -> PlanBuildResult:
    """Build an idempotent schema-v3 plan while holding its destination lock."""
    plan_path = Path(path)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    _require_safe_directory(plan_path.parent, "job-plan parent")
    with _locked_plan_destination(plan_path) as authority:
        return _build_job_plan_locked(
            plan_path,
            authority=authority,
            configuration=configuration,
            input_verification=input_verification,
            dataset_split=dataset_split,
            fixture_masks=fixture_masks,
            drop_scenarios=drop_scenarios,
            fractions=fractions,
            seeds=seeds,
            max_jobs=max_jobs,
            disk_reserve_bytes=disk_reserve_bytes,
        )


def _build_job_plan_locked(
    path: Path,
    *,
    authority: _PlanDestinationAuthority,
    configuration: Mapping[str, object],
    input_verification: Mapping[str, object],
    dataset_split: object,
    fixture_masks: object,
    drop_scenarios: object,
    fractions: object,
    seeds: object,
    max_jobs: object = DEFAULT_MAX_JOBS,
    disk_reserve_bytes: object = DEFAULT_DISK_RESERVE_BYTES,
) -> PlanBuildResult:
    """Build and atomically publish a schema-v3 plan plus authenticated shards."""
    public_plan_path = Path(path)
    if public_plan_path.name != authority.plan_name or public_plan_path.parent != authority.parent:
        raise ValueError("job-plan publication authority does not match destination")
    _assert_plan_parent(authority)
    parent = authority.pinned_parent
    plan_path = parent / authority.plan_name
    _reject_unsafe_existing_plan(plan_path)
    if (
        not isinstance(disk_reserve_bytes, int)
        or isinstance(disk_reserve_bytes, bool)
        or disk_reserve_bytes < 0
    ):
        raise ValueError("disk_reserve_bytes must be a non-negative integer")

    matrix = JobMatrix.create(
        dataset_split=dataset_split,
        fixture_masks=fixture_masks,
        drop_scenarios=drop_scenarios,
        fractions=fractions,
        seeds=seeds,
        max_jobs=max_jobs,
    )
    base_configuration = _copy_json_mapping(configuration, "configuration")
    if "job_matrix" in base_configuration or "input_verification_sha256" in base_configuration:
        raise ValueError("configuration must not predefine job-plan-managed fields")
    verification = _copy_json_mapping(input_verification, "input verification")

    placeholder_root = "0" * _DIGEST_HEX_LENGTH
    placeholder_directory = _shards_directory_name(plan_path, placeholder_root)
    estimated_configuration = dict(base_configuration)
    estimated_configuration["job_matrix"] = _matrix_descriptor(
        matrix,
        root_sha256=placeholder_root,
        shards_directory=placeholder_directory,
    )
    estimated_configuration["input_verification_sha256"] = sha256_json(verification)
    estimated_plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "configuration": estimated_configuration,
        "input_verification": verification,
    }
    block_size = _filesystem_block_size(parent)
    estimated_disk_bytes = estimate_build_disk_bytes(
        matrix,
        plan_payload_bytes=len(canonical_json_bytes(estimated_plan)),
        block_size=block_size,
    )
    available = shutil.disk_usage(parent).free
    required = estimated_disk_bytes + disk_reserve_bytes
    if available < required:
        raise OSError(
            f"insufficient disk space for job-plan build: need {required} bytes "
            f"including reserve, have {available}"
        )

    temporary_root = Path(tempfile.mkdtemp(prefix=f".{plan_path.name}.v3.", dir=parent))
    published_shards: Path | None = None
    published_plan: Path | None = None
    plan_published = False
    try:
        level_paths, level_counts, root = _build_merkle_levels(
            matrix,
            temporary_root / "levels",
        )
        root_sha256 = root.hex()
        shards_directory = _shards_directory_name(plan_path, root_sha256)
        destination = parent / shards_directory
        temporary_shards = temporary_root / "shards"

        final_configuration = dict(base_configuration)
        final_configuration["job_matrix"] = _matrix_descriptor(
            matrix,
            root_sha256=root_sha256,
            shards_directory=shards_directory,
        )
        final_configuration["input_verification_sha256"] = sha256_json(verification)
        plan_payload = {
            "schema_version": PLAN_SCHEMA_VERSION,
            "configuration": final_configuration,
            "input_verification": verification,
        }
        temporary_plan = temporary_root / "plan.json"
        _write_new_bytes(temporary_plan, canonical_json_bytes(plan_payload))
        _write_shards(
            matrix,
            temporary_shards,
            level_paths,
            level_counts,
            root,
        )

        try:
            destination.lstat()
        except FileNotFoundError:
            _assert_plan_parent(authority)
            _publish(temporary_shards, destination)
            published_shards = destination
            _assert_plan_parent(authority)
        else:
            if not _existing_shards_match(temporary_shards, destination, matrix.row_count):
                raise ValueError(
                    f"existing job-shard directory is unsafe or inconsistent: {destination}"
                )

        try:
            plan_path.lstat()
        except FileNotFoundError:
            _assert_plan_parent(authority)
            _publish(temporary_plan, plan_path)
            published_plan = plan_path
            _assert_plan_parent(authority)
        else:
            if not _files_identical(temporary_plan, plan_path):
                raise ValueError(
                    f"existing job plan has a different authenticated identity: {plan_path}"
                )
            temporary_plan.unlink()
        _assert_plan_parent(authority)
        plan_published = True
        return PlanBuildResult(
            configuration=final_configuration,
            configuration_sha256=sha256_json(final_configuration),
            merkle_root_sha256=root_sha256,
            row_count=matrix.row_count,
            shards_directory=shards_directory,
            estimated_disk_bytes=estimated_disk_bytes,
        )
    finally:
        if published_plan is not None and not plan_published:
            published_plan.unlink(missing_ok=True)
        if published_shards is not None and not plan_published:
            shutil.rmtree(published_shards, ignore_errors=True)
        shutil.rmtree(temporary_root, ignore_errors=True)


def _read_matrix(raw: object, plan_path: Path) -> tuple[JobMatrix, str, str]:
    expected_keys = {
        "ordering",
        "count",
        "max_jobs",
        "merkle_root_sha256",
        "shards_directory",
        "axes",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected_keys:
        raise ValueError("job matrix has an invalid schema")
    if raw["ordering"] != ORDERING:
        raise ValueError("job matrix has an unsupported ordering")
    max_jobs = _validate_max_jobs(raw["max_jobs"])
    matrix = JobMatrix.from_json(raw["axes"], max_jobs=max_jobs)
    count = raw["count"]
    if not isinstance(count, int) or isinstance(count, bool) or count != matrix.row_count:
        raise ValueError("job matrix count does not match its Cartesian axes")
    root = raw["merkle_root_sha256"]
    if not _is_sha256(root):
        raise ValueError("job matrix has an invalid Merkle root")
    directory = raw["shards_directory"]
    expected_directory = _shards_directory_name(plan_path, root)
    if directory != expected_directory:
        raise ValueError("job matrix has an invalid shard-directory binding")
    return matrix, root, directory


def read_job_plan(
    path: Path,
    expected_configuration_sha256: object,
    job_index: object,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """Authenticate one schema-v3 shard and reconstruct its expected Cartesian row."""
    plan_path = Path(path)
    if not _is_sha256(expected_configuration_sha256):
        raise ValueError("expected configuration SHA-256 is invalid")
    raw_plan, plan_snapshot = _read_canonical_json(plan_path, "job plan")
    if (
        not isinstance(raw_plan, Mapping)
        or set(raw_plan) != {"schema_version", "configuration", "input_verification"}
        or raw_plan.get("schema_version") != PLAN_SCHEMA_VERSION
    ):
        raise ValueError(f"{plan_path}: expected job-plan schema version 3")
    raw_configuration = raw_plan["configuration"]
    raw_verification = raw_plan["input_verification"]
    if not isinstance(raw_configuration, Mapping):
        raise ValueError("job-plan configuration must be an object")
    if sha256_json(raw_configuration) != expected_configuration_sha256:
        raise ValueError("configuration SHA-256 does not match expected value")
    if not isinstance(raw_verification, Mapping):
        raise ValueError("job-plan input verification must be an object")
    if raw_configuration.get("input_verification_sha256") != sha256_json(raw_verification):
        raise ValueError("job-plan input-verification digest is invalid")

    matrix, root, directory_name = _read_matrix(
        raw_configuration.get("job_matrix"),
        plan_path,
    )
    expected_row = matrix.row_at(job_index)
    shard_directory = plan_path.parent / directory_name
    _require_safe_directory(shard_directory, "job-shard directory")
    shard_path = shard_directory / f"{job_index:08d}.json"
    raw_shard, shard_snapshot = _read_canonical_json(shard_path, "job shard")
    raw_leaf_index = raw_shard.get("leaf_index") if isinstance(raw_shard, Mapping) else None
    raw_leaf_count = raw_shard.get("leaf_count") if isinstance(raw_shard, Mapping) else None
    if (
        not isinstance(raw_shard, Mapping)
        or set(raw_shard) != {"schema_version", "leaf_index", "leaf_count", "job", "merkle_proof"}
        or raw_shard.get("schema_version") != SHARD_SCHEMA_VERSION
        or not isinstance(raw_leaf_index, int)
        or isinstance(raw_leaf_index, bool)
        or raw_leaf_index != job_index
        or not isinstance(raw_leaf_count, int)
        or isinstance(raw_leaf_count, bool)
        or raw_leaf_count != matrix.row_count
    ):
        raise ValueError("job shard has an invalid schema or leaf index/count")
    raw_row = raw_shard["job"]
    if not isinstance(raw_row, Mapping) or canonical_json_bytes(raw_row) != canonical_json_bytes(
        expected_row
    ):
        raise ValueError("job shard row does not match the Cartesian axes")
    if not verify_merkle_proof(
        expected_row,
        leaf_index=job_index,
        leaf_count=matrix.row_count,
        proof=raw_shard["merkle_proof"],
        expected_root_sha256=root,
    ):
        raise ValueError("job shard has an invalid Merkle proof")

    _assert_unchanged(shard_path, shard_snapshot, "job shard")
    _assert_unchanged(plan_path, plan_snapshot, "job plan")
    return dict(raw_configuration), expected_row, dict(raw_verification)
