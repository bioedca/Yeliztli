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
- **Nightly:** the slow tier plus a cross-OS backstop; failures auto-file a regression issue.

!!! tip "Verify UI changes before merging"
    Because the E2E suite is Tier 2 (merge-time), it's worth driving a UI change in a real
    browser before merging so a frontend regression doesn't surface only at merge.

## Test conventions

The suite follows specific assertion standards (assert real values, not just non-null;
carriage-gated modules need `hom_ref` negative controls). See [contributing](contributing.md).
