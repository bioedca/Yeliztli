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

require python
require md5sum
require tar
require conda  # gnomix-model re-export runs in $GNOMIX_ENV (numpy/xgboost/sklearn)
require_file "$VALIDATION_DIR/held_out_validation.tsv"

cd "$BUNDLE_DIR"

phase_log "assembling bundle layout"
mkdir -p phasing_panel genetic_maps gnomix_models liftover beagle metadata

for chr in $CHROMS; do
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
  conda run -n "$GNOMIX_ENV" --no-capture-output \
    python "$SCRIPT_DIR/07b_reexport_gnomix_models.py" \
    --model-pkl "$GNOMIX_DIR/output_chr${chr}/models/model_chm_chr${chr}/model_chm_chr${chr}.pkl" \
    --out-dir "gnomix_models/chr${chr}" \
    --gnomix-dir "$GNOMIX_DIR_INSTALL"
done

cp -f "$LIFTOVER_DIR/hg19ToHg38.over.chain.gz" liftover/
cp -f "$LIFTOVER_DIR/rsid_to_grch38.tsv" liftover/array_site_mapping.tsv

cp -f "$BEAGLE_JAR" beagle/beagle.jar

phase_log "extracting held-out per-superpopulation fixtures"
python "$SCRIPT_DIR/extract_heldout_fixtures.py" \
  --panel-dir "$PANEL_DIR" \
  --validation-dir "$VALIDATION_DIR" \
  --site-map "$LIFTOVER_DIR/rsid_to_grch38.tsv" \
  --chroms $CHROMS

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
  --admixture-seed "$ADMIXTURE_SEED"

phase_log "generating CHECKSUMS.md5"
find . -type f ! -name CHECKSUMS.md5 -print0 | xargs -0 md5sum > CHECKSUMS.md5

phase_log "creating tarball"
tarball="$WORKDIR/yeliztli_lai_bundle_${LAI_BUNDLE_VERSION}.tar.gz"
tar -czf "$tarball" -C "$BUNDLE_DIR" .
sha256sum "$tarball" > "${tarball}.sha256"

phase_log "tarball: $(du -sh "$tarball" | awk '{print $1}'); sha256: $(awk '{print $1}' "${tarball}.sha256")"
phase_log "phase 7 complete"
