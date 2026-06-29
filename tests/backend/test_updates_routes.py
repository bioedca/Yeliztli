"""Tests for /api/updates routes — Step 13.

Covers:
- POST /api/updates/auto-update — persists the toggle, returns the new value.
- POST /api/updates/auto-update — 404 for an unknown db_name.
- GET  /api/updates/status     — reflects auto_update_settings after POST.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.db.connection import get_registry
from backend.db.tables import auto_update_settings
from backend.db.update_manager import AUTO_UPDATE_DEFAULTS, get_auto_update


def _ref_engine(test_client: TestClient) -> sa.Engine:
    from backend.db.connection import get_registry

    return get_registry().reference_engine


# ── POST /api/updates/auto-update ────────────────────────────────────


def test_post_auto_update_persists_value(test_client: TestClient) -> None:
    # clinvar defaults to True; flip to False.
    resp = test_client.post(
        "/api/updates/auto-update",
        json={"db_name": "clinvar", "enabled": False},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"db_name": "clinvar", "enabled": False}

    engine = _ref_engine(test_client)
    assert get_auto_update(engine, "clinvar") is False

    with engine.connect() as conn:
        row = conn.execute(
            sa.select(auto_update_settings.c.enabled).where(
                auto_update_settings.c.db_name == "clinvar"
            )
        ).fetchone()
    assert row is not None
    assert bool(row.enabled) is False


def test_post_auto_update_round_trip(test_client: TestClient) -> None:
    """Toggle off, then back on — the row updates in place."""
    test_client.post(
        "/api/updates/auto-update",
        json={"db_name": "vep_bundle", "enabled": True},
    )
    test_client.post(
        "/api/updates/auto-update",
        json={"db_name": "vep_bundle", "enabled": False},
    )

    engine = _ref_engine(test_client)
    assert get_auto_update(engine, "vep_bundle") is False

    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(auto_update_settings).where(auto_update_settings.c.db_name == "vep_bundle")
        ).fetchall()
    assert len(rows) == 1  # upsert, not duplicate inserts


def test_post_auto_update_unknown_db_returns_404(test_client: TestClient) -> None:
    resp = test_client.post(
        "/api/updates/auto-update",
        json={"db_name": "no_such_db", "enabled": True},
    )
    assert resp.status_code == 404
    assert "no_such_db" in resp.json()["detail"]


def test_post_auto_update_validates_body(test_client: TestClient) -> None:
    """Missing fields → 422 (FastAPI validation), not 404."""
    resp = test_client.post("/api/updates/auto-update", json={"db_name": "clinvar"})
    assert resp.status_code == 422


# ── GET /api/updates/status ──────────────────────────────────────────


def test_get_status_reflects_persisted_auto_update(test_client: TestClient) -> None:
    # Default for clinvar is True; verify baseline.
    resp = test_client.get("/api/updates/status")
    assert resp.status_code == 200, resp.text
    statuses = {row["db_name"]: row for row in resp.json()}
    assert statuses["clinvar"]["auto_update"] is True

    # Flip via the new endpoint.
    test_client.post(
        "/api/updates/auto-update",
        json={"db_name": "clinvar", "enabled": False},
    )

    # GET should now reflect the persisted value, not AUTO_UPDATE_DEFAULTS.
    resp = test_client.get("/api/updates/status")
    statuses = {row["db_name"]: row for row in resp.json()}
    assert statuses["clinvar"]["auto_update"] is False

    # Other DBs are still at their defaults.
    for db_name, default in AUTO_UPDATE_DEFAULTS.items():
        if db_name == "clinvar":
            continue
        assert statuses[db_name]["auto_update"] is default


# ── update_check_interval == "off": no outbound update checks (#1241) ────────


def test_check_off_returns_empty_without_outbound(test_client: TestClient) -> None:
    """With checks off, GET /check returns an empty result and never calls
    check_all_updates — so the dashboard's auto-poll makes no outbound manifest/
    version fetch."""
    get_registry().settings.update_check_interval = "off"
    with patch("backend.api.routes.updates.check_all_updates") as spy:
        resp = test_client.get("/api/updates/check")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] == []
    assert data["up_to_date"] == []
    assert data["errors"] == []
    spy.assert_not_called()


def test_check_enabled_invokes_update_check(test_client: TestClient) -> None:
    """Control: with checks enabled the route DOES call check_all_updates, proving
    the off-path's assert_not_called isn't a mis-targeted (vacuous) spy."""
    get_registry().settings.update_check_interval = "daily"
    result = MagicMock(available=[], up_to_date=[], errors=[], checked_at=datetime.now(UTC))
    with patch("backend.api.routes.updates.check_all_updates", return_value=result) as spy:
        resp = test_client.get("/api/updates/check")
    assert resp.status_code == 200
    spy.assert_called_once()


def test_app_update_off_reports_no_update_without_outbound(test_client: TestClient) -> None:
    """With checks off, GET /app-update reports no update and never calls the
    GitHub Releases checker — so it makes no outbound request to github.com."""
    get_registry().settings.update_check_interval = "off"
    with patch("backend.utils.update_checker.check_app_update") as spy:
        resp = test_client.get("/api/updates/app-update")
    assert resp.status_code == 200
    assert resp.json()["update_available"] is False
    spy.assert_not_called()
