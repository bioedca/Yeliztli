"""Shared helpers for the finding-text gene-label guards (#2044).

Every categorical module persists a per-SNP card as
``"<label> (<genotype>) — <effect>"`` where ``<label>`` is
``variant_label(gene, variant_name)``. About half of the curated
``variant_name`` values already open with their gene ("FTO intron 1",
"LCT -13910C>T"), so a storage path that rebuilds the label by hand renders
"FTO FTO intron 1" or "MCM6/LCT LCT -13910C>T".

These helpers let each module's own test file drive the *production* path —
seed ``raw_variants`` → ``score_<module>_pathways`` → ``store_<module>_findings``
→ read ``findings`` back — over every gene-prefixed locus its real panel
carries, without hand-maintaining a locus list. They deliberately do **not**
call ``variant_label`` to decide what "gene-prefixed" means: the oracle here is
the test's own, so a regression in the helper cannot also shrink the sweep.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from backend.analysis.genotype_lookup import is_strand_ambiguous

_STANDARD = "Standard"

# A leading whitespace token repeated immediately after itself: "FTO FTO intron 1".
_DOUBLED_LEAD = re.compile(r"^(\S+)\s+\1(\s|$)")


class GenePrefixedLocus(NamedTuple):
    """A curated locus whose ``variant_name`` already opens with its gene."""

    rsid: str
    gene: str
    variant_name: str
    #: A curated, non-Standard, strand-unambiguous genotype — the one to seed so
    #: the module emits a per-SNP card for this locus.
    genotype: str


def _gene_aliases(gene: str) -> list[str]:
    """``gene`` plus, for a "/"-composite label, each component and the "-"-joined form."""
    parts = [part for part in gene.split("/") if part]
    if len(parts) < 2:
        return [gene]
    return [gene, *parts, "-".join(parts)]


def leads_with_gene(gene: str, variant_name: str) -> bool:
    """``variant_name`` opens with ``gene`` (or a composite alias) at a word boundary."""
    lowered = variant_name.lower()
    for alias in _gene_aliases(gene):
        token = alias.lower()
        if lowered.startswith(token) and (
            len(lowered) == len(token) or not lowered[len(token)].isalnum()
        ):
            return True
    return False


def _card_genotype(genotype_effects: dict[str, dict[str, Any]]) -> str | None:
    """A genotype that yields a per-SNP card: non-Standard and strand-unambiguous.

    Heterozygotes are preferred because they can never be strand-ambiguous; a
    homozygote is used only when the curated mapping keys no non-Standard
    heterozygote (recessive models such as SLC26A4). Deterministic in the
    panel's own key order.
    """
    candidates = [
        gt
        for gt, effect in genotype_effects.items()
        if effect.get("category") != _STANDARD and not is_strand_ambiguous(genotype_effects, gt)
    ]
    heterozygous = [gt for gt in candidates if len(gt) == 2 and gt[0] != gt[1]]
    ordered = heterozygous or candidates
    return ordered[0] if ordered else None


def gene_prefixed_loci(panel: Any) -> list[GenePrefixedLocus]:
    """Every panel locus whose ``variant_name`` leads with its gene, with a card genotype.

    Walks ``panel.pathways[*].snps`` in curated order. A locus is skipped only
    when no genotype could produce a card at all (every curated genotype is
    Standard or strand-ambiguous), because there is then no stored text to
    inspect.
    """
    loci: list[GenePrefixedLocus] = []
    for pathway in panel.pathways:
        for snp in pathway.snps:
            if not leads_with_gene(snp.gene, snp.variant_name):
                continue
            genotype = _card_genotype(snp.genotype_effects)
            if genotype is None:
                continue
            loci.append(GenePrefixedLocus(snp.rsid, snp.gene, snp.variant_name, genotype))
    return loci


def raw_variant_rows(loci: list[GenePrefixedLocus]) -> list[tuple[str, str, int, str]]:
    """``(rsid, chrom, pos, genotype)`` seed rows for ``_seed_variants``.

    The categorical scorers select ``raw_variants`` by rsid and read only the
    genotype, so chromosome and position are placeholders; the position is made
    unique per row purely so the seeded table looks like a real one.
    """
    return [(locus.rsid, "1", 1_000 + index, locus.genotype) for index, locus in enumerate(loci)]


def renders_gene_twice(label: str) -> bool:
    """Whether a rendered ``"<gene> <variant_name>"`` label prints its gene twice.

    Catches the plain repeat ("FTO FTO intron 1") and the composite repeat,
    where the lead token is a "/"-joined gene label and the next token repeats
    one of its components under any separator ("MCM6/LCT LCT -13910C>T",
    "CAV1/CAV2 CAV1-CAV2 intergenic").
    """
    if _DOUBLED_LEAD.match(label):
        return True
    tokens = label.split()
    if len(tokens) < 2:
        return False
    lead_parts = {part.lower() for part in tokens[0].split("/") if part}
    if len(lead_parts) < 2:
        return False
    next_parts = {part.lower() for part in re.split(r"[/-]", tokens[1]) if part}
    return bool(lead_parts & next_parts)
