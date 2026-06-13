"""Positive PMID-resolution verifier for panel citations (issue #365).

The offline citation-provenance guard (``test_citation_provenance_guard.py``) is a
*denylist*: by construction it only catches PMIDs already known to be off-domain. It
cannot flag a brand-new fabricated, mistyped, or never-existed citation. This is the
**positive** complement #276/#277/#365 asked for, at its most robust, false-positive-
free form: confirm that every panel- and proxy-cited PMID actually *resolves* to a
real PubMed record.

These checks fetch NCBI live, so they are ``@pytest.mark.slow`` — they run only in
the nightly tier (``nightly.yml``, ``pytest -m slow``), never in per-PR CI
(network-flaky), and **skip** (do not fail) on a network error so a flaky NCBI
cannot redden the run. They reuse the guard's PMID-extraction path so the offline
and online checks share one source of cited PMIDs (#365's "share one extraction
path").

Two nightly checks live here:
  1. ``test_every_cited_pmid_resolves_on_pubmed`` — every cited PMID resolves to a
     real PubMed record (catches fabricated / mistyped citations).
  2. ``test_snapshot_titles_match_live_pubmed`` (#425, part 3 of #365) — the
     network-gated complement to the committed offline snapshot
     (``pmid_metadata_snapshot.json``) and the offline topic-consistency guard
     (``test_citation_topic_consistency.py``, parts 1-2, landed in #419): re-fetch
     each snapshotted PMID's live title and assert (a) it still matches the
     committed snapshot (drift / retraction detector → prompts a snapshot
     regeneration) and (b) the registered gene/condition topic terms still appear
     in the live title. This closes the loop the offline snapshot only approximates.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# Reuse the offline guard's extraction path (pytest prepends tests/backend to
# sys.path, so the sibling test module imports by bare name) — one source of
# cited PMIDs for both the offline denylist and this online check.
from test_citation_provenance_guard import all_panel_pmids, all_proxy_pmids

# NCBI esummary accepts large id lists; keep batches modest to stay within URL limits
# and be polite to the service.
_ESUMMARY_BATCH = 180
_FALLBACK_EMAIL = "ci@yeliztli.example"

_SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "pmid_metadata_snapshot.json"
)


def _configure_entrez():
    """Configure and return ``Bio.Entrez`` (caller must ``importorskip('Bio')`` first)."""
    from Bio import Entrez

    try:  # config is optional here; fall back to a generic contact email
        from backend.config import get_settings

        settings = get_settings()
        Entrez.email = settings.pubmed_email or _FALLBACK_EMAIL
        if getattr(settings, "pubmed_api_key", ""):
            Entrez.api_key = settings.pubmed_api_key
    except Exception:
        Entrez.email = _FALLBACK_EMAIL
    Entrez.tool = "yeliztli-citation-nightly-verifier"
    return Entrez


def _fetch_live_records(pmids: list[str]) -> dict[str, dict]:
    """Fetch live esummary records for *pmids* → ``{pmid: record}``.

    ``retmode=json`` returns a per-UID result dict (title + optional ``error``), so
    one bad UID does not abort the batch the way ``Entrez.read()`` would. On any
    network/NCBI error this ``pytest.skip``s rather than failing — a flaky NCBI must
    not redden the nightly run.
    """
    entrez = _configure_entrez()
    records: dict[str, dict] = {}
    for start in range(0, len(pmids), _ESUMMARY_BATCH):
        batch = pmids[start : start + _ESUMMARY_BATCH]
        try:
            handle = entrez.esummary(db="pubmed", id=",".join(batch), retmode="json")
            try:
                data = json.load(handle)
            finally:
                handle.close()
        except Exception as exc:  # network/NCBI error — don't redden the nightly run
            pytest.skip(f"PubMed esummary unreachable ({exc!r}); skipping live check")
        result = data.get("result", {})
        for pmid in batch:
            rec = result.get(pmid)
            if rec is not None:
                records[pmid] = rec
    return records


# PMIDs that already fail to resolve on PubMed today (verified via NCBI esearch
# ``[uid]`` -> count 0: fabricated / mistyped / deleted), tracked for replacement in
# issue #417. The resolution verifier fails on any *new* non-resolving PMID; this
# quarantine must only ever shrink as #417 lands per-panel fixes — never grow.
_KNOWN_UNRESOLVED: dict[str, str] = {
    # 11746697 (allergy HNMT row) fixed in #417 — replaced with verified HNMT
    # Thr105Ile evidence (9547362 Preuss 1998, 10803682 Yan 2000).
    # 19187342 (methylation TCN2 row) fixed in #314 — the whole TCN2 rs1801198
    # row was re-cited with verified one-carbon refs (28814397, 20808328, 12911562).
    "12874175": "gene_health_panel.json",  # KCNJ11 — delegated to #326
    "27457907": "gene_health_panel.json",  # ABCG2 — delegated to #326
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

    sources = _cited_pmid_sources()
    pmids = sorted(sources)
    assert pmids, "no cited PMIDs found — collector regression"

    records = _fetch_live_records(pmids)
    resolved = {
        pmid
        for pmid, rec in records.items()
        if not rec.get("error") and str(rec.get("title", "")).strip()
    }

    unresolved = [p for p in pmids if p not in resolved]
    new_unresolved = sorted(set(unresolved) - set(_KNOWN_UNRESOLVED))
    assert not new_unresolved, (
        "Newly non-resolving PMID(s) — do not resolve to a real PubMed record (likely "
        "fabricated or mistyped citations). Verify and fix, or quarantine with a "
        "tracking issue: "
        + "; ".join(f"{p} (cited in {sorted(sources[p])})" for p in new_unresolved)
    )


def _normalize_title(title: str) -> str:
    """Collapse to lowercase alphanumeric words so trivial punctuation / whitespace /
    casing differences don't read as drift — but real word-level changes (retraction
    prefixes, corrected words) do."""
    return " ".join(re.split(r"[^a-z0-9]+", title.lower())).strip()


@pytest.mark.slow
def test_snapshot_titles_match_live_pubmed() -> None:
    """Live drift + topic verifier (#425, part 3 of #365).

    The committed ``pmid_metadata_snapshot.json`` and the offline topic-consistency
    guard only *approximate* PubMed (they're a frozen point-in-time copy). This
    nightly check re-fetches each snapshotted PMID's live title and asserts:

      (a) **No drift** — the live title still matches the committed snapshot title
          (normalized for punctuation/case). A mismatch usually means a title
          correction or a *Retracted:/WITHDRAWN:* prefix → the snapshot should be
          regenerated (and the citation re-reviewed).
      (b) **Topic terms hold live** — for every registered ``_GENE_TOPIC_LOCKED`` /
          ``_CONDITION_TOPIC_LOCKED`` entry, the gene symbol / expected condition
          term still appears in at least one cited PMID's *live* title.

    Network-gated: a fetch error skips (does not fail). Entries whose cited PMIDs
    aren't all in the snapshot are skipped (the same fleet-safe rule the offline
    guard uses — re-snapshot to cover them).
    """
    pytest.importorskip("Bio", reason="biopython required for the live PubMed verifier")

    # Reuse the offline guard's registries + extraction helpers (sibling module).
    from test_citation_topic_consistency import (
        _CONDITION_TOPIC_LOCKED,
        _GENE_KEYS,
        _GENE_TOPIC_LOCKED,
        _entry_pmids,
        _load_snapshot,
        _panel_entries,
        _tokens,
    )

    snapshot = _load_snapshot()
    snap_pmids = sorted(snapshot)
    assert snap_pmids, "empty snapshot — regeneration regression"

    records = _fetch_live_records(snap_pmids)
    live_titles: dict[str, str] = {}
    for pmid in snap_pmids:
        rec = records.get(pmid, {})
        title = str(rec.get("title", "")).strip()
        if not rec.get("error") and title:
            live_titles[pmid] = title

    # (a) Drift: every snapshotted PMID whose live record we got back must still
    # carry the snapshot's title (normalized). A PMID that no longer resolves live
    # is reported too (likely a retraction/withdrawal removing the title).
    drift: list[str] = []
    for pmid in snap_pmids:
        snap_title = snapshot[pmid]["title"]
        if pmid not in live_titles:
            drift.append(
                f"{pmid}: no live title now (withdrawn/retracted?) — snapshot {snap_title!r}"
            )
            continue
        if _normalize_title(live_titles[pmid]) != _normalize_title(snap_title):
            drift.append(f"{pmid}: snapshot {snap_title!r} != live {live_titles[pmid]!r}")

    # (b) Topic terms still present in the LIVE titles of registered entries.
    entries = _panel_entries()

    def _live_title_tokens(pmids: list[str]) -> set[str]:
        toks: set[str] = set()
        for p in pmids:
            toks |= _tokens(live_titles[p])
        return toks

    topic_failures: list[str] = []
    for key in sorted(_GENE_TOPIC_LOCKED):
        for entry in entries.get(key, []):
            gene = next((entry[k] for k in _GENE_KEYS if entry.get(k)), None)
            pmids = _entry_pmids(entry)
            if not gene or not pmids or any(p not in live_titles for p in pmids):
                continue  # unresolved / not-yet-snapshotted → skip (fleet-safe)
            if not (_tokens(gene) & _live_title_tokens(pmids)):
                titles = "; ".join(live_titles[p] for p in pmids)
                topic_failures.append(
                    f"{key} ({gene}): gene token absent from live titles — [{titles}]"
                )
    for key, expected in _CONDITION_TOPIC_LOCKED.items():
        for entry in entries.get(key, []):
            pmids = _entry_pmids(entry)
            if not pmids or any(p not in live_titles for p in pmids):
                continue
            if not (expected & _live_title_tokens(pmids)):
                titles = "; ".join(live_titles[p] for p in pmids)
                topic_failures.append(
                    f"{key}: no expected term {sorted(expected)} in live titles — [{titles}]"
                )

    problems: list[str] = []
    if drift:
        problems.append(
            "Live PubMed titles drifted from the committed snapshot — regenerate "
            "tests/fixtures/pmid_metadata_snapshot.json (and re-review any retracted "
            "citation):\n" + "\n".join(drift)
        )
    if topic_failures:
        problems.append(
            "Registered topic terms no longer in live titles:\n" + "\n".join(topic_failures)
        )
    assert not problems, "\n\n".join(problems)
