"""Fail-closed tests for the schema-v2 mtDNA provenance frontier."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

import pytest

from scripts.build_haplogroup_bundle import (
    _MT_SOURCE,
    _index_mt_tree,
    _mt_migration_complete_ready,
    _mt_parse_substitution_notation,
    _mt_validate_exact_record,
    _summarize_mt_provenance,
    _validate_mt_registry_against_tree,
    _validate_mt_source,
    _validate_mt_source_schema,
    build_bundle,
    build_mt_tree,
)

LEGACY_EXACT_NAMES_SHA256 = "7d968626b02229ba77f7e58a32b337621c71a1a071e4564d5e815d5c3dee4d5d"
LEGACY_V1_SEMANTIC_SHA256 = "521dedbac66952e7df628dda8da495b6e03f640b3b6765006835d805cd32d63a"
LEGACY_V1_COVERAGE_SHA256 = "375c6a5af32e22bd71026391b5a0552bfa260bac09cf6666f84bab6ea52b7947"
BASELINE_COMMIT = "e463604fc5b4af4d5887c9e9a76c2f54598ef312"
BASELINE_EXACT_NAMES_SHA256 = "3e3386bf2d57ce5814df595576223e08addccba96c92818b7d1cf338b02bf5d9"
BASELINE_V1_SEMANTIC_SHA256 = "c044b73c08b339d0be782306b84d593982b13242d995d541522da6f5bc9fc7c6"
BASELINE_V1_COVERAGE_SHA256 = "d88d4491671f99175bea3c6188affb3b0bbbd31681e0f7c35103ac4f194da6e6"
BASELINE_V2_REGISTRY_SEMANTIC_SHA256 = (
    "3eaa8bb5a9cc33c1a892bd70a7007b8293c2698d4e541a95566f7547c914c553"
)
BASELINE_V2_COVERAGE_SHA256 = "9e9d25bd07652d0637fde59d9292b6a4cba1c593268c2301bba1c910b9bd338b"
LOCKED_EXACT_NAMES_SHA256 = "3e3386bf2d57ce5814df595576223e08addccba96c92818b7d1cf338b02bf5d9"
LOCKED_EXACT_SEMANTIC_SHA256 = "4d1b8376a376df3eca9fd84cbaa912e994429d04890f3699db588cbf53fe1c66"
LOCKED_EXACT_COVERAGE_SHA256 = "9e9d25bd07652d0637fde59d9292b6a4cba1c593268c2301bba1c910b9bd338b"
DIRECT_MOTIF_EXACT_NAMES_SHA256 = (
    "0dc2cc812e511bc89b76fca6ed13614d8ddb75a6ebe6321bde670096c44fba61"
)
DIRECT_MOTIF_EXACT_SEMANTIC_SHA256 = (
    "ecc1dbf4c93872031e102ee166eac50e31d6468395e5d0053357af44f8a9785a"
)
INITIAL_DIRECT_MOTIF_PENDING_NAMES_SHA256 = (
    "7b4848980e34ca1eff9739f964906d68eb4acdbbcd5e93227e17ece79296aefb"
)
INITIAL_PENDING_NAMES_SHA256 = "996c2c96c22d37a2aa7edf1f4639d626ccc5199ecc5eb35984aa84204e05a591"
ARRAY_MANIFEST_SHA256 = "42de22517a4644884596e36b0499a4fc45f264986c63f6fb239452b88719f977"
SOURCE_METADATA_SHA256 = "5b3a3578fc208c91f6c3fdcc6d772f5071851b3604762b9e81994cf2632deb3d"
STATE_PARTITION_SHA256 = "93227229ec35249659fbec5c753470ca6b7d562cfbc23e5079b89a05295d7114"
EMITTED_TREE_SHA256 = "02a40be2096dd8c60e6e2934ba68a813f07478117a749e60e94e0608bed21914"

PRIMARY_EXPORTS = ["pgp_4139", "pgp_4162", "pgp_4187", "pgp_huA08F4D"]
HISTORICAL_EXPORTS = [*PRIMARY_EXPORTS, "pgp_1050"]

EXPECTED_EXPORTS = {
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

EXPECTED_COHORTS = {
    "primary_four_23andme": {"export_ids": PRIMARY_EXPORTS},
    "historical_five_23andme_including_2014": {
        "extends": "primary_four_23andme",
        "export_ids": HISTORICAL_EXPORTS,
    },
}

DIRECT_MOTIF_EXACT_NODES = [
    "C",
    "G1",
    "G2",
    "H10",
    "H13",
    "H13a",
    "H6a",
    "J1d",
    "K1b",
    "K2",
    "K2b",
    "L2c",
    "M1",
    "M8",
    "M8a",
    "N9",
    "S",
    "T2a",
    "U2",
    "U3a",
    "U3b",
    "U5b2",
    "W1",
    "X",
    "X2b",
    "Z",
    "Z1",
]
DIRECT_MOTIF_LEGACY_PARTIAL_NODES = [
    "G",
    "H6",
    "K",
    "K1",
    "K1a",
    "K2a",
    "U2e",
    "X2",
    "X2a",
    "Y1",
    "Y2",
    "Y_mt",
]


def _canonical_sha256(value: Any) -> str:
    """Independent canonicalizer: do not share the production digest helper."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    return hashlib.sha256(payload.encode()).hexdigest()


def _tsv_sha256(rows: list[tuple[Any, ...]]) -> str:
    payload = "".join("\t".join(str(value) for value in row) + "\n" for row in sorted(rows))
    return hashlib.sha256(payload.encode()).hexdigest()


