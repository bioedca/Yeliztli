"""Hardcoded disclaimer text for Yeliztli.

All disclaimer, gate, and carrier status text lives here.
Referenced by the setup wizard (global disclaimer) and analysis modules
(APOE gate, carrier status, cancer, cardiovascular).
"""

# ── Global first-launch disclaimer ────────────────────────────────────

GLOBAL_DISCLAIMER_TITLE = "Important Information About Yeliztli"

GLOBAL_DISCLAIMER_TEXT = """\
Yeliztli is an educational and research tool designed to help you \
explore your personal genomic data. It is NOT a medical device and has \
NOT been reviewed or approved by the FDA or any regulatory authority.

Please read and understand the following before proceeding:

1. **Not a diagnostic tool.** The information provided by Yeliztli \
is for educational and research purposes only. It should not be used to \
diagnose, treat, cure, or prevent any disease or medical condition.

2. **Not a substitute for professional medical advice.** Always consult \
a qualified healthcare provider or certified genetic counselor before \
making any medical decisions based on genetic information. Do not \
disregard professional medical advice or delay seeking it because of \
something you have read in this application.

3. **Variant interpretation has limitations.** Genetic variant \
classifications change over time as scientific understanding evolves. \
A variant classified as "benign" today may be reclassified in the \
future, and vice versa. Yeliztli uses publicly available databases \
that may not reflect the most current scientific consensus.

4. **Genotyping chip limitations.** Consumer genotyping chips (such as \
23andMe) test only a subset of genetic variants. A negative result does \
not mean you do not carry a particular variant — it may simply not have \
been tested. Clinical-grade genetic testing is required for definitive \
diagnostic results.

5. **Population-specific considerations.** Risk scores and frequency \
data may not be equally accurate across all ancestral populations. Many \
genetic studies have been conducted primarily in populations of European \
descent, which may limit the applicability of results to other groups.

6. **Privacy responsibility.** You are responsible for the security of \
your own genetic data. Yeliztli runs locally on your computer and \
does not transmit your genetic data to external servers (except for \
optional PubMed literature lookups which send gene names only, never \
variant data).

7. **Emotional preparedness.** Genetic information may reveal unexpected \
findings about health risks, carrier status, or ancestry. Consider \
whether you are prepared to receive potentially sensitive information \
before proceeding.

By clicking "I Understand and Accept," you acknowledge that you have \
read and understood these limitations and agree to use Yeliztli \
solely for educational and research purposes.\
"""

GLOBAL_DISCLAIMER_ACCEPT_LABEL = "I Understand and Accept"

# ── APOE opt-in disclosure gate ────────────────────────────────────────

APOE_GATE_TITLE = "APOE Genetic Information Disclosure"

APOE_GATE_TEXT = """\
You are about to view information about your APOE genotype. The APOE \
gene has variants (particularly the e4 allele) that have been associated \
with increased risk of late-onset Alzheimer's disease and cardiovascular \
conditions.

**Important considerations before viewing:**

- Having an APOE e4 allele does NOT mean you will develop Alzheimer's \
disease. Many people with e4 never develop the condition, and many \
people without e4 do.

- APOE genotype is only one of many factors that influence disease risk. \
Lifestyle, environment, and other genetic factors also play significant \
roles.

- This information may cause significant emotional distress. You may \
wish to have a support person available or to consult with a genetic \
counselor before viewing.

- This result is based on a consumer genotyping chip and is NOT a \
clinical diagnostic test.

**Resources:**
- National Institute on Aging: https://www.nia.nih.gov/health/alzheimers-causes-and-risk-factors/alzheimers-disease-genetics-fact-sheet
- Alzheimer's Association: https://www.alz.org/alzheimers-dementia/what-is-alzheimers/causes-and-risk-factors/genetics
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/

This gate cannot be dismissed. You must actively choose to view or skip \
APOE information each time you access this section.\
"""

APOE_GATE_ACCEPT_LABEL = "I Understand — Show My APOE Results"
APOE_GATE_DECLINE_LABEL = "Not Now — Skip APOE Results"

# ── Parkinson's (LRRK2 G2019S) opt-in disclosure gate ───────────────────

PARKINSONS_GATE_TITLE = "Parkinson's Disease Genetic Information Disclosure"

PARKINSONS_GATE_TEXT = """\
You are about to view information about LRRK2 G2019S, the most common known \
genetic risk factor for Parkinson's disease.

**Important considerations before viewing:**

- Carrying LRRK2 G2019S does NOT mean you will develop Parkinson's disease. \
Penetrance is reduced and age-dependent — lifetime risk for carriers is \
estimated at roughly 25-42.5% by age 80, so most carriers never develop the \
disease. A positive result is not a diagnosis or a prediction.

- There is no proven way to prevent Parkinson's disease, and a positive result \
does not call for any specific preventive treatment. The value of knowing is \
personal — for awareness, family planning, or research participation.

- This information may cause significant emotional distress. You may wish to \
have a support person available, or to consult a neurologist or genetic \
counselor, before viewing.

- GBA1, another Parkinson's-associated gene, is deliberately NOT reported from \
this array: a nearby pseudogene (GBAP1) makes array-based GBA1 genotyping \
unreliable, and we will not present an unreliable result.

- This result is based on a consumer genotyping chip and is NOT a clinical \
diagnostic test. Confirm any actionable result in a CLIA/accredited laboratory.

**Resources:**
- The Michael J. Fox Foundation (Genetics): https://www.michaeljfox.org/news/genetics-and-parkinsons
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/

This gate cannot be dismissed. You must actively choose to view or skip \
Parkinson's information each time you access this section.\
"""

PARKINSONS_GATE_ACCEPT_LABEL = "I Understand — Show My Parkinson's Results"
PARKINSONS_GATE_DECLINE_LABEL = "Not Now — Skip Parkinson's Results"

# ── Sex-chromosome aneuploidy screen opt-in disclosure gate ─────────────

ANEUPLOIDY_GATE_TITLE = "Sex-Chromosome Screen Disclosure"

