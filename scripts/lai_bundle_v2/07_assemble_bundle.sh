#!/usr/bin/env bash
# Phase 7 — Assemble the final bundle tarball + CHECKSUMS.md5 + metadata.json.
#
# Output:
#   $BUNDLE_DIR/{phasing_panel,genetic_maps,gnomix_models,liftover,beagle}/
#   $BUNDLE_DIR/metadata.json     — provenance per Plan §6.5
#   $BUNDLE_DIR/README.md         — citation + build summary
#   $BUNDLE_DIR/CHECKSUMS.md5
#   $VALIDATION_DIR/heldout_superpop_accuracy_report.json
#   $WORKDIR/yeliztli_lai_bundle_${LAI_BUNDLE_VERSION}.tar.gz
#
# Plan §6.4 phase 7 — bundle layout unchanged from v1.1; only the per-chrom
# panel and model sizes grow (~30% bigger total).

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PHASE_NAME=07_assemble_bundle
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
read -r -a chromosomes <<< "$CHROMS"
expected_autosomes="1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22"
if [ "${chromosomes[*]}" != "$expected_autosomes" ]; then
  phase_log "Phase 07 publishes full autosomal bundles only; CHROMS must be 1 through 22" >&2
  exit 1
fi

require python
require python3
require md5sum
require sha256sum
require tar
require git
require flock
require conda  # gnomix-model re-export runs in $GNOMIX_ENV (numpy/xgboost/sklearn)
require_file "$VALIDATION_DIR/held_out_validation.tsv"
require_file "$RAW_DIR/genetic_maps_gnomix/provenance.json"
require_file "$GNOMIX_DIR_INSTALL/gnomix.py"
require_file "$SCRIPT_DIR/gnomix_provenance.py"

# Hold the assembly lock exclusively until this process exits. Phase 05 takes a
# shared lock, so no concurrent/manual retrain can replace a model or record
# after preflight but before re-export/checksum publication. The descriptor
# closes automatically on every normal or error exit.
mkdir -p "$GNOMIX_DIR/.locks"
exec 8> "$GNOMIX_DIR/.locks/assembly.lock"
if ! flock -n 8; then
  phase_log "Phase 05 training is still active; refusing assembly" >&2
  exit 1
fi

phase_log "verifying pinned, clean Gnomix checkout"
python3 "$SCRIPT_DIR/gnomix_provenance.py" verify-checkout \
  --gnomix-dir "$GNOMIX_DIR_INSTALL" \
  --expected-commit "$GNOMIX_EXPECTED_COMMIT"

phase_log "verifying Gnomix maps and trained-model provenance"
python "$SCRIPT_DIR/01_convert_gnomix_maps.py" \
  --verify \
  --output-dir "$RAW_DIR/genetic_maps_gnomix" \
  --chromosomes "${chromosomes[@]}"

# Validate the complete model generation before changing the bundle layout.
# Phase 07 intentionally does not hash its current/default config: each Phase 05
# task persisted the exact effective config it actually passed to Gnomix.
provenance_args=()
for chr in "${chromosomes[@]}"; do
  derived_map="$RAW_DIR/genetic_maps_gnomix/chr${chr}.map"
  model_dir="$GNOMIX_DIR/output_chr${chr}/models/model_chm_chr${chr}"
  model_pkl="$model_dir/model_chm_chr${chr}.pkl"
  model_map_sha="$model_dir/genetic_map.sha256"
  model_provenance="$model_dir/training_provenance.json"
  require_file "$derived_map"
  require_file "$model_pkl"
  require_file "$model_map_sha"
  require_file "$model_provenance"
  current_map_sha=$(sha256sum "$derived_map" | awk '{print $1}')
  recorded_map_sha=$(awk 'NR == 1 {print $1}' "$model_map_sha")
  if [ "$recorded_map_sha" != "$current_map_sha" ]; then
    phase_log "chr${chr}: trained model does not match current genetic map" >&2
    exit 1
  fi
  python3 "$SCRIPT_DIR/gnomix_provenance.py" verify-record \
    --record "$model_provenance" \
    --chromosome "chr${chr}" \
    --expected-commit "$GNOMIX_EXPECTED_COMMIT" \
    --genetic-map "$derived_map" \
    --model "$model_pkl"
  provenance_args+=(--record "$model_provenance")
