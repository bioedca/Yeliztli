#!/usr/bin/env python3
"""Run reproducible, leakage-resistant LAI coverage calibration.

This harness deliberately does *not* choose or enforce a production threshold.
It produces one JSON Lines record for every selected
sample x mask x chromosome-drop scenario x fraction x seed job.  A later
analysis can use those records to choose a threshold against an explicitly
documented accuracy target.

Each explicitly named fixture is a simulated genome with exact marker-level
donor/haplotype truth.  The planner independently projects those donors' pinned
model classes onto the production Gnomix windows and rejects a mismatching
window-truth cache.  Its public donor haplotypes must be absent from Gnomix
training and from every calibration phasing panel; startup validates those
isolation claims against the real training map, donor list, declared
donor-relative isolation set, reference manifest, and VCF headers before
enumerating jobs.  The calibration panel may reuse the production models, but
validation donors and their declared close relatives must be absent from both
model training and the phasing reference.  A separate, generator-independent
verification stamp must authenticate distinct generator/verifier source,
reconcile founder tracts, replay source-VCF donor haplotypes, and confirm marker
rsIDs and paired fixture alleles before the planner accepts the truth.

The planner requires 23andMe and AncestryDNA masks as explicit, privacy-safe derived
three-column site lists (``rsid<TAB>chrom<TAB>pos``); they are not represented
as vendor-published manifests.  Membership is joined by rsID because the build
site lists are GRCh37 while the simulated fixtures are GRCh38.  The harness never
infers a vendor from a fixture name.  It additionally constructs a synthetic
merged mask whose source labels are ``S1`` (23andMe-only), ``S2``
(AncestryDNA-only), and ``both`` (the GRCh37 coordinate occurs in both masks).
Conflicting aliases use a pinned production-VEP rsID membership snapshot and
the same fallback as production merge.

Downsampling is deterministic and nested.  For a given seed, markers receive a
stable within-chromosome order and are interleaved so every prefix remains as
close as possible to the original chromosome distribution.  Therefore every
smaller requested fraction is an exact subset of every larger fraction.
Structured chromosome loss is applied only after selecting that shared prefix,
so a drop scenario differs from its baseline solely by the removed chromosomes.

Typical SLURM use first validates and freezes the deterministic matrix, then
runs one array element from that authenticated plan per invocation::

    python scripts/lai_bundle_v2/06g_calibrate_coverage.py ... \
      --list-jobs --job-plan calibration-plan.json
    python scripts/lai_bundle_v2/06g_calibrate_coverage.py ... \
      --job-plan calibration-plan.json --job-index "$SLURM_ARRAY_TASK_ID" \
      --expected-configuration-sha256 "$CONFIG_SHA256" \
      --output "records/$SLURM_ARRAY_TASK_ID.jsonl"

The planning pass parses and hashes every declared truth and mask, then writes
Merkle-authenticated per-job shards.  Array tasks validate one proof plus cheap
live-file fingerprints and hydrate only the selected fixture and required masks.

Each output is written to a sibling temporary file, fsynced, and atomically
renamed.  Known sparse-data failures are retained as negative boundary
observations (``status=coverage_failure``).  Operational failures require a
rerun (``status=operational_error``).  Completed runs with malformed or
incomplete coverage telemetry have ``status=invalid``.  Only ``status=ok`` rows
are eligible for later threshold selection.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import fcntl
import gzip
import hashlib
import heapq
import importlib.metadata
import io
import json
import math
import os
import platform
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
import sqlalchemy as sa

# Allow direct execution from the repository root without installing the package.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.analysis.lai import run_lai_analysis  # noqa: E402
from backend.analysis.lai_liftover import resolve_lai_liftover_path  # noqa: E402
from backend.db.sample_schema import create_sample_tables  # noqa: E402
from backend.db.tables import raw_variants, sample_metadata_table  # noqa: E402
from scripts.lai_bundle_v2 import lai_coverage_plan  # noqa: E402
from scripts.lai_bundle_v2.lai_coverage_metrics import (  # noqa: E402
    coverage_metrics_calibration_exclusion as _shared_coverage_metrics_calibration_exclusion,
)
from scripts.lai_bundle_v2.lai_coverage_policy import (  # noqa: E402
    ConfirmationPolicy,
    confirmation_policy_provenance,
    read_confirmation_policy,
)

SCHEMA_VERSION = 1
AUTOSOMES = tuple(str(chrom) for chrom in range(1, 23))
AUTOSOME_SET = frozenset(AUTOSOMES)
SUPERPOPULATIONS = frozenset({"AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE"})
POPULATION_ORDER = ("AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE")
MIN_FOUNDERS_PER_CLASS_PER_SPLIT = 2
MIN_SIMULATIONS_PER_CLASS_PER_SPLIT = 2
MISSING_ANCESTRY_LABEL = "__missing__"
RAW_VARIANT_INSERT_BATCH_SIZE = 5_000
MAX_BREAKPOINTS_PER_HAPLOTYPE = 1_000
GENERATOR_NAME = "yeliztli-founder-mosaic-v1"
JOB_PLAN_INODE_RESERVE = 128
MAX_SIMULATIONS = 64
MAX_SIMULATION_MANIFEST_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Marker:
    """One autosomal, diploid simulated-validation genotype."""

    rsid: str
    chrom: str
    pos: int
    genotype: str
    source: str = ""
    db_chrom: str | None = None
    db_pos: int | None = None

    @property
    def site_key(self) -> tuple[str, str, int]:
        return self.rsid, self.chrom, self.pos

    def as_raw_variant(self) -> dict[str, str | int]:
        return {
            "rsid": self.rsid,
            "chrom": self.db_chrom or self.chrom,
            "pos": self.db_pos or self.pos,
            "genotype": self.genotype,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class TruthWindow:
    """Expected local ancestry for one exact production-model window."""

    chrom: str
    start: int
    end: int
    hap0: str
    hap1: str

    @property
    def key(self) -> tuple[str, int, int]:
        return self.chrom, self.start, self.end


@dataclass(frozen=True, slots=True)
class ValidationFixture:
    iid: str
    validation_stratum: str
    path: Path
    sha256: str
    markers: tuple[Marker, ...]
    parsing: Mapping[str, int]
    truth_path: Path
    truth_sha256: str
    truth_windows: tuple[TruthWindow, ...]
    marker_truth_path: Path | None = None
    marker_truth_sha256: str = ""
    marker_truth_rows: int = 0
    truth_donor_iids: frozenset[str] = frozenset()
    tract_truth_path: Path | None = None
    tract_truth_sha256: str = ""
    tract_count: int = 0
    tract_summary: Mapping[str, object] = dataclasses.field(default_factory=dict)
    model_marker_counts_by_autosome: Mapping[str, int] = dataclasses.field(default_factory=dict)
    truth_founders_by_class: Mapping[str, frozenset[str]] = dataclasses.field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CalibrationReferenceManifest:
    """Provenance for a donor-excluded calibration phasing reference."""

    path: Path
    sha256: str
    source_bundle_artifact_sha256: str
    excluded_iids: frozenset[str]
    phasing_panel: Mapping[str, Mapping[str, object]]
    inherited_files: Mapping[str, Mapping[str, object]]
    inherited_tree_sha256: str
    resolved_liftover_file: str


@dataclass(frozen=True, slots=True)
class SimulationManifest:
    """Pinned generator contract for calibration and final-confirmation truth."""

    path: Path
    sha256: str
    dataset_id: str
    source_bundle_artifact_sha256: str
    generator: Mapping[str, object]
    simulation_protocol: Mapping[str, object]
    relationships: Mapping[str, object]
    donor_metadata: Mapping[str, object]
    donor_haplotype_source: Mapping[str, object]
    models: Mapping[str, object]
    truth_projection: Mapping[str, object]
    splits: Mapping[str, tuple[str, ...]]
    simulations: Mapping[str, Mapping[str, object]]


@dataclass(frozen=True, slots=True)
class SimulationVerificationReport:
    """Pinned independent replay of donor haplotypes into fixture genotypes."""

    path: Path
    sha256: str
    verifier: Mapping[str, object]
    marker_rows_verified: int


@dataclass(frozen=True, slots=True)
class SelectionDesignArtifact:
    """Opaque, independently authenticated threshold-selection preregistration."""

    path: Path
    sha256: str
    fingerprint: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class TruthModelSpec:
    chrom: str
    marker_count: int
    window_size: int
    window_count: int
    positions: np.ndarray
    population_order: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TruthTract:
    chrom: str
    haplotype: int
    start_marker_index: int
    end_marker_index_exclusive: int
    donor_iid: str
    source_hap: int


@dataclass(frozen=True, slots=True)
class TractTruth:
    path: Path
    sha256: str
    tracts_by_haplotype: Mapping[tuple[str, int], tuple[TruthTract, ...]]
    tract_count: int
    summary: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ReferenceVerification:
    """One full-hash attestation plus cheap live-file fingerprints."""

    path: Path
    sha256: str
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ManifestSite:
    rsid: str
    chrom: str
    pos: int


@dataclass(frozen=True, slots=True)
class SiteManifest:
    name: str
    path: Path
    sha256: str
    rsids: frozenset[str]
    site_count: int
    autosomal_sites: tuple[ManifestSite, ...] = ()


@dataclass(frozen=True, slots=True)
class IdentifierManifest:
    name: str
    path: Path
    sha256: str
    identifiers: frozenset[str]
    identifier_count: int


@dataclass(frozen=True, slots=True)
class MaskScenario:
    name: str
    kind: str
    file_format: str
    markers: tuple[Marker, ...]
    manifest_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChromosomeDropScenario:
    name: str
    dropped_autosomes: frozenset[str]


@dataclass(frozen=True, slots=True)
class CalibrationJob:
    index: int
    fixture: ValidationFixture
    mask: MaskScenario
    drop_scenario: ChromosomeDropScenario
    fraction: Decimal
    seed: int


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest for ``path``."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_payload_and_sha256(
    path: Path,
    *,
    max_bytes: int | None = None,
) -> tuple[object, str]:
    """Read, hash, and decode JSON through exactly one open file handle."""
    with path.open("rb") as handle:
        encoded = handle.read() if max_bytes is None else handle.read(max_bytes + 1)
    if max_bytes is not None and len(encoded) > max_bytes:
        raise ValueError(f"{path}: JSON input exceeds the {max_bytes}-byte safety limit")
    payload = json.loads(encoded.decode("utf-8"))
    return payload, hashlib.sha256(encoded).hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    """Serialize JSON deterministically for configuration hashing."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def canonical_autosome(raw: str) -> str | None:
    """Normalize ``1``/``chr1`` autosome spellings; return ``None`` otherwise."""
    chrom = raw.strip()
    if chrom.lower().startswith("chr"):
        chrom = chrom[3:]
    try:
        normalized = str(int(chrom))
    except ValueError:
        return None
    return normalized if normalized in AUTOSOME_SET else None


def read_labels(path: Path) -> dict[str, str]:
    """Read an IID/validation-stratum TSV and reject ambiguous labels."""
    labels: dict[str, str] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 2:
                raise ValueError(
                    f"{path}:{line_number}: expected exactly IID and validation stratum"
                )
            iid, stratum = parts[0].strip(), parts[1].strip()
            if iid.lower() == "iid" and stratum.lower() in {
                "region",
                "genetic_region",
                "superpopulation",
                "stratum",
                "validation_stratum",
            }:
                continue
            if not iid:
                raise ValueError(f"{path}:{line_number}: empty IID")
            if not stratum:
                raise ValueError(f"{path}:{line_number}: empty validation stratum")
            previous = labels.get(iid)
            if previous is not None:
                detail = (
                    f"conflicting labels {previous!r} and {stratum!r}"
                    if previous != stratum
                    else "duplicate IID"
                )
                raise ValueError(f"{path}:{line_number}: {iid}: {detail}")
            labels[iid] = stratum
    if not labels:
        raise ValueError(f"{path}: no validation labels found")
    return labels


def read_local_truth(path: Path) -> tuple[TruthWindow, ...]:
    """Read exact model-window ancestry truth for both simulated haplotypes."""
    windows: list[TruthWindow] = []
    seen: set[tuple[str, int, int]] = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if parts[0].strip().lower() in {"chrom", "chromosome"}:
                normalized_header = tuple(part.strip().lower() for part in parts)
                if normalized_header != ("chrom", "start", "end", "hap0", "hap1"):
                    raise ValueError(
                        f"{path}:{line_number}: expected header chrom, start, end, hap0, hap1"
                    )
                continue
            if len(parts) != 5:
                raise ValueError(f"{path}:{line_number}: expected chrom, start, end, hap0, hap1")
            raw_chrom, raw_start, raw_end, raw_hap0, raw_hap1 = (part.strip() for part in parts)
            chrom = canonical_autosome(raw_chrom)
            if chrom is None:
                raise ValueError(f"{path}:{line_number}: invalid autosome {raw_chrom!r}")
            try:
                start, end = int(raw_start), int(raw_end)
            except ValueError:
                raise ValueError(
                    f"{path}:{line_number}: truth-window coordinates must be integers"
                ) from None
            if start <= 0 or end < start:
                raise ValueError(f"{path}:{line_number}: invalid truth-window interval")
            hap0, hap1 = raw_hap0.upper(), raw_hap1.upper()
            if hap0 not in SUPERPOPULATIONS or hap1 not in SUPERPOPULATIONS:
                raise ValueError(
                    f"{path}:{line_number}: truth labels must be canonical "
                    "AFR/AMR/CSA/EAS/EUR/MID/OCE values"
                )
            window = TruthWindow(chrom, start, end, hap0, hap1)
            if window.key in seen:
                raise ValueError(f"{path}:{line_number}: duplicate truth window {window.key}")
            seen.add(window.key)
            windows.append(window)
    if not windows:
        raise ValueError(f"{path}: no local-ancestry truth windows found")
    windows.sort(key=lambda window: (int(window.chrom), window.start, window.end))
    represented = {window.chrom for window in windows}
    missing = sorted(AUTOSOME_SET - represented, key=int)
    if missing:
        raise ValueError(f"{path}: local truth is missing autosomes {missing}")
    return tuple(windows)


