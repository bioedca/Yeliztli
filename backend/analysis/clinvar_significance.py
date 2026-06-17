"""Shared ClinVar clinical-significance matching for the variant-extraction
modules (cancer / carrier_status / cardiovascular).

ClinVar stores **compound** significance strings whose *primary* classification is
(Likely) Pathogenic with a secondary clinical-impact clause appended via ``|`` or
``,`` — e.g. ``"Pathogenic|drug response"``, ``"Likely pathogenic|risk factor"``.
These are genuinely (likely-)pathogenic, but an exact-match significance set
silently dropped them, so a carrier of such a variant in a panel gene received no
finding at all (e.g. CFTR ``rs77834169``, stored as ``"Pathogenic|drug response"``
at 3★ → a missed CF carrier; #813). Matching the **primary** classification fixes
that, while still excluding ``"Conflicting classifications of pathogenicity"`` — an
aggregate of disagreeing submissions, not a confident call (the same boundary as
the frontend #799 fix).

**Low-penetrance / risk-allele modifiers are the exception (#987/#1027).**
ClinVar/ClinGen treat ``"Pathogenic, low penetrance"`` /
``"Likely pathogenic, low penetrance"`` and the risk-allele classifications
(``"Established risk allele"`` etc.) as a *distinct* category from ordinary
high-penetrance Mendelian P/LP, requiring their own classification/reporting
considerations (ClinGen Low Penetrance & Risk Allele Working Group, Schmidt
et al., online 2023 / Genet Med 2024, PMID 38054408; the ACMG/AMP five-tier is
for Mendelian disorders, Richards et al. 2015, PMID 25741868). So a
``low penetrance`` / ``risk allele`` modifier is NOT just an extra
clinical-impact clause — it must NOT be promoted into the ordinary
``clinvar_pathogenic`` / high-evidence path, else a
decreased-penetrance assertion reads as a standard high-penetrance P/LP result.

ClinVar aggregate values can use ``/`` to join ACMG/ClinGen germline terms (e.g.
``"Pathogenic/Established risk allele"``). Ingest preserves slash compounds when
one term carries a lower-penetrance / risk-allele modifier so this classifier can
route them to the distinct tier instead of erasing the modifier (#1027).

Single source of truth so cancer / carrier_status / cardiovascular cannot drift.
"""

from __future__ import annotations

import sqlalchemy as sa

# Primary ClinVar classifications that count as (likely-)pathogenic for clinical
# variant extraction. The secondary clause of a compound value (drug response,
# risk factor, …) is an *additional* clinical-impact assertion, not a downgrade, so
# the primary token alone decides membership — EXCEPT for the low-penetrance /
# risk-allele modifiers below, which ARE a downgrade out of this ordinary path.
PATHOGENIC_PRIMARY_CLASSIFICATIONS: tuple[str, ...] = ("Pathogenic", "Likely pathogenic")

# Secondary-clause substrings that route a compound OUT of the ordinary
# high-penetrance Mendelian P/LP path (#987): ClinGen low-penetrance and risk-allele
# assertions are a distinct classification category. ``"risk allele"`` matches the
# ``Established`` / ``Likely`` / ``Uncertain risk allele`` terms; ``"risk factor"``
# (a separate clinical-impact clause) is deliberately NOT included.
_NON_MENDELIAN_MODIFIERS: tuple[str, ...] = ("low penetrance", "risk allele")

# Storage category used when these lower-penetrance/risk-allele assertions are
# surfaced as findings rather than promoted to ordinary high-penetrance P/LP.
LOWER_PENETRANCE_RISK_ALLELE_CATEGORY = "clinvar_low_penetrance_or_risk_allele"
LOWER_PENETRANCE_RISK_ALLELE_PMIDS: tuple[str, ...] = ("38054408",)


def _has_non_mendelian_modifier(significance: str) -> bool:
    """Whether a low-penetrance / risk-allele modifier downgrades this value (#987)."""
    lowered = significance.lower()
    return any(modifier in lowered for modifier in _NON_MENDELIAN_MODIFIERS)


def is_low_penetrance_or_risk_allele(significance: str | None) -> bool:
    """Whether ``significance`` is a ClinGen lower-penetrance / risk-allele term.

    This is the public Python counterpart to
    :func:`low_penetrance_or_risk_allele_filter`, used anywhere the value needs to
    be surfaced distinctly rather than treated as ordinary high-penetrance P/LP.
    """
    if not significance:
        return False
    return _has_non_mendelian_modifier(significance)


def primary_pathogenic_classification(significance: str | None) -> str | None:
    """Return the primary pathogenic ClinVar classification, if present.

    ClinVar compounds append secondary clauses with ``|`` or ``,``. The leading
    token decides whether the row is clinically (likely-)pathogenic; slash
    compounds are intentionally not matched because ingest normalizes those
    before storage. A ``low penetrance`` / ``risk allele`` modifier is NOT counted
    as ordinary primary-pathogenic (#987) — it is a distinct lower-penetrance /
    risk-allele assertion, returned ``None`` so it isn't promoted into the
    high-evidence ``clinvar_pathogenic`` path.
    """
    if not significance:
        return None
    for term in PATHOGENIC_PRIMARY_CLASSIFICATIONS:
        if (
            significance == term
            or significance.startswith(f"{term}|")
            or significance.startswith(f"{term},")
        ):
            if _has_non_mendelian_modifier(significance):
                return None
            return term
    return None


def is_pathogenic_primary(significance: str | None) -> bool:
    """Whether ``significance`` has a Pathogenic/Likely pathogenic primary token."""
    return primary_pathogenic_classification(significance) is not None


def low_penetrance_or_risk_allele_filter(column: sa.ColumnElement) -> sa.ColumnElement:
    """A SQLAlchemy predicate selecting lower-penetrance/risk-allele assertions.

    Mirrors :func:`is_low_penetrance_or_risk_allele` for database queries. It
    deliberately matches ``risk allele`` but not ClinVar's separate ``risk factor``
    clinical-impact clause.
    """
    return sa.and_(
        column.isnot(None),
        sa.or_(*(column.ilike(f"%{m}%") for m in _NON_MENDELIAN_MODIFIERS)),
    )


def pathogenic_significance_filter(column: sa.ColumnElement) -> sa.ColumnElement:
    """A SQLAlchemy ``.where(...)`` predicate selecting rows whose primary ClinVar
    classification is (Likely) Pathogenic — exact, or a ``|``/``,`` compound such
    as ``"Pathogenic|drug response"`` (#813).

    Excluded: ``"Conflicting classifications of pathogenicity"`` (does not start with
    a pathogenic primary token; the #799 boundary); a NULL significance; and
    ``low penetrance`` / ``risk allele`` compounds, which are a distinct lower-
    penetrance / risk-allele category, not ordinary high-penetrance P/LP (#987).
    """
    primary: list[sa.ColumnElement] = []
    for term in PATHOGENIC_PRIMARY_CLASSIFICATIONS:
        primary.append(column == term)  # plain "Pathogenic" / "Likely pathogenic"
        primary.append(column.like(f"{term}|%"))  # "Pathogenic|drug response"
        primary.append(column.like(f"{term},%"))  # "Pathogenic, <clause>"
    # AND-exclude the low-penetrance / risk-allele modifiers (#987), mirroring
    # _has_non_mendelian_modifier so the SQL and Python predicates agree.
    not_downgraded = [sa.not_(column.ilike(f"%{m}%")) for m in _NON_MENDELIAN_MODIFIERS]
    return sa.and_(sa.or_(*primary), *not_downgraded)
