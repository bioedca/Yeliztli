"""Strand-aware build-36 → GRCh37 lift for 23andMe v3 uploads (#562).

23andMe **v3** exports are on NCBI build 36 (hg18); v4/v5 are GRCh37. The
analysis pipeline assumes every sample's primary ``(chrom, pos)`` are GRCh37, so
before #562 a v3 file was *rejected* at ingest (PR #547) to avoid silently
mis-placing its variants by up to several megabases. This module instead lifts
v3 coordinates to GRCh37 so the invariant holds and v3 can be analysed.

The lift is **strand-aware**, which is the crux. hg18→hg19 conversion is
position-dependent and inverts ~2–5 Mb of the genome between builds; in those
inverted regions ``pyliftover`` reports a ``"-"`` target strand, and the
genotype alleles must be Watson–Crick complemented so the stored data stays
plus-strand-relative on GRCh37 — otherwise positional joins (e.g. PRS, which
expect plus-strand GRCh37 effect alleles) silently mismatch, and palindromic
(A/T, C/G) variants in inverted regions are the documented failure mode (Sheng
et al. 2022, *HGG Advances*; Ormond et al. 2021, *Brief. Bioinform.*).

Only pure-ACGT genotypes are complemented. Vendor indel tokens (``I``/``D``,
e.g. ``"II"``/``"DD"``/``"DI"``), no-calls (``"--"``) and any other non-ACGT
token are emitted verbatim — complementing them is undefined and would corrupt
the call. Variants that do not lift (chromosome absent from the chain, or the
position deleted/rearranged in hg19) are **dropped and counted**, never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import structlog

from backend.analysis.zygosity import COMPLEMENT
from backend.ingestion.base import ParsedVariant
from backend.ingestion.liftover import convert_hg18_to_hg19

logger = structlog.get_logger(__name__)


@dataclass
class Build36LiftResult:
    """Outcome of lifting a list of build-36 variants to GRCh37."""

    variants: list[ParsedVariant]
    """The lifted variants (GRCh37 coordinates, strand-corrected genotypes)."""
    lifted: int
    """Number of variants successfully lifted (== ``len(variants)``)."""
    dropped: int
    """Number of variants dropped because they did not lift."""
    strand_flipped: int
    """Number of lifted variants whose alleles were complemented (``-`` strand)."""


def _complement_genotype(genotype: str) -> str:
    """Watson–Crick complement a genotype, but only if it is pure ACGT.

    ``"AG"`` → ``"TC"``, ``"A"`` (hemizygous) → ``"T"``. No-calls (``"--"``),
    indel tokens (``"II"``/``"DD"``/``"DI"``/``"I"``/``"D"``) and anything else
    containing a non-ACGT character are returned **unchanged** — base complement
    is undefined for them, so complementing would corrupt the call.
    """
    if genotype and all(base in COMPLEMENT for base in genotype):
        return "".join(COMPLEMENT[base] for base in genotype)
    return genotype


def lift_build36_variants(variants: list[ParsedVariant]) -> Build36LiftResult:
    """Lift build-36 (hg18) variants to GRCh37, complementing inverted regions.

    For each variant: lift its ``(chrom, pos)`` hg18→hg19. If it does not lift,
    drop and count it. If the chain inverted the region (target strand ``"-"``),
    complement the genotype's ACGT alleles so it stays plus-strand-relative on
    GRCh37. Returns the lifted variants plus drop/flip counts for provenance.
    """
    lifted: list[ParsedVariant] = []
    dropped = 0
    strand_flipped = 0

    for v in variants:
        result = convert_hg18_to_hg19(v.chrom, v.pos)
        if result is None:
            dropped += 1
            continue
        new_chrom, new_pos, strand = result
        genotype = v.genotype
        if strand == "-":
            complemented = _complement_genotype(genotype)
            if complemented != genotype:
                strand_flipped += 1
            genotype = complemented
        lifted.append(replace(v, chrom=new_chrom, pos=new_pos, genotype=genotype))

    logger.info(
        "build36_lift_complete",
        total=len(variants),
        lifted=len(lifted),
        dropped=dropped,
        strand_flipped=strand_flipped,
    )
    return Build36LiftResult(
        variants=lifted,
        lifted=len(lifted),
        dropped=dropped,
        strand_flipped=strand_flipped,
    )
