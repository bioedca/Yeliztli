"""A standalone rebuild must publish at the path and preserve existing readers."""

import hashlib
import multiprocessing
import os
import sqlite3
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
import sqlalchemy as sa

from backend.api.routes import databases
from backend.config import Settings
from backend.db.connection import DBRegistry
from backend.db.database_registry import DATABASES, _record_db_version
from backend.db.tables import database_versions, jobs, reference_metadata

TARGETS = ("gnomad", "dbnsfp", "alphamissense", "gtex_eqtl")
TABLES = {
    "gnomad": "gnomad_af",
    "dbnsfp": "dbnsfp_scores",
    "alphamissense": "alphamissense_scores",
    "gtex_eqtl": "gtex_eqtl",
}


@pytest.fixture(params=[(name, wal) for name in TARGETS for wal in (False, True)])
def rebuild_env(request, tmp_path, monkeypatch):
    name, wal = request.param
    settings = Settings(data_dir=tmp_path, wal_mode=wal)
    settings.downloads_dir.mkdir(parents=True)
    registry = DBRegistry(settings)
    reference_metadata.create_all(registry.reference_engine)
    monkeypatch.setattr(databases, "get_registry", lambda: registry)
    # gnomAD no longer registers a pipeline, but any pipeline routed to this
    # standalone target must obey the same engine/publication contract.
    info = replace(DATABASES[name], build_mode="pipeline")
    target = getattr(registry, f"{name}_engine")
    with target.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE previous (value TEXT)")
        conn.exec_driver_sql("INSERT INTO previous VALUES ('usable-old-data')")
    _record_db_version(registry.reference_engine, name, "old", 123)
    databases._create_job_record(registry.reference_engine, "rebuild", name)

    def build(engine, _downloads, *, reference_engine, **_kwargs):
        with engine.begin() as conn:
            conn.exec_driver_sql(
                f"CREATE TABLE {TABLES[name]} "
                "(rsid TEXT, chrom TEXT, pos INTEGER, ref TEXT, alt TEXT, "
                "homozygous_count INTEGER, marker TEXT)"
            )
            conn.exec_driver_sql(
                f"INSERT INTO {TABLES[name]} VALUES "
                "('synthetic', '1', 1, 'A', 'C', 0, 'replacement')"
            )
        _record_db_version(reference_engine, name, "new", 456)

    def run():
        databases._execute_build(
            db_info=info, job_id="rebuild", engine=registry.reference_engine, settings=settings
        )

    monkeypatch.setattr(databases, "get_build_fn", lambda _name: build)
    yield name, settings, registry, target, build, run
    registry.dispose_all()


def _job(registry):
    with registry.reference_engine.connect() as conn:
        return conn.execute(sa.select(jobs).where(jobs.c.job_id == "rebuild")).one()


def _version(registry, name):
    with registry.reference_engine.connect() as conn:
        return conn.execute(
            sa.select(database_versions.c.version).where(database_versions.c.db_name == name)
        ).scalar_one()


def test_warmed_engine_publishes_at_path_and_reopens(rebuild_env):
    name, settings, registry, retained_engine, _build, run = rebuild_env
    run()
    assert _job(registry).status == "complete", _job(registry).error
    with sqlite3.connect(DATABASES[name].dest_path(settings)) as conn:
        assert conn.execute(f"SELECT marker FROM {TABLES[name]}").fetchall() == [("replacement",)]
        assert conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]
    with retained_engine.connect() as conn:
        assert conn.exec_driver_sql(f"SELECT marker FROM {TABLES[name]}").all() == [
            ("replacement",)
        ]
    assert _version(registry, name) == "new"


def test_failed_publication_preserves_artifact_and_version(rebuild_env, monkeypatch):
    from backend.db import standalone

    name, settings, registry, retained_engine, _build, run = rebuild_env

    def fail(*_args, **_kwargs):
        raise OSError("synthetic metadata commit failure")

    monkeypatch.setattr(standalone, "_record_published_version", fail)
    run()
    assert _job(registry).status == "failed"
    assert "synthetic metadata commit failure" in _job(registry).error
    assert _version(registry, name) == "old"
    with retained_engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT value FROM previous").scalar_one() == "usable-old-data"
    assert list(settings.data_dir.glob(".*.publication.json")) == []
    assert list(settings.data_dir.glob(".*.previous-*")) == []


