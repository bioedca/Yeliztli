# Copilot instructions for Yeliztli

These are repository-wide instructions for GitHub Copilot (code review, chat, and
the coding agent). They encode the same contribution contract human contributors
follow — see [CONTRIBUTING.md](../CONTRIBUTING.md) for the full workflow. Treat
these as the house rules when reviewing a pull request or writing a change.

**You draft; a human owns the merge.** Copilot output — review comments, code,
tests, PR text — is a first draft that a maintainer must verify. Never imply a
change is ready to merge on its own.

## What this project is

Yeliztli is a **privacy-first, local-only personal-genomics** platform (FastAPI +
SQLAlchemy 2.0 backend, React/TypeScript frontend, MkDocs docs). It annotates
consumer genotyping-array data (23andMe, AncestryDNA) on the user's machine.
Genome coordinates are **GRCh37/b37**. Its output can look like clinical
information, so correctness — especially scientific correctness — is the top
priority.

## Contribution contract

- **One cohesive change per PR.** If a change does more than one thing (a bugfix
  *and* a rename *and* a feature), recommend splitting it — unrelated changes
  can't be reviewed or reverted independently.
- **Commits & PR titles** are imperative and specific; squashed PR subjects end
  with `(#<PR number>)`. Conventional-commit prefixes (`fix(pgx):`, `docs:`) are
  welcome but optional.
- **Every PR answers what / how / why.** The diff shows what and how; the
  description must give the why.
- **A change merges only when the review gate passes and all required CI checks
  are green.** The per-PR (Tier-1) gate is `ci-required` + `lint` (Ruff, Vulture,
  ESLint, Knip), `test-backend` (py3.12 + py3.13), `test-frontend`,
  `build-frontend`, `smoke-install`, `docs-build --strict`. **End-to-end
  (Playwright) and macOS legs are Tier-2** — they run on merge/nightly, not on the
  PR — so a UI change must be verified in a browser before merge.

## Testing standards (enforced advisorily; flag violations in review)

- Never let `assert x is not None` be the **only** assertion on a value-producing
  function, and never let `assert response.status_code == 200` be the **only**
  assertion on an endpoint that returns data. Assert the **value / body** — the
  specific field, count, row, rendered SQL, VCF `REF`/`ALT`/`GT`, or exact
  diplotype.
- Prefer **two-sided** filter checks (assert the excluded row is *absent*, not
  only that returned rows match).
- Don't guard a loop with no membership check (`for x in items: assert ...` passes
  vacuously when `items` is empty — assert non-empty first).
- Drive the production path; don't hand-overwrite the column under test in an
  "end-to-end" fixture.
- **`hom_ref` negative controls**: a genotyping chip reports a call at every probe,
  so any module emitting carriage-dependent findings needs a test seeding a
  non-carrier (`hom_ref`) Pathogenic variant and asserting it is **absent**.
  Builders live in `tests/backend/_carriage_fixtures.py`. When you add or review a
  carriage-gated module, expect this control.

## Scientific correctness & evidence (the highest bar)

- Any logic or claim that rests on a **biological, statistical, or clinical fact**
  must be verified against the peer-reviewed literature or an authoritative source
  **before it is encoded** — never on recall.
- **Carry a citation** in durable artifacts: `PMID:… / DOI:… / ChEMBL:… / NCT:…`
  plus `(accessed YYYY-MM-DD)`.
- **High-stakes claims** — allele direction/strand, risk-allele identity, a PGx
  metabolizer call, a shipped clinical threshold — need **≥2 independent, agreeing
  sources**. On disagreement, **withhold and flag**; don't pick a side. A preprint
  is never a sole basis.
- **Never fabricate, stub, or placeholder reference data**, and **never weaken a
  test** to match missing data. If the repo lacks the data an issue needs, source
  it from an authoritative public dataset (with provenance) or withhold the result
  and say so. (A placeholder is a silent bug CI won't catch — cf. the PRS
  reference distribution that was a placeholder `0/1` and inflated every percentile.)

## Third-party libraries

When writing or reviewing code that calls a third-party library (FastAPI,
Pydantic v2, SQLAlchemy 2.0, Alembic, Huey, httpx, pysam, numpy, Playwright,
Plotly, …), match the version pinned in `pyproject.toml` / `frontend/package.json`.
Don't mix incompatible-version APIs (notably SQLAlchemy 1.x vs 2.0, Pydantic v1 vs
v2).
