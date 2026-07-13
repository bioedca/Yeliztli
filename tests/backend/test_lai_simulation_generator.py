from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = REPO_ROOT / "scripts" / "lai_bundle_v2" / "06g_generate_simulation.py"
VERIFIER_PATH = REPO_ROOT / "scripts" / "lai_bundle_v2" / "06g_verify_simulation.py"
CALIBRATION_PATH = REPO_ROOT / "scripts" / "lai_bundle_v2" / "06g_calibrate_coverage.py"
AUTOSOMES = tuple(str(chrom) for chrom in range(1, 23))
POPULATION_ORDER = ("AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE")


def _load_script(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


generator = _load_script("lai_simulation_generator_tests", GENERATOR_PATH)
verifier = _load_script("lai_simulation_verifier_generator_tests", VERIFIER_PATH)
calibration = _load_script("lai_coverage_calibration_generator_tests", CALIBRATION_PATH)


@pytest.fixture(autouse=True)
def _allow_in_development_generator_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """The verifier path still authenticates source bytes; bypass only dirty-worktree status."""
    monkeypatch.setattr(
        generator,
        "_verify_repository_generator_script",
        lambda _path, _repo_root: None,
    )


def _git_revision() -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_artifact(output_dir: Path, raw_relative: object) -> Path:
    assert isinstance(raw_relative, str)
    relative = PurePosixPath(raw_relative)
    assert not relative.is_absolute()
    assert all(part not in {"", ".", ".."} for part in relative.parts)
    return output_dir.joinpath(*relative.parts)


@dataclass(slots=True)
class TinyGenerationCase:
    root: Path
    design: Path
    donor_metadata: Path
    relationships: Path
    environment_lock: Path
    metadata: dict[str, Path]
    maps: dict[str, Path]
    vcfs: dict[str, Path]
    indexes: dict[str, Path]
    donors: tuple[str, ...]
    donor_classes: dict[str, str]
    source_bundle_sha256: str

    def args(self, output_dir: Path) -> list[str]:
        arguments = [
            "--design",
            str(self.design),
            "--donor-metadata",
            str(self.donor_metadata),
            "--relationships",
            str(self.relationships),
            "--generator-environment-lock",
            str(self.environment_lock),
            "--code-revision",
            _git_revision(),
            "--output-dir",
            str(output_dir),
        ]
        for chrom in AUTOSOMES:
            arguments.extend(("--model-metadata", f"{chrom}={self.metadata[chrom]}"))
            arguments.extend(("--genetic-map", f"{chrom}={self.maps[chrom]}"))
            arguments.extend(("--donor-vcf", f"{chrom}={self.vcfs[chrom]}"))
            arguments.extend(("--donor-vcf-index", f"{chrom}={self.indexes[chrom]}"))
        return arguments

    def payload(self) -> dict[str, object]:
        return json.loads(self.design.read_text(encoding="utf-8"))

    def write_payload(self, payload: dict[str, object]) -> None:
        self.design.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )


