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

# Keys under which curated JSON stores citations (list[str] or a bare str):
#   pmids        — the common per-row list (most panels)
#   pmid         — single citation (e.g. hla_proxy_lookup.json)
#   source_pmid  — PRS/score provenance (pgs_score_registry, cancer_prs_weights, traits)
#   pmid_citations — the runtime findings output shape; not present in curated input
#                    today, covered for forward-compat.
# test_pmid_bearing_keys_are_all_covered() fails if a panel ever introduces a new
# PMID-bearing key not listed here, so the scan can't silently miss a citation.
_PMID_KEYS = ("pmids", "pmid_citations", "pmid", "source_pmid")


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


def _all_keys(obj: object, into: set[str]) -> None:
    """Recursively collect every dict key present in a loaded JSON document."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            into.add(key)
            _all_keys(value, into)
    elif isinstance(obj, list):
        for item in obj:
            _all_keys(item, into)


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
        assert len(GLOBALLY_UNRELATED_PMIDS) >= 21

    def test_scanner_sees_real_citations(self) -> None:
        """Sanity: the scanner actually finds the (legitimate) panel citations."""
        all_pmids: set[str] = set()
        for pmids in _pmids_by_panel().values():
            all_pmids |= pmids
        # The curated panels collectively cite hundreds of (legitimate) PMIDs.
        assert len(all_pmids) > 100

    def test_pmid_bearing_keys_are_all_covered(self) -> None:
        """Every PMID-bearing key used by any panel must be scanned.

        Without this, a panel could introduce a new citation key (e.g. a future
        ``*_pmid`` field) that the scanner silently ignores — exactly the
        ``source_pmid`` blind spot this guard was hardened against. Any panel key
        whose name references a PMID must be listed in ``_PMID_KEYS``.
        """
        keys: set[str] = set()
        for path in _panel_files():
            _all_keys(json.loads(path.read_text(encoding="utf-8")), keys)
        pmid_keys = {k for k in keys if "pmid" in k.lower()}
        uncovered = pmid_keys - set(_PMID_KEYS)
        assert not uncovered, (
            f"panel JSON uses PMID-bearing key(s) not scanned by the guard: "
            f"{sorted(uncovered)} — add them to _PMID_KEYS"
        )

    def test_scanner_collects_all_pmid_key_shapes(self) -> None:
        """``_collect_pmids`` reads every supported key, as both str and list."""
        doc = {
            "a": {"pmids": ["111", "222"]},
            "b": [{"pmid": "333"}, {"source_pmid": "444"}],
            "c": {"pmid_citations": ["555"]},
            "ignored": {"note": "999", "id": "888"},  # non-citation keys skipped
        }
        found: set[str] = set()
        _collect_pmids(doc, found)
        assert found == {"111", "222", "333", "444", "555"}
