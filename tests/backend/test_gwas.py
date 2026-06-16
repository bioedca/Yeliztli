"""Tests for the GWAS Catalog TSV loader and annotation lookup (P3-07).

Covers:
- EFO whitelist trait matching
- TSV row parsing (rsid extraction, risk allele, sample size, OR vs beta)
- Chromosome normalization
- Streaming iterator parsing with EFO filtering
- Bulk loading into SQLite via gwas_associations table
- Stream loading via load_gwas_from_iter
- Version tracking in database_versions
- Download function (mocked HTTP, error handling, partial cleanup)
- End-to-end download_and_load_gwas pipeline (mocked HTTP)
- Annotation lookup by rsids (single + multi-trait)
- Simplified trait name lookup
"""

from __future__ import annotations

import csv
import gzip
import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import sqlalchemy as sa

from backend.annotation.gwas import (
    EFO_MODULES,
    EFO_WHITELIST,
    _extract_risk_allele,
    _extract_rsid,
    _is_odds_ratio,
    _normalize_chrom,
    _parse_float,
    _parse_int,
    _parse_sample_size,
    _trait_matches_whitelist,
    download_and_load_gwas,
    download_gwas_catalog,
    iter_gwas_tsv,
    load_gwas_from_iter,
    load_gwas_into_db,
    parse_gwas_tsv,
    parse_gwas_tsv_row,
    record_gwas_version,
)
from backend.db.tables import database_versions, gwas_associations, reference_metadata

# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def reference_engine() -> sa.Engine:
    """In-memory SQLite engine with reference tables created."""
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    return engine


_GWAS_HEADER = [
    "DATE ADDED TO CATALOG",
    "PUBMEDID",
    "FIRST AUTHOR",
    "DATE",
    "JOURNAL",
    "LINK",
    "STUDY",
    "DISEASE/TRAIT",
    "INITIAL SAMPLE SIZE",
    "REPLICATION SAMPLE SIZE",
    "REGION",
    "CHR_ID",
    "CHR_POS",
    "REPORTED GENE(S)",
    "MAPPED_GENE",
    "UPSTREAM_GENE_ID",
    "DOWNSTREAM_GENE_ID",
    "SNP_GENE_IDS",
    "UPSTREAM_GENE_DISTANCE",
    "DOWNSTREAM_GENE_DISTANCE",
    "STRONGEST SNP-RISK ALLELE",
    "SNPS",
    "MERGED",
    "SNP_ID_CURRENT",
    "CONTEXT",
    "INTERGENIC",
    "RISK ALLELE FREQUENCY",
    "P-VALUE",
    "PVALUE_MLOG",
    "P-VALUE (TEXT)",
    "OR or BETA",
    "95% CI (TEXT)",
    "PLATFORM [SNPS PASSING QC]",
    "CNV",
    "MAPPED_TRAIT",
    "MAPPED_TRAIT_URI",
    "STUDY ACCESSION",
    "GENOTYPING TECHNOLOGY",
]

