from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "lai_bundle_v2" / "gnomix_training_manifests.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("gnomix_training_manifests_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class ManifestInputs:
    root: Path
    metadata: Path
    selected: Path
    full_map: Path
    training_map: Path
    heldout_map: Path
    union_catalog: Path
    lifted_regions: Path
    chromosome_files: dict[str, tuple[Path, Path]]

    @property
    def marker_sources(self) -> dict[str, Path]:
        return {
            "lifted_regions": self.lifted_regions,
            "union_catalog": self.union_catalog,
        }


def _write_gzip(path: Path, text: str) -> None:
    path.write_bytes(gzip.compress(text.encode("ascii"), mtime=0))


def _vcf(
    chromosome: str,
    *,
    second_position: int = 200,
    samples: tuple[str, ...] = ("S1", "S2", "S3", "S4"),
) -> str:
    header = "\t".join(
        ("#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT", *samples)
    )
    all_genotypes = {"S1": "0|0", "S2": "0|1", "S3": "1|0", "S4": "1|1"}
    genotypes = "\t".join(("GT", *(all_genotypes[sample] for sample in samples)))
    return (
        "##fileformat=VCFv4.2\n"
        f"##contig=<ID={chromosome}>\n"
        f"{header}\n"
        f"{chromosome}\t100\trs{chromosome[3:]}a\tA\tG\t.\tPASS\t.\t{genotypes}\n"
        f"{chromosome}\t{second_position}\trs{chromosome[3:]}b\tC\tT\t.\tPASS\t.\t{genotypes}\n"
    )


def _make_inputs(root: Path) -> ManifestInputs:
    root.mkdir(parents=True)
    metadata = root / "gnomad_meta_updated.tsv"
    metadata.write_text(
        "s\thgdp_tgp_meta.Population\thgdp_tgp_meta.Genetic.region\n"
        "S1\tPop1\tAFR\n"
        "S2\tPop2\tAFR\n"
        "S3\tPop3\tEUR\n"
        "S4\tPop4\tEUR\n"
    )
    selected = root / "single_ancestry_samples.tsv"
    selected.write_text(
        "IID\tpopulation\tgenetic_region\n"
        "S1\tPop1\tAFR\n"
        "S2\tPop2\tAFR\n"
        "S3\tPop3\tEUR\n"
        "S4\tPop4\tEUR\n"
    )
    full_map = root / "sample_map.full.txt"
    full_map.write_text("S1\tAFR\nS2\tAFR\nS3\tEUR\nS4\tEUR\n")
    training_map = root / "sample_map.txt"
    training_map.write_text("S1\tAFR\nS2\tAFR\nS3\tEUR\n")
    heldout_map = root / "held_out_validation.tsv"
    heldout_map.write_text("IID\tgenetic_region\nS4\tEUR\n")
    union_catalog = root / "union_sites.tsv"
    union_catalog.write_text(
        "".join(
            f"rs{number}{suffix}\t{number}\t{position}\n"
            for number in range(1, 23)
            for suffix, position in (("a", 100), ("b", 200))
        )
    )
    lifted_regions = root / "array_sites_grch38_regions.tsv"
    lifted_regions.write_text(
        "".join(
            f"chr{number}\t{position}\t{position}\n"
            for number in range(1, 23)
            for position in (100, 200)
        )
    )

    chromosome_files: dict[str, tuple[Path, Path]] = {}
    for chromosome in (f"chr{number}" for number in range(1, 23)):
        vcf = root / f"ref_panel_{chromosome}.vcf.gz"
        index = root / f"ref_panel_{chromosome}.vcf.gz.tbi"
        _write_gzip(vcf, _vcf(chromosome))
        index.write_bytes(f"index-{chromosome}\n".encode())
        chromosome_files[chromosome] = (vcf, index)

    return ManifestInputs(
        root=root,
        metadata=metadata,
        selected=selected,
        full_map=full_map,
        training_map=training_map,
        heldout_map=heldout_map,
        union_catalog=union_catalog,
        lifted_regions=lifted_regions,
        chromosome_files=chromosome_files,
    )


