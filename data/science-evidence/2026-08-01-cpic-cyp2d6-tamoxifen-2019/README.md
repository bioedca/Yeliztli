# CYP2D6 / tamoxifen recommendation evidence (#2019)

Accessed 2026-08-01 from commit
`813d7568b286f2d76c2338a13f035aa3d8632234`. This packet contains only
public CPIC records and sanitized public bibliographic metadata. It contains no
patient data, genotypes, credentials, article full text, author or contributor
data, or host-specific paths.

## Decision and scope

The bundled CYP2D6/tamoxifen rows retain CPIC's current text as an **audit-only
source record** for the three phenotypes represented by this repository:

- Normal Metabolizer: avoid moderate and strong CYP2D6 inhibitors, then use
  standard tamoxifen dosing.
- Intermediate Metabolizer: consider the stated aromatase-inhibitor approach
  first; only if that approach is contraindicated, consider the stated
  FDA-approved 40 mg/day tamoxifen contingency; avoid strong-to-weak CYP2D6
  inhibitors.
- Poor Metabolizer: recommend the stated aromatase-inhibitor approach; retain
  CPIC's limited 40 mg/day contingency when aromatase inhibitors are
  contraindicated.

The prior local Intermediate row led with dose escalation and omitted the
conditional aromatase-inhibitor priority. All three prior local rows also
omitted at least part of CPIC's inhibitor warning. The source rows now preserve
the correct CPIC transcription, but they are **not prescribing output**:
`generate_prescribing_alerts` withholds every CYP2D6/tamoxifen pair and sample
schema v25 removes exactly fingerprinted historical alerts and finding-diff
entries. The application therefore makes no patient-specific CYP2D6/tamoxifen
treatment, dose, or inhibitor recommendation.

The CPIC API classification varies by CYP2D6 activity score: the retained
Normal and Poor Metabolizer records are `Strong`; the Intermediate records are
`Moderate` for activity scores 0.25, 0.5, and 0.75, and `Optional` at 1.0. The
repository's `classification=A` field is retained with the audit record; it is
not an active alert tier for this withheld pair and is not a claim that CPIC
labels every recommendation `A`. The raw response also contains phenotypes not
represented by the repository's current CYP2D6 diplotype table; they are outside
#2019's source-record scope.

## Queries and retained payloads

1. `GET https://api.cpicpgx.org/v1/drug?name=eq.tamoxifen&select=drugid,name`
   returned `RxNorm:10324` for tamoxifen. The minimal response is
   `raw/cpic-tamoxifen-drug.json`.
2. `GET https://api.cpicpgx.org/v1/recommendation?drugid=eq.RxNorm%3A10324&select=guidelineid,lookupkey,drugrecommendation,classification,phenotypes`
   returned current CPIC guideline ID `100415`, including the three copied
   phenotype recommendations. The complete selected response is retained as
   `raw/cpic-cyp2d6-tamoxifen-recommendations.json` after canonicalizing only
   insignificant trailing whitespace outside JSON strings for repository
   whitespace checks; no source field value changed.
3. NCBI Entrez `esummary`, database `pubmed`, IDs
   `29385237,26211827,27226358,23213055,21768473`, `retmode=json`. The
   sanitized metadata response is `raw/pubmed-esummary-sanitized.json`; it
   retains citation identifiers and dates but omits author and contributor
   fields.
4. NCBI Entrez `efetch`, database `pubmed`, the same IDs, `retmode=xml`.
   `raw/pubmed-efetch-sanitized.xml` retains only PMID, revised date, article
   title, PubMed/DOI IDs, and linked correction/retraction relationship tags.
   `raw/pubmed-comments-corrections-extract.json` records its sanitizer
   accounting and relationship summary.
5. Consensus search
   `CYP2D6 tamoxifen CPIC inhibitor endoxifen human:true` was available.
   Its fetched result `cce42d06ab1c5e0d99aba3646926ce87` described observed
   tamoxifen drug interactions by CYP2D6 phenotype and inhibitor potency.
   Consensus was used for discovery only; durable claim sources are CPIC and
   the PubMed-identified papers below.
6. Scite literature lookup for the targeted CPIC/tamoxifen evidence was
   attempted after Consensus but returned a monthly-quota `INVALID_ARGUMENT`.
   No plan, purchase, login, or fallback source was used.
7. `raw/clinical-validation-decision.json` is a sanitized public-source record
   of the independent-authority and study-independence check that led to
   withholding the clinical output. It contains identifiers, public URLs,
   access date, and concise decision-relevant scope only.

The CPIC endpoint is API v1 and exposes no separate source-data release in the
selected response. The API version, guideline ID, full request URL, payload
hash, and concrete access date are therefore the retrieval identity.

## Source-provenance mapping and clinical boundary

