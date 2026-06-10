"""Yeliztli native install/uninstall logic.

Handles:
- launchd plist installation on macOS
- systemd user unit installation on Linux/WSL2
- Data directory creation
- Frontend build
- Service management (start/stop/status)

Entry point: `yeliztli-setup` console script.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# ── Constants ──────────────────────────────────────────────

DATA_DIR = Path.home() / ".yeliztli"

LAUNCHD_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCHD_LABELS = ("com.yeliztli.api", "com.yeliztli.huey")

SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
SYSTEMD_UNITS = ("yeliztli-api.service", "yeliztli-huey.service")

LOG_DIR_MACOS = Path.home() / "Library" / "Logs"


def _repo_root() -> Path:
    """Return the repository / install root (parent of backend/)."""
    return Path(__file__).resolve().parent.parent


def _detect_platform() -> str:
    """Return 'macos', 'linux', or 'wsl2'."""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "linux":
        # Check for WSL2
        try:
            version_info = Path("/proc/version").read_text()
            if "microsoft" in version_info.lower() or "wsl" in version_info.lower():
                return "wsl2"
        except OSError:
            pass
        return "linux"
    return system  # fallback


def _find_python() -> str:
    """Return the path to the current Python interpreter."""
    return sys.executable


def _find_command(name: str) -> str | None:
    """Find a command on PATH, return full path or None."""
    return shutil.which(name)


def _run(cmd: list[str], check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess with stdout/stderr visible."""
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, **kwargs)


# ── Data directory ─────────────────────────────────────────


