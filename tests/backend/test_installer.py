"""Tests for backend.installer — native install packaging (P1-22)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from backend import config, installer

# ── Platform detection ─────────────────────────────────────


class TestDetectPlatform:
    def test_macos(self):
        with patch("platform.system", return_value="Darwin"):
            assert installer._detect_platform() == "macos"

    def test_linux(self):
        with patch("platform.system", return_value="Linux"):
            with patch("pathlib.Path.read_text", return_value="Linux version 5.15.0-generic"):
                assert installer._detect_platform() == "linux"

    def test_wsl2(self):
        with patch("platform.system", return_value="Linux"):
            with patch(
                "pathlib.Path.read_text",
                return_value="Linux version 5.15.153.1-microsoft-standard-WSL2",
            ):
                assert installer._detect_platform() == "wsl2"

    def test_wsl2_lowercase_microsoft(self):
        with patch("platform.system", return_value="Linux"):
            with patch(
                "pathlib.Path.read_text",
                return_value="Linux version 5.15.0-Microsoft-custom",
            ):
                assert installer._detect_platform() == "wsl2"

    def test_proc_version_unreadable(self):
        with patch("platform.system", return_value="Linux"):
            with patch("pathlib.Path.read_text", side_effect=OSError("No such file")):
                assert installer._detect_platform() == "linux"


# ── Data directory ─────────────────────────────────────────


class TestEnsureDataDir:
    def test_creates_directories(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        data_dir = tmp_path / ".yeliztli"
        monkeypatch.setattr(installer, "DATA_DIR", data_dir)

        installer.ensure_data_dir()

        assert data_dir.is_dir()
        assert (data_dir / "samples").is_dir()
        assert (data_dir / "downloads").is_dir()
        assert (data_dir / "logs").is_dir()

    def test_idempotent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        data_dir = tmp_path / ".yeliztli"
        monkeypatch.setattr(installer, "DATA_DIR", data_dir)

        installer.ensure_data_dir()
        installer.ensure_data_dir()  # Should not raise

        assert data_dir.is_dir()


# ── Plist rendering ────────────────────────────────────────


class TestFindHueyConsumer:
    def test_resolves_path_command_to_absolute_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        huey_script = bin_dir / "huey_consumer"
        huey_script.write_text("#!/bin/sh\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(installer, "_find_command", lambda name: "bin/huey_consumer")

        assert installer._find_huey_consumer() == str(huey_script)

    def test_prefers_entrypoint_sibling_when_not_on_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        setup_script = tmp_path / "yeliztli-setup"
        huey_script = tmp_path / "huey_consumer"
        setup_script.write_text("#!/bin/sh\n")
        huey_script.write_text("#!/bin/sh\n")
        monkeypatch.setattr(sys, "argv", [str(setup_script)])
        monkeypatch.setattr(installer, "_find_command", lambda name: None)
        monkeypatch.setattr(installer, "_find_python", lambda: "/opt/python/bin/python")

        assert installer._find_huey_consumer() == str(huey_script)

    def test_prefers_explicit_relative_entrypoint_sibling_when_not_on_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        setup_script = tmp_path / "yeliztli-setup"
        huey_script = tmp_path / "huey_consumer"
        setup_script.write_text("#!/bin/sh\n")
        huey_script.write_text("#!/bin/sh\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["./yeliztli-setup"])
        monkeypatch.setattr(installer, "_find_command", lambda name: None)
        monkeypatch.setattr(installer, "_find_python", lambda: "/opt/python/bin/python")

        assert installer._find_huey_consumer() == str(huey_script)


class TestRenderPlist:
    def test_replaces_install_dir(self, tmp_path: Path):
        plist = tmp_path / "test.plist"
        plist.write_text(
            '<?xml version="1.0"?>\n'
            "<dict>\n"
            "  <string>__INSTALL_DIR__</string>\n"
            "  <string>~/Library/Logs/test.log</string>\n"
            "</dict>\n"
        )

        rendered = installer._render_plist(plist, Path("/opt/yeliztli"))

        assert "__INSTALL_DIR__" not in rendered
        assert "/opt/yeliztli" in rendered

    def test_expands_tilde_in_logs(self, tmp_path: Path):
        plist = tmp_path / "test.plist"
        plist.write_text("<string>~/Library/Logs/test.log</string>")

        rendered = installer._render_plist(plist, Path("/opt/gi"))

        assert "~/Library/Logs" not in rendered
        assert str(installer.LOG_DIR_MACOS) in rendered

    def test_replaces_python_placeholder(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        plist = tmp_path / "test.plist"
        plist.write_text("<array><string>__PYTHON__</string><string>-m</string></array>")
        monkeypatch.setattr(installer, "_find_python", lambda: "/opt/python/bin/python")

        rendered = installer._render_plist(plist, Path("/opt/gi"))

        assert "__PYTHON__" not in rendered
        assert "/opt/python/bin/python" in rendered

    def test_replaces_huey_consumer_placeholder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        plist = tmp_path / "test.plist"
        plist.write_text("<array><string>__HUEY_CONSUMER__</string></array>")
        monkeypatch.setattr(
            installer, "_find_huey_consumer", lambda: "/opt/python/bin/huey_consumer"
        )

        rendered = installer._render_plist(plist, Path("/opt/gi"))

        assert "__HUEY_CONSUMER__" not in rendered
        assert "/opt/python/bin/huey_consumer" in rendered

    def test_huey_plist_uses_python_bin_consumer_without_path(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        template = installer._repo_root() / "launchd" / "com.yeliztli.huey.plist"
        monkeypatch.setattr(sys, "argv", ["yeliztli-setup"])
        monkeypatch.setattr(installer, "_find_python", lambda: "/opt/python/bin/python")
        monkeypatch.setattr(installer, "_find_command", lambda name: None)

        rendered = installer._render_plist(template, Path("/opt/gi"))

        assert "<string>/opt/python/bin/huey_consumer</string>" in rendered
        assert "<string>huey_consumer</string>" not in rendered
        assert "__HUEY_CONSUMER__" not in rendered

    def test_xml_escapes_inserted_values(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        plist = tmp_path / "test.plist"
        plist.write_text(
            "<dict>\n"
            "  <string>__INSTALL_DIR__</string>\n"
            "  <string>__PYTHON__</string>\n"
            "  <string>~/Library/Logs/test.log</string>\n"
            "</dict>\n"
        )
        monkeypatch.setattr(installer, "_find_python", lambda: "/opt/Python & Co/bin/python")
        monkeypatch.setattr(installer, "LOG_DIR_MACOS", Path("/Users/A & B/Library/Logs"))

        rendered = installer._render_plist(plist, Path("/opt/Yeliztli & Data"))

        assert "/opt/Yeliztli &amp; Data" in rendered
        assert "/opt/Python &amp; Co/bin/python" in rendered
        assert "/Users/A &amp; B/Library/Logs/test.log" in rendered


# ── Systemd rendering ─────────────────────────────────────


class TestRenderSystemdUnit:
    def test_replaces_working_directory(self, tmp_path: Path):
        unit = tmp_path / "test.service"
        unit.write_text(
            "[Service]\nWorkingDirectory=%h/Yeliztli\nEnvironment=PATH=%h/.local/bin:/usr/bin\n"
        )

        rendered = installer._render_systemd_unit(unit, Path("/home/user/Yeliztli"))

        assert "WorkingDirectory=/home/user/Yeliztli" in rendered
        assert "%h/Yeliztli" not in rendered

    def test_includes_python_bin_in_path(self, tmp_path: Path):
        unit = tmp_path / "test.service"
        unit.write_text("Environment=PATH=%h/.local/bin:/usr/bin\n")

        rendered = installer._render_systemd_unit(unit, Path("/home/user/gi"))

        # Should include the Python interpreter's bin directory
        python_dir = str(Path(installer._find_python()).parent)
        assert python_dir in rendered
        # %h should be expanded to actual home dir
        assert "%h" not in rendered
        home_dir = str(Path.home())
        assert f"{home_dir}/.local/bin" in rendered

    def test_replaces_python_placeholder(self, tmp_path: Path):
        unit = tmp_path / "test.service"
        unit.write_text(
            "[Service]\n"
            "ExecStart=__PYTHON__ -m backend.main\n"
            "Environment=PATH=%h/.local/bin:/usr/bin\n"
        )

        rendered = installer._render_systemd_unit(unit, Path("/home/user/gi"))

        assert "__PYTHON__" not in rendered
        assert f'ExecStart="{installer._find_python()}" -m backend.main' in rendered

    def test_quotes_python_placeholder_with_spaces(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        unit = tmp_path / "test.service"
        unit.write_text(
            "[Service]\n"
            "ExecStart=__PYTHON__ -m backend.main\n"
            "Environment=PATH=%h/.local/bin:/usr/bin\n"
        )
        monkeypatch.setattr(installer, "_find_python", lambda: "/tmp/Python Dir/python")

        rendered = installer._render_systemd_unit(unit, Path("/home/user/gi"))

        assert 'ExecStart="/tmp/Python Dir/python" -m backend.main' in rendered


# ── Health check ───────────────────────────────────────────


class TestHealthCheck:
    def test_returns_false_on_connection_error(self):
        # No server running on a random port
        assert installer.health_check(port=59999) is False


# ── CLI argument parsing ───────────────────────────────────


class TestCLIParsing:
    def test_install_defaults(self):
        """install command parses with default options."""
        with patch.object(installer, "cmd_install", return_value=0) as mock:
            installer.main(["install"])
            mock.assert_called_once()
            args = mock.call_args[0][0]
            assert args.skip_browser_install is False

    def test_install_skip_flags(self):
        """install skip flags are parsed."""
        with patch.object(installer, "cmd_install", return_value=0) as mock:
            installer.main(["install", "--skip-pip", "--skip-browser-install", "--skip-frontend"])
            args = mock.call_args[0][0]
            assert args.skip_pip is True
            assert args.skip_browser_install is True
            assert args.skip_frontend is True

    def test_uninstall_defaults(self):
        with patch.object(installer, "cmd_uninstall", return_value=0) as mock:
            installer.main(["uninstall"])
            args = mock.call_args[0][0]
            assert args.remove_data is False

    def test_uninstall_remove_data(self):
        with patch.object(installer, "cmd_uninstall", return_value=0) as mock:
            installer.main(["uninstall", "--remove-data"])
            args = mock.call_args[0][0]
            assert args.remove_data is True

    def test_status_command(self):
        with patch.object(installer, "cmd_status", return_value=0) as mock:
            installer.main(["status"])
            mock.assert_called_once()

    def test_start_command(self):
        with patch.object(installer, "cmd_start", return_value=0) as mock:
            installer.main(["start"])
            mock.assert_called_once()

    def test_stop_command(self):
        with patch.object(installer, "cmd_stop", return_value=0) as mock:
            installer.main(["stop"])
            mock.assert_called_once()

    def test_no_command_exits(self):
        with pytest.raises(SystemExit):
            installer.main([])


# ── Install flow (mocked subprocess) ──────────────────────


class TestPlaywrightBrowserInstall:
    @patch("backend.installer._run")
    def test_install_playwright_chromium_uses_python_module(
        self, mock_run: MagicMock, monkeypatch: pytest.MonkeyPatch
    ):
        """Browser install uses the active Python environment's Playwright CLI."""
        monkeypatch.setattr(installer, "_find_python", lambda: "/opt/yeliztli/bin/python")

        installer.install_playwright_chromium()

        mock_run.assert_called_once_with(
            ["/opt/yeliztli/bin/python", "-m", "playwright", "install", "chromium"]
        )


