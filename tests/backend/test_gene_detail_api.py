"""Tests for gene detail page API (P3-41).

Covers:
- GET /api/genes/{symbol} — Full gene detail with UniProt, phenotypes,
  literature, variants, and population AF
- GET /api/genes/{symbol}/variants — Lightweight variant list
- UniProt cache-first architecture with 30-day TTL
- Graceful offline fallback
"""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    gene_phenotype,
    reference_metadata,
    samples,
    uniprot_cache,
)

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    return data_dir


@pytest.fixture
def gene_detail_client(
    tmp_data_dir: Path,
) -> Generator[TestClient, None, None]:
    """FastAPI test client with sample seeded with BRCA1 variants."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    # Create reference.db
    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)

    # Create sample DB
    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    # Register sample
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

    # Seed gene-phenotype records
    with ref_engine.begin() as conn:
        conn.execute(
            gene_phenotype.insert().values(
                gene_symbol="BRCA1",
                disease_name="Hereditary breast-ovarian cancer syndrome",
                disease_id="MONDO:0011450",
                source="mondo_hpo",
                hpo_terms=json.dumps(["HP:0003002", "HP:0003003"]),
                inheritance="autosomal dominant",
            )
        )

    # Seed annotated variants for BRCA1
    with sample_engine.begin() as conn:
        conn.execute(
            annotated_variants.insert().values(
                [
                    {
                        "rsid": "rs80357906",
                        "chrom": "17",
                        "pos": 43091983,
                        "genotype": "A/G",
                        "gene_symbol": "BRCA1",
                        "consequence": "frameshift_variant",
                        "hgvs_protein": "p.Gln1756Profs*74",
                        "hgvs_coding": "c.5266dupC",
                        "clinvar_significance": "Pathogenic",
                        "clinvar_review_stars": 3,
                        "gnomad_af_global": 0.000003,
                        "gnomad_af_afr": 0.000001,
                        "gnomad_af_amr": 0.000002,
                        "gnomad_af_asj": 0.000004,
                        "gnomad_af_eas": 0.0,
                        "gnomad_af_eur": 0.000005,
                        "gnomad_af_fin": 0.0,
                        "gnomad_af_sas": 0.0,
                        "cadd_phred": 38.4,
                        "evidence_conflict": False,
                        "annotation_coverage": 15,
                    },
                    {
                        "rsid": "rs1799950",
                        "chrom": "17",
                        "pos": 43094464,
                        "genotype": "G/A",
                        "gene_symbol": "BRCA1",
                        "consequence": "missense_variant",
                        "hgvs_protein": "p.Arg1699Gln",
                        "hgvs_coding": "c.5096G>A",
                        "clinvar_significance": "Likely_benign",
                        "clinvar_review_stars": 2,
                        "gnomad_af_global": 0.02,
                        "gnomad_af_afr": 0.01,
                        "gnomad_af_amr": 0.015,
                        "gnomad_af_asj": 0.018,
                        "gnomad_af_eas": 0.005,
                        "gnomad_af_eur": 0.025,
                        "gnomad_af_fin": 0.03,
                        "gnomad_af_sas": 0.008,
                        "cadd_phred": 12.1,
                        "evidence_conflict": False,
                        "annotation_coverage": 15,
                    },
                ]
            )
        )

    with patch("backend.db.connection.get_settings", return_value=settings):
        reset_registry()
        from backend.main import create_app

        app = create_app()
        yield TestClient(app)
        reset_registry()


# ── Tests: Full gene detail ──────────────────────────────────────────


class TestGeneDetailEndpoint:
    """Tests for GET /api/genes/{symbol}."""

    def test_gene_detail_returns_phenotypes(self, gene_detail_client: TestClient) -> None:
        """Gene detail returns gene-phenotype records from reference.db."""
        # Mock UniProt and PubMed to isolate phenotype testing
        with (
            patch("backend.api.routes.genes._fetch_uniprot_from_cache", return_value=None),
            patch("backend.api.routes.genes._fetch_uniprot_from_api", return_value=None),
            patch("backend.api.routes.genes._get_stale_uniprot", return_value=None),
            patch("backend.api.routes.genes._fetch_gene_literature", return_value=([], [])),
        ):
            resp = gene_detail_client.get("/api/genes/BRCA1?sample_id=1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["gene_symbol"] == "BRCA1"
        assert len(data["phenotypes"]) == 1
        assert data["phenotypes"][0]["disease_name"] == "Hereditary breast-ovarian cancer syndrome"
        assert data["phenotypes"][0]["inheritance"] == "autosomal dominant"

    def test_gene_detail_returns_variants(self, gene_detail_client: TestClient) -> None:
        """Gene detail includes all sample variants for the gene."""
        with (
            patch("backend.api.routes.genes._fetch_uniprot_from_cache", return_value=None),
            patch("backend.api.routes.genes._fetch_uniprot_from_api", return_value=None),
            patch("backend.api.routes.genes._get_stale_uniprot", return_value=None),
            patch("backend.api.routes.genes._fetch_gene_literature", return_value=([], [])),
        ):
            resp = gene_detail_client.get("/api/genes/BRCA1?sample_id=1")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["variants"]) == 2
        rsids = [v["rsid"] for v in data["variants"]]
        assert "rs80357906" in rsids
        assert "rs1799950" in rsids

    def test_gene_detail_returns_population_af(self, gene_detail_client: TestClient) -> None:
        """Gene detail includes per-population AF data for chart rendering."""
        with (
            patch("backend.api.routes.genes._fetch_uniprot_from_cache", return_value=None),
            patch("backend.api.routes.genes._fetch_uniprot_from_api", return_value=None),
            patch("backend.api.routes.genes._get_stale_uniprot", return_value=None),
            patch("backend.api.routes.genes._fetch_gene_literature", return_value=([], [])),
        ):
            resp = gene_detail_client.get("/api/genes/BRCA1?sample_id=1")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["population_af"]) == 2
        pathogenic_af = next(af for af in data["population_af"] if af["rsid"] == "rs80357906")
        assert pathogenic_af["gnomad_af_global"] == pytest.approx(0.000003)
        assert pathogenic_af["gnomad_af_asj"] == pytest.approx(0.000004)
        assert pathogenic_af["gnomad_af_eur"] == pytest.approx(0.000005)

    def test_gene_detail_case_insensitive(self, gene_detail_client: TestClient) -> None:
        """Gene symbol is normalized to uppercase."""
        with (
            patch("backend.api.routes.genes._fetch_uniprot_from_cache", return_value=None),
            patch("backend.api.routes.genes._fetch_uniprot_from_api", return_value=None),
            patch("backend.api.routes.genes._get_stale_uniprot", return_value=None),
            patch("backend.api.routes.genes._fetch_gene_literature", return_value=([], [])),
        ):
            resp = gene_detail_client.get("/api/genes/brca1?sample_id=1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["gene_symbol"] == "BRCA1"
        assert len(data["variants"]) == 2

    def test_gene_detail_unknown_gene_returns_empty(self, gene_detail_client: TestClient) -> None:
        """Unknown gene returns 200 with empty data (not 404)."""
        with (
            patch("backend.api.routes.genes._fetch_uniprot_from_cache", return_value=None),
            patch("backend.api.routes.genes._fetch_uniprot_from_api", return_value=None),
            patch("backend.api.routes.genes._get_stale_uniprot", return_value=None),
            patch("backend.api.routes.genes._fetch_gene_literature", return_value=([], [])),
        ):
            resp = gene_detail_client.get("/api/genes/FAKEGENE?sample_id=1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["gene_symbol"] == "FAKEGENE"
        assert data["variants"] == []
        assert data["phenotypes"] == []
        assert data["uniprot"] is None
        assert data["uniprot_error"] == "Protein data unavailable offline."

    def test_gene_detail_invalid_sample(self, gene_detail_client: TestClient) -> None:
        """Invalid sample_id returns 404."""
        resp = gene_detail_client.get("/api/genes/BRCA1?sample_id=999")
        assert resp.status_code == 404

    def test_gene_detail_missing_sample_id(self, gene_detail_client: TestClient) -> None:
        """Missing sample_id returns 422."""
        resp = gene_detail_client.get("/api/genes/BRCA1")
        assert resp.status_code == 422


# ── Tests: Gene variants endpoint ────────────────────────────────────


class TestGeneVariantsEndpoint:
    """Tests for GET /api/genes/{symbol}/variants."""

    def test_gene_variants_returns_list(self, gene_detail_client: TestClient) -> None:
        """Variants endpoint returns correct gene variants."""
        resp = gene_detail_client.get("/api/genes/BRCA1/variants?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["gene_symbol"] == "BRCA1"
        assert data["total"] == 2
        assert len(data["variants"]) == 2

    def test_gene_variants_sorted_by_position(self, gene_detail_client: TestClient) -> None:
        """Variants are returned sorted by genomic position."""
        resp = gene_detail_client.get("/api/genes/BRCA1/variants?sample_id=1")
        data = resp.json()
        positions = [v["pos"] for v in data["variants"]]
        assert positions == sorted(positions)

    def test_gene_variants_empty_for_unknown(self, gene_detail_client: TestClient) -> None:
        """Unknown gene returns empty variants list."""
        resp = gene_detail_client.get("/api/genes/NONEXIST/variants?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["variants"] == []


# ── Tests: UniProt cache ─────────────────────────────────────────────


class TestUniProtCache:
    """Tests for UniProt cache-first architecture with 30-day TTL."""

    def test_uniprot_cache_hit(self, gene_detail_client: TestClient) -> None:
        """Fresh cache entry is returned without API call."""
        from backend.api.routes.genes import UniProtData

        cached = UniProtData(
            accession="P38398",
            gene_symbol="BRCA1",
            sequence_length=1863,
            domains=[],
            features=[],
            fetched_at=str(datetime.now(UTC)),
            is_cached=True,
        )

        with (
            patch("backend.api.routes.genes._fetch_uniprot_from_cache", return_value=cached),
            patch("backend.api.routes.genes._fetch_uniprot_from_api") as mock_api,
            patch("backend.api.routes.genes._fetch_gene_literature", return_value=([], [])),
        ):
            resp = gene_detail_client.get("/api/genes/BRCA1?sample_id=1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["uniprot"] is not None
        assert data["uniprot"]["accession"] == "P38398"
        assert data["uniprot"]["is_cached"] is True
        # API should NOT have been called
        mock_api.assert_not_called()

    def test_uniprot_cache_miss_fetches_api(self, gene_detail_client: TestClient) -> None:
        """Cache miss triggers live API fetch."""
        from backend.api.routes.genes import UniProtData

        api_result = UniProtData(
            accession="P38398",
            gene_symbol="BRCA1",
            sequence_length=1863,
            domains=[],
            features=[],
            fetched_at=str(datetime.now(UTC)),
            is_cached=False,
        )

        with (
            patch("backend.api.routes.genes._fetch_uniprot_from_cache", return_value=None),
            patch("backend.api.routes.genes._fetch_uniprot_from_api", return_value=api_result),
            patch("backend.api.routes.genes._fetch_gene_literature", return_value=([], [])),
        ):
            resp = gene_detail_client.get("/api/genes/BRCA1?sample_id=1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["uniprot"] is not None
        assert data["uniprot"]["accession"] == "P38398"
        assert data["uniprot_error"] is None

    def test_uniprot_offline_stale_fallback(self, gene_detail_client: TestClient) -> None:
        """When API fails, stale cache is returned with warning."""
        from backend.api.routes.genes import UniProtData

        stale = UniProtData(
            accession="P38398",
            gene_symbol="BRCA1",
            sequence_length=1863,
            domains=[],
            features=[],
            fetched_at=str(datetime.now(UTC) - timedelta(days=60)),
            is_cached=True,
        )

        with (
            patch("backend.api.routes.genes._fetch_uniprot_from_cache", return_value=None),
            patch("backend.api.routes.genes._fetch_uniprot_from_api", return_value=None),
            patch("backend.api.routes.genes._get_stale_uniprot", return_value=stale),
            patch("backend.api.routes.genes._fetch_gene_literature", return_value=([], [])),
        ):
            resp = gene_detail_client.get("/api/genes/BRCA1?sample_id=1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["uniprot"] is not None
        assert data["uniprot_error"] == "Protein data may be outdated (offline fallback)."

    def test_uniprot_offline_no_cache(self, gene_detail_client: TestClient) -> None:
        """When API fails and no cache exists, error message returned."""
        with (
            patch("backend.api.routes.genes._fetch_uniprot_from_cache", return_value=None),
            patch("backend.api.routes.genes._fetch_uniprot_from_api", return_value=None),
            patch("backend.api.routes.genes._get_stale_uniprot", return_value=None),
            patch("backend.api.routes.genes._fetch_gene_literature", return_value=([], [])),
        ):
            resp = gene_detail_client.get("/api/genes/BRCA1?sample_id=1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["uniprot"] is None
        assert data["uniprot_error"] == "Protein data unavailable offline."


# ── Tests: UniProt cache storage/retrieval (unit) ────────────────────


class TestUniProtCacheStorage:
    """Unit tests for cache storage and retrieval functions."""

    def test_store_and_retrieve_cache(self, tmp_data_dir: Path) -> None:
        """Store UniProt data in cache and retrieve it."""
        from backend.api.routes.genes import (
            ProteinDomain,
            ProteinFeature,
            _fetch_uniprot_from_cache,
            _store_uniprot_cache,
        )

        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
        reference_metadata.create_all(ref_engine)

        with patch("backend.db.connection.get_settings", return_value=settings):
            reset_registry()

            domains = [
                ProteinDomain(type="Domain", description="BRCT 1", start=1646, end=1736),
                ProteinDomain(type="Domain", description="BRCT 2", start=1756, end=1855),
            ]
            features = [
                ProteinFeature(
                    type="Active site",
                    description="Phosphoserine",
                    position=1524,
                    start=1524,
                    end=1524,
                ),
            ]

            _store_uniprot_cache(
                accession="P38398",
                gene_symbol="BRCA1",
                domains=domains,
                features=features,
                sequence_length=1863,
            )

            result = _fetch_uniprot_from_cache("BRCA1")
            assert result is not None
            assert result.accession == "P38398"
            assert result.sequence_length == 1863
            assert len(result.domains) == 2
            assert result.domains[0].description == "BRCT 1"
            assert len(result.features) == 1
            assert result.features[0].position == 1524
            assert result.is_cached is True

            reset_registry()

    def test_cache_ttl_expiry(self, tmp_data_dir: Path) -> None:
        """Expired cache entries return None (triggering re-fetch)."""
        from backend.api.routes.genes import _fetch_uniprot_from_cache

        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
        reference_metadata.create_all(ref_engine)

        # Insert a stale cache entry (40 days old)
        stale_time = datetime.now(UTC) - timedelta(days=40)
        with ref_engine.begin() as conn:
            conn.execute(
                uniprot_cache.insert().values(
                    accession="P38398",
                    gene_symbol="BRCA1",
                    domains="[]",
                    features="[]",
                    sequence_length=1863,
                    fetched_at=stale_time,
                    ttl_days=30,
                )
            )

        with patch("backend.db.connection.get_settings", return_value=settings):
            reset_registry()
            result = _fetch_uniprot_from_cache("BRCA1")
            assert result is None  # Expired — should re-fetch
            reset_registry()

    def test_stale_fallback_returns_expired(self, tmp_data_dir: Path) -> None:
        """Stale fallback returns expired entries for offline mode."""
        from backend.api.routes.genes import _get_stale_uniprot

        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
        reference_metadata.create_all(ref_engine)

        stale_time = datetime.now(UTC) - timedelta(days=60)
        with ref_engine.begin() as conn:
            conn.execute(
                uniprot_cache.insert().values(
                    accession="P38398",
                    gene_symbol="BRCA1",
                    domains=json.dumps(
                        [{"type": "Domain", "description": "BRCT 1", "start": 1646, "end": 1736}]
                    ),
                    features="[]",
                    sequence_length=1863,
                    fetched_at=stale_time,
                    ttl_days=30,
                )
            )

        with patch("backend.db.connection.get_settings", return_value=settings):
            reset_registry()
            result = _get_stale_uniprot("BRCA1")
            assert result is not None
            assert result.accession == "P38398"
            assert len(result.domains) == 1
            reset_registry()


# ── Tests: Literature integration ────────────────────────────────────


class TestGeneLiterature:
    """Tests for PubMed literature in gene detail."""

    def test_literature_included_in_response(self, gene_detail_client: TestClient) -> None:
        """Literature articles appear in gene detail response."""
        from backend.api.routes.genes import PubMedArticleResponse

        mock_articles = [
            PubMedArticleResponse(
                pmid="12345678",
                title="BRCA1 mutations and cancer risk",
                abstract="Abstract text here.",
                authors=["Author A", "Author B"],
                journal="Nature Genetics",
                year=2024,
                is_stale=False,
            ),
        ]

        with (
            patch("backend.api.routes.genes._fetch_uniprot_from_cache", return_value=None),
            patch("backend.api.routes.genes._fetch_uniprot_from_api", return_value=None),
            patch("backend.api.routes.genes._get_stale_uniprot", return_value=None),
            patch(
                "backend.api.routes.genes._fetch_gene_literature",
                return_value=(mock_articles, []),
            ),
        ):
            resp = gene_detail_client.get("/api/genes/BRCA1?sample_id=1")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["literature"]) == 1
        assert data["literature"][0]["pmid"] == "12345678"
        assert data["literature"][0]["title"] == "BRCA1 mutations and cancer risk"
        assert data["literature_errors"] == []

    def test_literature_errors_surfaced(self, gene_detail_client: TestClient) -> None:
        """Literature fetch errors are included in response."""
        with (
            patch("backend.api.routes.genes._fetch_uniprot_from_cache", return_value=None),
            patch("backend.api.routes.genes._fetch_uniprot_from_api", return_value=None),
            patch("backend.api.routes.genes._get_stale_uniprot", return_value=None),
            patch(
                "backend.api.routes.genes._fetch_gene_literature",
                return_value=([], ["PubMed network request failed. Showing cached data."]),
            ),
        ):
            resp = gene_detail_client.get("/api/genes/BRCA1?sample_id=1")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["literature_errors"]) == 1
        assert "PubMed network request failed" in data["literature_errors"][0]
