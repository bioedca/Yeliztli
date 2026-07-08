"""Tests for structured logging privacy filters."""

from __future__ import annotations

import json
import warnings

import pytest
import sqlalchemy as sa
import structlog

from backend.config import get_settings
from backend.db.tables import log_entries, reference_metadata
from backend.logging_config import (
    _REDACTED_LOG_VALUE,
    _redact_sensitive_log_fields,
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
                sa.select(log_entries.c.event_data).where(
                    log_entries.c.message == "analysis_event"
                )
            ).one()

        event_data = json.loads(row.event_data)
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


def test_console_exception_logging_does_not_warn_and_persists_traceback(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    structlog.reset_defaults()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)

    try:
        configure_logging(engine_getter=lambda: engine)
        logger = structlog.get_logger("tests.logging_exceptions")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                raise RuntimeError("boom")
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
        assert _REDACTED_LOG_VALUE in stdout
        assert not [
            warning
            for warning in caught
            if issubclass(warning.category, UserWarning)
            and "format_exc_info" in str(warning.message)
        ]

        with engine.connect() as conn:
            row = conn.execute(
                sa.select(log_entries.c.event_data).where(
                    log_entries.c.message == "exception_event"
                )
            ).one()

        event_data = json.loads(row.event_data)
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
    monkeypatch.setenv("YELIZTLI_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    structlog.reset_defaults()
    try:
        from backend.tasks import huey_tasks

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
    finally:
        structlog.reset_defaults()
        get_settings.cache_clear()