def _v1_semantic_projection(source: dict[str, Any], names: list[str]) -> list[dict[str, Any]]:
    result = []
    for name in names:
        record = source["nodes"][name]
        markers = []
        for marker in record["emitted_snps"]:
            coverage = marker["array_coverage"]
            cohort = source["array_cohorts"][coverage["cohort_id"]]["export_ids"]
            markers.append(
                {
                    "rsid": marker["rsid"],
                    "pos": marker["pos"],
                    "ancestral_allele": marker["ancestral_allele"],
                    "allele": marker["allele"],
                    "array_coverage": {
                        "modern_exports_tested": len(cohort),
                        "modern_exports_with_position": len(coverage["position_present_in"]),
                    },
                }
            )
        result.append(
            {
                "node": name,
                "emitted_snps": markers,
                "source_motif": record["direct_source_motif"],
            }
        )
    return sorted(result, key=lambda item: item["node"])


def _v1_coverage_rows(source: dict[str, Any], names: list[str]) -> list[tuple[Any, ...]]:
    rows = []
    for name in names:
        for marker in source["nodes"][name]["emitted_snps"]:
            coverage = marker["array_coverage"]
            rows.append(
                (
                    name,
                    marker["rsid"],
                    marker["pos"],
                    coverage["cohort_id"],
                    len(source["array_cohorts"][coverage["cohort_id"]]["export_ids"]),
                    len(coverage["position_present_in"]),
                )
            )
    return rows


def _locked_semantic_projection(source: dict[str, Any], names: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "node": name,
            "source_node": source["nodes"][name]["source_node"],
            "emitted_parent": source["nodes"][name]["emitted_parent"],
            "source_topology": source["nodes"][name]["source_topology"],
            "direct_source_motif": source["nodes"][name]["direct_source_motif"],
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
                for marker in source["nodes"][name]["emitted_snps"]
            ],
        }
        for name in names
    ]


def _baseline_v2_registry_projection(
    source: dict[str, Any], names: list[str]
) -> list[dict[str, Any]]:
    return [
        {
            "node": name,
            "source_node": source["nodes"][name]["source_node"],
            "emitted_parent": source["nodes"][name]["emitted_parent"],
            "direct_source_motif": source["nodes"][name]["direct_source_motif"],
            "emitted_snps": _locked_semantic_projection(source, [name])[0]["emitted_snps"],
        }
        for name in names
    ]


def _direct_motif_semantic_projection(
    source: dict[str, Any], names: list[str]
) -> list[dict[str, Any]]:
    return [
        {
            "node": name,
            "source_node": source["nodes"][name]["source_node"],
            "direct_source_motif": source["nodes"][name]["direct_source_motif"],
        }
        for name in names
    ]


def _locked_coverage_rows(source: dict[str, Any], names: list[str]) -> list[tuple[Any, ...]]:
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


def _tree_projection(tree: dict[str, Any]) -> list[dict[str, Any]]:
    projection: list[dict[str, Any]] = []

    def visit(node: dict[str, Any], parent: str | None) -> None:
        projection.append(
            {
                "node": node["haplogroup"],
                "parent": parent,
                "defining_snps": node.get("defining_snps", []),
            }
        )
        for child in node.get("children", []):
            visit(child, node["haplogroup"])

    visit(tree, None)
    return projection


def _find_node(tree: dict[str, Any], name: str) -> dict[str, Any]:
    if tree["haplogroup"] == name:
        return tree
    for child in tree.get("children", []):
        try:
            return _find_node(child, name)
        except LookupError:
            pass
    raise LookupError(name)


def _issues_text(issues: list[str]) -> str:
    return "\n".join(issues)


def _refresh_semantic_projection_digests(source: dict[str, Any]) -> None:
    migration = source["migration"]
    migration["legacy_v1_semantic_sha256"] = _canonical_sha256(
        _v1_semantic_projection(source, migration["legacy_locked_exact_nodes"])
    )
    migration["baseline_v1_semantic_sha256"] = _canonical_sha256(
        _v1_semantic_projection(source, migration["baseline_exact_nodes"])
    )
    migration["baseline_v2_registry_semantic_sha256"] = _canonical_sha256(
        _baseline_v2_registry_projection(source, migration["baseline_exact_nodes"])
    )
    migration["locked_exact_semantic_sha256"] = _canonical_sha256(
        _locked_semantic_projection(source, migration["locked_exact_nodes"])
    )
    migration["baseline_direct_motif_semantic_sha256"] = _canonical_sha256(
        _direct_motif_semantic_projection(source, migration["baseline_direct_motif_exact_nodes"])
    )
    migration["locked_direct_motif_semantic_sha256"] = _canonical_sha256(
        _direct_motif_semantic_projection(source, migration["locked_direct_motif_exact_nodes"])
    )


def _refresh_coverage_projection_digests(source: dict[str, Any]) -> None:
    migration = source["migration"]
    migration["legacy_v1_coverage_sha256"] = _tsv_sha256(
        _v1_coverage_rows(source, migration["legacy_locked_exact_nodes"])
    )
    migration["baseline_v1_coverage_sha256"] = _tsv_sha256(
        _v1_coverage_rows(source, migration["baseline_exact_nodes"])
    )
    migration["baseline_v2_coverage_membership_sha256"] = _tsv_sha256(
        _locked_coverage_rows(source, migration["baseline_exact_nodes"])
    )
    migration["locked_exact_coverage_membership_sha256"] = _tsv_sha256(
        _locked_coverage_rows(source, migration["locked_exact_nodes"])
    )


