"""Config.toml writers share one lock and one path (PR: config lock + single path).

Every endpoint that persists to config.toml (setup credentials, preferences
theme, auth settings) must:
  * serialize its read-modify-write on a single shared lock, and
  * write to the one config.toml the Settings read source actually loads
    (``DEFAULT_DATA_DIR``), so the saved values round-trip back into Settings —
    even when the wizard has relocated ``data_dir`` to a different volume.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import backend.api.routes.auth as auth_mod
import backend.api.routes.preferences as prefs_mod
import backend.api.routes.setup as setup_mod
import backend.config as config


def test_all_config_writers_share_one_lock() -> None:
    """setup / preferences / auth must reference the single shared lock."""
    assert setup_mod.config_write_lock is config.config_write_lock
    assert prefs_mod.config_write_lock is config.config_write_lock
    assert auth_mod.config_write_lock is config.config_write_lock


def _relocate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Wire DEFAULT_DATA_DIR=home and a pointer relocating data_dir elsewhere."""
    home = tmp_path / "home"
    home.mkdir()
    relocated = tmp_path / "big_volume" / "store"
    relocated.mkdir(parents=True)
    monkeypatch.setattr(config, "DEFAULT_DATA_DIR", home)
    (home / config.DATA_DIR_POINTER_NAME).write_text(str(relocated), encoding="utf-8")
    return home, relocated


def test_theme_round_trips_for_relocated_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relocated install's saved theme is read back by Settings (not lost).

    Regression: the writers wrote to data_dir/config.toml while the read source
    loads DEFAULT_DATA_DIR/config.toml, so on a relocated install the saved value
    landed in a file Settings never reads and reverted to the default.
    """
    home, relocated = _relocate(tmp_path, monkeypatch)

    asyncio.run(prefs_mod.set_theme(prefs_mod.ThemeRequest(theme="dark")))

    # Written to the home config.toml (the read path), NOT the relocated dir.
    assert (home / "config.toml").exists()
    assert not (relocated / "config.toml").exists()
    # A fresh Settings round-trips both the theme (home config.toml) and the
    # relocated data_dir (home pointer).
    fresh = config.Settings()
    assert fresh.theme == "dark"
    assert fresh.data_dir == relocated


def test_all_writers_round_trip_for_relocated_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """credentials + theme + auth all coexist and are read back by Settings."""
    home, relocated = _relocate(tmp_path, monkeypatch)

    asyncio.run(
        setup_mod.save_credentials(
            setup_mod.SaveCredentialsRequest(
                pubmed_email="a@b.com", ncbi_api_key="key1", omim_api_key="key2"
            )
        )
    )
    asyncio.run(prefs_mod.set_theme(prefs_mod.ThemeRequest(theme="dark")))
    auth_mod._persist_auth_settings(auth_enabled=True, auth_password_hash="hash123")

    assert (home / "config.toml").exists()
    assert not (relocated / "config.toml").exists()

    fresh = config.Settings()
    assert fresh.theme == "dark"
    assert fresh.pubmed_email == "a@b.com"
    assert fresh.pubmed_api_key == "key1"
    assert fresh.omim_api_key == "key2"
    assert fresh.auth_enabled is True
    assert fresh.auth_password_hash == "hash123"
    assert fresh.data_dir == relocated


def test_writers_hold_the_lock_during_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each writer's config.toml write happens inside config_write_lock.

    Deterministic guard for the lock's purpose: if a writer dropped the
    ``with config_write_lock`` (so the read-modify-write were unsynchronized),
    the lock would not be held when write_config_toml runs and this fails.
    """
    monkeypatch.setattr(config, "DEFAULT_DATA_DIR", tmp_path)

    cases = [
        (
            prefs_mod,
            lambda: asyncio.run(prefs_mod.set_theme(prefs_mod.ThemeRequest(theme="dark"))),
        ),
        (
            setup_mod,
            lambda: asyncio.run(
                setup_mod.save_credentials(
                    setup_mod.SaveCredentialsRequest(pubmed_email="a@b.com")
                )
            ),
        ),
        (
            auth_mod,
            lambda: auth_mod._persist_auth_settings(auth_enabled=True, auth_password_hash="h"),
        ),
    ]
    for mod, call in cases:
        held: list[bool] = []
        original = mod.write_config_toml

        def spy(path, content, _orig=original, _held=held):
            _held.append(config.config_write_lock.locked())
            return _orig(path, content)

        monkeypatch.setattr(mod, "write_config_toml", spy)
        call()
        assert held == [True], f"{mod.__name__} must write config.toml under config_write_lock"
