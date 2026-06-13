# Module reference

Yeliztli runs many analysis **modules** over your data. Each one loads a curated panel of
variants, extracts the ones you carry, and stores **findings** with an
[evidence rating](../getting-started/reading-your-results.md#findings-and-evidence-ratings).
For how to read each kind of output — evidence stars, categorical levels, diplotypes,
polygenic percentiles, and the recurring caveats — see the
[interpretation reference](interpretation-reference.md).

Modules come in three kinds:

- **Modules with their own page** in the app — you can open them from the dashboard and see a
  tailored view.
- **Findings-only modules** — they don't have a dedicated page, but their results appear in
  the unified **Findings Explorer** (these are documented together under
  [Specialized findings](specialized.md)).
- **Disclosure-gated modules** — sensitive results that stay hidden until you opt in.

!!! warning "How to read any module"
    Every module analyses **consumer genotyping-array** data and is **research/educational
    only** — not diagnostic. Many report **categorical levels** or **polygenic percentiles**
    rather than yes/no answers, and some withhold a number when the data can't support it.
    See [Intended use & disclaimers](../intended-use.md).

## Health & hereditary risk

| Module | What it analyses |
|--------|------------------|
| [Hereditary cancer](health-risk/cancer.md) | 28-gene hereditary-cancer panel + cancer polygenic scores |
| [Cardiovascular](health-risk/cardiovascular.md) | 16-gene panel: familial hypercholesterolemia, cardiomyopathy, channelopathy |
| [Carrier status](health-risk/carrier-status.md) | Reproductive carrier screening across 7 recessive-disease genes |
| [Gene health](health-risk/gene-health.md) | Categorical risk across ~17 conditions in 4 body systems |
| [Familial hypercholesterolemia](health-risk/familial-hypercholesterolemia.md) | FH-focused view: LDLR/APOB/PCSK9 + LDL-C polygenic score |
| [Metabolic](health-risk/metabolic.md) | Polygenic scores for type-2 diabetes and BMI/obesity |
| [Bone density (eBMD)](health-risk/bone-density-ebmd.md) | Heel bone-density polygenic score (fracture-risk context) |
| [Rare variants](rare-variants.md) | Customisable finder for rare and ultra-rare carried variants |

## Pharmacogenomics

| Module | What it analyses |
|--------|------------------|
| [Pharmacogenomics](pharma/pharmacogenomics.md) | Star-allele calling + CPIC prescribing context for 11 drug-metabolism genes |

## Wellness & traits

| Module | What it analyses |
|--------|------------------|
| [Nutrigenomics](wellness/nutrigenomics.md) | Nutrient-metabolism pathways (folate, vitamin D, B12, omega-3, iron, lactose) |
| [Methylation](wellness/methylation.md) | MTHFR and five methylation-cycle pathways |
| [Fitness](wellness/fitness.md) | Endurance, power, recovery, and training-response pathways |
| [Sleep](wellness/sleep.md) | Caffeine metabolism, chronotype, sleep quality and disorders |
| [Skin](wellness/skin.md) | Pigmentation/UV, barrier, oxidative-aging, and micronutrient pathways |
| [Allergy & immune](wellness/allergy.md) | Atopic, drug-hypersensitivity, food (celiac), and histamine pathways |
| [Traits & personality](wellness/traits-personality.md) | Cognitive, Big-Five, and behavioural trait scores (research-use) |

## Ancestry

| Module | What it analyses |
|--------|------------------|
| [Ancestry](ancestry/ancestry.md) | Global ancestry (PCA + admixture) and mtDNA / Y haplogroups |

## Disclosure-gated (opt-in)

These stay hidden until you explicitly choose to view them.

| Module | What it analyses |
|--------|------------------|
| [APOE](gated/apoe.md) | APOE ε2/ε3/ε4 diplotype — cardiovascular, Alzheimer's, and lipid context |
| [Parkinson's (LRRK2)](gated/parkinsons.md) | LRRK2 G2019S risk variant, with reduced-penetrance framing |
| [Sex-chromosome aneuploidy](gated/sex-aneuploidy.md) | Screen for an XXY signature (confirmation-only) |

## Specialized findings

Ten more condition-specific modules run automatically and surface in the **Findings
Explorer** — haemochromatosis, thrombophilia, alpha-1 antitrypsin, AMD, APOL1 kidney risk,
gout, LHON, MT-RNR1, G6PD, and BChE. See **[Specialized findings](specialized.md)**.

## Quality control

A quality-control pass (call rate, heterozygosity, per-chromosome counts, sex inference)
runs on every sample and feeds the QC summary on the dashboard. It is not a finding-producing
module you pick — it's always-on background quality reporting.
