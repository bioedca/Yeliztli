# Greptile reviews

Greptile is an AI code-review GitHub App installed on this repository. Its allowance is
small enough that an unguarded configuration would exhaust it in about a day, so this
repository asks Greptile to run in **manual-only** mode: to review a pull request when a
maintainer asks it to, and never on its own.

"Asks" is exact. Org-enforced dashboard rules and a `.greptile/` folder both take
precedence over the root `greptile.json` described here, so either can override this
configuration — see [Caveats](#caveats).

## The budget

This repository is allocated **16 Greptile reviews per month**.

Greptile meters *completed reviews*, not pull requests: "Billing counts **completed
reviews**, not PRs. Each finished review consumes one credit, charged to the PR author.
Skipped reviews don't count."[^billing] Three events each consume one review — two automatic,
one manual:

1. *automatic* — opening a pull request,
2. *automatic* — pushing a commit while `triggerOnUpdates` is enabled,
3. *manual* — mentioning the Greptile bot on a pull request.

The configuration below disables both automatic paths and leaves the manual mention
available, so a review is only ever billed because someone deliberately asked for one.

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

**Layer 1 — the org dashboard.** Not visible from this repository, and the layer a pull
request cannot edit. It carries two filters, described in the dashboard as "Control which
pull requests Greptile reviews — PRs that don't pass these filters are skipped":

- **Labels ∈ Include → `greptile-review`.** Nothing is reviewed automatically unless it
  carries that label, and no pull request does unless someone adds it. This is the primary
  block.
- **Authors ∉ Exclude → `dependabot[bot]`, `renovate[bot]`, `pre-commit-ci[bot]`,
  `github-actions[bot]`, `allcontributors[bot]`.** Defence in depth for machine authors.

Plus `fileChangeLimit: 1`, which skips "PRs with more than this many changed files"[^config]
— observed refusing PR #2252 with `Too many files changed for review. (6 files found, 1 file
limit)`.

The label filter was verified empirically: PR #2260 carried exactly **one** file — the case
`fileChangeLimit` cannot block, since it skips only PRs with *more* than one — and no label.
Greptile produced no comment, no review, and no `Greptile Review` check run. Zero credits.

**Layer 2 — `greptile.json` sets `skipReview` to `"AUTOMATIC"`.** The repository's own
statement of intent, versioned and reviewable, and the fallback if a dashboard filter is
ever relaxed: "skip auto-reviews but allow manual triggers".[^skip] It is deliberately
*narrow* — see "Deliberately not set" below, which is as load-bearing as what is set.

The rest of `greptile.json` pins defaults so they cannot be switched on silently:

| Key | Value | Why |
| --- | --- | --- |
| `skipReview` | `"AUTOMATIC"` | Skips automatic review while still allowing manual triggers. |
| `triggerOnUpdates` | `false` | No re-review per pushed commit. Each one would be a separate billed review. |
| `triggerOnDrafts` | `false` | No *automatic* review of draft pull requests; a manual mention on a draft still works. Marking a draft ready is itself an automatic trigger. |
| `shouldUpdateDescription` | `false` | Keeps the review as a bot-authored artifact instead of rewriting the pull request body. |

`shouldUpdateDescription` is load-bearing beyond cost: set to `true`, Greptile writes its
summary into the pull request description and leaves **no provider-authored artifact at
all** — nothing a review-route validator could verify.

### Deliberately not set

No **PR filter** appears in this file: not `labels`, `includeAuthors`, `includeBranches`,
`includeKeywords`, or `excludeAuthors`. Two reasons, and the second is the important one.

1. A filter here could refuse the *manual* `@greptile-apps` trigger. Greptile's
   documentation contradicts itself about whether a mention overrides PR filters, and losing
   manual review is worse than an occasional stray credit.
2. **Repository config overrides the dashboard per key, so setting a filter here silently
   narrows the dashboard's.** An `excludeAuthors` of `["dependabot[bot]"]` would shrink the
   five-bot dashboard exclusion above down to one, quietly re-admitting Renovate and the
   rest. Leaving the key unset is what keeps the dashboard authoritative — and the dashboard
   is the only layer a pull request branch cannot edit.

Bot-author exclusion therefore lives in the dashboard, not here. The test suite fails if any
of these keys reappears in the repository.

### The gap Layer 2 cannot close alone

A pull request that changes only `greptile.json` is not protected by `greptile.json`. Three
facts compose: Greptile reads its configuration from the pull request's **source branch**, so
a branch that deletes or weakens this file is already unguarded when the pull request opens;
that change is exactly **one file**, so `fileChangeLimit: 1` — which skips PRs with *more*
than one — permits it; and the review fires at open, before CI runs the guard test or the
route floor can reject the change. Every repository-side control is evaluated *after* the
review has already started, so the most dangerous change to this file is the one Layer 2
cannot protect.

**Layer 1's label filter is what closes it.** PR #2260 was exactly this shape — one file, no
label — and Greptile produced no comment, no review, and no check run.

So a configuration-only pull request is free **today**, and stays free only while that
dashboard filter exists. It would start costing a credit again the moment `Labels ∈ Include`
is removed, and nothing in this repository would signal that: the dashboard is invisible from
here, and no repository-side control substitutes for it.

One route that sounds like a fix is not one. Greptile's "org enforced rules" never document
*which* settings are enforceable — the stated purpose is "security policies or compliance
requirements"[^config-precedence] — and `skipReview` is not among them. Do not assume it.

That a dashboard key survives a repository-side edit is directly evidenced: PR #2252 carried
a `greptile.json` that does **not** set `fileChangeLimit`, and the dashboard's value still
refused the review. That per-key behaviour is exactly what makes the label filter
authoritative — provided this repository never sets `labels` itself.

### Caveats

- `greptile.json` is read **from the source branch of the pull request**, not from
  `main`[^config]. A branch cut before this file landed is not covered; merge `main` into a
  long-lived branch to pick it up.
- Config resolves as *org enforced rules > `.greptile/` folder > `greptile.json` > org
  default rules*. Dashboard state can pre-empt this file, and cannot be seen from here.
- **Do not add a `.greptile/` folder without a `config.json`.** It is the folder's presence,
  not the config file's, that makes `greptile.json` ignored — so a folder holding only
  `rules.md` silently drops every trigger setting on this page while the root file sits
  there looking authoritative. If you add the folder, move the whole policy into
  `.greptile/config.json`. The test suite fails on a rules-only folder.
- **Organization Settings → Billing → Flex Usage Limit set to `$0`** is the only vendor-side
  spend control Greptile documents, but it is not a review kill switch: it stops *flex*
  (overage) spend beyond the included credits. Authors still inside their included
  allowance keep getting reviews.[^billing] It caps the bill, not the review count.

## The route lane

Greptile is a selectable hosted reviewer on schema v3, on any route. It is nobody's default:
with 16 reviews a month against this repository's volume, making it a preferred tier would
guarantee exhaustion. Check its box only when you mean to spend a credit.

The accepted evidence is the **check run**, and only the check run:

| Property | Where it comes from | Depends on model prose? |
| --- | --- | --- |
| `app.databaseId == 867647` | GitHub App identity | no |
| head commit | bound by GitHub, not self-reported | no |
| `status == "COMPLETED"`, `conclusion == "SUCCESS"` | GitHub check lifecycle | no |
| `<N> files reviewed, <M> comments added.` | Greptile's own counter, fixed template | template only |

The clean test is `M == 0`. There is no sentence to match and no flourish to overflow, which
is what makes this the only envelope here that is not keyed on a model's choice of words —
the failure mode behind #2248, #2255 and #2256.

The other two artifacts are unusable as evidence, and the validator rejects both:

- The **formal review** is submitted only when Greptile has findings. Of 429 Greptile reviews
  sampled across four repositories, 427 carried at least one inline comment and an empty body.
  The only two carrying **zero** comments carried its billing notice: *"Your free trial has
  ended. If you'd like to continue receiving code reviews, you can add a payment method."*
  Reading zero attached comments as a clean verdict — the shape every other provider's
  envelope uses — would accept a refusal to review as a passing gate.
- The **summary comment** is edited in place on re-review, so it fails the immutability rule
  the other providers are held to.

Two counts are deliberately *not* compared. Greptile's reviewed-file count differed from
GitHub's changed-file count in 522 of 2196 live check runs, because Greptile applies its own
path filters. It is also asserted in the same generated string as the verdict, so comparing
the two buys no trust (#2248) while rejecting roughly one clean review in four.

`conclusion` is required on top of the counter but is never the verdict on its own: a review
that found three problems on this repository's PR #2203 still concluded `SUCCESS`. Conversely
a confidence threshold can turn the check `NEUTRAL` or `FAILURE` with a summary like
`Greptile confidence 3/5 is below the required 4/5.` — 141 of 2341 sampled runs did exactly
that. Neither shape passes.

## What the validator enforces, and what it cannot

**Enforced: at most two Greptile reviews per pull request.** Re-triggering on every push is the
cheapest way to spend a month's allowance, so the route counts the billed `Greptile Review` check
runs across the pull request's own commits and refuses the lane above two — one review, plus one
re-review after fixing what it found. The error reads
`Greptile per-pull-request review budget exceeded: 3 > 2`, and switching to another hosted
reviewer clears it.

The count reads check runs because they are the only artifact that is one-to-one with a billed
credit. Three measurements say why nothing cheaper works, and are recorded here so the cheap
shortcuts are not retried:

- **Formal reviews** are submitted only when Greptile has findings, so clean reviews — the
  ones this lane exists for — are invisible. Counting them undercounts, which fails *open*.
- **Comments are wrong in both directions.** Greptile no longer posts anything when a review is
  clean: PRs #2203 and #2254 each spent a credit and carry no trigger comment at all. Meanwhile
  unbilled skip notices (`Too many files changed for review`) do post — on #2250, #2252 and
  #2258, none of which spent anything. A comment count both misses real credits and invents
  phantom ones.
- **Trigger events** cannot be proven complete: `timelineItems.totalCount` ignores the
  `itemTypes` filter, so a label page can never show that it saw every label. Greptile's own
  skip notice also contains the literal string `@greptile-apps`, so a mention scan counts its
  advice as a trigger.

**The cap is a floor on one pull request, not a bound on spend.** Three limits follow from
counting a *pull request's* commits, and none is closable from GitHub's data:

- A check run belongs to a **commit, not a pull request**. Commits are shared — a stacked child
  inherits its parent's — and nothing records which pull request triggered a run. Runs that
  predate this pull request are skipped as the parent's, but a parent reviewed *after* the child
  was opened is charged to the child. That direction refuses a lane that has spent nothing;
  select another hosted reviewer.
- A commit can **leave** `pullRequest.commits` with no force push to detect: when a stacked
  parent merges, the merge-base moves and its commits drop out of the child's range, taking
  their billed runs with them.
- Closing a pull request and pushing the replacement on a different commit — the documented way
  to supersede your own — starts the count at zero.

The first fails closed; the last two fail open. The repository-wide bound below narrows them:
a run that leaves one pull request's range is still counted against the month, because the
sweep reads every pull request rather than one.

Two details of the check-run query are load-bearing and easy to get wrong:

- `checkRuns` defaults to `checkType: LATEST`, which drops a run superseded on an unchanged
  commit — and `totalCount` is computed *after* that filter, so it agrees with the shortened
  page and the truncation guard proves nothing. Measured on bruin-data/bruin commit `37f776c5`,
  which carries two billed runs: `LATEST` reports `totalCount` 1, `ALL` reports 2. Ask for
  `filterBy: {checkType: ALL}`.
- `PullRequest.commits` is ordered oldest-first, so `commits(first: 100)` omits the head commit
  on a long branch — the one commit that must carry the accepted run. Use `last:`.

**The 16 a month is enforced as a lower bound** (#2266). Every finalization that selects
Greptile sweeps the repository and refuses the lane once the *provable* spend over a rolling 30
days exceeds 16:

```text
Greptile rolling-30-day review count (lower bound) exceeds the 16 allowance: 17 > 16
```

The word "lower bound" is doing real work. Three truncations make an exact count impossible,
and none of them is an implementation gap:

- Credits are billed **per author across every repository they open pull requests in**. The 16
  is our own split of a shared pool, not a limit Greptile enforces here, and no per-repository
  count can see the other repositories drawing on it.
- Labelling a pull request does not change its `updatedAt`, and neither does a check run
  completing, so a ledger ordered by `updatedAt` structurally misses a *dormant* pull request
  reviewed today.
- Force-pushed commits vanish from `pullRequest.commits` and take their billed runs with them.

So the count is only ever "at least this many", and a bound that is honest about being a bound
still does the job: once even the provable subset passes 16, the month is spent.

What the sweep *can* prove is its coverage: *(every open pull request)* ∪ *(every pull request
updated inside the window)*. A pull request reviewed in-window and still open is in the first
set; one closed after its review is in the second, because closing **does** bump `updatedAt`.
`repository.pullRequests(orderBy: {field: UPDATED_AT, direction: DESC})` was verified strictly
monotonic, so reaching a pull request older than the window start proves the second set is
complete. Falling short of either half fails closed rather than reporting a smaller number.

Two properties keep it affordable and reproducible:

- The sweep is fetched **once per job** and the same file is handed to all three validator
  invocations. `after-success` runs *after* `Review Route: success` is published, so a
  rate-limited sweep there would demote a legitimately green route to `pending`. `checkSuites`
  is trimmed to 1 (~40 points a page against the fleet's shared 5,000/hour, versus ~120);
  `commits(last: 100)` is never trimmed, because a `first: 5` trim measurably lost PR #2203's
  run at commit 21 of 44.
- The window is anchored on the **accepted run's `completedAt`**, never `now()`, so re-running
  validation on an unchanged head cannot reach a different verdict.

One limit remains, and it is structural: the check runs *after* a review has been spent. It can
refuse to honour the 17th review, never prevent it — which is why the manual-only trigger, not
a count, is still what protects the budget day to day.

## Requesting a review

Only trigger Greptile on a pull request whose `## Review route` section **selects Greptile**.
Any other review cannot be used as route evidence, so the credit buys nothing, and the
contract forbids triggering a lane the route did not select.

