"""Tests for backend.config module."""

from pathlib import Path

import pytest

from backend.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Clear the get_settings lru_cache between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_default_settings():
    """Settings should load with sensible defaults."""
    settings = get_settings()
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.debug is False
    assert settings.wal_mode is True
    assert settings.auth_enabled is False
    assert settings.theme == "system"
    assert settings.log_level == "INFO"
    assert settings.update_check_interval == "daily"


def test_data_dir_default():
    """Default data_dir should be ~/.yeliztli."""
    settings = get_settings()
    assert settings.data_dir == Path.home() / ".yeliztli"


def test_derived_paths():
    """Derived paths should be relative to data_dir."""
    settings = Settings(data_dir=Path("/tmp/ylzt-test"))
    assert settings.samples_dir == Path("/tmp/ylzt-test/samples")
    assert settings.downloads_dir == Path("/tmp/ylzt-test/downloads")
    assert settings.resolved_log_dir == Path("/tmp/ylzt-test/logs")
    assert settings.reference_db_path == Path("/tmp/ylzt-test/reference.db")
    assert settings.vep_bundle_db_path == Path("/tmp/ylzt-test/vep_bundle.db")
    assert settings.gnomad_db_path == Path("/tmp/ylzt-test/gnomad_af.db")
    assert settings.dbnsfp_db_path == Path("/tmp/ylzt-test/dbnsfp.db")


def test_env_override(monkeypatch):
    """Canonical YELIZTLI_ environment variables should override defaults."""
    monkeypatch.setenv("YELIZTLI_PORT", "9000")
    monkeypatch.setenv("YELIZTLI_DEBUG", "true")
    settings = Settings()
    assert settings.port == 9000
    assert settings.debug is True


def test_get_settings_caching():
    """get_settings should return the same instance on repeated calls."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def _write_toml(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_config_toml_section_applied(tmp_path, monkeypatch):
    """Q13 fix: values under the [yeliztli] table reach Settings."""
    monkeypatch.setattr("backend.config.DEFAULT_DATA_DIR", tmp_path)
    _write_toml(
        tmp_path / "config.toml",
        '[yeliztli]\nauth_enabled = true\nauth_password_hash = "abc123"\ntheme = "dark"\n',
    )
    settings = Settings()
    assert settings.auth_enabled is True
    assert settings.auth_password_hash == "abc123"
    assert settings.theme == "dark"


def test_config_toml_data_dir_excluded(tmp_path, monkeypatch):
    """data_dir is never sourced from config.toml (location-defining; avoids stale path)."""
    monkeypatch.setattr("backend.config.DEFAULT_DATA_DIR", tmp_path)
    _write_toml(
        tmp_path / "config.toml",
        f'[yeliztli]\ndata_dir = "{tmp_path / "stale"}"\ntheme = "dark"\n',
    )
    settings = Settings()
    assert settings.theme == "dark"  # other keys still applied
    assert settings.data_dir != tmp_path / "stale"
