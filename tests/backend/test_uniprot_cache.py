"""Tests for UniProt protein domain cache (P4-12c).

Covers:
- UniProtCacheFetcher: cache-first get, TTL expiry, stale fallback,
  API fetch, refresh, cache stats, batch pre-fetch
- API endpoints: POST /api/genes/{symbol}/refresh-uniprot,
  GET /api/uniprot-cache/stats
- Huey task: prefetch_uniprot_priority_genes
"""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    reference_metadata,
    samples,
    uniprot_cache,
)
from backend.utils.uniprot import (
    CANCER_PANEL_GENES,
    CARDIO_PANEL_GENES,
    PRIORITY_GENES,
    CacheStats,
    ProteinDomainData,
    ProteinFeatureData,
    UniProtCacheFetcher,
    UniProtResult,
)

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    return data_dir


@pytest.fixture
def ref_engine(tmp_data_dir: Path) -> sa.Engine:
    """Create reference.db engine with tables."""
    db_path = tmp_data_dir / "reference.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")
    reference_metadata.create_all(engine)
    return engine


@pytest.fixture
def fetcher(ref_engine: sa.Engine) -> UniProtCacheFetcher:
    """UniProtCacheFetcher instance for testing."""
    return UniProtCacheFetcher(ref_engine, ttl_days=30)


