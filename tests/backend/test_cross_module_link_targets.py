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
#: Modules defined in code rather than ``backend/data/panels``.
CODE_DEFINED_TARGETS = {"metabolic"}


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
                if cross:
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
