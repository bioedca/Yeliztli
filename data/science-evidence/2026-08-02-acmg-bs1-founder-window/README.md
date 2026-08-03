# ACMG BS1 founder-frequency window — issue #2068

## Scope

This packet supports a narrow, safety-first correction to the draft ACMG engine:
generic benign frequency criteria use the existing general-continental gnomAD
AF/AN window, so an ASJ- or FIN-only founder peak cannot by itself apply BS1.
It does not change the founder-inclusive rarity denominator used by PM2, make a
new clinical classification, or reannotate stored samples.

The current repository ingestion and persistence path for its checked-in gnomAD
r2.1.1-exomes reference exposes AF and observed-allele counts for global, AFR,
AMR, ASJ, EAS, EUR/NFE, FIN, and SAS populations. It does not parse or persist
filtering allele frequency (FAF) or OTH fields. Therefore this change
deliberately does not infer FAF from AF/AN or claim to implement a
release-pinned FAF policy. When no general continental AF/AN observation is
available, BS1 withholds instead of falling back to founder-inclusive popmax.
When more than one general-continental observation exists, BS1 chooses the
highest AF whose paired observed-allele count satisfies its existing guard.

## Claim map

| Claim | Source and version | Sanitized payload | Status |
| --- | --- | --- | --- |
| Generic benign-frequency evidence should use a general-population/FAF-aware window, rather than treating a founder peak as sufficient benign evidence. | ClinGen SVI overview, DOI:10.1002/cphg.93 (accessed 2026-08-02); ClinGen TP53 VCEP specification, DOI:10.1002/humu.24152 (accessed 2026-08-02) | `literature-metadata.json` | Supported methodological boundary; no new disease-specific threshold is introduced. |
| The existing engine can implement a founder-excluding AF/AN window without a migration because per-population AF/AN fields already reach ACMG assessment; FAF and OTH do not. | Source inventory at commit `410241021cfa5531d7db108991080c7a5a76b560` (accessed 2026-08-02) | `source-inventory.json` | Direct repository observation. |
| Ghosh's BA1 update and the public gnomAD reference paper are the provenance for the existing BA1/general-population distinction and reference dataset. | PMID:30311383; DOI:10.1002/humu.23642; PMID:32461654; PMID:28518168 (accessed 2026-08-02) | `pubmed-esummary.json`, `literature-metadata.json` | Citation metadata only; this patch does not reproduce or alter population measurements. |
| The public gnomAD GraphQL endpoint was reachable while qualifying this packet. | gnomAD GraphQL `meta.clinvar_release_date=2026-06-06` (accessed 2026-08-02) | `gnomad-meta.json` | Availability/provenance check only; no sample or genotype data were queried. |

## Evidence interpretation

The ClinGen SVI overview describes FAF as the relevant confidence-bounded
population-frequency construct for both BA1 and BS1. The TP53 ClinGen VCEP
specification applies a founder-aware boundary to both criteria by excluding
ASJ and FIN frequencies because of founder effects. They are distinct guideline
outputs but both use public population-frequency resources, including gnomAD;
this packet does not treat them as independent empirical frequency
measurements. The implementation consequence is deliberately conservative: it
withholds generic BS1 when the repository does not have a usable
general-continental AF/AN observation, and makes no new clinical assertion.

Consensus and Scite were queried before the database skills. Consensus returned
and was fetched for Ghosh et al.'s BA1 recommendation; Scite returned the
ClinGen overview and TP53 VCEP sources with no retraction notice in the returned
metadata. Query details and unavailable-payload safeguards are in
`query-log.md`.

## Scope boundaries and residual risk

- PM2 continues to use founder-inclusive `gnomad_af_popmax`, where a high
  founder frequency conservatively prevents a pathogenic rarity assertion.
- BS1 continues to use the existing general default thresholds and matching
  observed-allele guard; this packet does not claim gene- or disease-specific
  calibration.
- A true FAF implementation requires authoritative release-specific semantics,
  FAF/OTH ingestion and persistence, bundle provenance, migration/reannotation,
  and its own evidence packet. It must not derive FAF from stored AF/AN.
- All test fixtures in this change are synthetic. No real genotype, PII, or
  restricted source payload is stored here.

## Reproducibility and source handling

All paths are repository-relative. The public metadata derivatives retain only
identifiers, titles, dates, query parameters, and compact source findings; they
exclude article abstracts, full text, author/affiliation details, user-specific
access URLs, and raw gnomAD variation records. This limits the packet to
sanitized public/synthetic material while preserving durable identifiers and the
exact access date. Each JSON artifact records a source-specific retention and
license/terms note; no licensed source content is copied into this repository.
