# Vendor export coverage for three DPYD candidates

The scope is the four versions accepted by the upload guide at repository
commit `cf08f712a130591598eae00648a1b8dce2d0b384`. The completed matrix
in `docs/maintainer/dpyd-vendor-export-coverage.md` records twelve unknown
vendor-export statuses and three verified Illumina design memberships.
No individual exports were used. The cutoff is 2026-11-19 UTC.

## Claim-to-source map

- **Public design membership and probe identity.** The manufacturer archive for GSA-24v3-0_A1 matches the SHA-256 already recorded by #2455. A fresh in-memory scan of all 654,027 assay records matched each candidate by both rsID and its independently established GRCh37 position. Each has one corresponding probe. Preserve the source-native strand and address fields. [Illumina GSA-24v3-0_A1 manifest](https://www.illumina.com/content/dam/illumina-support/documents/downloads/productfiles/global-screening-array-24/v3-0/GSA-24v3-0-A1-manifest-file-csv.zip) (accessed 2026-11-19). Source hashes, exact probe IDs and addresses are in `data/science-evidence/2026-11-19-dpyd-vendor-exports-2719/raw/illumina-probes.json`.
- **23andMe chip design.** Section 2, page 2 of the manufacturer's r8_g1 supplementary materials, revision 1.1 dated 2026-07-29, describes the v3, v4 and v5 designs. The technical overview points to this supplement. The section describes each platform but does not enumerate the three candidate probes or export rules. [23andMe r8_g1 supplement](https://permalinks.23andme.com/pdf/supplemental-r8_g1-materials.pdf) (accessed 2026-11-19). The dated digest and extracted design fields are in `raw/23andme-research-platforms.json`.
- **Clinical report versus download.** In section “Indications for Use,” page 3, the FDA filing K193492 identifies the two DPYD variant entries in that report. None of the three candidates is listed. This is an explicit boundary of that filing, not an exclusion from the raw file. [FDA K193492](https://www.accessdata.fda.gov/cdrh_docs/pdf19/K193492.pdf) (accessed 2026-11-19). `raw/23andme-fda-report.json` retains the two listed identifiers and document digest.
- **Export conventions.** Manufacturer articles describe 23andMe's internal marker names and Ancestry's export rsID and forward-strand GRCh37 convention. Their scope is format, not versioned candidate membership. [23andMe identifiers](https://support.23andme.org/hc/en-us/articles/42965294875031-What-Are-RS-Numbers-Rsid) (accessed 2026-11-19). [Ancestry download format](https://help.ancestry.com/hc/en-us/articles/53933317283603-Downloading-DNA-Data) (accessed 2026-11-19). Selected facts and the direct-retrieval limitation are in `raw/export-documentation.json`.
- **Ancestry research methods.** Han et al. describe genotyping on an Illumina Human OmniExpress platform. Their research methods do not certify a versioned AncestryDNA v2.0 export list. NCBI EFetch returned no linked correction or retraction on the retrieval date. [PMID:28169989](https://pubmed.ncbi.nlm.nih.gov/28169989/) and [DOI:10.1038/ncomms14238](https://doi.org/10.1038/ncomms14238) (accessed 2026-11-19). See `raw/ancestry-methods.json` and `raw/pubmed-methods.json`.
- The [#2455 source packet](../2026-11-08-dpyd-coverage-2455/README.md) records the transcript and independent GRCh37 coordinate mappings, originally retrieved 2026-11-08. The new Illumina retrieval repeats the same manufacturer's evidence. The 23andMe supplement and FDA report also concern the same manufacturer. None supplies independent genotype-performance validation. No clinical-effect or performance claim is made.

## Discovery boundaries

Consensus query: `DPYD rs146356975 rs186169810 rs112766203 SNP microarray probe manufacturer 23andMe AncestryDNA raw data export coverage variant chip platform`.
Scite query: `"DPYD" AND ("rs146356975" OR "rs186169810" OR "rs112766203") AND ("23andMe" OR "AncestryDNA" OR "microarray")`. The retrieved literature
did not supply a versioned consumer export manifest for these probes.
Searches of public Illumina downloads, the two vendors' technical/support
publications and FDA device documentation used the three rsIDs, chip
versions, provider names and the terms `probe`, `manifest` and `raw data`.
NCBI GEO returned zero platform titles for the exact query in
`raw/geo-platform-search.json`. This bounded catalog search cannot establish
absence from other titles, archives, vendor channels or lots.

No patient call sets, third-party reconstructed lists, inferred chip unions,
imputation results or individual download examples were used to label a cell.
Neither absence of evidence nor a different regulated product establishes
a negative raw-export result. A later authoritative source should update only
the cells it actually covers and retain its revision and alias mapping.

The retained files contain factual metadata, source identifiers and limited
paraphrases. The vendor and regulator documents, artwork, probe sequences and
manifest are not redistributed. No license to redistribute proprietary chip
designs or vendor documents is asserted. The published research article is
open access; the retained NCBI and Europe PMC records are limited to provenance
and method metadata. [NCBI usage policy](https://www.ncbi.nlm.nih.gov/home/about/policies/) (accessed 2026-11-19).
