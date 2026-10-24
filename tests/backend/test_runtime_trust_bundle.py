"""Packaged requirements keep existing installs on a supported TLS bundle."""

import importlib.metadata
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def test_runtime_metadata_rejects_legacy_bundle_and_accepts_locked_release():
    # Inspect the installed project's production metadata, which pip consumes
    # during upgrades, rather than only the resolver's independent lockfile.
    requires = importlib.metadata.requires("yeliztli")
    assert requires, "installed project has no dependency metadata"
    bundles = [
        requirement
        for raw in requires
        if canonicalize_name((requirement := Requirement(raw)).name) == "certifi"
        and requirement.marker is None
    ]
    assert len(bundles) == 1, "all installation paths need an unconditional bundle requirement"
    lock = tomllib.loads((Path(__file__).resolve().parents[2] / "uv.lock").read_text())
    locked = [package["version"] for package in lock["package"] if package["name"] == "certifi"]
    assert len(locked) == 1
    assert "2026.6.17" not in bundles[0].specifier
    assert locked[0] in bundles[0].specifier
