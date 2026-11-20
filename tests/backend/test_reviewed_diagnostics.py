"""Operator-visible diagnostics through the configured application sinks."""

import json
import logging

import pytest
import sqlalchemy as sa
import structlog

from backend.db.tables import (
    LOG_ENTRY_PRESENTATION_POLICY_VERSION,
    log_entries,
    reference_metadata,
)
from backend.diagnostics import DATABASE_BUSY, GENERIC_EVENT, GENERIC_FAILURE, storage_failure
from backend.logging_config import configure_logging


@pytest.fixture
def diagnostics(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'diagnostics.db'}")
    reference_metadata.create_all(engine)
    configure_logging(lambda: engine)
    try:
        yield engine
    finally:
        engine.dispose()


def read_rows(engine):
    with engine.connect() as conn:
        return [
            (
                row.level,
                row.logger,
                row.message,
                json.loads(row.event_data or "{}"),
                row.presentation_policy_version,
            )
            for row in conn.execute(sa.select(log_entries).order_by(log_entries.c.id))
        ]


@pytest.mark.parametrize("source", ["native", "stdlib"])
def test_ordinary_text_cannot_select_a_reviewed_template(diagnostics, capsys, source):
    logger = (
        structlog.get_logger("backend.example")
        if source == "native"
        else logging.getLogger("backend.example")
    )
    logger.warning(DATABASE_BUSY)
    assert read_rows(diagnostics) == [
        ("WARNING", None, GENERIC_EVENT, {}, LOG_ENTRY_PRESENTATION_POLICY_VERSION)
    ]
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert GENERIC_EVENT in output
    assert DATABASE_BUSY not in output


@pytest.mark.parametrize("source", ["native", "stdlib"])
def test_unreviewed_failure_retains_the_category_only(diagnostics, capsys, source):
    logger = (
        structlog.get_logger("backend.example")
        if source == "native"
        else logging.getLogger("backend.example")
    )
    try:
        raise ValueError("example unreviewed wording")
    except ValueError:
        logger.exception("example unreviewed event")
    level = "ERROR" if source == "stdlib" else "EXCEPTION"
    assert read_rows(diagnostics) == [
        (
            level,
            None,
            GENERIC_FAILURE,
            {"reason": "invalid_input"},
            LOG_ENTRY_PRESENTATION_POLICY_VERSION,
        )
    ]
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert GENERIC_FAILURE in output and "invalid_input" in output
    assert "unreviewed" not in output and "Traceback" not in output


def test_validated_fields_belong_to_the_reviewed_producer(diagnostics, capsys):
    with structlog.contextvars.bound_contextvars(retry_count=9, context_note="fixture note"):
        storage_failure("busy")
        storage_failure("busy", retry_count=2)
        storage_failure("busy", retry_count=-1)
        storage_failure("unknown")
    version = LOG_ENTRY_PRESENTATION_POLICY_VERSION
    assert read_rows(diagnostics) == [
        ("WARNING", "backend.storage", DATABASE_BUSY, {"reason": "busy"}, version),
        (
            "WARNING",
            "backend.storage",
            DATABASE_BUSY,
            {"reason": "busy", "retry_count": 2},
            version,
        ),
        ("WARNING", "backend.storage", DATABASE_BUSY, {"reason": "busy"}, version),
        ("WARNING", "backend.storage", GENERIC_FAILURE, {}, version),
    ]
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert text.count(DATABASE_BUSY) == 3 and text.count(GENERIC_FAILURE) == 1
    assert "fixture note" not in text


@pytest.mark.parametrize("source", ["native", "stdlib"])
@pytest.mark.parametrize("shape", ["event", "exception", "cause", "group", "opaque"])
def test_restricted_or_unknown_evidence_has_one_neutral_delivery(
    diagnostics, capsys, source, shape
):
    logger = (
        structlog.get_logger("backend.fixture")
        if source == "native"
        else logging.getLogger("backend.fixture")
    )
    calls = []

    class Uninspectable:
        def __str__(self):
            calls.append(True)
            raise ValueError

    # Public regression identifiers from the existing response hold.
    held = "CYP2D6 tamoxifen"
    if shape == "event":
        logger.warning(held)
    elif shape == "opaque":
        logger.warning("fixture %s", Uninspectable())
    else:
        error = ValueError(held)
        if shape == "cause":
            outer = RuntimeError("fixture outer")
            outer.__cause__ = error
            error = outer
        elif shape == "group":
            error = ExceptionGroup("fixture outer", [ExceptionGroup("nested", [error])])
        logger.warning("fixture failure", exc_info=(type(error), error, None))
    assert calls == []
    assert read_rows(diagnostics) == [
        (
            "WARNING",
            None,
            "clinical_guidance_withheld",
            {"clinical_guidance_withheld": True},
            LOG_ENTRY_PRESENTATION_POLICY_VERSION,
        )
    ]
    captured = capsys.readouterr()
    lines = (captured.out + captured.err).splitlines()
    assert len(lines) == 1 and "clinical_guidance_withheld" in lines[0]
    assert "CYP2D6" not in lines[0] and "tamoxifen" not in lines[0]


