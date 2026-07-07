"""HLA autoimmune-susceptibility report (Wave D / SW-D4, roadmap #36/#42).

Surfaces the well-established HLA autoimmune **susceptibility** associations from a
sample's imputed classical-HLA calls (``hla_calls``, resolved via
:mod:`backend.analysis.hla_resolver`). Every association is framed strictly as a
**susceptibility marker, not diagnostic**: these alleles are common in the general
population and most carriers never develop the disease. Each was evidence-verified
(≥2 agreeing peer-reviewed sources) before encoding; all PMIDs re-verified against
PubMed (accessed 2026-07-02).

Associations:
- **HLA-B*27 → ankylosing spondylitis / axial spondyloarthritis** (and acute
  anterior uveitis, reactive arthritis). Strongest AS genetic risk (~20% of
  heritability) but neither necessary (~25–40% of axSpA is B*27-negative) nor
  sufficient (~1–5% of carriers develop AS). Chen 2017 PMID:28259985; Rosenbaum &
  Asquith 2018 PMID:30301938; Li 2023 PMID:37679034. **B*27:06 and B*27:09 are
  disease-neutral subtypes** — carriage of only those is reported as neutral.
- **HLA-C*06:02 → psoriasis** (early-onset/type-I, guttate). The major psoriasis
  susceptibility allele. Chen & Tsai 2018 PMID:29072309; Mallon 2000 PMID:11122018.
  It is **not** a psoriatic-arthritis risk allele (neutral-to-protective for the
  joint phenotype) — the note says so.
- **HLA-DRB1 shared epitope → rheumatoid arthritis**, especially seropositive
  (ACPA/anti-CCP-positive) RA (SE-homozygote OR ~17.8 for anti-CCP-positive vs ~1.07
  NS for anti-CCP-negative). Bax 2011 PMID:21556860; Pedersen 2007 PMID:17469102.
  The SE is the QKRAA/QRRAA/RRRAA motif at DRβ1 70–74; this curated report covers
  common literature-supported risk alleles including DRB1*01:01, *04:01, *04:04,
  *04:05, *04:08, *04:10, and *10:01, but is not an exhaustive residue classifier.
  Typed DRB1 calls outside this curated set are reported as a limited screen rather
  than a negative.
- **HLA DR3-DQ2 / DR4-DQ8 → type 1 diabetes**. The highest-risk signals are
  DRB1-DQA1-DQB1 haplotypes: DR3
  (DRB1*03:01-DQA1*05:01-DQB1*02:01) and DR4
  (risk DRB1*04-DQA1*03:01-DQB1*03:02/04); the DR3/DR4 diplotype carries the
  greatest HLA risk. This report uses unphased per-locus imputed calls, so it only
  reports possible allele patterns and does not confirm DR-DQ haplotypes. Noble &
  Valdes 2011 PMID:21912932; Pugliese 1995 PMID:7789622. Risk hierarchy is
  European-derived; some DRB1*04 subtypes (e.g. *04:03) are protective despite DQ8.

Imputed, not typed — every response carries :data:`HLA_IMPUTED_CONFIRMATION_CAVEAT`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from backend.analysis.hla_drug_hypersensitivity import HLA_IMPUTED_CONFIRMATION_CAVEAT
from backend.analysis.hla_resolver import ResolvedHLACall, carries_allele

# Finding status.
STATUS_INCREASED = "increased_risk"  # a risk allele/haplotype is carried
STATUS_NOT_INCREASED = "not_increased"  # locus typed, risk allele absent
STATUS_NEUTRAL_SUBTYPE = "neutral_subtype"  # carries only a disease-neutral B*27 subtype
STATUS_LIMITED_SCREEN = "limited_screen"  # typed, but this curated screen cannot classify it
STATUS_NOT_TYPED = "not_typed"  # required locus or haplotype phase not called

# HLA-B*27 subtypes that are NOT disease-associated: a group "B*27" match would
# otherwise flag these as risk, so they are downgraded to a neutral subtype.
_B27_NEUTRAL_SUBTYPES = frozenset({"27:06", "27:09"})

# Curated RA shared-epitope/risk DRB1 alleles (2-field; non-exhaustive). The SE
# itself is motif-defined at DRβ1 70-74, but imputed reports use explicit alleles.
_SE_ALLELES = (
    "DRB1*01:01",
    "DRB1*04:01",
    "DRB1*04:04",
    "DRB1*04:05",
    "DRB1*04:08",
    "DRB1*04:10",
    "DRB1*10:01",
)
_T1D_DR4_RISK_DRB1 = (
    "DRB1*04:01",
    "DRB1*04:02",
    "DRB1*04:04",
    "DRB1*04:05",
    "DRB1*04:08",
)
_T1D_DR4_DQB1 = ("DQB1*03:02", "DQB1*03:04")


@dataclass(frozen=True)
class SusceptibilityFinding:
    """One HLA autoimmune-susceptibility assessment."""

    condition: str
    hla: str
    status: str  # STATUS_*
    carried: bool
    detail: str  # what was found, e.g. "HLA-B*27:05 (heterozygous)"
    interpretation: str
    low_confidence: bool
    citations: list[str]
    notes: list[str]


@dataclass
class SusceptibilityReport:
    """Combined HLA autoimmune-susceptibility report for a sample."""

    available: bool
    findings: list[SusceptibilityFinding] = field(default_factory=list)
    caveat: str = HLA_IMPUTED_CONFIRMATION_CAVEAT
    unavailable_note: str | None = None


_UNAVAILABLE_NOTE = (
    "No imputed HLA calls are available for this sample. HLA imputation requires an "
    "operator-installed HIBAG runtime and a per-ancestry model."
)

_NOT_DIAGNOSTIC = (
    "This is a susceptibility marker, not a diagnosis — the allele is common in the "
    "general population and most carriers never develop the condition."
)


def _locus_called(calls: Sequence[ResolvedHLACall], locus: str) -> bool:
    return any(c.locus.upper() == locus.upper() for c in calls)


def _carried(calls: Sequence[ResolvedHLACall], allele: str) -> bool:
    c = carries_allele(calls, allele)
    return c is not None and c.carried


def _any_carried(calls: Sequence[ResolvedHLACall], alleles: Sequence[str]) -> bool:
    return any(_carried(calls, allele) for allele in alleles)


def _two_field(allele: str) -> str:
    """First two colon fields of an allele (``27:05:01`` → ``27:05``)."""
    parts = [a for a in (allele or "").split(":") if a]
    return ":".join(parts[:2])


def _called_locus_two_field_alleles(calls: Sequence[ResolvedHLACall], locus: str) -> list[str]:
    """Normalize called alleles as ``LOCUS*xx:yy`` strings."""
    normalized: list[str] = []
    locus_upper = locus.upper()
    for call in calls:
        if call.locus.upper() != locus_upper:
            continue
        for allele in (call.allele1, call.allele2):
            two_field = _two_field(allele)
            if two_field:
                normalized.append(f"{locus_upper}*{two_field}")
    return normalized


def _low_conf(calls: Sequence[ResolvedHLACall], loci: Sequence[str]) -> bool:
    wanted = {loc_.upper() for loc_ in loci}
    return any(c.low_confidence for c in calls if c.locus.upper() in wanted)


def _assess_b27(calls: Sequence[ResolvedHLACall]) -> SusceptibilityFinding:
    condition = "Ankylosing spondylitis / axial spondyloarthritis"
    citations = ["PMID:28259985", "PMID:30301938", "PMID:37679034"]
    notes = [
        "HLA-B*27 also associates with acute anterior uveitis and reactive arthritis.",
        "Carrier frequency is ancestry-dependent (~6–8% in Europeans).",
    ]
    b_call = next((c for c in calls if c.locus.upper() == "B"), None)
    if b_call is None:
        return SusceptibilityFinding(
            condition,
            "HLA-B*27",
            STATUS_NOT_TYPED,
            False,
            "HLA-B not imputed",
            "HLA-B was not imputed — risk unknown; clinical HLA typing required.",
            False,
            citations,
            notes,
        )
    b27 = [a for a in (b_call.allele1, b_call.allele2) if a.split(":")[0] == "27"]
    if not b27:
        return SusceptibilityFinding(
            condition,
            "HLA-B*27",
            STATUS_NOT_INCREASED,
            False,
            "HLA-B*27 not detected",
            "HLA-B*27 was not detected — no increased ankylosing-spondylitis susceptibility "
            "from this allele.",
            b_call.low_confidence,
            citations,
            notes,
        )
    risk = [a for a in b27 if _two_field(a) not in _B27_NEUTRAL_SUBTYPES]
    if not risk:
        neutral_zyg = "homozygous" if len(b27) == 2 else "heterozygous"
        return SusceptibilityFinding(
            condition,
            "HLA-B*27",
            STATUS_NEUTRAL_SUBTYPE,
            True,
            f"HLA-B*{'/B*'.join(b27)} ({neutral_zyg})",
            "The HLA-B*27 subtype(s) detected (B*27:06 / B*27:09) are not disease-associated, "
            "so they do not confer increased ankylosing-spondylitis susceptibility.",
            b_call.low_confidence,
            citations,
            notes,
        )
    # Zygosity of the DISEASE-ASSOCIATED subtypes only: a risk + neutral compound
    # heterozygote (e.g. B*27:05 + B*27:06) carries one risk copy, not two.
    risk_zyg = "homozygous" if len(risk) == 2 else "heterozygous"
    return SusceptibilityFinding(
        condition,
        "HLA-B*27",
        STATUS_INCREASED,
        True,
        f"HLA-B*{'/B*'.join(risk)} ({risk_zyg})",
        f"HLA-B*27 is present. It is the strongest genetic risk factor for ankylosing "
        f"spondylitis, but {_NOT_DIAGNOSTIC.lower()} (~1–5% of B*27 carriers develop AS, "
        f"and ~25–40% of patients are B*27-negative).",
        b_call.low_confidence,
        citations,
        notes,
    )


def _assess_c0602(calls: Sequence[ResolvedHLACall]) -> SusceptibilityFinding:
    condition = "Psoriasis (early-onset / guttate)"
    citations = ["PMID:29072309", "PMID:11122018"]
    notes = [
        "HLA-C*06:02 is NOT a psoriatic-arthritis risk allele — it is neutral-to-"
        "protective for the joint phenotype (marks an early-onset, skin-predominant subtype).",
        "Effect size is ancestry-dependent (strongest in Europeans).",
    ]
    carriage = carries_allele(calls, "C*06:02")
    if carriage is None:
        return SusceptibilityFinding(
            condition,
            "HLA-C*06:02",
            STATUS_NOT_TYPED,
            False,
            "HLA-C not imputed",
            "HLA-C was not imputed — risk unknown; clinical HLA typing required.",
            False,
            citations,
            notes,
        )
    if not carriage.carried:
        return SusceptibilityFinding(
            condition,
            "HLA-C*06:02",
            STATUS_NOT_INCREASED,
            False,
            "HLA-C*06:02 not detected",
            "HLA-C*06:02 was not detected — no increased psoriasis susceptibility from "
            "this allele.",
            carriage.low_confidence,
            citations,
            notes,
        )
    return SusceptibilityFinding(
        condition,
        "HLA-C*06:02",
        STATUS_INCREASED,
        True,
        f"HLA-C*06:02 ({carriage.zygosity})",
        f"HLA-C*06:02 is present — the major psoriasis susceptibility allele, associated with "
        f"early-onset and guttate psoriasis. {_NOT_DIAGNOSTIC}",
        carriage.low_confidence,
        citations,
        notes,
    )


def _assess_ra_se(calls: Sequence[ResolvedHLACall]) -> SusceptibilityFinding:
    condition = "Rheumatoid arthritis (seropositive)"
    citations = [
        "PMID:21556860",
        "PMID:17469102",
        "PMID:15818663",
        "PMID:16255021",
        "PMID:23737967",
        "PMID:9135224",
        "DOI:10.3346/jkms.2007.22.6.973",
    ]
    notes = [
        "The shared-epitope association is strongest for seropositive (ACPA/anti-CCP-"
        "positive) RA and weak/absent for seronegative RA.",
        "Modern fine-mapping localizes the strongest signal to DRβ1 positions 11/13, so "
        "'shared epitope at 70–74' is a partial simplification; risk-allele identity is "
        "ancestry-dependent (most robust in European ancestry).",
        "This curated screen is not a residue-aware DRB1 classifier; typed DRB1 "
        "alleles outside the curated set are reported as limited rather than negative.",
    ]
    if not _locus_called(calls, "DRB1"):
        return SusceptibilityFinding(
            condition,
            "HLA-DRB1 shared epitope",
            STATUS_NOT_TYPED,
            False,
            "HLA-DRB1 not imputed",
            "HLA-DRB1 was not imputed — risk unknown; clinical HLA typing required.",
            False,
            citations,
            notes,
        )
    se_hits = [a for a in _SE_ALLELES if _carried(calls, a)]
    if not se_hits:
        unclassified = sorted(set(_called_locus_two_field_alleles(calls, "DRB1")))
        detail = (
            f"{', '.join(unclassified)} outside the curated shared-epitope screen"
            if unclassified
            else "HLA-DRB1 imputed but no classifiable two-field allele detected"
        )
        return SusceptibilityFinding(
            condition,
            "HLA-DRB1 shared epitope",
            STATUS_LIMITED_SCREEN,
            False,
            detail,
            "HLA-DRB1 was imputed, but no curated shared-epitope allele was detected. "
            "This non-exhaustive screen cannot classify residue-level seropositive-RA "
            "susceptibility for the detected DRB1 allele(s); do not interpret this as "
            "no increased RA susceptibility. Clinical high-resolution HLA typing or a "
            "residue-aware classifier is required for interpretation.",
            _low_conf(calls, ["DRB1"]),
            citations,
            notes,
        )
    return SusceptibilityFinding(
        condition,
        "HLA-DRB1 shared epitope",
        STATUS_INCREASED,
        True,
        ", ".join(se_hits),
        "A shared-epitope HLA-DRB1 allele is present — the major genetic risk factor for "
        f"seropositive (ACPA-positive) rheumatoid arthritis. {_NOT_DIAGNOSTIC} HLA-DRB1 "
        "genotyping has no diagnostic value in RA.",
        _low_conf(calls, ["DRB1"]),
        citations,
        notes,
    )


def _assess_t1d(calls: Sequence[ResolvedHLACall]) -> SusceptibilityFinding:
    condition = "Type 1 diabetes"
    citations = ["PMID:21912932", "PMID:7789622", "PMID:18694972"]
    notes = [
        "The DR3-DQ2 / DR4-DQ8 risk hierarchy is European-ancestry-derived; other "
        "populations have different top haplotypes.",
        "Some DRB1*04 subtypes (e.g. DRB1*04:03) are protective despite carrying DQ8.",
    ]
    required_loci = ("DRB1", "DQA1", "DQB1")
    protective = _carried(calls, "DQB1*06:02")
    protective_note = (
        " HLA-DQB1*06:02, which is strongly protective against type 1 diabetes, is also present."
        if protective
        else ""
    )
    low_conf = _low_conf(calls, required_loci)
    dr3_components = (
        _carried(calls, "DRB1*03:01")
        and _carried(calls, "DQA1*05")
        and _carried(calls, "DQB1*02:01")
    )
    dr4_components = (
        _any_carried(calls, _T1D_DR4_RISK_DRB1)
        and _carried(calls, "DQA1*03")
        and _any_carried(calls, _T1D_DR4_DQB1)
    )
    dqa_dqb_only = (_carried(calls, "DQA1*05") and _carried(calls, "DQB1*02:01")) or (
        _carried(calls, "DQA1*03") and _any_carried(calls, _T1D_DR4_DQB1)
    )
    if not all(_locus_called(calls, locus) for locus in required_loci):
        missing = "/".join(locus for locus in required_loci if not _locus_called(calls, locus))
        detail = f"HLA-{missing} not imputed"
        interp = (
            "HLA-DRB1, DQA1, and DQB1 are required to evaluate T1D DR-DQ susceptibility "
            "haplotypes; risk unknown without clinical HLA typing."
        )
        if dqa_dqb_only:
            detail = (
                "DQA1/DQB1 alleles consistent with a possible T1D-risk haplotype; DRB1 missing"
            )
            interp = (
                "DQA1/DQB1 allele carriage alone does not establish DR3-DQ2 or DR4-DQ8. "
                "HLA-DRB1 context and haplotype-aware clinical typing are required before "
                "reporting these T1D DR-DQ susceptibility haplotypes."
            )
        return SusceptibilityFinding(
            condition,
            "HLA DR3-DQ2 / DR4-DQ8",
            STATUS_NOT_TYPED,
            False,
            detail,
            interp + protective_note,
            low_conf,
            citations,
            notes,
        )
    if dr3_components or dr4_components:
        if dr3_components and dr4_components:
            detail = (
                "Possible DR3-DQ2-like and DR4-DQ8-like allele patterns; phase not established"
            )
        else:
            which = "DR3-DQ2-like" if dr3_components else "DR4-DQ8-like"
            detail = f"Possible {which} allele pattern; phase not established"
        interp = (
            "DRB1/DQA1/DQB1 alleles are consistent with a possible T1D-risk HLA background, "
            "but these imputed calls are per-locus and unphased. They do not establish the "
            "cis DR-DQ haplotype or high-risk DR3/DR4 diplotype; confirm with haplotype-aware "
            "clinical HLA typing before using this as an increased-susceptibility finding."
        )
        return SusceptibilityFinding(
            condition,
            "HLA DR3-DQ2 / DR4-DQ8",
            STATUS_NOT_TYPED,
            False,
            detail,
            interp + protective_note,
            low_conf,
            citations,
            notes,
        )
    return SusceptibilityFinding(
        condition,
        "HLA DR3-DQ2 / DR4-DQ8",
        STATUS_NOT_INCREASED,
        False,
        "No complete DRB1-DQA1-DQB1 T1D-risk allele pattern detected",
        "The unphased calls do not contain the DRB1/DQA1/DQB1 allele set required for a "
        "possible DR3-DQ2 or DR4-DQ8 T1D-risk background — lower HLA-conferred "
        "susceptibility." + protective_note,
        low_conf,
        citations,
        notes,
    )


def assess_susceptibility(calls: Sequence[ResolvedHLACall]) -> SusceptibilityReport:
    """Assess a sample's HLA calls against the curated autoimmune-susceptibility set.

    With no calls the report is ``available=False``. Otherwise each association is
    assessed as ``increased_risk`` / ``not_increased`` / ``neutral_subtype`` (B*27
    disease-neutral subtypes) / ``limited_screen`` / ``not_typed`` — always
    distinguishing a typed negative from a locus or haplotype phase that was never
    called.
    """
    if not calls:
        return SusceptibilityReport(available=False, unavailable_note=_UNAVAILABLE_NOTE)
    return SusceptibilityReport(
        available=True,
        findings=[
            _assess_b27(calls),
            _assess_c0602(calls),
            _assess_ra_se(calls),
            _assess_t1d(calls),
        ],
    )
