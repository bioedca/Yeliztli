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
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── Version & metadata ─────────────────────────────────────────────────

BUNDLE_VERSION = "1.1.19"
BUILD = "GRCh37"
MT_SOURCE_PATH = Path(__file__).with_name("mt_haplogroup_source.json")
MT_BASELINE_SNAPSHOT_PATH = Path(__file__).with_name("mt_haplogroup_baseline_snapshot.json")
Y_SOURCE_PATH = Path(__file__).with_name("y_haplogroup_source.json")

_MT_SCHEMA_VERSION = 3
_MT_BASELINE_SNAPSHOT_SCHEMA_VERSION = 1
_MT_BASELINE_COMMIT = "e463604fc5b4af4d5887c9e9a76c2f54598ef312"
_MT_BASELINE_NORMALIZATION_COMMIT = "182dfc713cec506bb9d84088a77b54061549e43c"
# Canonical JSON SHA-256: sorted keys, compact separators, and one trailing newline.
_MT_BASELINE_SNAPSHOT_SHA256 = "f8aecb8ba02e5c2becbccfc40846bd3c8668d4b8c6de5be1761ab78c0d83a87e"
_MT_PHYLOTREE_ARCHIVE_SHA256 = "3fe8cf00a15e1ccb09235091016eef1af3a68f44dd9355dd2b7666f8f767b146"
_MT_RCRS_SHA256 = "fc392cde8e63b4d2e3a870bb97cc0626dea33d46dfb8abdebffada040f42ec92"
_MT_LEGACY_EXACT_NAMES_SHA256 = "7d968626b02229ba77f7e58a32b337621c71a1a071e4564d5e815d5c3dee4d5d"
_MT_LEGACY_V1_SEMANTIC_SHA256 = "521dedbac66952e7df628dda8da495b6e03f640b3b6765006835d805cd32d63a"
_MT_LEGACY_V1_COVERAGE_SHA256 = "375c6a5af32e22bd71026391b5a0552bfa260bac09cf6666f84bab6ea52b7947"
_MT_BASELINE_EXACT_NAMES_SHA256 = (
    "3e3386bf2d57ce5814df595576223e08addccba96c92818b7d1cf338b02bf5d9"
)
_MT_BASELINE_V1_SEMANTIC_SHA256 = (
    "c044b73c08b339d0be782306b84d593982b13242d995d541522da6f5bc9fc7c6"
)
_MT_BASELINE_V1_COVERAGE_SHA256 = (
    "d88d4491671f99175bea3c6188affb3b0bbbd31681e0f7c35103ac4f194da6e6"
)
_MT_BASELINE_V2_REGISTRY_SEMANTIC_SHA256 = (
    "3eaa8bb5a9cc33c1a892bd70a7007b8293c2698d4e541a95566f7547c914c553"
)
_MT_BASELINE_V2_COVERAGE_MEMBERSHIP_SHA256 = (
    "9e9d25bd07652d0637fde59d9292b6a4cba1c593268c2301bba1c910b9bd338b"
)
_MT_LOCKED_EXACT_NAMES_SHA256 = "2df501afa2899171549f2a4f3fedc5e16e19ce8310fe5bd3f1e63e19d07957ae"
_MT_LOCKED_EXACT_SEMANTIC_SHA256 = (
    "c2b87f89f3fc4e166bc09c9292236eec0c09099297006906ae308d41fd27db58"
)
_MT_LOCKED_EXACT_COVERAGE_MEMBERSHIP_SHA256 = (
    "3c65e61be08659aa74b243eb302ae84e84bea9ebe40c840123e91637f0e83db2"
)
_MT_BASELINE_DIRECT_MOTIF_EXACT_NAMES_SHA256 = (
    "0dc2cc812e511bc89b76fca6ed13614d8ddb75a6ebe6321bde670096c44fba61"
)
_MT_BASELINE_DIRECT_MOTIF_SEMANTIC_SHA256 = (
    "ecc1dbf4c93872031e102ee166eac50e31d6468395e5d0053357af44f8a9785a"
)
_MT_LOCKED_DIRECT_MOTIF_EXACT_NAMES_SHA256 = (
    "3a00aa587a5dfc5bf4d9c94587d0f23db3bcca25e17568d4e398748d8ce81442"
)
_MT_LOCKED_DIRECT_MOTIF_SEMANTIC_SHA256 = (
    "a8187e3ad3e284c95e4f58253f5e7bdd490c6bedce2624b25392e115d96b7938"
)
_MT_INITIAL_DIRECT_MOTIF_PENDING_NAMES_SHA256 = (
    "7b4848980e34ca1eff9739f964906d68eb4acdbbcd5e93227e17ece79296aefb"
)
_MT_INITIAL_PENDING_NAMES_SHA256 = (
    "996c2c96c22d37a2aa7edf1f4639d626ccc5199ecc5eb35984aa84204e05a591"
)
_MT_ARRAY_MANIFEST_SHA256 = "42de22517a4644884596e36b0499a4fc45f264986c63f6fb239452b88719f977"
_MT_SOURCE_METADATA_SHA256 = "5b3a3578fc208c91f6c3fdcc6d772f5071851b3604762b9e81994cf2632deb3d"
_MT_STATE_PARTITION_SHA256 = "455617bb7d15f029293d1861031239a061738aac6cb32f4047a73124ec9f2bd4"
_MT_BASELINE_EMITTED_TREE_SHA256 = (
    "02a40be2096dd8c60e6e2934ba68a813f07478117a749e60e94e0608bed21914"
)
_MT_LOCKED_EMITTED_TREE_SHA256 = "7d9847e94e3c6a62919de750823af34c410eab98a12e58361bdb558bd9be0f97"
_MT_SYNTHETIC_ROOT_NAME = "mt-MRCA"
_MT_FLATTENED_OMISSION_TYPES = frozenset(
    {
        "flattened_source_intermediate",
        "flattened_unreportable_source_intermediate",
    }
)
_MT_RETIRED_NODE_TYPE = "retired_unmapped_emitted_node"
_MT_RETIRED_NODE_BASELINES: dict[str, dict[str, Any]] = {
    "A4": {
        "former_emitted_parent": "A",
        "former_defining_snps": [
            {"rsid": "i5009347", "pos": 9347, "allele": "G"},
            {"rsid": "i5014308", "pos": 14308, "allele": "A"},
        ],
    }
}

_MT_EXPECTED_ARRAY_EXPORTS: dict[str, dict[str, Any]] = {
    "pgp_4139": {
        "filename": "pgp_4139.txt",
        "vendor": "23andMe",
        "generated": "2020-09-10",
        "role": "primary_modern_23andme",
        "sha256": "f4a37d23e75d7406afef22b55fe723eb5c8c7901823365410fd2abf988fd4619",
        "line_count": 638564,
    },
    "pgp_4162": {
        "filename": "pgp_4162.txt",
        "vendor": "23andMe",
        "generated": "2024-07-30",
        "role": "primary_modern_23andme",
        "sha256": "2e9cbdd1a69ad7b226751d2741c0b56a7d1f1625a4e6e10384239783dadefa94",
        "line_count": 643555,
    },
    "pgp_4187": {
        "filename": "pgp_4187.txt",
        "vendor": "23andMe",
        "generated": "2017-12-21",
        "role": "primary_modern_23andme",
        "sha256": "19481e7e2e94f441ce25d2d98ecbe90b3de59533c40b52242ed9572d3cb91127",
        "line_count": 638483,
    },
    "pgp_huA08F4D": {
        "filename": "pgp_huA08F4D.txt",
        "vendor": "23andMe",
        "generated": "2026-04-29",
        "role": "primary_modern_23andme",
        "sha256": "8663f40f503b4a2873ef152095d88762c6d739af72876c637e38727693bf251c",
        "line_count": 631479,
    },
    "pgp_ancestry_4190": {
        "filename": "pgp_ancestry_4190.txt",
        "vendor": "AncestryDNA",
        "generated": "2018-04-22",
        "role": "other_vendor_comparator_only",
        "sha256": "9ccba5275793a6e07fe191e1ce92eb9ea3c7095159f0a4572a3de2990d984e58",
        "line_count": 650429,
    },
    "pgp_1050": {
        "filename": "pgp_1050.txt",
        "vendor": "23andMe",
        "generated": "2014-02-01",
        "role": "historical_fifth_23andme_only",
        "sha256": "30b6e03db180e17b097e06aa94d9352ca195b2948e38bde28ced3285fb8921c7",
        "line_count": 1001437,
    },
}
_MT_EXPECTED_ARRAY_COHORTS: dict[str, dict[str, Any]] = {
    "primary_four_23andme": {"export_ids": ["pgp_4139", "pgp_4162", "pgp_4187", "pgp_huA08F4D"]},
    "historical_five_23andme_including_2014": {
        "extends": "primary_four_23andme",
        "export_ids": [
            "pgp_4139",
            "pgp_4162",
            "pgp_4187",
            "pgp_huA08F4D",
            "pgp_1050",
        ],
    },
}

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


def _load_mt_source(path: Path = MT_SOURCE_PATH) -> dict[str, Any]:
    """Load the source-backed registry for explicitly audited mtDNA nodes."""
    return json.loads(path.read_text(encoding="utf-8"))