def test_production_registry_is_a_complete_dynamic_partition() -> None:
    tree = build_mt_tree()
    inventory = _index_mt_tree(tree)

    assert len(inventory.occurrences) == 194
    assert len(inventory.by_name) == 194
    assert not inventory.duplicates
    assert len(inventory.marker_bearing_names) == 192
    assert len(inventory.markerless_names) == 2
    assert inventory.marker_count == 395
    assert inventory.edge_count == 193
    assert set(_MT_SOURCE["nodes"]) | set(_MT_SOURCE["structural_exceptions"]) | set(
        _MT_SOURCE["pending_nodes"]
    ) == set(inventory.by_name)
    assert set(_MT_SOURCE["nodes"]) | set(_MT_SOURCE["pending_nodes"]) == set(
        inventory.marker_bearing_names
    )
    assert set(_MT_SOURCE["structural_exceptions"]) == set(inventory.markerless_names)
    assert _MT_SOURCE["direct_source_motif_states"] == {
        "exact_nodes": DIRECT_MOTIF_EXACT_NODES,
        "legacy_partial_nodes": DIRECT_MOTIF_LEGACY_PARTIAL_NODES,
    }
    assert set(DIRECT_MOTIF_EXACT_NODES).isdisjoint(DIRECT_MOTIF_LEGACY_PARTIAL_NODES)
    assert set(DIRECT_MOTIF_EXACT_NODES) | set(DIRECT_MOTIF_LEGACY_PARTIAL_NODES) == set(
        _MT_SOURCE["nodes"]
    )
    assert all(
        _MT_SOURCE["nodes"][name]["source_motif_status"] == "exact"
        for name in DIRECT_MOTIF_EXACT_NODES
    )
    assert all(
        _MT_SOURCE["nodes"][name]["source_motif_status"] == "legacy_partial"
        for name in DIRECT_MOTIF_LEGACY_PARTIAL_NODES
    )
    assert _validate_mt_source_schema(_MT_SOURCE) == []
    assert _validate_mt_registry_against_tree(_MT_SOURCE, inventory) == []
    assert _validate_mt_source(_MT_SOURCE) == []
    assert _validate_mt_source(_MT_SOURCE, tree) == []


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("missing", "missing=['A']"),
        ("overlap", "marker-exact and pending states overlap: G"),
        ("orphan", "extra=['not-an-emitted-node']"),
    ],
)
def test_partition_rejects_missing_overlap_and_orphan(mutation: str, expected: str) -> None:
    source = deepcopy(_MT_SOURCE)
    inventory = _index_mt_tree(build_mt_tree())

    if mutation == "missing":
        source["pending_nodes"].pop("A")
        issues = _validate_mt_registry_against_tree(source, inventory)
    elif mutation == "overlap":
        source["pending_nodes"]["G"] = {"emitted_parent": "M"}
        issues = _validate_mt_source_schema(source)
    else:
        source["pending_nodes"]["not-an-emitted-node"] = {"emitted_parent": "M"}
        issues = _validate_mt_registry_against_tree(source, inventory)

    assert expected in _issues_text(issues)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("overlap", "exact and legacy-partial direct-source motif states overlap: G"),
        ("missing", "direct-source motif states do not partition the marker-exact nodes"),
        ("orphan", "direct-source motif states do not partition the marker-exact nodes"),
    ],
)
def test_direct_source_motif_states_are_disjoint_and_exhaustive(
    mutation: str, expected: str
) -> None:
    source = deepcopy(_MT_SOURCE)
    states = source["direct_source_motif_states"]
    if mutation == "overlap":
        states["exact_nodes"].append("G")
        states["exact_nodes"].sort()
    elif mutation == "missing":
        states["legacy_partial_nodes"].remove("G")
    else:
        states["exact_nodes"].append("not-an-emitted-node")
        states["exact_nodes"].sort()

    assert expected in _issues_text(_validate_mt_source_schema(source))


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("missing", "invalid provenance fields"),
        ("unknown", "invalid source-motif status"),
        ("disagrees", "source-motif status disagrees with the direct-source motif frontier"),
    ],
)
def test_per_record_source_motif_status_is_explicit_and_agrees_with_frontier(
    mutation: str, expected: str
) -> None:
    source = deepcopy(_MT_SOURCE)
    if mutation == "missing":
        source["nodes"]["C"].pop("source_motif_status")
    elif mutation == "unknown":
        source["nodes"]["C"]["source_motif_status"] = "assumed"
    else:
        source["nodes"]["C"]["source_motif_status"] = "legacy_partial"

    assert expected in _issues_text(_validate_mt_source_schema(source))


def test_duplicate_tree_occurrence_is_reported_before_name_deduplication() -> None:
    tree = build_mt_tree()
    duplicated_name = tree["children"][0]["haplogroup"]
    tree["children"].append(deepcopy(tree["children"][0]))
    inventory = _index_mt_tree(tree)

    assert duplicated_name in inventory.duplicates
    assert len(inventory.duplicates[duplicated_name]) == 2
    issues = _validate_mt_registry_against_tree(_MT_SOURCE, inventory)
    assert issues
    assert all(issue.startswith("Duplicate emitted mtDNA node ") for issue in issues)
    assert any(f"Duplicate emitted mtDNA node {duplicated_name}" in issue for issue in issues)


