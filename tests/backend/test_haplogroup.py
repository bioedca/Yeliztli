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
    # L3 defining SNPs
    {"rsid": "i5000769", "chrom": "MT", "pos": 769, "genotype": "GG"},
    {"rsid": "i5001018", "chrom": "MT", "pos": 1018, "genotype": "AA"},
    {"rsid": "i5016311", "chrom": "MT", "pos": 16311, "genotype": "CC"},
    # N defining SNPs
    {"rsid": "i5008701", "chrom": "MT", "pos": 8701, "genotype": "GG"},
    {"rsid": "i5009540", "chrom": "MT", "pos": 9540, "genotype": "CC"},
    {"rsid": "rs1000318", "chrom": "MT", "pos": 10740, "genotype": "TT"},
    {"rsid": "i5010873", "chrom": "MT", "pos": 10873, "genotype": "CC"},
    {"rsid": "i5015301", "chrom": "MT", "pos": 15301, "genotype": "AA"},
    # R defining SNPs
    {"rsid": "i5012705", "chrom": "MT", "pos": 12705, "genotype": "CC"},
    {"rsid": "rs1000622", "chrom": "MT", "pos": 13824, "genotype": "TT"},
    # R0 defining SNPs
    {"rsid": "i5000073", "chrom": "MT", "pos": 73, "genotype": "GG"},
    # HV defining SNPs
    {"rsid": "i5014766", "chrom": "MT", "pos": 14766, "genotype": "TT"},
    # H defining SNPs
    {"rsid": "i5002706", "chrom": "MT", "pos": 2706, "genotype": "GG"},
    {"rsid": "rs1000687", "chrom": "MT", "pos": 13252, "genotype": "TT"},
    # H1 defining SNPs
    {"rsid": "i5003010", "chrom": "MT", "pos": 3010, "genotype": "AA"},
    # H1a defining SNPs
    {"rsid": "rs1000390", "chrom": "MT", "pos": 13290, "genotype": "TT"},
    {"rsid": "i5013404", "chrom": "MT", "pos": 13404, "genotype": "CC"},
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

# Known genotype fixture for R1b1a path in Y-chromosome:
# Y-Adam → CT → F → K → K2 → P → R → R1 → R1b → R1b1 → R1b1a
_R1B1A_GENOTYPES = [
    # CT
    {"rsid": "rs2032652", "chrom": "Y", "pos": 21869271, "genotype": "TT"},
    {"rsid": "rs13304168", "chrom": "Y", "pos": 23058920, "genotype": "GG"},
    # F
    {"rsid": "rs3900", "chrom": "Y", "pos": 14413839, "genotype": "CC"},
    # K
    {"rsid": "rs2032631", "chrom": "Y", "pos": 14416951, "genotype": "CC"},
    # K2 — rs3900 already included above
    # P
    {"rsid": "rs1000147", "chrom": "Y", "pos": 41031901, "genotype": "AA"},
    # R
    {"rsid": "rs2032658", "chrom": "Y", "pos": 15025620, "genotype": "AA"},
    {"rsid": "rs1000546", "chrom": "Y", "pos": 36452173, "genotype": "TT"},
    # R1
    {"rsid": "rs2032624", "chrom": "Y", "pos": 15022755, "genotype": "AA"},
    {"rsid": "rs1000867", "chrom": "Y", "pos": 32170896, "genotype": "TT"},
    # R1b
    {"rsid": "rs9786184", "chrom": "Y", "pos": 2887824, "genotype": "AA"},
    {"rsid": "rs1000331", "chrom": "Y", "pos": 20085901, "genotype": "TT"},
    # R1b1
    {"rsid": "rs1000247", "chrom": "Y", "pos": 20503721, "genotype": "AA"},
    # R1b1a
    {"rsid": "rs9461019", "chrom": "Y", "pos": 22741842, "genotype": "TT"},
    {"rsid": "rs1000154", "chrom": "Y", "pos": 39970128, "genotype": "GG"},
]

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
    {"rsid": "rs2032597", "chrom": "Y", "pos": 2832640, "genotype": "AA"},
    # CT (M168 + rs13304168)
    {"rsid": "rs2032652", "chrom": "Y", "pos": 21869271, "genotype": "TT"},
    {"rsid": "rs13304168", "chrom": "Y", "pos": 23058920, "genotype": "GG"},
    # C
    {"rsid": "rs35284970", "chrom": "Y", "pos": 2723523, "genotype": "CC"},
    {"rsid": "rs2032666", "chrom": "Y", "pos": 7701164, "genotype": "CC"},
    {"rsid": "rs17250625", "chrom": "Y", "pos": 8459804, "genotype": "AA"},
    # C2
    {"rsid": "rs3916762", "chrom": "Y", "pos": 2720073, "genotype": "TT"},
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
        assert bundle.version == "1.0.0"
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


