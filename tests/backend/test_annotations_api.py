"""Tests for annotated variants API (P2-19).

T2-18: Annotation API filters correctly by ClinVar significance and AF threshold.
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
from backend.db.tables import annotated_variants, reference_metadata, samples

# Rich test data covering all annotation fields and filter dimensions.
ANNOTATED_VARIANTS = [
    {
        "rsid": "rs100",
        "chrom": "1",
        "pos": 50000,
        "ref": "A",
        "alt": "G",
        "genotype": "AG",
        "zygosity": "het",
        "gene_symbol": "BRCA1",
        "transcript_id": "NM_007294.4",
        "consequence": "missense_variant",
        "hgvs_coding": "c.5266dupC",
        "hgvs_protein": "p.Gln1756fs",
        "mane_select": True,
        "clinvar_significance": "Pathogenic",
        "clinvar_review_stars": 3,
        "clinvar_accession": "VCV000017661",
        "clinvar_conditions": "Breast-ovarian cancer",
        "gnomad_af_global": 0.0001,
        "gnomad_af_eur": 0.0002,
        "rare_flag": True,
        "ultra_rare_flag": True,
        "cadd_phred": 35.0,
        "sift_score": 0.001,
        "sift_pred": "D",
        "polyphen2_hsvar_score": 0.999,
        "polyphen2_hsvar_pred": "D",
        "revel": 0.95,
        "disease_name": "Hereditary breast cancer",
        "disease_id": "MONDO:0005012",
        "phenotype_source": "mondo_hpo",
        "evidence_conflict": False,
        "ensemble_pathogenic": True,
        "annotation_coverage": 0b011111,
        "deleterious_count": 5,
    },
    {
        "rsid": "rs200",
        "chrom": "2",
        "pos": 10000,
        "ref": "C",
        "alt": "T",
        "genotype": "CT",
        "zygosity": "het",
        "gene_symbol": "TP53",
        "transcript_id": "NM_000546.6",
        "consequence": "synonymous_variant",
        "mane_select": True,
        "clinvar_significance": "Benign",
        "clinvar_review_stars": 2,
        "gnomad_af_global": 0.35,
        "rare_flag": False,
        "ultra_rare_flag": False,
        "evidence_conflict": False,
        "ensemble_pathogenic": False,
        "annotation_coverage": 0b000111,
    },
    {
        "rsid": "rs300",
        "chrom": "7",
        "pos": 117559590,
        "ref": "G",
        "alt": "A",
        "genotype": "GA",
        "zygosity": "het",
        "gene_symbol": "CFTR",
        "consequence": "missense_variant",
        "clinvar_significance": "Pathogenic/Likely pathogenic",
        "clinvar_review_stars": 4,
        "gnomad_af_global": 0.005,
        "rare_flag": True,
        "ultra_rare_flag": False,
        "cadd_phred": 28.0,
        "evidence_conflict": True,
        "ensemble_pathogenic": True,
        "annotation_coverage": 0b001111,
    },
    {
        "rsid": "rs400",
        "chrom": "10",
        "pos": 89717672,
        "ref": "T",
        "alt": "C",
        "genotype": "CC",
        "zygosity": "hom_alt",
        "gene_symbol": "PTEN",
        "consequence": "stop_gained",
        "clinvar_significance": "Likely pathogenic",
        "clinvar_review_stars": 1,
        "gnomad_af_global": 0.00001,
        "rare_flag": True,
        "ultra_rare_flag": True,
        "cadd_phred": 42.0,
        "evidence_conflict": False,
        "ensemble_pathogenic": True,
        "annotation_coverage": 0b001111,
    },
    {
        "rsid": "rs500",
        "chrom": "17",
        "pos": 43094464,
        "ref": "G",
        "alt": "T",
        "genotype": "GG",
        "zygosity": "hom_ref",
        "gene_symbol": "BRCA1",
        "consequence": "intron_variant",
        "clinvar_significance": "Uncertain significance",
        "clinvar_review_stars": 1,
        "gnomad_af_global": 0.02,
        "rare_flag": False,
        "ultra_rare_flag": False,
        "evidence_conflict": False,
        "ensemble_pathogenic": False,
        "annotation_coverage": 0b000111,
    },
    {
        "rsid": "rs600",
        "chrom": "19",
        "pos": 44908684,
        "ref": "T",
        "alt": "C",
        "genotype": "TC",
        "zygosity": "het",
        "gene_symbol": "APOE",
        "consequence": "missense_variant",
        "mane_select": False,
        "clinvar_significance": "Pathogenic",
        "clinvar_review_stars": 4,
        "gnomad_af_global": 0.08,
        "rare_flag": False,
        "ultra_rare_flag": False,
        "evidence_conflict": False,
        "ensemble_pathogenic": False,
        "annotation_coverage": 0b011111,
    },
    {
        "rsid": "rsX01",
        "chrom": "X",
        "pos": 5000,
        "ref": "A",
        "alt": "G",
        "genotype": "AA",
        "zygosity": "hom_ref",
        "gene_symbol": None,
        "consequence": "intergenic_variant",
        "gnomad_af_global": None,
        "rare_flag": False,
        "evidence_conflict": False,
        "ensemble_pathogenic": False,
        "annotation_coverage": 0b000001,
    },
]


def _setup_client(tmp_data_dir: Path, variants: list[dict]):
    """Create TestClient with annotated sample."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name="test_annotated",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="hash123",
            )
        )
        sample_id = result.lastrowid
    ref_engine.dispose()

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)
    if variants:
        # Normalize: all rows must have the same keys for batch insert.
        all_cols = {col.name for col in annotated_variants.c}
        with sample_engine.begin() as conn:
            normalized = [{k: v.get(k) for k in all_cols} for v in variants]
            conn.execute(annotated_variants.insert(), normalized)
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.api.routes.annotations_api.get_registry") as mock_reg,
        patch("backend.api.routes.variants.get_registry") as mock_reg2,
        patch("backend.api.routes.ingest.get_registry") as mock_reg3,
        patch("backend.api.routes.samples.get_registry") as mock_reg4,
    ):
        reset_registry()
        registry = DBRegistry(settings)
        mock_reg.return_value = registry
        mock_reg2.return_value = registry
        mock_reg3.return_value = registry
        mock_reg4.return_value = registry

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc, sample_id

        registry.dispose_all()
        reset_registry()


