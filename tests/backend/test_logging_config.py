"""Tests for structured logging privacy filters."""

from __future__ import annotations

import json
import logging
import warnings

import pytest
import sqlalchemy as sa
import structlog
from _logging_state import restore_logging_state, snapshot_logging_state

from backend.config import get_settings
from backend.db.connection import get_registry, reset_registry
from backend.db.tables import (
    LOG_ENTRY_PRESENTATION_POLICY_VERSION,
    log_entries,
    reference_metadata,
)
from backend.diagnostics import (
    DATABASE_BUSY,
    GENERIC_EVENT,
    GENERIC_FAILURE,
    NETWORK_BIND_WARNING,
    safe_event,
    storage_failure,
    warn_network_bind,
)
from backend.logging_config import (
    _redact_withheld_prescribing_alert_fields,
    configure_logging,
)


def test_unreviewed_nested_fields_are_excluded_together() -> None:
    event_dict = {
        "event": "analysis_event",
        "has_e4": True,
        "e4_count": 1,
        "genotype": "AG",
        "call_confidence": "high",
        "diplotypes": ["epsilon3/epsilon4"],
        "diplotype_count": 42,
        "rs429358_genotype": "GG",
        "nested": {"diplotype": "*1/*2", "gene": "CYP2C19"},
        "items": [{"haplotype": "H1", "rsid": "rs123"}],
        "input_gt": "0/1",
    }

    assert safe_event(event_dict) == {"event": GENERIC_EVENT}


def test_withheld_prescribing_alert_log_keeps_only_neutral_metadata() -> None:
    event_dict = {
        "event": "pgx_prescribing_alert",
        "logger": "backend.analysis.pharmacogenomics",
        "gene": " CYP2D6 ",
        "drug": "\ttamoxifen\n",
        "recommendation": "Escalate tamoxifen to 40 mg/day.",
        "classification": "A",
    }

    redacted = _redact_withheld_prescribing_alert_fields(event_dict)

    assert redacted == {
        "event": "clinical_guidance_withheld",
        "clinical_guidance_withheld": True,
    }


def test_nested_or_aliased_withheld_log_fields_keep_only_neutral_metadata() -> None:
    """#2019: legacy nested aliases cannot reach a sink as guidance."""
    event_dict = {
        "event": "legacy_pgx_event",
        "logger": "backend.analysis.pharmacogenomics",
        "nested": {
            " gene": " CYP2D6 ",
            " drug": "\ttamoxifen\n",
            "recommendation": "Use alternate hormonal therapy.",
        },
    }

    redacted = _redact_withheld_prescribing_alert_fields(event_dict)

    assert redacted == {
        "event": "clinical_guidance_withheld",
        "clinical_guidance_withheld": True,
    }


def test_malformed_prescribing_identifier_pair_keeps_only_neutral_metadata() -> None:
    """#2019: blank canonical identifiers cannot send legacy guidance to a sink."""
    event_dict = {
        "event": "legacy_pgx_event",
        "logger": "backend.analysis.pharmacogenomics",
        "gene": " \t",
        "drug": "tamoxifen",
        "recommendation": "Use alternate hormonal therapy.",
    }

    redacted = _redact_withheld_prescribing_alert_fields(event_dict)

    assert redacted == {
        "event": "clinical_guidance_withheld",
        "clinical_guidance_withheld": True,
    }


