"""Cross-stack drift-guard: the FindingsExplorer module registry must cover every
backend module that persists findings (#544/#620).

#544 hand-expanded ``FindingsExplorer``'s module map over three review passes to
cover every ``module`` string that persists into the unified ``findings`` table;
#620 makes that no longer drift. This test computes the canonical findings-module
set from the backend's own signals and asserts the shared frontend registry
(``frontend/src/lib/modules.ts`` ``MODULE_META``) matches it exactly — so a new
analysis module that writes findings without a registry entry (or a registry
entry for a module the backend no longer produces) fails CI.

It reads the frontend source directly (the repo's cross-stack parity-test
pattern), avoiding a fixture that could itself drift.

The backend set is gathered from three self-discovering signals so a new module
added via *any* of them is caught:
  1. ``MODULE`` / ``MODULE_NAME`` constants on every ``backend.analysis.*`` module,
  2. the top-level ``module`` field of every curated panel JSON,
  3. the orchestration names in ``run_all._get_modules()``.

Two small, documented adjustments map signals to the persisted ``module`` value:
aliases (a name stored under a different module string) and a non-findings
allowlist (a runner that writes a table other than ``findings``).
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import re
from pathlib import Path

import backend.analysis as analysis_pkg
from backend.analysis.run_all import _get_modules

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PANELS = _REPO_ROOT / "backend" / "data" / "panels"
_MODULES_TS = _REPO_ROOT / "frontend" / "src" / "lib" / "modules.ts"

# Orchestration / panel names that persist under a *different* ``module`` string
# in the findings table.
_MODULE_ALIASES = {
    # run_all runner name; the carrier_status panel persists module="carrier".
    "carrier_status": "carrier",
    # the haplogroup panel's findings are stored under the ancestry module.
    "haplogroup": "ancestry",
}

# Runners/constants that do NOT persist into the findings table, so they are not
# findings modules and must not require a registry entry.
_NON_FINDINGS_MODULES = {
    "qc",  # _run_qc writes the qc_metrics table, not findings (see run_all._run_qc)
}


def _module_constants() -> set[str]:
    """Every ``MODULE`` / ``MODULE_NAME`` string declared by a backend.analysis module."""
    found: set[str] = set()
    for info in pkgutil.iter_modules(analysis_pkg.__path__):
        try:
            mod = importlib.import_module(f"backend.analysis.{info.name}")
        except Exception:  # pragma: no cover - an unimportable module is not a findings source
            continue
        for attr in ("MODULE", "MODULE_NAME"):
            value = getattr(mod, attr, None)
            if isinstance(value, str) and value:
                found.add(value)
    return found


def _panel_modules() -> set[str]:
    """Every top-level ``module`` field across the curated panel JSONs."""
    found: set[str] = set()
    for path in sorted(_PANELS.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data.get("module"), str):
            found.add(data["module"])
    return found


def _run_all_modules() -> set[str]:
    """Every orchestration name in run_all._get_modules()."""
    return {name for name, _ in _get_modules()}


def _compute_findings_modules() -> set[str]:
    """The canonical set of ``module`` strings persisted to the findings table,
    gathered from all three signals with aliases applied and non-findings runners
    removed."""
    raw = _module_constants() | _panel_modules() | _run_all_modules()
    aliased = {_MODULE_ALIASES.get(name, name) for name in raw}
    return aliased - _NON_FINDINGS_MODULES


def _registry_keys() -> set[str]:
    """The top-level keys of ``MODULE_META`` in frontend/src/lib/modules.ts."""
    text = _MODULES_TS.read_text(encoding="utf-8")
    body = re.search(
        r"export const MODULE_META: Record<string, ModuleMeta> = \{(.*?)\n\}",
        text,
        re.DOTALL,
    )
    assert body, "could not locate the MODULE_META object in frontend/src/lib/modules.ts"
    # Top-level entries are two-space-indented `key: {`.
    return set(re.findall(r"^  ([A-Za-z0-9_]+): \{", body.group(1), re.MULTILINE))


def test_signals_are_non_vacuous() -> None:
    """Guard the guard: each source must actually discover entries, so a refactor
    that breaks discovery can't make the parity assertion pass vacuously."""
    assert len(_module_constants()) >= 15
    assert len(_panel_modules()) >= 10
    assert len(_run_all_modules()) >= 20
    assert len(_registry_keys()) >= 25


def test_registry_matches_findings_module_set() -> None:
    """The frontend MODULE_META registry must contain exactly the backend's
    findings-producing module set. A new findings module (via a MODULE constant, a
    panel, or run_all) fails here until added to frontend/src/lib/modules.ts; a
    stale registry entry for a module the backend no longer produces fails too."""
    computed = _compute_findings_modules()
    registry = _registry_keys()
    missing_from_registry = computed - registry
    stale_in_registry = registry - computed
    assert computed == registry, (
        "FindingsExplorer module registry drifted from the backend findings-module set.\n"
        f"  persists findings but missing from MODULE_META: {sorted(missing_from_registry)}\n"
        f"  in MODULE_META but no longer a findings module: {sorted(stale_in_registry)}\n"
        "Update frontend/src/lib/modules.ts. If a runner does not write findings add "
        "it to _NON_FINDINGS_MODULES; if it persists under a different module string "
        "add a _MODULE_ALIASES entry."
    )


def test_known_aliases_and_non_findings_are_handled() -> None:
    """Lock the alias / allowlist decisions so they can't silently change."""
    computed = _compute_findings_modules()
    assert "carrier_status" not in computed and "carrier" in computed
    assert "haplogroup" not in computed and "ancestry" in computed
    assert "qc" not in computed
