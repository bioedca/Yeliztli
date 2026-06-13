# Ancestry methods & validation

This page explains **how** Yeliztli infers ancestry, and — just as importantly — the
**limitations** of those methods. It's the deeper companion to the user-facing
[Ancestry module](modules/ancestry/ancestry.md).

!!! note "Ancestry is an estimate, not an identity"
    Everything here produces **statistical estimates** with genuine uncertainty. Genetic
    ancestry is a continuous, model- and reference-dependent quantity — not a fixed label,
    and not a statement about culture, ethnicity, or identity.

Yeliztli uses a **two-tier** system:

- **Tier 1 (always available, instant):** genome-wide *global* ancestry via PCA, admixture
  proportions, and haplogroups.
- **Tier 2 (optional, slower):** *local* ancestry inference — "painting" each chromosome by
  ancestral origin.

---

## Tier 1 — Global ancestry by PCA

Principal Component Analysis (PCA) is the standard way to summarise genome-wide genetic
structure: a handful of principal components capture the major axes of variation that
separate populations [1]. Yeliztli does **not** re-run PCA on your data; instead it
**projects** you onto a pre-computed reference space.

- **Reference panel & markers.** A panel of **3,419 reference individuals** spanning **7
  super-populations** — African (AFR), American (AMR), Central/South Asian (CSA), East Asian
  (EAS), European (EUR), Middle Eastern (MID), and Oceanian (OCE) — with **8 principal
  components** (their significance assessed by Tracy–Widom statistics).
- **Ancestry-informative markers (AIMs).** Projection uses **5,000 AIMs** — markers chosen
  because their allele frequencies differ most between populations. Carefully selected AIMs
  can assign continental ancestry with high accuracy using only a small fraction of the
  genome [2].
- **Projection onto reference PCs.** Reusing pre-computed PC loadings from a reference panel
  (rather than re-deriving them) is an established, accurate approach to ancestry
  inference [3].

!!! warning "Projection shrinkage on sparse data"
    Markers your file doesn't cover are mean-imputed before projection. Projected PCs are
    known to suffer a **shrinkage bias toward the origin**, which grows as coverage drops [4].
    On sparse genotypes this can pull you toward the centre of the PCA plot and soften the
    signal — which is one reason Yeliztli reports a coverage fraction and can return
    *Uncertain*.

### Admixture proportions

PCA places you in a space; admixture estimation turns that position into **fractions**:

- **Primary estimate — NNLS.** Your coordinates are decomposed into per-population
  proportions by **non-negative least squares** (proportions can't be negative and sum
  sensibly). Combining PCA with NNLS is a fast, validated way to estimate ancestry
  proportions, even with substantial missing data [5]. A **95% confidence interval** is
  produced by **bootstrapping** (100 iterations, resampling AIMs).
- **Secondary estimate — kNN.** A *k*-nearest-neighbours estimate (k = 15) is computed
  independently; the **cosine similarity** between the NNLS and kNN vectors becomes a
  confidence signal. When the two disagree, confidence is lowered.

### How a call is decided

- A **confident single-population** call requires **AIM coverage above ~55%**; below that,
  Yeliztli returns **Uncertain** rather than guess.
- If your second-nearest population centroid is not much farther than the nearest (within
  ~3×), you're reported as **Admixed** rather than forced into one label.
- Quality flags (low coverage, narrow centroid margin) travel with the result.

### Haplogroups

Yeliztli also assigns deep-lineage haplogroups from lineage-defining variants:

- **Mitochondrial (maternal)** haplogroups via the **PhyloTree** reference phylogeny, the
  widely accepted reference tree for human mtDNA variation [6].
- **Y-chromosome (paternal)** haplogroups via the ISOGG tree — only for samples inferred as
  XY (skipped otherwise).

---

## Tier 2 — Local ancestry inference (optional)

Where Tier 1 gives one genome-wide summary, **local ancestry inference (LAI)** estimates the
ancestral origin of each *segment* of each chromosome — "chromosome painting." This is the
optional **LAI bundle** (large download, **Java 8+**, ~15–30 minutes per sample; see
[reference data](install/reference-data.md)).

