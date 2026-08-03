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
| C1 | The BS1 implementation uses the repository's existing founder-excluding BA1 selector and fails closed without an eligible general-continental AF/AN observation. The sources have deliberately separate roles: the ClinGen SVI BA1 refinement defines the existing selector; the SVI overview describes FAF and the Finnish/Ashkenazi Jewish noncontinental limitation; a separate TP53 VCEP specification corroborates a founder-effect exclusion only for TP53. | Repository implementation and regression suite; DOI:10.1002/humu.23642, PMID:30311383; DOI:10.1002/cphg.93, PMID:31479589; DOI:10.1002/humu.24152, PMID:33300245 (accessed 2026-08-02; response snapshots accessed 2026-08-03). | `source-inventory.json`, `source-response-index.json` | Engine-level conservative evidence-eligibility boundary. The TP53 source is not generalized, neither guidance source is treated as an independent empirical frequency measurement, and the engine does not calculate FAF or establish a universal disease-specific clinical threshold. |
| C2 | Per-population AF/AN fields already reach ACMG assessment; FAF and OTH do not. | Immutable base and implementation snapshots named in `source-inventory.json` (accessed 2026-08-02). | `source-inventory.json` | Direct repository observation. |
| C3 | Ghosh's BA1 update and the public gnomAD reference paper provide citation provenance for the existing BA1/general-population distinction and reference dataset. | PMID:30311383; DOI:10.1002/humu.23642; PMID:32461654; PMID:28518168 (accessed 2026-08-02; source-response and correction snapshots accessed 2026-08-03). | `source-response-index.json`, `raw/consensus-search-fetch-sanitized.json`, `raw/scite-targeted-doi-responses-sanitized.json`, `pubmed-efetch-corrections-sanitized.json` | Citation provenance only; this patch does not reproduce or alter population measurements. The gnomAD reference record has linked PubMed errata and is not used for a frequency result. |
| C4 | The public gnomAD GraphQL endpoint was reachable while qualifying this packet. | gnomAD GraphQL `meta.clinvar_release_date=2026-06-06` (accessed 2026-08-02). | `gnomad-meta.json` | Availability/provenance check only; no sample or genotype data were queried. |

## Evidence interpretation

The ClinGen SVI overview separates two concerns. It identifies FAF as the
confidence-bounded population-frequency construct for both BA1 and BS1, and it
records that gnomAD does not calculate FAF for Finnish or Ashkenazi Jewish
noncontinental subpopulations. The preceding SVI BA1 refinement is the source of
the pre-existing general-continental, minimum-AN selector; it does not calibrate
BS1. Disease-specific threshold calibration remains a separate requirement. This
repository does not ingest FAF or a condition-policy model, so it does not claim
that its AF/AN values are FAF or that every gene and disease shares a clinical
threshold. The implementation consequence is a narrow, engine-level, fail-closed
evidence-eligibility guard: BS1 uses the pre-existing BA1 general-continental
AF/AN selector and withholds when no usable candidate is available rather than
allowing a founder-only raw AF peak to create benign draft evidence. The TP53
ClinGen VCEP specification is separate, TP53-specific corroborative founder-effect
rationale, not the source of a universal threshold. These guidance sources use
public population-frequency resources, including gnomAD; this packet does not
treat them as independent empirical frequency measurements.

Consensus and Scite were queried before the database skills. Consensus returned
and was fetched for Ghosh et al.'s BA1 recommendation; the retained sanitized
provider envelopes in `raw/` preserve their exact query/identifier, returned
citation metadata, status fields, and bounded relevant context. Scite did not
return a `retraction_notices` field, so this packet does not treat that absence
as a retraction clearance. The separate PubMed EFetch snapshot records
correction links for every cited record, including the gnomAD reference paper's
linked errata. Query details and redaction safeguards are in `query-log.md`.

## Scope boundaries and residual risk

- PM2 continues to use founder-inclusive `gnomad_af_popmax`, where a high
  founder frequency conservatively prevents a pathogenic rarity assertion.
- BA1 and BS1 use the same eligible general-continental AF/AN selector; BA1's
  2,000-observed-allele guard and BS1's existing default threshold remain
  unchanged. This is a conservative engine-wide evidence-eligibility guard, not
  gene- or disease-specific calibration.
- The selector considers paired allele frequency (AF) and allele number (AN)
  only. It neither ingests nor enforces allele count (AC) for BA1 or BS1.
- A true FAF implementation requires authoritative release-specific semantics,
  FAF/OTH ingestion and persistence, bundle provenance, migration/reannotation,
  and its own evidence packet. It must not derive FAF from stored AF/AN.
- All test fixtures in this change are synthetic. No real genotype, PII, or
  restricted source payload is stored here.

## Reproducibility and source handling

All paths are repository-relative. `source-inventory.json` records the immutable
pre-change base and the preceding implementation commit rather than a
self-referential commit ID: an evidence file cannot accurately contain the OID
of the commit that changes it. The exact evidence-only commits are instead bound
by the PR head and review route. `source-response-index.json` maps each
Consensus and Scite source to a sanitized raw provider-response artifact and
its checksum. Those artifacts retain direct request/response metadata and only
bounded source context; they exclude article abstracts, full text,
author/affiliation details, user-specific access URLs, and raw gnomAD variation
records. This limits the packet to sanitized public/synthetic material while
preserving durable identifiers and concrete access dates. Each JSON artifact
records a source-specific retention and license/terms note; no abstract,
full-text, or long licensed source content is copied into this repository.
