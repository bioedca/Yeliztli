# Specialized findings

--8<-- "health-disclaimer.md"

These condition-specific modules run automatically but don't have their own dashboard page —
their findings appear in the **Findings Explorer**. Each looks at a small, well-defined set of
variants.

## Hereditary haemochromatosis

**Genes/variants:** *HFE* C282Y (rs1800562), H63D (rs1799945).
Classifies your *HFE* genotype (e.g. C282Y homozygous, compound heterozygous) for hereditary
iron overload, with **sex-stratified penetrance** text. Most people with these genotypes are
never diagnosed (≈88% of men, ≈97% of women with C282Y/C282Y in one large cohort), and
compound-heterozygous calls are phase-inferred from unphased array data.

## Inherited thrombophilia

**Genes/variants:** Factor V Leiden (*F5* rs6025), Prothrombin G20210A (*F2* rs1799963).
Reports your combined genotype with odds ratios and **absolute-risk context** (risk rises
mainly around triggers such as hormones, pregnancy, surgery, or immobility). This is a
relative-risk module — absolute lifetime risk for most carriers stays low, and asymptomatic
carriers are not routinely anticoagulated.

## Alpha-1 antitrypsin deficiency

**Gene/variants:** *SERPINA1* Pi\*Z (rs28929474), Pi\*S (rs17580).
Reports PiZZ / PiSZ / PiSS / PiMZ / PiMS combinations with smoking and clinical-context notes.
The array types only Pi\*Z and Pi\*S, so rarer deficiency alleles aren't detected, and PiSZ is
phase-inferred.

## Age-related macular degeneration (AMD)

**Genes/variants:** *CFH* Y402H (rs1061170), *ARMS2/HTRA1* (rs10490924).
Reports common risk-allele combinations with odds ratios and absolute-risk caveats. These are
common GWAS variants (not pathogenic mutations), so evidence is capped; actual AMD risk also
depends on age, smoking, and ~50 other variants.

## APOL1 kidney risk

**Gene/variants:** *APOL1* G1 (rs73885319, rs60910145), G2 (rs71785313), with an N264K modifier.
A recessive-style risk model (two risk alleles = high risk) validated primarily in
African-ancestry populations; results are ancestry-gated and partial genotypes are never
reported as falsely low-risk.

## Gout & serum urate

**Genes/variants:** *ABCG2* Q141K (rs2231142), *SLC2A9* (rs13129697).
Reports urate-transporter risk genotypes with **ancestry-stratified** odds ratios (the *ABCG2*
effect is larger in East-Asian ancestry). Gout is multifactorial and most carriers never
develop it; the module gives no dietary or treatment advice.

## Leber hereditary optic neuropathy (LHON)

**Variants:** mtDNA m.11778G>A, m.3460G>A, m.14484T>C.
Reports each of the three primary LHON mutations if detected. Penetrance is **incomplete and
sex-biased** (≈50% of male vs ≈10% of female carriers ever lose vision), inheritance is
maternal only, and the array **cannot measure heteroplasmy** (the fraction of affected mtDNA).

## MT-RNR1 (aminoglycoside ototoxicity)

**Variants:** mtDNA m.1555A>G, m.1494C>T, m.1095T>C.
Following CPIC's aminoglycoside-avoidance guideline, reports carriers of these 12S-rRNA
variants, which raise the risk of hearing loss from aminoglycoside antibiotics. Maternally
inherited; heteroplasmy isn't measured; often off-chip, so a negative doesn't rule it out.

## G6PD deficiency

**Gene:** *G6PD* — 13 CPIC-defined deficiency variants (A−, Mediterranean, Canton, and others).
An **X-linked**, sex-aware pharmacogenomic context module: it assigns a phenotype (normal /
variable / deficient / indeterminate) and lists high-risk oxidative drugs (e.g. rasburicase,
primaquine, dapsone). Array coverage of these variants varies.

## BChE (butyrylcholinesterase)

**Gene/variants:** *BCHE* rs1799807 (atypical), rs1803274 (K-variant).
Context-only background on sensitivity to the anaesthetic muscle-relaxants succinylcholine and
mivacurium. It types only two of many *BCHE* variants and **does not store findings** — it's
purely interpretive background; true BChE deficiency is confirmed by an enzyme-activity assay
with your anaesthesia team.
