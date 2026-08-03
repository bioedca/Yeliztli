# ClinGen risk-allele terminology evidence for #2052

## Claim and scope

The source below supports the terminology used to distinguish
lower-penetrance/risk-allele classifications from ordinary high-penetrance
Mendelian P/LP findings. It is cited only to explain Yeliztli's existing stored
output category; this change does not add or alter variant classification behavior.

Two precision points, because the shorthand above can be misread:

- **Low-penetrance and risk-allele are two classes, not one.** The Working Group
  recognises risk alleles *and* low-penetrance variants as distinct variant
  classes from those causing highly penetrant disease. Yeliztli's single storage
  category `clinvar_low_penetrance_or_risk_allele` deliberately spans both, which
  is a software grouping and not a claim that the two are the same thing.
- **"Distinct from P/LP" means distinct from *ordinary high-penetrance Mendelian*
  P/LP.** A ClinVar value such as `Pathogenic, low penetrance` still carries a
  Pathogenic primary classification. Penetrance and pathogenicity are not mutually
  exclusive, and nothing here should be read as claiming every lower-penetrance
  finding is non-P/LP. The variant is routed out of the ordinary high-penetrance
  path because of its penetrance modifier, so a decreased-penetrance assertion is
  never presented as a standard high-penetrance result.

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

- Consensus query attempted first on 2026-07-31: unavailable because the monthly
  250-search quota was exhausted; the service reported an August 1 reset.
- Scite DOI lookup attempted first on 2026-07-31: unavailable because the monthly
  MCP quota was exhausted; the service reported a 2026-08-03 UTC reset.
- **Both first-tier services were re-run on 2026-08-03 after their reported quota
  resets, so this packet no longer rests on an unavailable first tier.**
    - Consensus returned this packet's primary source as its rank-1 result and the
      Working Group's own conference abstract as rank 3. Rank 2 is the 2019
      Senol-Cosar low-penetrance framework paper (PMID:31147632;
      DOI:10.1038/s41436-019-0560-8; *Genet Med.* 2019;21(12):2765-2773, accessed
      2026-08-03). It is recorded as the documented antecedent of the same
      terminology and **not** as an independent second source, because Schmidt RJ
      and Lebo MS author both it and PMID:38054408 and the 2024 recommendations
      build on that framework rather than testing it separately.
    - Scite verified the DOI, title, journal, volume/issue/page, and publication
      date, and returned **zero editorial notices** — no retraction, correction,
      expression of concern, or erratum. That is a second, independent
      correction/retraction screening agreeing with the NCBI EFetch typed
      `CommentsCorrectionsList` check recorded below. Scite's returned three-author
      subset is not the leading three of the NCBI author list, so authorship
      attribution follows the authoritative NCBI record.
    - Sanitized payloads: `raw/consensus-search-2026-08-03.json`,
      `raw/scite-doi-lookup-2026-08-03.json`,
      `raw/pubmed-31147632-metadata.json`.
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
  2026-07-31). The 2026-08-03 Scite screening above independently agrees.
- This terminology/provenance statement is not a patient-specific or
  quantitative clinical claim, so the high-stakes two-independent-source rule
  does not apply. That is stated explicitly because the only corroborating paper
  surfaced by Consensus shares two authors with the primary source and therefore
  could not satisfy that rule if it were engaged.

## Versions, licences, and retention basis

`queries.json` carries a `source_versions_and_licenses` block recording, for each
source and each service, its version/build, its licence or terms, and the basis
on which the retained payload may be kept. Fields that genuinely do not exist are
recorded as unavailable with the boundary that follows, rather than left blank.

| Source / service | Version or build | Licence or terms | What is retained |
| --- | --- | --- | --- |
| PMID:38054408 | Version of record; PubMed revision 2026-05-18 | **None stated** — bronze OA | Bibliographic metadata only |
| PMID:31147632 | Version of record, Genet Med 2019;21(12) | **Unavailable** — not retrieved | Bibliographic metadata only |
| NCBI Entrez / PubMed | esummary response version `0.3` | NLM public data; no signed licence required | Bibliographic metadata + retained terms snapshot |
| Consensus | **Unavailable** — no service or index-build stamp | Consensus terms; discovery aid only | Query, result ranks, hit identity |
| Scite | **Unavailable** — no service or index-build stamp | Scite terms; screening aid only | Bibliographic fields, editorial-notice outcome, point-in-time tallies |

Two consequences follow and are stated plainly. Neither discovery service exposes
a version or index build, so the access date is the only retrieval-version
boundary and neither result is reproducible against a pinned index state — the
citation tallies in particular will drift. And no claim in this packet rests on
provider output: Consensus was used to discover the primary source and Scite to
screen it for retractions, while every claim rests on the primary records.

## Claim mapping

- `docs/modules/health-risk/cancer.md`
- `docs/modules/health-risk/cardiovascular.md`
- `docs/modules/health-risk/carrier-status.md`
- `docs/modules/rare-variants.md`
- `docs/modules/interpretation-reference.md`

The exact sanitized queries and service outcomes are recorded in
`queries.json`.
