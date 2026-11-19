# Updating findings that were stored before a correction

**Proposed decision for #2705.** A person reopening an old sample should see
which analyses are current and how to recover an affected result. Use one
shared eligibility check for stored findings. Permit a read-time wording
update only when its applicability is provably unchanged. Withhold results
whose interpretation can have changed and offer a scoped rerun.

## Which changes need which action?

| Work item | Trigger | Proposed handling |
| --- | --- | --- |
| #2304, corrected prescribing text | Corrected guidance for a persisted drug finding | The shipped CYP2C19/clopidogrel repair addresses the Intermediate and Poor Metabolizer records independently. Withhold both superseded recommendations until a scoped rerun. A separate wording-only correction may project approved wording and citations into the recommendation field and finding text when its complete key, phenotype, activity score, partner-gene and population applicability, severity and action still match. Keep original analysis provenance and record the presentation revision. A missing or ambiguous key goes to reanalysis. |
| #2454, changed CPIC interpretation or callable allele | Updated interpretation can change the call, caveat, action, membership or whether a result exists | Withhold affected PGx output and prompt once per affected sample and analysis, listing safe reasons. A schema migration's return value or the presence of an old finding cannot establish who was affected. Reconcile even on installs that already received the reference migration. |
| #2435, seven pathway panels | Panel changes in allergy, sleep, skin, fitness, nutrigenomics, methylation or traits | Project only when the exact dependency, category, membership and rendered template still match. Refresh citations and embedded SNP detail consistently. Preserve independently authored notes and customized text. A moved SNP, newly eligible finding or changed pathway aggregate requires rerunning the affected panel. |
| #2413, changed analysis order | Historical cancer, APOL1, gout, nutrigenomics and traits runs may predate the ancestry-first order | Use an analysis revision and its input dependencies. Recompute ancestry before its affected consumers. If ancestry still cannot produce a usable result, let those modules report their normal unknown state. An unchanged reference bundle cannot certify the run order. |

A clinician-reviewed action or scope change always uses the interpretation
path, even when the database diff appears to change just a sentence. Content
owners declare a stable correction ID, affected scope, prior and target
revisions, dependencies, applicability predicate and migration tests. The
analysis owner controls its producer revision. Preserve the real upstream
release in reference provenance. A repository edit cannot manufacture a new
CPIC, panel-source or VEP release.

During rollout, inventory the shipped CYP2B6/efavirenz, CYP2D6/tamoxifen
and TPMT repairs alongside the existing phenytoin path. Classify each from
its released old and new records and test its actual sample-side effect
before enabling projection. For panels, match the genotype used in scoring
after module-specific normalization and verify the stored template and
citations. An ambiguous match goes to the panel rerun.

## Durable state and recovery

Record successful completion for each sample and affected producer, even for
zero results. Keep the input and producer revisions and a local generation.
Write the completion record atomically with its finding set. Track a wording
projection separately so the original analysis remains auditable. The sample's
verified storage identity scopes every correction, including after restore or
replacement. The registry's existing CYP2C9/phenytoin prompt validates that
identity and a file fingerprint before publication; a fingerprint alone cannot
identify the inputs or analysis revision.

| Evidence at open or upgrade | Reader and repair behavior |
| --- | --- |
| Proven current or provably outside the correction scope | Serve current output under existing disclosure rules. Repeated startup creates no prompt. |
| Legacy run or empty history with no dependable revision | Do not infer currentness from zero rows, dates or an upgraded reference DB. If prior execution or affectedness cannot be excluded, withhold the affected scope with “Reanalysis needed to verify these results.” If a trustworthy marker proves no run occurred, use the ordinary first-run action. |
| Reviewed wording-only correction with one verifiable mapping | Show the amended wording consistently on every surface. Preserve source values and the old analysis revision. Keep unrelated and user-authored text intact. If a custom field could contain stale clinical guidance and cannot be separated safely, retain it in storage and request review or rerun before patient presentation. |
| Interpretation, membership or dependency change | Suppress stale affected results before counting, deduplicating and choosing a top finding. Surface a generic rerun reason through authorized readers. Unaffected results stay visible. |
| Installed reference content is missing, duplicated or otherwise unverifiable | Keep affected output withheld and identify the local reference component needing repair. A successful scorer exit or an empty row set cannot resolve this state. Verify the installed correction's required content and applicability before rerunning and clearing it. |
| Storage unavailable, failed job or interrupted replacement | Preserve the prior completed state and pending reasons. Never mark an analysis current because a prompt was emitted or a run was queued. Reconcile and retry without duplicating rows or losing an independent reason. |
| Successful rerun at the target revisions, including zero results | Atomically replace the producer's replaceable results and completion record. Retain audit-only rows under their existing disclosure rules, including withdrawn prescribing alerts. Clear only matching reasons for this sample and run generation. A repeated rerun gives the same visible set. Another correction remains pending. |

