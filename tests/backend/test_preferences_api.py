"""Tests for the preferences API (P4-26a).

Covers:
- GET /api/preferences/theme — returns current theme
- PUT /api/preferences/theme — persists theme to config.toml
- Round-trip: set → get consistency
- Invalid theme values rejected
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.tables import reference_metadata


@pytest.fixture
def prefs_client(tmp_data_dir: Path) -> TestClient:
    """FastAPI TestClient with patched settings for preferences API tests."""
    base_settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    # Create reference.db so the registry can initialize
    ref_path = base_settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()

    def _make_settings() -> Settings:
        """Re-read settings from config.toml in tmp dir."""
        config_path = tmp_data_dir / "config.toml"
        kwargs: dict = {"data_dir": tmp_data_dir, "wal_mode": False}
        if config_path.exists():
            import tomllib

            data = tomllib.loads(config_path.read_text())
            section = data.get("yeliztli") or {}
            if "theme" in section:
                kwargs["theme"] = section["theme"]
        return Settings(**kwargs)

    with (
        patch("backend.main.get_settings", return_value=base_settings),
        patch("backend.db.connection.get_settings", return_value=base_settings),
        patch("backend.api.routes.preferences.get_settings", side_effect=_make_settings),
        # config.toml lives in DEFAULT_DATA_DIR (where set_theme writes via
        # config_toml_path and _make_settings reads it back); isolate it to the
        # temp dir so the test never touches the real home config.
        patch("backend.config.DEFAULT_DATA_DIR", tmp_data_dir),
        patch("backend.api.routes.databases.get_settings", return_value=base_settings),
    ):
        reset_registry()

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc

        reset_registry()


class TestGetTheme:
    """GET /api/preferences/theme."""

    def test_default_theme_is_system(self, prefs_client: TestClient) -> None:
        resp = prefs_client.get("/api/preferences/theme")
        assert resp.status_code == 200
        assert resp.json()["theme"] == "system"


class TestSetTheme:
    """PUT /api/preferences/theme."""

    @pytest.mark.parametrize("theme", ["light", "dark", "system"])
    def test_set_valid_theme(self, prefs_client: TestClient, theme: str) -> None:
        resp = prefs_client.put(
            "/api/preferences/theme",
            json={"theme": theme},
        )
        assert resp.status_code == 200
        assert resp.json()["theme"] == theme

    def test_set_theme_persists_to_config_toml(
        self, prefs_client: TestClient, tmp_data_dir: Path
    ) -> None:
        prefs_client.put("/api/preferences/theme", json={"theme": "dark"})
        config_path = tmp_data_dir / "config.toml"
        assert config_path.exists()
        content = config_path.read_text()
        assert 'theme = "dark"' in content

    def test_round_trip(self, prefs_client: TestClient) -> None:
        prefs_client.put("/api/preferences/theme", json={"theme": "dark"})
        resp = prefs_client.get("/api/preferences/theme")
        assert resp.json()["theme"] == "dark"

    def test_invalid_theme_rejected(self, prefs_client: TestClient) -> None:
        resp = prefs_client.put(
            "/api/preferences/theme",
            json={"theme": "rainbow"},
        )
        assert resp.status_code == 422

    def test_missing_body_rejected(self, prefs_client: TestClient) -> None:
        resp = prefs_client.put("/api/preferences/theme", json={})
        assert resp.status_code == 422

    def test_set_theme_preserves_existing_config(
        self, prefs_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """Setting theme should not clobber existing config entries."""
        config_path = tmp_data_dir / "config.toml"
        config_path.write_text('[yeliztli]\ndata_dir = "/custom/path"\n', encoding="utf-8")
        prefs_client.put("/api/preferences/theme", json={"theme": "light"})
        content = config_path.read_text()
        assert 'theme = "light"' in content
        assert 'data_dir = "/custom/path"' in content
