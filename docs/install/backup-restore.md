# Backup & restore

Your data lives entirely in the data directory (default `~/.yeliztli/`), so backups are
ordinary local files that stay under your control. Restore still enforces the
compatibility checks described below before it writes anything into your data
directory.

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

### Version compatibility

Sample databases in a backup record the VEP consequence bundle version they were
annotated against. When you restore onto an install that already has a recorded
VEP consequence bundle, Yeliztli requires the installed bundle **major version** to
match the backed-up samples. A major-version mismatch in either direction stops
the whole restore with a bundle-version error before files are extracted.

This most often happens after upgrading Yeliztli to a release with a newer major
VEP bundle, or when moving a backup to another machine that already has a
different bundle major installed. Very old backups that do not record a sample
bundle version are treated as `v1.0.0` for this check.

To recover, either:

- restore the archive into a fresh install before installing or downloading a VEP
  consequence bundle; a fresh install with no recorded bundle skips this
  comparison, or
- install or select the same VEP consequence bundle major version that the backup
  samples were annotated against, then retry the restore.

After restore, keep the samples on a matching bundle major or re-annotate them as
part of a deliberate upgrade path.

!!! tip "Plain files, too"
    Because everything is just files under the data directory, you can also back it
    up with your normal file-backup or disk-snapshot tooling. A whole-directory copy
    preserves every runtime file exactly as-is. Treat that directory as sensitive —
    it contains your genetic data (see [Privacy](../privacy.md)).
