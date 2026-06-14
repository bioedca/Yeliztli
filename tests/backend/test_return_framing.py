"""Tests for the shared responsible-return framing (SW-A1)."""

from __future__ import annotations

import re
from pathlib import Path

from backend.analysis.return_framing import (
    CLIA_CONFIRMATION,
    prs_ci_label,
    prs_return_framing,
    prs_source_population_label,
)

# Reader-facing copy of CLIA_CONFIRMATION lives in this frontend component
# (its own docstring names the backend constant as canonical, "keep in sync").
_CLIA_GATE_TSX = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "components"
    / "ui"
    / "ClinicalConfirmationGate.tsx"
)


def _normalize(text: str) -> str:
    """Collapse all runs of whitespace to single spaces and strip ends."""
    return " ".join(text.split())


def _extract_clia_paragraph(tsx: str) -> str | None:
    """Return the whitespace-normalized text of the gate's CLIA disclaimer <p>.

    Strips any JSX ``{...}`` expressions inside the paragraph. Returns ``None``
    if no ``<p>`` mentioning CLIA is found, so a structural refactor of the
    component trips the parity test instead of making it silently vacuous.
    """
    for body in re.findall(r"<p[^>]*>(.*?)</p>", tsx, re.S):
        if "CLIA" in body:
            return _normalize(re.sub(r"\{[^}]*\}", "", body))
    return None


class TestCliaConfirmation:
    def test_text_is_confirm_in_clia_and_counseling(self) -> None:
        text = CLIA_CONFIRMATION.lower()
        assert "clia" in text
        assert "not a clinical diagnosis" in text
        assert "genetic counselor" in text


class TestSourcePopulationLabel:
    def test_names_the_population(self) -> None:
        assert "EUR" in prs_source_population_label("EUR")

    def test_handles_missing_population(self) -> None:
        assert "unspecified" in prs_source_population_label(None)


class TestCiLabel:
    def test_paired_ci(self) -> None:
        assert prs_ci_label(55.2, 74.8) == "95% CI 55–75th percentile"

    def test_ci_always_stated_when_unavailable(self) -> None:
        assert "unavailable" in prs_ci_label(None, None)
        assert "unavailable" in prs_ci_label(50.0, None)


class TestPrsReturnFraming:
    def test_block_pairs_research_source_and_ci(self) -> None:
        block = prs_return_framing(
            {"source_ancestry": "EAS", "bootstrap_ci_lower": 40.0, "bootstrap_ci_upper": 60.0}
        )
        assert block["research_use_only"] is True
        assert block["source_population"] == "EAS"
        assert "EAS" in block["source_population_label"]
        assert block["ci_label"] == "95% CI 40–60th percentile"

    def test_block_states_ci_unavailable_when_missing(self) -> None:
        block = prs_return_framing({"source_ancestry": "EUR"})
        assert "unavailable" in block["ci_label"]


class TestCliaCrossStackParity:
    """The clinical CLIA-confirmation disclaimer is shown to a user before they
    act on an actionable P/LP finding. It is duplicated as hardcoded text in the
    backend constant ``CLIA_CONFIRMATION`` and the frontend
    ``ClinicalConfirmationGate.tsx`` card, with "keep identical" enforced only by
    code comments. This guard fails if the two copies drift, so the reader-facing
    safety wording can't silently weaken while both suites stay green (#565)."""

    def test_gate_component_exists(self) -> None:
        # The parity test is meaningless if the component moved/renamed; assert
        # the path up front with a clear message rather than failing obscurely.
        assert _CLIA_GATE_TSX.exists(), (
            f"ClinicalConfirmationGate.tsx not found at {_CLIA_GATE_TSX} — update "
            "this cross-stack CLIA parity guard if the component was moved/renamed."
        )

    def test_frontend_gate_text_matches_backend_constant(self) -> None:
        frontend = _extract_clia_paragraph(_CLIA_GATE_TSX.read_text(encoding="utf-8"))
        assert frontend is not None, (
            "No <p> mentioning CLIA found in ClinicalConfirmationGate.tsx — the "
            "reader-facing disclaimer paragraph could not be located (component "
            "structure changed?); update this parity guard."
        )
        assert frontend == _normalize(CLIA_CONFIRMATION), (
            "ClinicalConfirmationGate.tsx CLIA disclaimer has DRIFTED from the "
            "canonical backend CLIA_CONFIRMATION constant — the two clinical-safety "
            "copies must stay identical (#565).\n"
            f"  backend : {_normalize(CLIA_CONFIRMATION)!r}\n"
            f"  frontend: {frontend!r}"
        )
