"""Structured logging configuration for Yeliztli (P4-21b).

Configures structlog to write log entries to both console (for development)
and the ``log_entries`` table in reference.db (for the admin panel log explorer).
"""

from __future__ import annotations

import contextvars
import json
import logging
from datetime import UTC, datetime
from pathlib import Path, PurePath

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
        # ``exc_info`` has not reached either renderer yet. Format a private copy
        # for classification so a held pair in an exception message cannot bypass
        # the event-dict scan and reappear in the console traceback or DB payload.
        evidence = dict(event_dict)
        if evidence.get("exc_info"):
            evidence = structlog.processors.format_exc_info(None, "", evidence)
        should_redact = contains_unpresentable_prescribing_identifier(evidence)
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


# Identifying free text that must not enter a durable log through stdlib
# ``extra={...}``. These are user-supplied strings — a sample name can be a
# person's name — and the Log Explorer is a durable, admin-visible surface, so
# deleting a sample must not leave its name behind in reference.db forever.
#
# Scoped deliberately to the foreign-record import path (#2126): native
# structlog events choose their own fields and are covered by the curated
# genotype policy above, so widening that curated list here would be a separate
# decision with its own blast radius.
# Path-bearing extras keep their basename and lose every directory component.
# The directory is what identifies: a user picks their own data directory, so an
# absolute path routinely embeds the account name (/home/Alice/...) or a
# self-chosen folder, and that would sit in reference.db indefinitely.
#
# The basename is kept because it *is* the diagnostic — which database was
# missing — and because every path-bearing extra here is id-derived or a fixed
# filename: db_path values are generated as ``samples/sample_<id>.db`` and
# ``samples/<prefix><publication_id>.db``, sample_merge passes a candidate under
# those, and preferences passes config.toml. None carries user-supplied text.
#
# The limit that follows: a user-supplied *name* must be sanitised where it is
# logged, not here. That is why the uploaded filename is reported by content
# hash at its call site rather than routed through this scrubber.
_PATH_EXTRA_KEYS = frozenset(
    {
        "path",
        "db_path",
        "file_path",
        "sample_db_path",
        "config_path",
        "data_dir",
        "directory",
    }
)

_IDENTIFYING_EXTRA_KEYS = frozenset(
    {
        "name",
        "sample_name",
        "deleted_sample_name",
        "individual_name",
        "display_name",
        "filename",
        "file_name",
    }
)


