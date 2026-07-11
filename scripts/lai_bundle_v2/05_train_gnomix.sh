#!/usr/bin/env bash
# Phase 5 — Train Gnomix per chromosome at array density.
#
# Input:
#   $PANEL_DIR/ref_panel_chr{N}.vcf.gz        (Phase 3)
#   $ADMIX_DIR/sample_map.txt                 (Phase 4)
#   $RAW_DIR/genetic_maps_gnomix/chr{N}.map  (Phase 1; TAB-delimited 3-col chrom/pos/cM for gnomix)
#   $GNOMIX_DIR_INSTALL/gnomix.py             (cloned from AI-sandbox/gnomix)
#
# Output:
#   $GNOMIX_DIR/output_chr{N}/                — pickled XGBoost models + config
#   $GNOMIX_DIR/output_chr{N}/.../genetic_map.sha256 — model-to-map provenance binding
#   $LOG_DIR/gnomix_train_chr{N}.log          — per-chrom training log
#
# Plan §6.4: phase unchanged from v1.1; models retrain against the larger
# window count (~30% bigger total). Bio-validator validates per-window
# accuracy ≥0.88 mean before publication.
#
# BUG F (FIXED — minimal-query): gnomix runs a post-train inference on the
# *query* AFTER it trains+saves the model. The v1.1 invocation passed the full
# ~4091-sample phasing panel as the query, so that tail re-phased all 4091
# haplotypes and HUNG for hours ("Phasing individual N/4091") even though the
# trained model_chm_chrN.pkl was already on disk (07b re-exports it; the
# inference output is unused). Training consumes only reference_file + sample_map
# (+ the seeded simulation in config.yaml, seed=94305), so the model is
# independent of the query. This script now passes a tiny 2-sample query (a
# strict subset of the panel -> identical sites) so the post-train inference is
# trivial and the SLURM array finishes on its own — no harvest babysitting, the
# afterok finish fires cleanly. phase=True is kept so the saved model still ships
# its phasing module for real unphased AncestryDNA uploads. (The EUR-fix rebuild
# instead HARVESTED each task — scancel once the model + "Estimated val accuracy"
# appeared, then ran finish directly — which remains a valid fallback.)

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PHASE_NAME=05_train_gnomix
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
read -r -a chromosomes <<< "$CHROMS"

require conda  # gnomix runs in its own env (GNOMIX_ENV) via `conda run`
require python3
require sha256sum
require bcftools
require_file "$ADMIX_DIR/sample_map.txt"
require_file "$GNOMIX_DIR_INSTALL/gnomix.py"
require_file "$SCRIPT_DIR/gnomix_launcher.py"  # pandas>=2 compat shim wrapper
require_file "$GNOMIX_CONFIG"
require_file "$RAW_DIR/genetic_maps_gnomix/provenance.json"

phase_log "verifying Gnomix genetic maps against Phase 1 provenance"
python3 "$SCRIPT_DIR/01_convert_gnomix_maps.py" \
  --verify \
  --output-dir "$RAW_DIR/genetic_maps_gnomix" \
  --chromosomes "${chromosomes[@]}"

# Do NOT stage the sample_map into a single shared $GNOMIX_DIR path. Under the
# phase-05 SLURM array all 22 tasks share $GNOMIX_DIR, so `cp` to one shared
# destination RACES on the cluster NFS (`cp: cannot create regular file
# '.../sample_map.txt': File exists`); with `set -e` that kills the task and
# SLURM requeues + re-trains it — a wasteful loop that also strands chroms that
# keep losing the race (observed: chr6 never produced a model). gnomix reads the
# sample_map read-only (laidataset.py: pd.read_csv), so pass
# $ADMIX_DIR/sample_map.txt directly — concurrent reads of the unchanging file
# are race-free, and no per-task copy is needed.

cd "$GNOMIX_DIR"