ANEUPLOIDY_GATE_TEXT = """\
You are about to view a screen for a sex-chromosome difference (an XXY, or \
Klinefelter, pattern) based on your genotype data.

**Important considerations before viewing:**

- This is a SCREEN, not a diagnosis. A positive screen must be confirmed by \
clinical karyotyping (a chromosome test). Many people with a sex-chromosome \
difference are healthy and never knew they had one.

- It can detect only the XXY pattern from this kind of data. It CANNOT detect \
Turner syndrome (45,X) or XYY, which need DNA-quantity measurements that a \
genotyping chip does not provide. A negative screen is not a karyotype and does \
not rule these out.

- This information can be unexpected and emotionally significant, including for \
how you understand your own body. You may wish to have a support person \
available, or to consult a genetic counselor, before viewing.

- This screen never changes your recorded sex.

- This result is based on a consumer genotyping chip and is NOT a clinical \
diagnostic test.

**Resources:**
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/
- Genetic and Rare Diseases (GARD) Information Center: https://rarediseases.info.nih.gov/

This gate cannot be dismissed. You must actively choose to view or skip this \
screen each time you access this section.\
"""

ANEUPLOIDY_GATE_ACCEPT_LABEL = "I Understand — Show the Screen Result"
ANEUPLOIDY_GATE_DECLINE_LABEL = "Not Now — Skip This Screen"

# ── Carrier status disclaimer ──────────────────────────────────────────

CARRIER_STATUS_DISCLAIMER_TITLE = "About Carrier Status Results"

CARRIER_STATUS_DISCLAIMER_TEXT = """\
Carrier status results indicate whether you carry one copy (heterozygous) \
of a variant associated with a genetic condition. Carriers typically do \
not show symptoms of the condition themselves.

**This information is most relevant in a reproductive context.** If both \
partners carry a variant in the same autosomal recessive gene, there is \
a 25% chance with each pregnancy that the child will be affected by the \
condition. This is the basis of carrier screening in family planning.

**Please understand the following before reviewing:**

1. **Carrier ≠ affected.** Being a carrier means you have one working \
copy and one non-working copy of a gene. For autosomal recessive \
conditions (such as Cystic Fibrosis, Sickle Cell Disease, Tay-Sachs, \
Gaucher Disease, and Spinal Muscular Atrophy), carriers are typically \
healthy and unaffected.

2. **BRCA1/BRCA2 are a special case.** These genes follow autosomal \
dominant inheritance for cancer predisposition. A single pathogenic \
variant confers personal cancer risk (see the Cancer module) AND \
reproductive carrier risk. Both perspectives are shown in Yeliztli \
with distinct framing.

3. **Genotyping chip limitations.** Consumer genotyping chips test only \
a subset of known variants in each gene. A negative carrier result does \
NOT guarantee that you are not a carrier. Clinical-grade carrier \
screening (expanded carrier panels with full gene sequencing) is \
recommended for comprehensive reproductive planning.

4. **Population-specific carrier frequencies.** Some conditions are more \
common in certain ancestral populations (e.g., Tay-Sachs in Ashkenazi \
Jewish populations, Sickle Cell in African descent populations). The \
carrier panel used here is not population-specific — consult a genetic \
counselor for ancestry-informed screening recommendations.

5. **Professional genetic counseling is recommended.** If you have a \
carrier finding, a certified genetic counselor can help you understand \
the implications for family planning, discuss partner testing options, \
and explain reproductive alternatives.

**Resources:**
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/
- ACOG Carrier Screening: https://www.acog.org/clinical/clinical-guidance/committee-opinion/articles/2017/03/carrier-screening-for-genetic-conditions
- MedlinePlus — Genetic Testing: https://medlineplus.gov/genetictesting.html\
"""

# ── Per-gene carrier display notes ────────────────────────────────────

CARRIER_GENE_NOTES: dict[str, str] = {
    "CFTR": (
        "Cystic Fibrosis is the most common life-limiting autosomal "
        "recessive condition in people of European descent. Carrier "
        "frequency is approximately 1 in 25 in this population."
    ),
    "HBB": (
        "Variants in HBB can cause Sickle Cell Disease or "
        "Beta-Thalassemia depending on the specific variant. Carrier "
        "frequency is highest in populations from Africa, the "
        "Mediterranean, Middle East, and South Asia."
    ),
    "GBA": (
        "GBA variants are associated with Gaucher Disease. Carrier "
        "frequency is approximately 1 in 15 in Ashkenazi Jewish "
        "populations. GBA carrier status has also been associated with "
        "a modestly increased risk of Parkinson's disease, though this "
        "is a research finding and not a clinical diagnosis."
    ),
    "HEXA": (
        "HEXA variants are associated with Tay-Sachs Disease. Carrier "
        "frequency is approximately 1 in 30 in Ashkenazi Jewish "
        "populations and 1 in 300 in the general population."
    ),
    "BRCA1": (
        "BRCA1 is a dual-role gene: a pathogenic variant confers both "
        "personal cancer predisposition (autosomal dominant) and "
        "reproductive carrier risk. See the Cancer module for disease "
        "risk framing."
    ),
    "BRCA2": (
        "BRCA2 is a dual-role gene: a pathogenic variant confers both "
        "personal cancer predisposition (autosomal dominant) and "
        "reproductive carrier risk. See the Cancer module for disease "
        "risk framing."
    ),
    "SMN1": (
        "SMN1 variants are associated with Spinal Muscular Atrophy. "
        "Consumer genotyping chips cannot reliably detect SMN1 copy "
        "number variations, which are the most common cause of SMA. "
        "Results for this gene should be interpreted with extra caution."
    ),
}

# ── Cancer module disclaimer ─────────────────────────────────────────

CANCER_DISCLAIMER_TITLE = "About Cancer Predisposition Results"

