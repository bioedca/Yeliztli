#!/usr/bin/env python3
"""Select a held-out validation set from the phase-04 gnomix training panel.

The mean per-window accuracy (06e) is BLIND to per-population correctness — it is
how v2.0.0 first shipped with EUR=3 and misclassified every European while still
reporting ~0.97. The required model-training holdout gate is a per-superpopulation
INFERENCE check (06f_heldout_superpop_accuracy.py): hold a few samples per
superpopulation OUT of Gnomix training, run them through the assembled bundle,
and confirm each classifies to its own superpopulation (EUR must classify as EUR).

This step picks that held-out set (seeded, reproducible), writes it to
``held_out_validation.tsv``, backs up the full panel to ``sample_map.full.txt``,
and rewrites ``sample_map.txt`` to the training (reduced) panel. The held-out
samples stay in the phasing panel (so extract_heldout_fixtures.py can pull their
genotypes) but are removed from the Gnomix founder list. This isolates classifier
training and catches representation regressions; it is not an independent
end-to-end phasing or sparse-coverage test because Beagle can still use each
target donor. Coverage calibration must also remove its donors from the phasing
reference and score simulated, exact local-window truth.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import numpy as np

DEFAULT_REGIONS = ("AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--sample-map", required=True, type=Path, help="phase-04 sample_map.txt (IID<TAB>region)"
    )
    p.add_argument("--n", type=int, default=5, help="held-out samples per superpopulation")
    p.add_argument("--seed", type=int, default=42, help="RNG seed (reproducible selection)")
    p.add_argument(
        "--force",
        action="append",
        default=[],
        help="IID:REGION to force into the held-out set (e.g. HG01502:EUR); repeatable",
    )
    p.add_argument("--out-heldout", required=True, type=Path)
    p.add_argument(
        "--out-training",
        required=True,
        type=Path,
        help="reduced training sample_map (overwrites if same as --sample-map)",
    )
    p.add_argument("--out-full-backup", type=Path, default=None)
    p.add_argument(
        "--min-per-region",
        type=int,
        default=20,
        help="fail if any training region drops below this",
    )
    args = p.parse_args()

    rows = [ln.split("\t") for ln in args.sample_map.read_text().splitlines() if ln.strip()]
    by: dict[str, list[str]] = {}
    for iid, region in rows:
        by.setdefault(region, []).append(iid)

    forced: dict[str, list[str]] = {}
    for spec in args.force:
        iid, region = spec.split(":")
        forced.setdefault(region, []).append(iid)

    rng = np.random.default_rng(args.seed)
    held: list[tuple[str, str]] = []
    train: list[tuple[str, str]] = []
    for region in sorted(by):
        samples = sorted(by[region])
        force_here = [s for s in forced.get(region, []) if s in samples]
        pool = [s for s in samples if s not in force_here]
        need = max(0, args.n - len(force_here))
        pick = list(rng.choice(pool, size=min(need, len(pool)), replace=False)) if need else []
        heldset = set(force_here) | set(pick)
        for s in samples:
            (held if s in heldset else train).append((s, region))

    if args.out_full_backup:
        args.out_full_backup.write_text("\n".join(f"{i}\t{r}" for i, r in rows) + "\n")
    args.out_training.write_text("\n".join(f"{i}\t{r}" for i, r in train) + "\n")
    args.out_heldout.write_text(
        "IID\tgenetic_region\n" + "\n".join(f"{i}\t{r}" for i, r in held) + "\n"
    )

    tc, hc = Counter(r for _, r in train), Counter(r for _, r in held)
    print(f"full={len(rows)}  training={len(train)}  held_out={len(held)}")
    print("region   train  held")
    for r in sorted(by):
        print(f"  {r:5}  {tc[r]:5}  {hc[r]:4}")
    under = {r: tc[r] for r in by if tc[r] < args.min_per_region}
    if under:
        raise SystemExit(
            f"TRAINING UNDER-REPRESENTED after hold-out (<{args.min_per_region}): {under}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
