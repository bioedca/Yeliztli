"""Synthetic controls for GRCh37 calls scored on PCA training alleles (#2566)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import runpy
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa

from backend.analysis import ancestry
from backend.db.tables import annotated_variants, findings, raw_variants

ORACLE = Path(__file__).parents[1] / "fixtures" / "synthetic_eur_aim_coordinates.tsv"
REVERSED = (
    "rs11638659",
    "rs75536403",
    "rs28493303",
    "rs11901495",
    "rs28689766",
    "rs199898426",
)


def _oracle() -> dict[str, list[str]]:
    rows = [
        line.split("\t")
        for line in ORACLE.read_text().splitlines()
        if line and not line.startswith("#")
    ]
    assert len(rows) == 5000
    assert len({row[0] for row in rows}) == len(rows)
    return {row[0]: row for row in rows}


ROWS = _oracle()
SAME_PALINDROME = next(
    row[0]
    for row in ROWS.values()
    if row[5] == "+" and set(row[6].split("/")) in ({"A", "T"}, {"C", "G"})
)
CASES = (*REVERSED, "rs117382002", SAME_PALINDROME)
BUNDLE = ORACLE.parents[2] / "backend/data/panels/ancestry_pca_bundle.npz"


@pytest.fixture(scope="module")
def training_bundle() -> ancestry.AncestryBundle:
    return ancestry.load_ancestry_bundle()


def _single_aim(training_bundle: ancestry.AncestryBundle, rsid: str) -> ancestry.AncestryBundle:
    marker = next(snp for snp in training_bundle.snps if snp.rsid == rsid)
    return replace(
        training_bundle,
        snps=[marker],
        n_components=1,
        loadings=np.array([[1.0]]),
        means=np.array([1.0]),
        stds=np.array([1.0]),
        populations=["LOW", "HIGH"],
        reference_centroids={"LOW": np.array([-1.0]), "HIGH": np.array([1.0])},
    )


def _synthetic_call(rsid: str, dosage: int) -> str:
    ref, alt = ROWS[rsid][6].split("/")
    return (ref * 2, ref + alt, alt * 2)[dosage]


@pytest.mark.parametrize("rsid", CASES)
@pytest.mark.parametrize("dosage", (0, 1, 2), ids=("hom_ref", "heterozygous", "hom_alt"))
def test_projection_scores_grch37_calls_on_training_orientation(training_bundle, rsid, dosage):
    bundle = _single_aim(training_bundle, rsid)
    projected, used = ancestry._project_onto_pca(bundle, {rsid: _synthetic_call(rsid, dosage)})
    assert used == 1
    np.testing.assert_array_equal(projected, [dosage - 1.0])
    assert ancestry.compute_missing_aim_rate({rsid: _synthetic_call(rsid, dosage)}, bundle) == 0.0


@pytest.mark.parametrize("rsid", CASES)
@pytest.mark.parametrize("dosage", (0, 1, 2), ids=("hom_ref", "heterozygous", "hom_alt"))
def test_bootstrap_uses_the_same_harmonized_dosage(training_bundle, monkeypatch, rsid, dosage):
    bundle = _single_aim(training_bundle, rsid)
    targets = []

    def record_projection(centroid_matrix, target_pcs, n_pops):
        assert centroid_matrix.shape == (1, 2)
        assert n_pops == 2
        targets.append(target_pcs.copy())
        return np.array([0.5, 0.5])

    monkeypatch.setattr(ancestry, "_solve_admixture_fcls", record_projection)
    low, high = ancestry.bootstrap_admixture_nnls(
        np.array([dosage - 1.0]),
        bundle,
        {rsid: _synthetic_call(rsid, dosage)},
        n_iterations=3,
        rng_seed=2566,
    )
    assert len(targets) == 3
    for projected in targets:
        np.testing.assert_array_equal(projected, [dosage - 1.0])
    assert low == high == {"LOW": 0.5, "HIGH": 0.5}


@pytest.mark.parametrize("genotype", (None, "--", "00", "??", "A", "CCC", "AA", "CA", "CX"))
def test_unscorable_calls_are_missing_in_projection_bootstrap_and_coverage(
    training_bundle, monkeypatch, genotype
):
    # This public marker's supported GRCh37 pair is C/T; every call here is
    # deliberately absent, malformed or incompatible with that pair.
    rsid = "rs117382002"
    assert ROWS[rsid][6] == "C/T"
    bundle = _single_aim(training_bundle, rsid)
    calls = {rsid: genotype}
    projected, used = ancestry._project_onto_pca(bundle, calls)
    assert used == 0
    np.testing.assert_array_equal(projected, [0.0])
    assert ancestry.compute_missing_aim_rate(calls, bundle) == 1.0
    targets = []

    def record_projection(centroid_matrix, target_pcs, n_pops):
        targets.append(target_pcs.copy())
        return np.array([0.5, 0.5])

    monkeypatch.setattr(ancestry, "_solve_admixture_fcls", record_projection)
    ancestry.bootstrap_admixture_nnls(projected, bundle, calls, n_iterations=2, rng_seed=2566)
    assert len(targets) == 2
    for target in targets:
        np.testing.assert_array_equal(target, [0.0])


@pytest.mark.parametrize("dosage", (0, 1, 2), ids=("hom_ref", "heterozygous", "hom_alt"))
def test_all_5000_mapped_aims_preserve_training_identity(training_bundle, dosage):
    assert len(training_bundle.snps) == len(ROWS) == 5000
    assert {s.rsid for s in training_bundle.snps} == set(ROWS)
    assert {s.rsid for s in training_bundle.snps if s.grch37_strand == -1} == set(REVERSED)
    for snp in training_bundle.snps:
        row = ROWS[snp.rsid]
        assert (snp.chrom, snp.pos, snp.grch37_pos) == (row[1], int(row[2]), int(row[3]))
        assert snp.grch37_strand == (1 if row[5] == "+" else -1)
        call = _synthetic_call(snp.rsid, dosage)
        assert ancestry._encode_aim_dosage(call, snp) == dosage, snp.rsid
        assert ancestry._encode_aim_dosage(call.lower()[::-1], snp) == dosage, snp.rsid
    with np.load(BUNDLE, allow_pickle=False) as source:
        assert [s.ref for s in training_bundle.snps] == list(source["aim_a2"])
        assert [s.alt for s in training_bundle.snps] == list(source["aim_a1"])
        for name in ("loadings", "means", "stds"):
            np.testing.assert_array_equal(getattr(training_bundle, name), source[name])


def test_full_panel_projection_and_seeded_bootstrap_match_independent_dosages(
    training_bundle, monkeypatch
):
    bundle = training_bundle
    calls = {}
    standardized = np.zeros(5000)
    valid = 0
    for i, snp in enumerate(bundle.snps):
        if i % 11 == 0:
            calls[snp.rsid] = "??"
        elif i % 13 == 0:
            calls[snp.rsid] = "XYZ"  # malformed is missing, never a zero dosage
        else:
            dose = i % 3
            calls[snp.rsid] = _synthetic_call(snp.rsid, dose)
            standardized[i] = (dose - bundle.means[i]) / bundle.stds[i]
            valid += 1
    pcs, used = ancestry._project_onto_pca(bundle, calls)
    np.testing.assert_array_equal(pcs, standardized @ bundle.loadings)
    assert used == valid
    assert ancestry.compute_missing_aim_rate(calls, bundle) == (5000 - valid) / 5000
    targets = []

    def record_projection(centroid_matrix, target_pcs, n_pops):
        targets.append(target_pcs.copy())
        return np.full(n_pops, 1 / n_pops)

    monkeypatch.setattr(ancestry, "_solve_admixture_fcls", record_projection)
    ancestry.bootstrap_admixture_nnls(pcs, bundle, calls, n_iterations=5, rng_seed=2566)
    rng = np.random.default_rng(2566)
    assert len(targets) == 5
    for target in targets:
        idx = rng.integers(0, 5000, size=5000)
        np.testing.assert_array_equal(target, standardized[idx] @ bundle.loadings[idx, :])


def _mapping_copy(tmp_path):
    for suffix in (".npz", ".grch37.json", ".grch37.tsv"):
        shutil.copyfile(BUNDLE.with_suffix(suffix), tmp_path / BUNDLE.with_suffix(suffix).name)
    return tmp_path / BUNDLE.name


@pytest.mark.parametrize("sidecar", (".grch37.json", ".grch37.tsv"))
def test_file_loaded_bundle_requires_complete_mapping(tmp_path, sidecar):
    path = _mapping_copy(tmp_path)
    path.with_suffix(sidecar).unlink()
    with pytest.raises(FileNotFoundError):
        ancestry.load_ancestry_bundle(path)


@pytest.mark.parametrize(
    "key,value",
    (
        ("schema_version", 2),
        ("mapping_version", "unknown"),
        ("training_build", "GRCh37"),
        ("sample_build", "GRCh38"),
        ("sample_strand", "-"),
        ("marker_count", 4999),
        ("bundle_sha256", "0" * 64),
        ("mapping_sha256", "0" * 64),
    ),
)
def test_mapping_rejects_incompatible_metadata(tmp_path, key, value):
    path = _mapping_copy(tmp_path)
    metadata_path = path.with_suffix(".grch37.json")
    metadata = json.loads(metadata_path.read_text())
    metadata[key] = value
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="mapping metadata"):
        ancestry.load_ancestry_bundle(path)


@pytest.mark.parametrize(
    "defect",
    (
        "duplicate",
        "missing",
        "extra",
        "unknown",
        "chrom",
        "training_position",
        "sample_position",
        "strand_zero",
        "strand_missing",
        "training_ref",
        "sample_ref",
        "palindromic_swap",
        "evidence_missing",
        "extra_column",
    ),
)
def test_mapping_rejects_invalid_rows_even_with_a_matching_file_hash(tmp_path, defect):
    path = _mapping_copy(tmp_path)
    table_path = path.with_suffix(".grch37.tsv")
    rows = list(csv.reader(table_path.read_text().splitlines(), delimiter="\t"))
    assert len(rows) == 5001
    if defect == "duplicate":
        rows[2] = rows[1].copy()
    elif defect == "missing":
        rows.pop()
    elif defect == "extra":
        rows.append(rows[-1].copy())
    elif defect == "palindromic_swap":
        row = next(r for r in rows[1:] if r[0] == "rs75536403")
        row[7], row[8] = row[8], row[7]
    else:
        field, value = {
            "unknown": (0, "rsUNKNOWN"),
            "chrom": (1, "1"),
            "training_position": (2, "1"),
            "sample_position": (5, "0"),
            "strand_zero": (6, "0"),
            "strand_missing": (6, ""),
            "training_ref": (3, "N"),
            "sample_ref": (7, "A"),
            "evidence_missing": (9, ""),
            "extra_column": (10, "unrecognized"),
        }[defect]
        if field == 10:
            rows[2].append(value)
        else:
            rows[1][field] = value
    out = io.StringIO(newline="")
    csv.writer(out, delimiter="\t", lineterminator="\n").writerows(rows)
    table_path.write_text(out.getvalue())
    metadata_path = path.with_suffix(".grch37.json")
    metadata = json.loads(metadata_path.read_text())
    metadata["mapping_sha256"] = hashlib.sha256(table_path.read_bytes()).hexdigest()
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="mapping"):
        ancestry.load_ancestry_bundle(path)


def test_shipped_mapping_reproduces_from_public_evidence():
    script = (
        BUNDLE.parents[3]
        / "data/science-evidence/2026-11-08-pca-strand-2566/scripts/build_mapping.py"
    )
    table, metadata = runpy.run_path(str(script))["build"]()
    assert BUNDLE.with_suffix(".grch37.tsv").read_bytes() == table
    assert BUNDLE.with_suffix(".grch37.json").read_bytes() == metadata


def test_scientific_packet_hashes_and_refresh_audit():
    packet = BUNDLE.parents[3] / "data/science-evidence/2026-11-08-pca-strand-2566"
    manifest = json.loads((packet / "manifest.json").read_text())
    assert manifest["payloads"]
    assert manifest["payload_count"] == len(manifest["payloads"])
    for relative, digest in manifest["payloads"].items():
        assert hashlib.sha256((packet / relative).read_bytes()).hexdigest() == digest, relative
    audit = runpy.run_path(str(packet / "scripts/audit_refresh.py"))["audit"]()
    assert audit == json.loads((packet / "refresh-audit.json").read_text())
    index = json.loads((packet / "source-response-index.json").read_text())
    services = index["discovery_services"]
    assert {s["service"] for s in services} == {"Consensus", "Scite"}
    for service in services:
        assert service["provider_output_retained"] is False
        assert service["provider_output_used_as_evidence"] is False
    assert not list((packet / "raw").glob("consensus-*.json"))
    assert not (packet / "raw/scite-verify.json").exists()
    papers = json.loads((packet / "raw/pubmed-primary-metadata.json").read_text())["records"]
    assert {paper["pmid"] for paper in papers} == {"25236461", "25352552", "11125122"}
    for paper in papers:
        assert "doi" in paper["identifiers"]
        assert "abstract" not in paper


@pytest.mark.parametrize("script", ("build_mapping.py", "audit_refresh.py"))
def test_evidence_audits_reject_disabled_assertions(script):
    packet = BUNDLE.parents[3] / "data/science-evidence/2026-11-08-pca-strand-2566"
    result = subprocess.run(
        [sys.executable, "-O", str(packet / "scripts" / script)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0
    assert "requires enabled assertions" in result.stderr


@pytest.mark.parametrize("table", (raw_variants, annotated_variants), ids=("raw", "annotated"))
def test_reanalysis_replaces_legacy_findings_and_preserves_stored_grch37_calls(
    training_bundle, sample_engine, table
):
    bundle = training_bundle
    calls = [
        {
            "rsid": s.rsid,
            "chrom": s.chrom,
            "pos": s.grch37_pos,
            "genotype": _synthetic_call(s.rsid, i % 3),
        }
        for i, s in enumerate(bundle.snps)
    ]
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(table), calls)
        conn.execute(
            sa.insert(findings),
            [
                {
                    "module": "ancestry",
                    "category": category,
                    "finding_text": "legacy",
                    "detail_json": json.dumps({"legacy": True}),
                }
                for category in ("pca_projection", "nnls_admixture", "knn_admixture", "unrelated")
            ],
        )
    result = ancestry.infer_ancestry(bundle, sample_engine)
    expected = ((np.arange(5000) % 3 - bundle.means) / bundle.stds) @ bundle.loadings
    np.testing.assert_array_equal(result.pc_scores, np.round(expected, 6))
    assert result.snps_used == 5000 and result.coverage_fraction == 1.0
    assert result.missing_aim_rate == 0.0
    assert result.inference_provenance == {
        "training_bundle_version": "2.0.0",
        "training_build": "GRCh38",
        "sample_build": "GRCh37",
        "genotype_mapping_version": "grch37-pca-v1",
    }
    assert ancestry.store_ancestry_findings(result, sample_engine) == 3
    assert ancestry.store_ancestry_findings(result, sample_engine) == 3
    with sample_engine.connect() as conn:
        stored = conn.execute(sa.select(findings.c.category, findings.c.detail_json)).all()
        observed = conn.execute(sa.select(table.c.rsid, table.c.genotype)).all()
    assert len(stored) == 4
    assert {r.rsid: r.genotype for r in observed} == {r["rsid"]: r["genotype"] for r in calls}
    payloads = {r.category: json.loads(r.detail_json) for r in stored}
    assert payloads.pop("unrelated") == {"legacy": True}
    assert set(payloads) == {"pca_projection", "nnls_admixture", "knn_admixture"}
    for payload in payloads.values():
        assert "legacy" not in payload
        assert payload["inference_provenance"] == result.inference_provenance
    assert payloads["pca_projection"]["pc_scores"] == result.pc_scores
