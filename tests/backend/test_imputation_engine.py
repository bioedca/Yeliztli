"""Shared engine-binary resolution (SW-C7).

Validates the resolution policy shared by the GLIMPSE2 / IMPUTE5 seams: an
executable in ``bin_dir`` resolves, a non-executable placeholder does not, and the
availability helpers report missing binaries without raising.
"""

from __future__ import annotations

from pathlib import Path

from backend.analysis.imputation_engine import (
    engine_available,
    missing_binaries,
    resolve_binary,
)


def _exe(path: Path) -> Path:
    path.touch()
    path.chmod(0o755)
    return path


class TestResolveBinary:
    def test_resolves_executable_in_bin_dir(self, tmp_path: Path) -> None:
        _exe(tmp_path / "tool")
        assert resolve_binary("tool", tmp_path) == tmp_path / "tool"

    def test_non_executable_ignored(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / "tool").touch()  # exists but not executable
        monkeypatch.setenv("PATH", "")  # isolate: tmp_path is the only candidate
        assert resolve_binary("tool", tmp_path) is None

    def test_absent_returns_none(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("PATH", "")
        assert resolve_binary("definitely-not-a-real-binary-xyz", tmp_path) is None

    def test_falls_back_to_path(self, tmp_path: Path, monkeypatch) -> None:
        # bin_dir has nothing; a PATH dir holds the executable.
        path_dir = tmp_path / "pathdir"
        path_dir.mkdir()
        _exe(path_dir / "tool")
        monkeypatch.setenv("PATH", str(path_dir))
        empty = tmp_path / "empty"
        empty.mkdir()
        assert resolve_binary("tool", empty) == path_dir / "tool"


class TestAvailability:
    def test_missing_lists_unresolved(self, tmp_path: Path) -> None:
        _exe(tmp_path / "a")
        assert missing_binaries(("a", "b"), tmp_path) == ["b"]

    def test_engine_available(self, tmp_path: Path) -> None:
        _exe(tmp_path / "a")
        _exe(tmp_path / "b")
        assert engine_available(("a", "b"), tmp_path) is True
        assert engine_available(("a", "c"), tmp_path) is False
