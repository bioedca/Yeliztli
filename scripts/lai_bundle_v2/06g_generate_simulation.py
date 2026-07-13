#!/usr/bin/env python3
"""Generate deterministic, donor-replayable local-ancestry simulations.

The generator builds phased synthetic genomes as mosaics of public founder
haplotypes at the exact markers used by the frozen production Gnomix models.
Every biological and acceptance parameter comes from a versioned design JSON;
there are no scientific defaults.  Recombination breakpoints follow a
single-pulse, homogeneous Poisson process on the supplied genetic maps.  A
bounded, deterministic rejection loop enforces predeclared composition,
founder-representation, and breakpoint constraints plus the verifier's fixed
tract-count safety ceiling.

Outputs include five-column genotype fixtures, marker-level donor/haplotype
truth, gap-free founder tracts, model-window ancestry truth, split-scoped label
files, and a schema-v2 manifest.  The output directory is published only after
all inputs remain unchanged and every artifact has been hashed.  The separate
``06g_verify_simulation.py`` program must still replay the source VCF alleles;
this generator never self-verifies its output.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import fcntl
import gzip
import hashlib
import io
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import TextIO

import numpy as np

AUTOSOMES = tuple(str(chrom) for chrom in range(1, 23))
AUTOSOME_SET = frozenset(AUTOSOMES)
SUPERPOPULATIONS = frozenset({"AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE"})
DNA_BASES = frozenset({"A", "C", "G", "T"})
SPLITS = ("calibration", "final_confirmation")
GENERATOR_NAME = "yeliztli-founder-mosaic-v1"
MAX_SIMULATIONS = 64
MAX_BREAKPOINTS = 1_000
MAX_TRACTS_PER_SIMULATION = 20_000
MAX_ATTEMPTS = 10_000
MAX_JSON_BYTES = 16 * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024
IID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")
VCF_FIXED_HEADER = ("#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT")
MARKER_TRUTH_HEADER = (
    "sim_iid",
    "chrom",
    "marker_index",
    "position_grch38",
    "rsid",
    "hap0_donor_iid",
    "hap0_source_hap",
    "hap1_donor_iid",
    "hap1_source_hap",
)
TRACT_TRUTH_HEADER = (
    "sim_iid",
    "chrom",
    "haplotype",
    "start_marker_index",
    "end_marker_index_exclusive",
    "donor_iid",
    "source_hap",
)
FIXTURE_HEADER = ("rsid", "chromosome", "position", "allele1", "allele2")
WINDOW_TRUTH_HEADER = ("chrom", "start", "end", "hap0", "hap1")


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    path: Path
    sha256: str
    size_bytes: int
    signature: tuple[int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class ModelSpec:
    chrom: str
    positions: np.ndarray
    refs: tuple[str, ...]
    alts: tuple[str, ...]
    population_order: tuple[str, ...]
    marker_count: int
    window_size: int
    window_count: int
    marker_cm: np.ndarray
    metadata_sha256: str
    map_sha256: str


@dataclass(frozen=True, slots=True)
class SimulationSpec:
    iid: str
    split: str
    generation: int
    validation_stratum: str
    donor_iids: tuple[str, ...]
    target_fractions: Mapping[str, Decimal]
    target_fraction_strings: Mapping[str, str]
    tolerance: Decimal
    tolerance_string: str


@dataclass(frozen=True, slots=True)
class Design:
    dataset_id: str
    source_bundle_artifact_sha256: str
    seed: int
    max_attempts: int
    allowed_generations: tuple[int, ...]
    max_breakpoints: Mapping[str, int]
    minimums: Mapping[str, int]
    donor_metadata: Mapping[str, str]
    simulations: tuple[SimulationSpec, ...]


@dataclass(frozen=True, slots=True)
class Tract:
    start: int
    end: int
    donor_iid: str
    source_hap: int


@dataclass(frozen=True, slots=True)
class WindowTruth:
    chrom: str
    start: int
    end: int
    hap0: str
    hap1: str


@dataclass(frozen=True, slots=True)
class GeneratedSimulation:
    spec: SimulationSpec
    tracts: Mapping[tuple[str, int], tuple[Tract, ...]]
    windows: tuple[WindowTruth, ...]
    ancestry_marker_counts: Mapping[str, int]
    realized_fractions: Mapping[str, str]
    modal_founders: Mapping[str, frozenset[str]]


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None


def _canonical_autosome(raw: str) -> str | None:
    token = raw.strip()
    if token.lower().startswith("chr"):
        token = token[3:]
    try:
        normalized = str(int(token))
    except ValueError:
        return None
    return normalized if normalized in AUTOSOME_SET else None


def _absolute_without_symlinks(path: Path, *, label: str) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path.expanduser())))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"{label}: symlink path component is not allowed: {current}")
    return absolute


def _stat_signature(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _regular_file(path: Path, *, label: str) -> tuple[Path, os.stat_result]:
    absolute = _absolute_without_symlinks(path, label=label)
    try:
        metadata = absolute.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"{label}: file does not exist: {absolute}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label}: expected a non-symlink regular file: {absolute}")
    return absolute, metadata


def _open_descriptor(path: Path, *, label: str) -> io.BufferedReader:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{label}: could not safely open {path}: {exc}") from exc
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise ValueError(f"{label}: opened object is not a regular file: {path}")
    return io.BufferedReader(io.FileIO(descriptor, mode="rb", closefd=True))


def _snapshot_file(path: Path, *, label: str) -> FileSnapshot:
    absolute, before = _regular_file(path, label=label)
    digest = hashlib.sha256()
    size = 0
    with _open_descriptor(absolute, label=label) as handle:
        opened = os.fstat(handle.fileno())
        if _stat_signature(opened) != _stat_signature(before):
            raise ValueError(f"{label}: file changed while it was opened: {absolute}")
        while chunk := handle.read(READ_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
        closed_signature = _stat_signature(os.fstat(handle.fileno()))
    _, after = _regular_file(absolute, label=label)
    signature = _stat_signature(after)
    if (
        signature != _stat_signature(before)
        or signature != closed_signature
        or size != after.st_size
    ):
        raise ValueError(f"{label}: file changed while it was hashed: {absolute}")
    return FileSnapshot(absolute, digest.hexdigest(), size, signature)


def _assert_snapshot_unchanged(snapshot: FileSnapshot, *, label: str) -> None:
    _, metadata = _regular_file(snapshot.path, label=label)
    if _stat_signature(metadata) != snapshot.signature:
        raise ValueError(f"{label}: file changed during generation: {snapshot.path}")


@contextlib.contextmanager
def _open_snapshot_binary(snapshot: FileSnapshot, *, label: str) -> Iterator[io.BufferedReader]:
    handle = _open_descriptor(snapshot.path, label=label)
    try:
        if _stat_signature(os.fstat(handle.fileno())) != snapshot.signature:
            raise ValueError(f"{label}: file changed before parsing: {snapshot.path}")
        yield handle
        if _stat_signature(os.fstat(handle.fileno())) != snapshot.signature:
            raise ValueError(f"{label}: file changed while parsing: {snapshot.path}")
    finally:
        handle.close()
    _assert_snapshot_unchanged(snapshot, label=label)


@contextlib.contextmanager
def _open_snapshot_text(
    snapshot: FileSnapshot,
    *,
    label: str,
    allow_gzip: bool = False,
) -> Iterator[TextIO]:
    raw = _open_descriptor(snapshot.path, label=label)
    text: TextIO | None = None
    try:
        if _stat_signature(os.fstat(raw.fileno())) != snapshot.signature:
            raise ValueError(f"{label}: file changed before parsing: {snapshot.path}")
        magic = raw.peek(2)[:2]
        binary: io.BufferedIOBase
        if magic == b"\x1f\x8b":
            if not allow_gzip:
                raise ValueError(f"{label}: gzip input is not supported: {snapshot.path}")
            binary = gzip.GzipFile(fileobj=raw, mode="rb")
        elif magic.startswith(b"BC"):
            raise ValueError(f"{label}: binary BCF is not supported; use textual VCF/VCF.gz")
        else:
            binary = raw
        text = io.TextIOWrapper(binary, encoding="utf-8", errors="strict", newline=None)
        yield text
        if not raw.closed and _stat_signature(os.fstat(raw.fileno())) != snapshot.signature:
            raise ValueError(f"{label}: file changed while parsing: {snapshot.path}")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{label}: could not decode {snapshot.path}: {exc}") from exc
    finally:
        if text is not None:
            text.close()
        if not raw.closed:
            raw.close()
    _assert_snapshot_unchanged(snapshot, label=label)


def _read_json(snapshot: FileSnapshot, *, label: str) -> Mapping[str, object]:
    if snapshot.size_bytes > MAX_JSON_BYTES:
        raise ValueError(f"{label}: JSON exceeds {MAX_JSON_BYTES} bytes")
    with _open_snapshot_binary(snapshot, label=label) as handle:
        raw = handle.read()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label}: invalid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"{label}: top-level JSON value must be an object")
    return value


def _decimal_string(raw: object, *, label: str) -> tuple[Decimal, str]:
    if not isinstance(raw, str):
        raise ValueError(f"{label}: expected a canonical decimal string")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"{label}: invalid decimal") from None
    canonical = str(value.normalize())
    if not value.is_finite() or raw != canonical:
        raise ValueError(f"{label}: expected canonical finite decimal spelling")
    return value, canonical


def _require_exact_keys(value: Mapping[str, object], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ValueError(f"{label}: keys are not exact; missing={missing}, extra={extra}")


def _parse_design(payload: Mapping[str, object]) -> Design:
    expected_top = {
        "schema_version",
        "dataset_id",
        "source_bundle_artifact_sha256",
        "seed",
        "model_frozen_before_generation",
        "max_attempts_per_simulation",
        "allowed_generations",
        "max_breakpoints_per_haplotype_by_autosome",
        "minimums",
        "donor_metadata",
        "simulations",
    }
    _require_exact_keys(payload, expected_top, label="design")
    if payload.get("schema_version") != 1:
        raise ValueError("design: expected schema_version 1")
    if payload.get("model_frozen_before_generation") is not True:
        raise ValueError("design: model_frozen_before_generation must be true")
    dataset_id = payload.get("dataset_id")
    if (
        not isinstance(dataset_id, str)
        or not dataset_id
        or dataset_id != dataset_id.strip()
        or "\x00" in dataset_id
    ):
        raise ValueError("design: dataset_id must be non-empty")
    bundle_hash = payload.get("source_bundle_artifact_sha256")
    if not _is_sha256(bundle_hash):
        raise ValueError("design: source_bundle_artifact_sha256 is invalid")
    seed = payload.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0 or seed > (1 << 64) - 1:
        raise ValueError("design: seed must be an unsigned 64-bit integer")
    max_attempts = payload.get("max_attempts_per_simulation")
    if (
        not isinstance(max_attempts, int)
        or isinstance(max_attempts, bool)
        or not 1 <= max_attempts <= MAX_ATTEMPTS
    ):
        raise ValueError(f"design: max attempts must be 1..{MAX_ATTEMPTS}")
    raw_generations = payload.get("allowed_generations")
    if (
        not isinstance(raw_generations, list)
        or len(raw_generations) < 2
        or raw_generations != sorted(raw_generations)
        or len(raw_generations) != len(set(raw_generations))
        or not all(
            isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 10_000
            for value in raw_generations
        )
    ):
        raise ValueError(
            "design: allowed_generations must be at least two sorted positive integers"
        )
    raw_breakpoints = payload.get("max_breakpoints_per_haplotype_by_autosome")
    if not isinstance(raw_breakpoints, Mapping) or set(raw_breakpoints) != AUTOSOME_SET:
        raise ValueError("design: breakpoint maxima must cover autosomes 1..22 exactly")
    max_breakpoints: dict[str, int] = {}
    for chrom in AUTOSOMES:
        value = raw_breakpoints[chrom]
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= MAX_BREAKPOINTS
        ):
            raise ValueError(f"design: chr{chrom} breakpoint maximum must be 0..{MAX_BREAKPOINTS}")
        max_breakpoints[chrom] = value
    raw_minimums = payload.get("minimums")
    minimum_keys = {
        "founders_per_class_per_split",
        "simulations_per_class_per_split",
        "truth_haplotype_windows_per_class_per_split",
    }
    if not isinstance(raw_minimums, Mapping):
        raise ValueError("design: minimums must be an object")
    _require_exact_keys(raw_minimums, minimum_keys, label="design.minimums")
    floors = {
        "founders_per_class_per_split": 2,
        "simulations_per_class_per_split": 2,
        "truth_haplotype_windows_per_class_per_split": 1,
    }
    minimums: dict[str, int] = {}
    for field, floor in floors.items():
        value = raw_minimums[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < floor:
            raise ValueError(f"design.minimums.{field} must be at least {floor}")
        minimums[field] = value
    raw_metadata = payload.get("donor_metadata")
    metadata_keys = {
        "source_url",
        "iid_field",
        "population_field",
        "model_class_field",
        "release_field",
        "hard_filtered_field",
        "release_related_field",
        "all_samples_related_field",
    }
    if not isinstance(raw_metadata, Mapping):
        raise ValueError("design: donor_metadata must be an object")
    _require_exact_keys(raw_metadata, metadata_keys, label="design.donor_metadata")
    donor_metadata = {field: raw_metadata[field] for field in sorted(metadata_keys)}
    if any(
        not isinstance(value, str) or not value or value != value.strip()
        for value in donor_metadata.values()
    ):
        raise ValueError("design: donor_metadata strings must be non-empty")
    field_values = [donor_metadata[field] for field in metadata_keys if field != "source_url"]
    if len(field_values) != len(set(field_values)):
        raise ValueError("design: donor metadata field names must be distinct")
    raw_simulations = payload.get("simulations")
    if not isinstance(raw_simulations, list) or not 1 <= len(raw_simulations) <= MAX_SIMULATIONS:
        raise ValueError(f"design: simulations must contain 1..{MAX_SIMULATIONS} entries")
    simulations: list[SimulationSpec] = []
    seen_iids: set[str] = set()
    simulation_keys = {
        "iid",
        "split",
        "generation",
        "validation_stratum",
        "donor_iids",
        "target_marker_ancestry_fractions",
        "fraction_absolute_tolerance",
    }
    for index, raw in enumerate(raw_simulations):
        if not isinstance(raw, Mapping):
            raise ValueError(f"design.simulations[{index}]: expected object")
        _require_exact_keys(raw, simulation_keys, label=f"design.simulations[{index}]")
        iid = raw.get("iid")
        if not isinstance(iid, str) or IID_PATTERN.fullmatch(iid) is None or iid in seen_iids:
            raise ValueError(f"design.simulations[{index}]: invalid or duplicate IID")
        seen_iids.add(iid)
        split = raw.get("split")
        if split not in SPLITS:
            raise ValueError(f"design simulation {iid!r}: invalid split")
        generation = raw.get("generation")
        if generation not in raw_generations or isinstance(generation, bool):
            raise ValueError(f"design simulation {iid!r}: generation is not predeclared")
        stratum = raw.get("validation_stratum")
        if (
            not isinstance(stratum, str)
            or not stratum
            or stratum != stratum.strip()
            or any(token in stratum for token in ("\t", "\r", "\n", "\x00"))
        ):
            raise ValueError(f"design simulation {iid!r}: invalid validation stratum")
        raw_donors = raw.get("donor_iids")
        if (
            not isinstance(raw_donors, list)
            or not raw_donors
            or raw_donors != sorted(raw_donors)
            or len(raw_donors) != len(set(raw_donors))
            or not all(
                isinstance(donor, str) and IID_PATTERN.fullmatch(donor) for donor in raw_donors
            )
        ):
            raise ValueError(f"design simulation {iid!r}: donor_iids must be canonical and unique")
        raw_target = raw.get("target_marker_ancestry_fractions")
        if not isinstance(raw_target, Mapping) or set(raw_target) != SUPERPOPULATIONS:
            raise ValueError(
                f"design simulation {iid!r}: target fractions must cover seven classes"
            )
        targets: dict[str, Decimal] = {}
        target_strings: dict[str, str] = {}
        target_sum = Decimal(0)
        for ancestry in sorted(SUPERPOPULATIONS):
            value, canonical = _decimal_string(
                raw_target[ancestry],
                label=f"design simulation {iid!r} target {ancestry}",
            )
            if value <= 0:
                raise ValueError(f"design simulation {iid!r}: every class target must be positive")
            targets[ancestry] = value
            target_strings[ancestry] = canonical
            target_sum += value
        if target_sum != Decimal(1):
            raise ValueError(f"design simulation {iid!r}: target fractions must sum to one")
        tolerance, tolerance_string = _decimal_string(
            raw.get("fraction_absolute_tolerance"),
            label=f"design simulation {iid!r} fraction tolerance",
        )
        if not Decimal(0) < tolerance <= Decimal("0.1"):
            raise ValueError(f"design simulation {iid!r}: tolerance must be in (0, 0.1]")
        simulations.append(
            SimulationSpec(
                iid=iid,
                split=str(split),
                generation=int(generation),
                validation_stratum=stratum,
                donor_iids=tuple(raw_donors),
                target_fractions=targets,
                target_fraction_strings=target_strings,
                tolerance=tolerance,
                tolerance_string=tolerance_string,
            )
        )
    simulations.sort(key=lambda item: item.iid)
    for split in SPLITS:
        selected = [simulation for simulation in simulations if simulation.split == split]
        if not selected:
            raise ValueError(f"design: split {split!r} must be non-empty")
        observed = sorted({simulation.generation for simulation in selected})
        if observed != list(raw_generations):
            raise ValueError(
                f"design: split {split!r} generations {observed} do not match "
                f"allowed_generations {raw_generations}"
            )
        if len(selected) < minimums["simulations_per_class_per_split"]:
            raise ValueError(
                f"design: split {split!r} has fewer simulations than the declared minimum"
            )
    split_donors = {
        split: {
            donor
            for simulation in simulations
            if simulation.split == split
            for donor in simulation.donor_iids
        }
        for split in SPLITS
    }
    overlap = sorted(split_donors["calibration"] & split_donors["final_confirmation"])
    if overlap:
        raise ValueError(f"design: founder donors occur in both splits: {overlap}")
    return Design(
        dataset_id=dataset_id.strip(),
        source_bundle_artifact_sha256=str(bundle_hash),
        seed=seed,
        max_attempts=max_attempts,
        allowed_generations=tuple(raw_generations),
        max_breakpoints=max_breakpoints,
        minimums=minimums,
        donor_metadata=donor_metadata,
        simulations=tuple(simulations),
    )


def _parse_chromosome_paths(values: Sequence[str], *, option: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for raw in values:
        key, separator, raw_path = raw.partition("=")
        chrom = _canonical_autosome(key) if separator else None
        if chrom is None or not raw_path:
            raise ValueError(f"{option}: expected CHROM=PATH, got {raw!r}")
        if chrom in result:
            raise ValueError(f"{option}: duplicate chromosome {chrom}")
        result[chrom] = Path(raw_path)
    if set(result) != AUTOSOME_SET:
        raise ValueError(f"{option}: must cover chromosomes 1..22 exactly")
    return result


def _as_text(value: object, *, label: str) -> str:
    if isinstance(value, (bytes, np.bytes_)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{label}: invalid UTF-8 string") from exc
    return str(value)


def _read_map(snapshot: FileSnapshot, *, chrom: str) -> tuple[np.ndarray, np.ndarray]:
    positions: list[int] = []
    positions_cm: list[float] = []
    with _open_snapshot_text(snapshot, label=f"genetic map chr{chrom}") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) != 4:
                raise ValueError(f"{snapshot.path}:{line_number}: expected four PLINK columns")
            row_chrom = _canonical_autosome(fields[0])
            try:
                cm = float(fields[2])
                position = int(fields[3])
            except ValueError:
                raise ValueError(
                    f"{snapshot.path}:{line_number}: invalid map coordinate"
                ) from None
            if row_chrom != chrom or position <= 0 or not math.isfinite(cm) or cm < 0:
                raise ValueError(f"{snapshot.path}:{line_number}: invalid chr{chrom} map row")
            if positions and position <= positions[-1]:
                raise ValueError(f"{snapshot.path}:{line_number}: map positions must increase")
            if positions_cm and cm < positions_cm[-1]:
                raise ValueError(f"{snapshot.path}:{line_number}: map cM must not decrease")
            positions.append(position)
            positions_cm.append(cm)
    if len(positions) < 2 or positions_cm[-1] <= positions_cm[0]:
        raise ValueError(f"{snapshot.path}: map needs at least two rows and positive genetic span")
    return np.asarray(positions, dtype=np.int64), np.asarray(positions_cm, dtype=np.float64)


def _load_model(
    metadata_snapshot: FileSnapshot,
    map_snapshot: FileSnapshot,
    *,
    chrom: str,
) -> ModelSpec:
    with _open_snapshot_binary(metadata_snapshot, label=f"model metadata chr{chrom}") as handle:
        try:
            with np.load(handle, allow_pickle=False) as metadata:
                required = {"C", "M", "W", "snp_pos", "snp_ref", "snp_alt", "population_order"}
                if not required <= set(metadata.files):
                    raise ValueError(f"{metadata_snapshot.path}: incomplete Gnomix metadata")
                marker_count = int(metadata["C"].item())
                window_size = int(metadata["M"].item())
                window_count = int(metadata["W"].item())
                raw_positions = np.asarray(metadata["snp_pos"])
                raw_refs = np.asarray(metadata["snp_ref"])
                raw_alts = np.asarray(metadata["snp_alt"])
                population_order = tuple(
                    _as_text(value, label=f"model chr{chrom} population_order")
                    for value in np.asarray(metadata["population_order"])
                )
        except (OSError, KeyError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(metadata_snapshot.path) in str(exc):
                raise
            raise ValueError(f"{metadata_snapshot.path}: invalid NumPy metadata: {exc}") from exc
    if (
        marker_count <= 0
        or window_size <= 0
        or window_count <= 0
        or window_count != marker_count // window_size
    ):
        raise ValueError(f"{metadata_snapshot.path}: inconsistent C/M/W")
    if raw_positions.ndim != 1 or not np.issubdtype(raw_positions.dtype, np.integer):
        raise ValueError(
            f"{metadata_snapshot.path}: snp_pos must be a one-dimensional integer array"
        )
    positions = np.asarray(raw_positions, dtype=np.int64).copy()
    if len(positions) != marker_count or np.any(positions <= 0):
        raise ValueError(f"{metadata_snapshot.path}: snp_pos length/value does not match C")
    if marker_count > 1 and not bool(np.all(positions[1:] > positions[:-1])):
        raise ValueError(f"{metadata_snapshot.path}: snp_pos must be strictly increasing")
    if (
        raw_refs.ndim != 1
        or raw_alts.ndim != 1
        or len(raw_refs) != marker_count
        or len(raw_alts) != marker_count
    ):
        raise ValueError(f"{metadata_snapshot.path}: snp_ref/snp_alt lengths must equal C")
    refs = tuple(_as_text(value, label="snp_ref").upper() for value in raw_refs)
    alts = tuple(_as_text(value, label="snp_alt").upper() for value in raw_alts)
    if any(
        ref not in DNA_BASES or alt not in DNA_BASES or ref == alt
        for ref, alt in zip(refs, alts, strict=True)
    ):
        raise ValueError(f"{metadata_snapshot.path}: model markers must be biallelic SNVs")
    if len(population_order) != 7 or set(population_order) != SUPERPOPULATIONS:
        raise ValueError(
            f"{metadata_snapshot.path}: population_order must be a seven-class permutation"
        )
    map_positions, map_cm = _read_map(map_snapshot, chrom=chrom)
    if positions[0] < map_positions[0] or positions[-1] > map_positions[-1]:
        raise ValueError(f"{map_snapshot.path}: genetic map does not span every model marker")
    marker_cm = np.interp(positions, map_positions, map_cm)
    positions.setflags(write=False)
    marker_cm.setflags(write=False)
    return ModelSpec(
        chrom=chrom,
        positions=positions,
        refs=refs,
        alts=alts,
        population_order=population_order,
        marker_count=marker_count,
        window_size=window_size,
        window_count=window_count,
        marker_cm=marker_cm,
        metadata_sha256=metadata_snapshot.sha256,
        map_sha256=map_snapshot.sha256,
    )


def _parse_boolean(raw: object, *, path: Path, iid: str, field: str) -> bool:
    value = str(raw).strip().casefold()
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise ValueError(f"{path}: donor {iid!r} has invalid boolean {field}={raw!r}")


def _read_donor_labels(
    snapshot: FileSnapshot,
    *,
    metadata_contract: Mapping[str, str],
    donor_iids: frozenset[str],
) -> dict[str, str]:
    fields = {
        key: metadata_contract[key]
        for key in (
            "iid_field",
            "population_field",
            "model_class_field",
            "release_field",
            "hard_filtered_field",
            "release_related_field",
            "all_samples_related_field",
        )
    }
    labels: dict[str, str] = {}
    with _open_snapshot_text(snapshot, label="donor metadata") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        header = reader.fieldnames
        if header is None or len(header) != len(set(header)):
            raise ValueError(f"{snapshot.path}: donor metadata header is missing or duplicated")
        missing = sorted(set(fields.values()) - set(header))
        if missing:
            raise ValueError(f"{snapshot.path}: donor metadata omits fields {missing}")
        for row in reader:
            iid = str(row[fields["iid_field"]]).strip()
            if iid not in donor_iids:
                continue
            if iid in labels:
                raise ValueError(f"{snapshot.path}: duplicate donor IID {iid!r}")
            if not str(row[fields["population_field"]]).strip():
                raise ValueError(f"{snapshot.path}: donor {iid!r} has an empty population")
            ancestry = str(row[fields["model_class_field"]]).strip().upper()
            if ancestry not in SUPERPOPULATIONS:
                raise ValueError(f"{snapshot.path}: donor {iid!r} has invalid model class")
            release = _parse_boolean(
                row[fields["release_field"]],
                path=snapshot.path,
                iid=iid,
                field=fields["release_field"],
            )
            excluded = [
                _parse_boolean(row[fields[key]], path=snapshot.path, iid=iid, field=fields[key])
                for key in (
                    "hard_filtered_field",
                    "release_related_field",
                    "all_samples_related_field",
                )
            ]
            if not release or any(excluded):
                raise ValueError(
                    f"{snapshot.path}: donor {iid!r} is not an unrelated release-QC sample"
                )
            labels[iid] = ancestry
    missing_donors = sorted(donor_iids - set(labels))
    if missing_donors:
        raise ValueError(f"{snapshot.path}: donor metadata omits donors {missing_donors}")
    return labels


def _read_relationships(
    snapshot: FileSnapshot,
    *,
    donor_split: Mapping[str, str],
) -> frozenset[str]:
    parent: dict[str, str] = {}

    def find(iid: str) -> str:
        parent.setdefault(iid, iid)
        while parent[iid] != iid:
            parent[iid] = parent[parent[iid]]
            iid = parent[iid]
        return iid

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    seen: set[tuple[str, str]] = set()
    with _open_snapshot_text(snapshot, label="relationships") as handle:
        for line_number, raw in enumerate(handle, start=1):
            columns = tuple(raw.rstrip("\r\n").split("\t"))
            if line_number == 1:
                if columns != ("iid_a", "iid_b", "relationship"):
                    raise ValueError(f"{snapshot.path}: expected exact relationship header")
                continue
            if len(columns) != 3 or any(not value.strip() for value in columns):
                raise ValueError(f"{snapshot.path}:{line_number}: invalid relationship row")
            iid_a, iid_b, relationship = (value.strip() for value in columns)
            if (iid_a == iid_b) != (relationship == "self"):
                raise ValueError(f"{snapshot.path}:{line_number}: invalid self relationship")
            pair = tuple(sorted((iid_a, iid_b)))
            if pair in seen:
                raise ValueError(f"{snapshot.path}:{line_number}: duplicate relationship pair")
            seen.add(pair)
            union(iid_a, iid_b)
    if not parent:
        raise ValueError(f"{snapshot.path}: relationship graph is empty")
    missing = sorted(set(donor_split) - set(parent))
    if missing:
        raise ValueError(f"{snapshot.path}: relationship graph omits donors {missing}")
    components: defaultdict[str, set[str]] = defaultdict(set)
    for iid in sorted(parent):
        components[find(iid)].add(iid)
    for component in components.values():
        component_splits = {donor_split[iid] for iid in component if iid in donor_split}
        if len(component_splits) > 1:
            raise ValueError(
                f"{snapshot.path}: relationship component crosses splits: {sorted(component)}"
            )
    return frozenset(parent)


def _validate_founders(
    design: Design,
    donor_labels: Mapping[str, str],
) -> dict[str, str]:
    donor_split: dict[str, str] = {}
    for simulation in design.simulations:
        for donor in simulation.donor_iids:
            previous = donor_split.setdefault(donor, simulation.split)
            if previous != simulation.split:
                raise ValueError(f"donor {donor!r} contributes to both splits")
            if donor_labels[donor] not in simulation.target_fractions:
                raise ValueError(f"simulation {simulation.iid!r} donor class lacks a target")
    required_founders = design.minimums["founders_per_class_per_split"]
    for split in SPLITS:
        donors = {
            donor for donor, donor_split_name in donor_split.items() if donor_split_name == split
        }
        counts = Counter(donor_labels[donor] for donor in donors)
        missing = {
            ancestry: required_founders - counts[ancestry]
            for ancestry in sorted(SUPERPOPULATIONS)
            if counts[ancestry] < required_founders
        }
        if missing:
            raise ValueError(f"split {split!r} lacks declared founder replication: {missing}")
    return donor_split


def _rng(seed: int, *domain: object) -> np.random.Generator:
    digest = hashlib.sha256("\x1f".join(str(value) for value in domain).encode("utf-8")).digest()
    entropy = [seed & 0xFFFFFFFF, (seed >> 32) & 0xFFFFFFFF]
    entropy.extend(
        int.from_bytes(digest[index : index + 4], "little") for index in range(0, 32, 4)
    )
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence(entropy)))


def _sample_tracts(
    *,
    design: Design,
    simulation: SimulationSpec,
    model: ModelSpec,
    chrom: str,
    haplotype: int,
    attempt: int,
    donor_labels: Mapping[str, str],
) -> tuple[Tract, ...]:
    rng = _rng(design.seed, GENERATOR_NAME, simulation.iid, attempt, chrom, haplotype)
    genetic_span_morgans = float(model.marker_cm[-1] - model.marker_cm[0]) / 100.0
    event_rate = simulation.generation * genetic_span_morgans
    if not math.isfinite(event_rate) or event_rate < 0 or event_rate > 1_000_000:
        raise ValueError(f"simulation {simulation.iid!r} chr{chrom}: unsafe crossover rate")
    event_count = int(rng.poisson(event_rate))
    boundaries: list[int] = []
    if event_count and model.marker_count > 1:
        events = rng.uniform(float(model.marker_cm[0]), float(model.marker_cm[-1]), event_count)
        raw_boundaries = np.searchsorted(model.marker_cm, events, side="right")
        boundaries = sorted(
            {max(1, min(model.marker_count - 1, int(boundary))) for boundary in raw_boundaries}
        )
    identities: list[tuple[str, int]] = []
    weights: list[float] = []
    donors_by_class = Counter(donor_labels[donor] for donor in simulation.donor_iids)
    for donor in simulation.donor_iids:
        ancestry = donor_labels[donor]
        weight = float(simulation.target_fractions[ancestry]) / donors_by_class[ancestry] / 2.0
        for source_hap in (0, 1):
            identities.append((donor, source_hap))
            weights.append(weight)
    segments = [0, *boundaries, model.marker_count]
    tracts: list[Tract] = []
    normalized_weights = np.asarray(weights, dtype=np.float64)
    normalized_weights /= normalized_weights.sum()
    for start, end in zip(segments, segments[1:]):
        selected = int(rng.choice(len(identities), p=normalized_weights))
        identity = identities[selected]
        if tracts and identity == (tracts[-1].donor_iid, tracts[-1].source_hap):
            previous_tract = tracts.pop()
            tracts.append(Tract(previous_tract.start, end, identity[0], identity[1]))
        else:
            tracts.append(Tract(start, end, identity[0], identity[1]))
    if len(tracts) - 1 > design.max_breakpoints[chrom]:
        raise ValueError(
            f"chr{chrom} hap{haplotype} produced {len(tracts) - 1} breakpoints above "
            f"the declared maximum {design.max_breakpoints[chrom]}"
        )
    return tuple(tracts)


def _tract_at(tracts: Sequence[Tract], marker_index: int, offset: int) -> tuple[Tract, int]:
    while marker_index >= tracts[offset].end:
        offset += 1
    return tracts[offset], offset


def _derive_windows(
    *,
    model: ModelSpec,
    chrom: str,
    tracts: Mapping[tuple[str, int], tuple[Tract, ...]],
    donor_labels: Mapping[str, str],
) -> tuple[list[WindowTruth], dict[str, set[str]]]:
    windows: list[WindowTruth] = []
    modal_founders: defaultdict[str, set[str]] = defaultdict(set)
    labels_by_window: list[list[str]] = []
    for window in range(model.window_count):
        start = window * model.window_size
        end = (
            model.marker_count
            if window == model.window_count - 1
            else (window + 1) * model.window_size
        )
        hap_labels: list[str] = []
        for haplotype in (0, 1):
            class_counts: Counter[str] = Counter()
            donor_counts: Counter[str] = Counter()
            for tract in tracts[(chrom, haplotype)]:
                overlap = max(0, min(end, tract.end) - max(start, tract.start))
                if overlap:
                    ancestry = donor_labels[tract.donor_iid]
                    class_counts[ancestry] += overlap
                    donor_counts[tract.donor_iid] += overlap
            maximum = max(class_counts.values())
            code = min(
                model.population_order.index(ancestry)
                for ancestry, count in class_counts.items()
                if count == maximum
            )
            label = model.population_order[code]
            hap_labels.append(label)
            eligible = {
                donor: count
                for donor, count in donor_counts.items()
                if donor_labels[donor] == label
            }
            founder_maximum = max(eligible.values())
            founder = min(donor for donor, count in eligible.items() if count == founder_maximum)
            modal_founders[label].add(founder)
        labels_by_window.append(hap_labels)
        windows.append(
            WindowTruth(
                chrom=chrom,
                start=int(model.positions[start]),
                end=int(model.positions[end - 1]),
                hap0=hap_labels[0],
                hap1=hap_labels[1],
            )
        )
    return windows, dict(modal_founders)


def _generate_one(
    design: Design,
    simulation: SimulationSpec,
    models: Mapping[str, ModelSpec],
    donor_labels: Mapping[str, str],
    *,
    attempt: int,
) -> GeneratedSimulation:
    for chrom in AUTOSOMES:
        model = models[chrom]
        genetic_span_morgans = float(model.marker_cm[-1] - model.marker_cm[0]) / 100.0
        event_rate = simulation.generation * genetic_span_morgans
        if not math.isfinite(event_rate) or event_rate < 0 or event_rate > 1_000_000:
            raise ValueError(f"simulation {simulation.iid!r} chr{chrom}: unsafe crossover rate")
    last_reason = "no attempt executed"
    for candidate_attempt in (attempt,):
        try:
            tracts: dict[tuple[str, int], tuple[Tract, ...]] = {}
            ancestry_counts: Counter[str] = Counter()
            observed_donors: set[str] = set()
            windows: list[WindowTruth] = []
            modal_founders: defaultdict[str, set[str]] = defaultdict(set)
            for chrom in AUTOSOMES:
                model = models[chrom]
                for haplotype in (0, 1):
                    chromosome_tracts = _sample_tracts(
                        design=design,
                        simulation=simulation,
                        model=model,
                        chrom=chrom,
                        haplotype=haplotype,
                        attempt=candidate_attempt,
                        donor_labels=donor_labels,
                    )
                    tracts[(chrom, haplotype)] = chromosome_tracts
                    for tract in chromosome_tracts:
                        length = tract.end - tract.start
                        ancestry_counts[donor_labels[tract.donor_iid]] += length
                        observed_donors.add(tract.donor_iid)
                chromosome_windows, chromosome_founders = _derive_windows(
                    model=model,
                    chrom=chrom,
                    tracts=tracts,
                    donor_labels=donor_labels,
                )
                windows.extend(chromosome_windows)
                for ancestry, founders in chromosome_founders.items():
                    modal_founders[ancestry].update(founders)
        except ValueError as exc:
            last_reason = str(exc)
            continue
        if observed_donors != set(simulation.donor_iids):
            last_reason = "not every declared founder contributed markers"
            continue
        tract_count = sum(len(chromosome_tracts) for chromosome_tracts in tracts.values())
        if tract_count > MAX_TRACTS_PER_SIMULATION:
            last_reason = (
                f"tract count {tract_count} exceeds the verifier safety limit "
                f"{MAX_TRACTS_PER_SIMULATION}"
            )
            continue
        total = sum(ancestry_counts.values())
        realized: dict[str, str] = {}
        fraction_failure: str | None = None
        for ancestry in sorted(SUPERPOPULATIONS):
            fraction = Decimal(ancestry_counts[ancestry]) / Decimal(total)
            realized[ancestry] = str(fraction)
            if abs(fraction - simulation.target_fractions[ancestry]) > simulation.tolerance:
                fraction_failure = (
                    f"realized {ancestry} fraction {fraction} exceeds tolerance "
                    f"{simulation.tolerance}"
                )
                break
        if fraction_failure is not None:
            last_reason = fraction_failure
            continue
        return GeneratedSimulation(
            spec=simulation,
            tracts=tracts,
            windows=tuple(windows),
            ancestry_marker_counts={
                ancestry: ancestry_counts[ancestry] for ancestry in sorted(SUPERPOPULATIONS)
            },
            realized_fractions=realized,
            modal_founders={
                ancestry: frozenset(modal_founders[ancestry])
                for ancestry in sorted(SUPERPOPULATIONS)
            },
        )
    raise ValueError(
        f"simulation {simulation.iid!r} rejected dataset attempt {attempt}: {last_reason}"
    )


def _split_minimum_failure(
    generated: Mapping[str, GeneratedSimulation],
    design: Design,
) -> str | None:
    required_simulations = design.minimums["simulations_per_class_per_split"]
    required_truth = design.minimums["truth_haplotype_windows_per_class_per_split"]
    required_founders = design.minimums["founders_per_class_per_split"]
    for split in SPLITS:
        split_generated = [item for item in generated.values() if item.spec.split == split]
        simulations_by_class = Counter()
        truth_by_class = Counter()
        founders_by_class: defaultdict[str, set[str]] = defaultdict(set)
        truth_by_stratum: defaultdict[str, Counter[str]] = defaultdict(Counter)
        for item in split_generated:
            observed = {label for window in item.windows for label in (window.hap0, window.hap1)}
            simulations_by_class.update(observed)
            truth_by_class.update(
                label for window in item.windows for label in (window.hap0, window.hap1)
            )
            truth_by_stratum[item.spec.validation_stratum].update(
                label for window in item.windows for label in (window.hap0, window.hap1)
            )
            for ancestry, founders in item.modal_founders.items():
                founders_by_class[ancestry].update(founders)
        for ancestry in sorted(SUPERPOPULATIONS):
            if simulations_by_class[ancestry] < required_simulations:
                return f"split {split!r} lacks simulation replication for {ancestry}"
            if truth_by_class[ancestry] < required_truth:
                return f"split {split!r} lacks truth-window replication for {ancestry}"
            if len(founders_by_class[ancestry]) < required_founders:
                return f"split {split!r} lacks modal founder replication for {ancestry}"
        for stratum, counts in sorted(truth_by_stratum.items()):
            missing = sorted(ancestry for ancestry in SUPERPOPULATIONS if counts[ancestry] == 0)
            if missing:
                return f"split {split!r} stratum {stratum!r} lacks truth {missing}"
    return None


def _generate_dataset(
    design: Design,
    models: Mapping[str, ModelSpec],
    donor_labels: Mapping[str, str],
) -> dict[str, GeneratedSimulation]:
    """Retry whole-dataset candidates until every declared split minimum holds."""
    last_failure = "no dataset candidate was evaluated"
    for attempt in range(1, design.max_attempts + 1):
        generated: dict[str, GeneratedSimulation] = {}
        try:
            for simulation in design.simulations:
                item = _generate_one(
                    design,
                    simulation,
                    models,
                    donor_labels,
                    attempt=attempt,
                )
                generated[simulation.iid] = item
        except ValueError as exc:
            last_failure = str(exc)
            continue
        split_failure = _split_minimum_failure(generated, design)
        if split_failure is None:
            return generated
        last_failure = f"dataset attempt {attempt}: {split_failure}"
    raise ValueError(
        f"simulation dataset exhausted {design.max_attempts} deterministic "
        f"whole-dataset attempt(s): {last_failure}"
    )


def _read_vcf_header(
    snapshot: FileSnapshot,
    *,
    chrom: str,
    donor_iids: frozenset[str],
) -> tuple[str, ...]:
    with _open_snapshot_text(
        snapshot,
        label=f"source VCF chr{chrom}",
        allow_gzip=True,
    ) as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.rstrip("\r\n")
            if line.startswith("##"):
                continue
            columns = tuple(line.split("\t"))
            if tuple(columns[:9]) != VCF_FIXED_HEADER:
                raise ValueError(f"{snapshot.path}:{line_number}: invalid VCF header")
            sample_ids = columns[9:]
            if (
                not sample_ids
                or len(sample_ids) != len(set(sample_ids))
                or any(not iid for iid in sample_ids)
            ):
                raise ValueError(f"{snapshot.path}:{line_number}: invalid VCF sample IDs")
            missing = sorted(donor_iids - set(sample_ids))
            if missing:
                raise ValueError(
                    f"{snapshot.path}: declared donors absent from chr{chrom}: {missing}"
                )
            return sample_ids
    raise ValueError(f"{snapshot.path}: missing #CHROM header")


def _vcf_model_markers(
    snapshot: FileSnapshot,
    *,
    chrom: str,
    model: ModelSpec,
    donor_iids: frozenset[str],
    expected_sample_ids: tuple[str, ...],
) -> Iterator[tuple[int, str, Mapping[str, tuple[str, str]]]]:
    with _open_snapshot_text(
        snapshot,
        label=f"source VCF chr{chrom}",
        allow_gzip=True,
    ) as handle:
        line_number = 0
        sample_ids: tuple[str, ...] | None = None
        sample_indexes: dict[str, int] = {}
        records: Iterator[str] | None = None
        for raw in handle:
            line_number += 1
            line = raw.rstrip("\r\n")
            if line.startswith("##"):
                continue
            columns = tuple(line.split("\t"))
            if tuple(columns[:9]) != VCF_FIXED_HEADER:
                raise ValueError(f"{snapshot.path}:{line_number}: invalid VCF header")
            sample_ids = columns[9:]
            if sample_ids != expected_sample_ids:
                raise ValueError(f"{snapshot.path}: VCF sample header changed between passes")
            all_indexes = {iid: index for index, iid in enumerate(sample_ids)}
            sample_indexes = {iid: all_indexes[iid] for iid in donor_iids}
            records = iter(handle)
            break
        if sample_ids is None or records is None:
            raise ValueError(f"{snapshot.path}: missing #CHROM header")

        lookahead: tuple[int, tuple[str, ...]] | None = None
        previous_position = 0

        def next_record() -> tuple[int, tuple[str, ...]] | None:
            nonlocal line_number, previous_position
            for raw_record in records:
                line_number += 1
                line = raw_record.rstrip("\r\n")
                if not line or line.startswith("#"):
                    raise ValueError(f"{snapshot.path}:{line_number}: invalid VCF record ordering")
                columns = tuple(line.split("\t"))
                if len(columns) != 9 + len(sample_ids):
                    raise ValueError(f"{snapshot.path}:{line_number}: wrong VCF column count")
                row_chrom = _canonical_autosome(columns[0])
                try:
                    position = int(columns[1])
                except ValueError:
                    raise ValueError(
                        f"{snapshot.path}:{line_number}: invalid VCF position"
                    ) from None
                if row_chrom != chrom or position <= 0 or position < previous_position:
                    raise ValueError(
                        f"{snapshot.path}:{line_number}: VCF is not ordered chr{chrom}"
                    )
                previous_position = position
                return line_number, columns
            return None

        for marker_index, position in enumerate(model.positions):
            target = int(position)
            candidates: list[tuple[int, tuple[str, ...]]] = []
            while True:
                if lookahead is None:
                    lookahead = next_record()
                if lookahead is None:
                    break
                record_position = int(lookahead[1][1])
                if record_position < target:
                    lookahead = None
                    continue
                if record_position > target:
                    break
                candidate = lookahead
                lookahead = None
                columns = candidate[1]
                if (
                    columns[3].upper() == model.refs[marker_index]
                    and columns[4].upper() == model.alts[marker_index]
                ):
                    candidates.append(candidate)
            if len(candidates) != 1:
                raise ValueError(
                    f"{snapshot.path}: expected one REF/ALT-matching model marker "
                    f"at chr{chrom}:{target}"
                )
            candidate_line, columns = candidates[0]
            ref, alt = columns[3].upper(), columns[4].upper()
            if (
                len(ref) != 1
                or len(alt) != 1
                or ref not in DNA_BASES
                or alt not in DNA_BASES
                or "," in alt
            ):
                raise ValueError(
                    f"{snapshot.path}:{candidate_line}: model marker is not a biallelic SNV"
                )
            rsids = sorted(
                identifier
                for identifier in columns[2].split(";")
                if identifier and identifier != "." and identifier.lower().startswith("rs")
            )
            if len(rsids) != 1:
                raise ValueError(f"{snapshot.path}:{candidate_line}: expected exactly one rsID")
            format_fields = tuple(columns[8].split(":"))
            if format_fields.count("GT") != 1:
                raise ValueError(f"{snapshot.path}:{candidate_line}: FORMAT must contain one GT")
            genotype_index = format_fields.index("GT")
            alleles: dict[str, tuple[str, str]] = {}
            for iid, sample_index in sample_indexes.items():
                sample_fields = columns[9 + sample_index].split(":")
                if genotype_index >= len(sample_fields):
                    raise ValueError(f"{snapshot.path}:{candidate_line}: donor {iid!r} lacks GT")
                genotype = sample_fields[genotype_index]
                if "/" in genotype:
                    raise ValueError(
                        f"{snapshot.path}:{candidate_line}: donor {iid!r} GT is not phased"
                    )
                calls = genotype.split("|")
                if len(calls) != 2 or any(call not in {"0", "1"} for call in calls):
                    raise ValueError(
                        f"{snapshot.path}:{candidate_line}: donor {iid!r} GT is "
                        "not diploid biallelic"
                    )
                bases = (ref, alt)
                alleles[iid] = (bases[int(calls[0])], bases[int(calls[1])])
            yield target, rsids[0], alleles


def _write_lines(path: Path, header: Sequence[str], rows: Iterator[Sequence[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\t".join(header) + "\n")
        for row in rows:
            handle.write("\t".join(str(value) for value in row) + "\n")


def _relative_artifact(path: Path, root: Path) -> str:
    relative = PurePosixPath(path.relative_to(root).as_posix())
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"unsafe generated relative path: {relative}")
    return str(relative)


def _verify_repository_generator_script(path: Path, repo_root: Path) -> None:
    """Require this generator to be the reviewed, tracked, clean HEAD source."""
    absolute = _absolute_without_symlinks(path, label="simulation generator script")
    try:
        relative = absolute.relative_to(repo_root)
    except ValueError:
        raise ValueError("simulation generator script must live in the repository") from None
    expected = Path("scripts/lai_bundle_v2/06g_generate_simulation.py")
    if relative != expected:
        raise ValueError(f"simulation generator script must be the reviewed {expected}")
    tracked = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "--error-unmatch", "--", str(relative)],
        check=False,
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            str(relative),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if tracked.returncode != 0 or status.returncode != 0 or status.stdout.strip():
        raise ValueError("simulation generator script must be tracked and clean at HEAD")


def _publish(
    *,
    output_dir: Path,
    staging: Path,
    input_snapshots: Sequence[tuple[FileSnapshot, str]],
    output_snapshots: Sequence[tuple[FileSnapshot, str]],
) -> None:
    descendants = tuple(staging.rglob("*"))
    if any(path.is_symlink() for path in descendants):
        raise ValueError("generated output tree contains a symlink")
    expected_directories = {
        staging / directory
        for directory in ("fixtures", "marker-truth", "tract-truth", "window-truth", "labels")
    }
    expected_output_paths = {snapshot.path for snapshot, _label in output_snapshots}
    expected_descendants = expected_directories | expected_output_paths
    if set(descendants) != expected_descendants:
        missing = sorted(str(path) for path in expected_descendants - set(descendants))
        extra = sorted(str(path) for path in set(descendants) - expected_descendants)
        raise ValueError(f"generated output tree changed; missing={missing}, extra={extra}")
    parent = output_dir.parent
    parent_metadata = parent.lstat()
    if (
        stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or staging.parent != parent
    ):
        raise ValueError("output parent/staging directory is not safe for publication")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    directory_descriptor = os.open(parent, directory_flags)
    input_handles: list[tuple[FileSnapshot, str, io.BufferedReader]] = []
    renamed = False
    try:
        opened_parent = os.fstat(directory_descriptor)
        parent_identity = (parent_metadata.st_dev, parent_metadata.st_ino)
        if (opened_parent.st_dev, opened_parent.st_ino) != parent_identity:
            raise ValueError("output parent changed while it was opened")
        try:
            os.stat(output_dir.name, dir_fd=directory_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError(f"output directory already exists: {output_dir}")
        staging_metadata = os.stat(
            staging.name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if not stat.S_ISDIR(staging_metadata.st_mode):
            raise ValueError("staging output is no longer a directory")
        for snapshot, label in input_snapshots:
            _assert_snapshot_unchanged(snapshot, label=label)
            handle = _open_descriptor(snapshot.path, label=label)
            if _stat_signature(os.fstat(handle.fileno())) != snapshot.signature:
                handle.close()
                raise ValueError(f"{label}: input changed before publication")
            input_handles.append((snapshot, label, handle))
        for snapshot, label in output_snapshots:
            _assert_snapshot_unchanged(snapshot, label=label)
            handle = _open_descriptor(snapshot.path, label=label)
            try:
                opened_output = os.fstat(handle.fileno())
                if (
                    _stat_signature(opened_output) != snapshot.signature
                    or opened_output.st_nlink != 1
                ):
                    raise ValueError(f"{label}: generated file changed before publication")
                os.fsync(handle.fileno())
            finally:
                handle.close()
        for snapshot, label, handle in input_handles:
            _assert_snapshot_unchanged(snapshot, label=label)
            if _stat_signature(os.fstat(handle.fileno())) != snapshot.signature:
                raise ValueError(f"{label}: opened input changed before publication")
        for snapshot, label in output_snapshots:
            _assert_snapshot_unchanged(snapshot, label=label)
        os.rename(
            staging.name,
            output_dir.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        renamed = True
        for snapshot, label in output_snapshots:
            relative = snapshot.path.relative_to(staging)
            published_path = output_dir / relative
            _, published_metadata = _regular_file(published_path, label=label)
            if _stat_signature(published_metadata) != snapshot.signature:
                raise ValueError(f"{label}: generated file changed during publication")
        for snapshot, label, handle in input_handles:
            try:
                _assert_snapshot_unchanged(snapshot, label=label)
            except ValueError as exc:
                raise ValueError(f"{label}: input changed during publication") from exc
            if _stat_signature(os.fstat(handle.fileno())) != snapshot.signature:
                raise ValueError(f"{label}: opened input changed during publication")
        os.fsync(directory_descriptor)
        for snapshot, label, handle in input_handles:
            try:
                _assert_snapshot_unchanged(snapshot, label=label)
            except ValueError as exc:
                raise ValueError(f"{label}: input changed while publication was synced") from exc
            if _stat_signature(os.fstat(handle.fileno())) != snapshot.signature:
                raise ValueError(f"{label}: opened input changed while publication was synced")
        for snapshot, label in output_snapshots:
            relative = snapshot.path.relative_to(staging)
            _, published_metadata = _regular_file(output_dir / relative, label=label)
            if _stat_signature(published_metadata) != snapshot.signature:
                raise ValueError(f"{label}: generated file changed while publication was synced")
        live_parent = parent.lstat()
        if (live_parent.st_dev, live_parent.st_ino) != parent_identity:
            raise ValueError("output parent changed during publication")
    except BaseException:
        if renamed:
            try:
                os.rename(
                    output_dir.name,
                    staging.name,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                )
                renamed = False
                os.fsync(directory_descriptor)
            except OSError as rollback_error:
                raise OSError(
                    f"publication failed and output rollback also failed: {rollback_error}"
                ) from rollback_error
        raise
    finally:
        for _snapshot, _label, handle in input_handles:
            with contextlib.suppress(OSError):
                handle.close()
        with contextlib.suppress(OSError):
            os.close(directory_descriptor)


def generate(args: argparse.Namespace) -> Path:
    output_dir = _absolute_without_symlinks(args.output_dir, label="output directory")
    if output_dir.exists() or output_dir.is_symlink():
        raise ValueError(f"output directory must not already exist: {output_dir}")
    parent = output_dir.parent
    try:
        parent_metadata = parent.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"output parent does not exist: {parent}") from exc
    if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(parent_metadata.st_mode):
        raise ValueError(f"output parent must be a non-symlink directory: {parent}")
    code_revision = args.code_revision
    if not isinstance(code_revision, str) or REVISION_PATTERN.fullmatch(code_revision) is None:
        raise ValueError("--code-revision must be a lowercase 40-character Git revision")
    repo_root = Path(__file__).absolute().parents[2]
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or completed.stdout.strip() != code_revision:
        raise ValueError("--code-revision must match the generator repository HEAD")
    _verify_repository_generator_script(Path(__file__), repo_root)

    design_snapshot = _snapshot_file(args.design, label="simulation design")
    design = _parse_design(_read_json(design_snapshot, label="simulation design"))
    metadata_paths = _parse_chromosome_paths(args.model_metadata, option="--model-metadata")
    map_paths = _parse_chromosome_paths(args.genetic_map, option="--genetic-map")
    vcf_paths = _parse_chromosome_paths(args.donor_vcf, option="--donor-vcf")
    index_paths = _parse_chromosome_paths(args.donor_vcf_index, option="--donor-vcf-index")
    donor_metadata_snapshot = _snapshot_file(args.donor_metadata, label="donor metadata")
    relationship_snapshot = _snapshot_file(args.relationships, label="relationships")
    environment_snapshot = _snapshot_file(
        args.generator_environment_lock,
        label="generator environment lock",
    )
    script_snapshot = _snapshot_file(Path(__file__), label="simulation generator script")
    metadata_snapshots = {
        chrom: _snapshot_file(metadata_paths[chrom], label=f"model metadata chr{chrom}")
        for chrom in AUTOSOMES
    }
    map_snapshots = {
        chrom: _snapshot_file(map_paths[chrom], label=f"genetic map chr{chrom}")
        for chrom in AUTOSOMES
    }
    vcf_snapshots = {
        chrom: _snapshot_file(vcf_paths[chrom], label=f"source VCF chr{chrom}")
        for chrom in AUTOSOMES
    }
    index_snapshots = {
        chrom: _snapshot_file(index_paths[chrom], label=f"source VCF index chr{chrom}")
        for chrom in AUTOSOMES
    }
    all_donors = frozenset(
        donor for simulation in design.simulations for donor in simulation.donor_iids
    )
    donor_labels = _read_donor_labels(
        donor_metadata_snapshot,
        metadata_contract=design.donor_metadata,
        donor_iids=all_donors,
    )
    donor_split = _validate_founders(design, donor_labels)
    protected_iids = _read_relationships(relationship_snapshot, donor_split=donor_split)

    models: dict[str, ModelSpec] = {}
    population_order: tuple[str, ...] | None = None
    for chrom in AUTOSOMES:
        model = _load_model(metadata_snapshots[chrom], map_snapshots[chrom], chrom=chrom)
        if population_order is None:
            population_order = model.population_order
        elif model.population_order != population_order:
            raise ValueError(
                f"model chr{chrom} population_order disagrees with earlier chromosomes"
            )
        if design.max_breakpoints[chrom] > model.marker_count - 1:
            raise ValueError(
                f"design chr{chrom} breakpoint maximum exceeds C-1 ({model.marker_count - 1})"
            )
        models[chrom] = model
    assert population_order is not None

    sample_ids_by_chromosome: dict[str, tuple[str, ...]] = {}
    for chrom in AUTOSOMES:
        sample_ids_by_chromosome[chrom] = _read_vcf_header(
            vcf_snapshots[chrom],
            chrom=chrom,
            donor_iids=all_donors,
        )

    generated = _generate_dataset(design, models, donor_labels)

    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=parent))
    published = False
    lock_handle: io.FileIO | None = None
    try:
        output_snapshots: list[tuple[FileSnapshot, str]] = []
        for directory in ("fixtures", "marker-truth", "tract-truth", "window-truth", "labels"):
            (staging / directory).mkdir()
        for iid, item in sorted(generated.items()):
            tract_path = staging / "tract-truth" / f"{iid}.tsv"
            _write_lines(
                tract_path,
                TRACT_TRUTH_HEADER,
                (
                    (
                        iid,
                        chrom,
                        haplotype,
                        tract.start,
                        tract.end,
                        tract.donor_iid,
                        tract.source_hap,
                    )
                    for chrom in AUTOSOMES
                    for haplotype in (0, 1)
                    for tract in item.tracts[(chrom, haplotype)]
                ),
            )
            window_path = staging / "window-truth" / f"{iid}.tsv"
            _write_lines(
                window_path,
                WINDOW_TRUTH_HEADER,
                (
                    (window.chrom, window.start, window.end, window.hap0, window.hap1)
                    for window in item.windows
                ),
            )
        for split in SPLITS:
            label_path = staging / "labels" / f"{split}.tsv"
            _write_lines(
                label_path,
                ("iid", "validation_stratum"),
                (
                    (item.spec.iid, item.spec.validation_stratum)
                    for item in sorted(generated.values(), key=lambda value: value.spec.iid)
                    if item.spec.split == split
                ),
            )

        with contextlib.ExitStack() as stack:
            marker_handles: dict[str, TextIO] = {}
            fixture_handles: dict[str, TextIO] = {}
            for iid in sorted(generated):
                marker_handle = stack.enter_context(
                    (staging / "marker-truth" / f"{iid}.tsv").open(
                        "w", encoding="utf-8", newline="\n"
                    )
                )
                fixture_handle = stack.enter_context(
                    (staging / "fixtures" / f"{iid}.tsv").open("w", encoding="utf-8", newline="\n")
                )
                marker_handle.write("\t".join(MARKER_TRUTH_HEADER) + "\n")
                fixture_handle.write("\t".join(FIXTURE_HEADER) + "\n")
                marker_handles[iid] = marker_handle
                fixture_handles[iid] = fixture_handle
            seen_rsids: set[str] = set()
            tract_offsets: defaultdict[tuple[str, str, int], int] = defaultdict(int)
            for chrom in AUTOSOMES:
                model = models[chrom]
                marker_iterator = _vcf_model_markers(
                    vcf_snapshots[chrom],
                    chrom=chrom,
                    model=model,
                    donor_iids=all_donors,
                    expected_sample_ids=sample_ids_by_chromosome[chrom],
                )
                marker_rows = 0
                for marker_index, (position, rsid, donor_alleles) in enumerate(marker_iterator):
                    marker_rows += 1
                    if rsid in seen_rsids:
                        raise ValueError(f"source VCF rsID {rsid!r} is not globally unique")
                    seen_rsids.add(rsid)
                    for iid, item in sorted(generated.items()):
                        identities: list[tuple[str, int]] = []
                        for haplotype in (0, 1):
                            key = (iid, chrom, haplotype)
                            tract, offset = _tract_at(
                                item.tracts[(chrom, haplotype)],
                                marker_index,
                                tract_offsets[key],
                            )
                            tract_offsets[key] = offset
                            identities.append((tract.donor_iid, tract.source_hap))
                        hap0, hap1 = identities
                        allele1 = donor_alleles[hap0[0]][hap0[1]]
                        allele2 = donor_alleles[hap1[0]][hap1[1]]
                        marker_handles[iid].write(
                            "\t".join(
                                (
                                    iid,
                                    chrom,
                                    str(marker_index),
                                    str(position),
                                    rsid,
                                    hap0[0],
                                    str(hap0[1]),
                                    hap1[0],
                                    str(hap1[1]),
                                )
                            )
                            + "\n"
                        )
                        fixture_handles[iid].write(
                            f"{rsid}\t{chrom}\t{position}\t{allele1}\t{allele2}\n"
                        )
                if marker_rows != model.marker_count:
                    raise ValueError(
                        f"source VCF chr{chrom} yielded {marker_rows} model markers, "
                        f"expected {model.marker_count}"
                    )

        labels_manifest = {}
        for split in SPLITS:
            path = staging / "labels" / f"{split}.tsv"
            snapshot = _snapshot_file(path, label=f"generated {split} labels")
            output_snapshots.append((snapshot, f"generated {split} labels"))
            labels_manifest[split] = {
                "filename": _relative_artifact(path, staging),
                "sha256": snapshot.sha256,
            }
        simulations_manifest = []
        for iid, item in sorted(generated.items()):
            artifact_fields = {
                "fixture": staging / "fixtures" / f"{iid}.tsv",
                "marker_truth": staging / "marker-truth" / f"{iid}.tsv",
                "tract_truth": staging / "tract-truth" / f"{iid}.tsv",
                "window_truth": staging / "window-truth" / f"{iid}.tsv",
            }
            artifact_snapshots = {
                key: _snapshot_file(path, label=f"generated {key} {iid}")
                for key, path in artifact_fields.items()
            }
            output_snapshots.extend(
                (artifact_snapshots[key], f"generated {key} {iid}") for key in artifact_fields
            )
            simulations_manifest.append(
                {
                    "iid": iid,
                    "split": item.spec.split,
                    "simulation_kind": "admixed_mosaic",
                    "generation": item.spec.generation,
                    "validation_stratum": item.spec.validation_stratum,
                    "donor_iids": list(item.spec.donor_iids),
                    "target_marker_ancestry_fractions": dict(item.spec.target_fraction_strings),
                    "fraction_absolute_tolerance": item.spec.tolerance_string,
                    **{
                        f"{key}_file": _relative_artifact(artifact_fields[key], staging)
                        for key in artifact_fields
                    },
                    **{f"{key}_sha256": artifact_snapshots[key].sha256 for key in artifact_fields},
                }
            )
        models_manifest = {
            "population_order": list(population_order),
            "population_order_sha256": _sha256_json(list(population_order)),
            "per_chromosome": {
                chrom: {
                    "metadata_npz_sha256": models[chrom].metadata_sha256,
                    "genetic_map_sha256": models[chrom].map_sha256,
                    "C": models[chrom].marker_count,
                    "M": models[chrom].window_size,
                    "W": models[chrom].window_count,
                }
                for chrom in AUTOSOMES
            },
        }
        manifest = {
            "schema_version": 2,
            "dataset_id": design.dataset_id,
            "source_bundle_artifact_sha256": design.source_bundle_artifact_sha256,
            "model_frozen_before_generation": True,
            "generator": {
                "name": GENERATOR_NAME,
                "code_revision": code_revision,
                "script_sha256": script_snapshot.sha256,
                "environment_lock_sha256": environment_snapshot.sha256,
                "rng_library": "NumPy",
                "rng_version": np.__version__,
                "rng_algorithm": "PCG64 via SeedSequence domain-separated streams",
                "seed": design.seed,
                "design_sha256": design_snapshot.sha256,
            },
            "simulation_protocol": {
                "schema": "founder_mosaic_v1",
                "genome_build": "GRCh38",
                "breakpoint_process": "genetic_map_poisson_v1",
                "admixture_model": "single_pulse_v1",
                "generation_rate_semantics": (
                    "lambda=generation*(last_model_marker_cM-first_model_marker_cM)/100"
                ),
                "event_projection_semantics": (
                    "right-search model-marker cM boundaries; clamp to [1,C-1]; deduplicate"
                ),
                "breakpoint_envelope_semantics": (
                    "post-projection tract transitions after merging adjacent identical "
                    "donor/source-haplotype identities"
                ),
                "recombination_map_sha256_by_autosome": {
                    chrom: map_snapshots[chrom].sha256 for chrom in AUTOSOMES
                },
                "max_breakpoints_per_haplotype_by_autosome": {
                    chrom: design.max_breakpoints[chrom] for chrom in AUTOSOMES
                },
                "allowed_generations": list(design.allowed_generations),
                "minimums": dict(design.minimums),
                "max_attempts_per_simulation": design.max_attempts,
            },
            "relationships": {
                "filename": relationship_snapshot.path.name,
                "sha256": relationship_snapshot.sha256,
            },
            "donor_metadata": {
                **dict(design.donor_metadata),
                "sha256": donor_metadata_snapshot.sha256,
            },
            "donor_haplotype_source": {
                "genome_build": "GRCh38",
                "per_chromosome_vcf_sha256": {
                    chrom: vcf_snapshots[chrom].sha256 for chrom in AUTOSOMES
                },
                "per_chromosome_vcf_index_sha256": {
                    chrom: index_snapshots[chrom].sha256 for chrom in AUTOSOMES
                },
                "per_chromosome_sample_ids_sha256": {
                    chrom: _sha256_json(sorted(sample_ids_by_chromosome[chrom]))
                    for chrom in AUTOSOMES
                },
                "per_chromosome_sample_count": {
                    chrom: len(sample_ids_by_chromosome[chrom]) for chrom in AUTOSOMES
                },
            },
            "models": models_manifest,
            "protected_iids_sha256": _sha256_json(sorted(protected_iids)),
            "splits": {
                split: sorted(
                    item.spec.iid for item in generated.values() if item.spec.split == split
                )
                for split in SPLITS
            },
            "simulations": simulations_manifest,
            "truth_projection": {
                "schema": "gnomix-window-mode-v1",
                "regular_window": "[w*M,(w+1)*M)",
                "final_window": "[(W-1)*M,C)",
                "tie_rule": "lowest production-model ancestry code",
            },
            "labels": labels_manifest,
        }
        manifest_path = staging / "simulation-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        if manifest_path.stat().st_size > MAX_JSON_BYTES:
            raise ValueError(f"generated manifest exceeds {MAX_JSON_BYTES} bytes")
        manifest_snapshot = _snapshot_file(
            manifest_path,
            label="generated simulation manifest",
        )
        output_snapshots.append((manifest_snapshot, "generated simulation manifest"))

        input_snapshots: list[tuple[FileSnapshot, str]] = [
            (design_snapshot, "simulation design"),
            (donor_metadata_snapshot, "donor metadata"),
            (relationship_snapshot, "relationships"),
            (environment_snapshot, "generator environment lock"),
            (script_snapshot, "simulation generator script"),
        ]
        input_snapshots.extend(
            (metadata_snapshots[chrom], f"model metadata chr{chrom}") for chrom in AUTOSOMES
        )
        input_snapshots.extend(
            (map_snapshots[chrom], f"genetic map chr{chrom}") for chrom in AUTOSOMES
        )
        input_snapshots.extend(
            (vcf_snapshots[chrom], f"source VCF chr{chrom}") for chrom in AUTOSOMES
        )
        input_snapshots.extend(
            (index_snapshots[chrom], f"source VCF index chr{chrom}") for chrom in AUTOSOMES
        )
        final_revision = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
        if final_revision.returncode != 0 or final_revision.stdout.strip() != code_revision:
            raise ValueError("generator repository HEAD changed during generation")
        _verify_repository_generator_script(Path(__file__), repo_root)
        lock_path = parent / f".{output_dir.name}.generator.lock"
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        lock_handle = io.FileIO(descriptor, mode="r+", closefd=True)
        lock_metadata = os.fstat(lock_handle.fileno())
        if not stat.S_ISREG(lock_metadata.st_mode) or lock_metadata.st_nlink != 1:
            raise ValueError("output generator lock is not a regular file")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("another generator is publishing this output") from None
        _publish(
            output_dir=output_dir,
            staging=staging,
            input_snapshots=input_snapshots,
            output_snapshots=output_snapshots,
        )
        published = True
        return output_dir / "simulation-manifest.json"
    finally:
        if lock_handle is not None:
            lock_handle.close()
        if not published:
            shutil.rmtree(staging, ignore_errors=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", required=True, type=Path)
    parser.add_argument("--donor-metadata", required=True, type=Path)
    parser.add_argument("--relationships", required=True, type=Path)
    parser.add_argument("--model-metadata", action="append", default=[], metavar="CHROM=PATH")
    parser.add_argument("--genetic-map", action="append", default=[], metavar="CHROM=PATH")
    parser.add_argument("--donor-vcf", action="append", default=[], metavar="CHROM=PATH")
    parser.add_argument("--donor-vcf-index", action="append", default=[], metavar="CHROM=PATH")
    parser.add_argument("--generator-environment-lock", required=True, type=Path)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        manifest_path = generate(args)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
