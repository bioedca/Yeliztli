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
    _check_node_match,
    _classify_node_match,
    _collect_rsids,
    _parse_tree_node,
    _tree_walk,
    assign_haplogroups,
    load_haplogroup_bundle,
    run_haplogroup_assignment,
    store_haplogroup_findings,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
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
# PAR1 (ends at 2,699,520) and before PAR2 (starts at 154,931,044). Two
# rows is enough — the algorithm requires "≥1 non-PAR chrX typed and every
# typed call homozygous", and these two rows leave the legacy ``Y count >
# 0`` heuristic with the same result (no chrY signal needed for the
# legacy gate; the algorithm only reads chrX here).
_NONPAR_X_HOM_GENOTYPES = [
    {"rsid": "rs_haplo_x_hom_1", "chrom": "X", "pos": 50_000_001, "genotype": "AA"},
    {"rsid": "rs_haplo_x_hom_2", "chrom": "X", "pos": 50_000_002, "genotype": "GG"},
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


def _seed_mt_h1a(engine: sa.Engine) -> None:
    """Seed H1a mtDNA genotypes into raw_variants."""
    with engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), _H1A_GENOTYPES)


def _seed_both(engine: sa.Engine) -> None:
    """Seed mt H1a, Y R1b1a, and the chrX hom evidence the sex-inference
    service needs to classify the sample as XY (Plan §9.4)."""
    all_rows = _H1A_GENOTYPES + _R1B1A_GENOTYPES + _NONPAR_X_HOM_GENOTYPES
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


class TestCheckNodeMatch:
    """Test defining SNP matching logic."""

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
        present, total = _check_node_match(node, genotypes)
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
        present, total = _check_node_match(node, genotypes)
        assert present == 1
        assert total == 2

    def test_missing_genotype(self) -> None:
        node = HaplogroupNode(
            haplogroup="H",
            defining_snps=[HaplogroupSNP("rs1", 100, "A")],
            children=[],
        )
        genotypes = {}  # no data
        present, total = _check_node_match(node, genotypes)
        assert present == 0
        assert total == 1

    def test_no_call_genotype(self) -> None:
        node = HaplogroupNode(
            haplogroup="H",
            defining_snps=[HaplogroupSNP("rs1", 100, "A")],
            children=[],
        )
        genotypes = {"rs1": "--"}
        present, total = _check_node_match(node, genotypes)
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
        present, total = _check_node_match(node, genotypes)
        assert present == 1
        assert total == 1

    def test_empty_defining_snps(self) -> None:
        node = HaplogroupNode(haplogroup="root", defining_snps=[], children=[])
        present, total = _check_node_match(node, {})
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
        """T3-33: Confidence equals defining_snps_present / defining_snps_total."""
        _seed_mt_h1a(sample_engine)
        results = assign_haplogroups(bundle, sample_engine)

        mt = results[0]
        expected_confidence = mt.defining_snps_present / mt.defining_snps_total
        assert mt.confidence == round(expected_confidence, 4)
        assert mt.defining_snps_present > 0
        assert mt.defining_snps_total > 0

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


# Heterozygous non-PAR chrX call with no chrY evidence → XX under the
# Plan §9.4 algorithm.
_XX_CHROM_X_HET = [
    {"rsid": "rs_xx_x_het_1", "chrom": "X", "pos": 50_000_001, "genotype": "AG"},
    {"rsid": "rs_xx_x_hom_1", "chrom": "X", "pos": 50_000_002, "genotype": "GG"},
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
            conn.execute(sa.insert(raw_variants), _H1A_GENOTYPES + _XX_CHROM_X_HET)

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
