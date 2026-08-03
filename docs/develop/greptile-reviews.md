# Greptile reviews

Greptile is an AI code-review GitHub App installed on this repository. Its allowance is
small enough that an unguarded configuration would exhaust it in about a day, so Greptile
runs here in **manual-only** mode: it reviews a pull request when a maintainer asks it to,
and never on its own.

## The budget

This repository is allocated **16 Greptile reviews per month**.

Greptile meters *completed reviews*, not pull requests: "Billing counts **completed
reviews**, not PRs. Each finished review consumes one credit, charged to the PR author.
Skipped reviews don't count."[^billing] Three events each consume one review:

1. opening a pull request,
2. mentioning the Greptile bot on a pull request,
3. pushing a commit while `triggerOnUpdates` is enabled.

A review that finds nothing costs the same as one that finds a bug, and a re-review of the
same pull request costs a fresh unit. There is no partial refund and no API that reports
the remaining balance — usage is visible only in Greptile's dashboard.

Two things make the 16 tighter than it looks. Credits are billed **per author**, not per
repository — "Overages are per-author, not pooled across the team"[^billing] — so every
repository this account opens pull requests in draws on the same pool; 16 is this
repository's share of it, not a limit Greptile enforces here. And for scale, this
repository saw 374 pull requests updated in a recent 30-day window: at one automatic review
per opened pull request, the whole month is gone well inside the first day.

## Two layers block automatic review

**Layer 1 — the org dashboard sets `fileChangeLimit` to 1.** Greptile skips "PRs with more
than this many changed files"[^config], so every pull request touching two or more files is
refused automatically. This was observed working on PR #2252:

> Too many files changed for review. (`6 files found`, `1 file limit`)

This layer is deliberate and is the primary block. It is not visible from this repository —
it lives in Greptile's dashboard.

**Layer 2 — `greptile.json` sets `skipReview` to `"AUTOMATIC"`.** Layer 1 has a hole it
cannot close: a pull request changing **exactly one file** is not "more than 1", so it is
still auto-reviewed, and `fileChangeLimit` has a documented minimum of 1, so it cannot be
set lower. Single-file pull requests are common here — a docs correction, a one-line fix.
`skipReview: "AUTOMATIC"` — "skip auto-reviews but allow manual triggers"[^skip] — is what
closes that hole.

The rest of `greptile.json` pins defaults so they cannot be switched on silently:

| Key | Value | Why |
| --- | --- | --- |
| `skipReview` | `"AUTOMATIC"` | Skips automatic review while still allowing manual triggers. |
| `triggerOnUpdates` | `false` | No re-review per pushed commit. Each one would be a separate billed review. |
| `triggerOnDrafts` | `false` | No review of draft pull requests. Marking a draft ready is itself a trigger. |
| `shouldUpdateDescription` | `false` | Keeps the review as a bot-authored artifact instead of rewriting the pull request body. |
| `excludeAuthors` | `["dependabot[bot]"]` | Dependency bumps never spend the budget. |

`shouldUpdateDescription` is load-bearing beyond cost: set to `true`, Greptile writes its
summary into the pull request description and leaves **no provider-authored artifact at
all** — nothing a review-route validator could verify.

Deliberately **not** set: `labels`, `includeAuthors`, `includeBranches`, `includeKeywords`.
An allow-list filter would add no third guarantee against automatic review, but Greptile's
documentation contradicts itself about whether a mention overrides such filters — so it
could also refuse the *manual* trigger. Losing manual review is worse than an occasional
stray credit.

### Caveats

- `greptile.json` is read **from the source branch of the pull request**, not from
  `main`[^config]. A branch cut before this file landed is not covered; merge `main` into a
  long-lived branch to pick it up.
- Config resolves as *org enforced rules > `.greptile/` folder > `greptile.json` > org
  default rules*. Dashboard state can pre-empt this file, and cannot be seen from here.
- The only hard stop Greptile documents is **Organization Settings → Billing → Flex Usage
  Limit set to `$0`**, which stops spend at the vendor rather than at the repository.

## Requesting a review — not yet

**Do not trigger Greptile today.** Greptile is not a selectable hosted reviewer: the v3 pull
request template and `scripts/validate_review_route.py` accept only Copilot, Codex, and
CodeRabbit. Every valid pull request therefore selects one of those three, and the contract
forbids triggering a lane the route did not select.

So a Greptile review requested now would spend one of the 16 on evidence the route validator
cannot accept, on a pull request that asked for a different reviewer. Greptile stays silent
until the lane lands — that is the point of the two layers above.

Adding the lane is tracked in issue #2251. The rest of this section describes how triggering
will work **once that merges**; it is not a licence to trigger before then.

To spend one of the 16, comment on the pull request:

```
@greptile-apps
```

This also bypasses the `fileChangeLimit` — Greptile's own refusal message says "Bypass the
limit by tagging `@greptile-apps` to review." Greptile's documentation instead gives the
mention as `@greptileai`[^trigger]; if one produces no response, try the other. Each
successful trigger costs a review, so do not spray both.

Before spending one, check that it is worth a unit of a 16/month budget:

- Only trigger Greptile on a pull request whose `## Review route` section **selects
  Greptile**. Any other review cannot be used as route evidence, so the credit buys nothing.
- Do not re-trigger to "refresh" after a push unless the new evidence is actually needed.
- Prefer a single trigger on the final, frozen head over one per iteration.

## What Greptile posts

A completed review produces, on the reviewed commit:

- a check run named `Greptile Review` (app id `867647`) whose `output.summary` reads
  `Greptile has reviewed the Pull Request.` followed by `<N> files reviewed, <M> comments added.`,
- a top-level issue comment carrying the human-readable `Greptile Summary`,
- a formal pull-request review with an **empty body** and state `COMMENTED`, which exists
  only to carry inline comments.

`conclusion: success` on the check run means the review *completed*, not that it was clean —
a review that found three problems still succeeds. Cleanliness is `0 comments added`.

Greptile **edits its summary comment in place** on each re-review rather than posting a new
one, so that comment's creation time does not tell you when the latest review ran.

[^billing]: Greptile, "Billing and seats". <https://www.greptile.com/docs/code-review-bot/billing-seats> (accessed 2026-08-03).
[^config]: Greptile, "greptile.json reference". <https://www.greptile.com/docs/code-review/greptile-json-reference> (accessed 2026-08-03).
[^skip]: Greptile, "greptile.json reference", Review Behavior: `skipReview` — "Set to `\"AUTOMATIC\"` to skip auto-reviews but allow manual triggers". (accessed 2026-08-03).
[^trigger]: Greptile, "Trigger a code review". <https://www.greptile.com/docs/code-review-bot/trigger-code-review> (accessed 2026-08-03).
