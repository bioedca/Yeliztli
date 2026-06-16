"""Repo-wide offline citation-provenance guard (#276).

There is a recurring class of "<panel> row cites unrelated PMID" defects: a
curated ``pmids`` entry that resolves to a paper from a *completely different
field*. These PMIDs are persisted verbatim into ``findings.pmid_citations`` and
surfaced to users as evidence links, but nothing in CI catches them repo-wide —
each has been fixed (and locked) one panel at a time.

This guard is the shared, offline (no-network), deterministic layer that the
per-panel fixes plug into. It does two things:

1. ``all_panel_pmids`` / ``all_proxy_pmids`` — reusable collectors over every
   curated citation surface (``backend/data/panels/*.json`` + the HLA proxy
   lookup), so future per-panel provenance tests don't re-implement extraction
   (#277 registers per-panel allow-lists/topic checks on top of these).

2. ``BANNED_OFF_TOPIC_PMIDS`` — a central registry of PMIDs that were caught
   misattributed AND are **globally off-topic** (a different scientific field
   entirely, so they can never legitimately back *any* human-genomics variant
   panel). It asserts none reappears anywhere (panels, proxy lookup, analysis
   source). Each entry carries the paper's real title as provenance.

Deliberately NOT in this registry: *same-field, wrong-gene* misattributions
(e.g. GeneReviews ``20301xxx`` chapters, or an aneurysm/oncology GWAS) — those
are legitimately citable for their *correct* gene, so they stay gene-scoped in
the per-panel guards (e.g. ``test_cancer.py`` MUTYH/CHEK2 banlists,
``test_hemochromatosis.py`` HFE allow-list). Banning them repo-wide would block
a future legitimate citation. See #277 for the per-panel topic/allow-list layer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent.parent / "backend"
_PANELS_DIR = _BACKEND / "data" / "panels"
_PROXY_LOOKUP = _PANELS_DIR / "hla_proxy_lookup.json"
_ANALYSIS_DIR = _BACKEND / "analysis"

# Each PMID below was confirmed (NCBI esummary) to resolve to a paper from a
# field with no possible connection to a human-genomics variant panel, after
# being caught misattributed to the listed panel. The title is the provenance:
# it is self-evidently off-topic. Add here ONLY a globally off-topic PMID — a
# same-field wrong-gene misattribution belongs in a gene-scoped per-panel guard.
BANNED_OFF_TOPIC_PMIDS: dict[str, dict[str, str]] = {
    "11735260": {
        "title": "Regulation of sodium-calcium exchange and mitochondrial energetics by Bcl-2",
        "field": "cardiac cell biology (Bcl-2), not a germline variant",
        "caught_in": "cardiovascular",
    },
    "17343727": {
        "title": "Automated array-CGH optimized for archival FFPE tissue",
        "field": "lab methodology (array-CGH on FFPE)",
        "caught_in": "sleep",
    },
    "17597076": {
        "title": "Structural basis for the function of DCN-1 in protein Neddylation",
        "field": "structural biology (neddylation)",
        "caught_in": "skin (FLG, #189)",
    },
    "18196153": {
        "title": "Grazing-incidence toroidal mirror pairs in imaging/spectroscopy",
        "field": "optics / instrumentation",
        "caught_in": "allergy",
    },
    "20162554": {
        "title": "Antigenic strength controls antigen-specific IL-10-secreting T cells",
        "field": "T-cell immunology",
        "caught_in": "methylation (DHFR)",
    },
    "20689844": {
        "title": "The biodiversity of the Mediterranean Sea: estimates, patterns, and threats",
        "field": "marine ecology",
        "caught_in": "gene_health",
    },
    "22177658": {
        "title": "Treatment decision-making and information preferences of patients",
        "field": "patient communication / decision science",
        "caught_in": "allergy",
    },
    "25904306": {
        "title": "Dispersant (not nanocarbon) induced lysosome abnormality in macrophages",
        "field": "nanotechnology toxicology",
        "caught_in": "gene_health",
    },
    "26092464": {
        "title": "Ectomycorrhizal communities on roots of two beech (Fagus sylvatica) populations",
        "field": "fungal ecology",
        "caught_in": "allergy",
    },
    "27095798": {
        "title": "Early-career family physicians' antibiotic prescribing for URTIs",
        "field": "prescribing-behaviour health-services research",
        "caught_in": "cardiovascular",
    },
    "28774630": {
        "title": "The chlorination transformation characteristics of benzophenone-4",
        "field": "environmental chemistry",
        "caught_in": "cancer (MUTYH)",
    },
    "15657627": {
        "title": "A review of Salmonella surveillance in New South Wales, 1998-2000",
        "field": "infectious-disease epidemiology",
        "caught_in": "sleep (ADORA2A)",
    },
    "16702423": {
        "title": "Mapping quantitative trait loci by an extension of the "
        "Haley-Knott regression method using estimating equations",
        "field": "statistical-genetics methodology (no specific gene/variant)",
        "caught_in": "gene_health (PPARG, #285)",
    },
    "18197166": {
        "title": "Mechanisms of post-transcriptional regulation by microRNAs",
        "field": "RNA-biology review (no specific variant)",
        "caught_in": "traits",
    },
    "21248726": {
        "title": "The emerging role of electronic medical records in pharmacogenomics",
        "field": "health informatics",
        "caught_in": "allergy",
    },
    "22232607": {
        "title": "Is There a Relationship between DNA Methylation and Phenotypic "
        "Plasticity in Invertebrates?",
        "field": "invertebrate epigenetics",
        "caught_in": "sleep (ADORA2A)",
    },
    "25979839": {
        "title": "Transcranial Direct Current Stimulation Against Sudden Unexpected "
        "Death in Epilepsy",
        "field": "neurostimulation therapy",
        "caught_in": "sleep (ADORA2A)",
    },
    "26547463": {
        "title": "Advancing Cardiovascular Science (editorial)",
        "field": "journal editorial (not a variant association)",
        "caught_in": "cardiovascular",
    },
    "30580001": {
        "title": "WITHDRAWN: Impact of Staging 68Ga-PSMA-11 PET scans on radiation "
        "treatment plans",
        "field": "withdrawn nuclear-medicine/radiology paper",
        "caught_in": "cardiovascular (LPA)",
    },
    "11251926": {
        "title": "Twin reversed arterial perfusion sequence in twin-to-twin transfusion syndrome",
        "field": "obstetrics / fetal medicine",
        "caught_in": "skin (MMP1, #345)",
    },
    "16826401": {
        "title": "Renewal effect: context-dependent extinction of a cocaine- and a "
        "morphine-induced conditioned floor preference",
        "field": "behavioural psychopharmacology",
        "caught_in": "skin (MMP1, #345)",
    },
    "20622888": {
        "title": "To name or not to name? ... removing anonymity from sperm donors",
        "field": "reproductive-ethics review (andrology)",
        "caught_in": "skin (MMP1, #345)",
    },
    # ── Registered from the #314 / #326 citation cleanups via #277 ──────────
    "16207938": {
        "title": "Acrylamide inhibits dopamine uptake in rat striatal synaptic vesicles.",
        "field": "neurotoxicology (acrylamide, rat striatum)",
        "caught_in": "methylation (#314)",
    },
    "10666248": {
        "title": "Human CD4+ T-cell clones recognizing Lassa-virus epitopes.",
        "field": "virology/immunology (Lassa-virus epitopes)",
        "caught_in": "methylation (#314)",
    },
    "20860029": {
        "title": "Phytochemistry and free-radical-scavenging of Melaleuca essential oil.",
        "field": "plant phytochemistry (essential oil)",
        "caught_in": "methylation (#314)",
    },
    "16962000": {
        "title": "The discoloration illusion.",
        "field": "visual perception (a colour-illusion paper)",
        "caught_in": "methylation (#314)",
    },
    "21680034": {
        "title": "The expanding scope of antimicrobial peptide structures.",
        "field": "microbiology (antimicrobial peptides)",
        "caught_in": "methylation (#314)",
    },
    "16234067": {
        "title": "Tracing the evolution of hepatitis C virus in the US, Japan, and Egypt.",
        "field": "virology (HCV molecular evolution)",
        "caught_in": "methylation (#314)",
    },
    "18404103": {
        "title": "Lumbar intervertebral disc puncture changes spontaneous pain behavior (rat).",
        "field": "rat pain model (disc puncture)",
        "caught_in": "methylation (#314)",
    },
    "18175331": {
        "title": "No transcriptional changes in mouse brain exposed to 1800 MHz GSM signal.",
        "field": "mouse RF-radiation study",
        "caught_in": "methylation (#314)",
    },
    "17522615": {
        "title": "Cereal-fibre content of the evening meal and next-day glucose tolerance.",
        "field": "nutrition/dietetics (cereal fibre)",
        "caught_in": "methylation (#314)",
    },
    "17445041": {
        "title": "Lipid profile in children with acute viral hepatitis A.",
        "field": "paediatric clinical chemistry",
        "caught_in": "methylation (#314)",
    },
    "16159893": {
        "title": "Hypotension in NKCC1 null mice: role of the kidneys.",
        "field": "mouse renal physiology (NKCC1-null)",
        "caught_in": "methylation (#314)",
    },
    "22012967": {
        "title": "Care for veterans with mental and substance use disorders.",
        "field": "health-services research",
        "caught_in": "methylation (#314)",
    },
    "22884227": {
        "title": "Early gastric fistula after laparoscopic sleeve gastrectomy.",
        "field": "bariatric surgery (gastric fistula)",
        "caught_in": "gene_health (#326)",
    },
    "22926369": {
        "title": "Acinetobacter baumannii as a cause of sepsis.",
        "field": "infectious-disease sepsis microbiology",
        "caught_in": "allergy/celiac HLA-DQ proxies (#876)",
    },
    "24076671": {
        "title": "Anatomical study of the medial crura and nasal tip projection in rhinoplasty.",
        "field": "plastic surgery (rhinoplasty)",
        "caught_in": "gene_health (#326)",
    },
    "18552285": {
        "title": "MAP kinase Hog1 mediates adaptation to G1 checkpoint arrest (yeast).",
        "field": "yeast cell biology (Hog1 MAPK)",
        "caught_in": "gene_health (#326)",
    },
    "17170444": {
        "title": "Epidemic dynamics of two coexisting hepatitis C virus subtypes.",
        "field": "virology epidemiology (HCV)",
        "caught_in": "gene_health (#326)",
    },
    "18191106": {
        "title": "Quinolones as enhancers of camptothecin anti-topoisomerase-I effects.",
        "field": "pharmacology/chemistry (topoisomerase)",
        "caught_in": "gene_health (#326)",
    },
    "26752085": {
        "title": "3D macroassembly of porous carbon/graphene nanosheets.",
        "field": "materials science (carbon aerogels)",
        "caught_in": "gene_health (#326)",
    },
    "22977957": {
        "title": "Culpability and the problem of the human genome (bioethics essay).",
        "field": "bioethics essay",
        "caught_in": "gene_health (#326)",
    },
    "27184023": {
        "title": "Lack of specific LGBTQ health-care education in medical school.",
        "field": "medical-education survey",
        "caught_in": "gene_health (#326)",
    },
    "25533199": {
        "title": "Mitotic accumulation of dimethylated H3K79 maintains genome integrity.",
        "field": "cell biology (histone H3K79)",
        "caught_in": "gene_health (#326)",
    },
}

# PMIDs that WERE caught misattributed but are biomedical/genomics-ADJACENT (they
# name real human genes or sit in an oncology/infection/receptor field), so they
# could legitimately back some *correct* gene in future. They must NOT be in the
# repo-wide registry above — repo-wide banning would block a valid citation. They
# stay caught by their existing gene/panel-scoped guards (the #277 layer), e.g.
# 12181445 -> test_methylation_panel.py (MTRR), 19289833 -> test_sleep_panel.py
# (PER3), 21149639 -> test_hemochromatosis.py (HFE). This set locks that
# decision so they can't be re-added to the global ban by mistake.
_GENE_SCOPED_NOT_REPO_BANNED: frozenset[str] = frozenset(
    {
        "12181445",  # CDK2/Chk1-Cdc25A cancer cell-cycle pharmacology (CHEK1/CDC25A/CDK2)
        "19289833",  # HIV gp41/CCR5 — CCR5 is a real human gene (Delta32 trait)
        "21149639",  # GPER1/GPR30 human GPCR cell biology
        "23430975",  # arginine butyrate for Duchenne MD — names the DMD gene/disease
        "27914672",  # superficial basal cell carcinoma — skin-cancer clinical (skin panel)
        # same-field wrong-gene catches from #314/#326 (real human gene/syndrome papers):
        "15701835",  # CREBBP/CBP mutations in human lung cancer (#314, DNMT3B row)
        "11745004",  # BAT-26/BAT-40 microsatellite variation (#314, SLC19A1 row)
        "12161596",  # PTPN11 mutations in LEOPARD syndrome (#314, TCN2 row)
        "15637710",  # Crisponi syndrome / CRLF1 human genetics (#314, MTR/CBS rows)
    }
)


# Keys under which curated JSON stores citations (a list, or a bare str/int):
#   pmids        — the common per-row list (most panels)
#   pmid         — single citation (e.g. hla_proxy_lookup.json)
#   source_pmid  — PRS/score provenance (pgs_score_registry, cancer_prs_weights, traits)
#   pmid_citations — the runtime findings output shape; not in curated input today.
# test_all_pmid_bearing_keys_are_covered() fails if a panel introduces a new
# PMID-bearing key not listed here, so the scan can't silently miss a citation.
_PMID_KEYS = ("pmids", "pmid_citations", "pmid", "source_pmid")
_SOURCE_PMID_RE = re.compile(r"\bPMID\s*:?\s*(\d+)\b", re.IGNORECASE)


def _iter_pmids(obj) -> list[str]:
    """Recursively collect PMIDs from the structured citation fields of a panel."""
    out: list[str] = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key in _PMID_KEYS:
                if isinstance(val, list):
                    out.extend(str(x) for x in val)
                elif isinstance(val, (str, int)):
                    out.append(str(val))
            else:
                out.extend(_iter_pmids(val))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_iter_pmids(item))
    return out


def _iter_indel_polarity_pmids(prov: dict) -> list[str]:
    """Collect structured PMIDs plus ``PMID 123`` mentions in provenance sources."""
    out = _iter_pmids(prov)
    sources = prov.get("sources")
    if isinstance(sources, str):
        out.extend(_SOURCE_PMID_RE.findall(sources))
    elif isinstance(sources, list):
        for source in sources:
            if isinstance(source, str):
                out.extend(_SOURCE_PMID_RE.findall(source))
    return out


def _iter_keys(obj, into: set[str]) -> None:
    """Recursively collect every dict key present in a loaded JSON document."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            into.add(key)
            _iter_keys(val, into)
    elif isinstance(obj, list):
        for item in obj:
            _iter_keys(item, into)


