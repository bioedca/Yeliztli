# Annotation Overlays

Annotation Overlays let you upload your own BED or VCF annotation file and match
those records against the variants in a selected sample. Matched fields are shown
in the overlay results table for the selected sample.

Overlays are for review and triage. They do not change clinical findings, module
scores, risk classifications, pharmacogenomic interpretations, or evidence ratings.

## When To Use

Use an overlay when you have a small annotation track that you want to compare with
your own variants, for example:

- regions of interest from a BED file
- a small VCF with custom INFO fields
- a project-specific list of loci that you want to review alongside the built-in
  annotations

The overlay engine is built into Yeliztli. You do not need to install the external
`vcfanno` command-line tool.

## Workflow

1. Select a sample from the sample selector.
2. Open **Annotation Overlays** from the sidebar or command palette.
3. Upload a plain-text BED or VCF file, or a gzipped VCF (`.vcf.gz`).
4. Review the detected format, record count, column names, and parse warnings.
5. Save the overlay with a name and optional description.
6. Click **Apply** for the selected sample.
7. Select the applied overlay to review matched annotations in the overlay results
   table.

Saved overlay definitions are stored once, but applying an overlay writes matched
rows for each sample. Reapplying the same overlay to the same sample replaces that
sample's previous overlay results for that overlay.

### Refresh Previously Applied BED Overlays

BED interval matching has been corrected to include the right bases at both
boundaries. Existing saved results do not update automatically. If you applied a
BED overlay before this correction, select each sample where you used it and click
**Apply** again for that saved overlay. Repeat for every affected BED overlay.
There is no need to upload the file again: **Apply** reparses the saved file and
replaces only that overlay's results for the selected sample. If the saved file is
missing, the app asks you to re-upload it. VCF overlays are unaffected by this
coordinate correction.

## File Requirements

| Requirement | Details |
|-------------|---------|
| File type | The upload page accepts plain-text `.bed` and `.vcf`, plus gzip-compressed `.vcf.gz`. Gzip content is decompressed before parsing. |
| Genome build | GRCh37/hg19 coordinates. Yeliztli's live annotation pipeline is GRCh37-based, and overlays are matched directly by chromosome and position. |
| Size limit | 5 MiB per uploaded file. Gzip content must also expand to at most 5 MiB. |
| Record limit | 50,000 valid BED regions or VCF ALT entries. Each ALT in a multi-ALT VCF record counts separately. |
| Chromosomes | `chr` prefixes are accepted and normalized away. `M` is normalized to `MT`. |
| Encoding | UTF-8 text, after decompression for gzip files. |

Yeliztli does not lift over overlay files. If a BED or VCF is in GRCh38, T2T, or
another coordinate system, it may match nothing or match the wrong locus.

## BED Matching

BED overlays match by chromosome and the variant's stored position:

- the first three columns are interpreted as `chrom`, `start`, and `end`
- BED uses a zero-based start and an exclusive end; sample positions are one-based,
  so a variant matches when `start < pos <= end`
- for example, BED `[100, 200)` matches sample positions 101 through 200; positions
  100 and 201 are outside, and `[199, 200)` matches only position 200
- matching remains point-based for all variants: a multi-base REF does not extend
  the matched span, unlike the Rare Variant Finder's REF-span overlap
- additional columns become overlay annotation fields
- if several records cover the same variant, the first matching record's
  annotations are kept
- a header comment such as `#chrom start end label score` is used to name the
  annotation columns; otherwise columns are named automatically

The coordinate conversion follows the
[UCSC BED format specification](https://genome.ucsc.edu/FAQ/FAQformat.html#format1)
(accessed 2026-11-10).

Use GRCh37 coordinates and keep intervals narrow enough for the question you are
asking. Broad regions can match many variants and make the results table less useful.

## VCF Matching

VCF overlays use INFO fields as overlay annotation columns:

- `##INFO` header IDs become column names
- records match by exact chromosome and POS
- when the sample has annotated REF/ALT alleles, matching is allele-aware by
  chromosome, POS, REF, and ALT
- if only raw variants are available, or an annotated row lacks REF or ALT, that
  row uses position-only matching because usable REF/ALT are not available

For multi-ALT VCF records, Yeliztli creates one entry per listed ALT allele. For
allele-aware matches, INFO values declared in the `##INFO` header as `Number=A`
are selected by the matching ALT's index. For `Number=R`, the reference value is
skipped and the matching ALT's value is selected. This projection requires the
value count to match the declared number of alleles: one value per ALT for
`Number=A`, or one for REF followed by one per ALT for `Number=R`. Other INFO
values, including lists with a mismatched count, are kept as parsed.

For example, with `REF=C`, `ALT=G,T`, `AF=0.1,0.2` declared as `Number=A`, and
`AD=7,3,9` declared as `Number=R`, an allele-aware C/G match displays `AF=0.1` and
`AD=3`; a C/T match displays `AF=0.2` and `AD=9`. These fields do not require
splitting the VCF into one ALT per record before upload.

Position-only fallback cannot select an ALT for the sample row. It retains the
original, unprojected INFO values, so the same example displays `AF=0.1,0.2` and
`AD=7,3,9`. Those lists should not be read as values specific to the sample's ALT.

## Limits And Interpretation

- Overlay results are display-only. They do not alter existing module results.
- Overlays are per-sample once applied. Applying an overlay to one sample does not
  apply it to other samples.
- The matcher compares stored chromosome and position values. It does not resolve
  rsID aliases, remap assemblies, or infer equivalent loci.
- Uploading a file with the wrong build or chromosome naming can produce zero
  matches without meaning the loci are absent from your sample.
- Large genome-wide resources should be converted into a supported reference bundle
  or a smaller targeted overlay instead of uploaded through this page.
