"""Tests for shared allelic-state labeling helpers."""

from __future__ import annotations

from backend.analysis.allelic_state import (
    ALLELIC_STATE_HEMIZYGOUS,
    ALLELIC_STATE_MAP,
    allelic_state_coding_for_call,
    is_grch37_y_par_position,
)


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
