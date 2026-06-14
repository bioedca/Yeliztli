"""Wiring tests for the cross-process build claim (PR-13b).

The primitive itself (``cross_process_build_claim`` / ``build_claim``) is unit
tested in ``test_build_guard.py``. This module locks the *route-level* fast-path
guards: when another process already holds the claim for a database, the
trigger/resume/update endpoints must refuse to queue a competing build/download
(HTTP 409 / skip) rather than racing it. The patches stub the cheap probe
``is_cross_process_build_claimed`` so the tests assert routing behaviour without
spawning a second process.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

_DATABASES_PROBE = "backend.api.routes.databases.is_cross_process_build_claimed"
_UPDATES_PROBE = "backend.api.routes.updates.is_cross_process_build_claimed"


class TestTriggerUpdateClaimGuard:
    def test_returns_409_when_claimed(self, test_client: TestClient) -> None:
        with patch(_UPDATES_PROBE, return_value=True):
            resp = test_client.post("/api/updates/trigger", json={"db_name": "clinvar"})
        assert resp.status_code == 409, resp.text
        assert "in progress" in resp.json()["detail"].lower()

    def test_proceeds_when_not_claimed(self, test_client: TestClient) -> None:
        # With the claim free the route must get past the guard. Stub the Huey
        # plumbing so no real build runs; we only assert the guard didn't 409.
        with (
            patch(_UPDATES_PROBE, return_value=False),
            patch(
                "backend.tasks.huey_tasks.create_database_update_job",
                return_value="job-xyz",
            ),
            patch("backend.tasks.huey_tasks.run_database_update_task"),
        ):
            resp = test_client.post("/api/updates/trigger", json={"db_name": "clinvar"})
        assert resp.status_code == 202, resp.text
        assert resp.json()["db_name"] == "clinvar"


class TestTriggerDownloadClaimGuard:
    def test_claimed_db_is_skipped(self, test_client: TestClient) -> None:
        # clinvar is not present in the fresh data dir, so the only reason it
        # drops out of the download set is the cross-process claim → nothing
        # left to download → 409.
        with patch(_DATABASES_PROBE, return_value=True):
            resp = test_client.post("/api/databases/download", json={"databases": ["clinvar"]})
        assert resp.status_code == 409, resp.text
