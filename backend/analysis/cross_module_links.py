"""Serve cross-module cards from the panel that is loaded, not the one that was.

A cross-module card's target and note are panel data, but they are baked into
``findings.finding_text`` / ``detail_json`` when the module is scored. Sample
staleness is tied to reference-bundle versions, so a panel edit never prompts a
re-analysis: without this, correcting a link fixes it only for samples analysed
afterwards, and everyone else keeps following the old one (#2021).

Resolving against the live panel at read time needs no migration and no
invalidation — ``rsid`` is stable metadata every cross-module finding already
carries, and it is what the panel keys its links on.
"""

from __future__ import annotations

from typing import Any


def panel_cross_module_links(panel: Any) -> dict[str, dict]:
    """Map rsid -> the ``cross_module`` block the panel currently declares."""
    return {
        snp.rsid: snp.cross_module
        for pathway in panel.pathways
        for snp in pathway.snps
        if getattr(snp, "cross_module", None)
    }


def current_link(links: dict[str, dict], finding: dict[str, Any]) -> dict | None:
    """The live ``cross_module`` block for a stored finding, or None to drop it.

    ``None`` means the panel no longer declares this link at all — the celiac
    DQ2/DQ8 proxies, whose Nutrigenomics handoff was removed — so the card must
    not be rendered from the stored row.
    """
    return links.get(finding.get("rsid") or "")


def refreshed_finding_text(finding: dict[str, Any], link: dict) -> str:
    """Swap the stored trailing note for the one the panel gives now.

    Only the note is replaced, and only when the stored text actually ends with
    the note the finding recorded, so the sample-specific prefix (variant label,
    genotype, and any module-specific framing) is never rewritten. Anything
    unrecognised is returned untouched.
    """
    text = finding.get("finding_text") or ""
    stored_note = (finding.get("detail") or {}).get("cross_module_note")
    current_note = link.get("note")
    if not stored_note or not current_note or stored_note == current_note:
        return text
    if not text.endswith(str(stored_note)):
        return text
    return text[: -len(str(stored_note))] + str(current_note)
