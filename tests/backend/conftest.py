"""Shared fixtures for backend tests.

Provides reusable in-memory SQLite engines, a seeded mini reference
bundle, a DBRegistry wired to tmp_path, a FastAPI test client, and a
sample database pre-loaded with raw variants.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    clinvar_variants,
    cpic_alleles,
    cpic_diplotypes,
    cpic_guidelines,
    dbsnp_merges,
    gene_phenotype,
    gnomad_gene_constraint,
    gwas_associations,
    raw_variants,
    reference_metadata,
    samples,
)

# ═══════════════════════════════════════════════════════════════════════
# In-memory SQLite engines (function-scoped for isolation)
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def reference_engine() -> sa.Engine:
    """In-memory SQLite engine with all reference tables created (empty)."""
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    return engine


@pytest.fixture
def sample_engine() -> sa.Engine:
    """In-memory SQLite engine with all sample tables + predefined tags seeded."""
    engine = sa.create_engine("sqlite://")
    # create_sample_tables sets WAL, creates tables, and seeds tags.
    # WAL PRAGMA is a no-op on :memory: but harmless.
    create_sample_tables(engine)
    return engine


# ═══════════════════════════════════════════════════════════════════════
# Seeded reference engine — mini reference bundle
# ═══════════════════════════════════════════════════════════════════════

# Seed data kept as module-level constants so tests can import & assert
# against them without duplicating values.

SEED_CLINVAR = [
    {
        "rsid": "rs429358",
        "chrom": "19",
        "pos": 44908684,
        "ref": "T",
        "alt": "C",
        "significance": "risk_factor",
        "review_stars": 3,
        "accession": "VCV000017864",
        "conditions": "Alzheimer disease",
        "gene_symbol": "APOE",
        "variation_id": 17864,
    },
    {
        "rsid": "rs7412",
        "chrom": "19",
        "pos": 44908822,
        "ref": "C",
        "alt": "T",
        "significance": "risk_factor",
        "review_stars": 3,
        "accession": "VCV000017865",
        "conditions": "Alzheimer disease",
        "gene_symbol": "APOE",
        "variation_id": 17865,
    },
    {
        "rsid": "rs1801133",
        "chrom": "1",
        "pos": 11856378,
        "ref": "G",
        "alt": "A",
        "significance": "drug_response",
        "review_stars": 2,
        "accession": "VCV000003520",
        "conditions": "Homocysteinemia",
        "gene_symbol": "MTHFR",
        "variation_id": 3520,
    },
    {
        "rsid": "rs4680",
        "chrom": "22",
        "pos": 19963748,
        "ref": "G",
        "alt": "A",
        "significance": "benign",
        "review_stars": 2,
        "accession": "VCV000016312",
        "conditions": "not specified",
        "gene_symbol": "COMT",
        "variation_id": 16312,
    },
    {
        "rsid": "rs80357906",
        "chrom": "17",
        "pos": 43091983,
        "ref": "CTC",
        "alt": "C",
        "significance": "Pathogenic",
        "review_stars": 3,
        "accession": "VCV000017661",
        "conditions": "Hereditary breast and ovarian cancer syndrome",
        "gene_symbol": "BRCA1",
        "variation_id": 17661,
    },
    {
        "rsid": "rs113993960",
        "chrom": "7",
        "pos": 117559590,
        "ref": "ATCT",
        "alt": "A",
        "significance": "Pathogenic",
        "review_stars": 3,
        "accession": "VCV000007105",
        "conditions": "Cystic fibrosis",
        "gene_symbol": "CFTR",
        "variation_id": 7105,
    },
    {
        "rsid": "rs12345",
        "chrom": "1",
        "pos": 100000,
        "ref": "A",
        "alt": "G",
        "significance": "Uncertain_significance",
        "review_stars": 1,
        "accession": "VCV000099999",
        "conditions": "not provided",
        "gene_symbol": "GENE1",
        "variation_id": 99999,
    },
]

SEED_GENE_PHENOTYPE = [
    {
        "gene_symbol": "BRCA1",
        "disease_name": "Hereditary breast and ovarian cancer syndrome",
        "disease_id": "MONDO:0011450",
        "hpo_terms": json.dumps(["HP:0003002", "HP:0100013"]),
        "source": "mondo_hpo",
        "inheritance": "Autosomal dominant",
    },
    {
        "gene_symbol": "CFTR",
        "disease_name": "Cystic fibrosis",
        "disease_id": "MONDO:0009061",
        "hpo_terms": json.dumps(["HP:0002110", "HP:0006538"]),
        "source": "mondo_hpo",
        "inheritance": "Autosomal recessive",
    },
    {
        "gene_symbol": "MTHFR",
        "disease_name": "Homocysteinemia due to MTHFR deficiency",
        "disease_id": "MONDO:0019226",
        "hpo_terms": json.dumps(["HP:0003572"]),
        "source": "mondo_hpo",
        "inheritance": "Autosomal recessive",
    },
    {
        "gene_symbol": "APOE",
        "disease_name": "Alzheimer disease, susceptibility to",
        "disease_id": "MONDO:0004975",
        "hpo_terms": json.dumps(["HP:0002145", "HP:0002354"]),
        "source": "mondo_hpo",
        "inheritance": None,
    },
    {
        "gene_symbol": "COMT",
        "disease_name": "Catechol-O-methyltransferase deficiency",
        "disease_id": "MONDO:0012822",
        "hpo_terms": json.dumps(["HP:0001249"]),
        "source": "mondo_hpo",
        "inheritance": "Autosomal recessive",
    },
]

SEED_CPIC_ALLELES = [
    {
        "gene": "CYP2D6",
        "allele_name": "*1",
        "defining_variants": json.dumps([]),
        "function": "Normal function",
        "activity_score": 1.0,
    },
    {
        "gene": "CYP2D6",
        "allele_name": "*2",
        "defining_variants": json.dumps([{"rsid": "rs16947", "ref": "G", "alt": "A"}]),
        "function": "Normal function",
        "activity_score": 1.0,
    },
    {
        "gene": "CYP2D6",
        "allele_name": "*4",
        "defining_variants": json.dumps([{"rsid": "rs3892097", "ref": "C", "alt": "T"}]),
        "function": "No function",
        "activity_score": 0.0,
    },
    {
        "gene": "CYP2D6",
        "allele_name": "*10",
        "defining_variants": json.dumps([{"rsid": "rs1065852", "ref": "G", "alt": "A"}]),
        "function": "Decreased function",
        "activity_score": 0.25,
    },
    {
        "gene": "CYP2C19",
        "allele_name": "*1",
        "defining_variants": json.dumps([]),
        "function": "Normal function",
        "activity_score": 1.0,
    },
    {
        "gene": "CYP2C19",
        "allele_name": "*2",
        "defining_variants": json.dumps([{"rsid": "rs4244285", "ref": "G", "alt": "A"}]),
        "function": "No function",
        "activity_score": 0.0,
    },
    {
        "gene": "CYP2C19",
        "allele_name": "*17",
        "defining_variants": json.dumps([{"rsid": "rs12248560", "ref": "C", "alt": "T"}]),
        "function": "Increased function",
        "activity_score": 1.5,
    },
]

SEED_CPIC_DIPLOTYPES = [
    {
        "gene": "CYP2D6",
        "diplotype": "*1/*1",
        "phenotype": "Normal Metabolizer",
        "ehr_notation": "CYP2D6 Normal Metabolizer",
        "activity_score": 2.0,
    },
    {
        "gene": "CYP2D6",
        "diplotype": "*1/*4",
        "phenotype": "Intermediate Metabolizer",
        "ehr_notation": "CYP2D6 Intermediate Metabolizer",
        "activity_score": 1.0,
    },
    {
        "gene": "CYP2D6",
        "diplotype": "*4/*4",
        "phenotype": "Poor Metabolizer",
        "ehr_notation": "CYP2D6 Poor Metabolizer",
        "activity_score": 0.0,
    },
    {
        "gene": "CYP2D6",
        "diplotype": "*1/*2",
        "phenotype": "Normal Metabolizer",
        "ehr_notation": "CYP2D6 Normal Metabolizer",
        "activity_score": 2.0,
    },
    {
        "gene": "CYP2D6",
        "diplotype": "*1/*10",
        "phenotype": "Normal Metabolizer",
        "ehr_notation": "CYP2D6 Normal Metabolizer",
        "activity_score": 1.25,
    },
    {
        "gene": "CYP2C19",
        "diplotype": "*1/*1",
        "phenotype": "Normal Metabolizer",
        "ehr_notation": "CYP2C19 Normal Metabolizer",
        "activity_score": 2.0,
    },
    {
        "gene": "CYP2C19",
        "diplotype": "*1/*2",
        "phenotype": "Intermediate Metabolizer",
        "ehr_notation": "CYP2C19 Intermediate Metabolizer",
        "activity_score": 1.0,
    },
    {
        "gene": "CYP2C19",
        "diplotype": "*2/*2",
        "phenotype": "Poor Metabolizer",
        "ehr_notation": "CYP2C19 Poor Metabolizer",
        "activity_score": 0.0,
    },
    {
        "gene": "CYP2C19",
        "diplotype": "*1/*17",
        "phenotype": "Rapid Metabolizer",
        "ehr_notation": "CYP2C19 Rapid Metabolizer",
        "activity_score": 2.5,
    },
    {
        "gene": "CYP2C19",
        "diplotype": "*2/*17",
        "phenotype": "Intermediate Metabolizer",
        "ehr_notation": "CYP2C19 Intermediate Metabolizer",
        "activity_score": 1.5,
    },
]

SEED_CPIC_GUIDELINES = [
    {
        "gene": "CYP2D6",
        "drug": "codeine",
        "phenotype": "Normal Metabolizer",
        "recommendation": "Use label-recommended age- or weight-specific dosing.",
        "classification": "A",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-codeine-and-cyp2d6/",
    },
    {
        "gene": "CYP2D6",
        "drug": "codeine",
        "phenotype": "Intermediate Metabolizer",
        "recommendation": "Use label-recommended age- or weight-specific dosing.",
        "classification": "A",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-codeine-and-cyp2d6/",
    },
    {
        "gene": "CYP2D6",
        "drug": "codeine",
        "phenotype": "Poor Metabolizer",
        "recommendation": "Avoid codeine use. Alternative analgesics recommended.",
        "classification": "A",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-codeine-and-cyp2d6/",
    },
    {
        "gene": "CYP2D6",
        "drug": "tramadol",
        "phenotype": "Poor Metabolizer",
        "recommendation": "Avoid tramadol use due to lack of efficacy.",
        "classification": "B",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-codeine-and-cyp2d6/",
    },
    {
        "gene": "CYP2C19",
        "drug": "clopidogrel",
        "phenotype": "Poor Metabolizer",
        "recommendation": "Use alternative antiplatelet therapy.",
        "classification": "A",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-clopidogrel-and-cyp2c19/",
    },
]

SEED_GWAS = [
    {
        "rsid": "rs429358",
        "chrom": "19",
        "pos": 44908684,
        "trait": "Alzheimer disease",
        "p_value": 1e-200,
        "odds_ratio": 3.68,
        "beta": None,
        "risk_allele": "C",
        "pubmed_id": "24162737",
        "study": "Lambert et al. 2013",
        "sample_size": 74046,
    },
    {
        "rsid": "rs1801133",
        "chrom": "1",
        "pos": 11856378,
        "trait": "Homocysteine levels",
        "p_value": 2e-50,
        "odds_ratio": None,
        "beta": 1.73,
        "risk_allele": "A",
        "pubmed_id": "23824729",
        "study": "van Meurs et al. 2013",
        "sample_size": 44147,
    },
    {
        "rsid": "rs4680",
        "chrom": "22",
        "pos": 19963748,
        "trait": "Pain sensitivity",
        "p_value": 3e-8,
        "odds_ratio": 1.15,
        "beta": None,
        "risk_allele": "A",
        "pubmed_id": "16258542",
        "study": "Zubieta et al. 2003",
        "sample_size": 202,
    },
    {
        "rsid": "rs12913832",
        "chrom": "15",
        "pos": 28365618,
        "trait": "Eye color",
        "p_value": 1e-300,
        "odds_ratio": None,
        "beta": None,
        "risk_allele": "G",
        "pubmed_id": "18488028",
        "study": "Sturm et al. 2008",
        "sample_size": 4000,
    },
    {
        "rsid": "rs7903146",
        "chrom": "10",
        "pos": 114758349,
        "trait": "Type 2 diabetes",
        "p_value": 5e-120,
        "odds_ratio": 1.37,
        "beta": None,
        "risk_allele": "T",
        "pubmed_id": "17463246",
        "study": "Zeggini et al. 2007",
        "sample_size": 10128,
    },
]

SEED_DBSNP_MERGES = [
    {
        "old_rsid": "rs3219489",
        "current_rsid": "rs1805007",
        "build_id": 137,
    },
    {
        "old_rsid": "rs12345",
        "current_rsid": "rs67890",
        "build_id": 144,
    },
    {
        "old_rsid": "rs9999999",
        "current_rsid": "rs429358",
        "build_id": 151,
    },
]

SEED_SAMPLE = {
    "name": "Test Sample",
    "db_path": "samples/sample_1.db",
    "file_format": "23andme_v5",
    "file_hash": "abc123deadbeef",
}

# gnomAD gene-constraint mini bundle (gnomAD v2.1.1, GRCh37). Representative real
# values: APC/SCN5A are strongly LoF-constrained; PCSK9 is not. lof_constrained
# (loeuf < 0.35 or pli > 0.9) is derived at lookup, so only raw metrics are stored.
SEED_GENE_CONSTRAINT = [
    {
        "gene_symbol": "APC",
        "transcript": "ENST00000257430",
        "oe_lof": 0.10,
        "loeuf": 0.16,
        "pli": 1.0,
        "mis_z": 3.06,
        "syn_z": 0.40,
    },
    {
        "gene_symbol": "SCN5A",
        "transcript": "ENST00000333535",
        "oe_lof": 0.10,
        "loeuf": 0.18,
        "pli": 1.0,
        "mis_z": 5.07,
        "syn_z": 0.45,
    },
    {
        "gene_symbol": "PCSK9",
        "transcript": "ENST00000302118",
        "oe_lof": 0.55,
        "loeuf": 0.66,
        "pli": 0.0,
        "mis_z": 1.10,
        "syn_z": -0.20,
    },
]


@pytest.fixture
def seeded_reference_engine(reference_engine: sa.Engine) -> sa.Engine:
    """Reference engine pre-populated with a mini test bundle.

    Includes realistic rows in clinvar_variants, gene_phenotype,
    cpic_alleles, cpic_diplotypes, cpic_guidelines, gwas_associations,
    and samples.
    """
    with reference_engine.begin() as conn:
        conn.execute(clinvar_variants.insert(), SEED_CLINVAR)
        conn.execute(gene_phenotype.insert(), SEED_GENE_PHENOTYPE)
        conn.execute(cpic_alleles.insert(), SEED_CPIC_ALLELES)
        conn.execute(cpic_diplotypes.insert(), SEED_CPIC_DIPLOTYPES)
        conn.execute(cpic_guidelines.insert(), SEED_CPIC_GUIDELINES)
        conn.execute(gwas_associations.insert(), SEED_GWAS)
        conn.execute(dbsnp_merges.insert(), SEED_DBSNP_MERGES)
        conn.execute(gnomad_gene_constraint.insert(), SEED_GENE_CONSTRAINT)
        conn.execute(samples.insert(), [SEED_SAMPLE])
    return reference_engine


# ═══════════════════════════════════════════════════════════════════════
# DBRegistry wired to tmp_path
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def db_registry(tmp_data_dir: Path) -> DBRegistry:
    """DBRegistry backed by real temp-dir SQLite files.

    Creates reference.db with all tables. Yields the registry, then
    disposes engines and resets the module-level singleton.
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    # Pre-create reference.db so DBRegistry.__init__ succeeds
    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()

    registry = DBRegistry(settings)
    yield registry
    registry.dispose_all()
    reset_registry()


