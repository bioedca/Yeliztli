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
  The SE is the QKRAA/QRRAA/RRRAA motif at DRβ1 70–74; the major SE alleles are
  DRB1*01:01, *04:01, *04:04, *04:05, *10:01 (non-exhaustive).
- **HLA DR3-DQ2 / DR4-DQ8 → type 1 diabetes**. DR3-DQ2 (DQA1*05:01-DQB1*02:01) and
  DR4-DQ8 (DQA1*03:01-DQB1*03:02) are the highest-risk haplotypes, with the DR3/DR4
  heterozygote at greatest risk (OR ~16.6); HLA-DQB1*06:02 is strongly protective.
  Noble & Valdes 2011 PMID:21912932; Pugliese 1995 PMID:7789622. Risk hierarchy is
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
STATUS_NOT_TYPED = "not_typed"  # required locus not called

# HLA-B*27 subtypes that are NOT disease-associated: a group "B*27" match would
# otherwise flag these as risk, so they are downgraded to a neutral subtype.
_B27_NEUTRAL_SUBTYPES = frozenset({"27:06", "27:09"})

# Major RA shared-epitope DRB1 alleles (2-field; non-exhaustive — the SE is defined
# by the QKRAA/QRRAA/RRRAA motif at DRβ1 70-74, and these are its common carriers).
_SE_ALLELES = ("DRB1*01:01", "DRB1*04:01", "DRB1*04:04", "DRB1*04:05", "DRB1*10:01")


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


def _two_field(allele: str) -> str:
    """First two colon fields of an allele (``27:05:01`` → ``27:05``)."""
    parts = [a for a in (allele or "").split(":") if a]
    return ":".join(parts[:2])


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
    citations = ["PMID:21556860", "PMID:17469102", "DOI:10.1111/tan.70442"]
    notes = [
        "The shared-epitope association is strongest for seropositive (ACPA/anti-CCP-"
        "positive) RA and weak/absent for seronegative RA.",
        "Modern fine-mapping localizes the strongest signal to DRβ1 positions 11/13, so "
        "'shared epitope at 70–74' is a partial simplification; risk-allele identity is "
        "ancestry-dependent (most robust in European ancestry).",
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
        return SusceptibilityFinding(
            condition,
            "HLA-DRB1 shared epitope",
            STATUS_NOT_INCREASED,
            False,
            "No shared-epitope DRB1 allele detected",
            "No major shared-epitope HLA-DRB1 allele was detected — no increased "
            "seropositive-RA susceptibility from the shared epitope.",
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
    if not (_locus_called(calls, "DQA1") and _locus_called(calls, "DQB1")):
        return SusceptibilityFinding(
            condition,
            "HLA DR3-DQ2 / DR4-DQ8",
            STATUS_NOT_TYPED,
            False,
            "HLA-DQA1/DQB1 not imputed",
            "HLA-DQA1/DQB1 were not both imputed — risk unknown; clinical HLA typing required.",
            False,
            citations,
            notes,
        )
    dr3_dq2 = _carried(calls, "DQA1*05") and _carried(calls, "DQB1*02:01")
    dr4_dq8 = _carried(calls, "DQA1*03") and _carried(calls, "DQB1*03:02")
    protective = _carried(calls, "DQB1*06:02")
    low_conf = _low_conf(calls, ["DQA1", "DQB1"])
    protective_note = (
        " HLA-DQB1*06:02, which is strongly protective against type 1 diabetes, is also present."
        if protective
        else ""
    )
    if dr3_dq2 and dr4_dq8:
        detail = "DR3-DQ2 + DR4-DQ8 (DR3/DR4 heterozygote)"
        interp = (
            "Both the DR3-DQ2 and DR4-DQ8 high-risk haplotypes are present — the DR3/DR4 "
            "heterozygote carries the greatest HLA type-1-diabetes susceptibility. "
            f"{_NOT_DIAGNOSTIC}"
        )
        return SusceptibilityFinding(
            condition,
            "HLA DR3-DQ2 / DR4-DQ8",
            STATUS_INCREASED,
            True,
            detail,
            interp + protective_note,
            low_conf,
            citations,
            notes,
        )
    if dr3_dq2 or dr4_dq8:
        which = "DR3-DQ2" if dr3_dq2 else "DR4-DQ8"
        return SusceptibilityFinding(
            condition,
            "HLA DR3-DQ2 / DR4-DQ8",
            STATUS_INCREASED,
            True,
            which,
            f"A high-risk type-1-diabetes HLA haplotype ({which}) is present. {_NOT_DIAGNOSTIC}"
            + protective_note,
            low_conf,
            citations,
            notes,
        )
    return SusceptibilityFinding(
        condition,
        "HLA DR3-DQ2 / DR4-DQ8",
        STATUS_NOT_INCREASED,
        False,
        "Neither DR3-DQ2 nor DR4-DQ8 detected",
        "Neither of the high-risk type-1-diabetes HLA haplotypes (DR3-DQ2, DR4-DQ8) was "
        "detected — lower HLA-conferred susceptibility." + protective_note,
        low_conf,
        citations,
        notes,
    )


def assess_susceptibility(calls: Sequence[ResolvedHLACall]) -> SusceptibilityReport:
    """Assess a sample's HLA calls against the curated autoimmune-susceptibility set.

    With no calls the report is ``available=False``. Otherwise each association is
    assessed as ``increased_risk`` / ``not_increased`` / ``neutral_subtype`` (B*27
    disease-neutral subtypes) / ``not_typed`` — always distinguishing a typed
    negative from a locus that was never called.
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