_GWAS_ROWS = [
    {
        "PUBMEDID": "22885922",
        "STUDY": "Morris et al. 2012",
        "DISEASE/TRAIT": "Type 2 diabetes mellitus",
        "INITIAL SAMPLE SIZE": "149,821 European ancestry individuals",
        "CHR_ID": "10",
        "CHR_POS": "114758349",
        "STRONGEST SNP-RISK ALLELE": "rs7903146-T",
        "SNPS": "rs7903146",
        "P-VALUE": "5e-120",
        "OR or BETA": "1.37",
        "95% CI (TEXT)": "[1.31-1.43]",
        "MAPPED_TRAIT": "type 2 diabetes mellitus",
    },
    {
        "PUBMEDID": "23824729",
        "STUDY": "van Meurs et al. 2013",
        "DISEASE/TRAIT": "Homocysteine levels",
        "INITIAL SAMPLE SIZE": "44,147 European ancestry individuals",
        "CHR_ID": "1",
        "CHR_POS": "11856378",
        "STRONGEST SNP-RISK ALLELE": "rs1801133-A",
        "SNPS": "rs1801133",
        "P-VALUE": "2e-50",
        "OR or BETA": "1.73",
        "95% CI (TEXT)": "[1.55-1.91] unit increase",
        "MAPPED_TRAIT": "homocysteine measurement",
    },
    {
        "PUBMEDID": "99999999",
        "STUDY": "Smith et al. 2023",
        "DISEASE/TRAIT": "Hair whorl direction",
        "INITIAL SAMPLE SIZE": "500 individuals",
        "CHR_ID": "5",
        "CHR_POS": "131424209",
        "STRONGEST SNP-RISK ALLELE": "rs9999999-G",
        "SNPS": "rs9999999",
        "P-VALUE": "3e-10",
        "OR or BETA": "1.25",
        "95% CI (TEXT)": "[1.15-1.36]",
        "MAPPED_TRAIT": "hair whorl",
    },
    {
        "PUBMEDID": "16258542",
        "STUDY": "Zubieta et al. 2003",
        "DISEASE/TRAIT": "Pain sensitivity",
        "INITIAL SAMPLE SIZE": "202 individuals",
        "CHR_ID": "22",
        "CHR_POS": "19963748",
        "STRONGEST SNP-RISK ALLELE": "rs4680-A",
        "SNPS": "rs4680",
        "P-VALUE": "3e-8",
        "OR or BETA": "1.15",
        "95% CI (TEXT)": "[1.02-1.30]",
        "MAPPED_TRAIT": "pain sensitivity measurement",
    },
    {
        "PUBMEDID": "20686565",
        "STUDY": "Teslovich et al. 2010",
        "DISEASE/TRAIT": "HDL cholesterol levels",
        "INITIAL SAMPLE SIZE": "100,184 European ancestry individuals",
        "CHR_ID": "16",
        "CHR_POS": "57015091",
        "STRONGEST SNP-RISK ALLELE": "rs708272-A",
        "SNPS": "rs708272",
        "P-VALUE": "5e-50",
        "OR or BETA": "0.82",
        "95% CI (TEXT)": "[0.72-0.92] unit increase",
        "MAPPED_TRAIT": "HDL cholesterol measurement",
    },
]


