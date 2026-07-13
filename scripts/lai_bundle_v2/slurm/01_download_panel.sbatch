#!/bin/bash
# LAI v2 rebuild — resumable Phase 01 download and map-generation prerequisite.
# This job initializes a clean workdir before prep (phases 02–04) can run. Phase
# 01 skips complete panel downloads and deterministically regenerates the Gnomix
# maps and provenance on retry.
#
#SBATCH --job-name=lai_phase01
#SBATCH --partition=gpu
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=24:00:00
#SBATCH --output=lai_phase01_%j.out

set -euo pipefail

# shellcheck source=/dev/null
source "${CONDA_SH:-$HOME/miniconda3/etc/profile.d/conda.sh}"
conda activate "${CONDA_ENV:-lai_bundle}"

SCRIPTS_DIR="${LAI_SCRIPTS_DIR:-$SLURM_SUBMIT_DIR}"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] phase01: download panel and generate maps (WORKDIR=$WORKDIR)"
bash "$SCRIPTS_DIR/run_rebuild.sh" 01
