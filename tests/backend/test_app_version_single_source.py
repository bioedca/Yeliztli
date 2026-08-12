"""Suite-wide guard: the app version has one source, and exported artifacts use it.

#2025: ``backend/reports/generator.py`` and ``backend/reports/variant_card.py``
each carried their own ``VERSION = "0.1.0"`` literal while the application was
``0.2.0``, so every exported report and variant card stamped a footer
misattributing it to an older build. The generation timestamp was correct, which
made the stale version look deliberate rather than broken.

Bumping those two literals would have fixed the symptom and rebuilt the defect at
the next release, because nothing tied them to ``pyproject.toml``. So the tests
here are about the *shape* of the fix rather than today's number: they assert that
every version surface resolves to the same value and that no module reintroduces a
private copy. A future ``0.2.0 -> 0.3.0`` bump must not need any edit here.
"""

from __future__ import annotations

import re
from importlib.metadata import version
from pathlib import Path

import pytest

from backend.analysis.provenance import pipeline_version
from backend.main import VERSION as MAIN_VERSION
from backend.reports.generator import VERSION as REPORT_VERSION
from backend.reports.variant_card import VERSION as VARIANT_CARD_VERSION
from backend.version import UNKNOWN_VERSION, app_version

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
# A module-level assignment of a bare semver to the APPLICATION version. Scoped to
# the exact names the app uses — a bare ``VERSION``, ``APP_VERSION`` or
# ``__version__`` — because the backend legitimately pins many *other* versions
# that must stay hardcoded: GNOMAD_CONSTRAINT_VERSION, SPLICEAI_VERSION,
# GTEX_VERSION, ALPHAMISSENSE_VERSION, PANEL_VERSION and friends are reference-data
# identities, not this application's. Matching those would make the guard fire on
# correct code, which is how a guard gets deleted instead of obeyed.
# Deliberately not anchored to "0.1.0": the point is that no hardcoded app version
# comes back, whatever its value.
_HARDCODED_VERSION = re.compile(
    r'^\s*(?:VERSION|APP_VERSION|__version__)\s*=\s*["\']\d+\.\d+\.\d+', re.MULTILINE
)


def test_every_version_surface_agrees() -> None:
    """The distribution, the API, and both exported artifacts report one version."""
    distribution = version("yeliztli")
    assert app_version() == distribution
    assert MAIN_VERSION == distribution
    assert REPORT_VERSION == distribution
    assert VARIANT_CARD_VERSION == distribution
    assert pipeline_version() == distribution


def test_report_artifacts_match_the_api_version() -> None:
    """The #2025 defect stated directly: a report must not claim a version the
    running application does not have.

    ``/api/health`` serves ``backend.main.VERSION``; the report and variant-card
    footers render their own module's ``VERSION`` through
    ``Yeliztli v{{ version }}``. Those were 0.2.0 and 0.1.0 respectively.
    """
    assert REPORT_VERSION == MAIN_VERSION
    assert VARIANT_CARD_VERSION == MAIN_VERSION


def test_no_backend_module_hardcodes_a_version_literal() -> None:
    """SELF-DISCOVERING: nobody may reintroduce a private copy of the version.

    This is what makes the fix durable rather than a one-time correction — it
    walks the whole backend, so a new module that writes the version out by hand
    fails immediately instead of drifting silently until someone reads a footer.
    """
    offenders = []
    for path in sorted(_BACKEND.rglob("*.py")):
        for match in _HARDCODED_VERSION.finditer(path.read_text(encoding="utf-8")):
            line = match.group(0).strip()
            offenders.append(f"{path.relative_to(_BACKEND.parent)}: {line}")
    assert not offenders, (
        "a module hardcodes the application version instead of calling "
        "backend.version.app_version(); that is #2025, which shipped reports "
        "stamped 0.1.0 while the app was 0.2.0: " + "; ".join(offenders)
    )


def test_exactly_one_module_resolves_the_distribution() -> None:
    """The lookup itself must exist once.

    Duplicating ``importlib.metadata.version("yeliztli")`` would be the same
    defect one level up: two resolvers can disagree about fallback behaviour even
    when neither hardcodes a number. ``provenance.pipeline_version`` used to hold
    a second copy and now delegates.
    """
    resolvers = [
        path.relative_to(_BACKEND.parent).as_posix()
        for path in sorted(_BACKEND.rglob("*.py"))
        if "importlib.metadata" in path.read_text(encoding="utf-8")
    ]
    assert resolvers == ["backend/version.py"], resolvers


def test_missing_distribution_is_reported_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A source checkout with no install must say so, not invent a number.

    ``Yeliztli vunknown`` in a footer tells a reader the provenance could not be
    established. A plausible-looking default tells them something false with total
    confidence, which is precisely how #2025 misled.
    """
    from importlib.metadata import PackageNotFoundError

    import backend.version as version_module

    def _absent(_name: str) -> str:
        raise PackageNotFoundError(_name)

    monkeypatch.setattr(version_module, "version", _absent)
    assert version_module.app_version() == UNKNOWN_VERSION
    assert not re.fullmatch(r"\d+\.\d+\.\d+", version_module.app_version())
