"""Tests for bounded-memory LAI coverage job plans."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "lai_bundle_v2" / "lai_coverage_plan.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("lai_coverage_plan", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


plan = _load_module()


def _axes_for_count(count: int):
    return plan.JobMatrix.create(
        dataset_split="calibration",
        fixture_masks=(("SIM1", tuple(f"mask-{index}" for index in range(count))),),
        drop_scenarios=("none",),
        fractions=("1",),
        seeds=(42,),
    )


def _reference_merkle(rows):
    leaves = [
        hashlib.sha256(
            plan.LEAF_HASH_DOMAIN
            + len(plan.canonical_json_bytes(row)).to_bytes(8, "big")
            + plan.canonical_json_bytes(row)
        ).digest()
        for row in rows
    ]
    levels = [leaves]
    while len(levels[-1]) > 1:
        current = levels[-1]
        levels.append(
            [
                hashlib.sha256(
                    plan.NODE_HASH_DOMAIN
                    + current[index]
                    + (current[index + 1] if index + 1 < len(current) else current[index])
                ).digest()
                for index in range(0, len(current), 2)
            ]
        )
    proofs = []
    for leaf_index in range(len(rows)):
        index = leaf_index
        proof = []
        for level in levels[:-1]:
            sibling_index = index ^ 1
            if sibling_index >= len(level):
                sibling_index = index
            proof.append(
                {
                    "side": "left" if sibling_index < index else "right",
                    "sha256": level[sibling_index].hex(),
                }
            )
            index //= 2
        proofs.append(proof)
    return levels[-1][0].hex(), proofs


def _build(tmp_path: Path, count: int = 3):
    matrix = _axes_for_count(count)
    path = tmp_path / "jobs.json"
    result = plan.build_job_plan(
        path,
        configuration={"dataset_id": "fixture-v1"},
        input_verification={"fixtures": {"SIM1": {"sha256": "a" * 64}}},
        dataset_split=matrix.dataset_split,
        fixture_masks=matrix.fixture_masks,
        drop_scenarios=matrix.drop_scenarios,
        fractions=matrix.fractions,
        seeds=matrix.seeds,
        disk_reserve_bytes=0,
    )
    return path, matrix, result


def _read_json(path: Path):
    return json.loads(path.read_bytes())


def _write_json(path: Path, payload: object) -> None:
    path.write_bytes(plan.canonical_json_bytes(payload))


def _shard_path(path: Path, result, index: int) -> Path:
    return path.parent / result.shards_directory / f"{index:08d}.json"


def test_plan_publication_is_idempotent_but_rejects_different_identity(tmp_path):
    path, matrix, first = _build(tmp_path)
    second = plan.build_job_plan(
        path,
        configuration={"dataset_id": "fixture-v1"},
        input_verification={"fixtures": {"SIM1": {"sha256": "a" * 64}}},
        dataset_split=matrix.dataset_split,
        fixture_masks=matrix.fixture_masks,
        drop_scenarios=matrix.drop_scenarios,
        fractions=matrix.fractions,
        seeds=matrix.seeds,
        disk_reserve_bytes=0,
    )
    assert second.configuration_sha256 == first.configuration_sha256

    with pytest.raises(ValueError, match="different authenticated identity"):
        plan.build_job_plan(
            path,
            configuration={"dataset_id": "different"},
            input_verification={"fixtures": {"SIM1": {"sha256": "a" * 64}}},
            dataset_split=matrix.dataset_split,
            fixture_masks=matrix.fixture_masks,
            drop_scenarios=matrix.drop_scenarios,
            fractions=matrix.fractions,
            seeds=matrix.seeds,
            disk_reserve_bytes=0,
        )


def test_plan_publication_rejects_concurrent_destination_lock(tmp_path):
    path = tmp_path / "jobs.json"
    with plan._locked_plan_destination(path):
        with pytest.raises(ValueError, match="already locked"):
            _build(tmp_path)


def test_plan_publication_rejects_parent_inode_swap(tmp_path, monkeypatch):
    parent = tmp_path / "plans"
    parent.mkdir()
    moved_parent = tmp_path / "moved-plans"
    real_publish = plan._publish
    publish_count = 0

    def swap_parent_after_first_publish(source, destination):
        nonlocal publish_count
        real_publish(source, destination)
        publish_count += 1
        if publish_count == 1:
            parent.rename(moved_parent)
            parent.mkdir()

    monkeypatch.setattr(plan, "_publish", swap_parent_after_first_publish)
    matrix = _axes_for_count(2)

    with pytest.raises(ValueError, match="job-plan parent changed"):
        plan.build_job_plan(
            parent / "jobs.json",
            configuration={},
            input_verification={},
            dataset_split=matrix.dataset_split,
            fixture_masks=matrix.fixture_masks,
            drop_scenarios=matrix.drop_scenarios,
            fractions=matrix.fractions,
            seeds=matrix.seeds,
            disk_reserve_bytes=0,
        )

    assert not (parent / "jobs.json").exists()
    assert not (moved_parent / "jobs.json").exists()
    assert not list(moved_parent.glob("jobs.*.shards"))


def test_plan_reader_enforces_json_artifact_size_limit(tmp_path, monkeypatch):
    path, _matrix, result = _build(tmp_path)
    monkeypatch.setattr(plan, "MAX_JSON_ARTIFACT_BYTES", 1)

    with pytest.raises(ValueError, match="safety limit"):
        plan.read_job_plan(path, result.configuration_sha256, 0)


@pytest.mark.parametrize("count", [1, 2, 3, 5, 32, 257])
def test_rows_roots_and_proofs_match_simple_reference(tmp_path, count):
    matrix = _axes_for_count(count)
    rows = list(matrix.iter_rows())
    expected_root, expected_proofs = _reference_merkle(rows)

    assert matrix.row_count == count
    assert (
        plan.row_count(
            matrix.fixture_masks,
            matrix.drop_scenarios,
            matrix.fractions,
            matrix.seeds,
        )
        == count
    )
    assert [matrix.row_at(index) for index in range(count)] == rows
    assert (
        list(
            plan.iter_rows(
                dataset_split=matrix.dataset_split,
                fixture_masks=matrix.fixture_masks,
                drop_scenarios=matrix.drop_scenarios,
                fractions=matrix.fractions,
                seeds=matrix.seeds,
            )
        )
        == rows
    )

    path, _built_matrix, result = _build(tmp_path, count)
    assert result.merkle_root_sha256 == expected_root
    assert result.row_count == count
    for index, expected_row in enumerate(rows):
        shard = _read_json(_shard_path(path, result, index))
        assert shard["job"] == expected_row
        assert shard["merkle_proof"] == expected_proofs[index]
        configuration, observed_row, verification = plan.read_job_plan(
            path,
            result.configuration_sha256,
            index,
        )
        assert configuration == result.configuration
        assert observed_row == expected_row
        assert verification == {"fixtures": {"SIM1": {"sha256": "a" * 64}}}


def test_domain_separation_changes_leaf_and_node_hashes():
    row = _axes_for_count(1).row_at(0)
    encoded = plan.canonical_json_bytes(row)
    leaf = plan.hash_job_leaf(row)

    assert leaf != hashlib.sha256(encoded).digest()
    assert plan.hash_merkle_node(leaf, leaf) != hashlib.sha256(leaf + leaf).digest()
    assert plan.LEAF_HASH_DOMAIN != plan.NODE_HASH_DOMAIN


def test_fixture_specific_masks_and_cartesian_arithmetic():
    matrix = plan.JobMatrix.create(
        dataset_split="final_confirmation",
        fixture_masks=(("SIM2", ("native",)), ("SIM1", ("native", "vendor"))),
        drop_scenarios=("none", "chr22"),
        fractions=(Decimal("0.5"), Decimal("1")),
        seeds=(-1, 7),
    )
    rows = list(matrix.iter_rows())

    assert matrix.row_count == 24
    assert rows[0] == {
        "job_index": 0,
        "iid": "SIM2",
        "dataset_split": "final_confirmation",
        "mask": "native",
        "chromosome_drop_scenario": "none",
        "fraction": "0.5",
        "seed": -1,
    }
    assert rows[8]["iid"] == "SIM1"
    assert rows[16]["mask"] == "vendor"
    assert rows[-1] == matrix.row_at(23)
    assert (
        plan.row_at(
            13,
            dataset_split=matrix.dataset_split,
            fixture_masks=matrix.fixture_masks,
            drop_scenarios=matrix.drop_scenarios,
            fractions=matrix.fractions,
            seeds=matrix.seeds,
        )
        == rows[13]
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda proof: proof.pop(), "invalid Merkle proof"),
        (
            lambda proof: proof.append({"side": "right", "sha256": "0" * 64}),
            "invalid Merkle proof",
        ),
        (
            lambda proof: proof[0].update(side="left" if proof[0]["side"] == "right" else "right"),
            "invalid Merkle proof",
        ),
        (
            lambda proof: proof[0].update(
                sha256=("0" if proof[0]["sha256"][0] != "0" else "1") + proof[0]["sha256"][1:]
            ),
            "invalid Merkle proof",
        ),
        (
            lambda proof: proof[0].update(extra="unexpected"),
            "invalid Merkle proof",
        ),
    ],
    ids=("truncated", "extra-step", "flipped-side", "flipped-hash", "extra-field"),
)
def test_reader_rejects_malformed_exact_proof_shape(tmp_path, mutation, message):
    path, _matrix, result = _build(tmp_path, 5)
    shard_path = _shard_path(path, result, 1)
    shard = _read_json(shard_path)
    mutation(shard["merkle_proof"])
    _write_json(shard_path, shard)

    with pytest.raises(ValueError, match=message):
        plan.read_job_plan(path, result.configuration_sha256, 1)


def test_reader_rejects_wrong_shard_leaf_count(tmp_path):
    path, _matrix, result = _build(tmp_path, 3)
    shard_path = _shard_path(path, result, 0)
    shard = _read_json(shard_path)
    shard["leaf_count"] = 4
    _write_json(shard_path, shard)

    with pytest.raises(ValueError, match="leaf index/count"):
        plan.read_job_plan(path, result.configuration_sha256, 0)


def test_reader_rejects_count_that_disagrees_with_axes(tmp_path):
    path, _matrix, result = _build(tmp_path, 3)
    payload = _read_json(path)
    payload["configuration"]["job_matrix"]["count"] = 4
    expected_sha256 = plan.sha256_json(payload["configuration"])
    _write_json(path, payload)

    with pytest.raises(ValueError, match="count does not match"):
        plan.read_job_plan(path, expected_sha256, 0)


def test_reader_enforces_odd_leaf_self_duplication(tmp_path):
    path, _matrix, result = _build(tmp_path, 3)
    shard_path = _shard_path(path, result, 2)
    shard = _read_json(shard_path)
    assert shard["merkle_proof"][0]["side"] == "right"
    shard["merkle_proof"][0]["sha256"] = "0" * 64
    _write_json(shard_path, shard)

    with pytest.raises(ValueError, match="invalid Merkle proof"):
        plan.read_job_plan(path, result.configuration_sha256, 2)


def test_reader_reconstructs_row_instead_of_trusting_authenticated_payload(tmp_path):
    path, _matrix, result = _build(tmp_path, 2)
    shard_path = _shard_path(path, result, 0)
    shard = _read_json(shard_path)
    shard["job"]["mask"] = "other"
    _write_json(shard_path, shard)

    with pytest.raises(ValueError, match="does not match the Cartesian axes"):
        plan.read_job_plan(path, result.configuration_sha256, 0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"dataset_split": " calibration"},
        {"fixture_masks": ()},
        {"fixture_masks": (("SIM1", ("native",)), ("SIM1", ("vendor",)))},
        {"fixture_masks": (("SIM1", ("native", "native")),)},
        {"drop_scenarios": ("none", "none")},
        {"fractions": ("1.0",)},
        {"fractions": ("0",)},
        {"fractions": ("1.0001",)},
        {"fractions": (Decimal("sNaN"),)},
        {"fractions": (Decimal("0.5"), Decimal("0.50"))},
        {"seeds": (True,)},
        {"seeds": (-(1 << 63) - 1,)},
        {"seeds": (1 << 63,)},
        {"seeds": (7, 7)},
        {"max_jobs": plan.HARD_MAX_JOBS + 1},
    ],
)
def test_axis_contract_rejects_invalid_values(overrides):
    values = {
        "dataset_split": "calibration",
        "fixture_masks": (("SIM1", ("native",)),),
        "drop_scenarios": ("none",),
        "fractions": ("1",),
        "seeds": (42,),
        "max_jobs": plan.DEFAULT_MAX_JOBS,
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        plan.JobMatrix.create(**values)


def test_signed_64_bit_seed_boundaries_are_valid():
    matrix = plan.JobMatrix.create(
        dataset_split="calibration",
        fixture_masks=(("SIM1", ("native",)),),
        drop_scenarios=("none",),
        fractions=("1",),
        seeds=(-(1 << 63), (1 << 63) - 1),
    )

    assert [row["seed"] for row in matrix.iter_rows()] == [-(1 << 63), (1 << 63) - 1]


def test_default_and_hard_matrix_caps():
    fixture_masks = (("SIM1", tuple(f"mask-{index}" for index in range(101))),)
    drops = tuple(f"drop-{index}" for index in range(10))
    fractions = tuple(str(Decimal(index) / 10) for index in range(1, 11))
    seeds = tuple(range(10))

    with pytest.raises(ValueError, match="exceeding max_jobs=100000"):
        plan.row_count(fixture_masks, drops, fractions, seeds)
    assert (
        plan.row_count(
            fixture_masks,
            drops,
            fractions,
            seeds,
            max_jobs=101_000,
        )
        == 101_000
    )
    with pytest.raises(ValueError, match="exceeding max_jobs=1000000"):
        plan.row_count(
            fixture_masks,
            drops,
            fractions,
            tuple(range(100)),
            max_jobs=plan.HARD_MAX_JOBS,
        )


def test_build_checks_peak_disk_estimate_before_writing(tmp_path, monkeypatch):
    matrix = _axes_for_count(3)
    estimate = plan.estimate_build_disk_bytes(matrix, plan_payload_bytes=4096)
    assert estimate > 4096
    monkeypatch.setattr(plan.shutil, "disk_usage", lambda _path: SimpleNamespace(free=0))

    with pytest.raises(OSError, match="insufficient disk space"):
        plan.build_job_plan(
            tmp_path / "jobs.json",
            configuration={},
            input_verification={},
            dataset_split=matrix.dataset_split,
            fixture_masks=matrix.fixture_masks,
            drop_scenarios=matrix.drop_scenarios,
            fractions=matrix.fractions,
            seeds=matrix.seeds,
            disk_reserve_bytes=0,
        )
    assert [entry.name for entry in tmp_path.iterdir()] == [".jobs.json.lock"]


def test_interrupted_atomic_publish_removes_new_shards_and_temporary_files(
    tmp_path,
    monkeypatch,
):
    real_publish = plan._publish
    publish_count = 0

    def fail_when_publishing_plan(source, destination):
        nonlocal publish_count
        publish_count += 1
        if publish_count == 2:
            raise OSError("simulated interruption")
        real_publish(source, destination)

    monkeypatch.setattr(plan, "_publish", fail_when_publishing_plan)
    matrix = _axes_for_count(5)
    plan_path = tmp_path / "jobs.json"

    with pytest.raises(OSError, match="simulated interruption"):
        plan.build_job_plan(
            plan_path,
            configuration={},
            input_verification={},
            dataset_split=matrix.dataset_split,
            fixture_masks=matrix.fixture_masks,
            drop_scenarios=matrix.drop_scenarios,
            fractions=matrix.fractions,
            seeds=matrix.seeds,
            disk_reserve_bytes=0,
        )

    assert publish_count == 2
    assert not plan_path.exists()
    assert not list(tmp_path.glob("*.jobs"))
    assert [entry.name for entry in tmp_path.iterdir() if entry.name.startswith(".")] == [
        ".jobs.json.lock"
    ]


def test_reader_rejects_symlink_plan_and_shard(tmp_path):
    path, _matrix, result = _build(tmp_path, 2)
    real_plan = tmp_path / "real-plan.json"
    path.replace(real_plan)
    path.symlink_to(real_plan.name)

    with pytest.raises(ValueError, match="non-symlink regular file"):
        plan.read_job_plan(path, result.configuration_sha256, 0)

    path.unlink()
    real_plan.replace(path)
    shard_path = _shard_path(path, result, 0)
    real_shard = shard_path.with_name("real-shard.json")
    shard_path.replace(real_shard)
    shard_path.symlink_to(real_shard.name)
    with pytest.raises(ValueError, match="non-symlink regular file"):
        plan.read_job_plan(path, result.configuration_sha256, 0)


def test_reader_rejects_nonregular_plan_without_opening_it(tmp_path):
    fifo = tmp_path / "jobs.json"
    os.mkfifo(fifo)
    assert stat.S_ISFIFO(fifo.lstat().st_mode)

    with pytest.raises(ValueError, match="non-symlink regular file"):
        plan.read_job_plan(fifo, "0" * 64, 0)


def test_builder_rejects_existing_symlink_plan(tmp_path):
    target = tmp_path / "target.json"
    target.write_text("do not replace", encoding="utf-8")
    path = tmp_path / "jobs.json"
    path.symlink_to(target.name)
    matrix = _axes_for_count(1)

    with pytest.raises(ValueError, match="non-symlink regular file"):
        plan.build_job_plan(
            path,
            configuration={},
            input_verification={},
            dataset_split=matrix.dataset_split,
            fixture_masks=matrix.fixture_masks,
            drop_scenarios=matrix.drop_scenarios,
            fractions=matrix.fractions,
            seeds=matrix.seeds,
            disk_reserve_bytes=0,
        )
    assert target.read_text(encoding="utf-8") == "do not replace"
