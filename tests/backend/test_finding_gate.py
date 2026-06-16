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

import pytest

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
