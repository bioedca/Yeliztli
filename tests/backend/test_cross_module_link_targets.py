"""A cross-module link must point somewhere the target actually surfaces (#2021).

The cards that say "See <Module> for …" are the app's "we joined the dots for
you" feature, and they were pointing at modules that had never heard of the
subject: gene_health sent FTO rs9939609 to Nutrigenomics, which lists it under
``additional_genes`` and never scores it, and both celiac HLA proxies promised
"gluten-related nutrient interactions" from a panel with zero occurrences of
the word.

Scope is deliberately the **Nutrigenomics target**. A family-wide guard over
every cross-module link belongs with the full audit in #2024 and would fail
today on ``sleep rs762551 -> pharmacogenomics``, that issue's headline defect.
Pharmacogenomics targets are gated separately, by drug rather than by rsid
(#2020).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PANEL_DIR = Path(__file__).resolve().parent.parent.parent / "backend" / "data" / "panels"
NUTRIGENOMICS_PANEL = PANEL_DIR / "nutrigenomics_panel.json"


def _panel(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _scored_rsids(panel: dict) -> set[str]:
    """rsids the module actually scores — ``additional_genes`` is not coverage."""
    return {
        snp["rsid"] for pathway in panel.get("pathways", []) for snp in pathway.get("snps", [])
    }


def _cross_module_links() -> list[tuple[str, str, dict]]:
    """Every (source module, rsid, cross_module block) declared across panels."""
    links: list[tuple[str, str, dict]] = []
    for path in sorted(PANEL_DIR.glob("*_panel.json")):
        panel = _panel(path)
        source = panel.get("module", path.stem)
        for pathway in panel.get("pathways", []):
            for snp in pathway.get("snps", []):
                cross = snp.get("cross_module")
                if cross:
                    links.append((source, snp["rsid"], cross))
    return links


@pytest.fixture(scope="module")
def nutrigenomics_scored() -> set[str]:
    return _scored_rsids(_panel(NUTRIGENOMICS_PANEL))


class TestNutrigenomicsLinkTargets:
    """Every link into Nutrigenomics must name rsids Nutrigenomics scores."""

    def _links(self) -> list[tuple[str, str, dict]]:
        return [
            (src, rsid, cross)
            for src, rsid, cross in _cross_module_links()
            if cross.get("module") == "nutrigenomics"
        ]

    def test_guard_is_not_vacuous(self) -> None:
        assert self._links(), "no Nutrigenomics cross-links found — guard proves nothing"

    def test_every_link_declares_target_rsids(self) -> None:
        for source, rsid, cross in self._links():
            declared = cross.get("target_rsids")
            assert isinstance(declared, list) and declared, (
                f"{source}/{rsid} points at Nutrigenomics without naming what the "
                f"user will find there"
            )

    def test_declared_targets_are_scored_not_merely_listed(
        self, nutrigenomics_scored: set[str]
    ) -> None:
        """Fails on the pre-fix panels: FTO is in additional_genes, never scored."""
        for source, rsid, cross in self._links():
            for target in cross.get("target_rsids") or []:
                assert target in nutrigenomics_scored, (
                    f"{source}/{rsid} promises {target}, which Nutrigenomics does "
                    f"not score (additional_genes is a reference list, not coverage)"
                )

    def test_no_link_promises_a_subject_the_panel_never_mentions(self) -> None:
        """The celiac links promised gluten from a panel with no gluten in it."""
        panel_text = NUTRIGENOMICS_PANEL.read_text(encoding="utf-8").lower()
        for source, rsid, cross in self._links():
            note = cross["note"].lower()
            for subject in ("gluten", "celiac", "coeliac"):
                if subject in note:
                    assert subject in panel_text, (
                        f"{source}/{rsid} sends the user to Nutrigenomics for "
                        f"{subject!r}, which the panel never mentions"
                    )


class TestNutrigenomicsAdditionalGenesAreNotCoverage:
    """Lock the distinction the defect rested on."""

    def test_fto_is_listed_but_not_scored(self, nutrigenomics_scored: set[str]) -> None:
        panel = _panel(NUTRIGENOMICS_PANEL)
        assert "rs9939609" in panel["additional_genes"]["FTO"]["rsids"]
        assert "rs9939609" not in nutrigenomics_scored

    def test_apoe_note_still_states_the_sections_meaning(self) -> None:
        """The section's own APOE entry is what documents it as non-scoring."""
        panel = _panel(NUTRIGENOMICS_PANEL)
        assert "not scored" in panel["additional_genes"]["APOE"]["note"].lower()


