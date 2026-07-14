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
BASELINE_EXACT_NAMES_SHA256 = "e1acb0428d22ecfd4549614c92acc4987fed3c6c735c679c354957ad0cf5b885"
BASELINE_V1_SEMANTIC_SHA256 = "9228fe0e2158acc8afc324227860c33031c1e1fda8d256f807e19d10eff44972"
BASELINE_V1_COVERAGE_SHA256 = "4a6ceb1fe2210316d8121c67715d7da2277d56f6714910102cc4d6a92d0eff2e"
BASELINE_V2_DIRECT_SEMANTIC_SHA256 = (
    "156762467005af2445abe582258a22384b56a3bc80fd8a147669963c32147a7e"
)
BASELINE_V2_COVERAGE_SHA256 = "036b43c4e5e4e41fd80c1ca17e4c6b53f9e7ff5fc40c4408cb4179a8328a3d3a"
LOCKED_EXACT_NAMES_SHA256 = "e1acb0428d22ecfd4549614c92acc4987fed3c6c735c679c354957ad0cf5b885"
LOCKED_EXACT_SEMANTIC_SHA256 = "e370e48564a5e1ec51960f24608c1d1edd4891e4be9f68b7e001db6ea4a19faa"
LOCKED_EXACT_COVERAGE_SHA256 = "036b43c4e5e4e41fd80c1ca17e4c6b53f9e7ff5fc40c4408cb4179a8328a3d3a"
INITIAL_PENDING_NAMES_SHA256 = "c782d49b4b2d4e3e4fa3034615ead6e2eb647b60f4dff0564dd59493b44f4cde"
ARRAY_MANIFEST_SHA256 = "42de22517a4644884596e36b0499a4fc45f264986c63f6fb239452b88719f977"
SOURCE_METADATA_SHA256 = "5b3a3578fc208c91f6c3fdcc6d772f5071851b3604762b9e81994cf2632deb3d"
STATE_PARTITION_SHA256 = "bedc610cd57aec4ede72a3832bf5e03247fae471ea66fb764dc6792d2ef3673d"
EMITTED_TREE_SHA256 = "2088185a21395806d8ce6b9d7a33b4c1056ff7985e46308bbf2432a9f10b3f63"

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


