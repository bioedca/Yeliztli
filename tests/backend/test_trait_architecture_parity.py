"""Cross-stack parity guard for the PRS trait-architecture education content (#574).

The polygenic-score "trait architecture" explainer — the heritability ordering,
the cross-ancestry portability claim with its **Ding et al. (2023) citation and
statistic (Pearson r ≈ −0.95 across 84 traits)**, and the calibration framing — is
duplicated as hardcoded text in the frontend ``TraitArchitectureCard.tsx`` and the
canonical backend ``PRS_TRAIT_ARCHITECTURE`` block. The card is section-level (not
per-finding), so it renders a static copy rather than threading the API value; the
two had already DRIFTED by hand (the frontend dropped the DOI). This guard fails if
the reader-facing card no longer embeds the canonical citation + statistic, so a
scientific drift (wrong page range, DOI, r-value, or paper) can't ship green.

Mirrors ``test_return_framing.py::TestCliaCrossStackParity`` (the #565 template).
"""

from __future__ import annotations

from pathlib import Path

from backend.analysis.prs import PRS_TRAIT_ARCHITECTURE

# Reader-facing copy of PRS_TRAIT_ARCHITECTURE lives in this frontend component.
_CARD_TSX = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "components"
    / "ui"
    / "TraitArchitectureCard.tsx"
)

# The specific cross-ancestry statistic, verified against Ding et al. 2023, Nature
# (Pearson correlation of −0.95 between genetic distance and PGS accuracy averaged
# across 84 traits). Pinned on both sides so a numeric drift fails CI.
_DING_STAT = "Pearson r ≈ −0.95 across 84 traits"


def _normalize(text: str) -> str:
    """Collapse all runs of whitespace to single spaces and strip ends."""
    return " ".join(text.split())


class TestCanonicalTraitArchitecture:
    """Pin the backend constant so the canonical citation/statistic itself can't
    drift silently (Crossref-verified: Nature 618:774-781 (2023); Consensus-verified
    statistic from Ding et al. 2023)."""

    def test_citation_is_pinned(self) -> None:
        assert PRS_TRAIT_ARCHITECTURE["citation"] == (
            "Ding et al., Nature 618:774-781 (2023); doi:10.1038/s41586-023-06079-4"
        )

    def test_portability_carries_the_ding_statistic(self) -> None:
        assert _DING_STAT in PRS_TRAIT_ARCHITECTURE["portability"]

    def test_heritability_ordering_is_present(self) -> None:
        assert "h²_twin > h²_SNP > h²_PRS" in PRS_TRAIT_ARCHITECTURE["heritability"]


class TestTraitArchitectureCrossStackParity:
    """The frontend card must keep embedding the canonical citation + statistic, so
    the two trait-architecture copies can no longer silently diverge (#574)."""

    def test_card_component_exists(self) -> None:
        assert _CARD_TSX.exists(), (
            f"TraitArchitectureCard.tsx not found at {_CARD_TSX} — update this "
            "cross-stack parity guard if the component was moved/renamed."
        )

    def test_card_embeds_canonical_citation(self) -> None:
        card = _normalize(_CARD_TSX.read_text(encoding="utf-8"))
        citation = PRS_TRAIT_ARCHITECTURE["citation"]
        assert citation in card, (
            "TraitArchitectureCard.tsx no longer embeds the canonical backend "
            "PRS_TRAIT_ARCHITECTURE['citation'] verbatim — the two copies have "
            "DRIFTED (#574).\n"
            f"  expected substring: {citation!r}"
        )

    def test_card_carries_the_ding_statistic(self) -> None:
        card = _normalize(_CARD_TSX.read_text(encoding="utf-8"))
        assert _DING_STAT in card, (
            "TraitArchitectureCard.tsx no longer states the Ding 2023 statistic "
            f"{_DING_STAT!r} — the cross-ancestry portability claim has drifted (#574)."
        )
