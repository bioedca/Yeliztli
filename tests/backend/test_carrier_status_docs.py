"""Docs↔code guard: carrier-status docs describe the affected-status behavior (#1612).

``docs/modules/health-risk/carrier-status.md`` used to claim *"Two-copy
(homozygous) findings are excluded here"*, but
``backend/analysis/carrier_status.py::extract_carrier_variants`` intentionally
surfaces autosomal-recessive **homozygous** P/LP variants and **possible
compound-heterozygous** patterns as *affected-status* findings (categories
``autosomal_recessive_affected`` / ``autosomal_recessive_possible_compound_
heterozygote``). This locks the doc to the implemented behavior so it can't drift
back to telling users those disease-state findings won't appear on the page.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_DOC = _REPO / "docs" / "modules" / "health-risk" / "carrier-status.md"

# Terms the doc must carry now that the module returns these findings.
_REQUIRED_PHRASES = ("affected-status", "homozygous", "compound heterozygote")


def test_docs_describe_affected_status_behavior() -> None:
    # Collapse whitespace so multi-word phrases match across Markdown line wraps.
    text = re.sub(r"\s+", " ", _DOC.read_text(encoding="utf-8").lower())
    missing = [p for p in _REQUIRED_PHRASES if p not in text]
    assert not missing, (
        "docs/modules/health-risk/carrier-status.md no longer documents the carrier "
        f"module's affected-status behavior (missing {missing}). extract_carrier_variants "
        "surfaces homozygous / possible-compound-het AR P/LP as affected-status findings "
        "— keep the 'What you'll see' section aligned (#1612)."
    )


def test_docs_do_not_claim_homozygous_findings_are_excluded() -> None:
    """The stale, incorrect claim — that two-copy/homozygous findings are
    'excluded here' — must not return: the module surfaces them as affected-status
    findings, so a user must not be told they won't appear (#1612)."""
    text = _DOC.read_text(encoding="utf-8").lower()
    assert "excluded here" not in text, (
        "carrier-status.md again claims two-copy/homozygous findings are 'excluded here', "
        "but extract_carrier_variants surfaces them as affected-status findings (#1612)."
    )
