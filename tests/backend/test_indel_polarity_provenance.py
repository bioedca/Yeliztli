"""Guard: indel I/D → allele polarity is documented and cannot silently invert (#256).

Several high-evidence indel loci map the parser's canonical vendor ``I``/``D``
tokens to biological alleles using the standard **deletion=`D` / insertion=`I`**
convention:

* GJB2 35delG (rs80338939) — ``gene_health_panel.json`` ``indel_genotype_map``
* CFTR F508del (rs113993960) — ``carrier_status._SUPPORTED_CARRIER_INDEL_ZYGOSITY``
* APOL1 G2 (rs71785313) — ``apol1_panel.json`` (risk_allele ``D`` / ref_allele ``I``)
* DHFR 19-bp intron-1 deletion (rs70991108) — ``methylation_panel.json`` (#508)

The polarity (``D`` = the deletion / variant allele, ``I`` = reference) is the
literature-standard, dbSNP-consistent convention, but a silent inversion at any
of these loci would flip a clinical call (a true hom-reference person scored as
hom-affected, and vice-versa). Each locus carries a re-verifiable
``indel_polarity`` provenance record; this test locks both that provenance and
the live mapping so the assumption can never drift without CI catching it.

The per-locus ``Test*Polarity`` classes lock each locus's *live mapping* (the
genotype → call/category lookup). The ``test_every_*_indel_locus_has_canonical_*``
tests are **self-discovering** (#508): they enumerate every I/D-token locus in
the panels and the carrier module from disk, so a newly added or edited indel
locus that omits its ``indel_polarity`` provenance fails CI automatically — there
is no hand-maintained allow-list to forget (the omission DHFR slipped through).

Scope: the guard covers the vendor ``I``/``D``-token encoding (risk/ref alleles
literally in {D, I}) and the ``indel_genotype_map`` form — the encodings where a
D↔I inversion can silently flip a call. Bare-base indels written as explicit
sequences (e.g. MMP1 -1607 1G/2G as ``G``/``GG`` in skin_panel.json) are
intentionally out of scope: they carry no I/D token, so there is no polarity to
invert and no ``indel_polarity`` record is required.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import backend.analysis.carrier_status as carrier_mod
import backend.analysis.gene_health as gene_health_mod
from backend.analysis.apol1 import load_apol1_panel
from backend.analysis.gene_health import load_gene_health_panel

_PANELS = Path(gene_health_mod.__file__).resolve().parent.parent / "data" / "panels"

# A PMID is a bare digit string; the citation tooling iterates these, so the
# convention is a list of such strings (a bare string would iterate as chars).
_PMID_RE = re.compile(r"^\d+$")


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
    # At least one citation, AND a consistent shape across every record (#570).
    # Before this was truthiness-only, so CFTR's bare string "2570460" slipped
    # through and would have iterated as the characters '2','5','7',… for any
    # consumer walking the PMIDs.
    pmids = prov.get("pmids")
    sources = prov.get("sources")
    assert pmids or sources, f"{where}: must cite at least one of pmids/sources"
    if pmids is not None:
        assert isinstance(pmids, list) and pmids, (
            f"{where}: pmids must be a non-empty list[str], got {pmids!r}"
        )
        assert all(isinstance(p, str) and _PMID_RE.match(p) for p in pmids), (
            f"{where}: every pmid must be a numeric PMID string, got {pmids!r}"
        )
    if sources is not None:
        assert isinstance(sources, list) and sources, (
            f"{where}: sources must be a non-empty list[str], got {sources!r}"
        )
        assert all(isinstance(s, str) and s for s in sources), (
            f"{where}: every source must be a non-empty string, got {sources!r}"
        )


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


class TestDHFRPolarity:
    """DHFR 19-bp intron-1 deletion (rs70991108) — methylation_panel.json (#508)."""

    def _snp(self) -> dict:
        raw = _raw_panel("methylation_panel.json")
        return next(
            s for pw in raw["pathways"] for s in pw["snps"] if s.get("rsid") == "rs70991108"
        )

    def test_provenance_is_canonical(self) -> None:
        snp = self._snp()
        _assert_canonical_polarity(snp["indel_polarity"], where="DHFR rs70991108")
        assert snp["indel_polarity"]["dbsnp"] == "rs70991108"

    def test_live_effects_match_polarity(self) -> None:
        eff = self._snp()["genotype_effects"]
        # D=deletion(variant): II is the wild-type reference (normal DHFR), and any
        # D (the 19-bp deletion) shifts the Folate-pathway category. Inverting D/I
        # would relabel the reference homozygote as the variant one.
        assert eff["II"]["category"] == "Standard", "DHFR: II must be the wild-type reference"
        assert eff["DD"]["category"] == "Moderate", "DHFR: DD must be the homozygous deletion"
        assert eff["ID"]["category"] == eff["DI"]["category"] == "Moderate", (
            "DHFR: one D (heterozygous deletion) must be Moderate"
        )


# --- Self-discovering guards: no indel locus can escape provenance (#508) -----


def _walk_dicts(node: object):
    """Yield every dict nested anywhere inside a parsed-JSON structure."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_dicts(item)


def _is_indel_locus(node: dict) -> bool:
    """A scored locus that uses the parser's vendor I/D tokens — either via
    risk/ref alleles literally in {D, I} or via an ``indel_genotype_map``."""
    if not node.get("rsid"):
        return False
    tokens = {str(node.get("risk_allele", "")).upper(), str(node.get("ref_allele", "")).upper()}
    return bool(tokens & {"D", "I"}) or "indel_genotype_map" in node


def _discover_panel_indel_loci() -> dict[str, dict]:
    """{f'{panel}:{rsid}': locus_dict} for every I/D-token locus in any panel JSON."""
    found: dict[str, dict] = {}
    for path in sorted(_PANELS.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for node in _walk_dicts(raw):
            if _is_indel_locus(node):
                found[f"{path.name}:{node['rsid']}"] = node
    return found


def _discover_carrier_indel_polarities() -> dict[str, dict]:
    """{rsid: polarity_record} for every ``*_INDEL_POLARITY`` dict in carrier_status."""
    out: dict[str, dict] = {}
    for name in dir(carrier_mod):
        if name.endswith("_INDEL_POLARITY"):
            rec = getattr(carrier_mod, name)
            if isinstance(rec, dict) and rec.get("dbsnp"):
                out[rec["dbsnp"]] = rec
    return out


def _carrier_indel_rsids() -> set[str]:
    """rsids of every carrier_status zygosity map that contains literal D/I tokens."""
    rsids: set[str] = set()
    for key, zyg in carrier_mod._SUPPORTED_CARRIER_INDEL_ZYGOSITY.items():
        if any(tok in zyg for tok in ("DD", "II", "DI", "ID")):
            rsids.add(key[1])  # key == (gene, rsid, ref, alt)
    return rsids


def test_every_panel_indel_locus_has_canonical_polarity() -> None:
    """SELF-DISCOVERING durable guard (#508): enumerate every I/D-token locus in
    the panels and assert each carries a canonical ``indel_polarity`` record whose
    dbSNP matches the locus rsid. Fails the moment an indel locus is added or
    edited without its polarity provenance — no hand-maintained allow-list to
    forget. (This is the test that was failing on DHFR rs70991108 before #508.)"""
    loci = _discover_panel_indel_loci()
    # Discovery itself must not silently break and find nothing.
    assert {
        "gene_health_panel.json:rs80338939",
        "apol1_panel.json:rs71785313",
        "methylation_panel.json:rs70991108",
    } <= set(loci), f"indel-locus discovery regressed; found only {sorted(loci)}"
    for label, node in sorted(loci.items()):
        prov = node.get("indel_polarity")
        _assert_canonical_polarity(prov, where=label)
        assert prov["dbsnp"] == node["rsid"], (
            f"{label}: indel_polarity.dbsnp {prov['dbsnp']!r} != locus rsid {node['rsid']!r}"
        )


def test_every_carrier_indel_locus_has_canonical_polarity() -> None:
    """Self-discovering counterpart for the carrier_status indel zygosity maps:
    any map carrying D/I tokens must have a matching ``*_INDEL_POLARITY``
    provenance record indexed by the same rsid."""
    polarities = _discover_carrier_indel_polarities()
    carrier_rsids = _carrier_indel_rsids()
    assert carrier_rsids, "no carrier_status indel zygosity maps discovered"
    for rsid in sorted(carrier_rsids):
        prov = polarities.get(rsid)
        _assert_canonical_polarity(prov, where=f"carrier_status:{rsid}")
        assert prov["dbsnp"] == rsid


def test_all_indel_loci_share_one_polarity_convention() -> None:
    """Cross-locus: EVERY discovered I/D locus (panels + carrier module) uses the
    SAME D=deletion / I=reference convention, so the assumption is consistent
    repo-wide and re-verifiable."""
    records: list[dict] = []
    for label, node in sorted(_discover_panel_indel_loci().items()):
        prov = node.get("indel_polarity")
        assert prov is not None, f"{label}: missing indel_polarity provenance"
        records.append(prov)
    records += list(_discover_carrier_indel_polarities().values())
    # GJB2 + APOL1 + DHFR (panels) + CFTR (carrier module), at minimum.
    assert len(records) >= 4, f"expected ≥4 indel polarity records, found {len(records)}"
    for prov in records:
        assert prov["variant_class"] == "deletion"
        assert prov["variant_allele_token"] == "D"
        assert prov["reference_allele_token"] == "I"
