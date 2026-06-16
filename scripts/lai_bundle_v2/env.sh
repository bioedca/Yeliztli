# shellcheck shell=bash
# scripts/lai_bundle_v2/env.sh
#
# Source this file from every phase script (or from run_rebuild.sh) to load
# the cluster-rebuild environment. All paths are parametrized so the same
# scripts run against either the v1.1 working directory (back-fill) or the
# v2.0.0 working directory (forward rebuild) without edits.
#
# Override any variable by exporting it before invoking run_rebuild.sh.

# ─── Working directories ──────────────────────────────────────────────────
# Top-level rebuild directory on the cluster. Defaults match the v2 layout
# documented in docs/lai-bundle-release-runbook.md and Plan §6.2.
: "${LAI_BUNDLE_VERSION:=v2.0.0}"
: "${WORKDIR:=$HOME/lai_bundle_v2}"
: "${LOG_DIR:=$WORKDIR/logs}"

# ─── Subdirectories (created by Phase 0 of run_rebuild.sh) ───────────────
: "${RAW_DIR:=$WORKDIR/00_raw_downloads}"
: "${SITES_DIR:=$WORKDIR/01_site_lists}"
: "${LIFTOVER_DIR:=$WORKDIR/02_liftover}"
: "${PANEL_DIR:=$WORKDIR/03_subsetted_panels}"
: "${ADMIX_DIR:=$WORKDIR/04_admixture_filtering}"
: "${GNOMIX_DIR:=$WORKDIR/05_gnomix_training}"
: "${VALIDATION_DIR:=$WORKDIR/06_validation}"
: "${BUNDLE_DIR:=$WORKDIR/07_final_bundle}"

# ─── Inputs that must be supplied by the caller ──────────────────────────
# Union catalog TSV (rsid, chrom, pos GRCh37). Produced by the VEP rebuild
# (scripts/generate_vep_input.py --rsid-catalog) — Plan §6.4 phase 2.
: "${UNION_CATALOG_TSV:=}"

# 1000-Genomes pedigree (20130606_g1k.ped) — supplies the parent-child
# relationships Phase 6a needs to pick validation trios (the gnomAD meta has no
# paternal/maternal-id columns). Place it in the validation dir or override.
: "${G1K_PED:=$VALIDATION_DIR/20130606_g1k.ped}"

# ─── External tool paths ─────────────────────────────────────────────────
: "${BEAGLE_JAR:=$HOME/tools/beagle.jar}"
: "${GNOMIX_DIR_INSTALL:=$HOME/tools/gnomix}"
# gnomix reads ./config.yaml relative to CWD; pass this absolute path as its 8th
# arg instead (CWD-independent). The SLURM array writes a per-task copy with
# n_cores set so concurrent chromosomes don't oversubscribe the node.
: "${GNOMIX_CONFIG:=$GNOMIX_DIR_INSTALL/config.yaml}"

# gnomix has its own conda env (it needs sklearn_crfsuite / xgboost, which the
# lai_bundle env lacks). Phase 05 runs gnomix via `conda run -n $GNOMIX_ENV`, so
# the rest of the pipeline can stay in lai_bundle.
: "${GNOMIX_ENV:=gnomix}"
: "${CHAIN_URL:=https://hgdownload.cse.ucsc.edu/goldenpath/hg19/liftOver/hg19ToHg38.over.chain.gz}"
: "${GENETIC_MAPS_URL:=https://bochet.gcc.biostat.washington.edu/beagle/genetic_maps/plink.GRCh38.map.zip}"
: "${GNOMAD_BUCKET:=gs://gcp-public-data--gnomad/resources/hgdp_1kg/phased_haplotypes_v2}"
: "${GNOMAD_META_URL:=gs://gcp-public-data--gnomad/release/3.1/secondary_analyses/hgdp_1kg_v2/metadata_and_qc/gnomad_meta_updated.tsv}"

