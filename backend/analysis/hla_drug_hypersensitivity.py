"""HLA drug-hypersensitivity report (Wave D / SW-D2, roadmap #18).

Turns a sample's imputed classical-HLA calls (persisted in ``hla_calls``, resolved
by :mod:`backend.analysis.hla_resolver`) into per-drug hypersensitivity-risk
assessments for the well-established HLA pharmacogenetic contraindications. Every
association here is a CPIC Level-A or guideline-grade HLA–drug pairing, evidence-
verified (≥2 agreeing peer-reviewed sources) before encoding; each carries its
citation.

**Imputed, not typed.** These calls are statistically imputed from SNP genotypes,
so the report is a *screening lead*, not a clinical HLA typing result: it attaches
:data:`HLA_IMPUTED_CONFIRMATION_CAVEAT` to every response and frames a positive as
"confirm with clinical high-resolution HLA typing before withholding a drug", a
negative as "does not replace confirmatory typing before prescribing". Absence of a
call at a locus is reported as **not typed** (unknown), never a false "no risk".

**Ancestry.** Some risk alleles are ancestry-enriched (e.g. HLA-B*15:02 is common in
Han Chinese / Thai / Malay and rare in Europeans/Japanese, where HLA-B*15:11 or
HLA-A*31:01 are the relevant carbamazepine risk alleles); that scoping note travels
with the assessment.

Evidence (accessed 2026-07-02):
- HLA-B*57:01 / abacavir hypersensitivity — CPIC, PMID:24561393 (DOI:10.1038/clpt.2014.38).
- HLA-B*15:02 / carbamazepine & oxcarbazepine SJS-TEN — CPIC 2017, PMID:29392710
  (DOI:10.1002/cpt.1004); discovery Chung 2004 PMID:15057820.
- HLA-B*15:11 / carbamazepine SCAR — Kaniwa 2010 PMID:21204807
  (DOI:10.1111/j.1528-1167.2010.02766.x); Wong 2021 PMID:34553372
  (DOI:10.1111/ijd.15792); meta-analysis Biswas 2022 PMID:35599240
  (DOI:10.1111/cts.13291); DPWG 2024 PMID:38570725
  (DOI:10.1038/s41431-024-01572-4).
- HLA-B*15:02 / phenytoin & fosphenytoin SJS-TEN — CPIC 2020, PMID:32779747
  (DOI:10.1002/cpt.2008); meta-analysis Phung 2021 PMID:34816768.
- HLA-A*31:01 / carbamazepine DRESS/MPE/SJS-TEN — CPIC 2017, PMID:29392710.
- HLA-B*58:01 / allopurinol severe cutaneous adverse reactions — CPIC, PMID:23232549
  (DOI:10.1038/clpt.2012.209).
- HLA-B*13:01 / dapsone hypersensitivity syndrome — Zhang 2013 PMID:24152261
  (DOI:10.1056/NEJMoa1213096); prospective screening Liu 2019 PMID:30916737.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from backend.analysis.hla_resolver import ResolvedHLACall, carries_allele

# Standing caveat for any clinical claim built on an imputed HLA call. Imputed HLA
# is not a substitute for a clinical high-resolution HLA typing lab result, and is
# never valid for transplant/donor matching (that guard is enforced in SW-D5).
HLA_IMPUTED_CONFIRMATION_CAVEAT = (
    "HLA alleles here are statistically imputed from SNP genotypes, not directly "
    "typed. Confirm with clinical high-resolution HLA typing before acting on any "
    "result; never use imputed HLA for transplant or donor matching."
)

# Per-assessment status.
STATUS_AT_RISK = "at_risk"  # a risk allele is carried with usable confidence
STATUS_LOW_CONFIDENCE = "low_confidence"  # imputed call exists but is not reliable
STATUS_NO_RISK_ALLELE = "no_risk_allele"  # locus typed, risk allele absent
STATUS_NOT_TYPED = "not_typed"  # no call at this locus (unknown, not a negative)


@dataclass(frozen=True)
class DrugHLARisk:
    """A curated HLA allele → drug-hypersensitivity contraindication."""

    query: str  # locus-qualified resolver query, e.g. "B*57:01"
    display_allele: str  # human label, e.g. "HLA-B*57:01"
    drugs: tuple[str, ...]
    reaction: str
    positive_recommendation: str  # action when the allele is carried
    guideline: str  # e.g. "CPIC"
    citations: tuple[str, ...]
    ancestry_note: str | None = None
    extra_notes: tuple[str, ...] = ()


# Curated, evidence-verified associations (see the module docstring for sources).
_DRUG_HLA_RISKS: tuple[DrugHLARisk, ...] = (
    DrugHLARisk(
        query="B*57:01",
        display_allele="HLA-B*57:01",
        drugs=("abacavir",),
        reaction="abacavir hypersensitivity reaction",
        positive_recommendation=(
            "CPIC: do not prescribe abacavir to HLA-B*57:01-positive patients "
            "(confirm with clinical HLA-B*57:01 typing first)."
        ),
        guideline="CPIC",
        citations=("PMID:24561393",),
    ),
    DrugHLARisk(
        query="B*15:02",
        display_allele="HLA-B*15:02",
        drugs=("carbamazepine", "oxcarbazepine", "phenytoin", "fosphenytoin"),
        reaction="Stevens-Johnson syndrome / toxic epidermal necrolysis (SJS/TEN)",
        positive_recommendation=(
            "CPIC: avoid carbamazepine and oxcarbazepine in carbamazepine-naive "
            "HLA-B*15:02-positive patients; if phenytoin-naive, do not use "
            "phenytoin/fosphenytoin unless benefits clearly outweigh SJS/TEN risk."
        ),
        guideline="CPIC",
        citations=("PMID:29392710", "PMID:15057820", "PMID:32779747", "PMID:34816768"),
        ancestry_note=(
            "HLA-B*15:02 is common in Han Chinese, Thai and Malay/South-East-Asian "
            "ancestries and rare in Europeans and Japanese — where HLA-B*15:11 "
            "(Japanese) or HLA-A*31:01 are the relevant carbamazepine risk alleles, "
            "so a negative here does not exclude risk in those populations."
        ),
        extra_notes=(
            "CPIC phenytoin/fosphenytoin guidance applies before first use; patients "
            "with more than three months of continuous tolerated exposure have lower "
            "future hypersensitivity risk.",
            "Other aromatic anticonvulsants, including eslicarbazepine, lamotrigine "
            "and phenobarbital, have weaker HLA-B*15:02 SJS/TEN evidence; choose "
            "alternatives with caution.",
        ),
    ),
    DrugHLARisk(
        query="B*15:11",
        display_allele="HLA-B*15:11",
        drugs=("carbamazepine",),
        reaction="carbamazepine severe cutaneous adverse reactions (SJS/TEN, DRESS, MPE)",
        positive_recommendation=(
            "DPWG: choose an alternative anti-epileptic drug instead of carbamazepine "
            "in HLA-B*15:11-positive patients when possible; if no alternative is "
            "possible, start only after careful benefit-risk review and counsel the "
            "patient to report any rash immediately."
        ),
        guideline="DPWG / PharmGKB",
        citations=("PMID:21204807", "PMID:34553372", "PMID:35599240", "PMID:38570725"),
        ancestry_note=(
            "HLA-B*15:11 carbamazepine SCAR evidence is strongest in Japanese, Korean "
            "and Chinese cohorts, and in Asian HLA-B75 contexts; HLA-B*15:02 "
            "screening alone does not cover this risk."
        ),
        extra_notes=(
            "This row is limited to carbamazepine SCAR evidence; oxcarbazepine, "
            "phenytoin and fosphenytoin guidance is not inferred for HLA-B*15:11.",
        ),
    ),
    DrugHLARisk(
        query="A*31:01",
        display_allele="HLA-A*31:01",
        drugs=("carbamazepine",),
        reaction=("carbamazepine hypersensitivity (DRESS, maculopapular exanthema, SJS/TEN)"),
        positive_recommendation=(
            "CPIC: consider an alternative to carbamazepine in HLA-A*31:01-positive "
            "patients (weigh against the clinical need)."
        ),
        guideline="CPIC",
        citations=("PMID:29392710",),
    ),
    DrugHLARisk(
        query="B*58:01",
        display_allele="HLA-B*58:01",
        drugs=("allopurinol",),
        reaction="allopurinol severe cutaneous adverse reactions (SJS/TEN, DRESS)",
        positive_recommendation=(
            "CPIC: use an alternative urate-lowering therapy (e.g. febuxostat) rather "
            "than allopurinol in HLA-B*58:01-positive patients."
        ),
        guideline="CPIC",
        citations=("PMID:23232549",),
        ancestry_note=("HLA-B*58:01 carriage is higher in East/South-East Asian ancestries."),
    ),
    DrugHLARisk(
        query="B*13:01",
        display_allele="HLA-B*13:01",
        drugs=("dapsone",),
        reaction="dapsone hypersensitivity syndrome (DRESS, SJS/TEN, drug-induced liver injury)",
        positive_recommendation=(
            "Increased risk of dapsone hypersensitivity syndrome; consider an "
            "alternative or close monitoring, per dapsone HLA-B guidance."
        ),
        guideline="Zhang 2013 / Liu 2019",
        citations=("PMID:24152261", "PMID:30916737"),
        ancestry_note=(
            "HLA-B*13:01 is common in East and South Asian ancestries and near-absent "
            "in Europeans and Africans; its clinical utility is established there."
        ),
        extra_notes=(
            "Positive predictive value is low (~8%) — most carriers tolerate dapsone; "
            "the value is in the high negative predictive value in Asian ancestry.",
        ),
    ),
)


@dataclass(frozen=True)
class DrugRiskAssessment:
    """The sample's assessment against one HLA drug-hypersensitivity association."""

    allele: str  # display, e.g. "HLA-B*57:01"
    drugs: list[str]
    reaction: str
    status: str  # STATUS_*
    carried: bool
    zygosity: str | None
    copies: int
    prob: float | None
    low_confidence: bool
    recommendation: str  # positive recommendation (when at risk) or reassurance
    guideline: str
    citations: list[str]
    notes: list[str]


