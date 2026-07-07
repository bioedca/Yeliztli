# System requirements

| Requirement | Minimum | Notes |
|-------------|---------|-------|
| **Python** | 3.12+ | Yeliztli uses 3.12-only standard-library features. |
| **Node.js** | 20+ | Needed to build (or hot-reload) the frontend. |
| **Operating system** | macOS (Apple Silicon or Intel), Linux, or Windows via **WSL2** | Native Windows (outside WSL2) is not supported — see [WSL2](wsl2.md). |
| **RAM** | ~1 GB free | More is helpful while annotating a large file. |
| **Free disk space** | ~60 GB for full reference setup; **~80 GB recommended** | See the peak-vs-steady-state breakdown below. |
| **Java** | 8+ *(optional)* | Only required for chromosome-level ancestry painting (the Tier-2 LAI bundle). |
| **Chromium browser for reports** | Installed by `yeliztli-setup install` or the Docker image | Required for PDF reports and backend variant-card rendering endpoints. Manual installs can run `python -m playwright install chromium`. |

## Disk space, realistically

The application itself is small, but the **reference databases** it downloads during setup
are not. The setup wizard **warns** when less than ~80 GB is free and **blocks** setup below
~60 GB, because peak setup usage is higher than the final steady-state footprint:

| Component | Approx. setup footprint |
|-----------|-------------------------|
| **dbNSFP** source archive while building | ~50 GB transient ZIP |
| **dbNSFP** built SQLite database | ~10+ GB steady-state |
| gnomAD allele frequencies | ~1.30 GB download / ~2.85 GB installed |
| VEP consequences + PGS scores | ~700 MB |
| ClinVar, CPIC, GWAS Catalog, dbSNP, MONDO/HPO, ENCODE cCREs | ~420 MB |
| Optional ancestry **LAI bundle** (Tier-2 chromosome painting) | ~1.7 GB |
| Optional AlphaMissense or GTEx context databases | ~3-3.5 GB each when installed |
| Playwright Chromium browser for report/card rendering | ~200-400 MB |
| Your samples | varies (typically tens to a few hundred MB each) |

After a successful dbNSFP build, Yeliztli removes the transient source ZIP. If a build is
interrupted after the download completes, the archive may remain so the next run can resume
without downloading another ~50 GB.

Advanced installs can set `YELIZTLI_DOWNLOAD_STAGING_DIR` to put transient source archives
on a different filesystem. In that layout, the setup check evaluates persistent database
space in `data_dir` separately from dbNSFP staging space.

## First-run setup time

Plan for full reference setup to take on the order of an hour or more. The dbNSFP source
archive and build/index step dominate that time, and slower networks or disks can run longer.
Downloads are resumable, and an in-progress `Downloading` or `Building` state under
**Settings → System Health → Database Health** means setup is still active.

See **[reference data](reference-data.md)** for the full list of bundles and sources, with
licenses.

## Network

Yeliztli needs internet access **during setup and updates** to download public reference
data. After that it runs fully on `localhost` — your genotype data is never uploaded (see
[Privacy](../privacy.md)).
