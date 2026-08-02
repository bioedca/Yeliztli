# UniProt disulfide endpoint rendering — issue #2164

## Scope

This packet supports a narrow data-shape and rendering correction: when a
reviewed UniProt `Disulfide bond` feature supplies two explicit, `EXACT`, unequal
residue coordinates, preserve both values through the application and render the
feature as a non-continuous connector. It does not add, infer, or change a biological,
clinical, or protein-folding claim.

For new live parser output, the legacy `position` convenience field is populated
only for a true point feature (`start == end`). The viewer prioritizes explicit
`start` and `end` values, but an older cache entry without endpoint modifiers is
rendered as a non-bridge until a live refresh supplies its exactness qualifiers.

## Claim map

| Claim | Source and version | Sanitized payload | Status |
| --- | --- | --- | --- |
| Reviewed human insulin entry P01308 reports three explicit `EXACT` disulfide endpoint pairs: 31–96, 43–109, and 95–100. | UniProtKB P01308, entry version 283 (accessed 2026-08-02); each annotation links to PMID:1433291 and DOI:10.1016/0022-2836(92)90527-q (accessed 2026-08-02) | `uniprot-p01308.json` | Direct authoritative record; source-linked primary evidence |
| A disulfide connectivity represents which nonadjacent cysteines are cross-linked. | PMID:26523116; DOI:10.4137/EBO.S25349 (accessed 2026-08-02) | `pubmed-26523116.xml` | Context only; no clinical claim |
| The pinned upstream Nightingale track maps `DISULFID` to the non-continuous `bridge` shape. | `ebi-webcomponents/nightingale` commit `a4a65eccbf03fe5290adb1ec171cb5a43e8a3d83` (accessed 2026-08-02) | Upstream source URLs in `query-log.md` | Renderer contract |

## Source and license notes

- UniProtKB is public data under CC BY 4.0; see
  <https://www.uniprot.org/help/license> (accessed 2026-08-02). The raw API
  response is public and contains no user, sample, genotype, or restricted data.
- The repository lockfile pins `@nightingale-elements/nightingale-track` 5.6.0
  under the MIT license. The upstream source above was used only to verify the
  supported `bridge` shape.
- `pubmed-26523116.xml` is a sanitized EFetch citation-metadata derivative, not
  article text. The full response's abstract, author/affiliation fields,
  keywords, and reference list were removed; the request, hashes, DTD, and
  removals are recorded in `pubmed-26523116-sanitization.md`. NCBI notes that
  PubMed abstracts can be copyright-protected, so this packet retains no
  abstract or full text and treats the metadata only as citation context; see
  <https://www.ncbi.nlm.nih.gov/home/about/policies/> (accessed 2026-08-02).
- The original fetched response has no `CommentsCorrectionsList` and no
  retraction publication type. This is a metadata check as of 2026-08-02, not a
  claim that no future notice can exist.

## Scope boundaries

- Preserve endpoint modifiers and render a bridge only when both endpoint
  values are explicitly present, `EXACT`, and unequal.
- Do not turn an unknown or alternative partner into a numeric interval; the
  present `int | None` model cannot represent those semantics without separately
  sourced schema work.
- Do not use this packet to make protein-folding, pathogenicity, or prescribing
  claims.

## Reproducibility

Requests, versions, access dates, and unavailable-tool fallback are recorded in
`query-log.md`. All paths in this packet are repository-relative and public.
