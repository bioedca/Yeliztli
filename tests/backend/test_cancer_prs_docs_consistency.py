"""Docs↔cancer-PRS status consistency guard (#2028).

The cancer module page used to advertise breast PRS as computed after the
canonical PRS77 model had become fail-closed at runtime.  Keep the page's active
trait list tied to the weight-set gates, and require a distinct explanation for
the disabled breast model so readers do not mistake it for ordinary ancestry
calibration withholding.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_PANEL_PATH = REPO_ROOT / "backend" / "data" / "panels" / "cancer_prs_weights.json"
_DOC_PATH = REPO_ROOT / "docs" / "modules" / "health-risk" / "cancer.md"

_TRAIT_LABELS = {
    "breast_cancer": "breast",
    "prostate_cancer": "prostate",
    "colorectal_cancer": "colorectal",
    "melanoma": "melanoma",
}
_ADVERTISED_TRAITS_RE = re.compile(r"\(PRS\) for (?P<traits>[^.]+)\.", re.IGNORECASE)


def _weight_sets() -> list[dict[str, object]]:
    data = json.loads(_PANEL_PATH.read_text(encoding="utf-8"))
    return data["weight_sets"]


def _advertised_traits(doc_text: str) -> set[str]:
    match = _ADVERTISED_TRAITS_RE.search(doc_text)
    assert match, "cancer.md must retain an explicit '(PRS) for ...' active-trait list"
    prose = match.group("traits")
    return {
        trait
        for trait, label in _TRAIT_LABELS.items()
        if re.search(rf"\b{re.escape(label)}\b", prose, re.IGNORECASE)
    }


def test_cancer_docs_advertise_exactly_the_enabled_prs_models() -> None:
    doc_text = _DOC_PATH.read_text(encoding="utf-8")
    enabled = {
        weight_set["trait"]
        for weight_set in _weight_sets()
        if weight_set.get("scoring_enabled", True)
    }
    assert _advertised_traits(doc_text) == enabled, (
        "cancer.md's active PRS list must exactly match weight sets whose "
        "scoring_enabled gate is true"
    )


def test_cancer_docs_distinguish_the_runtime_block_from_ancestry_withholding() -> None:
    breast = next(
        weight_set for weight_set in _weight_sets() if weight_set["trait"] == "breast_cancer"
    )
    assert breast["model_status"] == "source_verified_runtime_blocked"
    assert breast["scoring_enabled"] is False

    doc_text = " ".join(_DOC_PATH.read_text(encoding="utf-8").lower().split())
    required_phrases = {
        "source-verified breast-cancer prs77",
        "not scored or reported",
        "multiallelic",
        "palindromic",
        "not an ancestry-calibration result",
    }
    missing = sorted(phrase for phrase in required_phrases if phrase not in doc_text)
    assert not missing, (
        "cancer.md must explain the disabled breast PRS77 and distinguish its "
        f"runtime block from ancestry withholding; missing phrases: {missing}"
    )
