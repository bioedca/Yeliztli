# dbNSFP mini-fixture boundary for issue #2035

## Decision

The checked-in dbNSFP mini fixture is now an explicitly synthetic software-
behavior fixture. It does not associate predictor values with human variants
and is not a scientific score oracle.

The previous seed mixed real rsIDs with invented values. It also contained
SIFT and PolyPhen score/category pairs that contradicted the thresholds used by
the production ensemble. Replacing those values with a different live score
snapshot would not establish reproducibility: Yeliztli is pinned to the dbNSFP
5.3.1a academic archive, while the public query service's score-free health
endpoint reported dataset 5.4a during this audit. The dbNSFP license also keeps
the full provider data in the existing bring-your-own, provider-fetched path
rather than this repository.

The replacement therefore uses six reserved `rsYELIZTLI####` identities and
arbitrary GRCh38 lookup keys. The rows deliberately cover complete, benign,
partial, conflicting-vote, boundary, and sparse predictor scenarios. Tests
enforce the exact scenario set, coherent SIFT/PolyPhen score-category pairs,
and byte-independent semantic equality between the CSV and SQLite fixture.

## Claim mapping

| ID | Claim | Evidence | Repository consequence |
| --- | --- | --- | --- |
| C1 | The former shared seed was not a valid scientific oracle. | Direct repository audit found four SIFT and five PolyPhen score/category contradictions under the production thresholds. The retained Ensembl response summary independently shows that current Ensembl transcript annotations for rs429358 do not reproduce the former fixture's SIFT/PolyPhen pair, but Ensembl is not substituted as a dbNSFP oracle. | Remove all human rsIDs and upstream-score claims from the shared dbNSFP seed. |
| C2 | An exact current dbNSFP score snapshot was not available for redistribution from the production-pinned source. | Production manifest and loader pin 5.3.1a; the retained official release metadata identifies v5.3.1 and its 5.3.1a/5.3.1c README branches, while the retained public query-service health response reports 5.4a (accessed 2026-08-12). | Do not copy the browser values into a fixture presented as 5.3.1a-derived. |
| C3 | Provider values must remain outside the repository's shared fixture. | dbNSFP states that the academic/non-commercial branch is CC BY-NC-ND 4.0 and that component-source terms also apply; Yeliztli's documented posture is provider-fetched and non-redistributed (accessed 2026-08-12). | Retain only public release/license metadata; test predictor behavior with synthetic values. |
| C4 | The replacement validates software behavior, not pathogenicity. | `dbnsfp_seed.contract.json` and the regression suite reject real dbSNP identifiers, unreviewed scenario drift, and incoherent SIFT/PolyPhen pairs. | No biological, clinical, or patient-specific inference is encoded by the seed values. |
| C5 | The retained literature records establish resource and predictor-method identity only. | PMID:33261662 / DOI:10.1186/s13073-020-00803-9 and PMID:27666373 / DOI:10.1016/j.ajhg.2016.08.016; bounded bibliographic and correction-link metadata are retained. | Do not treat the literature records as validation of a synthetic score, variant-specific prediction, or clinical claim. |

## Retained evidence

- `raw/` contains six sanitized, source-structured response copies: the dbNSFP
  releases and license HTML pages, the complete score-free health JSON, the
  complete public Ensembl VEP JSON, and the PubMed ESummary JSON and EFetch
  XML. `source-manifest.json` records each exact request, source-response hash,
  committed-payload hash, access date, source selector, and sanitization
  boundary. Scripts, public contact details, article text, author metadata,
  references, grants, and unused indexing terms were removed as declared; no
  predictor-score response is present.
- `dbnsfp-query-health-sanitized.json` retains the complete score-free public
  health response fields (`status` and `dataset_version`) as a derived summary.
  It contains no variant query or predictor value.
- `dbnsfp-releases-sanitized.json` retains only the official release IDs,
  dates, and academic/commercial README branch labels as a derived summary of
  the retained source HTML.
- `dbnsfp-license-sanitized.json` retains only the official branch, permitted-
  use, license-identifier, and component-license facts used by the
  non-redistribution decision as a derived summary of the retained source HTML.
- `ensembl-rs429358-sanitized.json` retains only request identity, assembly,
  allele, and aggregated APOE transcript prediction fields as a derived
  summary. The source-structured response is retained under `raw/`; Ensembl is
  context for rejecting the old fixture, not a source for replacement values.
- `pubmed-metadata-sanitized.json` retains bibliographic identifiers and the
  observed PubMed correction-link field state as a derived summary of the two
  source-structured PubMed responses. It contains no abstracts, article text,
  affiliations, or contact information.
- `source-manifest.json` binds all derived summaries and raw responses to exact
  repository-relative paths and SHA-256 digests.
- `query-log.md` records the sanitized public queries and unavailable
  documentation lookup. Consensus and Scite were discovery/checking tools only;
  their output is not retained or treated as evidence.

No dbNSFP result row, score excerpt, source archive, genotype, patient record,
credential, or restricted payload is retained.

## Sources and version boundary

- dbNSFP releases and README branches:
  <https://www.dbnsfp.org/releases/> (accessed 2026-08-12).
- dbNSFP academic/non-commercial and component-source terms:
  <https://www.dbnsfp.org/license/> (accessed 2026-08-12).
- dbNSFP web-query entry point:
  <https://www.dbnsfp.org/web-query/> (accessed 2026-08-12).
- dbNSFP public score-free health metadata:
  <https://query.genos.us/api/health> (accessed 2026-08-12).
- dbNSFP v4 publication: PMID:33261662;
  DOI:10.1186/s13073-020-00803-9 (accessed 2026-08-12).
- REVEL publication: PMID:27666373;
  DOI:10.1016/j.ajhg.2016.08.016 (accessed 2026-08-12).
- Ensembl VEP REST request:
  <https://rest.ensembl.org/vep/human/id/rs429358>
  (accessed 2026-08-12; GRCh38 response).

The literature records establish resource/method identity only. They are not
used to validate any synthetic score or to make a clinical claim.

## Corrections and retractions

NCBI PubMed EFetch records for PMID:33261662 and PMID:27666373 were inspected on
2026-08-12. Neither retrieved record emitted a `CommentsCorrectionsList`.
That bounded observation is retained in the sanitized source-structured EFetch
XML and summarized in `pubmed-metadata-sanitized.json`; it is not a general
retraction clearance, and no score claim depends on it.

## Reproduction and withholding rule

Regenerate the mini databases into a temporary directory with:

```bash
python scripts/regenerate_fixtures.py --output-dir /tmp/yeliztli-mini-preview
```

Review semantic SQLite dumps before replacing a checked-in binary. If a future
change needs source-faithful dbNSFP values, it must first establish the exact
pinned release, provider-authorized redistribution terms, exact source identity,
and a production-parser derivation. Until then, withhold those values and keep
the shared fixture synthetic.