@pytest.mark.parametrize("rebuild_env", [("gtex_eqtl", True)], indirect=True)
@pytest.mark.parametrize("after_commit", [False, True])
def test_interruption_at_publication_commit_boundary(rebuild_env, monkeypatch, after_commit):
    from backend.db import standalone

    name, settings, registry, retained_engine, _build, run = rebuild_env
    original_record = standalone._record_published_version
    original_replace = standalone.os.replace
    destination = DATABASES[name].dest_path(settings)

    def interrupted_record(*args, **kwargs):
        original_record(*args, **kwargs)
        raise KeyboardInterrupt("synthetic interruption after version commit")

    def interrupted_replace(source, target):
        original_replace(source, target)
        if source.name == "replacement.db":
            raise KeyboardInterrupt("synthetic interruption after rename")

    with monkeypatch.context() as interruption:
        if after_commit:
            interruption.setattr(standalone, "_record_published_version", interrupted_record)
        else:
            interruption.setattr(standalone.os, "replace", interrupted_replace)
        with pytest.raises(KeyboardInterrupt, match="synthetic interruption"):
            run()
    assert _version(registry, name) == ("new" if after_commit else "old")
    with sqlite3.connect(destination) as connection:
        sql = (
            f"SELECT marker FROM {TABLES[name]}" if after_commit else "SELECT value FROM previous"
        )
        assert connection.execute(sql).fetchone()[0] == (
            "replacement" if after_commit else "usable-old-data"
        )
    connection.close()
    if after_commit:
        with pytest.raises(RuntimeError, match="restart to recover"):
            retained_engine.connect()
    restarted = DBRegistry(settings)
    try:
        assert _version(restarted, name) == ("new" if after_commit else "old")
        assert list(settings.data_dir.glob(".*.publication.json")) == []
    finally:
        restarted.dispose_all()


@pytest.mark.parametrize("rebuild_env", [("dbnsfp", True)], indirect=True)
@pytest.mark.parametrize("change_source", [False, True])
@pytest.mark.parametrize("legacy_progress", [False, True])
def test_dbnsfp_retry_reuses_only_matching_source_progress(
    rebuild_env, monkeypatch, change_source, legacy_progress
):
    from backend.annotation import dbnsfp

    name, settings, registry, retained_engine, _build, run = rebuild_env
    archive = settings.downloads_dir / "synthetic-dbnsfp.zip"

    def write_archive(prefix):
        with zipfile.ZipFile(archive, "w") as output:
            for chrom in (1, 2):
                output.writestr(
                    f"dbNSFP5.3.1a_variant.chr{chrom}",
                    "#chr\tpos(1-based)\tref\talt\trs_dbSNP\tCADD_phred\n"
                    f"{chrom}\t1\tA\tC\trsYELIZTLI{prefix}{chrom}\t2.0\n",
                )

    write_archive("OLD")
    monkeypatch.setattr(dbnsfp, "download_dbnsfp", lambda *_args, **_kwargs: archive)
    monkeypatch.setattr(databases, "get_build_fn", lambda _name: dbnsfp.download_and_load_dbnsfp)
    original_record = dbnsfp._record_member_loaded
    original_read = dbnsfp._read_zip_member
    interrupted = False
    reads = []

    def interrupt_after_member(*args):
        nonlocal interrupted
        original_record(*args)
        if not interrupted:
            interrupted = True
            raise RuntimeError("synthetic interruption after completed chromosome")

    def read_member(*args):
        reads.append(args[1])
        yield from original_read(*args)

    monkeypatch.setattr(dbnsfp, "_record_member_loaded", interrupt_after_member)
    monkeypatch.setattr(dbnsfp, "_read_zip_member", read_member)
    run()
    assert _job(registry).status == "failed"
    assert "synthetic interruption" in _job(registry).error
    assert _version(registry, name) == "old"
    assert reads == ["dbNSFP5.3.1a_variant.chr1"]
    with retained_engine.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT value FROM previous").scalar_one()
            == "usable-old-data"
        )
    if legacy_progress:
        # Before private staging, an interrupted loader kept its valid progress
        # at the installed path without a completed version stamp.
        os.replace(
            settings.data_dir / ".dbnsfp-rebuild" / "replacement.db",
            DATABASES[name].dest_path(settings),
        )
        with registry.reference_engine.begin() as connection:
            connection.execute(
                database_versions.delete().where(database_versions.c.db_name == name)
            )
    if change_source:
        write_archive("NEW")
    expected_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    reads.clear()
    run()
    assert _job(registry).status == "complete", _job(registry).error
    assert reads == (
        ["dbNSFP5.3.1a_variant.chr1", "dbNSFP5.3.1a_variant.chr2"]
        if change_source
        else ["dbNSFP5.3.1a_variant.chr2"]
    )
    prefix = "NEW" if change_source else "OLD"
    with retained_engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT rsid FROM dbnsfp_scores ORDER BY rsid"
        ).all() == [(f"rsYELIZTLI{prefix}1",), (f"rsYELIZTLI{prefix}2",)]
        assert connection.exec_driver_sql(
            "SELECT DISTINCT source_token FROM dbnsfp_load_progress"
        ).all() == [(expected_digest,)]
    assert _version(registry, name) == "5.3.1a"


