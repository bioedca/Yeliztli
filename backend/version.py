"""The one place the running application's version is resolved.

Before this existed the version was written out as a literal in three separate
modules, and two of them drifted: ``backend/reports/generator.py`` and
``backend/reports/variant_card.py`` still said ``0.1.0`` after the app moved to
``0.2.0``, so every exported report and variant card carried a footer
misattributing it to an older build (#2025). Nothing linked those copies to
``pyproject.toml``, so they were guaranteed to drift again at the next bump.

Resolving from the installed distribution instead means the version has exactly
one source — ``pyproject.toml`` — and a release bump cannot leave a surface
behind.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

_DISTRIBUTION = "yeliztli"
UNKNOWN_VERSION = "unknown"


def app_version() -> str:
    """The installed distribution's version, e.g. ``"0.2.0"``.

    Returns :data:`UNKNOWN_VERSION` when the package is not installed as a
    distribution — a bare source checkout with no ``pip install -e .``. That is
    deliberately honest rather than a plausible-looking default: a report footer
    reading ``Yeliztli vunknown`` tells a reader the provenance could not be
    established, whereas a hardcoded number tells them something false with
    total confidence, which is the failure #2025 reported.
    """
    try:
        return version(_DISTRIBUTION)
    except PackageNotFoundError:
        return UNKNOWN_VERSION