def test_frontier_and_registry_digests_match_independent_canonicalizers() -> None:
    migration = _MT_SOURCE["migration"]

    assert migration["baseline_commit"] == BASELINE_COMMIT
    assert _canonical_sha256(migration["legacy_locked_exact_nodes"]) == (LEGACY_EXACT_NAMES_SHA256)
    assert _canonical_sha256(migration["baseline_exact_nodes"]) == (BASELINE_EXACT_NAMES_SHA256)
    assert _canonical_sha256(migration["locked_exact_nodes"]) == (LOCKED_EXACT_NAMES_SHA256)
    assert _canonical_sha256(migration["baseline_direct_motif_exact_nodes"]) == (
        DIRECT_MOTIF_EXACT_NAMES_SHA256
    )
    assert _canonical_sha256(migration["locked_direct_motif_exact_nodes"]) == (
        DIRECT_MOTIF_EXACT_NAMES_SHA256
    )
    assert _canonical_sha256(migration["initial_direct_motif_pending_nodes"]) == (
        INITIAL_DIRECT_MOTIF_PENDING_NAMES_SHA256
    )
    assert _canonical_sha256(migration["initial_pending_nodes"]) == (INITIAL_PENDING_NAMES_SHA256)
    assert (
        _canonical_sha256(
            {
                "array_exports": _MT_SOURCE["array_exports"],
                "array_cohorts": _MT_SOURCE["array_cohorts"],
            }
        )
        == ARRAY_MANIFEST_SHA256
    )
    assert (
        _canonical_sha256({"source": _MT_SOURCE["source"], "references": _MT_SOURCE["references"]})
        == SOURCE_METADATA_SHA256
    )
    assert (
        _canonical_sha256(
            {
                "direct_source_motif_states": _MT_SOURCE["direct_source_motif_states"],
                "omitted_nodes": _MT_SOURCE["omitted_nodes"],
                "structural_exceptions": _MT_SOURCE["structural_exceptions"],
                "pending_nodes": _MT_SOURCE["pending_nodes"],
            }
        )
        == STATE_PARTITION_SHA256
    )
    assert _canonical_sha256(_tree_projection(build_mt_tree())) == EMITTED_TREE_SHA256

    assert (
        _canonical_sha256(
            _v1_semantic_projection(_MT_SOURCE, migration["legacy_locked_exact_nodes"])
        )
        == LEGACY_V1_SEMANTIC_SHA256
    )
    assert (
        _tsv_sha256(_v1_coverage_rows(_MT_SOURCE, migration["legacy_locked_exact_nodes"]))
        == LEGACY_V1_COVERAGE_SHA256
    )
    assert (
        _canonical_sha256(_v1_semantic_projection(_MT_SOURCE, migration["baseline_exact_nodes"]))
        == BASELINE_V1_SEMANTIC_SHA256
    )
    assert (
        _tsv_sha256(_v1_coverage_rows(_MT_SOURCE, migration["baseline_exact_nodes"]))
        == BASELINE_V1_COVERAGE_SHA256
    )
    assert (
        _canonical_sha256(
            _baseline_v2_registry_projection(_MT_SOURCE, migration["baseline_exact_nodes"])
        )
        == BASELINE_V2_REGISTRY_SEMANTIC_SHA256
    )
    assert (
        _tsv_sha256(_locked_coverage_rows(_MT_SOURCE, migration["baseline_exact_nodes"]))
        == BASELINE_V2_COVERAGE_SHA256
    )
    assert (
        _canonical_sha256(_locked_semantic_projection(_MT_SOURCE, migration["locked_exact_nodes"]))
        == LOCKED_EXACT_SEMANTIC_SHA256
    )
    assert (
        _tsv_sha256(_locked_coverage_rows(_MT_SOURCE, migration["locked_exact_nodes"]))
        == LOCKED_EXACT_COVERAGE_SHA256
    )
    assert (
        _canonical_sha256(
            _direct_motif_semantic_projection(
                _MT_SOURCE, migration["baseline_direct_motif_exact_nodes"]
            )
        )
        == DIRECT_MOTIF_EXACT_SEMANTIC_SHA256
    )
    assert (
        _canonical_sha256(
            _direct_motif_semantic_projection(
                _MT_SOURCE, migration["locked_direct_motif_exact_nodes"]
            )
        )
        == DIRECT_MOTIF_EXACT_SEMANTIC_SHA256
    )

    expected_literals = {
        "legacy_locked_exact_nodes_sha256": LEGACY_EXACT_NAMES_SHA256,
        "legacy_v1_semantic_sha256": LEGACY_V1_SEMANTIC_SHA256,
        "legacy_v1_coverage_sha256": LEGACY_V1_COVERAGE_SHA256,
        "baseline_exact_nodes_sha256": BASELINE_EXACT_NAMES_SHA256,
        "baseline_v1_semantic_sha256": BASELINE_V1_SEMANTIC_SHA256,
        "baseline_v1_coverage_sha256": BASELINE_V1_COVERAGE_SHA256,
        "baseline_v2_registry_semantic_sha256": BASELINE_V2_REGISTRY_SEMANTIC_SHA256,
        "baseline_v2_coverage_membership_sha256": BASELINE_V2_COVERAGE_SHA256,
        "locked_exact_nodes_sha256": LOCKED_EXACT_NAMES_SHA256,
        "locked_exact_semantic_sha256": LOCKED_EXACT_SEMANTIC_SHA256,
        "locked_exact_coverage_membership_sha256": LOCKED_EXACT_COVERAGE_SHA256,
        "baseline_direct_motif_exact_nodes_sha256": DIRECT_MOTIF_EXACT_NAMES_SHA256,
        "baseline_direct_motif_semantic_sha256": DIRECT_MOTIF_EXACT_SEMANTIC_SHA256,
        "locked_direct_motif_exact_nodes_sha256": DIRECT_MOTIF_EXACT_NAMES_SHA256,
        "locked_direct_motif_semantic_sha256": DIRECT_MOTIF_EXACT_SEMANTIC_SHA256,
        "initial_direct_motif_pending_nodes_sha256": (INITIAL_DIRECT_MOTIF_PENDING_NAMES_SHA256),
        "initial_pending_nodes_sha256": INITIAL_PENDING_NAMES_SHA256,
        "array_manifest_sha256": ARRAY_MANIFEST_SHA256,
        "source_metadata_sha256": SOURCE_METADATA_SHA256,
        "state_partition_sha256": STATE_PARTITION_SHA256,
        "baseline_emitted_tree_sha256": EMITTED_TREE_SHA256,
    }
    for field, expected in expected_literals.items():
        assert migration[field] == expected


