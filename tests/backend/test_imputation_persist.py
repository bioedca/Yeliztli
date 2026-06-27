"""Persist firewall-cleared imputed variants + the impute→persist orchestrator (Wave C).

Pins that only imputed variants clearing the SW-C3 firewall reach the
``imputed_variants`` table (genotyped + quarantined dropped), that persistence is
replace-semantics and multi-allelic-safe, and that the end-to-end
``impute_and_persist_sample`` driver (Beagle mocked) prepares input, imputes,
firewalls, and persists in one pass.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

import backend.analysis.imputation_runner as ir_mod
from backend.analysis.imputation_persist import (
    impute_and_persist_sample,
    persist_imputed_variants,
)
from backend.analysis.imputation_runner import ImputationRunner, ImputedVariant
from backend.db.tables import annotated_variants, imputed_variants


@pytest.fixture
def sample_engine() -> sa.Engine:
    from backend.db.sample_schema import create_sample_tables

    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    return engine


def _iv(
    *, pos: int, dr2: float | None, af: float | None, imputed: bool, ref: str = "A", alt: str = "G"
) -> ImputedVariant:
    return ImputedVariant(chrom="22", pos=pos, ref=ref, alt=alt, dr2=dr2, af=af, imputed=imputed)


def _read_rows(engine: sa.Engine) -> list[dict]:
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sa.select(imputed_variants))]


class TestPersist:
    def test_only_firewall_cleared_imputed_rows_written(self, sample_engine: sa.Engine) -> None:
        variants = [
            _iv(pos=100, dr2=0.95, af=0.30, imputed=True),  # reportable
            _iv(pos=200, dr2=0.50, af=0.30, imputed=True),  # low_dr2 → drop
            _iv(pos=300, dr2=0.99, af=0.002, imputed=True),  # imputed_rare → drop
            _iv(pos=400, dr2=0.99, af=0.30, imputed=False),  # genotyped → drop
            _iv(pos=500, dr2=0.88, af=0.97, imputed=True),  # reportable (MAF folds to 0.03)
        ]
        n = persist_imputed_variants(sample_engine, variants)
        assert n == 2
        rows = _read_rows(sample_engine)
        assert {(r["pos"], r["dr2"], r["af"]) for r in rows} == {
            (100, 0.95, 0.30),
            (500, 0.88, 0.97),
        }

    def test_replace_semantics(self, sample_engine: sa.Engine) -> None:
        persist_imputed_variants(sample_engine, [_iv(pos=100, dr2=0.95, af=0.30, imputed=True)])
        # A second run with different variants replaces, not accumulates.
        persist_imputed_variants(sample_engine, [_iv(pos=999, dr2=0.90, af=0.40, imputed=True)])
        rows = _read_rows(sample_engine)
        assert [r["pos"] for r in rows] == [999]

    def test_multiallelic_distinct_alts_kept(self, sample_engine: sa.Engine) -> None:
        variants = [
            _iv(pos=100, dr2=0.95, af=0.30, imputed=True, ref="C", alt="A"),
            _iv(pos=100, dr2=0.90, af=0.20, imputed=True, ref="C", alt="T"),
        ]
        n = persist_imputed_variants(sample_engine, variants)
        assert n == 2
        rows = _read_rows(sample_engine)
        assert {(r["pos"], r["alt"]) for r in rows} == {(100, "A"), (100, "T")}

    def test_no_reportable_clears_table(self, sample_engine: sa.Engine) -> None:
        persist_imputed_variants(sample_engine, [_iv(pos=100, dr2=0.95, af=0.30, imputed=True)])
        n = persist_imputed_variants(
            sample_engine, [_iv(pos=200, dr2=0.10, af=0.30, imputed=True)]
        )
        assert n == 0
        assert _read_rows(sample_engine) == []


# Real Beagle 5.5 output shape (mix of reportable + quarantined imputed + a typed marker).
_FAKE_IMPUTED_VCF = (
    "##fileformat=VCFv4.2\n"
    '##INFO=<ID=AF,Number=A,Type=Float,Description="Estimated ALT Allele Frequencies">\n'
    '##INFO=<ID=DR2,Number=A,Type=Float,Description="Dosage R-Squared">\n'
    '##INFO=<ID=IMP,Number=0,Type=Flag,Description="Imputed marker">\n'
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    '##FORMAT=<ID=DS,Number=A,Type=Float,Description="estimated ALT dose">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
    "22\t100\trs1\tG\tA\t.\tPASS\tDR2=0.95;AF=0.30;IMP\tGT:DS\t0|1:1\n"  # reportable
    "22\t200\trs2\tC\tT\t.\tPASS\tDR2=0.50;AF=0.20;IMP\tGT:DS\t0|0:0\n"  # low_dr2
    "22\t300\trs3\tA\tG\t.\tPASS\tDR2=0.99;AF=0.002;IMP\tGT:DS\t0|0:0\n"  # imputed_rare
    "22\t400\trs4\tT\tC\t.\tPASS\tDR2=1.00;AF=0.40\tGT:DS\t0|1:1\n"  # typed (no IMP)
)


def _stub_panel_and_jar(tmp_path: Path, *, chroms: tuple[str, ...] = ("22",)) -> tuple[Path, Path]:
    jar = tmp_path / "lai" / "beagle" / "beagle.jar"
    jar.parent.mkdir(parents=True)
    jar.touch()
    panel = tmp_path / "panel"
    panel.mkdir()
    for c in chroms:
        (panel / f"chr{c}.1kg.phase3.v5a.b37.bref3").touch()
        (panel / f"plink.chr{c}.GRCh37.map").touch()
    return panel, jar


class TestImputeAndPersistSample:
    def test_end_to_end_mocked_beagle(
        self, sample_engine: sa.Engine, tmp_path: Path, monkeypatch
    ) -> None:
        # One reference-aligned chr22 SNP so input-prep produces chr22.vcf.gz.
        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert(),
                [
                    {
                        "rsid": "rsIn",
                        "chrom": "22",
                        "pos": 500,
                        "ref": "C",
                        "alt": "T",
                        "genotype": "CT",
                        "zygosity": "het",
                    }
                ],
            )

        def fake_run(cmd, **_kw):  # noqa: ANN001, ANN202
            out_prefix = next(a.split("=", 1)[1] for a in cmd if a.startswith("out="))
            with gzip.open(Path(out_prefix + ".vcf.gz"), "wt", encoding="utf-8") as fh:
                fh.write(_FAKE_IMPUTED_VCF)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(ir_mod.subprocess, "run", fake_run)
        panel, jar = _stub_panel_and_jar(tmp_path)

        # Patch ImputationRunner's panel/jar via the explicit args.
        result = impute_and_persist_sample(
            sample_engine,
            tmp_path / "work",
            panel_dir=panel,
            beagle_jar=jar,
            chromosomes=("22",),
        )

        assert result.n_input_sites == 1
        assert result.n_imputed == 3  # rs1, rs2, rs3 (rs4 is typed)
        assert result.n_persisted == 1  # only rs1 clears the firewall
        assert result.firewall.n_reportable == 1
        assert result.firewall.quarantine_reasons == {"low_dr2": 1, "imputed_rare": 1}

        rows = _read_rows(sample_engine)
        assert len(rows) == 1
        assert (rows[0]["chrom"], rows[0]["pos"], rows[0]["alt"], rows[0]["dr2"]) == (
            "22",
            100,
            "A",
            0.95,
        )

    def test_partial_failure_skips_persist_and_preserves_prior(
        self, sample_engine: sa.Engine, tmp_path: Path, monkeypatch
    ) -> None:
        # Reference-aligned input on two chromosomes.
        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert(),
                [
                    {
                        "rsid": "rsA",
                        "chrom": "21",
                        "pos": 100,
                        "ref": "C",
                        "alt": "T",
                        "genotype": "CT",
                        "zygosity": "het",
                    },
                    {
                        "rsid": "rsB",
                        "chrom": "22",
                        "pos": 200,
                        "ref": "A",
                        "alt": "G",
                        "genotype": "AG",
                        "zygosity": "het",
                    },
                ],
            )
        # A complete prior snapshot that must survive a partial re-run.
        persist_imputed_variants(sample_engine, [_iv(pos=999, dr2=0.95, af=0.40, imputed=True)])
        assert [r["pos"] for r in _read_rows(sample_engine)] == [999]

        def fake_run(cmd, **_kw):  # noqa: ANN001, ANN202
            out_prefix = next(a.split("=", 1)[1] for a in cmd if a.startswith("out="))
            if out_prefix.endswith("imputed_chr21"):
                return SimpleNamespace(returncode=1, stdout="", stderr="boom")  # chr21 fails
            with gzip.open(Path(out_prefix + ".vcf.gz"), "wt", encoding="utf-8") as fh:
                fh.write(_FAKE_IMPUTED_VCF)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(ir_mod.subprocess, "run", fake_run)
        panel, jar = _stub_panel_and_jar(tmp_path, chroms=("21", "22"))

        result = impute_and_persist_sample(
            sample_engine,
            tmp_path / "work",
            panel_dir=panel,
            beagle_jar=jar,
            chromosomes=("21", "22"),
        )

        assert any(not r.return_ok for r in result.chrom_results)  # chr21 failed
        assert result.n_persisted == 0  # persistence skipped on partial failure
        # The prior snapshot is preserved (not overwritten by chr22-only data).
        assert [r["pos"] for r in _read_rows(sample_engine)] == [999]

    def test_missing_jar_raises(self, tmp_path: Path) -> None:
        panel = tmp_path / "panel"
        panel.mkdir()
        with pytest.raises(FileNotFoundError, match="Beagle JAR"):
            ImputationRunner(panel, tmp_path / "nope.jar")