def _write_tiny_generation_case(tmp_path: Path) -> TinyGenerationCase:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    metadata_dir = inputs / "metadata"
    map_dir = inputs / "maps"
    vcf_dir = inputs / "vcfs"
    metadata_dir.mkdir()
    map_dir.mkdir()
    vcf_dir.mkdir()

    donor_classes: dict[str, str] = {}
    split_donors: dict[str, list[str]] = {"calibration": [], "final_confirmation": []}
    split_prefixes = {"calibration": "CAL", "final_confirmation": "FINAL"}
    for split, prefix in split_prefixes.items():
        for population in POPULATION_ORDER:
            for founder in (1, 2):
                iid = f"{prefix}_{population}_{founder}"
                donor_classes[iid] = population
                split_donors[split].append(iid)
    donors = tuple(sorted(donor_classes))

    donor_metadata = inputs / "donor-metadata.tsv"
    metadata_rows = [
        "sample_id\tpopulation\tmodel_class\trelease\thard_filtered\t"
        "release_related\tall_samples_related"
    ]
    for iid in donors:
        population = donor_classes[iid]
        metadata_rows.append(
            f"{iid}\t{population}-population\t{population}\ttrue\tfalse\tfalse\tfalse"
        )
    donor_metadata.write_text("\n".join(metadata_rows) + "\n", encoding="utf-8")

    relationships = inputs / "validation-relationships.tsv"
    relationships.write_text(
        "iid_a\tiid_b\trelationship\n" + "".join(f"{iid}\t{iid}\tself\n" for iid in donors),
        encoding="utf-8",
    )
    environment_lock = inputs / "generator-environment.lock"
    environment_lock.write_text("numpy=deterministic-test-environment\n", encoding="utf-8")

    model_metadata: dict[str, Path] = {}
    genetic_maps: dict[str, Path] = {}
    donor_vcfs: dict[str, Path] = {}
    donor_indexes: dict[str, Path] = {}
    genotype_patterns = ("0|0", "0|1", "1|0", "1|1")
    refs = np.asarray(("A", "C", "G", "T"))
    alts = np.asarray(("G", "T", "A", "C"))
    for chrom in AUTOSOMES:
        base = int(chrom) * 1_000_000
        positions = np.asarray((base + 100, base + 200, base + 300, base + 400), dtype=np.int64)
        rsids = tuple(f"rsSIM{chrom}_{index + 1}" for index in range(4))

        metadata_path = metadata_dir / f"chr{chrom}.metadata.npz"
        np.savez(
            metadata_path,
            C=np.asarray(4, dtype=np.int64),
            M=np.asarray(2, dtype=np.int64),
            W=np.asarray(2, dtype=np.int64),
            A=np.asarray(7, dtype=np.int64),
            S=np.asarray(0, dtype=np.int64),
            context=np.asarray(0, dtype=np.int64),
            n_features=np.asarray(0, dtype=np.int64),
            snp_pos=positions,
            snp_ref=refs,
            snp_alt=alts,
            population_order=np.asarray(POPULATION_ORDER),
        )
        model_metadata[chrom] = metadata_path

        map_path = map_dir / f"chr{chrom}.map"
        map_path.write_text(
            "".join(
                f"{chrom}\t{rsid}\t{centimorgans}\t{position}\n"
                for rsid, centimorgans, position in zip(
                    rsids,
                    (0, 25, 50, 75),
                    positions,
                    strict=True,
                )
            ),
            encoding="utf-8",
        )
        genetic_maps[chrom] = map_path

        suffix = ".vcf.gz" if int(chrom) % 2 == 0 else ".vcf"
        vcf_path = vcf_dir / f"donors.chr{chrom}{suffix}"
        lines = [
            "##fileformat=VCFv4.2",
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(donors),
        ]
        for marker_index, (position, rsid, ref, alt) in enumerate(
            zip(positions, rsids, refs, alts, strict=True)
        ):
            genotypes = [
                genotype_patterns[(donor_index + marker_index) % len(genotype_patterns)]
                for donor_index in range(len(donors))
            ]
            lines.append(
                f"{chrom}\t{position}\t{rsid}\t{ref}\t{alt}\t.\tPASS\t.\tGT\t"
                + "\t".join(genotypes)
            )
        vcf_text = "\n".join(lines) + "\n"
        if vcf_path.suffix == ".gz":
            with gzip.open(vcf_path, "wt", encoding="utf-8") as handle:
                handle.write(vcf_text)
        else:
            vcf_path.write_text(vcf_text, encoding="utf-8")
        donor_vcfs[chrom] = vcf_path
        index_path = Path(f"{vcf_path}.tbi")
        index_path.write_bytes(f"tiny-index-chr{chrom}\n".encode())
        donor_indexes[chrom] = index_path

    target_fractions = {
        population: ("0.16" if population == "AFR" else "0.14") for population in POPULATION_ORDER
    }
    simulations = []
    for split, prefix in split_prefixes.items():
        for sequence, generation in enumerate((4, 8), start=1):
            simulations.append(
                {
                    "iid": f"SIM_{prefix}_{sequence}",
                    "split": split,
                    "generation": generation,
                    "validation_stratum": "balanced-seven-class-mosaic",
                    "donor_iids": sorted(split_donors[split]),
                    "target_marker_ancestry_fractions": target_fractions,
                    "fraction_absolute_tolerance": "0.1",
                }
            )
    source_bundle_sha256 = "a" * 64
    design = inputs / "simulation-design.json"
    design.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_id": "tiny-founder-mosaic",
                "source_bundle_artifact_sha256": source_bundle_sha256,
                "seed": 1750,
                "model_frozen_before_generation": True,
                "max_attempts_per_simulation": 128,
                "allowed_generations": [4, 8],
                "max_breakpoints_per_haplotype_by_autosome": {chrom: 3 for chrom in AUTOSOMES},
                "minimums": {
                    "founders_per_class_per_split": 2,
                    "simulations_per_class_per_split": 2,
                    "truth_haplotype_windows_per_class_per_split": 1,
                },
                "donor_metadata": {
                    "source_url": "https://example.invalid/pinned-donor-metadata.tsv",
                    "iid_field": "sample_id",
                    "population_field": "population",
                    "model_class_field": "model_class",
                    "release_field": "release",
                    "hard_filtered_field": "hard_filtered",
                    "release_related_field": "release_related",
                    "all_samples_related_field": "all_samples_related",
                },
                "simulations": simulations,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    return TinyGenerationCase(
        root=inputs,
        design=design,
        donor_metadata=donor_metadata,
        relationships=relationships,
        environment_lock=environment_lock,
        metadata=model_metadata,
        maps=genetic_maps,
        vcfs=donor_vcfs,
        indexes=donor_indexes,
        donors=donors,
        donor_classes=donor_classes,
        source_bundle_sha256=source_bundle_sha256,
    )


