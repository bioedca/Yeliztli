"""Strand- and order-aware genotype lookup for categorical panel scoring.

Every categorical-scoring module (nutrigenomics, gene_health, traits, fitness,
sleep, methylation, skin, allergy) resolves a curated effect by the sample's
genotype string via ``snp.genotype_effects.get(genotype)``. Two representations
of the *same* genotype must resolve to the same entry:

  - **Allele order** — a chip may report ``"TC"`` where the panel keys ``"CT"``
    (and ``"G/delG"`` where the panel keys ``"delG/G"`` for slash-delimited
    indel calls).
  - **Strand** — a genotyping chip reports alleles on its *design* strand, which
    for some SNPs is the reverse strand relative to the panel's curated keys. The
    flagship case is MTHFR C677T (``rs1801133``): 23andMe reports it as ``C``/``T``
    but the panel keys ``genotype_effects`` on the ``G``/``A`` (Watson–Crick
    complement) strand. Matching only the raw string silently drops such carriers
    to the default category.

``lookup_by_genotype`` tries candidates **reference strand first, complement as a
fallback** (so an already-matching genotype is never re-strand-flipped), each in
both allele orders. This mirrors the ref-then-complement discipline in
:mod:`backend.analysis.allele_match`. Non-ACGT genotypes (slash-delimited indels,
``"--"`` no-calls) skip the complement step, since a base complement is undefined
for them.
"""

from __future__ import annotations

from backend.analysis.zygosity import COMPLEMENT


def _order_variants(genotype: str) -> list[str]:
    """Return the genotype with its two alleles in both orders.

    Handles slash-delimited calls (``"delG/G"`` ↔ ``"G/delG"``) and plain
    two-character calls (``"CT"`` ↔ ``"TC"``). A single allele has no alternate
    order and is returned unchanged.
    """
    if "/" in genotype:
        first, second = genotype.split("/", 1)
        return [genotype, f"{second}/{first}"]
    if len(genotype) == 2:
        return [genotype, genotype[::-1]]
    return [genotype]


def genotype_candidates(genotype: str) -> list[str]:
    """Genotype keys to try, in priority order (reference strand, then complement).

    De-duplicated while preserving order, so a palindromic or single-allele
    genotype does not produce repeat lookups.
    """
    gt = genotype.upper()
    if gt and all(base in COMPLEMENT for base in gt):
        # Pure A/C/G/T: normalize case (a chip may report lowercase) so both the
        # reference-strand and complement candidates compare in the panel's
        # uppercase frame, then add the Watson–Crick complement strand.
        candidates = list(_order_variants(gt))
        complemented = "".join(COMPLEMENT[base] for base in gt)
        candidates.extend(_order_variants(complemented))
    else:
        # Slash-delimited indels and no-calls: keep the original case so tokens
        # like "del" still match the panel key; the base complement is undefined.
        candidates = list(_order_variants(genotype))

    seen: set[str] = set()
    deduped: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            deduped.append(candidate)
    return deduped


def lookup_by_genotype[T](mapping: dict[str, T], genotype: str) -> T | None:
    """Find ``mapping``'s value for ``genotype``, harmonizing allele order and strand.

    Returns the first matching value across :func:`genotype_candidates`, or
    ``None`` when no representation of the genotype is present in ``mapping``.
    """
    for candidate in genotype_candidates(genotype):
        # Membership test (not ``.get() is not None``) so an explicit ``None``
        # value is returned for a present key rather than skipped.
        if candidate in mapping:
            return mapping[candidate]
    return None


# ── Palindromic (strand-ambiguous) SNP guard (#170) ────────────────────────
#
# A/T and C/G are palindromic allele pairs: one allele is the Watson–Crick
# complement of the other. A *homozygous* call at such a locus (AA/TT or CC/GG)
# cannot be assigned to a strand from the genotype string alone — an array that
# reports the opposite strand from the curated panel turns the same biological
# homozygote into its complement, flipping the curated category. The generic
# complement fallback above cannot rescue this (the complement *is* the other
# homozygote), so the honest contract is to withhold the category rather than
# report the possibly-flipped one. The heterozygote (AT/CG) is strand-invariant
# and stays resolvable. (Deelen et al., *BMC Res Notes* 2014, PMID 25495213 —
# harmonizers resolve A/T & C/G SNPs by LD/reference, never by unconditional
# base complementation.)


def is_strand_ambiguous(mapping: dict[str, object], genotype: str) -> bool:
    """Whether ``genotype`` is a strand-unresolvable palindromic homozygote.

    True only when the genotype is a homozygote (``"AA"``, ``"TT"``, ``"CC"``,
    ``"GG"``) *and* the curated ``mapping`` keys both that homozygote and its
    Watson–Crick complement to **different** values — i.e. the call's category
    would flip with the (unknown) array strand. Only a palindromic SNP keys both
    a homozygote and its complement-homozygote, so this condition selects exactly
    the A/T and C/G strand-ambiguous case. Heterozygotes, non-palindromic SNPs,
    and palindromic homozygotes whose two strands map to the *same* value return
    ``False`` (strand cannot change the resolved category).
    """
    gt = genotype.upper()
    if len(gt) != 2 or gt[0] != gt[1] or gt[0] not in COMPLEMENT:
        return False
    complement = "".join(COMPLEMENT[base] for base in gt)
    forward = mapping.get(gt)
    reverse = mapping.get(complement)
    return forward is not None and reverse is not None and forward != reverse
