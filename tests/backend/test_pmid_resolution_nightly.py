"""Positive PMID-resolution verifier for panel citations (issue #365).

The offline citation-provenance guard (``test_citation_provenance_guard.py``) is a
*denylist*: by construction it only catches PMIDs already known to be off-domain. It
cannot flag a brand-new fabricated, mistyped, or never-existed citation. This is the
**positive** complement #276/#277/#365 asked for, at its most robust, false-positive-
free form: confirm that every panel- and proxy-cited PMID actually *resolves* to a
real PubMed record.

The resolution check fetches NCBI live, so it is ``@pytest.mark.slow`` — it runs only
in the nightly tier (``nightly.yml``, ``pytest -m slow``), never in per-PR CI
(network-flaky), and **skips** (does not fail) on a network error so a flaky NCBI
cannot redden the run. It reuses the guard's PMID-extraction path so the offline and
online checks share one source of cited PMIDs (#365's "share one extraction path").

Deliberately out of scope here (kept open on #365, to land incrementally per panel
once the active per-row PMID cleanup settles, avoiding fleet CI thrash):
  * the checked-in PMID -> {title, journal, year} metadata snapshot, and
  * the offline title-shares-a-gene/condition-term topic-consistency heuristic.
This resolution check is the safe, immediate net under those.
"""

from __future__ import annotations

import json

import pytest

# Reuse the offline guard's extraction path (pytest prepends tests/backend to
# sys.path, so the sibling test module imports by bare name) — one source of
# cited PMIDs for both the offline denylist and this online check.
from test_citation_provenance_guard import all_panel_pmids, all_proxy_pmids

# NCBI esummary accepts large id lists; keep batches modest to stay within URL limits
# and be polite to the service.
_ESUMMARY_BATCH = 180
_FALLBACK_EMAIL = "ci@yeliztli.example"

# PMIDs that already fail to resolve on PubMed today (verified via NCBI esearch
# ``[uid]`` -> count 0: fabricated / mistyped / deleted), tracked for replacement in
# issue #417. The resolution verifier fails on any *new* non-resolving PMID; this
# quarantine must only ever shrink as #417 lands per-panel fixes — never grow.
_KNOWN_UNRESOLVED: dict[str, str] = {
    "11746697": "allergy_panel.json",
    "12874175": "gene_health_panel.json",
    "27457907": "gene_health_panel.json",
    "19187342": "methylation_panel.json",
}


def _cited_pmid_sources() -> dict[str, set[str]]:
    """Map each cited PMID -> the source(s) (panel filename / 'hla_proxy') citing it."""
    sources: dict[str, set[str]] = {}
    for panel_name, pmids in all_panel_pmids().items():
        for pmid in pmids:
            sources.setdefault(pmid, set()).add(panel_name)
    for pmid in all_proxy_pmids():
        sources.setdefault(pmid, set()).add("hla_proxy_lookup.json")
    return sources


def test_quarantined_pmids_are_still_cited() -> None:
    """Offline forcing-function: prune the quarantine as #417 lands fixes.

    Each ``_KNOWN_UNRESOLVED`` PMID must still be cited by some panel; once a fix
    removes/replaces it, this fails so the quarantine entry is dropped (it can't
    silently mask a future, unrelated reuse of the same number).
    """
    cited = set(_cited_pmid_sources())
    stale = sorted(set(_KNOWN_UNRESOLVED) - cited)
    assert not stale, (
        f"PMID(s) {stale} are quarantined as non-resolving but no longer cited by any "
        f"panel (fixed via #417?) — remove them from _KNOWN_UNRESOLVED"
    )


@pytest.mark.slow
def test_every_cited_pmid_resolves_on_pubmed() -> None:
    """Every panel/proxy-cited PMID resolves to a real PubMed record (#365).

    A PMID that does not resolve to a record with a title is almost always a
    fabricated or mistyped citation — exactly the failure the offline denylist
    cannot catch. Known pre-existing offenders are quarantined (#417); this fails
    only on a *new* one. Network-gated: a fetch error skips (does not fail).
    """
    pytest.importorskip("Bio", reason="biopython required for the live PubMed verifier")
    from Bio import Entrez

    try:  # config is optional here; fall back to a generic contact email
        from backend.config import get_settings

        settings = get_settings()
        Entrez.email = settings.pubmed_email or _FALLBACK_EMAIL
        if getattr(settings, "pubmed_api_key", ""):
            Entrez.api_key = settings.pubmed_api_key
    except Exception:
        Entrez.email = _FALLBACK_EMAIL
    Entrez.tool = "yeliztli-citation-resolution-verifier"

    sources = _cited_pmid_sources()
    pmids = sorted(sources)
    assert pmids, "no cited PMIDs found — collector regression"

    resolved: set[str] = set()
    for start in range(0, len(pmids), _ESUMMARY_BATCH):
        batch = pmids[start : start + _ESUMMARY_BATCH]
        try:
            # retmode=json returns a per-UID result dict (title + optional 'error'),
            # so one bad UID does not abort the batch the way Entrez.read() would.
            handle = Entrez.esummary(db="pubmed", id=",".join(batch), retmode="json")
            try:
                data = json.load(handle)
            finally:
                handle.close()
        except Exception as exc:  # network/NCBI error — don't redden the nightly run
            pytest.skip(f"PubMed esummary unreachable ({exc!r}); skipping resolution check")

        result = data.get("result", {})
        for pmid in batch:
            rec = result.get(pmid, {})
            if not rec.get("error") and str(rec.get("title", "")).strip():
                resolved.add(pmid)

    unresolved = [p for p in pmids if p not in resolved]
    new_unresolved = sorted(set(unresolved) - set(_KNOWN_UNRESOLVED))
    assert not new_unresolved, (
        "Newly non-resolving PMID(s) — do not resolve to a real PubMed record (likely "
        "fabricated or mistyped citations). Verify and fix, or quarantine with a "
        "tracking issue: "
        + "; ".join(f"{p} (cited in {sorted(sources[p])})" for p in new_unresolved)
    )
