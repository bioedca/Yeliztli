"""Sanity tests for scripts/lai_bundle_v2/ — Step 20 deliverable.

The actual cluster rebuild is out-of-repo (Plan §6.2, §12.2 PR-0c). This
test module verifies that the in-repo scripts package ships with:

  1. The expected phase scripts present and executable.
  2. The orchestrator `run_rebuild.sh` references every phase in the
     documented order.
  3. No script hardcodes the v1.1 working directory — every path is
     either an env-var-overridable default or sourced from `env.sh`.
  4. Phase scripts source the shared `env.sh` (so overrides flow through).
  5. The Python helper scripts compile cleanly under the project Python.

The runbook is also verified for the rsync flow that ports the scripts onto
the cluster (Plan §6.3 step 1, runbook §4).
"""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import os
import py_compile
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


def _load_module(filename: str, mod_name: str):
    """Import a digit-prefixed helper (e.g. 06e_lai_accuracy.py) by path."""
    spec = importlib.util.spec_from_file_location(mod_name, SCRIPTS_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts" / "lai_bundle_v2"
RUNBOOK = REPO_ROOT / "docs" / "lai-bundle-release-runbook.md"
MAINTAINER_DOC = REPO_ROOT / "docs" / "maintainer" / "lai-bundle.md"


def _gnomix_config(*, seed: int | str = 1) -> str:
    """Return a minimal config satisfying the three-split release contract."""
    return (
        f"verbose: true\nseed: {seed}\n"
        "simulation:\n"
        "  run: true\n"
        "  splits:\n"
        "    ratios:\n"
        "      train1: 0.8\n"
        "      train2: 0.15\n"
        "      val: 0.05\n"
        "model:\n"
        "  name: model\n"
    )


def _gnomix_superpopulation(sample_number: int) -> str:
    superpopulations = ("AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE")
    return superpopulations[min((sample_number - 1) // 11, len(superpopulations) - 1)]


def _gnomix_split_map(sample_numbers: list[int]) -> str:
    return "".join(f"S{number}\t{_gnomix_superpopulation(number)}\n" for number in sample_numbers)


_GNOMIX_TRAIN1_SAMPLES = [
    group_start + offset for group_start in range(1, 78, 11) for offset in range(8)
]
_GNOMIX_TRAIN2_SAMPLES = [
    group_start + offset for group_start in range(1, 78, 11) for offset in (0, 8, 9)
]
_GNOMIX_VAL_SAMPLES = [group_start + 10 for group_start in range(1, 78, 11)]
_GNOMIX_TRAIN1_MAP = _gnomix_split_map(_GNOMIX_TRAIN1_SAMPLES)
_GNOMIX_TRAIN2_MAP = _gnomix_split_map(_GNOMIX_TRAIN2_SAMPLES)
_GNOMIX_VAL_MAP = _gnomix_split_map(_GNOMIX_VAL_SAMPLES)


def _write_gnomix_training_contract(workdir: Path) -> dict[str, Path]:
    """Write one tiny, production-shaped immutable training generation."""
    manifests = _load_module(
        "gnomix_training_manifests.py",
        f"gnomix_training_manifest_fixture_{hash(workdir)}",
    )
    raw_dir = workdir / "00_raw_downloads"
    liftover_dir = workdir / "02_liftover"
    panel_dir = workdir / "03_subsetted_panels"
    admix_dir = workdir / "04_admixture_filtering"
    gnomix_dir = workdir / "05_gnomix_training"
    validation_dir = workdir / "06_validation"
    for directory in (
        raw_dir,
        liftover_dir,
        panel_dir,
        admix_dir,
        gnomix_dir,
        validation_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    metadata = raw_dir / "gnomad_meta_updated.tsv"
    metadata.write_text(
        "s\thgdp_tgp_meta.Population\thgdp_tgp_meta.Genetic.region\n"
        + "".join(
            f"S{number}\tPop{number}\t{_gnomix_superpopulation(number)}\n"
            for number in range(1, 79)
        )
    )
    selected = admix_dir / "single_ancestry_samples.tsv"
    selected.write_text(
        "IID\tpopulation\tgenetic_region\n"
        + "".join(
            f"S{number}\tPop{number}\t{_gnomix_superpopulation(number)}\n"
            for number in range(1, 79)
        )
    )
    full_map = admix_dir / "sample_map.full.txt"
    full_map.write_text(_gnomix_split_map(list(range(1, 79))))
    training_map = admix_dir / "sample_map.txt"
    training_map.write_text(_gnomix_split_map(list(range(1, 78))))
    heldout_map = validation_dir / "held_out_validation.tsv"
    heldout_map.write_text("IID\tgenetic_region\nS78\tOCE\n")
    union_catalog = workdir / "union_sites.tsv"
    union_catalog.write_text(
        "".join(
            f"rs{number}a\t{number}\t100\nrs{number}b\t{number}\t200\n" for number in range(1, 23)
        )
    )
    lifted_regions = liftover_dir / "array_sites_grch38_regions.tsv"
    lifted_regions.write_text(
        "".join(f"chr{number}\t100\t100\nchr{number}\t200\t200\n" for number in range(1, 23))
    )

    chromosome_files: dict[str, tuple[Path, Path]] = {}
    for number in range(1, 23):
        chromosome = f"chr{number}"
        vcf = panel_dir / f"ref_panel_{chromosome}.vcf.gz"
        index = panel_dir / f"ref_panel_{chromosome}.vcf.gz.tbi"
        header = "\t".join(
            (
                "#CHROM",
                "POS",
                "ID",
                "REF",
                "ALT",
                "QUAL",
                "FILTER",
                "INFO",
                "FORMAT",
                *(f"S{sample}" for sample in range(1, 79)),
            )
        )
        genotype_cycle = ("0|0", "0|1", "1|0", "1|1")
        genotype_row = "\t".join(genotype_cycle[(sample - 1) % 4] for sample in range(1, 79))
        body = (
            "##fileformat=VCFv4.2\n"
            f"##contig=<ID={chromosome}>\n"
            f"{header}\n"
            f"{chromosome}\t100\trs{number}a\tA\tG\t.\tPASS\t.\tGT\t{genotype_row}\n"
            f"{chromosome}\t200\trs{number}b\tC\tT\t.\tPASS\t.\tGT\t{genotype_row}\n"
        )
        vcf.write_bytes(gzip.compress(body.encode("ascii"), mtime=0))
        index.write_bytes(f"index-{chromosome}\n".encode())
        chromosome_files[chromosome] = (vcf, index)

    input_manifest = admix_dir / "gnomix_training_inputs.json"
    manifests.write_input_manifest(
        input_manifest,
        reference_build="GRCh38",
        reference_panel_name="gnomAD HGDP+1KG v3.1.2 (phased SHAPEIT5)",
        reference_panel_source=(
            "gs://gcp-public-data--gnomad/resources/hgdp_1kg/phased_haplotypes_v2"
        ),
        metadata_path=metadata,
        metadata_source=(
            "gs://gcp-public-data--gnomad/release/3.1/secondary_analyses/"
            "hgdp_1kg_v2/metadata_and_qc/gnomad_meta_updated.tsv"
        ),
        selected_samples_path=selected,
        full_sample_map_path=full_map,
        training_sample_map_path=training_map,
        heldout_sample_map_path=heldout_map,
        marker_sources={"lifted_regions": lifted_regions, "union_catalog": union_catalog},
        chromosome_files=chromosome_files,
    )

    split_dir = gnomix_dir / "output_chr1" / "generated_data" / "sample_maps"
    split_dir.mkdir(parents=True)
    train1 = split_dir / "train1.map"
    train2 = split_dir / "train2.map"
    val = split_dir / "val.map"
    train1.write_text(_GNOMIX_TRAIN1_MAP)
    train2.write_text(_GNOMIX_TRAIN2_MAP)
    val.write_text(_GNOMIX_VAL_MAP)
    split_manifest = (
        gnomix_dir / "output_chr1" / "models" / "model_chm_chr1" / "training_splits.json"
    )
    split_manifest.parent.mkdir(parents=True)
    manifests.write_split_manifest(
        split_manifest,
        input_manifest_path=input_manifest,
        split_files={"train1": train1, "train2": train2, "val": val},
    )
    return {
        "input_manifest": input_manifest,
        "split_manifest": split_manifest,
        "union_catalog": union_catalog,
        "train1": train1,
        "train2": train2,
        "val": val,
    }


EXPECTED_PHASE_SCRIPTS = [
    "01_download_panel.sh",
    "02_prepare_sites.sh",
    "03_subset_panel.sh",
    "04_admixture_filter.sh",
    "05_train_gnomix.sh",
    "06_validate.sh",
    "07_assemble_bundle.sh",
]

EXPECTED_HELPERS = [
    "env.sh",
    "run_rebuild.sh",
    "01_convert_gnomix_maps.py",
    "04c_filter_single_ancestry.py",
    "06a_identify_trios.py",
    "06b_mendelian_phasing.py",
    "06c_beagle_loo_phasing.sh",
    "06c_beagle_one.sh",
    "06d_phasing_accuracy.py",
    "06e_lai_accuracy.py",
    "06f_select_heldout.py",
    "06f_heldout_superpop_accuracy.py",
    "extract_heldout_fixtures.py",
    "07_write_metadata.py",
    "gnomix_launcher.py",
    "gnomix_provenance.py",
    "gnomix_training_manifests.py",
    "07b_reexport_gnomix_models.py",
]


# Hardcoded private shared-filesystem roots that scripts MUST NOT bake in. The
# dispatcher should accept concrete build paths via env override only.
_PRIVATE_SHARED_ROOT = re.compile(r"/exports(?:/|$)")
_HOME_LAI_BUNDLE_V1_HARDCODED = re.compile(r"\$HOME/lai_bundle(?!_v2)\b|~/lai_bundle(?!_v2)\b")


class TestScriptsPresent:
    @pytest.mark.parametrize("name", EXPECTED_PHASE_SCRIPTS + EXPECTED_HELPERS)
    def test_script_exists(self, name: str) -> None:
        path = SCRIPTS_DIR / name
        assert path.is_file(), f"{path} missing"

    @pytest.mark.parametrize("name", EXPECTED_PHASE_SCRIPTS + EXPECTED_HELPERS)
    def test_script_executable(self, name: str) -> None:
        path = SCRIPTS_DIR / name
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR, f"{path} is not user-executable"


class TestOrchestratorPhaseOrder:
    def test_run_rebuild_lists_every_phase_in_order(self) -> None:
        text = (SCRIPTS_DIR / "run_rebuild.sh").read_text()
        # ALL_PHASES=(01 02 03 04 05 06 07)
        m = re.search(r"ALL_PHASES=\(([^)]+)\)", text)
        assert m, "run_rebuild.sh must declare ALL_PHASES=(...)"
        phases = m.group(1).split()
        assert phases == ["01", "02", "03", "04", "05", "06", "07"]

    def test_phase_dispatch_maps_each_phase_to_its_script(self) -> None:
        text = (SCRIPTS_DIR / "run_rebuild.sh").read_text()
        for phase_script in EXPECTED_PHASE_SCRIPTS:
            phase_num = phase_script.split("_", 1)[0]
            # PHASE_SCRIPT[NN]="NN_..."
            pat = rf"\[{re.escape(phase_num)}\]=\"{re.escape(phase_script)}\""
            assert re.search(pat, text), f"run_rebuild.sh missing dispatch for phase {phase_num}"

    def test_orchestrator_sources_env_sh(self) -> None:
        text = (SCRIPTS_DIR / "run_rebuild.sh").read_text()
        assert 'source "$SCRIPT_DIR/env.sh"' in text


class TestEveryPhaseSourcesEnv:
    @pytest.mark.parametrize(
        "name", EXPECTED_PHASE_SCRIPTS + ["06c_beagle_loo_phasing.sh", "06c_beagle_one.sh"]
    )
    def test_phase_script_sources_env(self, name: str) -> None:
        text = (SCRIPTS_DIR / name).read_text()
        assert 'source "$SCRIPT_DIR/env.sh"' in text, f"{name} must source env.sh"


class TestNoV11PathLeak:
    """Plan §6.2 mandates the v1.1 working dir is read-only reference. Scripts
    must default to v2.0.0 paths and accept the v1.1 path only via env-var
    override (`WORKDIR=...`), never as a hardcoded constant.
    """

    @pytest.mark.parametrize(
        "name",
        EXPECTED_PHASE_SCRIPTS + EXPECTED_HELPERS,
    )
    def test_no_hardcoded_v1_cluster_path(self, name: str) -> None:
        text = (SCRIPTS_DIR / name).read_text()
        assert not _PRIVATE_SHARED_ROOT.search(text), (
            f"{name} hardcodes a private shared-filesystem path; parametrize via env.sh instead"
        )

    @pytest.mark.parametrize(
        "name",
        EXPECTED_PHASE_SCRIPTS + EXPECTED_HELPERS,
    )
    def test_no_hardcoded_home_lai_bundle_v1(self, name: str) -> None:
        text = (SCRIPTS_DIR / name).read_text()
        # env.sh ships the default `$HOME/lai_bundle_v2` as the WORKDIR
        # default; no other script may bake in a `~/lai_bundle` (v1) path.
        if name == "env.sh":
            return
        assert not _HOME_LAI_BUNDLE_V1_HARDCODED.search(text), (
            f"{name} hardcodes ~/lai_bundle (v1.1); use $WORKDIR (sourced from env.sh)"
        )


class TestEnvShDefaults:
    """`env.sh` is the single source of truth for parametrization."""

    def test_default_workdir_is_v2(self) -> None:
        text = (SCRIPTS_DIR / "env.sh").read_text()
        assert "WORKDIR:=$HOME/lai_bundle_v2" in text

    def test_default_bundle_version_is_v2(self) -> None:
        text = (SCRIPTS_DIR / "env.sh").read_text()
        assert "LAI_BUNDLE_VERSION:=v2.0.0" in text

    def test_union_catalog_required_input(self) -> None:
        # UNION_CATALOG_TSV must default to empty and be checked by
        # 02_prepare_sites.sh via require_file (Plan §6.4 phase 2).
        env_text = (SCRIPTS_DIR / "env.sh").read_text()
        phase2_text = (SCRIPTS_DIR / "02_prepare_sites.sh").read_text()
        assert "UNION_CATALOG_TSV:=" in env_text
        assert 'require_file "$UNION_CATALOG_TSV"' in phase2_text

    def test_admixture_seed_is_locked(self) -> None:
        # Plan §6.3 step 4: re-running with the same seed reproduces labels
        # bit-for-bit. The seed default is part of the build contract.
        text = (SCRIPTS_DIR / "env.sh").read_text()
        assert "ADMIXTURE_SEED:=42" in text


class TestShellSyntax:
    """Catch shell parse errors before they hit the cluster."""

    @pytest.mark.parametrize(
        "name",
        ["env.sh", "run_rebuild.sh"]
        + EXPECTED_PHASE_SCRIPTS
        + ["06c_beagle_loo_phasing.sh", "06c_beagle_one.sh"],
    )
    def test_bash_n_passes(self, name: str) -> None:
        path = SCRIPTS_DIR / name
        result = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{name} has shell-syntax errors:\n{result.stderr}"


class TestPythonHelpersCompile:
    @pytest.mark.parametrize(
        "name",
        [
            "01_convert_gnomix_maps.py",
            "04c_filter_single_ancestry.py",
            "06a_identify_trios.py",
            "06b_mendelian_phasing.py",
            "06d_phasing_accuracy.py",
            "06e_lai_accuracy.py",
            "06f_select_heldout.py",
            "06f_heldout_superpop_accuracy.py",
            "extract_heldout_fixtures.py",
            "07_write_metadata.py",
            "gnomix_launcher.py",
            "gnomix_provenance.py",
            "07b_reexport_gnomix_models.py",
        ],
    )
    def test_py_compile(self, name: str) -> None:
        py_compile.compile(str(SCRIPTS_DIR / name), doraise=True)


class TestPhase01GnomixMaps:
    @staticmethod
    def _write_source(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    @staticmethod
    def _init_git_checkout(path: Path) -> str:
        git_env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
        subprocess.run(["git", "init", "-q", str(path)], check=True, env=git_env)
        subprocess.run(["git", "-C", str(path), "add", "."], check=True, env=git_env)
        subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "-c",
                "user.name=Yeliztli Tests",
                "-c",
                "user.email=tests@yeliztli.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-q",
                "-m",
                "fixture",
            ],
            check=True,
            env=git_env,
        )
        commit = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            env=git_env,
        ).stdout.strip()
        subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "remote",
                "add",
                "origin",
                "https://github.com/AI-sandbox/gnomix.git",
            ],
            check=True,
            env=git_env,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "update-ref",
                "refs/remotes/origin/main",
                commit,
            ],
            check=True,
            env=git_env,
        )
        return commit

    @staticmethod
    def _run_converter(
        source_dir: Path,
        output_dir: Path,
        *chromosomes: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "01_convert_gnomix_maps.py"),
                "--source-dir",
                str(source_dir),
                "--output-dir",
                str(output_dir),
                "--chromosomes",
                *chromosomes,
                "--source-url",
                "https://example.test/plink.GRCh38.map.zip",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def _run_verifier(
        output_dir: Path,
        *chromosomes: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "01_convert_gnomix_maps.py"),
                "--verify",
                "--output-dir",
                str(output_dir),
                "--chromosomes",
                *chromosomes,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_phase01_derives_exact_gnomix_map_and_provenance(self, tmp_path: Path) -> None:
        workdir = tmp_path / "work"
        raw_dir = workdir / "00_raw_downloads"
        source = (
            raw_dir / "genetic_maps_grch38" / "chr_in_chrom_field" / "plink.chrchr1.GRCh38.map"
        )
        self._write_source(source, "chr1 . 0 55550\nchr1 . 0.080572 82571\n")
        (raw_dir / "hgdp1kgp_chr1.filtered.SNV_INDEL.phased.shapeit5.bcf").write_bytes(b"bcf")
        (raw_dir / "hgdp1kgp_chr1.filtered.SNV_INDEL.phased.shapeit5.bcf.csi").write_bytes(b"csi")
        (raw_dir / "gnomad_meta_updated.tsv").write_text("sample\n")

        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        for command in ("gsutil", "wget", "unzip"):
            stub = stub_dir / command
            stub.write_text("#!/bin/sh\necho unexpected download command >&2\nexit 97\n")
            stub.chmod(0o755)

        env = os.environ.copy()
        for variable in (
            "RAW_DIR",
            "LOG_DIR",
            "SITES_DIR",
            "LIFTOVER_DIR",
            "PANEL_DIR",
            "ADMIX_DIR",
            "GNOMIX_DIR",
            "VALIDATION_DIR",
            "BUNDLE_DIR",
        ):
            env.pop(variable, None)
        env.update(
            {
                "WORKDIR": str(workdir),
                "CHROMS": "1",
                "GENETIC_MAPS_URL": "https://example.test/plink.GRCh38.map.zip",
                "PATH": f"{stub_dir}{os.pathsep}{env['PATH']}",
            }
        )

        first = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "01_download_panel.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert first.returncode == 0, first.stderr

        output = raw_dir / "genetic_maps_gnomix" / "chr1.map"
        provenance = raw_dir / "genetic_maps_gnomix" / "provenance.json"
        assert output.read_bytes() == b"chr1\t55550\t0\nchr1\t82571\t0.080572\n"
        manifest = json.loads(provenance.read_text())
        assert manifest["transformation"] == "PLINK columns 1,4,3 with chrN labels retained"
        assert manifest["source"]["url"] == env["GENETIC_MAPS_URL"]
        assert manifest["maps"] == [
            {
                "chromosome": "chr1",
                "derived_file": "chr1.map",
                "derived_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "row_count": 2,
                "source_file": "chr_in_chrom_field/plink.chrchr1.GRCh38.map",
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        ]
        verification = self._run_verifier(output.parent, "1")
        assert verification.returncode == 0, verification.stderr

        original_output = output.read_bytes()
        original_provenance = provenance.read_bytes()
        second = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "01_download_panel.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert second.returncode == 0, second.stderr
        assert output.read_bytes() == original_output
        assert provenance.read_bytes() == original_provenance

    @pytest.mark.parametrize(
        "source_text,error_text",
        [
            ("chr1 . 0\n", "expected 4 PLINK columns"),
            ("1 . 0 55550\n", "expected chr1"),
            ("chr2 . 0 55550\n", "expected chr1"),
            ("chr1 . 0 55550\nchr1 . 0.1 55550\n", "strictly increasing"),
            ("chr1 . 1.0 55550\nchr1 . 0.9 82571\n", "non-decreasing"),
        ],
    )
    def test_converter_rejects_invalid_source_without_publishing_output(
        self,
        tmp_path: Path,
        source_text: str,
        error_text: str,
    ) -> None:
        source_dir = tmp_path / "source"
        self._write_source(source_dir / "plink.chrchr1.GRCh38.map", source_text)
        output_dir = tmp_path / "output"

        result = self._run_converter(source_dir, output_dir, "1")

        assert result.returncode == 1
        assert error_text in result.stderr
        assert not (output_dir / "chr1.map").exists()
        assert not (output_dir / "provenance.json").exists()

    def test_all_maps_validate_before_any_are_published(self, tmp_path: Path) -> None:
        source_dir = tmp_path / "source"
        self._write_source(source_dir / "plink.chrchr1.GRCh38.map", "chr1 . 0 55550\n")
        self._write_source(source_dir / "plink.chrchr2.GRCh38.map", "chr3 . 0 10001\n")
        output_dir = tmp_path / "output"

        result = self._run_converter(source_dir, output_dir, "1", "2")

        assert result.returncode == 1
        assert not (output_dir / "chr1.map").exists()
        assert not (output_dir / "chr2.map").exists()
        assert not (output_dir / "provenance.json").exists()

    def test_verifier_rejects_map_changed_after_publication(self, tmp_path: Path) -> None:
        source_dir = tmp_path / "source"
        self._write_source(source_dir / "plink.chrchr1.GRCh38.map", "chr1 . 0 55550\n")
        output_dir = tmp_path / "output"
        conversion = self._run_converter(source_dir, output_dir, "1")
        assert conversion.returncode == 0, conversion.stderr

        with (output_dir / "chr1.map").open("a") as handle:
            handle.write("chr1\t82571\t0.080572\n")

        verification = self._run_verifier(output_dir, "1")
        assert verification.returncode == 1
        assert "checksum does not match provenance marker" in verification.stderr

    def test_interrupted_publication_invalidates_prior_manifest(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        converter = _load_module("01_convert_gnomix_maps.py", "gnomix_map_converter")
        source_dir = tmp_path / "source"
        self._write_source(source_dir / "plink.chrchr1.GRCh38.map", "chr1 . 0 55550\n")
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        old_map = b"chr1\t100\t0\n"
        (output_dir / "chr1.map").write_bytes(old_map)
        (output_dir / "provenance.json").write_text('{"old_generation": true}\n')

        original_replace = Path.replace

        def interrupt_map_replace(path: Path, target: Path) -> Path:
            if path.name == "chr1.map" and path.parent.name.startswith(".gnomix-maps."):
                raise OSError("simulated publication interruption")
            return original_replace(path, target)

        monkeypatch.setattr(Path, "replace", interrupt_map_replace)

        with pytest.raises(OSError, match="simulated publication interruption"):
            converter.derive_maps(
                source_dir,
                output_dir,
                ["1"],
                "https://example.test/plink.GRCh38.map.zip",
            )

        assert (output_dir / "chr1.map").read_bytes() == old_map
        assert not (output_dir / "provenance.json").exists()

    def test_phase05_verifies_provenance_before_training(self) -> None:
        text = (SCRIPTS_DIR / "05_train_gnomix.sh").read_text()
        verify_index = text.index("verifying Gnomix genetic maps")
        training_index = text.index('cd "$GNOMIX_DIR"')
        assert verify_index < training_index
        assert "01_convert_gnomix_maps.py" in text
        assert "--verify" in text
        assert 'require_file "$RAW_DIR/genetic_maps_gnomix/provenance.json"' in text
        assert "genetic_map.sha256" in text
        assert 'recorded_map_sha" = "$current_map_sha' in text

    def test_phase05_retrains_model_when_map_generation_changes(self, tmp_path: Path) -> None:
        workdir = tmp_path / "work"
        contract = _write_gnomix_training_contract(workdir)
        raw_dir = workdir / "00_raw_downloads"
        source_dir = raw_dir / "genetic_maps_grch38" / "chr_in_chrom_field"
        source = source_dir / "plink.chrchr1.GRCh38.map"
        self._write_source(source, "chr1 . 0 55550\n")
        map_dir = raw_dir / "genetic_maps_gnomix"
        conversion = self._run_converter(source_dir, map_dir, "1")
        assert conversion.returncode == 0, conversion.stderr

        gnomix_dir = workdir / "05_gnomix_training"
        install_dir = tmp_path / "gnomix-install"
        self._write_source(install_dir / "gnomix.py", "# stub\n")
        self._write_source(install_dir / "config.yaml", "seed: 1\n")
        gnomix_commit = self._init_git_checkout(install_dir)
        effective_config = tmp_path / "effective-config.yaml"
        effective_config.write_text(_gnomix_config())
        config_snapshot = gnomix_dir / "config_snapshots" / "chr1" / "effective_config.yaml"
        self._write_source(config_snapshot, _gnomix_config())
        self._write_source(gnomix_dir / "minquery_chr1.vcf.gz", "stale cached query\n")
        self._write_source(gnomix_dir / "minquery_chr1.vcf.gz.tbi", "stale cached index\n")

        model_dir = gnomix_dir / "output_chr1" / "models" / "model_chm_chr1"
        model_path = model_dir / "model_chm_chr1.pkl"
        marker_path = model_dir / "genetic_map.sha256"
        provenance_path = model_dir / "training_provenance.json"
        self._write_source(model_path, "old-model\n")
        first_map_sha = hashlib.sha256((map_dir / "chr1.map").read_bytes()).hexdigest()
        marker_path.write_text(f"{first_map_sha}  chr1.map\n")
        provenance = _load_module("gnomix_provenance.py", "gnomix_provenance_map_test")
        provenance.write_model_record(
            provenance_path,
            chromosome="chr1",
            expected_commit=gnomix_commit,
            config=effective_config,
            genetic_map=map_dir / "chr1.map",
            model=model_path,
            training_input_manifest=contract["input_manifest"],
            training_split_manifest=contract["split_manifest"],
            expected_training_input_sha256=hashlib.sha256(
                contract["input_manifest"].read_bytes()
            ).hexdigest(),
            expected_training_split_sha256=hashlib.sha256(
                contract["split_manifest"].read_bytes()
            ).hexdigest(),
        )

        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        conda_called = tmp_path / "conda-called"
        conda_stub = stub_dir / "conda"
        conda_stub.write_text(
            "#!/bin/sh\n"
            "training=\n"
            'for argument in "$@"; do\n'
            '  if [ "$argument" = None ]; then training=1; fi\n'
            "done\n"
            'if [ -z "$training" ]; then\n'
            '  if [ "$STUB_RUNTIME_PREFLIGHT_FAIL" = 1 ]; then\n'
            "    echo simulated Gnomix runtime preflight failure >&2\n"
            "    exit 95\n"
            "  fi\n"
            "  exit 0\n"
            "fi\n"
            "printf 'called\\n' > \"$STUB_CONDA_CALLED\"\n"
            "printf 'new-model\\n' > \"$STUB_MODEL_PATH\"\n"
            'mkdir -p "$STUB_SPLIT_DIR"\n'
            'printf \'%s\' "$STUB_TRAIN1_MAP" > "$STUB_SPLIT_DIR/train1.map"\n'
            'printf \'%s\' "$STUB_TRAIN2_MAP" > "$STUB_SPLIT_DIR/train2.map"\n'
            'printf \'%s\' "$STUB_VAL_MAP" > "$STUB_SPLIT_DIR/val.map"\n'
            'if [ -n "${STUB_MUTATE_CONFIG:-}" ]; then\n'
            "  printf 'seed: mutated\\nsimulation:\\n  run: true\\n  splits:\\n"
            "    ratios:\\n      train1: 0.8\\n      train2: 0.15\\n"
            '      val: 0.05\\nmodel:\\n  name: model\\n\' > "$STUB_CONFIG_PATH"\n'
            "fi\n"
            'if [ -n "${STUB_REPLACEMENT_MANIFEST:-}" ]; then\n'
            '  cp "$STUB_REPLACEMENT_MANIFEST" "$STUB_INPUT_MANIFEST_PATH"\n'
            "fi\n"
        )
        conda_stub.chmod(0o755)
        bcftools_called = tmp_path / "bcftools-called"
        bcftools_stub = stub_dir / "bcftools"
        bcftools_stub.write_text(
            "#!/bin/sh\nprintf 'called\\n' > \"$STUB_BCFTOOLS_CALLED\"\nexit 97\n"
        )
        bcftools_stub.chmod(0o755)
        flock_stub = stub_dir / "flock"
        flock_stub.write_text(
            "#!/bin/sh\n"
            'if [ "$STUB_DELETE_SNAPSHOT_ON_UNLOCK" = 1 ] '
            '&& [ "$1" = -u ] && [ "$2" = 9 ]; then\n'
            '  rm -f "$STUB_CONFIG_SNAPSHOT"\n'
            "fi\n"
            "exit 0\n"
        )
        flock_stub.chmod(0o755)

        env = os.environ.copy()
        for variable in (
            "RAW_DIR",
            "LOG_DIR",
            "SITES_DIR",
            "LIFTOVER_DIR",
            "PANEL_DIR",
            "ADMIX_DIR",
            "GNOMIX_DIR",
            "VALIDATION_DIR",
            "BUNDLE_DIR",
        ):
            env.pop(variable, None)
        env.update(
            {
                "WORKDIR": str(workdir),
                "CHROMS": "1",
                "GNOMIX_DIR_INSTALL": str(install_dir),
                "GNOMIX_CONFIG": str(effective_config),
                "GNOMIX_EXPECTED_COMMIT": gnomix_commit,
                "UNION_CATALOG_TSV": str(contract["union_catalog"]),
                "PATH": f"{stub_dir}{os.pathsep}{env['PATH']}",
                "STUB_CONDA_CALLED": str(conda_called),
                "STUB_CONFIG_PATH": str(effective_config),
                "STUB_MODEL_PATH": str(model_path),
                "STUB_SPLIT_DIR": str(contract["train1"].parent),
                "STUB_TRAIN1_MAP": _GNOMIX_TRAIN1_MAP,
                "STUB_TRAIN2_MAP": _GNOMIX_TRAIN2_MAP,
                "STUB_VAL_MAP": _GNOMIX_VAL_MAP,
                "STUB_INPUT_MANIFEST_PATH": str(contract["input_manifest"]),
                "STUB_BCFTOOLS_CALLED": str(bcftools_called),
                "STUB_CONFIG_SNAPSHOT": str(config_snapshot),
                "STUB_DELETE_SNAPSHOT_ON_UNLOCK": "",
                "STUB_RUNTIME_PREFLIGHT_FAIL": "",
            }
        )

        matching = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "05_train_gnomix.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert matching.returncode == 0, matching.stderr
        assert not conda_called.exists()
        assert not bcftools_called.exists()
        assert model_path.read_text() == "old-model\n"

        completion_paths = {
            "model": model_path,
            "marker": marker_path,
            "record": provenance_path,
            "split": contract["split_manifest"],
            "config_snapshot": config_snapshot,
        }
        completion_before = {name: path.read_bytes() for name, path in completion_paths.items()}
        runtime_preflight_failed = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "05_train_gnomix.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env | {"STUB_RUNTIME_PREFLIGHT_FAIL": "1"},
        )
        assert runtime_preflight_failed.returncode == 95
        assert "simulated Gnomix runtime preflight failure" in (runtime_preflight_failed.stderr)
        assert {
            name: path.read_bytes() for name, path in completion_paths.items()
        } == completion_before
        assert not conda_called.exists()

        config_snapshot.unlink()
        missing_snapshot = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "05_train_gnomix.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert missing_snapshot.returncode == 0, missing_snapshot.stderr
        assert conda_called.is_file()
        assert config_snapshot.read_text() == _gnomix_config()
        assert not bcftools_called.exists()
        conda_called.unlink()

        deleted_before_completeness = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "05_train_gnomix.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env | {"STUB_DELETE_SNAPSHOT_ON_UNLOCK": "1"},
        )
        assert deleted_before_completeness.returncode == 1
        assert "MISSING OR STALE" in deleted_before_completeness.stdout
        assert not config_snapshot.exists()
        assert not conda_called.exists()
        config_snapshot.write_text(_gnomix_config())

        contract["train1"].write_text("S1\tAFR\n")
        split_changed = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "05_train_gnomix.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert split_changed.returncode == 0, split_changed.stderr
        assert conda_called.is_file()
        assert not bcftools_called.exists()
        assert contract["train1"].read_text() == _GNOMIX_TRAIN1_MAP
        conda_called.unlink()

        original_input_sha = hashlib.sha256(contract["input_manifest"].read_bytes()).hexdigest()
        contract["union_catalog"].write_bytes(
            contract["union_catalog"].read_bytes() + b"rs-extra\t1\t300\n"
        )
        input_payload = json.loads(contract["input_manifest"].read_text())
        union_artifact = next(
            entry["artifact"]
            for entry in input_payload["source_artifacts"]["marker_selection"]
            if entry["name"] == "union_catalog"
        )
        union_artifact["sha256"] = hashlib.sha256(
            contract["union_catalog"].read_bytes()
        ).hexdigest()
        union_artifact["size_bytes"] = contract["union_catalog"].stat().st_size
        contract["input_manifest"].write_text(
            json.dumps(input_payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        )
        replacement_input_sha = hashlib.sha256(contract["input_manifest"].read_bytes()).hexdigest()
        assert replacement_input_sha != original_input_sha

        input_changed = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "05_train_gnomix.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert input_changed.returncode == 0, input_changed.stderr
        assert conda_called.is_file()
        assert (
            json.loads(provenance_path.read_text())["training_input_manifest"]["sha256"]
            == replacement_input_sha
        )
        conda_called.unlink()

        source.write_text("chr1 . 0 55550\nchr1 . 0.080572 82571\n")
        conversion = self._run_converter(source_dir, map_dir, "1")
        assert conversion.returncode == 0, conversion.stderr
        changed_map_sha = hashlib.sha256((map_dir / "chr1.map").read_bytes()).hexdigest()
        assert changed_map_sha != first_map_sha

        changed = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "05_train_gnomix.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert changed.returncode == 0, changed.stderr
        assert (
            "source/input/split/config/map/model provenance changed or missing; retraining"
            in changed.stdout
        )
        assert conda_called.is_file()
        assert model_path.read_text() == "new-model\n"
        assert marker_path.read_text() == f"{changed_map_sha}  chr1.map\n"
        record = json.loads(provenance_path.read_text())
        assert record["gnomix_git_commit"] == gnomix_commit
        assert (
            record["effective_config_sha256"]
            == hashlib.sha256(effective_config.read_bytes()).hexdigest()
        )
        assert record["genetic_map_sha256"] == changed_map_sha
        assert record["model_sha256"] == hashlib.sha256(model_path.read_bytes()).hexdigest()
        assert not Path(f"{model_path}.stale").exists()

        conda_called.unlink()
        effective_config.write_text(_gnomix_config(seed=2))
        changed_config = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "05_train_gnomix.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert changed_config.returncode == 0, changed_config.stderr
        assert conda_called.is_file()
        record = json.loads(provenance_path.read_text())
        assert (
            record["effective_config_sha256"]
            == hashlib.sha256(effective_config.read_bytes()).hexdigest()
        )

        conda_called.unlink()
        stable_input_bytes = contract["input_manifest"].read_bytes()
        replacement_manifest = tmp_path / "replacement-training-inputs.json"
        replacement_payload = json.loads(stable_input_bytes)
        replacement_payload["reference_panel"]["name"] += " replacement"
        replacement_manifest.write_text(
            json.dumps(
                replacement_payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        manifests = _load_module(
            "gnomix_training_manifests.py",
            "gnomix_training_manifest_generation_race_test",
        )
        manifests.load_input_manifest(replacement_manifest)
        contract["train1"].write_text("S1\tAFR\n")
        generation_race_env = env | {"STUB_REPLACEMENT_MANIFEST": str(replacement_manifest)}
        generation_changed_during_training = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "05_train_gnomix.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=generation_race_env,
        )
        assert generation_changed_during_training.returncode == 1
        assert "training-input manifest generation changed during Gnomix training" in (
            generation_changed_during_training.stderr + generation_changed_during_training.stdout
        )
        assert conda_called.is_file()
        assert not provenance_path.exists()
        assert not marker_path.exists()
        contract["input_manifest"].write_bytes(stable_input_bytes)

        conda_called.unlink()
        effective_config.write_text(_gnomix_config(seed=3))
        mutating_env = env | {"STUB_MUTATE_CONFIG": "1"}
        mutated_during_training = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "05_train_gnomix.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=mutating_env,
        )
        assert mutated_during_training.returncode == 1
        assert "effective Gnomix config changed during training" in (
            mutated_during_training.stderr + mutated_during_training.stdout
        )
        assert conda_called.is_file()
        assert not provenance_path.exists()
        assert not marker_path.exists()

    @pytest.mark.parametrize(
        ("expected_commit", "message"),
        [
            ("", "must be an explicitly selected full 40-character"),
            ("0" * 40, "checkout commit mismatch"),
        ],
    )
    def test_phase05_rejects_missing_or_mismatched_revision_before_model_mutation(
        self,
        tmp_path: Path,
        expected_commit: str,
        message: str,
    ) -> None:
        workdir = tmp_path / "work"
        install_dir = tmp_path / "gnomix-install"
        self._write_source(install_dir / "gnomix.py", "# stub\n")
        self._write_source(install_dir / "config.yaml", "seed: 1\n")
        self._init_git_checkout(install_dir)
        self._write_source(workdir / "04_admixture_filtering" / "sample_map.txt", "sample\tEUR\n")
        self._write_source(
            workdir / "00_raw_downloads" / "genetic_maps_gnomix" / "provenance.json",
            "{}\n",
        )

        model_dir = workdir / "05_gnomix_training" / "output_chr1" / "models" / "model_chm_chr1"
        model_path = model_dir / "model_chm_chr1.pkl"
        marker_path = model_dir / "genetic_map.sha256"
        provenance_path = model_dir / "training_provenance.json"
        self._write_source(model_path, "existing-model\n")
        marker_path.write_text("existing-map-binding\n")
        provenance_path.write_text("existing-provenance\n")

        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        conda_called = tmp_path / "conda-called"
        for command in ("conda", "bcftools"):
            stub = stub_dir / command
            stub.write_text("#!/bin/sh\nprintf 'called\\n' > \"$STUB_CONDA_CALLED\"\nexit 97\n")
            stub.chmod(0o755)
        flock_stub = stub_dir / "flock"
        flock_stub.write_text("#!/bin/sh\nexit 0\n")
        flock_stub.chmod(0o755)

        env = os.environ.copy()
        for variable in (
            "RAW_DIR",
            "LOG_DIR",
            "SITES_DIR",
            "LIFTOVER_DIR",
            "PANEL_DIR",
            "ADMIX_DIR",
            "GNOMIX_DIR",
            "VALIDATION_DIR",
            "BUNDLE_DIR",
        ):
            env.pop(variable, None)
        env.update(
            {
                "WORKDIR": str(workdir),
                "CHROMS": "1",
                "GNOMIX_DIR_INSTALL": str(install_dir),
                "GNOMIX_CONFIG": str(install_dir / "config.yaml"),
                "GNOMIX_EXPECTED_COMMIT": expected_commit,
                "PATH": f"{stub_dir}{os.pathsep}{env['PATH']}",
                "STUB_CONDA_CALLED": str(conda_called),
            }
        )

        result = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "05_train_gnomix.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

        assert result.returncode == 1
        assert message in result.stderr
        assert model_path.read_text() == "existing-model\n"
        assert marker_path.read_text() == "existing-map-binding\n"
        assert provenance_path.read_text() == "existing-provenance\n"
        assert not conda_called.exists()

    def test_phase05_skips_discarded_post_train_query(self) -> None:
        text = (SCRIPTS_DIR / "05_train_gnomix.sh").read_text()
        assert "require bcftools" not in text
        assert "minquery" not in text
        launcher_index = text.index('"$GNOMIX_DIR_INSTALL/gnomix.py"')
        assert text.index("    None \\\n", launcher_index) > launcher_index

    def test_phase07_packages_map_provenance(self) -> None:
        text = (SCRIPTS_DIR / "07_assemble_bundle.sh").read_text()
        assert 'require_file "$RAW_DIR/genetic_maps_gnomix/provenance.json"' in text
        assert (
            'cp -f "$RAW_DIR/genetic_maps_gnomix/provenance.json" '
            "metadata/gnomix_genetic_maps.json"
        ) in text
        assert "trained model does not match current genetic map" in text
        assert '"metadata/gnomix_model_map_chr${chr}.sha256"' in text
        assert '"metadata/gnomix_model_chr${chr}.provenance.json"' in text
        assert "metadata/gnomix_training_provenance.json" in text
        assert 'gnomix_provenance.py" verify-record' in text
        assert text.index('gnomix_provenance.py" verify-record') < text.index('cd "$BUNDLE_DIR"')
        assert "Phase 07 publishes full autosomal bundles only" in text
        assert (
            'expected_autosomes="1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22"' in text
        )
        assert "rm -rf gnomix_models/chr*" in text
        assert "metadata/gnomix_model_map_chr*.sha256" in text


class TestGnomixTrainingManifestIntegration:
    @staticmethod
    def _phase05_preflight_fixture(
        tmp_path: Path,
        *,
        config_text: str,
        drift_union_catalog: bool = False,
        undersize_oce_training_population: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, bytes], dict[str, Path]]:
        workdir = tmp_path / "work"
        contract = _write_gnomix_training_contract(workdir)
        if undersize_oce_training_population:
            payload = json.loads(contract["input_manifest"].read_text())
            oce_training = [
                member
                for member in payload["sample_mappings"]["members"]
                if member["role"] == "training" and member["superpopulation"] == "OCE"
            ]
            for member in oce_training[4:]:
                member["role"] = "heldout_test"
            training_count = sum(
                member["role"] == "training" for member in payload["sample_mappings"]["members"]
            )
            payload["sample_mappings"]["counts"] = {
                "full": len(payload["sample_mappings"]["members"]),
                "heldout_test": len(payload["sample_mappings"]["members"]) - training_count,
                "training": training_count,
            }
            contract["input_manifest"].write_text(
                json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            )
        if drift_union_catalog:
            contract["union_catalog"].write_bytes(
                contract["union_catalog"].read_bytes() + b"rs-extra\t1\t300\n"
            )

        install_dir = tmp_path / "gnomix-install"
        install_dir.mkdir()
        (install_dir / "gnomix.py").write_text("# fixture\n")
        commit = TestPhase01GnomixMaps._init_git_checkout(install_dir)
        config = tmp_path / "effective-config.yaml"
        config.write_text(config_text)
        map_dir = workdir / "00_raw_downloads" / "genetic_maps_gnomix"
        map_dir.mkdir(parents=True)
        (map_dir / "provenance.json").write_text("{}\n")

        model_dir = workdir / "05_gnomix_training" / "output_chr1" / "models" / "model_chm_chr1"
        model = model_dir / "model_chm_chr1.pkl"
        marker = model_dir / "genetic_map.sha256"
        record = model_dir / "training_provenance.json"
        model.write_bytes(b"prior-model\n")
        marker.write_bytes(b"prior-map-binding\n")
        record.write_bytes(b"prior-provenance\n")
        paths = {
            "model": model,
            "marker": marker,
            "record": record,
            "split": contract["split_manifest"],
        }
        before = {name: path.read_bytes() for name, path in paths.items()}

        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        called = tmp_path / "training-command-called"
        for command in ("conda", "bcftools", "flock"):
            stub = stub_dir / command
            stub.write_text("#!/bin/sh\nprintf 'called\\n' > \"$STUB_COMMAND_CALLED\"\nexit 97\n")
            stub.chmod(0o755)

        env = os.environ.copy()
        for variable in (
            "RAW_DIR",
            "LOG_DIR",
            "SITES_DIR",
            "LIFTOVER_DIR",
            "PANEL_DIR",
            "ADMIX_DIR",
            "GNOMIX_DIR",
            "VALIDATION_DIR",
            "BUNDLE_DIR",
        ):
            env.pop(variable, None)
        env.update(
            {
                "WORKDIR": str(workdir),
                "CHROMS": "1",
                "GNOMIX_DIR_INSTALL": str(install_dir),
                "GNOMIX_CONFIG": str(config),
                "GNOMIX_EXPECTED_COMMIT": commit,
                "UNION_CATALOG_TSV": str(contract["union_catalog"]),
                "PATH": f"{stub_dir}{os.pathsep}{env['PATH']}",
                "STUB_COMMAND_CALLED": str(called),
            }
        )
        result = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "05_train_gnomix.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert not called.exists()
        return result, before, paths

    def test_phase05_rejects_presimulated_config_before_model_mutation(
        self, tmp_path: Path
    ) -> None:
        result, before, paths = self._phase05_preflight_fixture(
            tmp_path,
            config_text="seed: 1\nsimulation:\n  run: false\n",
        )

        assert result.returncode == 1
        assert "simulation.run=false is unsupported" in result.stderr
        assert {name: path.read_bytes() for name, path in paths.items()} == before

    @pytest.mark.parametrize(
        ("config_text", "message"),
        [
            (
                _gnomix_config().replace("val: 0.05", "val: 0"),
                "ratios.val must be a finite number greater than zero",
            ),
            (
                _gnomix_config().replace("val: 0.05", "val: 0.0005"),
                "ratios.val must equal the release value 0.05",
            ),
            (
                _gnomix_config().replace(
                    "      train1: 0.8\n      train2: 0.15\n      val: 0.05\n",
                    "      val: 0.05\n      train2: 0.15\n      train1: 0.8\n",
                ),
                "must be ordered train1, train2, val",
            ),
            (
                _gnomix_config().replace("name: model", "name: custom"),
                "model.name must be 'model'",
            ),
            (
                _gnomix_config().replace("model:\n  name: model\n", "model: {}\n"),
                "model.name must be 'model'",
            ),
            (
                _gnomix_config().replace("name: model", "name: model\n  inference: fast"),
                "model.inference must resolve to 'default'",
            ),
            (
                _gnomix_config().replace("name: model", "name: model\n  calibrate: true"),
                "model.calibrate must be false or null",
            ),
            (
                _gnomix_config().replace("verbose: true", "verbose: false"),
                "verbose must be the boolean true",
            ),
        ],
    )
    def test_phase05_rejects_incompatible_config_before_model_mutation(
        self,
        tmp_path: Path,
        config_text: str,
        message: str,
    ) -> None:
        result, before, paths = self._phase05_preflight_fixture(
            tmp_path,
            config_text=config_text,
        )

        assert result.returncode == 1
        assert message in result.stderr
        assert {name: path.read_bytes() for name, path in paths.items()} == before

    def test_phase05_rejects_population_with_empty_validation_rounding_before_mutation(
        self, tmp_path: Path
    ) -> None:
        result, before, paths = self._phase05_preflight_fixture(
            tmp_path,
            config_text=_gnomix_config(),
            undersize_oce_training_population=True,
        )

        assert result.returncode == 1
        assert "at least 11 training founders per superpopulation" in result.stderr
        assert {name: path.read_bytes() for name, path in paths.items()} == before

    def test_phase05_rejects_training_input_drift_before_model_mutation(
        self, tmp_path: Path
    ) -> None:
        result, before, paths = self._phase05_preflight_fixture(
            tmp_path,
            config_text=_gnomix_config(),
            drift_union_catalog=True,
        )

        assert result.returncode == 1
        assert "training-input manifest" in result.stderr
        assert {name: path.read_bytes() for name, path in paths.items()} == before

    def test_phase_scripts_wire_manifest_gates_before_publication(self) -> None:
        shared_env = (SCRIPTS_DIR / "env.sh").read_text()
        phase4 = (SCRIPTS_DIR / "04_admixture_filter.sh").read_text()
        phase5 = (SCRIPTS_DIR / "05_train_gnomix.sh").read_text()
        phase7 = (SCRIPTS_DIR / "07_assemble_bundle.sh").read_text()
        build_env_lock = (
            REPO_ROOT / "docs" / "lai-bundle-release-runbook-env.lock.yaml"
        ).read_text()

        assert "Phase 04 freezes full autosomal inputs only" in phase4
        assert phase4.index("06f_select_heldout.py") < phase4.index("create-input")
        assert "--out-full-backup" in phase4
        assert "GNOMIX_TRAINING_INPUT_SHARED_ARGS" in shared_env
        assert "--heldout-sample-map" in shared_env
        for text in (phase4, phase5, phase7):
            assert '"${GNOMIX_TRAINING_INPUT_SHARED_ARGS[@]}"' in text
        assert "  - pyyaml=6.0.3\n" in build_env_lock

        mutation_index = phase5.index('rm -f "$model_provenance"')
        assert phase5.index("verify-config") < mutation_index
        assert phase5.index("verify-input") < mutation_index
        assert phase5.index("create-splits") < phase5.index("write-record")
        assert "training-input manifest generation changed during Gnomix training" in phase5

        preflight_end = phase7.index('cd "$BUNDLE_DIR"')
        assert phase7.index("verify-input") < preflight_end
        assert phase7.index("verify-splits") < preflight_end
        assert phase7.index("verify-record") < preflight_end
        assert phase7.index("verify-aggregate") < preflight_end
        assert phase7.index('python "$SCRIPT_DIR/gnomix_launcher.py"') < preflight_end
        assert phase7.index('python "$SCRIPT_DIR/07b_reexport_gnomix_models.py"') < preflight_end
        for path in (
            "metadata/gnomix_training_inputs.json",
            "metadata/gnomix_training_splits.json",
            "metadata/gnomix_training_provenance.json",
        ):
            assert path in phase7

    def test_phase07_missing_model_fails_before_bundle_mutation(self, tmp_path: Path) -> None:
        workdir = tmp_path / "work"
        contract = _write_gnomix_training_contract(workdir)
        raw_dir = workdir / "00_raw_downloads"
        source_dir = raw_dir / "genetic_maps_grch38" / "chr_in_chrom_field"
        for number in range(1, 23):
            TestPhase01GnomixMaps._write_source(
                source_dir / f"plink.chrchr{number}.GRCh38.map",
                f"chr{number} . 0 100\n",
            )
        map_dir = raw_dir / "genetic_maps_gnomix"
        conversion = TestPhase01GnomixMaps._run_converter(
            source_dir,
            map_dir,
            *(str(number) for number in range(1, 23)),
        )
        assert conversion.returncode == 0, conversion.stderr

        install_dir = tmp_path / "gnomix-install"
        install_dir.mkdir()
        (install_dir / "gnomix.py").write_text("# fixture\n")
        commit = TestPhase01GnomixMaps._init_git_checkout(install_dir)
        bundle_dir = workdir / "07_final_bundle"
        bundle_dir.mkdir()
        sentinel = bundle_dir / "prior-generation.txt"
        sentinel.write_bytes(b"do-not-mutate\n")

        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        conda_called = tmp_path / "conda-called"
        conda_stub = stub_dir / "conda"
        conda_stub.write_text("#!/bin/sh\nprintf 'called\\n' > \"$STUB_CONDA_CALLED\"\nexit 97\n")
        conda_stub.chmod(0o755)
        flock_stub = stub_dir / "flock"
        flock_stub.write_text("#!/bin/sh\nexit 0\n")
        flock_stub.chmod(0o755)
        env = os.environ.copy()
        for variable in (
            "RAW_DIR",
            "LOG_DIR",
            "SITES_DIR",
            "LIFTOVER_DIR",
            "PANEL_DIR",
            "ADMIX_DIR",
            "GNOMIX_DIR",
            "VALIDATION_DIR",
            "BUNDLE_DIR",
        ):
            env.pop(variable, None)
        env.update(
            {
                "WORKDIR": str(workdir),
                "GNOMIX_DIR_INSTALL": str(install_dir),
                "GNOMIX_EXPECTED_COMMIT": commit,
                "UNION_CATALOG_TSV": str(contract["union_catalog"]),
                "PATH": f"{stub_dir}{os.pathsep}{env['PATH']}",
                "STUB_CONDA_CALLED": str(conda_called),
            }
        )

        result = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "07_assemble_bundle.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

        assert result.returncode == 1
        assert "model_chm_chr1.pkl" in result.stderr
        assert sentinel.read_bytes() == b"do-not-mutate\n"
        assert sorted(path.name for path in bundle_dir.iterdir()) == [sentinel.name]
        assert not conda_called.exists()

    def test_phase07_runtime_preflight_failure_preserves_existing_bundle(
        self, tmp_path: Path
    ) -> None:
        workdir = tmp_path / "work"
        contract = _write_gnomix_training_contract(workdir)
        raw_dir = workdir / "00_raw_downloads"
        map_dir = raw_dir / "genetic_maps_gnomix"
        TestPhase01GnomixMaps._write_source(map_dir / "provenance.json", "fixture\n")

        install_dir = tmp_path / "gnomix-install"
        TestPhase01GnomixMaps._write_source(install_dir / "gnomix.py", "# fixture\n")
        for number in range(1, 23):
            chromosome = f"chr{number}"
            derived_map = map_dir / f"{chromosome}.map"
            TestPhase01GnomixMaps._write_source(derived_map, f"{chromosome}\t100\t0\n")
            model_dir = (
                workdir
                / "05_gnomix_training"
                / f"output_chr{number}"
                / "models"
                / f"model_chm_{chromosome}"
            )
            TestPhase01GnomixMaps._write_source(
                model_dir / f"model_chm_{chromosome}.pkl", "fixture-model\n"
            )
            TestPhase01GnomixMaps._write_source(
                model_dir / "genetic_map.sha256",
                f"{hashlib.sha256(derived_map.read_bytes()).hexdigest()}  {derived_map.name}\n",
            )
            TestPhase01GnomixMaps._write_source(model_dir / "training_provenance.json", "{}\n")
            (model_dir / "training_splits.json").write_bytes(
                contract["split_manifest"].read_bytes()
            )
            config_snapshot = (
                workdir
                / "05_gnomix_training"
                / "config_snapshots"
                / chromosome
                / "effective_config.yaml"
            )
            TestPhase01GnomixMaps._write_source(config_snapshot, _gnomix_config())
            split_dir = (
                workdir
                / "05_gnomix_training"
                / f"output_chr{number}"
                / "generated_data"
                / "sample_maps"
            )
            for name, content in (
                ("train1", _GNOMIX_TRAIN1_MAP),
                ("train2", _GNOMIX_TRAIN2_MAP),
                ("val", _GNOMIX_VAL_MAP),
            ):
                TestPhase01GnomixMaps._write_source(split_dir / f"{name}.map", content)

        bundle_dir = workdir / "07_final_bundle"
        bundle_dir.mkdir()
        sentinel = bundle_dir / "prior-generation.txt"
        sentinel.write_bytes(b"do-not-mutate\n")

        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        for command in ("python", "python3"):
            stub = stub_dir / command
            stub.write_text("#!/bin/sh\nexit 0\n")
            stub.chmod(0o755)
        conda_log = tmp_path / "conda.log"
        conda_stub = stub_dir / "conda"
        conda_stub.write_text(
            "#!/bin/sh\n"
            'printf \'%s\\n\' "$*" >> "$STUB_CONDA_LOG"\n'
            'calls=$(wc -l < "$STUB_CONDA_LOG")\n'
            'if [ "$calls" -eq 1 ]; then exit 0; fi\n'
            "echo simulated Phase 07 runtime preflight failure >&2\n"
            "exit 95\n"
        )
        conda_stub.chmod(0o755)
        flock_stub = stub_dir / "flock"
        flock_stub.write_text("#!/bin/sh\nexit 0\n")
        flock_stub.chmod(0o755)

        env = os.environ.copy()
        for variable in (
            "RAW_DIR",
            "LOG_DIR",
            "SITES_DIR",
            "LIFTOVER_DIR",
            "PANEL_DIR",
            "ADMIX_DIR",
            "GNOMIX_DIR",
            "VALIDATION_DIR",
            "BUNDLE_DIR",
        ):
            env.pop(variable, None)
        env.update(
            {
                "WORKDIR": str(workdir),
                "GNOMIX_DIR_INSTALL": str(install_dir),
                "GNOMIX_EXPECTED_COMMIT": "a" * 40,
                "GNOMIX_ENV": "expected-gnomix",
                "UNION_CATALOG_TSV": str(contract["union_catalog"]),
                "PATH": f"{stub_dir}{os.pathsep}{env['PATH']}",
                "STUB_CONDA_LOG": str(conda_log),
            }
        )

        result = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "07_assemble_bundle.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

        assert result.returncode == 95
        assert "simulated Phase 07 runtime preflight failure" in result.stderr
        conda_calls = conda_log.read_text().splitlines()
        assert len(conda_calls) == 2
        assert all(
            call.startswith("run -n expected-gnomix --no-capture-output ") for call in conda_calls
        )
        assert "gnomix_launcher.py" in conda_calls[0]
        assert "07b_reexport_gnomix_models.py" in conda_calls[1]
        assert "model_chm_chr1.pkl" in conda_calls[1]
        assert "--out-dir" in conda_calls[1]
        assert str(bundle_dir) not in conda_calls[1]
        assert sentinel.read_bytes() == b"do-not-mutate\n"
        assert sorted(path.name for path in bundle_dir.iterdir()) == [sentinel.name]

    def test_phase07_rejects_coherently_forged_config_hashes_before_bundle_mutation(
        self, tmp_path: Path
    ) -> None:
        workdir = tmp_path / "work"
        contract = _write_gnomix_training_contract(workdir)
        raw_dir = workdir / "00_raw_downloads"
        source_dir = raw_dir / "genetic_maps_grch38" / "chr_in_chrom_field"
        for number in range(1, 23):
            TestPhase01GnomixMaps._write_source(
                source_dir / f"plink.chrchr{number}.GRCh38.map",
                f"chr{number} . 0 100\n",
            )
        map_dir = raw_dir / "genetic_maps_gnomix"
        conversion = TestPhase01GnomixMaps._run_converter(
            source_dir,
            map_dir,
            *(str(number) for number in range(1, 23)),
        )
        assert conversion.returncode == 0, conversion.stderr

        install_dir = tmp_path / "gnomix-install"
        install_dir.mkdir()
        (install_dir / "gnomix.py").write_text("# fixture\n")
        commit = TestPhase01GnomixMaps._init_git_checkout(install_dir)
        provenance = _load_module(
            "gnomix_provenance.py", "gnomix_phase07_config_authentication_test"
        )
        input_sha256 = hashlib.sha256(contract["input_manifest"].read_bytes()).hexdigest()
        forged_config_sha256 = "f" * 64
        record_paths: list[Path] = []

        for number in range(1, 23):
            chromosome = f"chr{number}"
            split_map_dir = (
                workdir
                / "05_gnomix_training"
                / f"output_chr{number}"
                / "generated_data"
                / "sample_maps"
            )
            split_map_dir.mkdir(parents=True, exist_ok=True)
            for split_name, source_key in (
                ("train1", "train1"),
                ("train2", "train2"),
                ("val", "val"),
            ):
                target = split_map_dir / f"{split_name}.map"
                if not target.exists():
                    target.write_bytes(contract[source_key].read_bytes())

            model_dir = (
                workdir
                / "05_gnomix_training"
                / f"output_chr{number}"
                / "models"
                / f"model_chm_{chromosome}"
            )
            model_dir.mkdir(parents=True, exist_ok=True)
            split_manifest = model_dir / "training_splits.json"
            if not split_manifest.exists():
                split_manifest.write_bytes(contract["split_manifest"].read_bytes())
            split_sha256 = hashlib.sha256(split_manifest.read_bytes()).hexdigest()

            model = model_dir / f"model_chm_{chromosome}.pkl"
            model.write_bytes(f"model-{chromosome}\n".encode())
            derived_map = map_dir / f"{chromosome}.map"
            (model_dir / "genetic_map.sha256").write_text(
                f"{hashlib.sha256(derived_map.read_bytes()).hexdigest()}  {derived_map.name}\n"
            )
            config = (
                workdir
                / "05_gnomix_training"
                / "config_snapshots"
                / chromosome
                / "effective_config.yaml"
            )
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(_gnomix_config())
            record = model_dir / "training_provenance.json"
            provenance.write_model_record(
                record,
                chromosome=chromosome,
                expected_commit=commit,
                config=config,
                genetic_map=derived_map,
                model=model,
                training_input_manifest=contract["input_manifest"],
                training_split_manifest=split_manifest,
                expected_training_input_sha256=input_sha256,
                expected_training_split_sha256=split_sha256,
            )
            payload = json.loads(record.read_text())
            payload["effective_config_sha256"] = forged_config_sha256
            record.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            record_paths.append(record)

        assert {
            json.loads(record.read_text())["effective_config_sha256"] for record in record_paths
        } == {forged_config_sha256}

        bundle_dir = workdir / "07_final_bundle"
        bundle_dir.mkdir()
        sentinel = bundle_dir / "prior-generation.txt"
        sentinel.write_bytes(b"do-not-mutate\n")
        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        conda_called = tmp_path / "conda-called"
        conda_stub = stub_dir / "conda"
        conda_stub.write_text("#!/bin/sh\nprintf 'called\\n' > \"$STUB_CONDA_CALLED\"\nexit 97\n")
        conda_stub.chmod(0o755)
        flock_stub = stub_dir / "flock"
        flock_stub.write_text("#!/bin/sh\nexit 0\n")
        flock_stub.chmod(0o755)
        env = os.environ.copy()
        for variable in (
            "RAW_DIR",
            "LOG_DIR",
            "SITES_DIR",
            "LIFTOVER_DIR",
            "PANEL_DIR",
            "ADMIX_DIR",
            "GNOMIX_DIR",
            "VALIDATION_DIR",
            "BUNDLE_DIR",
        ):
            env.pop(variable, None)
        env.update(
            {
                "WORKDIR": str(workdir),
                "GNOMIX_DIR_INSTALL": str(install_dir),
                "GNOMIX_EXPECTED_COMMIT": commit,
                "UNION_CATALOG_TSV": str(contract["union_catalog"]),
                "PATH": f"{stub_dir}{os.pathsep}{env['PATH']}",
                "STUB_CONDA_CALLED": str(conda_called),
            }
        )

        result = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "07_assemble_bundle.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

        assert result.returncode == 1
        assert "effective_config_sha256 mismatch" in result.stderr
        assert sentinel.read_bytes() == b"do-not-mutate\n"
        assert sorted(path.name for path in bundle_dir.iterdir()) == [sentinel.name]
        assert not conda_called.exists()