CANCER_DISCLAIMER_TEXT = """\
This section reports genetic variants associated with hereditary cancer \
predisposition syndromes. These results are based on a curated panel of \
28 genes with established links to cancer risk.

**Please understand the following before reviewing:**

1. **Predisposition is not diagnosis.** Carrying a pathogenic variant in \
a cancer predisposition gene means you may have an increased lifetime \
risk for certain cancers. It does NOT mean you have cancer or will \
develop cancer. Many carriers never develop the associated condition.

2. **Absence of findings does not mean absence of risk.** Consumer \
genotyping chips test only a small fraction of known variants in these \
genes. A negative result here does NOT rule out hereditary cancer risk. \
Clinical-grade genetic testing (full gene sequencing and deletion/ \
duplication analysis) is required for comprehensive assessment.

3. **Polygenic Risk Scores are research-grade.** The PRS results in this \
module are derived from published GWAS data and are presented for \
educational purposes only. They reflect statistical associations across \
populations and may not accurately predict individual risk. PRS results \
should never be used to make clinical decisions.

4. **Variant classification evolves.** ClinVar classifications can change \
as new evidence emerges. A variant classified as pathogenic today may be \
reclassified, and variants not flagged now may be flagged in the future.

5. **Professional guidance is essential.** If you have a pathogenic or \
likely pathogenic finding in this module, consult a certified genetic \
counselor or medical geneticist for proper risk assessment, management \
recommendations, and discussion of implications for family members.

**Resources:**
- National Cancer Institute Genetics: https://www.cancer.gov/about-cancer/causes-prevention/genetics
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/
- FORCE (Facing Our Risk of Cancer Empowered): https://www.facingourrisk.org/\
"""

# ── Cardiovascular module disclaimer ───────────────────────────────

CARDIOVASCULAR_DISCLAIMER_TITLE = "About Cardiovascular Genetic Results"

CARDIOVASCULAR_DISCLAIMER_TEXT = """\
This section reports genetic variants associated with cardiovascular \
conditions including familial hypercholesterolemia (FH), \
cardiomyopathies, channelopathies, and lipid metabolism disorders. \
Results are based on a curated panel of 16 genes with established \
links to cardiovascular risk.

**Please understand the following before reviewing:**

1. **Genetic predisposition is not diagnosis.** Carrying a pathogenic \
variant in a cardiovascular gene means you may have an increased risk \
for the associated condition. It does NOT mean you currently have or \
will develop heart disease.

2. **Familial Hypercholesterolemia (FH).** FH is one of the most \
common genetic conditions affecting cholesterol metabolism. Early \
identification and treatment with lipid-lowering therapy can \
significantly reduce cardiovascular risk. If an FH-associated variant \
is identified, discuss cholesterol management with your physician.

3. **Absence of findings does not mean absence of risk.** Consumer \
genotyping chips test only a subset of known variants. A negative \
result does NOT rule out hereditary cardiovascular conditions. \
Clinical-grade genetic testing is required for comprehensive assessment.

4. **Variant classification evolves.** ClinVar classifications can \
change as new evidence emerges. A variant classified as pathogenic \
today may be reclassified in the future.

5. **Professional guidance is essential.** If you have a pathogenic or \
likely pathogenic finding, consult a cardiologist, certified genetic \
counselor, or medical geneticist for proper risk assessment and \
management recommendations.

**Resources:**
- Family Heart Foundation: https://familyheart.org/
- American Heart Association: https://www.heart.org/en/health-topics/cholesterol
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/\
"""


# ── Hereditary haemochromatosis (HFE) module ─────────────────────────────────

HEMOCHROMATOSIS_DISCLAIMER_TITLE = "About Hereditary Haemochromatosis Results"

HEMOCHROMATOSIS_DISCLAIMER_TEXT = """\
This section reports the HFE genotype (C282Y / H63D) associated with hereditary \
haemochromatosis — a treatable disorder of iron metabolism.

**Please understand the following before reviewing:**

1. **Genotype is not diagnosis, and penetrance is reduced and sex-dependent.** \
Even C282Y homozygotes — the highest-risk genotype — do not all develop disease. \
Cumulative haemochromatosis diagnosis by age 80 was ~56% in men and ~41% in \
women, and most homozygotes are undiagnosed. Compound C282Y/H63D and single \
carriers are low-penetrance.

2. **It is treatable.** When iron overload does occur, it is managed effectively \
with phlebotomy. A pathogenic genotype is a reason to discuss iron studies \
(ferritin, transferrin saturation) with a clinician — not a reason to assume disease.

3. **Ancestry context.** Prevalence and penetrance figures come from \
European-ancestry populations; C282Y is near-absent in East Asian and \
sub-Saharan African ancestry.

4. **A negative result does not rule out iron overload.** The array tests only \
C282Y and H63D; rare HFE and non-HFE iron-overload variants are not interrogated.

5. **Professional guidance.** Discuss results with your physician or a genetic \
counselor before any medical action; confirm in a CLIA/accredited laboratory.

**Resources:**
- American Hemochromatosis Society: https://www.americanhs.org/
- Iron Disorders Institute: https://irondisorders.org/
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/\
"""


# ── Inherited thrombophilia (Factor V Leiden / Prothrombin) module ──────────

THROMBOPHILIA_DISCLAIMER_TITLE = "About Inherited Thrombophilia Results"

THROMBOPHILIA_DISCLAIMER_TEXT = """\
This section reports two well-established inherited thrombophilia variants — \
Factor V Leiden (rs6025) and Prothrombin G20210A (rs1799963) — that modestly \
raise the risk of venous blood clots (venous thromboembolism, VTE).

**Please understand the following before reviewing:**

1. **Relative risk is not absolute risk.** These variants raise *relative* risk \
(roughly 3–5× for a single Factor V Leiden allele, 2–3× for Prothrombin, ~5× for \
both together), but the *absolute* lifetime risk for most carriers stays low — \
only about 10% of Factor V Leiden carriers ever have a clot.

2. **Risk concentrates around triggers.** Most clots in carriers occur with \
estrogen-containing contraception or hormone therapy, pregnancy and the weeks \
after delivery, major surgery, or prolonged immobility. These are the situations \
worth discussing with a clinician.

3. **Carriers are not treated for being carriers.** Asymptomatic carriers are \
not put on blood thinners, and routine population screening is not recommended.

4. **A negative result does not rule out a clotting disorder.** Many other \
inherited and acquired factors influence clotting risk and are not tested here.

5. **Family relevance.** A positive result may be relevant for blood relatives, \
particularly before pregnancy or starting hormonal therapy.

Discuss results with your physician or a genetic counselor; confirm in a \
CLIA/accredited laboratory before any medical decision.

**Resources:**
- National Blood Clot Alliance: https://www.stoptheclot.org/
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/\
"""


# ── Alpha-1 antitrypsin deficiency (SERPINA1) module ────────────────────────

ALPHA1_DISCLAIMER_TITLE = "About Alpha-1 Antitrypsin Deficiency Results"

