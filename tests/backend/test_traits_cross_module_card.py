"""The Traits cross-module card says what the destination holds (#2347).

Until #2347 the DRD4 -> Gene Health card ended with "See Gene Health module for
related findings." and kept the panel note only in ``detail``, and Traits was the
one module whose stored cards were never resolved against the live panel. The
card now ends with the note, Traits resolves like the other modules, and a card
stored with the generic pointer gets the note at read time without a re-score.
"""

from __future__ import annotations

from backend.analysis.cross_module_links import (
    current_recommendation,
    generic_cross_module_pointer,
    normalize_cross_module_row,
    panel_cross_module_links,
    refreshed_legacy_pointer,
    resolve_stored_card,
)
from backend.analysis.traits import (
    MODERATE,
    PathwayResult,
    SNPResult,
    _generate_cross_module_findings,
    load_traits_panel,
)

_RSID = "rs747302"
_PREFIX = "DRD4 exon III VNTR proxy (CG) — One copy of proxy allele. "


def _panel_snp():
    return next(
        snp
        for pathway in load_traits_panel().pathways
        for snp in pathway.snps
        if snp.rsid == _RSID
    )


def _legacy_row() -> dict:
    """A DRD4 card as it was stored before #2347."""
    return {
        "rsid": _RSID,
        "finding_text": _PREFIX + generic_cross_module_pointer("gene_health"),
        "detail": {
            "genotype": "CG",
            "category": MODERATE,
            "to_module": "gene_health",
            "link_type": "ADHD",
            "note": "an older note",
        },
    }


def _card(cross_module: dict) -> SNPResult:
    return SNPResult(
        rsid=_RSID,
        gene="DRD4",
        variant_name="DRD4 exon III VNTR proxy",
        genotype="CG",
        category=MODERATE,
        effect_summary="One copy of proxy allele.",
        evidence_level=1,
        pmids=["12210280"],
        recommendation_text="",
        present_in_sample=True,
        trait_domain="novelty_seeking",
        cross_module=cross_module,
    )


def _build(cross_module: dict):
    result = PathwayResult(
        pathway_id="behavioral_traits",
        pathway_name="Behavioral Traits",
        level=MODERATE,
        snp_results=[_card(cross_module)],
    )
    (finding,) = _generate_cross_module_findings([result], load_traits_panel())
    return finding


def test_a_new_card_ends_with_the_panel_note() -> None:
    note = _panel_snp().cross_module["note"]
    finding = _build(_panel_snp().cross_module)
    assert finding.finding_text == _PREFIX + note
    assert finding.detail["cross_module_note"] == note
    assert "ADHD" in finding.finding_text


def test_a_link_without_a_note_keeps_the_generic_pointer() -> None:
    finding = _build({"module": "gene_health"})
    assert finding.finding_text == _PREFIX + generic_cross_module_pointer("gene_health")


def test_the_traits_route_and_the_aggregator_share_one_resolution() -> None:
    links = panel_cross_module_links(load_traits_panel())
    note = links[_RSID]["note"]

    resolved = resolve_stored_card("traits", _legacy_row(), links)
    assert resolved is not None
    link, text = resolved
    assert link["module"] == "gene_health"
    assert text == _PREFIX + note

    row = _legacy_row()
    text, detail = normalize_cross_module_row(
        "traits", "cross_module", _RSID, row["finding_text"], row["detail"]
    )
    assert text == _PREFIX + note
    assert detail["target_module"] == "gene_health"
    assert detail["cross_module_note"] == note


def test_a_stored_note_is_replaced_by_the_one_the_panel_gives_now() -> None:
    links = panel_cross_module_links(load_traits_panel())
    stored = {
        "rsid": _RSID,
        "finding_text": _PREFIX + "An earlier note.",
        "detail": {
            "genotype": "CG",
            "to_module": "gene_health",
            "cross_module_note": "An earlier note.",
        },
    }
    _, text = resolve_stored_card("traits", stored, links)
    assert text == _PREFIX + links[_RSID]["note"]


def test_the_legacy_pointer_is_replaced_only_on_an_exact_match() -> None:
    link = {"module": "gene_health", "note": "New note."}
    pointer = generic_cross_module_pointer("gene_health")
    row = {"detail": {"to_module": "gene_health"}}

    assert refreshed_legacy_pointer(row, link, "Prefix. " + pointer) == "Prefix. New note."
    # Text that does not end with the pointer it would have recorded is left alone.
    assert (
        refreshed_legacy_pointer(row, link, "Prefix. Something else.") == "Prefix. Something else."
    )
    other = {"detail": {"to_module": "sleep"}}
    assert refreshed_legacy_pointer(other, link, "Prefix. " + pointer) == "Prefix. " + pointer
    # A card that already records its note is refreshed by the note swap instead.
    noted = {"detail": {"to_module": "gene_health", "cross_module_note": "Old."}}
    assert refreshed_legacy_pointer(noted, link, "Prefix. " + pointer) == "Prefix. " + pointer
    # Without a note there is nothing better to say than the pointer.
    assert refreshed_legacy_pointer(row, {"module": "gene_health"}, pointer) == pointer


def test_traits_recommendations_are_not_refreshed_at_read_time() -> None:
    """Its page serves the stored recommendation, so the aggregator must too."""
    panel_text = _panel_snp().recommendation_text
    assert panel_text
    assert current_recommendation("traits", _RSID, "Stored advice.") == "Stored advice."
