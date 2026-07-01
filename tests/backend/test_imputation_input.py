"""Per-chromosome GRCh37 imputation-input VCF prep (Wave C glue).

Pins the reference-aligned biallelic-SNP filter (zygosity -> GT, indels/no-calls/
multi-allelic/unresolved-REF dropped), the autosome default scope, the
ploidy-aware chromosome-X region path, the coordinate-sorted single-contig VCF
text (bare GRCh37 #CHROM token), and the end-to-end DB -> bgzipped+tabix-indexed
writer (read back via pysam).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pysam
import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from backend.analysis.imputation_input import (
    AUTOSOMAL_INPUT_CHROMOSOMES,
    DEFAULT_INPUT_CHROMOSOMES,
    INPUT_CHROMOSOMES,
    InputSite,
    build_chrom_vcf_text,
    collect_input_sites,
    encode_input_gt,
    input_unit_specs_for_chromosomes,
    write_imputation_input_vcfs,
)
from backend.annotation.engine import CLINVAR_BIT, VEP_BIT, _merge_annotations, run_annotation
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, raw_variants, reference_metadata


@pytest.fixture
def sample_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    return engine


def _threadsafe_sqlite_engine() -> sa.Engine:
    return sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _create_minimal_reference_engine() -> sa.Engine:
    engine = _threadsafe_sqlite_engine()
    reference_metadata.create_all(engine)
    return engine


def _create_minimal_vep_engine(rows: list[dict]) -> sa.Engine:
    engine = _threadsafe_sqlite_engine()
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE vep_annotations ("
                "  rsid TEXT, chrom TEXT, pos INTEGER,"
                "  ref TEXT, alt TEXT, gene_symbol TEXT,"
                "  transcript_id TEXT, consequence TEXT,"
                "  hgvs_coding TEXT, hgvs_protein TEXT,"
                "  strand TEXT, exon_number INTEGER,"
                "  intron_number INTEGER, mane_select INTEGER"
                ")"
            )
        )
        conn.execute(sa.text("CREATE INDEX idx_vep_rsid ON vep_annotations(rsid)"))
        conn.execute(
            sa.text(
                "INSERT INTO vep_annotations "
                "(rsid, chrom, pos, ref, alt, gene_symbol, transcript_id, consequence, "
                "hgvs_coding, hgvs_protein, strand, exon_number, intron_number, mane_select) "
                "VALUES (:rsid, :chrom, :pos, :ref, :alt, :gene_symbol, :transcript_id, "
                ":consequence, :hgvs_coding, :hgvs_protein, :strand, :exon_number, "
                ":intron_number, :mane_select)"
            ),
            rows,
        )
    return engine


class _MinimalRegistry:
    def __init__(self, *, reference_engine: sa.Engine, vep_engine: sa.Engine) -> None:
        self.reference_engine = reference_engine
        self.vep_engine = vep_engine

    @property
    def gnomad_engine(self) -> sa.Engine:
        raise FileNotFoundError("gnomAD not configured for this regression")

    @property
    def dbnsfp_engine(self) -> sa.Engine:
        raise FileNotFoundError("dbNSFP not configured for this regression")

    @property
    def alphamissense_engine(self) -> sa.Engine:
        raise FileNotFoundError("AlphaMissense not configured for this regression")


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

    @pytest.mark.parametrize(
        ("zyg", "expected"),
        [("hom_ref", "0"), ("hom_alt", "1"), ("het", None)],
    )
    def test_haploid_xy_nonpar_encoding(self, zyg, expected) -> None:
        assert encode_input_gt("A", "G", zyg, ploidy="haploid") == expected


class TestCollectInputSites:
    def test_run_annotation_vep_only_snp_emits_imputation_site(self, tmp_path: Path) -> None:
        sample_engine = _threadsafe_sqlite_engine()
        create_sample_tables(sample_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [{"rsid": "rs_vep_only", "chrom": "1", "pos": 12345, "genotype": "AG"}],
            )

        reference_engine = _create_minimal_reference_engine()
        vep_engine = _create_minimal_vep_engine(
            [
                {
                    "rsid": "rs_vep_only",
                    "chrom": "1",
                    "pos": 12345,
                    "ref": "A",
                    "alt": "G",
                    "gene_symbol": "GENE",
                    "transcript_id": "ENST00000000000",
                    "consequence": "intron_variant",
                    "hgvs_coding": None,
                    "hgvs_protein": None,
                    "strand": "+",
                    "exon_number": None,
                    "intron_number": 1,
                    "mane_select": 0,
                }
            ]
        )
        registry = _MinimalRegistry(reference_engine=reference_engine, vep_engine=vep_engine)

        result = run_annotation(sample_engine, registry, batch_size=1)

        assert result.errors == []
        assert result.source_failures == {}
        assert result.rows_written == 1
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs_vep_only")
            ).one()
        assert row.annotation_coverage == VEP_BIT
        assert row.ref == "A"
        assert row.alt == "G"
        assert row.zygosity == "het"

        imputation_result = write_imputation_input_vcfs(
            sample_engine, tmp_path / "vcfs", chromosomes=("1",)
        )

        assert imputation_result.n_total == 1
        assert imputation_result.n_emitted == 1
        chr1 = tmp_path / "vcfs" / "chr1.vcf.gz"
        with pysam.VariantFile(str(chr1)) as vf:
            recs = list(vf)
        assert [(r.chrom, r.pos, r.id, r.ref, r.alts) for r in recs] == [
            ("1", 12345, "rs_vep_only", "A", ("G",))
        ]
        assert recs[0].samples[0]["GT"] == (0, 1)

    def test_run_annotation_ambiguous_vep_alleles_do_not_emit_imputation_site(
        self, tmp_path: Path
    ) -> None:
        sample_engine = _threadsafe_sqlite_engine()
        create_sample_tables(sample_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [{"rsid": "rs_multi_alt", "chrom": "1", "pos": 12345, "genotype": "AG"}],
            )

        reference_engine = _create_minimal_reference_engine()
        vep_engine = _create_minimal_vep_engine(
            [
                {
                    "rsid": "rs_multi_alt",
                    "chrom": "1",
                    "pos": 12345,
                    "ref": "A",
                    "alt": "G",
                    "gene_symbol": "GENE",
                    "transcript_id": "ENST00000000001",
                    "consequence": "intron_variant",
                    "hgvs_coding": None,
                    "hgvs_protein": None,
                    "strand": "+",
                    "exon_number": None,
                    "intron_number": 1,
                    "mane_select": 0,
                },
                {
                    "rsid": "rs_multi_alt",
                    "chrom": "1",
                    "pos": 12345,
                    "ref": "A",
                    "alt": "T",
                    "gene_symbol": "GENE",
                    "transcript_id": "ENST00000000002",
                    "consequence": "missense_variant",
                    "hgvs_coding": None,
                    "hgvs_protein": None,
                    "strand": "+",
                    "exon_number": 1,
                    "intron_number": None,
                    "mane_select": 0,
                },
            ]
        )
        registry = _MinimalRegistry(reference_engine=reference_engine, vep_engine=vep_engine)

        result = run_annotation(sample_engine, registry, batch_size=1)

        assert result.errors == []
        assert result.rows_written == 1
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs_multi_alt")
            ).one()
        assert row.annotation_coverage == VEP_BIT
        assert row.ref is None
        assert row.alt is None
        assert row.zygosity is None

        imputation_result = write_imputation_input_vcfs(
            sample_engine, tmp_path / "vcfs", chromosomes=("1",)
        )

        assert imputation_result.n_total == 1
        assert imputation_result.n_emitted == 0
        assert imputation_result.vcf_paths == {}

    def test_run_annotation_shared_rsid_off_coordinate_alleles_do_not_emit_imputation_site(
        self, tmp_path: Path
    ) -> None:
        sample_engine = _threadsafe_sqlite_engine()
        create_sample_tables(sample_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [{"rsid": "rs_shared", "chrom": "1", "pos": 12345, "genotype": "AG"}],
            )

        reference_engine = _create_minimal_reference_engine()
        vep_engine = _create_minimal_vep_engine(
            [
                {
                    "rsid": "rs_shared",
                    "chrom": "2",
                    "pos": 99999,
                    "ref": "A",
                    "alt": "G",
                    "gene_symbol": "GENE",
                    "transcript_id": "ENST00000000003",
                    "consequence": "intron_variant",
                    "hgvs_coding": None,
                    "hgvs_protein": None,
                    "strand": "+",
                    "exon_number": None,
                    "intron_number": 1,
                    "mane_select": 0,
                },
                {
                    "rsid": "rs_shared",
                    "chrom": "3",
                    "pos": 88888,
                    "ref": "A",
                    "alt": "G",
                    "gene_symbol": "GENE",
                    "transcript_id": "ENST00000000004",
                    "consequence": "missense_variant",
                    "hgvs_coding": None,
                    "hgvs_protein": None,
                    "strand": "+",
                    "exon_number": 1,
                    "intron_number": None,
                    "mane_select": 0,
                },
            ]
        )
        registry = _MinimalRegistry(reference_engine=reference_engine, vep_engine=vep_engine)

        result = run_annotation(sample_engine, registry, batch_size=1)

        assert result.errors == []
        assert result.rows_written == 1
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs_shared")
            ).one()
        assert row.annotation_coverage == VEP_BIT
        assert row.ref is None
        assert row.alt is None
        assert row.zygosity is None

        imputation_result = write_imputation_input_vcfs(
            sample_engine, tmp_path / "vcfs", chromosomes=("1",)
        )

        assert imputation_result.n_total == 1
        assert imputation_result.n_emitted == 0
        assert imputation_result.vcf_paths == {}

    def test_clinvar_alleles_remain_preferred_over_vep_fallback(self) -> None:
        raw = SimpleNamespace(rsid="rs_vep_only", chrom="1", pos=12345, genotype="AG")
        merged = _merge_annotations(
            [raw],
            vep_data={
                "rs_vep_only": {
                    "gene_symbol": "GENE",
                    "consequence": "intron_variant",
                    "_vep_ref": "T",
                    "_vep_alt": "C",
                }
            },
            clinvar_data={
                "rs_vep_only": {
                    "clinvar_significance": "Pathogenic",
                    "ref": "A",
                    "alt": "G",
                }
            },
            gnomad_data={},
            dbnsfp_data={},
        )

        assert merged[0]["annotation_coverage"] == VEP_BIT | CLINVAR_BIT
        assert merged[0]["ref"] == "A"
        assert merged[0]["alt"] == "G"
        assert merged[0]["zygosity"] == "het"

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

    def test_x_region_units_and_xy_nonpar_haploid_encoding(self) -> None:
        rows = [
            ("rs_np1", "X", 60_000, "A", "G", "hom_alt"),  # non-PAR before PAR1
            ("rs_par1", "X", 60_001, "C", "T", "het"),  # PAR1 start is diploid
            ("rs_np2_het", "X", 2_699_521, "G", "A", "het"),  # XY non-PAR het -> drop
            ("rs_np2_alt", "X", 2_699_522, "G", "A", "hom_alt"),  # haploid alt
            ("rs_par2", "X", 154_931_044, "T", "C", "het"),  # PAR2 start is diploid
            ("rs_np3", "X", 155_260_561, "G", "A", "hom_ref"),  # final non-PAR
        ]

        by_unit, n_total, n_emitted = collect_input_sites(
            rows, chromosomes=("X",), biological_sex="XY"
        )

        assert n_total == 6
        assert n_emitted == 5
        assert by_unit["X_NONPAR1"][0].gt == "1"
        assert by_unit["X_PAR1"][0].gt == "0/1"
        assert [s.rsid for s in by_unit["X_NONPAR2"]] == ["rs_np2_alt"]
        assert by_unit["X_NONPAR2"][0].gt == "1"
        assert by_unit["X_PAR2"][0].gt == "0/1"
        assert by_unit["X_NONPAR3"][0].gt == "0"

    def test_xx_nonpar_heterozygote_remains_diploid(self) -> None:
        rows = [("rs_np2", "X", 2_699_521, "G", "A", "het")]
        by_unit, _n_total, n_emitted = collect_input_sites(
            rows, chromosomes=("X",), biological_sex="XX"
        )
        assert n_emitted == 1
        assert by_unit["X_NONPAR2"][0].gt == "0/1"

    @pytest.mark.parametrize("sex", [None, "unknown", "manual_review"])
    def test_x_requires_resolved_biological_sex(self, sex) -> None:
        rows = [("rs_x", "X", 2_699_521, "G", "A", "hom_alt")]
        with pytest.raises(ValueError, match="biological_sex"):
            collect_input_sites(rows, chromosomes=("X",), biological_sex=sex)


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

    def test_end_to_end_writes_x_region_vcfs(
        self, sample_engine: sa.Engine, tmp_path: Path
    ) -> None:
        _insert(
            sample_engine,
            [
                _row("rs_par", "X", 60_001, "A", "G", "het"),
                _row("rs_np", "X", 2_699_521, "C", "T", "hom_alt"),
                _row("rs_noise", "X", 2_699_522, "G", "A", "het"),  # XY non-PAR drop
            ],
        )

        result = write_imputation_input_vcfs(
            sample_engine,
            tmp_path / "vcfs",
            chromosomes=("X",),
            biological_sex="XY",
            sample_name="S1",
        )

        assert result.n_total == 3
        assert result.n_emitted == 2
        assert result.n_dropped == 1
        assert result.per_chrom_emitted == {"X_PAR1": 1, "X_NONPAR2": 1}
        assert [unit.key for unit in result.units] == ["X_PAR1", "X_NONPAR2"]
        assert {unit.key: unit.beagle_region for unit in result.units} == {
            "X_PAR1": "X:60001-2699520",
            "X_NONPAR2": "X:2699521-154931043",
        }

        par_vcf = tmp_path / "vcfs" / "chrX_PAR1.vcf.gz"
        nonpar_vcf = tmp_path / "vcfs" / "chrX_NONPAR2.vcf.gz"
        assert par_vcf.exists()
        assert nonpar_vcf.exists()
        assert (tmp_path / "vcfs" / "chrX.vcf.gz").exists() is False

        with pysam.VariantFile(str(par_vcf)) as vf:
            [par_rec] = list(vf)
        assert (par_rec.chrom, par_rec.pos, par_rec.id) == ("X", 60_001, "rs_par")
        assert par_rec.samples["S1"]["GT"] == (0, 1)

        with pysam.VariantFile(str(nonpar_vcf)) as vf:
            [nonpar_rec] = list(vf)
        assert (nonpar_rec.chrom, nonpar_rec.pos, nonpar_rec.id) == ("X", 2_699_521, "rs_np")
        assert nonpar_rec.samples["S1"]["GT"] == (1,)

    def test_x_writer_requires_resolved_biological_sex(
        self, sample_engine: sa.Engine, tmp_path: Path
    ) -> None:
        _insert(sample_engine, [_row("rs_x", "X", 2_699_521, "C", "T", "hom_alt")])
        with pytest.raises(ValueError, match="biological_sex"):
            write_imputation_input_vcfs(sample_engine, tmp_path / "vcfs", chromosomes=("X",))

    def test_supported_scope_includes_x_but_default_scope_is_autosomes(self) -> None:
        assert AUTOSOMAL_INPUT_CHROMOSOMES == tuple(str(i) for i in range(1, 23))
        assert DEFAULT_INPUT_CHROMOSOMES == AUTOSOMAL_INPUT_CHROMOSOMES
        assert INPUT_CHROMOSOMES == (*tuple(str(i) for i in range(1, 23)), "X")
        assert [s.key for s in input_unit_specs_for_chromosomes(("X",))] == [
            "X_NONPAR1",
            "X_PAR1",
            "X_NONPAR2",
            "X_PAR2",
            "X_NONPAR3",
        ]

    def test_x_unit_beagle_regions(self) -> None:
        """Pin every chrX Beagle region (#1289/#1352).

        Finite intervals must carry explicit one-based starts and ends so they
        do not bleed into adjacent PAR/non-PAR units. The terminal X_NONPAR3
        interval intentionally uses Beagle 5.5's documented ``chrom=X:start-``
        form to run through the chromosome end.
        """
        regions = {s.key: s.beagle_region for s in input_unit_specs_for_chromosomes(("X",))}
        assert regions == {
            "X_NONPAR1": "X:1-60000",
            "X_PAR1": "X:60001-2699520",
            "X_NONPAR2": "X:2699521-154931043",
            "X_PAR2": "X:154931044-155260560",
            "X_NONPAR3": "X:155260561-",
        }
