"""Tests for variant detail API (P2-20).

T2-19: Variant detail endpoint returns all expected fields including
evidence conflict data.
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
    gene_phenotype,
    reference_metadata,
    samples,
)

# ── Test fixtures ────────────────────────────────────────────────────

SAMPLE_VARIANT_BRCA1 = {
    "rsid": "rs80357906",
    "chrom": "17",
    "pos": 43094464,
    "ref": "A",
    "alt": "G",
    "genotype": "AG",
    "zygosity": "het",
    "gene_symbol": "BRCA1",
    "transcript_id": "NM_007294.4",
    "consequence": "frameshift_variant",
    "hgvs_coding": "c.5266dupC",
    "hgvs_protein": "p.Gln1756Profs*74",
    "strand": "+",
    "exon_number": 11,
    "mane_select": True,
    "clinvar_significance": "Pathogenic",
    "clinvar_review_stars": 2,
    "clinvar_accession": "VCV000017661",
    "clinvar_conditions": "Breast-ovarian cancer",
    "gnomad_af_global": 0.000003,
    "gnomad_af_afr": 0.000001,
    "gnomad_af_eur": 0.000005,
    "gnomad_af_sas": 0.000002,
    "gnomad_homozygous_count": 0,
    "rare_flag": True,
    "ultra_rare_flag": True,
    "cadd_phred": 38.4,
    "sift_score": 0.0,
    "sift_pred": "D",
    "polyphen2_hsvar_score": 0.999,
    "polyphen2_hsvar_pred": "D",
    "revel": 0.95,
    "mutpred2": 0.9,
    "vest4": 0.88,
    "metasvm": 1.2,
    "metalr": 0.9,
    "gerp_rs": 5.5,
    "phylop": 9.8,
    "mpc": 2.1,
    "primateai": 0.85,
    "dbsnp_build": 132,
    "dbsnp_rsid_current": None,
    "dbsnp_validation": "valid",
    "disease_name": "Hereditary breast cancer",
    "disease_id": "MONDO:0005012",
    "phenotype_source": "mondo_hpo",
    "hpo_terms": '["HP:0003002"]',
    "inheritance_pattern": "AD",
    "deleterious_count": 4,
    "deleterious_total_assessed": 4,
    "evidence_conflict": False,
    "ensemble_pathogenic": True,
    "annotation_coverage": 0b011111,
}

SAMPLE_VARIANT_VUS = {
    "rsid": "rs123456789",
    "chrom": "7",
    "pos": 117559590,
    "ref": "G",
    "alt": "A",
    "genotype": "GA",
    "zygosity": "het",
    "gene_symbol": "CFTR",
    "transcript_id": "NM_000492.4",
    "consequence": "missense_variant",
    "clinvar_significance": "Uncertain significance",
    "clinvar_review_stars": 1,
    "clinvar_accession": "VCV000012345",
    "clinvar_conditions": "Cystic fibrosis",
    "gnomad_af_global": 0.001,
    "rare_flag": True,
    "ultra_rare_flag": False,
    "cadd_phred": 28.4,
    "sift_score": 0.01,
    "sift_pred": "D",
    "polyphen2_hsvar_score": 0.95,
    "polyphen2_hsvar_pred": "D",
    "revel": 0.75,
    "metasvm": 0.8,
    "metalr": 0.7,
    "alphamissense_pathogenicity": 0.91,
    "alphamissense_class": "likely_pathogenic",
    "deleterious_count": 4,
    "evidence_conflict": True,
    "ensemble_pathogenic": True,
    "annotation_coverage": 0b001111,
}

SAMPLE_VARIANT_MINIMAL = {
    "rsid": "rs999",
    "chrom": "1",
    "pos": 1000,
    "ref": "C",
    "alt": "T",
    "genotype": "CC",
    "zygosity": "hom_ref",
    "annotation_coverage": 0b000001,
}

GENE_PHENOTYPE_DATA = [
    {
        "gene_symbol": "BRCA1",
        "disease_name": "Hereditary breast cancer",
        "disease_id": "MONDO:0005012",
        "hpo_terms": '["HP:0003002", "HP:0100013"]',
        "source": "mondo_hpo",
        "inheritance": "AD",
    },
    {
        "gene_symbol": "BRCA1",
        "disease_name": "Breast-ovarian cancer, familial 1",
        "disease_id": "OMIM:604370",
        "hpo_terms": '["HP:0003002"]',
        "source": "omim",
        "inheritance": "AD",
    },
    {
        "gene_symbol": "CFTR",
        "disease_name": "Cystic fibrosis",
        "disease_id": "MONDO:0009061",
        "hpo_terms": '["HP:0002110"]',
        "source": "mondo_hpo",
        "inheritance": "AR",
    },
]

# Reference rows that exercise the full-page hygiene path (F21/F14/F23): an
# obsolete term that must be dropped, two real diseases that must ALL surface
# (not one arbitrary record), all on BRCA1 — a gene the curated override
# relabels dominant, even though the source rows are mislabelled recessive.
HYGIENE_GENE_PHENOTYPE_DATA = [
    {
        "gene_symbol": "BRCA1",
        "disease_name": "obsolete hereditary cancer predisposition",
        "disease_id": "MONDO:0000001",
        "hpo_terms": "[]",
        "source": "mondo_hpo",
        "inheritance": "AR",
    },
    {
        "gene_symbol": "BRCA1",
        "disease_name": "Hereditary breast cancer",
        "disease_id": "MONDO:0005012",
        "hpo_terms": '["HP:0003002"]',
        "source": "mondo_hpo",
        "inheritance": "AR",
    },
    {
        "gene_symbol": "BRCA1",
        "disease_name": "Breast-ovarian cancer, familial 1",
        "disease_id": "OMIM:604370",
        "hpo_terms": '["HP:0003002"]',
        "source": "omim",
        "inheritance": "AR",
    },
]


@pytest.fixture
def tmp_data_dir(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    return data_dir


def _setup_client(tmp_data_dir: Path, variants: list[dict], gene_pheno: list[dict] | None = None):
    """Create TestClient with annotated sample and optionally gene-phenotype data."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name="test_detail",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="hash123",
            )
        )
        sample_id = result.lastrowid

        # Insert gene-phenotype data
        if gene_pheno:
            conn.execute(gene_phenotype.insert(), gene_pheno)
    ref_engine.dispose()

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)
    if variants:
        all_cols = {col.name for col in annotated_variants.c}
        with sample_engine.begin() as conn:
            normalized = [{k: v.get(k) for k in all_cols} for v in variants]
            conn.execute(annotated_variants.insert(), normalized)
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.api.routes.variant_detail.get_registry") as mock_reg,
        patch("backend.api.routes.annotations_api.get_registry") as mock_reg2,
        patch("backend.api.routes.variants.get_registry") as mock_reg3,
        patch("backend.api.routes.ingest.get_registry") as mock_reg4,
        patch("backend.api.routes.samples.get_registry") as mock_reg5,
    ):
        reset_registry()
        registry = DBRegistry(settings)
        mock_reg.return_value = registry
        mock_reg2.return_value = registry
        mock_reg3.return_value = registry
        mock_reg4.return_value = registry
        mock_reg5.return_value = registry

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc, sample_id

        registry.dispose_all()
        reset_registry()


