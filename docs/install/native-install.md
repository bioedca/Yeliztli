# Native install

Recommended for daily use on macOS, Linux, and Windows (WSL2). Confirm the
[system requirements](system-requirements.md) first.

## 1. Clone and install

```bash
git clone https://github.com/bioedca/Yeliztli.git
cd Yeliztli
pip install -e .
cd frontend && npm install && npm run build && cd ..
```

## 2. Install as a background service

The installer registers Yeliztli to run in the background and start automatically. It
auto-detects your platform:

```bash
yeliztli-setup install      # install + start the API and worker services
yeliztli-setup status       # show service status and a health check
yeliztli-setup start        # start services
yeliztli-setup stop         # stop services
yeliztli-setup uninstall    # remove services, keep your data
yeliztli-setup uninstall --remove-data   # remove services, data, and control files
```

`yeliztli-setup install` also installs the Playwright Chromium browser used by
**Generate PDF** and backend variant-card rendering endpoints. If you are doing a
manual or offline install and skip that step, run:

```bash
python -m playwright install chromium
```

With `--remove-data`, the installer deletes the configured data directory, including a
storage path chosen in the setup wizard or supplied through `YELIZTLI_DATA_DIR`. It also
removes the default `~/.yeliztli/` control/config directory when that directory is
separate from the configured data directory.

For custom paths that contain unrelated files, uninstall removes Yeliztli sample databases
and known app artifacts but leaves the directory and unrelated files in place. Use an
absolute `YELIZTLI_DATA_DIR` path; relative paths are refused for destructive removal. If
the custom path is a symlink, uninstall removes the link itself, not the symlink target.

**macOS** uses `launchd` user agents that start at login; logs go to
`~/Library/Logs/yeliztli-*.log`. After install, run `yeliztli-setup status` and
confirm both `com.yeliztli.api` and `com.yeliztli.huey` are running before you
leave annotation jobs unattended.

**Linux / WSL2** uses `systemd` user services. To start them automatically at boot, enable
lingering for your user:

```bash
loginctl enable-linger "$USER"
```

View logs with `journalctl --user -u yeliztli-api` (or `-u yeliztli-huey` for the worker).

The API service starts through `python -m backend.main`, so it reads `host` and `port` from
`~/.yeliztli/config.toml` and honors `YELIZTLI_HOST` / `YELIZTLI_PORT` in the service
environment. To change the installed service port, set `port = 9000` in
`~/.yeliztli/config.toml` and restart:

```bash
yeliztli-setup stop
yeliztli-setup start
```

### Install options

```bash
yeliztli-setup install --skip-pip        # skip the Python package install
yeliztli-setup install --skip-browser-install   # skip Playwright Chromium install
yeliztli-setup install --skip-frontend   # skip the frontend build
```

## 3. Open the application

Visit **[http://localhost:8000](http://localhost:8000)**, or the configured host/port if you
changed them. On first run, the
**[setup wizard](setup-wizard.md)** launches automatically to finish configuration and
download reference data.

!!! note "On Windows?"
    Native installation runs inside **WSL2**, not Windows directly. See the
    [WSL2 notes](wsl2.md) for enabling `systemd` and accessing the app from your Windows
    browser.

## Keeping it up to date

See **[updating](updating.md)** for application and reference-database updates, and
**[backup & restore](backup-restore.md)** to snapshot your data first.
