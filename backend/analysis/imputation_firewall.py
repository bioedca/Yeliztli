"""Hard MAF/r² imputation firewall (SW-C3 / roadmap #3).

Wave C safety gate. The local Beagle runtime (SW-C2,
:mod:`backend.analysis.imputation_runner`) imputes a sample's typed genotypes up
to the 1000G Phase 3 v5a panel (SW-C1) and records each imputed marker's **DR2**
(dosage r², 0-1). Imputation is *not* uniformly reliable: against 1000G Phase 3,
accuracy is high for common variants but **degrades sharply for rare variants**
(MAF < 1%) and for ancestries under-represented in 1000 Genomes — exactly the
regime where a single false imputed allele would be most damaging if it drove a
**pathogenic / likely-pathogenic / carrier / monogenic** call. This module is the
firewall that quarantines those unreliable imputed variants so they can never
back such a high-stakes finding.

**Policy.** An *imputed* marker (Beagle ``IMP`` flag) may back a P/LP/carrier/
monogenic call only when it is **both well-imputed and common**:

* ``DR2 >= WELL_IMPUTED_DR2`` (0.8 — a deliberately conservative well-imputed
  dosage-r² cutoff; see below), **and**
* ``MAF >= RARE_MAF_THRESHOLD`` (1%), where ``MAF = min(AF, 1 - AF)``.

Anything else is **quarantined** with a machine-readable reason (low DR2, missing
DR2, imputed-rare, or missing AF). A **genotyped** (directly typed, non-imputed)
marker is *not* subject to the firewall — it is an observed call, not an
imputation — so it always passes (reason ``genotyped``). The firewall is additive
and one-directional: it can only *withhold* an imputed variant from high-stakes
calls; it never upgrades, relabels, or fabricates a finding.

**Why these thresholds (evidence-verified 2026-06-26 — ≥2 agreeing peer-reviewed
sources).** *Rarity gate:* against 1000G Phase 3, imputation accuracy (dosage r²)
collapses for rare variants — dosage r² falls from ~0.95 at common frequencies to
well under 0.5 at MAF < 0.1%, and the rarest variants are essentially un-imputable
(Zheng 2012, PMID:23089364; Huang 2015, DOI:10.1371/journal.pone.0116487; Liu
2012, DOI:10.1002/gepi.21603), reinforced by the panel-level rare/under-represented-
ancestry caveat already cited in :mod:`backend.annotation.imputation_panel`. Rare
variants imported into clinical interpretation have very low positive predictive
value and must be validated rather than acted on (Weedon 2021, PMID:33589468 — by
analogy from direct array genotyping). MAF < 1% is the standard population-genetics
definition of a *rare* variant. *Quality gate:* DR2 >= 0.8 is **not** a universal
field standard — the looser GWAS convention is r²/info ≈ 0.3–0.5 and rigorous
practice favours MAF-dependent thresholds (Naj 2019, DOI:10.1002/cphg.84); 0.8 is
chosen here as a deliberately stringent, conservative cutoff for the clinical use
case. Crucially, Beagle's *estimated* DR2 over-states quality at low MAF (winner's
curse), so DR2 >= 0.8 is **necessary but not sufficient** for a rare variant —
which is exactly why rare imputed variants are additionally quarantined regardless
of DR2. (accessed 2026-06-26)

This module decides; it does **not** itself read the per-sample DB or surface
findings (a following Wave C slice wires the imputed variants into the annotation
pipeline behind this gate). It is consumed today by ``scripts/run_imputation.py``,
which reports how many imputed markers clear the firewall.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from backend.analysis.imputation_runner import WELL_IMPUTED_DR2, ImputedVariant

# Minor-allele-frequency floor below which an *imputed* variant is "rare" and is
# quarantined regardless of its DR2: 1000G Phase 3 imputation accuracy degrades
# sharply below this frequency (see module docstring). MAF < 1% is the standard
# definition of a rare variant. Boundary: MAF == RARE_MAF_THRESHOLD is *common*
# (reportable); only MAF strictly below it is quarantined.
RARE_MAF_THRESHOLD = 0.01


class FirewallReason(StrEnum):
    """Why the firewall reached its verdict (machine-readable, stable strings)."""

    GENOTYPED = "genotyped"  # directly typed marker — firewall N/A, always passes
    IMPUTED_PASS = "imputed_pass"  # imputed AND well-imputed AND common → passes
    MISSING_DR2 = "missing_dr2"  # imputed but no usable DR2 → cannot vouch quality
    LOW_DR2 = "low_dr2"  # imputed, DR2 < WELL_IMPUTED_DR2
    MISSING_AF = "missing_af"  # imputed, DR2 ok, but no usable AF → cannot vouch rarity
    IMPUTED_RARE = "imputed_rare"  # imputed, DR2 ok, but MAF < RARE_MAF_THRESHOLD


# Reasons that permit a variant to back a P/LP/carrier/monogenic call.
_REPORTABLE_REASONS = frozenset({FirewallReason.GENOTYPED, FirewallReason.IMPUTED_PASS})


@dataclass(frozen=True)
class FirewallDecision:
    """Verdict for one variant: may it back a high-stakes (P/LP/carrier/monogenic) call?"""

    reportable: bool
    reason: FirewallReason


def minor_allele_frequency(af: float | None) -> float | None:
    """Fold an ALT allele frequency to its minor-allele frequency, ``min(af, 1-af)``.

    Returns ``None`` when ``af`` is ``None`` (the runner already drops non-finite /
    out-of-range AF to ``None``, so any value reaching here is a real AF in [0, 1]).
    """
    if af is None:
        return None
    return min(af, 1.0 - af)


def assess_variant(variant: ImputedVariant) -> FirewallDecision:
    """Decide whether ``variant`` may back a P/LP/carrier/monogenic call.

    Genotyped (non-imputed) markers always pass. An imputed marker passes only
    when it is both well-imputed (``DR2 >= WELL_IMPUTED_DR2``) and common
    (``MAF >= RARE_MAF_THRESHOLD``); otherwise it is quarantined and the first
    failing gate is reported as the reason, in this precedence: quality (missing
    DR2 → low DR2) before frequency (missing AF → imputed-rare).
    """
    if not variant.imputed:
        return FirewallDecision(reportable=True, reason=FirewallReason.GENOTYPED)

    # Quality gate first: DR2 is the direct imputation-quality metric.
    if variant.dr2 is None:
        return FirewallDecision(reportable=False, reason=FirewallReason.MISSING_DR2)
    if variant.dr2 < WELL_IMPUTED_DR2:
        return FirewallDecision(reportable=False, reason=FirewallReason.LOW_DR2)

    # Frequency gate: imputed rare variants are quarantined even at high DR2.
    maf = minor_allele_frequency(variant.af)
    if maf is None:
        return FirewallDecision(reportable=False, reason=FirewallReason.MISSING_AF)
    if maf < RARE_MAF_THRESHOLD:
        return FirewallDecision(reportable=False, reason=FirewallReason.IMPUTED_RARE)

    return FirewallDecision(reportable=True, reason=FirewallReason.IMPUTED_PASS)


@dataclass
class FirewallSummary:
    """Aggregate firewall outcome over a set of *imputed* markers.

    Genotyped markers are excluded from these counts (the firewall does not apply
    to them); they are already reflected in the runner's DR2 summary.
    """

    n_imputed: int = 0  # imputed markers assessed
    n_reportable: int = 0  # imputed AND cleared the firewall
    n_quarantined: int = 0  # imputed AND withheld from high-stakes calls
    quarantine_reasons: dict[str, int] = field(default_factory=dict)  # reason → count

    @property
    def frac_reportable(self) -> float | None:
        """Fraction of imputed markers that clear the firewall (None if none imputed)."""
        if self.n_imputed == 0:
            return None
        return self.n_reportable / self.n_imputed


def summarize_firewall(variants: Iterable[ImputedVariant]) -> FirewallSummary:
    """Apply :func:`assess_variant` across ``variants`` and tally imputed outcomes."""
    summary = FirewallSummary()
    for v in variants:
        if not v.imputed:
            continue  # firewall N/A for directly typed markers
        summary.n_imputed += 1
        decision = assess_variant(v)
        if decision.reportable:
            summary.n_reportable += 1
        else:
            summary.n_quarantined += 1
            key = decision.reason.value
            summary.quarantine_reasons[key] = summary.quarantine_reasons.get(key, 0) + 1
    return summary