def test_configured_logging_redacts_before_db_and_console(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        logger = structlog.get_logger("tests.logging_privacy")

        logger.info(
            "analysis_event",
            genotype="AG",
            diplotype="*1/*2",
            rs429358_genotype="GG",
            rsid="rs123",
            nested={"haplotype": "H1", "gene": "APOE"},
        )

        stdout = capsys.readouterr().out
        assert "AG" not in stdout
        assert "*1/*2" not in stdout
        assert "GG" not in stdout
        assert "H1" not in stdout
        assert GENERIC_EVENT in stdout
        assert "rs123" not in stdout
        assert "APOE" not in stdout

        with engine.connect() as conn:
            row = conn.execute(
                sa.select(
                    log_entries.c.event_data,
                    log_entries.c.presentation_policy_version,
                ).where(log_entries.c.message == GENERIC_EVENT)
            ).one()

        assert row.event_data is None
        assert row.presentation_policy_version == LOG_ENTRY_PRESENTATION_POLICY_VERSION
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_configured_logging_redacts_nested_guidance_before_db_and_console(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#2019: a nested legacy PGx payload is redacted before either log sink."""
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        structlog.get_logger("tests.logging_privacy").info(
            "legacy_pgx_event",
            nested={
                " gene": "CYP2D6",
                " drug": "tamoxifen",
                "recommendation": "Use alternate hormonal therapy.",
            },
        )

        stdout = capsys.readouterr().out
        assert "CYP2D6" not in stdout
        assert "tamoxifen" not in stdout
        assert "alternate hormonal" not in stdout

        with engine.connect() as conn:
            row = conn.execute(
                sa.select(
                    log_entries.c.event_data,
                    log_entries.c.presentation_policy_version,
                ).where(log_entries.c.message == "clinical_guidance_withheld")
            ).one()

        stored = json.loads(row.event_data)
        assert stored == {"clinical_guidance_withheld": True}
        assert row.presentation_policy_version == LOG_ENTRY_PRESENTATION_POLICY_VERSION
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_configured_logging_redacts_held_event_and_logger_metadata(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A held pair split across event and logger cannot survive either sink."""
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        structlog.get_logger("CYP2D6").info("tamoxifen treatment instruction")

        stdout = capsys.readouterr().out.lower()
        assert "cyp2d6" not in stdout
        assert "tamoxifen" not in stdout

        with engine.connect() as conn:
            logger_name, message, event_data, presentation_policy_version = conn.execute(
                sa.select(
                    log_entries.c.logger,
                    log_entries.c.message,
                    log_entries.c.event_data,
                    log_entries.c.presentation_policy_version,
                ).where(log_entries.c.message == "clinical_guidance_withheld")
            ).one()

        assert logger_name is None
        assert message == "clinical_guidance_withheld"
        assert json.loads(event_data) == {"clinical_guidance_withheld": True}
        assert presentation_policy_version == LOG_ENTRY_PRESENTATION_POLICY_VERSION
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_configured_logging_redacts_held_exception_text(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Traceback rendering cannot create a late prescribing-guidance sink."""
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        try:
            raise ValueError("CYP2D6 tamoxifen dose escalation")
        except ValueError:
            structlog.get_logger("tests.logging_privacy").exception("ordinary failure")

        stdout = capsys.readouterr().out.lower()
        assert "cyp2d6" not in stdout
        assert "tamoxifen" not in stdout
        assert "dose escalation" not in stdout

        with engine.connect() as conn:
            message, event_data, presentation_policy_version = conn.execute(
                sa.select(
                    log_entries.c.message,
                    log_entries.c.event_data,
                    log_entries.c.presentation_policy_version,
                ).where(log_entries.c.message == "clinical_guidance_withheld")
            ).one()

        assert message == "clinical_guidance_withheld"
        assert json.loads(event_data) == {"clinical_guidance_withheld": True}
        assert presentation_policy_version == LOG_ENTRY_PRESENTATION_POLICY_VERSION
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_console_exception_logging_preserves_only_the_reviewed_reason(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        logger = structlog.get_logger("tests.logging_exceptions")

        private_marker = "traceback-private-" + "0123456789abcdef"

        def fail_with_private_local(value: str) -> None:
            source_binding_key = value
            assert source_binding_key
            raise RuntimeError("boom")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                fail_with_private_local(private_marker)
            except RuntimeError:
                logger.exception(
                    "exception_event",
                    genotype="AG",
                    nested={"haplotype": "H1", "gene": "APOE"},
                    rsid="rs123",
                )

        stdout = capsys.readouterr().out
        assert GENERIC_FAILURE in stdout
        assert "exception_event" not in stdout
        assert "AG" not in stdout
        assert "H1" not in stdout
        assert private_marker not in stdout
        assert "Traceback" not in stdout and "boom" not in stdout
        assert not [
            warning
            for warning in caught
            if issubclass(warning.category, UserWarning)
            and "format_exc_info" in str(warning.message)
        ]

        with engine.connect() as conn:
            row = conn.execute(
                sa.select(
                    log_entries.c.event_data,
                    log_entries.c.presentation_policy_version,
                ).where(log_entries.c.message == GENERIC_FAILURE)
            ).one()

        event_data = json.loads(row.event_data)
        assert row.presentation_policy_version == LOG_ENTRY_PRESENTATION_POLICY_VERSION
        assert event_data == {"reason": "runtime_error"}
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_huey_worker_logging_bootstrap_redacts_without_api_startup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("YELIZTLI_DATA_DIR", str(data_dir))
    get_settings.cache_clear()
    reset_registry()
    legacy_engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    with legacy_engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE log_entries ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "timestamp DATETIME, level TEXT NOT NULL, logger TEXT, "
                "message TEXT, event_data TEXT)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO log_entries (level, logger, message, event_data) VALUES "
                "('WARNING', 'backend.analysis.pharmacogenomics', "
                "'pgx_prescribing_alert', "
                '\'{"gene": "CYP2D6", "drug": "tamoxifen"}\')'
            )
        )
    legacy_engine.dispose()
    structlog.reset_defaults()
    try:
        from backend.tasks import huey_tasks

        huey_tasks._worker_logging_schema_engine = None
        huey_tasks._configure_worker_logging()
        logger = structlog.get_logger("tests.worker_logging_privacy")

        logger.info(
            "worker_analysis_event",
            diplotype="epsilon3/epsilon4",
            rs429358_genotype="GG",
            nested={"genotype": "AG"},
            rsid="rs429358",
        )

        stdout = capsys.readouterr().out
        assert "epsilon3/epsilon4" not in stdout
        assert "GG" not in stdout
        assert "AG" not in stdout
        assert GENERIC_EVENT in stdout
        assert "rs429358" not in stdout

        with get_registry().reference_engine.connect() as conn:
            legacy_version = conn.execute(
                sa.select(log_entries.c.presentation_policy_version).where(
                    log_entries.c.message == "pgx_prescribing_alert"
                )
            ).scalar_one()
            policy_version = conn.execute(
                sa.select(log_entries.c.presentation_policy_version).where(
                    log_entries.c.message == GENERIC_EVENT
                )
            ).scalar_one()
            indexes = {row[1] for row in conn.exec_driver_sql("PRAGMA index_list(log_entries)")}
            jobs_table = conn.exec_driver_sql(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
            ).scalar()

        assert legacy_version == 0
        assert policy_version == LOG_ENTRY_PRESENTATION_POLICY_VERSION
        assert "idx_log_entries_presentation_policy_id" in indexes
        assert jobs_table == 1
    finally:
        structlog.reset_defaults()
        reset_registry()
        if "huey_tasks" in locals():
            huey_tasks._worker_logging_schema_engine = None
        get_settings.cache_clear()


def test_reviewed_producer_component_is_persisted(tmp_path) -> None:
    """The Explorer can filter a reviewed producer by its registered component."""
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        storage_failure("busy")

        with engine.connect() as conn:
            logger_name = conn.execute(
                sa.select(log_entries.c.logger).where(log_entries.c.message == DATABASE_BUSY)
            ).scalar_one()

        # The exact name the writer must record — the substring the component
        # filter (`logger.contains(...)`) matches against.
        assert logger_name == "backend.storage"
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_nameless_logger_persists_null_component_not_empty_string(tmp_path) -> None:
    """A logger created with no name records NULL, not "" — a misconfigured chain
    reads as a visible gap rather than a tidy blank column (#1997)."""
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        structlog.get_logger().warning("nameless_event")

        with engine.connect() as conn:
            logger_name = conn.execute(
                sa.select(log_entries.c.logger).where(log_entries.c.message == GENERIC_EVENT)
            ).scalar_one()

        assert logger_name is None
    finally:
        structlog.reset_defaults()
        engine.dispose()


# ── Stdlib logging reaches the Log Explorer (#2126) ──────────────────────────


def test_stdlib_logger_persists_to_log_entries(tmp_path) -> None:
    """The real standard-library warning reaches the Explorer with its component and level."""
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        warn_network_bind()

        with engine.connect() as conn:
            row = conn.execute(
                sa.select(log_entries.c.logger, log_entries.c.level).where(
                    log_entries.c.message == NETWORK_BIND_WARNING
                )
            ).one()

        assert row.logger == "backend.main"
        assert row.level == "WARNING"
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_unreviewed_stdlib_interpolation_uses_the_fallback(tmp_path) -> None:
    """Positional arguments cannot supply durable explanatory text."""
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        logging.getLogger("backend.main").warning(
            "data_directory_not_private: %s", "world-readable"
        )

        with engine.connect() as conn:
            message = conn.execute(
                sa.select(log_entries.c.message).where(log_entries.c.level == "WARNING")
            ).scalar_one()

        assert message == GENERIC_EVENT
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_stdlib_routing_does_not_duplicate_native_events(tmp_path) -> None:
    """Native structlog events are still stored exactly once.

    The routing handler lives on the stdlib root logger. This app's
    ``logger_factory`` is a ``PrintLoggerFactory``, so native events never pass
    through stdlib and cannot be written twice — but a future switch to
    ``structlog.stdlib.LoggerFactory`` would double every row, which is the
    failure this pins.
    """
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        structlog.get_logger("backend.analysis.metabolic").info("native_only_event")

        with engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(log_entries)).scalar_one()

        assert count == 1
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_stdlib_records_are_redacted_before_storage(tmp_path) -> None:
    """Bound request fields cannot enter a standard-library diagnostic."""
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        structlog.contextvars.bind_contextvars(diplotype="*1/*2", sample_rsid_genotype="AG")
        try:
            logging.getLogger("backend.analysis.pharmacogenomics").info("stdlib_in_bound_scope")
        finally:
            structlog.contextvars.clear_contextvars()

        with engine.connect() as conn:
            event_data = conn.execute(
                sa.select(log_entries.c.event_data).where(log_entries.c.message == GENERIC_EVENT)
            ).scalar_one()

        assert event_data is None
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_repeated_configuration_does_not_stack_root_handlers(tmp_path) -> None:
    """``configure_logging`` is idempotent on the root logger.

    The API lifespan configures once, and tests and the Huey worker bootstrap
    configure again in the same process. Appending a handler each time would
    duplicate every stdlib line on the console and every row in the DB.
    """
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        configure_logging(engine_getter=lambda: engine)
        configure_logging(engine_getter=lambda: engine)

        assert len(logging.getLogger("backend").handlers) == 1

        logging.getLogger("backend.main").info("configured_thrice")
        with engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(log_entries)).scalar_one()
        assert count == 1
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_logging_after_deletion_does_not_recreate_the_database(tmp_path) -> None:
    """A log line emitted after the database is deleted must not recreate it.

    SQLite creates the file on connect, so the DB processor would resurrect
    ``reference.db`` moments after the nuclear-delete endpoint removed it,
    leaving a "delete all my data" feature with its own artefact on disk. The
    window became reachable when stdlib logging started flowing through this
    processor (#2126) — the lifespan and shutdown paths log through stdlib, and
    the nuclear-delete tests caught the resurrection.
    """
    structlog.reset_defaults()
    database = tmp_path / "reference.db"
    engine = sa.create_engine(f"sqlite:///{database}")
    reference_metadata.create_all(engine)
    assert database.exists()

    try:
        configure_logging(engine_getter=lambda: engine)
        engine.dispose()
        database.unlink()

        # Both paths into the processor: a native event and a stdlib record.
        structlog.get_logger("backend.analysis.metabolic").info("after_nuclear_delete")
        logging.getLogger("backend.main").info("after_nuclear_delete_stdlib")

        assert not database.exists(), "the log processor recreated a deleted database"
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_withheld_pair_on_a_stdlib_record_is_held_not_crashed(tmp_path) -> None:
    """A withheld prescribing pair logged through stdlib is held, not published.

    The fail-closed path calls ``event_dict.clear()``, which also drops
    ProcessorFormatter's ``_record``/``_from_structlog`` keys;
    ``remove_processors_meta`` then deletes them unconditionally and raised
    ``KeyError``. The formatter aborted, so the neutral replacement was neither
    stored nor rendered, and stdlib's ``handleError`` printed the original
    record's ``Message:`` — publishing the pair that was supposed to be
    withheld. Running the redactor after the meta keys are removed makes the
    clear safe (#2126).
    """
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)
    previously_raising = logging.raiseExceptions

    try:
        logging.raiseExceptions = True
        configure_logging(engine_getter=lambda: engine)
        logging.getLogger("backend.analysis.pharmacogenomics").warning(
            "withheld guidance for CYP2D6 tamoxifen"
        )

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(log_entries.c.message, log_entries.c.event_data)
            ).fetchall()

        # The neutral marker is persisted — not nothing, and not the pair.
        assert len(rows) == 1
        message, event_data = rows[0]
        assert "tamoxifen" not in message.lower()
        assert "cyp2d6" not in message.lower()
        assert "tamoxifen" not in (event_data or "").lower()
    finally:
        logging.raiseExceptions = previously_raising
        structlog.reset_defaults()
        engine.dispose()


