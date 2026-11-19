# Proposed MONDO/HPO refresh baseline

The current loader refuses a disease-scope match rate below 40%. This proposal for #2500 adds a comparison with the installed release and a check for complete loss of a previously matched namespace. The choice affects which reference refreshes an operator can install. This page records the evidence for that decision. [Issue #2707](https://github.com/bioedca/Yeliztli/issues/2707)

## What the releases showed

Fourteen dated public release triplets produced intact match rates of **58.80% to 87.81%**. Across all 36 earlier-to-later combinations of the first nine releases, the largest intact drop was **12.6506 percentage points**, from September to November 2025. Choosing the smallest whole number strictly above that maximum gives **13 points**. This rule preserves every measured calibration transition, including skipped releases. It offers only 0.3494 points of margin on the worst observed transition.

We then compared each of the five later intact releases or its damaged counterpart to every earlier intact installation. The table shows how often a candidate was refused in these 55 comparisons. The last column adds a refusal if any namespace contributing at least one installed match contributes zero candidate matches. All three rules retain the current absolute floor.

| Candidate | 40% floor | Add 13-point drop | Also check namespace loss |
| --- | ---: | ---: | ---: |
| Intact | 0/55 | 0/55 | 0/55 |
| Remove OMIM mappings | 55/55 | 55/55 | 55/55 |
| Remove Orphanet mappings | 0/55 | 5/55 | 55/55 |
| Keep about half of Orphanet mappings | 0/55 | 0/55 | 0/55 |
| Keep about 75% of all mappings | 0/55 | 38/55 | 38/55 |
| Keep about 90% of all mappings | 0/55 | 4/55 | 4/55 |

For just the five **adjacent** held-out upgrades, the 13-point check caught **zero of five** complete Orphanet losses. Adding the namespace check caught five of five. Of the same five candidates retaining about 75% of mappings, both combined rules caught four. November 2026's intact rate rose from 64.82% to 87.81%. Its 75%-retention candidate still matched 64.65%, close to October's intact rate. An improving source can conceal substantial damage. A competent release that retires or renames a whole namespace could also trigger the proposed guard. Repeated smaller losses across successive accepted installations can accumulate. A namespace cutoff for *partial* loss needs separate calibration.

The experiment covers the scope-rate gates. Other loader guards, including the minimum mapping count, run independently in production. Source releases share data, and this sample offers five holdout dates. The intact designation means unmodified upstream files. Semantic coverage and clinical accuracy require other evidence. The [evidence packet](https://github.com/bioedca/Yeliztli/tree/main/data/science-evidence/2026-11-19-mondo-hpo-refresh-2707) contains the source register, scripts, five overlapping replications of #2285, 84 measured cases and all comparator results.

## Proposed decision

Retain the 40% floor and refuse a decline **greater than 13 percentage points** versus the committed predecessor. Include the complete-namespace check for previously positive matches. Store per-namespace matched and unmatched counts so an operator can see what changed. Show the old and new counts, bundle identities and reason on refusal. The first install or a verified legacy installation uses only the absolute floor among these three checks.

For matched counts `a` and `b` in positive totals `A` (installed) and `B` (candidate), the relative check refuses when `100 * (a*B - b*A) > 13*A*B`. Use exact integers. Exactly 13 points passes. The absolute check refuses when `100*b < 40*B`, so exactly 40% passes its own gate. A zero denominator or malformed count fails validation. Decide each gate separately, including on equal rates.

## Atomic storage and recovery

Use the **reference SQLite database** already holding `gene_phenotype` and `database_versions`. Add a proposed source-specific baseline table with a unique installation key (the three-file bundle identity plus measurement-format revision), nonnegative integer totals and per-namespace counts with the same installation key. Retain all three input digests, the current composite installer version and measurement-format revision. Use the loader's existing `_source_bundle_id(sources)` and `_mondo_hpo_install_version`. The primary KG checksum alone cannot distinguish two installations that differ only in HPO or MONDO. `reference_metadata` names a SQLAlchemy metadata collection in this repository.

Record the active baseline key or an explicit migration-issued legacy state on the installed MONDO/HPO version row. Backfill the legacy state only for an existing installation the migration positively identifies. Fresh schema initializes empty. Validate installed version, rows, key, counts, namespace sums, format and bundle identity together. A missing required table on a migrated database, mismatched pointer, unexpected orphan row, unknown revision, unreadable database or malformed counts refuses the operation. A missing baseline row behind an active key means corruption. Restore from a verified backup or use a separately verified rebuild of the same pinned inputs. A corrupt installation must never be downgraded to legacy by deleting its baseline.

1. Parse and measure the candidate from the already staged inputs. Bind measurements to their verified hashes. Under the existing database and bundle claims, compare the starting installation with the current one. Take the existing serialized database write and read the predecessor on the **same connection and transaction** that will change the rows. Refuse stale or damaged candidates before deleting any installed row. A refresh comparison requires no additional network lookup.
2. For a fresh install with no installed version, baseline or MONDO/HPO rows, or an explicitly marked legacy predecessor with a consistent installed version, log which fallback applied. On success insert the new rows, installed version, baseline counts and active pointer together. Every managed path that can move the version, including alternate version-writer entry points, must preserve this invariant or refuse. Keep existing source-verification callbacks through the commit boundary.
3. On refusal, write failure or rollback, preserve the previous rows, version and baseline. Reuse the loader's existing reconciliation for a pending source bundle left by a crash. Reopen the database after an uncertain commit and read the durable pointer before retry. An identical retry may report the existing installation when identities, counts and rows agree. A contender that measured against a stale predecessor must restart its comparison against the newly committed installation.
4. Log aggregate counts, known source namespaces and public source identities. A genuine upstream retirement or a new measurement definition needs separate review with pinned releases and an explicit migration or operator recovery procedure. A missing candidate namespace is zero for the disappearance check. On the installed side, a missing namespace record whose absence makes totals inconsistent is corruption.

## Acceptance cases for #2500

Use the actual load and database write path with synthetic bundles. Verify exact rows, prior and new version, baseline key, digests and namespace counts after each attempt.

| Case | Expected outcome |
| --- | --- |
| Same, just under, exactly at and just over a 13-point drop | With all other gates passing, accept the first three and refuse the last before replacing rows. Include unequal denominators. |
| First install and real pre-migration install | Apply the floor, log the distinct fallback and publish an active keyed baseline with the rows. |
| Missing current baseline, incorrect digest, negative/zero total, inconsistent namespace sum, unreadable store | Refuse and preserve state. Exercise a missing migrated table separately from a positively marked legacy row. |
| Namespace disappears with otherwise acceptable rate | Refuse. A previously unseen namespace and a still-positive namespace pass the disappearance check. |
| Floor at and just below 40%, or zero candidate total | Equal passes its floor, below refuses. Zero total refuses before comparisons. |
| Failed insert, final precommit guard or process interruption | Reopen and observe an entirely old or entirely new installation. Verify pending bundle reconciliation and identical retry. |
| Concurrent refreshes from separate source directories against one database | One installation owns the write. A stale contender rechecks the committed predecessor or aborts. |
| Healthy upstream change or gradual partial loss | Surface counts and decision reason. Apply a reviewed change in policy when evidence warrants it. |

The implementation belongs to [#2500](https://github.com/bioedca/Yeliztli/issues/2500), after the threshold and storage decision are approved.
