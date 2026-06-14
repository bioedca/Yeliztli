"""Offline citation **topic-consistency** guard (#365).

The positive complement to #358's denylist (``test_citation_provenance_guard.py``).
The denylist is *reactive* — it can only block reintroduction of PMIDs already
known to be off-domain. This guard is *proactive but bounded*: for an explicitly
**registered** set of panel entries, it asserts the entry's cited PMIDs resolve
(via the committed offline metadata snapshot) to titles that actually mention the
entry's gene or condition — catching a transposed/unrelated citation *before* it
is individually reported.

Two committed inputs:

* ``tests/fixtures/pmid_metadata_snapshot.json`` — PMID → ``{title, journal, year}`` for
  every panel-cited PMID, generated offline from NCBI E-utilities by
  ``scripts/build_pmid_metadata_snapshot.py`` (which reuses the guard's
  ``all_panel_pmids``/``all_proxy_pmids`` extraction, so both halves cover the
  same PMIDs). Never fetched at test time; regenerated deliberately.
* The ``_GENE_TOPIC_LOCKED`` / ``_CONDITION_TOPIC_LOCKED`` registries below — the
  **opt-in, incremental** coverage surface #277/#365 asked for.

**Fleet-robustness (why this can't redden main on someone else's PMID change):**
a registered entry is *evaluated* only when it still resolves to a panel entry
**and** all its currently-cited PMIDs are in the snapshot. A parallel PMID change
swaps in PMIDs not yet snapshotted → that entry is **skipped** (re-snapshot to
re-cover it), never failed. A removed/renamed entry is skipped too. So the guard
only fails on a genuine, already-snapshotted transposition — exactly its purpose.

**Extending coverage:** after auditing a panel, add its ``panel::rsid`` keys to
``_GENE_TOPIC_LOCKED`` (gene symbol appears in ≥1 cited title) or, when the
literature names the *condition* rather than the gene, add a
``_CONDITION_TOPIC_LOCKED`` entry with the expected condition terms. Regenerate
the snapshot in the same change so the new entries are covered.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent.parent / "backend"
_PANELS_DIR = _BACKEND / "data" / "panels"
_SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "pmid_metadata_snapshot.json"
)

# Mirror the guard's PMID-bearing keys (test_citation_provenance_guard._PMID_KEYS).
_PMID_KEYS = ("pmids", "pmid_citations", "pmid", "source_pmid")
# Sibling keys that name the panel entry whose citations we are checking.
_RSID_KEYS = ("rsid", "primary_rsid")
_GENE_KEYS = ("gene", "gene_symbol")


# ── Gene-topic-locked entries ────────────────────────────────────────────────
# Each ``panel::rsid`` entry must keep citing ≥1 PMID whose snapshot title names
# the entry's gene symbol (≥1 gene token, len ≥ 3, appears as a title token).
# Generated from the current snapshot (every entry below verifiably passes today)
# and committed as the initial registration; extend per-panel as audits land.
_GENE_TOPIC_LOCKED: frozenset[str] = frozenset(
    {
        "allergy_panel.json::rs1061235",  # HLA-A
        "allergy_panel.json::rs144012689",  # HLA-B
        "allergy_panel.json::rs20541",  # IL13
        "allergy_panel.json::rs2395029",  # HLA-B
        "allergy_panel.json::rs324011",  # STAT6
        "allergy_panel.json::rs8076131",  # ORMDL3
        "allergy_panel.json::rs9263726",  # HLA-B
        "fitness_panel.json::rs17602729",  # AMPD1
        "fitness_panel.json::rs1800012",  # COL1A1
        "fitness_panel.json::rs1815739",  # ACTN3
        "fitness_panel.json::rs4341",  # ACE
        "fitness_panel.json::rs9939609",  # FTO
        "gene_health_panel.json::rs10490924",  # ARMS2
        "gene_health_panel.json::rs1061170",  # CFH
        "gene_health_panel.json::rs10955255",  # GRHL2
        "gene_health_panel.json::rs111033313",  # SLC26A4
        "gene_health_panel.json::rs11136000",  # CLU
        "gene_health_panel.json::rs1143679",  # ITGAM
        "gene_health_panel.json::rs12498742",  # SLC2A9
        "gene_health_panel.json::rs13266634",  # SLC30A8
        "gene_health_panel.json::rs17782313",  # MC4R
        "gene_health_panel.json::rs1801133",  # MTHFR
        "gene_health_panel.json::rs1801282",  # PPARG
        "gene_health_panel.json::rs2004640",  # IRF5
        "gene_health_panel.json::rs2066844",  # NOD2
        "gene_health_panel.json::rs2157719",  # CDKN2B-AS1
        "gene_health_panel.json::rs2476601",  # PTPN22
        "gene_health_panel.json::rs34637584",  # LRRK2
        "gene_health_panel.json::rs3746544",  # SNAP25
        "gene_health_panel.json::rs58542926",  # TM6SF2
        "gene_health_panel.json::rs6822844",  # IL2/IL21
        "gene_health_panel.json::rs738409",  # PNPLA3
        "gene_health_panel.json::rs747302",  # DRD4
        "gene_health_panel.json::rs7574865",  # STAT4
        "gene_health_panel.json::rs7903146",  # TCF7L2
        "gene_health_panel.json::rs9939609",  # FTO
        "methylation_panel.json::rs1050450",  # GPX1
        "methylation_panel.json::rs1801394",  # MTRR
        "nutrigenomics_panel.json::rs10741657",  # CYP2R1
        "nutrigenomics_panel.json::rs174547",  # FADS1
        "nutrigenomics_panel.json::rs1799945",  # HFE
        "nutrigenomics_panel.json::rs1800562",  # HFE
        "nutrigenomics_panel.json::rs182549",  # MCM6/LCT
        "nutrigenomics_panel.json::rs4988235",  # MCM6/LCT
        "nutrigenomics_panel.json::rs602662",  # FUT2
        "skin_panel.json::rs1805007",  # MC1R
        "skin_panel.json::rs1805008",  # MC1R
        "skin_panel.json::rs1805009",  # MC1R
        "skin_panel.json::rs885479",  # MC1R
        "sleep_panel.json::rs2858884",  # HLA-DQB1
        "sleep_panel.json::rs5751876",  # ADORA2A
        "sleep_panel.json::rs762551",  # CYP1A2
        "sleep_panel.json::rs9357271",  # BTBD9
        "traits_panel.json::rs747302",  # DRD4
    }
)

# Entries whose literature names the *condition*, not the gene symbol (so the
# gene-token rule would false-positive). Each maps ``panel::rsid`` → expected
# condition terms; ≥1 must appear as a title token in ≥1 cited PMID. Curated and
# verified against the snapshot. Demonstrates the synonym path for incremental
# per-panel registration (extend as conditions are audited).
_CONDITION_TOPIC_LOCKED: dict[str, frozenset[str]] = {
    "sleep_panel.json::rs2300478": frozenset(  # MEIS1 — restless legs / PLMS GWAS
        {"restless", "limb", "periodic"}
    ),
    # SOD2 rs4880 (Val16Ala): the functional literature names the protein
    # ("manganese superoxide dismutase" / "MnSOD") and the variant ("Ala16Val"),
    # not the "SOD2" gene token — so it's condition-locked (#390).
    "skin_panel.json::rs4880": frozenset({"superoxide", "dismutase", "mnsod", "ala16val"}),
    # VDR FokI rs2228570 / BsmI rs1544410: the literature names the receptor
    # ("vitamin D receptor") + the variant/condition (foki/bsmi/psoriasis), not the
    # "VDR" gene token — condition-locked (#437). Cited refs are the psoriasis
    # meta-analyses (+ a functional FokI paper).
    "skin_panel.json::rs2228570": frozenset({"vitamin", "receptor", "psoriasis", "foki"}),
    "skin_panel.json::rs1544410": frozenset({"vitamin", "receptor", "psoriasis", "bsmi"}),
}


def _tokens(text: str) -> set[str]:
    """Lowercase alphanumeric tokens of length ≥ 3 (matches the snapshot generator)."""
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 3}


def _load_snapshot() -> dict[str, dict[str, str]]:
    data = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    return data["pmids"]


def _entry_pmids(entry: dict) -> list[str]:
    out: list[str] = []
    for key in _PMID_KEYS:
        val = entry.get(key)
        if isinstance(val, list):
            out.extend(str(x) for x in val)
        elif isinstance(val, (str, int)):
            out.append(str(val))
    return [p for p in out if p.isdigit()]


def _panel_entries() -> dict[str, list[dict]]:
    """Map ``panel::rsid`` → list of entry dicts (a dict holding a PMID list and an rsid)."""
    result: dict[str, list[dict]] = {}

    def walk(obj, panel: str) -> None:
        if isinstance(obj, dict):
            if set(_PMID_KEYS) & obj.keys():
                rsid = next((obj[k] for k in _RSID_KEYS if obj.get(k)), None)
                if isinstance(rsid, str):
                    result.setdefault(f"{panel}::{rsid}", []).append(obj)
            for val in obj.values():
                walk(val, panel)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, panel)

    for path in sorted(_PANELS_DIR.glob("*.json")):
        if path.name == "hla_proxy_lookup.json":
            continue
        try:
            walk(json.loads(path.read_text(encoding="utf-8")), path.name)
        except json.JSONDecodeError:
            continue
    return result


def _title_tokens(pmids: list[str], snapshot: dict[str, dict[str, str]]) -> set[str]:
    toks: set[str] = set()
    for pmid in pmids:
        toks |= _tokens(snapshot[pmid]["title"])
    return toks


# ── Tests ────────────────────────────────────────────────────────────────────


def test_snapshot_well_formed() -> None:
    """The committed snapshot parses, carries provenance, and is non-trivial."""
    data = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    prov = data.get("_provenance", {})
    assert prov.get("source"), "snapshot missing provenance.source"
    assert prov.get("accessed"), "snapshot missing provenance.accessed (regen date)"
    pmids = data["pmids"]
    assert len(pmids) >= 100, f"snapshot suspiciously small ({len(pmids)}) — was it truncated?"
    for pmid, meta in pmids.items():
        assert pmid.isdigit(), f"non-numeric snapshot key {pmid!r}"
        assert set(meta) >= {"title", "journal", "year"}, f"{pmid}: missing metadata fields"
        assert meta["title"].strip(), f"{pmid}: empty title"


def test_locked_registries_are_well_formed() -> None:
    """Registry keys are ``panel::rsid``; the two registries don't overlap."""
    for key in _GENE_TOPIC_LOCKED | set(_CONDITION_TOPIC_LOCKED):
        panel, _, rsid = key.partition("::")
        assert panel.endswith(".json") and rsid.startswith("rs"), f"malformed key {key!r}"
    overlap = _GENE_TOPIC_LOCKED & set(_CONDITION_TOPIC_LOCKED)
    assert not overlap, f"keys in both gene- and condition-locked registries: {sorted(overlap)}"