Keep authoritative pending state in the sample database. Publish central UI
reminders idempotently using sample identity, producer, correction and target
revision. Retry an interrupted cross-database publication on the next valid
open. Check the current local state and ownership before publishing or
retracting. Bind publication to the observed generation. After the central
write, recheck the local reason and identity. Conditionally retract only a
reminder from the obsolete generation if a rerun, replacement or dismissal
won the race. On interruption reconcile both sides, including a central
reminder whose local reason has cleared. Check eligibility again when
serving reminders. Dismissal hides that reminder for that revision. It never restores
a stale finding or acknowledges a different sample, revision or correction.
Deduplicate the user-facing action when several reasons share a rerun. Existing
prompt schema constraints and API enums need a coordinated migration for a new
reason. The existing major-version 423 gate remains a prerequisite when it
applies.

A completed prerequisite with an explicitly unknown result can supply a
documented unknown input. A failed or unfinished prerequisite supplies no
completion proof. Keep dependent results stale until a successful run can
establish the required input and outcome.

When a legacy finding carries customized text, preserve the original text and
its sample, producer and historical finding identity in a local record outside
the replaceable result set. Commit that preservation before or atomically
with replacement. Stamp the newly computed rows with their run's actual
provenance. A retained row or archived note keeps its original provenance
even when the full reannotation job stamps findings from the new run.
Recover a note only through an authorized history view
that applies the sample's disclosure rules and identifies the prior result.
Place the archive under the same protections used for audit-only content,
including direct SQL query and export controls from its first write. Only
reattach a note to a current result when its identity is unambiguous.

The transaction must cover every producer the write actually changes. The
methylation writer currently migrates historical MTHFR nutrigenomics rows
before replacing its own results. Put any affected rows for both panels,
customized text and completion state in one transaction for the scoped
repair. A failure restores the prior state for both panels.

