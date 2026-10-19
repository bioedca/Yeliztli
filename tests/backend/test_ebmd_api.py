"""The eBMD page receives its DXA/FRAX safety framing from the API (#2083).

``EBMDView`` renders ``context.not_a_substitute``, ``context.utility`` and
``context.byo`` from ``GET /api/analysis/ebmd/prs``. The content of those
strings is pinned in ``test_ebmd_prs.py``; this guards the route actually
serving them, including when no BYO score is installed (the default state).
"""

from __future__ import annotations

import json
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
from backend.db.tables import findings, reference_metadata, samples

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


def _store_ebmd_prs_finding(tmp_path: Path, detail: dict[str, object]) -> None:
    sample_engine = sa.create_engine(f"sqlite:///{tmp_path / 'data' / 'samples' / 'sample_1.db'}")
    try:
        with sample_engine.begin() as conn:
            conn.execute(
                findings.insert().values(
                    module="ebmd",
                    category="prs",
                    evidence_level=1,
                    finding_text="Heel eBMD polygenic score",
                    prs_percentile=None,
                    detail_json=json.dumps(detail),
                )
            )
    finally:
        sample_engine.dispose()


def test_a_never_scored_sample_is_neither_available_nor_withheld(client: TestClient) -> None:
    """#2307: with no stored score, the page must be able to say "not scored yet".

    It can only do that if the response does not dress the empty state up as a
    withheld one; ``scoring_state`` carries "never".
    """
    body = client.get("/api/analysis/ebmd/prs?sample_id=1").json()

    assert body["available"] is False
    assert body["prs"] is None
    assert body["withheld"] is False
    assert body["scoring_state"] == "never"


def test_a_refused_stored_score_is_withheld_not_not_installed(
    client: TestClient, tmp_path: Path
) -> None:
    """#2307: a stored score the presentability guard refuses is withheld.

    ``available`` alone read "not installed" for it, which is the #2297 shape:
    a withheld result presented as an absence the user could act on.
    """
    _store_ebmd_prs_finding(
        tmp_path,
        {"name": "Heel eBMD", "snps_used": 10, "source_study": "CYP2D6 tamoxifen dose guidance"},
    )

    resp = client.get("/api/analysis/ebmd/prs?sample_id=1")
    body = resp.json()

    assert body["withheld"] is True
    assert body["available"] is False
    assert body["prs"] is None
    assert "tamoxifen" not in resp.text.lower()


def test_a_presentable_stored_score_is_available_and_not_withheld(
    client: TestClient, tmp_path: Path
) -> None:
    _store_ebmd_prs_finding(
        tmp_path,
        {
            "name": "Heel eBMD",
            "snps_used": 10,
            "snps_total": 20,
            "source_study": "Synthetic study",
        },
    )

    body = client.get("/api/analysis/ebmd/prs?sample_id=1").json()

    assert body["withheld"] is False
    assert body["available"] is True
    assert body["prs"]["name"] == "Heel eBMD"
    assert body["prs"]["snps_used"] == 10
