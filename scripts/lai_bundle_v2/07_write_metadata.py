#!/usr/bin/env python3
"""Write metadata.json for the v2.0.0 LAI bundle.

Schema per AncestryDNA_Integration_Plan.md §6.5. Pulls validation metrics from
the JSON reports produced in Phase 6 when present.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from gnomix_provenance import (  # noqa: E402
    TRAINING_INPUT_BUNDLE_PATH,
    TRAINING_PROVENANCE_BUNDLE_PATH,
    TRAINING_SPLIT_BUNDLE_PATH,
    ProvenanceError,
    verify_aggregate_snapshot,
    verify_published_model_records,
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tool_version(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=10)
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unavailable"


def _load_gnomix_training_provenance(
    path: Path,
    *,
    bundle_dir: Path,
    training_input_manifest: Path,
    training_split_manifest: Path,
) -> tuple[dict[str, object], dict[str, object], str]:
    """Authenticate one captured aggregate, manifest, and record generation."""
    try:
        data, aggregate_sha256, input_payload = verify_aggregate_snapshot(
            path,
            training_input_manifest=training_input_manifest,
            training_split_manifest=training_split_manifest,
            require_complete=True,
        )
        verify_published_model_records(bundle_dir, data)
    except (OSError, ProvenanceError) as exc:
        raise ValueError(f"invalid Gnomix training provenance manifest {path}: {exc}") from exc
    return data, input_payload, aggregate_sha256


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", required=True, type=Path)
    parser.add_argument("--union-catalog", required=True, type=Path)
    parser.add_argument("--validation-dir", required=True, type=Path)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--build-host", required=True)
    parser.add_argument("--build-date", required=True)
    parser.add_argument("--bundle-version", required=True)
    parser.add_argument("--gnomix-provenance", required=True, type=Path)
    parser.add_argument("--admixture-seed", required=True, type=int)
    args = parser.parse_args()

    bundle = args.bundle_dir

    # Site count = lines in the runtime liftover mapping (one per kept rsid).
    site_map = bundle / "liftover" / "array_site_mapping.tsv"
    site_count = sum(1 for _ in site_map.open()) if site_map.exists() else None

    # Window count: total LAI windows across the genome = sum of each chrom
    # model's W, stored in the re-exported gnomix_models/<chr>/metadata.npz.
    # (The bundle ships the dependency-free npz/json model, not gnomix .pkl, so
    # the old `*.pkl` proxy counted nothing.)
    window_count = 0
    for meta_npz in sorted(bundle.glob("gnomix_models/*/metadata.npz")):
        try:
            window_count += int(np.load(meta_npz, allow_pickle=False)["W"])
        except (OSError, KeyError, ValueError):
            pass

    # Validation metrics — pulled from Phase 6 reports if present.
    accuracy = phasing = heldout = None
    lai_report = args.validation_dir / "lai_accuracy_report.json"
    phase_report = args.validation_dir / "phasing_accuracy_report.json"
    heldout_report = args.validation_dir / "heldout_superpop_accuracy_report.json"
    if lai_report.exists():
        accuracy = json.loads(lai_report.read_text()).get("mean_val_accuracy")
    if phase_report.exists():
        phasing = json.loads(phase_report.read_text()).get("mean_switch_error_rate")
    if heldout_report.exists():
        heldout_data = json.loads(heldout_report.read_text())
        heldout = {
            "overall_accuracy": heldout_data.get("overall_accuracy"),
            "per_region_accuracy": heldout_data.get("per_region_accuracy"),
            "per_region_n": heldout_data.get("per_region_n"),
            "min_region_accuracy": heldout_data.get("min_region_accuracy"),
            "min_eur_accuracy": heldout_data.get("min_eur_accuracy"),
            "eur_accuracy": heldout_data.get("eur_accuracy"),
            "eur_passes": heldout_data.get("eur_passes"),
            "all_regions_pass": heldout_data.get("all_regions_pass"),
        }

    beagle_jar = bundle / "beagle" / "beagle.jar"
    beagle_sha = _sha256(beagle_jar) if beagle_jar.exists() else None
    expected_gnomix_manifest = bundle / TRAINING_PROVENANCE_BUNDLE_PATH
    if args.gnomix_provenance.resolve() != expected_gnomix_manifest.resolve():
        raise ValueError(
            "--gnomix-provenance must point to "
            "metadata/gnomix_training_provenance.json inside the bundle"
        )
    training_input_manifest = bundle / TRAINING_INPUT_BUNDLE_PATH
    training_split_manifest = bundle / TRAINING_SPLIT_BUNDLE_PATH
    gnomix_provenance, training_inputs, gnomix_manifest_sha256 = _load_gnomix_training_provenance(
        args.gnomix_provenance,
        bundle_dir=bundle,
        training_input_manifest=training_input_manifest,
        training_split_manifest=training_split_manifest,
    )
    union_sha256 = _sha256(args.union_catalog)
    marker_sources = training_inputs["source_artifacts"]["marker_selection"]
    union_sources = [
        entry["artifact"]["sha256"] for entry in marker_sources if entry["name"] == "union_catalog"
    ]
    if union_sources != [union_sha256]:
        raise ValueError("union catalog does not match the authenticated training-input manifest")

    meta = {
        "bundle_version": args.bundle_version,
        "build_date": args.build_date or str(date.today()),
        "build_host": args.build_host,
        "git_commit": args.git_commit,
        "source_sites_sha256": union_sha256,
        "tool_versions": {
            "bcftools": _tool_version(["bcftools", "--version"]),
            "beagle_jar_sha256": beagle_sha,
            "admixture": _tool_version(["fastmixture", "--version"]),
        },
        "admixture_seed": args.admixture_seed,
        "gnomix_training": {
            "repository": gnomix_provenance["gnomix_repository"],
            "git_commit": gnomix_provenance["gnomix_git_commit"],
            "checkout_clean": gnomix_provenance["gnomix_checkout_clean"],
            "simulation_run": gnomix_provenance["simulation_run"],
            "effective_config_sha256": gnomix_provenance["effective_config_sha256"],
            "model_count": len(gnomix_provenance["models"]),
            "manifest": TRAINING_PROVENANCE_BUNDLE_PATH,
            "manifest_sha256": gnomix_manifest_sha256,
            "training_input_manifest": {
                "path": TRAINING_INPUT_BUNDLE_PATH,
                **gnomix_provenance["training_input_manifest"],
            },
            "training_split_manifest": {
                "path": TRAINING_SPLIT_BUNDLE_PATH,
                **gnomix_provenance["training_split_manifest"],
            },
        },
        "reference_build": training_inputs["reference_build"],
        "reference_panel": training_inputs["reference_panel"]["name"],
        "sample_identifier_policy": training_inputs["sample_identifier_policy"],
        "site_count": site_count,
        "window_count": window_count,
        "accuracy_per_window_mean": accuracy,
        "phasing_switch_error": phasing,
        "heldout_superpop_accuracy": heldout,
    }
    (bundle / "metadata.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
