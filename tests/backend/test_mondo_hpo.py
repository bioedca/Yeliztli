"""Tests for MONDO/HPO gene-phenotype loader (P2-14).

Covers:
- T2-13: MONDO/HPO lookup returns correct phenotype for BRCA1 gene.
- CSV seed loading into gene_phenotype table
- Lookup by gene symbol (single and batch)
- Source filtering (mondo_hpo vs omim)
- Empty input handling
- HPO terms JSON parsing
- Inheritance pattern extraction
- Version recording in database_versions
- LoadStats dataclass
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from backend.annotation.mondo_hpo import (
    _INHERITANCE_MAP,
    GenePhenotypeRecord,
    HpoTerm,
    LoadStats,
    _extract_gene_symbol_from_subject,
    _records_to_rows,
    decode_hpo_terms,
    load_mondo_hpo_from_csv,
    load_mondo_hpo_rows,
    lookup_gene_phenotypes,
    parse_hpo_genes_to_phenotype,
    parse_mondo_gene_disease_tsv,
    record_mondo_hpo_version,
)
from backend.db.tables import database_versions, gene_phenotype, reference_metadata

# ── Fixtures ────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
GENE_PHENOTYPE_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "gene_phenotype_seed.csv"


@pytest.fixture
def reference_engine() -> sa.Engine:
    """In-memory reference engine with tables created."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    reference_metadata.create_all(engine)
    return engine


@pytest.fixture
def loaded_engine(reference_engine: sa.Engine) -> sa.Engine:
    """Reference engine loaded with seed CSV data."""
    load_mondo_hpo_from_csv(GENE_PHENOTYPE_SEED_CSV, reference_engine, clear_existing=False)
    return reference_engine


@pytest.fixture
def mondo_tsv_file(tmp_path: Path) -> Path:
    """Create a minimal MONDO gene-disease TSV for testing."""
    header = "subject\tsubject_label\tpredicate\tobject\tobject_label\tqualifier"
    predicate = "biolink:gene_associated_with_condition"
    rows = [
        f"HGNC:1100\tBRCA1\t{predicate}\tMONDO:0011450\t"
        "Hereditary breast and ovarian cancer syndrome\t",
        f"HGNC:1101\tBRCA2\t{predicate}\tMONDO:0012933\t"
        "breast-ovarian cancer, familial, susceptibility to, 2\t",
        f"HGNC:1884\tCFTR\t{predicate}\tMONDO:0009061\tCystic fibrosis\t",
        f"\t\t{predicate}\tMONDO:0000001\tSome disease\t",
        f"HGNC:9999\tFAKE\t{predicate}\t\t\t",
    ]
    content = "\n".join([header, *rows]) + "\n"
    tsv_path = tmp_path / "gene_disease.all.tsv"
    tsv_path.write_text(content, encoding="utf-8")
    return tsv_path


@pytest.fixture
def hpo_phenotype_file(tmp_path: Path) -> Path:
    """Create a minimal HPO genes_to_phenotype.txt for testing."""
    content = textwrap.dedent("""\
        ncbi_gene_id\tgene_symbol\thpo_id\thpo_name\tfrequency\tdisease_id
        672\tBRCA1\tHP:0003002\tBreast carcinoma\t\tOMIM:604370
        672\tBRCA1\tHP:0100013\tNeoplasm of the breast\t\tOMIM:604370
        672\tBRCA1\tHP:0000006\tAutosomal dominant\t\tOMIM:604370
        1080\tCFTR\tHP:0002110\tBronchiectasis\t\tOMIM:219700
        1080\tCFTR\tHP:0006538\tRecurrent pneumonia\t\tOMIM:219700
        1080\tCFTR\tHP:0000007\tAutosomal recessive\t\tOMIM:219700
        7436\tMTHFR\tHP:0003572\tLow plasma methionine\t\tOMIM:607093
    """)
    hpo_path = tmp_path / "genes_to_phenotype.txt"
    hpo_path.write_text(content, encoding="utf-8")
    return hpo_path


