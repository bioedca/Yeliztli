# Withholding the XXY screen's clean negative for an unresolvable X dosage (#2040)

## What this change claims

It **withholds** an affirmative negative. It asserts no positive finding, introduces no
threshold and moves none — the only change to a constant makes an already-calibrated bound
visible to a second module.

Exactly **one** background fact is load-bearing, and it is evidenced below with primary
sources. Everything else in the diff is software behaviour, verifiable from the code and its
tests.

## The load-bearing claim

**C1 — the 47,XXY (Klinefelter) karyotype is a supernumerary X on a Y-bearing karyotype; the
pattern requires a Y chromosome.**

This is why the new escalation is conditional on a chrY signal (`ambiguous_x and y_discordant`)
rather than unconditional: with no chrY signal an unresolved X dosage has nothing to pair with, so
this change leaves the screen's existing answer there untouched.

| Source | Kind | Independence |
| --- | --- | --- |
| `PMID:13632697` / `DOI:10.1038/183302a0` — Jacobs PA & Strong JA, *Nature* 1959, "A case of human intersexuality having a possible XXY sex-determining mechanism." **(accessed 2026-08-29)** | **Primary** — the original cytogenetic description | — |
| `PMID:1183067` / `DOI:10.1111/j.1399-0004.1975.tb01498.x` — Hamerton JL, et al., *Clin Genet* 1975, "A cytogenetic survey of 14,069 newborn infants." **(accessed 2026-08-29)** | **Primary** — consecutive-newborn karyotype survey | Disjoint authors, 16 years apart, different designs (single-case description vs population survey) and different cohorts. Neither derives from the other's data. |

Both are `Journal Article` primary literature, not reviews. An earlier revision of this packet
cited two review articles; that was the wrong tier.

**What C1 does *not* establish, and this packet does not claim:** that a consumer array with an
evaluable chrY rate at or below `THRESHOLD_Y_PAR_NOISE` (0.10) carries no Y. That is the sex
classifier's calibrated PAR-noise floor, shared with the screen since #1130 and documented in
`docs/internal/sex_inference_threshold_validation.md`. It is **pre-existing behaviour** that this
change neither introduces, moves nor re-derives, and the ambiguous-X / no-Y-signal arm keeps
exactly the answer the screen gave before. If that calibration is wrong, the negative it produces
was wrong before this change and is exactly as wrong after it.

> **Correction.** An earlier revision of this section used C1 as if a karyotype definition
> justified returning a clean negative at that measurement boundary. It cannot establish an array
> threshold's sensitivity. The sentence was withdrawn on 2026-09-05 after review; the arm is
> recorded above as unchanged pre-existing behaviour, which is all this change can claim about it.

## Retraction checking — method matters

Retraction status was checked through **PubMed EFetch typed `CommentsCorrections` relations**,
not publication type and not eLink. An original record keeps its own `pubtype` while
corrections, errata and retractions are carried as *separate linked records*, so a `pubtype`
scan does not do what it appears to. The EFetch XML for all three cited PMIDs is retained
(abstracts removed, nothing else touched) with a derived reduction of its notice fields:
**no record carries a `CommentsCorrections` link of any `RefType`** (`ErratumIn`, `RetractionIn`,
`PartialRetractionIn`, `ExpressionOfConcernIn`, `CorrectedandRepublishedIn`, `RepublishedIn`,
`UpdateIn`, `ReprintIn`, or any other). Scite's metadata for the same three DOIs **(accessed
2026-09-05)** reports no editorial notice either — a second notice source that does not share
PubMed's index.

> **Correction.** An earlier revision of this packet checked only the eLink `pubmed_pubmed*` link
> sets and read their silence as clearance. Those are generic related-article neighbourhoods and
> do not expose the typed relation, so that check could not have found a retraction. The eLink
> payloads are retained for what they are; the typed check above is the authority.

> **Correction.** This packet was rebuilt on 2026-08-29 but never reached the pull request's
> tree: `data/` is gitignored, the files were staged without `--force`, and the commit that
> claimed to restore it carried only a docs change. Review caught the absence on 2026-09-05; the
> packet was committed with `--force` the same day, with the retraction check upgraded as above.

## Pre-existing behaviour, recorded but **not** re-claimed

That a non-PAR X-heterozygosity *rate* separates one X from two X, and that a rate between the
calibrated bounds is unresolved, is pre-existing behaviour of
`backend/services/sex_inference.py`. This change does not introduce, alter or re-derive it — it
makes the existing lower bound importable so the screen sees the same three zones the
classifier already used.

It is listed for traceability with its in-repo citation (`PMID:28035028`, cited in four places
before this change) and deliberately **not** run through the two-source gate, because this
change makes no new assertion about it. Re-deriving the thresholds would be its own issue.

## What is deliberately **not** claimed

The issue's own evidence gate discusses mosaic 46,XY/47,XXY and the relationship between X-het
and aneuploid-cell fraction. **None of that is asserted or evidenced here**, because an
unresolved X dosage is withheld rather than interpreted. Earlier revisions of the pull request
carried an intermediate-chromosome-complement mechanism, a FISH confirmation recommendation,
and a statement about what X heterozygosity measures; all three were removed rather than
evidenced, and `test_manual_review_text_names_the_ambiguous_x_cause` now enforces that none
returns to the patient-facing text.

## Discovery-tool ladder

- **Consensus** — invoked 2026-08-29; 20 papers, retained as a **derived** record. It surfaced review articles only, so nothing from it is load-bearing; C1's primary sources were located separately through PubMed.
- **Scite** — invoked **2026-09-05**, after the service-reported quota reset of 2026-09-03. Two calls, both retained as **derived** records: metadata for the three cited DOIs (no editorial notice on any) and a term search for the karyotype claim (1,261 results; the ten returned are case reports, mosaicism reports and reviews, none of which displaces the two primaries). An earlier revision still recorded the August quota failure as current at finalization; review caught it.
- **Fallback** — PubMed, used for the primary-literature search and for every verification above.

Tool versions: neither the Consensus MCP surface, the Scite MCP surface nor the NCBI E-utilities
endpoints expose a server, index or build version on these calls. All are recorded as **not
exposed** rather than guessed.

## Retained evidence and payload integrity

`raw/` holds **five unedited verbatim NCBI responses** (two eSummary, three eLink), **one
sanitized response** (the EFetch XML for all three PMIDs, abstracts removed and nothing else
touched, labelled in its filename) and **four derived records** labelled as derived inside the
file itself: the Consensus call and the two Scite calls, none of which has an HTTP response to
capture, and the reduction of the EFetch XML to its `CommentsCorrections` and publication-type
fields. `source-manifest.json`
records each exact request, source, SHA-256, byte length, access date, per-source licence
reference and the retraction-check method.

No payload required sanitization: all are public bibliographic records containing no genotype,
sample, personal or credentialed data.