def _baseline_v2_direct_projection(
    source: dict[str, Any], names: list[str]
) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in record.items() if key != "source_topology"}
        | {
            "node": name,
            "emitted_snps": _locked_semantic_projection(source, [name])[0]["emitted_snps"],
        }
        for name in names
        for record in [source["nodes"][name]]
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
    migration["baseline_v2_direct_semantic_sha256"] = _canonical_sha256(
        _baseline_v2_direct_projection(source, migration["baseline_exact_nodes"])
    )
    migration["locked_exact_semantic_sha256"] = _canonical_sha256(
        _locked_semantic_projection(source, migration["locked_exact_nodes"])
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
    assert inventory.marker_count == 396
    assert inventory.edge_count == 193
    assert set(_MT_SOURCE["nodes"]) | set(_MT_SOURCE["structural_exceptions"]) | set(
        _MT_SOURCE["pending_nodes"]
    ) == set(inventory.by_name)
    assert set(_MT_SOURCE["nodes"]) | set(_MT_SOURCE["pending_nodes"]) == set(
        inventory.marker_bearing_names
    )
    assert set(_MT_SOURCE["structural_exceptions"]) == set(inventory.markerless_names)
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

    assert _canonical_sha256(migration["legacy_locked_exact_nodes"]) == (LEGACY_EXACT_NAMES_SHA256)
    assert _canonical_sha256(migration["baseline_exact_nodes"]) == (BASELINE_EXACT_NAMES_SHA256)
    assert _canonical_sha256(migration["locked_exact_nodes"]) == (LOCKED_EXACT_NAMES_SHA256)
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
            _baseline_v2_direct_projection(_MT_SOURCE, migration["baseline_exact_nodes"])
        )
        == BASELINE_V2_DIRECT_SEMANTIC_SHA256
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

    expected_literals = {
        "legacy_locked_exact_nodes_sha256": LEGACY_EXACT_NAMES_SHA256,
        "legacy_v1_semantic_sha256": LEGACY_V1_SEMANTIC_SHA256,
        "legacy_v1_coverage_sha256": LEGACY_V1_COVERAGE_SHA256,
        "baseline_exact_nodes_sha256": BASELINE_EXACT_NAMES_SHA256,
        "baseline_v1_semantic_sha256": BASELINE_V1_SEMANTIC_SHA256,
        "baseline_v1_coverage_sha256": BASELINE_V1_COVERAGE_SHA256,
        "baseline_v2_direct_semantic_sha256": BASELINE_V2_DIRECT_SEMANTIC_SHA256,
        "baseline_v2_coverage_membership_sha256": BASELINE_V2_COVERAGE_SHA256,
        "locked_exact_nodes_sha256": LOCKED_EXACT_NAMES_SHA256,
        "locked_exact_semantic_sha256": LOCKED_EXACT_SEMANTIC_SHA256,
        "locked_exact_coverage_membership_sha256": LOCKED_EXACT_COVERAGE_SHA256,
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
    source["nodes"]["G"]["direct_source_motif"][0]["notation"] = "A4833g"
    _refresh_semantic_projection_digests(source)

    issues = _validate_mt_source_schema(source)
    text = _issues_text(issues)
    assert "does not match its registry projection" not in text
    assert "legacy_v1_semantic_sha256 differs from the locked baseline" in text
    assert "baseline_v1_semantic_sha256 differs from the locked baseline" in text
    assert "baseline_v2_direct_semantic_sha256 differs from the locked baseline" in text
    assert "locked_exact_semantic_sha256 differs from the locked baseline" in text


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


def test_source_aware_recurrence_allows_same_position_for_distinct_motif_owners() -> None:
    k1_marker = next(
        marker for marker in _MT_SOURCE["nodes"]["K1"]["emitted_snps"] if marker["pos"] == 10398
    )
    y_marker = next(
        marker for marker in _MT_SOURCE["nodes"]["Y_mt"]["emitted_snps"] if marker["pos"] == 10398
    )

    assert k1_marker["rsid"] == y_marker["rsid"] == "i5010398"
    assert k1_marker["motif_owner"] == "K1"
    assert _MT_SOURCE["nodes"]["Y_mt"]["source_node"] == "Y"
    assert y_marker["motif_owner"] == "Y"
    assert any(
        mutation["notation"] == "A10398G!"
        for mutation in _MT_SOURCE["nodes"]["Y_mt"]["direct_source_motif"]
    )
    assert _validate_mt_source_schema(_MT_SOURCE) == []


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("motif_owner", "K1", "outside motif owner 'K1'"),
        ("allele", "T", "does not match its source mutation direction"),
    ],
)
def test_recurrence_marker_rejects_wrong_owner_and_direction(
    field: str, value: str, expected: str
) -> None:
    source = deepcopy(_MT_SOURCE)
    marker = next(item for item in source["nodes"]["Y_mt"]["emitted_snps"] if item["pos"] == 10398)
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
    assert summary["marker_exact_nodes"]["count"] == 38
    assert summary["structural_nodes"] == {
        "count": 2,
        "names": ["R0", "mt-MRCA"],
    }
    assert summary["pending_nodes"]["count"] == 154
    assert summary["marker_records"] == {
        "emitted": 396,
        "marker_exact": 86,
        "marker_exact_by_cohort": {
            "historical_five_23andme_including_2014": 6,
            "primary_four_23andme": 80,
        },
    }
    assert summary["source_mutation_decisions"] == {
        "total": 116,
        "emitted": 86,
        "omitted": 30,
        "recurrent_events": 11,
    }
    assert summary["emitted_parent_edges"] == {
        "total": 193,
        "validated_declarations": 193,
    }
    assert summary["source_parent_edges"] == {"validated": 0, "pending": 193}
    assert summary["omitted_source_nodes"] == {"count": 2, "names": ["CZ", "K1c"]}
    assert summary["arrays"] == {"exports": 6, "cohorts": 2}
    assert summary["locked_exact_frontier"] == {
        "count": 38,
        "sha256": BASELINE_EXACT_NAMES_SHA256,
    }

    bundle = build_bundle()
    mt_audit = bundle["sources"]["mt"]["audit"]
    assert bundle["version"] == "1.1.9"
    assert bundle["stats"]["mt_haplogroups"] == 194
    assert bundle["stats"]["mt_defining_snps"] == 396
    assert mt_audit["schema_version"] == 2
    assert mt_audit["audited_nodes"] == sorted(_MT_SOURCE["nodes"])
    assert mt_audit["omitted_nodes"] == {
        name: record["reason"] for name, record in sorted(_MT_SOURCE["omitted_nodes"].items())
    }
    assert mt_audit["provenance"] == summary
    assert bundle["trees"]["mt"] == tree
