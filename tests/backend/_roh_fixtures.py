"""Shared ROH test fixture: a segment-eligible marker block.

Three suites need a sample whose own markers could actually carry an ROH
segment, because the legacy-row rule is re-derived from the sample rather than
trusting the stored count. They had three copies of the same 200-marker seed.

The copies are the hazard, not the duplication: if ``MIN_ROH_SNPS`` or
``MIN_ROH_KB`` moves and one copy is missed, that suite's *control* test
silently becomes a withheld-path test — and it keeps passing, because a
withheld row also lacks the "typical result" narrative the control asserts is
absent. The test would then be green for the opposite reason it was written.

So this seed is derived from the detector's own emission rule and asserts the
properties it must satisfy. A constant change that invalidates it fails loudly
here, once, instead of quietly weakening three controls.
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.analysis.roh import MAX_GAP_KB, MIN_ROH_KB, MIN_ROH_SNPS
from backend.db.tables import raw_variants

# Comfortably clear of the count floor rather than exactly at it: a fixture
# sitting on the boundary would flip to unevaluable on any tightening.
ELIGIBLE_MARKER_COUNT = MIN_ROH_SNPS * 2
ELIGIBLE_SPACING_BP = 20_000
_ELIGIBLE_START_BP = 1_000_000

# Fail here, not in a control test that would still pass for the wrong reason.
assert ELIGIBLE_MARKER_COUNT >= MIN_ROH_SNPS, "fixture no longer clears the SNP-count floor"
assert ELIGIBLE_SPACING_BP <= MAX_GAP_KB * 1000, "fixture spacing would break a run at MAX_GAP_KB"
assert ((ELIGIBLE_MARKER_COUNT - 1) * ELIGIBLE_SPACING_BP) / 1000 >= MIN_ROH_KB, (
    "fixture no longer spans MIN_ROH_KB"
)


def seed_segment_eligible_markers(sample_engine: sa.Engine) -> None:
    """Seed one chr1 block dense and long enough that a run could occupy it."""
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(raw_variants),
            [
                {
                    "rsid": f"roh{i}",
                    "chrom": "1",
                    "pos": _ELIGIBLE_START_BP + i * ELIGIBLE_SPACING_BP,
                    "genotype": "AG",
                }
                for i in range(ELIGIBLE_MARKER_COUNT)
            ],
        )
