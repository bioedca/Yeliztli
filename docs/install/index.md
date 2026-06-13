# Install & self-host

This section is the complete reference for installing, configuring, and running your own
Yeliztli instance. If you just want the fastest path, the **[native install](native-install.md)**
is recommended for day-to-day use.

## Choose how to run it

- **[Native install](native-install.md)** — install the Python package and frontend, then
  run Yeliztli as a background service (launchd on macOS, systemd on Linux/WSL2). Best for
  daily use.
- **[Docker Compose](docker.md)** — run the API and background worker as containers with a
  persistent data volume. Good if you already use Docker.
- **Development mode** — hot-reloading backend + frontend for contributors, via
  `make dev` (starts the API, the Vite dev server, and the background worker together).

## After installing

1. Open the app and complete the **[setup wizard](setup-wizard.md)** — disclaimer, storage
   path, optional external services, reference-data download, and your first upload.
2. Understand what gets downloaded and how much space it needs:
   **[reference data](reference-data.md)**.
3. Tune behaviour in **[configuration](configuration.md)** if you need non-default ports,
   authentication, or paths.

!!! tip "Check requirements first"
    Confirm your machine meets the [system requirements](system-requirements.md) —
    especially **free disk space**, which is larger than you might expect once the
    reference databases are downloaded.

Need help? See **[troubleshooting](troubleshooting.md)**.