@pytest.fixture
def mini_gwas_tsv(tmp_path: Path) -> Path:
    """Create a minimal GWAS Catalog TSV fixture for testing."""
    tsv_path = tmp_path / "gwas_test.tsv"
    with open(tsv_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=_GWAS_HEADER,
            delimiter="\t",
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in _GWAS_ROWS:
            # Fill missing columns with empty strings
            full_row = {h: row.get(h, "") for h in _GWAS_HEADER}
            writer.writerow(full_row)
    return tsv_path


@pytest.fixture
def mini_gwas_tsv_gz(tmp_path: Path, mini_gwas_tsv: Path) -> Path:
    """Gzipped version of the mini GWAS TSV."""
    gz_path = tmp_path / "gwas_test.tsv.gz"
    with open(mini_gwas_tsv, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
        f_out.write(f_in.read())
    return gz_path


@pytest.fixture
def seeded_reference_engine(reference_engine: sa.Engine) -> sa.Engine:
    """Reference engine with pre-loaded GWAS seed data."""
    seed_data = [
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
            "rsid": "rs429358",
            "chrom": "19",
            "pos": 44908684,
            "trait": "Coronary artery disease",
            "p_value": 2e-15,
            "odds_ratio": 1.08,
            "beta": None,
            "risk_allele": "C",
            "pubmed_id": "26343387",
            "study": "Nikpay et al. 2015",
            "sample_size": 184305,
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
    with reference_engine.begin() as conn:
        conn.execute(gwas_associations.insert(), seed_data)
    return reference_engine


# ═══════════════════════════════════════════════════════════════════════
# Unit tests — helper functions
# ═══════════════════════════════════════════════════════════════════════


class TestNormalizeChrom:
    def test_plain_number(self):
        assert _normalize_chrom("1") == "1"
        assert _normalize_chrom("22") == "22"

    def test_chr_prefix(self):
        assert _normalize_chrom("chr1") == "1"
        assert _normalize_chrom("chrX") == "X"

    def test_sex_chromosomes(self):
        assert _normalize_chrom("X") == "X"
        assert _normalize_chrom("Y") == "Y"
        assert _normalize_chrom("MT") == "MT"

    def test_invalid(self):
        assert _normalize_chrom("23") is None
        assert _normalize_chrom("") is None
        assert _normalize_chrom("Z") is None

    def test_whitespace(self):
        assert _normalize_chrom(" 1 ") == "1"


class TestParseFloat:
    def test_valid(self):
        assert _parse_float("1.5") == 1.5
        assert _parse_float("1e-10") == 1e-10

    def test_empty_and_na(self):
        assert _parse_float("") is None
        assert _parse_float("NR") is None
        assert _parse_float("NA") is None
        assert _parse_float("-") is None
        assert _parse_float(None) is None

    def test_invalid(self):
        assert _parse_float("abc") is None


class TestParseInt:
    def test_valid(self):
        assert _parse_int("123") == 123
        assert _parse_int("1,234") == 1234

    def test_empty_and_na(self):
        assert _parse_int("") is None
        assert _parse_int("NR") is None
        assert _parse_int(None) is None

    def test_invalid(self):
        assert _parse_int("abc") is None


class TestExtractRsid:
    def test_single_rsid(self):
        assert _extract_rsid("rs429358") == "rs429358"

    def test_semicolon_separated(self):
        assert _extract_rsid("rs429358; rs7412") == "rs429358"

    def test_interaction(self):
        assert _extract_rsid("rs429358 x rs7412") == "rs429358"

    def test_no_rsid(self):
        assert _extract_rsid("") is None
        assert _extract_rsid("chr19:44908684") is None

    def test_none(self):
        assert _extract_rsid(None) is None  # type: ignore[arg-type]

    def test_case_insensitive(self):
        assert _extract_rsid("RS429358") == "rs429358"


class TestExtractRiskAllele:
    def test_standard_format(self):
        assert _extract_risk_allele("rs429358-C") == "C"
        assert _extract_risk_allele("rs429358-A") == "A"

    def test_unknown_allele(self):
        assert _extract_risk_allele("rs429358-?") is None

    def test_empty(self):
        assert _extract_risk_allele("") is None
        assert _extract_risk_allele(None) is None

    def test_no_hyphen(self):
        assert _extract_risk_allele("rs429358") is None


class TestParseSampleSize:
    def test_standard(self):
        assert _parse_sample_size("74,046 European ancestry individuals") == 74046

    def test_cases_controls(self):
        result = _parse_sample_size("1,234 cases, 5,678 controls")
        assert result == 6912

    def test_empty(self):
        assert _parse_sample_size("") is None
        assert _parse_sample_size(None) is None

    def test_no_numbers(self):
        assert _parse_sample_size("European ancestry") is None


class TestTraitMatchesWhitelist:
    def test_exact_match(self):
        assert _trait_matches_whitelist("Type 2 diabetes") is True

    def test_substring_match(self):
        assert _trait_matches_whitelist("Type 2 diabetes mellitus") is True

    def test_case_insensitive(self):
        assert _trait_matches_whitelist("VITAMIN D levels") is True

    def test_no_match(self):
        assert _trait_matches_whitelist("Hair whorl direction") is False

    def test_nutrigenomics_terms(self):
        assert _trait_matches_whitelist("Folate metabolism") is True
        assert _trait_matches_whitelist("Homocysteine levels") is True
        assert _trait_matches_whitelist("Omega-3 fatty acids") is True
        assert _trait_matches_whitelist("Lactose intolerance") is True

    def test_fitness_terms(self):
        assert _trait_matches_whitelist("Grip strength") is True
        assert _trait_matches_whitelist("Bone mineral density") is True

    def test_sleep_terms(self):
        assert _trait_matches_whitelist("Insomnia") is True
        assert _trait_matches_whitelist("Chronotype") is True

    def test_skin_terms(self):
        assert _trait_matches_whitelist("Melanoma risk") is True
        assert _trait_matches_whitelist("Hair color") is True

    def test_allergy_terms(self):
        assert _trait_matches_whitelist("Asthma risk") is True
        assert _trait_matches_whitelist("Allergic rhinitis") is True

    def test_traits_terms(self):
        assert _trait_matches_whitelist("Educational attainment") is True
        assert _trait_matches_whitelist("Neuroticism") is True
        assert _trait_matches_whitelist("Pain sensitivity measurement") is True


class TestIsOddsRatio:
    def test_ci_with_brackets(self):
        assert _is_odds_ratio("[1.2-1.5]", 1.3) is True

    def test_ci_with_unit_increase(self):
        assert _is_odds_ratio("[0.5-1.2] unit increase", 0.8) is False

    def test_no_ci(self):
        assert _is_odds_ratio(None, 1.5) is True  # between 0.1 and 20

    def test_negative_value_is_beta(self):
        assert _is_odds_ratio(None, -0.5) is False

    def test_none_value(self):
        assert _is_odds_ratio(None, None) is True  # default


class TestEFOWhitelist:
    def test_whitelist_not_empty(self):
        assert len(EFO_WHITELIST) > 0

    def test_all_modules_present(self):
        expected_modules = {
            "nutrigenomics",
            "fitness",
            "sleep",
            "skin",
            "allergy",
            "methylation",
            "traits",
        }
        assert set(EFO_MODULES.keys()) == expected_modules

    def test_whitelist_is_union(self):
        union = frozenset()
        for terms in EFO_MODULES.values():
            union = union | terms
        assert union == EFO_WHITELIST


# ═══════════════════════════════════════════════════════════════════════
# Unit tests — TSV row parsing
# ═══════════════════════════════════════════════════════════════════════


class TestParseGwasTsvRow:
    def test_valid_row_with_or(self):
        row = {
            "SNPS": "rs7903146",
            "DISEASE/TRAIT": "Type 2 diabetes",
            "MAPPED_TRAIT": "type 2 diabetes mellitus",
            "CHR_ID": "10",
            "CHR_POS": "114758349",
            "P-VALUE": "5e-120",
            "OR or BETA": "1.37",
            "95% CI (TEXT)": "[1.31-1.43]",
            "STRONGEST SNP-RISK ALLELE": "rs7903146-T",
            "PUBMEDID": "17463246",
            "STUDY": "Zeggini et al. 2007",
            "INITIAL SAMPLE SIZE": "10,128 European ancestry individuals",
        }
        result, skip = parse_gwas_tsv_row(row)
        assert skip is None
        assert result is not None
        assert result["rsid"] == "rs7903146"
        assert result["chrom"] == "10"
        assert result["pos"] == 114758349
        assert result["trait"] == "type 2 diabetes mellitus"
        assert result["p_value"] == 5e-120
        assert result["odds_ratio"] == 1.37
        assert result["beta"] is None
        assert result["risk_allele"] == "T"
        assert result["pubmed_id"] == "17463246"
        assert result["sample_size"] == 10128

    def test_valid_row_with_beta(self):
        row = {
            "SNPS": "rs1801133",
            "DISEASE/TRAIT": "Homocysteine levels",
            "MAPPED_TRAIT": "homocysteine measurement",
            "CHR_ID": "1",
            "CHR_POS": "11856378",
            "P-VALUE": "2e-50",
            "OR or BETA": "1.73",
            "95% CI (TEXT)": "[1.55-1.91] unit increase",
            "STRONGEST SNP-RISK ALLELE": "rs1801133-A",
            "PUBMEDID": "23824729",
            "STUDY": "van Meurs et al. 2013",
            "INITIAL SAMPLE SIZE": "44,147 individuals",
        }
        result, skip = parse_gwas_tsv_row(row)
        assert skip is None
        assert result is not None
        assert result["rsid"] == "rs1801133"
        assert result["beta"] == 1.73
        assert result["odds_ratio"] is None

    def test_skip_no_rsid(self):
        row = {
            "SNPS": "chr19:44908684",
            "DISEASE/TRAIT": "Alzheimer disease",
            "MAPPED_TRAIT": "Alzheimer disease",
        }
        result, skip = parse_gwas_tsv_row(row)
        assert result is None
        assert skip == "no_rsid"

    def test_skip_no_trait(self):
        row = {
            "SNPS": "rs429358",
            "DISEASE/TRAIT": "",
            "MAPPED_TRAIT": "",
        }
        result, skip = parse_gwas_tsv_row(row)
        assert result is None
        assert skip == "no_trait"

    def test_skip_efo_filter(self):
        row = {
            "SNPS": "rs9999999",
            "DISEASE/TRAIT": "Hair whorl direction",
            "MAPPED_TRAIT": "hair whorl",
            "CHR_ID": "5",
            "CHR_POS": "131424209",
            "P-VALUE": "3e-10",
            "OR or BETA": "1.25",
            "95% CI (TEXT)": "[1.15-1.36]",
            "STRONGEST SNP-RISK ALLELE": "rs9999999-G",
            "PUBMEDID": "99999999",
            "STUDY": "Smith et al. 2023",
            "INITIAL SAMPLE SIZE": "500 individuals",
        }
        result, skip = parse_gwas_tsv_row(row)
        assert result is None
        assert skip == "efo_filter"

    def test_mapped_trait_preferred(self):
        """MAPPED_TRAIT should be used over DISEASE/TRAIT when available."""
        row = {
            "SNPS": "rs7903146",
            "DISEASE/TRAIT": "T2D",
            "MAPPED_TRAIT": "type 2 diabetes mellitus",
            "CHR_ID": "10",
            "CHR_POS": "114758349",
            "P-VALUE": "1e-10",
            "OR or BETA": "",
            "95% CI (TEXT)": "",
            "STRONGEST SNP-RISK ALLELE": "",
            "PUBMEDID": "12345",
            "STUDY": "Test",
            "INITIAL SAMPLE SIZE": "",
        }
        result, skip = parse_gwas_tsv_row(row)
        assert result is not None
        assert result["trait"] == "type 2 diabetes mellitus"

    def test_missing_optional_fields(self):
        """Row with only required fields should still parse."""
        row = {
            "SNPS": "rs429358",
            "DISEASE/TRAIT": "Type 2 diabetes",
            "MAPPED_TRAIT": "",
            "CHR_ID": "",
            "CHR_POS": "",
            "P-VALUE": "",
            "OR or BETA": "",
            "95% CI (TEXT)": "",
            "STRONGEST SNP-RISK ALLELE": "",
            "PUBMEDID": "",
            "STUDY": "",
            "INITIAL SAMPLE SIZE": "",
        }
        result, skip = parse_gwas_tsv_row(row)
        assert result is not None
        assert result["rsid"] == "rs429358"
        assert result["trait"] == "Type 2 diabetes"
        assert result["chrom"] is None
        assert result["pos"] is None
        assert result["p_value"] is None


# ═══════════════════════════════════════════════════════════════════════
# Integration tests — TSV parsing
# ═══════════════════════════════════════════════════════════════════════


class TestIterGwasTsv:
    def test_parse_mini_fixture(self, mini_gwas_tsv: Path):
        """Parse the mini GWAS TSV and verify filtering."""
        rows, stats = parse_gwas_tsv(mini_gwas_tsv)
        # 5 lines total: 4 match EFO filter, 1 filtered out (hair whorl)
        assert stats.total_lines == 5
        assert stats.associations_loaded == 4
        assert stats.skipped_efo_filter == 1
        assert len(rows) == 4

    def test_parse_gzipped(self, mini_gwas_tsv_gz: Path):
        """Parse the gzipped mini GWAS TSV."""
        rows, stats = parse_gwas_tsv(mini_gwas_tsv_gz)
        assert stats.associations_loaded == 4

    def test_progress_callback(self, mini_gwas_tsv: Path):
        """Progress callback should be called."""
        # With only 5 lines, won't trigger the 10k modulo callback,
        # but should still complete without error
        callback = MagicMock()
        rows, stats = parse_gwas_tsv(mini_gwas_tsv, progress_callback=callback)
        assert stats.associations_loaded == 4

    def test_specific_rows_parsed(self, mini_gwas_tsv: Path):
        """Verify specific rows are correctly parsed."""
        rows, stats = parse_gwas_tsv(mini_gwas_tsv)

        # Find the T2D row
        t2d = [r for r in rows if "type 2 diabetes" in r["trait"].lower()]
        assert len(t2d) == 1
        assert t2d[0]["rsid"] == "rs7903146"
        assert t2d[0]["odds_ratio"] == 1.37
        assert t2d[0]["risk_allele"] == "T"

        # Find the pain sensitivity row
        pain = [r for r in rows if "pain" in r["trait"].lower()]
        assert len(pain) == 1
        assert pain[0]["rsid"] == "rs4680"


# ═══════════════════════════════════════════════════════════════════════
# Integration tests — Database loading
# ═══════════════════════════════════════════════════════════════════════


class TestLoadGwasIntoDb:
    def test_load_rows(self, reference_engine: sa.Engine):
        """Load parsed GWAS rows into the database."""
        rows = [
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
        stats = load_gwas_into_db(rows, reference_engine)
        assert stats.associations_loaded == 2

        with reference_engine.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(gwas_associations)
            ).scalar()
            assert count == 2

    def test_clear_existing(self, reference_engine: sa.Engine):
        """Loading with clear_existing=True should replace data."""
        row1 = [
            {
                "rsid": "rs1",
                "chrom": "1",
                "pos": 100,
                "trait": "Type 2 diabetes",
                "p_value": 1e-10,
                "odds_ratio": 1.5,
                "beta": None,
                "risk_allele": "A",
                "pubmed_id": "123",
                "study": "Test",
                "sample_size": 100,
            }
        ]
        row2 = [
            {
                "rsid": "rs2",
                "chrom": "2",
                "pos": 200,
                "trait": "Obesity",
                "p_value": 1e-8,
                "odds_ratio": 1.2,
                "beta": None,
                "risk_allele": "G",
                "pubmed_id": "456",
                "study": "Test2",
                "sample_size": 200,
            }
        ]

        load_gwas_into_db(row1, reference_engine)
        load_gwas_into_db(row2, reference_engine, clear_existing=True)

        with reference_engine.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(gwas_associations)
            ).scalar()
            assert count == 1

    def test_append_existing(self, reference_engine: sa.Engine):
        """Loading with clear_existing=False should append data."""
        row1 = [
            {
                "rsid": "rs1",
                "chrom": "1",
                "pos": 100,
                "trait": "Type 2 diabetes",
                "p_value": 1e-10,
                "odds_ratio": 1.5,
                "beta": None,
                "risk_allele": "A",
                "pubmed_id": "123",
                "study": "Test",
                "sample_size": 100,
            }
        ]
        row2 = [
            {
                "rsid": "rs2",
                "chrom": "2",
                "pos": 200,
                "trait": "Obesity",
                "p_value": 1e-8,
                "odds_ratio": 1.2,
                "beta": None,
                "risk_allele": "G",
                "pubmed_id": "456",
                "study": "Test2",
                "sample_size": 200,
            }
        ]

        load_gwas_into_db(row1, reference_engine)
        load_gwas_into_db(row2, reference_engine, clear_existing=False)

        with reference_engine.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(gwas_associations)
            ).scalar()
            assert count == 2

    def test_load_from_tsv(self, reference_engine: sa.Engine, mini_gwas_tsv: Path):
        """Load directly from TSV file parsing."""
        rows, stats = parse_gwas_tsv(mini_gwas_tsv)
        load_gwas_into_db(rows, reference_engine, stats=stats)

        with reference_engine.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(gwas_associations)
            ).scalar()
            assert count == 4