def test_stdlib_extra_fields_need_a_reviewed_producer(tmp_path) -> None:
    """Unreviewed IDs and row counts stay out of the stored payload."""
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        logging.getLogger("backend.db.connection").info(
            "sample_delete_cascade", extra={"sample_id": 7, "deleted_rows": 42}
        )

        with engine.connect() as conn:
            event_data = conn.execute(
                sa.select(log_entries.c.event_data).where(log_entries.c.message == GENERIC_EVENT)
            ).scalar_one()

        assert event_data is None
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_stdlib_extra_fields_are_redacted(tmp_path) -> None:
    """Adjacent unreviewed fields are excluded along with sensitive ones."""
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        logging.getLogger("backend.analysis.pharmacogenomics").info(
            "call_recorded", extra={"diplotype": "*1/*2", "gene": "CYP2C19"}
        )

        with engine.connect() as conn:
            event_data = conn.execute(
                sa.select(log_entries.c.event_data).where(log_entries.c.message == GENERIC_EVENT)
            ).scalar_one()

        assert event_data is None
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_logging_against_a_legacy_log_schema_still_persists(tmp_path) -> None:
    """A database predating ``presentation_policy_version`` still records logs.

    The insert names that column, and the DB processor swallows the SQL error,
    so on a legacy upgrade every entry was silently dropped — including the
    startup diagnostics that configuring logging early is meant to preserve.
    The lifespan therefore repairs the log schema before enabling the processor;
    this asserts the repair is what makes the write land (#2126).
    """
    from backend.db.reference_schema import ensure_log_entry_presentation_policy

    structlog.reset_defaults()
    database = tmp_path / "reference.db"
    engine = sa.create_engine(f"sqlite:///{database}")
    reference_metadata.create_all(engine)
    # Reduce the table to its pre-policy shape.
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE log_entries")
        conn.exec_driver_sql(
            "CREATE TABLE log_entries ("
            "id INTEGER PRIMARY KEY, timestamp DATETIME, level VARCHAR, "
            "logger VARCHAR, message TEXT, event_data TEXT)"
        )

    try:
        ensure_log_entry_presentation_policy(engine)
        configure_logging(engine_getter=lambda: engine)
        logging.getLogger("backend.main").info("legacy_schema_startup_diagnostic")

        with engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(log_entries)).scalar_one()

        assert count == 1
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_configure_logging_replaces_rather_than_accumulates(tmp_path) -> None:
    """Configuring logging replaces the ``backend`` handler, never stacks one.

    The handler carries the **database** processor, so an accumulated one is a
    second live writer, and a leaked one is a writer bound to a finished test's
    engine. ``logging.basicConfig`` was harmless to leak because it only reached
    stderr; this is not, which is why ``conftest.py`` restores the logger around
    every test (#2126).

    Note the process already has a handler here: ``backend/tasks/huey_tasks.py``
    installs one at import time, so the invariant is "exactly one", not "one
    more than before".
    """
    backend_logger = logging.getLogger("backend")
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)
    try:
        configure_logging(engine_getter=lambda: engine)
        assert len(backend_logger.handlers) == 1
        first = backend_logger.handlers[0]

        configure_logging(engine_getter=lambda: engine)
        assert len(backend_logger.handlers) == 1
        # Replaced, not reused-in-place and not appended.
        assert backend_logger.handlers[0] is not first
        assert backend_logger.level == logging.INFO
    finally:
        engine.dispose()


