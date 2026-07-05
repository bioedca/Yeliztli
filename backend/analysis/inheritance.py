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

# Inheritance modes the shared classifier knows how to gate, normalized to
# upper-case with surrounding whitespace stripped. It only handles the two
# autosomal modes; anything else — X-linked, mitochondrial, semi-dominant,
# undetermined (the wider ``moi`` vocabulary in ``db.tables``) or a curation typo
# — is rejected rather than silently defaulted to "affected", which would
# over-claim a carrier as an affected diagnosis (#616, the exact failure direction
# #36/#86 fixed). The full-text forms are accepted so OMIM/MONDO-style inheritance
# ("autosomal recessive") is forward-compatible.
_DOMINANT_INHERITANCE = frozenset({"AD", "AUTOSOMAL DOMINANT"})
_RECESSIVE_INHERITANCE = frozenset({"AR", "AUTOSOMAL RECESSIVE"})
_RECOGNIZED_INHERITANCE = _DOMINANT_INHERITANCE | _RECESSIVE_INHERITANCE


class DiseaseVariant(Protocol):
    """Minimal shape :func:`classify_disease_status` reads from a variant result."""

    inheritance: str  # an autosomal mode: "AD"/"AR" (or their full-text forms)
    zygosity: str | None
    gene_symbol: str


def _normalized_gene_symbol(variant: object) -> str:
    return (getattr(variant, "gene_symbol", "") or "").strip().upper()


def carried_variant_identity_key(variant: object) -> tuple[object, ...]:
    """Return a key for one carried biological allele, not one probe/result row.

    Consumer genotyping arrays can report the same physical variant under both a
    dbSNP rsID and a legacy probe ID. Compound-heterozygous heuristics must count
    distinct carried loci, so coordinate identity wins over display/probe ID.
    """
    gene_symbol = _normalized_gene_symbol(variant)
    chrom = (getattr(variant, "chrom", None) or "").strip().casefold()
    ref = (getattr(variant, "ref", None) or "").strip().upper()
    alt = (getattr(variant, "alt", None) or "").strip().upper()
    pos = getattr(variant, "pos", None)
    zygosity = (getattr(variant, "zygosity", None) or "").strip().casefold()

    if chrom and pos is not None and ref and alt:
        return ("allele", gene_symbol, chrom, int(pos), ref, alt, zygosity)

    rsid = (getattr(variant, "rsid", None) or "").strip().casefold()
    if rsid:
        return ("rsid", gene_symbol, rsid, zygosity)

    # Preserve pre-coordinate behavior for minimal duck-typed test doubles and
    # any future incomplete records: unknown identity should not collapse two
    # potentially distinct variants into one locus.
    return ("object", id(variant))


def variant_rsid_preference_key(variant: object) -> tuple[int, str]:
    """Prefer public dbSNP rsIDs over legacy array probe IDs for duplicate rows."""
    rsid = (getattr(variant, "rsid", None) or "").strip()
    normalized = rsid.casefold()
    is_dbsnp_rsid = normalized.startswith("rs") and normalized[2:].isdigit()
    return (0 if is_dbsnp_rsid else 1, normalized)


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

    The inheritance string is normalized (``.strip().upper()``) and matched
    against the recognized autosomal modes; an unrecognized value (typo, X-linked,
    mitochondrial, etc.) raises ``ValueError`` rather than silently defaulting to
    affected — over-claiming a carrier as an affected diagnosis is the failure
    direction #36/#86 fixed, and was previously reachable via the ``!= "AR"``
    catch-all (#616).

    Returns:
        - ``DISEASE_AFFECTED``: AD variant, or AR variant homozygous for the alt
          (biallelic at one locus).
        - ``DISEASE_POSSIBLE_BIALLELIC``: AR gene with >=2 heterozygous P/LP loci —
          a possible compound heterozygote, but genotype data cannot phase the
          alleles, so biallelic status is unconfirmed.
        - ``DISEASE_CARRIER``: AR gene with a single heterozygous P/LP allele.

    Raises:
        ValueError: if ``variant.inheritance`` is not a recognized autosomal mode
            (see ``_RECOGNIZED_INHERITANCE``).
    """
    inheritance = (variant.inheritance or "").strip().upper()
    if inheritance in _RECESSIVE_INHERITANCE:
        # AR needs a biallelic genotype; a single het P/LP allele is a carrier.
        if variant.zygosity == ZYG_HOM_ALT:
            return DISEASE_AFFECTED
        gene_symbol = _normalized_gene_symbol(variant)
        gene_het_loci = {
            carried_variant_identity_key(v)
            for v in variants
            if _normalized_gene_symbol(v) == gene_symbol and v.zygosity == ZYG_HET
        }
        if len(gene_het_loci) >= 2:
            return DISEASE_POSSIBLE_BIALLELIC
        return DISEASE_CARRIER
    if inheritance in _DOMINANT_INHERITANCE:
        # AD variants are disease-relevant when heterozygous.
        return DISEASE_AFFECTED
    raise ValueError(
        f"classify_disease_status: unrecognized inheritance {variant.inheritance!r} "
        f"for gene {variant.gene_symbol!r}; expected one of "
        f"{sorted(_RECOGNIZED_INHERITANCE)} (case- and whitespace-insensitive). "
        f"Refusing to classify rather than defaulting to {DISEASE_AFFECTED!r}, which "
        f"would over-claim a carrier as affected (#616). Add explicit handling for "
        f"this mode — e.g. X-linked recessive needs sex-aware logic the shared "
        f"classifier does not have."
    )
