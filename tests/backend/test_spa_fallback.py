"""SPA deep links must survive a browser refresh.

The app is served by the API server itself (``frontend/dist``). A
``StaticFiles(html=True)`` mount only maps ``/`` to ``index.html``, so a
refresh of an in-app route like ``/settings/updates`` used to return
``{"detail": "Not Found"}`` before the SPA could route. ``create_app`` now
serves ``index.html`` for every non-API GET that doesn't match a built file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.main import create_app

_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

pytestmark = pytest.mark.skipif(
    not (_FRONTEND_DIST / "index.html").is_file(),
    reason="frontend build output (frontend/dist) not present",
)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    # No lifespan context: static serving must not depend on startup hooks.
    return TestClient(create_app())


def test_spa_deep_link_refresh_serves_index_html(client):
    resp = client.get("/settings/updates")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert b'id="root"' in resp.content


def test_nested_spa_route_refresh_serves_index_html(client):
    resp = client.get("/individuals/1/findings")
    assert resp.status_code == 200
    assert b'id="root"' in resp.content


def test_built_root_file_is_served_directly(client):
    favicon = _FRONTEND_DIST / "favicon.ico"
    assert favicon.is_file(), "expected favicon.ico in the built frontend"
    resp = client.get("/favicon.ico")
    assert resp.status_code == 200
    assert resp.content == favicon.read_bytes()


def test_unknown_api_route_still_returns_json_404(client):
    resp = client.get("/api/definitely-not-a-route")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}