@dataclass
class DrugHypersensitivityReport:
    """Full HLA drug-hypersensitivity report for a sample."""

    available: bool  # any HLA calls present for this sample
    any_at_risk: bool
    assessments: list[DrugRiskAssessment] = field(default_factory=list)
    caveat: str = HLA_IMPUTED_CONFIRMATION_CAVEAT
    unavailable_note: str | None = None


_UNAVAILABLE_NOTE = (
    "No imputed HLA calls are available for this sample. HLA imputation requires an "
    "operator-installed HIBAG runtime and a per-ancestry model; until it is run, the "
    "single-tag HLA proxy in the Allergy module still covers HLA-B*57:01 (abacavir)."
)


def _assess_one(risk: DrugHLARisk, calls: Sequence[ResolvedHLACall]) -> DrugRiskAssessment:
    carriage = carries_allele(calls, risk.query)
    notes: list[str] = []
    if risk.ancestry_note:
        notes.append(risk.ancestry_note)
    notes.extend(risk.extra_notes)

    if carriage is None:
        return DrugRiskAssessment(
            allele=risk.display_allele,
            drugs=list(risk.drugs),
            reaction=risk.reaction,
            status=STATUS_NOT_TYPED,
            carried=False,
            zygosity=None,
            copies=0,
            prob=None,
            low_confidence=False,
            recommendation=(
                f"{risk.display_allele} was not imputed for this sample — risk for "
                f"{', '.join(risk.drugs)} is unknown; clinical HLA typing is required."
            ),
            guideline=risk.guideline,
            citations=list(risk.citations),
            notes=notes,
        )

    if carriage.low_confidence:
        prob = f" (posterior probability {carriage.prob:.2f})" if carriage.prob is not None else ""
        recommendation = (
            f"{risk.display_allele} has a low-confidence imputed call{prob}. Do not "
            f"interpret this as positive or negative for {', '.join(risk.drugs)} "
            f"hypersensitivity risk; clinical high-resolution HLA typing is required "
            f"before using this result."
        )
        status = STATUS_LOW_CONFIDENCE
    elif carriage.carried:
        recommendation = risk.positive_recommendation
        status = STATUS_AT_RISK
    else:
        status = STATUS_NO_RISK_ALLELE
        recommendation = (
            f"{risk.display_allele} not detected — no increased {', '.join(risk.drugs)} "
            f"hypersensitivity risk from this allele. Imputation does not replace "
            f"confirmatory HLA typing before prescribing."
        )
    return DrugRiskAssessment(
        allele=risk.display_allele,
        drugs=list(risk.drugs),
        reaction=risk.reaction,
        status=status,
        carried=carriage.carried,
        zygosity=carriage.zygosity,
        copies=carriage.copies,
        prob=carriage.prob,
        low_confidence=carriage.low_confidence,
        recommendation=recommendation,
        guideline=risk.guideline,
        citations=list(risk.citations),
        notes=notes,
    )


def assess_drug_hypersensitivity(
    calls: Sequence[ResolvedHLACall],
) -> DrugHypersensitivityReport:
    """Assess a sample's HLA calls against the curated drug-hypersensitivity set.

    With no calls (HIBAG never run for this sample) the report is ``available=
    False`` with a note — never a list of false negatives. Otherwise every
    association is assessed as ``at_risk`` (allele carried with usable confidence),
    ``low_confidence`` (imputed call exists but is not interpretable as positive or
    negative), ``no_risk_allele`` (locus typed, allele absent), or ``not_typed``
    (locus not called).
    """
    if not calls:
        return DrugHypersensitivityReport(
            available=False, any_at_risk=False, unavailable_note=_UNAVAILABLE_NOTE
        )
    assessments = [_assess_one(risk, calls) for risk in _DRUG_HLA_RISKS]
    any_at_risk = any(a.status == STATUS_AT_RISK for a in assessments)
    return DrugHypersensitivityReport(
        available=True, any_at_risk=any_at_risk, assessments=assessments
    )