def all_panel_pmids() -> dict[str, set[str]]:
    """Map each panel JSON filename -> the set of PMIDs it cites (structured)."""
    result: dict[str, set[str]] = {}
    for path in sorted(_PANELS_DIR.glob("*.json")):
        if path.name == "hla_proxy_lookup.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        result[path.name] = set(_iter_pmids(data))
    return result


def all_proxy_pmids() -> set[str]:
    """All PMIDs referenced by the HLA proxy lookup table."""
    if not _PROXY_LOOKUP.exists():
        return set()
    return set(_iter_pmids(json.loads(_PROXY_LOOKUP.read_text(encoding="utf-8"))))


def all_indel_polarity_pmids() -> dict[str, set[str]]:
    """Map each discovered indel-polarity provenance record -> cited PMIDs.

    Panel JSON indel provenance is already covered by ``all_panel_pmids`` when it
    uses structured ``pmids`` lists. This collector makes the carrier-status
    module visible to the snapshot generator and also scans source strings for
    explicit ``PMID`` mentions.
    """
    from test_indel_polarity_provenance import (  # noqa: PLC0415
        _discover_carrier_indel_polarities,
        _discover_panel_indel_loci,
    )

    result: dict[str, set[str]] = {}
    for label, node in sorted(_discover_panel_indel_loci().items()):
        prov = node.get("indel_polarity")
        if isinstance(prov, dict):
            result[label] = set(_iter_indel_polarity_pmids(prov))
    for rsid, prov in sorted(_discover_carrier_indel_polarities().items()):
        result[f"carrier_status:{rsid}"] = set(_iter_indel_polarity_pmids(prov))
    return result