# ─── Build parameters ────────────────────────────────────────────────────
: "${CHROMS:=1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22}"
: "${BCFTOOLS_THREADS:=4}"
: "${ADMIXTURE_K_LIST:=7 12 20}"
: "${ADMIXTURE_SEED:=42}"  # locked — re-running with this seed reproduces labels (Plan §6.3 step 4)
# Reference-panel selection (phase 04c). The old SINGLE_ANCESTRY_THRESHOLD=0.95
# ADMIXTURE cutoff is DEPRECATED — it dropped 767/770 EUR samples (intermediate
# continental groups never reach 0.95 on one component) → gnomix trained on 3
# Europeans → all Europeans misclassified. Selection is now by curated
# genetic_region with a light outlier floor + a per-region composition gate.
: "${SINGLE_ANCESTRY_THRESHOLD:=0.95}"   # DEPRECATED/unused (see 04c --threshold)
: "${SINGLE_ANCESTRY_MIN_Q:=0.5}"        # light admixture-outlier floor (0 = off)
# Class balance for continentally-intermediate groups. MID is genetically
# adjacent to EUR and the panel is ~5x EUR-heavy (738 vs 152), so an uncapped
# panel lets gnomix pull borderline MID windows into EUR (held-out MID 2/5). The
# floor (MIN_PER_REGION) only guarantees presence; the cap is what makes the
# minority classes competitive. 250 caps EUR/AFR/EAS/CSA and leaves AMR/MID/OCE
# untouched (~250/250/250/250/238/157/30). Override at runtime if the held-out
# per-superpop gate forces a different value (e.g. 350).
: "${PER_REGION_CAP:=250}"               # 0 = no cap; else balance each region to <= N
: "${MIN_PER_REGION:=20}"                # BUILD GATE — fail if any superpop under-represented
: "${HELDOUT_PER_REGION_N:=5}"           # Phase 4 hold-out count per superpop for production-path validation
: "${HELDOUT_MIN_REGION_ACCURACY:=1.0}"  # Phase 7 gate: every superpop must meet/exceed this held-out accuracy
: "${HELDOUT_MIN_EUR_ACCURACY:=1.0}"     # Phase 7 gate: EUR must meet/exceed this held-out accuracy
: "${BEAGLE_XMX:=4g}"
# Phase 6c parallel fan-out: BEAGLE_PARALLEL concurrent Beagle runs, each capped to
# BEAGLE_NTHREADS threads. BEAGLE_PARALLEL auto-scales from the SLURM cpu allocation
# (cpus / threads) so PARALLEL*NTHREADS never oversubscribes the job; floored at 1.
: "${BEAGLE_NTHREADS:=4}"
: "${BEAGLE_PARALLEL:=$(( ${SLURM_CPUS_PER_TASK:-16} / BEAGLE_NTHREADS ))}"
[ "${BEAGLE_PARALLEL}" -ge 1 ] 2>/dev/null || BEAGLE_PARALLEL=1

# ─── Provenance ──────────────────────────────────────────────────────────
: "${GIT_COMMIT:=$(git -C "${BASH_SOURCE%/*}/../.." rev-parse HEAD 2>/dev/null || echo unknown)}"
: "${BUILD_HOST:=$(hostname -s 2>/dev/null || echo unknown)}"
: "${BUILD_DATE:=$(date -u +%Y-%m-%d)}"

mkdir -p \
  "$WORKDIR" "$LOG_DIR" "$RAW_DIR" "$SITES_DIR" "$LIFTOVER_DIR" \
  "$PANEL_DIR" "$ADMIX_DIR" "$GNOMIX_DIR" "$VALIDATION_DIR" "$BUNDLE_DIR"

# ─── Helpers ─────────────────────────────────────────────────────────────
log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG_DIR/run_rebuild.log"; }
phase_log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG_DIR/${PHASE_NAME:-phase}.log"; }
require() { command -v "$1" >/dev/null 2>&1 || { echo "missing required command: $1" >&2; exit 1; }; }
require_file() { [ -s "$1" ] || { echo "missing required input file: $1" >&2; exit 1; }; }