@pytest.mark.parametrize("rebuild_env", [("dbnsfp", True)], indirect=True)
@pytest.mark.parametrize("worker", [False, True])
def test_dbnsfp_publication_retry_keeps_source_until_success(rebuild_env, monkeypatch, worker):
    from backend.annotation import dbnsfp
    from backend.db import connection, database_registry, standalone
    from backend.tasks.huey_tasks import _execute_database_update

    name, settings, registry, retained_engine, _build, run = rebuild_env
    archive = settings.downloads_dir / "synthetic-dbnsfp.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(
            "dbNSFP5.3.1a_variant.chr1",
            "#chr\tpos(1-based)\tref\talt\trs_dbSNP\tCADD_phred\n"
            "1\t1\tA\tC\trsYELIZTLISYNTHETIC\t2.0\n",
        )

    def cached_download(*_args, **_kwargs):
        assert archive.exists(), "Retry must work without downloading another archive"
        return archive

    monkeypatch.setattr(dbnsfp, "download_dbnsfp", cached_download)
    monkeypatch.setattr(databases, "get_build_fn", lambda _name: dbnsfp.download_and_load_dbnsfp)
    if worker:
        monkeypatch.setattr(connection, "get_registry", lambda: registry)
        monkeypatch.setattr(
            database_registry, "get_build_fn", lambda _name: dbnsfp.download_and_load_dbnsfp
        )

        def run():
            _execute_database_update("rebuild", name)

    def failed_commit(*_args):
        raise OSError("synthetic publication failure")

    with monkeypatch.context() as failure:
        failure.setattr(standalone, "_record_published_version", failed_commit)
        run()
    assert _job(registry).status == "failed"
    assert "synthetic publication failure" in _job(registry).error
    assert archive.exists(), "Do not remove the source before publication commits"
    assert _version(registry, name) == "old"
    with retained_engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT value FROM previous").scalar_one() == "usable-old-data"

    def unexpected_parse(*_args):
        yield pytest.fail("Completed members must not be parsed again on publication retry")

    monkeypatch.setattr(dbnsfp, "_read_zip_member", unexpected_parse)
    run()
    assert _job(registry).status == "complete", _job(registry).error
    assert not archive.exists(), "Successful publication releases the transient archive"
    assert _version(registry, name) == "5.3.1a"
    with retained_engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT rsid FROM dbnsfp_scores").all() == [
            ("rsYELIZTLISYNTHETIC",)
        ]


@pytest.mark.parametrize("rebuild_env", [("dbnsfp", True)], indirect=True)
@pytest.mark.parametrize("claim_key", ["dbnsfp", "dbnsfp_staging"])
def test_clean_discards_partial_staging_only_when_unclaimed(rebuild_env, monkeypatch, claim_key):
    from backend.db.build_guard import cross_process_build_claim

    name, settings, registry, _retained, build, run = rebuild_env

    def partial(*args, **kwargs):
        build(*args, **kwargs)
        raise RuntimeError("synthetic partial import")

    monkeypatch.setattr(databases, "get_build_fn", lambda _name: partial)
    run()
    assert _job(registry).status == "failed"
    cache = settings.data_dir / ".dbnsfp-rebuild"
    assert (cache / "replacement.db").exists()
    destination = DATABASES[name].dest_path(settings)
    with cross_process_build_claim(claim_key, settings.data_dir) as acquired:
        assert acquired
        assert (
            databases._clean_under_lock(DATABASES[name], settings, registry.reference_engine)
            == "locked"
        )
        assert cache.exists() and destination.exists()
        assert _version(registry, name) == "old"
    result = databases._clean_under_lock(DATABASES[name], settings, registry.reference_engine)
    assert result["db_name"] == name
    assert str(cache) in result["removed"]
    assert not cache.exists() and not destination.exists()
    with registry.reference_engine.connect() as conn:
        assert (
            conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == name)
            ).all()
            == []
        )