ALPHA1_DISCLAIMER_TEXT = """\
This section reports the SERPINA1 Pi*Z (rs28929474) and Pi*S (rs17580) variants \
that cause alpha-1 antitrypsin deficiency (AATD) — an under-diagnosed, \
*actionable* condition affecting the lungs and liver.

**Please understand the following before reviewing:**

1. **The severe genotype is actionable.** PiZZ (two Z alleles) gives severe \
deficiency and raises the risk of early-onset emphysema/COPD and liver disease. \
The single most important step is avoiding smoking and vaping; augmentation \
therapy and liver monitoring may be options to discuss with a clinician.

2. **Genotype is not diagnosis.** Many carriers — and even some with deficiency \
genotypes — remain healthy, particularly non-smokers. Serum AAT level and Pi \
phenotyping confirm and quantify deficiency.

3. **A negative result does not exclude rare null alleles.** The array tests only \
Z and S. A result other than PiZZ or PiSZ does not exclude AATD — rare null and \
other deficiency alleles (e.g. Pi*null, Pi*Mmalton) are not interrogated.

4. **Family relevance.** Carrier (PiMZ/PiMS) results can be relevant for family \
planning.

5. **Professional guidance.** Discuss results with your physician or a genetic \
counselor; confirm in a CLIA/accredited laboratory before any medical action.

**Resources:**
- Alpha-1 Foundation: https://www.alpha1.org/
- COPD Foundation: https://www.copdfoundation.org/
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/\
"""


# ── Age-related macular degeneration (AMD: CFH / ARMS2) module ──────────────

AMD_DISCLAIMER_TITLE = "About Age-Related Macular Degeneration (AMD) Risk Results"

AMD_DISCLAIMER_TEXT = """\
This section reports two common AMD risk alleles — CFH Y402H (rs1061170) and \
ARMS2/HTRA1 (rs10490924). These are common genetic risk factors, not pathogenic \
mutations, and they describe an *association* with age-related macular \
degeneration — not a diagnosis.

**Please understand the following before reviewing:**

1. **Relative risk is not absolute risk.** The odds ratios shown (up to ~7× for \
CFH and ~5.5× for ARMS2 homozygotes, and an illustrative ~33× for the \
double-homozygous genotype, n=14, with a very wide confidence interval) are \
*relative* odds from case-control studies. Your absolute, lifetime AMD risk \
depends much more on age, smoking, and your overall genetic background.

2. **These two loci are not the whole picture.** A genome-wide study found that \
52 variants across 34 loci explain ~47% (95% CI 44.5–48.8%) of advanced-AMD \
variability in European-ancestry subjects — just under half — and that ~47% is \
attributable to all 52 variants, not to CFH and ARMS2 alone. Most of AMD's \
heritability and most of a person's actual risk lie outside these two SNPs.

3. **Ancestry context.** These odds ratios are derived primarily from \
European-ancestry populations and attenuate in East Asian populations.

4. **A negative result does not rule out AMD risk.** Other AMD variants and \
non-genetic factors (age, smoking, diet) are not captured here.

5. **AMD is modifiable and screenable.** Not smoking, a healthy diet, and \
regular dilated eye exams are the actionable steps; discuss your eye health \
with an optometrist or ophthalmologist.

**Resources:**
- BrightFocus Foundation (Macular Degeneration): https://www.brightfocus.org/macular/
- American Academy of Ophthalmology: https://www.aao.org/eye-health/diseases/amd-macular-degeneration
- National Eye Institute: https://www.nei.nih.gov/learn-about-eye-health/eye-conditions-and-diseases/age-related-macular-degeneration\
"""


# ── APOL1 kidney-risk (G1 / G2 / N264K) module ──────────────────────────────

APOL1_DISCLAIMER_TITLE = "About APOL1 Kidney-Risk Results"

APOL1_DISCLAIMER_TEXT = """\
This section reports the APOL1 G1 (rs73885319 / rs60910145) and G2 (rs71785313) \
kidney-risk variants and the N264K (rs73885316) modifier. These variants arose \
on, and are validated in, recent African-ancestry genetic backgrounds and are \
near-absent in other populations.

**Please understand the following before reviewing:**

1. **Ancestry-specific.** This module is reported as actionable only for \
individuals of inferred African ancestry. The G1/G2 alleles are essentially \
absent elsewhere, and the risk estimates were established in African-ancestry \
cohorts. For other inferred ancestries, no actionable high-risk result is shown.

2. **Recessive — two risk alleles are needed.** Increased kidney-disease risk \
requires two risk alleles (any combination of G1 and G2). Carrying a single \
risk allele does not raise risk.

3. **Relative risk, with incomplete penetrance.** The two-risk-allele genotype \
is associated with higher odds of focal segmental glomerulosclerosis (OR ~10.5) \
and hypertension-attributed end-stage kidney disease (OR ~7.3), but these are \
*relative* odds. Most people with the high-risk genotype never develop kidney \
disease; a 'second hit' (such as an interferon-driven inflammatory state or \
certain infections) is usually involved. This is not a diagnosis.

4. **The N264K modifier matters.** N264K (rs73885316) strongly attenuates \
G2-associated risk and can reclassify a high-risk genotype toward low risk. If \
N264K was not typed on your array, a high-risk estimate may be overstated.

5. **The G2 variant is a 6-bp deletion often missing from arrays.** When G2 \
(rs71785313) is not typed, your genotype is partial and the risk-allele count \
could be higher than observed — never read a partial result as 'low risk'. \
(Some datasets label this deletion rs1317778148.)

6. **Confirm clinically.** Discuss results with a nephrologist or genetic \
counselor; confirm in a CLIA/accredited laboratory before any medical action. \
This is a research/educational result, not a clinical test.

**Resources:**
- National Kidney Foundation: https://www.kidney.org/
- American Society of Nephrology (APOL1): https://www.asn-online.org/
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/\
"""


# ── Gout / serum-urate (ABCG2 Q141K + SLC2A9) module ────────────────────────

GOUT_DISCLAIMER_TITLE = "About Gout & Serum-Urate Risk Results"