@pytest.mark.parametrize("destination", ["pending", "structural"])
def test_baseline_exact_node_cannot_regress_to_a_weaker_state(destination: str) -> None:
    source = deepcopy(_MT_SOURCE)
    source["nodes"].pop("C")
    source["migration"]["locked_exact_nodes"].remove("C")
    source["migration"]["locked_exact_nodes_sha256"] = _canonical_sha256(
        source["migration"]["locked_exact_nodes"]
    )
    if destination == "pending":
        source["pending_nodes"]["C"] = {"emitted_parent": "M8"}
    else:
        source["structural_exceptions"]["C"] = {
            "type": "markerless_passthrough",
            "emitted_parent": "M8",
            "source_status": "pending",
            "reason": "test-only attempted regression",
        }

    issues = _validate_mt_source_schema(source)
    assert "mtDNA baseline exact frontier regressed" in issues
    if destination == "structural":
        registry_issues = _validate_mt_registry_against_tree(
            source, _index_mt_tree(build_mt_tree())
        )
        assert registry_issues


def test_coherent_semantic_drift_cannot_rewrite_locked_digests() -> None:
    source = deepcopy(_MT_SOURCE)
    source["nodes"]["G1"]["direct_source_motif"][0]["notation"] = "T8200c"
    _refresh_semantic_projection_digests(source)

    issues = _validate_mt_source_schema(source)
    text = _issues_text(issues)
    assert "does not match its registry projection" not in text
    assert "legacy_v1_semantic_sha256 differs from the locked baseline" in text
    assert "baseline_v1_semantic_sha256 differs from the locked baseline" in text
    assert "baseline_v2_registry_semantic_sha256 differs from the locked baseline" in text
    assert "locked_exact_semantic_sha256 differs from the locked baseline" in text
    assert "baseline_direct_motif_semantic_sha256 differs from the locked baseline" in text
    assert "locked_direct_motif_semantic_sha256 differs from the locked baseline" in text


def test_direct_source_motif_exact_frontier_cannot_regress() -> None:
    source = deepcopy(_MT_SOURCE)
    source["direct_source_motif_states"]["exact_nodes"].remove("C")
    source["direct_source_motif_states"]["legacy_partial_nodes"].append("C")
    source["direct_source_motif_states"]["legacy_partial_nodes"].sort()
    source["migration"]["locked_direct_motif_exact_nodes"].remove("C")
    source["migration"]["locked_direct_motif_exact_nodes_sha256"] = _canonical_sha256(
        source["migration"]["locked_direct_motif_exact_nodes"]
    )

    text = _issues_text(_validate_mt_source_schema(source))
    assert "baseline direct-source motif frontier regressed" in text
    assert "direct-source motif pending frontier grew beyond its baseline" in text


def test_coherent_coverage_drift_cannot_rewrite_locked_digests() -> None:
    source = deepcopy(_MT_SOURCE)
    coverage = source["nodes"]["G"]["emitted_snps"][1]["array_coverage"]
    coverage["callable_snv_in"].remove("pgp_4162")
    _refresh_coverage_projection_digests(source)

    issues = _validate_mt_source_schema(source)
    text = _issues_text(issues)
    assert "does not match its registry projection" not in text
    assert "baseline_v2_coverage_membership_sha256 differs from the locked baseline" in text
    assert "locked_exact_coverage_membership_sha256 differs from the locked baseline" in text


def test_marker_and_source_direction_drift_fails_both_source_and_tree_guards() -> None:
    source = deepcopy(_MT_SOURCE)
    marker = source["nodes"]["G"]["emitted_snps"][0]
    marker["allele"] = "T"
    source["nodes"]["G"]["direct_source_motif"][0]["derived_allele"] = "T"
    _refresh_semantic_projection_digests(source)

    schema_text = _issues_text(_validate_mt_source_schema(source))
    assert "semantic_sha256 differs from the locked baseline" in schema_text
    registry_text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert "Marker-exact mtDNA node G has markers" in registry_text


def test_stored_digest_drift_fails_even_when_records_are_unchanged() -> None:
    source = deepcopy(_MT_SOURCE)
    source["migration"]["locked_exact_semantic_sha256"] = "0" * 64

    text = _issues_text(_validate_mt_source_schema(source))
    assert "locked_exact_semantic_sha256 differs from the locked baseline" in text
    assert "locked_exact_semantic_sha256 does not match its registry projection" in text


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("cohort-member-object", "non-string export member"),
        ("cohort-id-object", "invalid array cohort"),
        ("motif-owner-object", "invalid motif owner"),
    ],
)
def test_json_compatible_non_string_values_fail_without_crashing(
    mutation: str, expected: str
) -> None:
    source = deepcopy(_MT_SOURCE)
    if mutation == "cohort-member-object":
        source["array_cohorts"]["primary_four_23andme"]["export_ids"].append(
            {"not": "an export ID"}
        )
    elif mutation == "cohort-id-object":
        source["nodes"]["G"]["emitted_snps"][0]["array_coverage"]["cohort_id"] = {
            "not": "a cohort ID"
        }
    else:
        source["nodes"]["G"]["emitted_snps"][0]["motif_owner"] = {"not": "a source node"}

    assert expected in _issues_text(_validate_mt_source_schema(source))


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("marker", "marker with invalid fields"),
        ("coverage", "invalid coverage fields"),
    ],
)
def test_unknown_marker_and_coverage_fields_fail_closed(target: str, expected: str) -> None:
    source = deepcopy(_MT_SOURCE)
    marker = source["nodes"]["G"]["emitted_snps"][0]
    if target == "marker":
        marker["unreviewed_field"] = True
    else:
        marker["array_coverage"]["unreviewed_field"] = True

    assert expected in _issues_text(_validate_mt_source_schema(source))


def test_substitution_notation_must_match_declared_direction() -> None:
    source = deepcopy(_MT_SOURCE)
    source["nodes"]["G"]["direct_source_motif"][0]["notation"] = "C4833T"

    assert "notation disagrees with its declared allele direction" in _issues_text(
        _validate_mt_source_schema(source)
    )