@pytest.fixture
def client(tmp_data_dir: Path):
    yield from _setup_client(
        tmp_data_dir,
        [SAMPLE_VARIANT_BRCA1, SAMPLE_VARIANT_VUS, SAMPLE_VARIANT_MINIMAL],
        GENE_PHENOTYPE_DATA,
    )


@pytest.fixture
def hygiene_client(tmp_data_dir: Path):
    """Client whose BRCA1 gene-phenotype rows include an obsolete + mislabelled set."""
    yield from _setup_client(
        tmp_data_dir,
        [SAMPLE_VARIANT_BRCA1],
        HYGIENE_GENE_PHENOTYPE_DATA,
    )


@pytest.fixture
def empty_client(tmp_data_dir: Path):
    """Client with sample but no annotated variants."""
    yield from _setup_client(tmp_data_dir, [])


# ═══════════════════════════════════════════════════════════════════════
# GET /api/variants/{rsid} — Core retrieval
# ═══════════════════════════════════════════════════════════════════════


class TestGetVariantDetail:
    def test_returns_200_with_valid_rsid(self, client):
        tc, sid = client
        resp = tc.get(f"/api/variants/rs80357906?sample_id={sid}")
        assert resp.status_code == 200

    def test_returns_correct_rsid(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        assert data["rsid"] == "rs80357906"

    def test_returns_all_core_fields(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        assert data["chrom"] == "17"
        assert data["pos"] == 43094464
        assert data["ref"] == "A"
        assert data["alt"] == "G"
        assert data["genotype"] == "AG"
        assert data["zygosity"] == "het"

    def test_returns_vep_fields(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        assert data["gene_symbol"] == "BRCA1"
        assert data["transcript_id"] == "NM_007294.4"
        assert data["consequence"] == "frameshift_variant"
        assert data["hgvs_coding"] == "c.5266dupC"
        assert data["hgvs_protein"] == "p.Gln1756Profs*74"
        assert data["exon_number"] == 11
        assert data["mane_select"] is True

    def test_returns_clinvar_fields(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        assert data["clinvar_significance"] == "Pathogenic"
        assert data["clinvar_review_stars"] == 2
        assert data["clinvar_accession"] == "VCV000017661"
        assert data["clinvar_conditions"] == "Breast-ovarian cancer"

    def test_returns_gnomad_fields(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        assert data["gnomad_af_global"] == pytest.approx(0.000003)
        assert data["gnomad_af_eur"] == pytest.approx(0.000005)
        assert data["gnomad_homozygous_count"] == 0
        assert data["rare_flag"] is True
        assert data["ultra_rare_flag"] is True

    def test_returns_dbnsfp_fields(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        assert data["cadd_phred"] == pytest.approx(38.4)
        assert data["sift_score"] == pytest.approx(0.0)
        assert data["sift_pred"] == "D"
        assert data["polyphen2_hsvar_score"] == pytest.approx(0.999)
        assert data["revel"] == pytest.approx(0.95)
        assert data["gerp_rs"] == pytest.approx(5.5)
        assert data["phylop"] == pytest.approx(9.8)

    def test_returns_dbsnp_fields(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        assert data["dbsnp_build"] == 132
        assert data["dbsnp_validation"] == "valid"

    def test_returns_gene_phenotype_stored_fields(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        assert data["disease_name"] == "Hereditary breast cancer"
        assert data["disease_id"] == "MONDO:0005012"
        assert data["inheritance_pattern"] == "AD"

    def test_returns_coverage_and_flags(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        assert data["annotation_coverage"] == 0b011111
        assert data["ensemble_pathogenic"] is True
        assert data["deleterious_count"] == 4
        # F25: the k-of-present denominator is surfaced alongside the count.
        assert data["deleterious_total_assessed"] == 4

    def test_returns_alphamissense_context_badge(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs123456789?sample_id={sid}").json()

        assert data["alphamissense_pathogenicity"] == pytest.approx(0.91)
        assert data["alphamissense_class"] == "likely_pathogenic"
        badge = data["alphamissense_badge"]
        assert badge["predictor"] == "AlphaMissense"
        assert badge["context_only"] is True
        assert badge["acmg_vote"] is False
        assert badge["revel_concordance"] == "concordant"


# ═══════════════════════════════════════════════════════════════════════
# 404 cases
# ═══════════════════════════════════════════════════════════════════════


class TestVariantDetailNotFound:
    def test_unknown_rsid_returns_404(self, client):
        tc, sid = client
        resp = tc.get(f"/api/variants/rs_nonexistent?sample_id={sid}")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_unknown_sample_returns_404(self, client):
        tc, _ = client
        resp = tc.get("/api/variants/rs80357906?sample_id=9999")
        assert resp.status_code == 404
        assert "sample" in resp.json()["detail"].lower()

    def test_missing_sample_id_returns_422(self, client):
        tc, _ = client
        resp = tc.get("/api/variants/rs80357906")
        assert resp.status_code == 422

    def test_empty_table_returns_404(self, empty_client):
        tc, sid = empty_client
        resp = tc.get(f"/api/variants/rs80357906?sample_id={sid}")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# Gene-phenotype records (OMIM links)
# ═══════════════════════════════════════════════════════════════════════


class TestGenePhenotypes:
    def test_returns_gene_phenotypes_for_brca1(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        gps = data["gene_phenotypes"]
        assert len(gps) == 2

    def test_mondo_record_present(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        mondo = [gp for gp in data["gene_phenotypes"] if gp["source"] == "mondo_hpo"]
        assert len(mondo) == 1
        assert mondo[0]["disease_name"] == "Hereditary breast cancer"
        assert mondo[0]["disease_id"] == "MONDO:0005012"
        assert mondo[0]["omim_link"] is None

    def test_omim_record_has_link(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        omim = [gp for gp in data["gene_phenotypes"] if gp["source"] == "omim"]
        assert len(omim) == 1
        assert omim[0]["disease_id"] == "OMIM:604370"
        assert omim[0]["omim_link"] == "https://omim.org/entry/604370"

    def test_hpo_terms_parsed_as_list(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        omim = [gp for gp in data["gene_phenotypes"] if gp["source"] == "omim"][0]
        assert omim["hpo_terms"] == ["HP:0003002"]

    def test_no_gene_phenotypes_for_variant_without_gene(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs999?sample_id={sid}").json()
        assert data["gene_phenotypes"] == []

    def test_cftr_phenotypes_for_cftr_variant(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs123456789?sample_id={sid}").json()
        gps = data["gene_phenotypes"]
        assert len(gps) == 1
        assert gps[0]["gene_symbol"] == "CFTR"
        assert gps[0]["disease_name"] == "Cystic fibrosis"
        assert gps[0]["inheritance"] == "AR"


class TestGenePhenotypeRefDataHygiene:
    """F23: the full-page gene-phenotype list inherits the engine's hygiene.

    The list is built via ``lookup_gene_phenotypes`` (not a raw table read), so
    obsolete MONDO terms (F21) and gene-mislabelled inheritance (F14) are
    filtered/corrected exactly as the stored single-disease summary is, and
    *every* non-obsolete disease surfaces rather than one arbitrary record.
    """

    def test_obsolete_terms_dropped(self, hygiene_client):
        tc, sid = hygiene_client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        names = [gp["disease_name"].lower() for gp in data["gene_phenotypes"]]
        assert names, "expected at least one non-obsolete disease"
        assert not any(n.startswith("obsolete") for n in names), names

    def test_all_non_obsolete_diseases_surface(self, hygiene_client):
        # F23 core: both real diseases survive — not one arbitrary annots[0].
        tc, sid = hygiene_client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        ids = {gp["disease_id"] for gp in data["gene_phenotypes"]}
        assert ids == {"MONDO:0005012", "OMIM:604370"}

    def test_dominant_inheritance_corrected(self, hygiene_client):
        # F14: curated override relabels the mislabelled-recessive dominant gene.
        tc, sid = hygiene_client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        gps = data["gene_phenotypes"]
        assert gps, "expected gene-phenotype records"
        assert all(gp["inheritance"] == "Autosomal dominant" for gp in gps), gps


# ═══════════════════════════════════════════════════════════════════════
# Evidence conflict detail
# ═══════════════════════════════════════════════════════════════════════


class TestEvidenceConflictDetail:
    def test_no_conflict_for_pathogenic(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        ecd = data["evidence_conflict_detail"]
        assert ecd["has_conflict"] is False
        assert ecd["summary"] is None

    def test_conflict_detail_for_vus(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs123456789?sample_id={sid}").json()
        ecd = data["evidence_conflict_detail"]
        assert ecd["has_conflict"] is True
        assert ecd["clinvar_significance"] == "Uncertain significance"
        assert ecd["clinvar_review_stars"] == 1
        assert ecd["cadd_phred"] == pytest.approx(28.4)

    def test_conflict_summary_text(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs123456789?sample_id={sid}").json()
        ecd = data["evidence_conflict_detail"]
        assert "Uncertain significance" in ecd["summary"]
        assert "in-silico tools predict deleterious" in ecd["summary"]
        assert "CADD: 28.4" in ecd["summary"]

    def test_conflict_lists_deleterious_tools(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs123456789?sample_id={sid}").json()
        ecd = data["evidence_conflict_detail"]
        assert ecd["total_tools_assessed"] > 0
        assert len(ecd["deleterious_tools"]) > 0

    def test_conflict_detail_for_minimal_variant(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs999?sample_id={sid}").json()
        ecd = data["evidence_conflict_detail"]
        assert ecd["has_conflict"] is False
        assert ecd["total_tools_assessed"] == 0
        assert ecd["deleterious_tools"] == []


# ═══════════════════════════════════════════════════════════════════════
# Transcripts (VEP bundle integration)
# ═══════════════════════════════════════════════════════════════════════


VEP_TRANSCRIPT_ROWS = [
    {
        "rsid": "rs80357906",
        "gene": "BRCA1",
        "tid": "NM_007294.4",
        "csq": "frameshift_variant",
        "hgvsc": "c.5266dupC",
        "hgvsp": "p.Gln1756Profs*74",
        "strand": "+",
        "exon": 11,
        "intron": None,
        "mane": 1,
        "chrom": "17",
        "pos": 43094464,
    },
    {
        "rsid": "rs80357906",
        "gene": "BRCA1",
        "tid": "NM_007300.4",
        "csq": "frameshift_variant",
        "hgvsc": "c.5100dupC",
        "hgvsp": "p.Gln1701Profs*74",
        "strand": "+",
        "exon": 10,
        "intron": None,
        "mane": 0,
        "chrom": "17",
        "pos": 43094464,
    },
    {
        "rsid": "rs80357906",
        "gene": "BRCA1",
        "tid": "ENST00000357654.9",
        "csq": "frameshift_variant",
        "hgvsc": "c.5266dupC",
        "hgvsp": "p.Gln1756Profs*74",
        "strand": "+",
        "exon": 11,
        "intron": None,
        "mane": 0,
        "chrom": "17",
        "pos": 43094464,
    },
]


def _setup_vep_client(tmp_data_dir: Path, transcript_rows: list[dict]):
    """Create TestClient with VEP bundle configured."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name="test_vep",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="vep_hash",
            )
        )
        sample_id = result.lastrowid
    ref_engine.dispose()

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)
    all_cols = {col.name for col in annotated_variants.c}
    with sample_engine.begin() as conn:
        conn.execute(
            annotated_variants.insert(),
            [{k: SAMPLE_VARIANT_BRCA1.get(k) for k in all_cols}],
        )
    sample_engine.dispose()

    vep_db_path = tmp_data_dir / "vep_bundle.db"
    vep_engine = sa.create_engine(f"sqlite:///{vep_db_path}")
    with vep_engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE vep_annotations ("
                "  rsid TEXT, gene_symbol TEXT, transcript_id TEXT,"
                "  consequence TEXT, hgvs_coding TEXT, hgvs_protein TEXT,"
                "  strand TEXT, exon_number INTEGER, intron_number INTEGER,"
                "  mane_select INTEGER, chrom TEXT, pos INTEGER"
                ")"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO vep_annotations VALUES "
                "(:rsid, :gene, :tid, :csq, :hgvsc, :hgvsp,"
                " :strand, :exon, :intron, :mane, :chrom, :pos)"
            ),
            transcript_rows,
        )

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.api.routes.variant_detail.get_registry") as mock_reg,
        patch("backend.api.routes.annotations_api.get_registry") as mock_reg2,
        patch("backend.api.routes.variants.get_registry") as mock_reg3,
        patch("backend.api.routes.ingest.get_registry") as mock_reg4,
        patch("backend.api.routes.samples.get_registry") as mock_reg5,
    ):
        reset_registry()
        registry = DBRegistry(settings)
        registry._vep_engine = vep_engine
        mock_reg.return_value = registry
        mock_reg2.return_value = registry
        mock_reg3.return_value = registry
        mock_reg4.return_value = registry
        mock_reg5.return_value = registry

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc, sample_id

        registry.dispose_all()
        reset_registry()
    vep_engine.dispose()


@pytest.fixture
def vep_client(tmp_data_dir: Path):
    """Client with VEP bundle containing multiple transcripts."""
    yield from _setup_vep_client(tmp_data_dir, VEP_TRANSCRIPT_ROWS)


class TestTranscripts:
    def test_transcripts_empty_when_vep_unavailable(self, client):
        """When VEP bundle is not available, transcripts list is empty."""
        tc, sid = client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        # VEP bundle is not set up in test env, so transcripts should be []
        assert data["transcripts"] == []

    def test_transcripts_returned_from_vep_bundle(self, vep_client):
        """When VEP bundle is available, all transcripts are returned."""
        tc, sample_id = vep_client
        resp = tc.get(f"/api/variants/rs80357906?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["transcripts"]) == 3

        # Check MANE Select transcript is flagged
        mane = [t for t in data["transcripts"] if t["mane_select"]]
        assert len(mane) == 1
        assert mane[0]["transcript_id"] == "NM_007294.4"

        # Check non-MANE transcripts
        non_mane = [t for t in data["transcripts"] if not t["mane_select"]]
        assert len(non_mane) == 2
        tids = {t["transcript_id"] for t in non_mane}
        assert "NM_007300.4" in tids
        assert "ENST00000357654.9" in tids

    def test_transcripts_contain_expected_fields(self, vep_client):
        """Each transcript entry has the correct fields."""
        tc, sample_id = vep_client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sample_id}").json()
        t = data["transcripts"][0]
        expected_keys = {
            "transcript_id",
            "gene_symbol",
            "consequence",
            "hgvs_coding",
            "hgvs_protein",
            "strand",
            "exon_number",
            "intron_number",
            "mane_select",
        }
        assert expected_keys <= set(t.keys())
        # At least one transcript should be BRCA1
        brca1 = [t for t in data["transcripts"] if t["gene_symbol"] == "BRCA1"]
        assert len(brca1) > 0


# ═══════════════════════════════════════════════════════════════════════
# Response shape validation
# ═══════════════════════════════════════════════════════════════════════


class TestResponseShape:
    def test_response_has_all_top_level_keys(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        required = {
            "rsid",
            "chrom",
            "pos",
            "ref",
            "alt",
            "genotype",
            "zygosity",
            "gene_symbol",
            "transcript_id",
            "consequence",
            "clinvar_significance",
            "clinvar_review_stars",
            "gnomad_af_global",
            "rare_flag",
            "cadd_phred",
            "sift_score",
            "revel",
            "annotation_coverage",
            "transcripts",
            "gene_phenotypes",
            "evidence_conflict_detail",
        }
        assert required <= set(data.keys())

    def test_evidence_conflict_detail_has_expected_keys(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs80357906?sample_id={sid}").json()
        ecd = data["evidence_conflict_detail"]
        expected = {
            "has_conflict",
            "clinvar_significance",
            "clinvar_review_stars",
            "clinvar_accession",
            "deleterious_count",
            "total_tools_assessed",
            "deleterious_tools",
            "cadd_phred",
            "summary",
        }
        assert expected <= set(ecd.keys())

    def test_minimal_variant_has_null_optional_fields(self, client):
        tc, sid = client
        data = tc.get(f"/api/variants/rs999?sample_id={sid}").json()
        assert data["gene_symbol"] is None
        assert data["clinvar_significance"] is None
        assert data["gnomad_af_global"] is None
        assert data["cadd_phred"] is None

    def test_minimal_variant_surfaces_hom_ref_carriage(self, client):
        """The endpoint must faithfully surface a non-carrier's carriage.

        ``test_returns_all_core_fields`` covers the het branch (rs80357906); this
        is the hom_ref counterpart. rs999 is seeded ``genotype='CC'`` /
        ``zygosity='hom_ref'`` (the individual carries no copy of the ALT), so the
        variant-detail response must report exactly that — a regression that
        dropped or mislabeled carriage (e.g. blanked ``zygosity`` to NULL, or
        rendered the non-carrier as a carrier) would otherwise pass unnoticed.
        """
        tc, sid = client
        data = tc.get(f"/api/variants/rs999?sample_id={sid}").json()
        assert data["genotype"] == "CC"
        assert data["zygosity"] == "hom_ref"
