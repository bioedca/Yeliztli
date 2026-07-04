# Reference data

Yeliztli annotates your variants against public scientific datasets. They fall into two
groups: **prebuilt bundles** that Yeliztli publishes, and **pipeline sources** fetched from
their original providers. Everything is downloaded during the
[setup wizard](setup-wizard.md) (or later, on demand) and stored under your data directory
(default `~/.yeliztli/`).

All downloads are **resumable** and integrity-checked. You can inspect, resume, verify, or
clean any of them under **Settings → System Health → Database Health**.

## First-run duration

Full reference-data setup is a long one-time operation. It uses more than 60 GB at peak, with
~80 GB recommended for headroom, and commonly takes on the order of an hour or more. Slow
networks, slower disks, or optional databases can make it considerably longer.

The **dbNSFP** step dominates the runtime. Its source archive is a large network download,
and then Yeliztli parses, builds, and indexes a multi-GB SQLite database. Download progress is
shown as bytes transferred; the later build/index phase is CPU- and disk-bound and may not
show the same moving progress bar. If **Settings → System Health → Database Health** shows
the database as `Downloading` or `Building`, setup is still active. If it shows `Failed`, use
the repair/resume controls from that page.

## Prebuilt bundles

These are published as GitHub release assets, pinned by version and SHA-256 checksum in
[`bundles/manifest.json`](https://github.com/bioedca/Yeliztli/blob/main/bundles/manifest.json).

| Bundle | Approx. size | What it provides |
|--------|--------------|------------------|
| **gnomAD allele frequencies** | ~1.30 GB download / ~2.85 GB installed | Population allele frequencies, observed allele counts, and homozygous counts — CC0 / public domain. |
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
| **UCSC hg19 FASTA + RefSeq (`refGene`)** | Optional fully local Genome Browser reference and gene track | ~4 GB when installed | UCSC Genome Browser data terms |

!!! warning "dbNSFP license"
    dbNSFP is distributed under an **academic / non-commercial** license. Make sure your use
    complies with its terms. Its setup footprint is also large: the source archive is removed
    after a successful build, but an interrupted build may keep the completed archive so setup
    can resume without starting another large download. The build and index step can take a
    long time after the archive has already downloaded.

## HIBAG HLA model files

The **[HLA (imputed)](../modules/hla.md)** feature uses HIBAG pre-fit model files, but those
files are **not** Yeliztli release bundles or setup-wizard downloads. They are bring-your-own
external inputs because HIBAG itself is an operator-installed R/Bioconductor runtime and the
model files carry their own distribution terms.

If you enable HLA imputation, fetch the model files yourself, place them in the directory
configured by `YELIZTLI_HIBAG_MODEL_DIR`, and keep their expected names:
`European-HLA4.RData`, `Asian-HLA4.RData`, `Hispanic-HLA4.RData`, or `African-HLA4.RData`.
Then run `scripts/predict_hla.py` for each sample to populate `hla_calls`.

## Genome Browser local reference files

The Genome Browser can run without contacting IGV.js reference hosts when these local files
are installed:

- `grch37.fa`
- `grch37.fa.fai`
- `grch37_refseq.bed`
- `genome_browser_reference_manifest.json`

By default Yeliztli looks for them in the data directory. You can point to local runtime files
with `YELIZTLI_GRCH37_FASTA_PATH` and
`YELIZTLI_GENOME_BROWSER_REFSEQ_TRACK_PATH`, but the files are accepted only when the manifest
describes the expected UCSC hg19 FASTA / `refGene` build and the FASTA index matches GRCh37/hg19
sentinel chromosome lengths. If any file is missing or validation fails, the Genome Browser keeps
the disclosure-gated hosted `hg19` fallback.

Maintainers can build the local reference files from UCSC sources with
[`scripts/build_genome_browser_reference.py`](https://github.com/bioedca/Yeliztli/blob/main/scripts/build_genome_browser_reference.py).
The SLURM procedure and provenance checklist are in the
[Genome Browser reference bundle runbook](../maintainer/genome-browser-reference-bundle.md).

## Updating reference data

Reference data can be refreshed any time from **Settings → Database Management**, with
per-database auto-update toggles and an optional bandwidth window for large downloads. See
[updating](updating.md).
