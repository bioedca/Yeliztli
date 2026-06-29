# Backup & restore

Your data lives entirely in the data directory (default `~/.yeliztli/`), so backups are
simple and fully under your control.

## Export a backup

From **Settings → Backup**, export a `.tar.gz` archive containing:

- all sample databases,
- the sample registry metadata that makes those samples appear in the app,
  including custom sample names and individual groupings,
- your configuration (`config.toml`),
- optionally, standalone downloaded reference files such as gnomAD, dbNSFP,
  VEP, and other large file-backed bundles (see [reference data](reference-data.md)).
  Datasets stored inside `reference.db`, or installed as expanded directories,
  are not archived wholesale and can be re-downloaded on the target machine instead.

## Restore a backup

You can restore either:

- during the [setup wizard](setup-wizard.md) (Step 2 — *Import from backup*), or
- from **Settings → Backup → Import** on an existing install.

A restore **merges** the archive into your current data directory — it selectively
extracts `config.toml`, your `samples/`, the disclaimer flag, the backed-up sample
registry rows, and any optional standalone reference files included in the archive.
It does not replace the whole registry database, so existing installations keep
unrelated runtime/reference data. Reference-resident datasets can be downloaded
again after restore. When an existing installation is detected, the wizard offers
*Import Backup* (restore/merge) or *Skip — Start Fresh* (continue without
restoring); skip simply advances the wizard and leaves your data untouched.

!!! tip "Plain files, too"
    Because everything is just files under the data directory, you can also back it
    up with your normal file-backup or disk-snapshot tooling. A whole-directory copy
    preserves every runtime file exactly as-is. Treat that directory as sensitive —
    it contains your genetic data (see [Privacy](../privacy.md)).