GOUT_DISCLAIMER_TEXT = """\
This section reports common urate-transporter risk alleles — ABCG2 Q141K \
(rs2231142) and a SLC2A9 serum-urate variant (rs13129697). These influence how \
much urate the body retains, which relates to gout susceptibility.

**Please understand the following before reviewing:**

1. **This is a risk modifier, not a diagnosis.** A risk genotype here does not \
mean you have or will develop gout. Gout is multifactorial — many genetic, \
clinical, and lifestyle factors contribute, and most people carrying a risk \
allele never develop gout.

2. **Relative risk, with absolute context.** The ABCG2 Q141K allele is \
associated with roughly 2-fold higher gout odds per risk allele (and larger \
effects in East Asian ancestry), but the absolute lifetime risk for most \
carriers remains modest.

3. **Ancestry context.** The reported effect sizes differ by ancestry; the \
estimate shown is selected for your inferred ancestry where available.

4. **No medical or lifestyle prescriptions.** Yeliztli does not recommend \
treatments, foods, supplements, or any lifestyle change. Any decision about \
urate-lowering therapy or other management belongs with a clinician, based on \
your measured urate levels, symptoms, and history — not on a genotype alone.

5. **A negative result does not rule out gout susceptibility.** Many other \
genetic and non-genetic factors are not captured here.

**Resources:**
- American College of Rheumatology (Gout): https://rheumatology.org/patients/gout
- National Kidney Foundation: https://www.kidney.org/
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/\
"""


# ── Within-account KING-robust kinship / relatedness QC ─────────────────────

KINSHIP_DISCLAIMER_TITLE = "About Relatedness (Kinship) Results"

KINSHIP_DISCLAIMER_TEXT = """\
This section estimates how genetically related your local samples are to one \
another, using the KING-robust kinship method. Its main purpose is quality \
control — spotting a duplicate upload or a sample mix-up — and providing \
relatedness context.

**Please understand the following before reviewing:**

1. **Within your own samples only.** This compares only the samples stored in \
this local instance against each other. It never compares against other users \
and never sends your data anywhere.

2. **An estimate, not a legal or clinical test.** Kinship coefficients place a \
pair into broad relationship bands (duplicate/twin, 1st-degree, 2nd-degree, \
3rd-degree, unrelated). They are statistical estimates from array genotypes, not \
a paternity, immigration, or diagnostic test.

3. **Parent-offspring vs full-sibling is provisional.** Within the 1st-degree \
band, the two are separated using the IBS0 proportion (a heuristic); treat that \
specific label as provisional.

4. **Cross-vendor comparisons are weaker.** Samples from different vendors share \
fewer positions and can differ in strand convention, which reduces accuracy. The \
number of shared SNPs used is reported with each estimate so you can judge \
confidence.

5. **A near-duplicate result usually means a duplicate file.** A kinship near 0.5 \
most often means the same person was uploaded twice (or identical twins) — worth \
checking before interpreting any downstream results.

**Resources:**
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/\
"""


# ── Sample QC metrics + reference-bias disclosure ───────────────────────────

QC_DISCLAIMER_TITLE = "About Sample Quality-Control Metrics"

QC_DISCLAIMER_TEXT = """\
This section reports quality-control (QC) metrics for your genotyping data — \
call rate, heterozygosity, Ti/Tv ratio, and a genetic-vs-recorded sex check. \
They describe data quality, not health.

**Please understand the following before reviewing:**

1. **Reference and population bias.** Call rate, heterozygosity, and Ti/Tv all \
depend on which array generated the data and on your genetic ancestry. A value \
that looks 'off' may simply reflect the array design or an ancestry under- \
represented in the array's reference panel, not a problem with your sample.

2. **The sex check is concordance only.** The genetically inferred sex is \
compared to the sex you recorded, and reported as concordant, discordant, or \
indeterminate. This is a data-integrity check (e.g. catching a mislabeled file). \
It is NOT a sex-chromosome aneuploidy test and never changes your recorded sex.

3. **Call-rate pass line.** A call rate at or above ~98% is the usual array \
threshold; a lower value suggests degraded data and that downstream results \
should be read with extra caution.

4. **Outlier detection needs a batch.** A heterozygosity outlier is only \
meaningful relative to a group of comparable samples; with few local samples no \
reliable outlier judgment can be made, and none is asserted.

5. **Not a clinical result.** These are research/educational data-quality \
metrics, not a clinical or diagnostic test.\
"""


# ── Runs-of-Homozygosity (ROH / FROH) autozygosity metric ───────────────────

ROH_DISCLAIMER_TITLE = "About Runs of Homozygosity (FROH)"

ROH_DISCLAIMER_TEXT = """\
This section reports runs of homozygosity (ROH) — long stretches of the genome \
where both inherited copies are identical — and summarises them as FROH, a \
genome-wide estimate of autozygosity.

**Please understand the following before reviewing:**

1. **FROH is an estimate, not a diagnosis.** It is a population-genetics metric \
describing how much of your genome falls in long homozygous runs. It is not a \
medical result and does not, by itself, indicate any health condition.

2. **It is not a statement about your parents.** Long runs of homozygosity arise \
from many benign causes — the history of your ancestral population, genuine \
ancestral isolation, and large stretches of the genome that simply recombine \
rarely. A given FROH value does not establish that your parents are related, and \
Yeliztli does not infer or report any such relationship.

3. **It is an array-based approximation.** The estimate depends on which \
positions the array typed and on the detection parameters used; sequencing-based \
methods can give different values. It is provided for genomic-ancestry context, \
not clinical use.

4. **Confirm clinically if relevant.** If you have a specific clinical question \
about recessive-condition risk, discuss it with a clinician or genetic counselor \
rather than relying on this estimate.

**Resources:**
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/\
"""


# ── LHON (Leber hereditary optic neuropathy) primary-mutation module ────────

LHON_DISCLAIMER_TITLE = "About Leber Hereditary Optic Neuropathy (LHON) Results"

