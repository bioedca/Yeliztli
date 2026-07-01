# Variant Explorer

--8<-- "health-disclaimer.md"

The Variant Explorer is the central table for browsing every variant in your sample.

## Navigation

- **Chromosome anchors** — jump to any chromosome from the navigation bar.
- **Infinite scroll** — variants load progressively as you scroll.
- **Total count** — shown asynchronously, so the first page appears immediately.

## Filtering

- Toggle between **all variants** and **annotated only**.
- Use the advanced filter panel for specific criteria — gene, consequence, allele frequency,
  ClinVar significance, and more.
- Search ClinVar significance for `conflicting` to review variants ClinVar marks as
  **Conflicting classifications of pathogenicity**; these variants are not shown as
  pathogenic-variant findings.
- Filter by the **tags** you've applied (see below).

## Column presets

Switch between predefined column layouts, or build your own from **Column Settings**:

| Preset | Columns shown |
|--------|---------------|
| Clinical | Genotype, Gene, Consequence, ClinVar significance, ClinVar review stars |
| Research | Genotype, Gene, Consequence, ClinVar significance, ClinVar review stars, CADD, SIFT score/prediction, PolyPhen-2 score/prediction, REVEL, ensemble pathogenic flag |
| Frequency | Genotype, Gene, global gnomAD AF, rare flag |
| Scores | Gene, Consequence, CADD, SIFT score/prediction, PolyPhen-2 score/prediction, REVEL |

## Tagging

Apply tags to variants for personal tracking — predefined ones (*Pathogenic interest*,
*Benign confirmed*, *Follow-up*, *Research*) or your own. You can then filter the table by tag.

## Custom annotation overlays

Use [Annotation Overlays](annotation-overlays.md) to upload small BED or VCF files and
compare custom annotations with your sample's variants. Overlay matches are
display-only: they help triage variants, but they do not change findings, scores,
or evidence ratings.

## Watching a VUS

For a **Variant of Uncertain Significance**, click **Watch**. When reference databases are
updated, watched variants are re-checked and you're notified if any are reclassified.

Click any row to open the **[variant detail](variant-detail.md)** panel.