def read_simulation_manifest(
    path: Path,
    *,
    dataset_id: str,
    source_bundle_artifact_sha256: str,
    donor_iids: frozenset[str],
    isolation_iids: frozenset[str],
) -> SimulationManifest:
    """Read the pinned generator/model/source contract for simulated truth."""
    payload, payload_sha256 = _read_json_payload_and_sha256(
        path,
        max_bytes=MAX_SIMULATION_MANIFEST_BYTES,
    )
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 2:
        raise ValueError(f"{path}: expected simulation-manifest schema version 2")
    if payload.get("dataset_id") != dataset_id:
        raise ValueError(f"{path}: dataset_id does not match --dataset-id")
    if payload.get("source_bundle_artifact_sha256") != source_bundle_artifact_sha256:
        raise ValueError(f"{path}: source bundle artifact SHA-256 does not match")
    if payload.get("model_frozen_before_generation") is not True:
        raise ValueError(
            f"{path}: model_frozen_before_generation must be true to prevent exposure"
        )

    generator = payload.get("generator")
    if not isinstance(generator, Mapping):
        raise ValueError(f"{path}: generator must be an object")
    required_generator_strings = {
        "name",
        "code_revision",
        "rng_library",
        "rng_version",
        "rng_algorithm",
    }
    for field in required_generator_strings:
        if not isinstance(generator.get(field), str) or not str(generator[field]).strip():
            raise ValueError(f"{path}: generator.{field} must be non-empty")
    if generator.get("name") != GENERATOR_NAME:
        raise ValueError(f"{path}: generator.name must be {GENERATOR_NAME!r}")
    for field in ("script_sha256", "environment_lock_sha256"):
        if not is_sha256(generator.get(field)):
            raise ValueError(f"{path}: generator.{field} is invalid")
    seed = generator.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError(f"{path}: generator.seed must be an integer")

    simulation_protocol = payload.get("simulation_protocol")
    if not isinstance(simulation_protocol, Mapping):
        raise ValueError(f"{path}: simulation_protocol must be an object")
    expected_protocol_strings = {
        "schema": "founder_mosaic_v1",
        "genome_build": "GRCh38",
        "breakpoint_process": "genetic_map_poisson_v1",
        "admixture_model": "single_pulse_v1",
    }
    for field, expected in expected_protocol_strings.items():
        if simulation_protocol.get(field) != expected:
            raise ValueError(f"{path}: simulation_protocol.{field} must be {expected!r}")
    protocol_map_hashes = simulation_protocol.get("recombination_map_sha256_by_autosome")
    if (
        not isinstance(protocol_map_hashes, Mapping)
        or set(protocol_map_hashes) != AUTOSOME_SET
        or not all(is_sha256(protocol_map_hashes[chrom]) for chrom in AUTOSOMES)
    ):
        raise ValueError(f"{path}: simulation protocol recombination-map hashes must cover 1..22")
    max_breakpoints = simulation_protocol.get("max_breakpoints_per_haplotype_by_autosome")
    if (
        not isinstance(max_breakpoints, Mapping)
        or set(max_breakpoints) != AUTOSOME_SET
        or not all(
            isinstance(max_breakpoints[chrom], int)
            and not isinstance(max_breakpoints[chrom], bool)
            and 0 <= max_breakpoints[chrom] <= MAX_BREAKPOINTS_PER_HAPLOTYPE
            for chrom in AUTOSOMES
        )
    ):
        raise ValueError(
            f"{path}: simulation protocol breakpoint envelope must cover 1..22 "
            f"with integers from 0 through {MAX_BREAKPOINTS_PER_HAPLOTYPE}"
        )
    allowed_generations = simulation_protocol.get("allowed_generations")
    if (
        not isinstance(allowed_generations, list)
        or len(allowed_generations) < 2
        or not all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 1
            for value in allowed_generations
        )
        or len(allowed_generations) != len(set(allowed_generations))
        or allowed_generations != sorted(allowed_generations)
    ):
        raise ValueError(
            f"{path}: simulation protocol must predeclare at least two positive generations"
        )
    minimums = simulation_protocol.get("minimums")
    if not isinstance(minimums, Mapping) or set(minimums) != {
        "founders_per_class_per_split",
        "simulations_per_class_per_split",
        "truth_haplotype_windows_per_class_per_split",
    }:
        raise ValueError(f"{path}: simulation protocol minimums are incomplete")
    minimum_floor = {
        "founders_per_class_per_split": MIN_FOUNDERS_PER_CLASS_PER_SPLIT,
        "simulations_per_class_per_split": MIN_SIMULATIONS_PER_CLASS_PER_SPLIT,
        "truth_haplotype_windows_per_class_per_split": 1,
    }
    for field, floor in minimum_floor.items():
        value = minimums.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < floor:
            raise ValueError(f"{path}: simulation protocol {field} must be at least {floor}")
    normalized_protocol = {
        **expected_protocol_strings,
        "recombination_map_sha256_by_autosome": {
            chrom: protocol_map_hashes[chrom] for chrom in AUTOSOMES
        },
        "max_breakpoints_per_haplotype_by_autosome": {
            chrom: int(max_breakpoints[chrom]) for chrom in AUTOSOMES
        },
        "allowed_generations": list(allowed_generations),
        "minimums": dict(minimums),
    }
    relationships = payload.get("relationships")
    if (
        not isinstance(relationships, Mapping)
        or set(relationships) != {"filename", "sha256"}
        or not isinstance(relationships.get("filename"), str)
        or not str(relationships["filename"]).strip()
        or PurePosixPath(str(relationships["filename"])).name != str(relationships["filename"])
        or not is_sha256(relationships.get("sha256"))
    ):
        raise ValueError(f"{path}: relationships provenance is invalid")

    donor_metadata = payload.get("donor_metadata")
    if not isinstance(donor_metadata, Mapping):
        raise ValueError(f"{path}: donor_metadata must be an object")
    required_metadata_strings = {
        "source_url",
        "iid_field",
        "population_field",
        "model_class_field",
        "release_field",
        "hard_filtered_field",
        "release_related_field",
        "all_samples_related_field",
    }
    if any(
        not isinstance(donor_metadata.get(field), str) or not str(donor_metadata[field]).strip()
        for field in required_metadata_strings
    ) or not is_sha256(donor_metadata.get("sha256")):
        raise ValueError(f"{path}: donor_metadata provenance is incomplete")

    haplotype_source = payload.get("donor_haplotype_source")
    if (
        not isinstance(haplotype_source, Mapping)
        or haplotype_source.get("genome_build") != "GRCh38"
    ):
        raise ValueError(f"{path}: donor_haplotype_source must declare GRCh38")
    source_vcfs = haplotype_source.get("per_chromosome_vcf_sha256")
    source_indexes = haplotype_source.get("per_chromosome_vcf_index_sha256")
    if (
        not isinstance(source_vcfs, Mapping)
        or set(source_vcfs) != AUTOSOME_SET
        or not all(is_sha256(source_vcfs[chrom]) for chrom in AUTOSOMES)
        or not isinstance(source_indexes, Mapping)
        or set(source_indexes) != AUTOSOME_SET
        or not all(is_sha256(source_indexes[chrom]) for chrom in AUTOSOMES)
    ):
        raise ValueError(f"{path}: donor source VCF/index hashes must cover autosomes 1..22")

    models = payload.get("models")
    if not isinstance(models, Mapping):
        raise ValueError(f"{path}: models must be an object")
    raw_population_order = models.get("population_order")
    if (
        not isinstance(raw_population_order, list)
        or len(raw_population_order) != len(SUPERPOPULATIONS)
        or not all(isinstance(label, str) for label in raw_population_order)
        or set(raw_population_order) != SUPERPOPULATIONS
    ):
        raise ValueError(f"{path}: models.population_order must be a seven-class permutation")
    population_order = tuple(raw_population_order)
    if models.get("population_order_sha256") != sha256_json(list(population_order)):
        raise ValueError(f"{path}: models.population_order_sha256 is inconsistent")
    per_chromosome = models.get("per_chromosome")
    if not isinstance(per_chromosome, Mapping) or set(per_chromosome) != AUTOSOME_SET:
        raise ValueError(f"{path}: models.per_chromosome must cover autosomes 1..22")
    normalized_models: dict[str, Mapping[str, object]] = {}
    for chrom in AUTOSOMES:
        entry = per_chromosome[chrom]
        if not isinstance(entry, Mapping):
            raise ValueError(f"{path}: model chr{chrom} entry must be an object")
        for field in ("metadata_npz_sha256", "genetic_map_sha256"):
            if not is_sha256(entry.get(field)):
                raise ValueError(f"{path}: model chr{chrom} {field} is invalid")
        c_value, m_value, w_value = entry.get("C"), entry.get("M"), entry.get("W")
        if (
            not all(
                isinstance(value, int) and not isinstance(value, bool) and value > 0
                for value in (c_value, m_value, w_value)
            )
            or w_value != c_value // m_value
        ):
            raise ValueError(f"{path}: model chr{chrom} C/M/W values are inconsistent")
        normalized_models[chrom] = dict(entry)
        if protocol_map_hashes[chrom] != entry["genetic_map_sha256"]:
            raise ValueError(
                f"{path}: simulation protocol map hash disagrees with chr{chrom} model"
            )

    protected_hash = payload.get("protected_iids_sha256")
    if protected_hash != sha256_json(sorted(isolation_iids)):
        raise ValueError(f"{path}: protected_iids_sha256 does not match isolation set")

    raw_simulations = payload.get("simulations")
    if (
        not isinstance(raw_simulations, list)
        or not raw_simulations
        or len(raw_simulations) > MAX_SIMULATIONS
    ):
        raise ValueError(f"{path}: simulations must contain 1..{MAX_SIMULATIONS} entries")
    simulations: dict[str, Mapping[str, object]] = {}
    split_lists: dict[str, list[str]] = {
        "calibration": [],
        "final_confirmation": [],
    }
    split_donors: dict[str, set[str]] = {
        "calibration": set(),
        "final_confirmation": set(),
    }
    for index, raw_entry in enumerate(raw_simulations):
        if not isinstance(raw_entry, Mapping):
            raise ValueError(f"{path}: simulation {index} must be an object")
        iid, split = raw_entry.get("iid"), raw_entry.get("split")
        if not isinstance(iid, str) or not iid or iid in simulations:
            raise ValueError(f"{path}: simulation {index} has an invalid/duplicate IID")
        if split not in split_lists:
            raise ValueError(f"{path}: simulation {iid!r} has an invalid split")
        if raw_entry.get("simulation_kind") != "admixed_mosaic":
            raise ValueError(
                f"{path}: simulation {iid!r} must declare simulation_kind='admixed_mosaic'"
            )
        generation = raw_entry.get("generation")
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
            or generation not in allowed_generations
        ):
            raise ValueError(f"{path}: simulation {iid!r} generation is invalid")
        for field in (
            "marker_truth_sha256",
            "fixture_sha256",
            "window_truth_sha256",
            "tract_truth_sha256",
        ):
            if not is_sha256(raw_entry.get(field)):
                raise ValueError(f"{path}: simulation {iid!r} {field} is invalid")
        for field in (
            "marker_truth_file",
            "fixture_file",
            "window_truth_file",
            "tract_truth_file",
        ):
            raw_relative = raw_entry.get(field)
            if not isinstance(raw_relative, str) or not raw_relative.strip():
                raise ValueError(f"{path}: simulation {iid!r} {field} is invalid")
            relative = PurePosixPath(raw_relative)
            if (
                relative.is_absolute()
                or not relative.parts
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise ValueError(f"{path}: simulation {iid!r} {field} is unsafe")
        raw_donors = raw_entry.get("donor_iids")
        if (
            not isinstance(raw_donors, list)
            or not raw_donors
            or not all(isinstance(donor, str) and donor for donor in raw_donors)
            or len(raw_donors) != len(set(raw_donors))
        ):
            raise ValueError(
                f"{path}: simulation {iid!r} donor_iids must be a unique non-empty list"
            )
        undeclared_donors = sorted(set(raw_donors) - set(donor_iids))
        if undeclared_donors:
            raise ValueError(
                f"{path}: simulation {iid!r} uses donor(s) absent from "
                f"--validation-donors: {undeclared_donors}"
            )
        unprotected_donors = sorted(set(raw_donors) - set(isolation_iids))
        if unprotected_donors:
            raise ValueError(
                f"{path}: simulation {iid!r} uses unprotected donor(s): {unprotected_donors}"
            )
        stratum = raw_entry.get("validation_stratum")
        if not isinstance(stratum, str) or not stratum:
            raise ValueError(f"{path}: simulation {iid!r} lacks a validation stratum")
        raw_target = raw_entry.get("target_marker_ancestry_fractions")
        if not isinstance(raw_target, Mapping) or set(raw_target) != SUPERPOPULATIONS:
            raise ValueError(
                f"{path}: simulation {iid!r} target ancestry fractions must cover all classes"
            )
        target_fractions: dict[str, str] = {}
        target_sum = Decimal(0)
        for label in sorted(SUPERPOPULATIONS):
            raw_fraction = raw_target[label]
            if not isinstance(raw_fraction, str):
                raise ValueError(
                    f"{path}: simulation {iid!r} target fraction {label} must be a string"
                )
            try:
                fraction = Decimal(raw_fraction)
            except InvalidOperation:
                raise ValueError(
                    f"{path}: simulation {iid!r} target fraction {label} is invalid"
                ) from None
            canonical = str(fraction.normalize())
            if not fraction.is_finite() or fraction < 0 or raw_fraction != canonical:
                raise ValueError(f"{path}: simulation {iid!r} target fraction {label} is invalid")
            target_fractions[label] = canonical
            target_sum += fraction
        if target_sum != Decimal(1):
            raise ValueError(f"{path}: simulation {iid!r} target fractions must sum to one")
        raw_tolerance = raw_entry.get("fraction_absolute_tolerance")
        if not isinstance(raw_tolerance, str):
            raise ValueError(f"{path}: simulation {iid!r} fraction tolerance is invalid")
        try:
            tolerance = Decimal(raw_tolerance)
        except InvalidOperation:
            raise ValueError(f"{path}: simulation {iid!r} fraction tolerance is invalid") from None
        if (
            not tolerance.is_finite()
            or not Decimal(0) < tolerance <= Decimal("0.1")
            or raw_tolerance != str(tolerance.normalize())
        ):
            raise ValueError(f"{path}: simulation {iid!r} fraction tolerance is invalid")
        normalized_entry = dict(raw_entry)
        normalized_entry["donor_iids"] = tuple(sorted(raw_donors))
        normalized_entry["target_marker_ancestry_fractions"] = target_fractions
        normalized_entry["fraction_absolute_tolerance"] = str(tolerance.normalize())
        simulations[iid] = normalized_entry
        split_lists[str(split)].append(iid)
        split_donors[str(split)].update(raw_donors)
    if not all(split_lists.values()):
        raise ValueError(f"{path}: both simulation splits must be non-empty")
    if split_donors["calibration"] & split_donors["final_confirmation"]:
        overlap = sorted(split_donors["calibration"] & split_donors["final_confirmation"])
        raise ValueError(f"{path}: calibration and final-confirmation donors overlap: {overlap}")
    declared_donors = split_donors["calibration"] | split_donors["final_confirmation"]
    if declared_donors != set(donor_iids):
        missing = sorted(set(donor_iids) - declared_donors)
        extra = sorted(declared_donors - set(donor_iids))
        raise ValueError(
            f"{path}: simulation donor union does not match --validation-donors; "
            f"unused={missing}, undeclared={extra}"
        )
    splits = {split: tuple(sorted(iids)) for split, iids in split_lists.items()}

    truth_projection = payload.get("truth_projection")
    expected_projection = {
        "schema": "gnomix-window-mode-v1",
        "regular_window": "[w*M,(w+1)*M)",
        "final_window": "[(W-1)*M,C)",
        "tie_rule": "lowest production-model ancestry code",
    }
    if truth_projection != expected_projection:
        raise ValueError(f"{path}: truth_projection does not match Gnomix semantics")
    return SimulationManifest(
        path=path,
        sha256=payload_sha256,
        dataset_id=dataset_id,
        source_bundle_artifact_sha256=source_bundle_artifact_sha256,
        generator=dict(generator),
        simulation_protocol=normalized_protocol,
        relationships=dict(relationships),
        donor_metadata=dict(donor_metadata),
        donor_haplotype_source=dict(haplotype_source),
        models={
            "population_order": list(population_order),
            "population_order_sha256": models["population_order_sha256"],
            "per_chromosome": normalized_models,
        },
        truth_projection=dict(truth_projection),
        splits=splits,
        simulations=simulations,
    )


def compute_simulation_split_commitment(
    simulation_manifest: SimulationManifest,
    dataset_split: str,
) -> str:
    """Commit to every identity and truth hash in one simulation split."""
    split_iids = simulation_manifest.splits.get(dataset_split)
    if not isinstance(split_iids, tuple) or not split_iids:
        raise ValueError(f"simulation manifest has no {dataset_split!r} split")
    if tuple(sorted(split_iids)) != split_iids or len(split_iids) != len(set(split_iids)):
        raise ValueError(f"simulation split {dataset_split!r} identities are not canonical")

    committed_simulations: list[dict[str, object]] = []
    for iid in split_iids:
        raw = simulation_manifest.simulations.get(iid)
        if not isinstance(raw, Mapping) or raw.get("split") != dataset_split:
            raise ValueError(f"simulation {iid!r} is missing or misclassified")
        hashes: dict[str, str] = {}
        for field in (
            "fixture_sha256",
            "marker_truth_sha256",
            "tract_truth_sha256",
            "window_truth_sha256",
        ):
            value = raw.get(field)
            if not is_sha256(value):
                raise ValueError(f"simulation {iid!r} has invalid {field}")
            hashes[field] = str(value)
        donors = raw.get("donor_iids")
        if (
            not isinstance(donors, tuple)
            or not donors
            or not all(isinstance(donor, str) and donor for donor in donors)
            or tuple(sorted(donors)) != donors
            or len(donors) != len(set(donors))
        ):
            raise ValueError(f"simulation {iid!r} has invalid donors")
        generation = raw.get("generation")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            raise ValueError(f"simulation {iid!r} has invalid generation")
        validation_stratum = raw.get("validation_stratum")
        if not isinstance(validation_stratum, str) or not validation_stratum:
            raise ValueError(f"simulation {iid!r} has invalid validation stratum")
        committed_simulations.append(
            {
                "iid": iid,
                **hashes,
                "donor_iids": list(donors),
                "generation": generation,
                "validation_stratum": validation_stratum,
            }
        )
    return sha256_json(
        {
            "schema_version": 1,
            "dataset_id": simulation_manifest.dataset_id,
            "split": dataset_split,
            "simulations": committed_simulations,
        }
    )


def compute_final_confirmation_split_commitment(
    simulation_manifest: SimulationManifest,
) -> str:
    """Commit to the complete, still-sealed final-confirmation truth split."""
    return compute_simulation_split_commitment(simulation_manifest, "final_confirmation")


def matrix_from_confirmation_policy(
    policy: ConfirmationPolicy,
    masks_by_iid: Mapping[str, Sequence[MaskScenario]],
) -> tuple[
    dict[str, tuple[MaskScenario, ...]],
    tuple[ChromosomeDropScenario, ...],
    tuple[Decimal, ...],
    tuple[int, ...],
]:
    """Use the frozen policy as the sole final-confirmation matrix definition."""
    selected_masks: dict[str, tuple[MaskScenario, ...]] = {}
    for iid, masks in sorted(masks_by_iid.items()):
        masks_by_name = {mask.name: mask for mask in masks}
        missing = [
            name for name in policy.confirmation_matrix.input_masks if name not in masks_by_name
        ]
        if missing:
            raise ValueError(f"fixture {iid!r} lacks confirmation-policy mask(s): {missing}")
        selected_masks[iid] = tuple(
            masks_by_name[name] for name in policy.confirmation_matrix.input_masks
        )
    drop_scenarios = tuple(
        ChromosomeDropScenario(
            scenario.name,
            frozenset(scenario.dropped_autosomes),
        )
        for scenario in policy.confirmation_matrix.drop_scenarios
    )
    return (
        selected_masks,
        drop_scenarios,
        policy.confirmation_matrix.fractions,
        policy.confirmation_matrix.seeds,
    )


def _parse_metadata_boolean(path: Path, iid: str, field: str, raw: object) -> bool:
    value = str(raw).strip().casefold()
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise ValueError(f"{path}: donor {iid!r} has invalid boolean {field}={raw!r}")


def read_simulation_verification_report(
    path: Path,
    simulation_manifest: SimulationManifest,
    *,
    dataset_split: str,
    expected_code_revision: str | None = None,
    expected_confirmation_policy: ConfirmationPolicy | None = None,
) -> SimulationVerificationReport:
    """Authenticate the repository verifier's executed allele-replay stamp."""
    payload, payload_sha256 = _read_json_payload_and_sha256(path)
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 2:
        raise ValueError(f"{path}: expected simulation-verification schema version 2")
    if (
        payload.get("dataset_id") != simulation_manifest.dataset_id
        or payload.get("dataset_split") != dataset_split
        or payload.get("verification_status") != "passed"
    ):
        raise ValueError(f"{path}: dataset/split does not match simulation manifest")
    if payload.get("split_commitment_sha256") != compute_simulation_split_commitment(
        simulation_manifest,
        dataset_split,
    ):
        raise ValueError(f"{path}: simulation split commitment does not match")
    stamped_policy = payload.get("confirmation_policy")
    if dataset_split == "final_confirmation":
        if (
            expected_confirmation_policy is None
            or stamped_policy != confirmation_policy_provenance(expected_confirmation_policy)
        ):
            raise ValueError(f"{path}: final verification policy provenance does not match")
    elif stamped_policy is not None:
        raise ValueError(f"{path}: calibration verification must not bind a confirmation policy")
    manifest_snapshot = payload.get("simulation_manifest")
    if (
        not isinstance(manifest_snapshot, Mapping)
        or manifest_snapshot.get("sha256") != simulation_manifest.sha256
    ):
        raise ValueError(f"{path}: simulation manifest snapshot does not match")
    verifier = payload.get("verifier")
    if not isinstance(verifier, Mapping):
        raise ValueError(f"{path}: verifier provenance is missing")
    for field in ("name", "code_revision"):
        if not isinstance(verifier.get(field), str) or not str(verifier[field]).strip():
            raise ValueError(f"{path}: verifier.{field} must be non-empty")
    for field in ("script_sha256", "environment_lock_sha256"):
        if not is_sha256(verifier.get(field)):
            raise ValueError(f"{path}: verifier.{field} is invalid")
    if verifier.get("distinct_from_generator_script_sha256") is not True:
        raise ValueError(f"{path}: verifier and generator scripts must have distinct hashes")
    if verifier.get("script_sha256") == simulation_manifest.generator.get("script_sha256"):
        raise ValueError(f"{path}: verifier script must differ from the generator script")
    generator_snapshot = payload.get("generator_script_snapshot")
    if not isinstance(generator_snapshot, Mapping) or generator_snapshot.get(
        "sha256"
    ) != simulation_manifest.generator.get("script_sha256"):
        raise ValueError(f"{path}: executed generator script does not match the manifest")
    generator_environment_snapshot = payload.get("generator_environment_lock_snapshot")
    if (
        not isinstance(generator_environment_snapshot, Mapping)
        or generator_environment_snapshot.get("sha256")
        != simulation_manifest.generator.get("environment_lock_sha256")
        or payload.get("generator_code_revision")
        != simulation_manifest.generator.get("code_revision")
    ):
        raise ValueError(f"{path}: executed generator environment/revision does not match")
    verifier_script_path = REPO_ROOT / "scripts/lai_bundle_v2/06g_verify_simulation.py"
    if verifier.get("script_sha256") != _stable_file_snapshot(verifier_script_path)["sha256"]:
        raise ValueError(f"{path}: live repository verifier script differs from stamp")
    if (
        expected_code_revision is not None
        and verifier.get("code_revision") != expected_code_revision
    ):
        raise ValueError(f"{path}: verifier code revision differs from calibration code")
    if (
        verifier.get("environment_lock_sha256")
        != _stable_file_snapshot(REPO_ROOT / "uv.lock")["sha256"]
    ):
        raise ValueError(f"{path}: verifier environment lock differs from uv.lock")
    if payload.get("donor_haplotype_source_sha256") != sha256_json(
        simulation_manifest.donor_haplotype_source
    ):
        raise ValueError(f"{path}: donor haplotype source provenance does not match")
    if payload.get("simulation_manifest_models_sha256") != sha256_json(simulation_manifest.models):
        raise ValueError(f"{path}: declared simulation-model provenance does not match")
    for field in (
        "source_vcf_haplotypes_verified",
        "fixture_genotypes_verified",
        "source_vcf_marker_rsids_verified",
        "marker_truth_tracts_reconciled",
    ):
        if payload.get(field) is not True:
            raise ValueError(f"{path}: {field} must be true")
    source_snapshots = payload.get("source_snapshots")
    source_vcfs = simulation_manifest.donor_haplotype_source["per_chromosome_vcf_sha256"]
    source_indexes = simulation_manifest.donor_haplotype_source["per_chromosome_vcf_index_sha256"]
    if not isinstance(source_snapshots, Mapping) or set(source_snapshots) != AUTOSOME_SET:
        raise ValueError(f"{path}: source snapshots must cover autosomes 1..22")
    for chrom in AUTOSOMES:
        source_entry = source_snapshots[chrom]
        if not isinstance(source_entry, Mapping):
            raise ValueError(f"{path}: source snapshot chr{chrom} is invalid")
        vcf_entry, index_entry = source_entry.get("vcf"), source_entry.get("index")
        if (
            not isinstance(vcf_entry, Mapping)
            or not isinstance(index_entry, Mapping)
            or vcf_entry.get("sha256") != source_vcfs[chrom]
            or index_entry.get("sha256") != source_indexes[chrom]
            or source_entry.get("model_markers_verified")
            != simulation_manifest.models["per_chromosome"][chrom]["C"]
        ):
            raise ValueError(f"{path}: source snapshot chr{chrom} does not match manifest")
    expected_rows = sum(
        int(entry["C"]) for entry in simulation_manifest.models["per_chromosome"].values()
    )
    expected_simulation_iids = set(simulation_manifest.splits[dataset_split])
    raw_simulations = payload.get("simulations")
    if not isinstance(raw_simulations, Mapping) or set(raw_simulations) != (
        expected_simulation_iids
    ):
        raise ValueError(f"{path}: verification must cover the selected split exactly")
    total_rows = 0
    for iid in sorted(expected_simulation_iids):
        manifest_entry = simulation_manifest.simulations[iid]
        report_entry = raw_simulations[iid]
        if not isinstance(report_entry, Mapping):
            raise ValueError(f"{path}: simulation verification for {iid!r} is invalid")
        if (
            report_entry.get("marker_truth_sha256") != manifest_entry["marker_truth_sha256"]
            or report_entry.get("tract_truth_sha256") != manifest_entry["tract_truth_sha256"]
            or report_entry.get("fixture_sha256") != manifest_entry["fixture_sha256"]
            or report_entry.get("marker_rows_verified") != expected_rows
            or report_entry.get("haplotype_alleles_verified") != expected_rows * 2
            or report_entry.get("missing_rows") != 0
            or report_entry.get("mismatches") != 0
        ):
            raise ValueError(f"{path}: simulation verification for {iid!r} does not match")
        total_rows += expected_rows
    totals = payload.get("totals")
    if (
        not isinstance(totals, Mapping)
        or totals.get("autosomes_verified") != 22
        or totals.get("simulations_verified") != len(expected_simulation_iids)
        or totals.get("marker_rows_verified") != total_rows
        or totals.get("haplotype_alleles_verified") != total_rows * 2
        or totals.get("missing_rows") != 0
        or totals.get("mismatches") != 0
    ):
        raise ValueError(f"{path}: simulation verification totals are invalid")
    return SimulationVerificationReport(
        path=path,
        sha256=payload_sha256,
        verifier=dict(verifier),
        marker_rows_verified=total_rows,
    )


def read_donor_labels(
    path: Path,
    simulation_manifest: SimulationManifest,
    donor_iids: frozenset[str],
) -> dict[str, str]:
    """Resolve donor model classes from pinned metadata and enforce release QC."""
    expected_sha256 = simulation_manifest.donor_metadata["sha256"]
    fields = {
        key: str(simulation_manifest.donor_metadata[key])
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
    with path.open("rb") as binary_handle:
        digest = hashlib.sha256()
        while chunk := binary_handle.read(1 << 20):
            digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            raise ValueError(f"{path}: donor metadata SHA-256 does not match manifest")
        binary_handle.seek(0)
        handle = io.TextIOWrapper(binary_handle, encoding="utf-8", newline="")
        reader = csv.DictReader(handle, delimiter="\t")
        header = reader.fieldnames
        if header is None or len(header) != len(set(header)):
            raise ValueError(f"{path}: donor metadata header is missing or duplicated")
        missing_fields = sorted(set(fields.values()) - set(header))
        if missing_fields:
            raise ValueError(f"{path}: donor metadata omits fields {missing_fields}")
        for row in reader:
            iid = str(row[fields["iid_field"]]).strip()
            if iid not in donor_iids:
                continue
            if iid in labels:
                raise ValueError(f"{path}: duplicate donor IID {iid!r}")
            model_class = str(row[fields["model_class_field"]]).strip().upper()
            if model_class not in SUPERPOPULATIONS:
                raise ValueError(f"{path}: donor {iid!r} has invalid model class {model_class!r}")
            release = _parse_metadata_boolean(
                path,
                iid,
                fields["release_field"],
                row[fields["release_field"]],
            )
            excluded_flags = {
                key: _parse_metadata_boolean(path, iid, fields[key], row[fields[key]])
                for key in (
                    "hard_filtered_field",
                    "release_related_field",
                    "all_samples_related_field",
                )
            }
            if not release or any(excluded_flags.values()):
                raise ValueError(f"{path}: donor {iid!r} is not an unrelated release-QC sample")
            labels[iid] = model_class
    missing_donors = sorted(set(donor_iids) - set(labels))
    if missing_donors:
        raise ValueError(f"{path}: donor metadata omits donor(s) {missing_donors}")
    return labels


def validate_split_population_coverage(
    simulation_manifest: SimulationManifest,
    donor_labels: Mapping[str, str],
) -> None:
    """Require every model class in both founder-disjoint biological splits."""
    for split, simulation_iids in simulation_manifest.splits.items():
        split_donors = {
            donor
            for iid in simulation_iids
            for donor in simulation_manifest.simulations[iid]["donor_iids"]
        }
        represented = {donor_labels[donor] for donor in split_donors}
        missing = sorted(SUPERPOPULATIONS - represented)
        if missing:
            raise ValueError(
                f"simulation split {split!r} lacks required model class(es): " + ", ".join(missing)
            )


def validate_selected_split_evaluation_coverage(
    *,
    simulation_manifest: SimulationManifest,
    donor_labels: Mapping[str, str],
    dataset_split: str,
    fixtures: Sequence[ValidationFixture],
) -> dict[str, object]:
    """Require replicated, window-evaluable truth for every ancestry class."""
    expected_iids = set(simulation_manifest.splits[dataset_split])
    fixture_by_iid = {fixture.iid: fixture for fixture in fixtures}
    if set(fixture_by_iid) != expected_iids:
        raise ValueError(f"fixtures must match simulation split {dataset_split!r} exactly")

    split_donors = {
        donor
        for iid in expected_iids
        for donor in simulation_manifest.simulations[iid]["donor_iids"]
    }
    minimums = simulation_manifest.simulation_protocol.get("minimums")
    if not isinstance(minimums, Mapping):
        raise ValueError("simulation protocol minimums are unavailable")
    required_founders = int(minimums["founders_per_class_per_split"])
    required_simulations = int(minimums["simulations_per_class_per_split"])
    required_truth_calls = int(minimums["truth_haplotype_windows_per_class_per_split"])
    founders_by_class = Counter(donor_labels[donor] for donor in split_donors)
    truth_founders_by_class: dict[str, set[str]] = {label: set() for label in SUPERPOPULATIONS}
    simulations_by_class: dict[str, set[str]] = {label: set() for label in SUPERPOPULATIONS}
    truth_calls_by_class: Counter[str] = Counter()
    truth_calls_by_stratum: defaultdict[str, Counter[str]] = defaultdict(Counter)
    simulations_by_stratum_class: defaultdict[str, dict[str, set[str]]] = defaultdict(
        lambda: {label: set() for label in SUPERPOPULATIONS}
    )
    for fixture in fixtures:
        for label in SUPERPOPULATIONS:
            raw_founders = fixture.truth_founders_by_class.get(label, frozenset())
            if not isinstance(raw_founders, (set, frozenset)) or not all(
                isinstance(founder, str) and donor_labels.get(founder) == label
                for founder in raw_founders
            ):
                raise ValueError(
                    f"fixture {fixture.iid!r} has invalid modal truth founders for {label}"
                )
            truth_founders_by_class[label].update(raw_founders)
        observed_classes: set[str] = set()
        for window in fixture.truth_windows:
            truth_calls_by_class.update((window.hap0, window.hap1))
            truth_calls_by_stratum[fixture.validation_stratum].update((window.hap0, window.hap1))
            observed_classes.update((window.hap0, window.hap1))
        for label in observed_classes:
            simulations_by_class[label].add(fixture.iid)
            simulations_by_stratum_class[fixture.validation_stratum][label].add(fixture.iid)

    for label in sorted(SUPERPOPULATIONS):
        if founders_by_class[label] < required_founders:
            raise ValueError(
                f"simulation split {dataset_split!r} has only "
                f"{founders_by_class[label]} unique {label} founder(s); at least "
                f"{required_founders} are required"
            )
        if len(truth_founders_by_class[label]) < required_founders:
            raise ValueError(
                f"simulation split {dataset_split!r} has only "
                f"{len(truth_founders_by_class[label])} distinct {label} founder(s) "
                f"supplying modal window truth; at least {required_founders} are required"
            )
        if len(simulations_by_class[label]) < required_simulations:
            raise ValueError(
                f"simulation split {dataset_split!r} has only "
                f"{len(simulations_by_class[label])} simulation(s) with evaluable "
                f"{label} window truth; at least "
                f"{required_simulations} are required"
            )
        if truth_calls_by_class[label] < required_truth_calls:
            raise ValueError(
                f"simulation split {dataset_split!r} has only "
                f"{truth_calls_by_class[label]} evaluable {label} truth haplotype "
                f"windows; at least {required_truth_calls} are required"
            )

    allowed_generations = set(
        simulation_manifest.simulation_protocol.get("allowed_generations", [])
    )
    observed_generations = {
        simulation_manifest.simulations[iid]["generation"] for iid in expected_iids
    }
    if observed_generations != allowed_generations:
        raise ValueError(
            f"simulation split {dataset_split!r} generations {sorted(observed_generations)} "
            f"do not match the predeclared set {sorted(allowed_generations)}"
        )

    for stratum, class_counts in sorted(truth_calls_by_stratum.items()):
        missing = sorted(label for label in SUPERPOPULATIONS if class_counts[label] <= 0)
        if missing:
            raise ValueError(
                f"validation stratum {stratum!r} in split {dataset_split!r} lacks "
                "evaluable truth for class(es): " + ", ".join(missing)
            )

    return {
        "founders_by_class": {
            label: founders_by_class[label] for label in sorted(SUPERPOPULATIONS)
        },
        "modal_truth_founders_by_class": {
            label: len(truth_founders_by_class[label]) for label in sorted(SUPERPOPULATIONS)
        },
        "simulations_by_class": {
            label: len(simulations_by_class[label]) for label in sorted(SUPERPOPULATIONS)
        },
        "truth_haplotype_windows_by_class": {
            label: truth_calls_by_class[label] for label in sorted(SUPERPOPULATIONS)
        },
        "generations": sorted(observed_generations),
        "by_validation_stratum": {
            stratum: {
                label: {
                    "simulations": len(simulations_by_stratum_class[stratum][label]),
                    "truth_haplotype_windows": class_counts[label],
                }
                for label in sorted(SUPERPOPULATIONS)
            }
            for stratum, class_counts in sorted(truth_calls_by_stratum.items())
        },
    }


def load_truth_model_specs(
    bundle_dir: Path,
    simulation_manifest: SimulationManifest,
    reference_manifest: CalibrationReferenceManifest,
) -> dict[str, TruthModelSpec]:
    """Load exact production marker/window metadata pinned by both manifests."""
    per_chromosome = simulation_manifest.models["per_chromosome"]
    raw_population_order = simulation_manifest.models["population_order"]
    assert isinstance(per_chromosome, Mapping)
    assert isinstance(raw_population_order, list)
    manifest_population_order = tuple(str(value) for value in raw_population_order)
    specs: dict[str, TruthModelSpec] = {}
    for chrom in AUTOSOMES:
        entry = per_chromosome[chrom]
        assert isinstance(entry, Mapping)
        metadata_relative = f"gnomix_models/chr{chrom}/metadata.npz"
        map_relative = f"genetic_maps/plink.chrchr{chrom}.GRCh38.map"
        if (
            reference_manifest.inherited_files[metadata_relative]["sha256"]
            != entry["metadata_npz_sha256"]
            or reference_manifest.inherited_files[map_relative]["sha256"]
            != entry["genetic_map_sha256"]
        ):
            raise ValueError(
                f"simulation model provenance for chr{chrom} does not match "
                "the calibration reference"
            )
        metadata_path = bundle_dir / metadata_relative
        (
            (marker_count, window_size, window_count, population_order, positions),
            metadata_snapshot,
        ) = stable_read(metadata_path, _read_truth_model_metadata_pinned)
        if metadata_snapshot["sha256"] != entry["metadata_npz_sha256"]:
            raise ValueError(f"{metadata_path}: metadata SHA-256 disagrees with manifests")
        if (
            marker_count != entry["C"]
            or window_size != entry["M"]
            or window_count != entry["W"]
            or window_count != marker_count // window_size
        ):
            raise ValueError(f"{metadata_path}: C/M/W disagree with simulation manifest")
        if population_order != manifest_population_order:
            raise ValueError(
                f"{metadata_path}: population order disagrees with simulation manifest"
            )
        if positions.ndim != 1 or len(positions) != marker_count:
            raise ValueError(f"{metadata_path}: snp_pos length does not equal C")
        if marker_count > 1 and not bool(np.all(positions[1:] > positions[:-1])):
            raise ValueError(f"{metadata_path}: snp_pos must be strictly increasing")
        positions.setflags(write=False)
        specs[chrom] = TruthModelSpec(
            chrom=chrom,
            marker_count=marker_count,
            window_size=window_size,
            window_count=window_count,
            positions=positions,
            population_order=population_order,
        )
    return specs


def _read_truth_model_metadata_pinned(
    path: Path,
) -> tuple[int, int, int, tuple[str, ...], np.ndarray]:
    """Parse Gnomix metadata through the descriptor pinned by ``stable_read``."""
    with np.load(path, allow_pickle=False) as metadata:
        required = {"C", "M", "W", "snp_pos", "population_order"}
        if not required <= set(metadata.files):
            raise ValueError(f"{path}: incomplete Gnomix metadata")
        return (
            int(metadata["C"].item()),
            int(metadata["M"].item()),
            int(metadata["W"].item()),
            tuple(str(value) for value in metadata["population_order"]),
            np.asarray(metadata["snp_pos"], dtype=np.int64).copy(),
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


def read_tract_truth(
    path: Path,
    *,
    iid: str,
    model_specs: Mapping[str, TruthModelSpec],
    donor_iids: frozenset[str],
    donor_labels: Mapping[str, str],
    max_breakpoints_by_autosome: Mapping[str, object],
    target_marker_ancestry_fractions: Mapping[str, object],
    fraction_absolute_tolerance: object,
    expected_sha256: str,
) -> TractTruth:
    """Validate gap-free founder tracts and their predeclared ancestry mixture."""
    parsed, snapshot = stable_read(
        path,
        lambda pinned_path: _read_tract_truth_pinned(
            pinned_path,
            iid=iid,
            model_specs=model_specs,
            donor_iids=donor_iids,
            donor_labels=donor_labels,
            max_breakpoints_by_autosome=max_breakpoints_by_autosome,
            target_marker_ancestry_fractions=target_marker_ancestry_fractions,
            fraction_absolute_tolerance=fraction_absolute_tolerance,
            expected_sha256=expected_sha256,
        ),
    )
    if snapshot["sha256"] != expected_sha256:
        raise ValueError(f"{path}: tract truth SHA-256 does not match manifest")
    return replace(
        parsed,
        path=path,
        sha256=str(snapshot["sha256"]),
    )


def _read_tract_truth_pinned(
    path: Path,
    *,
    iid: str,
    model_specs: Mapping[str, TruthModelSpec],
    donor_iids: frozenset[str],
    donor_labels: Mapping[str, str],
    max_breakpoints_by_autosome: Mapping[str, object],
    target_marker_ancestry_fractions: Mapping[str, object],
    fraction_absolute_tolerance: object,
    expected_sha256: str,
) -> TractTruth:
    """Parse tract truth through an already descriptor-pinned path."""
    grouped: defaultdict[tuple[str, int], list[TruthTract]] = defaultdict(list)
    with path.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.rstrip("\r\n")
            columns = tuple(line.split("\t"))
            if line_number == 1:
                if columns != TRACT_TRUTH_HEADER:
                    raise ValueError(f"{path}: expected exact tract-truth header")
                continue
            if not line or len(columns) != len(TRACT_TRUTH_HEADER):
                raise ValueError(f"{path}:{line_number}: expected exactly seven tract columns")
            (
                sim_iid,
                raw_chrom,
                raw_haplotype,
                raw_start,
                raw_end,
                donor_iid,
                raw_source_hap,
            ) = columns
            chrom = canonical_autosome(raw_chrom)
            try:
                haplotype = int(raw_haplotype)
                start = int(raw_start)
                end = int(raw_end)
                source_hap = int(raw_source_hap)
            except ValueError:
                raise ValueError(f"{path}:{line_number}: invalid numeric tract field") from None
            if sim_iid != iid:
                raise ValueError(f"{path}:{line_number}: expected IID {iid!r}")
            if chrom is None or haplotype not in {0, 1} or source_hap not in {0, 1}:
                raise ValueError(f"{path}:{line_number}: invalid tract chromosome/haplotype")
            if donor_iid not in donor_iids or donor_iid not in donor_labels:
                raise ValueError(f"{path}:{line_number}: undeclared donor {donor_iid!r}")
            if start < 0 or end <= start:
                raise ValueError(f"{path}:{line_number}: invalid half-open tract interval")
            grouped[(chrom, haplotype)].append(
                TruthTract(
                    chrom=chrom,
                    haplotype=haplotype,
                    start_marker_index=start,
                    end_marker_index_exclusive=end,
                    donor_iid=donor_iid,
                    source_hap=source_hap,
                )
            )

    ancestry_marker_counts: Counter[str] = Counter()
    tract_lengths: defaultdict[str, list[int]] = defaultdict(list)
    normalized: dict[tuple[str, int], tuple[TruthTract, ...]] = {}
    for chrom in AUTOSOMES:
        marker_count = model_specs[chrom].marker_count
        max_breakpoints = max_breakpoints_by_autosome.get(chrom)
        if (
            not isinstance(max_breakpoints, int)
            or isinstance(max_breakpoints, bool)
            or max_breakpoints < 0
        ):
            raise ValueError(f"invalid breakpoint envelope for chr{chrom}")
        for haplotype in (0, 1):
            tracts = grouped.get((chrom, haplotype), [])
            expected_start = 0
            previous_identity: tuple[str, int] | None = None
            for tract in tracts:
                if tract.start_marker_index != expected_start:
                    raise ValueError(
                        f"{path}: chr{chrom} hap{haplotype} tract coverage has a gap/overlap"
                    )
                if tract.end_marker_index_exclusive > marker_count:
                    raise ValueError(
                        f"{path}: chr{chrom} hap{haplotype} tract exceeds model markers"
                    )
                identity = (tract.donor_iid, tract.source_hap)
                if identity == previous_identity:
                    raise ValueError(
                        f"{path}: adjacent identical chr{chrom} hap{haplotype} tracts "
                        "must be merged"
                    )
                length = tract.end_marker_index_exclusive - tract.start_marker_index
                ancestry = donor_labels[tract.donor_iid]
                ancestry_marker_counts[ancestry] += length
                tract_lengths[ancestry].append(length)
                expected_start = tract.end_marker_index_exclusive
                previous_identity = identity
            if expected_start != marker_count:
                raise ValueError(f"{path}: chr{chrom} hap{haplotype} tract truth does not cover C")
            breakpoints = max(0, len(tracts) - 1)
            if breakpoints > max_breakpoints:
                raise ValueError(
                    f"{path}: chr{chrom} hap{haplotype} has {breakpoints} breakpoints, "
                    f"above the predeclared maximum {max_breakpoints}"
                )
            normalized[(chrom, haplotype)] = tuple(tracts)

    try:
        tolerance = Decimal(str(fraction_absolute_tolerance))
    except InvalidOperation:
        raise ValueError("invalid target-fraction tolerance") from None
    total_markers = sum(ancestry_marker_counts.values())
    if total_markers <= 0:
        raise ValueError(f"{path}: no tract marker contributions")
    realized_fractions: dict[str, str] = {}
    for label in sorted(SUPERPOPULATIONS):
        try:
            target = Decimal(str(target_marker_ancestry_fractions[label]))
        except (KeyError, InvalidOperation):
            raise ValueError(f"invalid target fraction for {label}") from None
        realized = Decimal(ancestry_marker_counts[label]) / Decimal(total_markers)
        if abs(realized - target) > tolerance:
            raise ValueError(
                f"{path}: realized {label} marker fraction {realized} differs from "
                f"target {target} by more than {tolerance}"
            )
        realized_fractions[label] = str(realized)

    return TractTruth(
        path=path,
        sha256=expected_sha256,
        tracts_by_haplotype=normalized,
        tract_count=sum(len(tracts) for tracts in normalized.values()),
        summary={
            "ancestry_marker_counts": {
                label: ancestry_marker_counts[label] for label in sorted(SUPERPOPULATIONS)
            },
            "realized_marker_ancestry_fractions": realized_fractions,
            "tract_counts_by_class": {
                label: len(tract_lengths[label]) for label in sorted(SUPERPOPULATIONS)
            },
            "tract_marker_length_range_by_class": {
                label: (
                    {"minimum": min(tract_lengths[label]), "maximum": max(tract_lengths[label])}
                    if tract_lengths[label]
                    else {"minimum": None, "maximum": None}
                )
                for label in sorted(SUPERPOPULATIONS)
            },
        },
    )


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


def attach_marker_truth(
    fixture: ValidationFixture,
    marker_truth_path: Path,
    *,
    tract_truth: TractTruth,
    model_specs: Mapping[str, TruthModelSpec],
    donor_labels: Mapping[str, str],
    donor_iids: frozenset[str],
    expected_sha256: str,
    expected_truth_donors: frozenset[str],
) -> ValidationFixture:
    """Stream exact donor-haplotype truth and independently derive Gnomix windows."""
    (parsed,), snapshot = stable_read(
        marker_truth_path,
        lambda pinned_path: (
            _attach_marker_truth_pinned(
                fixture,
                pinned_path,
                tract_truth=tract_truth,
                model_specs=model_specs,
                donor_labels=donor_labels,
                donor_iids=donor_iids,
                expected_sha256=expected_sha256,
                expected_truth_donors=expected_truth_donors,
            ),
        ),
    )
    if snapshot["sha256"] != expected_sha256:
        raise ValueError(f"{marker_truth_path}: SHA-256 does not match manifest")
    return replace(
        parsed,
        marker_truth_path=marker_truth_path,
        marker_truth_sha256=str(snapshot["sha256"]),
    )


def _attach_marker_truth_pinned(
    fixture: ValidationFixture,
    marker_truth_path: Path,
    *,
    tract_truth: TractTruth,
    model_specs: Mapping[str, TruthModelSpec],
    donor_labels: Mapping[str, str],
    donor_iids: frozenset[str],
    expected_sha256: str,
    expected_truth_donors: frozenset[str],
) -> ValidationFixture:
    """Parse marker truth through an already descriptor-pinned path."""
    expected_chrom_index = 0
    expected_marker_index = 0
    row_count = 0
    truth_donors: set[str] = set()
    window_counts: defaultdict[tuple[str, int, int], Counter[int]] = defaultdict(Counter)
    window_donor_counts: defaultdict[tuple[str, int, int], Counter[str]] = defaultdict(Counter)
    tract_offsets: defaultdict[tuple[str, int], int] = defaultdict(int)

    with marker_truth_path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            try:
                line = raw_line.decode("utf-8").rstrip("\r\n")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    f"{marker_truth_path}:{line_number}: marker truth is not UTF-8"
                ) from exc
            columns = tuple(line.split("\t"))
            if line_number == 1:
                if columns != MARKER_TRUTH_HEADER:
                    raise ValueError(f"{marker_truth_path}: expected exact marker-truth header")
                continue
            if not line or len(columns) != len(MARKER_TRUTH_HEADER):
                raise ValueError(
                    f"{marker_truth_path}:{line_number}: expected exactly nine columns"
                )
            while (
                expected_chrom_index < len(AUTOSOMES)
                and expected_marker_index
                == model_specs[AUTOSOMES[expected_chrom_index]].marker_count
            ):
                expected_chrom_index += 1
                expected_marker_index = 0
            if expected_chrom_index >= len(AUTOSOMES):
                raise ValueError(f"{marker_truth_path}:{line_number}: excess marker row")
            (
                sim_iid,
                raw_chrom,
                raw_index,
                raw_position,
                rsid,
                hap0_donor,
                raw_hap0_source,
                hap1_donor,
                raw_hap1_source,
            ) = columns
            chrom = canonical_autosome(raw_chrom)
            expected_chrom = AUTOSOMES[expected_chrom_index]
            try:
                marker_index = int(raw_index)
                position = int(raw_position)
                hap0_source = int(raw_hap0_source)
                hap1_source = int(raw_hap1_source)
            except ValueError:
                raise ValueError(
                    f"{marker_truth_path}:{line_number}: invalid numeric field"
                ) from None
            if sim_iid != fixture.iid:
                raise ValueError(
                    f"{marker_truth_path}:{line_number}: expected IID {fixture.iid!r}"
                )
            if chrom != expected_chrom or marker_index != expected_marker_index:
                raise ValueError(
                    f"{marker_truth_path}:{line_number}: expected chr{expected_chrom} "
                    f"marker_index {expected_marker_index}"
                )
            spec = model_specs[chrom]
            if position != int(spec.positions.item(marker_index)):
                raise ValueError(
                    f"{marker_truth_path}:{line_number}: position does not match model"
                )
            if not rsid:
                raise ValueError(f"{marker_truth_path}:{line_number}: empty rsID")
            for donor, source_hap in (
                (hap0_donor, hap0_source),
                (hap1_donor, hap1_source),
            ):
                if donor not in donor_iids or donor not in donor_labels:
                    raise ValueError(
                        f"{marker_truth_path}:{line_number}: undeclared donor {donor!r}"
                    )
                if source_hap not in {0, 1}:
                    raise ValueError(
                        f"{marker_truth_path}:{line_number}: source haplotype must be 0 or 1"
                    )
                truth_donors.add(donor)
            for haplotype, donor, source_hap in (
                (0, hap0_donor, hap0_source),
                (1, hap1_donor, hap1_source),
            ):
                tract_key = (chrom, haplotype)
                tracts = tract_truth.tracts_by_haplotype[tract_key]
                tract_offset = tract_offsets[tract_key]
                while marker_index >= tracts[tract_offset].end_marker_index_exclusive:
                    tract_offset += 1
                tract_offsets[tract_key] = tract_offset
                tract = tracts[tract_offset]
                if not (
                    tract.start_marker_index <= marker_index < tract.end_marker_index_exclusive
                    and tract.donor_iid == donor
                    and tract.source_hap == source_hap
                ):
                    raise ValueError(
                        f"{marker_truth_path}:{line_number}: marker contribution "
                        "does not match tract truth"
                    )
            window = min(marker_index // spec.window_size, spec.window_count - 1)
            hap0_code = spec.population_order.index(donor_labels[hap0_donor])
            hap1_code = spec.population_order.index(donor_labels[hap1_donor])
            window_counts[(chrom, window, 0)][hap0_code] += 1
            window_counts[(chrom, window, 1)][hap1_code] += 1
            window_donor_counts[(chrom, window, 0)][hap0_donor] += 1
            window_donor_counts[(chrom, window, 1)][hap1_donor] += 1
            expected_marker_index += 1
            row_count += 1

    while (
        expected_chrom_index < len(AUTOSOMES)
        and expected_marker_index == model_specs[AUTOSOMES[expected_chrom_index]].marker_count
    ):
        expected_chrom_index += 1
        expected_marker_index = 0
    if expected_chrom_index != len(AUTOSOMES):
        chrom = AUTOSOMES[expected_chrom_index]
        raise ValueError(
            f"{marker_truth_path}: marker truth ended before chr{chrom} "
            f"marker_index {expected_marker_index}"
        )
    if truth_donors != set(expected_truth_donors):
        raise ValueError(
            f"{marker_truth_path}: observed donor set does not match simulation manifest"
        )

    derived_windows: list[TruthWindow] = []
    truth_founders_by_class: defaultdict[str, set[str]] = defaultdict(set)
    for chrom in AUTOSOMES:
        spec = model_specs[chrom]
        for window in range(spec.window_count):
            start_index = window * spec.window_size
            end_index = (
                spec.marker_count - 1
                if window == spec.window_count - 1
                else (window + 1) * spec.window_size - 1
            )
            labels: list[str] = []
            for haplotype in (0, 1):
                counts = window_counts[(chrom, window, haplotype)]
                expected_count = end_index - start_index + 1
                if sum(counts.values()) != expected_count:
                    raise ValueError(
                        f"{marker_truth_path}: incomplete chr{chrom} window {window} truth"
                    )
                max_count = max(counts.values())
                code = min(code for code, count in counts.items() if count == max_count)
                label = spec.population_order[code]
                labels.append(label)
                donor_counts = window_donor_counts[(chrom, window, haplotype)]
                eligible_donors = {
                    donor: count
                    for donor, count in donor_counts.items()
                    if donor_labels[donor] == label
                }
                maximum_donor_count = max(eligible_donors.values())
                modal_founder = min(
                    donor
                    for donor, count in eligible_donors.items()
                    if count == maximum_donor_count
                )
                truth_founders_by_class[label].add(modal_founder)
            derived_windows.append(
                TruthWindow(
                    chrom=chrom,
                    start=int(spec.positions.item(start_index)),
                    end=int(spec.positions.item(end_index)),
                    hap0=labels[0],
                    hap1=labels[1],
                )
            )
    if tuple(derived_windows) != fixture.truth_windows:
        raise ValueError(f"{fixture.truth_path}: cached window truth does not match marker truth")
    return replace(
        fixture,
        marker_truth_path=marker_truth_path,
        marker_truth_sha256=expected_sha256,
        marker_truth_rows=row_count,
        truth_donor_iids=frozenset(truth_donors),
        tract_truth_path=tract_truth.path,
        tract_truth_sha256=tract_truth.sha256,
        tract_count=tract_truth.tract_count,
        tract_summary=tract_truth.summary,
        model_marker_counts_by_autosome={
            chrom: model_specs[chrom].marker_count for chrom in AUTOSOMES
        },
        truth_founders_by_class={
            label: frozenset(truth_founders_by_class[label]) for label in sorted(SUPERPOPULATIONS)
        },
    )


def attach_validated_simulation_truth(
    fixture: ValidationFixture,
    *,
    marker_truth_path: Path,
    tract_truth_path: Path,
    model_specs: Mapping[str, TruthModelSpec],
    donor_labels: Mapping[str, str],
    donor_iids: frozenset[str],
    simulation_manifest: SimulationManifest,
) -> ValidationFixture:
    """Bind tract and marker truth to one manifest-declared simulation."""
    entry = simulation_manifest.simulations[fixture.iid]
    tract_truth = read_tract_truth(
        tract_truth_path,
        iid=fixture.iid,
        model_specs=model_specs,
        donor_iids=donor_iids,
        donor_labels=donor_labels,
        max_breakpoints_by_autosome=simulation_manifest.simulation_protocol[
            "max_breakpoints_per_haplotype_by_autosome"
        ],
        target_marker_ancestry_fractions=entry["target_marker_ancestry_fractions"],
        fraction_absolute_tolerance=entry["fraction_absolute_tolerance"],
        expected_sha256=str(entry["tract_truth_sha256"]),
    )
    return attach_marker_truth(
        fixture,
        marker_truth_path,
        tract_truth=tract_truth,
        model_specs=model_specs,
        donor_labels=donor_labels,
        donor_iids=donor_iids,
        expected_sha256=str(entry["marker_truth_sha256"]),
        expected_truth_donors=frozenset(entry["donor_iids"]),
    )


def validate_simulation_fixtures(
    *,
    simulation_manifest: SimulationManifest,
    dataset_split: str,
    fixtures: Sequence[ValidationFixture],
) -> None:
    """Bind selected fixture, marker truth, and cached windows to the manifest."""
    expected_iids = set(simulation_manifest.splits[dataset_split])
    fixture_by_iid = {fixture.iid: fixture for fixture in fixtures}
    if set(fixture_by_iid) != expected_iids:
        raise ValueError(f"fixtures must match simulation split {dataset_split!r} exactly")
    for iid, fixture in fixture_by_iid.items():
        entry = simulation_manifest.simulations[iid]
        checks = (
            (fixture.sha256, entry["fixture_sha256"], "simulated fixture"),
            (fixture.truth_sha256, entry["window_truth_sha256"], "window truth"),
            (fixture.marker_truth_sha256, entry["marker_truth_sha256"], "marker truth"),
            (fixture.tract_truth_sha256, entry["tract_truth_sha256"], "tract truth"),
        )
        for observed, expected, description in checks:
            if observed != expected:
                raise ValueError(f"{description} {iid!r} SHA-256 does not match manifest")
        if fixture.validation_stratum != entry["validation_stratum"]:
            raise ValueError(f"fixture {iid!r} validation stratum does not match manifest")
        expected_donors = frozenset(entry["donor_iids"])
        if fixture.truth_donor_iids != expected_donors:
            raise ValueError(f"fixture {iid!r} donor set does not match manifest")


def read_fixture(
    iid: str,
    validation_stratum: str,
    path: Path,
    truth_path: Path,
) -> ValidationFixture:
    """Read one explicit five-column simulated validation fixture.

    Non-autosomal rows are counted but excluded because LAI coverage and the
    production runner are autosome-only.  Duplicate rsIDs are deterministically
    keep-first, matching the single-vendor sample database primary key.
    """
    compressed = path.suffix == ".gz"
    (parsed,), fixture_snapshot = stable_read(
        path,
        lambda pinned_path: (
            _read_fixture_pinned(
                iid,
                validation_stratum,
                pinned_path,
                truth_path,
                compressed=compressed,
            ),
        ),
    )
    return replace(
        parsed,
        path=path,
        sha256=str(fixture_snapshot["sha256"]),
    )


def _read_fixture_pinned(
    iid: str,
    validation_stratum: str,
    path: Path,
    truth_path: Path,
    *,
    compressed: bool,
) -> ValidationFixture:
    """Parse a fixture through an already descriptor-pinned path."""
    opener = gzip.open if compressed else open
    markers: list[Marker] = []
    seen_rsids: set[str] = set()
    counts: Counter[str] = Counter()
    with opener(path, "rt", encoding="utf-8", errors="strict") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if parts[0].strip().lower() == "rsid":
                continue
            if len(parts) != 5:
                raise ValueError(
                    f"{path}:{line_number}: expected 5 tab-separated columns, got {len(parts)}"
                )
            rsid, raw_chrom, raw_pos, allele1, allele2 = (part.strip() for part in parts)
            if not rsid:
                raise ValueError(f"{path}:{line_number}: empty rsID")
            try:
                pos = int(raw_pos)
            except ValueError:
                raise ValueError(
                    f"{path}:{line_number}: non-numeric position {raw_pos!r}"
                ) from None
            if pos <= 0:
                raise ValueError(f"{path}:{line_number}: position must be positive")
            allele1, allele2 = allele1.upper(), allele2.upper()
            if allele1 not in {"A", "C", "G", "T"} or allele2 not in {
                "A",
                "C",
                "G",
                "T",
            }:
                raise ValueError(
                    f"{path}:{line_number}: expected one A/C/G/T base per allele, "
                    f"got {allele1!r}/{allele2!r}"
                )
            chrom = canonical_autosome(raw_chrom)
            if chrom is None:
                counts["non_autosomal_rows"] += 1
                continue
            if rsid in seen_rsids:
                counts["duplicate_rsid_rows"] += 1
                continue
            seen_rsids.add(rsid)
            markers.append(Marker(rsid, chrom, pos, f"{allele1}{allele2}"))
            counts["autosomal_markers"] += 1
    if not markers:
        raise ValueError(f"{path}: no unique autosomal markers found")
    markers.sort(key=lambda marker: (int(marker.chrom), marker.pos, marker.rsid))
    truth_windows, truth_snapshot = stable_read(truth_path, read_local_truth)
    return ValidationFixture(
        iid=iid,
        validation_stratum=validation_stratum,
        path=path,
        sha256="",
        markers=tuple(markers),
        parsing=dict(sorted(counts.items())),
        truth_path=truth_path,
        truth_sha256=str(truth_snapshot["sha256"]),
        truth_windows=truth_windows,
    )


def read_site_manifest(
    name: str,
    path: Path,
) -> SiteManifest:
    """Read one explicit three-column, privacy-safe derived site list."""
    parsed, snapshot = stable_read(
        path,
        lambda pinned_path: _read_site_manifest_pinned(name, pinned_path),
    )
    return replace(
        parsed,
        path=path,
        sha256=str(snapshot["sha256"]),
    )


def _read_site_manifest_pinned(name: str, path: Path) -> SiteManifest:
    """Parse a site manifest through an already descriptor-pinned path."""
    rsids: set[str] = set()
    coordinate_by_rsid: dict[str, tuple[str, int]] = {}
    sites: list[ManifestSite] = []
    seen_sites: set[tuple[str, str, int]] = set()
    site_count = 0
    with path.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if parts[0].strip().lower() == "rsid":
                continue
            if len(parts) != 3:
                raise ValueError(f"{path}:{line_number}: expected exact 3-column site manifest")
            rsid, raw_chrom, raw_pos = (part.strip() for part in parts)
            try:
                pos = int(raw_pos)
            except ValueError:
                raise ValueError(
                    f"{path}:{line_number}: non-numeric position {raw_pos!r}"
                ) from None
            if not rsid or not raw_chrom or pos <= 0:
                raise ValueError(f"{path}:{line_number}: invalid site")
            site_count += 1
            chrom = canonical_autosome(raw_chrom)
            if chrom is None:
                continue
            key = (rsid, chrom, pos)
            previous_coordinate = coordinate_by_rsid.setdefault(rsid, (chrom, pos))
            if previous_coordinate != (chrom, pos):
                raise ValueError(
                    f"{path}:{line_number}: rsID {rsid!r} maps to multiple coordinates"
                )
            if key in seen_sites:
                continue
            seen_sites.add(key)
            rsids.add(rsid)
            sites.append(ManifestSite(rsid, chrom, pos))
    if not sites:
        raise ValueError(f"{path}: no autosomal sites found")
    sites.sort(key=lambda site: (int(site.chrom), site.pos, site.rsid))
    return SiteManifest(
        name=name,
        path=path,
        sha256="",
        rsids=frozenset(rsids),
        site_count=site_count,
        autosomal_sites=tuple(sites),
    )


def read_identifier_manifest(
    name: str,
    path: Path,
) -> IdentifierManifest:
    """Read a privacy-safe one-column identifier membership snapshot."""
    parsed, snapshot = stable_read(
        path,
        lambda pinned_path: _read_identifier_manifest_pinned(name, pinned_path),
    )
    return replace(
        parsed,
        path=path,
        sha256=str(snapshot["sha256"]),
    )


def _read_identifier_manifest_pinned(name: str, path: Path) -> IdentifierManifest:
    """Parse an identifier manifest through an already descriptor-pinned path."""
    identifiers: set[str] = set()
    row_count = 0
    with path.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            value = raw.rstrip("\r\n").strip()
            if not value or value.startswith("#"):
                continue
            if "\t" in value:
                raise ValueError(f"{path}:{line_number}: expected one identifier column")
            if value.lower() == "rsid":
                continue
            row_count += 1
            if value in identifiers:
                raise ValueError(f"{path}:{line_number}: duplicate identifier {value!r}")
            identifiers.add(value)
    if not identifiers:
        raise ValueError(f"{path}: no identifiers found")
    return IdentifierManifest(
        name=name,
        path=path,
        sha256="",
        identifiers=frozenset(identifiers),
        identifier_count=row_count,
    )


def read_iid_set(path: Path) -> frozenset[str]:
    """Read a one-or-more-column IID manifest without touching genotype data."""
    iids: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            iid = line.split("\t", 1)[0].strip()
            if iid.lower() in {"iid", "sample", "sample_id"}:
                continue
            if not iid:
                raise ValueError(f"{path}:{line_number}: empty IID")
            if iid in iids:
                raise ValueError(f"{path}:{line_number}: duplicate IID {iid!r}")
            iids.add(iid)
    if not iids:
        raise ValueError(f"{path}: no IIDs found")
    return frozenset(iids)


def read_and_validate_relationships(
    path: Path,
    *,
    simulation_manifest: SimulationManifest,
    donor_iids: frozenset[str],
    isolation_iids: frozenset[str],
) -> dict[str, object]:
    """Validate relationship components and keep each component in one split."""
    expected_sha256 = simulation_manifest.relationships["sha256"]
    parsed, snapshot = stable_read(
        path,
        lambda pinned_path: _read_and_validate_relationships_pinned(
            pinned_path,
            simulation_manifest=simulation_manifest,
            donor_iids=donor_iids,
            isolation_iids=isolation_iids,
        ),
    )
    if snapshot["sha256"] != expected_sha256:
        raise ValueError(f"{path}: relationship file SHA-256 does not match manifest")
    return {
        **parsed,
        "filename": path.name,
        "sha256": str(snapshot["sha256"]),
    }


def _read_and_validate_relationships_pinned(
    path: Path,
    *,
    simulation_manifest: SimulationManifest,
    donor_iids: frozenset[str],
    isolation_iids: frozenset[str],
) -> dict[str, object]:
    """Parse relationships through an already descriptor-pinned path."""
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

    seen_pairs: set[tuple[str, str]] = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            columns = tuple(raw.rstrip("\r\n").split("\t"))
            if line_number == 1:
                if columns != ("iid_a", "iid_b", "relationship"):
                    raise ValueError(f"{path}: expected exact relationship header")
                continue
            if len(columns) != 3 or any(not value.strip() for value in columns):
                raise ValueError(f"{path}:{line_number}: invalid relationship row")
            iid_a, iid_b, relationship = (value.strip() for value in columns)
            if (iid_a == iid_b) != (relationship == "self"):
                raise ValueError(f"{path}:{line_number}: invalid self relationship")
            pair = tuple(sorted((iid_a, iid_b)))
            if pair in seen_pairs:
                raise ValueError(f"{path}:{line_number}: duplicate relationship pair")
            seen_pairs.add(pair)
            union(iid_a, iid_b)
    declared_iids = set(parent)
    if declared_iids != set(isolation_iids):
        missing = sorted(set(isolation_iids) - declared_iids)
        extra = sorted(declared_iids - set(isolation_iids))
        raise ValueError(
            f"{path}: relationship IID set must equal the protected set; "
            f"missing={missing}, extra={extra}"
        )
    if not set(donor_iids) <= declared_iids:
        raise ValueError(f"{path}: relationship file omits validation donors")

    components: defaultdict[str, set[str]] = defaultdict(set)
    for iid in sorted(declared_iids):
        components[find(iid)].add(iid)
    donor_split: dict[str, str] = {}
    for split, simulation_iids in simulation_manifest.splits.items():
        for simulation_iid in simulation_iids:
            for donor in simulation_manifest.simulations[simulation_iid]["donor_iids"]:
                previous = donor_split.setdefault(donor, split)
                if previous != split:
                    raise ValueError(f"donor {donor!r} contributes to both splits")
    for component in components.values():
        component_splits = {donor_split[iid] for iid in component if iid in donor_split}
        if len(component_splits) > 1:
            raise ValueError(
                f"{path}: relationship component crosses simulation splits: "
                + ", ".join(sorted(component))
            )
    component_payload = [sorted(component) for component in components.values()]
    component_payload.sort()
    return {
        "iid_count": len(declared_iids),
        "component_count": len(component_payload),
        "components_sha256": sha256_json(component_payload),
    }


def read_calibration_reference_manifest(
    path: Path,
    source_bundle_artifact_sha256: str,
) -> CalibrationReferenceManifest:
    """Read provenance for a donor-excluded calibration phasing reference."""
    payload, payload_sha256 = _read_json_payload_and_sha256(path)
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise ValueError(f"{path}: expected calibration-reference schema version 1")
    recorded_source = payload.get("source_bundle_artifact_sha256")
    if recorded_source != source_bundle_artifact_sha256:
        raise ValueError(f"{path}: source bundle SHA does not match --bundle-artifact-sha256")
    raw_excluded = payload.get("excluded_iids")
    if (
        not isinstance(raw_excluded, list)
        or not raw_excluded
        or not all(isinstance(iid, str) and iid for iid in raw_excluded)
    ):
        raise ValueError(f"{path}: excluded_iids must be a non-empty string list")
    if len(raw_excluded) != len(set(raw_excluded)):
        raise ValueError(f"{path}: excluded_iids contains duplicates")
    raw_panel = payload.get("phasing_panel")
    if not isinstance(raw_panel, Mapping) or set(raw_panel) != AUTOSOME_SET:
        raise ValueError(f"{path}: phasing_panel must describe autosomes 1..22 exactly")
    panel: dict[str, Mapping[str, object]] = {}
    for chrom in AUTOSOMES:
        entry = raw_panel[chrom]
        if not isinstance(entry, Mapping):
            raise ValueError(f"{path}: phasing_panel[{chrom!r}] must be an object")
        sample_count = entry.get("sample_count")
        if (
            not isinstance(sample_count, int)
            or isinstance(sample_count, bool)
            or sample_count <= 0
        ):
            raise ValueError(f"{path}: chr{chrom} sample_count must be a positive integer")
        for field in ("vcf_sha256", "index_sha256", "sample_ids_sha256"):
            if not is_sha256(entry.get(field)):
                raise ValueError(f"{path}: chr{chrom} {field} is not a complete SHA-256")
        for field in ("vcf_size_bytes", "index_size_bytes"):
            value = entry.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{path}: chr{chrom} {field} must be a positive integer")
        panel[chrom] = dict(entry)

    raw_inherited = payload.get("inherited_files")
    if not isinstance(raw_inherited, Mapping) or not raw_inherited:
        raise ValueError(f"{path}: inherited_files must be a non-empty object")
    inherited: dict[str, Mapping[str, object]] = {}
    for raw_relative, raw_entry in raw_inherited.items():
        if not isinstance(raw_relative, str):
            raise ValueError(f"{path}: inherited file paths must be strings")
        relative = PurePosixPath(raw_relative)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.parts[0] == "phasing_panel"
        ):
            raise ValueError(f"{path}: unsafe inherited file path {raw_relative!r}")
        if not isinstance(raw_entry, Mapping):
            raise ValueError(f"{path}: inherited file {raw_relative!r} must be an object")
        digest = raw_entry.get("sha256")
        size = raw_entry.get("size_bytes")
        if not is_sha256(digest):
            raise ValueError(f"{path}: inherited file {raw_relative!r} has an invalid SHA-256")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError(f"{path}: inherited file {raw_relative!r} has an invalid byte size")
        inherited[relative.as_posix()] = {"sha256": digest, "size_bytes": size}

    required_inherited = {
        "metadata.json",
        "beagle/beagle.jar",
        *(
            f"gnomix_models/chr{chrom}/{filename}"
            for chrom in AUTOSOMES
            for filename in ("metadata.npz", "base_coefs.npz", "smoother.json")
        ),
        *(f"genetic_maps/plink.chrchr{chrom}.GRCh38.map" for chrom in AUTOSOMES),
    }
    missing_inherited = sorted(required_inherited - set(inherited))
    if missing_inherited:
        raise ValueError(
            f"{path}: inherited_files omits required production component(s): "
            + ", ".join(missing_inherited)
        )
    liftover_candidates = {
        "liftover/array_site_mapping.tsv",
        "liftover/rsid_to_grch38.tsv",
    }
    if not liftover_candidates & set(inherited):
        raise ValueError(f"{path}: inherited_files omits the production array-site map")
    resolved_liftover_file = payload.get("resolved_liftover_file")
    if (
        resolved_liftover_file not in liftover_candidates
        or resolved_liftover_file not in inherited
    ):
        raise ValueError(
            f"{path}: resolved_liftover_file must name the manifest-pinned production map"
        )
    inherited_tree_sha256 = payload.get("inherited_tree_sha256")
    calculated_tree_sha256 = sha256_json(inherited)
    if inherited_tree_sha256 != calculated_tree_sha256:
        raise ValueError(f"{path}: inherited_tree_sha256 does not match inherited_files")
    return CalibrationReferenceManifest(
        path=path,
        sha256=payload_sha256,
        source_bundle_artifact_sha256=source_bundle_artifact_sha256,
        excluded_iids=frozenset(raw_excluded),
        phasing_panel=panel,
        inherited_files=inherited,
        inherited_tree_sha256=calculated_tree_sha256,
        resolved_liftover_file=str(resolved_liftover_file),
    )


def read_vcf_sample_ids(path: Path) -> frozenset[str]:
    """Read only the BGZF/VCF header's sample IDs."""
    with gzip.open(path, "rt", encoding="utf-8", errors="strict") as handle:
        for raw in handle:
            if raw.startswith("#CHROM\t"):
                fields = raw.rstrip("\r\n").split("\t")
                sample_ids = fields[9:]
                if not sample_ids or any(not iid for iid in sample_ids):
                    raise ValueError(f"{path}: VCF header has no non-empty sample IDs")
                if len(sample_ids) != len(set(sample_ids)):
                    raise ValueError(f"{path}: VCF header contains duplicate sample IDs")
                return frozenset(sample_ids)
            if not raw.startswith("#"):
                break
    raise ValueError(f"{path}: VCF header has no #CHROM sample line")


def _stable_file_snapshot(path: Path) -> dict[str, object]:
    """Hash one descriptor-pinned regular file without following a leaf symlink."""
    try:
        path_before = path.lstat()
    except OSError as exc:
        raise ValueError(f"{path}: cannot stat input file: {exc}") from exc
    if stat.S_ISLNK(path_before.st_mode) or not stat.S_ISREG(path_before.st_mode):
        raise ValueError(f"{path}: expected a non-symlink regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{path}: cannot safely open input file: {exc}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or _stat_signature(before) != _stat_signature(
            path_before
        ):
            raise ValueError(f"{path}: input file changed while it was opened")
        digest = hashlib.sha256()
        os.lseek(fd, 0, os.SEEK_SET)
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(fd)
        if _stat_signature(before) != _stat_signature(after):
            raise ValueError(f"{path}: file changed while its SHA-256 was being verified")
        try:
            path_after = path.lstat()
        except OSError as exc:
            raise ValueError(f"{path}: input file changed after hashing") from exc
        if _stat_signature(after) != _stat_signature(path_after):
            raise ValueError(f"{path}: input pathname changed while it was being hashed")
        return {
            "sha256": digest.hexdigest(),
            "device": after.st_dev,
            "inode": after.st_ino,
            "size_bytes": after.st_size,
            "mtime_ns": after.st_mtime_ns,
            "ctime_ns": after.st_ctime_ns,
        }
    finally:
        os.close(fd)


def _stable_file_verification(path: Path, expected_sha256: str) -> dict[str, object]:
    snapshot = _stable_file_snapshot(path)
    if snapshot["sha256"] != expected_sha256:
        raise ValueError(f"{path}: live SHA-256 does not match the expected manifest")
    return snapshot


def _stat_signature(stat_result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_ctime_ns,
    )


def stable_read(path: Path, reader: Callable[[Path], Any]) -> tuple[Any, dict[str, object]]:
    """Parse and hash one descriptor-pinned regular file inode.

    The reader must open its supplied path at most once.  Darwin ``/dev/fd``
    aliases share the held descriptor's offset, so multiple opens are not
    independent streams; callers needing multiple passes must seek one handle.
    """
    try:
        path_before = path.lstat()
    except OSError as exc:
        raise ValueError(f"{path}: cannot stat input file: {exc}") from exc
    if stat.S_ISLNK(path_before.st_mode) or not stat.S_ISREG(path_before.st_mode):
        raise ValueError(f"{path}: expected a non-symlink regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{path}: cannot safely open input file: {exc}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or _stat_signature(before) != _stat_signature(
            path_before
        ):
            raise ValueError(f"{path}: input file changed while it was opened")
        try:
            pinned_path = lai_coverage_plan.descriptor_file_path(fd)
        except OSError as exc:
            raise ValueError(f"{path}: no portable descriptor path is available") from exc
        os.lseek(fd, 0, os.SEEK_SET)
        value = reader(pinned_path)
        if dataclasses.is_dataclass(value) and hasattr(value, "path"):
            value = replace(value, path=path)
        digest = hashlib.sha256()
        os.lseek(fd, 0, os.SEEK_SET)
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(fd)
        if _stat_signature(before) != _stat_signature(after):
            raise ValueError(f"{path}: file changed while it was being parsed")
        try:
            path_after = path.lstat()
        except OSError as exc:
            raise ValueError(f"{path}: input file changed after parsing") from exc
        if _stat_signature(after) != _stat_signature(path_after):
            raise ValueError(f"{path}: input pathname changed while it was being parsed")
        return value, {
            "sha256": digest.hexdigest(),
            "device": after.st_dev,
            "inode": after.st_ino,
            "size_bytes": after.st_size,
            "mtime_ns": after.st_mtime_ns,
            "ctime_ns": after.st_ctime_ns,
        }
    finally:
        os.close(fd)


def build_reference_verification(
    bundle_dir: Path,
    reference_manifest: CalibrationReferenceManifest,
) -> dict[str, object]:
    """Perform the one-time full hash pass used before a SLURM sweep."""
    panel: dict[str, dict[str, object]] = {}
    panel_dir = bundle_dir / "phasing_panel"
    for chrom in AUTOSOMES:
        vcf_path = panel_dir / f"ref_panel_chr{chrom}.vcf.gz"
        index_path = Path(f"{vcf_path}.tbi")
        if not vcf_path.is_file() or not index_path.is_file():
            raise ValueError(f"calibration phasing panel chr{chrom} VCF/index is missing")
        expected = reference_manifest.phasing_panel[chrom]
        panel[chrom] = {
            "vcf": _stable_file_verification(vcf_path, str(expected["vcf_sha256"])),
            "index": _stable_file_verification(
                index_path,
                str(expected["index_sha256"]),
            ),
        }

    inherited: dict[str, dict[str, object]] = {}
    for relative, expected in sorted(reference_manifest.inherited_files.items()):
        live_path = bundle_dir.joinpath(*PurePosixPath(relative).parts)
        if not live_path.is_file():
            raise ValueError(f"calibration bundle inherited file is missing: {relative}")
        inherited[relative] = _stable_file_verification(
            live_path,
            str(expected["sha256"]),
        )

    return {
        "schema_version": 1,
        "reference_manifest_sha256": reference_manifest.sha256,
        "inherited_tree_sha256": reference_manifest.inherited_tree_sha256,
        "phasing_panel": panel,
        "inherited_files": inherited,
    }


def _validate_verification_entry(
    *,
    description: str,
    entry: object,
    expected_sha256: object,
    expected_size: object,
) -> Mapping[str, object]:
    if not isinstance(entry, Mapping):
        raise ValueError(f"reference verification entry is missing for {description}")
    if entry.get("sha256") != expected_sha256 or entry.get("size_bytes") != expected_size:
        raise ValueError(f"reference verification does not match manifest for {description}")
    for field in ("device", "inode", "mtime_ns", "ctime_ns"):
        value = entry.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"reference verification has an invalid {field} for {description}")
    return entry


def validate_reference_verification(
    *,
    bundle_dir: Path,
    reference_manifest: CalibrationReferenceManifest,
    payload: Mapping[str, object],
) -> None:
    """Validate an attestation and cheaply prove live files have not changed."""
    if payload.get("schema_version") != 1:
        raise ValueError("reference verification must use schema version 1")
    if payload.get("reference_manifest_sha256") != reference_manifest.sha256:
        raise ValueError("reference verification was built for a different manifest")
    if payload.get("inherited_tree_sha256") != reference_manifest.inherited_tree_sha256:
        raise ValueError("reference verification inherited-tree hash is inconsistent")
    raw_panel = payload.get("phasing_panel")
    raw_inherited = payload.get("inherited_files")
    if not isinstance(raw_panel, Mapping) or set(raw_panel) != AUTOSOME_SET:
        raise ValueError("reference verification must cover autosomes 1..22 exactly")
    if not isinstance(raw_inherited, Mapping) or set(raw_inherited) != set(
        reference_manifest.inherited_files
    ):
        raise ValueError("reference verification inherited-file set is inconsistent")

    def validate_live(path: Path, entry: Mapping[str, object], description: str) -> None:
        try:
            live_stat = path.lstat()
        except FileNotFoundError:
            raise ValueError(f"verified reference file is missing: {description}")
        if not stat.S_ISREG(live_stat.st_mode):
            raise ValueError(f"verified reference file is not regular: {description}")
        observed = {
            "device": live_stat.st_dev,
            "inode": live_stat.st_ino,
            "size_bytes": live_stat.st_size,
            "mtime_ns": live_stat.st_mtime_ns,
            "ctime_ns": live_stat.st_ctime_ns,
        }
        expected = {field: entry[field] for field in observed}
        if observed != expected:
            raise ValueError(
                f"verified reference file changed after the full hash pass: {description}"
            )

    panel_dir = bundle_dir / "phasing_panel"
    for chrom in AUTOSOMES:
        raw_entry = raw_panel[chrom]
        if not isinstance(raw_entry, Mapping) or set(raw_entry) != {"vcf", "index"}:
            raise ValueError(f"reference verification chr{chrom} entry is invalid")
        manifest_entry = reference_manifest.phasing_panel[chrom]
        vcf_entry = _validate_verification_entry(
            description=f"chr{chrom} VCF",
            entry=raw_entry["vcf"],
            expected_sha256=manifest_entry["vcf_sha256"],
            expected_size=manifest_entry["vcf_size_bytes"],
        )
        index_entry = _validate_verification_entry(
            description=f"chr{chrom} index",
            entry=raw_entry["index"],
            expected_sha256=manifest_entry["index_sha256"],
            expected_size=manifest_entry["index_size_bytes"],
        )
        vcf_path = panel_dir / f"ref_panel_chr{chrom}.vcf.gz"
        validate_live(vcf_path, vcf_entry, f"chr{chrom} VCF")
        validate_live(Path(f"{vcf_path}.tbi"), index_entry, f"chr{chrom} index")

    for relative, manifest_entry in reference_manifest.inherited_files.items():
        verification_entry = _validate_verification_entry(
            description=relative,
            entry=raw_inherited[relative],
            expected_sha256=manifest_entry["sha256"],
            expected_size=manifest_entry["size_bytes"],
        )
        live_path = bundle_dir.joinpath(*PurePosixPath(relative).parts)
        validate_live(live_path, verification_entry, relative)


def read_reference_verification(
    path: Path,
    reference_manifest: CalibrationReferenceManifest,
) -> ReferenceVerification:
    payload, payload_sha256 = _read_json_payload_and_sha256(path)
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path}: reference verification must be a JSON object")
    # Structural validation that does not require a bundle path happens through
    # the same validator immediately before use.
    if payload.get("reference_manifest_sha256") != reference_manifest.sha256:
        raise ValueError(f"{path}: verification was built for a different manifest")
    return ReferenceVerification(
        path=path,
        sha256=payload_sha256,
        payload=dict(payload),
    )