LHON_DISCLAIMER_TEXT = """\
This section reports the three primary mitochondrial LHON mutations — MT-ND4 \
m.11778G>A (rs199476112), MT-ND1 m.3460G>A (rs199476118), and MT-ND6 m.14484T>C \
(rs199476104) — which together account for more than 90% of Leber hereditary \
optic neuropathy.

**Please understand the following before reviewing:**

1. **A positive result is not a diagnosis or a prediction.** Penetrance is \
incomplete and strongly sex-biased: only about half of male carriers and roughly \
one in ten female carriers ever develop vision loss, and onset is often triggered \
by environmental factors such as smoking or heavy alcohol use. Most carriers keep \
normal vision for life.

2. **Maternally inherited.** Mitochondrial DNA passes only from mother to child. \
A variant here is shared with your maternal relatives but is not passed on by \
fathers — relevant information for the wider maternal family.

3. **Heteroplasmy is not measured.** Arrays give a single binary call and cannot \
measure heteroplasmy (the proportion of mitochondrial copies carrying the \
variant), which can influence whether and how severely vision is affected. Only \
quantitative clinical mitochondrial testing can establish it.

4. **Visual prognosis differs by variant.** m.14484T>C carries the best outlook, \
with spontaneous recovery seen in some affected individuals; m.11778G>A and \
m.3460G>A are associated with poorer spontaneous recovery.

5. **A negative result does not rule LHON out.** Rare and private LHON-causing \
variants exist beyond these three, and a position not present on the array is \
shown as indeterminate rather than negative. The absence of a finding is not a \
clean bill of health — clinical mitochondrial testing is the way to confirm.

6. **Confirm clinically.** This is a research/educational result, not a clinical \
test. Anyone with vision symptoms, or considering this information for family \
planning, should seek evaluation by an ophthalmologist or genetic counselor and \
confirmation in a CLIA/accredited laboratory.

**Resources:**
- LHON GeneReviews: https://www.ncbi.nlm.nih.gov/books/NBK1174/
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/\
"""


# ── MT-RNR1 aminoglycoside-ototoxicity (m.1555A>G / m.1494C>T / m.1095T>C) ──

MT_RNR1_DISCLAIMER_TITLE = "About MT-RNR1 Aminoglycoside-Ototoxicity Results"

MT_RNR1_DISCLAIMER_TEXT = """\
This section reports mitochondrial MT-RNR1 (12S rRNA) variants associated with \
aminoglycoside-induced hearing loss — m.1555A>G (rs267606617), m.1494C>T \
(rs267606619), and m.1095T>C (rs267606618). Aminoglycosides are a class of \
antibiotics that includes gentamicin, tobramycin, amikacin, and streptomycin.

**Please understand the following before reviewing:**

1. **Decision-support, not a prescription.** Where m.1555A>G or m.1494C>T is \
detected, the 2021 CPIC guideline recommends avoiding aminoglycoside antibiotics \
unless a severe infection and the lack of a safe, effective alternative outweigh \
the high risk of permanent hearing loss. Yeliztli does not start, stop, or change \
any medication — that decision belongs with your clinician or pharmacist, who can \
weigh it against your specific situation.

2. **Maternally inherited.** Mitochondrial DNA passes only from mother to child. \
A variant here is shared with your maternal relatives but is not passed on by \
fathers — relevant information for the wider maternal family.

3. **Heteroplasmy is not measured.** Arrays give a single mitochondrial call and \
cannot measure heteroplasmy (the proportion of mitochondrial copies carrying the \
variant). Penetrance can depend on that proportion, which only quantitative \
clinical mitochondrial testing can establish.

4. **Evidence differs by variant.** m.1555A>G and m.1494C>T have strong, \
family-study evidence; m.1095T>C is a weaker, preliminary association that needs \
further study.

5. **A negative result does not rule this out.** These positions are frequently \
not present on consumer arrays. A position that was not typed is shown as \
indeterminate, and the absence of a finding does not mean the variant is absent — \
clinical mitochondrial testing is the way to confirm.

6. **Confirm clinically.** This is a research/educational result, not a clinical \
test. Confirm in a CLIA/accredited laboratory before any medical action, and \
carry this information to medical encounters where antibiotics may be prescribed.

**Resources:**
- CPIC Aminoglycosides / MT-RNR1 guideline: https://cpicpgx.org/guidelines/cpic-guideline-for-aminoglycosides-and-mt-rnr1/
- National Society of Genetic Counselors: https://findageneticcounselor.nsgc.org/\
"""


# ── Calibrated in-silico (Pejaver PP3/BP4) evidence-only disclosure ──────────
# Attached to the in-silico tier block (backend.analysis.insilico_tiers) that is
# added to existing cancer/cardiovascular findings. It is an ACMG/AMP *evidence*
# strength tag only — never a clinical classification — and never alters the
# finding's evidence_level or ClinVar significance.

INSILICO_ACMG_EVIDENCE_ONLY = (
    "Draft ACMG/AMP in-silico evidence strength from REVEL (Pejaver 2022) — an "
    "evidence tag only, NOT a clinical classification. It does not change the "
    "variant's ClinVar significance or this finding's evidence level."
)


# ── gnomAD gene-constraint context-only disclosure ───────────────────────────
# Attached to the gene-constraint badge (backend.analysis.gene_constraint) added
# to cancer/cardiovascular findings. The badge is background on how a gene
# tolerates loss-of-function — it never auto-upgrades an ACMG classification.

GENE_CONSTRAINT_CONTEXT_ONLY = (
    "Gene-level loss-of-function constraint from gnomAD v2.1.1 (context only). "
    "A constraint badge does NOT change this finding's classification or evidence "
    "level — it is background on how the gene tolerates loss-of-function variation."
)


# ── Array-genotyping reliability disclosure (SW-A11 / roadmap #14) ────────────
# Attached to the array-confidence reliability badge
# (backend.analysis.array_confidence). Weedon 2021 (BMJ; PMID 33589468) showed
# genotyping-array calls are near-perfect for common variants but increasingly
# unreliable as allele frequency falls — confirmed by sequencing only ~16% of the
# time below 0.001%, and ~4% for very rare ClinVar P/LP variants in BRCA1/BRCA2.
# The badge is a reliability flag only: it NEVER changes a finding's evidence
# level or ClinVar significance, and a low-reliability flag does not make a true
# call false — it means the call should be confirmed in a CLIA/accredited lab.

ARRAY_CONFIDENCE_CONTEXT_ONLY = (
    "Genotyping-array reliability flag (Weedon 2021; reliability flag only). It does "
    "NOT change this finding's classification or evidence level. Array calls are "
    "near-perfect for common variants but increasingly unreliable as a variant gets "
    "rarer — a low-reliability flag means confirm the call in a CLIA/accredited lab "
    "before any medical action, not that the call is wrong."
)


