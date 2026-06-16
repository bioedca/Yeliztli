"""Suite-wide guard: the ``{risk, ref}`` dosage pair must be two distinct alleles.

A curated panel locus encodes its effect as a dosage of the *risk* allele within
the ``{risk_allele, ref_allele}`` pair, so the pair is meaningless unless the two
alleles differ. ``risk_allele == ref_allele`` is a degenerate, recurring
direction error — #332 shipped ``ref == risk`` on rs10741657 (→ #336/#337). Unlike
a *mislabeled* risk allele (the wrong-but-distinct allele of #538/#545/#581, which
needs external verification against Ensembl/GWAS to detect), this degenerate case
needs no external reference: it is a pure data-integrity check.

This is a SELF-DISCOVERING guard (mirrors the indel-polarity guard, #508/PR #554,
``test_indel_polarity_provenance.py``): it walks every
``backend/data/panels/*.json`` locus, so a new or edited locus that sets
``risk == ref`` fails immediately — there is no hand-maintained allow-list to
forget to update, which is the precise gap that let #332 ship ``ref == risk``.
Before this, the invariant was enforced only per-locus (a single SNP in
``test_nutrigenomics.py`` and a few exact pairs in ``test_allergy.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

import backend.analysis.gene_health as gene_health_mod

_PANELS = Path(gene_health_mod.__file__).resolve().parent.parent / "data" / "panels"


def _walk_dicts(node: object):
    """Yield every dict nested anywhere inside a parsed-JSON structure."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_dicts(item)


def _discover_risk_ref_loci() -> dict[str, tuple[object, object]]:
    """``{f'{panel}::{rsid}': (risk_allele, ref_allele)}`` for every locus that
    carries BOTH a ``risk_allele`` and a ``ref_allele`` key, across all panels."""
    found: dict[str, tuple[object, object]] = {}
    for path in sorted(_PANELS.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for node in _walk_dicts(raw):
            if "risk_allele" in node and "ref_allele" in node:
                found[f"{path.name}::{node.get('rsid')}"] = (
                    node["risk_allele"],
                    node["ref_allele"],
                )
    return found


def test_discovery_finds_the_panel_loci() -> None:
    """Sanity: the walker must find the curated loci, so the invariant test below
    cannot pass vacuously if panel discovery ever breaks (e.g. a schema change)."""
    loci = _discover_risk_ref_loci()
    assert len(loci) >= 100, f"risk/ref locus discovery regressed; found only {len(loci)}"


def test_every_panel_locus_has_distinct_risk_and_ref() -> None:
    """SELF-DISCOVERING durable guard (#332): every panel locus's ``{risk, ref}``
    pair must be two different alleles.

    Loci where either allele is ``None`` are skipped — e.g. the HLA-DQB1 proxy
    ``sleep_panel.json::rs2858884`` (``risk_allele=None``), which is scored outside
    the ``{risk, ref}`` dosage frame.
    """
    offenders = []
    for label, (risk, ref) in sorted(_discover_risk_ref_loci().items()):
        if risk is None or ref is None:
            continue
        if str(risk) == str(ref):
            offenders.append(f"{label} risk==ref=={risk!r}")
    assert not offenders, (
        "risk_allele == ref_allele (degenerate {risk, ref} dosage pair): " + "; ".join(offenders)
    )
