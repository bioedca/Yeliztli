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

import gzip
import json
import textwrap
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from backend.annotation.mondo_hpo import (
    _INHERITANCE_MAP,
    MONDO_HPO_INGESTION_REVISION,
    GenePhenotypeRecord,
    HpoTerm,
    LoadStats,
    _extract_gene_symbol_from_subject,
    _records_to_rows,
    decode_hpo_terms,
    download_and_load_mondo_hpo,
    load_mondo_hpo_from_csv,
    load_mondo_hpo_rows,
    lookup_gene_phenotypes,
    parse_hpo_genes_to_phenotype,
    parse_mondo_gene_disease_tsv,
    parse_mondo_sssom,
    record_mondo_hpo_version,
)
from backend.db.tables import database_versions, gene_phenotype, reference_metadata

# ── Fixtures ────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
GENE_PHENOTYPE_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "gene_phenotype_seed.csv"
_SSSOM_HEADER = (
    "subject_id\tsubject_label\tpredicate_id\tobject_id\tobject_label\tmapping_justification"
)


def _sssom_line(*columns: str) -> str:
    return "\t".join(columns)


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


@pytest.fixture
def mondo_sssom_file(tmp_path: Path) -> Path:
    """Create a minimal authoritative-style MONDO SSSOM exact-map TSV."""
    content = (
        "\n".join(
            [
                "# mapping_set_id: http://purl.obolibrary.org/obo/mondo/mappings/mondo.sssom.tsv",
                _SSSOM_HEADER,
                _sssom_line(
                    "MONDO:0011450",
                    "Hereditary breast cancer",
                    "skos:exactMatch",
                    "OMIM:604370",
                    "Breast-ovarian cancer",
                    "semapv:UnspecifiedMatching",
                ),
                _sssom_line(
                    "MONDO:0009061",
                    "Cystic fibrosis",
                    "skos:exactMatch",
                    "OMIM:219700",
                    "Cystic fibrosis",
                    "semapv:UnspecifiedMatching",
                ),
            ]
        )
        + "\n"
    )
    sssom_path = tmp_path / "mondo.sssom.tsv"
    sssom_path.write_text(content, encoding="utf-8")
    return sssom_path


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

    def test_rejects_missing_or_non_mondo_disease_ids(self, tmp_path: Path) -> None:
        """Only canonical MONDO object IDs may enter disease-scoped matching."""
        tsv_path = tmp_path / "invalid_disease_ids.tsv"
        tsv_path.write_text(
            "subject\tsubject_label\tpredicate\tobject\tobject_label\n"
            "HGNC:1100\tBRCA1\tassociated\tOMIM:604370\tNot a MONDO row\n"
            "HGNC:1100\tBRCA1\tassociated\t\tMissing ID\n"
            "HGNC:1100\tBRCA1\tassociated\tMONDO:0011450\tValid MONDO row\n",
            encoding="utf-8",
        )

        records, stats = parse_mondo_gene_disease_tsv(tsv_path)

        assert [record.disease_id for record in records["BRCA1"]] == ["MONDO:0011450"]
        assert stats.skipped_no_disease == 2


# ── HPO parsing tests ───────────────────────────────────────────────────