@pytest.fixture
def uniprot_client(
    tmp_data_dir: Path,
) -> Generator[TestClient, None, None]:
    """FastAPI test client with reference.db for UniProt cache tests."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)

    # Create sample DB (needed for gene detail endpoint)
    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="v5",
                file_hash="abc123",
            )
        )

    with patch("backend.db.connection.get_settings", return_value=settings):
        reset_registry()
        from backend.main import create_app

        app = create_app()
        yield TestClient(app)
        reset_registry()


def _seed_cache_entry(
    engine: sa.Engine,
    *,
    gene_symbol: str = "BRCA1",
    accession: str = "P38398",
    sequence_length: int = 1863,
    domains: list[dict] | None = None,
    features: list[dict] | None = None,
    fetched_at: datetime | None = None,
    ttl_days: int = 30,
) -> None:
    """Seed a uniprot_cache entry for testing."""
    if fetched_at is None:
        fetched_at = datetime.now(UTC)
    if domains is None:
        domains = [{"type": "Domain", "description": "BRCT 1", "start": 1646, "end": 1736}]
    if features is None:
        features = [
            {
                "type": "Active site",
                "description": "Phosphoserine",
                "position": 1524,
                "start": 1524,
                "end": 1524,
            }
        ]

    with engine.begin() as conn:
        conn.execute(
            uniprot_cache.insert().values(
                accession=accession,
                gene_symbol=gene_symbol,
                domains=json.dumps(domains),
                features=json.dumps(features),
                sequence_length=sequence_length,
                fetched_at=fetched_at,
                ttl_days=ttl_days,
            )
        )


# ── Tests: UniProtCacheFetcher ──────────────────────────────────────


class TestUniProtCacheFetcher:
    """Tests for the UniProtCacheFetcher utility class."""

    def test_get_from_fresh_cache(
        self, ref_engine: sa.Engine, fetcher: UniProtCacheFetcher
    ) -> None:
        """Fresh cache entry is returned without API call."""
        _seed_cache_entry(ref_engine)

        with patch.object(fetcher, "_fetch_from_api") as mock_api:
            result = fetcher.get("BRCA1")

        assert result is not None
        assert result.accession == "P38398"
        assert result.gene_symbol == "BRCA1"
        assert result.is_cached is True
        assert result.is_stale is False
        assert len(result.domains) == 1
        assert result.domains[0].description == "BRCT 1"
        assert len(result.features) == 1
        mock_api.assert_not_called()

    def test_get_stale_triggers_api(
        self, ref_engine: sa.Engine, fetcher: UniProtCacheFetcher
    ) -> None:
        """Stale cache entry triggers API fetch."""
        stale_time = datetime.now(UTC) - timedelta(days=40)
        _seed_cache_entry(ref_engine, fetched_at=stale_time)

        mock_result = UniProtResult(
            accession="P38398",
            gene_symbol="BRCA1",
            sequence_length=1863,
            is_cached=False,
        )

        with patch.object(fetcher, "_fetch_from_api", return_value=mock_result):
            result = fetcher.get("BRCA1")

        assert result is not None
        assert result.is_cached is False

    def test_get_api_failure_returns_stale(
        self, ref_engine: sa.Engine, fetcher: UniProtCacheFetcher
    ) -> None:
        """When API fails, stale cache is returned as fallback."""
        stale_time = datetime.now(UTC) - timedelta(days=40)
        _seed_cache_entry(ref_engine, fetched_at=stale_time)

        with patch.object(fetcher, "_fetch_from_api", return_value=None):
            result = fetcher.get("BRCA1")

        assert result is not None
        assert result.is_cached is True
        assert result.is_stale is True
        assert result.accession == "P38398"

    def test_get_no_cache_no_api_returns_none(self, fetcher: UniProtCacheFetcher) -> None:
        """When no cache exists and API fails, returns None."""
        with patch.object(fetcher, "_fetch_from_api", return_value=None):
            result = fetcher.get("NONEXIST")

        assert result is None

    def test_refresh_bypasses_cache(
        self, ref_engine: sa.Engine, fetcher: UniProtCacheFetcher
    ) -> None:
        """Refresh always calls the API, ignoring cache."""
        _seed_cache_entry(ref_engine)

        mock_result = UniProtResult(
            accession="P38398",
            gene_symbol="BRCA1",
            sequence_length=1863,
            is_cached=False,
        )

        with patch.object(fetcher, "_fetch_from_api", return_value=mock_result) as mock_api:
            result = fetcher.refresh("BRCA1")

        mock_api.assert_called_once_with("BRCA1")
        assert result is not None
        assert result.is_cached is False

    def test_cache_ttl_respected(
        self, ref_engine: sa.Engine, fetcher: UniProtCacheFetcher
    ) -> None:
        """Entry exactly at TTL boundary is treated as fresh."""
        # 29 days old (within 30-day TTL)
        almost_stale = datetime.now(UTC) - timedelta(days=29)
        _seed_cache_entry(ref_engine, fetched_at=almost_stale)

        result = fetcher._get_from_cache("BRCA1")
        assert result is not None

    def test_cache_ttl_expiry(self, ref_engine: sa.Engine, fetcher: UniProtCacheFetcher) -> None:
        """Entry beyond TTL returns None from cache lookup."""
        stale = datetime.now(UTC) - timedelta(days=31)
        _seed_cache_entry(ref_engine, fetched_at=stale)

        result = fetcher._get_from_cache("BRCA1")
        assert result is None

    def test_store_and_retrieve(self, ref_engine: sa.Engine, fetcher: UniProtCacheFetcher) -> None:
        """Store data in cache and retrieve it."""
        domains = [ProteinDomainData(type="Domain", description="Test", start=1, end=100)]
        features = [ProteinFeatureData(type="Active site", description="Test", position=50)]

        fetcher._store_cache(
            accession="Q12345",
            gene_symbol="TESTGENE",
            domains=domains,
            features=features,
            sequence_length=500,
        )

        result = fetcher._get_from_cache("TESTGENE")
        assert result is not None
        assert result.accession == "Q12345"
        assert result.sequence_length == 500
        assert len(result.domains) == 1
        assert len(result.features) == 1

    def test_store_updates_existing(
        self, ref_engine: sa.Engine, fetcher: UniProtCacheFetcher
    ) -> None:
        """Storing data for existing gene updates the entry."""
        _seed_cache_entry(ref_engine, sequence_length=1000)

        fetcher._store_cache(
            accession="P38398",
            gene_symbol="BRCA1",
            domains=[],
            features=[],
            sequence_length=2000,
        )

        result = fetcher._get_from_cache("BRCA1")
        assert result is not None
        assert result.sequence_length == 2000

    def test_malformed_json_handled_gracefully(
        self, ref_engine: sa.Engine, fetcher: UniProtCacheFetcher
    ) -> None:
        """Malformed JSON in cache is handled without crash."""
        with ref_engine.begin() as conn:
            conn.execute(
                uniprot_cache.insert().values(
                    accession="X00000",
                    gene_symbol="BADJSON",
                    domains="not valid json",
                    features="also bad",
                    sequence_length=100,
                    fetched_at=datetime.now(UTC),
                    ttl_days=30,
                )
            )

        result = fetcher._get_from_cache("BADJSON")
        assert result is not None
        assert result.domains == []
        assert result.features == []

    def test_fetch_from_api_url_encodes_gene_symbol(self, fetcher: UniProtCacheFetcher) -> None:
        """A gene symbol carrying URL metacharacters is percent-encoded into the
        outbound request, so it cannot inject extra query params or alter the
        request (CodeQL py/partial-ssrf)."""
        captured: dict[str, str] = {}

        class _Resp:
            def raise_for_status(self) -> None: ...

            def json(self) -> dict[str, list]:
                return {"results": []}

        class _Client:
            def __init__(self, *args: object, **kwargs: object) -> None: ...

            def __enter__(self) -> _Client:
                return self

            def __exit__(self, *args: object) -> bool:
                return False

            def get(self, url: str) -> _Resp:
                captured["url"] = url
                return _Resp()

        with patch("httpx.Client", _Client):
            result = fetcher._fetch_from_api("BRCA1&size=500")

        assert result is None
        # The injected `&size=500` must survive only in encoded form.
        assert "BRCA1%26size%3D500" in captured["url"]
        assert "BRCA1&size=500" not in captured["url"]


# ── Tests: Cache statistics ─────────────────────────────────────────


class TestCacheStats:
    """Tests for UniProt cache statistics."""

    def test_empty_cache_stats(self, fetcher: UniProtCacheFetcher) -> None:
        """Empty cache returns zero counts."""
        stats = fetcher.get_cache_stats()
        assert stats.total_entries == 0
        assert stats.fresh_entries == 0
        assert stats.stale_entries == 0
        assert stats.genes_cached == []

    def test_populated_cache_stats(
        self, ref_engine: sa.Engine, fetcher: UniProtCacheFetcher
    ) -> None:
        """Cache stats reflect stored entries correctly."""
        _seed_cache_entry(ref_engine, gene_symbol="BRCA1", accession="P38398")
        _seed_cache_entry(ref_engine, gene_symbol="TP53", accession="P04637")

        stats = fetcher.get_cache_stats()
        assert stats.total_entries == 2
        assert stats.fresh_entries == 2
        assert stats.stale_entries == 0
        assert "BRCA1" in stats.genes_cached
        assert "TP53" in stats.genes_cached

    def test_stale_entries_counted(
        self, ref_engine: sa.Engine, fetcher: UniProtCacheFetcher
    ) -> None:
        """Stale entries are counted separately."""
        _seed_cache_entry(ref_engine, gene_symbol="BRCA1", accession="P38398")
        _seed_cache_entry(
            ref_engine,
            gene_symbol="TP53",
            accession="P04637",
            fetched_at=datetime.now(UTC) - timedelta(days=60),
        )

        stats = fetcher.get_cache_stats()
        assert stats.total_entries == 2
        assert stats.fresh_entries == 1
        assert stats.stale_entries == 1


# ── Tests: Batch pre-fetch ──────────────────────────────────────────


class TestPrefetch:
    """Tests for batch pre-fetch functionality."""

    def test_prefetch_skips_fresh(
        self, ref_engine: sa.Engine, fetcher: UniProtCacheFetcher
    ) -> None:
        """Pre-fetch skips genes already in fresh cache."""
        _seed_cache_entry(ref_engine, gene_symbol="BRCA1", accession="P38398")

        with patch.object(fetcher, "_fetch_from_api", return_value=None) as mock_api:
            result = fetcher.prefetch_genes(["BRCA1"], skip_fresh=True, delay_seconds=0)

        assert result.cached_already == 1
        assert result.fetched == 0
        mock_api.assert_not_called()

    def test_prefetch_fetches_missing(self, fetcher: UniProtCacheFetcher) -> None:
        """Pre-fetch fetches genes not in cache."""
        mock_result = UniProtResult(
            accession="P38398",
            gene_symbol="BRCA1",
            sequence_length=1863,
        )

        with patch.object(fetcher, "_fetch_from_api", return_value=mock_result):
            result = fetcher.prefetch_genes(["BRCA1"], delay_seconds=0)

        assert result.fetched == 1
        assert result.cached_already == 0
        assert result.failed == 0

    def test_prefetch_counts_failures(self, fetcher: UniProtCacheFetcher) -> None:
        """Pre-fetch records failures."""
        with patch.object(fetcher, "_fetch_from_api", return_value=None):
            result = fetcher.prefetch_genes(["UNKNOWN"], delay_seconds=0)

        assert result.failed == 1
        assert "UNKNOWN" in result.errors[0]

    def test_prefetch_mixed_results(
        self, ref_engine: sa.Engine, fetcher: UniProtCacheFetcher
    ) -> None:
        """Pre-fetch handles mix of cached, fetched, and failed genes."""
        _seed_cache_entry(ref_engine, gene_symbol="BRCA1", accession="P38398")

        def mock_fetch(gene: str) -> UniProtResult | None:
            if gene == "TP53":
                return UniProtResult(accession="P04637", gene_symbol="TP53", sequence_length=393)
            return None

        with patch.object(fetcher, "_fetch_from_api", side_effect=mock_fetch):
            result = fetcher.prefetch_genes(
                ["BRCA1", "TP53", "FAKEGENE"],
                skip_fresh=True,
                delay_seconds=0,
            )

        assert result.cached_already == 1
        assert result.fetched == 1
        assert result.failed == 1


# ── Tests: Gene panel constants ─────────────────────────────────────


class TestGenePanelConstants:
    """Tests for pre-fetch gene panel constants."""

    def test_cancer_panel_has_expected_genes(self) -> None:
        """Cancer panel contains expected gene symbols."""
        assert "BRCA1" in CANCER_PANEL_GENES
        assert "BRCA2" in CANCER_PANEL_GENES
        assert "TP53" in CANCER_PANEL_GENES
        assert "MLH1" in CANCER_PANEL_GENES
        assert len(CANCER_PANEL_GENES) >= 20

    def test_cardio_panel_has_expected_genes(self) -> None:
        """Cardiovascular panel contains expected gene symbols."""
        assert "LDLR" in CARDIO_PANEL_GENES
        assert "PCSK9" in CARDIO_PANEL_GENES
        assert "MYBPC3" in CARDIO_PANEL_GENES
        assert len(CARDIO_PANEL_GENES) >= 9

    def test_priority_genes_combines_panels(self) -> None:
        """Priority genes list includes all genes from both panels."""
        for gene in CANCER_PANEL_GENES:
            assert gene in PRIORITY_GENES
        for gene in CARDIO_PANEL_GENES:
            assert gene in PRIORITY_GENES

    def test_no_duplicates_in_panels(self) -> None:
        """No duplicate genes within individual panels."""
        assert len(CANCER_PANEL_GENES) == len(set(CANCER_PANEL_GENES))
        assert len(CARDIO_PANEL_GENES) == len(set(CARDIO_PANEL_GENES))


# ── Tests: API endpoints ────────────────────────────────────────────


class TestRefreshEndpoint:
    """Tests for POST /api/genes/{symbol}/refresh-uniprot."""

    def test_refresh_success(self, uniprot_client: TestClient) -> None:
        """Successful refresh returns updated data."""
        from backend.utils.uniprot import UniProtResult

        mock_result = UniProtResult(
            accession="P38398",
            gene_symbol="BRCA1",
            sequence_length=1863,
            domains=[ProteinDomainData(type="Domain", description="BRCT", start=1, end=100)],
            features=[],
        )

        with patch("backend.api.routes.genes._get_fetcher") as mock_get_fetcher:
            mock_fetcher = MagicMock()
            mock_fetcher.refresh.return_value = mock_result
            mock_get_fetcher.return_value = mock_fetcher

            resp = uniprot_client.post("/api/genes/BRCA1/refresh-uniprot")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["gene_symbol"] == "BRCA1"
        assert data["accession"] == "P38398"
        assert data["domains_count"] == 1

    def test_refresh_failure(self, uniprot_client: TestClient) -> None:
        """Failed refresh returns error message."""
        with patch("backend.api.routes.genes._get_fetcher") as mock_get_fetcher:
            mock_fetcher = MagicMock()
            mock_fetcher.refresh.return_value = None
            mock_get_fetcher.return_value = mock_fetcher

            resp = uniprot_client.post("/api/genes/NONEXIST/refresh-uniprot")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "Failed" in data["message"]

    def test_refresh_normalizes_case(self, uniprot_client: TestClient) -> None:
        """Gene symbol is normalized to uppercase."""
        from backend.utils.uniprot import UniProtResult

        mock_result = UniProtResult(
            accession="P38398",
            gene_symbol="BRCA1",
            sequence_length=1863,
        )

        with patch("backend.api.routes.genes._get_fetcher") as mock_get_fetcher:
            mock_fetcher = MagicMock()
            mock_fetcher.refresh.return_value = mock_result
            mock_get_fetcher.return_value = mock_fetcher

            resp = uniprot_client.post("/api/genes/brca1/refresh-uniprot")

        assert resp.status_code == 200
        assert resp.json()["gene_symbol"] == "BRCA1"

    def test_refresh_cooldown(self, uniprot_client: TestClient) -> None:
        """Second refresh within cooldown period is rejected."""
        from backend.api.routes import genes as genes_mod
        from backend.utils.uniprot import UniProtResult

        mock_result = UniProtResult(
            accession="P38398",
            gene_symbol="COOLDOWN",
            sequence_length=1863,
        )

        # Clear any existing cooldown
        genes_mod._refresh_cooldowns.pop("COOLDOWN", None)

        with patch("backend.api.routes.genes._get_fetcher") as mock_get_fetcher:
            mock_fetcher = MagicMock()
            mock_fetcher.refresh.return_value = mock_result
            mock_get_fetcher.return_value = mock_fetcher

            # First refresh succeeds
            resp1 = uniprot_client.post("/api/genes/COOLDOWN/refresh-uniprot")
            assert resp1.status_code == 200
            assert resp1.json()["success"] is True

            # Second refresh within cooldown is rejected
            resp2 = uniprot_client.post("/api/genes/COOLDOWN/refresh-uniprot")
            assert resp2.status_code == 200
            assert resp2.json()["success"] is False
            assert "Cooldown" in resp2.json()["message"]

        # Clean up
        genes_mod._refresh_cooldowns.pop("COOLDOWN", None)


class TestCacheStatsEndpoint:
    """Tests for GET /api/uniprot-cache/stats."""

    def test_stats_empty_cache(self, uniprot_client: TestClient) -> None:
        """Stats endpoint returns zeros for empty cache."""
        resp = uniprot_client.get("/api/uniprot-cache/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_entries"] == 0
        assert data["fresh_entries"] == 0
        assert data["stale_entries"] == 0
        assert data["genes_cached"] == []

    def test_stats_with_entries(self, uniprot_client: TestClient) -> None:
        """Stats endpoint reflects cached entries."""
        with patch("backend.api.routes.genes._get_fetcher") as mock_get_fetcher:
            mock_fetcher = MagicMock()
            mock_fetcher.get_cache_stats.return_value = CacheStats(
                total_entries=5,
                fresh_entries=3,
                stale_entries=2,
                oldest_entry="2025-01-01 00:00:00",
                newest_entry="2026-03-20 00:00:00",
                genes_cached=["BRCA1", "BRCA2", "TP53", "LDLR", "PCSK9"],
            )
            mock_get_fetcher.return_value = mock_fetcher

            resp = uniprot_client.get("/api/uniprot-cache/stats")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_entries"] == 5
        assert data["fresh_entries"] == 3
        assert data["stale_entries"] == 2
        assert len(data["genes_cached"]) == 5


# ── Tests: Huey pre-fetch task ──────────────────────────────────────


class TestHueyPrefetchTask:
    """Tests for the Huey background pre-fetch task."""

    def test_create_prefetch_job(self, tmp_data_dir: Path) -> None:
        """create_prefetch_job creates a job record."""
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
        reference_metadata.create_all(ref_engine)

        with patch("backend.db.connection.get_settings", return_value=settings):
            reset_registry()

            from backend.db.tables import jobs
            from backend.tasks.huey_tasks import create_prefetch_job

            job_id = create_prefetch_job()
            assert job_id is not None

            with ref_engine.connect() as conn:
                row = conn.execute(sa.select(jobs).where(jobs.c.job_id == job_id)).fetchone()

            assert row is not None
            assert row.job_type == "uniprot_prefetch"
            assert row.status == "pending"

            reset_registry()


# ── Tests: API fetch and parsing ────────────────────────────────────


class TestAPIFetchParsing:
    """Tests for UniProt API response parsing."""

    def test_extract_features_domains(self, fetcher: UniProtCacheFetcher) -> None:
        """Feature extraction correctly categorizes domains."""
        entry = {
            "features": [
                {
                    "type": "Domain",
                    "description": "BRCT 1",
                    "location": {"start": {"value": 1646}, "end": {"value": 1736}},
                },
                {
                    "type": "Zinc finger",
                    "description": "RING-type",
                    "location": {"start": {"value": 24}, "end": {"value": 64}},
                },
                {
                    "type": "Active site",
                    "description": "Phosphoserine",
                    "location": {"start": {"value": 1524}, "end": {"value": 1524}},
                },
                {
                    "type": "Binding site",
                    "description": "DNA binding",
                    "location": {"start": {"value": 200}, "end": {"value": 250}},
                },
            ]
        }

        domains, features = fetcher._extract_features(entry)
        assert len(domains) == 2
        assert domains[0].type == "Domain"
        assert domains[1].type == "Zinc finger"
        assert len(features) == 2
        assert features[0].type == "Active site"
        assert features[1].type == "Binding site"

    def test_extract_features_skips_unknown(self, fetcher: UniProtCacheFetcher) -> None:
        """Unknown feature types are ignored."""
        entry = {
            "features": [
                {
                    "type": "UnknownType",
                    "description": "test",
                    "location": {"start": {"value": 1}, "end": {"value": 10}},
                },
            ]
        }

        domains, features = fetcher._extract_features(entry)
        assert len(domains) == 0
        assert len(features) == 0

    def test_extract_features_handles_missing_location(self, fetcher: UniProtCacheFetcher) -> None:
        """Domain entries without start/end are skipped."""
        entry = {
            "features": [
                {
                    "type": "Domain",
                    "description": "Incomplete",
                    "location": {"start": {}, "end": {}},
                },
            ]
        }

        domains, features = fetcher._extract_features(entry)
        assert len(domains) == 0

    def test_fetch_from_api_success(self, fetcher: UniProtCacheFetcher) -> None:
        """Successful API fetch parses and caches result."""
        import httpx

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {
                    "primaryAccession": "P38398",
                    "sequence": {"length": 1863},
                    "features": [
                        {
                            "type": "Domain",
                            "description": "BRCT 1",
                            "location": {"start": {"value": 1646}, "end": {"value": 1736}},
                        },
                    ],
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        with patch.object(httpx, "Client", return_value=mock_client):
            result = fetcher._fetch_from_api("BRCA1")

        assert result is not None
        assert result.accession == "P38398"
        assert result.sequence_length == 1863
        assert len(result.domains) == 1

    def test_fetch_from_api_no_results(self, fetcher: UniProtCacheFetcher) -> None:
        """API returning empty results returns None."""
        import httpx

        mock_response = MagicMock()
        mock_response.json.return_value = {"results": []}
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        with patch.object(httpx, "Client", return_value=mock_client):
            result = fetcher._fetch_from_api("NONEXIST")

        assert result is None

    def test_fetch_from_api_network_error(self, fetcher: UniProtCacheFetcher) -> None:
        """Network error returns None (graceful failure)."""
        import httpx

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = httpx.ConnectError("Network unreachable")

        with patch.object(httpx, "Client", return_value=mock_client):
            result = fetcher._fetch_from_api("BRCA1")

        assert result is None
