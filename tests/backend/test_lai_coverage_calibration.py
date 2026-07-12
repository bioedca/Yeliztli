"""Focused tests for the deterministic LAI coverage-calibration harness."""

from __future__ import annotations

import argparse
import dataclasses
import gzip
import importlib.util
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import sqlalchemy as sa

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "lai_bundle_v2" / "06g_calibrate_coverage.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("lai_coverage_calibration", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cal = _load_script_module()


def _marker(
    index: int,
    chrom: str = "1",
    *,
    source: str = "",
    pos: int | None = None,
    rsid: str | None = None,
):
    return cal.Marker(
        rsid=rsid or f"rs{index}",
        chrom=chrom,
        pos=pos if pos is not None else 1000 + index,
        genotype="AG",
        source=source,
    )


def _default_truth_windows() -> tuple:
    return tuple(
        cal.TruthWindow(
            chrom=str(chrom),
            start=chrom * 1000,
            end=chrom * 1000 + 99,
            hap0="EUR",
            hap1="EUR",
        )
        for chrom in range(1, 23)
    )


def _write_truth(path: Path, windows=None) -> Path:
    windows = tuple(windows or _default_truth_windows())
    rows = ["chrom\tstart\tend\thap0\thap1"]
    rows.extend(
        f"{window.chrom}\t{window.start}\t{window.end}\t{window.hap0}\t{window.hap1}"
        for window in windows
    )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _fixture(
    tmp_path: Path,
    markers=None,
    *,
    iid: str = "SIMTEST",
    validation_stratum: str = "cross-superpopulation-mosaic",
    truth_windows=None,
    truth_founders_by_class=None,
):
    path = tmp_path / f"{iid}.tsv"
    path.write_text("fixture bytes\n", encoding="utf-8")
    truth_path = _write_truth(
        tmp_path / f"{iid}.truth.tsv",
        truth_windows or _default_truth_windows(),
    )
    marker_tuple = tuple(markers or [_marker(chrom, str(chrom)) for chrom in range(1, 23)])
    return cal.ValidationFixture(
        iid=iid,
        validation_stratum=validation_stratum,
        path=path,
        sha256=cal.sha256_file(path),
        markers=marker_tuple,
        parsing={"autosomal_markers": len(marker_tuple)},
        truth_path=truth_path,
        truth_sha256=cal.sha256_file(truth_path),
        truth_windows=cal.read_local_truth(truth_path),
        truth_founders_by_class=truth_founders_by_class or {},
    )


def _painting_for_truth(truth_windows) -> dict[str, list[dict[str, object]]]:
    painting: dict[str, list[dict[str, object]]] = {}
    for window in truth_windows:
        painting.setdefault(f"chr{window.chrom}", []).append(
            {
                "start": window.start,
                "end": window.end,
                "hap0": window.hap0,
                "hap1": window.hap1,
            }
        )
    return painting


def _truth_global_ancestry(truth_windows) -> dict[str, dict[str, float]]:
    counts = Counter(label for window in truth_windows for label in (window.hap0, window.hap1))
    denominator = 2 * len(truth_windows)
    return {
        label: {"fraction": counts[label] / denominator} for label in sorted(cal.SUPERPOPULATIONS)
    }


def _complete_coverage_metrics(truth_windows, *, assigned_keys=None):
    assigned_keys = (
        {window.key for window in truth_windows} if assigned_keys is None else set(assigned_keys)
    )
    truth_by_chrom = Counter(window.chrom for window in truth_windows)
    assigned_by_chrom = Counter(key[0] for key in assigned_keys)
    expected_by = {chrom: 2 * truth_by_chrom[chrom] for chrom in cal.AUTOSOMES}
    valid_by = {chrom: 2 * assigned_by_chrom[chrom] for chrom in cal.AUTOSOMES}
    model_by = {
        chrom: {
            "matched": 1,
            "total": 2,
            "allele_mismatch": 0,
            "match_rate": 0.5,
        }
        for chrom in cal.AUTOSOMES
    }
    expected = sum(expected_by.values())
    valid = sum(valid_by.values())
    return {
        "schema_version": 1,
        "model_denominators": {
            "complete": True,
            "unreadable_autosomes": [],
        },
        "emitted_markers": {
            "total": 22,
            "by_autosome": {chrom: 1 for chrom in cal.AUTOSOMES},
        },
        "model_markers": {
            "aggregate": {
                "matched": 22,
                "total": 44,
                "allele_mismatch": 0,
                "match_rate": 0.5,
            },
            "by_autosome": model_by,
        },
        "phased_autosomes": {
            "count": 22,
            "identities": list(range(1, 23)),
        },
        "analyzed_autosomes": {
            "count": 22,
            "identities": list(range(1, 23)),
        },
        "haplotype_windows": {
            "expected": expected,
            "valid_assigned": valid,
            "assignment_rate": valid / expected,
            "expected_by_autosome": expected_by,
            "valid_assigned_by_autosome": valid_by,
        },
        "per_source": {"unknown": {"hits": 22, "drops": 0}},
    }


