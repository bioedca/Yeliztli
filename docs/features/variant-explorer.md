# Variant Explorer

--8<-- "health-disclaimer.md"

The Variant Explorer is the central table for browsing every variant in your sample.

## Navigation

- **Chromosome anchors** — jump to any chromosome from the navigation bar.
- **Infinite scroll** — variants load progressively as you scroll.
- **Total count** — shown asynchronously, so the first page appears immediately. By default this
  is the table count after hiding rows with missing annotation state; turn on
  **Show unannotated** to include those rows. This is separate from the dashboard's full
  uploaded-position count, which includes no-calls.

## Filtering

- Toggle **Show unannotated** to include rows whose annotation coverage is still missing.
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

## Coordinates & assembly (GRCh37 / GRCh38)

The default **Chr** and **Position** columns are **GRCh37 (hg19)** — the app's native assembly,
which every uploaded sample is normalised to. A toolbar **GRCh38** toggle adds two extra columns,
**Chr (GRCh38)** and **Pos (GRCh38)**, so a variant's coordinate is shown in both assemblies side
by side:

- The **GRCh38 columns are a computational liftover** of the native GRCh37 coordinates, added for
  looking a variant up in GRCh38-based external tools — not an independent source of truth
  (a liftover can be wrong or fail).
- A **blank GRCh38 cell** means the position **could not be lifted over**: either the region was
  deleted or rearranged between GRCh37 and GRCh38, or the variant is **mitochondrial** (MT is
  deliberately never lifted — the UCSC hg19 `chrM` is the old Yoruba reference sequence, not the
  rCRS the chip data uses, so lifting it would give wrong GRCh38 coordinates).
- **Which to use:** paste the default (GRCh37) coordinate into GRCh37/hg19-based tools and the
  **GRCh38** coordinate into GRCh38/hg38-based tools. Two differing Position numbers for one
  variant are expected — it is the same variant in two assemblies.

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

Click any row to open the **[variant detail](variant-detail.md)** panel. Gene symbols link to
the full **[Gene Detail](gene-detail.md)** page for that gene.