def test_real_checkpoint_failure_recovers_without_formatting(
    diagnostics, capsys, tmp_path, monkeypatch
):
    import sqlite3

    from backend.db.download_manager import DownloadManager
    from backend.diagnostics import STORAGE_UNAVAILABLE

    manager = DownloadManager(diagnostics, tmp_path / "downloads")
    attempts = []

    def write_failure(_):
        original = sqlite3.OperationalError("fixture unavailable")
        original.sqlite_errorcode = sqlite3.SQLITE_CANTOPEN
        raise sa.exc.OperationalError(None, None, original)

    monkeypatch.setattr(manager, "_write_retrying", write_failure)
    manager._checkpoint_offset(3, 5)

    class Unclassified(Exception):
        def __str__(self):
            attempts.append(True)
            raise ValueError

    # SQLAlchemy constructs its exception upstream. Isolate the callback under
    # test so construction by that library cannot count as application logging.
    wrapped = sa.exc.OperationalError(None, None, sqlite3.OperationalError("fixture"))
    wrapped.orig = Unclassified()

    def unclassified(_):
        raise wrapped

    monkeypatch.setattr(manager, "_write_retrying", unclassified)
    manager._checkpoint_offset(3, 6)
    rows = read_rows(diagnostics)
    assert rows == [
        (
            "WARNING",
            "backend.storage",
            STORAGE_UNAVAILABLE,
            {"reason": "unavailable"},
            LOG_ENTRY_PRESENTATION_POLICY_VERSION,
        ),
        (
            "WARNING",
            None,
            "clinical_guidance_withheld",
            {"clinical_guidance_withheld": True},
            LOG_ENTRY_PRESENTATION_POLICY_VERSION,
        ),
    ]
    assert attempts == []
    output = capsys.readouterr().err
    assert STORAGE_UNAVAILABLE in output and "clinical_guidance_withheld" in output
    assert "fixture unavailable" not in output


def test_bound_and_direct_context_cannot_select_approved_fields(diagnostics):
    held = {"gene": "CYP2D6", "drug": "tamoxifen"}
    storage_failure("busy", context={"fixture": "unused"})
    storage_failure("busy", context=held)
    with structlog.contextvars.bound_contextvars(**held):
        storage_failure("busy")
    version = LOG_ENTRY_PRESENTATION_POLICY_VERSION
    assert read_rows(diagnostics) == [
        ("WARNING", "backend.storage", DATABASE_BUSY, {"reason": "busy"}, version),
        (
            "WARNING",
            None,
            "clinical_guidance_withheld",
            {"clinical_guidance_withheld": True},
            version,
        ),
        (
            "WARNING",
            None,
            "clinical_guidance_withheld",
            {"clinical_guidance_withheld": True},
            version,
        ),
    ]


@pytest.mark.parametrize("name", ["", "backend", "backend.fixture"])
def test_host_observer_only_receives_projected_records(diagnostics, capsys, name):
    seen = []

    class Observe(logging.Handler):
        def emit(self, record):
            seen.append(vars(record).copy())

    observer = Observe()
    observed = logging.getLogger(name)
    observed.addHandler(observer)
    try:
        try:
            raise RuntimeError("synthetic exception details")
        except RuntimeError:
            logging.getLogger("backend.fixture").exception(
                "fixture %s", "synthetic argument", extra={"private_note": "fixture"}
            )
    finally:
        observed.removeHandler(observer)
    assert len(seen) == 1
    record = seen[0]
    assert record["msg"] == GENERIC_FAILURE and record["name"] == "application"
    assert record["args"] == () and record["exc_info"] is None
    assert record["exc_text"] is None and record["stack_info"] is None
    assert record["pathname"] == "" and "private_note" not in record
    assert "synthetic" not in capsys.readouterr().err


def test_only_final_response_disposition_can_emit_refusal(diagnostics, capsys):
    from backend.analysis.pharmacogenomics import is_patient_presentable_response_payload
    from backend.diagnostics import finalize_response

    operation = "backend.api.routes.cancer.list_cancer_prs"
    with structlog.contextvars.bound_contextvars(gene="CYP2D6", drug="tamoxifen"):
        assert finalize_response(True, operation)
        checks = [
            is_patient_presentable_response_payload({"gene": "CYP2D6", "drug": "tamoxifen"})
            for _ in range(2)
        ]
        assert checks == [False, False]
        assert not finalize_response(all(checks), operation)
    assert read_rows(diagnostics) == [
        (
            "WARNING",
            "backend.api",
            "patient_response_payload_withheld",
            {"caller": operation},
            LOG_ENTRY_PRESENTATION_POLICY_VERSION,
        )
    ]
    output = capsys.readouterr().out
    assert output.count("patient_response_payload_withheld") == 1 and operation in output
    assert "CYP2D6" not in output and "tamoxifen" not in output
