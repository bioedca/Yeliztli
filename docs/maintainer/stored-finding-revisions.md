# Updating findings that were stored before a correction

**Proposed decision for #2705.** A person reopening an old sample should see
which analyses are current and how to recover an affected result. Use one
shared eligibility check for stored findings. Permit a read-time wording
update only when its applicability is provably unchanged. Withhold results
whose interpretation can have changed and offer a scoped rerun.

## Which changes need which action?

| Work item | Trigger | Proposed handling |
| --- | --- | --- |
| [#2304](https://github.com/bioedca/Yeliztli/issues/2304), corrected prescribing text | Corrected guidance for a persisted drug finding | The shipped CYP2C19/clopidogrel repair addresses the Intermediate and Poor Metabolizer records independently. Withhold both superseded recommendations until a scoped rerun. A separate wording-only correction may project approved wording and citations into the recommendation field and finding text when its complete key, phenotype, activity score, partner-gene and population applicability, severity and action still match. Keep original analysis provenance and record the presentation revision. A missing or ambiguous key goes to reanalysis. |
| [#2454](https://github.com/bioedca/Yeliztli/issues/2454), changed CPIC interpretation or callable allele | Updated interpretation can change the call, caveat, action, membership or whether a result exists | Withhold affected PGx output and prompt once per affected sample and analysis, listing safe reasons. A schema migration's return value or the presence of an old finding cannot establish who was affected. Reconcile even on installs that already received the reference migration. |
| [#2435](https://github.com/bioedca/Yeliztli/issues/2435), seven pathway panels | Panel changes in allergy, sleep, skin, fitness, nutrigenomics, methylation or traits | Project only when the exact dependency, category, membership and rendered template still match. Refresh citations and embedded SNP detail consistently. Preserve independently authored notes and customized text. A moved SNP, newly eligible finding or changed pathway aggregate requires rerunning the affected panel. |
| [#2413](https://github.com/bioedca/Yeliztli/issues/2413), changed analysis order | Historical cancer, APOL1, gout, nutrigenomics and traits runs may predate the ancestry-first order | Use an analysis revision and its input dependencies. Recompute ancestry before its affected consumers. If ancestry still cannot produce a usable result, let those modules report their normal unknown state. An unchanged reference bundle cannot certify the run order. |

A clinician-reviewed action or scope change always uses the interpretation
path, even when the database diff appears to change just a sentence. Content
owners declare a stable correction ID, affected scope, prior and target
revisions, dependencies, applicability predicate and migration tests. The
analysis owner controls its producer revision. Preserve the real upstream
release in reference provenance. A repository edit cannot manufacture a new
CPIC, panel-source or VEP release.

A correction may explicitly supersede earlier corrections for the same
scope. The owner records which obligations the successor satisfies and
tests the direct upgrade. Verify that the installed successor and completed
rerun satisfy every covered obligation before retiring the older reasons.
An unrelated reason, an uncovered part of an older scope or an unverified
successor stays pending. Dates, version order and a shared drug name alone
cannot establish supersession. Reject cyclic or contradictory declarations.

During rollout, inventory the shipped CYP2B6/efavirenz, CYP2D6/tamoxifen
and TPMT repairs alongside the existing phenytoin path. Classify each from
its released old and new records and test its actual sample-side effect
before enabling projection. Include the DPYD activity-score guidance matrix
from [#2119](https://github.com/bioedca/Yeliztli/pull/2119) as its own
content correction. The current marker table, confidence algorithm and
conservative diplotype algorithm do not certify the separate dosing table.
For panels, match the genotype used in scoring
after module-specific normalization and verify the stored template and
citations. An ambiguous match goes to the panel rerun.

## Durable state and recovery

Record successful completion for each sample and affected producer, even for
zero results. Keep the input and producer revisions and a local generation.
Write the completion record atomically with its finding set and every
patient-visible value derived by that run. In PGx this includes the CPIC
coverage bit written to annotated variants. Publish the desired CPIC bit
for the affected scope with its alerts and leave other coverage bits intact.
Before repair, every variant reader must mask a CPIC contribution whose
eligibility is unknown. Apply the same rule to filters and counts as to a
displayed badge. Return a per-source pending mask alongside verified
coverage. A pending source supplies neither a positive nor a negative
answer. Keep unrelated verified sources visible and label the unresolved
source as pending. Show “no sources covered” only when absence is known for
all relevant sources. Version the API representation. A client that has
not negotiated pending coverage must receive an upgrade-required response
without variant data whenever the response could contain a pending source.
The legacy tooltip treats both zero and null as confirmed absence. Test a
legacy client and a current client on the same pending-only and mixed rows.
Before activating this protocol on an existing installation, close the old
application windows and browser tabs, then reopen them on the current
client. An already-open legacy tab can keep a cached finding indefinitely
without making a request. Treat this as a coordinated upgrade prerequisite.
Retire old backend processes and their launch configuration as well. The
supported launcher or an external storage boundary must keep an older
reader or writer away from lifecycle-managed samples, including after a
service or container rollback. If that boundary cannot survive a downgrade,
run the old release only against an isolated compatible pre-upgrade backup,
with separate references and queue. Preserve the upgraded installation for
recovery. Keep the backup out of patient-facing reads and exports until its
eligibility can be verified. Opening an upgraded sample in place with an
older release is unsafe.
Before the new worker consumes queued jobs, drain old work under its
original verified installation or retire it. A job submitted for a sample
must carry its copy identity from enqueue through execution and publication.
A job with an unverified identity cannot resolve a current copy by numeric
ID or path. Offer a new run for the explicitly selected current copy.
In the SQL console and SQL export, expose a nullable
predicate for each source. It returns true for verified coverage, false for
verified absence and SQL NULL for pending or unassessed coverage. Expose
processing status separately. Permit access to the legacy integer mask only
where every source is resolved or the existing unannotated state is known.
Refuse ambiguous mask queries, including filters and aggregates, when the
queried relation can include pending rows. Clearing the bit on such rows
would make a negative test count them as uncovered.
Track a wording projection separately so the original
analysis remains auditable. The sample's
verified storage identity scopes every correction, including after restore or
replacement. The registry's existing CYP2C9/phenytoin prompt validates that
identity and a file fingerprint before publication; a fingerprint alone cannot
identify the inputs or analysis revision.

Keep the existing processed/unannotated distinction separate from a single
source's coverage. The `annotation_coverage:notnull` view includes a
processed row with mask zero and a processed row with verified VEP and
pending CPIC. The `:null` view still identifies unannotated rows. Exclude
a pending source from that source's positive and negative predicates only.

Give each installed copy of a sample a fresh identity on restore or
replacement, even when its registry ID, content hash, path and creation
time are preserved. The registry identifies each installed copy. Bind
writers, links, reminders and dismissals to that identity before comparing
revisions. On replacement, retire the superseded copy as part of the switch.
On a side-by-side restore, the importer assigns a distinct ID and path on
collision. Keep both copies active and fence writes by each copy's own
identity. Before publishing any sample or shared reference, durably
record one restore operation with a retry key established before upload,
the archive digest, target installation,
the planned copy IDs and paths, and the reference publication ID. Keep
partially registered copies unavailable. Retrying that operation reuses
its allocations and reconciles completed steps. An intentional second
import gets a new operation ID, even for the same archive. Reject a
retry whose archive or target has changed. Reconcile the imported copy
when both stores agree on its identity. Within one copy, use an ordered,
sample-local publication counter for conditional writes.
The existing annotated-variants generation is a UUID equality token for a
table swap. Never use it to order publications.

Bind producer completion and coverage contributions to the annotated-variants
generation actually used. The full annotation path replaces that table
before running the analysis modules. Atomically advance its generation and
invalidate dependent receipts when the staging table goes live. Carry a
contribution forward only with proof that its inputs, output and disclosure
still match. A process that stops after the swap leaves dependent results
pending until their generation has been verified.
Record the entire set of generation tokens the producer used. A PRS model
with positional weights may read both annotated calls and imputed dosages.
Capture the imputed-variants token even when an applicable positional lookup
returned no rows. Models that cannot consult this table need no imputation
dependency. Reject publication if an input token changed during scoring,
including a dosage replacement that leaves the annotated table untouched.
Record reference artifacts and supporting results that affected the
outcome. For PRS calibration this includes the score weights, the relevant
ancestry and monogenic findings, and each applicable frequency lookup.
Record a verified revision of the installed `gnomad_af.db` even if no
matching allele row or usable population frequency was found. If the
store was missing, record that distinct state. A new match can change a
previously uncalibrated score while the sample stays identical. Record a
verified version of each artifact that changes on content replacement, including committed
database updates, even if its upstream label is unchanged. A trusted
publication or content revision must distinguish different scoring inputs
even when filesystem metadata agrees. Use the existing
scoring fingerprint inputs as an inventory and compare the inputs before
publishing and when reopening a completed score.

| Evidence at open or upgrade | Reader and repair behavior |
| --- | --- |
| Proven current or provably outside the correction scope | Serve current output under existing disclosure rules. Repeated startup creates no prompt. |
| Legacy run or empty history with no dependable revision | Withhold the affected scope with “Reanalysis needed to verify these results” only when a prior run is possible **and** its inputs could be affected. Proven unaffected inputs need no correction prompt, even with unknown history. A trustworthy marker proving no run occurred leads to the ordinary first-run action. Zero rows, dates and an upgraded reference DB cannot establish currentness. |
| Reviewed wording-only correction with one verifiable mapping | Show the amended wording consistently on every surface. Preserve source values, original notes and the old analysis revision. Keep inseparable custom clinical guidance in protected history until a separate content review approves it. A rerun may publish a new canonical finding without publishing the historical note. |
| Interpretation, membership or dependency change | Suppress stale affected results before counting, deduplicating and choosing a top finding. Surface a generic rerun reason through authorized readers. Unaffected results stay visible. |
| Installed reference content is missing, duplicated or otherwise unverifiable | Keep affected output withheld and identify the local reference component needing repair. A successful scorer exit or an empty row set cannot resolve this state. Verify the installed correction's required content and applicability before rerunning and clearing it. |
| Storage unavailable, failed job or interrupted replacement | Preserve the prior completed state and pending reasons. Never mark an analysis current because a prompt was emitted or a run was queued. Reconcile and retry without duplicating rows or losing an independent reason. |
| Successful rerun at the target revisions, including zero results | Atomically replace the producer's replaceable results and completion record. Retain audit-only rows under their existing disclosure rules, including withdrawn prescribing alerts. Clear only matching reasons for this sample and run generation. A repeated rerun gives the same visible set. Another correction remains pending. |

### Reminders and prerequisites

Keep authoritative pending state in the sample database. Publish central UI
reminders idempotently using sample identity, producer, correction and target
revision. Retry an interrupted cross-database publication on the next valid
open. Check the current local state and ownership before publishing or
retracting. Bind publication to the observed generation. The central store
must accept writes only at or above its last verified generation for that
identity and key. Retain this generation when retracting or dismissing a
reminder so a late writer cannot recreate or overwrite it. Retries at the
same generation must agree on the reason and acknowledgment state. After the central
write, recheck the local reason and identity. Conditionally retract only a
reminder from the obsolete generation if a rerun, replacement or dismissal
won the race. On interruption reconcile both sides, including a central
reminder whose local reason has cleared. Check eligibility again when
serving reminders. Bind a dismissal to the verified sample identity,
displayed publication counter, target revision and exact set of reasons.
Apply it only if all still match. If a
different reason or revision arrived while the tab was open, return the new
state for review and leave it visible. Dismissal never makes a finding current.
Deduplicate the user-facing action when several reasons share a rerun. Existing
prompt schema constraints and API enums need a coordinated migration for a new
reason. The existing major-version 423 gate remains a prerequisite when it
applies.

On upgrade, reconcile the v21 local phenytoin marker with its central
`version_staleness` entry under the existing sample-ownership check.
Adopt a dismissed entry only when it can be bound to this installed copy
and the same legacy CPIC correction and target. Carry that acknowledgment
to the matching reason only. The local repair remains pending until a
verified rerun. Preserve a matching active reminder as visible. A missing,
ambiguous or unbound acknowledgment cannot silence a reason. Applying the
migration twice must preserve the same result. Stock v21 stores a hash of
registry metadata with the central entry. The file fingerprint checked at
publication was never persisted with that acknowledgment. Matching these
legacy fields alone leaves the reminder visible, including after a restore
that reproduces them. Preserve the historical dismissal in its original form.

A completed prerequisite with an explicitly unknown result can supply a
documented unknown input. A failed or unfinished prerequisite supplies no
completion proof. Keep dependent results stale until a successful run can
establish the required input and outcome.

### Protected history and upgrades

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
reattach an independently presentable note to a current result when its
identity is unambiguous and its content is still appropriate. A match on
gene, drug and phenotype alone cannot approve copied prescribing advice.

On normal upgrade and backup restore, inventory the old schema before any
migration deletes **or rewrites** an affected finding. Preserve its original
text, structured detail and provenance, including a missing provenance value,
under the protected history policy. This must precede the pre-v21 phenytoin
deletion and the TPMT and CYP2B6/efavirenz in-place updates in v22 and v23.
Capture the original saved diff before those migrations scrub or rewrite
its entries, including when a removed finding exists only in the diff.
An install whose earlier migration already deleted text or cleared provenance
can identify a gap. Recover an original only from a verified source such as
the prior backup. A new run's provenance cannot fill that gap.

### Repairs that span producers

The transaction must cover every producer the write actually changes. The
methylation writer currently migrates historical MTHFR nutrigenomics rows
before replacing its own results. Put any affected rows for both panels,
customized text and completion state in one transaction for the scoped
repair. A failure restores the prior state for both panels.
The GWAS coverage bit can have multiple producers. Record each producer's
current contribution per variant, including a verified source annotation
when applicable. Expose the bit only when at least one current contributor
supports it. Publish each contribution with its own completed findings.
Removing one producer's contribution cannot clear another's. A legacy set
bit alone does not identify a contributor. Reconcile the contributing inputs
before showing it when its only possible support is uncertain.
Assign methylation ownership of the standalone SNP findings for the three
identifiers in that migration. Nutrigenomics still owns its pathway-level
interpretation, supporting SNP detail and citations. Track each module's
eligible rsID membership independently of standalone row ownership. When
the same SNP qualifies in both modules, retain both membership records with
their own producer revisions and gates. A scoped FHIR export can then
include it for either eligible module without creating a second standalone
finding or duplicate Observation. Persist the applicable
recommendation and its own PMID list per SNP in the nutrigenomics pathway
detail only when that run's existing called, non-Standard finding predicate
would have published them. A Standard SNP may have text in the source panel
and still return a null recommendation and empty per-SNP PMID list.
Preserve the independently computed pathway-level citations. Read the fields from the pathway
detail even when there is no standalone nutrigenomics row. Missing legacy
detail requires reconciliation before display. Its writer must honor
the same ownership rule on every later run. For an old sample needing both
panels, repair them together, or keep the affected scope pending until both
can publish consistently.

A user can run nutrigenomics alone. If its current pathway qualifies a shared
SNP and methylation has no eligible completed result, publish the eligible
nutrigenomics pathway and membership and mark the standalone finding as
awaiting its owning analysis. Offer the methylation action on affected
surfaces subject to disclosure. It contributes no standalone count or card
until an eligible methylation run produces it. A successful methylation run
that produces none resolves this pending state to a verified absence.

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

Publish the allowed repair action with the stale state. When a correction
requires a manual rerun, every scoring page must wait for the user's action.
The existing `useAutoScore` hook starts work on a settled non-current state,
so a plain `stale` flag cannot communicate this choice. Ordinary first-run
scoring and explicitly approved automatic repairs retain their own policy.

## Which readers need the eligibility check?

Apply eligibility before query limits, group counts, evidence levels and
summary selection. Use the same projection on module list/detail, All
Findings, pathway or drug detail, variant cards, direct finding SVGs, Individuals, global summary,
report preview, HTML/PDF and finding-based export. Scope FHIR selections
derived from finding membership consistently in both preflight and delivery.
Bind the picker or preflight, preview and final delivery to the selected
sample-copy identity, result generation and selection. Require those
values on FHIR and PDF requests. Recheck authorization and eligibility
against a stable snapshot before delivering bytes. A replacement or
generation change returns a conflict without artifact bytes and requires
a fresh selection or preflight. Bind any retrievable report to that same
identity and recheck it on download. An older
request unable to identify its copy must not return a report.
Carry completeness separately from surviving rows into the HTML preview
and the generated PDF. If a selected, disclosable analysis is pending,
include a generic notice in the document itself and qualify its totals,
even when that analysis contributes no section. Apply the same disclosure
decision to the notice, counts, ordering and section titles.
Populate the Report Builder picker and its “Select all” action from
eligible findings and independently disclosable pending analyses. A
pending-only module remains selectable with zero eligible findings. Never
expose a hidden module through the picker or its totals.
An unscoped FHIR export also uses findings for disclosure. When a gated
module names an rsID, only an independently eligible, visible finding may
justify including it under the shared-membership exception. Apply this rule
to both calls even when `modules` is omitted.
For the caller's authorized selection, return whether reanalysis could
change the eligible observations. Explain that incomplete state in the
preflight and in the delivered `DiagnosticReport.conclusion` alongside
its research-use disclaimer. Keep the existing preliminary report status.
Derive the notice only from reasons the caller can independently see.
Adding or removing a gated reason alone must leave the notice unchanged.
Patient-independent raw annotations and reference assertions retain their own
provenance and access rules. Every response derived from a sample finding
inherits its eligibility before any join, count or lookup. In particular,
`GET /api/analysis/pgx-guidelines` joins prescribing alerts to external
sources. Filter the sample side first, including alerts for which no
supporting source is available. A stale-state reason must respect opt-in gates and avoid
revealing a withheld gene, drug or carrier status.

The medication safety report aggregates evidence across genes. Compute each
drug verdict from eligible effects and independently known, disclosable
pending assessments. A confirmed actionable effect warrants review even if
another is pending. When a pending effect could change an otherwise routine
verdict, show “Reanalysis needed” and keep the eligible effects visible.
With pending assessments, label aggregate counts as confirmed so far and
mark the report incomplete. Apply the same rule when no eligible effect
remains. Preserve permanent clinical holds throughout.
Eligibility follows each effect's full dependency set, including partner
genes used to choose between standalone and joint advice. A row for a
different gene of the same drug can still depend on the unresolved call.

Negotiate the lifecycle capability on every response whose meaning depends
on pending results. Send the upgrade requirement, without a legacy verdict,
whenever the caller cannot represent the response. Test an incomplete
medication report on a legacy client with all variant coverage bits resolved.
Compatible clients should still see the eligible effects and incomplete count.

Cache identity must include the verified sample identity and its published
result generation. Advance and notify clients on correction registration,
dependency invalidation, result publication or disclosure change. For a
change within one database, commit the invalidation and a durable change
notice in its transaction.

A shared reference-file replacement spans the filesystem and potentially
many sample databases. Before the switch, durably record a publication ID,
the old and intended content revisions, an in-progress state and a change
notice in the authoritative reference registry. From that point, fence
dependent reads and in-flight publications across *all* installed samples.
Clients already displaying them use the same generation and notification
rules. Install the verified file durably, then commit its confirmed revision
and a replayable completion notice. Reconcile each dependent sample by
publication ID and sample-copy identity. Serialize changes to the same
artifact. Reconcile only the current intent. A retry must not duplicate
a notice or clear a newer reason. On interruption, recheck the installed
content. Complete a matching new publication or retire the intent if the
old revision is intact. Reevaluate the sample's other eligibility rules
in either case. For any other content, keep affected results pending
until recovery establishes the installed revision.

Revalidate on mount, navigation, focus and reconnect. While a view is open, a change
notification invalidates its prior generation. If notifications disconnect,
require revalidation before showing cached results. Close the gap between
a GET and subscription by replaying changes since the generation returned
by the GET, or rechecking it after the subscription is live. On mismatch discard
cached patient output, including an old withheld state, and fetch under the
new generation. Discard late responses from the old one. A refetch that
continues displaying old data does not protect this boundary.

Finding links and card requests must identify the verified sample, result
generation and finding. A SQLite integer row ID alone can name a different
row after replacement. Require that identity on direct SVG, variant-card and
other by-ID reads, including copied links. Return the ordinary unavailable
response when the generation changed or a legacy link cannot prove its
identity. Current listings supply fresh links.

### What the Updates page can show and dismiss

The updates banner reads a separate saved diff. Preserve the original
`last_finding_diff_json` under its existing audit and retention rules. When
producing a new diff, capture each prior finding's producer, input and
presentation revisions before replacement and its successor's revisions
from the completed run. Full and scoped repairs must commit either the
finished diff or the protected before/after evidence needed to finish it
idempotently with that result generation. Display or clear the repair
reminder only according to its own state. A completed repair must retain
the comparison across a crash, and retries must not replace a newer diff
or recreate a dismissed one. Persist the appropriate side for an addition or
removal and both sides for a change in a versioned diff. Include each
producer's completion status on **both** sides. Distinguish a verified
nonempty or empty run from an unknown legacy completion. Compare membership
only across two verified completions for that producer. With an unknown
baseline, do not infer additions or removals. A verified stored legacy row
can still establish a change to one matched finding if its identity and old
field value are independently proven and its current replacement comes from
a verified completion. Keep the prior run status unknown and omit the change
when the old value or match is ambiguous. Within a matched
identity, compare the correction-specific fields declared by its owner,
including the reviewed prescribing action and the gout model's structured
odds ratio, as well as the existing structured meaning fields. A changed prescribing action can require a notice with the
same drug, phenotype and evidence level. Keep the old advice in protected
history and present a safe explanation linked to eligible current guidance.
A punctuation-only projection does not constitute a new prescribing action. At
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

Give each published diff an immutable identity independent of its reminder.
The Updates page discovers undismissed, eligible diffs for authorized samples
even after the last repair reason clears. Recheck eligibility before
advertising a sample or its count. Accept a dismissal only for the sample
and exact diff the user saw. Atomically compare both its identity and the
displayed eligibility revision, including disclosure state. Persist an
acknowledgment for each entry and presentation revision actually shown.
Do not acknowledge a hidden entry or reveal its existence in the response.
A later disclosure or content change can surface newly eligible or revised
entries without reviving unchanged ones already acknowledged. A late request
after newly eligible entries become visible must leave the current view
undismissed even when the stored diff ID is unchanged. A repeated request about an old diff leaves its
replacement visible. Dismissing a diff has no effect on a reanalysis reason.

Honor an existing legacy snapshot-wide dismissal for that immutable
historical snapshot only. Preserve its original flag in history and retire
the banner without inventing per-entry acknowledgments for it. Newly
published corrections and diffs start with independent identities and
remain eligible to appear. This one-time migration preserves the previous
user action even when its historical visible subset is unknowable.

## Acceptance cases for the four implementation issues

Use the actual scoring, persistence, upgrade and read paths with synthetic controls.

### Corrected prescribing text

- Score and store the released legacy clopidogrel Intermediate and Poor
  Metabolizer fixtures before upgrading the reference. Verify each old
  recommendation is withheld after upgrade and that each scoped rerun returns
  its own shipped text in the drug view, summary, card and report. Include an
  explicit `reanalysis_needed` state on the drug API and panel whenever an
  unresolved correction applies to that sample and gene–drug pair, including
  when the previous completed run was empty. Consult eligible correction
  records independently of reference discovery, before a missing guideline
  can exclude a gene or produce a drug-level 404. Combine known pending
  pairs with independently eligible reference rows under the existing
  disclosure and permanent withholding rules. Show the action to update guidance.
  Expose pending pairs through a disclosure-checked, sample-specific list
  or link on the Pharmacogenomics page. Keep the shared drug catalog
  independent of samples. Navigate from the actual page for each fixture
  and verify the pending drug remains reachable when its last reference
  row is removed. An unaffected sample sees its own ordinary list.
  An actually unassessed control follows its existing `not_assessed` path.
  Assert both API states and displayed explanations before the rerun. Include an
  unaffected Normal Metabolizer control. Run full reannotation using a different
  reference snapshot and producing no presentable alerts. Verify the retained
  audit-only CYP2D6/tamoxifen row keeps its original provenance and stays
  withheld. In a separate synthetic wording-only update, assert projected
  text, detail and citations on the same surfaces. A missing legacy key or
  an applicability change requires reanalysis. A missing or duplicated
  *installed* reference row keeps the correction pending even if scoring
  finishes. Repair the reference and verify that only a subsequent run using
  the verified content clears that reason.
  Remove every reference row for a known pending drug, then remove just
  its affected gene from a multi-gene drug. In both cases require the
  pending state without guidance for that pair, preserving any current
  independent effect. A genuinely unknown drug still returns 404.
  Keep a custom note containing superseded guidance on the same finding
  identity through that run. Verify the new canonical recommendation is
  eligible while the historical note remains outside all current results.
  Upgrade and restore fixtures from before each legacy repair. Verify the
  original phenytoin, TPMT and CYP2B6/efavirenz text, detail and provenance
  in protected history after deletion or in-place update and a second open.
  Seed a saved diff containing a removed item whose original finding row is
  already gone. Verify that its original payload survives in protected history
  after upgrade and restore and remains absent from patient-facing output.
  Check continued withholding. On an already-migrated fixture verify that
  unavailable text and provenance remain explicitly unknown.
  Use the two shipped TPMT/thioguanine Poor Metabolizer revisions
  (`38b6ccea` and `54de582d`) to compare a direct upgrade across both
  with a staged upgrade. After a verified run at the successor, both
  yield the same current recommendation and no obsolete reminder. An
  unrelated correction stays pending, including after a restart.

### PGx interpretation, reminders and coverage

- Take the DPYD `rs115232898` reference migration through both a fresh
  upgrade and an install that already has the target allele. Seed synthetic
  raw inputs for an affected stored result, an empty completed result, a
  sample whose inputs exclude the marker and a fully called `hom_ref`
  control. Assert the old and corrected calls, the eligible findings, and
  exactly which sample receives a reminder across two startups, dismissal
  and successful rerun. A current run stays current on either install.
  Separately keep the target allele and dosing rows installed while
  omitting only the shipped `c.557A>G/c.557A>G` mapping from
  `cpic_diplotypes`. Require the affected correction to stay pending
  across restart and an attempted run. Verify the allele, diplotype
  mapping and guideline independently, restore the mapping, then run the
  ordinary scorer and check its stored and presented correction before
  clearing the reason. An intact reference is the comparison case.
  Verify the caveat as well as the call before classifying an input as
  unaffected. A missing or uncalled marker cannot establish non-carriage.
  Separate the allele-table correction from the shared PGx confidence
  algorithm revision that shipped with it. Give each its own correction ID,
  verified dependencies and affected producers. With an old completed empty
  CYP2B6 result, exercise a sparse synthetic input for which the new caller
  changes the assessment from Insufficient to Partial and emits an alert.
  Before repair require `reanalysis_needed` with no stale guidance in the
  drug response and panel. After repair require `available` and the new
  recommendation. An unaffected unassessed control keeps its ordinary state.
  Check an unaffected caller control and verify that repairing DPYD content
  alone cannot clear a pending caller revision. Seed a matching annotated
  variant with a known coverage mask. Inject failure after the real coverage
  update and before alert storage. Verify the prior mask, findings and
  completion record after rollback. On retry verify the exact CPIC bit,
  unchanged unrelated bits and matching new alerts and completion. Include
  a previously assessed marker that the corrected scorer drops. Variant
  list, detail and coverage filters must suppress its pending CPIC bit and
  retain unrelated source bits. After retry verify the bit is cleared for
  that marker and set for the newly assessed control.
  Pair the pending marker with a verified covered marker and a genuinely
  uncovered one. Positive and negative filters and their counts must each
  return only their own verified control. The pending marker appears in
  neither. Verify the unfiltered API and the Variant Explorer tooltip
  distinguish its pending state from confirmed absence, including when it
  has no other covered source.
  Separately verify `annotation_coverage:notnull` and `:null` counts using
  a processed zero-mask row, a processed mixed verified/pending row and a
  never-annotated row.
  On both SQL paths, require one verified CPIC positive and one verified
  negative among the three processed controls, with the pending row in
  neither count. Require a raw `(annotation_coverage & 64) = 0` count
  over that population to be refused. Verify a resolved-only query can
  still use the mask and an unrelated verified source remains queryable.
  Open the drug and coverage views, then stop a full reannotation immediately
  after the production staging-table swap and before PGx runs. Without a
  reload, verify both open views invalidate their previous results. A fresh
  request and a reconnected client must agree with the new generation.
  Assert no old conclusion or missing coverage bit is served as current.
  Retry and verify the new
  variant mask, findings and receipt agree.
  Independently register the conservative diplotype selection change shipped
  in [#2643](https://github.com/bioedca/Yeliztli/pull/2643). An old alert
  can have a current allele table, direct call and Partial confidence while
  retaining a superseded prescribing basis. Exercise the four policy genes
  from the existing #2453 regressions. Preserve the observed call, withhold
  the superseded alert and confirm the new conservative basis, score-specific
  guidance and citations after repair. A fully assayed `hom_ref` control
  stays current. Neither of the earlier revisions can clear this correction.
  Register DPYD's earlier inclusion in the conservative prescribing policy
  as a separate revision of gene applicability. With the new allele-table
  content already installed, isolate an older untyped defining marker and
  one observed non-reference allele. Hold the direct call and Partial
  confidence constant. Verify the previously stored alert is withheld and
  the eligible rerun uses the reviewed conservative basis. Current coverage,
  the confidence floor and later homozygote enumeration cannot certify this
  policy revision. Include a fully assayed unaffected control.
  Separately restore a DPYD reference with phenotype-only rows and a newly
  added nullable activity-score column. Use two shipped score-specific rows
  within the same phenotype band that map to different guidance. A scorer
  completing via the legacy null-score fallback cannot clear the correction.
  After verified reference repair, require the production prescribing path to
  resolve each score to its own approved row and return its exact text.
  Include an already-current installation and an unaffected guidance key.
  Generate both SVG markers through the production annotation path. Before
  the correction, request each ID and verify its expected SVG content.
  Once the correction advances the generation, both old links must expire.
  At the new generation, request the still-stored affected ID and require
  the ordinary unavailable response. A newly issued link for the eligible
  control must return its SVG. After repair,
  verify the card for the new current result reflects the actual rerun. Force
  reuse of the old integer ID for a different eligible result. The old SVG
  link and variant-card request must remain unavailable while newly issued
  identifiers return only their matching result.
  Query the PGx evidence overlay with an affected and an unaffected alert.
  Check the exact remaining identities, statuses and source associations,
  then verify the corrected alert after repair.
  In the medication report API and rendered report, combine a pending TPMT
  assessment with an eligible routine NUDT15 effect for the same test drug.
  Require a pending drug verdict, the visible current effect and an
  incomplete flagged count. Repeat with an eligible actionable effect:
  the drug is flagged once and coverage remains incomplete. A fully
  assessed routine control stays routine. Rerunning the affected pair
  restores the verdict and counts derived from the completed results.
  Exercise the shipped joint TPMT/NUDT15 scorer with a stored standalone
  NUDT15 Intermediate alert. While the unresolved TPMT call could replace
  that advice, withhold the standalone recommendation across drug detail,
  the medication report and exports. With both fixture calls Intermediate
  and one verified joint guideline, require exactly the joint alert for
  that drug and no standalone advice after repair. An effect whose
  dependencies are independently current remains visible throughout.
  Delay central publication until after the rerun clears the sample's
  reason. The late publisher and a restart must leave no obsolete reminder,
  preserve an independent live reason and honor dismissal.
  Upgrade a stock v21 sample with its marker and a dismissed central entry
  matching every saved identity field. Across two opens expect one visible
  reminder, the local reason pending and the historical dismissal intact.
  Repeat with a restored copy having the same fields. Separately, seed a
  dismissal carrying an independently recorded copy identity from the time
  of acknowledgment. Only that copy and correction retain the dismissal.
  An active reminder and a new, independent reason each remain visible.
  Delay dismissal of the original reminder until a different correction
  occupies its place. Verify that the new correction stays visible.
  Also clear and recreate the same reason and target under a new publication
  counter. A delayed dismissal from the first occurrence must not hide it.
  Race two publishers for the same central key in opposite generation order.
  The older one must neither replace nor retract the newer reminder, even
  after retry or when the newer reminder has already been dismissed.
  Restore an older backup keeping the same registry metadata and a lower
  local counter. Its new copy identity must permit a legitimate reminder
  and reject late writes, reads and dismissals for the retired copy.
  Separately import that backup alongside its original using the real
  collision-handling path. Verify each registry entry opens its own
  findings and links, accepts its own writes and maintains its own
  reminders. Clearing a reason in one copy must leave the other intact.

### Panel wording, membership and rollback

- Exercise each of the seven panel readers with a wording-only change and a
  change that moves category, eliminates a row, introduces a row or changes
  the pathway aggregate. Cover missing or ambiguous keys and custom wording.
  Assert identifiers, counts, text and citations on both sides of each filter,
  including a fully called non-carrier for a carriage-gated finding.
  In fitness, use the two shipped `rs1815739` genotype keys that both score
  Standard and have different effect summaries. Persist a synthetic sample
  for each, correct only one genotype's wording, and assert exact returned
  pathway-detail text for both from their normalized genotype keys.
  Remove a customized finding in a successful rerun. Recover its exact
  original note through authorized history and verify that the ordinary SQL
  console, SQL export, reports and current findings cannot return its archived
  payload, including after another rerun. With the real methylation writer,
  interrupt the repair after legacy MTHFR migration and before replacement.
  Verify both panels, original notes and completion records on failure and
  retry, including a control outside the migrated set. Run both real panel
  writers in each order and repeat the scoped runs. Require identical SNP
  ownership, pathway summaries, supporting detail, citations and counts.
  Inspect a nutrigenomics-only run before invoking methylation. Require the
  eligible pathway and membership plus the explicit standalone pending
  state. After methylation runs, check the exact standalone set, including
  an owning producer that completes without a finding.
  At the nutrigenomics pathway API, verify the exact scorer recommendation
  and a nonempty expected PMID list for the called non-Standard shared SNP
  in each order. A fully called `hom_ref` fixture scored as Standard must
  return a null recommendation and an empty per-SNP PMID list in both orders.
  With the shared carried SNP eligible in both modules, require the same
  Observation in nutrigenomics-only and methylation-only FHIR delivery,
  with matching preflight counts. Selecting both returns it once. Withhold
  only the nutrigenomics membership and verify methylation's independently
  eligible membership still works. Repeat after the opposite rerun order.
  Seed one GWAS bit supported only by the panel being repaired and another
  supported by two panels. Drop that panel's contribution to both, interrupt
  publication and retry. Check stored contributions and variant API masks.
  Only the shared marker retains GWAS coverage after repair.
  Repeat through full reannotation, including the actual variant table
  swap and all module runners. Verify exact masks and producer receipts
  after the swap, after interruption and after full completion, including
  a marker whose sole current GWAS contribution comes from a panel.

### Ancestry-first reanalysis

- Restore a pre-ancestry-first fixture, rerun the prerequisite and its
  dependent modules, then open it again. Check an unaffected module, empty
  results, failed prerequisites, concurrent readers and replacement of a
  sample with the same registry ID. A stale result must never be presented as
  current through a secondary surface. Use a synthetic gout carrier whose
  stored finding text and structured odds-ratio field differ between the
  unknown-ancestry fallback and the fixture's inferred ancestry. Assert the
  exact old values before upgrade,
  withholding while pending, and the exact ancestry-specific values after
  repair and reopen. Use the ABCG2 `hom_ref` control (`GG` at rs2231142)
  and require no ABCG2 finding. For an empty panel, also set rs13129697
  to the shipped non-risk `GG` fixture. Its GRCh37 reference allele is T,
  so an all-reference sample is a different control. Tie the expected text to the same
  reviewed panel revision. Pass the retained and corrected gout values
  through the production diff path. Expect one changed finding with the
  reviewed odds-ratio change and zero additions or removals for that identity.
  For each of the other four consumers, assert the old stored fields first,
  their withholding after upgrade, and their exact expected values after
  repair and reopen. In APOL1, exercise the shipped caveat and indeterminate
  disclosure separately with an eligible synthetic ancestry input and an
  input outside the gate. Add an ancestry-eligible, fully called `hom_ref`
  APOL1 non-carrier. Verify its completed result is empty after repair and
  reopen, including the absence of an indeterminate finding.
  In cancer PRS and traits PRS, check each producer's
  ancestry caveat and, for a score with calibration support, its calibration
  method and fractions. Keep the shipped weight loaders in these cases.
  In nutrigenomics, choose an LCT fixture whose ancestry-conditional caveat
  changes and another whose result stays current. Compare with the same
  inputs scored in the supported ancestry-first order.
- For the shared PRS dependency contract, use the metabolic BMI route and
  synthetic positional weights for its PGS005198 model in the PGS store.
  Exercise the real weight loader, scorer and result store. In a two-locus
  synthetic check, one typed and one imputed contribution produced a
  calibrated finding in the executable BMI case.
  Run `python -m pytest tests/backend/test_prs_gnomad_routing.py -k 'positional_bmi or typed_bmi_calibration'`.
  Updating the fixture's frequency changed the route
  state from current to stale before rerun and its recalculated reference
  mean from 1.4 to 2.2. These values describe the fixture. Require
  `snps_used_imputed > 0` for an imputed dosage. Separately, all dosages
  can be typed while a typed locus lacks an allele needed for calibration.
  The scorer can consult imputed alleles and the standalone frequency store
  for that locus. Track those actual dependencies even when
  `snps_used_imputed == 0`. Verify the changed calibration in that case.
  With typed dosage, keep the genotype, weights and frequency fixed and
  remove the matching imputed allele pair. Both typed fixtures retain the
  synthetic raw score of 3.0. Only the incomplete-alleles fixture loses
  calibration and withholds its quantitative score. Complete typed alleles
  with their own frequency retain the 95.42 fixture percentile. Separately
  begin with an installed store whose lookup misses. Add the matching row
  in that database without altering the sample. Verify the old uncalibrated
  finding becomes stale and the rerun supplies the fixture percentile.
  Replace imputed dosages between scoring and publication without changing
  the annotated token. Reject the old publication and match a fresh run on
  the replacement dosages. In a separate case record the published
  calibration, then change a frequency used by that contribution while
  keeping both sample tokens and the upstream label fixed. Exercise both
  file replacement at the same path and a committed update to the same
  database file. Include a restoration fixture with equal filesystem
  identity metadata for two different committed contents and an unchanged
  control. Require a new content revision and stale state only for changed
  inputs. Before manual repair, inspect the GET for the affected score.
  It must report reanalysis needed and omit the obsolete percentile. Reopen
  the Metabolic page and verify that no run starts on mount or refocus.
  Choose reanalysis, observe one run request, then compare the displayed
  recalibration with a fresh run and require the expected changed value.
  Also remove and restore the store and check its documented unavailable
  outcome and recovery.
  Keep an existing sample open while importing another sample with a
  replacement shared reference. Interrupt once before the file switch,
  once after it and before registry completion, and once partway through
  notifying dependent samples. Keep the existing view open through the
  interruption and reconnection, then separately test a newly opened view.
  Interrupt also after sample registration and before the reference
  switch. Retry the same restore operation twice. It must complete with
  one imported copy and one logical reference publication. Initiate a
  second intentional import of the same fixture and require a distinct
  copy without changing the first one's association.
  Verify the content revision, one logical change per affected sample,
  the absence of the old percentile while pending, and agreement with a
  fresh run after repair. An unrelated sample keeps its current values.

### Browser caches, change notices and exports

- Starting in Report Builder, select a pending-only PGx module and a current
  module using the picker, then repeat with “Select all”. Generate the
  selected reports, including scoped FHIR. Repeat with both pending and
  current findings within PGx.
  Inspect the actual preview HTML and generated PDF text. Require the
  incomplete notice and current findings in each. Compare with a verified
  empty PGx run and a reason hidden by disclosure. Neither adds a pending
  notice, a section or a count that could disclose withheld membership.
- Warm the shipped legacy client on the drug and coverage pages before
  upgrading the backend. Retire those tabs before activating the correction.
  Reopen on the current client and verify pending results on both pages.
  An old-format request after activation receives the upgrade requirement.
  Also restore the old backend and launcher against the synthetic deployment.
  Verify they cannot open its upgraded sample or consume queued work. An
  isolated compatible backup must open under the old release without
  changing the upgraded sample's receipts, pending state or retained rows.
  Start the new worker with a pre-upgrade task lacking a verified copy
  identity still queued. Require no execution against either sample and
  no changes to their findings or receipts. A newly submitted, identified
  task runs once against the selected copy.
- Warm the drug and summary views in two tabs. Apply the correction and run
  the repair from one tab. Without reloading, verify that the other stops
  showing the old guidance, shows the pending state, and then receives only
  the eligible current response. Include an open background tab returning
  to focus, a delayed older response, and loss of the notification channel.
  On mount and reconnect, commit a correction between the first GET and
  subscription establishment. Without another navigation, verify the view
  discards the earlier generation and displays the eligible result.
- Generate diffs with the production snapshot, comparison and persistence
  paths. Open an undismissed legacy diff and its new-format equivalent,
  each containing a stale added prescribing alert and one entry whose
  eligibility can be proved. Verify the banner API returns only the eligible
  text and exact bucket counts, and that rendering the banner cannot reveal
  the old text. Cover stale before/after values, an all-withheld diff, and
  a repeated read after a successful rerun. With unchanged reference releases
  and a new analysis order, verify the banner attributes the visible change
  to the analysis or uses neutral wording. With the shipped old and new
  clopidogrel records, verify a changed-action notice for the same finding
  identity, with current guidance and no superseded advice. Include a
  wording-only control. Interrupt a scoped repair after result replacement
  and before banner publication. Restart and verify exactly one eligible
  changed-action notice from the durably saved comparison. Compare the same new finding
  against a completed empty run and a producer with no verified prior run.
  Only the completed empty baseline should yield an addition, regardless of
  other producers' findings. Check the reverse transition to an empty run.
  With no legacy receipt, test a proven old clopidogrel action or gout
  estimate on a matched row and an ambiguous legacy value. Only the
  independently proven field change may produce a notice.
  Open the actual Updates page after the last pending reason clears. Show the
  undismissed eligible diff, replace it with a later one, then deliver the
  delayed dismissal for the first. Only dismissal of the displayed
  replacement should remove it. With the same diff ID, change disclosure
  so that an additional entry becomes visible, then deliver a dismissal
  from the older view. Require the new entry to stay visible. Include an
  all-withheld control on the page. Also dismiss the visible entry *before*
  disclosure changes. Only the newly eligible entry should then appear.
  Migrate an already-dismissed legacy diff across two opens. Its banner stays
  retired, and the next eligible correction is still shown and dismissible.
- Use the same module selection and `include_all=true` on
  `GET /api/export/fhir/eligibility` and `POST /api/export/fhir`. Seed two
  synthetic carried rsIDs, one selected only by a stale finding and one by a
  current finding, plus a selected `hom_ref` control. Before repair require
  an observation count of one and exactly the current rsID in the delivered
  Observations. Rerun with a current finding for the formerly stale rsID.
  Require a count of two and the exact pair of carried rsIDs in the bundle.
  For both FHIR and PDF, preflight or preview one synthetic copy, then
  replace it before delivery. The stale selection must return no report
  or observations from either copy. Repeat the selection on the new
  copy and verify a complete result containing only its expected data.
  With one disclosable pending-only selection and a separate verified empty
  selection, require zero observations in each. Only the pending selection
  reports incompleteness in both preflight and the delivered report. A gated
  reason alone cannot change either signal. Verify that a successful empty
  rerun clears the incomplete notice.
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
