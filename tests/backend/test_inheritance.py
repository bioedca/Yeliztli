"""Unit tests for the shared AR disease-status classifier (#201).

``classify_disease_status`` was extracted from the cardiovascular (#36/#84) and
cancer (#86/#196) modules, which had byte-identical copies. These tests exercise
the rule directly on a minimal duck-typed variant so the shared logic is covered
independently of either panel; the module-specific
``TestRecessiveInheritanceGating`` suites in test_cardiovascular.py and
test_cancer.py continue to assert the end-to-end behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.analysis.inheritance import (
    DISEASE_AFFECTED,
    DISEASE_CARRIER,
    DISEASE_POSSIBLE_BIALLELIC,
    classify_disease_status,
)
from backend.analysis.zygosity import ZYG_HET, ZYG_HOM_ALT


@dataclass
class _V:
    """Minimal stand-in satisfying the DiseaseVariant protocol."""

    inheritance: str
    zygosity: str | None
    gene_symbol: str


class TestClassifyDiseaseStatus:
    def test_autosomal_dominant_het_is_affected(self) -> None:
        v = _V("AD", ZYG_HET, "BRCA1")
        assert classify_disease_status(v, [v]) == DISEASE_AFFECTED

    def test_autosomal_dominant_hom_alt_is_affected(self) -> None:
        v = _V("AD", ZYG_HOM_ALT, "BRCA1")
        assert classify_disease_status(v, [v]) == DISEASE_AFFECTED

    def test_recessive_hom_alt_is_affected(self) -> None:
        # Biallelic at one locus → affected.
        v = _V("AR", ZYG_HOM_ALT, "MUTYH")
        assert classify_disease_status(v, [v]) == DISEASE_AFFECTED

    def test_recessive_single_het_is_carrier(self) -> None:
        v = _V("AR", ZYG_HET, "MUTYH")
        assert classify_disease_status(v, [v]) == DISEASE_CARRIER

    def test_recessive_two_hets_same_gene_is_possible_biallelic(self) -> None:
        # ≥2 heterozygous P/LP loci in one AR gene → possible compound het.
        v1 = _V("AR", ZYG_HET, "MUTYH")
        v2 = _V("AR", ZYG_HET, "MUTYH")
        assert classify_disease_status(v1, [v1, v2]) == DISEASE_POSSIBLE_BIALLELIC

    def test_recessive_hets_in_different_genes_stay_carriers(self) -> None:
        # The same-gene count is gene-scoped: two AR genes each with one het
        # allele are each a carrier, not a compound het.
        a = _V("AR", ZYG_HET, "ABCG5")
        b = _V("AR", ZYG_HET, "ABCG8")
        assert classify_disease_status(a, [a, b]) == DISEASE_CARRIER
        assert classify_disease_status(b, [a, b]) == DISEASE_CARRIER

    def test_recessive_three_hets_same_gene_is_possible_biallelic(self) -> None:
        vs = [_V("AR", ZYG_HET, "MUTYH") for _ in range(3)]
        assert classify_disease_status(vs[0], vs) == DISEASE_POSSIBLE_BIALLELIC
