"""Tests for structured logging privacy filters."""

from __future__ import annotations

import json
import logging
import warnings

import pytest
import sqlalchemy as sa
import structlog

from backend.config import get_settings
from backend.db.connection import get_registry, reset_registry
from backend.db.tables import (
    LOG_ENTRY_PRESENTATION_POLICY_VERSION,
    log_entries,
    reference_metadata,
)
from backend.logging_config import (
    _REDACTED_LOG_VALUE,
    _redact_sensitive_log_fields,
    _redact_withheld_prescribing_alert_fields,
    configure_logging,
)


def test_redact_sensitive_log_fields_recursively() -> None:
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

    redacted = _redact_sensitive_log_fields(None, "info", event_dict)

    assert redacted["has_e4"] == _REDACTED_LOG_VALUE
    assert redacted["e4_count"] == _REDACTED_LOG_VALUE
    assert redacted["genotype"] == _REDACTED_LOG_VALUE
    assert redacted["rs429358_genotype"] == _REDACTED_LOG_VALUE
    assert redacted["call_confidence"] == "high"
    assert redacted["diplotypes"] == _REDACTED_LOG_VALUE
    assert redacted["diplotype_count"] == 42
    assert redacted["nested"] == {
        "diplotype": _REDACTED_LOG_VALUE,
        "gene": "CYP2C19",
    }
    assert redacted["items"] == [{"haplotype": _REDACTED_LOG_VALUE, "rsid": "rs123"}]
    assert redacted["input_gt"] == _REDACTED_LOG_VALUE


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
        assert _REDACTED_LOG_VALUE in stdout
        assert "rs123" in stdout

        with engine.connect() as conn:
            row = conn.execute(
                sa.select(
                    log_entries.c.event_data,
                    log_entries.c.presentation_policy_version,
                ).where(log_entries.c.message == "analysis_event")
            ).one()

        event_data = json.loads(row.event_data)
        assert row.presentation_policy_version == LOG_ENTRY_PRESENTATION_POLICY_VERSION
        assert event_data["genotype"] == _REDACTED_LOG_VALUE
        assert event_data["diplotype"] == _REDACTED_LOG_VALUE
        assert event_data["rs429358_genotype"] == _REDACTED_LOG_VALUE
        assert event_data["nested"] == {
            "haplotype": _REDACTED_LOG_VALUE,
            "gene": "APOE",
        }
        assert event_data["rsid"] == "rs123"
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


