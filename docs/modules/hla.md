# HLA (imputed)

--8<-- "health-disclaimer.md"

!!! warning "Provisioned feature"
    The **HLA (imputed)** page is visible in the app for every install, but it stays empty
    until an operator installs the HIBAG runtime, supplies ancestry-specific HIBAG model
    files, and runs the HLA prediction script for a sample. An empty page usually means
    "not provisioned yet", not a broken sample.

HLA (imputed) predicts classical HLA alleles from SNP-array data and uses those calls to
power four surfaces:

- **Drug hypersensitivity** — HLA-drug safety findings for abacavir, carbamazepine,
  oxcarbazepine, phenytoin/fosphenytoin, allopurinol, and dapsone [1-6,9-13].
- **Disease rule-outs** — celiac HLA-DQ and narcolepsy type 1 HLA-DQB1\*06:02 context.
- **Autoimmune susceptibility** — selected HLA susceptibility markers such as HLA-B\*27,
  HLA-C\*06:02, rheumatoid-arthritis shared epitope alleles, and type-1-diabetes DR-DQ
  patterns.
- **Raw imputed-HLA viewer/export** — the called HLA loci, posterior probability, low-confidence
  flag, source, and ancestry model.

HIBAG is an HLA imputation method: it predicts HLA alleles from dense SNP genotypes using
attribute bagging, with published validation performance that depends on locus, array content,
and ancestry [7]. Later comparisons show that SNP-based HLA imputation is still not as accurate
as molecular HLA genotyping, especially for harder loci and diverse populations [8].

## How to read it

Treat every result as a **screening lead**, not a clinical HLA type.

- **Imputed is not typed.** Confirm any result that could change medication, diagnosis, or care
  with clinical high-resolution HLA typing.
- **Never use imputed HLA for transplant, organ, or stem-cell donor/recipient matching.** The raw
  viewer repeats this guard and embeds it in exported CSV files.
- **A positive drug-safety finding is not a medication order.** It means the imputed allele
  matches an HLA-drug association that should be discussed with a clinician and confirmed before
  changing therapy [1-6,9-13].
- **A negative finding is not universal reassurance.** HLA-drug test performance varies by drug
  pair and population; many pairs have high negative predictive value but low positive predictive
  value [9].
- **Ancestry and model choice matter.** Use the model that best matches the sample ancestry, and
  interpret mismatches or admixed ancestry with extra caution.
- **Low-confidence calls are flagged, not hidden.** They remain visible so you can see why a
  section is uncertain, but they should not be treated as reliable positives or negatives.

## Why the page may be empty

Default Yeliztli installs do not run HLA imputation automatically. If a sample has no persisted
`hla_calls` table entries, the HLA page reports that no imputed HLA calls are available. The
Allergy module's single-tag HLA proxy fallback may still produce limited HLA-proxy findings, but
that is separate from this first-class imputed-HLA page.

## Operator setup

HIBAG is intentionally a bring-your-own runtime and model workflow. Yeliztli does not bundle the
R/Bioconductor HIBAG package or pre-fit HLA model files.

1. Install R, `Rscript`, and the Bioconductor `HIBAG` package on the machine that runs the
   backend.
2. Fetch the required pre-fit HIBAG model files yourself, respecting their license terms. Yeliztli
   expects files named `{ancestry}-HLA4.RData`; supported ancestry names are `European`, `Asian`,
   `Hispanic`, and `African`.
3. Configure the runtime and model directory:

   ```bash
   export YELIZTLI_HIBAG_RSCRIPT=/usr/bin/Rscript
   export YELIZTLI_HIBAG_MODEL_DIR=/path/to/hibag-models
   ```

   The same values can be set as `hibag_rscript` and `hibag_model_dir` in
   `~/.yeliztli/config.toml`.

4. Prepare the sample's HLA-region PLINK input from an already annotated sample database:

   ```bash
   python scripts/prepare_hla_input.py \
     --sample-db ~/.yeliztli/samples/<sample>.db \
     --out-prefix ~/.yeliztli/hla/<sample>/<sample>
   ```

5. Run prediction and persist the calls:

   ```bash
   python scripts/predict_hla.py \
     --sample-db ~/.yeliztli/samples/<sample>.db \
     --work-dir ~/.yeliztli/hla/<sample> \
     --ancestry European
   ```

6. Reopen **HLA (imputed)** for that sample. You can also check `/api/hla/status` to confirm
   whether `Rscript` and at least one ancestry model are visible to the backend.

!!! note "Model files are not Yeliztli reference data"
    HIBAG model files are user-supplied external inputs, not release-bundled Yeliztli
    reference data. See the [external inputs strategy](../external-inputs-strategy.md)
    for the license posture.

## References

