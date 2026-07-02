"""HTTP 409 vep_bundle gate on AncestryDNA uploads (Plan §5.4, Step 7).

Three cases lock the gate:
- AncestryDNA + bundle < v2.0.0  → 409 with the §5.4 payload shape.
- AncestryDNA + bundle >= v2.0.0 → 202 (gate falls through to the parser).
- 23andMe       + bundle < v2.0.0 → 202 (gate is vendor-scoped).

Step 31 (Plan §8.7) wires the ingest route to
:func:`backend.ingestion.dispatcher.parse`, so the v2-bundle case now
exercises the real AncestryDNA parser (step 30) end-to-end and asserts
the dispatcher-composed ``file_format`` shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.tables import database_versions, reference_metadata

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
V5_FILE = FIXTURES / "sample_23andme_v5.txt"
ANCESTRY_FILE = FIXTURES / "sample_ancestrydna_v2.txt"
REPO_MANIFEST = Path(__file__).resolve().parents[2] / "bundles" / "manifest.json"


def _seed_vep_bundle_version(ref_path: Path, version: str) -> None:
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    with engine.begin() as conn:
        conn.execute(
            database_versions.insert().values(
                db_name="vep_bundle",
                version=version,
                file_path=None,
                file_size_bytes=None,
                downloaded_at=datetime.now(UTC),
                checksum_sha256=None,
            )
        )
    engine.dispose()


@pytest.fixture
def manifest_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point manifest fetches at the in-repo manifest so the 409 payload
    is built deterministically (no network)."""
    monkeypatch.setenv("YELIZTLI_MANIFEST_PATH", str(REPO_MANIFEST))
    from backend.db.manifest import reset_cache

    reset_cache()
    yield
    reset_cache()


def _make_client(tmp_data_dir: Path, *, vep_bundle_version: str | None) -> TestClient:
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()

    if vep_bundle_version is not None:
        _seed_vep_bundle_version(ref_path, vep_bundle_version)

    patchers = [
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.api.routes.ingest.get_registry"),
        patch("backend.api.routes.samples.get_registry"),
    ]
    started = [p.start() for p in patchers]

    from backend.db.connection import DBRegistry, reset_registry

    reset_registry()
    registry = DBRegistry(settings)
    # Indices 2 and 3 are the get_registry patches.
    started[2].return_value = registry
    started[3].return_value = registry

    from backend.main import create_app

    app = create_app()
    client = TestClient(app)

    def _close() -> None:
        client.close()
        registry.dispose_all()
        reset_registry()
        for p in patchers:
            p.stop()

    client.__teardown__ = _close
    return client


@pytest.fixture
def client_factory(tmp_data_dir: Path):
    clients: list[TestClient] = []

    def _factory(vep_bundle_version: str | None) -> TestClient:
        c = _make_client(tmp_data_dir, vep_bundle_version=vep_bundle_version)
        clients.append(c)
        return c

    yield _factory

    for c in clients:
        c.__teardown__()


def test_ancestrydna_with_v1_bundle_returns_409(manifest_env, client_factory) -> None:
    client = client_factory("v1.0.0")
    with open(ANCESTRY_FILE, "rb") as f:
        response = client.post(
            "/api/ingest",
            files={"file": ("ancestry.txt", f, "text/plain")},
        )
    assert response.status_code == 409, response.text
    payload = response.json()["detail"]
    assert payload["error"] == "bundle_version_too_old"
    assert payload["installed_version"] == "v1.0.0"
    # required_version is the manifest's latest vep_bundle version. v1.0.0 is
    # still < the 2.0.0 AncestryDNA floor, so the 409 gate still fires.
    assert payload["required_version"] == "v4.0.0"
    assert payload["vendor"] == "ancestrydna"
    assert payload["update_url"]  # non-empty
    assert payload["size_bytes"] > 0
    assert isinstance(payload["checksum_sha256"], str)
    assert len(payload["checksum_sha256"]) == 64


def test_ancestrydna_with_v2_bundle_returns_202(manifest_env, client_factory) -> None:
    # Step 31 (Plan §8.7): ingest route now uses dispatcher.parse, so the
    # real AncestryDNA parser (step 30) runs end-to-end. With vep_bundle at
    # v2.0.0 the gate falls through, the dispatcher routes the file to
    # parser_ancestrydna, and the composed ``file_format`` proves the route
    # built it from ``result.vendor.value`` + ``result.version``.
    client = client_factory("v2.0.0")
    with open(ANCESTRY_FILE, "rb") as f:
        response = client.post(
            "/api/ingest",
            files={"file": ("ancestry.txt", f, "text/plain")},
        )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["variant_count"] > 0
    assert body["file_format"] == "ancestrydna_v2.0"


def test_23andme_with_v1_bundle_returns_202(manifest_env, client_factory) -> None:
    client = client_factory("v1.0.0")
    with open(V5_FILE, "rb") as f:
        response = client.post(
            "/api/ingest",
            files={"file": ("sample.txt", f, "text/plain")},
        )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["file_format"] == "23andme_v5"
