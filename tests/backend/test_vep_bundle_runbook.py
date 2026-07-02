"""Regression guards for the VEP bundle release runbook."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = REPO_ROOT / "docs" / "bundle-release-runbook.md"


def _section(text: str, heading: str, next_heading: str) -> str:
    try:
        start = text.index(heading)
    except ValueError as exc:
        raise AssertionError(f"missing heading {heading!r}") from exc
    try:
        end = text.index(next_heading, start)
    except ValueError as exc:
        raise AssertionError(f"missing heading {next_heading!r} after {heading!r}") from exc
    return text[start:end]


def _bash_block_containing(text: str, marker: str) -> str:
    for block in text.split("```bash")[1:]:
        body = block.split("```", 1)[0]
        if marker in body:
            return body
    raise AssertionError(f"missing bash block containing {marker!r}")


@pytest.fixture(scope="module")
def runbook_text() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def build_section(runbook_text: str) -> str:
    return _section(runbook_text, "## 3. Run VEP and build the bundle", "## 4.")


def test_vep_runbook_records_canonical_vep_recipe(runbook_text: str, build_section: str) -> None:
    """The production VEP recipe must emit CANONICAL for GRCh37 builds (#1358)."""
    for required in (
        "variation_feature.txt.gz",
        "seq_region.txt.gz",
        "map_weight = 1",
        "feature_key = (rsid, chrom, start, end, alleles, strand_symbol)",
        "vep_default_input.unsorted.txt",
        "vep_default_input.txt",
        "sort -k1,1V -k2,2n",
        "--cache",
        "--offline",
        "--dir_cache vep_cache",
        "--fasta Homo_sapiens.GRCh37.dna.primary_assembly.fa",
        "--vcf",
        "--symbol",
        "--hgvs",
        "--numbers",
        "--canonical",
        "scripts/build_vep_bundle.py",
        "--bundle-version",
        "--rsid-catalog union_sites.tsv",
        "--write-stats",
    ):
        assert required in build_section, f"missing canonical recipe string: {required!r}"

    assert "bundle-v4.0.0" in runbook_text

    vep_command = _bash_block_containing(build_section, "vep \\")
    assert "--format id" not in vep_command
    assert "--database" not in vep_command


def test_vep_runbook_records_canonical_validation_queries(build_section: str) -> None:
    """Release checks must verify canonical rows and targeted HGVS regressions."""
    for required in (
        "SELECT mane_select, COUNT(*) FROM vep_annotations GROUP BY mane_select",
        "rs771467011",
        "p.Leu98=",
        "rs1801133",
        "ENST00000376592",
        "c.665C>T",
        "p.Ala222Val",
    ):
        assert required in build_section, f"missing canonical validation string: {required!r}"
