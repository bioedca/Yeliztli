#!/usr/bin/env bash
# scripts/lai_bundle_v2/run_rebuild.sh
#
# Orchestrate the LAI bundle v2.0.0 rebuild against the union 23andMe v5 ∪
# AncestryDNA v2.0 site list (Plan §6.4). Each phase is independently
# re-runnable — phases skip work that already produced its expected outputs,
# so re-invoking after a partial failure picks up where it left off.
#
# Usage:
#   UNION_CATALOG_TSV=/path/to/union_sites.tsv \
#   WORKDIR=$HOME/lai_bundle_v2 \
#     bash scripts/lai_bundle_v2/run_rebuild.sh [phase ...]
#
# With no arguments, all phases run in order. Pass one or more phase tokens
# to run only those phases — useful when iterating on a single stage:
#   bash scripts/lai_bundle_v2/run_rebuild.sh 03 04
#
# Phases (executed in this order when none specified):
#   01 — download gnomAD HGDP+1KG phased panel + genetic maps
#   02 — prepare union site list + liftover GRCh37→GRCh38
#   03 — subset reference panel to union sites
#   04 — ADMIXTURE filtering → single-ancestry sample map
#   05 — train Gnomix per chromosome
#   06 — validation (phasing + LAI accuracy)
#   07 — assemble final bundle tarball + checksums
#
# Plan §6.4: only phases 02 and 03 differ from v1.1 (union site list grows
# from ~605k to ~840k); phases 04–07 are unchanged.

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

ALL_PHASES=(01 02 03 04 05 06 07)

# GitHub's macOS runners still use Bash 3.2, which predates associative
# arrays. Keep phase dispatch in a case statement so the direct orchestrator
# has the same behavior on macOS and on newer Linux Bash versions.
phase_script_for() {
  case "$1" in
    01) printf '%s\n' "01_download_panel.sh" ;;
    02) printf '%s\n' "02_prepare_sites.sh" ;;
    03) printf '%s\n' "03_subset_panel.sh" ;;
    04) printf '%s\n' "04_admixture_filter.sh" ;;
    05) printf '%s\n' "05_train_gnomix.sh" ;;
    06) printf '%s\n' "06_validate.sh" ;;
    07) printf '%s\n' "07_assemble_bundle.sh" ;;
    *) return 1 ;;
  esac
}

if [ "$#" -gt 0 ]; then
  PHASES=("$@")
else
  PHASES=("${ALL_PHASES[@]}")
fi

needs_gnomix_environment=false
for phase in "${PHASES[@]}"; do
  case "$phase" in
    05|07) needs_gnomix_environment=true ;;
  esac
done
if [ "$needs_gnomix_environment" = true ]; then
  require python3
  require "$GNOMIX_CONDA_EXECUTABLE"
  require_file "$GNOMIX_ENV_LOCK"
  require_file "$GNOMIX_ENV_VERIFIER"
  verify_gnomix_environment
fi

log "rebuild start — bundle_version=$LAI_BUNDLE_VERSION workdir=$WORKDIR git=$GIT_COMMIT"
log "phases requested: ${PHASES[*]}"

for phase in "${PHASES[@]}"; do
  if ! script=$(phase_script_for "$phase"); then
    echo "unknown phase: $phase (valid: ${ALL_PHASES[*]})" >&2
    exit 2
  fi
  phase_script="$SCRIPT_DIR/$script"
  [ -x "$phase_script" ] || chmod +x "$phase_script" 2>/dev/null || true
  log "▶ phase $phase: $script"
  start=$(date -u +%s)
  bash "$phase_script"
  end=$(date -u +%s)
  log "✓ phase $phase complete in $((end - start))s"
done

log "rebuild finished — tarball at $WORKDIR/yeliztli_lai_bundle_${LAI_BUNDLE_VERSION}.tar.gz"
