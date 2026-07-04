# Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `ImportError: cannot import name 'UTC'` | Python older than 3.12 | Install Python 3.12+. |
| `ModuleNotFoundError: No module named 'backend'` | Package not installed | Run `pip install -e .` (or `".[dev]"` for development) from the repo root. |
| Node version errors during `npm install` | Node older than 20 | Install Node 20+ (e.g. `nvm install 20`). |
| `database is locked` / SQLite WAL errors | Concurrent writes without WAL mode | Ensure `wal_mode = true` in your [config](configuration.md) (it is the default). |
| Annotation never finishes | Background worker not running | Start it with `make run-huey`, or use `make dev` / the installed services. |
| Blank page at `localhost:5173` | Backend not running | In development, start both servers with `make dev`. |
| Setup or dbNSFP looks stuck for a long time | dbNSFP is downloading a large archive or building/indexing the SQLite database after the download completes | This can normally take on the order of an hour or more, especially on slow networks or disks. Check **Settings → System Health → Database Health**: `Downloading` or `Building` means it is still active; `Failed` means you should resume, clean, or retry the database. |
| A reference-database download fails | Network interruption | Re-run it from **Settings → Database Management** — downloads are **resumable**. Check **Settings → System Health → Database Health** for the specific error. |
| Setup never completes or the dashboard stays unreachable after a reference-database failure | A required reference database is `Failed` or not integrity-ready | Check **Settings → System Health → Database Health** for **ClinVar**, **gnomAD**, **dbNSFP**, **CPIC**, **GWAS Catalog**, **dbSNP**, and **MONDO/HPO**. Resume, clean, or retry the failed required database; setup cannot complete until all required databases are `Ready`. Most optional databases can be added later from **Settings → Database Management**; manual or bring-your-own sources use their documented local ingest path. |
| Ancestry "chromosome painting" (Tier-2) is unavailable | The LAI bundle or Java is missing | Tier-1 ancestry still works without it. For Tier-2, install **Java 8+** and download the optional LAI bundle (see [reference data](reference-data.md)). |
| Port 8000 already in use | Another process owns the port | Set a different port: `YELIZTLI_PORT=9000 make run-api`, `YELIZTLI_PORT=9000 docker compose up -d`, or set `port = 9000` in `~/.yeliztli/config.toml` and restart installed services (see [configuration](configuration.md)). |
| Locked out after forgetting your PIN/password | Password changes and removal require the current password, and Yeliztli has no in-app reset flow | Use local file recovery below. |

## Recover from a forgotten PIN/password

Yeliztli authentication protects network and browser access. It is not encryption for
the files in your data directory, so anyone who can edit `~/.yeliztli/config.toml`
already has local access to the same data. If you are the local owner and forgot the
PIN/password, disable authentication from the config file and restart Yeliztli:

1. Stop the running app or installed services.
2. Open `~/.yeliztli/config.toml`.
3. Under `[yeliztli]`, set `auth_enabled = false` and clear
   `auth_password_hash = ""`. Clearing the saved hash is required before you can set a
   new PIN/password without knowing the old one.
4. Restart Yeliztli so it reloads the config file.
5. Open **Settings → Authentication** and set a new PIN/password.

Do not hand-edit `auth_password_hash` for normal password changes. Use
**Settings → Authentication** whenever you still know the current PIN/password.

## Still stuck?

- Check the service logs: `journalctl --user -u yeliztli-api` (Linux/WSL2) or
  `~/Library/Logs/yeliztli-*.log` (macOS); for Docker, `docker compose logs -f`.
  These logs may contain local sample filenames/paths, gene symbols, variant identifiers such as
  rsIDs, genomic coordinates, and error details. Current versions redact structured genotype-like
  fields before writing new logs, but older logs may contain them. Review and redact logs before
  sharing, or reproduce the issue with synthetic data first.
- Confirm your machine meets the [system requirements](system-requirements.md), especially
  free disk space.
- Open an issue at [github.com/bioedca/Yeliztli](https://github.com/bioedca/Yeliztli/issues)
  — please use **synthetic/test data**, never your real genotype file, in any attachment.
