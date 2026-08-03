# Query log — ACMG BS1 founder-frequency window

All queries used public, sanitized scientific terms and were accessed on
2026-08-02. No real genotype, sample, credential, or restricted payload was
submitted or retained.

## Evidence ladder

1. Consensus search: `ACMG BS1 BA1 allele frequency founder populations
   Ashkenazi Jewish Finnish filtering allele frequency ClinGen SVI`.
   The returned Ghosh et al. record was fetched as Consensus ID
   `4439bbb6071d5097972fac0f2fea8fb0`.
2. Scite literature search used the same terms, then targeted
   `10.1002/humu.23642`, `10.1002/cphg.93`, and `10.1002/humu.24152` with
   `Ashkenazi Finnish BS1 BA1 filtering allele frequency`.
3. NCBI Entrez ESummary queried PubMed IDs `30311383,32461654,28518168`.
4. The gnomAD GraphQL skill queried `meta { clinvar_release_date }` to record
   public endpoint availability. It did not fetch a variant or genotype record.

## Results and checks

- Consensus supplied the Ghosh BA1 recommendation record; no Consensus result
  is cited without its required fetch step.
- Scite's targeted results supported the ClinGen SVI overview's use of FAF for
  BA1/BS1 and the TP53 VCEP's ASJ/FIN founder-effect exclusion for both
  criteria. Returned source metadata did not list a retraction notice.
- PubMed metadata confirmed the identifiers and titles in
  `pubmed-esummary.json`. This is a metadata check as of the access date, not a
  claim about future corrections or retractions.
- The gnomAD endpoint returned `clinvar_release_date` `2026-06-06`; the local
  bundle remains versioned separately as r2.1.1 exomes.

## Sanitization

Scite access links can contain user-specific parameters, so raw Scite response
objects were not checked in. `literature-metadata.json` is a sanitized metadata
derivative. PubMed records similarly retain citation metadata only, not article
abstracts or full text.