def test_registry_is_well_formed() -> None:
    """Each banned entry must carry a real title + provenance so it stays auditable."""
    assert BANNED_OFF_TOPIC_PMIDS, "registry must not be empty"
    for pmid, meta in BANNED_OFF_TOPIC_PMIDS.items():
        assert pmid.isdigit(), f"{pmid!r} is not a numeric PMID"
        assert meta.get("title"), f"{pmid}: missing real title (provenance)"
        assert meta.get("field"), f"{pmid}: missing off-topic field"
        assert meta.get("caught_in"), f"{pmid}: missing source panel"


def test_gene_adjacent_pmids_stay_gene_scoped() -> None:
    """Genomics-adjacent misattributions must never enter the repo-wide ban — they
    are legitimately citable for their correct gene and stay in per-panel guards."""
    wrongly_global = _GENE_SCOPED_NOT_REPO_BANNED & set(BANNED_OFF_TOPIC_PMIDS)
    assert not wrongly_global, (
        f"genomics-adjacent PMID(s) {sorted(wrongly_global)} must not be repo-wide "
        "banned (false-positive risk); keep them in gene-scoped per-panel guards"
    )


def test_banned_pmids_absent_from_every_panel() -> None:
    """No globally off-topic PMID may appear in any curated panel's citations."""
    by_panel = all_panel_pmids()
    offenders: list[str] = []
    for panel, pmids in by_panel.items():
        for bad in BANNED_OFF_TOPIC_PMIDS:
            if bad in pmids:
                field = BANNED_OFF_TOPIC_PMIDS[bad]["field"]
                offenders.append(f"{panel} cites off-topic PMID {bad} ({field})")
    assert not offenders, "off-topic PMIDs reappeared:\n" + "\n".join(offenders)