def _write_reference_bundle(
    tmp_path: Path,
    *,
    samples_by_chrom: dict[str, tuple[str, ...]] | None = None,
    excluded_iids=("DONOR1",),
    model_geometry: dict[str, tuple[int, int, tuple[int, ...]]] | None = None,
    population_order: tuple[str, ...] = cal.POPULATION_ORDER,
):
    bundle_dir = tmp_path / "calibration-bundle"
    panel_dir = bundle_dir / "phasing_panel"
    panel_dir.mkdir(parents=True)
    source_bundle_sha256 = "a" * 64
    inherited_paths = [
        "metadata.json",
        "beagle/beagle.jar",
        "liftover/array_site_mapping.tsv",
        *(
            f"gnomix_models/chr{chrom}/{filename}"
            for chrom in cal.AUTOSOMES
            for filename in ("metadata.npz", "base_coefs.npz", "smoother.json")
        ),
        *(f"genetic_maps/plink.chrchr{chrom}.GRCh38.map" for chrom in cal.AUTOSOMES),
    ]
    inherited_manifest = {}
    for relative in inherited_paths:
        path = bundle_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative.startswith("gnomix_models/") and relative.endswith("/metadata.npz"):
            chrom = relative.split("/")[1].removeprefix("chr")
            marker_count, window_size, positions = (model_geometry or {}).get(
                chrom,
                (2, 2, (int(chrom) * 1000, int(chrom) * 1000 + 99)),
            )
            assert len(positions) == marker_count
            np.savez(
                path,
                C=np.asarray(marker_count, dtype=np.int64),
                M=np.asarray(window_size, dtype=np.int64),
                W=np.asarray(marker_count // window_size, dtype=np.int64),
                snp_pos=np.asarray(positions, dtype=np.int64),
                population_order=np.asarray(population_order),
            )
        else:
            content = (
                json.dumps({"bundle_version": "2.0.0", "window_count": 22}).encode()
                if relative == "metadata.json"
                else f"fixture inherited bytes: {relative}\n".encode()
            )
            path.write_bytes(content)
        inherited_manifest[relative] = {
            "sha256": cal.sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
    panel_manifest = {}
    for chrom in cal.AUTOSOMES:
        sample_iids = tuple((samples_by_chrom or {}).get(chrom, ("REF1", "REF2")))
        vcf_path = panel_dir / f"ref_panel_chr{chrom}.vcf.gz"
        with gzip.open(vcf_path, "wt", encoding="utf-8") as handle:
            handle.write("##fileformat=VCFv4.2\n")
            handle.write(
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT"
                + "".join(f"\t{iid}" for iid in sample_iids)
                + "\n"
            )
        index_path = Path(f"{vcf_path}.tbi")
        index_path.write_bytes(f"index-{chrom}".encode())
        panel_manifest[chrom] = {
            "sample_count": len(sample_iids),
            "sample_ids_sha256": cal.sha256_json(sorted(sample_iids)),
            "vcf_sha256": cal.sha256_file(vcf_path),
            "index_sha256": cal.sha256_file(index_path),
            "vcf_size_bytes": vcf_path.stat().st_size,
            "index_size_bytes": index_path.stat().st_size,
        }
    manifest_path = tmp_path / "calibration-reference.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_bundle_artifact_sha256": source_bundle_sha256,
                "excluded_iids": list(excluded_iids),
                "phasing_panel": panel_manifest,
                "inherited_files": inherited_manifest,
                "inherited_tree_sha256": cal.sha256_json(inherited_manifest),
                "resolved_liftover_file": "liftover/array_site_mapping.tsv",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    manifest = cal.read_calibration_reference_manifest(
        manifest_path,
        source_bundle_sha256,
    )
    verification_path = tmp_path / "reference-verification.json"
    cal.atomic_write_json(
        verification_path,
        cal.build_reference_verification(bundle_dir, manifest),
    )
    verification = cal.read_reference_verification(verification_path, manifest)
    return bundle_dir, manifest, verification


DONOR_METADATA_HEADER = (
    "sample_id",
    "population",
    "model_class",
    "release",
    "hard_filtered",
    "release_related",
    "all_samples_related",
)


def _write_donor_metadata(path: Path, rows: list[dict[str, object]]) -> Path:
    lines = ["\t".join(DONOR_METADATA_HEADER)]
    lines.extend("\t".join(str(row[field]) for field in DONOR_METADATA_HEADER) for row in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _donor_row(iid: str, model_class: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "sample_id": iid,
        "population": f"{model_class}-population",
        "model_class": model_class,
        "release": "true",
        "hard_filtered": "false",
        "release_related": "false",
        "all_samples_related": "false",
    }
    row.update(overrides)
    return row


def _donor_metadata_contract(path: Path) -> dict[str, object]:
    return {
        "source_url": "https://example.invalid/pinned-donor-metadata.tsv",
        "sha256": cal.sha256_file(path),
        "iid_field": "sample_id",
        "population_field": "population",
        "model_class_field": "model_class",
        "release_field": "release",
        "hard_filtered_field": "hard_filtered",
        "release_related_field": "release_related",
        "all_samples_related_field": "all_samples_related",
    }


def _simulation_models(bundle_dir: Path, reference_manifest) -> dict[str, object]:
    per_chromosome = {}
    population_orders: set[tuple[str, ...]] = set()
    for chrom in cal.AUTOSOMES:
        metadata_relative = f"gnomix_models/chr{chrom}/metadata.npz"
        map_relative = f"genetic_maps/plink.chrchr{chrom}.GRCh38.map"
        with np.load(bundle_dir / metadata_relative, allow_pickle=False) as metadata:
            marker_count = int(metadata["C"].item())
            window_size = int(metadata["M"].item())
            window_count = int(metadata["W"].item())
            population_orders.add(tuple(str(value) for value in metadata["population_order"]))
        per_chromosome[chrom] = {
            "metadata_npz_sha256": reference_manifest.inherited_files[metadata_relative]["sha256"],
            "genetic_map_sha256": reference_manifest.inherited_files[map_relative]["sha256"],
            "C": marker_count,
            "M": window_size,
            "W": window_count,
        }
    assert len(population_orders) == 1
    population_order = population_orders.pop()
    return {
        "population_order": list(population_order),
        "population_order_sha256": cal.sha256_json(list(population_order)),
        "per_chromosome": per_chromosome,
    }


def _simulation_entry(
    iid: str,
    split: str,
    donor_iids: tuple[str, ...],
    *,
    validation_stratum: str = "cross-superpopulation-mosaic",
    marker_truth_sha256: str = "1" * 64,
    fixture_sha256: str = "2" * 64,
    window_truth_sha256: str = "3" * 64,
    tract_truth_sha256: str = "b" * 64,
    generation: int = 8,
) -> dict[str, object]:
    return {
        "iid": iid,
        "split": split,
        "simulation_kind": "admixed_mosaic",
        "generation": generation,
        "validation_stratum": validation_stratum,
        "donor_iids": list(donor_iids),
        "target_marker_ancestry_fractions": {
            label: ("0.16" if label == "AFR" else "0.14") for label in cal.POPULATION_ORDER
        },
        "fraction_absolute_tolerance": "0.1",
        "marker_truth_file": f"{iid}.marker-truth.tsv",
        "marker_truth_sha256": marker_truth_sha256,
        "tract_truth_file": f"{iid}.tract-truth.tsv",
        "tract_truth_sha256": tract_truth_sha256,
        "fixture_file": f"{iid}.tsv",
        "fixture_sha256": fixture_sha256,
        "window_truth_file": f"{iid}.truth.tsv",
        "window_truth_sha256": window_truth_sha256,
    }


def _write_simulation_manifest(
    tmp_path: Path,
    *,
    bundle_dir: Path,
    reference_manifest,
    donor_metadata_path: Path,
    donor_iids: frozenset[str],
    isolation_iids: frozenset[str],
    simulations: list[dict[str, object]],
    dataset_id: str = "public-release",
):
    path = tmp_path / "simulation-manifest.json"
    relationships_path = tmp_path / "validation-relationships.tsv"
    relationships_path.write_text(
        "iid_a\tiid_b\trelationship\n"
        + "".join(f"{iid}\t{iid}\tself\n" for iid in sorted(isolation_iids)),
        encoding="utf-8",
    )
    payload = {
        "schema_version": 2,
        "dataset_id": dataset_id,
        "source_bundle_artifact_sha256": (reference_manifest.source_bundle_artifact_sha256),
        "model_frozen_before_generation": True,
        "generator": {
            "name": "yeliztli-founder-mosaic-v1",
            "code_revision": "d" * 40,
            "script_sha256": "4" * 64,
            "environment_lock_sha256": "5" * 64,
            "rng_library": "numpy",
            "rng_version": "2.4.0",
            "rng_algorithm": "PCG64",
            "seed": 17,
        },
        "simulation_protocol": {
            "schema": "founder_mosaic_v1",
            "genome_build": "GRCh38",
            "breakpoint_process": "genetic_map_poisson_v1",
            "admixture_model": "single_pulse_v1",
            "recombination_map_sha256_by_autosome": {
                chrom: reference_manifest.inherited_files[
                    f"genetic_maps/plink.chrchr{chrom}.GRCh38.map"
                ]["sha256"]
                for chrom in cal.AUTOSOMES
            },
            "max_breakpoints_per_haplotype_by_autosome": {chrom: 100 for chrom in cal.AUTOSOMES},
            "allowed_generations": [4, 8],
            "minimums": {
                "founders_per_class_per_split": 2,
                "simulations_per_class_per_split": 2,
                "truth_haplotype_windows_per_class_per_split": 1,
            },
        },
        "relationships": {
            "filename": relationships_path.name,
            "sha256": cal.sha256_file(relationships_path),
        },
        "donor_metadata": _donor_metadata_contract(donor_metadata_path),
        "donor_haplotype_source": {
            "genome_build": "GRCh38",
            "per_chromosome_vcf_sha256": {
                chrom: cal.sha256_json(["donor-source-vcf", chrom]) for chrom in cal.AUTOSOMES
            },
            "per_chromosome_vcf_index_sha256": {
                chrom: cal.sha256_json(["donor-source-vcf-index", chrom])
                for chrom in cal.AUTOSOMES
            },
        },
        "models": _simulation_models(bundle_dir, reference_manifest),
        "protected_iids_sha256": cal.sha256_json(sorted(isolation_iids)),
        "simulations": simulations,
        "truth_projection": {
            "schema": "gnomix-window-mode-v1",
            "regular_window": "[w*M,(w+1)*M)",
            "final_window": "[(W-1)*M,C)",
            "tie_rule": "lowest production-model ancestry code",
        },
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return cal.read_simulation_manifest(
        path,
        dataset_id=dataset_id,
        source_bundle_artifact_sha256=(reference_manifest.source_bundle_artifact_sha256),
        donor_iids=donor_iids,
        isolation_iids=isolation_iids,
    )


def _write_simulation_verification_report(
    tmp_path: Path,
    simulation_manifest,
    dataset_split: str,
    confirmation_policy_path: Path | None = None,
) -> Path:
    expected_rows = sum(
        int(entry["C"]) for entry in simulation_manifest.models["per_chromosome"].values()
    )
    selected_iids = simulation_manifest.splits[dataset_split]
    path = tmp_path / "simulation-verification.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "dataset_id": simulation_manifest.dataset_id,
                "dataset_split": dataset_split,
                "split_commitment_sha256": cal.compute_simulation_split_commitment(
                    simulation_manifest,
                    dataset_split,
                ),
                "verification_status": "passed",
                "simulation_manifest": {
                    "filename": simulation_manifest.path.name,
                    "sha256": simulation_manifest.sha256,
                    "size_bytes": simulation_manifest.path.stat().st_size,
                },
                "verifier": {
                    "name": "independent-donor-vcf-replay",
                    "code_revision": "d" * 40,
                    "script_sha256": cal.sha256_file(
                        REPO_ROOT / "scripts/lai_bundle_v2/06g_verify_simulation.py"
                    ),
                    "environment_lock_sha256": cal.sha256_file(REPO_ROOT / "uv.lock"),
                    "distinct_from_generator_script_sha256": True,
                },
                "generator_script_snapshot": {
                    "filename": "generate-simulations.py",
                    "sha256": simulation_manifest.generator["script_sha256"],
                    "size_bytes": 1,
                },
                "generator_environment_lock_snapshot": {
                    "filename": "generator-environment.lock",
                    "sha256": simulation_manifest.generator["environment_lock_sha256"],
                    "size_bytes": 1,
                },
                "generator_code_revision": simulation_manifest.generator["code_revision"],
                "donor_haplotype_source_sha256": cal.sha256_json(
                    simulation_manifest.donor_haplotype_source
                ),
                "simulation_manifest_models_sha256": cal.sha256_json(simulation_manifest.models),
                "source_vcf_haplotypes_verified": True,
                "fixture_genotypes_verified": True,
                "source_vcf_marker_rsids_verified": True,
                "marker_truth_tracts_reconciled": True,
                "source_snapshots": {
                    chrom: {
                        "vcf": {
                            "filename": f"donor-chr{chrom}.vcf.gz",
                            "sha256": simulation_manifest.donor_haplotype_source[
                                "per_chromosome_vcf_sha256"
                            ][chrom],
                            "size_bytes": 1,
                        },
                        "index": {
                            "filename": f"donor-chr{chrom}.vcf.gz.tbi",
                            "sha256": simulation_manifest.donor_haplotype_source[
                                "per_chromosome_vcf_index_sha256"
                            ][chrom],
                            "size_bytes": 1,
                        },
                        "sample_count": 28,
                        "sample_ids_sha256": "c" * 64,
                        "records_scanned": expected_rows,
                        "model_markers_verified": simulation_manifest.models["per_chromosome"][
                            chrom
                        ]["C"],
                    }
                    for chrom in cal.AUTOSOMES
                },
                "simulations": {
                    iid: {
                        "marker_truth_sha256": entry["marker_truth_sha256"],
                        "tract_truth_sha256": entry["tract_truth_sha256"],
                        "fixture_sha256": entry["fixture_sha256"],
                        "marker_rows_verified": expected_rows,
                        "haplotype_alleles_verified": expected_rows * 2,
                        "missing_rows": 0,
                        "mismatches": 0,
                    }
                    for iid, entry in simulation_manifest.simulations.items()
                    if iid in selected_iids
                },
                "totals": {
                    "autosomes_verified": 22,
                    "simulations_verified": len(selected_iids),
                    "marker_rows_verified": expected_rows * len(selected_iids),
                    "haplotype_alleles_verified": expected_rows * len(selected_iids) * 2,
                    "missing_rows": 0,
                    "mismatches": 0,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    if dataset_split == "final_confirmation":
        assert confirmation_policy_path is not None
        policy = cal.read_confirmation_policy(
            confirmation_policy_path,
            dataset_id=simulation_manifest.dataset_id,
            bundle_artifact_sha256=simulation_manifest.source_bundle_artifact_sha256,
            simulation_manifest_sha256=simulation_manifest.sha256,
            code_revision="d" * 40,
            final_confirmation_split_commitment_sha256=(
                cal.compute_final_confirmation_split_commitment(simulation_manifest)
            ),
            expected_confirmation_policy_sha256=cal.sha256_file(confirmation_policy_path),
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["confirmation_policy"] = cal.confirmation_policy_provenance(policy)
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _marker_truth_projection_case(tmp_path: Path, *, cached_mismatch: bool = False):
    native_population_order = ("CSA", "AFR", "OCE", "EUR", "MID", "AMR", "EAS")
    model_geometry = {
        "1": (5, 2, (1000, 1010, 1020, 1030, 1099)),
    }
    bundle_dir, reference_manifest, _verification = _write_reference_bundle(
        tmp_path,
        model_geometry=model_geometry,
        population_order=native_population_order,
    )
    donor_metadata_path = _write_donor_metadata(
        tmp_path / "donor-metadata.tsv",
        [
            _donor_row("DONOR_AFR", "AFR"),
            _donor_row("DONOR_EAS", "EAS"),
            _donor_row("DONOR_EUR", "EUR"),
            _donor_row("DONOR_OCE", "OCE"),
        ],
    )
    simulation_manifest = cal.SimulationManifest(
        path=tmp_path / "simulation-manifest.json",
        sha256="6" * 64,
        dataset_id="public-release",
        source_bundle_artifact_sha256=(reference_manifest.source_bundle_artifact_sha256),
        generator={},
        simulation_protocol={},
        relationships={},
        donor_metadata=_donor_metadata_contract(donor_metadata_path),
        donor_haplotype_source={},
        models=_simulation_models(bundle_dir, reference_manifest),
        truth_projection={},
        splits={},
        simulations={},
    )
    donor_iids = frozenset({"DONOR_AFR", "DONOR_EAS", "DONOR_EUR", "DONOR_OCE"})
    donor_labels = cal.read_donor_labels(
        donor_metadata_path,
        simulation_manifest,
        donor_iids,
    )
    model_specs = cal.load_truth_model_specs(
        bundle_dir,
        simulation_manifest,
        reference_manifest,
    )

    expected_windows = [
        cal.TruthWindow("1", 1000, 1010, "OCE", "EUR"),
        cal.TruthWindow("1", 1020, 1099, "EAS", "AFR"),
    ]
    expected_windows.extend(
        cal.TruthWindow(chrom, int(chrom) * 1000, int(chrom) * 1000 + 99, "EUR", "EUR")
        for chrom in cal.AUTOSOMES[1:]
    )
    if cached_mismatch:
        expected_windows[0] = cal.TruthWindow("1", 1000, 1010, "EUR", "EUR")
    fixture = _fixture(
        tmp_path,
        iid="SIMCAL",
        truth_windows=tuple(expected_windows),
    )

    rows = ["\t".join(cal.MARKER_TRUTH_HEADER)]
    contributions: dict[tuple[str, int], list[tuple[str, int]]] = {}
    for chrom in cal.AUTOSOMES:
        spec = model_specs[chrom]
        for marker_index, position in enumerate(spec.positions):
            if chrom == "1":
                hap0_donor = (
                    "DONOR_OCE",
                    "DONOR_EUR",
                    "DONOR_EAS",
                    "DONOR_EAS",
                    "DONOR_EUR",
                )[marker_index]
                hap1_donor = (
                    "DONOR_EUR",
                    "DONOR_EUR",
                    "DONOR_AFR",
                    "DONOR_AFR",
                    "DONOR_EUR",
                )[marker_index]
            else:
                hap0_donor = hap1_donor = "DONOR_EUR"
            contributions.setdefault((chrom, 0), []).append((hap0_donor, 0))
            contributions.setdefault((chrom, 1), []).append((hap1_donor, 1))
            rows.append(
                "\t".join(
                    (
                        fixture.iid,
                        chrom,
                        str(marker_index),
                        str(int(position)),
                        f"rs{chrom}_{marker_index}",
                        hap0_donor,
                        "0",
                        hap1_donor,
                        "1",
                    )
                )
            )
    marker_truth_path = tmp_path / "SIMCAL.marker-truth.tsv"
    marker_truth_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    tract_rows = ["\t".join(cal.TRACT_TRUTH_HEADER)]
    ancestry_counts = Counter()
    for (chrom, haplotype), values in contributions.items():
        start = 0
        for index in range(1, len(values) + 1):
            if index < len(values) and values[index] == values[start]:
                continue
            donor, source_hap = values[start]
            tract_rows.append(
                "\t".join(
                    (
                        fixture.iid,
                        chrom,
                        str(haplotype),
                        str(start),
                        str(index),
                        donor,
                        str(source_hap),
                    )
                )
            )
            ancestry_counts[donor_labels[donor]] += index - start
            start = index
    tract_truth_path = tmp_path / "SIMCAL.tract-truth.tsv"
    tract_truth_path.write_text("\n".join(tract_rows) + "\n", encoding="utf-8")
    total = sum(ancestry_counts.values())
    tract_truth = cal.read_tract_truth(
        tract_truth_path,
        iid=fixture.iid,
        model_specs=model_specs,
        donor_iids=donor_iids,
        donor_labels=donor_labels,
        max_breakpoints_by_autosome={chrom: 100 for chrom in cal.AUTOSOMES},
        target_marker_ancestry_fractions={
            label: str(Decimal(ancestry_counts[label]) / Decimal(total))
            for label in cal.POPULATION_ORDER
        },
        fraction_absolute_tolerance="0.000001",
        expected_sha256=cal.sha256_file(tract_truth_path),
    )
    return {
        "fixture": fixture,
        "marker_truth_path": marker_truth_path,
        "tract_truth": tract_truth,
        "model_specs": model_specs,
        "donor_labels": donor_labels,
        "donor_iids": donor_iids,
    }


def _write_multiclass_truth(
    tmp_path: Path,
    *,
    iid: str,
    bundle_dir: Path,
    donors_by_class: dict[str, str | tuple[str, ...]],
) -> tuple[Path, Path, Path]:
    normalized_donors = {
        label: (donors if isinstance(donors, tuple) else (donors,))
        for label, donors in donors_by_class.items()
    }
    labels_by_donor = {
        donor: label for label, donors in normalized_donors.items() for donor in donors
    }
    rows = ["\t".join(cal.MARKER_TRUTH_HEADER)]
    tract_rows = ["\t".join(cal.TRACT_TRUTH_HEADER)]
    windows: list = []
    for chrom in cal.AUTOSOMES:
        with np.load(
            bundle_dir / f"gnomix_models/chr{chrom}/metadata.npz",
            allow_pickle=False,
        ) as metadata:
            positions = metadata["snp_pos"]
            window_size = int(metadata["M"].item())
            population_order = tuple(str(value) for value in metadata["population_order"])
        hap_counts = (Counter(), Counter())
        truth_class = cal.POPULATION_ORDER[(int(chrom) - 1) % len(cal.POPULATION_ORDER)]
        truth_donors = normalized_donors[truth_class]
        hap0_donor = truth_donors[(int(chrom) - 1) % len(truth_donors)]
        hap1_donor = truth_donors[int(chrom) % len(truth_donors)]
        tract_rows.extend(
            (
                "\t".join((iid, chrom, "0", "0", str(len(positions)), hap0_donor, "0")),
                "\t".join((iid, chrom, "1", "0", str(len(positions)), hap1_donor, "1")),
            )
        )
        for marker_index, position in enumerate(positions):
            rows.append(
                "\t".join(
                    (
                        iid,
                        chrom,
                        str(marker_index),
                        str(int(position)),
                        f"rs{chrom}_{marker_index}",
                        hap0_donor,
                        "0",
                        hap1_donor,
                        "1",
                    )
                )
            )
            hap_counts[0][population_order.index(labels_by_donor[hap0_donor])] += 1
            hap_counts[1][population_order.index(labels_by_donor[hap1_donor])] += 1
        assert len(positions) == window_size
        window_labels = []
        for counts in hap_counts:
            maximum = max(counts.values())
            code = min(code for code, count in counts.items() if count == maximum)
            window_labels.append(population_order[code])
        windows.append(
            cal.TruthWindow(
                chrom,
                int(positions[0]),
                int(positions[-1]),
                window_labels[0],
                window_labels[1],
            )
        )
    marker_truth_path = tmp_path / f"{iid}.marker-truth.tsv"
    marker_truth_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    tract_truth_path = tmp_path / f"{iid}.tract-truth.tsv"
    tract_truth_path.write_text("\n".join(tract_rows) + "\n", encoding="utf-8")
    truth_path = _write_truth(tmp_path / f"{iid}.truth.tsv", windows)
    return marker_truth_path, truth_path, tract_truth_path


def test_local_truth_parser_accepts_exact_22_autosome_truth(tmp_path):
    truth_path = _write_truth(tmp_path / "truth.tsv")

    truth = cal.read_local_truth(truth_path)

    assert len(truth) == 22
    assert truth[0].key == ("1", 1000, 1099)
    assert truth[-1].key == ("22", 22000, 22099)


def test_local_truth_parser_rejects_duplicate_windows(tmp_path):
    windows = _default_truth_windows()
    truth_path = _write_truth(tmp_path / "duplicate.tsv", (*windows, windows[0]))

    with pytest.raises(ValueError, match="duplicate truth window"):
        cal.read_local_truth(truth_path)


def test_local_truth_parser_rejects_missing_autosome(tmp_path):
    truth_path = _write_truth(tmp_path / "missing.tsv", _default_truth_windows()[:-1])

    with pytest.raises(ValueError, match="missing autosomes.*22"):
        cal.read_local_truth(truth_path)


def test_local_truth_parser_rejects_noncanonical_ancestry(tmp_path):
    windows = list(_default_truth_windows())
    windows[0] = cal.TruthWindow("1", 1000, 1099, "ASN", "EUR")
    truth_path = _write_truth(tmp_path / "noncanonical.tsv", windows)

    with pytest.raises(ValueError, match="truth labels must be canonical"):
        cal.read_local_truth(truth_path)


def test_fixture_parser_is_strict_and_records_keep_first_dedup(tmp_path):
    fixture_path = tmp_path / "simulated.tsv"
    fixture_path.write_text(
        "rsid\tchrom\tpos\ta1\ta2\n"
        "rs1\t1\t100\ta\tg\n"
        "rs1\t1\t101\tA\tA\n"
        "rsX\tX\t200\tC\tT\n"
        "rs2\tchr2\t300\tG\tG\n",
        encoding="utf-8",
    )
    truth_path = _write_truth(tmp_path / "simulated.truth.tsv")

    fixture = cal.read_fixture(
        "SIMTEST",
        "cross-superpopulation-mosaic",
        fixture_path,
        truth_path,
    )

    assert [(m.rsid, m.chrom, m.pos, m.genotype) for m in fixture.markers] == [
        ("rs1", "1", 100, "AG"),
        ("rs2", "2", 300, "GG"),
    ]
    assert fixture.validation_stratum == "cross-superpopulation-mosaic"
    assert len(fixture.truth_windows) == 22
    assert fixture.parsing == {
        "autosomal_markers": 2,
        "duplicate_rsid_rows": 1,
        "non_autosomal_rows": 1,
    }


def test_fixture_parser_rejects_non_diploid_acgt_alleles(tmp_path):
    fixture_path = tmp_path / "invalid.tsv"
    fixture_path.write_text("rs1\t1\t100\tA\t-\n", encoding="utf-8")

    with pytest.raises(ValueError, match="one A/C/G/T base per allele"):
        cal.read_fixture(
            "SIMTEST",
            "mosaic",
            fixture_path,
            tmp_path / "not-reached.truth.tsv",
        )


def test_donor_metadata_resolves_canonical_model_classes_after_strict_qc(tmp_path):
    metadata_path = _write_donor_metadata(
        tmp_path / "donor-metadata.tsv",
        [
            _donor_row(
                "DONOR1",
                "afr",
                release="1",
                hard_filtered="0",
                release_related="0",
                all_samples_related="0",
            ),
            _donor_row("DONOR2", "EUR"),
            _donor_row("NOT_SELECTED", "OCE", release="not-a-boolean"),
        ],
    )
    manifest = cal.SimulationManifest(
        path=tmp_path / "simulation.json",
        sha256="1" * 64,
        dataset_id="public-release",
        source_bundle_artifact_sha256="2" * 64,
        generator={},
        simulation_protocol={},
        relationships={},
        donor_metadata=_donor_metadata_contract(metadata_path),
        donor_haplotype_source={},
        models={},
        truth_projection={},
        splits={},
        simulations={},
    )

    labels = cal.read_donor_labels(
        metadata_path,
        manifest,
        frozenset({"DONOR1", "DONOR2"}),
    )

    assert labels == {"DONOR1": "AFR", "DONOR2": "EUR"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"release": "false"},
        {"hard_filtered": "true"},
        {"release_related": "true"},
        {"all_samples_related": "true"},
    ],
)
def test_donor_metadata_rejects_any_nonrelease_or_related_qc_flag(
    tmp_path,
    overrides,
):
    metadata_path = _write_donor_metadata(
        tmp_path / "donor-metadata.tsv",
        [_donor_row("DONOR1", "AFR", **overrides)],
    )
    manifest = cal.SimulationManifest(
        path=tmp_path / "simulation.json",
        sha256="1" * 64,
        dataset_id="public-release",
        source_bundle_artifact_sha256="2" * 64,
        generator={},
        simulation_protocol={},
        relationships={},
        donor_metadata=_donor_metadata_contract(metadata_path),
        donor_haplotype_source={},
        models={},
        truth_projection={},
        splits={},
        simulations={},
    )

    with pytest.raises(ValueError, match="not an unrelated release-QC sample"):
        cal.read_donor_labels(metadata_path, manifest, frozenset({"DONOR1"}))


def test_simulation_manifest_rejects_founder_overlap_between_splits(tmp_path):
    bundle_dir, reference_manifest, _verification = _write_reference_bundle(tmp_path)
    metadata_path = _write_donor_metadata(
        tmp_path / "donor-metadata.tsv",
        [_donor_row("DONOR1", "AFR"), _donor_row("DONOR2", "EUR")],
    )

    with pytest.raises(
        ValueError,
        match="calibration and final-confirmation donors overlap.*DONOR1",
    ):
        _write_simulation_manifest(
            tmp_path,
            bundle_dir=bundle_dir,
            reference_manifest=reference_manifest,
            donor_metadata_path=metadata_path,
            donor_iids=frozenset({"DONOR1", "DONOR2"}),
            isolation_iids=frozenset({"DONOR1", "DONOR2"}),
            simulations=[
                _simulation_entry("SIMCAL", "calibration", ("DONOR1",)),
                _simulation_entry("SIMFINAL", "final_confirmation", ("DONOR1",)),
            ],
        )


def test_simulation_manifest_requires_exact_validation_donor_union(tmp_path):
    bundle_dir, reference_manifest, _verification = _write_reference_bundle(tmp_path)
    metadata_path = _write_donor_metadata(
        tmp_path / "donor-metadata.tsv",
        [
            _donor_row("DONOR1", "AFR"),
            _donor_row("DONOR2", "EUR"),
            _donor_row("UNUSED", "EAS"),
        ],
    )

    with pytest.raises(
        ValueError,
        match="simulation donor union does not match.*unused=.*UNUSED",
    ):
        _write_simulation_manifest(
            tmp_path,
            bundle_dir=bundle_dir,
            reference_manifest=reference_manifest,
            donor_metadata_path=metadata_path,
            donor_iids=frozenset({"DONOR1", "DONOR2", "UNUSED"}),
            isolation_iids=frozenset({"DONOR1", "DONOR2", "UNUSED"}),
            simulations=[
                _simulation_entry("SIMCAL", "calibration", ("DONOR1",)),
                _simulation_entry("SIMFINAL", "final_confirmation", ("DONOR2",)),
            ],
        )


def test_simulation_manifest_preserves_native_model_population_order(tmp_path):
    native_order = ("CSA", "AFR", "OCE", "EUR", "MID", "AMR", "EAS")
    bundle_dir, reference_manifest, _verification = _write_reference_bundle(
        tmp_path,
        population_order=native_order,
    )
    metadata_path = _write_donor_metadata(
        tmp_path / "donor-metadata.tsv",
        [_donor_row("DONOR1", "AFR"), _donor_row("DONOR2", "EUR")],
    )

    manifest = _write_simulation_manifest(
        tmp_path,
        bundle_dir=bundle_dir,
        reference_manifest=reference_manifest,
        donor_metadata_path=metadata_path,
        donor_iids=frozenset({"DONOR1", "DONOR2"}),
        isolation_iids=frozenset({"DONOR1", "DONOR2"}),
        simulations=[
            _simulation_entry("SIMCAL", "calibration", ("DONOR1",)),
            _simulation_entry("SIMFINAL", "final_confirmation", ("DONOR2",)),
        ],
    )

    assert manifest.models["population_order"] == list(native_order)


def _confirmation_commitment_manifest(tmp_path: Path, simulations=None):
    entries = simulations or {
        "SIMFINAL1": {
            "split": "final_confirmation",
            "fixture_sha256": "1" * 64,
            "marker_truth_sha256": "2" * 64,
            "tract_truth_sha256": "3" * 64,
            "window_truth_sha256": "4" * 64,
            "donor_iids": ("DONOR1", "DONOR2"),
            "generation": 4,
            "validation_stratum": "mosaic-a",
        },
        "SIMFINAL2": {
            "split": "final_confirmation",
            "fixture_sha256": "5" * 64,
            "marker_truth_sha256": "6" * 64,
            "tract_truth_sha256": "7" * 64,
            "window_truth_sha256": "8" * 64,
            "donor_iids": ("DONOR3", "DONOR4"),
            "generation": 8,
            "validation_stratum": "mosaic-b",
        },
    }
    return cal.SimulationManifest(
        path=tmp_path / "simulation.json",
        sha256="9" * 64,
        dataset_id="public-release",
        source_bundle_artifact_sha256="a" * 64,
        generator={},
        simulation_protocol={},
        relationships={},
        donor_metadata={},
        donor_haplotype_source={},
        models={},
        truth_projection={},
        splits={"calibration": ("SIMCAL",), "final_confirmation": tuple(sorted(entries))},
        simulations={"SIMCAL": {}, **entries},
    )


def test_final_confirmation_commitment_has_an_explicit_canonical_preimage(tmp_path):
    manifest = _confirmation_commitment_manifest(tmp_path)

    commitment = cal.compute_final_confirmation_split_commitment(manifest)

    assert commitment == cal.sha256_json(
        {
            "schema_version": 1,
            "dataset_id": "public-release",
            "split": "final_confirmation",
            "simulations": [
                {
                    "iid": "SIMFINAL1",
                    "fixture_sha256": "1" * 64,
                    "marker_truth_sha256": "2" * 64,
                    "tract_truth_sha256": "3" * 64,
                    "window_truth_sha256": "4" * 64,
                    "donor_iids": ["DONOR1", "DONOR2"],
                    "generation": 4,
                    "validation_stratum": "mosaic-a",
                },
                {
                    "iid": "SIMFINAL2",
                    "fixture_sha256": "5" * 64,
                    "marker_truth_sha256": "6" * 64,
                    "tract_truth_sha256": "7" * 64,
                    "window_truth_sha256": "8" * 64,
                    "donor_iids": ["DONOR3", "DONOR4"],
                    "generation": 8,
                    "validation_stratum": "mosaic-b",
                },
            ],
        }
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fixture_sha256", "a" * 64),
        ("marker_truth_sha256", "b" * 64),
        ("tract_truth_sha256", "c" * 64),
        ("window_truth_sha256", "d" * 64),
        ("donor_iids", ("DONOR1", "DONOR5")),
        ("generation", 8),
        ("validation_stratum", "different-stratum"),
    ],
)
def test_final_confirmation_commitment_binds_every_sealed_simulation_field(tmp_path, field, value):
    original = _confirmation_commitment_manifest(tmp_path)
    simulations = {
        iid: dict(entry)
        for iid, entry in original.simulations.items()
        if iid in original.splits["final_confirmation"]
    }
    simulations["SIMFINAL1"][field] = value
    changed = _confirmation_commitment_manifest(tmp_path, simulations)

    assert cal.compute_final_confirmation_split_commitment(
        changed
    ) != cal.compute_final_confirmation_split_commitment(original)


def test_split_population_coverage_requires_all_seven_classes(tmp_path):
    manifest = cal.SimulationManifest(
        path=tmp_path / "simulation.json",
        sha256="1" * 64,
        dataset_id="public-release",
        source_bundle_artifact_sha256="2" * 64,
        generator={},
        simulation_protocol={},
        relationships={},
        donor_metadata={},
        donor_haplotype_source={},
        models={},
        truth_projection={},
        splits={"calibration": ("SIMCAL",), "final_confirmation": ("SIMFINAL",)},
        simulations={
            "SIMCAL": {"donor_iids": ("CAL_AFR", "CAL_EUR")},
            "SIMFINAL": {"donor_iids": tuple(f"FINAL_{label}" for label in cal.POPULATION_ORDER)},
        },
    )
    donor_labels = {
        "CAL_AFR": "AFR",
        "CAL_EUR": "EUR",
        **{f"FINAL_{label}": label for label in cal.POPULATION_ORDER},
    }

    with pytest.raises(
        ValueError,
        match="split 'calibration' lacks required model class.*AMR.*OCE",
    ):
        cal.validate_split_population_coverage(manifest, donor_labels)


def test_marker_truth_projects_final_remainder_and_lowest_code_tie(tmp_path):
    case = _marker_truth_projection_case(tmp_path)

    fixture = cal.attach_marker_truth(
        case["fixture"],
        case["marker_truth_path"],
        tract_truth=case["tract_truth"],
        model_specs=case["model_specs"],
        donor_labels=case["donor_labels"],
        donor_iids=case["donor_iids"],
        expected_sha256=cal.sha256_file(case["marker_truth_path"]),
        expected_truth_donors=case["donor_iids"],
    )

    # Native order has OCE before EUR, opposite their canonical ordering.  The
    # exact tie must therefore select OCE from the pinned model code space.
    assert fixture.truth_windows[:2] == (
        cal.TruthWindow("1", 1000, 1010, "OCE", "EUR"),
        # C=5, M=2, W=2: the final window consumes indices 2..4, including remainder.
        cal.TruthWindow("1", 1020, 1099, "EAS", "AFR"),
    )
    assert fixture.marker_truth_rows == 47
    assert fixture.marker_truth_sha256 == cal.sha256_file(case["marker_truth_path"])
    assert fixture.truth_donor_iids == case["donor_iids"]


def test_marker_truth_rejects_mismatched_cached_window_truth(tmp_path):
    case = _marker_truth_projection_case(tmp_path, cached_mismatch=True)

    with pytest.raises(
        ValueError,
        match="cached window truth does not match marker truth",
    ):
        cal.attach_marker_truth(
            case["fixture"],
            case["marker_truth_path"],
            tract_truth=case["tract_truth"],
            model_specs=case["model_specs"],
            donor_labels=case["donor_labels"],
            donor_iids=case["donor_iids"],
            expected_sha256=cal.sha256_file(case["marker_truth_path"]),
            expected_truth_donors=case["donor_iids"],
        )


def test_planner_rejects_donor_vcf_marker_absent_from_model_metadata(tmp_path):
    case = _marker_truth_projection_case(tmp_path)
    marker_truth_path = case["marker_truth_path"]
    marker_truth_path.write_text(
        marker_truth_path.read_text(encoding="utf-8").replace(
            "SIMCAL\t1\t0\t1000\trs1_0",
            "SIMCAL\t1\t0\t1001\trsVALID_IN_SOURCE_VCF",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="position does not match model"):
        cal.attach_marker_truth(
            case["fixture"],
            marker_truth_path,
            tract_truth=case["tract_truth"],
            model_specs=case["model_specs"],
            donor_labels=case["donor_labels"],
            donor_iids=case["donor_iids"],
            expected_sha256=cal.sha256_file(marker_truth_path),
            expected_truth_donors=case["donor_iids"],
        )


def _reread_projection_tract(case, *, max_breakpoints=None, targets=None):
    tract = case["tract_truth"]
    return cal.read_tract_truth(
        tract.path,
        iid=case["fixture"].iid,
        model_specs=case["model_specs"],
        donor_iids=case["donor_iids"],
        donor_labels=case["donor_labels"],
        max_breakpoints_by_autosome=(
            max_breakpoints
            if max_breakpoints is not None
            else {chrom: 100 for chrom in cal.AUTOSOMES}
        ),
        target_marker_ancestry_fractions=(
            targets if targets is not None else tract.summary["realized_marker_ancestry_fractions"]
        ),
        fraction_absolute_tolerance="0.000001",
        expected_sha256=cal.sha256_file(tract.path),
    )


def test_tract_truth_rejects_gap_or_overlap(tmp_path):
    case = _marker_truth_projection_case(tmp_path)
    path = case["tract_truth"].path
    rows = [line.split("\t") for line in path.read_text(encoding="utf-8").splitlines()]
    chr1_hap0_rows = [row for row in rows[1:] if row[1] == "1" and row[2] == "0"]
    chr1_hap0_rows[1][3] = "0"
    path.write_text("\n".join("\t".join(row) for row in rows) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="tract coverage has a gap/overlap"):
        _reread_projection_tract(case)


def test_tract_truth_rejects_excess_breakpoints(tmp_path):
    case = _marker_truth_projection_case(tmp_path)
    maximums = {chrom: 100 for chrom in cal.AUTOSOMES}
    maximums["1"] = 0

    with pytest.raises(ValueError, match="above the predeclared maximum 0"):
        _reread_projection_tract(case, max_breakpoints=maximums)


def test_tract_truth_rejects_target_fraction_drift(tmp_path):
    case = _marker_truth_projection_case(tmp_path)

    with pytest.raises(ValueError, match="differs from target"):
        _reread_projection_tract(
            case,
            targets={label: "0" for label in cal.POPULATION_ORDER},
        )


def test_marker_truth_must_match_covering_founder_tract(tmp_path):
    case = _marker_truth_projection_case(tmp_path)
    marker_truth_path = case["marker_truth_path"]
    marker_truth_path.write_text(
        marker_truth_path.read_text(encoding="utf-8").replace(
            "DONOR_OCE\t0\tDONOR_EUR",
            "DONOR_EUR\t0\tDONOR_EUR",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="marker contribution does not match tract truth"):
        cal.attach_marker_truth(
            case["fixture"],
            marker_truth_path,
            tract_truth=case["tract_truth"],
            model_specs=case["model_specs"],
            donor_labels=case["donor_labels"],
            donor_iids=case["donor_iids"],
            expected_sha256=cal.sha256_file(marker_truth_path),
            expected_truth_donors=case["donor_iids"],
        )


def test_ancestry_accuracy_uses_exact_windows_and_penalizes_missing_calls():
    truth = list(_default_truth_windows())
    truth[0] = cal.TruthWindow("1", 1000, 1099, "AFR", "EUR")
    truth.insert(1, cal.TruthWindow("1", 1100, 1199, "EAS", "EUR"))
    missing_key = truth[2].key  # the sole chr2 truth window
    painting = _painting_for_truth(truth)
    painting["chr1"][0].update({"hap0": "EUR", "hap1": "AFR"})
    painting["chr1"][1].update({"hap0": "EUR", "hap1": "EAS"})
    painting["chr2"] = []
    result = SimpleNamespace(
        global_ancestry=_truth_global_ancestry(truth),
        chromosome_painting=painting,
    )

    accuracy = cal.ancestry_accuracy(result, truth)

    assert missing_key == ("2", 2000, 2099)
    assert accuracy["truth_windows_expected"] == 23
    assert accuracy["windows_assigned"] == 22
    assert accuracy["windows_missing"] == 1
    assert accuracy["assignment_completeness"] == pytest.approx(22 / 23)
    # Both chr1 calls are diplotype-correct despite their swapped haplotypes.
    assert accuracy["diplotype_windows_correct"] == 22
    assert accuracy["local_diplotype_accuracy"] == pytest.approx(22 / 23)
    # One consistent chromosome-wide swap is accepted; the missing window is wrong.
    assert accuracy["haplotype_calls_correct_best_orientation"] == 44
    assert accuracy["local_haplotype_accuracy_best_orientation"] == pytest.approx(44 / 46)
    assert accuracy["best_orientation_by_autosome"]["1"] == 1
    assert accuracy["per_truth_class"]["AFR"] == {
        "truth_haplotype_calls_expected": 1,
        "assigned_haplotype_calls": 1,
        "correct_haplotype_calls_best_orientation": 1,
        "assignment_completeness": 1.0,
        "local_haplotype_accuracy_best_orientation": 1.0,
    }
    assert accuracy["per_truth_class"]["EUR"]["truth_haplotype_calls_expected"] == 44
    assert accuracy["per_truth_class"]["EUR"]["assigned_haplotype_calls"] == 42
    assert accuracy["per_truth_class"]["EUR"]["correct_haplotype_calls_best_orientation"] == 42
    assert accuracy["haplotype_confusion_counts"]["EUR"]["__missing__"] == 2
    assert accuracy["per_truth_diplotype"]["EUR|EUR"] == {
        "windows_expected": 21,
        "windows_assigned": 20,
        "windows_correct": 20,
        "assignment_completeness": pytest.approx(20 / 21),
        "local_diplotype_accuracy": pytest.approx(20 / 21),
    }
    assert accuracy["global_ancestry_total_variation"] == pytest.approx(0)


def test_evaluation_coverage_rejects_declared_class_without_window_truth(tmp_path):
    labels = tuple(label for label in cal.POPULATION_ORDER if label != "OCE")
    truth = tuple(
        cal.TruthWindow(
            chrom=chrom,
            start=int(chrom) * 1000,
            end=int(chrom) * 1000 + 99,
            hap0=labels[(int(chrom) - 1) % len(labels)],
            hap1=labels[(int(chrom) - 1) % len(labels)],
        )
        for chrom in cal.AUTOSOMES
    )
    fixtures = (
        _fixture(tmp_path, iid="SIM1", validation_stratum="mosaic", truth_windows=truth),
        _fixture(tmp_path, iid="SIM2", validation_stratum="mosaic", truth_windows=truth),
    )
    donor_labels = {
        f"{label}_{replicate}": label for label in cal.POPULATION_ORDER for replicate in (1, 2)
    }
    donors = tuple(sorted(donor_labels))
    truth_founders = {
        label: frozenset({f"{label}_1", f"{label}_2"}) for label in cal.POPULATION_ORDER
    }
    fixtures = tuple(
        dataclasses.replace(fixture, truth_founders_by_class=truth_founders)
        for fixture in fixtures
    )
    manifest = SimpleNamespace(
        splits={"calibration": ("SIM1", "SIM2")},
        simulations={
            "SIM1": {"donor_iids": donors, "generation": 4},
            "SIM2": {"donor_iids": donors, "generation": 8},
        },
        simulation_protocol={
            "allowed_generations": [4, 8],
            "minimums": {
                "founders_per_class_per_split": 2,
                "simulations_per_class_per_split": 2,
                "truth_haplotype_windows_per_class_per_split": 1,
            },
        },
    )

    with pytest.raises(ValueError, match="evaluable OCE window truth"):
        cal.validate_selected_split_evaluation_coverage(
            simulation_manifest=manifest,
            donor_labels=donor_labels,
            dataset_split="calibration",
            fixtures=fixtures,
        )


def _evaluation_coverage_case(
    tmp_path: Path,
    *,
    modal_founders_per_class: int = 2,
    minimums: dict[str, int] | None = None,
):
    truth = tuple(
        cal.TruthWindow(
            chrom=chrom,
            start=int(chrom) * 1000,
            end=int(chrom) * 1000 + 99,
            hap0=cal.POPULATION_ORDER[(int(chrom) - 1) % len(cal.POPULATION_ORDER)],
            hap1=cal.POPULATION_ORDER[(int(chrom) - 1) % len(cal.POPULATION_ORDER)],
        )
        for chrom in cal.AUTOSOMES
    )
    donor_labels = {
        f"{label}_{replicate}": label for label in cal.POPULATION_ORDER for replicate in (1, 2)
    }
    donors = tuple(sorted(donor_labels))
    truth_founders = {
        label: frozenset(
            f"{label}_{replicate}" for replicate in range(1, modal_founders_per_class + 1)
        )
        for label in cal.POPULATION_ORDER
    }
    fixtures = tuple(
        _fixture(
            tmp_path,
            iid=iid,
            validation_stratum="mosaic",
            truth_windows=truth,
            truth_founders_by_class=truth_founders,
        )
        for iid in ("SIM1", "SIM2")
    )
    manifest = SimpleNamespace(
        splits={"calibration": ("SIM1", "SIM2")},
        simulations={
            "SIM1": {"donor_iids": donors, "generation": 4},
            "SIM2": {"donor_iids": donors, "generation": 8},
        },
        simulation_protocol={
            "allowed_generations": [4, 8],
            "minimums": minimums
            or {
                "founders_per_class_per_split": 2,
                "simulations_per_class_per_split": 2,
                "truth_haplotype_windows_per_class_per_split": 1,
            },
        },
    )
    return manifest, donor_labels, fixtures


def test_evaluation_coverage_rejects_single_modal_truth_founder_per_class(tmp_path):
    manifest, donor_labels, fixtures = _evaluation_coverage_case(
        tmp_path,
        modal_founders_per_class=1,
    )

    with pytest.raises(ValueError, match="distinct AFR founder.*modal window truth"):
        cal.validate_selected_split_evaluation_coverage(
            simulation_manifest=manifest,
            donor_labels=donor_labels,
            dataset_split="calibration",
            fixtures=fixtures,
        )


@pytest.mark.parametrize(
    ("field", "minimum", "message"),
    [
        ("founders_per_class_per_split", 3, "unique AFR founder"),
        ("simulations_per_class_per_split", 3, "simulation.*evaluable AFR window truth"),
        (
            "truth_haplotype_windows_per_class_per_split",
            100,
            "evaluable AFR truth haplotype windows",
        ),
    ],
)
def test_evaluation_coverage_enforces_declared_minimums(
    tmp_path,
    field,
    minimum,
    message,
):
    minimums = {
        "founders_per_class_per_split": 2,
        "simulations_per_class_per_split": 2,
        "truth_haplotype_windows_per_class_per_split": 1,
    }
    minimums[field] = minimum
    manifest, donor_labels, fixtures = _evaluation_coverage_case(
        tmp_path,
        minimums=minimums,
    )

    with pytest.raises(ValueError, match=message):
        cal.validate_selected_split_evaluation_coverage(
            simulation_manifest=manifest,
            donor_labels=donor_labels,
            dataset_split="calibration",
            fixtures=fixtures,
        )


def test_ancestry_accuracy_rejects_nonfinite_global_fraction():
    truth = _default_truth_windows()
    global_ancestry = _truth_global_ancestry(truth)
    global_ancestry["EUR"]["fraction"] = float("nan")
    result = SimpleNamespace(
        global_ancestry=global_ancestry,
        chromosome_painting=_painting_for_truth(truth),
    )

    with pytest.raises(ValueError, match="invalid fraction for EUR"):
        cal.ancestry_accuracy(result, truth)


def test_complete_coverage_schema_is_calibration_eligible(tmp_path):
    truth = _fixture(tmp_path).truth_windows

    assert (
        cal.coverage_metrics_calibration_exclusion(
            _complete_coverage_metrics(truth),
            truth,
        )
        is None
    )


def test_coverage_schema_rejects_incomplete_denominators_and_bad_invariants(tmp_path):
    truth = _fixture(tmp_path).truth_windows

    incomplete = _complete_coverage_metrics(truth)
    incomplete["model_denominators"] = {
        "complete": False,
        "unreadable_autosomes": [7],
    }
    assert cal.coverage_metrics_calibration_exclusion(incomplete, truth)["type"] == (
        "incomplete_model_denominators"
    )

    inconsistent = _complete_coverage_metrics(truth)
    inconsistent["model_markers"]["aggregate"]["matched"] += 1
    assert cal.coverage_metrics_calibration_exclusion(inconsistent, truth)["type"] == (
        "invalid_model_markers"
    )

    wrong_model_c = _complete_coverage_metrics(truth)
    expected_model_c = {
        chrom: wrong_model_c["model_markers"]["by_autosome"][chrom]["total"]
        for chrom in cal.AUTOSOMES
    }
    expected_model_c["1"] += 1
    assert (
        cal.coverage_metrics_calibration_exclusion(
            wrong_model_c,
            truth,
            expected_model_markers_by_autosome=expected_model_c,
        )["type"]
        == "model_marker_denominator_mismatch"
    )

    wrong_window_denominator = _complete_coverage_metrics(truth)
    wrong_window_denominator["haplotype_windows"]["expected_by_autosome"]["1"] += 2
    wrong_window_denominator["haplotype_windows"]["expected"] += 2
    wrong_window_denominator["haplotype_windows"]["assignment_rate"] = 44 / 46
    assert (
        cal.coverage_metrics_calibration_exclusion(
            wrong_window_denominator,
            truth,
        )["type"]
        == "truth_model_window_mismatch"
    )

    incomplete_source_counts = _complete_coverage_metrics(truth)
    incomplete_source_counts["per_source"]["unknown"]["hits"] -= 1
    assert (
        cal.coverage_metrics_calibration_exclusion(
            incomplete_source_counts,
            truth,
        )["type"]
        == "invalid_per_source_metrics"
    )


def test_masks_join_by_rsid_and_merged_aliases_collapse_by_coordinate(tmp_path):
    fixture = _fixture(
        tmp_path,
        [
            _marker(1, pos=100, rsid="rs1"),
            _marker(2, pos=100, rsid="rs2"),
            _marker(9, pos=100, rsid="rs9"),
            _marker(3, pos=200, rsid="rs3"),
        ],
    )
    v5 = cal.SiteManifest(
        name="twentythreeandme_v5_derived_site_mask",
        path=tmp_path / "v5.tsv",
        sha256="a" * 64,
        rsids=frozenset({"rs1", "rs2"}),
        site_count=2,
        autosomal_sites=(
            cal.ManifestSite("rs1", "1", 10),
            cal.ManifestSite("rs2", "1", 20),
        ),
    )
    adna = cal.SiteManifest(
        name="ancestrydna_v2_empirical_site_mask",
        path=tmp_path / "adna.tsv",
        sha256="b" * 64,
        rsids=frozenset({"rs9", "rs3"}),
        site_count=2,
        autosomal_sites=(
            cal.ManifestSite("rs9", "1", 20),
            cal.ManifestSite("rs3", "1", 30),
        ),
    )

    masks = {mask.name: mask for mask in cal.build_masks(fixture, v5, adna, frozenset({"rs9"}))}

    assert [m.rsid for m in masks["twentythreeandme_derived_mask"].markers] == [
        "rs1",
        "rs2",
    ]
    merged = masks["synthetic_merged_derived_masks"]
    assert [(m.rsid, m.pos, m.db_pos, m.source) for m in merged.markers] == [
        ("rs1", 100, 10, "S1"),
        ("rs9", 100, 20, "both"),
        ("rs3", 200, 30, "S2"),
    ]
    assert merged.file_format == "merged_v1"


def test_site_manifest_rejects_one_rsid_at_multiple_coordinates(tmp_path):
    path = tmp_path / "bad-sites.tsv"
    path.write_text(
        "rsid\tchrom\tpos\nrs1\t1\t10\nrs1\t1\t20\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="rsID 'rs1' maps to multiple coordinates"):
        cal.read_site_manifest("bad", path)


def test_merged_masks_reject_cross_source_rsid_coordinate_conflict(tmp_path):
    fixture = _fixture(tmp_path, [_marker(1, pos=100, rsid="rs1")])
    v5 = cal.SiteManifest(
        "v5",
        tmp_path / "v5.tsv",
        "a" * 64,
        frozenset({"rs1"}),
        1,
        (cal.ManifestSite("rs1", "1", 10),),
    )
    adna = cal.SiteManifest(
        "adna",
        tmp_path / "adna.tsv",
        "b" * 64,
        frozenset({"rs1"}),
        1,
        (cal.ManifestSite("rs1", "1", 20),),
    )

    with pytest.raises(ValueError, match="rsID 'rs1'.*multiple GRCh37 coordinates"):
        cal.build_masks(fixture, v5, adna, frozenset({"rs1"}))


def test_supplied_site_mask_with_zero_fixture_overlap_fails_early(tmp_path):
    fixture = _fixture(tmp_path)
    empty_overlap = cal.SiteManifest(
        name="twentythreeandme_v5_derived_site_mask",
        path=tmp_path / "v5.tsv",
        sha256="a" * 64,
        rsids=frozenset({"rs999"}),
        site_count=1,
        autosomal_sites=(cal.ManifestSite("rs999", "1", 999),),
    )

    with pytest.raises(ValueError, match="realizes zero markers"):
        cal.build_masks(fixture, empty_overlap, None)


def test_downsampling_is_deterministic_nested_and_chromosome_balanced():
    markers = tuple(
        [_marker(index, "1") for index in range(1, 81)]
        + [_marker(index + 100, "2") for index in range(1, 21)]
    )

    small = cal.downsample_nested(markers, Decimal("0.25"), seed=42)
    large = cal.downsample_nested(markers, Decimal("0.50"), seed=42)
    repeat = cal.downsample_nested(markers, Decimal("0.25"), seed=42)

    assert small == repeat
    assert {marker.site_key for marker in small} <= {marker.site_key for marker in large}
    assert len(small) == 25
    assert cal.count_by_autosome(small)["1"] == 20
    assert cal.count_by_autosome(small)["2"] == 5
    assert cal.downsample_nested(markers, Decimal("0.25"), seed=43) != small


def test_chromosome_drop_is_applied_after_shared_downsampling_prefix():
    markers = tuple(
        [_marker(index, "1") for index in range(1, 31)]
        + [_marker(index + 100, "2") for index in range(1, 31)]
    )

    baseline_prefix, baseline = cal.select_markers_for_job(
        markers,
        Decimal("0.5"),
        42,
        frozenset(),
    )
    dropped_prefix, dropped = cal.select_markers_for_job(
        markers,
        Decimal("0.5"),
        42,
        frozenset({"2"}),
    )

    assert dropped_prefix == baseline_prefix == baseline
    assert dropped == tuple(marker for marker in baseline if marker.chrom != "2")


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity"])
def test_fraction_parser_rejects_nonfinite_values(raw):
    with pytest.raises(argparse.ArgumentTypeError, match="finite"):
        cal.parse_fraction(raw)


def test_runtime_revision_rejects_untracked_backend_code(monkeypatch):
    expected_revision = "d" * 40
    responses = iter(
        (
            cal.subprocess.CompletedProcess(
                args=["git", "rev-parse", "HEAD"],
                returncode=0,
                stdout=f"{expected_revision}\n",
                stderr="",
            ),
            cal.subprocess.CompletedProcess(
                args=["git", "ls-files"],
                returncode=0,
                stdout="backend/analysis/lai.py\npyproject.toml\nuv.lock\n",
                stderr="",
            ),
            cal.subprocess.CompletedProcess(
                args=["git", "status"],
                returncode=0,
                stdout="?? backend/analysis/shadow.py\n",
                stderr="",
            ),
        )
    )
    monkeypatch.setattr(cal.subprocess, "run", lambda *_args, **_kwargs: next(responses))

    with pytest.raises(ValueError, match="runtime code/dependency files differ"):
        cal.verify_runtime_code_revision(expected_revision)


def test_production_adapter_uses_merged_sample_db_and_diagnostic_contract(
    tmp_path,
    monkeypatch,
):
    fixture = _fixture(
        tmp_path,
        [_marker(1, source="S1"), _marker(2, source="both")],
    )
    snapshots = []
    observed = {}

    def fake_run_lai_analysis(**kwargs):
        observed.update(kwargs)
        with kwargs["sample_engine"].connect() as connection:
            observed["metadata"] = connection.execute(
                sa.select(cal.sample_metadata_table.c.file_format)
            ).scalar_one()
            observed["rows"] = connection.execute(
                sa.select(
                    cal.raw_variants.c.rsid,
                    cal.raw_variants.c.source,
                ).order_by(cal.raw_variants.c.rsid)
            ).all()
        kwargs["diagnostic_metrics_callback"]({"schema_version": 1, "stage": "mapped"})
        job_dir = tmp_path / "work" / "lai_work" / "sample_7"
        sibling_dir = tmp_path / "work" / "lai_work" / "sample_8"
        job_dir.mkdir(parents=True)
        sibling_dir.mkdir()
        (job_dir / "large-intermediate.vcf.gz").write_bytes(b"job")
        (sibling_dir / "keep.txt").write_bytes(b"sibling")
        return SimpleNamespace(metadata={})

    monkeypatch.setattr(cal, "run_lai_analysis", fake_run_lai_analysis)
    monkeypatch.setenv("YELIZTLI_DATA_DIR", "original-data")

    result = cal.run_production_diagnostic(
        markers=fixture.markers,
        file_format="merged_v1",
        fixture=fixture,
        bundle_dir=tmp_path / "bundle",
        work_dir=tmp_path / "work",
        sample_id=7,
        diagnostic_metrics_callback=snapshots.append,
    )

    assert result.metadata == {}
    assert observed["allow_below_minimum_for_diagnostics"] is True
    assert observed["sample_id"] == 7
    assert observed["metadata"] == "merged_v1"
    assert observed["rows"] == [("rs1", "S1"), ("rs2", "both")]
    assert snapshots == [{"schema_version": 1, "stage": "mapped"}]
    assert cal.os.environ["YELIZTLI_DATA_DIR"] == "original-data"
    assert not (tmp_path / "work" / "lai_work" / "sample_7").exists()
    assert (tmp_path / "work" / "lai_work" / "sample_8" / "keep.txt").is_file()


def test_production_adapter_cleans_failed_job_directory_without_touching_sibling(
    tmp_path,
    monkeypatch,
):
    fixture = _fixture(tmp_path)

    def failing_run_lai_analysis(**kwargs):
        job_dir = tmp_path / "work" / "lai_work" / "sample_9"
        sibling_dir = tmp_path / "work" / "lai_work" / "sample_10"
        job_dir.mkdir(parents=True)
        sibling_dir.mkdir()
        (job_dir / "failed-beagle-output.vcf.gz").write_bytes(b"job")
        (sibling_dir / "keep.txt").write_bytes(b"sibling")
        kwargs["diagnostic_metrics_callback"]({"stage": "phased"})
        raise RuntimeError("inference failed")

    monkeypatch.setattr(cal, "run_lai_analysis", failing_run_lai_analysis)

    with pytest.raises(RuntimeError, match="inference failed"):
        cal.run_production_diagnostic(
            markers=fixture.markers,
            file_format="",
            fixture=fixture,
            bundle_dir=tmp_path / "bundle",
            work_dir=tmp_path / "work",
            sample_id=9,
            diagnostic_metrics_callback=lambda _snapshot: None,
        )

    assert not (tmp_path / "work" / "lai_work" / "sample_9").exists()
    assert (tmp_path / "work" / "lai_work" / "sample_10" / "keep.txt").is_file()


def _job(tmp_path: Path):
    fixture = _fixture(tmp_path)
    mask = cal.MaskScenario(
        name="native_unmasked",
        kind="public_fixture_native",
        file_format="",
        markers=fixture.markers,
        manifest_names=(),
    )
    return cal.CalibrationJob(
        index=3,
        fixture=fixture,
        mask=mask,
        drop_scenario=cal.ChromosomeDropScenario("none", frozenset()),
        fraction=Decimal("1"),
        seed=42,
    )


def _run_job(
    tmp_path: Path,
    production_runner,
    confirmation_policy_provenance_entry=None,
):
    return cal.run_job(
        _job(tmp_path),
        dataset_id="gnomad-hgdp-1kg-v3.1.2",
        dataset_split=(
            "final_confirmation"
            if confirmation_policy_provenance_entry is not None
            else "calibration"
        ),
        bundle_dir=tmp_path / "bundle",
        bundle_metadata_sha256="1" * 64,
        bundle_artifact_sha256="2" * 64,
        code_revision="deadbeef",
        harness_script_sha256="5" * 64,
        runtime_environment={"python": {"version": "3.13.11"}},
        labels_sha256="3" * 64,
        manifests={},
        configuration_sha256="4" * 64,
        work_dir=tmp_path / "work",
        confirmation_policy_provenance_entry=confirmation_policy_provenance_entry,
        production_runner=production_runner,
    )


def test_success_record_scores_exact_truth_and_complete_telemetry(tmp_path):
    fixture = _fixture(tmp_path)

    def fake_runner(**kwargs):
        kwargs["diagnostic_metrics_callback"]({"stage": 1})
        kwargs["diagnostic_metrics_callback"]({"stage": 2, "mapped": 2})
        return SimpleNamespace(
            global_ancestry=_truth_global_ancestry(fixture.truth_windows),
            chromosome_painting=_painting_for_truth(fixture.truth_windows),
            metadata={"lai_coverage_metrics": _complete_coverage_metrics(fixture.truth_windows)},
        )

    record = _run_job(tmp_path, fake_runner)

    assert record["status"] == "ok"
    assert record["local_diplotype_accuracy"] == 1.0
    assert record["local_haplotype_accuracy_best_orientation"] == 1.0
    assert record["assignment_completeness"] == 1.0
    assert record["global_ancestry_total_variation"] == 0.0
    assert record["calibration_eligible"] is True
    assert record["calibration_exclusion"] is None
    assert record["coverage_metadata"]["snapshot_count"] == 2
    assert record["coverage_metadata"]["last_progressive_snapshot"] == {
        "stage": 2,
        "mapped": 2,
    }
    assert record["provenance"] == {
        "bundle_metadata_sha256": "1" * 64,
        "bundle_artifact_sha256": "2" * 64,
        "code_revision": "deadbeef",
        "harness_script_sha256": "5" * 64,
        "runtime_environment": {"python": {"version": "3.13.11"}},
        "fixture_sha256": cal.sha256_file(tmp_path / "SIMTEST.tsv"),
        "local_truth_sha256": cal.sha256_file(tmp_path / "SIMTEST.truth.tsv"),
        "marker_truth_sha256": "",
        "labels_sha256": "3" * 64,
        "mask_sha256": {},
        "configuration_sha256": "4" * 64,
    }


def test_every_final_result_repeats_confirmation_selection_provenance(tmp_path):
    policy_provenance = {
        "filename": "confirmation-policy.json",
        "sha256": "a" * 64,
        "policy_id": "lai-coverage-confirmation-v1",
        "confirmation_commitment": "b" * 64,
        "calibration_plan": {
            "configuration_sha256": "c" * 64,
            "job_plan_sha256": "d" * 64,
            "expected_job_count": 8,
        },
        "calibration_observation_sha256": "e" * 64,
        "selection_provenance": {
            "script_sha256": "f" * 64,
            "report_sha256": "1" * 64,
            "code_revision": "2" * 40,
        },
    }

    def failing_runner(**_kwargs):
        raise RuntimeError("inference failed")

    record = _run_job(
        tmp_path,
        failing_runner,
        confirmation_policy_provenance_entry=policy_provenance,
    )

    assert record["status"] == "operational_error"
    assert record["provenance"]["confirmation_policy"] == policy_provenance


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (
            RuntimeError(
                "Insufficient data for local ancestry inference: no usable markers remained"
            ),
            "coverage_failure",
        ),
        (
            RuntimeError(
                "Insufficient data for local ancestry inference: no chromosome was "
                "successfully phased from the usable markers"
            ),
            "coverage_failure",
        ),
        (RuntimeError("database is locked"), "operational_error"),
    ],
)
def test_failed_run_classifies_sparse_boundaries_and_operational_errors(
    tmp_path,
    error,
    expected_status,
):
    def failing_runner(**kwargs):
        kwargs["diagnostic_metrics_callback"]({"stage": "phased", "count": 1})
        raise error

    record = _run_job(tmp_path, failing_runner)

    assert cal.classify_production_failure(error) == expected_status
    assert record["status"] == expected_status
    assert record["coverage_metadata"]["last_progressive_snapshot"] == {
        "stage": "phased",
        "count": 1,
    }
    assert record["calibration_eligible"] is False
    assert record["calibration_exclusion"]["type"] == expected_status
    assert record["error"]["message"] == str(error)


def test_successful_run_with_invalid_coverage_schema_is_ineligible(tmp_path):
    fixture = _fixture(tmp_path)
    incomplete_metrics = _complete_coverage_metrics(fixture.truth_windows)
    incomplete_metrics["model_denominators"] = {
        "complete": False,
        "unreadable_autosomes": [7],
    }

    def incomplete_runner(**_kwargs):
        return SimpleNamespace(
            global_ancestry=_truth_global_ancestry(fixture.truth_windows),
            chromosome_painting=_painting_for_truth(fixture.truth_windows),
            metadata={"lai_coverage_metrics": incomplete_metrics},
        )

    record = _run_job(tmp_path, incomplete_runner)

    assert record["status"] == "invalid"
    assert record["calibration_eligible"] is False
    assert record["calibration_exclusion"]["type"] == "incomplete_model_denominators"
    assert record["error"] is None


def test_successful_run_rejects_per_autosome_painting_telemetry_shift(tmp_path):
    fixture = _fixture(tmp_path)
    painting = _painting_for_truth(fixture.truth_windows)
    painting["chr1"] = []
    telemetry_keys = {window.key for window in fixture.truth_windows if window.chrom != "2"}

    def shifted_runner(**_kwargs):
        return SimpleNamespace(
            global_ancestry=_truth_global_ancestry(fixture.truth_windows),
            chromosome_painting=painting,
            metadata={
                "lai_coverage_metrics": _complete_coverage_metrics(
                    fixture.truth_windows,
                    assigned_keys=telemetry_keys,
                )
            },
        )

    record = _run_job(tmp_path, shifted_runner)

    assert record["status"] == "invalid"
    assert record["calibration_exclusion"]["type"] == "painting_coverage_mismatch"
    assert "Per-autosome" in record["calibration_exclusion"]["message"]


@pytest.mark.parametrize("metadata", [object(), None, [], "metadata"])
def test_malformed_success_metadata_becomes_structured_operational_error(
    tmp_path,
    metadata,
):
    def malformed_runner(**_kwargs):
        return SimpleNamespace(metadata=metadata)

    record = _run_job(tmp_path, malformed_runner)

    assert record["status"] == "operational_error"
    assert record["calibration_eligible"] is False
    assert record["error"]["type"] == "TypeError"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_success_metadata_becomes_structured_operational_error(
    tmp_path,
    value,
):
    def malformed_runner(**_kwargs):
        return SimpleNamespace(metadata={"nonfinite": value})

    record = _run_job(tmp_path, malformed_runner)

    assert record["status"] == "operational_error"
    assert record["calibration_eligible"] is False
    assert record["error"] == {
        "type": "TypeError",
        "message": "cannot serialize a non-finite float",
    }


def test_calibration_isolation_accepts_donor_excluded_reference(tmp_path):
    fixture = _fixture(tmp_path)
    bundle_dir, manifest, verification = _write_reference_bundle(tmp_path)

    summary = cal.validate_calibration_isolation(
        bundle_dir=bundle_dir,
        fixtures=[fixture],
        donor_iids=frozenset({"DONOR1"}),
        isolation_iids=frozenset({"DONOR1"}),
        training_iids=frozenset({"TRAIN1"}),
        reference_manifest=manifest,
        reference_verification=verification.payload,
    )

    assert set(summary) == set(cal.AUTOSOMES)
    assert summary["1"]["sample_count"] == 2
    assert summary["1"]["sample_ids_sha256"] == cal.sha256_json(["REF1", "REF2"])


def test_calibration_isolation_rejects_manifest_that_does_not_exclude_donor(tmp_path):
    fixture = _fixture(tmp_path)
    bundle_dir, manifest, verification = _write_reference_bundle(
        tmp_path,
        excluded_iids=("SOMEONE_ELSE",),
    )

    with pytest.raises(ValueError, match="does not exclude protected IID.*DONOR1"):
        cal.validate_calibration_isolation(
            bundle_dir=bundle_dir,
            fixtures=[fixture],
            donor_iids=frozenset({"DONOR1"}),
            isolation_iids=frozenset({"DONOR1"}),
            training_iids=frozenset({"TRAIN1"}),
            reference_manifest=manifest,
            reference_verification=verification.payload,
        )


def test_calibration_isolation_rejects_training_leak(tmp_path):
    fixture = _fixture(tmp_path)
    bundle_dir, manifest, verification = _write_reference_bundle(tmp_path)

    with pytest.raises(ValueError, match="remain in Gnomix training.*DONOR1"):
        cal.validate_calibration_isolation(
            bundle_dir=bundle_dir,
            fixtures=[fixture],
            donor_iids=frozenset({"DONOR1"}),
            isolation_iids=frozenset({"DONOR1"}),
            training_iids=frozenset({"DONOR1", "TRAIN1"}),
            reference_manifest=manifest,
            reference_verification=verification.payload,
        )


def test_calibration_isolation_rejects_declared_relative_in_training(tmp_path):
    fixture = _fixture(tmp_path)
    bundle_dir, manifest, verification = _write_reference_bundle(
        tmp_path,
        excluded_iids=("DONOR1", "RELATIVE1"),
    )

    with pytest.raises(ValueError, match="remain in Gnomix training.*RELATIVE1"):
        cal.validate_calibration_isolation(
            bundle_dir=bundle_dir,
            fixtures=[fixture],
            donor_iids=frozenset({"DONOR1"}),
            isolation_iids=frozenset({"DONOR1", "RELATIVE1"}),
            training_iids=frozenset({"RELATIVE1", "TRAIN1"}),
            reference_manifest=manifest,
            reference_verification=verification.payload,
        )


def test_calibration_isolation_rejects_live_phasing_panel_leak(tmp_path):
    fixture = _fixture(tmp_path)
    samples = {chrom: ("REF1", "DONOR1") for chrom in cal.AUTOSOMES}
    bundle_dir, manifest, verification = _write_reference_bundle(
        tmp_path, samples_by_chrom=samples
    )

    with pytest.raises(ValueError, match="chr1 still contains protected IID.*DONOR1"):
        cal.validate_calibration_isolation(
            bundle_dir=bundle_dir,
            fixtures=[fixture],
            donor_iids=frozenset({"DONOR1"}),
            isolation_iids=frozenset({"DONOR1"}),
            training_iids=frozenset({"TRAIN1"}),
            reference_manifest=manifest,
            reference_verification=verification.payload,
        )


def test_calibration_isolation_rejects_panel_file_drift(tmp_path):
    fixture = _fixture(tmp_path)
    bundle_dir, manifest, verification = _write_reference_bundle(tmp_path)
    index_path = bundle_dir / "phasing_panel" / "ref_panel_chr1.vcf.gz.tbi"
    with index_path.open("ab") as handle:
        handle.write(b"drift")

    with pytest.raises(
        ValueError,
        match="verified reference file changed after the full hash pass: chr1 index",
    ):
        cal.validate_calibration_isolation(
            bundle_dir=bundle_dir,
            fixtures=[fixture],
            donor_iids=frozenset({"DONOR1"}),
            isolation_iids=frozenset({"DONOR1"}),
            training_iids=frozenset({"TRAIN1"}),
            reference_manifest=manifest,
            reference_verification=verification.payload,
        )


def test_configuration_records_isolation_truth_and_script_provenance(tmp_path):
    fixture = _fixture(tmp_path, [_marker(1), _marker(2)])
    manifest = cal.SiteManifest(
        name="twentythreeandme_v5_derived_site_mask",
        path=tmp_path / "v5.tsv",
        sha256="b" * 64,
        rsids=frozenset({"rs1", "rs999"}),
        site_count=2,
    )
    masks = cal.build_masks(fixture, manifest, None)
    bundle_dir, reference_manifest, reference_verification = _write_reference_bundle(tmp_path)
    runtime_summary = cal.validate_calibration_isolation(
        bundle_dir=bundle_dir,
        fixtures=[fixture],
        donor_iids=frozenset({"DONOR1"}),
        isolation_iids=frozenset({"DONOR1"}),
        training_iids=frozenset({"TRAIN1"}),
        reference_manifest=reference_manifest,
        reference_verification=reference_verification.payload,
    )
    donor_path = tmp_path / "donors.tsv"
    donor_path.write_text("DONOR1\n", encoding="utf-8")
    isolation_path = tmp_path / "isolation.tsv"
    isolation_path.write_text("DONOR1\n", encoding="utf-8")
    training_path = tmp_path / "training.tsv"
    training_path.write_text("TRAIN1\n", encoding="utf-8")
    donor_metadata_path = _write_donor_metadata(
        tmp_path / "donor-metadata.tsv",
        [_donor_row("DONOR1", "EUR")],
    )
    vep_path = tmp_path / "vep-rsids.txt"
    vep_path.write_text("rsid\nrs1\n", encoding="utf-8")
    vep_membership = cal.read_identifier_manifest(
        "production_vep_rsid_membership",
        vep_path,
    )
    simulation_path = tmp_path / "simulation.json"
    simulation_path.write_text("{}", encoding="utf-8")
    simulation_manifest = cal.SimulationManifest(
        path=simulation_path,
        sha256=cal.sha256_file(simulation_path),
        dataset_id="public-release",
        source_bundle_artifact_sha256="a" * 64,
        generator={
            "name": "yeliztli-founder-mosaic-v1",
            "code_revision": "d" * 40,
            "script_sha256": "1" * 64,
            "environment_lock_sha256": "2" * 64,
            "rng_library": "numpy",
            "rng_version": "2.4.0",
            "rng_algorithm": "PCG64",
            "seed": 17,
        },
        simulation_protocol={},
        relationships={},
        donor_metadata=_donor_metadata_contract(donor_metadata_path),
        donor_haplotype_source={
            "genome_build": "GRCh38",
            "per_chromosome_vcf_sha256": {
                chrom: cal.sha256_json(["donor-source-vcf", chrom]) for chrom in cal.AUTOSOMES
            },
            "per_chromosome_vcf_index_sha256": {
                chrom: cal.sha256_json(["donor-source-vcf-index", chrom])
                for chrom in cal.AUTOSOMES
            },
        },
        models=_simulation_models(bundle_dir, reference_manifest),
        truth_projection={
            "schema": "gnomix-window-mode-v1",
            "regular_window": "[w*M,(w+1)*M)",
            "final_window": "[(W-1)*M,C)",
            "tie_rule": "lowest production-model ancestry code",
        },
        splits={"calibration": ("SIMTEST",), "final_confirmation": ("SIMFINAL",)},
        simulations={},
    )
    simulation_verification_path = tmp_path / "simulation-verification.json"
    simulation_verification_path.write_text("{}", encoding="utf-8")
    simulation_verification = cal.SimulationVerificationReport(
        path=simulation_verification_path,
        sha256=cal.sha256_file(simulation_verification_path),
        verifier={"name": "test-verifier"},
        marker_rows_verified=44,
    )

    configuration = cal.build_configuration(
        dataset_id="public-release",
        dataset_split="calibration",
        simulation_manifest=simulation_manifest,
        simulation_verification=simulation_verification,
        bundle_metadata_path=tmp_path / "metadata.json",
        bundle_metadata_sha256="c" * 64,
        bundle_metadata_summary={"bundle_version": "2.0.0", "window_count": 100},
        bundle_artifact_sha256="a" * 64,
        code_revision="abc123",
        harness_script_sha256="d" * 64,
        runtime_environment={"python": {"version": "3.13.11"}},
        labels_path=tmp_path / "labels.tsv",
        labels_sha256="e" * 64,
        donor_manifest_path=donor_path,
        donor_manifest_sha256=cal.sha256_file(donor_path),
        donor_iids=frozenset({"DONOR1"}),
        donor_metadata_path=donor_metadata_path,
        donor_metadata_sha256=cal.sha256_file(donor_metadata_path),
        donor_labels={"DONOR1": "EUR"},
        isolation_manifest_path=isolation_path,
        isolation_manifest_sha256=cal.sha256_file(isolation_path),
        isolation_iids=frozenset({"DONOR1"}),
        relationship_summary={"component_count": 1},
        training_samples_path=training_path,
        training_samples_sha256=cal.sha256_file(training_path),
        training_iids=frozenset({"TRAIN1"}),
        reference_manifest=reference_manifest,
        reference_verification=reference_verification,
        reference_runtime_summary=runtime_summary,
        evaluation_coverage_summary={"truth_haplotype_windows_by_class": {"EUR": 44}},
        fixtures=[fixture],
        manifests={manifest.name: manifest},
        vep_membership=vep_membership,
        masks_by_iid={fixture.iid: masks},
        drop_scenarios=[cal.ChromosomeDropScenario("none", frozenset())],
        fractions=[Decimal("0.5")],
        seeds=[42],
    )

    inputs = configuration["inputs"]
    assert inputs["harness_script_sha256"] == "d" * 64
    assert inputs["validation_donors"]["count"] == 1
    assert inputs["donor_metadata"]["model_class_counts"] == {"EUR": 1}
    assert inputs["validation_isolation_samples"]["count"] == 1
    assert inputs["simulation_manifest"]["source_bundle_artifact_sha256"] == "a" * 64
    assert inputs["simulation_manifest"]["split_sizes"] == {
        "calibration": 1,
        "final_confirmation": 1,
    }
    assert inputs["calibration_reference"]["source_bundle_artifact_sha256"] == "a" * 64
    assert inputs["calibration_reference"]["inherited_file_count"] == len(
        reference_manifest.inherited_files
    )
    assert inputs["calibration_reference"]["phasing_panel"]["1"] == runtime_summary["1"]
    assert inputs["fixtures"]["SIMTEST"]["local_truth_windows"] == 22
    assert inputs["fixtures"]["SIMTEST"]["local_truth_sha256"] == fixture.truth_sha256
    assert inputs["fixtures"]["SIMTEST"]["marker_truth_sha256"] == ""
    assert inputs["privacy_safe_site_masks"][manifest.name]["realized_fixture_overlap"] == {
        "SIMTEST": 1
    }
    assert configuration["threshold_selected"] is None


def _write_confirmation_policy(tmp_path: Path, simulation_manifest) -> Path:
    path = tmp_path / "confirmation-policy.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "policy_id": "lai-coverage-confirmation-v1",
                "frozen": True,
                "dataset_id": simulation_manifest.dataset_id,
                "bundle_artifact_sha256": "a" * 64,
                "simulation_manifest_sha256": simulation_manifest.sha256,
                "code_revision": "d" * 40,
                "calibration_plan": {
                    "configuration_sha256": "1" * 64,
                    "job_plan_sha256": "2" * 64,
                    "expected_job_count": 8,
                },
                "calibration_observation_sha256": "3" * 64,
                "selection_provenance": {
                    "script_sha256": "4" * 64,
                    "report_sha256": "5" * 64,
                    "code_revision": "d" * 40,
                },
                "endpoints": [
                    {
                        "name": "assignment_completeness",
                        "op": ">=",
                        "value": "0.9",
                    },
                    {
                        "name": "local_diplotype_accuracy",
                        "op": ">=",
                        "value": "0.9",
                    },
                    {
                        "name": "local_haplotype_accuracy_best_orientation",
                        "op": ">=",
                        "value": "0.9",
                    },
                    {
                        "name": "global_ancestry_total_variation",
                        "op": "<=",
                        "value": "0.1",
                    },
                    {
                        "name": "per_truth_class.assignment_completeness",
                        "op": ">=",
                        "value": "0.8",
                    },
                    {
                        "name": "per_truth_diplotype.local_diplotype_accuracy",
                        "op": ">=",
                        "value": "0.8",
                    },
                ],
                "aggregation": {
                    "dimensions": [
                        "simulation_iid",
                        "input_mask",
                        "validation_stratum",
                        "chromosome_drop_scenario",
                        "fraction",
                        "seed",
                    ],
                    "all_cells_pass": True,
                    "average_biological_replicates": False,
                },
                "coverage_predicates": [
                    {"field": "emitted_markers.total", "op": ">=", "value": "1"},
                    {
                        "field": "model_markers.aggregate.matched",
                        "op": ">=",
                        "value": "1",
                    },
                    {
                        "field": "model_markers.aggregate.match_rate",
                        "op": ">=",
                        "value": "0.5",
                    },
                    {"field": "phased_autosomes.count", "op": ">=", "value": "1"},
                    {"field": "analyzed_autosomes.count", "op": ">=", "value": "1"},
                    {
                        "field": "haplotype_windows.valid_assigned",
                        "op": ">=",
                        "value": "1",
                    },
                    {
                        "field": "haplotype_windows.assignment_rate",
                        "op": ">=",
                        "value": "0.5",
                    },
                ],
                "confirmation_matrix": {
                    "input_masks": [
                        "twentythreeandme_derived_mask",
                        "ancestrydna_empirical_mask",
                        "synthetic_merged_derived_masks",
                    ],
                    "fractions": ["0.5", "1"],
                    "seeds": [17],
                    "drop_scenarios": [
                        {"name": "none", "dropped_autosomes": []},
                        {"name": "drop_chr22", "dropped_autosomes": ["22"]},
                    ],
                },
                "confirmation_commitment": (
                    cal.compute_final_confirmation_split_commitment(simulation_manifest)
                ),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _prepare_planner_case(
    tmp_path: Path,
    monkeypatch,
    *,
    calibration_iids: tuple[str, ...] = ("SIM1", "SIM2"),
    include_masks: bool = True,
    dataset_split: str = "calibration",
):
    calibration_donors = {
        label: (f"CAL_{label}_1", f"CAL_{label}_2") for label in cal.POPULATION_ORDER
    }
    final_donors = {
        label: (f"FINAL_{label}_1", f"FINAL_{label}_2") for label in cal.POPULATION_ORDER
    }
    final_iids = ("SIMFINAL1", "SIMFINAL2")
    selected_iids = calibration_iids if dataset_split == "calibration" else final_iids
    donor_iids = frozenset(
        donor
        for donors in (*calibration_donors.values(), *final_donors.values())
        for donor in donors
    )
    bundle_dir, reference_manifest, reference_verification = _write_reference_bundle(
        tmp_path,
        excluded_iids=tuple(sorted(donor_iids)),
    )
    donor_metadata_path = _write_donor_metadata(
        tmp_path / "donor-metadata.tsv",
        [
            *(
                _donor_row(donor, label)
                for label, donors in calibration_donors.items()
                for donor in donors
            ),
            *(
                _donor_row(donor, label)
                for label, donors in final_donors.items()
                for donor in donors
            ),
        ],
    )
    labels_path = tmp_path / "labels.tsv"
    labels_path.write_text(
        "IID\tvalidation_stratum\n" + "".join(f"{iid}\tmosaic\n" for iid in selected_iids),
        encoding="utf-8",
    )
    fixture_paths: dict[str, Path] = {}
    truth_paths: dict[str, Path] = {}
    marker_truth_paths: dict[str, Path] = {}
    tract_truth_paths: dict[str, Path] = {}
    simulations: list[dict[str, object]] = []
    for simulation_index, iid in enumerate(calibration_iids):
        fixture_path = tmp_path / f"{iid}.tsv"
        fixture_path.write_text(
            "rsid\tchrom\tpos\ta1\ta2\n"
            + "".join(
                f"rs{chrom}_0\t{chrom}\t{int(chrom) * 1000}\tA\tG\n" for chrom in cal.AUTOSOMES
            ),
            encoding="utf-8",
        )
        marker_truth_path, truth_path, tract_truth_path = _write_multiclass_truth(
            tmp_path,
            iid=iid,
            bundle_dir=bundle_dir,
            donors_by_class=calibration_donors,
        )
        fixture_paths[iid] = fixture_path
        truth_paths[iid] = truth_path
        marker_truth_paths[iid] = marker_truth_path
        tract_truth_paths[iid] = tract_truth_path
        simulations.append(
            _simulation_entry(
                iid,
                "calibration",
                tuple(donor for donors in calibration_donors.values() for donor in donors),
                validation_stratum="mosaic",
                marker_truth_sha256=cal.sha256_file(marker_truth_path),
                tract_truth_sha256=cal.sha256_file(tract_truth_path),
                generation=(4, 8)[simulation_index % 2],
                fixture_sha256=cal.sha256_file(fixture_path),
                window_truth_sha256=cal.sha256_file(truth_path),
            )
        )
    for final_index, iid in enumerate(final_iids):
        fixture_path = tmp_path / f"{iid}.tsv"
        fixture_path.write_text(
            "rsid\tchrom\tpos\ta1\ta2\n"
            + "".join(
                f"rs{chrom}_0\t{chrom}\t{int(chrom) * 1000}\tA\tG\n" for chrom in cal.AUTOSOMES
            ),
            encoding="utf-8",
        )
        marker_truth_path, truth_path, tract_truth_path = _write_multiclass_truth(
            tmp_path,
            iid=iid,
            bundle_dir=bundle_dir,
            donors_by_class=final_donors,
        )
        fixture_paths[iid] = fixture_path
        truth_paths[iid] = truth_path
        marker_truth_paths[iid] = marker_truth_path
        tract_truth_paths[iid] = tract_truth_path
        simulations.append(
            _simulation_entry(
                iid,
                "final_confirmation",
                tuple(donor for donors in final_donors.values() for donor in donors),
                validation_stratum="mosaic",
                generation=(4, 8)[final_index % 2],
                marker_truth_sha256=cal.sha256_file(marker_truth_path),
                tract_truth_sha256=cal.sha256_file(tract_truth_path),
                fixture_sha256=cal.sha256_file(fixture_path),
                window_truth_sha256=cal.sha256_file(truth_path),
            )
        )
    simulation_manifest = _write_simulation_manifest(
        tmp_path,
        bundle_dir=bundle_dir,
        reference_manifest=reference_manifest,
        donor_metadata_path=donor_metadata_path,
        donor_iids=donor_iids,
        isolation_iids=donor_iids,
        simulations=simulations,
        dataset_id="public-simulation-v1",
    )
    confirmation_policy_path = (
        _write_confirmation_policy(tmp_path, simulation_manifest)
        if dataset_split == "final_confirmation"
        else None
    )
    simulation_verification_path = _write_simulation_verification_report(
        tmp_path,
        simulation_manifest,
        dataset_split,
        confirmation_policy_path,
    )
    donors_path = tmp_path / "donors.tsv"
    donors_path.write_text("\n".join(sorted(donor_iids)) + "\n", encoding="utf-8")
    isolation_path = tmp_path / "isolation.tsv"
    isolation_path.write_text("\n".join(sorted(donor_iids)) + "\n", encoding="utf-8")
    training_path = tmp_path / "training.tsv"
    training_path.write_text("TRAIN1\tEUR\n", encoding="utf-8")
    job_plan_path = tmp_path / f"{dataset_split}-plan.json"
    planner_args = [
        "--bundle-dir",
        str(bundle_dir),
        "--bundle-artifact-sha256",
        "a" * 64,
        "--code-revision",
        "d" * 40,
        "--labels",
        str(labels_path),
        "--simulation-manifest",
        str(simulation_manifest.path),
        "--simulation-verification-report",
        str(simulation_verification_path),
        "--dataset-split",
        dataset_split,
        "--validation-donors",
        str(donors_path),
        "--donor-metadata",
        str(donor_metadata_path),
        "--validation-isolation-samples",
        str(isolation_path),
        "--validation-relationships",
        str(tmp_path / "validation-relationships.tsv"),
        "--gnomix-training-samples",
        str(training_path),
        "--calibration-reference-manifest",
        str(reference_manifest.path),
        "--reference-verification-stamp",
        str(reference_verification.path),
        "--dataset-id",
        "public-simulation-v1",
        "--job-plan",
        str(job_plan_path),
    ]
    if dataset_split == "calibration":
        planner_args.extend(("--fraction", "1", "--seed", "42"))
    else:
        assert confirmation_policy_path is not None
        planner_args.extend(
            (
                "--confirmation-policy",
                str(confirmation_policy_path),
                "--expected-confirmation-policy-sha256",
                cal.sha256_file(confirmation_policy_path),
            )
        )
    for iid in selected_iids:
        planner_args.extend(("--fixture", f"{iid}={fixture_paths[iid]}"))
        planner_args.extend(("--local-truth", f"{iid}={truth_paths[iid]}"))
        planner_args.extend(("--marker-truth", f"{iid}={marker_truth_paths[iid]}"))
        planner_args.extend(("--tract-truth", f"{iid}={tract_truth_paths[iid]}"))
    site_paths: dict[str, Path] = {}
    if include_masks:
        for option, filename in (
            ("--twentythreeandme-sites", "v5-sites.tsv"),
            ("--ancestrydna-sites", "ancestry-sites.tsv"),
        ):
            path = tmp_path / filename
            path.write_text(
                "rsid\tchrom\tpos\n"
                + "".join(
                    f"rs{chrom}_0\t{chrom}\t{int(chrom) * 1000 + 10}\n" for chrom in cal.AUTOSOMES
                ),
                encoding="utf-8",
            )
            planner_args.extend((option, str(path)))
            site_paths[option] = path
        vep_path = tmp_path / "vep-rsids.txt"
        vep_path.write_text(
            "rsid\n" + "".join(f"rs{chrom}_0\n" for chrom in cal.AUTOSOMES),
            encoding="utf-8",
        )
        planner_args.extend(("--vep-rsids", str(vep_path)))
        site_paths["--vep-rsids"] = vep_path
    monkeypatch.setattr(cal, "verify_runtime_code_revision", lambda _revision: None)
    run_common_args = [
        "--bundle-dir",
        str(bundle_dir),
        "--bundle-artifact-sha256",
        "a" * 64,
        "--code-revision",
        "d" * 40,
        "--labels",
        str(labels_path),
        "--dataset-split",
        dataset_split,
        "--calibration-reference-manifest",
        str(reference_manifest.path),
        "--reference-verification-stamp",
        str(reference_verification.path),
        "--dataset-id",
        "public-simulation-v1",
        "--job-plan",
        str(job_plan_path),
        "--work-dir",
        str(tmp_path / "run-work"),
    ]
    if confirmation_policy_path is not None:
        run_common_args.extend(
            (
                "--confirmation-policy",
                str(confirmation_policy_path),
                "--expected-confirmation-policy-sha256",
                cal.sha256_file(confirmation_policy_path),
            )
        )
    if include_masks:
        for option in (
            "--twentythreeandme-sites",
            "--ancestrydna-sites",
            "--vep-rsids",
        ):
            run_common_args.extend((option, str(site_paths[option])))
    return {
        "planner_args": planner_args,
        "run_common_args": run_common_args,
        "simulation_manifest": simulation_manifest,
        "simulation_verification_path": simulation_verification_path,
        "confirmation_policy_path": confirmation_policy_path,
        "reference_manifest_path": reference_manifest.path,
        "reference_verification_path": reference_verification.path,
        "job_plan_path": job_plan_path,
        "fixture_paths": fixture_paths,
        "truth_paths": truth_paths,
        "marker_truth_paths": marker_truth_paths,
        "tract_truth_paths": tract_truth_paths,
        "site_paths": site_paths,
    }


def _selected_run_args(case, iid: str) -> list[str]:
    return [
        *case["run_common_args"],
        "--fixture",
        f"{iid}={case['fixture_paths'][iid]}",
        "--local-truth",
        f"{iid}={case['truth_paths'][iid]}",
        "--marker-truth",
        f"{iid}={case['marker_truth_paths'][iid]}",
        "--tract-truth",
        f"{iid}={case['tract_truth_paths'][iid]}",
    ]


def test_main_lists_fully_wired_isolated_job_matrix(tmp_path, capsys, monkeypatch):
    case = _prepare_planner_case(tmp_path, monkeypatch)

    exit_code = cal.main([*case["planner_args"], "--list-jobs"])

    assert exit_code == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["job_index"] for row in rows] == list(range(8))
    assert [row["mask"] for row in rows] == [
        "native_unmasked",
        "twentythreeandme_derived_mask",
        "ancestrydna_empirical_mask",
        "synthetic_merged_derived_masks",
    ] * 2
    assert {row["iid"] for row in rows} == {"SIM1", "SIM2"}
    assert {row["dataset_split"] for row in rows} == {"calibration"}
    assert len({row["configuration_sha256"] for row in rows}) == 1
    assert len(rows[0]["configuration_sha256"]) == 64
    for index, expected in enumerate(rows):
        configuration, job, verification = cal.lai_coverage_plan.read_job_plan(
            case["job_plan_path"],
            rows[0]["configuration_sha256"],
            index,
        )
        assert job == {
            key: value for key, value in expected.items() if key != "configuration_sha256"
        }
    assert configuration["job_matrix"]["count"] == 8
    assert configuration["job_matrix"]["max_jobs"] == 100_000
    assert configuration["job_matrix"]["axes"] == {
        "dataset_split": "calibration",
        "fixture_masks": [
            {
                "iid": iid,
                "masks": [
                    "native_unmasked",
                    "twentythreeandme_derived_mask",
                    "ancestrydna_empirical_mask",
                    "synthetic_merged_derived_masks",
                ],
            }
            for iid in ("SIM1", "SIM2")
        ],
        "chromosome_drop_scenarios": ["none"],
        "fractions": ["1"],
        "seeds": [42],
    }
    assert json.loads(case["job_plan_path"].read_text(encoding="utf-8"))["schema_version"] == 3
    assert set(verification["fixtures"]) == {"SIM1", "SIM2"}


def test_final_confirmation_matrix_comes_only_from_frozen_policy(
    tmp_path,
    capsys,
    monkeypatch,
):
    case = _prepare_planner_case(
        tmp_path,
        monkeypatch,
        dataset_split="final_confirmation",
    )

    assert cal.main([*case["planner_args"], "--list-jobs"]) == 0

    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows) == 24
    assert {row["iid"] for row in rows} == {"SIMFINAL1", "SIMFINAL2"}
    assert {row["dataset_split"] for row in rows} == {"final_confirmation"}
    assert {row["mask"] for row in rows} == {
        "twentythreeandme_derived_mask",
        "ancestrydna_empirical_mask",
        "synthetic_merged_derived_masks",
    }
    assert "native_unmasked" not in {row["mask"] for row in rows}
    assert {row["fraction"] for row in rows} == {"0.5", "1"}
    assert {row["seed"] for row in rows} == {17}
    assert {row["chromosome_drop_scenario"] for row in rows} == {
        "none",
        "drop_chr22",
    }
    configuration, _job, verification = cal.lai_coverage_plan.read_job_plan(
        case["job_plan_path"],
        rows[0]["configuration_sha256"],
        0,
    )
    policy_path = case["confirmation_policy_path"]
    policy_config = configuration["inputs"]["confirmation_policy"]
    assert policy_config == {
        "schema_version": 1,
        "filename": policy_path.name,
        "sha256": cal.sha256_file(policy_path),
        "policy_id": "lai-coverage-confirmation-v1",
        "frozen": True,
        "dataset_id": "public-simulation-v1",
        "bundle_artifact_sha256": "a" * 64,
        "simulation_manifest_sha256": case["simulation_manifest"].sha256,
        "code_revision": "d" * 40,
        "confirmation_commitment": cal.compute_final_confirmation_split_commitment(
            case["simulation_manifest"]
        ),
        "calibration_plan": {
            "configuration_sha256": "1" * 64,
            "job_plan_sha256": "2" * 64,
            "expected_job_count": 8,
        },
        "calibration_observation_sha256": "3" * 64,
        "selection_provenance": {
            "script_sha256": "4" * 64,
            "report_sha256": "5" * 64,
            "code_revision": "d" * 40,
        },
    }
    assert configuration["threshold_selected"] == "lai-coverage-confirmation-v1"
    assert verification["confirmation_policy"]["sha256"] == cal.sha256_file(policy_path)


def _remove_option_with_value(arguments: list[str], option: str) -> list[str]:
    result = list(arguments)
    index = result.index(option)
    del result[index : index + 2]
    return result


def _replace_option_value(arguments: list[str], option: str, value: str) -> list[str]:
    result = list(arguments)
    result[result.index(option) + 1] = value
    return result


def test_final_confirmation_planning_requires_policy(tmp_path, capsys, monkeypatch):
    case = _prepare_planner_case(
        tmp_path,
        monkeypatch,
        dataset_split="final_confirmation",
    )
    arguments = _remove_option_with_value(case["planner_args"], "--confirmation-policy")

    with pytest.raises(SystemExit) as exc_info:
        cal.main([*arguments, "--list-jobs"])

    assert exc_info.value.code == 2
    assert "requires --confirmation-policy" in capsys.readouterr().err


def test_final_reference_verification_requires_policy_before_truth_open(
    tmp_path,
    capsys,
    monkeypatch,
):
    case = _prepare_planner_case(
        tmp_path,
        monkeypatch,
        dataset_split="final_confirmation",
    )
    arguments = _remove_option_with_value(case["planner_args"], "--confirmation-policy")
    arguments = _remove_option_with_value(
        arguments,
        "--expected-confirmation-policy-sha256",
    )
    arguments = _remove_option_with_value(arguments, "--job-plan")

    with pytest.raises(SystemExit) as exc_info:
        cal.main([*arguments, "--verify-reference"])

    assert exc_info.value.code == 2
    assert "requires --confirmation-policy" in capsys.readouterr().err


def test_final_confirmation_planning_requires_expected_policy_hash(
    tmp_path,
    capsys,
    monkeypatch,
):
    case = _prepare_planner_case(
        tmp_path,
        monkeypatch,
        dataset_split="final_confirmation",
    )
    arguments = _remove_option_with_value(
        case["planner_args"],
        "--expected-confirmation-policy-sha256",
    )

    with pytest.raises(SystemExit) as exc_info:
        cal.main([*arguments, "--list-jobs"])

    assert exc_info.value.code == 2
    assert "requires --expected-confirmation-policy-sha256" in capsys.readouterr().err


def test_final_confirmation_planning_rejects_wrong_expected_policy_hash(
    tmp_path,
    capsys,
    monkeypatch,
):
    case = _prepare_planner_case(
        tmp_path,
        monkeypatch,
        dataset_split="final_confirmation",
    )
    arguments = _replace_option_value(
        case["planner_args"],
        "--expected-confirmation-policy-sha256",
        "0" * 64,
    )

    with pytest.raises(SystemExit) as exc_info:
        cal.main([*arguments, "--list-jobs"])

    assert exc_info.value.code == 2
    assert "policy SHA-256 does not match expected identity" in capsys.readouterr().err


def test_final_confirmation_planning_rejects_wrong_sealed_split_commitment(
    tmp_path,
    capsys,
    monkeypatch,
):
    case = _prepare_planner_case(
        tmp_path,
        monkeypatch,
        dataset_split="final_confirmation",
    )
    policy_path = case["confirmation_policy_path"]
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    payload["confirmation_commitment"] = "0" * 64
    policy_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    arguments = _replace_option_value(
        case["planner_args"],
        "--expected-confirmation-policy-sha256",
        cal.sha256_file(policy_path),
    )

    with pytest.raises(SystemExit) as exc_info:
        cal.main([*arguments, "--list-jobs"])

    assert exc_info.value.code == 2
    assert "confirmation_commitment does not match expected identity" in capsys.readouterr().err


def test_calibration_planning_rejects_confirmation_policy(tmp_path, capsys, monkeypatch):
    case = _prepare_planner_case(tmp_path, monkeypatch)
    policy_path = _write_confirmation_policy(tmp_path, case["simulation_manifest"])

    with pytest.raises(SystemExit) as exc_info:
        cal.main(
            [
                *case["planner_args"],
                "--confirmation-policy",
                str(policy_path),
                "--list-jobs",
            ]
        )

    assert exc_info.value.code == 2
    assert "calibration rejects" in capsys.readouterr().err


def test_calibration_planning_rejects_expected_confirmation_policy_hash(
    tmp_path,
    capsys,
    monkeypatch,
):
    case = _prepare_planner_case(tmp_path, monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        cal.main(
            [
                *case["planner_args"],
                "--expected-confirmation-policy-sha256",
                "0" * 64,
                "--list-jobs",
            ]
        )

    assert exc_info.value.code == 2
    assert "calibration rejects" in capsys.readouterr().err


@pytest.mark.parametrize(
    "override",
    [
        ("--fraction", "1"),
        ("--seed", "17"),
        ("--drop-scenario", "drop_chr1=1"),
    ],
)
def test_final_confirmation_planning_rejects_every_cli_matrix_override(
    tmp_path,
    capsys,
    monkeypatch,
    override,
):
    case = _prepare_planner_case(
        tmp_path,
        monkeypatch,
        dataset_split="final_confirmation",
    )

    with pytest.raises(SystemExit) as exc_info:
        cal.main([*case["planner_args"], *override, "--list-jobs"])

    assert exc_info.value.code == 2
    assert "rejects matrix-definition option" in capsys.readouterr().err


def test_simulation_verifier_cannot_self_attest_generator_script(
    tmp_path,
    monkeypatch,
):
    case = _prepare_planner_case(tmp_path, monkeypatch)
    report_path = case["simulation_verification_path"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["verifier"]["script_sha256"] = case["simulation_manifest"].generator["script_sha256"]
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ValueError, match="verifier script must differ"):
        cal.read_simulation_verification_report(
            report_path,
            case["simulation_manifest"],
            dataset_split="calibration",
        )


def test_planner_requires_every_supported_input_shape(tmp_path, capsys, monkeypatch):
    case = _prepare_planner_case(tmp_path, monkeypatch, include_masks=False)

    with pytest.raises(SystemExit) as exc_info:
        cal.main([*case["planner_args"], "--list-jobs"])

    assert exc_info.value.code == 2
    assert "requires both --twentythreeandme-sites" in capsys.readouterr().err


def test_planner_enforces_explicit_max_jobs(tmp_path, capsys, monkeypatch):
    case = _prepare_planner_case(tmp_path, monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        cal.main([*case["planner_args"], "--max-jobs", "7", "--list-jobs"])

    assert exc_info.value.code == 2
    assert "exceeding max_jobs=7" in capsys.readouterr().err


def test_inference_rejects_planner_only_max_jobs(tmp_path, capsys, monkeypatch):
    case = _prepare_planner_case(tmp_path, monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        cal.main(
            [
                *case["planner_args"],
                "--max-jobs",
                "8",
                "--job-index",
                "0",
                "--expected-configuration-sha256",
                "0" * 64,
                "--output",
                str(tmp_path / "result.jsonl"),
            ]
        )

    assert exc_info.value.code == 2
    assert "--max-jobs is only valid with --list-jobs" in capsys.readouterr().err


def test_planned_job_hydrates_only_selected_fixture_and_mask(
    tmp_path,
    capsys,
    monkeypatch,
):
    case = _prepare_planner_case(
        tmp_path,
        monkeypatch,
        calibration_iids=("SIM1", "SIM2"),
        include_masks=True,
    )
    assert cal.main([*case["planner_args"], "--list-jobs"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    selected = next(
        row
        for row in rows
        if row["iid"] == "SIM1" and row["mask"] == "twentythreeandme_derived_mask"
    )
    for paths in (
        case["fixture_paths"],
        case["truth_paths"],
        case["marker_truth_paths"],
        case["tract_truth_paths"],
    ):
        paths["SIM2"].unlink()

    fixture_reads: list[str] = []
    manifest_reads: list[str] = []
    stable_reads: list[Path] = []
    original_read_fixture = cal.read_fixture
    original_read_site_manifest = cal.read_site_manifest
    original_stable_read = cal.stable_read

    def observed_read_fixture(iid, *args, **kwargs):
        fixture_reads.append(iid)
        return original_read_fixture(iid, *args, **kwargs)

    def observed_read_site_manifest(name, *args, **kwargs):
        manifest_reads.append(name)
        return original_read_site_manifest(name, *args, **kwargs)

    def observed_stable_read(path, *args, **kwargs):
        stable_reads.append(path)
        return original_stable_read(path, *args, **kwargs)

    monkeypatch.setattr(cal, "read_fixture", observed_read_fixture)
    monkeypatch.setattr(cal, "read_site_manifest", observed_read_site_manifest)
    monkeypatch.setattr(cal, "stable_read", observed_stable_read)
    monkeypatch.setattr(
        cal,
        "run_job",
        lambda job, **_kwargs: {"status": "ok", "job_index": job.index},
    )
    output = tmp_path / "selected-job.jsonl"
    inference_args = [
        *_selected_run_args(case, "SIM1"),
        "--job-index",
        str(selected["job_index"]),
        "--expected-configuration-sha256",
        selected["configuration_sha256"],
        "--output",
        str(output),
    ]

    assert cal.main(inference_args) == 0

    assert fixture_reads
    assert set(fixture_reads) == {"SIM1"}
    assert manifest_reads
    assert set(manifest_reads) == {"twentythreeandme_v5_derived_site_mask"}
    assert case["simulation_manifest"].path not in stable_reads
    assert case["reference_manifest_path"] in stable_reads
    assert case["reference_verification_path"] in stable_reads
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "job_index": selected["job_index"],
        "status": "ok",
    }


def test_final_planned_job_authenticates_policy_and_passes_provenance(
    tmp_path,
    capsys,
    monkeypatch,
):
    case = _prepare_planner_case(
        tmp_path,
        monkeypatch,
        dataset_split="final_confirmation",
    )
    assert cal.main([*case["planner_args"], "--list-jobs"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    selected = next(
        row
        for row in rows
        if row["iid"] == "SIMFINAL1" and row["mask"] == "twentythreeandme_derived_mask"
    )
    configuration, _row, _verification = cal.lai_coverage_plan.read_job_plan(
        case["job_plan_path"],
        selected["configuration_sha256"],
        selected["job_index"],
    )
    observed: dict[str, object] = {}

    def observed_run_job(job, **kwargs):
        observed.update(kwargs)
        return {"status": "ok", "job_index": job.index}

    monkeypatch.setattr(cal, "run_job", observed_run_job)
    output = tmp_path / "final-result.jsonl"

    assert (
        cal.main(
            [
                *_selected_run_args(case, "SIMFINAL1"),
                "--job-index",
                str(selected["job_index"]),
                "--expected-configuration-sha256",
                selected["configuration_sha256"],
                "--output",
                str(output),
            ]
        )
        == 0
    )

    assert (
        observed["confirmation_policy_provenance_entry"]
        == configuration["inputs"]["confirmation_policy"]
    )
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "job_index": selected["job_index"],
        "status": "ok",
    }


def test_final_planned_job_requires_live_policy(tmp_path, capsys, monkeypatch):
    case = _prepare_planner_case(
        tmp_path,
        monkeypatch,
        dataset_split="final_confirmation",
    )
    assert cal.main([*case["planner_args"], "--list-jobs"]) == 0
    selected = json.loads(capsys.readouterr().out.splitlines()[0])
    inference_args = _remove_option_with_value(
        _selected_run_args(case, "SIMFINAL1"),
        "--confirmation-policy",
    )

    with pytest.raises(SystemExit) as exc_info:
        cal.main(
            [
                *inference_args,
                "--job-index",
                str(selected["job_index"]),
                "--expected-configuration-sha256",
                selected["configuration_sha256"],
                "--output",
                str(tmp_path / "must-not-exist.jsonl"),
            ]
        )

    assert exc_info.value.code == 2
    assert "requires --confirmation-policy" in capsys.readouterr().err


def test_final_planned_job_requires_expected_policy_hash(tmp_path, capsys, monkeypatch):
    case = _prepare_planner_case(
        tmp_path,
        monkeypatch,
        dataset_split="final_confirmation",
    )
    assert cal.main([*case["planner_args"], "--list-jobs"]) == 0
    selected = json.loads(capsys.readouterr().out.splitlines()[0])
    inference_args = _remove_option_with_value(
        _selected_run_args(case, "SIMFINAL1"),
        "--expected-confirmation-policy-sha256",
    )

    with pytest.raises(SystemExit) as exc_info:
        cal.main(
            [
                *inference_args,
                "--job-index",
                str(selected["job_index"]),
                "--expected-configuration-sha256",
                selected["configuration_sha256"],
                "--output",
                str(tmp_path / "must-not-exist.jsonl"),
            ]
        )

    assert exc_info.value.code == 2
    assert "requires --expected-confirmation-policy-sha256" in capsys.readouterr().err


def test_final_planned_job_rejects_wrong_expected_policy_hash(
    tmp_path,
    capsys,
    monkeypatch,
):
    case = _prepare_planner_case(
        tmp_path,
        monkeypatch,
        dataset_split="final_confirmation",
    )
    assert cal.main([*case["planner_args"], "--list-jobs"]) == 0
    selected = json.loads(capsys.readouterr().out.splitlines()[0])
    inference_args = _replace_option_value(
        _selected_run_args(case, "SIMFINAL1"),
        "--expected-confirmation-policy-sha256",
        "0" * 64,
    )
    inference_called = False

    def unexpected_run_job(*_args, **_kwargs):
        nonlocal inference_called
        inference_called = True
        return {"status": "ok"}

    monkeypatch.setattr(cal, "run_job", unexpected_run_job)

    with pytest.raises(SystemExit) as exc_info:
        cal.main(
            [
                *inference_args,
                "--job-index",
                str(selected["job_index"]),
                "--expected-configuration-sha256",
                selected["configuration_sha256"],
                "--output",
                str(tmp_path / "must-not-exist.jsonl"),
            ]
        )

    assert exc_info.value.code == 2
    assert "policy SHA-256 does not match expected identity" in capsys.readouterr().err
    assert inference_called is False


@pytest.mark.parametrize(
    "override",
    [
        ("--fraction", "1"),
        ("--seed", "17"),
        ("--drop-scenario", "drop_chr1=1"),
    ],
)
def test_final_planned_job_rejects_cli_matrix_override(
    tmp_path,
    capsys,
    monkeypatch,
    override,
):
    case = _prepare_planner_case(
        tmp_path,
        monkeypatch,
        dataset_split="final_confirmation",
    )
    assert cal.main([*case["planner_args"], "--list-jobs"]) == 0
    selected = json.loads(capsys.readouterr().out.splitlines()[0])

    with pytest.raises(SystemExit) as exc_info:
        cal.main(
            [
                *_selected_run_args(case, "SIMFINAL1"),
                *override,
                "--job-index",
                str(selected["job_index"]),
                "--expected-configuration-sha256",
                selected["configuration_sha256"],
                "--output",
                str(tmp_path / "must-not-exist.jsonl"),
            ]
        )

    assert exc_info.value.code == 2
    assert "planned inference rejects matrix-definition" in capsys.readouterr().err


def test_final_planned_job_rejects_policy_tamper_before_inference(
    tmp_path,
    capsys,
    monkeypatch,
):
    case = _prepare_planner_case(
        tmp_path,
        monkeypatch,
        dataset_split="final_confirmation",
    )
    assert cal.main([*case["planner_args"], "--list-jobs"]) == 0
    selected = json.loads(capsys.readouterr().out.splitlines()[0])
    with case["confirmation_policy_path"].open("a", encoding="utf-8") as handle:
        handle.write("\n")
    inference_called = False

    def unexpected_run_job(*_args, **_kwargs):
        nonlocal inference_called
        inference_called = True
        return {"status": "ok"}

    monkeypatch.setattr(cal, "run_job", unexpected_run_job)

    with pytest.raises(SystemExit) as exc_info:
        cal.main(
            [
                *_selected_run_args(case, "SIMFINAL1"),
                "--job-index",
                str(selected["job_index"]),
                "--expected-configuration-sha256",
                selected["configuration_sha256"],
                "--output",
                str(tmp_path / "must-not-exist.jsonl"),
            ]
        )

    assert exc_info.value.code == 2
    assert "planned input changed after validation" in capsys.readouterr().err
    assert inference_called is False


def test_final_planned_job_rechecks_policy_after_inference(
    tmp_path,
    capsys,
    monkeypatch,
):
    case = _prepare_planner_case(
        tmp_path,
        monkeypatch,
        dataset_split="final_confirmation",
    )
    assert cal.main([*case["planner_args"], "--list-jobs"]) == 0
    selected = json.loads(capsys.readouterr().out.splitlines()[0])
    policy_path = case["confirmation_policy_path"]

    def drifting_run_job(job, **_kwargs):
        with policy_path.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        return {"status": "ok", "job_index": job.index}

    monkeypatch.setattr(cal, "run_job", drifting_run_job)
    output = tmp_path / "must-not-commit.jsonl"

    with pytest.raises(SystemExit) as exc_info:
        cal.main(
            [
                *_selected_run_args(case, "SIMFINAL1"),
                "--job-index",
                str(selected["job_index"]),
                "--expected-configuration-sha256",
                selected["configuration_sha256"],
                "--output",
                str(output),
            ]
        )

    assert exc_info.value.code == 2
    assert "planned input changed after validation" in capsys.readouterr().err
    assert not output.exists()


def test_job_plan_rejects_tampered_job_row(tmp_path):
    verification = {"fixtures": {}, "site_masks": {}, "identifier_manifests": {}}
    plan_path = tmp_path / "tampered-plan.json"
    result = cal.lai_coverage_plan.build_job_plan(
        plan_path,
        configuration={"dataset_id": "fixture-v1"},
        input_verification=verification,
        dataset_split="calibration",
        fixture_masks=(("SIM1", ("native_unmasked",)),),
        drop_scenarios=("none",),
        fractions=("1",),
        seeds=(42,),
        disk_reserve_bytes=0,
    )
    shard_path = tmp_path / result.shards_directory / "00000000.json"
    shard_payload = json.loads(shard_path.read_text(encoding="utf-8"))
    shard_payload["job"]["seed"] = 43
    cal.atomic_write_json(shard_path, shard_payload)

    with pytest.raises(ValueError, match="row does not match the Cartesian axes"):
        cal.lai_coverage_plan.read_job_plan(
            plan_path,
            result.configuration_sha256,
            0,
        )


def test_planned_job_rejects_selected_mask_drift_before_inference(
    tmp_path,
    capsys,
    monkeypatch,
):
    case = _prepare_planner_case(tmp_path, monkeypatch, include_masks=True)
    assert cal.main([*case["planner_args"], "--list-jobs"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    selected = next(row for row in rows if row["mask"] == "twentythreeandme_derived_mask")
    with case["site_paths"]["--twentythreeandme-sites"].open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write("rs-drift\t1\t999999\n")
    inference_called = False

    def unexpected_run_job(*_args, **_kwargs):
        nonlocal inference_called
        inference_called = True
        return {"status": "ok"}

    monkeypatch.setattr(cal, "run_job", unexpected_run_job)
    with pytest.raises(SystemExit) as exc_info:
        cal.main(
            [
                *_selected_run_args(case, "SIM1"),
                "--job-index",
                str(selected["job_index"]),
                "--expected-configuration-sha256",
                selected["configuration_sha256"],
                "--output",
                str(tmp_path / "must-not-exist.jsonl"),
            ]
        )

    assert exc_info.value.code == 2
    assert "planned input changed after validation" in capsys.readouterr().err
    assert inference_called is False


def test_planned_job_rechecks_reference_after_inference_and_cleans_attempt(
    tmp_path,
    capsys,
    monkeypatch,
):
    case = _prepare_planner_case(tmp_path, monkeypatch)
    assert cal.main([*case["planner_args"], "--list-jobs"]) == 0
    row = json.loads(capsys.readouterr().out.splitlines()[0])
    attempt_dirs: list[Path] = []
    monkeypatch.setattr(
        cal,
        "run_job",
        lambda _job, **kwargs: (
            attempt_dirs.append(kwargs["work_dir"])
            or {"status": "ok", "job_index": row["job_index"]}
        ),
    )
    original_validate = cal.validate_reference_verification
    validation_calls = 0

    def drifting_validate(*args, **kwargs):
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 2:
            raise ValueError("reference drift after inference")
        return original_validate(*args, **kwargs)

    monkeypatch.setattr(cal, "validate_reference_verification", drifting_validate)
    output = tmp_path / "must-not-commit.jsonl"

    with pytest.raises(SystemExit) as exc_info:
        cal.main(
            [
                *_selected_run_args(case, "SIM1"),
                "--job-index",
                str(row["job_index"]),
                "--expected-configuration-sha256",
                row["configuration_sha256"],
                "--output",
                str(output),
            ]
        )

    assert exc_info.value.code == 2
    assert "reference drift after inference" in capsys.readouterr().err
    assert not output.exists()
    assert len(attempt_dirs) == 1
    assert not attempt_dirs[0].exists()


def test_atomic_jsonl_writer_replaces_complete_destination(tmp_path):
    output = tmp_path / "nested" / "job.jsonl"
    output.parent.mkdir()
    output.write_text("stale\n", encoding="utf-8")

    cal.atomic_write_jsonl(output, [{"job": 1}, {"job": 2}])

    assert [json.loads(line) for line in output.read_text().splitlines()] == [
        {"job": 1},
        {"job": 2},
    ]
    assert list(output.parent.glob(f".{output.name}.*.tmp")) == []


def test_job_plan_inode_preflight_rejects_insufficient_capacity(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cal.os,
        "statvfs",
        lambda _path: SimpleNamespace(f_favail=cal.JOB_PLAN_INODE_RESERVE + 9),
    )

    with pytest.raises(OSError, match="insufficient free inodes"):
        cal.require_job_plan_inode_capacity(tmp_path / "plan.json", row_count=10)