To spend one of the 16, comment on the pull request:

```text
@greptile-apps
```

This also bypasses the `fileChangeLimit` — Greptile's own refusal message says "Bypass the
limit by tagging `@greptile-apps` to review." Greptile's documentation instead gives the
mention as `@greptileai`[^trigger]; if one produces no response, try the other. Each
successful trigger costs a review, so do not spray both.

Before spending one, check that it is worth a unit of a 16/month budget:

- Do not re-trigger to "refresh" after a push unless the new evidence is actually needed.
  Every head-changing push invalidates the review, so a trigger before the head is frozen
  buys a credit's worth of evidence that the next commit throws away.
- Prefer a single trigger on the final, frozen head over one per iteration.
- The route stops you at **three on one pull request**, not at 16 overall. Nothing counts the
  month for you: check the allowance before spending by searching the repository's pull
  requests for the `Greptile Review` check run, or read the balance in the Greptile dashboard,
  which is the only place that knows the true per-author figure.

## What Greptile posts

A completed review produces, on the reviewed commit:

- a check run named `Greptile Review` (app id `867647`) whose `output.summary` reads
  `Greptile has reviewed the Pull Request.` followed by `<N> files reviewed, <M> comments added.`
  — this is the **only** artifact the route accepts,
- a top-level issue comment carrying the human-readable `Greptile Summary`,
- a formal pull-request review with state `COMMENTED` — but **only when it has findings**,
  in which case the body is empty and the inline comments carry the content. A clean review
  submits no formal review at all, so a zero-comment Greptile review is not a clean verdict;
  in the sample behind this page, every such review was the "free trial has ended" notice.

