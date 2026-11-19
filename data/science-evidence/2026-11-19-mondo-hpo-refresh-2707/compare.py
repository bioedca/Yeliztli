"""Calibrate on intact earlier releases, then evaluate fixed rules on the holdout."""

import json
from fractions import Fraction
from itertools import combinations
from pathlib import Path


def rate(case):
    return Fraction(100 * case["matched"], case["matched"] + case["unmatched"])


def results(pairs, scenario, tolerance):
    refusals = {"absolute": 0, "absolute_and_drop": 0, "with_namespace_guard": 0}
    drops = []
    for old, new in pairs:
        prior = old["cases"]["intact"]
        candidate = new["cases"][scenario]
        drop = rate(prior) - rate(candidate)
        drops.append(drop)
        below_floor = rate(candidate) < 40
        reject = below_floor or drop > tolerance
        lost_namespace = any(
            row["matched"] > 0 and candidate["namespaces"].get(key, {}).get("matched", 0) == 0
            for key, row in prior["namespaces"].items()
        )
        refusals["absolute"] += int(below_floor)
        refusals["absolute_and_drop"] += int(reject)
        refusals["with_namespace_guard"] += int(reject or lost_namespace)
    return {
        "comparisons": len(pairs),
        "drop_range_pp": [round(float(min(drops)), 4), round(float(max(drops)), 4)],
        "refused": refusals,
    }


def compare(rows, calibration_end):
    calibration = [r for r in rows if r["release"] <= calibration_end]
    if not 1 < len(calibration) < len(rows) or [r["release"] for r in rows] != sorted(
        {r["release"] for r in rows}
    ):
        raise ValueError("Expected ordered, distinct calibration and holdout releases")
    train = list(combinations(calibration, 2))
    worst, before, after = max(
        (rate(a["cases"]["intact"]) - rate(b["cases"]["intact"]), a["release"], b["release"])
        for a, b in train
    )
    tolerance = int(max(0, worst)) + 1
    pairs = list(combinations(rows, 2))
    groups = {
        "calibration": train,
        "holdout_all_earlier": [(a, b) for a, b in pairs if b["release"] > calibration_end],
        "holdout_adjacent": [
            (a, b) for a, b in zip(rows, rows[1:]) if b["release"] > calibration_end
        ],
        "same_release": [(r, r) for r in rows],
    }
    return {
        "calibration_largest_intact_drop": {
            "prior": before,
            "candidate": after,
            "exact_pp": str(worst),
            "rounded_pp": round(float(worst), 4),
        },
        "tolerance_pp": tolerance,
        "groups": {
            group: {name: results(current, name, tolerance) for name in rows[0]["cases"]}
            for group, current in groups.items()
        },
    }


if __name__ == "__main__":
    raw = Path(__file__).parent / "raw"
    records = json.loads((raw / "measurements.json").read_text())
    cutoff = json.loads((raw / "sources.json").read_text())["calibration_end"]
    print(json.dumps(compare(records, cutoff), indent=2))
