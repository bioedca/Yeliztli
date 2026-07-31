# ClinGen risk-allele terminology evidence for #2052

## Claim and scope

The source below supports the terminology used to distinguish
lower-penetrance/risk-allele classifications from ordinary high-penetrance
Mendelian P/LP findings. It is cited only to explain Yeliztli's existing output
tier; this change does not add or alter variant classification behavior.

## Source

- Schmidt RJ, et al. *Recommendations for risk allele evidence curation,
  classification, and reporting from the ClinGen Low Penetrance/Risk Allele
  Working Group.* Genetics in Medicine. 2024;26(3):101036.
  PMID:38054408; DOI:10.1016/j.gim.2023.101036 (accessed 2026-07-31).
- PubMed record status: indexed for MEDLINE.
- PubMed metadata source: NCBI Entrez E-Utilities `esummary`, response version
  `0.3` (accessed 2026-07-31).
- PubMed relationship source: NCBI Entrez E-Utilities `elink`, response version
  `0.3` (accessed 2026-07-31).
- Sanitized raw metadata and relationship payloads:
  `data/science-evidence/2026-07-31-clingen-risk-allele-2052/raw/pubmed-esummary-38054408.json`.
  `data/science-evidence/2026-07-31-clingen-risk-allele-2052/raw/pubmed-elink-38054408.json`.
- The packet stores bibliographic metadata only, not article full text. NCBI
  PubMed citation metadata is redistributed subject to NCBI's data-usage
  policies; the linked article remains under its publisher's license.

## Evidence route and checks

- Consensus query attempted first: unavailable because the monthly 250-search
  quota was exhausted; the service reported an August 1 reset.
- Scite DOI lookup attempted first: unavailable because the monthly MCP quota
  was exhausted; the service reported a 2026-08-03 UTC reset.
- NCBI Entrez verified the title, journal, publication date, PMID, DOI, and
  collective author.
- Correction/retraction screening: a dedicated PubMed ELink relationship check
  returned no correction, erratum, retraction, expression-of-concern, or update
  relationship for PMID:38054408 in the captured response (accessed 2026-07-31).
  Scite remained unavailable because of the quota above.
- This terminology/provenance statement is not a patient-specific or
  quantitative clinical claim, so the high-stakes two-independent-source rule
  does not apply.

## Claim mapping

- `docs/modules/health-risk/cancer.md`
- `docs/modules/health-risk/cardiovascular.md`
- `docs/modules/health-risk/carrier-status.md`

The exact sanitized queries and service outcomes are recorded in
`queries.json`.