@pytest.mark.parametrize(
    ("notation", "expected"),
    [
        ("T146C!", ("T", 146, "C", False, 1)),
        ("C146T!!", ("C", 146, "T", False, 2)),
        ("(T146C!!!)", ("T", 146, "C", True, 3)),
    ],
)
def test_substitution_notation_preserves_lineage_local_event_marks(
    notation: str, expected: tuple[str, int, str, bool, int]
) -> None:
    assert _mt_parse_substitution_notation(notation) == expected


@pytest.mark.parametrize("notation", ["(T146C", "T146C)", "T146C!?", "A0G"])
def test_invalid_substitution_notation_is_rejected(notation: str) -> None:
    assert _mt_parse_substitution_notation(notation) is None


def test_exact_structural_source_identity_cannot_alias_marker_exact_source() -> None:
    source = deepcopy(_MT_SOURCE)
    source["structural_exceptions"]["R0"] = {
        "type": "markerless_passthrough",
        "emitted_parent": "R",
        "source_status": "exact",
        "reason": "test-only attempted source alias",
        "source_node": "G",
        "source_topology": {
            "status": "exact",
            "emitted_parent_source_node": "R",
            "source_parent": "R",
            "flattened_source_path": [],
        },
        "direct_source_motif": [
            {
                "notation": "A73G",
                "mutation_type": "substitution",
                "pos": 73,
                "ancestral_allele": "A",
                "derived_allele": "G",
                "emitted": False,
                "omission_reason": "test-only markerless decision",
            }
        ],
        "emitted_snps": [],
    }

    assert "exact records repeat a direct source-node identity" in _issues_text(
        _validate_mt_source_schema(source)
    )


def test_new_marker_bearing_tree_node_cannot_enter_pending_frontier() -> None:
    source = deepcopy(_MT_SOURCE)
    source["pending_nodes"]["synthetic-marker-node"] = {"emitted_parent": "M"}
    tree = build_mt_tree()
    _find_node(tree, "M")["children"].append(
        {
            "haplogroup": "synthetic-marker-node",
            "defining_snps": [{"rsid": "test1798", "pos": 42, "allele": "A"}],
            "children": [],
        }
    )

    schema_text = _issues_text(_validate_mt_source_schema(source))
    assert "pending frontier grew beyond the initial audited tree" in schema_text
    registry_text = _issues_text(_validate_mt_registry_against_tree(source, _index_mt_tree(tree)))
    assert "locked issue-1798 baseline" in registry_text


@pytest.mark.parametrize(
    ("category", "name", "wrong_parent", "expected"),
    [
        ("nodes", "Z", "M", "Marker-exact mtDNA node Z declares parent 'M'"),
        ("pending_nodes", "A", "M", "Pending mtDNA node A declares parent 'M'"),
        (
            "structural_exceptions",
            "R0",
            "N",
            "Structural mtDNA node R0 declares parent 'N'",
        ),
    ],
)
def test_exact_pending_and_structural_parent_declarations_are_live_checked(
    category: str, name: str, wrong_parent: str, expected: str
) -> None:
    source = deepcopy(_MT_SOURCE)
    source[category][name]["emitted_parent"] = wrong_parent

    issues = _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    assert expected in _issues_text(issues)


def test_moving_copied_z_from_m8_to_m_is_detected_as_topology_drift() -> None:
    tree = build_mt_tree()
    m8 = _find_node(tree, "M8")
    z = next(child for child in m8["children"] if child["haplogroup"] == "Z")
    m8["children"].remove(z)
    _find_node(tree, "M")["children"].append(z)

    issues = _validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree))
    text = _issues_text(issues)
    assert "Marker-exact mtDNA node Z declares parent 'M8'; emitted parent is 'M'" in text
    assert "locked issue-1798 baseline" in text


def test_structural_exceptions_are_narrow_and_markerless() -> None:
    assert _MT_SOURCE["structural_exceptions"] == {
        "mt-MRCA": {
            "type": "root",
            "emitted_parent": None,
            "source_status": "synthetic",
            "reason": "Synthetic tree-walk root; it emits no defining marker.",
        },
        "R0": {
            "type": "markerless_passthrough",
            "emitted_parent": "R",
            "source_status": "pending",
            "reason": (
                "Retained to preserve the emitted tree path while its direct Build-17 "
                "motif and source topology remain pending; it emits no defining marker."
            ),
        },
    }

    tree = build_mt_tree()
    _find_node(tree, "R0")["defining_snps"].append(
        {"rsid": "structural-escape", "pos": 1, "allele": "A"}
    )
    text = _issues_text(_validate_mt_registry_against_tree(_MT_SOURCE, _index_mt_tree(tree)))
    assert "Structural mtDNA pass-through R0 must be markerless" in text
    assert "mtDNA markerless nodes do not equal the structural exceptions" in text


def test_synthetic_root_cannot_be_retyped_or_reparented() -> None:
    source = deepcopy(_MT_SOURCE)
    source["structural_exceptions"]["mt-MRCA"].update(
        {"type": "markerless_passthrough", "emitted_parent": "L3"}
    )

    text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert "Structural mtDNA pass-through mt-MRCA cannot be the root" in text
    assert "declares parent 'L3'; emitted parent is None" in text


