# Backup & restore

Your data lives entirely in the data directory (default `~/.yeliztli/`), so backups are
simple and fully under your control.

## Export a backup

From **Settings → Backup**, export a `.tar.gz` archive containing:

- all sample databases and their metadata,
- your configuration (`config.toml`),
- optionally, the downloaded reference databases (these are large — see
  [reference data](reference-data.md) — so you may prefer to re-download them instead).

## Restore a backup

You can restore either:

- during the [setup wizard](setup-wizard.md) (Step 2 — *Import from backup*), or
- from **Settings → Backup → Import** on an existing install.

A restore **merges** the archive into your current data directory — it selectively
extracts `config.toml`, your `samples/`, and the disclaimer flag, and does **not** wipe or
overwrite anything else. When an existing installation is detected, the wizard offers
*Import Backup* (restore/merge) or *Skip — Start Fresh* (continue without restoring); skip
simply advances the wizard and leaves your data untouched.

!!! tip "Plain files, too"
    Because everything is just files under the data directory, you can also back it up with
    your normal file-backup or disk-snapshot tooling. Treat that directory as sensitive — it
    contains your genetic data (see [Privacy](../privacy.md)).
