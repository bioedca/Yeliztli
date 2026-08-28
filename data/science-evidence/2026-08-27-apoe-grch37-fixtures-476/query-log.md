# Query log — issue #476, accessed 2026-08-27

All requests are unauthenticated GETs against public reference-database and
bibliographic endpoints. No repository data, genotype, or sample identifier was
transmitted.

## Coordinate resolution

| # | Endpoint | Purpose | Outcome |
| --- | --- | --- | --- |
| 1 | `GET https://grch37.rest.ensembl.org/variation/human/rs429358` | Primary GRCh37 mapping, strand, allele string | HTTP 200 — `19:45411941`, strand `1`, `T/C` |
| 2 | `GET https://grch37.rest.ensembl.org/variation/human/rs7412` | Primary GRCh37 mapping, strand, allele string | HTTP 500 twice, HTTP 200 on the third attempt — `19:45412079`, strand `1`, `C/T` |
| 3 | `GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=snp&id=429358,7412` | Both builds from the upstream variant database | HTTP 200 — GRCh38 `19:44908684` / `19:44908822`, GRCh37 `19:45411941` / `19:45412079` |

Each Ensembl response was inspected for every mapping, not only the first: all
records expose exactly one `coord_system: chromosome` mapping on the primary
assembly, so no patch or alternate-locus selection was required.

## Independent mapping check

Ensembl Variation imports its rsID mappings from dbSNP, so queries 1-3 are one
corroborated variant-database assertion rather than two independent ones. The
independent mapping is obtained from VariantValidator, which is given the coding
HGVS only -- no rsID -- and projects it onto the genome through RefSeq/UTA
transcript alignments.

| # | Endpoint | Purpose | Outcome |
| --- | --- | --- | --- |
| 4 | `GET https://rest.variantvalidator.org/VariantValidator/variantvalidator/GRCh37/NM_000041.4%3Ac.388T%3EC/all` | Independent genomic projection for the codon-112 variant | HTTP 200 — GRCh37 `NC_000019.9:g.45411941T>C`, GRCh38 `NC_000019.10:g.44908684T>C`, `rsid: None` |
| 5 | `GET .../GRCh37/NM_000041.4%3Ac.526C%3ET/all` | Independent genomic projection for the codon-158 variant | HTTP 200 — GRCh37 `NC_000019.9:g.45412079C>T`, GRCh38 `NC_000019.10:g.44908822C>T`, `rsid: None` |

An earlier attempt through NCBI Variation Services
(`/variation/v0/hgvs/{hgvs}/contextuals`) returned transcript-relative SPDIs
(`NM_000041.4:456 T>C`) rather than a genomic projection and was not used.

## Assembly-level control

A reference base is necessary but not sufficient to place a variant, so these
reads corroborate rather than establish the positive claim; the two control rows
are what make the check discriminating.

| # | Endpoint | Purpose | Outcome |
| --- | --- | --- | --- |
| 6 | `GET https://api.genome.ucsc.edu/getData/sequence?genome=hg19;chrom=chr19;start=45411940;end=45411941` | Base at rs429358's GRCh37 position | HTTP 200 — `T`, consistent with the `T/C` allele string |
| 7 | `GET .../start=45412078;end=45412079` | Base at rs7412's GRCh37 position | HTTP 200 — `C`, consistent with the `C/T` allele string |
| 8 | `GET .../start=44908683;end=44908684` | Negative control at the rejected position | HTTP 200 — `G`, which rs429358 cannot use |
| 9 | `GET .../start=44908821;end=44908822` | Negative control at the rejected position | HTTP 200 — `A`, which rs7412 cannot use |

## Discovery-tool ladder

| # | Tool | Purpose | Outcome |
| --- | --- | --- | --- |
| 10 | Consensus MCP `search` | Required first rung | 20 papers. None asserts a genomic build coordinate; the APOE literature identifies these variants by codon or coding position. Surfaced both records cited for the identity claim. |
| 11 | Scite MCP `search_literature` | Required first rung | **Unavailable** — monthly MCP call quota exhausted (250/250); service reported reset on 2026-09-03. No Scite result used. |
| 12 | PubMed MCP `search_articles` | Fallback for Scite | `APOE AND (rs429358 OR rs7412) AND (codon 112 OR codon 158)` returned 2 records; PMID:27078154 retained. |

## Source version and license probes

| # | Endpoint | Outcome |
| --- | --- | --- |
| 13 | `GET https://grch37.rest.ensembl.org/info/data` | HTTP 200 — release 116 |
| 14 | `GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi?db=snp` | HTTP 200 — `dbbuild` Build250306-1408.1, `lastupdate` 2025/03/11 |
| 15 | `GET https://api.genome.ucsc.edu/list/ucscGenomes` | HTTP 200 — hg19 = "Feb. 2009 (GRCh37/hg19)", GCA_000001405.1 |
| 16 | `GET https://www.ebi.ac.uk/about/terms-of-use/` | HTTP 200 — EMBL-EBI Terms of Use, revised 2024-02-05 |
| 17 | `GET https://www.ncbi.nlm.nih.gov/home/about/policies/` | HTTP 200 — US-government content is public domain |
| 18 | `GET https://genome.ucsc.edu/conditions.html` | HTTP 200 — Genome Browser sequence and annotation data "freely available for any use with the following conditions"; the commercial licence covers binaries/source, not the sequence data used here |
| 19 | `GET https://raw.githubusercontent.com/openvar/variantValidator/master/LICENSE.txt` | HTTP 200 — GNU Affero General Public License v3 |

Observed versions and license references are recorded per source in
`source-manifest.json` under `source_provenance`.

## Citation verification

| # | Endpoint | Purpose | Outcome |
| --- | --- | --- | --- |
| 20 | `GET .../esummary.fcgi?db=pubmed&id=11125122,12045153` | Confirm dbSNP and UCSC citations by title; check retraction flags | HTTP 200 — titles match; no retraction or correction `pubtype` |
| 21 | `GET .../esearch.fcgi?db=pubmed&term=Ensembl 2025[title] AND Nucleic Acids Res[jour]` | Locate the current Ensembl database paper rather than recall it | HTTP 200 — resolved to PMID 39656687 |
| 22 | `GET .../esummary.fcgi?db=pubmed&id=39656687,39658041` | Confirm the Ensembl citation by title; check retraction flags | HTTP 200 — "Ensembl 2025." confirmed; no retraction or correction `pubtype` |
| 23 | `GET .../esearch.fcgi?db=pubmed&term=10.1002/jcla.23925[doi]` then `esummary` on the result plus 27078154 | Resolve and confirm both identity-claim citations | HTTP 200 — PMID:34313350 and PMID:27078154 confirmed by title; no retraction or correction `pubtype`. A first attempt at this lookup guessed PMID 34327739, which eSummary showed to be an unrelated pharmacology paper; the guess was discarded rather than cited. |

## Side finding

| # | Endpoint | Purpose | Outcome |
| --- | --- | --- | --- |
| 24 | `GET https://grch37.rest.ensembl.org/variation/human/{rs440446,rs405509,rs769449}` | APOE-region neighbours in the vendor fixtures | HTTP 200 each (a batch POST to `/variation/homo_sapiens` returned HTTP 500 and was replaced by per-rsID GETs) |
| 25 | `GET .../esummary.fcgi?db=snp&id=440446,405509,769449` | Second record for the side finding | HTTP 200 |

Transient upstream failures are recorded above rather than retried silently.
No quota limit was reached.
