# What an application diagnostic may retain

**Proposed policy for #2710 and #2459.** An operator needs to identify the
failed operation and an appropriate next step without exposing the person's
inputs or analysis results. Apply one content policy before writing an
operator log entry to `log_entries` or rendering the shipped API and
worker consoles. The database scope includes presentation through the Log
Explorer. Product job-status records, API error responses and support-file
contents have separate contracts and require their own review. The same
rules apply to the API and worker in development. `make dev` also runs a
frontend development server. Its combined terminal transcript is outside
this guarantee. For a support report, capture the API or worker's approved
output separately, before it is combined with other developer tools.

## What an operator sees

For a storage operation that could not acquire a lock, an approved event can
render **“The database is busy. Retry the operation.”** Alongside it, expose
the severity, stable component and operation, a bounded retry count and an
approved error category. If the application has no reviewed explanatory
template, a generic **“Operation failed”** event with a known component and
error category still gives the operator a place to start. Suppress unknown
fields independently so a valid diagnostic remains useful.
Each template declares required and optional fields. An invalid required
reason selects the generic failure template. Drop an invalid optional
retry count while preserving the valid explanation and safe fields.

| Input | Proposed database and default-console behavior |
| --- | --- |
| Approved application event | Select its static template from a versioned event catalog. Render only that event's explicitly typed, validated fields. A valid template can preserve the full approved explanation. |
| Free text, exception message, interpolated argument, unknown event or third-party message | Use a static fallback with approved context. Arbitrary strings cannot acquire permission through a familiar event name, a different log level or a caller's claim that they are safe. |
| Exception, chain or exception group | Retain an approved class or reason category and, when needed, a bounded list of reviewed application frame identifiers. Map unknown or dynamically named classes to a generic category. Exclude stringification, messages, notes, locals, paths, source lines and unreviewed context from every member. |
| Counts, timing, status, component and operation | Retain only when explicitly allowed for that event and safe under its disclosure policy. Reject invalid types, unexpected nested keys, unbounded values and user-controlled identifiers. A public-looking gene, filename or count can still disclose a private association. |
| Withheld or restricted analysis | Suppress individual result events at both sinks. If an operator needs confirmation that an operation ran, its trusted producer may emit one neutral event per operation under the same rule for zero, one or many restricted results. Apply that rule to retries, severities and summary fields as well as message text. |
| Legacy and independently managed output | Keep an explicit new presentation-policy revision. Query only eligible rows before filtering, counting or pagination. Never relabel old serialized text as newly verified. Existing database rows, backups and service logs have their own retention lifecycle. |

Apply the rules in order. Suppress result-dependent events at their
producer. For every remaining routed record, apply the existing
restricted-content rule before catalog lookup, template rendering or
generic fallback, regardless of level or whether it carries an exception.
Classify only safe primitive inputs and trusted context. Avoid executing
methods on caller-supplied objects or exceptions. If safe inspection
cannot establish that restricted context is absent, use the stricter
neutral form. An ordinary held record or one independently
occurring operational failure whose diagnostic is restricted produces one
`clinical_guidance_withheld` record containing only its approved neutral
marker, with the current policy revision. Keep
`test_configured_logging_redacts_held_exception_text` unchanged as its
regression. Enforce the separate result-count rule at the producer, before
it calls the logger for a result. A result-dependent occurrence cannot
become an independent operational failure just by using an exception API.
The zero/one/many test covers the producer and the existing test covers
the safe representation of a failure that actually occurred.
Preserve the existing response-refusal audit as a separate approved event.
Its trusted producer accepts only the final disposition and reviewed
operation. Construct and route this event without importing ambient
request context at any stage. An ordinary logger cannot select that
route by supplying the event name. A refused response emits one
`patient_response_payload_withheld` event with its reviewed operation
context. A presentable response emits none.
This records the disposition of the request. Holding that disposition
fixed, the number of contributing results and internal checks must not
change the audit. Preserve the product response's own `withheld` status.
For both native and standard-library calls, exercise an ordinary held
record as well as a held exception. Inspect every new
database row from the isolated operation and both console streams. Require
the single expected neutral record and only the approved console diagnostic.

