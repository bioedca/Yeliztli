# DPYD coverage investigation for issue #2455

Accessed **2026-11-08**. Repository baseline:
`19dab90f3ca732765954ce0ba486048d8203e208`.

This is the bounded investigation authorized by the issue's triage audit. It
does not install alleles, change prescribing guidance, or certify an array test.
The maintainer-facing findings and complete candidate table are in
[`docs/maintainer/dpyd-coverage-investigation.md`](../../../docs/maintainer/dpyd-coverage-investigation.md).

## Result and scope

The refreshed CPIC API has 84 DPYD allele definitions, 28 classified as no
function or decreased function. Five of those definitions correspond to the
bundled panel. The remaining 23 records comprise 19 no-function records, three
single-variant decreased-function records, and one overlapping two-variant
HapB3 definition. They are not 23 independently established omissions in
supported vendor arrays.

The complete per-candidate record is `dispositions.json`: definition identity,
CPIC classification, GRCh37 carrier representation, coordinate sources,
public-manifest membership, population-data availability, verbatim CPIC
diplotypes, existing guideline coverage and disposition. Every entry has
`release_ready: false`. The next bounded group is `c.868A>G`, `c.1314T>G` and
`c.2279C>T`, tracked in [#2632](https://github.com/bioedca/Yeliztli/issues/2632),
conditional on exact vendor coverage and the #2453/#2454 release dependencies.
Selection does not assert clinical efficacy or compare ancestry
groups by global allele frequency.

## Sources, versions and permitted retention

| Source | Version/build and retained material | Terms and access |
| --- | --- | --- |
| CPIC API | DPYD allele, definition, sequence-location and diplotype payloads; sequence-location versions 100/108. No API-wide release is inferred from a GitHub release or PostgREST's software version. | [CPIC licensing](https://github.com/cpicpgx/cpic-data/blob/main/LICENSE.md), blob `81c504256332202d6527205483ab3de8789a82cc`: curated content CC0 1.0; API/database also subject to linked [ClinPGx terms](https://www.clinpgx.org/page/dataUsagePolicy). Primary source is ClinPGx; data can change. (accessed 2026-11-08) |
| Ensembl GRCh37 REST | Explicit GRCh37, chromosome 1, plus-strand mappings by rsID. No unspecified current-release number is asserted. | [Ensembl data terms](https://www.ensembl.org/info/about/legal/disclaimer.html); public variant metadata only. (accessed 2026-11-08) |
| VariantValidator | `NM_000110.4` coding HGVS input, no rsID input; GRCh37 `NC_000001.10`. Response metadata: service `4.0.1.dev7+gbdab9c72f`, HGVS `4.0.0`, `vvdb_2025_3`, `VV_SR_2025_02/master`, `vvta_2025_02`. | [Public service](https://variantvalidator.org/); retain coordinate/provenance responses, not software or third-party sequence libraries. Software license is not asserted to license every underlying dataset. (accessed 2026-11-08) |
| Illumina GSA-24v3-0_A1 | Manufacturer manifest dated 2019-08-07; 654,027 assay rows; exact ZIP/CSV hashes in `raw/gsa-manifest-audit.json`. | [Public manufacturer manifest](https://www.illumina.com/content/dam/illumina-support/documents/downloads/productfiles/global-screening-array-24/v3-0/GSA-24v3-0-A1-manifest-file-csv.zip). Only factual membership, locus, allele-pair and strand metadata retained. No manifest archive, probe sequence or full design is redistributed. (accessed 2026-11-08) |
| gnomAD | `gnomad_r2_1`, v2.1.1, GRCh37; aggregate exome/genome AC/AN and population strata. | [gnomAD policies](https://gnomad.broadinstitute.org/policies), CC0 public aggregate data. No individual-level data. (accessed 2026-11-08) |
| PharmVar | DPYD Gene Info `05-20-2024_v1.4`, pages 1-3, not asserted to be the latest document revision. | [Source document](https://a.storyblok.com/f/70677/x/2ff7a3c61b/gene-info_dpyd_v1-4.pdf); only source metadata and original factual paraphrases retained in `raw/pharmvar-nomenclature-derived.json`. The PDF/images are not redistributed. (accessed 2026-11-08) |
| PubMed / publications | Public identifiers, titles and correction links in `raw/pubmed-metadata-derived.json`. | Bibliographic metadata only; no full articles or abstracts redistributed. (accessed 2026-11-08) |
| Consensus / Scite | Public discovery metadata and editorial-notice screening. | Sanitized result metadata only; no institutional links, account details or long excerpts. These are discovery/checking tools, not primary evidence. (accessed 2026-11-08) |

## Claim mapping and independence

1. **CPIC definition and activity value:** the current CPIC registry plus its
   defining guideline, [PMID:29152729](https://pubmed.ncbi.nlm.nih.gov/29152729/),
   [DOI:10.1002/cpt.911](https://doi.org/10.1002/cpt.911)
   (accessed 2026-11-08). These are **not independent**: both are CPIC products.
   This is the contract's definitional exception, not evidence for a new
   toxicity effect size. The 2017 document's historical proxy statements and
   dosing text are not adopted as current guidance by this investigation.
2. **SNV coordinate identity:** Ensembl's dbSNP-based GRCh37 mapping agrees with
   VariantValidator's independent projection from coding HGVS. The latter
   request contains no rsID. The two derivations share the reference assembly
   by necessity, but the second is not a copy of the first's RefSNP mapping.
   All 26 distinct SNV loci in the non-normal inventory agree. The two indels
   retain both representations and are withheld from the initial group until
   normalization and vendor I/D polarity are explicitly validated.
3. **HapB3 identity:** CPIC defines the combined record with rs75017182 and
   rs56038477; the bundled record contains rs75017182 alone. PharmVar's named
   definition agrees. This is a nomenclature/definition comparison with
   overlapping curation, not two independent biological studies. A
   proxy-only allele or a duplicate match rule is not justified.
4. **Probe membership:** all assay rows of the checksum-pinned public GSA
   manifest were checked by rsID and independently mapped GRCh37 position.
   This establishes membership in that exact manifest only. Exact 23andMe,
   AncestryDNA and other vendor-specific custom manifests were not available
   in this investigation. Their coverage remains unknown. No personal sample
   was read; previous sample-derived presence observations are not generalized.
5. **Population evidence:** 18 of the 24 distinct candidate loci returned
   aggregate records; six exact variants returned `Variant not found`.
   Neither absence nor a null genome block means zero population frequency.
   Strata overlap (sex, ancestry and subpopulation), and exomes/genomes are
   not independent confirmations. No second independent cohort was
   established for each candidate, so ancestry enrichment, comparative risk
   and population-based prioritization are **withheld**. Counts and denominators
   are retained for future verification, not converted to carrier prevalence.

The CPIC rows cite [PMID:24648345](https://pubmed.ncbi.nlm.nih.gov/24648345/)
([DOI:10.1158/0008-5472.CAN-13-2482](https://doi.org/10.1158/0008-5472.CAN-13-2482)),
[PMID:23328581](https://pubmed.ncbi.nlm.nih.gov/23328581/)
([DOI:10.1158/0008-5472.CAN-12-3858](https://doi.org/10.1158/0008-5472.CAN-12-3858))
and [PMID:28295243](https://pubmed.ncbi.nlm.nih.gov/28295243/)
([DOI:10.1002/cpt.685](https://doi.org/10.1002/cpt.685))
(accessed 2026-11-08). Their bibliographic identity is checked here; this
packet does not treat CPIC's citation and its source paper as independent
replications, nor infer allele-specific ancestry from a group-level abstract.

## Retrieval and editorial checks

Consensus search and fetch, then Scite `verify_papers`, preceded the narrow
Ensembl/gnomAD/Entrez skill requests. Scite verified all four cited DOIs and
returned no editorial notices. The separate contrasting-discussion DOI was also
verified with no editorial notices (`raw/scite-contrast-source-verification.json`).
PubMed records contained no correction links.
This is a dated search result, not proof that notices cannot exist. Scite
flagged contrasting discussion of the separately classified DPYD *4 in
DOI:10.1177/1559325818803042; that signal is retained, not used to assert a new
finding about these candidates.

`cpic-queries.json` records exact counts/ranges for every page (84 alleles,
84 definitions, 83 sequence locations and 3,570 diplotypes). Each candidate has
84 CPIC diplotype rows, including its homozygote and reference pairing. The
seven rows involving itself, Reference and the five bundled alleles are
copied verbatim into its disposition. All their phenotype/activity pairs
already have fluorouracil and capecitabine guideline rows in this repository.
No activity score or phenotype was derived by summing allele values.

`coordinate-queries.json` and `gnomad-queries.json` record requests and saved
payload paths. Context7 had no matching CPIC or gnomAD entry: CPIC used its
published API and official PostgREST docs; gnomAD used its official schema and
live error response. Ensembl docs were available. The first gnomAD request
shape used the historical `variant_id` argument and failed; the API explicitly
required `variantId`. Rate-limit responses stopped the corrected run; remaining
candidate requests completed after backoff at 15-second intervals. Failures
were retained in local diagnostic evidence; the final query record retains the
intermediate rate-limit result as well. No failed request is counted as a
successful population lookup. CPIC's initial Python HTTP request returned 403;
the public curl request succeeded without authentication or settings changes.

## Reproduce the checks

```bash
python data/science-evidence/2026-11-08-dpyd-coverage-2455/audit.py --check
```

The validator checks complete CPIC counts, nonempty/unique candidate and
diplotype sets, both coordinate sources for SNVs, reference and carrier bases,
public probe identifiers/positions, exact variant identity for aggregate
counts, pre-existing drug/score coverage, the regenerated disposition artifact,
and all retained raw hashes. It does not turn software checks into scientific
validation. `raw-hashes.json` covers every retained raw payload.

Privacy: only public reference definitions, aggregate statistics, manufacturer
membership facts and bibliographic metadata are retained. There are no sample
genotypes, personal identifiers, private paths, credentials or probe sequences.
Slurm/NGS: N/A; this is lightweight public-reference retrieval and validation.
