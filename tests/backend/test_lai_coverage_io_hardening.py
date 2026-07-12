from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "lai_bundle_v2" / "06g_calibrate_coverage.py"
MODULE_SPEC = importlib.util.spec_from_file_location("lai_coverage_io_harness", SCRIPT_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
cal = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = cal
MODULE_SPEC.loader.exec_module(cal)


def _close_lock(handle) -> None:
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()


@pytest.mark.parametrize(
    "reader", [cal._stable_file_snapshot, lambda path: cal.stable_read(path, Path.read_bytes)]
)
def test_descriptor_pinned_readers_reject_leaf_symlinks(tmp_path, reader):
    target = tmp_path / "target.txt"
    target.write_text("authenticated\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="non-symlink regular file"):
        reader(link)


def test_stable_read_rejects_pathname_replacement_during_parse(tmp_path):
    source = tmp_path / "source.json"
    replacement = tmp_path / "replacement.json"
    source.write_text('{"value":1}\n', encoding="utf-8")
    replacement.write_text('{"value":1}\n', encoding="utf-8")

    def replace_path(pinned_path: Path) -> object:
        parsed = json.loads(pinned_path.read_text(encoding="utf-8"))
        os.replace(replacement, source)
        return parsed

    with pytest.raises(ValueError, match="pathname changed"):
        cal.stable_read(source, replace_path)


def test_destination_rejects_path_hardlink_and_protected_tree_aliases(tmp_path):
    authenticated_input = tmp_path / "input.json"
    authenticated_input.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="aliases an authenticated input"):
        cal.validate_destination(
            authenticated_input,
            role="test output",
            inputs=(authenticated_input,),
        )

    hardlink = tmp_path / "hardlink.json"
    os.link(authenticated_input, hardlink)
    with pytest.raises(ValueError, match="hardlinks an authenticated input"):
        cal.validate_destination(
            hardlink,
            role="test output",
            inputs=(authenticated_input,),
        )

    protected = tmp_path / "bundle"
    protected.mkdir()
    with pytest.raises(ValueError, match="protected directory"):
        cal.validate_destination(
            protected / "result.json",
            role="test output",
            inputs=(),
            forbidden_directories=(protected,),
        )


def test_destination_rejects_existing_nonregular_file(tmp_path):
    destination = tmp_path / "result.json"
    destination.mkdir()

    with pytest.raises(ValueError, match="regular file"):
        cal.validate_destination(destination, role="test output", inputs=())


def test_result_lock_rejects_concurrent_writer(tmp_path):
    output = tmp_path / "result.jsonl"
    normalized, first = cal.acquire_result_output_lock(
        output,
        job_index=3,
        configuration_sha256="a" * 64,
        inputs=(),
        forbidden_directories=(),
        overwrite=False,
    )
    try:
        assert normalized == output
        with pytest.raises(ValueError, match="already locked"):
            cal.acquire_result_output_lock(
                output,
                job_index=3,
                configuration_sha256="a" * 64,
                inputs=(),
                forbidden_directories=(),
                overwrite=False,
            )
    finally:
        _close_lock(first)


def test_result_lock_allows_same_job_retry_but_rejects_other_identity(tmp_path):
    output = tmp_path / "result.jsonl"
    configuration_sha256 = "b" * 64
    cal.atomic_write_jsonl(
        output,
        (
            {
                "job_index": 7,
                "provenance": {"configuration_sha256": configuration_sha256},
            },
        ),
    )

    _normalized, retry = cal.acquire_result_output_lock(
        output,
        job_index=7,
        configuration_sha256=configuration_sha256,
        inputs=(),
        forbidden_directories=(),
        overwrite=False,
    )
    _close_lock(retry)

    with pytest.raises(ValueError, match="different or malformed job"):
        cal.acquire_result_output_lock(
            output,
            job_index=8,
            configuration_sha256=configuration_sha256,
            inputs=(),
            forbidden_directories=(),
            overwrite=False,
        )


def test_result_publication_rejects_parent_inode_swap(tmp_path):
    parent = tmp_path / "results"
    parent.mkdir()
    output = parent / "result.jsonl"
    normalized, authority = cal.acquire_result_output_lock(
        output,
        job_index=4,
        configuration_sha256="c" * 64,
        inputs=(),
        forbidden_directories=(),
        overwrite=False,
    )
    moved_parent = tmp_path / "moved-results"
    parent.rename(moved_parent)
    parent.mkdir()

    try:
        with pytest.raises(ValueError, match="parent changed"):
            cal.atomic_write_jsonl(
                normalized,
                ({"job_index": 4},),
                authority=authority,
            )
    finally:
        _close_lock(authority)

    assert not output.exists()
    assert not (moved_parent / output.name).exists()


def test_production_adapter_inserts_variants_in_bounded_batches(tmp_path, monkeypatch):
    markers = tuple(
        cal.Marker(
            rsid=f"rs{index}",
            chrom="1",
            pos=index + 1,
            genotype="AA",
        )
        for index in range(5)
    )
    fixture_path = tmp_path / "fixture.tsv"
    fixture_path.write_text("fixture\n", encoding="utf-8")
    truth_path = tmp_path / "truth.tsv"
    truth_path.write_text("truth\n", encoding="utf-8")
    fixture = cal.ValidationFixture(
        iid="SIM_BATCH",
        validation_stratum="test",
        path=fixture_path,
        sha256="c" * 64,
        markers=markers,
        parsing={},
        truth_path=truth_path,
        truth_sha256="d" * 64,
        truth_windows=(),
    )

    real_create_engine = cal.sa.create_engine
    batch_sizes: list[int] = []

    def instrumented_create_engine(*args, **kwargs):
        engine = real_create_engine(*args, **kwargs)

        def capture_batch(
            _connection,
            _cursor,
            statement,
            parameters,
            _context,
            executemany,
        ):
            if "INSERT INTO raw_variants" in statement:
                batch_sizes.append(len(parameters) if executemany else 1)

        cal.sa.event.listen(engine, "before_cursor_execute", capture_batch)
        return engine

    def fake_run_lai_analysis(**_kwargs):
        return SimpleNamespace(metadata={})

    monkeypatch.setattr(cal, "RAW_VARIANT_INSERT_BATCH_SIZE", 2)
    monkeypatch.setattr(cal.sa, "create_engine", instrumented_create_engine)
    monkeypatch.setattr(cal, "run_lai_analysis", fake_run_lai_analysis)

    cal.run_production_diagnostic(
        markers=markers,
        file_format="",
        fixture=fixture,
        bundle_dir=tmp_path / "bundle",
        work_dir=tmp_path / "work",
        sample_id=11,
        diagnostic_metrics_callback=lambda _snapshot: None,
    )

    assert batch_sizes == [2, 2, 1]