Choose a **structured allowlist plus reviewed templates**. Trying to remove
recognizable sensitive patterns from arbitrary text cannot establish that
the remainder is safe. Omitting every message would also discard approved
instructions that distinguish a retryable problem from an installation
problem. A permissive developer console would put raw values back into
durable service logs. Under the proposed resolution of
[issue #2459](https://github.com/bioedca/Yeliztli/issues/2459), known
events retain their approved explanations and unknown events receive a
safe category. Only reviewed templates can supply durable explanatory text.

## Boundaries and migration

At `cf08f712a130591598eae00648a1b8dce2d0b384`, #2679 already supplies
structured-field protection, held-event handling and a versioned Log Explorer
filter. `backend/logging_config.py` configures both native structured logging
and the standard-library bridge. The database stores the rendered message
and structured extras; the console has a separate rendering path. An
implementation must classify the event **before** interpolating a message,
rendering an exception or serializing an arbitrary object for either sink.
Keep the strict final check at each output boundary. The logging operation
must not raise into the application or print the rejected input on its
own error path.

The console guarantee covers the shipped API and worker entrypoints and
all handlers they configure, including the server's access and error
handlers and the queue consumer's own handler.
Pass a safe event to every application-controlled destination before any
handler formats it. An additional handler attached by a host may receive
only the safe form of application events, or propagation must stop before
reaching it. Keep other applications' logging configuration intact. Direct
output by independently launched tools and historical service logs are
outside this policy. Document those boundaries in operator guidance.
For processes shipped by the application, only the approved diagnostic
channel may reach the operator console. This includes log records, direct
stdout or stderr writes and output inherited or forwarded from helpers
launched by the API or worker. Discard unclassified diagnostic bytes
without persisting them. A genuine operational failure can still produce
its separately classified diagnostic.
Keep the existing routing to sinks. A console-only framework event stays
console-only. A safe fallback changes the content of a routed event without
creating a new reason to retain it in the database.
Cover the entire lifetime of each shipped process. A startup failure before
normal logging is configured still needs a static operator explanation and
a nonzero exit. An error in the fallback itself must not print the rejected
input or formatted traceback.

Register trusted producers, event IDs, approved templates, field validators
and ownership in application code. Route approved events through their
registered producer's typed interface. Treat a claimed producer field,
logger name or event ID on an ordinary record as untrusted input.
A free-form third-party message equal to template text is still a free-form
message. At application-owned call sites, pass typed arguments or the
exception itself to the approved interface. Do not format an object to
prepare a diagnostic. This preserves the caller's normal recovery even
when formatting that object could raise. Already-rendered third-party
messages take the fallback. Names and basenames
from untrusted paths are not generic “safe” strings. If an operator needs to
know the storage area, supply a reviewed category such as “application cache.”

Coordinate the writer revision and the database/API read policy, including
existing installations, workers started without the API, older schemas,
mixed-version processes, and restored backups. At cutover, stop serving the
Log Explorer until every serving API reader enforces the new revision.
Drain old readers and workers and check each replacement before reopening
access. Enforce admission on every managed start, restart and rollback.
The authority controlling access to the active data and queue must survive
replacement of the release, launcher and its configuration. Where a
deployment cannot provide that guarantee, support rollback only into an
isolated environment with no access to the active service, data or queue.
A reader must qualify before serving requests and a worker before consuming
tasks or emitting task diagnostics.
Older binaries cannot grant themselves admission. A process unable to attest
the current policy cannot create patient-presentable entries. A missing,
old or unrecognized marker must withhold old content from search, result
counts and excerpts. Keep the current log schema compatible where possible
and leave underlying historical records intact. Any deletion or retention
period needs its own authorized decision. Update the user-facing privacy
documentation when implementation ships so it states exactly which output
paths the application controls.

Manual launches require an operator cutover. Stop all old API and worker
processes, including independently started ones. Disable their restart
commands and point the supported manual launch commands at the verified
release before resuming service. Keep older releases away from the active
data directory and task queue. If the operator cannot establish which
processes have access, the new policy is not yet active. A rollback uses
an isolated compatible environment and requires its own explicit policy
decision before it can serve operator logs again.
Before reopening the Explorer, retire the old browser sessions used for
the application, including inactive tabs, cached queries and restorable
history documents. Start the replacement in a fresh browsing context
with no history or session state from the old application. Keep retired
sessions out of service. If the operator cannot verify this transition,
keep the Explorer closed and the cutover pending. The new client accepts
responses only for the verified current revision, including after a
delayed reply. Previously exported information has its own retention
lifecycle.

## Synthetic acceptance cases

Run these cases against the shipped API and worker configuration, using
only synthetic inputs, a disposable database, the Log Explorer and both
console streams. Exercise native structured logging and its standard-library
counterpart wherever either can supply the event. For each isolated operation,
assert the complete approved event, the number of deliveries to each intended
destination, and the absence of test markers from every column of every new
row, console output and Explorer results. Compare variable service metadata
against its approved type and range. Preserve a known, useful diagnostic
as a positive control.

### Event identity, fields and exceptions

| Synthetic input | Required observation |
| --- | --- |
| Two approved failures and an unknown exception | Each approved failure has its distinct explanation and exact allowed fields. The unknown failure has a nonempty static fallback. |
| Registered event ID and valid fields through the typed producer, then through an ordinary record | The producer gets its template. The ordinary record on the same route gets the fallback. A console-only third-party message reaches only its console. |
| Independent registered failure with extra fields, supplied directly or through bound request context | For benign unapproved extras, retain the approved explanation and fields and drop the extra keys and values. For a synthetic restricted extra, emit only the neutral diagnostic. Exercise both inputs through each context source independently. |
| Invalid required reason, then a valid reason with an out-of-range optional count | The first gets “Operation failed” and the approved component. The second preserves its explanation and drops the count. |
| Ordinary exception logged with a fixed message and distinct harmless markers in the exception text, raising file and source line | Both exception paths keep the approved category and exactly the reviewed frame identifiers, if any. All three markers are absent. |
| Inspectable, nonrestricted primitive text in a free-form message, nested metadata and a path | The fallback contains the approved reason and component. Searching or filtering for the test value returns zero rows and a zero total. |
| Opaque arguments, logger names and exceptions with observable formatting methods | When restricted content cannot be excluded, emit only the neutral marker, with zero formatting calls. No reason or component comes from the input. An Explorer component filter still finds the approved positive control. |
| A restricted ordinary record, a held exception, a cause/context chain and a nested exception group with a benign outer message and one held member | Each independently occurring operational event gets exactly one neutral diagnostic on its configured route. No member text is rendered. Exercise each exception shape independently. |

Exercise an actual application producer with a synthetic failure whose
formatting method records calls and raises. Require zero calls, the
expected safe diagnostic and the producer's normal recovery outcome.

### Restricted results and request audits

Run one operation with zero, one and several restricted results, holding
public inputs and retries fixed. Observe calls where the real producer
would submit a result to its logger. Require zero per-result calls in each
case, including for a registered event. Separately compare all downstream
event counts and approved content, including Explorer filters, totals and
pages. Any permitted operation-level event has the same count in all runs.

Run both the presentable and refused request fixtures and assert the product
response status. A presentable detail response with an eligible item and one
or several omitted items returns that eligible item and produces zero refusal
audits. A refused request produces one neutral refusal audit with exactly its
approved operation context, whether one or several items were withheld.
Repeat both fixtures with synthetic restricted request context. The
presentable case still produces zero refusal audits. The refused case
still produces exactly one with the same approved fields and no extra
marker. On the real cancer PRS list route, assert the final withheld
response and its single audit. In a separate controlled case, run the
same response check twice within one request and finalize it once.
Require two refused checks and one audit. Production routes may
short-circuit as soon as they know the final disposition.

### Process output and routing

- Start the shipped worker on its own. Run a harmless failing task with
  its consumer handler enabled. Require the expected safe category. Have
  a test dependency write distinctive harmless bytes directly to stdout
  and stderr. Neither stream nor the database may retain those bytes.
  Separately exercise a worker task that collects a helper's output and
  reports a failure. Use a synthetic helper that emits a marker and exits
  unsuccessfully. Require the task's failure outcome and approved category.
  Neither forwarded nor inherited output may expose the marker in the
  worker console, new log rows or Explorer.
- Attach an observing standard-library handler. Inspect what reaches it
  before formatting. Require zero delivery or solely the approved record,
  including arguments, exception state and extras. Verify repeated
  configuration emits each diagnostic once. When the log database has
  been removed, logging must leave it absent. A failure in logging itself
  must preserve the same content rule.
- Start the shipped API process. Exercise a real Explorer search and an
  isolated synthetic application exception. Require the approved access
  status and error category on their intended routes without the distinct
  query or exception markers. In the supported developer reload mode,
  trigger one reload and send a request to the new child. Check the
  supervisor and both children for safe lifecycle and request output.
  The fixture's watched path and filename remain private.
- Exercise the API's report-generation path with a synthetic child that
  emits distinct harmless markers to stdout and stderr. Verify the child
  ran and the expected report outcome was delivered. Neither marker may
  reach the API console or durable diagnostics.
- Cause a synthetic failure before normal logging starts in each shipped
  entrypoint. Require a nonzero exit and the approved startup explanation.
  Apply the same output assertions to initialization and shutdown.

### Upgrade and restore

- Begin with an old API reader still serving. Require the Explorer to
  stay closed until only verified replacement readers can serve it.
  Seed a historical row and a valid current-revision control. Check both
  sides of search, filters, totals and pagination. Restoring an older
  backup or restarting an old reader must never re-expose the old row.
- Warm two filtered Explorer views in the old application. Leave one
  visible and navigate away from the other, including to a different
  document. Require cutover to stay pending while either old document is
  restorable. In the new session, check first paint, loading states, Back
  navigation and a delayed reply from the prior revision. No navigation
  can restore an old document. Only eligible rows and totals may appear.
- Queue a harmless task and exercise both an old worker under the new
  launcher and a full rollback of the API, worker and launch configuration,
  including the image and service definition for a Compose deployment. Neither old
  process may serve the active Explorer, access its data or consume the
  queued task. Check the supported isolated rollback where external
  admission is unavailable. The task stays queued without task output
  until a compatible worker runs it once and emits its approved diagnostic.
  Repeat the cutover with manually launched API and worker processes.
  Only replacements may serve the active data and consume the queued task.

Approval is requested for the common content policy, the safe-template
exception and the treatment of historical rows before #2459 changes logging.
