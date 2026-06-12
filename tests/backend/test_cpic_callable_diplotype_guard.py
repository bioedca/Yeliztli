"""Repo-wide guard for greedily callable CPIC diplotypes (issue #59)."""

from __future__ import annotations

from collections.abc import Iterable
from itertools import combinations, product
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from backend.analysis.pharmacogenomics import (
    STRUCTURAL_UNCALLABLE_ALLELES,
    CallConfidence,
    _fetch_alleles_for_gene,
    _indel_alt_token,
    call_star_alleles_for_gene,
)
from backend.annotation.cpic import CPIC_GENES, load_cpic_from_csvs
from backend.db.tables import reference_metadata

_CPIC_DIR = Path(__file__).resolve().parents[2] / "backend" / "data" / "cpic"

# Exhaustive 3^N enumeration is cheap through N=8 (6561 calls). Genes above the
# cap are sampled by reference, each single-locus alt state, and every pairwise
# heterozygous alt combination. CYP2D6 is currently the capped case and is always
# Partial because of structural-variant uncertainty, so this still exercises the
# caller without making the suite combinatorially expensive.
_MAX_EXHAUSTIVE_LOCI = 8
_Variant = dict[str, Any]
_VariantKey = tuple[str, str, str]


@pytest.fixture(scope="module")
def reference_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    load_cpic_from_csvs(
        _CPIC_DIR / "cpic_alleles.csv",
        _CPIC_DIR / "cpic_diplotypes.csv",
        _CPIC_DIR / "cpic_guidelines.csv",
        engine,
    )
    return engine


def _genotype_states(variant: _Variant) -> tuple[str, str, str] | None:
    """Return {ref, het, hom-alt} genotype tokens for caller-supported variants."""
    ref = variant["ref"].upper()
    alt = variant["alt"].upper()

    indel_alt_token = _indel_alt_token(ref, alt)
    if indel_alt_token is not None:
        ref_token = "I" if indel_alt_token == "D" else "D"
        return (ref_token * 2, ref_token + indel_alt_token, indel_alt_token * 2)

    if len(ref) == len(alt) == 1:
        return (ref * 2, ref + alt, alt * 2)

    return None


def _variant_key(variant: _Variant) -> _VariantKey:
    return (
        variant["rsid"],
        variant["ref"].upper(),
        variant["alt"].upper(),
    )


def _defining_variants(gene: str, alleles: list[dict[str, Any]]) -> dict[_VariantKey, _Variant]:
    """Index one gene's directly callable allele definitions by variant signature.

    Args:
        gene: Gene symbol used in assertion messages.
        alleles: CPIC allele dictionaries returned by _fetch_alleles_for_gene().

    Returns:
        Mapping of (rsid, ref, alt) to its defining variant dictionary.
    """
    variants: dict[_VariantKey, _Variant] = {}
    structural_uncallable = set(STRUCTURAL_UNCALLABLE_ALLELES.get(gene, ()))
    for allele in alleles:
        if allele["allele_name"] in structural_uncallable:
            continue
        for variant in allele["defining_variants"]:
            key = _variant_key(variant)
            if key in variants:
                continue
            variants[key] = variant
    return variants


def _collapse_variant_states(
    loci: list[_VariantKey],
    states: tuple[str, ...],
) -> dict[str, str] | None:
    """Collapse per-variant states to rsid genotypes, skipping contradictions."""
    genotypes: dict[str, str] = {}
    for variant_key, state in zip(loci, states):
        rsid = variant_key[0]
        if rsid in genotypes and genotypes[rsid] != state:
            return None
        genotypes[rsid] = state
    return genotypes


def _genotype_cases(
    state_map: dict[_VariantKey, tuple[str, str, str]],
) -> Iterable[tuple[str, dict[str, str]]]:
    """Generate labeled genotype dictionaries from per-variant state tuples.

    Args:
        state_map: Mapping of rsid to (hom-ref, het-alt, hom-alt) genotype
            tokens.

    Returns:
        Labeled genotype cases as (case label, rsid -> genotype). Genes at or
        below _MAX_EXHAUSTIVE_LOCI use itertools.product for the full Cartesian
        space. Larger genes are sampled with the full reference case, each
        single-locus alt state, and every pairwise heterozygous alt combination
        built with itertools.combinations.
    """
    loci = sorted(state_map)
    if len(loci) <= _MAX_EXHAUSTIVE_LOCI:
        for states in product(*(state_map[vkey] for vkey in loci)):
            genotypes = _collapse_variant_states(loci, states)
            if genotypes is not None:
                yield ("exhaustive", genotypes)
        return

    reference = _collapse_variant_states(loci, tuple(state_map[vkey][0] for vkey in loci))
    if reference is not None:
        yield ("sample/reference", dict(reference))
    else:
        reference = {}

    for vkey in loci:
        for state in state_map[vkey][1:]:
            genotype = dict(reference)
            genotype[vkey[0]] = state
            yield (f"sample/single-alt/{vkey}", genotype)

    for vkey1, vkey2 in combinations(loci, 2):
        genotype = dict(reference)
        genotype[vkey1[0]] = state_map[vkey1][1]
        genotype[vkey2[0]] = state_map[vkey2][1]
        yield (f"sample/pair-het/{vkey1}/{vkey2}", genotype)


def test_every_complete_confidence_cpic_diplotype_has_a_phenotype(
    reference_engine: sa.Engine,
) -> None:
    """No Complete-confidence CPIC call should be silently dropped as phenotype=None.

    Complex loci that the raw genotype parser cannot express, such as the
    UGT1A1 TA-repeat marker, are omitted from generated genotypes; their absence
    prevents Complete confidence, so they cannot hide a Complete-confidence
    phenotype mapping gap in this guard.
    """
    unmapped: list[str] = []

    for gene in sorted(CPIC_GENES):
        alleles = _fetch_alleles_for_gene(gene, reference_engine)
        variants = _defining_variants(gene, alleles)
        state_map = {
            rsid: states
            for rsid, variant in variants.items()
            if (states := _genotype_states(variant)) is not None
        }

        for case_label, genotypes in _genotype_cases(state_map):
            result = call_star_alleles_for_gene(gene, alleles, genotypes, reference_engine)
            if result.call_confidence == CallConfidence.COMPLETE and result.phenotype is None:
                unmapped.append(
                    f"{gene} {result.diplotype} from {case_label} genotypes={genotypes}"
                )

    assert not unmapped, (
        "Complete-confidence CPIC diplotypes with no phenotype mapping: " + "; ".join(unmapped)
    )
