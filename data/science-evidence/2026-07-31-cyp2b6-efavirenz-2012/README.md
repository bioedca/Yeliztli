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
4. NCBI Entrez `esummary` and `efetch`, database `pubmed`, IDs
   `26622191,17918089`. The public metadata response and a sanitized extract of
   the abstract fields that bear on genotype-guided initiation are at
   `data/science-evidence/2026-07-31-cyp2b6-efavirenz-2012/raw/pubmed_genotype_guided_initiation_esummary.json`
   and
   `data/science-evidence/2026-07-31-cyp2b6-efavirenz-2012/raw/pubmed_genotype_guided_initiation_extract.json`.
5. Retraction/correction queries:
   `(31006110[pmid] OR 19433561[pmid] OR 21447868[pmid] OR
   26044067[pmid]) AND (retracted publication[pt] OR retraction of
   publication[pt] OR published erratum[pt] OR corrected and republished
   article[pt])` and the same publication-type filter for
   `(26622191[pmid] OR 17918089[pmid])`. Entrez returned zero records for both.

The CPIC endpoint is API v1 and returned PostgREST records keyed by guideline
ID `104245`; the selected response does not expose a separate data-release
version. The concrete access date and complete request URL therefore form the
versioned retrieval identity.

## Source and claim mapping

| Claim | Sources | Mapping |
| --- | --- | --- |
| CPIC currently says to initiate 400 mg/day for Intermediate and 400 or 200 mg/day for Poor Metabolizers. | CPIC API guideline `104245` (accessed 2026-07-31); PMID:31006110 (accessed 2026-07-31); DOI:10.1002/cpt.1477 (accessed 2026-07-31) | The API is the current normative record. The published guideline independently fixes its publication identity, but is the same CPIC assertion and is not counted as an independent clinical dataset. |
| A 400 mg Intermediate and 200 mg Poor regimen is pharmacokinetically supported. | PMID:19433561 (accessed 2026-07-31); DOI:10.1128/AAC.01537-08 (accessed 2026-07-31) | A population-PK study of 131 patients (32 genotyped) directly derived those two genotype-stratified doses for target exposure. |
| Reduced-dose efavirenz can be selected before treatment from a poor-metabolizer genotype rather than only after side effects. | PMID:17918089 (accessed 2026-07-31); DOI:10.1086/522175 (accessed 2026-07-31) | A Japanese multicenter study genotyped 456 patients. Five efavirenz-naive CYP2B6 \*6/\*6 or \*6/\*26 carriers began at 400 mg/day; two with persistently high concentrations were then reduced to 200 mg/day and retained HIV-1 suppression. |
| A separate prospective randomized cohort supports genotype selection before the initial ART dose. | PMID:26622191 (accessed 2026-07-31); DOI:10.2147/PGPM.S86446 (accessed 2026-07-31) | In a 24-patient Thai pilot, assignment to CYP2B6 testing occurred before ART initiation; the guided arm initiated \*6/\*6 carriers at 400 mg/day and other genotypes at 600 mg/day. Only two poor metabolizers were enrolled, so this is timing evidence, not a precise effect-size estimate. |
| Reductions to 400 or 200 mg can retain target exposure and virologic suppression. | PMID:21447868 (accessed 2026-07-31); DOI:10.3851/IMP1742 (accessed 2026-07-31) | A separate multicenter therapeutic-drug-monitoring cohort reduced high-exposure patients to 400 or 200 mg and reported maintained suppression at six months. |
| 400 mg was not virologically inferior to 600 mg and produced fewer efavirenz-related/CNS adverse events. | PMID:26044067 (accessed 2026-07-31); DOI:10.1002/cpt.156 (accessed 2026-07-31) | ENCORE1 was a separate randomized cohort and supports the 400 mg efficacy/safety premise used by CPIC. |

PMID:17918089 and PMID:26622191 independently establish that CYP2B6 genotype
can select a reduced efavirenz dose before treatment rather than only in
reaction to side effects. They used different investigators, countries,
participants, designs, and source datasets. PMID:19433561 independently maps
the Intermediate and Poor phenotypes to 400 and 200 mg/day for target exposure;
PMID:26044067 independently establishes that initiating 400 rather than 600
mg/day does not reduce virologic efficacy. PMID:21447868 supplies a further
independent monitored 400/200 mg reduction cohort. None reuses another's
participants or patient-level upstream assertion. The direct initiation
cohorts are small and do not by themselves establish the entire CPIC phenotype
matrix; CPIC remains the authority for the exact moderate, consider-initiating
wording, while the independent cohorts and PK/noninferiority studies agree on
its timing, dose, exposure, and efficacy premises.

## Availability and licensing

- Consensus searches for `CYP2B6 efavirenz intermediate metabolizer initiate
  400 mg/day CPIC guideline human:true` and `CYP2B6 efavirenz dose guideline`
  both returned `INVALID_ARGUMENT`.
- Scite lookup for DOI `10.1002/cpt.1477` with the targeted recommendation
  terms returned `INVALID_ARGUMENT`.
- NCBI Entrez and CPIC's public API were available. The six PubMed records had
  no retraction/correction/erratum publication-type hit on the access date.
- The CPIC API response does not state a license. It is retained here only as a
  minimal factual provenance record with source attribution; no license beyond
  the source's terms is asserted.
- PubMed bibliographic metadata is retained with NLM attribution. Article
  copyrights and licenses remain with their publishers; no article full text
  is redistributed in this packet.
