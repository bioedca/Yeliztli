"""Tests for haplogroup assignment engine (P3-32).

Covers:
  - T3-31: mtDNA tree-walk correctly assigns H1a for known genotype fixture
  - T3-32: Y-chromosome assignment skipped when sex_inferred = 'XX'
  - T3-33: Confidence score correctly reflects defining_snps_present / defining_snps_total
  - T3-34: haplogroup_assignments table populated correctly after ancestry module runs
  - Bundle loading and parsing
  - Tree-walk algorithm correctness
  - Findings storage in both haplogroup_assignments and findings tables

Sex inference itself is tested in ``tests/backend/test_sex_inference.py``
since the helper moved to ``backend/services/sex_inference.py`` at Step 54
(see Plan §9.4). Haplogroup fixtures here include the chrX evidence the
PAR-aware algorithm needs to confirm XY.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.ancestry import (
    HaplogroupBundle,
    HaplogroupNode,
    HaplogroupResult,
    HaplogroupSNP,
    HaplogroupTraversalStep,
    _classify_node_match,
    _collect_rsids,
    _haplogroup_confidence,
    _parse_tree_node,
    _tree_walk,
    assign_haplogroups,
    load_haplogroup_bundle,
    run_haplogroup_assignment,
    store_haplogroup_findings,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    findings,
    haplogroup_assignments,
    raw_variants,
)

# ── Paths ────────────────────────────────────────────────────────────────

BUNDLE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "haplogroup_bundle.json"
)

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture()
def bundle() -> HaplogroupBundle:
    """Load the real haplogroup bundle."""
    return load_haplogroup_bundle(BUNDLE_PATH)


@pytest.fixture()
def sample_engine() -> sa.Engine:
    """In-memory SQLite engine with all sample tables."""
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    return engine


# Known genotype fixture for H1a path:
# mt-MRCA → L3 → N → R → R0 → HV → H → H1 → H1a
_H1A_GENOTYPES = [
    # L3 defining SNPs (PhyloTree Build 17 forward-direction labels:
    # A769G, A1018G, C16311T).
    {"rsid": "i5000769", "chrom": "MT", "pos": 769, "genotype": "GG"},
    {"rsid": "i5001018", "chrom": "MT", "pos": 1018, "genotype": "GG"},
    {"rsid": "i5016311", "chrom": "MT", "pos": 16311, "genotype": "TT"},
    # N defining SNPs (source-direction non-colliding subset: G8701A, C9540T,
    # C10873T). Source positions 10398 and 15301 are modeled on downstream
    # clades with opposite alleles, so they stay off the N ancestor in this
    # bundle.
    {"rsid": "i5008701", "chrom": "MT", "pos": 8701, "genotype": "AA"},
    {"rsid": "i5009540", "chrom": "MT", "pos": 9540, "genotype": "TT"},
    {"rsid": "i5010873", "chrom": "MT", "pos": 10873, "genotype": "TT"},
    # R defining SNPs (T12705C, T16223C).
    {"rsid": "i5012705", "chrom": "MT", "pos": 12705, "genotype": "CC"},
    {"rsid": "i5016223", "chrom": "MT", "pos": 16223, "genotype": "CC"},
    # HV0 sibling marker: Build 17 HV0 is T72C. A true H carrier carries the
    # rCRS base 72=T, so TT keeps this H path out of the HV0 branch (#1648).
    {"rsid": "i5000072", "chrom": "MT", "pos": 72, "genotype": "TT"},
    # HV defining SNP T14766C — a true HV/H carrier is C (the derived allele;
    # rCRS is H2a2a1). The prior "TT" was the ancestral, biologically-impossible
    # for an HV/H person, and masked the #1579 inversion.
    {"rsid": "i5014766", "chrom": "MT", "pos": 14766, "genotype": "CC"},
    # H defining SNP G2706A — a true H carrier is A (derived; rCRS base). The prior
    # "GG" was ancestral. (rs1000687 @ m.13252 removed — it is autosomal chr11,
    # not an H-defining mtDNA marker; #1579.)
    {"rsid": "i5002706", "chrom": "MT", "pos": 2706, "genotype": "AA"},
    # H1 defining SNPs
    {"rsid": "i5003010", "chrom": "MT", "pos": 3010, "genotype": "AA"},
    # H1a defining SNPs
    {"rsid": "rs1000390", "chrom": "MT", "pos": 13290, "genotype": "TT"},
    {"rsid": "i5013404", "chrom": "MT", "pos": 13404, "genotype": "CC"},
]

_MT_R_TRUNK_GENOTYPES = _H1A_GENOTYPES[:8]

_MT_U_TRUNK_GENOTYPES = _MT_R_TRUNK_GENOTYPES + [
    {"rsid": "rs1000731", "chrom": "MT", "pos": 13133, "genotype": "TT"},
    {"rsid": "i5012308", "chrom": "MT", "pos": 12308, "genotype": "GG"},
    {"rsid": "i5012372", "chrom": "MT", "pos": 12372, "genotype": "AA"},
]

_MT_U8_TRUNK_GENOTYPES = _MT_U_TRUNK_GENOTYPES + [
    {"rsid": "i5009698", "chrom": "MT", "pos": 9698, "genotype": "CC"},
]

_MT_K_GENOTYPES = _MT_U8_TRUNK_GENOTYPES + [
    {"rsid": "i5001189", "chrom": "MT", "pos": 1189, "genotype": "CC"},
    {"rsid": "i5010550", "chrom": "MT", "pos": 10550, "genotype": "GG"},
    {"rsid": "i5011299", "chrom": "MT", "pos": 11299, "genotype": "CC"},
    {"rsid": "i5014798", "chrom": "MT", "pos": 14798, "genotype": "CC"},
    {"rsid": "i5016224", "chrom": "MT", "pos": 16224, "genotype": "CC"},
]

_RCRS_H2A2A1_GENOTYPES = _MT_R_TRUNK_GENOTYPES + [
    {"rsid": "i5000072", "chrom": "MT", "pos": 72, "genotype": "TT"},
    {"rsid": "i5014766", "chrom": "MT", "pos": 14766, "genotype": "CC"},
    {"rsid": "i5002706", "chrom": "MT", "pos": 2706, "genotype": "AA"},
    {"rsid": "i5001438", "chrom": "MT", "pos": 1438, "genotype": "AA"},
    {"rsid": "i5004769", "chrom": "MT", "pos": 4769, "genotype": "AA"},
    {"rsid": "i5009380", "chrom": "MT", "pos": 9380, "genotype": "GG"},
    {"rsid": "i5000750", "chrom": "MT", "pos": 750, "genotype": "AA"},
    {"rsid": "i5008860", "chrom": "MT", "pos": 8860, "genotype": "AA"},
    {"rsid": "i5015326", "chrom": "MT", "pos": 15326, "genotype": "AA"},
    {"rsid": "i5000263", "chrom": "MT", "pos": 263, "genotype": "AA"},
    {"rsid": "i5000951", "chrom": "MT", "pos": 951, "genotype": "GG"},
    {"rsid": "i5015354", "chrom": "MT", "pos": 15354, "genotype": "CC"},
    {"rsid": "i5016354", "chrom": "MT", "pos": 16354, "genotype": "CC"},
]

_H2A1_SIBLING_GENOTYPES = _MT_R_TRUNK_GENOTYPES + [
    {"rsid": "i5014766", "chrom": "MT", "pos": 14766, "genotype": "CC"},
    {"rsid": "i5002706", "chrom": "MT", "pos": 2706, "genotype": "AA"},
    {"rsid": "i5001438", "chrom": "MT", "pos": 1438, "genotype": "AA"},
    {"rsid": "i5004769", "chrom": "MT", "pos": 4769, "genotype": "AA"},
    {"rsid": "i5000951", "chrom": "MT", "pos": 951, "genotype": "AA"},
    {"rsid": "i5016354", "chrom": "MT", "pos": 16354, "genotype": "TT"},
    {"rsid": "i5000750", "chrom": "MT", "pos": 750, "genotype": "GG"},
    {"rsid": "i5000263", "chrom": "MT", "pos": 263, "genotype": "GG"},
]

_HV0_GENOTYPES = _MT_R_TRUNK_GENOTYPES + [
    {"rsid": "i5014766", "chrom": "MT", "pos": 14766, "genotype": "CC"},
    {"rsid": "i5000072", "chrom": "MT", "pos": 72, "genotype": "CC"},
]

_MT_N1_REVERSAL_GENOTYPES = _H1A_GENOTYPES[:6] + [
    {"rsid": "i5006365", "chrom": "MT", "pos": 6365, "genotype": "CC"},
    {"rsid": "i5010398", "chrom": "MT", "pos": 10398, "genotype": "GG"},
]

_MT_B_REVERSAL_GENOTYPES = _MT_R_TRUNK_GENOTYPES + [
    {"rsid": "i5000827", "chrom": "MT", "pos": 827, "genotype": "GG"},
    {"rsid": "i5008281", "chrom": "MT", "pos": 8281, "genotype": "CC"},
    {"rsid": "i5015301", "chrom": "MT", "pos": 15301, "genotype": "AA"},
]

_MT_J_REVERSAL_GENOTYPES = _MT_R_TRUNK_GENOTYPES + [
    {"rsid": "i5000489", "chrom": "MT", "pos": 489, "genotype": "CC"},
    {"rsid": "i5011251", "chrom": "MT", "pos": 11251, "genotype": "GG"},
    {"rsid": "i5000295", "chrom": "MT", "pos": 295, "genotype": "TT"},
    {"rsid": "i5010398", "chrom": "MT", "pos": 10398, "genotype": "GG"},
    {"rsid": "i5012612", "chrom": "MT", "pos": 12612, "genotype": "GG"},
    {"rsid": "i5016069", "chrom": "MT", "pos": 16069, "genotype": "TT"},
]

_MT_K1_REVERSAL_GENOTYPES = _MT_K_GENOTYPES + [
    {"rsid": "i5010398", "chrom": "MT", "pos": 10398, "genotype": "GG"},
]

_MT_L0_GENOTYPES = [
    {"rsid": "i5001048", "chrom": "MT", "pos": 1048, "genotype": "TT"},
    {"rsid": "i5005442", "chrom": "MT", "pos": 5442, "genotype": "CC"},
    {"rsid": "i5006185", "chrom": "MT", "pos": 6185, "genotype": "CC"},
    {"rsid": "i5009042", "chrom": "MT", "pos": 9042, "genotype": "TT"},
    {"rsid": "i5010589", "chrom": "MT", "pos": 10589, "genotype": "AA"},
]

# Non-PAR chrX hom calls needed for the Plan §9.4 sex-inference algorithm
# (Step 54) to classify a sample as candidate XY. Positions sit well past
# PAR1 (ends at 2,699,520) and before PAR2 (starts at 154,931,044). The pool
# clears the issue-363 minimum-evidence floor (≥ MIN_X_NONPAR_TYPED typed
# non-PAR chrX), and every typed call is homozygous so the §9.4 candidate-XY
# branch fires.
_NONPAR_X_HOM_GENOTYPES = [
    {"rsid": f"rs_haplo_x_hom_{i}", "chrom": "X", "pos": 50_000_001 + i, "genotype": "GG"}
    for i in range(120)
]

# chrY typed padding so an XY fixture clears the issue-363 chrY floor
# (≥ MIN_Y_PROBES probes); these are non-tree-defining positions the Y
# tree-walk ignores while sex inference counts them toward ``y_total``.
_Y_TYPED_PADDING = [
    {"rsid": f"rs_haplo_y_pad_{i}", "chrom": "Y", "pos": 3_000_000 + i, "genotype": "AA"}
    for i in range(60)
]

# chrY no-call padding so an XX fixture has an evaluable chrY denominator at
# rate 0.0 (issue #363) rather than zero chrY probes.
_Y_NOCALL_PADDING = [
    {"rsid": f"rs_haplo_ync_{i}", "chrom": "Y", "pos": 4_000_000 + i, "genotype": "--"}
    for i in range(60)
]


def _derived_y_path_genotypes(target: str) -> list[dict[str, object]]:
    """Build derived calls for one emitted Y path from the generated bundle."""
    tree = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))["trees"]["Y"]

    def find_path(node: dict) -> list[dict] | None:
        if node["haplogroup"] == target:
            return [node]
        for child in node.get("children", []):
            path = find_path(child)
            if path is not None:
                return [node, *path]
        return None

    path = find_path(tree)
    assert path is not None, f"Y test target {target} is absent from the generated bundle"
    return [
        {
            "rsid": snp["rsid"],
            "chrom": "Y",
            "pos": snp["pos"],
            "genotype": snp["allele"] * 2,
        }
        for node in path
        for snp in node["defining_snps"]
    ]


# Reportable R-M269 path after unsupported R1b1a1 is pruned and its child promoted.
_R1B1A_GENOTYPES = _derived_y_path_genotypes("R1b1a1a")

# Issue #660: a CT/M168+ male whose rs2032597 *is typed* — as the ancestral
# allele A that every non-A man carries. Pre-fix, the A node encoded its derived
# state as "A" (the dbSNP/Ensembl ancestral allele; ancestral_allele=A, alt=C), so
# this man false-matched haplogroup A and the greedy walk drove him to the
# basal-African A1b — a wrong paternal-lineage finding for the global majority of
# men. Post-fix (A/A1 derived="C") the ancestral A now *conflicts* with the A node,
# blocking that branch, so the man resolves into his real CT clade. Routes
# Y-Adam → CT → C → C2.
_CT_M168_GENOTYPES = [
    # The bug trigger: ancestral allele A at the A-clade marker. (Diploid notation
    # matches the other fixtures; the tree-walk's substring match treats "AA" and
    # haploid "A" identically.)
    {"rsid": "rs2032597", "chrom": "Y", "pos": 14847792, "genotype": "AA"},
    *_derived_y_path_genotypes("C2"),
]


def _seed_mt_h1a(engine: sa.Engine) -> None:
    """Seed H1a mtDNA genotypes into raw_variants."""
    with engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), _H1A_GENOTYPES)


def _seed_both(engine: sa.Engine) -> None:
    """Seed mt H1a, Y R1b1a, and the chrX/chrY evidence the sex-inference
    service needs to classify the sample as XY (Plan §9.4) at evaluable
    densities (issue #363)."""
    all_rows = _H1A_GENOTYPES + _R1B1A_GENOTYPES + _Y_TYPED_PADDING + _NONPAR_X_HOM_GENOTYPES
    with engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), all_rows)