@pytest.mark.parametrize("rebuild_env", [("dbnsfp", True)], indirect=True)
def test_clean_preserves_unrecognized_staging(rebuild_env):
    name, settings, registry, retained, _build, _run = rebuild_env
    cache = settings.data_dir / ".dbnsfp-rebuild"
    cache.mkdir(mode=0o700)
    unrelated = cache / "unrecognized.txt"
    unrelated.write_text("preserve")
    databases._update_job(registry.reference_engine, "rebuild", status="failed")
    with pytest.raises(ValueError, match="Unrecognized standalone staging"):
        databases._clean_under_lock(DATABASES[name], settings, registry.reference_engine)
    assert unrelated.read_text() == "preserve"
    assert _version(registry, name) == "old"
    with retained.connect() as conn:
        assert conn.exec_driver_sql("SELECT value FROM previous").scalar_one() == "usable-old-data"


@pytest.mark.parametrize("rebuild_env", [("gtex_eqtl", True)], indirect=True)
def test_clean_drains_live_readers(rebuild_env):
    name, settings, registry, retained, _build, _run = rebuild_env
    databases._update_job(registry.reference_engine, "rebuild", status="failed")
    entered = threading.Event()

    def clean():
        entered.set()
        return databases._clean_under_lock(DATABASES[name], settings, registry.reference_engine)

    with ThreadPoolExecutor(max_workers=1) as executor:
        with retained.connect() as reader:
            assert (
                reader.exec_driver_sql("SELECT value FROM previous").scalar_one()
                == "usable-old-data"
            )
            future = executor.submit(clean)
            assert entered.wait(5)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.1)
            assert DATABASES[name].dest_path(settings).exists()
            assert _version(registry, name) == "old"
        result = future.result(timeout=5)
    assert result["db_name"] == name
    assert not DATABASES[name].dest_path(settings).exists()


def test_invalid_replacement_preserves_artifact(rebuild_env, monkeypatch):
    name, _settings, registry, retained_engine, build, run = rebuild_env

    def invalid(engine, *args, **kwargs):
        build(engine, *args, **kwargs)
        with engine.begin() as conn:
            conn.exec_driver_sql(f"DELETE FROM {TABLES[name]}")

    monkeypatch.setattr(databases, "get_build_fn", lambda _name: invalid)
    run()
    assert _job(registry).status == "failed"
    assert "Invalid" in _job(registry).error
    assert _version(registry, name) == "old"
    with retained_engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT value FROM previous").scalar_one() == "usable-old-data"


def test_corrupt_header_can_be_rebuilt(rebuild_env):
    name, settings, registry, retained_engine, _build, run = rebuild_env
    DATABASES[name].dest_path(settings).write_bytes(b"synthetic corrupt SQLite header")
    with pytest.raises(sa.exc.DatabaseError):
        with retained_engine.connect() as connection:
            connection.exec_driver_sql("SELECT name FROM sqlite_master").all()
    run()
    assert _job(registry).status == "complete", _job(registry).error
    with retained_engine.connect() as conn:
        assert (
            conn.exec_driver_sql(f"SELECT marker FROM {TABLES[name]}").scalar_one()
            == "replacement"
        )
    assert _version(registry, name) == "new"


def test_replace_permission_error_preserves_old_artifact(rebuild_env, monkeypatch):
    from backend.db import standalone

    name, _settings, registry, retained_engine, _build, run = rebuild_env
    real_replace = standalone.os.replace

    def denied(source, destination):
        if source.name == "replacement.db":
            raise PermissionError("synthetic destination is open")
        return real_replace(source, destination)

    monkeypatch.setattr(standalone.os, "replace", denied)
    run()
    assert _job(registry).status == "failed"
    assert "synthetic destination is open" in _job(registry).error
    assert _version(registry, name) == "old"
    with retained_engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT value FROM previous").scalar_one() == "usable-old-data"


@pytest.mark.parametrize("rebuild_env", [("gtex_eqtl", False)], indirect=True)
def test_publication_without_process_lock_support_refuses_safely(rebuild_env, monkeypatch):
    from backend.db import build_guard

    name, _settings, registry, retained_engine, _build, run = rebuild_env
    monkeypatch.setattr(build_guard, "fcntl", None)
    run()
    assert _job(registry).status == "failed"
    assert "requires Linux, macOS, or WSL" in _job(registry).error
    assert _version(registry, name) == "old"
    with retained_engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT value FROM previous").scalar_one() == "usable-old-data"