def _read_generated_manifest(output_dir: Path) -> tuple[Path, dict[str, object]]:
    path = output_dir / "simulation-manifest.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return path, payload


def _harness_manifest(case: TinyGenerationCase, output_dir: Path):
    path, _payload = _read_generated_manifest(output_dir)
    donors = frozenset(case.donors)
    return calibration.read_simulation_manifest(
        path,
        dataset_id="tiny-founder-mosaic",
        source_bundle_artifact_sha256=case.source_bundle_sha256,
        donor_iids=donors,
        isolation_iids=donors,
    )


def _model_specs(case: TinyGenerationCase) -> dict[str, object]:
    result = {}
    for chrom in AUTOSOMES:
        with np.load(case.metadata[chrom], allow_pickle=False) as metadata:
            result[chrom] = calibration.TruthModelSpec(
                chrom=chrom,
                marker_count=int(metadata["C"].item()),
                window_size=int(metadata["M"].item()),
                window_count=int(metadata["W"].item()),
                positions=np.asarray(metadata["snp_pos"], dtype=np.int64).copy(),
                population_order=tuple(str(value) for value in metadata["population_order"]),
            )
    return result


def _validate_with_harness(case: TinyGenerationCase, output_dir: Path):
    manifest = _harness_manifest(case, output_dir)
    donors = frozenset(case.donors)
    donor_labels = calibration.read_donor_labels(case.donor_metadata, manifest, donors)
    calibration.validate_split_population_coverage(manifest, donor_labels)
    labels_path = output_dir / "labels" / "calibration.tsv"
    labels = calibration.read_labels(labels_path)
    assert set(labels) == set(manifest.splits["calibration"])

    specs = _model_specs(case)
    fixtures = []
    for iid in manifest.splits["calibration"]:
        entry = manifest.simulations[iid]
        assert labels[iid] == entry["validation_stratum"]
        fixture = calibration.read_fixture(
            iid,
            labels[iid],
            _relative_artifact(output_dir, entry["fixture_file"]),
            _relative_artifact(output_dir, entry["window_truth_file"]),
        )
        fixtures.append(
            calibration.attach_validated_simulation_truth(
                fixture,
                marker_truth_path=_relative_artifact(output_dir, entry["marker_truth_file"]),
                tract_truth_path=_relative_artifact(output_dir, entry["tract_truth_file"]),
                model_specs=specs,
                donor_labels=donor_labels,
                donor_iids=donors,
                simulation_manifest=manifest,
            )
        )
    calibration.validate_simulation_fixtures(
        simulation_manifest=manifest,
        dataset_split="calibration",
        fixtures=fixtures,
    )
    coverage = calibration.validate_selected_split_evaluation_coverage(
        simulation_manifest=manifest,
        donor_labels=donor_labels,
        dataset_split="calibration",
        fixtures=fixtures,
    )
    return manifest, coverage


