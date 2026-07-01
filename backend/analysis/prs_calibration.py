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
admixture components are dropped and the remaining fractions renormalised; when
less than half the sample's ancestry maps to represented gnomAD populations, the
sample cannot be calibrated this way (returns ``None``).
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
# Minimum fraction of normalized sample ancestry represented by available gnomAD
# population AF columns. MID/OCE currently have no dedicated column; if they
# dominate, the calibration would otherwise collapse to the minority represented
# ancestry and look falsely calibrated.
_MIN_ANCESTRY_COVERAGE = 0.5
_POSITION_BATCH_SIZE = 500

PRS_CALIBRATION_PMIDS = [
    "37198491",  # Ding 2023 (ancestry continuum); ePRS Huang 2024
    "38374346",  # Lennon 2024 (eMERGE genetic-ancestry mean/variance calibration)
]


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


def _norm_chrom(chrom: str | None) -> str | None:
    """Normalize a chromosome label for positional matching (strip ``chr``)."""
    if chrom is None:
        return None
    c = str(chrom).strip()
    if c[:3].lower() == "chr":
        c = c[3:]
    return c.upper()


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


def represented_ancestry_fraction(ancestry_fractions: dict[str, float]) -> float:
    """Fraction of sample ancestry covered by available gnomAD AF columns."""
    return sum(
        frac
        for pop, frac in ancestry_fractions.items()
        if frac > 0 and _POP_TO_GNOMAD_COL.get(pop)
    )


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


def _variant_entry(
    ref: str, alt: str, per_pop_alt_af: dict[str, float | None], weight: dict
) -> dict:
    """Shape a calibration variant entry from a weight + its ref/alt + per-pop AF."""
    return {
        "effect_allele": weight["effect_allele"],
        "other_allele": weight.get("other_allele"),
        "ref": ref,
        "alt": alt,
        "weight": weight["weight"],
        "per_pop_alt_af": per_pop_alt_af,
    }


def _pair_orients(effect_allele: str, other_allele: str | None, ref: str, alt: str) -> bool:
    """Whether a weight can be oriented against ``{ref, alt}`` to pick its record at
    a multi-allelic locus. The per-sample near-0.5 palindrome drop is applied later
    by :func:`effect_allele_frequency`, not here.

    With ``other_allele`` the ``{effect, other}`` pair must match ``{ref, alt}`` on
    the reference or complemented strand (same harmonization as scoring). Without it
    (legacy weights), only a **same-strand** literal match counts — mirroring
    ``effect_allele_frequency``'s no-strand-attempt path, so any record this accepts
    also yields a frequency there (a complement-only match would pass the
    resolve-count check yet be dropped from the moments, re-opening the #1236 bias).
    """
    ea = _single_base(effect_allele)
    ref_u = _single_base(ref)
    alt_u = _single_base(alt)
    if ea is None or ref_u is None or alt_u is None:
        return False
    pair = {ref_u, alt_u}
    oa = _single_base(other_allele)
    if oa is None:
        return ea in pair
    return {ea, oa} == pair or {COMPLEMENT[ea], COMPLEMENT[oa]} == pair