def test_identifying_extras_are_not_persisted(tmp_path) -> None:
    """Names, IDs and nested child details stay out of a deletion diagnostic."""
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        logging.getLogger("backend.services.sample_delete").info(
            "sample_delete_cascade",
            extra={
                "deleted_sample_id": 7,
                "deleted_sample_name": "Alice DNA",
                "deleted_merged_children": [{"id": 8, "name": "Alice v5"}],
            },
        )

        with engine.connect() as conn:
            event_data = conn.execute(
                sa.select(log_entries.c.event_data).where(log_entries.c.message == GENERIC_EVENT)
            ).scalar_one()

        stored = event_data or ""
        assert "Alice DNA" not in stored
        assert "Alice v5" not in stored, "a nested child name survived"
        assert event_data is None
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_default_configuration_installs_no_root_handler(tmp_path) -> None:
    """Leave host handlers installed and allow them to observe the sanitized record."""
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)
    root = logging.getLogger()
    before = root.handlers[:]
    try:
        configure_logging(engine_getter=lambda: engine)
        assert root.handlers == before, "configure_logging must not touch the root logger"
        assert len(logging.getLogger("backend").handlers) == 1
        # Propagation stays on, so caplog and host integrations keep working.
        assert logging.getLogger("backend").propagate is True
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_restoring_logging_state_puts_back_the_prior_structlog_chain(tmp_path) -> None:
    """The autouse fixture's restore must reinstate the previous configuration.

    ``configure_logging`` replaces structlog's global processor chain with one
    that has an engine getter bound into it, so a test that configures logging
    and only disposes its engine leaves that chain live — and a later test's
    *native* structlog event then reconnects through a disposed engine, writing
    to a temporary database that belongs to nobody.

    Restoring is not the same as resetting. Test modules import ``huey_tasks``,
    which installs the production DB/redaction chain at module scope, so
    ``structlog.reset_defaults()`` would leave every later test running under a
    chain production never uses — passing while no longer exercising the real
    behaviour.

    Exercises the helper the fixture delegates to, in one test: an earlier
    version of this check was split across a pair and read state the companion
    had left behind, so selecting it alone failed even when cleanup worked.
    """
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)
    outer = snapshot_logging_state()

    try:
        # A handler installed *before* the snapshot, standing in for the one
        # ``huey_tasks`` installs at import. Without it this module's ``backend``
        # logger starts with an empty handler list, so every later assertion
        # about reinstating handlers compares [] to [] and holds whether or not
        # the restore works.
        backend_logger = logging.getLogger("backend")
        sentinel = logging.NullHandler()
        backend_logger.addHandler(sentinel)

        prior = snapshot_logging_state()
        assert sentinel in prior.handlers
        configure_logging(engine_getter=lambda: engine)
        installed = structlog.get_config()["processors"]
        assert installed is not prior.structlog_config["processors"], (
            "configure_logging did not install its own chain, so this proves nothing"
        )

        restore_logging_state(prior)

        restored = structlog.get_config()["processors"]
        assert restored is prior.structlog_config["processors"], (
            "the prior structlog chain was not reinstated"
        )
        assert restored is not installed, "the test's own chain survived the restore"

        # The handler list is reinstated, not merely pruned of what the test
        # added. ``huey_tasks`` installs a handler on this logger at import, so
        # a test that clears the list must not leave later tests with none —
        # which is the failure the removal loop alone cannot undo.
        backend_logger.handlers.clear()
        restore_logging_state(prior)
        assert sentinel in backend_logger.handlers, (
            "a cleared handler list was not reinstated, so a test that clears "
            "handlers leaves every later test with none"
        )
        assert backend_logger.handlers == prior.handlers
    finally:
        restore_logging_state(outer)
        engine.dispose()


