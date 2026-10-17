# Breast-cancer PRS77 Ensembl release check for #2246

## Claim and scope

`backend/data/sources/breast_prs77/ensembl_primary_grch38_2026-07-16.json` is
the GRCh38 allele audit that keeps breast PRS77 fail-closed. It records a date
and a payload digest but no Ensembl release. So it could be reused, but nobody
could re-fetch it and confirm it against a named database state.

This packet supports three **provenance** claims. It makes no biological,
statistical or clinical claim, and the model stays non-reporting.

- **C1.** On 2026-10-17, `rest.ensembl.org` served Ensembl data release
  **116** (`/info/data` and `/info/software` both report 116; REST server
  15.12). Its human variation set is imported from **dbSNP 156**
  (`/info/variation/homo_sapiens`).
- **C2.** Re-running the same lookup (the 77 supplement rsIDs) at release 116
  gives primary-GRCh38 mappings **identical** to the 2026-07-16 snapshot's
  records: 0 of 77 differ, and 39 are multiallelic in both. The snapshot's
  39 / 2 / 41 runtime-block classification therefore holds at release 116.
- **C3.** The resource is cited as Hunt et al., *Ensembl variation resources*,
  Database 2018. DOI:10.1093/database/bay119; PMID:30576484 (accessed
  2026-10-17). PubMed lists no correction or retraction for it.

**Not claimed:** which release answered the 2026-07-16 lookup. It was never
recorded, and it is deliberately not inferred from the date.

## What the repository records

- `backend/data/sources/breast_prs77/ensembl_release_check_2026-10-17.json` —
  the release-named re-run:
  - `ensembl_release: 116`, the endpoint, the REST version and the dbSNP source
    version;
  - the raw-response digest, and the 77 records;
  - `snapshot_ensembl_release: null`, with a note.
- `scripts/build_breast_prs77.py` pins that file's SHA-256 and its raw-response
  digest. It fails unless the file's records equal the 2026-07-16 snapshot's
  exactly. If a future re-run differs, the classification must be re-derived;
  neither file is quietly overwritten.
- `model_provenance.current_allele_audit` (breast_cancer, in
  `backend/data/panels/cancer_prs_weights.json`) gains
  `snapshot_ensembl_release: null` and a `release_verification` block naming
  release 116.
- The 2026-07-16 snapshot itself is **unchanged**, byte for byte. Its digest
  stays pinned by the panel, by `NOTICE.md`, and by the copy in
  `data/science-evidence/2026-08-03-breast-prs77-availability-2028/`.

## Evidence route

- **Ensembl REST**, the authoritative source for C1 and C2:
  - five requests, every response retained under `raw/`;
  - `raw/ensembl-response-headers-2026-10-17.txt` keeps the server `Date`
    headers, with cookie lines removed. The variation POST is dated
    `Sat, 17 Oct 2026 21:11:29 GMT`.
- **The 2026-07-16 raw response** is added as a verbatim copy. It was
  retrieved during the #1934 work but never committed, so until now the
  snapshot's `raw_payload_sha256` (`36597c0a…`) pointed at nothing a reader
  could check. Its digest matches that pin, and re-deriving the records from
  it reproduces the committed snapshot exactly
  (`raw/derived-record-comparison-2026-10-17.json`).
- **The discovery ladder:**
  - **Consensus** (first tier) returned Hunt et al. 2018 at rank 1;
    `raw/consensus-search-2026-10-17.json` is a derived summary, since an MCP
    call has no wire payload.
  - **Scite** (first tier) was **unavailable**: the monthly MCP quota was
    exhausted, resetting 2026-11-03. That is recorded in
    `raw/scite-unavailable-2026-10-17.json`.
  - **PubMed E-utilities** (narrowest specialist) resolved the DOI to
    PMID:30576484 and fetched the record. Its XML carries **no
    `CommentsCorrections` element**, so it has no linked erratum, retraction,
    or comment. This is the correction/retraction screen, done here in place
    of Scite.

### Independence

The two-source rule is not engaged, because these are provenance facts. C1's
three signals agree: `/info/data`, `/info/software`, and the website banner
"Ensembl release 116 - June 2026". They are **not independent**, though:
all three are Ensembl reporting on itself.

## Licences

- **Ensembl:** "Ensembl imposes no restrictions on access to, or use of, the
  data provided …" (disclaimer, fetched from the `useast` mirror because
  `www.ensembl.org` refuses scripted clients).
- **EMBL-EBI:** "places no additional restrictions on the use or
  redistribution of the data … other than those provided by the original data
  owners".
- **dbSNP:** the records are imported from dbSNP, under NCBI's policy that "NCBI
  itself places no restrictions on the use or distribution of the data".

Everything retained is public reference-variant data for 77 published rsIDs:
no sample, genotype or personal data.

## Inventory

`queries.json` lists 17 entries, one per file in `raw/`, each with its
SHA-256:

| Source | Files |
| --- | --- |
| Ensembl REST captures (4 `/info` endpoints, the variation POST, its request body, the headers) | 7 |
| 2026-07-16 variation response, verbatim copy | 1 |
| Local derivation (record comparison) | 1 |
| Licence pages (Ensembl, EMBL-EBI, NCBI), sanitized text extracts | 3 |
| Consensus (derived) and Scite (unavailable) | 2 |
| PubMed E-utilities (esearch, esummary, efetch) | 3 |
| **Total** | **17** |

Payloads are retained as fetched, except for three deliberate changes:
- the header file, which drops cookie lines;
- the three licence pages, which are **sanitized text extracts**: script,
  style and markup are removed, and each `queries.json` entry records the
  fetched page's `original_sha256`. The full pages carried third-party inline
  JavaScript that code scanning analyses as repository source;
- the three records marked `"kind": "derived"`, which are summaries, not
  captures.
