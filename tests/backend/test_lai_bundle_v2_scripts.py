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

import importlib.util
import py_compile
import re
import stat
import subprocess
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
    "04c_filter_single_ancestry.py",
    "06a_identify_trios.py",
    "06b_mendelian_phasing.py",
    "06c_beagle_loo_phasing.sh",
    "06c_beagle_one.sh",
    "06d_phasing_accuracy.py",
    "06e_lai_accuracy.py",
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
            "04c_filter_single_ancestry.py",
            "06a_identify_trios.py",
            "06b_mendelian_phasing.py",
            "06d_phasing_accuracy.py",
            "06e_lai_accuracy.py",
            "07_write_metadata.py",
            "gnomix_launcher.py",
            "07b_reexport_gnomix_models.py",
        ],
    )
    def test_py_compile(self, name: str) -> None:
        py_compile.compile(str(SCRIPTS_DIR / name), doraise=True)


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
        assert "mean_val_accuracy" in text  # the field 06e actually writes
        assert "overall_accuracy" not in text  # the wrong field that returned null

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
