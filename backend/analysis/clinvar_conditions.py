"""Shared ClinVar ``clinvar_conditions`` (raw ``CLNDN``) formatter for the
server-rendered reports.

ClinVar serves ``CLNDN`` verbatim: a ``|``-delimited list of disease names that
also contains ClinVar placeholders (``not provided`` / ``not specified``) and
non-disease pharmacogenomic entries (``… - Efficacy`` / ``… - Dosage`` /
``… - Toxicity``). Rendered raw, a report's "Conditions" row showed the literal
``|`` separators plus those misleading entries (e.g. CFTR ``rs78655421`` carrier
card showing ``not provided`` and ``ivacaftor response - Efficacy`` as
"conditions"; #832).

This is the backend counterpart of the frontend display helper
``frontend/src/lib/clinvar-conditions.ts`` (#917). The cleaning rules are
intentionally identical — split on ``|``, trim, drop the placeholders and the
drug-response entries, de-dupe (case-insensitive, first casing kept) — and a
parity test pins them to the frontend so the two cannot silently drift
(``tests/backend/test_clinvar_conditions.py``). The raw value is left intact in
the data layer (consistent with #832's display-only decision); this cleans only
at report-render time.
"""

from __future__ import annotations

import re

# ClinVar placeholders that are not real conditions.
_PLACEHOLDERS = frozenset({"not provided", "not specified"})

# Pharmacogenomic ClinVar entries (drug response), not disease conditions:
# "ivacaftor response - Efficacy", "<drug> - Dosage", "<drug> - Toxicity".
_DRUG_RESPONSE = re.compile(r"\s-\s(efficacy|dosage|toxicity)$", re.IGNORECASE)


def format_clinvar_conditions(raw: str | None) -> list[str]:
    """Split the raw ``CLNDN`` blob into cleaned, de-duped condition names.

    Drops empty parts, the ``not provided`` / ``not specified`` placeholders
    (case-insensitive), and drug-response entries (``… - Efficacy/Dosage/
    Toxicity``). De-dupes case-insensitively, keeping the first casing seen.
    """
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for part in raw.split("|"):
        condition = part.strip()
        if not condition:
            continue
        if condition.lower() in _PLACEHOLDERS:
            continue
        if _DRUG_RESPONSE.search(condition):
            continue
        key = condition.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(condition)
    return out


def format_clinvar_conditions_text(raw: str | None) -> str:
    """The cleaned conditions as a comma-joined string (empty when none remain —
    a falsy result lets the report hide the row for a placeholder/drug-response-
    only value)."""
    return ", ".join(format_clinvar_conditions(raw))
