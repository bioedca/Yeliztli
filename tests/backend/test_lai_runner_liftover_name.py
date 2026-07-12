"""Regression guard: LAIRunner must accept the v2.0.0 liftover filename.

The v2.0.0 LAI bundle renamed the rsID->GRCh38 liftover table
``liftover/rsid_to_grch38.tsv`` (v1.1) -> ``liftover/array_site_mapping.tsv``
(``scripts/lai_bundle_v2/07_assemble_bundle.sh``; identical 3-column format).
The runtime ``LAIRunner`` previously hardcoded the v1.1 name, so LAI inference
raised ``FileNotFoundError`` against every published v2.0.0 bundle. These tests
lock in that both names resolve and that a genuinely absent table still errors.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.analysis.lai_runner import LAIRunner


def _make_stub_bundle(root: Path, liftover_name: str | None) -> Path:
    """Build a minimal bundle whose component files exist (empty stubs).

    ``_validate_bundle`` only checks for *existence*, and ``__init__`` reads the
    liftover table, so empty stubs + a small liftover TSV are enough to drive
    ``LAIRunner.__init__`` without invoking Beagle/gnomix.
    """
    (root / "beagle").mkdir(parents=True)
    (root / "beagle" / "beagle.jar").touch()
    for c in range(1, 23):
        (root / "phasing_panel").mkdir(exist_ok=True)
        (root / "phasing_panel" / f"ref_panel_chr{c}.vcf.gz").touch()
        gm = root / "gnomix_models" / f"chr{c}"
        gm.mkdir(parents=True)
        for f in ("metadata.npz", "base_coefs.npz", "smoother.json"):
            (gm / f).touch()
        (root / "genetic_maps").mkdir(exist_ok=True)
        (root / "genetic_maps" / f"plink.chrchr{c}.GRCh38.map").touch()
    (root / "liftover").mkdir()
    if liftover_name is not None:
        (root / "liftover" / liftover_name).write_text(
            "rs1\tchr1\t100\nrs2\tchr2\t200\n", encoding="utf-8"
        )
    return root


def test_v2_0_0_liftover_name_is_accepted(tmp_path: Path) -> None:
    bundle = _make_stub_bundle(tmp_path / "v2", "array_site_mapping.tsv")
    runner = LAIRunner(bundle)
    assert runner.rsid_lookup == {"rs1": ("chr1", 100), "rs2": ("chr2", 200)}


def test_v1_1_liftover_name_still_works(tmp_path: Path) -> None:
    bundle = _make_stub_bundle(tmp_path / "v1", "rsid_to_grch38.tsv")
    runner = LAIRunner(bundle)
    assert runner.rsid_lookup == {"rs1": ("chr1", 100), "rs2": ("chr2", 200)}


def test_numeric_liftover_chromosome_reaches_phasing(tmp_path: Path) -> None:
    """Legacy bare autosomes are canonicalized before VCF/phasing dispatch."""
    bundle = _make_stub_bundle(tmp_path / "numeric", "array_site_mapping.tsv")
    (bundle / "liftover" / "array_site_mapping.tsv").write_text("rs1\t1\t100\n", encoding="utf-8")
    runner = LAIRunner(bundle)
    out = tmp_path / "run"

    with (
        patch.object(
            LAIRunner,
            "_write_single_vcf",
            return_value={"": {"hits": 1, "drops": 0}},
        ),
        patch.object(LAIRunner, "_phase_chromosome", return_value=None) as phase,
        pytest.raises(RuntimeError, match="no chromosome was successfully phased"),
    ):
        runner.run(
            genotypes=[{"rsid": "rs1", "chrom": "1", "pos": 100, "genotype": "AA"}],
            output_dir=out,
            cleanup=False,
            file_format="23andme_v5",
        )

    assert runner.rsid_lookup == {"rs1": ("chr1", 100)}
    phase.assert_called_once_with(1, out / "unphased_vcfs" / "user_chr1.vcf.gz", out)


def test_liftover_without_autosome_is_rejected(tmp_path: Path) -> None:
    """A structurally complete bundle still needs a usable autosomal row."""
    bundle = _make_stub_bundle(tmp_path / "invalid", "array_site_mapping.tsv")
    (bundle / "liftover" / "array_site_mapping.tsv").write_text(
        "rs1\tchrX\t100\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="no supported autosomal mappings"):
        LAIRunner(bundle)


def test_duplicate_rsid_uses_final_chromosome_scope(tmp_path: Path) -> None:
    """Autosomal availability follows the loader's last-write-wins lookup."""
    bundle = _make_stub_bundle(tmp_path / "duplicate", "array_site_mapping.tsv")
    (bundle / "liftover" / "array_site_mapping.tsv").write_text(
        "rs1\tchr1\t100\nrs1\tchrX\t200\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="no supported autosomal mappings"):
        LAIRunner(bundle)


def test_supplemental_alt_contig_rows_remain_compatible(tmp_path: Path) -> None:
    """Published-v2 alternate contigs coexist with usable primary autosomes."""
    bundle = _make_stub_bundle(tmp_path / "alt", "array_site_mapping.tsv")
    (bundle / "liftover" / "array_site_mapping.tsv").write_text(
        "rs1\tchr1\t100\nrs_alt\tchr1_KI270766v1_alt\t200\n",
        encoding="utf-8",
    )

    runner = LAIRunner(bundle)

    assert runner.rsid_lookup == {
        "rs1": ("chr1", 100),
        "rs_alt": ("chr1_KI270766v1_alt", 200),
    }


def test_missing_liftover_table_raises(tmp_path: Path) -> None:
    bundle = _make_stub_bundle(tmp_path / "none", None)
    with pytest.raises(FileNotFoundError, match="array_site_mapping.tsv"):
        LAIRunner(bundle)
