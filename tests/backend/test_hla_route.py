"""Route coverage for the HLA/HIBAG status endpoint (Wave D / SW-D1).

Exercises ``GET /api/hla/status`` at the route boundary. With no model directory
configured (the default test settings), the engine is deterministically
unavailable regardless of whether Rscript happens to be on the CI PATH.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_hla_status_unavailable_without_models(test_client: TestClient) -> None:
    resp = test_client.get("/api/hla/status")
    assert resp.status_code == 200
    body = resp.json()
    # No model dir configured → no ancestry models → engine unavailable (deterministic).
    assert body["model_dir_configured"] is False
    assert body["ancestry_models"] == []
    assert body["available"] is False
    assert "rscript_available" in body  # env-dependent; presence-only assertion