def ensure_data_dir() -> None:
    """Create the ~/.yeliztli directory structure."""
    dirs = [
        DATA_DIR,
        DATA_DIR / "samples",
        DATA_DIR / "downloads",
        DATA_DIR / "logs",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    print(f"  Data directory: {DATA_DIR}")


# ── Frontend build ─────────────────────────────────────────


def build_frontend() -> bool:
    """Build the React frontend for production."""
    frontend_dir = _repo_root() / "frontend"
    if not (frontend_dir / "package.json").exists():
        print("  [skip] frontend/package.json not found")
        return False

    npm = _find_command("npm")
    if not npm:
        print("  [warn] npm not found — skipping frontend build")
        return False

    print("  Installing frontend dependencies...")
    _run([npm, "install"], cwd=str(frontend_dir))
    print("  Building frontend...")
    _run([npm, "run", "build"], cwd=str(frontend_dir))
    print("  Frontend built to frontend/dist/")
    return True


# ── macOS launchd ──────────────────────────────────────────


def _render_plist(template_path: Path, install_dir: Path) -> str:
    """Render a launchd plist template, replacing __INSTALL_DIR__."""
    content = template_path.read_text()
    content = content.replace("__INSTALL_DIR__", str(install_dir))
    # Expand ~ in log paths to absolute home
    content = content.replace("~/Library/Logs", str(LOG_DIR_MACOS))
    return content


def install_launchd() -> None:
    """Install and load launchd agents on macOS."""
    install_dir = _repo_root()
    launchd_src = install_dir / "launchd"
    LAUNCHD_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR_MACOS.mkdir(parents=True, exist_ok=True)

    for label in LAUNCHD_LABELS:
        src = launchd_src / f"{label}.plist"
        dst = LAUNCHD_DIR / f"{label}.plist"

        if not src.exists():
            print(f"  [warn] Template not found: {src}")
            continue

        rendered = _render_plist(src, install_dir)
        dst.write_text(rendered)
        print(f"  Installed: {dst}")

        # Load the agent
        _run(["launchctl", "load", str(dst)], check=False)
        print(f"  Loaded: {label}")


def uninstall_launchd() -> None:
    """Unload and remove launchd agents on macOS."""
    for label in LAUNCHD_LABELS:
        plist = LAUNCHD_DIR / f"{label}.plist"
        if plist.exists():
            _run(["launchctl", "unload", str(plist)], check=False)
            plist.unlink()
            print(f"  Removed: {plist}")
        else:
            print(f"  [skip] Not installed: {plist}")


def status_launchd() -> None:
    """Check launchd agent status on macOS."""
    for label in LAUNCHD_LABELS:
        result = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            # Parse PID from output
            lines = result.stdout.strip().split("\n")
            print(f"  {label}: running")
            for line in lines:
                if "PID" in line or line.strip().startswith('"PID"'):
                    print(f"    {line.strip()}")
        else:
            print(f"  {label}: not running")


# ── Linux/WSL2 systemd ────────────────────────────────────


def _has_systemd() -> bool:
    """Check if systemd is available (user session)."""
    result = subprocess.run(
        ["systemctl", "--user", "is-system-running"],
        capture_output=True,
        text=True,
    )
    # "running", "degraded", "initializing" all mean systemd is active
    return result.returncode == 0 or result.stdout.strip() in (
        "degraded",
        "initializing",
        "starting",
    )


def _render_systemd_unit(template_path: Path, install_dir: Path) -> str:
    """Render a systemd unit template with the actual install directory."""
    content = template_path.read_text()
    home_dir = str(Path.home())
    # Replace %h/Yeliztli with the actual install dir
    content = content.replace("%h/Yeliztli", str(install_dir))
    # Ensure PATH includes common Python install locations with expanded home
    python_bin_dir = str(Path(_find_python()).parent)
    content = content.replace(
        "Environment=PATH=%h/.local/bin:/usr/bin",
        f"Environment=PATH={python_bin_dir}:{home_dir}/.local/bin:/usr/local/bin:/usr/bin",
    )
    return content


def install_systemd() -> None:
    """Install and enable systemd user units on Linux/WSL2."""
    if not _has_systemd():
        print("  [warn] systemd user session not available.")
        print("  On WSL2, enable systemd in /etc/wsl.conf:")
        print("    [boot]")
        print("    systemd=true")
        print("  Then restart WSL with: wsl --shutdown")
        print()
        print("  You can still run Yeliztli manually:")
        print(f"    cd {_repo_root()}")
        print("    uvicorn backend.main:app --host 127.0.0.1 --port 8000 &")
        print("    huey_consumer backend.tasks.huey_tasks.huey -w 1 &")
        return

    install_dir = _repo_root()
    systemd_src = install_dir / "systemd"
    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)

    for unit in SYSTEMD_UNITS:
        src = systemd_src / unit
        dst = SYSTEMD_USER_DIR / unit

        if not src.exists():
            print(f"  [warn] Template not found: {src}")
            continue

        rendered = _render_systemd_unit(src, install_dir)
        dst.write_text(rendered)
        print(f"  Installed: {dst}")

    # Reload and enable
    _run(["systemctl", "--user", "daemon-reload"])
    for unit in SYSTEMD_UNITS:
        _run(["systemctl", "--user", "enable", unit], check=False)
        _run(["systemctl", "--user", "start", unit], check=False)
        print(f"  Enabled and started: {unit}")


def uninstall_systemd() -> None:
    """Stop, disable, and remove systemd user units on Linux/WSL2."""
    for unit in SYSTEMD_UNITS:
        _run(["systemctl", "--user", "stop", unit], check=False)
        _run(["systemctl", "--user", "disable", unit], check=False)
        unit_path = SYSTEMD_USER_DIR / unit
        if unit_path.exists():
            unit_path.unlink()
            print(f"  Removed: {unit_path}")
    _run(["systemctl", "--user", "daemon-reload"], check=False)


def status_systemd() -> None:
    """Check systemd unit status on Linux/WSL2."""
    if not _has_systemd():
        print("  systemd not available")
        return
    for unit in SYSTEMD_UNITS:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            capture_output=True,
            text=True,
        )
        state = result.stdout.strip() or "unknown"
        print(f"  {unit}: {state}")


# ── Health check ───────────────────────────────────────────


def health_check(host: str = "127.0.0.1", port: int = 8000) -> bool:
    """Check if the API server is responding."""
    import urllib.error
    import urllib.request

    url = f"http://{host}:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


# ── Main commands ──────────────────────────────────────────


