"""The backend suite never reads the real data home (#2079).

``tests/backend/conftest.py``'s autouse ``_isolated_data_home`` resolves every
default data path into a throwaway directory, so a test that leaves a path
unpatched can no longer pick up whatever an earlier process left in
``~/.yeliztli``. These pin that behaviour and the ``requires_real_bundle``
exemption the nightly slow tier depends on.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from _isolated_data_home import (
    REAL_DATA_MARKERS,
    isolate_data_home,
    settings_env_names,
    uses_real_data_home,
)

from backend import config
from backend.config import get_settings

_REAL_HOME = Path("~").expanduser() / ".yeliztli"


def _is_within(path: Path, root: Path) -> bool:
    return path.resolve(strict=False).is_relative_to(root.resolve(strict=False))


def test_default_paths_resolve_outside_the_real_home(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    basetemp = tmp_path_factory.getbasetemp()
    paths = {
        "data_dir": get_settings().data_dir,
        "config.toml": config.config_toml_path(),
        "data_dir pointer": config.data_dir_pointer_path(),
    }
    for name, path in paths.items():
        assert not _is_within(path, _REAL_HOME), f"{name} resolves into the real home: {path}"
        assert _is_within(path, basetemp), f"{name} is not in pytest's temp dir: {path}"


def test_the_default_home_starts_empty() -> None:
    # None of what a prior app boot leaves behind (#2079's trigger) is visible:
    # the home holds only the pointer that redirects data_dir into it.
    home = get_settings().data_dir
    assert config.data_dir_pointer_path().read_text(encoding="utf-8") == str(home)
    assert sorted(p.name for p in home.iterdir()) == [config.DATA_DIR_POINTER_NAME]


@pytest.mark.parametrize("_round", [1, 2])
def test_each_test_gets_its_own_home(
    _round: int, tmp_path_factory: pytest.TempPathFactory
) -> None:
    # Whichever round runs second fails if the first round's file leaked.
    home = get_settings().data_dir
    # Refuse to write anywhere but pytest's temp dir (never the real home).
    assert _is_within(home, tmp_path_factory.getbasetemp()), home
    marker = home / "leak-marker"
    assert not marker.exists()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(_round), encoding="utf-8")


def test_ambient_settings_env_is_removed_but_harness_controls_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A developer's shell can carry YELIZTLI_* overrides; YELIZTLI_DATA_DIR
    # outranks the pointer, and others flip behaviour (auth -> 401s). The
    # match is case-insensitive, like Settings.
    monkeypatch.setenv("YELIZTLI_DATA_DIR", str(_REAL_HOME))
    monkeypatch.setenv("yeliztli_theme", "dark")
    monkeypatch.setenv("YELIZTLI_NCBI_API_KEY", "ambient-key")
    # Not a setting: the golden-snapshot regeneration switch must survive.
    monkeypatch.setenv("YELIZTLI_UPDATE_GOLDEN", "1")
    home = tmp_path / "home" / ".yeliztli"
    isolate_data_home(monkeypatch, home)
    names = settings_env_names()
    assert not [key for key in os.environ if key.upper() in names]
    assert os.environ["YELIZTLI_UPDATE_GOLDEN"] == "1"
    settings = get_settings()
    assert settings.data_dir == home
    assert settings.theme == "system"
    assert settings.pubmed_api_key == ""  # the NCBI alias feeds this field


def test_real_bundle_tests_keep_the_real_home() -> None:
    def node(*markers: str) -> SimpleNamespace:
        return SimpleNamespace(
            get_closest_marker=lambda name: object() if name in markers else None
        )

    assert uses_real_data_home(node("requires_real_bundle"))
    assert not uses_real_data_home(node())
    assert not uses_real_data_home(node("slow"))
    # The exemption must stay a marker the root conftest actually gates on.
    root_conftest = Path(__file__).resolve().parents[1] / "conftest.py"
    text = root_conftest.read_text(encoding="utf-8")
    assert all(f'"{marker}" in item.keywords' in text for marker in REAL_DATA_MARKERS)
