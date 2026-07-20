"""Tests for the source-audited PhyloTree mtDNA + YBrowse haplogroup bundle (P3-31).

Validates:
- Bundle JSON structure and required fields
- Tree node structure (haplogroup, defining_snps, children)
- SNP entry format (rsid, pos, allele)
- mtDNA and Y-chromosome tree integrity
- Known fixture sample SNPs are present in the bundle
- Build script produces identical output
- Bundle statistics are within expected ranges
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

# ── Paths ────────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
PANELS_DIR = Path(__file__).resolve().parent.parent.parent / "backend" / "data" / "panels"
BUNDLE_FIXTURE = FIXTURES_DIR / "haplogroup_bundle.json"
BUNDLE_PRODUCTION = PANELS_DIR / "haplogroup_bundle.json"


@pytest.fixture(scope="module")
def bundle() -> dict:
    """Load the haplogroup bundle from the test fixture."""
    with BUNDLE_FIXTURE.open() as f:
        return json.load(f)


@pytest.fixture(scope="module")
def mt_tree(bundle: dict) -> dict:
    """Extract the mtDNA tree from the bundle."""
    return bundle["trees"]["mt"]


@pytest.fixture(scope="module")
def y_tree(bundle: dict) -> dict:
    """Extract the Y-chromosome tree from the bundle."""
    return bundle["trees"]["Y"]


# ── Helpers ──────────────────────────────────────────────────────────────


def collect_all_nodes(node: dict) -> list[dict]:
    """Recursively collect all nodes from a tree."""
    nodes = [node]
    for child in node.get("children", []):
        nodes.extend(collect_all_nodes(child))
    return nodes


def collect_all_snps(node: dict) -> list[dict]:
    """Recursively collect all defining SNPs from a tree."""
    snps = list(node.get("defining_snps", []))
    for child in node.get("children", []):
        snps.extend(collect_all_snps(child))
    return snps


def collect_haplogroup_names(node: dict) -> list[str]:
    """Recursively collect all haplogroup names from a tree."""
    names = [node["haplogroup"]]
    for child in node.get("children", []):
        names.extend(collect_haplogroup_names(child))
    return names


def tree_max_depth(node: dict, depth: int = 0) -> int:
    """Get the maximum depth of the tree."""
    if not node.get("children"):
        return depth
    return max(tree_max_depth(c, depth + 1) for c in node["children"])


def find_node(node: dict, haplogroup: str) -> dict | None:
    """Find a node by haplogroup name."""
    if node["haplogroup"] == haplogroup:
        return node
    for child in node.get("children", []):
        result = find_node(child, haplogroup)
        if result is not None:
            return result
    return None


def snp_key(snp: dict) -> tuple[str, int, str]:
    """Return the identity tuple for a defining SNP record."""
    return (snp["rsid"], snp["pos"], snp["allele"])


def get_path_to(node: dict, target: str, path: list[str] | None = None) -> list[str] | None:
    """Get the path from root to a target haplogroup."""
    if path is None:
        path = []
    current_path = [*path, node["haplogroup"]]
    if node["haplogroup"] == target:
        return current_path
    for child in node.get("children", []):
        result = get_path_to(child, target, current_path)
        if result is not None:
            return result
    return None


# ── Bundle structure tests ───────────────────────────────────────────────


class TestBundleStructure:
    """Validate top-level bundle structure and metadata."""

    def test_bundle_file_exists(self) -> None:
        assert BUNDLE_FIXTURE.exists(), "Test fixture bundle not found"

    def test_production_bundle_exists(self) -> None:
        assert BUNDLE_PRODUCTION.exists(), "Production bundle not found"

    def test_bundles_are_identical(self) -> None:
        """Test fixture and production bundles must be identical."""
        fixture_content = BUNDLE_FIXTURE.read_bytes()
        production_content = BUNDLE_PRODUCTION.read_bytes()
        assert fixture_content == production_content

    def test_required_top_level_keys(self, bundle: dict) -> None:
        required = {
            "module",
            "version",
            "description",
            "build",
            "assignment",
            "sources",
            "trees",
            "stats",
        }
        assert required.issubset(bundle.keys())

    def test_module_name(self, bundle: dict) -> None:
        assert bundle["module"] == "haplogroup"

    def test_version_format(self, bundle: dict) -> None:
        parts = bundle["version"].split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)
        assert bundle["version"] == "1.1.25"

    def test_build_is_grch37(self, bundle: dict) -> None:
        assert bundle["build"] == "GRCh37"

    def test_sources_mt(self, bundle: dict) -> None:
        mt_source = bundle["sources"]["mt"]
        assert mt_source["name"] == "PhyloTree"
        assert "Build 17" in mt_source["version"]
        assert mt_source["archive_sha256"] == (
            "3fe8cf00a15e1ccb09235091016eef1af3a68f44dd9355dd2b7666f8f767b146"
        )
        assert mt_source["reference_sequence"]["accession"] == "NC_012920.1"
        audit = mt_source["audit"]
        provenance = audit["provenance"]
        assert audit["schema_version"] == 3
        assert audit["audited_nodes"] == provenance["marker_exact_nodes"]["names"]
        assert set(provenance["marker_exact_nodes"]["names"]) == {
            "A",
            "A2",
            "A5",
            "C",
            "C1",
            "C4",
            "C5",
            "D",
            "D1",
            "D2",
            "D3",
            "D4",
            "D4a",
            "D4b",
            "D5",
            "E",
            "G",
            "G1",
            "G2",
            "G2a",
            "H",
            "H1",
            "H10",
            "H11",
            "H13",
            "H13a",
            "H1a",
            "H1a1",
            "H1b",
            "H1c",
            "H1e",
            "H2",
            "H2a",
            "H2a1",
            "H2a2a",
            "H2a2a1",
            "H3",
            "H4",
            "H5a",
            "H6",
            "H6a",
            "H7",
            "I",
            "J1d",
            "K",
            "K1",
            "K1a",
            "K1b",
            "K2",
            "K2a",
            "K2b",
            "L0",
            "L0a",
            "L0a1",
            "L0a2",
            "L0b",
            "L0d",
            "L0d1",
            "L0d2",
            "L0f",
            "L0k",
            "L1",
            "L1b",
            "L1b1",
            "L1b2",
            "L1c",
            "L1c1",
            "L1c2",
            "L1c3",
            "L2",
            "L2a",
            "L2a1",
            "L2a2",
            "L2b",
            "L2b1",
            "L2c",
            "L2d",
            "L2e",
            "L3",
            "L3a",
            "L3b",
            "L3b1",
            "L3d",
            "L3e",
            "L3e1",
            "L3e2",
            "L3f",
            "L4",
            "L4a",
            "L4b",
            "L5",
            "L5a",
            "L5b",
            "L6",
            "M",
            "M1",
            "M7",
            "M7a",
            "M7b",
            "M7c",
            "M8",
            "M8a",
            "M9",
            "N",
            "N1",
            "N1a",
            "N1b",
            "N9",
            "N9a",
            "N9b",
            "R",
            "S",
            "S1",
            "S2",
            "T2a",
            "U2",
            "U2e",
            "U3a",
            "U3b",
            "U5b2",
            "W",
            "W1",
            "W3",
            "X",
            "X1",
            "X2",
            "X2a",
            "X2b",
            "Y1",
            "Y2",
            "Y_mt",
            "Z",
            "Z1",
        }
        assert provenance["migration_status"] == "in_progress"
        assert provenance["emitted_nodes"] == 193
        assert provenance["marker_bearing_nodes"] == 188
        assert provenance["marker_exact_nodes"]["count"] == 133
        assert provenance["direct_source_motif_nodes"]["exact"]["count"] == 128
        assert provenance["direct_source_motif_nodes"]["legacy_partial"] == {
            "count": 5,
            "names": ["K", "K1", "K1a", "K2a", "U2e"],
        }
        assert "I" in provenance["direct_source_motif_nodes"]["exact"]["names"]
        assert set(provenance["direct_source_motif_nodes"]["exact"]["names"]) == (
            set(provenance["marker_exact_nodes"]["names"])
            - set(provenance["direct_source_motif_nodes"]["legacy_partial"]["names"])
        )
        assert set(provenance["direct_source_motif_nodes"]["exact"]["names"]).isdisjoint(
            provenance["direct_source_motif_nodes"]["legacy_partial"]["names"]
        )
        assert provenance["structural_nodes"] == {
            "count": 5,
            "names": ["H2a2", "H5", "HV", "R0", "mt-MRCA"],
        }
        assert provenance["pending_nodes"]["count"] == 55
        assert provenance["retired_emitted_nodes"] == {"count": 1, "names": ["A4"]}
        assert provenance["marker_records"] == {
            "emitted": 594,
            "marker_exact": 495,
            "marker_exact_by_cohort": {
                "historical_five_23andme_including_2014": 13,
                "primary_four_23andme": 482,
            },
        }
        assert provenance["source_mutation_decisions"] == {
            "total": 803,
            "emitted": 495,
            "omitted": 308,
            "direct_motif_exact": 645,
            "direct_motif_legacy_partial": 13,
            "recurrent_or_uncertain_events": 9,
            "reversion_events": 63,
            "reversion_marks": 67,
        }
        assert provenance["emitted_parent_edges"] == {
            "total": 192,
            "validated_declarations": 192,
        }
        assert provenance["source_parent_edges"] == {
            "validated": 123,
            "pending": 69,
        }
        assert provenance["omitted_source_nodes"] == {
            "count": 54,
            "names": [
                "A+152",
                "A+152+16362",
                "CZ",
                "D+16189",
                "D4b1",
                "D4b1c",
                "D4e",
                "D4e1",
                "D4e1'3",
                "G2a'c",
                "H+195",
                "H1+16189",
                "H5'36",
                "K1c",
                "L0a'b'f'g",
                "L0a'b'f'g'k",
                "L0a'b'g",
                "L0a'g",
                "L0a1'4",
                "L0d1'2",
                "L1'2'3'4'5'6",
                "L1b2'3",
                "L1c1'2'4'5'6",
                "L1c1'2'4'6",
                "L1c2'4",
                "L2'3'4'5'6",
                "L2'3'4'6",
                "L2a'b'c'd",
                "L2a1'2'3'4",
                "L2a2'3",
                "L2a2'3'4",
                "L2b'c",
                "L2b'c'd",
                "L3'4",
                "L3'4'6",
                "L3b'f",
                "L3c'd",
                "L3e'i'k'x",
                "M1'20'51",
                "M12'G",
                "M7b'c",
                "M80'D",
                "N1'5",
                "N1a1",
                "N1a1'2",
                "N1a1b",
                "N2",
                "W+194",
                "X1'2'3",
                "X1'3",
                "X2+225",
                "X2a'j",
                "X2b'd",
                "Z+152",
            ],
            "by_type": {
                "flattened_source_intermediate": 46,
                "flattened_unreportable_source_intermediate": 7,
                "unreportable_source_node": 1,
            },
        }
        assert provenance["arrays"] == {"exports": 6, "cohorts": 2}
        assert provenance["locked_exact_frontier"] == {
            "count": 133,
            "sha256": "203bd72b4f41a55e92e5e095551156d55e5102f7cdde5fa2572ff451451bd26c",
        }
        assert provenance["locked_direct_motif_frontier"] == {
            "count": 128,
            "sha256": "dc7a231de1a97ce6f252b2b25878eb32edb1ca2d0d0888aa77ec6dd47620fe72",
        }
        digests = provenance["digests"]
        assert {
            key: digests[key]
            for key in {
                "baseline_snapshot_sha256",
                "locked_emitted_tree_sha256",
                "locked_direct_motif_exact_nodes_sha256",
                "locked_direct_motif_semantic_sha256",
                "locked_exact_coverage_membership_sha256",
                "locked_exact_semantic_sha256",
                "source_metadata_sha256",
                "state_partition_sha256",
            }
        } == {
            "baseline_snapshot_sha256": (
                "f8aecb8ba02e5c2becbccfc40846bd3c8668d4b8c6de5be1761ab78c0d83a87e"
            ),
            "locked_emitted_tree_sha256": (
                "978d88ca53852601daef6e1614fa4742184437d8e46e5953f62dd02c16d7e1ff"
            ),
            "locked_direct_motif_exact_nodes_sha256": (
                "dc7a231de1a97ce6f252b2b25878eb32edb1ca2d0d0888aa77ec6dd47620fe72"
            ),
            "locked_direct_motif_semantic_sha256": (
                "84d79d5e8ce056de59fb4681618f33c5a5df19e0786b60763c61cf3302cc4f16"
            ),
            "locked_exact_coverage_membership_sha256": (
                "9bfd7222cadaa3a6cac6acb10097c8d7594930dac5ec30ed4e142cdb712c31ea"
            ),
            "locked_exact_semantic_sha256": (
                "5863c80a9498a620a50b3bab00aca5404538678b7fdbf7745134a174547a8283"
            ),
            "source_metadata_sha256": (
                "13755a154c19c603bac63a2195287165271571ece1e36e178a666aa35184d04b"
            ),
            "state_partition_sha256": (
                "af1fff7c71642cea289e2a9adab205e41b34af27495fa4e1029ebefae0b53f03"
            ),
        }

        # The registry uses typed omission records, while the bundle retains
        # the original name -> reason compatibility mapping.
        omitted = audit["omitted_nodes"]
        assert set(omitted) == set(provenance["omitted_source_nodes"]["names"])
        assert all(isinstance(reason, str) and reason for reason in omitted.values())
        assert audit["retired_emitted_nodes"] == {
            "A4": {
                "type": "retired_unmapped_emitted_node",
                "former_emitted_parent": "A",
                "former_defining_snps": [
                    {"rsid": "i5009347", "pos": 9347, "allele": "G"},
                    {"rsid": "i5014308", "pos": 14308, "allele": "A"},
                ],
                "reason": (
                    "Retired in issue #1798 batch 06 because emitted A4 has no exact "
                    "PhyloTree Build 17 source identity."
                ),
            }
        }
        assert {reference["id"] for reference in mt_source["references"]} == {1, 2, 3, 4, 5}

    def test_sources_y(self, bundle: dict) -> None:
        y_source = bundle["sources"]["Y"]
        assert y_source["name"] == "YBrowse hg19 SNP export"
        assert y_source["sha256"] == (
            "9ebc0b217d906f39a0aa6c0df572e44daed17d415993896dff7dd4274ff111fa"
        )
        assert len(y_source["omitted_nodes"]) == 20
        assert "BT" in y_source["omitted_nodes"]["A1"]
        assert "BT" in y_source["omitted_nodes"]["A1b"]
        assert "[6]" in y_source["omitted_nodes"]["A1"]
        assert "[6]" in y_source["omitted_nodes"]["A1b"]
        assert y_source["current_validation"]["current_rsids"] == 135
        assert y_source["current_validation"]["failed_marker_records"] == 0

    def test_y_assignment_policy_is_bundled(self, bundle: dict) -> None:
        policy = bundle["assignment"]["Y"]
        assert policy["min_internal_terminal_specific_snps"] == 2
        assert policy["trusted_missing_internal_passthrough_rsids"] == [
            "rs2032599",
            "rs2033003",
        ]
        assert set(policy["trusted_single_marker_terminal_rsids"])

    def test_trees_has_mt_and_y(self, bundle: dict) -> None:
        assert "mt" in bundle["trees"]
        assert "Y" in bundle["trees"]

    def test_stats_present(self, bundle: dict) -> None:
        stats = bundle["stats"]
        required = {
            "mt_haplogroups",
            "mt_defining_snps",
            "mt_unique_snps",
            "mt_max_depth",
            "y_haplogroups",
            "y_defining_snps",
            "y_unique_snps",
            "y_max_depth",
            "y_source_haplogroups",
            "y_omitted_haplogroups",
            "total_haplogroups",
            "total_defining_snps",
            "total_unique_snps",
        }
        assert required.issubset(stats.keys())

    def test_bundle_size_reasonable(self) -> None:
        """Bundle should be ~200 KB (100-300 KB range)."""
        size_kb = BUNDLE_FIXTURE.stat().st_size / 1024
        assert 100 <= size_kb <= 300, f"Bundle size {size_kb:.1f} KB outside expected range"


# ── Tree node structure tests ────────────────────────────────────────────


class TestTreeNodeStructure:
    """Validate individual tree node format."""

    @pytest.mark.parametrize("tree_key", ["mt", "Y"])
    def test_root_has_haplogroup(self, bundle: dict, tree_key: str) -> None:
        root = bundle["trees"][tree_key]
        assert "haplogroup" in root

    @pytest.mark.parametrize("tree_key", ["mt", "Y"])
    def test_root_has_defining_snps(self, bundle: dict, tree_key: str) -> None:
        root = bundle["trees"][tree_key]
        assert "defining_snps" in root

    @pytest.mark.parametrize("tree_key", ["mt", "Y"])
    def test_root_has_children(self, bundle: dict, tree_key: str) -> None:
        root = bundle["trees"][tree_key]
        assert "children" in root
        assert len(root["children"]) > 0

    @pytest.mark.parametrize("tree_key", ["mt", "Y"])
    def test_all_nodes_have_required_fields(self, bundle: dict, tree_key: str) -> None:
        """Every node must have 'haplogroup' and 'defining_snps'."""
        nodes = collect_all_nodes(bundle["trees"][tree_key])
        for node in nodes:
            assert "haplogroup" in node, "Missing 'haplogroup' in node"
            assert "defining_snps" in node, (
                f"Missing 'defining_snps' in {node.get('haplogroup', '?')}"
            )

    @pytest.mark.parametrize("tree_key", ["mt", "Y"])
    def test_all_snps_have_required_fields(self, bundle: dict, tree_key: str) -> None:
        """Every SNP must have 'rsid', 'pos', 'allele'."""
        snps = collect_all_snps(bundle["trees"][tree_key])
        for snp in snps:
            assert "rsid" in snp, f"Missing 'rsid' in SNP: {snp}"
            assert "pos" in snp, f"Missing 'pos' in SNP: {snp}"
            assert "allele" in snp, f"Missing 'allele' in SNP: {snp}"

    @pytest.mark.parametrize("tree_key", ["mt", "Y"])
    def test_snp_positions_are_positive_integers(self, bundle: dict, tree_key: str) -> None:
        snps = collect_all_snps(bundle["trees"][tree_key])
        for snp in snps:
            assert isinstance(snp["pos"], int), f"Non-integer pos: {snp}"
            assert snp["pos"] > 0, f"Non-positive pos: {snp}"

    @pytest.mark.parametrize("tree_key", ["mt", "Y"])
    def test_snp_alleles_are_valid(self, bundle: dict, tree_key: str) -> None:
        snps = collect_all_snps(bundle["trees"][tree_key])
        valid_alleles = {"A", "C", "G", "T"}
        for snp in snps:
            assert snp["allele"] in valid_alleles, f"Invalid allele: {snp}"

    @pytest.mark.parametrize("tree_key", ["mt", "Y"])
    def test_snp_rsids_are_strings(self, bundle: dict, tree_key: str) -> None:
        snps = collect_all_snps(bundle["trees"][tree_key])
        for snp in snps:
            assert isinstance(snp["rsid"], str), f"Non-string rsid: {snp}"
            assert len(snp["rsid"]) > 0, f"Empty rsid: {snp}"

    @pytest.mark.parametrize("tree_key", ["mt", "Y"])
    def test_haplogroup_names_are_non_empty(self, bundle: dict, tree_key: str) -> None:
        names = collect_haplogroup_names(bundle["trees"][tree_key])
        for name in names:
            assert isinstance(name, str)
            assert len(name) > 0


# ── mtDNA tree-specific tests ────────────────────────────────────────────


class TestMtDNATree:
    """Validate mtDNA (PhyloTree) tree content."""

    def test_root_is_mt_mrca(self, mt_tree: dict) -> None:
        assert mt_tree["haplogroup"] == "mt-MRCA"

    def test_root_has_no_defining_snps(self, mt_tree: dict) -> None:
        """Root (mt-MRCA) should have no defining SNPs."""
        assert len(mt_tree["defining_snps"]) == 0

    def test_major_haplogroups_present(self, mt_tree: dict) -> None:
        """All major African + out-of-Africa haplogroups should exist."""
        names = set(collect_haplogroup_names(mt_tree))
        expected = {"L0", "L1", "L2", "L3", "M", "N", "R"}
        assert expected.issubset(names), f"Missing: {expected - names}"

    def test_common_european_haplogroups(self, mt_tree: dict) -> None:
        """Common European haplogroups should be present."""
        names = set(collect_haplogroup_names(mt_tree))
        expected = {"H", "H1", "H1a", "J", "T", "U", "K", "V", "W", "X"}
        assert expected.issubset(names), f"Missing: {expected - names}"

    def test_common_asian_haplogroups(self, mt_tree: dict) -> None:
        """Common Asian haplogroups should be present."""
        names = set(collect_haplogroup_names(mt_tree))
        expected = {"A", "B", "C", "D", "F"}
        assert expected.issubset(names), f"Missing: {expected - names}"

    def test_h1a_path_exists(self, mt_tree: dict) -> None:
        """H1a should be reachable: mt-MRCA → L3 → N → R → ... → H → H1 → H1a."""
        path = get_path_to(mt_tree, "H1a")
        assert path is not None, "H1a not found in tree"
        assert path[0] == "mt-MRCA"
        assert "L3" in path
        assert "N" in path
        assert "R" in path
        assert "H" in path
        assert "H1" in path
        assert path[-1] == "H1a"

    def test_k_path_descends_through_u8(self, mt_tree: dict) -> None:
        """#1337: PhyloTree Build 17 places mtDNA K below U8, not directly below R."""
        path = get_path_to(mt_tree, "K")
        assert path == ["mt-MRCA", "L3", "N", "R", "U", "U8", "K"]

        r_node = find_node(mt_tree, "R")
        assert r_node is not None
        assert "K" not in {child["haplogroup"] for child in r_node["children"]}

        u8_node = find_node(mt_tree, "U8")
        assert u8_node is not None
        assert "K" in {child["haplogroup"] for child in u8_node["children"]}

    def test_macro_haplogroup_alleles_follow_phylotree_direction(self, mt_tree: dict) -> None:
        """#1080: macro mtDNA alleles must use the forward evolutionary allele."""

        def allele_map(haplogroup: str) -> dict[int, str]:
            node = find_node(mt_tree, haplogroup)
            assert node is not None, f"{haplogroup} not found"
            return {snp["pos"]: snp["allele"] for snp in node["defining_snps"]}

        assert allele_map("L0") == {
            1048: "T",
            3516: "A",
            5442: "C",
            6185: "C",
            9347: "G",
            10589: "A",
            12007: "A",
            12720: "G",
        }
        assert allele_map("L3") == {1018: "G"}
        assert allele_map("N") == {9540: "T"}
        assert allele_map("N1") == {1719: "A", 10238: "C", 12501: "A"}
        assert allele_map("N1a") == {204: "C", 13780: "G"}
        assert allele_map("R") == {12705: "C", 16223: "C"}

    def test_issue_1798_root_l0_batch_uses_exact_build17_marker_sets(self, mt_tree: dict) -> None:
        """All 16 batch-01 nodes retain only their reviewed reportable markers."""

        def allele_map(haplogroup: str) -> dict[int, str]:
            """Return the emitted position-to-allele contract for one mtDNA node."""
            node = find_node(mt_tree, haplogroup)
            assert node is not None, f"{haplogroup} not found"
            return {snp["pos"]: snp["allele"] for snp in node["defining_snps"]}

        expected_markers = {
            "L0": {
                1048: "T",
                3516: "A",
                5442: "C",
                6185: "C",
                9347: "G",
                10589: "A",
                12007: "A",
                12720: "G",
            },
            "L0a": {5231: "A", 5460: "A", 11176: "A", 14308: "C"},
            "L0a1": {5096: "C"},
            "L0a2": {
                64: "T",
                5147: "A",
                5711: "G",
                6257: "A",
                8460: "G",
                11172: "G",
                16129: "G",
            },
            "L0b": {
                6719: "C",
                15106: "A",
                15622: "C",
                16051: "G",
                16164: "G",
            },
            "L0d": {
                1438: "A",
                4232: "C",
                8152: "A",
                8251: "A",
                12121: "C",
                15466: "A",
                15930: "A",
                15941: "C",
                16243: "C",
            },
            "L0d1": {719: "A", 2706: "A", 3438: "A", 6266: "G", 13759: "A"},
            "L0d2": {
                3981: "G",
                4025: "T",
                4044: "G",
                7154: "G",
                11854: "C",
                15766: "G",
            },
            "L0f": {
                207: "A",
                4964: "T",
                9581: "C",
                9620: "T",
                13470: "G",
                14109: "T",
                15852: "C",
                16169: "T",
                16327: "T",
            },
            "L0k": {
                199: "C",
                850: "C",
                1243: "C",
                4541: "A",
                4907: "C",
                5811: "G",
                8911: "C",
                8994: "A",
                9136: "G",
                10499: "G",
                10920: "T",
                11299: "C",
                11653: "G",
                13590: "A",
                13928: "C",
                14020: "C",
                14182: "C",
                14371: "C",
                16129: "G",
                16291: "G",
            },
            "L1": {3666: "A", 7389: "C", 13789: "C", 14178: "C", 14560: "A"},
            "L2": {
                2416: "C",
                8206: "A",
                9221: "G",
                10115: "C",
                13590: "A",
                16390: "A",
            },
            "L3": {1018: "G"},
            "L4": {5460: "A", 16362: "C"},
            "L5": {3423: "C", 7972: "G", 12950: "G", 16148: "T"},
            "L6": {
                146: "C",
                961: "C",
                1461: "G",
                4964: "T",
                5267: "C",
                6002: "G",
                6284: "G",
                9332: "T",
                10978: "G",
                11116: "C",
                12771: "A",
                13710: "G",
                15244: "G",
                15289: "C",
                16048: "A",
            },
        }
        assert {name: allele_map(name) for name in expected_markers} == expected_markers

        obsolete_positions = {
            "L0": {9042},
            "L0a": {1438, 9042},
            "L0a1": {7158, 9818, 14308},
            "L0a2": {7256, 11899},
            "L0b": {3693, 5580, 12171},
            "L0d": {1715, 9755},
            "L0d1": {8113, 15466},
            "L0d2": {2969, 10394},
            "L0f": {3396, 10586},
            "L0k": {2352, 11176},
            "L1": {7055, 10589},
            "L2": {2789, 7175, 7771},
            "L3": {769, 16311},
            "L4": {5108, 10685},
            "L5": {5108, 15301},
            "L6": {3396, 7146, 10589},
        }
        assert all(
            obsolete_positions[name].isdisjoint(allele_map(name)) for name in obsolete_positions
        )
        assert allele_map("L0a")[11176] == "A"
        assert 11176 not in allele_map("L0k")

    def test_issue_1742_nodes_use_exact_reportable_build17_motifs(self, mt_tree: dict) -> None:
        """U5b2, W1, and U3b must not regress to ancestral or sibling markers."""

        def allele_map(haplogroup: str) -> dict[int, str]:
            node = find_node(mt_tree, haplogroup)
            assert node is not None, f"{haplogroup} not found"
            return {snp["pos"]: snp["allele"] for snp in node["defining_snps"]}

        assert allele_map("U5b2") == {1721: "T", 13637: "G"}
        assert allele_map("W1") == {7864: "T"}
        assert allele_map("U3b") == {4188: "G", 9656: "C", 13743: "C"}
        assert 12669 not in allele_map("W1")
        assert 9266 not in allele_map("U3b")

    def test_issue_1794_u3a_uses_exact_reportable_build17_motif(self, mt_tree: dict) -> None:
        """U3a uses its direct motif while remote m.3834 homoplasy remains elsewhere."""

        def allele_map(haplogroup: str) -> dict[int, str]:
            node = find_node(mt_tree, haplogroup)
            assert node is not None, f"{haplogroup} not found"
            return {snp["pos"]: snp["allele"] for snp in node["defining_snps"]}

        assert allele_map("U3a") == {6518: "T", 10506: "G", 13934: "T", 16390: "A"}
        assert 3834 not in allele_map("U3a")
        assert allele_map("U9")[3834] == "A"
        assert allele_map("Y1")[3834] == "A"

    def test_issue_1795_i_uses_the_cumulative_build17_lineage_motif(self, mt_tree: dict) -> None:
        """I follows the exact N1a spine and emits only its reportable sparse motif."""
        node = find_node(mt_tree, "I")
        n = find_node(mt_tree, "N")
        n1a = find_node(mt_tree, "N1a")
        assert node is not None
        assert n is not None
        assert n1a is not None
        assert {snp["pos"]: snp["allele"] for snp in node["defining_snps"]} == {
            10034: "C",
            15043: "A",
            16129: "A",
        }
        assert get_path_to(mt_tree, "I") == ["mt-MRCA", "L3", "N", "N1", "N1a", "I"]
        assert "I" not in {child["haplogroup"] for child in n["children"]}
        assert "I" in {child["haplogroup"] for child in n1a["children"]}

    def test_issue_1899_n_spine_uses_exact_reportable_build17_motifs(self, mt_tree: dict) -> None:
        """N1 inherits m.1719 from N1'5 while N1/N1a own their direct rows."""

        def allele_map(haplogroup: str) -> dict[int, str]:
            node = find_node(mt_tree, haplogroup)
            assert node is not None
            return {snp["pos"]: snp["allele"] for snp in node["defining_snps"]}

        assert allele_map("N") == {9540: "T"}
        assert allele_map("N1") == {1719: "A", 10238: "C", 12501: "A"}
        assert allele_map("N1a") == {204: "C", 13780: "G"}
        assert allele_map("N1b") == {
            1598: "A",
            5471: "A",
            8251: "A",
            16176: "G",
            16390: "A",
        }
        assert {8701, 10398, 10873, 15301}.isdisjoint(allele_map("N"))
        assert {6365, 10398}.isdisjoint(allele_map("N1"))
        assert {152, 6365, 10398}.isdisjoint(allele_map("N1a"))

    def test_issue_1796_k1b_uses_exact_reportable_build17_marker(self, mt_tree: dict) -> None:
        """K1b uses direct G5913A rather than unsupported C14167T."""

        k1b = find_node(mt_tree, "K1b")
        assert k1b is not None
        assert k1b["defining_snps"] == [{"rsid": "i5005913", "pos": 5913, "allele": "A"}]

    def test_issue_1797_m8_ancestry_and_marker_placement(self, mt_tree: dict) -> None:
        """M8 owns its direct motif and routes M8a, C, and Z around omitted CZ."""

        def allele_map(haplogroup: str) -> dict[int, str]:
            node = find_node(mt_tree, haplogroup)
            assert node is not None, f"{haplogroup} not found"
            return {snp["pos"]: snp["allele"] for snp in node["defining_snps"]}

        m = find_node(mt_tree, "M")
        m8 = find_node(mt_tree, "M8")
        assert m is not None
        assert m8 is not None

        assert {child["haplogroup"] for child in m["children"]}.isdisjoint({"C", "Z"})
        assert {child["haplogroup"] for child in m8["children"]} == {"M8a", "C", "Z"}
        assert "CZ" not in collect_haplogroup_names(mt_tree)
        assert get_path_to(mt_tree, "M8a") == ["mt-MRCA", "L3", "M", "M8", "M8a"]
        assert get_path_to(mt_tree, "C") == ["mt-MRCA", "L3", "M", "M8", "C"]
        assert get_path_to(mt_tree, "Z") == ["mt-MRCA", "L3", "M", "M8", "Z"]

        assert allele_map("M8") == {4715: "G", 8584: "A", 15487: "T"}
        assert allele_map("M8a") == {6179: "A", 8684: "T", 14470: "C"}
        assert allele_map("C") == {
            3552: "A",
            9545: "G",
            11914: "A",
            13263: "G",
            14318: "C",
        }
        assert allele_map("Z") == {6752: "G", 9090: "C", 15784: "C"}

    def test_issue_1798_nodes_use_exact_reportable_build17_motifs(self, mt_tree: dict) -> None:
        """The five direct-row conflicts retain only their source-backed alleles."""

        def allele_map(haplogroup: str) -> dict[int, str]:
            node = find_node(mt_tree, haplogroup)
            assert node is not None, f"{haplogroup} not found"
            return {snp["pos"]: snp["allele"] for snp in node["defining_snps"]}

        assert allele_map("L2c") == {
            93: "G",
            325: "T",
            680: "C",
            3200: "A",
            13928: "C",
            13958: "C",
            15849: "T",
        }
        assert allele_map("M1") == {
            14110: "C",
            6446: "A",
            6680: "C",
            12950: "C",
            16129: "A",
            16249: "C",
        }
        assert allele_map("S") == {8404: "C"}
        assert allele_map("X") == {6221: "C", 6371: "T", 13966: "G", 14470: "C"}
        assert allele_map("H10") == {14470: "A"}

        assert allele_map("I")[16129] == allele_map("M1")[16129] == "A"
        assert allele_map("M8a")[14470] == allele_map("X")[14470] == "C"
        assert allele_map("H10")[14470] == "A"

    def test_issue_1808_n9_does_not_borrow_r_defining_mutation(self, mt_tree: dict) -> None:
        """N9 retains only G5417A while R owns T12705C and T16223C."""

        def allele_map(haplogroup: str) -> dict[int, str]:
            node = find_node(mt_tree, haplogroup)
            assert node is not None, f"{haplogroup} not found"
            return {snp["pos"]: snp["allele"] for snp in node["defining_snps"]}

        assert allele_map("N9") == {5417: "A"}
        assert allele_map("R") == {12705: "C", 16223: "C"}

    def test_issue_1798_batch_04_d_subtree_matches_build17_order(self, mt_tree: dict) -> None:
        """D4 ancestry and sibling order remain deterministic for equal-score calls."""

        def allele_map(haplogroup: str) -> dict[int, str]:
            node = find_node(mt_tree, haplogroup)
            assert node is not None, f"{haplogroup} not found"
            return {snp["pos"]: snp["allele"] for snp in node["defining_snps"]}

        d = find_node(mt_tree, "D")
        d4 = find_node(mt_tree, "D4")
        d4b = find_node(mt_tree, "D4b")
        assert d is not None
        assert d4 is not None
        assert d4b is not None

        assert [child["haplogroup"] for child in d["children"]] == ["D4", "D5"]
        assert [child["haplogroup"] for child in d4["children"]] == [
            "D1",
            "D4a",
            "D4b",
            "D2",
        ]
        assert [child["haplogroup"] for child in d4b["children"]] == ["D3"]
        assert get_path_to(mt_tree, "D3") == [
            "mt-MRCA",
            "L3",
            "M",
            "D",
            "D4",
            "D4b",
            "D3",
        ]
        assert get_path_to(mt_tree, "D2") == ["mt-MRCA", "L3", "M", "D", "D4", "D2"]
        assert get_path_to(mt_tree, "D5") == ["mt-MRCA", "L3", "M", "D", "D5"]

        assert allele_map("D") == {4883: "T", 5178: "A", 16362: "C"}
        assert allele_map("D4") == {3010: "A", 8414: "T", 14668: "T"}
        assert allele_map("D1") == {2092: "T", 16325: "C"}
        assert allele_map("D4a") == {3206: "T", 8473: "C", 14979: "C", 16129: "A"}
        assert allele_map("D4b") == {8020: "A"}
        assert allele_map("D2") == {8703: "T", 16129: "A"}
        assert allele_map("D3") == {722: "T", 4023: "C", 6374: "C", 9785: "T"}
        assert allele_map("D5") == {1107: "C", 5301: "G", 10397: "G"}

    def test_issue_1798_batch_05_m_sibling_subtrees_match_build17(self, mt_tree: dict) -> None:
        """M9/E, G, M7, and M8/CZ retain exact emitted order and marker rows."""

        def allele_map(haplogroup: str) -> dict[int, str]:
            node = find_node(mt_tree, haplogroup)
            assert node is not None, f"{haplogroup} not found"
            return {snp["pos"]: snp["allele"] for snp in node["defining_snps"]}

        m = find_node(mt_tree, "M")
        m9 = find_node(mt_tree, "M9")
        g = find_node(mt_tree, "G")
        g2 = find_node(mt_tree, "G2")
        m7 = find_node(mt_tree, "M7")
        m8 = find_node(mt_tree, "M8")
        c = find_node(mt_tree, "C")
        z = find_node(mt_tree, "Z")
        assert all(node is not None for node in (m, m9, g, g2, m7, m8, c, z))

        assert [child["haplogroup"] for child in m["children"]] == [
            "D",
            "G",
            "M1",
            "M7",
            "M8",
            "M9",
        ]
        assert [child["haplogroup"] for child in m9["children"]] == ["E"]
        assert [child["haplogroup"] for child in g["children"]] == ["G1", "G2"]
        assert [child["haplogroup"] for child in g2["children"]] == ["G2a"]
        assert [child["haplogroup"] for child in m7["children"]] == ["M7a", "M7b", "M7c"]
        assert [child["haplogroup"] for child in m8["children"]] == ["M8a", "C", "Z"]
        assert [child["haplogroup"] for child in c["children"]] == ["C1", "C4", "C5"]
        assert [child["haplogroup"] for child in z["children"]] == ["Z1"]

        assert get_path_to(mt_tree, "E") == ["mt-MRCA", "L3", "M", "M9", "E"]
        assert get_path_to(mt_tree, "G2a") == ["mt-MRCA", "L3", "M", "G", "G2", "G2a"]
        assert get_path_to(mt_tree, "M7c") == ["mt-MRCA", "L3", "M", "M7", "M7c"]
        assert get_path_to(mt_tree, "C5") == ["mt-MRCA", "L3", "M", "M8", "C", "C5"]
        assert get_path_to(mt_tree, "Z1") == ["mt-MRCA", "L3", "M", "M8", "Z", "Z1"]

        assert allele_map("E") == {3027: "C", 3705: "A", 7598: "A", 13626: "T", 16390: "A"}
        assert allele_map("G2a") == {9575: "A", 7600: "A", 9377: "G", 16227: "G"}
        assert allele_map("M7") == {6455: "T", 9824: "C"}
        assert allele_map("M7a") == {2626: "C", 2772: "T", 4386: "C", 4958: "G", 12771: "A"}
        assert allele_map("M7b") == {12405: "T"}
        assert allele_map("M7c") == {146: "C", 11665: "T", 12091: "C"}
        assert allele_map("C1") == {16325: "C"}
        assert allele_map("C4") == {11969: "A", 15204: "C"}
        assert allele_map("C5") == {16288: "C"}
        assert allele_map("Z1") == {15261: "A"}

        emitted_names = collect_haplogroup_names(mt_tree)
        assert {"CZ", "G2a'c", "M7b'c", "Z+152"}.isdisjoint(emitted_names)

    def test_issue_1798_batch_06_n_a_i_n9_y_subtrees_match_build17(self, mt_tree: dict) -> None:
        """Batch 06 retains the audited N topology and reportable marker rows."""

        def allele_map(haplogroup: str) -> dict[int, str]:
            node = find_node(mt_tree, haplogroup)
            assert node is not None, f"{haplogroup} not found"
            return {snp["pos"]: snp["allele"] for snp in node["defining_snps"]}

        n = find_node(mt_tree, "N")
        a = find_node(mt_tree, "A")
        n1a = find_node(mt_tree, "N1a")
        n9 = find_node(mt_tree, "N9")
        y_mt = find_node(mt_tree, "Y_mt")
        assert all(node is not None for node in (n, a, n1a, n9, y_mt))

        assert [child["haplogroup"] for child in n["children"]] == [
            "A",
            "N1",
            "N9",
            "S",
            "W",
            "X",
            "R",
        ]
        assert [child["haplogroup"] for child in a["children"]] == ["A2", "A5"]
        assert [child["haplogroup"] for child in n9["children"]] == [
            "N9a",
            "N9b",
            "Y_mt",
        ]
        assert [child["haplogroup"] for child in y_mt["children"]] == ["Y1", "Y2"]

        assert get_path_to(mt_tree, "I") == ["mt-MRCA", "L3", "N", "N1", "N1a", "I"]
        assert get_path_to(mt_tree, "Y2") == ["mt-MRCA", "L3", "N", "N9", "Y_mt", "Y2"]
        emitted_names = set(collect_haplogroup_names(mt_tree))
        assert {"A4", "A+152", "A+152+16362"}.isdisjoint(emitted_names)

        expected_markers = {
            "N": {9540: "T"},
            "A": {235: "G", 663: "G", 4248: "C", 4824: "G", 8794: "T"},
            "A2": {146: "C", 8027: "A", 12007: "A", 16111: "T", 16362: "C"},
            "A5": {8563: "G", 11536: "T"},
            "N1b": {1598: "A", 5471: "A", 8251: "A", 16176: "G", 16390: "A"},
            "N9": {5417: "A"},
            "N9a": {5231: "A", 12372: "A", 16261: "T"},
            "N9b": {
                5147: "A",
                10607: "T",
                11016: "A",
                13183: "G",
                14893: "G",
            },
            "Y_mt": {
                8392: "A",
                10398: "G",
                14178: "C",
                14693: "G",
                16126: "C",
                16223: "C",
                16231: "C",
            },
            "Y1": {3834: "A"},
            "Y2": {
                482: "C",
                5147: "A",
                6941: "C",
                7859: "A",
                14914: "G",
                15244: "G",
            },
            "I": {10034: "C", 15043: "A", 16129: "A"},
        }
        assert {name: allele_map(name) for name in expected_markers} == expected_markers

        assert {8701, 10398, 10873, 15301}.isdisjoint(allele_map("N"))
        assert 16362 in allele_map("A2")
        assert 152 not in allele_map("A2")

    def test_issue_1798_batch_07_s_w_x_subtrees_match_build17(self, mt_tree: dict) -> None:
        """S, W, and X retain their exact flattened topology and callable rows."""

        def marker_pairs(haplogroup: str) -> list[tuple[int, str]]:
            node = find_node(mt_tree, haplogroup)
            assert node is not None, f"{haplogroup} not found"
            return [(snp["pos"], snp["allele"]) for snp in node["defining_snps"]]

        def allele_map(haplogroup: str) -> dict[int, str]:
            return dict(marker_pairs(haplogroup))

        n = find_node(mt_tree, "N")
        s = find_node(mt_tree, "S")
        w = find_node(mt_tree, "W")
        x = find_node(mt_tree, "X")
        x2 = find_node(mt_tree, "X2")
        assert all(node is not None for node in (n, s, w, x, x2))

        assert [child["haplogroup"] for child in n["children"]] == [
            "A",
            "N1",
            "N9",
            "S",
            "W",
            "X",
            "R",
        ]
        assert [child["haplogroup"] for child in s["children"]] == ["S1", "S2"]
        assert [child["haplogroup"] for child in w["children"]] == ["W1", "W3"]
        assert [child["haplogroup"] for child in x["children"]] == ["X1", "X2"]
        assert [child["haplogroup"] for child in x2["children"]] == ["X2a", "X2b"]

        assert get_path_to(mt_tree, "S2") == ["mt-MRCA", "L3", "N", "S", "S2"]
        assert get_path_to(mt_tree, "W3") == ["mt-MRCA", "L3", "N", "W", "W3"]
        assert get_path_to(mt_tree, "X1") == ["mt-MRCA", "L3", "N", "X", "X1"]
        assert get_path_to(mt_tree, "X2a") == [
            "mt-MRCA",
            "L3",
            "N",
            "X",
            "X2",
            "X2a",
        ]

        expected_markers = {
            "S": {8404: "C"},
            "S1": {14384: "C", 16075: "C"},
            "S2": {2380: "T", 3438: "A", 6167: "C"},
            "W": {
                207: "A",
                1243: "C",
                3505: "G",
                5460: "A",
                8251: "A",
                8994: "A",
                11947: "G",
                15884: "C",
                16292: "T",
            },
            "W1": {7864: "T"},
            "W3": {1406: "C"},
            "X": {6221: "C", 6371: "T", 13966: "G", 14470: "C"},
            "X1": {5302: "C", 15654: "C", 16104: "T"},
            "X2": {1719: "A"},
            "X2a": {8913: "G", 14502: "C"},
            "X2b": {8393: "T"},
        }
        assert {name: allele_map(name) for name in expected_markers} == expected_markers
        assert marker_pairs("X1") == [(5302, "C"), (15654, "C"), (16104, "T")]

        emitted_names = set(collect_haplogroup_names(mt_tree))
        assert {
            "N2",
            "W+194",
            "X1'2'3",
            "X1'3",
            "X2+225",
            "X2a'j",
            "X2b'd",
        }.isdisjoint(emitted_names)
        assert {146, 153, 6253}.isdisjoint(allele_map("X1"))
        assert {195, 225}.isdisjoint(allele_map("X2"))
        assert {200, 225, 12397, 16213}.isdisjoint(allele_map("X2a"))
        assert {225, 13708, 15927}.isdisjoint(allele_map("X2b"))

    def test_issue_1798_batch_08_r_h_spine_matches_build17(self, mt_tree: dict) -> None:
        """Batch 08 locks R/H parents, direct markers, and markerless gateways."""

        def marker_pairs(haplogroup: str) -> tuple[tuple[int, str], ...]:
            node = find_node(mt_tree, haplogroup)
            assert node is not None, f"{haplogroup} not found"
            return tuple((snp["pos"], snp["allele"]) for snp in node["defining_snps"])

        expected = {
            "R": ((12705, "C"), (16223, "C")),
            "R0": (),
            "HV": (),
            "H": ((2706, "A"), (7028, "C")),
            "H1": ((3010, "A"),),
            "H2": ((1438, "A"),),
            "H3": ((6776, "C"),),
            "H4": ((3992, "T"), (5004, "C")),
            "H5": (),
            "H5a": ((4336, "C"),),
            "H6": ((239, "C"), (16362, "C"), (16482, "G")),
            "H7": ((4793, "G"),),
            "H10": ((14470, "A"),),
            "H11": ((8448, "C"), (13759, "A")),
            "H13": ((14872, "T"),),
        }
        assert {name: marker_pairs(name) for name in expected} == expected
        assert get_path_to(mt_tree, "H11") == [
            "mt-MRCA",
            "L3",
            "N",
            "R",
            "R0",
            "HV",
            "H",
            "H11",
        ]
        assert get_path_to(mt_tree, "H5a") == [
            "mt-MRCA",
            "L3",
            "N",
            "R",
            "R0",
            "HV",
            "H",
            "H5",
            "H5a",
        ]
        emitted_positions = {
            position
            for name in ("HV", "H4", "H5", "H5a", "H11")
            for position, _allele in marker_pairs(name)
        }
        assert {456, 9123, 13101, 14766, 16304}.isdisjoint(emitted_positions)

    def test_issue_1798_batch_09_h_descendants_match_build17(self, mt_tree: dict) -> None:
        """Batch 09 locks H descendants, H1b's sibling edge, and markerless H2a2."""

        def marker_pairs(haplogroup: str) -> tuple[tuple[int, str], ...]:
            node = find_node(mt_tree, haplogroup)
            assert node is not None, f"{haplogroup} not found"
            return tuple((snp["pos"], snp["allele"]) for snp in node["defining_snps"])

        expected = {
            "H1a": ((73, "G"), (16162, "G")),
            "H1a1": ((6365, "C"),),
            "H1b": ((16356, "C"),),
            "H1c": ((477, "C"),),
            "H1e": ((5460, "A"),),
            "H2a": ((4769, "A"),),
            "H2a1": ((951, "A"), (16354, "T")),
            "H2a2": (),
            "H2a2a": ((8860, "A"),),
            "H2a2a1": ((263, "A"),),
            "H5a": ((4336, "C"),),
            "H6a": ((3915, "A"),),
            "H13a": ((2259, "T"),),
        }
        assert {name: marker_pairs(name) for name in expected} == expected

        h1 = find_node(mt_tree, "H1")
        h1a = find_node(mt_tree, "H1a")
        h2a = find_node(mt_tree, "H2a")
        assert h1 is not None
        assert h1a is not None
        assert h2a is not None
        assert {child["haplogroup"] for child in h1["children"]} == {
            "H1a",
            "H1b",
            "H1c",
            "H1e",
        }
        assert {child["haplogroup"] for child in h1a["children"]} == {"H1a1"}
        assert {child["haplogroup"] for child in h2a["children"]} == {"H2a1", "H2a2"}
        assert get_path_to(mt_tree, "H1b") == [
            "mt-MRCA",
            "L3",
            "N",
            "R",
            "R0",
            "HV",
            "H",
            "H1",
            "H1b",
        ]
        assert get_path_to(mt_tree, "H2a2a1") == [
            "mt-MRCA",
            "L3",
            "N",
            "R",
            "R0",
            "HV",
            "H",
            "H2",
            "H2a",
            "H2a2",
            "H2a2a",
            "H2a2a1",
        ]

        removed_positions = {
            "H1a1": {14587, 16209},
            "H1b": {3010, 16189},
            "H1c": {4310},
            "H1e": {3796, 9066},
            "H2a2": {750},
            "H2a2a": {15326},
        }
        for name, positions in removed_positions.items():
            assert positions.isdisjoint(position for position, _allele in marker_pairs(name))

    def test_issue_1907_d2_uses_exact_build17_motif(self, mt_tree: dict) -> None:
        """D2 uses its direct row while inherited D and R markers stay at their owners."""

        def marker_map(haplogroup: str) -> dict[int, tuple[str, str]]:
            node = find_node(mt_tree, haplogroup)
            assert node is not None, f"{haplogroup} not found"
            return {snp["pos"]: (snp["rsid"], snp["allele"]) for snp in node["defining_snps"]}

        assert marker_map("D2") == {
            8703: ("i5008703", "T"),
            16129: ("i5016129", "A"),
        }
        assert marker_map("D")[4883] == ("i5004883", "T")
        assert marker_map("R")[12705] == ("i5012705", "C")
        assert 4883 not in marker_map("D2")
        assert 12705 not in marker_map("D2")

    def test_issue_1814_s_children_use_exact_build17_motifs(self, mt_tree: dict) -> None:
        """S1 and S2 retain only their direct child-of-S substitutions."""

        def allele_map(haplogroup: str) -> dict[int, str]:
            node = find_node(mt_tree, haplogroup)
            assert node is not None, f"{haplogroup} not found"
            return {snp["pos"]: snp["allele"] for snp in node["defining_snps"]}

        s = find_node(mt_tree, "S")
        assert s is not None
        assert {child["haplogroup"] for child in s["children"]} == {"S1", "S2"}
        assert allele_map("S") == {8404: "C"}
        assert allele_map("S1") == {14384: "C", 16075: "C"}
        assert allele_map("S2") == {2380: "T", 3438: "A", 6167: "C"}
        assert 10238 not in allele_map("S1")
        assert 14364 not in allele_map("S2")

    def test_issue_1834_w_and_w3_use_exact_build17_motifs(self, mt_tree: dict) -> None:
        """W owns its nine callable direct markers and W3 owns only m.1406."""

        def allele_map(haplogroup: str) -> dict[int, str]:
            node = find_node(mt_tree, haplogroup)
            assert node is not None, f"{haplogroup} not found"
            return {snp["pos"]: snp["allele"] for snp in node["defining_snps"]}

        w = find_node(mt_tree, "W")
        assert w is not None
        assert {child["haplogroup"] for child in w["children"]} == {"W1", "W3"}
        assert get_path_to(mt_tree, "W") == ["mt-MRCA", "L3", "N", "W"]
        assert get_path_to(mt_tree, "W3") == ["mt-MRCA", "L3", "N", "W", "W3"]
        assert allele_map("W") == {
            207: "A",
            1243: "C",
            3505: "G",
            5460: "A",
            8251: "A",
            8994: "A",
            11947: "G",
            15884: "C",
            16292: "T",
        }
        assert allele_map("W3") == {1406: "C"}
        assert 189 not in allele_map("W")
        assert 195 not in allele_map("W")
        assert 204 not in allele_map("W")
        assert 5460 not in allele_map("W3")

    def test_issue_1849_h1a_uses_exact_build17_motif(self, mt_tree: dict) -> None:
        """H1a is H1's direct child and carries only m.73G and m.16162G."""
        h1 = find_node(mt_tree, "H1")
        h1a = find_node(mt_tree, "H1a")
        assert h1 is not None
        assert h1a is not None

        assert get_path_to(mt_tree, "H1a") == [
            "mt-MRCA",
            "L3",
            "N",
            "R",
            "R0",
            "HV",
            "H",
            "H1",
            "H1a",
        ]
        assert "H1a" in {child["haplogroup"] for child in h1["children"]}
        assert {child["haplogroup"] for child in h1a["children"]} == {"H1a1"}
        assert h1a["defining_snps"] == [
            {"rsid": "i5000073", "pos": 73, "allele": "G"},
            {"rsid": "i5016162", "pos": 16162, "allele": "G"},
        ]
        assert {13290, 13404}.isdisjoint(snp["pos"] for snp in h1a["defining_snps"])

    def test_mt_snp_positions_in_valid_range(self, mt_tree: dict) -> None:
        """mtDNA positions must be within rCRS range (1-16569)."""
        snps = collect_all_snps(mt_tree)
        for snp in snps:
            assert 1 <= snp["pos"] <= 16569, (
                f"mtDNA position {snp['pos']} out of range for {snp['rsid']}"
            )

    def test_mt_tree_depth_reasonable(self, mt_tree: dict) -> None:
        """mtDNA tree should have depth >= 3 (for 2-3 levels resolution)."""
        depth = tree_max_depth(mt_tree)
        assert depth >= 3, f"mtDNA tree depth {depth} too shallow"

    def test_haplogroup_stats_match(self, bundle: dict, mt_tree: dict) -> None:
        """Stats should accurately reflect tree contents."""
        nodes = collect_all_nodes(mt_tree)
        snps = collect_all_snps(mt_tree)
        unique_rsids = {s["rsid"] for s in snps}
        assert (len(nodes), len(snps), len(unique_rsids), tree_max_depth(mt_tree)) == (
            193,
            594,
            481,
            11,
        )
        assert bundle["stats"]["mt_haplogroups"] == len(nodes)
        assert bundle["stats"]["mt_defining_snps"] == len(snps)
        assert bundle["stats"]["mt_unique_snps"] == len(unique_rsids)
        assert bundle["stats"]["mt_max_depth"] == tree_max_depth(mt_tree)
        assert bundle["stats"]["total_haplogroups"] == 300
        assert bundle["stats"]["total_defining_snps"] == 748
        assert bundle["stats"]["total_unique_snps"] == 635

    def test_non_root_nodes_have_defining_snps(self, bundle: dict, mt_tree: dict) -> None:
        """Every non-root node should have at least one defining SNP.

        Exact structural exceptions are retained as markerless pass-throughs
        when source events cannot safely gate their emitted descendants.
        """
        nodes = collect_all_nodes(mt_tree)
        structural = set(
            bundle["sources"]["mt"]["audit"]["provenance"]["structural_nodes"]["names"]
        )
        for node in nodes[1:]:  # Skip root
            if node["haplogroup"] in structural:
                assert node["defining_snps"] == []  # documented structural exception
                continue
            assert len(node["defining_snps"]) > 0, f"{node['haplogroup']} has no defining SNPs"

    def test_every_reportable_node_has_ancestor_distinguishing_evidence(
        self, bundle: dict, mt_tree: dict
    ) -> None:
        """No emitted mtDNA label may rely only on inherited identifiers or loci."""
        failures: list[str] = []
        structural = set(
            bundle["sources"]["mt"]["audit"]["provenance"]["structural_nodes"]["names"]
        )

        def walk(node: dict, ancestor_rsids: set[str], ancestor_positions: set[int]) -> None:
            snps = node.get("defining_snps", [])
            if node["haplogroup"] not in structural and not any(
                snp["rsid"] not in ancestor_rsids and snp["pos"] not in ancestor_positions
                for snp in snps
            ):
                failures.append(node["haplogroup"])
            next_rsids = ancestor_rsids | {snp["rsid"] for snp in snps}
            next_positions = ancestor_positions | {snp["pos"] for snp in snps}
            for child in node.get("children", []):
                walk(child, next_rsids, next_positions)

        walk(mt_tree, set(), set())
        assert not failures

    def test_unreportable_source_nodes_are_not_emitted(self, bundle: dict, mt_tree: dict) -> None:
        omitted = set(bundle["sources"]["mt"]["audit"]["omitted_nodes"])
        assert omitted
        assert set(collect_haplogroup_names(mt_tree)).isdisjoint(omitted)
        assert "K1c" in omitted


