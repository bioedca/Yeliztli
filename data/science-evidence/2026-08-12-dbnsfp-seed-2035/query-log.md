# Query log

All requests used public, sanitized identifiers only. Access date for every
entry is 2026-08-12.

| Order | Service | Query or request | Disposition |
| --- | --- | --- | --- |
| 1 | Consensus | `dbNSFP v4 transcript-specific SIFT PolyPhen REVEL functional prediction annotation provenance rs429358` | Discovery only; no result is retained or cited as evidence. |
| 2 | Scite | DOIs `10.1186/s13073-020-00803-9` and `10.1016/j.ajhg.2016.08.016`; term `correction OR retraction` | Citation-status check only; no result is retained or cited as evidence. |
| 3 | NCBI PubMed E-utilities | ESummary and EFetch for PMIDs `33261662,27666373` | Bibliographic identifiers and the bounded `CommentsCorrectionsList` observation are retained in `pubmed-metadata-sanitized.json`; abstracts and article text were discarded. |
| 4 | Ensembl VEP REST | `GET https://rest.ensembl.org/vep/human/id/rs429358`, `Content-Type: application/json` | The bounded GRCh38/APOE transcript summary is retained in `ensembl-rs429358-sanitized.json`; the full response was discarded. |
| 5 | dbNSFP Web Query Service | Variant `rs429358`, build `GRCh38` | Inspected only. All score-bearing output was discarded because it does not match the production pin `5.3.1a` and dbNSFP values are not redistributed in this fixture. The browser's then-rendered branch label is not used as evidence because no durable metadata payload was retained for that observation. |
| 6 | dbNSFP Variant Browser public health endpoint | `GET https://query.genos.us/api/health` | The complete score-free response fields (`status=ok`, `dataset_version=5.4a`) and exact source-payload hash are retained in `dbnsfp-query-health-sanitized.json`. |
| 7 | dbNSFP official site | `GET https://www.dbnsfp.org/releases/` and `GET https://www.dbnsfp.org/license/` | The bounded release/version and license facts, exact source-payload hashes, requests, access date, and sanitization boundaries are retained in `dbnsfp-releases-sanitized.json` and `dbnsfp-license-sanitized.json`; no dbNSFP row is retained. |
| 8 | Context7 CLI | Three library-resolution attempts for MyVariant.info/BioThings documentation using the issue's provenance question | No suitable official library match was returned within the three-command limit; no Context7 documentation was used. |

The direct repository audit also compared every former seed score/category pair
against the thresholds in the production ensemble. That is local implementation
evidence rather than an external scientific query.
