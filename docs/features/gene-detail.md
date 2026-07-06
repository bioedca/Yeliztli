# Gene Detail

--8<-- "health-disclaimer.md"

The Gene Detail page brings together sample variants and cached public gene context for one
gene. Open it from a gene symbol in the Findings Explorer, a dashboard finding, or the
**Protein** tab on a variant detail page. Its URL is `/genes/{symbol}` for the selected sample.

## What it shows

- **Protein viewer** -- UniProt accession, sequence length, cache status, and a Nightingale
  diagram of protein domains and features. Protein-changing sample variants with HGVS p.
  notation can be highlighted on the protein map.
- **Variants** -- the sample's annotated variants in the gene, including rsID, HGVS protein
  notation, consequence, genotype, ClinVar significance and review stars, evidence-conflict
  status, gnomAD allele frequency, and CADD. Variant IDs link back to their variant detail pages.
- **Population allele frequencies** -- a gnomAD subpopulation chart for the gene's variants
  when population frequency fields are available.
- **Phenotypes** -- local gene-phenotype records, including MONDO/HPO/OMIM links when those
  references are available.
- **Literature** -- PubMed article cards from the local literature cache and optional enrichment
  setup.

## Network and privacy note

Opening this page is local for your sample data, but the protein viewer can make one outbound
lookup. Yeliztli checks the local UniProt cache first; on a cache miss or stale cache entry, it
requests reviewed human protein annotations from `rest.uniprot.org/uniprotkb` using the gene
symbol you are viewing.

That request does **not** send your genotypes, variants, sample ID, or findings, but it does
reveal the inspected gene symbol, your IP address, and the request timing to UniProt/EBI. Results
are cached locally in `reference.db` for 30 days. If the request fails or network access is
blocked, Yeliztli shows stale cached protein data when available, or a message that protein data
is unavailable.

If you configured PubMed enrichment with a contact email, the **Literature** section can also
search NCBI PubMed for the viewed gene and fetch matching article metadata by PMID. Without that
email, Yeliztli uses cached literature only. PubMed requests send public identifiers and NCBI
contact metadata, not your genotypes, sample ID, or findings.

See [Privacy & data handling](../privacy.md#when-yeliztli-does-use-the-network) for the full
network accounting.
