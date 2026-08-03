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
  science, security, dependency, or workflow impact.
- **Standard:** routine code, tests, UI, refactors, or bug fixes outside a
  load-bearing area.
- **Load-bearing:** science or clinical logic/data and their tests; privacy,
  security, or auth; schema, migration, or data-loss paths; concurrency;
  dependencies; updater, installer, release, CI, workflows, permissions, core
  architecture, or broad/hard-to-revert changes.

The route decides how carefully the change is reviewed; it does not decide who
reviews it. The three hosted providers are interchangeable on every route.
Codex `@codex review` is the default lane. CodeRabbit no longer reviews
automatically, so on a v3 pull request it starts only when the
`review:coderabbit` label is applied or someone asks for a review in a comment
— pick it deliberately, because its included reviews and the local `coderabbit`
CLI draw on one shared budget that adaptive fair-usage limits throttle. Never
trigger a provider you did not select.

A **v2** pull request that selects CodeRabbit keeps its legacy protocol
unchanged: a `coderabbit-reservation: <40-character head SHA>` comment for the
current head, followed by the exact comment `@coderabbitai full review`. The
label gate does not replace that pair — a label alone, or a plain
`@coderabbitai review`, is not recorded as a v2 protocol event and leaves the
route pending.

New pull requests use `review-route-schema:v3`. Existing v2 pull requests keep
their human-gated contract until their body is explicitly migrated; v1 remains
obsolete. Both versions select exactly one hosted provider and record its exact
40-character head plus UTC completion time. Unused provider rows are `N/A`.
Route controls must render as normal Markdown, not fenced, indented, or hidden
in raw HTML.

V3 trusts only a small provider-authenticated terminal envelope:

- **Codex:** an exact-head empty formal approval with zero attached comments,
  or its canonical immutable clean comment and reviewed-commit marker.
- **Copilot:** its unedited review closing with the coverage sentence
  `Copilot reviewed N out of M changed files in this pull request and generated
  no comments.`, where both counts equal GitHub's changed-file count and the
  review has no attached comments. Copilot skips files it judges low risk, so a
  partial count is normal output but is not accepted evidence. Copilot can also
  withhold a low-confidence finding rather than post it; nothing in the envelope
  detects that, so a clean Copilot review does not assert its absence (#2256).
- **CodeRabbit:** its unedited structured clean review, with zero actionable or
  attached comments, no ignored files, and its selected-file count equal to
  GitHub's changed-file count.

The provider identity, exact head, unedited evidence, resolved threads, absence
of an active change request, and unique open-PR ownership are all fail-closed.
When an output format is unsupported, select another provider instead of
guessing or adding a permissive parser. A v3 PR cannot modify the
PR-controlled review-signal workflow; that change must use v2.

After the selected provider is recorded and every thread is resolved, a
write-capable finalizer posts the exact unedited `/validate-route`. Trusted
default-branch code takes a complete live API snapshot, rechecks mutable route
facts immediately before the dedicated publisher App posts success, then audits
the result once more. If the audit changed, the App replaces success with
`pending` and the run fails. Lifecycle, body, review, and comment events only
invalidate affected heads to `pending`; they do not authorize a route.
Privileged jobs never check out PR code or consume PR artifacts or caches.

V2 retains its existing requirements: selected hosted review, independent
human approval after that review, resolved threads, exact finalizer, DCO, and
explicit maintainer merge authorization. Its CodeRabbit reservation and
rolling-hour trigger protocol also remain unchanged. Human approval cannot
substitute for the selected provider in v3, and an active human change request
blocks both versions.

V3 does not require DCO, independent human approval, or per-PR human merge
authorization. It may be squash-merged when the App-authored `Review Route`
status and all required CI/security checks are green, the branch is current,
and native required conversation resolution is satisfied. The dedicated App,
never `github-actions[bot]`, is the only accepted source for `Review Route`.

Feedback etiquette: criticise the code, not the coder; prefix optional nits with
`Nit:`; explain the rationale for a suggestion; and let automated formatters
settle style so review time goes to substance.

## Contribution provenance

Legacy v2 pull requests retain the
[Developer Certificate of Origin](https://developercertificate.org/) checkbox.
New v3 pull requests instead record operational provenance: linked issue, exact
head, selected hosted reviewer, test evidence, and public agent claim ID. V3
does not request a legal or DCO certification.

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