class TestTreeWalkSharedAncestralMarkers:
    """#804: descent must rest on clade-*specific* derived markers, so a child
    that merely re-lists a marker inherited from its parent clade cannot divert or
    over-extend the walk — while a structural pass-through node (one defined solely
    by inherited markers) stays transparent to a deeper, supported clade."""

    def test_real_bundle_only_ct_markers_stop_at_ct(self, bundle: HaplogroupBundle) -> None:
        """The reproduced #804 case on the real Y bundle: a sample with only the two
        shared CT markers typed (M168 ``rs2032652`` + ``rs13304168``) must stop at
        CT — not over-resolve into DE (re-lists ``rs13304168``) or F (re-lists
        ``rs2032652``), which provide no clade-specific evidence."""
        genotypes = {"rs2032652": "TT", "rs13304168": "GG"}  # CT derived; no DE/F-specific marker
        terminal, path = _tree_walk(bundle.y_tree, genotypes, [])
        assert terminal.haplogroup == "CT"
        assert [s.haplogroup for s in path] == ["CT"]

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
        """A node defined only by an inherited marker (the K2 = only F's rs3900
        shape) is transparent: the walk descends through it to a deeper clade that
        has its own derived evidence."""
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

    def test_confidence_calculation(
        self, bundle: HaplogroupBundle, sample_engine: sa.Engine
    ) -> None:
        """T3-33: confidence = defining_snps_present / defining_snps_total, pinned
        to INDEPENDENTLY-derived literals (#640).

        The ``_seed_mt_h1a`` path is deterministic — mt-MRCA → L3 → N → R → R0 →
        HV → H → H1 → H1a, with 3 + 5 + 2 + 1 + 1 + 2 + 1 + 2 = 17 defining SNPs,
        all 17 derived in the fixture — so the expected present/total/confidence
        are knowable offline (17 / 17 → 1.0). Asserting those literals, rather
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
        assert mt.defining_snps_present == 17
        assert mt.defining_snps_total == 17
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
            assert step.snps_total > 0

    def test_empty_sample(self, bundle: HaplogroupBundle, sample_engine: sa.Engine) -> None:
        """Empty sample returns mt-MRCA (root) with empty traversal path."""
        results = assign_haplogroups(bundle, sample_engine)

        assert len(results) == 1
        mt = results[0]
        assert mt.haplogroup == "mt-MRCA"
        assert len(mt.traversal_path) == 0


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
    """Regression for #660: a CT/M168+ male must not be mis-assigned to A1b.

    The proximate cause was a mis-polarized A-clade marker: ``rs2032597`` is
    ``ref/ancestral=A``, ``alt/derived=C`` (Ensembl: ancestral_allele=A; SPDI
    NC_000024.10:…:A:C), but the bundle encoded the A/A1 nodes' derived state as
    the *ancestral* allele ``A``. Because ``A`` is the common state in every
    non-A lineage, every non-A man false-matched haplogroup A, and M168
    (``rs2032652``, a CT-defining marker) erroneously listed under A1b let the
    walk reach the basal-African A1b. These tests pin all three fixes.
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

    def test_a_node_rs2032597_polarity_in_real_bundle(self, bundle: HaplogroupBundle) -> None:
        """The A/A1 nodes' rs2032597 derived allele is C (alt), so the ancestral
        A is a *conflict* (evidence against A), not a match."""
        for name in ("A", "A1"):
            node = _find_y_node(bundle.y_tree, name)
            assert node is not None, f"{name} node missing from bundle"
            snp = next(s for s in node.defining_snps if s.rsid == "rs2032597")
            assert snp.allele == "C", f"{name} rs2032597 derived allele must be C (alt)"

            # Ancestral A → conflicting; derived C → present.
            present, conflicting, _ = _classify_node_match(node, {"rs2032597": "A"})
            assert (present, conflicting) == (0, 1)
            present, conflicting, _ = _classify_node_match(node, {"rs2032597": "C"})
            assert (present, conflicting) == (1, 0)

    def test_m168_not_an_a1b_defining_marker(self, bundle: HaplogroupBundle) -> None:
        """M168 (rs2032652) defines CT, the sister clade of A — it must not appear
        under A1b (where it forced a conflict for real A1b men and let mis-routed
        non-A men reach A1b). It remains a defining marker of CT."""
        a1b = _find_y_node(bundle.y_tree, "A1b")
        assert a1b is not None
        assert "rs2032652" not in {s.rsid for s in a1b.defining_snps}

        ct = _find_y_node(bundle.y_tree, "CT")
        assert ct is not None
        assert "rs2032652" in {s.rsid for s in ct.defining_snps}


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
            categories = {r.category for r in f_rows}
            assert "haplogroup_mt" in categories
            assert "haplogroup_Y" in categories

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
