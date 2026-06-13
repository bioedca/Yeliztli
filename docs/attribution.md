# Data sources & attribution

Yeliztli's own source code is released under the **MIT license**. To annotate your genome it
draws on several public scientific datasets, each retained under **its own license**. This
page summarises them; the repository
[`NOTICE`](https://github.com/bioedca/Yeliztli/blob/main/NOTICE) file holds the full,
authoritative attribution text, and [`LICENSE`](https://github.com/bioedca/Yeliztli/blob/main/LICENSE)
covers Yeliztli's code.

For sizes and how each is obtained, see [reference data](install/reference-data.md).

## Bundled / redistributed data

These are packaged with Yeliztli (or its release bundles), so their licenses permit
redistribution.

| Source | Used for | License | Key citation |
|--------|----------|---------|--------------|
| **gnomAD** v2.1.1 (GRCh37) | Population allele frequencies (and homozygous counts) only | **CC0 1.0** | Karczewski et al., *Nature* 581:434 (2020), doi:10.1038/s41586-020-2308-7 |
| **ClinGen** gene–disease validity | Context-only gene-validity guardrail | **CC0 1.0** (attribution requested) | Strande et al., *AJHG* 100:895 (2017), doi:10.1016/j.ajhg.2017.04.015 |
| **AlphaMissense** (hg19) | Missense-pathogenicity context (complements REVEL) | **CC-BY-4.0** (per Zenodo record 10813168) | Cheng et al., *Science* 381:eadg7492 (2023), doi:10.1126/science.adg7492 |
| **PharmGKB** (LoE + DPWG) | Cross-source pharmacogenomic evidence, context-only | **CC-BY-SA-4.0** (share-alike honored) | Whirl-Carrillo et al., *CPT* 110:563 (2021), doi:10.1002/cpt.2350 |
| **FDA PGx labeling** (via PharmGKB) | Drug-label testing-level classification, context-only | **CC-BY-SA-4.0** (PharmGKB derivative; FDA text is public-domain) | — |
| **GTEx** v8 | Tissue eQTL regulatory context (summary stats only) | **Open-access summary statistics** | GTEx Consortium, *Science* 369:1318 (2020), doi:10.1126/science.aaz1776 |
| **PGS Catalog** (3 bundled scores) | Polygenic-score weights — research-use only | **CC-BY-4.0** (per score) | Lambert et al., *Nat. Genet.* 56:1989 (2024), doi:10.1038/s41588-024-01937-x |

!!! note "Only redistributable scores are bundled"
    Of the PGS Catalog, only the clearly redistributable CC-BY-4.0 scores are bundled —
    **PGS000713** (type-2 diabetes), **PGS005198** (BMI), and **PGS000688** (LDL cholesterol).
    Non-commercial or author-restricted scores (such as the eBMD score, PGS000657) are
    **not** bundled and must be fetched by the user. Yeliztli deliberately excludes
    academic-license-restricted predictor columns (SpliceAI, CADD, REVEL, SIFT, PolyPhen)
    from the gnomAD bundle.

## Downloaded from the provider

These are fetched from their original sources during setup/updates and used locally; they
are not redistributed by Yeliztli.

| Source | Used for | License |
|--------|----------|---------|
| **ClinVar** (NCBI) | Clinical variant classifications | Public domain |
| **dbNSFP** | In-silico predictions (REVEL, CADD, …) | **Academic / non-commercial** |
| **CPIC** | Pharmacogenomic allele & guideline data | CC0-1.0 |
| **PharmVar** | Star-allele definitions | Open |
| **GWAS Catalog** (EBI) | Trait/disease associations | Open |
| **dbSNP** (NCBI) | rsID identity resolution | Public domain |
| **Mondo / HPO** (Monarch) | Disease & phenotype associations | Open |
| **Ensembl VEP** | Variant consequence predictions (the VEP bundle) | Open (Ensembl) |

!!! warning "Mind the licenses for your use"
    Some sources — notably **dbNSFP** (academic/non-commercial) and the **share-alike**
    PharmGKB/FDA-derived data — carry conditions. If you build on Yeliztli or its data,
    make sure your use complies with each source's terms.

## Independence

"gnomAD", "Broad Institute", "ClinGen", "AlphaMissense", "Google DeepMind", "PharmGKB",
"DPWG", "FDA", "GTEx", "PGS Catalog", and other names appear here **solely for source
attribution**. Yeliztli is an **independent project** and is not affiliated with, sponsored
by, or endorsed by any of these organisations.