`conclusion: success` on the check run means the review *completed*, not that it was clean —
a review that found three problems still succeeds. Cleanliness is `0 comments added`.

Greptile **edits its summary comment in place** on each re-review rather than posting a new
one, so that comment's creation time does not tell you when the latest review ran.

[^billing]: Greptile, "Billing and seats". <https://www.greptile.com/docs/code-review-bot/billing-seats> (accessed 2026-08-03).
[^config]: Greptile, "greptile.json reference". <https://www.greptile.com/docs/code-review/greptile-json-reference> (accessed 2026-08-03).
[^skip]: Greptile, "greptile.json reference", Review Behavior: `skipReview` — "Set to `\"AUTOMATIC\"` to skip auto-reviews but allow manual triggers". (accessed 2026-08-03).
[^trigger]: Greptile, "Trigger a code review". <https://www.greptile.com/docs/code-review-bot/trigger-code-review> (accessed 2026-08-03).
[^config-precedence]: Greptile, ".greptile/ configuration" — precedence and org-level rules: "Org-level enforced rules (set by admins in the dashboard) always apply. They cannot be disabled or overridden by any `.greptile/` configuration. This lets organizations enforce security policies or compliance requirements across all repositories regardless of per-repo config." <https://www.greptile.com/docs/code-review/greptile-config> (accessed 2026-08-03).