Use **scoped automatic recomputation** only for bounded, deterministic repairs
with verified inputs, an idempotent transaction and a tested failure path. The
legacy rare-variant repair in #2606 provides one precedent. Expensive jobs,
unavailable inputs or changed clinical interpretation use the user-initiated
rerun. A per-sample job must capture its dependencies, acquire the appropriate
write lease and compare its generation again at publication. A slow old run
cannot overwrite a newer result. If the published reference/context changes
while a reader builds a response, retry or withhold before committing the
response. Coordinate the cross-process publication boundary with
[#2511](https://github.com/bioedca/Yeliztli/issues/2511); recorded sex remains
a separate cause.

## Consumer contract and acceptance

Apply eligibility before query limits, group counts, evidence levels and
summary selection. Use the same projection on module list/detail, All
Findings, pathway or drug detail, variant cards, Individuals, global summary,
report preview, HTML/PDF and finding-based export. Scope FHIR selections
derived from finding membership consistently in both preflight and delivery.
An unscoped FHIR export also uses findings for disclosure. When a gated
module names an rsID, only an independently eligible, visible finding may
justify including it under the shared-membership exception. Apply this rule
to both calls even when `modules` is omitted.
Raw variant annotation and source assertions retain their own provenance and
access rules. A stale-state reason must respect opt-in gates and avoid
revealing a withheld gene, drug or carrier status.

The updates banner reads a separate saved diff. Preserve the original
`last_finding_diff_json` under its existing audit and retention rules. When
producing a new diff, capture each prior finding's producer, input and
presentation revisions before replacement and its successor's revisions
from the completed run. Persist the appropriate side for an addition or
removal and both sides for a change in a versioned diff. At
`GET /api/updates/finding-changes`, check this evidence for every displayed
value. Legacy diffs have no per-entry revision. Only present entries that
can independently be proved eligible. Project only a verified wording change
and withhold entries with unsafe or unverifiable text. Apply disclosure
checks to the final display, recalculate each bucket count from its visible
entries and return `available=false` when none remain. A later rerun cannot
make obsolete text in an older snapshot eligible. Record the actual correction
cause separately from upstream release deltas. When a producer changed and
the source releases did not, the banner can say “Analysis updated.” Attribute
changes to an upstream release only when that attribution is established.

For the four implementation issues, use the actual scoring, persistence,
upgrade and read paths with synthetic controls:

- Score and store the released legacy clopidogrel Intermediate and Poor
  Metabolizer fixtures before upgrading the reference. Verify each old
  recommendation is withheld after upgrade and that each scoped rerun returns
  its own shipped text in the drug view, summary, card and report. Include an
  unaffected Normal Metabolizer control. Run full reannotation using a different
  reference snapshot and producing no presentable alerts. Verify the retained
  audit-only CYP2D6/tamoxifen row keeps its original provenance and stays
  withheld. In a separate synthetic wording-only update, assert projected
  text, detail and citations on the same surfaces. A missing legacy key or
  an applicability change requires reanalysis. A missing or duplicated
  *installed* reference row keeps the correction pending even if scoring
  finishes. Repair the reference and verify that only a subsequent run using
  the verified content clears that reason.
- Take the DPYD `rs115232898` reference migration through both a fresh
  upgrade and an install that already has the target allele. Seed synthetic
  raw inputs for an affected stored result, an empty completed result, a
  sample whose inputs exclude the marker and a fully called `hom_ref`
  control. Assert the old and corrected calls, the eligible findings, and
  exactly which sample receives a reminder across two startups, dismissal
  and successful rerun. A current run stays current on either install.
  Verify the caveat as well as the call before classifying an input as
  unaffected. A missing or uncalled marker cannot establish non-carriage.
  Delay central publication until after the rerun clears the sample's
  reason. The late publisher and a restart must leave no obsolete reminder,
  preserve an independent live reason and honor dismissal.
- Exercise each of the seven panel readers with a wording-only change and a
  change that moves category, eliminates a row, introduces a row or changes
  the pathway aggregate. Cover missing or ambiguous keys and custom wording.
  Assert identifiers, counts, text and citations on both sides of each filter,
  including a fully called non-carrier for a carriage-gated finding.
  Remove a customized finding in a successful rerun. Recover its exact
  original note through authorized history and verify that the ordinary SQL
  console, SQL export, reports and current findings cannot return its archived
  payload, including after another rerun. With the real methylation writer,
  interrupt the repair after legacy MTHFR migration and before replacement.
  Verify both panels, original notes and completion records on failure and
  retry, including a control outside the migrated set.
- Restore a pre-ancestry-first fixture, rerun the prerequisite and its
  dependent modules, then open it again. Check an unaffected module, empty
  results, failed prerequisites, concurrent readers and replacement of a
  sample with the same registry ID. A stale result must never be presented as
  current through a secondary surface.
- Generate diffs with the production snapshot, comparison and persistence
  paths. Open an undismissed legacy diff and its new-format equivalent,
  each containing a stale added prescribing alert and one entry whose
  eligibility can be proved. Verify the banner API returns only the eligible
  text and exact bucket counts, and that rendering the banner cannot reveal
  the old text. Cover stale before/after values, an all-withheld diff, and
  a repeated read after a successful rerun. With unchanged reference releases
  and a new analysis order, verify the banner attributes the visible change
  to the analysis or uses neutral wording.
- Use the same module selection and `include_all=true` on
  `GET /api/export/fhir/eligibility` and `POST /api/export/fhir`. Seed two
  synthetic carried rsIDs, one selected only by a stale finding and one by a
  current finding, plus a selected `hom_ref` control. Before repair require
  an observation count of one and exactly the current rsID in the delivered
  Observations. Rerun with a current finding for the formerly stale rsID.
  Require a count of two and the exact pair of carried rsIDs in the bundle.
  Repeat both calls without a module selection, using an rsID named by a
  gated finding and a stale visible finding, plus a separate eligible rsID.
  Require only the separate rsID and a count of one. When the visible
  finding becomes current and independently eligible, require both rsIDs
  and a count of two.

This document records a proposal. The four linked implementation issues
require a maintainer decision on the limited wording projection, default
withholding and rerun behavior, and completion criteria above before they
change stored or presented results. Record that decision with the proposal
and link it from those issues.
