"""Route-level coverage for the FH HTTP endpoints (issue #530).

Two non-trivial GET handlers had zero test references at the route boundary
(their underlying logic is unit-tested in ``test_fh.py`` / ``test_cardiovascular.py``,
but no test exercised the routes themselves):

- ``GET /api/analysis/fh/assessment``            — composed FH view (fh.py)
- ``GET /api/analysis/cardiovascular/fh-status``  — FH status determination,
  gated by ``require_fresh_sample`` (cardiovascular.py)

These tests register an existing-but-never-annotated sample with an empty
``findings`` table (so ``require_fresh_sample`` passes and both handlers take
their "nothing found" path) and assert the routes respond 200 with a
well-formed body, plus the 404 missing-sample contract. The fixture mirrors
the established ``test_g6pd_api.py`` pattern (patch ``get_settings`` + reset the
DB registry so it rebuilds against the temp data dir).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import reference_metadata, samples


@pytest.fixture
def fh_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    """Client with a single existing sample (id=1) whose ``findings`` table is
    empty — exists for the route to resolve, never annotated so the staleness
    gate passes."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)

    (tmp_data_dir / "samples").mkdir(parents=True, exist_ok=True)
    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Empty Sample",
                db_path="samples/sample_1.db",
                file_format="v5",
                file_hash="fh530",
            )
        )

    ref_engine.dispose()
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc
        reset_registry()


class TestFhAssessmentRoute:
    """GET /api/analysis/fh/assessment"""

    def test_empty_sample_returns_200_with_no_findings(self, fh_client: TestClient) -> None:
        resp = fh_client.get("/api/analysis/fh/assessment", params={"sample_id": 1})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["has_monogenic"] is False
        assert body["monogenic"] == []
        assert body["apob_fdb"] is None
        assert body["ldl_prs"] is None
        assert body["criteria_context"]  # the framing context is always present

    def test_missing_sample_returns_404(self, fh_client: TestClient) -> None:
        resp = fh_client.get("/api/analysis/fh/assessment", params={"sample_id": 999})
        assert resp.status_code == 404


class TestFhStatusRoute:
    """GET /api/analysis/cardiovascular/fh-status (require_fresh_sample-gated)"""

    def test_empty_sample_returns_negative(self, fh_client: TestClient) -> None:
        resp = fh_client.get("/api/analysis/cardiovascular/fh-status", params={"sample_id": 1})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "Negative"
        assert body["summary_text"]
        # No FH-associated P/LP variants recorded → empty variant set.
        assert body.get("variants", []) == []

    def test_missing_sample_returns_404(self, fh_client: TestClient) -> None:
        # 404 is enforced redundantly: require_fresh_sample resolves existence
        # before staleness, and the handler's own _get_sample_engine also 404s —
        # either layer alone yields 404 for a missing sample.
        resp = fh_client.get("/api/analysis/cardiovascular/fh-status", params={"sample_id": 999})
        assert resp.status_code == 404
