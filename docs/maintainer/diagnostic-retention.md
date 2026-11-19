# What an application diagnostic may retain

**Proposed policy for #2710 and #2459.** An operator needs to identify the
failed operation and an appropriate next step without exposing the person's
inputs or analysis results. Apply one content policy before the application
writes to its database or renders the API and worker consoles. Normal
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

| Input | Proposed database and default-console behavior |
| --- | --- |
| Approved application event | Select its static template from a versioned event catalog. Render only that event's explicitly typed, validated fields. A valid template can preserve the full approved explanation. |
| Free text, exception message, interpolated argument, unknown event or third-party message | Use a static fallback with approved context. Arbitrary strings cannot acquire permission through a familiar event name, a different log level or a caller's claim that they are safe. |
| Exception or exception chain | Retain an approved class or reason category and, when needed, a bounded list of reviewed application frame identifiers. Map unknown or dynamically named classes to a generic category. Exclude stringification, messages and notes, locals, paths, source lines and unreviewed chained context. |
| Counts, timing, status, component and operation | Retain only when explicitly allowed for that event and safe under its disclosure policy. Reject invalid types, unexpected nested keys, unbounded values and user-controlled identifiers. A public-looking gene, filename or count can still disclose a private association. |
| Withheld or restricted analysis | Preserve the stricter existing neutral-event rule. No site, gene, drug, sample identity or result-specific count may be reconstructed through text, error type, component, stack frames, filters or aggregate counts. |
| Legacy and independently managed output | Keep an explicit new presentation-policy revision. Query only eligible rows before filtering, counting or pagination. Never relabel old serialized text as newly verified. Existing database rows, backups and service logs have their own retention lifecycle. |

Choose a **structured allowlist plus reviewed templates**. Trying to remove
recognizable sensitive patterns from arbitrary text cannot establish that
the remainder is safe. Omitting every message would also discard approved
instructions that distinguish a retryable problem from an installation
problem. A permissive developer console would put raw values back into
durable service logs. This resolves the conflicting acceptance language in
#2459: preserve complete explanations for known safe cases and a useful
category for unknown ones. Arbitrary apparently harmless exception strings
need explicit approval before becoming durable output.

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

Register trusted producers, event IDs, approved templates, field validators
and ownership in application code. A free-form third-party message equal to
template text is still a free-form message. For standard-library call sites,
migrate useful operator messages to fixed templates with explicitly typed
arguments. Messages already formatted before the bridge need the fallback
unless carried through a trusted structured producer. Names and basenames
from untrusted paths are not generic “safe” strings. If an operator needs to
know the storage area, supply a reviewed category such as “application cache.”

Coordinate the writer revision and the database/API read policy, including
existing installations, workers started without the API, older schemas,
mixed-version processes, and restored backups. A process unable to attest
the current policy cannot create patient-presentable entries. A missing,
old or unrecognized marker must withhold old content from search, result
counts and excerpts. Keep the current log schema compatible where possible
and leave underlying historical records intact. Any deletion or retention
period needs its own authorized decision. Update the user-facing privacy
documentation when implementation ships so it states exactly which output
paths the application controls.

## Synthetic acceptance cases

Exercise native structured logging and standard-library logging through the
actual API and worker configuration, a disposable database, the Log Explorer
and captured console output.

1. Trigger two distinct approved failure categories and an unknown exception.
   Assert exact helpful messages and allowed fields for the known cases.
   Require a nonempty generic event for the unknown case.
2. Put a synthetic opaque sentinel in free text, a formatting argument,
   nested metadata, path, chained exception and diagnostic object. Assert it
   is absent from stored message and JSON, console, explorer search and
   counts. Verify stable reason and component still reach both sinks.
3. For the existing restricted-event fixture, require its neutral output on
   both routes even when a normally approved template or category is supplied.
   Check normal events remain discoverable. Invalid fields, malformed records
   and failures in logging itself cannot cause fallback to the original text.
4. Verify repeated configuration produces one event, worker-only bootstrap
   uses the same policy, and a restored legacy row cannot be exposed by a
   search match or count. Preserve existing noncreation behavior when the
   database was removed. Use synthetic inputs throughout.

Approval is requested for the common content policy, the safe-template
exception and the treatment of historical rows before #2459 changes logging.
