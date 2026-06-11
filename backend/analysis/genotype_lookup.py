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


def risk_allele_dosage(genotype: str, risk_allele: str, ref_allele: str) -> int:
    """Count copies (0–2) of ``risk_allele`` in ``genotype``, harmonizing strand.

    Aggregate allele-dosage counts must use the same strand-aware frame as
    :func:`lookup_by_genotype`, or a chip genotype reported on the opposite strand
    (e.g. ``"GA"`` for a C/T SNP whose panel risk allele is ``"T"``) scores as a
    carrier per-SNP yet contributes 0 to the dosage — an internally inconsistent
    result. Mirrors the reference-strand-first, complement-fallback discipline:
    count directly when the genotype already lives in the panel's ``{risk, ref}``
    frame, otherwise re-express it on the panel strand before counting. Strand-
    ambiguous (palindromic A/T or C/G) SNPs resolve to the reference strand, never
    re-flipped. Indels, no-calls, or genotypes that fit neither frame fall back to
    a direct count on the reference-strand string.
    """
    gt = genotype.upper()
    risk = risk_allele.upper()
    ref = ref_allele.upper()
    panel_alleles = {risk, ref}

    # Reference strand first: count directly when both alleles already live in the
    # panel's {risk, ref} frame (also the correct branch for palindromic SNPs).
    if gt and set(gt) <= panel_alleles:
        return gt.count(risk)

    # Complement fallback (pure A/C/G/T only): re-express on the panel strand.
    if gt and all(base in COMPLEMENT for base in gt):
        complemented = "".join(COMPLEMENT[base] for base in gt)
        if set(complemented) <= panel_alleles:
            return complemented.count(risk)

    # Neither frame fits (unexpected base / indel / no-call): direct count.
    return gt.count(risk)
