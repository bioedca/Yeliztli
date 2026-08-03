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
    literature_cache,
    reference_metadata,
    samples,
    uniprot_cache,
)
from backend.utils.uniprot import UniProtFetchError, UniProtNoMatchError

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
            gene_phenotype.insert(),
            [
                {
                    "gene_symbol": "BRCA1",
                    "disease_name": "Hereditary breast-ovarian cancer syndrome",
                    "disease_id": "MONDO:0011450",
                    "source": "mondo_hpo",
                    "hpo_terms": json.dumps(
                        [
                            {"id": "HP:0003002", "name": "Breast carcinoma"},
                            {"id": "HP:0003003", "name": None},
                        ]
                    ),
                    # Deliberately divergent source inheritance across two
                    # diseases of one gene: a gene-wide rule would have to
                    # rewrite one of them, so the pair discriminates the
                    # disease-scoped lookup from the removed gene-wide override.
                    "inheritance": "autosomal dominant",
                },
                {
                    "gene_symbol": "BRCA1",
                    "disease_name": "Legacy BRCA1 phenotype record",
                    "disease_id": "OMIM:123456",
                    "source": "omim",
                    "hpo_terms": json.dumps(["HP:0001250"]),
                    "inheritance": "Autosomal recessive",
                },
                {
                    "gene_symbol": "BRCA1",
                    "disease_name": "obsolete BRCA1 phenotype record",
                    "disease_id": "MONDO:9999999",
                    "source": "mondo_hpo",
                    "hpo_terms": json.dumps(["HP:0000001"]),
                    "inheritance": "Autosomal dominant",
                },
            ],
        )

    # Seed annotated variants for BRCA1
    with sample_engine.begin() as conn:
        conn.execute(
            annotated_variants.insert().values(
                [
                    {
                        "rsid": "rs80357906",
                        "chrom": "17",
                        "pos": 41209080,
                        "genotype": "GGG/GGGG",
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

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        try:
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as client:
                yield client
        finally:
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
        assert len(data["phenotypes"]) == 2
        phenotype = next(
            record for record in data["phenotypes"] if record["disease_id"] == "MONDO:0011450"
        )
        assert phenotype["disease_name"] == "Hereditary breast-ovarian cancer syndrome"
        # Each disease keeps the inheritance its own source row carries,
        # verbatim; nothing re-stamps a single gene-wide value over the gene.
        assert {record["disease_id"]: record["inheritance"] for record in data["phenotypes"]} == {
            "MONDO:0011450": "autosomal dominant",
            "OMIM:123456": "Autosomal recessive",
        }
        assert all(
            not record["disease_name"].lower().startswith("obsolete")
            for record in data["phenotypes"]
        )

    def test_gene_detail_decodes_legacy_and_labelled_hpo_storage(
        self, gene_detail_client: TestClient
    ) -> None:
        """Both HPO storage shapes retain the legacy ID list and expose details."""
        with (
            patch("backend.api.routes.genes._fetch_uniprot_from_cache", return_value=None),
            patch("backend.api.routes.genes._fetch_uniprot_from_api", return_value=None),
            patch("backend.api.routes.genes._get_stale_uniprot", return_value=None),
            patch("backend.api.routes.genes._fetch_gene_literature", return_value=([], [])),
        ):
            resp = gene_detail_client.get("/api/genes/BRCA1?sample_id=1")

        assert resp.status_code == 200
        phenotypes = resp.json()["phenotypes"]

        labelled = next(p for p in phenotypes if p["disease_id"] == "MONDO:0011450")
        assert labelled["hpo_terms"] == ["HP:0003002", "HP:0003003"]
        assert labelled["hpo_term_details"] == [
            {"id": "HP:0003002", "name": "Breast carcinoma"},
            {"id": "HP:0003003", "name": None},
        ]

        legacy = next(p for p in phenotypes if p["disease_id"] == "OMIM:123456")
        assert legacy["hpo_terms"] == ["HP:0001250"]
        assert legacy["hpo_term_details"] == [
            {"id": "HP:0001250", "name": None},
        ]

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
            patch(
                "backend.api.routes.genes._fetch_uniprot_from_api",
                side_effect=UniProtNoMatchError("FAKEGENE"),
            ),
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
        assert data["uniprot_error"] == "Protein data is not available for this gene."

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
        assert data["uniprot_error"] == (
            "Showing cached protein data because live data could not be refreshed."
        )

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
        assert data["uniprot_error"] == (
            "UniProt could not be reached. Protein data is unavailable."
        )

    def test_uniprot_http_error_is_not_reported_as_offline(
        self, gene_detail_client: TestClient
    ) -> None:
        """A rejected request keeps the partial response but uses service-error copy."""
        with (
            patch("backend.api.routes.genes._fetch_uniprot_from_cache", return_value=None),
            patch(
                "backend.api.routes.genes._fetch_uniprot_from_api",
                side_effect=UniProtFetchError("HTTP 400"),
            ),
            patch("backend.api.routes.genes._get_stale_uniprot", return_value=None),
            patch("backend.api.routes.genes._fetch_gene_literature", return_value=([], [])),
        ):
            resp = gene_detail_client.get("/api/genes/BRCA1?sample_id=1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["uniprot"] is None
        assert data["uniprot_error"] == "Protein data is temporarily unavailable."
        assert "offline" not in data["uniprot_error"].lower()


class TestUniProtLiveFetch:
    """Unit coverage for the Gene Detail UniProt request contract."""

    @pytest.mark.parametrize("cache_error", [False, True])
    def test_fetch_requests_and_parses_every_supported_feature_field(
        self, cache_error: bool
    ) -> None:
        """The request uses valid ft_* selectors for every annotation the parser consumes."""
        from backend.api.routes.genes import _fetch_uniprot_from_api

        captured_urls: list[str] = []
        captured_params: list[dict[str, str]] = []

        class _Resp:
            def raise_for_status(self) -> None: ...

            def json(self) -> dict[str, list[dict[str, object]]]:
                return {
                    "results": [
                        {
                            "primaryAccession": "P04637",
                            "sequence": {"length": 393},
                            "features": [
                                {
                                    "type": "Region",
                                    "description": "DNA-binding",
                                    "location": {
                                        "start": {"value": 102},
                                        "end": {"value": 292},
                                    },
                                },
                                {
                                    "type": "Binding site",
                                    "description": "Zinc",
                                    "location": {
                                        "start": {"value": 176},
                                        "end": {"value": 176},
                                    },
                                },
                                {
                                    "type": "Disulfide bond",
                                    "description": "Paired cysteines",
                                    "location": {
                                        "start": {"value": 31, "modifier": "EXACT"},
                                        "end": {"value": 96, "modifier": "EXACT"},
                                    },
                                },
                            ],
                        }
                    ]
                }

        class _Client:
            def __init__(self, *args: object, **kwargs: object) -> None: ...

            def __enter__(self) -> _Client:
                return self

            def __exit__(self, *args: object) -> bool:
                return False

            def get(self, url: str, *, params: dict[str, str]) -> _Resp:
                captured_urls.append(url)
                captured_params.append(params)
                return _Resp()

        with (
            patch("httpx.Client", _Client),
            patch("backend.api.routes.genes._store_uniprot_cache") as store_cache,
        ):
            if cache_error:
                store_cache.side_effect = RuntimeError("disk full")
            result = _fetch_uniprot_from_api("TP53")

        assert result is not None
        assert result.accession == "P04637"
        assert [domain.type for domain in result.domains] == ["Region"]
        assert [
            (
                feature.type,
                feature.position,
                feature.start,
                feature.end,
                feature.start_modifier,
                feature.end_modifier,
            )
            for feature in result.features
        ] == [
            ("Binding site", 176, 176, 176, None, None),
            ("Disulfide bond", None, 31, 96, "EXACT", "EXACT"),
        ]
        store_cache.assert_called_once()

        assert captured_urls == ["https://rest.uniprot.org/uniprotkb/search"]
        fields = captured_params[0]["fields"].split(",")
        assert {"ft_region", "ft_binding"}.issubset(fields)
        assert "features" not in fields

    def test_fetch_raises_service_error_for_http_status(self) -> None:
        """The low-level Gene Detail fetch never flattens an HTTP 400 to offline."""
        import httpx

        from backend.api.routes.genes import _fetch_uniprot_from_api

        class _Client:
            def __init__(self, *args: object, **kwargs: object) -> None: ...

            def __enter__(self) -> _Client:
                return self

            def __exit__(self, *args: object) -> bool:
                return False

            def get(self, url: str, *, params: dict[str, str]) -> httpx.Response:
                request = httpx.Request("GET", url, params=params)
                return httpx.Response(400, request=request)

        with patch("httpx.Client", _Client), pytest.raises(UniProtFetchError):
            _fetch_uniprot_from_api("TP53")

    def test_fetch_no_match_invalidates_route_cache(self) -> None:
        """An explicit empty response removes stale data before reporting no match."""
        from backend.api.routes.genes import _fetch_uniprot_from_api

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

            def get(self, url: str, *, params: dict[str, str]) -> _Resp:
                return _Resp()

        with (
            patch("httpx.Client", _Client),
            patch("backend.api.routes.genes._delete_uniprot_cache") as delete_cache,
            pytest.raises(UniProtNoMatchError),
        ):
            _fetch_uniprot_from_api("TP53")

        delete_cache.assert_called_once_with("TP53")


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
                ProteinFeature(
                    type="Disulfide bond",
                    description="Interchain (between B and A chains)",
                    position=None,
                    start=31,
                    end=96,
                    start_modifier="EXACT",
                    end_modifier="EXACT",
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
            assert [
                (
                    feature.type,
                    feature.position,
                    feature.start,
                    feature.end,
                    feature.start_modifier,
                    feature.end_modifier,
                )
                for feature in result.features
            ] == [
                ("Active site", 1524, 1524, 1524, None, None),
                ("Disulfide bond", None, 31, 96, "EXACT", "EXACT"),
            ]
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

    def test_cached_markup_is_normalized_in_api_response(
        self,
        gene_detail_client: TestClient,
        tmp_data_dir: Path,
    ) -> None:
        """Legacy cached markup is normalized through the real gene-detail route."""
        settings = Settings(
            data_dir=tmp_data_dir,
            wal_mode=False,
            pubmed_email="",
            pubmed_api_key="",
        )
        ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
        with ref_engine.begin() as conn:
            conn.execute(
                literature_cache.insert().values(
                    pmid="36766853",
                    gene="BRCA1",
                    title="<i>TP53</i> and CO<sub>2</sub>",
                    abstract="The assay measured 10<sup>6</sup> cells.",
                    authors=json.dumps(["Smith & Jones AB"]),
                    journal="Research & Practice",
                    year=2023,
                    fetched_at=datetime.now(UTC),
                )
            )

        with (
            patch("backend.api.routes.genes._fetch_uniprot_from_cache", return_value=None),
            patch("backend.api.routes.genes._fetch_uniprot_from_api", return_value=None),
            patch("backend.api.routes.genes._get_stale_uniprot", return_value=None),
            patch("backend.api.routes.genes.get_settings", return_value=settings),
        ):
            resp = gene_detail_client.get("/api/genes/BRCA1?sample_id=1")

        ref_engine.dispose()
        assert resp.status_code == 200
        article = resp.json()["literature"][0]
        assert article["title"] == "TP53 and CO_(2)"
        assert article["abstract"] == "The assay measured 10^(6) cells."
        assert article["authors"] == ["Smith & Jones AB"]
        assert article["journal"] == "Research & Practice"

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
