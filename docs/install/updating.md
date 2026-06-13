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
