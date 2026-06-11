"""VKORC1 + CYP4F2 warfarin-dosing pharmacogenomic context (SW-E1 warfarin layer / roadmap #13).

Warfarin dose requirement is driven mainly by two genes this module reads from
array data — *VKORC1* (the drug target) and *CYP4F2* (vitamin-K metabolism) —
acting together with *CYP2C9* (covered by the star-allele engine in
:mod:`backend.analysis.pharmacogenomics`). Per the CPIC 2017 guideline update
(Johnson 2017, PMID 28198005):

* **VKORC1 c.-1639G>A (rs9923231).** The promoter A allele lowers VKORC1
  expression → **increased warfarin sensitivity and lower dose requirement**
  (G/G typical → G/A intermediate → A/A most sensitive).
* **CYP4F2 *3 (V433M, rs2108622).** The *3 (T) allele modestly **raises** the
  dose requirement in individuals of European or Asian ancestry (C/C none →
  C/T modest → T/T larger), with no established effect in African ancestry.

**Strand.** 23andMe reports the forward (plus) strand. VKORC1 sits on the minus
strand, so the gene's reference "G" is forward **C** and the dose-lowering "A" is
forward **T**; CYP4F2's *1 reference is forward **C** and *3 is forward **T**
(verified against Ensembl GRCh37 REST). Genotypes are interpreted on that basis.

**Context only — never a dose.** This layer is interpretive background that
NEVER changes any finding's evidence level or ClinVar significance and never
emits a milligram dose. CPIC dosing requires a *validated pharmacogenetic
algorithm* (VKORC1 + CYP2C9 + clinical factors) plus INR monitoring; surfacing
the single-gene direction of effect is decision support for a clinician, not a
prescription. See :data:`backend.disclaimers.WARFARIN_PGX_CONTEXT_ONLY`.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from backend.analysis.pharmacogenomics import _count_alt_alleles, _fetch_sample_genotypes
from backend.disclaimers import WARFARIN_PGX_CONTEXT_ONLY

# CPIC 2017 Pharmacogenetics-Guided Warfarin Dosing update (Johnson 2017) is the
# authoritative source for both gene effects below.
WARFARIN_CPIC_PMID = "28198005"

# VKORC1 c.-1639G>A — rs9923231. Forward-strand alleles (23andMe convention).
VKORC1_RSID = "rs9923231"
VKORC1_REF = "C"  # forward strand → gene "G" (typical sensitivity)
VKORC1_ALT = "T"  # forward strand → gene "A" (increased sensitivity → lower dose)

# CYP4F2 *3 (V433M) — rs2108622. Forward-strand alleles.
CYP4F2_RSID = "rs2108622"
CYP4F2_REF = "C"  # *1 (reference)
CYP4F2_ALT = "T"  # *3 (modestly higher dose requirement)


def vkorc1_phenotype(alt_count: int | None) -> dict[str, str] | None:
    """Map the VKORC1 c.-1639G>A A-allele count to a sensitivity phenotype.

    ``alt_count`` is the number of forward-strand ``T`` (gene ``A``) alleles, i.e.
    the output of :func:`_count_alt_alleles`. Returns ``None`` when the variant
    could not be called (so callers treat no-call as "not assessed").
    """
    if alt_count is None:
        return None
    if alt_count == 0:
        return {
            "diplotype": "G/G",
            "phenotype": "Normal warfarin sensitivity",
            "dose_effect": "typical",
            "detail": (
                "VKORC1 c.-1639 G/G — normal VKORC1 expression and typical warfarin "
                "dose requirement (the reference for the VKORC1 component)."
            ),
        }
    if alt_count == 1:
        return {
            "diplotype": "G/A",
            "phenotype": "Increased warfarin sensitivity",
            "dose_effect": "lower",
            "detail": (
                "VKORC1 c.-1639 G/A — one reduced-expression A allele increases "
                "warfarin sensitivity, lowering the dose requirement relative to G/G."
            ),
        }
    return {
        "diplotype": "A/A",
        "phenotype": "High warfarin sensitivity",
        "dose_effect": "lowest",
        "detail": (
            "VKORC1 c.-1639 A/A — two reduced-expression A alleles give the highest "
            "warfarin sensitivity and the lowest dose requirement of the three genotypes."
        ),
    }


def cyp4f2_phenotype(alt_count: int | None) -> dict[str, str] | None:
    """Map the CYP4F2 rs2108622 *3-allele count to a dose-effect phenotype.

    ``alt_count`` is the number of forward-strand ``T`` (*3) alleles. Returns
    ``None`` when the variant could not be called.
    """
    if alt_count is None:
        return None
    if alt_count == 0:
        return {
            "diplotype": "*1/*1",
            "phenotype": "No CYP4F2 dose effect",
            "dose_effect": "typical",
            "detail": (
                "CYP4F2 *1/*1 — no *3 allele; no CYP4F2-attributable change to the "
                "warfarin dose requirement."
            ),
        }
    if alt_count == 1:
        return {
            "diplotype": "*1/*3",
            "phenotype": "Modestly higher dose requirement",
            "dose_effect": "higher",
            "detail": (
                "CYP4F2 *1/*3 — one *3 allele is associated with a modest increase in "
                "warfarin dose requirement (European/Asian ancestry)."
            ),
        }
    return {
        "diplotype": "*3/*3",
        "phenotype": "Higher dose requirement",
        "dose_effect": "higher",
        "detail": (
            "CYP4F2 *3/*3 — two *3 alleles are associated with a higher warfarin dose "
            "requirement (European/Asian ancestry)."
        ),
    }


def _assess_gene(
    *,
    gene: str,
    rsid: str,
    variant: str,
    ref: str,
    alt: str,
    genotype: str | None,
    phenotype_fn: Any,
) -> dict[str, Any]:
    """Build the context-only assessment for one warfarin gene from a raw genotype."""
    alt_count = _count_alt_alleles(genotype, ref, alt) if genotype else None
    pheno = phenotype_fn(alt_count)
    if pheno is None:
        return {
            "gene": gene,
            "rsid": rsid,
            "variant": variant,
            "observed_genotype": genotype,
            "called": False,
            "diplotype": None,
            "phenotype": None,
            "dose_effect": None,
            "detail": (
                f"{gene} {variant} ({rsid}) was not assayed or not callable on this "
                f"array, so its warfarin contribution could not be assessed."
            ),
        }
    return {
        "gene": gene,
        "rsid": rsid,
        "variant": variant,
        "observed_genotype": genotype,
        "called": True,
        **pheno,
    }


def assess_warfarin(sample_engine: sa.Engine) -> dict[str, Any]:
    """Context-only VKORC1 + CYP4F2 warfarin-dosing summary for a sample.

    Read-only. Looks up the two defining rsids in the sample database and reports
    each gene's direction of effect on the warfarin dose requirement. Emits no
    milligram dose and changes no finding — CPIC dosing needs a validated
    algorithm (with CYP2C9) plus clinical factors and INR monitoring.
    """
    genotypes = _fetch_sample_genotypes([VKORC1_RSID, CYP4F2_RSID], sample_engine)

    vkorc1 = _assess_gene(
        gene="VKORC1",
        rsid=VKORC1_RSID,
        variant="c.-1639G>A",
        ref=VKORC1_REF,
        alt=VKORC1_ALT,
        genotype=genotypes.get(VKORC1_RSID),
        phenotype_fn=vkorc1_phenotype,
    )
    cyp4f2 = _assess_gene(
        gene="CYP4F2",
        rsid=CYP4F2_RSID,
        variant="*3 (V433M)",
        ref=CYP4F2_REF,
        alt=CYP4F2_ALT,
        genotype=genotypes.get(CYP4F2_RSID),
        phenotype_fn=cyp4f2_phenotype,
    )

    return {
        "genes": [vkorc1, cyp4f2],
        "any_called": vkorc1["called"] or cyp4f2["called"],
        "context_only": True,
        "note": WARFARIN_PGX_CONTEXT_ONLY,
        "pmid_citations": [WARFARIN_CPIC_PMID],
    }