# ── Y-chromosome tree-specific tests ────────────────────────────────────


class TestYChromTree:
    """Validate the source-audited Y-chromosome tree content."""

    def test_root_is_y_adam(self, y_tree: dict) -> None:
        assert y_tree["haplogroup"] == "Y-Adam"

    def test_root_has_no_defining_snps(self, y_tree: dict) -> None:
        assert len(y_tree["defining_snps"]) == 0

    def test_major_haplogroups_present(self, y_tree: dict) -> None:
        """Major Y-chr haplogroups should exist."""
        names = set(collect_haplogroup_names(y_tree))
        expected = {
            "A0",
            "A1a",
            "A1b1",
            "B",
            "C",
            "D",
            "E",
            "G",
            "I",
            "J",
            "K",
            "N_Y",
            "O",
            "R",
        }
        assert expected.issubset(names), f"Missing: {expected - names}"

    def test_common_european_haplogroups(self, y_tree: dict) -> None:
        """Common European Y-chr haplogroups."""
        names = set(collect_haplogroup_names(y_tree))
        expected = {"R1a", "R1b", "I1", "I2", "J2", "G2"}
        assert expected.issubset(names), f"Missing: {expected - names}"

    def test_r1b1a_path_exists(self, y_tree: dict) -> None:
        """R1b1a should be reachable via the expected path."""
        path = get_path_to(y_tree, "R1b1a")
        assert path is not None, "R1b1a not found in tree"
        assert path[0] == "Y-Adam"
        assert "R" in path
        assert "R1" in path
        assert "R1b" in path
        assert "R1b1" in path
        assert path[-1] == "R1b1a"

    def test_y_snp_positions_positive(self, y_tree: dict) -> None:
        """Y-chromosome positions must be positive."""
        snps = collect_all_snps(y_tree)
        for snp in snps:
            assert snp["pos"] > 0, f"Invalid Y-chr position: {snp}"

    def test_y_tree_depth_reasonable(self, y_tree: dict) -> None:
        """Y-chr tree should have depth >= 3."""
        depth = tree_max_depth(y_tree)
        assert depth >= 3, f"Y-chr tree depth {depth} too shallow"

    def test_haplogroup_stats_match(self, bundle: dict, y_tree: dict) -> None:
        nodes = collect_all_nodes(y_tree)
        snps = collect_all_snps(y_tree)
        unique_rsids = {s["rsid"] for s in snps}
        assert (len(nodes), len(snps), len(unique_rsids), tree_max_depth(y_tree)) == (
            107,
            154,
            154,
            11,
        )
        assert bundle["stats"]["y_haplogroups"] == len(nodes)
        assert bundle["stats"]["y_defining_snps"] == len(snps)
        assert bundle["stats"]["y_unique_snps"] == len(unique_rsids)
        assert bundle["stats"]["y_max_depth"] == tree_max_depth(y_tree)
        assert bundle["stats"]["y_source_haplogroups"] == 127
        assert bundle["stats"]["y_omitted_haplogroups"] == 20

    def test_every_y_node_has_ancestor_distinguishing_evidence(self, y_tree: dict) -> None:
        """Every emitted non-root node must add a new identifier and locus."""
        failures: list[str] = []

        def walk(node: dict, ancestor_rsids: set[str], ancestor_positions: set[int]) -> None:
            snps = node.get("defining_snps", [])
            if node["haplogroup"] != "Y-Adam" and not any(
                snp["rsid"] not in ancestor_rsids and snp["pos"] not in ancestor_positions
                for snp in snps
            ):
                failures.append(node["haplogroup"])
            next_rsids = ancestor_rsids | {snp["rsid"] for snp in snps}
            next_positions = ancestor_positions | {snp["pos"] for snp in snps}
            for child in node.get("children", []):
                walk(child, next_rsids, next_positions)

        walk(y_tree, set(), set())
        assert not failures

    def test_y_defining_identifiers_and_loci_are_globally_unique(self, y_tree: dict) -> None:
        snps = collect_all_snps(y_tree)
        assert snps
        assert len({snp["rsid"] for snp in snps}) == len(snps)
        assert len({snp["pos"] for snp in snps}) == len(snps)

    def test_unreportable_source_placeholders_are_not_emitted(self, bundle: dict) -> None:
        names = set(collect_haplogroup_names(bundle["trees"]["Y"]))
        omitted = set(bundle["sources"]["Y"]["omitted_nodes"])
        assert omitted
        assert names.isdisjoint(omitted)
        assert {"A1", "A1b", "GH", "MS", "R1b1a1"}.issubset(omitted)

    def test_no_prunable_y_ancestor_snp_relistings(self, y_tree: dict) -> None:
        """Y nodes should not repeat an ancestor SNP when independent SNPs remain."""
        failures: list[str] = []

        def walk(node: dict, ancestor_snps: set[tuple[str, int, str]], path: list[str]) -> None:
            current_path = [*path, node["haplogroup"]]
            snps = node.get("defining_snps", [])
            duplicate_snps = [snp for snp in snps if snp_key(snp) in ancestor_snps]
            independent_snps = [snp for snp in snps if snp_key(snp) not in ancestor_snps]
            if duplicate_snps and independent_snps:
                duplicate_labels = ", ".join(
                    f"{snp['rsid']}:{snp['allele']}" for snp in duplicate_snps
                )
                failures.append(f"{' > '.join(current_path)} repeats {duplicate_labels}")

            next_ancestor_snps = ancestor_snps | {snp_key(snp) for snp in snps}
            for child in node.get("children", []):
                walk(child, next_ancestor_snps, current_path)

        walk(y_tree, set(), [])
        assert not failures

    def test_y_rsids_are_not_reused_across_unrelated_clades(self, y_tree: dict) -> None:
        """Every repeated Y rsID must stay on one ancestor-descendant lineage."""
        locations: dict[str, list[tuple[str, ...]]] = {}

        def walk(node: dict, path: tuple[str, ...]) -> None:
            current_path = (*path, node["haplogroup"])
            for snp in node.get("defining_snps", []):
                locations.setdefault(snp["rsid"], []).append(current_path)
            for child in node.get("children", []):
                walk(child, current_path)

        walk(y_tree, ())
        failures: list[str] = []
        for rsid, paths in locations.items():
            for index, left in enumerate(paths):
                for right in paths[index + 1 :]:
                    related = left[: len(right)] == right or right[: len(left)] == left
                    if not related:
                        failures.append(f"{rsid}: {'/'.join(left)} vs {'/'.join(right)}")

        assert not failures


