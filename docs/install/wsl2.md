# Windows (WSL2)

Yeliztli runs on Windows through **WSL2** (Windows Subsystem for Linux). It is not supported
directly on native Windows.

## Set up

1. Install WSL2 with a Linux distribution (Ubuntu is a good default).
2. Inside WSL2, install **Python 3.12+** and **Node 20+**.
3. Follow the [native install](native-install.md) steps inside your WSL2 shell.
4. Open **[http://localhost:8000](http://localhost:8000)** in your Windows browser.
   `localhost` is normally shared between Windows and WSL2; if the page doesn't load, see
   [Can't reach the app from your browser?](#cant-reach-the-app-from-your-browser) below.

## Run the dev server

For local development, run the backend, the Vite dev server, and the Huey worker together.
On WSL2 use the **`dev-wsl`** target so the servers bind to `0.0.0.0` and your Windows-host
browser can reach them:

```bash
make dev-wsl
```

Then open **[http://localhost:5173](http://localhost:5173)**. `make dev-wsl` is the
WSL-friendly counterpart of `make dev`: it runs `backend.main` with `YELIZTLI_HOST=0.0.0.0`
and starts Vite with `--host`. Plain `make dev` binds to `127.0.0.1` only.

!!! warning "LAN exposure"
    Binding to `0.0.0.0` exposes the dev servers to **your local network**, not just your PC.
    That is fine on a trusted network, but for a private-by-default setup prefer **mirrored
    networking** (below), which keeps the loopback bind and still lets `localhost` work from
    Windows. Don't bind `0.0.0.0` on an untrusted network.

## Can't reach the app from your browser?

If a server starts inside WSL2 but the browser shows **"This site can't be reached"**, it is
bound to `127.0.0.1` (loopback) and WSL2's `localhost` forwarding to Windows isn't reaching it.
Fix it one of these ways.

**Bind to all interfaces (quickest).** Run `make dev-wsl`, or set `YELIZTLI_HOST=0.0.0.0` for
the backend and pass `--host` to Vite (`make run-frontend VITE_ARGS=--host`). Mind the
LAN-exposure caveat above.

**Mirrored networking (private, recommended).** Keep the loopback bind and make `localhost`
transparent between Windows and WSL2. Add this to `C:\Users\<you>\.wslconfig` on the
**Windows** side, then run `wsl --shutdown` from PowerShell:

```ini
[wsl2]
networkingMode=mirrored
```

**Reach WSL directly.** As a fallback, browse the WSL VM's IP instead of `localhost` — find it
with `hostname -I` inside WSL (e.g. `http://172.22.98.184:5173`).

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
