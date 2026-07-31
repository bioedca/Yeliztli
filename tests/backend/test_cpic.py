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
import sqlite3
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

_TPMT_GUIDELINE_URL = "https://cpicpgx.org/guidelines/guideline-for-thiopurines-and-tpmt/"
_TPMT_POOR_METABOLIZER_GUIDELINES = {
    ("mercaptopurine", "Poor Metabolizer"): (
        None,
        "For malignancy: initiate therapy with drastically reduced starting doses. "
        "Reduce starting dose by 10-fold and reduce frequency to thrice weekly instead "
        "of daily (e.g. 10 mg/m2/day given 3 days/week). During therapy, adjust "
        "mercaptopurine doses based on the degree of myelosuppression and disease-specific "
        "guidelines. It usually takes at least 4-6 weeks of stable dosing to reach steady "
        "state after each dose adjustment. If myelosuppression occurs, emphasis should be "
        "on reducing mercaptopurine over other agents. For nonmalignancy: consider "
        "alternative nonthiopurine immunosuppressant therapy.",
        "A",
        _TPMT_GUIDELINE_URL,
    ),
    ("azathioprine", "Poor Metabolizer"): (
        None,
        "Consider alternative nonthiopurine immunosuppressant therapy.",
        "A",
        _TPMT_GUIDELINE_URL,
    ),
    ("thioguanine", "Poor Metabolizer"): (
        None,
        "Initiate therapy with drastically reduced starting doses. Reduce starting dose "
        "by 10-fold and reduce frequency to thrice weekly instead of daily. During therapy, "
        "adjust thioguanine doses based on degree of myelosuppression and disease-specific "
        "guidelines. It usually takes at least 4-6 weeks of stable dosing to reach steady "
        "state after each dose adjustment. If myelosuppression occurs, emphasis should be "
        "on reducing thioguanine over other agents.",
        "A",
        _TPMT_GUIDELINE_URL,
    ),
}


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

        # E1 (47) + NAT2 (*4,*5,*6,*7,*14) + CYP2B6 (*1,*4,*6,*9,*18); *4 added #1985
        assert len(rows) == 57
        assert stats.alleles_loaded == 57
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

        # 113 + 5 CYP2B6 *4-containing diplotypes (*1/*4,*4/*4,*4/*6,*4/*9,*4/*18) #1985
        assert len(rows) == 118
        assert stats.diplotypes_loaded == 118
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
            "activity_score",
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

    @pytest.mark.parametrize(
        "csv_path",
        [
            SEED_DIR / "cpic_guidelines_seed.csv",
            CPIC_DATA_DIR / "cpic_guidelines.csv",
        ],
    )
    def test_cyp2c9_phenytoin_rows_match_cpic_activity_score_matrix(self, csv_path: Path) -> None:
        with csv_path.open(newline="", encoding="utf-8") as fh:
            rows = [
                row
                for row in csv.DictReader(fh)
                if row["gene"] == "CYP2C9" and row["drug"] == "phenytoin"
            ]

        by_score = {row["activity_score"]: row for row in rows}
        assert len(rows) == 5
        assert set(by_score) == {"2.0", "1.5", "1.0", "0.5", "0.0"}
        assert {score: row["phenotype"] for score, row in by_score.items()} == {
            "2.0": "Normal Metabolizer",
            "1.5": "Intermediate Metabolizer",
            "1.0": "Intermediate Metabolizer",
            "0.5": "Poor Metabolizer",
            "0.0": "Poor Metabolizer",
        }
        assert {row["classification"] for row in rows} == {"A"}

        for score in ("2.0", "1.5"):
            recommendation = by_score[score]["recommendation"]
            assert recommendation.startswith(
                "No adjustments needed from typical dosing strategies."
            )
            assert "% less than typical maintenance dose" not in recommendation

        as_10 = by_score["1.0"]["recommendation"]
        assert as_10.startswith("For first dose, use typical initial or loading dose.")
        assert "approximately 25% less than typical maintenance dose" in as_10

        as_05 = by_score["0.5"]["recommendation"]
        assert as_05 == by_score["0.0"]["recommendation"]
        assert as_05.startswith("For first dose, use typical initial or loading dose.")
        assert "approximately 50% less than typical maintenance dose" in as_05

        for row in rows:
            recommendation = row["recommendation"]
            assert "therapeutic drug monitoring" in recommendation
            assert "HLA-B*15:02 negative test does not eliminate" in recommendation
            assert "phenytoin-induced SJS/TEN" in recommendation
            assert "alternative anticonvulsant" not in recommendation.lower()

    def test_tpmt_guideline_matrix_matches_production_seed_and_mini_fixture(self) -> None:
        """Fixtures mirror production; TPMT PM rows match the 2025 CPIC update (#2000)."""

        def csv_matrix(path: Path) -> dict[tuple[str, str], tuple[str | None, str, str, str]]:
            with path.open(newline="", encoding="utf-8") as fh:
                rows = [row for row in csv.DictReader(fh) if row["gene"] == "TPMT"]
            matrix = {
                (row["drug"], row["phenotype"]): (
                    row["activity_score"] or None,
                    row["recommendation"],
                    row["classification"],
                    row["guideline_url"],
                )
                for row in rows
            }
            assert len(rows) == len(matrix) == 9
            return matrix

        with sqlite3.connect(FIXTURES_DIR / "mini_reference.db") as conn:
            mini_rows = conn.execute(
                "SELECT drug, phenotype, activity_score, recommendation, classification, "
                "guideline_url FROM cpic_guidelines WHERE gene = 'TPMT'"
            ).fetchall()
        mini_matrix = {
            (drug, phenotype): (activity_score, recommendation, classification, guideline_url)
            for drug, phenotype, activity_score, recommendation, classification, guideline_url in (
                mini_rows
            )
        }

        production_matrix = csv_matrix(CPIC_DATA_DIR / "cpic_guidelines.csv")
        seed_matrix = csv_matrix(SEED_DIR / "cpic_guidelines_seed.csv")
        poor_metabolizer_rows = {
            key: value for key, value in production_matrix.items() if key[1] == "Poor Metabolizer"
        }

        assert len(mini_rows) == len(mini_matrix) == 9
        assert production_matrix == seed_matrix == mini_matrix
        assert poor_metabolizer_rows == _TPMT_POOR_METABOLIZER_GUIDELINES

    def test_parse_seed_file(self):
        rows, stats = parse_cpic_guidelines_csv(SEED_DIR / "cpic_guidelines_seed.csv")

        # E1 (61) + CYP2B6 efavirenz (5): Normal/IM/PM + Rapid/Ultrarapid (#1985),
        # DPYD's 6 phenotype rows re-derived to 10 AS rows (#1993) → +4, then
        # CYP2C9/phenytoin's 3 phenotype rows re-derived to 5 AS rows (#1989) → +2,
        # then TPMT thioguanine's complete NM/IM/PM set was added (#2000) → +3.
        assert len(rows) == 75
        assert stats.guidelines_loaded == 75
        assert stats.guidelines_skipped == 0

    def test_first_row_structure(self):
        rows, _ = parse_cpic_guidelines_csv(SEED_DIR / "cpic_guidelines_seed.csv")

        first = rows[0]
        assert first["gene"] == "CYP2D6"
        assert first["drug"] == "codeine"
        assert first["phenotype"] == "Normal Metabolizer"
        assert first["classification"] == "A"
        assert "cpicpgx.org" in first["guideline_url"]

    def test_cyp2b6_efavirenz_reduced_dose_rows_preserve_cpic_text(self):
        expected = {
            "Intermediate Metabolizer": (
                "Consider initiating efavirenz with decreased dose of 400 mg/day."
            ),
            "Poor Metabolizer": (
                "Consider initiating efavirenz with decreased dose of 400 or 200 mg/day."
            ),
        }

        for csv_path in (
            CPIC_DATA_DIR / "cpic_guidelines.csv",
            SEED_DIR / "cpic_guidelines_seed.csv",
        ):
            rows, _ = parse_cpic_guidelines_csv(csv_path)
            efavirenz_rows = {
                row["phenotype"]: row
                for row in rows
                if row["gene"] == "CYP2B6"
                and row["drug"] == "efavirenz"
                and row["phenotype"] in expected
            }

            assert {
                phenotype: row["recommendation"] for phenotype, row in efavirenz_rows.items()
            } == expected
            for row in efavirenz_rows.values():
                assert row["classification"] == "A"
                assert row["guideline_url"] == (
                    "https://cpicpgx.org/guidelines/"
                    "cpic-guideline-for-efavirenz-based-on-cyp2b6-genotype/"
                )

        with sqlite3.connect(FIXTURES_DIR / "mini_reference.db") as conn:
            mini_rows = conn.execute(
                "SELECT phenotype, recommendation FROM cpic_guidelines "
                "WHERE gene = 'CYP2B6' AND drug = 'efavirenz' "
                "AND phenotype IN ('Intermediate Metabolizer', 'Poor Metabolizer')"
            ).fetchall()
        assert dict(mini_rows) == expected

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
