# Greptile reviews

Greptile is an AI code-review GitHub App installed on this repository. Unlike the other
hosted reviewers, its allowance is small enough that an unguarded configuration would
exhaust it in about a day, so this repository runs Greptile in **manual-only** mode.

## The budget

This repository can use **16 Greptile reviews per month**.

Greptile meters *completed reviews*, not pull requests: "Billing counts **completed
reviews**, not PRs. Each finished review consumes one credit, charged to the PR author.
Skipped reviews don't count."[^billing] Three events each consume one review:

1. opening a pull request,
2. commenting `@greptileai` on a pull request,
3. pushing a commit while `triggerOnUpdates` is enabled.

A review that finds nothing still costs the same as one that finds a bug, and a
re-review of the same pull request costs a fresh unit. There is no partial refund and no
API that reports the remaining balance — usage is visible only in Greptile's dashboard.

For scale: this repository saw 374 pull requests updated in a recent 30-day window. At
one automatic review per opened pull request, the monthly allowance is gone well inside
the first day, and every one of those reviews lands on a pull request that never asked
for it.

## The guard

`greptile.json` at the repository root disables every automatic trigger. Greptile reads
this file **from the source branch of the pull request**, not from `main`[^config] — so a
branch cut before this file landed is not protected by it. Merge `main` into a long-lived
branch to pick the guard up.

| Key | Value | Why |
| --- | --- | --- |
| `skipReview` | `"AUTOMATIC"` | Skips automatic reviews while still allowing manual triggers.[^skip] |
| `labels` | `["greptile-review"]` | Restricts review to pull requests carrying that label — a second, independent guard. |
| `triggerOnUpdates` | `false` | No re-review per pushed commit. Each one would be a separate credit. |
| `triggerOnDrafts` | `false` | No review of draft pull requests. |
| `shouldUpdateDescription` | `false` | Keeps the review as a bot-authored artifact instead of rewriting the pull request body. |
| `excludeAuthors` | `["dependabot[bot]"]` | Dependency bumps never spend the budget. |

Two guards are deliberate rather than redundant. Greptile's own reference pages describe
`skipReview` differently in two places — "skip auto-reviews but allow manual triggers" in
the `greptile.json` reference[^skip] versus "skip review entirely for files in this
directory" in the `.greptile/` reference — and at least one public repository uses an
undocumented plural `skipReviews` spelling. The `labels` filter does not depend on that
key being parsed the way the reference describes.

`shouldUpdateDescription` is load-bearing beyond cost control: set to `true`, Greptile
writes its summary into the pull request description instead of posting a review, leaving
no provider-authored artifact for a route validator to verify.

## Requesting a review

Greptile will not review anything unless you ask. To spend one of the 16:

1. Add the `greptile-review` label to the pull request.
2. Comment `@greptileai` on the pull request.

Step 1 satisfies the `labels` filter; step 2 is Greptile's documented manual trigger.[^trigger]
Do both — Greptile's documentation is contradictory about whether a label-filtered pull
request can still be triggered by mention alone, so relying on either one is a coin flip.

Before spending a review, check that it is worth a unit of a 16/month budget:

- Do not trigger Greptile on a pull request whose `## Review route` section selects a
  different hosted reviewer. That review cannot be used as route evidence, so the credit
  buys nothing.
- Do not re-trigger to "refresh" a review after a push unless the new evidence is actually
  needed — the previous review is stale, but the replacement costs another unit.
- Prefer a single trigger on the final, frozen head rather than one per iteration.

## What Greptile posts

A completed review produces, on the reviewed commit:

- a check run named `Greptile Review` (app id `867647`) whose `output.summary` reads
  `Greptile has reviewed the Pull Request.` followed by `<N> files reviewed, <M> comments added.`
- a top-level issue comment carrying the human-readable `Greptile Summary`,
- a formal pull-request review with an **empty body** and state `COMMENTED`, which exists
  only to carry inline comments.

`conclusion: success` on the check run means the review *completed*, not that it was
clean — a review that found three problems still succeeds. Cleanliness is `0 comments added`.

Greptile **edits its summary comment in place** on each re-review rather than posting a new
one, so the comment's creation time does not tell you when the latest review ran.

[^billing]: Greptile, "Billing and seats". <https://www.greptile.com/docs/code-review-bot/billing-seats> (accessed 2026-08-03).
[^config]: Greptile, "greptile.json reference". <https://www.greptile.com/docs/code-review/greptile-json-reference> (accessed 2026-08-03).
[^skip]: Greptile, "greptile.json reference", Review Behavior: `skipReview` — "Set to `\"AUTOMATIC\"` to skip auto-reviews but allow manual triggers". (accessed 2026-08-03).
[^trigger]: Greptile, "Trigger a code review". <https://www.greptile.com/docs/code-review-bot/trigger-code-review> (accessed 2026-08-03).
