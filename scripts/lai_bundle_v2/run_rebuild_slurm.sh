#!/usr/bin/env bash
# Submit the LAI v2.0.0 rebuild as a SLURM DAG:
#   prep (02 03 04)  ->  gnomix array (05, one task per chromosome)  ->  finish (06 07)
# chained by afterok dependencies. Phase 05 (gnomix training — the rebuild's long
# pole) runs ~22 chromosomes concurrently as a job array instead of sequentially.
#
# Usage (from anywhere; paths come from env.sh / overrides):
#   UNION_CATALOG_TSV="$LAI_WORKDIR/00_raw_downloads/union_sites.tsv" \
#   WORKDIR="$LAI_WORKDIR" \
#   G1K_PED="$LAI_WORKDIR/06_validation/20130606_g1k.ped" \
#     bash scripts/lai_bundle_v2/run_rebuild_slurm.sh
#
# Tunables: SLURM_PARTITION (cluster partition name), GNOMIX_CPUS
#   (cpus-per-task for phase 05), GNOMIX_ARRAY (e.g. "1-22%11" to throttle
#   concurrency), CONDA_SH, CONDA_ENV.
#
# For a direct (non-SLURM) run, use run_rebuild.sh instead.

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SLURM_DIR="$SCRIPT_DIR/slurm"
export LAI_SCRIPTS_DIR="$SCRIPT_DIR"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

command -v sbatch >/dev/null 2>&1 || { echo "sbatch not found — not on a SLURM cluster?" >&2; exit 1; }
require_file "$UNION_CATALOG_TSV"
require_file "$G1K_PED"

PART="${SLURM_PARTITION:-gpu}"
CPUS="${GNOMIX_CPUS:-8}"
ARRAY="${GNOMIX_ARRAY:-1-22}"
COMMON=(--parsable --export=ALL --partition="$PART" --chdir="$LOG_DIR")

echo "Submitting LAI v2 rebuild DAG (partition=$PART, gnomix array=$ARRAY @ ${CPUS} cpus/task)"

jid_prep=$(sbatch "${COMMON[@]}" "$SLURM_DIR/prep.sbatch")
echo "  prep   (02 03 04)  -> job $jid_prep"

jid_train=$(sbatch "${COMMON[@]}" --dependency="afterok:$jid_prep" \
  --array="$ARRAY" --cpus-per-task="$CPUS" "$SLURM_DIR/05_train_gnomix.sbatch")
echo "  gnomix (05 array)  -> job $jid_train  (after $jid_prep)"

jid_finish=$(sbatch "${COMMON[@]}" --dependency="afterok:$jid_train" "$SLURM_DIR/finish.sbatch")
echo "  finish (06 07)     -> job $jid_finish (after $jid_train)"

echo
echo "Watch:  squeue -j ${jid_prep},${jid_train},${jid_finish}"
echo "Logs:   $LOG_DIR/  (SLURM *.out + per-phase *.log)"
echo "Bundle: $WORKDIR/yeliztli_lai_bundle_${LAI_BUNDLE_VERSION}.tar.gz (after finish)"
