<!--
  Title: use a specific, imperative summary — e.g. "Add the missing CYP2B6*4 allele to the bundled CPIC tables".
  Conventional prefixes (fix/feat/docs/test/refactor/chore, optionally scoped like fix(pgx):) are welcome but optional.
  Keep this PR to ONE cohesive change — if it does more than one thing, split it: separate concerns can't be
  reviewed or reverted independently.
-->

## Related issue
<!-- Link the issue this PR addresses, e.g. "Closes #123". One PR should address one issue. -->

## Summary
<!-- What changed, how (and any side effects), and — most importantly — WHY. The diff shows what/how; only you know the why. -->

## Definition of Done
<!-- Check what applies. These mirror the Tier-1 (per-PR) CI gate; the aggregate check is `ci-required` + `lint`. -->
- [ ] One cohesive change, reasonably sized (split if not)
- [ ] `lint` is green (Ruff check + Ruff format + Vulture + ESLint + Knip)
- [ ] `test-backend` (py3.12 + py3.13) and `test-frontend` (Vitest) pass
- [ ] New/changed behavior is covered by tests that fail without the change
- [ ] For carriage-gated analysis modules: a `hom_ref` non-carrier negative control is included
- [ ] Docs updated where behavior changed, and `docs-build` (`mkdocs build --strict`) passes
- [ ] Science/data changes carry a citation (`PMID:` / `DOI:` / `ChEMBL:` / `NCT:` + `(accessed YYYY-MM-DD)`); high-stakes claims have ≥2 agreeing sources
- [ ] UI changes verified in a real browser — **E2E (Playwright) and macOS legs are Tier-2 and do NOT run on this PR**, so a UI change can pass PR checks and still redden `main` at merge
- [ ] I performed a self-review of my own diff

## Reviewer notes
<!-- Optional: point reviewers at a design decision you're unsure about, an area you want scrutiny on, or context they need. -->

## Legal
- [ ] I certify that this contribution is mine to submit and may be included under the project's MIT license (Developer Certificate of Origin).

<!--
  Do not merge until required CI checks are green and a reviewer other than the author has approved.
  Merges are squash-merges; the squashed subject should stay imperative and end with "(#<PR number>)".
-->
