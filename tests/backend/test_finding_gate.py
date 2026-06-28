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
from backend.analysis.finding_gate import imputed_variant_surfaceable, is_surfaceable
from backend.analysis.imputation_runner import ImputedVariant


def _iv(
    *,
    imputed: bool = True,
    dr2: float | None = 0.95,
    af: float | None = 0.30,
) -> ImputedVariant:
    """Build an ImputedVariant for firewall-gate tests (defaults: imputed, well+common)."""
    return ImputedVariant(
        chrom="1", pos=100, ref="A", alt="G", dr2=dr2, af=af, imputed=imputed, dosage=1.0
    )


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


@pytest.mark.parametrize(
    "imputed,dr2,af,expected",
    [
        # A directly genotyped (non-imputed) marker always passes — the firewall N/A.
        (False, None, None, True),
        (False, 0.10, 0.0001, True),
        # Imputed AND well-imputed AND common → passes.
        (True, 0.95, 0.30, True),
        (True, 0.80, 0.01, True),  # exactly at both thresholds (inclusive)
        # Imputed but quarantined: low / missing DR2, or rare / missing AF.
        (True, 0.79, 0.30, False),  # below DR2 floor
        (True, None, 0.30, False),  # missing DR2
        (True, 0.95, 0.005, False),  # rare (MAF < 1%)
        (True, 0.95, 0.999, False),  # rare via the high-AF fold (MAF = 0.001)
        (True, 0.95, None, False),  # missing AF
    ],
)
def test_imputed_variant_surfaceable(
    imputed: bool, dr2: float | None, af: float | None, expected: bool
) -> None:
    """The firewall gate mirrors imputation_firewall.assess_variant().reportable."""
    assert imputed_variant_surfaceable(_iv(imputed=imputed, dr2=dr2, af=af)) is expected


def test_imputed_variant_surfaceable_caller_set() -> None:
    """SW-C6: the docstring states only ``imputed_findings`` wires the firewall gate.

    Lock that doc↔code agreement the same way ``test_caller_set_matches_documented_scope``
    pins ``is_surfaceable``: if a new generator opts into the imputed-finding firewall
    gate (or the sole caller is removed) without updating the finding_gate docstring's
    scope note, this fails — keeping the "rule lives in one place" doc honest.
    """
    analysis_dir = pathlib.Path(finding_gate.__file__).resolve().parent
    callers = {
        py.name
        for py in analysis_dir.glob("*.py")
        if py.name != "finding_gate.py"
        and "imputed_variant_surfaceable" in py.read_text(encoding="utf-8")
    }
    assert callers == {"imputed_findings.py"}, (
        "imputed_variant_surfaceable caller set drifted from the documented scope "
        "(SW-C6) — update finding_gate.py's docstring and this guard together. "
        f"Found: {sorted(callers)}"
    )
