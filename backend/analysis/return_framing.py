"""Shared responsible-return framing (SW-A1 / roadmap #10) — §12.3 / §12.10.

One home for the two load-bearing return-of-results statements so they read
identically everywhere they surface:

  - **PRS** results are *research-only*, must carry their **source population**,
    and explicitly state that an individual interval is unavailable with the
    current weight-set inputs.
  - An actionable **ClinVar Pathogenic / Likely-pathogenic** result is
    array-derived and must be **confirmed in a CLIA/accredited lab** with genetic
    counseling before any medical action (mirrors the APOE-gate framing).

These are additive disclosure helpers: they never change a score, percentile,
evidence level, or ClinVar significance.
"""

from __future__ import annotations

from typing import Any

# Non-dismissible confirm-in-CLIA framing for actionable P/LP findings.
CLIA_CONFIRMATION = (
    "This is an array-derived research/educational result, not a clinical "
    "diagnosis. Genotyping-array calls can be wrong, especially for rare "
    "variants. Before any medical decision, confirm an actionable result in a "
    "CLIA/accredited laboratory and review it with a genetic counselor or clinician."
)


def prs_source_population_label(source_ancestry: str | None) -> str:
    """Mandatory source-population label for a PRS percentile (§12.3)."""
    pop = source_ancestry or "an unspecified"
    return (
        f"Derived in {pop} ancestry. A percentile is a research estimate whose "
        f"accuracy is reduced the further your ancestry is from the source population."
    )


def prs_ci_label(ci_lower: float | None, ci_upper: float | None) -> str:
    """Legacy interval label; current PRS inputs do not support individual CIs."""
    if ci_lower is not None or ci_upper is not None:
        return "PRS interval unavailable (effect-size uncertainty inputs are not available)"
    return "PRS interval unavailable (effect-size uncertainty inputs are not available)"


def prs_return_framing(detail: dict[str, Any]) -> dict[str, Any]:
    """Consolidated responsible-return block for a PRS finding's ``detail_json``.

    Pairs research-only + the source-population label + the interval status so a
    consumer can render them without re-deriving the framing.
    """
    return {
        "research_use_only": True,
        "source_population": detail.get("source_ancestry"),
        "source_population_label": prs_source_population_label(detail.get("source_ancestry")),
        "ci_label": prs_ci_label(
            detail.get("bootstrap_ci_lower"), detail.get("bootstrap_ci_upper")
        ),
    }
