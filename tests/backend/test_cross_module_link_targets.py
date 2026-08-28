"""Every cross-module link must name what the user will find there (#2024).

The cards that say "See <Module> for …" are the app's "we joined the dots for
you" feature, and an audit found most of them pointing at a module that never
surfaces the subject: CYP1A2 sent to a Pharmacogenomics module that cannot call
CYP1A2, MC1R sent to a Cancer module that assesses melanoma through CDKN2A and
BAP1, FTO sent to a Nutrigenomics module that lists it under ``additional_genes``
and never scores it (#2021), and four HLA proxies sent for drug guidance that
does not exist (#2020).

Nothing caught them because a note is free text and ``target_module`` is just a
name. The fix is that each link declares what the user will find **in the
target's own currency**, and this asserts the declaration resolves:

===================  ====================================================
declaration          checked against
===================  ====================================================
``target_rsids``     rsids the target panel actually **scores** — its
                     ``pathways[].snps[]``, never ``additional_genes``
``target_genes``     ``gene_symbol`` entries of a gene-keyed panel (Cancer)
``drug``             the Pharmacogenomics lane, decided per request against
                     ``cpic_guidelines`` and the caller's gene set (#2020);
                     this file only requires the drug to be named
===================  ====================================================

A single "the rsid must be scored in the target" rule — what #2024 originally
proposed — cannot work: the targets do not share a currency. ``cancer_panel``
is gene-keyed with no rsids to match, Pharmacogenomics is drug-keyed, and
Metabolic is defined in code rather than a panel at all.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PANEL_DIR = REPO_ROOT / "backend" / "data" / "panels"
CPIC_GUIDELINES_PATH = REPO_ROOT / "backend" / "data" / "cpic" / "cpic_guidelines.csv"
CPIC_ALLELES_PATH = REPO_ROOT / "backend" / "data" / "cpic" / "cpic_alleles.csv"

#: Targets whose coverage is not a panel of scored rsids.
PHARMACOGENOMICS = "pharmacogenomics"
NUTRIGENOMICS_PANEL = PANEL_DIR / "nutrigenomics_panel.json"
#: Modules defined in code rather than ``backend/data/panels``.
CODE_DEFINED_TARGETS = {"metabolic"}

#: Source modules whose route gates a Pharmacogenomics handoff on whether the
#: destination can actually act for *this* sample. Naming a drug is not enough:
#: #2020's dead end was a "View in Pharmacogenomics" button rendered whether or
#: not PGx had a callable gene and a matching guideline. Only Allergy carries
#: that gate today, so a PGx link from anywhere else fails closed here rather
#: than shipping an ungated button. Adding a module means porting the gate, not
#: extending this set.
PGX_GATED_SOURCE_MODULES = frozenset({"allergy"})


def _panel(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _scored_rsids(panel: dict) -> set[str]:
    """rsids the module actually scores — ``additional_genes`` is not coverage."""
    return {
        snp["rsid"] for pathway in panel.get("pathways", []) for snp in pathway.get("snps", [])
    }


def _panel_genes(panel: dict) -> set[str]:
    """Gene symbols a gene-keyed panel evaluates (Cancer), else its SNP genes."""
    entries = panel.get("genes")
    if isinstance(entries, list):
        return {entry["gene_symbol"] for entry in entries if entry.get("gene_symbol")}
    return {
        snp["gene"]
        for pathway in panel.get("pathways", [])
        for snp in pathway.get("snps", [])
        if snp.get("gene")
    }


def _is_gene_keyed(panel: dict) -> bool:
    """A gene-keyed panel (Cancer) lists ``genes`` instead of scored rsids."""
    return isinstance(panel.get("genes"), list)


def _panels_by_module() -> dict[str, dict]:
    loaded: dict[str, dict] = {}
    for path in sorted(PANEL_DIR.glob("*_panel.json")):
        panel = _panel(path)
        loaded[panel.get("module", path.stem.replace("_panel", ""))] = panel
    return loaded


def _cross_module_links() -> list[tuple[str, str, str, dict]]:
    """Every (source module, rsid, gene, cross_module block) across all panels."""
    links: list[tuple[str, str, str, dict]] = []
    for module, panel in _panels_by_module().items():
        for pathway in panel.get("pathways", []):
            for snp in pathway.get("snps", []):
                cross = snp.get("cross_module")
                # ``is not None`` on purpose: an empty ``{}`` is malformed, not
                # absent. Production loaders preserve it and the Skin, Allergy
                # and Gene Health generators treat anything non-None as a
                # declaration before indexing ``cross_module["module"]``, so a
                # non-Standard call at that SNP would crash scoring. Skipping it
                # here would hide exactly that from every check below.
                if cross is not None:
                    links.append((module, snp["rsid"], snp.get("gene") or "", cross))
    return links


def _metabolic_anchor_rsids() -> set[str]:
    from backend.analysis.metabolic_prs import ANCHOR_SNPS

    return {anchor["rsid"] for anchors in ANCHOR_SNPS.values() for anchor in anchors}


@pytest.fixture(scope="module")
def panels() -> dict[str, dict]:
    return _panels_by_module()


class TestEveryLinkDeclaresItsTarget:
    """The declaration is what makes a promise checkable at all."""

    def test_guard_is_not_vacuous(self) -> None:
        assert _cross_module_links(), "no cross-module links found — guard proves nothing"

    def test_every_block_is_well_formed(self) -> None:
        """A ``cross_module`` key must be a usable declaration or absent.

        ``{}`` is the dangerous shape: production loaders keep it, and the Skin,
        Allergy and Gene Health generators treat any non-None value as a
        declaration before reading ``cross_module["module"]`` — so a
        non-Standard call at that SNP raises ``KeyError`` mid-scoring.
        """
        for source, rsid, _gene, cross in _cross_module_links():
            assert isinstance(cross, dict) and cross, (
                f"{source}/{rsid} has a malformed cross_module block {cross!r}; "
                "use a full declaration or remove the key"
            )
            assert cross.get("module"), f"{source}/{rsid} names no target module"
            assert cross.get("note"), f"{source}/{rsid} carries no note"

    def test_target_module_exists(self, panels: dict[str, dict]) -> None:
        known = set(panels) | CODE_DEFINED_TARGETS | {PHARMACOGENOMICS}
        for source, rsid, _gene, cross in _cross_module_links():
            assert cross["module"] in known, (
                f"{source}/{rsid} points at unknown module {cross['module']!r}"
            )

    def test_every_link_declares_exactly_one_kind_of_coverage(self) -> None:
        for source, rsid, _gene, cross in _cross_module_links():
            declared = [key for key in ("target_rsids", "target_genes", "drug") if cross.get(key)]
            assert len(declared) == 1, (
                f"{source}/{rsid} -> {cross['module']} declares {declared or 'nothing'}; "
                f"exactly one of target_rsids / target_genes / drug is required"
            )

    def test_pharmacogenomics_links_are_drug_keyed(self) -> None:
        """PGx coverage is a drug and a callable gene, never an rsid (#2020)."""
        for source, rsid, _gene, cross in _cross_module_links():
            if cross["module"] != PHARMACOGENOMICS:
                continue
            assert cross.get("drug"), f"{source}/{rsid} names no drug for the PGx lane"
            assert "target_rsids" not in cross, (
                f"{source}/{rsid} declares rsids for a drug-keyed target"
            )

    def test_pgx_links_only_come_from_modules_that_gate_them(self) -> None:
        for source, rsid, _gene, cross in _cross_module_links():
            if cross["module"] != PHARMACOGENOMICS:
                continue
            assert source in PGX_GATED_SOURCE_MODULES, (
                f"{source}/{rsid} hands off to Pharmacogenomics, but {source}'s route "
                "does not gate the handoff on whether PGx can act for this sample; "
                "port that gate before declaring the link"
            )

    def test_the_gated_modules_really_implement_the_gate(self) -> None:
        """Keeps the set above a statement of capability, not a permission slip.

        Without this, ``PGX_GATED_SOURCE_MODULES`` could be widened to silence
        the check above while the route still renders an ungated button.
        """
        for module in PGX_GATED_SOURCE_MODULES:
            route = (REPO_ROOT / "backend" / "api" / "routes" / f"{module}.py").read_text()
            assert "pgx_covered_gene_drugs" in route, (
                f"{module} is listed as gating PGx handoffs but its route never "
                "consults pgx_covered_gene_drugs"
            )

    def test_the_declaration_uses_the_documented_shape(self) -> None:
        """Truthiness is not a type check, and the previous guard did check.

        ``origin/main`` asserted ``isinstance(declared, list) and declared``;
        rewriting the validator dropped it. Without it a mapping passes — the
        resolution loops below iterate a dict's keys, so a
        ``{"rs1801133": ...}`` object would resolve and the whole validator
        would go green on a declaration that does not match the contract the
        panel README documents.
        """
        for source, rsid, _gene, cross in _cross_module_links():
            for key in ("target_rsids", "target_genes"):
                if key not in cross:
                    continue
                declared = cross[key]
                assert isinstance(declared, list) and declared, (
                    f"{source}/{rsid} declares {key}={declared!r}; a non-empty list is required"
                )
                for value in declared:
                    assert isinstance(value, str) and value.strip(), (
                        f"{source}/{rsid} declares {key} entry {value!r}; "
                        "each entry must be a non-empty string"
                    )
            if "drug" in cross:
                drug = cross["drug"]
                assert isinstance(drug, str) and drug.strip(), (
                    f"{source}/{rsid} declares drug={drug!r}; a non-empty string is required"
                )

    def test_the_declared_currency_matches_the_target(self, panels: dict[str, dict]) -> None:
        """Declaring *a* currency is not enough — it must be the target's.

        "Exactly one of the three" alone leaves a hole: a link to ``skin``
        carrying only ``drug`` satisfies it, skips the Pharmacogenomics-only
        check because its target is not PGx, and skips both resolution tests
        because it declares neither rsids nor genes. The guard would then accept
        a declaration that says nothing verifiable about where it points.
        """
        for source, rsid, _gene, cross in _cross_module_links():
            target = cross["module"]
            if target == PHARMACOGENOMICS:
                expected = "drug"
            elif target in CODE_DEFINED_TARGETS:
                expected = "target_rsids"
            elif _is_gene_keyed(panels[target]):
                expected = "target_genes"
            else:
                expected = "target_rsids"
            shape = {
                "drug": "drug-keyed",
                "target_genes": "gene-keyed",
                "target_rsids": "rsid-keyed",
            }[expected]
            declared = [key for key in ("target_rsids", "target_genes", "drug") if cross.get(key)]
            assert declared == [expected], (
                f"{source}/{rsid} -> {target} declares {declared}; "
                f"a {shape} target requires {expected}"
            )


class TestDeclarationsResolve:
    """A declaration that does not resolve is the defect this issue is about."""

    def test_declared_rsids_are_scored_by_the_target(self, panels: dict[str, dict]) -> None:
        for source, rsid, _gene, cross in _cross_module_links():
            declared = cross.get("target_rsids")
            if not declared:
                continue
            target = cross["module"]
            if target in CODE_DEFINED_TARGETS:
                available = _metabolic_anchor_rsids()
            else:
                assert target in panels, f"{source}/{rsid} targets unknown panel {target!r}"
                available = _scored_rsids(panels[target])
            for wanted in declared:
                assert wanted in available, (
                    f"{source}/{rsid} promises {wanted} in {target}, which does not "
                    f"score it (additional_genes is a reference list, not coverage)"
                )

    def test_declared_genes_are_evaluated_by_the_target(self, panels: dict[str, dict]) -> None:
        for source, rsid, _gene, cross in _cross_module_links():
            declared = cross.get("target_genes")
            if not declared:
                continue
            target = cross["module"]
            assert target in panels, f"{source}/{rsid} targets unknown panel {target!r}"
            available = _panel_genes(panels[target])
            for wanted in declared:
                assert wanted in available, (
                    f"{source}/{rsid} promises {wanted} in {target}, which does not evaluate it"
                )

    def test_a_link_never_declares_its_own_subject_as_the_target(self) -> None:
        """Pointing at yourself is not a cross-reference.

        The exception is a genuinely shared variant — DRD4 rs747302 is scored by
        both Gene Health and Traits — so this only rejects a declaration that
        resolves *because* the target happens to be the source.
        """
        for source, rsid, _gene, cross in _cross_module_links():
            if cross["module"] == source:
                pytest.fail(f"{source}/{rsid} cross-links to its own module")


class TestMc1rDoesNotImplyCancerEvaluatesIt:
    """The MC1R notes sent the user to Cancer looking for their own variant.

    Cancer assesses inherited melanoma risk through CDKN2A (FAMMM) and BAP1 and
    has never evaluated MC1R, so "See Cancer module for melanoma risk
    assessment" beside an MC1R finding read as though it would be there.

    This is deliberately a **targeted** regression rather than a family-wide
    prose rule. Such a rule was tried and rejected: it flagged the IL2/IL21 ->
    Allergy note, which is already honest — it says risk is determined by
    HLA-DQ2/DQ8 and points at Allergy *for DQ2/DQ8*, naming the target's content
    rather than its own gene. Keying a guard on the shape of curated prose
    reclassifies good notes as defects; the structural declaration above is what
    is actually checkable.
    """

    MC1R_RSIDS = ("rs1805007", "rs1805008", "rs1805009")

    def _mc1r_links(self) -> list[tuple[str, dict]]:
        return [
            (rsid, cross)
            for _source, rsid, _gene, cross in _cross_module_links()
            if rsid in self.MC1R_RSIDS
        ]

    def test_all_three_are_present(self) -> None:
        assert len(self._mc1r_links()) == len(self.MC1R_RSIDS)

    def test_each_says_cancer_does_not_evaluate_mc1r(self) -> None:
        for rsid, cross in self._mc1r_links():
            assert cross["module"] == "cancer"
            assert "MC1R is not among the genes the Cancer module evaluates" in cross["note"], (
                f"{rsid} promises Cancer without saying MC1R is not evaluated there"
            )

    def test_the_note_stays_navigational(self) -> None:
        """The note must not make a claim it cannot cite.

        It is rendered beside the *source* variant's PMIDs — MC1R's — so a
        sentence about what CDKN2A and BAP1 do would be a target-gene claim
        carried on the wrong citations. Those genes belong in ``target_genes``,
        where the guard below checks them against the Cancer panel, not in
        prose the reader will read as evidenced.
        """
        for rsid, cross in self._mc1r_links():
            for gene in ("CDKN2A", "BAP1"):
                assert gene not in cross["note"], (
                    f"{rsid} names {gene} in prose rendered beside MC1R's citations"
                )

    def test_the_declaration_names_genes_cancer_really_evaluates(
        self, panels: dict[str, dict]
    ) -> None:
        cancer_genes = _panel_genes(panels["cancer"])
        for rsid, cross in self._mc1r_links():
            declared = cross.get("target_genes")
            assert declared, f"{rsid} declares no target genes"
            for gene in declared:
                assert gene in cancer_genes, (
                    f"{rsid} promises {gene}, which Cancer does not evaluate"
                )


class TestSleepCyp1a2LinkRemoved:
    """CYP1A2 → Pharmacogenomics was the last clean dead end (#2024)."""

    def _cyp1a2(self) -> dict:
        panel = _panel(PANEL_DIR / "sleep_panel.json")
        for pathway in panel["pathways"]:
            for snp in pathway["snps"]:
                if snp["rsid"] == "rs762551":
                    return snp
        pytest.fail("rs762551 not found in the sleep panel")

    def test_no_cross_module_link_remains(self) -> None:
        snp = self._cyp1a2()
        assert "cross_module" not in snp, (
            "CYP1A2 still cross-links; Pharmacogenomics cannot call CYP1A2 and "
            "cpic_guidelines has no clozapine or theophylline row"
        )

    def test_recommendation_no_longer_sends_the_user_to_pgx(self) -> None:
        """The pointer was in the recommendation too, which the detail panel serves."""
        recommendation = self._cyp1a2()["recommendation_text"]
        assert "see the Pharmacogenomics module" not in recommendation
        assert "does not call CYP1A2" in recommendation

    def test_the_caffeine_interpretation_survives(self) -> None:
        """Dropping the handoff must not remove the module's own content."""
        snp = self._cyp1a2()
        assert "caffeine half-life" in snp["recommendation_text"]
        assert set(snp["genotype_effects"]) == {"AA", "AC", "CA", "CC"}

    def test_pgx_still_covers_neither_drug(self) -> None:
        """If this ever fails, the link can come back — that is the point."""
        with CPIC_GUIDELINES_PATH.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        drugs = {row["drug"].strip().lower() for row in rows}
        genes = {row["gene"].strip().upper() for row in rows}
        assert drugs, "cpic_guidelines.csv parsed to no drugs — the check is vacuous"
        assert "clozapine" not in drugs
        assert "theophylline" not in drugs
        assert "CYP1A2" not in genes

    def test_pgx_cannot_call_cyp1a2_at_all(self) -> None:
        """The load-bearing fact, and the one the guideline table cannot show.

        A drug row is not what makes the handoff possible — an allele definition
        is. ``cpic_alleles.csv`` is the gene universe the caller can assign a
        diplotype in, so CYP1A2's absence there is why Pharmacogenomics could
        not interpret this variant even if a clozapine guideline appeared
        tomorrow. Asserting only on ``cpic_guidelines`` would let a guideline
        row alone read as "the link may return".
        """
        with CPIC_ALLELES_PATH.open(newline="", encoding="utf-8") as handle:
            genes = {row["gene"].strip().upper() for row in csv.DictReader(handle)}
        assert genes, "cpic_alleles.csv parsed to no genes — the check is vacuous"
        assert "CYP1A2" not in genes, (
            "Pharmacogenomics can now call CYP1A2; revisit the retired Sleep handoff"
        )
        # Discriminating: the same table does define the genes PGx does call, so
        # the assertion above is a real absence rather than an empty read.
        assert {"CYP2C19", "CYP2D6", "DPYD"} <= genes


# ── Coverage inherited from #2021 ────────────────────────────────────────
#
# These are panel-specific regressions the family-wide guard above does not
# subsume: it checks that a declaration resolves, not that curated prose stays
# traceable. Both matter — a link can name a scored rsid and still describe
# biology its citations do not support.


@pytest.fixture(scope="module")
def nutrigenomics_scored() -> set[str]:
    return _scored_rsids(_panel(NUTRIGENOMICS_PANEL))


class TestNutrigenomicsLinkTargets:
    """Every link into Nutrigenomics must name rsids Nutrigenomics scores."""

    def _links(self) -> list[tuple[str, str, dict]]:
        return [
            (src, rsid, cross)
            for src, rsid, _gene, cross in _cross_module_links()
            if cross.get("module") == "nutrigenomics"
        ]

    def test_guard_is_not_vacuous(self) -> None:
        assert self._links(), "no Nutrigenomics cross-links found — guard proves nothing"

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

    def test_no_note_claims_anything_about_the_target_genes(self) -> None:
        """A note may point at the target; it may not describe its biology.

        The cross-module finding carries the *source* SNP's PMIDs, so a claim
        about the target's genes would be rendered beside citations that do not
        support it — including in generated reports. Same rule the MC1R notes
        are held to above.
        """
        for source, rsid, cross in self._links():
            note = cross["note"]
            for gene in ("GC", "CYP2R1", "DBP"):
                assert gene not in note.split(), (
                    f"{source}/{rsid} describes {gene}, whose evidence this finding does not carry"
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
        cross = self._fto_cross_module()
        assert cross["module"] == "metabolic"
        assert "rs9939609" in _metabolic_anchor_rsids(), (
            "Metabolic no longer carries the FTO anchor"
        )

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