# ── CSV seed loading tests ──────────────────────────────────────────────


class TestLoadFromCSV:
    """Test CSV seed loading."""

    def test_load_seed_csv(self, reference_engine: sa.Engine) -> None:
        """Loading seed CSV populates gene_phenotype table."""
        stats = load_mondo_hpo_from_csv(
            GENE_PHENOTYPE_SEED_CSV, reference_engine, clear_existing=False
        )
        assert stats.records_loaded > 0
        assert stats.total_lines > 0

        with reference_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(gene_phenotype)).scalar()
            assert count == stats.records_loaded

    def test_load_preserves_all_columns(self, loaded_engine: sa.Engine) -> None:
        """All columns are populated correctly for BRCA1."""
        with loaded_engine.connect() as conn:
            row = conn.execute(
                sa.select(gene_phenotype).where(gene_phenotype.c.gene_symbol == "BRCA1")
            ).first()

        assert row is not None
        assert row.gene_symbol == "BRCA1"
        assert "breast" in row.disease_name.lower()
        assert row.disease_id == "MONDO:0011450"
        assert row.source == "mondo_hpo"
        assert row.inheritance == "Autosomal dominant"

        # HPO terms should be a JSON array
        hpo_terms = json.loads(row.hpo_terms)
        assert isinstance(hpo_terms, list)
        assert "HP:0003002" in hpo_terms

    def test_load_clears_existing_mondo_hpo_only(self, reference_engine: sa.Engine) -> None:
        """clear_existing only removes mondo_hpo rows, not omim rows."""
        # Insert a fake OMIM row
        with reference_engine.begin() as conn:
            conn.execute(
                gene_phenotype.insert().values(
                    gene_symbol="FAKE",
                    disease_name="Fake OMIM disease",
                    disease_id="OMIM:100000",
                    source="omim",
                )
            )

        # Load seed CSV (should clear mondo_hpo but keep omim)
        load_mondo_hpo_from_csv(GENE_PHENOTYPE_SEED_CSV, reference_engine, clear_existing=True)

        with reference_engine.connect() as conn:
            omim_count = conn.execute(
                sa.select(sa.func.count())
                .select_from(gene_phenotype)
                .where(gene_phenotype.c.source == "omim")
            ).scalar()
            assert omim_count == 1  # OMIM row preserved

    def test_load_empty_csv(self, reference_engine: sa.Engine, tmp_path: Path) -> None:
        """Empty CSV results in zero records loaded."""
        empty_csv = tmp_path / "empty.csv"
        empty_csv.write_text("gene_symbol,disease_name,disease_id,hpo_terms,source,inheritance\n")
        stats = load_mondo_hpo_from_csv(empty_csv, reference_engine, clear_existing=False)
        assert stats.records_loaded == 0


# ── MONDO TSV parsing tests ─────────────────────────────────────────────


class TestMondoTSVParsing:
    """Test MONDO gene-disease TSV parsing."""

    def test_parse_basic_tsv(self, mondo_tsv_file: Path) -> None:
        """Parse a minimal MONDO gene-disease TSV."""
        records, stats = parse_mondo_gene_disease_tsv(mondo_tsv_file)
        assert stats.total_lines == 5
        assert "BRCA1" in records
        assert "BRCA2" in records
        assert "CFTR" in records
        assert stats.skipped_no_gene >= 1  # blank row
        assert stats.skipped_no_disease >= 1  # FAKE with no disease

    def test_parse_deduplication(self, tmp_path: Path) -> None:
        """Duplicate (gene, disease_id) entries are skipped."""
        content = textwrap.dedent("""\
            subject\tsubject_label\tpredicate\tobject\tobject_label
            HGNC:1100\tBRCA1\tassociated\tMONDO:0011450\tDisease A
            HGNC:1100\tBRCA1\tassociated\tMONDO:0011450\tDisease A
            HGNC:1100\tBRCA1\tassociated\tMONDO:0011451\tDisease B
        """)
        tsv_path = tmp_path / "dup_test.tsv"
        tsv_path.write_text(content, encoding="utf-8")
        records, stats = parse_mondo_gene_disease_tsv(tsv_path)
        assert stats.skipped_duplicate == 1
        assert len(records["BRCA1"]) == 2


