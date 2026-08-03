# Breast-cancer PRS77 availability evidence for #2028

## Claim and scope

`docs/modules/health-risk/cancer.md` states that the bundled breast-cancer PRS77
model is **not scored or reported**, and cites two sources to identify what is
being withheld and where its allele audit came from:

- reference `[2]` identifies the published model the bundled weight set derives
  from (Mavaddat et al., 77-SNP breast-cancer PRS);
- reference `[3]` identifies the resource used for the bundled GRCh38 allele
  audit recorded at `weight_sets[0].model_provenance.current_allele_audit` in
  `backend/data/panels/cancer_prs_weights.json`.

**This packet supports source identity and provenance only.** The documentation
change asserts no biological, statistical, or clinical claim about breast-cancer
risk — it *withholds* one. Nothing here should be read as Yeliztli endorsing,
reproducing, or validating the published risk estimates: the model is fail-closed
at runtime and no score or percentile is produced for any user.

## Sources

- Mavaddat N, Pharoah PDP, Michailidou K, et al. *Prediction of breast cancer
  risk based on profiling with common genetic variants.* Journal of the National
  Cancer Institute. 2015;107(5):djv036.
  PMID:25855707; DOI:10.1093/jnci/djv036 (accessed 2026-08-03).
  PubMed publication types: `Journal Article`, `Research Support, N.I.H.,
  Extramural`, `Research Support, Non-U.S. Gov't`. 212 authors.
- Hunt S, McLaren W, Gil L, et al. *Ensembl variation resources.* Database.
  2018;2018. DOI:10.1093/database/bay119 (accessed 2026-08-03).
  Gold open access under CC BY; the version is not stated by Scite or by the
  publisher article page, so no version is asserted.

## Evidence route and checks

The repository's ladder is Consensus and Scite first, then the narrowest
specialist connector. All three tiers ran on 2026-08-03 and none was skipped.

- **Consensus** (first tier) returned the Mavaddat 2015 paper as its rank-1
  result for the 77-SNP breast-cancer PRS query, matching reference `[2]`'s
  title, first author, year, and journal.
- **Scite** (first tier) resolved both DOIs directly. It returned **zero
  editorial notices** for each — no retraction, correction, expression of
  concern, or erratum — which is this packet's correction/retraction screening.
  It also recorded the Ensembl paper's CC-BY licence.
- **PubMed** (narrowest specialist) confirmed that PMID:25855707 and
  DOI:10.1093/jnci/djv036 are the same record, with the journal, volume, issue,
  and 2015 publication date used in reference `[2]`.

### Independence and the two-source rule

The high-stakes two-independent-source rule is **not engaged**, because this
packet asserts no patient-specific or quantitative clinical claim; it records
which published model and which annotation resource the bundled panel points at.

That distinction is recorded deliberately rather than assumed. Consensus also
surfaced an independent population validation of PRS77 (Hovhannisyan et al.,
*Cancer*, 2024) with a different cohort and author group. It is **not** relied on
here: were Yeliztli ever to score this model, that validation would be part of
the evidence needed, and this packet must not be mistaken for that work.

### Scite citation tallies (context, not evidence of correctness)

Retained because a contrasting-citation count is a screening signal, not because
citation counts establish a claim:

- DOI:10.1093/jnci/djv036 — 550 total, 33 supporting, 3 contrasting, 496
  mentioning, 571 citing publications.
- DOI:10.1093/database/bay119 — 373 total, 8 supporting, 0 contrasting, 357
  mentioning, 505 citing publications.

## Versions, licences, and retention basis

`queries.json` carries a `source_versions_and_licenses` block recording, for each
source and each service, its version/build, its licence or terms, and the basis
on which the retained payload may be kept. Where a field genuinely does not
exist it is recorded as unavailable together with the boundary that follows,
rather than left blank. In summary:

| Source / service | Version or build | Licence or terms | What is retained |
| --- | --- | --- | --- |
| PMID:25855707 / DOI:10.1093/jnci/djv036 | Version of record, JNCI 107(5); none issued beyond the DOI | **CC BY 3.0** (article and supplement) | Bibliographic metadata only; no abstract, no full text |
| DOI:10.1093/database/bay119 | Version of record, Database vol. 2018 | CC BY, **version not established** | Bibliographic metadata only |
| Consensus | **Unavailable** — no service or index-build stamp | Consensus terms; discovery aid only | Query, result ranks, hit identity |
| Scite | **Unavailable** — no service or index-build stamp | Scite terms; screening aid only | Bibliographic fields, editorial-notice outcome, point-in-time tallies |
| PubMed connector | **Unavailable** for this record | NCBI/NLM public data, no signed licence required | Bibliographic metadata only |

Three consequences follow and are stated plainly.

First, neither discovery service exposes a version or index build, so the
2026-08-03 access date is the only retrieval-version boundary and neither result
is reproducible against a pinned index state; the citation tallies in particular
will drift.

Second, no claim in this packet rests on provider output — Consensus was used to
discover the primary source and Scite to screen it for retractions, and every
claim rests on the primary records themselves. That distinction matters for the
licence too: Scite reports the Mavaddat record as *bronze* open access, which
describes reader access at the publisher and is **not** evidence that no licence
exists. This repository's own source notice
(`backend/data/sources/breast_prs77/NOTICE.md`) and the bundled panel's
`license_basis` field both record the article and its supplement as **CC BY
3.0**, and that explicit record governs.

Third, the two retained payloads disagree on the Mavaddat publication date —
PubMed records 2015-04-08, Scite records 2015-04-02 — and neither service labels
the semantics of its date field. The packet deliberately does **not** pick a
winner or invent an online-versus-issue explanation. Both are retained with their
source named, `DOI:10.1093/jnci/djv036` is the stable identifier, and no claim
uses either date as a version boundary.

## Sanitization

Sanitized payloads live under `raw/`. Consistent with this repository's other
evidence packets:

- **no** publisher-supplied abstract is retained for any record, including the
  openly licensed Ensembl one. That is this packet's metadata-only choice, not a
  licence consequence — both sources are CC BY, so retention would have been
  permitted;
- author affiliation and contact fields are **not retained at all** (rather than
  email-redacted), and the 212-author Mavaddat list is reduced to its leading ten
  with the full count recorded;
- Scite's resolved publisher access links are signed, expiring redirects and are
  **not** retained; the canonical DOI is retained instead;
- no genotype, sample, or user data of any kind is present. Every input sent to
  an external service was a public bibliographic identifier or a public search
  phrase.

## Claim mapping

| Documentation | Claim | Source |
| --- | --- | --- |
| `docs/modules/health-risk/cancer.md` reference `[2]` | Identity of the withheld published 77-SNP model | PMID:25855707 / DOI:10.1093/jnci/djv036 |
| `docs/modules/health-risk/cancer.md` reference `[3]` | Resource behind the bundled GRCh38 allele audit | DOI:10.1093/database/bay119 |

The locus counts quoted in that page (39 multiallelic primary loci, 2 additional
palindromic biallelic loci, 41 runtime-blocked) are **not** sourced from either
paper. They are read from the bundled panel's own
`model_provenance.current_allele_audit`, and
`tests/backend/test_cancer_prs_docs_consistency.py` derives the expected numbers
from that panel so a regenerated audit reddens the guard rather than leaving the
page asserting stale counts.

### Known gap: the allele audit is not independently reproducible

The bundled panel records the resource name, the assembly, a mapping policy and
the check date `2026-07-16` — but **no Ensembl Variation release or database
build, no query, and no stored response**. Ensembl Variation evolves, so a date
alone cannot establish which database state produced the 39/2/41 classification.

That provenance is deliberately **not** reconstructed here. The audit predates
this pull request and its inputs are not in this repository, so supplying a
release number or query would mean inventing provenance — and a fabricated build
identifier is worse than a recorded gap, because it reads as verified.

This is a pre-existing limitation of the panel, not something this documentation
change introduces: the PR reports counts the panel already carries and ties the
documentation to those stored values. The consequence is stated plainly — the
counts are reproducible against the bundled panel, but not independently against
Ensembl Variation. Issue #2246 tracks fixing the panel's audit provenance.

The exact sanitized queries and service outcomes are recorded in `queries.json`.