done

aggregate_tmp=$(mktemp "$WORKDIR/.gnomix-training-provenance.XXXXXX")
trap 'rm -f "$aggregate_tmp"' EXIT
python3 "$SCRIPT_DIR/gnomix_provenance.py" aggregate \
  --expected-commit "$GNOMIX_EXPECTED_COMMIT" \
  --output "$aggregate_tmp" \
  "${provenance_args[@]}"

cd "$BUNDLE_DIR"

phase_log "assembling bundle layout"
mkdir -p phasing_panel genetic_maps gnomix_models liftover beagle metadata
# A subset rerun must not leave stale records from a prior chromosome set.
rm -f metadata/gnomix_model_chr*.provenance.json \
  metadata/gnomix_training_provenance.json

for chr in "${chromosomes[@]}"; do
  model_dir="$GNOMIX_DIR/output_chr${chr}/models/model_chm_chr${chr}"
  cp -f "$model_dir/genetic_map.sha256" \
    "metadata/gnomix_model_map_chr${chr}.sha256"
  cp -f "$model_dir/training_provenance.json" \
    "metadata/gnomix_model_chr${chr}.provenance.json"
done
cp -f "$aggregate_tmp" metadata/gnomix_training_provenance.json
rm -f "$aggregate_tmp"
trap - EXIT

for chr in "${chromosomes[@]}"; do
  cp -f "$PANEL_DIR/ref_panel_chr${chr}.vcf.gz" phasing_panel/
  cp -f "$PANEL_DIR/ref_panel_chr${chr}.vcf.gz.tbi" phasing_panel/
  # Ship the chr_in_chrom_field plink map the runtime loads as
  # genetic_maps/plink.chrchrN.GRCh38.map (backend/analysis/lai_runner.py).
  # No `|| true`: a missing source must fail loudly, not silently ship an empty
  # genetic_maps/ dir (the old flat path genetic_maps_grch38/plink.chrN... did exactly that).
  cp -f "$RAW_DIR/genetic_maps_grch38/chr_in_chrom_field/plink.chrchr${chr}.GRCh38.map" genetic_maps/
  mkdir -p "gnomix_models/chr${chr}"
  # gnomix's native .pkl is NOT the shipped format — the runtime
  # (backend/analysis/gnomix_inference.py) loads base_coefs.npz + smoother.json +
  # metadata.npz. Re-export the pickle into that dependency-free trio (faithful port
  # of v1.1 reexport_gnomix_models.py; v2 had been raw-copying the gnomix output).
  # Runs in $GNOMIX_ENV to unpickle the sklearn/xgboost model objects.
  python3 "$SCRIPT_DIR/gnomix_provenance.py" verify-checkout \
    --gnomix-dir "$GNOMIX_DIR_INSTALL" \
    --expected-commit "$GNOMIX_EXPECTED_COMMIT" >/dev/null
  python3 "$SCRIPT_DIR/gnomix_provenance.py" verify-record \
    --record "$GNOMIX_DIR/output_chr${chr}/models/model_chm_chr${chr}/training_provenance.json" \
    --chromosome "chr${chr}" \
    --expected-commit "$GNOMIX_EXPECTED_COMMIT" \
    --genetic-map "$RAW_DIR/genetic_maps_gnomix/chr${chr}.map" \
    --model "$GNOMIX_DIR/output_chr${chr}/models/model_chm_chr${chr}/model_chm_chr${chr}.pkl"
  PYTHONDONTWRITEBYTECODE=1 conda run -n "$GNOMIX_ENV" --no-capture-output \
    python "$SCRIPT_DIR/07b_reexport_gnomix_models.py" \
    --model-pkl "$GNOMIX_DIR/output_chr${chr}/models/model_chm_chr${chr}/model_chm_chr${chr}.pkl" \
    --out-dir "gnomix_models/chr${chr}" \
    --gnomix-dir "$GNOMIX_DIR_INSTALL"
  python3 "$SCRIPT_DIR/gnomix_provenance.py" verify-checkout \
    --gnomix-dir "$GNOMIX_DIR_INSTALL" \
    --expected-commit "$GNOMIX_EXPECTED_COMMIT" >/dev/null
  python3 "$SCRIPT_DIR/gnomix_provenance.py" verify-record \
    --record "$GNOMIX_DIR/output_chr${chr}/models/model_chm_chr${chr}/training_provenance.json" \
    --chromosome "chr${chr}" \
    --expected-commit "$GNOMIX_EXPECTED_COMMIT" \
    --genetic-map "$RAW_DIR/genetic_maps_gnomix/chr${chr}.map" \
    --model "$GNOMIX_DIR/output_chr${chr}/models/model_chm_chr${chr}/model_chm_chr${chr}.pkl"