class TestLoadGwasFromIter:
    def test_stream_load(self, reference_engine: sa.Engine, mini_gwas_tsv: Path):
        """Stream-load from TSV iterator."""
        row_iter = iter_gwas_tsv(mini_gwas_tsv)
        stats = load_gwas_from_iter(row_iter, reference_engine)

        assert stats.associations_loaded == 4

        with reference_engine.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(gwas_associations)
            ).scalar()
            assert count == 4


class TestRecordGwasVersion:
    def test_insert_version(self, reference_engine: sa.Engine):
        """Insert a GWAS version record."""
        record_gwas_version(
            reference_engine,
            version="20240101",
            file_path="/tmp/gwas.tsv",
            file_size_bytes=1000,
            checksum="abc123",
        )
        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "gwas_catalog")
            ).first()
            assert row is not None
            assert row.version == "20240101"
            assert row.checksum_sha256 == "abc123"

    def test_update_version(self, reference_engine: sa.Engine):
        """Updating should overwrite existing version."""
        record_gwas_version(reference_engine, version="v1")
        record_gwas_version(reference_engine, version="v2")

        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "gwas_catalog")
            ).first()
            assert row.version == "v2"


# ═══════════════════════════════════════════════════════════════════════
# Integration tests — Download (mocked HTTP)
# ═══════════════════════════════════════════════════════════════════════