def _resolve_imputed_against_reference(
    weights: list[dict],
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
    af_cols: list[str],
) -> list[dict]:
    """Build calibration entries for scored variants absent from annotated_variants.

    These are the imputed-only contributions SW-C5 adds to the raw score (#1236).
    Their ref/alt come from the sample's ``imputed_variants``; their per-population
    gnomAD AF from the reference DB (``lookup_gnomad_by_positions``). A variant
    whose ref/alt or gnomAD AF can't be sourced is left out — it then sits in the
    same coverage-tolerance bucket as before, never standardized over wrong moments.
    """
    from backend.annotation.gnomad import lookup_gnomad_by_positions
    from backend.db.tables import imputed_variants

    # (normalized chrom, pos) → weights at that locus.
    by_locus: dict[tuple[str, int], list[dict]] = {}
    for w in weights:
        chrom = _norm_chrom(w.get("chrom"))
        pos = w.get("pos")
        if chrom is not None and pos is not None:
            by_locus.setdefault((chrom, pos), []).append(w)
    if not by_locus:
        return []

    # ref/alt candidates per locus from the sample's imputed_variants. A position
    # can carry more than one alt, so collect the full set and match each weight to
    # the right one below — never an arbitrary last-write-wins choice.
    ref_alts: dict[tuple[str, int], set[tuple[str, str]]] = {}
    pos_values = sorted({pos for (_chrom, pos) in by_locus})
    with sample_engine.connect() as conn:
        if not sa.inspect(conn).has_table(imputed_variants.name):
            return []
        for i in range(0, len(pos_values), _POSITION_BATCH_SIZE):
            batch = pos_values[i : i + _POSITION_BATCH_SIZE]
            stmt = sa.select(
                imputed_variants.c.chrom,
                imputed_variants.c.pos,
                imputed_variants.c.ref,
                imputed_variants.c.alt,
            ).where(imputed_variants.c.pos.in_(batch))
            for r in conn.execute(stmt):
                key = (_norm_chrom(r.chrom), r.pos)
                if key in by_locus and r.ref and r.alt:
                    ref_alts.setdefault(key, set()).add((r.ref, r.alt))
    if not ref_alts:
        return []

    # Per-pop gnomAD AF by (chrom, pos, ref, alt). chrom is already normalized to
    # the gnomad_af store form (strip "chr", upper) so the exact-tuple match lands.
    positions = [
        (chrom, pos, ref, alt)
        for (chrom, pos), alleles in ref_alts.items()
        for (ref, alt) in alleles
    ]
    annotations = lookup_gnomad_by_positions(positions, reference_engine)

    entries: list[dict] = []
    for (chrom, pos), weights_at_locus in by_locus.items():
        # gnomAD-annotated (ref, alt, per_pop) candidates at this locus.
        candidates: list[tuple[str, str, dict[str, float | None]]] = []
        for ref, alt in ref_alts.get((chrom, pos), set()):
            ann = annotations.get((chrom, pos, ref, alt))
            if ann is None:
                continue
            # af_cols are gnomad_af_<pop>; the annotation exposes them as af_<pop>.
            per_pop = {col: getattr(ann, col.removeprefix("gnomad_"), None) for col in af_cols}
            candidates.append((ref, alt, per_pop))
        for w in weights_at_locus:
            # Pick the unique candidate whose allele pair orients this weight (on
            # either strand). 0 or >1 → leave the weight unresolved; the caller then
            # withholds rather than calibrate over a set missing a scored variant.
            matches = [
                c
                for c in candidates
                if _pair_orients(w["effect_allele"], w.get("other_allele"), c[0], c[1])
            ]
            if len(matches) == 1:
                ref, alt, per_pop = matches[0]
                entries.append(_variant_entry(ref, alt, per_pop, w))
    return entries