def test_banned_pmids_absent_from_proxy_lookup() -> None:
    proxy = all_proxy_pmids()
    leaked = sorted(set(BANNED_OFF_TOPIC_PMIDS) & proxy)
    assert not leaked, f"hla_proxy_lookup.json cites off-topic PMID(s) {leaked}"


def test_banned_pmids_absent_from_analysis_source() -> None:
    """Catch hard-coded PMID fallbacks in analysis modules (e.g. skin.py default lists)."""
    blob = "\n".join(p.read_text(encoding="utf-8") for p in sorted(_ANALYSIS_DIR.glob("*.py")))
    leaked = sorted(bad for bad in BANNED_OFF_TOPIC_PMIDS if bad in blob)
    assert not leaked, f"backend/analysis source hard-codes off-topic PMID(s) {leaked}"


def test_collectors_find_known_citations() -> None:
    """Sanity-check the shared collectors actually parse PMIDs (so absence above is real)."""
    by_panel = all_panel_pmids()
    total = sum(len(v) for v in by_panel.values())
    assert total > 100, f"expected many panel PMIDs, collector found only {total}"


def test_all_pmid_bearing_keys_are_covered() -> None:
    """Every PMID-bearing key used by any panel must be in ``_PMID_KEYS``.

    Without this, a panel could introduce a new citation key (e.g. a future
    ``*_pmid`` field) that the collector silently ignores — the ``source_pmid``
    blind spot this guard was hardened against. Any panel key whose name
    references a PMID must be scanned.
    """
    keys: set[str] = set()
    for path in sorted(_PANELS_DIR.glob("*.json")):
        _iter_keys(json.loads(path.read_text(encoding="utf-8")), keys)
    pmid_keys = {k for k in keys if "pmid" in k.lower()}
    uncovered = pmid_keys - set(_PMID_KEYS)
    assert not uncovered, (
        f"panel JSON uses PMID-bearing key(s) not scanned by the guard: "
        f"{sorted(uncovered)} — add them to _PMID_KEYS"
    )