# ── ClinGen gene-disease validity guardrail (SW-A11 / roadmap #14) ────────────
# Attached to the gene-validity guardrail (backend.analysis.gene_validity). ClinGen
# gene-disease validity (Strande 2017, PMID 28552198) measures how strong the
# evidence is that a gene causes a disease *at all* — orthogonal to a variant's
# ACMG pathogenicity. Thaxton 2022 (PMID 34694049) shows this validity should
# temper variant interpretation: a Pathogenic/Likely-pathogenic call in a gene
# with only Limited/Disputed/Refuted disease validity warrants caution (such
# variants show markedly lower observed penetrance). Guardrail only: it NEVER
# changes a finding's classification or evidence level.

GENE_VALIDITY_CONTEXT_ONLY = (
    "ClinGen gene-disease validity (Strande 2017; context only). This reflects how "
    "strong the evidence is that the GENE causes disease at all — not whether this "
    "specific variant is pathogenic — and it does NOT change the finding's "
    "classification or evidence level. A weak gene-disease validity (Limited, "
    "Disputed, or Refuted) means a Pathogenic/Likely-pathogenic call here should be "
    "interpreted cautiously and, if actionable, confirmed and counselled in a "
    "clinical setting; absence of a ClinGen curation is not evidence either way."
)


# ── DRAFT ACMG/AMP classification disclosure (SW-F1 / roadmap #13) ─────────────
# Attached to every draft ACMG/AMP classification (backend.analysis.acmg). This is
# an automated, NON-CLINICAL reimplementation of a subset of the 2015 ACMG/AMP
# criteria (Richards 2015) combined with the Tavtigian (2018/2020) Bayesian point
# system, with PVS1 following the Abou-Tayoun (2018) decision tree. It is a
# computational starting point only: it cannot apply the case-level, segregation,
# de-novo, functional, or phasing criteria that require clinical data, and from
# unphased array genotypes PM3 (in-trans) is never assessable. It NEVER changes a
# finding's stored evidence level or ClinVar significance, and a draft "Pathogenic"
# is not a clinical diagnosis.

WARFARIN_PGX_CONTEXT_ONLY = (
    "Warfarin pharmacogenomics (CPIC 2017; context only). VKORC1 c.-1639G>A and "
    "CYP4F2*3 genotypes modulate warfarin dose requirement together with CYP2C9 — "
    "the VKORC1 A allele increases warfarin sensitivity (lower dose) and CYP4F2*3 "
    "modestly raises the dose requirement. This is interpretive context, NOT a dose: "
    "CPIC dosing requires a validated pharmacogenetic algorithm plus clinical factors "
    "and INR monitoring. It does not change any finding; discuss warfarin dosing only "
    "with your care team."
)


BCHE_PGX_CONTEXT_ONLY = (
    "BCHE (butyrylcholinesterase) deficiency pharmacogenomics (Zhu 2020; context only). "
    "The atypical/dibucaine-resistant (rs1799807) and K/Kalow (rs1803274) variants reduce "
    "BChE activity, which can prolong neuromuscular blockade (apnea) after the muscle "
    "relaxants succinylcholine (suxamethonium) or mivacurium. This is interpretive context, "
    "NOT a diagnosis: BChE deficiency is confirmed by an enzyme-activity (dibucaine-number) "
    "assay, an array types only two of the relevant variants, and it cannot phase compound "
    "genotypes. It changes no finding; discuss anesthesia planning only with your care team."
)


G6PD_PGX_CONTEXT_ONLY = (
    "G6PD deficiency pharmacogenomics (CPIC 2022; context only). G6PD is X-linked: a single "
    "deficiency allele makes a male (XY) deficient, whereas a heterozygous female (XX) has a "
    "WIDE range of activity (random X-inactivation) and may test normal yet still be at risk "
    "— so a heterozygous result is reported as 'variable', not reassurance. In G6PD "
    "deficiency, oxidative drugs (e.g., rasburicase, primaquine, tafenoquine, dapsone, "
    "methylene blue) can trigger acute hemolytic anemia. This is interpretive context, NOT a "
    "diagnosis: an array types only a few of 200+ variants and biological sex is inferred, "
    "not recorded; G6PD status is confirmed by an enzyme-activity assay. It changes no "
    "finding; discuss any oxidative-drug exposure with your care team."
)


PGX_SOURCES_CONTEXT_ONLY = (
    "Cross-source pharmacogenomic evidence (PharmGKB + DPWG + FDA; context only). For each "
    "drug your CPIC report already flags, this shows the corroborating evidence from other "
    "authorities: the PharmGKB Level of Evidence (1A is the strongest, guideline-backed), "
    "whether the Dutch Pharmacogenetics Working Group (DPWG) also publishes a recommendation, "
    "and the FDA pharmacogenomic labeling status. It is interpretive context that does NOT "
    "change any finding or the CPIC recommendation; prescribing decisions belong with your "
    "care team. PharmGKB data is reused under CC-BY-SA 4.0 (see NOTICE)."
)


GTEX_EQTL_CONTEXT_ONLY = (
    "GTEx eQTL regulatory context (GTEx v8; context only). An eQTL means a variant is "
    "statistically ASSOCIATED with a gene's expression level in a tissue — it is NOT proof "
    "that the variant causes the expression change or any disease (the true causal variant is "
    "often a correlated neighbor in linkage disequilibrium). This regulatory context is shown "
    "for typed non-coding variants only; it NEVER changes a finding and is deliberately NOT "
    "counted as ACMG evidence (no PP3/PS3 uplift). Discuss interpretation with your care team."
)


ALPHAMISSENSE_CONTEXT_ONLY = (
    "AlphaMissense missense pathogenicity (Cheng 2023; context only). A structure-based "
    "AI prediction shown as a COMPLEMENT to the REVEL in-silico evidence — because "
    "REVEL and AlphaMissense are correlated meta-predictors, AlphaMissense is NOT counted "
    "as a separate ACMG PP3/BP4 vote (that would double-count). It does NOT change this "
    "finding's classification or evidence level; concordance with REVEL is supportive "
    "context, discordance is a flag to interpret with extra caution."
)


