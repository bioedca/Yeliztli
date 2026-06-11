"""Ingestion tests for ClinGen gene-disease validity (SW-A11 / #14)."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.annotation.clingen import (
    CLINGEN_DATA_DIR,
    download_and_load_clingen,
    load_clingen_from_csv,
    lookup_gene_validities,
    lookup_gene_validity,
    parse_clingen_csv,
    parse_file_created_date,
)
from backend.db.tables import clingen_gene_validity, reference_metadata

# A minimal CSV that reproduces ClinGen's real preamble: title, FILE CREATED,
# webpage, "+++" separators around the header, then data rows.
_FIXTURE_CSV = (
    '"CLINGEN GENE DISEASE VALIDITY CURATIONS","","","","","","","","",""\n'
    '"FILE CREATED: 2025-01-02","","","","","","","","",""\n'
    '"WEBPAGE: https://search.clinicalgenome.org/kb/gene-validity","","","","","","","","",""\n'
    '"+++++++++++","++++++++++++++","+++++++++++++","++++++++++++++++++","+++++++++","'
    '+++++++++","++++++++++++++","+++++++++++++","+++++++++++++++++++","+++++++++++++++++++"\n'
    '"GENE SYMBOL","GENE ID (HGNC)","DISEASE LABEL","DISEASE ID (MONDO)","MOI","SOP",'
    '"CLASSIFICATION","ONLINE REPORT","CLASSIFICATION DATE","GCEP"\n'
    '"+++++++++++","++++++++++++++","+++++++++++++","++++++++++++++++++","+++++++++","'
    '+++++++++","++++++++++++++","+++++++++++++","+++++++++++++++++++","+++++++++++++++++++"\n'
    '"BRCA1","HGNC:1100","hereditary breast ovarian cancer syndrome","MONDO:0003582",'
    '"AD","SOP10","Definitive","https://example/report1","2023-01-01T00:00:00.000Z",'
    '"Breast Cancer GCEP"\n'
    '"TTN","HGNC:12403","dilated cardiomyopathy","MONDO:0005021","AD","SOP9","Limited",'
    '"https://example/report2","2022-06-01T00:00:00.000Z","Cardiomyopathy GCEP"\n'
    '"FOO1","HGNC:9999","some disputed disease","MONDO:0000001","AR","SOP8","Disputed",'
    '"https://example/report3","2021-03-01T00:00:00.000Z","General GCEP"\n'
    '"BADROW","HGNC:0","junk disease","MONDO:0000002","AD","SOP1","NotARealTier",'
    '"https://example/report4","2020-01-01T00:00:00.000Z","General GCEP"\n'
)


@pytest.fixture
def fixture_csv(tmp_path: Path) -> Path:
    p = tmp_path / "clingen.csv"
    p.write_text(_FIXTURE_CSV, encoding="utf-8")
    return p


@pytest.fixture
def ref_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    return engine


def test_parse_file_created_date(fixture_csv: Path) -> None:
    assert parse_file_created_date(fixture_csv) == "2025-01-02"


def test_parse_skips_preamble_and_bad_tiers(fixture_csv: Path) -> None:
    rows, stats = parse_clingen_csv(fixture_csv)
    # Three valid rows; the unknown-tier row is skipped, not mislabelled.
    assert stats.rows_loaded == 3
    assert stats.rows_skipped == 1
    genes = {r["gene_symbol"] for r in rows}
    assert genes == {"BRCA1", "TTN", "FOO1"}
    brca1 = next(r for r in rows if r["gene_symbol"] == "BRCA1")
    assert brca1["classification"] == "Definitive"
    assert brca1["disease_id"] == "MONDO:0003582"
    assert brca1["moi"] == "AD"


def test_parse_raises_when_header_missing(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text('"NOPE","NADA"\n"x","y"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="GENE SYMBOL"):
        parse_clingen_csv(bad)


def test_load_and_lookup(fixture_csv: Path, ref_engine: sa.Engine) -> None:
    stats = load_clingen_from_csv(fixture_csv, ref_engine)
    assert stats.rows_loaded == 3
    assert stats.version == "2025-01-02"  # from FILE CREATED, not wall clock

    with ref_engine.connect() as conn:
        total = conn.execute(
            sa.select(sa.func.count()).select_from(clingen_gene_validity)
        ).scalar_one()
    assert total == 3

    found = lookup_gene_validities(ref_engine, ["BRCA1", "TTN", "NOSUCHGENE"])
    assert set(found) == {"BRCA1", "TTN"}  # uncurated gene simply absent
    assert found["TTN"][0]["classification"] == "Limited"
    assert lookup_gene_validity(ref_engine, "FOO1")[0]["classification"] == "Disputed"
    assert lookup_gene_validity(ref_engine, None) == []


def test_load_is_idempotent(fixture_csv: Path, ref_engine: sa.Engine) -> None:
    load_clingen_from_csv(fixture_csv, ref_engine)
    load_clingen_from_csv(fixture_csv, ref_engine)  # clear_existing=True default
    with ref_engine.connect() as conn:
        total = conn.execute(
            sa.select(sa.func.count()).select_from(clingen_gene_validity)
        ).scalar_one()
    assert total == 3


def test_version_recorded(fixture_csv: Path, ref_engine: sa.Engine) -> None:
    from backend.db.update_manager import get_current_version

    load_clingen_from_csv(fixture_csv, ref_engine)
    assert get_current_version(ref_engine, "clingen") == "2025-01-02"


def test_committed_snapshot_parses() -> None:
    """The committed real ClinGen CSV parses with zero unparseable rows."""
    csv_path = CLINGEN_DATA_DIR / "clingen_gene_validity.csv"
    assert csv_path.exists()
    rows, stats = parse_clingen_csv(csv_path)
    assert stats.rows_loaded > 3000
    assert stats.rows_skipped == 0
    # Every row carries a recognised classification.
    from backend.annotation.clingen import VALID_CLASSIFICATIONS

    assert {r["classification"] for r in rows} <= VALID_CLASSIFICATIONS


def test_download_and_load_uses_committed_csv(ref_engine: sa.Engine, tmp_path: Path) -> None:
    stats = download_and_load_clingen(ref_engine, tmp_path)
    assert stats.rows_loaded > 3000
    # A well-known Definitive gene-disease pair is present.
    curations = lookup_gene_validity(ref_engine, "BRCA1")
    assert any(c["classification"] == "Definitive" for c in curations)


def test_empty_load_refuses_to_wipe(fixture_csv: Path, ref_engine: sa.Engine) -> None:
    """A clear_existing load with 0 rows must NOT wipe the curated table."""
    from backend.annotation.clingen import load_clingen_into_db

    load_clingen_from_csv(fixture_csv, ref_engine)  # 3 rows present
    with pytest.raises(ValueError, match="0 rows"):
        load_clingen_into_db([], ref_engine, clear_existing=True)
    with ref_engine.connect() as conn:
        total = conn.execute(
            sa.select(sa.func.count()).select_from(clingen_gene_validity)
        ).scalar_one()
    assert total == 3  # untouched
    # A non-destructive empty load is a safe no-op (no raise).
    load_clingen_into_db([], ref_engine, clear_existing=False)
