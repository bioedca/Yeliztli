"""Structured logging configuration for Yeliztli (P4-21b).

Configures structlog to write log entries to both console (for development)
and the ``log_entries`` table in reference.db (for the admin panel log explorer).
"""

from __future__ import annotations

import contextvars
import json
import logging
from datetime import UTC, datetime

import structlog

from backend.analysis.pharmacogenomics import contains_unpresentable_prescribing_identifier

# Guard against recursive log writes (e.g. if the DB insert triggers logging)
_in_db_processor = contextvars.ContextVar("_in_db_processor", default=False)

_REDACTED_LOG_VALUE = "[REDACTED]"
_SENSITIVE_LOG_KEY_SUFFIXES = (
    "_genotype",
    "_genotypes",
    "_diplotype",
    "_diplotypes",
    "_haplotype",
    "_haplotypes",
    "_gt",
)
_SENSITIVE_LOG_KEYS = {
    "e4_count",
    "e4_present",
    "genotype",
    "genotypes",
    "has_e4",
    "diplotype",
    "diplotypes",
    "haplotype",
    "haplotypes",
    "gt",
}
_LOG_METADATA_KEYS = frozenset(
    {"event", "logger", "_logger", "timestamp", "level", "_record", "_from_structlog"}
)


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
    except RecursionError:
        should_redact = True
    if not should_redact:
        return event_dict

    safe_metadata = {key: value for key, value in event_dict.items() if key in _LOG_METADATA_KEYS}
    safe_metadata["clinical_guidance_withheld"] = True
    event_dict.clear()
    event_dict.update(safe_metadata)
    return event_dict


def _is_sensitive_log_key(key: object) -> bool:
    """Return True for structured log keys that carry genotype-like values."""
    normalized = str(key).lower()
    return normalized in _SENSITIVE_LOG_KEYS or normalized.endswith(_SENSITIVE_LOG_KEY_SUFFIXES)


def _redact_sensitive_value(value: object) -> object:
    """Recursively redact sensitive fields in structured containers."""
    if isinstance(value, dict):
        return {
            key: (
                _REDACTED_LOG_VALUE
                if _is_sensitive_log_key(key)
                else _redact_sensitive_value(nested)
            )
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_sensitive_value(item) for item in value)
    return value


def _redact_sensitive_log_fields(
    logger: structlog.types.WrappedLogger,
    method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """Redact genotype-like structured fields before any log sink sees them."""
    _redact_withheld_prescribing_alert_fields(event_dict)
    for key, value in list(event_dict.items()):
        event_dict[key] = (
            _REDACTED_LOG_VALUE if _is_sensitive_log_key(key) else _redact_sensitive_value(value)
        )
    return event_dict


def _event_dict_for_db_storage(
    logger: structlog.types.WrappedLogger,
    method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """Return a DB-only event copy with exception info rendered as text."""
    if not event_dict.get("exc_info"):
        return event_dict
    # Runs after _redact_sensitive_log_fields. Keep ConsoleRenderer's event dict
    # untouched so it can pretty-print exc_info without structlog's warning.
    return structlog.processors.format_exc_info(logger, method_name, dict(event_dict))


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

            import sqlalchemy as sa

            from backend.db.tables import log_entries

            level = method_name.upper()
            db_event_dict = _event_dict_for_db_storage(logger, method_name, event_dict)
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
            extra_json = json.dumps(extra, default=str) if extra else None

            with engine.begin() as conn:
                conn.execute(
                    sa.insert(log_entries).values(
                        timestamp=datetime.now(UTC),
                        level=level,
                        logger=str(logger_name) if logger_name is not None else None,
                        message=str(message),
                        event_data=extra_json,
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
        logger.name = str(args[0]) if args else ""
        return logger


def configure_logging(engine_getter: callable | None = None) -> None:
    """Configure structlog with console + optional DB output.

    Args:
        engine_getter: Optional callable returning the reference DB engine.
            If provided, log entries are also persisted to reference.db.
    """
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        # Puts the get_logger() name into the event dict as ``logger`` (works
        # because _NamedPrintLoggerFactory reattaches it to the PrintLogger).
        # Feeds the DB processor's ``logger`` column — the Log Explorer's
        # Component column and filter (#1997).
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        _redact_sensitive_log_fields,
    ]

    if engine_getter is not None:
        processors.append(_db_processor_factory(engine_getter))

    processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=_NamedPrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )

    # Also route stdlib logging through structlog
    logging.basicConfig(
        format="%(message)s",
        level=logging.INFO,
    )
