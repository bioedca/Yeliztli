"""Tests for shared allelic-state labeling helpers."""

from __future__ import annotations

import pytest

from backend.analysis.allelic_state import (
    ALLELIC_STATE_HEMIZYGOUS,
    ALLELIC_STATE_MAP,
    allelic_state_coding,
    allelic_state_coding_for_call,
    allelic_state_label,
    allelic_state_label_for_call,
    is_grch37_y_par_position,
)


@pytest.mark.parametrize(
    ("chrom", "pos", "sex"),
    [
        ("1", 100_000, "XX"),
        ("X", 10_000_000, "XY"),
        ("Y", 10_000_000, "XY"),
        ("X", 100_000, "XY"),
        ("MT", 1_000, "XX"),
        ("chrM", 1_000, None),
        (None, None, None),
    ],
)
def test_reference_display_is_distinct_from_unknown_without_a_loinc_code(chrom, pos, sex):
    """#2389: a stored reference state needs no ploidy guess or invented code."""
    reference = {"chrom": chrom, "pos": pos, "zygosity": "hom_ref"}
    assert allelic_state_label(reference, sex) == "Reference call"
    assert allelic_state_label_for_call(**reference, sex=sex) == "Reference call"
    assert allelic_state_coding(reference, sex) is None
    assert allelic_state_coding_for_call(**reference, sex=sex) is None

    for state in (None, "", "unknown"):
        unknown = {**reference, "zygosity": state}
        assert allelic_state_label(unknown, sex) is None
        assert allelic_state_label_for_call(**unknown, sex=sex) is None
        assert allelic_state_coding(unknown, sex) is None


@pytest.mark.parametrize(
    ("chrom", "pos", "zygosity", "sex", "expected"),
    [
        ("1", 100_000, "het", "XX", "Heterozygous"),
        ("1", 100_000, "hom_alt", "XX", "Homozygous"),
        ("X", 10_000_000, "hom_alt", "XY", "Hemizygous"),
        ("Y", 10_000_000, "hom_alt", "XY", "Hemizygous"),
        ("MT", 1_000, "het", None, "Heteroplasmic"),
        ("MT", 1_000, "hom_alt", None, "Homoplasmic"),
    ],
)
def test_carried_display_still_matches_its_loinc_code(chrom, pos, zygosity, sex, expected):
    row = {"chrom": chrom, "pos": pos, "zygosity": zygosity}
    coding = allelic_state_coding(row, sex)
    assert coding is not None
    assert coding["display"] == expected
    assert allelic_state_label(row, sex) == expected
    assert allelic_state_label_for_call(**row, sex=sex) == expected


def test_grch37_y_par2_boundaries_are_pseudoautosomal() -> None:
    assert is_grch37_y_par_position(59_034_050)
    assert is_grch37_y_par_position(59_198_808)
    assert is_grch37_y_par_position(59_363_566)

    assert not is_grch37_y_par_position(59_034_049)
    assert not is_grch37_y_par_position(59_363_567)


def test_xy_y_par2_hom_alt_remains_diploid_homozygous() -> None:
    coding = allelic_state_coding_for_call(
        chrom="Y",
        pos=59_198_808,
        zygosity="hom_alt",
        sex="XY",
    )

    assert coding == ALLELIC_STATE_MAP["hom_alt"]
    assert coding["code"] == "LA6705-3"
    assert coding["display"] == "Homozygous"


def test_xy_y_just_outside_par2_hom_alt_is_hemizygous() -> None:
    coding = allelic_state_coding_for_call(
        chrom="Y",
        pos=59_363_567,
        zygosity="hom_alt",
        sex="XY",
    )

    assert coding == ALLELIC_STATE_HEMIZYGOUS