# ── Bundle loading tests ────────────────────────────────────────────────


class TestLoadHaplogroupBundle:
    """Test haplogroup bundle loading from JSON."""

    def test_loads_from_json(self, bundle: HaplogroupBundle) -> None:
        assert bundle.version == "1.1.0"
        assert bundle.build == "GRCh37"

    def test_mt_tree_root(self, bundle: HaplogroupBundle) -> None:
        assert bundle.mt_tree.haplogroup == "mt-MRCA"
        assert len(bundle.mt_tree.defining_snps) == 0
        assert len(bundle.mt_tree.children) > 0

    def test_y_tree_root(self, bundle: HaplogroupBundle) -> None:
        assert bundle.y_tree.haplogroup == "Y-Adam"
        assert len(bundle.y_tree.defining_snps) == 0
        assert len(bundle.y_tree.children) > 0

    def test_mt_snp_rsids_populated(self, bundle: HaplogroupBundle) -> None:
        assert len(bundle.mt_snp_rsids) > 100

    def test_y_snp_rsids_populated(self, bundle: HaplogroupBundle) -> None:
        assert len(bundle.y_snp_rsids) > 50

    def test_y_trusted_single_markers_loaded_from_bundle(self, bundle: HaplogroupBundle) -> None:
        assert bundle.y_min_internal_terminal_specific_snps == 2
        assert bundle.y_trusted_missing_internal_passthrough_rsids == frozenset({"rs2032599"})
        assert bundle.y_trusted_single_marker_terminal_rsids
        assert bundle.y_trusted_single_marker_terminal_rsids <= bundle.y_snp_rsids

    def test_direct_construction_retains_legacy_y_policy(self, bundle: HaplogroupBundle) -> None:
        legacy = HaplogroupBundle(
            version=bundle.version,
            build=bundle.build,
            mt_tree=bundle.mt_tree,
            y_tree=bundle.y_tree,
            mt_snp_rsids=bundle.mt_snp_rsids,
            y_snp_rsids=bundle.y_snp_rsids,
        )
        assert legacy.y_min_internal_terminal_specific_snps == 2
        assert {"rs2032595", "rs2032652", "rs3900", "rs2032631", "rs2032658"} <= (
            legacy.y_trusted_single_marker_terminal_rsids
        )

    def test_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_haplogroup_bundle(Path("/nonexistent/bundle.json"))


# ── Tree node parsing tests ─────────────────────────────────────────────


class TestParseTreeNode:
    """Test recursive tree node parsing."""

    def test_simple_node(self) -> None:
        data = {
            "haplogroup": "H",
            "defining_snps": [{"rsid": "rs1", "pos": 100, "allele": "A"}],
            "children": [],
        }
        node = _parse_tree_node(data)
        assert node.haplogroup == "H"
        assert len(node.defining_snps) == 1
        assert node.defining_snps[0].rsid == "rs1"
        assert node.defining_snps[0].allele == "A"

    def test_nested_children(self) -> None:
        data = {
            "haplogroup": "root",
            "defining_snps": [],
            "children": [
                {
                    "haplogroup": "A",
                    "defining_snps": [{"rsid": "rs1", "pos": 1, "allele": "G"}],
                    "children": [
                        {
                            "haplogroup": "A1",
                            "defining_snps": [{"rsid": "rs2", "pos": 2, "allele": "T"}],
                            "children": [],
                        }
                    ],
                }
            ],
        }
        node = _parse_tree_node(data)
        assert len(node.children) == 1
        assert node.children[0].haplogroup == "A"
        assert len(node.children[0].children) == 1
        assert node.children[0].children[0].haplogroup == "A1"

    def test_collect_rsids(self) -> None:
        node = HaplogroupNode(
            haplogroup="root",
            defining_snps=[HaplogroupSNP("rs1", 1, "A")],
            children=[
                HaplogroupNode(
                    haplogroup="child",
                    defining_snps=[HaplogroupSNP("rs2", 2, "G"), HaplogroupSNP("rs3", 3, "T")],
                    children=[],
                )
            ],
        )
        rsids = _collect_rsids(node)
        assert rsids == {"rs1", "rs2", "rs3"}


# ── SNP matching tests ──────────────────────────────────────────────────


class TestClassifyNodeMatchPresence:
    """Test defining-SNP present/total counts from _classify_node_match."""

    def test_all_match(self) -> None:
        node = HaplogroupNode(
            haplogroup="H",
            defining_snps=[
                HaplogroupSNP("rs1", 100, "A"),
                HaplogroupSNP("rs2", 200, "G"),
            ],
            children=[],
        )
        genotypes = {"rs1": "AA", "rs2": "GG"}
        present, _conflicting, total = _classify_node_match(node, genotypes)
        assert present == 2
        assert total == 2

    def test_partial_match(self) -> None:
        node = HaplogroupNode(
            haplogroup="H",
            defining_snps=[
                HaplogroupSNP("rs1", 100, "A"),
                HaplogroupSNP("rs2", 200, "G"),
            ],
            children=[],
        )
        genotypes = {"rs1": "AA", "rs2": "TT"}  # rs2 doesn't have G
        present, _conflicting, total = _classify_node_match(node, genotypes)
        assert present == 1
        assert total == 2

    def test_missing_genotype(self) -> None:
        node = HaplogroupNode(
            haplogroup="H",
            defining_snps=[HaplogroupSNP("rs1", 100, "A")],
            children=[],
        )
        genotypes = {}  # no data
        present, _conflicting, total = _classify_node_match(node, genotypes)
        assert present == 0
        assert total == 1

    def test_no_call_genotype(self) -> None:
        node = HaplogroupNode(
            haplogroup="H",
            defining_snps=[HaplogroupSNP("rs1", 100, "A")],
            children=[],
        )
        genotypes = {"rs1": "--"}
        present, _conflicting, total = _classify_node_match(node, genotypes)
        assert present == 0
        assert total == 1

    def test_heterozygous_match(self) -> None:
        """Derived allele present in het genotype should match."""
        node = HaplogroupNode(
            haplogroup="H",
            defining_snps=[HaplogroupSNP("rs1", 100, "G")],
            children=[],
        )
        genotypes = {"rs1": "AG"}
        present, _conflicting, total = _classify_node_match(node, genotypes)
        assert present == 1
        assert total == 1

    def test_empty_defining_snps(self) -> None:
        node = HaplogroupNode(haplogroup="root", defining_snps=[], children=[])
        present, _conflicting, total = _classify_node_match(node, {})
        assert present == 0
        assert total == 0


class TestClassifyNodeMatch:
    """#165 — distinguish present / conflicting (ancestral) / missing markers."""

    def _two_marker_node(self) -> HaplogroupNode:
        return HaplogroupNode(
            haplogroup="X",
            defining_snps=[HaplogroupSNP("rs1", 100, "A"), HaplogroupSNP("rs2", 200, "G")],
            children=[],
        )

    def test_present_and_conflicting_split(self) -> None:
        # rs1 derived A present; rs2 typed but ancestral (no G) → conflicting.
        present, conflicting, total = _classify_node_match(
            self._two_marker_node(), {"rs1": "AA", "rs2": "TT"}
        )
        assert (present, conflicting, total) == (1, 1, 2)

    def test_missing_is_not_conflicting(self) -> None:
        # rs1 derived present; rs2 untyped (absent) → missing, NOT conflicting.
        present, conflicting, total = _classify_node_match(self._two_marker_node(), {"rs1": "AA"})
        assert (present, conflicting, total) == (1, 0, 2)

    def test_no_call_is_not_conflicting(self) -> None:
        # A no-call sentinel is missing, not an ancestral conflict.
        present, conflicting, total = _classify_node_match(
            self._two_marker_node(), {"rs1": "AA", "rs2": "--"}
        )
        assert (present, conflicting, total) == (1, 0, 2)

    def test_all_conflicting(self) -> None:
        present, conflicting, total = _classify_node_match(
            self._two_marker_node(), {"rs1": "TT", "rs2": "TT"}
        )
        assert (present, conflicting, total) == (0, 2, 2)


