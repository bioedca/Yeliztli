"""Docs guard: Dashboard QC count interpretation stays documented (#1586, #1590).

The Dashboard QC panel renders a bare ``z-score N.NN`` under the heterozygosity
check, but the number is uninterpretable without its comparator — which is the
account's **own other samples on the same genotyping array** (min 3), *not* a
population or array-wide baseline (``backend/analysis/qc.py::het_outlier_zscore``
plus the same-array restriction in ``backend/api/routes/qc.py``, #563). The
intuitive reading (a chip-wide/population expectation) is wrong, so this locks the
comparator explanation into the user docs — it must not silently disappear.

The Dashboard's prominent sample count is similarly easy to misread: it is the
full uploaded-position denominator, including no-calls, while call rate reports
called / total and the Variant Explorer defaults to hiding rows whose annotation
state is still missing. Keep that reconciliation in the published docs too.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_READING_RESULTS_DOC = _REPO / "docs" / "getting-started" / "reading-your-results.md"
_VARIANT_EXPLORER_DOC = _REPO / "docs" / "features" / "variant-explorer.md"

# Phrases that together assert the z-score's comparator is documented: it is a
# z-score, measured against the user's own samples, restricted to one array type.
_REQUIRED_PHRASES = ("z-score", "your own other", "same genotyping array")
_COUNT_REQUIRED_PHRASES = (
    "genotyped positions",
    "including positions that were not called",
    "no-calls",
    "call rate uses that same total",
    "variant explorer",
    "missing annotation state",
    "show unannotated",
)
_VARIANT_EXPLORER_COUNT_PHRASES = (
    "missing annotation state",
    "show unannotated",
    "annotation coverage",
    "includes no-calls",
)


def test_het_zscore_comparator_is_documented() -> None:
    text = _READING_RESULTS_DOC.read_text(encoding="utf-8").lower()
    missing = [p for p in _REQUIRED_PHRASES if p not in text]
    assert not missing, (
        "docs/getting-started/reading-your-results.md no longer documents the QC "
        f"heterozygosity z-score comparator (missing {missing}). The z-score compares "
        "a sample's het rate to the account's own other same-array samples — keep that "
        "documented (#1586)."
    )


def test_dashboard_total_count_composition_is_documented() -> None:
    text = _READING_RESULTS_DOC.read_text(encoding="utf-8").lower()
    missing = [p for p in _COUNT_REQUIRED_PHRASES if p not in text]
    assert not missing, (
        "docs/getting-started/reading-your-results.md no longer explains the dashboard "
        f"sample-count denominator (missing {missing}). It must state that the prominent "
        "dashboard count is total uploaded genotyped positions including no-calls, and "
        "reconcile that denominator with call rate and the Variant Explorer's default "
        "missing-annotation-state filter (#1590)."
    )


def test_variant_explorer_default_count_is_documented() -> None:
    text = _VARIANT_EXPLORER_DOC.read_text(encoding="utf-8").lower()
    missing = [p for p in _VARIANT_EXPLORER_COUNT_PHRASES if p not in text]
    assert not missing, (
        "docs/features/variant-explorer.md no longer documents that the default Variant "
        f"Explorer count hides rows with missing annotation state (missing {missing}). "
        "Keep the default-count and Show unannotated distinction documented (#1590)."
    )