for chr in "${chromosomes[@]}"; do
  panel_vcf="$PANEL_DIR/ref_panel_chr${chr}.vcf.gz"
  # gnomix wants a 3-col TAB map (chrom, pos, cM); that is genetic_maps_gnomix/chrN.map,
  # NOT the 4-col space-delimited genetic_maps_grch38/.../plink.*.GRCh38.map (Beagle's format).
  genetic_map="$RAW_DIR/genetic_maps_gnomix/chr${chr}.map"
  out_dir="output_chr${chr}"
  # gnomix saves the trained model NESTED at
  # output_chrN/models/model_chm_chrN/model_chm_chrN.pkl (NOT output_chrN/*.pkl) —
  # check that exact path or the skip-guard / success-check below never fires and
  # the task exit-1's "MISSING" after a successful train.
  model_dir="$out_dir/models/model_chm_chr${chr}"
  model_pkl="$model_dir/model_chm_chr${chr}.pkl"
  model_map_sha="$model_dir/genetic_map.sha256"
  require_file "$panel_vcf"
  require_file "$genetic_map"
  current_map_sha=$(sha256sum "$genetic_map" | awk '{print $1}')
  recorded_map_sha=$(awk 'NR == 1 {print $1}' "$model_map_sha" 2>/dev/null || true)

  if [ -s "$model_pkl" ] && [ "$recorded_map_sha" = "$current_map_sha" ]; then
    phase_log "chr${chr}: gnomix model matches current genetic map, skipping"
    continue
  fi
  if [ -s "$model_pkl" ]; then
    phase_log "chr${chr}: genetic-map provenance changed or missing; retraining"
  fi

  phase_log "chr${chr}: training gnomix"
  mkdir -p "$model_dir"
  # Invalidate the binding before training and move any prior model aside. A
  # failed retry can never pass the final completeness gate with stale output.
  : > "$model_map_sha"
  if [ -s "$model_pkl" ]; then
    mv -f "$model_pkl" "${model_pkl}.stale"
  fi
  # ── BUG F fix: minimal query so the post-train inference can't hang ────────
  # Build a tiny 2-sample query (first 2 panel samples → a strict subset, so its
  # sites are identical to the reference). gnomix only uses the query for the
  # discarded post-train inference; passing 2 instead of ~4091 samples makes that
  # pass trivial. Per-chrom filename → no write race under the SLURM array. Built
  # in the outer (lai_bundle) env, which has bcftools, before the gnomix conda run.
  query_vcf="$GNOMIX_DIR/minquery_chr${chr}.vcf.gz"
  if [ ! -s "$query_vcf" ] || [ ! -s "$query_vcf.tbi" ]; then
    # NB: `... | head -n2` would SIGPIPE bcftools (exit 141) and, under
    # `set -o pipefail`, kill the task; `awk 'NR<=2'` drains the stream so
    # bcftools always exits 0.
    qsamples=$(bcftools query -l "$panel_vcf" | awk 'NR<=2' | paste -sd,)
    bcftools view -s "$qsamples" -Oz -o "$query_vcf" "$panel_vcf"
    bcftools index -t -f "$query_vcf"
  fi
  # gnomix.py infers its mode SOLELY from positional arg count (see
  # ~/tools/gnomix/gnomix.py): len(sys.argv)==6 -> pre-trained/inference;
  # ==8 or ==9 -> train. TRAINING needs exactly 7 positional args in this
  # source order:
  #   query_file  output_basename  chr_nr  phase  genetic_map  reference_file  sample_map
  # reference_file = the phased panel; query_file = the tiny $query_vcf built
  # above (BUG F fix). Training reads reference_file + sample_map only, so the
  # model is identical to passing the panel as the query — only the discarded
  # post-train inference shrinks (and so no longer hangs).
  # The old 6-arg call gave len(sys.argv)==7 -> "Incorrect number of arguments"
  # + sys.exit(0): a SILENT no-op that set -e cannot catch (exit 0).
  # The 7-positional form (args 1-7) is the proven v1.1 production invocation
  # (phase=True, chr_nr="chr${chr}", reference=panel — confirmed against the
  # cluster bash_history training loop; phase=True ships the model's phasing
  # module for unphased query data).
  # 8th arg = config file (len(sys.argv)==9): gnomix otherwise reads ./config.yaml
  # relative to CWD, which is $GNOMIX_DIR (no config there) -> FileNotFoundError.
  # Passing $GNOMIX_CONFIG (absolute) makes it CWD-independent AND lets the SLURM
  # array cap n_cores per task.
  # gnomix runs in its own env ($GNOMIX_ENV) — it needs sklearn_crfsuite/xgboost
  # the lai_bundle env lacks — via `conda run` so the rest of the pipeline (this
  # script, run_rebuild.sh) can stay in lai_bundle. --no-capture-output streams to tee.
  # gnomix is a pandas<2 tool: src/laidataset.py calls the removed DataFrame.append
  # (the small-population include_all path; fires for tiny pops like EUR=3). The
  # shared GNOMIX_ENV ships pandas 2.x, so run gnomix THROUGH gnomix_launcher.py,
  # which restores DataFrame.append (-> pd.concat) in-process only — no mutation of
  # the shared env or the gnomix checkout. The launcher forwards every arg after the
  # gnomix.py path verbatim, so gnomix still sees the 8 positional args + config.
  conda run -n "$GNOMIX_ENV" --no-capture-output \
    python "$SCRIPT_DIR/gnomix_launcher.py" \
    "$GNOMIX_DIR_INSTALL/gnomix.py" \
    "$query_vcf" \
    "$out_dir" \
    "chr${chr}" \
    True \
    "$genetic_map" \
    "$panel_vcf" \
    "$ADMIX_DIR/sample_map.txt" \
    "$GNOMIX_CONFIG" \
    2>&1 | tee "$LOG_DIR/gnomix_train_chr${chr}.log"
  # gnomix exits 0 even on the bad-argc usage path; fail loudly if that happens
  # so the orchestrator stops instead of "completing" with no model.
  if grep -q "Incorrect number of arguments" "$LOG_DIR/gnomix_train_chr${chr}.log"; then
    phase_log "chr${chr}: gnomix rejected its arguments (see log)" >&2
    exit 1
  fi
  require_file "$model_pkl"
  rm -f "${model_pkl}.stale"
  marker_tmp="${model_map_sha}.tmp.$$"
  printf '%s  %s\n' "$current_map_sha" "chr${chr}.map" > "$marker_tmp"
  mv -f "$marker_tmp" "$model_map_sha"
done

phase_log "phase 5 complete"
missing=0
for chr in "${chromosomes[@]}"; do
  chr_model="output_chr${chr}/models/model_chm_chr${chr}/model_chm_chr${chr}.pkl"
  chr_marker="output_chr${chr}/models/model_chm_chr${chr}/genetic_map.sha256"
  chr_map="$RAW_DIR/genetic_maps_gnomix/chr${chr}.map"
  current_map_sha=$(sha256sum "$chr_map" | awk '{print $1}')
  recorded_map_sha=$(awk 'NR == 1 {print $1}' "$chr_marker" 2>/dev/null || true)
  if [ -s "$chr_model" ] && [ "$recorded_map_sha" = "$current_map_sha" ]; then
    phase_log "chr${chr}: OK ($(du -sh "output_chr${chr}" | awk '{print $1}'))"
  else
    phase_log "chr${chr}: MISSING OR STALE"
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  phase_log "phase 5 FAILED: one or more gnomix models missing or stale" >&2
  exit 1
fi
