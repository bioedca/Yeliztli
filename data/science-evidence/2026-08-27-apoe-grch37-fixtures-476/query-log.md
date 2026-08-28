# Query log — issue #476, accessed 2026-08-27

All requests are unauthenticated GETs against public reference-database
endpoints. No repository data, genotype, or sample identifier was transmitted.

| # | Endpoint | Purpose | Outcome |
| --- | --- | --- | --- |
| 1 | `GET https://grch37.rest.ensembl.org/variation/human/rs429358` | Primary GRCh37 mapping, strand, allele string | HTTP 200 — `19:45411941`, strand `1`, `T/C` |
| 2 | `GET https://grch37.rest.ensembl.org/variation/human/rs7412` | Primary GRCh37 mapping, strand, allele string | HTTP 500 twice, HTTP 200 on the third attempt — `19:45412079`, strand `1`, `C/T` |
| 3 | `GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=snp&id=429358,7412` | Independent second source for both builds | HTTP 200 — GRCh38 `19:44908684` / `19:44908822`, GRCh37 `19:45411941` / `19:45412079` |
| 4 | `GET https://grch37.rest.ensembl.org/variation/human/{rs440446,rs405509,rs769449}` | Side-finding check on the APOE-region neighbours | HTTP 200 each (batch POST to `/variation/homo_sapiens` returned HTTP 500 and was replaced by per-rsID GETs) |
| 5 | `GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=snp&id=440446,405509,769449` | Second source for the side finding | HTTP 200 |

Each Ensembl response was inspected for every mapping, not only the first: all
five records expose exactly one `coord_system: chromosome` mapping on the
primary assembly, so no patch or alternate-locus selection was required.

Transient upstream failures are recorded above rather than retried silently.
No quota limit was reached.