class TestGnomixTrainingProvenance:
    @staticmethod
    def _module():
        return _load_module("gnomix_provenance.py", "gnomix_provenance_tests")

    @staticmethod
    def _checkout(path: Path) -> str:
        path.mkdir()
        (path / "gnomix.py").write_text("# fixture\n")
        (path / "config.yaml").write_text("seed: 1\n")
        return TestPhase01GnomixMaps._init_git_checkout(path)

    def test_checkout_requires_full_matching_revision(self, tmp_path: Path) -> None:
        provenance = self._module()
        checkout = tmp_path / "gnomix"
        commit = self._checkout(checkout)

        with pytest.raises(provenance.ProvenanceError, match="full 40-character"):
            provenance.verify_checkout(checkout, "")
        with pytest.raises(provenance.ProvenanceError, match="commit mismatch"):
            provenance.verify_checkout(checkout, "0" * 40)
        assert provenance.verify_checkout(checkout, commit) == commit

    @pytest.mark.parametrize(
        "value",
        [
            _gnomix_config(),
            (
                "verbose: true\nsimulation:\n"
                "  run: True # upstream spelling\n"
                "  splits: {ratios: {train1: 0.8, train2: 0.15, val: 0.05}}\n"
                "model: {name: model}\n"
            ),
            (
                "verbose: true\nseed: 94305\n"
                "simulation:\n"
                "    run: TRUE\n"
                "    path: simulated\n"
                "    splits: {ratios: {train1: 0.8, train2: 0.15, val: 0.05}}\n"
                "model: {name: model}\n"
            ),
            (
                "verbose: true\nsimulation: {run: true, "
                "splits: {ratios: {train1: 0.8, train2: 0.15, val: 0.05}}}\n"
                "model: {name: model}\n"
            ),
            (
                "verbose: true\nsimulation:\n"
                "  run: &enabled true\n"
                "  splits: {ratios: {train1: 0.8, train2: 0.15, val: 0.05}}\n"
                "model: {name: model}\n"
            ),
            (
                '"verbose": !!bool true\n"simulation":\n'
                '  "run": !!bool true\n'
                '  "splits": {"ratios": {"train1": 0.8, "train2": 0.15, "val": 0.05}}\n'
                '"model": {"name": "model"}\n'
            ),
        ],
    )
    def test_config_gate_accepts_only_fresh_simulation(self, tmp_path: Path, value: str) -> None:
        provenance = self._module()
        config = tmp_path / "config.yaml"
        config.write_text(value)

        assert provenance.verify_simulation_config(config) is True

    @pytest.mark.parametrize(
        ("value", "message"),
        [
            ("simulation:\n  run: false\n", "pre-simulated arrays are not inventoried"),
            ("simulation:\n  run: 'true'\n", "must be the boolean true"),
            ("simulation:\n\trun: true\n", "invalid Gnomix config YAML"),
            ("simulation:\n  run: true\n  run: true\n", "duplicate key 'run'"),
            (
                'simulation:\n  run: true\n  "run": false\n  path: /tmp/precomputed\n',
                "duplicate key 'run'",
            ),
            (
                "simulation:\n  run: true\n  !!str run: false\n  path: /tmp/precomputed\n",
                "duplicate key 'run'",
            ),
            (
                'simulation:\n  run: true\n"simulation":\n  run: false\n',
                "duplicate key 'simulation'",
            ),
            ("simulation: !!python/object:builtins.dict {}\n", "invalid Gnomix config YAML"),
            ("seed: 94305\n", "simulation must be a mapping"),
            ("simulation:\n  run: true\n", "simulation.splits must be a mapping"),
            (
                "simulation:\n  run: true\n  splits: {}\n",
                "simulation.splits.ratios must be a mapping",
            ),
            (
                "simulation:\n  run: true\n  splits: {ratios: {train1: 0.8, train2: 0.2}}\n",
                "must contain exactly train1, train2, and val",
            ),
            (
                "simulation:\n"
                "  run: true\n"
                "  splits: {ratios: {train1: 0.8, train2: 0.15, val: 0}}\n",
                "ratios.val must be a finite number greater than zero",
            ),
            (
                "simulation:\n"
                "  run: true\n"
                "  splits: {ratios: {train1: true, train2: 0.15, val: 0.05}}\n",
                "ratios.train1 must be a finite number greater than zero",
            ),
            (
                "simulation:\n"
                "  run: true\n"
                "  splits: {ratios: {train1: 0.8, train2: .nan, val: 0.05}}\n",
                "ratios.train2 must be a finite number greater than zero",
            ),
            (
                _gnomix_config().replace("val: 0.05", "val: 0.0005"),
                "ratios.val must equal the release value 0.05",
            ),
            (
                _gnomix_config().replace(
                    "      train1: 0.8\n      train2: 0.15\n      val: 0.05\n",
                    "      val: 0.05\n      train2: 0.15\n      train1: 0.8\n",
                ),
                "must be ordered train1, train2, val",
            ),
            (
                _gnomix_config().replace("name: model", "name: custom"),
                "model.name must be 'model'",
            ),
            (
                _gnomix_config().replace("name: model", "name: model\n  inference: best"),
                "model.inference must resolve to 'default'",
            ),
            (
                _gnomix_config().replace("name: model", "name: model\n  calibrate: true"),
                "model.calibrate must be false or null",
            ),
            (
                _gnomix_config().replace("name: model", "name: model\n  calibrate: 0"),
                "model.calibrate must be false or null",
            ),
            (
                _gnomix_config().replace("verbose: true", "verbose: false"),
                "verbose must be the boolean true",
            ),
            (
                _gnomix_config().replace("model:\n  name: model\n", ""),
                "config model must be a mapping",
            ),
        ],
    )
    def test_config_gate_rejects_uninventoried_or_ambiguous_modes(
        self,
        tmp_path: Path,
        value: str,
        message: str,
    ) -> None:
        provenance = self._module()
        config = tmp_path / "config.yaml"
        config.write_text(value)

        with pytest.raises(provenance.ProvenanceError, match=message):
            provenance.verify_simulation_config(config)

    @pytest.mark.parametrize(
        ("training_limit", "missing_superpopulation", "message"),
        [
            (25, None, "more than 25 training founders"),
            (None, "OCE", "exactly the release superpopulations"),
        ],
    )
    def test_training_input_viability_rejects_upstream_split_edge_cases(
        self,
        tmp_path: Path,
        training_limit: int | None,
        missing_superpopulation: str | None,
        message: str,
    ) -> None:
        provenance = self._module()
        contract = _write_gnomix_training_contract(tmp_path / "contract")
        manifest = contract["input_manifest"]
        payload = json.loads(manifest.read_text())
        seen_training = 0
        for member in payload["sample_mappings"]["members"]:
            if member["role"] != "training":
                continue
            seen_training += 1
            if member["superpopulation"] == missing_superpopulation or (
                training_limit is not None and seen_training > training_limit
            ):
                member["role"] = "heldout_test"
        training_count = sum(
            member["role"] == "training" for member in payload["sample_mappings"]["members"]
        )
        payload["sample_mappings"]["counts"] = {
            "full": len(payload["sample_mappings"]["members"]),
            "heldout_test": len(payload["sample_mappings"]["members"]) - training_count,
            "training": training_count,
        }
        manifest.write_text(
            json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        )

        with pytest.raises(provenance.ProvenanceError, match=message):
            provenance.verify_training_input_viability(manifest)

    def test_checkout_rejects_tracked_and_untracked_changes(self, tmp_path: Path) -> None:
        provenance = self._module()
        checkout = tmp_path / "gnomix"
        commit = self._checkout(checkout)

        (checkout / "gnomix.py").write_text("# modified\n")
        with pytest.raises(provenance.ProvenanceError, match="checkout is dirty"):
            provenance.verify_checkout(checkout, commit)
        (checkout / "gnomix.py").write_text("# fixture\n")
        (checkout / "untracked.py").write_text("# untracked\n")
        with pytest.raises(provenance.ProvenanceError, match="checkout is dirty"):
            provenance.verify_checkout(checkout, commit)

    def test_checkout_rejects_missing_or_wrong_official_origin(self, tmp_path: Path) -> None:
        provenance = self._module()
        checkout = tmp_path / "gnomix"
        commit = self._checkout(checkout)

        subprocess.run(
            [
                "git",
                "-C",
                str(checkout),
                "remote",
                "set-url",
                "origin",
                "https://github.com/example/gnomix-fork.git",
            ],
            check=True,
        )
        with pytest.raises(provenance.ProvenanceError, match="origin mismatch"):
            provenance.verify_checkout(checkout, commit)

        subprocess.run(["git", "-C", str(checkout), "remote", "remove", "origin"], check=True)
        with pytest.raises(provenance.ProvenanceError, match="must configure origin"):
            provenance.verify_checkout(checkout, commit)

    def test_checkout_commit_must_be_in_fetched_origin_history(self, tmp_path: Path) -> None:
        provenance = self._module()
        checkout = tmp_path / "gnomix"
        self._checkout(checkout)
        (checkout / "config.yaml").write_text("seed: 2\n")
        subprocess.run(["git", "-C", str(checkout), "add", "config.yaml"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(checkout),
                "-c",
                "user.name=Yeliztli Tests",
                "-c",
                "user.email=tests@yeliztli.invalid",
                "commit",
                "-q",
                "-m",
                "local-only commit",
            ],
            check=True,
        )
        local_commit = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        with pytest.raises(provenance.ProvenanceError, match="not contained in a fetched"):
            provenance.verify_checkout(checkout, local_commit)

    def test_model_record_binds_commit_config_map_and_pickle(self, tmp_path: Path) -> None:
        provenance = self._module()
        contract = _write_gnomix_training_contract(tmp_path / "contract")
        config = tmp_path / "config.yaml"
        genetic_map = tmp_path / "chr1.map"
        model = tmp_path / "model_chm_chr1.pkl"
        record = tmp_path / "training_provenance.json"
        config.write_text(_gnomix_config())
        genetic_map.write_text("chr1\t100\t0\n")
        model.write_bytes(b"model-v1")
        commit = "1" * 40

        written = provenance.write_model_record(
            record,
            chromosome="chr1",
            expected_commit=commit,
            config=config,
            genetic_map=genetic_map,
            model=model,
            training_input_manifest=contract["input_manifest"],
            training_split_manifest=contract["split_manifest"],
            expected_training_input_sha256=hashlib.sha256(
                contract["input_manifest"].read_bytes()
            ).hexdigest(),
            expected_training_split_sha256=hashlib.sha256(
                contract["split_manifest"].read_bytes()
            ).hexdigest(),
        )
        assert written["gnomix_git_commit"] == commit
        assert (
            written["effective_config_sha256"] == hashlib.sha256(config.read_bytes()).hexdigest()
        )
        assert written["model_sha256"] == hashlib.sha256(model.read_bytes()).hexdigest()
        assert (
            written["training_input_manifest"]["sha256"]
            == hashlib.sha256(contract["input_manifest"].read_bytes()).hexdigest()
        )
        assert (
            written["training_split_manifest"]["sha256"]
            == hashlib.sha256(contract["split_manifest"].read_bytes()).hexdigest()
        )
        provenance.verify_model_record(
            record,
            chromosome="chr1",
            expected_commit=commit,
            config=config,
            genetic_map=genetic_map,
            model=model,
            training_input_manifest=contract["input_manifest"],
            training_split_manifest=contract["split_manifest"],
        )

        model.write_bytes(b"model-v2")
        with pytest.raises(provenance.ProvenanceError, match="model_sha256 mismatch"):
            provenance.verify_model_record(
                record,
                chromosome="chr1",
                expected_commit=commit,
                config=config,
                genetic_map=genetic_map,
                model=model,
                training_input_manifest=contract["input_manifest"],
                training_split_manifest=contract["split_manifest"],
            )
        model.write_bytes(b"model-v1")

        config.write_text(_gnomix_config(seed=2))
        with pytest.raises(provenance.ProvenanceError, match="effective_config_sha256 mismatch"):
            provenance.verify_model_record(
                record,
                chromosome="chr1",
                expected_commit=commit,
                config=config,
                genetic_map=genetic_map,
                model=model,
                training_input_manifest=contract["input_manifest"],
                training_split_manifest=contract["split_manifest"],
            )
        config.write_text(_gnomix_config())

        genetic_map.write_text("chr1\t200\t0.1\n")
        with pytest.raises(provenance.ProvenanceError, match="genetic_map_sha256 mismatch"):
            provenance.verify_model_record(
                record,
                chromosome="chr1",
                expected_commit=commit,
                config=config,
                genetic_map=genetic_map,
                model=model,
                training_input_manifest=contract["input_manifest"],
                training_split_manifest=contract["split_manifest"],
            )

    def test_aggregate_rejects_mixed_effective_configs(self, tmp_path: Path) -> None:
        provenance = self._module()
        contract = _write_gnomix_training_contract(tmp_path / "contract")
        commit = "2" * 40
        genetic_map = tmp_path / "map"
        genetic_map.write_text("chr1\t100\t0\n")
        records = []
        for chromosome, seed in (("chr1", 1), ("chr2", 2)):
            config = tmp_path / f"{chromosome}.yaml"
            model = tmp_path / f"model_chm_{chromosome}.pkl"
            record = tmp_path / f"{chromosome}.json"
            config.write_text(_gnomix_config(seed=seed))
            model.write_bytes(chromosome.encode())
            provenance.write_model_record(
                record,
                chromosome=chromosome,
                expected_commit=commit,
                config=config,
                genetic_map=genetic_map,
                model=model,
                training_input_manifest=contract["input_manifest"],
                training_split_manifest=contract["split_manifest"],
                expected_training_input_sha256=hashlib.sha256(
                    contract["input_manifest"].read_bytes()
                ).hexdigest(),
                expected_training_split_sha256=hashlib.sha256(
                    contract["split_manifest"].read_bytes()
                ).hexdigest(),
            )
            records.append(record)

        second = json.loads(records[1].read_text())
        second["gnomix_git_commit"] = "9" * 40
        records[1].write_text(json.dumps(second))
        with pytest.raises(provenance.ProvenanceError, match="mixed or unexpected"):
            provenance.aggregate_records(
                records,
                commit,
                tmp_path / "aggregate.json",
                training_input_manifest=contract["input_manifest"],
                training_split_manifest=contract["split_manifest"],
            )
        second["gnomix_git_commit"] = commit
        records[1].write_text(json.dumps(second))

        with pytest.raises(provenance.ProvenanceError, match="mixed effective Gnomix"):
            provenance.aggregate_records(
                records,
                commit,
                tmp_path / "aggregate.json",
                training_input_manifest=contract["input_manifest"],
                training_split_manifest=contract["split_manifest"],
            )

    def test_aggregate_publishes_common_generation_and_per_model_hashes(
        self, tmp_path: Path
    ) -> None:
        provenance = self._module()
        contract = _write_gnomix_training_contract(tmp_path / "contract")
        commit = "3" * 40
        config = tmp_path / "config.yaml"
        config.write_text(_gnomix_config(seed=7))
        records = []
        expected_hashes = {}
        for chromosome in ("chr1", "chr2"):
            genetic_map = tmp_path / f"{chromosome}.map"
            model = tmp_path / f"model_chm_{chromosome}.pkl"
            record = tmp_path / f"{chromosome}.json"
            genetic_map.write_text(f"{chromosome}\t100\t0\n")
            model.write_bytes(chromosome.encode())
            provenance.write_model_record(
                record,
                chromosome=chromosome,
                expected_commit=commit,
                config=config,
                genetic_map=genetic_map,
                model=model,
                training_input_manifest=contract["input_manifest"],
                training_split_manifest=contract["split_manifest"],
                expected_training_input_sha256=hashlib.sha256(
                    contract["input_manifest"].read_bytes()
                ).hexdigest(),
                expected_training_split_sha256=hashlib.sha256(
                    contract["split_manifest"].read_bytes()
                ).hexdigest(),
            )
            records.append(record)
            expected_hashes[chromosome] = {
                "genetic_map_sha256": hashlib.sha256(genetic_map.read_bytes()).hexdigest(),
                "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
            }

        output = tmp_path / "aggregate.json"
        manifest = provenance.aggregate_records(
            records,
            commit,
            output,
            training_input_manifest=contract["input_manifest"],
            training_split_manifest=contract["split_manifest"],
        )
        assert json.loads(output.read_text()) == manifest
        assert manifest["gnomix_git_commit"] == commit
        assert (
            manifest["effective_config_sha256"] == hashlib.sha256(config.read_bytes()).hexdigest()
        )
        assert [model["chromosome"] for model in manifest["models"]] == ["chr1", "chr2"]
        for model_entry in manifest["models"]:
            assert (
                model_entry["genetic_map_sha256"]
                == expected_hashes[model_entry["chromosome"]]["genetic_map_sha256"]
            )
            assert (
                model_entry["model_sha256"]
                == expected_hashes[model_entry["chromosome"]]["model_sha256"]
            )
        assert manifest["models"][0]["provenance_file"] == (
            "metadata/gnomix_model_chr1.provenance.json"
        )
        with pytest.raises(provenance.ProvenanceError, match="exactly chr1 through chr22"):
            provenance.load_aggregate_manifest(output, require_complete=True)

    def test_aggregate_rejects_mixed_and_substituted_manifest_generations(
        self, tmp_path: Path
    ) -> None:
        provenance = self._module()
        contract = _write_gnomix_training_contract(tmp_path / "contract")
        alternate_input = tmp_path / "alternate-input.json"
        alternate_payload = json.loads(contract["input_manifest"].read_text())
        alternate_payload["reference_panel"]["name"] += " alternate"
        alternate_input.write_text(
            json.dumps(alternate_payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        )
        alternate_input_sha = hashlib.sha256(alternate_input.read_bytes()).hexdigest()
        alternate_split = tmp_path / "alternate-split.json"
        alternate_split_payload = json.loads(contract["split_manifest"].read_text())
        alternate_split_payload["training_input_manifest"]["sha256"] = alternate_input_sha
        alternate_split.write_text(
            json.dumps(
                alternate_split_payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        )

        config = tmp_path / "config.yaml"
        config.write_text(_gnomix_config())
        genetic_map = tmp_path / "map"
        genetic_map.write_text("chr1\t100\t0\n")
        commit = "c" * 40
        records = []
        for chromosome, input_manifest, split_manifest in (
            ("chr1", contract["input_manifest"], contract["split_manifest"]),
            ("chr2", alternate_input, alternate_split),
        ):
            model = tmp_path / f"model_chm_{chromosome}.pkl"
            record = tmp_path / f"{chromosome}.json"
            model.write_bytes(chromosome.encode())
            provenance.write_model_record(
                record,
                chromosome=chromosome,
                expected_commit=commit,
                config=config,
                genetic_map=genetic_map,
                model=model,
                training_input_manifest=input_manifest,
                training_split_manifest=split_manifest,
                expected_training_input_sha256=hashlib.sha256(
                    input_manifest.read_bytes()
                ).hexdigest(),
                expected_training_split_sha256=hashlib.sha256(
                    split_manifest.read_bytes()
                ).hexdigest(),
            )
            records.append(record)

        with pytest.raises(provenance.ProvenanceError, match="mixed.*training-input"):
            provenance.aggregate_records(
                records,
                commit,
                tmp_path / "mixed.json",
                training_input_manifest=contract["input_manifest"],
                training_split_manifest=contract["split_manifest"],
            )

        aggregate = tmp_path / "aggregate.json"
        provenance.aggregate_records(
            [records[0]],
            commit,
            aggregate,
            training_input_manifest=contract["input_manifest"],
            training_split_manifest=contract["split_manifest"],
        )
        with pytest.raises(provenance.ProvenanceError, match="do not match aggregate"):
            provenance.verify_aggregate_manifest(
                aggregate,
                training_input_manifest=alternate_input,
                training_split_manifest=alternate_split,
            )

    def test_model_record_rejects_extra_or_malformed_fields(self, tmp_path: Path) -> None:
        provenance = self._module()
        record = tmp_path / "record.json"
        valid = {
            "schema_version": 2,
            "chromosome": "chr1",
            "gnomix_repository": provenance.GNOMIX_REPOSITORY,
            "gnomix_git_commit": "4" * 40,
            "gnomix_checkout_clean": True,
            "simulation_run": True,
            "effective_config_sha256": "5" * 64,
            "genetic_map_sha256": "6" * 64,
            "model_filename": "model_chm_chr1.pkl",
            "model_sha256": "7" * 64,
            "training_input_manifest": {"schema_version": 1, "sha256": "8" * 64},
            "training_split_manifest": {"schema_version": 1, "sha256": "9" * 64},
        }
        record.write_text(json.dumps(valid | {"unexpected": True}))
        with pytest.raises(provenance.ProvenanceError, match="unexpected.*fields"):
            provenance.load_model_record(record)

        invalid_cases = [
            ({"schema_version": 1}, "unsupported.*schema"),
            ({"schema_version": 2.0}, "unsupported.*schema"),
            ({"chromosome": "chr23"}, "invalid autosome"),
            ({"gnomix_git_commit": "short"}, "full 40-character"),
            ({"gnomix_checkout_clean": False}, "does not attest a clean checkout"),
            ({"simulation_run": False}, "does not attest fresh simulation"),
            ({"effective_config_sha256": "not-a-sha"}, "invalid or missing"),
            ({"model_filename": "../model.pkl"}, "invalid model_filename"),
        ]
        for replacement, message in invalid_cases:
            record.write_text(json.dumps(valid | replacement))
            with pytest.raises(provenance.ProvenanceError, match=message):
                provenance.load_model_record(record)

        duplicate = json.dumps(valid).replace(
            '"schema_version": 2',
            '"schema_version": 999, "schema_version": 2',
            1,
        )
        record.write_text(duplicate)
        with pytest.raises(provenance.ProvenanceError, match="duplicate key 'schema_version'"):
            provenance.load_model_record(record)

    def test_aggregate_rejects_ambiguous_json_and_float_schema(self, tmp_path: Path) -> None:
        provenance = self._module()
        aggregate = tmp_path / "aggregate.json"
        valid = {
            "schema_version": 2,
            "gnomix_repository": provenance.GNOMIX_REPOSITORY,
            "gnomix_git_commit": "a" * 40,
            "gnomix_checkout_clean": True,
            "simulation_run": True,
            "effective_config_sha256": "b" * 64,
            "training_input_manifest": {"schema_version": 1, "sha256": "c" * 64},
            "training_split_manifest": {"schema_version": 1, "sha256": "d" * 64},
            "models": [
                {
                    "chromosome": "chr1",
                    "model_filename": "model_chm_chr1.pkl",
                    "model_sha256": "e" * 64,
                    "genetic_map_sha256": "f" * 64,
                    "provenance_file": "metadata/gnomix_model_chr1.provenance.json",
                }
            ],
        }

        aggregate.write_text(json.dumps(valid | {"schema_version": 2.0}))
        with pytest.raises(provenance.ProvenanceError, match="unsupported.*schema"):
            provenance.load_aggregate_manifest(aggregate)

        duplicate = json.dumps(valid).replace(
            '"schema_version": 2',
            '"schema_version": 999, "schema_version": 2',
            1,
        )
        aggregate.write_text(duplicate)
        with pytest.raises(provenance.ProvenanceError, match="duplicate key 'schema_version'"):
            provenance.load_aggregate_manifest(aggregate)

    def test_record_publication_is_atomic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provenance = self._module()
        contract = _write_gnomix_training_contract(tmp_path / "contract")
        config = tmp_path / "config.yaml"
        genetic_map = tmp_path / "chr1.map"
        model = tmp_path / "model_chm_chr1.pkl"
        record = tmp_path / "training_provenance.json"
        config.write_text(_gnomix_config())
        genetic_map.write_text("chr1\t100\t0\n")
        model.write_bytes(b"model")
        record.write_text("prior-generation\n")
        original_replace = Path.replace

        def interrupted_replace(path: Path, target: Path) -> Path:
            if target == record:
                raise OSError("simulated publication interruption")
            return original_replace(path, target)

        monkeypatch.setattr(Path, "replace", interrupted_replace)
        with pytest.raises(OSError, match="simulated publication interruption"):
            provenance.write_model_record(
                record,
                chromosome="chr1",
                expected_commit="8" * 40,
                config=config,
                genetic_map=genetic_map,
                model=model,
                training_input_manifest=contract["input_manifest"],
                training_split_manifest=contract["split_manifest"],
                expected_training_input_sha256=hashlib.sha256(
                    contract["input_manifest"].read_bytes()
                ).hexdigest(),
                expected_training_split_sha256=hashlib.sha256(
                    contract["split_manifest"].read_bytes()
                ).hexdigest(),
            )
        assert record.read_text() == "prior-generation\n"
        assert not list(tmp_path.glob(".training_provenance.json.*.tmp"))


class TestPhase05ModelPathCheck:
    """gnomix saves the model NESTED at
    output_chrN/models/model_chm_chrN/model_chm_chrN.pkl, not output_chrN/*.pkl.
    The skip-guard and the success-check must look at the nested path or the task
    exit-1's "MISSING" after a successful train (and on resume it re-trains).
    """

    def test_skip_and_success_check_use_nested_model_path(self) -> None:
        text = (SCRIPTS_DIR / "05_train_gnomix.sh").read_text()
        assert 'model_dir="$out_dir/models/model_chm_chr${chr}"' in text
        assert 'model_pkl="$model_dir/model_chm_chr${chr}.pkl"' in text
        # the broken top-level glob must be gone from the guards
        assert '"$out_dir"/*.pkl' not in text
        assert '"output_chr${chr}"/*.pkl' not in text


class TestPhase05SampleMapNoCpRace:
    """Under the phase-05 SLURM array every chromosome task shares $GNOMIX_DIR, so
    copying the sample_map to a single shared $GNOMIX_DIR/sample_map.txt races on
    the cluster NFS (cp: 'File exists') and, with set -e + the default Requeue=1,
    kills + requeues + re-trains the task (a non-converging loop that strands
    chroms which keep losing the race). gnomix reads the map read-only, so it is
    passed directly from $ADMIX_DIR; the array must also disable requeue.
    """

    def test_no_shared_sample_map_copy(self) -> None:
        text = (SCRIPTS_DIR / "05_train_gnomix.sh").read_text()
        # the racing shared-destination copy must be gone
        assert 'cp "$ADMIX_DIR/sample_map.txt" "$GNOMIX_DIR/sample_map.txt"' not in text

    def test_gnomix_reads_sample_map_directly_from_admix_dir(self) -> None:
        text = (SCRIPTS_DIR / "05_train_gnomix.sh").read_text()
        # gnomix is handed the read-only ADMIX_DIR map, not a per-run shared copy
        assert '"$ADMIX_DIR/sample_map.txt"' in text

    def test_array_sbatch_disables_requeue(self) -> None:
        text = (SCRIPTS_DIR / "slurm" / "05_train_gnomix.sbatch").read_text()
        assert "--no-requeue" in text

    def test_array_sbatch_mem_sized_for_genetic_region_panel(self) -> None:
        # the v2.0.0 genetic_region panel (~3690 founders) needs more than the
        # old 32G default sized for the ~1939-founder single-ancestry panel.
        text = (SCRIPTS_DIR / "slurm" / "05_train_gnomix.sbatch").read_text()
        assert "--mem=64G" in text


class TestPhase07ReexportsGnomixModels:
    """The shipped bundle ships base_coefs.npz + smoother.json + metadata.npz per
    chromosome (what backend/analysis/gnomix_inference.load_gnomix_model loads), not
    gnomix's native .pkl. Phase 07 must re-export, not raw-copy the gnomix output.
    """

    def test_assemble_runs_reexport_not_raw_copy(self) -> None:
        text = (SCRIPTS_DIR / "07_assemble_bundle.sh").read_text()
        assert "07b_reexport_gnomix_models.py" in text
        # the old raw copy of the gnomix output dir must be gone
        assert 'cp -r "$GNOMIX_DIR/output_chr${chr}/." "gnomix_models/chr${chr}/"' not in text

    def test_reexporter_emits_runtime_trio(self) -> None:
        text = (SCRIPTS_DIR / "07b_reexport_gnomix_models.py").read_text()
        for artifact in ("base_coefs.npz", "smoother.json", "metadata.npz"):
            assert artifact in text
        # metadata keys the runtime reads must be written
        for key in ("snp_pos", "snp_ref", "snp_alt", "population_order"):
            assert key in text


class TestMendelianTruthPhasing06b:
    """06b truth-phases trio children by Mendelian inheritance. pysam's
    VariantRecordSamples cannot delete samples from a record, so 06b must NOT try
    to strip parents (06d selects the child by name). Lock in the resolve_phase
    logic and the absence of the unsupported deletion.
    """

    def _mod(self):
        pytest.importorskip("pysam")
        pytest.importorskip("pandas")
        return _load_module("06b_mendelian_phasing.py", "mendelian_06b")

    @pytest.mark.parametrize(
        "child,father,mother,expected",
        [
            ((0, 1), (0, 0), (0, 1), (0, 1)),  # father hom-ref, mother carries alt
            ((0, 1), (0, 1), (0, 0), (1, 0)),  # mother hom-ref, father carries alt
            ((0, 1), (0, 1), (0, 1), None),  # both het -> ambiguous
            ((0, 0), (0, 1), (0, 1), None),  # child not het -> skip
            ((0, 1), (1, 1), (0, 0), (1, 0)),  # father hom-alt, mother hom-ref
            ((0, 1), (0, 0), (1, 1), (0, 1)),  # father hom-ref, mother hom-alt
        ],
    )
    def test_resolve_phase(self, child, father, mother, expected) -> None:
        assert self._mod().resolve_phase(child, father, mother) == expected

    def test_no_unsupported_sample_deletion(self) -> None:
        text = (SCRIPTS_DIR / "06b_mendelian_phasing.py").read_text()
        # pysam VariantRecordSamples does not support item deletion
        assert "del new_rec.samples" not in text


class TestPhase06cParallel:
    """06c fans out leave-one-out Beagle phasing over (child,chrom) via xargs -P,
    delegating each pair to the 06c_beagle_one.sh worker. Lock in the fan-out
    wiring, the per-run thread cap, the SLURM cpu bump, and the completeness-checked
    skip guards (a bare -s test would reuse a truncated file from a killed run).
    """

    def test_fanout_uses_xargs_over_worker(self) -> None:
        text = (SCRIPTS_DIR / "06c_beagle_loo_phasing.sh").read_text()
        assert "xargs -P" in text
        assert "06c_beagle_one.sh" in text
        assert "BEAGLE_PARALLEL" in text

    def test_worker_caps_beagle_threads(self) -> None:
        text = (SCRIPTS_DIR / "06c_beagle_one.sh").read_text()
        assert "nthreads=" in text
        assert "BEAGLE_NTHREADS" in text

    def test_skip_guards_check_completeness_not_just_size(self) -> None:
        text = (SCRIPTS_DIR / "06c_beagle_one.sh").read_text()
        # Beagle output reuse must verify BGZF integrity (not a bare -s), so a
        # truncated file left by a killed/scancel'd worker is regenerated rather
        # than skipped and shipped to 06d as corrupt phasing.
        assert "bgzip -t" in text
        # The ref panel reuse must additionally require its index (.tbi, written
        # last by bcftools index -t) as a completion marker.
        assert ".tbi" in text

    def test_env_defines_parallel_and_threads(self) -> None:
        text = (SCRIPTS_DIR / "env.sh").read_text()
        assert "BEAGLE_NTHREADS" in text
        assert "BEAGLE_PARALLEL" in text
        assert "SLURM_CPUS_PER_TASK" in text  # auto-scales concurrency to the alloc

    def test_finish_sbatch_sized_for_parallel_beagle(self) -> None:
        text = (SCRIPTS_DIR / "slurm" / "finish.sbatch").read_text()
        assert "--cpus-per-task=64" in text


class TestPhase07Metadata:
    """07_write_metadata pulls the validation metrics into the bundle metadata.json.
    Two prior bugs left it incomplete: it read the wrong accuracy field (so
    accuracy_per_window_mean was null), and counted gnomix .pkl files (which the
    npz/json re-export no longer ships, so window_count was 0). Lock in the fixes.
    """

    def test_reads_correct_accuracy_field(self) -> None:
        text = (SCRIPTS_DIR / "07_write_metadata.py").read_text()
        # the field 06e actually writes, not the old wrong field that returned null
        assert 'accuracy = json.loads(lai_report.read_text()).get("mean_val_accuracy")' in text
        assert 'accuracy = json.loads(lai_report.read_text()).get("overall_accuracy")' not in text

    def test_window_count_from_npz_not_pkl(self) -> None:
        text = (SCRIPTS_DIR / "07_write_metadata.py").read_text()
        # window_count must sum W from the re-exported metadata.npz, not glob *.pkl
        assert "metadata.npz" in text
        assert 'glob("gnomix_models/*/*.pkl")' not in text

    def test_metadata_uses_one_authenticated_provenance_snapshot(self) -> None:
        text = (SCRIPTS_DIR / "07_write_metadata.py").read_text()
        assert "verify_aggregate_snapshot" in text
        assert "verify_published_model_records" in text
        assert "_sha256(args.gnomix_provenance)" not in text

    def test_assemble_cp_is_force(self) -> None:
        # Phase 07 re-run must overwrite the read-only files copied from read-only
        # sources on a prior run; plain cp fails "Permission denied" on re-run.
        text = (SCRIPTS_DIR / "07_assemble_bundle.sh").read_text()
        assert "cp -f " in text
        assert re.search(r'\bcp "\$', text) is None  # every cp is forced

    def test_metadata_records_heldout_superpop_gate(self) -> None:
        text = (SCRIPTS_DIR / "07_write_metadata.py").read_text()
        assert "heldout_superpop_accuracy_report.json" in text
        assert "heldout_superpop_accuracy" in text

    def test_metadata_publishes_validated_gnomix_generation(self, tmp_path: Path) -> None:
        provenance = _load_module("gnomix_provenance.py", "gnomix_provenance_metadata_test")
        contract = _write_gnomix_training_contract(tmp_path / "contract")
        bundle = tmp_path / "bundle"
        validation = tmp_path / "validation"
        metadata_dir = bundle / "metadata"
        (bundle / "liftover").mkdir(parents=True)
        metadata_dir.mkdir()
        validation.mkdir()
        (bundle / "liftover" / "array_site_mapping.tsv").write_text("rs1\t1\t100\n")
        union_catalog = contract["union_catalog"]

        config = tmp_path / "config.yaml"
        config.write_text(_gnomix_config(seed=42))
        commit = "a" * 40
        input_manifest_path = metadata_dir / "gnomix_training_inputs.json"
        split_manifest_path = metadata_dir / "gnomix_training_splits.json"
        shutil.copyfile(contract["input_manifest"], input_manifest_path)
        shutil.copyfile(contract["split_manifest"], split_manifest_path)
        records = []
        for number in range(1, 23):
            chromosome = f"chr{number}"
            genetic_map = tmp_path / f"{chromosome}.map"
            model = tmp_path / f"model_chm_{chromosome}.pkl"
            record = metadata_dir / f"gnomix_model_{chromosome}.provenance.json"
            genetic_map.write_text(f"{chromosome}\t100\t0\n")
            model.write_bytes(f"model-{chromosome}".encode())
            provenance.write_model_record(
                record,
                chromosome=chromosome,
                expected_commit=commit,
                config=config,
                genetic_map=genetic_map,
                model=model,
                training_input_manifest=input_manifest_path,
                training_split_manifest=split_manifest_path,
                expected_training_input_sha256=hashlib.sha256(
                    input_manifest_path.read_bytes()
                ).hexdigest(),
                expected_training_split_sha256=hashlib.sha256(
                    split_manifest_path.read_bytes()
                ).hexdigest(),
            )
            records.append(record)
        manifest_path = metadata_dir / "gnomix_training_provenance.json"
        provenance.aggregate_records(
            records,
            commit,
            manifest_path,
            training_input_manifest=input_manifest_path,
            training_split_manifest=split_manifest_path,
        )

        command = [
            sys.executable,
            str(SCRIPTS_DIR / "07_write_metadata.py"),
            "--bundle-dir",
            str(bundle),
            "--union-catalog",
            str(union_catalog),
            "--validation-dir",
            str(validation),
            "--git-commit",
            "b" * 40,
            "--build-host",
            "test-host",
            "--build-date",
            "2026-07-13",
            "--bundle-version",
            "v2.0.0",
            "--gnomix-provenance",
            str(manifest_path),
            "--admixture-seed",
            "42",
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        metadata = json.loads((bundle / "metadata.json").read_text())
        assert metadata["gnomix_training"] == {
            "repository": provenance.GNOMIX_REPOSITORY,
            "git_commit": commit,
            "checkout_clean": True,
            "simulation_run": True,
            "effective_config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
            "model_count": 22,
            "manifest": "metadata/gnomix_training_provenance.json",
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "training_input_manifest": {
                "path": "metadata/gnomix_training_inputs.json",
                "schema_version": 1,
                "sha256": hashlib.sha256(input_manifest_path.read_bytes()).hexdigest(),
            },
            "training_split_manifest": {
                "path": "metadata/gnomix_training_splits.json",
                "schema_version": 1,
                "sha256": hashlib.sha256(split_manifest_path.read_bytes()).hexdigest(),
            },
        }
        assert metadata["reference_build"] == "GRCh38"
        assert metadata["reference_panel"] == "gnomAD HGDP+1KG v3.1.2 (phased SHAPEIT5)"
        assert metadata["sample_identifier_policy"] == "public_reference_panel_ids"

        metadata_before = (bundle / "metadata.json").read_bytes()
        (metadata_dir / "gnomix_model_chr22.provenance.json").unlink()
        missing_record = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        assert missing_record.returncode == 1
        assert "gnomix_model_chr22.provenance.json" in missing_record.stderr
        assert "missing or empty" in missing_record.stderr
        assert (bundle / "metadata.json").read_bytes() == metadata_before


class TestGnomixPandasAppendShim:
    """gnomix's src/laidataset.py calls the pandas<2 ``DataFrame.append`` (removed
    in pandas 2.0) in the small-population ``include_all`` path (fires for tiny
    pops like EUR=3). The shared ``gnomix`` env runs pandas>=2, so gnomix_launcher
    restores ``append`` in-process before running gnomix. Lock in that behaviour and
    the phase-05 wiring so the env-version regression cannot silently return.
    """

    def _mod(self):
        return _load_module("gnomix_launcher.py", "gnomix_launcher")

    def test_df_append_helper_concats_rows(self) -> None:
        import pandas as pd

        mod = self._mod()
        df = pd.DataFrame({"a": [1, 2]})
        out = mod._df_append(df, pd.DataFrame({"a": [3]}))
        assert list(out["a"]) == [1, 2, 3]
        # gnomix never uses it, but the pandas<2 list form must also work.
        out2 = mod._df_append(df, [pd.DataFrame({"a": [3]}), pd.DataFrame({"a": [4]})])
        assert list(out2["a"]) == [1, 2, 3, 4]

    def test_series_append_helper_concats(self) -> None:
        import pandas as pd

        mod = self._mod()
        s = pd.Series([1, 2])
        assert list(mod._series_append(s, pd.Series([3]))) == [1, 2, 3]

    def test_install_shim_yields_working_append(self) -> None:
        import pandas as pd

        mod = self._mod()
        had_df = hasattr(pd.DataFrame, "append")
        had_s = hasattr(pd.Series, "append")
        orig_df = pd.DataFrame.append if had_df else None
        orig_s = pd.Series.append if had_s else None
        try:
            mod.install_pandas_append_shim()
            assert hasattr(pd.DataFrame, "append")
            df = pd.DataFrame({"a": [1]})
            assert list(df.append(pd.DataFrame({"a": [2]}))["a"]) == [1, 2]
        finally:
            # never leak a patched/removed attr into the rest of the suite
            if had_df:
                pd.DataFrame.append = orig_df
            elif hasattr(pd.DataFrame, "append"):
                del pd.DataFrame.append
            if had_s:
                pd.Series.append = orig_s
            elif hasattr(pd.Series, "append"):
                del pd.Series.append

    def test_install_shim_does_not_overwrite_existing_append(self) -> None:
        import pandas as pd

        mod = self._mod()

        def sentinel(*_a, **_k):
            return "ORIGINAL"

        orig = pd.DataFrame.append if hasattr(pd.DataFrame, "append") else None
        try:
            pd.DataFrame.append = sentinel
            mod.install_pandas_append_shim()
            assert pd.DataFrame.append is sentinel  # no-op when append already present
        finally:
            if orig is not None:
                pd.DataFrame.append = orig
            else:
                del pd.DataFrame.append

    def test_phase05_routes_gnomix_through_launcher(self) -> None:
        text = (SCRIPTS_DIR / "05_train_gnomix.sh").read_text()
        # phase 05 must invoke gnomix THROUGH the launcher, passing the real
        # gnomix.py entrypoint as the launcher's first argument.
        assert "gnomix_launcher.py" in text
        assert re.search(r"gnomix_launcher\.py\b.*\n.*gnomix\.py", text) or (
            "gnomix_launcher.py" in text and "$GNOMIX_DIR_INSTALL/gnomix.py" in text
        )


class TestLaiAccuracyParser:
    """06e parses gnomix's `Estimated val accuracy: NN.NN%` (the proven v1.1
    LAI-accuracy source), so lock in the real log-line format.
    """

    def _mod(self):
        return _load_module("06e_lai_accuracy.py", "lai_accuracy_06e")

    @pytest.mark.parametrize(
        "line,expected",
        [
            ("Estimated val accuracy: 86.88%", 0.8688),
            ("Estimated val accuracy: 85.7%", 0.857),  # gnomix drops trailing zero
            ("Estimated val accuracy: 89.79%", 0.8979),
        ],
    )
    def test_parses_real_gnomix_format(self, line: str, expected: float) -> None:
        acc = self._mod().parse_val_accuracy(f"...\n{line}\nTime: 5m\n")
        assert acc == pytest.approx(expected, abs=1e-6)

    def test_last_match_wins(self) -> None:
        text = "Estimated val accuracy: 70.0%\nretry\nEstimated val accuracy: 88.5%\n"
        assert self._mod().parse_val_accuracy(text) == pytest.approx(0.885)

    def test_no_match_returns_none(self) -> None:
        assert self._mod().parse_val_accuracy("no accuracy here\n") is None

    def test_failing_report_exits_nonzero(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "gnomix_train_chr1.log").write_text("Estimated val accuracy: 50.0%\n")
        report_path = tmp_path / "lai_accuracy_report.json"

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "06e_lai_accuracy.py"),
                "--log-dir",
                str(log_dir),
                "--chroms",
                "1 2",
                "--out-report",
                str(report_path),
                "--min-accuracy",
                "0.88",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 1
        assert "LAI ACCURACY GATE FAILED" in result.stdout
        report = json.loads(report_path.read_text())
        assert report["passes"] is False
        assert report["missing_chroms"] == ["2"]
        assert report["mean_val_accuracy"] == pytest.approx(0.5)


class TestPhase06WiresLogParser:
    """06_validate.sh must drive 06e off the gnomix logs (--log-dir/--chroms),
    not the removed inference-glob contract (--gnomix-dir/--single-ancestry).
    """

    def test_06e_called_with_log_dir(self) -> None:
        text = (SCRIPTS_DIR / "06_validate.sh").read_text()
        assert "06e_lai_accuracy.py" in text
        assert "--log-dir" in text and "--chroms" in text
        # the dead inference-glob flags must be gone
        assert "--gnomix-dir" not in text


class TestHeldoutSuperpopGate:
    """Held-out production-path LAI validation is mandatory and ordered.

    The hold-out must be selected before phase 05 trains Gnomix, while the
    production inference check must run after phase 07 has assembled the runtime
    bundle layout but before metadata/tarball publication.
    """

    def test_phase04_selects_holdout_before_training(self) -> None:
        text = (SCRIPTS_DIR / "04_admixture_filter.sh").read_text()
        assert "06f_select_heldout.py" in text
        assert '--out-heldout "$VALIDATION_DIR/held_out_validation.tsv"' in text
        assert '--out-training "$ADMIX_DIR/sample_map.txt"' in text
        assert '--out-full-backup "$ADMIX_DIR/sample_map.full.txt"' in text

    def test_phase07_runs_heldout_gate_before_metadata_and_tarball(self) -> None:
        text = (SCRIPTS_DIR / "07_assemble_bundle.sh").read_text()
        extract_idx = text.index("extract_heldout_fixtures.py")
        gate_idx = text.index("06f_heldout_superpop_accuracy.py")
        metadata_idx = text.index("07_write_metadata.py")
        tarball_idx = text.index("tar -czf")
        assert extract_idx < gate_idx < metadata_idx < tarball_idx
        assert 'YELIZTLI_LAI_BUNDLE_PATH="$BUNDLE_DIR"' in text
        assert '"$VALIDATION_DIR/heldout_superpop_accuracy_report.json"' in text

    def test_extract_heldout_fixtures_is_parametrized(self) -> None:
        text = (SCRIPTS_DIR / "extract_heldout_fixtures.py").read_text()
        assert "Path.home()" not in text
        for flag in ("--panel-dir", "--validation-dir", "--site-map"):
            assert flag in text

    def test_heldout_accuracy_script_is_a_hard_gate(self) -> None:
        text = (SCRIPTS_DIR / "06f_heldout_superpop_accuracy.py").read_text()
        assert "HELDOUT_MIN_REGION_ACCURACY" in text
        assert "HELDOUT_MIN_EUR_ACCURACY" in text
        assert "read_accuracy_threshold" in text
        assert "must be between 0.0 and 1.0" in text
        assert "HELD-OUT SUPERPOPULATION GATE FAILED" in text
        assert "raise SystemExit(1)" in text

    def test_extract_heldout_labels_have_clear_format_error(self) -> None:
        text = (SCRIPTS_DIR / "extract_heldout_fixtures.py").read_text()
        assert "fewer than 2 tab-separated columns" in text
        assert "upstream Phase 04 held-out output may be malformed" in text


class TestTrioIdentification:
    """06a builds trios from the 1000G pedigree ∩ the panel (v1.1 method), since
    the gnomAD meta has no paternal/maternal-id columns.
    """

    def _run(self, tmp_path, ped_rows, panel, meta_rows):
        ped = tmp_path / "g1k.ped"
        ped.write_text(
            "Family ID\tIndividual ID\tPaternal ID\tMaternal ID\tGender\tPopulation\n"
            + "".join(ped_rows)
        )
        (tmp_path / "panel.txt").write_text("\n".join(panel) + "\n")
        meta = tmp_path / "meta.tsv"
        meta.write_text(
            "s\thgdp_tgp_meta.Genetic.region\thgdp_tgp_meta.Population\n" + "".join(meta_rows)
        )
        out_ped = tmp_path / "trio_pedigree.tsv"
        out_children = tmp_path / "trio_children.txt"
        subprocess.run(
            [
                "python",
                str(SCRIPTS_DIR / "06a_identify_trios.py"),
                "--ped",
                str(ped),
                "--panel-samples",
                str(tmp_path / "panel.txt"),
                "--meta",
                str(meta),
                "--out-trios",
                str(out_children),
                "--out-pedigree",
                str(out_ped),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return out_ped.read_text(), out_children.read_text()

    def test_complete_trio_kept_incomplete_dropped(self, tmp_path) -> None:
        ped_rows = [
            # complete trio: child HG1 + both parents all in panel
            "F1\tHG1\tHG2\tHG3\t1\tACB\n",
            "F1\tHG2\t0\t0\t1\tACB\n",
            "F1\tHG3\t0\t0\t2\tACB\n",
            # child whose father is NOT in the panel -> dropped
            "F2\tHG4\tHG9\tHG5\t1\tCEU\n",
            "F2\tHG5\t0\t0\t2\tCEU\n",
        ]
        panel = ["HG1", "HG2", "HG3", "HG4", "HG5"]  # HG9 (father of HG4) absent
        meta_rows = ["HG1\tAFR\tACB\n", "HG4\tEUR\tCEU\n"]
        ped_text, children = self._run(tmp_path, ped_rows, panel, meta_rows)
        assert "child\tfather\tmother\tpopulation\tregion" in ped_text
        assert "HG1\tHG2\tHG3\tACB\tAFR" in ped_text
        assert "HG4" not in ped_text  # incomplete trio dropped
        assert children.strip() == "HG1"


class TestSlurmRebuild:
    """SLURM DAG: phase01 -> prep(02-04) -> gnomix array(05) -> finish(06-07),
    with phase 05 running gnomix in its own conda env.
    """

    SLURM_DIR = SCRIPTS_DIR / "slurm"

    @pytest.mark.parametrize(
        "name",
        ["01_download_panel.sbatch", "prep.sbatch", "05_train_gnomix.sbatch", "finish.sbatch"],
    )
    def test_sbatch_present_and_bash_n(self, name: str) -> None:
        path = self.SLURM_DIR / name
        assert path.is_file(), f"{path} missing"
        r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr

    def test_orchestrator_chains_the_dag(self) -> None:
        path = SCRIPTS_DIR / "run_rebuild_slurm.sh"
        r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr
        text = path.read_text()
        for f in (
            "01_download_panel.sbatch",
            "prep.sbatch",
            "05_train_gnomix.sbatch",
            "finish.sbatch",
        ):
            assert f in text
        assert "--dependency=" in text and "afterok" in text  # chained
        assert "--array=" in text  # phase 05 is an array

    def test_phase01_sbatch_runs_phase01_only(self) -> None:
        text = (self.SLURM_DIR / "01_download_panel.sbatch").read_text()
        assert 'bash "$SCRIPTS_DIR/run_rebuild.sh" 01' in text

    def test_clean_workdir_dag_submits_phase01_before_prep(self, tmp_path: Path) -> None:
        workdir = tmp_path / "clean-workdir"
        install_dir = tmp_path / "gnomix"
        install_dir.mkdir()
        (install_dir / "gnomix.py").write_text("# fixture\n")
        expected_commit = TestPhase01GnomixMaps._init_git_checkout(install_dir)

        union_catalog = tmp_path / "union.tsv"
        pedigree = tmp_path / "pedigree.ped"
        union_catalog.write_text("rs1\t1\t100\n")
        pedigree.write_text("fixture\n")

        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        sbatch_log = tmp_path / "sbatch.log"
        sbatch = stub_dir / "sbatch"
        sbatch.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "job_id=1\n"
            'if [ -f "$STUB_SBATCH_LOG" ]; then\n'
            '  job_id=$(( $(wc -l < "$STUB_SBATCH_LOG") + 1 ))\n'
            "fi\n"
            'printf \'%s\\n\' "$*" >> "$STUB_SBATCH_LOG"\n'
            "printf '%s\\n' \"$job_id\"\n"
        )
        sbatch.chmod(0o755)

        env = os.environ.copy()
        for variable in (
            "RAW_DIR",
            "LOG_DIR",
            "SITES_DIR",
            "LIFTOVER_DIR",
            "PANEL_DIR",
            "ADMIX_DIR",
            "GNOMIX_DIR",
            "VALIDATION_DIR",
            "BUNDLE_DIR",
        ):
            env.pop(variable, None)
        env.update(
            {
                "WORKDIR": str(workdir),
                "UNION_CATALOG_TSV": str(union_catalog),
                "G1K_PED": str(pedigree),
                "GNOMIX_DIR_INSTALL": str(install_dir),
                "GNOMIX_EXPECTED_COMMIT": expected_commit,
                "SLURM_PARTITION": "gpu",
                "GNOMIX_CPUS": "8",
                "GNOMIX_ARRAY": "1-22",
                "PATH": f"{stub_dir}{os.pathsep}{env['PATH']}",
                "STUB_SBATCH_LOG": str(sbatch_log),
            }
        )

        result = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "run_rebuild_slurm.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

        assert result.returncode == 0, result.stderr
        calls = sbatch_log.read_text().splitlines()
        assert [Path(call.split()[-1]).name for call in calls] == [
            "01_download_panel.sbatch",
            "prep.sbatch",
            "05_train_gnomix.sbatch",
            "finish.sbatch",
        ]
        assert "--dependency=" not in calls[0]
        assert "--dependency=afterok:1" in calls[1]
        assert "--dependency=afterok:2" in calls[2]
        assert "--dependency=afterok:3" in calls[3]
        assert "--array=1-22" in calls[2]
        assert "squeue -j 1,2,3,4" in result.stdout

    def test_phase05_array_is_per_chromosome_and_caps_cores(self) -> None:
        text = (self.SLURM_DIR / "05_train_gnomix.sbatch").read_text()
        assert "--array=1-22" in text
        assert "SLURM_ARRAY_TASK_ID" in text  # one chromosome per task
        assert "n_cores" in text  # caps gnomix cores per task
        assert "SLURM_ARRAY_JOB_ID" in text  # overlapping submissions get unique configs
        assert 'cfg_tmp="${cfg}.tmp.$$"' in text
        assert 'mv -f "$cfg_tmp" "$cfg"' in text
        assert "expected exactly one n_cores entry" in text

    def test_phase05_runs_in_gnomix_env(self) -> None:
        text = (SCRIPTS_DIR / "05_train_gnomix.sh").read_text()
        assert "conda run -n" in text and "GNOMIX_ENV" in text

    def test_env_defines_gnomix_env_and_config(self) -> None:
        text = (SCRIPTS_DIR / "env.sh").read_text()
        assert "GNOMIX_ENV:=gnomix" in text
        assert "GNOMIX_CONFIG:=" in text
        assert "GNOMIX_EXPECTED_COMMIT:=" in text

    def test_slurm_orchestrator_rejects_revision_before_submission(self, tmp_path: Path) -> None:
        workdir = tmp_path / "work"
        install_dir = tmp_path / "gnomix"
        install_dir.mkdir()
        (install_dir / "gnomix.py").write_text("# fixture\n")
        union_catalog = tmp_path / "union.tsv"
        pedigree = tmp_path / "pedigree.ped"
        union_catalog.write_text("rs1\t1\t100\n")
        pedigree.write_text("fixture\n")

        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        sbatch_called = tmp_path / "sbatch-called"
        sbatch = stub_dir / "sbatch"
        sbatch.write_text("#!/bin/sh\nprintf 'called\\n' > \"$STUB_SBATCH_CALLED\"\n")
        sbatch.chmod(0o755)

        env = os.environ.copy()
        for variable in (
            "RAW_DIR",
            "LOG_DIR",
            "SITES_DIR",
            "LIFTOVER_DIR",
            "PANEL_DIR",
            "ADMIX_DIR",
            "GNOMIX_DIR",
            "VALIDATION_DIR",
            "BUNDLE_DIR",
        ):
            env.pop(variable, None)
        env.update(
            {
                "WORKDIR": str(workdir),
                "UNION_CATALOG_TSV": str(union_catalog),
                "G1K_PED": str(pedigree),
                "GNOMIX_DIR_INSTALL": str(install_dir),
                "GNOMIX_EXPECTED_COMMIT": "",
                "PATH": f"{stub_dir}{os.pathsep}{env['PATH']}",
                "STUB_SBATCH_CALLED": str(sbatch_called),
            }
        )

        result = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "run_rebuild_slurm.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

        assert result.returncode == 1
        assert "must be an explicitly selected full 40-character" in result.stderr
        assert not sbatch_called.exists()


class TestRunbook:
    def test_runbook_exists(self) -> None:
        assert RUNBOOK.is_file(), f"runbook missing at {RUNBOOK}"

    def test_runbook_documents_rsync_flow(self) -> None:
        text = RUNBOOK.read_text()
        # Plan §6.3 step 1 mandates an rsync section.
        assert "rsync" in text.lower()
        assert "scripts/lai_bundle_v2" in text
        assert "LAI_BUILD_HOST" in text
        assert "${LAI_BUILD_HOST}:${LAI_WORKDIR%/}/scripts/" in text

    def test_runbook_calls_out_v2_paths(self) -> None:
        text = RUNBOOK.read_text()
        # Both v1.1 (reference) and v2.0.0 working dirs must be operator-supplied
        # so the repo does not leak a private build-host path.
        assert "$LAI_V1_WORKDIR" in text
        assert "$LAI_WORKDIR" in text
        assert not _PRIVATE_SHARED_ROOT.search(text)

    def test_runbook_lists_bio_validator_targets(self) -> None:
        text = RUNBOOK.read_text()
        # Plan §6.4 final paragraph + Plan §12.2 Validation gates.
        assert "0.88" in text  # mean per-window LAI accuracy
        assert "0.0566" in text  # phasing switch error baseline

    def test_runbook_orchestrator_invocation_documented(self) -> None:
        text = RUNBOOK.read_text()
        assert "bash scripts/run_rebuild.sh" in text
        assert "UNION_CATALOG_TSV=" in text
        assert text.count('export GNOMIX_EXPECTED_COMMIT="$GNOMIX_SHA"') >= 4
        assert "metadata/gnomix_training_provenance.json" in text
        assert "effective-config SHA-256" in text
        assert "10.1101/2021.09.19.460980" in text

    def test_runbook_slurm_plan_starts_with_phase01(self) -> None:
        slurm_section = RUNBOOK.read_text().split("### 6a.", 1)[1].split("## 7.", 1)[0]
        assert "4-job SLURM DAG" in slurm_section
        assert "phase01 (01)" in slurm_section
        assert "prep    (02 03 04)" in slurm_section
        assert "gnomix  (05 array)" in slurm_section
        assert "finish  (06 07)" in slurm_section
        assert "squeue -j N,N+1,N+2,N+3" in slurm_section

    def test_slurm_docs_require_a_shared_workdir(self) -> None:
        runbook = RUNBOOK.read_text()
        maintainer_doc = MAINTAINER_DOC.read_text()
        assert "shared cluster storage" in runbook
        assert "same path" in runbook
        assert "shared cluster workdir" in maintainer_doc
        assert "node-local scratch" not in maintainer_doc
