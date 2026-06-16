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
from datetime import date
from pathlib import Path

import numpy as np


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", required=True, type=Path)
    parser.add_argument("--union-catalog", required=True, type=Path)
    parser.add_argument("--validation-dir", required=True, type=Path)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--build-host", required=True)
    parser.add_argument("--build-date", required=True)
    parser.add_argument("--bundle-version", required=True)
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

    meta = {
        "bundle_version": args.bundle_version,
        "build_date": args.build_date or str(date.today()),
        "build_host": args.build_host,
        "git_commit": args.git_commit,
        "source_sites_sha256": _sha256(args.union_catalog),
        "tool_versions": {
            "bcftools": _tool_version(["bcftools", "--version"]),
            "beagle_jar_sha256": beagle_sha,
            "admixture": _tool_version(["fastmixture", "--version"]),
        },
        "admixture_seed": args.admixture_seed,
        "reference_panel": "gnomAD HGDP+1KG v3.1.2 (phased SHAPEIT5)",
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
