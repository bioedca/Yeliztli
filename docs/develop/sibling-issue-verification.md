# Verifying sibling issues on main

**Proposed decision for #2368, awaiting maintainer review.** Keep one primary
issue per PR. When a change appears to satisfy another open issue, record a
neutral reference to that candidate. Close it separately after its own
acceptance criteria pass on green `main`. Use the same evidence standard
when an agent picks up an issue whose fix may already have shipped.

Two earlier closures illustrate why both checks matter. The stale-sample
route change in #2236 addressed #2017. Issue #2049 stayed open for 33 more
days. Its closing investigation traced the non-Dashboard path, the stale
response and the recovery path. A finding raised during #2238 became #2290
and was corrected in that PR. The sibling stayed open for 24 more days.
Its closing investigation checked legacy withholding, setup readiness and
the current-install control. [Verification for #2049](https://github.com/bioedca/Yeliztli/issues/2049#issuecomment-5555320815)
and [verification for #2290](https://github.com/bioedca/Yeliztli/issues/2290#issuecomment-5555346814).

## How a candidate reaches closure

1. **Before claiming an issue**, read its acceptance criteria, comments,
   linked work, live claims and Project state. Search merged PRs by issue
   number and by the affected route, module, error text or symbol. Inspect
   the history of the relevant paths since the issue was filed, including
   renames and fixes made during review. A shared file supplies a lead.
2. **At draft creation and before merge**, search open issues using the
   final diff and review findings. List each plausible sibling with a brief
   reason. Keep independent fixes under their own issues and claims. Use a
   plain reference in the PR and squash message. A closing keyword or a
   manual link in the Development sidebar can close the sibling on merge,
   before post-merge checks finish. [GitHub's linking rules](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue)
3. **After the merge is green, or when investigating an older fix**, fetch
   current `origin/main` and record its full SHA and covering check IDs.
   Confirm the proposed fix is in its history. Map **each** acceptance
   criterion to its production path, expected result and observed result.
   A confirmed owner runs write-producing checks in a dedicated worktree.
   Run a discriminating test or reproduce the reported behavior on that SHA,
   including the unaffected control where applicable. Apply the existing
   UI, science and privacy requirements to the evidence.
4. **Immediately before closure**, recheck the main SHA, issue state,
   linked PRs and live ownership. If main advanced, verify on the new green
   head. A live owner handles their own issue. For an unclaimed candidate,
   use the normal claim protocol before mutating it. When all criteria pass,
   post the evidence on the sibling, close it as completed and update its
   Project item. The proposed verification-only exception ends at issue
   closure. Any needed source change follows the usual one-issue PR route.

Keep a candidate open when a criterion fails, is unspecified, or has no
reproducible evidence. Record the remaining gap for its owner. If the
source PR is abandoned, its neutral reference leaves the issue open.

## PR examples

These excerpts use example issue numbers. The primary issue is #42 and the
candidate is #84. Include the rest of the applicable canonical template.

An existing schema-v2 PR can use this related-issue section.

```markdown
## Related issue

Closes #42

Sibling to verify after green main: #84 (same readiness response).
```

The human contributor handles DCO. Independent exact-head approval, the
selected review, the validation comment, publisher success and explicit
merge authorization still apply. Add the reference before finalization and
revalidate after an invalidating event.

A schema-v3 PR uses the same related-issue section and retains exactly
one primary number in the `Issue` field.

```markdown
## Automated contribution provenance

- Issue: Closes #42
```

Complete the remaining provenance fields with the real head, provider, tests
and claim ID. The validator requires a single closing reference in that field.
Appending the candidate there fails validation. A neutral reference elsewhere
does not certify the sibling or count as review evidence.

## Alternatives and follow-up

| Option | What it catches | Cost or remaining gap |
| --- | --- | --- |
| Search at claim, PR and post-merge time | A historically fixed issue, a broad fix or a finding corrected before merge | The author and closer must inspect actual acceptance criteria. |
| Add a validated sibling list | Missing or malformed entries for candidates the author declares | Adds API and parser dependencies. A well-formed number cannot establish that a fix works or discover an omitted sibling. |
| Periodic backlog sweep | Stale entries when a later sweep reaches them | Leaves avoidable pickups and duplicate investigation between sweeps. |

If approved, update both development contracts together. Revisit automation
if a subsequent case escapes the search or misses its closure check.
Record that case and the specific failure a validator could detect.