def test_console_exception_logging_does_not_warn_and_persists_traceback(
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
        assert "exception_event" in stdout
        assert "AG" not in stdout
        assert "H1" not in stdout
        assert private_marker not in stdout
        assert _REDACTED_LOG_VALUE in stdout
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
                ).where(log_entries.c.message == "exception_event")
            ).one()

        event_data = json.loads(row.event_data)
        assert row.presentation_policy_version == LOG_ENTRY_PRESENTATION_POLICY_VERSION
        assert event_data["genotype"] == _REDACTED_LOG_VALUE
        assert event_data["nested"] == {
            "haplotype": _REDACTED_LOG_VALUE,
            "gene": "APOE",
        }
        assert event_data["rsid"] == "rs123"
        assert "exc_info" not in event_data
        assert "RuntimeError: boom" in event_data["exception"]
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
        assert _REDACTED_LOG_VALUE in stdout
        assert "rs429358" in stdout

        with get_registry().reference_engine.connect() as conn:
            legacy_version = conn.execute(
                sa.select(log_entries.c.presentation_policy_version).where(
                    log_entries.c.message == "pgx_prescribing_alert"
                )
            ).scalar_one()
            policy_version = conn.execute(
                sa.select(log_entries.c.presentation_policy_version).where(
                    log_entries.c.message == "worker_analysis_event"
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


def test_logger_name_persists_as_component(tmp_path) -> None:
    """A log line emitted through the configured pipeline persists its logger
    name — the value the Log Explorer's Component column and filter read (#1997).

    The route + writer were each fine in isolation; only their composition was
    broken (the processor chain omitted add_logger_name, so every row was written
    with an empty component). This spans the composition, so it fails on the
    pre-fix chain where a hand-seeded fixture could not.
    """
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        structlog.get_logger("backend.analysis.metabolic").info("prs_findings_stored")

        with engine.connect() as conn:
            logger_name = conn.execute(
                sa.select(log_entries.c.logger).where(
                    log_entries.c.message == "prs_findings_stored"
                )
            ).scalar_one()

        # The exact name the writer must record — the substring the component
        # filter (`logger.contains(...)`) matches against.
        assert logger_name == "backend.analysis.metabolic"
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
                sa.select(log_entries.c.logger).where(log_entries.c.message == "nameless_event")
            ).scalar_one()

        assert logger_name is None
    finally:
        structlog.reset_defaults()
        engine.dispose()


# ── Stdlib logging reaches the Log Explorer (#2126) ──────────────────────────


def test_stdlib_logger_persists_to_log_entries(tmp_path) -> None:
    """A ``logging.getLogger`` call reaches the DB, so the Log Explorer shows it.

    ``configure_logging`` used to end with ``logging.basicConfig(...)`` under a
    comment claiming it routed stdlib logging through structlog. It did not:
    ``basicConfig`` installs a plain ``StreamHandler`` that formats the record
    itself and never touches a processor, so all 65 ``logging.getLogger``
    call sites in ``backend/`` wrote to stderr and nothing else. Verified against
    a real database before the fix — ``DBRegistry initialised``, logged every
    boot, had zero rows in ``log_entries``.

    This asserts the message, the level and the component, because a route that
    stored the text under an empty logger would still leave the Component filter
    blind to every stdlib site.
    """
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        logging.getLogger("backend.main").info(
            "DBRegistry initialised (reference.db engine ready)"
        )

        with engine.connect() as conn:
            row = conn.execute(
                sa.select(log_entries.c.logger, log_entries.c.level).where(
                    log_entries.c.message == "DBRegistry initialised (reference.db engine ready)"
                )
            ).one()

        assert row.logger == "backend.main"
        assert row.level == "INFO"
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_stdlib_percent_formatting_persists_interpolated(tmp_path) -> None:
    """``%s`` arguments are interpolated before storage, not stored as a template.

    The lifespan's surviving stdlib sites use ``%``-style arguments precisely
    because a stdlib logger rejects keywords, so storing ``"...: %s"`` with the
    value dropped would make those rows useless.
    """
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

        assert message == "data_directory_not_private: world-readable"
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
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(log_entries)
                .where(log_entries.c.message == "native_only_event")
            ).scalar_one()

        assert count == 1
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_stdlib_records_are_redacted_before_storage(tmp_path) -> None:
    """A stdlib record is redacted on the same terms as a native event.

    The vector is a **bound contextvar**, not a keyword: a stdlib logger cannot
    take structured keywords at all, and ``extra=`` is not merged into the event
    dict by ``ProcessorFormatter``. What *does* reach a foreign record is
    anything bound with ``bind_contextvars`` — request middleware or an analysis
    routine binds a diplotype, and any stdlib line logged inside that scope
    would carry it. Routing stdlib logs into the database must not turn that
    into a privacy bypass, so the redactor runs in the foreign pre-chain.
    """
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
                sa.select(log_entries.c.event_data).where(
                    log_entries.c.message == "stdlib_in_bound_scope"
                )
            ).scalar_one()

        stored = event_data or ""
        # The keys survive (they are useful structure); the values must not.
        assert "*1/*2" not in stored
        assert '"AG"' not in stored
        assert _REDACTED_LOG_VALUE in stored
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
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(log_entries)
                .where(log_entries.c.message == "configured_thrice")
            ).scalar_one()
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


