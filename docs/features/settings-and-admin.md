# Settings & admin

Open settings from the gear icon in the sidebar.

## Database management

- View installed reference-database versions and sizes.
- Trigger manual updates for individual databases.
- Configure the auto-update schedule (startup / daily / weekly) and an optional bandwidth
  window for large downloads.
- View the update-history log.

See [reference data](../install/reference-data.md) and [updating](../install/updating.md).

## System health

An admin panel showing:

- **Log explorer** — search and filter structured application logs. Log entries may include
  local sample paths and analysis metadata such as variant identifiers, gene symbols,
  coordinates, and error details. Current versions redact structured genotype-like fields from
  new logs, but older entries may contain them; review logs before sharing from a shared or
  support workflow.
- **Database stats** — row counts, file sizes, and last-modified dates.
- **Disk usage** — storage broken down by database and sample.
- **Database health** — each database's state (Ready / Downloading / Building / Partial /
  Corrupt / Failed / Not installed) with an integrity check that confirms it's actually
  readable by the annotation engine. For one that needs attention you can **Resume** an
  interrupted download, **Verify** it with a deep integrity check, or **Clean** a
  partial/corrupt artifact so it can be re-downloaded.

## Authentication

Optionally protect your instance with a PIN or password:

1. Go to **Settings → Authentication**.
2. Enable authentication and set a PIN/password (stored only as a salted bcrypt hash).
3. Sessions expire after a period of inactivity. Once a password exists, API routes require
   a valid session except for health, login, and auth-status endpoints; first-run setup
   endpoints remain reachable only before a password has been configured.

## Theme

Switch between **Light**, **Dark**, and **System** (follows your OS) from the top navigation
or Settings.

## Backup & restore

Export a `.tar.gz` of your samples and configuration, and restore it later — see
[backup & restore](../install/backup-restore.md).
