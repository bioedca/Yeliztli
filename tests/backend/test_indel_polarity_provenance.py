"""Guard: indel I/D → allele polarity is documented and cannot silently invert (#256).

Several high-evidence indel loci map the parser's canonical vendor ``I``/``D``
tokens to biological alleles using the standard **deletion=`D` / insertion=`I`**
convention:

* GJB2 35delG (rs80338939) — ``gene_health_panel.json`` ``indel_genotype_map``
* CFTR F508del (rs113993960) — ``carrier_status._SUPPORTED_CARRIER_INDEL_ZYGOSITY``
* APOL1 G2 (rs71785313) — ``apol1_panel.json`` (risk_allele ``D`` / ref_allele ``I``)

The polarity (``D`` = the deletion / variant allele, ``I`` = reference) is the
literature-standard, dbSNP-consistent convention, but a silent inversion at any
of these loci would flip a clinical call (a true hom-reference person scored as
hom-affected, and vice-versa). Each locus now carries a re-verifiable
``indel_polarity`` provenance record; this test locks both that provenance and
the live mapping so the assumption can never drift without CI catching it.
"""

from __future__ import annotations

import json
from pathlib import Path

import backend.analysis.carrier_status as carrier_mod
import backend.analysis.gene_health as gene_health_mod
from backend.analysis.apol1 import load_apol1_panel
from backend.analysis.gene_health import load_gene_health_panel

_PANELS = Path(gene_health_mod.__file__).resolve().parent.parent / "data" / "panels"


def _assert_canonical_polarity(prov: dict, *, where: str) -> None:
    """Every indel_polarity record must declare D=deletion(variant), I=reference."""
    assert prov is not None, f"{where}: missing indel_polarity provenance"
    assert prov["variant_class"] == "deletion", where
    assert prov["variant_allele_token"] == "D", where
    assert prov["reference_allele_token"] == "I", where
    # The provenance must be self-describing and re-verifiable.
    assert "deletion" in prov["d_token_meaning"].lower(), where
    assert "reference" in prov["i_token_meaning"].lower(), where
    assert prov.get("dbsnp", "").startswith("rs"), where
    assert prov.get("accessed"), where
    assert prov.get("sources") or prov.get("pmids"), where


def _raw_panel(name: str) -> dict:
    return json.loads((_PANELS / name).read_text(encoding="utf-8"))


class TestGJB2Polarity:
    """GJB2 35delG (rs80338939) — gene_health_panel.json indel_genotype_map."""

    def test_provenance_is_canonical(self) -> None:
        raw = _raw_panel("gene_health_panel.json")
        snp = next(s for pw in raw["pathways"] for s in pw["snps"] if s["rsid"] == "rs80338939")
        _assert_canonical_polarity(snp["indel_polarity"], where="GJB2 rs80338939")
        assert snp["indel_polarity"]["dbsnp"] == "rs80338939"

    def test_live_map_matches_polarity(self) -> None:
        panel = load_gene_health_panel(_PANELS / "gene_health_panel.json")
        snp = next(s for pw in panel.pathways for s in pw.snps if s.rsid == "rs80338939")
        m = snp.indel_genotype_map
        # D=deletion(variant): DD is the homozygous-deletion (affected) genotype,
        # II is homozygous reference. Inverting D/I would swap these.
        assert m["DD"] == "delG/delG", "GJB2: DD must be the homozygous deletion"
        assert m["II"] == "GG", "GJB2: II must be homozygous reference"
        assert m["DI"] == m["ID"] == "G/delG", "GJB2: one D = heterozygous carrier"


class TestCFTRPolarity:
    """CFTR F508del (rs113993960) — carrier_status indel zygosity map."""

    KEY = ("CFTR", "rs113993960", "ATCT", "A")

    def test_provenance_is_canonical(self) -> None:
        _assert_canonical_polarity(
            carrier_mod._CFTR_F508DEL_INDEL_POLARITY, where="CFTR rs113993960"
        )
        assert carrier_mod._CFTR_F508DEL_INDEL_POLARITY["dbsnp"] == "rs113993960"

    def test_live_map_matches_polarity(self) -> None:
        m = carrier_mod._SUPPORTED_CARRIER_INDEL_ZYGOSITY[self.KEY]
        assert m["DD"] == "hom_alt", "CFTR: DD must be hom_alt (F508del/F508del)"
        assert m["II"] == "hom_ref", "CFTR: II must be hom_ref"
        assert m["DI"] == m["ID"] == "het", "CFTR: one D = heterozygous carrier"


class TestAPOL1Polarity:
    """APOL1 G2 (rs71785313) — apol1_panel.json indel risk allele."""

    def test_provenance_is_canonical(self) -> None:
        raw = _raw_panel("apol1_panel.json")
        g2 = next(loc for loc in raw["loci"] if loc["rsid"] == "rs71785313")
        _assert_canonical_polarity(g2["indel_polarity"], where="APOL1 rs71785313")
        assert g2["indel_polarity"]["dbsnp"] == "rs71785313"

    def test_live_locus_matches_polarity(self) -> None:
        panel = load_apol1_panel(_PANELS / "apol1_panel.json")
        g2 = panel.locus("rs71785313")
        assert g2 is not None
        assert g2.allele_type == "indel"
        # D=deletion is the G2 risk allele; I=reference is the non-risk allele.
        assert g2.risk_allele == "D", "APOL1 G2: risk allele must be D (deletion)"
        assert g2.ref_allele == "I", "APOL1 G2: ref allele must be I (reference)"


def test_all_indel_loci_share_one_polarity_convention() -> None:
    """Cross-locus: every I/D-mapped locus uses the SAME D=deletion / I=reference
    convention, so the assumption is consistent repo-wide and re-verifiable."""
    gh = _raw_panel("gene_health_panel.json")
    gjb2 = next(s for pw in gh["pathways"] for s in pw["snps"] if s["rsid"] == "rs80338939")[
        "indel_polarity"
    ]
    apol1 = next(
        loc for loc in _raw_panel("apol1_panel.json")["loci"] if loc["rsid"] == "rs71785313"
    )["indel_polarity"]
    cftr = carrier_mod._CFTR_F508DEL_INDEL_POLARITY

    for prov in (gjb2, apol1, cftr):
        assert prov["variant_class"] == "deletion"
        assert prov["variant_allele_token"] == "D"
        assert prov["reference_allele_token"] == "I"