def test_stdlib_extra_fields_reach_the_payload(tmp_path) -> None:
    """``extra={...}`` survives into the stored payload.

    ProcessorFormatter builds a foreign event dict from the rendered message and
    metadata only, so without ``ExtraAdder`` these fields are dropped and events
    like ``sample_delete_cascade`` would reach the Log Explorer as a bare name
    with their ids and counts discarded (#2126).
    """
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
                sa.select(log_entries.c.event_data).where(
                    log_entries.c.message == "sample_delete_cascade"
                )
            ).scalar_one()

        assert event_data is not None
        assert json.loads(event_data) == {"sample_id": 7, "deleted_rows": 42}
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_stdlib_extra_fields_are_redacted(tmp_path) -> None:
    """Imported ``extra`` fields are sanitised on the same terms as the rest.

    ``ExtraAdder`` runs before the redactor precisely so a sensitive field
    cannot enter the payload by the stdlib route and skip redaction.
    """
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
                sa.select(log_entries.c.event_data).where(log_entries.c.message == "call_recorded")
            ).scalar_one()

        stored = json.loads(event_data)
        assert stored["diplotype"] == _REDACTED_LOG_VALUE
        # A non-sensitive neighbour is kept, so the redaction is targeted.
        assert stored["gene"] == "CYP2C19"
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
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(log_entries)
                .where(log_entries.c.message == "legacy_schema_startup_diagnostic")
            ).scalar_one()

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
    """A deleted sample's name must not survive in the Log Explorer (#2126).

    ``sample_delete_cascade`` passes ``deleted_sample_name`` and a list of child
    ``{"id", "name"}`` objects through stdlib ``extra={...}``. Those only ever
    reached stderr before; once ``ExtraAdder`` imports extras they would be
    written to ``log_entries`` durably — so deleting a sample would remove its
    row and its database while leaving its name visible indefinitely, which
    inverts the point of the deletion.

    Ids and counts are deliberately kept: they make the entry useful for triage
    and identify nothing on their own.
    """
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
                sa.select(log_entries.c.event_data).where(
                    log_entries.c.message == "sample_delete_cascade"
                )
            ).scalar_one()

        stored = event_data or ""
        assert "Alice DNA" not in stored
        assert "Alice v5" not in stored, "a nested child name survived"
        # The useful, non-identifying structure is retained.
        payload = json.loads(stored)
        assert payload["deleted_sample_id"] == 7
        assert payload["deleted_sample_name"] == _REDACTED_LOG_VALUE
        assert payload["deleted_merged_children"][0]["id"] == 8
        assert payload["deleted_merged_children"][0]["name"] == _REDACTED_LOG_VALUE
    finally:
        structlog.reset_defaults()
        engine.dispose()


def test_default_configuration_installs_no_root_handler(tmp_path) -> None:
    """The replacement for ``basicConfig`` leaves the root logger alone.

    This is the exposure that actually got smaller: ``basicConfig`` installed a
    root handler that printed every stdlib record **raw**, unconditionally. The
    routing handler lives on the ``backend`` logger instead, so with no root
    handler installed a propagated record reaches nothing and the only rendered
    copy is the redacted one.

    What this does *not* claim: propagation is still on, so a host or test
    harness that installs its own root handler does receive the unredacted
    original. Closing that needs record-level sanitisation in a logger-level
    filter — turning propagation off instead silences ``caplog`` process-wide,
    because this module is configured at import by the Huey bootstrap. Tracked
    separately (#2126 review).
    """
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


def _db_processor_in_structlog_chain() -> bool:
    """True when structlog's global chain still carries a DB-writing processor."""
    return any(
        getattr(processor, "__name__", "") == "db_processor"
        for processor in structlog.get_config().get("processors", [])
    )


def test_configuring_logging_without_a_reset_is_cleaned_up_a(tmp_path) -> None:
    """Configure logging and leave structlog configured, as a careless test would.

    Deliberately omits ``structlog.reset_defaults()`` in cleanup — the shape
    Codex flagged on this PR: the global processor chain keeps the engine getter
    bound, so a later test's *native* event reconnects through an engine whose
    database has been disposed and deleted. The autouse fixture in
    ``conftest.py`` is what contains it, and its companion below is what proves
    that (#2126).
    """
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)
    configure_logging(engine_getter=lambda: engine)
    assert _db_processor_in_structlog_chain()
    engine.dispose()


def test_configuring_logging_without_a_reset_is_cleaned_up_b() -> None:
    """The previous test's processor chain must not still be installed.

    Without the fixture's ``structlog.reset_defaults()`` this fails, because the
    chain from the test above survives with its disposed engine still bound.
    """
    assert not _db_processor_in_structlog_chain(), (
        "structlog still carries the previous test's DB processor, bound to an "
        "engine whose database is gone"
    )


def test_path_extras_keep_only_the_basename(tmp_path) -> None:
    """A durable log must not record the directory a user chose (#2126).

    ``merged_sample_db_missing`` passes an absolute ``db_path`` through stdlib
    ``extra={...}``. The data directory is user-selected, so that path routinely
    embeds the account name or a self-chosen folder, and ``ExtraAdder`` would
    persist it to ``log_entries`` indefinitely.

    The basename survives on purpose: it is the diagnostic — which database was
    missing — and every path-bearing extra here is id-derived or a fixed
    filename, so it carries no user-supplied text.
    """
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
                sa.select(log_entries.c.event_data).where(
                    log_entries.c.message == "merged_sample_db_missing"
                )
            ).scalar_one()

        stored = event_data or ""
        assert "Alice" not in stored, f"the account name was persisted: {stored}"
        assert "My Genomes" not in stored, "the user's chosen directory was persisted"
        payload = json.loads(stored)
        assert payload["db_path"] == "sample_4.db"
        assert payload["merged_sample_id"] == 4
    finally:
        structlog.reset_defaults()
        engine.dispose()
