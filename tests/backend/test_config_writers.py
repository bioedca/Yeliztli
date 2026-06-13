"""Config.toml writers share one lock and one path (PR: config lock + single path).

Every endpoint that persists to config.toml (setup credentials, preferences
theme, auth settings) must serialize its read-modify-write on a single shared
lock and target the *effective* data_dir, so concurrent saves can't clobber each
other's keys and a relocated install writes to the config.toml it actually reads.
"""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import backend.api.routes.auth as auth_mod
import backend.api.routes.preferences as prefs_mod
import backend.api.routes.setup as setup_mod
import backend.config as config
from backend.config import Settings


def test_all_config_writers_share_one_lock() -> None:
    """setup / preferences / auth must reference the single shared lock."""
    assert setup_mod.config_write_lock is config.config_write_lock
    assert prefs_mod.config_write_lock is config.config_write_lock
    assert auth_mod.config_write_lock is config.config_write_lock


def test_set_theme_writes_to_data_dir_not_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Theme is saved under the effective data_dir, never the default home dir."""
    data_dir = tmp_path / "relocated"
    data_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(config, "DEFAULT_DATA_DIR", home)
    monkeypatch.setattr(
        prefs_mod,
        "get_settings",
        MagicMock(return_value=Settings(data_dir=data_dir, wal_mode=False)),
    )

    asyncio.run(prefs_mod.set_theme(prefs_mod.ThemeRequest(theme="dark")))

    assert 'theme = "dark"' in (data_dir / "config.toml").read_text(encoding="utf-8")
    assert not (home / "config.toml").exists()


def test_writers_coexist_in_one_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """All three writers merge into one config.toml without clobbering keys."""
    data_dir = tmp_path / "store"
    data_dir.mkdir()
    settings = Settings(data_dir=data_dir, wal_mode=False)
    for mod in (setup_mod, prefs_mod, auth_mod):
        monkeypatch.setattr(mod, "get_settings", MagicMock(return_value=settings))

    asyncio.run(
        setup_mod.save_credentials(
            setup_mod.SaveCredentialsRequest(
                pubmed_email="a@b.com", ncbi_api_key="key1", omim_api_key="key2"
            )
        )
    )
    asyncio.run(prefs_mod.set_theme(prefs_mod.ThemeRequest(theme="dark")))
    auth_mod._persist_auth_settings(auth_enabled=True, auth_password_hash="hash123")

    saved = tomllib.loads((data_dir / "config.toml").read_text(encoding="utf-8"))["yeliztli"]
    assert saved["pubmed_email"] == "a@b.com"
    assert saved["pubmed_api_key"] == "key1"
    assert saved["omim_api_key"] == "key2"
    assert saved["theme"] == "dark"
    assert saved["auth_enabled"] is True
    assert saved["auth_password_hash"] == "hash123"
