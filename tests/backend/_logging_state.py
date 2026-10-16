"""Snapshot and restore the process-wide logging state (#2126).

Routing stdlib logging into structlog made both halves of that state shared and
leaky: ``configure_logging`` attaches a handler to the ``backend`` logger whose
formatter carries the **database** processor, and it replaces structlog's global
processor chain with one that has an engine getter bound into it. A test that
calls it therefore leaves a live writer pointing at its own temporary engine
behind, and every later test in the process drives that writer — writing to, or
failing against, a database belonging to a finished test. The previous
``logging.basicConfig`` was harmless to leak because it only reached stderr, so
nothing used to need any of this.

The snapshot/restore pair lives here rather than inline in ``conftest.py`` so a
test can exercise it directly, without depending on a companion test having run
first to leave the right state behind.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import structlog

#: The logger ``configure_logging`` installs its routing handler on.
BACKEND_LOGGER_NAME = "backend"


@dataclass(frozen=True)
class LoggingState:
    """The pieces of global logging state that ``configure_logging`` replaces."""

    handlers: list[logging.Handler]
    level: int
    propagate: bool
    structlog_config: dict[str, Any] = field(default_factory=dict)


def snapshot_logging_state() -> LoggingState:
    """Capture the current logging state so it can be put back verbatim."""
    logger = logging.getLogger(BACKEND_LOGGER_NAME)
    return LoggingState(
        handlers=logger.handlers[:],
        level=logger.level,
        propagate=logger.propagate,
        # Snapshot rather than plan to reset. Test modules import
        # ``huey_tasks``, which installs the production DB/redaction chain at
        # module scope before any fixture runs, so resetting to structlog's
        # library defaults afterwards leaves every later test's *native*
        # structlog call running under a configuration production never uses —
        # passing while no longer exercising the behaviour under test.
        structlog_config=structlog.get_config().copy(),
    )


def restore_logging_state(previous: LoggingState) -> None:
    """Put back exactly what :func:`snapshot_logging_state` captured.

    A full restore, not just a removal of what a test added.
    ``configure_logging`` *replaces* the handlers on the ``backend`` logger, and
    one is already installed at import time by ``backend/tasks/huey_tasks.py``
    (``_configure_worker_logging()`` runs at module scope). Removing only the
    test's own handler therefore left the logger with none at all, so every
    later test silently lost logging — trading one kind of cross-test pollution
    for another.

    Levels and propagation are restored for the same reason:
    ``configure_logging`` sets this logger to INFO, and an intermediate logger's
    level filters records before they propagate, so a leaked level can silence
    ``caplog`` assertions in unrelated tests.
    """
    logger = logging.getLogger(BACKEND_LOGGER_NAME)
    for handler in logger.handlers[:]:
        if handler not in previous.handlers:
            logger.removeHandler(handler)
            handler.close()
    logger.handlers[:] = previous.handlers
    logger.setLevel(previous.level)
    logger.propagate = previous.propagate
    if previous.structlog_config:
        structlog.configure(**previous.structlog_config)
