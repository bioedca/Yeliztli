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

from backend.analysis.allele_match import AMBIGUOUS_MAF_HIGH, AMBIGUOUS_MAF_LOW
from backend.analysis.zygosity import COMPLEMENT

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


def _single_base(allele: str | None) -> str | None:
    if not allele:
        return None
    allele_u = allele.strip().upper()
    if len(allele_u) != 1 or allele_u not in COMPLEMENT:
        return None
    return allele_u


def _frequency_for_reference_allele(
    allele: str, ref: str, alt: str, alt_af: float
) -> float | None:
    if allele == alt:
        return alt_af
    if allele == ref:
        return 1.0 - alt_af
    return None


def effect_allele_frequency(
    effect_allele: str,
    ref: str,
    alt: str,
    alt_af: float,
    other_allele: str | None = None,
) -> float | None:
    """Frequency of the *effect* allele given the gnomAD alt-allele frequency.

    gnomAD reports the alt-allele frequency. If the PRS effect allele is the alt,
    that is the effect-allele frequency; if it is the ref, it is ``1 − alt_af``.
    When a weight provides ``other_allele``, resolve the effect/other pair against
    ``{ref, alt}`` and its Watson-Crick complement, mirroring PRS scoring. Returns
    ``None`` when the allele pair is unresolved, multiallelic, or a strand-
    ambiguous palindrome in the same near-half frequency band used by scoring.
    """
    ea = _single_base(effect_allele)
    ref_u = _single_base(ref)
    alt_u = _single_base(alt)
    if ea is None or ref_u is None or alt_u is None:
        return None

    has_other_allele = bool(other_allele and other_allele.strip())
    oa = _single_base(other_allele)
    if oa is None:
        if has_other_allele:
            return None
        return _frequency_for_reference_allele(ea, ref_u, alt_u, alt_af)

    if oa == COMPLEMENT[ea]:
        if AMBIGUOUS_MAF_LOW <= alt_af <= AMBIGUOUS_MAF_HIGH:
            return None
        return _frequency_for_reference_allele(ea, ref_u, alt_u, alt_af)

    ref_pair = {ref_u, alt_u}
    if {ea, oa} == ref_pair:
        return _frequency_for_reference_allele(ea, ref_u, alt_u, alt_af)

    complemented_effect = COMPLEMENT[ea]
    complemented_other = COMPLEMENT[oa]
    if {complemented_effect, complemented_other} == ref_pair:
        return _frequency_for_reference_allele(complemented_effect, ref_u, alt_u, alt_af)

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
    ``per_pop_alt_af`` ({gnomAD col: af}); ``other_allele`` is optional and
    enables the same strand-aware allele-pair harmonization used by scoring.
    Returns ``(mean, std, n_used)``; variants with no usable AF or an unmatched
    effect allele are skipped.
    """
    mean = 0.0
    variance = 0.0
    n_used = 0
    for v in variants:
        alt_af = ancestry_weighted_af(v["per_pop_alt_af"], ancestry_fractions)
        if alt_af is None:
            continue
        p = effect_allele_frequency(
            v["effect_allele"],
            v["ref"],
            v["alt"],
            alt_af,
            v.get("other_allele"),
        )
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

    ``weights`` is a list of ``{rsid, effect_allele, weight, other_allele?}``.
    Per-variant ref/alt and per-population gnomAD AFs are read from the sample's
    ``annotated_variants``. Returns ``None`` if ancestry is unknown or too few
    variants have a usable AF.
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
                "other_allele": w.get("other_allele"),
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