| Repository provenance claim | Sources | Mapping and boundary |
| --- | --- | --- |
| The three repository rows are a source-faithful copy of current CPIC API guideline `100415`, including the source text's inhibitor warning and conditional language. | CPIC API guideline `100415` (accessed 2026-08-01); PMID:29385237 (accessed 2026-08-01); DOI:10.1002/cpt.1007 (accessed 2026-08-01) | The API is the normative record for the copied wording. The publication establishes guideline identity; neither is counted as independent clinical-cohort evidence. The rows are audit provenance, never Yeliztli prescribing output. |
| The cited PubMed records are identifiers named in the copied CPIC source text and are retained so future reviewers can trace the source's references. | PMID:26211827 (accessed 2026-08-01); DOI:10.1016/S0140-6736(15)61074-1 (accessed 2026-08-01); PMID:27226358 (accessed 2026-08-01); DOI:10.1634/theoncologist.2015-0480 (accessed 2026-08-01); PMID:23213055 (accessed 2026-08-01); DOI:10.1158/1078-0432.CCR-12-2153 (accessed 2026-08-01); PMID:21768473 (accessed 2026-08-01); DOI:10.1200/JCO.2010.31.4427 (accessed 2026-08-01) | This is bibliographic provenance only. It does not independently validate comparative efficacy, dose-response, inhibitor effect size, or a patient-specific clinical outcome. |

## Clinical-validity decision

CPIC accurately establishes the wording being preserved, but cannot supply the
two independent clinical validations required for Yeliztli to present that
wording as its own treatment, dose, or inhibitor instruction. The additional
authority and independence check reaches a fail-closed result:

| Decision input | Durable source(s) | Result for this repository |
| --- | --- | --- |
| CYP2D6-guided adjuvant tamoxifen treatment selection | PMID:31236598 (accessed 2026-08-01); DOI:10.1093/annonc/mdz173 (accessed 2026-08-01); NCBI Bookshelf:NBK247013 (accessed 2026-08-01) | ESMO does not support CYP2D6-guided treatment selection outside a clinical trial, while the DPWG table uses a different intermediate-metabolizer presentation. These authorities do not provide the required agreement. |
| CYP2D6 genotype clinical outcome and inhibitor action | GOV.UK:tamoxifen-for-breast-cancer (accessed 2026-08-01) | MHRA limits its precaution to potent inhibitors where possible and describes genotype-outcome evidence as mixed or inconclusive. This does not validate the bundled blanket clinical action. |
| Conditional 40 mg dose escalation | PMID:21768473 (accessed 2026-08-01); DOI:10.1200/JCO.2010.31.4427 (accessed 2026-08-01); PMID:27226358 (accessed 2026-08-01); DOI:10.1634/theoncologist.2015-0480 (accessed 2026-08-01) | The later report is an expansion or secondary analysis of the same prospective dose-escalation study, rather than an independent clinical-outcome validation. |

The two-independent-source gate therefore fails for the displayed clinical
actions. Runtime generation withholds the pair and schema v25 removes only
exactly fingerprinted historical generated output. Any future effort to surface
it must first document two agreeing, cohort-independent sources and resolve the
identified authority conflict.

`raw/pubmed-claim-excerpts.json` is the corresponding concise source/citation
provenance crosswalk. It deliberately records citation identity and scope,
rather than article abstracts, article text, or new clinical conclusions.

## Corrections, retractions, sanitization, and integrity

The retained EFetch relationship extraction was inspected for correction and
retraction relationship types on all five source records. It contains two
`CommentIn` links (PMID:26211827 to PMID:26211823 and PMID:21768473 to
PMID:21768456), and no retained retraction or erratum relationship. This is a
relationship-field check on the access date, not a claim that no future notice
can exist. The exact retained IDs and link types are in
`raw/pubmed-comments-corrections-extract.json`.

The sanitized EFetch copy excludes five abstracts, 42 affiliation blocks, five
author lists, four reference lists, three conflict-of-interest statements, five
grant lists, and three email-address occurrences from the raw response. The
sanitized ESummary copy likewise excludes all author and contributor fields.
The original unsanitized responses are deliberately not retained in the
repository.

| Retained file | SHA-256 |
| --- | --- |
| `raw/cpic-tamoxifen-drug.json` | `87fc3d9eb10a5a67583170aaf270e4d1aac2523227043ee7f04dfa702f68524b` |
| `raw/cpic-cyp2d6-tamoxifen-recommendations.json` | `3d8b87e39867e532bbe48282fff34f9d6a9776bde73957056ad4c8df65dd2c78` |
| `raw/pubmed-esummary-sanitized.json` | `b80c2bd2fd70c12116d4094ef2ceb4ed335fa26e16f20517034bcb147874e5e6` |
| `raw/pubmed-efetch-sanitized.xml` | `3d24ebece7724793a626b31d2a52b96076ab2f7d226783efa939cc03bf3317dd` |
| `raw/pubmed-comments-corrections-extract.json` | `52b2d1ebdaa0b6743842294fe120be6472a24e6cefc62218c89e6ac6224f96f5` |
| `raw/pubmed-claim-excerpts.json` | `309d48a5046bcc7a36f2f64774501f1e5fa3ec99d2015f494b2371411efcc794` |
| `raw/clinical-validation-decision.json` | `114ae5a73757cb6c105c86cefbe16d65730bdadc043621737a9f7770ceacee1f` |

## Availability and licensing

- CPIC API v1, Consensus, and NCBI Entrez were available on the access date.
  Context7 library resolution returned no relevant CPIC API/library match, so
  no Context7 documentation was substituted for the authoritative CPIC record.
- Scite was quota-blocked as recorded above. This limitation is retained rather
  than masked by a guessed citation or paid access.
- The CPIC response does not state a license. It is stored only as a minimal
  factual provenance record with source attribution; no license beyond the
  source's terms is asserted.
- PubMed bibliographic metadata is retained with NLM attribution. Article
  copyrights and licenses remain with their publishers; no article full text
  is redistributed in this packet.
