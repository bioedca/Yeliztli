# ClinGen risk-allele terminology evidence for #2052

## Claim and scope

The source below supports the terminology used to distinguish
lower-penetrance/risk-allele classifications from ordinary high-penetrance
Mendelian P/LP findings. It is cited only to explain Yeliztli's existing stored
output category; this change does not add or alter variant classification behavior.

## Source

- Schmidt RJ, et al. *Recommendations for risk allele evidence curation,
  classification, and reporting from the ClinGen Low Penetrance/Risk Allele
  Working Group.* Genetics in Medicine. 2024;26(3):101036.
  PMID:38054408; DOI:10.1016/j.gim.2023.101036 (accessed 2026-07-31).
- PubMed record status: indexed for MEDLINE.
- PubMed metadata source: NCBI Entrez E-Utilities `esummary`, response version
  `0.3` (accessed 2026-07-31).
- PubMed typed correction/retraction source: NCBI Entrez E-Utilities `efetch`
  PubMed XML (record revised 2026-05-18; accessed 2026-07-31).
- Sanitized raw metadata and record payloads:
  `data/science-evidence/2026-07-31-clingen-risk-allele-2052/raw/pubmed-esummary-38054408.json`.
  `data/science-evidence/2026-07-31-clingen-risk-allele-2052/raw/pubmed-efetch-38054408.xml`.
  The publisher-supplied `Abstract` (including its all-rights-reserved
  `CopyrightInformation`) is removed, and public author contact email addresses
  are replaced with `[redacted-email]`; the remaining typed record structure is
  unchanged. The source-native EFetch response itself starts its first
  `ReferenceList/Citation` at `ssing heritability of complex diseases`; a fresh
  response on the access date reproduced that upstream truncation. It is retained
  verbatim, is not repaired from another source, and is not used to support this
  packet's claim.
- Sanitized raw NLM terms snapshot:
  `data/science-evidence/2026-07-31-clingen-risk-allele-2052/raw/nlm-copyright-download-terms-2026-07-31.html`.
  Site chrome, scripts, analytics, styling, and unrelated download links are
  removed; the copyright and terms text relevant to this packet is retained
  verbatim.
- The packet stores bibliographic metadata only, not the publisher abstract or
  article full text. NLM's [Copyright Information & Downloading NLM
  Data](https://www.nlm.nih.gov/databases/download.html) terms state that no
  signed license is needed for publicly accessible NLM data, subject to source
  acknowledgement, non-endorsement, and currentness disclosure (accessed
  2026-07-31). This packet identifies NLM/PubMed as the source, claims no
  endorsement, and preserves its access date as a fixed historical snapshot.

## Evidence route and checks

- Consensus query attempted first: unavailable because the monthly 250-search
  quota was exhausted; the service reported an August 1 reset.
- Scite DOI lookup attempted first: unavailable because the monthly MCP quota
  was exhausted; the service reported a 2026-08-03 UTC reset.
- Narrow Life Science Research fallback:
  `life-science-research:ncbi-entrez-skill` ran its bundled
  `scripts/ncbi_entrez.py` client with the recorded PubMed `esummary` request.
  It returned `ok=true`, source `ncbi-entrez`, no warnings, and the expected
  title for PMID:38054408 (accessed 2026-07-31).
- NCBI Entrez verified the title, journal, publication date, PMID, DOI, and
  collective author.
- Correction/retraction screening: the captured PubMed EFetch XML has no typed
  `CommentsCorrectionsList` element for PMID:38054408, and its publication types
  are `Journal Article` and `Research Support, N.I.H., Extramural` (accessed
  2026-07-31). Scite remained unavailable because of the quota above.
- This terminology/provenance statement is not a patient-specific or
  quantitative clinical claim, so the high-stakes two-independent-source rule
  does not apply.

## Claim mapping

- `docs/modules/health-risk/cancer.md`
- `docs/modules/health-risk/cardiovascular.md`
- `docs/modules/health-risk/carrier-status.md`
- `docs/modules/rare-variants.md`
- `docs/modules/interpretation-reference.md`

The exact sanitized queries and service outcomes are recorded in
`queries.json`.
