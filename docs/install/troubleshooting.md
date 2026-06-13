# Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `ImportError: cannot import name 'UTC'` | Python older than 3.12 | Install Python 3.12+. |
| `ModuleNotFoundError: No module named 'backend'` | Package not installed | Run `pip install -e .` (or `".[dev]"` for development) from the repo root. |
| Node version errors during `npm install` | Node older than 20 | Install Node 20+ (e.g. `nvm install 20`). |
| `database is locked` / SQLite WAL errors | Concurrent writes without WAL mode | Ensure `wal_mode = true` in your [config](configuration.md) (it is the default). |
| Annotation never finishes | Background worker not running | Start it with `make run-huey`, or use `make dev` / the installed services. |
| Blank page at `localhost:5173` | Backend not running | In development, start both servers with `make dev`. |
| A reference-database download fails | Network interruption | Re-run it from **Settings → Database Management** — downloads are **resumable**. Check **Settings → System Health → Database Health** for the specific error. |
| Ancestry "chromosome painting" (Tier-2) is unavailable | The LAI bundle or Java is missing | Tier-1 ancestry still works without it. For Tier-2, install **Java 8+** and download the optional LAI bundle (see [reference data](reference-data.md)). |
| Port 8000 already in use | Another process owns the port | Set a different port: `YELIZTLI_PORT=9000` (see [configuration](configuration.md)). |

## Still stuck?

- Check the service logs: `journalctl --user -u yeliztli-api` (Linux/WSL2) or
  `~/Library/Logs/yeliztli-*.log` (macOS); for Docker, `docker compose logs -f`.
- Confirm your machine meets the [system requirements](system-requirements.md), especially
  free disk space.
- Open an issue at [github.com/bioedca/Yeliztli](https://github.com/bioedca/Yeliztli/issues)
  — please use **synthetic/test data**, never your real genotype file, in any attachment.
