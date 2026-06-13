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
yeliztli-setup uninstall --remove-data   # remove services and all data
```

**macOS** uses `launchd` user agents that start at login; logs go to
`~/Library/Logs/yeliztli-*.log`.

**Linux / WSL2** uses `systemd` user services. To start them automatically at boot, enable
lingering for your user:

```bash
loginctl enable-linger "$USER"
```

View logs with `journalctl --user -u yeliztli-api` (or `-u yeliztli-huey` for the worker).

### Install options

```bash
yeliztli-setup install --skip-pip        # skip the Python package install
yeliztli-setup install --skip-frontend   # skip the frontend build
```

## 3. Open the application

Visit **[http://localhost:8000](http://localhost:8000)**. On first run, the
**[setup wizard](setup-wizard.md)** launches automatically to finish configuration and
download reference data.

!!! note "On Windows?"
    Native installation runs inside **WSL2**, not Windows directly. See the
    [WSL2 notes](wsl2.md) for enabling `systemd` and accessing the app from your Windows
    browser.

## Keeping it up to date

See **[updating](updating.md)** for application and reference-database updates, and
**[backup & restore](backup-restore.md)** to snapshot your data first.
