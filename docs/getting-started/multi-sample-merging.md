# Multi-sample merging

If you've tested with more than one service — say **23andMe** *and* **AncestryDNA** — you can
combine those files for the same person. Different arrays cover somewhat different markers, so
merging gives you broader coverage and lets you cross-check where the two agree.

There are two distinct steps: **grouping** samples under an individual, and (optionally)
**merging** them into one combined sample.

## Group samples under an individual

An **individual** is a container that links the samples belonging to one person. Grouping does
**not** change any data — each sample stays independent and keeps its own results.

1. Create an individual (give it a name).
2. **Link** your samples to it (each sample is annotated on its own first).

This alone is useful for keeping a person's samples together. To actually combine them,
continue to merging.

## Merge two samples

Merging creates a **new, combined sample** — the **union** of variants from both sources —
with extra columns recording where each call came from and whether the two sources agreed.

From the individual's page, once **exactly two** linked samples are present, choose **Merge
samples**. A short wizard walks you through:

1. **Strategy** — how to handle sites where the two sources disagree:
    - **Flag discordant calls** *(recommended)* — keep neither call at a conflicting site; mark
      it as a no-call and record both original calls so nothing is silently guessed.
    - **Prefer 23andMe** — keep the 23andMe call at conflicts.
    - **Prefer AncestryDNA** — keep the AncestryDNA call at conflicts.
2. **Preview** — a dry run shows the concordance summary (below) and an estimated duration.
3. **Confirm** — name the merged sample and start it. Annotation runs automatically, after
   which the merged sample appears like any other.

## The concordance summary

The preview and the **Concordance Report** break the merge down into buckets:

| Bucket | Meaning |
|--------|---------|
| **match** | Both sources gave the same call (or both were no-calls) |
| **filled_nocall** | One source had no call; the other's call was used |
| **discordant** | Both called, but **disagreed** (handled per your chosen strategy) |
| **unique (S1 / S2)** | The site was present in only one source |
| **collapsed_rsid** | Same position in both sources but under different rsIDs (collapsed into one) |

The **Concordance Report** (from the merged sample) shows these totals plus a paginated table
of the **discordant loci** — the exact sites where your two files disagreed, with gene,
consequence, and ClinVar context so you can see whether any disagreement matters.

## Provenance on every merged variant

In a merged sample, each variant records:

- **source** — which file it came from (`S1`, `S2`, or `both`), and
- **concordance** — its bucket (match / filled_nocall / discordant / unique).

You can filter the [Variant Explorer](../features/variant-explorer.md) by these, e.g. to show
only the sites where your two sources disagreed.

## Good to know

- **Two samples at a time.** Merging combines exactly two sources; merging three or more isn't
  supported yet.
- **Same genome build only.** Merging matches sites by coordinate, so both files must be on the
  same build. 23andMe and AncestryDNA are both GRCh37, so this is normally fine — just don't
  try to merge files from different builds.
- **Both sources must be fully annotated and current** before you can merge; you'll be asked to
  re-annotate a stale source first.
- **A merged sample is a fresh, independent copy.** It isn't a live view of its sources —
  re-annotating a source later won't change it. Deleting a source also deletes any merged
  samples built from it.
- **Tags and watched variants don't carry over** automatically; after a merge you'll be offered
  the chance to re-apply watches.
