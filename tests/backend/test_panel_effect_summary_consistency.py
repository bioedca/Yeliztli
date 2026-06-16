"""Panel-level guards for internally inconsistent effect-summary wording."""

from __future__ import annotations

import json
import re
from pathlib import Path

PANEL_DIR = Path(__file__).resolve().parents[2] / "backend" / "data" / "panels"

POSITIVE_FLEXIBILITY = (
    re.compile(r"\bgreater\b[^.]{0,50}\bflexib", re.IGNORECASE),
    re.compile(r"\bhigher\b[^.]{0,50}\bflexib", re.IGNORECASE),
    re.compile(r"\bhigher range of motion\b", re.IGNORECASE),
    re.compile(r"\bincreased range of motion\b", re.IGNORECASE),
)
NEGATIVE_FLEXIBILITY = (
    re.compile(r"\breduced\b[^.]{0,50}\bflexib", re.IGNORECASE),
    re.compile(r"\blower\b[^.]{0,50}\bflexib", re.IGNORECASE),
    re.compile(r"\blower range of motion\b", re.IGNORECASE),
    re.compile(r"\breduced range of motion\b", re.IGNORECASE),
)
INJURY_TARGETS = {"injury", "injuries", "risk", "susceptibility"}
INCREASE_WORDS = {"higher", "increased", "elevated"}
REDUCTION_WORDS = {"lower", "reduced", "decreased"}
ALL_INJURY_DIRECTION_WORDS = INCREASE_WORDS | REDUCTION_WORDS


def _matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _has_directional_injury_phrase(text: str, direction_words: set[str]) -> bool:
    tokens = re.findall(r"[a-z]+", text.lower())
    for index, token in enumerate(tokens):
        if token not in INJURY_TARGETS:
            continue
        window = tokens[max(0, index - 5) : index]
        for offset, candidate in enumerate(window):
            if candidate not in direction_words:
                continue
            intervening = window[offset + 1 :]
            if not ALL_INJURY_DIRECTION_WORDS.intersection(intervening):
                return True
    return False


def _has_flexibility_injury_contradiction(text: str) -> bool:
    positive_flex = _matches_any(text, POSITIVE_FLEXIBILITY)
    negative_flex = _matches_any(text, NEGATIVE_FLEXIBILITY)
    increased_injury = _has_directional_injury_phrase(text, INCREASE_WORDS)
    reduced_injury = _has_directional_injury_phrase(text, REDUCTION_WORDS)
    return (positive_flex and increased_injury) or (negative_flex and reduced_injury)


def _iter_effect_summaries() -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for panel_path in sorted(PANEL_DIR.glob("*_panel.json")):
        panel = json.loads(panel_path.read_text(encoding="utf-8"))
        for pathway in panel.get("pathways", []):
            for snp in pathway.get("snps", []):
                rsid = snp.get("rsid", "<missing-rsid>")
                for genotype, effect in snp.get("genotype_effects", {}).items():
                    summary = effect.get("effect_summary", "")
                    rows.append((panel_path.name, rsid, genotype, summary))
    return rows


def test_detector_flags_known_flexibility_injury_contradictions() -> None:
    bad_positive = "Associated with greater flexibility and higher soft-tissue injury risk."
    bad_negative = "Associated with reduced flexibility and lower injury risk."
    good_positive = "Associated with greater flexibility and lower tendon/ligament injury risk."
    good_negative = "Associated with reduced flexibility and increased injury susceptibility."

    assert _has_flexibility_injury_contradiction(bad_positive)
    assert _has_flexibility_injury_contradiction(bad_negative)
    assert not _has_flexibility_injury_contradiction(good_positive)
    assert not _has_flexibility_injury_contradiction(good_negative)


def test_panel_effect_summaries_do_not_self_contradict_flexibility_and_injury() -> None:
    effect_summaries = _iter_effect_summaries()
    assert effect_summaries

    contradictions = [
        f"{panel}:{rsid}:{genotype} -> {summary}"
        for panel, rsid, genotype, summary in effect_summaries
        if _has_flexibility_injury_contradiction(summary)
    ]

    assert contradictions == []
