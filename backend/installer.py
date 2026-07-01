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
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

# ── Constants ──────────────────────────────────────────────

DATA_DIR = Path.home() / ".yeliztli"

LAUNCHD_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCHD_LABELS = ("com.yeliztli.api", "com.yeliztli.huey")

SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
SYSTEMD_UNITS = ("yeliztli-api.service", "yeliztli-huey.service")

LOG_DIR_MACOS = Path.home() / "Library" / "Logs"

_YELIZTLI_DATA_ARTIFACT_NAMES = frozenset(
    {
        ".claims",
        ".disclaimer_accepted",
        "alphamissense.db",
        "dbnsfp.db",
        "encode_ccres.db",
        "gnomad_af.db",
        "grch37.fa",
        "gtex_eqtl.db",
        "huey.db",
        "imputation_panel",
        "lai_bundle",
        "lai_bundle.tar.gz",
        "overlays",
        "reference.db",
        "samples",
        "saved_queries.json",
        "spliceai.db",
        "vep_bundle.db",
    }
)


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


def _effective_data_dir() -> Path:
    """Return the data directory Yeliztli would use at runtime."""
    from backend.config import get_settings

    return get_settings().data_dir


def _uninstall_data_dirs() -> list[Path]:
    """Return unique native data/control directories removed by --remove-data."""
    from backend.config import DEFAULT_DATA_DIR

    seen: set[Path] = set()
    data_dirs: list[Path] = []
    for path in (_effective_data_dir(), DEFAULT_DATA_DIR):
        expanded = path.expanduser()
        key = expanded.absolute() if expanded.is_absolute() else expanded
        if key in seen:
            continue
        seen.add(key)
        data_dirs.append(expanded)
    return data_dirs


def _default_data_dir() -> Path:
    """Return the fixed control/config directory for native installs."""
    from backend.config import DEFAULT_DATA_DIR

    return DEFAULT_DATA_DIR.expanduser()


def _same_data_dir(left: Path, right: Path) -> bool:
    """Compare paths after expanding symlinks for duplicate/ownership checks."""
    return left.expanduser().resolve(strict=False) == right.expanduser().resolve(strict=False)


def _contains_path(container: Path, child: Path) -> bool:
    """Whether ``child`` is inside or equal to ``container``."""
    try:
        child.relative_to(container)
    except ValueError:
        return False
    return True


def _validate_removable_data_dir(path: Path) -> None:
    """Refuse obviously unsafe uninstall targets."""
    expanded = path.expanduser()
    if not expanded.is_absolute():
        msg = f"Refusing to remove relative data directory: {path}"
        raise ValueError(msg)

    resolved = expanded.resolve(strict=False)
    root = Path(resolved.anchor).resolve(strict=False)
    home = Path.home().resolve(strict=False)
    repo_root = _repo_root().resolve(strict=False)
    if (
        resolved in (root, home)
        or _contains_path(resolved, home)
        or _contains_path(resolved, repo_root)
    ):
        msg = f"Refusing to remove unsafe data directory: {path}"
        raise ValueError(msg)


def _remove_path(path: Path) -> None:
    """Remove one path without following a symlink root."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)


def _remove_data_dir(path: Path, *, remove_all: bool) -> None:
    """Remove a native data/control directory or its Yeliztli-owned artifacts."""
    if not path.exists():
        print(f"Data directory not found: {path}")
        return

    if remove_all or path.is_symlink() or path.is_file():
        print(f"Removing data directory: {path}")
        _remove_path(path)
        print("  Done.")
        return

    removed_any = False
    for child in list(path.iterdir()):
        if child.name not in _YELIZTLI_DATA_ARTIFACT_NAMES:
            continue
        print(f"Removing data artifact: {child}")
        _remove_path(child)
        removed_any = True

    if not any(path.iterdir()):
        print(f"Removing empty data directory: {path}")
        path.rmdir()
    elif removed_any:
        print(f"Data directory preserved: {path}")
        print("  Non-Yeliztli entries were left in place.")
    else:
        print(f"No removable Yeliztli data artifacts found: {path}")


def _remove_uninstall_data_dirs(data_dirs: list[Path]) -> None:
    """Remove data/control targets after validation."""
    default_data_dir = _default_data_dir()
    for data_dir in data_dirs:
        _remove_data_dir(data_dir, remove_all=_same_data_dir(data_dir, default_data_dir))


def _print_preserved_data_dirs(data_dirs: list[Path]) -> None:
    """Print preserved data/control targets."""
    for data_dir in data_dirs:
        print(f"Data directory preserved: {data_dir}")
    target = "it" if len(data_dirs) == 1 else "them"
    print(f"  Use --remove-data to delete {target}.")


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
    content = content.replace("__INSTALL_DIR__", xml_escape(str(install_dir)))
    content = content.replace("__PYTHON__", xml_escape(_find_python()))
    # Expand ~ in log paths to absolute home
    content = content.replace("~/Library/Logs", xml_escape(str(LOG_DIR_MACOS)))
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


def _quote_systemd_exec_arg(value: str) -> str:
    """Quote one systemd ExecStart argument."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


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
    python_path = _find_python()
    content = content.replace("__PYTHON__", _quote_systemd_exec_arg(python_path))
    # Ensure PATH includes common Python install locations with expanded home
    python_bin_dir = str(Path(python_path).parent)
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
        print(f"    {shlex.quote(_find_python())} -m backend.main &")
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
    from backend.config import get_settings

    settings = get_settings()
    print(f"  API server:     http://{settings.host}:{settings.port}")
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

    data_dirs = _uninstall_data_dirs()
    if args.remove_data:
        try:
            for data_dir in data_dirs:
                _validate_removable_data_dir(data_dir)
        except ValueError as exc:
            print(f"[error] {exc}")
            return 1

        _remove_uninstall_data_dirs(data_dirs)
    else:
        _print_preserved_data_dirs(data_dirs)

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
        help="Also remove configured Yeliztli data and default control files",
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
