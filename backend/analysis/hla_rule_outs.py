"""HLA high-NPV rule-out reports — celiac + narcolepsy (Wave D / SW-D3, roadmap #19).

Two classical high-negative-predictive-value HLA rule-outs, computed from a
sample's imputed classical-HLA calls (``hla_calls``, resolved via
:mod:`backend.analysis.hla_resolver`). Both are framed as **rule-OUT** tools: the
*absence* of the permissive alleles makes the disease very unlikely, while their
*presence* is common in the general population and **non-diagnostic** (most
carriers never develop the disease).

**Celiac disease** is HLA-DQ-restricted. Near-complete rule-out requires the panel
to capture **all** the celiac-permissive DQ heterodimers, not just DQ2.5/DQ8:
~1–6% of celiac patients are DQ2.5/DQ8-negative but carry **DQ2.2** or a DQB1*02 /
DQB1*03:02 half-heterodimer (Rouvroye 2019 PMID:31066583, which reports up to
99.7% NPV when DQ2.5, DQ2.2 **and** DQ8 are all captured). Because HIBAG imputes
full DQA1 **and** DQB1 genotypes, this module
composes the heterodimers directly — conservatively (any-allele presence across the
locus pair, so a *trans* pairing is not missed), which is the safe direction for a
rule-out (it withholds the rule-out more readily, never over-calls it). Absence of
DQ2.5, DQ8, DQ2.2 **and** the DQB1*02 / DQB1*03:02 β-chains → celiac very unlikely.

  - DQ2.5 = DQA1*05 + DQB1*02:01 · DQ8 = DQA1*03 + DQB1*03:02 · DQ2.2 = DQA1*02:01 + DQB1*02:02
    (molecular definitions verified across Megiorni 2012 DOI:10.1186/1423-0127-19-88,
    Brown 2019 PMID:31274511; the DQ8 β-chain effect is population-variable.)

**Narcolepsy type 1** (with cataplexy) is very strongly associated with
**HLA-DQB1*06:02** (pooled OR ~24; Capittini 2018 PMID:30321823). >92–98% of NT1
patients carry it, so *absence* argues **strongly against** NT1 — but **not** a
rule-out: ~2% of genuine NT1 patients are DQB1*06:02-negative (Han 2012
DOI:10.1111/j.1399-0039.2012.01948.x). Its *presence* is non-diagnostic:
DQB1*06:02 is common (~8–30%, ancestry-dependent; ~24% in Europeans) and only
~1/1000 carriers develop narcolepsy (Mignot 1997 PMID:9456467).

Imputed, not typed — every response carries :data:`HLA_IMPUTED_CONFIRMATION_CAVEAT`.
Evidence accessed 2026-07-02.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from backend.analysis.hla_drug_hypersensitivity import HLA_IMPUTED_CONFIRMATION_CAVEAT
from backend.analysis.hla_resolver import ResolvedHLACall, carries_allele

# Celiac status.
CELIAC_RULE_OUT = "rule_out"  # no permissive DQ heterodimer/β-chain → very unlikely
CELIAC_PERMISSIVE = "permissive_present"  # a permissive haplotype present → non-diagnostic
CELIAC_NOT_TYPED = "not_typed"  # DQA1 or DQB1 not called → cannot assess

# Narcolepsy status.
NARCO_ABSENT = "absent_lowers"  # DQB1*06:02 absent → strongly lowers NT1 (not a rule-out)
NARCO_PRESENT = "present"  # DQB1*06:02 present → non-diagnostic (common)
NARCO_NOT_TYPED = "not_typed"  # DQB1 not called


def _locus_called(calls: Sequence[ResolvedHLACall], locus: str) -> bool:
    return any(c.locus.upper() == locus.upper() for c in calls)


def _carried(calls: Sequence[ResolvedHLACall], allele: str) -> bool:
    c = carries_allele(calls, allele)
    return c is not None and c.carried


def _any_low_confidence(calls: Sequence[ResolvedHLACall], loci: Sequence[str]) -> bool:
    wanted = {loc_.upper() for loc_ in loci}
    return any(c.low_confidence for c in calls if c.locus.upper() in wanted)


@dataclass(frozen=True)
class CeliacRuleOut:
    """Celiac-disease HLA-DQ rule-out assessment."""

    status: str  # CELIAC_*
    detected: list[str]  # permissive heterodimers/β-chains found (empty on rule_out)
    low_confidence: bool
    interpretation: str


@dataclass(frozen=True)
class NarcolepsyRuleOut:
    """Narcolepsy type 1 HLA-DQB1*06:02 rule-out assessment."""

    status: str  # NARCO_*
    carried: bool
    zygosity: str | None
    low_confidence: bool
    interpretation: str


@dataclass
class RuleOutReport:
    """Combined celiac + narcolepsy HLA rule-out report for a sample."""

    available: bool  # any HLA calls present
    celiac: CeliacRuleOut | None = None
    narcolepsy: NarcolepsyRuleOut | None = None
    caveat: str = HLA_IMPUTED_CONFIRMATION_CAVEAT
    unavailable_note: str | None = None
    citations: list[str] = field(default_factory=list)


_CELIAC_CITATIONS = ["PMID:31274511", "PMID:31066583", "DOI:10.1186/1423-0127-19-88"]
# Capittini 2018 meta-analysis, Mignot 1997 (carrier frequency), and Han 2012
# (>98% of hypocretin-deficient cases carry DQB1*06:02 — the ~2%-negative caveat).
_NARCO_CITATIONS = ["PMID:30321823", "PMID:9456467", "DOI:10.1111/j.1399-0039.2012.01948.x"]

_UNAVAILABLE_NOTE = (
    "No imputed HLA calls are available for this sample. HLA imputation requires an "
    "operator-installed HIBAG runtime and a per-ancestry model; the celiac DQ2/DQ8 "
    "single-tag proxy in the Allergy module still applies until it is run."
)


def _assess_celiac(calls: Sequence[ResolvedHLACall]) -> CeliacRuleOut:
    # Both DQ loci must be called to compose the heterodimers.
    if not (_locus_called(calls, "DQA1") and _locus_called(calls, "DQB1")):
        return CeliacRuleOut(
            status=CELIAC_NOT_TYPED,
            detected=[],
            low_confidence=False,
            interpretation=(
                "HLA-DQA1/DQB1 were not both imputed for this sample, so the celiac "
                "HLA rule-out cannot be assessed. Clinical HLA-DQ typing is required."
            ),
        )

    detected: list[str] = []
    # Full at-risk heterodimers (conservative: any DQA1 + any DQB1 across the locus
    # pair, so a trans pairing is caught).
    if _carried(calls, "DQA1*05") and _carried(calls, "DQB1*02:01"):
        detected.append("DQ2.5 (DQA1*05 + DQB1*02:01)")
    if _carried(calls, "DQA1*03") and _carried(calls, "DQB1*03:02"):
        detected.append("DQ8 (DQA1*03 + DQB1*03:02)")
    if _carried(calls, "DQA1*02:01") and _carried(calls, "DQB1*02:02"):
        detected.append("DQ2.2 (DQA1*02:01 + DQB1*02:02)")
    # β-chain half-heterodimers (residual risk when no full at-risk pairing is seen).
    if not detected:
        if _carried(calls, "DQB1*02"):
            detected.append("DQB1*02 half-heterodimer")
        if _carried(calls, "DQB1*03:02"):
            detected.append("DQB1*03:02 half-heterodimer")

    low_conf = _any_low_confidence(calls, ["DQA1", "DQB1"])
    if detected:
        return CeliacRuleOut(
            status=CELIAC_PERMISSIVE,
            detected=detected,
            low_confidence=low_conf,
            interpretation=(
                "A celiac-permissive HLA-DQ haplotype is present "
                f"({'; '.join(detected)}). This does NOT diagnose celiac disease — "
                "these haplotypes are common in the general population and most "
                "carriers never develop celiac. Celiac cannot be excluded on HLA; "
                "diagnosis needs serology and/or biopsy."
            ),
        )
    return CeliacRuleOut(
        status=CELIAC_RULE_OUT,
        detected=[],
        low_confidence=low_conf,
        interpretation=(
            "None of the celiac-permissive HLA-DQ heterodimers (DQ2.5, DQ8, DQ2.2) or "
            "their DQB1*02 / DQB1*03:02 β-chains were detected. Celiac disease is very "
            "unlikely (the HLA rule-out has a high negative predictive value). Confirm "
            "with clinical HLA-DQ typing before relying on this to exclude celiac."
        ),
    )


def _assess_narcolepsy(calls: Sequence[ResolvedHLACall]) -> NarcolepsyRuleOut:
    carriage = carries_allele(calls, "DQB1*06:02")
    if carriage is None:
        return NarcolepsyRuleOut(
            status=NARCO_NOT_TYPED,
            carried=False,
            zygosity=None,
            low_confidence=False,
            interpretation=(
                "HLA-DQB1 was not imputed for this sample, so the narcolepsy "
                "DQB1*06:02 assessment cannot be made. Clinical HLA typing is required."
            ),
        )
    if carriage.carried:
        return NarcolepsyRuleOut(
            status=NARCO_PRESENT,
            carried=True,
            zygosity=carriage.zygosity,
            low_confidence=carriage.low_confidence,
            interpretation=(
                "HLA-DQB1*06:02 is present. This is NOT diagnostic of narcolepsy — "
                "DQB1*06:02 is common in the general population (~8–30%, ancestry-"
                "dependent) and only about 1 in 1000 carriers develops narcolepsy. "
                "Narcolepsy is a clinical diagnosis (cataplexy, sleep studies, CSF "
                "hypocretin)."
            ),
        )
    return NarcolepsyRuleOut(
        status=NARCO_ABSENT,
        carried=False,
        zygosity=None,
        low_confidence=carriage.low_confidence,
        interpretation=(
            "HLA-DQB1*06:02 was not detected. Because >92% of narcolepsy type 1 (with "
            "cataplexy) patients carry it, its absence argues strongly against "
            "narcolepsy type 1 — but does not fully exclude it (~2% of genuine cases "
            "are DQB1*06:02-negative). Confirm with clinical HLA typing if narcolepsy "
            "is suspected."
        ),
    )


def assess_rule_outs(calls: Sequence[ResolvedHLACall]) -> RuleOutReport:
    """Assess a sample's HLA calls for the celiac + narcolepsy high-NPV rule-outs.

    With no calls the report is ``available=False``. Otherwise each rule-out is
    assessed, distinguishing ``not_typed`` (the required locus was not called) from
    a genuine negative — so an absent rule-out is never a false reassurance.
    """
    if not calls:
        return RuleOutReport(available=False, unavailable_note=_UNAVAILABLE_NOTE)
    return RuleOutReport(
        available=True,
        celiac=_assess_celiac(calls),
        narcolepsy=_assess_narcolepsy(calls),
        citations=_CELIAC_CITATIONS + _NARCO_CITATIONS,
    )
