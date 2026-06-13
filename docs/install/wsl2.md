# Windows (WSL2)

Yeliztli runs on Windows through **WSL2** (Windows Subsystem for Linux). It is not supported
directly on native Windows.

## Set up

1. Install WSL2 with a Linux distribution (Ubuntu is a good default).
2. Inside WSL2, install **Python 3.12+** and **Node 20+**.
3. Follow the [native install](native-install.md) steps inside your WSL2 shell.
4. Open **[http://localhost:8000](http://localhost:8000)** in your Windows browser —
   `localhost` is shared between Windows and WSL2.

## Enable systemd

Yeliztli's background services use `systemd` on Linux/WSL2. Enable systemd in your distro by
adding this to `/etc/wsl.conf`:

```ini
[boot]
systemd=true
```

Then restart WSL2 from PowerShell:

```powershell
wsl --shutdown
```

After WSL2 restarts, `yeliztli-setup install` can register the services, and
`loginctl enable-linger "$USER"` makes them start automatically.

!!! note
    Keep your data and the repository **inside the WSL2 filesystem** (e.g. under your Linux
    home directory) rather than on a mounted Windows drive (`/mnt/c/...`) for much better
    performance.
