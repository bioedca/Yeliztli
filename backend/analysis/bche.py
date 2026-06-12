"""BCHE (butyrylcholinesterase) succinylcholine/mivacurium apnea-risk context (SW-E6).

Butyrylcholinesterase (pseudocholinesterase) hydrolyses the depolarising muscle
relaxants **succinylcholine (suxamethonium)** and **mivacurium**. Inherited
variants that lower BChE activity prolong neuromuscular blockade — "scoline
apnea" — which at the severe end means hours of post-operative paralysis
requiring continued ventilation. Two reduced-activity variants are reliably
array-typeable (Zhu 2020, PMID 33061533):

* **Atypical / dibucaine-resistant (A) — rs1799807** (p.Asp70Gly). The major
  determinant: homozygotes have markedly reduced activity and substantially
  prolonged blockade.
* **K / Kalow — rs1803274** (p.Ala539Thr). Common (~20% MAF) and milder; a
  single K allele prolongs succinylcholine action only modestly (~2 min;
  Bretlau 2013, PMID 23400986), but it compounds the atypical allele's effect.

**Strand.** 23andMe reports the forward (plus) strand. Ensembl GRCh37 gives
rs1799807 = T/C (the atypical, deficient allele is forward **C**) and rs1803274
= C/T (the K allele is forward **T**); both map on strand +1.

**Context only — not a diagnosis.** A SNP array types only these two variants
(not the silent/fluoride alleles or rarer mutations), and from unphased
genotypes it cannot tell whether a co-occurring K allele sits in *cis* with the
atypical allele (the most-deficient common compound genotype) or in *trans*.
BChE deficiency is confirmed by an enzyme-activity / dibucaine-number assay.
This layer changes no finding and is decision support for an anaesthetist, never
a clinical determination. See :data:`backend.disclaimers.BCHE_PGX_CONTEXT_ONLY`.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from backend.analysis.pharmacogenomics import _count_alt_alleles, _fetch_sample_genotypes
from backend.disclaimers import BCHE_PGX_CONTEXT_ONLY

# Zhu 2020 (risk stratification) is primary; Bretlau 2013 (K magnitude) and the
# Nguyen 2025 review give the K-allele and clinical-implementation context.
BCHE_PMID_CITATIONS = ["33061533", "23400986", "40778538"]

# Atypical / dibucaine-resistant (A) variant — rs1799807. Forward-strand alleles.
BCHE_ATYPICAL_RSID = "rs1799807"
BCHE_ATYPICAL_REF = "T"  # usual (U) allele
BCHE_ATYPICAL_ALT = "C"  # atypical (A), reduced-activity allele

# K / Kalow variant — rs1803274. Forward-strand alleles.
BCHE_K_RSID = "rs1803274"
BCHE_K_REF = "C"  # usual allele
BCHE_K_ALT = "T"  # K (Kalow), reduced-activity allele


def bche_risk(atypical_count: int | None, k_count: int | None) -> dict[str, Any] | None:
    """Combine atypical + K allele counts into a BChE-deficiency risk category.

    ``atypical_count`` / ``k_count`` are the number of reduced-activity alleles
    at each locus (``_count_alt_alleles`` output), or ``None`` when that variant
    was not called. Returns ``None`` only when *neither* variant could be called
    (nothing to assess). The atypical (dibucaine-resistant) allele is the major
    determinant; the K allele is a milder modifier.
    """
    if atypical_count is None and k_count is None:
        return None

    k = k_count or 0
    k_known = k_count is not None
    # Whether a co-occurring K allele could be aggravating but is unphased.
    k_caveat = (
        " A co-occurring K allele (phase not determinable from array) would reduce "
        "activity further."
        if k >= 1
        else ""
    )
    coverage_note = (
        ""
        if (atypical_count is not None and k_known)
        else (
            " Note: not all BChE deficiency variants were assayed, so a low-risk "
            "result here does not exclude deficiency from an untyped variant."
        )
    )

    if atypical_count == 2:
        return {
            "risk_category": "high",
            "phenotype": "Markedly reduced BChE activity",
            "detail": (
                "Two atypical (dibucaine-resistant) alleles — markedly reduced BChE "
                "activity and a high likelihood of substantially prolonged neuromuscular "
                "blockade (hours of apnea) after succinylcholine or mivacurium." + coverage_note
            ),
        }
    if atypical_count == 1:
        return {
            "risk_category": "intermediate",
            "phenotype": "Moderately reduced BChE activity",
            "detail": (
                "One atypical (dibucaine-resistant) allele — moderately reduced BChE "
                "activity; succinylcholine/mivacurium action may be prolonged."
                + k_caveat
                + coverage_note
            ),
        }
    if atypical_count is None:
        # The major-determinant atypical locus was NOT callable.
        if k >= 1:
            # A K allele alone is only a mild modifier; with the atypical (major-
            # determinant) variant untyped, an unobserved atypical allele could place
            # this genotype in a moderate-to-severe deficiency state. Reporting "mild"
            # here would understate an unresolved genotype, so it is indeterminate.
            return {
                "risk_category": "indeterminate",
                "phenotype": "Indeterminate — major-determinant variant not assayed",
                "detail": (
                    f"{'Two K alleles were' if k == 2 else 'One K allele was'} observed, "
                    "but the atypical (dibucaine-resistant) variant — the major "
                    "determinant of BChE deficiency — was not callable. A K allele alone "
                    "is only a mild modifier, so BChE status cannot be established here: "
                    "an untyped atypical allele could indicate a moderate-to-severe "
                    "deficiency genotype that this array did not resolve."
                ),
            }
        # Only K was assayed and it was absent — cannot speak to the major determinant.
        return None
    # atypical_count == 0: the major-determinant variant was typed and is absent.
    if k >= 1:
        return {
            "risk_category": "mild",
            "phenotype": "Modestly reduced BChE activity",
            "detail": (
                f"{'Two K alleles' if k == 2 else 'One K allele'} (no atypical allele "
                "detected) — modestly reduced BChE activity; any prolongation of "
                "succinylcholine action is usually minor." + coverage_note
            ),
        }
    return {
        "risk_category": "typical",
        "phenotype": "Typical BChE activity",
        "detail": (
            "Neither the atypical nor the K reduced-activity allele was detected — "
            "typical BChE activity for these two variants." + coverage_note
        ),
    }


def _variant_call(
    *, name: str, rsid: str, protein: str, ref: str, alt: str, genotype: str | None
) -> dict[str, Any]:
    """Per-variant observed call (allele count + whether it was assayed)."""
    count = _count_alt_alleles(genotype, ref, alt) if genotype else None
    return {
        "name": name,
        "rsid": rsid,
        "protein_change": protein,
        "observed_genotype": genotype,
        "called": count is not None,
        "reduced_activity_alleles": count,
    }


def assess_bche(sample_engine: sa.Engine) -> dict[str, Any]:
    """Context-only BChE (succinylcholine/mivacurium) apnea-risk summary for a sample.

    Read-only. Looks up the two array-typeable reduced-activity variants and
    reports a combined deficiency-risk category. Emits no diagnosis and changes no
    finding — BChE deficiency is confirmed by an enzyme-activity assay.
    """
    genotypes = _fetch_sample_genotypes([BCHE_ATYPICAL_RSID, BCHE_K_RSID], sample_engine)

    atypical = _variant_call(
        name="atypical (dibucaine-resistant)",
        rsid=BCHE_ATYPICAL_RSID,
        protein="p.Asp70Gly",
        ref=BCHE_ATYPICAL_REF,
        alt=BCHE_ATYPICAL_ALT,
        genotype=genotypes.get(BCHE_ATYPICAL_RSID),
    )
    k_variant = _variant_call(
        name="K (Kalow)",
        rsid=BCHE_K_RSID,
        protein="p.Ala539Thr",
        ref=BCHE_K_REF,
        alt=BCHE_K_ALT,
        genotype=genotypes.get(BCHE_K_RSID),
    )

    risk = bche_risk(atypical["reduced_activity_alleles"], k_variant["reduced_activity_alleles"])
    any_called = atypical["called"] or k_variant["called"]

    if risk is not None:
        detail = risk["detail"]
    elif any_called:
        # The only way to reach here is K called with no reduced-activity allele while
        # the atypical (major-determinant) variant was NOT callable — so a "typical"
        # verdict would be misleading; the dominant locus is simply unknown.
        detail = (
            "Only the K variant was callable and showed no reduced-activity allele; the "
            "atypical (major-determinant) variant was not called, so BChE status was not "
            "assessed."
        )
    else:
        detail = (
            "Neither BChE reduced-activity variant could be called from this array; "
            "BChE status was not assessed."
        )

    return {
        "variants": [atypical, k_variant],
        "any_called": any_called,
        "coverage_complete": atypical["called"] and k_variant["called"],
        "risk_category": risk["risk_category"] if risk else None,
        "phenotype": risk["phenotype"] if risk else None,
        "detail": detail,
        "context_only": True,
        "note": BCHE_PGX_CONTEXT_ONLY,
        "pmid_citations": BCHE_PMID_CITATIONS,
    }
