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
auth_password_hash = ""        # bcrypt hash; normally set via Settings, not by hand.
                               # Clear this during forgotten-password recovery; see Troubleshooting.
session_timeout_hours = 4

# External services (optional)
pubmed_email = "your@email.com"
omim_api_key = ""

# Updates
update_check_interval = "daily"          # "startup", "daily", "weekly"
# update_download_window = "02:00-06:00" # optional bandwidth window

# Ancestry Tier-2 (LAI) — only used if you install the LAI bundle
# lai_java_mem = "4g"

# HLA imputation (optional) — only used if you provision R + HIBAG + BYO models
# hibag_rscript = "/usr/bin/Rscript"
# hibag_model_dir = "/path/to/hibag-models"

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
| `host` | `YELIZTLI_HOST` | `127.0.0.1` | Bind address. Keep it on loopback for local-only access; binding to `0.0.0.0`, `::`, a LAN IP, or a hostname makes the app reachable from other machines. |
| `port` | `YELIZTLI_PORT` | `8000` | Server port. |
| `data_dir` | `YELIZTLI_DATA_DIR` | `~/.yeliztli` | Where all databases, samples, and logs live. Set via the **env var only** — it cannot be configured in `config.toml`. |
| `auth_enabled` | `YELIZTLI_AUTH_ENABLED` | `false` | Require a PIN/password to use the app. This protects requests only when a password hash is also configured. |
| `auth_password_hash` | `YELIZTLI_AUTH_PASSWORD_HASH` | `""` | bcrypt hash for the PIN/password. If this is empty, requests remain open even when `auth_enabled` is `true`. Normally set this through **Settings → Authentication**; clear it by hand only when recovering from a forgotten password. |
| `pubmed_email` | `YELIZTLI_PUBMED_EMAIL` | `""` | Contact email for NCBI literature lookups. |
| `omim_api_key` | `YELIZTLI_OMIM_API_KEY` | `""` | Optional OMIM enrichment key. |
| `hibag_rscript` | `YELIZTLI_HIBAG_RSCRIPT` | unset | Optional path to `Rscript`, or a directory containing it, for the operator-provisioned HIBAG HLA imputation runtime. When unset, Yeliztli tries `Rscript` on `PATH`. |
| `hibag_model_dir` | `YELIZTLI_HIBAG_MODEL_DIR` | unset | Optional directory containing BYO ancestry-specific HIBAG model files named `{ancestry}-HLA4.RData`. Required before the HLA (imputed) page can be populated. |
| `theme` | `YELIZTLI_THEME` | `system` | UI theme. |
| `log_level` | `YELIZTLI_LOG_LEVEL` | `INFO` | Logging verbosity. |

!!! note "Authoritative list"
    The complete, always-current set of settings (including paths derived from `data_dir`)
    is defined in [`backend/config.py`](https://github.com/bioedca/Yeliztli/blob/main/backend/config.py).

## Optional HLA imputation runtime

The **[HLA (imputed)](../modules/hla.md)** page is off by default. To populate it, an operator
must install R + Bioconductor `HIBAG`, supply ancestry-specific model files, configure
`hibag_rscript` / `hibag_model_dir`, and run the HLA prediction script for each sample. Missing
runtime pieces are reported as unavailable rather than fatal; other modules continue to work.

## Launch behavior

Native services and `make run-api` start Yeliztli through `python -m backend.main`, so
`host` / `port` from `config.toml` and `YELIZTLI_HOST` / `YELIZTLI_PORT` are used when the
API binds.

Docker Compose is slightly different because Docker has two network layers:

- inside the container, the API binds to `YELIZTLI_HOST=0.0.0.0` so Docker can publish it;
- on the Docker host, Compose publishes only loopback by default via
  `YELIZTLI_PUBLISH_HOST=127.0.0.1`;
- when set in your shell or `.env`, `YELIZTLI_PORT` controls both the API port inside the
  container and the host port mapping.

Examples:

```bash
YELIZTLI_PORT=9000 make run-api
YELIZTLI_PORT=9000 docker compose up -d
YELIZTLI_PUBLISH_HOST=0.0.0.0 YELIZTLI_PORT=9000 docker compose up -d
```

## Exposing Yeliztli to your network

Yeliztli's safe default is loopback-only: `host = "127.0.0.1"` serves the app only on the
computer running it. If you change `host` to a non-loopback value such as `0.0.0.0`, `::`,
or a LAN address, every reachable client can access the full API, including samples,
variants, reports, and clinical findings.

For Docker Compose, change `YELIZTLI_PUBLISH_HOST` instead of `YELIZTLI_HOST` to publish the
container beyond the Docker host's loopback interface.

Before binding beyond loopback, enable authentication **and set a password**. Setting
`auth_enabled = true` without a non-empty `auth_password_hash` is still passwordless and
does not protect the API. For remote access, prefer a reverse proxy with TLS and avoid
exposing uvicorn directly to the internet. At startup, Yeliztli logs a security warning when
it sees a non-loopback bind without effective authentication.