def test_six_export_manifest_and_two_23andme_cohorts_are_pinned() -> None:
    assert _MT_SOURCE["array_exports"] == EXPECTED_EXPORTS
    assert _MT_SOURCE["array_cohorts"] == EXPECTED_COHORTS
    assert "pgp_ancestry_4190" not in PRIMARY_EXPORTS
    assert "pgp_ancestry_4190" not in HISTORICAL_EXPORTS
    assert _MT_SOURCE["array_exports"]["pgp_ancestry_4190"]["role"] == (
        "other_vendor_comparator_only"
    )

    k1b = _MT_SOURCE["nodes"]["K1b"]["emitted_snps"][0]
    assert k1b["pos"] == 5913
    assert k1b["array_coverage"] == {
        "cohort_id": "primary_four_23andme",
        "position_present_in": ["pgp_4139", "pgp_4187"],
        "callable_snv_in": [],
    }


@pytest.mark.parametrize(
    ("members", "expected"),
    [
        (
            [*PRIMARY_EXPORTS, "pgp_ancestry_4190"],
            "includes the Ancestry comparator",
        ),
        ([*PRIMARY_EXPORTS, "pgp_4139"], "repeats an export"),
        ([*PRIMARY_EXPORTS, "unknown-export"], "names an unknown export"),
    ],
)
def test_cohort_membership_rejects_ancestry_duplicates_and_unknown_exports(
    members: list[str], expected: str
) -> None:
    source = deepcopy(_MT_SOURCE)
    source["array_cohorts"]["primary_four_23andme"]["export_ids"] = members

    assert expected in _issues_text(_validate_mt_source_schema(source))


def test_marker_coverage_rejects_outside_and_callable_beyond_present_members() -> None:
    outside = deepcopy(_MT_SOURCE)
    outside["nodes"]["K1b"]["emitted_snps"][0]["array_coverage"]["position_present_in"].append(
        "pgp_1050"
    )
    assert "position-present exports outside its cohort" in _issues_text(
        _validate_mt_source_schema(outside)
    )

    callable_absent = deepcopy(_MT_SOURCE)
    callable_absent["nodes"]["K1b"]["emitted_snps"][0]["array_coverage"]["callable_snv_in"].append(
        "pgp_4162"
    )
    assert "callable where its position is absent" in _issues_text(
        _validate_mt_source_schema(callable_absent)
    )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("type", "omitted source node CZ has an invalid omission type"),
        ("reason", "omitted source node CZ has no reason"),
        ("overlap", "source nodes are both omitted and emitted: G"),
    ],
)
def test_omissions_require_typed_reasons_and_cannot_overlap_emitted_nodes(
    mutation: str, expected: str
) -> None:
    source = deepcopy(_MT_SOURCE)
    if mutation == "type":
        source["omitted_nodes"]["CZ"]["type"] = "hand_waved"
    elif mutation == "reason":
        source["omitted_nodes"]["CZ"]["reason"] = " "
    else:
        source["omitted_nodes"]["G"] = {
            "type": "unreportable_source_node",
            "reason": "test-only overlap",
        }

    assert expected in _issues_text(_validate_mt_source_schema(source))


def test_migration_status_cannot_claim_complete_with_pending_nodes() -> None:
    source = deepcopy(_MT_SOURCE)
    source["migration"]["status"] = "complete"

    text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert "migration status must be 'in_progress'" in text


def test_migration_completion_requires_every_direct_source_motif_to_be_exact() -> None:
    tree = {
        "haplogroup": "root",
        "defining_snps": [],
        "children": [
            {
                "haplogroup": "child",
                "defining_snps": [{"rsid": "test", "pos": 1, "allele": "G"}],
            }
        ],
    }
    inventory = _index_mt_tree(tree)
    source = {
        "pending_nodes": {},
        "nodes": {"child": {"source_topology": {"status": "exact"}}},
        "structural_exceptions": {
            "root": {"source_status": "synthetic"},
        },
        "direct_source_motif_states": {
            "exact_nodes": [],
            "legacy_partial_nodes": ["child"],
        },
    }

    assert not _mt_migration_complete_ready(source, inventory)
    source["direct_source_motif_states"] = {
        "exact_nodes": ["child"],
        "legacy_partial_nodes": [],
    }
    assert _mt_migration_complete_ready(source, inventory)


def test_clearing_pending_map_without_migrating_nodes_fails_closed() -> None:
    source = deepcopy(_MT_SOURCE)
    source["pending_nodes"].clear()
    source["migration"]["status"] = "in_progress"

    schema_text = _issues_text(_validate_mt_source_schema(source))
    assert "initial pending frontier contains nodes with no current disposition" in schema_text
    registry_text = _issues_text(
        _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree()))
    )
    assert "provenance partition differs from the emitted tree" in registry_text
    assert "marker-bearing nodes do not equal exact plus pending states" in registry_text


def test_source_aware_recurrence_allows_same_direction_for_distinct_exact_owners() -> None:
    m8a_marker = next(
        marker for marker in _MT_SOURCE["nodes"]["M8a"]["emitted_snps"] if marker["pos"] == 14470
    )
    x_marker = next(
        marker for marker in _MT_SOURCE["nodes"]["X"]["emitted_snps"] if marker["pos"] == 14470
    )

    assert m8a_marker["rsid"] == x_marker["rsid"] == "i5014470"
    assert m8a_marker["motif_owner"] == "M8a"
    assert x_marker["motif_owner"] == "X"
    assert {"M8a", "X"} <= set(DIRECT_MOTIF_EXACT_NODES)
    assert all(
        any(
            mutation["notation"] == "T14470C"
            for mutation in _MT_SOURCE["nodes"][owner]["direct_source_motif"]
        )
        for owner in ("M8a", "X")
    )
    assert _validate_mt_source_schema(_MT_SOURCE) == []


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("motif_owner", "M8a", "outside motif owner 'M8a'"),
        ("allele", "A", "does not match its source mutation direction"),
    ],
)
def test_recurrent_marker_rejects_wrong_owner_and_direction(
    field: str, value: str, expected: str
) -> None:
    source = deepcopy(_MT_SOURCE)
    marker = next(item for item in source["nodes"]["X"]["emitted_snps"] if item["pos"] == 14470)
    marker[field] = value

    assert expected in _issues_text(_validate_mt_source_schema(source))