# ── HPO parsing tests ───────────────────────────────────────────────────


class TestHPOParsing:
    """Test HPO genes_to_phenotype parsing."""

    def test_parse_hpo_basic(self, hpo_phenotype_file: Path) -> None:
        """Parse HPO phenotype file and extract terms + inheritance."""
        result = parse_hpo_genes_to_phenotype(hpo_phenotype_file)

        assert "BRCA1" in result
        brca1 = result["BRCA1"]
        # Should have HP:0003002 and HP:0100013 but NOT HP:0000006 (inheritance)
        assert brca1["hpo_terms"] == [
            HpoTerm(id="HP:0003002", name="Breast carcinoma"),
            HpoTerm(id="HP:0100013", name="Neoplasm of the breast"),
        ]
        assert brca1["inheritance"] == "Autosomal dominant"

        assert "CFTR" in result
        cftr = result["CFTR"]
        assert HpoTerm(id="HP:0002110", name="Bronchiectasis") in cftr["hpo_terms"]
        assert cftr["inheritance"] == "Autosomal recessive"

    def test_parse_hpo_no_inheritance(self, hpo_phenotype_file: Path) -> None:
        """MTHFR has no inheritance HPO term."""
        result = parse_hpo_genes_to_phenotype(hpo_phenotype_file)
        assert "MTHFR" in result
        assert result["MTHFR"]["inheritance"] is None
        assert result["MTHFR"]["hpo_terms"] == [
            HpoTerm(id="HP:0003572", name="Low plasma methionine")
        ]

    def test_parse_hpo_deduplicates_and_prefers_nonempty_label(self, tmp_path: Path) -> None:
        """Repeated IDs collapse deterministically without losing a later label."""
        hpo_path = tmp_path / "genes_to_phenotype.txt"
        hpo_path.write_text(
            "ncbi_gene_id\tgene_symbol\thpo_id\thpo_name\tfrequency\tdisease_id\n"
            "672\tBRCA1\tHP:0003002\t\t\tOMIM:604370\n"
            "672\tBRCA1\tHP:0003002\tBreast carcinoma\t\tOMIM:604370\n"
            "672\tBRCA1\tnot-an-hpo-id\tIgnored\t\tOMIM:604370\n",
            encoding="utf-8",
        )

        result = parse_hpo_genes_to_phenotype(hpo_path)

        assert result["BRCA1"]["hpo_terms"] == [HpoTerm(id="HP:0003002", name="Breast carcinoma")]


class TestHpoTermDecoding:
    """Compatibility decoding for legacy and labelled reference rows."""

    def test_decodes_legacy_id_array(self) -> None:
        assert decode_hpo_terms('["HP:0003002"]') == [HpoTerm(id="HP:0003002")]

    def test_decodes_labelled_object_array(self) -> None:
        raw = '[{"id": "HP:0003002", "name": "Breast carcinoma"}]'
        assert decode_hpo_terms(raw) == [HpoTerm(id="HP:0003002", name="Breast carcinoma")]

    def test_drops_invalid_items_and_prefers_available_label(self) -> None:
        raw = json.dumps(
            [
                "HP:0003002",
                {"id": "HP:0003002", "name": "Breast carcinoma"},
                {"id": "not-an-hpo-id", "name": "Ignored"},
                {"name": "Missing ID"},
                42,
            ]
        )
        assert decode_hpo_terms(raw) == [HpoTerm(id="HP:0003002", name="Breast carcinoma")]

    @pytest.mark.parametrize("raw", [None, "", "not-json", "{}"])
    def test_malformed_or_non_array_input_is_empty(self, raw: str | None) -> None:
        assert decode_hpo_terms(raw) == []


