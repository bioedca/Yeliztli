# ACMG BS1 founder-frequency window — issue #2068

## Scope

This packet supports a narrow, safety-first correction to the draft ACMG engine:
it aligns BS1 with the repository's pre-existing BA1 founder-excluding
general-continental AF/AN selector, so an ASJ- or FIN-only founder peak cannot
by itself apply BS1. It can change the returned draft `AcmgResult.classification`
for future evaluations. It does not change PM2's founder-inclusive rarity
denominator, persist a finding, override ClinVar, issue a standalone clinical
report or assertion, or reannotate stored samples.

BA1's founder-excluding selector predates this patch. The review follow-up moves
BA1's existing 2,000-observed-allele guard into candidate selection, so an
underpowered higher continental AF cannot hide a lower adequately observed one.
It does not change BA1's threshold or establish a new founder-population policy.

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

| ID | Claim | Source and version | Sanitized payload | Status |
| --- | --- | --- | --- | --- |
| C1 | The BS1 implementation uses the repository's existing founder-excluding BA1 selector and fails closed without an eligible general-continental AF/AN observation. | Repository implementation and regression suite; the ClinGen sources below are methodological context only (accessed 2026-08-02). | `source-inventory.json` | Repository-consistency and safety boundary; not a universal disease-specific clinical threshold. |
| C2 | Per-population AF/AN fields already reach ACMG assessment; FAF and OTH do not. | Immutable base and implementation snapshots named in `source-inventory.json` (accessed 2026-08-02). | `source-inventory.json` | Direct repository observation. |
| C3 | Ghosh's BA1 update and the public gnomAD reference paper provide citation provenance for the existing BA1/general-population distinction and reference dataset. | PMID:30311383; DOI:10.1002/humu.23642; PMID:32461654; PMID:28518168 (accessed 2026-08-02). | `pubmed-esummary.json`, `literature-metadata.json` | Citation metadata only; this patch does not reproduce or alter population measurements. |
| C4 | The public gnomAD GraphQL endpoint was reachable while qualifying this packet. | gnomAD GraphQL `meta.clinvar_release_date=2026-06-06` (accessed 2026-08-02). | `gnomad-meta.json` | Availability/provenance check only; no sample or genotype data were queried. |

## Evidence interpretation

The ClinGen SVI overview describes FAF as the relevant confidence-bounded
population-frequency construct for both BA1 and BS1. The TP53 ClinGen VCEP
specification is a TP53-specific example that excludes ASJ and FIN frequencies
because of founder effects. It is not evidence that every gene or disease should
apply that specification. Both guideline outputs use public
population-frequency resources, including gnomAD; this packet does not treat
them as independent empirical frequency measurements. The implementation
consequence is deliberately conservative repository-consistency work: BS1 now
uses the pre-existing BA1 selector and withholds when no usable
general-continental AF/AN observation is available. It makes no new
disease-specific clinical assertion.

Consensus and Scite were queried before the database skills. Consensus returned
and was fetched for Ghosh et al.'s BA1 recommendation; Scite returned the
ClinGen overview and TP53 VCEP sources with no retraction notice in the returned
metadata. Query details and unavailable-payload safeguards are in
`query-log.md`.

## Scope boundaries and residual risk

- PM2 continues to use founder-inclusive `gnomad_af_popmax`, where a high
  founder frequency conservatively prevents a pathogenic rarity assertion.
- BA1 and BS1 use the same eligible general-continental AF/AN selector; BA1's
  2,000-observed-allele guard and BS1's existing default threshold remain
  unchanged. This packet does not claim gene- or disease-specific calibration.
- A true FAF implementation requires authoritative release-specific semantics,
  FAF/OTH ingestion and persistence, bundle provenance, migration/reannotation,
  and its own evidence packet. It must not derive FAF from stored AF/AN.
- All test fixtures in this change are synthetic. No real genotype, PII, or
  restricted source payload is stored here.

## Reproducibility and source handling

All paths are repository-relative. `source-inventory.json` records the immutable
pre-change base and the preceding implementation commit rather than a
self-referential commit ID: an evidence file cannot accurately contain the OID
of the commit that changes it. The exact evidence-only commit is instead bound
by the PR head and review route. The public metadata derivatives retain only
identifiers, titles, dates, query parameters, and compact source findings; they
exclude article abstracts, full text, author/affiliation details, user-specific
access URLs, and raw gnomAD variation records. This limits the packet to
sanitized public/synthetic material while preserving durable identifiers and the
exact access date. Each JSON artifact records a source-specific retention and
license/terms note; no licensed source content is copied into this repository.
