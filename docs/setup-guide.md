# Setup Guide

This guide covers installing and running Yeliztli on your local machine. Choose the method that fits your environment:

- [Native Install](#native-install) — recommended for daily use (macOS, Linux, WSL2)
- [Docker Compose](#docker-compose) — alternative containerized deployment
- [Development Mode](#development-mode) — for contributors

---

## System Requirements

| Requirement | Minimum |
|-------------|---------|
| Python | 3.12+ |
| Node.js | 20+ |
| Disk space | ~2 GB (app + reference databases) |
| RAM | 1 GB available |
| OS | macOS (ARM/x86), Linux, Windows (WSL2) |

Yeliztli runs entirely on localhost. No internet connection is needed after initial setup (database downloads).

---

## Native Install

### 1. Clone and install

```bash
git clone https://github.com/bioedca/Yeliztli.git
cd Yeliztli
pip install -e .
cd frontend && npm install && npm run build && cd ..
```

### 2. Install as a service

The installer sets up Yeliztli to run automatically in the background:

```bash
# Install services (auto-detects macOS/Linux/WSL2)
yeliztli-setup install

# Check status
yeliztli-setup status

# Start/stop manually
yeliztli-setup start
yeliztli-setup stop

# Uninstall (preserves your data)
yeliztli-setup uninstall

# Uninstall and remove all data
yeliztli-setup uninstall --remove-data
```

**macOS**: Uses `launchd` user agents. Services start automatically at login. Logs are written to `~/Library/Logs/yeliztli-*.log`.

**Linux / WSL2**: Uses `systemd` user services. Enable lingering for auto-start at boot:

```bash
loginctl enable-linger $USER
```

Logs are available via `journalctl --user -u yeliztli-api`.

### 3. Open the application

Navigate to [http://localhost:8000](http://localhost:8000) in your browser. The setup wizard will launch automatically on first run.

### Install options

```bash
yeliztli-setup install --skip-pip        # Skip Python package install
yeliztli-setup install --skip-frontend   # Skip frontend build
```

---

## Docker Compose

### 1. Build and start

```bash
git clone https://github.com/bioedca/Yeliztli.git
cd Yeliztli
docker compose up -d
```

This starts two services:

- **api** — FastAPI server on [http://localhost:8000](http://localhost:8000)
- **huey** — Background task worker for annotation pipeline

Data is persisted in a Docker volume (`yeliztli-data`).

### 2. Check health

```bash
docker compose ps
curl http://localhost:8000/api/health
```

### 3. View logs

```bash
docker compose logs -f          # All services
docker compose logs -f api      # API server only
docker compose logs -f huey     # Task worker only
```

### 4. Stop and restart

```bash
docker compose stop             # Stop services
docker compose start            # Restart services
docker compose down             # Stop and remove containers (data volume preserved)
docker compose down -v          # Stop and remove everything including data
```

### Custom data directory

To mount a host directory instead of a Docker volume:

```yaml
# docker-compose.override.yml
services:
  api:
    volumes:
      - /path/to/your/data:/data
  huey:
    volumes:
      - /path/to/your/data:/data
```

### Environment overrides

Configure via environment variables (prefix `YELIZTLI_`):

```bash
YELIZTLI_PORT=9000 docker compose up -d
```

Or add to `docker-compose.override.yml`:

```yaml
services:
  api:
    environment:
      - YELIZTLI_AUTH_ENABLED=true
      - YELIZTLI_LOG_LEVEL=DEBUG
```

---

## Development Mode

For contributors or those who want hot-reload during use:

```bash
git clone https://github.com/bioedca/Yeliztli.git
cd Yeliztli
pip install -e ".[dev]"
cd frontend && npm install && cd ..
make dev
```

This starts:
- Backend (FastAPI with auto-reload) on port 8000
- Frontend (Vite dev server with HMR) on port 5173

Open [http://localhost:5173](http://localhost:5173) — the Vite dev server proxies API requests to port 8000.

---

## First-Time Setup Wizard

On first launch, Yeliztli presents a 6-step setup wizard:

### Step 1: Disclaimer

Read and accept the disclaimer acknowledging that Yeliztli is for educational and informational purposes only, not a diagnostic tool.

### Step 2: Import from Backup (optional)

If you have a previous Yeliztli backup (`.tar.gz`), import it here. This restores your samples, configuration, and optionally reference databases. Skip this step for a fresh install.

### Step 3: Storage Path

Configure where Yeliztli stores its data. Default: `~/.yeliztli/`. The wizard displays available disk space and warns if space is low (< 10 GB warning, < 5 GB blocks setup).

### Step 4: External Services

- **PubMed email** (recommended) — Required by NCBI Terms of Service for literature lookups. Enables PubMed citation fetching for variant findings.
- **OMIM API key** (optional) — Enriches gene-disease associations with OMIM data. Get a key at [omim.org/api](https://omim.org/api).

### Step 5: Download Databases

Yeliztli downloads reference databases needed for variant annotation:

| Database | Size | Purpose |
|----------|------|---------|
| ClinVar | ~80 MB | Clinical variant classifications |
| gnomAD AF | ~500 MB | Population allele frequencies |
| dbNSFP | ~400 MB | In-silico pathogenicity scores |
| VEP bundle | ~500 MB | Variant consequence predictions |
| ENCODE cCREs | ~50 MB | Regulatory element annotations |
| **Total** | **~1.5 GB** | **Combined download size** |

Downloads are resumable — if interrupted, they pick up where they left off. Progress is displayed per-database via real-time streaming.

If a download is interrupted by a crash or disconnect, the wizard shows a **Resume** button for that database (it continues from the saved partial rather than restarting). At any time you can inspect every database's health — including integrity (is it readable by the annotation engine?), partial downloads, and last error — under **Settings → System Health → Database Health**, where you can Resume, Verify, or Clean a database that needs attention. A database is only treated as installed once its data is present *and* passes the integrity check, so a half-finished or corrupted download is reported honestly instead of silently failing during annotation.

### Step 6: Upload Your Data

Upload your 23andMe raw data file (`.txt` or `.zip`). Yeliztli auto-detects the file format version (v3, v4, or v5) and begins parsing. Once parsing completes, annotation runs automatically in the background.

---

## Configuration Reference

Yeliztli reads configuration from `~/.yeliztli/config.toml`. Settings can also be overridden with environment variables using the `YELIZTLI_` prefix.

### Example config.toml

```toml
# All settings live under the [yeliztli] table (the setup wizard writes them here;
# hand-edits must be under this header too).
[yeliztli]
# Server
host = "127.0.0.1"
port = 8000
debug = false

# Paths
data_dir = "~/.yeliztli"

# Authentication (optional)
auth_enabled = false
auth_password_hash = ""  # bcrypt hash — set via Settings UI
session_timeout_hours = 4

# External services
pubmed_email = "your@email.com"
omim_api_key = ""

# Updates
update_check_interval = "daily"  # "startup", "daily", "weekly"
# update_download_window = "02:00-06:00"  # Optional bandwidth window

# UI
theme = "system"  # "light", "dark", "system"

# Database
wal_mode = true

# Logging
log_level = "INFO"
```

### Resolution order

Settings are resolved in this order (highest priority first):

1. Constructor arguments (internal use)
2. Environment variables (`YELIZTLI_PORT=9000`)
3. `~/.yeliztli/config.toml`
4. `.env` file in the project directory
5. Built-in defaults

---

## WSL2 Notes

Yeliztli is fully supported on Windows via WSL2:

1. Install WSL2 with a Linux distribution (Ubuntu recommended)
2. Install Python 3.12+ and Node 20+ inside WSL2
3. Follow the [Native Install](#native-install) instructions
4. Access the app at `http://localhost:8000` from your Windows browser

The systemd service path is used on WSL2. Enable systemd in your WSL2 distribution:

```bash
# /etc/wsl.conf
[boot]
systemd=true
```

Then restart WSL2: `wsl --shutdown` from PowerShell.

---

## Updating

### Application updates

Yeliztli checks for new releases on GitHub at startup (configurable). When an update is available, a subtle indicator appears in the UI. To update:

```bash
cd Yeliztli
git pull
pip install -e .
cd frontend && npm install && npm run build && cd ..
yeliztli-setup install  # Restart services
```

### Database updates

Reference databases (ClinVar, gnomAD, etc.) can be updated from **Settings > Database Management**. You can configure:

- Per-database auto-update toggles
- Update check frequency (startup / daily / weekly)
- Bandwidth window for large downloads (> 100 MB)

Update history is logged and viewable in the Settings panel.

---

## Backup and Restore

### Export a backup

From **Settings > Backup**, export a `.tar.gz` archive containing:

- All sample databases and metadata
- Configuration (config.toml)
- Optionally: reference databases

### Import a backup

Import during initial setup (Step 2 of the wizard) or from **Settings > Backup > Import**. Yeliztli auto-detects existing installations and offers to merge or replace.

---

## Uninstalling

### Native install

```bash
yeliztli-setup uninstall              # Remove services, keep data
yeliztli-setup uninstall --remove-data # Remove services + all data
pip uninstall yeliztli
```

### Docker

```bash
docker compose down -v    # Remove containers and data volume
```

To keep your data volume for later:

```bash
docker compose down       # Remove containers only
```