@pytest.fixture
def client(tmp_data_dir: Path):
    yield from _setup_client(tmp_data_dir, ANNOTATED_VARIANTS)


@pytest.fixture
def empty_client(tmp_data_dir: Path):
    """Client with sample but no annotated variants (empty table)."""
    yield from _setup_client(tmp_data_dir, [])


# ═══════════════════════════════════════════════════════════════════════
# GET /api/annotations — Basic list
# ═══════════════════════════════════════════════════════════════════════


class TestListAnnotatedVariants:
    def test_returns_200(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}")
        assert r.status_code == 200

    def test_returns_all_variants_under_limit(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&limit=50")
        data = r.json()
        assert len(data["items"]) == len(ANNOTATED_VARIANTS)
        assert data["has_more"] is False

    def test_full_annotation_fields_present(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&limit=1")
        item = r.json()["items"][0]
        # Core fields
        assert "rsid" in item
        assert "chrom" in item
        assert "pos" in item
        # VEP fields
        assert "gene_symbol" in item
        assert "transcript_id" in item
        assert "consequence" in item
        assert "hgvs_coding" in item
        assert "hgvs_protein" in item
        assert "mane_select" in item
        # ClinVar fields
        assert "clinvar_significance" in item
        assert "clinvar_review_stars" in item
        assert "clinvar_accession" in item
        assert "clinvar_conditions" in item
        # gnomAD fields
        assert "gnomad_af_global" in item
        assert "gnomad_af_afr" in item
        assert "gnomad_af_eur" in item
        assert "gnomad_homozygous_count" in item
        assert "rare_flag" in item
        assert "ultra_rare_flag" in item
        # dbNSFP fields
        assert "cadd_phred" in item
        assert "sift_score" in item
        assert "revel" in item
        assert "mutpred2" in item
        assert "vest4" in item
        assert "gerp_rs" in item
        assert "phylop" in item
        # Gene-phenotype fields
        assert "disease_name" in item
        assert "disease_id" in item
        assert "inheritance_pattern" in item
        # Flags
        assert "evidence_conflict" in item
        assert "ensemble_pathogenic" in item
        assert "annotation_coverage" in item
        assert "deleterious_count" in item

    def test_canonical_chrom_order(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&limit=50")
        chroms = [i["chrom"] for i in r.json()["items"]]
        expected = ["1", "2", "7", "10", "17", "19", "X"]
        assert chroms == expected

    def test_404_when_no_annotated_variants(self, empty_client):
        tc, sid = empty_client
        r = tc.get(f"/api/annotations?sample_id={sid}")
        assert r.status_code == 404

    def test_404_nonexistent_sample(self, client):
        tc, _ = client
        r = tc.get("/api/annotations?sample_id=999")
        assert r.status_code == 404

    def test_422_missing_sample_id(self, client):
        tc, _ = client
        r = tc.get("/api/annotations")
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════
# Cursor pagination
# ═══════════════════════════════════════════════════════════════════════


class TestAnnotationPagination:
    def test_first_page(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&limit=3")
        data = r.json()
        assert len(data["items"]) == 3
        assert data["has_more"] is True
        assert data["next_cursor_chrom"] is not None

    def test_full_traversal(self, client):
        tc, sid = client
        all_items = []
        cursor_chrom = None
        cursor_pos = None

        for _ in range(20):
            params = f"sample_id={sid}&limit=3"
            if cursor_chrom is not None:
                params += f"&cursor_chrom={cursor_chrom}&cursor_pos={cursor_pos}"
            data = tc.get(f"/api/annotations?{params}").json()
            all_items.extend(data["items"])
            if not data["has_more"]:
                break
            cursor_chrom = data["next_cursor_chrom"]
            cursor_pos = data["next_cursor_pos"]

        assert len(all_items) == len(ANNOTATED_VARIANTS)
        collected = {i["rsid"] for i in all_items}
        expected = {v["rsid"] for v in ANNOTATED_VARIANTS}
        assert collected == expected

    def test_cursor_past_end(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&cursor_chrom=MT&cursor_pos=999999")
        data = r.json()
        assert len(data["items"]) == 0
        assert data["has_more"] is False


# ═══════════════════════════════════════════════════════════════════════
# Filtering — ClinVar significance (T2-18)
# ═══════════════════════════════════════════════════════════════════════


class TestClinvarFilter:
    def test_filter_pathogenic(self, client):
        """clinvar=pathogenic matches 'Pathogenic' and 'Pathogenic/Likely pathogenic'."""
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&clinvar=pathogenic")
        items = r.json()["items"]
        # rs100 (Pathogenic), rs300 (Pathogenic/Likely pathogenic),
        # rs400 (Likely pathogenic), rs600 (Pathogenic)
        rsids = {i["rsid"] for i in items}
        assert "rs100" in rsids
        assert "rs300" in rsids
        assert "rs600" in rsids
        # "Likely pathogenic" also contains "pathogenic"
        assert "rs400" in rsids

    def test_filter_benign(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&clinvar=benign")
        items = r.json()["items"]
        assert len(items) == 1
        assert items[0]["rsid"] == "rs200"

    def test_filter_case_insensitive(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&clinvar=PATHOGENIC")
        items = r.json()["items"]
        assert len(items) >= 3


# ═══════════════════════════════════════════════════════════════════════
# Filtering — AF threshold (T2-18)
# ═══════════════════════════════════════════════════════════════════════


class TestAfThresholdFilter:
    def test_af_max(self, client):
        """af_max=0.01 returns variants with gnomAD AF <= 0.01."""
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&af_max=0.01")
        items = r.json()["items"]
        rsids = {i["rsid"] for i in items}
        # rs100 (0.0001), rs300 (0.005), rs400 (0.00001)
        assert rsids == {"rs100", "rs300", "rs400"}
        # All AFs should be <= 0.01
        for item in items:
            assert item["gnomad_af_global"] is not None
            assert item["gnomad_af_global"] <= 0.01

    def test_af_min(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&af_min=0.01")
        items = r.json()["items"]
        for item in items:
            assert item["gnomad_af_global"] is not None
            assert item["gnomad_af_global"] >= 0.01

    def test_af_range(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&af_min=0.001&af_max=0.01")
        items = r.json()["items"]
        # Only rs300 (0.005) falls in [0.001, 0.01]
        assert len(items) == 1
        assert items[0]["rsid"] == "rs300"


# ═══════════════════════════════════════════════════════════════════════
# Filtering — Consequence
# ═══════════════════════════════════════════════════════════════════════


class TestConsequenceFilter:
    def test_filter_missense(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&consequence=missense_variant")
        items = r.json()["items"]
        assert all(i["consequence"] == "missense_variant" for i in items)
        rsids = {i["rsid"] for i in items}
        assert rsids == {"rs100", "rs300", "rs600"}

    def test_filter_stop_gained(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&consequence=stop_gained")
        items = r.json()["items"]
        assert len(items) == 1
        assert items[0]["rsid"] == "rs400"


# ═══════════════════════════════════════════════════════════════════════
# Filtering — Evidence conflict
# ═══════════════════════════════════════════════════════════════════════


class TestEvidenceConflictFilter:
    def test_filter_conflict_true(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&evidence_conflict=true")
        items = r.json()["items"]
        assert len(items) == 1
        assert items[0]["rsid"] == "rs300"

    def test_filter_conflict_false(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&evidence_conflict=false")
        items = r.json()["items"]
        assert len(items) == len(ANNOTATED_VARIANTS) - 1


# ═══════════════════════════════════════════════════════════════════════
# Filtering — Gene, zygosity, ensemble pathogenic, chromosome
# ═══════════════════════════════════════════════════════════════════════


class TestOtherFilters:
    def test_filter_gene_symbol(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&gene_symbol=BRCA1")
        items = r.json()["items"]
        rsids = {i["rsid"] for i in items}
        assert rsids == {"rs100", "rs500"}

    def test_filter_zygosity(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&zygosity=hom_alt")
        items = r.json()["items"]
        assert len(items) == 1
        assert items[0]["rsid"] == "rs400"

    def test_filter_ensemble_pathogenic(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&ensemble_pathogenic=true")
        items = r.json()["items"]
        rsids = {i["rsid"] for i in items}
        assert rsids == {"rs100", "rs300", "rs400"}

    def test_filter_chrom(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&chrom=1")
        items = r.json()["items"]
        assert len(items) == 1
        assert items[0]["chrom"] == "1"

    def test_filter_rare(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&rare=true")
        items = r.json()["items"]
        assert all(i["rare_flag"] for i in items)
        assert len(items) == 3  # rs100, rs300, rs400

    def test_filter_ultra_rare(self, client):
        """#907: ?ultra_rare=true must select on ultra_rare_flag, not rare_flag.

        rs300 is rare_flag=True but ultra_rare_flag=False — it is in the rare=true
        result (len 3) but must be EXCLUDED here. A wrong-column mutant querying
        rare_flag would include rs300, so the exact-set assertion kills it.
        """
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&ultra_rare=true")
        items = r.json()["items"]
        rsids = {i["rsid"] for i in items}
        assert rsids == {"rs100", "rs400"}  # the two ultra_rare_flag=True rows
        assert all(i["ultra_rare_flag"] for i in items)
        assert "rs300" not in rsids  # rare-but-not-ultra-rare discriminator excluded

    def test_filter_mane_select(self, client):
        """#907: ?mane_select=true must select on the mane_select column.

        rs600 is mane_select=False and rs300/rs400/rs500/rsX01 are NULL; only
        rs100/rs200 are True. The exact-set assertion (non-vacuous + excludes the
        False/NULL rows) makes a wrong-column/wrong-operator bug fail.
        """
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&mane_select=true")
        items = r.json()["items"]
        rsids = {i["rsid"] for i in items}
        assert rsids == {"rs100", "rs200"}  # the two mane_select=True rows
        assert all(i["mane_select"] for i in items)
        assert "rs600" not in rsids  # mane_select=False row excluded


# ═══════════════════════════════════════════════════════════════════════
# Combined filters
# ═══════════════════════════════════════════════════════════════════════


class TestCombinedFilters:
    def test_clinvar_and_af(self, client):
        """PRD example: clinvar=pathogenic&af_max=0.01"""
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&clinvar=pathogenic&af_max=0.01")
        items = r.json()["items"]
        rsids = {i["rsid"] for i in items}
        # rs100: Pathogenic, AF=0.0001 ✓
        # rs300: Pathogenic/LP, AF=0.005 ✓
        # rs400: Likely pathogenic, AF=0.00001 ✓
        assert rsids == {"rs100", "rs300", "rs400"}

    def test_gene_and_consequence(self, client):
        tc, sid = client
        r = tc.get(
            f"/api/annotations?sample_id={sid}&gene_symbol=BRCA1&consequence=missense_variant"
        )
        items = r.json()["items"]
        assert len(items) == 1
        assert items[0]["rsid"] == "rs100"

    def test_filter_with_pagination(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&clinvar=pathogenic&limit=2")
        data = r.json()
        assert len(data["items"]) == 2
        assert data["has_more"] is True

        # Second page
        r2 = tc.get(
            f"/api/annotations?sample_id={sid}&clinvar=pathogenic&limit=2"
            f"&cursor_chrom={data['next_cursor_chrom']}&cursor_pos={data['next_cursor_pos']}"
        )
        d2 = r2.json()
        assert len(d2["items"]) == 2
        # No overlap
        page1 = {i["rsid"] for i in data["items"]}
        page2 = {i["rsid"] for i in d2["items"]}
        assert page1.isdisjoint(page2)

    def test_no_matching_filters(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations?sample_id={sid}&clinvar=benign&af_max=0.001")
        items = r.json()["items"]
        assert len(items) == 0


# ═══════════════════════════════════════════════════════════════════════
# GET /api/annotations/count
# ═══════════════════════════════════════════════════════════════════════


class TestAnnotatedVariantCount:
    def test_total(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations/count?sample_id={sid}")
        data = r.json()
        assert data["total"] == len(ANNOTATED_VARIANTS)
        assert data["filtered"] is False

    def test_filtered_count_clinvar(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations/count?sample_id={sid}&clinvar=pathogenic")
        data = r.json()
        assert data["total"] == 4  # rs100, rs300, rs400, rs600
        assert data["filtered"] is True

    def test_filtered_count_af(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations/count?sample_id={sid}&af_max=0.01")
        data = r.json()
        assert data["total"] == 3  # rs100, rs300, rs400

    def test_404_empty(self, empty_client):
        tc, sid = empty_client
        r = tc.get(f"/api/annotations/count?sample_id={sid}")
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# GET /api/annotations/chromosomes
# ═══════════════════════════════════════════════════════════════════════


class TestAnnotatedChromosomeCounts:
    def test_returns_all_chroms(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations/chromosomes?sample_id={sid}")
        data = r.json()
        chroms = {d["chrom"] for d in data}
        expected = {"1", "2", "7", "10", "17", "19", "X"}
        assert chroms == expected

    def test_canonical_order(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations/chromosomes?sample_id={sid}")
        data = r.json()
        chroms = [d["chrom"] for d in data]
        assert chroms == ["1", "2", "7", "10", "17", "19", "X"]

    def test_filtered_counts(self, client):
        tc, sid = client
        r = tc.get(f"/api/annotations/chromosomes?sample_id={sid}&clinvar=pathogenic")
        data = r.json()
        count_map = {d["chrom"]: d["count"] for d in data}
        # rs100 (chr1), rs300 (chr7), rs400 (chr10), rs600 (chr19)
        assert count_map["1"] == 1
        assert count_map["7"] == 1
        assert count_map["10"] == 1
        assert count_map["19"] == 1

    def test_404_empty(self, empty_client):
        tc, sid = empty_client
        r = tc.get(f"/api/annotations/chromosomes?sample_id={sid}")
        assert r.status_code == 404