ACMG_DRAFT_CONTEXT_ONLY = (
    "DRAFT automated ACMG/AMP classification (non-clinical; context only). This is a "
    "computational estimate from a subset of ACMG/AMP criteria (Richards 2015) scored "
    "with the Tavtigian point system; it does NOT replace clinical variant curation. "
    "Many criteria (functional studies, segregation, de novo, phase/in-trans) cannot "
    "be assessed from array data, so the draft can differ from an expert/ClinVar "
    "classification. It does NOT change this finding's evidence level or ClinVar "
    "significance — confirm any actionable variant in a CLIA/accredited laboratory."
)


# ── DPYD fluoropyrimidine absent-allele / fatal-toxicity caveat (SW-E5) ───────
# Attached to every DPYD prescribing-alert finding (gene_caveat in detail_json,
# surfaced by backend.api.routes.pharma). DPYD encodes dihydropyrimidine
# dehydrogenase, the rate-limiting enzyme of fluoropyrimidine (5-FU /
# capecitabine) catabolism; DPD deficiency causes severe and sometimes fatal
# toxicity. This panel only types 4 variants, and CPIC is explicit that a
# normal-metabolizer result does NOT exclude DPD deficiency from rare or untested
# variants. Honesty guardrail: this is interpretive context only — it never
# changes the finding's metabolizer status or evidence level.

DPYD_FLUOROPYRIMIDINE_CAVEAT = (
    "DPYD result interpretation (context only). This panel types only 4 DPYD "
    "variants (DPYD*2A, *13, c.2846A>T, and the HapB3 intronic variant). A "
    "normal-metabolizer / negative result does NOT rule out dihydropyrimidine "
    "dehydrogenase (DPD) deficiency — rare or untested DPYD variants, and "
    "non-genetic causes, can still reduce DPD activity. DPD deficiency can cause "
    "severe or fatal fluoropyrimidine (5-fluorouracil / capecitabine) toxicity, so "
    "before fluoropyrimidine chemotherapy an oncologist may consider phenotypic DPD "
    "testing (plasma uracil or dihydrouracil-to-uracil ratio, or DPD enzyme "
    "activity) regardless of this genotype. This does not change the metabolizer "
    "status or evidence level above; confirm any actionable result in a "
    "CLIA/accredited laboratory and discuss dosing only with your care team."
)


# ── CYP2D6 structural-variant / copy-number caveat (SW-E3) ────────────────────
# Attached to every CYP2D6 prescribing-alert finding (gene_caveat in detail_json,
# surfaced by backend.api.routes.pharma). CYP2D6 is the canonical structural-variant
# pharmacogene: whole-gene duplications/multiplications, the CYP2D6*5 whole-gene
# deletion, and CYP2D6-CYP2D7 hybrid / gene-conversion alleles cannot be resolved
# from SNP-array data. The star-allele call assumes exactly two gene copies, so the
# activity score is an ASSAYED point estimate with a directional band: true activity
# may be HIGHER (a functional-allele duplication → Ultrarapid Metabolizer) or LOWER
# (a *5 deletion → Poor Metabolizer). Honesty guardrail: context only — it never
# changes the metabolizer status or evidence level (the call already carries
# "Partial" confidence via STRUCTURAL_VARIANT_GENES).

CYP2D6_CNV_CAVEAT = (
    "CYP2D6 result interpretation (context only). Array genotyping reads only "
    "single-nucleotide variants and assumes two CYP2D6 gene copies; it does NOT "
    "assess copy-number variation (whole-gene duplications/multiplications, the "
    "CYP2D6*5 whole-gene deletion) or CYP2D6-CYP2D7 hybrid / gene-conversion "
    "alleles. The activity score shown is therefore an assayed estimate: true "
    "CYP2D6 activity may be HIGHER if a functional allele is duplicated (toward "
    "Ultrarapid Metabolizer — e.g. increased morphine from codeine) or LOWER if a "
    "CYP2D6*5 gene deletion is present (toward Poor Metabolizer). A normal-"
    "metabolizer result does not exclude these structural variants. This does not "
    "change the metabolizer status or evidence level above; confirm with a "
    "copy-number-aware CYP2D6 assay in a CLIA/accredited laboratory before any "
    "medication change."
)


# ── Medication-safety report reference-bias disclosure (SW-E4) ────────────────
# Surfaced once at the top of the consolidated drug-centric medication-safety
# report (backend.api.routes.pharma -> GET /api/analysis/pharma/report). This is
# the report-level statement of the §12.1 / §12.6 guardrail: array-based star-allele
# calling is REFERENCE-BIASED — it only interrogates the specific variants on the
# array, so any allele that is not assayed defaults to the reference (usually the
# normal-function *1) call. A "Normal Metabolizer" / normal-function / negative
# result therefore never rules out rare, untested, or structural (copy-number /
# gene-conversion) alleles. Phenotype terms follow the CPIC consensus standard
# (Caudle et al., Genet Med 2017; PMID 27441996). Honesty guardrail: this is
# interpretive context for the whole report; it never changes any finding's
# metabolizer status, evidence level, or recommendation.

MEDICATION_SAFETY_REFERENCE_BIAS = (
    "About this medication-safety report (context only). Phenotype terms follow the "
    "CPIC consensus standard (e.g. Normal / Intermediate / Poor / Rapid / Ultrarapid "
    "Metabolizer; Normal / Decreased / Poor Function). These calls come from an array "
    "that types only specific pre-selected variants, so the result is REFERENCE-BIASED: "
    "any star allele that is not directly assayed defaults to the reference (normal-"
    "function) call. A Normal Metabolizer, normal-function, or otherwise reassuring "
    "result does NOT rule out rare or untested variants, copy-number changes "
    "(duplications/deletions), or gene-conversion alleles that this array cannot see, "
    "and several genes (e.g. CYP2D6, DPYD) carry additional gene-specific caveats. The "
    "per-gene coverage below counts how many of each gene's known SNP positions were "
    "assayed; it does NOT measure copy-number or structural completeness, which array "
    "data cannot assess — so a gene can show full SNP coverage and still hide a "
    "duplication or deletion. Call-confidence flags genes whose result is structurally "
    "uncertain. This report is research/educational decision support only — it is not a "
    "prescribing instruction. Confirm any actionable result in a CLIA/accredited "
    "laboratory and discuss medication changes only with your care team."
)