def validate_calibration_isolation(
    *,
    bundle_dir: Path,
    fixtures: Sequence[ValidationFixture],
    donor_iids: frozenset[str],
    isolation_iids: frozenset[str],
    training_iids: frozenset[str],
    reference_manifest: CalibrationReferenceManifest,
    reference_verification: Mapping[str, object],
) -> dict[str, dict[str, object]]:
    """Prove fixtures, donors, and declared relatives are isolated."""
    validate_reference_verification(
        bundle_dir=bundle_dir,
        reference_manifest=reference_manifest,
        payload=reference_verification,
    )
    resolved_liftover = resolve_lai_liftover_path(bundle_dir)
    if resolved_liftover is None:
        raise ValueError("calibration bundle has no production-resolved liftover file")
    resolved_relative = resolved_liftover.relative_to(bundle_dir).as_posix()
    if resolved_relative != reference_manifest.resolved_liftover_file:
        raise ValueError(
            "production resolves a liftover file different from the reference manifest"
        )
    fixture_iids = {fixture.iid for fixture in fixtures}
    missing_donors = sorted(set(donor_iids) - set(isolation_iids))
    if missing_donors:
        raise ValueError(
            "validation isolation manifest omits donor IID(s): " + ", ".join(missing_donors)
        )
    protected_iids = set(isolation_iids) | fixture_iids
    missing_exclusions = sorted(set(isolation_iids) - reference_manifest.excluded_iids)
    if missing_exclusions:
        raise ValueError(
            "calibration reference manifest does not exclude protected IID(s): "
            + ", ".join(missing_exclusions)
        )
    training_leaks = sorted(protected_iids & set(training_iids))
    if training_leaks:
        raise ValueError(
            "validation donor/relative/fixture IID(s) remain in Gnomix training: "
            + ", ".join(training_leaks)
        )

    summary: dict[str, dict[str, object]] = {}
    panel_dir = bundle_dir / "phasing_panel"
    for chrom in AUTOSOMES:
        vcf_path = panel_dir / f"ref_panel_chr{chrom}.vcf.gz"
        index_path = Path(f"{vcf_path}.tbi")
        if not vcf_path.is_file() or not index_path.is_file():
            raise ValueError(f"calibration phasing panel chr{chrom} VCF/index is missing")
        sample_iids, vcf_snapshot = stable_read(vcf_path, read_vcf_sample_ids)
        index_snapshot = _stable_file_snapshot(index_path)
        phasing_leaks = sorted(protected_iids & set(sample_iids))
        if phasing_leaks:
            raise ValueError(
                f"calibration phasing panel chr{chrom} still contains protected IID(s): "
                + ", ".join(phasing_leaks)
            )
        sample_ids_sha256 = sha256_json(sorted(sample_iids))
        expected = reference_manifest.phasing_panel[chrom]
        if (
            vcf_snapshot["sha256"] != expected["vcf_sha256"]
            or vcf_snapshot["size_bytes"] != expected["vcf_size_bytes"]
        ):
            raise ValueError(f"calibration phasing panel chr{chrom} VCF identity mismatch")
        if (
            index_snapshot["sha256"] != expected["index_sha256"]
            or index_snapshot["size_bytes"] != expected["index_size_bytes"]
        ):
            raise ValueError(f"calibration phasing panel chr{chrom} index identity mismatch")
        if len(sample_iids) != expected["sample_count"]:
            raise ValueError(f"calibration phasing panel chr{chrom} sample count mismatch")
        if sample_ids_sha256 != expected["sample_ids_sha256"]:
            raise ValueError(f"calibration phasing panel chr{chrom} sample-set hash mismatch")
        summary[chrom] = {
            "sample_count": len(sample_iids),
            "sample_ids_sha256": sample_ids_sha256,
            "vcf_sha256": expected["vcf_sha256"],
            "index_sha256": expected["index_sha256"],
            "vcf_size_bytes": expected["vcf_size_bytes"],
            "index_size_bytes": expected["index_size_bytes"],
        }
    return summary