def cmd_install(args: argparse.Namespace) -> int:
    """Run the full install sequence."""
    plat = _detect_platform()
    print(f"Platform: {plat}")
    print()

    # 1. Data directory
    print("[1/4] Creating data directory...")
    ensure_data_dir()
    print()

    # 2. pip install
    if not args.skip_pip:
        print("[2/4] Installing Python package...")
        _run([_find_python(), "-m", "pip", "install", "-e", str(_repo_root())])
        print()
    else:
        print("[2/4] Skipping pip install (--skip-pip)")
        print()

    # 3. Frontend build
    if not args.skip_frontend:
        print("[3/4] Building frontend...")
        build_frontend()
        print()
    else:
        print("[3/4] Skipping frontend build (--skip-frontend)")
        print()

    # 4. Service installation
    print("[4/4] Installing services...")
    if plat == "macos":
        install_launchd()
    else:
        install_systemd()
    print()

    print("Installation complete!")
    print(f"  Data directory: {DATA_DIR}")
    print("  API server:     http://127.0.0.1:8000")
    print("  Open in browser to start the setup wizard.")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Uninstall services (does not remove data)."""
    plat = _detect_platform()
    print(f"Platform: {plat}")
    print()

    print("Stopping and removing services...")
    if plat == "macos":
        uninstall_launchd()
    else:
        uninstall_systemd()
    print()

    if args.remove_data:
        if DATA_DIR.exists():
            print(f"Removing data directory: {DATA_DIR}")
            shutil.rmtree(DATA_DIR)
            print("  Done.")
        else:
            print(f"Data directory not found: {DATA_DIR}")
    else:
        print(f"Data directory preserved: {DATA_DIR}")
        print("  Use --remove-data to delete it.")

    print()
    print("Uninstall complete.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Show service status."""
    plat = _detect_platform()
    print(f"Platform: {plat}")
    print()

    print("Services:")
    if plat == "macos":
        status_launchd()
    else:
        status_systemd()
    print()

    print("Health check:")
    if health_check():
        print("  API server: healthy")
    else:
        print("  API server: not responding")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    """Start services."""
    plat = _detect_platform()
    if plat == "macos":
        for label in LAUNCHD_LABELS:
            plist = LAUNCHD_DIR / f"{label}.plist"
            if plist.exists():
                _run(["launchctl", "load", str(plist)], check=False)
            else:
                print(f"  [skip] Not installed: {plist}")
    else:
        for unit in SYSTEMD_UNITS:
            _run(["systemctl", "--user", "start", unit], check=False)
    print("Services started.")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    """Stop services."""
    plat = _detect_platform()
    if plat == "macos":
        for label in LAUNCHD_LABELS:
            plist = LAUNCHD_DIR / f"{label}.plist"
            if plist.exists():
                _run(["launchctl", "unload", str(plist)], check=False)
    else:
        for unit in SYSTEMD_UNITS:
            _run(["systemctl", "--user", "stop", unit], check=False)
    print("Services stopped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for yeliztli-setup."""
    parser = argparse.ArgumentParser(
        prog="yeliztli-setup",
        description="Yeliztli native install manager",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # install
    p_install = subparsers.add_parser("install", help="Install Yeliztli services")
    p_install.add_argument("--skip-pip", action="store_true", help="Skip pip install step")
    p_install.add_argument("--skip-frontend", action="store_true", help="Skip frontend build step")
    p_install.set_defaults(func=cmd_install)

    # uninstall
    p_uninstall = subparsers.add_parser("uninstall", help="Remove Yeliztli services")
    p_uninstall.add_argument(
        "--remove-data",
        action="store_true",
        help="Also remove ~/.yeliztli data directory",
    )
    p_uninstall.set_defaults(func=cmd_uninstall)

    # status
    p_status = subparsers.add_parser("status", help="Show service status")
    p_status.set_defaults(func=cmd_status)

    # start
    p_start = subparsers.add_parser("start", help="Start services")
    p_start.set_defaults(func=cmd_start)

    # stop
    p_stop = subparsers.add_parser("stop", help="Stop services")
    p_stop.set_defaults(func=cmd_stop)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
