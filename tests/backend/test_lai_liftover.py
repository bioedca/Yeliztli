"""Focused tests for memory-bounded LAI liftover validation internals."""

from __future__ import annotations

from pathlib import Path

from backend.analysis.lai_liftover import _has_effective_autosomal_mapping


def test_reverse_scan_reassembles_lines_split_across_chunks(tmp_path: Path) -> None:
    mapping = tmp_path / "mapping.tsv"
    mapping.write_text(
        "rs_nonauto\tchrX\t1\nrs_auto\tchr1\t2",
        encoding="utf-8",
    )

    assert _has_effective_autosomal_mapping(mapping, chunk_size=5) is True


def test_reverse_scan_duplicate_rsid_preserves_last_write_wins(tmp_path: Path) -> None:
    mapping = tmp_path / "mapping.tsv"
    mapping.write_text(
        "rs_duplicate\tchr1\t1\nrs_filler\tchrX\t2\nrs_duplicate\tchrX\t3\n",
        encoding="utf-8",
    )

    assert _has_effective_autosomal_mapping(mapping, chunk_size=7) is False


def test_reverse_scan_handles_table_larger_than_default_chunk(tmp_path: Path) -> None:
    mapping = tmp_path / "mapping.tsv"
    mapping.write_text(
        "".join(f"rs{index}\tchr1\t{index + 1}\n" for index in range(5_000)),
        encoding="utf-8",
    )

    assert mapping.stat().st_size > 64 * 1024
    assert _has_effective_autosomal_mapping(mapping) is True