# ── Tree-walk algorithm tests ───────────────────────────────────────────


def _find_mt_node(node: HaplogroupNode, haplogroup: str) -> HaplogroupNode | None:
    """Depth-first search for an mtDNA node by haplogroup name."""
    if node.haplogroup == haplogroup:
        return node
    for child in node.children:
        found = _find_mt_node(child, haplogroup)
        if found is not None:
            return found
    return None


def _mt_snp_map(node: HaplogroupNode) -> dict[int, str]:
    """Map defining mtDNA positions to alleles for compact curation assertions."""
    return {snp.pos: snp.allele for snp in node.defining_snps}


class TestTreeWalk:
    """Test the recursive tree-walk algorithm."""

    def test_simple_two_level(self) -> None:
        """Walk a simple tree and find the deepest match."""
        root = HaplogroupNode(
            haplogroup="root",
            defining_snps=[],
            children=[
                HaplogroupNode(
                    haplogroup="A",
                    defining_snps=[HaplogroupSNP("rs1", 1, "G")],
                    children=[
                        HaplogroupNode(
                            haplogroup="A1",
                            defining_snps=[HaplogroupSNP("rs2", 2, "T")],
                            children=[],
                        ),
                    ],
                ),
                HaplogroupNode(
                    haplogroup="B",
                    defining_snps=[HaplogroupSNP("rs3", 3, "C")],
                    children=[],
                ),
            ],
        )

        genotypes = {"rs1": "GG", "rs2": "TT", "rs3": "AA"}
        terminal, path = _tree_walk(root, genotypes, [])

        assert terminal.haplogroup == "A1"
        assert len(path) == 2
        assert path[0].haplogroup == "A"
        assert path[1].haplogroup == "A1"

    def test_stops_at_non_matching_child(self) -> None:
        root = HaplogroupNode(
            haplogroup="root",
            defining_snps=[],
            children=[
                HaplogroupNode(
                    haplogroup="A",
                    defining_snps=[HaplogroupSNP("rs1", 1, "G")],
                    children=[
                        HaplogroupNode(
                            haplogroup="A1",
                            defining_snps=[HaplogroupSNP("rs2", 2, "T")],
                            children=[],
                        ),
                    ],
                ),
            ],
        )

        # Only rs1 matches, rs2 doesn't
        genotypes = {"rs1": "GG", "rs2": "AA"}
        terminal, path = _tree_walk(root, genotypes, [])

        assert terminal.haplogroup == "A"
        assert len(path) == 1

    def test_no_match_returns_root(self) -> None:
        root = HaplogroupNode(
            haplogroup="root",
            defining_snps=[],
            children=[
                HaplogroupNode(
                    haplogroup="A",
                    defining_snps=[HaplogroupSNP("rs1", 1, "G")],
                    children=[],
                ),
            ],
        )

        genotypes = {"rs1": "AA"}  # doesn't match
        terminal, path = _tree_walk(root, genotypes, [])

        assert terminal.haplogroup == "root"
        assert len(path) == 0

    def test_picks_best_child(self) -> None:
        """When multiple children match, pick the one with higher fraction."""
        root = HaplogroupNode(
            haplogroup="root",
            defining_snps=[],
            children=[
                HaplogroupNode(
                    haplogroup="A",
                    defining_snps=[
                        HaplogroupSNP("rs1", 1, "G"),
                        HaplogroupSNP("rs2", 2, "T"),
                    ],
                    children=[],
                ),
                HaplogroupNode(
                    haplogroup="B",
                    defining_snps=[
                        HaplogroupSNP("rs3", 3, "C"),
                        HaplogroupSNP("rs4", 4, "A"),
                    ],
                    children=[],
                ),
            ],
        )

        # A matches 2/2 = 100%, B matches 1/2 = 50%
        genotypes = {"rs1": "GG", "rs2": "TT", "rs3": "CC", "rs4": "GG"}
        terminal, path = _tree_walk(root, genotypes, [])

        assert terminal.haplogroup == "A"

    def _parent_with_two_marker_child(self) -> HaplogroupNode:
        """Root → A (rs1) → A1 (rs2, rs3): A1 is a two-defining-SNP terminal."""
        return HaplogroupNode(
            haplogroup="root",
            defining_snps=[],
            children=[
                HaplogroupNode(
                    haplogroup="A",
                    defining_snps=[HaplogroupSNP("rs1", 1, "G")],
                    children=[
                        HaplogroupNode(
                            haplogroup="A1",
                            defining_snps=[
                                HaplogroupSNP("rs2", 2, "T"),
                                HaplogroupSNP("rs3", 3, "C"),
                            ],
                            children=[],
                        ),
                    ],
                ),
            ],
        )

    def test_conflicting_terminal_marker_blocks_descent(self) -> None:
        """#165 — one of A1's two defining SNPs is ancestral (typed, not derived):
        the old 50%-of-total rule descended (1/2); now the conflict stops at A."""
        root = self._parent_with_two_marker_child()
        # rs1 derived (G) → A matches; rs2 derived (T) present, rs3 typed ANCESTRAL.
        genotypes = {"rs1": "GG", "rs2": "TT", "rs3": "AA"}
        terminal, path = _tree_walk(root, genotypes, [])
        assert terminal.haplogroup == "A"  # not over-resolved to A1
        assert [s.haplogroup for s in path] == ["A"]

    def test_missing_terminal_marker_still_descends(self) -> None:
        """A missing (untyped) marker is lack of evidence, not a conflict —
        descent into A1 is still allowed when its other marker is derived (1/2)."""
        root = self._parent_with_two_marker_child()
        # rs3 absent from the map → missing, not conflicting.
        genotypes = {"rs1": "GG", "rs2": "TT"}
        terminal, path = _tree_walk(root, genotypes, [])
        assert terminal.haplogroup == "A1"
        assert [s.haplogroup for s in path] == ["A", "A1"]

    def test_conflicting_child_loses_to_clean_sibling(self) -> None:
        """A sibling clade whose markers all agree is chosen over one with an
        ancestral conflict, even when the conflicting child has more raw matches."""
        root = HaplogroupNode(
            haplogroup="root",
            defining_snps=[],
            children=[
                HaplogroupNode(
                    haplogroup="P",  # 2 present + 1 conflicting (would pass old 50%)
                    defining_snps=[
                        HaplogroupSNP("rs1", 1, "G"),
                        HaplogroupSNP("rs2", 2, "T"),
                        HaplogroupSNP("rs3", 3, "C"),
                    ],
                    children=[],
                ),
                HaplogroupNode(
                    haplogroup="Q",  # 1 present + 1 missing, no conflict
                    defining_snps=[
                        HaplogroupSNP("rs4", 4, "A"),
                        HaplogroupSNP("rs5", 5, "G"),
                    ],
                    children=[],
                ),
            ],
        )
        # P: rs1/rs2 derived, rs3 ANCESTRAL (conflict). Q: rs4 derived, rs5 missing.
        genotypes = {"rs1": "GG", "rs2": "TT", "rs3": "AA", "rs4": "AA"}
        terminal, _path = _tree_walk(root, genotypes, [])
        assert terminal.haplogroup == "Q"  # clean sibling wins; conflicting P refused

    def test_h1a_on_real_bundle(self, bundle: HaplogroupBundle) -> None:
        """T3-31: mtDNA tree-walk correctly assigns H1a for known genotype fixture."""
        genotypes = {row["rsid"]: row["genotype"] for row in _H1A_GENOTYPES}
        terminal, path = _tree_walk(bundle.mt_tree, genotypes, [])

        assert terminal.haplogroup == "H1a"
        haplogroups_in_path = [s.haplogroup for s in path]
        assert "L3" in haplogroups_in_path
        assert "N" in haplogroups_in_path
        assert "H" in haplogroups_in_path
        assert "H1" in haplogroups_in_path
        assert "H1a" in haplogroups_in_path

    def test_true_h_carrier_reaches_h_and_ancestral_is_blocked(
        self, bundle: HaplogroupBundle
    ) -> None:
        """#1579: a true H carrier carries the DERIVED alleles HV m.14766=C and
        H m.2706=A (rCRS is haplogroup H2a2a1, so carries them — Ensembl GRCh37
        MT:14766=C, MT:2706=A), and must reach H. The ancestral 14766T/2706G that
        a non-HV/H person carries must NOT reach H (it is evidence against the
        clade). Before #1579 the bundle stored the ancestral alleles, scoring
        every real H carrier (~40-45% of Europeans) as conflicting → blocked."""
        trunk = {row["rsid"]: row["genotype"] for row in _MT_R_TRUNK_GENOTYPES}
        derived = {**trunk, "i5014766": "CC", "i5002706": "AA"}  # true HV/H carrier
        ancestral = {**trunk, "i5014766": "TT", "i5002706": "GG"}  # rCRS-ancestral, non-HV/H

        derived_terminal, derived_path = _tree_walk(bundle.mt_tree, derived, [])
        assert derived_terminal.haplogroup == "H"
        assert "HV" in [s.haplogroup for s in derived_path]

        anc_terminal, anc_path = _tree_walk(bundle.mt_tree, ancestral, [])
        assert anc_terminal.haplogroup == "R"  # blocked below R
        assert "H" not in [s.haplogroup for s in anc_path]

    def test_h2a2a1_rcrs_spine_and_hv0_markers_match_phylotree_build17(
        self, bundle: HaplogroupBundle
    ) -> None:
        """#1648: below H, rCRS-spine markers must use the rCRS base, while
        off-spine H2a1 and HV0 must use their Build 17 defining coordinates."""
        h2 = _find_mt_node(bundle.mt_tree, "H2")
        h2a = _find_mt_node(bundle.mt_tree, "H2a")
        h2a1 = _find_mt_node(bundle.mt_tree, "H2a1")
        h2a2 = _find_mt_node(bundle.mt_tree, "H2a2")
        h2a2a = _find_mt_node(bundle.mt_tree, "H2a2a")
        h2a2a1 = _find_mt_node(bundle.mt_tree, "H2a2a1")
        hv0 = _find_mt_node(bundle.mt_tree, "HV0")

        assert h2 is not None
        assert h2a is not None
        assert h2a1 is not None
        assert h2a2 is not None
        assert h2a2a is not None
        assert h2a2a1 is not None
        assert hv0 is not None

        assert _mt_snp_map(h2) == {1438: "A"}  # Build 17: G1438A
        assert _mt_snp_map(h2a) == {4769: "A"}  # Build 17: G4769A
        assert _mt_snp_map(h2a1) == {951: "A", 16354: "T"}  # G951A, C16354T
        assert _mt_snp_map(h2a2) == {750: "A"}  # G750A
        assert _mt_snp_map(h2a2a) == {8860: "A", 15326: "A"}  # G8860A, G15326A
        assert _mt_snp_map(h2a2a1) == {263: "A"}  # G263A
        assert _mt_snp_map(hv0) == {72: "C"}  # Build 17: T72C

        assert 9380 not in _mt_snp_map(h2a)
        assert 15354 not in _mt_snp_map(h2a1)
        assert 73 not in _mt_snp_map(hv0)

    def test_rcrs_profile_reaches_h2a2a1_not_h2a1(self, bundle: HaplogroupBundle) -> None:
        """#1648: rCRS is H2a2a1. A synthetic rCRS callset must walk beyond H and
        must not satisfy the H2a1 sibling via the old 15354C trap."""
        genotypes = {row["rsid"]: row["genotype"] for row in _RCRS_H2A2A1_GENOTYPES}

        terminal, path = _tree_walk(bundle.mt_tree, genotypes, [])

        assert terminal.haplogroup == "H2a2a1"
        assert [step.haplogroup for step in path] == [
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

    def test_h2a1_sibling_profile_does_not_follow_rcrs_spine(
        self, bundle: HaplogroupBundle
    ) -> None:
        """#1648: H2a1 is an H2a sibling branch, not the rCRS H2a2a1 spine."""
        genotypes = {row["rsid"]: row["genotype"] for row in _H2A1_SIBLING_GENOTYPES}

        terminal, path = _tree_walk(bundle.mt_tree, genotypes, [])

        assert terminal.haplogroup == "H2a1"
        assert [step.haplogroup for step in path] == [
            "L3",
            "N",
            "R",
            "R0",
            "HV",
            "H",
            "H2",
            "H2a",
            "H2a1",
        ]

    def test_hv0_uses_position_72_not_recurrent_position_73(
        self, bundle: HaplogroupBundle
    ) -> None:
        """#1648: HV0 is Build 17 T72C, not a recurrent A73G marker."""
        genotypes = {row["rsid"]: row["genotype"] for row in _HV0_GENOTYPES}

        terminal, path = _tree_walk(bundle.mt_tree, genotypes, [])

        assert terminal.haplogroup == "HV0"
        assert [step.haplogroup for step in path] == ["L3", "N", "R", "R0", "HV", "HV0"]

    def test_source_polarity_trunk_resolves_to_r_on_real_bundle(
        self, bundle: HaplogroupBundle
    ) -> None:
        """#1080: source-direction L3/N/R alleles must not collapse to mt-MRCA."""
        genotypes = {row["rsid"]: row["genotype"] for row in _MT_R_TRUNK_GENOTYPES}
        terminal, path = _tree_walk(bundle.mt_tree, genotypes, [])

        assert terminal.haplogroup == "R"
        assert [step.haplogroup for step in path] == ["L3", "N", "R"]

    def test_true_k_profile_resolves_below_u8_on_real_bundle(
        self, bundle: HaplogroupBundle
    ) -> None:
        """#1337: K is a U8 descendant, so a true-K profile must not stop at U8."""
        genotypes = {row["rsid"]: row["genotype"] for row in _MT_K_GENOTYPES}
        terminal, path = _tree_walk(bundle.mt_tree, genotypes, [])

        assert terminal.haplogroup == "K"
        assert [step.haplogroup for step in path] == ["L3", "N", "R", "U", "U8", "K"]

    def test_source_polarity_l0_resolves_on_real_bundle(self, bundle: HaplogroupBundle) -> None:
        """#1080: an L0 source-motif sample should resolve below mt-MRCA."""
        genotypes = {row["rsid"]: row["genotype"] for row in _MT_L0_GENOTYPES}
        terminal, path = _tree_walk(bundle.mt_tree, genotypes, [])

        assert terminal.haplogroup == "L0"
        assert [step.haplogroup for step in path] == ["L0"]

    @pytest.mark.parametrize(
        ("rows", "expected_path"),
        [
            (_MT_N1_REVERSAL_GENOTYPES, ["L3", "N", "N1"]),
            (_MT_B_REVERSAL_GENOTYPES, ["L3", "N", "R", "B"]),
            (_MT_J_REVERSAL_GENOTYPES, ["L3", "N", "R", "JT", "J"]),
            (_MT_K1_REVERSAL_GENOTYPES, ["L3", "N", "R", "U", "U8", "K", "K1"]),
        ],
    )
    def test_n_subset_does_not_block_descendant_reversion_markers(
        self,
        bundle: HaplogroupBundle,
        rows: list[dict[str, object]],
        expected_path: list[str],
    ) -> None:
        """#1080: N markers must not conflict with known downstream reversions."""
        genotypes = {str(row["rsid"]): str(row["genotype"]) for row in rows}

        terminal, path = _tree_walk(bundle.mt_tree, genotypes, [])

        assert terminal.haplogroup == expected_path[-1]
        assert [step.haplogroup for step in path] == expected_path


class TestTreeWalkSharedAncestralMarkers:
    """#804: descent must rest on clade-*specific* derived markers, so a child
    that merely re-lists a marker inherited from its parent clade cannot divert or
    over-extend the walk — while a structural pass-through node (one defined solely
    by inherited markers) stays transparent to a deeper, supported clade."""

    def test_real_bundle_audited_m168_marker_can_be_terminal(
        self, bundle: HaplogroupBundle
    ) -> None:
        """The canonical M168 derived state supports CT without borrowing markers
        from descendant clades."""
        genotypes = {"rs2032595": "TT"}
        terminal, path = _tree_walk(
            bundle.y_tree,
            genotypes,
            [],
            min_internal_terminal_specific_snps=2,
            trusted_single_marker_terminal_rsids=frozenset({"rs2032595"}),
        )
        assert terminal.haplogroup == "CT"
        assert [s.haplogroup for s in path] == ["CT"]

    def test_real_bundle_partial_two_marker_de_evidence_reaches_de(
        self, bundle: HaplogroupBundle
    ) -> None:
        """One typed locus from each two-locus CT and DE definition reaches DE."""
        genotypes = {
            "rs2032595": "TT",  # CT / M168
            "rs9786479": "GG",  # DE / P153
        }

        terminal, path = _tree_walk(
            bundle.y_tree,
            genotypes,
            [],
            min_internal_terminal_specific_snps=2,
            trusted_single_marker_terminal_rsids=(bundle.y_trusted_single_marker_terminal_rsids),
        )

        assert terminal.haplogroup == "DE"
        assert [(s.haplogroup, s.snps_present, s.snps_total) for s in path] == [
            ("CT", 1, 2),
            ("DE", 1, 2),
        ]

    def test_real_bundle_single_audited_r_m207_marker_can_be_terminal(
        self, bundle: HaplogroupBundle
    ) -> None:
        """#1654: R must not depend on the removed autosomal rs1000546 placeholder.

        A sparse XY sample with its audited ancestral path plus the canonical R
        marker M207 (rs2032658 derived G) should resolve to R when the second
        independent R locus is untyped.
        """
        genotypes = {
            "rs2032595": "TT",  # CT / M168, C->T
            "rs2032652": "TT",  # F / M89, C->T
            "rs3900": "GG",  # K / M9, C->G
            "rs2033003": "CC",  # K2 / M526, A->C
            "rs2032631": "AA",  # P / M45, G->A
            "rs2032658": "GG",  # R / M207, A->G
        }

        terminal, path = _tree_walk(
            bundle.y_tree,
            genotypes,
            [],
            min_internal_terminal_specific_snps=2,
            trusted_single_marker_terminal_rsids=(bundle.y_trusted_single_marker_terminal_rsids),
        )

        assert terminal.haplogroup == "R"
        assert [step.haplogroup for step in path] == ["CT", "F", "K", "K2", "P", "R"]

    def test_real_bundle_m45_derived_a_resolves_to_p(self, bundle: HaplogroupBundle) -> None:
        """M45's canonical derived A routes a supported CT/F/K lineage to P."""
        genotypes = {row["rsid"]: row["genotype"] for row in _derived_y_path_genotypes("P")}

        terminal, path = _tree_walk(
            bundle.y_tree,
            genotypes,
            [],
            min_internal_terminal_specific_snps=2,
        )

        assert terminal.haplogroup == "P"
        assert [step.haplogroup for step in path] == ["CT", "F", "K", "K2", "P"]

    def test_withheld_l_markers_do_not_divert_to_f1(self, bundle: HaplogroupBundle) -> None:
        """Non-Y or misassigned L-lineage records cannot support F1."""
        genotypes = {
            "rs2032595": "TT",
            "rs2032652": "TT",
            "rs3900": "GG",
            "rs2032668": "TT",
            "rs9786139": "GG",
            "rs17316625": "GG",
            "rs34424943": "TT",
        }

        terminal, path = _tree_walk(
            bundle.y_tree,
            genotypes,
            [],
            min_internal_terminal_specific_snps=2,
            trusted_single_marker_terminal_rsids=frozenset(
                {"rs2032595", "rs2032652", "rs3900", "rs2032631", "rs2032658"}
            ),
        )

        assert terminal.haplogroup == "K"
        assert [step.haplogroup for step in path] == ["CT", "F", "K"]

    def test_withheld_s_markers_do_not_divert_to_f2_or_m2(self, bundle: HaplogroupBundle) -> None:
        """The unsupported S2 motif cannot route through F2 or M2 duplicates."""
        genotypes = {
            "rs2032595": "TT",
            "rs2032652": "TT",
            "rs3900": "GG",
            "rs9786076": "CC",
            "rs2032677": "GG",
            "rs17250359": "TT",
        }

        terminal, path = _tree_walk(
            bundle.y_tree,
            genotypes,
            [],
            min_internal_terminal_specific_snps=2,
            trusted_single_marker_terminal_rsids=frozenset(
                {"rs2032595", "rs2032652", "rs3900", "rs2032631", "rs2032658"}
            ),
        )

        assert terminal.haplogroup == "K"
        assert [step.haplogroup for step in path] == ["CT", "F", "K"]

    def test_one_marker_leaf_can_still_be_terminal_with_internal_floor(self) -> None:
        """The #1079 guard is for under-supported internal nodes. A one-SNP leaf has
        no deeper branch to over-resolve into, so the existing conflict/fraction
        rules still allow it as terminal."""
        root = HaplogroupNode(
            haplogroup="root",
            defining_snps=[],
            children=[
                HaplogroupNode(
                    haplogroup="A",
                    defining_snps=[HaplogroupSNP("a1", 1, "G"), HaplogroupSNP("a2", 2, "T")],
                    children=[
                        HaplogroupNode(
                            haplogroup="A1",
                            defining_snps=[HaplogroupSNP("leaf", 3, "C")],
                            children=[],
                        ),
                    ],
                ),
            ],
        )
        genotypes = {"a1": "GG", "a2": "TT", "leaf": "CC"}

        terminal, path = _tree_walk(
            root,
            genotypes,
            [],
            min_internal_terminal_specific_snps=2,
        )

        assert terminal.haplogroup == "A1"
        assert [s.haplogroup for s in path] == ["A", "A1"]

    def test_sparse_internal_passthrough_competes_with_direct_sibling(self) -> None:
        """A sparse internal branch that reaches deeper support should not be skipped
        just because a direct sibling also clears the minimum fraction."""
        root = HaplogroupNode(
            haplogroup="root",
            defining_snps=[],
            children=[
                HaplogroupNode(
                    haplogroup="SPARSE",
                    defining_snps=[HaplogroupSNP("s", 1, "G")],
                    children=[
                        HaplogroupNode(
                            haplogroup="DEEP",
                            defining_snps=[
                                HaplogroupSNP("d1", 2, "T"),
                                HaplogroupSNP("d2", 3, "C"),
                            ],
                            children=[],
                        ),
                    ],
                ),
                HaplogroupNode(
                    haplogroup="SIBLING",
                    defining_snps=[
                        HaplogroupSNP("sib1", 4, "A"),
                        HaplogroupSNP("sib2", 5, "G"),
                    ],
                    children=[],
                ),
            ],
        )
        genotypes = {
            "s": "GG",
            "d1": "TT",
            "d2": "CC",
            "sib1": "AA",
        }

        terminal, path = _tree_walk(
            root,
            genotypes,
            [],
            min_internal_terminal_specific_snps=2,
        )

        assert terminal.haplogroup == "DEEP"
        assert [s.haplogroup for s in path] == ["SPARSE", "DEEP"]

    def test_passthrough_ranking_ignores_inherited_marker_counts(self) -> None:
        """Candidate ranking must use clade-specific support, not the full display
        counts that include parent markers re-listed on structural pass-throughs."""
        root = HaplogroupNode(
            haplogroup="root",
            defining_snps=[],
            children=[
                HaplogroupNode(
                    haplogroup="PARENT",
                    defining_snps=[HaplogroupSNP("p", 1, "G")],
                    children=[
                        HaplogroupNode(
                            haplogroup="PT",
                            defining_snps=[HaplogroupSNP("p", 1, "G")],
                            children=[
                                HaplogroupNode(
                                    haplogroup="DEEP",
                                    defining_snps=[HaplogroupSNP("d", 2, "T")],
                                    children=[],
                                ),
                            ],
                        ),
                        HaplogroupNode(
                            haplogroup="SIBLING",
                            defining_snps=[
                                HaplogroupSNP("sib1", 3, "A"),
                                HaplogroupSNP("sib2", 4, "C"),
                            ],
                            children=[],
                        ),
                    ],
                ),
            ],
        )
        genotypes = {
            "p": "GG",
            "d": "TT",
            "sib1": "AA",
            "sib2": "CC",
        }

        terminal, path = _tree_walk(
            root,
            genotypes,
            [],
            min_internal_terminal_specific_snps=2,
        )

        assert terminal.haplogroup == "SIBLING"
        assert [s.haplogroup for s in path] == ["PARENT", "SIBLING"]

    def test_synthetic_shared_marker_children_do_not_over_resolve(self) -> None:
        """Two children that each re-list one of the parent's markers (the CT/DE,
        CT/F shape) are both refused when their own markers are untyped."""
        root = HaplogroupNode(
            haplogroup="root",
            defining_snps=[],
            children=[
                HaplogroupNode(
                    haplogroup="CT",
                    defining_snps=[HaplogroupSNP("m168", 1, "T"), HaplogroupSNP("x", 2, "G")],
                    children=[
                        HaplogroupNode(  # DE: own rs + re-listed x
                            haplogroup="DE",
                            defining_snps=[
                                HaplogroupSNP("de", 3, "T"),
                                HaplogroupSNP("x", 2, "G"),
                            ],
                            children=[],
                        ),
                        HaplogroupNode(  # F: re-listed m168 + own rs
                            haplogroup="F",
                            defining_snps=[
                                HaplogroupSNP("m168", 1, "T"),
                                HaplogroupSNP("f", 4, "C"),
                            ],
                            children=[],
                        ),
                    ],
                ),
            ],
        )
        # Only the two CT markers typed-derived; DE-specific (de) and F-specific (f) untyped.
        genotypes = {"m168": "TT", "x": "GG"}
        terminal, path = _tree_walk(root, genotypes, [])
        assert terminal.haplogroup == "CT"
        assert [s.haplogroup for s in path] == ["CT"]

    def test_passthrough_node_reaches_deeper_supported_clade(self) -> None:
        """A node defined only by an inherited marker is transparent: the walk
        descends through it to a deeper clade that has its own derived evidence."""
        root = HaplogroupNode(
            haplogroup="root",
            defining_snps=[],
            children=[
                HaplogroupNode(
                    haplogroup="P",
                    defining_snps=[HaplogroupSNP("x", 1, "G")],
                    children=[
                        HaplogroupNode(  # pass-through: re-lists only x
                            haplogroup="PT",
                            defining_snps=[HaplogroupSNP("x", 1, "G")],
                            children=[
                                HaplogroupNode(
                                    haplogroup="DEEP",
                                    defining_snps=[HaplogroupSNP("d", 2, "T")],
                                    children=[],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )
        genotypes = {"x": "GG", "d": "TT"}
        terminal, path = _tree_walk(root, genotypes, [])
        assert terminal.haplogroup == "DEEP"
        assert [s.haplogroup for s in path] == ["P", "PT", "DEEP"]

    def test_passthrough_node_with_no_supported_descendant_stops_at_parent(self) -> None:
        """A pass-through node (A1 = only A's marker re-listed) with no deeper
        supported clade is a spurious over-resolution and is not reported: the walk
        stops at the parent it is indistinguishable from (#805-robust)."""
        root = HaplogroupNode(
            haplogroup="root",
            defining_snps=[],
            children=[
                HaplogroupNode(
                    haplogroup="A",
                    defining_snps=[HaplogroupSNP("x", 1, "G")],
                    children=[
                        HaplogroupNode(  # A1: re-lists only x → no own evidence
                            haplogroup="A1",
                            defining_snps=[HaplogroupSNP("x", 1, "G")],
                            children=[
                                HaplogroupNode(  # A1b: own marker, but untyped here
                                    haplogroup="A1b",
                                    defining_snps=[HaplogroupSNP("b", 2, "T")],
                                    children=[],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )
        genotypes = {"x": "GG"}  # A1b-specific marker untyped
        terminal, path = _tree_walk(root, genotypes, [])
        assert terminal.haplogroup == "A"
        assert [s.haplogroup for s in path] == ["A"]


class TestYTreeSelfConsistency:
    """Run every emitted Y clade through the exact production tree-walk policy."""

    def test_every_emitted_y_node_resolves_to_itself(self, bundle: HaplogroupBundle) -> None:
        failures: list[str] = []

        def walk(node: HaplogroupNode, ancestors: list[HaplogroupNode]) -> None:
            current_path = [*ancestors, node]
            if node.haplogroup != "Y-Adam":
                genotypes = {
                    snp.rsid: snp.allele * 2
                    for path_node in current_path
                    for snp in path_node.defining_snps
                }
                terminal, traversal = _tree_walk(
                    bundle.y_tree,
                    genotypes,
                    [],
                    min_internal_terminal_specific_snps=2,
                    trusted_single_marker_terminal_rsids=(
                        bundle.y_trusted_single_marker_terminal_rsids
                    ),
                )
                expected_path = [path_node.haplogroup for path_node in current_path[1:]]
                actual_path = [step.haplogroup for step in traversal]
                if terminal.haplogroup != node.haplogroup or actual_path != expected_path:
                    failures.append(
                        f"{node.haplogroup}: terminal={terminal.haplogroup}, "
                        f"path={actual_path}, expected={expected_path}"
                    )
            for child in node.children:
                walk(child, current_path)

        walk(bundle.y_tree, [])
        assert not failures

    def test_missing_internal_marker_can_route_to_supported_descendant(
        self, bundle: HaplogroupBundle
    ) -> None:
        """A platform-specific gap at B must not make all-four-array B2 unreachable."""
        genotypes = {
            row["rsid"]: row["genotype"]
            for row in _derived_y_path_genotypes("B2")
            if row["rsid"] != "rs2032599"
        }

        terminal, traversal = _tree_walk(
            bundle.y_tree,
            genotypes,
            [],
            min_internal_terminal_specific_snps=(bundle.y_min_internal_terminal_specific_snps),
            trusted_single_marker_terminal_rsids=(bundle.y_trusted_single_marker_terminal_rsids),
            trusted_missing_internal_passthrough_rsids=(
                bundle.y_trusted_missing_internal_passthrough_rsids
            ),
        )

        assert terminal.haplogroup == "B2"
        assert [(step.haplogroup, step.snps_present, step.snps_total) for step in traversal] == [
            ("B", 0, 1),
            ("B2", 2, 2),
        ]

    def test_isolated_m269_cannot_jump_untyped_y_ancestors(self, bundle: HaplogroupBundle) -> None:
        terminal, traversal = _tree_walk(
            bundle.y_tree,
            {"rs9786153": "CC"},
            [],
            min_internal_terminal_specific_snps=(bundle.y_min_internal_terminal_specific_snps),
            trusted_single_marker_terminal_rsids=(bundle.y_trusted_single_marker_terminal_rsids),
            trusted_missing_internal_passthrough_rsids=(
                bundle.y_trusted_missing_internal_passthrough_rsids
            ),
        )

        assert terminal.haplogroup == "Y-Adam"
        assert traversal == []

    def test_h1a_leaf_markers_cannot_jump_untyped_mt_ancestors(
        self, bundle: HaplogroupBundle
    ) -> None:
        terminal, traversal = _tree_walk(
            bundle.mt_tree,
            {"rs1000390": "TT", "i5013404": "CC"},
            [],
        )

        assert terminal.haplogroup == "mt-MRCA"
        assert traversal == []

    def test_legacy_basal_a_markers_do_not_divert_ct_lineage(
        self, bundle: HaplogroupBundle
    ) -> None:
        """P305/V168 and P108/V221 are basal to BT, not competing CT siblings."""
        genotypes = {row["rsid"]: row["genotype"] for row in _derived_y_path_genotypes("R")}
        genotypes.update(
            {
                "rs72625368": "GG",  # P305
                "rs191505182": "AA",  # V168
                "rs761539052": "TT",  # P108
                "rs188292317": "TT",  # V221
            }
        )

        terminal, traversal = _tree_walk(
            bundle.y_tree,
            genotypes,
            [],
            min_internal_terminal_specific_snps=(bundle.y_min_internal_terminal_specific_snps),
            trusted_single_marker_terminal_rsids=(bundle.y_trusted_single_marker_terminal_rsids),
        )

        assert terminal.haplogroup == "R"
        assert traversal[0].haplogroup == "CT"


# ── Full haplogroup assignment tests ────────────────────────────────────


class TestAssignHaplogroups:
    """Test the full haplogroup assignment pipeline."""

    def test_mt_only_xx(self, bundle: HaplogroupBundle, sample_engine: sa.Engine) -> None:
        """T3-32: Y-chromosome assignment skipped when sex_inferred = 'XX'."""
        _seed_mt_h1a(sample_engine)
        results = assign_haplogroups(bundle, sample_engine)

        assert len(results) == 1
        assert results[0].tree_type == "mt"
        assert results[0].haplogroup == "H1a"

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_mt_assigned_when_vendor_rsids_differ_from_bundle(
        self, bundle: HaplogroupBundle, sample_engine: sa.Engine, source_table: sa.Table
    ) -> None:
        """#498: real vendor files label mtDNA with their own ids (or none) — never the
        bundle's synthetic ``i5<pos>`` ids — so mtDNA must be assigned by rCRS POSITION
        on chrom MT, not by a doomed rsid join. Re-key the H1a fixture onto vendor-style
        rsids that are absent from the bundle (keeping the real chrom MT + pos) and
        confirm H1a is still assigned. Parameterized over both source tables, since
        assign_haplogroups reads annotated_variants once that table is populated and
        falls back to raw_variants otherwise — both MT position paths must hold."""
        vendor_rows = [
            {**row, "rsid": f"i{900000 + idx}"} for idx, row in enumerate(_H1A_GENOTYPES)
        ]
        # The test is only meaningful if NONE of these rsids match the bundle — i.e.
        # any successful assignment comes from the position join, not a lucky rsid hit.
        assert not ({r["rsid"] for r in vendor_rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), vendor_rows)

        results = assign_haplogroups(bundle, sample_engine)

        assert len(results) == 1
        assert results[0].tree_type == "mt"
        # Assigned by rCRS position despite zero rsid matches (pre-#498 this was mt-MRCA).
        assert results[0].haplogroup == "H1a"
        assert results[0].defining_snps_present > 0

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("conflict_first", [False, True])
    def test_mt_duplicate_position_discordance_is_order_independent(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        conflict_first: bool,
    ) -> None:
        """#1388: discordant duplicate MT probes must not depend on row order.

        Position 769 defines the L3 step in the H1a fixture. A second, non-alias
        probe at the same rCRS position but with an ancestral call is ambiguous
        evidence for that coordinate, not evidence against L3. The position is
        therefore treated as missing, leaving the rest of the H1a motif to drive
        a stable result regardless of insertion order or source table.
        """
        conflict = {"rsid": "i_conflict_769", "chrom": "MT", "pos": 769, "genotype": "AA"}
        rows = [conflict, *_H1A_GENOTYPES] if conflict_first else [*_H1A_GENOTYPES, conflict]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        results = assign_haplogroups(bundle, sample_engine)

        assert len(results) == 1
        mt = results[0]
        assert mt.tree_type == "mt"
        assert mt.haplogroup == "H1a"
        # 13 H1a-path defining SNPs (#1579 removed R0's m.73 + H's rs1000687); the
        # ambiguous duplicate at m.769 is treated as missing, so 12 of 13 present.
        assert mt.defining_snps_present == 12
        assert mt.defining_snps_total == 13

    def test_both_mt_and_y(self, bundle: HaplogroupBundle, sample_engine: sa.Engine) -> None:
        """XY sample gets both mt and Y haplogroup assignments."""
        _seed_both(sample_engine)
        results = assign_haplogroups(bundle, sample_engine)

        assert len(results) == 2
        mt = next(r for r in results if r.tree_type == "mt")
        y = next(r for r in results if r.tree_type == "Y")

        assert mt.haplogroup == "H1a"
        # Tree may walk deeper than R1b1a if child nodes also match
        assert y.haplogroup.startswith("R1b1a")
        assert [step.haplogroup for step in y.traversal_path[:6]] == [
            "CT",
            "F",
            "K",
            "K2",
            "P",
            "R",
        ]

    def test_y_m45_sample_resolves_to_p(
        self, bundle: HaplogroupBundle, sample_engine: sa.Engine
    ) -> None:
        """An XY sample with the canonical M168/M89/M9/M45 states reaches P."""
        path_rows = _derived_y_path_genotypes("P")
        assert {"rs2032595", "rs2032652", "rs3900", "rs2032631"} <= {
            row["rsid"] for row in path_rows
        }
        rows = path_rows + _Y_TYPED_PADDING + _NONPAR_X_HOM_GENOTYPES
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), rows)

        results = assign_haplogroups(bundle, sample_engine)

        y = next(result for result in results if result.tree_type == "Y")
        assert y.haplogroup == "P"
        assert [step.haplogroup for step in y.traversal_path] == ["CT", "F", "K", "K2", "P"]

    def test_y_bundle_policy_allows_audited_k2_terminal(
        self, bundle: HaplogroupBundle, sample_engine: sa.Engine
    ) -> None:
        """The bundle-only M526 exception is consumed by production assignment."""
        rows = _derived_y_path_genotypes("K2") + _Y_TYPED_PADDING + _NONPAR_X_HOM_GENOTYPES
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), rows)

        results = assign_haplogroups(bundle, sample_engine)

        y = next(result for result in results if result.tree_type == "Y")
        assert y.haplogroup == "K2"
        assert y.traversal_path[-1].haplogroup == "K2"

    def test_y_partial_two_marker_de_evidence_reaches_de(
        self, bundle: HaplogroupBundle, sample_engine: sa.Engine
    ) -> None:
        """A two-locus DE definition can meet the 0.5 fraction with one typed locus."""
        rows = (
            [
                {"rsid": "rs2032595", "chrom": "Y", "pos": 14813991, "genotype": "TT"},
                {"rsid": "rs9786479", "chrom": "Y", "pos": 18561042, "genotype": "GG"},
            ]
            + _Y_TYPED_PADDING
            + _NONPAR_X_HOM_GENOTYPES
        )
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), rows)

        results = assign_haplogroups(bundle, sample_engine)

        assert len(results) == 2
        y = next(r for r in results if r.tree_type == "Y")
        assert y.haplogroup == "DE"
        assert [(s.haplogroup, s.snps_present, s.snps_total) for s in y.traversal_path] == [
            ("CT", 1, 2),
            ("DE", 1, 2),
        ]

    def test_confidence_calculation(
        self, bundle: HaplogroupBundle, sample_engine: sa.Engine
    ) -> None:
        """T3-33: confidence = defining_snps_present / defining_snps_total, pinned
        to INDEPENDENTLY-derived literals (#640).

        The ``_seed_mt_h1a`` path is deterministic — mt-MRCA → L3 → N → R → R0 →
        HV → H → H1 → H1a, with 3 + 3 + 2 + 0 + 1 + 1 + 1 + 2 = 13 defining SNPs
        (R0's recurrent m.73 marker and H's spurious autosomal rs1000687 were
        removed in #1579), all 13 derived in the fixture — so the expected
        present/total/confidence are knowable offline (13 / 13 → 1.0). Asserting
        those literals, rather
        than recomputing from the result's own ``defining_snps_present`` /
        ``defining_snps_total`` (the old self-derivation tautology), means a
        present/total miscount (e.g. an #498-class tree-walk counting a
        conflicting/ancestral marker as derived) or a changed confidence formula
        now fails here instead of shipping green. The formula itself is pinned
        against a non-trivial ratio in :class:`TestHaplogroupConfidence`."""
        _seed_mt_h1a(sample_engine)
        results = assign_haplogroups(bundle, sample_engine)

        mt = results[0]
        assert mt.haplogroup == "H1a"
        assert mt.defining_snps_present == 13
        assert mt.defining_snps_total == 13
        assert mt.confidence == 1.0

    def test_traversal_path_populated(
        self, bundle: HaplogroupBundle, sample_engine: sa.Engine
    ) -> None:
        """Traversal path includes intermediate nodes with match counts."""
        _seed_mt_h1a(sample_engine)
        results = assign_haplogroups(bundle, sample_engine)

        mt = results[0]
        assert len(mt.traversal_path) > 0
        for step in mt.traversal_path:
            assert isinstance(step.haplogroup, str)
            assert step.snps_present >= 0
            # R0 is a structural pass-through node with no defining SNP (its only
            # marker, the recurrent m.73, was removed in #1579), so total may be 0.
            assert step.snps_total >= 0

    def test_empty_sample(self, bundle: HaplogroupBundle, sample_engine: sa.Engine) -> None:
        """Empty sample returns mt-MRCA (root) with empty traversal path."""
        results = assign_haplogroups(bundle, sample_engine)

        assert len(results) == 1
        mt = results[0]
        assert mt.haplogroup == "mt-MRCA"
        assert len(mt.traversal_path) == 0

    def test_mt_source_trunk_assigned_by_position(
        self, bundle: HaplogroupBundle, sample_engine: sa.Engine
    ) -> None:
        """#1080: source-direction L3/N/R calls assign through the DB position path."""
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), _MT_R_TRUNK_GENOTYPES)

        results = assign_haplogroups(bundle, sample_engine)

        assert len(results) == 1
        mt = results[0]
        assert mt.tree_type == "mt"
        assert mt.haplogroup == "R"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "N", "R"]


# ── Y A-branch polarity / placement regression (#660) ────────────────────


def _find_y_node(node: HaplogroupNode, haplogroup: str) -> HaplogroupNode | None:
    """Depth-first search for a node by haplogroup name."""
    if node.haplogroup == haplogroup:
        return node
    for child in node.children:
        found = _find_y_node(child, haplogroup)
        if found is not None:
            return found
    return None


class TestYABranchPolarity:
    """Regression for #660 (a CT/M168+ male must not be mis-assigned to A) and its
    correct root cause, fixed in #1583.

    ``rs2032597`` is **M170** — an A→C transversion whose derived C defines
    haplogroup **I** (I-M170), not the basal A lineage (Ensembl GRCh37 rs2032597
    A/C, ancestral A; Wikipedia "Haplogroup I-M170"). The #660/#805 remediation
    left M170 on the A/A1 nodes and band-aided the polarity (allele ``C``), which
    asserted a false marker→clade fact. #1583 moved M170 to the **I** node. The
    source-audited tree now omits generic or paraphyletic basal A placeholders and
    emits only independently distinguishable A0/A1a/A1b1 descendants. M168
    (``rs2032595``) remains a CT marker, not an A-lineage competitor.
    """

    def test_ct_m168_male_resolves_into_ct_not_a_branch(
        self, bundle: HaplogroupBundle, sample_engine: sa.Engine
    ) -> None:
        """End-to-end: a CT/M168+ male with rs2032597 typed as the ancestral A
        resolves into the CT subtree (its real clade), never the A branch."""
        rows = _CT_M168_GENOTYPES + _Y_TYPED_PADDING + _NONPAR_X_HOM_GENOTYPES
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), rows)

        results = assign_haplogroups(bundle, sample_engine)
        y = next(r for r in results if r.tree_type == "Y")
        path = [step.haplogroup for step in y.traversal_path]

        # The bug surfaced basal-African A1b (path A → A1 → A1b); the fix keeps
        # the walk in the man's true CT clade.
        assert path[0] == "CT", f"expected CT branch, walked into {path!r}"
        assert not ({"A", "A0", "A1", "A1a", "A1b", "A1b1"} & set(path)), (
            f"non-A man mis-routed through the A branch: {path!r}"
        )
        assert y.haplogroup not in {"A", "A0", "A1", "A1a", "A1b", "A1b1"}

    def test_rs2032597_m170_not_on_basal_a_nodes(self, bundle: HaplogroupBundle) -> None:
        """#1583: rs2032597 (M170) is haplogroup I's marker, not A's. It must not
        define the basal A/A1 nodes — a real haplogroup A man carries the ancestral
        A (A split off before M170's A→C mutation), so defining A by M170's derived
        C (the #805 band-aid) is doubly wrong: derived allele + foreign clade."""
        for name in ("A0", "A1a", "A1b1"):
            node = _find_y_node(bundle.y_tree, name)
            assert node is not None, f"{name} node missing from bundle"
            assert "rs2032597" not in {s.rsid for s in node.defining_snps}, (
                f"rs2032597 (M170, a haplogroup-I marker) must not define {name} (#1583)"
            )

    def test_m168_not_an_a1b_defining_marker(self, bundle: HaplogroupBundle) -> None:
        """M168 defines CT; the paraphyletic flattened A1b placeholder is omitted."""
        assert _find_y_node(bundle.y_tree, "A1b") is None

        ct = _find_y_node(bundle.y_tree, "CT")
        assert ct is not None
        assert "rs2032595" in {s.rsid for s in ct.defining_snps}

    def test_i_node_defined_by_m170_rs2032597_derived_c(self, bundle: HaplogroupBundle) -> None:
        """#1583 (corrects the inverted #805 premise): rs2032597 (M170) IS
        haplogroup I's canonical defining SNP, with the DERIVED allele C indicating
        I (Ensembl GRCh37 A/C; Wikipedia 'Haplogroup I-M170'). An M170+ man (derived
        C) matches; an M170- man (ancestral A) conflicts."""
        i_node = _find_y_node(bundle.y_tree, "I")
        assert i_node is not None
        snp = next((s for s in i_node.defining_snps if s.rsid == "rs2032597"), None)
        assert snp is not None, "I must be defined by its canonical marker M170 (rs2032597)"
        assert snp.allele == "C", "M170's I-indicating (derived) allele is C"

        present, conflicting, _ = _classify_node_match(i_node, {"rs2032597": "C"})
        assert present >= 1 and conflicting == 0  # M170+ (derived C) → evidence FOR I
        present, conflicting, _ = _classify_node_match(i_node, {"rs2032597": "A"})
        assert conflicting >= 1  # ancestral A → evidence against I

    def test_audited_y_rsids_match_grch37_and_derived_states(
        self, bundle: HaplogroupBundle
    ) -> None:
        """Audited Y rsIDs keep GRCh37 coordinates and derived alleles. The
        classifier keys by rsID, but records remain reference-consistent."""

        def y_snps(node: HaplogroupNode) -> list[HaplogroupSNP]:
            out = list(node.defining_snps)
            for child in node.children:
                out.extend(y_snps(child))
            return out

        by_rsid: dict[str, set[tuple[int, str]]] = {}
        for snp in y_snps(bundle.y_tree):
            by_rsid.setdefault(snp.rsid, set()).add((snp.pos, snp.allele))

        assert by_rsid["rs13447352"] == {(22749853, "C")}
        assert by_rsid["rs2032595"] == {(14813991, "T")}
        assert by_rsid["rs2032597"] == {(14847792, "C")}
        assert by_rsid["rs2032631"] == {(21867787, "A")}
        assert by_rsid["rs2032652"] == {(21917313, "T")}
        assert by_rsid["rs2032658"] == {(15581983, "G")}
        assert by_rsid["rs9786153"] == {(22739367, "C")}
        assert by_rsid["rs2032673"] == {(21894058, "C")}
        assert by_rsid["rs9341279"] == {(15437152, "T")}
        assert by_rsid["rs9341286"] == {(15019092, "C")}
        assert by_rsid["rs9341296"] == {(15022707, "T")}
        assert by_rsid["rs3900"] == {(21730257, "G")}

    def test_excluded_y_rsids_are_absent(self, bundle: HaplogroupBundle) -> None:
        """#1654: known non-Y or unresolved duplicate rsIDs must not define Y clades."""

        def y_snps(node: HaplogroupNode) -> list[HaplogroupSNP]:
            out = list(node.defining_snps)
            for child in node.children:
                out.extend(y_snps(child))
            return out

        by_rsid = {snp.rsid for snp in y_snps(bundle.y_tree)}
        assert "rs1000546" not in by_rsid  # Ensembl GRCh37 chr18 via rs502450 alias
        assert "rs35489731" not in by_rsid  # Ensembl GRCh37 chr2
        assert {"rs9341278", "rs2032604"} <= by_rsid
        assert next(s for s in y_snps(bundle.y_tree) if s.rsid == "rs9341278").allele == "A"
        assert next(s for s in y_snps(bundle.y_tree) if s.rsid == "rs2032604").allele == "G"
        assert "rs13304168" not in by_rsid  # impossible historic G allele; clade unresolved
        assert not (_CROSS_CLADE_WITHHELD_Y_RSIDS & by_rsid)

    def test_r_node_defined_by_m207_rs2032658_derived_g(self, bundle: HaplogroupBundle) -> None:
        """#1654 (Class A allele polarity): rs2032658 (M207) defines haplogroup R
        with the DERIVED allele G (Ensembl GRCh37 rs2032658 G/A, ancestral A). The
        node had stored the ancestral A, so a real R man (M207+, derived G) scored
        conflicting at R — the ancestral-inversion class of #1583/#1579."""
        r_node = _find_y_node(bundle.y_tree, "R")
        assert r_node is not None
        snp = next((s for s in r_node.defining_snps if s.rsid == "rs2032658"), None)
        assert snp is not None, "R must be defined by its canonical marker M207 (rs2032658)"
        assert snp.allele == "G", "M207's R-indicating (derived) allele is G"
        assert snp.pos == 15581983, "rs2032658 at its Ensembl GRCh37 coordinate (Y:15581983)"

        present, conflicting, _ = _classify_node_match(r_node, {"rs2032658": "G"})
        assert present >= 1 and conflicting == 0  # M207+ (derived G) → evidence FOR R
        present, conflicting, _ = _classify_node_match(r_node, {"rs2032658": "A"})
        assert conflicting >= 1  # ancestral A → evidence against R

    def test_canonical_y_markers_are_filed_under_the_correct_clade(
        self, bundle: HaplogroupBundle
    ) -> None:
        """Marker→clade guard (#1583/#1584): a canonical, well-established Y-SNP
        must define its own clade's subtree and appear NOWHERE outside it. Covers
        the recurring mis-attribution class — #660/#805/#1583 (M170 under A instead
        of I) and #1584 (M269 spuriously duplicated onto I1b, M207 onto G2/G2a).
        Extend ``_CANONICAL_Y_MARKER_CLADE`` as more ISOGG markers are audited."""

        def subtree_rsids(node: object) -> list[str]:
            out = [s.rsid for s in node.defining_snps]
            for child in node.children:
                out += subtree_rsids(child)
            return out

        all_occurrences = subtree_rsids(bundle.y_tree)
        for rsid, clade in _CANONICAL_Y_MARKER_CLADE.items():
            clade_node = _find_y_node(bundle.y_tree, clade)
            assert clade_node is not None, f"clade {clade} missing from Y tree"
            in_clade = subtree_rsids(clade_node)
            assert rsid in in_clade, (
                f"{rsid} must be a defining marker of its canonical clade {clade}"
            )
            # Every occurrence in the whole Y tree must fall inside the canonical
            # clade's subtree — a copy on any unrelated clade is a mis-attribution.
            assert all_occurrences.count(rsid) == in_clade.count(rsid), (
                f"{rsid} (a {clade} marker) appears on a clade outside the {clade} subtree"
            )


# Canonical, well-established Y-SNP marker → its single defining clade (ISOGG). The
# marker must appear only within this clade's subtree; a copy on any unrelated clade
# is a mis-attribution (#660/#805/#1583/#1584). Add entries as bundle markers are
# audited against an authoritative Y-SNP index.
_CANONICAL_Y_MARKER_CLADE: dict[str, str] = {
    "rs13447352": "J",  # M304 / Page16 / PF4609: A->C defines J.
    "rs2032595": "CT",  # M168: C->T defines CT.
    "rs2032597": "I",  # M170: A→C, derived C defines haplogroup I (not the basal A).
    "rs2032652": "F",  # M89: C->T defines F.
    "rs2032673": "H1a",  # M69 / Page45: T->C defines H1a in the source snapshot.
    "rs3900": "K",  # M9: C->G defines K.
    "rs2032631": "P",  # M45: canonical G->A defines P/P1.
    "rs9341279": "N_Y",  # M232 / M2188: C->T defines Y haplogroup N.
    "rs9341278": "N_Y",  # M231: G->A defines Y haplogroup N.
    "rs9341286": "E1b1b",  # M243 / PF1943: T->C defines E1b1b.
    "rs9786153": "R1b1a1a",  # M269 at the closest emitted simplified descendant.
    "rs2032658": "R",  # M207: defines haplogroup R (R-M207), not G.
    "rs2032604": "J2",  # M172: T->G defines J2.
}

_CROSS_CLADE_WITHHELD_Y_RSIDS = {
    "rs16981295",
    "rs17250359",
    "rs17250625",
    "rs17250667",
    "rs17316625",
    "rs17316724",
    "rs17317007",
    "rs2032623",
    "rs2032677",
    "rs34175940",
    "rs34282407",
    "rs34424943",
    "rs34602841",
    "rs35882927",
    "rs9341283",
    "rs9786076",
    "rs9786139",
    "rs9786281",
    "rs9786429",
    "rs9786856",
}


# ── Confidence formula unit tests (#640) ─────────────────────────────────


class TestHaplogroupConfidence:
    """Pin the ``_haplogroup_confidence`` formula to literals (#640).

    The integration fixture happens to be a full match (17 / 17 → 1.0), a ratio
    too trivial to distinguish ``present / total`` from alternatives on its own.
    These cases pin the formula against a NON-trivial ratio (16 / 17) and the
    zero-denominator guard, so a Jaccard-style rewrite — ``present / (total +
    present)`` — or any other formula change fails here. Shared by both the mt
    and Y tree-walks, so this is the single place the arithmetic is locked.
    """

    def test_partial_path_ratio_is_present_over_total(self) -> None:
        # 16 / 17 = 0.94117… → 0.9412 rounded. A Jaccard present/(total+present)
        # would be 16 / 33 = 0.4848, and present/(total) inverted (total/present)
        # would be 17 / 16 = 1.0625 — neither rounds to 0.9412.
        assert round(_haplogroup_confidence(16, 17), 4) == 0.9412

    def test_full_match_is_one(self) -> None:
        assert _haplogroup_confidence(17, 17) == 1.0

    def test_half_match(self) -> None:
        assert _haplogroup_confidence(1, 2) == 0.5

    def test_zero_total_guards_division(self) -> None:
        # Root / empty path: no defining SNP evaluated → 0.0, not ZeroDivisionError.
        assert _haplogroup_confidence(0, 0) == 0.0


# ── Findings storage tests ──────────────────────────────────────────────


class TestStoreHaplogroupFindings:
    """Test haplogroup findings storage."""

    def test_stores_in_haplogroup_assignments(self, sample_engine: sa.Engine) -> None:
        """T3-34: haplogroup_assignments table populated correctly."""
        results = [
            HaplogroupResult(
                tree_type="mt",
                haplogroup="H1a",
                confidence=0.9412,
                defining_snps_present=16,
                defining_snps_total=17,
                traversal_path=[
                    HaplogroupTraversalStep("L3", 3, 3),
                    HaplogroupTraversalStep("N", 5, 5),
                    HaplogroupTraversalStep("R", 2, 2),
                    HaplogroupTraversalStep("R0", 1, 1),
                    HaplogroupTraversalStep("HV", 1, 1),
                    HaplogroupTraversalStep("H", 2, 2),
                    HaplogroupTraversalStep("H1", 1, 1),
                    HaplogroupTraversalStep("H1a", 1, 2),
                ],
                assignment_time_ms=0.5,
            ),
        ]

        count = store_haplogroup_findings(results, sample_engine)
        assert count == 1

        with sample_engine.connect() as conn:
            rows = conn.execute(sa.select(haplogroup_assignments)).fetchall()
            assert len(rows) == 1
            row = rows[0]
            assert row.type == "mt"
            assert row.haplogroup == "H1a"
            assert row.confidence == pytest.approx(0.9412)
            assert row.defining_snps_present == 16
            assert row.defining_snps_total == 17

    def test_stores_finding(self, sample_engine: sa.Engine) -> None:
        """Finding inserted with module='ancestry' and category='haplogroup_mt'."""
        results = [
            HaplogroupResult(
                tree_type="mt",
                haplogroup="H1a",
                confidence=1.0,
                defining_snps_present=17,
                defining_snps_total=17,
                traversal_path=[HaplogroupTraversalStep("H1a", 17, 17)],
                assignment_time_ms=0.5,
            ),
        ]

        store_haplogroup_findings(results, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "ancestry",
                    findings.c.category == "haplogroup_mt",
                )
            ).fetchall()
            assert len(rows) == 1
            row = rows[0]
            assert row.haplogroup == "H1a"
            assert row.evidence_level == 2
            assert "H1a" in row.finding_text
            assert "17/17" in row.finding_text

            detail = json.loads(row.detail_json)
            assert detail["haplogroup"] == "H1a"
            assert detail["confidence"] == 1.0
            assert len(detail["traversal_path"]) == 1

    def test_stores_both_mt_and_y(self, sample_engine: sa.Engine) -> None:
        """Both mt and Y findings stored."""
        results = [
            HaplogroupResult(
                tree_type="mt",
                haplogroup="H1a",
                confidence=1.0,
                defining_snps_present=17,
                defining_snps_total=17,
                traversal_path=[HaplogroupTraversalStep("H1a", 17, 17)],
                assignment_time_ms=0.5,
            ),
            HaplogroupResult(
                tree_type="Y",
                haplogroup="R1b",
                confidence=0.9,
                defining_snps_present=9,
                defining_snps_total=10,
                traversal_path=[HaplogroupTraversalStep("R1b", 9, 10)],
                assignment_time_ms=0.3,
            ),
        ]

        count = store_haplogroup_findings(results, sample_engine)
        assert count == 2

        with sample_engine.connect() as conn:
            ha_rows = conn.execute(sa.select(haplogroup_assignments)).fetchall()
            assert len(ha_rows) == 2
            types = {r.type for r in ha_rows}
            assert types == {"mt", "Y"}

            f_rows = conn.execute(
                sa.select(findings).where(findings.c.module == "ancestry")
            ).fetchall()
            assert len(f_rows) == 2
            categories = {r.category for r in f_rows}
            assert "haplogroup_mt" in categories
            assert "haplogroup_Y" in categories
            findings_by_category = {r.category: r for r in f_rows}
            assert (
                findings_by_category["haplogroup_mt"].finding_text
                == "Mitochondrial haplogroup: H1a (17/17 defining SNPs matched, 100% confidence)"
            )
            assert (
                findings_by_category["haplogroup_Y"].finding_text
                == "Y-chromosome haplogroup: R1b (9/10 defining SNPs matched, 90% confidence)"
            )

    def test_replaces_previous_assignments(self, sample_engine: sa.Engine) -> None:
        """Re-running clears old assignments."""
        results = [
            HaplogroupResult(
                tree_type="mt",
                haplogroup="H",
                confidence=1.0,
                defining_snps_present=2,
                defining_snps_total=2,
                traversal_path=[HaplogroupTraversalStep("H", 2, 2)],
                assignment_time_ms=0.5,
            ),
        ]
        store_haplogroup_findings(results, sample_engine)

        # Re-store with different haplogroup
        results[0] = HaplogroupResult(
            tree_type="mt",
            haplogroup="H1a",
            confidence=0.9,
            defining_snps_present=16,
            defining_snps_total=17,
            traversal_path=[HaplogroupTraversalStep("H1a", 16, 17)],
            assignment_time_ms=0.4,
        )
        store_haplogroup_findings(results, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(sa.select(haplogroup_assignments)).fetchall()
            assert len(rows) == 1
            assert rows[0].haplogroup == "H1a"

    def test_rerun_without_y_clears_stale_y_finding(self, sample_engine: sa.Engine) -> None:
        """Re-running with no Y result clears the stored uppercase Y finding."""
        mt_result = HaplogroupResult(
            tree_type="mt",
            haplogroup="H1a",
            confidence=1.0,
            defining_snps_present=17,
            defining_snps_total=17,
            traversal_path=[HaplogroupTraversalStep("H1a", 17, 17)],
            assignment_time_ms=0.5,
        )
        y_result = HaplogroupResult(
            tree_type="Y",
            haplogroup="R1b",
            confidence=0.9,
            defining_snps_present=9,
            defining_snps_total=10,
            traversal_path=[HaplogroupTraversalStep("R1b", 9, 10)],
            assignment_time_ms=0.3,
        )

        assert store_haplogroup_findings([mt_result, y_result], sample_engine) == 2
        assert store_haplogroup_findings([mt_result], sample_engine) == 1

        with sample_engine.connect() as conn:
            categories = (
                conn.execute(
                    sa.select(findings.c.category)
                    .where(findings.c.module == "ancestry")
                    .order_by(findings.c.category)
                )
                .scalars()
                .all()
            )
            assignment_types = (
                conn.execute(
                    sa.select(haplogroup_assignments.c.type).order_by(
                        haplogroup_assignments.c.type
                    )
                )
                .scalars()
                .all()
            )

        assert categories == ["haplogroup_mt"]
        assert assignment_types == ["mt"]

    def test_empty_results(self, sample_engine: sa.Engine) -> None:
        """Empty results list stores nothing."""
        count = store_haplogroup_findings([], sample_engine)
        assert count == 0

    def test_skips_root_only_result(self, sample_engine: sa.Engine) -> None:
        """Result with empty traversal path (root only) is skipped."""
        results = [
            HaplogroupResult(
                tree_type="mt",
                haplogroup="mt-MRCA",
                confidence=0.0,
                defining_snps_present=0,
                defining_snps_total=0,
                traversal_path=[],
                assignment_time_ms=0.1,
            ),
        ]
        count = store_haplogroup_findings(results, sample_engine)
        assert count == 0


# ── Integration test ────────────────────────────────────────────────────


class TestRunHaplogroupAssignment:
    """Integration test for the full pipeline."""

    def test_full_pipeline_mt(self, sample_engine: sa.Engine) -> None:
        """Full pipeline: load → assign → store for mtDNA only."""
        _seed_mt_h1a(sample_engine)
        results = run_haplogroup_assignment(sample_engine, bundle_path=BUNDLE_PATH)

        assert len(results) == 1
        assert results[0].haplogroup == "H1a"

        # Verify haplogroup_assignments populated
        with sample_engine.connect() as conn:
            rows = conn.execute(sa.select(haplogroup_assignments)).fetchall()
            assert len(rows) == 1
            assert rows[0].haplogroup == "H1a"

    def test_full_pipeline_xy(self, sample_engine: sa.Engine) -> None:
        """Full pipeline for XY sample: both mt and Y stored."""
        _seed_both(sample_engine)
        results = run_haplogroup_assignment(sample_engine, bundle_path=BUNDLE_PATH)

        assert len(results) == 2

        with sample_engine.connect() as conn:
            rows = conn.execute(sa.select(haplogroup_assignments)).fetchall()
            assert len(rows) == 2

            f_rows = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "ancestry",
                    findings.c.category.like("haplogroup_%"),
                )
            ).fetchall()
            assert len(f_rows) == 2


