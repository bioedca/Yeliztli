#!/usr/bin/env bash
# Phase 4 — build the gnomix training reference panel.
#
# Input:
#   $PANEL_DIR/ref_panel_all_autosomes.vcf.gz  (Phase 3)
#   $RAW_DIR/gnomad_meta_updated.tsv           (Phase 1)
#
# Output:
#   $ADMIX_DIR/ref_panel_pruned.{bed,bim,fam}  — LD-pruned PLINK files
#   $ADMIX_DIR/admix_K{7,12,20}.K{7,12,20}.s${ADMIXTURE_SEED}.Q — ancestry proportions (fastmixture naming)
#   $ADMIX_DIR/sample_map.txt                  — sample_id<TAB>population (Gnomix input)
#   $ADMIX_DIR/single_ancestry_samples.tsv     — selected table
#   $ADMIX_DIR/excluded_admixed_samples.tsv    — audit log
#   $VALIDATION_DIR/held_out_validation.tsv    — held-out per-superpop validation set
#   $ADMIX_DIR/sample_map.full.txt             — pre-holdout full training map
#
# fastmixture (ADMIXTURE) is still run — its Q now drives only a LIGHT
# admixture-outlier floor + audit, NOT label assignment. Reference labels come
# from the curated gnomAD genetic_region (see 04c_filter_single_ancestry.py).
# The old `max_q >= 0.95` single-ancestry cutoff dropped 767/770 EUR samples
# (intermediate groups never reach 0.95 on one component) → gnomix trained on 3
# Europeans → all Europeans misclassified as CSA. A per-region composition gate
# (MIN_PER_REGION) now fails the build before that can ship again.
# ADMIXTURE seed is locked (env.sh::ADMIXTURE_SEED) for reproducibility.

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PHASE_NAME=04_admixture_filter
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

require plink2
require fastmixture
require python

require_file "$PANEL_DIR/ref_panel_all_autosomes.vcf.gz"
require_file "$RAW_DIR/gnomad_meta_updated.tsv"

phase_log "converting subset panel to LD-pruned PLINK"

cd "$ADMIX_DIR"

if [ ! -s ref_panel_pruned.bed ]; then
  plink2 --vcf "$PANEL_DIR/ref_panel_all_autosomes.vcf.gz" \
    --make-bed \
    --out ref_panel_plink \
    --set-all-var-ids '@:#:$r:$a' \
    --max-alleles 2

  plink2 --bfile ref_panel_plink \
    --indep-pairwise 50 10 0.1 \
    --out pruned_sites

  plink2 --bfile ref_panel_plink \
    --extract pruned_sites.prune.in \
    --make-bed \
    --out ref_panel_pruned
fi

phase_log "LD-pruned SNP count: $(wc -l < ref_panel_pruned.bim)"

for K in $ADMIXTURE_K_LIST; do
  # fastmixture writes <out>.K<k>.s<seed>.Q (e.g. admix_K7.K7.s42.Q), NOT
  # admix_K7.Q — match the real output name or this guard never fires.
  if [ -s "admix_K${K}.K${K}.s${ADMIXTURE_SEED}.Q" ]; then
    phase_log "fastmixture K=$K already complete, skipping"
    continue
  fi
  phase_log "running fastmixture K=$K seed=$ADMIXTURE_SEED"
  fastmixture \
    --bfile ref_panel_pruned \
    --K "$K" \
    --threads "$BCFTOOLS_THREADS" \
    --out "admix_K${K}" \
    --seed "$ADMIXTURE_SEED"
done

phase_log "selecting reference panel by curated genetic_region (min_q>=${SINGLE_ANCESTRY_MIN_Q}, per-region-cap=${PER_REGION_CAP}, gate>=${MIN_PER_REGION})"
python "$SCRIPT_DIR/04c_filter_single_ancestry.py" \
  --fam "$ADMIX_DIR/ref_panel_pruned.fam" \
  --meta "$RAW_DIR/gnomad_meta_updated.tsv" \
  --q-matrix "$ADMIX_DIR/admix_K7.K7.s${ADMIXTURE_SEED}.Q" \
  --min-q "$SINGLE_ANCESTRY_MIN_Q" \
  --per-region-cap "$PER_REGION_CAP" \
  --min-per-region "$MIN_PER_REGION" \
  --seed "$ADMIXTURE_SEED" \
  --out-sample-map "$ADMIX_DIR/sample_map.txt" \
  --out-single-ancestry "$ADMIX_DIR/single_ancestry_samples.tsv" \
  --out-excluded "$ADMIX_DIR/excluded_admixed_samples.tsv"

phase_log "selecting held-out per-superpopulation validation samples before Gnomix training"
python "$SCRIPT_DIR/06f_select_heldout.py" \
  --sample-map "$ADMIX_DIR/sample_map.txt" \
  --n "$HELDOUT_PER_REGION_N" \
  --seed "$ADMIXTURE_SEED" \
  --out-heldout "$VALIDATION_DIR/held_out_validation.tsv" \
  --out-training "$ADMIX_DIR/sample_map.txt" \
  --out-full-backup "$ADMIX_DIR/sample_map.full.txt" \
  --min-per-region "$MIN_PER_REGION"

phase_log "phase 4 complete: $(wc -l < sample_map.txt) reference training samples after hold-out"
