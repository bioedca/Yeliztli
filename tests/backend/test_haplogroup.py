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
from functools import lru_cache
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
from backend.analysis.zygosity import MERGE_AMBIGUITY_SENTINEL, is_no_call
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    findings,
    haplogroup_assignments,
    raw_variants,
)
from backend.services.sample_merge import MergeStrategy, _apply_semantics

# ── Paths ────────────────────────────────────────────────────────────────

BUNDLE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "haplogroup_bundle.json"
)
MT_HAPLOGROUP_SOURCE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "scripts" / "mt_haplogroup_source.json"
)
PGP_1050_MT_I_LAYOUT_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "pgp_1050_mt_i_callable_layout.json"
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
_MT_L3_TRUNK_GENOTYPES = [
    # L3's primary-array-callable Build 17 substitution. The other two direct
    # events (A769G and C16311T) are retained as non-emitted source provenance.
    {"rsid": "i5001018", "chrom": "MT", "pos": 1018, "genotype": "GG"},
]

_MT_N_TRUNK_GENOTYPES = [
    *_MT_L3_TRUNK_GENOTYPES,
    # N's sole scoreable Build 17 event is C9540T. The source events at 8701
    # and 10873 are historical-array-only, while 10398 and 15301 collide with
    # downstream reversions; all four remain provenance-only.
    {"rsid": "i5009540", "chrom": "MT", "pos": 9540, "genotype": "TT"},
]

_MT_R_TRUNK_GENOTYPES = [
    *_MT_N_TRUNK_GENOTYPES,
    # R defining SNPs (T12705C, T16223C).
    {"rsid": "i5012705", "chrom": "MT", "pos": 12705, "genotype": "CC"},
    {"rsid": "i5016223", "chrom": "MT", "pos": 16223, "genotype": "CC"},
]

_H1A_GENOTYPES = [
    *_MT_R_TRUNK_GENOTYPES,
    # HV0 sibling marker: Build 17 HV0 is T72C. A true H carrier carries the
    # rCRS base 72=T, so TT keeps this H path out of the HV0 branch (#1648).
    {"rsid": "i5000072", "chrom": "MT", "pos": 72, "genotype": "TT"},
    # H defining SNPs G2706A and T7028C. HV's direct T14766C event is retained
    # as historical-only provenance, so this fixture proves that markerless HV
    # still reaches H from H's own primary-callable evidence.
    {"rsid": "i5002706", "chrom": "MT", "pos": 2706, "genotype": "AA"},
    {"rsid": "i5007028", "chrom": "MT", "pos": 7028, "genotype": "CC"},
    # H1 defining SNPs
    {"rsid": "i5003010", "chrom": "MT", "pos": 3010, "genotype": "AA"},
    # H1a defining SNPs (A73G! is the direct H1a back mutation; A16162G is
    # its second direct Build 17 substitution).
    {"rsid": "i5000073", "chrom": "MT", "pos": 73, "genotype": "GG"},
    {"rsid": "i5016162", "chrom": "MT", "pos": 16162, "genotype": "GG"},
]

_MT_U_TRUNK_GENOTYPES = _MT_R_TRUNK_GENOTYPES + [
    {"rsid": "i5011467", "chrom": "MT", "pos": 11467, "genotype": "GG"},
    {"rsid": "i5012308", "chrom": "MT", "pos": 12308, "genotype": "GG"},
    {"rsid": "i5012372", "chrom": "MT", "pos": 12372, "genotype": "AA"},
]

_MT_M_TRUNK_GENOTYPES = [
    *_MT_L3_TRUNK_GENOTYPES,
    {"rsid": "i5014783", "chrom": "MT", "pos": 14783, "genotype": "CC"},
    {"rsid": "i5015043", "chrom": "MT", "pos": 15043, "genotype": "AA"},
]

_MT_W_TRUNK_GENOTYPES = _MT_N_TRUNK_GENOTYPES + [
    {"rsid": "i5000207", "chrom": "MT", "pos": 207, "genotype": "AA"},
    {"rsid": "i5001243", "chrom": "MT", "pos": 1243, "genotype": "CC"},
    {"rsid": "i5003505", "chrom": "MT", "pos": 3505, "genotype": "GG"},
    {"rsid": "i5005460", "chrom": "MT", "pos": 5460, "genotype": "AA"},
    {"rsid": "i5008251", "chrom": "MT", "pos": 8251, "genotype": "AA"},
    {"rsid": "i5008994", "chrom": "MT", "pos": 8994, "genotype": "AA"},
    {"rsid": "i5011947", "chrom": "MT", "pos": 11947, "genotype": "GG"},
    {"rsid": "i5015884", "chrom": "MT", "pos": 15884, "genotype": "CC"},
    {"rsid": "i5016292", "chrom": "MT", "pos": 16292, "genotype": "TT"},
]

_MT_U3_TRUNK_GENOTYPES = _MT_U_TRUNK_GENOTYPES + [
    {"rsid": "i5014139", "chrom": "MT", "pos": 14139, "genotype": "GG"},
    {"rsid": "i5015454", "chrom": "MT", "pos": 15454, "genotype": "CC"},
    {"rsid": "i5016343", "chrom": "MT", "pos": 16343, "genotype": "GG"},
]

_MT_U5B_TRUNK_GENOTYPES = _MT_U_TRUNK_GENOTYPES + [
    # U5 is an exact markerless gateway; shared U5a'b events stay source-only.
    {"rsid": "i5014182", "chrom": "MT", "pos": 14182, "genotype": "CC"},
]

_MT_U8_TRUNK_GENOTYPES = _MT_U_TRUNK_GENOTYPES + [
    {"rsid": "i5009698", "chrom": "MT", "pos": 9698, "genotype": "CC"},
]

_MT_K_GENOTYPES = _MT_U8_TRUNK_GENOTYPES + [
    # K is below the direct U8b gateway and cannot bypass m.14167T.
    {"rsid": "i5014167", "chrom": "MT", "pos": 14167, "genotype": "TT"},
    {"rsid": "i5010550", "chrom": "MT", "pos": 10550, "genotype": "GG"},
    {"rsid": "i5011299", "chrom": "MT", "pos": 11299, "genotype": "CC"},
    {"rsid": "i5014798", "chrom": "MT", "pos": 14798, "genotype": "CC"},
]

_RCRS_H2A2A1_GENOTYPES = _MT_R_TRUNK_GENOTYPES + [
    {"rsid": "i5000072", "chrom": "MT", "pos": 72, "genotype": "TT"},
    {"rsid": "i5002706", "chrom": "MT", "pos": 2706, "genotype": "AA"},
    {"rsid": "i5007028", "chrom": "MT", "pos": 7028, "genotype": "CC"},
    {"rsid": "i5001438", "chrom": "MT", "pos": 1438, "genotype": "AA"},
    {"rsid": "i5004769", "chrom": "MT", "pos": 4769, "genotype": "AA"},
    {"rsid": "i5009380", "chrom": "MT", "pos": 9380, "genotype": "GG"},
    {"rsid": "i5008860", "chrom": "MT", "pos": 8860, "genotype": "AA"},
    {"rsid": "i5000263", "chrom": "MT", "pos": 263, "genotype": "AA"},
    {"rsid": "i5000951", "chrom": "MT", "pos": 951, "genotype": "GG"},
    {"rsid": "i5015354", "chrom": "MT", "pos": 15354, "genotype": "CC"},
    {"rsid": "i5016354", "chrom": "MT", "pos": 16354, "genotype": "CC"},
]

_H2A1_SIBLING_GENOTYPES = _MT_R_TRUNK_GENOTYPES + [
    {"rsid": "i5002706", "chrom": "MT", "pos": 2706, "genotype": "AA"},
    {"rsid": "i5007028", "chrom": "MT", "pos": 7028, "genotype": "CC"},
    {"rsid": "i5001438", "chrom": "MT", "pos": 1438, "genotype": "AA"},
    {"rsid": "i5004769", "chrom": "MT", "pos": 4769, "genotype": "AA"},
    {"rsid": "i5000951", "chrom": "MT", "pos": 951, "genotype": "AA"},
    {"rsid": "i5016354", "chrom": "MT", "pos": 16354, "genotype": "TT"},
    {"rsid": "i5000750", "chrom": "MT", "pos": 750, "genotype": "GG"},
    {"rsid": "i5000263", "chrom": "MT", "pos": 263, "genotype": "GG"},
]

_HV0_GENOTYPES = _MT_R_TRUNK_GENOTYPES + [
    {"rsid": "i5000072", "chrom": "MT", "pos": 72, "genotype": "CC"},
]

_MT_N1_REVERSAL_GENOTYPES = _MT_N_TRUNK_GENOTYPES + [
    {"rsid": "i5001719", "chrom": "MT", "pos": 1719, "genotype": "AA"},
    {"rsid": "i5010238", "chrom": "MT", "pos": 10238, "genotype": "CC"},
    {"rsid": "i5012501", "chrom": "MT", "pos": 12501, "genotype": "AA"},
    # N omits its source m.10398A event so this downstream reversal remains
    # reachable without a hard ancestor conflict.
    {"rsid": "i5010398", "chrom": "MT", "pos": 10398, "genotype": "GG"},
]

_MT_N1A_GENOTYPES = _MT_N1_REVERSAL_GENOTYPES + [
    {"rsid": "i5000204", "chrom": "MT", "pos": 204, "genotype": "CC"},
    {"rsid": "i5013780", "chrom": "MT", "pos": 13780, "genotype": "GG"},
]

_MT_I_GENOTYPES = _MT_N1A_GENOTYPES + [
    {"rsid": "i5010034", "chrom": "MT", "pos": 10034, "genotype": "CC"},
    {"rsid": "i5015043", "chrom": "MT", "pos": 15043, "genotype": "AA"},
    {"rsid": "i5016129", "chrom": "MT", "pos": 16129, "genotype": "AA"},
]

_MT_B_REVERSAL_GENOTYPES = _MT_R_TRUNK_GENOTYPES + [
    {"rsid": "i5008584", "chrom": "MT", "pos": 8584, "genotype": "AA"},
    {"rsid": "i5009950", "chrom": "MT", "pos": 9950, "genotype": "CC"},
    # N omits its source m.10398A event so the B5 back mutation remains
    # reachable through markerless B without a hard ancestor conflict.
    {"rsid": "i5010398", "chrom": "MT", "pos": 10398, "genotype": "GG"},
    {"rsid": "i5016140", "chrom": "MT", "pos": 16140, "genotype": "CC"},
]

_MT_J_REVERSAL_GENOTYPES = _MT_R_TRUNK_GENOTYPES + [
    {"rsid": "i5011251", "chrom": "MT", "pos": 11251, "genotype": "GG"},
    {"rsid": "i5015452", "chrom": "MT", "pos": 15452, "genotype": "AA"},
    {"rsid": "i5016126", "chrom": "MT", "pos": 16126, "genotype": "CC"},
    {"rsid": "i5000295", "chrom": "MT", "pos": 295, "genotype": "TT"},
    {"rsid": "i5000489", "chrom": "MT", "pos": 489, "genotype": "CC"},
    {"rsid": "i5010398", "chrom": "MT", "pos": 10398, "genotype": "GG"},
    {"rsid": "i5012612", "chrom": "MT", "pos": 12612, "genotype": "GG"},
    {"rsid": "i5013708", "chrom": "MT", "pos": 13708, "genotype": "AA"},
    {"rsid": "i5016069", "chrom": "MT", "pos": 16069, "genotype": "TT"},
]

_MT_K1_REVERSAL_GENOTYPES = _MT_K_GENOTYPES + [
    {"rsid": "i5001189", "chrom": "MT", "pos": 1189, "genotype": "CC"},
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


@lru_cache(maxsize=1)
def _mt_tree_json() -> dict:
    """Load the generated mtDNA tree once for path-derived test fixtures."""
    return json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))["trees"]["mt"]


def _derived_mt_path_genotypes(target: str) -> list[dict[str, object]]:
    """Build derived calls for one emitted mtDNA path from the generated bundle."""
    tree = _mt_tree_json()

    def find_path(node: dict) -> list[dict] | None:
        if node["haplogroup"] == target:
            return [node]
        for child in node.get("children", []):
            path = find_path(child)
            if path is not None:
                return [node, *path]
        return None

    path = find_path(tree)
    assert path is not None, f"mtDNA test target {target} is absent from the generated bundle"
    return [
        {
            "rsid": snp["rsid"],
            "chrom": "MT",
            "pos": snp["pos"],
            "genotype": snp["allele"] * 2,
        }
        for node in path
        for snp in node["defining_snps"]
    ]


_ROOT_L0_BATCH_DIRECT_POSITIONS = {
    "L0": (1048, 3516, 5442, 6185, 9347, 10589, 12007, 12720),
    "L0a": (5231, 5460, 11176, 14308),
    "L0a1": (5096,),
    "L0a2": (64, 5147, 5711, 6257, 8460, 11172, 16129),
    "L0b": (6719, 15106, 15622, 16051, 16164),
    "L0d": (1438, 4232, 8152, 8251, 12121, 15466, 15930, 15941, 16243),
    "L0d1": (719, 2706, 3438, 6266, 13759),
    "L0d2": (3981, 4025, 4044, 7154, 11854, 15766),
    "L0f": (207, 4964, 9581, 9620, 13470, 14109, 15852, 16169, 16327),
    "L0k": (
        199,
        850,
        1243,
        4541,
        4907,
        5811,
        8911,
        8994,
        9136,
        10499,
        10920,
        11299,
        11653,
        13590,
        13928,
        14020,
        14182,
        14371,
        16129,
        16291,
    ),
    "L1": (3666, 7389, 13789, 14178, 14560),
    "L2": (2416, 8206, 9221, 10115, 13590, 16390),
    "L3": (1018,),
    "L4": (5460, 16362),
    "L5": (3423, 7972, 12950, 16148),
    "L6": (
        146,
        961,
        1461,
        4964,
        5267,
        6002,
        6284,
        9332,
        10978,
        11116,
        12771,
        13710,
        15244,
        15289,
        16048,
    ),
}

_ROOT_L0_BATCH_EXPECTED_STEPS = {
    "L0": (("L0", 8, 8),),
    "L0a": (("L0", 8, 8), ("L0a", 4, 4)),
    "L0a1": (("L0", 8, 8), ("L0a", 4, 4), ("L0a1", 1, 1)),
    "L0a2": (("L0", 8, 8), ("L0a", 4, 4), ("L0a2", 7, 7)),
    "L0b": (("L0", 8, 8), ("L0b", 5, 5)),
    "L0d": (("L0", 8, 8), ("L0d", 9, 9)),
    "L0d1": (("L0", 8, 8), ("L0d", 9, 9), ("L0d1", 5, 5)),
    "L0d2": (("L0", 8, 8), ("L0d", 9, 9), ("L0d2", 6, 6)),
    "L0f": (("L0", 8, 8), ("L0f", 9, 9)),
    "L0k": (("L0", 8, 8), ("L0k", 20, 20)),
    "L1": (("L1", 5, 5),),
    "L2": (("L2", 6, 6),),
    "L3": (("L3", 1, 1),),
    "L4": (("L4", 2, 2),),
    "L5": (("L5", 4, 4),),
    "L6": (("L6", 15, 15),),
}

_ISSUE_1798_BATCH02_EXPECTED_PATHS = {
    "L1b": ("L1", "L1b"),
    "L1b1": ("L1", "L1b", "L1b1"),
    "L1b2": ("L1", "L1b", "L1b2"),
    "L1c": ("L1", "L1c"),
    "L1c1": ("L1", "L1c", "L1c1"),
    "L1c2": ("L1", "L1c", "L1c2"),
    "L1c3": ("L1", "L1c", "L1c3"),
    "L2a": ("L2", "L2a"),
    "L2a1": ("L2", "L2a", "L2a1"),
    "L2a2": ("L2", "L2a", "L2a2"),
    "L2b": ("L2", "L2b"),
    "L2b1": ("L2", "L2b", "L2b1"),
    "L2c": ("L2", "L2c"),
    "L2d": ("L2", "L2d"),
    "L2e": ("L2", "L2e"),
    "L4a": ("L4", "L4a"),
    "L4b": ("L4", "L4b"),
    "L5a": ("L5", "L5a"),
    "L5b": ("L5", "L5b"),
}

_ISSUE_1798_BATCH02_CONFLICTS = {
    "L1b": (710, "T"),
    "L1b1": (5036, "A"),
    "L1b2": (13893, "A"),
    "L1c": (5951, "A"),
    "L1c1": (3796, "A"),
    "L1c2": (6150, "G"),
    "L1c3": (6221, "T"),
    "L2a": (7175, "T"),
    "L2a1": (182, "T"),
    "L2a2": (9932, "G"),
    "L2b": (1706, "C"),
    "L2b1": (418, "C"),
    "L2c": (93, "A"),
    "L2d": (182, "T"),
    "L2e": (719, "G"),
    "L4a": (3357, "G"),
    "L4b": (3918, "G"),
    "L5a": (851, "A"),
    "L5b": (182, "T"),
}

_ISSUE_1798_BATCH02_OLD_MARKERS = {
    "L1b": ((6185, "C"), (10115, "C"), (16126, "C")),
    "L1b1": ((5393, "T"), (12950, "G")),
    "L1b2": ((6446, "G"), (14869, "A")),
    "L1c": ((1048, "T"), (9072, "G"), (16129, "C")),
    "L1c1": ((3483, "T"), (7859, "C")),
    "L1c2": ((8655, "T"), (13404, "C")),
    "L1c3": ((9947, "A"), (15452, "A")),
    "L2a": ((3594, "C"), (5836, "G"), (13803, "G")),
    "L2a1": ((3918, "A"), (11914, "A"), (15784, "C")),
    "L2a2": ((4158, "C"), (10688, "A")),
    "L2b": ((1227, "A"), (6680, "C")),
    "L2b1": ((6722, "G"), (14769, "G")),
    "L2d": ((1442, "A"), (6293, "C")),
    "L2e": ((3200, "A"), (8404, "T")),
    "L4a": ((7424, "A"), (14401, "C")),
    "L4b": ((2626, "C"), (10289, "G")),
    "L5a": ((7055, "G"),),
    "L5b": ((11002, "G"),),
}

_ISSUE_1798_BATCH02_FLATTENED_PREFIXES = {
    "L1b2'3": ("L1b", ((195, "T"),)),
    "L1c1'2'4'5'6": ("L1c", ((297, "G"),)),
    "L1c1'2'4'6": ("L1c", ((198, "T"), (10321, "C"))),
    "L1c2'4": ("L1c", ((12049, "T"), (13149, "G"))),
    "L2a'b'c'd": ("L2", ((195, "C"), (11944, "C"))),
    "L2a1'2'3'4": (
        "L2a",
        (
            (2789, "T"),
            (7274, "T"),
            (7771, "G"),
            (11914, "A"),
            (13803, "G"),
            (14566, "G"),
            (16294, "T"),
        ),
    ),
    "L2a2'3'4": (
        "L2a",
        ((146, "T"), (6752, "G"), (16189, "C"), (16229, "C"), (16311, "C")),
    ),
    "L2a2'3": ("L2a", ((709, "A"), (15939, "T"), (16291, "T"))),
    "L2b'c'd": ("L2", ((2332, "T"),)),
    "L2b'c": (
        "L2",
        ((198, "T"), (1442, "A"), (7624, "A"), (12236, "A"), (15110, "A"), (15217, "A")),
    ),
}

_ISSUE_1798_BATCH03_EXPECTED_PATHS = {
    "L3a": ("L3", "L3a"),
    "L3b": ("L3", "L3b"),
    "L3b1": ("L3", "L3b", "L3b1"),
    "L3d": ("L3", "L3d"),
    "L3e": ("L3", "L3e"),
    "L3e1": ("L3", "L3e", "L3e1"),
    "L3e2": ("L3", "L3e", "L3e2"),
    "L3f": ("L3", "L3f"),
    "M": ("L3", "M"),
    "D": ("L3", "M", "D"),
    "G": ("L3", "M", "G"),
    "M1": ("L3", "M", "M1"),
    "M7": ("L3", "M", "M7"),
    "M8": ("L3", "M", "M8"),
    "M9": ("L3", "M", "M9"),
}

_ISSUE_1798_BATCH03_CONFLICTS = {
    "L3a": (12816, "C"),
    "L3b": (3450, "C"),
    "L3b1": (10373, "G"),
    "L3d": (5147, "G"),
    "L3e": (14212, "T"),
    "L3e1": (6221, "T"),
    "L3e2": (14905, "G"),
    "L3f": (3396, "T"),
    "M": (14783, "T"),
    "D": (16362, "T"),
    "G": (4833, "A"),
    "M1": (6446, "G"),
    "M7": (6455, "C"),
    "M8": (4715, "A"),
    "M9": (4491, "G"),
}

# These eight legacy sets no longer meet their replacement's runtime threshold.
# G and M9 deliberately remain outside this negative matrix because their old
# sets retain exactly half of the new valid markers. M7's old set retains its
# sole safely emitted marker while m.9824 is temporarily omitted for its pending
# child conflict. All three therefore still satisfy the caller's documented
# >= 0.5 partial-coverage policy.
_ISSUE_1798_BATCH03_RUNTIME_OLD_MARKERS = {
    "L3a": ((4386, "C"), (10086, "G")),
    "L3b": ((2352, "C"), (10143, "A")),
    "L3b1": ((6221, "C"), (12049, "A")),
    "L3d": ((8618, "C"), (15514, "C")),
    "L3e": ((2352, "C"), (14905, "A")),
    "L3e1": ((3675, "A"), (9554, "A")),
    "L3e2": ((2352, "C"), (5261, "A")),
    "L3f": ((4218, "C"), (15670, "C")),
}

_ISSUE_1798_BATCH03_FLATTENED_PREFIXES = {
    "L3c'd": ("L3", ((152, "C"), (13105, "G"))),
    "L3e'i'k'x": ("L3", ((150, "T"), (10819, "G"))),
    "M1'20'51": ("M", ((14110, "C"),)),
    "M12'G": ("M", ((14569, "A"),)),
    "M80'D": ("M", ((4883, "T"),)),
}

_ISSUE_1798_BATCH04_EXPECTED_PATHS = {
    "D1": ("L3", "M", "D", "D4", "D1"),
    "D2": ("L3", "M", "D", "D4", "D2"),
    "D3": ("L3", "M", "D", "D4", "D4b", "D3"),
    "D4": ("L3", "M", "D", "D4"),
    "D4a": ("L3", "M", "D", "D4", "D4a"),
    "D4b": ("L3", "M", "D", "D4", "D4b"),
    "D5": ("L3", "M", "D", "D5"),
}

_ISSUE_1798_BATCH04_CONFLICTS = {
    "D1": (2092, "C"),
    "D2": (8703, "C"),
    "D3": (722, "C"),
    "D4": (3010, "G"),
    "D4a": (3206, "C"),
    "D4b": (8020, "G"),
    "D5": (1107, "T"),
}

# These replacement motifs are disjoint from their former hand-curated sets,
# so the legacy calls provide no evidence for the migrated terminal node.
_ISSUE_1798_BATCH04_RUNTIME_OLD_MARKERS = {
    "D3": ((3394, "C"), (10181, "T")),
    "D4a": ((12026, "G"),),
    "D5": ((1048, "T"), (4883, "T")),
}

_ISSUE_1798_BATCH04_D2_FLATTENED_HELPERS = (
    (11215, "T"),  # D4e
    (9536, "T"),  # D4e1'3
    (3316, "A"),  # D4e1
    (16092, "C"),  # D4e1
)

_ISSUE_1798_BATCH04_D3_FLATTENED_HELPERS = (
    (10181, "T"),  # D4b1
    (15440, "C"),  # D4b1
    (15951, "G"),  # D4b1
    (16319, "A"),  # D4b1
    (239, "C"),  # D4b1c
    (297, "G"),  # D4b1c
    (951, "A"),  # D4b1c
)


_ISSUE_1798_BATCH05_EXPECTED_MARKERS = {
    "E": ((3027, "C"), (3705, "A"), (7598, "A"), (13626, "T"), (16390, "A")),
    "G1": ((8200, "C"), (15323, "A"), (15497, "A")),
    "G2": ((5601, "T"), (13563, "G")),
    "G2a": ((9575, "A"), (7600, "A"), (9377, "G"), (16227, "G")),
    "M7a": ((2626, "C"), (2772, "T"), (4386, "C"), (4958, "G"), (12771, "A")),
    "M7b": ((12405, "T"),),
    "M7c": ((146, "C"), (11665, "T"), (12091, "C")),
    "M8a": ((6179, "A"), (8684, "T"), (14470, "C")),
    "C": ((3552, "A"), (9545, "G"), (11914, "A"), (13263, "G"), (14318, "C")),
    "C1": ((16325, "C"),),
    "C4": ((11969, "A"), (15204, "C")),
    "C5": ((16288, "C"),),
    "Z": ((6752, "G"), (9090, "C"), (15784, "C")),
    "Z1": ((15261, "A"),),
}

_ISSUE_1798_BATCH05_EXPECTED_PATHS = {
    "E": ("L3", "M", "M9", "E"),
    "G1": ("L3", "M", "G", "G1"),
    "G2": ("L3", "M", "G", "G2"),
    "G2a": ("L3", "M", "G", "G2", "G2a"),
    "M7": ("L3", "M", "M7"),
    "M7a": ("L3", "M", "M7", "M7a"),
    "M7b": ("L3", "M", "M7", "M7b"),
    "M7c": ("L3", "M", "M7", "M7c"),
    "M8a": ("L3", "M", "M8", "M8a"),
    "C": ("L3", "M", "M8", "C"),
    "C1": ("L3", "M", "M8", "C", "C1"),
    "C4": ("L3", "M", "M8", "C", "C4"),
    "C5": ("L3", "M", "M8", "C", "C5"),
    "Z": ("L3", "M", "M8", "Z"),
    "Z1": ("L3", "M", "M8", "Z", "Z1"),
}

_ISSUE_1798_BATCH05_CONFLICTS = {
    "E": (3027, "T"),
    "G1": (8200, "T"),
    "G2": (5601, "C"),
    "G2a": (7600, "G"),
    "M7a": (2626, "T"),
    "M7b": (12405, "C"),
    "M7c": (11665, "C"),
    "M8a": (6179, "G"),
    "C": (3552, "T"),
    "C1": (16325, "T"),
    "C4": (11969, "G"),
    "C5": (16288, "T"),
    "Z": (6752, "A"),
    "Z1": (15261, "G"),
}

# These former hand-curated motifs retain fewer than half of the replacement
# node's direct substitutions. M7b is exercised separately because its old
# m.9824A call now conflicts with M7's repaired m.9824C gateway.
_ISSUE_1798_BATCH05_RUNTIME_OLD_MARKERS = {
    "E": ((7598, "A"), (12405, "T"), (14110, "C")),
    "M7a": ((4386, "C"), (8684, "T")),
    "M7c": ((3606, "G"), (11665, "T")),
    "C1": ((6026, "T"), (11969, "A"), (13263, "G")),
    "C4": ((5979, "T"), (11365, "C")),
    "C5": ((1607, "G"), (9545, "G")),
}

_ISSUE_1798_BATCH05_SOURCE_PREFIXES = {
    # This shared G2a'c substitution is intentionally only 1/4 G2a evidence.
    "G2a'c": ("G2", ((9575, "A"),)),
    # These Build 17 intermediates remain provenance-only and non-scoring.
    "M7b'c": ("M7", ((4071, "T"),)),
    "Z+152": ("Z", ((152, "C"),)),
}


