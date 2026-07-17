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
4. **Pass the gates** — a change merges only after it passes the review gate
   (below) **and all required CI checks are green**. CI's per-PR (Tier-1) gate is
   the `ci-required` aggregate plus `lint` (Ruff, Vulture, ESLint, Knip),
   `test-backend` (py3.12 + py3.13), `test-frontend`, `build-frontend`,
   `smoke-install`, and `docs-build --strict`. Note that the end-to-end
   (Playwright) and macOS legs are **Tier-2** — they run on merge to `main` and
   nightly, **not** on your PR — so verify UI changes in a real browser before
   merging.
5. **Merge** — pull requests are **squash-merged**; the squashed subject stays
   imperative and ends with `(#<PR number>)`.

Issues are organised by a labelled taxonomy — see
[Labels & triage](https://bioedca.github.io/Yeliztli/develop/labels-and-triage/).

## The review gate

Every change is reviewed before it merges — by someone other than the author.
Automated review runs first and is not a substitute for human judgement:

1. **CodeRabbit** — the primary automated reviewer on each PR.
2. **GitHub Copilot code review** — an additional automated reviewer (where
   enabled), which follows the repository's `.github/copilot-instructions.md`.
3. A maintainer reviews and approves. AI tools *draft* review and suggestions; a
   **human owns the decision to merge** and is accountable for what lands.

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
