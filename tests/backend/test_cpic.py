"""Tests for the CPIC data loader (P3-01 / Step 64).

Covers:
- CSV parsing for allele definitions, diplotype→phenotype, and guidelines
- Edge cases: missing fields, malformed JSON, empty files
- Bulk loading into SQLite via three CPIC tables
- Version tracking in database_versions
- Full pipeline via load_cpic_from_csvs
- Lookup functions: by gene, by rsid, by gene-drug pair
- Lookup with seeded fixture data
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.annotation.cpic import (
    CPIC_DATA_DIR,
    CPIC_GENES,
    _parse_float,
    load_cpic_from_csvs,
    load_cpic_into_db,
    parse_cpic_alleles_csv,
    parse_cpic_diplotypes_csv,
    parse_cpic_guidelines_csv,
    record_cpic_version,
)
from backend.db.tables import (
    cpic_alleles,
    cpic_diplotypes,
    cpic_guidelines,
    database_versions,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SEED_DIR = FIXTURES_DIR / "seed_csvs"


# ═══════════════════════════════════════════════════════════════════════
# Unit tests — helper functions
# ═══════════════════════════════════════════════════════════════════════


class TestParseFloat:
    def test_valid_float(self):
        assert _parse_float("1.5") == 1.5

    def test_integer_string(self):
        assert _parse_float("2") == 2.0

    def test_zero(self):
        assert _parse_float("0.0") == 0.0

    def test_empty_string(self):
        assert _parse_float("") is None

    def test_whitespace(self):
        assert _parse_float("  ") is None

    def test_invalid(self):
        assert _parse_float("abc") is None

    def test_none(self):
        assert _parse_float(None) is None


# ═══════════════════════════════════════════════════════════════════════
# CSV parsing tests — allele definitions
# ═══════════════════════════════════════════════════════════════════════


class TestParseAllelesCSV:
    def test_parse_seed_file(self):
        rows, stats = parse_cpic_alleles_csv(SEED_DIR / "cpic_alleles_seed.csv")

        assert len(rows) == 56  # E1 (47) + NAT2 (*4,*5,*6,*7,*14) + CYP2B6 (*1,*6,*9,*18)
        assert stats.alleles_loaded == 56
        assert stats.alleles_skipped == 0
        assert "CYP2D6" in stats.genes_found
        assert "CYP2C19" in stats.genes_found

    def test_first_row_structure(self):
        rows, _ = parse_cpic_alleles_csv(SEED_DIR / "cpic_alleles_seed.csv")

        first = rows[0]
        assert first["gene"] == "CYP2D6"
        assert first["allele_name"] == "*1"
        assert first["defining_variants"] == "[]"
        assert first["function"] == "Normal function"
        assert first["activity_score"] == 1.0

    def test_row_with_defining_variants(self):
        rows, _ = parse_cpic_alleles_csv(SEED_DIR / "cpic_alleles_seed.csv")

        # *2 has rs16947
        star2 = next(r for r in rows if r["allele_name"] == "*2" and r["gene"] == "CYP2D6")
        assert (
            '"rsid":"rs16947"' in star2["defining_variants"]
            or '"rsid": "rs16947"' in star2["defining_variants"]
        )

    def test_empty_csv(self, tmp_path: Path):
        csv_path = tmp_path / "empty.csv"
        csv_path.write_text("gene,allele_name,defining_variants,function,activity_score\n")

        rows, stats = parse_cpic_alleles_csv(csv_path)
        assert len(rows) == 0
        assert stats.alleles_loaded == 0

    def test_missing_gene_skipped(self, tmp_path: Path):
        csv_path = tmp_path / "bad.csv"
        csv_path.write_text(
            "gene,allele_name,defining_variants,function,activity_score\n"
            ",*1,[],Normal function,1.0\n"
        )

        rows, stats = parse_cpic_alleles_csv(csv_path)
        assert len(rows) == 0
        assert stats.alleles_skipped == 1

    def test_malformed_json_variants(self, tmp_path: Path):
        csv_path = tmp_path / "bad_json.csv"
        csv_path.write_text(
            "gene,allele_name,defining_variants,function,activity_score\n"
            "CYP2D6,*1,{not valid json},Normal function,1.0\n"
        )

        rows, stats = parse_cpic_alleles_csv(csv_path)
        assert len(rows) == 1
        assert rows[0]["defining_variants"] == "[]"  # Falls back to empty


# ═══════════════════════════════════════════════════════════════════════
# CSV parsing tests — diplotypes
# ═══════════════════════════════════════════════════════════════════════


class TestParseDiplotypesCSV:
    def test_parse_seed_file(self):
        rows, stats = parse_cpic_diplotypes_csv(SEED_DIR / "cpic_diplotypes_seed.csv")

        assert len(rows) == 113  # 109 + 4 CYP2B6*18-containing diplotypes (issue #42)
        assert stats.diplotypes_loaded == 113
        assert stats.diplotypes_skipped == 0

    def test_first_row_structure(self):
        rows, _ = parse_cpic_diplotypes_csv(SEED_DIR / "cpic_diplotypes_seed.csv")

        first = rows[0]
        assert first["gene"] == "CYP2D6"
        assert first["diplotype"] == "*1/*1"
        assert first["phenotype"] == "Normal Metabolizer"
        assert first["ehr_notation"] == "CYP2D6 Normal Metabolizer"
        assert first["activity_score"] == 2.0

    def test_empty_csv(self, tmp_path: Path):
        csv_path = tmp_path / "empty.csv"
        csv_path.write_text("gene,diplotype,phenotype,ehr_notation,activity_score\n")

        rows, stats = parse_cpic_diplotypes_csv(csv_path)
        assert len(rows) == 0

    def test_missing_phenotype_skipped(self, tmp_path: Path):
        csv_path = tmp_path / "bad.csv"
        csv_path.write_text(
            "gene,diplotype,phenotype,ehr_notation,activity_score\n"
            "CYP2D6,*1/*1,,CYP2D6 Normal,2.0\n"
        )

        rows, stats = parse_cpic_diplotypes_csv(csv_path)
        assert len(rows) == 0
        assert stats.diplotypes_skipped == 1


# ═══════════════════════════════════════════════════════════════════════
# CSV parsing tests — guidelines
# ═══════════════════════════════════════════════════════════════════════


class TestParseGuidelinesCSV:
    @pytest.mark.parametrize(
        "csv_path",
        [
            SEED_DIR / "cpic_guidelines_seed.csv",
            CPIC_DATA_DIR / "cpic_guidelines.csv",
        ],
    )
    def test_guideline_csv_column_integrity(self, csv_path: Path):
        expected_columns = [
            "gene",
            "drug",
            "phenotype",
            "recommendation",
            "classification",
            "guideline_url",
        ]
        valid_classifications = {"A", "B", "C", "D"}
        errors: list[str] = []

        with csv_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            assert reader.fieldnames == expected_columns

            for line_number, row in enumerate(reader, start=2):
                if row.get(None):
                    errors.append(f"line {line_number}: extra fields {row[None]!r}")

                classification = (row.get("classification") or "").strip()
                if classification not in valid_classifications:
                    errors.append(f"line {line_number}: invalid classification {classification!r}")

                guideline_url = (row.get("guideline_url") or "").strip()
                if not guideline_url.startswith(("http://", "https://")):
                    errors.append(f"line {line_number}: invalid guideline_url {guideline_url!r}")

        assert not errors, "\n".join(errors)

    def test_parse_seed_file(self):
        rows, stats = parse_cpic_guidelines_csv(SEED_DIR / "cpic_guidelines_seed.csv")

        assert len(rows) == 64  # E1 (61) + CYP2B6 efavirenz (3) (SW-E1b)
        assert stats.guidelines_loaded == 64
        assert stats.guidelines_skipped == 0

    def test_first_row_structure(self):
        rows, _ = parse_cpic_guidelines_csv(SEED_DIR / "cpic_guidelines_seed.csv")

        first = rows[0]
        assert first["gene"] == "CYP2D6"
        assert first["drug"] == "codeine"
        assert first["phenotype"] == "Normal Metabolizer"
        assert first["classification"] == "A"
        assert "cpicpgx.org" in first["guideline_url"]

    def test_cyp2b6_efavirenz_poor_metabolizer_row_preserves_fields(self):
        rows, _ = parse_cpic_guidelines_csv(SEED_DIR / "cpic_guidelines_seed.csv")

        row = next(
            row
            for row in rows
            if row["gene"] == "CYP2B6"
            and row["drug"] == "efavirenz"
            and row["phenotype"] == "Poor Metabolizer"
        )

        assert row["recommendation"] == (
            "Consider initiating at a decreased dose (e.g., 400 mg/day); "
            "higher plasma exposure raises CNS-toxicity risk."
        )
        assert row["classification"] == "A"
        assert row["guideline_url"] == (
            "https://cpicpgx.org/guidelines/cpic-guideline-for-efavirenz-based-on-cyp2b6-genotype/"
        )

    def test_empty_csv(self, tmp_path: Path):
        csv_path = tmp_path / "empty.csv"
        csv_path.write_text("gene,drug,phenotype,recommendation,classification,guideline_url\n")

        rows, stats = parse_cpic_guidelines_csv(csv_path)
        assert len(rows) == 0

    def test_missing_drug_skipped(self, tmp_path: Path):
        csv_path = tmp_path / "bad.csv"
        csv_path.write_text(
            "gene,drug,phenotype,recommendation,classification,guideline_url\n"
            "CYP2D6,,Normal Metabolizer,Use standard dosing,A,\n"
        )

        rows, stats = parse_cpic_guidelines_csv(csv_path)
        assert len(rows) == 0
        assert stats.guidelines_skipped == 1


# ═══════════════════════════════════════════════════════════════════════
# Database loading tests
# ═══════════════════════════════════════════════════════════════════════


class TestLoadCPICIntoDB:
    def test_load_all_tables(self, reference_engine: sa.Engine):
        allele_rows = [
            {
                "gene": "CYP2D6",
                "allele_name": "*1",
                "defining_variants": "[]",
                "function": "Normal function",
                "activity_score": 1.0,
            },
        ]
        diplotype_rows = [
            {
                "gene": "CYP2D6",
                "diplotype": "*1/*1",
                "phenotype": "Normal Metabolizer",
                "ehr_notation": "CYP2D6 Normal Metabolizer",
                "activity_score": 2.0,
            },
        ]
        guideline_rows = [
            {
                "gene": "CYP2D6",
                "drug": "codeine",
                "phenotype": "Normal Metabolizer",
                "recommendation": "Use standard dosing.",
                "classification": "A",
                "guideline_url": "https://cpicpgx.org/",
            },
        ]

        stats = load_cpic_into_db(allele_rows, diplotype_rows, guideline_rows, reference_engine)

        assert stats.alleles_loaded == 1
        assert stats.diplotypes_loaded == 1
        assert stats.guidelines_loaded == 1
        assert "CYP2D6" in stats.genes_found

        # Verify data in database
        with reference_engine.connect() as conn:
            allele_count = conn.execute(
                sa.select(sa.func.count()).select_from(cpic_alleles)
            ).scalar()
            assert allele_count == 1

            diplo_count = conn.execute(
                sa.select(sa.func.count()).select_from(cpic_diplotypes)
            ).scalar()
            assert diplo_count == 1

            guide_count = conn.execute(
                sa.select(sa.func.count()).select_from(cpic_guidelines)
            ).scalar()
            assert guide_count == 1

    def test_clear_existing_replaces(self, reference_engine: sa.Engine):
        row = [
            {
                "gene": "CYP2D6",
                "allele_name": "*1",
                "defining_variants": "[]",
                "function": "Normal function",
                "activity_score": 1.0,
            }
        ]

        load_cpic_into_db(row, [], [], reference_engine)
        load_cpic_into_db(row, [], [], reference_engine, clear_existing=True)

        with reference_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(cpic_alleles)).scalar()
            assert count == 1  # Not 2

    def test_empty_load_refuses_to_clear(self, reference_engine: sa.Engine):
        """A clear_existing load with no rows must NOT wipe the CPIC tables."""
        row = [
            {
                "gene": "CYP2D6",
                "allele_name": "*1",
                "defining_variants": "[]",
                "function": "Normal function",
                "activity_score": 1.0,
            }
        ]
        load_cpic_into_db(row, [], [], reference_engine)
        with pytest.raises(ValueError, match="0 rows"):
            load_cpic_into_db([], [], [], reference_engine, clear_existing=True)
        with reference_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(cpic_alleles)).scalar()
            assert count == 1  # untouched

    def test_no_clear_appends(self, reference_engine: sa.Engine):
        row = [
            {
                "gene": "CYP2D6",
                "allele_name": "*1",
                "defining_variants": "[]",
                "function": "Normal function",
                "activity_score": 1.0,
            }
        ]

        load_cpic_into_db(row, [], [], reference_engine)
        load_cpic_into_db(row, [], [], reference_engine, clear_existing=False)

        with reference_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(cpic_alleles)).scalar()
            assert count == 2

    def test_load_seed_csvs(self, reference_engine: sa.Engine):
        """Load the full seed CSV files into the database."""
        allele_rows, _ = parse_cpic_alleles_csv(SEED_DIR / "cpic_alleles_seed.csv")
        diplotype_rows, _ = parse_cpic_diplotypes_csv(SEED_DIR / "cpic_diplotypes_seed.csv")
        guideline_rows, _ = parse_cpic_guidelines_csv(SEED_DIR / "cpic_guidelines_seed.csv")

        stats = load_cpic_into_db(allele_rows, diplotype_rows, guideline_rows, reference_engine)

        assert stats.alleles_loaded == len(allele_rows)
        assert stats.diplotypes_loaded == len(diplotype_rows)
        assert stats.guidelines_loaded == len(guideline_rows)


class TestRecordCPICVersion:
    def test_insert_new_version(self, reference_engine: sa.Engine):
        record_cpic_version(reference_engine, version="20260301")

        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "cpic")
            ).first()
            assert row is not None
            assert row.version == "20260301"

    def test_update_existing_version(self, reference_engine: sa.Engine):
        record_cpic_version(reference_engine, version="20260301")
        record_cpic_version(reference_engine, version="20260315", checksum="abc123")

        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "cpic")
            ).first()
            assert row.version == "20260315"
            assert row.checksum_sha256 == "abc123"


class TestLoadCPICFromCSVs:
    def test_full_pipeline(self, reference_engine: sa.Engine):
        stats = load_cpic_from_csvs(
            SEED_DIR / "cpic_alleles_seed.csv",
            SEED_DIR / "cpic_diplotypes_seed.csv",
            SEED_DIR / "cpic_guidelines_seed.csv",
            reference_engine,
        )

        assert stats.alleles_loaded > 0
        assert stats.diplotypes_loaded > 0
        assert stats.guidelines_loaded > 0
        assert stats.sha256 is not None
        assert stats.version is not None

        # Verify version recorded
        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "cpic")
            ).first()
            assert row is not None


# ═══════════════════════════════════════════════════════════════════════
# Constants / module-level tests
# ═══════════════════════════════════════════════════════════════════════


class TestCPICGenes:
    def test_required_genes_present(self):
        """All PRD-specified genes are in the CPIC_GENES set."""
        required = {"CYP2D6", "CYP2C19", "CYP2C9", "SLCO1B1", "DPYD", "TPMT"}
        assert required.issubset(CPIC_GENES)

    def test_is_frozenset(self):
        assert isinstance(CPIC_GENES, frozenset)