def _input_kwargs(inputs: ManifestInputs) -> dict[str, object]:
    return {
        "reference_build": "GRCh38",
        "reference_panel_name": "gnomAD HGDP+1KG v3.1.2",
        "reference_panel_source": "gs://public.example/hgdp-1kg",
        "metadata_path": inputs.metadata,
        "metadata_source": "gs://public.example/gnomad_meta_updated.tsv",
        "selected_samples_path": inputs.selected,
        "full_sample_map_path": inputs.full_map,
        "training_sample_map_path": inputs.training_map,
        "heldout_sample_map_path": inputs.heldout_map,
        "marker_sources": inputs.marker_sources,
        "chromosome_files": inputs.chromosome_files,
    }


def _write_input(module, inputs: ManifestInputs, name: str = "training-inputs.json") -> Path:
    output = inputs.root / name
    module.write_input_manifest(output, **_input_kwargs(inputs))
    return output


def _write_canonical(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _split_files(root: Path) -> dict[str, Path]:
    split_dir = root / "sample_maps"
    split_dir.mkdir()
    files = {
        "train1": split_dir / "train1.map",
        "train2": split_dir / "train2.map",
        "val": split_dir / "val.map",
    }
    files["train1"].write_text("S1\tAFR\nS2\tAFR\n")
    # Upstream Gnomix may deliberately duplicate a small-population founder
    # between train1 and train2. The manifest must record, not erase, that fact.
    files["train2"].write_text("S2\tAFR\n")
    files["val"].write_text("S3\tEUR\n")
    return files


def _cli_input_args(inputs: ManifestInputs) -> list[str]:
    args = [
        "--reference-build",
        "GRCh38",
        "--reference-panel-name",
        "gnomAD HGDP+1KG v3.1.2",
        "--reference-panel-source",
        "gs://public.example/hgdp-1kg",
        "--metadata",
        str(inputs.metadata),
        "--metadata-source",
        "gs://public.example/gnomad_meta_updated.tsv",
        "--selected-samples",
        str(inputs.selected),
        "--full-sample-map",
        str(inputs.full_map),
        "--training-sample-map",
        str(inputs.training_map),
        "--heldout-sample-map",
        str(inputs.heldout_map),
    ]
    for name, path in inputs.marker_sources.items():
        args.extend(("--marker-source", f"{name}={path}"))
    for chromosome, (vcf, index) in inputs.chromosome_files.items():
        args.extend(("--chromosome-file", f"{chromosome}={vcf},{index}"))
    return args


def test_input_manifest_is_canonical_and_path_independent(tmp_path: Path) -> None:
    module = _load_module()
    first = _make_inputs(tmp_path / "first")
    second = _make_inputs(tmp_path / "second")

    first_path = _write_input(module, first)
    second_path = _write_input(module, second)

    assert first_path.read_bytes() == second_path.read_bytes()
    contract = module.load_input_manifest(first_path)
    assert contract.sha256 == hashlib.sha256(first_path.read_bytes()).hexdigest()
    assert contract.payload["reference_build"] == "GRCh38"
    assert contract.payload["manifest_type"] == "yeliztli_gnomix_training_inputs"
    assert contract.payload["sample_identifier_policy"] == "public_reference_panel_ids"
    assert [entry["chromosome"] for entry in contract.payload["chromosomes"]] == [
        f"chr{number}" for number in range(1, 23)
    ]
    assert contract.payload["sample_mappings"]["counts"] == {
        "full": 4,
        "heldout_test": 1,
        "training": 3,
    }
    assert [
        (entry["sample_id"], entry["population"], entry["superpopulation"], entry["role"])
        for entry in contract.payload["sample_mappings"]["members"]
    ] == [
        ("S1", "Pop1", "AFR", "training"),
        ("S2", "Pop2", "AFR", "training"),
        ("S3", "Pop3", "EUR", "training"),
        ("S4", "Pop4", "EUR", "heldout_test"),
    ]
    assert not any(str(first.root) in value for value in first_path.read_text().splitlines())


def test_input_manifest_binds_exact_panel_samples_markers_and_raw_artifacts(
    tmp_path: Path,
) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    manifest_path = _write_input(module, inputs)
    payload = module.load_input_manifest(manifest_path).payload

    chromosome = payload["chromosomes"][0]
    assert (
        chromosome["vcf"]["sha256"]
        == hashlib.sha256(inputs.chromosome_files["chr1"][0].read_bytes()).hexdigest()
    )
    assert (
        chromosome["index"]["sha256"]
        == hashlib.sha256(inputs.chromosome_files["chr1"][1].read_bytes()).hexdigest()
    )
    assert chromosome["samples"]["count"] == 4
    assert chromosome["markers"]["count"] == 2
    assert (
        chromosome["samples"]["ordered_sha256"]
        == hashlib.sha256(b'["S1","S2","S3","S4"]').hexdigest()
    )
    expected_markers = [
        ["chr1", 100, "rs1a", "A", "G"],
        ["chr1", 200, "rs1b", "C", "T"],
    ]
    assert (
        chromosome["markers"]["ordered_sha256"]
        == hashlib.sha256(json.dumps(expected_markers, separators=(",", ":")).encode()).hexdigest()
    )


def test_input_manifest_requires_all_22_autosomes(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    kwargs = _input_kwargs(inputs)
    kwargs["chromosome_files"] = {
        chromosome: paths
        for chromosome, paths in inputs.chromosome_files.items()
        if chromosome != "chr22"
    }

    with pytest.raises(module.ManifestError, match="exactly chr1-chr22"):
        module.write_input_manifest(inputs.root / "training-inputs.json", **kwargs)


def test_input_manifest_requires_selected_labels_to_match_reference_metadata(
    tmp_path: Path,
) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    inputs.selected.write_text(inputs.selected.read_text().replace("S1\tPop1\t", "S1\tWrong\t"))

    with pytest.raises(module.ManifestError, match="disagrees with metadata"):
        _write_input(module, inputs)


def test_input_manifest_requires_vcf_markers_to_descend_from_lifted_regions(
    tmp_path: Path,
) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    inputs.lifted_regions.write_text(
        inputs.lifted_regions.read_text().replace("chr1\t200\t200\n", "")
    )

    with pytest.raises(module.ManifestError, match="absent from.*lifted"):
        _write_input(module, inputs)


@pytest.mark.parametrize(
    "artifact",
    [
        "vcf",
        "index",
        "metadata",
        "selected",
        "full_map",
        "training_map",
        "heldout_map",
        "union_catalog",
        "lifted_regions",
    ],
)
def test_input_verification_fails_closed_on_every_artifact_drift(
    tmp_path: Path, artifact: str
) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    manifest_path = _write_input(module, inputs)

    if artifact == "vcf":
        _write_gzip(inputs.chromosome_files["chr1"][0], _vcf("chr1", second_position=201))
    elif artifact == "index":
        inputs.chromosome_files["chr1"][1].write_bytes(b"changed-index\n")
    else:
        path = getattr(inputs, artifact)
        path.write_bytes(path.read_bytes() + b"# drift\n")

    with pytest.raises(module.ManifestError):
        module.verify_input_manifest(manifest_path, **_input_kwargs(inputs))


@pytest.mark.parametrize(
    "artifact",
    [
        "vcf",
        "index",
        "metadata",
        "selected",
        "full_map",
        "training_map",
        "heldout_map",
        "union_catalog",
        "lifted_regions",
    ],
)
@pytest.mark.parametrize("failure", ["missing", "empty"])
def test_input_manifest_rejects_every_missing_or_empty_required_artifact(
    tmp_path: Path, artifact: str, failure: str
) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    if artifact == "vcf":
        path = inputs.chromosome_files["chr1"][0]
    elif artifact == "index":
        path = inputs.chromosome_files["chr1"][1]
    else:
        path = getattr(inputs, artifact)
    if failure == "missing":
        path.unlink()
    else:
        path.write_bytes(b"")

    with pytest.raises(module.ManifestError, match="does not exist|empty"):
        _write_input(module, inputs)


def test_subset_verification_only_rehashes_requested_chromosomes(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    manifest_path = _write_input(module, inputs)
    inputs.chromosome_files["chr2"][0].unlink()
    inputs.chromosome_files["chr2"][1].unlink()

    module.verify_input_manifest(manifest_path, chromosomes=["chr1"], **_input_kwargs(inputs))
    with pytest.raises(module.ManifestError):
        module.verify_input_manifest(manifest_path, **_input_kwargs(inputs))


@pytest.mark.parametrize("subset", [[], ["1", "chr1"], ["chr23"]])
def test_subset_verification_rejects_empty_duplicate_or_unknown_chromosomes(
    tmp_path: Path, subset: list[str]
) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    manifest_path = _write_input(module, inputs)

    with pytest.raises(module.ManifestError):
        module.verify_input_manifest(
            manifest_path,
            chromosomes=subset,
            **_input_kwargs(inputs),
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("overlap", "training.*heldout|heldout.*training|partition"),
        ("omission", "partition|account"),
        ("unknown", "selected|unknown"),
        ("duplicate_selected", "duplicate"),
        ("duplicate_full", "duplicate"),
        ("duplicate_training", "duplicate"),
        ("duplicate_heldout", "duplicate"),
        ("label", "superpopulation|label"),
        ("vcf_missing", "VCF|vcf|reference"),
    ],
)
def test_input_manifest_rejects_invalid_mapping_contracts(
    tmp_path: Path, change: str, message: str
) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    if change == "overlap":
        inputs.training_map.write_text("S1\tAFR\nS2\tAFR\nS3\tEUR\nS4\tEUR\n")
    elif change == "omission":
        inputs.training_map.write_text("S1\tAFR\nS2\tAFR\n")
    elif change == "unknown":
        inputs.full_map.write_text(inputs.full_map.read_text() + "S5\tAFR\n")
    elif change == "duplicate_selected":
        inputs.selected.write_text(inputs.selected.read_text() + "S1\tPop1\tAFR\n")
    elif change == "duplicate_full":
        inputs.full_map.write_text(inputs.full_map.read_text() + "S1\tAFR\n")
    elif change == "duplicate_training":
        inputs.training_map.write_text(inputs.training_map.read_text() + "S1\tAFR\n")
    elif change == "duplicate_heldout":
        inputs.heldout_map.write_text(inputs.heldout_map.read_text() + "S4\tEUR\n")
    elif change == "label":
        inputs.training_map.write_text("S1\tEUR\nS2\tAFR\nS3\tEUR\n")
    else:
        for chromosome, (_vcf_path, index) in inputs.chromosome_files.items():
            vcf_path = inputs.root / f"ref_panel_{chromosome}.vcf.gz"
            # Missing an interior IID exercises exact membership validation;
            # upstream's searchsorted lookup can otherwise select the next sample.
            _write_gzip(vcf_path, _vcf(chromosome, samples=("S1", "S3", "S4")))
            inputs.chromosome_files[chromosome] = (vcf_path, index)

    with pytest.raises(module.ManifestError, match=message):
        _write_input(module, inputs)


@pytest.mark.parametrize("corruption", ["duplicate_sample", "duplicate_marker"])
def test_input_manifest_rejects_ambiguous_vcf_samples_and_markers(
    tmp_path: Path, corruption: str
) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    vcf_path, index_path = inputs.chromosome_files["chr1"]
    if corruption == "duplicate_sample":
        text = _vcf("chr1", samples=("S1", "S2", "S2", "S3", "S4"))
    else:
        text = _vcf("chr1")
        text += text.splitlines()[-1] + "\n"
    _write_gzip(vcf_path, text)
    inputs.chromosome_files["chr1"] = (vcf_path, index_path)

    with pytest.raises(module.ManifestError, match="unique|duplicate"):
        _write_input(module, inputs)


def test_input_manifest_normalizes_corrupt_deflate_errors(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    vcf_path, _index_path = inputs.chromosome_files["chr1"]
    vcf_path.write_bytes(b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03not-a-deflate-stream")

    with pytest.raises(module.ManifestError, match="invalid gzip/BGZF stream"):
        _write_input(module, inputs)


def test_input_manifest_loader_rejects_extra_fields_and_noncanonical_json(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    manifest_path = _write_input(module, inputs)
    payload = json.loads(manifest_path.read_text())

    payload["unexpected"] = True
    manifest_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    with pytest.raises(module.ManifestError, match="unexpected|fields"):
        module.load_input_manifest(manifest_path)

    payload.pop("unexpected")
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    with pytest.raises(module.ManifestError, match="canonical"):
        module.load_input_manifest(manifest_path)


def test_input_manifest_loader_rejects_malformed_embedded_checksums(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    manifest_path = _write_input(module, inputs)
    payload = json.loads(manifest_path.read_text())
    payload["chromosomes"][0]["vcf"]["sha256"] = "not-a-checksum"
    manifest_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))

    with pytest.raises(module.ManifestError, match="SHA|sha|checksum"):
        module.load_input_manifest(manifest_path)


def test_input_manifest_loader_rejects_nested_extra_fields_and_duplicate_json_keys(
    tmp_path: Path,
) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    manifest_path = _write_input(module, inputs)
    payload = json.loads(manifest_path.read_text())
    payload["chromosomes"][0]["vcf"]["unexpected"] = True
    manifest_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    with pytest.raises(module.ManifestError, match="unexpected|fields"):
        module.load_input_manifest(manifest_path)

    manifest_path.write_text(
        '{"manifest_type":"yeliztli_gnomix_training_inputs",'
        '"manifest_type":"yeliztli_gnomix_training_inputs"}'
    )
    with pytest.raises(module.ManifestError, match="duplicate key"):
        module.load_input_manifest(manifest_path)


@pytest.mark.parametrize(
    "corruption",
    [
        "boolean_schema",
        "float_schema",
        "identifier_policy",
        "missing_chromosome",
        "missing_required_marker_source",
        "short_chromosome_sample_count",
        "non_string_role",
    ],
)
def test_input_manifest_loader_rejects_semantically_invalid_canonical_payloads(
    tmp_path: Path, corruption: str
) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    manifest_path = _write_input(module, inputs)
    payload = json.loads(manifest_path.read_text())
    if corruption == "boolean_schema":
        payload["schema_version"] = True
    elif corruption == "float_schema":
        payload["schema_version"] = 1.0
    elif corruption == "identifier_policy":
        payload["sample_identifier_policy"] = "private_unprojected_ids"
    elif corruption == "missing_chromosome":
        payload["chromosomes"].pop()
    elif corruption == "missing_required_marker_source":
        payload["source_artifacts"]["marker_selection"] = [
            entry
            for entry in payload["source_artifacts"]["marker_selection"]
            if entry["name"] != "union_catalog"
        ]
    elif corruption == "short_chromosome_sample_count":
        payload["chromosomes"][0]["samples"]["count"] = 3
    else:
        payload["sample_mappings"]["members"][0]["role"] = True
    _write_canonical(manifest_path, payload)

    with pytest.raises(module.ManifestError):
        module.load_input_manifest(manifest_path)


def test_manifest_inputs_must_be_regular_files_not_symlinks(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    real_training_map = inputs.root / "real-sample-map.txt"
    inputs.training_map.replace(real_training_map)
    inputs.training_map.symlink_to(real_training_map)

    with pytest.raises(module.ManifestError, match="symlink|regular file"):
        _write_input(module, inputs)


def test_manifest_output_must_not_alias_an_input_or_be_a_broken_symlink(
    tmp_path: Path,
) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    hardlink_output = inputs.root / "metadata-alias.json"
    hardlink_output.hardlink_to(inputs.metadata)

    with pytest.raises(module.ManifestError, match="aliases"):
        module.write_input_manifest(hardlink_output, **_input_kwargs(inputs))

    broken_output = inputs.root / "broken-output.json"
    broken_output.symlink_to(inputs.root / "missing-target.json")
    with pytest.raises(module.ManifestError, match="regular file"):
        module.write_input_manifest(broken_output, **_input_kwargs(inputs))
    assert broken_output.is_symlink()


def test_training_inputs_must_not_be_hard_link_aliases(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    _chr1_vcf, chr1_index = inputs.chromosome_files["chr1"]
    chr2_vcf, chr2_index = inputs.chromosome_files["chr2"]
    chr2_index.unlink()
    chr2_index.hardlink_to(chr1_index)
    inputs.chromosome_files["chr2"] = (chr2_vcf, chr2_index)

    with pytest.raises(module.ManifestError, match="inputs alias the same file"):
        _write_input(module, inputs)


def test_source_uris_cannot_embed_credentials_or_signed_queries(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    kwargs = _input_kwargs(inputs)
    kwargs["metadata_source"] = "https://token@example.test/metadata.tsv?signature=secret"

    with pytest.raises(module.ManifestError, match="credentials|query"):
        module.write_input_manifest(inputs.root / "manifest.json", **kwargs)


def test_manifest_publication_failure_preserves_prior_generation(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    manifest_path = _write_input(module, inputs)
    prior = manifest_path.read_bytes()
    inputs.metadata.write_bytes(b"")

    with pytest.raises(module.ManifestError):
        module.write_input_manifest(manifest_path, **_input_kwargs(inputs))

    assert manifest_path.read_bytes() == prior


def test_manifest_replace_is_atomic_and_cleans_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    manifest_path = _write_input(module, inputs)
    prior = manifest_path.read_bytes()
    original_replace = Path.replace

    def interrupt_replace(path: Path, target: Path) -> Path:
        if target == manifest_path:
            raise OSError("simulated manifest publication interruption")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", interrupt_replace)
    with pytest.raises(OSError, match="simulated manifest publication interruption"):
        module.write_input_manifest(manifest_path, **_input_kwargs(inputs))

    assert manifest_path.read_bytes() == prior
    assert not list(inputs.root.glob(f".{manifest_path.name}.*.tmp"))


def test_manifest_publication_rechecks_inputs_after_all_chromosomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    output = inputs.root / "training-inputs.json"
    original = module._chromosome_payload

    def mutate_after_first_chromosome(*args, **kwargs):
        payload = original(*args, **kwargs)
        if args[0] == "chr1":
            inputs.metadata.write_text(inputs.metadata.read_text() + "S5\tPop5\tSAS\n")
        return payload

    monkeypatch.setattr(module, "_chromosome_payload", mutate_after_first_chromosome)
    with pytest.raises(module.ManifestError, match="changed"):
        module.write_input_manifest(output, **_input_kwargs(inputs))
    assert not output.exists()


@pytest.mark.parametrize(
    ("limit_name", "limit", "message"),
    [
        ("_MAX_VCF_LINE_BYTES", 10, "line exceeds safety limit"),
        ("_MAX_VCF_DECOMPRESSED_BYTES", 20, "decompressed data exceeds safety limit"),
        ("_MAX_VCF_MARKERS_PER_CHROMOSOME", 1, "marker count exceeds safety limit"),
    ],
)
def test_vcf_parser_enforces_resource_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    limit: int,
    message: str,
) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    monkeypatch.setattr(module, limit_name, limit)

    with pytest.raises(module.ManifestError, match=message):
        _write_input(module, inputs)


def test_split_manifest_records_actual_internal_and_external_membership(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    input_manifest = _write_input(module, inputs)
    split_files = _split_files(inputs.root)
    split_manifest = inputs.root / "training-splits.json"

    module.write_split_manifest(
        split_manifest,
        input_manifest_path=input_manifest,
        split_files=split_files,
    )

    contract = module.load_split_manifest(split_manifest)
    assert contract.payload["manifest_type"] == "yeliztli_gnomix_training_splits"
    assert contract.payload["training_input_manifest"] == {
        "schema_version": 1,
        "sha256": hashlib.sha256(input_manifest.read_bytes()).hexdigest(),
    }
    assert [entry["name"] for entry in contract.payload["internal_splits"]] == [
        "train1",
        "train2",
        "val",
    ]
    assert contract.payload["train1_train2_overlap"] == ["S2"]
    assert contract.payload["external_test"]["members"] == [
        {"sample_id": "S4", "superpopulation": "EUR"}
    ]


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("root", True),
        ("root", 1.0),
        ("input", True),
        ("input", 1.0),
    ],
)
def test_split_manifest_loader_requires_integer_schema_versions(
    tmp_path: Path, field: str, bad_value: object
) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    input_manifest = _write_input(module, inputs)
    split_files = _split_files(inputs.root)
    split_manifest = inputs.root / "training-splits.json"
    module.write_split_manifest(
        split_manifest,
        input_manifest_path=input_manifest,
        split_files=split_files,
    )
    payload = json.loads(split_manifest.read_text())
    if field == "root":
        payload["schema_version"] = bad_value
    else:
        payload["training_input_manifest"]["schema_version"] = bad_value
    _write_canonical(split_manifest, payload)

    with pytest.raises(module.ManifestError, match="schema"):
        module.load_split_manifest(split_manifest)


def test_split_manifest_loader_rejects_inconsistent_overlap_labels(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    input_manifest = _write_input(module, inputs)
    split_files = _split_files(inputs.root)
    split_manifest = inputs.root / "training-splits.json"
    module.write_split_manifest(
        split_manifest,
        input_manifest_path=input_manifest,
        split_files=split_files,
    )
    payload = json.loads(split_manifest.read_text())
    payload["internal_splits"][1]["members"][0]["superpopulation"] = "EUR"
    _write_canonical(split_manifest, payload)

    with pytest.raises(module.ManifestError, match="inconsistent superpopulation"):
        module.load_split_manifest(split_manifest)


@pytest.mark.parametrize(
    ("split", "contents", "message"),
    [
        ("train1", "S1\tAFR\nS2\tAFR\nS4\tEUR\n", "heldout|test"),
        ("train1", "S1\tAFR\nS2\tAFR\nUNKNOWN\tAFR\n", "unknown|training"),
        ("train1", "S1\tEUR\nS2\tAFR\n", "superpopulation|label"),
        ("train1", "S1\tAFR\nS2\tAFR\nS2\tAFR\n", "duplicate"),
        ("val", "S2\tAFR\nS3\tEUR\n", "overlap|val"),
        ("val", "", "empty"),
    ],
)
def test_split_manifest_rejects_leaks_unknowns_duplicates_and_invalid_overlap(
    tmp_path: Path, split: str, contents: str, message: str
) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    input_manifest = _write_input(module, inputs)
    split_files = _split_files(inputs.root)
    split_files[split].write_text(contents)

    with pytest.raises(module.ManifestError, match=message):
        module.write_split_manifest(
            inputs.root / "training-splits.json",
            input_manifest_path=input_manifest,
            split_files=split_files,
        )


def test_split_manifest_requires_internal_splits_to_cover_every_training_member(
    tmp_path: Path,
) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    input_manifest = _write_input(module, inputs)
    split_files = _split_files(inputs.root)
    split_files["train1"].write_text("S1\tAFR\n")
    split_files["train2"].write_text("S1\tAFR\n")
    split_files["val"].write_text("S2\tAFR\n")

    with pytest.raises(module.ManifestError, match="cover|missing"):
        module.write_split_manifest(
            inputs.root / "training-splits.json",
            input_manifest_path=input_manifest,
            split_files=split_files,
        )


@pytest.mark.parametrize("contract_error", ["missing", "unexpected", "aliased", "hardlinked"])
def test_split_manifest_requires_three_distinct_exact_split_files(
    tmp_path: Path, contract_error: str
) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    input_manifest = _write_input(module, inputs)
    split_files = _split_files(inputs.root)
    if contract_error == "missing":
        split_files.pop("val")
    elif contract_error == "unexpected":
        extra = inputs.root / "sample_maps" / "test.map"
        extra.write_text("S3\tEUR\n")
        split_files["test"] = extra
    elif contract_error == "aliased":
        split_files["train2"] = split_files["train1"]
    else:
        split_files["train2"].unlink()
        split_files["train2"].hardlink_to(split_files["train1"])

    with pytest.raises(module.ManifestError, match="exactly|different|alias"):
        module.write_split_manifest(
            inputs.root / "training-splits.json",
            input_manifest_path=input_manifest,
            split_files=split_files,
        )


def test_split_manifest_output_must_not_alias_its_input_manifest(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    input_manifest = _write_input(module, inputs)
    split_files = _split_files(inputs.root)

    with pytest.raises(module.ManifestError, match="aliases"):
        module.write_split_manifest(
            input_manifest,
            input_manifest_path=input_manifest,
            split_files=split_files,
        )


def test_split_verification_rejects_membership_or_input_manifest_drift(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _make_inputs(tmp_path / "inputs")
    input_manifest = _write_input(module, inputs)
    split_files = _split_files(inputs.root)
    split_manifest = inputs.root / "training-splits.json"
    module.write_split_manifest(
        split_manifest,
        input_manifest_path=input_manifest,
        split_files=split_files,
    )

    split_files["train1"].write_text("S2\tAFR\nS1\tAFR\n")
    with pytest.raises(module.ManifestError):
        module.verify_split_manifest(
            split_manifest,
            input_manifest_path=input_manifest,
            split_files=split_files,
        )

    split_files["train1"].write_text("S1\tAFR\nS2\tAFR\n")
    inputs.metadata.write_text(inputs.metadata.read_text() + "S5\tPop5\tSAS\n")
    changed_input_manifest = _write_input(module, inputs, "changed-training-inputs.json")
    with pytest.raises(module.ManifestError):
        module.verify_split_manifest(
            split_manifest,
            input_manifest_path=changed_input_manifest,
            split_files=split_files,
        )


def test_cli_creates_and_verifies_both_canonical_manifests(tmp_path: Path) -> None:
    inputs = _make_inputs(tmp_path / "inputs")
    input_manifest = inputs.root / "training-inputs.json"
    create_input = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "create-input",
            "--output",
            str(input_manifest),
            *_cli_input_args(inputs),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert create_input.returncode == 0, create_input.stderr
    assert "sha256=" in create_input.stdout

    verify_input = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "verify-input",
            "--manifest",
            str(input_manifest),
            "--chromosomes",
            "chr1",
            *_cli_input_args(inputs),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert verify_input.returncode == 0, verify_input.stderr

    split_files = _split_files(inputs.root)
    split_manifest = inputs.root / "training-splits.json"
    split_args = [
        value for name, path in split_files.items() for value in ("--split-file", f"{name}={path}")
    ]
    create_splits = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "create-splits",
            "--output",
            str(split_manifest),
            "--input-manifest",
            str(input_manifest),
            *split_args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert create_splits.returncode == 0, create_splits.stderr

    verify_splits = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "verify-splits",
            "--manifest",
            str(split_manifest),
            "--input-manifest",
            str(input_manifest),
            *split_args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert verify_splits.returncode == 0, verify_splits.stderr
