"""Docs guard: the QC heterozygosity z-score's reference group stays documented (#1586).

The Dashboard QC panel renders a bare ``z-score N.NN`` under the heterozygosity
check, but the number is uninterpretable without its comparator — which is the
account's **own other samples on the same genotyping array** (min 3), *not* a
population or array-wide baseline (``backend/analysis/qc.py::het_outlier_zscore``
plus the same-array restriction in ``backend/api/routes/qc.py``, #563). The
intuitive reading (a chip-wide/population expectation) is wrong, so this locks the
comparator explanation into the user docs — it must not silently disappear.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_DOC = _REPO / "docs" / "getting-started" / "reading-your-results.md"

# Phrases that together assert the z-score's comparator is documented: it is a
# z-score, measured against the user's own samples, restricted to one array type.
_REQUIRED_PHRASES = ("z-score", "your own other", "same genotyping array")


def test_het_zscore_comparator_is_documented() -> None:
    text = _DOC.read_text(encoding="utf-8").lower()
    missing = [p for p in _REQUIRED_PHRASES if p not in text]
    assert not missing, (
        "docs/getting-started/reading-your-results.md no longer documents the QC "
        f"heterozygosity z-score comparator (missing {missing}). The z-score compares "
        "a sample's het rate to the account's own other same-array samples — keep that "
        "documented (#1586)."
    )
