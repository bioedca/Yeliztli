"""Stored cross-module cards must follow the panel that is loaded now (#2021).

Target and note are panel data baked into ``findings`` at scoring time, and
sample staleness is tied to reference-bundle versions rather than panel edits.
Without read-time resolution, correcting a link would fix it only for samples
analysed afterwards — everyone else would keep following the old one with no
prompt to re-analyse.
"""

from __future__ import annotations

from backend.analysis.allergy import load_allergy_panel
from backend.analysis.cross_module_links import (
    current_link,
    current_recommendation,
    panel_cross_module_links,
    refreshed_finding_text,
)
from backend.analysis.gene_health import load_gene_health_panel

LEGACY_FTO_NOTE = (
    "FTO rs9939609 influences appetite regulation and macronutrient metabolism. "
    "See Nutrigenomics for dietary recommendations."
)
LEGACY_CELIAC_NOTE = (
    "Celiac-related HLA-DQ2 finding may affect dietary considerations. "
    "See Nutrigenomics for gluten-related nutrient interactions."
)


def _legacy_finding(rsid: str, note: str, prefix: str) -> dict:
    """A cross-module finding as it was stored before the panels were corrected."""
    return {
        "rsid": rsid,
        "finding_text": f"{prefix} — {note}",
        "detail": {"target_module": "nutrigenomics", "cross_module_note": note},
    }


class TestLegacyFtoCard:
    """A sample scored before the retarget follows it without a re-score."""

    def test_target_is_resolved_to_metabolic(self) -> None:
        links = panel_cross_module_links(load_gene_health_panel())
        finding = _legacy_finding("rs9939609", LEGACY_FTO_NOTE, "FTO FTO intron 1 (AT)")

        link = current_link(links, finding)
        assert link is not None
        assert link["module"] == "metabolic"

    def test_note_is_refreshed_and_the_prefix_preserved(self) -> None:
        links = panel_cross_module_links(load_gene_health_panel())
        finding = _legacy_finding("rs9939609", LEGACY_FTO_NOTE, "FTO FTO intron 1 (AT)")

        text = refreshed_finding_text(finding, current_link(links, finding))
        assert "See Nutrigenomics for dietary recommendations" not in text
        assert "Metabolic module" in text
        # The sample-specific prefix is never rewritten — including its own
        # historical doubling, which only a re-score can clean up.
        assert text.startswith("FTO FTO intron 1 (AT) — ")


class TestLegacyCeliacCard:
    """A link the panel dropped must stop being served."""

    def test_dropped_link_resolves_to_none(self) -> None:
        links = panel_cross_module_links(load_allergy_panel())
        finding = _legacy_finding("rs2187668", LEGACY_CELIAC_NOTE, "HLA-DQ2.5 proxy (CT)")
        assert current_link(links, finding) is None

    def test_a_surviving_allergy_link_still_resolves(self) -> None:
        """Discriminates 'dropped' from 'nothing resolves at all'."""
        links = panel_cross_module_links(load_allergy_panel())
        assert current_link(links, {"rsid": "rs20541"})["module"] == "skin"


class TestRefreshedFindingTextIsConservative:
    """Only the trailing note is replaced, and only when it is recognised."""

    LINK = {"module": "metabolic", "note": "New note."}

    def test_unrecognised_text_is_returned_untouched(self) -> None:
        finding = {
            "rsid": "rs9939609",
            "finding_text": "Hand-edited text that ends differently.",
            "detail": {"cross_module_note": "Old note."},
        }
        assert (
            refreshed_finding_text(finding, self.LINK) == "Hand-edited text that ends differently."
        )

    def test_matching_note_is_left_alone(self) -> None:
        finding = {
            "rsid": "rs9939609",
            "finding_text": "Prefix — New note.",
            "detail": {"cross_module_note": "New note."},
        }
        assert refreshed_finding_text(finding, self.LINK) == "Prefix — New note."

    def test_missing_stored_note_is_not_guessed_at(self) -> None:
        finding = {"rsid": "rs9939609", "finding_text": "Prefix — Old note.", "detail": {}}
        assert refreshed_finding_text(finding, self.LINK) == "Prefix — Old note."

    def test_a_note_appearing_only_mid_text_is_not_replaced(self) -> None:
        """Anchored at the end, so an occurrence elsewhere cannot be clipped."""
        finding = {
            "rsid": "rs9939609",
            "finding_text": "Old note. appears first — then something else",
            "detail": {"cross_module_note": "Old note."},
        }
        assert refreshed_finding_text(finding, self.LINK) == (
            "Old note. appears first — then something else"
        )


class TestPanelCrossModuleLinks:
    def test_indexes_only_snps_that_declare_a_link(self) -> None:
        panel = load_gene_health_panel()
        links = panel_cross_module_links(panel)
        declared = {
            snp.rsid for pathway in panel.pathways for snp in pathway.snps if snp.cross_module
        }
        assert set(links) == declared
        assert declared, "gene health panel declares no cross-links — guard is vacuous"


class TestCurrentRecommendation:
    """Per-SNP recommendation prose is panel data persisted into each finding.

    The FTO row told the user to see Nutrigenomics for dietary considerations,
    and the pathway-detail endpoint reads it straight from the stored row — so
    the detail panel kept advertising content that does not exist even once the
    adjacent cross-module card had been retargeted.
    """

    LEGACY = (
        "FTO is the most replicated obesity GWAS locus. Risk allele effect on BMI is "
        "attenuated by physical activity. Carriers benefit from regular exercise and "
        "mindful eating. See the Nutrigenomics module for dietary considerations "
        "related to FTO genotype."
    )

    def test_legacy_fto_recommendation_is_refreshed(self) -> None:
        current = current_recommendation("gene_health", "rs9939609", self.LEGACY)
        assert current is not None
        assert "nutrigenomics" not in current.lower()
        # The rest of the curated advice is retained, not truncated away.
        assert "attenuated by physical activity" in current

    def test_a_snp_with_no_stored_recommendation_gets_none(self) -> None:
        """A called Standard non-carrier must not be handed carrier advice.

        Storage deliberately writes no ``snp_finding`` row for a Standard
        genotype, so the stored recommendation is absent. Looking the panel up
        regardless would give a hom-ref user instructions written for carriers.
        """
        assert current_recommendation("gene_health", "rs9939609", None) is None
        assert current_recommendation("gene_health", "rs9939609", "") == ""

    def test_unknown_rsid_keeps_what_was_stored(self) -> None:
        assert current_recommendation("gene_health", "rs00000000", "stored") == "stored"

    def test_module_without_a_panel_keeps_what_was_stored(self) -> None:
        assert current_recommendation("cancer", "rs1805007", "stored") == "stored"

    def test_missing_rsid_keeps_what_was_stored(self) -> None:
        assert current_recommendation("gene_health", None, "stored") == "stored"
