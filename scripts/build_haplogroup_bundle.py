#!/usr/bin/env python3
"""Build the PhyloTree + array-reportable Y-tree haplogroup JSON bundle.

Generates a ~150 KB JSON reference file containing defining SNP tables for
mtDNA (PhyloTree Build 17) and Y-chromosome (YBrowse hg19) haplogroup
trees.  The bundle is designed for the tree-walk haplogroup assignment
algorithm (P3-32).

The tree structure supports traversal from root to deepest matching node.
Each node contains the haplogroup name and its defining SNPs (mutations
that distinguish it from its parent).  The tree-walk algorithm checks
whether a sample's genotype matches the defining SNPs of each child node,
descending as deeply as possible.

SNPs are filtered to those present on 23andMe v5-era arrays:
  - ~500 mtDNA SNPs (positions on chrM, rCRS reference)
  - a source-audited Y-chromosome marker registry (GRCh37)

Resolution varies with array coverage; retained Y paths reach up to 11 levels.

Output files:
  - tests/fixtures/haplogroup_bundle.json  (for testing)
  - backend/data/panels/haplogroup_bundle.json  (for production)

Pre-built bundles are also hosted on GitHub Releases alongside VEP and
ancestry bundles.

Usage::

    python scripts/build_haplogroup_bundle.py
    python scripts/build_haplogroup_bundle.py --output tests/fixtures/haplogroup_bundle.json
    python scripts/build_haplogroup_bundle.py --dry-run
    python scripts/build_haplogroup_bundle.py --stats
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

# ── Version & metadata ─────────────────────────────────────────────────

BUNDLE_VERSION = "1.1.0"
BUILD = "GRCh37"
Y_SOURCE_PATH = Path(__file__).with_name("y_haplogroup_source.json")

# ── mtDNA haplogroup tree (PhyloTree Build 17) ─────────────────────────
#
# Structure: nested dicts with keys:
#   haplogroup: str        — haplogroup name
#   defining_snps: list    — SNPs that define this node vs parent
#   children: list         — child haplogroup nodes
#
# Each SNP: {"rsid": str, "pos": int, "allele": str}
#   - rsid: rs number or 23andMe internal ID (i-prefix) if no rs exists
#   - pos: position on the rCRS mitochondrial reference (1-16569)
#   - allele: derived allele that defines the mutation
#
# Data curated from PhyloTree Build 17 (van Oven & Kayser 2009),
# filtered to SNPs present on the 23andMe v5 genotyping array.
# Positions use the revised Cambridge Reference Sequence (rCRS, NC_012920).


def _mt_snp(rsid: str, pos: int, allele: str) -> dict[str, Any]:
    """Create an mtDNA defining SNP entry."""
    return {"rsid": rsid, "pos": pos, "allele": allele}


def _y_snp(rsid: str, pos: int, allele: str) -> dict[str, Any]:
    """Create a Y-chromosome defining SNP entry."""
    return {"rsid": rsid, "pos": pos, "allele": allele}


_EXCLUDED_Y_RSIDS: dict[str, str] = {
    # Ensembl GRCh37 places rs1000546 as a synonym of rs502450 at chr18:55773440.
    # It is not a Y marker and must never be used to satisfy the R min-evidence gate.
    "rs1000546": "autosomal chr18 alias of rs502450, not a Y defining marker",
    # Ensembl GRCh37 places this duplicate suspect at chr2:237800066.
    "rs35489731": "autosomal chr2 variant, not a Y defining marker",
    # Ensembl and NCBI report only C/T at this Y locus. The hand-curated tree
    # stored impossible G records on CT/DE/D without an authoritative marker name.
    "rs13304168": "invalid historic G allele and unresolved clade assignment",
    # Current RefSNP records place these aliases/identifiers off chromosome Y.
    "rs16981295": "current RefSNP maps to chromosome 20, not Y",
    "rs17250359": "merged into chromosome-X rs5945587, not a Y marker",
    "rs17250625": "merged into chromosome-X rs11555927, not a Y marker",
    "rs17250667": "merged into chromosome-X rs4826364, not a Y marker",
    "rs17316625": "current RefSNP maps to chromosome X, not Y",
    "rs17316724": "merged into chromosome-X rs6633675, not a Y marker",
    "rs17317007": "merged into chromosome-10 rs7907710, not a Y marker",
    "rs34282407": "current RefSNP maps to chromosome 7, not Y",
    "rs34424943": "current RefSNP is a chromosome-5 delins, not a Y SNP",
    "rs34602841": "current RefSNP is a chromosome-1 insertion, not a Y SNP",
    "rs35882927": "current RefSNP is a chromosome-18 delins, not a Y SNP",
    "rs34175940": "withdrawn RefSNP with no current placement",
    # These remain on Y, but the available source cannot support a precise node in
    # this simplified tree. Withhold them rather than broadening a deeper marker.
    "rs2032623": "Y insertion with no co-located YBrowse marker assignment",
    "rs2032677": "M194 defines a deeper Q subclade absent from this tree",
    "rs9341283": "only approximate or unresolved YBrowse clade assignments",
    "rs9786076": "L11 defines a deeper R1b subclade absent from this tree",
    "rs9786139": "L15 defines the absent IJK node and has a conflicting co-located row",
    "rs9786281": "only unknown or approximate YBrowse clade assignments",
    "rs9786429": "merged Y record has only an approximate KR assignment",
    "rs9786856": "only unknown or unresolved YBrowse clade assignments",
}


def _node(
    haplogroup: str,
    defining_snps: list[dict[str, Any]],
    children: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a haplogroup tree node."""
    node: dict[str, Any] = {
        "haplogroup": haplogroup,
        "defining_snps": defining_snps,
    }
    if children:
        node["children"] = children
    return node


def _load_y_source(path: Path = Y_SOURCE_PATH) -> dict[str, Any]:
    """Load the curated, array-reportable Y marker registry."""
    return json.loads(path.read_text(encoding="utf-8"))


