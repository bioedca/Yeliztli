"""No stdlib logger is called with structlog-style keywords (#2126).

``logging.Logger.warning("msg", foo=1)`` does not record a structured field —
it raises ``TypeError: Logger._log() got an unexpected keyword argument 'foo'``
before any handler runs. A site that does this is therefore not a cosmetic
inconsistency but a latent crash, and it crashes exactly when the condition it
reports is true.

``backend/main.py`` had one: the genome-build skew warning passed ``sources=``
and ``pipeline_build=`` to the lifespan's stdlib logger, so the first time a
build skew was actually detected the warning aborted application startup
instead of reporting it. Routing stdlib logging through structlog (#2126) does
**not** fix this class — the TypeError happens inside ``Logger._log``, upstream
of every handler — so it needs its own guard.

The scan is AST-based rather than textual: it resolves which module-level names
are bound to ``logging.getLogger(...)`` and only inspects calls on those, so a
``structlog.get_logger`` logger in the same file is correctly ignored.
"""

from __future__ import annotations

import ast
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2] / "backend"

# Keywords ``Logger._log`` actually accepts.
_STDLIB_LOG_KWARGS = {"exc_info", "stack_info", "stacklevel", "extra"}
_LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}


def _stdlib_logger_names(tree: ast.Module) -> set[str]:
    """Names assigned from ``logging.getLogger(...)`` anywhere in the module."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if isinstance(func, ast.Attribute) and func.attr == "getLogger":
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _offending_calls(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    if "logging.getLogger" not in source:
        return []
    tree = ast.parse(source)
    logger_names = _stdlib_logger_names(tree)
    if not logger_names:
        return []

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _LOG_METHODS:
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id not in logger_names:
            continue
        unexpected = sorted(
            keyword.arg
            for keyword in node.keywords
            if keyword.arg is not None and keyword.arg not in _STDLIB_LOG_KWARGS
        )
        if unexpected:
            offenders.append(f"{path.name}:{node.lineno} {node.func.attr}(... {unexpected})")
    return offenders


def test_no_stdlib_logger_receives_structlog_keywords() -> None:
    offenders: list[str] = []
    for path in sorted(_BACKEND.rglob("*.py")):
        offenders.extend(_offending_calls(path))

    assert not offenders, (
        "stdlib logger calls passing keywords Logger._log cannot accept — each "
        "raises TypeError at the moment it is reached, aborting whatever it was "
        "meant to warn about. Format the values into the message (%-style) or "
        "use a structlog logger:\n  " + "\n  ".join(offenders)
    )


def test_guard_detects_a_planted_offender(tmp_path: Path) -> None:
    """The scan would actually catch the bug it was written for.

    Without this, a scan that silently matched nothing — a changed call shape, a
    renamed logger — would pass forever and assert nothing.
    """
    planted = tmp_path / "planted.py"
    planted.write_text(
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "logger.warning('genome_build_skew_detected', sources=[], pipeline_build='GRCh37')\n",
        encoding="utf-8",
    )
    offenders = _offending_calls(planted)
    assert len(offenders) == 1
    assert "pipeline_build" in offenders[0] and "sources" in offenders[0]


def test_guard_ignores_structlog_loggers(tmp_path: Path) -> None:
    """A structlog logger may take keywords; the scan must not flag it.

    Both logger styles coexist in this codebase, so a scan that keyed on the
    method name alone would produce false positives on every structured event.
    """
    mixed = tmp_path / "mixed.py"
    mixed.write_text(
        "import logging\n"
        "import structlog\n"
        "logger = logging.getLogger(__name__)\n"
        "slog = structlog.get_logger(__name__)\n"
        "slog.info('structured_event', sources=[], pipeline_build='GRCh37')\n"
        "logger.info('plain message')\n"
        "logger.warning('with allowed kwargs', exc_info=True, stacklevel=2)\n",
        encoding="utf-8",
    )
    assert _offending_calls(mixed) == []
