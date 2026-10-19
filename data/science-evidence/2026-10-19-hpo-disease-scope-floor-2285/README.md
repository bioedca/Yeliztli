# HPO disease-scope match-rate floor for #2285

## Claim and scope

`download_and_load_mondo_hpo` joins each HPO `genes_to_phenotype` disease scope
to a MONDO disease row for the same gene, through MONDO's exact SSSOM
cross-references. Until #2285 it refused a refresh only when **zero** scopes
resolved, so a release that lost a whole identifier namespace could replace a
healthy installation provided one incidental match survived.

This packet supports three **measurement** claims about public files. It makes
no biological, statistical or clinical claim.

- **C1.** With the loader's own parsers and `_count_scoped_hpo_matches`, five
  paired releases of the three exports (Monarch KG 2025-09-17 to 2026-10-07)
  resolved **63.9-71.5%** of HPO gene-disease scopes.
- **C2.** With the OMIM rows removed from each release's MONDO SSSOM export,
  the export still carried **98,153-99,666** unambiguous exact identifiers.
  That is above `MINIMUM_UNAMBIGUOUS_MONDO_XREFS` (50,000). The join resolved
  only **10.9-12.2%** of scopes, never zero, so the old guard accepted it.
- **C3.** `MINIMUM_HPO_DISEASE_SCOPE_MATCH_PERCENT = 40` is derived from C1 and
  C2, as set out below.

**Not claimed:**
- That the rate measures semantic completeness, or that any one gene or disease
  is fully annotated.
- That unmatched scopes are defects. Of the 4,458 unmatched in the 2026-10-07
  release, 3,676 are HPO gene-disease pairs whose exact MONDO target Monarch
  does not list for that gene, 756 belong to genes with no Monarch rows, and 26
  lack an unambiguous exact cross-reference.
- That each pairing is exactly what a refresh on that date fetched. The
  selection rule is in `raw/snapshot-sources.json`.

## Measurements

Rates are matched scopes divided by all HPO gene-disease scopes, which is the
statistic the guard computes. The last two columns are the OMIM-loss
simulation.

| Monarch KG | HPO | MONDO | Scopes | Matched | Rate | Without OMIM: identifiers | Without OMIM: rate |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2025-09-17 | v2025-09-01 | v2025-09-02 | 14,392 | 10,283 | 71.45% | 98,153 | 10.87% |
| 2026-01-11 | v2026-01-08 | v2026-01-06 | 12,490 | 8,064 | 64.56% | 98,811 | 12.17% |
| 2026-04-14 | v2026-02-16 | v2026-04-07 | 12,627 | 8,069 | 63.90% | 99,209 | 12.05% |
| 2026-07-14 | v2026-06-23 | v2026-07-06 | 12,654 | 8,198 | 64.79% | 99,261 | 12.02% |
| 2026-10-07 | v2026-09-01 | master, 2026-10-19 | 12,672 | 8,214 | 64.82% | 99,666 | 12.18% |

The largest change between neighbouring releases is 6.89 points (2025-09-17 to
2026-01-11). OMIM scopes resolve at 89-95% in every release and Orphanet scopes
at 29-30%, so OMIM carries 81-85% of all matches.

Further simulations on the 2026-10-07 release:

| Failure | Identifiers | Rate | 40% floor |
| --- | ---: | ---: | --- |
| OMIM prefix renamed in HPO (`OMIM:` to `MIM:`) | 109,712 | 12.18% | refused |
| Mapping export with neither OMIM nor Orphanet rows | 89,880 | 0.00% | refused |
| Mapping export, 50% of rows kept at random | 55,194 | 32.24% | refused |
| Mapping export, 60% of rows kept at random | 65,834 | 38.70% | refused |
| Mapping export, 75% of rows kept at random | 82,114 | 47.43% | accepted |
| Mapping export without Orphanet rows | 99,926 | 52.64% | accepted |

## Floor derivation

- 40% sits 23.9 points below the lowest observed rate. That is more than three
  times the largest observed release-to-release change.
- It refuses every simulated namespace failure above, and truncation to 60% of
  the mapping rows or fewer.
- It deliberately accepts the loss of Orphanet rows alone, and a 75%
  truncation. A floor high enough to refuse those would sit within about 11
  points of a healthy release.
- The comparison is in integer percent (`matches * 100 < 40 * scopes`), so the
  boundary cannot move by float rounding.

This follows the sibling floors in `backend/annotation/mondo_hpo.py`. Each one
cites a measured value and sits well below it, and none claims to prove
completeness.

## Reproduction

1. Download the 15 files whose URLs are in `raw/snapshot-sources.json` into one
   directory per release.
2. From a repository checkout, run `raw/measure_disease_scope_rate.py`, passing
   it `raw/snapshot-sources.json` and one `LABEL=DIR` argument per release.
3. It prints `raw/measurements-2026-10-19.json`, including each input file's
   sha256 and size. The random truncation uses seed 2285.

**Cross-check.** These numbers match the earlier #2163 packet
(`data/science-evidence/2026-08-01-hpo-disease-scope-2163/source-manifest.json`):
- its Monarch 2026-07-14 archive sha256 (`8a57c65a…`) equals this packet's;
- its HPO file size (20,732,778 bytes) equals this packet's v2026-06-23 file;
- its 109,306 exact identifiers equal this packet's 2026-07-14 count.

## Evidence route

- **Primary:** the five measured releases above. The source files are not
  committed, because their licenses are not all established (see
  `queries.json`). Only derived counts, sizes and sha256 digests are committed.
- **Consensus** was used for discovery: three results, recorded in
  `raw/consensus-search-2026-10-19.json`.
- **Scite** was unavailable: its monthly MCP quota is spent until 2026-11-03
  (`raw/scite-unavailable-2026-10-19.json`).
- **PubMed** resolved the three context citations.
  - The raw `esearch`, `esummary` and `efetch` responses are retained.
  - None carries a `CommentsCorrections` element. That is the correction and
    retraction screen used here.

## Citations

These identify the resources only; none is evidence for a rate.

- Gargano MA, et al. The Human Phenotype Ontology in 2024: phenotypes around
  the world. *Nucleic Acids Res.* 2024. PMID:37953324 /
  DOI:10.1093/nar/gkad1005 (accessed 2026-10-19).
- Matentzoglu N, et al. A Simple Standard for Sharing Ontological Mappings
  (SSSOM). *Database (Oxford).* 2022. PMID:35616100 /
  DOI:10.1093/database/baac035 (accessed 2026-10-19).
- Putman TE, et al. The Monarch Initiative in 2024: an analytic platform
  integrating phenotypes, genes and diseases across species. *Nucleic Acids
  Res.* 2024. PMID:38000386 / DOI:10.1093/nar/gkad1082 (accessed 2026-10-19).

## Inventory

`queries.json` lists all 16 files under `raw/` with their sizes and sha256.

| Kind | Files |
| --- | ---: |
| Measurement script, source list and results | 3 |
| PubMed responses (3 esearch, 1 esummary, 1 efetch) | 5 |
| License records (MONDO and HPO repositories, Monarch page, HPO page) | 4 |
| Canonical-URL response headers, signed query string removed | 1 |
| Monarch KG release metadata excerpt | 1 |
| Discovery records (Consensus, Scite) | 2 |
| **Total** | **16** |