class TestHPOParsing:
    """Test HPO genes_to_phenotype parsing."""

    def test_parse_hpo_basic(self, hpo_phenotype_file: Path) -> None:
        """Parse HPO phenotype data by source disease, terms, and inheritance."""
        result = parse_hpo_genes_to_phenotype(hpo_phenotype_file)

        assert "BRCA1" in result
        brca1 = result["BRCA1"]["OMIM:604370"]
        # Should have HP:0003002 and HP:0100013 but NOT HP:0000006 (inheritance)
        assert brca1["hpo_terms"] == [
            HpoTerm(id="HP:0003002", name="Breast carcinoma"),
            HpoTerm(id="HP:0100013", name="Neoplasm of the breast"),
        ]
        assert brca1["inheritance"] == "Autosomal dominant"

        assert "CFTR" in result
        cftr = result["CFTR"]["OMIM:219700"]
        assert HpoTerm(id="HP:0002110", name="Bronchiectasis") in cftr["hpo_terms"]
        assert cftr["inheritance"] == "Autosomal recessive"

    def test_parse_hpo_no_inheritance(self, hpo_phenotype_file: Path) -> None:
        """MTHFR has no inheritance HPO term."""
        result = parse_hpo_genes_to_phenotype(hpo_phenotype_file)
        assert "MTHFR" in result
        mthfr = result["MTHFR"]["OMIM:607093"]
        assert mthfr["inheritance"] is None
        assert mthfr["hpo_terms"] == [HpoTerm(id="HP:0003572", name="Low plasma methionine")]

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

        assert result["BRCA1"]["OMIM:604370"]["hpo_terms"] == [
            HpoTerm(id="HP:0003002", name="Breast carcinoma")
        ]

    def test_keeps_terms_and_inheritance_scoped_to_each_disease(self, tmp_path: Path) -> None:
        """The same gene's disease rows cannot pool terms or inheritance."""
        hpo_path = tmp_path / "genes_to_phenotype.txt"
        hpo_path.write_text(
            "ncbi_gene_id\tgene_symbol\thpo_id\thpo_name\tfrequency\tdisease_id\n"
            "672\tBRCA1\tHP:0003002\tBreast carcinoma\t\tOMIM:604370\n"
            "672\tBRCA1\tHP:0000006\tAutosomal dominant\t\tOMIM:604370\n"
            "672\tBRCA1\tHP:0002110\tBronchiectasis\t\tORPHA:33364\n"
            "672\tBRCA1\tHP:0000007\tAutosomal recessive\t\tORPHA:33364\n",
            encoding="utf-8",
        )

        result = parse_hpo_genes_to_phenotype(hpo_path)

        assert result["BRCA1"]["OMIM:604370"] == {
            "hpo_terms": [HpoTerm(id="HP:0003002", name="Breast carcinoma")],
            "inheritance": "Autosomal dominant",
        }
        assert result["BRCA1"]["Orphanet:33364"] == {
            "hpo_terms": [HpoTerm(id="HP:0002110", name="Bronchiectasis")],
            "inheritance": "Autosomal recessive",
        }

    @pytest.mark.parametrize("disease_id", ["", "not-a-curie", "OMIM: 604370"])
    def test_requires_a_valid_source_disease_id(self, tmp_path: Path, disease_id: str) -> None:
        """Missing or malformed sixth-column identifiers are not silently pooled."""
        hpo_path = tmp_path / "genes_to_phenotype.txt"
        hpo_path.write_text(
            "ncbi_gene_id\tgene_symbol\thpo_id\thpo_name\tfrequency\tdisease_id\n"
            f"672\tBRCA1\tHP:0003002\tBreast carcinoma\t\t{disease_id}\n",
            encoding="utf-8",
        )

        assert parse_hpo_genes_to_phenotype(hpo_path) == {}

    def test_ambiguous_inheritance_is_withheld(self, tmp_path: Path) -> None:
        """A scalar field must not select one of multiple source inheritance modes."""
        hpo_path = tmp_path / "genes_to_phenotype.txt"
        hpo_path.write_text(
            "ncbi_gene_id\tgene_symbol\thpo_id\thpo_name\tfrequency\tdisease_id\n"
            "672\tBRCA1\tHP:0000006\tAutosomal dominant\t\tOMIM:604370\n"
            "672\tBRCA1\tHP:0000007\tAutosomal recessive\t\tOMIM:604370\n",
            encoding="utf-8",
        )

        assert parse_hpo_genes_to_phenotype(hpo_path)["BRCA1"]["OMIM:604370"] == {
            "hpo_terms": [],
            "inheritance": None,
        }