# ── Record merging tests ────────────────────────────────────────────────


class TestRecordMerging:
    """Test merging MONDO records with HPO data."""

    def test_merge_with_hpo(self) -> None:
        """Records get HPO terms and inheritance from HPO data."""
        records = {
            "BRCA1": [
                GenePhenotypeRecord(
                    gene_symbol="BRCA1",
                    disease_name="Breast cancer",
                    disease_id="MONDO:0011450",
                )
            ],
        }
        hpo_data = {
            "BRCA1": {
                "hpo_terms": [
                    HpoTerm(id="HP:0003002", name="Breast carcinoma"),
                    HpoTerm(id="HP:0100013", name="Neoplasm of the breast"),
                ],
                "inheritance": "Autosomal dominant",
            }
        }
        rows = _records_to_rows(records, hpo_data)
        assert len(rows) == 1
        row = rows[0]
        assert row["gene_symbol"] == "BRCA1"
        assert row["source"] == "mondo_hpo"
        assert row["inheritance"] == "Autosomal dominant"
        hpo = json.loads(row["hpo_terms"])
        assert hpo == [
            {"id": "HP:0003002", "name": "Breast carcinoma"},
            {"id": "HP:0100013", "name": "Neoplasm of the breast"},
        ]

    def test_merge_without_hpo(self) -> None:
        """Records without HPO data get None hpo_terms."""
        records = {
            "UNKNOWN": [
                GenePhenotypeRecord(
                    gene_symbol="UNKNOWN",
                    disease_name="Unknown disease",
                    disease_id="MONDO:0000001",
                )
            ],
        }
        rows = _records_to_rows(records, {})
        assert len(rows) == 1
        assert rows[0]["hpo_terms"] is None
        assert rows[0]["inheritance"] is None


# ── Lookup tests ─────────────────────────────────────────────────────────


class TestLookup:
    """Test gene-phenotype lookup function."""

    def test_lookup_brca1(self, loaded_engine: sa.Engine) -> None:
        """T2-13: MONDO/HPO lookup returns correct phenotype for BRCA1."""
        results = lookup_gene_phenotypes(["BRCA1"], loaded_engine)
        assert "BRCA1" in results
        brca1_phenotypes = results["BRCA1"]
        assert len(brca1_phenotypes) >= 1

        first = brca1_phenotypes[0]
        assert first.gene_symbol == "BRCA1"
        assert "breast" in first.disease_name.lower()
        assert first.disease_id == "MONDO:0011450"
        assert first.source == "mondo_hpo"
        assert first.inheritance == "Autosomal dominant"
        assert isinstance(first.hpo_terms, list)
        assert "HP:0003002" in first.hpo_terms
        assert HpoTerm(id="HP:0003002") in first.hpo_term_details

    def test_lookup_multiple_genes(self, loaded_engine: sa.Engine) -> None:
        """Batch lookup returns results for multiple genes."""
        results = lookup_gene_phenotypes(["BRCA1", "CFTR", "MTHFR"], loaded_engine)
        assert "BRCA1" in results
        assert "CFTR" in results
        assert "MTHFR" in results

    def test_lookup_gene_with_multiple_diseases(self, loaded_engine: sa.Engine) -> None:
        """HBB has multiple diseases (Sickle cell + Beta-thal)."""
        results = lookup_gene_phenotypes(["HBB"], loaded_engine)
        assert "HBB" in results
        assert len(results["HBB"]) == 2
        disease_names = {r.disease_name for r in results["HBB"]}
        assert "Sickle cell disease" in disease_names
        assert "Beta-thalassemia" in disease_names

    def test_lookup_nonexistent_gene(self, loaded_engine: sa.Engine) -> None:
        """Lookup for nonexistent gene returns empty."""
        results = lookup_gene_phenotypes(["NONEXISTENT_GENE"], loaded_engine)
        assert "NONEXISTENT_GENE" not in results

    def test_lookup_empty_list(self, loaded_engine: sa.Engine) -> None:
        """Lookup with empty list returns empty dict."""
        results = lookup_gene_phenotypes([], loaded_engine)
        assert results == {}

    def test_lookup_source_filter(self, loaded_engine: sa.Engine) -> None:
        """Source filter restricts results to specified source."""
        # All seed data is mondo_hpo source
        results = lookup_gene_phenotypes(["BRCA1"], loaded_engine, source_filter="mondo_hpo")
        assert "BRCA1" in results

        results = lookup_gene_phenotypes(["BRCA1"], loaded_engine, source_filter="omim")
        assert "BRCA1" not in results

    def test_lookup_hpo_terms_json_parsing(self, loaded_engine: sa.Engine) -> None:
        """HPO terms are correctly parsed from JSON."""
        results = lookup_gene_phenotypes(["CFTR"], loaded_engine)
        assert "CFTR" in results
        cftr = results["CFTR"][0]
        assert isinstance(cftr.hpo_terms, list)
        assert all(t.startswith("HP:") for t in cftr.hpo_terms)

    def test_lookup_preserves_labels_from_structured_rows(self, loaded_engine: sa.Engine) -> None:
        """Labelled reference rows expose details while retaining the ID list."""
        labelled = json.dumps([{"id": "HP:0002110", "name": "Bronchiectasis"}])
        with loaded_engine.begin() as conn:
            conn.execute(
                gene_phenotype.update()
                .where(gene_phenotype.c.gene_symbol == "CFTR")
                .values(hpo_terms=labelled)
            )

        cftr = lookup_gene_phenotypes(["CFTR"], loaded_engine)["CFTR"][0]

        assert cftr.hpo_terms == ["HP:0002110"]
        assert cftr.hpo_term_details == [HpoTerm(id="HP:0002110", name="Bronchiectasis")]


