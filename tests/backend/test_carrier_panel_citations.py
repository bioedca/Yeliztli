"""Per-PMID citation topic-consistency guard for the carrier panel (#1551).

``carrier_panel.json``'s HBB entry cited PMID ``20301357`` = *"WAS-Related
Disorders"* (the GeneReviews chapter for Wiskott-Aldrich Syndrome), completely
unrelated to the hemoglobinopathies — the same transposed-citation class fixed
one carrier gene at a time (#1386 HEXA, #173 GBA, #440 CFTR).

The existing topic-consistency guard (``test_citation_topic_consistency.py``)
does **not** reach carrier-panel gene entries: they are keyed by
``gene_symbol`` + ``expected_clinvar_rsids`` (a list), not the
``rsid``/``primary_rsid`` sibling its ``_panel_entries()`` discovery requires;
and its gene/condition locks use *union* logic (≥1 cited title on-topic), which
deliberately tolerates one mixed-in off-topic citation — exactly the shape of
this bug (three correct sickle-cell refs + one WAS ref).

This is the carrier-panel, **per-PMID** complement: for an opt-in registry of
audited carrier genes, *every* cited PMID that is present in the committed
offline snapshot must resolve to a title naming the gene or one of its
conditions. Un-snapshotted PMIDs are skipped (fleet-safe: a parallel PMID
change pending re-snapshot cannot redden ``main``), matching the established
guard. Extend ``_CARRIER_CITATION_TOPICS`` as more carrier genes are audited.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_PANEL_PATH = _REPO / "backend" / "data" / "panels" / "carrier_panel.json"
_SNAPSHOT_PATH = _REPO / "tests" / "fixtures" / "pmid_metadata_snapshot.json"

# The specific mis-citation this guard was created for (#1551): the GeneReviews
# chapter for Wiskott-Aldrich (WAS), which must never reappear on HBB.
_WAS_GENEREVIEWS_PMID = "20301357"

# Opt-in, incremental. Each *audited* carrier gene → acceptable title topic
# terms (lowercase, len ≥ 3). Every snapshotted PMID the gene cites must contain
# ≥ 1 of these as a title token. Verified against the snapshot at authoring time.
_CARRIER_CITATION_TOPICS: dict[str, frozenset[str]] = {
    # HBB: Sickle Cell Disease / Beta-Thalassemia. Cited GeneReviews + sickle-cell
    # -trait outcome papers all name "sickle"/"thalassemia" (#1551).
    "HBB": frozenset({"sickle", "thalassemia", "hemoglobin", "hbb", "hbs"}),
}


def _tokens(text: str) -> set[str]:
    """Lowercase alphanumeric title tokens of length ≥ 3 (matches the snapshot generator)."""
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 3}


def _snapshot() -> dict[str, dict[str, str]]:
    return json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))["pmids"]


def _carrier_genes() -> dict[str, dict]:
    data = json.loads(_PANEL_PATH.read_text(encoding="utf-8"))
    return {g["gene_symbol"]: g for g in data["genes"] if g.get("gene_symbol")}


def test_registered_genes_exist_in_panel() -> None:
    """Guards the registry's premise: every audited gene is still in the panel."""
    genes = _carrier_genes()
    missing = sorted(g for g in _CARRIER_CITATION_TOPICS if g not in genes)
    assert not missing, f"registry lists genes absent from carrier_panel.json: {missing}"


def test_carrier_citations_are_topically_consistent() -> None:
    """Every snapshotted PMID an audited carrier gene cites must be on-topic."""
    snapshot = _snapshot()
    genes = _carrier_genes()
    evaluated = 0
    failures: list[str] = []
    for gene, expected in _CARRIER_CITATION_TOPICS.items():
        entry = genes.get(gene)
        if not entry:
            continue
        pmids = [str(p) for p in entry.get("pmids", [])]
        if not pmids or any(p not in snapshot for p in pmids):
            continue  # not fully snapshotted yet → skip (re-snapshot to re-cover)
        evaluated += 1
        for pmid in pmids:
            title = snapshot[pmid]["title"]
            if not (expected & _tokens(title)):
                failures.append(
                    f"{gene}: cited PMID {pmid} is off-topic for {sorted(expected)} — {title!r}"
                )
    assert not failures, "carrier citation topic-consistency failures:\n" + "\n".join(failures)
    assert evaluated, "no carrier genes evaluated — registry/snapshot out of sync"


def test_hbb_does_not_cite_was_genereviews() -> None:
    """Named regression (#1551): HBB must never cite the Wiskott-Aldrich
    GeneReviews chapter (``20301357`` = "WAS-Related Disorders")."""
    hbb = _carrier_genes().get("HBB")
    assert hbb is not None, "HBB missing from carrier_panel.json"
    cited = [str(p) for p in hbb.get("pmids", [])]
    assert _WAS_GENEREVIEWS_PMID not in cited, (
        f"HBB cites {_WAS_GENEREVIEWS_PMID} (WAS-Related Disorders), unrelated to the "
        f"hemoglobinopathies — see #1551."
    )
