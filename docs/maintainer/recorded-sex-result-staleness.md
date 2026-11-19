# Stored results after a recorded-sex correction

**Proposed decision for #2511.** When a person's recorded biological sex
changes, readers should immediately stop showing results that depend on the
old context. Offer to rerun the affected analysis for each linked sample. A
person correcting their profile should be able to save the edit even if a
sample is offline or an analysis is already running.

## Choice and scope

Choose **option B, withhold until current**. A warning attached to the old
result still allows its text, score or count to appear on its own in a report.
Starting every analysis during the edit (A) also couples a reference-database
write to several sample databases and potentially long jobs. Recomputing text
in each reader (C) could repair one sentence but cannot compute a newly
eligible score or establish what an empty run evaluated.

The current HFE scoring path calls `resolve_biological_sex`, renders the
finding, and saves the resolved value, source and conflict indicator in
finding detail. Cancer PRS saves its scoring context on individual stored
rows. Its dedicated route already checks current eligibility and explains
some skipped models. An empty set of rows cannot establish either run's
context. The individual update writes the reference database, and an
individual's linked samples can have different inferred values. These are
software observations at `cf08f712a130591598eae00648a1b8dce2d0b384`.

## What a completed run records

Record a versioned completion entry in the sample database for the HFE
assessment and a separate one for cancer PRS. Do this in the transaction
that replaces their respective findings, including successful runs with zero
rows. Persist the context actually supplied to scoring, the run identity,
the affected output scope and whether the run completed. A completed run
with an explicitly unresolved value differs from a missing snapshot. Do
not store a second copy of the inputs used to infer sex.

Compare each scope's actual dependencies. HFE detail exposes the resolver's
value, source and conflict flag, so a source-only change can invalidate that
detail even when the value matches. The cancer PRS gate currently consumes
the resolved value alone. Apply the rule to its sex-specific traits and
their eligibility metadata. Identify these traits by the model registry,
including existing legacy sex-specific rows. Keep eligible sex-independent
scores, monogenic cancer findings and unrelated modules visible under
their existing gates. When an old row could belong to the affected scope
and lacks an unambiguous type, withhold that row until it can be regenerated.

| Stored evidence and present context | What the person sees |
| --- | --- |
| Completed run and all inputs this output uses still match | The current result or its normal empty state. Re-saving the same value or editing only a display name produces no extra prompt. |
| XX → XY or XY → XX | Withhold the previous dependent output and its count. Show “This analysis used an earlier sex setting. Rerun it to update the result.” A newly eligible score is pending until computed. |
| Recorded override removed or added | Resolve each sample again. An inferred value matching the old value can keep a cancer gate current. Withhold HFE detail if its recorded provenance changed. Different resolved values stale both dependent scopes. |
| Known → unresolved, unresolved → known, or a change to the resolver's review state | Compare the actual resolver values, including explicit unknown, manual-review and absent values. Withhold incompatible stored output. A successful rerun applies the module's existing rules for unresolved context. |
| Successful empty run with matching context | Report completion with no presentable finding. If context changes, show the rerun state even with zero rows. A gated, disabled or under-covered model retains its own explanation. |
| Legacy run without a valid completion entry, including ambiguous empty history | If the scope may have run, show “Reanalysis needed to verify this result.” Withhold affected derived rows. Per-row sex, an empty table or a dismissal cannot certify the whole run. Use the ordinary first-run action only when a trustworthy record establishes the analysis never ran. |
| Failed, interrupted, superseded or partially written run | Preserve the previous completed snapshot. If it is stale, keep withholding. Allow a retry. Only a matching successful replacement can certify new output. |

## Where the rule applies

Use a shared, sample-aware eligibility check for every reader of the affected
stored findings. Apply it before pagination, sorting, counts, deduplication,
top-finding selection and report section construction. Resolve the current
sample context once per consistent read. A generic rerun notice contains no
previous sex, genotype, model score or old finding count. Existing opt-in and
payload-disclosure checks govern whether even the analysis-specific notice
may be shown.

- **Dedicated routes.** Keep the cancer PRS checks and reasons from #2506
  and #2510. Combine a known run context with current model eligibility so
  an old score cannot reappear and an unavailable model is never promised
  by a rerun. Apply the same stored-row eligibility in HFE's findings API.
