# CodeRabbit review evidence

**The configured CodeRabbit clean-run path is unavailable for schema-v3
finalization as of the 2026-11-08 audit.** A fully clean run can finish without
the immutable formal review required by this repository. Choose another
available hosted provider; do not spend a review to probe this known mismatch.
This is the fail-closed disposition permitted by
[issue #2490](https://github.com/bioedca/Yeliztli/issues/2490).

The existing formal-envelope contract remains unchanged: an authenticated,
unedited, exact-head `COMMENTED` review with zero actionable and attached
comments, no ignored files, and a selected-file count equal to GitHub's changed
files. An artifact that actually satisfies it still passes. The synthetic
`_coderabbit_v3_body` test helper models that contract; it does **not** establish
that a native clean run emits the modeled body.

## What the public artifacts establish

The audit retrieved existing public GitHub objects without invoking a review or
changing provider settings, permissions, billing or entitlement. All sources
below were accessed 2026-11-08.

On [PR #2489](https://github.com/bioedca/Yeliztli/pull/2489), the provider issued
the same successful `Review completed` commit status for these different
outcomes:

| Reviewed commit | Completion status ID | Formal review |
| --- | --- | --- |
| `1f44a5e87007da7dc36c451182ebce60acc0951a` | `56613080096` | [5560840290](https://github.com/bioedca/Yeliztli/pull/2489#pullrequestreview-5560840290): two actionable comments |
| `27cf5b96b7ea8270094c27e99397ba464a335189` | `56613186926` | [5560851560](https://github.com/bioedca/Yeliztli/pull/2489#pullrequestreview-5560851560): one actionable comment |
| `a0ba892b03437d7620f0ccdbfcc109333f1deb4d` | `56613541488` | None |

The status author is CodeRabbit bot database ID `136622811`. The clean head's
status completed at `2026-10-18T01:08:02Z`, but supplies neither a finding count
nor complete-file coverage. Complete check-run pages contain no CodeRabbit
check on those three historical commits.

The [walkthrough comment 6243604551](https://github.com/bioedca/Yeliztli/pull/2489#issuecomment-6243604551)
names the clean head and reports no actionable comments, with two files
selected. It was created at `2026-10-18T00:14:20Z` and updated at
`2026-10-18T23:58:54Z`: its contents are mutable. The separate
[completion comment 6244042633](https://github.com/bioedca/Yeliztli/pull/2489#issuecomment-6244042633)
was also edited and does not attest zero findings and coverage. Copying either
comment into the PR body cannot turn it into immutable provider evidence.

A newer control is [check run 128837371400 on PR #2650](https://github.com/bioedca/Yeliztli/runs/128837371400).
App ID `347564` published it on commit
`9a18bd9ea72e5be49c07de2eef8e68f2cb687f7c`. It completed successfully at
`2026-11-08T19:37:33Z` with zero check annotations, while explicitly reporting
incomplete coverage. Check annotations are not a count of PR review findings.

To reproduce these observations, retrieve every page of the PR's `reviews`,
issue `comments`, and `commits` REST endpoints, then each exact commit's
`statuses` and `check-runs?filter=all` endpoints. Preserve provider IDs, head
SHAs, server times, edit metadata and complete-page counts. The audit concerns
these observed artifacts; it is not a claim that all possible CodeRabbit
configurations or future versions behave identically.

## Why other signals do not replace the envelope

CodeRabbit's [status-check documentation](https://docs.coderabbit.ai/pr-reviews/status-check)
(accessed 2026-11-08) describes a completion/progress signal. Passing can include
skips or rate limits; coverage can exclude files or cover only an increment.
Depending on configuration, an error need not fail the status. Thus a successful
check or commit status alone cannot prove a clean, complete review.

[Automatic Approvals](https://docs.coderabbit.ai/pr-reviews/automatic-approvals)
(accessed 2026-11-08) also do not supply this contract. They can approve when all
changed files are excluded, and manual approval/resolution commands can bypass
a completed review of the latest commit and pre-merge checks. An `APPROVED`
review is therefore not substituted for the required CodeRabbit envelope.
No approval setting or command was changed or invoked during this audit.

## Recovery and future support

When selected CodeRabbit evidence fails the existing v3 acceptance rules, the
validator reports `CodeRabbit schema-v3 evidence unavailable` and the artifact
reason. It does not interpret a mutable walkthrough or invent missing counters.
Finish the current attempt, or confirm it cancelled/idle, before selecting
another available provider. Update the canonical PR body, keep exactly one
provider selected, put `N/A` in both cells for unused providers, refetch the
exact head and body, then invoke only the selected lane.

New CodeRabbit support requires a real provider-authenticated artifact proving
the exact head, immutable completion, zero findings and required coverage,
with positive and adversarial controls. An unverified setting change or another
paid retry is not evidence. Re-audit before assuming the limitation persists.

Schema v2 retains its existing reservation/trigger protocol, review semantics,
DCO, independent human approval and explicit merge authorization. No provider
settings or acceptance rules changed here. Schema-v3 merge authority remains
the dedicated Review Route publisher App's exact-head success together with all
other required gates.