def _drop_identifying_extras(
    _logger: structlog.types.WrappedLogger,
    _method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """Redact identifying names imported from a stdlib record's ``extra``.

    ``ExtraAdder`` copies every field verbatim, and ``sample_delete_cascade``
    passes ``deleted_sample_name`` plus a list of child ``{"id", "name"}``
    objects — so without this, deleting a sample removed its row and its
    database while persisting its name to the Log Explorer indefinitely.

    Ids and counts are kept: they are what makes the entry useful for triage and
    identify nothing on their own.
    """

    def sanitize(key: str, value: object) -> object:
        lowered = key.lower()
        if lowered in _IDENTIFYING_EXTRA_KEYS:
            return _REDACTED_LOG_VALUE
        if lowered in _PATH_EXTRA_KEYS and isinstance(value, str):
            return PurePath(value).name or _REDACTED_LOG_VALUE
        return scrub(value)

    def scrub(value: object) -> object:
        if isinstance(value, dict):
            return {key: sanitize(key, sub) for key, sub in value.items()}
        if isinstance(value, (list, tuple)):
            return [scrub(item) for item in value]
        return value

    for key in list(event_dict):
        event_dict[key] = sanitize(key, event_dict[key])
    return event_dict


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
            db_event_dict = _event_dict_for_db_storage(logger, method_name, event_dict)
            # Rendering exception information for the database creates a fresh
            # event shape, so apply the same fail-closed hold immediately before
            # deriving durable fields and attesting their presentation policy.
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
            extra_json = json.dumps(extra, default=str) if extra else None

            with engine.begin() as conn:
                conn.execute(
                    sa.insert(log_entries).values(
                        timestamp=datetime.now(UTC),
                        level=level,
                        logger=str(logger_name) if logger_name is not None else None,
                        message=str(message),
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

    # RichTracebackFormatter defaults to ``show_locals=True``.  Exception-frame
    # locals can contain credentials, keyed binding material, or private sample
    # state that was never added to the structured event and therefore cannot
    # be removed by the field redactor above.  The dependency-free formatter
    # keeps the stack and exception while never rendering arbitrary locals.
    processors.append(
        structlog.dev.ConsoleRenderer(exception_formatter=structlog.dev.plain_traceback)
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=_NamedPrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )

    _route_stdlib_logging_through_structlog(engine_getter)


def _route_stdlib_logging_through_structlog(engine_getter: callable | None) -> None:
    """Send records from ``logging.getLogger`` through the structlog chain (#2126).

    The previous implementation was ``logging.basicConfig(...)`` under a comment
    claiming it routed stdlib logging through structlog. It does not: it installs
    a plain :class:`logging.StreamHandler` on the root logger, which formats and
    writes the record itself and never touches a structlog processor. Everything
    logged through the 65 ``logging.getLogger(__name__)`` call sites in
    ``backend/`` — including the lifespan's own startup diagnostics — reached
    stderr and nothing else, so the Log Explorer, whose rows come from the DB
    processor, showed none of it. Verified against a real database before the
    fix: ``DBRegistry initialised``, logged on every boot, had zero rows in
    ``log_entries``.

    :class:`structlog.stdlib.ProcessorFormatter` is the supported way to do what
    the comment described. Foreign (stdlib) records run through
    ``foreign_pre_chain`` to gain the same level, logger name, timestamp and
    **redaction** a native structlog event gets, then through the formatter's own
    processors, which is where the DB processor sits.

    The DB processor runs *after* ``remove_processors_meta`` deliberately. That
    call strips the ``_record`` and ``_from_structlog`` keys ProcessorFormatter
    adds, and ``_record`` is a :class:`logging.LogRecord` — passing it to the DB
    writer would put an unserialisable object into the stored payload.

    Native structlog events do not pass through here at all: this app's
    ``logger_factory`` is a :class:`~structlog.PrintLoggerFactory`, so structlog
    writes straight to stderr and never through the stdlib root logger. This
    handler therefore only ever sees foreign records, and no event is recorded
    twice.

    Root handlers are replaced rather than appended, so calling
    ``configure_logging`` more than once (the API lifespan reconfigures once the
    reference engine exists) cannot stack duplicate handlers.
    """
    foreign_pre_chain: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        # Reads ``record.name`` for foreign records, so a stdlib logger lands in
        # the Log Explorer's Component column under its real module path.
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        # ProcessorFormatter builds a foreign event dict from the rendered
        # message and metadata only, so fields passed as ``extra={...}`` are
        # dropped unless this adds them. Without it, newly persisted events such
        # as sample_delete_cascade and sample_merge_complete would reach the Log
        # Explorer as a bare event name with their ids, counts and error details
        # discarded. It runs here, before ``remove_processors_meta``, because it
        # reads ``_record`` — and before the redactor below, so imported fields
        # are sanitised on the same terms as everything else.
        structlog.stdlib.ExtraAdder(),
        # Immediately after the import, so an identifying name never reaches the
        # renderer or the durable writer.
        _drop_identifying_extras,
    ]

    formatter_processors: list[structlog.types.Processor] = [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        # Redaction runs *after* the formatter metadata is removed, not in the
        # pre-chain. The fail-closed path for a withheld prescribing pair calls
        # ``event_dict.clear()``, which would also drop ProcessorFormatter's
        # ``_record`` and ``_from_structlog`` keys; ``remove_processors_meta``
        # then deletes them unconditionally and raises ``KeyError``, so the
        # neutral replacement was neither stored nor rendered and stdlib's
        # ``handleError`` printed the original message — publishing the held pair
        # it was supposed to withhold. Running the redactor once the meta keys
        # are gone makes clearing safe.
        _redact_sensitive_log_fields,
    ]
    if engine_getter is not None:
        formatter_processors.append(_db_processor_factory(engine_getter))
    formatter_processors.append(
        structlog.dev.ConsoleRenderer(exception_formatter=structlog.dev.plain_traceback)
    )

    handler = logging.StreamHandler()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=foreign_pre_chain,
            processors=formatter_processors,
        )
    )

    # Attached to the ``backend`` logger, not the root logger. The gap this
    # closes is the application's own 65 ``logging.getLogger(__name__)`` sites;
    # routing *every* stdlib logger would also persist third-party chatter
    # (httpx, uvicorn, sqlalchemy, huey) into the Log Explorer, which is both
    # noise and a behaviour change nobody asked for. Verified by the admin Log
    # Explorer tests, which count rows and failed on the extra entries when this
    # was installed on root.
    #
    # Handlers are replaced rather than appended, so repeated configuration (the
    # API lifespan, the Huey worker bootstrap, tests in one process) cannot stack
    # duplicates.
    #
    # ``propagate`` is deliberately left **on**, and the limit is worth stating.
    # Redaction happens in this handler's formatter and does not mutate the
    # ``LogRecord``, so an ancestor handler — one a host or a test harness
    # installs on the root logger — receives the unredacted original. Turning
    # propagation off does close that, but it is the wrong lever here: this
    # module is configured at *import* by ``backend/tasks/huey_tasks.py``, so
    # disabling propagation applies process-wide and silences ``caplog`` for
    # every ``backend.*`` logger, which took out 15 tests across four files.
    #
    # Closing it properly means sanitising the ``LogRecord`` itself in a
    # logger-level filter, so propagation carries redacted data rather than
    # being switched off. That duplicates the redaction semantics for
    # ``msg``/``args``/``exc_info`` and is its own change — tracked separately.
    # Note the default configuration is still strictly better than the
    # ``basicConfig`` it replaces, which printed every stdlib record raw to
    # stderr unconditionally; with no root handler installed, propagation now
    # reaches nothing.
    backend_logger = logging.getLogger("backend")
    for existing in backend_logger.handlers[:]:
        backend_logger.removeHandler(existing)
    backend_logger.addHandler(handler)
    backend_logger.setLevel(logging.INFO)
