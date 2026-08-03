# PubMed PMID:26523116 sanitization record

## Source request

- Retrieved 2026-08-02 through NCBI E-utilities:
  `GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=26523116&retmode=xml`
- Returned DTD: `-//NLM//DTD PubMedArticle, 1st January 2025//EN`
  (`https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_250101.dtd`).
- Original response SHA-256 (not retained):
  `64349d244662df3431291147ef88404eb0420f43165f0b7c58034d8e3ba83003`.
- Sanitized derivative SHA-256:
  `8fb6d2ca9fd49ec871d1047f9faaebd23b4df83ab14fe8f9c903f1d5f10a75b2`.

## Retained metadata

The derivative keeps the identifier, dates, journal and citation fields, DOI,
publication types, publication status, and article IDs. The original response
contained no `CommentsCorrectionsList`; this absence and the absence of a
retraction publication type were checked before sanitization.

## Removed material and reuse boundary

Removed 1 `Abstract`, 0 `OtherAbstract`, 1 `ReferenceList`, 2 author records
and 2 affiliation records, 1 keyword list, 0 `CoiStatement`, and 0 author email
fields. No article full text was fetched or retained.

NCBI's policy says PubMed abstracts may incorporate copyright-protected
material. This packet therefore retains citation metadata only, attributes NCBI,
and does not treat the derivative as a license to reproduce article text. See
<https://www.ncbi.nlm.nih.gov/home/about/policies/> (accessed 2026-08-02).
