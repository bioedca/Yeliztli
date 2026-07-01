# Genome Browser

--8<-- "health-disclaimer.md"

The built-in **IGV.js** genome browser gives you a visual view of your variants in their
genomic context.

- Navigate by **gene name**, **rsID**, or **coordinates**.
- A **variant track** shows your genotypes with colour-coded annotations.
- A **reference genome track** provides sequence context.
- Click a variant in the browser to open its **[detail panel](variant-detail.md)**.

When `grch37.fa`, `grch37.fa.fai`, and `grch37_refseq.bed` are installed in the
Yeliztli data directory, the Genome Browser serves the GRCh37 reference and RefSeq
track locally. If those files are missing, it falls back to IGV.js's hosted `hg19`
reference after showing the one-time third-party reference notice.

This is useful for seeing how your variants sit relative to genes, exons, and nearby
variation.
