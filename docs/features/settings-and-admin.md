# Settings & admin

Open settings from the gear icon in the sidebar.

## Sample metadata

Under **General → Sample Management**, edit a sample's name, notes, collection
date, source, or custom fields. To remove an incorrect collection date, clear
**Date Collected** and choose **Save Changes**. Editing another field leaves an
untouched date unchanged.

For API clients, `PATCH /api/samples/{sample_id}` distinguishes omitted fields
from explicit JSON `null` for `date_collected`, `notes`, and `source`:

| Request value | Effect |
| --- | --- |
| Field omitted | Preserve its stored value. |
| Explicit `null` | Clear its stored value. Previously, this was a no-op. |
| Non-null value | Update the field; dates must be valid date strings. |

For example, `{"date_collected": null}` removes the date without changing notes
or source. Send dates in `YYYY-MM-DD` form. Empty-string and malformed dates
return **422**, before any rename or metadata change is saved. The date editor
sends `null` when cleared and omits untouched fields.

Empty strings for notes and source remain literal stored values; responses
continue to present those empty strings as `null`. Name and custom-field
semantics are unchanged: `null` does not update either, names must be nonblank,
and an empty `extra` object (`{}`) clears custom fields.

## Database management

- View installed reference-database versions and sizes.
- Trigger manual updates for individual databases.
- Configure the auto-update schedule (startup / daily / weekly) and an optional bandwidth
  window for large downloads.
- View the update-history log.

See [reference data](../install/reference-data.md) and [updating](../install/updating.md).

## System health

An admin panel showing:

- **Log explorer** — search and filter structured application logs. Log entries may include
  local sample paths and analysis metadata such as variant identifiers, gene symbols,
  coordinates, and error details. Current versions redact structured genotype-like fields from
  new logs, but older entries may contain them; review logs before sharing from a shared or
  support workflow.
- **Database stats** — row counts, file sizes, and last-modified dates.
- **Disk usage** — storage broken down by database and sample.
- **Database health** — each database's state (Ready / Downloading / Building / Partial /
  Corrupt / Failed / Not installed) with an integrity check that confirms it's actually
  readable by the annotation engine. For one that needs attention you can **Resume** an
  interrupted download, **Verify** it with a deep integrity check, or **Clean** a
  partial/corrupt artifact so it can be re-downloaded.

## Authentication

Optionally protect your instance with a PIN or password:

1. Go to **Settings → Authentication**.
2. Enable authentication and set a PIN/password (stored only as a salted bcrypt hash).
3. Sessions expire after a period of inactivity. Once a password exists, API routes require
   a valid session except for health, login, and auth-status endpoints; first-run setup
   endpoints remain reachable only before a password has been configured.
4. If you forget the PIN/password, there is no in-app reset. Recover as the local owner by
   disabling auth and clearing the saved password hash in `~/.yeliztli/config.toml`,
   restarting Yeliztli, and setting a new PIN/password; see
   [Troubleshooting](../install/troubleshooting.md#recover-from-a-forgotten-pinpassword).

## Theme

Switch between **Light**, **Dark**, and **System** (follows your OS) from the top navigation
or Settings.

## Backup & restore

Export a `.tar.gz` of your samples and configuration, and restore it later — see
[backup & restore](../install/backup-restore.md).