def _build_y_marker_reference(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Flatten the source registry into the builder's marker validation table."""
    references: dict[str, dict[str, Any]] = {}
    for clade, node in source["nodes"].items():
        for marker in node["markers"]:
            references[marker["rsid"]] = {
                "pos": marker["pos"],
                "allele": marker["allele"],
                "alleles": tuple(
                    marker.get(
                        "ncbi_grch37_y_alleles",
                        (marker["ancestral_allele"], marker["allele"]),
                    )
                ),
                "clade": clade,
                "marker": marker["ybrowse_marker"],
                "source_clades": tuple(marker["source_clade_aliases"]),
            }
    return references


def _build_y_tree_from_source(source: dict[str, Any]) -> dict[str, Any]:
    """Build the emitted Y tree from the validated reportable-node registry."""
    nodes = source["nodes"]

    def build_node(name: str) -> dict[str, Any]:
        node = nodes[name]
        return _node(
            name,
            [
                _y_snp(marker["rsid"], marker["pos"], marker["allele"])
                for marker in node["markers"]
            ],
            [build_node(child) for child in node["children"]],
        )

    return _node(
        source["root"],
        [],
        [build_node(child) for child in source["root_children"]],
    )


def _validate_y_source(source: dict[str, Any]) -> list[str]:
    """Validate topology, evidence provenance, and reportability before emission."""
    issues: list[str] = []
    root = source.get("root")
    nodes = source.get("nodes", {})
    omitted = source.get("omitted_nodes", {})
    assignment = source.get("assignment", {})
    current_validation = source.get("current_validation", {})

    if root != "Y-Adam":
        issues.append(f"Y source root is {root!r}; expected 'Y-Adam'")
    if root in nodes:
        issues.append("Y source root must not also appear in the non-root node registry")
    if not isinstance(nodes, dict) or not nodes:
        return [*issues, "Y source has no node registry"]
    if set(nodes) & set(omitted):
        issues.append("Y source retained and omitted node sets overlap")
    expected_source_nodes = source.get("source_topology_non_root_nodes")
    if expected_source_nodes != len(nodes) + len(omitted):
        issues.append(
            "Y source retained + omitted node count does not match "
            f"source_topology_non_root_nodes={expected_source_nodes}"
        )
    if current_validation.get("failed_marker_records") != 0:
        issues.append("Y source current-record audit contains failed markers")
    for name, reason in omitted.items():
        if not isinstance(reason, str) or not reason.strip():
            issues.append(f"Omitted Y node {name} has no reason")

    root_children = source.get("root_children", [])
    derived_root_children = [name for name, node in nodes.items() if node.get("parent") == root]
    if root_children != derived_root_children:
        issues.append("Y source root_children does not match node parent declarations")

    seen_ids: dict[str, str] = {}
    seen_positions: dict[int, str] = {}
    trusted_from_nodes: set[str] = set()
    eligible_missing_passthrough: set[str] = set()
    allowed_alias_classes = {"single", "lineal", "nomenclature"}
    allowed_match_kinds = {"exact", "canonical_legacy"}

    for name, node in nodes.items():
        children = node.get("children", [])
        markers = node.get("markers", [])
        if not markers:
            issues.append(f"Reportable Y node {name} has no defining marker")
        if len(children) != len(set(children)):
            issues.append(f"Y node {name} repeats a child")
        for child in children:
            child_node = nodes.get(child)
            if child_node is None:
                issues.append(f"Y node {name} references missing child {child}")
            elif child_node.get("parent") != name:
                issues.append(f"Y child {child} does not declare {name} as its parent")

        trusted_single = node.get("trusted_single_marker") is True
        if children and len(markers) < assignment.get("min_internal_terminal_specific_snps", 2):
            if not trusted_single or len(markers) != 1:
                issues.append(
                    f"Internal Y node {name} has {len(markers)} marker(s) without "
                    "a trusted-single declaration"
                )
        elif trusted_single:
            issues.append(f"Y node {name} is trusted-single but does not need the exception")

        for marker in markers:
            rsid = marker.get("rsid")
            pos = marker.get("pos")
            ancestral = marker.get("ancestral_allele")
            derived = marker.get("allele")
            source_aliases = marker.get("source_clade_aliases")
            expected_source_clade = {"M_Y": "M", "N_Y": "N"}.get(name, name)
            if not isinstance(rsid, str) or not rsid:
                issues.append(f"Y node {name} has a marker without an identifier")
                continue
            previous_clade = seen_ids.get(rsid)
            if previous_clade is not None:
                issues.append(f"Y marker {rsid} is reused by {previous_clade} and {name}")
            else:
                seen_ids[rsid] = name
            if not isinstance(pos, int) or pos <= 0:
                issues.append(f"Y marker {rsid} at {name} has invalid GRCh37 position {pos!r}")
            else:
                previous_position_clade = seen_positions.get(pos)
                if previous_position_clade is not None:
                    issues.append(
                        f"Y position {pos} is reused by {previous_position_clade} and {name}"
                    )
                else:
                    seen_positions[pos] = name
            if ancestral not in {"A", "C", "G", "T"}:
                issues.append(
                    f"Y marker {rsid} at {name} has invalid ancestral allele {ancestral!r}"
                )
            if derived not in {"A", "C", "G", "T"} or derived == ancestral:
                issues.append(f"Y marker {rsid} at {name} has invalid derived allele {derived!r}")
            if marker.get("source_alias_class") not in allowed_alias_classes:
                issues.append(f"Y marker {rsid} at {name} has an unsafe source-clade alias class")
            if marker.get("match_kind") not in allowed_match_kinds:
                issues.append(f"Y marker {rsid} at {name} has an unsupported clade match kind")
            if not isinstance(source_aliases, list) or not source_aliases:
                issues.append(f"Y marker {rsid} at {name} has no source-clade provenance")
            elif (
                marker.get("match_kind") == "exact" and expected_source_clade not in source_aliases
            ):
                issues.append(
                    f"Y marker {rsid} at {name} exact-match aliases do not include "
                    f"{expected_source_clade}"
                )
            if (
                marker.get("match_kind") == "exact"
                and marker.get("source_isogg_clade") != expected_source_clade
            ):
                issues.append(
                    f"Y marker {rsid} at {name} has exact source clade "
                    f"{marker.get('source_isogg_clade')!r}; expected {expected_source_clade!r}"
                )
            if marker.get("current_validation_pass") is not True:
                issues.append(f"Y marker {rsid} at {name} failed current-record validation")
            identifier_source = marker.get("identifier_source")
            selected_alleles = {ancestral, derived}
            if identifier_source == "ncbi_refsnp":
                if marker.get("current_record_status") != "current":
                    issues.append(f"Y marker {rsid} at {name} is not a current RefSNP")
                if marker.get("ncbi_coordinate_match") is not True:
                    issues.append(f"Y marker {rsid} at {name} lacks its NCBI GRCh37 coordinate")
                if marker.get("ensembl_grch38_y_placement") is not True:
                    issues.append(f"Y marker {rsid} at {name} lacks an Ensembl Y placement")
                if not selected_alleles <= set(marker.get("ncbi_grch37_y_alleles", [])):
                    issues.append(f"Y marker {rsid} at {name} has alleles absent from NCBI")
                if not selected_alleles <= set(marker.get("ensembl_grch38_y_alleles", [])):
                    issues.append(f"Y marker {rsid} at {name} has alleles absent from Ensembl")
            elif identifier_source == "ybrowse_vendor_coordinate":
                if marker.get("current_record_status") != "vendor_internal":
                    issues.append(f"Y vendor marker {rsid} at {name} has an invalid status")
                if marker.get("vendor_ybrowse_coordinate_match") is not True:
                    issues.append(
                        f"Y vendor marker {rsid} at {name} lacks a YBrowse coordinate match"
                    )
                if not marker.get("array_coverage"):
                    issues.append(f"Y vendor marker {rsid} at {name} has no array coverage")
            else:
                issues.append(f"Y marker {rsid} at {name} has unknown identifier provenance")
            if trusted_single:
                trusted_from_nodes.add(rsid)
            if children and marker.get("all_four_arrays") is False:
                eligible_missing_passthrough.add(rsid)

    visited: set[str] = set()
    visiting: set[str] = set()

    def walk(name: str) -> None:
        if name in visiting:
            issues.append(f"Y source topology contains a cycle at {name}")
            return
        if name in visited or name not in nodes:
            return
        visiting.add(name)
        for child in nodes[name].get("children", []):
            walk(child)
        visiting.remove(name)
        visited.add(name)

    for child in root_children:
        walk(child)
    unreachable = set(nodes) - visited
    if unreachable:
        issues.append(f"Y source has unreachable nodes: {', '.join(sorted(unreachable))}")

    declared_trusted = set(assignment.get("trusted_single_marker_terminal_rsids", []))
    if declared_trusted != trusted_from_nodes:
        issues.append("Y trusted-single marker list does not match node declarations")
    declared_missing_passthrough = set(
        assignment.get("trusted_missing_internal_passthrough_rsids", [])
    )
    invalid_missing_passthrough = declared_missing_passthrough - eligible_missing_passthrough
    if invalid_missing_passthrough:
        issues.append(
            "Y missing-marker pass-through list contains ineligible markers: "
            + ", ".join(sorted(invalid_missing_passthrough))
        )
    refsnps = sum(
        marker.get("identifier_source") == "ncbi_refsnp"
        for node in nodes.values()
        for marker in node["markers"]
    )
    vendor_probes = sum(
        marker.get("identifier_source") == "ybrowse_vendor_coordinate"
        for node in nodes.values()
        for marker in node["markers"]
    )
    if refsnps != current_validation.get("current_rsids"):
        issues.append("Y source current RefSNP count does not match its audit summary")
    if vendor_probes != current_validation.get("vendor_internal_probes"):
        issues.append("Y source vendor-probe count does not match its audit summary")
    return issues


# The full output whitelist supersedes the former small hand-maintained table.
_Y_SOURCE = _load_y_source()
_AUDITED_Y_RSID_REFERENCE = _build_y_marker_reference(_Y_SOURCE)


def build_mt_tree() -> dict[str, Any]:
    """Build the mtDNA (PhyloTree) haplogroup tree.

    The tree represents the maternal lineage phylogeny.  Major macro-
    haplogroups L0-L6 are African; M and N (both descended from L3) are
    the two major out-of-Africa branches.  R is a sub-branch of N.

    Defining SNPs are the mutations (relative to rCRS) that distinguish
    each haplogroup from its parent in the tree.  Only SNPs genotyped on
    the 23andMe v5 array are included (~500 total).
    """
    # ── L0 branch ──────────────────────────────────────────────────
    l0a1 = _node(
        "L0a1",
        [
            _mt_snp("i5007158", 7158, "G"),
            _mt_snp("i5009818", 9818, "C"),
            _mt_snp("i5014308", 14308, "A"),
        ],
    )
    l0a2 = _node(
        "L0a2",
        [
            _mt_snp("i5007256", 7256, "T"),
            _mt_snp("i5011899", 11899, "C"),
        ],
    )
    l0a = _node(
        "L0a",
        [
            _mt_snp("i5001438", 1438, "G"),
            _mt_snp("i5005231", 5231, "A"),
            _mt_snp("i5009042", 9042, "T"),
        ],
        [l0a1, l0a2],
    )

    l0b = _node(
        "L0b",
        [
            _mt_snp("i5003693", 3693, "A"),
            _mt_snp("i5005580", 5580, "C"),
            _mt_snp("i5012171", 12171, "G"),
        ],
    )
    l0d1 = _node(
        "L0d1",
        [
            _mt_snp("i5008113", 8113, "T"),
            _mt_snp("i5015466", 15466, "G"),
        ],
    )
    l0d2 = _node(
        "L0d2",
        [
            _mt_snp("i5002969", 2969, "A"),
            _mt_snp("i5010394", 10394, "T"),
        ],
    )
    l0d = _node(
        "L0d",
        [
            _mt_snp("i5001715", 1715, "C"),
            _mt_snp("i5008251", 8251, "A"),
            _mt_snp("i5009755", 9755, "A"),
        ],
        [l0d1, l0d2],
    )

    l0f = _node(
        "L0f",
        [
            _mt_snp("i5003396", 3396, "G"),
            _mt_snp("i5010586", 10586, "A"),
        ],
    )
    l0k = _node(
        "L0k",
        [
            _mt_snp("i5002352", 2352, "C"),
            _mt_snp("i5011176", 11176, "A"),
        ],
    )

    l0 = _node(
        "L0",
        [
            # PhyloTree Build 17 gives mutations in forward evolutionary direction;
            # keep an array-covered subset of the L0 motif here.
            _mt_snp("i5001048", 1048, "T"),
            _mt_snp("i5005442", 5442, "C"),
            _mt_snp("i5006185", 6185, "C"),
            _mt_snp("i5009042", 9042, "T"),
            _mt_snp("i5010589", 10589, "A"),
        ],
        [l0a, l0b, l0d, l0f, l0k],
    )

    # ── L1 branch ──────────────────────────────────────────────────
    l1b1 = _node(
        "L1b1",
        [
            _mt_snp("i5005393", 5393, "T"),
            _mt_snp("i5012950", 12950, "G"),
        ],
    )
    l1b2 = _node(
        "L1b2",
        [
            _mt_snp("i5006446", 6446, "G"),
            _mt_snp("i5014869", 14869, "A"),
        ],
    )
    l1b = _node(
        "L1b",
        [
            _mt_snp("i5006185", 6185, "C"),
            _mt_snp("i5010115", 10115, "C"),
            _mt_snp("i5016126", 16126, "C"),
        ],
        [l1b1, l1b2],
    )

    l1c1 = _node(
        "L1c1",
        [
            _mt_snp("i5003483", 3483, "T"),
            _mt_snp("i5007859", 7859, "C"),
        ],
    )
    l1c2 = _node(
        "L1c2",
        [
            _mt_snp("i5008655", 8655, "T"),
            _mt_snp("i5013404", 13404, "C"),
        ],
    )
    l1c3 = _node(
        "L1c3",
        [
            _mt_snp("i5009947", 9947, "A"),
            _mt_snp("i5015452", 15452, "A"),
        ],
    )
    l1c = _node(
        "L1c",
        [
            _mt_snp("i5001048", 1048, "T"),
            _mt_snp("i5009072", 9072, "G"),
            _mt_snp("i5016129", 16129, "C"),
        ],
        [l1c1, l1c2, l1c3],
    )

    l1 = _node(
        "L1",
        [
            _mt_snp("i5003666", 3666, "A"),
            _mt_snp("i5007055", 7055, "G"),
            _mt_snp("i5007389", 7389, "C"),
            _mt_snp("i5010589", 10589, "A"),
            _mt_snp("i5010810", 10810, "C"),
        ],
        [l1b, l1c],
    )

    # ── L2 branch ──────────────────────────────────────────────────
    l2a1 = _node(
        "L2a1",
        [
            _mt_snp("i5003918", 3918, "A"),
            _mt_snp("i5011914", 11914, "A"),
            _mt_snp("i5015784", 15784, "C"),
        ],
    )
    l2a2 = _node(
        "L2a2",
        [
            _mt_snp("i5004158", 4158, "C"),
            _mt_snp("i5010688", 10688, "A"),
        ],
    )
    l2a = _node(
        "L2a",
        [
            _mt_snp("i5003594", 3594, "C"),
            _mt_snp("i5005836", 5836, "G"),
            _mt_snp("i5013803", 13803, "G"),
        ],
        [l2a1, l2a2],
    )

    l2b1 = _node(
        "L2b1",
        [
            _mt_snp("i5006722", 6722, "G"),
            _mt_snp("i5014769", 14769, "G"),
        ],
    )
    l2b = _node(
        "L2b",
        [
            _mt_snp("i5001227", 1227, "A"),
            _mt_snp("i5006680", 6680, "C"),
        ],
        [l2b1],
    )

    l2c = _node(
        "L2c",
        [
            _mt_snp("i5003010", 3010, "A"),
            _mt_snp("i5011944", 11944, "C"),
            _mt_snp("i5013958", 13958, "T"),
        ],
    )
    l2d = _node(
        "L2d",
        [
            _mt_snp("i5001442", 1442, "A"),
            _mt_snp("i5006293", 6293, "C"),
        ],
    )
    l2e = _node(
        "L2e",
        [
            _mt_snp("i5003200", 3200, "A"),
            _mt_snp("i5008404", 8404, "T"),
        ],
    )

    l2 = _node(
        "L2",
        [
            _mt_snp("i5002789", 2789, "C"),
            _mt_snp("i5007175", 7175, "C"),
            _mt_snp("i5007771", 7771, "G"),
            _mt_snp("i5009221", 9221, "G"),
            _mt_snp("i5016390", 16390, "A"),
        ],
        [l2a, l2b, l2c, l2d, l2e],
    )

    # ── L3 branch (ancestor of M and N → out of Africa) ───────────
    l3a = _node(
        "L3a",
        [
            _mt_snp("i5004386", 4386, "C"),
            _mt_snp("i5010086", 10086, "G"),
        ],
    )
    l3b1 = _node(
        "L3b1",
        [
            _mt_snp("i5006221", 6221, "C"),
            _mt_snp("i5012049", 12049, "A"),
        ],
    )
    l3b = _node(
        "L3b",
        [
            _mt_snp("i5002352", 2352, "C"),
            _mt_snp("i5010143", 10143, "A"),
        ],
        [l3b1],
    )
    l3d = _node(
        "L3d",
        [
            _mt_snp("i5008618", 8618, "C"),
            _mt_snp("i5015514", 15514, "C"),
        ],
    )
    l3e1 = _node(
        "L3e1",
        [
            _mt_snp("i5003675", 3675, "A"),
            _mt_snp("i5009554", 9554, "A"),
        ],
    )
    l3e2 = _node(
        "L3e2",
        [
            _mt_snp("i5002352", 2352, "C"),
            _mt_snp("i5005261", 5261, "A"),
        ],
    )
    l3e = _node(
        "L3e",
        [
            _mt_snp("i5002352", 2352, "C"),
            _mt_snp("i5014905", 14905, "A"),
        ],
        [l3e1, l3e2],
    )
    l3f = _node(
        "L3f",
        [
            _mt_snp("i5004218", 4218, "C"),
            _mt_snp("i5015670", 15670, "C"),
        ],
    )

    # ── M branch (out-of-Africa via L3) ────────────────────────────
    c1 = _node(
        "C1",
        [
            _mt_snp("i5006026", 6026, "T"),
            _mt_snp("i5011969", 11969, "A"),
            _mt_snp("i5013263", 13263, "G"),
        ],
    )
    c4 = _node(
        "C4",
        [
            _mt_snp("i5005979", 5979, "T"),
            _mt_snp("i5011365", 11365, "C"),
        ],
    )
    c5 = _node(
        "C5",
        [
            _mt_snp("i5001607", 1607, "G"),
            _mt_snp("i5009545", 9545, "G"),
        ],
    )
    c = _node(
        "C",
        [
            _mt_snp("i5003552", 3552, "A"),
            _mt_snp("i5009545", 9545, "G"),
            _mt_snp("i5011914", 11914, "A"),
            _mt_snp("i5013263", 13263, "G"),
        ],
        [c1, c4, c5],
    )

    d1 = _node(
        "D1",
        [
            _mt_snp("i5005178", 5178, "A"),
            _mt_snp("i5016325", 16325, "C"),
        ],
    )
    d2 = _node(
        "D2",
        [
            _mt_snp("i5004883", 4883, "T"),
            _mt_snp("i5012705", 12705, "C"),
        ],
    )
    d3 = _node(
        "D3",
        [
            _mt_snp("i5003394", 3394, "C"),
            _mt_snp("i5010181", 10181, "T"),
        ],
    )
    d4a = _node(
        "D4a",
        [
            _mt_snp("i5012026", 12026, "G"),
        ],
    )
    d4b = _node(
        "D4b",
        [
            _mt_snp("i5008020", 8020, "A"),
        ],
    )
    d4 = _node(
        "D4",
        [
            _mt_snp("i5003010", 3010, "A"),
            _mt_snp("i5008414", 8414, "T"),
            _mt_snp("i5014668", 14668, "T"),
        ],
        [d4a, d4b],
    )
    d5 = _node(
        "D5",
        [
            _mt_snp("i5001048", 1048, "T"),
            _mt_snp("i5004883", 4883, "T"),
        ],
    )
    d = _node(
        "D",
        [
            _mt_snp("i5004883", 4883, "T"),
            _mt_snp("i5005178", 5178, "A"),
            _mt_snp("i5016362", 16362, "C"),
        ],
        [d1, d2, d3, d4, d5],
    )

    e = _node(
        "E",
        [
            _mt_snp("i5007598", 7598, "A"),
            _mt_snp("i5012405", 12405, "T"),
            _mt_snp("i5014110", 14110, "C"),
        ],
    )
    g1 = _node(
        "G1",
        [
            _mt_snp("i5004833", 4833, "G"),
        ],
    )
    g2a = _node(
        "G2a",
        [
            _mt_snp("i5007600", 7600, "A"),
        ],
    )
    g2 = _node(
        "G2",
        [
            _mt_snp("i5007598", 7598, "A"),
        ],
        [g2a],
    )
    g = _node(
        "G",
        [
            _mt_snp("i5004833", 4833, "G"),
            _mt_snp("i5007598", 7598, "A"),
        ],
        [g1, g2],
    )

    z1 = _node(
        "Z1",
        [
            _mt_snp("i5015487", 15487, "T"),
        ],
    )
    z = _node(
        "Z",
        [
            _mt_snp("i5006752", 6752, "G"),
            _mt_snp("i5015487", 15487, "T"),
        ],
        [z1],
    )

    m1 = _node(
        "M1",
        [
            _mt_snp("i5006446", 6446, "G"),
            _mt_snp("i5012403", 12403, "T"),
            _mt_snp("i5014110", 14110, "C"),
        ],
    )
    m7a = _node(
        "M7a",
        [
            _mt_snp("i5004386", 4386, "C"),
            _mt_snp("i5008684", 8684, "T"),
        ],
    )
    m7b = _node(
        "M7b",
        [
            _mt_snp("i5005351", 5351, "G"),
            _mt_snp("i5009824", 9824, "A"),
        ],
    )
    m7c = _node(
        "M7c",
        [
            _mt_snp("i5003606", 3606, "G"),
            _mt_snp("i5011665", 11665, "T"),
        ],
    )
    m7 = _node(
        "M7",
        [
            _mt_snp("i5004071", 4071, "T"),
            _mt_snp("i5006455", 6455, "T"),
        ],
        [m7a, m7b, m7c],
    )

    m8a = _node(
        "M8a",
        [
            _mt_snp("i5008684", 8684, "T"),
            _mt_snp("i5015487", 15487, "T"),
        ],
    )
    m8 = _node(
        "M8",
        [
            _mt_snp("i5007196", 7196, "A"),
            _mt_snp("i5008684", 8684, "T"),
        ],
        [m8a],
    )

    m9 = _node(
        "M9",
        [
            _mt_snp("i5003394", 3394, "C"),
            _mt_snp("i5014308", 14308, "A"),
            _mt_snp("i5016362", 16362, "C"),
        ],
    )

    m_branch = _node(
        "M",
        [
            _mt_snp("i5000489", 489, "C"),
            _mt_snp("rs1000361", 10951, "A"),
            _mt_snp("i5014783", 14783, "C"),
            _mt_snp("i5015043", 15043, "A"),
        ],
        [c, d, e, g, z, m1, m7, m8, m9],
    )

    # ── N branch (out-of-Africa via L3) ────────────────────────────
    a2 = _node(
        "A2",
        [
            _mt_snp("i5008027", 8027, "A"),
            _mt_snp("i5016111", 16111, "T"),
        ],
    )
    a4 = _node(
        "A4",
        [
            _mt_snp("i5009347", 9347, "G"),
            _mt_snp("i5014308", 14308, "A"),
        ],
    )
    a5 = _node(
        "A5",
        [
            _mt_snp("i5011884", 11884, "G"),
        ],
    )
    a = _node(
        "A",
        [
            _mt_snp("i5000235", 235, "G"),
            _mt_snp("i5000663", 663, "G"),
            _mt_snp("i5001736", 1736, "G"),
            _mt_snp("i5004824", 4824, "G"),
        ],
        [a2, a4, a5],
    )

    ii = _node(
        "I",
        [
            _mt_snp("i5001719", 1719, "A"),
            _mt_snp("i5010034", 10034, "C"),
            _mt_snp("i5015043", 15043, "A"),
            _mt_snp("i5016129", 16129, "C"),
        ],
    )

    n1a = _node(
        "N1a",
        [
            _mt_snp("i5000152", 152, "C"),
            _mt_snp("i5006365", 6365, "C"),
            _mt_snp("i5010398", 10398, "G"),
        ],
    )
    n1b = _node(
        "N1b",
        [
            _mt_snp("i5006261", 6261, "A"),
            _mt_snp("i5012501", 12501, "A"),
        ],
    )
    n1 = _node(
        "N1",
        [
            _mt_snp("i5006365", 6365, "C"),
            _mt_snp("i5010398", 10398, "G"),
        ],
        [n1a, n1b],
    )

    n9a = _node(
        "N9a",
        [
            _mt_snp("i5005231", 5231, "A"),
            _mt_snp("i5012358", 12358, "G"),
        ],
    )
    n9b = _node(
        "N9b",
        [
            _mt_snp("i5001598", 1598, "A"),
            _mt_snp("i5012549", 12549, "G"),
        ],
    )
    n9 = _node(
        "N9",
        [
            _mt_snp("i5005417", 5417, "A"),
            _mt_snp("i5012705", 12705, "C"),
        ],
        [n9a, n9b],
    )

    s1 = _node(
        "S1",
        [
            _mt_snp("i5010238", 10238, "C"),
        ],
    )
    s2 = _node(
        "S2",
        [
            _mt_snp("i5014364", 14364, "T"),
        ],
    )
    s = _node(
        "S",
        [
            _mt_snp("i5001359", 1359, "C"),
            _mt_snp("i5008404", 8404, "T"),
        ],
        [s1, s2],
    )

    w1 = _node(
        "W1",
        [
            _mt_snp("i5012669", 12669, "C"),
        ],
    )
    w3 = _node(
        "W3",
        [
            _mt_snp("i5005460", 5460, "A"),
        ],
    )
    w = _node(
        "W",
        [
            _mt_snp("i5000189", 189, "G"),
            _mt_snp("i5000204", 204, "C"),
            _mt_snp("i5000207", 207, "A"),
            _mt_snp("i5001243", 1243, "C"),
        ],
        [w1, w3],
    )

    x1 = _node(
        "X1",
        [
            _mt_snp("i5006253", 6253, "C"),
        ],
    )
    x2a = _node(
        "X2a",
        [
            _mt_snp("i5012397", 12397, "G"),
        ],
    )
    x2b = _node(
        "X2b",
        [
            _mt_snp("i5001719", 1719, "A"),
        ],
    )
    x2 = _node(
        "X2",
        [
            _mt_snp("i5001719", 1719, "A"),
            _mt_snp("i5008913", 8913, "A"),
        ],
        [x2a, x2b],
    )
    x = _node(
        "X",
        [
            _mt_snp("i5006221", 6221, "C"),
            _mt_snp("i5006371", 6371, "C"),
            _mt_snp("i5013966", 13966, "G"),
        ],
        [x1, x2],
    )

    y1 = _node(
        "Y1",
        [
            _mt_snp("i5007933", 7933, "G"),
        ],
    )
    y2 = _node(
        "Y2",
        [
            _mt_snp("i5003834", 3834, "A"),
        ],
    )
    y_mt = _node(
        "Y_mt",
        [
            _mt_snp("i5007933", 7933, "G"),
            _mt_snp("i5010398", 10398, "G"),
        ],
        [y1, y2],
    )

    # ── R branch (sub-branch of N) ────────────────────────────────
    b4a = _node(
        "B4a",
        [
            _mt_snp("i5006719", 6719, "C"),
            _mt_snp("i5009123", 9123, "A"),
        ],
    )
    b4b = _node(
        "B4b",
        [
            _mt_snp("i5003453", 3453, "G"),
            _mt_snp("i5004820", 4820, "A"),
        ],
    )
    b4c = _node(
        "B4c",
        [
            _mt_snp("i5003497", 3497, "T"),
        ],
    )
    b4 = _node(
        "B4",
        [
            _mt_snp("i5003453", 3453, "G"),
            _mt_snp("i5009123", 9123, "A"),
        ],
        [b4a, b4b, b4c],
    )
    b5 = _node(
        "B5",
        [
            _mt_snp("i5000210", 210, "G"),
            _mt_snp("i5001809", 1809, "C"),
            _mt_snp("i5006960", 6960, "C"),
        ],
    )
    b = _node(
        "B",
        [
            _mt_snp("i5000827", 827, "G"),
            _mt_snp("i5008281", 8281, "C"),
            _mt_snp("i5015301", 15301, "A"),
        ],
        [b4, b5],
    )

    f1a = _node(
        "F1a",
        [
            _mt_snp("i5003970", 3970, "T"),
            _mt_snp("i5013759", 13759, "A"),
        ],
    )
    f1b = _node(
        "F1b",
        [
            _mt_snp("i5007828", 7828, "G"),
        ],
    )
    f1 = _node(
        "F1",
        [
            _mt_snp("i5003970", 3970, "T"),
            _mt_snp("i5012406", 12406, "A"),
        ],
        [f1a, f1b],
    )
    f2 = _node(
        "F2",
        [
            _mt_snp("i5004218", 4218, "C"),
            _mt_snp("i5013928", 13928, "C"),
        ],
    )
    f = _node(
        "F",
        [
            _mt_snp("i5000249", 249, "A"),
            _mt_snp("i5006392", 6392, "C"),
            _mt_snp("i5010310", 10310, "A"),
        ],
        [f1, f2],
    )

    p = _node(
        "P",
        [
            _mt_snp("i5001438", 1438, "G"),
            _mt_snp("i5003705", 3705, "T"),
            _mt_snp("i5016176", 16176, "G"),
        ],
    )

    # ── HV → H branch (most common European haplogroup) ──────────
    h1a1 = _node(
        "H1a1",
        [
            _mt_snp("i5014587", 14587, "G"),
        ],
    )
    h1a = _node(
        "H1a",
        [
            _mt_snp("rs1000390", 13290, "T"),
            _mt_snp("i5013404", 13404, "C"),
        ],
        [h1a1],
    )
    h1b = _node(
        "H1b",
        [
            _mt_snp("i5003010", 3010, "A"),
            _mt_snp("i5016189", 16189, "C"),
        ],
    )
    h1c = _node(
        "H1c",
        [
            _mt_snp("i5004310", 4310, "G"),
        ],
    )
    h1e = _node(
        "H1e",
        [
            _mt_snp("i5003796", 3796, "G"),
            _mt_snp("i5009066", 9066, "G"),
        ],
    )
    h1 = _node(
        "H1",
        [
            _mt_snp("i5003010", 3010, "A"),
        ],
        [h1a, h1b, h1c, h1e],
    )

    h2a1 = _node(
        "H2a1",
        [
            # PhyloTree Build 17 H2a1 = G951A + C16354T. The prior bundle used
            # inherited H2a m.4769 plus a mistyped 15354C entry, which let rCRS
            # (H2a2a1) satisfy the off-spine H2a1 sibling (#1648).
            _mt_snp("i5000951", 951, "A"),
            _mt_snp("i5016354", 16354, "T"),
        ],
    )
    h2a2a1 = _node(
        "H2a2a1",
        [
            _mt_snp("i5000263", 263, "A"),
        ],
    )
    h2a2a = _node(
        "H2a2a",
        [
            _mt_snp("i5008860", 8860, "A"),
            _mt_snp("i5015326", 15326, "A"),
        ],
        [h2a2a1],
    )
    h2a2 = _node(
        "H2a2",
        [
            _mt_snp("i5000750", 750, "A"),
        ],
        [h2a2a],
    )
    h2a = _node(
        "H2a",
        [
            # H2a is on the rCRS spine (H2a2a1). Build 17 gives G4769A, so
            # the derived allele for an rCRS-like H2a carrier is A. The old
            # extra 9380A marker is not part of the H2a row and conflicts with
            # the rCRS base 9380G (#1648).
            _mt_snp("i5004769", 4769, "A"),
        ],
        [h2a1, h2a2],
    )
    h2 = _node(
        "H2",
        [
            # Build 17 H2 = G1438A; rCRS carries the derived H2 allele A.
            _mt_snp("i5001438", 1438, "A"),
        ],
        [h2a],
    )

    h3 = _node(
        "H3",
        [
            _mt_snp("i5006776", 6776, "C"),
        ],
    )
    h4 = _node(
        "H4",
        [
            _mt_snp("i5003992", 3992, "T"),
            _mt_snp("i5005004", 5004, "C"),
        ],
    )
    h5a = _node(
        "H5a",
        [
            _mt_snp("i5004336", 4336, "C"),
            _mt_snp("i5016304", 16304, "C"),
        ],
    )
    h5 = _node(
        "H5",
        [
            _mt_snp("i5000456", 456, "T"),
            _mt_snp("i5016304", 16304, "C"),
        ],
        [h5a],
    )
    h6a = _node(
        "H6a",
        [
            _mt_snp("i5003915", 3915, "A"),
        ],
    )
    h6 = _node(
        "H6",
        [
            _mt_snp("i5003915", 3915, "A"),
            _mt_snp("i5007337", 7337, "A"),
        ],
        [h6a],
    )
    h7 = _node(
        "H7",
        [
            _mt_snp("i5004793", 4793, "G"),
        ],
    )
    h10 = _node(
        "H10",
        [
            _mt_snp("i5014470", 14470, "C"),
        ],
    )
    h11 = _node(
        "H11",
        [
            _mt_snp("i5008448", 8448, "C"),
            _mt_snp("i5013101", 13101, "A"),
        ],
    )
    h13a = _node(
        "H13a",
        [
            _mt_snp("i5002259", 2259, "T"),
        ],
    )
    h13 = _node(
        "H13",
        [
            _mt_snp("i5002259", 2259, "T"),
            _mt_snp("i5014872", 14872, "T"),
        ],
        [h13a],
    )

    h = _node(
        "H",
        [
            # H is defined by G2706A: the DERIVED allele is A. rCRS is haplogroup
            # H2a2a1, so it carries the derived base (Ensembl GRCh37 MT:2706=A);
            # the prior "G" was the ancestral allele, which scored every true H
            # carrier (~40-45% of Europeans) as conflicting and blocked H (#1579).
            _mt_snp("i5002706", 2706, "A"),
            # (removed) rs1000687 @ m.13252 was spurious: rs1000687 is an autosomal
            # chr11:133005679 dbSNP variant, not an mtDNA marker, so it never
            # matched a real MT call and its conflict blocked descent into H (#1579).
        ],
        [h1, h2, h3, h4, h5, h6, h7, h10, h11, h13],
    )

    # ── V branch ───────────────────────────────────────────────────
    v1 = _node(
        "V1",
        [
            _mt_snp("i5004732", 4732, "G"),
        ],
    )
    v7 = _node(
        "V7",
        [
            _mt_snp("i5005263", 5263, "T"),
        ],
    )
    v = _node(
        "V",
        [
            _mt_snp("i5004580", 4580, "A"),
            _mt_snp("i5015904", 15904, "C"),
        ],
        [v1, v7],
    )

    hv0 = _node(
        "HV0",
        [
            # Build 17 HV0 is T72C. The previous 73G entry modeled a recurrent
            # A73G marker, not the HV0-defining mutation (#1648).
            _mt_snp("i5000072", 72, "C"),
        ],
        [v],
    )
    hv1 = _node(
        "HV1",
        [
            _mt_snp("i5016067", 16067, "T"),
        ],
    )

    hv = _node(
        "HV",
        [
            # HV is defined by T14766C: the DERIVED allele is C. rCRS (H2a2a1 ⊂ HV)
            # carries it (Ensembl GRCh37 MT:14766=C); the prior "T" was ancestral,
            # blocking every true HV/H carrier as conflicting (#1579).
            _mt_snp("i5014766", 14766, "C"),
        ],
        [h, hv0, hv1],
    )

    # ── J branch ───────────────────────────────────────────────────
    j1b = _node(
        "J1b",
        [
            _mt_snp("i5008269", 8269, "A"),
            _mt_snp("i5015452", 15452, "A"),
        ],
    )
    j1c = _node(
        "J1c",
        [
            _mt_snp("i5009055", 9055, "A"),
            _mt_snp("i5013708", 13708, "A"),
        ],
    )
    j1d = _node(
        "J1d",
        [
            _mt_snp("i5011251", 11251, "G"),
        ],
    )
    j1 = _node(
        "J1",
        [
            _mt_snp("i5003010", 3010, "A"),
            _mt_snp("i5013708", 13708, "A"),
        ],
        [j1b, j1c, j1d],
    )
    j2a = _node(
        "J2a",
        [
            _mt_snp("i5007476", 7476, "T"),
            _mt_snp("i5015257", 15257, "A"),
        ],
    )
    j2b = _node(
        "J2b",
        [
            _mt_snp("i5006261", 6261, "A"),
            _mt_snp("i5013708", 13708, "A"),
        ],
    )
    j2 = _node(
        "J2",
        [
            _mt_snp("i5007476", 7476, "T"),
        ],
        [j2a, j2b],
    )
    j = _node(
        "J",
        [
            _mt_snp("i5000295", 295, "T"),
            _mt_snp("i5000489", 489, "C"),
            _mt_snp("i5010398", 10398, "G"),
            _mt_snp("i5012612", 12612, "G"),
            _mt_snp("i5016069", 16069, "T"),
        ],
        [j1, j2],
    )

    # ── T branch ───────────────────────────────────────────────────
    t1a = _node(
        "T1a",
        [
            _mt_snp("i5006253", 6253, "C"),
            _mt_snp("i5016163", 16163, "G"),
        ],
    )
    t1 = _node(
        "T1",
        [
            _mt_snp("i5006185", 6185, "C"),
            _mt_snp("i5016189", 16189, "C"),
        ],
        [t1a],
    )
    t2a = _node(
        "T2a",
        [
            _mt_snp("i5011812", 11812, "G"),
        ],
    )
    t2b = _node(
        "T2b",
        [
            _mt_snp("i5005147", 5147, "A"),
            _mt_snp("i5015907", 15907, "G"),
        ],
    )
    t2c = _node(
        "T2c",
        [
            _mt_snp("i5006489", 6489, "G"),
        ],
    )
    t2e = _node(
        "T2e",
        [
            _mt_snp("i5007859", 7859, "C"),
        ],
    )
    t2f = _node(
        "T2f",
        [
            _mt_snp("i5012633", 12633, "G"),
        ],
    )
    t2 = _node(
        "T2",
        [
            _mt_snp("i5011812", 11812, "G"),
        ],
        [t2a, t2b, t2c, t2e, t2f],
    )
    t = _node(
        "T",
        [
            _mt_snp("i5000709", 709, "A"),
            _mt_snp("i5001888", 1888, "A"),
            _mt_snp("i5004917", 4917, "G"),
            _mt_snp("i5008697", 8697, "A"),
            _mt_snp("i5010463", 10463, "C"),
            _mt_snp("i5013368", 13368, "A"),
            _mt_snp("i5016294", 16294, "T"),
        ],
        [t1, t2],
    )

    # ── U branch ───────────────────────────────────────────────────
    u1a = _node(
        "U1a",
        [
            _mt_snp("i5006026", 6026, "T"),
        ],
    )
    u1b = _node(
        "U1b",
        [
            _mt_snp("i5004991", 4991, "A"),
        ],
    )
    u1 = _node(
        "U1",
        [
            _mt_snp("i5003531", 3531, "A"),
            _mt_snp("i5007581", 7581, "C"),
        ],
        [u1a, u1b],
    )

    u2e = _node(
        "U2e",
        [
            _mt_snp("i5003720", 3720, "G"),
        ],
    )
    u2 = _node(
        "U2",
        [
            _mt_snp("i5003720", 3720, "G"),
            _mt_snp("i5016051", 16051, "G"),
        ],
        [u2e],
    )

    u3a = _node(
        "U3a",
        [
            _mt_snp("i5003834", 3834, "A"),
        ],
    )
    u3b = _node(
        "U3b",
        [
            _mt_snp("i5009266", 9266, "G"),
        ],
    )
    u3 = _node(
        "U3",
        [
            _mt_snp("i5001811", 1811, "G"),
            _mt_snp("i5015454", 15454, "C"),
        ],
        [u3a, u3b],
    )

    u4a = _node(
        "U4a",
        [
            _mt_snp("i5005999", 5999, "C"),
        ],
    )
    u4b = _node(
        "U4b",
        [
            _mt_snp("i5001811", 1811, "G"),
        ],
    )
    u4c = _node(
        "U4c",
        [
            _mt_snp("i5011332", 11332, "T"),
        ],
    )
    u4 = _node(
        "U4",
        [
            _mt_snp("i5003714", 3714, "G"),
            _mt_snp("i5011339", 11339, "C"),
        ],
        [u4a, u4b, u4c],
    )

    u5a1 = _node(
        "U5a1",
        [
            _mt_snp("i5014793", 14793, "G"),
            _mt_snp("i5016256", 16256, "T"),
        ],
    )
    u5a2 = _node(
        "U5a2",
        [
            _mt_snp("i5001700", 1700, "C"),
        ],
    )
    u5a = _node(
        "U5a",
        [
            _mt_snp("i5014793", 14793, "G"),
        ],
        [u5a1, u5a2],
    )
    u5b1 = _node(
        "U5b1",
        [
            _mt_snp("i5005656", 5656, "G"),
            _mt_snp("i5012618", 12618, "A"),
        ],
    )
    u5b2 = _node(
        "U5b2",
        [
            _mt_snp("i5001721", 1721, "C"),
        ],
    )
    u5b = _node(
        "U5b",
        [
            _mt_snp("i5007768", 7768, "G"),
        ],
        [u5b1, u5b2],
    )
    u5 = _node(
        "U5",
        [
            _mt_snp("i5003197", 3197, "C"),
            _mt_snp("i5009477", 9477, "A"),
        ],
        [u5a, u5b],
    )

    u6a = _node(
        "U6a",
        [
            _mt_snp("i5016219", 16219, "G"),
        ],
    )
    u6 = _node(
        "U6",
        [
            _mt_snp("i5003348", 3348, "G"),
        ],
        [u6a],
    )

    u7 = _node(
        "U7",
        [
            _mt_snp("i5012308", 12308, "G"),
            _mt_snp("i5016309", 16309, "G"),
        ],
    )
    u8a = _node(
        "U8a",
        [
            _mt_snp("i5007028", 7028, "T"),
        ],
    )
    u8b = _node(
        "U8b",
        [
            _mt_snp("i5003480", 3480, "G"),
        ],
    )
    u8 = _node(
        "U8",
        [
            _mt_snp("i5009698", 9698, "C"),
        ],
        [u8a, u8b],
    )
    u9 = _node(
        "U9",
        [
            _mt_snp("i5003834", 3834, "A"),
            _mt_snp("i5011914", 11914, "A"),
        ],
    )

    u = _node(
        "U",
        [
            _mt_snp("rs1000731", 13133, "T"),
            _mt_snp("i5012308", 12308, "G"),
            _mt_snp("i5012372", 12372, "A"),
        ],
        [u1, u2, u3, u4, u5, u6, u7, u8, u9],
    )

    # ── K branch (sub-branch of U8) ───────────────────────────────
    k1a = _node(
        "K1a",
        [
            _mt_snp("i5001189", 1189, "C"),
            _mt_snp("i5008311", 8311, "C"),
        ],
    )
    k1b = _node(
        "K1b",
        [
            _mt_snp("i5014167", 14167, "T"),
        ],
    )
    k1c = _node(
        "K1c",
        [
            _mt_snp("i5009716", 9716, "C"),
        ],
    )
    k1 = _node(
        "K1",
        [
            _mt_snp("i5010398", 10398, "G"),
            _mt_snp("i5010550", 10550, "G"),
        ],
        [k1a, k1b, k1c],
    )
    k2a = _node(
        "K2a",
        [
            _mt_snp("i5009716", 9716, "C"),
        ],
    )
    k2b = _node(
        "K2b",
        [
            _mt_snp("i5006152", 6152, "C"),
        ],
    )
    k2 = _node(
        "K2",
        [
            _mt_snp("i5001189", 1189, "C"),
        ],
        [k2a, k2b],
    )

    k = _node(
        "K",
        [
            _mt_snp("i5001189", 1189, "C"),
            _mt_snp("i5010550", 10550, "G"),
            _mt_snp("i5011299", 11299, "C"),
            _mt_snp("i5014798", 14798, "C"),
            _mt_snp("i5016224", 16224, "C"),
        ],
        [k1, k2],
    )
    # PhyloTree Build 17 places K within U8, not as a direct sibling of U.
    u8["children"].append(k)

    # Assemble R branch
    r0 = _node(
        "R0",
        # m.73 (A73G) is a recurrent control-region site that cannot serve as a
        # single R0 discriminator. PhyloTree defines R0 by G73A, so the rCRS/H
        # spine carries 73A (Ensembl GRCh37 MT:73=A) — but the prior bundle stored
        # R0 as 73G (inverted) AND m.73 recurs on sub-branches, so no single R0
        # allele works: as "G" it scored a true H carrier (73A) as conflicting and
        # blocked the whole rCRS spine; as "A" it would block whichever descendants
        # carry 73G (empirically the bundle's HV0/V branch). Verified by the shipped
        # classifier: dropping m.73 lets the rCRS genotype reach H while the HV0/V
        # sibling still resolves. R0 is kept as a structural node (descent is scored
        # on HV's 14766 and below) rather than mis-polarised (#1579).
        [],
        [hv],
    )

    jt = _node(
        "JT",
        [
            _mt_snp("i5000489", 489, "C"),
            _mt_snp("i5011251", 11251, "G"),
        ],
        [j, t],
    )

    r = _node(
        "R",
        [
            _mt_snp("i5012705", 12705, "C"),
            _mt_snp("i5016223", 16223, "C"),
        ],
        [r0, b, f, p, jt, u],
    )

    # Assemble N branch
    n_branch = _node(
        "N",
        [
            # Source-direction N markers, excluding positions with modeled
            # downstream reversions/opposite alleles (10398, 15301) so typed
            # descendant clades do not hard-conflict before reaching N.
            _mt_snp("i5008701", 8701, "A"),
            _mt_snp("i5009540", 9540, "T"),
            _mt_snp("i5010873", 10873, "T"),
        ],
        [a, ii, n1, n9, s, w, x, y_mt, r],
    )

    # ── L4, L5, L6 branches ───────────────────────────────────────
    l4a = _node(
        "L4a",
        [
            _mt_snp("i5007424", 7424, "A"),
            _mt_snp("i5014401", 14401, "C"),
        ],
    )
    l4b = _node(
        "L4b",
        [
            _mt_snp("i5002626", 2626, "C"),
            _mt_snp("i5010289", 10289, "G"),
        ],
    )
    l4 = _node(
        "L4",
        [
            _mt_snp("i5005108", 5108, "C"),
            _mt_snp("i5010685", 10685, "A"),
        ],
        [l4a, l4b],
    )

    l5a = _node(
        "L5a",
        [
            _mt_snp("i5007055", 7055, "G"),
        ],
    )
    l5b = _node(
        "L5b",
        [
            _mt_snp("i5011002", 11002, "G"),
        ],
    )
    l5 = _node(
        "L5",
        [
            _mt_snp("i5005108", 5108, "C"),
            _mt_snp("i5015301", 15301, "A"),
        ],
        [l5a, l5b],
    )

    l6 = _node(
        "L6",
        [
            _mt_snp("i5003396", 3396, "G"),
            _mt_snp("i5007146", 7146, "G"),
            _mt_snp("i5010589", 10589, "A"),
        ],
    )

    # ── L3 node (parent of M and N) ───────────────────────────────
    l3 = _node(
        "L3",
        [
            _mt_snp("i5000769", 769, "G"),
            _mt_snp("i5001018", 1018, "G"),
            _mt_snp("i5016311", 16311, "T"),
        ],
        [l3a, l3b, l3d, l3e, l3f, m_branch, n_branch],
    )

    # ── Root ───────────────────────────────────────────────────────
    root = _node("mt-MRCA", [], [l0, l1, l2, l3, l4, l5, l6])

    return root


def build_y_tree() -> dict[str, Any]:
    """Build the source-audited, array-reportable Y-chromosome tree."""
    source_issues = _validate_y_source(_Y_SOURCE)
    if source_issues:
        raise ValueError(
            f"Y source validation failed with {len(source_issues)} issues:\n"
            + "\n".join(f"  - {issue}" for issue in source_issues)
        )
    return _build_y_tree_from_source(_Y_SOURCE)


# ── Tree statistics helpers ─────────────────────────────────────────────


def _count_nodes(node: dict[str, Any]) -> int:
    """Count total haplogroup nodes in a tree."""
    count = 1
    for child in node.get("children", []):
        count += _count_nodes(child)
    return count


def _count_snps(node: dict[str, Any]) -> int:
    """Count total defining SNPs across all nodes in a tree."""
    count = len(node.get("defining_snps", []))
    for child in node.get("children", []):
        count += _count_snps(child)
    return count


def _collect_snp_rsids(node: dict[str, Any]) -> set[str]:
    """Collect all unique SNP rsids in a tree."""
    rsids = {s["rsid"] for s in node.get("defining_snps", [])}
    for child in node.get("children", []):
        rsids |= _collect_snp_rsids(child)
    return rsids


def _max_depth(node: dict[str, Any], depth: int = 0) -> int:
    """Get maximum depth of the tree."""
    if not node.get("children"):
        return depth
    return max(_max_depth(c, depth + 1) for c in node["children"])


def _validate_tree(node: dict[str, Any], path: str = "") -> list[str]:
    """Validate tree structure and return list of issues."""
    issues: list[str] = []
    current_path = f"{path}/{node['haplogroup']}" if path else node["haplogroup"]

    if "haplogroup" not in node:
        issues.append(f"Missing 'haplogroup' at {current_path}")
    if "defining_snps" not in node:
        issues.append(f"Missing 'defining_snps' at {current_path}")

    for snp in node.get("defining_snps", []):
        if not all(k in snp for k in ("rsid", "pos", "allele")):
            issues.append(f"Incomplete SNP at {current_path}: {snp}")
        if "pos" in snp and not isinstance(snp["pos"], int):
            issues.append(f"Non-integer pos at {current_path}: {snp}")
        if "allele" in snp and snp["allele"] not in ("A", "C", "G", "T"):
            issues.append(f"Invalid allele at {current_path}: {snp}")

    for child in node.get("children", []):
        issues.extend(_validate_tree(child, current_path))

    return issues


def _iter_snps_with_path(
    node: dict[str, Any], path: str = ""
) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Return defining SNPs with their haplogroup path for validation messages."""
    current_path = f"{path}/{node['haplogroup']}" if path else node["haplogroup"]
    records = [(current_path, snp) for snp in node.get("defining_snps", [])]
    for child in node.get("children", []):
        records.extend(_iter_snps_with_path(child, current_path))
    return tuple(records)


def _is_related_y_path(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    """Return whether two Y-tree paths are the same or ancestor/descendant."""
    return left == right or left[: len(right)] == right or right[: len(left)] == left


def _validate_y_cross_clade_duplicates(node: dict[str, Any]) -> list[str]:
    """Reject identifier or locus reuse that can inflate or divert Y evidence."""
    issues: list[str] = []
    locations: dict[str, list[tuple[str, ...]]] = {}
    position_locations: dict[int, list[tuple[str, ...]]] = {}

    for path, snp in _iter_snps_with_path(node):
        parsed_path = tuple(path.split("/"))
        locations.setdefault(snp["rsid"], []).append(parsed_path)
        position_locations.setdefault(snp["pos"], []).append(parsed_path)

    for rsid, paths in sorted(locations.items()):
        if len(paths) < 2:
            continue
        joined_paths = ", ".join("/".join(path) for path in paths)
        qualifier = (
            "unrelated Y clades"
            if any(
                not _is_related_y_path(left, right)
                for index, left in enumerate(paths)
                for right in paths[index + 1 :]
            )
            else "the same Y lineage"
        )
        issues.append(f"{rsid} is reused across {qualifier}: {joined_paths}")

    for pos, paths in sorted(position_locations.items()):
        if len(paths) < 2:
            continue
        joined_paths = ", ".join("/".join(path) for path in paths)
        issues.append(f"GRCh37 Y:{pos} is reused by multiple defining records: {joined_paths}")

    return issues


def _validate_audited_y_rsids(node: dict[str, Any]) -> list[str]:
    """Require every Y marker to match the complete source-backed whitelist."""
    issues: list[str] = []
    seen: set[str] = set()

    for path, snp in _iter_snps_with_path(node):
        rsid = snp.get("rsid")
        excluded_reason = _EXCLUDED_Y_RSIDS.get(rsid)
        if excluded_reason is not None:
            issues.append(f"{rsid} at {path} is excluded from the Y tree: {excluded_reason}")
            continue

        reference = _AUDITED_Y_RSID_REFERENCE.get(rsid)
        if reference is None:
            issues.append(f"{rsid} at {path} is absent from the audited Y marker registry")
            continue

        seen.add(rsid)
        if snp.get("pos") != reference["pos"]:
            issues.append(
                f"{rsid} at {path} has pos {snp.get('pos')}; expected GRCh37 Y:{reference['pos']}"
            )
        if snp.get("allele") not in reference["alleles"]:
            issues.append(
                f"{rsid} at {path} has allele {snp.get('allele')!r}; "
                f"expected one of {reference['alleles']}"
            )
        if snp.get("allele") != reference["allele"]:
            issues.append(
                f"{rsid} at {path} has defining allele {snp.get('allele')!r}; "
                f"expected derived allele {reference['allele']!r}"
            )
        expected_clade = reference.get("clade")
        if expected_clade is not None and path.rsplit("/", 1)[-1] != expected_clade:
            issues.append(f"{rsid} at {path} defines the wrong clade; expected {expected_clade}")

    missing = set(_AUDITED_Y_RSID_REFERENCE) - seen
    for rsid in sorted(missing):
        issues.append(f"Audited Y rsID {rsid} is missing from the Y tree")

    return issues


def _validate_y_reportability(
    node: dict[str, Any],
    trusted_single_marker_rsids: frozenset[str],
    ancestor_rsids: frozenset[str] = frozenset(),
    ancestor_positions: frozenset[int] = frozenset(),
) -> list[str]:
    """Require every emitted non-root Y node to carry terminal-grade evidence."""
    issues: list[str] = []
    current_rsids = frozenset(snp["rsid"] for snp in node.get("defining_snps", []))
    current_positions = frozenset(snp["pos"] for snp in node.get("defining_snps", []))
    if node["haplogroup"] != "Y-Adam":
        specific = [
            snp
            for snp in node.get("defining_snps", [])
            if snp["rsid"] not in ancestor_rsids and snp["pos"] not in ancestor_positions
        ]
        if not specific:
            issues.append(f"Y node {node['haplogroup']} has no ancestor-distinguishing marker")
        elif node.get("children") and len(specific) < 2:
            trusted = len(specific) == 1 and specific[0]["rsid"] in trusted_single_marker_rsids
            if not trusted:
                issues.append(
                    f"Internal Y node {node['haplogroup']} has only {len(specific)} "
                    "specific marker(s) without a trusted-single marker"
                )

    next_rsids = ancestor_rsids | current_rsids
    next_positions = ancestor_positions | current_positions
    for child in node.get("children", []):
        issues.extend(
            _validate_y_reportability(
                child,
                trusted_single_marker_rsids,
                next_rsids,
                next_positions,
            )
        )
    return issues


# ── Bundle assembly ─────────────────────────────────────────────────────


def build_bundle() -> dict[str, Any]:
    """Assemble the complete haplogroup bundle."""
    mt_tree = build_mt_tree()
    y_tree = build_y_tree()

    # Validate trees
    mt_issues = _validate_tree(mt_tree)
    y_issues = _validate_tree(y_tree)
    y_reference_issues = _validate_audited_y_rsids(y_tree)
    y_duplicate_issues = _validate_y_cross_clade_duplicates(y_tree)
    trusted_y_markers = frozenset(_Y_SOURCE["assignment"]["trusted_single_marker_terminal_rsids"])
    y_reportability_issues = _validate_y_reportability(y_tree, trusted_y_markers)
    if mt_issues or y_issues or y_reference_issues or y_duplicate_issues or y_reportability_issues:
        all_issues = (
            mt_issues + y_issues + y_reference_issues + y_duplicate_issues + y_reportability_issues
        )
        raise ValueError(
            f"Tree validation failed with {len(all_issues)} issues:\n"
            + "\n".join(f"  - {i}" for i in all_issues)
        )

    mt_snp_rsids = _collect_snp_rsids(mt_tree)
    y_snp_rsids = _collect_snp_rsids(y_tree)

    bundle = {
        "module": "haplogroup",
        "version": BUNDLE_VERSION,
        "description": (
            "PhyloTree mtDNA + source-audited Y-chromosome haplogroup defining SNP "
            "trees for haplogroup assignment via tree-walk algorithm. "
            "SNPs filtered to 23andMe v5-era array coverage. Resolution varies "
            "with the markers typed by each array revision."
        ),
        "build": BUILD,
        "assignment": {
            "Y": _Y_SOURCE["assignment"],
        },
        "sources": {
            "mt": {
                "name": "PhyloTree",
                "version": "Build 17",
                "reference": "van Oven M, Kayser M. Updated comprehensive "
                "phylogenetic tree of global human mitochondrial DNA "
                "variation. Hum Mutat. 2009;30(2):E386-E394.",
                "url": "https://www.phylotree.org",
            },
            "Y": {
                **_Y_SOURCE["source"],
                "references": _Y_SOURCE["references"],
                "current_validation": _Y_SOURCE["current_validation"],
                "source_topology_non_root_nodes": _Y_SOURCE["source_topology_non_root_nodes"],
                "omitted_nodes": _Y_SOURCE["omitted_nodes"],
            },
        },
        "trees": {
            "mt": mt_tree,
            "Y": y_tree,
        },
        "stats": {
            "mt_haplogroups": _count_nodes(mt_tree),
            "mt_defining_snps": _count_snps(mt_tree),
            "mt_unique_snps": len(mt_snp_rsids),
            "mt_max_depth": _max_depth(mt_tree),
            "y_haplogroups": _count_nodes(y_tree),
            "y_defining_snps": _count_snps(y_tree),
            "y_unique_snps": len(y_snp_rsids),
            "y_max_depth": _max_depth(y_tree),
            "y_source_haplogroups": _Y_SOURCE["source_topology_non_root_nodes"] + 1,
            "y_omitted_haplogroups": len(_Y_SOURCE["omitted_nodes"]),
            "total_haplogroups": _count_nodes(mt_tree) + _count_nodes(y_tree),
            "total_defining_snps": _count_snps(mt_tree) + _count_snps(y_tree),
            "total_unique_snps": len(mt_snp_rsids | y_snp_rsids),
        },
    }
    return bundle


def print_stats(bundle: dict[str, Any]) -> None:
    """Print bundle statistics."""
    stats = bundle["stats"]
    print("=" * 60)
    print("Haplogroup Bundle Statistics")
    print("=" * 60)
    print(f"  Version:            {bundle['version']}")
    print(f"  Build:              {bundle['build']}")
    print()
    print("  mtDNA (PhyloTree):")
    print(f"    Haplogroups:      {stats['mt_haplogroups']}")
    print(f"    Defining SNPs:    {stats['mt_defining_snps']}")
    print(f"    Unique SNPs:      {stats['mt_unique_snps']}")
    print(f"    Max depth:        {stats['mt_max_depth']}")
    print()
    print("  Y-chromosome (YBrowse):")
    print(f"    Haplogroups:      {stats['y_haplogroups']}")
    print(f"    Defining SNPs:    {stats['y_defining_snps']}")
    print(f"    Unique SNPs:      {stats['y_unique_snps']}")
    print(f"    Max depth:        {stats['y_max_depth']}")
    print()
    print("  Combined:")
    print(f"    Total haplogroups:  {stats['total_haplogroups']}")
    print(f"    Total defining SNPs:{stats['total_defining_snps']}")
    print(f"    Total unique SNPs:  {stats['total_unique_snps']}")
    print("=" * 60)


def write_bundle(bundle: dict[str, Any], output_path: Path) -> str:
    """Write the bundle to a JSON file.  Returns SHA-256 checksum."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    json_bytes = json.dumps(bundle, indent=2, ensure_ascii=False).encode("utf-8")
    checksum = hashlib.sha256(json_bytes).hexdigest()

    output_path.write_bytes(json_bytes)
    return checksum


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build PhyloTree + source-audited Y-tree haplogroup JSON bundle.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Output path for the JSON bundle.  Defaults to writing both "
            "tests/fixtures/haplogroup_bundle.json and "
            "backend/data/panels/haplogroup_bundle.json."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print stats without writing files.",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print bundle statistics and exit.",
    )
    args = parser.parse_args(argv)

    bundle = build_bundle()

    if args.stats or args.dry_run:
        print_stats(bundle)
        if args.dry_run:
            print("\n[dry-run] No files written.")
        return

    # Determine project root (scripts/ is one level below root)
    project_root = Path(__file__).resolve().parent.parent

    if args.output:
        outputs = [args.output]
    else:
        outputs = [
            project_root / "tests" / "fixtures" / "haplogroup_bundle.json",
            project_root / "backend" / "data" / "panels" / "haplogroup_bundle.json",
        ]

    print_stats(bundle)
    print()

    for output_path in outputs:
        checksum = write_bundle(bundle, output_path)
        size_kb = output_path.stat().st_size / 1024
        print(f"Wrote {output_path} ({size_kb:.1f} KB)")
        print(f"  SHA-256: {checksum}")

    print("\nDone.")


if __name__ == "__main__":
    main()
