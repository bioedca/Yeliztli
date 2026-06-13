# System requirements

| Requirement | Minimum | Notes |
|-------------|---------|-------|
| **Python** | 3.12+ | Yeliztli uses 3.12-only standard-library features. |
| **Node.js** | 20+ | Needed to build (or hot-reload) the frontend. |
| **Operating system** | macOS (Apple Silicon or Intel), Linux, or Windows via **WSL2** | Native Windows (outside WSL2) is not supported — see [WSL2](wsl2.md). |
| **RAM** | ~1 GB free | More is helpful while annotating a large file. |
| **Free disk space** | ~5 GB to start; **~10 GB recommended** | See the breakdown below. |
| **Java** | 8+ *(optional)* | Only required for chromosome-level ancestry painting (the Tier-2 LAI bundle). |

## Disk space, realistically

The application itself is small, but the **reference databases** it downloads during setup
are not. The setup wizard **warns** when less than ~10 GB is free and **blocks** setup below
~5 GB, which is a good guide to plan around:

| Component | Approx. size |
|-----------|--------------|
| Core reference bundles (gnomAD allele frequencies, VEP consequences, PGS scores) | ~2.5 GB |
| Additional annotation sources (ClinVar, dbNSFP, AlphaMissense, GWAS Catalog, …) | ~1–1.5 GB |
| Optional ancestry **LAI bundle** (Tier-2 chromosome painting) | ~1.7 GB |
| Your samples | varies (typically tens to a few hundred MB each) |

See **[reference data](reference-data.md)** for the full list of bundles and sources, with
licenses.

## Network

Yeliztli needs internet access **during setup and updates** to download public reference
data. After that it runs fully on `localhost` — your genotype data is never uploaded (see
[Privacy](../privacy.md)).
