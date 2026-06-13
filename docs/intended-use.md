# Intended use & disclaimers

!!! danger "Yeliztli is not a medical device and does not provide medical advice"
    Yeliztli is a **research and educational tool**. It is **not** a diagnostic test, **not**
    a medical device, and is **not** intended to diagnose, treat, cure, or prevent any
    disease. Nothing it produces is clinically validated. **Do not make medical, lifestyle,
    or reproductive decisions based on its output.** If a result concerns you, take it to a
    qualified clinician or a certified genetic counsellor and confirm it with an accredited
    clinical laboratory.

## What Yeliztli is for

Yeliztli helps a curious, technically comfortable person explore the **raw genotyping data**
they already obtained from a consumer service such as 23andMe or AncestryDNA. It annotates
that data against public scientific databases and presents it through analysis modules so
you can learn about your own genome, understand how variant interpretation works, and keep
the analysis entirely on your own hardware.

It is **not** a substitute for clinical genetic testing, professional genetic counselling,
or medical care.

## Why consumer array data must not drive medical decisions

Yeliztli's input is data from a **SNP genotyping array** (a "chip"), not a clinical-grade
DNA sequence. This matters, and it is well documented in the peer-reviewed literature:

- When variants flagged in consumer **raw data** were sent to an accredited clinical lab for
  confirmation, **about 40% were false positives** — i.e. the variant reported in the raw
  data was not actually present [1]. Some variants labelled "increased risk" were in fact
  common, benign population variants [1].
- Genotyping arrays are **accurate for common variants but unreliable for the rare,
  clinically important variants** that matter most for hereditary disease. In a study of
  ~50,000 UK Biobank participants, the positive predictive value of the array for rare
  pathogenic *BRCA1*/*BRCA2* variants was only **~4%**, and for very rare variants only
  ~16% of positive calls were confirmed by sequencing [2]. The authors conclude such calls
  "should not be used to guide health decisions without validation" [2].
- A systematic review of direct-to-consumer genetic health information likewise found that
  raw-data interpretation can surface false-positive or misinterpreted rare variants and
  presents real challenges for consumers and health services [3].

This is **not** a flaw specific to any one company or to Yeliztli — it is an inherent
property of array genotyping applied to rare variants. Yeliztli surfaces these variants for
exploration and clearly rates the strength of evidence behind each finding, but a positive
result is a prompt to seek clinical confirmation, **never** a diagnosis.

## How Yeliztli tries to keep you safe

- **Evidence ratings.** Findings carry an evidence rating so speculative associations are
  visibly distinguished from well-established ones. (How to read these is covered in the
  module documentation.)
- **Disclosure gates.** The most sensitive modules — for example *APOE* (Alzheimer's-risk
  related), Parkinson's-risk variants, and sex-chromosome aneuploidy — are **opt-in**: their
  results stay hidden until you explicitly acknowledge what you are about to see.
- **Honest gaps.** Where Yeliztli cannot produce a defensible result (for example an
  uncalibrated polygenic percentile), it withholds the number rather than show a misleading
  one.

## Your responsibilities

- Treat every finding as **provisional** until confirmed by a clinical laboratory.
- Discuss anything health-related with a qualified professional before acting.
- Remember that genetic information can affect blood relatives; share results thoughtfully.
- Understand that ancestry, trait, and wellness results are **estimates** with real
  uncertainty, not statements of fact.

## No warranty

Yeliztli is provided under the MIT license, **"as is", without warranty of any kind**. The
authors accept no liability for decisions made on the basis of its output. See the
[LICENSE](https://github.com/bioedca/Yeliztli/blob/main/LICENSE) for the full terms.

---

## References

1. [False-positive results released by direct-to-consumer genetic tests highlight the importance of clinical confirmation testing for appropriate patient care](https://consensus.app/papers/details/b8e5bd6dcb245ff99c33b4aa9cd74e40/) (Tandy-Connor et al., 2018, *Genetics in Medicine*).
2. [Use of SNP chips to detect rare pathogenic variants: retrospective, population based diagnostic evaluation](https://consensus.app/papers/details/45d2e8ad458e5ad3ab524d8e50cdc084/) (Weedon et al., 2021, *The BMJ*).
3. [Direct-to-consumer genetic tests providing health risk information: A systematic review of consequences for consumers and health services](https://consensus.app/papers/details/43b67c16cbf150dd8a595d1283899ffc/) (Nolan et al., 2023, *Clinical Genetics*).