def continuous_reference_distribution(
    weights: list[dict],
    sample_engine: sa.Engine,
    reference_engine: sa.Engine | None = None,
) -> CalibratedDistribution | None:
    """Build an ancestry-continuous reference distribution for a PRS weight set.

    ``weights`` is a list of ``{rsid, effect_allele, weight, other_allele?}``
    or ``{chrom, pos, effect_allele, weight, other_allele?}``. Per-variant
    ref/alt and per-population gnomAD AFs are read from the sample's
    ``annotated_variants``. Returns ``None`` if ancestry is unknown or too few
    variants have a usable AF.

    When ``reference_engine`` is supplied, scored variants that are **absent from
    ``annotated_variants``** — the imputed-only contributions SW-C5 adds to the
    raw score (#1236) — are resolved against the gnomAD reference: their ref/alt
    come from the sample's ``imputed_variants`` and their per-population gnomAD AF
    from the reference DB. This keeps the expected distribution over the *same*
    typed+imputed set the raw score used; without it, imputed contributions would
    inflate the raw score but be missing from the moments, biasing the z-score.
    """
    from backend.db.tables import annotated_variants

    fractions = get_ancestry_fractions(sample_engine)
    if not fractions:
        return None
    if represented_ancestry_fraction(fractions) < _MIN_ANCESTRY_COVERAGE:
        return None

    by_rsid = {w["rsid"]: w for w in weights if w.get("rsid")}
    by_pos = {(_norm_chrom(w.get("chrom")), w.get("pos")): w for w in weights if not w.get("rsid")}
    if not by_rsid and not by_pos:
        return None

    af_cols = [c for c in _POP_TO_GNOMAD_COL.values() if c]
    rows_by_rsid: dict[str, sa.Row] = {}
    rows_by_pos: dict[tuple[str | None, int], sa.Row] = {}
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
        if by_pos:
            pos_values = sorted({pos for (_chrom, pos) in by_pos if pos is not None})
            chrom_values = {chrom for (chrom, _pos) in by_pos if chrom}
            chrom_candidates = chrom_values | {f"CHR{chrom}" for chrom in chrom_values}
            for i in range(0, len(pos_values), _POSITION_BATCH_SIZE):
                batch = pos_values[i : i + _POSITION_BATCH_SIZE]
                stmt = sa.select(
                    annotated_variants.c.chrom,
                    annotated_variants.c.pos,
                    annotated_variants.c.ref,
                    annotated_variants.c.alt,
                    *[getattr(annotated_variants.c, c) for c in af_cols],
                ).where(annotated_variants.c.pos.in_(batch))
                if chrom_candidates:
                    stmt = stmt.where(
                        sa.func.upper(annotated_variants.c.chrom).in_(chrom_candidates)
                    )
                for r in conn.execution_options(stream_results=True).execute(stmt):
                    key = (_norm_chrom(r.chrom), r.pos)
                    if key in by_pos:
                        rows_by_pos[key] = r

    variants: list[dict] = []
    unresolved: list[dict] = []

    def _append_from_annotated(row: sa.Row | None, weight: dict) -> None:
        # Scored variants missing from annotated_variants (imputed-only) are held
        # for reference-DB resolution rather than silently dropped (#1236).
        if row is None or row.ref is None or row.alt is None:
            unresolved.append(weight)
            return
        variants.append(
            _variant_entry(row.ref, row.alt, {c: getattr(row, c) for c in af_cols}, weight)
        )

    for rsid, w in by_rsid.items():
        _append_from_annotated(rows_by_rsid.get(rsid), w)
    for key, w in by_pos.items():
        _append_from_annotated(rows_by_pos.get(key), w)

    if reference_engine is not None and unresolved:
        resolved = _resolve_imputed_against_reference(
            unresolved, sample_engine, reference_engine, af_cols
        )
        # Every scored variant absent from annotated_variants must be calibrated
        # against the reference; the helper emits exactly one entry per resolvable
        # weight, so a shortfall means a scored imputed contribution could not be
        # resolved (no imputed ref/alt, no gnomAD row, or an unorientable/ambiguous
        # pair). Withhold rather than standardize the raw score — which includes it
        # — over moments that omit it (#1236); the typed-only coverage gate could
        # otherwise still pass on a mixed set and re-introduce the bias.
        if len(resolved) != len(unresolved):
            return None
        variants.extend(resolved)

    mean, std, n_used = expected_prs_mean_sd(variants, fractions)
    variants_total = len(by_rsid) + len(by_pos)
    if std <= 0 or n_used < max(1, int(_MIN_VARIANT_COVERAGE * variants_total)):
        return None
    return CalibratedDistribution(
        mean=round(mean, 6),
        std=round(std, 6),
        variants_used=n_used,
        variants_total=variants_total,
        ancestry_fractions=fractions,
    )