class TestInstallFlow:
    @patch("backend.installer._detect_platform", return_value="linux")
    @patch("backend.installer._has_systemd", return_value=False)
    @patch("subprocess.run")
    def test_install_linux_no_systemd(
        self,
        mock_run: MagicMock,
        mock_systemd: MagicMock,
        mock_plat: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        """Install on Linux without systemd prints manual instructions."""
        monkeypatch.setattr(installer, "DATA_DIR", tmp_path / ".yeliztli")
        monkeypatch.setattr(installer, "_find_python", lambda: "/tmp/Python Dir/python")

        ns = argparse.Namespace(skip_pip=True, skip_frontend=True)
        result = installer.cmd_install(ns)

        assert result == 0
        assert (tmp_path / ".yeliztli").is_dir()
        mock_run.assert_any_call(
            ["/tmp/Python Dir/python", "-m", "playwright", "install", "chromium"],
            check=True,
        )
        assert "    '/tmp/Python Dir/python' -m backend.main &" in capsys.readouterr().out

    @patch("backend.installer._detect_platform", return_value="macos")
    @patch("subprocess.run")
    def test_install_macos(
        self,
        mock_run: MagicMock,
        mock_plat: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Install on macOS calls launchctl load."""
        monkeypatch.setattr(installer, "DATA_DIR", tmp_path / ".yeliztli")
        monkeypatch.setattr(installer, "LAUNCHD_DIR", tmp_path / "LaunchAgents")
        monkeypatch.setattr(installer, "LOG_DIR_MACOS", tmp_path / "Logs")

        mock_run.return_value = MagicMock(returncode=0)

        ns = argparse.Namespace(skip_pip=True, skip_frontend=True)
        result = installer.cmd_install(ns)

        assert result == 0
        mock_run.assert_any_call(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
        )
        # Verify plists were written
        for label in installer.LAUNCHD_LABELS:
            plist = tmp_path / "LaunchAgents" / f"{label}.plist"
            assert plist.exists()
            content = plist.read_text()
            assert "__INSTALL_DIR__" not in content

    def test_install_launchd_unloads_existing_plists_before_loading(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        launchd_dir = tmp_path / "LaunchAgents"
        launchd_dir.mkdir()
        monkeypatch.setattr(installer, "LAUNCHD_DIR", launchd_dir)
        monkeypatch.setattr(installer, "LOG_DIR_MACOS", tmp_path / "Logs")

        for label in installer.LAUNCHD_LABELS:
            (launchd_dir / f"{label}.plist").write_text("stale plist")

        with patch.object(installer, "_run") as mock_run:
            installer.install_launchd()

        calls = mock_run.call_args_list
        for label in installer.LAUNCHD_LABELS:
            plist = launchd_dir / f"{label}.plist"
            unload = call(["launchctl", "unload", str(plist)], check=False)
            load = call(["launchctl", "load", str(plist)], check=False)
            assert unload in calls
            assert load in calls
            assert calls.index(unload) < calls.index(load)

    @patch("backend.installer._detect_platform", return_value="linux")
    @patch("backend.installer._has_systemd", return_value=False)
    @patch("backend.installer.install_playwright_chromium")
    def test_install_skip_browser_install(
        self,
        mock_browser_install: MagicMock,
        mock_systemd: MagicMock,
        mock_plat: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """The explicit skip flag leaves PDF/PNG browser setup to the operator."""
        monkeypatch.setattr(installer, "DATA_DIR", tmp_path / ".yeliztli")

        ns = argparse.Namespace(
            skip_pip=True,
            skip_browser_install=True,
            skip_frontend=True,
        )
        result = installer.cmd_install(ns)

        assert result == 0
        mock_browser_install.assert_not_called()


# ── Uninstall flow ─────────────────────────────────────────


class TestUninstallFlow:
    @patch("backend.installer._detect_platform", return_value="linux")
    @patch("subprocess.run")
    def test_uninstall_preserves_data(
        self,
        mock_run: MagicMock,
        mock_plat: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        data_dir = tmp_path / ".yeliztli"
        data_dir.mkdir()
        monkeypatch.setattr(installer, "_uninstall_data_dirs", lambda: [data_dir])
        monkeypatch.setattr(installer, "SYSTEMD_USER_DIR", tmp_path / "systemd")
        mock_run.return_value = MagicMock(returncode=0)

        ns = argparse.Namespace(remove_data=False)
        result = installer.cmd_uninstall(ns)

        assert result == 0
        assert data_dir.is_dir()  # Data preserved

    @patch("backend.installer._detect_platform", return_value="linux")
    @patch("subprocess.run")
    def test_uninstall_removes_data(
        self,
        mock_run: MagicMock,
        mock_plat: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        data_dir = tmp_path / ".yeliztli"
        data_dir.mkdir()
        (data_dir / "reference.db").touch()
        monkeypatch.setattr(installer, "_uninstall_data_dirs", lambda: [data_dir])
        monkeypatch.setattr(installer, "SYSTEMD_USER_DIR", tmp_path / "systemd")
        mock_run.return_value = MagicMock(returncode=0)

        ns = argparse.Namespace(remove_data=True)
        result = installer.cmd_uninstall(ns)

        assert result == 0
        assert not data_dir.exists()  # Data removed

    @patch("backend.installer._detect_platform", return_value="linux")
    @patch("subprocess.run")
    def test_uninstall_removes_relocated_data_dir_and_default_control_dir(
        self,
        mock_run: MagicMock,
        mock_plat: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        home_dir = tmp_path / "home"
        relocated_data_dir = tmp_path / "relocated"
        (relocated_data_dir / "samples").mkdir(parents=True)
        (relocated_data_dir / "samples" / "sample_1.db").touch()
        home_dir.mkdir()
        (home_dir / config.DATA_DIR_POINTER_NAME).write_text(
            str(relocated_data_dir), encoding="utf-8"
        )
        monkeypatch.delenv("YELIZTLI_DATA_DIR", raising=False)
        monkeypatch.setattr(config, "DEFAULT_DATA_DIR", home_dir)
        monkeypatch.setattr(installer, "SYSTEMD_USER_DIR", tmp_path / "systemd")
        mock_run.return_value = MagicMock(returncode=0)
        config.get_settings.cache_clear()

        try:
            ns = argparse.Namespace(remove_data=True)
            result = installer.cmd_uninstall(ns)
        finally:
            config.get_settings.cache_clear()

        assert result == 0
        assert not relocated_data_dir.exists()
        assert not home_dir.exists()

    @patch("backend.installer._detect_platform", return_value="linux")
    @patch("subprocess.run")
    def test_uninstall_removes_env_data_dir_and_default_control_dir(
        self,
        mock_run: MagicMock,
        mock_plat: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        home_dir = tmp_path / "home"
        env_data_dir = tmp_path / "from-env"
        (env_data_dir / "samples").mkdir(parents=True)
        (env_data_dir / "samples" / "sample_1.db").touch()
        home_dir.mkdir()
        (home_dir / "config.toml").write_text("[yeliztli]\n", encoding="utf-8")
        monkeypatch.setenv("YELIZTLI_DATA_DIR", str(env_data_dir))
        monkeypatch.setattr(config, "DEFAULT_DATA_DIR", home_dir)
        monkeypatch.setattr(installer, "SYSTEMD_USER_DIR", tmp_path / "systemd")
        mock_run.return_value = MagicMock(returncode=0)
        config.get_settings.cache_clear()

        try:
            ns = argparse.Namespace(remove_data=True)
            result = installer.cmd_uninstall(ns)
        finally:
            config.get_settings.cache_clear()

        assert result == 0
        assert not env_data_dir.exists()
        assert not home_dir.exists()

    @patch("backend.installer._detect_platform", return_value="linux")
    @patch("subprocess.run")
    def test_uninstall_removes_known_artifacts_but_preserves_mixed_data_dir(
        self,
        mock_run: MagicMock,
        mock_plat: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        home_dir = tmp_path / "home"
        mixed_data_dir = tmp_path / "mixed"
        (mixed_data_dir / "samples").mkdir(parents=True)
        (mixed_data_dir / "samples" / "sample_1.db").touch()
        (mixed_data_dir / "notes.txt").write_text("not yeliztli", encoding="utf-8")
        home_dir.mkdir()
        (home_dir / "config.toml").write_text("[yeliztli]\n", encoding="utf-8")
        monkeypatch.setenv("YELIZTLI_DATA_DIR", str(mixed_data_dir))
        monkeypatch.setattr(config, "DEFAULT_DATA_DIR", home_dir)
        monkeypatch.setattr(installer, "SYSTEMD_USER_DIR", tmp_path / "systemd")
        mock_run.return_value = MagicMock(returncode=0)
        config.get_settings.cache_clear()

        try:
            ns = argparse.Namespace(remove_data=True)
            result = installer.cmd_uninstall(ns)
        finally:
            config.get_settings.cache_clear()

        assert result == 0
        assert mixed_data_dir.exists()
        assert (mixed_data_dir / "notes.txt").exists()
        assert not (mixed_data_dir / "samples").exists()
        assert not home_dir.exists()

    @patch("backend.installer._detect_platform", return_value="linux")
    @patch("subprocess.run")
    def test_uninstall_preserves_custom_dir_with_only_generic_non_yeliztli_names(
        self,
        mock_run: MagicMock,
        mock_plat: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        home_dir = tmp_path / "home"
        broad_data_dir = tmp_path / "broad"
        (broad_data_dir / "samples").mkdir(parents=True)
        (broad_data_dir / "samples" / "sample_1.db").touch()
        (broad_data_dir / "downloads").mkdir()
        (broad_data_dir / "downloads" / "other-file.txt").touch()
        (broad_data_dir / "logs").mkdir()
        (broad_data_dir / "logs" / "other.log").touch()
        home_dir.mkdir()
        (home_dir / "config.toml").write_text("[yeliztli]\n", encoding="utf-8")
        monkeypatch.setenv("YELIZTLI_DATA_DIR", str(broad_data_dir))
        monkeypatch.setattr(config, "DEFAULT_DATA_DIR", home_dir)
        monkeypatch.setattr(installer, "SYSTEMD_USER_DIR", tmp_path / "systemd")
        mock_run.return_value = MagicMock(returncode=0)
        config.get_settings.cache_clear()

        try:
            ns = argparse.Namespace(remove_data=True)
            result = installer.cmd_uninstall(ns)
        finally:
            config.get_settings.cache_clear()

        assert result == 0
        assert broad_data_dir.exists()
        assert (broad_data_dir / "downloads" / "other-file.txt").exists()
        assert (broad_data_dir / "logs" / "other.log").exists()
        assert not (broad_data_dir / "samples").exists()
        assert not home_dir.exists()

    @patch("backend.installer._detect_platform", return_value="linux")
    @patch("subprocess.run")
    def test_uninstall_unlinks_symlink_data_dir_without_deleting_target(
        self,
        mock_run: MagicMock,
        mock_plat: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        home_dir = tmp_path / "home"
        target_data_dir = tmp_path / "target"
        linked_data_dir = tmp_path / "linked"
        (target_data_dir / "samples").mkdir(parents=True)
        (target_data_dir / "samples" / "sample_1.db").touch()
        linked_data_dir.symlink_to(target_data_dir, target_is_directory=True)
        home_dir.mkdir()
        (home_dir / config.DATA_DIR_POINTER_NAME).write_text(
            str(linked_data_dir), encoding="utf-8"
        )
        monkeypatch.delenv("YELIZTLI_DATA_DIR", raising=False)
        monkeypatch.setattr(config, "DEFAULT_DATA_DIR", home_dir)
        monkeypatch.setattr(installer, "SYSTEMD_USER_DIR", tmp_path / "systemd")
        mock_run.return_value = MagicMock(returncode=0)
        config.get_settings.cache_clear()

        try:
            ns = argparse.Namespace(remove_data=True)
            result = installer.cmd_uninstall(ns)
        finally:
            config.get_settings.cache_clear()

        assert result == 0
        assert not linked_data_dir.exists()
        assert target_data_dir.exists()
        assert (target_data_dir / "samples" / "sample_1.db").exists()
        assert not home_dir.exists()

    @patch("backend.installer._detect_platform", return_value="linux")
    @patch("subprocess.run")
    def test_uninstall_refuses_relative_env_data_dir_without_deleting_control_dir(
        self,
        mock_run: MagicMock,
        mock_plat: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        (home_dir / "config.toml").write_text("[yeliztli]\n", encoding="utf-8")
        monkeypatch.setenv("YELIZTLI_DATA_DIR", "relative-data")
        monkeypatch.setattr(config, "DEFAULT_DATA_DIR", home_dir)
        monkeypatch.setattr(installer, "SYSTEMD_USER_DIR", tmp_path / "systemd")
        mock_run.return_value = MagicMock(returncode=0)
        config.get_settings.cache_clear()

        try:
            ns = argparse.Namespace(remove_data=True)
            result = installer.cmd_uninstall(ns)
        finally:
            config.get_settings.cache_clear()

        assert result == 1
        assert home_dir.exists()


# ── Huey tasks stub ────────────────────────────────────────


class TestHueyTasks:
    def test_huey_instance_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """The huey instance referenced by service configs exists."""
        env = os.environ.copy()
        env["YELIZTLI_DATA_DIR"] = str(tmp_path / "data")
        pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            f"{Path.cwd()}{os.pathsep}{pythonpath}" if pythonpath else str(Path.cwd())
        )

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from backend.tasks.huey_tasks import huey; assert huey.name == 'yeliztli'",
            ],
            check=False,
            env=env,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr


# ── Repo root detection ───────────────────────────────────


class TestRepoRoot:
    def test_repo_root_is_project(self):
        root = installer._repo_root()
        assert (root / "pyproject.toml").exists()
        assert (root / "backend").is_dir()


# ── Template file existence ────────────────────────────────


class TestTemplateFiles:
    def test_launchd_templates_exist(self):
        root = installer._repo_root()
        for label in installer.LAUNCHD_LABELS:
            assert (root / "launchd" / f"{label}.plist").exists()

    def test_systemd_templates_exist(self):
        root = installer._repo_root()
        for unit in installer.SYSTEMD_UNITS:
            assert (root / "systemd" / unit).exists()
