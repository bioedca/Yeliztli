# Reference data

Yeliztli annotates your variants against public scientific datasets. They fall into two
groups: **prebuilt bundles** that Yeliztli publishes, and **pipeline sources** fetched from
their original providers. Everything is downloaded during the
[setup wizard](setup-wizard.md) (or later, on demand) and stored under your data directory
(default `~/.yeliztli/`).

All downloads are **resumable** and integrity-checked. You can inspect, resume, verify, or
clean any of them under **Settings → System Health → Database Health**.

## Prebuilt bundles

These are published as GitHub release assets, pinned by version and SHA-256 checksum in
[`bundles/manifest.json`](https://github.com/bioedca/Yeliztli/blob/main/bundles/manifest.json).

| Bundle | Approx. size | What it provides |
|--------|--------------|------------------|
| **gnomAD allele frequencies** | ~1.95 GB | Population allele frequencies (and homozygous counts) — CC0 / public domain. |
| **VEP consequence bundle** | ~360 MB | Pre-computed variant consequences, HGVS, and transcript context for the genotyped sites. |
| **PGS scores** | ~104 MB | Polygenic-score weight sets used by the risk modules. |
| **Ancestry PCA bundle** | ~0.4 MB | Ancestry-informative markers and PCA loadings — ships **inside the app**, no download. |
| **Ancestry LAI bundle** *(optional)* | ~1.7 GB | Local-ancestry-inference models + phasing reference for Tier-2 chromosome painting. Requires **Java 8+**; only download it if you want chromosome-level ancestry. |

## Pipeline sources

These are downloaded from the original providers. Each retains its own license — the full
attribution list lives in the repository
[`NOTICE`](https://github.com/bioedca/Yeliztli/blob/main/NOTICE) file.

| Source | Purpose | Approx. setup footprint | License |
|--------|---------|-------------------------|---------|
| **ClinVar** (NCBI) | Clinical variant classifications | ~250 MB | Public domain |
| **dbNSFP** | In-silico pathogenicity predictions (REVEL, CADD, ...) | ~50 GB transient ZIP + ~10+ GB built DB | Academic / non-commercial |
| **CPIC** | Pharmacogenomics allele & guideline data | ~5 MB | CC0-1.0 |
| **ClinGen** | Gene-disease validity & dosage | ~1 MB | CC0-1.0 |
| **PharmVar** | Pharmacogene star-allele definitions | Small metadata source | Open |
| **AlphaMissense** | Missense pathogenicity predictions | ~3.5 GB when installed | CC-BY-4.0 |
| **GWAS Catalog** (EBI) | Trait/disease associations for risk modules | ~100 MB | Open |
| **dbSNP** (NCBI) | rsID merge/identity resolution | ~20 MB | Public domain |
| **Mondo / HPO** (Monarch) | Disease & phenotype associations | ~15 MB | Open |
| **PharmGKB** | Clinical drug annotations | Small metadata source | CC-BY-SA-4.0 |
| **FDA drug labels** (via PharmGKB) | Pharmacogenomic labeling | Small metadata source | CC-BY-SA-4.0 |
| **GTEx** | Tissue eQTLs for functional context | ~3 GB when installed | Open-access summary stats |

!!! warning "dbNSFP license"
    dbNSFP is distributed under an **academic / non-commercial** license. Make sure your use
    complies with its terms. Its setup footprint is also large: the source archive is removed
    after a successful build, but an interrupted build may keep the completed archive so setup
    can resume without starting another large download.

## Updating reference data

Reference data can be refreshed any time from **Settings → Database Management**, with
per-database auto-update toggles and an optional bandwidth window for large downloads. See
[updating](updating.md).
