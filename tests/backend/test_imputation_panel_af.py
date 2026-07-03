"""1000G Phase 3 v5a panel AF source for Beagle imputation firewall."""

from __future__ import annotations

import gzip
from pathlib import Path

from backend.annotation.imputation_panel_af import (
    PanelAfLookup,
    build_panel_af_index,
    infer_panel_af_chrom,
    panel_af_path,
    panel_vcf_path,
)


def _write_gz(path: Path, text: str) -> Path:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(text)
    return path


class TestBuildPanelAfIndex:
    def test_builds_per_alt_af_from_panel_vcf_genotypes(self, tmp_path: Path) -> None:
        vcf = _write_gz(
            tmp_path / "chr22.1kg.phase3.v5a.vcf.gz",
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\n"
            "22\t100\trs1\tG\tA\t.\tPASS\t.\tGT\t0|1\t1|1\n"
            "22\t200\trs2\tC\tA,T\t.\tPASS\t.\tGT\t1|2\t0|2\n",
        )
        out = panel_af_path(tmp_path, "22")

        n = build_panel_af_index(vcf, out)

        assert n == 3
        lookup = PanelAfLookup(tmp_path)
        assert lookup.lookup("chr22", 100, "g", "a") == 0.75  # 3 ALT copies / 4 alleles
        assert lookup.lookup("22", 200, "C", "A") == 0.25  # 1 / 4
        assert lookup.lookup("22", 200, "C", "T") == 0.50  # 2 / 4

    def test_build_can_use_info_af_when_no_samples(self, tmp_path: Path) -> None:
        vcf = _write_gz(
            tmp_path / "chr21.1kg.phase3.v5a.vcf.gz",
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "21\t300\trs3\tA\tG,C\t.\tPASS\tAF=0.125,0.25\n",
        )

        n = build_panel_af_index(vcf, panel_af_path(tmp_path, "21"))

        lookup = PanelAfLookup(tmp_path)
        assert n == 2
        assert lookup.lookup("21", 300, "A", "G") == 0.125
        assert lookup.lookup("21", 300, "A", "C") == 0.25

    def test_build_skips_symbolic_and_placeholder_alts(self, tmp_path: Path) -> None:
        vcf = _write_gz(
            tmp_path / "chr22.1kg.phase3.v5a.vcf.gz",
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
            "22\t100\trsa\tG\t.\t.\tPASS\t.\tGT\t0|0\n"
            "22\t200\trsb\tG\t*\t.\tPASS\t.\tGT\t0|1\n"
            "22\t300\trsc\tG\t<DEL>\t.\tPASS\t.\tGT\t0|1\n"
            "22\t400\trsd\tG\tG[1:10[\t.\tPASS\t.\tGT\t0|1\n"
            "22\t500\trse\tG\tA\t.\tPASS\t.\tGT\t0|1\n",
        )

        n = build_panel_af_index(vcf, panel_af_path(tmp_path, "22"))

        lookup = PanelAfLookup(tmp_path)
        assert n == 1
        assert lookup.lookup("22", 100, "G", ".") is None
        assert lookup.lookup("22", 200, "G", "*") is None
        assert lookup.lookup("22", 300, "G", "<DEL>") is None
        assert lookup.lookup("22", 400, "G", "G[1:10[") is None
        assert lookup.lookup("22", 500, "G", "A") == 0.5


class TestPanelAfLookup:
    def test_duplicate_exact_key_is_ambiguous_and_fails_closed(self, tmp_path: Path) -> None:
        _write_gz(
            panel_af_path(tmp_path, "22"),
            "chrom\tpos\tref\talt\taf\n"
            "22\t100\tG\tA\t0.30\n"
            "22\t100\tG\tA\t0.40\n"
            "22\t200\tC\tT\t0.20\n",
        )

        lookup = PanelAfLookup(tmp_path)

        assert lookup.lookup("22", 100, "G", "A") is None
        assert lookup.lookup("22", 200, "C", "T") == 0.20

    def test_missing_source_returns_none(self, tmp_path: Path) -> None:
        lookup = PanelAfLookup(tmp_path)
        assert lookup.lookup("22", 100, "G", "A") is None

    def test_canonical_paths_and_chrom_inference(self, tmp_path: Path) -> None:
        assert panel_vcf_path(tmp_path, "chrX").name == "chrX.1kg.phase3.v5a.vcf.gz"
        path = panel_af_path(tmp_path, "chrX")
        assert path.name == "chrX.1kg.phase3.v5a.b37.af.tsv.gz"
        assert infer_panel_af_chrom(path) == "X"
        assert infer_panel_af_chrom(tmp_path / "not-panel.tsv") is None
