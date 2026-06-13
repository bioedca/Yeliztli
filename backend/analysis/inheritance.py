"""Shared autosomal-recessive disease-status classifier (#201).

Both the cardiovascular (#36 / #84) and cancer (#86 / #196) modules gate P/LP
variant findings on inheritance + zygosity with an identical rule: an
autosomal-dominant variant is affected-relevant when heterozygous, while an
autosomal-recessive condition needs a biallelic genotype, so a single
heterozygous P/LP allele is a *carrier* state, not an affected diagnosis. This
module is the single source of truth for that rule so the two panels cannot
drift — a correction made to one copy but not the other would be a silent
scientific-correctness bug, exactly the class of defect #36/#86 addressed.

``classify_disease_status`` is duck-typed over any result object exposing
``inheritance``, ``zygosity`` and ``gene_symbol`` (the :class:`DiseaseVariant`
protocol), so it works unchanged for ``CardiovascularVariantResult`` and
``CancerVariantResult`` and for any future panel that adopts AR gating. The
user-facing finding-text wording stays module-specific and is intentionally
*not* part of this module.
"""

from __future__ import annotations

from typing import Protocol

from backend.analysis.zygosity import ZYG_HET, ZYG_HOM_ALT

# Disease-status classifications for a P/LP variant under its gene's inheritance.
DISEASE_AFFECTED = "affected"
DISEASE_CARRIER = "carrier"
DISEASE_POSSIBLE_BIALLELIC = "possible_biallelic"


class DiseaseVariant(Protocol):
    """Minimal shape :func:`classify_disease_status` reads from a variant result."""

    inheritance: str  # "AD" or "AR"
    zygosity: str | None
    gene_symbol: str


def classify_disease_status(
    variant: DiseaseVariant,
    variants: list[DiseaseVariant],
) -> str:
    """Classify whether a P/LP variant supports an affected-disease finding.

    Autosomal-dominant (AD) variants are disease-relevant when heterozygous.
    Autosomal-recessive (AR) conditions require a biallelic genotype, so a single
    heterozygous P/LP allele is a *carrier* state, not an affected diagnosis
    (issue #36 cardiovascular, #86 cancer).

    Args:
        variant: The variant being classified.
        variants: All P/LP variants for the sample (used to count same-gene
            heterozygous loci for the possible-compound-heterozygote heuristic).

    Returns:
        - ``DISEASE_AFFECTED``: AD variant, or AR variant homozygous for the alt
          (biallelic at one locus).
        - ``DISEASE_POSSIBLE_BIALLELIC``: AR gene with >=2 heterozygous P/LP loci —
          a possible compound heterozygote, but genotype data cannot phase the
          alleles, so biallelic status is unconfirmed.
        - ``DISEASE_CARRIER``: AR gene with a single heterozygous P/LP allele.
    """
    if variant.inheritance != "AR":
        return DISEASE_AFFECTED
    if variant.zygosity == ZYG_HOM_ALT:
        return DISEASE_AFFECTED
    gene_het_plp = sum(
        1 for v in variants if v.gene_symbol == variant.gene_symbol and v.zygosity == ZYG_HET
    )
    if gene_het_plp >= 2:
        return DISEASE_POSSIBLE_BIALLELIC
    return DISEASE_CARRIER
