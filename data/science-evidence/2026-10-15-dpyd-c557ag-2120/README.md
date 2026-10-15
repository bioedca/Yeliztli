# DPYD c.557A>G: evidence for issue #2120

## Scope

This packet supports adding one allele to the bundled CPIC tables
(`backend/data/cpic/cpic_alleles.csv`, `cpic_diplotypes.csv`, and their fixture seeds):

**DPYD `c.557A>G`, rs115232898, p.Tyr186Cys — Decreased function, activity value 0.5.**

It supports exactly three claims:

- **(a)** CPIC classifies this allele as decreased function with activity value 0.5, and assigns
  the diplotype phenotypes added here.
- **(b)** Its GRCh37 plus-strand representation is `1:98165030 T>C`, with `T` the normal/`*1`
  base and `C` the base a carrier has.
- **(c)** The allele is found predominantly in people of African ancestry, so omitting it lowered
  the panel's DPD-deficiency sensitivity for that group specifically.

It makes **no** claim about the magnitude of any individual's toxicity risk, and adds no dosing
text: every diplotype added maps onto activity-score guideline rows that already shipped (#2119).

Repository base: `5c99e911f9f5802c87d00cf1ee550f778b592688`

Access date: `2026-10-15`

## Evidence ladder

| Source | Result |
| --- | --- |
| Consensus | One search, full ranking in `raw/consensus-search-sanitized.json`. The free tier caps results at 3 and none addressed this allele specifically, so the variant-level evidence came from PubMed. Recorded as a shortfall, not a pass. |
| Scite | All **three** cited DOIs indexed; **no** editorial notices on any (`raw/scite-editorial-notices-sanitized.json`). |
| PubMed | Records and abstract excerpts in `raw/pubmed-records-sanitized.json`. |
| CPIC API | `allele`, `allele_definition`, `sequence_location` and `diplotype` endpoints, saved verbatim under `raw/cpic-api-*.json`. |
| Ensembl GRCh37 REST | RefSNP rs115232898 mapping (`raw/ensembl-grch37-rs115232898.json`). |
| VariantValidator | `NM_000110.4:c.557A>G` projected to GRCh37 (`raw/variantvalidator-grch37-NM_000110.4-c557AG.json`). |
| gnomAD v2.1.1 | Per-population allele frequencies for rs115232898, exomes and genomes (`raw/gnomad-v2-rs115232898-population-frequencies.json`). Frequencies only; no individual-level data. |

## Claim mapping

### (a) CPIC classifies c.557A>G as decreased function, activity 0.5

The panel's job is to implement CPIC, so the function assignment is a **definitional/registry**
claim: it is met by CPIC's defining guideline plus the CPIC registry that implements it, whose
**non-independence is recorded here** — both are CPIC products.

- **Registry.** `api.cpicpgx.org/v1/allele?genesymbol=eq.DPYD` (accessed 2026-10-15,
  `raw/cpic-api-dpyd-alleles.json`) lists `c.557A>G` with
  `clinicalfunctionalstatus: "Decreased function"`, `activityvalue: "0.5"`, citing PMID 24648345.
- **Defining document.** CPIC's fluoropyrimidine/DPYD guideline, 2017 update:
  PMID:29152729 / DOI:10.1002/cpt.911 (accessed 2026-10-15). This is the activity-score dosing
  framework the repo already implements for DPYD.
- **Diplotype phenotypes.** `api.cpicpgx.org/v1/diplotype?genesymbol=eq.DPYD`
  (`raw/cpic-api-dpyd-diplotypes-c557.json`) gives, for the five alleles this panel can call:

  | Diplotype | CPIC phenotype | Activity score |
  | --- | --- | --- |
  | `c.557A>G/Reference` | Intermediate Metabolizer | 1.5 |
  | `c.557A>G/c.557A>G` | Intermediate Metabolizer | 1.0 |
  | `c.557A>G/c.2846A>T` | Intermediate Metabolizer | 1.0 |
  | `c.557A>G/c.1129-5923C>G` | Intermediate Metabolizer | 1.0 |
  | `c.557A>G/c.1905+1G>A (*2A)` | Poor Metabolizer | 0.5 |
  | `c.557A>G/c.1679T>G (*13)` | Poor Metabolizer | 0.5 |

  Each of those six rows was added verbatim in activity score and phenotype. Their activity
  scores (1.5, 1.0, 0.5) all already have `cpic_guidelines` rows for fluorouracil and
  capecitabine, so **no guideline row was added or changed**.

**Independent functional support, with its limitation recorded.** Offer et al. 2014
(PMID:24648345 / DOI:10.1158/0008-5472.CAN-13-2482, accessed 2026-10-15) is the functional study
CPIC cites, and is independent of CPIC. It expressed 80 DPYD protein-coding variants and measured
5-FU conversion. Recorded honestly: its discussion reports the **Y186C substitution reduced
activity by only about 15%** relative to wild type, which the authors call "less than we
expected", noting a prior study focused on this variant found a similar reduction, and
hypothesising an effect on dimerisation. CPIC's `0.5` is therefore a **curation judgement across
several lines of evidence**, not a restatement of that single effect size. This packet asserts
only what CPIC assigns; it does not claim a specific enzyme-activity percentage, and the shipped
panel reports CPIC's activity score rather than any effect size.

### (b) GRCh37 plus-strand representation is 1:98165030 T>C

DPYD is on the minus strand, so the CPIC/HGVS coding change `c.557A>G` is the reverse complement
of the plus-strand change. The repo stores defining variants plus-strand, with `alt` the base a
carrier has (`tests/backend/test_cpic_allele_strand.py`). Two independent sources agree:

- **Ensembl GRCh37 REST** (`raw/ensembl-grch37-rs115232898.json`): rs115232898 maps to GRCh37
  `1:98165030`, strand `1` (forward), `allele_string` `T/A/C`, minor allele `C`, MAF 0.00639.
- **VariantValidator** (`raw/variantvalidator-grch37-NM_000110.4-c557AG.json`), queried on the
  **coding** HGVS with no rsID, so it is an independent projection rather than the same dbSNP
  record: `NM_000110.4:c.557A>G` → `NC_000001.10:g.98165030T>C`, VCF `{chr 1, pos 98165030,
  ref T, alt C}`, protein `NP_000101.2:p.(Tyr186Cys)`, zero validation warnings.
- **CPIC's own definition** (`raw/cpic-api-sequence-location-rs115232898.json`) agrees:
  `chromosomelocation: "g.97699474T>C"` on its contig, `proteinlocation: "p.Y186C"`,
  `dbsnpid: "rs115232898"`, with the variant allele `C`
  (`raw/cpic-api-allele-definition-c557.json`).

So the stored row is `ref: "T"`, `alt: "C"` — the plus-strand complement of `A>G`, with the
ref/alt roles preserved. The value is pinned in the strand guard so it cannot regress.

### (c) The allele is predominantly African-ancestry, which is why its omission was inequitable

Two independent, **allele-specific** sources sharing no cohort, dataset or upstream assertion:

- **gnomAD v2.1.1** (`raw/gnomad-v2-rs115232898-population-frequencies.json`, accessed
  2026-10-15) measures rs115232898 directly. Exomes (AN = 251,028): African **AF 0.0230** vs
  non-Finnish European **0.000053** — about **435x** — with **374 of 417** alt alleles observed in
  the African group. Genomes (AN = 31,384): African **AF 0.0187** against **0.0** in the
  non-Finnish European, Latino/Admixed American, Ashkenazi Jewish, East Asian and Finnish groups;
  the pooled **"Other" group is 0.00368** (4/1088), which is the only other non-zero genome group.
  gnomAD also independently reproduces the coordinates as `1-98165030-T-C`, making it a third
  agreeing source for claim (b).

  *Correction:* an earlier revision of this packet said the genomes were "0.0 in every other
  population", which the committed payload contradicts — the pooled "Other" group is non-zero.
  Caught in review of PR #2452 and corrected here, in `manifest.json` and in the user-facing
  footnote. The African-predominance conclusion is unchanged; the over-broad quantitative
  phrasing was wrong.
- Cunha-Junior et al. 2019 (PMID:31641844 / DOI:10.1007/s00280-019-03974-4, accessed 2026-10-15)
  reports that Y186C "is found almost exclusively in African populations and is related to low
  DPD function", measures 2.6% heterozygous prevalence across 115 African Brazilians, and
  concludes it "should be taken in account in targeted genotyping for fluoropyrimidine risk
  variants". Its 2.6% agrees with gnomAD's 2.30% African AF.

**Offer 2014 is not counted toward this claim.** It says the deficient variants it newly
classified are rare in Europeans and occur "at appreciable frequencies in other populations" — a
statement about a *group* of variants, not about c.557A>G. Review of PR #2452 flagged that relying
on it made this a one-source claim; gnomAD replaces it, and Offer is kept as context only.

## Array coverage, reported honestly

`raw/array-probe-coverage.json` records a read-only **probe-presence** check on the two local
sample databases. No genotype, allele or call is recorded in this packet.

- The rs115232898 probe is present on **23andMe v5** and absent on **AncestryDNA v2.0**.
- On an array that does not type it, the allele is now named by the engine's existing
  "Cannot exclude ..." note and the call is no longer `COMPLETE`, instead of the allele being
  invisible. `test_an_array_without_c557_cannot_exclude_it` locks that.
- The four previously bundled DPYD alleles are unchanged: rs3918290, rs55886062 and rs67376798
  are on both arrays, and rs75017182 (HapB3) on 23andMe v5 only.

## What this change does not do

- **No dosing text changed.** Every added diplotype maps to an activity score that already had
  fluorouracil and capecitabine rows.
- **No other allele added.** CPIC also defines `c.1314T>G`, `c.2279C>T` and `c.868A>G` as
  decreased function 0.5 (visible in `raw/cpic-api-dpyd-alleles.json`). Each needs its own
  strand verification and array-coverage check, so they are deliberately left out of this change,
  as the issue proposes.
- **No claim that the panel is now complete.** The shipped caveat — a normal DPYD call does not
  rule out DPD deficiency — remains correct and unchanged, since CPIC defines many more alleles
  than any array types.
