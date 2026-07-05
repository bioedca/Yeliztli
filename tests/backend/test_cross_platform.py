"""Cross-platform build verification tests (P4-25).

Tests that the project installs and runs correctly across
macOS (ARM/x86), Linux, and WSL2 environments.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from backend.installer import (
    LAUNCHD_LABELS,
    SYSTEMD_UNITS,
    _detect_platform,
    _find_python,
    _repo_root,
    ensure_data_dir,
    health_check,
)

# ── Platform detection ────────────────────────────────────


class TestPlatformDetection:
    """Verify _detect_platform() returns correct values."""

    def test_detect_platform_returns_known_value(self):
        plat = _detect_platform()
        assert plat in ("macos", "linux", "wsl2"), f"Unknown platform: {plat}"

    @patch("platform.system", return_value="Darwin")
    def test_detect_macos(self, _mock):
        assert _detect_platform() == "macos"

    @patch("platform.system", return_value="Linux")
    def test_detect_linux_without_wsl(self, _mock):
        with patch("pathlib.Path.read_text", return_value="Linux version 5.15.0-generic"):
            assert _detect_platform() == "linux"

    @patch("platform.system", return_value="Linux")
    def test_detect_wsl2(self, _mock):
        with patch(
            "pathlib.Path.read_text",
            return_value="Linux version 5.15.153.1-microsoft-standard-WSL2",
        ):
            assert _detect_platform() == "wsl2"

    @patch("platform.system", return_value="Linux")
    def test_detect_wsl_lowercase(self, _mock):
        with patch(
            "pathlib.Path.read_text",
            return_value="Linux version 5.15.0-1-Microsoft",
        ):
            assert _detect_platform() == "wsl2"

    @patch("platform.system", return_value="Linux")
    def test_detect_linux_proc_version_missing(self, _mock):
        with patch("pathlib.Path.read_text", side_effect=OSError("No such file")):
            assert _detect_platform() == "linux"


# ── Python environment ────────────────────────────────────


class TestPythonEnvironment:
    """Verify Python version and interpreter discovery."""

    def test_python_version_312_plus(self):
        assert sys.version_info >= (3, 12), f"Python 3.12+ required, got {sys.version}"

    def test_find_python_returns_valid_path(self):
        py = _find_python()
        assert Path(py).exists(), f"Python not found at: {py}"

    def test_find_python_matches_current(self):
        assert _find_python() == sys.executable


# ── Repository structure ──────────────────────────────────


class TestRepoStructure:
    """Verify required project files exist."""

    def test_repo_root_exists(self):
        root = _repo_root()
        assert root.is_dir()

    def test_pyproject_toml_exists(self):
        assert (_repo_root() / "pyproject.toml").exists()

    def test_backend_package_exists(self):
        assert (_repo_root() / "backend" / "__init__.py").exists()

    def test_frontend_package_json_exists(self):
        assert (_repo_root() / "frontend" / "package.json").exists()

    def test_dockerfile_exists(self):
        assert (_repo_root() / "Dockerfile").exists()

    def test_docker_compose_exists(self):
        assert (_repo_root() / "docker-compose.yml").exists()

    def test_makefile_exists(self):
        assert (_repo_root() / "Makefile").exists()


# ── Service templates ─────────────────────────────────────


class TestServiceTemplates:
    """Verify service configuration templates are valid."""

    def test_launchd_templates_exist(self):
        launchd_dir = _repo_root() / "launchd"
        for label in LAUNCHD_LABELS:
            plist = launchd_dir / f"{label}.plist"
            assert plist.exists(), f"Missing launchd template: {plist}"

    def test_launchd_templates_have_placeholders(self):
        launchd_dir = _repo_root() / "launchd"
        for label in LAUNCHD_LABELS:
            content = (launchd_dir / f"{label}.plist").read_text()
            assert "__INSTALL_DIR__" in content, f"Missing placeholder in {label}.plist"

    def test_launchd_templates_valid_plist_structure(self):
        launchd_dir = _repo_root() / "launchd"
        for label in LAUNCHD_LABELS:
            content = (launchd_dir / f"{label}.plist").read_text()
            assert '<?xml version="1.0"' in content, f"Missing XML header in {label}.plist"
            assert "<plist" in content, f"Missing <plist> element in {label}.plist"
            assert "<dict>" in content, f"Missing <dict> element in {label}.plist"
            assert "<key>Label</key>" in content, f"Missing Label key in {label}.plist"
            assert "<key>ProgramArguments</key>" in content

    def test_systemd_templates_exist(self):
        systemd_dir = _repo_root() / "systemd"
        for unit in SYSTEMD_UNITS:
            unit_path = systemd_dir / unit
            assert unit_path.exists(), f"Missing systemd template: {unit_path}"

    def test_systemd_templates_have_required_sections(self):
        systemd_dir = _repo_root() / "systemd"
        for unit in SYSTEMD_UNITS:
            content = (systemd_dir / unit).read_text()
            assert "[Unit]" in content, f"Missing [Unit] in {unit}"
            assert "[Service]" in content, f"Missing [Service] in {unit}"
            assert "[Install]" in content, f"Missing [Install] in {unit}"

    def test_systemd_api_service_config(self):
        content = (_repo_root() / "systemd" / "yeliztli-api.service").read_text()
        assert "ExecStart=__PYTHON__ -m backend.main" in content
        assert "--host" not in content
        assert "--port" not in content

    def test_systemd_huey_service_config(self):
        content = (_repo_root() / "systemd" / "yeliztli-huey.service").read_text()
        assert "huey_consumer" in content
        assert "yeliztli-api.service" in content


# ── Data directory ────────────────────────────────────────


class TestDataDirectory:
    """Verify data directory creation works."""

    def test_ensure_data_dir_creates_structure(self, tmp_path, monkeypatch):
        fake_data = tmp_path / ".yeliztli"
        monkeypatch.setattr("backend.installer.DATA_DIR", fake_data)
        ensure_data_dir()
        assert fake_data.is_dir()
        assert (fake_data / "samples").is_dir()
        assert (fake_data / "downloads").is_dir()
        assert (fake_data / "logs").is_dir()

    def test_ensure_data_dir_idempotent(self, tmp_path, monkeypatch):
        fake_data = tmp_path / ".yeliztli"
        monkeypatch.setattr("backend.installer.DATA_DIR", fake_data)
        ensure_data_dir()
        ensure_data_dir()  # Should not raise
        assert fake_data.is_dir()


# ── Docker configuration ─────────────────────────────────


class TestDockerConfiguration:
    """Verify Docker build files are valid."""

    def test_dockerfile_base_image(self):
        content = (_repo_root() / "Dockerfile").read_text()
        assert "python:3.12" in content

    def test_dockerfile_non_root_user(self):
        content = (_repo_root() / "Dockerfile").read_text()
        assert "USER appuser" in content

    def test_dockerfile_healthcheck(self):
        content = (_repo_root() / "Dockerfile").read_text()
        assert "HEALTHCHECK" in content

    def test_dockerfile_installs_report_browser(self):
        content = (_repo_root() / "Dockerfile").read_text()
        assert "python -m playwright install --with-deps chromium" in content
        assert "HOME=/home/appuser" in content

    def test_dockerfile_exposes_port(self):
        content = (_repo_root() / "Dockerfile").read_text()
        assert "ARG YELIZTLI_PORT=8000" in content
        assert "EXPOSE ${YELIZTLI_PORT}" in content

    def test_docker_compose_services(self):
        import yaml

        content = (_repo_root() / "docker-compose.yml").read_text()
        config = yaml.safe_load(content)
        services = config.get("services", {})
        assert "api" in services, "Missing api service"
        assert "huey" in services, "Missing huey service"
        assert services["api"]["build"]["args"]["YELIZTLI_PORT"] == "${YELIZTLI_PORT:-8000}"

    def test_docker_compose_localhost_only(self):
        content = (_repo_root() / "docker-compose.yml").read_text()
        assert "${YELIZTLI_PUBLISH_HOST:-127.0.0.1}" in content
        assert "${YELIZTLI_PORT:-8000}" in content

    def test_docker_compose_persistent_volume(self):
        import yaml

        content = (_repo_root() / "docker-compose.yml").read_text()
        config = yaml.safe_load(content)
        assert "yeliztli-data" in config.get("volumes", {})


# ── Package build ─────────────────────────────────────────


class TestPackageBuild:
    """Verify the Python package builds correctly."""

    def test_package_importable(self):
        import backend

        assert hasattr(backend, "__file__")

    def test_main_app_importable(self):
        from backend.main import app

        assert app is not None

    def test_installer_importable(self):
        from backend.installer import main

        assert callable(main)

    def test_cli_help_runs(self):
        result = subprocess.run(
            [sys.executable, "-m", "backend.installer", "--help"],
            capture_output=True,
            text=True,
        )
        # Allow both 0 (argparse help) and 2 (missing required subcommand)
        assert result.returncode in (0, 2)

    def test_entry_point_registered_in_pyproject(self):
        """Verify the console_scripts entry point is declared in pyproject.toml."""
        content = (_repo_root() / "pyproject.toml").read_text()
        assert "yeliztli-setup" in content
        assert "backend.installer:main" in content


# ── Architecture detection ────────────────────────────────


class TestArchitectureDetection:
    """Verify architecture is correctly identified."""

    def test_machine_is_known(self):
        machine = platform.machine().lower()
        known = {"x86_64", "amd64", "arm64", "aarch64", "i386", "i686"}
        assert machine in known, f"Unknown architecture: {machine}"

    def test_architecture_reported(self):
        """Smoke test: architecture info is available."""
        info = {
            "system": platform.system(),
            "machine": platform.machine(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        }
        assert all(info.values())


# ── Health check ──────────────────────────────────────────


class TestHealthCheck:
    """Verify health_check function behavior."""

    def test_health_check_fails_on_closed_port(self):
        # Port 39999 should not have a service running
        assert health_check(port=39999) is False

    def test_health_check_returns_bool(self):
        result = health_check(port=39998)
        assert isinstance(result, bool)
