# Consensus / Scite ladder outcomes, 2026-08-10

> **This file is analysis, not a source payload.** It is the prose reading of the
> two record-level artifacts in this directory —
> `consensus-search-2026-08-10.json` (all 20 Consensus records) and
> `scite-and-pubmed-notices-2026-08-10.json` (all 6 Scite and all 7 PubMed
> records). Check claims here against those, not the other way round.

Recorded because the repository's evidence ladder requires the discovery services
to be invoked and their outcomes retained, including when they do not decide the
claim. Neither service is load-bearing for the reference-base claim — that rests
on RefSeq and UniProt — but both were run, and one changed the wording
that shipped.

## Consensus

- Service: Consensus MCP, `search`. Available; no quota fallback needed.
- Query: `SLC19A1 RFC1 rs1051266 A80G G80A polymorphism nomenclature reduced folate carrier`
- Filters: none.
- Returned: 20 papers.

**Outcome — the naming split is real, widespread, and extends to HGVS `c.`
notation, not just the legacy shorthand.** Both spellings appear in peer-reviewed
titles and abstracts for the same rsID:

| Spelling | Examples from the result set |
| --- | --- |
| `G80A` / `c.80G>A` / `80G>A` | PMID:16750224 (Dufficy 2006); PMID:24597986 (He 2014); PMID:16962770 (Wang 2006); PMID:19650776 (Stanisławska-Sachadyn 2009, `c.80G>A`); Coppedè 2014, BioMed Res Int (`c.80G>A`); Kurzawski 2010, Biomarkers (`rs1051266:G>A, 80G>A`); Gregers 2010, Blood (`80G>A`) |
| `A80G` / `c.80A>G` / `80A>G` | PMID:33935279 (Yi 2021); Coppedè 2013, Nutrients (`c.80A>G`); PMID:33749319 (Naushad 2021, `c.80A>G`); Imani 2019, Arch Oral Biol |

This is why the shipped `recommendation_text` says the literature "names this SNP
G80A **or c.80G>A**, taking the opposite base as the reference" rather than
offering a historical explanation: the search establishes *that* both frames are
in active use, and does not establish *why*. An earlier draft attributed the
legacy frame to the ancestral allele; that was an inference about authors' intent
and was removed.

**Outcome — direction of effect is contested, which is why this PR does not touch
it.** Among the returned papers, on the same allele:

- PMID:19650776 (Stanisławska-Sachadyn 2009, Ann Hum Genet): women with GA and AA
  had *higher* red-cell folate than GG.
- PMID:33749319 (Naushad 2021, Ann Pharmacother, 18 studies, 3592 RA patients): the
  80A allele *increased* methotrexate efficacy and safety.
- PMID:16962770 (Wang 2006, Eur J Cancer): 80AA associated with *increased*
  gastro-oesophageal cancer risk.
- PMID:41673870 (Yue 2026, World J Surg Oncol, 13 studies): 80AA associated with
  *greater* methotrexate toxicity in paediatric ALL.
- PMID:24597986 (He 2014, the row's own citation): no influence of G80A on
  methotrexate toxicity.

The panel's `genotype_effects` call AA "reduced folate carrier efficiency", which
at least Stanisławska-Sachadyn 2009 contradicts. That conflict is **not**
adjudicated here and is filed separately; this PR changes no category, no
`risk_allele`/`ref_allele`, and no evidence level.

## Scite

- Service: Scite MCP, `search_literature` by DOI. Available; no quota fallback needed.
- Query: `dois: ["10.12659/MSM.929911", "10.1016/j.lfs.2006.05.009", "10.3109/10428194.2014.898761", "10.1111/j.1469-1809.2009.00529.x", "10.1177/10600280211002053", "10.1016/j.ejca.2006.04.022"]`, no term (metadata + editorial notices).
- Returned: **6 of 6** records. The seventh cited paper, PMID:41673870, is **not indexed by Scite** and was not requested here; PubMed is its only notice check.

**Outcome — correction/retraction check performed on three legs, negative
throughout.** None of the six Scite records carried an `editorialNotices` entry.
Cross-checked against PubMed `article_types` for **all seven** cited PMIDs, none
of which carries a `Retracted Publication` or `Erratum` type. Article types alone
are not sufficient, though: a correction published as a *separate* record leaves
the original typed `Journal Article`, and PubMed is the only notice check
covering PMID:41673870. So all seven were also read for `CommentsCorrections`
links (`raw/pubmed-comments-corrections-2026-08-10.json`) — **zero links of any
kind**, so no `ErratumIn`, `RetractionIn`, `ExpressionOfConcernIn` or sibling.

The check covers every paper the packet cites in a claim, not only the three the
panel row carries:

| PMID | DOI | Scite editorial notices | PubMed article types |
| --- | --- | --- | --- |
| 33935279 | 10.12659/MSM.929911 | none | Journal Article; Meta-Analysis |
| 16750224 | 10.1016/j.lfs.2006.05.009 | none | Journal Article |
| 24597986 | 10.3109/10428194.2014.898761 | none | Journal Article; Meta-Analysis |
| 19650776 | 10.1111/j.1469-1809.2009.00529.x | none | Journal Article; Research Support |
| 33749319 | 10.1177/10600280211002053 | none | Journal Article; Meta-Analysis |
| 16962770 | 10.1016/j.ejca.2006.04.022 | none | Journal Article; Research Support |
| 41673870 | 10.1186/s12957-026-04222-9 | not indexed | Journal Article; Meta-Analysis |

PMID:41673870 published 2026-02-12, so the absence of a notice tells you very
little at this date; it is cited as one voice in a documented conflict and no
conclusion turns on it.

Bibliographic metadata retrieved from PubMed (https://pubmed.ncbi.nlm.nih.gov/);
DOIs above resolve at https://doi.org/.

## Identifiers

Consensus returns its own paper URLs but no PMID or DOI. Identifiers were resolved
separately via PubMed for every record cited in a claim above (accessed 2026-08-10), and are
recorded per-record in `consensus-search-2026-08-10.json` under `pmid`/`doi`, with
`used_in_packet_claim` marking which ones those are. Records left without an
identifier are the ones no claim rests on; they are retained so the result set stays
complete and it is visible what was *not* selected. Bibliographic metadata from
PubMed (https://pubmed.ncbi.nlm.nih.gov/); DOIs resolve at https://doi.org/.
