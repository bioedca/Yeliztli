# Genome Browser

--8<-- "health-disclaimer.md"

The built-in **IGV.js** genome browser gives you a visual view of your variants in their
genomic context.

- Navigate by **gene name**, **rsID**, or **coordinates**.
- When a sample is selected, **Your Variants** shows your genotypes as a single teal
  sample track.
- **ClinVar Variants** shows ClinVar records near the current locus, colour-coded by
  clinical significance.
- **gnomAD AF** shows population allele-frequency context for nearby variants.
- A **reference genome track** provides sequence context.
- Click a variant in the browser to open its **[detail panel](variant-detail.md)**.

## Default annotation tracks

The default GRCh37/hg19 browser view includes these local annotation tracks:

| Track | What it shows | How to read it |
|-------|---------------|----------------|
| **Your Variants** | Variants from the selected sample, when a sample is loaded | A solid teal genotype track. Click a variant to inspect that sample call. |
| **ClinVar Variants** | ClinVar variant records in the visible region | Marker colour reflects ClinVar clinical significance, using the legend below. |
| **gnomAD AF** | gnomAD population allele frequency records in the visible region | Purple markers provide frequency context for how common a variant is in population data. |

The **ENCODE cCREs** track is available only for GRCh38-compatible browser builds. The
current default Genome Browser build is GRCh37/hg19, so ENCODE cCREs is not part of
the default GRCh37 view.

### ClinVar colour legend

| ClinVar clinical significance | Marker colour |
|-------------------------------|---------------|
| Pathogenic | Red (`#DC2626`) |
| Likely pathogenic | Light red (`#EF4444`) |
| Uncertain significance | Amber (`#F59E0B`) |
| Likely benign | Light green (`#22C55E`) |
| Benign | Green (`#16A34A`) |
| Conflicting classifications / interpretations of pathogenicity | Orange (`#F97316`) |
| Not provided or another ClinVar value | Gray (`#6B7280`) |

ClinVar records are external clinical-significance interpretations, not Yeliztli
module findings by themselves. Use them as browser context alongside the variant detail
page and the module-specific reports.

### gnomAD AF track

The **gnomAD AF** track shows population allele-frequency annotations from the local
gnomAD GRCh37 reference data. It helps distinguish variants that are common in population
data from variants that are rare or absent in that source. It is context only: frequency
does not by itself determine whether a variant is clinically important.

When `grch37.fa`, `grch37.fa.fai`, `grch37_refseq.bed`, and
`genome_browser_reference_manifest.json` are installed in the Yeliztli data directory,
the Genome Browser serves the GRCh37 reference and RefSeq track locally. The local bundle must
validate as the expected UCSC hg19 FASTA / `refGene` build, including GRCh37/hg19 FASTA-index
sentinel chromosome lengths. If those files are missing or validation fails, it falls back to
IGV.js's hosted `hg19` reference after showing the one-time third-party reference notice.

This is useful for seeing how your variants sit relative to genes, exons, and nearby
variation.

## References

[1] Karczewski KJ, et al. [The mutational constraint spectrum quantified from variation
in 141,456 humans](https://doi.org/10.1038/s41586-020-2308-7). *Nature*. 2020;581:434-443.

[2] Landrum MJ, et al. [ClinVar: improving access to variant interpretations and
supporting evidence](https://doi.org/10.1093/nar/gkx1153). *Nucleic Acids Research*.
2018;46(D1):D1062-D1067.