# ── Version recording tests ─────────────────────────────────────────────


class TestVersionRecording:
    """Test version recording in database_versions."""

    def test_record_version_insert(self, reference_engine: sa.Engine) -> None:
        """First call inserts a new version record."""
        record_mondo_hpo_version(
            reference_engine,
            version="20260312",
            file_path="/data/gene_disease.tsv",
            file_size_bytes=1024,
            checksum="abc123",
        )
        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "mondo_hpo")
            ).first()
        assert row is not None
        assert row.version == "20260312"
        assert row.file_path == "/data/gene_disease.tsv"
        assert row.checksum_sha256 == "abc123"

    def test_record_version_update(self, reference_engine: sa.Engine) -> None:
        """Second call updates the existing version record."""
        record_mondo_hpo_version(reference_engine, version="20260301")
        record_mondo_hpo_version(reference_engine, version="20260312")
        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "mondo_hpo")
            ).first()
        assert row.version == "20260312"


# ── Helper function tests ───────────────────────────────────────────────


class TestHelpers:
    """Test helper/utility functions."""

    def test_extract_gene_symbol_bare(self) -> None:
        """Bare gene symbol is returned as-is."""
        assert _extract_gene_symbol_from_subject("BRCA1") == "BRCA1"

    def test_extract_gene_symbol_with_prefix(self) -> None:
        """Prefixed identifiers return None (need subject_label)."""
        assert _extract_gene_symbol_from_subject("HGNC:1100") is None

    def test_extract_gene_symbol_empty(self) -> None:
        """Empty string returns None."""
        assert _extract_gene_symbol_from_subject("") is None

    def test_inheritance_map_completeness(self) -> None:
        """Inheritance map covers key HPO inheritance terms."""
        assert "HP:0000006" in _INHERITANCE_MAP  # AD
        assert "HP:0000007" in _INHERITANCE_MAP  # AR
        assert "HP:0001417" in _INHERITANCE_MAP  # XL

    def test_load_stats_defaults(self) -> None:
        """LoadStats initializes with zeros."""
        stats = LoadStats()
        assert stats.total_lines == 0
        assert stats.records_loaded == 0
        assert stats.sha256 is None


