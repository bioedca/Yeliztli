# CYP2B6 / efavirenz recommendation evidence (#2012)

Accessed 2026-07-31. This packet contains only public, non-personal scientific
metadata and public CPIC API records.

## Decision

The shipped CYP2B6 efavirenz recommendations must preserve CPIC's preemptive
starting-dose instruction:

- Intermediate Metabolizer: `Consider initiating efavirenz with decreased dose
  of 400 mg/day`
- Poor Metabolizer: `Consider initiating efavirenz with decreased dose of 400
  or 200 mg/day`

The previous Intermediate row instead retained the 600 mg label dose until CNS
side effects occurred. That changed the timing and therefore the operative
instruction. The Poor row also omitted CPIC's 200 mg option.

This change copies the normative recommendation text from CPIC rather than
inventing a clinical paraphrase. The repository retains its existing local
classification and guideline URL fields; issue #2135 owns the broader
authority-snapshot and CPIC-classification conformance work.

## Queries and raw payloads

1. `GET https://api.cpicpgx.org/v1/drug?name=eq.efavirenz&select=drugid,name`
   returned `RxNorm:195085`.
2. `GET https://api.cpicpgx.org/v1/recommendation?drugid=eq.RxNorm%3A195085&select=guidelineid,lookupkey,drugrecommendation,classification,phenotypes`
   returned guideline ID `104245` and the recommendations above. The complete
   selected response is at
   `data/science-evidence/2026-07-31-cyp2b6-efavirenz-2012/raw/cpic_efavirenz_recommendations.json`.
3. NCBI Entrez `esummary`, database `pubmed`, IDs
   `31006110,19433561,21447868,26044067`, `retmode=json`. A sanitized copy of
   the identifying response fields is at
   `data/science-evidence/2026-07-31-cyp2b6-efavirenz-2012/raw/pubmed_esummary_sanitized.json`.
4. Retraction/correction query:
   `(31006110[pmid] OR 19433561[pmid] OR 21447868[pmid] OR
   26044067[pmid]) AND (retracted publication[pt] OR retraction of
   publication[pt] OR published erratum[pt] OR corrected and republished
   article[pt])`. Entrez returned zero records.

The CPIC endpoint is API v1 and returned PostgREST records keyed by guideline
ID `104245`; the selected response does not expose a separate data-release
version. The concrete access date and complete request URL therefore form the
versioned retrieval identity.

## Source and claim mapping

| Claim | Sources | Mapping |
| --- | --- | --- |
| CPIC currently says to initiate 400 mg/day for Intermediate and 400 or 200 mg/day for Poor Metabolizers. | CPIC API guideline `104245`; PMID:31006110; DOI:10.1002/cpt.1477 | The API is the current normative record. The published guideline independently fixes its publication identity, but is the same CPIC assertion and is not counted as an independent clinical dataset. |
| A 400 mg Intermediate and 200 mg Poor regimen is pharmacokinetically supported. | PMID:19433561; DOI:10.1128/AAC.01537-08 | A population-PK study of 131 patients (32 genotyped) directly derived those two genotype-stratified doses for target exposure. |
| Reductions to 400 or 200 mg can retain target exposure and virologic suppression. | PMID:21447868; DOI:10.3851/IMP1742 | A separate multicenter therapeutic-drug-monitoring cohort reduced high-exposure patients to 400 or 200 mg and reported maintained suppression at six months. |
| 400 mg was not virologically inferior to 600 mg and produced fewer efavirenz-related/CNS adverse events. | PMID:26044067; DOI:10.1002/cpt.156 | ENCORE1 was a separate randomized cohort and supports the 400 mg efficacy/safety premise used by CPIC. |

PMID:19433561 and PMID:21447868 have different investigators, study designs,
participants, and source datasets; neither reuses the other's cohort.
PMID:26044067 is a third, randomized ENCORE1 dataset. These independent sources
agree on the feasibility of lower dosing and do not share a cohort or
patient-level upstream assertion. The therapeutic-drug-monitoring study does
not itself establish preemptive phenotype-specific timing. CPIC synthesizes
these results with additional evidence and is the authority for that exact
timing and phenotype-specific wording.

## Availability and licensing

- Consensus searches for `CYP2B6 efavirenz intermediate metabolizer initiate
  400 mg/day CPIC guideline human:true` and `CYP2B6 efavirenz dose guideline`
  both returned `INVALID_ARGUMENT`.
- Scite lookup for DOI `10.1002/cpt.1477` with the targeted recommendation
  terms returned `INVALID_ARGUMENT`.
- NCBI Entrez and CPIC's public API were available. PubMed records were indexed
  for MEDLINE and had no retraction/correction/erratum publication-type hit on
  the access date.
- The CPIC API response does not state a license. It is retained here only as a
  minimal factual provenance record with source attribution; no license beyond
  the source's terms is asserted.
- PubMed bibliographic metadata is retained with NLM attribution. Article
  copyrights and licenses remain with their publishers; no article full text
  is redistributed in this packet.
