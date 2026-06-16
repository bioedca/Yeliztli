"""Docs↔panel consistency guard for the Gene Sleep module page (#880).

The PER3 chronotype pathway and its sole marker ``rs57875989`` were removed from
``sleep_panel.json`` in #615 (``rs57875989`` IS the PER3 54-bp VNTR — a
deprecated/unplaced dbSNP record consumer SNP arrays do not type, with no
array-typeable tag SNP replacing it), but ``docs/modules/wellness/sleep.md``
kept advertising them. This locks the docs to the active panel so the page can
never again promise a marker/pathway the module no longer scores:

1. Every rsID the page cites must be a *live* panel locus.
2. The specific removed PER3 VNTR ``rs57875989`` must stay out of the page.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_PANEL_PATH = REPO_ROOT / "backend" / "data" / "panels" / "sleep_panel.json"
_DOC_PATH = REPO_ROOT / "docs" / "modules" / "wellness" / "sleep.md"

# The PER3 VNTR removed in #615 — kept explicit so the regression is named.
_REMOVED_PER3_VNTR = "rs57875989"


def _panel_locus_rsids() -> set[str]:
    """rsIDs of the active sleep-panel pathway SNPs (the markers actually scored)."""
    data = json.loads(_PANEL_PATH.read_text(encoding="utf-8"))
    rsids: set[str] = set()
    for pathway in data.get("pathways", []):
        for snp in pathway.get("snps", []):
            rsid = snp.get("rsid")
            if rsid:
                rsids.add(rsid)
    return rsids


def test_doc_cites_only_live_panel_rsids() -> None:
    doc_rsids = set(re.findall(r"rs\d+", _DOC_PATH.read_text(encoding="utf-8")))
    stale = doc_rsids - _panel_locus_rsids()
    assert not stale, (
        f"docs/modules/wellness/sleep.md cites rsID(s) absent from the active sleep "
        f"panel loci: {sorted(stale)}. Update the docs to match "
        f"backend/data/panels/sleep_panel.json — a marker may have been removed "
        f"(e.g. the PER3 VNTR {_REMOVED_PER3_VNTR} in #615)."
    )


def test_doc_does_not_advertise_removed_per3_marker() -> None:
    text = _DOC_PATH.read_text(encoding="utf-8")
    assert _REMOVED_PER3_VNTR not in text, (
        f"sleep.md still cites the removed PER3 VNTR {_REMOVED_PER3_VNTR} "
        f"(dropped from the panel in #615; see #880)."
    )


def test_removed_marker_really_is_absent_from_panel() -> None:
    # Guards the test's own premise: if the marker is ever re-introduced as a real
    # locus, this trips so the docs-consistency assertions above are revisited too.
    assert _REMOVED_PER3_VNTR not in _panel_locus_rsids()
