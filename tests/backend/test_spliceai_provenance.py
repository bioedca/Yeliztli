"""Synthetic regressions for SpliceAI build provenance and atomic imports (#2477)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from backend.annotation.spliceai import (
    create_spliceai_tables,
    ingest_spliceai_vcf,
    lookup_spliceai_by_variant,
    record_spliceai_version,
    spliceai_scores,
)
from backend.config import Settings
from backend.db.database_registry import get_database, get_database_status
from backend.db.db_health import get_database_health, validate_database
from backend.db.tables import database_versions, reference_metadata

_ROW = "1\t100\t.\tA\tG\t.\t.\tSpliceAI=G|SYNTHETIC|0.7|0|0|0|1|2|3|4\n"


def _vcf(
    tmp_path: Path,
    name: str = "synthetic.vcf",
    *,
    header: str = "##reference=GRCh37\n",
    row: str = _ROW,
) -> Path:
    path = tmp_path / name
    path.write_text(
        "##fileformat=VCFv4.2\n" + header + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n" + row
    )
    return path


@pytest.fixture
def install(tmp_path):
    settings = Settings(data_dir=tmp_path)
    reference = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(reference)
    scores = sa.create_engine(f"sqlite:///{settings.spliceai_db_path}")
    yield SimpleNamespace(settings=settings, reference=reference, scores=scores)
    scores.dispose()
    reference.dispose()


def _health(install):
    return get_database_health(get_database("spliceai"), install.settings, install.reference)


def _rows(engine, table):
    with engine.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(sa.text(f"SELECT * FROM {table} ORDER BY id")).mappings()
        ]


def _legacy(install):
    create_spliceai_tables(install.scores)
    with install.scores.begin() as conn:
        conn.execute(
            spliceai_scores.insert(),
            dict(chrom="1", pos=100, ref="A", alt="G", symbol="SYNTHETIC", ds_max=0.7),
        )
    record_spliceai_version(install.reference, genome_build="GRCh37")


@pytest.mark.parametrize(
    "header,confirmation,method",
    [
        ("##reference=GRCh37\n", None, "header"),
        ('##contig=<ID="chr1",length="249250621">\n', None, "header"),
        ("##reference=GRCh37\n", "GRCh37", "header"),
        ("", "GRCh37", "caller_confirmation"),
        ("##contig=<ID=synthetic>\n", "GRCh37", "caller_confirmation"),
    ],
)
def test_verified_import_records_build_evidence(install, tmp_path, header, confirmation, method):
    path = _vcf(tmp_path, header=header)
    stats = ingest_spliceai_vcf(path, install.scores, genome_build=confirmation)
    records = _rows(install.scores, "spliceai_sources")
    assert len(records) == 1
    source = records[0]
    assert source["source_name"] == path.name
    assert source["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert source["source_size_bytes"] == path.stat().st_size
    assert source["genome_build"] == stats.genome_build == "GRCh37"
    assert source["validation_method"] == method
    assert source["caller_confirmation"] == confirmation
    assert json.loads(source["build_header_json"]) == header.splitlines()
    assert source["row_count"] == stats.loaded == 1
    assert source["min_ds"] == 0.2
    assert source["ingestion_revision"] == 1
    assert source["ingested_at"]
    assert _health(install).state == "ready"
    assert validate_database("spliceai", install.settings, deep=True).ok
    assert get_database_status(get_database("spliceai"), install.settings)["downloaded"]
    assert lookup_spliceai_by_variant("1", 100, "A", "G", install.scores)["ds_max"] == 0.7
    assert lookup_spliceai_by_variant("1", 100, "A", "T", install.scores) is None


def test_unrelated_header_fields_are_not_persisted(install, tmp_path):
    path = _vcf(
        tmp_path,
        header='##reference=GRCh37\n##SAMPLE=<ID=synthetic-private-label>\n##INFO=<ID=SpliceAI,Number=.,Type=String,Description="Synthetic">\n',
    )
    ingest_spliceai_vcf(path, install.scores)
    source = _rows(install.scores, "spliceai_sources")[0]
    assert json.loads(source["build_header_json"]) == ["##reference=GRCh37"]
    assert "synthetic-private-label" not in json.dumps(source)
    assert str(tmp_path) not in json.dumps(source)


@pytest.mark.parametrize("drop_provenance", [False, True])
def test_legacy_stamp_cannot_certify_database(install, drop_provenance):
    _legacy(install)
    if drop_provenance:
        with install.scores.begin() as conn:
            conn.execute(sa.text("DROP TABLE IF EXISTS spliceai_sources"))
    assert _rows(install.scores, "spliceai_scores")
    health = _health(install)
    assert health.state == "unverified"
    assert health.present and health.integrity_ok is False
    assert "re-ingest" in health.integrity_detail.lower()
    assert health.can_verify and not health.can_resume and not health.can_clean
    assert not validate_database("spliceai", install.settings, deep=True).ok
    assert not get_database_status(get_database("spliceai"), install.settings)["downloaded"]
    assert lookup_spliceai_by_variant("1", 100, "A", "G", install.scores) is None
    assert _health(install).state == "unverified"  # Verify never upgrades old stamps.


@pytest.mark.parametrize(
    "kind", ["missing_table", "empty_table", "missing_column", "malformed_file"]
)
def test_invalid_score_storage_is_corrupt(install, kind):
    if kind == "malformed_file":
        install.settings.spliceai_db_path.write_bytes(b"not a SQLite database")
    else:
        with install.scores.begin() as conn:
            if kind == "missing_table":
                conn.execute(sa.text("CREATE TABLE unrelated (id INTEGER)"))
            elif kind == "missing_column":
                conn.execute(sa.text("CREATE TABLE spliceai_scores (id INTEGER)"))
                conn.execute(sa.text("INSERT INTO spliceai_scores VALUES (1)"))
        if kind == "empty_table":
            create_spliceai_tables(install.scores)
    assert _health(install).state == "corrupt"
    assert not validate_database("spliceai", install.settings).ok
    assert lookup_spliceai_by_variant("1", 100, "A", "G", install.scores) is None


@pytest.mark.parametrize(
    "column,value",
    [
        ("genome_build", "GRCh38"),
        ("validation_method", "legacy_stamp"),
        ("source_sha256", "not-a-checksum"),
        ("build_header_json", "not json"),
        ("build_header_json", '["##reference=GRCh38"]'),
        ("build_header_json", "[]"),
        ("ingestion_revision", 0),
        ("row_count", 0),
        ("row_count", 0.5),
        ("source_size_bytes", 0),
        ("source_size_bytes", 0.5),
        ("min_ds", 2),
        ("ingested_at", "not-a-date"),
    ],
)
def test_invalid_provenance_never_reaches_consumers(install, tmp_path, column, value):
    ingest_spliceai_vcf(_vcf(tmp_path), install.scores)
    assert lookup_spliceai_by_variant("1", 100, "A", "G", install.scores)
    with install.scores.begin() as conn:
        conn.execute(sa.text(f"UPDATE spliceai_sources SET {column}=:value"), {"value": value})
    assert _health(install).state == "unverified"
    assert not validate_database("spliceai", install.settings, deep=True).ok
    assert lookup_spliceai_by_variant("1", 100, "A", "G", install.scores) is None


def test_append_refuses_to_bless_legacy_rows(install, tmp_path):
    _legacy(install)
    before = _rows(install.scores, "spliceai_scores")
    assert before
    with pytest.raises(ValueError, match="re-ingest"):
        ingest_spliceai_vcf(_vcf(tmp_path), install.scores, clear_existing=False)
    assert _rows(install.scores, "spliceai_scores") == before
    assert _health(install).state == "unverified"


def test_replacement_upgrades_legacy_and_append_preserves_sources(install, tmp_path):
    _legacy(install)
    first = _vcf(tmp_path, "first.vcf")
    second = _vcf(tmp_path, "second.vcf", header="", row=_ROW.replace("\t100\t", "\t200\t"))
    ingest_spliceai_vcf(first, install.scores)
    ingest_spliceai_vcf(second, install.scores, clear_existing=False, genome_build="GRCh37")
    sources = _rows(install.scores, "spliceai_sources")
    assert [row["source_name"] for row in sources] == ["first.vcf", "second.vcf"]
    assert [row["validation_method"] for row in sources] == ["header", "caller_confirmation"]
    assert len(_rows(install.scores, "spliceai_scores")) == 2
    assert _health(install).state == "ready"
    assert lookup_spliceai_by_variant("1", 100, "A", "G", install.scores)
    assert lookup_spliceai_by_variant("1", 200, "A", "G", install.scores)
    ingest_spliceai_vcf(first, install.scores)
    assert len(_rows(install.scores, "spliceai_sources")) == 1
    assert lookup_spliceai_by_variant("1", 200, "A", "G", install.scores) is None


@pytest.mark.parametrize("failure", ["score_insert", "provenance_insert"])
def test_interrupted_write_rolls_back_data_and_provenance(install, tmp_path, failure):
    ingest_spliceai_vcf(_vcf(tmp_path), install.scores)
    before = {
        name: _rows(install.scores, name) for name in ("spliceai_scores", "spliceai_sources")
    }
    assert all(before.values())
    target = "spliceai_scores" if failure == "score_insert" else "spliceai_sources"

    def fail_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith(f"INSERT INTO {target}"):
            raise RuntimeError("synthetic interrupted write")

    sa.event.listen(install.scores, "before_cursor_execute", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="synthetic interrupted write"):
            ingest_spliceai_vcf(
                _vcf(tmp_path, "replacement.vcf", row=_ROW.replace("\t100\t", "\t200\t")),
                install.scores,
            )
    finally:
        sa.event.remove(install.scores, "before_cursor_execute", fail_insert)
    for name, rows in before.items():
        assert _rows(install.scores, name) == rows
    assert _health(install).state == "ready"


@pytest.mark.parametrize(
    "bad_header,bad_row", [("##reference=GRCh38\n", _ROW), ("##reference=GRCh37\n", "malformed\n")]
)
def test_cli_failed_second_file_preserves_entire_install(
    install, tmp_path, monkeypatch, bad_header, bad_row
):
    from scripts import ingest_spliceai_scores as cli

    ingest_spliceai_vcf(_vcf(tmp_path), install.scores)
    record_spliceai_version(install.reference, genome_build="GRCh37", version="old")
    before = {
        name: _rows(install.scores, name) for name in ("spliceai_scores", "spliceai_sources")
    }
    with install.reference.connect() as conn:
        version_before = conn.execute(sa.select(database_versions)).mappings().all()
    assert version_before
    first = _vcf(tmp_path, "first.vcf", row=_ROW.replace("\t100\t", "\t200\t"))
    bad = _vcf(tmp_path, "bad.vcf", header=bad_header, row=bad_row)
    monkeypatch.setattr(
        cli,
        "get_registry",
        lambda: SimpleNamespace(
            settings=install.settings,
            spliceai_engine=install.scores,
            reference_engine=install.reference,
        ),
    )
    with pytest.raises((ValueError, SystemExit)):
        cli.main([str(first), str(bad), "--version", "new"])
    for name, rows in before.items():
        assert _rows(install.scores, name) == rows
    with install.reference.connect() as conn:
        assert conn.execute(sa.select(database_versions)).mappings().all() == version_before
    assert _health(install).state == "ready"


@pytest.mark.parametrize("malformation", ["empty", "schema"])
def test_reingestion_repairs_unverifiable_manifest(install, tmp_path, malformation):
    path = _vcf(tmp_path)
    ingest_spliceai_vcf(path, install.scores)
    with install.scores.begin() as conn:
        if malformation == "empty":
            conn.execute(sa.text("DELETE FROM spliceai_sources"))
        else:
            conn.execute(sa.text("DROP TABLE spliceai_sources"))
            conn.execute(sa.text("CREATE TABLE spliceai_sources (id INTEGER)"))
    assert _health(install).state == "unverified"
    ingest_spliceai_vcf(path, install.scores)
    assert _health(install).state == "ready"
    assert len(_rows(install.scores, "spliceai_sources")) == 1


def test_cli_commits_all_sources_together(install, tmp_path, monkeypatch):
    from scripts import ingest_spliceai_scores as cli

    first = _vcf(tmp_path, "first.vcf")
    second = _vcf(tmp_path, "second.vcf", header="", row=_ROW.replace("\t100\t", "\t200\t"))
    monkeypatch.setattr(
        cli,
        "get_registry",
        lambda: SimpleNamespace(
            settings=install.settings,
            spliceai_engine=install.scores,
            reference_engine=install.reference,
        ),
    )
    assert (
        cli.main([str(first), str(second), "--genome-build", "GRCh37", "--version", "synthetic"])
        == 0
    )
    sources = _rows(install.scores, "spliceai_sources")
    assert [source["source_name"] for source in sources] == ["first.vcf", "second.vcf"]
    assert (
        sum(source["row_count"] for source in sources)
        == len(_rows(install.scores, "spliceai_scores"))
        == 2
    )
    assert validate_database("spliceai", install.settings, deep=True).ok
    with install.reference.connect() as conn:
        stamp = (
            conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "spliceai")
            )
            .mappings()
            .one()
        )
    assert stamp["version"] == "synthetic"
    assert stamp["genome_build"] == "GRCh37"


@pytest.mark.parametrize("min_ds", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_filter_cannot_create_provenance(install, tmp_path, min_ds):
    path = _vcf(tmp_path)
    ingest_spliceai_vcf(path, install.scores)
    before = _rows(install.scores, "spliceai_sources")
    with pytest.raises(ValueError, match="min_ds"):
        ingest_spliceai_vcf(path, install.scores, min_ds=min_ds)
    assert _rows(install.scores, "spliceai_sources") == before
    assert _health(install).state == "ready"


def test_deep_verification_detects_row_count_mismatch(install, tmp_path):
    ingest_spliceai_vcf(_vcf(tmp_path), install.scores)
    assert validate_database("spliceai", install.settings, deep=True).ok
    with install.scores.begin() as conn:
        conn.execute(sa.text("UPDATE spliceai_sources SET row_count=row_count+1"))
    assert not validate_database("spliceai", install.settings, deep=True).ok
