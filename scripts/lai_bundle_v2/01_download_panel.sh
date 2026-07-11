#!/usr/bin/env bash
# Phase 1 — Download the gnomAD HGDP+1KG phased haplotypes + genetic maps.
#
# Re-runnable: complete downloads are skipped and derived maps are regenerated
# deterministically from the extracted source maps.
# Source: gs://gcp-public-data--gnomad/resources/hgdp_1kg/phased_haplotypes_v2
# Output: $RAW_DIR/hgdp1kgp_chr{1..22}.filtered.SNV_INDEL.phased.shapeit5.bcf{,.csi}
#         $RAW_DIR/gnomad_meta_updated.tsv
#         $RAW_DIR/genetic_maps_grch38/{chr_in_chrom_field,no_chr_in_chrom_field}/plink.chr*.GRCh38.map
#           (the plink.GRCh38.map.zip unpacks into these two chrom-naming variants;
#            Beagle/phase 06c + the shipped bundle use chr_in_chrom_field/plink.chrchrN.GRCh38.map)
#         $RAW_DIR/genetic_maps_gnomix/chr{1..22}.map  (3-col TAB chrom/pos/cM; gnomix phase 05)
#
# Plan §6.4 reuses the v1.1 download for v2 unless gnomAD republishes the
# panel — record any version swap in docs/lai-bundle-release-runbook.md.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PHASE_NAME=01_download_panel
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
read -r -a chromosomes <<< "$CHROMS"

require gsutil
require wget
require unzip
require python3

phase_log "downloading gnomAD HGDP+1KG phased haplotypes to $RAW_DIR"

cd "$RAW_DIR"

for chr in "${chromosomes[@]}"; do
  pattern="hgdp1kgp_chr${chr}.filtered.SNV_INDEL.phased.shapeit5.bcf"
  if [ -s "$pattern" ] && [ -s "${pattern}.csi" ]; then
    phase_log "chr${chr}: BCF + index already present, skipping"
    continue
  fi
  gsutil -m cp "${GNOMAD_BUCKET}/${pattern}"*  .
done

if [ ! -s gnomad_meta_updated.tsv ]; then
  gsutil cp "$GNOMAD_META_URL" .
fi

mkdir -p genetic_maps_grch38
maps_complete=true
for chr in "${chromosomes[@]}"; do
  if [ ! -s "genetic_maps_grch38/chr_in_chrom_field/plink.chrchr${chr}.GRCh38.map" ]; then
    maps_complete=false
    break
  fi
done
if [ "$maps_complete" != true ]; then
  phase_log "downloading GRCh38 genetic maps for Beagle"
  wget -q -O plink.GRCh38.map.zip "$GENETIC_MAPS_URL"
  unzip -o plink.GRCh38.map.zip -d genetic_maps_grch38/
  rm -f plink.GRCh38.map.zip
fi

phase_log "deriving validated 3-column Gnomix genetic maps"
python3 "$SCRIPT_DIR/01_convert_gnomix_maps.py" \
  --source-dir "$RAW_DIR/genetic_maps_grch38/chr_in_chrom_field" \
  --output-dir "$RAW_DIR/genetic_maps_gnomix" \
  --chromosomes "${chromosomes[@]}" \
  --source-url "$GENETIC_MAPS_URL"
phase_log "Gnomix map provenance: $RAW_DIR/genetic_maps_gnomix/provenance.json"

phase_log "phase 1 complete"
