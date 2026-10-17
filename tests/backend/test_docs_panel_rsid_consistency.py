"""Generalized docs↔panel rsID consistency guard (#957, follow-up to #880).

A module doc page must never advertise a marker the product no longer scores —
the #880 class, where ``docs/modules/wellness/sleep.md`` kept citing the PER3
VNTR ``rs57875989`` after ``sleep_panel.json`` dropped it in #615. The focused
sleep guard (``test_sleep_docs_consistency.py``) is retained as the *named* PER3
regression; this generalizes its idea across every module page.

Two complementary checks (see #957's design caveat: a naive "every doc rsID ⊆
panel" sweep has false positives because some pages legitimately cite non-locus
rsIDs — worked examples, cross-references, context-only prose — so the broad
check carries a documented allowlist):

1. **Curated per-doc → panel check.** For module pages that map cleanly to one
   topical panel, every rsID the page cites must appear in that panel's JSON
   (the markers the module references). Catches a page promising a marker its
   own panel dropped.
2. **Union safety net.** Every rsID cited in *any* ``docs/modules/**/*.md`` page
   must exist in at least one panel — or be in the documented per-page allowlist
   of legitimately-cited non-locus rsIDs. Catches a marker dropped from *all*
   panels (the literal #880 case) even on pages without a clean 1:1 panel.

A third check (#2132) covers what an rsID subset cannot: on pages that list pathway
membership as ``- **Pathway** — *GENE*``, each pathway must name the genes its
panel pathway scores (the #2001 "right pathway, wrong gene" class).

Audit basis (2026-06-16): of the 23 module pages, only 8 cite any rsID; all
their cited rsIDs resolve to a panel except the two context-only *BCHE* variants
on ``specialized.md`` (allowlisted below). No drift exists at introduction —
this guard locks that in.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_PANELS_DIR = REPO_ROOT / "backend" / "data" / "panels"
_CPIC_ALLELES_CSV = REPO_ROOT / "backend" / "data" / "cpic" / "cpic_alleles.csv"
_DOCS_DIR = REPO_ROOT / "docs" / "modules"

_RSID_RE = re.compile(r"rs\d+")

# ── Check A: pages with a clean 1:1 topical panel ──────────────────────────
# doc (relative to docs/modules/) → panel filename. Only pages whose cited
# rsIDs all belong to a single topical panel are listed; cross-cutting pages
# (apoe — code-matched, no panel JSON; metabolic — PRS markers spanning several
# panels; specialized — many small panels) are intentionally covered only by the
# union check below, not force-mapped to one panel.
_CLEAN_DOC_PANEL = {
    "wellness/sleep.md": "sleep_panel.json",
    "wellness/fitness.md": "fitness_panel.json",
    "gated/parkinsons.md": "parkinsons_panel.json",
    "health-risk/carrier-status.md": "carrier_panel.json",
    # FH monogenic variants (e.g. APOB rs5742904) are scored in the
    # cardiovascular panel — FH has no separate panel JSON.
    "health-risk/familial-hypercholesterolemia.md": "cardiovascular_panel.json",
}

# ── Check B: per-page allowlist of legitimately-cited non-locus rsIDs ───────
# rsIDs a page mentions in prose that are NOT scored loci in any panel. Each
# needs a justification so the allowlist stays honest and auditable.
_DOC_NON_LOCUS_ALLOWLIST: dict[str, dict[str, str]] = {
    "specialized.md": {
        "rs1799807": (
            "BCHE 'atypical' variant — the BChE section is explicit context-only "
            "background and 'does not store findings'; not a scored locus."
        ),
        "rs1803274": (
            "BCHE 'K-variant' — same context-only BChE background; not a scored "
            "locus (true BChE deficiency is confirmed by enzyme-activity assay)."
        ),
    },
}


def _doc_rsids(path: Path) -> set[str]:
    return set(_RSID_RE.findall(path.read_text(encoding="utf-8")))


def _panel_rsids(panel_filename: str) -> set[str]:
    """Every rsID the panel JSON references (loci, ClinVar match lists, proxies,
    aliases — the union of all rsID-bearing fields)."""
    return set(_RSID_RE.findall((_PANELS_DIR / panel_filename).read_text(encoding="utf-8")))


def _cpic_allele_rsids() -> set[str]:
    """Star-allele defining rsIDs the CPIC caller scores (#2169).

    These are genuinely scored markers -- ``call_star_alleles_for_gene`` reads
    them straight out of this CSV -- they just are not panel loci. Folding them
    into the scored universe keeps the guard *discriminating*: if a marker is
    later dropped or renamed in the CPIC tables, a page still citing it fails
    here, which an allowlist entry would have silently masked.
    """
    return set(_RSID_RE.findall(_CPIC_ALLELES_CSV.read_text(encoding="utf-8")))


def _all_panel_rsids() -> set[str]:
    rsids: set[str] = set()
    for panel in _PANELS_DIR.glob("*.json"):
        rsids |= set(_RSID_RE.findall(panel.read_text(encoding="utf-8")))
    return rsids | _cpic_allele_rsids()


def _docs_citing_rsids() -> list[Path]:
    return sorted(
        p for p in _DOCS_DIR.rglob("*.md") if _RSID_RE.search(p.read_text(encoding="utf-8"))
    )


@pytest.mark.parametrize("doc_rel, panel", sorted(_CLEAN_DOC_PANEL.items()))
def test_mapped_doc_cites_only_panel_rsids(doc_rel: str, panel: str) -> None:
    """Check A: a cleanly-mapped page cites no rsID absent from its panel."""
    doc_path = _DOCS_DIR / doc_rel
    assert doc_path.exists(), f"mapped doc missing: {doc_rel}"
    cited = _doc_rsids(doc_path)
    assert cited, f"{doc_rel} is mapped to a panel but cites no rsID — drop it from the map"
    stale = cited - _panel_rsids(panel)
    assert not stale, (
        f"docs/modules/{doc_rel} cites rsID(s) absent from {panel}: {sorted(stale)}. "
        f"A marker may have been removed from the panel (cf. the PER3 VNTR rs57875989 "
        f"in #615/#880) — update the page, or if it's a legitimate non-locus mention add "
        f"it to _DOC_NON_LOCUS_ALLOWLIST with a justification."
    )


def test_every_doc_rsid_is_scored_or_allowlisted() -> None:
    """Check B: every module-doc rsID exists in some panel, or is allowlisted."""
    panel_universe = _all_panel_rsids()
    offenders: dict[str, list[str]] = {}
    for doc_path in _docs_citing_rsids():
        rel = doc_path.relative_to(_DOCS_DIR).as_posix()
        allow = set(_DOC_NON_LOCUS_ALLOWLIST.get(doc_path.name, {}))
        unscored = _doc_rsids(doc_path) - panel_universe - allow
        if unscored:
            offenders[rel] = sorted(unscored)
    assert not offenders, (
        "module docs cite rsID(s) that exist in NO panel and are not allowlisted "
        "(a marker dropped from every panel, or a typo): "
        + json.dumps(offenders)
        + ". Fix the page, or add the rsID to _DOC_NON_LOCUS_ALLOWLIST with a justification."
    )


def test_cpic_allele_universe_is_load_bearing() -> None:
    """The CPIC fold-in must not silently become a no-op (#2169).

    Star-allele markers are scored but are not panel loci, so they are admitted
    via ``_cpic_allele_rsids``. If that source were emptied, renamed, or dropped,
    the union check would quietly start rejecting every CPIC marker a page cites
    — or, worse, an allowlist entry would mask it. Pin that the CSV really is
    contributing, using the CYP3A5*3 marker this guard first tripped on.
    """
    cpic_rsids = _cpic_allele_rsids()
    assert len(cpic_rsids) > 10, "CPIC allele universe looks empty — check the CSV path"
    assert "rs776746" in cpic_rsids
    assert cpic_rsids - _panel_universe_from_panels(), (
        "every CPIC marker is already a panel locus, so this fold-in proves nothing"
    )


def _panel_universe_from_panels() -> set[str]:
    """Panel-only universe (no CPIC), for the load-bearing check above."""
    rsids: set[str] = set()
    for panel in _PANELS_DIR.glob("*.json"):
        rsids |= set(_RSID_RE.findall(panel.read_text(encoding="utf-8")))
    return rsids


def test_allowlisted_rsids_really_are_non_loci() -> None:
    """Premise guard: an allowlisted rsID must genuinely be absent from every
    panel. If one is later added as a real locus, this trips so the allowlist
    entry is removed rather than masking a now-scored marker.

    Deliberately panel-only: the CPIC fold-in widened ``_all_panel_rsids`` to the
    whole *scored* universe, and reusing it here would conflate "became a panel
    locus" with "is CPIC-scored". The CPIC half is asserted separately below so
    each failure message names the right cause.
    """
    panel_universe = _panel_universe_from_panels()
    leaked = {
        f"{doc}:{rsid}"
        for doc, entries in _DOC_NON_LOCUS_ALLOWLIST.items()
        for rsid in entries
        if rsid in panel_universe
    }
    assert not leaked, (
        f"allowlisted rsID(s) are now scored panel loci — remove them from "
        f"_DOC_NON_LOCUS_ALLOWLIST: {sorted(leaked)}"
    )


def test_allowlisted_rsids_are_not_cpic_scored() -> None:
    """The other half of the premise: an allowlisted rsID must not be CPIC-scored.

    If a star-allele marker is later added to ``cpic_alleles.csv``, the union
    check admits it on its own and the allowlist entry becomes both redundant and
    misleading (it asserts "not a scored locus" about something the product does
    score). Keeping this separate from the panel guard means the failure message
    points at the actual source.
    """
    cpic_universe = _cpic_allele_rsids()
    scored = {
        f"{doc}:{rsid}"
        for doc, entries in _DOC_NON_LOCUS_ALLOWLIST.items()
        for rsid in entries
        if rsid in cpic_universe
    }
    assert not scored, (
        f"allowlisted rsID(s) are CPIC star-allele markers and are therefore "
        f"scored — drop them from _DOC_NON_LOCUS_ALLOWLIST, the CPIC universe "
        f"already admits them: {sorted(scored)}"
    )


# ── Check C: gene→pathway attribution (#2132) ──────────────────────────────
# Checks A and B cannot see "right pathway, wrong gene". #2001 shipped
# fitness.md with Power as *ACTN3* + *MCT1* while the panel's Power pathway is
# ACE + MCT1, and stayed green because the page's only rsID (rs4341) was still a
# live ACE locus. The pages listed here enumerate pathway membership as
# ``- **Pathway** — *GENE*, *GENE*``; every such bullet that names a gene must
# name exactly the genes its panel pathway scores.
_PATHWAY_DOC_PANEL = {
    "wellness/allergy.md": "allergy_panel.json",
    "wellness/fitness.md": "fitness_panel.json",
    "wellness/skin.md": "skin_panel.json",
    "wellness/sleep.md": "sleep_panel.json",
    "wellness/traits-personality.md": "traits_panel.json",
}

# Doc bullet name → panel pathway name, for names that differ by more than case
# or a parenthetical.
_PATHWAY_NAME_ALIASES = {"behavioural traits": "behavioral traits"}

_PATHWAY_BULLET_RE = re.compile(r"^- \*\*(?P<name>[^*]+)\*\* — (?P<body>.*)$")
_PARENTHETICAL_RE = re.compile(r"\([^()]*\)")
# A single-asterisk italic token; the lookarounds keep **bold** text out.
_ITALIC_GENE_RE = re.compile(r"(?<!\*)\*([A-Za-z0-9-]+)\*(?!\*)")


def _pathway_key(name: str) -> str:
    key = " ".join(_PARENTHETICAL_RE.sub("", name).split()).casefold()
    return _PATHWAY_NAME_ALIASES.get(key, key)


def _doc_pathway_genes(text: str) -> dict[str, set[str]]:
    """``{pathway: genes}`` for each pathway bullet that names at least one gene.

    A bullet continues over its indented lines. Parenthetical asides are dropped
    before reading genes, so an italic word inside "(… see *Notes*)" is not a gene.
    A bullet naming no gene (HLA proxies, GWAS rows) makes no gene claim.
    """
    bullets: list[str] = []
    in_bullet = False
    for line in text.splitlines():
        if _PATHWAY_BULLET_RE.match(line):
            bullets.append(line)
            in_bullet = True
        elif in_bullet and line.startswith("  ") and line.strip():
            bullets[-1] += " " + line.strip()
        else:
            in_bullet = False
    claims: dict[str, set[str]] = {}
    for bullet in bullets:
        match = _PATHWAY_BULLET_RE.match(bullet)
        assert match is not None
        genes = set(_ITALIC_GENE_RE.findall(_PARENTHETICAL_RE.sub("", match["body"])))
        if genes:
            claims[_pathway_key(match["name"])] = genes
    return claims


def _panel_pathway_genes(panel_filename: str) -> dict[str, set[str]]:
    panel = json.loads((_PANELS_DIR / panel_filename).read_text(encoding="utf-8"))
    return {
        _pathway_key(pathway["name"]): {snp["gene"] for snp in pathway["snps"]}
        for pathway in panel["pathways"]
    }


def _pathway_gene_mismatches(
    claims: dict[str, set[str]], panel_genes: dict[str, set[str]]
) -> list[str]:
    mismatches: list[str] = []
    for pathway, genes in sorted(claims.items()):
        expected = panel_genes.get(pathway)
        if expected is None:
            mismatches.append(
                f"{pathway!r}: doc names {sorted(genes)}, but the panel has no such "
                f"pathway (it has {sorted(panel_genes)})"
            )
        elif genes != expected:
            mismatches.append(
                f"{pathway!r}: doc says {sorted(genes)}, panel says {sorted(expected)}"
            )
    return mismatches


@pytest.mark.parametrize("doc_rel, panel", sorted(_PATHWAY_DOC_PANEL.items()))
def test_doc_pathway_genes_match_panel(doc_rel: str, panel: str) -> None:
    """Check C: each gene-naming pathway bullet names exactly its panel pathway's genes."""
    doc_path = _DOCS_DIR / doc_rel
    assert doc_path.exists(), f"mapped doc missing: {doc_rel}"
    claims = _doc_pathway_genes(doc_path.read_text(encoding="utf-8"))
    assert claims, (
        f"{doc_rel} is mapped for gene→pathway checking but no "
        f"'- **Pathway** — *GENE*' bullet was parsed; fix the page or drop it from the map"
    )
    mismatches = _pathway_gene_mismatches(claims, _panel_pathway_genes(panel))
    assert not mismatches, f"docs/modules/{doc_rel} vs {panel}: " + "; ".join(mismatches)


def test_pathway_check_catches_the_2001_misattribution() -> None:
    """The pre-#2001 fitness page must fail Check C, and the corrected one must pass."""
    panel_genes = _panel_pathway_genes("fitness_panel.json")
    before = _doc_pathway_genes("- **Power** — *ACTN3* (sprint variant), *MCT1*\n")
    after = _doc_pathway_genes("- **Power** — *ACE* (an rs4341 I/D proxy), *MCT1*\n")

    assert _pathway_gene_mismatches(before, panel_genes) == [
        "'power': doc says ['ACTN3', 'MCT1'], panel says ['ACE', 'MCT1']"
    ]
    assert _pathway_gene_mismatches(after, panel_genes) == []


def test_pathway_bullet_parsing() -> None:
    """Parentheticals, bold text, continuation lines and gene-free bullets."""
    text = (
        "- **Power** — *ACE* (an rs4341 proxy — see *Notes*), **not** *MCT1*\n"
        "- **Drug hypersensitivity** — proxies for HLA-B\\*57:01, HLA-A\\*31:01\n"
        "- **Behavioural traits** — *CADM2* (risk tolerance),\n"
        "  a *DRD4* proxy\n"
        "\n"
        "  *NOTAGENE* indented after a blank line\n"
    )
    assert _doc_pathway_genes(text) == {
        "power": {"ACE", "MCT1"},
        "behavioral traits": {"CADM2", "DRD4"},
    }