def build_masks(
    fixture: ValidationFixture,
    twentythreeandme: SiteManifest | None,
    ancestrydna: SiteManifest | None,
    vep_rsids: frozenset[str] | None = None,
) -> tuple[MaskScenario, ...]:
    """Build native, derived/empirical-mask, and source-aware merged scenarios."""
    masks = [
        MaskScenario(
            name="native_unmasked",
            kind="public_fixture_native",
            file_format="",
            markers=fixture.markers,
            manifest_names=(),
        )
    ]
    if twentythreeandme is not None:
        v5_markers = tuple(
            marker for marker in fixture.markers if marker.rsid in twentythreeandme.rsids
        )
        if not v5_markers:
            raise ValueError(
                f"{twentythreeandme.path}: derived 23andMe mask realizes zero "
                f"markers for fixture {fixture.iid}"
            )
        masks.append(
            MaskScenario(
                name="twentythreeandme_derived_mask",
                kind="privacy_safe_derived_site_mask",
                file_format="23andme_v5",
                markers=v5_markers,
                manifest_names=(twentythreeandme.name,),
            )
        )
    if ancestrydna is not None:
        adna_markers = tuple(
            marker for marker in fixture.markers if marker.rsid in ancestrydna.rsids
        )
        if not adna_markers:
            raise ValueError(
                f"{ancestrydna.path}: empirical AncestryDNA mask realizes zero "
                f"markers for fixture {fixture.iid}"
            )
        masks.append(
            MaskScenario(
                name="ancestrydna_empirical_mask",
                kind="privacy_safe_empirical_site_mask",
                file_format="ancestrydna_v2.0",
                markers=adna_markers,
                manifest_names=(ancestrydna.name,),
            )
        )
    if twentythreeandme is not None and ancestrydna is not None:
        merged = _build_coordinate_collapsed_merged_markers(
            fixture.markers,
            twentythreeandme,
            ancestrydna,
            vep_rsids,
        )
        masks.append(
            MaskScenario(
                name="synthetic_merged_derived_masks",
                kind="source_aware_merged_derived_site_masks",
                file_format="merged_v1",
                markers=merged,
                manifest_names=(twentythreeandme.name, ancestrydna.name),
            )
        )
    return tuple(masks)


def _build_coordinate_collapsed_merged_markers(
    markers: Sequence[Marker],
    twentythreeandme: SiteManifest,
    ancestrydna: SiteManifest,
    vep_rsids: frozenset[str] | None,
) -> tuple[Marker, ...]:
    """Mirror production's source-build coordinate merge.

    Production source databases are reduced to coordinate-keyed maps before a
    merged sample is materialized.  Within one source, SQL text ordering makes
    the lexicographically greatest rsID the deterministic survivor at an alias
    locus.  If both source masks realize a coordinate, production's fallback
    merge tiebreaker prefers S1 when the pinned VEP-membership snapshot does not
    distinguish the aliases. The harness applies the same tiebreaker and labels
    the realized locus ``both``. Source coordinates come from the supplied GRCh37
    masks; fixture GRCh38 coordinates remain the target coordinates used for
    chromosome-balanced sampling.
    """
    marker_by_rsid = {marker.rsid: marker for marker in markers}
    coordinate_by_rsid: dict[str, tuple[str, int]] = {}
    for manifest in (twentythreeandme, ancestrydna):
        for site in manifest.autosomal_sites:
            coordinate = (site.chrom, site.pos)
            previous = coordinate_by_rsid.setdefault(site.rsid, coordinate)
            if previous != coordinate:
                raise ValueError(
                    f"merged masks map rsID {site.rsid!r} to multiple GRCh37 coordinates"
                )

    def source_by_coordinate(manifest: SiteManifest) -> dict[tuple[str, int], Marker]:
        candidates: dict[tuple[str, int], list[Marker]] = defaultdict(list)
        for site in manifest.autosomal_sites:
            marker = marker_by_rsid.get(site.rsid)
            if marker is not None:
                candidates[(site.chrom, site.pos)].append(marker)
        return {
            coordinate: max(coordinate_markers, key=lambda marker: marker.rsid)
            for coordinate, coordinate_markers in candidates.items()
        }

    s1_by_coordinate = source_by_coordinate(twentythreeandme)
    s2_by_coordinate = source_by_coordinate(ancestrydna)

    merged: list[Marker] = []
    coordinates = set(s1_by_coordinate) | set(s2_by_coordinate)
    for coordinate in sorted(coordinates, key=lambda item: (int(item[0]), item[1])):
        s1 = s1_by_coordinate.get(coordinate)
        s2 = s2_by_coordinate.get(coordinate)
        if s1 is not None and s2 is not None:
            if s1.rsid == s2.rsid:
                chosen = s1
            else:
                if vep_rsids is None:
                    raise ValueError(
                        "merged alias resolution requires a pinned VEP rsID membership list"
                    )
                s1_hit = s1.rsid in vep_rsids
                s2_hit = s2.rsid in vep_rsids
                chosen = s2 if s2_hit and not s1_hit else s1
            source = "both"
        elif s1 is not None:
            chosen, source = s1, "S1"
        else:
            assert s2 is not None
            chosen, source = s2, "S2"
        merged.append(
            replace(
                chosen,
                source=source,
                db_chrom=coordinate[0],
                db_pos=coordinate[1],
            )
        )
    realized_coordinates: dict[str, tuple[str, int]] = {}
    for marker in merged:
        coordinate = (marker.db_chrom or marker.chrom, marker.db_pos or marker.pos)
        previous = realized_coordinates.setdefault(marker.rsid, coordinate)
        if previous != coordinate:
            raise ValueError(
                f"synthetic merged sample has rsID {marker.rsid!r} at multiple coordinates"
            )
    return tuple(merged)


def parse_fraction(raw: str) -> Decimal:
    try:
        fraction = Decimal(raw)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"invalid fraction {raw!r}") from None
    if not fraction.is_finite():
        raise argparse.ArgumentTypeError("fractions must be finite")
    if not Decimal("0") < fraction <= Decimal("1"):
        raise argparse.ArgumentTypeError("fractions must be > 0 and <= 1")
    return fraction.normalize()


def parse_drop_scenario(raw: str) -> ChromosomeDropScenario:
    """Parse ``NAME=1,2,...`` into a validated structured scenario."""
    if "=" not in raw:
        raise argparse.ArgumentTypeError("drop scenario must be NAME=CHR[,CHR...]")
    name, raw_chroms = raw.split("=", 1)
    name = name.strip()
    if not name or name == "none":
        raise argparse.ArgumentTypeError("drop scenario name is empty or reserved")
    chroms = frozenset(part.strip().removeprefix("chr") for part in raw_chroms.split(","))
    if not chroms or "" in chroms or not chroms <= AUTOSOME_SET:
        raise argparse.ArgumentTypeError("drop chromosomes must be a non-empty subset of 1..22")
    return ChromosomeDropScenario(name=name, dropped_autosomes=chroms)


def _stable_marker_digest(marker: Marker, seed: int) -> bytes:
    payload = f"{seed}\0{marker.rsid}\0{marker.chrom}\0{marker.pos}".encode()
    return hashlib.sha256(payload).digest()