The pipeline first **statistically phases** your genotypes against a reference panel (Beagle),
then assigns ancestry to short windows using machine-learning models, and smooths the result.
This is the same family of methods as **RFMix**, a discriminative (random-forest) approach
that is fast and robust for continental-scale local ancestry [7], and as the large-panel
deconvolution pipelines used by consumer-genomics services [10].

!!! warning "LAI accuracy depends on phasing and reference panels"
    Two dependencies materially affect LAI quality:

    - **Phase quality.** Phase-based LAI degrades when statistical phasing is imperfect;
      switch errors directly reduce accuracy [9].
    - **Reference representation.** Accuracy is **ancestry-specific** and depends on having a
      well-matched reference panel. In benchmarks, true-positive rates were ~96–99% for
      European and African tracts but notably lower (~88–94%) for Indigenous-American tracts,
      and mis-calls tended to default toward European ancestry [8].

    Treat painted segments — especially for under-represented ancestries — as estimates, not
    ground truth.

---

## Limitations & honest caveats

- **Reference panels bound what can be seen.** Populations that are sparsely represented in
  the reference panel (e.g. MID, OCE, many Indigenous groups) are inferred less reliably [8].
- **PCA itself has pitfalls.** PCA results can be sensitive to data processing and ascertainment
  and can be over-interpreted; they should be read as a summary of structure, not a literal
  map of populations [11].
- **Array coverage matters.** Sparse or low-coverage files shrink toward the centre of the PCA
  space [4]; Yeliztli surfaces coverage and downgrades confidence accordingly.
- **It is not an identity test.** Admixture percentages and haplogroups describe genetic
  similarity to reference groups; they don't define who you are.

---

## References

1. [Principal components analysis corrects for stratification in genome-wide association studies](https://consensus.app/papers/details/e73e457db18c5facb014406dadea0f04/) (Price et al., 2006, *Nature Genetics*).
2. [Ancestry informative markers for fine-scale individual assignment to worldwide populations](https://consensus.app/papers/details/86645c097b7e51c58ab053b25269c773/) (Paschou et al., 2010, *J. Med. Genet.*).
3. [Improved ancestry inference using weights from external reference panels](https://consensus.app/papers/details/906d9692d58b593791a9c40b76083481/) (Chen et al., 2013, *Bioinformatics*).
4. [Efficient toolkit implementing best practices for principal component analysis of population genetic data](https://consensus.app/papers/details/907fdc5d236655fbae4b79255fb2ba45/) (Privé et al., 2019, *Bioinformatics*).
5. [PANE: fast and reliable ancestral reconstruction on ancient genotype data with non-negative least square and principal component analysis](https://consensus.app/papers/details/01d37fd341cc5d80b184b24e7b7fbd1b/) (de Gennaro et al., 2025, *Genome Biology*).
6. [Updated comprehensive phylogenetic tree of global human mitochondrial DNA variation (PhyloTree)](https://consensus.app/papers/details/acdbf1a62608546cb0a06fc9a83e04fb/) (van Oven & Kayser, 2009, *Human Mutation*).
7. [RFMix: a discriminative modeling approach for rapid and robust local-ancestry inference](https://consensus.app/papers/details/3236e2040d0d5426ba0b74797c99fa29/) (Maples et al., 2013, *Am. J. Hum. Genet.*).
8. [Characterizing features affecting local ancestry inference performance in admixed populations](https://consensus.app/papers/details/40cc769810275c9d934e880f588cd5a5/) (Honorato-Mauer et al., 2024, *Am. J. Hum. Genet.*).
9. [Phase-free local ancestry inference mitigates the impact of switch errors on phase-based methods](https://consensus.app/papers/details/6803c35649a059cc9c3f8e6a5462e7ff/) (Avadhanam et al., 2025, *G3*).
10. [A scalable pipeline for local ancestry inference using tens of thousands of reference haplotypes](https://consensus.app/papers/details/9b855da12e4f5d3394b856732046d0f8/) (Durand et al., 2021).
11. [Principal Component Analyses (PCA)-based findings in population genetic studies are highly biased and must be reevaluated](https://consensus.app/papers/details/e3fe973e0b2a50619b907cf7304f7e7a/) (Elhaik, 2022, *Scientific Reports*).