def _fake_stream_download(*, content: bytes = b"", headers=None, exc: BaseException | None = None):
    """Fake ``stream_download`` writing ``content`` (or raising ``exc``).

    Resume/retry behaviour is covered in ``test_http_download.py``; these tests
    verify the wrapper's ZIP download → extract → cleanup flow.
    """
    from backend.annotation.http_download import DownloadOutcome

    def _impl(url, tmp_path, *, progress_callback=None, **kwargs):
        if exc is not None:
            raise exc
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_bytes(content)
        if progress_callback is not None:
            progress_callback(len(content), len(content))
        return DownloadOutcome(
            path=tmp_path,
            total_bytes=len(content),
            expected_total=len(content),
            headers=httpx.Headers(headers or {}),  # case-insensitive, like the real one
            attempts=1,
            resumed=False,
        )

    return _impl


class TestDownloadGwasCatalog:
    def test_download_success(self, tmp_path: Path):
        """Download should write ZIP, extract TSV, and clean up ZIP."""
        tsv_content = b"SNPS\ttrait\nrs429358\tAlzheimer disease\n"

        # Build a ZIP archive containing a TSV
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr("gwas-catalog-download-associations-alt-full.tsv", tsv_content)
        zip_bytes = zip_buf.getvalue()

        with patch(
            "backend.annotation.gwas.stream_download",
            _fake_stream_download(content=zip_bytes),
        ):
            result = download_gwas_catalog(tmp_path, url="http://test/gwas.zip")

        assert result.exists()
        assert result.name == "gwas_catalog_associations.tsv"
        assert result.read_bytes() == tsv_content
        # ZIP should be cleaned up
        assert not (tmp_path / "gwas_catalog_associations.zip").exists()

    def test_download_preserves_partial_for_resume(self, tmp_path: Path):
        """A failed download preserves the partial ZIP for cross-run resume (#756)."""
        partial = tmp_path / "gwas_catalog_associations.zip.tmp"
        partial.write_bytes(b"partial-from-prior-run")
        err = httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock())
        with (
            patch("backend.annotation.gwas.stream_download", _fake_stream_download(exc=err)),
            pytest.raises(httpx.HTTPStatusError),
        ):
            download_gwas_catalog(tmp_path, url="http://test/gwas.zip")

        assert partial.exists()  # preserve-for-resume, not deleted on failure

    def test_wires_resumable_and_validator_sidecar(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """download_gwas_catalog opts into cross-run resume + validator sidecar (#756)."""
        from types import SimpleNamespace

        zip_tmp = tmp_path / "gwas_catalog_associations.zip.tmp"
        sidecar = tmp_path / "gwas_catalog_associations.zip.tmp.validator"
        zip_tmp.write_bytes(b"partial")
        sidecar.write_text('"etag-v1"', encoding="utf-8")
        captured: dict = {}

        def fake(url, tmp_path_arg, **kwargs):
            captured.update(kwargs)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("assoc.tsv", b"SNPS\ttrait\nrs1\tx\n")
            tmp_path_arg.write_bytes(buf.getvalue())
            return SimpleNamespace(headers={})

        monkeypatch.setattr("backend.annotation.gwas.stream_download", fake)
        result = download_gwas_catalog(tmp_path, url="http://test/gwas.zip")

        assert result.name == "gwas_catalog_associations.tsv"
        assert captured["resumable"] is True
        assert captured["validator"] == '"etag-v1"'
        assert callable(captured["on_validator"])
        assert not sidecar.exists()  # cleared on success

    def test_progress_callback(self, tmp_path: Path):
        """Progress callback should be called with byte counts."""
        # Build a ZIP containing a small TSV
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr("data.tsv", b"data\n")
        zip_bytes = zip_buf.getvalue()
        callback = MagicMock()

        with patch(
            "backend.annotation.gwas.stream_download",
            _fake_stream_download(content=zip_bytes),
        ):
            download_gwas_catalog(
                tmp_path,
                url="http://test/gwas.zip",
                progress_callback=callback,
            )

        callback.assert_called()


class TestDownloadAndLoadGwas:
    def test_full_pipeline(self, reference_engine: sa.Engine, mini_gwas_tsv: Path):
        """Full download + parse + load pipeline (mocked download)."""
        with (
            patch(
                "backend.annotation.gwas.download_gwas_catalog",
                return_value=mini_gwas_tsv,
            ),
            patch(
                "backend.annotation.gwas._compute_sha256",
                return_value="fake_sha256",
            ),
        ):
            stats = download_and_load_gwas(
                reference_engine,
                mini_gwas_tsv.parent,
                url="http://test/gwas.tsv",
            )

        assert stats.associations_loaded == 4
        assert stats.sha256 == "fake_sha256"

        # Verify data loaded
        with reference_engine.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(gwas_associations)
            ).scalar()
            assert count == 4

        # Verify version recorded
        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "gwas_catalog")
            ).first()
            assert row is not None
            assert row.checksum_sha256 == "fake_sha256"


