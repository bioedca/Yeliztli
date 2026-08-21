"""Tests for variant table API endpoints (P1-14, P1-15d).

T1-14: GET /api/variants returns cursor-paginated results.
       GET /api/variants/count returns total count.
T1-15d: Async total count returns correct value with annotation_coverage
        filtering; first page loads without waiting for count.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    merge_provenance,
    raw_variants,
    reference_metadata,
    samples,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
V5_FILE = FIXTURES / "sample_23andme_v5.txt"

# Deterministic test variants spanning multiple chromosomes.
# Ordered by canonical chrom sort: 1, 2, 10, 15, 19, 22, X, MT
TEST_VARIANTS = [
    {"rsid": "rs100", "chrom": "1", "pos": 50000, "genotype": "AA"},
    {"rsid": "rs101", "chrom": "1", "pos": 100000, "genotype": "AG"},
    {"rsid": "rs102", "chrom": "1", "pos": 200000, "genotype": "GG"},
    {"rsid": "rs200", "chrom": "2", "pos": 10000, "genotype": "CC"},
    {"rsid": "rs201", "chrom": "2", "pos": 20000, "genotype": "CT"},
    {"rsid": "rs1000", "chrom": "10", "pos": 50000, "genotype": "AA"},
    {"rsid": "rs1500", "chrom": "15", "pos": 30000, "genotype": "GG"},
    {"rsid": "rs1900", "chrom": "19", "pos": 44908684, "genotype": "TC"},
    {"rsid": "rs2200", "chrom": "22", "pos": 19963748, "genotype": "AG"},
    {"rsid": "rsX001", "chrom": "X", "pos": 5000, "genotype": "AA"},
    {"rsid": "rsMT01", "chrom": "MT", "pos": 1000, "genotype": "CC"},
]


def _setup_sample_client(
    tmp_data_dir: Path,
    *,
    sample_name: str,
    db_filename: str,
    file_hash: str,
    variants_table: sa.Table,
    variants_data: list[dict],
):
    """Shared helper: create TestClient with a sample pre-loaded into the given table.

    Yields (client, sample_id) tuple.
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    with ref_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name=sample_name,
                db_path=f"samples/{db_filename}",
                file_format="23andme_v5",
                file_hash=file_hash,
            )
        )
        sample_id = result.lastrowid
    ref_engine.dispose()

    sample_db_path = tmp_data_dir / "samples" / db_filename
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)
    with sample_engine.begin() as conn:
        conn.execute(variants_table.insert(), variants_data)
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.api.routes.variants.get_registry") as mock_reg,
        patch("backend.api.routes.ingest.get_registry") as mock_reg2,
        patch("backend.api.routes.samples.get_registry") as mock_reg3,
    ):
        reset_registry()
        registry = DBRegistry(settings)
        mock_reg.return_value = registry
        mock_reg2.return_value = registry
        mock_reg3.return_value = registry

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc, sample_id

        registry.dispose_all()
        reset_registry()


@pytest.fixture
def client_with_sample(tmp_data_dir: Path):
    """FastAPI TestClient with a sample pre-loaded with TEST_VARIANTS.

    Yields (client, sample_id) tuple.
    """
    yield from _setup_sample_client(
        tmp_data_dir,
        sample_name="test_sample",
        db_filename="sample_1.db",
        file_hash="testhash123",
        variants_table=raw_variants,
        variants_data=TEST_VARIANTS,
    )


# ═══════════════════════════════════════════════════════════════════════
# GET /api/variants — Basic pagination
# ═══════════════════════════════════════════════════════════════════════


