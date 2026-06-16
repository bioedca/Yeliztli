"""Suite guards: no panel may rely on a non-array-typeable/withdrawn rsid, and
every pathway must contain at least one scoreable marker (#615).

#615: the sleep "Chronotype & Circadian Rhythm" pathway's only marker was
``rs57875989`` — the PER3 54-bp VNTR itself (a deprecated/unplaced dbSNP record,
not genotyped on any SNP array), modeled as a fake biallelic A/G SNP. The marker
could never match, so the whole pathway silently returned Standard/absent for
every user, yet CI stayed green. These self-discovering guards catch that class:

  (a) a denylist of rsids known NOT to be single-nucleotide array-typeable
      variants (length/repeat polymorphisms, CNVs) or withdrawn from dbSNP, so
      re-adding one to any panel fails immediately; and
  (b) every pathway in a pathways-structured panel must carry >= 1 scoreable SNP
      (a ``genotype_effects`` or ``indel_genotype_map`` marker), so a pathway can
      never be left with no marker that can actually fire.

A fuller check — resolving *every* panel rsid to a current dbSNP/Ensembl
coordinate (catching any withdrawn rsid, not just denylisted ones) — needs a
bundled rsid->coordinate reference to stay offline in CI; tracked as a follow-up.
"""

from __future__ import annotations

import json
from pathlib import Path

import backend.analysis.gene_health as gene_health_mod

_PANELS = Path(gene_health_mod.__file__).resolve().parent.parent / "data" / "panels"

# rsids that are NOT single-nucleotide, array-typeable variants (length / repeat
# polymorphisms, CNVs) or that have been withdrawn from dbSNP without a current
# genomic placement — so they can never be matched in the genotyping pipeline and
# must not be curated as scoreable SNP markers in any panel.
WITHDRAWN_OR_NON_ARRAY_TYPEABLE = {
    # rs57875989 IS the PER3 54-bp VNTR (4- vs 5-repeat, exon 18). dbSNP refsnp v2
    # returns it as unsupported_snapshot_data (no current placement) and Ensembl
    # GRCh37/GRCh38 return no record; being a VNTR it is not on any SNP array, and
    # no validated array-typeable tag SNP for it exists (Mansour 2017: PER3-locus
    # tag SNPs have LD < 0.5 and do not capture the VNTR). Removed in #615.
    "rs57875989",
}


def _walk_dicts(node: object):
    """Yield every dict nested anywhere inside a parsed-JSON structure."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_dicts(item)


def _all_panel_rsids() -> dict[str, set[str]]:
    """``{panel_filename: {rsid, ...}}`` across every panel JSON."""
    out: dict[str, set[str]] = {}
    for path in sorted(_PANELS.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        out[path.name] = {n["rsid"] for n in _walk_dicts(raw) if isinstance(n.get("rsid"), str)}
    return out


def _is_scoreable_marker(snp: object) -> bool:
    """A SNP entry that can actually be matched and scored: it carries a
    ``genotype_effects`` map or an ``indel_genotype_map`` (the two curated SNP
    scoring forms)."""
    if not isinstance(snp, dict):
        return False
    return bool(snp.get("genotype_effects")) or bool(snp.get("indel_genotype_map"))


def _pathway_can_fire(pathway: object) -> bool:
    """Whether a pathway has a marker that can actually produce a result: at least
    one scoreable SNP, or a PRS-scored pathway (``prs_primary``, e.g. traits
    ``cognitive_ability`` carries no curated SNPs and is scored from a PRS)."""
    if not isinstance(pathway, dict):
        return False
    if pathway.get("prs_primary"):
        return True
    snps = pathway.get("snps")
    return isinstance(snps, list) and any(_is_scoreable_marker(s) for s in snps)


def test_discovery_is_non_vacuous() -> None:
    """Guard the guards: panels and pathways must actually be discovered."""
    rsids = _all_panel_rsids()
    assert rsids, "no panel JSON files discovered"
    assert sum(len(v) for v in rsids.values()) >= 100, "panel rsid discovery regressed"
    sleep = json.loads((_PANELS / "sleep_panel.json").read_text(encoding="utf-8"))
    assert isinstance(sleep.get("pathways"), list) and sleep["pathways"], "no sleep pathways"


def test_no_panel_uses_a_non_array_typeable_or_withdrawn_rsid() -> None:
    """SELF-DISCOVERING guard (#615): no panel may curate a denylisted rsid that
    can never be matched in the genotyping pipeline."""
    offenders = []
    for panel, rsids in _all_panel_rsids().items():
        for bad in sorted(rsids & WITHDRAWN_OR_NON_ARRAY_TYPEABLE):
            offenders.append(f"{panel}::{bad}")
    assert not offenders, (
        "panel curates a non-array-typeable / withdrawn rsid that can never match "
        "(silently dead marker, #615): " + ", ".join(offenders)
    )


def test_every_pathway_has_a_scoreable_marker() -> None:
    """SELF-DISCOVERING guard (#615): every pathway in a pathways-structured panel
    must contain at least one scoreable SNP, so a pathway can never be advertised
    while being unable to ever fire (the dead chronotype pathway's failure mode)."""
    offenders = []
    for path in sorted(_PANELS.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        pathways = raw.get("pathways")
        if not isinstance(pathways, list):
            continue
        for pathway in pathways:
            if not _pathway_can_fire(pathway):
                offenders.append(
                    f"{path.name}::{pathway.get('id') if isinstance(pathway, dict) else pathway}"
                )
    assert offenders == [], (
        "pathway has no marker that can fire (no scoreable SNP and no PRS), so it is "
        "permanently dead (#615): " + ", ".join(offenders)
    )