class TestFtoLinkTarget:
    """FTO must point at a module that actually surfaces rs9939609."""

    def _fto_cross_module(self) -> dict:
        panel = _panel(PANEL_DIR / "gene_health_panel.json")
        for pathway in panel["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == "rs9939609":
                    assert snp.get("cross_module"), "FTO cross-link disappeared"
                    return snp["cross_module"]
        pytest.fail("rs9939609 not found in the gene health panel")

    def test_target_module_surfaces_the_variant(self) -> None:
        from backend.analysis.metabolic_prs import ANCHOR_SNPS

        cross = self._fto_cross_module()
        assert cross["module"] == "metabolic"
        anchors = {anchor["rsid"] for anchors in ANCHOR_SNPS.values() for anchor in anchors}
        assert "rs9939609" in anchors, "Metabolic no longer carries the FTO anchor"

    def test_note_does_not_promise_dietary_recommendations(self) -> None:
        """Metabolic reports a BMI/adiposity anchor, not dietary advice."""
        note = self._fto_cross_module()["note"].lower()
        assert "nutrigenomics" not in note
        assert "dietary recommendation" not in note

    def test_recommendation_text_drops_the_nutrigenomics_promise(self) -> None:
        panel = _panel(PANEL_DIR / "gene_health_panel.json")
        for pathway in panel["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == "rs9939609":
                    assert "nutrigenomics" not in snp["recommendation_text"].lower()
                    return
        pytest.fail("rs9939609 not found in the gene health panel")

    def test_note_reuses_curated_wording_for_both_halves(self) -> None:
        """The note may not introduce a claim neither side already carries.

        Retargeting is a wording change on a science panel, so each half has to
        trace to curated text: the biology to this panel's own effect summaries,
        the destination framing to the Metabolic anchor's own summary. An
        earlier revision said "body-weight set point", which appears in neither.
        """
        from backend.analysis.metabolic_prs import ANCHOR_SNPS

        note = self._fto_cross_module()["note"]
        panel = _panel(PANEL_DIR / "gene_health_panel.json")
        summaries = " ".join(
            effect["effect_summary"]
            for pathway in panel["pathways"]
            for snp in pathway["snps"]
            if snp["rsid"] == "rs9939609"
            for effect in snp["genotype_effects"].values()
        ).lower()
        assert "appetite regulation" in note.lower()
        assert "appetite regulation" in summaries

        anchor = next(
            a for anchors in ANCHOR_SNPS.values() for a in anchors if a["rsid"] == "rs9939609"
        )
        assert "bmi/adiposity" in note.lower()
        assert "bmi/adiposity" in anchor["summary"].lower()

        assert "set point" not in note.lower(), (
            "uncited claim reintroduced — every claim must trace to curated text"
        )

    def test_dedup_note_no_longer_names_nutrigenomics_as_canonical(self) -> None:
        """The panel contradicted itself: canonical in a module that never scores it."""
        panel = _panel(PANEL_DIR / "gene_health_panel.json")
        note = panel["scoring_rules"]["cross_module_deduplication"]
        assert "rs9939609 (canonical in Nutrigenomics module)" not in note


class TestCeliacLinksDropped:
    """The celiac proxies keep their inline assessment and lose the dead link."""

    CELIAC_RSIDS = ("rs2187668", "rs7454108")

    def test_no_celiac_cross_module_link_remains(self) -> None:
        panel = _panel(PANEL_DIR / "allergy_panel.json")
        for pathway in panel["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] in self.CELIAC_RSIDS:
                    assert "cross_module" not in snp, (
                        f"{snp['rsid']} still cross-links; Nutrigenomics has no "
                        f"gluten or celiac content"
                    )

    def test_combined_celiac_assessment_survives(self) -> None:
        """Dropping the link must not remove the module's own celiac content."""
        panel = _panel(PANEL_DIR / "allergy_panel.json")
        combined = panel["special_calling"]["celiac_DQ2_DQ8_combined"]
        assert set(combined["rsids"]) == set(self.CELIAC_RSIDS)


class TestPanelMetadataMatchesItsLinks:
    """The panel's prose inventory must not drift from the links it declares."""

    def test_gene_health_description_names_every_target(self) -> None:
        panel = _panel(PANEL_DIR / "gene_health_panel.json")
        declared = {
            snp["cross_module"]["module"]
            for pathway in panel["pathways"]
            for snp in pathway["snps"]
            if snp.get("cross_module")
        }
        description = panel["description"].lower()
        for module in declared:
            assert module.replace("_", " ") in description, (
                f"panel declares a {module!r} cross-link its description omits"
            )
        # ...and does not advertise a target it no longer links to.
        assert "nutrigenomics" not in description

    def test_cross_module_links_inventory_matches_the_snp_blocks(self) -> None:
        """The top-level cross_module_links list is a second representation."""
        panel = _panel(PANEL_DIR / "gene_health_panel.json")
        from_snps = {
            snp["cross_module"]["module"]
            for pathway in panel["pathways"]
            for snp in pathway["snps"]
            if snp.get("cross_module")
        }
        from_inventory = {link["to_module"] for link in panel["cross_module_links"]}
        assert from_snps <= from_inventory, (
            f"cross_module_links omits {sorted(from_snps - from_inventory)}"
        )
        assert "nutrigenomics" not in from_inventory
