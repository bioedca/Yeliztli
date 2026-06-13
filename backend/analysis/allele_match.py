"""Strand-aware allele matching for weighted scoring and risk-genotype calling.

The single home for strand/allele harmonization logic, shared by:

  - the PRS engine (:mod:`backend.analysis.prs`), via
    :func:`match_effect_allele_dosage` — counts effect-allele dosage for a
    published weight set, harmonizing the weight's allele frame against the
    chip's observed genotype; and
  - the by-rsID risk-genotype caller (:mod:`backend.analysis.risk_genotype`),
    via :func:`risk_dosage` / :func:`canonical_alleles` — counts copies of a
    curated risk allele, resolving the cross-vendor minus-strand pitfalls that
    bite monogenic risk loci (Factor V Leiden ``rs6025`` and Prothrombin
    ``rs1799963`` are reported on the minus strand by 23andMe).

Why this is necessary (EXPANSION_STRATEGY.md §10): the array's reported alleles
are on the chip *design* strand, which is sometimes the reverse strand relative
to the reference genome (and therefore relative to GWAS summary stats / ClinVar,
which are on the ``+`` strand). Raw string-matching an effect allele against the
genotype silently inverts the dosage for any opposite-strand weight set — the #1
source of a wrong PRS. ``annotated_variants.strand`` is the VEP *transcript*
strand (gene orientation) and is useless for this; the only reliable signals are
allele-set comparison (reference strand then Watson–Crick complement) plus the
allele frequency for the strand-ambiguous palindromes.

Harmonization mirrors the canonical bigsnpr ``snp_match`` discipline (Privé 2022,
*HGG Advances*; doi:10.1016/j.xhgg.2022.100136): match on the allele pair, allow
an opposite-strand ``_FLIP_`` via complement, and **drop strand-ambiguous A/T &
C/G SNPs whose minor-allele frequency is in [0.40, 0.60]** — near 0.5 the
frequency cannot disambiguate which strand the genotype is on, so scoring it
would be a coin flip. Away from that band a palindromic **homozygote** is still
strand-unknowable for a *single* sample (frequency-based orientation is a cohort
technique, invalid for n=1) and is likewise dropped, while a strand-invariant
**heterozygote** resolves to one effect-allele copy (#247).

The genotype is treated as ground truth and never flipped; it is the foreign
weight-set allele frame that we resolve *into* the chip's representation, so
downstream zygosity / QC stay consistent.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.analysis.zygosity import COMPLEMENT, is_no_call

# ── Match-status vocabulary ────────────────────────────────────────────────

#: Resolved on the reference strand (alleles matched the given pair directly).
MATCHED_REF = "matched_ref"
#: Resolved on the complemented strand (the effect allele was strand-flipped).
MATCHED_FLIP = "matched_flip"
#: Genotype is a no-call / unscoreable (see :func:`backend.analysis.zygosity.is_no_call`).
NO_CALL = "no_call"
#: Strand-ambiguous palindrome dropped — A/T or C/G near MAF 0.5 (bigsnpr rule), or a
#: palindromic homozygote away from 0.5 (strand unknowable for a single sample, #247).
AMBIGUOUS_DROPPED = "ambiguous_dropped"
#: Alleles fit neither the reference pair nor its complement (different/triallelic variant).
UNRESOLVED = "unresolved"
#: A palindromic pair could not be resolved because no MAF was supplied to
#: disambiguate the strand → dropped. (Named for the missing *frequency*, not a
#: missing allele — the other allele is present; the frequency is what's absent.)
MISSING_FREQ = "missing_freq"

#: The default lower/upper MAF bounds of the strand-ambiguous drop band.
AMBIGUOUS_MAF_LOW = 0.40
AMBIGUOUS_MAF_HIGH = 0.60


@dataclass(frozen=True)
class AlleleMatch:
    """Outcome of resolving a weight-set effect allele against a genotype.

    Attributes:
        dosage: Copies of the effect allele (0/1/2), or ``None`` when the SNP
            could not be scored (no-call, ambiguous-dropped, unresolved).
        status: One of the module-level status constants.
        strand: ``"ref"`` | ``"flip"`` | ``"n/a"`` — the frame the match resolved on.
    """

    dosage: int | None
    status: str
    strand: str


def _parse_alleles(genotype: str) -> set[str] | None:
    """Parse a 1- or 2-char genotype into an allele set of A/C/G/T, or None."""
    gt = genotype.strip().upper()
    if len(gt) == 1:
        alleles = {gt}
    elif len(gt) == 2:
        alleles = {gt[0], gt[1]}
    else:
        return None
    if any(a not in COMPLEMENT for a in alleles):  # non-ACGT (e.g. "-", "I", "D")
        return None
    return alleles


def _count(genotype_alleles_list: list[str], target: str) -> int:
    """Count copies of ``target`` among the (ordered) genotype alleles, capped at 2."""
    return min(sum(1 for a in genotype_alleles_list if a == target), 2)


def match_effect_allele_dosage(
    genotype: str | None,
    effect_allele: str,
    other_allele: str | None,
    maf: float | None,
    *,
    ambiguous_maf_low: float = AMBIGUOUS_MAF_LOW,
    ambiguous_maf_high: float = AMBIGUOUS_MAF_HIGH,
) -> AlleleMatch:
    """Resolve effect-allele dosage with strand harmonization (for PRS scoring).

    Behaviour depends on whether the weight set supplies the ``other_allele``:

    - **With ``other_allele``** (modern PGS-Catalog-style weights): full
      harmonization. The ``{effect, other}`` pair is matched against the observed
      alleles on the reference strand, then on the complemented strand (flipping
      the effect allele). Strand-ambiguous palindromes (``other == complement(
      effect)``) near MAF 0.5 are dropped per the bigsnpr rule; away from 0.5 a
      heterozygote (strand-invariant) resolves to dosage 1 while a homozygote
      (strand-ambiguous for a single sample) is dropped — never taken at ``+``
      strand face value, since frequency cannot orient one genotype's strand (#247).

    - **Without ``other_allele``** (legacy curated weights, e.g. the four
      hand-curated cancer scores): a strict back-compatible literal count — the
      effect allele is counted as-is with no strand attempt. A reverse-strand
      flip is genuinely undecidable from a lone effect allele, so guessing would
      *introduce* the inversion this function exists to prevent. This reproduces
      the historical ``_count_effect_allele`` math byte-for-byte.

    Args:
        genotype: Two-char (or haploid one-char) genotype string, or None.
        effect_allele: The allele the weight is expressed for.
        other_allele: The non-effect allele of the SNP, or None if unknown.
        maf: gnomAD allele frequency for the SNP (any-allele; only its distance
            from 0.5 matters), or None if unavailable.
        ambiguous_maf_low / ambiguous_maf_high: drop band for palindromes.

    Returns:
        An :class:`AlleleMatch`.
    """
    if is_no_call(genotype):
        return AlleleMatch(None, NO_CALL, "n/a")
    assert genotype is not None  # narrowed by is_no_call
    alleles = _parse_alleles(genotype)
    if alleles is None:
        return AlleleMatch(None, UNRESOLVED, "n/a")

    ea = effect_allele.strip().upper()
    if ea not in COMPLEMENT:
        return AlleleMatch(None, UNRESOLVED, "n/a")

    gt = genotype.strip().upper()
    gt_list = [gt] if len(gt) == 1 else [gt[0], gt[1]]

    oa = other_allele.strip().upper() if other_allele else None
    if oa is not None and oa not in COMPLEMENT:
        oa = None

    # ── Legacy path: no usable other allele → literal count, no strand attempt.
    # Preserves the historical ``_count_effect_allele`` contract byte-for-byte,
    # including treating a haploid single-allele call as 0 (no diploid dosage).
    if oa is None:
        if len(gt_list) < 2:
            return AlleleMatch(0, MATCHED_REF, "ref")
        return AlleleMatch(_count(gt_list, ea), MATCHED_REF, "ref")

    # ── Palindrome handling (A/T or C/G): the locus is strand-ambiguous.
    if oa == COMPLEMENT[ea]:
        # With no MAF we cannot place the SNP relative to the drop band → drop.
        if maf is None:
            return AlleleMatch(None, MISSING_FREQ, "n/a")
        # The drop band is symmetric around 0.5, so testing the raw frequency
        # against [low, high] is equivalent to min(af, 1-af) >= low.
        if ambiguous_maf_low <= maf <= ambiguous_maf_high:
            return AlleleMatch(None, AMBIGUOUS_DROPPED, "n/a")
        # Away from 0.5, a palindromic genotype still reads as the same allele set
        # on either strand, so resolution depends on ZYGOSITY, not on taking the +
        # strand at face value (#247): a HETEROZYGOTE is strand-invariant — exactly
        # one effect-allele copy either way — and resolves to dosage 1; a HOMOZYGOTE
        # is strand-ambiguous for a single sample (an opposite-strand "AA" is the
        # complement "TT"), so it is dropped. Allele frequency cannot orient one
        # genotype's strand — that is a cohort technique, invalid for n=1 (Deelen
        # 2014), the same discipline the metabolic anchors use (#138).
        if not alleles <= {ea, oa}:
            return AlleleMatch(None, UNRESOLVED, "n/a")
        if len(alleles) == 2:
            return AlleleMatch(1, MATCHED_REF, "ref")
        return AlleleMatch(None, AMBIGUOUS_DROPPED, "n/a")

    # ── Non-palindromic: try reference strand, then the complemented pair.
    pair = {ea, oa}
    if alleles <= pair:
        return AlleleMatch(_count(gt_list, ea), MATCHED_REF, "ref")
    cea, coa = COMPLEMENT[ea], COMPLEMENT[oa]
    if alleles <= {cea, coa}:
        # Reverse strand: count the complemented effect allele.
        return AlleleMatch(_count(gt_list, cea), MATCHED_FLIP, "flip")

    return AlleleMatch(None, UNRESOLVED, "n/a")


def canonical_alleles(
    genotype: str | None,
    ref: str | None,
    alt: str | None,
    *,
    allow_complement: bool = True,
) -> set[str] | None:
    """Resolve a genotype's alleles into the ``{ref, alt}`` frame, or None.

    Reference-strand comparison is tried first, then (when ``allow_complement``)
    the Watson–Crick complement for chip probes reported on the reverse strand.
    Returns the allele set expressed on the strand that matched ``{ref, alt}``,
    or ``None`` when the genotype is a no-call, ``ref``/``alt`` are not
    single-base SNVs, or the alleles match neither strand. This is the same
    resolution :func:`backend.analysis.zygosity.classify_zygosity` performs,
    exposed for the risk-genotype caller's dosage counting.

    ``allow_complement=False`` disables the reverse-strand fallback: a
    complemented-only observation returns ``None`` (indeterminate). This is for
    haploid mtDNA loci, where a complemented single base is a different variant
    rather than a strand flip (see :class:`RiskLocus.allow_strand_complement`).
    """
    if is_no_call(genotype):
        return None
    assert genotype is not None
    if not ref or not alt or len(ref) != 1 or len(alt) != 1:
        return None
    ref_u, alt_u = ref.upper(), alt.upper()
    if ref_u not in COMPLEMENT or alt_u not in COMPLEMENT:
        return None
    alleles = _parse_alleles(genotype)
    if alleles is None:
        return None
    if alleles <= {ref_u, alt_u}:
        return alleles
    if not allow_complement:
        return None
    cref, calt = COMPLEMENT[ref_u], COMPLEMENT[alt_u]
    if alleles <= {cref, calt}:
        # Re-express the reverse-strand observation on the reference strand so
        # callers can count the risk allele in a single frame.
        return {COMPLEMENT[a] for a in alleles}
    return None


def risk_dosage(
    genotype: str | None,
    risk_allele: str,
    ref_allele: str,
    *,
    allow_complement: bool = True,
) -> int | None:
    """Count copies of ``risk_allele`` carried at a locus, or ``None``.

    Resolves the observed genotype into the ``{risk, ref}`` frame (handling the
    reverse-strand representation when ``allow_complement``), then counts copies
    of the risk allele. Returns ``None`` (indeterminate — never a false negative)
    when the probe is a no-call or the alleles are explained by neither strand.

    This is the counting primitive for the by-rsID risk-genotype caller; it is
    what makes the minus-strand Factor V Leiden ``rs6025`` and Prothrombin
    ``rs1799963`` carriers call correctly regardless of the vendor's strand.

    ``allow_complement=False`` (haploid mtDNA loci) treats a complemented-only
    observation as indeterminate rather than a reverse-strand risk allele, so a
    literal plus-strand base that merely *complements* the risk allele does not
    fire a false-positive finding (see :class:`RiskLocus.allow_strand_complement`).
    """
    risk_u = risk_allele.strip().upper()
    ref_u = ref_allele.strip().upper()
    if risk_u not in COMPLEMENT or ref_u not in COMPLEMENT:
        return None
    # Canonicalize into the {ref, risk} frame (ref-strand or complemented).
    resolved = canonical_alleles(genotype, ref_u, risk_u, allow_complement=allow_complement)
    if resolved is None:
        return None
    gt = genotype.strip().upper()  # type: ignore[union-attr]  # not a no-call here
    gt_list = [gt] if len(gt) == 1 else [gt[0], gt[1]]
    # Map the observed alleles into the resolved (reference) frame, then count.
    if set(gt_list) <= {ref_u, risk_u}:
        framed = gt_list
    else:
        framed = [COMPLEMENT[a] for a in gt_list]
    return _count(framed, risk_u)