done

cp -f "$LIFTOVER_DIR/hg19ToHg38.over.chain.gz" liftover/
cp -f "$LIFTOVER_DIR/rsid_to_grch38.tsv" liftover/array_site_mapping.tsv

cp -f "$BEAGLE_JAR" beagle/beagle.jar
cp -f "$RAW_DIR/genetic_maps_gnomix/provenance.json" metadata/gnomix_genetic_maps.json

phase_log "extracting held-out per-superpopulation fixtures"
python "$SCRIPT_DIR/extract_heldout_fixtures.py" \
  --panel-dir "$PANEL_DIR" \
  --validation-dir "$VALIDATION_DIR" \
  --site-map "$LIFTOVER_DIR/rsid_to_grch38.tsv" \
  --chroms "${chromosomes[@]}"

phase_log "running held-out per-superpopulation production-inference gate"
mkdir -p "$VALIDATION_DIR/heldout_runtime_data"
YELIZTLI_DATA_DIR="$VALIDATION_DIR/heldout_runtime_data" \
YELIZTLI_LAI_BUNDLE_PATH="$BUNDLE_DIR" \
HELDOUT_MIN_REGION_ACCURACY="$HELDOUT_MIN_REGION_ACCURACY" \
HELDOUT_MIN_EUR_ACCURACY="$HELDOUT_MIN_EUR_ACCURACY" \
VAL_WORKERS="${VAL_WORKERS:-6}" \
  python "$SCRIPT_DIR/06f_heldout_superpop_accuracy.py" \
    "$VALIDATION_DIR/heldout_fixtures" \
    "$VALIDATION_DIR/held_out_validation.tsv" \
    "$VALIDATION_DIR/heldout_superpop_accuracy_report.json"

phase_log "writing metadata.json (Plan §6.5)"
python "$SCRIPT_DIR/07_write_metadata.py" \
  --bundle-dir "$BUNDLE_DIR" \
  --union-catalog "$UNION_CATALOG_TSV" \
  --validation-dir "$VALIDATION_DIR" \
  --git-commit "$GIT_COMMIT" \
  --build-host "$BUILD_HOST" \
  --build-date "$BUILD_DATE" \
  --bundle-version "$LAI_BUNDLE_VERSION" \
  --gnomix-provenance "$BUNDLE_DIR/metadata/gnomix_training_provenance.json" \
  --admixture-seed "$ADMIXTURE_SEED"

phase_log "generating CHECKSUMS.md5"
find . -type f ! -name CHECKSUMS.md5 -print0 | xargs -0 md5sum > CHECKSUMS.md5

phase_log "creating tarball"
tarball="$WORKDIR/yeliztli_lai_bundle_${LAI_BUNDLE_VERSION}.tar.gz"
tar -czf "$tarball" -C "$BUNDLE_DIR" .
sha256sum "$tarball" > "${tarball}.sha256"

phase_log "tarball: $(du -sh "$tarball" | awk '{print $1}'); sha256: $(awk '{print $1}' "${tarball}.sha256")"
phase_log "phase 7 complete"