# ═══════════════════════════════════════════════════════════════════════
# FastAPI TestClient
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def test_client(tmp_data_dir: Path) -> TestClient:
    """FastAPI TestClient with a temporary data directory.

    Patches get_settings everywhere so the app + DBRegistry use the
    temp directory. Resets the DB singleton on teardown.
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    # Pre-create reference.db with tables
    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()

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


# ═══════════════════════════════════════════════════════════════════════
# Sample engine with raw variants pre-loaded
# ═══════════════════════════════════════════════════════════════════════

SEED_RAW_VARIANTS = [
    {"rsid": "rs429358", "chrom": "19", "pos": 44908684, "genotype": "TC"},
    {"rsid": "rs7412", "chrom": "19", "pos": 44908822, "genotype": "CC"},
    {"rsid": "rs1801133", "chrom": "1", "pos": 11856378, "genotype": "AG"},
    {"rsid": "rs4680", "chrom": "22", "pos": 19963748, "genotype": "AG"},
    {"rsid": "rs16947", "chrom": "22", "pos": 42522613, "genotype": "AG"},
    {"rsid": "rs3892097", "chrom": "22", "pos": 42524947, "genotype": "CC"},
    {"rsid": "rs12913832", "chrom": "15", "pos": 28365618, "genotype": "GG"},
    {"rsid": "rs7903146", "chrom": "10", "pos": 114758349, "genotype": "CT"},
    {"rsid": "rs1805007", "chrom": "16", "pos": 89919709, "genotype": "CC"},
    {"rsid": "rs12345", "chrom": "1", "pos": 100000, "genotype": "AA"},
    {"rsid": "rs4244285", "chrom": "10", "pos": 96541616, "genotype": "GA"},
]


@pytest.fixture
def sample_with_variants(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine with 10 raw_variants rows inserted.

    Uses well-known rsids that overlap with the seeded reference data
    so join/lookup tests are straightforward.
    """
    with sample_engine.begin() as conn:
        conn.execute(raw_variants.insert(), SEED_RAW_VARIANTS)
    return sample_engine
