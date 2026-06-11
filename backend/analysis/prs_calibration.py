"""Ancestry-continuous PRS calibration (SW-B2 / roadmap #5).

Replaces the placeholder ``(mean=0, sd=1)`` reference distribution — which makes a
PRS percentile look calibrated but meaningless (issue #7) — with an *expected* PRS
distribution computed from the sample's own continuous genetic ancestry.

Rather than a single per-population mean/SD, we interpolate each scored variant's
**effect-allele frequency** across super-populations by the sample's PCA admixture
fractions, then derive the PRS mean and variance analytically:

    mean      = Σ_i  w_i · 2 · p_i
    variance  = Σ_i  w_i² · 2 · p_i · (1 − p_i)          (Hardy-Weinberg)

where ``p_i`` is the ancestry-weighted frequency of variant *i*'s effect allele.
This is the "expected PRS" (ePRS) calibration (Huang 2024); adjusting both the
mean AND the variance by admixture yields a standard-Normal z-score anywhere on
the genetic-ancestry continuum (Rosenthal 2023; Ding 2023, PMID 37198491), fixing
*calibration* — not the underlying portability of the score's effect sizes.

The output ``(mean, std)`` plugs directly into
:func:`backend.analysis.prs.compute_prs_percentile`.

Requires a working PCA (the admixture fractions) — see
:mod:`backend.analysis.ancestry` (the PCA confidence fix is a prerequisite).

**Caveats.** (1) The variance assumes scored variants are independent (no LD); in
linkage it is an under-estimate, so extreme-tail percentiles are approximate.
(2) gnomAD has no dedicated Middle-Eastern / Oceanian frequency, so those
admixture components are dropped and the remaining fractions renormalised; a
sample dominated by them cannot be calibrated this way (returns ``None``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import sqlalchemy as sa

# Super-population code → gnomAD alt-allele-frequency column. CSA maps to gnomAD
# "sas"; MID/OCE have no dedicated gnomAD population (dropped + renormalised).
_POP_TO_GNOMAD_COL: dict[str, str | None] = {
    "AFR": "gnomad_af_afr",
    "AMR": "gnomad_af_amr",
    "CSA": "gnomad_af_sas",
    "EAS": "gnomad_af_eas",
    "EUR": "gnomad_af_eur",
    "MID": None,
    "OCE": None,
}

# Minimum fraction of a weight set's variants that must have a usable AF before a
# calibrated distribution is emitted (else the percentile would rest on too few SNPs).
_MIN_VARIANT_COVERAGE = 0.5

PRS_CALIBRATION_PMIDS = ["37198491"]  # Ding 2023 (ancestry continuum); ePRS Huang 2024


@dataclass
class CalibratedDistribution:
    """An ancestry-continuous reference distribution for one PRS."""

    mean: float
    std: float
    variants_used: int
    variants_total: int
    ancestry_fractions: dict[str, float]


def effect_allele_frequency(effect_allele: str, ref: str, alt: str, alt_af: float) -> float | None:
    """Frequency of the *effect* allele given the gnomAD alt-allele frequency.

    gnomAD reports the alt-allele frequency. If the PRS effect allele is the alt,
    that is the effect-allele frequency; if it is the ref, it is ``1 − alt_af``.
    Returns ``None`` when the effect allele matches neither ref nor alt
    (strand/multiallelic mismatch → exclude from the score's expectation).
    """
    ea = effect_allele.strip().upper()
    if ea == alt.strip().upper():
        return alt_af
    if ea == ref.strip().upper():
        return 1.0 - alt_af
    return None


def ancestry_weighted_af(
    per_pop_alt_af: dict[str, float | None],
    ancestry_fractions: dict[str, float],
) -> float | None:
    """Interpolate a variant's alt-allele frequency by the sample's admixture.

    ``per_pop_alt_af`` is keyed by gnomAD column name. Populations without an AF
    (missing value or no gnomAD column, e.g. MID/OCE) are dropped and the weights
    renormalised. Returns ``None`` if no weighted population has an AF.
    """
    num = 0.0
    denom = 0.0
    for pop, frac in ancestry_fractions.items():
        if frac <= 0:
            continue
        col = _POP_TO_GNOMAD_COL.get(pop)
        af = per_pop_alt_af.get(col) if col else None
        if af is not None:
            num += frac * af
            denom += frac
    return (num / denom) if denom > 0 else None


def expected_prs_mean_sd(
    variants: list[dict],
    ancestry_fractions: dict[str, float],
) -> tuple[float, float, int]:
    """Analytic PRS mean + SD under HWE for the sample's ancestry.

    Each variant dict needs ``effect_allele``, ``ref``, ``alt``, ``weight``, and
    ``per_pop_alt_af`` ({gnomAD col: af}). Returns ``(mean, std, n_used)``;
    variants with no usable AF or an unmatched effect allele are skipped.
    """
    mean = 0.0
    variance = 0.0
    n_used = 0
    for v in variants:
        alt_af = ancestry_weighted_af(v["per_pop_alt_af"], ancestry_fractions)
        if alt_af is None:
            continue
        p = effect_allele_frequency(v["effect_allele"], v["ref"], v["alt"], alt_af)
        if p is None:
            continue
        w = v["weight"]
        mean += w * 2.0 * p
        variance += (w**2) * 2.0 * p * (1.0 - p)
        n_used += 1
    return mean, math.sqrt(variance), n_used


def get_ancestry_fractions(sample_engine: sa.Engine) -> dict[str, float] | None:
    """The sample's continuous admixture fractions from its ancestry finding.

    Returns ``None`` when ancestry could not be confidently/ admixedly assessed
    (e.g. low-coverage → UNCERTAIN, no stored finding) — in which case the PRS
    must stay *uncalibrated* rather than be percentile'd against a wrong ancestry.
    """
    from backend.analysis.ancestry import _get_latest_ancestry_finding

    _top, detail = _get_latest_ancestry_finding(sample_engine)
    if not detail:
        return None
    fracs = detail.get("nnls_fractions") or detail.get("admixture_fractions")
    if not fracs:
        return None
    total = sum(f for f in fracs.values() if f and f > 0)
    if total <= 0:
        return None
    return {pop: f / total for pop, f in fracs.items() if f and f > 0}


def continuous_reference_distribution(
    weights: list[dict],
    sample_engine: sa.Engine,
    reference_engine: sa.Engine | None = None,
) -> CalibratedDistribution | None:
    """Build an ancestry-continuous reference distribution for a PRS weight set.

    ``weights`` is a list of ``{rsid, effect_allele, weight}``. Per-variant ref/alt
    and per-population gnomAD AFs are read from the sample's ``annotated_variants``.
    Returns ``None`` if ancestry is unknown or too few variants have a usable AF.
    """
    from backend.db.tables import annotated_variants

    fractions = get_ancestry_fractions(sample_engine)
    if not fractions:
        return None

    by_rsid = {w["rsid"]: w for w in weights if w.get("rsid")}
    if not by_rsid:
        return None

    af_cols = [c for c in _POP_TO_GNOMAD_COL.values() if c]
    rows_by_rsid: dict[str, sa.Row] = {}
    rsids = list(by_rsid)
    with sample_engine.connect() as conn:
        for i in range(0, len(rsids), 500):
            batch = rsids[i : i + 500]
            stmt = sa.select(
                annotated_variants.c.rsid,
                annotated_variants.c.ref,
                annotated_variants.c.alt,
                *[getattr(annotated_variants.c, c) for c in af_cols],
            ).where(annotated_variants.c.rsid.in_(batch))
            for r in conn.execute(stmt):
                rows_by_rsid[r.rsid] = r

    variants: list[dict] = []
    for rsid, w in by_rsid.items():
        r = rows_by_rsid.get(rsid)
        if r is None or r.ref is None or r.alt is None:
            continue
        variants.append(
            {
                "effect_allele": w["effect_allele"],
                "ref": r.ref,
                "alt": r.alt,
                "weight": w["weight"],
                "per_pop_alt_af": {c: getattr(r, c) for c in af_cols},
            }
        )

    mean, std, n_used = expected_prs_mean_sd(variants, fractions)
    if std <= 0 or n_used < max(1, int(_MIN_VARIANT_COVERAGE * len(by_rsid))):
        return None
    return CalibratedDistribution(
        mean=round(mean, 6),
        std=round(std, 6),
        variants_used=n_used,
        variants_total=len(by_rsid),
        ancestry_fractions=fractions,
    )
