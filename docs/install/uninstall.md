# Uninstall

## Native install

```bash
yeliztli-setup uninstall               # remove the background services, keep your data
yeliztli-setup uninstall --remove-data # remove services, data, and control files
pip uninstall yeliztli
```

`uninstall` (without `--remove-data`) leaves your data directory untouched, so you can
reinstall later and pick up where you left off.

`--remove-data` deletes the configured native data directory that Yeliztli would use at
startup, including a storage path chosen in the setup wizard or supplied through
`YELIZTLI_DATA_DIR`. If that directory is separate from the default `~/.yeliztli/`
control/config directory, the default directory is removed too.

If a custom data directory contains files that do not look like Yeliztli data,
`--remove-data` removes Yeliztli sample databases and known app artifacts but leaves the
directory and unrelated files in place. Set `YELIZTLI_DATA_DIR` to an absolute path before
running uninstall; relative data paths are refused for destructive removal. If the custom
path is a symlink, uninstall removes the link itself, not the symlink target.

## Docker

```bash
docker compose down -v   # remove containers and the Docker data volume
```

To keep your data volume for later, omit `-v`:

```bash
docker compose down      # remove containers only
```

If you configured a host-directory bind mount such as `/path/to/your/data:/data`,
`docker compose down -v` does not delete that host directory. Remove the host directory
yourself when you want to erase those Docker-hosted samples.

!!! warning "Removing data is permanent"
    `--remove-data` deletes native samples and configuration. `docker compose down -v`
    deletes samples stored in the Docker volume, but not a bind-mounted host directory.
    [Export a backup](backup-restore.md) first if you might want them again.
