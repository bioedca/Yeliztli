"""Tests for the PhyloTree + ISOGG Y-tree haplogroup bundle (P3-31).

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
        required = {"module", "version", "description", "build", "sources", "trees", "stats"}
        assert required.issubset(bundle.keys())

    def test_module_name(self, bundle: dict) -> None:
        assert bundle["module"] == "haplogroup"

    def test_version_format(self, bundle: dict) -> None:
        parts = bundle["version"].split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_build_is_grch37(self, bundle: dict) -> None:
        assert bundle["build"] == "GRCh37"

    def test_sources_mt(self, bundle: dict) -> None:
        mt_source = bundle["sources"]["mt"]
        assert mt_source["name"] == "PhyloTree"
        assert "Build 17" in mt_source["version"]
        assert "url" in mt_source

    def test_sources_y(self, bundle: dict) -> None:
        y_source = bundle["sources"]["Y"]
        assert y_source["name"] == "ISOGG Y-DNA Haplogroup Tree"
        assert "url" in y_source

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
        assert bundle["stats"]["mt_haplogroups"] == len(nodes)
        assert bundle["stats"]["mt_defining_snps"] == len(snps)
        assert bundle["stats"]["mt_unique_snps"] == len(unique_rsids)

    def test_non_root_nodes_have_defining_snps(self, mt_tree: dict) -> None:
        """Every non-root node should have at least one defining SNP."""
        nodes = collect_all_nodes(mt_tree)
        for node in nodes[1:]:  # Skip root
            assert len(node["defining_snps"]) > 0, f"{node['haplogroup']} has no defining SNPs"


# ── Y-chromosome tree-specific tests ────────────────────────────────────


class TestYChromTree:
    """Validate Y-chromosome (ISOGG) tree content."""

    def test_root_is_y_adam(self, y_tree: dict) -> None:
        assert y_tree["haplogroup"] == "Y-Adam"

    def test_root_has_no_defining_snps(self, y_tree: dict) -> None:
        assert len(y_tree["defining_snps"]) == 0

    def test_major_haplogroups_present(self, y_tree: dict) -> None:
        """Major Y-chr haplogroups should exist."""
        names = set(collect_haplogroup_names(y_tree))
        expected = {"A", "B", "C", "D", "E", "G", "I", "J", "K", "N_Y", "O", "R"}
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
        assert bundle["stats"]["y_haplogroups"] == len(nodes)
        assert bundle["stats"]["y_defining_snps"] == len(snps)
        assert bundle["stats"]["y_unique_snps"] == len(unique_rsids)

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
        "rs1000546": ("Y", 36452173, "T"),
        "rs1000154": ("Y", 39970128, "G"),
        "rs1000147": ("Y", 41031901, "A"),
        "rs1000306": ("Y", 53186638, "C"),
    }

    def test_fixture_mt_snps_in_bundle(self, mt_tree: dict) -> None:
        """Test fixture's MT SNP rsids should appear in the bundle tree."""
        bundle_rsids = {s["rsid"] for s in collect_all_snps(mt_tree)}
        fixture_rsids = set(self.FIXTURE_MT_SNPS.keys())
        overlap = fixture_rsids & bundle_rsids
        # At least some fixture SNPs should be in the bundle (for testability)
        assert len(overlap) >= 3, f"Only {len(overlap)} fixture MT SNPs found in bundle: {overlap}"

    def test_fixture_y_snps_in_bundle(self, y_tree: dict) -> None:
        """Test fixture's Y SNP rsids should appear in the bundle tree."""
        bundle_rsids = {s["rsid"] for s in collect_all_snps(y_tree)}
        fixture_rsids = set(self.FIXTURE_Y_SNPS.keys())
        overlap = fixture_rsids & bundle_rsids
        assert len(overlap) >= 3, f"Only {len(overlap)} fixture Y SNPs found in bundle: {overlap}"

    def test_fixture_sample_resolves_to_h_lineage(self, mt_tree: dict) -> None:
        """Fixture MT SNPs should enable resolution to H branch.

        The fixture sample has rs1000687 (pos 13252, T) which is a defining
        SNP for haplogroup H in our tree.
        """
        h_node = find_node(mt_tree, "H")
        assert h_node is not None
        h_rsids = {s["rsid"] for s in h_node["defining_snps"]}
        assert "rs1000687" in h_rsids

    def test_fixture_sample_resolves_to_r1b_lineage(self, y_tree: dict) -> None:
        """Fixture Y SNPs should enable resolution to R1b branch.

        The fixture sample has rs1000331 (pos 20085901, T) which is a
        defining SNP for R1b in our tree.
        """
        r1b_node = find_node(y_tree, "R1b")
        assert r1b_node is not None
        r1b_rsids = {s["rsid"] for s in r1b_node["defining_snps"]}
        assert "rs1000331" in r1b_rsids


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

    def test_validate_tree_passes(self) -> None:
        """Internal validation should report no issues."""
        from scripts.build_haplogroup_bundle import (
            _validate_tree,
            build_mt_tree,
            build_y_tree,
        )

        mt_issues = _validate_tree(build_mt_tree())
        y_issues = _validate_tree(build_y_tree())
        assert mt_issues == [], f"mtDNA validation issues: {mt_issues}"
        assert y_issues == [], f"Y-chr validation issues: {y_issues}"

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
