# GTEx v8 egenes gene names for the eQTL badge (#2087)

**Purpose.** #2087 names the Variant Detail eQTL badge's top gene by its symbol. The loader (`backend/annotation/gtex_eqtl.py::load_gtex_gene_names`) relies on these facts about the GTEx v8 eQTL release it already ingests, recorded here so they can be re-checked when the upstream archive changes.

## Claims and evidence

| # | Claim the code relies on | Evidence |
| --- | --- | --- |
| 1 | The v8 eQTL tar ships one `<Tissue>.v8.egenes.txt.gz` per tissue (49), alongside the 49 signif-pairs files. | `derived/egenes-gene-name-summary.json` (`egenes_files`, `signif_pairs_files`) |
| 2 | Each egenes file has `gene_id` and `gene_name` columns, and `gene_id` uses the same versioned Ensembl form that `gtex_eqtl.gene_id` stores (e.g. `ENSG00000177000.10`). | `raw/whole-blood-v8-egenes-excerpt.tsv` (verbatim header and rows, first six columns) |
| 3 | No gene ID carries conflicting names across tissues (39,832 IDs, 0 conflicts), so a single `gene_id → gene_name` table is well defined. | `derived/egenes-gene-name-summary.json` |
| 4 | Every gene in the signif-pairs files has a name (34,548 IDs, 0 missing). | `derived/egenes-gene-name-summary.json` |
| 5 | The release has no placeholder names (`.`, `NA`, empty, or the gene's own unversioned ID; 0 found). The loader's placeholder skip therefore guards future releases and changes no v8 output. | `derived/egenes-gene-name-summary.json` |
| 6 | Worked example from the issue: `ENSG00000177000.10` → `MTHFR`. | excerpt row 4; `derived/…summary.json` `example` |

## Sources

- **Primary data.** GTEx Analysis v8 single-tissue cis-eQTL tar, `https://storage.googleapis.com/adult-gtex/bulk-qtl/v8/single-tissue-cis-qtl/GTEx_Analysis_v8_eQTL.tar`. This is the URL pinned in `bundles/manifest.json` (`pipeline_pins.gtex_eqtl`) and `GTEX_EQTL_URL`.
  - **Observed version:** object generation `1675875522137618`, last-modified 2023-02-08, size 1,562,828,800 bytes, MD5 `d35b32152bdb21316b2509c46b0af998` (`raw/gtex-v8-eqtl-tar-head.txt`, HEAD request 2026-10-13).
  - **Local copy:** the copy analysed (downloaded 2026-07-10) has the identical size and MD5, and SHA-256 `c61ac336daf86a92f1596ee59de7dd5e60d451a507a6d7a15d8b030a0266ac5d`.
  - **Accessed:** 2026-10-13.
- **Citation.** GTEx Consortium, "The GTEx Consortium atlas of genetic regulatory effects across human tissues," *Science* 369:1318-1330 (2020), PMID:32913098 / DOI:10.1126/science.aaz1776 (accessed 2026-10-13).
  - Title-matched via PubMed eSummary (`raw/pubmed-esummary-32913098.json`). The abstract describes v8 across 49 tissues, matching claim 1.
  - Correction/retraction check: efetch `CommentsCorrectionsList` (`raw/pubmed-efetch-32913098.xml`) holds a single `CommentIn` link, with no `ErratumIn`, `RetractionIn` or `ExpressionOfConcernIn`.
- **License.**
  - The repository's existing determination is open-access summary statistics: `bundles/manifest.json` `pipeline_pins.gtex_eqtl.license = open-access-summary-stats` and `docs/attribution.md`, citing the paper above.
  - The GTEx Portal terms page (`https://gtexportal.org/home/license`) is a JavaScript application, and a fetch on 2026-10-13 returned no readable terms. Its terms are therefore **not captured** here; this packet does not re-establish the license.

## Method and limits

- `derive_gene_name_summary.py` produced `derived/egenes-gene-name-summary.json` from the extracted tar. Run it as `python derive_gene_name_summary.py <extracted GTEx_Analysis_v8_eQTL dir>`.
- The summary is a **derived** record (counts), not a raw capture.
- The raw excerpt is the first six columns of four rows of an open-access file, copied verbatim.
- **Discovery-tool ladder (Consensus, Scite, specialist connectors): not invoked.** The claims concern the columns and labels of a specific data release, which literature search cannot attest. The release files themselves are the primary source.
- No biological or clinical claim is made. The badge shows GTEx's own label for an ID that GTEx reports.

## Inventory

The packet holds 9 files: this `README.md`, `manifest.json`, and the 7 files that `manifest.json` lists with SHA-256:
- `queries.json`
- `derive_gene_name_summary.py`
- `raw/` (4): `pubmed-esummary-32913098.json`, `pubmed-efetch-32913098.xml`, `gtex-v8-eqtl-tar-head.txt`, `whole-blood-v8-egenes-excerpt.tsv`
- `derived/egenes-gene-name-summary.json` (1)
