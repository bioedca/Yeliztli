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

## Independent assembly check

Ensembl Variation imports its rsID mappings from dbSNP, so queries 1–3 are one
corroborated variant-database assertion rather than two independent ones. The
independent check reads the GRCh37/hg19 reference base directly.

| # | Endpoint | Purpose | Outcome |
| --- | --- | --- | --- |
| 4 | `GET https://api.genome.ucsc.edu/getData/sequence?genome=hg19;chrom=chr19;start=45411940;end=45411941` | Base at rs429358's GRCh37 position | HTTP 200 — `T`, matching the `T/C` allele string |
| 5 | `GET https://api.genome.ucsc.edu/getData/sequence?genome=hg19;chrom=chr19;start=45412078;end=45412079` | Base at rs7412's GRCh37 position | HTTP 200 — `C`, matching the `C/T` allele string |
| 6 | `GET https://api.genome.ucsc.edu/getData/sequence?genome=hg19;chrom=chr19;start=44908683;end=44908684` | Negative control at the rejected position | HTTP 200 — `G`, which rs429358 cannot use |
| 7 | `GET https://api.genome.ucsc.edu/getData/sequence?genome=hg19;chrom=chr19;start=44908821;end=44908822` | Negative control at the rejected position | HTTP 200 — `A`, which rs7412 cannot use |

## Citation verification

| # | Endpoint | Purpose | Outcome |
| --- | --- | --- | --- |
| 8 | `GET .../esummary.fcgi?db=pubmed&id=11125122,12045153` | Confirm dbSNP and UCSC citations by title; check retraction flags | HTTP 200 — titles match; no retraction or correction `pubtype` |
| 9 | `GET .../esearch.fcgi?db=pubmed&term=Ensembl 2025[title] AND Nucleic Acids Res[jour]` | Locate the current Ensembl database paper rather than recall it | HTTP 200 — resolved to PMID 39656687 |
| 10 | `GET .../esummary.fcgi?db=pubmed&id=39656687,39658041` | Confirm the Ensembl citation by title; check retraction flags | HTTP 200 — "Ensembl 2025." confirmed; no retraction or correction `pubtype` |

## Side finding

| # | Endpoint | Purpose | Outcome |
| --- | --- | --- | --- |
| 11 | `GET https://grch37.rest.ensembl.org/variation/human/{rs440446,rs405509,rs769449}` | APOE-region neighbours in the vendor fixtures | HTTP 200 each (a batch POST to `/variation/homo_sapiens` returned HTTP 500 and was replaced by per-rsID GETs) |
| 12 | `GET .../esummary.fcgi?db=snp&id=440446,405509,769449` | Second record for the side finding | HTTP 200 |

Transient upstream failures are recorded above rather than retried silently.
No quota limit was reached.
