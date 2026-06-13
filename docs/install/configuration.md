# Configuration

Yeliztli reads its configuration from `~/.yeliztli/config.toml` (the setup wizard writes it
there). Any setting can also be overridden with a `YELIZTLI_`-prefixed environment variable.

## Resolution order

Settings are resolved highest-priority first:

1. Environment variables (`YELIZTLI_PORT=9000`)
2. `~/.yeliztli/config.toml`
3. A `.env` file in the project directory
4. Built-in defaults

## Example `config.toml`

```toml
# All settings live under the [yeliztli] table. The setup wizard writes them here;
# hand-edits must stay under this header.
[yeliztli]
# Server
host = "127.0.0.1"
port = 8000
debug = false

# Paths
# Note: data_dir is NOT set here. It defines *where* this config.toml lives, so it
# cannot be read back from it — set it with the YELIZTLI_DATA_DIR environment
# variable (or via the setup wizard) instead.

# Authentication (optional)
auth_enabled = false
auth_password_hash = ""        # bcrypt hash — set via the Settings UI, not by hand
session_timeout_hours = 4

# External services (optional)
pubmed_email = "your@email.com"
omim_api_key = ""

# Updates
update_check_interval = "daily"          # "startup", "daily", "weekly"
# update_download_window = "02:00-06:00" # optional bandwidth window

# Ancestry Tier-2 (LAI) — only used if you install the LAI bundle
# lai_java_mem = "4g"

# UI
theme = "system"               # "light", "dark", "system"

# Database
wal_mode = true

# Logging
log_level = "INFO"             # DEBUG, INFO, WARNING, ERROR
```

## Common settings

| Setting | Env var | Default | Purpose |
|---------|---------|---------|---------|
| `host` | `YELIZTLI_HOST` | `127.0.0.1` | Bind address. Keep it on loopback for local-only access. |
| `port` | `YELIZTLI_PORT` | `8000` | Server port. |
| `data_dir` | `YELIZTLI_DATA_DIR` | `~/.yeliztli` | Where all databases, samples, and logs live. Set via the **env var only** — it cannot be configured in `config.toml`. |
| `auth_enabled` | `YELIZTLI_AUTH_ENABLED` | `false` | Require a PIN/password to use the app. |
| `pubmed_email` | `YELIZTLI_PUBMED_EMAIL` | `""` | Contact email for NCBI literature lookups. |
| `omim_api_key` | `YELIZTLI_OMIM_API_KEY` | `""` | Optional OMIM enrichment key. |
| `theme` | `YELIZTLI_THEME` | `system` | UI theme. |
| `log_level` | `YELIZTLI_LOG_LEVEL` | `INFO` | Logging verbosity. |

!!! note "Authoritative list"
    The complete, always-current set of settings (including paths derived from `data_dir`)
    is defined in [`backend/config.py`](https://github.com/bioedca/Yeliztli/blob/main/backend/config.py).