# ── Test fixture integration tests ──────────────────────────────────────


class TestFixtureIntegration:
    """Verify bundle SNPs align with the test fixture sample."""

    # MT rsids from sample_23andme_v5.txt (non-no-call)
    FIXTURE_MT_SNPS = {
        "rs1000318": ("MT", 10740, "T"),
        "rs1000361": ("MT", 10951, "A"),
        "rs1000731": ("MT", 13133, "T"),
        "rs1000687": ("MT", 13252, "T"),
        "rs1000390": ("MT", 13290, "T"),
        "rs1000622": ("MT", 13824, "T"),
        "rs1000223": ("MT", 14508, "G"),
    }

    # Y rsids from sample_23andme_v5.txt (non-no-call)
    FIXTURE_Y_SNPS = {
        "rs1000331": ("Y", 20085901, "T"),
        "rs1000247": ("Y", 20503721, "A"),
        "rs1000867": ("Y", 32170896, "T"),
        "rs2032658": ("Y", 15581983, "G"),
        "rs1000154": ("Y", 39970128, "G"),
        "rs1000147": ("Y", 41031901, "A"),
        "rs1000306": ("Y", 53186638, "C"),
    }

    def test_fixture_mt_snps_in_bundle(self, mt_tree: dict) -> None:
        """Test fixture's MT SNP rsids should appear in the bundle tree."""
        bundle_rsids = {s["rsid"] for s in collect_all_snps(mt_tree)}
        fixture_rsids = set(self.FIXTURE_MT_SNPS.keys())
        overlap = fixture_rsids & bundle_rsids
        # H1a no longer borrows fixture rs1000390; assignment uses rCRS positions,
        # while the U-path probe remains useful fixture overlap. M no longer
        # borrows rs1000361/m.10951 from outside its exact Build-17 motif.
        assert overlap == {"rs1000731"}

    def test_fixture_y_snps_in_bundle(self, y_tree: dict) -> None:
        """The compact fixture retains its canonical R-M207 probe in the tree."""
        bundle_rsids = {s["rsid"] for s in collect_all_snps(y_tree)}
        fixture_rsids = set(self.FIXTURE_Y_SNPS.keys())
        overlap = fixture_rsids & bundle_rsids
        assert "rs2032658" in overlap

    def test_fixture_sample_resolves_to_h_lineage(self, mt_tree: dict) -> None:
        """H is defined by G2706A and T7028C; rCRS H2a2a1 carries both.

        The spurious autosomal ``rs1000687`` (chr11) was removed as an H marker in
        #1579; Batch 08 restores H's complete primary-callable Build 17 motif.
        """
        h_node = find_node(mt_tree, "H")
        assert h_node is not None
        h_snps = {s["rsid"]: s for s in h_node["defining_snps"]}
        assert "rs1000687" not in h_snps  # autosomal chr11 id, never an mtDNA marker
        assert {rsid: (snp["pos"], snp["allele"]) for rsid, snp in h_snps.items()} == {
            "i5002706": (2706, "A"),
            "i5007028": (7028, "C"),
        }

    def test_fixture_sample_carries_canonical_r_marker(self, y_tree: dict) -> None:
        """The compact fixture still exercises canonical M207/R evidence."""
        r_node = find_node(y_tree, "R")
        assert r_node is not None
        assert "rs2032658" in {s["rsid"] for s in r_node["defining_snps"]}