def iter_balanced_nested_order(markers: Sequence[Marker], seed: int) -> Iterator[Marker]:
    """Yield a deterministic order whose prefixes preserve chromosome balance.

    Each chromosome is independently hash-shuffled.  Its j-th item receives the
    exact rational quantile ``(2j+1)/(2*n_chrom)``.  A 22-way heap merge emits
    those already sorted per-chromosome sequences without materializing a second
    whole-genome list of rational sort keys.  Any prefix is consequently balanced
    to within roughly one marker per chromosome, while prefix selection guarantees
    nesting.
    """
    per_chromosome: dict[str, list[Marker]] = defaultdict(list)
    for marker in markers:
        per_chromosome[marker.chrom].append(marker)

    ranked_by_chromosome: dict[int, list[Marker]] = {}
    heap: list[tuple[Fraction, int, tuple[str, str, int], int]] = []
    for chrom in AUTOSOMES:
        chrom_markers = sorted(
            per_chromosome.get(chrom, []),
            key=lambda marker: (_stable_marker_digest(marker, seed), marker.site_key),
        )
        if not chrom_markers:
            continue
        chrom_number = int(chrom)
        ranked_by_chromosome[chrom_number] = chrom_markers
        first = chrom_markers[0]
        heapq.heappush(
            heap,
            (Fraction(1, 2 * len(chrom_markers)), chrom_number, first.site_key, 0),
        )

    while heap:
        _quantile, chrom_number, _site_key, rank = heapq.heappop(heap)
        chrom_markers = ranked_by_chromosome[chrom_number]
        yield chrom_markers[rank]
        next_rank = rank + 1
        if next_rank < len(chrom_markers):
            marker = chrom_markers[next_rank]
            heapq.heappush(
                heap,
                (
                    Fraction(2 * next_rank + 1, 2 * len(chrom_markers)),
                    chrom_number,
                    marker.site_key,
                    next_rank,
                ),
            )


def downsample_nested(
    markers: Sequence[Marker], fraction: Decimal, seed: int
) -> tuple[Marker, ...]:
    """Select an exact prefix of the balanced order for a requested fraction."""
    if not Decimal("0") < fraction <= Decimal("1"):
        raise ValueError("fraction must be > 0 and <= 1")
    target = int((Decimal(len(markers)) * fraction).to_integral_value(rounding=ROUND_HALF_UP))
    if markers:
        target = max(1, min(len(markers), target))
    chosen: list[Marker] = []
    for index, marker in enumerate(iter_balanced_nested_order(markers, seed)):
        if index >= target:
            break
        chosen.append(marker)
    return tuple(sorted(chosen, key=lambda marker: (int(marker.chrom), marker.pos, marker.rsid)))


def select_markers_for_job(
    markers: Sequence[Marker],
    fraction: Decimal,
    seed: int,
    dropped_autosomes: frozenset[str],
) -> tuple[tuple[Marker, ...], tuple[Marker, ...]]:
    """Select the shared nested prefix, then remove structured-loss chromosomes."""
    downsampled = downsample_nested(markers, fraction, seed)
    selected = tuple(marker for marker in downsampled if marker.chrom not in dropped_autosomes)
    return downsampled, selected


def count_by_autosome(markers: Iterable[Marker]) -> dict[str, int]:
    counts = Counter(marker.chrom for marker in markers)
    return {chrom: counts.get(chrom, 0) for chrom in AUTOSOMES}


def count_by_source(markers: Iterable[Marker]) -> dict[str, int]:
    counts = Counter(marker.source or "unmerged" for marker in markers)
    return dict(sorted(counts.items()))


def _jsonable(value: object) -> Any:
    """Convert callback dataclasses and common containers to JSON-safe values."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("cannot serialize a non-finite float")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"cannot serialize value of type {type(value).__name__}")


def run_production_diagnostic(
    *,
    markers: Sequence[Marker],
    file_format: str,
    fixture: ValidationFixture,
    bundle_dir: Path,
    work_dir: Path,
    sample_id: int,
    diagnostic_metrics_callback: Callable[[object], None],
) -> object:
    """Run the production LAI wrapper with coverage enforcement disabled.

    This is intentionally the harness's only production-integration adapter.
    The in-memory sample database uses the same SQLAlchemy 2.0 Core tables and
    reads as a real imported sample, while the diagnostic callback preserves
    progressive coverage snapshots even if inference later fails.
    """
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine, is_merged_sample=file_format == "merged_v1")
    with engine.begin() as connection:
        connection.execute(
            sample_metadata_table.insert().values(
                id=1,
                name=f"coverage_calibration_{fixture.iid}",
                file_format=file_format,
                file_hash=fixture.sha256,
            )
        )
        if markers:
            for start in range(0, len(markers), RAW_VARIANT_INSERT_BATCH_SIZE):
                connection.execute(
                    raw_variants.insert(),
                    [
                        marker.as_raw_variant()
                        for marker in markers[start : start + RAW_VARIANT_INSERT_BATCH_SIZE]
                    ],
                )

    # The wrapper resolves these through Settings.  Restore the environment and
    # clear both cached Settings objects around the call so sequential jobs do
    # not leak paths into one another.
    from backend.config import get_settings

    environment = {
        "YELIZTLI_LAI_BUNDLE_PATH": str(bundle_dir),
        "YELIZTLI_DATA_DIR": str(work_dir),
    }
    previous = {key: os.environ.get(key) for key in environment}
    isolated_job_dir = work_dir / "lai_work" / f"sample_{sample_id}"
    os.environ.update(environment)
    get_settings.cache_clear()
    try:
        return run_lai_analysis(
            sample_id=sample_id,
            sample_engine=engine,
            progress_callback=None,
            diagnostic_metrics_callback=diagnostic_metrics_callback,
            allow_below_minimum_for_diagnostics=True,
        )
    finally:
        get_settings.cache_clear()
        for key, old_value in previous.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value
        engine.dispose()
        # Failed production runs do not reach LAIRunner._cleanup.  Delete the
        # entire job-isolated directory here on both success and failure so a
        # large sparse sweep cannot accumulate Beagle/VCF intermediates.  The
        # path contains only this integer sample_id; sibling jobs are untouched.
        if isolated_job_dir.exists():
            shutil.rmtree(isolated_job_dir)


def ancestry_accuracy(
    result: object,
    truth_windows: Sequence[TruthWindow],
) -> dict[str, object]:
    """Score explicit model-window truth, counting unreturned windows as wrong."""
    global_ancestry = getattr(result, "global_ancestry", {})
    if not isinstance(global_ancestry, Mapping):
        raise ValueError("production global ancestry is not a mapping")
    predicted_fractions: dict[str, float] = {}
    for raw_label, details in global_ancestry.items():
        label = str(raw_label)
        if label not in SUPERPOPULATIONS or not isinstance(details, Mapping):
            raise ValueError("production global ancestry has a non-canonical entry")
        try:
            fraction = float(details["fraction"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(
                f"production global ancestry lacks a valid fraction for {label}"
            ) from None
        if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
            raise ValueError(f"production global ancestry has an invalid fraction for {label}")
        predicted_fractions[label] = fraction
    if set(predicted_fractions) != SUPERPOPULATIONS:
        raise ValueError("production global ancestry must contain all canonical labels")
    if not math.isclose(sum(predicted_fractions.values()), 1.0, abs_tol=0.001):
        raise ValueError("production global ancestry fractions do not sum to one")

    truth_by_key = {window.key: window for window in truth_windows}
    if not truth_by_key or len(truth_by_key) != len(truth_windows):
        raise ValueError("local truth is empty or contains duplicate windows")
    predicted_by_key: dict[tuple[str, int, int], tuple[str, str]] = {}
    chromosome_painting = getattr(result, "chromosome_painting", {})
    if not isinstance(chromosome_painting, Mapping):
        raise ValueError("production chromosome painting is not a mapping")
    for raw_chrom, segments in chromosome_painting.items():
        chrom = canonical_autosome(str(raw_chrom))
        if (
            chrom is None
            or not isinstance(segments, Sequence)
            or isinstance(segments, (str, bytes))
        ):
            raise ValueError("production chromosome painting has an invalid chromosome")
        for segment in segments:
            if not isinstance(segment, Mapping):
                raise ValueError("production chromosome painting has a non-object segment")
            try:
                start, end = int(segment["start"]), int(segment["end"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(
                    "production chromosome painting lacks valid window coordinates"
                ) from None
            if start <= 0 or end < start:
                raise ValueError("production chromosome painting has an invalid interval")
            hap0, hap1 = str(segment.get("hap0", "")), str(segment.get("hap1", ""))
            if hap0 not in SUPERPOPULATIONS or hap1 not in SUPERPOPULATIONS:
                raise ValueError(
                    "production chromosome painting has a non-canonical ancestry label"
                )
            key = (chrom, start, end)
            if key in predicted_by_key:
                raise ValueError(f"production chromosome painting duplicates window {key}")
            predicted_by_key[key] = (hap0, hap1)

    expected_windows = len(truth_by_key)
    expected_haplotype_calls = 2 * expected_windows
    unexpected_keys = sorted(
        set(predicted_by_key) - set(truth_by_key),
        key=lambda key: (int(key[0]), key[1], key[2]),
    )
    assigned_keys = set(predicted_by_key) & set(truth_by_key)
    assigned_by_autosome = Counter(key[0] for key in assigned_keys)
    diplotype_correct = 0
    orientation_counts: dict[str, list[int]] = {chrom: [0, 0] for chrom in AUTOSOMES}
    truth_counts: Counter[str] = Counter()
    for key, truth in truth_by_key.items():
        truth_counts.update((truth.hap0, truth.hap1))
        predicted = predicted_by_key.get(key)
        if predicted is None:
            continue
        diplotype_correct += int(sorted(predicted) == sorted((truth.hap0, truth.hap1)))
        orientation_counts[truth.chrom][0] += int(predicted[0] == truth.hap0) + int(
            predicted[1] == truth.hap1
        )
        orientation_counts[truth.chrom][1] += int(predicted[0] == truth.hap1) + int(
            predicted[1] == truth.hap0
        )
    orientation_by_autosome = {
        chrom: 0 if counts[0] >= counts[1] else 1 for chrom, counts in orientation_counts.items()
    }
    confusion: dict[str, Counter[str]] = {label: Counter() for label in SUPERPOPULATIONS}
    assigned_by_truth_class: Counter[str] = Counter()
    correct_by_truth_class: Counter[str] = Counter()
    diplotype_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for key, truth in truth_by_key.items():
        truth_pair = tuple(sorted((truth.hap0, truth.hap1)))
        truth_diplotype = "|".join(truth_pair)
        diplotype_counts[truth_diplotype]["expected"] += 1
        predicted = predicted_by_key.get(key)
        if predicted is None:
            confusion[truth.hap0][MISSING_ANCESTRY_LABEL] += 1
            confusion[truth.hap1][MISSING_ANCESTRY_LABEL] += 1
            continue
        diplotype_counts[truth_diplotype]["assigned"] += 1
        diplotype_counts[truth_diplotype]["correct"] += int(tuple(sorted(predicted)) == truth_pair)
        oriented_prediction = (
            predicted
            if orientation_by_autosome[truth.chrom] == 0
            else (predicted[1], predicted[0])
        )
        for truth_label, predicted_label in zip(
            (truth.hap0, truth.hap1),
            oriented_prediction,
            strict=True,
        ):
            assigned_by_truth_class[truth_label] += 1
            correct_by_truth_class[truth_label] += int(predicted_label == truth_label)
            confusion[truth_label][predicted_label] += 1
    oriented_correct = sum(correct_by_truth_class.values())
    per_truth_class: dict[str, dict[str, object]] = {}
    confusion_payload: dict[str, dict[str, int]] = {}
    confusion_labels = (*sorted(SUPERPOPULATIONS), MISSING_ANCESTRY_LABEL)
    for label in sorted(SUPERPOPULATIONS):
        expected_calls = truth_counts[label]
        assigned_calls = assigned_by_truth_class[label]
        correct_calls = correct_by_truth_class[label]
        per_truth_class[label] = {
            "truth_haplotype_calls_expected": expected_calls,
            "assigned_haplotype_calls": assigned_calls,
            "correct_haplotype_calls_best_orientation": correct_calls,
            "assignment_completeness": (
                assigned_calls / expected_calls if expected_calls else None
            ),
            "local_haplotype_accuracy_best_orientation": (
                correct_calls / expected_calls if expected_calls else None
            ),
        }
        confusion_payload[label] = {
            predicted_label: confusion[label][predicted_label]
            for predicted_label in confusion_labels
        }
        if sum(confusion_payload[label].values()) != expected_calls:
            raise ValueError(f"per-class confusion counts do not reconcile for {label}")
    per_truth_diplotype = {
        diplotype: {
            "windows_expected": counts["expected"],
            "windows_assigned": counts["assigned"],
            "windows_correct": counts["correct"],
            "assignment_completeness": counts["assigned"] / counts["expected"],
            "local_diplotype_accuracy": counts["correct"] / counts["expected"],
        }
        for diplotype, counts in sorted(diplotype_counts.items())
    }
    if sum(truth_counts.values()) != expected_haplotype_calls:
        raise ValueError("per-class truth denominators do not reconcile")
    if sum(assigned_by_truth_class.values()) != 2 * len(assigned_keys):
        raise ValueError("per-class assigned calls do not reconcile")
    truth_fractions = {
        label: truth_counts[label] / expected_haplotype_calls for label in sorted(SUPERPOPULATIONS)
    }
    normalized_predicted = {
        label: predicted_fractions.get(label, 0.0) for label in sorted(SUPERPOPULATIONS)
    }
    total_variation = 0.5 * sum(
        abs(normalized_predicted[label] - truth_fractions[label])
        for label in sorted(SUPERPOPULATIONS)
    )
    return {
        "truth_windows_expected": expected_windows,
        "windows_assigned": len(assigned_keys),
        "windows_assigned_by_autosome": {
            chrom: assigned_by_autosome[chrom] for chrom in AUTOSOMES
        },
        "windows_missing": expected_windows - len(assigned_keys),
        "unexpected_prediction_windows": [
            {"chrom": key[0], "start": key[1], "end": key[2]} for key in unexpected_keys
        ],
        "assignment_completeness": len(assigned_keys) / expected_windows,
        "diplotype_windows_correct": diplotype_correct,
        "local_diplotype_accuracy": diplotype_correct / expected_windows,
        "haplotype_calls_expected": expected_haplotype_calls,
        "haplotype_calls_correct_best_orientation": oriented_correct,
        "local_haplotype_accuracy_best_orientation": (oriented_correct / expected_haplotype_calls),
        "best_orientation_by_autosome": orientation_by_autosome,
        "per_truth_class": per_truth_class,
        "haplotype_confusion_counts": confusion_payload,
        "per_truth_diplotype": per_truth_diplotype,
        "truth_global_ancestry_fractions": truth_fractions,
        "predicted_global_ancestry_fractions": normalized_predicted,
        "global_ancestry_total_variation": total_variation,
    }


def coverage_metrics_calibration_exclusion(
    metrics: object,
    truth_windows: Sequence[TruthWindow],
    expected_input_markers: int | None = None,
    expected_input_markers_by_autosome: Mapping[str, int] | None = None,
    expected_input_markers_by_source: Mapping[str, int] | None = None,
    expected_model_markers_by_autosome: Mapping[str, int] | None = None,
) -> dict[str, object] | None:
    """Validate coverage telemetry using this fixture's local-truth geometry."""
    truth_by_autosome = Counter(window.chrom for window in truth_windows)
    expected_truth_haplotype_windows_by_autosome = {
        chrom: 2 * truth_by_autosome[chrom] for chrom in AUTOSOMES
    }
    return _shared_coverage_metrics_calibration_exclusion(
        metrics,
        expected_truth_haplotype_windows_by_autosome=(
            expected_truth_haplotype_windows_by_autosome
        ),
        expected_input_markers=expected_input_markers,
        expected_input_markers_by_autosome=expected_input_markers_by_autosome,
        expected_input_markers_by_source=expected_input_markers_by_source,
        expected_model_markers_by_autosome=expected_model_markers_by_autosome,
    )


def classify_production_failure(exc: Exception) -> str:
    """Separate reproducible sparse-coverage boundaries from operational errors."""
    message = str(exc)
    expected_fragments = (
        "No genotypes found in sample database",
        ("Insufficient data for local ancestry inference: no usable markers remained"),
        ("Insufficient data for local ancestry inference: no chromosome was successfully phased"),
        (
            "Insufficient data for local ancestry inference: no ancestry-assigned "
            "windows were produced"
        ),
    )
    if isinstance(exc, RuntimeError) and any(
        fragment in message for fragment in expected_fragments
    ):
        return "coverage_failure"
    return "operational_error"