_ISSUE_1798_BATCH06_EXPECTED_MARKERS = {
    "N": ((9540, "T"),),
    "A": ((235, "G"), (663, "G"), (4248, "C"), (4824, "G"), (8794, "T")),
    # m.16362C is the scoreable A+152+16362 helper carried by emitted A2.
    "A2": ((16362, "C"), (146, "C"), (8027, "A"), (12007, "A"), (16111, "T")),
    "A5": ((8563, "G"), (11536, "T")),
    "N1b": ((1598, "A"), (5471, "A"), (8251, "A"), (16176, "G"), (16390, "A")),
    "N9": ((5417, "A"),),
    "N9a": ((5231, "A"), (12372, "A"), (16261, "T")),
    "N9b": ((5147, "A"), (10607, "T"), (11016, "A"), (13183, "G"), (14893, "G")),
    "Y_mt": (
        (8392, "A"),
        (10398, "G"),
        (14178, "C"),
        (14693, "G"),
        (16126, "C"),
        (16223, "C"),
        (16231, "C"),
    ),
    "Y1": ((3834, "A"),),
    "Y2": (
        (482, "C"),
        (5147, "A"),
        (6941, "C"),
        (7859, "A"),
        (14914, "G"),
        (15244, "G"),
    ),
}

_ISSUE_1798_BATCH06_EXPECTED_PATHS = {
    "N": ("L3", "N"),
    "A": ("L3", "N", "A"),
    "A2": ("L3", "N", "A", "A2"),
    "A5": ("L3", "N", "A", "A5"),
    "N1b": ("L3", "N", "N1", "N1b"),
    "N9": ("L3", "N", "N9"),
    "N9a": ("L3", "N", "N9", "N9a"),
    "N9b": ("L3", "N", "N9", "N9b"),
    "Y_mt": ("L3", "N", "N9", "Y_mt"),
    "Y1": ("L3", "N", "N9", "Y_mt", "Y1"),
    "Y2": ("L3", "N", "N9", "Y_mt", "Y2"),
}

_ISSUE_1798_BATCH06_CONFLICTS = {
    "N": (9540, "C"),
    "A": (235, "A"),
    "A2": (146, "T"),
    "A5": (8563, "A"),
    "N1b": (1598, "G"),
    "N9": (5417, "G"),
    "N9a": (5231, "G"),
    "N9b": (5147, "G"),
    "Y_mt": (8392, "G"),
    "Y1": (3834, "G"),
    "Y2": (482, "T"),
}

# Former hand-curated states that no longer meet the replacement node's
# scoreable threshold. A retains three valid legacy calls and is therefore
# represented here only by its removed m.1736G state.
_ISSUE_1798_BATCH06_RUNTIME_OLD_MARKERS = {
    "N": ((8701, "A"), (10873, "T")),
    "A": ((1736, "G"),),
    "A2": ((8027, "A"), (16111, "T")),
    "A5": ((11884, "G"),),
    "N1b": ((6261, "A"),),
    "N9a": ((5231, "A"), (12358, "G")),
    "N9b": ((1598, "A"), (12549, "G")),
    "Y_mt": ((8392, "A"), (10398, "G"), (14178, "C")),
}

# Each tuple pins both sides of the caller's >= 0.5 missing-data boundary.
_ISSUE_1798_BATCH06_PARTIAL_COVERAGE = (
    ("A", (235, 663, 4248), "A", (3, 5)),
    ("A", (235, 663), "N", None),
    ("A2", (16362, 146, 8027), "A2", (3, 5)),
    ("A2", (16362, 146), "A", None),
    ("A5", (8563,), "A5", (1, 2)),
    ("A5", (), "A", None),
    ("N1b", (1598, 5471, 8251), "N1b", (3, 5)),
    ("N1b", (1598, 5471), "N1", None),
    ("N9a", (5231, 12372), "N9a", (2, 3)),
    ("N9a", (5231,), "N9", None),
    ("N9b", (5147, 10607, 11016), "N9b", (3, 5)),
    ("N9b", (5147, 10607), "N9", None),
    ("Y_mt", (8392, 10398, 14178, 14693), "Y_mt", (4, 7)),
    ("Y_mt", (8392, 10398, 14178), "N9", None),
    ("Y2", (482, 5147, 6941), "Y2", (3, 6)),
    ("Y2", (482, 5147), "Y_mt", None),
)


_ISSUE_1798_BATCH07_TREE = {
    "S": ("N", ((8404, "C"),)),
    "S1": ("S", ((14384, "C"), (16075, "C"))),
    "S2": ("S", ((2380, "T"), (3438, "A"), (6167, "C"))),
    "W": (
        "N",
        (
            (207, "A"),
            (1243, "C"),
            (3505, "G"),
            (5460, "A"),
            (8251, "A"),
            (8994, "A"),
            (11947, "G"),
            (15884, "C"),
            (16292, "T"),
        ),
    ),
    "W1": ("W", ((7864, "T"),)),
    "W3": ("W", ((1406, "C"),)),
    "X": ("N", ((6221, "C"), (6371, "T"), (13966, "G"), (14470, "C"))),
    "X1": ("X", ((5302, "C"), (15654, "C"), (16104, "T"))),
    "X2": ("X", ((1719, "A"),)),
    "X2a": ("X2", ((8913, "G"), (14502, "C"))),
    "X2b": ("X2", ((8393, "T"),)),
}


_ISSUE_1798_BATCH08_TREE = {
    "R": ("N", ((12705, "C"), (16223, "C"))),
    "R0": ("R", ()),
    "HV": ("R0", ()),
    "H": ("HV", ((2706, "A"), (7028, "C"))),
    "H1": ("H", ((3010, "A"),)),
    "H2": ("H", ((1438, "A"),)),
    "H3": ("H", ((6776, "C"),)),
    "H4": ("H", ((3992, "T"), (5004, "C"))),
    "H5": ("H", ()),
    "H5a": ("H5", ((4336, "C"),)),
    "H6": ("H", ((239, "C"), (16362, "C"), (16482, "G"))),
    "H7": ("H", ((4793, "G"),)),
    "H10": ("H", ((14470, "A"),)),
    "H11": ("H", ((8448, "C"), (13759, "A"))),
    "H13": ("H", ((14872, "T"),)),
}


_ISSUE_1798_BATCH09_TREE = {
    "H1a": ("H1", ((73, "G"), (16162, "G"))),
    "H1a1": ("H1a", ((6365, "C"),)),
    "H1b": ("H1", ((16356, "C"),)),
    "H1c": ("H1", ((477, "C"),)),
    "H1e": ("H1", ((5460, "A"),)),
    "H2a": ("H2", ((4769, "A"),)),
    "H2a1": ("H2a", ((951, "A"), (16354, "T"))),
    "H2a2": ("H2a", ()),
    "H2a2a": ("H2a2", ((8860, "A"),)),
    "H2a2a1": ("H2a2a", ((263, "A"),)),
    "H5a": ("H5", ((4336, "C"),)),
    "H6a": ("H6", ((3915, "A"),)),
    "H13a": ("H13", ((2259, "T"),)),
}


_ISSUE_1798_BATCH10_TREE = {
    "HV0": ("HV", ((72, "C"),)),
    "HV1": ("HV", ((8014, "T"), (16067, "T"))),
    "V": ("HV0", ((4580, "A"),)),
    "V1": ("V", ((8869, "G"),)),
    "V7": ("V", ((93, "G"), (7444, "A"))),
    "B": ("R", ()),
    "B4": ("B", ((16217, "C"),)),
    "B4a": ("B4", ((5465, "C"),)),
    "B4b": ("B4", ((499, "A"), (4820, "A"), (13590, "A"))),
    "B4c": ("B4", ((1119, "C"), (15346, "A"))),
    "B5": ("B", ((8584, "A"), (9950, "C"), (10398, "G"), (16140, "C"))),
    "F": ("R", ((6392, "C"), (10310, "A"))),
    "F1": ("F", ((6962, "A"), (10609, "C"), (12882, "T"))),
    "F1a": ("F1", ((4086, "T"), (16172, "C"))),
    "F1b": (
        "F1",
        ((10976, "T"), (12633, "T"), (14476, "A"), (16232, "A"), (16249, "C")),
    ),
    "F2": (
        "F",
        ((1005, "C"), (7828, "G"), (10535, "C"), (10586, "A"), (12338, "C"), (13708, "A")),
    ),
    "P": ("R", ((15607, "G"),)),
}


_ISSUE_1798_BATCH11_TREE = {
    "JT": ("R", ((11251, "G"), (15452, "A"), (16126, "C"))),
    "J": (
        "JT",
        ((295, "T"), (489, "C"), (10398, "G"), (12612, "G"), (13708, "A"), (16069, "T")),
    ),
    "J1": ("J", ((462, "T"), (3010, "A"))),
    "J1b": ("J1", ((16145, "A"), (16222, "T"), (16261, "T"))),
    "J1c": ("J1", ((228, "A"), (14798, "C"))),
    "J1d": ("J1", ((7963, "G"),)),
    "J2": ("J", ((7476, "T"), (15257, "A"))),
    "J2a": ("J2", ((10499, "G"), (11377, "A"))),
    "J2b": ("J2", ((5633, "T"), (15812, "A"))),
    "T": (
        "JT",
        (
            (1888, "A"),
            (4917, "G"),
            (8697, "A"),
            (10463, "C"),
            (13368, "A"),
            (15607, "G"),
            (15928, "A"),
        ),
    ),
    "T1": ("T", ((12633, "A"), (16163, "G"))),
    "T1a": ("T1", ((16186, "T"),)),
    "T2": ("T", ((11812, "G"), (14233, "G"))),
    "T2a": ("T2", ((13965, "C"),)),
    "T2b": ("T2", ((5147, "A"),)),
    "T2c": ("T2", ((10822, "T"),)),
    "T2e": ("T2", ((16153, "A"),)),
    "T2f": ("T2", ((8270, "T"),)),
}


# Sparse, primary-array-shaped paths used by the Batch 11 caller regressions.
# Each internal node has exactly the minimum direct support needed by the
# caller's >= 0.5 threshold; no sibling or flattened-helper event contributes
# credit. T intentionally excludes recurrent m.15607G here so the focused
# per-node cases do not accidentally rely on P's identically encoded marker.
_ISSUE_1798_BATCH11_JT_CALLS = ((11251, "G"), (15452, "A"))
_ISSUE_1798_BATCH11_J_CALLS = _ISSUE_1798_BATCH11_JT_CALLS + (
    (295, "T"),
    (489, "C"),
    (10398, "G"),
)
_ISSUE_1798_BATCH11_J1_CALLS = _ISSUE_1798_BATCH11_J_CALLS + ((462, "T"),)
_ISSUE_1798_BATCH11_J2_CALLS = _ISSUE_1798_BATCH11_J_CALLS + ((7476, "T"),)
_ISSUE_1798_BATCH11_T_CALLS = _ISSUE_1798_BATCH11_JT_CALLS + (
    (1888, "A"),
    (4917, "G"),
    (8697, "A"),
    (10463, "C"),
)
_ISSUE_1798_BATCH11_T1_CALLS = _ISSUE_1798_BATCH11_T_CALLS + ((12633, "A"),)
_ISSUE_1798_BATCH11_T2_CALLS = _ISSUE_1798_BATCH11_T_CALLS + ((11812, "G"),)


_ISSUE_1798_BATCH11_DIRECT_CASES = {
    "JT": (_ISSUE_1798_BATCH11_JT_CALLS, ("L3", "N", "R", "JT")),
    "J": (_ISSUE_1798_BATCH11_J_CALLS, ("L3", "N", "R", "JT", "J")),
    "J1": (_ISSUE_1798_BATCH11_J1_CALLS, ("L3", "N", "R", "JT", "J", "J1")),
    "J1b": (
        _ISSUE_1798_BATCH11_J1_CALLS + ((16145, "A"), (16222, "T")),
        ("L3", "N", "R", "JT", "J", "J1", "J1b"),
    ),
    "J1c": (
        _ISSUE_1798_BATCH11_J1_CALLS + ((228, "A"),),
        ("L3", "N", "R", "JT", "J", "J1", "J1c"),
    ),
    "J1d": (
        _ISSUE_1798_BATCH11_J1_CALLS + ((7963, "G"),),
        ("L3", "N", "R", "JT", "J", "J1", "J1d"),
    ),
    "J2": (_ISSUE_1798_BATCH11_J2_CALLS, ("L3", "N", "R", "JT", "J", "J2")),
    "J2a": (
        _ISSUE_1798_BATCH11_J2_CALLS + ((10499, "G"),),
        ("L3", "N", "R", "JT", "J", "J2", "J2a"),
    ),
    "J2b": (
        _ISSUE_1798_BATCH11_J2_CALLS + ((5633, "T"),),
        ("L3", "N", "R", "JT", "J", "J2", "J2b"),
    ),
    "T": (_ISSUE_1798_BATCH11_T_CALLS, ("L3", "N", "R", "JT", "T")),
    "T1": (_ISSUE_1798_BATCH11_T1_CALLS, ("L3", "N", "R", "JT", "T", "T1")),
    "T2": (_ISSUE_1798_BATCH11_T2_CALLS, ("L3", "N", "R", "JT", "T", "T2")),
    "T2a": (
        _ISSUE_1798_BATCH11_T2_CALLS + ((13965, "C"),),
        ("L3", "N", "R", "JT", "T", "T2", "T2a"),
    ),
    "T2b": (
        _ISSUE_1798_BATCH11_T2_CALLS + ((5147, "A"),),
        ("L3", "N", "R", "JT", "T", "T2", "T2b"),
    ),
    "T2c": (
        _ISSUE_1798_BATCH11_T2_CALLS + ((10822, "T"),),
        ("L3", "N", "R", "JT", "T", "T2", "T2c"),
    ),
}


_ISSUE_1798_BATCH12_TREE = {
    "U": ("R", ((11467, "G"), (12308, "G"), (12372, "A"))),
    "U1": (
        "U",
        (
            (285, "T"),
            (12879, "C"),
            (14070, "G"),
            (15148, "A"),
            (15954, "C"),
            (16249, "C"),
        ),
    ),
    "U1a": ("U1", ((2218, "T"),)),
    "U1b": (
        "U1",
        (
            (146, "C"),
            (2387, "C"),
            (8395, "T"),
            (11566, "G"),
            (15172, "A"),
            (16111, "T"),
            (16327, "T"),
        ),
    ),
    "U2": ("U", ((16051, "G"),)),
    "U2e": (
        "U2",
        (
            (508, "G"),
            (3720, "G"),
            (5390, "G"),
            (5426, "C"),
            (6045, "T"),
            (13020, "C"),
            (13734, "C"),
            (15907, "G"),
            (16129, "C"),
            (16362, "C"),
        ),
    ),
    "U3": ("U", ((14139, "G"), (15454, "C"), (16343, "G"))),
    "U3a": ("U3", ((6518, "T"), (10506, "G"), (13934, "T"), (16390, "A"))),
    "U3b": ("U3", ((4188, "G"), (9656, "C"), (13743, "C"))),
    "U4": ("U", ((4646, "C"), (6047, "G"), (15693, "C"), (16356, "C"))),
    "U4a": ("U4", ((8818, "T"),)),
    "U4b": ("U4", ((7705, "C"),)),
    "U4c": ("U4", ((10907, "C"),)),
    "U6": ("U", ((3348, "G"), (16172, "C"))),
    "U6a": ("U6", ((7805, "A"), (14179, "G"))),
    "U7": (
        "U",
        (
            (980, "C"),
            (3741, "T"),
            (8137, "T"),
            (8684, "T"),
            (10142, "T"),
            (13500, "C"),
            (14569, "A"),
            (16309, "G"),
            (16318, "T"),
        ),
    ),
    "U8": ("U", ((9698, "C"),)),
    "U9": ("U", ((3531, "A"), (3834, "A"), (14094, "C"))),
}


_ISSUE_1798_BATCH12_U_CALLS = ((11467, "G"), (12372, "A"))
_ISSUE_1798_BATCH12_U1_CALLS = _ISSUE_1798_BATCH12_U_CALLS + (
    (285, "T"),
    (14070, "G"),
    (15148, "A"),
)
_ISSUE_1798_BATCH12_U2_CALLS = _ISSUE_1798_BATCH12_U_CALLS + ((16051, "G"),)
_ISSUE_1798_BATCH12_U3_CALLS = _ISSUE_1798_BATCH12_U_CALLS + (
    (14139, "G"),
    (15454, "C"),
)
_ISSUE_1798_BATCH12_U4_CALLS = _ISSUE_1798_BATCH12_U_CALLS + (
    (4646, "C"),
    (6047, "G"),
)
_ISSUE_1798_BATCH12_U6_CALLS = _ISSUE_1798_BATCH12_U_CALLS + ((3348, "G"),)


_ISSUE_1798_BATCH12_DIRECT_CASES = {
    "U": (_ISSUE_1798_BATCH12_U_CALLS, ("L3", "N", "R", "U")),
    "U1": (_ISSUE_1798_BATCH12_U1_CALLS, ("L3", "N", "R", "U", "U1")),
    "U1a": (
        _ISSUE_1798_BATCH12_U1_CALLS + ((2218, "T"),),
        ("L3", "N", "R", "U", "U1", "U1a"),
    ),
    "U1b": (
        _ISSUE_1798_BATCH12_U1_CALLS + ((2387, "C"), (8395, "T"), (11566, "G"), (16327, "T")),
        ("L3", "N", "R", "U", "U1", "U1b"),
    ),
    "U2": (_ISSUE_1798_BATCH12_U2_CALLS, ("L3", "N", "R", "U", "U2")),
    "U2e": (
        _ISSUE_1798_BATCH12_U2_CALLS
        + ((508, "G"), (3720, "G"), (5390, "G"), (6045, "T"), (13020, "C")),
        ("L3", "N", "R", "U", "U2", "U2e"),
    ),
    "U3": (_ISSUE_1798_BATCH12_U3_CALLS, ("L3", "N", "R", "U", "U3")),
    "U3a": (
        _ISSUE_1798_BATCH12_U3_CALLS + ((6518, "T"), (10506, "G")),
        ("L3", "N", "R", "U", "U3", "U3a"),
    ),
    "U3b": (
        _ISSUE_1798_BATCH12_U3_CALLS + ((4188, "G"), (9656, "C")),
        ("L3", "N", "R", "U", "U3", "U3b"),
    ),
    "U4": (_ISSUE_1798_BATCH12_U4_CALLS, ("L3", "N", "R", "U", "U4")),
    "U4a": (
        _ISSUE_1798_BATCH12_U4_CALLS + ((8818, "T"),),
        ("L3", "N", "R", "U", "U4", "U4a"),
    ),
    "U4b": (
        _ISSUE_1798_BATCH12_U4_CALLS + ((7705, "C"),),
        ("L3", "N", "R", "U", "U4", "U4b"),
    ),
    "U4c": (
        _ISSUE_1798_BATCH12_U4_CALLS + ((10907, "C"),),
        ("L3", "N", "R", "U", "U4", "U4c"),
    ),
    "U6": (_ISSUE_1798_BATCH12_U6_CALLS, ("L3", "N", "R", "U", "U6")),
    "U6a": (
        _ISSUE_1798_BATCH12_U6_CALLS + ((7805, "A"),),
        ("L3", "N", "R", "U", "U6", "U6a"),
    ),
    "U7": (
        _ISSUE_1798_BATCH12_U_CALLS
        + ((980, "C"), (8684, "T"), (13500, "C"), (14569, "A"), (16318, "T")),
        ("L3", "N", "R", "U", "U7"),
    ),
    "U8": (
        _ISSUE_1798_BATCH12_U_CALLS + ((9698, "C"),),
        ("L3", "N", "R", "U", "U8"),
    ),
    "U9": (
        _ISSUE_1798_BATCH12_U_CALLS + ((3531, "A"), (14094, "C")),
        ("L3", "N", "R", "U", "U9"),
    ),
}


_ISSUE_1798_BATCH12_CONFLICTS = {
    "U": (11467, "A", "R", ("L3", "N", "R")),
    "U1": (285, "C", "U", ("L3", "N", "R", "U")),
    "U1a": (2218, "C", "U1", ("L3", "N", "R", "U", "U1")),
    "U1b": (2387, "T", "U1", ("L3", "N", "R", "U", "U1")),
    "U2": (16051, "A", "U", ("L3", "N", "R", "U")),
    "U2e": (508, "A", "U2", ("L3", "N", "R", "U", "U2")),
    "U3": (14139, "A", "U", ("L3", "N", "R", "U")),
    "U3a": (6518, "C", "U3", ("L3", "N", "R", "U", "U3")),
    "U3b": (4188, "A", "U3", ("L3", "N", "R", "U", "U3")),
    "U4": (4646, "T", "U", ("L3", "N", "R", "U")),
    "U4a": (8818, "C", "U4", ("L3", "N", "R", "U", "U4")),
    "U4b": (7705, "T", "U4", ("L3", "N", "R", "U", "U4")),
    "U4c": (10907, "T", "U4", ("L3", "N", "R", "U", "U4")),
    "U6": (3348, "A", "U", ("L3", "N", "R", "U")),
    "U6a": (7805, "G", "U6", ("L3", "N", "R", "U", "U6")),
    "U7": (980, "T", "U", ("L3", "N", "R", "U")),
    "U8": (9698, "T", "U", ("L3", "N", "R", "U")),
    "U9": (3531, "G", "U", ("L3", "N", "R", "U")),
}


_ISSUE_1798_BATCH12_LAYOUT_CALLABLE_POSITIONS = {
    "pgp_4139": frozenset(
        {
            146,
            285,
            508,
            980,
            2387,
            3348,
            3531,
            3720,
            3741,
            3834,
            4188,
            4646,
            5390,
            5426,
            6045,
            6047,
            6518,
            7705,
            7805,
            8395,
            8684,
            8818,
            9656,
            9698,
            10142,
            10506,
            10907,
            11467,
            11566,
            12308,
            12372,
            13020,
            13500,
            13734,
            13743,
            13934,
            14070,
            14094,
            14139,
            14179,
            14569,
            15148,
            15454,
            15693,
            15907,
            15954,
            16051,
            16111,
            16172,
            16249,
            16318,
            16327,
            16343,
            16356,
            16362,
            16390,
        }
    ),
    "pgp_4162": frozenset(
        {
            285,
            508,
            980,
            2218,
            2387,
            3348,
            3531,
            3720,
            3741,
            3834,
            4188,
            4646,
            5390,
            5426,
            6045,
            6047,
            6518,
            7705,
            7805,
            8137,
            8395,
            8684,
            8818,
            9656,
            9698,
            10506,
            11467,
            11566,
            12308,
            12372,
            13020,
            13500,
            13734,
            13934,
            14070,
            14094,
            14139,
            14569,
            15148,
            15172,
            15454,
            15693,
            15907,
            15954,
            16051,
            16111,
            16129,
            16172,
            16327,
            16343,
            16390,
        }
    ),
    "pgp_4187": frozenset(
        {
            146,
            285,
            508,
            980,
            2387,
            3348,
            3531,
            3720,
            3834,
            4188,
            4646,
            5390,
            5426,
            6045,
            6047,
            6518,
            7705,
            7805,
            8395,
            8684,
            8818,
            9656,
            9698,
            10506,
            10907,
            11467,
            11566,
            12308,
            12372,
            12879,
            13020,
            13500,
            13734,
            13743,
            13934,
            14070,
            14094,
            14139,
            14179,
            14569,
            15148,
            15454,
            15693,
            15907,
            15954,
            16051,
            16129,
            16172,
            16249,
            16309,
            16318,
            16327,
            16343,
            16356,
            16362,
            16390,
        }
    ),
    "pgp_huA08F4D": frozenset(
        {
            146,
            285,
            508,
            980,
            2387,
            3348,
            3531,
            3720,
            3834,
            4188,
            4646,
            5390,
            5426,
            6045,
            6047,
            6518,
            7705,
            7805,
            8395,
            8684,
            8818,
            9656,
            9698,
            10506,
            10907,
            11467,
            11566,
            12308,
            12372,
            12879,
            13020,
            13500,
            13734,
            13743,
            13934,
            14070,
            14094,
            14139,
            14179,
            14569,
            15148,
            15454,
            15693,
            15907,
            15954,
            16051,
            16129,
            16249,
            16309,
            16318,
            16327,
            16343,
            16356,
            16362,
            16390,
        }
    ),
    "pgp_1050": frozenset(
        {
            146,
            285,
            508,
            980,
            2218,
            2387,
            3348,
            3531,
            3720,
            3834,
            4188,
            4646,
            5390,
            5426,
            6045,
            6047,
            6518,
            7705,
            7805,
            8395,
            8684,
            8818,
            9656,
            9698,
            10142,
            10506,
            10907,
            11467,
            12308,
            12372,
            12879,
            13020,
            13500,
            13734,
            13743,
            13934,
            14070,
            14094,
            14139,
            14179,
            14569,
            15148,
            15454,
            15693,
            15907,
            15954,
            16051,
            16111,
            16129,
            16172,
            16249,
            16309,
            16318,
            16327,
            16343,
            16356,
            16362,
            16390,
        }
    ),
}


_ISSUE_1798_BATCH12_LAYOUT_COUNTS = {
    "pgp_4139": (3, 5, 0, 6, 1, 9, 3, 4, 3, 4, 1, 1, 1, 2, 2, 7, 1, 3),
    "pgp_4162": (3, 4, 1, 6, 1, 9, 3, 4, 2, 3, 1, 1, 0, 2, 1, 6, 1, 3),
    "pgp_4187": (3, 6, 0, 5, 1, 10, 3, 4, 3, 4, 1, 1, 1, 2, 2, 6, 1, 3),
    "pgp_huA08F4D": (3, 6, 0, 5, 1, 10, 3, 4, 3, 4, 1, 1, 1, 1, 2, 6, 1, 3),
    "pgp_1050": (3, 6, 1, 5, 1, 10, 3, 4, 3, 4, 1, 1, 1, 2, 2, 7, 1, 3),
}


_ISSUE_1798_BATCH13_TREE = {
    "U5": ("U", ()),
    "U5a": ("U5", ((16256, "T"),)),
    "U5a1": ("U5a", ((15218, "G"), (16399, "G"))),
    "U5a2": ("U5a", ((16526, "A"),)),
    "U5b": ("U5", ((14182, "C"),)),
    "U5b1": ("U5b", ((5656, "G"),)),
    "U5b2": ("U5b", ((1721, "T"), (13637, "G"))),
    "U8a": ("U8", ((282, "C"), (6392, "C"), (6455, "T"), (9365, "T"), (13145, "A"))),
    "U8b": ("U8", ((14167, "T"),)),
    "K": ("U8b", ((10550, "G"), (11299, "C"), (14798, "C"))),
    "K1": ("K", ((1189, "C"), (10398, "G"))),
    "K1a": ("K1", ((497, "T"),)),
    "K1b": ("K1", ((5913, "A"),)),
    "K2": ("K", ((9716, "C"),)),
    "K2a": ("K2", ((4561, "C"),)),
    "K2b": ("K2", ((5231, "A"), (14037, "G"))),
}