def _verify_calibration_split(
    case: TinyGenerationCase,
    output_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    manifest, payload = _read_generated_manifest(output_dir)
    monkeypatch.setattr(
        verifier,
        "_verify_repository_generator_script",
        lambda _path, _repo_root: None,
    )
    arguments = [
        "--simulation-manifest",
        str(manifest),
        "--dataset-split",
        "calibration",
        "--verifier-environment-lock",
        str(REPO_ROOT / "uv.lock"),
        "--generator-script",
        str(GENERATOR_PATH),
        "--generator-environment-lock",
        str(case.environment_lock),
        "--expected-code-revision",
        _git_revision(),
        "--output",
        str(case.root.parent / "simulation-verification.json"),
    ]
    simulations = {
        entry["iid"]: entry for entry in payload["simulations"] if entry["split"] == "calibration"
    }
    for iid in sorted(simulations):
        entry = simulations[iid]
        arguments.extend(
            ("--fixture", f"{iid}={_relative_artifact(output_dir, entry['fixture_file'])}")
        )
        arguments.extend(
            (
                "--marker-truth",
                f"{iid}={_relative_artifact(output_dir, entry['marker_truth_file'])}",
            )
        )
        arguments.extend(
            (
                "--tract-truth",
                f"{iid}={_relative_artifact(output_dir, entry['tract_truth_file'])}",
            )
        )
    for chrom in AUTOSOMES:
        arguments.extend(("--donor-vcf", f"{chrom}={case.vcfs[chrom]}"))
        arguments.extend(("--donor-vcf-index", f"{chrom}={case.indexes[chrom]}"))

    assert verifier.main(arguments) == 0
    return json.loads((case.root.parent / "simulation-verification.json").read_text())


def _tree_bytes(path: Path) -> dict[str, bytes]:
    return {
        file.relative_to(path).as_posix(): file.read_bytes()
        for file in sorted(path.rglob("*"))
        if file.is_file()
    }


def test_generator_artifacts_pass_harness_and_independent_donor_replay(
    tmp_path,
    monkeypatch,
    capsys,
):
    case = _write_tiny_generation_case(tmp_path)
    output_dir = tmp_path / "generated"

    assert generator.main(case.args(output_dir)) == 0
    assert "ERROR:" not in capsys.readouterr().err

    manifest, coverage = _validate_with_harness(case, output_dir)
    _manifest_path, raw_manifest = _read_generated_manifest(output_dir)
    assert set(manifest.splits) == {"calibration", "final_confirmation"}
    assert len(manifest.splits["calibration"]) == 2
    assert len(manifest.splits["final_confirmation"]) == 2
    assert coverage["generations"] == [4, 8]
    assert all(value >= 2 for value in coverage["founders_by_class"].values())
    assert set(calibration.read_labels(output_dir / "labels" / "final_confirmation.tsv")) == set(
        manifest.splits["final_confirmation"]
    )
    assert set(raw_manifest["labels"]) == {"calibration", "final_confirmation"}
    for split in ("calibration", "final_confirmation"):
        label_provenance = raw_manifest["labels"][split]
        assert set(label_provenance) == {"filename", "sha256"}
        label_path = _relative_artifact(output_dir, label_provenance["filename"])
        assert label_provenance["sha256"] == _sha256_file(label_path)

    protocol = raw_manifest["simulation_protocol"]
    assert protocol["generation_rate_semantics"] == (
        "lambda=generation*(last_model_marker_cM-first_model_marker_cM)/100"
    )
    assert protocol["event_projection_semantics"] == (
        "right-search model-marker cM boundaries; clamp to [1,C-1]; deduplicate"
    )
    assert protocol["breakpoint_envelope_semantics"] == (
        "post-projection tract transitions after merging adjacent identical "
        "donor/source-haplotype identities"
    )
    expected_simulation_fields = {
        "iid",
        "split",
        "simulation_kind",
        "generation",
        "validation_stratum",
        "donor_iids",
        "target_marker_ancestry_fractions",
        "fraction_absolute_tolerance",
        "fixture_file",
        "fixture_sha256",
        "marker_truth_file",
        "marker_truth_sha256",
        "tract_truth_file",
        "tract_truth_sha256",
        "window_truth_file",
        "window_truth_sha256",
    }
    final_entries = [
        entry for entry in raw_manifest["simulations"] if entry["split"] == "final_confirmation"
    ]
    assert final_entries
    assert all(set(entry) == expected_simulation_fields for entry in final_entries)

    report = _verify_calibration_split(case, output_dir, monkeypatch)
    assert report["verification_status"] == "passed"
    assert report["source_vcf_haplotypes_verified"] is True
    assert report["fixture_genotypes_verified"] is True
    assert report["marker_truth_tracts_reconciled"] is True
    assert report["totals"] == {
        "autosomes_verified": 22,
        "simulations_verified": 2,
        "marker_rows_verified": 176,
        "haplotype_alleles_verified": 352,
        "missing_rows": 0,
        "mismatches": 0,
    }


def test_generator_is_byte_reproducible_across_output_directories(tmp_path):
    case = _write_tiny_generation_case(tmp_path)
    first = tmp_path / "generated-first"
    second = tmp_path / "generated-second"

    assert generator.main(case.args(first)) == 0
    assert generator.main(case.args(second)) == 0

    first_bytes = _tree_bytes(first)
    second_bytes = _tree_bytes(second)
    assert first_bytes
    assert first_bytes == second_bytes
    assert "simulation-manifest.json" in first_bytes
    assert "labels/calibration.tsv" in first_bytes
    assert "labels/final_confirmation.tsv" in first_bytes


@pytest.mark.parametrize("invalid_gt", ["0/1", "0|2", ".|1"])
def test_generator_rejects_invalid_donor_genotypes_without_publishing(
    tmp_path,
    capsys,
    invalid_gt,
):
    case = _write_tiny_generation_case(tmp_path)
    chromosome_one = case.vcfs["1"]
    original = chromosome_one.read_text(encoding="utf-8")
    chromosome_one.write_text(original.replace("\t0|0", f"\t{invalid_gt}", 1), encoding="utf-8")
    output_dir = tmp_path / "must-not-exist"

    assert generator.main(case.args(output_dir)) == 2

    error = capsys.readouterr().err.lower()
    assert "gt" in error
    assert "phased" in error or "biallelic" in error
    assert not output_dir.exists()


def test_generator_rejects_vcf_model_allele_mismatch_without_publishing(tmp_path, capsys):
    case = _write_tiny_generation_case(tmp_path)
    chromosome_one = case.vcfs["1"]
    original = chromosome_one.read_text(encoding="utf-8")
    chromosome_one.write_text(
        original.replace("\tA\tG\t.\tPASS", "\tA\tC\t.\tPASS", 1),
        encoding="utf-8",
    )
    output_dir = tmp_path / "must-not-exist"

    assert generator.main(case.args(output_dir)) == 2

    assert "ref/alt-matching model marker" in capsys.readouterr().err.lower()
    assert not output_dir.exists()


@pytest.mark.parametrize(
    ("input_attribute", "expected_label"),
    [("donor_metadata", "donor metadata"), ("relationships", "relationships")],
)
def test_generator_rejects_gzip_for_non_vcf_tabular_inputs(
    tmp_path,
    capsys,
    input_attribute,
    expected_label,
):
    case = _write_tiny_generation_case(tmp_path)
    path = getattr(case, input_attribute)
    original = path.read_bytes()
    with gzip.open(path, "wb") as handle:
        handle.write(original)
    output_dir = tmp_path / "must-not-exist"

    assert generator.main(case.args(output_dir)) == 2

    error = capsys.readouterr().err.lower()
    assert expected_label in error
    assert "gzip input is not supported" in error
    assert not output_dir.exists()


def test_publication_rolls_back_when_generated_file_changes_after_rename(tmp_path, monkeypatch):
    staging = tmp_path / ".generated-staging"
    for directory in ("fixtures", "marker-truth", "tract-truth", "window-truth", "labels"):
        (staging / directory).mkdir(parents=True)
    generated_file = staging / "fixtures" / "SIM.tsv"
    generated_file.write_text("original generated bytes\n", encoding="utf-8")
    snapshot = generator._snapshot_file(generated_file, label="generated fixture")
    output_dir = tmp_path / "generated"
    original_rename = generator.os.rename
    publication_rename_seen = False

    def rename_then_mutate(source, destination, *args, **kwargs):
        nonlocal publication_rename_seen
        result = original_rename(source, destination, *args, **kwargs)
        if destination == output_dir.name and not publication_rename_seen:
            publication_rename_seen = True
            (output_dir / "fixtures" / "SIM.tsv").write_text(
                "mutated after publication rename\n",
                encoding="utf-8",
            )
        return result

    monkeypatch.setattr(generator.os, "rename", rename_then_mutate)

    with pytest.raises(ValueError, match="generated file changed during publication"):
        generator._publish(
            output_dir=output_dir,
            staging=staging,
            input_snapshots=(),
            output_snapshots=((snapshot, "generated fixture"),),
        )

    assert publication_rename_seen is True
    assert not output_dir.exists()
    assert staging.is_dir()
    assert generated_file.read_text(encoding="utf-8") == "mutated after publication rename\n"


def test_publication_rolls_back_when_input_changes_after_rename(tmp_path, monkeypatch):
    staging = tmp_path / ".generated-staging"
    for directory in ("fixtures", "marker-truth", "tract-truth", "window-truth", "labels"):
        (staging / directory).mkdir(parents=True)
    generated_file = staging / "fixtures" / "SIM.tsv"
    generated_file.write_text("stable generated bytes\n", encoding="utf-8")
    generated_snapshot = generator._snapshot_file(generated_file, label="generated fixture")
    input_file = tmp_path / "simulation-design.json"
    input_file.write_text("stable input bytes\n", encoding="utf-8")
    input_snapshot = generator._snapshot_file(input_file, label="simulation design")
    output_dir = tmp_path / "generated"
    original_rename = generator.os.rename
    publication_rename_seen = False

    def rename_then_mutate_input(source, destination, *args, **kwargs):
        nonlocal publication_rename_seen
        result = original_rename(source, destination, *args, **kwargs)
        if destination == output_dir.name and not publication_rename_seen:
            publication_rename_seen = True
            input_file.write_text("mutated input after publication rename\n", encoding="utf-8")
        return result

    monkeypatch.setattr(generator.os, "rename", rename_then_mutate_input)

    with pytest.raises(ValueError, match="input changed during publication"):
        generator._publish(
            output_dir=output_dir,
            staging=staging,
            input_snapshots=((input_snapshot, "simulation design"),),
            output_snapshots=((generated_snapshot, "generated fixture"),),
        )

    assert publication_rename_seen is True
    assert not output_dir.exists()
    assert staging.is_dir()
    assert generated_file.read_text(encoding="utf-8") == "stable generated bytes\n"


def test_split_minima_are_aggregated_instead_of_required_from_each_simulation():
    minimums = {
        "founders_per_class_per_split": 2,
        "simulations_per_class_per_split": 2,
        "truth_haplotype_windows_per_class_per_split": 1,
    }
    design = SimpleNamespace(minimums=minimums)
    generated = {}
    sparse_iids = []
    for split in ("calibration", "final_confirmation"):
        for sequence in (1, 2, 3):
            iid = f"{split}_{sequence}"
            complete = sequence < 3
            labels = POPULATION_ORDER if complete else ("AFR",)
            windows = tuple(
                generator.WindowTruth(
                    chrom="1",
                    start=index + 1,
                    end=index + 1,
                    hap0=label,
                    hap1=label,
                )
                for index, label in enumerate(labels)
            )
            if not complete:
                sparse_iids.append(iid)
            generated[iid] = SimpleNamespace(
                spec=SimpleNamespace(
                    split=split,
                    validation_stratum="aggregate-seven-class-stratum",
                ),
                windows=windows,
                modal_founders={
                    label: frozenset({f"{split}_{label}_FOUNDER_{sequence}"}) for label in labels
                },
            )

    assert all(
        {label for window in generated[iid].windows for label in (window.hap0, window.hap1)}
        == {"AFR"}
        for iid in sparse_iids
    )
    assert generator._split_minimum_failure(generated, design) is None


def test_window_projection_uses_native_code_ties_and_absorbs_remainder():
    population_order = ("EUR", "AFR", "AMR", "CSA", "EAS", "MID", "OCE")
    model = generator.ModelSpec(
        chrom="1",
        positions=np.asarray((100, 200, 300, 400, 500), dtype=np.int64),
        refs=("A",) * 5,
        alts=("G",) * 5,
        population_order=population_order,
        marker_count=5,
        window_size=2,
        window_count=2,
        marker_cm=np.asarray((0.0, 1.0, 2.0, 3.0, 4.0)),
        metadata_sha256="a" * 64,
        map_sha256="b" * 64,
    )
    tracts = {
        ("1", 0): (
            generator.Tract(0, 1, "AFR_DONOR", 0),
            generator.Tract(1, 5, "EUR_DONOR", 0),
        ),
        ("1", 1): (
            generator.Tract(0, 1, "AFR_DONOR", 1),
            generator.Tract(1, 5, "EUR_DONOR", 1),
        ),
    }

    windows, modal_founders = generator._derive_windows(
        model=model,
        chrom="1",
        tracts=tracts,
        donor_labels={"AFR_DONOR": "AFR", "EUR_DONOR": "EUR"},
    )

    assert windows == [
        generator.WindowTruth("1", 100, 200, "EUR", "EUR"),
        generator.WindowTruth("1", 300, 500, "EUR", "EUR"),
    ]
    assert modal_founders == {"EUR": {"EUR_DONOR"}}


def test_generator_exhausts_predeclared_attempt_bound_without_publishing(tmp_path, capsys):
    case = _write_tiny_generation_case(tmp_path)
    payload = case.payload()
    payload["max_attempts_per_simulation"] = 2
    impossible = {population: "0.000001" for population in POPULATION_ORDER[:-1]}
    impossible[POPULATION_ORDER[-1]] = "0.999994"
    for simulation in payload["simulations"]:
        simulation["target_marker_ancestry_fractions"] = impossible
        simulation["fraction_absolute_tolerance"] = "0.000001"
    case.write_payload(payload)
    output_dir = tmp_path / "must-not-exist"

    assert generator.main(case.args(output_dir)) == 2

    error = capsys.readouterr().err.lower()
    assert "2" in error
    assert "attempt" in error or "exhaust" in error
    assert not output_dir.exists()
