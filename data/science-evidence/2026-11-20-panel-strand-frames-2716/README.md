# Public sources for the categorical panel frame design

This packet documents input conventions and a disagreement on the allele set
for a public reference marker. It authorizes no effect direction, diplotype,
clinical action or individual call. All queries use public identifiers.

| Source and query | Retained evidence and scope |
| --- | --- |
| [NCBI RefSNP rs1051266](https://api.ncbi.nlm.nih.gov/variation/v0/refsnp/1051266), build 157 (accessed 2026-11-20) | `raw/public-records.json` retains the GRCh37.p13 chromosome placement on `NC_000021.8` and transcript placement on `NM_194255.4`. The RefSNP alignment flags show `is_aln_opposite_orientation=false` for the chromosome and `true` for the transcript. Both have `is_mismatch=false`. Chromosome g.46957794 has reference T and alternatives A, C and G. NCBI public database metadata. |
| [Ensembl GRCh37 variation/rs1051266](https://grch37.rest.ensembl.org/variation/homo_sapiens/rs1051266?content-type=application/json) (accessed 2026-11-20) | The same file retains the returned mapping, build, strand, allele string and data source. At 21:46957794 Ensembl gives T/C/G. Its source says it imports from dbSNP. These records share upstream assertions and do not independently establish an effect or settle whether a missing alternate is impossible. Only source-attributed factual fields are retained. |
| [Deelen et al., Genotype Harmonizer](https://doi.org/10.1186/1756-0500-7-901), PMID:25495213, DOI:10.1186/1756-0500-7-901 (accessed 2026-11-20) | Methods precedent. The authors aligned datasets to a specified reference and used linkage disequilibrium to resolve ambiguous SNPs. This does not validate a particular panel key or a single-sample strand inference. Article licensed CC BY 4.0. PubMed `efetch.fcgi?db=pubmed&id=25495213&retmode=xml` returned a journal article with no linked comments or corrections in the record. Bibliographic fields are retained in `raw/public-records.json`. Consensus returned this paper in a search and Scite looked it up by PMID. The query and returned identifiers are in `raw/discovery.json`. |
| [Ancestry, Downloading DNA Data](https://redir.ancestry.help/hc/en-gb/articles/53933317283603-Downloading-DNA-Data), support article 53933317283603 (accessed 2026-11-20) | The vendor describes the forward strand of GRCh37 for its SNP download. The declaration applies to that format. |
| [23andMe, Raw Genotype Data Technical Details](https://int.customercare.23andme.com/hc/en-us/articles/215983947-Raw-Genotype-Data-Technical-Details), support article 215983947 (accessed 2026-11-20) | The indexed article describes the plus strand for the GRCh37 **browser** display. The old link redirects to the Raw Data help section. It does not establish the provenance of an arbitrary uploaded file. Read the supported export header and validate its assembly and orientation declarations. |

The last two sources document their own products. Short factual extracts from
the public support articles are retained in `raw/vendor-format.json`. No
customer export was queried. No vendor article is redistributed in full.
The primary database extracts contain the complete alleles of the two named
RefSNP placements and the complete Ensembl mapping returned for this query.

## Reuse terms

The NCBI extract retains factual RefSNP and bibliographic metadata. NCBI
imposes no restrictions of its own on molecular data, and cautions that
submitters may claim separate rights. Ensembl makes its own data available
without restriction and cautions that imported data can have third-party
constraints. The above Ensembl record identifies dbSNP as its source.
No Apache software licence is assigned to that imported record.
[NCBI policies](https://www.ncbi.nlm.nih.gov/home/about/policies/)
(accessed 2026-11-20) and
[Ensembl data disclaimer](https://www.ensembl.org/info/about/legal/disclaimer.html)
(accessed 2026-11-20).

Ancestry and 23andMe retain rights in their help text. No open licence for
the articles was identified. Only short attributed quotations supporting the
file-format discussion were retained. The discovery-services extract retains
query inputs and bibliographic facts; no third-party abstract, generated
summary or full text is redistributed.