def run_job(
    job: CalibrationJob,
    *,
    dataset_id: str,
    dataset_split: str,
    bundle_dir: Path,
    bundle_metadata_sha256: str,
    bundle_artifact_sha256: str,
    code_revision: str,
    harness_script_sha256: str,
    runtime_environment: Mapping[str, object],
    labels_sha256: str,
    manifests: Mapping[str, SiteManifest],
    configuration_sha256: str,
    work_dir: Path,
    confirmation_policy_provenance_entry: Mapping[str, object] | None = None,
    production_runner: Callable[..., object] = run_production_diagnostic,
) -> dict[str, object]:
    """Execute one matrix cell and always return a structured observation."""
    downsampled, selected = select_markers_for_job(
        job.mask.markers,
        job.fraction,
        job.seed,
        job.drop_scenario.dropped_autosomes,
    )
    selected_by_autosome = count_by_autosome(selected)
    if job.mask.file_format == "merged_v1" or any(marker.source for marker in selected):
        selected_by_source = count_by_source(selected)
    else:
        vendor = (
            job.mask.file_format.split("_", 1)[0].lower() if job.mask.file_format else "unknown"
        )
        selected_by_source = {vendor: len(selected)}
    snapshot_count = 0
    last_progressive_snapshot: object | None = None

    def capture_snapshot(snapshot: object) -> None:
        nonlocal snapshot_count, last_progressive_snapshot
        snapshot_count += 1
        # Snapshots are cumulative and contain all 22 autosomes.  Retaining only
        # the last one preserves the furthest failure state without multiplying
        # a full sweep into gigabytes of redundant JSON.
        last_progressive_snapshot = _jsonable(snapshot)

    manifest_provenance = {
        name: {
            "sha256": manifests[name].sha256,
            "site_rows": manifests[name].site_count,
            "unique_rsids": len(manifests[name].rsids),
            "realized_fixture_overlap": sum(
                marker.rsid in manifests[name].rsids for marker in job.fixture.markers
            ),
        }
        for name in job.mask.manifest_names
    }
    record: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "job_index": job.index,
        "dataset_id": dataset_id,
        "dataset_split": dataset_split,
        "sample": {
            "iid": job.fixture.iid,
            "validation_stratum": job.fixture.validation_stratum,
            "fixture_path": str(job.fixture.path),
            "fixture_parsing": dict(job.fixture.parsing),
            "local_truth_path": str(job.fixture.truth_path),
            "local_truth_windows": len(job.fixture.truth_windows),
            "marker_truth_path": (
                str(job.fixture.marker_truth_path)
                if job.fixture.marker_truth_path is not None
                else None
            ),
            "marker_truth_rows": job.fixture.marker_truth_rows,
        },
        "mask": {
            "name": job.mask.name,
            "kind": job.mask.kind,
            "file_format": job.mask.file_format or None,
            "manifest_provenance": manifest_provenance,
            "realized_fixture_markers": len(job.mask.markers),
            "source_counts": count_by_source(job.mask.markers),
        },
        "chromosome_drop_scenario": {
            "name": job.drop_scenario.name,
            "dropped_autosomes": sorted(job.drop_scenario.dropped_autosomes, key=int),
        },
        "downsampling": {
            "fraction": float(job.fraction),
            "fraction_canonical": str(job.fraction),
            "seed": job.seed,
        },
        "input_coverage": {
            "markers_before_downsampling": len(job.mask.markers),
            "markers_after_downsampling_before_chromosome_drop": len(downsampled),
            "markers_after_chromosome_drop": len(selected),
            "markers_selected": len(selected),
            "selected_by_autosome": selected_by_autosome,
            "selected_by_source": selected_by_source,
            "autosomes_present": sorted({marker.chrom for marker in selected}, key=int),
        },
        "provenance": {
            "bundle_metadata_sha256": bundle_metadata_sha256,
            "bundle_artifact_sha256": bundle_artifact_sha256,
            "code_revision": code_revision,
            "harness_script_sha256": harness_script_sha256,
            "runtime_environment": dict(runtime_environment),
            "fixture_sha256": job.fixture.sha256,
            "local_truth_sha256": job.fixture.truth_sha256,
            "marker_truth_sha256": job.fixture.marker_truth_sha256,
            "labels_sha256": labels_sha256,
            "mask_sha256": {name: manifests[name].sha256 for name in job.mask.manifest_names},
            "configuration_sha256": configuration_sha256,
        },
    }
    if confirmation_policy_provenance_entry is not None:
        provenance = record["provenance"]
        assert isinstance(provenance, dict)
        provenance["confirmation_policy"] = dict(confirmation_policy_provenance_entry)

    try:
        result = production_runner(
            markers=selected,
            file_format=job.mask.file_format,
            fixture=job.fixture,
            bundle_dir=bundle_dir,
            work_dir=work_dir,
            sample_id=job.index + 1,
            diagnostic_metrics_callback=capture_snapshot,
        )
    except Exception as exc:  # classified failures remain explicit observations
        failure_class = classify_production_failure(exc)
        record.update(
            {
                "status": failure_class,
                "coverage_metadata": {
                    "snapshot_count": snapshot_count,
                    "last_progressive_snapshot": last_progressive_snapshot,
                    "production_metadata": None,
                },
                "local_diplotype_accuracy": None,
                "local_haplotype_accuracy_best_orientation": None,
                "assignment_completeness": None,
                "global_ancestry_total_variation": None,
                "accuracy": None,
                "calibration_eligible": False,
                "calibration_exclusion": {
                    "type": failure_class,
                    "message": (
                        "Reproducible sparse-coverage failure; retain as a negative "
                        "boundary observation."
                        if failure_class == "coverage_failure"
                        else "Operational failure; rerun before analyzing the matrix."
                    ),
                },
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            }
        )
        return record

    try:
        production_metadata = _jsonable(getattr(result, "metadata", {}))
        if not isinstance(production_metadata, Mapping):
            raise TypeError("production metadata must be a JSON object")
    except Exception as exc:
        record.update(
            {
                "status": "operational_error",
                "coverage_metadata": {
                    "snapshot_count": snapshot_count,
                    "last_progressive_snapshot": last_progressive_snapshot,
                    "production_metadata": None,
                },
                "local_diplotype_accuracy": None,
                "local_haplotype_accuracy_best_orientation": None,
                "assignment_completeness": None,
                "global_ancestry_total_variation": None,
                "accuracy": None,
                "calibration_eligible": False,
                "calibration_exclusion": {
                    "type": "operational_error",
                    "message": "Production output could not be normalized; rerun required.",
                },
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        )
        return record
    final_coverage_metrics = production_metadata.get("lai_coverage_metrics")
    calibration_exclusion = coverage_metrics_calibration_exclusion(
        final_coverage_metrics,
        job.fixture.truth_windows,
        expected_input_markers=len(selected),
        expected_input_markers_by_autosome=selected_by_autosome,
        expected_input_markers_by_source=selected_by_source,
        expected_model_markers_by_autosome=(job.fixture.model_marker_counts_by_autosome or None),
    )
    try:
        accuracy = ancestry_accuracy(result, job.fixture.truth_windows)
    except (TypeError, ValueError) as exc:
        accuracy = None
        calibration_exclusion = calibration_exclusion or {
            "type": "invalid_local_ancestry_output",
            "message": str(exc),
        }
    if accuracy is not None and accuracy["unexpected_prediction_windows"]:
        calibration_exclusion = calibration_exclusion or {
            "type": "unexpected_prediction_windows",
            "message": "Production returned windows absent from explicit local truth.",
            "windows": accuracy["unexpected_prediction_windows"],
        }
    if accuracy is not None and isinstance(final_coverage_metrics, Mapping):
        window_metrics = final_coverage_metrics.get("haplotype_windows")
        if isinstance(window_metrics, Mapping):
            expected_valid = 2 * int(accuracy["windows_assigned"])
            if window_metrics.get("valid_assigned") != expected_valid:
                calibration_exclusion = calibration_exclusion or {
                    "type": "painting_coverage_mismatch",
                    "message": (
                        "Coverage telemetry and returned chromosome painting disagree "
                        "on assigned haplotype windows."
                    ),
                    "telemetry_valid_assigned": window_metrics.get("valid_assigned"),
                    "painting_valid_assigned": expected_valid,
                }
            expected_valid_by = {
                chrom: 2 * int(accuracy["windows_assigned_by_autosome"][chrom])
                for chrom in AUTOSOMES
            }
            if window_metrics.get("valid_assigned_by_autosome") != expected_valid_by:
                calibration_exclusion = calibration_exclusion or {
                    "type": "painting_coverage_mismatch",
                    "message": (
                        "Per-autosome coverage telemetry and returned chromosome "
                        "painting disagree on assigned haplotype windows."
                    ),
                    "telemetry_valid_assigned_by_autosome": window_metrics.get(
                        "valid_assigned_by_autosome"
                    ),
                    "painting_valid_assigned_by_autosome": expected_valid_by,
                }
    record.update(
        {
            "status": "ok" if calibration_exclusion is None else "invalid",
            "coverage_metadata": {
                "snapshot_count": snapshot_count,
                "last_progressive_snapshot": last_progressive_snapshot,
                "production_metadata": production_metadata,
                "final_lai_coverage_metrics": final_coverage_metrics,
            },
            "local_diplotype_accuracy": (
                accuracy["local_diplotype_accuracy"] if accuracy is not None else None
            ),
            "local_haplotype_accuracy_best_orientation": (
                accuracy["local_haplotype_accuracy_best_orientation"]
                if accuracy is not None
                else None
            ),
            "assignment_completeness": (
                accuracy["assignment_completeness"] if accuracy is not None else None
            ),
            "global_ancestry_total_variation": (
                accuracy["global_ancestry_total_variation"] if accuracy is not None else None
            ),
            "accuracy": accuracy,
            "calibration_eligible": calibration_exclusion is None,
            "calibration_exclusion": calibration_exclusion,
            "error": None,
        }
    )
    return record


def _input_fingerprint(path: Path, expected_sha256: str) -> dict[str, object]:
    """Re-hash once at plan freeze and bind the digest to stable live stats."""
    return _stable_file_verification(path, expected_sha256)


def build_input_verification(
    fixtures: Sequence[ValidationFixture],
    manifests: Mapping[str, SiteManifest],
    identifier_manifests: Mapping[str, IdentifierManifest] | None = None,
    confirmation_policy: ConfirmationPolicy | None = None,
    selection_design: SelectionDesignArtifact | None = None,
) -> dict[str, object]:
    """Bind planner-validated fixture/truth/mask bytes to live stat fingerprints."""
    fixture_entries: dict[str, dict[str, object]] = {}
    for fixture in sorted(fixtures, key=lambda item: item.iid):
        if fixture.marker_truth_path is None or fixture.tract_truth_path is None:
            raise ValueError(f"fixture {fixture.iid!r} has no validated marker/tract truth")
        fixture_entries[fixture.iid] = {
            "fixture": _input_fingerprint(fixture.path, fixture.sha256),
            "local_truth": _input_fingerprint(
                fixture.truth_path,
                fixture.truth_sha256,
            ),
            "marker_truth": _input_fingerprint(
                fixture.marker_truth_path,
                fixture.marker_truth_sha256,
            ),
            "tract_truth": _input_fingerprint(
                fixture.tract_truth_path,
                fixture.tract_truth_sha256,
            ),
        }
    verification = {
        "fixtures": fixture_entries,
        "site_masks": {
            name: _input_fingerprint(manifest.path, manifest.sha256)
            for name, manifest in sorted(manifests.items())
        },
        "identifier_manifests": {
            name: _input_fingerprint(manifest.path, manifest.sha256)
            for name, manifest in sorted((identifier_manifests or {}).items())
        },
    }
    if confirmation_policy is not None:
        verification["confirmation_policy"] = _input_fingerprint(
            confirmation_policy.path,
            confirmation_policy.sha256,
        )
    if selection_design is not None:
        verification["selection_design"] = dict(selection_design.fingerprint)
    return verification


def validate_live_input_fingerprint(
    path: Path,
    raw_entry: object,
    *,
    expected_sha256: object,
    description: str,
) -> None:
    """Reject a planner-validated input whose live bytes may have changed."""
    if not isinstance(raw_entry, Mapping) or raw_entry.get("sha256") != expected_sha256:
        raise ValueError(f"job plan fingerprint is invalid for {description}")
    try:
        live_stat = path.lstat()
    except FileNotFoundError:
        raise ValueError(f"planned input is missing for {description}: {path}")
    if not stat.S_ISREG(live_stat.st_mode):
        raise ValueError(f"planned input is not a regular file: {description}")
    observed = {
        "device": live_stat.st_dev,
        "inode": live_stat.st_ino,
        "size_bytes": live_stat.st_size,
        "mtime_ns": live_stat.st_mtime_ns,
        "ctime_ns": live_stat.st_ctime_ns,
    }
    expected = {field: raw_entry.get(field) for field in observed}
    if observed != expected:
        raise ValueError(f"planned input changed after validation: {description}")


def _spec_paths(
    raw_specs: Sequence[tuple[str, Path]],
    option: str,
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for iid, path in raw_specs:
        if iid in paths:
            raise ValueError(f"duplicate {option} IID {iid!r}")
        paths[iid] = lexical_absolute_path(path)
    return paths


def validate_destination(
    destination: Path,
    *,
    role: str,
    inputs: Iterable[Path],
    forbidden_directories: Iterable[Path] = (),
) -> Path:
    """Reject symlink, pathname, hardlink, and protected-tree output aliases."""
    destination = destination.expanduser()
    resolved_parent = destination.parent.resolve()
    normalized = resolved_parent / destination.name
    if destination.is_symlink():
        raise ValueError(f"{role} destination must not be a symlink: {destination}")
    if normalized.exists():
        observed = normalized.lstat()
        if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
            raise ValueError(f"{role} destination must be a regular file: {normalized}")
    for forbidden in forbidden_directories:
        forbidden_resolved = forbidden.expanduser().resolve()
        if normalized == forbidden_resolved or normalized.is_relative_to(forbidden_resolved):
            raise ValueError(f"{role} destination is inside a protected directory")
    for raw_input in inputs:
        input_path = raw_input.expanduser()
        try:
            input_resolved = input_path.resolve(strict=True)
        except FileNotFoundError:
            continue
        if normalized == input_resolved:
            raise ValueError(f"{role} destination aliases an authenticated input")
        if normalized.exists():
            try:
                if os.path.samefile(normalized, input_path):
                    raise ValueError(f"{role} destination hardlinks an authenticated input")
            except FileNotFoundError:
                pass
    return normalized


@dataclass(slots=True)
class ResultOutputLock:
    """A lock and publication authority pinned to one directory inode."""

    handle: Any
    directory_fd: int
    parent_device: int
    parent_inode: int
    output_path: Path

    def fileno(self) -> int:
        return int(self.handle.fileno())

    def close(self) -> None:
        try:
            self.handle.close()
        finally:
            os.close(self.directory_fd)


def _assert_pinned_output_parent(authority: ResultOutputLock) -> None:
    """Reject a destination parent that was renamed or replaced mid-run."""
    descriptor_metadata = os.fstat(authority.directory_fd)
    try:
        path_metadata = authority.output_path.parent.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ValueError("result output parent disappeared during publication") from exc
    expected = (authority.parent_device, authority.parent_inode)
    if (descriptor_metadata.st_dev, descriptor_metadata.st_ino) != expected or (
        path_metadata.st_dev,
        path_metadata.st_ino,
    ) != expected:
        raise ValueError("result output parent changed during publication")


def acquire_result_output_lock(
    output: Path,
    *,
    job_index: int,
    configuration_sha256: str,
    inputs: Iterable[Path],
    forbidden_directories: Iterable[Path],
    overwrite: bool,
) -> tuple[Path, ResultOutputLock]:
    """Lock one output identity and reject cross-job last-writer-wins."""
    normalized = validate_destination(
        output,
        role="result output",
        inputs=inputs,
        forbidden_directories=forbidden_directories,
    )
    normalized.parent.mkdir(parents=True, exist_ok=True)
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        directory_fd = os.open(normalized.parent, directory_flags)
    except OSError as exc:
        raise ValueError(f"result output parent is unsafe: {normalized.parent}: {exc}") from exc
    parent_metadata = os.fstat(directory_fd)
    lock_name = f".{normalized.name}.lock"
    lock_flags = (
        os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        lock_fd = os.open(lock_name, lock_flags, 0o600, dir_fd=directory_fd)
    except OSError as exc:
        os.close(directory_fd)
        raise ValueError(
            f"result lock path is unsafe: {normalized.parent / lock_name}: {exc}"
        ) from exc
    lock_metadata = os.fstat(lock_fd)
    if not stat.S_ISREG(lock_metadata.st_mode) or lock_metadata.st_nlink != 1:
        os.close(lock_fd)
        os.close(directory_fd)
        raise ValueError(
            f"result lock path is not a private regular file: {normalized.parent / lock_name}"
        )
    lock_handle = os.fdopen(lock_fd, "a+b", closefd=True)
    authority = ResultOutputLock(
        handle=lock_handle,
        directory_fd=directory_fd,
        parent_device=parent_metadata.st_dev,
        parent_inode=parent_metadata.st_ino,
        output_path=normalized,
    )
    try:
        try:
            fcntl.flock(authority.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(f"result output is already locked by another task: {normalized}")
        _assert_pinned_output_parent(authority)
        if normalized.exists() and not overwrite:
            try:
                lines, _snapshot = stable_read(
                    normalized,
                    lambda path: path.read_text(encoding="utf-8").splitlines(),
                )
                existing = json.loads(lines[0]) if len(lines) == 1 else None
            except (OSError, UnicodeError, json.JSONDecodeError):
                existing = None
            existing_provenance = (
                existing.get("provenance") if isinstance(existing, Mapping) else None
            )
            if not (
                isinstance(existing, Mapping)
                and existing.get("job_index") == job_index
                and isinstance(existing_provenance, Mapping)
                and existing_provenance.get("configuration_sha256") == configuration_sha256
            ):
                raise ValueError(
                    "existing result output belongs to a different or malformed job; "
                    "use --overwrite-output only after review"
                )
        return normalized, authority
    except Exception:
        authority.close()
        raise


def _atomic_publish_bytes(
    path: Path,
    chunks: Iterable[bytes],
    *,
    authority: ResultOutputLock | None = None,
) -> None:
    """Publish bytes through a directory descriptor pinned across the rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    owns_directory_fd = authority is None
    if authority is None:
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        directory_fd = os.open(path.parent, directory_flags)
        parent_metadata = os.fstat(directory_fd)
        authority = ResultOutputLock(
            handle=None,
            directory_fd=directory_fd,
            parent_device=parent_metadata.st_dev,
            parent_inode=parent_metadata.st_ino,
            output_path=path,
        )
    elif authority.output_path != path:
        raise ValueError("result output authority does not match publication path")
    temporary_name = f".{path.name}.{secrets.token_hex(16)}.tmp"
    output_published = False
    temporary_created = False
    try:
        _assert_pinned_output_parent(authority)
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        temporary_fd = os.open(temporary_name, flags, 0o600, dir_fd=authority.directory_fd)
        temporary_created = True
        with os.fdopen(temporary_fd, "wb", closefd=True) as handle:
            for chunk in chunks:
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        _assert_pinned_output_parent(authority)
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=authority.directory_fd,
            dst_dir_fd=authority.directory_fd,
        )
        temporary_created = False
        output_published = True
        os.fsync(authority.directory_fd)
        _assert_pinned_output_parent(authority)
    except Exception:
        if output_published:
            try:
                os.unlink(path.name, dir_fd=authority.directory_fd)
                os.fsync(authority.directory_fd)
            except FileNotFoundError:
                pass
        raise
    finally:
        if temporary_created:
            try:
                os.unlink(temporary_name, dir_fd=authority.directory_fd)
            except FileNotFoundError:
                pass
        if owns_directory_fd:
            os.close(authority.directory_fd)


def atomic_write_jsonl(
    path: Path,
    records: Iterable[Mapping[str, object]],
    *,
    authority: ResultOutputLock | None = None,
) -> None:
    """Write complete JSONL output atomically within the destination directory."""

    def serialized_records() -> Iterator[bytes]:
        for record in records:
            yield (
                json.dumps(
                    record,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                + b"\n"
            )

    _atomic_publish_bytes(path, serialized_records(), authority=authority)


def atomic_write_json(
    path: Path,
    value: Mapping[str, object],
    *,
    authority: ResultOutputLock | None = None,
) -> None:
    """Write one canonical JSON object atomically."""
    _atomic_publish_bytes(path, (canonical_json_bytes(value),), authority=authority)


def _fsync_directory(path: Path) -> None:
    """Persist a preceding atomic rename in its containing directory."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def parse_fixture_spec(raw: str) -> tuple[str, Path]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError("fixture must be IID=PATH")
    iid, raw_path = raw.split("=", 1)
    if not iid.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("fixture must have non-empty IID and PATH")
    return iid.strip(), Path(raw_path).expanduser()


def lexical_absolute_path(path: Path) -> Path:
    """Make a CLI path absolute while rejecting every existing symlink component."""
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


def parse_sha256(raw: str) -> str:
    value = raw.strip().lower()
    if not is_sha256(value):
        raise argparse.ArgumentTypeError("expected a complete 64-character SHA-256 digest")
    return value


def parse_git_revision(raw: str) -> str:
    value = raw.strip().lower()
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise argparse.ArgumentTypeError("expected a complete 40-character Git revision")
    return value


def verify_runtime_code_revision(expected_revision: str) -> None:
    """Bind imported backend code and dependency locks to a real Git revision."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("runtime must execute from a Git checkout") from exc
    observed = completed.stdout.strip().lower()
    if observed != expected_revision:
        raise ValueError(
            f"--code-revision {expected_revision} does not match checkout HEAD {observed}"
        )
    pinned_paths = (
        "backend",
        "pyproject.toml",
        "uv.lock",
        "scripts/lai_bundle_v2/06g_calibrate_coverage.py",
        "scripts/lai_bundle_v2/06g_verify_simulation.py",
        "scripts/lai_bundle_v2/06h_select_coverage.py",
        "scripts/lai_bundle_v2/lai_coverage_metrics.py",
        "scripts/lai_bundle_v2/lai_coverage_plan.py",
        "scripts/lai_bundle_v2/lai_coverage_policy.py",
    )
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", *pinned_paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if tracked.returncode != 0:
        raise ValueError("runtime code and dependency paths must be tracked by Git")
    status = subprocess.run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            *pinned_paths,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode != 0:
        raise ValueError("unable to verify runtime code/dependency Git state")
    if status.stdout.strip():
        raise ValueError("runtime code/dependency files differ from --code-revision")


def runtime_environment_fingerprint() -> dict[str, object]:
    """Pin the resolved Python lock, imported runtimes, and JVM identity."""
    lock_snapshot = _stable_file_snapshot(REPO_ROOT / "uv.lock")
    packages = {
        name: importlib.metadata.version(name)
        for name in ("numpy", "SQLAlchemy", "pysam", "xgboost")
    }
    java = subprocess.run(
        ["java", "-version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if java.returncode != 0:
        raise ValueError("unable to identify the Java runtime")
    java_identity = (java.stderr or java.stdout).strip()
    if not java_identity:
        raise ValueError("Java runtime identity is empty")
    return {
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "packages": packages,
        "java_version_output": java_identity,
        "uv_lock": {
            "sha256": str(lock_snapshot["sha256"]),
            "size_bytes": int(lock_snapshot["size_bytes"]),
        },
    }


def build_configuration(
    *,
    dataset_id: str,
    dataset_split: str,
    simulation_manifest: SimulationManifest,
    simulation_verification: SimulationVerificationReport,
    bundle_metadata_path: Path,
    bundle_metadata_sha256: str,
    bundle_metadata_summary: Mapping[str, object],
    bundle_artifact_sha256: str,
    code_revision: str,
    harness_script_sha256: str,
    runtime_environment: Mapping[str, object],
    labels_path: Path,
    labels_sha256: str,
    donor_manifest_path: Path,
    donor_manifest_sha256: str,
    donor_iids: frozenset[str],
    donor_metadata_path: Path,
    donor_metadata_sha256: str,
    donor_labels: Mapping[str, str],
    isolation_manifest_path: Path,
    isolation_manifest_sha256: str,
    isolation_iids: frozenset[str],
    relationship_summary: Mapping[str, object],
    training_samples_path: Path,
    training_samples_sha256: str,
    training_iids: frozenset[str],
    reference_manifest: CalibrationReferenceManifest,
    reference_verification: ReferenceVerification,
    reference_runtime_summary: Mapping[str, Mapping[str, object]],
    evaluation_coverage_summary: Mapping[str, object],
    fixtures: Sequence[ValidationFixture],
    manifests: Mapping[str, SiteManifest],
    vep_membership: IdentifierManifest,
    masks_by_iid: Mapping[str, Sequence[MaskScenario]],
    drop_scenarios: Sequence[ChromosomeDropScenario],
    fractions: Sequence[Decimal],
    seeds: Sequence[int],
    confirmation_policy: ConfirmationPolicy | None = None,
    selection_design: SelectionDesignArtifact | None = None,
) -> dict[str, object]:
    """Build the canonical configuration authenticated by each lightweight row."""
    realized_overlap: dict[str, dict[str, int]] = {}
    for name, manifest in sorted(manifests.items()):
        realized_overlap[name] = {
            fixture.iid: sum(marker.rsid in manifest.rsids for marker in fixture.markers)
            for fixture in sorted(fixtures, key=lambda item: item.iid)
        }
    configuration: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "dataset_split": dataset_split,
        "inputs": {
            "simulation_manifest": {
                "filename": simulation_manifest.path.name,
                "sha256": simulation_manifest.sha256,
                "source_bundle_artifact_sha256": (
                    simulation_manifest.source_bundle_artifact_sha256
                ),
                "generator": dict(simulation_manifest.generator),
                "simulation_protocol": dict(simulation_manifest.simulation_protocol),
                "donor_haplotype_source_sha256": sha256_json(
                    simulation_manifest.donor_haplotype_source
                ),
                "models_sha256": sha256_json(simulation_manifest.models),
                "truth_projection": dict(simulation_manifest.truth_projection),
                "split_sizes": {
                    split: len(iids) for split, iids in sorted(simulation_manifest.splits.items())
                },
            },
            "simulation_verification": {
                "filename": simulation_verification.path.name,
                "sha256": simulation_verification.sha256,
                "verifier": dict(simulation_verification.verifier),
                "marker_rows_verified": simulation_verification.marker_rows_verified,
                "contract": "independent_donor_vcf_fixture_allele_replay_v1",
            },
            "bundle_metadata": {
                "filename": bundle_metadata_path.name,
                "sha256": bundle_metadata_sha256,
                "summary": dict(bundle_metadata_summary),
            },
            "bundle_artifact_sha256": bundle_artifact_sha256,
            "code_revision": code_revision,
            "harness_script_sha256": harness_script_sha256,
            "runtime_environment": dict(runtime_environment),
            "labels": {
                "filename": labels_path.name,
                "sha256": labels_sha256,
            },
            "validation_donors": {
                "filename": donor_manifest_path.name,
                "sha256": donor_manifest_sha256,
                "count": len(donor_iids),
                "iids_sha256": sha256_json(sorted(donor_iids)),
            },
            "donor_metadata": {
                "filename": donor_metadata_path.name,
                "sha256": donor_metadata_sha256,
                "source_url": simulation_manifest.donor_metadata["source_url"],
                "iid_field": simulation_manifest.donor_metadata["iid_field"],
                "population_field": simulation_manifest.donor_metadata["population_field"],
                "model_class_field": simulation_manifest.donor_metadata["model_class_field"],
                "model_class_counts": dict(sorted(Counter(donor_labels.values()).items())),
            },
            "validation_isolation_samples": {
                "filename": isolation_manifest_path.name,
                "sha256": isolation_manifest_sha256,
                "count": len(isolation_iids),
                "iids_sha256": sha256_json(sorted(isolation_iids)),
            },
            "validation_relationships": dict(relationship_summary),
            "gnomix_training_samples": {
                "filename": training_samples_path.name,
                "sha256": training_samples_sha256,
                "count": len(training_iids),
                "iids_sha256": sha256_json(sorted(training_iids)),
            },
            "calibration_reference": {
                "filename": reference_manifest.path.name,
                "manifest_sha256": reference_manifest.sha256,
                "verification_stamp_filename": reference_verification.path.name,
                "verification_stamp_sha256": reference_verification.sha256,
                "source_bundle_artifact_sha256": (
                    reference_manifest.source_bundle_artifact_sha256
                ),
                "excluded_iids_sha256": sha256_json(sorted(reference_manifest.excluded_iids)),
                "excluded_iids_count": len(reference_manifest.excluded_iids),
                "inherited_tree_sha256": reference_manifest.inherited_tree_sha256,
                "inherited_file_count": len(reference_manifest.inherited_files),
                "resolved_liftover_file": reference_manifest.resolved_liftover_file,
                "runtime_validation": "vcf_header_sample_set_against_manifest_v1",
                "phasing_panel": {
                    chrom: dict(reference_runtime_summary[chrom]) for chrom in AUTOSOMES
                },
            },
            "fixtures": {
                fixture.iid: {
                    "filename": fixture.path.name,
                    "sha256": fixture.sha256,
                    "validation_stratum": fixture.validation_stratum,
                    "autosomal_markers": len(fixture.markers),
                    "local_truth_filename": fixture.truth_path.name,
                    "local_truth_sha256": fixture.truth_sha256,
                    "local_truth_windows": len(fixture.truth_windows),
                    "local_truth_windows_by_autosome": {
                        chrom: sum(window.chrom == chrom for window in fixture.truth_windows)
                        for chrom in AUTOSOMES
                    },
                    "marker_truth_filename": (
                        fixture.marker_truth_path.name
                        if fixture.marker_truth_path is not None
                        else None
                    ),
                    "marker_truth_sha256": fixture.marker_truth_sha256,
                    "marker_truth_rows": fixture.marker_truth_rows,
                    "truth_donor_iids_sha256": sha256_json(sorted(fixture.truth_donor_iids)),
                    "tract_truth_filename": (
                        fixture.tract_truth_path.name
                        if fixture.tract_truth_path is not None
                        else None
                    ),
                    "tract_truth_sha256": fixture.tract_truth_sha256,
                    "tract_count": fixture.tract_count,
                    "tract_summary": dict(fixture.tract_summary),
                    "model_marker_counts_by_autosome": dict(
                        fixture.model_marker_counts_by_autosome
                    ),
                    "modal_truth_founders_by_class": {
                        label: sorted(fixture.truth_founders_by_class.get(label, frozenset()))
                        for label in sorted(SUPERPOPULATIONS)
                    },
                }
                for fixture in sorted(fixtures, key=lambda item: item.iid)
            },
            "evaluation_coverage": dict(evaluation_coverage_summary),
            "privacy_safe_site_masks": {
                name: {
                    "filename": manifest.path.name,
                    "sha256": manifest.sha256,
                    "site_rows": manifest.site_count,
                    "unique_rsids": len(manifest.rsids),
                    "realized_fixture_overlap": realized_overlap[name],
                }
                for name, manifest in sorted(manifests.items())
            },
            "vep_rsid_membership": {
                "filename": vep_membership.path.name,
                "sha256": vep_membership.sha256,
                "identifier_count": vep_membership.identifier_count,
                "purpose": "production-equivalent merged-alias resolution",
            },
        },
        "mask_scenarios": {
            iid: [
                {
                    "name": mask.name,
                    "kind": mask.kind,
                    "file_format": mask.file_format or None,
                    "realized_fixture_markers": len(mask.markers),
                    "manifest_names": list(mask.manifest_names),
                }
                for mask in masks_by_iid[iid]
            ]
            for iid in sorted(masks_by_iid)
        },
        "merged_source_semantics": {
            "S1": "present in 23andMe v5 derived site mask only",
            "S2": "present in AncestryDNA v2 empirical site mask only",
            "both": (
                "GRCh37 coordinate present in both masks; conflicting aliases use "
                "the pinned production VEP-membership tiebreaker, then S1 fallback"
            ),
        },
        "chromosome_drop_scenarios": [
            {
                "name": scenario.name,
                "dropped_autosomes": sorted(scenario.dropped_autosomes, key=int),
            }
            for scenario in drop_scenarios
        ],
        "fractions": [str(fraction) for fraction in fractions],
        "seeds": list(seeds),
        "sampling_algorithm": "sha256_within_chromosome_balanced_nested_prefix_v1",
        "mask_join_key": "rsid (site-mask GRCh37 coordinates; fixture GRCh38 coordinates)",
        "coverage_enforcement": "disabled_for_diagnostics",
        "threshold_selected": None,
    }
    if confirmation_policy is not None:
        inputs = configuration["inputs"]
        assert isinstance(inputs, dict)
        inputs["confirmation_policy"] = confirmation_policy_provenance(confirmation_policy)
        configuration["threshold_selected"] = confirmation_policy.policy_id
    if selection_design is not None:
        inputs = configuration["inputs"]
        assert isinstance(inputs, dict)
        inputs["selection_design"] = {
            "filename": selection_design.path.name,
            "sha256": selection_design.sha256,
        }
    return configuration


def _required_mapping(value: object, description: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"job plan has invalid {description}")
    return value


def require_job_plan_inode_capacity(path: Path, row_count: int) -> None:
    """Fail before plan construction when one shard per job cannot be allocated."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    filesystem = os.statvfs(parent)
    required = row_count + JOB_PLAN_INODE_RESERVE
    if filesystem.f_favail < required:
        raise OSError(
            f"insufficient free inodes for job plan: need at least {required}, "
            f"have {filesystem.f_favail}"
        )


def prepare_private_work_root(path: Path) -> Path:
    """Create or validate an operator-owned, non-symlinked 0700 scratch root."""
    work_root = Path(os.path.abspath(path.expanduser()))
    if work_root.exists() or work_root.is_symlink():
        metadata = work_root.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise ValueError(
                f"--work-dir must be an operator-owned, non-symlinked 0700 directory: {work_root}"
            )
    else:
        work_root.mkdir(mode=0o700, parents=True)
    return work_root


def acquire_bundle_lock(bundle_dir: Path, *, exclusive: bool) -> Any:
    """Hold the cooperative bundle lock across verification or inference."""
    lock_path = bundle_dir / ".yeliztli-calibration.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    handle = os.fdopen(fd, "a+b", closefd=True)
    try:
        fcntl.flock(
            handle.fileno(),
            (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB,
        )
    except BlockingIOError:
        handle.close()
        mode = "verification" if exclusive else "inference"
        raise ValueError(f"calibration bundle is locked against {mode}") from None
    return handle


def run_from_job_plan(args: argparse.Namespace) -> int:
    """Hydrate only the selected sample/mask inputs from an authenticated plan."""
    ignored_matrix_options = {
        "--simulation-manifest": args.simulation_manifest,
        "--simulation-verification-report": args.simulation_verification_report,
        "--validation-donors": args.validation_donors,
        "--donor-metadata": args.donor_metadata,
        "--validation-isolation-samples": args.validation_isolation_samples,
        "--validation-relationships": args.validation_relationships,
        "--gnomix-training-samples": args.gnomix_training_samples,
        "--fraction": args.fraction,
        "--seed": args.seed,
        "--drop-scenario": args.drop_scenario or None,
        "--max-jobs": args.max_jobs,
        "--donor-vcf": args.donor_vcf or None,
        "--donor-vcf-index": args.donor_vcf_index or None,
        "--simulation-generator-script": args.simulation_generator_script,
        "--simulation-generator-environment-lock": (args.simulation_generator_environment_lock),
    }
    supplied_ignored = [
        name for name, value in ignored_matrix_options.items() if value is not None
    ]
    if supplied_ignored:
        raise ValueError(
            "planned inference rejects matrix-definition option(s): " + ", ".join(supplied_ignored)
        )
    if args.job_plan is None or args.expected_configuration_sha256 is None:
        raise ValueError(
            "--job-plan and --expected-configuration-sha256 are required for inference"
        )
    if args.job_index is None:
        raise ValueError("planned inference requires --job-index")
    job_plan_path = lexical_absolute_path(args.job_plan)
    configuration, selected_row, input_verification = lai_coverage_plan.read_job_plan(
        job_plan_path,
        args.expected_configuration_sha256,
        args.job_index,
    )
    if configuration.get("dataset_id") != args.dataset_id:
        raise ValueError("job plan dataset_id does not match --dataset-id")
    if configuration.get("dataset_split") != args.dataset_split:
        raise ValueError("job plan dataset split does not match --dataset-split")
    selected_rows = (selected_row,)
    iid = str(selected_row["iid"])

    inputs = _required_mapping(configuration.get("inputs"), "inputs")
    if inputs.get("bundle_artifact_sha256") != args.bundle_artifact_sha256:
        raise ValueError("job plan bundle artifact SHA-256 does not match CLI")
    if inputs.get("code_revision") != args.code_revision:
        raise ValueError("job plan code revision does not match CLI")
    confirmation_policy_path: Path | None = None
    policy_provenance: Mapping[str, object] | None = None
    policy_fingerprint: object = None
    if args.dataset_split == "final_confirmation":
        if args.confirmation_policy is None:
            raise ValueError("final_confirmation inference requires --confirmation-policy")
        if args.expected_confirmation_policy_sha256 is None:
            raise ValueError(
                "final_confirmation inference requires --expected-confirmation-policy-sha256"
            )
        confirmation_policy_path = lexical_absolute_path(args.confirmation_policy)
        planned_policy = _required_mapping(
            inputs.get("confirmation_policy"),
            "confirmation-policy provenance",
        )
        simulation_config = _required_mapping(
            inputs.get("simulation_manifest"),
            "simulation-manifest provenance",
        )
        policy_fingerprint = input_verification.get("confirmation_policy")
        validate_live_input_fingerprint(
            confirmation_policy_path,
            policy_fingerprint,
            expected_sha256=planned_policy.get("sha256"),
            description="confirmation policy",
        )
        live_policy = read_confirmation_policy(
            confirmation_policy_path,
            dataset_id=args.dataset_id,
            bundle_artifact_sha256=args.bundle_artifact_sha256,
            simulation_manifest_sha256=str(simulation_config.get("sha256")),
            code_revision=args.code_revision,
            final_confirmation_split_commitment_sha256=str(
                planned_policy.get("confirmation_commitment")
            ),
            expected_confirmation_policy_sha256=args.expected_confirmation_policy_sha256,
        )
        live_policy_provenance = confirmation_policy_provenance(live_policy)
        if live_policy_provenance != dict(planned_policy):
            raise ValueError("live confirmation policy differs from the job plan")
        validate_live_input_fingerprint(
            confirmation_policy_path,
            policy_fingerprint,
            expected_sha256=planned_policy.get("sha256"),
            description="confirmation policy",
        )
        policy_provenance = planned_policy
    elif "confirmation_policy" in inputs or "confirmation_policy" in input_verification:
        raise ValueError("calibration job plan must not contain a confirmation policy")
    verify_runtime_code_revision(args.code_revision)
    live_runtime_environment = runtime_environment_fingerprint()
    if inputs.get("runtime_environment") != live_runtime_environment:
        raise ValueError("live Python/Java dependency environment differs from the job plan")
    harness_script_sha256 = str(_stable_file_snapshot(Path(__file__).resolve())["sha256"])
    if inputs.get("harness_script_sha256") != harness_script_sha256:
        raise ValueError("live harness script differs from the job plan")

    bundle_dir = lexical_absolute_path(args.bundle_dir)
    bundle_lock = acquire_bundle_lock(bundle_dir, exclusive=False)
    bundle_metadata_path = bundle_dir / "metadata.json"
    bundle_metadata = _required_mapping(
        inputs.get("bundle_metadata"),
        "bundle metadata provenance",
    )
    bundle_metadata_sha256 = str(_stable_file_snapshot(bundle_metadata_path)["sha256"])
    if bundle_metadata.get("sha256") != bundle_metadata_sha256:
        raise ValueError("live bundle metadata differs from the job plan")
    labels_path = lexical_absolute_path(args.labels)
    labels = _required_mapping(inputs.get("labels"), "labels provenance")
    labels_sha256 = str(_stable_file_snapshot(labels_path)["sha256"])
    if labels.get("sha256") != labels_sha256:
        raise ValueError("live validation labels differ from the job plan")

    reference_manifest_path = lexical_absolute_path(args.calibration_reference_manifest)
    reference_manifest, reference_manifest_snapshot = stable_read(
        reference_manifest_path,
        lambda path: read_calibration_reference_manifest(
            path,
            args.bundle_artifact_sha256,
        ),
    )
    if reference_manifest.sha256 != reference_manifest_snapshot["sha256"]:
        raise ValueError("calibration-reference manifest changed while it was parsed")
    reference_config = _required_mapping(
        inputs.get("calibration_reference"),
        "calibration reference provenance",
    )
    if reference_config.get("manifest_sha256") != reference_manifest.sha256:
        raise ValueError("live calibration-reference manifest differs from the job plan")
    verification_path = lexical_absolute_path(args.reference_verification_stamp)
    reference_verification, reference_verification_snapshot = stable_read(
        verification_path,
        lambda path: read_reference_verification(path, reference_manifest),
    )
    if reference_verification.sha256 != reference_verification_snapshot["sha256"]:
        raise ValueError("reference-verification stamp changed while it was parsed")
    if reference_config.get("verification_stamp_sha256") != reference_verification.sha256:
        raise ValueError("live reference-verification stamp differs from the job plan")
    # This is intentionally the cheap stat-fingerprint pass.  The planner already
    # checked all 22 VCF sample headers and wrote their exact sample-set hashes.
    validate_reference_verification(
        bundle_dir=bundle_dir,
        reference_manifest=reference_manifest,
        payload=reference_verification.payload,
    )

    fixture_paths = _spec_paths(args.fixture, "--fixture")
    local_truth_paths = _spec_paths(args.local_truth, "--local-truth")
    marker_truth_paths = _spec_paths(args.marker_truth, "--marker-truth")
    tract_truth_paths = _spec_paths(args.tract_truth, "--tract-truth")
    try:
        fixture_path = fixture_paths[iid]
        local_truth_path = local_truth_paths[iid]
        marker_truth_path = marker_truth_paths[iid]
        tract_truth_path = tract_truth_paths[iid]
    except KeyError as exc:
        raise ValueError(f"selected IID {iid!r} lacks {exc.args[0]!r} input") from None
    fixture_configs = _required_mapping(inputs.get("fixtures"), "fixture provenance")
    fixture_config = _required_mapping(
        fixture_configs.get(iid),
        f"fixture provenance for {iid}",
    )
    verification_fixtures = _required_mapping(
        input_verification.get("fixtures"),
        "fixture fingerprints",
    )
    fixture_verification = _required_mapping(
        verification_fixtures.get(iid),
        f"fixture fingerprints for {iid}",
    )
    planned_files = (
        (
            fixture_path,
            fixture_verification.get("fixture"),
            fixture_config.get("sha256"),
            f"fixture {iid}",
        ),
        (
            local_truth_path,
            fixture_verification.get("local_truth"),
            fixture_config.get("local_truth_sha256"),
            f"local truth {iid}",
        ),
        (
            marker_truth_path,
            fixture_verification.get("marker_truth"),
            fixture_config.get("marker_truth_sha256"),
            f"marker truth {iid}",
        ),
        (
            tract_truth_path,
            fixture_verification.get("tract_truth"),
            fixture_config.get("tract_truth_sha256"),
            f"tract truth {iid}",
        ),
    )
    for path, fingerprint, expected_sha256, description in planned_files:
        validate_live_input_fingerprint(
            path,
            fingerprint,
            expected_sha256=expected_sha256,
            description=description,
        )
    validation_stratum = fixture_config.get("validation_stratum")
    if not isinstance(validation_stratum, str) or not validation_stratum:
        raise ValueError(f"job plan has invalid validation stratum for {iid}")
    fixture = read_fixture(
        iid,
        validation_stratum,
        fixture_path,
        local_truth_path,
    )
    raw_truth_window_counts = fixture_config.get("local_truth_windows_by_autosome")
    observed_truth_window_counts = Counter(window.chrom for window in fixture.truth_windows)
    if (
        not isinstance(raw_truth_window_counts, Mapping)
        or set(raw_truth_window_counts) != AUTOSOME_SET
        or not all(
            isinstance(raw_truth_window_counts[chrom], int)
            and not isinstance(raw_truth_window_counts[chrom], bool)
            and raw_truth_window_counts[chrom] >= 0
            for chrom in AUTOSOMES
        )
        or sum(int(raw_truth_window_counts[chrom]) for chrom in AUTOSOMES)
        != fixture_config.get("local_truth_windows")
        or any(
            raw_truth_window_counts[chrom] != observed_truth_window_counts[chrom]
            for chrom in AUTOSOMES
        )
    ):
        raise ValueError(f"job plan has invalid local-truth window counts for {iid}")
    if (
        fixture.sha256 != fixture_config.get("sha256")
        or fixture.truth_sha256 != fixture_config.get("local_truth_sha256")
        or len(fixture.markers) != fixture_config.get("autosomal_markers")
        or len(fixture.truth_windows) != fixture_config.get("local_truth_windows")
    ):
        raise ValueError(f"selected fixture {iid!r} does not match the job plan")
    marker_truth_rows = fixture_config.get("marker_truth_rows")
    if (
        not isinstance(marker_truth_rows, int)
        or isinstance(marker_truth_rows, bool)
        or marker_truth_rows <= 0
    ):
        raise ValueError(f"job plan has invalid marker-truth row count for {iid}")
    tract_count = fixture_config.get("tract_count")
    if not isinstance(tract_count, int) or isinstance(tract_count, bool) or tract_count <= 0:
        raise ValueError(f"job plan has invalid tract count for {iid}")
    raw_model_marker_counts = fixture_config.get("model_marker_counts_by_autosome")
    if (
        not isinstance(raw_model_marker_counts, Mapping)
        or set(raw_model_marker_counts) != AUTOSOME_SET
        or not all(
            isinstance(raw_model_marker_counts[chrom], int)
            and not isinstance(raw_model_marker_counts[chrom], bool)
            and raw_model_marker_counts[chrom] > 0
            for chrom in AUTOSOMES
        )
    ):
        raise ValueError(f"job plan has invalid model marker counts for {iid}")
    raw_truth_founders = fixture_config.get("modal_truth_founders_by_class")
    if (
        not isinstance(raw_truth_founders, Mapping)
        or set(raw_truth_founders) != SUPERPOPULATIONS
        or not all(
            isinstance(raw_truth_founders[label], list)
            and all(isinstance(founder, str) and founder for founder in raw_truth_founders[label])
            and raw_truth_founders[label] == sorted(set(raw_truth_founders[label]))
            for label in SUPERPOPULATIONS
        )
    ):
        raise ValueError(f"job plan has invalid modal truth founders for {iid}")
    fixture = replace(
        fixture,
        marker_truth_path=marker_truth_path,
        marker_truth_sha256=str(fixture_config["marker_truth_sha256"]),
        marker_truth_rows=marker_truth_rows,
        tract_truth_path=tract_truth_path,
        tract_truth_sha256=str(fixture_config["tract_truth_sha256"]),
        tract_count=tract_count,
        tract_summary=_required_mapping(
            fixture_config.get("tract_summary"),
            f"tract summary for {iid}",
        ),
        model_marker_counts_by_autosome={
            chrom: int(raw_model_marker_counts[chrom]) for chrom in AUTOSOMES
        },
        truth_founders_by_class={
            label: frozenset(raw_truth_founders[label]) for label in sorted(SUPERPOPULATIONS)
        },
    )
    for path, fingerprint, expected_sha256, description in planned_files:
        validate_live_input_fingerprint(
            path,
            fingerprint,
            expected_sha256=expected_sha256,
            description=description,
        )

    selected_mask_names = {str(row["mask"]) for row in selected_rows}
    needs_twentythreeandme = bool(
        selected_mask_names & {"twentythreeandme_derived_mask", "synthetic_merged_derived_masks"}
    )
    needs_ancestrydna = bool(
        selected_mask_names & {"ancestrydna_empirical_mask", "synthetic_merged_derived_masks"}
    )
    site_mask_configs = _required_mapping(
        inputs.get("privacy_safe_site_masks"),
        "site-mask provenance",
    )
    site_mask_verification = _required_mapping(
        input_verification.get("site_masks"),
        "site-mask fingerprints",
    )
    manifests: dict[str, SiteManifest] = {}

    def load_planned_manifest(name: str, raw_path: Path | None) -> SiteManifest:
        if raw_path is None:
            raise ValueError(f"selected job requires the {name} site mask")
        path = lexical_absolute_path(raw_path)
        config = _required_mapping(
            site_mask_configs.get(name),
            f"site-mask provenance for {name}",
        )
        fingerprint = site_mask_verification.get(name)
        validate_live_input_fingerprint(
            path,
            fingerprint,
            expected_sha256=config.get("sha256"),
            description=name,
        )
        manifest = read_site_manifest(name, path)
        realized = _required_mapping(
            config.get("realized_fixture_overlap"),
            f"realized overlap for {name}",
        )
        if (
            manifest.sha256 != config.get("sha256")
            or manifest.site_count != config.get("site_rows")
            or len(manifest.rsids) != config.get("unique_rsids")
            or sum(marker.rsid in manifest.rsids for marker in fixture.markers)
            != realized.get(iid)
        ):
            raise ValueError(f"selected site mask {name!r} does not match the job plan")
        validate_live_input_fingerprint(
            path,
            fingerprint,
            expected_sha256=config.get("sha256"),
            description=name,
        )
        return manifest

    twentythreeandme = None
    ancestrydna = None
    if needs_twentythreeandme:
        name = "twentythreeandme_v5_derived_site_mask"
        twentythreeandme = load_planned_manifest(name, args.twentythreeandme_sites)
        manifests[name] = twentythreeandme
    if needs_ancestrydna:
        name = "ancestrydna_v2_empirical_site_mask"
        ancestrydna = load_planned_manifest(name, args.ancestrydna_sites)
        manifests[name] = ancestrydna
    vep_rsids: frozenset[str] | None = None
    if "synthetic_merged_derived_masks" in selected_mask_names:
        if args.vep_rsids is None:
            raise ValueError("selected merged job requires --vep-rsids")
        vep_path = lexical_absolute_path(args.vep_rsids)
        vep_config = _required_mapping(
            inputs.get("vep_rsid_membership"),
            "VEP rsID membership provenance",
        )
        identifier_verification = _required_mapping(
            input_verification.get("identifier_manifests"),
            "identifier-manifest fingerprints",
        )
        fingerprint = identifier_verification.get("production_vep_rsid_membership")
        validate_live_input_fingerprint(
            vep_path,
            fingerprint,
            expected_sha256=vep_config.get("sha256"),
            description="production VEP rsID membership",
        )
        vep_membership = read_identifier_manifest(
            "production_vep_rsid_membership",
            vep_path,
        )
        if vep_membership.sha256 != vep_config.get(
            "sha256"
        ) or vep_membership.identifier_count != vep_config.get("identifier_count"):
            raise ValueError("live VEP rsID membership differs from the job plan")
        validate_live_input_fingerprint(
            vep_path,
            fingerprint,
            expected_sha256=vep_config.get("sha256"),
            description="production VEP rsID membership",
        )
        vep_rsids = vep_membership.identifiers
    masks = {
        mask.name: mask
        for mask in build_masks(
            fixture,
            twentythreeandme,
            ancestrydna,
            vep_rsids,
        )
    }
    mask_scenarios = _required_mapping(
        configuration.get("mask_scenarios"),
        "mask scenarios",
    )
    raw_iid_masks = mask_scenarios.get(iid)
    if not isinstance(raw_iid_masks, list):
        raise ValueError(f"job plan has invalid mask scenarios for {iid}")
    mask_config_by_name = {
        str(entry["name"]): entry
        for entry in raw_iid_masks
        if isinstance(entry, Mapping) and isinstance(entry.get("name"), str)
    }
    raw_scenarios = configuration.get("chromosome_drop_scenarios")
    if not isinstance(raw_scenarios, list):
        raise ValueError("job plan has invalid chromosome-drop scenarios")
    scenario_config_by_name = {
        str(entry["name"]): entry
        for entry in raw_scenarios
        if isinstance(entry, Mapping) and isinstance(entry.get("name"), str)
    }
    hydrated_jobs: list[CalibrationJob] = []
    for row in selected_rows:
        if row["dataset_split"] != args.dataset_split:
            raise ValueError("selected job row has the wrong dataset split")
        mask_name = str(row["mask"])
        if mask_name not in masks or mask_name not in mask_config_by_name:
            raise ValueError(f"selected mask {mask_name!r} is unavailable")
        mask = masks[mask_name]
        mask_config = mask_config_by_name[mask_name]
        if (
            len(mask.markers) != mask_config.get("realized_fixture_markers")
            or list(mask.manifest_names) != mask_config.get("manifest_names")
            or mask.kind != mask_config.get("kind")
            or (mask.file_format or None) != mask_config.get("file_format")
        ):
            raise ValueError(f"selected mask {mask_name!r} does not match the job plan")
        scenario_name = str(row["chromosome_drop_scenario"])
        scenario_config = scenario_config_by_name.get(scenario_name)
        if not isinstance(scenario_config, Mapping):
            raise ValueError(f"unknown chromosome-drop scenario {scenario_name!r}")
        dropped = scenario_config.get("dropped_autosomes")
        if not isinstance(dropped, list) or not all(
            isinstance(chrom, str) and chrom in AUTOSOME_SET for chrom in dropped
        ):
            raise ValueError(f"invalid chromosome-drop scenario {scenario_name!r}")
        hydrated_jobs.append(
            CalibrationJob(
                index=int(row["job_index"]),
                fixture=fixture,
                mask=mask,
                drop_scenario=ChromosomeDropScenario(
                    scenario_name,
                    frozenset(dropped),
                ),
                fraction=Decimal(str(row["fraction"])),
                seed=int(row["seed"]),
            )
        )

    matrix_config = _required_mapping(configuration.get("job_matrix"), "job matrix")
    shards_directory = matrix_config.get("shards_directory")
    if not isinstance(shards_directory, str):
        raise ValueError("job plan has no shard directory")
    selected_shard_path = job_plan_path.parent / shards_directory / f"{args.job_index:08d}.json"
    result_inputs = [
        job_plan_path,
        selected_shard_path,
        bundle_metadata_path,
        labels_path,
        reference_manifest_path,
        verification_path,
        fixture_path,
        local_truth_path,
        marker_truth_path,
        tract_truth_path,
        Path(__file__).resolve(),
        *(manifest.path for manifest in manifests.values()),
    ]
    if vep_rsids is not None:
        result_inputs.append(vep_path)
    if confirmation_policy_path is not None:
        result_inputs.append(confirmation_policy_path)
    assert args.output is not None
    output_path, output_lock = acquire_result_output_lock(
        args.output,
        job_index=args.job_index,
        configuration_sha256=args.expected_configuration_sha256,
        inputs=result_inputs,
        forbidden_directories=(bundle_dir, job_plan_path.parent / shards_directory),
        overwrite=args.overwrite_output,
    )
    attempt_work_dir: Path | None = None
    try:
        work_root = prepare_private_work_root(args.work_dir)
        attempt_work_dir = Path(
            tempfile.mkdtemp(
                prefix=(f"{args.expected_configuration_sha256[:12]}-{args.job_index:08d}-"),
                dir=work_root,
            )
        )
        records = tuple(
            run_job(
                job,
                dataset_id=args.dataset_id,
                dataset_split=args.dataset_split,
                bundle_dir=bundle_dir,
                bundle_metadata_sha256=bundle_metadata_sha256,
                bundle_artifact_sha256=args.bundle_artifact_sha256,
                code_revision=args.code_revision,
                harness_script_sha256=harness_script_sha256,
                runtime_environment=live_runtime_environment,
                labels_sha256=labels_sha256,
                manifests=manifests,
                configuration_sha256=args.expected_configuration_sha256,
                work_dir=attempt_work_dir,
                confirmation_policy_provenance_entry=policy_provenance,
            )
            for job in hydrated_jobs
        )
        validate_reference_verification(
            bundle_dir=bundle_dir,
            reference_manifest=reference_manifest,
            payload=reference_verification.payload,
        )
        if confirmation_policy_path is not None:
            validate_live_input_fingerprint(
                confirmation_policy_path,
                policy_fingerprint,
                expected_sha256=policy_provenance.get("sha256") if policy_provenance else None,
                description="confirmation policy",
            )
        atomic_write_jsonl(output_path, records, authority=output_lock)
    finally:
        if attempt_work_dir is not None:
            shutil.rmtree(attempt_work_dir, ignore_errors=True)
        fcntl.flock(output_lock.fileno(), fcntl.LOCK_UN)
        output_lock.close()
        fcntl.flock(bundle_lock.fileno(), fcntl.LOCK_UN)
        bundle_lock.close()
    print(
        f"wrote {len(records)} calibration record(s) to {args.output}",
        file=sys.stderr,
    )
    failed = any(record["status"] in {"operational_error", "invalid"} for record in records)
    return 2 if failed else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle-dir",
        required=True,
        type=Path,
        help=(
            "calibration bundle containing production models plus a donor-excluded phasing_panel"
        ),
    )
    parser.add_argument(
        "--bundle-artifact-sha256",
        required=True,
        type=parse_sha256,
        help="complete SHA-256 of the source production bundle artifact",
    )
    parser.add_argument(
        "--code-revision",
        required=True,
        type=parse_git_revision,
        help="complete Git commit that defines the imported runtime code",
    )
    parser.add_argument(
        "--labels",
        required=True,
        type=Path,
        help="simulated validation IID/stratum TSV",
    )
    parser.add_argument(
        "--simulation-manifest",
        type=Path,
        help="pinned truth-generator, donor-contribution, and split manifest",
    )
    parser.add_argument(
        "--simulation-verification-stamp",
        "--simulation-verification-report",
        dest="simulation_verification_report",
        type=Path,
        help=(
            "executed allele-level replay stamp binding donor VCF haplotypes, "
            "marker rsIDs, and simulated fixture genotypes"
        ),
    )
    parser.add_argument(
        "--confirmation-policy",
        type=Path,
        help=(
            "frozen schema-v1 policy required to define and authenticate the "
            "final-confirmation matrix"
        ),
    )
    parser.add_argument(
        "--expected-confirmation-policy-sha256",
        type=parse_sha256,
        help="independent trusted SHA-256 of the frozen final-confirmation policy bytes",
    )
    parser.add_argument(
        "--selection-design",
        type=Path,
        help=(
            "opaque preregistered threshold-selection design; required only when "
            "planning the calibration matrix"
        ),
    )
    parser.add_argument(
        "--expected-selection-design-sha256",
        type=parse_sha256,
        help="independent trusted SHA-256 of the calibration selection-design bytes",
    )
    parser.add_argument(
        "--verify-simulation",
        action="store_true",
        help="execute the repository-owned allele replay verifier and write the stamp",
    )
    parser.add_argument(
        "--donor-vcf",
        action="append",
        type=parse_fixture_spec,
        default=[],
        metavar="CHROM=PATH",
        help="pinned donor VCF; repeat for autosomes 1..22 in verification mode",
    )
    parser.add_argument(
        "--donor-vcf-index",
        action="append",
        type=parse_fixture_spec,
        default=[],
        metavar="CHROM=PATH",
        help="pinned donor VCF index; repeat for autosomes 1..22",
    )
    parser.add_argument(
        "--verifier-environment-lock",
        type=Path,
        default=REPO_ROOT / "uv.lock",
        help="tracked runtime lock authenticated by the simulation verifier",
    )
    parser.add_argument(
        "--simulation-generator-script",
        type=Path,
        help="actual generator source pinned by simulation-manifest generator.script_sha256",
    )
    parser.add_argument(
        "--simulation-generator-environment-lock",
        type=Path,
        help="actual generator environment lock pinned by the simulation manifest",
    )
    parser.add_argument(
        "--dataset-split",
        required=True,
        choices=("calibration", "final_confirmation"),
        help="founder-disjoint simulation split to execute",
    )
    parser.add_argument(
        "--fixture",
        required=True,
        action="append",
        type=parse_fixture_spec,
        metavar="IID=PATH",
        help="explicit public/simulated five-column fixture; repeat for each IID",
    )
    parser.add_argument(
        "--local-truth",
        required=True,
        action="append",
        type=parse_fixture_spec,
        metavar="IID=PATH",
        help="exact chrom/start/end/hap0/hap1 model-window truth; repeat per IID",
    )
    parser.add_argument(
        "--marker-truth",
        required=True,
        action="append",
        type=parse_fixture_spec,
        metavar="IID=PATH",
        help=("exact marker-level donor/haplotype truth used to derive windows; repeat per IID"),
    )
    parser.add_argument(
        "--tract-truth",
        required=True,
        action="append",
        type=parse_fixture_spec,
        metavar="IID=PATH",
        help="gap-free founder-tract truth; repeat for each simulated IID",
    )
    parser.add_argument(
        "--validation-donors",
        type=Path,
        help="public donor IID manifest used to generate the simulated fixtures",
    )
    parser.add_argument(
        "--donor-metadata",
        type=Path,
        help="pinned donor metadata used to resolve canonical production model classes",
    )
    parser.add_argument(
        "--validation-isolation-samples",
        type=Path,
        help=(
            "IID manifest containing every validation donor and all declared close "
            "relatives that must be absent from model training and phasing"
        ),
    )
    parser.add_argument(
        "--validation-relationships",
        type=Path,
        help="complete protected-IID relationship graph used to isolate both splits",
    )
    parser.add_argument(
        "--gnomix-training-samples",
        type=Path,
        help="actual Gnomix training sample map; validation donors must be absent",
    )
    parser.add_argument(
        "--calibration-reference-manifest",
        required=True,
        type=Path,
        help=(
            "manifest for the donor-excluded phasing panel, including source-bundle "
            "and per-autosome hashes"
        ),
    )
    parser.add_argument(
        "--reference-verification-stamp",
        required=True,
        type=Path,
        help="one-time full-hash verification stamp for the live calibration bundle",
    )
    parser.add_argument(
        "--verify-reference",
        action="store_true",
        help="full-hash the reference once, atomically write the stamp, and exit",
    )
    parser.add_argument(
        "--dataset-id",
        required=True,
        help="stable identifier for the simulated validation dataset and release",
    )
    parser.add_argument(
        "--twentythreeandme-sites",
        type=Path,
        help=(
            "optional privacy-safe 23andMe v5 derived site mask TSV; this is "
            "not represented as a vendor-published manifest"
        ),
    )
    parser.add_argument(
        "--ancestrydna-sites",
        type=Path,
        help=(
            "optional privacy-safe empirical AncestryDNA v2 union site mask TSV; "
            "this is not represented as a vendor-published manifest"
        ),
    )
    parser.add_argument(
        "--vep-rsids",
        type=Path,
        help=(
            "privacy-safe one-column rsID membership snapshot from the pinned VEP "
            "bundle, used only for production-equivalent merged alias resolution"
        ),
    )
    parser.add_argument(
        "--fraction",
        action="append",
        type=parse_fraction,
        help="positive retained-marker fraction; repeat to define the curve",
    )
    parser.add_argument(
        "--seed",
        action="append",
        type=int,
        help="deterministic downsampling seed; repeat as needed",
    )
    parser.add_argument(
        "--drop-scenario",
        action="append",
        type=parse_drop_scenario,
        default=[],
        metavar="NAME=CHR[,CHR...]",
        help="structured chromosome-drop scenario; baseline 'none' is automatic",
    )
    parser.add_argument(
        "--job-index",
        type=int,
        help="run one zero-based matrix job (ideal for a SLURM array task)",
    )
    parser.add_argument(
        "--list-jobs",
        action="store_true",
        help="print the deterministic job matrix as JSON Lines without inference",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        help=("planner-only matrix cap (default: 100000; hard maximum: 1000000)"),
    )
    parser.add_argument(
        "--job-plan",
        type=Path,
        help=("atomic planner output; required with --list-jobs and for later planned inference"),
    )
    parser.add_argument(
        "--expected-configuration-sha256",
        type=parse_sha256,
        help="configuration hash emitted by --list-jobs; required for inference",
    )
    parser.add_argument("--output", type=Path, help="atomic JSONL output path")
    parser.add_argument(
        "--overwrite-output",
        action="store_true",
        help="replace an existing different-job output only after explicit review",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / f"yeliztli-lai-coverage-{os.getuid()}",
        help="operator-owned 0700 scratch root for attempt-unique LAI intermediates",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if sum((args.verify_reference, args.verify_simulation, args.list_jobs)) > 1:
        parser.error(
            "--verify-reference, --verify-simulation, and --list-jobs are mutually exclusive"
        )
    non_inference_mode = args.verify_reference or args.verify_simulation or args.list_jobs
    if non_inference_mode:
        irrelevant_execution_options = [
            name
            for name, supplied in (
                ("--job-index", args.job_index is not None),
                (
                    "--expected-configuration-sha256",
                    args.expected_configuration_sha256 is not None,
                ),
                ("--output", args.output is not None),
                ("--overwrite-output", args.overwrite_output),
            )
            if supplied
        ]
        if irrelevant_execution_options:
            parser.error(
                "planning/verification rejects inference-only option(s): "
                + ", ".join(irrelevant_execution_options)
            )
    if (args.verify_reference or args.verify_simulation) and args.job_plan is not None:
        parser.error("verification modes reject --job-plan")
    if not args.verify_simulation:
        irrelevant_verifier_options = [
            name
            for name, supplied in (
                ("--donor-vcf", bool(args.donor_vcf)),
                ("--donor-vcf-index", bool(args.donor_vcf_index)),
                ("--simulation-generator-script", args.simulation_generator_script is not None),
                (
                    "--simulation-generator-environment-lock",
                    args.simulation_generator_environment_lock is not None,
                ),
            )
            if supplied
        ]
        if irrelevant_verifier_options:
            parser.error(
                "this mode rejects simulation-verifier-only option(s): "
                + ", ".join(irrelevant_verifier_options)
            )
    if args.list_jobs and args.job_plan is None:
        parser.error("--job-plan is required with --list-jobs")
    if not args.list_jobs and args.max_jobs is not None:
        parser.error("--max-jobs is only valid with --list-jobs")
    selection_design_options = (
        args.selection_design is not None,
        args.expected_selection_design_sha256 is not None,
    )
    if args.list_jobs and args.dataset_split == "calibration":
        if not all(selection_design_options):
            parser.error(
                "calibration --list-jobs requires both --selection-design and "
                "--expected-selection-design-sha256"
            )
    elif any(selection_design_options):
        parser.error(
            "--selection-design and --expected-selection-design-sha256 are only valid "
            "together for calibration --list-jobs"
        )
    if args.dataset_split == "calibration" and (
        args.confirmation_policy is not None
        or args.expected_confirmation_policy_sha256 is not None
    ):
        parser.error(
            "calibration rejects --confirmation-policy and --expected-confirmation-policy-sha256"
        )
    if args.dataset_split == "final_confirmation":
        if args.confirmation_policy is None:
            parser.error("final_confirmation requires --confirmation-policy")
        if args.expected_confirmation_policy_sha256 is None:
            parser.error("final_confirmation requires --expected-confirmation-policy-sha256")
    if args.list_jobs and args.dataset_split == "final_confirmation":
        supplied_matrix_overrides = [
            name
            for name, supplied in (
                ("--fraction", args.fraction is not None),
                ("--seed", args.seed is not None),
                ("--drop-scenario", bool(args.drop_scenario)),
            )
            if supplied
        ]
        if supplied_matrix_overrides:
            parser.error(
                "final_confirmation planning rejects matrix-definition option(s): "
                + ", ".join(supplied_matrix_overrides)
            )
    if args.list_jobs and (
        args.twentythreeandme_sites is None
        or args.ancestrydna_sites is None
        or args.vep_rsids is None
    ):
        parser.error(
            "--list-jobs requires both --twentythreeandme-sites and "
            "--ancestrydna-sites plus --vep-rsids so every supported input shape "
            "is production-faithful"
        )
    if (
        not args.list_jobs
        and not args.verify_reference
        and not args.verify_simulation
        and args.output is None
    ):
        parser.error("--output is required for inference")
    if (
        not args.list_jobs
        and not args.verify_reference
        and not args.verify_simulation
        and args.expected_configuration_sha256 is None
    ):
        parser.error("--expected-configuration-sha256 is required for inference")
    if not args.list_jobs and not args.verify_reference and not args.verify_simulation:
        try:
            return run_from_job_plan(args)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
    required_eager = {
        "--simulation-manifest": args.simulation_manifest,
        "--simulation-verification-report": args.simulation_verification_report,
        "--validation-donors": args.validation_donors,
        "--donor-metadata": args.donor_metadata,
        "--validation-isolation-samples": args.validation_isolation_samples,
        "--validation-relationships": args.validation_relationships,
        "--gnomix-training-samples": args.gnomix_training_samples,
    }
    missing_eager = [name for name, value in required_eager.items() if value is None]
    if missing_eager:
        parser.error("planning/reference verification requires " + ", ".join(missing_eager))
    if (
        args.list_jobs
        and args.dataset_split == "calibration"
        and (not args.fraction or not args.seed)
    ):
        parser.error("--list-jobs requires at least one --fraction and --seed")

    try:
        confirmation_policy_path = (
            lexical_absolute_path(args.confirmation_policy)
            if args.confirmation_policy is not None
            else None
        )
        selection_design_path = (
            lexical_absolute_path(args.selection_design)
            if args.selection_design is not None
            else None
        )
    except ValueError as exc:
        parser.error(str(exc))
    bundle_dir = lexical_absolute_path(args.bundle_dir)
    eager_bundle_lock = acquire_bundle_lock(
        bundle_dir,
        exclusive=args.verify_reference,
    )
    bundle_metadata_path = bundle_dir / "metadata.json"
    labels_path = lexical_absolute_path(args.labels)
    assert args.simulation_manifest is not None
    assert args.simulation_verification_report is not None
    assert args.validation_donors is not None
    assert args.donor_metadata is not None
    assert args.validation_isolation_samples is not None
    assert args.validation_relationships is not None
    assert args.gnomix_training_samples is not None
    simulation_manifest_path = lexical_absolute_path(args.simulation_manifest)
    simulation_verification_path = lexical_absolute_path(args.simulation_verification_report)
    donor_manifest_path = lexical_absolute_path(args.validation_donors)
    donor_metadata_path = lexical_absolute_path(args.donor_metadata)
    isolation_manifest_path = lexical_absolute_path(args.validation_isolation_samples)
    relationships_path = lexical_absolute_path(args.validation_relationships)
    training_samples_path = lexical_absolute_path(args.gnomix_training_samples)
    reference_manifest_path = lexical_absolute_path(args.calibration_reference_manifest)
    reference_verification_path = lexical_absolute_path(args.reference_verification_stamp)
    required_paths = [
        bundle_metadata_path,
        labels_path,
        simulation_manifest_path,
        donor_manifest_path,
        donor_metadata_path,
        isolation_manifest_path,
        relationships_path,
        training_samples_path,
        reference_manifest_path,
        Path(__file__).absolute(),
        REPO_ROOT / "uv.lock",
        REPO_ROOT / "scripts/lai_bundle_v2/06g_verify_simulation.py",
        REPO_ROOT / "scripts/lai_bundle_v2/lai_coverage_plan.py",
        REPO_ROOT / "scripts/lai_bundle_v2/lai_coverage_policy.py",
    ]
    if not args.verify_simulation:
        required_paths.append(simulation_verification_path)
    if not args.verify_reference and not args.verify_simulation:
        required_paths.append(reference_verification_path)
    if confirmation_policy_path is not None:
        required_paths.append(confirmation_policy_path)
    if selection_design_path is not None:
        required_paths.append(selection_design_path)
    if args.twentythreeandme_sites is not None:
        required_paths.append(lexical_absolute_path(args.twentythreeandme_sites))
    if args.ancestrydna_sites is not None:
        required_paths.append(lexical_absolute_path(args.ancestrydna_sites))
    if args.vep_rsids is not None:
        required_paths.append(lexical_absolute_path(args.vep_rsids))
    required_paths.extend(lexical_absolute_path(path) for _, path in args.fixture)
    required_paths.extend(lexical_absolute_path(path) for _, path in args.local_truth)
    required_paths.extend(lexical_absolute_path(path) for _, path in args.marker_truth)
    required_paths.extend(lexical_absolute_path(path) for _, path in args.tract_truth)
    if args.verify_simulation:
        if args.simulation_generator_script is None:
            parser.error("--verify-simulation requires --simulation-generator-script")
        if args.simulation_generator_environment_lock is None:
            parser.error("--verify-simulation requires --simulation-generator-environment-lock")
        donor_vcf_paths = _spec_paths(args.donor_vcf, "--donor-vcf")
        donor_vcf_index_paths = _spec_paths(
            args.donor_vcf_index,
            "--donor-vcf-index",
        )
        if set(donor_vcf_paths) != AUTOSOME_SET or set(donor_vcf_index_paths) != AUTOSOME_SET:
            parser.error("--verify-simulation requires donor VCFs and indexes for autosomes 1..22")
        required_paths.extend(donor_vcf_paths.values())
        required_paths.extend(donor_vcf_index_paths.values())
        required_paths.append(lexical_absolute_path(args.verifier_environment_lock))
        required_paths.append(lexical_absolute_path(args.simulation_generator_script))
        required_paths.append(lexical_absolute_path(args.simulation_generator_environment_lock))
    missing = [path for path in required_paths if not path.is_file()]
    if missing:
        parser.error("missing input file(s): " + ", ".join(str(path) for path in missing))
    stamp_output_lock: ResultOutputLock | None = None
    try:
        if args.verify_reference:
            reference_verification_path, stamp_output_lock = acquire_result_output_lock(
                reference_verification_path,
                job_index=-1,
                configuration_sha256=args.code_revision,
                inputs=required_paths,
                forbidden_directories=(bundle_dir,),
                overwrite=True,
            )
        elif args.verify_simulation:
            simulation_verification_path = validate_destination(
                simulation_verification_path,
                role="simulation-verification stamp",
                inputs=required_paths,
                forbidden_directories=(bundle_dir,),
            )
        elif args.list_jobs:
            assert args.job_plan is not None
            validate_destination(
                args.job_plan,
                role="job plan",
                inputs=required_paths,
                forbidden_directories=(bundle_dir,),
            )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    if args.verify_simulation:
        try:
            verify_runtime_code_revision(args.code_revision)
        except ValueError as exc:
            parser.error(str(exc))
        verifier_command = [
            sys.executable,
            str(REPO_ROOT / "scripts/lai_bundle_v2/06g_verify_simulation.py"),
            "--simulation-manifest",
            str(simulation_manifest_path),
            "--dataset-split",
            args.dataset_split,
            "--verifier-environment-lock",
            str(lexical_absolute_path(args.verifier_environment_lock)),
            "--generator-script",
            str(lexical_absolute_path(args.simulation_generator_script)),
            "--generator-environment-lock",
            str(lexical_absolute_path(args.simulation_generator_environment_lock)),
            "--expected-code-revision",
            args.code_revision,
            "--output",
            str(simulation_verification_path),
        ]
        if args.dataset_split == "final_confirmation":
            assert confirmation_policy_path is not None
            assert args.expected_confirmation_policy_sha256 is not None
            verifier_command.extend(
                (
                    "--confirmation-policy",
                    str(confirmation_policy_path),
                    "--expected-confirmation-policy-sha256",
                    args.expected_confirmation_policy_sha256,
                )
            )
        for iid, path in args.fixture:
            verifier_command.extend(("--fixture", f"{iid}={lexical_absolute_path(path)}"))
        for iid, path in args.marker_truth:
            verifier_command.extend(("--marker-truth", f"{iid}={lexical_absolute_path(path)}"))
        for iid, path in args.tract_truth:
            verifier_command.extend(("--tract-truth", f"{iid}={lexical_absolute_path(path)}"))
        for chrom, path in args.donor_vcf:
            verifier_command.extend(("--donor-vcf", f"{chrom}={lexical_absolute_path(path)}"))
        for chrom, path in args.donor_vcf_index:
            verifier_command.extend(
                ("--donor-vcf-index", f"{chrom}={lexical_absolute_path(path)}")
            )
        completed = subprocess.run(verifier_command, check=False)
        if completed.returncode != 0:
            parser.error("repository simulation verifier failed; no stamp was accepted")
        print(f"wrote simulation verification to {simulation_verification_path}")
        fcntl.flock(eager_bundle_lock.fileno(), fcntl.LOCK_UN)
        eager_bundle_lock.close()
        return 0

    confirmation_policy: ConfirmationPolicy | None = None
    selection_design: SelectionDesignArtifact | None = None
    try:
        verify_runtime_code_revision(args.code_revision)
        runtime_environment = runtime_environment_fingerprint()
        # Parsing metadata verifies that the provenance file is valid JSON; the
        # full payload stays in the bundle and its bytes are pinned by SHA-256.
        bundle_metadata, bundle_metadata_snapshot = stable_read(
            bundle_metadata_path,
            lambda path: json.loads(path.read_text(encoding="utf-8")),
        )
        if not isinstance(bundle_metadata, dict):
            raise ValueError(f"{bundle_metadata_path}: expected a JSON object")
        bundle_metadata_summary = {
            key: bundle_metadata.get(key)
            for key in (
                "bundle_version",
                "build_date",
                "source_sites_sha256",
                "site_count",
                "window_count",
            )
        }
        labels, labels_snapshot = stable_read(labels_path, read_labels)
        fixture_specs: dict[str, Path] = {}
        for iid, path in args.fixture:
            if iid in fixture_specs:
                raise ValueError(f"duplicate --fixture IID {iid!r}")
            if iid not in labels:
                raise ValueError(f"fixture IID {iid!r} has no validation stratum in {labels_path}")
            fixture_specs[iid] = lexical_absolute_path(path)
        local_truth_specs: dict[str, Path] = {}
        for iid, path in args.local_truth:
            if iid in local_truth_specs:
                raise ValueError(f"duplicate --local-truth IID {iid!r}")
            local_truth_specs[iid] = lexical_absolute_path(path)
        marker_truth_specs: dict[str, Path] = {}
        for iid, path in args.marker_truth:
            if iid in marker_truth_specs:
                raise ValueError(f"duplicate --marker-truth IID {iid!r}")
            marker_truth_specs[iid] = lexical_absolute_path(path)
        tract_truth_specs: dict[str, Path] = {}
        for iid, path in args.tract_truth:
            if iid in tract_truth_specs:
                raise ValueError(f"duplicate --tract-truth IID {iid!r}")
            tract_truth_specs[iid] = lexical_absolute_path(path)
        if set(fixture_specs) != set(labels):
            missing_fixtures = sorted(set(labels) - set(fixture_specs))
            extra_fixtures = sorted(set(fixture_specs) - set(labels))
            raise ValueError(
                "labels and fixtures must contain exactly the same IIDs; "
                f"missing fixtures={missing_fixtures}, extra fixtures={extra_fixtures}"
            )
        if set(local_truth_specs) != set(fixture_specs):
            missing_truth = sorted(set(fixture_specs) - set(local_truth_specs))
            extra_truth = sorted(set(local_truth_specs) - set(fixture_specs))
            raise ValueError(
                "fixtures and local truth must contain exactly the same IIDs; "
                f"missing truth={missing_truth}, extra truth={extra_truth}"
            )
        if set(marker_truth_specs) != set(fixture_specs):
            missing_truth = sorted(set(fixture_specs) - set(marker_truth_specs))
            extra_truth = sorted(set(marker_truth_specs) - set(fixture_specs))
            raise ValueError(
                "fixtures and marker truth must contain exactly the same IIDs; "
                f"missing truth={missing_truth}, extra truth={extra_truth}"
            )
        if set(tract_truth_specs) != set(fixture_specs):
            missing_truth = sorted(set(fixture_specs) - set(tract_truth_specs))
            extra_truth = sorted(set(tract_truth_specs) - set(fixture_specs))
            raise ValueError(
                "fixtures and tract truth must contain exactly the same IIDs; "
                f"missing truth={missing_truth}, extra truth={extra_truth}"
            )
        donor_iids, donor_manifest_snapshot = stable_read(
            donor_manifest_path,
            read_iid_set,
        )
        isolation_iids, isolation_manifest_snapshot = stable_read(
            isolation_manifest_path,
            read_iid_set,
        )
        training_iids, training_samples_snapshot = stable_read(
            training_samples_path,
            read_iid_set,
        )
        simulation_manifest, simulation_manifest_snapshot = stable_read(
            simulation_manifest_path,
            lambda path: read_simulation_manifest(
                path,
                dataset_id=args.dataset_id,
                source_bundle_artifact_sha256=args.bundle_artifact_sha256,
                donor_iids=donor_iids,
                isolation_iids=isolation_iids,
            ),
        )
        if simulation_manifest.sha256 != simulation_manifest_snapshot["sha256"]:
            raise ValueError("simulation manifest changed while it was being parsed")
        if simulation_manifest.generator.get("code_revision") != args.code_revision:
            raise ValueError("simulation generator revision differs from --code-revision")
        if args.dataset_split == "final_confirmation":
            assert confirmation_policy_path is not None
            assert args.expected_confirmation_policy_sha256 is not None
            confirmation_policy = read_confirmation_policy(
                confirmation_policy_path,
                dataset_id=args.dataset_id,
                bundle_artifact_sha256=args.bundle_artifact_sha256,
                simulation_manifest_sha256=simulation_manifest.sha256,
                code_revision=args.code_revision,
                final_confirmation_split_commitment_sha256=(
                    compute_final_confirmation_split_commitment(simulation_manifest)
                ),
                expected_confirmation_policy_sha256=(args.expected_confirmation_policy_sha256),
            )
        relationship_summary = read_and_validate_relationships(
            relationships_path,
            simulation_manifest=simulation_manifest,
            donor_iids=donor_iids,
            isolation_iids=isolation_iids,
        )
        simulation_verification, simulation_verification_snapshot = stable_read(
            simulation_verification_path,
            lambda path: read_simulation_verification_report(
                path,
                simulation_manifest,
                dataset_split=args.dataset_split,
                expected_code_revision=args.code_revision,
                expected_confirmation_policy=confirmation_policy,
            ),
        )
        if simulation_verification.sha256 != simulation_verification_snapshot["sha256"]:
            raise ValueError("simulation verification report changed while it was being parsed")
        donor_labels, donor_metadata_snapshot = stable_read(
            donor_metadata_path,
            lambda path: read_donor_labels(
                path,
                simulation_manifest,
                donor_iids,
            ),
        )
        validate_split_population_coverage(simulation_manifest, donor_labels)
        basic_fixtures = tuple(
            read_fixture(iid, labels[iid], path, local_truth_specs[iid])
            for iid, path in sorted(fixture_specs.items())
        )
        reference_manifest, reference_manifest_snapshot = stable_read(
            reference_manifest_path,
            lambda path: read_calibration_reference_manifest(
                path,
                args.bundle_artifact_sha256,
            ),
        )
        if reference_manifest.sha256 != reference_manifest_snapshot["sha256"]:
            raise ValueError("reference manifest changed while it was being parsed")
        if args.verify_reference:
            verification_payload = build_reference_verification(
                bundle_dir,
                reference_manifest,
            )
            validate_calibration_isolation(
                bundle_dir=bundle_dir,
                fixtures=basic_fixtures,
                donor_iids=donor_iids,
                isolation_iids=isolation_iids,
                training_iids=training_iids,
                reference_manifest=reference_manifest,
                reference_verification=verification_payload,
            )
            truth_model_specs = load_truth_model_specs(
                bundle_dir,
                simulation_manifest,
                reference_manifest,
            )
            fixtures = tuple(
                attach_validated_simulation_truth(
                    fixture,
                    marker_truth_path=marker_truth_specs[fixture.iid],
                    tract_truth_path=tract_truth_specs[fixture.iid],
                    model_specs=truth_model_specs,
                    donor_labels=donor_labels,
                    donor_iids=donor_iids,
                    simulation_manifest=simulation_manifest,
                )
                for fixture in basic_fixtures
            )
            validate_simulation_fixtures(
                simulation_manifest=simulation_manifest,
                dataset_split=args.dataset_split,
                fixtures=fixtures,
            )
            validate_selected_split_evaluation_coverage(
                simulation_manifest=simulation_manifest,
                donor_labels=donor_labels,
                dataset_split=args.dataset_split,
                fixtures=fixtures,
            )
            assert stamp_output_lock is not None
            atomic_write_json(
                reference_verification_path,
                verification_payload,
                authority=stamp_output_lock,
            )
            print(f"wrote reference verification to {reference_verification_path}")
            fcntl.flock(stamp_output_lock.fileno(), fcntl.LOCK_UN)
            stamp_output_lock.close()
            fcntl.flock(eager_bundle_lock.fileno(), fcntl.LOCK_UN)
            eager_bundle_lock.close()
            return 0
        reference_verification, reference_verification_snapshot = stable_read(
            reference_verification_path,
            lambda path: read_reference_verification(path, reference_manifest),
        )
        if reference_verification.sha256 != reference_verification_snapshot["sha256"]:
            raise ValueError("reference verification changed while it was being parsed")
        reference_runtime_summary = validate_calibration_isolation(
            bundle_dir=bundle_dir,
            fixtures=basic_fixtures,
            donor_iids=donor_iids,
            isolation_iids=isolation_iids,
            training_iids=training_iids,
            reference_manifest=reference_manifest,
            reference_verification=reference_verification.payload,
        )
        truth_model_specs = load_truth_model_specs(
            bundle_dir,
            simulation_manifest,
            reference_manifest,
        )
        fixtures = tuple(
            attach_validated_simulation_truth(
                fixture,
                marker_truth_path=marker_truth_specs[fixture.iid],
                tract_truth_path=tract_truth_specs[fixture.iid],
                model_specs=truth_model_specs,
                donor_labels=donor_labels,
                donor_iids=donor_iids,
                simulation_manifest=simulation_manifest,
            )
            for fixture in basic_fixtures
        )
        validate_simulation_fixtures(
            simulation_manifest=simulation_manifest,
            dataset_split=args.dataset_split,
            fixtures=fixtures,
        )
        evaluation_coverage_summary = validate_selected_split_evaluation_coverage(
            simulation_manifest=simulation_manifest,
            donor_labels=donor_labels,
            dataset_split=args.dataset_split,
            fixtures=fixtures,
        )

        twentythreeandme = (
            read_site_manifest(
                "twentythreeandme_v5_derived_site_mask",
                lexical_absolute_path(args.twentythreeandme_sites),
            )
            if args.twentythreeandme_sites is not None
            else None
        )
        ancestrydna = (
            read_site_manifest(
                "ancestrydna_v2_empirical_site_mask",
                lexical_absolute_path(args.ancestrydna_sites),
            )
            if args.ancestrydna_sites is not None
            else None
        )
        vep_membership = (
            read_identifier_manifest(
                "production_vep_rsid_membership",
                lexical_absolute_path(args.vep_rsids),
            )
            if args.vep_rsids is not None
            else None
        )
        if vep_membership is None:
            raise ValueError("planning requires a VEP rsID membership manifest")
        manifests = {
            manifest.name: manifest
            for manifest in (twentythreeandme, ancestrydna)
            if manifest is not None
        }
        masks_by_iid = {
            fixture.iid: build_masks(
                fixture,
                twentythreeandme,
                ancestrydna,
                vep_membership.identifiers,
            )
            for fixture in fixtures
        }

        if confirmation_policy is not None:
            masks_by_iid, drop_scenarios, fractions, seeds = matrix_from_confirmation_policy(
                confirmation_policy,
                masks_by_iid,
            )
        else:
            assert args.fraction is not None
            assert args.seed is not None
            if len(set(args.fraction)) != len(args.fraction):
                raise ValueError("fractions must be unique")
            if len(set(args.seed)) != len(args.seed):
                raise ValueError("seeds must be unique")
            fractions = tuple(sorted(args.fraction))
            seeds = tuple(sorted(args.seed))
            scenarios_by_name = {"none": ChromosomeDropScenario("none", frozenset())}
            for scenario in args.drop_scenario:
                if scenario.name in scenarios_by_name:
                    raise ValueError(f"duplicate chromosome-drop scenario {scenario.name!r}")
                scenarios_by_name[scenario.name] = scenario
            drop_scenarios = (
                scenarios_by_name["none"],
                *(scenarios_by_name[name] for name in sorted(scenarios_by_name) if name != "none"),
            )
        if selection_design_path is not None:
            assert args.expected_selection_design_sha256 is not None
            selection_design_snapshot = _stable_file_snapshot(selection_design_path)
            if selection_design_snapshot["sha256"] != args.expected_selection_design_sha256:
                raise ValueError(
                    f"{selection_design_path}: selection-design SHA-256 does not match "
                    "expected identity"
                )
            selection_design = SelectionDesignArtifact(
                path=selection_design_path,
                sha256=args.expected_selection_design_sha256,
                fingerprint=selection_design_snapshot,
            )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    bundle_metadata_sha256 = str(bundle_metadata_snapshot["sha256"])
    harness_script_sha256 = sha256_file(Path(__file__).resolve())
    labels_sha256 = str(labels_snapshot["sha256"])
    donor_manifest_sha256 = str(donor_manifest_snapshot["sha256"])
    donor_metadata_sha256 = str(donor_metadata_snapshot["sha256"])
    isolation_manifest_sha256 = str(isolation_manifest_snapshot["sha256"])
    training_samples_sha256 = str(training_samples_snapshot["sha256"])
    configuration = build_configuration(
        dataset_id=args.dataset_id,
        dataset_split=args.dataset_split,
        simulation_manifest=simulation_manifest,
        simulation_verification=simulation_verification,
        bundle_metadata_path=bundle_metadata_path,
        bundle_metadata_sha256=bundle_metadata_sha256,
        bundle_metadata_summary=bundle_metadata_summary,
        bundle_artifact_sha256=args.bundle_artifact_sha256,
        code_revision=args.code_revision,
        harness_script_sha256=harness_script_sha256,
        runtime_environment=runtime_environment,
        labels_path=labels_path,
        labels_sha256=labels_sha256,
        donor_manifest_path=donor_manifest_path,
        donor_manifest_sha256=donor_manifest_sha256,
        donor_iids=donor_iids,
        donor_metadata_path=donor_metadata_path,
        donor_metadata_sha256=donor_metadata_sha256,
        donor_labels=donor_labels,
        isolation_manifest_path=isolation_manifest_path,
        isolation_manifest_sha256=isolation_manifest_sha256,
        isolation_iids=isolation_iids,
        relationship_summary=relationship_summary,
        training_samples_path=training_samples_path,
        training_samples_sha256=training_samples_sha256,
        training_iids=training_iids,
        reference_manifest=reference_manifest,
        reference_verification=reference_verification,
        reference_runtime_summary=reference_runtime_summary,
        evaluation_coverage_summary=evaluation_coverage_summary,
        fixtures=fixtures,
        manifests=manifests,
        vep_membership=vep_membership,
        masks_by_iid=masks_by_iid,
        drop_scenarios=drop_scenarios,
        fractions=fractions,
        seeds=seeds,
        confirmation_policy=confirmation_policy,
        selection_design=selection_design,
    )
    assert args.job_plan is not None
    plan_path = lexical_absolute_path(args.job_plan)
    input_verification = build_input_verification(
        fixtures,
        manifests,
        {vep_membership.name: vep_membership},
        confirmation_policy,
        selection_design,
    )
    fixture_mask_axes = tuple(
        (
            fixture.iid,
            tuple(mask.name for mask in masks_by_iid[fixture.iid]),
        )
        for fixture in sorted(fixtures, key=lambda item: item.iid)
    )
    max_jobs = args.max_jobs if args.max_jobs is not None else lai_coverage_plan.DEFAULT_MAX_JOBS
    try:
        job_matrix = lai_coverage_plan.JobMatrix.create(
            dataset_split=args.dataset_split,
            fixture_masks=fixture_mask_axes,
            drop_scenarios=tuple(scenario.name for scenario in drop_scenarios),
            fractions=fractions,
            seeds=seeds,
            max_jobs=max_jobs,
        )
        require_job_plan_inode_capacity(plan_path, job_matrix.row_count)
        plan_result = lai_coverage_plan.build_job_plan(
            plan_path,
            configuration=configuration,
            input_verification=input_verification,
            dataset_split=job_matrix.dataset_split,
            fixture_masks=job_matrix.fixture_masks,
            drop_scenarios=job_matrix.drop_scenarios,
            fractions=job_matrix.fractions,
            seeds=job_matrix.seeds,
            max_jobs=job_matrix.max_jobs,
        )
    except (OSError, ValueError) as exc:
        fcntl.flock(eager_bundle_lock.fileno(), fcntl.LOCK_UN)
        eager_bundle_lock.close()
        parser.error(str(exc))

    if args.list_jobs:
        for row in job_matrix.iter_rows():
            row["configuration_sha256"] = plan_result.configuration_sha256
            print(
                json.dumps(
                    row,
                    sort_keys=True,
                )
            )
        fcntl.flock(eager_bundle_lock.fileno(), fcntl.LOCK_UN)
        eager_bundle_lock.close()
        return 0
    raise AssertionError("unreachable planner state")


if __name__ == "__main__":
    raise SystemExit(main())
