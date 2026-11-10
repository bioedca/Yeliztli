"""Synthetic regressions for SpliceAI build provenance and atomic imports (#2477)."""

from __future__ import annotations

import gzip
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


def _rows(engine, table, order_by="id"):
    with engine.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                sa.text(f"SELECT * FROM {table} ORDER BY {order_by}")
            ).mappings()
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


@pytest.mark.parametrize("compressed", [False, True])
def test_source_checksum_describes_the_opened_file(install, tmp_path, monkeypatch, compressed):
    import backend.annotation.spliceai as module

    path = _vcf(tmp_path, name="source.vcf.gz" if compressed else "source.vcf")
    original = path.read_bytes()
    replacement = tmp_path / "replacement"
    replaced_bytes = original.replace(b"1\t100\t", b"1\t200\t")
    if compressed:
        original = gzip.compress(original, mtime=0)
        replaced_bytes = gzip.compress(replaced_bytes, mtime=0)
    path.write_bytes(original)
    replacement.write_bytes(replaced_bytes)
    saved = tmp_path / "saved"
    fingerprint = module._source_fingerprint
    parse = module.parse_spliceai_info
    swapped = False
    restored = False

    def fingerprint_and_replace(source):
        nonlocal swapped
        result = fingerprint(source)
        if not swapped:
            path.rename(saved)
            replacement.rename(path)
            swapped = True
        return result

    def parse_and_restore(info):
        nonlocal restored
        if not restored:
            path.rename(replacement)
            saved.rename(path)
            restored = True
        return parse(info)

    monkeypatch.setattr(module, "_source_fingerprint", fingerprint_and_replace)
    monkeypatch.setattr(module, "parse_spliceai_info", parse_and_restore)
    stats = ingest_spliceai_vcf(path, install.scores)
    assert swapped and restored
    assert stats.loaded == 1
    scores = _rows(install.scores, "spliceai_scores")
    assert len(scores) == 1
    assert scores[0]["pos"] == 100
    assert scores[0]["ds_max"] == 0.7
    sources = _rows(install.scores, "spliceai_sources")
    assert len(sources) == 1
    assert sources[0]["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert sources[0]["source_size_bytes"] == len(original)
    assert _health(install).state == "ready"


@pytest.mark.parametrize("compressed", [False, True])
def test_transient_in_place_change_cannot_mislabel_scores(
    install, tmp_path, monkeypatch, compressed
):
    import backend.annotation.spliceai as module

    path = _vcf(tmp_path, name="source.vcf.gz" if compressed else "source.vcf")
    original = path.read_bytes()
    changed_bytes = original.replace(b"1\t100\t", b"1\t200\t")
    if compressed:
        original = gzip.compress(original, mtime=0)
        changed_bytes = gzip.compress(changed_bytes, mtime=0)
    path.write_bytes(original)
    fingerprint = module._source_fingerprint
    parse = module.parse_spliceai_info
    changed = False
    restored = False

    def fingerprint_and_change(source):
        nonlocal changed
        result = fingerprint(source)
        if not changed:
            path.write_bytes(changed_bytes)
            changed = True
        return result

    def parse_and_restore(info):
        nonlocal restored
        path.write_bytes(original)
        restored = True
        return parse(info)

    monkeypatch.setattr(module, "_source_fingerprint", fingerprint_and_change)
    monkeypatch.setattr(module, "parse_spliceai_info", parse_and_restore)
    with pytest.raises(ValueError, match="source changed during ingestion"):
        ingest_spliceai_vcf(path, install.scores)
    assert changed and restored
    assert path.read_bytes() == original
    assert _rows(install.scores, "spliceai_scores") == []
    assert _rows(install.scores, "spliceai_sources") == []


@pytest.mark.parametrize("compressed", [False, True])
def test_in_place_source_change_rolls_back_import(install, tmp_path, monkeypatch, compressed):
    import backend.annotation.spliceai as module

    ingest_spliceai_vcf(_vcf(tmp_path, name="prior.vcf"), install.scores)
    prior_scores = _rows(install.scores, "spliceai_scores")
    prior_sources = _rows(install.scores, "spliceai_sources")
    path = _vcf(
        tmp_path,
        name="source.vcf.gz" if compressed else "source.vcf",
        row=_ROW.replace("1\t100\t", "1\t200\t"),
    )
    if compressed:
        path.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
    parse = module.parse_spliceai_info
    changed = False

    def parse_and_change(info):
        nonlocal changed
        result = parse(info)
        if not changed:
            # Mutate the same inode after its row was read, preserving a valid VCF.
            content = _vcf(tmp_path, name="changed.vcf", row=_ROW).read_bytes()
            path.write_bytes(gzip.compress(content, mtime=0) if compressed else content)
            changed = True
        return result

    monkeypatch.setattr(module, "parse_spliceai_info", parse_and_change)
    with pytest.raises(ValueError, match="source changed during ingestion"):
        ingest_spliceai_vcf(path, install.scores)
    assert changed
    assert _rows(install.scores, "spliceai_scores") == prior_scores
    assert _rows(install.scores, "spliceai_sources") == prior_sources
    assert _health(install).state == "ready"


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


def _child_import_until_released(path, database, started, release):
    engine = sa.create_engine(f"sqlite:///{database}")

    def small_cache(connection, _record):
        connection.execute("PRAGMA cache_size=1")

    sa.event.listen(engine, "connect", small_cache)

    def progress(_count):
        started.set()
        assert release.wait(30), "parent did not release the import"

    try:
        stats = ingest_spliceai_vcf(Path(path), engine, parse_progress=progress)
        assert stats.loaded == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("existing", [False, True])
def test_import_claim_covers_schema_creation_and_commit(install, tmp_path, monkeypatch, existing):
    from backend.annotation import spliceai
    from backend.api.routes.databases import _clean_under_lock

    path = _vcf(tmp_path)
    if existing:
        ingest_spliceai_vcf(path, install.scores)
    observed = []
    original_create = spliceai.create_spliceai_tables

    def assert_protected(phase):
        health = _health(install)
        observed.append((phase, health.state))
        assert health.state == "building"
        assert not health.can_clean and not health.can_verify and not health.can_resume
        assert (
            _clean_under_lock(get_database("spliceai"), install.settings, install.reference)
            == "locked"
        )

    def create(engine):
        assert_protected("before_schema")
        original_create(engine)

    monkeypatch.setattr(spliceai, "create_spliceai_tables", create)
    stats = ingest_spliceai_vcf(
        path, install.scores, parse_progress=lambda _count: assert_protected("before_commit")
    )
    assert stats.loaded == 1
    assert observed == [("before_schema", "building"), ("before_commit", "building")]
    assert install.settings.spliceai_db_path.is_file()
    assert _health(install).state == "ready"
    assert len(_rows(install.scores, "spliceai_scores")) == 1
    assert len(_rows(install.scores, "spliceai_sources")) == 1


def test_cli_process_import_blocks_clean_route(install, tmp_path, monkeypatch):
    import asyncio
    import multiprocessing

    from fastapi import HTTPException

    from backend.api.routes import databases

    pytest.importorskip("fcntl")
    path = _vcf(tmp_path)
    context = multiprocessing.get_context("spawn")
    started, release = context.Event(), context.Event()
    process = context.Process(
        target=_child_import_until_released,
        args=(str(path), str(install.settings.spliceai_db_path), started, release),
    )
    monkeypatch.setattr(databases, "get_settings", lambda: install.settings)
    monkeypatch.setattr(
        databases, "get_registry", lambda: SimpleNamespace(reference_engine=install.reference)
    )
    process.start()
    try:
        assert started.wait(20), "child import did not reach its uncommitted rows"
        with pytest.raises(HTTPException) as error:
            asyncio.run(databases.clean_database("spliceai"))
        assert error.value.status_code == 409
        assert "build" in error.value.detail
        health = _health(install)
        assert health.state == "building"
        assert not health.can_clean and not health.can_verify
        assert install.settings.spliceai_db_path.is_file()
    finally:
        release.set()
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0
    assert _health(install).state == "ready"
    assert len(_rows(install.scores, "spliceai_scores")) == 1
    assert len(_rows(install.scores, "spliceai_sources")) == 1


def test_cleanup_claim_prevents_import_until_unlink_finishes(install, tmp_path, monkeypatch):
    from backend.api.routes import databases

    create_spliceai_tables(install.scores)
    path = _vcf(tmp_path)
    original_clean = databases.clean_database_artifacts
    calls = []

    def clean(db_info, settings, reference):
        with pytest.raises(RuntimeError, match="SpliceAI.*in progress"):
            ingest_spliceai_vcf(path, install.scores)
        calls.append("blocked")
        return original_clean(db_info, settings, reference)

    monkeypatch.setattr(databases, "clean_database_artifacts", clean)
    result = databases._clean_under_lock(
        get_database("spliceai"), install.settings, install.reference
    )
    assert isinstance(result, dict) and result["removed"]
    assert calls == ["blocked"]
    assert not install.settings.spliceai_db_path.exists()
    install.scores.dispose()
    assert ingest_spliceai_vcf(path, install.scores).loaded == 1
    assert _health(install).state == "ready"
    assert (
        databases._clean_under_lock(get_database("spliceai"), install.settings, install.reference)
        == "ready"
    )


def test_failed_import_releases_cleanup_claim(install, tmp_path):
    from backend.api.routes.databases import _clean_under_lock

    with pytest.raises(ValueError):
        ingest_spliceai_vcf(_vcf(tmp_path, row=""), install.scores)
    assert _health(install).state == "corrupt"
    result = _clean_under_lock(get_database("spliceai"), install.settings, install.reference)
    assert isinstance(result, dict) and result["removed"]
    assert not install.settings.spliceai_db_path.exists()
    install.scores.dispose()
    assert ingest_spliceai_vcf(_vcf(tmp_path), install.scores).loaded == 1
    assert _health(install).state == "ready"


@pytest.mark.parametrize("wal", [False, True])
def test_clean_reimport_reopens_warmed_consumer(install, tmp_path, monkeypatch, wal):
    import asyncio

    from backend.api.routes import databases
    from backend.db.connection import DBRegistry

    settings = install.settings.model_copy(update={"wal_mode": wal})
    registry = DBRegistry(settings)
    create_spliceai_tables(install.scores)
    install.scores.dispose()
    monkeypatch.setattr(databases, "get_settings", lambda: settings)
    monkeypatch.setattr(databases, "get_registry", lambda: registry)
    try:
        assert lookup_spliceai_by_variant("1", 100, "A", "G", registry.spliceai_engine) is None
        assert _health(install).state == "corrupt"
        cleaned = asyncio.run(databases.clean_database("spliceai"))
        assert cleaned.db_name == "spliceai" and cleaned.removed
        assert not settings.spliceai_db_path.exists()
        assert ingest_spliceai_vcf(_vcf(tmp_path), install.scores).loaded == 1
        assert _health(install).state == "ready"
        result = lookup_spliceai_by_variant("1", 100, "A", "G", registry.spliceai_engine)
        assert result is not None
        assert result["ds_max"] == 0.7 and result["symbol"] == "SYNTHETIC"
        assert lookup_spliceai_by_variant("1", 100, "A", "T", registry.spliceai_engine) is None
    finally:
        registry.dispose_all()


@pytest.mark.parametrize("drop_manifest", [False, True])
def test_clean_route_preserves_unverified_legacy_import(install, monkeypatch, drop_manifest):
    import asyncio

    from fastapi import HTTPException

    from backend.api.routes import databases

    _legacy(install)
    if drop_manifest:
        with install.scores.begin() as conn:
            conn.execute(sa.text("DROP TABLE spliceai_sources"))
    before = _rows(install.scores, "spliceai_scores")
    versions = _rows(install.reference, "database_versions", "db_name")
    assert before and versions
    monkeypatch.setattr(databases, "get_settings", lambda: install.settings)
    monkeypatch.setattr(
        databases, "get_registry", lambda: SimpleNamespace(reference_engine=install.reference)
    )
    health = _health(install)
    assert health.state == "unverified" and not health.can_clean
    with pytest.raises(HTTPException) as error:
        asyncio.run(databases.clean_database("spliceai"))
    assert error.value.status_code == 409
    assert "re-ingest" in error.value.detail.lower()
    assert install.settings.spliceai_db_path.is_file()
    assert _rows(install.scores, "spliceai_scores") == before
    assert _rows(install.reference, "database_versions", "db_name") == versions
    assert _health(install).state == "unverified"


@pytest.mark.parametrize("wal", [False, True])
@pytest.mark.parametrize("consumer", ["lookup", "status"])
def test_consumers_skip_active_import_without_waiting_for_sqlite(install, tmp_path, wal, consumer):
    import multiprocessing
    import threading

    pytest.importorskip("fcntl")
    if wal:
        with install.scores.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    original = _vcf(tmp_path, name="large-synthetic.vcf", row=_ROW * 3000)
    assert ingest_spliceai_vcf(original, install.scores).loaded == 3000
    reader = sa.create_engine(
        f"sqlite:///{install.settings.spliceai_db_path}", connect_args={"timeout": 30}
    )
    assert lookup_spliceai_by_variant("1", 100, "A", "G", reader)["ds_max"] == 0.7
    replacement = _vcf(tmp_path, name="replacement-synthetic.vcf")
    context = multiprocessing.get_context("spawn")
    started, release = context.Event(), context.Event()
    process = context.Process(
        target=_child_import_until_released,
        args=(str(replacement), str(install.settings.spliceai_db_path), started, release),
    )
    returned = threading.Event()
    results, errors = [], []

    def lookup():
        try:
            if consumer == "lookup":
                results.append(lookup_spliceai_by_variant("1", 100, "A", "G", reader))
            else:
                results.append(
                    get_database_status(get_database("spliceai"), install.settings)["downloaded"]
                )
        except Exception as exc:
            errors.append(exc)
        finally:
            returned.set()

    reader_thread = threading.Thread(target=lookup)
    process.start()
    try:
        assert started.wait(20), "child import did not reach its uncommitted rows"
        reader_thread.start()
        assert returned.wait(5), "lookup waited on SQLite while the import was paused"
        assert not errors and results == [None if consumer == "lookup" else False]
    finally:
        release.set()
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join(5)
        if reader_thread.ident is not None:
            reader_thread.join(20)
            assert not reader_thread.is_alive()
        reader.dispose()
    assert process.exitcode == 0
    try:
        assert lookup_spliceai_by_variant("1", 100, "A", "G", reader)["ds_max"] == 0.7
        assert lookup_spliceai_by_variant("1", 100, "A", "T", reader) is None
    finally:
        reader.dispose()
    assert len(_rows(install.scores, "spliceai_scores")) == 1
    assert get_database_status(get_database("spliceai"), install.settings)["downloaded"] is True
