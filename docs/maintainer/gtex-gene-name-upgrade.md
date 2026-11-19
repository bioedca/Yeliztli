# Upgrading GTEx v8 installations with gene names

Use content readiness to offer an explicit rebuild of a legacy installation. Build a complete replacement privately and publish it through the existing standalone database publisher. Keep the reference release string exactly `v8`. The Health, setup and repair behavior below is proposed for the implementation ticket. [Upgrade feature #2427](https://github.com/bioedca/Yeliztli/issues/2427)

## What existing users see

A legacy installation can still return tissue associations and a top gene ID. Variant Detail reads the name from the installed database on each request. An individual gene without a mapped symbol can continue to use its labelled Ensembl ID.

At revision `4e6788ef`, nine synthetic installs exercised `get_database_health`, `get_database_status` and the Variant Detail badge reader. A missing or empty name table produced a badge with a null symbol, while a table missing the `gene_name` column suppressed the entire badge. All three stamped databases appeared ready and downloaded. An empty association table produced a fourth discrepancy. Health reported corruption while setup still reported the file as downloaded. The setup list and its worker both use this downloaded flag to skip builds.

## Readiness and recovery for #2427

Health, setup and the worker's check inside the build claim must agree on content readiness. Inspect the published association table's consumer columns and nonempty content first. Then check the name table for readable `gene_id` and `gene_name` fields and at least one usable mapping under the loader's rules. A particular association can lack a symbol in a ready installation. The name check needs no join or scan over the full association table. A table containing only unusable names may require reading the name map to establish that result.

| Installed state | Proposed Health / setup downloaded | What the user can do |
| --- | --- | --- |
| No database, including a leftover stamp | `not_installed` / false, or the existing failed-job state | Install explicitly. Listing databases leaves the file absent. |
| Core file unreadable, empty or missing required association content | `corrupt` / false | Show integrity failure and offer a staged replacement. Variant Detail withholds unavailable associations. |
| Usable core, missing release stamp | `partial` / false | Offer staged rebuild. Preserve readable data. Only a successful publication establishes release provenance. |
| Stamped v8, usable core, name table missing or empty | `partial` / false | Explain that gene names are available through rebuild. Keep serving gene IDs until publication. |
| Stamped v8, usable core, malformed name table or no usable name rows | `partial` / false | Identify the name-map fault and offer the same rebuild. Limit the fallback to names so valid associations remain visible. |
| Stamped v8, usable core and name map, including an unmapped individual gene | `ready` / true | Show a symbol when the exact stored gene ID matches. Use the labelled ID for each unmatched gene. |
| Build in progress | Existing `building` progress and job identity | Deduplicate a second request. Readers continue to use the published artifact during staging. |
| Retry after build or publication failure | Re-evaluate the installed artifact after recovery | Report the job failure and offer another complete staged build. A previously usable install remains available. |

The repair action for a usable database must submit the optional GTEx build directly. System Health currently derives `can_clean` from `partial`, and the clean endpoint checks the state again independently. Suppress Clean for usable legacy data in the UI **and** refuse its removal in the endpoint under the build claim. Verify should explain which component needs repair. Opening Health or visiting a sample must never start a multi-gigabyte transfer.

Validate the **candidate** more strictly than an installed legacy file. Both the core and the name-map checks must pass, along with the standalone publisher's deep SQLite check, before replacing anything. On ordinary failure before the version commit, preserve the previous file and stamp. A process death around rename or commit requires the publisher's existing digest-bound journal and startup recovery. Readers must wait at the publication boundary. The GTEx staging database is transient, so retry can repeat the full ingest. [Standalone publication and recovery](../install/reference-data.md#rebuilding-standalone-databases)

## Costs and alternatives

| Option | Inputs, resources and compatibility |
| --- | --- |
| Content readiness and private rebuild | Reuse the existing publisher for every supported legacy layout. Budget for the installed database plus a replacement, extracted inputs and downloads. The loader estimates about 1.6 GB for the tar and 0.8 GB for the lookup. The issue reports a 9.6 GB installation, while the registry's 3 GB figure is an estimate. Use actual free space and installed file size when presenting costs. A missing source or failed download leaves the old file usable. |
| Transactional name-only backfill | With complete release-matched egenes inputs this could avoid reloading all tissue pairs and a second full database. The existing loader parses names before replacing rows, but schema creation is separate and the loader accepts a nonempty subset of files. A supported live backfill would also need input completeness and identity checks, a writer claim, a transaction covering schema and rows, safe readers, and interruption and retry coverage. Absent, incomplete or unverified inputs require another path. |
| Separate source and loader versions across the application | An independent schema-capability field could serve future sources. Extending the public snapshot and migrating historical samples costs more than inspecting this one optional artifact. A global suffix rule could suppress meaningful changes for other databases. Keep exact per-source comparisons. |

Retained egenes files alone do not authorize a backfill. The selected path uses the full v8 build in both the retained and absent cases. The downloader requests the tar and lookup and can resume an eligible partial transfer. The extractor already clears the previous extraction before unpacking the new tar. Preserve this behavior so leftover tissue or name files cannot mask an incomplete archive. With insufficient space, unavailable sources or an incomplete release, show a retryable error and preserve the old artifact. Defer any offline reuse of completed inputs until their provenance and completeness can be verified.

## Acceptance

The synthetic upgrade test runs the existing standalone builder with a local fixture archive in place of the network download. It begins with two usable associations and a pre-name-table v8 file. The same retained reader sees both original association records and the added names after publication. The recorded sample remains current before and after, while a deliberately distinct reference release produces the exact expected staleness record. Existing publisher tests also cover interruption before and after the version commit. [Upgrade test](https://github.com/bioedca/Yeliztli/blob/main/tests/backend/test_gtex_eqtl.py) · [Publisher tests](https://github.com/bioedca/Yeliztli/blob/main/tests/backend/test_standalone_rebuild.py)

For #2427, exercise each table row through Health and setup, Verify, direct Clean, the setup worker and Variant Detail. Cover missing columns and placeholders, a valid unmatched gene, retained, missing and stale extracted inputs, a full disk during staging, interrupted publication and a successful retry. Assert the file and recorded release at each boundary, the absence of a GTEx re-annotation prompt for a recorded `v8` sample, and a positive staleness signal for a genuinely different source version.