[1] Martin MA, Hoffman JM, Freimuth RR, et al. [Clinical Pharmacogenetics Implementation Consortium Guidelines for HLA-B Genotype and Abacavir Dosing: 2014 Update](https://doi.org/10.1038/clpt.2014.38). *Clinical Pharmacology & Therapeutics*. 2014. DOI: 10.1038/clpt.2014.38; PMID: 24561393.

[2] Leckband SG, Kelsoe JR, Dunnenberger HM, et al. [Clinical Pharmacogenetics Implementation Consortium Guideline for HLA Genotype and Use of Carbamazepine and Oxcarbazepine: 2017 Update](https://doi.org/10.1002/cpt.1004). *Clinical Pharmacology & Therapeutics*. 2018. DOI: 10.1002/cpt.1004; PMID: 29392710.

[3] Karnes JH, Rettie AE, Somogyi AA, et al. [Clinical Pharmacogenetics Implementation Consortium (CPIC) Guideline for CYP2C9 and HLA-B Genotypes and Phenytoin Dosing: 2020 Update](https://doi.org/10.1002/cpt.2008). *Clinical Pharmacology & Therapeutics*. 2021. DOI: 10.1002/cpt.2008; PMID: 32779747.

[4] Hershfield MS, Callaghan JT, Tassaneeyakul W, et al. [Clinical Pharmacogenetics Implementation Consortium Guidelines for Human Leukocyte Antigen-B Genotype and Allopurinol Dosing](https://doi.org/10.1038/clpt.2012.209). *Clinical Pharmacology & Therapeutics*. 2013. DOI: 10.1038/clpt.2012.209; PMID: 23232549.

[5] Zhang FR, Liu H, Irwanto A, et al. [HLA-B\*13:01 and the Dapsone Hypersensitivity Syndrome](https://doi.org/10.1056/NEJMoa1213096). *New England Journal of Medicine*. 2013. DOI: 10.1056/NEJMoa1213096; PMID: 24152261.

[6] Liu H, Wang Z, Bao F, et al. [Evaluation of Prospective HLA-B\*13:01 Screening to Prevent Dapsone Hypersensitivity Syndrome in Patients With Leprosy](https://doi.org/10.1001/jamadermatol.2018.5360). *JAMA Dermatology*. 2019. DOI: 10.1001/jamadermatol.2018.5360; PMID: 30916737.

[7] Zheng X, Shen J, Cox C, et al. [HIBAG - HLA Genotype Imputation with Attribute Bagging](https://doi.org/10.1038/tpj.2013.18). *The Pharmacogenomics Journal*. 2014;14:192-200. DOI: 10.1038/tpj.2013.18.

[8] Pappas DJ, Lizee A, Paunic V, et al. [Significant variation between SNP-based HLA imputations in diverse populations: the last mile is the hardest](https://doi.org/10.1038/tpj.2017.7). *The Pharmacogenomics Journal*. 2018;18:367-376. DOI: 10.1038/tpj.2017.7.

[9] Manson LEN, Swen JJ, Guchelaar HJ. [Diagnostic Test Criteria for HLA Genotyping to Prevent Drug Hypersensitivity Reactions: A Systematic Review of Actionable HLA Recommendations in CPIC and DPWG Guidelines](https://doi.org/10.3389/fphar.2020.567048). *Frontiers in Pharmacology*. 2020;11:567048. DOI: 10.3389/fphar.2020.567048.

[10] Kaniwa N, Saito Y, Aihara M, et al. [HLA-B\*1511 is a risk factor for carbamazepine-induced Stevens-Johnson syndrome and toxic epidermal necrolysis in Japanese patients](https://doi.org/10.1111/j.1528-1167.2010.02766.x). *Epilepsia*. 2010;51:2461-2465. DOI: 10.1111/j.1528-1167.2010.02766.x; PMID: 21204807.

[11] Wong CSM, Yap DYH, Ip P, et al. [HLA-B\*15:11 status and carbamazepine-induced severe cutaneous adverse drug reactions in HLA-B\*15:02 negative Chinese](https://doi.org/10.1111/ijd.15792). *International Journal of Dermatology*. 2022;61:486-491. DOI: 10.1111/ijd.15792; PMID: 34553372.

[12] Biswas M, Ershadian M, Shobana J, Nguyen AH, Sukasem C. [Associations of HLA genetic variants with carbamazepine-induced cutaneous adverse drug reactions: An updated meta-analysis](https://doi.org/10.1111/cts.13291). *Clinical and Translational Science*. 2022;15:1887-1905. DOI: 10.1111/cts.13291; PMID: 35599240.

[13] Manson LEN, Nijenhuis M, Soree B, et al. [Dutch Pharmacogenetics Working Group (DPWG) guideline for the gene-drug interaction of CYP2C9, HLA-A and HLA-B with anti-epileptic drugs](https://doi.org/10.1038/s41431-024-01572-4). *European Journal of Human Genetics*. 2024;32:903-911. DOI: 10.1038/s41431-024-01572-4; PMID: 38570725.