def _flattened_g1_source() -> dict[str, Any]:
    source = deepcopy(_MT_SOURCE)
    record = source["nodes"]["G1"]
    flattened_mutation = record["direct_source_motif"].pop(0)
    record["emitted_snps"][0]["motif_owner"] = "G-flat"
    reason = "test-only source intermediate omitted from the emitted tree"
    source["omitted_nodes"]["G-flat"] = {
        "type": "flattened_unreportable_source_intermediate",
        "reason": reason,
    }
    record["source_topology"] = {
        "status": "exact",
        "emitted_parent_source_node": "G",
        "source_parent": "G-flat",
        "flattened_source_path": [
            {
                "source_node": "G-flat",
                "source_parent": "G",
                "reason": reason,
                "direct_source_motif": [flattened_mutation],
            }
        ],
    }
    return source


def test_flattened_source_path_accepts_ordered_adjacency_and_marker_ownership() -> None:
    source = _flattened_g1_source()
    issues: list[str] = []
    _mt_validate_exact_record(
        "G1",
        source["nodes"]["G1"],
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )

    assert issues == []
    # The topology-only registry guard can validate this state without changing
    # the locked emitted-tree fingerprint; schema digest locks intentionally remain.
    assert _validate_mt_registry_against_tree(source, _index_mt_tree(build_mt_tree())) == []


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("topology", "source topology for mtDNA node G1 has invalid fields"),
        ("path", "path step 0 has invalid fields"),
    ],
)
def test_unknown_exact_topology_fields_fail_closed(target: str, expected: str) -> None:
    source = _flattened_g1_source()
    topology = source["nodes"]["G1"]["source_topology"]
    if target == "topology":
        topology["unreviewed_field"] = True
    else:
        topology["flattened_source_path"][0]["unreviewed_field"] = True

    issues: list[str] = []
    _mt_validate_exact_record(
        "G1",
        source["nodes"]["G1"],
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )
    assert expected in _issues_text(issues)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("adjacency", "breaks adjacency at G-flat"),
        ("owner", "outside motif owner 'not-on-path'"),
        ("direction", "does not match its source mutation direction"),
    ],
)
def test_flattened_source_path_rejects_bad_adjacency_owner_and_direction(
    mutation: str, expected: str
) -> None:
    source = _flattened_g1_source()
    record = source["nodes"]["G1"]
    if mutation == "adjacency":
        record["source_topology"]["flattened_source_path"][0]["source_parent"] = "wrong-parent"
    elif mutation == "owner":
        record["emitted_snps"][0]["motif_owner"] = "not-on-path"
    else:
        record["emitted_snps"][0]["allele"] = "A"

    issues: list[str] = []
    _mt_validate_exact_record(
        "G1",
        record,
        source["omitted_nodes"],
        source["array_cohorts"],
        issues,
    )
    assert expected in _issues_text(issues)


def test_derived_provenance_metadata_and_bundle_compatibility_are_exact() -> None:
    tree = build_mt_tree()
    inventory = _index_mt_tree(tree)
    summary = _summarize_mt_provenance(_MT_SOURCE, inventory)

    assert summary["migration_status"] == "in_progress"
    assert summary["emitted_nodes"] == 194
    assert summary["marker_bearing_nodes"] == 192
    assert summary["marker_exact_nodes"]["count"] == 39
    assert summary["direct_source_motif_nodes"] == {
        "exact": {"count": 27, "names": DIRECT_MOTIF_EXACT_NODES},
        "legacy_partial": {
            "count": 12,
            "names": DIRECT_MOTIF_LEGACY_PARTIAL_NODES,
        },
    }
    assert summary["structural_nodes"] == {
        "count": 2,
        "names": ["R0", "mt-MRCA"],
    }
    assert summary["pending_nodes"]["count"] == 153
    assert summary["marker_records"] == {
        "emitted": 395,
        "marker_exact": 87,
        "marker_exact_by_cohort": {
            "historical_five_23andme_including_2014": 6,
            "primary_four_23andme": 81,
        },
    }
    assert summary["source_mutation_decisions"] == {
        "total": 117,
        "emitted": 87,
        "omitted": 30,
        "direct_motif_exact": 80,
        "direct_motif_legacy_partial": 37,
        "recurrent_or_uncertain_events": 0,
        "reversion_events": 11,
        "reversion_marks": 11,
    }
    assert summary["emitted_parent_edges"] == {
        "total": 193,
        "validated_declarations": 193,
    }
    assert summary["source_parent_edges"] == {"validated": 0, "pending": 193}
    assert summary["omitted_source_nodes"] == {"count": 2, "names": ["CZ", "K1c"]}
    assert summary["arrays"] == {"exports": 6, "cohorts": 2}
    assert summary["locked_exact_frontier"] == {
        "count": 39,
        "sha256": BASELINE_EXACT_NAMES_SHA256,
    }
    assert summary["locked_direct_motif_frontier"] == {
        "count": 27,
        "sha256": DIRECT_MOTIF_EXACT_NAMES_SHA256,
    }

    bundle = build_bundle()
    mt_audit = bundle["sources"]["mt"]["audit"]
    assert bundle["version"] == "1.1.9"
    assert bundle["stats"]["mt_haplogroups"] == 194
    assert bundle["stats"]["mt_defining_snps"] == 395
    assert mt_audit["schema_version"] == 2
    assert mt_audit["audited_nodes"] == sorted(_MT_SOURCE["nodes"])
    assert mt_audit["omitted_nodes"] == {
        name: record["reason"] for name, record in sorted(_MT_SOURCE["omitted_nodes"].items())
    }
    assert mt_audit["provenance"] == summary
    assert bundle["trees"]["mt"] == tree