def test_timed_out_publication_reopens_entry_gate(tmp_path):
    from backend.db.build_guard import database_access

    def publish():
        with database_access("gnomad", tmp_path, publish=True, timeout=0.05):
            pytest.fail("Publication must not pass an active reader")

    with database_access("gnomad", tmp_path), ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(publish)
        with pytest.raises(TimeoutError, match="active readers"):
            future.result(timeout=5)
        # The timed-out writer must release its entry gate even while the
        # original reader remains active.
        with database_access("gnomad", tmp_path, timeout=0.05):
            pass


def test_unconsumed_cursor_cannot_keep_old_sqlite_handle(rebuild_env):
    name, _settings, registry, retained_engine, _build, run = rebuild_env
    with retained_engine.connect() as conn:
        result = conn.exec_driver_sql("SELECT value FROM previous")
    try:
        run()
        assert _job(registry).status == "complete", _job(registry).error
        with retained_engine.connect() as conn:
            assert (
                conn.exec_driver_sql(f"SELECT marker FROM {TABLES[name]}").scalar_one()
                == "replacement"
            )
    finally:
        result.close()


def test_new_reader_waits_until_replacement_and_version_are_published(rebuild_env, monkeypatch):
    from backend.db import standalone

    name, _settings, registry, retained_engine, _build, run = rebuild_env
    publishing, release, attempting, opened = (threading.Event() for _ in range(4))
    real_replace = standalone.os.replace

    def gated_replace(source, destination):
        if source.name == "replacement.db":
            publishing.set()
            assert release.wait(5)
        return real_replace(source, destination)

    def read():
        attempting.set()
        with retained_engine.connect() as conn:
            opened.set()
            return conn.exec_driver_sql(f"SELECT marker FROM {TABLES[name]}").scalar_one()

    monkeypatch.setattr(standalone.os, "replace", gated_replace)
    with ThreadPoolExecutor(max_workers=2) as executor:
        builder = executor.submit(run)
        try:
            assert publishing.wait(5)
            reader = executor.submit(read)
            assert attempting.wait(5)
            assert not opened.wait(0.1)
            assert _version(registry, name) == "old"
        finally:
            release.set()
        builder.result(timeout=5)
        assert reader.result(timeout=5) == "replacement"
    assert _job(registry).status == "complete", _job(registry).error
    assert _version(registry, name) == "new"


@pytest.mark.parametrize(
    "rebuild_env", [(name, True) for name in TARGETS if name != "gnomad"], indirect=True
)
def test_worker_publishes_standalone_data_and_version(rebuild_env, monkeypatch):
    from backend.db import connection, database_registry
    from backend.tasks.huey_tasks import _execute_database_update

    name, settings, registry, retained_engine, build, _run = rebuild_env
    monkeypatch.setattr(connection, "get_registry", lambda: registry)
    monkeypatch.setattr(database_registry, "get_build_fn", lambda _name: build)
    _execute_database_update("rebuild", name)
    assert _job(registry).status == "complete", _job(registry).error
    assert _version(registry, name) == "new"
    with retained_engine.connect() as conn:
        assert (
            conn.exec_driver_sql(f"SELECT marker FROM {TABLES[name]}").scalar_one()
            == "replacement"
        )
    with sqlite3.connect(DATABASES[name].dest_path(settings)) as conn:
        assert conn.execute(f"SELECT marker FROM {TABLES[name]}").fetchall() == [("replacement",)]


def _crash_at_publication(data_dir, name, wal, after_replace):
    from backend.db import standalone

    settings = Settings(data_dir=data_dir, wal_mode=wal)
    registry = DBRegistry(settings)
    real_replace = standalone.os.replace

    def crash_before(source, destination):
        if source.name == "replacement.db":
            os._exit(73)
        return real_replace(source, destination)

    if after_replace:
        standalone._record_published_version = lambda *_: os._exit(73)
    else:
        standalone.os.replace = crash_before
    with standalone.standalone_build(DATABASES[name], settings, registry.reference_engine) as pair:
        target, versions = pair
        with target.begin() as conn:
            conn.exec_driver_sql(
                f"CREATE TABLE {TABLES[name]} "
                "(rsid TEXT, chrom TEXT, pos INTEGER, ref TEXT, alt TEXT, "
                "homozygous_count INTEGER, marker TEXT)"
            )
            conn.exec_driver_sql(
                f"INSERT INTO {TABLES[name]} VALUES "
                "('synthetic', '1', 1, 'A', 'C', 0, 'replacement')"
            )
        _record_db_version(versions, name, "new", 456)


