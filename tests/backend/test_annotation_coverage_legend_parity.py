"""Cross-stack parity guard for the annotation_coverage bitmask legend (#580).

The variant-table "Annotations" column decodes ``annotated_variants.annotation_coverage``
into source labels via a frontend copy of the bit→label map
(``frontend/src/components/variant-table/annotation-coverage.ts`` ``COVERAGE_BITS``).
The canonical definitions live in the backend
(``backend/analysis/provenance.py::_COVERAGE_BITS``, mirroring
``backend/annotation/engine.py``). Each previously had no test comparing them, so
the decoded column could silently mislabel a source if either side changed. This
guard fails on any drift (same cross-stack-parity pattern as #565/#574).
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.analysis.provenance import _COVERAGE_BITS

_FRONTEND_TS = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "components"
    / "variant-table"
    / "annotation-coverage.ts"
)


def _parse_frontend_coverage_bits(ts: str) -> list[tuple[int, str]]:
    """Extract the ``[0b…, "Label"]`` pairs from the frontend COVERAGE_BITS map.

    Only ``COVERAGE_BITS`` uses ``[0b…, "…"]`` literals in this file, so a
    whole-file scan for that shape is unambiguous and sidesteps nested-bracket
    parsing of the array body.
    """
    assert "COVERAGE_BITS" in ts, "COVERAGE_BITS not found in annotation-coverage.ts"
    pairs = re.findall(r"\[\s*(0b[01]+)\s*,\s*\"([^\"]+)\"\s*\]", ts)
    return [(int(bit, 0), label) for bit, label in pairs]


def test_frontend_annotation_coverage_legend_file_exists() -> None:
    assert _FRONTEND_TS.exists(), (
        f"annotation-coverage.ts not found at {_FRONTEND_TS} — update this "
        "cross-stack parity guard if the decode module was moved/renamed."
    )


def test_frontend_coverage_bits_match_backend_constant() -> None:
    frontend = _parse_frontend_coverage_bits(_FRONTEND_TS.read_text(encoding="utf-8"))
    assert frontend, "no [bit, label] pairs parsed from annotation-coverage.ts COVERAGE_BITS"
    assert frontend == list(_COVERAGE_BITS), (
        "frontend COVERAGE_BITS has DRIFTED from backend _COVERAGE_BITS — the "
        "annotation_coverage legend must stay identical across the stack so the "
        "decoded 'Annotations' column can't mislabel a source (#580).\n"
        f"  backend : {list(_COVERAGE_BITS)}\n"
        f"  frontend: {frontend}"
    )