_ISSUE_1798_BATCH13_PATHS = {
    "U5": ("L3", "N", "R", "U", "U5"),
    "U5a": ("L3", "N", "R", "U", "U5", "U5a"),
    "U5a1": ("L3", "N", "R", "U", "U5", "U5a", "U5a1"),
    "U5a2": ("L3", "N", "R", "U", "U5", "U5a", "U5a2"),
    "U5b": ("L3", "N", "R", "U", "U5", "U5b"),
    "U5b1": ("L3", "N", "R", "U", "U5", "U5b", "U5b1"),
    "U5b2": ("L3", "N", "R", "U", "U5", "U5b", "U5b2"),
    "U8a": ("L3", "N", "R", "U", "U8", "U8a"),
    "U8b": ("L3", "N", "R", "U", "U8", "U8b"),
    "K": ("L3", "N", "R", "U", "U8", "U8b", "K"),
    "K1": ("L3", "N", "R", "U", "U8", "U8b", "K", "K1"),
    "K1a": ("L3", "N", "R", "U", "U8", "U8b", "K", "K1", "K1a"),
    "K1b": ("L3", "N", "R", "U", "U8", "U8b", "K", "K1", "K1b"),
    "K2": ("L3", "N", "R", "U", "U8", "U8b", "K", "K2"),
    "K2a": ("L3", "N", "R", "U", "U8", "U8b", "K", "K2", "K2a"),
    "K2b": ("L3", "N", "R", "U", "U8", "U8b", "K", "K2", "K2b"),
}


_ISSUE_1798_BATCH13_U8_CALLS = _ISSUE_1798_BATCH12_U_CALLS + ((9698, "C"),)
_ISSUE_1798_BATCH13_U5A_CALLS = _ISSUE_1798_BATCH12_U_CALLS + ((16256, "T"),)
_ISSUE_1798_BATCH13_U5B_CALLS = _ISSUE_1798_BATCH12_U_CALLS + ((14182, "C"),)
_ISSUE_1798_BATCH13_U8B_CALLS = _ISSUE_1798_BATCH13_U8_CALLS + ((14167, "T"),)
_ISSUE_1798_BATCH13_K_CALLS = _ISSUE_1798_BATCH13_U8B_CALLS + (
    (10550, "G"),
    (11299, "C"),
    (14798, "C"),
)
_ISSUE_1798_BATCH13_K1_CALLS = _ISSUE_1798_BATCH13_K_CALLS + (
    (1189, "C"),
    (10398, "G"),
)
_ISSUE_1798_BATCH13_K2_CALLS = _ISSUE_1798_BATCH13_K_CALLS + ((9716, "C"),)


_ISSUE_1798_BATCH13_DIRECT_CASES = {
    "U5a": (_ISSUE_1798_BATCH13_U5A_CALLS, _ISSUE_1798_BATCH13_PATHS["U5a"]),
    "U5a1": (
        _ISSUE_1798_BATCH13_U5A_CALLS + ((15218, "G"), (16399, "G")),
        _ISSUE_1798_BATCH13_PATHS["U5a1"],
    ),
    "U5a2": (
        _ISSUE_1798_BATCH13_U5A_CALLS + ((16526, "A"),),
        _ISSUE_1798_BATCH13_PATHS["U5a2"],
    ),
    "U5b": (_ISSUE_1798_BATCH13_U5B_CALLS, _ISSUE_1798_BATCH13_PATHS["U5b"]),
    "U5b1": (
        _ISSUE_1798_BATCH13_U5B_CALLS + ((5656, "G"),),
        _ISSUE_1798_BATCH13_PATHS["U5b1"],
    ),
    "U5b2": (
        _ISSUE_1798_BATCH13_U5B_CALLS + ((1721, "T"), (13637, "G")),
        _ISSUE_1798_BATCH13_PATHS["U5b2"],
    ),
    "U8a": (
        _ISSUE_1798_BATCH13_U8_CALLS
        + ((282, "C"), (6392, "C"), (6455, "T"), (9365, "T"), (13145, "A")),
        _ISSUE_1798_BATCH13_PATHS["U8a"],
    ),
    "U8b": (_ISSUE_1798_BATCH13_U8B_CALLS, _ISSUE_1798_BATCH13_PATHS["U8b"]),
    "K": (_ISSUE_1798_BATCH13_K_CALLS, _ISSUE_1798_BATCH13_PATHS["K"]),
    "K1": (_ISSUE_1798_BATCH13_K1_CALLS, _ISSUE_1798_BATCH13_PATHS["K1"]),
    "K1a": (
        _ISSUE_1798_BATCH13_K1_CALLS + ((497, "T"),),
        _ISSUE_1798_BATCH13_PATHS["K1a"],
    ),
    "K1b": (
        _ISSUE_1798_BATCH13_K1_CALLS + ((5913, "A"),),
        _ISSUE_1798_BATCH13_PATHS["K1b"],
    ),
    "K2": (_ISSUE_1798_BATCH13_K2_CALLS, _ISSUE_1798_BATCH13_PATHS["K2"]),
    "K2a": (
        _ISSUE_1798_BATCH13_K2_CALLS + ((4561, "C"),),
        _ISSUE_1798_BATCH13_PATHS["K2a"],
    ),
    "K2b": (
        _ISSUE_1798_BATCH13_K2_CALLS + ((5231, "A"), (14037, "G")),
        _ISSUE_1798_BATCH13_PATHS["K2b"],
    ),
}


_ISSUE_1798_BATCH13_CONFLICTS = {
    "U5a": (16256, "C", "U", ("L3", "N", "R", "U")),
    "U5a1": (15218, "A", "U5a", _ISSUE_1798_BATCH13_PATHS["U5a"]),
    "U5a2": (16526, "G", "U5a", _ISSUE_1798_BATCH13_PATHS["U5a"]),
    "U5b": (14182, "T", "U", ("L3", "N", "R", "U")),
    "U5b1": (5656, "A", "U5b", _ISSUE_1798_BATCH13_PATHS["U5b"]),
    "U5b2": (1721, "C", "U5b", _ISSUE_1798_BATCH13_PATHS["U5b"]),
    "U8a": (282, "T", "U8", ("L3", "N", "R", "U", "U8")),
    "U8b": (14167, "C", "U8", ("L3", "N", "R", "U", "U8")),
    "K": (10550, "A", "U8b", _ISSUE_1798_BATCH13_PATHS["U8b"]),
    "K1": (1189, "T", "K", _ISSUE_1798_BATCH13_PATHS["K"]),
    "K1a": (497, "C", "K1", _ISSUE_1798_BATCH13_PATHS["K1"]),
    "K1b": (5913, "G", "K1", _ISSUE_1798_BATCH13_PATHS["K1"]),
    "K2": (9716, "T", "K", _ISSUE_1798_BATCH13_PATHS["K"]),
    "K2a": (4561, "T", "K2", _ISSUE_1798_BATCH13_PATHS["K2"]),
    "K2b": (5231, "G", "K2", _ISSUE_1798_BATCH13_PATHS["K2"]),
}


_ISSUE_1798_BATCH13_PRIMARY_LAYOUT_CALLABLE_POSITIONS = {
    "pgp_4139": frozenset(
        {
            282,
            497,
            1189,
            1721,
            4561,
            5231,
            5656,
            6392,
            6455,
            9365,
            9716,
            10398,
            10550,
            11299,
            13145,
            13637,
            14037,
            14167,
            14182,
            15218,
            16256,
            16399,
            16526,
        }
    ),
    "pgp_4162": frozenset(
        {
            282,
            1189,
            1721,
            4561,
            5656,
            6392,
            6455,
            9365,
            9716,
            10398,
            10550,
            11299,
            13145,
            14037,
            14167,
            14182,
            14798,
            15218,
            16256,
            16399,
            16526,
        }
    ),
    "pgp_4187": frozenset(
        {
            282,
            497,
            1189,
            1721,
            4561,
            5656,
            6392,
            6455,
            9365,
            9716,
            10398,
            10550,
            11299,
            13637,
            14037,
            14167,
            14182,
            15218,
            16256,
            16270,
            16399,
            16526,
        }
    ),
    "pgp_huA08F4D": frozenset(
        {
            282,
            497,
            1189,
            1721,
            4561,
            5231,
            5656,
            6392,
            6455,
            9365,
            9716,
            10398,
            10550,
            11299,
            13637,
            14037,
            14167,
            14182,
            15218,
            16256,
            16270,
            16399,
            16526,
        }
    ),
}


_ISSUE_1798_BATCH13_PRIMARY_LAYOUT_COUNTS = {
    "pgp_4139": (0, 1, 2, 1, 1, 1, 2, 5, 1, 2, 2, 1, 0, 1, 1, 2),
    "pgp_4162": (0, 1, 2, 1, 1, 1, 1, 5, 1, 3, 2, 0, 0, 1, 1, 1),
    "pgp_4187": (0, 1, 2, 1, 1, 1, 2, 4, 1, 2, 2, 1, 0, 1, 1, 1),
    "pgp_huA08F4D": (0, 1, 2, 1, 1, 1, 2, 4, 1, 2, 2, 1, 0, 1, 1, 2),
}


