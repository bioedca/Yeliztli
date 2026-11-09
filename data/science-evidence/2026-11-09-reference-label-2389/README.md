# Reference-call display terminology — issue #2389

## Scope and claim mapping

`Reference call` is project display wording for an existing stored `hom_ref`
classification. It makes no claim about health, pathogenicity, assay completeness,
or the individual's genome beyond that comparison. The change neither calculates
ploidy nor reclassifies a genotype. Unknown states remain unlabeled. Reference
calls remain excluded by the existing carrier-only analysis gates.

| Definition checked | Primary authority | Retained payload |
| --- | --- | --- |
| Reference and alternate allele indices differ from missing calls; allele count represents ploidy | Danecek et al. (2011), section 2.1.2, [PMID:21653522](https://pubmed.ncbi.nlm.nih.gov/21653522/), [DOI:10.1093/bioinformatics/btr330](https://doi.org/10.1093/bioinformatics/btr330) (accessed 2026-11-09); current [VCF v4.5 specification](https://samtools.github.io/hts-specs/VCFv4.5.pdf), section 1.6.2 (accessed 2026-11-09) | `raw/primary-paper-facts.json`, `raw/pubmed-metadata.json`, `raw/vcf-spec-extract.json` |
| The allelic-state answer list has five named answers and no separate reference-call answer | Defining [LOINC:53034-5](https://loinc.org/53034-5) and implementing [LOINC:LL381-5](https://loinc.org/LL381-5) registry (accessed 2026-11-09) | `raw/loinc-answer-list.txt` |

These are terminology definitions, not biological or clinical effect claims.
The VCF paper and maintained specification share an upstream standard; the
LOINC definition and answer registry share an authority. Their non-independence
is explicit under the repository's definitional-claim exception. They are not
two independent cohorts or datasets. Discovery through two tools does not make
the same paper two sources. No LOINC code is assigned to the project phrase.

## Versions, retrieval and editorial checks

- The current VCF PDF identifies revision `e821e4f`, dated 2026-02-25. The pinned
  source revision is `e821e4f02ae25c2175f9a366edca1322d6a2de72`; its full-response
  SHA-256 accompanies the retained short excerpts. Existing GRCh37/b37 coordinate
  and allele conventions are unchanged.
- LOINC is a dated public-page snapshot. The term page reports last update 2.68;
  the answer-list page supplies no release version. No current release is inferred.
- Consensus and Scite were queried first, then NCBI Entrez and primary sources.
  `queries.json` records queries and access limitations, including inconsistent
  discovery-year metadata, denied Scite full text, and direct LOINC HTTP 403.
- Scite verified the DOI with no editorial notices or contrasting-evidence flags.
  The retrieved PubMed record has no correction/retraction link elements or
  retraction publication type. These are checks as of 2026-11-09, not a guarantee
  that later notices cannot appear. `raw/scite-editorial-check.json` retains the
  sanitized result.

## Payloads, rights and validation limits

Payload paths above are relative to
`data/science-evidence/2026-11-09-reference-label-2389/`.
`raw-hashes.json` records their SHA-256 hashes. Retained data consists of public
bibliographic metadata, narrow source-native excerpts and factual mappings.
Scripts, page navigation, institutional-access URLs, author contacts, abstracts,
and sample data are excluded. PubMed XML attributes are not preserved by the
Entrez skill's JSON conversion; this is recorded in the payload.

The paper is CC BY-NC 2.5; no article prose or figures are redistributed. PubMed
metadata is attributed to NCBI. No explicit license was found in the pinned VCF
source or repository README, so only short factual excerpts are retained. LOINC
content is copyright Regenstrief Institute, Inc., and where applicable the LOINC
Committee, under the [LOINC license](https://loinc.org/license), updated 2026-08-06
(accessed 2026-11-09). Its answer names and codes are unchanged.

Synthetic tests cover diploid, X/Y, MT, unknown-state and carried-state behavior,
both label entry points, the production detail API, both rendered UI surfaces,
and existing carrier-only search controls. Eleven new reference-label cases fail
on the original implementation. These tests verify software behavior, not
scientific or clinical validity.
