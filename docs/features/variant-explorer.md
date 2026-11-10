# Variant Explorer

--8<-- "health-disclaimer.md"

The Variant Explorer is the central table for browsing every variant in your sample.

## Navigation

- **Chromosome anchors** — jump to any chromosome from the navigation bar.
- **Infinite scroll** — variants load progressively as you scroll.
- Variants at the same position remain separate rows, including when a page ends
  between them. Rows are ordered by chromosome order, chromosome name, position,
  then variant ID.
- **Total count** — shown asynchronously, so the first page appears immediately. By default this
  is the table count after hiding rows with missing annotation state; turn on
  **Show unannotated** to include those rows. This is separate from the dashboard's full
  uploaded-position count, which includes no-calls.

### API pagination

Both `GET /api/variants` and `GET /api/annotations` return `next_cursor_chrom`,
`next_cursor_pos`, and `next_cursor_rsid`. When `has_more` is true, send all three
values back as `cursor_chrom`, `cursor_pos`, and `cursor_rsid`, retaining the same
sample and filters. The next page starts strictly after that row. Treat the rsID
as an opaque string and URL-encode query parameters. All three next-cursor values
are `null` on the last page, including an empty result.

`/api/variants` also accepts a chromosome/position pair without `cursor_rsid` as an
**inclusive position seek**: every row at that position is eligible. This supports
chromosome navigation with `cursor_pos=0`. It is not a continuation cursor; older
clients must forward `next_cursor_rsid` to avoid repeating boundary rows.
`/api/annotations` requires all three cursor fields together. Other partial cursor
combinations return HTTP 422 on both endpoints.

These cursors traverse an unchanged filtered result set without omissions or
duplicates. They do not preserve a snapshot if the sample changes between requests.

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

See the interpretation reference for the [ensemble pathogenic flag's axes, thresholds, and
displayed fraction](../modules/interpretation-reference.md#ensemble-pathogenic).

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