class TestGwasSeedVitaminDDirection:
    """Guard the CYP2R1 rs10741657 GWAS seed direction (#335 / #340).

    The seed row previously encoded risk_allele=A with beta=-1.05 ("A lowers
    vitamin D"), the inverse of the corrected #242/#332 direction and of the
    weight of evidence (Duan 2018, PMID 30120973: risk-allele G lowers 25(OH)D;
    rs10741657 is A/G non-palindromic). The GWAS Catalog reports the A allele
    RAISING 'vitamin D amount'. Convention in gwas_seed.csv: beta is the effect of
    risk_allele on the trait, so for risk_allele=A on a vitamin-D-LEVELS trait the
    beta must be POSITIVE (A raises 25(OH)D). Lock it so it cannot re-invert.
    """

    _SEED = Path(__file__).resolve().parent.parent / "fixtures" / "seed_csvs" / "gwas_seed.csv"

    def _row(self, rsid: str) -> dict:
        with open(self._SEED, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["rsid"] == rsid:
                    return row
        pytest.fail(f"{rsid} not found in gwas_seed.csv")

    def test_rs10741657_seed_encodes_A_raises_vitamin_d(self) -> None:
        row = self._row("rs10741657")
        assert "vitamin d" in row["trait"].lower()
        assert row["risk_allele"] == "A"
        beta = float(row["beta"])
        # A is the higher-25(OH)D allele -> beta must be positive (G is the
        # lower-vitamin-D / deficiency-risk allele, consistent with the panel).
        assert beta > 0, (
            f"rs10741657 seed beta {beta} encodes A-lowers-vitamin-D (inverted); "
            "G is the lower-25(OH)D allele (#242/#335)"
        )


class TestGwasZeroRowGuard:
    """A destructive clear with 0 rows to load must never wipe gwas_associations.

    Mirrors the cpic/clingen guard: a zero-row external fetch/parse (empty or
    truncated download, an upstream format change that drops every row) must not
    silently empty the curated table (#753).
    """

    @staticmethod
    def _count(engine: sa.Engine) -> int:
        with engine.connect() as conn:
            return conn.execute(sa.select(sa.func.count()).select_from(gwas_associations)).scalar()

    def test_load_into_db_refuses_to_wipe(self, seeded_reference_engine: sa.Engine):
        assert self._count(seeded_reference_engine) == 5
        with pytest.raises(ValueError, match="0 rows"):
            load_gwas_into_db([], seeded_reference_engine, clear_existing=True)
        assert self._count(seeded_reference_engine) == 5  # curated rows preserved

    def test_load_into_db_empty_no_clear_is_noop(self, seeded_reference_engine: sa.Engine):
        # A non-destructive empty load is a safe no-op (no raise, no wipe).
        load_gwas_into_db([], seeded_reference_engine, clear_existing=False)
        assert self._count(seeded_reference_engine) == 5

    def test_load_from_iter_refuses_to_wipe(self, seeded_reference_engine: sa.Engine):
        # The streaming loader peeks the first batch before any DELETE.
        with pytest.raises(ValueError, match="0 rows"):
            load_gwas_from_iter(iter([]), seeded_reference_engine, clear_existing=True)
        assert self._count(seeded_reference_engine) == 5

    def test_load_from_iter_empty_no_clear_is_noop(self, seeded_reference_engine: sa.Engine):
        load_gwas_from_iter(iter([]), seeded_reference_engine, clear_existing=False)
        assert self._count(seeded_reference_engine) == 5