# ── Build script tests ──────────────────────────────────────────────────


class TestBuildScript:
    """Test the build_haplogroup_bundle.py script."""

    def test_build_bundle_function(self) -> None:
        """build_bundle() should produce a valid bundle dict."""
        from scripts.build_haplogroup_bundle import build_bundle

        bundle = build_bundle()
        assert bundle["module"] == "haplogroup"
        assert "trees" in bundle
        assert "mt" in bundle["trees"]
        assert "Y" in bundle["trees"]

    def test_build_produces_consistent_output(self) -> None:
        """Running build_bundle() twice should produce identical output."""
        from scripts.build_haplogroup_bundle import build_bundle

        b1 = build_bundle()
        b2 = build_bundle()
        assert json.dumps(b1, sort_keys=True) == json.dumps(b2, sort_keys=True)
        assert b1 == json.loads(BUNDLE_PRODUCTION.read_text())
        expected_bytes = json.dumps(b1, indent=2, ensure_ascii=False).encode("utf-8")
        assert BUNDLE_PRODUCTION.read_bytes() == expected_bytes
        assert BUNDLE_FIXTURE.read_bytes() == expected_bytes

    def test_validate_tree_passes(self) -> None:
        """Internal validation should report no issues."""
        from scripts.build_haplogroup_bundle import (
            _MT_SOURCE,
            _Y_SOURCE,
            _validate_audited_y_rsids,
            _validate_mt_reportability,
            _validate_mt_source,
            _validate_tree,
            _validate_y_cross_clade_duplicates,
            _validate_y_reportability,
            _validate_y_source,
            build_mt_tree,
            build_y_tree,
        )

        mt_tree = build_mt_tree()
        mt_issues = _validate_tree(mt_tree)
        mt_source_issues = _validate_mt_source(_MT_SOURCE, mt_tree)
        mt_reportability_issues = _validate_mt_reportability(mt_tree)
        y_tree = build_y_tree()
        y_issues = _validate_tree(y_tree)
        y_reference_issues = _validate_audited_y_rsids(y_tree)
        y_duplicate_issues = _validate_y_cross_clade_duplicates(y_tree)
        y_source_issues = _validate_y_source(_Y_SOURCE)
        trusted = frozenset(_Y_SOURCE["assignment"]["trusted_single_marker_terminal_rsids"])
        y_reportability_issues = _validate_y_reportability(y_tree, trusted)
        assert mt_issues == [], f"mtDNA validation issues: {mt_issues}"
        assert mt_source_issues == [], f"mtDNA source validation issues: {mt_source_issues}"
        assert mt_reportability_issues == [], (
            f"mtDNA reportability validation issues: {mt_reportability_issues}"
        )
        assert y_issues == [], f"Y-chr validation issues: {y_issues}"
        assert y_reference_issues == [], f"Y reference validation issues: {y_reference_issues}"
        assert y_duplicate_issues == [], f"Y duplicate validation issues: {y_duplicate_issues}"
        assert y_source_issues == [], f"Y source validation issues: {y_source_issues}"
        assert y_reportability_issues == [], (
            f"Y reportability validation issues: {y_reportability_issues}"
        )

    def test_marker_exact_mt_guard_rejects_wrong_exact_marker_sets(self) -> None:
        """Marker-exact nodes reject polarity, position, and extra-marker drift."""
        from scripts.build_haplogroup_bundle import (
            _MT_SOURCE,
            _validate_mt_source,
            build_mt_tree,
        )

        mt_tree = build_mt_tree()
        u5b2 = find_node(mt_tree, "U5b2")
        w1 = find_node(mt_tree, "W1")
        u3b = find_node(mt_tree, "U3b")
        k1b = find_node(mt_tree, "K1b")
        assert u5b2 is not None and w1 is not None and u3b is not None and k1b is not None

        u5b2["defining_snps"][0]["allele"] = "C"
        w1["defining_snps"][0]["pos"] = 12669
        u3b["defining_snps"].append({"rsid": "i5009266", "pos": 9266, "allele": "G"})
        k1b["defining_snps"][0]["pos"] = 14167

        issues = _validate_mt_source(_MT_SOURCE, mt_tree)
        assert any("U5b2" in issue and "expected" in issue for issue in issues)
        assert any("W1" in issue and "expected" in issue for issue in issues)
        assert any("U3b" in issue and "expected" in issue for issue in issues)
        assert any("K1b" in issue and "expected" in issue for issue in issues)

    @pytest.mark.parametrize(
        ("node_name", "position", "old_allele"),
        [
            pytest.param("L2c", 13958, "T", id="L2c-G13958c"),
            pytest.param("M1", 6446, "G", id="M1-G6446A"),
            pytest.param("S", 8404, "T", id="S-T8404C"),
            pytest.param("X", 6371, "C", id="X-C6371T"),
            pytest.param("H10", 14470, "C", id="H10-T14470a"),
        ],
    )
    def test_issue_1798_marker_exact_mt_guard_rejects_old_wrong_alleles(
        self, node_name: str, position: int, old_allele: str
    ) -> None:
        """Restoring any pre-fix allele breaks the exact source-backed set."""
        from scripts.build_haplogroup_bundle import (
            _MT_SOURCE,
            _validate_mt_source,
            build_mt_tree,
        )

        mt_tree = build_mt_tree()
        node = find_node(mt_tree, node_name)
        assert node is not None
        marker = next(snp for snp in node["defining_snps"] if snp["pos"] == position)
        marker["allele"] = old_allele

        issues = _validate_mt_source(_MT_SOURCE, mt_tree)
        assert any(
            f"Marker-exact mtDNA node {node_name}" in issue and "expected" in issue
            for issue in issues
        )

    def test_issue_1808_audited_mt_guard_rejects_r_marker_on_n9(self) -> None:
        """The exact N9 audit rejects the pre-fix borrowed R marker."""
        from scripts.build_haplogroup_bundle import (
            _MT_SOURCE,
            _validate_mt_source,
            build_mt_tree,
        )

        mt_tree = build_mt_tree()
        n9 = find_node(mt_tree, "N9")
        assert n9 is not None
        n9["defining_snps"].append({"rsid": "i5012705", "pos": 12705, "allele": "C"})

        issues = _validate_mt_source(_MT_SOURCE, mt_tree)
        assert any(
            "Marker-exact mtDNA node N9" in issue and "expected" in issue for issue in issues
        )

    @pytest.mark.parametrize(
        ("node_name", "legacy_markers"),
        [
            pytest.param(
                "S1",
                [{"rsid": "i5010238", "pos": 10238, "allele": "C"}],
                id="S1-legacy-10238C",
            ),
            pytest.param(
                "S2",
                [{"rsid": "i5014364", "pos": 14364, "allele": "T"}],
                id="S2-legacy-14364T",
            ),
        ],
    )
    def test_issue_1814_audited_mt_guard_rejects_legacy_marker_sets(
        self, node_name: str, legacy_markers: list[dict[str, object]]
    ) -> None:
        """Restoring either unsupported child marker breaks the exact lock."""
        from scripts.build_haplogroup_bundle import (
            _MT_SOURCE,
            _validate_mt_source,
            build_mt_tree,
        )

        mt_tree = build_mt_tree()
        node = find_node(mt_tree, node_name)
        assert node is not None
        node["defining_snps"] = legacy_markers

        issues = _validate_mt_source(_MT_SOURCE, mt_tree)
        assert any(
            f"Marker-exact mtDNA node {node_name}" in issue and "expected" in issue
            for issue in issues
        )

    @pytest.mark.parametrize(
        ("node_name", "legacy_markers"),
        [
            pytest.param(
                "W",
                [
                    {"rsid": "i5000189", "pos": 189, "allele": "G"},
                    {"rsid": "i5000204", "pos": 204, "allele": "C"},
                    {"rsid": "i5000207", "pos": 207, "allele": "A"},
                    {"rsid": "i5001243", "pos": 1243, "allele": "C"},
                ],
                id="W-legacy-189G-204C",
            ),
            pytest.param(
                "W3",
                [{"rsid": "i5005460", "pos": 5460, "allele": "A"}],
                id="W3-legacy-5460A",
            ),
        ],
    )
    def test_issue_1834_audited_mt_guard_rejects_legacy_marker_sets(
        self, node_name: str, legacy_markers: list[dict[str, object]]
    ) -> None:
        """Restoring either pre-correction W marker set breaks the exact lock."""
        from scripts.build_haplogroup_bundle import (
            _MT_SOURCE,
            _validate_mt_source,
            build_mt_tree,
        )

        mt_tree = build_mt_tree()
        node = find_node(mt_tree, node_name)
        assert node is not None
        node["defining_snps"] = legacy_markers

        issues = _validate_mt_source(_MT_SOURCE, mt_tree)
        assert any(
            f"Marker-exact mtDNA node {node_name}" in issue and "expected" in issue
            for issue in issues
        )

    def test_issue_1849_audited_mt_guard_rejects_legacy_h1a_marker_set(self) -> None:
        """Restoring the unsupported H1a pair breaks the exact marker lock."""
        from scripts.build_haplogroup_bundle import (
            _MT_SOURCE,
            _validate_mt_source,
            build_mt_tree,
        )

        mt_tree = build_mt_tree()
        h1a = find_node(mt_tree, "H1a")
        assert h1a is not None
        h1a["defining_snps"] = [
            {"rsid": "rs1000390", "pos": 13290, "allele": "T"},
            {"rsid": "i5013404", "pos": 13404, "allele": "C"},
        ]

        issues = _validate_mt_source(_MT_SOURCE, mt_tree)
        assert any(
            "Marker-exact mtDNA node H1a" in issue and "expected" in issue for issue in issues
        )

    def test_mt_reportability_guard_requires_new_identifier_and_locus(self) -> None:
        """A fresh rsID at an old locus or an old rsID at a fresh locus is insufficient."""
        from scripts.build_haplogroup_bundle import _validate_mt_reportability, build_mt_tree

        mt_tree = build_mt_tree()
        t2 = find_node(mt_tree, "T2")
        t2a = find_node(mt_tree, "T2a")
        assert t2 is not None and t2a is not None
        inherited = t2["defining_snps"][0]
        t2a["defining_snps"] = [
            {"rsid": inherited["rsid"], "pos": 15000, "allele": "A"},
            {"rsid": "fresh-id", "pos": inherited["pos"], "allele": "A"},
        ]

        issues = _validate_mt_reportability(mt_tree)
        assert issues.count("mtDNA node T2a has no ancestor-distinguishing marker") == 1

        t2a["defining_snps"] = [{"allele": "A"}]
        issues = _validate_mt_reportability(mt_tree)
        assert issues.count("mtDNA node T2a has no ancestor-distinguishing marker") == 1

    def test_mt_source_guard_rejects_unreportable_or_reversed_markers(self) -> None:
        """Provenance records must retain direction and observed array coverage."""
        from scripts.build_haplogroup_bundle import (
            _MT_SOURCE,
            _validate_mt_source,
            build_mt_tree,
        )

        source = copy.deepcopy(_MT_SOURCE)
        source["audit_scope"] = ""
        source.pop("omitted_nodes")
        marker = source["nodes"]["U5b2"]["emitted_snps"][0]
        marker["allele"] = marker["ancestral_allele"]
        marker["array_coverage"]["position_present_in"] = []
        marker["array_coverage"]["callable_snv_in"] = []
        source["nodes"]["U5b2"]["direct_source_motif"][1]["emitted"] = False

        issues = _validate_mt_source(source, build_mt_tree())
        assert any("no valid audit scope" in issue for issue in issues)
        assert any("no valid omitted-nodes mapping" in issue for issue in issues)
        assert any("source mutation direction" in issue for issue in issues)
        assert any("absent from its whole cohort" in issue for issue in issues)
        assert any(
            "source mutation U5b2:13637 must have an omission reason" in issue for issue in issues
        )

    def test_issue_1798_mt_source_guard_rejects_silent_omission_and_direction_drift(
        self,
    ) -> None:
        """The new direct rows fail closed on omitted evidence and marker direction."""
        from scripts.build_haplogroup_bundle import (
            _MT_SOURCE,
            _validate_mt_source,
            build_mt_tree,
        )

        source = copy.deepcopy(_MT_SOURCE)
        omitted = next(
            mutation
            for mutation in source["nodes"]["M1"]["direct_source_motif"]
            if mutation["pos"] == 195
        )
        omitted.pop("omission_reason")
        direction = next(
            mutation
            for mutation in source["nodes"]["L2c"]["direct_source_motif"]
            if mutation["pos"] == 13958
        )
        direction["derived_allele"] = "T"

        issues = _validate_mt_source(source, build_mt_tree())
        assert any(
            "source mutation M1:195 must have an omission reason" in issue for issue in issues
        )
        assert any(
            "i5013958 at L2c does not match its source mutation direction" in issue
            for issue in issues
        )

    def test_y_source_guard_rejects_stale_current_record_alleles(self) -> None:
        """Selected alleles must remain supported by the current RefSNP record."""
        from scripts.build_haplogroup_bundle import _Y_SOURCE, _validate_y_source

        source = copy.deepcopy(_Y_SOURCE)
        marker = next(
            marker
            for node in source["nodes"].values()
            for marker in node["markers"]
            if marker["identifier_source"] == "ncbi_refsnp"
        )
        marker["ncbi_grch37_y_alleles"] = [marker["ancestral_allele"]]

        issues = _validate_y_source(source)
        assert any(
            marker["rsid"] in issue and "alleles absent from NCBI" in issue for issue in issues
        )

    def test_y_source_guard_rejects_wrong_exact_clade_alias(self) -> None:
        """Exact markers must retain provenance for their emitted clade."""
        from scripts.build_haplogroup_bundle import _Y_SOURCE, _validate_y_source

        source = copy.deepcopy(_Y_SOURCE)
        marker = source["nodes"]["O2b"]["markers"][0]
        marker["source_clade_aliases"] = ["O1b2"]

        issues = _validate_y_source(source)
        assert any(
            marker["rsid"] in issue and "exact-match aliases do not include O2b" in issue
            for issue in issues
        )

    def test_y_source_guard_rejects_ineligible_missing_marker_passthrough(self) -> None:
        """Only audited partial-array internal gates may bypass missing evidence."""
        from scripts.build_haplogroup_bundle import _Y_SOURCE, _validate_y_source

        source = copy.deepcopy(_Y_SOURCE)
        source["assignment"]["trusted_missing_internal_passthrough_rsids"].append("rs2032595")

        issues = _validate_y_source(source)
        assert any("rs2032595" in issue and "ineligible" in issue for issue in issues)

    def test_audited_y_rsid_guard_rejects_stale_reference_records(self) -> None:
        """The builder rejects stale coordinates, alleles, and clade placement
        before they can be regenerated into the bundle."""
        from scripts.build_haplogroup_bundle import _validate_audited_y_rsids, build_y_tree

        y_tree = build_y_tree()
        ct_node = find_node(y_tree, "CT")
        k_node = find_node(y_tree, "K")
        b_node = find_node(y_tree, "B")
        assert ct_node is not None and k_node is not None and b_node is not None

        next(snp for snp in ct_node["defining_snps"] if snp["rsid"] == "rs2032595")["pos"] = (
            14813990
        )
        next(snp for snp in k_node["defining_snps"] if snp["rsid"] == "rs3900")["allele"] = "C"
        b_node["defining_snps"].append({"rsid": "rs2032631", "pos": 21867787, "allele": "A"})

        issues = _validate_audited_y_rsids(y_tree)
        assert any(
            "rs2032595" in issue and "expected GRCh37 Y:14813991" in issue for issue in issues
        )
        assert any(
            "rs3900" in issue and "expected derived allele 'G'" in issue for issue in issues
        )
        assert any("rs2032631" in issue and "expected P" in issue for issue in issues)

    def test_audited_y_rsid_guard_rejects_excluded_records(self) -> None:
        """#1654: non-Y and unresolved duplicate suspects cannot re-enter the Y tree."""
        from scripts.build_haplogroup_bundle import _validate_audited_y_rsids, build_y_tree

        y_tree = build_y_tree()
        r_node = find_node(y_tree, "R")
        assert r_node is not None
        r_node["defining_snps"].append({"rsid": "rs1000546", "pos": 36452173, "allele": "T"})

        issues = _validate_audited_y_rsids(y_tree)
        assert any(
            "rs1000546" in issue and "excluded from the Y tree" in issue for issue in issues
        )

    def test_y_marker_guard_rejects_unregistered_records(self) -> None:
        """No Y marker can bypass the complete source registry whitelist."""
        from scripts.build_haplogroup_bundle import _validate_audited_y_rsids, build_y_tree

        y_tree = build_y_tree()
        r_node = find_node(y_tree, "R")
        assert r_node is not None
        r_node["defining_snps"].append({"rsid": "rs999999999", "pos": 1, "allele": "A"})

        issues = _validate_audited_y_rsids(y_tree)
        assert any("rs999999999" in issue and "absent from" in issue for issue in issues)

    def test_y_duplicate_guard_rejects_all_cross_clade_reuse(self) -> None:
        """The guard rejects both new and formerly grandfathered duplicate rsIDs."""
        from scripts.build_haplogroup_bundle import (
            _validate_y_cross_clade_duplicates,
            build_y_tree,
        )

        y_tree = build_y_tree()
        b_node = find_node(y_tree, "B")
        r_node = find_node(y_tree, "R")
        assert b_node is not None and r_node is not None
        b_node["defining_snps"].append({"rsid": "rs2032658", "pos": 15581983, "allele": "G"})
        b_node["defining_snps"].append({"rsid": "rs17250359", "pos": 1, "allele": "T"})
        r_node["defining_snps"].append({"rsid": "rs17250359", "pos": 1, "allele": "T"})

        issues = _validate_y_cross_clade_duplicates(y_tree)
        assert any("rs2032658" in issue and "unrelated Y clades" in issue for issue in issues)
        assert any("rs17250359" in issue and "unrelated Y clades" in issue for issue in issues)

    def test_count_helpers_consistent(self) -> None:
        """_count_nodes and _count_snps should match collected counts."""
        from scripts.build_haplogroup_bundle import (
            _count_nodes,
            _count_snps,
            build_mt_tree,
        )

        mt = build_mt_tree()
        assert _count_nodes(mt) == len(collect_all_nodes(mt))
        assert _count_snps(mt) == len(collect_all_snps(mt))