def test_iter_pmids_collects_all_key_shapes() -> None:
    """``_iter_pmids`` reads every supported key, as list / str / int, at any depth."""
    doc = {
        "a": {"pmids": ["111", "222"]},
        "b": [{"pmid": "333"}, {"source_pmid": "444"}],
        "c": {"pmid_citations": [555]},  # ints coerced to str
        "ignored": {"note": "999", "gene_symbol": "888"},  # non-citation keys skipped
    }
    assert set(_iter_pmids(doc)) == {"111", "222", "333", "444", "555"}


def test_indel_polarity_collector_finds_known_provenance_pmids() -> None:
    by_locus = all_indel_polarity_pmids()
    assert {
        "apol1_panel.json:rs71785313",
        "gene_health_panel.json:rs80338939",
        "methylation_panel.json:rs70991108",
        "skin_panel.json:rs1799750",
        "carrier_status:rs113993960",
    } <= set(by_locus), f"indel-polarity discovery regressed: {sorted(by_locus)}"
    assert by_locus["carrier_status:rs113993960"] == {"2570460"}
    assert {"9285800", "20647424", "19022952"} <= set().union(*by_locus.values())
    assert set(_iter_indel_polarity_pmids({"sources": ["PubMed PMID: 12345"]})) == {"12345"}


def test_every_curated_panel_is_covered_by_the_shared_collector() -> None:
    """#277: the repo-wide guard auto-discovers EVERY curated panel.

    ``all_panel_pmids`` globs ``backend/data/panels/*.json`` (minus the proxy
    lookup, scanned separately), so a panel cannot be added without its PMIDs
    flowing into the offline denylist, the per-key coverage check, the
    topic-consistency snapshot guard, and the nightly resolution verifier — the
    generalization #277 asked for. This pins that no panel is silently excluded.
    """
    on_disk = {p.name for p in _PANELS_DIR.glob("*.json")} - {"hla_proxy_lookup.json"}
    covered = set(all_panel_pmids())
    assert on_disk, "no curated panels found on disk"
    assert covered == on_disk, (
        f"panel(s) not covered by all_panel_pmids(): {sorted(on_disk - covered)}"
    )
    assert len(covered) >= 8, f"suspiciously few panels covered ({len(covered)})"
