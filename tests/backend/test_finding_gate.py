"""Truth-table coverage for the shared finding-surfacing gate (F8).

``is_surfaceable`` (``backend/analysis/finding_gate.py``) is the single predicate
that suppresses a finding contradicting the inferred sex — today, a Y-chromosome
finding on a confidently-``"XX"`` sample (biologically impossible). It had **zero**
test coverage anywhere in the suite (#711): its sole production caller only runs
when ``inferred_sex`` is set, no test set it, and no chrY fixture existed — so a
regression that removed or inverted the gate shipped fully green. This pins every
row of its small truth table, especially the one row that matters (Y + XX).
"""

from __future__ import annotations

import pathlib

import pytest

from backend.analysis import finding_gate
from backend.analysis.finding_gate import is_surfaceable


@pytest.mark.parametrize(
    "chrom,inferred_sex,expected",
    [
        # The only suppressing case: a Y-chromosome finding on a confident XX
        # sample. Normalisation is case-insensitive and whitespace-trimmed.
        ("Y", "XX", False),
        ("chrY", "XX", False),
        ("CHRY", "XX", False),
        ("y", "XX", False),
        ("  Y  ", "XX", False),
        # A Y finding on XY is real — never dropped.
        ("Y", "XY", True),
        ("chrY", "XY", True),
        # Sex not confidently known → never drop (a false drop would hide a real
        # finding): manual_review / unknown / None / empty all surface.
        ("Y", "manual_review", True),
        ("Y", "unknown", True),
        ("Y", None, True),
        ("Y", "", True),
        # Non-Y chromosomes are never gated by this rule, for any sex.
        ("17", "XX", True),
        ("X", "XX", True),  # X is not Y — an X finding on XX is expected
        ("MT", "XX", True),
        # Missing / blank chromosome is surfaceable (no contradiction provable).
        (None, "XX", True),
        ("", "XX", True),
        ("   ", "XX", True),
    ],
)
def test_is_surfaceable(chrom: str | None, inferred_sex: str | None, expected: bool) -> None:
    assert is_surfaceable(chrom, inferred_sex) is expected


def test_caller_set_matches_documented_scope() -> None:
    """#851: the docstring states only ``rare_variant_finder`` wires the gate today.

    Lock that doc↔code agreement: scan ``backend/analysis/*.py`` for ``is_surfaceable``
    references and assert the caller set is exactly the documented one. If a new
    generator opts in (or the sole caller is removed) without updating the
    finding_gate docstring's scope note, this fails — closing the drift the issue
    reported (a "single predicate every generator consults" doc beside one caller).
    ``sex_aneuploidy`` / ``kinship`` are exempt by design (see the module docstring).
    """
    analysis_dir = pathlib.Path(finding_gate.__file__).resolve().parent
    callers = {
        py.name
        for py in analysis_dir.glob("*.py")
        if py.name != "finding_gate.py" and "is_surfaceable" in py.read_text(encoding="utf-8")
    }
    assert callers == {"rare_variant_finder.py"}, (
        "is_surfaceable caller set drifted from the documented scope (#851) — update "
        f"finding_gate.py's docstring and this guard together. Found: {sorted(callers)}"
    )
