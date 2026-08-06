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

## Automated contribution provenance
<!-- New v3 pull requests record operational provenance, not a legal attestation. -->
- Issue: <!-- e.g. Closes #123 -->
- Exact head SHA:
- Selected hosted reviewer:
- Test evidence:
- Agent claim ID:

## Review route
<!-- review-route-schema:v3 -->
<!-- Check EXACTLY ONE for the FINAL diff. Mixed/uncertain scope takes the higher route; reviews bind to one head SHA. Keep this section rendered as normal Markdown: do not indent it into or wrap it in a code block or raw HTML. -->
- [ ] Low — text/docs/mechanical only; no behavior, public contract, science, security, dependency, or workflow change
- [ ] Standard — routine code/tests/UI/refactor/bug fix not protecting a load-bearing area
- [ ] Load-bearing — science/clinical/reference data or their tests; privacy/security/auth; schema/migration/data loss; concurrency; dependencies; updater/installer/release; CI/workflows/permissions; core architecture; broad/hard-to-revert

<!-- Before ready/merge, check EXACTLY ONE hosted reviewer. They are substitutes on every route; select another provider instead of adding fallback parsers or waiting on quota. Codex is the default lane. A hosted CodeRabbit review starts only from the `review:coderabbit` label or an explicit comment trigger, so pick it deliberately and only once per head — it and the local `coderabbit` CLI share one budget. A legacy v2 PR selecting CodeRabbit still needs its unchanged reservation plus the exact `@coderabbitai full review`; the label does not replace that. Greptile never reviews on its own — start it by commenting `@greptile-apps`, which is its only trigger (labelling does nothing, because a label filters automatic review and automatic review is off), and only when you have checked its box. The route refuses a third billed Greptile review on one pull request, so spend it on the final frozen head rather than once per push; the 16-a-month allowance behind that cap is still yours to track. Never trigger a lane you did not check. -->
- [ ] Copilot — alternate lane; gated by account quota, not by format
- [ ] Codex — default lane on every route
- [ ] CodeRabbit — alternate lane; label-gated, for a deliberate hosted second engine
- [ ] Greptile — alternate lane; manual-trigger only, on a 16-review monthly allowance

<!-- Drafts need only the route classification. The selected row needs the exact 40-character head SHA and `YYYY-MM-DDTHH:MM:SSZ — COMPLETE`; enter exactly N/A in BOTH cells for unused providers. Evidence must be unedited, provider-authored, and exact-head. Codex accepts an empty zero-comment formal approval or its canonical immutable clean comment. Copilot accepts only a review carrying its zero-comment coverage verdict exactly once and zero attached comments. CodeRabbit accepts only its structured clean review with zero actionable/attached comments, no ignored files, and a selected-file count equal to GitHub's changed-file count. Greptile accepts only its `Greptile Review` check run on the head commit, completed, concluding success, reporting `0 comments added.` — never its formal review, whose zero-comment form is a billing notice rather than a clean verdict. Resolve every thread, then have a live write-capable finalizer post the exact unedited `/validate-route`. -->
| Required review gate | Applies to | Head SHA or N/A | UTC time and status, or N/A |
| --- | --- | --- | --- |
| Copilot PR review | alternate lane, any route | | |
| Codex `@codex review` | default lane, any route | | |
| CodeRabbit structured clean review | alternate lane, any route; label-gated | | |
| Greptile clean review check run | alternate lane, any route; manual-trigger only | | |

<!--
  Do not merge until required CI and the dedicated App's Review Route status are green.
  V3 does not require DCO, independent human approval, or per-PR human merge authorization.
  Merges are squash-merges; the squashed subject should stay imperative and end with "(#<PR number>)".
-->
