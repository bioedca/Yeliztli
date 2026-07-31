# LRRK2 G2019S penetrance documentation evidence for issue #2091

## Scope

This packet supports the narrow user-facing statement that published
age-80 penetrance estimates for LRRK2 G2019S are reduced, age-dependent, and
cohort-specific. It does not convert the cited population estimates into an
individual prediction.

Repository base: `823544d5fd6661e34844963148a76436b9c0e815`

Access date: `2026-07-31`

## Sanitized discovery queries

Consensus:

> LRRK2 G2019S penetrance by age 80 24% 49% 25% 42.5% cohort ancestry
> human:true

Result: unavailable because the monthly search quota was exhausted; the service
reported that it resets on 2026-08-01.

Scite:

> LRRK2 G2019S penetrance age 80 24 49 42.5 cohort ancestry

Result: unavailable because the monthly MCP quota was exhausted; the service
reported that it resets on 2026-08-03 UTC.

NCBI Entrez:

- `esearch`, database `pubmed`, query
  `(LRRK2[Title/Abstract] OR G2019S[Title/Abstract]) AND
  (penetrance[Title/Abstract] OR lifetime risk[Title/Abstract])`
- `esummary`, database `pubmed`, IDs
  `26062626,28639421,38804604,40926580`, JSON
- `efetch`, database `pubmed`, the same four IDs, XML; inspect
  `MedlineCitation/CommentsCorrectionsList`

## Primary sources and claim mapping

1. Marder K et al. “Age-specific penetrance of LRRK2 G2019S in the
   Michael J. Fox Ashkenazi Jewish LRRK2 Consortium.”
   `PMID:26062626`; `DOI:10.1212/WNL.0000000000001708`
   (accessed 2026-07-31).
   - The kin-cohort analysis estimated cumulative Parkinson disease risk of
     26% by age 80 (95% CI 18–36%) among Ashkenazi-Jewish carriers.
2. Lee AJ et al. “Penetrance estimate of LRRK2 p.G2019S mutation in
   individuals of non-Ashkenazi Jewish ancestry.”
   `PMID:28639421`; `DOI:10.1002/mds.27059`
   (accessed 2026-07-31).
   - The distinct non-Ashkenazi kin cohort estimated 42.5% by age 80
     (95% CI 26.3–65.8%) and summarized the populations then analyzed as
     approximately 25–42.5%.
3. Kmiecik MJ et al. “Genetic analysis and natural history of Parkinson's
   disease due to the LRRK2 G2019S variant.”
   `PMID:38804604`; `DOI:10.1093/brain/awae073`
   (accessed 2026-07-31).
   - A prospective 23andMe cohort estimated 49% cumulative incidence by
     age 80.
4. Kmiecik MJ et al. “Genetic Modifiers of Parkinson's Disease: A
   Case-Control Study.”
   `PMID:40926580`; `DOI:10.1002/acn3.70176`
   (accessed 2026-07-31).
   - A later 23andMe and Fox Insight Genetic Substudy analysis estimated
     24% cumulative incidence by age 80 for LRRK2 p.G2019S carriers.

The high-stakes conclusion is deliberately limited: the independent
kin-cohort and 23andMe participant streams agree that penetrance is incomplete
and age-dependent, while their differing estimates support explicit
cohort/method framing instead of a single personal-risk number. The 2015
Ashkenazi-Jewish and 2017 non-Ashkenazi kin cohorts used the same analytical
method and overlapping investigator network but distinct probands and
relatives. The 2024 23andMe cohort is participant-independent of those kin
cohorts. The 2025 study shares 23andMe data and investigators with the 2024
study, so it is not counted as an independent replication; it is cited only for
its source-specific 24% estimate.

## Corrections and retractions

PubMed EFetch records for all four PMIDs were retrieved on 2026-07-31 and
inspected at `MedlineCitation/CommentsCorrectionsList`, where PubMed records
linked corrections, retractions, expressions of concern, and related notices.
The element was absent from all four records. This records PubMed's state on
the access date, not a guarantee that no later notice will appear.

The corrections snapshot preserves every `MedlineCitation` record and source
element except the publisher-text containers `Abstract`, `OtherAbstract`,
`ReferenceList`, and `CoiStatement`, and one author email address was removed
from an affiliation string. It explicitly retains `CommentsCorrectionsList`
whenever present. A separate minimal claim snapshot retains the exact
claim-bearing sentence from each source's `AbstractText` element and omits all
author, affiliation, participant-level, and unrelated abstract content. Both
snapshots derive from the same EFetch response, whose raw SHA-256 is recorded.
The exact request, hashes, sanitizer removals, revision dates, source XPath
locations, and extracted notice results are recorded in:
`data/science-evidence/2026-07-31-lrrk2-penetrance-docs-2091/pubmed-comments-corrections-extract.json`.

## Payload format and usage

- `pubmed-esummary.json` is a minimal sanitized derivative of the public NCBI
  E-utilities citation-metadata response. It retains only each record's PMID,
  DOI, title, journal abbreviation, and publication date; author, history,
  identifier-array, and other unused response metadata are omitted.
- `pubmed-claim-excerpts.json` retains four exact, source-native
  claim-bearing `AbstractText` sentences, with PMID, DOI, source XPath, request,
  and raw-response hash. These sentences are the durable source evidence for
  the four age-80 estimates mapped above.
- `pubmed-efetch-sanitized.xml` is the auditable source snapshot used for the
  corrections/retractions check.

The live PubMed service exposes no frozen database build identifier, so the
access date is the packet's retrieval-version boundary. The packet contains no
article full text or full abstract, author email addresses, participant-level
data, genotypes, credentials, or restricted information.

NCBI's [Website and Data Usage Policies and Disclaimers](https://www.ncbi.nlm.nih.gov/home/about/policies/)
(accessed 2026-07-31) state that U.S.-government-created site information is
public domain and may be copied with acknowledgment, while contributed or
licensed material may remain protected. The
[E-utilities usage requirements](https://www.ncbi.nlm.nih.gov/books/NBK25497/)
(accessed 2026-07-31) require the NCBI disclaimer/copyright notice to remain
evident when E-utilities are used in software. This packet therefore preserves
NCBI attribution, excludes publisher-text containers and author email
addresses, and asserts no blanket license over publisher-contributed material.
