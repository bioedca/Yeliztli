"""Per-chromosome GRCh37 imputation-input VCF prep (Wave C glue).

Pins the reference-aligned biallelic-SNP filter (zygosity -> GT, indels/no-calls/
multi-allelic/unresolved-REF dropped), the autosome-only scope (X/Y/MT excluded),
the coordinate-sorted single-contig VCF text (bare GRCh37 #CHROM token), and the
end-to-end DB -> bgzipped+tabix-indexed chr{N}.vcf.gz writer (read back via pysam).
"""

from __future__ import annotations

from pathlib import Path

import pysam
import pytest
import sqlalchemy as sa

from backend.analysis.imputation_input import (
    INPUT_CHROMOSOMES,
    InputSite,
    build_chrom_vcf_text,
    collect_input_sites,
    encode_input_gt,
    write_imputation_input_vcfs,
)
from backend.db.tables import annotated_variants


@pytest.fixture
def sample_engine() -> sa.Engine:
    from backend.db.sample_schema import create_sample_tables

    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    return engine


def _insert(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(annotated_variants.insert(), rows)


def _row(rsid, chrom, pos, ref, alt, zyg, gt="") -> dict:
    return {
        "rsid": rsid,
        "chrom": chrom,
        "pos": pos,
        "ref": ref,
        "alt": alt,
        "zygosity": zyg,
        "genotype": gt,
    }


class TestEncodeInputGt:
    @pytest.mark.parametrize(
        ("zyg", "expected"),
        [("hom_ref", "0/0"), ("het", "0/1"), ("hom_alt", "1/1")],
    )
    def test_reference_aligned_snp(self, zyg, expected) -> None:
        assert encode_input_gt("A", "G", zyg) == expected

    def test_lowercase_alleles_accepted(self) -> None:
        assert encode_input_gt("a", "g", "het") == "0/1"

    @pytest.mark.parametrize("zyg", [None, "", "no_call", "unknown"])
    def test_unresolved_zygosity_dropped(self, zyg) -> None:
        assert encode_input_gt("A", "G", zyg) is None

    def test_unresolved_reference_n_dropped(self) -> None:
        # The vcf_export "honest fallback" REF=N can't align to the SNP panel.
        assert encode_input_gt("N", "G", "het") is None

    @pytest.mark.parametrize(
        ("ref", "alt"),
        [("AT", "G"), ("A", "ATG"), ("A", "G,T"), ("-", "G"), ("A", ""), ("A", None)],
    )
    def test_non_snp_dropped(self, ref, alt) -> None:
        assert encode_input_gt(ref, alt, "het") is None

    def test_ref_equals_alt_dropped(self) -> None:
        assert encode_input_gt("A", "A", "hom_alt") is None


class TestCollectInputSites:
    def test_groups_autosomes_and_drops_out_of_scope(self) -> None:
        rows = [
            ("rs1", "1", 100, "A", "G", "het"),  # emit
            ("rs2", "1", 50, "C", "T", "hom_alt"),  # emit (earlier pos)
            ("rs3", "2", 200, "G", "A", "hom_ref"),  # emit
            ("rs4", "22", 300, "AT", "A", "het"),  # drop: indel
            ("rs5", "7", 400, "A", "G", None),  # drop: no-call zygosity
            ("rs6", "X", 500, "A", "G", "het"),  # drop: out-of-scope chrom
            ("rs7", "Y", 600, "A", "G", "het"),  # drop: out-of-scope chrom
        ]
        by_chrom, n_total, n_emitted = collect_input_sites(rows)
        assert n_total == 7
        assert n_emitted == 3
        assert {c: [s.rsid for s in sites] for c, sites in by_chrom.items() if sites} == {
            "1": ["rs1", "rs2"],
            "2": ["rs3"],
        }
        # No X/Y key carries sites.
        assert "X" not in by_chrom
        assert all(not by_chrom[c] for c in by_chrom if c not in ("1", "2"))

    def test_restricting_chromosomes(self) -> None:
        rows = [("rs1", "1", 100, "A", "G", "het"), ("rs2", "2", 100, "A", "G", "het")]
        by_chrom, n_total, n_emitted = collect_input_sites(rows, chromosomes=("1",))
        assert n_total == 2
        assert n_emitted == 1
        assert list(by_chrom) == ["1"]


class TestBuildChromVcfText:
    def test_sorted_single_contig_bare_chrom_token(self) -> None:
        sites = [
            InputSite(pos=300, rsid="rsB", ref="A", alt="G", gt="0/1"),
            InputSite(pos=100, rsid="rsA", ref="C", alt="T", gt="1/1"),
        ]
        text = build_chrom_vcf_text("22", sites, sample_name="S1")
        lines = text.splitlines()
        assert lines[0] == "##fileformat=VCFv4.2"
        assert "##contig=<ID=22>" in lines
        assert '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">' in lines
        header = next(line for line in lines if line.startswith("#CHROM"))
        assert header.endswith("\tS1")
        data = [line for line in lines if not line.startswith("#")]
        # Sorted by position; bare "22" token (not "chr22"); full record shape.
        assert data[0] == "22\t100\trsA\tC\tT\t.\tPASS\t.\tGT\t1/1"
        assert data[1] == "22\t300\trsB\tA\tG\t.\tPASS\t.\tGT\t0/1"

    def test_sample_name_sanitized(self) -> None:
        text = build_chrom_vcf_text(
            "1", [InputSite(pos=1, rsid="rs1", ref="A", alt="G", gt="0/1")], sample_name="a\tb\nc"
        )
        header = next(line for line in text.splitlines() if line.startswith("#CHROM"))
        assert header.endswith("\tabc")  # tabs/newlines stripped


class TestWriteImputationInputVcfs:
    def test_end_to_end_writes_and_filters(self, sample_engine: sa.Engine, tmp_path: Path) -> None:
        _insert(
            sample_engine,
            [
                _row("rs1", "1", 200, "A", "G", "het"),
                _row("rs2", "1", 100, "C", "T", "hom_alt"),
                _row("rs3", "2", 500, "G", "A", "hom_ref"),
                _row("rs4", "22", 300, "AT", "A", "het"),  # indel -> dropped
                _row("rs5", "X", 700, "A", "G", "het"),  # out-of-scope -> dropped
                _row("rs6", "7", 800, "A", "G", None),  # no-call -> dropped
            ],
        )
        result = write_imputation_input_vcfs(sample_engine, tmp_path / "vcfs")

        assert result.n_total == 6
        assert result.n_emitted == 3
        assert result.n_dropped == 3
        assert result.per_chrom_emitted == {"1": 2, "2": 1}
        assert set(result.vcf_paths) == {"1", "2"}

        chr1 = tmp_path / "vcfs" / "chr1.vcf.gz"
        assert chr1.exists()
        assert (tmp_path / "vcfs" / "chr1.vcf.gz.tbi").exists()
        assert not (tmp_path / "vcfs" / "chr22.vcf.gz").exists()
        assert not (tmp_path / "vcfs" / "chrX.vcf.gz").exists()

        with pysam.VariantFile(str(chr1)) as vf:
            recs = list(vf)
        # Sorted by pos; reference-aligned GT preserved; bare contig "1".
        assert [(r.chrom, r.pos, r.id) for r in recs] == [("1", 100, "rs2"), ("1", 200, "rs1")]
        assert recs[0].samples[0]["GT"] == (1, 1)  # hom_alt
        assert recs[1].samples[0]["GT"] == (0, 1)  # het

    def test_empty_db_writes_nothing(self, sample_engine: sa.Engine, tmp_path: Path) -> None:
        result = write_imputation_input_vcfs(sample_engine, tmp_path / "vcfs")
        assert result.n_total == 0
        assert result.n_emitted == 0
        assert result.vcf_paths == {}

    def test_default_scope_is_autosomes(self) -> None:
        assert INPUT_CHROMOSOMES == tuple(str(i) for i in range(1, 23))
        assert "X" not in INPUT_CHROMOSOMES
