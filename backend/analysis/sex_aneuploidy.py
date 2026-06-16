"""Sex-chromosome aneuploidy screen (XXY / Klinefelter pattern) — roadmap #48.

Array genotypes can detect exactly one sex-chromosome aneuploidy with reasonable
confidence: **XXY** (Klinefelter). The signature is heterozygous non-PAR
chromosome-X calls (which require ≥2 X chromosomes) co-occurring with a present
chromosome Y. Everything else is out of reach from genotype calls alone and is
stated as such:

  - **45,X (Turner)** and **XYY** need DNA-quantity / probe-intensity (LRR/BAF)
    data, which a genotype-only export does not carry — so they are NOT screened
    (no false reassurance, no copy-number claims, §12.5).
  - **XXX** also shows heterozygous X without a Y; it cannot be distinguished
    from a typical XX from genotypes, so it is not asserted.

Guardrails:
  - This is a **screen, not a diagnosis** — a positive result is framed for
    confirmation by clinical karyotyping.
  - It **never overwrites** the recorded biological sex.
  - It requires a minimum number of typed non-PAR X and Y probes before judging
    either chromosome, so a single stray Y probe on an XX sample cannot produce a
    false XXY call; thin data yields ``indeterminate``.
  - Surfaced behind an opt-in gate (psychosocial weight) — see
    :mod:`backend.api.routes.sex_aneuploidy`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import sqlalchemy as sa
import structlog

from backend.db.tables import findings
from backend.services.sex_inference import (
    MIN_X_NONPAR_TYPED,
    MIN_Y_PROBES,
    THRESHOLD_X_HET_DIPLOID,
    compute_sex_signals,
)

logger = structlog.get_logger(__name__)

MODULE = "sex_aneuploidy"
CATEGORY = "aneuploidy_screen"

# Minimum typed probes before either chromosome is judged (real arrays have
# thousands of non-PAR X and hundreds of Y probes; this excludes stray single
# probes — e.g. the one spurious Y call on an otherwise-XX sample). The shared
# single source of truth lives in ``backend.services.sex_inference`` (re-exported
# above): both this screen and the sex-inference classifier judge the same
# ``compute_sex_signals`` denominators, so they gate on the same floors.
#
# "Two X" is decided on the non-PAR chrX heterozygosity *rate*, not a raw het
# count: a normal 46,XY male's X is hemizygous (rate ≈0, only genotyping noise —
# a lone non-PAR chrX het occurs even in males, Chen et al. PMID 38073250), while
# 47,XXY carries two X's and is heterozygous at a large fraction of non-PAR chrX
# markers (female-level, tens of percent; seXY PMID 28035028). A count threshold
# (e.g. ≥2) sits deep inside the male-noise range and fires on essentially every
# male export, so the screen reuses the classifier's diploid-X rate cutoff
# (``THRESHOLD_X_HET_DIPLOID``) — the same "two X" definition issue #519 adopted.
# chrY non-no-call rate above which a Y chromosome is considered present.
Y_PRESENT_RATE = 0.30

# Screen outcomes.
POSSIBLE_XXY = "possible_xxy"
NO_SIGNAL = "no_aneuploidy_signal"
INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class AneuploidyResult:
    outcome: str
    x_nonpar_typed: int
    x_nonpar_het: int
    y_total: int
    y_rate: float
    x_evaluable: bool
    y_evaluable: bool


def screen_aneuploidy(sample_engine: sa.Engine) -> AneuploidyResult:
    """Screen for an XXY (Klinefelter) genotype signature."""
    s = compute_sex_signals(sample_engine)
    x_evaluable = s.x_nonpar_typed >= MIN_X_NONPAR_TYPED
    y_evaluable = s.y_total >= MIN_Y_PROBES

    if not x_evaluable or not y_evaluable:
        outcome = INDETERMINATE
    else:
        # x_evaluable guarantees x_nonpar_typed >= MIN_X_NONPAR_TYPED (>0), so the
        # rate is well-defined.
        x_het_rate = s.x_nonpar_het / s.x_nonpar_typed
        two_x = x_het_rate >= THRESHOLD_X_HET_DIPLOID
        y_present = s.y_rate > Y_PRESENT_RATE
        outcome = POSSIBLE_XXY if (two_x and y_present) else NO_SIGNAL

    return AneuploidyResult(
        outcome=outcome,
        x_nonpar_typed=s.x_nonpar_typed,
        x_nonpar_het=s.x_nonpar_het,
        y_total=s.y_total,
        y_rate=round(s.y_rate, 4),
        x_evaluable=x_evaluable,
        y_evaluable=y_evaluable,
    )


def _finding_text(result: AneuploidyResult) -> str:
    if result.outcome == POSSIBLE_XXY:
        return (
            "Screen suggests a possible sex-chromosome aneuploidy with an XXY "
            "(Klinefelter) pattern: heterozygous X-chromosome calls (indicating two "
            "or more X chromosomes) together with a present Y chromosome. This is a "
            "SCREEN, not a diagnosis — it must be confirmed by clinical karyotyping. "
            "Many people with this genotype are healthy and unaware of it. Your "
            "recorded sex is not changed by this result."
        )
    if result.outcome == INDETERMINATE:
        return (
            "The sex-chromosome aneuploidy screen is indeterminate — too few X or Y "
            "probes were typed on this array to judge chromosome copy number. No "
            "screen result can be given, and this does not rule anything in or out."
        )
    return (
        "No XXY (Klinefelter) genotype signature was detected. Note this screen can "
        "only detect the XXY pattern from genotype data; it cannot detect Turner "
        "syndrome (45,X) or XYY, which require DNA-quantity data this array does not "
        "provide. A negative screen is not a karyotype."
    )


def store_aneuploidy_findings(result: AneuploidyResult, sample_engine: sa.Engine) -> int:
    """Persist a single screen-result finding (idempotent)."""
    row = {
        "module": MODULE,
        "category": CATEGORY,
        "evidence_level": 1,
        "finding_text": _finding_text(result),
        "conditions": f"Sex-chromosome aneuploidy screen: {result.outcome}",
        "clinvar_significance": None,
        "detail_json": json.dumps(
            {
                "outcome": result.outcome,
                "x_nonpar_typed": result.x_nonpar_typed,
                "x_nonpar_het": result.x_nonpar_het,
                "y_total": result.y_total,
                "y_rate": result.y_rate,
                "x_evaluable": result.x_evaluable,
                "y_evaluable": result.y_evaluable,
            }
        ),
    }
    with sample_engine.begin() as conn:
        conn.execute(
            sa.delete(findings).where(findings.c.module == MODULE, findings.c.category == CATEGORY)
        )
        conn.execute(sa.insert(findings), [row])
    logger.info("aneuploidy_screened", outcome=result.outcome)
    return 1
