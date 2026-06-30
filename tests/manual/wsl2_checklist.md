# WSL2 Manual Testing Checklist

## Pre-release WSL2 Verification

Run these checks on a WSL2 (Ubuntu 22.04+) environment before each release.
WSL2 is not testable in GitHub Actions CI, so this checklist must be completed
manually before tagging a release.

**Tested on:** _________________ (date)
**WSL2 distro/version:** _________________ (e.g., Ubuntu 24.04)
**Windows version:** _________________ (e.g., Windows 11 23H2)

---

### 1. Environment Prerequisites

- [ ] WSL2 kernel is 5.15+ (`uname -r`)
- [ ] systemd is enabled (`systemctl --user is-system-running` returns running/degraded)
- [ ] Python 3.12+ available (`python3 --version`)
- [ ] Node 20+ available (`node --version`)
- [ ] npm available (`npm --version`)
- [ ] curl available (`curl --version`)

### 2. Installation

- [ ] `pip install -e ".[dev]"` succeeds without errors
- [ ] `cd frontend && npm install` succeeds without errors
- [ ] `yeliztli-setup --help` prints usage

### 3. Data Directory

- [ ] `yeliztli-setup install --skip-pip --skip-frontend` creates `~/.yeliztli/`
- [ ] `~/.yeliztli/samples/` exists
- [ ] `~/.yeliztli/downloads/` exists
- [ ] `~/.yeliztli/logs/` exists

### 4. Backend

- [ ] `make test-backend` passes (all tests green)
- [ ] `make run-api` starts uvicorn without errors
- [ ] Health endpoint responds: `curl -s http://localhost:8000/api/health` returns 200
- [ ] Platform detected as `wsl2` (check startup logs)

### 5. Frontend

- [ ] `make test-frontend` passes (all tests green)
- [ ] `make build-frontend` produces `frontend/dist/index.html`
- [ ] `make run-frontend` starts Vite dev server without errors
- [ ] Browser (Windows) can access http://localhost:5173

### 6. systemd Services

- [ ] Units install: `systemctl --user enable yeliztli-api yeliztli-huey`
- [ ] Units start: `systemctl --user start yeliztli-api yeliztli-huey`
- [ ] API service active: `systemctl --user is-active yeliztli-api` returns active
- [ ] Huey service active: `systemctl --user is-active yeliztli-huey` returns active
- [ ] Health check after service start: `curl -s http://localhost:8000/api/health`
- [ ] Units stop: `systemctl --user stop yeliztli-api yeliztli-huey`
- [ ] Units uninstall: `yeliztli-setup uninstall` succeeds

### 7. systemd Not Available (Fallback)

If systemd is not enabled in WSL2:

- [ ] Installer prints helpful message about enabling systemd
- [ ] Manual start works: `python -m backend.main &`
- [ ] Manual huey works: `huey_consumer backend.tasks.huey_tasks.huey -w 1 &`

### 8. Docker (Inside WSL2)

- [ ] Docker engine available (`docker --version`)
- [ ] `docker compose build` succeeds
- [ ] `docker compose up -d` starts both services
- [ ] `curl -s http://localhost:8000/api/health` returns 200
- [ ] `docker compose ps` shows both api and huey running
- [ ] `docker compose down -v` cleans up

### 9. E2E Tests

- [ ] `npx playwright install --with-deps chromium` installs browser
- [ ] `make test-e2e` passes (Chromium)

### 10. Cross-Platform-Specific Checks

- [ ] File paths use forward slashes (no Windows backslash issues)
- [ ] SQLite databases create and open correctly in `~/.yeliztli/`
- [ ] WAL mode works (no locking issues with WSL2 filesystem)
- [ ] No permission errors on file creation/deletion in home directory
- [ ] localhost binding works from both WSL2 and Windows browser

---

### Sign-off

- [ ] All checks above pass
- **Tester:** _________________
- **Date:** _________________
- **Release version:** _________________
