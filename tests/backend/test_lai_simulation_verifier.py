from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "lai_bundle_v2" / "06g_verify_simulation.py"
MODULE_SPEC = importlib.util.spec_from_file_location("lai_simulation_verifier", SCRIPT_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
verifier = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = verifier
MODULE_SPEC.loader.exec_module(verifier)
ORIGINAL_VERIFY_REPOSITORY_GENERATOR_SCRIPT = verifier._verify_repository_generator_script


@pytest.fixture(autouse=True)
def _allow_controlled_test_generator_script(monkeypatch):
    """Keep replay tests independent of the not-yet-landed production generator."""
    monkeypatch.setattr(
        verifier,
        "_verify_repository_generator_script",
        lambda _path, _repo_root: None,
    )
    monkeypatch.setattr(
        verifier,
        "_verify_repository_verifier_files",
        lambda _repo_root: None,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _git_revision() -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@dataclass(slots=True)
class VerificationCase:
    manifest: Path
    fixture: Path
    marker_truth: Path
    tract_truth: Path
    generator_script: Path
    generator_environment_lock: Path
    environment_lock: Path
    donor_vcfs: dict[str, Path]
    indexes: dict[str, Path]
    output: Path

    def args(self) -> list[str]:
        result = [
            "--simulation-manifest",
            str(self.manifest),
            "--dataset-split",
            "calibration",
            "--fixture",
            f"SIM001={self.fixture}",
            "--marker-truth",
            f"SIM001={self.marker_truth}",
            "--tract-truth",
            f"SIM001={self.tract_truth}",
            "--verifier-environment-lock",
            str(self.environment_lock),
            "--generator-script",
            str(self.generator_script),
            "--generator-environment-lock",
            str(self.generator_environment_lock),
            "--expected-code-revision",
            _git_revision(),
            "--output",
            str(self.output),
        ]
        for chrom in verifier.AUTOSOMES:
            result.extend(("--donor-vcf", f"{chrom}={self.donor_vcfs[chrom]}"))
            result.extend(("--donor-vcf-index", f"{chrom}={self.indexes[chrom]}"))
        return result

    def refresh_simulation_hashes(self) -> None:
        payload = json.loads(self.manifest.read_text(encoding="utf-8"))
        payload["simulations"][0]["fixture_sha256"] = _sha256_file(self.fixture)
        payload["simulations"][0]["marker_truth_sha256"] = _sha256_file(self.marker_truth)
        self.manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_verification_case(tmp_path: Path) -> VerificationCase:
    donor_vcfs: dict[str, Path] = {}
    indexes: dict[str, Path] = {}
    vcf_hashes: dict[str, str] = {}
    index_hashes: dict[str, str] = {}
    truth_rows = ["\t".join(verifier.MARKER_TRUTH_HEADER)]
    fixture_rows = ["\t".join(verifier.FIXTURE_HEADER)]
    tract_rows = ["\t".join(verifier.TRACT_TRUTH_HEADER)]

    for chrom in verifier.AUTOSOMES:
        position = int(chrom) * 1000 + 7
        rsid = f"rsSIM{chrom}"
        suffix = ".vcf.gz" if int(chrom) % 2 == 0 else ".vcf"
        vcf_path = tmp_path / f"donors.chr{chrom}{suffix}"
        vcf_text = (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tDONOR_A\tDONOR_B\n"
            f"{chrom}\t{position}\t{rsid}\tA\tG\t.\tPASS\t.\tGT\t0|1\t1|0\n"
        )
        if suffix.endswith(".gz"):
            with gzip.open(vcf_path, "wt", encoding="utf-8") as handle:
                handle.write(vcf_text)
        else:
            vcf_path.write_text(vcf_text, encoding="utf-8")
        index_path = Path(f"{vcf_path}.tbi")
        index_path.write_bytes(f"tiny-index-chr{chrom}\n".encode())
        donor_vcfs[chrom] = vcf_path
        indexes[chrom] = index_path
        vcf_hashes[chrom] = _sha256_file(vcf_path)
        index_hashes[chrom] = _sha256_file(index_path)
        truth_rows.append(f"SIM001\t{chrom}\t0\t{position}\t{rsid}\tDONOR_A\t0\tDONOR_B\t0")
        fixture_rows.append(f"{rsid}\t{chrom}\t{position}\tA\tG")
        tract_rows.extend(
            (
                f"SIM001\t{chrom}\t0\t0\t1\tDONOR_A\t0",
                f"SIM001\t{chrom}\t1\t0\t1\tDONOR_B\t0",
            )
        )

    marker_truth = tmp_path / "SIM001.marker-truth.tsv"
    marker_truth.write_text("\n".join(truth_rows) + "\n", encoding="utf-8")
    tract_truth = tmp_path / "SIM001.tract-truth.tsv"
    tract_truth.write_text("\n".join(tract_rows) + "\n", encoding="utf-8")
    fixture = tmp_path / "SIM001.tsv"
    fixture.write_text("\n".join(fixture_rows) + "\n", encoding="utf-8")
    environment_lock = tmp_path / "verifier-environment.lock"
    environment_lock.write_text("stdlib-only\npython=3\n", encoding="utf-8")
    generator_script = REPO_ROOT / "scripts" / "lai_bundle_v2" / "06e_lai_accuracy.py"
    generator_environment_lock = tmp_path / "generator-environment.lock"
    generator_environment_lock.write_text("stdlib-only\npython=3\n", encoding="utf-8")
    manifest = tmp_path / "simulation-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "dataset_id": "tiny-independent-replay",
                "generator": {
                    "name": "yeliztli-founder-mosaic-v1",
                    "code_revision": _git_revision(),
                    "script_sha256": _sha256_file(generator_script),
                    "environment_lock_sha256": _sha256_file(generator_environment_lock),
                },
                "donor_haplotype_source": {
                    "genome_build": "GRCh38",
                    "per_chromosome_vcf_sha256": vcf_hashes,
                    "per_chromosome_vcf_index_sha256": index_hashes,
                    "per_chromosome_sample_ids_sha256": {
                        chrom: _sha256_json(["DONOR_A", "DONOR_B"]) for chrom in verifier.AUTOSOMES
                    },
                    "per_chromosome_sample_count": {chrom: 2 for chrom in verifier.AUTOSOMES},
                },
                "simulation_protocol": {
                    "max_breakpoints_per_haplotype_by_autosome": {
                        chrom: 0 for chrom in verifier.AUTOSOMES
                    },
                },
                "models": {
                    "population_order": ["AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE"],
                    "per_chromosome": {
                        chrom: {"C": 1, "M": 1, "W": 1} for chrom in verifier.AUTOSOMES
                    },
                },
                "splits": {
                    "calibration": ["SIM001"],
                    "final_confirmation": ["SIMFINAL"],
                },
                "simulations": [
                    {
                        "iid": "SIM001",
                        "split": "calibration",
                        "donor_iids": ["DONOR_A", "DONOR_B"],
                        "marker_truth_sha256": _sha256_file(marker_truth),
                        "tract_truth_sha256": _sha256_file(tract_truth),
                        "window_truth_sha256": "a" * 64,
                        "fixture_sha256": _sha256_file(fixture),
                        "generation": 4,
                        "validation_stratum": "test",
                    },
                    {
                        "iid": "SIMFINAL",
                        "split": "final_confirmation",
                        "donor_iids": ["DONOR_A", "DONOR_B"],
                        "marker_truth_sha256": "b" * 64,
                        "tract_truth_sha256": "c" * 64,
                        "window_truth_sha256": "d" * 64,
                        "fixture_sha256": "e" * 64,
                        "generation": 8,
                        "validation_stratum": "test",
                    },
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return VerificationCase(
        manifest=manifest,
        fixture=fixture,
        marker_truth=marker_truth,
        tract_truth=tract_truth,
        generator_script=generator_script,
        generator_environment_lock=generator_environment_lock,
        environment_lock=environment_lock,
        donor_vcfs=donor_vcfs,
        indexes=indexes,
        output=tmp_path / "simulation-verification.json",
    )


def test_independent_verifier_replays_plain_and_gzip_source_vcfs(tmp_path, capsys):
    case = _write_verification_case(tmp_path)

    assert verifier.main(case.args()) == 0
    assert capsys.readouterr().err == ""

    report = json.loads(case.output.read_text(encoding="utf-8"))
    assert report["schema_version"] == 2
    assert report["verification_status"] == "passed"
    assert report["source_vcf_haplotypes_verified"] is True
    assert report["fixture_genotypes_verified"] is True
    assert report["source_vcf_marker_rsids_verified"] is True
    assert report["marker_truth_tracts_reconciled"] is True
    assert report["verifier"]["distinct_from_generator_script_sha256"] is True
    assert report["dataset_split"] == "calibration"
    assert report["generator_code_revision"] == _git_revision()
    assert report["generator_environment_lock_snapshot"]["sha256"] == _sha256_file(
        case.generator_environment_lock
    )
    assert report["totals"] == {
        "autosomes_verified": 22,
        "simulations_verified": 1,
        "marker_rows_verified": 22,
        "haplotype_alleles_verified": 44,
        "missing_rows": 0,
        "mismatches": 0,
    }
    assert report["simulations"]["SIM001"]["marker_rows_verified"] == 22
    assert set(report["simulations"]) == {"SIM001"}
    assert set(report["source_snapshots"]) == set(verifier.AUTOSOMES)
    assert report["verifier"]["script_sha256"] != "f" * 64


def test_calibration_verification_does_not_open_sealed_final_split(tmp_path, capsys):
    case = _write_verification_case(tmp_path)

    assert verifier.main(case.args()) == 0

    assert capsys.readouterr().err == ""
    report = json.loads(case.output.read_text(encoding="utf-8"))
    assert report["dataset_split"] == "calibration"
    assert set(report["simulations"]) == {"SIM001"}
    assert report["totals"]["simulations_verified"] == 1


def test_final_verification_requires_frozen_policy_before_truth_open(tmp_path, capsys):
    case = _write_verification_case(tmp_path)
    arguments = case.args()
    arguments[arguments.index("--dataset-split") + 1] = "final_confirmation"

    assert verifier.main(arguments) == 2

    assert "requires --confirmation-policy" in capsys.readouterr().err
    assert not case.output.exists()


def test_independent_verifier_rejects_generator_environment_mismatch(tmp_path, capsys):
    case = _write_verification_case(tmp_path)
    case.generator_environment_lock.write_text("changed-generator-environment\n", encoding="utf-8")

    assert verifier.main(case.args()) == 2

    assert "generator environment lock SHA-256 does not match" in capsys.readouterr().err
    assert not case.output.exists()


def test_independent_verifier_rejects_generator_revision_mismatch(tmp_path, capsys):
    case = _write_verification_case(tmp_path)
    payload = json.loads(case.manifest.read_text(encoding="utf-8"))
    payload["generator"]["code_revision"] = "0" * 40
    case.manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    assert verifier.main(case.args()) == 2

    assert "generator revision does not match repository HEAD" in capsys.readouterr().err
    assert not case.output.exists()


def test_repository_generator_rejects_wrong_script_basename():
    with pytest.raises(ValueError, match="reviewed 06g_generate_simulation.py"):
        ORIGINAL_VERIFY_REPOSITORY_GENERATOR_SCRIPT(
            REPO_ROOT / "scripts" / "lai_bundle_v2" / "06e_lai_accuracy.py",
            REPO_ROOT,
        )


def test_independent_verifier_rejects_wrong_generator_name(tmp_path, capsys):
    case = _write_verification_case(tmp_path)
    payload = json.loads(case.manifest.read_text(encoding="utf-8"))
    payload["generator"]["name"] = "unreviewed-generator"
    case.manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    assert verifier.main(case.args()) == 2

    assert "generator.name must be 'yeliztli-founder-mosaic-v1'" in capsys.readouterr().err
    assert not case.output.exists()


def test_independent_verifier_rejects_fixture_allele_mismatch_atomically(tmp_path, capsys):
    case = _write_verification_case(tmp_path)
    original = case.fixture.read_text(encoding="utf-8")
    case.fixture.write_text(original.replace("\tA\tG\n", "\tG\tG\n", 1), encoding="utf-8")
    case.refresh_simulation_hashes()
    case.output.write_text("previous-valid-stamp\n", encoding="utf-8")

    assert verifier.main(case.args()) == 2

    assert "allele mismatch" in capsys.readouterr().err
    assert case.output.read_text(encoding="utf-8") == "previous-valid-stamp\n"


def test_independent_verifier_rejects_marker_rsid_not_in_source(tmp_path, capsys):
    case = _write_verification_case(tmp_path)
    case.marker_truth.write_text(
        case.marker_truth.read_text(encoding="utf-8").replace("rsSIM1", "rsWRONG", 1),
        encoding="utf-8",
    )
    case.fixture.write_text(
        case.fixture.read_text(encoding="utf-8").replace("rsSIM1", "rsWRONG", 1),
        encoding="utf-8",
    )
    case.refresh_simulation_hashes()

    assert verifier.main(case.args()) == 2

    assert "missing source VCF marker chr1:1007:rsWRONG" in capsys.readouterr().err
    assert not case.output.exists()


def test_independent_verifier_rejects_tampered_pinned_index(tmp_path, capsys):
    case = _write_verification_case(tmp_path)
    case.indexes["7"].write_bytes(b"tampered-index\n")

    assert verifier.main(case.args()) == 2

    assert "source VCF index chr7: SHA-256 does not match" in capsys.readouterr().err
    assert not case.output.exists()


def test_independent_verifier_rejects_tampered_tract_truth(tmp_path, capsys):
    case = _write_verification_case(tmp_path)
    case.tract_truth.write_text("tampered tract bytes\n", encoding="utf-8")

    assert verifier.main(case.args()) == 2

    assert "tract truth SIM001: SHA-256 does not match" in capsys.readouterr().err
    assert not case.output.exists()


def test_independent_verifier_reconciles_marker_and_tract_donors(tmp_path, capsys):
    case = _write_verification_case(tmp_path)
    case.tract_truth.write_text(
        case.tract_truth.read_text(encoding="utf-8").replace(
            "SIM001\t1\t0\t0\t1\tDONOR_A\t0",
            "SIM001\t1\t0\t0\t1\tDONOR_B\t0",
        ),
        encoding="utf-8",
    )
    payload = json.loads(case.manifest.read_text(encoding="utf-8"))
    payload["simulations"][0]["tract_truth_sha256"] = _sha256_file(case.tract_truth)
    case.manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    assert verifier.main(case.args()) == 2

    assert "contribution does not match tract truth" in capsys.readouterr().err
    assert not case.output.exists()


def test_text_parser_rejects_inode_swap_after_snapshot(tmp_path):
    source = tmp_path / "source.tsv"
    replacement = tmp_path / "replacement.tsv"
    source.write_text("original\n", encoding="utf-8")
    replacement.write_text("alternate\n", encoding="utf-8")
    snapshot = verifier._snapshot_file(source, label="test source")
    os.replace(replacement, source)

    with pytest.raises(ValueError, match="changed while it was opened"):
        with verifier._open_text_auto(
            source,
            label="test source",
            expected_snapshot=snapshot,
        ):
            pass


def test_text_parser_closes_raw_gzip_stream(tmp_path, monkeypatch):
    source = tmp_path / "source.tsv.gz"
    with gzip.open(source, "wt", encoding="utf-8") as handle:
        handle.write("value\n")
    opened = []
    original_open = verifier._open_binary_nofollow

    def capture_raw_stream(*args, **kwargs):
        raw = original_open(*args, **kwargs)
        opened.append(raw)
        return raw

    monkeypatch.setattr(verifier, "_open_binary_nofollow", capture_raw_stream)

    with verifier._open_text_auto(source, label="test source") as handle:
        assert handle.read() == "value\n"

    assert len(opened) == 1
    assert opened[0].closed


def test_manifest_reader_enforces_json_size_limit(tmp_path, capsys, monkeypatch):
    case = _write_verification_case(tmp_path)
    monkeypatch.setattr(verifier, "MAX_MANIFEST_BYTES", 1)

    assert verifier.main(case.args()) == 2

    assert "JSON exceeds the 1-byte safety limit" in capsys.readouterr().err


def test_independent_verifier_rejects_legacy_index_hash_field(tmp_path, capsys):
    case = _write_verification_case(tmp_path)
    payload = json.loads(case.manifest.read_text(encoding="utf-8"))
    source = payload["donor_haplotype_source"]
    source["per_chromosome_index_sha256"] = source.pop("per_chromosome_vcf_index_sha256")
    case.manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    assert verifier.main(case.args()) == 2

    assert "source VCF index SHA-256 mapping must cover" in capsys.readouterr().err
    assert not case.output.exists()


def test_independent_verifier_rejects_hardlinked_input_output(tmp_path, capsys):
    case = _write_verification_case(tmp_path)
    original = case.fixture.read_bytes()
    os.link(case.fixture, case.output)

    assert verifier.main(case.args()) == 2

    assert "hardlinked input" in capsys.readouterr().err
    assert case.fixture.read_bytes() == original


def test_independent_verifier_rejects_symlinked_source_vcf(tmp_path, capsys):
    case = _write_verification_case(tmp_path)
    source = case.donor_vcfs["1"]
    symlink = tmp_path / "donors.chr1.link.vcf"
    symlink.symlink_to(source)
    case.donor_vcfs["1"] = symlink

    assert verifier.main(case.args()) == 2

    assert "symlink path component is not allowed" in capsys.readouterr().err
    assert not case.output.exists()


def test_atomic_stamp_publication_rejects_parent_inode_swap(tmp_path, monkeypatch):
    parent = tmp_path / "output"
    moved_parent = tmp_path / "moved-output"
    parent.mkdir()
    output = parent / "stamp.json"
    real_replace = verifier.os.replace
    swapped = False

    def swap_parent_before_publish(source, destination, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            real_replace(parent, moved_parent)
            parent.mkdir()
        return real_replace(source, destination, **kwargs)

    monkeypatch.setattr(verifier.os, "replace", swap_parent_before_publish)

    with pytest.raises(ValueError, match="parent directory changed"):
        verifier._atomic_write_json(output, {"ok": True}, input_paths=set())

    assert not output.exists()
    assert not (moved_parent / "stamp.json").exists()


@pytest.mark.parametrize("genotype", ["0/1", "0|2", ".|1"])
def test_independent_verifier_refuses_unphased_or_nonbiallelic_gt(
    tmp_path,
    capsys,
    genotype,
):
    case = _write_verification_case(tmp_path)
    vcf_path = case.donor_vcfs["1"]
    vcf_path.write_text(
        vcf_path.read_text(encoding="utf-8").replace("0|1\t1|0", f"{genotype}\t1|0"),
        encoding="utf-8",
    )
    payload = json.loads(case.manifest.read_text(encoding="utf-8"))
    payload["donor_haplotype_source"]["per_chromosome_vcf_sha256"]["1"] = _sha256_file(vcf_path)
    case.manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    assert verifier.main(case.args()) == 2

    assert "GT is not" in capsys.readouterr().err
    assert not case.output.exists()
