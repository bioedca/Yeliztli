#!/usr/bin/env python3
"""Independently replay donor VCF haplotypes into simulated LAI fixtures.

This verifier is deliberately repository-owned and standard-library-only.  It
does not import the simulation generator or the coverage harness. Given a
schema-v2 simulation manifest, it authenticates the actual generator source,
validates gap-free founder tracts, streams every marker-truth and fixture row in
the manifest's model-marker order, reconciles marker donors with those tracts,
looks up the declared donor haplotypes in the pinned chromosome VCFs, and checks
the paired fixture alleles exactly.

The output is an atomic schema-v2 JSON stamp.  Any missing marker, mismatching
allele or rsID, unphased/non-biallelic donor genotype, incomplete simulation or
chromosome set, hash mismatch, unsafe input, or generator self-attestation
causes a fail-closed exit without replacing the requested output.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import gzip
import hashlib
import io
import json
import os
import secrets
import stat
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

# Allow direct execution from the repository root without installing the package.
REPO_ROOT = Path(__file__).absolute().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.lai_bundle_v2.lai_coverage_policy import (  # noqa: E402
    confirmation_policy_provenance,
    read_confirmation_policy,
)

AUTOSOMES = tuple(str(chrom) for chrom in range(1, 23))
AUTOSOME_SET = frozenset(AUTOSOMES)
DNA_BASES = frozenset({"A", "C", "G", "T"})
SHA256_LENGTH = 64
READ_CHUNK_BYTES = 1024 * 1024
MAX_SIMULATIONS = 64
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_BREAKPOINTS_PER_HAPLOTYPE = 1_000
MAX_TRACTS_PER_SIMULATION = 20_000
GENERATOR_NAME = "yeliztli-founder-mosaic-v1"
GENERATOR_SCRIPT_NAME = "06g_generate_simulation.py"
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
FIXTURE_HEADER = ("rsid", "chromosome", "position", "allele1", "allele2")
TRACT_TRUTH_HEADER = (
    "sim_iid",
    "chrom",
    "haplotype",
    "start_marker_index",
    "end_marker_index_exclusive",
    "donor_iid",
    "source_hap",
)
VCF_FIXED_HEADER = ("#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT")
INDEX_HASH_FIELD = "per_chromosome_vcf_index_sha256"


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    """A stable, content-addressed regular-file snapshot."""

    path: Path
    sha256: str
    device: int
    inode: int
    size_bytes: int
    mtime_ns: int
    ctime_ns: int

    @property
    def signature(self) -> tuple[int, int, int, int, int]:
        return self.device, self.inode, self.size_bytes, self.mtime_ns, self.ctime_ns

    def public(self) -> dict[str, object]:
        return {
            "filename": self.path.name,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class SimulationSpec:
    iid: str
    split: str
    donor_iids: frozenset[str]
    marker_truth_sha256: str
    tract_truth_sha256: str
    window_truth_sha256: str
    fixture_sha256: str
    generation: int
    validation_stratum: str


@dataclass(frozen=True, slots=True)
class ManifestContract:
    path: Path
    snapshot: FileSnapshot
    payload: Mapping[str, object]
    dataset_id: str
    generator_script_sha256: str
    generator_code_revision: str
    generator_environment_lock_sha256: str
    donor_haplotype_source: Mapping[str, object]
    models: Mapping[str, object]
    marker_counts: Mapping[str, int]
    vcf_hashes: Mapping[str, str]
    index_hashes: Mapping[str, str]
    sample_id_hashes: Mapping[str, str] | None
    sample_counts: Mapping[str, int] | None
    max_breakpoints_by_autosome: Mapping[str, int]
    splits: Mapping[str, tuple[str, ...]]
    simulations: Mapping[str, SimulationSpec]


@dataclass(frozen=True, slots=True)
class TruthRow:
    iid: str
    chrom: str
    marker_index: int
    position: int
    rsid: str
    hap0_donor: str
    hap0_source_hap: int
    hap1_donor: str
    hap1_source_hap: int


@dataclass(frozen=True, slots=True)
class FixtureRow:
    chrom: str
    position: int
    rsid: str
    allele1: str
    allele2: str


@dataclass(frozen=True, slots=True)
class TruthTract:
    chrom: str
    haplotype: int
    start: int
    end: int
    donor_iid: str
    source_hap: int


@dataclass(frozen=True, slots=True)
class VcfRecord:
    line_number: int
    chrom: str
    position: int
    identifiers: frozenset[str]
    ref: str
    alt: str
    format_fields: tuple[str, ...]
    sample_fields: tuple[str, ...]


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != SHA256_LENGTH:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def _sha256_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_autosome(raw: str) -> str | None:
    token = raw.strip()
    if token.lower().startswith("chr"):
        token = token[3:]
    try:
        number = int(token)
    except ValueError:
        return None
    canonical = str(number)
    return canonical if canonical in AUTOSOME_SET else None


def _absolute_path_without_symlinks(path: Path, *, label: str) -> Path:
    """Return an absolute path after refusing every existing symlink component."""
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


def _regular_file_metadata(path: Path, *, label: str) -> os.stat_result:
    path = _absolute_path_without_symlinks(path, label=label)
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"{label}: file does not exist: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label}: expected a non-symlink regular file: {path}")
    return metadata


def _metadata_signature(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _parse_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if not _is_sha256(normalized):
        raise argparse.ArgumentTypeError("expected a complete 64-character SHA-256 digest")
    return normalized


def _open_binary_nofollow(
    path: Path,
    *,
    label: str,
    expected_snapshot: FileSnapshot | None = None,
) -> io.BufferedReader:
    path_metadata = _regular_file_metadata(path, label=label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{label}: could not safely open {path}: {exc}") from exc
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or _metadata_signature(metadata) != _metadata_signature(path_metadata)
        or (
            expected_snapshot is not None
            and _metadata_signature(metadata) != expected_snapshot.signature
        )
    ):
        os.close(descriptor)
        raise ValueError(f"{label}: file changed while it was opened: {path}")
    return io.BufferedReader(io.FileIO(descriptor, mode="rb", closefd=True))


def _snapshot_file(path: Path, *, label: str) -> FileSnapshot:
    absolute = _absolute_path_without_symlinks(path, label=label)
    before = _regular_file_metadata(absolute, label=label)
    digest = hashlib.sha256()
    size_bytes = 0
    with _open_binary_nofollow(absolute, label=label) as handle:
        opened_before = os.fstat(handle.fileno())
        while chunk := handle.read(READ_CHUNK_BYTES):
            digest.update(chunk)
            size_bytes += len(chunk)
        opened_after = os.fstat(handle.fileno())
    after = _regular_file_metadata(absolute, label=label)
    if (
        _metadata_signature(before) != _metadata_signature(opened_before)
        or _metadata_signature(opened_before) != _metadata_signature(opened_after)
        or _metadata_signature(opened_after) != _metadata_signature(after)
        or size_bytes != after.st_size
    ):
        raise ValueError(f"{label}: file changed while it was hashed: {absolute}")
    return FileSnapshot(
        path=absolute,
        sha256=digest.hexdigest(),
        device=after.st_dev,
        inode=after.st_ino,
        size_bytes=size_bytes,
        mtime_ns=after.st_mtime_ns,
        ctime_ns=after.st_ctime_ns,
    )


def _assert_snapshot_unchanged(snapshot: FileSnapshot, *, label: str) -> None:
    metadata = _regular_file_metadata(snapshot.path, label=label)
    signature = _metadata_signature(metadata)
    if signature != snapshot.signature:
        raise ValueError(f"{label}: file changed during verification: {snapshot.path}")


def _read_json_regular(path: Path, *, label: str) -> tuple[Mapping[str, object], FileSnapshot]:
    snapshot = _snapshot_file(path, label=label)
    if snapshot.size_bytes > MAX_MANIFEST_BYTES:
        raise ValueError(
            f"{label}: JSON exceeds the {MAX_MANIFEST_BYTES}-byte safety limit: {path}"
        )
    with _open_binary_nofollow(
        snapshot.path,
        label=label,
        expected_snapshot=snapshot,
    ) as handle:
        raw = handle.read()
    _assert_snapshot_unchanged(snapshot, label=label)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label}: expected UTF-8 JSON: {snapshot.path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label}: top-level JSON value must be an object")
    return payload, snapshot


@contextlib.contextmanager
def _open_text_auto(
    path: Path,
    *,
    label: str,
    expected_snapshot: FileSnapshot | None = None,
) -> Iterator[TextIO]:
    """Open plain or gzip-compressed UTF-8 text without following symlinks."""
    raw = _open_binary_nofollow(
        path,
        label=label,
        expected_snapshot=expected_snapshot,
    )
    text: TextIO | None = None
    try:
        magic = raw.peek(2)[:2]
        binary: io.BufferedIOBase
        if magic == b"\x1f\x8b":
            binary = gzip.GzipFile(fileobj=raw, mode="rb")
        else:
            binary = raw
        text = io.TextIOWrapper(binary, encoding="utf-8", errors="strict", newline=None)
        yield text
        if expected_snapshot is not None:
            if _metadata_signature(os.fstat(raw.fileno())) != expected_snapshot.signature:
                raise ValueError(f"{label}: file changed while it was parsed: {path}")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{label}: could not decode {path}: {exc}") from exc
    finally:
        if text is not None:
            text.close()
        else:
            raw.close()
    if expected_snapshot is not None:
        _assert_snapshot_unchanged(expected_snapshot, label=label)


def _require_hash_mapping(
    source: Mapping[str, object],
    *,
    field: str,
    label: str,
) -> dict[str, str]:
    raw = source.get(field)
    if not isinstance(raw, Mapping) or set(raw) != AUTOSOME_SET:
        raise ValueError(f"simulation manifest: {label} must cover autosomes 1..22 exactly")
    result: dict[str, str] = {}
    for chrom in AUTOSOMES:
        value = raw.get(chrom)
        if not _is_sha256(value):
            raise ValueError(f"simulation manifest: invalid {label} for chr{chrom}")
        result[chrom] = str(value)
    return result


def _optional_hash_mapping(
    source: Mapping[str, object],
    *,
    field: str,
    label: str,
) -> dict[str, str] | None:
    if field not in source:
        return None
    return _require_hash_mapping(source, field=field, label=label)


def _optional_count_mapping(
    source: Mapping[str, object],
    *,
    field: str,
) -> dict[str, int] | None:
    if field not in source:
        return None
    raw = source.get(field)
    if not isinstance(raw, Mapping) or set(raw) != AUTOSOME_SET:
        raise ValueError(
            "simulation manifest: per-chromosome sample counts must cover autosomes 1..22"
        )
    result: dict[str, int] = {}
    for chrom in AUTOSOMES:
        value = raw.get(chrom)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"simulation manifest: invalid sample count for chr{chrom}")
        result[chrom] = value
    return result


def _load_manifest(path: Path) -> ManifestContract:
    payload, snapshot = _read_json_regular(path, label="simulation manifest")
    if payload.get("schema_version") != 2:
        raise ValueError("simulation manifest: expected schema_version 2")
    dataset_id = payload.get("dataset_id")
    if not isinstance(dataset_id, str) or not dataset_id.strip():
        raise ValueError("simulation manifest: dataset_id must be non-empty")

    generator = payload.get("generator")
    if not isinstance(generator, Mapping) or not _is_sha256(generator.get("script_sha256")):
        raise ValueError("simulation manifest: generator.script_sha256 is invalid")
    if generator.get("name") != GENERATOR_NAME:
        raise ValueError(f"simulation manifest: generator.name must be {GENERATOR_NAME!r}")
    generator_revision = generator.get("code_revision")
    if (
        not isinstance(generator_revision, str)
        or len(generator_revision) != 40
        or any(character not in "0123456789abcdef" for character in generator_revision)
        or not _is_sha256(generator.get("environment_lock_sha256"))
    ):
        raise ValueError("simulation manifest: generator revision/environment lock is invalid")

    source = payload.get("donor_haplotype_source")
    if not isinstance(source, Mapping) or source.get("genome_build") != "GRCh38":
        raise ValueError("simulation manifest: donor source must declare genome_build GRCh38")
    vcf_hashes = _require_hash_mapping(
        source,
        field="per_chromosome_vcf_sha256",
        label="source VCF SHA-256 mapping",
    )
    index_hashes = _require_hash_mapping(
        source,
        field=INDEX_HASH_FIELD,
        label="source VCF index SHA-256 mapping",
    )
    sample_id_hashes = _optional_hash_mapping(
        source,
        field="per_chromosome_sample_ids_sha256",
        label="source VCF sample-ID SHA-256 mapping",
    )
    sample_counts = _optional_count_mapping(
        source,
        field="per_chromosome_sample_count",
    )

    protocol = payload.get("simulation_protocol")
    if not isinstance(protocol, Mapping):
        raise ValueError("simulation manifest: simulation_protocol must be an object")
    raw_max_breakpoints = protocol.get("max_breakpoints_per_haplotype_by_autosome")
    if not isinstance(raw_max_breakpoints, Mapping) or set(raw_max_breakpoints) != AUTOSOME_SET:
        raise ValueError(
            "simulation manifest: breakpoint maxima must cover autosomes 1..22 exactly"
        )
    max_breakpoints_by_autosome: dict[str, int] = {}
    for chrom in AUTOSOMES:
        value = raw_max_breakpoints.get(chrom)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or value > MAX_BREAKPOINTS_PER_HAPLOTYPE
        ):
            raise ValueError(
                "simulation manifest: breakpoint maximum for "
                f"chr{chrom} must be 0..{MAX_BREAKPOINTS_PER_HAPLOTYPE}"
            )
        max_breakpoints_by_autosome[chrom] = value

    models = payload.get("models")
    if not isinstance(models, Mapping):
        raise ValueError("simulation manifest: models must be an object")
    per_chromosome = models.get("per_chromosome")
    if not isinstance(per_chromosome, Mapping) or set(per_chromosome) != AUTOSOME_SET:
        raise ValueError("simulation manifest: models must cover autosomes 1..22 exactly")
    marker_counts: dict[str, int] = {}
    for chrom in AUTOSOMES:
        entry = per_chromosome.get(chrom)
        if not isinstance(entry, Mapping):
            raise ValueError(f"simulation manifest: model chr{chrom} must be an object")
        marker_count = entry.get("C")
        if (
            not isinstance(marker_count, int)
            or isinstance(marker_count, bool)
            or marker_count <= 0
        ):
            raise ValueError(f"simulation manifest: model chr{chrom}.C must be positive")
        marker_counts[chrom] = marker_count

    raw_simulations = payload.get("simulations")
    if (
        not isinstance(raw_simulations, list)
        or not raw_simulations
        or len(raw_simulations) > MAX_SIMULATIONS
    ):
        raise ValueError(
            f"simulation manifest: simulations must contain 1..{MAX_SIMULATIONS} entries"
        )
    simulations: dict[str, SimulationSpec] = {}
    for index, raw_entry in enumerate(raw_simulations):
        if not isinstance(raw_entry, Mapping):
            raise ValueError(f"simulation manifest: simulation {index} must be an object")
        iid = raw_entry.get("iid")
        split = raw_entry.get("split")
        raw_donors = raw_entry.get("donor_iids")
        if not isinstance(iid, str) or not iid or iid in simulations:
            raise ValueError(f"simulation manifest: simulation {index} has invalid/duplicate IID")
        if split not in {"calibration", "final_confirmation"}:
            raise ValueError(f"simulation manifest: simulation {iid!r} has an invalid split")
        if (
            not isinstance(raw_donors, list)
            or not raw_donors
            or not all(isinstance(donor, str) and donor for donor in raw_donors)
            or len(raw_donors) != len(set(raw_donors))
            or raw_donors != sorted(raw_donors)
        ):
            raise ValueError(f"simulation manifest: simulation {iid!r} donor_iids are invalid")
        hashes: dict[str, str] = {}
        for field in (
            "marker_truth_sha256",
            "tract_truth_sha256",
            "window_truth_sha256",
            "fixture_sha256",
        ):
            value = raw_entry.get(field)
            if not _is_sha256(value):
                raise ValueError(f"simulation manifest: simulation {iid!r} {field} is invalid")
            hashes[field] = str(value)
        generation = raw_entry.get("generation")
        validation_stratum = raw_entry.get("validation_stratum")
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
            or not isinstance(validation_stratum, str)
            or not validation_stratum
        ):
            raise ValueError(
                f"simulation manifest: simulation {iid!r} generation/stratum are invalid"
            )
        simulations[iid] = SimulationSpec(
            iid=iid,
            split=str(split),
            donor_iids=frozenset(raw_donors),
            marker_truth_sha256=hashes["marker_truth_sha256"],
            tract_truth_sha256=hashes["tract_truth_sha256"],
            window_truth_sha256=hashes["window_truth_sha256"],
            fixture_sha256=hashes["fixture_sha256"],
            generation=generation,
            validation_stratum=validation_stratum,
        )
    raw_splits = payload.get("splits")
    if not isinstance(raw_splits, Mapping) or set(raw_splits) != {
        "calibration",
        "final_confirmation",
    }:
        raise ValueError(
            "simulation manifest: splits must declare calibration and final_confirmation"
        )
    splits: dict[str, tuple[str, ...]] = {}
    assigned: set[str] = set()
    for split in ("calibration", "final_confirmation"):
        raw_iids = raw_splits[split]
        if (
            not isinstance(raw_iids, list)
            or not raw_iids
            or not all(isinstance(iid, str) and iid for iid in raw_iids)
            or raw_iids != sorted(raw_iids)
            or len(raw_iids) != len(set(raw_iids))
        ):
            raise ValueError(f"simulation manifest: split {split!r} is not canonical")
        iids = tuple(raw_iids)
        if assigned & set(iids):
            raise ValueError("simulation manifest: simulation IID occurs in both splits")
        if any(iid not in simulations or simulations[iid].split != split for iid in iids):
            raise ValueError(f"simulation manifest: split {split!r} disagrees with simulations")
        assigned.update(iids)
        splits[split] = iids
    if assigned != set(simulations):
        raise ValueError("simulation manifest: splits must cover every simulation exactly")
    return ManifestContract(
        path=snapshot.path,
        snapshot=snapshot,
        payload=payload,
        dataset_id=dataset_id,
        generator_script_sha256=str(generator["script_sha256"]),
        generator_code_revision=generator_revision,
        generator_environment_lock_sha256=str(generator["environment_lock_sha256"]),
        donor_haplotype_source=source,
        models=models,
        marker_counts=marker_counts,
        vcf_hashes=vcf_hashes,
        index_hashes=index_hashes,
        sample_id_hashes=sample_id_hashes,
        sample_counts=sample_counts,
        max_breakpoints_by_autosome=max_breakpoints_by_autosome,
        splits=splits,
        simulations=simulations,
    )


def _parse_path_specs(
    values: Sequence[str],
    *,
    option: str,
    valid_keys: frozenset[str] | None = None,
) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for raw in values:
        key, separator, raw_path = raw.partition("=")
        if not separator or not key or not raw_path:
            raise ValueError(f"{option}: expected KEY=PATH, got {raw!r}")
        if key in parsed:
            raise ValueError(f"{option}: duplicate key {key!r}")
        if valid_keys is not None and key not in valid_keys:
            raise ValueError(f"{option}: invalid key {key!r}")
        parsed[key] = _absolute_path_without_symlinks(Path(raw_path), label=option)
    return parsed


def _split_commitment(manifest: ManifestContract, split: str) -> str:
    simulations = []
    for iid in manifest.splits[split]:
        spec = manifest.simulations[iid]
        simulations.append(
            {
                "iid": iid,
                "fixture_sha256": spec.fixture_sha256,
                "marker_truth_sha256": spec.marker_truth_sha256,
                "tract_truth_sha256": spec.tract_truth_sha256,
                "window_truth_sha256": spec.window_truth_sha256,
                "donor_iids": sorted(spec.donor_iids),
                "generation": spec.generation,
                "validation_stratum": spec.validation_stratum,
            }
        )
    return _sha256_json(
        {
            "schema_version": 1,
            "dataset_id": manifest.dataset_id,
            "split": split,
            "simulations": simulations,
        }
    )


def _truth_rows(handle: TextIO, path: Path) -> Iterator[TruthRow]:
    for line_number, raw in enumerate(handle, start=1):
        line = raw.rstrip("\r\n")
        columns = tuple(line.split("\t"))
        if line_number == 1:
            if columns != MARKER_TRUTH_HEADER:
                raise ValueError(f"{path}: expected exact marker-truth header")
            continue
        if not line or len(columns) != len(MARKER_TRUTH_HEADER):
            raise ValueError(f"{path}:{line_number}: expected exactly nine columns")
        (
            iid,
            raw_chrom,
            raw_index,
            raw_position,
            rsid,
            hap0_donor,
            raw_hap0_source,
            hap1_donor,
            raw_hap1_source,
        ) = columns
        chrom = _canonical_autosome(raw_chrom)
        try:
            marker_index = int(raw_index)
            position = int(raw_position)
            hap0_source = int(raw_hap0_source)
            hap1_source = int(raw_hap1_source)
        except ValueError:
            raise ValueError(f"{path}:{line_number}: invalid integer field") from None
        if chrom is None or marker_index < 0 or position <= 0:
            raise ValueError(f"{path}:{line_number}: invalid marker coordinate/index")
        if not rsid or rsid == "." or ";" in rsid:
            raise ValueError(f"{path}:{line_number}: expected one non-empty marker rsID")
        if (
            not hap0_donor
            or not hap1_donor
            or hap0_source not in {0, 1}
            or hap1_source not in {0, 1}
        ):
            raise ValueError(f"{path}:{line_number}: invalid donor/source-haplotype truth")
        yield TruthRow(
            iid=iid,
            chrom=chrom,
            marker_index=marker_index,
            position=position,
            rsid=rsid,
            hap0_donor=hap0_donor,
            hap0_source_hap=hap0_source,
            hap1_donor=hap1_donor,
            hap1_source_hap=hap1_source,
        )


def _fixture_rows(handle: TextIO, path: Path) -> Iterator[FixtureRow]:
    saw_header = False
    for line_number, raw in enumerate(handle, start=1):
        line = raw.rstrip("\r\n")
        if not line or line.startswith("#"):
            continue
        columns = tuple(line.split("\t"))
        if not saw_header:
            if tuple(column.strip().lower() for column in columns) != FIXTURE_HEADER:
                raise ValueError(
                    f"{path}:{line_number}: expected exact five-column fixture header"
                )
            saw_header = True
            continue
        if len(columns) != len(FIXTURE_HEADER):
            raise ValueError(f"{path}:{line_number}: expected exactly five fixture columns")
        rsid, raw_chrom, raw_position, raw_allele1, raw_allele2 = (
            column.strip() for column in columns
        )
        chrom = _canonical_autosome(raw_chrom)
        try:
            position = int(raw_position)
        except ValueError:
            raise ValueError(f"{path}:{line_number}: fixture position is not an integer") from None
        allele1, allele2 = raw_allele1.upper(), raw_allele2.upper()
        if (
            not rsid
            or rsid == "."
            or ";" in rsid
            or chrom is None
            or position <= 0
            or allele1 not in DNA_BASES
            or allele2 not in DNA_BASES
        ):
            raise ValueError(f"{path}:{line_number}: invalid fixture marker/genotype")
        yield FixtureRow(chrom, position, rsid, allele1, allele2)
    if not saw_header:
        raise ValueError(f"{path}: missing fixture header")


def _tract_contract(
    handle: TextIO,
    path: Path,
    *,
    simulation: SimulationSpec,
    marker_counts: Mapping[str, int],
    max_breakpoints_by_autosome: Mapping[str, int],
) -> dict[tuple[str, int], tuple[TruthTract, ...]]:
    """Validate exact founder-tract coverage independently of the generator."""
    grouped: dict[tuple[str, int], list[TruthTract]] = {}
    tract_count = 0
    for line_number, raw in enumerate(handle, start=1):
        columns = tuple(raw.rstrip("\r\n").split("\t"))
        if line_number == 1:
            if columns != TRACT_TRUTH_HEADER:
                raise ValueError(f"{path}: expected exact tract-truth header")
            continue
        if len(columns) != len(TRACT_TRUTH_HEADER) or any(not value for value in columns):
            raise ValueError(f"{path}:{line_number}: expected exactly seven tract columns")
        raw_iid, raw_chrom, raw_haplotype, raw_start, raw_end, donor_iid, raw_source = columns
        chrom = _canonical_autosome(raw_chrom)
        try:
            haplotype = int(raw_haplotype)
            start = int(raw_start)
            end = int(raw_end)
            source_hap = int(raw_source)
        except ValueError:
            raise ValueError(f"{path}:{line_number}: invalid tract integer field") from None
        if (
            raw_iid != simulation.iid
            or chrom is None
            or haplotype not in {0, 1}
            or start < 0
            or end <= start
            or donor_iid not in simulation.donor_iids
            or source_hap not in {0, 1}
        ):
            raise ValueError(f"{path}:{line_number}: invalid tract identity/interval")
        grouped.setdefault((chrom, haplotype), []).append(
            TruthTract(chrom, haplotype, start, end, donor_iid, source_hap)
        )
        tract_count += 1
        if tract_count > MAX_TRACTS_PER_SIMULATION:
            raise ValueError(f"{path}: exceeds the {MAX_TRACTS_PER_SIMULATION}-tract safety limit")

    contract: dict[tuple[str, int], tuple[TruthTract, ...]] = {}
    for chrom in AUTOSOMES:
        for haplotype in (0, 1):
            tracts = grouped.get((chrom, haplotype), [])
            if max(0, len(tracts) - 1) > max_breakpoints_by_autosome[chrom]:
                raise ValueError(
                    f"{path}: chr{chrom} hap{haplotype} exceeds its declared breakpoint maximum"
                )
            expected_start = 0
            previous_identity: tuple[str, int] | None = None
            for tract in tracts:
                if tract.start != expected_start or tract.end > marker_counts[chrom]:
                    raise ValueError(f"{path}: chr{chrom} hap{haplotype} has a tract gap/overlap")
                identity = tract.donor_iid, tract.source_hap
                if identity == previous_identity:
                    raise ValueError(
                        f"{path}: adjacent identical chr{chrom} hap{haplotype} tracts"
                    )
                expected_start = tract.end
                previous_identity = identity
            if expected_start != marker_counts[chrom]:
                raise ValueError(
                    f"{path}: chr{chrom} hap{haplotype} does not cover all model markers"
                )
            contract[(chrom, haplotype)] = tuple(tracts)
    return contract


class VcfReader:
    """Streaming lookup of ordered model markers in one chromosome VCF."""

    def __init__(
        self,
        *,
        path: Path,
        chrom: str,
        donor_iids: frozenset[str],
        expected_snapshot: FileSnapshot,
        expected_sample_ids_sha256: str | None,
        expected_sample_count: int | None,
    ) -> None:
        self.path = path
        self.chrom = chrom
        self.donor_iids = donor_iids
        self.expected_snapshot = expected_snapshot
        self.expected_sample_ids_sha256 = expected_sample_ids_sha256
        self.expected_sample_count = expected_sample_count
        self._context: contextlib.AbstractContextManager[TextIO] | None = None
        self._handle: TextIO | None = None
        self._line_number = 0
        self._sample_ids: tuple[str, ...] = ()
        self._sample_indexes: Mapping[str, int] = {}
        self._lookahead: VcfRecord | None = None
        self._last_position = 0
        self.records_scanned = 0

    @property
    def sample_ids(self) -> tuple[str, ...]:
        return self._sample_ids

    def __enter__(self) -> VcfReader:
        self._context = _open_text_auto(
            self.path,
            label=f"source VCF chr{self.chrom}",
            expected_snapshot=self.expected_snapshot,
        )
        self._handle = self._context.__enter__()
        self._read_header()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._context is not None:
            self._context.__exit__(exc_type, exc, traceback)

    def _read_header(self) -> None:
        assert self._handle is not None
        for raw in self._handle:
            self._line_number += 1
            line = raw.rstrip("\r\n")
            if line.startswith("##"):
                continue
            columns = tuple(line.split("\t"))
            if tuple(columns[: len(VCF_FIXED_HEADER)]) != VCF_FIXED_HEADER:
                raise ValueError(f"{self.path}:{self._line_number}: invalid VCF header")
            sample_ids = columns[len(VCF_FIXED_HEADER) :]
            if (
                not sample_ids
                or any(not iid for iid in sample_ids)
                or len(sample_ids) != len(set(sample_ids))
            ):
                raise ValueError(
                    f"{self.path}:{self._line_number}: invalid/duplicate VCF sample IDs"
                )
            missing = sorted(self.donor_iids - set(sample_ids))
            if missing:
                raise ValueError(
                    f"{self.path}: declared donor sample IDs absent from "
                    f"chr{self.chrom}: {missing}"
                )
            sample_hash = _sha256_json(sorted(sample_ids))
            if (
                self.expected_sample_ids_sha256 is not None
                and sample_hash != self.expected_sample_ids_sha256
            ):
                raise ValueError(f"{self.path}: sample-ID SHA-256 does not match manifest")
            if (
                self.expected_sample_count is not None
                and len(sample_ids) != self.expected_sample_count
            ):
                raise ValueError(f"{self.path}: sample count does not match manifest")
            self._sample_ids = sample_ids
            all_sample_indexes = {iid: index for index, iid in enumerate(sample_ids)}
            self._sample_indexes = {iid: all_sample_indexes[iid] for iid in self.donor_iids}
            return
        raise ValueError(f"{self.path}: missing VCF #CHROM header")

    def _next_record(self) -> VcfRecord | None:
        assert self._handle is not None
        for raw in self._handle:
            self._line_number += 1
            line = raw.rstrip("\r\n")
            if not line or line.startswith("#"):
                raise ValueError(
                    f"{self.path}:{self._line_number}: invalid record/header ordering"
                )
            columns = tuple(line.split("\t"))
            expected_columns = len(VCF_FIXED_HEADER) + len(self._sample_ids)
            if len(columns) != expected_columns:
                raise ValueError(
                    f"{self.path}:{self._line_number}: expected {expected_columns} VCF columns"
                )
            chrom = _canonical_autosome(columns[0])
            try:
                position = int(columns[1])
            except ValueError:
                raise ValueError(
                    f"{self.path}:{self._line_number}: invalid VCF position"
                ) from None
            if chrom != self.chrom or position <= 0 or position < self._last_position:
                raise ValueError(
                    f"{self.path}:{self._line_number}: VCF is not ordered chr{self.chrom}"
                )
            identifiers = frozenset(
                token for token in columns[2].split(";") if token and token != "."
            )
            self._last_position = position
            self.records_scanned += 1
            return VcfRecord(
                line_number=self._line_number,
                chrom=chrom,
                position=position,
                identifiers=identifiers,
                ref=columns[3].upper(),
                alt=columns[4].upper(),
                format_fields=tuple(columns[8].split(":")),
                sample_fields=columns[9:],
            )
        return None

    def find(self, *, position: int, rsid: str) -> VcfRecord:
        candidates: list[VcfRecord] = []
        while True:
            if self._lookahead is None:
                self._lookahead = self._next_record()
            record = self._lookahead
            if record is None or record.position > position:
                break
            self._lookahead = None
            if record.position < position:
                continue
            if rsid in record.identifiers:
                candidates.append(record)
        if not candidates:
            raise ValueError(
                f"{self.path}: missing source VCF marker chr{self.chrom}:{position}:{rsid}"
            )
        if len(candidates) != 1:
            raise ValueError(
                f"{self.path}: duplicate source VCF marker chr{self.chrom}:{position}:{rsid}"
            )
        return candidates[0]

    def donor_alleles(self, record: VcfRecord) -> Mapping[str, tuple[str, str]]:
        if (
            len(record.ref) != 1
            or record.ref not in DNA_BASES
            or len(record.alt) != 1
            or record.alt not in DNA_BASES
            or "," in record.alt
        ):
            raise ValueError(
                f"{self.path}:{record.line_number}: model marker must be a biallelic SNV"
            )
        if record.format_fields.count("GT") != 1:
            raise ValueError(f"{self.path}:{record.line_number}: FORMAT must contain one GT field")
        genotype_index = record.format_fields.index("GT")
        allele_bases = (record.ref, record.alt)
        result: dict[str, tuple[str, str]] = {}
        for iid, sample_index in self._sample_indexes.items():
            fields = record.sample_fields[sample_index].split(":")
            if genotype_index >= len(fields):
                raise ValueError(f"{self.path}:{record.line_number}: donor {iid!r} lacks GT")
            genotype = fields[genotype_index]
            if "/" in genotype:
                raise ValueError(
                    f"{self.path}:{record.line_number}: donor {iid!r} GT is not phased"
                )
            alleles = genotype.split("|")
            if len(alleles) != 2 or any(allele not in {"0", "1"} for allele in alleles):
                raise ValueError(
                    f"{self.path}:{record.line_number}: donor {iid!r} GT is not diploid biallelic"
                )
            result[iid] = (allele_bases[int(alleles[0])], allele_bases[int(alleles[1])])
        return result


def _current_code_revision(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    revision = completed.stdout.strip()
    if completed.returncode != 0 or len(revision) != 40:
        detail = completed.stderr.strip() or "git rev-parse failed"
        raise ValueError(f"could not determine verifier code revision: {detail}")
    return revision


def _verify_repository_generator_script(path: Path, repo_root: Path) -> None:
    """Require the generator implementation to be reviewed, tracked, and clean."""
    absolute = _absolute_path_without_symlinks(path, label="simulation generator script")
    try:
        relative = absolute.relative_to(repo_root)
    except ValueError:
        raise ValueError("simulation generator script must live in the repository") from None
    if relative.parent != Path("scripts/lai_bundle_v2"):
        raise ValueError("simulation generator script must live under scripts/lai_bundle_v2")
    if relative.name != GENERATOR_SCRIPT_NAME:
        raise ValueError(
            f"simulation generator script must be the reviewed {GENERATOR_SCRIPT_NAME}"
        )
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


def _verify_repository_verifier_files(repo_root: Path) -> None:
    """Bind the direct verifier and policy gate to their clean HEAD implementations."""
    relatives = (
        Path("scripts/lai_bundle_v2/06g_verify_simulation.py"),
        Path("scripts/lai_bundle_v2/lai_coverage_policy.py"),
    )
    tracked = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "ls-files",
            "--error-unmatch",
            "--",
            *(str(path) for path in relatives),
        ],
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
            *(str(path) for path in relatives),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if tracked.returncode != 0 or status.returncode != 0 or status.stdout.strip():
        raise ValueError("verifier and policy code must be tracked and clean at HEAD")


def _verify_hash(actual: FileSnapshot, expected: str, *, label: str) -> None:
    if actual.sha256 != expected:
        raise ValueError(f"{label}: SHA-256 does not match simulation manifest")


def _next_required(iterator: Iterator[Any], *, label: str) -> Any:
    try:
        return next(iterator)
    except StopIteration:
        raise ValueError(f"{label}: ended before all model markers were verified") from None


def _atomic_write_json(
    path: Path, payload: Mapping[str, object], *, input_paths: set[Path]
) -> None:
    output = _absolute_path_without_symlinks(path, label="output")
    parent = output.parent
    parent_metadata = parent.lstat() if parent.exists() else None
    if (
        parent_metadata is None
        or stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
    ):
        raise ValueError(f"output: parent must be an existing non-symlink directory: {parent}")
    if output in input_paths:
        raise ValueError("output: refusing to overwrite an input file")
    if output.exists() or output.is_symlink():
        metadata = output.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"output: existing path is not a regular file: {output}")
        for input_path in input_paths:
            try:
                if os.path.samefile(output, input_path):
                    raise ValueError("output: refusing to overwrite a hardlinked input file")
            except FileNotFoundError:
                continue
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    directory_descriptor = os.open(parent, directory_flags)
    opened_parent = os.fstat(directory_descriptor)
    parent_identity = (opened_parent.st_dev, opened_parent.st_ino)
    if parent_identity != (parent_metadata.st_dev, parent_metadata.st_ino):
        os.close(directory_descriptor)
        raise ValueError("output: parent directory changed while it was opened")
    lock_descriptor = -1
    lock_handle: Any = None
    temporary_name: str | None = None
    descriptor = -1
    try:
        lock_flags = (
            os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        lock_descriptor = os.open(
            f".{output.name}.lock",
            lock_flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        lock_metadata = os.fstat(lock_descriptor)
        if not stat.S_ISREG(lock_metadata.st_mode) or lock_metadata.st_nlink != 1:
            raise ValueError("output: lock is not a private regular file")
        lock_handle = os.fdopen(lock_descriptor, "a+b", closefd=True)
        lock_descriptor = -1
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("output: another verifier is already publishing this stamp")
        create_flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        for _attempt in range(128):
            candidate = f".{output.name}.{secrets.token_hex(12)}.tmp"
            try:
                descriptor = os.open(
                    candidate,
                    create_flags,
                    0o600,
                    dir_fd=directory_descriptor,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if temporary_name is None:
            raise OSError("could not allocate a unique temporary output file")
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temporary_name,
            output.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        temporary_name = None
        os.fsync(directory_descriptor)
        try:
            live_parent = parent.lstat()
        except OSError as exc:
            os.unlink(output.name, dir_fd=directory_descriptor)
            os.fsync(directory_descriptor)
            raise ValueError("output: parent directory changed during publication") from exc
        if (live_parent.st_dev, live_parent.st_ino) != parent_identity:
            os.unlink(output.name, dir_fd=directory_descriptor)
            os.fsync(directory_descriptor)
            raise ValueError("output: parent directory changed during publication")
    finally:
        if lock_handle is not None:
            lock_handle.close()
        elif lock_descriptor >= 0:
            os.close(lock_descriptor)
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
        os.close(directory_descriptor)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulation-manifest", required=True, type=Path)
    parser.add_argument(
        "--dataset-split",
        required=True,
        choices=("calibration", "final_confirmation"),
    )
    parser.add_argument(
        "--confirmation-policy",
        type=Path,
        help="frozen policy required before opening final-confirmation truth",
    )
    parser.add_argument(
        "--expected-confirmation-policy-sha256",
        type=_parse_sha256,
        help="independently recorded SHA-256 of the frozen confirmation policy",
    )
    parser.add_argument(
        "--fixture",
        action="append",
        default=[],
        metavar="IID=PATH",
        help="Simulated five-column fixture; repeat once for every manifest IID.",
    )
    parser.add_argument(
        "--marker-truth",
        action="append",
        default=[],
        metavar="IID=PATH",
        help="Nine-column marker truth; repeat once for every manifest IID.",
    )
    parser.add_argument(
        "--tract-truth",
        action="append",
        default=[],
        metavar="IID=PATH",
        help="Founder-tract truth bound by the manifest; repeat for every IID.",
    )
    parser.add_argument(
        "--donor-vcf",
        action="append",
        default=[],
        metavar="CHROM=PATH",
        help="Pinned donor VCF (plain or gzip); repeat for chromosomes 1 through 22.",
    )
    parser.add_argument(
        "--donor-vcf-index",
        "--index",
        dest="donor_vcf_index",
        action="append",
        default=[],
        metavar="CHROM=PATH",
        help="Pinned donor VCF index; repeat for chromosomes 1 through 22.",
    )
    parser.add_argument(
        "--verifier-environment-lock",
        "--environment-lock",
        dest="verifier_environment_lock",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--generator-script",
        required=True,
        type=Path,
        help="Actual simulation-generator source file pinned by generator.script_sha256.",
    )
    parser.add_argument(
        "--generator-environment-lock",
        required=True,
        type=Path,
        help="Actual generator environment lock pinned by the simulation manifest.",
    )
    parser.add_argument("--expected-code-revision", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def verify(args: argparse.Namespace) -> Mapping[str, object]:
    manifest = _load_manifest(args.simulation_manifest)
    repo_root = Path(__file__).absolute().parents[2]
    actual_revision = _current_code_revision(repo_root)
    if args.expected_code_revision != actual_revision:
        raise ValueError(
            "--expected-code-revision does not match the verifier repository HEAD "
            f"({actual_revision})"
        )
    _verify_repository_verifier_files(repo_root)
    confirmation_policy_entry: Mapping[str, object] | None = None
    confirmation_policy_path: Path | None = None
    if args.dataset_split == "final_confirmation":
        if args.confirmation_policy is None or args.expected_confirmation_policy_sha256 is None:
            raise ValueError(
                "final_confirmation verification requires --confirmation-policy and "
                "--expected-confirmation-policy-sha256 before truth may be opened"
            )
        confirmation_policy_path = _absolute_path_without_symlinks(
            args.confirmation_policy,
            label="confirmation policy",
        )
        source_bundle_sha256 = manifest.payload.get("source_bundle_artifact_sha256")
        if not _is_sha256(source_bundle_sha256):
            raise ValueError("simulation manifest: source bundle SHA-256 is invalid")
        policy = read_confirmation_policy(
            confirmation_policy_path,
            dataset_id=manifest.dataset_id,
            bundle_artifact_sha256=str(source_bundle_sha256),
            simulation_manifest_sha256=manifest.snapshot.sha256,
            code_revision=actual_revision,
            final_confirmation_split_commitment_sha256=_split_commitment(
                manifest,
                "final_confirmation",
            ),
            expected_confirmation_policy_sha256=args.expected_confirmation_policy_sha256,
        )
        confirmation_policy_entry = confirmation_policy_provenance(policy)
    elif (
        args.confirmation_policy is not None
        or args.expected_confirmation_policy_sha256 is not None
    ):
        raise ValueError("calibration verification rejects confirmation-policy options")
    selected_simulations = {
        iid: manifest.simulations[iid] for iid in manifest.splits[args.dataset_split]
    }
    fixtures = _parse_path_specs(args.fixture, option="--fixture")
    marker_truth = _parse_path_specs(args.marker_truth, option="--marker-truth")
    tract_truth = _parse_path_specs(args.tract_truth, option="--tract-truth")
    donor_vcfs = _parse_path_specs(
        args.donor_vcf,
        option="--donor-vcf",
        valid_keys=AUTOSOME_SET,
    )
    donor_indexes = _parse_path_specs(
        args.donor_vcf_index,
        option="--donor-vcf-index",
        valid_keys=AUTOSOME_SET,
    )
    expected_iids = set(selected_simulations)
    if (
        set(fixtures) != expected_iids
        or set(marker_truth) != expected_iids
        or set(tract_truth) != expected_iids
    ):
        raise ValueError(
            "--fixture, --marker-truth, and --tract-truth must each cover every "
            "manifest simulation exactly"
        )
    if set(donor_vcfs) != AUTOSOME_SET or set(donor_indexes) != AUTOSOME_SET:
        raise ValueError("--donor-vcf and --donor-vcf-index must each cover chromosomes 1..22")

    if manifest.generator_code_revision != actual_revision:
        raise ValueError("simulation generator revision does not match repository HEAD")
    _verify_repository_generator_script(args.generator_script, repo_root)
    script_snapshot = _snapshot_file(Path(__file__), label="verifier script")
    generator_script_snapshot = _snapshot_file(
        args.generator_script,
        label="simulation generator script",
    )
    if generator_script_snapshot.sha256 != manifest.generator_script_sha256:
        raise ValueError("simulation generator script SHA-256 does not match manifest")
    if script_snapshot.sha256 == generator_script_snapshot.sha256:
        raise ValueError("verifier script SHA-256 equals generator script SHA-256")
    generator_environment_snapshot = _snapshot_file(
        args.generator_environment_lock,
        label="simulation generator environment lock",
    )
    if generator_environment_snapshot.sha256 != manifest.generator_environment_lock_sha256:
        raise ValueError("simulation generator environment lock SHA-256 does not match manifest")
    environment_snapshot = _snapshot_file(
        args.verifier_environment_lock,
        label="verifier environment lock",
    )

    fixture_snapshots: dict[str, FileSnapshot] = {}
    truth_snapshots: dict[str, FileSnapshot] = {}
    tract_snapshots: dict[str, FileSnapshot] = {}
    for iid, spec in selected_simulations.items():
        fixture_snapshot = _snapshot_file(fixtures[iid], label=f"fixture {iid}")
        truth_snapshot = _snapshot_file(marker_truth[iid], label=f"marker truth {iid}")
        tract_snapshot = _snapshot_file(tract_truth[iid], label=f"tract truth {iid}")
        _verify_hash(fixture_snapshot, spec.fixture_sha256, label=f"fixture {iid}")
        _verify_hash(truth_snapshot, spec.marker_truth_sha256, label=f"marker truth {iid}")
        _verify_hash(tract_snapshot, spec.tract_truth_sha256, label=f"tract truth {iid}")
        fixture_snapshots[iid] = fixture_snapshot
        truth_snapshots[iid] = truth_snapshot
        tract_snapshots[iid] = tract_snapshot

    tract_contracts: dict[
        str,
        dict[tuple[str, int], tuple[TruthTract, ...]],
    ] = {}
    for iid, simulation in selected_simulations.items():
        with _open_text_auto(
            tract_truth[iid],
            label=f"tract truth {iid}",
            expected_snapshot=tract_snapshots[iid],
        ) as tract_handle:
            tract_contracts[iid] = _tract_contract(
                tract_handle,
                tract_truth[iid],
                simulation=simulation,
                marker_counts=manifest.marker_counts,
                max_breakpoints_by_autosome=manifest.max_breakpoints_by_autosome,
            )

    vcf_snapshots: dict[str, FileSnapshot] = {}
    index_snapshots: dict[str, FileSnapshot] = {}
    for chrom in AUTOSOMES:
        vcf_snapshot = _snapshot_file(donor_vcfs[chrom], label=f"source VCF chr{chrom}")
        index_snapshot = _snapshot_file(
            donor_indexes[chrom],
            label=f"source VCF index chr{chrom}",
        )
        _verify_hash(vcf_snapshot, manifest.vcf_hashes[chrom], label=f"source VCF chr{chrom}")
        _verify_hash(
            index_snapshot,
            manifest.index_hashes[chrom],
            label=f"source VCF index chr{chrom}",
        )
        vcf_snapshots[chrom] = vcf_snapshot
        index_snapshots[chrom] = index_snapshot

    all_donors = frozenset(
        donor for simulation in selected_simulations.values() for donor in simulation.donor_iids
    )
    expected_rows = sum(manifest.marker_counts.values())
    observed_donors = {iid: set() for iid in selected_simulations}
    row_counts = {iid: 0 for iid in selected_simulations}
    source_reports: dict[str, Mapping[str, object]] = {}

    with contextlib.ExitStack() as stack:
        truth_iterators: dict[str, Iterator[TruthRow]] = {}
        fixture_iterators: dict[str, Iterator[FixtureRow]] = {}
        for iid in sorted(selected_simulations):
            truth_handle = stack.enter_context(
                _open_text_auto(
                    marker_truth[iid],
                    label=f"marker truth {iid}",
                    expected_snapshot=truth_snapshots[iid],
                )
            )
            fixture_handle = stack.enter_context(
                _open_text_auto(
                    fixtures[iid],
                    label=f"fixture {iid}",
                    expected_snapshot=fixture_snapshots[iid],
                )
            )
            truth_iterators[iid] = iter(_truth_rows(truth_handle, marker_truth[iid]))
            fixture_iterators[iid] = iter(_fixture_rows(fixture_handle, fixtures[iid]))

        previous_position: dict[str, int] = {chrom: 0 for chrom in AUTOSOMES}
        tract_offsets: dict[tuple[str, str, int], int] = {}
        for chrom in AUTOSOMES:
            expected_sample_hash = (
                manifest.sample_id_hashes[chrom] if manifest.sample_id_hashes is not None else None
            )
            expected_sample_count = (
                manifest.sample_counts[chrom] if manifest.sample_counts is not None else None
            )
            with VcfReader(
                path=donor_vcfs[chrom],
                chrom=chrom,
                donor_iids=all_donors,
                expected_snapshot=vcf_snapshots[chrom],
                expected_sample_ids_sha256=expected_sample_hash,
                expected_sample_count=expected_sample_count,
            ) as vcf:
                for marker_index in range(manifest.marker_counts[chrom]):
                    rows: dict[str, tuple[TruthRow, FixtureRow]] = {}
                    shared_key: tuple[int, str] | None = None
                    for iid in sorted(selected_simulations):
                        truth_row = _next_required(
                            truth_iterators[iid],
                            label=f"marker truth {iid}",
                        )
                        fixture_row = _next_required(
                            fixture_iterators[iid],
                            label=f"fixture {iid}",
                        )
                        if (
                            truth_row.iid != iid
                            or truth_row.chrom != chrom
                            or truth_row.marker_index != marker_index
                        ):
                            raise ValueError(
                                f"marker truth {iid}: expected chr{chrom} "
                                f"marker_index {marker_index}"
                            )
                        simulation = selected_simulations[iid]
                        truth_donors = {truth_row.hap0_donor, truth_row.hap1_donor}
                        if not truth_donors <= simulation.donor_iids:
                            raise ValueError(
                                f"marker truth {iid}: undeclared donor at "
                                f"chr{chrom}:{marker_index}"
                            )
                        observed_donors[iid].update(truth_donors)
                        for haplotype, donor_iid, source_hap in (
                            (0, truth_row.hap0_donor, truth_row.hap0_source_hap),
                            (1, truth_row.hap1_donor, truth_row.hap1_source_hap),
                        ):
                            key = iid, chrom, haplotype
                            offset = tract_offsets.get(key, 0)
                            tracts = tract_contracts[iid][(chrom, haplotype)]
                            while marker_index >= tracts[offset].end:
                                offset += 1
                            tract_offsets[key] = offset
                            tract = tracts[offset]
                            if (
                                not tract.start <= marker_index < tract.end
                                or tract.donor_iid != donor_iid
                                or tract.source_hap != source_hap
                            ):
                                raise ValueError(
                                    f"marker truth {iid}: contribution does not match "
                                    f"tract truth at chr{chrom}:{marker_index}:hap{haplotype}"
                                )
                        if (
                            fixture_row.chrom != chrom
                            or fixture_row.position != truth_row.position
                            or fixture_row.rsid != truth_row.rsid
                        ):
                            raise ValueError(
                                f"fixture {iid}: rsID/coordinate mismatch at chr{chrom} "
                                f"marker_index {marker_index}"
                            )
                        key = (truth_row.position, truth_row.rsid)
                        if shared_key is None:
                            shared_key = key
                        elif key != shared_key:
                            raise ValueError(
                                f"marker truths disagree at chr{chrom} marker_index {marker_index}"
                            )
                        rows[iid] = truth_row, fixture_row
                    assert shared_key is not None
                    position, rsid = shared_key
                    if position <= previous_position[chrom]:
                        raise ValueError(
                            f"marker truth is not in strict model-marker order on chr{chrom}"
                        )
                    previous_position[chrom] = position
                    record = vcf.find(position=position, rsid=rsid)
                    donor_alleles = vcf.donor_alleles(record)
                    for iid, (truth_row, fixture_row) in rows.items():
                        expected_alleles = (
                            donor_alleles[truth_row.hap0_donor][truth_row.hap0_source_hap],
                            donor_alleles[truth_row.hap1_donor][truth_row.hap1_source_hap],
                        )
                        observed_alleles = (fixture_row.allele1, fixture_row.allele2)
                        if observed_alleles != expected_alleles:
                            raise ValueError(
                                f"fixture {iid}: allele mismatch at chr{chrom}:{position}:{rsid}; "
                                f"expected {expected_alleles[0]}/{expected_alleles[1]}, "
                                f"observed {observed_alleles[0]}/{observed_alleles[1]}"
                            )
                        row_counts[iid] += 1
                source_reports[chrom] = {
                    "vcf": vcf_snapshots[chrom].public(),
                    "index": index_snapshots[chrom].public(),
                    "sample_count": len(vcf.sample_ids),
                    "sample_ids_sha256": _sha256_json(sorted(vcf.sample_ids)),
                    "records_scanned": vcf.records_scanned,
                    "model_markers_verified": manifest.marker_counts[chrom],
                }

        for iid in sorted(selected_simulations):
            if next(truth_iterators[iid], None) is not None:
                raise ValueError(f"marker truth {iid}: excess row after model marker matrix")
            if next(fixture_iterators[iid], None) is not None:
                raise ValueError(f"fixture {iid}: excess row after model marker matrix")
            if observed_donors[iid] != set(selected_simulations[iid].donor_iids):
                missing = sorted(selected_simulations[iid].donor_iids - observed_donors[iid])
                raise ValueError(
                    f"marker truth {iid}: declared donor(s) never observed: {missing}"
                )
            if row_counts[iid] != expected_rows:
                raise ValueError(f"simulation {iid}: incomplete verified marker count")

    for iid in selected_simulations:
        _assert_snapshot_unchanged(fixture_snapshots[iid], label=f"fixture {iid}")
        _assert_snapshot_unchanged(truth_snapshots[iid], label=f"marker truth {iid}")
        _assert_snapshot_unchanged(tract_snapshots[iid], label=f"tract truth {iid}")
    for chrom in AUTOSOMES:
        _assert_snapshot_unchanged(vcf_snapshots[chrom], label=f"source VCF chr{chrom}")
        _assert_snapshot_unchanged(
            index_snapshots[chrom],
            label=f"source VCF index chr{chrom}",
        )
    _assert_snapshot_unchanged(manifest.snapshot, label="simulation manifest")
    _assert_snapshot_unchanged(script_snapshot, label="verifier script")
    _assert_snapshot_unchanged(
        generator_script_snapshot,
        label="simulation generator script",
    )
    _assert_snapshot_unchanged(
        generator_environment_snapshot,
        label="simulation generator environment lock",
    )
    _assert_snapshot_unchanged(environment_snapshot, label="verifier environment lock")

    simulations_report = {
        iid: {
            "marker_truth_sha256": spec.marker_truth_sha256,
            "tract_truth_sha256": spec.tract_truth_sha256,
            "fixture_sha256": spec.fixture_sha256,
            "marker_rows_verified": row_counts[iid],
            "haplotype_alleles_verified": row_counts[iid] * 2,
            "missing_rows": 0,
            "mismatches": 0,
        }
        for iid, spec in sorted(selected_simulations.items())
    }
    report: dict[str, object] = {
        "schema_version": 2,
        "dataset_id": manifest.dataset_id,
        "dataset_split": args.dataset_split,
        "split_commitment_sha256": _split_commitment(manifest, args.dataset_split),
        "verification_status": "passed",
        "simulation_manifest": manifest.snapshot.public(),
        "verifier": {
            "name": "independent-donor-vcf-replay",
            "code_revision": actual_revision,
            "script_sha256": script_snapshot.sha256,
            "script_snapshot": script_snapshot.public(),
            "environment_lock_sha256": environment_snapshot.sha256,
            "environment_lock_snapshot": environment_snapshot.public(),
            "distinct_from_generator_script_sha256": True,
        },
        "generator_script_snapshot": generator_script_snapshot.public(),
        "generator_environment_lock_snapshot": generator_environment_snapshot.public(),
        "generator_code_revision": manifest.generator_code_revision,
        "donor_haplotype_source_sha256": _sha256_json(manifest.donor_haplotype_source),
        "simulation_manifest_models_sha256": _sha256_json(manifest.models),
        "source_vcf_haplotypes_verified": True,
        "fixture_genotypes_verified": True,
        "source_vcf_marker_rsids_verified": True,
        "marker_truth_tracts_reconciled": True,
        "source_snapshots": source_reports,
        "simulations": simulations_report,
        "totals": {
            "autosomes_verified": len(AUTOSOMES),
            "simulations_verified": len(selected_simulations),
            "marker_rows_verified": sum(row_counts.values()),
            "haplotype_alleles_verified": sum(row_counts.values()) * 2,
            "missing_rows": 0,
            "mismatches": 0,
        },
    }
    if confirmation_policy_entry is not None:
        report["confirmation_policy"] = dict(confirmation_policy_entry)
    input_paths = {
        manifest.path,
        script_snapshot.path,
        generator_script_snapshot.path,
        generator_environment_snapshot.path,
        environment_snapshot.path,
        *(snapshot.path for snapshot in fixture_snapshots.values()),
        *(snapshot.path for snapshot in truth_snapshots.values()),
        *(snapshot.path for snapshot in tract_snapshots.values()),
        *(snapshot.path for snapshot in vcf_snapshots.values()),
        *(snapshot.path for snapshot in index_snapshots.values()),
    }
    if confirmation_policy_path is not None:
        input_paths.add(confirmation_policy_path)
    _atomic_write_json(args.output, report, input_paths=input_paths)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        verify(args)
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