def test_gene_topic_locked_entries_cite_gene_in_title() -> None:
    """Each gene-locked entry must keep ≥1 cited title naming its gene symbol.

    Skips (does not fail) an entry whose PMIDs aren't all in the snapshot yet —
    that is the fleet-safe path for a parallel PMID change pending re-snapshot.
    """
    snapshot = _load_snapshot()
    entries = _panel_entries()
    evaluated = 0
    failures: list[str] = []
    for key in sorted(_GENE_TOPIC_LOCKED):
        for entry in entries.get(key, []):
            gene = next((entry[k] for k in _GENE_KEYS if entry.get(k)), None)
            pmids = _entry_pmids(entry)
            if not gene or not pmids or any(p not in snapshot for p in pmids):
                continue  # unresolved / not yet snapshotted → skip (re-snapshot to cover)
            evaluated += 1
            if not (_tokens(gene) & _title_tokens(pmids, snapshot)):
                titles = "; ".join(snapshot[p]["title"] for p in pmids)
                failures.append(f"{key} ({gene}): no cited title names the gene — [{titles}]")
    assert not failures, "gene-topic-consistency failures:\n" + "\n".join(failures)
    assert evaluated, "no gene-locked entries were evaluated — registry/snapshot out of sync"


def test_condition_topic_locked_entries_cite_condition_in_title() -> None:
    """Each condition-locked entry must keep ≥1 cited title naming an expected term."""
    snapshot = _load_snapshot()
    entries = _panel_entries()
    evaluated = 0
    failures: list[str] = []
    for key, expected in _CONDITION_TOPIC_LOCKED.items():
        for entry in entries.get(key, []):
            pmids = _entry_pmids(entry)
            if not pmids or any(p not in snapshot for p in pmids):
                continue
            evaluated += 1
            if not (expected & _title_tokens(pmids, snapshot)):
                titles = "; ".join(snapshot[p]["title"] for p in pmids)
                failures.append(
                    f"{key}: no cited title contains an expected term "
                    f"{sorted(expected)} — [{titles}]"
                )
    assert not failures, "condition-topic-consistency failures:\n" + "\n".join(failures)
    assert evaluated, "no condition-locked entries evaluated — registry/snapshot out of sync"
