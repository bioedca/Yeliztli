"""Repo-wide guard for greedily callable CPIC diplotypes (issue #59)."""

from __future__ import annotations

from collections.abc import Iterable
from itertools import combinations, product
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.pharmacogenomics import (
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


def _genotype_states(variant: dict) -> tuple[str, str, str] | None:
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


def _defining_variants(gene: str, alleles: list[dict]) -> dict[str, dict]:
    variants: dict[str, dict] = {}
    for allele in alleles:
        for variant in allele["defining_variants"]:
            rsid = variant["rsid"]
            ref_alt = (variant["ref"].upper(), variant["alt"].upper())
            if rsid in variants:
                existing = variants[rsid]
                existing_ref_alt = (existing["ref"].upper(), existing["alt"].upper())
                assert ref_alt == existing_ref_alt, (
                    f"{gene} has conflicting definitions for {rsid}: "
                    f"{existing_ref_alt} vs {ref_alt}"
                )
                continue
            variants[rsid] = variant
    return variants


def _genotype_cases(
    state_map: dict[str, tuple[str, str, str]],
) -> Iterable[tuple[str, dict[str, str]]]:
    loci = sorted(state_map)
    if len(loci) <= _MAX_EXHAUSTIVE_LOCI:
        for states in product(*(state_map[rsid] for rsid in loci)):
            yield ("exhaustive", dict(zip(loci, states)))
        return

    reference = {rsid: state_map[rsid][0] for rsid in loci}
    yield ("sample/reference", dict(reference))

    for rsid in loci:
        for state in state_map[rsid][1:]:
            genotype = dict(reference)
            genotype[rsid] = state
            yield (f"sample/single-alt/{rsid}", genotype)

    for rsid1, rsid2 in combinations(loci, 2):
        genotype = dict(reference)
        genotype[rsid1] = state_map[rsid1][1]
        genotype[rsid2] = state_map[rsid2][1]
        yield (f"sample/pair-het/{rsid1}/{rsid2}", genotype)


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