class TestMondoSssomParsing:
    """Test exact, unambiguous MONDO cross-reference parsing."""

    def test_accepts_exact_matches_and_normalizes_orpha(self, tmp_path: Path) -> None:
        sssom_path = tmp_path / "mondo.sssom.tsv"
        sssom_path.write_text(
            "\n".join(
                [
                    "# mapping_set_id: https://example.test/mondo.sssom.tsv",
                    _SSSOM_HEADER,
                    _sssom_line(
                        "MONDO:0011450",
                        "Disease A",
                        "skos:exactMatch",
                        "OMIM:604370",
                        "Disease A",
                        "semapv:UnspecifiedMatching",
                    ),
                    _sssom_line(
                        "MONDO:0018053",
                        "Disease B",
                        "skos:exactMatch",
                        "Orphanet:33364",
                        "Disease B",
                        "semapv:UnspecifiedMatching",
                    ),
                    _sssom_line(
                        "MONDO:0000002",
                        "Broad only",
                        "skos:broadMatch",
                        "OMIM:999999",
                        "Broad only",
                        "semapv:UnspecifiedMatching",
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        mappings = parse_mondo_sssom(sssom_path)

        assert mappings["OMIM:604370"] == "MONDO:0011450"
        assert mappings["Orphanet:33364"] == "MONDO:0018053"
        assert "OMIM:999999" not in mappings

    def test_rejects_ambiguous_or_malformed_exact_targets(self, tmp_path: Path) -> None:
        sssom_path = tmp_path / "mondo.sssom.tsv"
        sssom_path.write_text(
            "\n".join(
                [
                    _SSSOM_HEADER,
                    _sssom_line(
                        "MONDO:0011450",
                        "Disease A",
                        "skos:exactMatch",
                        "OMIM:604370",
                        "Disease A",
                        "semapv:UnspecifiedMatching",
                    ),
                    _sssom_line(
                        "MONDO:0011451",
                        "Disease B",
                        "skos:exactMatch",
                        "OMIM:604370",
                        "Disease B",
                        "semapv:UnspecifiedMatching",
                    ),
                    _sssom_line(
                        "OMIM:1",
                        "Invalid subject",
                        "skos:exactMatch",
                        "OMIM:111111",
                        "Invalid",
                        "semapv:UnspecifiedMatching",
                    ),
                    _sssom_line(
                        "MONDO:0011452",
                        "Missing object",
                        "skos:exactMatch",
                        "",
                        "Missing",
                        "semapv:UnspecifiedMatching",
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        mappings = parse_mondo_sssom(sssom_path)

        assert "OMIM:604370" not in mappings
        assert "OMIM:111111" not in mappings


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

    def test_merge_with_disease_scoped_hpo(self) -> None:
        """Each MONDO disease gets only its exactly mapped HPO context."""
        records = {
            "BRCA1": [
                GenePhenotypeRecord(
                    gene_symbol="BRCA1",
                    disease_name="Breast cancer",
                    disease_id="MONDO:0011450",
                ),
                GenePhenotypeRecord(
                    gene_symbol="BRCA1",
                    disease_name="A distinct disease",
                    disease_id="MONDO:0018053",
                ),
            ],
        }
        hpo_data = {
            "BRCA1": {
                "OMIM:604370": {
                    "hpo_terms": [
                        HpoTerm(id="HP:0003002", name="Breast carcinoma"),
                        HpoTerm(id="HP:0100013", name="Neoplasm of the breast"),
                    ],
                    "inheritance": "Autosomal dominant",
                },
                "Orphanet:33364": {
                    "hpo_terms": [HpoTerm(id="HP:0002110", name="Bronchiectasis")],
                    "inheritance": "Autosomal recessive",
                },
            }
        }
        mappings = {
            "OMIM:604370": "MONDO:0011450",
            "Orphanet:33364": "MONDO:0018053",
        }

        rows = _records_to_rows(records, hpo_data, mappings)

        assert len(rows) == 2
        by_disease = {row["disease_id"]: row for row in rows}
        breast = by_disease["MONDO:0011450"]
        assert breast["gene_symbol"] == "BRCA1"
        assert breast["source"] == "mondo_hpo"
        assert breast["inheritance"] == "Autosomal dominant"
        assert json.loads(breast["hpo_terms"]) == [
            {"id": "HP:0003002", "name": "Breast carcinoma"},
            {"id": "HP:0100013", "name": "Neoplasm of the breast"},
        ]
        distinct = by_disease["MONDO:0018053"]
        assert distinct["inheritance"] == "Autosomal recessive"
        assert json.loads(distinct["hpo_terms"]) == [
            {"id": "HP:0002110", "name": "Bronchiectasis"}
        ]

    def test_merge_does_not_fall_back_to_gene_or_label_matching(self) -> None:
        """Unmapped source IDs must not leak terms to another disease for the gene."""
        records = {
            "BRCA1": [
                GenePhenotypeRecord(
                    gene_symbol="BRCA1",
                    disease_name="A disease with a similar label",
                    disease_id="MONDO:0011450",
                )
            ]
        }
        hpo_data = {
            "BRCA1": {
                "OMIM:999999": {
                    "hpo_terms": [HpoTerm(id="HP:0003002", name="Breast carcinoma")],
                    "inheritance": "Autosomal dominant",
                }
            }
        }

        row = _records_to_rows(records, hpo_data, {})[0]

        assert row["hpo_terms"] is None
        assert row["inheritance"] is None

    def test_merge_accepts_a_direct_mondo_scope_without_a_cross_reference(self) -> None:
        """A canonical MONDO source disease does not need to appear in SSSOM twice."""
        records = {
            "BRCA1": [
                GenePhenotypeRecord(
                    gene_symbol="BRCA1",
                    disease_name="Breast cancer",
                    disease_id="MONDO:0011450",
                )
            ]
        }
        hpo_data = {
            "BRCA1": {
                "MONDO:0011450": {
                    "hpo_terms": [HpoTerm(id="HP:0003002", name="Breast carcinoma")],
                    "inheritance": "Autosomal dominant",
                }
            }
        }

        row = _records_to_rows(records, hpo_data, {})[0]

        assert json.loads(row["hpo_terms"]) == [{"id": "HP:0003002", "name": "Breast carcinoma"}]
        assert row["inheritance"] == "Autosomal dominant"

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
        rows = _records_to_rows(records, {}, {})
        assert len(rows) == 1
        assert rows[0]["hpo_terms"] is None
        assert rows[0]["inheritance"] is None


class TestDownloadAndLoad:
    """Test the full three-source disease-scoped ingestion path."""

    def test_records_all_source_provenance_and_scoped_rows(
        self,
        monkeypatch,
        reference_engine: sa.Engine,
        mondo_tsv_file: Path,
        hpo_phenotype_file: Path,
        mondo_sssom_file: Path,
        tmp_path: Path,
    ) -> None:
        """The loader persists per-source evidence and maps only exact scopes."""
        monkeypatch.setattr("backend.annotation.mondo_hpo.MINIMUM_UNAMBIGUOUS_MONDO_XREFS", 2)
        source_files = {
            "gene_disease.9606.tsv.gz": mondo_tsv_file,
            "genes_to_phenotype.txt": hpo_phenotype_file,
            "mondo.sssom.tsv": mondo_sssom_file,
        }

        def fake_download(url, dest_dir, filename, **kwargs):
            target = dest_dir / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            if filename.endswith(".gz"):
                with gzip.open(target, "wb") as fh:
                    fh.write(source_files[filename].read_bytes())
            else:
                target.write_bytes(source_files[filename].read_bytes())
            meta = kwargs.get("meta")
            if meta is not None:
                meta.update(
                    {
                        "etag": f'"{filename}-etag"',
                        "last_modified": "Wed, 15 Apr 2026 12:34:56 GMT",
                        "version": "20260415",
                    }
                )
            return target

        monkeypatch.setattr("backend.annotation.mondo_hpo.download_file", fake_download)

        downloads = tmp_path / "downloads"
        prior_bundle = downloads / "mondo_hpo_sources" / "prior-immutable-bundle"
        prior_bundle.mkdir(parents=True)
        prior_source = prior_bundle / "gene_disease.9606.tsv.gz"
        prior_source.write_text("prior source bytes\n", encoding="utf-8")

        stats = download_and_load_mondo_hpo(
            reference_engine,
            downloads,
            mondo_url="https://operator:credential@source.example/mondo.tsv?access=opaque#fragment",
            hpo_url="https://source.example/hpo.txt?signature=secret",
            mondo_sssom_url="https://source.example/mondo.sssom.tsv#fragment",
        )

        assert stats.hpo_disease_matches == 2
        assert stats.hpo_disease_unmatched == 1  # MTHFR has no MONDO record in the fixture.
        assert stats.sha256 is not None
        assert stats.hpo_sha256 is not None
        assert stats.mondo_sssom_sha256 is not None
        assert stats.version.startswith(f"20260415+{MONDO_HPO_INGESTION_REVISION}+hpo-")
        assert stats.hpo_version == "20260415"
        assert stats.mondo_sssom_version == "20260415"

        bundle_dirs = [
            path
            for path in (downloads / "mondo_hpo_sources").iterdir()
            if path.name != "prior-immutable-bundle"
        ]
        assert len(bundle_dirs) == 1
        source_bundle = bundle_dirs[0]
        manifest = json.loads((source_bundle / "mondo_hpo_sources.json").read_text())
        assert manifest["ingestion_revision"] == MONDO_HPO_INGESTION_REVISION
        assert {source["role"] for source in manifest["sources"]} == {
            "mondo_gene_disease",
            "hpo_genes_to_phenotype",
            "mondo_sssom_exact_cross_references",
        }
        assert all(source["sha256"] for source in manifest["sources"])
        assert {source["url"] for source in manifest["sources"]} == {
            "https://source.example/mondo.tsv",
            "https://source.example/hpo.txt",
            "https://source.example/mondo.sssom.tsv",
        }
        assert prior_source.read_text(encoding="utf-8") == "prior source bytes\n"

        with reference_engine.connect() as conn:
            rows = conn.execute(
                sa.select(gene_phenotype).where(gene_phenotype.c.source == "mondo_hpo")
            ).fetchall()
            version_row = conn.execute(
                sa.select(database_versions.c.version, database_versions.c.file_path).where(
                    database_versions.c.db_name == "mondo_hpo"
                )
            ).one()
        brca = next(row for row in rows if row.gene_symbol == "BRCA1")
        cftr = next(row for row in rows if row.gene_symbol == "CFTR")
        assert json.loads(brca.hpo_terms) == [
            {"id": "HP:0003002", "name": "Breast carcinoma"},
            {"id": "HP:0100013", "name": "Neoplasm of the breast"},
        ]
        assert brca.inheritance == "Autosomal dominant"
        assert json.loads(cftr.hpo_terms) == [
            {"id": "HP:0002110", "name": "Bronchiectasis"},
            {"id": "HP:0006538", "name": "Recurrent pneumonia"},
        ]
        assert cftr.inheritance == "Autosomal recessive"
        assert version_row.version == stats.version
        assert version_row.file_path == str(source_bundle / "gene_disease.9606.tsv.gz")

    def test_retains_only_active_and_previous_valid_source_bundles(
        self,
        monkeypatch,
        reference_engine: sa.Engine,
        mondo_tsv_file: Path,
        hpo_phenotype_file: Path,
        mondo_sssom_file: Path,
        tmp_path: Path,
    ) -> None:
        """Repeated successful refreshes bound managed immutable bundle storage."""
        monkeypatch.setattr("backend.annotation.mondo_hpo.MINIMUM_UNAMBIGUOUS_MONDO_XREFS", 2)
        source_files = {
            "gene_disease.9606.tsv.gz": mondo_tsv_file,
            "genes_to_phenotype.txt": hpo_phenotype_file,
            "mondo.sssom.tsv": mondo_sssom_file,
        }
        generation = {"value": 0}

        def fake_download(url, dest_dir, filename, **kwargs):
            target = dest_dir / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            if filename.endswith(".gz"):
                with gzip.open(target, "wb") as fh:
                    fh.write(source_files[filename].read_bytes())
            else:
                target.write_bytes(source_files[filename].read_bytes())
            meta = kwargs.get("meta")
            if meta is not None:
                meta.update(
                    {
                        "etag": f'"generation-{generation["value"]}-{filename}"',
                        "last_modified": "Wed, 15 Apr 2026 12:34:56 GMT",
                        "version": "20260415",
                    }
                )
            return target

        monkeypatch.setattr("backend.annotation.mondo_hpo.download_file", fake_download)
        downloads = tmp_path / "downloads"
        bundle_names = []
        for value in range(3):
            generation["value"] = value
            download_and_load_mondo_hpo(reference_engine, downloads)
            with reference_engine.connect() as conn:
                primary_path = conn.execute(
                    sa.select(database_versions.c.file_path).where(
                        database_versions.c.db_name == "mondo_hpo"
                    )
                ).scalar_one()
            bundle_names.append(Path(primary_path).parent.name)

        managed = {
            path.name for path in (downloads / "mondo_hpo_sources").iterdir() if path.is_dir()
        }
        assert managed == set(bundle_names[-2:])

        # A source-identical refresh reuses its immutable bundle without churn.
        generation["value"] = 2
        download_and_load_mondo_hpo(reference_engine, downloads)
        assert {
            path.name for path in (downloads / "mondo_hpo_sources").iterdir() if path.is_dir()
        } == managed

    def test_failed_row_load_keeps_previously_recorded_source_bundle(
        self,
        monkeypatch,
        reference_engine: sa.Engine,
        mondo_tsv_file: Path,
        hpo_phenotype_file: Path,
        mondo_sssom_file: Path,
        tmp_path: Path,
    ) -> None:
        """A post-publication failure cannot delete the previously committed provenance."""
        monkeypatch.setattr("backend.annotation.mondo_hpo.MINIMUM_UNAMBIGUOUS_MONDO_XREFS", 2)
        source_files = {
            "gene_disease.9606.tsv.gz": mondo_tsv_file,
            "genes_to_phenotype.txt": hpo_phenotype_file,
            "mondo.sssom.tsv": mondo_sssom_file,
        }
        generation = {"value": 0}

        def fake_download(url, dest_dir, filename, **kwargs):
            target = dest_dir / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            if filename.endswith(".gz"):
                with gzip.open(target, "wb") as fh:
                    fh.write(source_files[filename].read_bytes())
            else:
                target.write_bytes(source_files[filename].read_bytes())
            meta = kwargs.get("meta")
            if meta is not None:
                meta.update(
                    {
                        "etag": f'"generation-{generation["value"]}-{filename}"',
                        "last_modified": "Wed, 15 Apr 2026 12:34:56 GMT",
                        "version": "20260415",
                    }
                )
            return target

        monkeypatch.setattr("backend.annotation.mondo_hpo.download_file", fake_download)
        downloads = tmp_path / "downloads"
        download_and_load_mondo_hpo(reference_engine, downloads)
        with reference_engine.connect() as conn:
            prior_path = conn.execute(
                sa.select(database_versions.c.file_path).where(
                    database_versions.c.db_name == "mondo_hpo"
                )
            ).scalar_one()

        generation["value"] = 1

        def fail_load(*args, **kwargs):
            raise RuntimeError("simulated row load failure")

        monkeypatch.setattr("backend.annotation.mondo_hpo.load_mondo_hpo_rows", fail_load)
        with pytest.raises(RuntimeError, match="simulated row load failure"):
            download_and_load_mondo_hpo(reference_engine, downloads)

        with reference_engine.connect() as conn:
            preserved_path = conn.execute(
                sa.select(database_versions.c.file_path).where(
                    database_versions.c.db_name == "mondo_hpo"
                )
            ).scalar_one()
        assert preserved_path == prior_path
        assert Path(prior_path).is_file()

    def test_refuses_zero_exact_matches_before_clearing_existing_rows(
        self,
        monkeypatch,
        reference_engine: sa.Engine,
        mondo_tsv_file: Path,
        tmp_path: Path,
    ) -> None:
        """A changed/malformed mapping source cannot erase prior HPO context."""
        monkeypatch.setattr("backend.annotation.mondo_hpo.MINIMUM_UNAMBIGUOUS_MONDO_XREFS", 1)
        hpo_path = tmp_path / "unmatched_hpo.txt"
        hpo_path.write_text(
            "ncbi_gene_id\tgene_symbol\thpo_id\thpo_name\tfrequency\tdisease_id\n"
            "672\tBRCA1\tHP:0003002\tBreast carcinoma\t\tOMIM:999999\n",
            encoding="utf-8",
        )
        sssom_path = tmp_path / "unmatched_mondo.sssom.tsv"
        sssom_path.write_text(
            "\n".join(
                [
                    _SSSOM_HEADER,
                    _sssom_line(
                        "MONDO:0011450",
                        "Disease A",
                        "skos:exactMatch",
                        "OMIM:604370",
                        "Disease A",
                        "semapv:UnspecifiedMatching",
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        source_files = {
            "gene_disease.9606.tsv.gz": mondo_tsv_file,
            "genes_to_phenotype.txt": hpo_path,
            "mondo.sssom.tsv": sssom_path,
        }

        def fake_download(url, dest_dir, filename, **kwargs):
            target = dest_dir / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            if filename.endswith(".gz"):
                with gzip.open(target, "wb") as fh:
                    fh.write(source_files[filename].read_bytes())
            else:
                target.write_bytes(source_files[filename].read_bytes())
            return target

        with reference_engine.begin() as conn:
            conn.execute(
                gene_phenotype.insert().values(
                    gene_symbol="PRESERVE",
                    disease_name="Prior disease",
                    disease_id="MONDO:0000001",
                    source="mondo_hpo",
                )
            )
        monkeypatch.setattr("backend.annotation.mondo_hpo.download_file", fake_download)
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        prior_sssom = downloads / "mondo.sssom.tsv"
        prior_sssom.write_text("previous validated source\n", encoding="utf-8")

        with pytest.raises(ValueError, match="no HPO disease scope resolved"):
            download_and_load_mondo_hpo(reference_engine, downloads)

        with reference_engine.connect() as conn:
            preserved = conn.execute(
                sa.select(gene_phenotype.c.gene_symbol).where(
                    gene_phenotype.c.gene_symbol == "PRESERVE"
                )
            ).scalar_one()
        assert preserved == "PRESERVE"
        assert prior_sssom.read_text(encoding="utf-8") == "previous validated source\n"

    def test_refuses_undersized_exact_mapping_before_replacing_rows(
        self,
        monkeypatch,
        reference_engine: sa.Engine,
        mondo_tsv_file: Path,
        hpo_phenotype_file: Path,
        mondo_sssom_file: Path,
        tmp_path: Path,
    ) -> None:
        """A plausibly parsed but truncated SSSOM source cannot clear the cache."""
        monkeypatch.setattr("backend.annotation.mondo_hpo.MINIMUM_UNAMBIGUOUS_MONDO_XREFS", 3)
        source_files = {
            "gene_disease.9606.tsv.gz": mondo_tsv_file,
            "genes_to_phenotype.txt": hpo_phenotype_file,
            "mondo.sssom.tsv": mondo_sssom_file,
        }

        def fake_download(url, dest_dir, filename, **kwargs):
            target = dest_dir / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            if filename.endswith(".gz"):
                with gzip.open(target, "wb") as fh:
                    fh.write(source_files[filename].read_bytes())
            else:
                target.write_bytes(source_files[filename].read_bytes())
            return target

        with reference_engine.begin() as conn:
            conn.execute(
                gene_phenotype.insert().values(
                    gene_symbol="PRESERVE",
                    disease_name="Prior disease",
                    disease_id="MONDO:0000001",
                    source="mondo_hpo",
                )
            )
        monkeypatch.setattr("backend.annotation.mondo_hpo.download_file", fake_download)

        with pytest.raises(ValueError, match="unambiguous exact mappings"):
            download_and_load_mondo_hpo(reference_engine, tmp_path / "downloads")

        with reference_engine.connect() as conn:
            preserved = conn.execute(
                sa.select(gene_phenotype.c.gene_symbol).where(
                    gene_phenotype.c.gene_symbol == "PRESERVE"
                )
            ).scalar_one()
        assert preserved == "PRESERVE"


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
        assert any(term.id == "HP:0003002" for term in first.hpo_term_details)

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

    def test_withholds_dated_gene_wide_install_until_scoped_refresh(
        self, loaded_engine: sa.Engine
    ) -> None:
        """A legacy MONDO source is withheld without hiding other source rows."""
        with loaded_engine.begin() as conn:
            conn.execute(
                gene_phenotype.insert().values(
                    gene_symbol="BRCA1",
                    disease_name="External curated disease",
                    disease_id="OMIM:604370",
                    hpo_terms='["HP:0003002"]',
                    source="omim",
                    inheritance="AD",
                )
            )
        record_mondo_hpo_version(loaded_engine, version="20270101")

        results = lookup_gene_phenotypes(["BRCA1"], loaded_engine)
        assert [annotation.source for annotation in results["BRCA1"]] == ["omim"]
        assert lookup_gene_phenotypes(["BRCA1"], loaded_engine, source_filter="mondo_hpo") == {}

    def test_withholds_unproven_noncanonical_install(self, loaded_engine: sa.Engine) -> None:
        """An uncomparable version cannot prove that MONDO rows are disease-scoped."""
        with loaded_engine.begin() as conn:
            conn.execute(
                gene_phenotype.insert().values(
                    gene_symbol="BRCA1",
                    disease_name="External curated disease",
                    disease_id="OMIM:604370",
                    hpo_terms='["HP:0003002"]',
                    source="omim",
                    inheritance="AD",
                )
            )
        record_mondo_hpo_version(loaded_engine, version="vNext")

        results = lookup_gene_phenotypes(["BRCA1"], loaded_engine)
        assert [annotation.source for annotation in results["BRCA1"]] == ["omim"]
        assert lookup_gene_phenotypes(["BRCA1"], loaded_engine, source_filter="mondo_hpo") == {}


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

    def test_failed_replace_rolls_back_rows_and_version(
        self, monkeypatch, reference_engine: sa.Engine
    ) -> None:
        """A later batch failure leaves both prior rows and version intact."""
        load_mondo_hpo_rows(
            [
                {
                    "gene_symbol": "PRESERVE",
                    "disease_name": "Prior disease",
                    "disease_id": "MONDO:0000001",
                    "hpo_terms": None,
                    "source": "mondo_hpo",
                    "inheritance": None,
                }
            ],
            reference_engine,
            version="20260415+prior-revision",
        )
        monkeypatch.setattr("backend.annotation.mondo_hpo.BATCH_SIZE", 1)
        rows = [
            {
                "gene_symbol": "NEW",
                "disease_name": "New disease",
                "disease_id": "MONDO:0000002",
                "hpo_terms": None,
                "source": "mondo_hpo",
                "inheritance": None,
            },
            {
                "gene_symbol": None,
                "disease_name": "Invalid disease",
                "disease_id": "MONDO:0000003",
                "hpo_terms": None,
                "source": "mondo_hpo",
                "inheritance": None,
            },
        ]

        with pytest.raises(sa.exc.IntegrityError):
            load_mondo_hpo_rows(
                rows,
                reference_engine,
                version="20260415+new-revision",
            )

        with reference_engine.connect() as conn:
            symbols = conn.execute(sa.select(gene_phenotype.c.gene_symbol)).scalars().all()
            version = conn.execute(
                sa.select(database_versions.c.version).where(
                    database_versions.c.db_name == "mondo_hpo"
                )
            ).scalar_one()
        assert symbols == ["PRESERVE"]
        assert version == "20260415+prior-revision"


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
