"""Shared ClinVar clinical-significance matching for the variant-extraction
modules (cancer / carrier_status / cardiovascular).

ClinVar stores **compound** significance strings whose *primary* classification is
(Likely) Pathogenic with a secondary clinical-impact clause appended via ``|`` or
``,`` — e.g. ``"Pathogenic|drug response"``, ``"Pathogenic, low penetrance"``,
``"Likely pathogenic|risk factor"``. These are genuinely (likely-)pathogenic, but
an exact-match significance set silently dropped them, so a carrier of such a
variant in a panel gene received no finding at all (e.g. CFTR ``rs77834169``,
stored as ``"Pathogenic|drug response"`` at 3★ → a missed CF carrier; #813).

Matching the **primary** classification fixes that, while still excluding
``"Conflicting classifications of pathogenicity"`` — an aggregate of disagreeing
submissions, not a confident call (the same boundary as the frontend #799 fix).
The ClinVar ingest already splits ``/``-combined values to their first token
(``clinvar.py``), so ``"Pathogenic/Likely pathogenic"`` never reaches storage —
only ``|``/``,`` compounds do, and only those need the primary-token match here.

Single source of truth so cancer / carrier_status / cardiovascular cannot drift.
"""

from __future__ import annotations

import sqlalchemy as sa

# Primary ClinVar classifications that count as (likely-)pathogenic for clinical
# variant extraction. The secondary clause of a compound value (drug response,
# risk factor, low penetrance, …) is an *additional* clinical-impact assertion,
# not a downgrade, so the primary token alone decides membership.
PATHOGENIC_PRIMARY_CLASSIFICATIONS: tuple[str, ...] = ("Pathogenic", "Likely pathogenic")


def primary_pathogenic_classification(significance: str | None) -> str | None:
    """Return the primary pathogenic ClinVar classification, if present.

    ClinVar compounds append secondary clauses with ``|`` or ``,``. The leading
    token decides whether the row is clinically (likely-)pathogenic; slash
    compounds are intentionally not matched because ingest normalizes those
    before storage.
    """
    if not significance:
        return None
    for term in PATHOGENIC_PRIMARY_CLASSIFICATIONS:
        if (
            significance == term
            or significance.startswith(f"{term}|")
            or significance.startswith(f"{term},")
        ):
            return term
    return None


def is_pathogenic_primary(significance: str | None) -> bool:
    """Whether ``significance`` has a Pathogenic/Likely pathogenic primary token."""
    return primary_pathogenic_classification(significance) is not None


def pathogenic_significance_filter(column: sa.ColumnElement) -> sa.ColumnElement:
    """A SQLAlchemy ``.where(...)`` predicate selecting rows whose primary ClinVar
    classification is (Likely) Pathogenic — exact, or a ``|``/``,`` compound such
    as ``"Pathogenic|drug response"`` / ``"Pathogenic, low penetrance"`` (#813).

    ``"Conflicting classifications of pathogenicity"`` is excluded (it does not
    start with a pathogenic primary token; the #799 boundary), and a NULL
    significance never matches.
    """
    clauses: list[sa.ColumnElement] = []
    for term in PATHOGENIC_PRIMARY_CLASSIFICATIONS:
        clauses.append(column == term)  # plain "Pathogenic" / "Likely pathogenic"
        clauses.append(column.like(f"{term}|%"))  # "Pathogenic|drug response"
        clauses.append(column.like(f"{term},%"))  # "Pathogenic, low penetrance"
    return sa.or_(*clauses)
