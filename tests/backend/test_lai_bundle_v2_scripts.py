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
        training_index = text.index("for chr in")
        assert verify_index < training_index
        assert "01_convert_gnomix_maps.py" in text
        assert "--verify" in text
        assert 'require_file "$RAW_DIR/genetic_maps_gnomix/provenance.json"' in text
        assert "genetic_map.sha256" in text
        assert 'recorded_map_sha" = "$current_map_sha' in text

    def test_phase05_retrains_model_when_map_generation_changes(self, tmp_path: Path) -> None:
        workdir = tmp_path / "work"
        raw_dir = workdir / "00_raw_downloads"
        source_dir = raw_dir / "genetic_maps_grch38" / "chr_in_chrom_field"
        source = source_dir / "plink.chrchr1.GRCh38.map"
        self._write_source(source, "chr1 . 0 55550\n")
        map_dir = raw_dir / "genetic_maps_gnomix"
        conversion = self._run_converter(source_dir, map_dir, "1")
        assert conversion.returncode == 0, conversion.stderr

        admix_dir = workdir / "04_admixture_filtering"
        panel_dir = workdir / "03_subsetted_panels"
        gnomix_dir = workdir / "05_gnomix_training"
        install_dir = tmp_path / "gnomix-install"
        self._write_source(admix_dir / "sample_map.txt", "sample\tEUR\n")
        self._write_source(panel_dir / "ref_panel_chr1.vcf.gz", "panel\n")
        self._write_source(install_dir / "gnomix.py", "# stub\n")
        self._write_source(install_dir / "config.yaml", "seed: 1\n")
        self._write_source(gnomix_dir / "minquery_chr1.vcf.gz", "query\n")
        self._write_source(gnomix_dir / "minquery_chr1.vcf.gz.tbi", "index\n")

        model_dir = gnomix_dir / "output_chr1" / "models" / "model_chm_chr1"
        model_path = model_dir / "model_chm_chr1.pkl"
        marker_path = model_dir / "genetic_map.sha256"
        self._write_source(model_path, "old-model\n")
        first_map_sha = hashlib.sha256((map_dir / "chr1.map").read_bytes()).hexdigest()
        marker_path.write_text(f"{first_map_sha}  chr1.map\n")

        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        conda_called = tmp_path / "conda-called"
        conda_stub = stub_dir / "conda"
        conda_stub.write_text(
            "#!/bin/sh\n"
            "printf 'called\\n' > \"$STUB_CONDA_CALLED\"\n"
            "printf 'new-model\\n' > \"$STUB_MODEL_PATH\"\n"
        )
        conda_stub.chmod(0o755)
        bcftools_stub = stub_dir / "bcftools"
        bcftools_stub.write_text("#!/bin/sh\necho unexpected bcftools call >&2\nexit 97\n")
        bcftools_stub.chmod(0o755)

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
                "PATH": f"{stub_dir}{os.pathsep}{env['PATH']}",
                "STUB_CONDA_CALLED": str(conda_called),
                "STUB_MODEL_PATH": str(model_path),
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
        assert model_path.read_text() == "old-model\n"

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
        assert "genetic-map provenance changed or missing; retraining" in changed.stdout
        assert conda_called.is_file()
        assert model_path.read_text() == "new-model\n"
        assert marker_path.read_text() == f"{changed_map_sha}  chr1.map\n"
        assert not Path(f"{model_path}.stale").exists()

    def test_phase05_missing_bcftools_fails_before_model_state_changes(
        self, tmp_path: Path
    ) -> None:
        workdir = tmp_path / "work"
        model_dir = workdir / "05_gnomix_training" / "output_chr1" / "models" / "model_chm_chr1"
        model_path = model_dir / "model_chm_chr1.pkl"
        marker_path = model_dir / "genetic_map.sha256"
        self._write_source(model_path, "existing-model\n")
        marker_path.write_text("sentinel-binding\n")

        stub_dir = tmp_path / "preflight-bin"
        stub_dir.mkdir()
        for command in ("date", "dirname", "mkdir", "python3", "sha256sum"):
            target = shutil.which(command)
            assert target is not None
            (stub_dir / command).symlink_to(target)
        conda_stub = stub_dir / "conda"
        conda_stub.write_text("#!/bin/sh\nexit 97\n")
        conda_stub.chmod(0o755)

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
        env.update({"WORKDIR": str(workdir), "CHROMS": "1", "PATH": str(stub_dir)})

        result = subprocess.run(
            ["/bin/bash", str(SCRIPTS_DIR / "05_train_gnomix.sh")],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

        assert result.returncode == 1
        assert "missing required command: bcftools" in result.stderr
        assert model_path.read_text() == "existing-model\n"
        assert marker_path.read_text() == "sentinel-binding\n"

    def test_phase07_packages_map_provenance(self) -> None:
        text = (SCRIPTS_DIR / "07_assemble_bundle.sh").read_text()
        assert 'require_file "$RAW_DIR/genetic_maps_gnomix/provenance.json"' in text
        assert (
            'cp -f "$RAW_DIR/genetic_maps_gnomix/provenance.json" '
            "metadata/gnomix_genetic_maps.json"
        ) in text
        assert "trained model does not match current genetic map" in text
        assert '"metadata/gnomix_model_map_chr${chr}.sha256"' in text


class TestPhase05ModelPathCheck:
    """gnomix saves the model NESTED at
    output_chrN/models/model_chm_chrN/model_chm_chrN.pkl, not output_chrN/*.pkl.
    The skip-guard and the success-check must look at the nested path or the task
    exit-1's "MISSING" after a successful train (and on resume it re-trains).
    """

    def test_skip_and_success_check_use_nested_model_path(self) -> None:
        text = (SCRIPTS_DIR / "05_train_gnomix.sh").read_text()
        assert "models/model_chm_chr${chr}/model_chm_chr${chr}.pkl" in text
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
    """SLURM DAG: prep(02-04) -> gnomix array(05, per-chrom) -> finish(06-07),
    and phase 05 runs gnomix in its own conda env.
    """

    SLURM_DIR = SCRIPTS_DIR / "slurm"

    @pytest.mark.parametrize("name", ["prep.sbatch", "05_train_gnomix.sbatch", "finish.sbatch"])
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
        for f in ("prep.sbatch", "05_train_gnomix.sbatch", "finish.sbatch"):
            assert f in text
        assert "--dependency=" in text and "afterok" in text  # chained
        assert "--array=" in text  # phase 05 is an array

    def test_phase05_array_is_per_chromosome_and_caps_cores(self) -> None:
        text = (self.SLURM_DIR / "05_train_gnomix.sbatch").read_text()
        assert "--array=1-22" in text
        assert "SLURM_ARRAY_TASK_ID" in text  # one chromosome per task
        assert "n_cores" in text  # caps gnomix cores per task

    def test_phase05_runs_in_gnomix_env(self) -> None:
        text = (SCRIPTS_DIR / "05_train_gnomix.sh").read_text()
        assert "conda run -n" in text and "GNOMIX_ENV" in text

    def test_env_defines_gnomix_env_and_config(self) -> None:
        text = (SCRIPTS_DIR / "env.sh").read_text()
        assert "GNOMIX_ENV:=gnomix" in text
        assert "GNOMIX_CONFIG:=" in text


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
