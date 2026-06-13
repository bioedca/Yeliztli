"""Repo-wide, offline citation-provenance guard (gh #276 / #277).

Scans every curated panel JSON and fails if any **globally unrelated** PMID
(see ``citation_provenance.GLOBALLY_UNRELATED_PMIDS``) reappears. Deterministic
and network-free: it reads the checked-in panels and the checked-in registry,
never PubMed. This prevents the recurring "cites unrelated PMID" class from
silently regressing in *any* panel — complementing the per-panel guards that
handle transposed-but-relevant citations.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.backend.citation_provenance import GLOBALLY_UNRELATED_PMIDS

PANELS_DIR = Path(__file__).resolve().parent.parent.parent / "backend" / "data" / "panels"

# Keys under which curated JSON stores citations (list[str] or a bare str).
_PMID_KEYS = ("pmids", "pmid_citations", "pmid")


def _collect_pmids(obj: object, into: set[str]) -> None:
    """Recursively gather every cited PMID from a loaded JSON document."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in _PMID_KEYS:
                if isinstance(value, str):
                    into.add(value)
                elif isinstance(value, list):
                    into.update(str(v) for v in value)
            _collect_pmids(value, into)
    elif isinstance(obj, list):
        for item in obj:
            _collect_pmids(item, into)


def _panel_files() -> list[Path]:
    files = sorted(PANELS_DIR.glob("*.json"))
    assert files, f"no panel JSON found under {PANELS_DIR}"
    return files


def _pmids_by_panel() -> dict[str, set[str]]:
    """Map each panel filename -> the set of PMIDs it cites."""
    result: dict[str, set[str]] = {}
    for path in _panel_files():
        pmids: set[str] = set()
        _collect_pmids(json.loads(path.read_text(encoding="utf-8")), pmids)
        result[path.name] = pmids
    return result


class TestGlobalCitationProvenance:
    def test_globally_unrelated_pmids_absent_from_all_panels(self) -> None:
        """No off-domain PMID from the registry may appear in any curated panel."""
        by_panel = _pmids_by_panel()
        leaks: dict[str, list[str]] = {}
        for pmid in GLOBALLY_UNRELATED_PMIDS:
            hit = sorted(name for name, pmids in by_panel.items() if pmid in pmids)
            if hit:
                leaks[pmid] = hit
        assert not leaks, (
            "Globally unrelated PMID(s) reappeared in curated panels — these are "
            "off-domain papers that must never back a finding:\n"
            + "\n".join(f"  PMID {p} in {leaks[p]} — {GLOBALLY_UNRELATED_PMIDS[p]}" for p in leaks)
        )

    @pytest.mark.parametrize("pmid", sorted(GLOBALLY_UNRELATED_PMIDS))
    def test_registry_entries_well_formed(self, pmid: str) -> None:
        """Each registry key is a numeric PMID with a non-empty provenance note."""
        assert pmid.isdigit(), f"registry key {pmid!r} is not a numeric PMID"
        note = GLOBALLY_UNRELATED_PMIDS[pmid]
        assert isinstance(note, str) and note.strip(), f"PMID {pmid} has an empty note"

    def test_registry_is_nontrivial(self) -> None:
        """Guard against the registry being accidentally emptied/gutted."""
        assert len(GLOBALLY_UNRELATED_PMIDS) >= 20

    def test_scanner_sees_real_citations(self) -> None:
        """Sanity: the scanner actually finds the (legitimate) panel citations."""
        all_pmids: set[str] = set()
        for pmids in _pmids_by_panel().values():
            all_pmids |= pmids
        # The curated panels collectively cite hundreds of (legitimate) PMIDs.
        assert len(all_pmids) > 100