# ── Sex-inference rewire regression (Step 54 / Plan §9.4) ───────────────


# Heterozygous non-PAR chrX calls over an evaluable denominator (issue #363);
# combined with ``_Y_NOCALL_PADDING`` (chrY at rate 0.0) → XX under §9.4.
_XX_CHROM_X_HET = [
    {"rsid": f"rs_xx_x_het_{i}", "chrom": "X", "pos": 50_000_001 + i, "genotype": "AG"}
    for i in range(60)
] + [
    {"rsid": f"rs_xx_x_hom_{i}", "chrom": "X", "pos": 50_100_001 + i, "genotype": "GG"}
    for i in range(60)
]


class TestHaplogroupSexInferenceRewire:
    """Lock byte-identical ``assign_haplogroups`` output on 23andMe-shaped
    XX and XY regression fixtures after the sex-inference rewire (Step 54).

    Plan §9.4 attests that the new PAR-aware algorithm matches the legacy
    ``y_count > 0`` heuristic on well-behaved XY/XX samples; this class is
    the regression fence. Sex-inference branch coverage lives in
    ``tests/backend/test_sex_inference.py``.
    """

    def test_xx_regression_fixture_yields_mt_only(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
    ) -> None:
        """23andMe XX regression: mtDNA assigned, Y tree-walk skipped."""
        from backend.services.sex_inference import infer_biological_sex

        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(raw_variants),
                _H1A_GENOTYPES + _XX_CHROM_X_HET + _Y_NOCALL_PADDING,
            )

        assert infer_biological_sex(sample_engine) == "XX"

        results = assign_haplogroups(bundle, sample_engine)

        assert len(results) == 1
        assert results[0].tree_type == "mt"
        assert results[0].haplogroup == "H1a"

    def test_xy_regression_fixture_yields_both_mt_and_y(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
    ) -> None:
        """23andMe XY regression: both mtDNA + Y haplogroups assigned.

        Uses ``_seed_both`` (chrX hom + chrY R1b1a + mt H1a), the same
        fixture ``TestAssignHaplogroups.test_both_mt_and_y`` exercises,
        which the rewire keeps byte-identical.
        """
        from backend.services.sex_inference import infer_biological_sex

        _seed_both(sample_engine)

        assert infer_biological_sex(sample_engine) == "XY"

        results = assign_haplogroups(bundle, sample_engine)

        assert len(results) == 2
        mt = next(r for r in results if r.tree_type == "mt")
        y = next(r for r in results if r.tree_type == "Y")
        assert mt.haplogroup == "H1a"
        # Tree-walk may descend deeper than R1b1a when child nodes also
        # match — same prefix-lock contract as the original test.
        assert y.haplogroup.startswith("R1b1a")

    def test_haplogroup_gate_matches_direct_sex_inference_call(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
    ) -> None:
        """The rewired ``assign_haplogroups`` Y-gate must observe the same
        classification the service returns when called directly — single
        source of truth (Plan §9.4)."""
        from backend.services.sex_inference import infer_biological_sex

        _seed_both(sample_engine)

        direct_sex = infer_biological_sex(sample_engine)
        results = assign_haplogroups(bundle, sample_engine)
        gated_tree_types = {r.tree_type for r in results}

        # XY → Y appears; anything else → Y is gated out. The rewired call
        # path must agree with a direct service call.
        if direct_sex == "XY":
            assert "Y" in gated_tree_types
        else:
            assert "Y" not in gated_tree_types