class TestListVariants:
    """GET /api/variants returns cursor-paginated raw variants."""

    def test_returns_200(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}")
        assert response.status_code == 200

    def test_returns_all_variants_when_under_limit(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&limit=50")
        data = response.json()
        assert len(data["items"]) == len(TEST_VARIANTS)
        assert data["has_more"] is False

    def test_returns_variant_fields(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&limit=1")
        item = response.json()["items"][0]
        assert "rsid" in item
        assert "chrom" in item
        assert "pos" in item
        assert "genotype" in item

    def test_canonical_chrom_order(self, client_with_sample):
        """Variants should be sorted by canonical chromosome order, not text."""
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&limit=50")
        items = response.json()["items"]
        chroms = [item["chrom"] for item in items]
        # Expected order: 1, 1, 1, 2, 2, 10, 15, 19, 22, X, MT
        expected = ["1", "1", "1", "2", "2", "10", "15", "19", "22", "X", "MT"]
        assert chroms == expected

    def test_within_chrom_sorted_by_pos(self, client_with_sample):
        """Within a chromosome, variants should be sorted by position."""
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&limit=50")
        items = response.json()["items"]
        chrom1_variants = [i for i in items if i["chrom"] == "1"]
        positions = [v["pos"] for v in chrom1_variants]
        assert positions == sorted(positions)

    def test_missing_sample_id_returns_422(self, client_with_sample):
        client, _ = client_with_sample
        response = client.get("/api/variants")
        assert response.status_code == 422

    def test_nonexistent_sample_returns_404(self, client_with_sample):
        client, _ = client_with_sample
        response = client.get("/api/variants?sample_id=999")
        assert response.status_code == 404

    def test_default_limit_is_50(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}")
        data = response.json()
        assert data["limit"] == 50


# ═══════════════════════════════════════════════════════════════════════
# Cursor-based pagination
# ═══════════════════════════════════════════════════════════════════════


class TestCursorPagination:
    """Keyset cursor pagination on (chrom, pos)."""

    def test_first_page_with_limit(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&limit=3")
        data = response.json()
        assert len(data["items"]) == 3
        assert data["has_more"] is True
        assert data["next_cursor_chrom"] is not None
        assert data["next_cursor_pos"] is not None

    def test_second_page_continues_from_cursor(self, client_with_sample):
        client, sid = client_with_sample
        # Page 1
        r1 = client.get(f"/api/variants?sample_id={sid}&limit=3")
        d1 = r1.json()
        cursor_chrom = d1["next_cursor_chrom"]
        cursor_pos = d1["next_cursor_pos"]

        # Page 2
        r2 = client.get(
            f"/api/variants?sample_id={sid}&limit=3"
            f"&cursor_chrom={cursor_chrom}&cursor_pos={cursor_pos}"
        )
        d2 = r2.json()
        assert len(d2["items"]) == 3

        # No overlap between pages
        page1_rsids = {i["rsid"] for i in d1["items"]}
        page2_rsids = {i["rsid"] for i in d2["items"]}
        assert page1_rsids.isdisjoint(page2_rsids)

    def test_full_traversal_returns_all_variants(self, client_with_sample):
        """Walking all pages collects every variant exactly once."""
        client, sid = client_with_sample
        all_items = []
        cursor_chrom = None
        cursor_pos = None

        for _ in range(20):  # safety limit
            params = f"sample_id={sid}&limit=3"
            if cursor_chrom is not None:
                params += f"&cursor_chrom={cursor_chrom}&cursor_pos={cursor_pos}"
            response = client.get(f"/api/variants?{params}")
            data = response.json()
            all_items.extend(data["items"])
            if not data["has_more"]:
                break
            cursor_chrom = data["next_cursor_chrom"]
            cursor_pos = data["next_cursor_pos"]

        assert len(all_items) == len(TEST_VARIANTS)
        collected_rsids = {i["rsid"] for i in all_items}
        expected_rsids = {v["rsid"] for v in TEST_VARIANTS}
        assert collected_rsids == expected_rsids

    def test_last_page_has_more_false(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&limit=50")
        data = response.json()
        assert data["has_more"] is False
        assert data["next_cursor_chrom"] is None
        assert data["next_cursor_pos"] is None

    def test_cursor_at_end_returns_empty(self, client_with_sample):
        """Cursor past the last variant returns an empty page."""
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&cursor_chrom=MT&cursor_pos=999999")
        data = response.json()
        assert len(data["items"]) == 0
        assert data["has_more"] is False

    def test_cursor_jump_to_chromosome(self, client_with_sample):
        """Providing cursor_chrom=10&cursor_pos=0 skips to chr10 variants."""
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&cursor_chrom=2&cursor_pos=999999")
        data = response.json()
        # Should skip past chrom 1 and 2, start at chrom 10
        assert len(data["items"]) > 0
        assert data["items"][0]["chrom"] == "10"


# ═══════════════════════════════════════════════════════════════════════
# Filtering
# ═══════════════════════════════════════════════════════════════════════


class TestFiltering:
    """Filter query parameter tests."""

    def test_filter_by_chrom(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&filter=chrom:1")
        data = response.json()
        assert all(i["chrom"] == "1" for i in data["items"])
        assert len(data["items"]) == 3  # 3 variants on chrom 1

    def test_filter_by_genotype(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&filter=genotype:AA")
        data = response.json()
        assert all(i["genotype"] == "AA" for i in data["items"])

    def test_filter_with_no_matches(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&filter=chrom:99")
        data = response.json()
        assert len(data["items"]) == 0
        assert data["has_more"] is False

    def test_filter_combined_with_cursor(self, client_with_sample):
        """Filtering and cursor should work together."""
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&filter=chrom:1&limit=2")
        data = response.json()
        assert len(data["items"]) == 2
        assert data["has_more"] is True

        # Next page of filtered results
        r2 = client.get(
            f"/api/variants?sample_id={sid}&filter=chrom:1&limit=2"
            f"&cursor_chrom={data['next_cursor_chrom']}&cursor_pos={data['next_cursor_pos']}"
        )
        d2 = r2.json()
        assert len(d2["items"]) == 1
        assert d2["has_more"] is False

    def test_unknown_filter_key_ignored(self, client_with_sample):
        """Unknown filter keys should be silently ignored."""
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&filter=nonexistent:foo")
        data = response.json()
        # Should return all variants (filter ignored)
        assert len(data["items"]) == len(TEST_VARIANTS)

    def test_multiple_filters(self, client_with_sample):
        """Multiple comma-separated filters should AND together."""
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&filter=chrom:1,genotype:AG")
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["rsid"] == "rs101"


# ═══════════════════════════════════════════════════════════════════════
# GET /api/variants/count
# ═══════════════════════════════════════════════════════════════════════


class TestVariantCount:
    """GET /api/variants/count returns total count."""

    def test_total_count(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants/count?sample_id={sid}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == len(TEST_VARIANTS)
        assert data["filtered"] is False

    def test_filtered_count(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants/count?sample_id={sid}&filter=chrom:1")
        data = response.json()
        assert data["total"] == 3
        assert data["filtered"] is True

    def test_count_nonexistent_sample(self, client_with_sample):
        client, _ = client_with_sample
        response = client.get("/api/variants/count?sample_id=999")
        assert response.status_code == 404

    def test_count_with_no_matches(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants/count?sample_id={sid}&filter=chrom:99")
        data = response.json()
        assert data["total"] == 0


# ═══════════════════════════════════════════════════════════════════════
# Limit validation
# ═══════════════════════════════════════════════════════════════════════


class TestLimitValidation:
    """Limit parameter validation."""

    def test_limit_zero_returns_422(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&limit=0")
        assert response.status_code == 422

    def test_limit_exceeds_max_returns_422(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&limit=501")
        assert response.status_code == 422

    def test_limit_1_returns_one_item(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&limit=1")
        data = response.json()
        assert len(data["items"]) == 1
        assert data["has_more"] is True


# ═══════════════════════════════════════════════════════════════════════
# GET /api/variants/chromosomes (P1-15b)
# ═══════════════════════════════════════════════════════════════════════


class TestChromosomeCounts:
    """GET /api/variants/chromosomes returns per-chromosome variant counts."""

    def test_returns_200(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants/chromosomes?sample_id={sid}")
        assert response.status_code == 200

    def test_returns_all_chromosomes_with_data(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants/chromosomes?sample_id={sid}")
        data = response.json()
        chroms = {item["chrom"] for item in data}
        # TEST_VARIANTS has: 1, 2, 10, 15, 19, 22, X, MT
        expected = {"1", "2", "10", "15", "19", "22", "X", "MT"}
        assert chroms == expected

    def test_returns_correct_counts(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants/chromosomes?sample_id={sid}")
        data = response.json()
        count_map = {item["chrom"]: item["count"] for item in data}
        assert count_map["1"] == 3  # rs100, rs101, rs102
        assert count_map["2"] == 2  # rs200, rs201
        assert count_map["10"] == 1
        assert count_map["X"] == 1
        assert count_map["MT"] == 1

    def test_canonical_order(self, client_with_sample):
        """Chromosomes should be returned in canonical sort order."""
        client, sid = client_with_sample
        response = client.get(f"/api/variants/chromosomes?sample_id={sid}")
        data = response.json()
        chroms = [item["chrom"] for item in data]
        assert chroms == ["1", "2", "10", "15", "19", "22", "X", "MT"]

    def test_with_filter(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants/chromosomes?sample_id={sid}&filter=genotype:AA")
        data = response.json()
        count_map = {item["chrom"]: item["count"] for item in data}
        # AA genotype: rs100 (chr1), rs1000 (chr10), rsX001 (chrX)
        assert count_map["1"] == 1
        assert count_map["10"] == 1
        assert count_map["X"] == 1
        assert "2" not in count_map  # no AA on chr2

    def test_nonexistent_sample_returns_404(self, client_with_sample):
        client, _ = client_with_sample
        response = client.get("/api/variants/chromosomes?sample_id=999")
        assert response.status_code == 404

    def test_missing_sample_id_returns_422(self, client_with_sample):
        client, _ = client_with_sample
        response = client.get("/api/variants/chromosomes")
        assert response.status_code == 422


# ═══════════════════════════════════════════════════════════════════════
# P1-15d: Async total count with annotation_coverage filtering
# ═══════════════════════════════════════════════════════════════════════

# Mix of annotated (coverage != NULL) and unannotated (coverage = NULL) variants.
ANNOTATED_TEST_VARIANTS = [
    {
        "rsid": "rs100",
        "chrom": "1",
        "pos": 50000,
        "genotype": "AA",
        "gene_symbol": "BRCA1",
        "consequence": "missense_variant",
        "annotation_coverage": 0b000111,
    },
    {
        "rsid": "rs101",
        "chrom": "1",
        "pos": 100000,
        "genotype": "AG",
        "gene_symbol": "TP53",
        "consequence": "synonymous_variant",
        "annotation_coverage": 0b000011,
    },
    {
        "rsid": "rs102",
        "chrom": "1",
        "pos": 200000,
        "genotype": "GG",
        "gene_symbol": None,
        "consequence": None,
        "annotation_coverage": None,
    },  # unannotated
    {
        "rsid": "rs200",
        "chrom": "2",
        "pos": 10000,
        "genotype": "CC",
        "gene_symbol": "APOE",
        "consequence": "missense_variant",
        "annotation_coverage": 0b111111,
    },
    {
        "rsid": "rs201",
        "chrom": "2",
        "pos": 20000,
        "genotype": "CT",
        "gene_symbol": None,
        "consequence": None,
        "annotation_coverage": None,
    },  # unannotated
    {
        "rsid": "rs1000",
        "chrom": "10",
        "pos": 50000,
        "genotype": "AA",
        "gene_symbol": None,
        "consequence": None,
        "annotation_coverage": None,
    },  # unannotated
]


@pytest.fixture
def client_with_annotated_sample(tmp_data_dir: Path):
    """FastAPI TestClient with a sample using annotated_variants table.

    3 annotated variants (coverage != NULL) + 3 unannotated (coverage = NULL).
    """
    yield from _setup_sample_client(
        tmp_data_dir,
        sample_name="annotated_sample",
        db_filename="sample_2.db",
        file_hash="annothash456",
        variants_table=annotated_variants,
        variants_data=ANNOTATED_TEST_VARIANTS,
    )


class TestAnnotationCoverageFilter:
    """P1-15d: annotation_coverage:notnull/null filtering on count and list endpoints."""

    def test_count_all_variants(self, client_with_annotated_sample):
        """Unfiltered count returns all variants (annotated + unannotated)."""
        client, sid = client_with_annotated_sample
        response = client.get(f"/api/variants/count?sample_id={sid}")
        data = response.json()
        assert data["total"] == 6
        assert data["filtered"] is False

    def test_count_annotated_only(self, client_with_annotated_sample):
        """annotation_coverage:notnull filters to annotated variants only."""
        client, sid = client_with_annotated_sample
        response = client.get(
            f"/api/variants/count?sample_id={sid}&filter=annotation_coverage:notnull"
        )
        data = response.json()
        assert data["total"] == 3
        assert data["filtered"] is True

    def test_count_unannotated_only(self, client_with_annotated_sample):
        """annotation_coverage:null filters to unannotated variants only."""
        client, sid = client_with_annotated_sample
        response = client.get(
            f"/api/variants/count?sample_id={sid}&filter=annotation_coverage:null"
        )
        data = response.json()
        assert data["total"] == 3
        assert data["filtered"] is True

    def test_list_annotated_only(self, client_with_annotated_sample):
        """List endpoint respects annotation_coverage:notnull filter."""
        client, sid = client_with_annotated_sample
        response = client.get(f"/api/variants?sample_id={sid}&filter=annotation_coverage:notnull")
        data = response.json()
        assert len(data["items"]) == 3
        assert all(item["annotation_coverage"] is not None for item in data["items"])

    def test_list_unannotated_only(self, client_with_annotated_sample):
        """List endpoint respects annotation_coverage:null filter."""
        client, sid = client_with_annotated_sample
        response = client.get(f"/api/variants?sample_id={sid}&filter=annotation_coverage:null")
        data = response.json()
        assert len(data["items"]) == 3
        assert all(item["annotation_coverage"] is None for item in data["items"])

    def test_combined_filter_chrom_and_coverage(self, client_with_annotated_sample):
        """annotation_coverage filter combines with other filters."""
        client, sid = client_with_annotated_sample
        response = client.get(
            f"/api/variants/count?sample_id={sid}&filter=chrom:1,annotation_coverage:notnull"
        )
        data = response.json()
        # chrom 1 has rs100 (annotated), rs101 (annotated), rs102 (unannotated)
        assert data["total"] == 2
        assert data["filtered"] is True

    def test_chromosome_counts_with_coverage_filter(self, client_with_annotated_sample):
        """Chromosome counts endpoint respects annotation_coverage filter."""
        client, sid = client_with_annotated_sample
        response = client.get(
            f"/api/variants/chromosomes?sample_id={sid}&filter=annotation_coverage:notnull"
        )
        data = response.json()
        count_map = {item["chrom"]: item["count"] for item in data}
        assert count_map["1"] == 2  # rs100, rs101
        assert count_map["2"] == 1  # rs200
        assert "10" not in count_map  # rs1000 is unannotated


# ═══════════════════════════════════════════════════════════════════════
# P2-26: ClinVar significance breakdown
# ═══════════════════════════════════════════════════════════════════════

# Annotated variants with ClinVar significance values.
CLINVAR_TEST_VARIANTS = [
    {
        "rsid": "rs100",
        "chrom": "1",
        "pos": 50000,
        "genotype": "AA",
        "gene_symbol": "BRCA1",
        "consequence": "missense_variant",
        "clinvar_significance": "Pathogenic",
        "annotation_coverage": 0b000111,
    },
    {
        "rsid": "rs101",
        "chrom": "1",
        "pos": 100000,
        "genotype": "AG",
        "gene_symbol": "TP53",
        "consequence": "synonymous_variant",
        "clinvar_significance": "Benign",
        "annotation_coverage": 0b000011,
    },
    {
        "rsid": "rs102",
        "chrom": "1",
        "pos": 200000,
        "genotype": "GG",
        "gene_symbol": None,
        "consequence": None,
        "clinvar_significance": None,
        "annotation_coverage": None,
    },
    {
        "rsid": "rs200",
        "chrom": "2",
        "pos": 10000,
        "genotype": "CC",
        "gene_symbol": "APOE",
        "consequence": "missense_variant",
        "clinvar_significance": "Benign",
        "annotation_coverage": 0b111111,
    },
    {
        "rsid": "rs201",
        "chrom": "2",
        "pos": 20000,
        "genotype": "CT",
        "gene_symbol": "LDLR",
        "consequence": "missense_variant",
        "clinvar_significance": "Uncertain significance",
        "annotation_coverage": 0b000011,
    },
    {
        "rsid": "rs300",
        "chrom": "3",
        "pos": 5000,
        "genotype": "TT",
        "gene_symbol": None,
        "consequence": None,
        "clinvar_significance": None,
        "annotation_coverage": None,
    },
]


@pytest.fixture
def client_with_clinvar_sample(tmp_data_dir: Path):
    """FastAPI TestClient with a sample containing ClinVar significance data.

    4 variants with ClinVar + 2 without (NULL).
    """
    yield from _setup_sample_client(
        tmp_data_dir,
        sample_name="clinvar_sample",
        db_filename="sample_clinvar.db",
        file_hash="clinvarhash789",
        variants_table=annotated_variants,
        variants_data=CLINVAR_TEST_VARIANTS,
    )


class TestClinvarSummary:
    """P2-26: GET /api/variants/clinvar-summary returns significance breakdown."""

    def test_returns_200(self, client_with_clinvar_sample):
        client, sid = client_with_clinvar_sample
        response = client.get(f"/api/variants/clinvar-summary?sample_id={sid}")
        assert response.status_code == 200

    def test_groups_by_significance(self, client_with_clinvar_sample):
        client, sid = client_with_clinvar_sample
        data = client.get(f"/api/variants/clinvar-summary?sample_id={sid}").json()
        sig_map = {item["significance"]: item["count"] for item in data["items"]}
        assert sig_map["Benign"] == 2
        assert sig_map["Pathogenic"] == 1
        assert sig_map["Uncertain significance"] == 1

    def test_excludes_null_significance(self, client_with_clinvar_sample):
        """Variants with NULL clinvar_significance are excluded."""
        client, sid = client_with_clinvar_sample
        data = client.get(f"/api/variants/clinvar-summary?sample_id={sid}").json()
        # 4 variants have ClinVar data, 2 have NULL
        assert data["total"] == 4
        significances = [item["significance"] for item in data["items"]]
        assert None not in significances

    def test_sorted_by_count_descending(self, client_with_clinvar_sample):
        client, sid = client_with_clinvar_sample
        data = client.get(f"/api/variants/clinvar-summary?sample_id={sid}").json()
        counts = [item["count"] for item in data["items"]]
        assert counts == sorted(counts, reverse=True)

    def test_total_matches_sum(self, client_with_clinvar_sample):
        client, sid = client_with_clinvar_sample
        data = client.get(f"/api/variants/clinvar-summary?sample_id={sid}").json()
        assert data["total"] == sum(item["count"] for item in data["items"])

    def test_raw_variants_returns_empty(self, client_with_sample):
        """Raw variants table has no ClinVar column → empty result."""
        client, sid = client_with_sample
        data = client.get(f"/api/variants/clinvar-summary?sample_id={sid}").json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_nonexistent_sample_returns_404(self, client_with_clinvar_sample):
        client, _ = client_with_clinvar_sample
        response = client.get("/api/variants/clinvar-summary?sample_id=999")
        assert response.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# P4-26e: Variant search for command palette
# ═══════════════════════════════════════════════════════════════════════


class TestVariantSearch:
    """GET /api/variants/search returns lightweight results for command palette."""

    def test_returns_200(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants/search?sample_id={sid}&q=rs")
        assert response.status_code == 200

    def test_rsid_prefix_search(self, client_with_sample):
        """Search by rsid prefix returns matching variants."""
        client, sid = client_with_sample
        data = client.get(f"/api/variants/search?sample_id={sid}&q=rs10").json()
        rsids = [v["rsid"] for v in data]
        assert "rs100" in rsids
        assert "rs101" in rsids
        assert "rs102" in rsids
        assert "rs1000" in rsids
        # Should not include rs200 etc.
        assert "rs200" not in rsids

    def test_search_respects_limit(self, client_with_sample):
        client, sid = client_with_sample
        data = client.get(f"/api/variants/search?sample_id={sid}&q=rs&limit=3").json()
        assert len(data) <= 3

    def test_search_returns_lightweight_fields(self, client_with_sample):
        client, sid = client_with_sample
        data = client.get(f"/api/variants/search?sample_id={sid}&q=rs100").json()
        assert len(data) >= 1
        v = data[0]
        assert "rsid" in v
        assert "chrom" in v
        assert "pos" in v
        # Raw variants don't have gene_symbol
        assert "gene_symbol" in v

    def test_gene_symbol_search_on_annotated(self, client_with_annotated_sample):
        """Search by gene symbol returns matching annotated variants."""
        client, sid = client_with_annotated_sample
        data = client.get(f"/api/variants/search?sample_id={sid}&q=BRCA1").json()
        assert len(data) == 1
        assert data[0]["rsid"] == "rs100"
        assert data[0]["gene_symbol"] == "BRCA1"

    def test_gene_search_returns_clinvar(self, client_with_clinvar_sample):
        """Annotated variant search includes clinvar_significance."""
        client, sid = client_with_clinvar_sample
        data = client.get(f"/api/variants/search?sample_id={sid}&q=APOE").json()
        assert len(data) == 1
        assert data[0]["clinvar_significance"] == "Benign"

    def test_empty_query_returns_422(self, client_with_sample):
        client, sid = client_with_sample
        response = client.get(f"/api/variants/search?sample_id={sid}&q=")
        assert response.status_code == 422

    def test_nonexistent_sample_returns_404(self, client_with_sample):
        client, _ = client_with_sample
        response = client.get("/api/variants/search?sample_id=999&q=rs1")
        assert response.status_code == 404

    def test_no_match_returns_empty_list(self, client_with_sample):
        client, sid = client_with_sample
        data = client.get(f"/api/variants/search?sample_id={sid}&q=rs99999999").json()
        assert data == []

    def test_whitespace_only_query_returns_empty_list(self, client_with_sample):
        client, sid = client_with_sample
        data = client.get(f"/api/variants/search?sample_id={sid}&q=%20").json()
        assert data == []


# ═══════════════════════════════════════════════════════════════════════
# Step 71 — Source / concordance columns + filter chips (Plan §10.7)
# ═══════════════════════════════════════════════════════════════════════

# Merged-sample fixture: variants live on BOTH raw_variants (source,
# concordance, alt_rsid populated) and annotated_variants (rsid-keyed). The
# list endpoint reads from annotated_variants and LEFT-JOINs raw_variants to
# surface the provenance columns + filter chips per Plan §10.7.
MERGED_RAW_VARIANTS = [
    {
        "rsid": "rs100",
        "chrom": "1",
        "pos": 50000,
        "genotype": "AA",
        "source": "S1",
        "concordance": "match",
        "discordant_alt_genotype": "",
        "alt_rsid": "",
    },
    {
        "rsid": "rs101",
        "chrom": "1",
        "pos": 100000,
        "genotype": "AG",
        "source": "S2",
        "concordance": "filled_nocall",
        "discordant_alt_genotype": "",
        "alt_rsid": "",
    },
    {
        "rsid": "rs102",
        "chrom": "1",
        "pos": 200000,
        "genotype": "??",
        "source": "both",
        "concordance": "discordant",
        "discordant_alt_genotype": "S1=AG;S2=GG",
        "alt_rsid": "",
    },
    {
        "rsid": "rs200",
        "chrom": "2",
        "pos": 10000,
        "genotype": "CC",
        "source": "S1",
        "concordance": "unique",
        "discordant_alt_genotype": "",
        "alt_rsid": "rs200_old",
    },
]

MERGED_ANNOTATED_VARIANTS = [
    {
        "rsid": "rs100",
        "chrom": "1",
        "pos": 50000,
        "genotype": "AA",
        "gene_symbol": "BRCA1",
        "annotation_coverage": 0b000111,
    },
    {
        "rsid": "rs101",
        "chrom": "1",
        "pos": 100000,
        "genotype": "AG",
        "gene_symbol": "TP53",
        "annotation_coverage": 0b000011,
    },
    {
        "rsid": "rs102",
        "chrom": "1",
        "pos": 200000,
        "genotype": "??",
        "gene_symbol": "APOE",
        "annotation_coverage": 0b000011,
    },
    {
        "rsid": "rs200",
        "chrom": "2",
        "pos": 10000,
        "genotype": "CC",
        "gene_symbol": "MTHFR",
        "annotation_coverage": 0b111111,
    },
]


@pytest.fixture
def client_with_merged_sample(tmp_data_dir: Path):
    """FastAPI TestClient for a merged sample: raw_variants AND annotated_variants
    populated, with non-empty source / concordance / alt_rsid values."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    with ref_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name="merged_sample",
                db_path="samples/merged.db",
                file_format="merged_v1",
                file_hash="mergedhash",
            )
        )
        sample_id = result.lastrowid
    ref_engine.dispose()

    sample_db_path = tmp_data_dir / "samples" / "merged.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)
    with sample_engine.begin() as conn:
        conn.execute(raw_variants.insert(), MERGED_RAW_VARIANTS)
        conn.execute(annotated_variants.insert(), MERGED_ANNOTATED_VARIANTS)
        conn.execute(
            merge_provenance.insert().values(
                id=1,
                strategy="flag_only",
                source_sample_ids="[1, 2]",
                source_file_hashes='["source-1", "source-2"]',
                concordance_summary=(
                    '{"match": 1, "filled_nocall": 1, "discordant": 1, '
                    '"unique_S1": 1, "unique_S2": 0, "collapsed_rsid": 1}'
                ),
            )
        )
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.api.routes.variants.get_registry") as mock_reg,
        patch("backend.api.routes.ingest.get_registry") as mock_reg2,
        patch("backend.api.routes.samples.get_registry") as mock_reg3,
    ):
        reset_registry()
        registry = DBRegistry(settings)
        mock_reg.return_value = registry
        mock_reg2.return_value = registry
        mock_reg3.return_value = registry

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc, sample_id

        registry.dispose_all()
        reset_registry()


class TestMergeProvenanceColumns:
    """Step 71 / Plan §10.7 — Source, Concordance, alt_rsid surface on list
    endpoint via LEFT-JOIN against raw_variants when the table is
    annotated_variants. Filters validate against the closed enum sets."""

    def test_list_surfaces_source_concordance_on_merged_sample(self, client_with_merged_sample):
        client, sid = client_with_merged_sample
        response = client.get(f"/api/variants?sample_id={sid}&limit=50")
        assert response.status_code == 200
        items = {item["rsid"]: item for item in response.json()["items"]}
        assert len(items) == 4
        assert items["rs100"]["source"] == "S1"
        assert items["rs100"]["concordance"] == "match"
        assert items["rs101"]["source"] == "S2"
        assert items["rs101"]["concordance"] == "filled_nocall"
        assert items["rs102"]["source"] == "both"
        assert items["rs102"]["concordance"] == "discordant"
        assert items["rs200"]["alt_rsid"] == "rs200_old"

    def test_unmerged_sample_carries_empty_provenance(self, client_with_sample):
        """Unmerged samples carry server-default '' for the new columns; the
        list endpoint passes them through unchanged."""
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&limit=1")
        item = response.json()["items"][0]
        assert item["source"] == ""
        assert item["concordance"] == ""
        assert item["alt_rsid"] == ""

    def test_filter_by_source_s1(self, client_with_merged_sample):
        client, sid = client_with_merged_sample
        response = client.get(f"/api/variants?sample_id={sid}&filter=source:S1")
        rsids = [i["rsid"] for i in response.json()["items"]]
        assert set(rsids) == {"rs100", "rs200"}

    def test_filter_by_source_both(self, client_with_merged_sample):
        client, sid = client_with_merged_sample
        response = client.get(f"/api/variants?sample_id={sid}&filter=source:both")
        rsids = [i["rsid"] for i in response.json()["items"]]
        assert rsids == ["rs102"]

    def test_filter_by_concordance_discordant(self, client_with_merged_sample):
        client, sid = client_with_merged_sample
        response = client.get(f"/api/variants?sample_id={sid}&filter=concordance:discordant")
        rsids = [i["rsid"] for i in response.json()["items"]]
        assert rsids == ["rs102"]

    def test_filter_combined_source_and_concordance(self, client_with_merged_sample):
        client, sid = client_with_merged_sample
        response = client.get(f"/api/variants?sample_id={sid}&filter=source:S1,concordance:unique")
        rsids = [i["rsid"] for i in response.json()["items"]]
        assert rsids == ["rs200"]

    def test_invalid_source_value_silently_dropped(self, client_with_merged_sample):
        """Stray ``source:bogus`` falls outside the enum set and is ignored —
        the response should match the unfiltered case rather than zero-row."""
        client, sid = client_with_merged_sample
        response = client.get(f"/api/variants?sample_id={sid}&filter=source:bogus")
        assert len(response.json()["items"]) == 4

    def test_invalid_concordance_value_silently_dropped(self, client_with_merged_sample):
        client, sid = client_with_merged_sample
        response = client.get(f"/api/variants?sample_id={sid}&filter=concordance:not_a_bucket")
        assert len(response.json()["items"]) == 4

    def test_count_filter_by_source(self, client_with_merged_sample):
        client, sid = client_with_merged_sample
        response = client.get(f"/api/variants/count?sample_id={sid}&filter=source:S1")
        data = response.json()
        assert data["total"] == 2
        assert data["filtered"] is True

    def test_count_filter_by_concordance(self, client_with_merged_sample):
        client, sid = client_with_merged_sample
        response = client.get(f"/api/variants/count?sample_id={sid}&filter=concordance:match")
        data = response.json()
        assert data["total"] == 1
        assert data["filtered"] is True

    def test_raw_variants_table_filters_by_source(self, client_with_sample):
        """When the route falls back to raw_variants (no annotation yet),
        source / concordance filters still resolve directly against it.
        The unmerged fixture has source='' everywhere so source:S1 zero-rows."""
        client, sid = client_with_sample
        response = client.get(f"/api/variants?sample_id={sid}&filter=source:S1")
        assert response.json()["items"] == []
