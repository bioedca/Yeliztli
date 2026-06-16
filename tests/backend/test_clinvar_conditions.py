"""Unit + cross-stack parity tests for the backend ClinVar-conditions formatter (#918).

``backend/analysis/clinvar_conditions.py`` is the report-side counterpart of the
frontend display helper ``frontend/src/lib/clinvar-conditions.ts`` (#917). Both
clean the raw ``CLNDN`` blob identically; the parity test pins the cleaning rules
(placeholders + drug-response suffixes) to the frontend so the two cannot silently
drift (same cross-stack-parity pattern as #565/#574/#580).
"""

from __future__ import annotations

from pathlib import Path

from backend.analysis.clinvar_conditions import (
    _DRUG_RESPONSE,
    _PLACEHOLDERS,
    format_clinvar_conditions,
    format_clinvar_conditions_text,
)

# The real CFTR rs78655421 carrier blob shape from #832/#918: real disease names
# interleaved with placeholders, a case-duplicate, and a drug-response entry.
_CFTR_BLOB = (
    "Respiratory ciliopathies including non-CF bronchiectasis|"
    "Cystic fibrosis|not provided|not specified|CFTR-related disorder|"
    "cystic fibrosis|Obstructive azoospermia|ivacaftor response - Efficacy"
)

_FRONTEND_TS = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "clinvar-conditions.ts"
)


class TestFormatClinvarConditions:
    def test_cftr_blob_cleaned(self) -> None:
        out = format_clinvar_conditions(_CFTR_BLOB)
        assert out == [
            "Respiratory ciliopathies including non-CF bronchiectasis",
            "Cystic fibrosis",
            "CFTR-related disorder",
            "Obstructive azoospermia",
        ]

    def test_text_is_comma_joined(self) -> None:
        assert format_clinvar_conditions_text("Cystic fibrosis|not provided") == "Cystic fibrosis"

    def test_none_and_empty(self) -> None:
        assert format_clinvar_conditions(None) == []
        assert format_clinvar_conditions("") == []
        assert format_clinvar_conditions_text(None) == ""

    def test_placeholders_dropped(self) -> None:
        # Placeholder-only → empty (a falsy text result lets the report hide the row).
        assert format_clinvar_conditions("not provided|not specified") == []
        assert format_clinvar_conditions("NOT PROVIDED|Not Specified") == []
        assert format_clinvar_conditions_text("not provided") == ""

    def test_drug_response_dropped(self) -> None:
        for suffix in ("Efficacy", "Dosage", "Toxicity"):
            assert format_clinvar_conditions(f"warfarin response - {suffix}") == []
        # Case-insensitive on the suffix.
        assert format_clinvar_conditions("drug response - efficacy") == []

    def test_dedupe_keeps_first_casing(self) -> None:
        assert format_clinvar_conditions("Cystic Fibrosis|cystic fibrosis|CYSTIC FIBROSIS") == [
            "Cystic Fibrosis"
        ]


class TestFrontendParity:
    """The cleaning rules must match frontend/src/lib/clinvar-conditions.ts (#917)."""

    def test_frontend_helper_exists(self) -> None:
        assert _FRONTEND_TS.exists(), (
            f"clinvar-conditions.ts not found at {_FRONTEND_TS} — update this parity "
            "guard if the frontend helper was moved/renamed."
        )

    def test_placeholders_match_frontend(self) -> None:
        ts = _FRONTEND_TS.read_text(encoding="utf-8")
        # Frontend: const _PLACEHOLDERS = new Set(["not provided", "not specified"])
        for placeholder in _PLACEHOLDERS:
            assert f'"{placeholder}"' in ts, (
                f"backend placeholder {placeholder!r} missing from the frontend helper — "
                "the two ClinVar-conditions cleaners have drifted (#918)."
            )

    def test_drug_response_suffixes_match_frontend(self) -> None:
        ts = _FRONTEND_TS.read_text(encoding="utf-8").lower()
        # Frontend: /\s-\s(efficacy|dosage|toxicity)$/i
        for suffix in ("efficacy", "dosage", "toxicity"):
            assert suffix in ts, (
                f"backend drug-response suffix {suffix!r} missing from the frontend helper — "
                "the two ClinVar-conditions cleaners have drifted (#918)."
            )
        # Confirm the backend regex actually recognizes each suffix.
        for suffix in ("efficacy", "dosage", "toxicity"):
            assert _DRUG_RESPONSE.search(f"x - {suffix}")
