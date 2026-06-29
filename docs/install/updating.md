# Updating

## Application updates

Yeliztli checks GitHub for new releases at startup (configurable via
`update_check_interval`). When an update is available, a subtle indicator appears in the UI.

To update a [native install](native-install.md):

```bash
cd Yeliztli
git pull
pip install -e .
cd frontend && npm install && npm run build && cd ..
yeliztli-setup install   # re-register and restart services
```

For [Docker](docker.md), rebuild and recreate the containers:

```bash
git pull
docker compose up -d --build
```

!!! tip "Back up first"
    Snapshot your data before a significant update — see [backup & restore](backup-restore.md).

## Reference-database updates

Reference data (ClinVar, gnomAD, and the rest — see [reference data](reference-data.md)) is
updated from **Settings → Database Management**, where you can configure:

- per-database auto-update toggles,
- update check frequency (startup / daily / weekly),
- an optional bandwidth window for large downloads, given as a 24-hour
  `"HH:MM-HH:MM"` range (e.g. `"02:00-06:00"`). The updates route holds large downloads to
  that window; a **Force update** action bypasses it when you need a download now.

Update history is logged and viewable in the Settings panel.

## How updates affect your existing results

Your findings are a **point-in-time snapshot**: each sample's results are computed against the
reference data installed **when that sample was annotated**. Updating reference data afterwards
does **not** change results you already have.

- **Updates don't re-annotate automatically.** Downloading newer ClinVar / gnomAD / etc. data
  leaves your existing samples' findings untouched until you re-annotate them.
- **Only ClinVar changes prompt you.** Yeliztli proactively flags a sample for re-annotation
  **only** when a ClinVar **significance** change affects one of its variants (or a variant you
  are watching). Updates to every other database — gnomAD allele frequencies, dbNSFP
  predictions, CPIC prescribing guidance, AlphaMissense, ClinGen, the GWAS catalog, the VEP
  bundle — raise **no prompt**, so a finding's frequency, in-silico evidence, or dosing guidance
  can quietly fall behind the data you just downloaded.
- **Re-annotate to refresh.** To bring a sample's findings up to date after *any* reference-data
  update, re-annotate it: accept the re-annotation prompt when one appears, or re-run annotation
  for the sample. The status bar's reference-database versions (see
  [reading your results](../getting-started/reading-your-results.md)) show which snapshot a
  result currently reflects.

!!! warning "After updating databases, re-annotate to refresh findings"
    A reference-database update only affects *new* analyses. Existing findings keep showing the
    older data — and you are notified automatically only for **ClinVar** reclassifications — until
    you re-annotate the sample.
