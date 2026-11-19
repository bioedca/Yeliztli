# What an application diagnostic may retain

**Proposed policy for #2710 and #2459.** An operator needs to identify the
failed operation and an appropriate next step without exposing the person's
inputs or analysis results. Apply one content policy before writing an
operator log entry to `log_entries` or rendering the shipped API and
worker consoles. The database scope includes presentation through the Log
Explorer. Product job-status records, API error responses and support-file
contents have separate contracts and require their own review. Normal
developer output can be captured by a service manager or included in a
support report, so the same content rules apply there.

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
| Exception or exception chain | Retain an approved class or reason category and, when needed, a bounded list of reviewed application frame identifiers. Map unknown or dynamically named classes to a generic category. Exclude stringification, messages and notes, locals, paths, source lines and unreviewed chained context. |
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
A refused response emits one `patient_response_payload_withheld` event
with its reviewed operation context. A presentable response emits none.
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
channel may reach the operator console. Contain unclassified writes to
stdout or stderr by in-process dependencies as well as log records.
Discard those bytes without persisting them. A genuine operational failure
can still produce its separately classified diagnostic.
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
A free-form third-party message equal to
template text is still a free-form message. For standard-library call sites,
migrate useful operator messages to fixed templates with explicitly typed
arguments. Messages already formatted before the bridge need the fallback
unless carried through a trusted structured producer. Names and basenames
from untrusted paths are not generic “safe” strings. If an operator needs to
know the storage area, supply a reviewed category such as “application cache.”

Coordinate the writer revision and the database/API read policy, including
existing installations, workers started without the API, older schemas,
mixed-version processes, and restored backups. At cutover, stop serving the
Log Explorer until every serving API reader enforces the new revision.
Drain old readers and workers and check each replacement before reopening
access. Apply this admission rule at the managed service entrypoint on
every start, restart and rollback. A reader must qualify before serving
requests and a worker before consuming tasks or emitting task diagnostics.
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
At cutover, retire the old Explorer revision across the entire client,
including inactive views and cached filter queries. Clear its rows, totals
and filter selections. Only a response for the verified current revision
may populate or render a view, even when an older request completes late.
A client that cannot establish the current revision must reopen the Explorer.
Previously exported information has its own retention lifecycle.

## Synthetic acceptance cases

Exercise native structured logging and standard-library logging through the
actual API and worker configuration, a disposable database, the Log Explorer
and captured console output.

1. Trigger two distinct approved failure categories and an unknown exception.
   Assert exact helpful messages and allowed fields for the known cases.
   Require a nonempty generic event for the unknown case.
   Send the same registered event ID and valid fields through its authorized
   interface and an ordinary record on the existing database-and-console
   application route. Require the approved template for the former and
   the safe fallback for the latter at both sinks. A console-only
   third-party record gets the fallback on its console and produces no
   database entry. Send an out-of-catalog required reason through the
   trusted interface and require “Operation failed” with its approved
   component. With a valid reason and an out-of-range optional retry count,
   require the approved explanation at both sinks and no count.
   For an ordinary nonrestricted failure, use a fixed log message and
   distinct harmless markers for the exception text, raising file and
   source line. Run the native and standard-library exception paths
   separately. Require the approved category and the exact reviewed frame
   identifiers, if any. None of the markers may appear in any column of
   any new row, the Explorer or either console stream.
2. With safely inspectable, nonrestricted primitive text, put an unapproved
   test value in the message, nested metadata and path. Require the approved
   fallback reason and component at both sinks, and no test value in stored
   fields, the console, Explorer search or counts. Separately supply an
   opaque argument whose formatting methods record calls. When safe
   inspection cannot exclude restricted context, require exactly the neutral
   marker with zero formatting calls and no reason or component derived
   from the input. Repeat with an opaque exception, including in a chain.
   Exercise native and standard-library records. Also put an opaque test
   value in a supplied logger name. Inspect all database columns, including
   the component, and both streams. An Explorer component filter for the
   test value returns zero rows and a zero total, while a registered
   component still returns its approved event.
3. Run the same operation with zero, one and several synthetic restricted
   results. Hold public inputs and retry count fixed. Compare emitted event
   counts and approved content across the database, captured console and
   Explorer filters, totals and pages. An allowed template cannot override
   suppression. A separate normal event remains discoverable. For the
   response audit, run the real presentable and refused request fixtures.
   Verify their distinct response statuses and zero versus one neutral
   audit event. A refusal involving several held results has the same
   approved audit as a refusal involving one.
   Also request a presentable detail response that omits one restricted
   item, then several. Verify the eligible item and presentable status in
   both responses. Neither operation emits a request-refusal event to
   either sink. Only the final disposition can trigger that audit.
4. Verify repeated configuration produces one event, worker-only bootstrap
   uses the same policy, and a restored legacy row cannot be exposed by a
   search match or count. Preserve existing noncreation behavior when the
   database was removed. Exercise a harmless failing task in the shipped
   worker process with the consumer handler enabled. Capture its complete
   console and verify the safe category also reaches the expected sink.
   Have a test dependency write benign distinctive bytes directly to the
   worker's stderr. They must appear in neither captured service output
   nor the database, while the approved task diagnostic remains visible.
   With an additional standard-library handler, inspect each record at
   delivery, before formatting. Require either zero deliveries or exactly
   the approved content, including arguments, exception state and extras.
   Check the expected event count at each sink and apply the same content
   rule to failures in logging itself.
   Start the shipped API server as a process as well. Exercise an ordinary
   Log Explorer search with a synthetic query value for access logging.
   Separately inject a harmless application exception in an isolated test
   process so the server's error handler runs. Use distinct test values and
   verify the approved access status and error category both appear at
   their intended sinks. Capture complete stdout and stderr and check that
   neither unapproved value appears there or in durable diagnostics.
   Exercise the supported developer reload launch as well. Use an isolated
   fixture directory, trigger one reload, and send a request after the new
   child starts. Check the supervisor and both children together for the
   expected safe lifecycle and request diagnostics. The synthetic watched
   path and changed filename stay out of both console streams.
   Finally, inject a synthetic initialization failure in each shipped
   entrypoint before normal logging starts. Require a nonzero exit and the
   approved startup explanation, without the test input or local path in
   either console stream.
5. Start with an old reader and writer and a synthetic historical row. Test
   cutover with that reader still running. No client may reach it once the
   new policy becomes active. The new reader returns only eligible entries
   and their counts. Restoring the fixture or restarting an old process
   must not reopen the old presentation path. Warm two filtered Explorer
   views. Leave one visible and navigate away from the other. Across
   cutover, first paint on return, and a delayed old response, require
   only current-revision rows and totals, even while loading. Queue a
   harmless synthetic task and attempt to restart an old worker under the
   managed service.
   Require the task to remain unconsumed and no task output to be emitted
   until a compatible worker starts. Then require one completed task and
   only its approved diagnostic.
   Exercise the manual cutover separately with the API and worker launched
   directly. Verify only the replacement processes can serve the active
   data directory and consume its queued synthetic task.

Approval is requested for the common content policy, the safe-template
exception and the treatment of historical rows before #2459 changes logging.
