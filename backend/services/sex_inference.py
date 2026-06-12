"""Biological sex inference from a sample's raw genotype data (Plan §9.4).

The Plan §9.4 algorithm is PAR-aware and conservative about discordant
chrX/chrY evidence:

1. **Pre-filter.** Drop every chrX call whose position falls inside PAR1
   or PAR2 — PAR sites are diploid in both XX and XY individuals and
   carry no sex signal. Both vendor parsers collapse PAR rows to chrX,
   so a PAR locus arrives here as a chrX position in one of the two
   intervals.
2. **XX evidence.** A heterozygous non-PAR chrX call supports XX only
   when chrY evidence is at or below the PAR-noise floor. If chrY rises
   above the manual-review threshold, the sample is discordant and must
   not be silently treated as ordinary XX.
3. **Candidate XY.** If at least one non-PAR chrX SNP was typed and
   every typed call is homozygous, the sample is a *candidate* XY that
   needs chrY confirmation.
4. **chrY confirmation.** Non-no-call rate strictly above
   ``_THRESHOLD_XY_CONFIRM`` (default 0.30) confirms XY. Above
   ``_THRESHOLD_PAR_NOISE`` (default 0.10) but not above the confirm
   threshold flags the sample for manual review. Anything at or below
   the PAR-noise floor falls back to ``unknown`` rather than auto-
   assigning a sex.

Thresholds were validated by the bio-validator subagent against the local
real AncestryDNA V2.0 export and the three synthetic fixtures committed
under ``tests/fixtures/sex_inference_synthetic/``; the attestation lives
at ``docs/sex_inference_threshold_validation.md`` (Step 53). No tuning
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
# (docs/sex_inference_threshold_validation.md, 2026-05-21)
# Mirrored in scripts/validate_sex_thresholds.py — keep both sides in sync.
# ---------------------------------------------------------------------------

_PAR1: tuple[int, int] = (60001, 2_699_520)
_PAR2: tuple[int, int] = (154_931_044, 155_260_560)
_THRESHOLD_XY_CONFIRM: float = 0.30
_THRESHOLD_PAR_NOISE: float = 0.10

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


def _classify(
    *,
    x_nonpar_het: int,
    x_nonpar_typed: int,
    x_nonpar_hom: int,
    y_rate: float,
) -> Classification:
    """Apply the Plan §9.4 decision tree to pre-tabulated counts.

    Order is load-bearing: non-PAR chrX heterozygosity is XX evidence only
    while chrY is at/below the PAR-noise floor. Stronger chrY evidence makes
    the X/Y signals discordant and returns ``manual_review``.
    """
    if x_nonpar_het >= 1:
        if y_rate > _THRESHOLD_PAR_NOISE:
            return "manual_review"
        return "XX"
    if x_nonpar_typed > 0 and x_nonpar_hom == x_nonpar_typed:
        if y_rate > _THRESHOLD_XY_CONFIRM:
            return "XY"
        if y_rate > _THRESHOLD_PAR_NOISE:
            return "manual_review"
    return "unknown"


@dataclass(frozen=True)
class SexSignals:
    """Raw chromosome-X/Y signals behind sex inference (also used by the
    sex-aneuploidy screen, which reads the same counts).

    ``y_rate`` is the non-no-call rate over the typed chrY probes (or 0.0 when no
    chrY probe exists).
    """

    x_nonpar_typed: int
    x_nonpar_het: int
    x_nonpar_hom: int
    y_total: int
    y_typed: int
    y_rate: float


def compute_sex_signals(sample_engine: sa.Engine) -> SexSignals:
    """Tabulate non-PAR chrX het/hom and chrY call counts from ``raw_variants``."""
    x_nonpar_typed = 0
    x_nonpar_het = 0
    x_nonpar_hom = 0
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
        y_rate=s.y_rate,
    )

    logger.info(
        "biological_sex_inferred",
        classification=classification,
        x_nonpar_het=s.x_nonpar_het,
        x_nonpar_hom=s.x_nonpar_hom,
        x_nonpar_typed=s.x_nonpar_typed,
        y_total=s.y_total,
        y_typed=s.y_typed,
        y_rate=round(s.y_rate, 4),
    )

    return classification
