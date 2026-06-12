#!/bin/bash
# Phase 5 (gnomix training) as a per-chromosome SLURM array — one task trains one
# chromosome, so all ~22 train concurrently instead of sequentially (the rebuild's
# long pole). Submitted by run_rebuild_slurm.sh with an afterok dependency on prep.
#
# Tunables (override on the sbatch command line or via run_rebuild_slurm.sh):
#   --array=1-22[%N]   chromosomes (N = max concurrent; omit %N to let SLURM pack)
#   --cpus-per-task    cores per chromosome (also caps gnomix n_cores — see below)
#   --partition        cluster partition used for rebuild jobs
#
#SBATCH --job-name=lai_gnomix
#SBATCH --partition=gpu
#SBATCH --array=1-22
#SBATCH --cpus-per-task=8
# 64G (not 32G): the v2.0.0 genetic_region panel is ~1.9x the old single-ancestry
# panel (3690 vs 1939 founders), so per-chrom gnomix simulation/training needs more.
#SBATCH --mem=64G
#SBATCH --time=48:00:00
# --no-requeue: a transient per-task failure must NOT silently requeue + re-train.
# SLURM's default Requeue=1 (combined with the old shared-sample_map cp race that
# this fix removes) caused a wasteful, non-converging re-train loop. Fail the task
# instead so the afterok finish dependency surfaces it.
#SBATCH --no-requeue
#SBATCH --output=lai_gnomix_chr%a_%A.out

set -euo pipefail

# shellcheck source=/dev/null
source "${CONDA_SH:-$HOME/miniconda3/etc/profile.d/conda.sh}"
conda activate "${CONDA_ENV:-lai_bundle}"

SCRIPTS_DIR="${LAI_SCRIPTS_DIR:-$SLURM_SUBMIT_DIR}"
# env.sh resolves GNOMIX_DIR / GNOMIX_DIR_INSTALL from WORKDIR (inherited via
# sbatch --export=ALL). It is also sourced again inside 05_train_gnomix.sh.
# shellcheck source=../env.sh
source "$SCRIPTS_DIR/env.sh"

chr="$SLURM_ARRAY_TASK_ID"
ncores="${SLURM_CPUS_PER_TASK:-8}"

# Per-task gnomix config with n_cores pinned to this task's core allocation, so
# concurrent array tasks don't each grab the whole node (gnomix defaults n_cores
# to all CPUs it sees). Unique filename per chromosome → no write race.
cfg="$GNOMIX_DIR/config_slurm_chr${chr}.yaml"
sed "s/^\([[:space:]]*\)n_cores:.*/\1n_cores: ${ncores}/" \
  "$GNOMIX_DIR_INSTALL/config.yaml" > "$cfg"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] gnomix array task: chr${chr}, ${ncores} cores, config=$cfg"
CHROMS="$chr" GNOMIX_CONFIG="$cfg" bash "$SCRIPTS_DIR/05_train_gnomix.sh"