# ── Bulk loading tests ──────────────────────────────────────────────────


class TestBulkLoading:
    """Test bulk loading of gene-phenotype rows."""

    def test_load_rows(self, reference_engine: sa.Engine) -> None:
        """Direct row loading works."""
        rows = [
            {
                "gene_symbol": "TEST1",
                "disease_name": "Test disease",
                "disease_id": "MONDO:0000001",
                "hpo_terms": json.dumps(["HP:0000001"]),
                "source": "mondo_hpo",
                "inheritance": "Autosomal dominant",
            },
            {
                "gene_symbol": "TEST2",
                "disease_name": "Another disease",
                "disease_id": "MONDO:0000002",
                "hpo_terms": None,
                "source": "mondo_hpo",
                "inheritance": None,
            },
        ]
        loaded = load_mondo_hpo_rows(rows, reference_engine, clear_existing=False)
        assert loaded == 2

        with reference_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(gene_phenotype)).scalar()
            assert count == 2

    def test_load_rows_clear_existing(self, reference_engine: sa.Engine) -> None:
        """clear_existing removes only mondo_hpo rows."""
        # First load
        load_mondo_hpo_rows(
            [
                {
                    "gene_symbol": "OLD",
                    "disease_name": "Old disease",
                    "disease_id": "MONDO:0000099",
                    "hpo_terms": None,
                    "source": "mondo_hpo",
                    "inheritance": None,
                }
            ],
            reference_engine,
            clear_existing=False,
        )

        # Second load with clear
        load_mondo_hpo_rows(
            [
                {
                    "gene_symbol": "NEW",
                    "disease_name": "New disease",
                    "disease_id": "MONDO:0000100",
                    "hpo_terms": None,
                    "source": "mondo_hpo",
                    "inheritance": None,
                }
            ],
            reference_engine,
            clear_existing=True,
        )

        with reference_engine.connect() as conn:
            rows = conn.execute(sa.select(gene_phenotype.c.gene_symbol)).fetchall()
            symbols = [r.gene_symbol for r in rows]
            assert "NEW" in symbols
            assert "OLD" not in symbols


class TestMondoHpoZeroRowGuard:
    """A destructive clear with 0 rows to load must never wipe gene_phenotype.

    Mirrors the cpic/clingen guard: a zero-row external fetch/parse (empty or
    truncated MONDO/HPO download, an upstream format change) must not silently
    empty the curated mondo_hpo rows (#753).
    """

    @staticmethod
    def _count(engine: sa.Engine) -> int:
        with engine.connect() as conn:
            return conn.execute(sa.select(sa.func.count()).select_from(gene_phenotype)).scalar()

    def test_load_rows_refuses_to_wipe(self, loaded_engine: sa.Engine) -> None:
        before = self._count(loaded_engine)
        assert before > 0
        with pytest.raises(ValueError, match="0 rows"):
            load_mondo_hpo_rows([], loaded_engine, clear_existing=True)
        assert self._count(loaded_engine) == before  # curated rows preserved

    def test_load_rows_empty_no_clear_is_noop(self, loaded_engine: sa.Engine) -> None:
        before = self._count(loaded_engine)
        load_mondo_hpo_rows([], loaded_engine, clear_existing=False)
        assert self._count(loaded_engine) == before

    def test_load_from_csv_refuses_to_wipe(self, loaded_engine: sa.Engine, tmp_path: Path) -> None:
        before = self._count(loaded_engine)
        assert before > 0
        empty_csv = tmp_path / "empty.csv"
        empty_csv.write_text("gene_symbol,disease_name,disease_id,hpo_terms,source,inheritance\n")
        with pytest.raises(ValueError, match="0 rows"):
            load_mondo_hpo_from_csv(empty_csv, loaded_engine, clear_existing=True)
        assert self._count(loaded_engine) == before
