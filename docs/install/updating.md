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

Reference data updates are managed from **Settings → Database Management** for the sources
registered in the update manager (see [reference data](reference-data.md)). That panel currently
tracks ClinVar, dbNSFP, CPIC, GWAS Catalog, dbSNP, MONDO/HPO, ENCODE cCREs, and the published
bundles for gnomAD, VEP, LAI, and PGS scores, plus the app-shipped ancestry PCA bundle. There you
can configure:

- per-database auto-update toggles,
- update check frequency (startup / daily / weekly),
- an optional bandwidth window for large downloads, given as a 24-hour
  `"HH:MM-HH:MM"` range (e.g. `"02:00-06:00"`). The updates route holds large downloads to
  that window; a **Force update** action bypasses it when you need a download now.

Update history is logged and viewable in the Settings panel. A few installed or optional
reference sources are outside that update-manager registry: AlphaMissense, GTEx eQTL, ClinGen,
and bring-your-own SpliceAI. Yeliztli does not check upstream releases for those sources, does
not show them in the per-database auto-update table, and cannot auto-refresh them; refresh them
only through the relevant manual rebuild, local ingest, or setup flow.

## How updates affect your existing results

Your findings are a **point-in-time snapshot**: each sample's results are computed against the
reference data installed **when that sample was annotated**. Updating reference data afterwards
does **not** change results you already have.

- **Updates don't re-annotate automatically.** Downloading newer ClinVar / gnomAD / etc. data
  leaves your existing samples' findings untouched until you re-annotate them.
- **Reference-data staleness prompts are broad and neutral.** After a successful annotation,
  Yeliztli records the reference-database versions used for that sample. When any installed
  reference database later moves beyond that snapshot, Settings shows a single per-sample
  re-annotation indicator: "Reference data is newer than this analysis." Most database updates
  will not change a given sample's findings; the prompt means the analysis snapshot is behind,
  not that a clinical interpretation changed.
- **ClinVar reclassification prompts remain specific.** Yeliztli still separately flags ClinVar
  **significance** changes that affect one of a sample's variants (or a variant you are
  watching). Those prompts identify potential reclassifications; the broader reference-data
  prompt only says the sample should be re-annotated to refresh against newer sources such as
  gnomAD allele frequencies, dbNSFP predictions, CPIC prescribing guidance, the GWAS catalog,
  ENCODE cCREs, or the VEP/LAI/ancestry/PGS bundles. Sources outside the update-manager registry
  can participate only after you manually rebuild or ingest them and their installed version stamp
  changes; they are not proactively checked for upstream releases.
- **Re-annotate to refresh.** To bring a sample's findings up to date after *any* reference-data
  update, re-annotate it: accept the re-annotation prompt when one appears, or re-run annotation
  for the sample. The status bar's reference-database versions (see
  [reading your results](../getting-started/reading-your-results.md)) show which snapshot a
  result currently reflects.

!!! warning "After updating databases, re-annotate to refresh findings"
    A reference-database update only affects *new* analyses. Existing findings keep showing the
    older data until you re-annotate the sample.