def _hold_reader(data_dir, name, ready, release):
    registry = DBRegistry(Settings(data_dir=data_dir))
    try:
        with getattr(registry, f"{name}_engine").connect() as conn:
            assert (
                conn.exec_driver_sql("SELECT value FROM previous").scalar_one()
                == "usable-old-data"
            )
            ready.set()
            assert release.wait(10)
            assert (
                conn.exec_driver_sql("SELECT value FROM previous").scalar_one()
                == "usable-old-data"
            )
    finally:
        registry.dispose_all()


@pytest.mark.parametrize("rebuild_env", [("gnomad", True)], indirect=True)
def test_reader_in_another_process_delays_publication(rebuild_env, monkeypatch):
    name, settings, registry, retained_engine, build, run = rebuild_env
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_hold_reader, args=(settings.data_dir, name, ready, release))
    built = threading.Event()

    def staged(*args, **kwargs):
        build(*args, **kwargs)
        built.set()

    monkeypatch.setattr(databases, "get_build_fn", lambda _name: staged)
    process.start()
    try:
        assert ready.wait(10)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run)
            try:
                assert built.wait(5)
                assert _version(registry, name) == "old"
                assert not future.done()
            finally:
                release.set()
            future.result(timeout=5)
        process.join(timeout=5)
        assert process.exitcode == 0
    finally:
        release.set()
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert _job(registry).status == "complete", _job(registry).error
    with retained_engine.connect() as conn:
        assert (
            conn.exec_driver_sql(f"SELECT marker FROM {TABLES[name]}").scalar_one()
            == "replacement"
        )


@pytest.mark.parametrize("after_replace", [False, True])
def test_restart_recovers_only_the_published_artifact(rebuild_env, after_replace):
    name, settings, registry, retained_engine, _build, _run = rebuild_env
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_at_publication,
        args=(settings.data_dir, name, settings.wal_mode, after_replace),
    )
    process.start()
    process.join(timeout=15)
    try:
        assert process.exitcode == 73
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert _version(registry, name) == "old"
    with pytest.raises(RuntimeError, match="restart to recover"):
        retained_engine.connect()
    restarted = DBRegistry(settings)
    try:
        assert _version(restarted, name) == ("new" if after_replace else "old")
        with retained_engine.connect() as conn:
            sql = (
                f"SELECT marker FROM {TABLES[name]}"
                if after_replace
                else "SELECT value FROM previous"
            )
            assert conn.exec_driver_sql(sql).scalar_one() == (
                "replacement" if after_replace else "usable-old-data"
            )
        assert list(settings.data_dir.glob(".*.publication.json")) == []
    finally:
        restarted.dispose_all()


def test_failed_build_preserves_old_file_and_version(rebuild_env, monkeypatch):
    name, settings, registry, retained_engine, build, run = rebuild_env
    destination = DATABASES[name].dest_path(settings)

    def fail(*args, **kwargs):
        build(*args, **kwargs)
        raise RuntimeError("synthetic build failure after version recording")

    monkeypatch.setattr(databases, "get_build_fn", lambda _name: fail)
    run()
    assert _job(registry).status == "failed"
    assert "synthetic build failure" in _job(registry).error
    assert _version(registry, name) == "old"
    with sqlite3.connect(destination) as conn:
        assert conn.execute("SELECT value FROM previous").fetchall() == [("usable-old-data",)]
    with retained_engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT value FROM previous").all() == [("usable-old-data",)]


def test_build_keeps_live_reader_until_publication(rebuild_env, monkeypatch):
    name, _settings, registry, retained_engine, build, run = rebuild_env
    built = threading.Event()

    def staged(*args, **kwargs):
        build(*args, **kwargs)
        built.set()

    monkeypatch.setattr(databases, "get_build_fn", lambda _name: staged)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with retained_engine.connect() as reader:
            assert (
                reader.exec_driver_sql("SELECT value FROM previous").scalar_one()
                == "usable-old-data"
            )
            future = executor.submit(run)
            assert built.wait(5), (
                "The isolated build must finish while a reader holds the old file"
            )
            assert (
                reader.exec_driver_sql("SELECT value FROM previous").scalar_one()
                == "usable-old-data"
            )
            assert _version(registry, name) == "old"
        future.result(timeout=5)
    assert _job(registry).status == "complete", _job(registry).error
    with retained_engine.connect() as conn:
        assert conn.exec_driver_sql(f"SELECT marker FROM {TABLES[name]}").all() == [
            ("replacement",)
        ]
