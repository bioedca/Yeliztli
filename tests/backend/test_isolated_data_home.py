"""The backend suite never reads the real data home (#2079).

``tests/backend/conftest.py`` isolates the session as soon as it is imported and
gives every test its own empty home (``_isolated_data_home``), so a path a test
leaves unpatched can no longer pick up whatever an earlier process, the shell or
the project ``.env`` left behind. These pin that behaviour, the import-time
production path, and the ``requires_real_bundle`` exemption the nightly slow tier
depends on.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from _isolated_data_home import (
    REAL_DATA_MARKERS,
    isolate_data_home,
    restore_real_data_home,
    session_root,
    settings_env_names,
    uses_real_data_home,
)

from backend import config
from backend.config import get_settings

# Imported at collection, like every test module that imports backend.main: the
# module binds the global Huey to get_settings().data_dir/huey.db right here.
from backend.tasks import huey_tasks

_REAL_HOME = Path("~").expanduser() / ".yeliztli"


def _is_within(path: Path, root: Path) -> bool:
    return path.resolve(strict=False).is_relative_to(root.resolve(strict=False))


def test_import_time_huey_is_bound_outside_the_real_home() -> None:
    huey_db = Path(huey_tasks.huey.storage.filename)
    assert not _is_within(huey_db, _REAL_HOME), f"Huey bound to the real home: {huey_db}"
    assert _is_within(huey_db, session_root()), huey_db


def test_default_paths_resolve_outside_the_real_home() -> None:
    paths = {
        "data_dir": get_settings().data_dir,
        "config.toml": config.config_toml_path(),
        "data_dir pointer": config.data_dir_pointer_path(),
    }
    for name, path in paths.items():
        assert not _is_within(path, _REAL_HOME), f"{name} resolves into the real home: {path}"
        assert _is_within(path, session_root()), f"{name} is not a throwaway home: {path}"


def test_the_default_home_starts_empty() -> None:
    # None of what a prior app boot leaves behind (#2079's trigger) is visible:
    # the home holds only the pointer that redirects data_dir into it.
    home = get_settings().data_dir
    assert config.data_dir_pointer_path().read_text(encoding="utf-8") == str(home)
    assert sorted(p.name for p in home.iterdir()) == [config.DATA_DIR_POINTER_NAME]


@pytest.mark.parametrize("_round", [1, 2])
def test_each_test_gets_its_own_home(_round: int) -> None:
    # Whichever round runs second fails if the first round's file leaked.
    home = get_settings().data_dir
    # Refuse to write anywhere but a throwaway home (never the real one).
    assert _is_within(home, session_root()), home
    marker = home / "leak-marker"
    assert not marker.exists()
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


def test_the_project_dotenv_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A developer's gitignored .env feeds Settings through the dotenv source.
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("YELIZTLI_THEME=dark\n", encoding="utf-8")
    assert get_settings().theme == "system"


def test_real_home_can_be_restored() -> None:
    isolated = get_settings().data_dir
    # Its own MonkeyPatch, so undoing it leaves this test's isolation in place.
    restore = pytest.MonkeyPatch()
    try:
        restore_real_data_home(restore)
        assert not _is_within(config.config_toml_path(), session_root())
        assert config.Settings.model_config["env_file"] == ".env"
    finally:
        restore.undo()
    config.get_settings.cache_clear()
    assert get_settings().data_dir == isolated


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
