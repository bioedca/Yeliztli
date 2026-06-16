"""Generalized docs↔panel rsID consistency guard (#957, follow-up to #880).

A module doc page must never advertise a marker the product no longer scores —
the #880 class, where ``docs/modules/wellness/sleep.md`` kept citing the PER3
VNTR ``rs57875989`` after ``sleep_panel.json`` dropped it in #615. The focused
sleep guard (``test_sleep_docs_consistency.py``) is retained as the *named* PER3
regression; this generalizes its idea across every module page.

Two complementary checks (see #957's design caveat: a naive "every doc rsID ⊆
panel" sweep has false positives because some pages legitimately cite non-locus
rsIDs — worked examples, cross-references, context-only prose — so the broad
check carries a documented allowlist):

1. **Curated per-doc → panel check.** For module pages that map cleanly to one
   topical panel, every rsID the page cites must appear in that panel's JSON
   (the markers the module references). Catches a page promising a marker its
   own panel dropped.
2. **Union safety net.** Every rsID cited in *any* ``docs/modules/**/*.md`` page
   must exist in at least one panel — or be in the documented per-page allowlist
   of legitimately-cited non-locus rsIDs. Catches a marker dropped from *all*
   panels (the literal #880 case) even on pages without a clean 1:1 panel.

Audit basis (2026-06-16): of the 23 module pages, only 8 cite any rsID; all
their cited rsIDs resolve to a panel except the two context-only *BCHE* variants
on ``specialized.md`` (allowlisted below). No drift exists at introduction —
this guard locks that in.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_PANELS_DIR = REPO_ROOT / "backend" / "data" / "panels"
_DOCS_DIR = REPO_ROOT / "docs" / "modules"

_RSID_RE = re.compile(r"rs\d+")

# ── Check A: pages with a clean 1:1 topical panel ──────────────────────────
# doc (relative to docs/modules/) → panel filename. Only pages whose cited
# rsIDs all belong to a single topical panel are listed; cross-cutting pages
# (apoe — code-matched, no panel JSON; metabolic — PRS markers spanning several
# panels; specialized — many small panels) are intentionally covered only by the
# union check below, not force-mapped to one panel.
_CLEAN_DOC_PANEL = {
    "wellness/sleep.md": "sleep_panel.json",
    "wellness/fitness.md": "fitness_panel.json",
    "gated/parkinsons.md": "parkinsons_panel.json",
    "health-risk/carrier-status.md": "carrier_panel.json",
    # FH monogenic variants (e.g. APOB rs5742904) are scored in the
    # cardiovascular panel — FH has no separate panel JSON.
    "health-risk/familial-hypercholesterolemia.md": "cardiovascular_panel.json",
}

# ── Check B: per-page allowlist of legitimately-cited non-locus rsIDs ───────
# rsIDs a page mentions in prose that are NOT scored loci in any panel. Each
# needs a justification so the allowlist stays honest and auditable.
_DOC_NON_LOCUS_ALLOWLIST: dict[str, dict[str, str]] = {
    "specialized.md": {
        "rs1799807": (
            "BCHE 'atypical' variant — the BChE section is explicit context-only "
            "background and 'does not store findings'; not a scored locus."
        ),
        "rs1803274": (
            "BCHE 'K-variant' — same context-only BChE background; not a scored "
            "locus (true BChE deficiency is confirmed by enzyme-activity assay)."
        ),
    },
}


def _doc_rsids(path: Path) -> set[str]:
    return set(_RSID_RE.findall(path.read_text(encoding="utf-8")))


def _panel_rsids(panel_filename: str) -> set[str]:
    """Every rsID the panel JSON references (loci, ClinVar match lists, proxies,
    aliases — the union of all rsID-bearing fields)."""
    return set(_RSID_RE.findall((_PANELS_DIR / panel_filename).read_text(encoding="utf-8")))


def _all_panel_rsids() -> set[str]:
    rsids: set[str] = set()
    for panel in _PANELS_DIR.glob("*.json"):
        rsids |= set(_RSID_RE.findall(panel.read_text(encoding="utf-8")))
    return rsids


def _docs_citing_rsids() -> list[Path]:
    return sorted(
        p for p in _DOCS_DIR.rglob("*.md") if _RSID_RE.search(p.read_text(encoding="utf-8"))
    )


@pytest.mark.parametrize("doc_rel, panel", sorted(_CLEAN_DOC_PANEL.items()))
def test_mapped_doc_cites_only_panel_rsids(doc_rel: str, panel: str) -> None:
    """Check A: a cleanly-mapped page cites no rsID absent from its panel."""
    doc_path = _DOCS_DIR / doc_rel
    assert doc_path.exists(), f"mapped doc missing: {doc_rel}"
    cited = _doc_rsids(doc_path)
    assert cited, f"{doc_rel} is mapped to a panel but cites no rsID — drop it from the map"
    stale = cited - _panel_rsids(panel)
    assert not stale, (
        f"docs/modules/{doc_rel} cites rsID(s) absent from {panel}: {sorted(stale)}. "
        f"A marker may have been removed from the panel (cf. the PER3 VNTR rs57875989 "
        f"in #615/#880) — update the page, or if it's a legitimate non-locus mention add "
        f"it to _DOC_NON_LOCUS_ALLOWLIST with a justification."
    )


def test_every_doc_rsid_is_scored_or_allowlisted() -> None:
    """Check B: every module-doc rsID exists in some panel, or is allowlisted."""
    panel_universe = _all_panel_rsids()
    offenders: dict[str, list[str]] = {}
    for doc_path in _docs_citing_rsids():
        rel = doc_path.relative_to(_DOCS_DIR).as_posix()
        allow = set(_DOC_NON_LOCUS_ALLOWLIST.get(doc_path.name, {}))
        unscored = _doc_rsids(doc_path) - panel_universe - allow
        if unscored:
            offenders[rel] = sorted(unscored)
    assert not offenders, (
        "module docs cite rsID(s) that exist in NO panel and are not allowlisted "
        "(a marker dropped from every panel, or a typo): "
        + json.dumps(offenders)
        + ". Fix the page, or add the rsID to _DOC_NON_LOCUS_ALLOWLIST with a justification."
    )


def test_allowlisted_rsids_really_are_non_loci() -> None:
    """Premise guard: an allowlisted rsID must genuinely be absent from every
    panel. If one is later added as a real locus, this trips so the allowlist
    entry is removed rather than masking a now-scored marker."""
    panel_universe = _all_panel_rsids()
    leaked = {
        f"{doc}:{rsid}"
        for doc, entries in _DOC_NON_LOCUS_ALLOWLIST.items()
        for rsid in entries
        if rsid in panel_universe
    }
    assert not leaked, (
        f"allowlisted rsID(s) are now scored panel loci — remove them from "
        f"_DOC_NON_LOCUS_ALLOWLIST: {sorted(leaked)}"
    )