def test_path_extras_exclude_the_basename_and_directory(tmp_path) -> None:
    """User-selected filenames and their parent directories cannot enter the fallback."""
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        logging.getLogger("backend.services.sample_delete").warning(
            "merged_sample_db_missing",
            extra={
                "merged_sample_id": 4,
                "db_path": "/home/Alice/My Genomes/samples/sample_4.db",
            },
        )

        with engine.connect() as conn:
            event_data = conn.execute(
                sa.select(log_entries.c.event_data).where(log_entries.c.message == GENERIC_EVENT)
            ).scalar_one()

        stored = event_data or ""
        assert "Alice" not in stored, f"the account name was persisted: {stored}"
        assert "My Genomes" not in stored, "the user's chosen directory was persisted"
        assert event_data is None
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_new_path_field_names_are_also_excluded(tmp_path) -> None:
    """A new extra field cannot admit a directory or a basename."""
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        logging.getLogger("backend.ingestion.liftover").warning(
            "liftover_chain_missing_fallback_to_web",
            extra={"expected_path": "/home/Alice/My Genomes/chains/hg19ToHg38.over.chain.gz"},
        )

        with engine.connect() as conn:
            event_data = conn.execute(
                sa.select(log_entries.c.event_data).where(log_entries.c.message == GENERIC_EVENT)
            ).scalar_one()

        stored = event_data or ""
        assert "Alice" not in stored, f"the account name was persisted: {stored}"
        assert "My Genomes" not in stored
        assert event_data is None
    finally:
        structlog.reset_defaults()
        engine.dispose()
