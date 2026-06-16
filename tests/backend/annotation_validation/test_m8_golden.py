"""M8 — Golden end-to-end snapshot (live path).

Runs the full live pipeline on a fixed synthetic sample + frozen reference
subset and diffs the result (per-category finding counts + the M1 carriage
table) against a committed golden file. Because the snapshot is *produced by the
live path*, it is the standing backstop that makes the orphaned-code defect
class (F1/F17/F18) impossible to reintroduce: any regression to a
genotype-agnostic engine shifts the carriage table and the diff fails.

The committed golden file is live and active: ``test_golden_snapshot_matches``
is a hard ``assert snapshot == golden`` with **no** ``xfail`` marker, so a diff
is a *real* regression — typically a slide back to a genotype-agnostic engine,
which shifts the carriage table — and must never be waved off as an expected
failure. Regenerate the golden with ``YELIZTLI_UPDATE_GOLDEN=1`` only as the
intentional refresh after a *verified* behavior change. The snapshot-builder
itself is also exercised on every run (it must not error).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from backend.analysis.qc_carriage import audit_carriage
from tests.backend.annotation_validation.conftest import (
    clinvar_row,
    with_chry_noise_floor,
    with_xx_scaffold,
)

GOLDEN_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "fixtures"
    / "annotation_validation"
    / "golden_findings.json"
)

# A small, fixed sample exercising carriage, sex, multi-allelic and merge.
_GOLDEN_VARIANTS = with_xx_scaffold(
    [
        {"rsid": "rs_het", "chrom": "7", "pos": 100, "genotype": "GA"},
        {"rsid": "rs_homalt", "chrom": "7", "pos": 200, "genotype": "AA"},
        {"rsid": "rs_homref", "chrom": "7", "pos": 300, "genotype": "GG"},
        *with_chry_noise_floor({"rsid": "rs_y", "chrom": "Y", "pos": 2_700_000, "genotype": "GG"}),
    ]
)
_GOLDEN_CLINVAR = [
    clinvar_row("rs_het", "7", 100, "G", "A", "Pathogenic", 3),
    clinvar_row("rs_homalt", "7", 200, "G", "A", "Pathogenic", 3),
    clinvar_row("rs_homref", "7", 300, "G", "A", "Pathogenic", 3),
    clinvar_row("rs_y", "Y", 2_700_000, "A", "G", "Pathogenic", 2, gene="SRY"),
]


def _build_snapshot(run) -> dict:
    counts: dict[str, int] = {}
    for f in run.findings:
        key = f"{f.module}:{f.category}"
        counts[key] = counts.get(key, 0) + 1
    carriage = audit_carriage(run.sample_engine, run.registry.reference_engine).as_dict()
    return {
        "findings_counts": dict(sorted(counts.items())),
        "carriage_table": carriage,
    }


def _golden_run(build_live_run):
    return build_live_run(variants=_GOLDEN_VARIANTS, clinvar=_GOLDEN_CLINVAR)


def test_snapshot_builder_runs(build_live_run) -> None:
    """The snapshot builder produces a well-formed dict (exercised every run)."""
    snapshot = _build_snapshot(_golden_run(build_live_run))
    assert "findings_counts" in snapshot
    assert "carriage_table" in snapshot
    # Guard the snapshot *shape*, not just key presence — a malformed builder
    # (e.g. counts as a list) would otherwise pass here and then fail opaquely on
    # the golden diff in test_golden_snapshot_matches.
    assert isinstance(snapshot["findings_counts"], dict)
    assert isinstance(snapshot["carriage_table"], dict)


def test_golden_snapshot_matches(build_live_run) -> None:
    snapshot = _build_snapshot(_golden_run(build_live_run))

    if os.environ.get("YELIZTLI_UPDATE_GOLDEN") == "1":
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")

    assert GOLDEN_PATH.exists(), (
        "golden snapshot missing — regenerate with YELIZTLI_UPDATE_GOLDEN=1 "
        "after a verified behavior change"
    )
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    # A diff here is a REAL regression (not an expected xfail): the live snapshot
    # drifted from the committed golden. Investigate before regenerating — only
    # refresh the golden once the new behavior is verified correct.
    assert snapshot == golden
