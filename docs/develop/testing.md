# Testing

Yeliztli has backend (pytest), frontend (Vitest), and end-to-end (Playwright) suites, run in
**tiers** so pull requests stay fast while `main` keeps broad coverage.

## Running the tests

```bash
# Backend — fast tier (what PRs run)
python -m pytest tests/backend/ -v -m "not slow"
make test-backend

# Frontend unit/component tests
cd frontend && npm test          # or: make test-frontend

# End-to-end (boots the app; cross-browser)
npx playwright test              # or: make test-e2e

# Everything except E2E
make test
```

Non-watch frontend test runs (`npm test`, `npm run test:ci`, and `npm run
test:coverage`) fail on React `not wrapped in act(...)` warnings as well as
Vitest assertion failures. CI and release jobs use the same strict command
through `npm run test:ci`; `npm run test:watch` remains a local watch-mode
command for iteration.

The **slow tier** (`-m slow`) holds long-running benchmarks and accuracy validations; it runs
**nightly**, not on every PR.

## CI tiers

CI (`.github/workflows/ci.yml`) is organised so a single **`ci-required`** check aggregates
everything:

- **Tier 1 — every PR (Linux):** lint, backend tests (fast), frontend tests, frontend build,
  install smoke test, Docker build, the strict **docs build**, and workflow lint. Path filters
  skip irrelevant jobs (a docs-only PR skips the backend suite, etc.), and skipped jobs count
  as pass.
- **Tier 2 — merge to `main` / merge queue:** the macOS backend + smoke-install legs and the
  three-browser **E2E** suite — portability gates that block the merge rather than every PR.
- **Nightly:** the slow tier (with the 600k annotation benchmark in a job of its own) plus a
  cross-OS backstop. A job that fails or runs past its time budget files a regression issue,
  or comments on the one already open.

Browser installation has a separate 60-minute command budget, a 65-minute step
backstop, and a 120-minute whole-job budget for setup, installation, tests and
diagnostics. The installation allowance retains headroom over the successful
33m09s WebKit install recorded in [#2600](https://github.com/bioedca/Yeliztli/issues/2600).
The command bounds the whole logging
pipeline and its process group, with a 30-second TERM-to-KILL grace period.
The supervisor stays alive through escalation so descendants that ignore TERM
are killed before diagnostics upload. Any nonzero phase exit still fails the gate.

Each browser/run attempt retains a `playwright-install-<browser>-<attempt>`
artifact for seven days, including installer stdout/stderr and JSON outcome,
exit code, elapsed seconds, configured budget, kill grace and whether the
supervisor observed TERM. Exit 124 is labelled `timeout_status`. Exit 137 is
also labelled `timeout_status` when the supervisor recorded TERM during the
deadline cleanup; otherwise it is labelled `kill_status`, since KILL alone
does not identify its cause. These diagnostics upload even when browser tests never
start. All three browser projects remain required.

!!! tip "Verify UI changes before merging"
    Because the E2E suite is Tier 2 (merge-time), it's worth driving a UI change in a real
    browser before merging so a frontend regression doesn't surface only at merge.

## Test conventions

The suite follows specific assertion standards (assert real values, not just non-null;
carriage-gated modules need `hom_ref` negative controls). See [contributing](contributing.md).
