"""Structured logging configuration for Yeliztli (P4-21b).

Configures structlog to write log entries to both console (for development)
and the ``log_entries`` table in reference.db (for the admin panel log explorer).
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import structlog

from backend.analysis.pharmacogenomics import contains_unpresentable_prescribing_identifier
from backend.diagnostics import safe_event

# Guard against recursive log writes (e.g. if the DB insert triggers logging)
_in_db_processor = contextvars.ContextVar("_in_db_processor", default=False)


def _redact_withheld_prescribing_alert_fields(
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """Strip all clinical fields for a pair held from patient presentation.

    Structured logs are persisted and rendered by the admin panel, so retaining
    a source-faithful row in the sample database must not create a second
    patient-visible recommendation channel. Preserve only event metadata and a
    neutral marker when a withheld pair is encountered.
    """
    try:
        should_redact = contains_unpresentable_prescribing_identifier(event_dict)
    except Exception:  # noqa: BLE001 - logging must fail closed before either sink
        should_redact = True
    if not should_redact:
        return event_dict

    event_dict.clear()
    # Do not retain dynamic event/logger metadata: either field can carry the
    # held pair itself, or split it across fields. A fixed neutral event keeps
    # both console and durable-log sinks informative without reintroducing a
    # clinical guidance channel.
    event_dict.update(
        {
            "event": "clinical_guidance_withheld",
            "clinical_guidance_withheld": True,
        }
    )
    return event_dict


def _db_processor_factory(engine_getter: callable) -> callable:
    """Create a structlog processor that writes log entries to reference.db.

    Args:
        engine_getter: Callable returning the reference DB SQLAlchemy engine.
            Deferred so the engine doesn't need to exist at import time.
    """

    def db_processor(
        logger: structlog.types.WrappedLogger,
        method_name: str,
        event_dict: structlog.types.EventDict,
    ) -> structlog.types.EventDict:
        """Write each log entry to the log_entries table."""
        if _in_db_processor.get():
            return event_dict

        _in_db_processor.set(True)
        try:
            engine = engine_getter()
            if engine is None:
                return event_dict

            # Never resurrect a deleted database. SQLite creates the file on
            # connect, so a single log line emitted after the nuclear-delete
            # endpoint removes reference.db would leave a fresh one behind —
            # a "delete all my data" feature depositing its own artefact. The
            # window is reachable because stdlib logging now flows through this
            # processor (#2126); before that, nothing happened to log in it.
            if engine.dialect.name == "sqlite":
                database_path = engine.url.database
                if (
                    database_path
                    and database_path != ":memory:"
                    and not Path(database_path).exists()
                ):
                    return event_dict

            import sqlalchemy as sa

            from backend.db.tables import LOG_ENTRY_PRESENTATION_POLICY_VERSION, log_entries

            level = method_name.upper()
            db_event_dict = dict(event_dict)
            # Recheck the projected shape just before attesting a durable row.
            _redact_withheld_prescribing_alert_fields(db_event_dict)
            # NULL (not "") when no name was captured, so a misconfigured processor
            # chain reads as a visible gap rather than a tidy blank column (#1997).
            logger_name = db_event_dict.get("logger") or db_event_dict.get("_logger") or None
            message = db_event_dict.get("event", "")

            # Collect extra structured data (exclude internal keys)
            _internal = {
                "event",
                "logger",
                "_logger",
                "timestamp",
                "level",
                "_record",
                "_from_structlog",
            }
            extra = {k: v for k, v in db_event_dict.items() if k not in _internal}
            extra_json = json.dumps(extra) if extra else None

            with engine.begin() as conn:
                conn.execute(
                    sa.insert(log_entries).values(
                        timestamp=datetime.now(UTC),
                        level=level,
                        logger=logger_name,
                        message=message,
                        event_data=extra_json,
                        presentation_policy_version=LOG_ENTRY_PRESENTATION_POLICY_VERSION,
                    )
                )
        except Exception:
            # Never let logging failures crash the app
            pass
        finally:
            _in_db_processor.set(False)

        return event_dict

    return db_processor


class _NamedPrintLoggerFactory(structlog.PrintLoggerFactory):
    """``PrintLoggerFactory`` that keeps the logger name so it can be recorded.

    The app creates loggers with ``structlog.get_logger(__name__)``, but the plain
    ``PrintLogger`` drops that name — it has no ``.name`` attribute, so
    ``structlog.stdlib.add_logger_name`` cannot read it (and in fact raises). This
    reattaches the ``get_logger()`` name to each PrintLogger so ``add_logger_name``
    populates the event dict, which is what fills the Log Explorer's Component
    column and makes its component filter matchable (#1997).
    """

    def __call__(self, *args: object) -> structlog.types.WrappedLogger:
        logger = super().__call__(*args)
        # structlog passes the get_logger() positional args here; the first is the
        # logger name (empty when get_logger() was called without one).
        logger.name = args[0] if args else None
        return logger


def _safe_native(logger, _method: str, raw: dict) -> dict:
    return safe_event(raw, logger.name)


_STANDARD_FIELDS = frozenset(logging.LogRecord("", logging.INFO, "", 0, "", (), None).__dict__)
_LEVELS = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warning",
    logging.ERROR: "error",
    logging.CRITICAL: "critical",
}


_FRAMEWORK_LOGGERS = ("uvicorn", "uvicorn.access", "uvicorn.error", "huey", "huey.consumer")


def _replace_observed_record(record: logging.LogRecord, level: int, event: dict) -> None:
    sanitized = logging.LogRecord(
        event.get("logger") or "application", level, "", 0, event["event"], (), None
    )
    # These names may be supplied by the task, thread, or process itself.
    sanitized.__dict__.update(threadName=None, processName=None, taskName=None)
    record.__dict__.clear()
    record.__dict__.update(sanitized.__dict__)


class _SafeHandler(logging.Handler):
    """Classify the unformatted record before our sinks or ancestor handlers see it."""

    def __init__(self, db_processor=None) -> None:
        super().__init__()
        self.db_processor = db_processor

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = (
                record.levelno
                if type(record.levelno) is int and record.levelno in _LEVELS
                else logging.INFO
            )
            raw = {
                key: value for key, value in vars(record).items() if key not in _STANDARD_FIELDS
            }
            raw.update(
                event=record.msg,
                args=record.args,
                exc_info=record.exc_info,
                exc_text=record.exc_text,
                stack_info=record.stack_info,
            )
            approved = safe_event(raw, record.name)
            _replace_observed_record(record, level, approved)
            if self.db_processor is not None:
                self.db_processor(None, _LEVELS[level], approved)
            _console_write(_LEVELS[level], approved)
        except Exception:
            # Handler.handleError formats the rejected input. Give ancestors a
            # fresh, minimal record even if classification or output failed.
            _replace_observed_record(record, logging.INFO, {"event": "Application diagnostic"})


def _console_write(method: str, event: dict) -> None:
    from backend.operator_console import write_diagnostic

    fields = {k: v for k, v in event.items() if k != "event"}
    suffix = " " + json.dumps(fields, ensure_ascii=True) if fields else ""
    write_diagnostic(f"{method.upper()} {event['event']}{suffix}\n", sys.stderr)


def _render_native(_logger, method: str, event: dict) -> str:
    from backend.operator_console import managed_console

    if managed_console():
        _console_write(method, event)
        raise structlog.DropEvent
    return structlog.dev.ConsoleRenderer(colors=False)(None, method, event)


def configure_logging(engine_getter: callable | None = None) -> None:
    """Install one safe route for each sink, without attaching to unrelated loggers."""
    processors = [_safe_native]
    db_processor = _db_processor_factory(engine_getter) if engine_getter is not None else None
    if db_processor is not None:
        processors.append(db_processor)
    processors.append(_render_native)
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=_NamedPrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )

    backend_logger = logging.getLogger("backend")
    backend_logger.handlers[:] = [_SafeHandler(db_processor)]
    backend_logger.setLevel(logging.INFO)
    from backend.operator_console import managed_console

    for name in _FRAMEWORK_LOGGERS:
        framework_logger = logging.getLogger(name)
        if name == "uvicorn.access" and managed_console():
            framework_logger.handlers.clear()
        else:
            framework_logger.handlers[:] = [_SafeHandler()]
        framework_logger.propagate = False
