"""Engine-agnostic imputed-VCF parser (SW-C7 shared seam).

Validates that the single :func:`parse_engine_vcf` maps Beagle (DR2/AF/IMP),
GLIMPSE2 (INFO/RAF, all-imputed), and IMPUTE5 (INFO/AF, all-imputed) output into
the same :class:`ImputedVariant`, plus the chromosome-token guard and the
malformed-float handling shared by all three engines.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from backend.analysis.imputation_vcf import (
    ImputedVariant,
    normalize_chrom,
    parse_engine_vcf,
)


def _write(path: Path, text: str, *, gz: bool = False) -> Path:
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


# Beagle shape: quality DR2, frequency AF, IMP flags imputed (typed markers lack it).
_BEAGLE_VCF = (
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
    "22\t100\trs1\tG\tA\t.\tPASS\tDR2=0.95;AF=0.30;IMP\tGT:DS\t0|1:1\n"
    "22\t200\trs2\tG\tT\t.\tPASS\tDR2=1.00;AF=0.12\tGT:DS\t0|1:1\n"  # typed (no IMP)
)

# GLIMPSE2 shape: quality INFO, frequencies RAF (panel) + AF (degenerate single-sample),
# FORMAT GT:DS:GP, no per-marker imputed flag → all imputed.
_GLIMPSE_VCF = (
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
    "22\t100\trs1\tG\tA\t.\tPASS\tRAF=0.30;AF=0.50;INFO=0.95\tGT:DS:GP\t0|1:1:0,1,0\n"
    "22\t300\trs3\tC\tA,T\t.\tPASS\tRAF=0.05,0.02;AF=0.5,0.0;INFO=0.70,0.30\tGT:DS:GP\t1|2:1,1:0,0,1\n"
)

# IMPUTE5 shape: quality INFO, frequency AF, FORMAT GT:DS, all imputed.
_IMPUTE5_VCF = (
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
    "22\t100\trs1\tG\tA\t.\tPASS\tAF=0.30;INFO=0.88\tGT:DS\t0|1:1\n"
)


class TestBeagleShape:
    def test_imp_flag_and_dr2_af(self, tmp_path: Path) -> None:
        variants = list(
            parse_engine_vcf(
                _write(tmp_path / "b.vcf", _BEAGLE_VCF),
                quality_key="DR2",
                af_key="AF",
                imputed_flag_key="IMP",
            )
        )
        by_pos = {v.pos: v for v in variants}
        assert by_pos[100].imputed is True and by_pos[100].dr2 == 0.95
        assert by_pos[100].af == 0.30 and by_pos[100].dosage == 1.0
        assert by_pos[100].best_guess_copies == 1
        assert by_pos[200].imputed is False  # typed marker (no IMP flag)


class TestGlimpseShape:
    def test_info_score_uses_raf_not_af_and_all_imputed(self, tmp_path: Path) -> None:
        variants = list(
            parse_engine_vcf(
                _write(tmp_path / "g.vcf.gz", _GLIMPSE_VCF, gz=True),
                quality_key="INFO",
                af_key="RAF",
                imputed_flag_key=None,
            )
        )
        by_key = {(v.pos, v.alt): v for v in variants}
        v = by_key[(100, "A")]
        assert v.imputed is True  # no flag key → every marker imputed
        assert v.dr2 == 0.95  # INFO score lands in the dr2 quality slot
        assert v.af == 0.30  # RAF (panel), NOT the degenerate single-sample AF=0.50
        assert v.dosage == 1.0  # DS from GT:DS:GP
        assert v.best_guess_copies == 1
        # Multi-allelic: per-ALT INFO/RAF align to each ALT.
        assert by_key[(300, "A")].dr2 == 0.70 and by_key[(300, "A")].af == 0.05
        assert by_key[(300, "T")].dr2 == 0.30 and by_key[(300, "T")].af == 0.02
        assert by_key[(300, "A")].best_guess_copies == 1
        assert by_key[(300, "T")].best_guess_copies == 1
        assert by_key[(300, "A")].imputed is True


class TestImpute5Shape:
    def test_info_score_and_af_all_imputed(self, tmp_path: Path) -> None:
        [v] = list(
            parse_engine_vcf(
                _write(tmp_path / "i.vcf", _IMPUTE5_VCF),
                quality_key="INFO",
                af_key="AF",
                imputed_flag_key=None,
            )
        )
        assert v.imputed is True
        assert v.dr2 == 0.88 and v.af == 0.30 and v.dosage == 1.0
        assert v.best_guess_copies == 1


class TestMalformedAndMissing:
    def test_out_of_range_and_nan_become_none(self, tmp_path: Path) -> None:
        vcf = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "22\t100\trsx\tA\tG\t.\tPASS\tINFO=1.2;RAF=-0.1\n"  # both out of [0,1]
            "22\t200\trsy\tA\tG\t.\tPASS\tINFO=nan;RAF=inf\n"  # non-finite
        )
        by_pos = {
            v.pos: v
            for v in parse_engine_vcf(
                _write(tmp_path / "m.vcf", vcf),
                quality_key="INFO",
                af_key="RAF",
                imputed_flag_key=None,
            )
        }
        assert by_pos[100].dr2 is None and by_pos[100].af is None
        assert by_pos[200].dr2 is None and by_pos[200].af is None

    def test_missing_keys_and_no_ds(self, tmp_path: Path) -> None:
        vcf = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
            "22\t100\trsx\tA\tG\t.\tPASS\tINFO=0.9\tGT\t0|1\n"  # no RAF, no DS
        )
        [v] = list(
            parse_engine_vcf(
                _write(tmp_path / "n.vcf", vcf),
                quality_key="INFO",
                af_key="RAF",
                imputed_flag_key=None,
            )
        )
        assert v.dr2 == 0.9 and v.af is None and v.dosage is None
        assert v.best_guess_copies == 1

    def test_multi_sample_row_rejected(self, tmp_path: Path) -> None:
        # The imputation pipeline is single-sample; a 2-sample row must fail loudly
        # rather than silently associating dosages with only the first sample.
        vcf = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\n"
            "22\t100\trsx\tA\tG\t.\tPASS\tINFO=0.9;RAF=0.2\tGT:DS\t0|0:0\t0|1:1\n"
        )
        with pytest.raises(ValueError, match="single-sample"):
            list(
                parse_engine_vcf(
                    _write(tmp_path / "ms.vcf", vcf),
                    quality_key="INFO",
                    af_key="RAF",
                    imputed_flag_key=None,
                )
            )

    def test_dosage_out_of_range_dropped(self, tmp_path: Path) -> None:
        vcf = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
            "22\t100\trsx\tA\tG\t.\tPASS\tINFO=0.9;RAF=0.2\tGT:DS\t0|1:2.5\n"  # DS>2
        )
        [v] = list(
            parse_engine_vcf(
                _write(tmp_path / "d.vcf", vcf),
                quality_key="INFO",
                af_key="RAF",
                imputed_flag_key=None,
            )
        )
        assert v.dosage is None

    def test_best_guess_copies_come_from_gt_not_ds_rounding(self, tmp_path: Path) -> None:
        vcf = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
            # DS would round down, but GT is heterozygous.
            "22\t100\trsa\tA\tG\t.\tPASS\tINFO=0.9;RAF=0.2\tGT:DS\t0|1:0.49\n"
            # DS is exactly the boundary, but GT is homozygous reference.
            "22\t200\trsb\tA\tG\t.\tPASS\tINFO=0.9;RAF=0.2\tGT:DS\t0|0:0.5\n"
            # DS is closer to het than hom-alt, but GT is homozygous ALT.
            "22\t300\trsc\tA\tG\t.\tPASS\tINFO=0.9;RAF=0.2\tGT:DS\t1|1:1.49\n"
        )
        by_pos = {
            v.pos: v
            for v in parse_engine_vcf(
                _write(tmp_path / "gt.vcf", vcf),
                quality_key="INFO",
                af_key="RAF",
                imputed_flag_key=None,
            )
        }
        assert by_pos[100].dosage == 0.49
        assert by_pos[100].best_guess_copies == 1
        assert by_pos[200].dosage == 0.5
        assert by_pos[200].best_guess_copies == 0
        assert by_pos[300].dosage == 1.49
        assert by_pos[300].best_guess_copies == 2

    def test_missing_or_malformed_gt_does_not_invent_best_guess(self, tmp_path: Path) -> None:
        vcf = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
            "22\t100\trsa\tA\tG\t.\tPASS\tINFO=0.9;RAF=0.2\tDS\t1.0\n"
            "22\t200\trsb\tA\tG\t.\tPASS\tINFO=0.9;RAF=0.2\tGT:DS\t.|1:1.0\n"
            "22\t300\trsc\tA\tG\t.\tPASS\tINFO=0.9;RAF=0.2\tGT:DS\t1:1.0\n"
            "22\t400\trsd\tA\tG\t.\tPASS\tINFO=0.9;RAF=0.2\tGT:DS\t0|1|1:1.0\n"
        )
        by_pos = {
            v.pos: v
            for v in parse_engine_vcf(
                _write(tmp_path / "missing-gt.vcf", vcf),
                quality_key="INFO",
                af_key="RAF",
                imputed_flag_key=None,
            )
        }
        assert by_pos[100].dosage == 1.0
        assert by_pos[100].best_guess_copies is None
        assert by_pos[200].dosage == 1.0
        assert by_pos[200].best_guess_copies is None
        assert by_pos[300].dosage == 1.0
        assert by_pos[300].best_guess_copies is None
        assert by_pos[400].dosage == 1.0
        assert by_pos[400].best_guess_copies is None


class TestNormalizeChrom:
    def test_valid(self) -> None:
        assert normalize_chrom("chr22") == "22"
        assert normalize_chrom("X") == "X"
        assert normalize_chrom("chrx") == "X"
        assert normalize_chrom(" 7 ") == "7"

    @pytest.mark.parametrize("bad", ["../etc", "22/x", "Y", "MT", "23", "0", ""])
    def test_rejects_unsupported_or_unsafe(self, bad: str) -> None:
        with pytest.raises(ValueError, match="unsupported chromosome"):
            normalize_chrom(bad)


def test_imputed_variant_is_frozen() -> None:
    v = ImputedVariant(chrom="22", pos=1, ref="A", alt="G", dr2=0.9, af=0.2, imputed=True)
    with pytest.raises((AttributeError, TypeError)):
        v.dr2 = 0.5  # type: ignore[misc]
