# Public evidence for mixed Y lineage markers

The decision proposal for #2709 is in `docs/maintainer/mixed-y-marker-calls.md`. This packet records source scope and the production behavior at revision `7be123f382be44cc3c0936d2787038886b6488f7`. All sample rows used to exercise the software were generated locally from the bundled reference or synthetic sex-inference padding.

## Source register

| Source and access date | Supports | Retained material |
| --- | --- | --- |
| [23andMe article 42965202914711](https://support.23andme.org/hc/en-us/articles/42965202914711-How-23andMe-Reports-Genotypes) (accessed 2026-11-19) | Its general single-allele convention for most male X/Y markers. Its downloadable no-call token is two hyphens. The guide has no version-specific mixed-Y specification. | `raw/vendor-guides.json` records two short excerpts, article revision and body digest. |
| [Ancestry article 53933317283603](https://help.ancestry.com/hc/en-us/articles/53933317283603-Downloading-DNA-Data) (accessed 2026-11-19) | General last-two-column convention, numeric sex-chromosome examples and forward-strand GRCh37. It does not document v2.0 non-PAR doubling, a mixed-call interpretation, or code 25. | Same file, fetched from the manufacturer's public help API. |
| [GRC assembly GCF_000001405.25](https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/001/405/GCF_000001405.25_GRCh37.p13/GCF_000001405.25_GRCh37.p13_assembly_regions.txt) (accessed 2026-11-19) | The four 1-based inclusive human reference X/Y PAR intervals. | `raw/grch37-par.json` records those rows and the complete source file digest. |
| [PMID:12815422](https://pubmed.ncbi.nlm.nih.gov/12815422/) · [DOI:10.1038/nature01722](https://doi.org/10.1038/nature01722) (accessed 2026-11-19) | Skaletsky et al. describe distinct human MSY sequence classes, including ampliconic regions. Outside-PAR placement alone cannot establish probe specificity. | `raw/pubmed.json` retains bibliographic metadata. PubMed lists a commentary and no correction or retraction. |
| [PMID:24281365](https://pubmed.ncbi.nlm.nih.gov/24281365/) · [DOI:10.1038/ejhg.2013.272](https://doi.org/10.1038/ejhg.2013.272) (accessed 2026-11-19) | Peng et al. excluded Y-SNP sites with heterozygous calls in their array study of 117 males. The study filters sites in a cohort and provides no individual download or lineage-probability contract. | Same file contains the methodological excerpt and primary full-text link. No linked PubMed correction or retraction. |

The vendors document their own products independently. Neither certifies the other's conventions. The GRC region table defines the assembly boundaries used by the software comparison. It shares human Y reference history with the structural paper and is not independent validation of ancestry accuracy. Peng et al. used their own array cohort and a different analysis from the reference-assembly work. No two sources here establish the biological cause, population frequency or clinical meaning of an individual mixed export call. The proposal makes no such inference. The result at a locus with an ambiguous call remains unassessed for that marker.

Manufacturer content is copyright to its respective owner. Only the listed short excerpts, factual identifiers and digests are retained. The GRC extract is reference assembly metadata. The paper records include bibliographic facts and a short methods fragment. No figure, full text, raw manufacturer HTML or individual genome is redistributed. `queries.json` records discovery, direct retrieval and limitations.

## Software and synthetic cases

`raw/bundle-scope.json` records the GRCh37 bundle digest, coordinate range and nineteen internal IDs. The complete 154-marker list is the Y subtree of `backend/data/panels/haplogroup_bundle.json`. The source-named ancestral alleles are in `scripts/y_haplogroup_source.json`. Enumerating positions and comparing them with the GRC intervals gave no overlaps. All comparisons begin after normalization to the repository build. The package's historical v5-era exemplars are not measurements of consumer export prevalence.

Run these commands from the repository root after installing development dependencies.

```sh
mkdir -p output
python data/science-evidence/2026-11-19-mixed-y-2709/measure.py > output/mixed-y-replay.json
cmp data/science-evidence/2026-11-19-mixed-y-2709/raw/synthetic-measurements.json output/mixed-y-replay.json
```

The script creates an in-memory schema for each case and uses the shipped bundle and `assign_haplogroups`. Twenty-one input perturbations and three misplaced-identifier controls run against both raw and annotated sources (48 assignments). Seventeen separate parser controls cover the four supported export versions. The v4 and v5 parser controls use the actual GRCh37 terminal locus. The v3 parser-only control uses a synthetic identifier and position. The B-marker controls include a mixed pair made entirely from known non-derived bases. No real genotype inputs, imputation, network access or cluster jobs are needed. Sign-off on the scoring choice and any vendor exceptions is still required before #2472 changes production behavior.
