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

from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.tables import database_versions, reference_metadata
from tests.backend.vep_bundle_test_utils import seed_embedded_vep_bundle_version

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


@contextmanager
def _make_client(
    tmp_data_dir: Path,
    *,
    vep_bundle_version: str | None,
    embedded_vep_bundle_version: str | None = None,
    unreadable_vep_bundle: bool = False,
) -> Generator[TestClient, None, None]:
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()

    if vep_bundle_version is not None:
        _seed_vep_bundle_version(ref_path, vep_bundle_version)
    if embedded_vep_bundle_version is not None:
        seed_embedded_vep_bundle_version(
            settings.vep_bundle_db_path,
            embedded_vep_bundle_version,
        )
    if unreadable_vep_bundle:
        settings.vep_bundle_db_path.write_bytes(b"not a SQLite database")

    with ExitStack() as stack:
        stack.enter_context(patch("backend.main.get_settings", return_value=settings))
        stack.enter_context(patch("backend.db.connection.get_settings", return_value=settings))
        ingest_registry = stack.enter_context(patch("backend.api.routes.ingest.get_registry"))
        samples_registry = stack.enter_context(patch("backend.api.routes.samples.get_registry"))

        from backend.db.connection import DBRegistry, reset_registry

        reset_registry()
        stack.callback(reset_registry)
        registry = DBRegistry(settings)
        stack.callback(registry.dispose_all)
        ingest_registry.return_value = registry
        samples_registry.return_value = registry

        from backend.main import create_app

        app = create_app()
        client = stack.enter_context(TestClient(app))
        client.__settings__ = settings
        yield client


@pytest.fixture
def client_factory(tmp_data_dir: Path):
    with ExitStack() as clients:

        def _factory(
            vep_bundle_version: str | None,
            *,
            embedded_vep_bundle_version: str | None = None,
            unreadable_vep_bundle: bool = False,
        ) -> TestClient:
            return clients.enter_context(
                _make_client(
                    tmp_data_dir,
                    vep_bundle_version=vep_bundle_version,
                    embedded_vep_bundle_version=embedded_vep_bundle_version,
                    unreadable_vep_bundle=unreadable_vep_bundle,
                )
            )

        yield _factory


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


def test_ancestrydna_with_embedded_v2_and_no_registry_row_returns_202(
    manifest_env,
    client_factory,
) -> None:
    client = client_factory(None, embedded_vep_bundle_version="v2.0.0")
    with open(ANCESTRY_FILE, "rb") as f:
        response = client.post(
            "/api/ingest",
            files={"file": ("ancestry.txt", f, "text/plain")},
        )

    assert response.status_code == 202, response.text
    reference_engine = sa.create_engine(f"sqlite:///{client.__settings__.reference_db_path}")
    try:
        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions.c.version).where(
                    database_versions.c.db_name == "vep_bundle"
                )
            ).fetchone()
        assert row is None
    finally:
        reference_engine.dispose()


def test_ancestrydna_with_versionless_bundle_uses_v1_baseline(
    manifest_env,
    client_factory,
) -> None:
    client = client_factory(None, embedded_vep_bundle_version="")
    with open(ANCESTRY_FILE, "rb") as f:
        response = client.post(
            "/api/ingest",
            files={"file": ("ancestry.txt", f, "text/plain")},
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["installed_version"] == "v1.0.0"


def test_explicit_v1_registry_row_precedes_embedded_v2(
    manifest_env,
    client_factory,
) -> None:
    client = client_factory("v1.0.0", embedded_vep_bundle_version="v2.0.0")
    with open(ANCESTRY_FILE, "rb") as f:
        response = client.post(
            "/api/ingest",
            files={"file": ("ancestry.txt", f, "text/plain")},
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["installed_version"] == "v1.0.0"


def test_malformed_embedded_version_fails_safe(manifest_env, client_factory) -> None:
    client = client_factory(None, embedded_vep_bundle_version="not-a-version")
    with open(ANCESTRY_FILE, "rb") as f:
        response = client.post(
            "/api/ingest",
            files={"file": ("ancestry.txt", f, "text/plain")},
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["installed_version"] == "not-a-version"


def test_unreadable_embedded_bundle_fails_safe(
    manifest_env,
    client_factory,
) -> None:
    client = client_factory(None, unreadable_vep_bundle=True)
    with open(ANCESTRY_FILE, "rb") as f:
        response = client.post(
            "/api/ingest",
            files={"file": ("ancestry.txt", f, "text/plain")},
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["installed_version"] == "v1.0.0"
    assert client.__settings__.vep_bundle_db_path.read_bytes() == b"not a SQLite database"


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
