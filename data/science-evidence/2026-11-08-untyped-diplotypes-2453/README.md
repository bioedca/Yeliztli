# Untyped diplotype enumeration — issue #2453

## Scope

This packet checks classifications already shipped in Yeliztli. The change adds
the homozygous candidate to the existing conservative policy when both called
chromosomes are reference fills. It preserves observed non-reference alleles,
looks up every candidate in the shipped diplotype table, and does not calculate
missing rows from allele activity values. It changes no allele definitions,
guideline wording, supported genes, coordinates, or builds.

The direct call remains separate from the possible conservative diplotype.
These sources define classifications for known diplotypes; they do **not**
establish that a person with an untyped marker carries that diplotype, or
clinically validate Yeliztli's conservative policy. No treatment-effect,
population-frequency, or clinical-outcome claim is introduced.

## Primary sources and claim mapping

All sources were accessed 2026-11-08.

| Existing classification checked | Defining document | Registry implementation |
| --- | --- | --- |
| DPYD `*2A/*2A`: Poor Metabolizer | CPIC 2017 fluoropyrimidine guideline, Table 1; [PMID:29152729](https://pubmed.ncbi.nlm.nih.gov/29152729/), [DOI:10.1002/cpt.911](https://doi.org/10.1002/cpt.911) (accessed 2026-11-08) | CPIC `diplotype`, source name `c.1905+1G>A (*2A)/c.1905+1G>A (*2A)` (accessed 2026-11-08) |
| CYP2B6 `*18/*18`: Poor Metabolizer | CPIC efavirenz guideline, Table 1; [PMID:31006110](https://pubmed.ncbi.nlm.nih.gov/31006110/), [DOI:10.1002/cpt.1477](https://doi.org/10.1002/cpt.1477) (accessed 2026-11-08) | CPIC `diplotype`, `*18/*18` (accessed 2026-11-08) |
| CYP2C9 `*3/*3`: Poor Metabolizer | CPIC 2020 phenytoin guideline, Table 2; [PMID:32779747](https://pubmed.ncbi.nlm.nih.gov/32779747/), [DOI:10.1002/cpt.2008](https://doi.org/10.1002/cpt.2008) (accessed 2026-11-08) | CPIC `diplotype`, `*3/*3` (accessed 2026-11-08) |
| UGT1A1 `*28/*28`: Poor Metabolizer | CPIC atazanavir guideline, Table 1; [PMID:26417955](https://pubmed.ncbi.nlm.nih.gov/26417955/), [DOI:10.1002/cpt.269](https://doi.org/10.1002/cpt.269) (accessed 2026-11-08) | CPIC `diplotype`, `*28/*28` (accessed 2026-11-08) |

The defining papers and CPIC registry are **not independent**: the registry
implements the same classification standard. This is the repository's
definitional-claim exception, not two independent studies of clinical effects.
Consensus and Scite are discovery/checking tools, not additional scientific
sources. Each paper counts once.

The API comparisons cover all 70 currently bundled diplotypes for these genes:
DPYD 21, CYP2B6 15, CYP2C9 28, UGT1A1 6. All phenotype labels agree after the
documented DPYD alias/order mapping in `queries.json`. Each response's exact
`Content-Range` total equals its saved row count. The API supplies numeric scores
for DPYD and CYP2C9. For CYP2B6 and UGT1A1 it returns `n/a`; their existing
repository numeric ordering values are therefore **not claimed as CPIC API
activity scores**. This change preserves those values and uses only existing
diplotype rows.

## Software evidence and limits

The new tests call the production star-allele caller, generate prescribing
alerts from the real bundled tables, and persist/read findings. They cover all
four policy genes, fully assayed homozygous-reference controls, one or two
observed alleles, absent homozygote rows, stable ties, and score-specific rows
within one phenotype band. The initial 15-case baseline had nine discriminating
failures and six passing controls. After adding compound-tie and same-band-score
cases, removing only the new homozygote branch makes ten of the 17 cases fail
while seven controls pass; the original source is then restored byte-for-byte.
Tests establish software behavior, not clinical validity.

The existing CYP3A5 direction-conflict withholding policy remains separate and
is exercised by the affected PGx suite. The change does not expand either policy
set. Reanalysis produces the corrected stored results; automatic refresh of
previously stored PGx results is the separate lifecycle design tracked by
#2454/#2304. No upstream CPIC release tag is changed.

## Retrieval, versions, and editorial checks

- Consensus discovery and Scite verification preceded the targeted Entrez and
  registry lookups. Four retrieved primary records were checked by DOI. Scite
  reported no editorial notices or contrasting-evidence flags; PubMed returned
  no correction/retraction links for these records as accessed 2026-11-08.
- PubMed metadata and the DPYD/CYP2C9 primary classification tables were read
  through the NCBI Entrez skill. The CYP2B6/UGT1A1 tables were read at PMC.
  `raw/primary-classification-facts.json` retains only the narrow factual mapping,
  not article text or author information.
- The CPIC API is a dated live snapshot, not an inferred release. `queries.json`
  records exact URLs, field selections, row counts, source-native DPYD names and
  repository mappings. No coordinate transformation or new genomic input occurs.
- Access fallbacks are recorded in `raw/discovery-metadata.json`: JavaScript-only
  guideline redirects, PMC browser challenges, and the initial urllib HTTP 403.
  The repository's established curl transport returned complete API responses.

## Payloads and rights

Repository-relative payload root:
`data/science-evidence/2026-11-08-untyped-diplotypes-2453/`.

- `raw/cpic-*-bundled-diplotypes.json`: source-native public CPIC fields only.
- `raw/pubmed-metadata.xml`: IDs, titles, journal metadata, publication types and
  editorial-link metadata; no abstracts, authors, affiliations or contacts.
- `raw/primary-classification-facts.json`: four narrow defining-table facts.
- `raw/discovery-metadata.json`: sanitized queries and editorial-check results;
  no institutional-access links or account metadata.
- `raw-hashes.json`: SHA-256 hashes of the retained payloads.

CPIC curated content is CC0 1.0 under its
[license](https://github.com/cpicpgx/cpic-data/blob/main/LICENSE.md)
(recorded blob `81c504256332202d6527205483ab3de8789a82cc`);
the API/database also links [ClinPGx data-use terms](https://www.clinpgx.org/page/dataUsagePolicy)
(accessed 2026-11-08), as recorded in the same-day #2455 packet. PubMed metadata
is retained with NCBI attribution under its
[data policies](https://www.ncbi.nlm.nih.gov/home/about/policies/) (accessed 2026-11-08).
Article full-text rights are not asserted or redistributed. No private samples,
real genotypes, credentials, or infrastructure locations are included.
