# DPYD coverage investigation

The investigation for [#2455](https://github.com/bioedca/Yeliztli/issues/2455)
refreshes the CPIC inventory and identifies a small follow-up group. It does
not expand the shipped panel. Every candidate still has release prerequisites.

The **2026-11-08** CPIC snapshot contains 28 non-normal DPYD records. Five map
to bundled definitions; the other 23 include **19 no-function records, three
single-variant decreased-function records and one overlapping HapB3 record**.
The 23-record difference is not proof that 23 independently callable alleles
are missing from every supported vendor array.

The [evidence packet](https://github.com/bioedca/Yeliztli/tree/main/data/science-evidence/2026-11-08-dpyd-coverage-2455)
retains the public source payloads, complete queries, source-specific versions
and terms, claim mapping, unknowns, per-candidate diplotypes and a reproducible
validator. Access dates throughout this page are **2026-11-08**.

## What the coverage check establishes

The public Illumina **GSA-24v3-0_A1** manifest has 654,027 assay records. Its
archive and embedded CSV match the repository's recorded SHA-256 hashes.
Checking both rsID and GRCh37 position finds loci for 21 of the 23 candidate
definitions, including both markers in the combined HapB3 record. The two
without a matching locus are `c.1024G>A` and `c.1774C>T`.

**GSA membership is not exact vendor coverage.** The audit does not establish
the custom content of 23andMe, AncestryDNA, FamilyTreeDNA or other supported
exports, probe performance, or a reliable call for a particular sample. Exact
vendor manifests were unavailable here; their per-candidate coverage remains
unknown. No personal sample was read. A missing locus never means a reference
genotype, and the earlier two-sample observation in #2120 is not generalized.

## Candidate disposition table

Coordinates below are **GRCh37 plus-strand reference > carrier allele**, not
CPIC's coding-minus descriptions or its source-native genomic positions.
Ensembl rsID mappings and independent VariantValidator coding-HGVS projections
agree for the SNVs. The deletions retain VariantValidator's normalized VCF
representation below and Ensembl's different repeat representation in the
packet; vendor I/D polarity and normalization remain release gates.

`NF/0` and `DF/0.5` mean CPIC's **No function / activity 0** and **Decreased
function / activity 0.5** classifications. These are registry definitions, not
new clinical effect-size claims. The defining guideline and current registry
are not independent sources; the definitional exception applies.[^cpic]

`Counts` means a gnomAD v2.1.1 aggregate record was retrieved. `Not found` means
that the exact variant query returned no record, **not zero frequency**.
For HapB3, counts refer to the two component loci, not a phased haplotype
frequency. No independent population conclusion has been established.

<!-- candidate-table:start -->
| CPIC record | Function/value | GRCh37 plus allele and rsID | GSA locus | Population | Disposition |
| --- | --- | --- | --- | --- | --- |
| `c.1024G>A` | NF/0 | `1:98058878 C>T` (rs183385770) | Not found | Counts | Coverage unknown |
| `c.1057C>T` | NF/0 | `1:98058845 G>A` (rs143154602) | Present | Not found | No-function prerequisite |
| `c.1129-5923C>G, c.1236G>A (HapB3)` | DF/0.5 | `1:98045449 G>C` (rs75017182); `1:98039419 C>T` (rs56038477) | Present | Counts; Counts | Definition overlap |
| `c.1156G>T (*12)` | NF/0 | `1:98039499 C>A` (rs78060119) | Present | Counts | No-function prerequisite |
| `c.1314T>G` | DF/0.5 | `1:98039341 A>C` (rs186169810) | Present | Counts | Follow up |
| `c.1475C>T` | NF/0 | `1:98015165 G>A` (rs72549304) | Present | Counts | No-function prerequisite |
| `c.1484A>G` | NF/0 | `1:98015156 T>C` (rs111858276) | Present | Not found | No-function prerequisite |
| `c.1774C>T` | NF/0 | `1:97915746 G>A` (rs59086055) | Not found | Counts | Coverage unknown |
| `c.1775G>A` | NF/0 | `1:97915745 C>T` (rs138616379) | Present | Counts | No-function prerequisite |
| `c.1777G>A` | NF/0 | `1:97915743 C>T` (rs145773863) | Present | Counts | No-function prerequisite |
| `c.1898delC (*3)` | NF/0 | `1:97915621 TG>T` (rs72549303) | Present | Not found | Indel prerequisite |
| `c.2021G>A` | NF/0 | `1:97839154 C>T` (rs137999090) | Present | Not found | No-function prerequisite |
| `c.2279C>T` | DF/0.5 | `1:97770835 G>A` (rs112766203) | Present | Counts | Follow up |
| `c.2639G>T` | NF/0 | `1:97564172 C>A` (rs55674432) | Present | Counts | No-function prerequisite |
| `c.2872A>G` | NF/0 | `1:97547921 T>C` (rs141044036) | Present | Counts | No-function prerequisite |
| `c.2933A>G` | NF/0 | `1:97544677 T>C` (rs72547601) | Present | Not found | No-function prerequisite |
| `c.295_298delTCAT (*7)` | NF/0 | `1:98205966 GATGA>G` (rs72549309) | Present | Counts | Indel prerequisite |
| `c.2983G>T (*10)` | NF/0 | `1:97544627 C>A` (rs1801268) | Present | Not found | No-function prerequisite |
| `c.601A>C` | NF/0 | `1:98164986 T>G` (rs72549308) | Present | Counts | No-function prerequisite |
| `c.61C>T` | NF/0 | `1:98348909 G>A` (rs72549310) | Present | Counts | No-function prerequisite |
| `c.632A>G` | NF/0 | `1:98164955 T>C` (rs72549307) | Present | Counts | No-function prerequisite |
| `c.703C>T (*8)` | NF/0 | `1:98157332 G>A` (rs1801266) | Present | Counts | No-function prerequisite |
| `c.868A>G` | DF/0.5 | `1:98060705 T>C` (rs146356975) | Present | Counts | Follow up |
<!-- candidate-table:end -->

Disposition keys:

- **Follow up:** the three decreased-function SNVs selected as a coherent,
  bounded next group. Each has two-source coordinate agreement, a matching
  public GSA locus and complete CPIC diplotype rows.
- **No-function prerequisite:** retain for a later slice after the conservative
  enumeration and existing-sample delivery dependencies are resolved.
- **Coverage unknown:** no matching locus in this exact GSA manifest; coverage
  on other arrays remains unknown.
- **Indel prerequisite:** verify the legacy/canonical HGVS relationship,
  normalized representation and vendor I/D semantics before implementation.
- **Definition overlap:** resolve matcher/reporting semantics; do not add a
  duplicate or a proxy-only risk definition.

## HapB3 is a definition distinction

CPIC defines the combined `c.1129-5923C>G, c.1236G>A (HapB3)` record with
**rs75017182 and rs56038477**. The bundled `c.1129-5923C>G` record specifies
**rs75017182 alone**. PharmVar's dated gene-info document describes the same
distinction and cross-references historical star labels rather than treating
them as a universal DPYD haplotype naming system.[^pharmvar]

The combined record therefore is not an identical definition with a second
name. Its overlap with the existing marker also means it is not evidence for
an additional independent risk locus to insert into the matcher. This audit
does not authorize using rs56038477 alone as a substitute, or inferring phase
from an unphased array export. The `*7` legacy name likewise differs from the
current location's `c.299_302del` description; both are retained explicitly.

## Population evidence and next implementation

The packet retains aggregate gnomAD AC/AN values, separately for exomes and
genomes and their population strata. Eighteen of the 24 distinct candidate
loci have records; six exact variants were not found. Null genome blocks and
zero denominators are unknown coverage, not absence in that population.
Sex and subpopulation strata overlap; they must not be summed with their
parent populations. No conversion to carrier prevalence is made.

The three selected candidates have population-stratified observations, but
this investigation did not establish a second independent cohort for each.
It withholds ancestry-enrichment and clinical-risk conclusions. In particular,
it does not rank candidates by global frequency or use a general statement
about a group of variants as allele-specific ancestry evidence.

The three-SNV implementation is tracked separately in
[#2632](https://github.com/bioedca/Yeliztli/issues/2632). Before releasing an
addition, it must:

1. Verify the intended allele is callable on at least one exact supported
   vendor array; public backbone membership alone does not close that gate.
2. Resolve [#2453](https://github.com/bioedca/Yeliztli/issues/2453) (conservative
   untyped enumeration) and [#2454](https://github.com/bioedca/Yeliztli/issues/2454)
   (delivery to already analyzed samples), as the parent triage requires.
3. Copy exact CPIC diplotype rows and preserve strand/allele guards. Each
   candidate has 84 registry diplotypes; the packet retains the seven with
   Reference, itself and the five bundled alleles. Their phenotype/activity
   combinations already have both drug guideline rows; this audit supplies
   no new dosing text.
4. Exercise the production caller and prescribing alerts with carrier,
   `hom_ref` non-carrier and untyped-array controls. Include compound calls,
   install/idempotency/leave-alone migration cases, existing-sample delivery,
   the caveat list/count and seed-count locks.
5. Refresh the evidence and editorial-notice checks on the implementation
   head. Independently substantiate any new population or clinical claim, or
   continue withholding it.

## Validation and limitations

Run the offline consistency check from the repository root:

```bash
python data/science-evidence/2026-11-08-dpyd-coverage-2455/audit.py --check
```

It checks the complete candidate set, exact CPIC pagination, nonempty
diplotype coverage, both reference and carrier bases, public probe membership,
variant identity for population records, existing drug/score coverage and raw
payload hashes. These checks validate software/artifact consistency, not
biological truth. There is no allele, API, UI, schema or migration change in
this investigation.

Consensus and Scite were invoked first. Scite returned no editorial notices
for the four primary DOIs retained for the CPIC framework/citation inventory;
the dated screening and its separate contrasting-*4* signal are recorded in
the packet. Initial HTTP/schema/rate-limit failures and the successful
retrieval fallbacks are recorded explicitly. No failed query is a pass.

[^cpic]: CPIC API `allele`, `allele_definition`, `sequence_location` and `diplotype`, plus the defining guideline [PMID:29152729](https://pubmed.ncbi.nlm.nih.gov/29152729/) / [DOI:10.1002/cpt.911](https://doi.org/10.1002/cpt.911) (accessed 2026-11-08). Both are CPIC products; this is a classification/definition claim. The 2017 publication's historical proxy and dosing statements are not adopted as current guidance here.

[^pharmvar]: [PharmVar DPYD Gene Info, version 1.4 dated 2024-05-20](https://a.storyblok.com/f/70677/x/2ff7a3c61b/gene-info_dpyd_v1-4.pdf), pages 1-3 (accessed 2026-11-08); agreement with the current CPIC definition is recorded with their overlapping curation. No claim that this PDF is the latest revision, and no new proxy-accuracy or clinical effect estimate, is made.
