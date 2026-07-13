#!/bin/bash
# LAI v2 rebuild — prerequisite phases for the gnomix array: 02 (liftover),
# 03 (subset panel, all chroms + merge), 04 (admixture -> sample_map). Runs as a
# single sequential job after Phase 01 succeeds; the gnomix array (phase 05)
# depends on it via afterok.
#
#SBATCH --job-name=lai_prep
#SBATCH --partition=gpu
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=24:00:00
#SBATCH --output=lai_prep_%j.out

set -euo pipefail

# shellcheck source=/dev/null
source "${CONDA_SH:-$HOME/miniconda3/etc/profile.d/conda.sh}"
conda activate "${CONDA_ENV:-lai_bundle}"

SCRIPTS_DIR="${LAI_SCRIPTS_DIR:-$SLURM_SUBMIT_DIR}"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] prep: phases 02 03 04 (WORKDIR=$WORKDIR)"
bash "$SCRIPTS_DIR/run_rebuild.sh" 02 03 04