- **Findings, summaries and reports.** Apply it to All Findings, module
  counts and top text, linked-sample counts on Individuals, variant detail
  and evidence cards, HTML previews and generated PDFs. Unaffected current
  rows from the same sample still contribute. A generated report should
  indicate incomplete coverage for included, visible affected modules.
- **Exports.** Exclude stale derived findings and their identifiers wherever
  stored findings select export content, including module-scoped FHIR and
  its size preflight. Keep independently eligible annotation observations,
  raw variant exports, SQL access controls and FHIR's live demographic
  resolution. Preserve existing read-time behavior of G6PD and the breast
  absolute-risk overlay. The inferred-only disclosure gate has a different
  dependency. Previously downloaded files cannot be revised.
- **Recovery.** Show one deduplicated prompt for each sample and affected
  analysis/context revision. Dismissal hides the reminder only. A typed prompt
  needs coordinated migration of the database CHECK constraint and API enum.
  Keep unavailable-database and required reannotation (423) handling. Show
  the appropriate prerequisite and offer the analysis once it can run.

The identity edit commits without writing any sample database. The shared
read check detects later inference changes, relinking and override removal
even if no edit event was delivered. At rerun, capture the inputs used and
a run generation. Protect replacement from a superseded writer. If identity
changes while scoring, readers must withhold results for the previous context.
Clear only this sample's matching, successfully completed reason.
Reference release and interpretation-revision prompts in [#2705](https://github.com/bioedca/Yeliztli/issues/2705)
have separate causes and can continue to apply.

### Ordering a profile correction and a result read

Give each sample a monotonic context revision in the reference database and
record the revision observed when a run starts. Increment it in the same
transaction as a recorded-sex or membership change for every affected sample.
Inference changes must participate too. Before replacing inference inputs in
the sample database, mark the reference-side context as updating under a
new revision. Readers withhold affected output until the replacement and
the resolved context are published. Recover an interrupted update by
reconciling its identity and revision before making the sample available.

Use a short cross-process publication guard shared by context writers and
all readers presenting dependent results. A reader captures the context and
revision, then builds its response or spools its export outside that guard.
Immediately before committing the immutable response for delivery, take the
guard and verify that every participating sample still has the captured
revision, the eligible completion generation and no update in progress.
If anything changed, discard the prepared result and retry from a fresh
snapshot or return the rerun state. The final check and commitment must
occur together while a profile correction cannot commit. The writer takes
the same guard when committing its revision change. Analysis publication
also takes it and verifies that the job still owns the intended generation.
For multi-sample results this ordering covers the complete membership set.

This defines when a result becomes current for a request. A correction
that commits first prevents publication of the earlier result. Bytes a
response already committed for delivery may arrive later. Render reports
to a private temporary artifact, validate before publishing its handle,
and guard a later download of a live cached artifact in the same way. Keep
slow rendering and network transfer outside the guard. A changed revision
forces fresh evaluation even if the resolved value has changed back. An
unchanged dependency set can still validate an existing completed result.

## Implementation acceptance

Use synthetic fixtures. Drive the real individual PATCH route, then read the
dedicated and generic routes, an individual summary, report and scoped export
before and after a rerun. Assert actual text, values, totals and identifiers.

1. Start with a positive HFE finding, a sex-specific cancer score and an
   unaffected positive control. Check both change directions and two linked
   samples with different inferred contexts. Verify all summaries exclude
   the old rows while the unrelated control stays visible.
2. Include a fully called HFE `hom_ref` sample, an explicitly unresolved run,
   an empty completed cancer run, a disabled model and a genuinely never-run
   sample. Distinguish stale, unavailable, current empty and first-run states.
3. Cover a matching-value source change, discordant override removal, no-op
   edit, absent or malformed legacy snapshot and denied disclosure. Check
   exact old text, count and score are absent from output and notices.
4. Dismiss and reload, retry after a failure, then rerun successfully twice.
   Change context while one run is in flight and finish two workers out of
   order. Check atomic replacement, no duplicates, preservation of competing
   staleness reasons and independence of other samples.
5. Pause a reader after eligibility evaluation but before response commitment.
   Commit the profile correction in a different worker and release the reader.
   The old text, count and score must be absent. Repeat with a prepared report,
   an export awaiting its first byte and an individual's membership change.
   Exercise the opposite order to verify that an already committed response
   does not prevent a correction or hold a lock during network transfer.

Maintainer approval of the withholding boundary, legacy handling and rerun
semantics releases [#2508](https://github.com/bioedca/Yeliztli/issues/2508)
for implementation.