def _load_mt_baseline_snapshot(
    path: Path = MT_BASELINE_SNAPSHOT_PATH,
) -> dict[str, Any]:
    """Load the immutable schema-v2 projection archive for historical locks."""
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
_MT_SOURCE = _load_mt_source()
_MT_BASELINE_SNAPSHOT = _load_mt_baseline_snapshot()
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
            _mt_snp("i5005096", 5096, "C"),
        ],
    )
    l0a2 = _node(
        "L0a2",
        [
            _mt_snp("i5000064", 64, "T"),
            _mt_snp("i5005147", 5147, "A"),
            _mt_snp("i5005711", 5711, "G"),
            _mt_snp("i5006257", 6257, "A"),
            _mt_snp("i5008460", 8460, "G"),
            _mt_snp("i5011172", 11172, "G"),
            _mt_snp("i5016129", 16129, "G"),
        ],
    )
    l0a = _node(
        "L0a",
        [
            # Build 17 places m.11176 on the flattened L0a'g source segment.
            # It is retained here with source ownership in the provenance
            # registry; the shared earlier L0 source segments stay non-emitted.
            _mt_snp("i5011176", 11176, "A"),
            _mt_snp("i5005231", 5231, "A"),
            _mt_snp("i5005460", 5460, "A"),
            _mt_snp("i5014308", 14308, "C"),
        ],
        [l0a1, l0a2],
    )

    l0b = _node(
        "L0b",
        [
            _mt_snp("i5006719", 6719, "C"),
            _mt_snp("i5015106", 15106, "A"),
            _mt_snp("i5015622", 15622, "C"),
            _mt_snp("i5016051", 16051, "G"),
            _mt_snp("i5016164", 16164, "G"),
        ],
    )
    l0d1 = _node(
        "L0d1",
        [
            _mt_snp("i5000719", 719, "A"),
            _mt_snp("i5002706", 2706, "A"),
            _mt_snp("i5003438", 3438, "A"),
            _mt_snp("i5006266", 6266, "G"),
            _mt_snp("i5013759", 13759, "A"),
        ],
    )
    l0d2 = _node(
        "L0d2",
        [
            _mt_snp("i5003981", 3981, "G"),
            _mt_snp("i5004025", 4025, "T"),
            _mt_snp("i5004044", 4044, "G"),
            _mt_snp("i5007154", 7154, "G"),
            _mt_snp("i5011854", 11854, "C"),
            _mt_snp("i5015766", 15766, "G"),
        ],
    )
    l0d = _node(
        "L0d",
        [
            _mt_snp("i5001438", 1438, "A"),
            _mt_snp("i5004232", 4232, "C"),
            _mt_snp("i5008152", 8152, "A"),
            _mt_snp("i5008251", 8251, "A"),
            _mt_snp("i5012121", 12121, "C"),
            _mt_snp("i5015466", 15466, "A"),
            _mt_snp("i5015930", 15930, "A"),
            _mt_snp("i5015941", 15941, "C"),
            _mt_snp("i5016243", 16243, "C"),
        ],
        [l0d1, l0d2],
    )

    l0f = _node(
        "L0f",
        [
            _mt_snp("i5000207", 207, "A"),
            _mt_snp("i5004964", 4964, "T"),
            _mt_snp("i5009581", 9581, "C"),
            _mt_snp("i5009620", 9620, "T"),
            _mt_snp("i5013470", 13470, "G"),
            _mt_snp("i5014109", 14109, "T"),
            _mt_snp("i5015852", 15852, "C"),
            _mt_snp("i5016169", 16169, "T"),
            _mt_snp("i5016327", 16327, "T"),
        ],
    )
    l0k = _node(
        "L0k",
        [
            _mt_snp("i5000199", 199, "C"),
            _mt_snp("i5000850", 850, "C"),
            _mt_snp("i5001243", 1243, "C"),
            _mt_snp("i5004541", 4541, "A"),
            _mt_snp("i5004907", 4907, "C"),
            _mt_snp("i5005811", 5811, "G"),
            _mt_snp("i5008911", 8911, "C"),
            _mt_snp("i5008994", 8994, "A"),
            _mt_snp("i5009136", 9136, "G"),
            _mt_snp("i5010499", 10499, "G"),
            _mt_snp("i5010920", 10920, "T"),
            _mt_snp("i5011299", 11299, "C"),
            _mt_snp("i5011653", 11653, "G"),
            _mt_snp("i5013590", 13590, "A"),
            _mt_snp("i5013928", 13928, "C"),
            _mt_snp("i5014020", 14020, "C"),
            _mt_snp("i5014182", 14182, "C"),
            _mt_snp("i5014371", 14371, "C"),
            _mt_snp("i5016129", 16129, "G"),
            _mt_snp("i5016291", 16291, "G"),
        ],
    )

    l0 = _node(
        "L0",
        [
            _mt_snp("i5001048", 1048, "T"),
            _mt_snp("i5003516", 3516, "A"),
            _mt_snp("i5005442", 5442, "C"),
            _mt_snp("i5006185", 6185, "C"),
            _mt_snp("i5009347", 9347, "G"),
            _mt_snp("i5010589", 10589, "A"),
            _mt_snp("i5012007", 12007, "A"),
            _mt_snp("i5012720", 12720, "G"),
        ],
        [l0a, l0b, l0d, l0f, l0k],
    )

    # ── L1 branch ──────────────────────────────────────────────────
    l1b1 = _node(
        "L1b1",
        [
            _mt_snp("i5005036", 5036, "G"),
            _mt_snp("i5005046", 5046, "A"),
            _mt_snp("i5013880", 13880, "A"),
            _mt_snp("i5014203", 14203, "G"),
        ],
    )
    l1b2 = _node(
        "L1b2",
        [
            _mt_snp("i5013893", 13893, "G"),
            _mt_snp("i5016239", 16239, "T"),
        ],
    )
    l1b = _node(
        "L1b",
        [
            _mt_snp("i5000710", 710, "C"),
            _mt_snp("i5001438", 1438, "A"),
            _mt_snp("i5002768", 2768, "G"),
            _mt_snp("i5003308", 3308, "C"),
            _mt_snp("i5003693", 3693, "A"),
            _mt_snp("i5006548", 6548, "T"),
            _mt_snp("i5006827", 6827, "C"),
            _mt_snp("i5006989", 6989, "G"),
            _mt_snp("i5007867", 7867, "T"),
            _mt_snp("i5012519", 12519, "C"),
            _mt_snp("i5014769", 14769, "G"),
            _mt_snp("i5015115", 15115, "C"),
            _mt_snp("i5016126", 16126, "C"),
            _mt_snp("i5016129", 16129, "G"),
            _mt_snp("i5016270", 16270, "T"),
        ],
        [l1b1, l1b2],
    )

    l1c1 = _node(
        "L1c1",
        [
            _mt_snp("i5003796", 3796, "T"),
            _mt_snp("i5003843", 3843, "G"),
            _mt_snp("i5014148", 14148, "G"),
        ],
    )
    l1c2 = _node(
        "L1c2",
        [
            _mt_snp("i5006150", 6150, "A"),
            _mt_snp("i5006253", 6253, "C"),
            _mt_snp("i5007076", 7076, "G"),
            _mt_snp("i5007337", 7337, "A"),
            _mt_snp("i5008784", 8784, "G"),
            _mt_snp("i5008877", 8877, "C"),
            _mt_snp("i5010792", 10792, "G"),
            _mt_snp("i5010793", 10793, "T"),
            _mt_snp("i5011654", 11654, "G"),
        ],
    )
    l1c3 = _node(
        "L1c3",
        [
            _mt_snp("i5006221", 6221, "A"),
            _mt_snp("i5006917", 6917, "A"),
            _mt_snp("i5011302", 11302, "T"),
            _mt_snp("i5015226", 15226, "G"),
            _mt_snp("i5015905", 15905, "C"),
            _mt_snp("i5015978", 15978, "T"),
        ],
    )
    l1c = _node(
        "L1c",
        [
            _mt_snp("i5005951", 5951, "G"),
            _mt_snp("i5006071", 6071, "C"),
            _mt_snp("i5008027", 8027, "A"),
            _mt_snp("i5009072", 9072, "G"),
            _mt_snp("i5010586", 10586, "A"),
            _mt_snp("i5012810", 12810, "G"),
            _mt_snp("i5013485", 13485, "G"),
            _mt_snp("i5014000", 14000, "A"),
            _mt_snp("i5014911", 14911, "T"),
            _mt_snp("i5016360", 16360, "T"),
        ],
        [l1c1, l1c2, l1c3],
    )

    l1 = _node(
        "L1",
        [
            _mt_snp("i5003666", 3666, "A"),
            _mt_snp("i5007389", 7389, "C"),
            _mt_snp("i5013789", 13789, "C"),
            _mt_snp("i5014178", 14178, "C"),
            _mt_snp("i5014560", 14560, "A"),
        ],
        [l1b, l1c],
    )

    # ── L2 branch ──────────────────────────────────────────────────
    l2a1 = _node(
        "L2a1",
        [
            _mt_snp("i5000182", 182, "C"),
            _mt_snp("i5012693", 12693, "G"),
            _mt_snp("i5015784", 15784, "C"),
            _mt_snp("i5016309", 16309, "G"),
        ],
    )
    l2a2 = _node(
        "L2a2",
        [
            _mt_snp("i5009932", 9932, "A"),
        ],
    )
    l2a = _node(
        "L2a",
        [
            _mt_snp("i5007175", 7175, "C"),
        ],
        [l2a1, l2a2],
    )

    l2b1 = _node(
        "L2b1",
        [
            _mt_snp("i5000418", 418, "T"),
            _mt_snp("i5010828", 10828, "C"),
            _mt_snp("i5013924", 13924, "T"),
            _mt_snp("i5016362", 16362, "C"),
        ],
    )
    l2b = _node(
        "L2b",
        [
            _mt_snp("i5001706", 1706, "T"),
            _mt_snp("i5002358", 2358, "G"),
            _mt_snp("i5004158", 4158, "G"),
            _mt_snp("i5004767", 4767, "G"),
            _mt_snp("i5005027", 5027, "T"),
            _mt_snp("i5005331", 5331, "A"),
            _mt_snp("i5005814", 5814, "C"),
            _mt_snp("i5006713", 6713, "T"),
            _mt_snp("i5008387", 8387, "A"),
            _mt_snp("i5012948", 12948, "G"),
            _mt_snp("i5014059", 14059, "G"),
            _mt_snp("i5016114", 16114, "A"),
            _mt_snp("i5016129", 16129, "A"),
        ],
        [l2b1],
    )

    l2c = _node(
        "L2c",
        [
            _mt_snp("i5000093", 93, "G"),
            _mt_snp("i5000325", 325, "T"),
            _mt_snp("i5000680", 680, "C"),
            _mt_snp("i5003200", 3200, "A"),
            _mt_snp("i5013928", 13928, "C"),
            _mt_snp("i5013958", 13958, "C"),
            _mt_snp("i5015849", 15849, "T"),
        ],
    )
    l2d = _node(
        "L2d",
        [
            _mt_snp("i5000182", 182, "C"),
            _mt_snp("i5000456", 456, "T"),
            _mt_snp("i5000870", 870, "T"),
            _mt_snp("i5002159", 2159, "C"),
            _mt_snp("i5003254", 3254, "A"),
            _mt_snp("i5003434", 3434, "G"),
            _mt_snp("i5003693", 3693, "A"),
            _mt_snp("i5006231", 6231, "T"),
            _mt_snp("i5009554", 9554, "A"),
            _mt_snp("i5009941", 9941, "G"),
            _mt_snp("i5010955", 10955, "T"),
            _mt_snp("i5014845", 14845, "T"),
            _mt_snp("i5015777", 15777, "C"),
            _mt_snp("i5016223", 16223, "C"),
            _mt_snp("i5016354", 16354, "T"),
            _mt_snp("i5016399", 16399, "G"),
        ],
    )
    l2e = _node(
        "L2e",
        [
            _mt_snp("i5000719", 719, "A"),
            _mt_snp("i5003537", 3537, "G"),
            _mt_snp("i5004562", 4562, "G"),
            _mt_snp("i5005069", 5069, "T"),
            _mt_snp("i5009377", 9377, "G"),
            _mt_snp("i5009971", 9971, "T"),
            _mt_snp("i5011935", 11935, "C"),
            _mt_snp("i5013708", 13708, "A"),
            _mt_snp("i5015697", 15697, "C"),
            _mt_snp("i5015734", 15734, "A"),
            _mt_snp("i5015889", 15889, "C"),
            _mt_snp("i5016111", 16111, "A"),
            _mt_snp("i5016145", 16145, "A"),
            _mt_snp("i5016239", 16239, "T"),
            _mt_snp("i5016292", 16292, "T"),
            _mt_snp("i5016399", 16399, "G"),
        ],
    )

    l2 = _node(
        "L2",
        [
            _mt_snp("i5002416", 2416, "C"),
            _mt_snp("i5008206", 8206, "A"),
            _mt_snp("i5009221", 9221, "G"),
            _mt_snp("i5010115", 10115, "C"),
            _mt_snp("i5013590", 13590, "A"),
            _mt_snp("i5016390", 16390, "A"),
        ],
        [l2a, l2b, l2c, l2d, l2e],
    )

    # ── L3 branch (ancestor of M and N → out of Africa) ───────────
    l3a = _node(
        "L3a",
        [
            _mt_snp("i5012816", 12816, "T"),
            _mt_snp("i5016254", 16254, "G"),
            _mt_snp("i5016316", 16316, "G"),
        ],
    )
    l3b1 = _node(
        "L3b1",
        [
            _mt_snp("i5010373", 10373, "A"),
        ],
    )
    l3b = _node(
        "L3b",
        [
            _mt_snp("i5003450", 3450, "T"),
            _mt_snp("i5006221", 6221, "C"),
            _mt_snp("i5009449", 9449, "T"),
            _mt_snp("i5010086", 10086, "G"),
            _mt_snp("i5013105", 13105, "G"),
            _mt_snp("i5013914", 13914, "A"),
            _mt_snp("i5015311", 15311, "G"),
            _mt_snp("i5015824", 15824, "G"),
            _mt_snp("i5016124", 16124, "C"),
            _mt_snp("i5016362", 16362, "C"),
        ],
        [l3b1],
    )
    l3d = _node(
        "L3d",
        [
            # Build 17 places m.13105 on the flattened L3c'd source segment.
            _mt_snp("i5013105", 13105, "G"),
            _mt_snp("i5005147", 5147, "A"),
            _mt_snp("i5007424", 7424, "G"),
            _mt_snp("i5013886", 13886, "C"),
            _mt_snp("i5014284", 14284, "T"),
            _mt_snp("i5016124", 16124, "C"),
        ],
    )
    l3e1 = _node(
        "L3e1",
        [
            _mt_snp("i5006221", 6221, "C"),
            _mt_snp("i5014152", 14152, "G"),
            _mt_snp("i5015670", 15670, "C"),
            _mt_snp("i5015942", 15942, "C"),
            _mt_snp("i5016327", 16327, "T"),
        ],
    )
    l3e2 = _node(
        "L3e2",
        [
            # These two defining substitutions are callable only in the pinned
            # historical 2014 export; the primary-four path stops at L3e.
            _mt_snp("i5014905", 14905, "A"),
            _mt_snp("i5016320", 16320, "T"),
        ],
    )
    l3e = _node(
        "L3e",
        [
            _mt_snp("i5014212", 14212, "C"),
        ],
        [l3e1, l3e2],
    )
    l3f = _node(
        "L3f",
        [
            _mt_snp("i5003396", 3396, "C"),
            _mt_snp("i5004218", 4218, "C"),
            _mt_snp("i5015514", 15514, "C"),
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
            _mt_snp("i5014318", 14318, "C"),
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
    # Direct Build 17 motif (C8703T + G16129A!); the prior D2 record reused
    # inherited D (m.4883) and R-lineage (m.12705) markers (#1907).
    d2 = _node(
        "D2",
        [
            _mt_snp("i5008703", 8703, "T"),
            _mt_snp("i5016129", 16129, "A"),
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
            _mt_snp("i5008200", 8200, "C"),
            _mt_snp("i5015323", 15323, "A"),
            _mt_snp("i5015497", 15497, "A"),
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
            _mt_snp("i5005601", 5601, "T"),
            _mt_snp("i5013563", 13563, "G"),
        ],
        [g2a],
    )
    g = _node(
        "G",
        [
            # Build 17 places m.14569 on the flattened M12'G source segment.
            _mt_snp("i5014569", 14569, "A"),
            _mt_snp("i5004833", 4833, "G"),
            _mt_snp("i5005108", 5108, "C"),
            _mt_snp("i5016362", 16362, "C"),
        ],
        [g1, g2],
    )

    z1 = _node(
        "Z1",
        [
            _mt_snp("i5015261", 15261, "A"),
        ],
    )
    z = _node(
        "Z",
        [
            _mt_snp("i5006752", 6752, "G"),
            _mt_snp("i5009090", 9090, "C"),
            _mt_snp("i5015784", 15784, "C"),
        ],
        [z1],
    )

    m1 = _node(
        "M1",
        [
            # Build 17 places m.14110 on the flattened M1'20'51 source segment.
            _mt_snp("i5014110", 14110, "C"),
            _mt_snp("i5006446", 6446, "A"),
            _mt_snp("i5006680", 6680, "C"),
            _mt_snp("i5012950", 12950, "C"),
            _mt_snp("i5016129", 16129, "A"),
            _mt_snp("i5016249", 16249, "C"),
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
            # Build 17 also has T9824C here, but the pending M7b child still
            # carries the opposite legacy allele. Batch 05 repairs that subtree.
            _mt_snp("i5006455", 6455, "T"),
        ],
        [m7a, m7b, m7c],
    )

    m8a = _node(
        "M8a",
        [
            _mt_snp("i5006179", 6179, "A"),
            _mt_snp("i5008684", 8684, "T"),
            _mt_snp("i5014470", 14470, "C"),
        ],
    )
    m8 = _node(
        "M8",
        [
            _mt_snp("i5004715", 4715, "G"),
            _mt_snp("i5008584", 8584, "A"),
            _mt_snp("i5015487", 15487, "T"),
        ],
        # Build 17 inserts deletion-only CZ (A249d) between M8 and C/Z.  The
        # substitution-only caller cannot score that event, so flatten only CZ
        # while preserving the inherited M8 path (#1797).
        [m8a, c, z],
    )

    m9 = _node(
        "M9",
        [
            _mt_snp("i5004491", 4491, "A"),
            _mt_snp("i5016362", 16362, "C"),
        ],
    )

    m_branch = _node(
        "M",
        [
            _mt_snp("i5014783", 14783, "C"),
            _mt_snp("i5015043", 15043, "A"),
        ],
        [d, e, g, m1, m7, m8, m9],
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
            # I now descends through emitted N1/N1a, so m.1719 is inherited
            # from N1. Retain N1a1's m.15043 lineage event beside I's direct
            # T10034C and G16129A! events; the reversion still ends at A.
            _mt_snp("i5010034", 10034, "C"),
            _mt_snp("i5015043", 15043, "A"),
            _mt_snp("i5016129", 16129, "A"),
        ],
    )

    n1a = _node(
        "N1a",
        [
            _mt_snp("i5000204", 204, "C"),
            _mt_snp("i5013780", 13780, "G"),
        ],
        [ii],
    )
    n1b = _node(
        "N1b",
        [
            _mt_snp("i5006261", 6261, "A"),
        ],
    )
    n1 = _node(
        "N1",
        [
            # N1'5 is flattened between N and N1; retain its reportable
            # G1719A lineage event beside N1's direct motif (#1899).
            _mt_snp("i5001719", 1719, "A"),
            _mt_snp("i5010238", 10238, "C"),
            _mt_snp("i5012501", 12501, "A"),
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
        ],
        [n9a, n9b],
    )

    s1 = _node(
        "S1",
        [
            _mt_snp("i5014384", 14384, "C"),
            _mt_snp("i5016075", 16075, "C"),
        ],
    )
    s2 = _node(
        "S2",
        [
            _mt_snp("i5002380", 2380, "T"),
            _mt_snp("i5003438", 3438, "A"),
            _mt_snp("i5006167", 6167, "C"),
        ],
    )
    s = _node(
        "S",
        [
            _mt_snp("i5008404", 8404, "C"),
        ],
        [s1, s2],
    )

    w1 = _node(
        "W1",
        [
            _mt_snp("i5007864", 7864, "T"),
        ],
    )
    w3 = _node(
        "W3",
        [
            _mt_snp("i5001406", 1406, "C"),
        ],
    )
    w = _node(
        "W",
        [
            _mt_snp("i5000207", 207, "A"),
            _mt_snp("i5001243", 1243, "C"),
            _mt_snp("i5003505", 3505, "G"),
            _mt_snp("i5005460", 5460, "A"),
            _mt_snp("i5008251", 8251, "A"),
            _mt_snp("i5008994", 8994, "A"),
            _mt_snp("i5011947", 11947, "G"),
            _mt_snp("i5015884", 15884, "C"),
            _mt_snp("i5016292", 16292, "T"),
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
            _mt_snp("i5008913", 8913, "G"),
            _mt_snp("i5014502", 14502, "C"),
        ],
    )
    x2b = _node(
        "X2b",
        [
            _mt_snp("i5008393", 8393, "T"),
        ],
    )
    x2 = _node(
        "X2",
        [
            _mt_snp("i5001719", 1719, "A"),
        ],
        [x2a, x2b],
    )
    x = _node(
        "X",
        [
            _mt_snp("i5006221", 6221, "C"),
            _mt_snp("i5006371", 6371, "T"),
            _mt_snp("i5013966", 13966, "G"),
            _mt_snp("i5014470", 14470, "C"),
        ],
        [x1, x2],
    )

    y1 = _node(
        "Y1",
        [
            _mt_snp("i5003834", 3834, "A"),
        ],
    )
    y2 = _node(
        "Y2",
        [
            _mt_snp("i5000482", 482, "C"),
            _mt_snp("i5005147", 5147, "A"),
            _mt_snp("i5006941", 6941, "C"),
            _mt_snp("i5007859", 7859, "A"),
            _mt_snp("i5014914", 14914, "G"),
            _mt_snp("i5015244", 15244, "G"),
        ],
    )
    y_mt = _node(
        "Y_mt",
        [
            _mt_snp("i5008392", 8392, "A"),
            _mt_snp("i5010398", 10398, "G"),
            _mt_snp("i5014178", 14178, "C"),
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
            _mt_snp("i5000073", 73, "G"),
            _mt_snp("i5016162", 16162, "G"),
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
            _mt_snp("i5000239", 239, "C"),
            _mt_snp("i5016362", 16362, "C"),
            _mt_snp("i5016482", 16482, "G"),
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
            _mt_snp("i5014470", 14470, "A"),
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
            _mt_snp("i5007963", 7963, "G"),
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
            _mt_snp("i5013965", 13965, "C"),
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
            _mt_snp("i5000508", 508, "G"),
            _mt_snp("i5013020", 13020, "C"),
        ],
    )
    u2 = _node(
        "U2",
        [
            _mt_snp("i5016051", 16051, "G"),
        ],
        [u2e],
    )

    u3a = _node(
        "U3a",
        [
            # Direct Build 17 motif; m.3834A is remote U9/Y1 homoplasy (#1794).
            _mt_snp("i5006518", 6518, "T"),
            _mt_snp("i5010506", 10506, "G"),
            _mt_snp("i5013934", 13934, "T"),
            _mt_snp("i5016390", 16390, "A"),
        ],
    )
    u3b = _node(
        "U3b",
        [
            _mt_snp("i5004188", 4188, "G"),
            _mt_snp("i5009656", 9656, "C"),
            _mt_snp("i5013743", 13743, "C"),
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
            _mt_snp("i5001721", 1721, "T"),
            _mt_snp("i5013637", 13637, "G"),
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
            _mt_snp("i5000497", 497, "T"),
        ],
    )
    k1b = _node(
        "K1b",
        [
            # Direct Build 17 motif; m.14167 is not assigned to K1b (#1796).
            _mt_snp("i5005913", 5913, "A"),
        ],
    )
    # K1c is not emitted: its direct Build-17 motif is limited to recurrent
    # control-region substitutions (m.146/m.152) plus the unsupported m.498d.
    # The former m.9716 record belongs to K2 and made K1c a false sibling match.
    k1 = _node(
        "K1",
        [
            _mt_snp("i5001189", 1189, "C"),
            _mt_snp("i5010398", 10398, "G"),
        ],
        [k1a, k1b],
    )
    k2a = _node(
        "K2a",
        [
            _mt_snp("i5004561", 4561, "C"),
        ],
    )
    k2b = _node(
        "K2b",
        [
            _mt_snp("i5005231", 5231, "A"),
            _mt_snp("i5014037", 14037, "G"),
        ],
    )
    k2 = _node(
        "K2",
        [
            _mt_snp("i5009716", 9716, "C"),
        ],
        [k2a, k2b],
    )

    k = _node(
        "K",
        [
            _mt_snp("i5010550", 10550, "G"),
            _mt_snp("i5011299", 11299, "C"),
            _mt_snp("i5014798", 14798, "C"),
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
        [a, n1, n9, s, w, x, y_mt, r],
    )

    # ── L4, L5, L6 branches ───────────────────────────────────────
    l4a = _node(
        "L4a",
        [
            _mt_snp("i5003357", 3357, "A"),
            _mt_snp("i5010373", 10373, "A"),
            _mt_snp("i5011253", 11253, "C"),
            _mt_snp("i5011485", 11485, "C"),
            _mt_snp("i5012414", 12414, "C"),
            _mt_snp("i5013174", 13174, "C"),
            _mt_snp("i5016260", 16260, "T"),
        ],
    )
    l4b = _node(
        "L4b",
        [
            _mt_snp("i5003918", 3918, "A"),
        ],
    )
    l4 = _node(
        "L4",
        [
            # Build 17 also places m.5460G>A on L0a; keep this homoplasy
            # explicit because either sparse L4 marker meets the 1/2 floor.
            _mt_snp("i5005460", 5460, "A"),
            _mt_snp("i5016362", 16362, "C"),
        ],
        [l4a, l4b],
    )

    l5a = _node(
        "L5a",
        [
            _mt_snp("i5000851", 851, "G"),
            _mt_snp("i5001822", 1822, "C"),
            _mt_snp("i5005111", 5111, "T"),
            _mt_snp("i5005147", 5147, "A"),
            _mt_snp("i5005656", 5656, "G"),
            _mt_snp("i5006182", 6182, "A"),
            _mt_snp("i5006297", 6297, "C"),
            _mt_snp("i5007424", 7424, "G"),
            _mt_snp("i5008155", 8155, "A"),
            _mt_snp("i5008188", 8188, "G"),
            _mt_snp("i5009305", 9305, "A"),
            _mt_snp("i5009329", 9329, "A"),
            _mt_snp("i5011025", 11025, "C"),
            _mt_snp("i5011881", 11881, "T"),
            _mt_snp("i5012236", 12236, "A"),
            _mt_snp("i5013722", 13722, "G"),
            _mt_snp("i5014212", 14212, "C"),
            _mt_snp("i5014239", 14239, "T"),
            _mt_snp("i5014971", 14971, "C"),
            _mt_snp("i5015217", 15217, "A"),
            _mt_snp("i5015884", 15884, "A"),
            _mt_snp("i5016362", 16362, "C"),
        ],
    )
    l5b = _node(
        "L5b",
        [
            _mt_snp("i5000182", 182, "C"),
            _mt_snp("i5013105", 13105, "A"),
            _mt_snp("i5016254", 16254, "G"),
        ],
    )
    l5 = _node(
        "L5",
        [
            _mt_snp("i5003423", 3423, "C"),
            _mt_snp("i5007972", 7972, "G"),
            _mt_snp("i5012950", 12950, "G"),
            _mt_snp("i5016148", 16148, "T"),
        ],
        [l5a, l5b],
    )

    l6 = _node(
        "L6",
        [
            _mt_snp("i5000146", 146, "C"),
            _mt_snp("i5000961", 961, "C"),
            _mt_snp("i5001461", 1461, "G"),
            _mt_snp("i5004964", 4964, "T"),
            _mt_snp("i5005267", 5267, "C"),
            _mt_snp("i5006002", 6002, "G"),
            _mt_snp("i5006284", 6284, "G"),
            _mt_snp("i5009332", 9332, "T"),
            _mt_snp("i5010978", 10978, "G"),
            _mt_snp("i5011116", 11116, "C"),
            _mt_snp("i5012771", 12771, "A"),
            _mt_snp("i5013710", 13710, "G"),
            _mt_snp("i5015244", 15244, "G"),
            _mt_snp("i5015289", 15289, "C"),
            _mt_snp("i5016048", 16048, "A"),
        ],
    )

    # ── L3 node (parent of M and N) ───────────────────────────────
    l3 = _node(
        "L3",
        [
            _mt_snp("i5001018", 1018, "G"),
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


@dataclass(frozen=True)
class MtTreeOccurrence:
    """One ordered mtDNA tree occurrence, before names are deduplicated."""

    name: str
    node: dict[str, Any]
    parent: str | None
    path: tuple[str, ...]


@dataclass(frozen=True)
class MtTreeInventory:
    """Duplicate-safe inventory of the emitted mtDNA tree."""

    occurrences: tuple[MtTreeOccurrence, ...]
    by_name: dict[str, MtTreeOccurrence]
    duplicates: dict[str, tuple[MtTreeOccurrence, ...]]
    marker_bearing_names: frozenset[str]
    markerless_names: frozenset[str]
    marker_count: int
    edge_count: int


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sorted_tsv_sha256(rows: list[tuple[Any, ...]]) -> str:
    payload = "".join("\t".join(str(value) for value in row) + "\n" for row in sorted(rows))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _index_mt_tree(root: dict[str, Any]) -> MtTreeInventory:
    """Index ordered occurrences without allowing duplicate names to disappear."""
    occurrences: list[MtTreeOccurrence] = []

    def visit(current: dict[str, Any], parent: str | None, parent_path: tuple[str, ...]) -> None:
        raw_name = current.get("haplogroup")
        name = raw_name if isinstance(raw_name, str) else repr(raw_name)
        path = (*parent_path, name)
        occurrences.append(MtTreeOccurrence(name, current, parent, path))
        children = current.get("children", [])
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    visit(child, name, path)

    visit(root, None, ())
    grouped: dict[str, list[MtTreeOccurrence]] = {}
    for occurrence in occurrences:
        grouped.setdefault(occurrence.name, []).append(occurrence)
    duplicates = {name: tuple(matches) for name, matches in grouped.items() if len(matches) != 1}
    by_name = {name: matches[0] for name, matches in grouped.items() if len(matches) == 1}
    marker_bearing = frozenset(
        occurrence.name
        for occurrence in occurrences
        if isinstance(occurrence.node.get("defining_snps"), list)
        and occurrence.node["defining_snps"]
    )
    markerless = frozenset(
        occurrence.name
        for occurrence in occurrences
        if isinstance(occurrence.node.get("defining_snps"), list)
        and not occurrence.node["defining_snps"]
    )
    return MtTreeInventory(
        occurrences=tuple(occurrences),
        by_name=by_name,
        duplicates=duplicates,
        marker_bearing_names=marker_bearing,
        markerless_names=markerless,
        marker_count=sum(
            len(occurrence.node.get("defining_snps", []))
            for occurrence in occurrences
            if isinstance(occurrence.node.get("defining_snps"), list)
        ),
        edge_count=sum(occurrence.parent is not None for occurrence in occurrences),
    )


def _sorted_unique_string_list(
    value: Any,
    label: str,
    issues: list[str],
    subject: str = "mtDNA migration",
) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        issues.append(f"{subject} {label} must be a list of node names")
        return []
    if value != sorted(value) or len(value) != len(set(value)):
        issues.append(f"{subject} {label} must be sorted and unique")
    return value


def _mt_parse_substitution_notation(
    notation: Any,
) -> tuple[str, int, str, bool, int] | None:
    """Parse one Build-17 substitution token without globalizing event semantics."""
    if not isinstance(notation, str) or not notation:
        return None
    starts_group = notation.startswith("(")
    ends_group = notation.endswith(")")
    if starts_group != ends_group:
        return None
    recurrent_or_uncertain = starts_group
    token = notation[1:-1] if recurrent_or_uncertain else notation
    reversion_count = len(token) - len(token.rstrip("!"))
    if reversion_count:
        token = token[:-reversion_count]
    match = re.fullmatch(r"([ACGT])(\d+)([ACGTacgt])", token)
    if match is None:
        return None
    pos = int(match.group(2))
    if not 1 <= pos <= 16569:
        return None
    return (
        match.group(1),
        pos,
        match.group(3).upper(),
        recurrent_or_uncertain,
        reversion_count,
    )


def _mt_validate_mutation_list(
    owner: str, value: Any, issues: list[str]
) -> dict[int, tuple[str, str]]:
    """Validate one source-local motif and return its emitted substitutions."""
    if not isinstance(value, list) or not value:
        issues.append(f"Marker-exact mtDNA source node {owner} has no direct source motif")
        return {}

    emitted_substitutions: dict[int, tuple[str, str]] = {}
    seen_positions: set[int] = set()
    for mutation in value:
        if not isinstance(mutation, dict):
            issues.append(f"mtDNA source motif {owner} contains a non-object decision")
            continue
        notation = mutation.get("notation")
        mutation_type = mutation.get("mutation_type")
        pos = mutation.get("pos")
        emitted = mutation.get("emitted")
        if not _is_nonblank(notation):
            issues.append(f"mtDNA source mutation {owner}:{pos!r} has no notation")
        common_mutation_fields = {
            "notation",
            "mutation_type",
            "pos",
            "emitted",
            "omission_reason",
        }
        if mutation_type == "substitution":
            allowed_mutation_fields = common_mutation_fields | {
                "ancestral_allele",
                "derived_allele",
            }
        elif mutation_type == "insertion":
            allowed_mutation_fields = common_mutation_fields | {"inserted_sequence"}
        elif mutation_type == "deletion":
            allowed_mutation_fields = common_mutation_fields | {"deleted_sequence"}
        else:
            allowed_mutation_fields = common_mutation_fields
        if set(mutation) - allowed_mutation_fields:
            issues.append(f"mtDNA source mutation {owner}:{pos!r} has unknown fields")
        if not isinstance(pos, int) or not 1 <= pos <= 16569:
            issues.append(f"mtDNA source node {owner} has invalid source position {pos!r}")
            continue
        if pos in seen_positions:
            issues.append(f"mtDNA source node {owner} repeats source position {pos}")
        seen_positions.add(pos)
        if isinstance(notation, str) and str(pos) not in notation:
            issues.append(f"mtDNA source mutation {owner}:{pos} notation omits its position")
        if (
            isinstance(notation, str)
            and notation.endswith("!")
            and mutation_type != "substitution"
        ):
            issues.append(f"mtDNA recurrent event {owner}:{pos} must be a substitution")
        if not isinstance(emitted, bool):
            issues.append(f"mtDNA source mutation {owner}:{pos} has no emission decision")
        elif emitted and "omission_reason" in mutation:
            issues.append(
                f"Emitted mtDNA source mutation {owner}:{pos} retains an omission reason"
            )
        elif not emitted and not _is_nonblank(mutation.get("omission_reason")):
            issues.append(f"mtDNA source mutation {owner}:{pos} must have an omission reason")

        if mutation_type == "substitution":
            ancestral = mutation.get("ancestral_allele")
            derived = mutation.get("derived_allele")
            notation_parts = _mt_parse_substitution_notation(notation)
            if notation_parts is None:
                issues.append(f"mtDNA substitution {owner}:{pos} has invalid Build-17 notation")
            elif (
                notation_parts[0] != ancestral
                or notation_parts[1] != pos
                or notation_parts[2] != derived
            ):
                issues.append(
                    f"mtDNA substitution {owner}:{pos} notation disagrees with its "
                    "declared allele direction"
                )
            if ancestral not in {"A", "C", "G", "T"}:
                issues.append(
                    f"mtDNA source node {owner} position {pos} has invalid ancestral "
                    f"allele {ancestral!r}"
                )
            if derived not in {"A", "C", "G", "T"} or derived == ancestral:
                issues.append(
                    f"mtDNA source node {owner} position {pos} has invalid derived "
                    f"allele {derived!r}"
                )
            if (
                emitted is True
                and ancestral in {"A", "C", "G", "T"}
                and derived
                in {
                    "A",
                    "C",
                    "G",
                    "T",
                }
            ):
                emitted_substitutions[pos] = (ancestral, derived)
        elif mutation_type in {"insertion", "deletion"}:
            if emitted is not False:
                issues.append(
                    f"mtDNA {mutation_type} {owner}:{pos} cannot be emitted by the "
                    "substitution-only classifier"
                )
        else:
            issues.append(
                f"mtDNA source node {owner} position {pos} has unsupported mutation "
                f"type {mutation_type!r}"
            )
    return emitted_substitutions


def _mt_validate_coverage(
    node_name: str,
    marker: dict[str, Any],
    cohorts: dict[str, Any],
    issues: list[str],
) -> None:
    rsid = marker.get("rsid")
    coverage = marker.get("array_coverage")
    if not isinstance(coverage, dict):
        issues.append(f"Marker-exact mtDNA marker {rsid} at {node_name} has no array coverage")
        return
    expected_coverage_fields = {
        "cohort_id",
        "position_present_in",
        "callable_snv_in",
    }
    if set(coverage) != expected_coverage_fields:
        issues.append(
            f"Marker-exact mtDNA marker {rsid} at {node_name} has invalid coverage fields"
        )
    if {"modern_exports_tested", "modern_exports_with_position"} & set(coverage):
        issues.append(
            f"Marker-exact mtDNA marker {rsid} at {node_name} retains ambiguous numeric coverage"
        )
    cohort_id = coverage.get("cohort_id")
    if not _is_nonblank(cohort_id):
        issues.append(
            f"Marker-exact mtDNA marker {rsid} at {node_name} has an invalid array cohort"
        )
        return
    cohort = cohorts.get(cohort_id)
    if not isinstance(cohort, dict) or not isinstance(cohort.get("export_ids"), list):
        issues.append(
            f"Marker-exact mtDNA marker {rsid} at {node_name} names an unknown array cohort"
        )
        return
    members = cohort["export_ids"]
    present = coverage.get("position_present_in")
    callable_snv = coverage.get("callable_snv_in")
    for label, selected in (
        ("position-present", present),
        ("callable-SNV", callable_snv),
    ):
        if not isinstance(selected, list) or not all(
            isinstance(export_id, str) for export_id in selected
        ):
            issues.append(
                f"Marker-exact mtDNA marker {rsid} at {node_name} has invalid {label} membership"
            )
            return
        if len(selected) != len(set(selected)):
            issues.append(
                f"Marker-exact mtDNA marker {rsid} at {node_name} repeats {label} exports"
            )
        if any(export_id not in members for export_id in selected):
            issues.append(
                f"Marker-exact mtDNA marker {rsid} at {node_name} has {label} exports "
                "outside its cohort"
            )
        expected_order = [export_id for export_id in members if export_id in selected]
        if selected != expected_order:
            issues.append(
                f"Marker-exact mtDNA marker {rsid} at {node_name} has unordered {label} exports"
            )
    if isinstance(present, list) and not present:
        issues.append(
            f"Marker-exact mtDNA marker {rsid} at {node_name} is absent from its whole cohort"
        )
    if (
        isinstance(present, list)
        and isinstance(callable_snv, list)
        and not set(callable_snv).issubset(present)
    ):
        issues.append(
            f"Marker-exact mtDNA marker {rsid} at {node_name} is callable where its "
            "position is absent"
        )


def _mt_owner_motifs(
    node_name: str,
    record: dict[str, Any],
    omitted_nodes: dict[str, Any],
    issues: list[str],
) -> dict[str, Any]:
    source_node = record.get("source_node")
    if not _is_nonblank(source_node):
        issues.append(f"Marker-exact mtDNA node {node_name} has no source-node identity")
        return {}
    owner_motifs: dict[str, Any] = {source_node: record.get("direct_source_motif")}
    topology = record.get("source_topology")
    if not isinstance(topology, dict):
        issues.append(f"Marker-exact mtDNA node {node_name} has no source-topology state")
        return owner_motifs
    status = topology.get("status")
    if status == "pending":
        if set(topology) != {"status"}:
            issues.append(
                f"Pending source topology for mtDNA node {node_name} contains partial exact fields"
            )
        return owner_motifs
    if status != "exact":
        issues.append(
            f"Marker-exact mtDNA node {node_name} has invalid source-topology status {status!r}"
        )
        return owner_motifs

    expected_topology_fields = {
        "status",
        "emitted_parent_source_node",
        "source_parent",
        "flattened_source_path",
    }
    if set(topology) != expected_topology_fields:
        issues.append(f"Exact source topology for mtDNA node {node_name} has invalid fields")

    emitted_parent_source = topology.get("emitted_parent_source_node")
    source_parent = topology.get("source_parent")
    flattened_path = topology.get("flattened_source_path")
    if not _is_nonblank(emitted_parent_source):
        issues.append(
            f"Exact source topology for mtDNA node {node_name} has no emitted-parent source node"
        )
    if not _is_nonblank(source_parent):
        issues.append(f"Exact source topology for mtDNA node {node_name} has no source parent")
    if not isinstance(flattened_path, list):
        issues.append(
            f"Exact source topology for mtDNA node {node_name} has no ordered flattened path"
        )
        return owner_motifs

    prior = emitted_parent_source
    seen_path_nodes: set[str] = set()
    for index, path_record in enumerate(flattened_path):
        if not isinstance(path_record, dict):
            issues.append(
                f"Exact source topology for mtDNA node {node_name} has a non-object path step"
            )
            continue
        expected_path_fields = {
            "source_node",
            "source_parent",
            "reason",
            "direct_source_motif",
        }
        if set(path_record) != expected_path_fields:
            issues.append(
                f"Exact source topology for mtDNA node {node_name} path step {index} "
                "has invalid fields"
            )
        path_node = path_record.get("source_node")
        path_parent = path_record.get("source_parent")
        if not _is_nonblank(path_node):
            issues.append(
                f"Exact source topology for mtDNA node {node_name} path step {index} has no name"
            )
            continue
        if path_node in seen_path_nodes or path_node == source_node:
            issues.append(
                f"Exact source topology for mtDNA node {node_name} repeats source path node "
                f"{path_node}"
            )
        seen_path_nodes.add(path_node)
        if path_parent != prior:
            issues.append(
                f"Exact source topology for mtDNA node {node_name} breaks adjacency at "
                f"{path_node}: parent {path_parent!r}, expected {prior!r}"
            )
        if not _is_nonblank(path_record.get("reason")):
            issues.append(f"Flattened mtDNA source path node {path_node} has no omission reason")
        omission = omitted_nodes.get(path_node)
        omission_type = omission.get("type") if isinstance(omission, dict) else None
        if omission_type not in _MT_FLATTENED_OMISSION_TYPES:
            issues.append(
                f"Flattened mtDNA source path node {path_node} has no typed top-level omission"
            )
        elif path_record.get("reason") != omission.get("reason"):
            issues.append(
                f"Flattened mtDNA source path node {path_node} disagrees with its omission reason"
            )
        path_motif = path_record.get("direct_source_motif")
        if omission_type == "flattened_unreportable_source_intermediate" and isinstance(
            path_motif, list
        ):
            if any(
                isinstance(mutation, dict) and mutation.get("emitted") is True
                for mutation in path_motif
            ):
                issues.append(
                    f"Flattened-unreportable mtDNA source node {path_node} has an "
                    "emitted source decision"
                )
        owner_motifs[path_node] = path_motif
        prior = path_node
    if source_parent != prior:
        issues.append(
            f"Exact source topology for mtDNA node {node_name} ends at {prior!r}; "
            f"declared source parent is {source_parent!r}"
        )
    return owner_motifs


def _mt_validate_exact_record(
    node_name: str,
    record: dict[str, Any],
    omitted_nodes: dict[str, Any],
    cohorts: dict[str, Any],
    issues: list[str],
) -> None:
    expected_keys = {
        "source_node",
        "emitted_parent",
        "source_motif_status",
        "source_topology",
        "direct_source_motif",
        "emitted_snps",
    }
    if set(record) != expected_keys:
        issues.append(f"Marker-exact mtDNA node {node_name} has invalid provenance fields")
    if record.get("source_motif_status") not in {"exact", "legacy_partial"}:
        issues.append(f"Marker-exact mtDNA node {node_name} has an invalid source-motif status")
    owner_motifs = _mt_owner_motifs(node_name, record, omitted_nodes, issues)
    emitted_decisions: dict[tuple[str, int], tuple[str, str]] = {}
    for owner, motif in owner_motifs.items():
        for pos, direction in _mt_validate_mutation_list(owner, motif, issues).items():
            emitted_decisions[(owner, pos)] = direction

    markers = record.get("emitted_snps")
    if not isinstance(markers, list) or not markers:
        issues.append(f"Marker-exact mtDNA node {node_name} has no emitted SNPs")
        return
    seen_positions: set[int] = set()
    matched_decisions: set[tuple[str, int]] = set()
    for marker in markers:
        if not isinstance(marker, dict):
            issues.append(f"Marker-exact mtDNA node {node_name} contains a non-object marker")
            continue
        expected_marker_fields = {
            "rsid",
            "pos",
            "ancestral_allele",
            "allele",
            "motif_owner",
            "array_coverage",
        }
        if set(marker) != expected_marker_fields:
            issues.append(f"Marker-exact mtDNA node {node_name} has a marker with invalid fields")
        rsid = marker.get("rsid")
        pos = marker.get("pos")
        ancestral = marker.get("ancestral_allele")
        derived = marker.get("allele")
        owner = marker.get("motif_owner")
        if not _is_nonblank(rsid):
            issues.append(
                f"Marker-exact mtDNA node {node_name} has a marker without an identifier"
            )
        if not isinstance(pos, int) or not 1 <= pos <= 16569:
            issues.append(
                f"Marker-exact mtDNA node {node_name} has invalid emitted position {pos!r}"
            )
            continue
        if pos in seen_positions:
            issues.append(f"Marker-exact mtDNA node {node_name} repeats emitted position {pos}")
        seen_positions.add(pos)
        key = (owner, pos) if _is_nonblank(owner) else None
        if key is None:
            issues.append(
                f"Marker-exact mtDNA marker {rsid} at {node_name} has an invalid motif owner"
            )
        elif owner not in owner_motifs:
            issues.append(
                f"Marker-exact mtDNA marker {rsid} at {node_name} has outside motif owner "
                f"{owner!r}"
            )
        elif emitted_decisions.get(key) != (ancestral, derived):
            issues.append(
                f"Marker-exact mtDNA marker {rsid} at {node_name} does not match its "
                "source mutation direction"
            )
        if key is not None:
            matched_decisions.add(key)
        _mt_validate_coverage(node_name, marker, cohorts, issues)
    if matched_decisions != set(emitted_decisions):
        issues.append(
            f"Marker-exact mtDNA node {node_name} emitted markers do not match every "
            "source emission decision"
        )


def _mt_v1_semantic_projection(source: dict[str, Any], names: list[str]) -> list[dict[str, Any]]:
    nodes = source["nodes"]
    cohorts = source["array_cohorts"]
    projection: list[dict[str, Any]] = []
    for name in names:
        record = nodes[name]
        emitted_snps = []
        for marker in record["emitted_snps"]:
            coverage = marker["array_coverage"]
            cohort_members = cohorts[coverage["cohort_id"]]["export_ids"]
            emitted_snps.append(
                {
                    "rsid": marker["rsid"],
                    "pos": marker["pos"],
                    "ancestral_allele": marker["ancestral_allele"],
                    "allele": marker["allele"],
                    "array_coverage": {
                        "modern_exports_tested": len(cohort_members),
                        "modern_exports_with_position": len(coverage["position_present_in"]),
                    },
                }
            )
        projection.append(
            {
                "node": name,
                "emitted_snps": emitted_snps,
                "source_motif": record["direct_source_motif"],
            }
        )
    return sorted(projection, key=lambda item: item["node"])


def _mt_v1_coverage_rows(source: dict[str, Any], names: list[str]) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    cohorts = source["array_cohorts"]
    for name in names:
        for marker in source["nodes"][name]["emitted_snps"]:
            coverage = marker["array_coverage"]
            rows.append(
                (
                    name,
                    marker["rsid"],
                    marker["pos"],
                    coverage["cohort_id"],
                    len(cohorts[coverage["cohort_id"]]["export_ids"]),
                    len(coverage["position_present_in"]),
                )
            )
    return rows


def _mt_baseline_v2_registry_projection(
    source: dict[str, Any], names: list[str]
) -> list[dict[str, Any]]:
    """Project the immutable schema-v2 registry snapshot without claiming motif parity."""
    nodes = source["nodes"]
    return [
        {
            "node": name,
            "source_node": nodes[name]["source_node"],
            "emitted_parent": nodes[name]["emitted_parent"],
            "direct_source_motif": nodes[name]["direct_source_motif"],
            "emitted_snps": [
                {
                    key: marker[key]
                    for key in (
                        "rsid",
                        "pos",
                        "ancestral_allele",
                        "allele",
                        "motif_owner",
                    )
                }
                for marker in nodes[name]["emitted_snps"]
            ],
        }
        for name in names
    ]


def _mt_direct_motif_semantic_projection(
    source: dict[str, Any], names: list[str]
) -> list[dict[str, Any]]:
    """Project only independently checked direct Build-17 motif evidence."""
    nodes = source["nodes"]
    return [
        {
            "node": name,
            "source_node": nodes[name]["source_node"],
            "direct_source_motif": nodes[name]["direct_source_motif"],
        }
        for name in names
    ]


def _mt_locked_semantic_projection(
    source: dict[str, Any], names: list[str]
) -> list[dict[str, Any]]:
    nodes = source["nodes"]
    return [
        {
            "node": name,
            "source_node": nodes[name]["source_node"],
            "emitted_parent": nodes[name]["emitted_parent"],
            "source_topology": nodes[name]["source_topology"],
            "direct_source_motif": nodes[name]["direct_source_motif"],
            "emitted_snps": [
                {
                    key: marker[key]
                    for key in (
                        "rsid",
                        "pos",
                        "ancestral_allele",
                        "allele",
                        "motif_owner",
                    )
                }
                for marker in nodes[name]["emitted_snps"]
            ],
        }
        for name in names
    ]


def _mt_locked_coverage_rows(source: dict[str, Any], names: list[str]) -> list[tuple[Any, ...]]:
    return [
        (
            name,
            marker["rsid"],
            marker["pos"],
            marker["array_coverage"]["cohort_id"],
            ",".join(marker["array_coverage"]["position_present_in"]),
            ",".join(marker["array_coverage"]["callable_snv_in"]),
        )
        for name in names
        for marker in source["nodes"][name]["emitted_snps"]
    ]


def _validate_mt_baseline_snapshot(
    snapshot: dict[str, Any], migration: dict[str, Any]
) -> list[str]:
    """Validate immutable historical locks against the archived schema-v2 state."""
    issues: list[str] = []
    if not isinstance(snapshot, dict):
        return ["mtDNA baseline snapshot must be an object"]

    expected_fields = {
        "schema_version",
        "baseline_commit",
        "normalization_commit",
        "array_cohorts",
        "legacy_locked_exact_nodes",
        "baseline_exact_nodes",
        "baseline_direct_motif_exact_nodes",
        "nodes",
        "emitted_tree_projection",
    }
    if set(snapshot) != expected_fields:
        issues.append("mtDNA baseline snapshot has unexpected or missing top-level fields")

    snapshot_digest = _canonical_json_sha256(snapshot)
    if migration.get("baseline_snapshot_sha256") != snapshot_digest:
        issues.append(
            "mtDNA migration baseline_snapshot_sha256 does not match the baseline archive"
        )
    if snapshot_digest != _MT_BASELINE_SNAPSHOT_SHA256:
        issues.append("mtDNA baseline snapshot differs from the review-locked archive")
    if snapshot.get("schema_version") != _MT_BASELINE_SNAPSHOT_SCHEMA_VERSION:
        issues.append("mtDNA baseline snapshot has an unsupported schema version")
    if snapshot.get("baseline_commit") != _MT_BASELINE_COMMIT:
        issues.append("mtDNA baseline snapshot has the wrong baseline commit")
    if snapshot.get("normalization_commit") != _MT_BASELINE_NORMALIZATION_COMMIT:
        issues.append("mtDNA baseline snapshot has the wrong normalization commit")
    if snapshot.get("array_cohorts") != _MT_EXPECTED_ARRAY_COHORTS:
        issues.append("mtDNA baseline snapshot has the wrong array cohorts")

    archived_legacy = _sorted_unique_string_list(
        snapshot.get("legacy_locked_exact_nodes"),
        "legacy_locked_exact_nodes",
        issues,
        "mtDNA baseline snapshot",
    )
    archived_baseline = _sorted_unique_string_list(
        snapshot.get("baseline_exact_nodes"),
        "baseline_exact_nodes",
        issues,
        "mtDNA baseline snapshot",
    )
    archived_direct = _sorted_unique_string_list(
        snapshot.get("baseline_direct_motif_exact_nodes"),
        "baseline_direct_motif_exact_nodes",
        issues,
        "mtDNA baseline snapshot",
    )
    archived_nodes = snapshot.get("nodes")
    if not isinstance(archived_nodes, dict):
        archived_nodes = {}
        issues.append("mtDNA baseline snapshot has no node archive")
    if set(archived_nodes) != set(archived_baseline):
        issues.append("mtDNA baseline snapshot nodes do not equal its baseline frontier")
    if not set(archived_legacy).issubset(archived_baseline):
        issues.append("mtDNA baseline snapshot legacy frontier is outside its baseline")
    if not set(archived_direct).issubset(archived_baseline):
        issues.append("mtDNA baseline snapshot direct-motif frontier is outside its baseline")

    archive_name_checks = (
        (
            "legacy_locked_exact_nodes",
            archived_legacy,
            _MT_LEGACY_EXACT_NAMES_SHA256,
        ),
        (
            "baseline_exact_nodes",
            archived_baseline,
            _MT_BASELINE_EXACT_NAMES_SHA256,
        ),
        (
            "baseline_direct_motif_exact_nodes",
            archived_direct,
            _MT_BASELINE_DIRECT_MOTIF_EXACT_NAMES_SHA256,
        ),
    )
    for field, archived_names, expected_digest in archive_name_checks:
        if snapshot.get(field) != migration.get(field):
            issues.append(f"mtDNA baseline snapshot {field} differs from the migration anchor")
        if _canonical_json_sha256(archived_names) != expected_digest:
            issues.append(f"mtDNA baseline snapshot {field} differs from its locked digest")

    tree_projection = snapshot.get("emitted_tree_projection")
    if not isinstance(tree_projection, list):
        issues.append("mtDNA baseline snapshot has no emitted-tree projection")
        tree_projection = []

    historical_constants = {
        "legacy_v1_semantic_sha256": _MT_LEGACY_V1_SEMANTIC_SHA256,
        "legacy_v1_coverage_sha256": _MT_LEGACY_V1_COVERAGE_SHA256,
        "baseline_v1_semantic_sha256": _MT_BASELINE_V1_SEMANTIC_SHA256,
        "baseline_v1_coverage_sha256": _MT_BASELINE_V1_COVERAGE_SHA256,
        "baseline_v2_registry_semantic_sha256": (_MT_BASELINE_V2_REGISTRY_SEMANTIC_SHA256),
        "baseline_v2_coverage_membership_sha256": (_MT_BASELINE_V2_COVERAGE_MEMBERSHIP_SHA256),
        "baseline_direct_motif_semantic_sha256": (_MT_BASELINE_DIRECT_MOTIF_SEMANTIC_SHA256),
        "baseline_emitted_tree_sha256": _MT_BASELINE_EMITTED_TREE_SHA256,
    }
    archive_source = {
        "array_cohorts": snapshot.get("array_cohorts"),
        "nodes": archived_nodes,
    }
    try:
        historical_projections = {
            "legacy_v1_semantic_sha256": _canonical_json_sha256(
                _mt_v1_semantic_projection(archive_source, archived_legacy)
            ),
            "legacy_v1_coverage_sha256": _sorted_tsv_sha256(
                _mt_v1_coverage_rows(archive_source, archived_legacy)
            ),
            "baseline_v1_semantic_sha256": _canonical_json_sha256(
                _mt_v1_semantic_projection(archive_source, archived_baseline)
            ),
            "baseline_v1_coverage_sha256": _sorted_tsv_sha256(
                _mt_v1_coverage_rows(archive_source, archived_baseline)
            ),
            "baseline_v2_registry_semantic_sha256": _canonical_json_sha256(
                _mt_baseline_v2_registry_projection(archive_source, archived_baseline)
            ),
            "baseline_v2_coverage_membership_sha256": _sorted_tsv_sha256(
                _mt_locked_coverage_rows(archive_source, archived_baseline)
            ),
            "baseline_direct_motif_semantic_sha256": _canonical_json_sha256(
                _mt_direct_motif_semantic_projection(archive_source, archived_direct)
            ),
            "baseline_emitted_tree_sha256": _canonical_json_sha256(tree_projection),
        }
    except (KeyError, TypeError, ValueError):
        issues.append("mtDNA historical baseline projections cannot be computed")
    else:
        for field, calculated in historical_projections.items():
            if migration.get(field) != calculated:
                issues.append(
                    f"mtDNA migration {field} does not match the baseline archive projection"
                )
            if calculated != historical_constants[field]:
                issues.append(
                    f"mtDNA baseline snapshot {field} differs from the locked historical value"
                )
    return issues


def _validate_mt_source_schema(
    source: dict[str, Any], baseline_snapshot: dict[str, Any] | None = None
) -> list[str]:
    """Validate schema-v3 mtDNA provenance independently of the emitted tree."""
    issues: list[str] = []
    if not isinstance(source, dict):
        return ["mtDNA source registry must be an object"]
    expected_top_level = {
        "schema_version",
        "audit_scope",
        "source",
        "references",
        "array_exports",
        "array_cohorts",
        "direct_source_motif_states",
        "omitted_nodes",
        "retired_emitted_nodes",
        "nodes",
        "structural_exceptions",
        "pending_nodes",
        "migration",
    }
    if set(source) != expected_top_level:
        issues.append("mtDNA source registry has unexpected or missing top-level fields")
    if source.get("schema_version") != _MT_SCHEMA_VERSION:
        issues.append("mtDNA source registry has an unsupported schema version")
    if not _is_nonblank(source.get("audit_scope")):
        issues.append("mtDNA source registry has no valid audit scope")

    source_metadata = source.get("source")
    if not isinstance(source_metadata, dict):
        source_metadata = {}
        issues.append("mtDNA source registry has no source metadata")
    if source_metadata.get("version") != "Build 17":
        issues.append("mtDNA source registry is not pinned to PhyloTree Build 17")
    if source_metadata.get("archive_sha256") != _MT_PHYLOTREE_ARCHIVE_SHA256:
        issues.append("mtDNA source registry has the wrong PhyloTree archive SHA-256")
    reference_sequence = source_metadata.get("reference_sequence")
    if not isinstance(reference_sequence, dict):
        reference_sequence = {}
    if reference_sequence.get("accession") != "NC_012920.1":
        issues.append("mtDNA source registry is not pinned to rCRS NC_012920.1")
    if reference_sequence.get("sha256") != _MT_RCRS_SHA256:
        issues.append("mtDNA source registry has the wrong rCRS FASTA SHA-256")
    references = source.get("references")
    if (
        not isinstance(references, list)
        or not references
        or not all(isinstance(reference, dict) for reference in references)
    ):
        issues.append("mtDNA source registry has no paper references")

    category_maps: dict[str, dict[str, Any]] = {}
    for key in (
        "array_exports",
        "array_cohorts",
        "omitted_nodes",
        "retired_emitted_nodes",
        "nodes",
        "structural_exceptions",
        "pending_nodes",
        "migration",
    ):
        value = source.get(key)
        if not isinstance(value, dict):
            issues.append(f"mtDNA source registry has no valid {key.replace('_', '-')} mapping")
            category_maps[key] = {}
        else:
            category_maps[key] = value

    exports = category_maps["array_exports"]
    cohorts = category_maps["array_cohorts"]
    if exports != _MT_EXPECTED_ARRAY_EXPORTS:
        issues.append("mtDNA array export manifest differs from the pinned six-export inventory")
    if cohorts != _MT_EXPECTED_ARRAY_COHORTS:
        issues.append("mtDNA array cohorts differ from the pinned 23andMe cohorts")
    for export_id, export in exports.items():
        if not isinstance(export, dict):
            issues.append(f"mtDNA array export {export_id} is not an object")
            continue
        digest = export.get("sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            issues.append(f"mtDNA array export {export_id} has no lowercase SHA-256")
        if not isinstance(export.get("line_count"), int) or export["line_count"] <= 0:
            issues.append(f"mtDNA array export {export_id} has no positive line count")
    for cohort_id, cohort in cohorts.items():
        if not isinstance(cohort, dict) or not isinstance(cohort.get("export_ids"), list):
            issues.append(f"mtDNA array cohort {cohort_id} has no export membership")
            continue
        members = cohort["export_ids"]
        if not all(isinstance(member, str) for member in members):
            issues.append(f"mtDNA array cohort {cohort_id} has a non-string export member")
            continue
        if len(members) != len(set(members)):
            issues.append(f"mtDNA array cohort {cohort_id} repeats an export")
        if any(member not in exports for member in members):
            issues.append(f"mtDNA array cohort {cohort_id} names an unknown export")
        if "pgp_ancestry_4190" in members:
            issues.append(f"mtDNA array cohort {cohort_id} includes the Ancestry comparator")

    nodes = category_maps["nodes"]
    structural = category_maps["structural_exceptions"]
    pending = category_maps["pending_nodes"]
    omitted = category_maps["omitted_nodes"]
    retired = category_maps["retired_emitted_nodes"]
    direct_motif_states = source.get("direct_source_motif_states")
    if not isinstance(direct_motif_states, dict):
        issues.append("mtDNA source registry has no direct-source motif states")
        direct_motif_states = {}
    if set(direct_motif_states) != {"exact_nodes", "legacy_partial_nodes"}:
        issues.append("mtDNA direct-source motif states have unexpected or missing fields")
    direct_motif_exact = _sorted_unique_string_list(
        direct_motif_states.get("exact_nodes"),
        "exact_nodes",
        issues,
        "mtDNA direct-source motif state",
    )
    direct_motif_partial = _sorted_unique_string_list(
        direct_motif_states.get("legacy_partial_nodes"),
        "legacy_partial_nodes",
        issues,
        "mtDNA direct-source motif state",
    )
    direct_motif_overlap = set(direct_motif_exact) & set(direct_motif_partial)
    if direct_motif_overlap:
        issues.append(
            "mtDNA exact and legacy-partial direct-source motif states overlap: "
            + ", ".join(sorted(direct_motif_overlap))
        )
    if set(direct_motif_exact) | set(direct_motif_partial) != set(nodes):
        issues.append("mtDNA direct-source motif states do not partition the marker-exact nodes")
    categories = {
        "marker-exact": set(nodes),
        "structural": set(structural),
        "pending": set(pending),
    }
    category_names = list(categories)
    for index, left_name in enumerate(category_names):
        for right_name in category_names[index + 1 :]:
            overlap = categories[left_name] & categories[right_name]
            if overlap:
                issues.append(
                    f"mtDNA {left_name} and {right_name} states overlap: "
                    + ", ".join(sorted(overlap))
                )
    emitted_categories = set().union(*categories.values())
    omitted_overlap = set(omitted) & emitted_categories
    if omitted_overlap:
        issues.append(
            "mtDNA source nodes are both omitted and emitted: "
            + ", ".join(sorted(omitted_overlap))
        )
    retired_names = set(retired)
    retired_live_overlap = retired_names & emitted_categories
    if retired_live_overlap:
        issues.append(
            "mtDNA retired-emitted state overlaps current states: "
            + ", ".join(sorted(retired_live_overlap))
        )
    retired_omitted_overlap = retired_names & set(omitted)
    if retired_omitted_overlap:
        issues.append(
            "mtDNA retired-emitted state overlaps omitted source nodes: "
            + ", ".join(sorted(retired_omitted_overlap))
        )

    allowed_omission_types = {
        "unreportable_source_node",
        *_MT_FLATTENED_OMISSION_TYPES,
    }
    for name, omission in omitted.items():
        if not _is_nonblank(name) or not isinstance(omission, dict):
            issues.append(f"mtDNA omitted source node {name!r} has no typed record")
            continue
        if set(omission) != {"type", "reason"}:
            issues.append(f"mtDNA omitted source node {name} has invalid fields")
        if omission.get("type") not in allowed_omission_types:
            issues.append(f"mtDNA omitted source node {name} has an invalid omission type")
        if not _is_nonblank(omission.get("reason")):
            issues.append(f"mtDNA omitted source node {name} has no reason")

    for name, tombstone in retired.items():
        if not _is_nonblank(name) or not isinstance(tombstone, dict):
            issues.append(f"Retired mtDNA node {name!r} has no typed tombstone")
            continue
        expected_tombstone_fields = {
            "type",
            "former_emitted_parent",
            "former_defining_snps",
            "reason",
        }
        if set(tombstone) != expected_tombstone_fields:
            issues.append(f"Retired mtDNA node {name} has invalid fields")
        if tombstone.get("type") != _MT_RETIRED_NODE_TYPE:
            issues.append(f"Retired mtDNA node {name} has an invalid retirement type")
        former_parent = tombstone.get("former_emitted_parent")
        if not _is_nonblank(former_parent) or former_parent == name:
            issues.append(f"Retired mtDNA node {name} has an invalid former emitted parent")
        if not _is_nonblank(tombstone.get("reason")):
            issues.append(f"Retired mtDNA node {name} has no reason")
        former_markers = tombstone.get("former_defining_snps")
        if not isinstance(former_markers, list) or not former_markers:
            issues.append(f"Retired mtDNA node {name} has no former defining markers")
            continue
        seen_marker_ids: set[str] = set()
        seen_marker_positions: set[int] = set()
        for marker in former_markers:
            if not isinstance(marker, dict):
                issues.append(f"Retired mtDNA node {name} has a non-object former marker")
                continue
            if set(marker) != {"rsid", "pos", "allele"}:
                issues.append(f"Retired mtDNA node {name} has a former marker with invalid fields")
            rsid = marker.get("rsid")
            pos = marker.get("pos")
            allele = marker.get("allele")
            if not _is_nonblank(rsid) or rsid in seen_marker_ids:
                issues.append(f"Retired mtDNA node {name} has an invalid former marker identifier")
            elif isinstance(rsid, str):
                seen_marker_ids.add(rsid)
            if (
                not isinstance(pos, int)
                or isinstance(pos, bool)
                or not 1 <= pos <= 16569
                or pos in seen_marker_positions
            ):
                issues.append(f"Retired mtDNA node {name} has an invalid former marker position")
            else:
                seen_marker_positions.add(pos)
            if allele not in {"A", "C", "G", "T"}:
                issues.append(f"Retired mtDNA node {name} has an invalid former marker allele")
        baseline = _MT_RETIRED_NODE_BASELINES.get(name)
        if baseline is None:
            issues.append(f"Retired mtDNA node {name} has no locked historical baseline")
        else:
            if former_parent != baseline["former_emitted_parent"]:
                issues.append(
                    f"Retired mtDNA node {name} former emitted parent differs from its "
                    "locked historical baseline"
                )
            if former_markers != baseline["former_defining_snps"]:
                issues.append(
                    f"Retired mtDNA node {name} former defining markers differ from its "
                    "locked historical baseline"
                )

    for name, record in nodes.items():
        if not _is_nonblank(name) or not isinstance(record, dict):
            issues.append(f"Marker-exact mtDNA node {name!r} has no provenance record")
            continue
        if record.get("emitted_parent") is not None and not _is_nonblank(
            record.get("emitted_parent")
        ):
            issues.append(f"Marker-exact mtDNA node {name} has an invalid emitted parent")
        expected_motif_status = "exact" if name in set(direct_motif_exact) else "legacy_partial"
        if record.get("source_motif_status") != expected_motif_status:
            issues.append(
                f"Marker-exact mtDNA node {name} source-motif status disagrees with "
                "the direct-source motif frontier"
            )
        _mt_validate_exact_record(name, record, omitted, cohorts, issues)
    direct_source_nodes = [
        record.get("source_node")
        for record in nodes.values()
        if isinstance(record, dict) and isinstance(record.get("source_node"), str)
    ]
    for name, record in pending.items():
        if not _is_nonblank(name) or not isinstance(record, dict):
            issues.append(f"Pending mtDNA node {name!r} has no state record")
            continue
        if set(record) != {"emitted_parent"} or not _is_nonblank(record.get("emitted_parent")):
            issues.append(f"Pending mtDNA node {name} has an invalid emitted-parent state")
    for name, record in structural.items():
        if not _is_nonblank(name) or not isinstance(record, dict):
            issues.append(f"Structural mtDNA node {name!r} has no state record")
            continue
        if record.get("type") not in {"root", "markerless_passthrough"}:
            issues.append(f"Structural mtDNA node {name} has an invalid exception type")
        if not _is_nonblank(record.get("reason")):
            issues.append(f"Structural mtDNA node {name} has no reason")
        if record.get("source_status") not in {"synthetic", "pending", "exact"}:
            issues.append(f"Structural mtDNA node {name} has an invalid source status")
        base_structural_fields = {
            "type",
            "emitted_parent",
            "source_status",
            "reason",
        }
        if record.get("type") == "root" and record.get("source_status") == "synthetic":
            expected_structural_fields = base_structural_fields | {"source_topology_anchor"}
            anchor = record.get("source_topology_anchor")
            if name != _MT_SYNTHETIC_ROOT_NAME:
                issues.append(
                    f"Synthetic mtDNA root {name} must use canonical root name "
                    f"{_MT_SYNTHETIC_ROOT_NAME!r}"
                )
            if not _is_nonblank(anchor):
                issues.append(f"Synthetic mtDNA root {name} has no source-topology anchor")
            elif anchor != _MT_SYNTHETIC_ROOT_NAME:
                issues.append(
                    f"Synthetic mtDNA root {name} source-topology anchor {anchor!r} "
                    f"must equal canonical emitted root name {_MT_SYNTHETIC_ROOT_NAME!r}"
                )
        elif record.get("source_status") == "exact":
            expected_structural_fields = base_structural_fields | {
                "source_node",
                "source_topology",
                "direct_source_motif",
                "emitted_snps",
            }
        else:
            expected_structural_fields = base_structural_fields
        if set(record) != expected_structural_fields:
            issues.append(f"Structural mtDNA node {name} has invalid provenance fields")
        if record.get("source_status") == "exact" and not _is_nonblank(record.get("source_node")):
            issues.append(f"Exact structural mtDNA node {name} has no source identity")

    exact_structural_source_nodes = [
        record.get("source_node")
        for record in structural.values()
        if isinstance(record, dict)
        and record.get("source_status") == "exact"
        and isinstance(record.get("source_node"), str)
    ]
    all_direct_source_nodes = [*direct_source_nodes, *exact_structural_source_nodes]
    if len(all_direct_source_nodes) != len(set(all_direct_source_nodes)):
        issues.append("mtDNA exact records repeat a direct source-node identity")
    all_direct_source_omissions = set(all_direct_source_nodes) & set(omitted)
    if all_direct_source_omissions:
        issues.append(
            "mtDNA exact direct source nodes are also globally omitted: "
            + ", ".join(sorted(all_direct_source_omissions))
        )
    synthetic_root_anchors = {
        record.get("source_topology_anchor")
        for record in structural.values()
        if isinstance(record, dict)
        and record.get("type") == "root"
        and record.get("source_status") == "synthetic"
        and _is_nonblank(record.get("source_topology_anchor"))
    }
    flattened_source_identities = _mt_referenced_flattened_source_nodes(source)
    colliding_root_anchors = synthetic_root_anchors & (
        set(all_direct_source_nodes) | set(omitted) | flattened_source_identities
    )
    if colliding_root_anchors:
        issues.append(
            "mtDNA synthetic root source-topology anchors collide with source-node "
            "identities: " + ", ".join(sorted(colliding_root_anchors))
        )

    migration = category_maps["migration"]
    expected_migration_fields = {
        "status",
        "baseline_commit",
        "baseline_snapshot_sha256",
        "legacy_locked_exact_nodes",
        "legacy_locked_exact_nodes_sha256",
        "legacy_v1_semantic_sha256",
        "legacy_v1_coverage_sha256",
        "baseline_exact_nodes",
        "baseline_exact_nodes_sha256",
        "baseline_v1_semantic_sha256",
        "baseline_v1_coverage_sha256",
        "baseline_v2_registry_semantic_sha256",
        "baseline_v2_coverage_membership_sha256",
        "locked_exact_nodes",
        "locked_exact_nodes_sha256",
        "locked_exact_semantic_sha256",
        "locked_exact_coverage_membership_sha256",
        "baseline_direct_motif_exact_nodes",
        "baseline_direct_motif_exact_nodes_sha256",
        "baseline_direct_motif_semantic_sha256",
        "locked_direct_motif_exact_nodes",
        "locked_direct_motif_exact_nodes_sha256",
        "locked_direct_motif_semantic_sha256",
        "initial_direct_motif_pending_nodes",
        "initial_direct_motif_pending_nodes_sha256",
        "initial_pending_nodes",
        "initial_pending_nodes_sha256",
        "array_manifest_sha256",
        "source_metadata_sha256",
        "state_partition_sha256",
        "baseline_emitted_tree_sha256",
        "locked_emitted_tree_sha256",
    }
    if set(migration) != expected_migration_fields:
        issues.append("mtDNA migration has unexpected or missing fields")
    issues.extend(
        _validate_mt_baseline_snapshot(
            _MT_BASELINE_SNAPSHOT if baseline_snapshot is None else baseline_snapshot,
            migration,
        )
    )
    legacy = _sorted_unique_string_list(
        migration.get("legacy_locked_exact_nodes"),
        "legacy_locked_exact_nodes",
        issues,
    )
    baseline = _sorted_unique_string_list(
        migration.get("baseline_exact_nodes"), "baseline_exact_nodes", issues
    )
    locked = _sorted_unique_string_list(
        migration.get("locked_exact_nodes"), "locked_exact_nodes", issues
    )
    baseline_direct_motif_exact = _sorted_unique_string_list(
        migration.get("baseline_direct_motif_exact_nodes"),
        "baseline_direct_motif_exact_nodes",
        issues,
    )
    locked_direct_motif_exact = _sorted_unique_string_list(
        migration.get("locked_direct_motif_exact_nodes"),
        "locked_direct_motif_exact_nodes",
        issues,
    )
    initial_direct_motif_pending = _sorted_unique_string_list(
        migration.get("initial_direct_motif_pending_nodes"),
        "initial_direct_motif_pending_nodes",
        issues,
    )
    initial_pending = _sorted_unique_string_list(
        migration.get("initial_pending_nodes"), "initial_pending_nodes", issues
    )
    if migration.get("baseline_commit") != _MT_BASELINE_COMMIT:
        issues.append("mtDNA migration has the wrong baseline commit")
    if migration.get("status") not in {"in_progress", "complete"}:
        issues.append("mtDNA migration has an invalid status")
    if not set(legacy).issubset(baseline):
        issues.append("mtDNA legacy exact frontier is not contained in the baseline frontier")
    if not set(baseline).issubset(locked):
        issues.append("mtDNA baseline exact frontier regressed")
    if set(locked) != set(nodes):
        issues.append("mtDNA locked exact frontier does not equal the live marker-exact nodes")
    if not set(baseline_direct_motif_exact).issubset(locked_direct_motif_exact):
        issues.append("mtDNA baseline direct-source motif frontier regressed")
    if set(locked_direct_motif_exact) != set(direct_motif_exact):
        issues.append(
            "mtDNA locked direct-source motif frontier does not equal the live exact state"
        )
    if not set(direct_motif_partial).issubset(initial_direct_motif_pending):
        issues.append("mtDNA direct-source motif pending frontier grew beyond its baseline")
    if set(baseline_direct_motif_exact) & set(initial_direct_motif_pending):
        issues.append("mtDNA baseline direct-source motif states overlap")
    if set(baseline_direct_motif_exact) | set(initial_direct_motif_pending) != set(baseline):
        issues.append(
            "mtDNA baseline direct-source motif states do not partition the marker baseline"
        )
    if not set(pending).issubset(initial_pending):
        issues.append("mtDNA pending frontier grew beyond the initial audited tree")
    retired_outside_initial = retired_names - set(initial_pending)
    if retired_outside_initial:
        issues.append(
            "mtDNA retired emitted nodes were not in the initial pending frontier: "
            + ", ".join(sorted(retired_outside_initial))
        )
    dispositions = set(nodes) | set(structural) | set(pending) | retired_names
    if not set(initial_pending).issubset(dispositions):
        issues.append("mtDNA initial pending frontier contains nodes with no current disposition")

    array_manifest_digest = _canonical_json_sha256(
        {"array_exports": exports, "array_cohorts": cohorts}
    )
    source_metadata_digest = _canonical_json_sha256(
        {"source": source_metadata, "references": references}
    )
    state_partition_digest = _canonical_json_sha256(
        {
            "direct_source_motif_states": direct_motif_states,
            "omitted_nodes": omitted,
            "retired_emitted_nodes": retired,
            "structural_exceptions": structural,
            "pending_nodes": pending,
        }
    )
    simple_digest_checks = (
        (
            "legacy_locked_exact_nodes_sha256",
            _canonical_json_sha256(legacy),
            _MT_LEGACY_EXACT_NAMES_SHA256,
        ),
        (
            "baseline_exact_nodes_sha256",
            _canonical_json_sha256(baseline),
            _MT_BASELINE_EXACT_NAMES_SHA256,
        ),
        (
            "locked_exact_nodes_sha256",
            _canonical_json_sha256(locked),
            _MT_LOCKED_EXACT_NAMES_SHA256,
        ),
        (
            "baseline_direct_motif_exact_nodes_sha256",
            _canonical_json_sha256(baseline_direct_motif_exact),
            _MT_BASELINE_DIRECT_MOTIF_EXACT_NAMES_SHA256,
        ),
        (
            "locked_direct_motif_exact_nodes_sha256",
            _canonical_json_sha256(locked_direct_motif_exact),
            _MT_LOCKED_DIRECT_MOTIF_EXACT_NAMES_SHA256,
        ),
        (
            "initial_direct_motif_pending_nodes_sha256",
            _canonical_json_sha256(initial_direct_motif_pending),
            _MT_INITIAL_DIRECT_MOTIF_PENDING_NAMES_SHA256,
        ),
        (
            "initial_pending_nodes_sha256",
            _canonical_json_sha256(initial_pending),
            _MT_INITIAL_PENDING_NAMES_SHA256,
        ),
        ("array_manifest_sha256", array_manifest_digest, _MT_ARRAY_MANIFEST_SHA256),
        (
            "source_metadata_sha256",
            source_metadata_digest,
            _MT_SOURCE_METADATA_SHA256,
        ),
        (
            "state_partition_sha256",
            state_partition_digest,
            _MT_STATE_PARTITION_SHA256,
        ),
    )
    for field, calculated, expected in simple_digest_checks:
        if migration.get(field) != calculated:
            issues.append(f"mtDNA migration {field} does not match its registry projection")
        if calculated != expected:
            issues.append(f"mtDNA migration {field} differs from the locked baseline")

    live_constant_checks = (
        ("locked_exact_semantic_sha256", _MT_LOCKED_EXACT_SEMANTIC_SHA256),
        (
            "locked_direct_motif_semantic_sha256",
            _MT_LOCKED_DIRECT_MOTIF_SEMANTIC_SHA256,
        ),
        (
            "locked_exact_coverage_membership_sha256",
            _MT_LOCKED_EXACT_COVERAGE_MEMBERSHIP_SHA256,
        ),
    )
    for field, expected in live_constant_checks:
        if migration.get(field) != expected:
            issues.append(f"mtDNA migration {field} differs from the review-locked live value")
    if migration.get("locked_emitted_tree_sha256") != _MT_LOCKED_EMITTED_TREE_SHA256:
        issues.append(
            "mtDNA migration locked_emitted_tree_sha256 differs from the review-locked live tree"
        )

    # Historical projections are verified against the immutable archive above;
    # the rolling frontier continues to be derived from the live registry.
    try:
        projection_checks = (
            (
                "locked_exact_semantic_sha256",
                _canonical_json_sha256(_mt_locked_semantic_projection(source, locked)),
            ),
            (
                "locked_direct_motif_semantic_sha256",
                _canonical_json_sha256(
                    _mt_direct_motif_semantic_projection(source, locked_direct_motif_exact)
                ),
            ),
            (
                "locked_exact_coverage_membership_sha256",
                _sorted_tsv_sha256(_mt_locked_coverage_rows(source, locked)),
            ),
        )
    except (KeyError, TypeError, ValueError):
        issues.append("mtDNA exact-frontier provenance projections cannot be computed")
    else:
        for field, calculated in projection_checks:
            if migration.get(field) != calculated:
                issues.append(f"mtDNA migration {field} does not match its registry projection")
    return issues


def _mt_tree_projection(inventory: MtTreeInventory) -> list[dict[str, Any]]:
    return [
        {
            "node": occurrence.name,
            "parent": occurrence.parent,
            "defining_snps": occurrence.node.get("defining_snps", []),
        }
        for occurrence in inventory.occurrences
    ]


def _validate_exact_mt_markers(source: dict[str, Any], inventory: MtTreeInventory) -> list[str]:
    """Require every marker-exact record to equal its emitted tree marker multiset."""
    issues: list[str] = []
    nodes = source.get("nodes", {})
    if not isinstance(nodes, dict):
        return ["mtDNA source registry has no marker-exact node mapping"]
    for node_name, audit in nodes.items():
        occurrence = inventory.by_name.get(node_name)
        if occurrence is None:
            issues.append(
                f"Marker-exact mtDNA node {node_name} does not occur exactly once in the tree"
            )
            continue
        actual = sorted(
            (snp.get("rsid"), snp.get("pos"), snp.get("allele"))
            for snp in occurrence.node.get("defining_snps", [])
        )
        expected = sorted(
            (snp.get("rsid"), snp.get("pos"), snp.get("allele"))
            for snp in audit.get("emitted_snps", [])
            if isinstance(snp, dict)
        )
        if actual != expected:
            issues.append(
                f"Marker-exact mtDNA node {node_name} has markers {actual!r}; "
                f"expected {expected!r}"
            )
    return issues


def _validate_mt_structural_records(
    source: dict[str, Any], inventory: MtTreeInventory
) -> list[str]:
    issues: list[str] = []
    structural = source["structural_exceptions"]
    for name, record in structural.items():
        occurrence = inventory.by_name.get(name)
        if occurrence is None:
            continue
        exception_type = record.get("type")
        if exception_type == "root":
            if occurrence.parent is not None or occurrence.path != (name,):
                issues.append(f"Structural mtDNA root {name} is not the actual tree root")
            if name != _MT_SYNTHETIC_ROOT_NAME:
                issues.append(
                    f"Structural mtDNA root {name} must use canonical root name "
                    f"{_MT_SYNTHETIC_ROOT_NAME!r}"
                )
            if record.get("emitted_parent") is not None:
                issues.append(f"Structural mtDNA root {name} must have a null emitted parent")
            if record.get("source_status") != "synthetic":
                issues.append(f"Structural mtDNA root {name} must have synthetic source status")
            if not _is_nonblank(record.get("source_topology_anchor")):
                issues.append(f"Structural mtDNA root {name} has no source-topology anchor")
            elif record.get("source_topology_anchor") != _MT_SYNTHETIC_ROOT_NAME:
                issues.append(
                    f"Structural mtDNA root {name} source-topology anchor "
                    f"{record.get('source_topology_anchor')!r} must equal canonical emitted "
                    f"root name {_MT_SYNTHETIC_ROOT_NAME!r}"
                )
            if occurrence.node.get("defining_snps"):
                issues.append(f"Structural mtDNA root {name} must be markerless")
        elif exception_type == "markerless_passthrough":
            if "source_topology_anchor" in record:
                issues.append(
                    f"Structural mtDNA non-root {name} cannot carry a source-topology anchor"
                )
            if occurrence.parent is None:
                issues.append(f"Structural mtDNA pass-through {name} cannot be the root")
            if record.get("emitted_parent") != occurrence.parent:
                issues.append(
                    f"Structural mtDNA node {name} declares parent "
                    f"{record.get('emitted_parent')!r}; emitted parent is {occurrence.parent!r}"
                )
            if occurrence.node.get("defining_snps"):
                issues.append(f"Structural mtDNA pass-through {name} must be markerless")
            if record.get("source_status") == "exact":
                required = {
                    "type",
                    "emitted_parent",
                    "source_status",
                    "reason",
                    "source_node",
                    "source_topology",
                    "direct_source_motif",
                    "emitted_snps",
                }
                if set(record) != required:
                    issues.append(
                        f"Exact structural mtDNA node {name} lacks complete source provenance"
                    )
                else:
                    synthetic_record = {
                        key: record[key]
                        for key in (
                            "source_node",
                            "emitted_parent",
                            "source_topology",
                            "direct_source_motif",
                            "emitted_snps",
                        )
                    }
                    if record["emitted_snps"]:
                        issues.append(
                            f"Markerless structural mtDNA node {name} cannot emit source markers"
                        )
                    owner_motifs = _mt_owner_motifs(
                        name,
                        synthetic_record,
                        source["omitted_nodes"],
                        issues,
                    )
                    for owner, motif in owner_motifs.items():
                        if _mt_validate_mutation_list(owner, motif, issues):
                            issues.append(
                                f"Markerless structural mtDNA node {name} has an emitted "
                                "source decision"
                            )
    return issues


def _validate_mt_registry_against_tree(
    source: dict[str, Any], inventory: MtTreeInventory
) -> list[str]:
    """Validate the complete dynamic mtDNA state partition against the tree."""
    if inventory.duplicates:
        return [
            "Duplicate emitted mtDNA node "
            + name
            + " occurs at "
            + ", ".join("/".join(match.path) for match in matches)
            for name, matches in sorted(inventory.duplicates.items())
        ]

    issues: list[str] = []
    nodes = source["nodes"]
    structural = source["structural_exceptions"]
    pending = source["pending_nodes"]
    omitted = source["omitted_nodes"]
    retired = source["retired_emitted_nodes"]
    exact_names = set(nodes)
    structural_names = set(structural)
    pending_names = set(pending)
    emitted_names = set(inventory.by_name)
    retired_names = set(retired)
    retired_emitted_overlap = retired_names & emitted_names
    if retired_emitted_overlap:
        issues.append(
            "Retired mtDNA nodes are still emitted in the tree: "
            + ", ".join(sorted(retired_emitted_overlap))
        )
    partition = exact_names | structural_names | pending_names
    if partition != emitted_names:
        missing = sorted(emitted_names - partition)
        extra = sorted(partition - emitted_names)
        issues.append(
            f"mtDNA provenance partition differs from the emitted tree; missing={missing!r}, "
            f"extra={extra!r}"
        )
    if exact_names | pending_names != set(inventory.marker_bearing_names):
        issues.append("mtDNA marker-bearing nodes do not equal exact plus pending states")
    if structural_names != set(inventory.markerless_names):
        issues.append("mtDNA markerless nodes do not equal the structural exceptions")
    emitted_omissions = set(omitted) & emitted_names
    if emitted_omissions:
        issues.append(
            "Omitted mtDNA source nodes are emitted in the tree: "
            + ", ".join(sorted(emitted_omissions))
        )

    for category, records in (
        ("Marker-exact", nodes),
        ("Pending", pending),
        ("Structural", structural),
    ):
        for name, record in records.items():
            occurrence = inventory.by_name.get(name)
            if occurrence is None or not isinstance(record, dict):
                continue
            if record.get("emitted_parent") != occurrence.parent:
                issues.append(
                    f"{category} mtDNA node {name} declares parent "
                    f"{record.get('emitted_parent')!r}; emitted parent is "
                    f"{occurrence.parent!r}"
                )

    tree_digest = _canonical_json_sha256(_mt_tree_projection(inventory))
    if tree_digest != source["migration"].get("locked_emitted_tree_sha256"):
        issues.append("mtDNA emitted tree differs from its live locked fingerprint")
    if tree_digest != _MT_LOCKED_EMITTED_TREE_SHA256:
        issues.append("mtDNA emitted tree differs from the review-locked live tree")

    issues.extend(_validate_exact_mt_markers(source, inventory))
    issues.extend(_validate_mt_structural_records(source, inventory))

    source_nodes_by_emitted: dict[str, str] = {}
    for name, record in nodes.items():
        source_node = record.get("source_node")
        if isinstance(source_node, str):
            source_nodes_by_emitted[name] = source_node
    for name, record in structural.items():
        source_node = record.get("source_node")
        if record.get("source_status") == "exact" and isinstance(source_node, str):
            source_nodes_by_emitted[name] = source_node
        elif record.get("type") == "root" and record.get("source_status") == "synthetic":
            anchor = record.get("source_topology_anchor")
            if _is_nonblank(anchor):
                source_nodes_by_emitted[name] = anchor
    source_topology_records = list(nodes.items()) + [
        (name, record)
        for name, record in structural.items()
        if record.get("source_status") == "exact"
    ]
    direct_source_identities = set(source_nodes_by_emitted.values())
    flattened_path_records: dict[str, dict[str, Any]] = {}
    for name, record in source_topology_records:
        topology = record.get("source_topology")
        if not isinstance(topology, dict) or topology.get("status") != "exact":
            continue
        source_node = record.get("source_node")
        if topology.get("source_parent") == source_node:
            issues.append(f"Exact source topology for mtDNA node {name} is self-parented")
        for path_record in topology.get("flattened_source_path", []):
            if not isinstance(path_record, dict):
                continue
            path_node = path_record.get("source_node")
            if path_node in direct_source_identities:
                issues.append(
                    f"Exact source topology for mtDNA node {name} flattens the direct "
                    f"source node {path_node} of another emitted record"
                )
            if isinstance(path_node, str):
                prior_path_record = flattened_path_records.get(path_node)
                if prior_path_record is not None and prior_path_record != path_record:
                    issues.append(
                        f"Flattened mtDNA source node {path_node} has inconsistent "
                        "provenance across emitted paths"
                    )
                flattened_path_records[path_node] = path_record
        emitted_parent = record.get("emitted_parent")
        if emitted_parent is None:
            continue
        expected_parent_source = source_nodes_by_emitted.get(emitted_parent)
        if expected_parent_source is None:
            issues.append(
                f"Exact source topology for mtDNA node {name} has a parent whose source "
                "identity is still pending"
            )
        elif topology.get("emitted_parent_source_node") != expected_parent_source:
            issues.append(
                f"Exact source topology for mtDNA node {name} names emitted-parent source "
                f"{topology.get('emitted_parent_source_node')!r}; expected "
                f"{expected_parent_source!r}"
            )

    # Marker provenance and source-edge topology migrate independently. An empty
    # marker-pending map alone is therefore not complete if any source edge or
    # markerless structural record is still pending.
    orphan_flattened_omissions = _mt_orphan_flattened_omissions(source)
    if source["migration"].get("status") == "complete" and orphan_flattened_omissions:
        issues.append(
            "Complete mtDNA migration has flattened source intermediates that are not "
            "referenced by an exact flattened path: "
            + ", ".join(sorted(orphan_flattened_omissions))
        )

    complete_ready = _mt_migration_complete_ready(source, inventory)
    expected_status = "complete" if complete_ready else "in_progress"
    if source["migration"].get("status") != expected_status:
        issues.append(
            f"mtDNA migration status must be {expected_status!r} for its live provenance state"
        )
    return issues


def _mt_referenced_flattened_source_nodes(source: dict[str, Any]) -> set[str]:
    referenced: set[str] = set()
    topology_records: list[Any] = []
    nodes = source.get("nodes", {})
    if isinstance(nodes, dict):
        topology_records.extend(nodes.values())
    structural = source.get("structural_exceptions", {})
    if isinstance(structural, dict):
        topology_records.extend(
            record
            for record in structural.values()
            if isinstance(record, dict) and record.get("source_status") == "exact"
        )
    for record in topology_records:
        if not isinstance(record, dict):
            continue
        topology = record.get("source_topology")
        if not isinstance(topology, dict) or topology.get("status") != "exact":
            continue
        path = topology.get("flattened_source_path")
        if not isinstance(path, list):
            continue
        for step in path:
            if not isinstance(step, dict):
                continue
            source_node = step.get("source_node")
            if isinstance(source_node, str):
                referenced.add(source_node)
    return referenced


def _mt_orphan_flattened_omissions(source: dict[str, Any]) -> set[str]:
    omitted = source.get("omitted_nodes", {})
    if not isinstance(omitted, dict):
        return set()
    flattened_omissions = {
        name
        for name, record in omitted.items()
        if isinstance(name, str)
        and isinstance(record, dict)
        and record.get("type") in _MT_FLATTENED_OMISSION_TYPES
    }
    return flattened_omissions - _mt_referenced_flattened_source_nodes(source)


def _mt_migration_complete_ready(source: dict[str, Any], inventory: MtTreeInventory) -> bool:
    if source["pending_nodes"]:
        return False
    retired = source.get("retired_emitted_nodes")
    if not isinstance(retired, dict) or set(retired) & set(inventory.by_name):
        return False
    motif_states = source.get("direct_source_motif_states")
    if (
        not isinstance(motif_states, dict)
        or motif_states.get("legacy_partial_nodes")
        or set(motif_states.get("exact_nodes", ())) != set(source["nodes"])
    ):
        return False
    root_occurrences = [
        occurrence for occurrence in inventory.occurrences if occurrence.parent is None
    ]
    if len(root_occurrences) != 1:
        return False
    root = root_occurrences[0]
    root_record = source.get("structural_exceptions", {}).get(root.name)
    if (
        not isinstance(root_record, dict)
        or root.name != _MT_SYNTHETIC_ROOT_NAME
        or root_record.get("type") != "root"
        or root_record.get("emitted_parent") is not None
        or root_record.get("source_status") != "synthetic"
        or root_record.get("source_topology_anchor") != _MT_SYNTHETIC_ROOT_NAME
        or bool(root.node.get("defining_snps"))
    ):
        return False
    for name, occurrence in inventory.by_name.items():
        if occurrence.parent is None:
            continue
        if name in source["nodes"]:
            topology = source["nodes"][name].get("source_topology")
            if not isinstance(topology, dict) or topology.get("status") != "exact":
                return False
        elif name in source["structural_exceptions"]:
            if source["structural_exceptions"][name].get("source_status") != "exact":
                return False
        else:
            return False
    return not _mt_orphan_flattened_omissions(source)


def _validate_mt_source(
    source: dict[str, Any], mt_tree: dict[str, Any] | None = None
) -> list[str]:
    """Compatibility entry point for complete schema and emitted-tree validation."""
    issues = _validate_mt_source_schema(source)
    if not issues:
        issues.extend(
            _validate_mt_registry_against_tree(
                source,
                _index_mt_tree(build_mt_tree() if mt_tree is None else mt_tree),
            )
        )
    return issues


def _summarize_mt_provenance(source: dict[str, Any], inventory: MtTreeInventory) -> dict[str, Any]:
    """Derive inspectable schema-v3 coverage metadata from validated records."""
    exact_names = sorted(source["nodes"])
    direct_motif_exact_names = source["direct_source_motif_states"]["exact_nodes"]
    direct_motif_partial_names = source["direct_source_motif_states"]["legacy_partial_nodes"]
    structural_names = sorted(source["structural_exceptions"])
    pending_names = sorted(source["pending_nodes"])
    omitted_names = sorted(source["omitted_nodes"])
    retired_names = sorted(source["retired_emitted_nodes"])
    exact_markers = [
        marker for record in source["nodes"].values() for marker in record["emitted_snps"]
    ]
    motifs_by_owner: dict[str, list[dict[str, Any]]] = {}
    for record in source["nodes"].values():
        motifs_by_owner[record["source_node"]] = record["direct_source_motif"]
        topology = record["source_topology"]
        if topology["status"] == "exact":
            for step in topology["flattened_source_path"]:
                motifs_by_owner.setdefault(step["source_node"], step["direct_source_motif"])
    for record in source["structural_exceptions"].values():
        if record.get("source_status") != "exact":
            continue
        motifs_by_owner[record["source_node"]] = record["direct_source_motif"]
        for step in record["source_topology"]["flattened_source_path"]:
            motifs_by_owner.setdefault(step["source_node"], step["direct_source_motif"])
    motif_decisions = [mutation for motif in motifs_by_owner.values() for mutation in motif]
    exact_direct_motif_decisions = [
        mutation
        for name in direct_motif_exact_names
        for mutation in source["nodes"][name]["direct_source_motif"]
    ]
    partial_direct_motif_decisions = [
        mutation
        for name in direct_motif_partial_names
        for mutation in source["nodes"][name]["direct_source_motif"]
    ]
    source_edges_validated = sum(
        record["source_topology"]["status"] == "exact" for record in source["nodes"].values()
    ) + sum(
        record.get("source_status") == "exact"
        for name, record in source["structural_exceptions"].items()
        if inventory.by_name[name].parent is not None
    )
    cohort_counts: dict[str, int] = {}
    for marker in exact_markers:
        cohort_id = marker["array_coverage"]["cohort_id"]
        cohort_counts[cohort_id] = cohort_counts.get(cohort_id, 0) + 1
    emitted_decisions = sum(mutation["emitted"] is True for mutation in motif_decisions)
    parsed_substitutions = [
        parsed
        for mutation in motif_decisions
        if (parsed := _mt_parse_substitution_notation(mutation.get("notation"))) is not None
    ]
    return {
        "migration_status": source["migration"]["status"],
        "emitted_nodes": len(inventory.occurrences),
        "marker_bearing_nodes": len(inventory.marker_bearing_names),
        "marker_exact_nodes": {"count": len(exact_names), "names": exact_names},
        "direct_source_motif_nodes": {
            "exact": {
                "count": len(direct_motif_exact_names),
                "names": direct_motif_exact_names,
            },
            "legacy_partial": {
                "count": len(direct_motif_partial_names),
                "names": direct_motif_partial_names,
            },
        },
        "structural_nodes": {
            "count": len(structural_names),
            "names": structural_names,
        },
        "pending_nodes": {"count": len(pending_names), "names": pending_names},
        "retired_emitted_nodes": {"count": len(retired_names), "names": retired_names},
        "marker_records": {
            "emitted": inventory.marker_count,
            "marker_exact": len(exact_markers),
            "marker_exact_by_cohort": dict(sorted(cohort_counts.items())),
        },
        "source_mutation_decisions": {
            "total": len(motif_decisions),
            "emitted": emitted_decisions,
            "omitted": len(motif_decisions) - emitted_decisions,
            "direct_motif_exact": len(exact_direct_motif_decisions),
            "direct_motif_legacy_partial": len(partial_direct_motif_decisions),
            "recurrent_or_uncertain_events": sum(parsed[3] for parsed in parsed_substitutions),
            "reversion_events": sum(parsed[4] > 0 for parsed in parsed_substitutions),
            "reversion_marks": sum(parsed[4] for parsed in parsed_substitutions),
        },
        "emitted_parent_edges": {
            "total": inventory.edge_count,
            "validated_declarations": inventory.edge_count,
        },
        "source_parent_edges": {
            "validated": source_edges_validated,
            "pending": inventory.edge_count - source_edges_validated,
        },
        "omitted_source_nodes": {
            "count": len(omitted_names),
            "names": omitted_names,
            "by_type": dict(
                sorted(
                    Counter(record["type"] for record in source["omitted_nodes"].values()).items()
                )
            ),
        },
        "arrays": {
            "exports": len(source["array_exports"]),
            "cohorts": len(source["array_cohorts"]),
        },
        "locked_exact_frontier": {
            "count": len(source["migration"]["locked_exact_nodes"]),
            "sha256": source["migration"]["locked_exact_nodes_sha256"],
        },
        "locked_direct_motif_frontier": {
            "count": len(source["migration"]["locked_direct_motif_exact_nodes"]),
            "sha256": source["migration"]["locked_direct_motif_exact_nodes_sha256"],
        },
        "digests": {
            key: source["migration"][key]
            for key in (
                "array_manifest_sha256",
                "baseline_snapshot_sha256",
                "baseline_emitted_tree_sha256",
                "locked_emitted_tree_sha256",
                "baseline_exact_nodes_sha256",
                "baseline_v1_coverage_sha256",
                "baseline_v1_semantic_sha256",
                "baseline_v2_coverage_membership_sha256",
                "baseline_v2_registry_semantic_sha256",
                "baseline_direct_motif_exact_nodes_sha256",
                "baseline_direct_motif_semantic_sha256",
                "initial_pending_nodes_sha256",
                "initial_direct_motif_pending_nodes_sha256",
                "locked_direct_motif_exact_nodes_sha256",
                "locked_direct_motif_semantic_sha256",
                "locked_exact_coverage_membership_sha256",
                "locked_exact_semantic_sha256",
                "source_metadata_sha256",
                "state_partition_sha256",
            )
        },
        "baseline_commit": source["migration"]["baseline_commit"],
    }


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


def _validate_mt_reportability(
    node: dict[str, Any],
    ancestor_rsids: frozenset[str] = frozenset(),
    ancestor_positions: frozenset[int] = frozenset(),
) -> list[str]:
    """Require every reportable mtDNA node to add branch-specific evidence."""
    issues: list[str] = []
    snps = [
        snp
        for snp in node.get("defining_snps", [])
        if isinstance(snp, dict)
        and isinstance(snp.get("rsid"), str)
        and isinstance(snp.get("pos"), int)
    ]
    current_rsids = frozenset(snp["rsid"] for snp in snps)
    current_positions = frozenset(snp["pos"] for snp in snps)
    if node["haplogroup"] not in {"mt-MRCA", "R0"} and not any(
        snp["rsid"] not in ancestor_rsids and snp["pos"] not in ancestor_positions for snp in snps
    ):
        issues.append(f"mtDNA node {node['haplogroup']} has no ancestor-distinguishing marker")

    next_rsids = ancestor_rsids | current_rsids
    next_positions = ancestor_positions | current_positions
    for child in node.get("children", []):
        issues.extend(_validate_mt_reportability(child, next_rsids, next_positions))
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
    mt_inventory = _index_mt_tree(mt_tree)
    mt_source_issues = _validate_mt_source_schema(_MT_SOURCE)
    mt_reference_issues = (
        [] if mt_source_issues else _validate_mt_registry_against_tree(_MT_SOURCE, mt_inventory)
    )
    mt_reportability_issues = _validate_mt_reportability(mt_tree)
    y_issues = _validate_tree(y_tree)
    y_reference_issues = _validate_audited_y_rsids(y_tree)
    y_duplicate_issues = _validate_y_cross_clade_duplicates(y_tree)
    trusted_y_markers = frozenset(_Y_SOURCE["assignment"]["trusted_single_marker_terminal_rsids"])
    y_reportability_issues = _validate_y_reportability(y_tree, trusted_y_markers)
    if (
        mt_issues
        or mt_source_issues
        or mt_reference_issues
        or mt_reportability_issues
        or y_issues
        or y_reference_issues
        or y_duplicate_issues
        or y_reportability_issues
    ):
        all_issues = (
            mt_issues
            + mt_source_issues
            + mt_reference_issues
            + mt_reportability_issues
            + y_issues
            + y_reference_issues
            + y_duplicate_issues
            + y_reportability_issues
        )
        raise ValueError(
            f"Tree validation failed with {len(all_issues)} issues:\n"
            + "\n".join(f"  - {i}" for i in all_issues)
        )

    mt_provenance = _summarize_mt_provenance(_MT_SOURCE, mt_inventory)

    mt_snp_rsids = _collect_snp_rsids(mt_tree)
    y_snp_rsids = _collect_snp_rsids(y_tree)

    bundle = {
        "module": "haplogroup",
        "version": BUNDLE_VERSION,
        "description": (
            "Source-audited PhyloTree mtDNA + YBrowse Y-chromosome haplogroup "
            "defining SNP trees for haplogroup assignment via tree-walk algorithm. "
            "SNPs filtered to 23andMe v5-era array coverage. Resolution varies "
            "with the markers typed by each array revision."
        ),
        "build": BUILD,
        "assignment": {
            "Y": _Y_SOURCE["assignment"],
        },
        "sources": {
            "mt": {
                **_MT_SOURCE["source"],
                "references": _MT_SOURCE["references"],
                "audit": {
                    "schema_version": _MT_SOURCE["schema_version"],
                    "scope": _MT_SOURCE["audit_scope"],
                    "audited_nodes": sorted(_MT_SOURCE["nodes"]),
                    "omitted_nodes": {
                        name: record["reason"]
                        for name, record in sorted(_MT_SOURCE["omitted_nodes"].items())
                    },
                    "retired_emitted_nodes": _MT_SOURCE["retired_emitted_nodes"],
                    "provenance": mt_provenance,
                },
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
        description="Build source-audited PhyloTree mtDNA + YBrowse haplogroup JSON bundle.",
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
