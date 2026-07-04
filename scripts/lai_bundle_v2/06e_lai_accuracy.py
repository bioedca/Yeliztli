#!/usr/bin/env python3
"""LAI accuracy from the gnomix per-chromosome training logs.

Matches the proven v1.1 method exactly: gnomix prints its held-out per-window
validation accuracy as `Estimated val accuracy: NN.NN%` to each
`gnomix_train_chr{N}.log` that Phase 5 produces (via `tee`). v1.1 read the
>=0.88 mean per-window LAI accuracy gate straight from those logs
(`grep "val accuracy" gnomix_train_chr*.log` -> mean ~88%, range 85.6-89.8%),
NOT from a separate inference pass.

(Earlier this script globbed `lai_<sample>_chr{N}.tsv` gnomix-inference output
that no phase produces -> it always found 0 files -> accuracy 0.0 -> the gate
could never pass. Rewritten 2026-06-01 against the v1.1 reference, Step 25.)

Output JSON shape:
{
  "per_chrom": [{"chrom": "1", "val_accuracy": 0.8799, "log": "..."}, ...],
  "mean_val_accuracy": <float>,
  "n_chrom": <int>,
  "missing_chroms": [<str>, ...],
  "min_chrom": {"chrom": ..., "val_accuracy": ...} | null,
  "target": <float>,
  "passes": <bool>
}

Plan §6.4 phase 6e.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
from pathlib import Path

# gnomix emits e.g. "Estimated val accuracy: 86.88%" (also "...: 85.7%").
_RE_VAL_ACC = re.compile(r"Estimated val accuracy:\s*([0-9.]+)\s*%")


def parse_val_accuracy(log_text: str) -> float | None:
    """Return the last `Estimated val accuracy: NN.NN%` as a 0-1 fraction, or None.

    The last match wins so a re-run that appends to the same log reflects the
    final model.
    """
    matches = _RE_VAL_ACC.findall(log_text)
    if not matches:
        return None
    return float(matches[-1]) / 100.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log-dir",
        required=True,
        type=Path,
        help="dir holding gnomix_train_chr{N}.log (Phase 5 output)",
    )
    parser.add_argument(
        "--chroms", required=True, type=str, help="space-separated chromosomes, e.g. '1 2 ... 22'"
    )
    parser.add_argument("--out-report", required=True, type=Path)
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=0.88,
        help="mean per-window LAI accuracy gate (Plan §6.4: 0.88)",
    )
    # ── Per-population composition gate (NEW) ──────────────────────────────
    # The mean per-window accuracy above is gnomix's internal training-val score
    # — it is BLIND to per-population balance, which is how v2.0.0 shipped with
    # EUR=3 in training (and misclassified every European as CSA) while still
    # reporting ~0.97. When the phase-04 sample_map is supplied, the gate also
    # fails if any target superpopulation is under-represented. (The gold-
    # standard follow-up is a held-out per-superpopulation INFERENCE accuracy
    # check against the assembled bundle — must include EUR — which requires a
    # bundle run on the cluster; see the PR description.)
    parser.add_argument(
        "--sample-map",
        type=Path,
        default=None,
        help="OPTIONAL phase-04 sample_map.txt (IID<TAB>genetic_region); "
        "enables the per-population training-composition gate",
    )
    parser.add_argument(
        "--min-per-region",
        type=int,
        default=20,
        help="composition gate: min training samples per superpopulation",
    )
    parser.add_argument(
        "--regions",
        type=str,
        default="AFR AMR CSA EAS EUR MID OCE",
        help="target superpopulations the bundle must represent",
    )
    args = parser.parse_args()

    chroms = shlex.split(args.chroms)
    per_chrom = []
    missing = []
    for chrom in chroms:
        log = args.log_dir / f"gnomix_train_chr{chrom}.log"
        acc = parse_val_accuracy(log.read_text()) if log.is_file() else None
        if acc is None:
            missing.append(chrom)
            continue
        per_chrom.append({"chrom": chrom, "val_accuracy": acc, "log": str(log)})

    accs = [r["val_accuracy"] for r in per_chrom]
    mean_acc = sum(accs) / len(accs) if accs else 0.0
    min_chrom = min(per_chrom, key=lambda r: r["val_accuracy"]) if per_chrom else None

    # Per-population training composition (only when the sample_map is supplied).
    train_per_region: dict[str, int] = {}
    under_represented: dict[str, int] = {}
    if args.sample_map is not None and args.sample_map.is_file():
        for line in args.sample_map.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1]:
                train_per_region[parts[1]] = train_per_region.get(parts[1], 0) + 1
        under_represented = {
            r: train_per_region.get(r, 0)
            for r in args.regions.split()
            if train_per_region.get(r, 0) < args.min_per_region
        }
    composition_ok = args.sample_map is None or not under_represented

    passes = bool(accs) and not missing and mean_acc >= args.min_accuracy and composition_ok

    report = {
        "per_chrom": per_chrom,
        "mean_val_accuracy": mean_acc,
        "n_chrom": len(per_chrom),
        "missing_chroms": missing,
        "min_chrom": min_chrom,
        "target": args.min_accuracy,
        "train_per_region": train_per_region,
        "train_min_per_region": args.min_per_region,
        "train_under_represented": under_represented,
        "passes": passes,
    }
    args.out_report.write_text(json.dumps(report, indent=2))
    print(f"mean per-window LAI (gnomix val) accuracy: {mean_acc:.4f} over {len(accs)} chrom")
    if missing:
        print(f"WARNING: no val accuracy parsed for chrom(s): {' '.join(missing)}")
    if under_represented:
        print(
            f"COMPOSITION GATE FAILED: under-represented superpopulations "
            f"(< {args.min_per_region} training samples): {under_represented}"
        )
    print(f"target (v1.1 baseline): >= {args.min_accuracy}")
    if not passes:
        print(f"LAI ACCURACY GATE FAILED: report -> {args.out_report}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
