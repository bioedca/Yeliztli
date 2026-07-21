# Contributing

Thanks for your interest in improving Yeliztli. Contributions of all kinds are
welcome — code, documentation, triage, reproducing bugs, and scientific review.

By participating you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).
For help, see [SUPPORT.md](SUPPORT.md); to report a vulnerability, see
[SECURITY.md](SECURITY.md); for roles and decision-making, see
[GOVERNANCE.md](GOVERNANCE.md).

## Ways to contribute

- **Report a bug or request a feature** via the [issue templates](https://github.com/bioedca/Yeliztli/issues/new/choose).
  One issue per problem, with enough detail to act on it without a follow-up.
- **Ask a usage question** in [Discussions → Q&A](https://github.com/bioedca/Yeliztli/discussions/categories/q-a)
  (not the issue tracker).
- **Send a pull request** — see the workflow below. Good first tasks are labelled
  [`good first issue`](https://github.com/bioedca/Yeliztli/labels/good%20first%20issue).

## Contribution workflow

1. **Set up** — see the [development setup guide](https://bioedca.github.io/Yeliztli/develop/development-setup/)
   (Python 3.12+, Node 20+; `pip install -e ".[dev]"`, `make dev`).
2. **Branch** — fork the repo and create a topic branch; never commit to `main`
   directly. Keep each pull request to **one cohesive change** — if it does more
   than one thing, split it, so it can be reviewed and reverted independently.
3. **Open a PR** — the [pull-request template](.github/PULL_REQUEST_TEMPLATE.md)
   guides you: link the issue (`Closes #123`), say what/how/**why**, and work the
   Definition-of-Done checklist. Give the PR a specific, imperative title.
4. **Pass the gates** — select the final-diff review route in the PR template. A
   change merges only after `Review Route` and **all required CI checks are
   green**. CI's per-PR (Tier-1) gate is
   the `ci-required` aggregate plus `lint` (Ruff, Vulture, ESLint, Knip),
   `test-backend` (py3.12 + py3.13), `test-frontend`, `build-frontend`,
   `smoke-install`, and `docs-build --strict`. Note that the end-to-end
   (Playwright) and macOS legs are **Tier-2** — they run on merge to `main` and
   nightly, **not** on your PR — so verify UI changes in a real browser before
   merging.
5. **Merge** — pull requests are **squash-merged**; the squashed subject stays
   imperative and ends with `(#<PR number>)`. Merge queue is not supported.

Issues are organised by a labelled taxonomy — see
[Labels & triage](https://bioedca.github.io/Yeliztli/develop/labels-and-triage/).

## The review gate

Choose the highest route required by any part of the final diff:

- **Low:** text or mechanical changes with no behavior, public-contract,
  science, security, dependency, or workflow impact. Run Copilot, then obtain
  independent human approval.
- **Standard:** routine code, tests, UI, refactors, or bug fixes outside a
  load-bearing area. Run Copilot, Codex `@codex review`, then human approval.
- **Load-bearing:** science or clinical logic/data and their tests; privacy,
  security, or auth; schema, migration, or data-loss paths; concurrency;
  dependencies; updater, installer, release, CI, workflows, permissions, core
  architecture, or broad/hard-to-revert changes. Run Copilot, Codex, manual
  CodeRabbit, then human approval.

Reviews must be formal GitHub reviews on the final head SHA and occur in route
order. Record the exact SHA and UTC completion time in the PR table. For
CodeRabbit, after Codex completes, the same maintainer posts two separate,
immutable comments at distinct times:

1. `coderabbit-reservation: <full-head-SHA>`
2. `@coderabbitai full review`

The reservation is a cooperative intent marker, not an atomic vendor slot.
Maintainers serialize triggers; the validator rejects a visible rolling-hour
ledger above five, but deleted comments or simultaneous races require manual
coordination. Any later trigger needs a new reservation and completion.

The final human approver must not be the PR author, and an active maintainer
change request blocks the route. After final approval, record its evidence,
resolve every review thread, then have a maintainer comment `/validate-route`.
Diff-comment mutations from every actor, plus trusted formal-review mutations,
emit only a credential-free invalidation signal; a dedicated GitHub App marks
the route pending and runs the explicit refresh only from trusted default-branch
workflow code. An outsider's body-only formal review is irrelevant, but an
outsider diff comment always invalidates because it can create an unresolved
thread. GitHub Actions cannot subscribe directly to review-thread
resolved/unresolved events.
Native required conversation resolution is therefore the authoritative
synchronous gate for those transitions, and final validation rechecks every
thread. Native repository rules also remain authoritative for approval after
the most recent push. Commit statuses are SHA-scoped and do not expire: two open
`main` PRs must never share a head SHA.
Any failed, skipped, or cancelled trusted/relevant signal or publisher run is
merge-blocking even if an older green status remains. The repository ruleset
must require the branch to be up to date before merging, and its
conversation-resolution rule must remain enabled whenever `Review Route` is
required. Immediately before merging, confirm the newest relevant signal and
publisher runs are terminal, then issue or repeat `/validate-route` after all
review activity.
AI tools draft review and suggestions; a **human owns the decision to merge**.

Feedback etiquette: criticise the code, not the coder; prefix optional nits with
`Nit:`; explain the rationale for a suggestion; and let automated formatters
settle style so review time goes to substance.

## Developer Certificate of Origin

Contributions are accepted under the [Developer Certificate of Origin](https://developercertificate.org/)
rather than a CLA. By checking the DCO box in the pull-request template, you
certify that the contribution is yours to submit and may be included under the
project's [MIT license](LICENSE).

## Test assertion standards

Two anti-patterns defeat a test's purpose, because they pass for almost any
non-crashing code:

- **`assert x is not None` as the _only_ assertion** on a value-producing
  function. Most functions return a non-`None` object for any input, so this
  asserts "it ran," not "it produced the right answer." Assert the _value_: the
  field, the rendered string, the returned set, the computed number.
- **`assert response.status_code == 200` as the _only_ assertion** on an
  endpoint that returns data. A `200` with a wrong, empty, or duplicated body
  still passes. Assert the body too — the specific fields, counts, or rows you
  expect.

Both are fine as a _first_ line (a precondition) when followed by assertions on
the actual value. They are insufficient _alone_.

Concretely:

- For a value-producing function, assert the value — and where a SQL/text/JSON
  artifact is produced, assert its content (compile a query with `literal_binds`
  and assert the rendered SQL; assert VCF `REF`/`ALT`/`GT`; assert the exact
  diplotype, not `'3' in str(genotype)`).
- Prefer two-sided checks for filters: assert the excluded row is _absent_, not
  only that the returned rows match.
- Don't guard a loop with no membership check — `for item in items: assert ...`
  passes vacuously when `items` is empty. Assert `items` is non-empty first.
- Don't hand-overwrite the column under test in an "end-to-end" fixture; drive
  the production path so the test fails if that path regresses.
- Keep timing/perf assertions self-documenting: if a relaxed regression ceiling
  differs from the product target, inline the real target next to the assertion.

## hom_ref negative controls (carriage-gated modules)

A genotyping chip reports a call at _every_ probe regardless of whether the
person carries the variant, so a ClinVar-Pathogenic record at a
homozygous-reference position must **not** surface as a clinical finding. Every
analysis module that emits carriage-dependent findings should have at least one
test that seeds a non-carrier (`hom_ref`) Pathogenic variant and asserts it is
_absent_ from findings.

Shared builders live in `tests/backend/_carriage_fixtures.py`
(`hom_ref_pathogenic_row` / `het_pathogenic_row`). Risk-genotype (dosage-based)
modules use the equivalent "all-reference genotype → no finding" control rather
than a ClinVar-significance seed.

## Enforcement

These standards are **advisory**, surfaced as review comments by CodeRabbit via
`.coderabbit.yaml` (path-scoped to `tests/**`). They are intentionally _not_
CI-blocking: the existing suite predates the convention, so a blocking lint
would flag hundreds of legacy lines. The goal is to stop _new_ vacuous
assertions at review time, and to migrate legacy ones opportunistically.
