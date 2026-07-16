from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "lai_bundle_v2" / "gnomix_environment.py"
SOURCE_SPEC = REPO_ROOT / "scripts" / "lai_bundle_v2" / "gnomix-training-environment.yml"
COMMITTED_LOCK = (
    REPO_ROOT / "scripts" / "lai_bundle_v2" / "gnomix-training-environment.conda-lock.yml"
)
ENV_SH = REPO_ROOT / "scripts" / "lai_bundle_v2" / "env.sh"
MODULE_SPEC = importlib.util.spec_from_file_location("gnomix_environment_test", SCRIPT)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
environment = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = environment
MODULE_SPEC.loader.exec_module(environment)

_PACKAGES = (
    {
        "name": "python",
        "version": "3.11.11",
        "manager": "conda",
        "platform": "linux-64",
        "url": "https://conda.example/conda-forge/linux-64/python-3.11.11-h9e4cc4f_2.conda",
        "hash": {"sha256": "1" * 64},
    },
    {
        "name": "numpy",
        "version": "2.2.2",
        "manager": "conda",
        "platform": "linux-64",
        "url": "https://conda.example/conda-forge/linux-64/numpy-2.2.2-py311h5d046bc_0.conda",
        "hash": {"sha256": "2" * 64},
    },
)


def _write_lock(
    tmp_path: Path,
    *,
    packages: tuple[dict[str, object], ...] = _PACKAGES,
    platforms: list[str] | None = None,
) -> Path:
    path = tmp_path / "gnomix-training-environment.conda-lock.yml"
    document = {
        "version": 1,
        "metadata": {"platforms": platforms or ["linux-64"]},
        "package": list(packages),
    }
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _json_inventory(
    packages: tuple[dict[str, object], ...] = _PACKAGES,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for package in packages:
        filename = str(package["url"]).rsplit("/", 1)[-1].removesuffix(".conda")
        prefix = f"{package['name']}-{package['version']}-"
        records.append(
            {
                "name": package["name"],
                "version": package["version"],
                "build_string": filename.removeprefix(prefix),
                "channel": "conda-forge",
                "platform": "linux-64",
            }
        )
    return records


def _explicit_inventory(packages: tuple[dict[str, object], ...] = _PACKAGES) -> str:
    return (
        "@EXPLICIT\n"
        + "\n".join(
            f"{package['url']}#{package['hash']['sha256']}"  # type: ignore[index]
            for package in packages
        )
        + "\n"
    )


def _fake_conda(
    *,
    explicit: str | None = None,
    inventory: list[dict[str, object]] | None = None,
    on_call: Callable[[list[str]], None] | None = None,
) -> tuple[Callable[..., subprocess.CompletedProcess[str]], list[list[str]]]:
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs.keys() == {"check", "capture_output", "text", "env"}
        assert kwargs["check"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        clean_environment = kwargs["env"]
        assert isinstance(clean_environment, dict)
        assert not environment._AMBIENT_INJECTION_VARIABLES & clean_environment.keys()
        calls.append(command)
        if on_call is not None:
            on_call(command)
        stdout = (
            explicit if "--explicit" in command else json.dumps(inventory or _json_inventory())
        )
        if stdout is None:
            stdout = _explicit_inventory()
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    return run, calls


def test_load_lock_identity_returns_raw_artifact_digest(tmp_path: Path) -> None:
    lock = _write_lock(tmp_path)
    expected = hashlib.sha256(lock.read_bytes()).hexdigest()

    identity = environment.load_lock_identity(
        lock,
        platform="linux-64",
        expected_lock_sha256=expected,
    )

    assert identity == {
        "schema_version": 1,
        "artifact_kind": "conda-lock",
        "platform": "linux-64",
        "lock_sha256": expected,
    }


def test_committed_lock_matches_pin_source_and_strict_schema() -> None:
    match = re.search(
        r"GNOMIX_ENV_LOCK_SHA256:=([0-9a-f]{64})",
        ENV_SH.read_text(encoding="utf-8"),
    )
    assert match is not None
    pinned_sha256 = match.group(1)

    identity = environment.load_lock_identity(
        COMMITTED_LOCK,
        platform="linux-64",
        expected_lock_sha256=pinned_sha256,
    )
    assert identity["lock_sha256"] == pinned_sha256

    document = yaml.safe_load(COMMITTED_LOCK.read_text(encoding="utf-8"))
    source_metadata = document["metadata"]["inputs_metadata"][SOURCE_SPEC.name]
    assert source_metadata["sha256"] == hashlib.sha256(SOURCE_SPEC.read_bytes()).hexdigest()


def test_verify_environment_matches_explicit_urls_and_json_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _write_lock(tmp_path)
    fake_run, calls = _fake_conda(explicit=_explicit_inventory())
    monkeypatch.setattr(environment.subprocess, "run", fake_run)
    monkeypatch.setenv("PYTHONPATH", "/tmp/host-shadow")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/native-shadow")

    identity = environment.verify_environment(
        lock,
        platform="linux-64",
        conda_env="gnomix-training",
        conda_executable="/opt/conda/bin/conda",
    )

    assert identity["lock_sha256"] == hashlib.sha256(lock.read_bytes()).hexdigest()
    assert calls == [
        [
            "/opt/conda/bin/conda",
            "list",
            "-n",
            "gnomix-training",
            "--explicit",
            "--sha256",
        ],
        ["/opt/conda/bin/conda", "list", "-n", "gnomix-training", "--json"],
    ]


def test_lock_rejects_missing_sha256(tmp_path: Path) -> None:
    packages = tuple(dict(package) for package in _PACKAGES)
    packages[0]["hash"] = {"md5": "a" * 32}
    lock = _write_lock(tmp_path, packages=packages)

    with pytest.raises(environment.EnvironmentVerificationError, match="lowercase SHA256"):
        environment.load_lock_identity(lock, platform="linux-64")


def test_lock_rejects_wrong_platform(tmp_path: Path) -> None:
    lock = _write_lock(tmp_path)

    with pytest.raises(environment.EnvironmentVerificationError, match="unsupported.*win-64"):
        environment.load_lock_identity(lock, platform="win-64")

    wrong_lock = _write_lock(tmp_path, platforms=["linux-64", "osx-64"])
    with pytest.raises(environment.EnvironmentVerificationError, match="exactly.*linux-64"):
        environment.load_lock_identity(wrong_lock, platform="linux-64")


def test_lock_rejects_duplicate_yaml_key(tmp_path: Path) -> None:
    lock = _write_lock(tmp_path)
    text = lock.read_text(encoding="utf-8")
    lock.write_text(text.replace("version: 1\n", "version: 1\nversion: 1\n", 1))

    with pytest.raises(environment.EnvironmentVerificationError, match="duplicate key 'version'"):
        environment.load_lock_identity(lock, platform="linux-64")


def test_lock_rejects_pip_manager_entry(tmp_path: Path) -> None:
    packages = tuple(dict(package) for package in _PACKAGES)
    packages[0]["manager"] = "pip"
    lock = _write_lock(tmp_path, packages=packages)

    with pytest.raises(environment.EnvironmentVerificationError, match="manager='conda'.*PyPI"):
        environment.load_lock_identity(lock, platform="linux-64")


@pytest.mark.parametrize(
    "url",
    [
        "http://conda.example/linux-64/python-3.11.11-h9e4cc4f_2.conda",
        "https://conda.example/linux-64/python-3.11.11-.conda",
        "https://conda.example/linux-64/python-3.11.11-h9e4cc4f_2.conda?token=mutable",
    ],
)
def test_lock_rejects_malformed_or_mutable_url(tmp_path: Path, url: str) -> None:
    packages = tuple(dict(package) for package in _PACKAGES)
    packages[0]["url"] = url
    lock = _write_lock(tmp_path, packages=packages)

    with pytest.raises(
        environment.EnvironmentVerificationError,
        match="(?:immutable HTTPS|build)",
    ):
        environment.load_lock_identity(lock, platform="linux-64")


def test_lock_rejects_expected_digest_drift(tmp_path: Path) -> None:
    lock = _write_lock(tmp_path)

    with pytest.raises(environment.EnvironmentVerificationError, match="lock digest drift"):
        environment.load_lock_identity(
            lock,
            platform="linux-64",
            expected_lock_sha256="f" * 64,
        )


@pytest.mark.parametrize("case", ["missing", "extra", "url-drift", "digest-drift"])
def test_verify_rejects_explicit_package_inventory_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    lock = _write_lock(tmp_path)
    artifacts = [
        f"{package['url']}#{package['hash']['sha256']}"  # type: ignore[index]
        for package in _PACKAGES
    ]
    if case == "missing":
        artifacts.pop()
    elif case == "extra":
        artifacts.append(
            "https://conda.example/linux-64/scipy-1.15.0-py311h1a2b3c4_0.conda#" + "3" * 64
        )
    elif case == "url-drift":
        artifacts[0] = artifacts[0].replace("h9e4cc4f_2", "h9e4cc4f_3")
    else:
        artifacts[0] = artifacts[0][:-64] + "f" * 64
    fake_run, _ = _fake_conda(explicit="@EXPLICIT\n" + "\n".join(artifacts) + "\n")
    monkeypatch.setattr(environment.subprocess, "run", fake_run)

    with pytest.raises(
        environment.EnvironmentVerificationError,
        match="package inventory drift",
    ):
        environment.verify_environment(
            lock,
            platform="linux-64",
            conda_env="gnomix-training",
        )


def test_verify_rejects_pypi_json_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _write_lock(tmp_path)
    inventory = _json_inventory()
    inventory.append(
        {
            "name": "unlocked-wheel",
            "version": "1.0",
            "build_string": "pypi_0",
            "channel": "pypi",
            "platform": "pypi",
        }
    )
    fake_run, _ = _fake_conda(explicit=_explicit_inventory(), inventory=inventory)
    monkeypatch.setattr(environment.subprocess, "run", fake_run)

    with pytest.raises(environment.EnvironmentVerificationError, match="pip/PyPI package"):
        environment.verify_environment(
            lock,
            platform="linux-64",
            conda_env="gnomix-training",
        )


def test_verify_rejects_json_package_inventory_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _write_lock(tmp_path)
    inventory = _json_inventory()
    inventory[0]["build_string"] = "different_build"
    fake_run, _ = _fake_conda(explicit=_explicit_inventory(), inventory=inventory)
    monkeypatch.setattr(environment.subprocess, "run", fake_run)

    with pytest.raises(
        environment.EnvironmentVerificationError,
        match="JSON package inventory drift",
    ):
        environment.verify_environment(
            lock,
            platform="linux-64",
            conda_env="gnomix-training",
        )


def test_verify_rejects_lock_changed_while_conda_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = _write_lock(tmp_path)
    calls = 0

    def mutate_on_second_call(_command: list[str]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            lock.write_bytes(lock.read_bytes() + b"# concurrent change\n")

    fake_run, _ = _fake_conda(explicit=_explicit_inventory(), on_call=mutate_on_second_call)
    monkeypatch.setattr(environment.subprocess, "run", fake_run)

    with pytest.raises(environment.EnvironmentVerificationError, match="changed during"):
        environment.verify_environment(
            lock,
            platform="linux-64",
            conda_env="gnomix-training",
        )


def test_cli_validation_failure_is_clean_exit_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lock = _write_lock(tmp_path)

    result = environment.main(["identity", "--lock", str(lock), "--platform", "osx-64"])

    assert result == 1
    assert capsys.readouterr().err.startswith("error: unsupported")
