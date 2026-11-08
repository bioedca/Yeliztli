# ClinVar gene identity fallback (#2464)

Public source-format metadata only; no sample data, real genotypes, or new
biological-effect claims. All software regression calls and loci are synthetic.
Primary storage and joins remain GRCh37/b37.

## Discovery and sources

Consensus and Scite were queried first for ClinVar VCF gene metadata. Queries,
selected paper metadata, and the Scite verification summary are retained in
`raw/science-discovery-metadata.json`. These are discovery tools, not independent
scientific sources. The NCBI Entrez skill then retrieved the paper's PubMed record.
The selected identifiers come from that article's own `PubmedData/ArticleIdList`,
not the identifiers of papers in its reference list.

- Landrum MJ et al., *ClinVar: improving access to variant interpretations and
  supporting evidence*. [PMID:29165669](https://pubmed.ncbi.nlm.nih.gov/29165669/),
  [DOI:10.1093/nar/gkx1153](https://doi.org/10.1093/nar/gkx1153),
  PMCID:PMC5753237 (accessed 2026-11-08). This is the database-description source,
  not evidence that any synthetic variant causes disease. Selected raw public
  identifiers/title are in `raw/pubmed-identifiers.xml`; derived metadata is in
  `raw/pubmed-metadata-derived.json`. PubMed returned no linked correction or
  retraction, and Scite reported no editorial notice as of this access date.
  This bounded check does not establish that no future notice can appear.
- The authoritative [ClinVar GRCh37 VCF](https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh37/clinvar.vcf.gz)
  (accessed 2026-11-08), fileDate **2026-11-01**, Last-Modified **2026-11-02**,
  defines `GENEINFO` as gene-symbol/GeneID pairs separated by `|`, with `:` within
  each pair. `raw/clinvar-vcf-header.txt` preserves the returned metadata headers;
  request range, response metadata and SHA-256 are in
  `raw/clinvar-vcf-header-metadata.json`. A bounded 64 KiB request was used, and
  no variant or sample rows were retained. This header directly supports the
  parser's delimiter handling and preservation of the complete field.

The paper and VCF share NCBI upstream provenance and are **not independent**.
They are not counted as independent evidence for a clinical or biological effect.
This change transfers explicit source gene identity into existing software joins;
it neither derives inheritance from ClinVar nor changes an effect or clinical rule.

Context7 returned official VCF/XML examples but not the complete current field
definition. The NCBI download-help page presented a browser challenge; no challenge
was bypassed. The public FTP header supplied the definition. Exact Entrez queries,
fallbacks and terms metadata are in `raw/queries-and-terms.json`. NGS tooling was
not applicable to this metadata and synthetic software task.

## Terms and retained payloads

The [NCBI data-use policy](https://www.ncbi.nlm.nih.gov/home/about/policies/)
(accessed 2026-11-08) identifies government-created information as public domain
and states that NCBI itself places no restrictions on molecular-data distribution,
with explicit caveats for submitter intellectual-property rights. Attribution is
to NCBI/ClinVar and the cited authors. Only metadata is copied here; paper abstracts,
full text, affiliations, and discovery-provider access links are omitted. Source
rights are not transferred or expanded by this repository.

## Software choices and limits

- A successful VEP lookup must genuinely lack the resolved allele. An existing
  selected VEP row, even one with a NULL gene, takes precedence. Failed,
  unavailable or still-ambiguous VEP lookups cannot authorize a fallback.
- The ClinVar lookup must identify one complete source allele, and its eligible
  records must agree on complete `GENEINFO`. Exactly one syntactically complete
  symbol/GeneID pair is required. Multiple pairs, missing IDs, conflicting
  records and unresolved I/D/no-call or alternate-allele selection withhold the
  fallback. Review stars never choose a gene. Gene aliases are not normalized.
- `gene_source=clinvar` records this origin; raw `clinvar_gene_info` remains
  separate. Transcript, HGVS, consequence and VEP coverage are not invented.
  The independently sourced phenotype lookup retains its disease-scope gate.
  Its scope marker is committed with the annotated rows so the first analysis
  sees the correct scope before its later successful-run snapshot is written.
- Additive sample schema 35 and reference migration leave historical provenance
  NULL. The `gene-info-v1` ingestion revision offers a same-release refresh to
  older loaders without downgrading a newer release. The existing version
  staleness prompt asks users to re-annotate; versionless samples require manual
  re-annotation. A source-only refresh clears stale ClinVar-owned gene context
  and its dependent phenotype join, while preserving VEP/unknown-owned fields.

Regression tests verify source transfer, filtering, migration and lifecycle
behavior. They do not scientifically validate the synthetic variants or the
existing downstream clinical safeguards.