_W_DIRECT_POSITION_GENOTYPES = [
    {"pos": row["pos"], "genotype": row["genotype"]}
    for row in _MT_W_TRUNK_GENOTYPES[len(_MT_N_TRUNK_GENOTYPES) :]
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
        assert bundle.version == "1.1.30"
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
        assert bundle.y_trusted_missing_internal_passthrough_rsids == frozenset(
            {"rs2032599", "rs2033003"}
        )
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

    def test_equal_fraction_picks_child_with_more_derived_support(self) -> None:
        """Equal fractions use absolute support instead of child order."""
        root = HaplogroupNode(
            haplogroup="root",
            defining_snps=[],
            children=[
                HaplogroupNode(
                    haplogroup="P",
                    defining_snps=[HaplogroupSNP("rs_p", 1, "G")],
                    children=[],
                ),
                HaplogroupNode(
                    haplogroup="JT",
                    defining_snps=[
                        HaplogroupSNP("rs_jt_1", 2, "A"),
                        HaplogroupSNP("rs_jt_2", 3, "C"),
                        HaplogroupSNP("rs_jt_3", 4, "T"),
                    ],
                    children=[],
                ),
            ],
        )
        genotypes = {
            "rs_p": "GG",
            "rs_jt_1": "AA",
            "rs_jt_2": "CC",
            "rs_jt_3": "TT",
        }

        terminal, path = _tree_walk(root, genotypes, [])

        assert terminal.haplogroup == "JT"
        assert [step.haplogroup for step in path] == ["JT"]

    def test_fraction_precedes_larger_absolute_support(self) -> None:
        """A higher fraction still wins when the lower fraction has more matches."""
        root = HaplogroupNode(
            haplogroup="root",
            defining_snps=[],
            children=[
                HaplogroupNode(
                    haplogroup="B",
                    defining_snps=[
                        HaplogroupSNP("rs_b_1", 1, "G"),
                        HaplogroupSNP("rs_b_2", 2, "T"),
                        HaplogroupSNP("rs_b_3", 3, "C"),
                        HaplogroupSNP("rs_b_4", 4, "A"),
                    ],
                    children=[],
                ),
                HaplogroupNode(
                    haplogroup="A",
                    defining_snps=[HaplogroupSNP("rs_a", 5, "G")],
                    children=[],
                ),
            ],
        )
        genotypes = {
            "rs_b_1": "GG",
            "rs_b_2": "TT",
            "rs_b_3": "CC",
            "rs_a": "GG",
        }

        terminal, path = _tree_walk(root, genotypes, [])

        assert terminal.haplogroup == "A"
        assert [step.haplogroup for step in path] == ["A"]

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

    def test_issue_1798_m11176_is_owned_by_l0a_not_l0k(self, bundle: HaplogroupBundle) -> None:
        """The flattened L0a'g marker is attached only to its L0a descendant."""
        l0a = _find_mt_node(bundle.mt_tree, "L0a")
        l0k = _find_mt_node(bundle.mt_tree, "L0k")
        assert l0a is not None
        assert l0k is not None
        assert _mt_snp_map(l0a)[11176] == "A"
        assert 11176 not in _mt_snp_map(l0k)

    def test_true_h_carrier_reaches_h_and_ancestral_is_blocked(
        self, bundle: HaplogroupBundle
    ) -> None:
        """Batch 08 scores H on its two direct markers below markerless HV."""
        trunk = {row["rsid"]: row["genotype"] for row in _MT_R_TRUNK_GENOTYPES}
        derived = {**trunk, "i5002706": "AA", "i5007028": "CC"}
        ancestral = {**trunk, "i5002706": "GG", "i5007028": "TT"}

        derived_terminal, derived_path = _tree_walk(bundle.mt_tree, derived, [])
        assert derived_terminal.haplogroup == "H"
        assert "HV" in [s.haplogroup for s in derived_path]

        anc_terminal, anc_path = _tree_walk(bundle.mt_tree, ancestral, [])
        assert anc_terminal.haplogroup == "R"
        assert [step.haplogroup for step in anc_path] == ["L3", "N", "R"]
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
        assert _mt_snp_map(h2a2) == {}  # historical-only G750A is source-only
        assert _mt_snp_map(h2a2a) == {8860: "A"}  # historical-only G15326A is source-only
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
        assert [step.haplogroup for step in path] == [
            "L3",
            "N",
            "R",
            "U",
            "U8",
            "U8b",
            "K",
        ]

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
            (_MT_N1A_GENOTYPES, ["L3", "N", "N1", "N1a"]),
            (_MT_B_REVERSAL_GENOTYPES, ["L3", "N", "R", "B", "B5"]),
            (_MT_J_REVERSAL_GENOTYPES, ["L3", "N", "R", "JT", "J"]),
            (
                _MT_K1_REVERSAL_GENOTYPES,
                ["L3", "N", "R", "U", "U8", "U8b", "K", "K1"],
            ),
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


class TestMtTreeSelfConsistency:
    """Run every reportable mtDNA clade through production-equivalent evidence."""

    def test_every_reportable_mt_node_resolves_to_itself(self, bundle: HaplogroupBundle) -> None:
        all_snps: list[HaplogroupSNP] = []
        failures: list[str] = []
        source = json.loads(MT_HAPLOGROUP_SOURCE_PATH.read_text(encoding="utf-8"))
        structural = set(source["structural_exceptions"])

        def collect(node: HaplogroupNode) -> None:
            all_snps.extend(node.defining_snps)
            for child in node.children:
                collect(child)

        def walk(node: HaplogroupNode, ancestors: list[HaplogroupNode]) -> None:
            current_path = [*ancestors, node]
            if node.haplogroup not in structural:
                alleles_by_pos = {
                    snp.pos: snp.allele
                    for path_node in current_path
                    for snp in path_node.defining_snps
                }
                # assign_haplogroups joins observed MT calls to every bundle marker
                # at the same rCRS position, even when vendor identifiers differ.
                genotypes = {
                    snp.rsid: alleles_by_pos[snp.pos] * 2
                    for snp in all_snps
                    if snp.pos in alleles_by_pos
                }
                terminal, traversal = _tree_walk(bundle.mt_tree, genotypes, [])
                expected_path = [path_node.haplogroup for path_node in current_path[1:]]
                actual_path = [step.haplogroup for step in traversal]
                if terminal.haplogroup != node.haplogroup or actual_path != expected_path:
                    failures.append(
                        f"{node.haplogroup}: terminal={terminal.haplogroup}, "
                        f"path={actual_path}, expected={expected_path}"
                    )
            for child in node.children:
                walk(child, current_path)

        collect(bundle.mt_tree)
        walk(bundle.mt_tree, [])
        assert not failures


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

    def test_missing_k2_gateway_marker_can_route_to_supported_r_descendant(
        self, bundle: HaplogroupBundle
    ) -> None:
        """An absent M526 must not collapse a supported P/R lineage to K."""
        genotypes = {
            row["rsid"]: row["genotype"]
            for row in _derived_y_path_genotypes("R")
            if row["rsid"] != "rs2033003"
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

        assert terminal.haplogroup == "R"
        assert [(step.haplogroup, step.snps_present, step.snps_total) for step in traversal] == [
            ("CT", 2, 2),
            ("F", 2, 2),
            ("K", 2, 2),
            ("K2", 0, 1),
            ("P", 2, 2),
            ("R", 2, 2),
        ]

    def test_ancestral_k2_gateway_marker_blocks_r_descent(self, bundle: HaplogroupBundle) -> None:
        """The missing-marker exception must not bypass conflicting M526 evidence."""
        genotypes = {row["rsid"]: row["genotype"] for row in _derived_y_path_genotypes("R")}
        genotypes["rs2033003"] = "AA"

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

        assert terminal.haplogroup == "K"
        assert [step.haplogroup for step in traversal] == ["CT", "F", "K"]

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
            {"i5000073": "GG", "i5016162": "GG"},
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
    @pytest.mark.parametrize("target", list(_ROOT_L0_BATCH_DIRECT_POSITIONS))
    def test_issue_1798_root_l0_batch_exact_motifs_assign_by_position(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
    ) -> None:
        """All batch-01 nodes resolve through both production MT position joins."""
        node = _find_mt_node(bundle.mt_tree, target)
        assert node is not None
        assert tuple(sorted(_mt_snp_map(node))) == _ROOT_L0_BATCH_DIRECT_POSITIONS[target]

        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_root_l0_exact_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(_derived_mt_path_genotypes(target))
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        expected_steps = _ROOT_L0_BATCH_EXPECTED_STEPS[target]
        assert mt.haplogroup == target
        assert [
            (step.haplogroup, step.snps_present, step.snps_total) for step in mt.traversal_path
        ] == list(expected_steps)
        assert (mt.defining_snps_present, mt.defining_snps_total) == (
            sum(step[1] for step in expected_steps),
            sum(step[2] for step in expected_steps),
        )

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("target", "conflict_position", "ancestral_allele", "expected", "expected_steps"),
        [
            pytest.param("L0", 1048, "C", "mt-MRCA", (), id="L0"),
            pytest.param("L0a", 11176, "G", "L0", (("L0", 8, 8),), id="L0a"),
            pytest.param(
                "L0a1",
                5096,
                "T",
                "L0a",
                (("L0", 8, 8), ("L0a", 4, 4)),
                id="L0a1",
            ),
            pytest.param(
                "L0a2",
                64,
                "C",
                "L0a",
                (("L0", 8, 8), ("L0a", 4, 4)),
                id="L0a2",
            ),
            pytest.param("L0b", 6719, "T", "L0", (("L0", 8, 8),), id="L0b"),
            pytest.param("L0d", 1438, "G", "L0", (("L0", 8, 8),), id="L0d"),
            pytest.param(
                "L0d1",
                719,
                "G",
                "L0d",
                (("L0", 8, 8), ("L0d", 9, 9)),
                id="L0d1",
            ),
            pytest.param(
                "L0d2",
                3981,
                "A",
                "L0d",
                (("L0", 8, 8), ("L0d", 9, 9)),
                id="L0d2",
            ),
            pytest.param("L0f", 207, "G", "L0", (("L0", 8, 8),), id="L0f"),
            pytest.param("L0k", 199, "T", "L0", (("L0", 8, 8),), id="L0k"),
            pytest.param("L1", 3666, "G", "mt-MRCA", (), id="L1"),
            pytest.param("L2", 2416, "T", "mt-MRCA", (), id="L2"),
            pytest.param("L3", 1018, "A", "mt-MRCA", (), id="L3"),
            pytest.param("L4", 5460, "G", "mt-MRCA", (), id="L4"),
            pytest.param("L5", 3423, "T", "mt-MRCA", (), id="L5"),
            pytest.param("L6", 146, "T", "mt-MRCA", (), id="L6"),
        ],
    )
    def test_issue_1798_root_l0_batch_typed_ancestral_calls_block_descent(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
        conflict_position: int,
        ancestral_allele: str,
        expected: str,
        expected_steps: tuple[tuple[str, int, int], ...],
    ) -> None:
        """One typed ancestral direct marker rejects a node despite its other calls."""
        path_rows = _derived_mt_path_genotypes(target)
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_root_l0_conflict_{target}_{index}",
                "chrom": "MT",
                "genotype": (
                    ancestral_allele * 2
                    if int(row["pos"]) == conflict_position
                    else row["genotype"]
                ),
            }
            for index, row in enumerate(path_rows)
        ]
        assert sum(int(row["pos"]) == conflict_position for row in rows) == 1

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [
            (step.haplogroup, step.snps_present, step.snps_total) for step in mt.traversal_path
        ] == list(expected_steps)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        "target",
        ["L0a", "L0a1", "L0a2", "L0b", "L0d", "L0d1", "L0d2", "L0f", "L0k"],
    )
    def test_issue_1798_root_l0_batch_children_cannot_bypass_parent_gateways(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
    ) -> None:
        """A complete child motif cannot jump its nearest untyped emitted parent."""
        expected_steps = _ROOT_L0_BATCH_EXPECTED_STEPS[target]
        parent = expected_steps[-2][0]
        parent_positions = set(_ROOT_L0_BATCH_DIRECT_POSITIONS[parent])
        path_rows = [
            row
            for row in _derived_mt_path_genotypes(target)
            if int(row["pos"]) not in parent_positions
        ]
        if target == "L0a":
            # L0a's recurrent m.5460A is also one of L4's two direct markers,
            # so a parent-free L0a motif exposes the separately documented L4
            # singleton ambiguity. Type L4's other locus ancestral here to keep
            # this test focused on the missing-L0 gateway.
            path_rows.append({"pos": 16362, "genotype": "TT"})
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_root_l0_gateway_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(path_rows)
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        expected_ancestor_steps = expected_steps[:-2]
        expected = expected_ancestor_steps[-1][0] if expected_ancestor_steps else "mt-MRCA"
        assert mt.haplogroup == expected
        assert [
            (step.haplogroup, step.snps_present, step.snps_total) for step in mt.traversal_path
        ] == list(expected_ancestor_steps)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("source_prefix", "ancestor", "prefix_rows", "expected", "expected_steps"),
        [
            pytest.param(
                "L0a'b'f'g'k",
                "L0",
                [(189, "G"), (4586, "C"), (9818, "T"), (16172, "C")],
                "L0",
                (("L0", 8, 8),),
                id="L0abfgk-stops-L0",
            ),
            pytest.param(
                "L0a'b'f'g",
                "L0",
                [
                    (73, "A"),
                    (185, "A"),
                    (195, "T"),
                    (263, "G"),
                    (2245, "G"),
                    (5603, "T"),
                    (11641, "G"),
                    (15136, "A"),
                    (15431, "A"),
                ],
                "L0",
                (("L0", 8, 8),),
                id="L0abfg-stops-L0",
            ),
            pytest.param(
                "L0a'b'g",
                "L0",
                [
                    (93, "G"),
                    (95, "C"),
                    (236, "C"),
                    (8428, "T"),
                    (8566, "G"),
                    (9755, "A"),
                    (16148, "T"),
                ],
                "L0",
                (("L0", 8, 8),),
                id="L0abg-stops-L0",
            ),
            pytest.param(
                "L0a'g",
                "L0",
                [(11176, "A"), (16188, "G")],
                "L0",
                (("L0", 8, 8),),
                id="L0ag-one-of-four-stops-L0",
            ),
            pytest.param(
                "L0a1'4",
                "L0a",
                [(16168, "T")],
                "L0a",
                (("L0", 8, 8), ("L0a", 4, 4)),
                id="L0a14-stops-L0a",
            ),
            pytest.param(
                "L0d1'2",
                "L0d",
                [(3756, "G"), (9755, "A"), (16278, "C")],
                "L0d",
                (("L0", 8, 8), ("L0d", 9, 9)),
                id="L0d12-stops-L0d",
            ),
            pytest.param(
                "L1'2'3'4'5'6",
                None,
                [
                    (146, "T"),
                    (182, "T"),
                    (4312, "C"),
                    (10664, "C"),
                    (10915, "T"),
                    (11914, "G"),
                    (13276, "A"),
                    (16230, "A"),
                ],
                "mt-MRCA",
                (),
                id="L123456-stops-root",
            ),
            pytest.param(
                "L2'3'4'5'6",
                None,
                [(152, "T"), (2758, "G"), (2885, "T"), (7146, "A"), (8468, "C")],
                "mt-MRCA",
                (),
                id="L23456-stops-root",
            ),
            pytest.param(
                "L2'3'4'6",
                None,
                [
                    (195, "T"),
                    (247, "G"),
                    (825, "T"),
                    (8655, "C"),
                    (10688, "G"),
                    (10810, "T"),
                    (13105, "A"),
                    (13506, "C"),
                    (15301, "A"),
                    (16129, "G"),
                    (16187, "C"),
                    (16189, "T"),
                ],
                "mt-MRCA",
                (),
                id="L2346-stops-root",
            ),
            pytest.param(
                "L3'4'6",
                None,
                [(4104, "A"), (7521, "G")],
                "mt-MRCA",
                (),
                id="L346-stops-root",
            ),
            pytest.param(
                "L3'4",
                None,
                [(182, "C"), (3594, "C"), (7256, "C"), (13650, "C"), (16278, "C")],
                "mt-MRCA",
                (),
                id="L34-stops-root",
            ),
        ],
    )
    def test_issue_1798_flattened_source_prefixes_stop_at_honest_ancestor(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        source_prefix: str,
        ancestor: str | None,
        prefix_rows: list[tuple[int, str]],
        expected: str,
        expected_steps: tuple[tuple[str, int, int], ...],
    ) -> None:
        """Shared source-only motifs cannot manufacture a flattened sibling call."""
        ancestor_rows = [] if ancestor is None else _derived_mt_path_genotypes(ancestor)
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_prefix_{source_prefix}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *ancestor_rows,
                    *(
                        {"pos": position, "genotype": allele * 2}
                        for position, allele in prefix_rows
                    ),
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [
            (step.haplogroup, step.snps_present, step.snps_total) for step in mt.traversal_path
        ] == list(expected_steps)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("target", list(_ISSUE_1798_BATCH02_EXPECTED_PATHS))
    def test_issue_1798_batch02_exact_motifs_assign_by_position(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
    ) -> None:
        """All 19 batch-02 targets resolve through raw and annotated position joins."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch02_exact_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(_derived_mt_path_genotypes(target))
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == target
        assert [step.haplogroup for step in mt.traversal_path] == list(
            _ISSUE_1798_BATCH02_EXPECTED_PATHS[target]
        )

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("target", list(_ISSUE_1798_BATCH02_EXPECTED_PATHS))
    def test_issue_1798_batch02_typed_ancestral_marker_blocks_descent(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
    ) -> None:
        """A typed source-ancestral direct marker stops at the emitted parent."""
        conflict_position, ancestral_allele = _ISSUE_1798_BATCH02_CONFLICTS[target]
        path_rows = _derived_mt_path_genotypes(target)
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch02_conflict_{target}_{index}",
                "chrom": "MT",
                "genotype": (
                    ancestral_allele * 2
                    if int(row["pos"]) == conflict_position
                    else row["genotype"]
                ),
            }
            for index, row in enumerate(path_rows)
        ]
        assert sum(int(row["pos"]) == conflict_position for row in rows) == 1

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        expected_path = _ISSUE_1798_BATCH02_EXPECTED_PATHS[target][:-1]
        assert mt.haplogroup == expected_path[-1]
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("target", list(_ISSUE_1798_BATCH02_OLD_MARKERS))
    def test_issue_1798_batch02_legacy_markers_no_longer_overresolve(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
    ) -> None:
        """The replaced hand-curated marker set stops honestly at the parent."""
        expected_path = _ISSUE_1798_BATCH02_EXPECTED_PATHS[target][:-1]
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch02_old_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes(expected_path[-1]),
                    *(
                        {"pos": position, "genotype": allele * 2}
                        for position, allele in _ISSUE_1798_BATCH02_OLD_MARKERS[target]
                    ),
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected_path[-1]
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("source_prefix", list(_ISSUE_1798_BATCH02_FLATTENED_PREFIXES))
    def test_issue_1798_batch02_flattened_prefixes_stop_at_honest_ancestor(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        source_prefix: str,
    ) -> None:
        """A skipped helper's complete substitution motif cannot manufacture a child."""
        ancestor, prefix = _ISSUE_1798_BATCH02_FLATTENED_PREFIXES[source_prefix]
        expected_path = _ISSUE_1798_BATCH02_EXPECTED_PATHS.get(ancestor) or tuple(
            step[0] for step in _ROOT_L0_BATCH_EXPECTED_STEPS[ancestor]
        )
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch02_prefix_{source_prefix}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes(ancestor),
                    *({"pos": position, "genotype": allele * 2} for position, allele in prefix),
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == ancestor
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("target", list(_ISSUE_1798_BATCH03_EXPECTED_PATHS))
    def test_issue_1798_batch03_exact_motifs_assign_by_position(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
    ) -> None:
        """All 15 batch-03 targets resolve through raw and annotated position joins."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch03_exact_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(_derived_mt_path_genotypes(target))
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == target
        assert [step.haplogroup for step in mt.traversal_path] == list(
            _ISSUE_1798_BATCH03_EXPECTED_PATHS[target]
        )

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch03_m_resolves_with_pgp4162_sparse_coverage(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """The pgp_4162-like m.15043-only layout still clears M's 1/2 threshold."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch03_m_sparse_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("L3"),
                    {"pos": 15043, "genotype": "AA"},
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "M"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "M"]
        assert (mt.traversal_path[-1].snps_present, mt.traversal_path[-1].snps_total) == (
            1,
            2,
        )

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch03_d_helper_only_stops_at_m(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """Flattened M80'D m.4883 alone is only 1/3 D evidence and cannot overresolve."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch03_d_helper_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [*_derived_mt_path_genotypes("M"), {"pos": 4883, "genotype": "TT"}]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "M"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "M"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("node_rows", "expected", "expected_path"),
        [
            pytest.param(
                [{"pos": 14905, "genotype": "AA"}, {"pos": 16320, "genotype": "TT"}],
                "L3e2",
                ["L3", "L3e", "L3e2"],
                id="historical-2014-callable",
            ),
            pytest.param(
                [],
                "L3e",
                ["L3", "L3e"],
                id="primary-four-untyped",
            ),
        ],
    )
    def test_issue_1798_batch03_l3e2_respects_historical_only_coverage(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        node_rows: list[dict[str, object]],
        expected: str,
        expected_path: list[str],
    ) -> None:
        """The 2014 loci resolve L3e2; their primary-four absence stops at L3e."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch03_l3e2_{expected}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*_derived_mt_path_genotypes("L3e"), *node_rows])
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == expected_path

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("target", list(_ISSUE_1798_BATCH03_EXPECTED_PATHS))
    def test_issue_1798_batch03_typed_ancestral_marker_blocks_descent(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
    ) -> None:
        """One typed source-ancestral direct marker stops at the emitted parent."""
        conflict_position, ancestral_allele = _ISSUE_1798_BATCH03_CONFLICTS[target]
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch03_conflict_{target}_{index}",
                "chrom": "MT",
                "genotype": (
                    ancestral_allele * 2
                    if int(row["pos"]) == conflict_position
                    else row["genotype"]
                ),
            }
            for index, row in enumerate(_derived_mt_path_genotypes(target))
        ]
        if target == "G":
            # G and M9 recurrently share derived m.16362. Once G is blocked by
            # ancestral m.4833, explicitly type M9's unique m.4491 ancestral so
            # this case isolates the intended G -> M parent gate.
            rows.append(
                {
                    "rsid": "vendor_issue_1798_batch03_conflict_G_M9_guard",
                    "chrom": "MT",
                    "pos": 4491,
                    "genotype": "GG",
                }
            )
        assert sum(int(row["pos"]) == conflict_position for row in rows) == 1

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        expected_path = _ISSUE_1798_BATCH03_EXPECTED_PATHS[target][:-1]
        assert mt.haplogroup == expected_path[-1]
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("target", list(_ISSUE_1798_BATCH03_RUNTIME_OLD_MARKERS))
    def test_issue_1798_batch03_replaced_l3_markers_no_longer_overresolve(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
    ) -> None:
        """Each replaced L3-descendant marker set stops honestly at its parent."""
        expected_path = _ISSUE_1798_BATCH03_EXPECTED_PATHS[target][:-1]
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch03_old_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes(expected_path[-1]),
                    *(
                        {"pos": position, "genotype": allele * 2}
                        for position, allele in _ISSUE_1798_BATCH03_RUNTIME_OLD_MARKERS[target]
                    ),
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected_path[-1]
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("source_prefix", list(_ISSUE_1798_BATCH03_FLATTENED_PREFIXES))
    def test_issue_1798_batch03_flattened_prefixes_stop_at_honest_ancestor(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        source_prefix: str,
    ) -> None:
        """A helper motif alone cannot manufacture its nearest emitted child."""
        ancestor, prefix = _ISSUE_1798_BATCH03_FLATTENED_PREFIXES[source_prefix]
        expected_path = _ISSUE_1798_BATCH03_EXPECTED_PATHS.get(ancestor, (ancestor,))
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch03_prefix_{source_prefix}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes(ancestor),
                    *({"pos": position, "genotype": allele * 2} for position, allele in prefix),
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == ancestor
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("target", list(_ISSUE_1798_BATCH04_EXPECTED_PATHS))
    def test_issue_1798_batch04_d_subtree_exact_motifs_assign_by_position(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
    ) -> None:
        """All seven batch-04 D nodes resolve through both MT position joins."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch04_exact_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(_derived_mt_path_genotypes(target))
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == target
        assert [step.haplogroup for step in mt.traversal_path] == list(
            _ISSUE_1798_BATCH04_EXPECTED_PATHS[target]
        )

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("target", list(_ISSUE_1798_BATCH04_EXPECTED_PATHS))
    def test_issue_1798_batch04_typed_ancestral_marker_blocks_descent(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
    ) -> None:
        """One typed ancestral direct marker stops at the repaired emitted parent."""
        conflict_position, ancestral_allele = _ISSUE_1798_BATCH04_CONFLICTS[target]
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch04_conflict_{target}_{index}",
                "chrom": "MT",
                "genotype": (
                    ancestral_allele * 2
                    if int(row["pos"]) == conflict_position
                    else row["genotype"]
                ),
            }
            for index, row in enumerate(_derived_mt_path_genotypes(target))
        ]
        if target == "D4a":
            # D4a and D2 independently share the same m.16129A back mutation.
            # Once D4a is blocked at m.3206, type D2's unique m.8703 ancestral
            # so the case isolates the intended D4a -> D4 parent boundary.
            rows.append(
                {
                    "rsid": "vendor_issue_1798_batch04_conflict_D4a_D2_guard",
                    "chrom": "MT",
                    "pos": 8703,
                    "genotype": "CC",
                }
            )
        assert sum(int(row["pos"]) == conflict_position for row in rows) == 1

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        expected_path = _ISSUE_1798_BATCH04_EXPECTED_PATHS[target][:-1]
        assert mt.haplogroup == expected_path[-1]
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("target", list(_ISSUE_1798_BATCH04_RUNTIME_OLD_MARKERS))
    def test_issue_1798_batch04_replaced_d_markers_no_longer_overresolve(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
    ) -> None:
        """Each disjoint legacy marker set stops honestly at the repaired parent."""
        expected_path = _ISSUE_1798_BATCH04_EXPECTED_PATHS[target][:-1]
        rows_by_position = {
            int(row["pos"]): str(row["genotype"])
            for row in _derived_mt_path_genotypes(expected_path[-1])
        }
        rows_by_position.update(
            (position, allele * 2)
            for position, allele in _ISSUE_1798_BATCH04_RUNTIME_OLD_MARKERS[target]
        )
        rows = [
            {
                "rsid": f"vendor_issue_1798_batch04_old_{target}_{position}",
                "chrom": "MT",
                "pos": position,
                "genotype": genotype,
            }
            for position, genotype in rows_by_position.items()
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected_path[-1]
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch04_legacy_d1_pair_stops_at_d4_when_new_locus_is_ancestral(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """The old D1 pair cannot override an explicit ancestral m.2092 call."""
        rows_by_position = {
            int(row["pos"]): str(row["genotype"]) for row in _derived_mt_path_genotypes("D4")
        }
        # Legacy m.16325C remains one of D1's two valid direct markers, so it
        # intentionally reaches the 1/2 sparse-call floor when m.2092 is absent.
        # A typed ancestral m.2092 is positive evidence against D1 and must win.
        rows_by_position.update({5178: "AA", 16325: "CC", 2092: "CC"})
        rows = [
            {
                "rsid": f"vendor_issue_1798_batch04_old_D1_{position}",
                "chrom": "MT",
                "pos": position,
                "genotype": genotype,
            }
            for position, genotype in rows_by_position.items()
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "D4"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "M", "D", "D4"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch04_d2_flattened_helpers_stop_at_d4(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """D4e/D4e1 source-prefix calls cannot manufacture an emitted D2 call."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch04_D2_helper_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("D4"),
                    *(
                        {"pos": position, "genotype": allele * 2}
                        for position, allele in _ISSUE_1798_BATCH04_D2_FLATTENED_HELPERS
                    ),
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "D4"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "M", "D", "D4"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch04_d2_cannot_bypass_missing_d4_gateway(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """A complete D2 motif cannot jump its untyped repaired D4 parent."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch04_D2_ungated_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("D"),
                    {"pos": 8703, "genotype": "TT"},
                    {"pos": 16129, "genotype": "AA"},
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "D"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "M", "D"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch04_d3_flattened_helpers_stop_at_d4b(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """D4b1/D4b1c source-prefix calls cannot manufacture an emitted D3 call."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch04_D3_helper_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("D4b"),
                    *(
                        {"pos": position, "genotype": allele * 2}
                        for position, allele in _ISSUE_1798_BATCH04_D3_FLATTENED_HELPERS
                    ),
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "D4b"
        assert [step.haplogroup for step in mt.traversal_path] == [
            "L3",
            "M",
            "D",
            "D4",
            "D4b",
        ]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch04_d3_cannot_bypass_missing_d4b_gateway(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """A complete D3 motif cannot jump its untyped one-marker D4b parent."""
        d3 = _find_mt_node(bundle.mt_tree, "D3")
        assert d3 is not None
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch04_D3_ungated_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("D4"),
                    *({"pos": snp.pos, "genotype": snp.allele * 2} for snp in d3.defining_snps),
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "D4"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "M", "D", "D4"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch04_d3_primary_layout_resolves_without_m722(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """The primary-layout three-marker D3 motif resolves when m.722 is absent."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch04_D3_primary_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("D4b"),
                    {"pos": 4023, "genotype": "CC"},
                    {"pos": 6374, "genotype": "CC"},
                    {"pos": 9785, "genotype": "TT"},
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "D3"
        assert [step.haplogroup for step in mt.traversal_path] == [
            "L3",
            "M",
            "D",
            "D4",
            "D4b",
            "D3",
        ]
        terminal = mt.traversal_path[-1]
        assert (terminal.snps_present, terminal.snps_total) == (3, 4)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch04_d5_remains_a_direct_child_of_d(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """D5 bypasses a typed-ancestral D4 and remains directly reportable below D."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch04_D5_direct_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("D5"),
                    {"pos": 3010, "genotype": "GG"},
                    {"pos": 8414, "genotype": "CC"},
                    {"pos": 14668, "genotype": "CC"},
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "D5"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "M", "D", "D5"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "node_rows", "expected_terminal_counts"),
        [
            pytest.param(
                "full",
                [
                    {"pos": 3206, "genotype": "TT"},
                    {"pos": 8473, "genotype": "CC"},
                    {"pos": 14979, "genotype": "CC"},
                    {"pos": 16129, "genotype": "AA"},
                ],
                (4, 4),
                id="full-outranks-partial-D2",
            ),
            pytest.param(
                "sparse",
                [
                    {"pos": 3206, "genotype": "TT"},
                    {"pos": 16129, "genotype": "AA"},
                ],
                (2, 4),
                id="sparse-tie-beats-partial-D2-by-source-order",
            ),
        ],
    )
    def test_issue_1798_batch04_d4a_outranks_recurrent_d2_sibling(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        node_rows: list[dict[str, object]],
        expected_terminal_counts: tuple[int, int],
    ) -> None:
        """Full and tied-sparse D4a evidence wins over D2 through shared m.16129A."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch04_D4a_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*_derived_mt_path_genotypes("D4"), *node_rows])
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "D4a"
        assert [step.haplogroup for step in mt.traversal_path] == [
            "L3",
            "M",
            "D",
            "D4",
            "D4a",
        ]
        terminal = mt.traversal_path[-1]
        assert (terminal.snps_present, terminal.snps_total) == expected_terminal_counts

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("target", list(_ISSUE_1798_BATCH05_EXPECTED_MARKERS))
    def test_issue_1798_batch05_m_sibling_exact_motifs_assign_by_position(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
    ) -> None:
        """All 14 batch-05 nodes resolve with their exact scoreable motifs."""
        node = _find_mt_node(bundle.mt_tree, target)
        assert node is not None
        # This also locks structural/provenance-only exclusions: CZ m.249d,
        # C1 m.290-291d, C5 m.595.1C, M7b'c m.4071, and Z+152 m.152.
        assert (
            tuple((snp.pos, snp.allele) for snp in node.defining_snps)
            == (_ISSUE_1798_BATCH05_EXPECTED_MARKERS[target])
        )

        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch05_exact_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(_derived_mt_path_genotypes(target))
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == target
        assert [step.haplogroup for step in mt.traversal_path] == list(
            _ISSUE_1798_BATCH05_EXPECTED_PATHS[target]
        )

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("target", list(_ISSUE_1798_BATCH05_EXPECTED_MARKERS))
    def test_issue_1798_batch05_typed_ancestral_marker_blocks_descent(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
    ) -> None:
        """One typed ancestral direct state rejects the terminal batch-05 node."""
        conflict_position, ancestral_allele = _ISSUE_1798_BATCH05_CONFLICTS[target]
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch05_conflict_{target}_{index}",
                "chrom": "MT",
                "genotype": (
                    ancestral_allele * 2
                    if int(row["pos"]) == conflict_position
                    else row["genotype"]
                ),
            }
            for index, row in enumerate(_derived_mt_path_genotypes(target))
        ]
        assert sum(int(row["pos"]) == conflict_position for row in rows) == 1

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        expected_path = _ISSUE_1798_BATCH05_EXPECTED_PATHS[target][:-1]
        assert mt.haplogroup == expected_path[-1]
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("target", list(_ISSUE_1798_BATCH05_RUNTIME_OLD_MARKERS))
    def test_issue_1798_batch05_replaced_markers_no_longer_overresolve(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
    ) -> None:
        """The former hand-curated motif stops at the repaired emitted parent."""
        expected_path = _ISSUE_1798_BATCH05_EXPECTED_PATHS[target][:-1]
        rows_by_position = {
            int(row["pos"]): str(row["genotype"])
            for row in _derived_mt_path_genotypes(expected_path[-1])
        }
        rows_by_position.update(
            (position, allele * 2)
            for position, allele in _ISSUE_1798_BATCH05_RUNTIME_OLD_MARKERS[target]
        )
        if target == "C1":
            # Old C1 m.11969A is now valid C4 evidence. Type C4's second
            # direct locus ancestral so this case isolates C1's replacement.
            rows_by_position[15204] = "TT"
        rows = [
            {
                "rsid": f"vendor_issue_1798_batch05_old_{target}_{position}",
                "chrom": "MT",
                "pos": position,
                "genotype": genotype,
            }
            for position, genotype in rows_by_position.items()
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected_path[-1]
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("target", "ancestor"),
        [
            pytest.param("E", "M", id="E-requires-M9"),
            pytest.param("G2a", "G", id="G2a-requires-G2"),
            pytest.param("M7a", "M", id="M7a-requires-M7"),
            pytest.param("M7b", "M", id="M7b-requires-M7"),
            pytest.param("M7c", "M", id="M7c-requires-M7"),
            pytest.param("C1", "M8", id="C1-requires-C"),
            pytest.param("C4", "M8", id="C4-requires-C"),
            pytest.param("C5", "M8", id="C5-requires-C"),
            pytest.param("Z1", "M8", id="Z1-requires-Z"),
        ],
    )
    def test_issue_1798_batch05_children_cannot_bypass_repaired_gateways(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
        ancestor: str,
    ) -> None:
        """A complete terminal motif cannot jump an untyped emitted gateway."""
        node = _find_mt_node(bundle.mt_tree, target)
        assert node is not None
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch05_gateway_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes(ancestor),
                    *({"pos": snp.pos, "genotype": snp.allele * 2} for snp in node.defining_snps),
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        expected_path = _ISSUE_1798_BATCH03_EXPECTED_PATHS[ancestor]
        assert mt.haplogroup == ancestor
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("source_prefix", list(_ISSUE_1798_BATCH05_SOURCE_PREFIXES))
    def test_issue_1798_batch05_source_prefixes_stop_at_honest_ancestor(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        source_prefix: str,
    ) -> None:
        """Shared or source-only intermediate calls cannot manufacture a child."""
        ancestor, prefix = _ISSUE_1798_BATCH05_SOURCE_PREFIXES[source_prefix]
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch05_prefix_{source_prefix}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes(ancestor),
                    *({"pos": position, "genotype": allele * 2} for position, allele in prefix),
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        expected_path = _ISSUE_1798_BATCH05_EXPECTED_PATHS[ancestor]
        assert mt.haplogroup == ancestor
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("allele", "expected", "expected_path"),
        [
            pytest.param("C", "M7", ("L3", "M", "M7"), id="derived-C"),
            pytest.param("T", "M", ("L3", "M"), id="ancestral-T"),
            pytest.param("A", "M", ("L3", "M"), id="legacy-wrong-A"),
        ],
    )
    def test_issue_1798_batch05_m7_9824_state_controls_gateway(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        allele: str,
        expected: str,
        expected_path: tuple[str, ...],
    ) -> None:
        """M7 emits m.9824C; ancestral T and legacy A both block descent."""
        m7 = _find_mt_node(bundle.mt_tree, "M7")
        assert m7 is not None
        assert _mt_snp_map(m7) == {6455: "T", 9824: "C"}
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch05_M7_9824_{allele}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("M"),
                    {"pos": 6455, "genotype": "TT"},
                    {"pos": 9824, "genotype": allele * 2},
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize(
        ("target", "expected_parent", "expected_markers"),
        [
            pytest.param(target, parent, markers, id=target)
            for target, (parent, markers) in _ISSUE_1798_BATCH12_TREE.items()
        ],
    )
    def test_issue_1798_batch12_u_branch_tree_is_exact(
        self,
        bundle: HaplogroupBundle,
        target: str,
        expected_parent: str,
        expected_markers: tuple[tuple[int, str], ...],
    ) -> None:
        """Batch 12 retains only exact basal-U markers at their runtime owners."""
        parent = _find_mt_node(bundle.mt_tree, expected_parent)
        assert parent is not None
        matching_children = [child for child in parent.children if child.haplogroup == target]
        assert len(matching_children) == 1
        node = matching_children[0]
        assert tuple((snp.pos, snp.allele) for snp in node.defining_snps) == expected_markers

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("target", "calls", "expected_path"),
        [
            pytest.param(target, calls, expected_path, id=target)
            for target, (calls, expected_path) in _ISSUE_1798_BATCH12_DIRECT_CASES.items()
        ],
    )
    def test_issue_1798_batch12_direct_branches_resolve_without_helper_or_sibling_borrowing(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
        calls: tuple[tuple[int, str], ...],
        expected_path: tuple[str, ...],
    ) -> None:
        """Each basal-U node resolves at the caller's half-support boundary."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch12_direct_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == target
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("layout_id", list(_ISSUE_1798_BATCH12_LAYOUT_COUNTS))
    @pytest.mark.parametrize("target", list(_ISSUE_1798_BATCH12_TREE))
    def test_issue_1798_batch12_pinned_array_layouts_keep_u_gateways_reportable(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        layout_id: str,
        target: str,
    ) -> None:
        """Pinned callable masks preserve every U gateway and honest untyped leaf."""
        callable_positions = _ISSUE_1798_BATCH12_LAYOUT_CALLABLE_POSITIONS[layout_id]
        expected_counts = dict(
            zip(
                _ISSUE_1798_BATCH12_TREE,
                _ISSUE_1798_BATCH12_LAYOUT_COUNTS[layout_id],
                strict=True,
            )
        )
        full_path = _ISSUE_1798_BATCH12_DIRECT_CASES[target][1]
        u_path = tuple(name for name in full_path if name in _ISSUE_1798_BATCH12_TREE)

        calls = tuple(
            (position, allele)
            for name in u_path
            for position, allele in _ISSUE_1798_BATCH12_TREE[name][1]
            if position in callable_positions
        )
        for name in u_path:
            observed = sum(
                position in callable_positions
                for position, _allele in _ISSUE_1798_BATCH12_TREE[name][1]
            )
            assert observed == expected_counts[name]
            if observed:
                assert observed * 2 >= len(_ISSUE_1798_BATCH12_TREE[name][1])

        target_is_typed = expected_counts[target] > 0
        if not target_is_typed:
            assert (layout_id, target) in {
                ("pgp_4139", "U1a"),
                ("pgp_4162", "U4c"),
                ("pgp_4187", "U1a"),
                ("pgp_huA08F4D", "U1a"),
            }
        expected_path = full_path if target_is_typed else full_path[:-1]
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch12_layout_{layout_id}_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected_path[-1]
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

        traversal_by_name = {step.haplogroup: step for step in mt.traversal_path}
        for name in u_path:
            if name not in traversal_by_name:
                assert name == target
                assert expected_counts[name] == 0
                continue
            step = traversal_by_name[name]
            assert (step.snps_present, step.snps_total) == (
                expected_counts[name],
                len(_ISSUE_1798_BATCH12_TREE[name][1]),
            )

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "calls", "expected", "expected_path"),
        [
            pytest.param(
                "U2-prime-3-prime-4-prime-7-prime-8-prime-9",
                _ISSUE_1798_BATCH12_U_CALLS + ((1811, "G"),),
                "U",
                ("L3", "N", "R", "U"),
                id="m1811-helper-stays-at-U",
            ),
            pytest.param(
                "U2-plus-152",
                _ISSUE_1798_BATCH12_U2_CALLS + ((152, "C"),),
                "U2",
                ("L3", "N", "R", "U", "U2"),
                id="U2-plus-152-helper-cannot-call-U2e",
            ),
            pytest.param(
                "U7-source-only-152",
                _ISSUE_1798_BATCH12_U_CALLS + ((152, "C"),),
                "U",
                ("L3", "N", "R", "U"),
                id="historical-m152-cannot-call-U7",
            ),
            pytest.param(
                "U3a-prime-c",
                _ISSUE_1798_BATCH12_U3_CALLS + ((2294, "G"), (4703, "C"), (9266, "A")),
                "U3",
                ("L3", "N", "R", "U", "U3"),
                id="U3a-prime-c-helper-cannot-call-U3a",
            ),
            pytest.param(
                "U4-prime-9",
                _ISSUE_1798_BATCH12_U_CALLS + ((195, "C"), (499, "A"), (5999, "C")),
                "U",
                ("L3", "N", "R", "U"),
                id="U4-prime-9-helper-stays-at-U",
            ),
            pytest.param(
                "U6a-prime-b-prime-d",
                _ISSUE_1798_BATCH12_U6_CALLS + ((16219, "G"),),
                "U6",
                ("L3", "N", "R", "U", "U6"),
                id="U6-helper-cannot-call-U6a",
            ),
        ],
    )
    def test_issue_1798_batch12_flattened_helpers_are_source_only(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        calls: tuple[tuple[int, str], ...],
        expected: str,
        expected_path: tuple[str, ...],
    ) -> None:
        """Flattened U intermediates cannot manufacture a reportable child."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch12_helper_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("target", "conflict_position", "ancestral", "expected", "expected_path"),
        [
            pytest.param(target, position, allele, expected, path, id=target)
            for target, (position, allele, expected, path) in _ISSUE_1798_BATCH12_CONFLICTS.items()
        ],
    )
    def test_issue_1798_batch12_typed_ancestral_states_block_refinement(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
        conflict_position: int,
        ancestral: str,
        expected: str,
        expected_path: tuple[str, ...],
    ) -> None:
        """One typed ancestral direct event hard-stops its basal-U branch."""
        direct_calls = _ISSUE_1798_BATCH12_DIRECT_CASES[target][0]
        assert conflict_position in {position for position, _allele in direct_calls}
        calls = tuple(
            (position, ancestral if position == conflict_position else allele)
            for position, allele in direct_calls
        )
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch12_ancestral_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "calls", "expected", "expected_path"),
        [
            pytest.param("old-U-only", ((13133, "T"),), "R", ("L3", "N", "R")),
            pytest.param(
                "old-U1",
                _ISSUE_1798_BATCH12_U_CALLS + ((3531, "A"), (7581, "C")),
                "U",
                ("L3", "N", "R", "U"),
            ),
            pytest.param(
                "old-U1a",
                _ISSUE_1798_BATCH12_U1_CALLS + ((6026, "T"),),
                "U1",
                ("L3", "N", "R", "U", "U1"),
            ),
            pytest.param(
                "old-U1b",
                _ISSUE_1798_BATCH12_U1_CALLS + ((4991, "A"),),
                "U1",
                ("L3", "N", "R", "U", "U1"),
            ),
            pytest.param(
                "old-U2e",
                _ISSUE_1798_BATCH12_U2_CALLS + ((508, "G"), (13020, "C")),
                "U2",
                ("L3", "N", "R", "U", "U2"),
            ),
            pytest.param(
                "old-U3",
                _ISSUE_1798_BATCH12_U_CALLS + ((1811, "G"), (15454, "C")),
                "U",
                ("L3", "N", "R", "U"),
            ),
            pytest.param(
                "old-U4",
                _ISSUE_1798_BATCH12_U_CALLS + ((3714, "G"), (11339, "C")),
                "U",
                ("L3", "N", "R", "U"),
            ),
            pytest.param(
                "old-U4a",
                _ISSUE_1798_BATCH12_U4_CALLS + ((5999, "C"),),
                "U4",
                ("L3", "N", "R", "U", "U4"),
            ),
            pytest.param(
                "old-U4b",
                _ISSUE_1798_BATCH12_U4_CALLS + ((1811, "G"),),
                "U4",
                ("L3", "N", "R", "U", "U4"),
            ),
            pytest.param(
                "old-U4c",
                _ISSUE_1798_BATCH12_U4_CALLS + ((11332, "T"),),
                "U4",
                ("L3", "N", "R", "U", "U4"),
            ),
            pytest.param(
                "old-U6a",
                _ISSUE_1798_BATCH12_U6_CALLS + ((16219, "G"),),
                "U6",
                ("L3", "N", "R", "U", "U6"),
            ),
            pytest.param(
                "old-U7",
                _ISSUE_1798_BATCH12_U_CALLS + ((12308, "G"), (16309, "G")),
                "U",
                ("L3", "N", "R", "U"),
            ),
            pytest.param(
                "old-U9",
                _ISSUE_1798_BATCH12_U_CALLS + ((3834, "A"), (11914, "A")),
                "U",
                ("L3", "N", "R", "U"),
            ),
            pytest.param(
                "historical-U3b-transversion",
                _ISSUE_1798_BATCH12_U3_CALLS + ((4640, "A"),),
                "U3",
                ("L3", "N", "R", "U", "U3"),
            ),
            pytest.param(
                "U9-homoplasy-pair",
                _ISSUE_1798_BATCH12_U_CALLS + ((3531, "A"), (3834, "A")),
                "U9",
                ("L3", "N", "R", "U", "U9"),
            ),
        ],
    )
    def test_issue_1798_batch12_legacy_and_source_only_calls_do_not_over_refine(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        calls: tuple[tuple[int, str], ...],
        expected: str,
        expected_path: tuple[str, ...],
    ) -> None:
        """Legacy, flattened, and historical-only calls cannot refine basal U."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch12_legacy_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("target", "expected_path"),
        [
            pytest.param(
                "U5b2",
                ("L3", "N", "R", "U", "U5", "U5b", "U5b2"),
                id="excluded-U5b2-stays-reachable",
            ),
            pytest.param(
                "K1b",
                ("L3", "N", "R", "U", "U8", "U8b", "K", "K1", "K1b"),
                id="excluded-K1b-stays-reachable",
            ),
        ],
    )
    def test_issue_1798_batch12_excluded_u5_and_k_paths_remain_reachable(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
        expected_path: tuple[str, ...],
    ) -> None:
        """The replaced shared U/U8 gateways preserve excluded descendant paths."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch12_boundary_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(_derived_mt_path_genotypes(target))
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == target
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize(
        ("target", "expected_parent", "expected_markers"),
        [
            pytest.param(target, parent, markers, id=target)
            for target, (parent, markers) in _ISSUE_1798_BATCH13_TREE.items()
        ],
    )
    def test_issue_1798_batch13_u5_u8_k_tree_is_exact(
        self,
        bundle: HaplogroupBundle,
        target: str,
        expected_parent: str,
        expected_markers: tuple[tuple[int, str], ...],
    ) -> None:
        """Batch 13 emits the audited runtime owner, parent, and marker set."""
        parent = _find_mt_node(bundle.mt_tree, expected_parent)
        assert parent is not None
        matching_children = [child for child in parent.children if child.haplogroup == target]
        assert len(matching_children) == 1
        node = matching_children[0]
        assert tuple((snp.pos, snp.allele) for snp in node.defining_snps) == expected_markers

    def test_issue_1798_batch13_structural_and_coverage_contracts_are_explicit(
        self,
        bundle: HaplogroupBundle,
    ) -> None:
        """U5 is structural, helpers are source-only, and K1b keeps its audited scope."""
        source = json.loads(MT_HAPLOGROUP_SOURCE_PATH.read_text(encoding="utf-8"))

        u5 = source["structural_exceptions"]["U5"]
        assert u5["type"] == "markerless_passthrough"
        assert u5["emitted_parent"] == "U"
        assert u5["source_status"] == "exact"
        assert u5["emitted_snps"] == []
        assert u5["optional_conflict_snps"] == [
            {
                "rsid": "i5016270",
                "pos": 16270,
                "ancestral_allele": "C",
                "allele": "T",
                "motif_owner": "U5",
                "array_coverage": {
                    "cohort_id": "primary_four_23andme",
                    "position_present_in": ["pgp_4139", "pgp_4187", "pgp_huA08F4D"],
                    "callable_snv_in": ["pgp_4187", "pgp_huA08F4D"],
                },
            }
        ]
        assert [(row["pos"], row["emitted"]) for row in u5["direct_source_motif"]] == [
            (16192, False),
            (16270, False),
        ]

        u5_node = _find_mt_node(bundle.mt_tree, "U5")
        assert u5_node is not None
        assert u5_node.defining_snps == []
        assert [(snp.rsid, snp.pos, snp.allele) for snp in u5_node.optional_conflict_snps] == [
            ("i5016270", 16270, "T")
        ]

        assert _find_mt_node(bundle.mt_tree, "U5a'b") is None
        assert _find_mt_node(bundle.mt_tree, "U8b'c") is None
        assert 3197 not in _mt_snp_map(_find_mt_node(bundle.mt_tree, "U5a"))
        assert 3197 not in _mt_snp_map(_find_mt_node(bundle.mt_tree, "U5b"))
        assert 3480 not in _mt_snp_map(_find_mt_node(bundle.mt_tree, "U8b"))

        k1b_marker = source["nodes"]["K1b"]["emitted_snps"]
        assert len(k1b_marker) == 1
        assert k1b_marker[0]["pos"] == 5913
        assert k1b_marker[0]["array_coverage"] == {
            "cohort_id": "primary_four_23andme",
            "position_present_in": ["pgp_4139", "pgp_4187"],
            "callable_snv_in": [],
        }
        assert _ISSUE_1798_BATCH13_PRIMARY_LAYOUT_COUNTS["pgp_4162"][11] == 0
        assert all(
            counts[12] == 0 for counts in _ISSUE_1798_BATCH13_PRIMARY_LAYOUT_COUNTS.values()
        )

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("layout_id", "guard_genotype"),
        [
            pytest.param("pgp_4139", None, id="untyped-pgp-4139"),
            pytest.param("pgp_4162", None, id="untyped-pgp-4162"),
            pytest.param("pgp_4187", None, id="missing-pgp-4187"),
            pytest.param("pgp_huA08F4D", None, id="missing-pgp-huA08F4D"),
            pytest.param("pgp_4187", "--", id="no-call-pgp-4187"),
            pytest.param("pgp_4187", "TT", id="derived-pgp-4187"),
            pytest.param("pgp_huA08F4D", "TT", id="derived-pgp-huA08F4D"),
            pytest.param("pgp_4187", "CC", id="ancestral-pgp-4187"),
            pytest.param("pgp_huA08F4D", "CC", id="ancestral-pgp-huA08F4D"),
            pytest.param("pgp_4187", "CT", id="mixed-pgp-4187"),
            pytest.param("pgp_huA08F4D", "TC", id="mixed-pgp-huA08F4D"),
        ],
    )
    @pytest.mark.parametrize("target", ("U5a", "U5b"))
    def test_issue_2165_u5_optional_guard_preserves_missing_and_blocks_ancestral(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        layout_id: str,
        guard_genotype: str | None,
        target: str,
    ) -> None:
        """m.16270 is optional when absent but vetoes a contradicted U5 descent."""
        direct_calls = _ISSUE_1798_BATCH13_DIRECT_CASES[target][0]
        assert 16270 not in {position for position, _allele in direct_calls}
        if guard_genotype is not None:
            assert 16270 in _ISSUE_1798_BATCH13_PRIMARY_LAYOUT_CALLABLE_POSITIONS[layout_id]

        rows = [
            {
                **row,
                "rsid": f"vendor_issue_2165_{layout_id}_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *(
                        {"pos": position, "genotype": allele * 2}
                        for position, allele in direct_calls
                    ),
                    *(
                        [{"pos": 16270, "genotype": guard_genotype}]
                        if guard_genotype is not None
                        else []
                    ),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        if guard_genotype in {"CC", "CT", "TC"}:
            assert mt.haplogroup == "U"
            assert [step.haplogroup for step in mt.traversal_path] == ["L3", "N", "R", "U"]
            assert all(not step.haplogroup.startswith("U5") for step in mt.traversal_path)
        else:
            assert mt.haplogroup == target
            assert [step.haplogroup for step in mt.traversal_path] == list(
                _ISSUE_1798_BATCH13_PATHS[target]
            )

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("target", ("U5a", "U5b"))
    @pytest.mark.parametrize(
        "guard_genotypes",
        (("TT", "CC"), ("CC", "TT")),
        ids=("derived-then-ancestral", "ancestral-then-derived"),
    )
    def test_issue_2165_u5_optional_guard_blocks_discordant_typed_probes(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
        guard_genotypes: tuple[str, str],
    ) -> None:
        """Discordant typed m.16270 probes withhold markerless U5 descent."""
        direct_calls = _ISSUE_1798_BATCH13_DIRECT_CASES[target][0]
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_2165_discordant_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *(
                        {"pos": position, "genotype": allele * 2}
                        for position, allele in direct_calls
                    ),
                    *({"pos": 16270, "genotype": genotype} for genotype in guard_genotypes),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )

        assert mt.haplogroup == "U"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "N", "R", "U"]
        assert all(not step.haplogroup.startswith("U5") for step in mt.traversal_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("target", ("U5a", "U5b"))
    @pytest.mark.parametrize(
        ("s1_guard", "s2_guard", "guard_blocks"),
        [
            pytest.param("CC", "TT", True, id="merged-discordant-s1-ancestral"),
            pytest.param("TT", "CC", True, id="merged-discordant-s2-ancestral"),
            pytest.param("CC", "CC", True, id="merged-concordant-ancestral"),
            pytest.param("TT", "TT", False, id="merged-concordant-derived"),
            pytest.param("--", "TT", False, id="merged-filled-nocall-derived"),
            pytest.param("--", "--", False, id="merged-both-no-call"),
        ],
    )
    def test_issue_2165_u5_guard_reads_flag_only_merged_representation(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
        s1_guard: str,
        s2_guard: str,
        guard_blocks: bool,
    ) -> None:
        """The guard sees a merged discordance through its production row shape.

        ``flag_only`` is the default merge strategy and it does *not* keep both
        conflicting calls: it writes one row carrying the merge-ambiguity
        sentinel. Building the rows with the real ``_apply_semantics`` keeps this
        test bound to that representation instead of to a hand-written literal,
        so a merged sample whose sources disagree at m.16270 must withhold U5
        exactly like two discordant probes in a single file. The ordinary
        no-call rows are the discriminating negative control: they must remain
        missing evidence and still reach the U5 subtype.
        """
        direct_calls = _ISSUE_1798_BATCH13_DIRECT_CASES[target][0]
        shared = [
            *_derived_mt_path_genotypes("R"),
            *({"pos": position, "genotype": allele * 2} for position, allele in direct_calls),
        ]
        s1_coords: dict[tuple[str, int], dict[str, str]] = {}
        s2_coords: dict[tuple[str, int], dict[str, str]] = {}
        for index, row in enumerate(shared):
            coord = ("MT", int(row["pos"]))
            entry = {"rsid": f"vendor_issue_2165_merged_{target}_{index}", **row}
            s1_coords[coord] = dict(entry)
            s2_coords[coord] = dict(entry)
        guard_rsid = f"vendor_issue_2165_merged_{target}_guard"
        s1_coords[("MT", 16270)] = {"rsid": guard_rsid, "genotype": s1_guard}
        s2_coords[("MT", 16270)] = {"rsid": guard_rsid, "genotype": s2_guard}

        merged_rows, _summary = _apply_semantics(
            s1_coords,
            s2_coords,
            strategy=MergeStrategy.FLAG_ONLY,
            rsids_in_bundle=set(),
            s1_vendor="23andme",
            s2_vendor="ancestrydna",
        )
        guard_row = next(row for row in merged_rows if row.pos == 16270)
        if s1_guard != s2_guard and "--" not in (s1_guard, s2_guard):
            # Lock the production shape this test depends on: one collapsed row
            # holding the sentinel, not the two source genotypes.
            assert guard_row.concordance == "discordant"
            assert guard_row.genotype == MERGE_AMBIGUITY_SENTINEL
            assert is_no_call(guard_row.genotype)

        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(source_table),
                [
                    {
                        "rsid": row.rsid,
                        "chrom": row.chrom,
                        "pos": row.pos,
                        "genotype": row.genotype,
                    }
                    for row in merged_rows
                ],
            )

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        if guard_blocks:
            assert mt.haplogroup == "U"
            assert [step.haplogroup for step in mt.traversal_path] == ["L3", "N", "R", "U"]
            assert all(not step.haplogroup.startswith("U5") for step in mt.traversal_path)
        else:
            assert mt.haplogroup == target
            assert [step.haplogroup for step in mt.traversal_path] == list(
                _ISSUE_1798_BATCH13_PATHS[target]
            )

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("target", "calls", "expected_path"),
        [
            pytest.param(target, calls, expected_path, id=target)
            for target, (calls, expected_path) in _ISSUE_1798_BATCH13_DIRECT_CASES.items()
        ],
    )
    def test_issue_1798_batch13_direct_branches_resolve_without_sibling_borrowing(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
        calls: tuple[tuple[int, str], ...],
        expected_path: tuple[str, ...],
    ) -> None:
        """Every reportable Batch 13 identity resolves from only its own path."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch13_direct_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == target
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("layout_id", list(_ISSUE_1798_BATCH13_PRIMARY_LAYOUT_COUNTS))
    @pytest.mark.parametrize("target", list(_ISSUE_1798_BATCH13_TREE))
    def test_issue_1798_batch13_primary_layouts_preserve_honest_u5_u8_k_calls(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        layout_id: str,
        target: str,
    ) -> None:
        """All four pinned modern layouts retain exact per-node callable counts."""
        callable_positions = _ISSUE_1798_BATCH13_PRIMARY_LAYOUT_CALLABLE_POSITIONS[layout_id]
        expected_counts = dict(
            zip(
                _ISSUE_1798_BATCH13_TREE,
                _ISSUE_1798_BATCH13_PRIMARY_LAYOUT_COUNTS[layout_id],
                strict=True,
            )
        )
        full_path = _ISSUE_1798_BATCH13_PATHS[target]
        batch_path = tuple(name for name in full_path if name in _ISSUE_1798_BATCH13_TREE)
        upstream_calls = (
            _ISSUE_1798_BATCH13_U8_CALLS if "U8" in full_path else _ISSUE_1798_BATCH12_U_CALLS
        )
        calls = upstream_calls + tuple(
            (position, allele)
            for name in batch_path
            for position, allele in _ISSUE_1798_BATCH13_TREE[name][1]
            if position in callable_positions
        )

        for name in batch_path:
            observed = sum(
                position in callable_positions
                for position, _allele in _ISSUE_1798_BATCH13_TREE[name][1]
            )
            assert observed == expected_counts[name]
            if observed:
                assert observed * 2 >= len(_ISSUE_1798_BATCH13_TREE[name][1])

        target_is_typed = expected_counts[target] > 0
        if target == "U5":
            expected_path = full_path[:-1]
        elif target_is_typed:
            expected_path = full_path
        else:
            assert target in {"K1a", "K1b"}
            if target == "K1a":
                assert layout_id == "pgp_4162"
            expected_path = full_path[:-1]

        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch13_layout_{layout_id}_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected_path[-1]
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

        traversal_by_name = {step.haplogroup: step for step in mt.traversal_path}
        for name in batch_path:
            if name not in traversal_by_name:
                assert name == target
                assert expected_counts[name] == 0
                continue
            step = traversal_by_name[name]
            assert (step.snps_present, step.snps_total) == (
                expected_counts[name],
                len(_ISSUE_1798_BATCH13_TREE[name][1]),
            )

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("target", "conflict_position", "ancestral", "expected", "expected_path"),
        [
            pytest.param(target, position, allele, expected, path, id=target)
            for target, (position, allele, expected, path) in (
                _ISSUE_1798_BATCH13_CONFLICTS.items()
            )
        ],
    )
    def test_issue_1798_batch13_typed_ancestral_states_block_refinement(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
        conflict_position: int,
        ancestral: str,
        expected: str,
        expected_path: tuple[str, ...],
    ) -> None:
        """A typed ancestral direct state blocks its branch despite other support."""
        direct_calls = _ISSUE_1798_BATCH13_DIRECT_CASES[target][0]
        assert conflict_position in {position for position, _allele in direct_calls}
        calls = tuple(
            (position, ancestral if position == conflict_position else allele)
            for position, allele in direct_calls
        )
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch13_ancestral_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "calls", "expected", "expected_path"),
        [
            pytest.param(
                "U5-direct-motif",
                _ISSUE_1798_BATCH12_U_CALLS + ((16192, "T"), (16270, "T")),
                "U",
                ("L3", "N", "R", "U"),
                id="markerless-U5-source-events",
            ),
            pytest.param(
                "U5a-prime-b",
                _ISSUE_1798_BATCH12_U_CALLS + ((3197, "C"), (9477, "A"), (13617, "C")),
                "U",
                ("L3", "N", "R", "U"),
                id="U5-helper-stays-source-only",
            ),
            pytest.param(
                "U5a-historical",
                _ISSUE_1798_BATCH12_U_CALLS + ((14793, "G"),),
                "U",
                ("L3", "N", "R", "U"),
                id="U5a-historical-marker-cannot-terminal-call",
            ),
            pytest.param(
                "U5b-historical",
                _ISSUE_1798_BATCH12_U_CALLS + ((150, "T"), (7768, "G")),
                "U",
                ("L3", "N", "R", "U"),
                id="U5b-historical-markers-cannot-terminal-call",
            ),
            pytest.param(
                "U8b-prime-c",
                _ISSUE_1798_BATCH13_U8_CALLS + ((3480, "G"),),
                "U8",
                ("L3", "N", "R", "U", "U8"),
                id="U8b-helper-stays-source-only",
            ),
            pytest.param(
                "U8a-historical",
                _ISSUE_1798_BATCH13_U8_CALLS + ((7055, "G"),),
                "U8",
                ("L3", "N", "R", "U", "U8"),
                id="U8a-historical-marker-cannot-terminal-call",
            ),
            pytest.param(
                "U8b-historical",
                _ISSUE_1798_BATCH13_U8_CALLS + ((9055, "A"),),
                "U8",
                ("L3", "N", "R", "U", "U8"),
                id="U8b-historical-marker-cannot-terminal-call",
            ),
            pytest.param(
                "K-source-only",
                _ISSUE_1798_BATCH13_U8B_CALLS + ((16224, "C"), (16311, "C")),
                "U8b",
                _ISSUE_1798_BATCH13_PATHS["U8b"],
                id="K-historical-and-recurrent-events-cannot-call-K",
            ),
            pytest.param(
                "K1a-optional",
                _ISSUE_1798_BATCH13_K1_CALLS + ((16093, "C"),),
                "K1",
                _ISSUE_1798_BATCH13_PATHS["K1"],
                id="K1a-optional-event-cannot-call-K1a",
            ),
            pytest.param(
                "K2-reversion",
                _ISSUE_1798_BATCH13_K_CALLS + ((146, "C"),),
                "K",
                _ISSUE_1798_BATCH13_PATHS["K"],
                id="K2-source-only-reversion-cannot-call-K2",
            ),
            pytest.param(
                "K2a-source-only",
                _ISSUE_1798_BATCH13_K2_CALLS + ((152, "C"), (709, "A")),
                "K2",
                _ISSUE_1798_BATCH13_PATHS["K2"],
                id="K2a-source-only-events-cannot-call-K2a",
            ),
        ],
    )
    def test_issue_1798_batch13_helpers_and_omitted_events_do_not_over_refine(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        calls: tuple[tuple[int, str], ...],
        expected: str,
        expected_path: tuple[str, ...],
    ) -> None:
        """Flattened, historical-only, optional, and recurrent events do not score."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch13_source_only_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch13_k_cannot_bypass_u8b_gateway(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """Complete K evidence remains unreachable when direct U8b evidence is absent."""
        calls = _ISSUE_1798_BATCH13_U8_CALLS + (
            (10550, "G"),
            (11299, "C"),
            (14798, "C"),
        )
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch13_ungated_K_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "U8"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "N", "R", "U", "U8"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("node_rows", "expected", "expected_path"),
        [
            pytest.param(
                [{"pos": 12405, "genotype": "TT"}],
                "M7b",
                ("L3", "M", "M7", "M7b"),
                id="historical-2014-callable",
            ),
            pytest.param([], "M7", ("L3", "M", "M7"), id="primary-four-untyped"),
        ],
    )
    def test_issue_1798_batch05_m7b_respects_historical_only_coverage(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        node_rows: list[dict[str, object]],
        expected: str,
        expected_path: tuple[str, ...],
    ) -> None:
        """Historical m.12405T resolves M7b; its primary-four absence does not."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch05_M7b_{expected}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*_derived_mt_path_genotypes("M7"), *node_rows])
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch05_old_m7b_motif_stops_at_m(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """Old m.5351G plus wrong-state m.9824A cannot enter repaired M7."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch05_old_M7b_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("M"),
                    {"pos": 5351, "genotype": "GG"},
                    {"pos": 9824, "genotype": "AA"},
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "M"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "M"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("target", list(_ISSUE_1798_BATCH06_EXPECTED_MARKERS))
    def test_issue_1798_batch06_n_subtrees_exact_motifs_assign_by_position(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
    ) -> None:
        """Every migrated N/A/N9/Y node resolves through its exact emitted path."""
        node = _find_mt_node(bundle.mt_tree, target)
        assert node is not None
        assert (
            tuple((snp.pos, snp.allele) for snp in node.defining_snps)
            == (_ISSUE_1798_BATCH06_EXPECTED_MARKERS[target])
        )

        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch06_exact_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(_derived_mt_path_genotypes(target))
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == target
        assert [step.haplogroup for step in mt.traversal_path] == list(
            _ISSUE_1798_BATCH06_EXPECTED_PATHS[target]
        )

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("target", list(_ISSUE_1798_BATCH06_EXPECTED_MARKERS))
    def test_issue_1798_batch06_typed_ancestral_marker_blocks_descent(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
    ) -> None:
        """One typed ancestral direct state rejects the migrated node."""
        conflict_position, ancestral_allele = _ISSUE_1798_BATCH06_CONFLICTS[target]
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch06_conflict_{target}_{index}",
                "chrom": "MT",
                "genotype": (
                    ancestral_allele * 2
                    if int(row["pos"]) == conflict_position
                    else row["genotype"]
                ),
            }
            for index, row in enumerate(_derived_mt_path_genotypes(target))
        ]
        assert sum(int(row["pos"]) == conflict_position for row in rows) == 1

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        expected_path = _ISSUE_1798_BATCH06_EXPECTED_PATHS[target][:-1]
        assert mt.haplogroup == expected_path[-1]
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("target", list(_ISSUE_1798_BATCH06_RUNTIME_OLD_MARKERS))
    def test_issue_1798_batch06_old_markers_stop_at_honest_ancestor(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
    ) -> None:
        """Former motifs below the new threshold cannot manufacture a terminal."""
        expected_path = _ISSUE_1798_BATCH06_EXPECTED_PATHS[target][:-1]
        rows_by_position = {
            int(row["pos"]): str(row["genotype"])
            for row in _derived_mt_path_genotypes(expected_path[-1])
        }
        rows_by_position.update(
            (position, allele * 2)
            for position, allele in _ISSUE_1798_BATCH06_RUNTIME_OLD_MARKERS[target]
        )
        rows = [
            {
                "rsid": f"vendor_issue_1798_batch06_old_{target}_{position}",
                "chrom": "MT",
                "pos": position,
                "genotype": genotype,
            }
            for position, genotype in rows_by_position.items()
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected_path[-1]
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch06_retired_a4_markers_do_not_refine_a(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """Unmapped A4 is absent, and its former pair leaves assignment at A."""
        assert _find_mt_node(bundle.mt_tree, "A4") is None
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch06_retired_A4_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("A"),
                    {"pos": 9347, "genotype": "GG"},
                    {"pos": 14308, "genotype": "AA"},
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "A"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "N", "A"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("target", "ancestor"),
        [
            pytest.param("A2", "N", id="A2-requires-A"),
            pytest.param("A5", "N", id="A5-requires-A"),
            pytest.param("N1b", "N", id="N1b-requires-N1"),
            pytest.param("N9a", "N", id="N9a-requires-N9"),
            pytest.param("N9b", "N", id="N9b-requires-N9"),
            pytest.param("Y1", "N9", id="Y1-requires-Y"),
            pytest.param("Y2", "N9", id="Y2-requires-Y"),
        ],
    )
    def test_issue_1798_batch06_children_cannot_bypass_repaired_gateways(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
        ancestor: str,
    ) -> None:
        """A complete child motif cannot jump an untyped emitted gateway."""
        node_rows = [
            {"pos": position, "genotype": allele * 2}
            for position, allele in _ISSUE_1798_BATCH06_EXPECTED_MARKERS[target]
        ]
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch06_gateway_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*_derived_mt_path_genotypes(ancestor), *node_rows])
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == ancestor
        assert [step.haplogroup for step in mt.traversal_path] == list(
            _ISSUE_1798_BATCH06_EXPECTED_PATHS[ancestor]
        )

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("target", "present_positions", "expected", "expected_counts"),
        _ISSUE_1798_BATCH06_PARTIAL_COVERAGE,
    )
    def test_issue_1798_batch06_partial_coverage_boundary_is_explicit(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
        present_positions: tuple[int, ...],
        expected: str,
        expected_counts: tuple[int, int] | None,
    ) -> None:
        """The >= 0.5 policy accepts the boundary and rejects the row below it."""
        target_path = _ISSUE_1798_BATCH06_EXPECTED_PATHS[target]
        parent = target_path[-2]
        allele_by_position = dict(_ISSUE_1798_BATCH06_EXPECTED_MARKERS[target])
        node_rows = [
            {"pos": position, "genotype": allele_by_position[position] * 2}
            for position in present_positions
        ]
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch06_partial_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*_derived_mt_path_genotypes(parent), *node_rows])
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        expected_path = target_path if expected == target else target_path[:-1]
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)
        if expected_counts is not None:
            terminal = mt.traversal_path[-1]
            assert (terminal.snps_present, terminal.snps_total) == expected_counts

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "n9_genotype"),
        [
            pytest.param("missing", None, id="missing-m5417"),
            pytest.param("no-call", "--", id="no-call-m5417"),
            pytest.param("ancestral", "GG", id="ancestral-m5417"),
        ],
    )
    def test_issue_1798_batch06_y_cannot_bypass_n9_gateway(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        n9_genotype: str | None,
    ) -> None:
        """Missing, no-call, and ancestral m.5417 all stop complete Y1 at N."""
        node_rows = [
            {"pos": position, "genotype": allele * 2}
            for target in ("Y_mt", "Y1")
            for position, allele in _ISSUE_1798_BATCH06_EXPECTED_MARKERS[target]
        ]
        if n9_genotype is not None:
            node_rows.append({"pos": 5417, "genotype": n9_genotype})
        # Y carries R's m.16223C state. Type R's other direct locus ancestral
        # so this test isolates the missing N9 gate instead of taking sparse R.
        node_rows.append({"pos": 12705, "genotype": "TT"})
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch06_N9_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*_derived_mt_path_genotypes("N"), *node_rows])
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "N"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "N"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch06_shared_n9_loci_do_not_select_a_sibling(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """Shared m.5147A and historical m.1598A alone leave an N9 sample at N9."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch06_shared_N9_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("N9"),
                    # m.5147A is shared by N9b and Y2; m.1598A moved to N1b.
                    {"pos": 5147, "genotype": "AA"},
                    {"pos": 1598, "genotype": "AA"},
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "N9"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "N", "N9"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch02_l2a2_reversion_and_l5a_recurrence_are_routable(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """Typed reversed/recurrent states do not block their true descendant paths."""
        cases = (
            ("L2a2", {146: "T"}, ("L2", "L2a", "L2a2")),
            ("L5a", {5460: "G"}, ("L5", "L5a")),
        )
        for target, extra_states, expected_path in cases:
            with sample_engine.begin() as conn:
                conn.execute(
                    sa.delete(source_table),
                )
                rows_by_position = {
                    int(row["pos"]): str(row["genotype"])
                    for row in _derived_mt_path_genotypes(target)
                }
                rows_by_position.update(extra_states)
                rows = [
                    {
                        "rsid": f"vendor_issue_1798_batch02_state_{target}_{position}",
                        "chrom": "MT",
                        "pos": position,
                        "genotype": allele * 2 if len(allele) == 1 else allele,
                    }
                    for position, allele in rows_by_position.items()
                ]
                conn.execute(sa.insert(source_table), rows)

            mt = next(
                result
                for result in assign_haplogroups(bundle, sample_engine)
                if result.tree_type == "mt"
            )
            assert mt.haplogroup == target
            assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("with_l0_gateway", [False, True])
    def test_issue_1798_m11176_alone_does_not_overresolve_l0a_or_l0k(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        with_l0_gateway: bool,
    ) -> None:
        """The one L0a'g call is only 1/4 L0a evidence and is no longer an L0k SNP."""
        trunk = _derived_mt_path_genotypes("L0") if with_l0_gateway else []
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_m11176_{with_l0_gateway}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*trunk, {"pos": 11176, "genotype": "AA"}])
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        expected = "L0" if with_l0_gateway else "mt-MRCA"
        assert mt.haplogroup == expected
        assert "L0a" not in [step.haplogroup for step in mt.traversal_path]
        assert "L0k" not in [step.haplogroup for step in mt.traversal_path]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("target", "ancestor", "legacy_rows", "expected", "expected_path"),
        [
            pytest.param("L0", None, [(9042, "T")], "mt-MRCA", [], id="L0"),
            pytest.param("L0a", "L0", [(1438, "G"), (9042, "T")], "L0", ["L0"], id="L0a"),
            pytest.param(
                "L0a1",
                "L0a",
                [(7158, "G"), (9818, "C")],
                "L0a",
                ["L0", "L0a"],
                id="L0a1",
            ),
            pytest.param(
                "L0a2",
                "L0a",
                [(7256, "T"), (11899, "C")],
                "L0a",
                ["L0", "L0a"],
                id="L0a2",
            ),
            pytest.param(
                "L0b",
                "L0",
                [(3693, "A"), (5580, "C"), (12171, "G")],
                "L0",
                ["L0"],
                id="L0b",
            ),
            pytest.param("L0d", "L0", [(1715, "C"), (9755, "A")], "L0", ["L0"], id="L0d"),
            pytest.param("L0d1", "L0d", [(8113, "T")], "L0d", ["L0", "L0d"], id="L0d1"),
            pytest.param(
                "L0d2",
                "L0d",
                [(2969, "A"), (10394, "T")],
                "L0d",
                ["L0", "L0d"],
                id="L0d2",
            ),
            pytest.param("L0f", "L0", [(3396, "G"), (10586, "A")], "L0", ["L0"], id="L0f"),
            pytest.param("L0k", "L0", [(2352, "C")], "L0", ["L0"], id="L0k"),
            pytest.param(
                "L1",
                None,
                [(7055, "G"), (10589, "A"), (10810, "C")],
                "mt-MRCA",
                [],
                id="L1",
            ),
            pytest.param(
                "L2",
                None,
                [(2789, "C"), (7175, "C"), (7771, "G")],
                "mt-MRCA",
                [],
                id="L2",
            ),
            pytest.param("L3", None, [(769, "G"), (16311, "T")], "mt-MRCA", [], id="L3"),
            pytest.param("L4", None, [(5108, "C"), (10685, "A")], "mt-MRCA", [], id="L4"),
            pytest.param("L5", None, [(5108, "C"), (15301, "A")], "mt-MRCA", [], id="L5"),
            pytest.param(
                "L6",
                None,
                [(3396, "G"), (7146, "G"), (10589, "A")],
                "mt-MRCA",
                [],
                id="L6",
            ),
        ],
    )
    def test_issue_1798_obsolete_root_l0_markers_do_not_restore_old_assignments(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
        ancestor: str | None,
        legacy_rows: list[tuple[int, str]],
        expected: str,
        expected_path: list[str],
    ) -> None:
        """Removed pre-audit markers cannot refine the nearest supported ancestor."""
        assert not (
            {position for position, _ in legacy_rows}
            & set(_ROOT_L0_BATCH_DIRECT_POSITIONS[target])
        )
        trunk = [] if ancestor is None else _derived_mt_path_genotypes(ancestor)
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_legacy_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *trunk,
                    *(
                        {"pos": position, "genotype": allele * 2}
                        for position, allele in legacy_rows
                    ),
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == expected_path

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("positions", "expected_counts"),
        [
            pytest.param((5460,), (1, 2), id="m5460-only"),
            pytest.param((16362,), (1, 2), id="m16362-only"),
            pytest.param((5460, 16362), (2, 2), id="complete-pair"),
        ],
    )
    def test_issue_1798_l4_sparse_recurrent_marker_ambiguity_is_explicit(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        positions: tuple[int, ...],
        expected_counts: tuple[int, int],
    ) -> None:
        """L4's pair is exact, while either recurrent singleton meets today's 1/2 floor."""
        # This documents a known sparse-data ambiguity, not marker uniqueness:
        # m.5460 and m.16362 recur elsewhere in Build 17. Tightening the general
        # mtDNA descent policy is deliberately outside this source-migration batch.
        allele_by_position = {5460: "A", 16362: "C"}
        rows = [
            {
                "rsid": f"vendor_issue_1798_l4_sparse_{position}",
                "chrom": "MT",
                "pos": position,
                "genotype": allele_by_position[position] * 2,
            }
            for position in positions
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "L4"
        assert [step.haplogroup for step in mt.traversal_path] == ["L4"]
        terminal = mt.traversal_path[-1]
        assert (terminal.snps_present, terminal.snps_total) == expected_counts

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_historical_2014_l0f_layout_stops_at_l0(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """The pinned 2014 layout has only 3/9 L0f SNPs, below the descent floor."""
        allele_by_position = {
            207: "A",
            1048: "T",
            5442: "C",
            6185: "C",
            9347: "G",
            10589: "A",
            12007: "A",
            12720: "G",
            16169: "T",
            16327: "T",
        }
        rows = [
            {
                "rsid": f"vendor_issue_1798_historical_l0f_{position}",
                "chrom": "MT",
                "pos": position,
                "genotype": allele * 2,
            }
            for position, allele in allele_by_position.items()
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "L0"
        assert [
            (step.haplogroup, step.snps_present, step.snps_total) for step in mt.traversal_path
        ] == [("L0", 7, 8)]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1899_pgp_1050_callable_layout_assigns_i(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """Audited pgp_1050 coverage supports a synthetic exact I path."""
        layout = json.loads(PGP_1050_MT_I_LAYOUT_PATH.read_text(encoding="utf-8"))
        source = json.loads(MT_HAPLOGROUP_SOURCE_PATH.read_text(encoding="utf-8"))
        source_export = source["array_exports"][layout["source_export_id"]]
        assert layout["schema_version"] == 1
        assert layout["source_export_id"] == "pgp_1050"
        assert layout["source_sha256"] == source_export["sha256"]
        assert layout["source_line_count"] == source_export["line_count"]

        layout_rows = layout["positions"]
        layout_positions = {int(row["position"]) for row in layout_rows}
        assert len(layout_rows) == len(layout_positions)
        assert all(
            row["position_present"] is True
            and row["callable_snv"] is True
            and int(row["probe_rows"]) >= 1
            and int(row["callable_rows"]) == int(row["probe_rows"])
            for row in layout_rows
        )
        runtime_positions = {int(row["pos"]) for row in _MT_I_GENOTYPES}
        assert runtime_positions <= layout_positions
        # The immutable coverage audit still records N's historical-only
        # m.8701 and m.10873 probes even though they no longer score at runtime.
        assert layout_positions - runtime_positions == {8701, 10873}

        # The fixture supplies hypothetical I-derived alleles; it does not claim
        # that the public exemplar donor carries haplogroup I.
        rows = [
            {**row, "rsid": f"vendor_issue_1899_pgp_1050_layout_{index}"}
            for index, row in enumerate(_MT_I_GENOTYPES)
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "I"
        assert [
            (step.haplogroup, step.snps_present, step.snps_total) for step in mt.traversal_path
        ] == [
            ("L3", 1, 1),
            ("N", 1, 1),
            ("N1", 3, 3),
            ("N1a", 2, 2),
            ("I", 3, 3),
        ]
        assert (mt.defining_snps_present, mt.defining_snps_total) == (10, 10)
        assert mt.confidence == 1.0

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "genotype_16129"),
        [
            pytest.param("missing", None, id="missing-m16129"),
            pytest.param("no-call", "--", id="no-call-m16129"),
        ],
    )
    def test_issue_1899_i_tolerates_one_untyped_direct_marker(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        genotype_16129: str | None,
    ) -> None:
        """Missing and explicit no-call m.16129 are lack of evidence, not conflict."""
        rows = [row for row in _MT_I_GENOTYPES if int(row["pos"]) != 16129]
        if genotype_16129 is not None:
            rows.append(
                {
                    "rsid": "i5016129",
                    "chrom": "MT",
                    "pos": 16129,
                    "genotype": genotype_16129,
                }
            )
        vendor_rows = [
            {**row, "rsid": f"vendor_issue_1899_{case}_{index}"} for index, row in enumerate(rows)
        ]
        assert not ({str(row["rsid"]) for row in vendor_rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), vendor_rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "I"
        assert [
            (step.haplogroup, step.snps_present, step.snps_total) for step in mt.traversal_path
        ] == [
            ("L3", 1, 1),
            ("N", 1, 1),
            ("N1", 3, 3),
            ("N1a", 2, 2),
            ("I", 2, 3),
        ]
        assert (mt.defining_snps_present, mt.defining_snps_total) == (9, 10)
        assert mt.confidence == 0.9

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1899_two_missing_path_markers_still_assign_i(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """One missing marker at N1a and I still leaves sufficient path evidence."""
        rows = [
            {**row, "rsid": f"vendor_issue_1899_sparse_path_{index}"}
            for index, row in enumerate(_MT_I_GENOTYPES)
            if int(row["pos"]) not in {204, 16129}
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "I"
        assert [
            (step.haplogroup, step.snps_present, step.snps_total) for step in mt.traversal_path
        ] == [
            ("L3", 1, 1),
            ("N", 1, 1),
            ("N1", 3, 3),
            ("N1a", 1, 2),
            ("I", 2, 3),
        ]
        assert (mt.defining_snps_present, mt.defining_snps_total) == (8, 10)
        assert mt.confidence == 0.8

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("position", "ancestral_genotype"),
        [
            pytest.param(10034, "TT", id="ancestral-m10034"),
            pytest.param(15043, "GG", id="ancestral-m15043"),
            pytest.param(16129, "GG", id="ancestral-m16129"),
        ],
    )
    def test_issue_1899_ancestral_i_marker_stops_at_n1a(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        position: int,
        ancestral_genotype: str,
    ) -> None:
        """A typed ancestral I locus is a hard boundary, not partial I support."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1899_ancestral_i_{index}",
                "genotype": (
                    ancestral_genotype if int(row["pos"]) == position else row["genotype"]
                ),
            }
            for index, row in enumerate(_MT_I_GENOTYPES)
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "N1a"
        assert [
            (step.haplogroup, step.snps_present, step.snps_total) for step in mt.traversal_path
        ] == [("L3", 1, 1), ("N", 1, 1), ("N1", 3, 3), ("N1a", 2, 2)]
        assert (mt.defining_snps_present, mt.defining_snps_total) == (7, 7)
        assert mt.confidence == 1.0

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("position", "ancestral_genotype", "expected", "expected_steps"),
        [
            pytest.param(
                1719,
                "GG",
                "N",
                [("L3", 1, 1), ("N", 1, 1)],
                id="ancestral-n1-m1719",
            ),
            pytest.param(
                204,
                "TT",
                "N1",
                [("L3", 1, 1), ("N", 1, 1), ("N1", 3, 3)],
                id="ancestral-n1a-m204",
            ),
        ],
    )
    def test_issue_1899_i_cannot_bypass_ancestral_spine_marker(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        position: int,
        ancestral_genotype: str,
        expected: str,
        expected_steps: list[tuple[str, int, int]],
    ) -> None:
        """Downstream I evidence cannot bypass a typed ancestral N1/N1a marker."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1899_ancestral_spine_{index}",
                "genotype": (
                    ancestral_genotype if int(row["pos"]) == position else row["genotype"]
                ),
            }
            for index, row in enumerate(_MT_I_GENOTYPES)
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [
            (step.haplogroup, step.snps_present, step.snps_total) for step in mt.traversal_path
        ] == expected_steps
        assert (mt.defining_snps_present, mt.defining_snps_total) == (
            sum(step[1] for step in expected_steps),
            sum(step[2] for step in expected_steps),
        )
        assert mt.confidence == 1.0

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "no_call"),
        [
            pytest.param("missing", False, id="missing-n1a-motif"),
            pytest.param("no-call", True, id="no-call-n1a-motif"),
        ],
    )
    def test_issue_1899_untyped_n1a_motif_stops_at_n1(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        no_call: bool,
    ) -> None:
        """I evidence cannot skip N1a when both of its direct markers are untyped."""
        rows = [
            {
                **row,
                "genotype": (
                    "--" if no_call and int(row["pos"]) in {204, 13780} else row["genotype"]
                ),
            }
            for row in _MT_I_GENOTYPES
            if no_call or int(row["pos"]) not in {204, 13780}
        ]
        vendor_rows = [
            {**row, "rsid": f"vendor_issue_1899_{case}_n1a_{index}"}
            for index, row in enumerate(rows)
        ]
        assert not ({str(row["rsid"]) for row in vendor_rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), vendor_rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "N1"
        assert [
            (step.haplogroup, step.snps_present, step.snps_total) for step in mt.traversal_path
        ] == [("L3", 1, 1), ("N", 1, 1), ("N1", 3, 3)]
        assert (mt.defining_snps_present, mt.defining_snps_total) == (5, 5)
        assert mt.confidence == 1.0

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1899_old_flattened_i_fixture_stops_at_n(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """The former N-child fixture cannot skip the newly explicit N1/N1a spine."""
        old_flattened_rows = [
            *_MT_N_TRUNK_GENOTYPES,
            {"rsid": "i5001719", "chrom": "MT", "pos": 1719, "genotype": "AA"},
            {"rsid": "i5010034", "chrom": "MT", "pos": 10034, "genotype": "CC"},
            {"rsid": "i5015043", "chrom": "MT", "pos": 15043, "genotype": "AA"},
            {"rsid": "i5016129", "chrom": "MT", "pos": 16129, "genotype": "AA"},
        ]
        rows = [
            {**row, "rsid": f"vendor_issue_1899_old_flattened_{index}"}
            for index, row in enumerate(old_flattened_rows)
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "N"
        assert [
            (step.haplogroup, step.snps_present, step.snps_total) for step in mt.traversal_path
        ] == [("L3", 1, 1), ("N", 1, 1)]
        assert (mt.defining_snps_present, mt.defining_snps_total) == (2, 2)
        assert mt.confidence == 1.0

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("node_rows", "expected", "expected_path"),
        [
            pytest.param(
                [
                    {"pos": 4715, "genotype": "GG"},
                    {"pos": 8584, "genotype": "AA"},
                    {"pos": 15487, "genotype": "TT"},
                ],
                "M8",
                ["L3", "M", "M8"],
                id="M8",
            ),
            pytest.param(
                [
                    {"pos": 4715, "genotype": "GG"},
                    {"pos": 8584, "genotype": "AA"},
                    {"pos": 15487, "genotype": "TT"},
                    {"pos": 6179, "genotype": "AA"},
                    {"pos": 8684, "genotype": "TT"},
                    {"pos": 14470, "genotype": "CC"},
                ],
                "M8a",
                ["L3", "M", "M8", "M8a"],
                id="M8a",
            ),
            pytest.param(
                [
                    {"pos": 4715, "genotype": "GG"},
                    {"pos": 8584, "genotype": "AA"},
                    {"pos": 15487, "genotype": "TT"},
                    {"pos": 3552, "genotype": "AA"},
                    {"pos": 9545, "genotype": "GG"},
                    {"pos": 11914, "genotype": "AA"},
                    {"pos": 13263, "genotype": "GG"},
                    {"pos": 14318, "genotype": "CC"},
                ],
                "C",
                ["L3", "M", "M8", "C"],
                id="C-via-M8",
            ),
            pytest.param(
                [
                    {"pos": 4715, "genotype": "GG"},
                    {"pos": 8584, "genotype": "AA"},
                    {"pos": 15487, "genotype": "TT"},
                    {"pos": 6752, "genotype": "GG"},
                    {"pos": 9090, "genotype": "CC"},
                    {"pos": 15784, "genotype": "CC"},
                ],
                "Z",
                ["L3", "M", "M8", "Z"],
                id="Z-via-M8",
            ),
        ],
    )
    def test_issue_1797_m8_descendants_resolve_by_position(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        node_rows: list[dict[str, object]],
        expected: str,
        expected_path: list[str],
    ) -> None:
        """M8, M8a, C, and Z follow their Build-17 ancestry in both tables."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1797_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*_MT_M_TRUNK_GENOTYPES, *node_rows])
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == expected_path

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("node_rows", "case"),
        [
            pytest.param(
                [
                    {"pos": 3552, "genotype": "AA"},
                    {"pos": 9545, "genotype": "GG"},
                    {"pos": 11914, "genotype": "AA"},
                    {"pos": 13263, "genotype": "GG"},
                    {"pos": 14318, "genotype": "CC"},
                ],
                "C",
                id="C-without-M8",
            ),
            pytest.param(
                [
                    {"pos": 6752, "genotype": "GG"},
                    {"pos": 9090, "genotype": "CC"},
                    {"pos": 15784, "genotype": "CC"},
                ],
                "Z",
                id="Z-without-M8",
            ),
            pytest.param(
                [
                    {"pos": 7196, "genotype": "AA"},
                    {"pos": 8684, "genotype": "TT"},
                    {"pos": 15487, "genotype": "TT"},
                ],
                "legacy-M8-markers",
                id="legacy-M8-markers",
            ),
        ],
    )
    def test_issue_1797_requires_current_m8_ancestral_evidence(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        node_rows: list[dict[str, object]],
        case: str,
    ) -> None:
        """Direct descendants and obsolete markers cannot bypass current M8."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1797_negative_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*_MT_M_TRUNK_GENOTYPES, *node_rows])
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "M"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "M"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("target", "trunk", "node_rows", "expected_path"),
        [
            pytest.param(
                "N9",
                _derived_mt_path_genotypes("N"),
                [
                    {"pos": 5417, "genotype": "AA"},
                    {"pos": 12705, "genotype": "TT"},
                ],
                ["L3", "N", "N9"],
                id="real-N9-with-ancestral-12705T",
            ),
            pytest.param(
                "R",
                _derived_mt_path_genotypes("N"),
                [{"pos": 12705, "genotype": "CC"}],
                ["L3", "N", "R"],
                id="sparse-R-does-not-divert-to-N9",
            ),
        ],
    )
    def test_issue_1808_cross_clade_12705_does_not_block_or_divert_assignment(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
        trunk: list[dict[str, object]],
        node_rows: list[dict[str, object]],
        expected_path: list[str],
    ) -> None:
        """Real N9 resolves and sparse R evidence stays on the R branch."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1808_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*trunk, *node_rows])
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == target
        assert [step.haplogroup for step in mt.traversal_path] == expected_path

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "node_rows", "expected_terminal_counts"),
        [
            pytest.param(
                "exact",
                [
                    {"pos": 8703, "genotype": "TT"},
                    {"pos": 12705, "genotype": "TT"},
                    {"pos": 16129, "genotype": "AA"},
                ],
                (2, 2),
                id="exact-2-of-2-with-ancestral-R-locus",
            ),
            pytest.param(
                "missing-8703",
                [
                    {"pos": 12705, "genotype": "TT"},
                    {"pos": 16129, "genotype": "AA"},
                ],
                (1, 2),
                id="missing-8703-1-of-2",
            ),
            pytest.param(
                "missing-16129",
                [
                    {"pos": 8703, "genotype": "TT"},
                    {"pos": 12705, "genotype": "TT"},
                ],
                (1, 2),
                id="missing-16129-1-of-2",
            ),
        ],
    )
    def test_issue_1907_d2_exact_and_partial_motifs_assign_by_position(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        node_rows: list[dict[str, object]],
        expected_terminal_counts: tuple[int, int],
    ) -> None:
        """D2 resolves below its repaired D4 parent through either position join."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1907_positive_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*_derived_mt_path_genotypes("D4"), *node_rows])
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "D2"
        assert [step.haplogroup for step in mt.traversal_path] == [
            "L3",
            "M",
            "D",
            "D4",
            "D2",
        ]
        terminal = mt.traversal_path[-1]
        assert (terminal.snps_present, terminal.snps_total) == expected_terminal_counts

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "trunk_target", "node_rows", "expected", "expected_path"),
        [
            pytest.param(
                "ancestral-8703",
                "D4",
                [
                    {"pos": 8703, "genotype": "CC"},
                    {"pos": 12705, "genotype": "TT"},
                    {"pos": 16129, "genotype": "AA"},
                ],
                "D4",
                ["L3", "M", "D", "D4"],
                id="ancestral-8703-blocks",
            ),
            pytest.param(
                "ancestral-16129",
                "D4",
                [
                    {"pos": 8703, "genotype": "TT"},
                    {"pos": 12705, "genotype": "TT"},
                    {"pos": 16129, "genotype": "GG"},
                ],
                "D4",
                ["L3", "M", "D", "D4"],
                id="ancestral-16129-blocks",
            ),
            pytest.param(
                "legacy-pair",
                "D4",
                [{"pos": 12705, "genotype": "CC"}],
                "D4",
                ["L3", "M", "D", "D4"],
                id="legacy-4883-and-12705-stop-D4",
            ),
            pytest.param(
                "cross-clade-with-new-loci-ancestral",
                "D4",
                [
                    {"pos": 8703, "genotype": "CC"},
                    {"pos": 12705, "genotype": "CC"},
                    {"pos": 16129, "genotype": "GG"},
                ],
                "D4",
                ["L3", "M", "D", "D4"],
                id="R-locus-cannot-divert-non-D2-D4",
            ),
            pytest.param(
                "ungated",
                "M",
                [
                    {"pos": 8703, "genotype": "TT"},
                    {"pos": 12705, "genotype": "TT"},
                    {"pos": 16129, "genotype": "AA"},
                ],
                "M",
                ["L3", "M"],
                id="leaf-pair-cannot-bypass-D",
            ),
        ],
    )
    def test_issue_1907_ancestral_legacy_and_ungated_controls_stop_descent(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        trunk_target: str,
        node_rows: list[dict[str, object]],
        expected: str,
        expected_path: list[str],
    ) -> None:
        """Typed ancestry, the obsolete pair, and an absent D gateway reject D2."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1907_negative_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*_derived_mt_path_genotypes(trunk_target), *node_rows])
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == expected_path

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("target", "trunk", "node_rows", "expected_path"),
        [
            pytest.param(
                "L2c",
                _derived_mt_path_genotypes("L2"),
                [
                    {"pos": 93, "genotype": "GG"},
                    {"pos": 325, "genotype": "TT"},
                    {"pos": 680, "genotype": "CC"},
                    {"pos": 3200, "genotype": "AA"},
                    {"pos": 13928, "genotype": "CC"},
                    {"pos": 13958, "genotype": "CC"},
                    {"pos": 15849, "genotype": "TT"},
                ],
                ["L2", "L2c"],
                id="L2c",
            ),
            pytest.param(
                "M1",
                _derived_mt_path_genotypes("M"),
                [
                    {"pos": 14110, "genotype": "CC"},
                    {"pos": 6446, "genotype": "AA"},
                    {"pos": 6680, "genotype": "CC"},
                    {"pos": 12950, "genotype": "CC"},
                    {"pos": 16129, "genotype": "AA"},
                    {"pos": 16249, "genotype": "CC"},
                ],
                ["L3", "M", "M1"],
                id="M1",
            ),
            pytest.param(
                "S",
                _derived_mt_path_genotypes("N"),
                [{"pos": 8404, "genotype": "CC"}],
                ["L3", "N", "S"],
                id="S",
            ),
            pytest.param(
                "X",
                _derived_mt_path_genotypes("N"),
                [
                    {"pos": 6221, "genotype": "CC"},
                    {"pos": 6371, "genotype": "TT"},
                    {"pos": 13966, "genotype": "GG"},
                    {"pos": 14470, "genotype": "CC"},
                ],
                ["L3", "N", "X"],
                id="X",
            ),
            pytest.param(
                "H10",
                _derived_mt_path_genotypes("H"),
                [{"pos": 14470, "genotype": "AA"}],
                ["L3", "N", "R", "R0", "HV", "H", "H10"],
                id="H10",
            ),
        ],
    )
    def test_issue_1798_direct_motifs_assign_by_position(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
        trunk: list[dict[str, object]],
        node_rows: list[dict[str, object]],
        expected_path: list[str],
    ) -> None:
        """Each corrected motif resolves through raw and annotated position joins."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*trunk, *node_rows])
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == target
        assert [step.haplogroup for step in mt.traversal_path] == expected_path

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "trunk", "node_rows", "expected", "expected_path"),
        [
            pytest.param(
                "L2c",
                _derived_mt_path_genotypes("L2"),
                [
                    {"pos": 93, "genotype": "GG"},
                    {"pos": 325, "genotype": "TT"},
                    {"pos": 680, "genotype": "CC"},
                    {"pos": 13928, "genotype": "CC"},
                    {"pos": 13958, "genotype": "TT"},
                    {"pos": 15849, "genotype": "TT"},
                ],
                "L2",
                ["L2"],
                id="L2c-old-G13958T",
            ),
            pytest.param(
                "M1",
                _derived_mt_path_genotypes("M"),
                [
                    {"pos": 14110, "genotype": "CC"},
                    {"pos": 6446, "genotype": "GG"},
                    {"pos": 6680, "genotype": "CC"},
                    {"pos": 12950, "genotype": "CC"},
                    {"pos": 16129, "genotype": "AA"},
                    {"pos": 16249, "genotype": "CC"},
                ],
                "M",
                ["L3", "M"],
                id="M1-old-G6446G",
            ),
            pytest.param(
                "S",
                _derived_mt_path_genotypes("N"),
                [{"pos": 8404, "genotype": "TT"}],
                "N",
                ["L3", "N"],
                id="S-old-T8404T",
            ),
            pytest.param(
                "X",
                _derived_mt_path_genotypes("N"),
                [
                    {"pos": 6221, "genotype": "CC"},
                    {"pos": 6371, "genotype": "CC"},
                    {"pos": 13966, "genotype": "GG"},
                    {"pos": 14470, "genotype": "CC"},
                ],
                "N",
                ["L3", "N"],
                id="X-old-C6371C",
            ),
            pytest.param(
                "H10",
                _derived_mt_path_genotypes("H"),
                [{"pos": 14470, "genotype": "CC"}],
                "H",
                ["L3", "N", "R", "R0", "HV", "H"],
                id="H10-old-T14470C",
            ),
        ],
    )
    def test_issue_1798_old_wrong_alleles_block_descent(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        trunk: list[dict[str, object]],
        node_rows: list[dict[str, object]],
        expected: str,
        expected_path: list[str],
    ) -> None:
        """A pre-fix allele conflicts even when the rest of the motif is derived."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_old_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*trunk, *node_rows])
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == expected_path

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("target", "child_rows", "expected_path", "expected_counts"),
        [
            pytest.param(
                "S1",
                [
                    {"pos": 14384, "genotype": "CC"},
                    {"pos": 16075, "genotype": "CC"},
                ],
                ["L3", "N", "S", "S1"],
                (2, 2),
                id="S1",
            ),
            pytest.param(
                "S2",
                [
                    {"pos": 2380, "genotype": "TT"},
                    {"pos": 3438, "genotype": "AA"},
                    {"pos": 6167, "genotype": "CC"},
                ],
                ["L3", "N", "S", "S2"],
                (3, 3),
                id="S2",
            ),
        ],
    )
    def test_issue_1814_s_children_assign_by_direct_motif_and_position(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
        child_rows: list[dict[str, object]],
        expected_path: list[str],
        expected_counts: tuple[int, int],
    ) -> None:
        """Raw and annotated position joins resolve both exact children of S."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1814_positive_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("N"),
                    {"pos": 8404, "genotype": "CC"},
                    *child_rows,
                ]
            )
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == target
        assert [step.haplogroup for step in mt.traversal_path] == expected_path
        terminal = mt.traversal_path[-1]
        assert (terminal.snps_present, terminal.snps_total) == expected_counts

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "legacy_row"),
        [
            pytest.param(
                "S1",
                {"pos": 10238, "genotype": "CC"},
                id="S1-legacy-10238C",
            ),
            pytest.param(
                "S2",
                {"pos": 14364, "genotype": "TT"},
                id="S2-legacy-14364T",
            ),
        ],
    )
    def test_issue_1814_legacy_child_markers_stop_at_s(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        legacy_row: dict[str, object],
    ) -> None:
        """Neither unsupported pre-fix position can refine a real S lineage."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1814_legacy_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("N"),
                    {"pos": 8404, "genotype": "CC"},
                    legacy_row,
                ]
            )
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "S"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "N", "S"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "child_rows"),
        [
            pytest.param(
                "S1",
                [
                    {"pos": 14384, "genotype": "CC"},
                    {"pos": 16075, "genotype": "CC"},
                ],
                id="S1-without-S",
            ),
            pytest.param(
                "S2",
                [
                    {"pos": 2380, "genotype": "TT"},
                    {"pos": 3438, "genotype": "AA"},
                    {"pos": 6167, "genotype": "CC"},
                ],
                id="S2-without-S",
            ),
        ],
    )
    def test_issue_1814_child_markers_cannot_bypass_s_parent(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        child_rows: list[dict[str, object]],
    ) -> None:
        """Complete child motifs cannot jump an untyped m.8404 S gateway."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1814_parent_gate_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*_derived_mt_path_genotypes("N"), *child_rows])
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "N"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "N"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1849_h1a_assigns_by_exact_direct_motif_and_position(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """Raw and annotated position joins resolve exact H1a below H1."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1849_positive_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("H1"),
                    # The former markers are ancestral and must not matter.
                    {"pos": 13290, "genotype": "CC"},
                    {"pos": 13404, "genotype": "TT"},
                    {"pos": 73, "genotype": "GG"},
                    {"pos": 16162, "genotype": "GG"},
                ]
            )
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "H1a"
        assert [step.haplogroup for step in mt.traversal_path] == [
            "L3",
            "N",
            "R",
            "R0",
            "HV",
            "H",
            "H1",
            "H1a",
        ]
        terminal = mt.traversal_path[-1]
        assert (terminal.snps_present, terminal.snps_total) == (2, 2)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "h1a_rows", "expected", "expected_counts"),
        [
            pytest.param(
                "missing-m73",
                [{"pos": 16162, "genotype": "GG"}],
                "H1a",
                (1, 2),
                id="missing-m73-still-resolves",
            ),
            pytest.param(
                "ancestral-m16162",
                [
                    {"pos": 73, "genotype": "GG"},
                    {"pos": 16162, "genotype": "AA"},
                ],
                "H1",
                None,
                id="ancestral-m16162-blocks",
            ),
        ],
    )
    def test_issue_1849_h1a_partial_coverage_and_conflict_behavior(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        h1a_rows: list[dict[str, object]],
        expected: str,
        expected_counts: tuple[int, int] | None,
    ) -> None:
        """A missing m.73 is tolerated, while an ancestral typed marker conflicts."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1849_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*_derived_mt_path_genotypes("H1"), *h1a_rows])
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        if expected_counts is not None:
            terminal = mt.traversal_path[-1]
            assert terminal.haplogroup == "H1a"
            assert (terminal.snps_present, terminal.snps_total) == expected_counts

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1849_legacy_h1a_markers_stop_at_h1(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """The unsupported pre-correction pair cannot refine H1 to H1a."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1849_legacy_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("H1"),
                    {"pos": 13290, "genotype": "TT"},
                    {"pos": 13404, "genotype": "CC"},
                    {"pos": 73, "genotype": "AA"},
                    {"pos": 16162, "genotype": "AA"},
                ]
            )
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "H1"
        assert [step.haplogroup for step in mt.traversal_path] == [
            "L3",
            "N",
            "R",
            "R0",
            "HV",
            "H",
            "H1",
        ]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1849_h1a_markers_cannot_bypass_h1_parent(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """The complete H1a motif cannot jump an ancestral m.3010 H1 gate."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1849_parent_gate_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("H"),
                    {"pos": 3010, "genotype": "GG"},
                    {"pos": 73, "genotype": "GG"},
                    {"pos": 16162, "genotype": "GG"},
                ]
            )
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "H"
        assert [step.haplogroup for step in mt.traversal_path] == [
            "L3",
            "N",
            "R",
            "R0",
            "HV",
            "H",
        ]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("target", "node_rows", "expected_path", "expected_counts"),
        [
            pytest.param(
                "W",
                [
                    {"pos": 189, "genotype": "AA"},
                    {"pos": 195, "genotype": "CC"},
                    {"pos": 204, "genotype": "CC"},
                    *_W_DIRECT_POSITION_GENOTYPES,
                ],
                ["L3", "N", "W"],
                (9, 9),
                id="W",
            ),
            pytest.param(
                "W3",
                [*_W_DIRECT_POSITION_GENOTYPES, {"pos": 1406, "genotype": "CC"}],
                ["L3", "N", "W", "W3"],
                (1, 1),
                id="W3",
            ),
        ],
    )
    def test_issue_1834_w_and_w3_assign_by_exact_direct_motifs(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
        node_rows: list[dict[str, object]],
        expected_path: list[str],
        expected_counts: tuple[int, int],
    ) -> None:
        """Exact motifs resolve W/W3 without treating ancestral m.189 as conflicting."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1834_positive_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*_derived_mt_path_genotypes("N"), *node_rows])
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == target
        assert [step.haplogroup for step in mt.traversal_path] == expected_path
        terminal = mt.traversal_path[-1]
        assert (terminal.snps_present, terminal.snps_total) == expected_counts

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "node_rows", "expected", "expected_path"),
        [
            pytest.param(
                "W-legacy-189-204",
                [
                    {"pos": 189, "genotype": "GG"},
                    {"pos": 204, "genotype": "CC"},
                    {"pos": 207, "genotype": "AA"},
                    {"pos": 1243, "genotype": "CC"},
                ],
                "N",
                ["L3", "N"],
                id="W-legacy-189-204",
            ),
            pytest.param(
                "W3-legacy-5460",
                _W_DIRECT_POSITION_GENOTYPES,
                "W",
                ["L3", "N", "W"],
                id="W3-legacy-5460",
            ),
        ],
    )
    def test_issue_1834_legacy_markers_cannot_restore_old_w_assignments(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        node_rows: list[dict[str, object]],
        expected: str,
        expected_path: list[str],
    ) -> None:
        """The old W motif stops at N, and m.5460 alone cannot refine W to W3."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1834_legacy_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*_derived_mt_path_genotypes("N"), *node_rows])
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == expected_path

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "node_rows"),
        [
            pytest.param("W", _W_DIRECT_POSITION_GENOTYPES, id="W-without-N"),
            pytest.param(
                "W3",
                [*_W_DIRECT_POSITION_GENOTYPES, {"pos": 1406, "genotype": "CC"}],
                id="W3-without-N",
            ),
        ],
    )
    def test_issue_1834_w_markers_cannot_bypass_n_parent(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        node_rows: list[dict[str, object]],
    ) -> None:
        """Complete W or W3 motifs cannot jump an untyped N gateway."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1834_parent_gate_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*_derived_mt_path_genotypes("L3"), *node_rows])
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "L3"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("selected_positions", "expected", "expected_path", "expected_counts"),
        [
            pytest.param(
                {6680, 12950, 14110, 16129, 16249},
                "M1",
                ["L3", "M", "M1"],
                (5, 6),
                id="missing-6446",
            ),
            pytest.param(
                {6446, 6680, 12950, 14110, 16249},
                "M1",
                ["L3", "M", "M1"],
                (5, 6),
                id="missing-16129",
            ),
            pytest.param(
                {6446, 6680, 12950, 14110, 16129},
                "M1",
                ["L3", "M", "M1"],
                (5, 6),
                id="missing-16249",
            ),
            pytest.param(
                {6446, 14110, 16129, 16249},
                "M1",
                ["L3", "M", "M1"],
                (4, 6),
                id="four-of-six",
            ),
            pytest.param(
                {6446, 14110, 16129},
                "M1",
                ["L3", "M", "M1"],
                (3, 6),
                id="three-of-six",
            ),
            pytest.param(
                {6446, 14110},
                "M",
                ["L3", "M"],
                None,
                id="two-of-six",
            ),
        ],
    )
    def test_issue_1798_m1_remains_assignable_with_primary_array_gaps(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        selected_positions: set[int],
        expected: str,
        expected_path: list[str],
        expected_counts: tuple[int, int] | None,
    ) -> None:
        """Observed one-marker gaps and the production descent threshold stay safe."""
        allele_by_position = {
            6446: "A",
            6680: "C",
            12950: "C",
            14110: "C",
            16129: "A",
            16249: "C",
        }
        node_rows = [
            {
                "rsid": f"vendor_issue_1798_m1_{position}",
                "chrom": "MT",
                "pos": position,
                "genotype": allele_by_position[position] * 2,
            }
            for position in sorted(selected_positions)
        ]
        rows = [
            {**row, "rsid": f"vendor_issue_1798_m1_path_{index}"}
            for index, row in enumerate([*_derived_mt_path_genotypes("M"), *node_rows])
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == expected_path
        if expected_counts is not None:
            terminal = mt.traversal_path[-1]
            assert (terminal.snps_present, terminal.snps_total) == expected_counts

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("trunk", "node_rows", "expected"),
        [
            pytest.param(
                _MT_K1_REVERSAL_GENOTYPES,
                [{"rsid": "placeholder", "chrom": "MT", "pos": 5913, "genotype": "AA"}],
                "K1b",
                id="K1b",
            ),
            pytest.param(
                _MT_U5B_TRUNK_GENOTYPES,
                [
                    {"rsid": "placeholder", "chrom": "MT", "pos": 1721, "genotype": "TT"},
                    {
                        "rsid": "placeholder",
                        "chrom": "MT",
                        "pos": 13637,
                        "genotype": "GG",
                    },
                ],
                "U5b2",
                id="U5b2",
            ),
            pytest.param(
                _MT_W_TRUNK_GENOTYPES,
                [{"rsid": "placeholder", "chrom": "MT", "pos": 7864, "genotype": "TT"}],
                "W1",
                id="W1",
            ),
            pytest.param(
                _MT_U3_TRUNK_GENOTYPES,
                [
                    {"rsid": "placeholder", "chrom": "MT", "pos": 4188, "genotype": "GG"},
                    {"rsid": "placeholder", "chrom": "MT", "pos": 9656, "genotype": "CC"},
                    {
                        "rsid": "placeholder",
                        "chrom": "MT",
                        "pos": 13743,
                        "genotype": "CC",
                    },
                ],
                "U3b",
                id="U3b",
            ),
            pytest.param(
                _MT_U3_TRUNK_GENOTYPES,
                [
                    {"rsid": "placeholder", "chrom": "MT", "pos": 6518, "genotype": "TT"},
                    {
                        "rsid": "placeholder",
                        "chrom": "MT",
                        "pos": 10506,
                        "genotype": "GG",
                    },
                    {
                        "rsid": "placeholder",
                        "chrom": "MT",
                        "pos": 13934,
                        "genotype": "TT",
                    },
                    {
                        "rsid": "placeholder",
                        "chrom": "MT",
                        "pos": 16390,
                        "genotype": "AA",
                    },
                ],
                "U3a",
                id="U3a",
            ),
        ],
    )
    def test_corrected_mt_nodes_match_direct_derived_motifs_by_position(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        trunk: list[dict[str, object]],
        node_rows: list[dict[str, object]],
        expected: str,
    ) -> None:
        """#1742/#1794/#1796: direct motifs resolve through both production MT tables."""
        rows = [
            {**row, "rsid": f"vendor_mt_{index}"} for index, row in enumerate([*trunk, *node_rows])
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        results = assign_haplogroups(bundle, sample_engine)

        mt = next(result for result in results if result.tree_type == "mt")
        assert mt.haplogroup == expected

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_remote_m3834_homoplasy_does_not_refine_u3_to_u3a(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """#1794: U9/Y1 homoplasy m.3834 is not a direct U3a marker."""
        rows = [
            {**row, "rsid": f"vendor_u3_{index}"}
            for index, row in enumerate(
                [
                    *_MT_U3_TRUNK_GENOTYPES,
                    {"rsid": "placeholder", "chrom": "MT", "pos": 3834, "genotype": "AA"},
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        results = assign_haplogroups(bundle, sample_engine)

        mt = next(result for result in results if result.tree_type == "mt")
        assert mt.haplogroup == "U3"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "N", "R", "U", "U3"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_m14167_does_not_refine_k1_to_k1b(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """#1796: m.14167 is not the direct Build 17 K1b marker."""
        rows = [
            {**row, "rsid": f"vendor_k1_{index}"}
            for index, row in enumerate(
                [
                    *_MT_K1_REVERSAL_GENOTYPES,
                    {"rsid": "placeholder", "chrom": "MT", "pos": 14167, "genotype": "TT"},
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        results = assign_haplogroups(bundle, sample_engine)

        mt = next(result for result in results if result.tree_type == "mt")
        assert mt.haplogroup == "K1"
        assert [step.haplogroup for step in mt.traversal_path] == [
            "L3",
            "N",
            "R",
            "U",
            "U8",
            "U8b",
            "K",
            "K1",
        ]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("trunk", "node_rows", "expected", "expected_path"),
        [
            pytest.param(
                _derived_mt_path_genotypes("T2"),
                [{"rsid": "placeholder", "chrom": "MT", "pos": 13965, "genotype": "CC"}],
                "T2a",
                ["L3", "N", "R", "JT", "T", "T2", "T2a"],
                id="T2a-T13965C",
            ),
            pytest.param(
                _derived_mt_path_genotypes("U2"),
                [
                    {"rsid": "placeholder", "chrom": "MT", "pos": 508, "genotype": "GG"},
                    {
                        "rsid": "placeholder",
                        "chrom": "MT",
                        "pos": 3720,
                        "genotype": "GG",
                    },
                    {
                        "rsid": "placeholder",
                        "chrom": "MT",
                        "pos": 5390,
                        "genotype": "GG",
                    },
                    {
                        "rsid": "placeholder",
                        "chrom": "MT",
                        "pos": 6045,
                        "genotype": "TT",
                    },
                    {
                        "rsid": "placeholder",
                        "chrom": "MT",
                        "pos": 13020,
                        "genotype": "CC",
                    },
                ],
                "U2e",
                ["L3", "N", "R", "U", "U2", "U2e"],
                id="U2e-five-of-ten-direct-callable-events",
            ),
        ],
    )
    def test_issue_1743_mt_subclades_resolve_by_direct_motif_and_position(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        trunk: list[dict[str, object]],
        node_rows: list[dict[str, object]],
        expected: str,
        expected_path: list[str],
    ) -> None:
        """T2a/U2e witnesses resolve through both production tables using vendor ids."""
        rows = [
            {**row, "rsid": f"vendor_issue_1743_{index}"}
            for index, row in enumerate([*trunk, *node_rows])
        ]
        assert not ({str(row["rsid"]) for row in rows} & bundle.mt_snp_rsids)

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        results = assign_haplogroups(bundle, sample_engine)

        mt = next(result for result in results if result.tree_type == "mt")
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == expected_path

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("trunk", "obsolete_row", "expected"),
        [
            pytest.param(
                _MT_U5B_TRUNK_GENOTYPES,
                {"rsid": "placeholder", "chrom": "MT", "pos": 1721, "genotype": "CC"},
                "U5b",
                id="ancestral-1721-does-not-call-U5b2",
            ),
            pytest.param(
                _MT_W_TRUNK_GENOTYPES,
                {"rsid": "placeholder", "chrom": "MT", "pos": 12669, "genotype": "CC"},
                "W",
                id="ancestral-12669-does-not-call-W1",
            ),
            pytest.param(
                _MT_U3_TRUNK_GENOTYPES,
                {"rsid": "placeholder", "chrom": "MT", "pos": 9266, "genotype": "GG"},
                "U3",
                id="ancestral-9266-does-not-call-U3b",
            ),
        ],
    )
    def test_obsolete_ancestral_mt_markers_stop_at_the_parent(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        trunk: list[dict[str, object]],
        obsolete_row: dict[str, object],
        expected: str,
    ) -> None:
        """#1742: ancestral and wrong-node records cannot trigger child calls."""
        rows = [
            {**row, "rsid": f"vendor_mt_{index}"}
            for index, row in enumerate([*trunk, obsolete_row])
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        results = assign_haplogroups(bundle, sample_engine)

        mt = next(result for result in results if result.tree_type == "mt")
        assert mt.haplogroup == expected

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("trunk", "node_rows", "expected"),
        [
            pytest.param(
                _MT_U5B_TRUNK_GENOTYPES,
                [{"rsid": "placeholder", "chrom": "MT", "pos": 1721, "genotype": "TT"}],
                "U5b2",
                id="U5b2-missing-13637",
            ),
            pytest.param(
                _MT_U3_TRUNK_GENOTYPES,
                [
                    {"rsid": "placeholder", "chrom": "MT", "pos": 4188, "genotype": "GG"},
                    {"rsid": "placeholder", "chrom": "MT", "pos": 9656, "genotype": "CC"},
                ],
                "U3b",
                id="U3b-missing-13743",
            ),
        ],
    )
    def test_corrected_mt_nodes_allow_missing_array_co_markers(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        trunk: list[dict[str, object]],
        node_rows: list[dict[str, object]],
        expected: str,
    ) -> None:
        """Typed direct evidence still resolves when an array omits one co-marker."""
        rows = [
            {**row, "rsid": f"vendor_mt_{index}"} for index, row in enumerate([*trunk, *node_rows])
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        results = assign_haplogroups(bundle, sample_engine)

        mt = next(result for result in results if result.tree_type == "mt")
        assert mt.haplogroup == expected

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

        Position 16223 is one of R's two markers in the H1a fixture. A second,
        non-alias probe at that coordinate with an ancestral call makes the
        coordinate missing, while m.12705 still clears R's 1/2 descent floor.
        """
        conflict = {
            "rsid": "i_conflict_16223",
            "chrom": "MT",
            "pos": 16223,
            "genotype": "TT",
        }
        rows = [conflict, *_H1A_GENOTYPES] if conflict_first else [*_H1A_GENOTYPES, conflict]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        results = assign_haplogroups(bundle, sample_engine)

        assert len(results) == 1
        mt = results[0]
        assert mt.tree_type == "mt"
        assert mt.haplogroup == "H1a"
        # Nine H1a-path defining SNPs: R0/HV remain markerless, H now owns two
        # direct events, and H1a legitimately reintroduces m.73 as a back
        # mutation. Ambiguous m.16223 is missing, so eight of nine are present.
        assert mt.defining_snps_present == 8
        assert mt.defining_snps_total == 9

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("conflict_first", [False, True])
    @pytest.mark.parametrize(
        ("target", "position", "ancestral_genotype", "expected", "expected_path"),
        [
            pytest.param("N", 9540, "CC", "L3", ["L3"], id="N-m9540"),
            pytest.param("N9", 5417, "GG", "N", ["L3", "N"], id="N9-m5417"),
        ],
    )
    def test_issue_1798_batch06_gateway_duplicate_discordance_fails_closed(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        conflict_first: bool,
        target: str,
        position: int,
        ancestral_genotype: str,
        expected: str,
        expected_path: list[str],
    ) -> None:
        """Ambiguity at N's or N9's sole marker cannot be bypassed."""
        derived_rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch06_duplicate_{target}_{index}",
            }
            for index, row in enumerate(_derived_mt_path_genotypes(target))
        ]
        conflict = {
            "rsid": f"vendor_issue_1798_batch06_conflict_{target}",
            "chrom": "MT",
            "pos": position,
            "genotype": ancestral_genotype,
        }
        rows = [conflict, *derived_rows] if conflict_first else [*derived_rows, conflict]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == expected_path

    @pytest.mark.parametrize(
        ("target", "expected_parent", "expected_markers"),
        [
            pytest.param(target, parent, markers, id=target)
            for target, (parent, markers) in _ISSUE_1798_BATCH07_TREE.items()
        ],
    )
    def test_issue_1798_batch07_tree_markers_and_parents_are_exact(
        self,
        bundle: HaplogroupBundle,
        target: str,
        expected_parent: str,
        expected_markers: tuple[tuple[int, str], ...],
    ) -> None:
        """The S/W/X batch retains only its reviewed direct runtime motifs."""
        parent = _find_mt_node(bundle.mt_tree, expected_parent)
        assert parent is not None
        matching_children = [child for child in parent.children if child.haplogroup == target]
        assert len(matching_children) == 1
        node = matching_children[0]
        assert tuple((snp.pos, snp.allele) for snp in node.defining_snps) == expected_markers

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("selected_positions", "expected", "expected_counts"),
        [
            pytest.param((5302, 15654), "X1", (2, 3), id="two-of-three-resolves-X1"),
            pytest.param((5302,), "X", None, id="one-of-three-stops-at-X"),
        ],
    )
    def test_issue_1798_batch07_x1_partial_coverage_boundary(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        selected_positions: tuple[int, ...],
        expected: str,
        expected_counts: tuple[int, int] | None,
    ) -> None:
        """X1 follows the caller's documented two-of-three missing-data boundary."""
        x1_markers = dict(_ISSUE_1798_BATCH07_TREE["X1"][1])
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch07_X1_partial_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("X"),
                    *(
                        {"pos": position, "genotype": x1_markers[position] * 2}
                        for position in selected_positions
                    ),
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        expected_path = ["L3", "N", "X", *(["X1"] if expected == "X1" else [])]
        assert [step.haplogroup for step in mt.traversal_path] == expected_path
        if expected_counts is not None:
            terminal = mt.traversal_path[-1]
            assert (terminal.snps_present, terminal.snps_total) == expected_counts

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch07_unsupported_x1_marker_is_ignored(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """Old m.6253C plus one valid X1 call remains below X1's threshold."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch07_old_X1_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("X"),
                    {"pos": 6253, "genotype": "CC"},
                    {"pos": 16104, "genotype": "TT"},
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "X"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "N", "X"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch07_x1_typed_ancestral_marker_blocks_descent(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """A typed ancestral X1 state wins over two derived direct calls."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch07_X1_conflict_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("X"),
                    {"pos": 5302, "genotype": "TT"},
                    {"pos": 15654, "genotype": "CC"},
                    {"pos": 16104, "genotype": "TT"},
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "X"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "N", "X"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("source_prefix", "ancestor", "helper_markers", "expected_path"),
        [
            pytest.param(
                "N2",
                "N",
                ((189, "G"), (709, "A"), (5046, "A"), (11674, "T"), (12414, "C")),
                ["L3", "N"],
                id="N2",
            ),
            pytest.param(
                "W+194",
                "W",
                ((194, "T"),),
                ["L3", "N", "W"],
                id="W+194",
            ),
            pytest.param(
                "X1'2'3",
                "X",
                ((153, "G"), (16104, "T")),
                ["L3", "N", "X"],
                id="X1-2-3",
            ),
            pytest.param(
                "X1'3",
                "X",
                ((146, "C"), (16104, "T")),
                ["L3", "N", "X"],
                id="X-plus-146-16104",
            ),
            pytest.param(
                "X2a'j",
                "X2",
                ((225, "A"), (12397, "G")),
                ["L3", "N", "X", "X2"],
                id="X2-plus-225-X2a-j",
            ),
            pytest.param(
                "X2b'd",
                "X2",
                ((225, "A"), (13708, "A")),
                ["L3", "N", "X", "X2"],
                id="X2-plus-225-13708",
            ),
        ],
    )
    def test_issue_1798_batch07_source_only_helpers_do_not_refine(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        source_prefix: str,
        ancestor: str,
        helper_markers: tuple[tuple[int, str], ...],
        expected_path: list[str],
    ) -> None:
        """Flattened Build 17 intermediates remain provenance-only at runtime."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch07_{source_prefix}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes(ancestor),
                    *(
                        {"pos": position, "genotype": allele * 2}
                        for position, allele in helper_markers
                    ),
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == ancestor
        assert [step.haplogroup for step in mt.traversal_path] == expected_path

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch07_n2_helpers_cannot_push_w_over_threshold(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """Four W calls plus all N2 helpers remain below W's direct-motif floor."""
        w_direct_calls = ((207, "A"), (1243, "C"), (3505, "G"), (5460, "A"))
        n2_source_helpers = (
            (189, "G"),
            (709, "A"),
            (5046, "A"),
            (11674, "T"),
            (12414, "C"),
        )
        assert len(w_direct_calls) == 4
        assert set(w_direct_calls) < set(_ISSUE_1798_BATCH07_TREE["W"][1])

        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch07_N2_W_boundary_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("N"),
                    *(
                        {"pos": position, "genotype": allele * 2}
                        for position, allele in (*w_direct_calls, *n2_source_helpers)
                    ),
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        # The four true W calls are 4/9 (< 0.5), so source-only N2 calls must
        # not turn them into nine apparent hits out of fourteen emitted events.
        assert mt.haplogroup == "N"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "N"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "ancestor", "node_markers", "expected", "expected_path"),
        [
            pytest.param(
                "X2a-without-X2",
                "X",
                ((8913, "G"), (14502, "C")),
                "X",
                ["L3", "N", "X"],
                id="X2a-cannot-bypass-X2",
            ),
            pytest.param(
                "X2b-without-X2",
                "X",
                ((8393, "T"),),
                "X",
                ["L3", "N", "X"],
                id="X2b-cannot-bypass-X2",
            ),
            pytest.param(
                "X2a-with-X2b-helper",
                "X2",
                ((225, "A"), (13708, "A"), (8913, "G"), (14502, "C")),
                "X2a",
                ["L3", "N", "X", "X2", "X2a"],
                id="X2a-ignores-X2b-source-helper",
            ),
            pytest.param(
                "X2b-with-X2a-helper",
                "X2",
                ((225, "A"), (12397, "G"), (8393, "T")),
                "X2b",
                ["L3", "N", "X", "X2", "X2b"],
                id="X2b-ignores-X2a-source-helper",
            ),
        ],
    )
    def test_issue_1798_batch07_x2_children_respect_gateway_and_sibling_helpers(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        ancestor: str,
        node_markers: tuple[tuple[int, str], ...],
        expected: str,
        expected_path: list[str],
    ) -> None:
        """X2 children require X2 and cannot be selected by a sibling's helper."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch07_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes(ancestor),
                    *(
                        {"pos": position, "genotype": allele * 2}
                        for position, allele in node_markers
                    ),
                ]
            )
        ]

        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == expected_path

    @pytest.mark.parametrize(
        ("target", "expected_parent", "expected_markers"),
        [
            pytest.param(target, parent, markers, id=target)
            for target, (parent, markers) in _ISSUE_1798_BATCH08_TREE.items()
        ],
    )
    def test_issue_1798_batch08_tree_markers_and_parents_are_exact(
        self,
        bundle: HaplogroupBundle,
        target: str,
        expected_parent: str,
        expected_markers: tuple[tuple[int, str], ...],
    ) -> None:
        """The R/H-spine runtime tree retains only reviewed terminal evidence."""
        parent = _find_mt_node(bundle.mt_tree, expected_parent)
        assert parent is not None
        matching_children = [child for child in parent.children if child.haplogroup == target]
        assert len(matching_children) == 1
        node = matching_children[0]
        assert tuple((snp.pos, snp.allele) for snp in node.defining_snps) == expected_markers

    @pytest.mark.parametrize(
        ("target", "expected_parent", "expected_markers"),
        [
            pytest.param(target, parent, markers, id=target)
            for target, (parent, markers) in _ISSUE_1798_BATCH09_TREE.items()
        ],
    )
    def test_issue_1798_batch09_h_descendant_tree_is_exact(
        self,
        bundle: HaplogroupBundle,
        target: str,
        expected_parent: str,
        expected_markers: tuple[tuple[int, str], ...],
    ) -> None:
        """Batch 09 retains only reviewed H-descendant evidence and parents."""
        parent = _find_mt_node(bundle.mt_tree, expected_parent)
        assert parent is not None
        matching_children = [child for child in parent.children if child.haplogroup == target]
        assert len(matching_children) == 1
        node = matching_children[0]
        assert tuple((snp.pos, snp.allele) for snp in node.defining_snps) == expected_markers

    @pytest.mark.parametrize(
        ("target", "expected_parent", "expected_markers"),
        [
            pytest.param(target, parent, markers, id=target)
            for target, (parent, markers) in _ISSUE_1798_BATCH10_TREE.items()
        ],
    )
    def test_issue_1798_batch10_r_other_branch_tree_is_exact(
        self,
        bundle: HaplogroupBundle,
        target: str,
        expected_parent: str,
        expected_markers: tuple[tuple[int, str], ...],
    ) -> None:
        """Batch 10 retains exact direct markers and deletion-defined markerless B."""
        parent = _find_mt_node(bundle.mt_tree, expected_parent)
        assert parent is not None
        matching_children = [child for child in parent.children if child.haplogroup == target]
        assert len(matching_children) == 1
        node = matching_children[0]
        assert tuple((snp.pos, snp.allele) for snp in node.defining_snps) == expected_markers

    @pytest.mark.parametrize(
        ("target", "expected_parent", "expected_markers"),
        [
            pytest.param(target, parent, markers, id=target)
            for target, (parent, markers) in _ISSUE_1798_BATCH11_TREE.items()
        ],
    )
    def test_issue_1798_batch11_jt_branch_tree_is_exact(
        self,
        bundle: HaplogroupBundle,
        target: str,
        expected_parent: str,
        expected_markers: tuple[tuple[int, str], ...],
    ) -> None:
        """Batch 11 retains only exact J/T markers at their runtime owners."""
        parent = _find_mt_node(bundle.mt_tree, expected_parent)
        assert parent is not None
        matching_children = [child for child in parent.children if child.haplogroup == target]
        assert len(matching_children) == 1
        node = matching_children[0]
        assert tuple((snp.pos, snp.allele) for snp in node.defining_snps) == expected_markers

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("target", "calls", "expected_path"),
        [
            pytest.param(target, calls, expected_path, id=target)
            for target, (calls, expected_path) in _ISSUE_1798_BATCH11_DIRECT_CASES.items()
        ],
    )
    def test_issue_1798_batch11_direct_branches_resolve_without_sibling_borrowing(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
        calls: tuple[tuple[int, str], ...],
        expected_path: tuple[str, ...],
    ) -> None:
        """Each J/T node resolves from its own minimum direct support."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch11_direct_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == target
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch11_full_t_motif_is_not_stolen_by_p_sibling(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """A complete emitted T path must traverse JT/T instead of sibling P."""
        t_path_rows = _derived_mt_path_genotypes("T")
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch11_full_T_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(t_path_rows)
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "T"
        assert [step.haplogroup for step in mt.traversal_path] == ["L3", "N", "R", "JT", "T"]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "calls", "expected", "expected_path"),
        [
            pytest.param(
                "R2-JT",
                ((4216, "C"),),
                "R",
                ("L3", "N", "R"),
                id="R2-JT-helper-cannot-call-JT",
            ),
            pytest.param(
                "J1-plus-16193",
                _ISSUE_1798_BATCH11_J1_CALLS + ((16193, "T"),),
                "J1",
                ("L3", "N", "R", "JT", "J", "J1"),
                id="J1-helper-cannot-call-J1d",
            ),
            pytest.param(
                "T2-plus-150",
                _ISSUE_1798_BATCH11_T2_CALLS + ((150, "T"),),
                "T2",
                ("L3", "N", "R", "JT", "T", "T2"),
                id="T2-150-helper-cannot-call-T2e",
            ),
            pytest.param(
                "T2-plus-16189",
                _ISSUE_1798_BATCH11_T2_CALLS + ((16189, "C"),),
                "T2",
                ("L3", "N", "R", "JT", "T", "T2"),
                id="T2-16189-helper-cannot-call-T2f",
            ),
        ],
    )
    def test_issue_1798_batch11_flattened_helpers_are_source_only(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        calls: tuple[tuple[int, str], ...],
        expected: str,
        expected_path: tuple[str, ...],
    ) -> None:
        """Flattened helper evidence cannot manufacture a reportable J/T child."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch11_helper_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize("include_historical", [True, False], ids=["typed", "untyped"])
    @pytest.mark.parametrize(
        ("target", "parent", "parent_calls", "direct_call", "target_path"),
        [
            pytest.param(
                "T1a",
                "T1",
                _ISSUE_1798_BATCH11_T1_CALLS,
                (16186, "T"),
                ("L3", "N", "R", "JT", "T", "T1", "T1a"),
                id="T1a-historical-m16186",
            ),
            pytest.param(
                "T2f",
                "T2",
                _ISSUE_1798_BATCH11_T2_CALLS,
                (8270, "T"),
                ("L3", "N", "R", "JT", "T", "T2", "T2f"),
                id="T2f-historical-m8270",
            ),
        ],
    )
    def test_issue_1798_batch11_historical_leaves_require_their_direct_marker(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        include_historical: bool,
        target: str,
        parent: str,
        parent_calls: tuple[tuple[int, str], ...],
        direct_call: tuple[int, str],
        target_path: tuple[str, ...],
    ) -> None:
        """Historical m.16186/m.8270 resolve their leaves only when typed."""
        calls = parent_calls + ((direct_call,) if include_historical else ())
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch11_historical_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        expected = target if include_historical else parent
        expected_path = target_path if include_historical else target_path[:-1]
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)
        if include_historical:
            assert (mt.traversal_path[-1].snps_present, mt.traversal_path[-1].snps_total) == (
                1,
                1,
            )

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch11_pgp_4162_layout_keeps_t2_descendant_reachable(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """Typed m.11812 alone keeps T2 at 1/2 and routes direct T2e evidence."""
        calls = _ISSUE_1798_BATCH11_T2_CALLS + ((16153, "A"),)
        positions = {position for position, _allele in calls}
        assert 11812 in positions
        assert positions.isdisjoint({14233, 16296})
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch11_pgp_4162_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "T2e"
        assert [step.haplogroup for step in mt.traversal_path] == [
            "L3",
            "N",
            "R",
            "JT",
            "T",
            "T2",
            "T2e",
        ]
        t2_step = next(step for step in mt.traversal_path if step.haplogroup == "T2")
        assert (t2_step.snps_present, t2_step.snps_total) == (1, 2)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "calls", "expected", "expected_path"),
        [
            pytest.param(
                "old-JT",
                ((11251, "G"), (489, "C")),
                "R",
                ("L3", "N", "R"),
                id="old-JT-pair-stays-at-R",
            ),
            pytest.param(
                "old-J1b",
                _ISSUE_1798_BATCH11_J1_CALLS + ((8269, "A"),),
                "J1",
                ("L3", "N", "R", "JT", "J", "J1"),
                id="old-J1b-markers-stay-at-J1",
            ),
            pytest.param(
                "old-J1c",
                _ISSUE_1798_BATCH11_J1_CALLS + ((9055, "A"), (13708, "A")),
                "J1",
                ("L3", "N", "R", "JT", "J", "J1"),
                id="old-J1c-markers-stay-at-J1",
            ),
            pytest.param(
                "old-J2a",
                _ISSUE_1798_BATCH11_J2_CALLS + ((15257, "A"),),
                "J2",
                ("L3", "N", "R", "JT", "J", "J2"),
                id="old-J2a-markers-are-now-inherited",
            ),
            pytest.param(
                "old-J2b",
                _ISSUE_1798_BATCH11_J2_CALLS + ((6261, "A"), (13708, "A")),
                "J2",
                ("L3", "N", "R", "JT", "J", "J2"),
                id="old-J2b-markers-stay-at-J2",
            ),
            pytest.param(
                "old-T1",
                _ISSUE_1798_BATCH11_T_CALLS + ((6185, "C"), (16189, "C")),
                "T",
                ("L3", "N", "R", "JT", "T"),
                id="old-T1-markers-stay-at-T",
            ),
            pytest.param(
                "old-T1a",
                _ISSUE_1798_BATCH11_T1_CALLS + ((6253, "C"), (16163, "G")),
                "T1",
                ("L3", "N", "R", "JT", "T", "T1"),
                id="old-T1a-markers-stay-at-T1",
            ),
            pytest.param(
                "removed-T2b",
                _ISSUE_1798_BATCH11_T2_CALLS + ((15907, "G"),),
                "T2",
                ("L3", "N", "R", "JT", "T", "T2"),
                id="removed-m15907-does-not-call-T2b",
            ),
            pytest.param(
                "old-T2c",
                _ISSUE_1798_BATCH11_T2_CALLS + ((6489, "G"),),
                "T2",
                ("L3", "N", "R", "JT", "T", "T2"),
                id="old-T2c-marker-stays-at-T2",
            ),
            pytest.param(
                "old-T2e",
                _ISSUE_1798_BATCH11_T2_CALLS + ((7859, "C"),),
                "T2",
                ("L3", "N", "R", "JT", "T", "T2"),
                id="old-T2e-marker-stays-at-T2",
            ),
            pytest.param(
                "old-T2f",
                _ISSUE_1798_BATCH11_T2_CALLS + ((12633, "G"),),
                "T2",
                ("L3", "N", "R", "JT", "T", "T2"),
                id="old-T2f-marker-stays-at-T2",
            ),
            pytest.param(
                "ancestral-JT",
                (
                    (11251, "A"),
                    (15452, "A"),
                    (16126, "C"),
                    (295, "T"),
                    (489, "C"),
                    (10398, "G"),
                ),
                "R",
                ("L3", "N", "R"),
                id="ancestral-JT-marker-blocks-J",
            ),
            pytest.param(
                "ancestral-J1",
                _ISSUE_1798_BATCH11_J_CALLS
                + ((462, "C"), (3010, "A"), (16145, "A"), (16222, "T")),
                "J",
                ("L3", "N", "R", "JT", "J"),
                id="ancestral-J1-marker-blocks-J1b",
            ),
            pytest.param(
                "ancestral-J2",
                _ISSUE_1798_BATCH11_J_CALLS + ((7476, "T"), (15257, "G"), (10499, "G")),
                "J",
                ("L3", "N", "R", "JT", "J"),
                id="ancestral-J2-marker-blocks-J2a",
            ),
            pytest.param(
                "ancestral-T",
                _ISSUE_1798_BATCH11_JT_CALLS
                + (
                    (1888, "G"),
                    (4917, "G"),
                    (8697, "A"),
                    (10463, "C"),
                    (12633, "A"),
                    (16163, "G"),
                ),
                "JT",
                ("L3", "N", "R", "JT"),
                id="ancestral-T-marker-blocks-T1",
            ),
            pytest.param(
                "ancestral-T1",
                _ISSUE_1798_BATCH11_T_CALLS + ((12633, "C"), (16163, "G"), (16186, "T")),
                "T",
                ("L3", "N", "R", "JT", "T"),
                id="ancestral-T1-marker-blocks-T1a",
            ),
            pytest.param(
                "ancestral-T2",
                _ISSUE_1798_BATCH11_T_CALLS + ((11812, "A"), (14233, "G"), (16153, "A")),
                "T",
                ("L3", "N", "R", "JT", "T"),
                id="ancestral-T2-marker-blocks-T2e",
            ),
        ],
    )
    def test_issue_1798_batch11_old_and_ancestral_states_do_not_refine(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        calls: tuple[tuple[int, str], ...],
        expected: str,
        expected_path: tuple[str, ...],
    ) -> None:
        """Former and explicitly ancestral states cannot over-resolve J/T."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch11_negative_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == list(expected_path)

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "calls", "expected", "expected_path"),
        [
            pytest.param(
                "B4",
                ((16217, "C"),),
                "B4",
                ["L3", "N", "R", "B", "B4"],
                id="direct-B4-through-markerless-B",
            ),
            pytest.param(
                "B5",
                ((8584, "A"), (9950, "C"), (10398, "G"), (16140, "C")),
                "B5",
                ["L3", "N", "R", "B", "B5"],
                id="direct-B5-through-markerless-B",
            ),
            pytest.param(
                "B-source-only",
                ((16189, "C"), (8281, "C")),
                "R",
                ["L3", "N", "R"],
                id="helper-and-old-substitution-cannot-terminal-call-B",
            ),
        ],
    )
    def test_issue_1798_batch10_markerless_b_routes_only_direct_children(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        calls: tuple[tuple[int, str], ...],
        expected: str,
        expected_path: list[str],
    ) -> None:
        """B itself is never terminal, while direct B4/B5 evidence remains reachable."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch10_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == expected_path

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "ancestor", "calls", "expected", "expected_path"),
        [
            pytest.param(
                "HV0a",
                "HV0",
                ((15904, "T"),),
                "HV0",
                ["L3", "N", "R", "R0", "HV", "HV0"],
                id="HV0a-does-not-call-V",
            ),
            pytest.param(
                "B4-plus-16261",
                "B4",
                ((16261, "T"),),
                "B4",
                ["L3", "N", "R", "B", "B4"],
                id="B4-helper-does-not-call-B4a",
            ),
            pytest.param(
                "B4b-shared",
                "B4",
                ((827, "G"), (15535, "T")),
                "B4",
                ["L3", "N", "R", "B", "B4"],
                id="B4b-shared-helper-does-not-refine",
            ),
            pytest.param(
                "R9",
                "R",
                ((3970, "T"), (13928, "C"), (16304, "C")),
                "R",
                ["L3", "N", "R"],
                id="R9-does-not-call-F",
            ),
            pytest.param(
                "F1a-shared",
                "F1",
                ((9053, "A"), (13759, "A"), (16129, "A")),
                "F1",
                ["L3", "N", "R", "F", "F1"],
                id="F1a-shared-helper-does-not-refine",
            ),
            pytest.param(
                "F1-plus-16189",
                "F1",
                ((16189, "C"),),
                "F1",
                ["L3", "N", "R", "F", "F1"],
                id="F1-helper-does-not-call-F1b",
            ),
        ],
    )
    def test_issue_1798_batch10_flattened_helpers_are_source_only(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        ancestor: str,
        calls: tuple[tuple[int, str], ...],
        expected: str,
        expected_path: list[str],
    ) -> None:
        """None of the corrected seven flattened paths can create terminal credit."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch10_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes(ancestor),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == expected_path

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("target", "ancestor", "direct_calls", "expected_path"),
        [
            pytest.param(
                "V1",
                "V",
                ((8869, "G"),),
                ["L3", "N", "R", "R0", "HV", "HV0", "V", "V1"],
                id="V1-direct",
            ),
            pytest.param(
                "V7",
                "V",
                ((93, "G"), (7444, "A")),
                ["L3", "N", "R", "R0", "HV", "HV0", "V", "V7"],
                id="V7-direct",
            ),
            pytest.param(
                "F1a",
                "F1",
                ((4086, "T"), (16172, "C")),
                ["L3", "N", "R", "F", "F1", "F1a"],
                id="F1a-direct",
            ),
            pytest.param(
                "F1b",
                "F1",
                ((10976, "T"), (12633, "T"), (14476, "A"), (16232, "A"), (16249, "C")),
                ["L3", "N", "R", "F", "F1", "F1b"],
                id="F1b-direct",
            ),
            pytest.param(
                "F2",
                "F",
                ((1005, "C"), (7828, "G"), (10535, "C"), (10586, "A"), (12338, "C"), (13708, "A")),
                ["L3", "N", "R", "F", "F2"],
                id="F2-direct",
            ),
            pytest.param(
                "P",
                "R",
                ((15607, "G"),),
                ["L3", "N", "R", "P"],
                id="P-direct",
            ),
        ],
    )
    def test_issue_1798_batch10_direct_branches_resolve_without_sibling_borrowing(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        target: str,
        ancestor: str,
        direct_calls: tuple[tuple[int, str], ...],
        expected_path: list[str],
    ) -> None:
        """Direct evidence resolves its own Batch 10 child through the reviewed parent."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch10_{target}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes(ancestor),
                    *(
                        {"pos": position, "genotype": allele * 2}
                        for position, allele in direct_calls
                    ),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == target
        assert [step.haplogroup for step in mt.traversal_path] == expected_path

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "ancestor", "calls", "expected", "expected_path"),
        [
            pytest.param(
                "V-ancestral",
                "HV0",
                ((4580, "G"),),
                "HV0",
                ["L3", "N", "R", "R0", "HV", "HV0"],
            ),
            pytest.param(
                "B4-ancestral",
                "R",
                ((16217, "T"),),
                "R",
                ["L3", "N", "R"],
            ),
            pytest.param(
                "B4a-ancestral",
                "B4",
                ((5465, "T"),),
                "B4",
                ["L3", "N", "R", "B", "B4"],
            ),
            pytest.param(
                "F-ancestral",
                "R",
                ((6392, "T"), (10310, "A")),
                "R",
                ["L3", "N", "R"],
            ),
            pytest.param(
                "F1b-ancestral",
                "F1",
                ((10976, "C"), (12633, "T"), (14476, "A"), (16232, "A"), (16249, "C")),
                "F1",
                ["L3", "N", "R", "F", "F1"],
            ),
            pytest.param(
                "P-ancestral",
                "R",
                ((15607, "A"),),
                "R",
                ["L3", "N", "R"],
            ),
        ],
    )
    def test_issue_1798_batch10_typed_ancestral_calls_block_descent(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        ancestor: str,
        calls: tuple[tuple[int, str], ...],
        expected: str,
        expected_path: list[str],
    ) -> None:
        """A typed source-ancestral state wins over otherwise matching child evidence."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch10_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes(ancestor),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == expected_path

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "ancestor", "calls", "expected", "expected_path"),
        [
            pytest.param(
                "old-V",
                "HV0",
                ((15904, "C"), (4732, "G"), (5263, "T")),
                "HV0",
                ["L3", "N", "R", "R0", "HV", "HV0"],
            ),
            pytest.param(
                "old-B",
                "R",
                ((827, "G"), (8281, "C"), (15301, "A")),
                "R",
                ["L3", "N", "R"],
            ),
            pytest.param(
                "old-F1",
                "F",
                ((3970, "T"), (12406, "A")),
                "F",
                ["L3", "N", "R", "F"],
            ),
            pytest.param(
                "old-F1b",
                "F1",
                ((7828, "G"),),
                "F1",
                ["L3", "N", "R", "F", "F1"],
            ),
            pytest.param(
                "old-P",
                "R",
                ((1438, "G"), (3705, "T"), (16176, "G")),
                "R",
                ["L3", "N", "R"],
            ),
        ],
    )
    def test_issue_1798_batch10_old_marker_sets_do_not_refine(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        ancestor: str,
        calls: tuple[tuple[int, str], ...],
        expected: str,
        expected_path: list[str],
    ) -> None:
        """Former inherited, helper, historical, and unsupported markers stay inert."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch10_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes(ancestor),
                    *({"pos": position, "genotype": allele * 2} for position, allele in calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == expected_path

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "extra_calls", "expected", "expected_path"),
        [
            pytest.param(
                "H1-only",
                (),
                "H1",
                ["L3", "N", "R", "R0", "HV", "H", "H1"],
                id="H1-does-not-borrow-duplicated-m3010",
            ),
            pytest.param(
                "source-helper-only",
                ((16189, "C"),),
                "H1",
                ["L3", "N", "R", "R0", "HV", "H", "H1"],
                id="H1-plus-16189-does-not-refine",
            ),
            pytest.param(
                "direct-H1b",
                ((16356, "C"),),
                "H1b",
                ["L3", "N", "R", "R0", "HV", "H", "H1", "H1b"],
                id="direct-m16356-resolves-H1b",
            ),
        ],
    )
    def test_issue_1798_batch09_h1b_requires_its_direct_marker(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        extra_calls: tuple[tuple[int, str], ...],
        expected: str,
        expected_path: list[str],
    ) -> None:
        """H1+16189 is source-only, while direct m.16356 resolves sibling H1b."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch09_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("H1"),
                    *(
                        {"pos": position, "genotype": allele * 2}
                        for position, allele in extra_calls
                    ),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == expected_path
        if expected == "H1b":
            assert (mt.traversal_path[-1].snps_present, mt.traversal_path[-1].snps_total) == (
                1,
                1,
            )

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "extra_calls", "expected", "expected_path"),
        [
            pytest.param(
                "historical-gateway-only",
                ((750, "A"),),
                "H2a",
                ["L3", "N", "R", "R0", "HV", "H", "H2", "H2a"],
                id="m750-cannot-terminal-call-H2a2",
            ),
            pytest.param(
                "primary-rCRS-descendant",
                ((8860, "A"), (263, "A")),
                "H2a2a1",
                [
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
                ],
                id="primary-markers-traverse-markerless-H2a2",
            ),
        ],
    )
    def test_issue_1798_batch09_h2a2_is_a_markerless_gateway(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        extra_calls: tuple[tuple[int, str], ...],
        expected: str,
        expected_path: list[str],
    ) -> None:
        """Historical m.750 is source-only, but primary descendants remain reachable."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch09_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("H2a"),
                    *(
                        {"pos": position, "genotype": allele * 2}
                        for position, allele in extra_calls
                    ),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == expected_path
        if expected == "H2a2a1":
            assert (mt.traversal_path[-1].snps_present, mt.traversal_path[-1].snps_total) == (
                1,
                1,
            )

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "ancestor", "source_only", "expected", "expected_path"),
        [
            pytest.param(
                "R0",
                "R",
                ((73, "A"), (11719, "G")),
                "R",
                ["L3", "N", "R"],
                id="R0-direct-source-events",
            ),
            pytest.param(
                "HV",
                "R",
                ((14766, "C"),),
                "R",
                ["L3", "N", "R"],
                id="HV-historical-only-event",
            ),
            pytest.param(
                "H5",
                "H",
                ((456, "T"), (16304, "C")),
                "H",
                ["L3", "N", "R", "R0", "HV", "H"],
                id="H5-shared-and-historical-events",
            ),
            pytest.param(
                "H-plus-195",
                "H",
                ((195, "C"), (16311, "C")),
                "H",
                ["L3", "N", "R", "R0", "HV", "H"],
                id="H11-flattened-and-historical-events",
            ),
        ],
    )
    def test_issue_1798_batch08_source_only_events_do_not_refine(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        ancestor: str,
        source_only: tuple[tuple[int, str], ...],
        expected: str,
        expected_path: list[str],
    ) -> None:
        """Structural and flattened Build 17 events cannot create terminal calls."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch08_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes(ancestor),
                    *(
                        {"pos": position, "genotype": allele * 2}
                        for position, allele in source_only
                    ),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == expected_path

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("h_calls", "expected", "expected_path", "expected_counts"),
        [
            pytest.param(
                ((2706, "A"),),
                "H",
                ["L3", "N", "R", "R0", "HV", "H"],
                (1, 2),
                id="one-of-two-m2706-resolves-H",
            ),
            pytest.param(
                ((7028, "C"),),
                "H",
                ["L3", "N", "R", "R0", "HV", "H"],
                (1, 2),
                id="one-of-two-m7028-resolves-H",
            ),
            pytest.param((), "R", ["L3", "N", "R"], None, id="zero-of-two-stops-at-R"),
            pytest.param(
                ((2706, "A"), (7028, "T")),
                "R",
                ["L3", "N", "R"],
                None,
                id="typed-ancestral-H-marker-blocks-descent",
            ),
        ],
    )
    def test_issue_1798_batch08_h_two_marker_boundary(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        h_calls: tuple[tuple[int, str], ...],
        expected: str,
        expected_path: list[str],
        expected_counts: tuple[int, int] | None,
    ) -> None:
        """H follows the documented one-of-two floor with no ancestral conflicts."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch08_H_boundary_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("R"),
                    *({"pos": position, "genotype": allele * 2} for position, allele in h_calls),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        assert [step.haplogroup for step in mt.traversal_path] == expected_path
        if expected_counts is not None:
            assert (mt.traversal_path[-1].snps_present, mt.traversal_path[-1].snps_total) == (
                expected_counts
            )

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch08_h5a_uses_only_its_direct_marker(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """H5a remains reachable through markerless H5 using direct m.4336 alone."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch08_H5a_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [*_derived_mt_path_genotypes("H"), {"pos": 4336, "genotype": "CC"}]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "H5a"
        assert [step.haplogroup for step in mt.traversal_path] == [
            "L3",
            "N",
            "R",
            "R0",
            "HV",
            "H",
            "H5",
            "H5a",
        ]
        assert (mt.traversal_path[-1].snps_present, mt.traversal_path[-1].snps_total) == (
            1,
            1,
        )

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    @pytest.mark.parametrize(
        ("case", "markers", "expected"),
        [
            pytest.param("direct", ((8448, "C"), (13759, "A")), "H11", id="direct-H11"),
            pytest.param("old-13101", ((13101, "A"),), "H", id="old-m13101-ignored"),
            pytest.param(
                "source-only",
                ((195, "C"), (16311, "C")),
                "H",
                id="flattened-and-historical-ignored",
            ),
            pytest.param("H4-9123", ((9123, "A"),), "H", id="historical-H4-ignored"),
        ],
    )
    def test_issue_1798_batch08_h_sibling_evidence_is_not_misattributed(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
        case: str,
        markers: tuple[tuple[int, str], ...],
        expected: str,
    ) -> None:
        """H11 is an H child, while old/helper/historical rows remain non-emitted."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch08_{case}_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate(
                [
                    *_derived_mt_path_genotypes("H"),
                    *({"pos": position, "genotype": allele * 2} for position, allele in markers),
                ]
            )
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == expected
        if expected == "H11":
            assert [step.haplogroup for step in mt.traversal_path][-2:] == ["H", "H11"]
            assert "H7" not in [step.haplogroup for step in mt.traversal_path]

    @pytest.mark.parametrize("source_table", [raw_variants, annotated_variants])
    def test_issue_1798_batch08_r0_reversions_cannot_block_h1a(
        self,
        bundle: HaplogroupBundle,
        sample_engine: sa.Engine,
        source_table: sa.Table,
    ) -> None:
        """H1a m.73G and a downstream m.11719A state pass through markerless R0."""
        rows = [
            {
                **row,
                "rsid": f"vendor_issue_1798_batch08_R0_reversion_{index}",
                "chrom": "MT",
            }
            for index, row in enumerate([*_H1A_GENOTYPES, {"pos": 11719, "genotype": "AA"}])
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(source_table), rows)

        mt = next(
            result
            for result in assign_haplogroups(bundle, sample_engine)
            if result.tree_type == "mt"
        )
        assert mt.haplogroup == "H1a"
        assert [step.haplogroup for step in mt.traversal_path] == [
            "L3",
            "N",
            "R",
            "R0",
            "HV",
            "H",
            "H1",
            "H1a",
        ]

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
        HV → H → H1 → H1a, with 1 + 1 + 2 + 0 + 0 + 2 + 1 + 2 = 9 defining SNPs
        (R0/HV remain markerless and H1a reintroduces m.73 as its direct back
        mutation), all
        9 derived in the fixture — so the expected
        present/total/confidence are knowable offline (9 / 9 → 1.0). Asserting
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
        assert mt.defining_snps_present == 9
        assert mt.defining_snps_total == 9
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
            # Structural pass-through nodes have no defining SNP, so total may be 0.
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
