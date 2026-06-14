"""Biological sex inference from a sample's raw genotype data (Plan §9.4).

The Plan §9.4 algorithm is PAR-aware and conservative about discordant
chrX/chrY evidence:

0. **Minimum evidence.** Sex inference is an aggregate quality-control step,
   not a single-locus Mendelian call: validated genotype-array tools score
   X-chromosome heterozygosity together with chrY missingness over many
   markers (seXY, PMID 28035028), and a lone non-PAR chrX heterozygous call
   occurs even in males as a genotyping/imputation artifact (Chen et al.,
   PMID 38073250). So a *confident* ``XX``/``XY``/``manual_review`` verdict
   requires a minimum evaluable denominator on **both** sex chromosomes —
   ``x_nonpar_typed >= MIN_X_NONPAR_TYPED`` and ``y_total >= MIN_Y_PROBES``.
   Below either floor the data is too thin to resolve sex and we return
   ``unknown`` rather than a call that would gate sex-specific findings
   (issue #363).
1. **Pre-filter.** Drop every chrX call whose position falls inside PAR1
   or PAR2 — PAR sites are diploid in both XX and XY individuals and
   carry no sex signal. Both vendor parsers collapse PAR rows to chrX,
   so a PAR locus arrives here as a chrX position in one of the two
   intervals.
2. **X dosage by het rate (issue #519).** Classify the *non-PAR chrX
   heterozygosity rate* (``x_nonpar_het / x_nonpar_typed``), not a binary
   "any het" count. A normal 46,XY male's non-PAR X is hemizygous, so his
   observed X-het rate is ≈0 — only genotyping noise (a lone non-PAR chrX
   het occurs even in males, Chen et al. PMID 38073250). A diploid-X
   individual is heterozygous at a large fraction of markers (female-level,
   tens of percent). So a rate at/below ``_THRESHOLD_X_HET_HEMIZYGOUS``
   (default 0.05) is a *candidate* XY; a rate at/above
   ``_THRESHOLD_X_HET_DIPLOID`` (default 0.15) is diploid-X; a rate in
   between is ambiguous X dosage → ``manual_review``. The denominator
   ``x_nonpar_typed`` counts every typed call — diploid homozygotes plus
   hemizygous single-allele male calls (``"A"``, the 23andMe non-PAR X
   representation; AncestryDNA pads to ``"AA"``) — so a male's near-zero het
   rate lands on the candidate-XY branch for **both** vendors (issue #504).
3. **Diploid-X disambiguation.** A diploid-X rate with chrY at/below the
   PAR-noise floor is ``XX``; with chrY above it the X and Y signals are
   discordant (the XXY pattern, issue #122) and must not be silently
   treated as ordinary XX → ``manual_review``.
4. **chrY confirmation (candidate XY).** On the hemizygous branch, a chrY
   non-no-call rate strictly above ``_THRESHOLD_XY_CONFIRM`` (default 0.30)
   confirms XY. Above ``_THRESHOLD_PAR_NOISE`` (default 0.10) but not above
   the confirm threshold flags the sample for manual review. Anything at or
   below the PAR-noise floor falls back to ``unknown`` rather than auto-
   assigning a sex.

Thresholds were validated by the bio-validator subagent against the local
real AncestryDNA V2.0 export and the three synthetic fixtures committed
under ``tests/fixtures/sex_inference_synthetic/``; the attestation lives
at ``docs/internal/sex_inference_threshold_validation.md`` (Step 53). No tuning
was required — the literature-default values land here verbatim.

This service is the single source of truth for sex inference across the
backend. ``backend/analysis/ancestry.py::assign_haplogroups`` calls it to
gate Y-tree assignment; future callers (e.g. ``services/sample_merge.py``
populating ``individuals.biological_sex``) will use it too.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
import structlog

from backend.db.tables import raw_variants

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Validated constants
# (docs/internal/sex_inference_threshold_validation.md, 2026-05-21)
# Mirrored in scripts/validate_sex_thresholds.py — keep both sides in sync.
# ---------------------------------------------------------------------------

_PAR1: tuple[int, int] = (60001, 2_699_520)
_PAR2: tuple[int, int] = (154_931_044, 155_260_560)
_THRESHOLD_XY_CONFIRM: float = 0.30
_THRESHOLD_PAR_NOISE: float = 0.10

# Non-PAR chrX heterozygosity *rate* thresholds (issue #519). A normal 46,XY
# male's non-PAR X is hemizygous, so his observed X-het rate is ≈0 — only
# genotyping/mapping noise (a few tenths of a percent; a lone non-PAR chrX het
# occurs even in males, Chen et al. PMID 38073250). A diploid-X individual (XX,
# or 47,XXY) is heterozygous at a large fraction of non-PAR chrX markers —
# female-level X-heterozygosity, tens of percent (47,XXY males carry two X's and
# are heterozygous at X markers). Validated genotype-array sex inference therefore
# thresholds on
# the X-het *rate*, not a binary "any het" count (seXY, PMID 28035028;
# Carracelas et al. 2025). The two clusters (≈0.3% vs ≈25–40%) are far apart, so
# a wide ambiguous band between these cutoffs is safe:
#   • rate ≤ _THRESHOLD_X_HET_HEMIZYGOUS → one X (male-consistent; tolerates noise)
#   • rate ≥ _THRESHOLD_X_HET_DIPLOID    → two X (XX / XXY-consistent)
#   • in between                          → ambiguous X dosage → manual_review
_THRESHOLD_X_HET_HEMIZYGOUS: float = 0.05
_THRESHOLD_X_HET_DIPLOID: float = 0.15

# Minimum evaluable sex-chromosome evidence required before a *confident*
# (XX / XY / manual_review) verdict; below either floor the sample is too thin
# to resolve and ``_classify`` returns ``unknown`` (issue #363). These are the
# shared single source of truth — ``backend/analysis/sex_aneuploidy.py`` imports
# them for the same denominators it screens on.
#
# Local calibration: real consumer arrays carry thousands of non-PAR chrX and
# hundreds of chrY probes, so these floors exclude stray single probes and
# partially-parsed inputs while passing every genuine export. The aggregate-
# evidence requirement (X-heterozygosity + chrY missingness over many markers,
# never one locus) is the published basis for genotype-array sex inference
# (seXY, PMID 28035028); a lone non-PAR chrX het is unreliable and occurs even
# in males (Chen et al., PMID 38073250).
MIN_X_NONPAR_TYPED: int = 100
MIN_Y_PROBES: int = 50

# Narrow no-call set used here; backend/analysis/zygosity.is_no_call (lands
# at Step 60) becomes the codebase-wide canonical set. These are the values
# the current parser canonicalisation and validate_sex_thresholds.py both
# accept.
_NO_CALL_VALUES: frozenset[str] = frozenset({"--", "00", "0", ""})

Classification = Literal["XX", "XY", "manual_review", "unknown"]


def _is_par(pos: int) -> bool:
    return _PAR1[0] <= pos <= _PAR1[1] or _PAR2[0] <= pos <= _PAR2[1]


def _is_no_call(genotype: str | None) -> bool:
    if genotype is None:
        return True
    return genotype.strip() in _NO_CALL_VALUES


def _is_het(genotype: str) -> bool:
    return len(genotype) == 2 and genotype[0] != genotype[1] and not _is_no_call(genotype)


def _is_hom(genotype: str) -> bool:
    return len(genotype) == 2 and genotype[0] == genotype[1] and not _is_no_call(genotype)


def _is_hemizygous(genotype: str) -> bool:
    """A single-allele non-PAR call — the hallmark of a single X copy.

    Males are hemizygous on the non-PAR X, so 23andMe reports their non-PAR chrX
    (and chrY) genotypes as a single character (``"A"``), not a padded diploid
    homozygote (``"AA"``). Such a call is typed, non-heterozygous evidence, so it
    counts toward ``x_nonpar_typed`` alongside diploid homozygotes for the §9.4
    candidate-XY test. No-call sentinels (``"0"``, ``""``) are excluded (issue
    #504)."""
    return len(genotype) == 1 and not _is_no_call(genotype)


def _classify(
    *,
    x_nonpar_het: int,
    x_nonpar_typed: int,
    x_nonpar_hom: int,
    y_total: int,
    y_rate: float,
    x_nonpar_hemizygous: int = 0,
) -> Classification:
    """Apply the Plan §9.4 decision tree to pre-tabulated counts.

    Order is load-bearing:

    - **Step 0 (minimum evidence).** A confident verdict needs an aggregate
      denominator on both sex chromosomes: ``x_nonpar_typed`` and ``y_total``
      at or above ``MIN_X_NONPAR_TYPED`` / ``MIN_Y_PROBES``. Below either floor
      the data is too thin to resolve sex (a single non-PAR chrX het is not
      evidence of two X chromosomes — it occurs even in males), so we return
      ``unknown`` rather than a call that would gate sex-specific findings
      (#363). A zero ``y_total`` also makes ``y_rate`` a vacuous 0.0, so the
      Y floor additionally guards against treating "no chrY probes" as
      "chrY absent".
    - **X dosage by het *rate*, not a binary count (#519).** The non-PAR chrX
      heterozygosity rate separates one X (≈0, genotyping noise only) from two X
      (female-level, tens of percent). A *rate* at/below
      ``_THRESHOLD_X_HET_HEMIZYGOUS`` is male-consistent and tolerates the noise
      every real male array carries; a rate at/above ``_THRESHOLD_X_HET_DIPLOID``
      is diploid-X (XX or, with chrY present, the discordant XXY case #122); a
      rate in between is ambiguous X dosage → ``manual_review``.
    - On the hemizygous (candidate-XY) branch, chrY confirms: a non-no-call rate
      above ``_THRESHOLD_XY_CONFIRM`` → ``XY``; above ``_THRESHOLD_PAR_NOISE``
      but not confirmed → ``manual_review``; at/below the PAR-noise floor →
      ``unknown`` (no chrY signal to assign on). On the diploid branch a chrY
      rate above the PAR-noise floor is discordant (``manual_review``);
      otherwise ``XX``.
    - The rate is ``x_nonpar_het / x_nonpar_typed``, and ``x_nonpar_typed``
      includes hemizygous single-allele male calls (the 23andMe representation,
      #504) alongside diploid homozygotes — so a 23andMe male's near-zero het
      rate lands on the hemizygous branch and an AncestryDNA male's lands there
      too, without needing the hom/hemizygous split. ``x_nonpar_hom`` /
      ``x_nonpar_hemizygous`` are retained for telemetry and caller compatibility
      but do not independently drive the dosage decision.
    """
    if x_nonpar_typed < MIN_X_NONPAR_TYPED or y_total < MIN_Y_PROBES:
        return "unknown"
    # x_nonpar_typed >= MIN_X_NONPAR_TYPED (>0) here, so the rate is well-defined.
    x_het_rate = x_nonpar_het / x_nonpar_typed
    if x_het_rate <= _THRESHOLD_X_HET_HEMIZYGOUS:
        # One X (male-consistent: hemizygous and/or homozygous calls + noise) —
        # confirm against chrY.
        if y_rate > _THRESHOLD_XY_CONFIRM:
            return "XY"
        if y_rate > _THRESHOLD_PAR_NOISE:
            return "manual_review"
        return "unknown"
    if x_het_rate >= _THRESHOLD_X_HET_DIPLOID:
        # Two X — discordant when chrY is also present (XXY case #122), else XX.
        if y_rate > _THRESHOLD_PAR_NOISE:
            return "manual_review"
        return "XX"
    # Ambiguous X dosage (between the hemizygous and diploid rates).
    return "manual_review"


@dataclass(frozen=True)
class SexSignals:
    """Raw chromosome-X/Y signals behind sex inference (also used by the
    sex-aneuploidy screen, which reads the same counts).

    ``y_rate`` is the non-no-call rate over the typed chrY probes (or 0.0 when no
    chrY probe exists). ``x_nonpar_typed`` is the sum of the three zygosity
    buckets — ``x_nonpar_het`` + ``x_nonpar_hom`` + ``x_nonpar_hemizygous``;
    the last counts single-allele male calls (the 23andMe representation, #504).
    """

    x_nonpar_typed: int
    x_nonpar_het: int
    x_nonpar_hom: int
    x_nonpar_hemizygous: int
    y_total: int
    y_typed: int
    y_rate: float


def compute_sex_signals(sample_engine: sa.Engine) -> SexSignals:
    """Tabulate non-PAR chrX het/hom and chrY call counts from ``raw_variants``."""
    x_nonpar_typed = 0
    x_nonpar_het = 0
    x_nonpar_hom = 0
    x_nonpar_hemizygous = 0
    y_total = 0
    y_typed = 0

    with sample_engine.connect() as conn:
        x_rows = conn.execute(
            sa.select(raw_variants.c.pos, raw_variants.c.genotype).where(
                raw_variants.c.chrom == "X"
            )
        )
        for pos, genotype in x_rows:
            if _is_par(int(pos)):
                continue
            if _is_no_call(genotype):
                continue
            if _is_het(genotype):
                x_nonpar_het += 1
                x_nonpar_typed += 1
            elif _is_hom(genotype):
                x_nonpar_hom += 1
                x_nonpar_typed += 1
            elif _is_hemizygous(genotype):
                # Single-allele male call (23andMe non-PAR chrX) — typed,
                # non-heterozygous evidence of a single X copy (issue #504).
                x_nonpar_hemizygous += 1
                x_nonpar_typed += 1

        y_rows = conn.execute(
            sa.select(raw_variants.c.genotype).where(raw_variants.c.chrom == "Y")
        )
        for (genotype,) in y_rows:
            y_total += 1
            if not _is_no_call(genotype):
                y_typed += 1

    y_rate = (y_typed / y_total) if y_total else 0.0
    return SexSignals(
        x_nonpar_typed=x_nonpar_typed,
        x_nonpar_het=x_nonpar_het,
        x_nonpar_hom=x_nonpar_hom,
        x_nonpar_hemizygous=x_nonpar_hemizygous,
        y_total=y_total,
        y_typed=y_typed,
        y_rate=y_rate,
    )


def infer_biological_sex(sample_engine: sa.Engine) -> Classification:
    """Infer biological sex from a sample's ``raw_variants`` table.

    Returns one of ``"XX"``, ``"XY"``, ``"manual_review"``, ``"unknown"``.
    """
    s = compute_sex_signals(sample_engine)
    classification = _classify(
        x_nonpar_het=s.x_nonpar_het,
        x_nonpar_typed=s.x_nonpar_typed,
        x_nonpar_hom=s.x_nonpar_hom,
        x_nonpar_hemizygous=s.x_nonpar_hemizygous,
        y_total=s.y_total,
        y_rate=s.y_rate,
    )

    logger.info(
        "biological_sex_inferred",
        classification=classification,
        x_nonpar_het=s.x_nonpar_het,
        x_nonpar_hom=s.x_nonpar_hom,
        x_nonpar_hemizygous=s.x_nonpar_hemizygous,
        x_nonpar_typed=s.x_nonpar_typed,
        y_total=s.y_total,
        y_typed=s.y_typed,
        y_rate=round(s.y_rate, 4),
    )

    return classification


# ── Recorded biological sex + resolution (issue #254) ──────────────────────

# The user-recorded individuals.biological_sex vocabulary (see
# backend/api/routes/individuals.py ``_BIOLOGICAL_SEX``) matches the confident
# inference codes, so both sides speak "XX"/"XY".
RECORDED_SEX_VALUES: frozenset[str] = frozenset({"XX", "XY"})


@dataclass(frozen=True)
class ResolvedSex:
    """A sample's biological sex resolved from recorded + inferred sources.

    ``sex`` is the value to act on (``"XX"``/``"XY"``/``"manual_review"``/
    ``"unknown"``/``None``); ``source`` is ``"recorded"`` (user-set,
    authoritative), ``"inferred"`` (array inference ran), or ``"none"`` (neither
    available). ``conflict`` is True only when a recorded value and a *confident*
    inference (``XX``/``XY``) disagree — the recorded value still wins, but
    callers can surface a discrepancy note.
    """

    sex: str | None
    source: str
    conflict: bool


def get_recorded_biological_sex(reference_engine: sa.Engine, sample_id: int) -> str | None:
    """Return the user-recorded ``individuals.biological_sex`` for a sample.

    Resolved via ``samples.individual_id`` → ``individuals.biological_sex`` in
    the reference DB. Returns ``"XX"`` / ``"XY"`` when explicitly recorded, or
    ``None`` when the sample has no linked individual, no recorded sex, or a
    value outside the recorded vocabulary.
    """
    from backend.db.tables import individuals, samples

    with reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(individuals.c.biological_sex)
            .select_from(samples.join(individuals, samples.c.individual_id == individuals.c.id))
            .where(samples.c.id == sample_id)
        ).fetchone()
    if row is None or row.biological_sex is None:
        return None
    value = str(row.biological_sex).strip().upper()
    return value if value in RECORDED_SEX_VALUES else None


def resolve_biological_sex(*, recorded_sex: str | None, inferred_sex: str | None) -> ResolvedSex:
    """Resolve biological sex, preferring an explicit recorded value (issue #254).

    Precedence: a recorded ``XX``/``XY`` is authoritative (the user set it) and
    wins even over a confident inference; otherwise fall back to the array
    inference. A recorded value that disagrees with a confident inference sets
    ``conflict`` so the caller can note the discrepancy.
    """
    recorded = recorded_sex.strip().upper() if recorded_sex else None
    if recorded in RECORDED_SEX_VALUES:
        conflict = inferred_sex in ("XX", "XY") and inferred_sex != recorded
        return ResolvedSex(sex=recorded, source="recorded", conflict=conflict)
    if inferred_sex is not None:
        return ResolvedSex(sex=inferred_sex, source="inferred", conflict=False)
    return ResolvedSex(sex=None, source="none", conflict=False)
