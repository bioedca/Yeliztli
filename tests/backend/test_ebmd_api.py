"""The eBMD page receives its DXA/FRAX safety framing from the API (#2083).

``EBMDView`` renders ``context.not_a_substitute``, ``context.utility`` and
``context.byo`` from ``GET /api/analysis/ebmd/prs``. The content of those
strings is pinned in ``test_ebmd_prs.py``; this guards the route actually
serving them, including when no BYO score is installed (the default state).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.analysis.ebmd_prs import EBMD_CONTEXT
from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import reference_metadata, samples

# The context keys EBMDView.tsx renders.
_RENDERED_CONTEXT_KEYS = ("not_a_substitute", "direction", "utility", "byo")


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    from backend.api.routes.ebmd import router

    data_dir = tmp_path / "data"
    (data_dir / "samples").mkdir(parents=True)

    ref_engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        conn.execute(
            sa.insert(samples),
            [
                {
                    "name": "test_sample",
                    "db_path": "samples/sample_1.db",
                    "file_format": "23andme_v5",
                    "file_hash": "abc123",
                }
            ],
        )
    ref_engine.dispose()

    sample_engine = sa.create_engine(f"sqlite:///{data_dir / 'samples' / 'sample_1.db'}")
    create_sample_tables(sample_engine)
    sample_engine.dispose()

    reset_registry()
    registry = DBRegistry(Settings(data_dir=data_dir))
    patchers = [
        patch("backend.api.routes.ebmd.get_registry", return_value=registry),
        patch("backend.api.dependencies.get_registry", return_value=registry),
        patch("backend.services.staleness.get_registry", return_value=registry),
    ]
    for p in patchers:
        p.start()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    try:
        yield TestClient(app)
    finally:
        for p in reversed(patchers):
            p.stop()
        registry.dispose_all()
        reset_registry()


def test_prs_response_serves_the_rendered_safety_framing(client: TestClient) -> None:
    resp = client.get("/api/analysis/ebmd/prs?sample_id=1")
    assert resp.status_code == 200
    body = resp.json()

    # No BYO score installed: the framing must still be served.
    assert body["available"] is False
    context = body["context"]
    for key in _RENDERED_CONTEXT_KEYS:
        assert context.get(key), f"context[{key!r}] is missing or empty"
        assert context[key] == EBMD_CONTEXT[key]
    assert "NOT a substitute for DXA" in context["not_a_substitute"]
