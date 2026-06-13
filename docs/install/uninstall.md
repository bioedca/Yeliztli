# Uninstall

## Native install

```bash
yeliztli-setup uninstall               # remove the background services, keep your data
yeliztli-setup uninstall --remove-data # remove services and all data
pip uninstall yeliztli
```

`uninstall` (without `--remove-data`) leaves your data directory untouched, so you can
reinstall later and pick up where you left off.

## Docker

```bash
docker compose down -v   # remove containers and the data volume
```

To keep your data volume for later, omit `-v`:

```bash
docker compose down      # remove containers only
```

!!! warning "Removing data is permanent"
    `--remove-data` and `docker compose down -v` delete your samples and configuration.
    [Export a backup](backup-restore.md) first if you might want them again.
