# LAI Bundle Release Runbook

This runbook describes how to rebuild and publish the local-ancestry
inference (LAI) bundle (`genomeinsight_lai_bundle_<version>.tar.gz`) as a
GitHub Release asset, and how to wire the release into
`bundles/manifest.json`.

Scope: this runbook covers the `lai_bundle` stream only. The VEP bundle has
its own runbook at `docs/bundle-release-runbook.md`. The two streams ship
under independent semver tags and are released independently (Plan §2.1).

The rebuild itself is **out-of-repo cluster work** — ADMIXTURE filtering,
Gnomix training, and trio-based phasing validation each take hours and run
on an operator-provided SLURM build host. This repo carries the parametrized
build scripts under `scripts/lai_bundle_v2/`, the orchestration entry point,
and this runbook.

---

## 1. Overview

The `lai_bundle` stream pins, per release, a tarball containing:

- `phasing_panel/` — subsetted gnomAD HGDP+1KG phased reference VCFs (per chrom).
- `genetic_maps/` — Beagle GRCh38 maps.
- `gnomix_models/chr{1..22}/` — trained Gnomix models (one dir per autosome).
- `liftover/` — `hg19ToHg38.over.chain.gz` + `array_site_mapping.tsv`
  (runtime rsID → GRCh38 lookup).
- `beagle/beagle.jar` — pinned Beagle 5.x JAR.
- `metadata.json` — provenance per [Plan §6.5](AncestryDNA_Integration_Plan.md#65-bundle-metadatajson-provenance-schema).
- `CHECKSUMS.md5` — file-level checksums for integrity audit.

Tag prefix: `lai-bundle-v<semver>` (e.g., `lai-bundle-v2.0.0`).
Asset filename (stable per tag): `genomeinsight_lai_bundle_v<semver>.tar.gz`.
Asset URL (stable per tag, never expires):
`https://github.com/<org>/Yeliztli/releases/download/lai-bundle-v<semver>/genomeinsight_lai_bundle_v<semver>.tar.gz`

The v1.1 baseline tarball is ~523 MB; v2.0.0 is ~700–750 MB
(union-catalog panel + larger Gnomix windows).

The manifest's `lai_bundle.version` is the authoritative semver consulted by
the soft staleness gate (Plan §6.7) and the update flow. The tarball's
internal `metadata.json::bundle_version` is informational/audit only.

---

## 2. Prerequisites

Run on the SLURM build host in the chosen `$WORKDIR`:

- `conda env list | grep lai_bundle` returns the dedicated rebuild env
  (`lai_bundle`). Pin it with
  `conda env export --no-builds > docs/lai-bundle-release-runbook-env.lock.yaml`
  and remove the host-specific `prefix:` line before committing the lock — the
  SHA-256 is referenced from `metadata.json::tool_versions` (Plan §6.3 step 2).
- Tool versions pinned (Plan §6.3 step 4):
  - `bcftools --version`
  - Beagle JAR (5.x) SHA-256 recorded
  - Gnomix full git commit SHA selected and enforced (not a branch, tag name,
    or short SHA), with a clean checkout from
    `https://github.com/AI-sandbox/gnomix`
  - `fastmixture --version` (or `admixture --version`) + the locked random seed
    (`scripts/lai_bundle_v2/env.sh::ADMIXTURE_SEED` defaults to `42`).
- ~500 GB scratch on `$WORKDIR`.
- `gh` CLI authenticated against the Yeliztli repo with `repo` scope.

The orchestrator script provisions the directory layout on first run; no
manual `mkdir` is needed.

Select the immutable Gnomix revision deliberately before submitting work. The
workflow has no default because the revision used by a historical mutable
checkout cannot be inferred safely. Replace the example SHA with the revision
validated for the release:

```bash
export GNOMIX_DIR_INSTALL="${GNOMIX_DIR_INSTALL:-$HOME/tools/gnomix}"
GNOMIX_SHA=0123456789abcdef0123456789abcdef01234567  # replace for this release
git -C "$GNOMIX_DIR_INSTALL" remote set-url origin https://github.com/AI-sandbox/gnomix.git
git -C "$GNOMIX_DIR_INSTALL" fetch --prune origin
git -C "$GNOMIX_DIR_INSTALL" checkout --detach "$GNOMIX_SHA"
test "$(git -C "$GNOMIX_DIR_INSTALL" rev-parse --verify 'HEAD^{commit}')" = "$GNOMIX_SHA"
test -z "$(git -C "$GNOMIX_DIR_INSTALL" status --porcelain=v1 --untracked-files=all)"
export GNOMIX_EXPECTED_COMMIT="$GNOMIX_SHA"
```

Phases 05 and 07 independently reject a missing/short/mismatched revision or a
dirty checkout. They also require the canonical `origin` above and a fetched
`origin/*` ref containing the selected commit, so a local/fork-only commit cannot
be mislabeled as official Gnomix source. Do not assign the current upstream
`main` revision to an older model whose training checkout was not recorded;
rebuild it under this contract.

---

## 3. Host & path conventions

| Variable           | Value                                                                  | Notes |
|--------------------|------------------------------------------------------------------------|-------|
| Build host alias   | `$LAI_BUILD_HOST`                                                       | operator-provided SSH alias for the SLURM host |
| v1.1 working dir   | `$LAI_V1_WORKDIR`                                                       | read-only reference; reuse Phase 1 downloads when possible |
| v2.0.0 working dir | `$LAI_WORKDIR` / `$WORKDIR`                                             | working directory for the v2 build |
| In-repo scripts    | `scripts/lai_bundle_v2/` (this repo)                                   | source of truth |
| On-cluster scripts | `$LAI_WORKDIR/scripts/`                                                 | rsynced from the repo per §4 |

The rebuild reuses v1.1's `00_raw_downloads/` whenever the upstream gnomAD
panel hasn't been republished — record any swap (and the new SHA-256) in
`lai_bundle_build/v2_rebuild_log.md`.

---

## 4. Rsync the in-repo scripts to the cluster

Before invoking `run_rebuild.sh` on the cluster, push the latest scripts
from the repo. Run this from your dev box after setting `LAI_BUILD_HOST` and
`LAI_WORKDIR`:

```bash
: "${LAI_BUILD_HOST:?set LAI_BUILD_HOST to the SLURM SSH alias}"
: "${LAI_WORKDIR:?set LAI_WORKDIR to the remote LAI build directory}"

# Dry-run first to confirm the list.
rsync -av --delete --dry-run \
  scripts/lai_bundle_v2/ \
  "${LAI_BUILD_HOST}:${LAI_WORKDIR%/}/scripts/"

# Real sync.
rsync -av --delete \
  scripts/lai_bundle_v2/ \
  "${LAI_BUILD_HOST}:${LAI_WORKDIR%/}/scripts/"
```

`--delete` keeps the cluster copy a clean mirror of the repo, so a script
that's been removed in the repo (or renamed) doesn't keep running stale on
the cluster. Re-run whenever you tweak a phase script.

---

## 5. Assemble the union site list (Phase 2 input)

The LAI rebuild consumes the same union catalog that drives the VEP rebuild
(Plan §6.4 phase 2). Either:

1. Reuse the union catalog produced for the VEP release — copy the TSV from
   the VEP rebuild's working dir to the cluster:

   ```bash
   rsync -av path/to/union_sites.tsv "${LAI_BUILD_HOST}:${LAI_WORKDIR%/}/00_raw_downloads/"
   ```

2. Or regenerate it from the in-repo helper:

   ```bash
   ssh "$LAI_BUILD_HOST"
   cd "$LAI_WORKDIR"
   conda activate lai_bundle
   python "$REPO_CHECKOUT/scripts/generate_vep_input.py" \
     --rsid-catalog 00_raw_downloads/union_sites.tsv \
     -o /tmp/vep_input.vcf
   ```

The TSV columns are `rsid<TAB>chrom<TAB>pos` in GRCh37 coordinates, sorted
by `(chrom, pos)`, autosomal sites only. Path is then passed as the
`UNION_CATALOG_TSV` environment variable to `run_rebuild.sh`.

---

## 6. Rebuild — end-to-end sequence

The orchestrator drives every phase. Each phase is idempotent — re-running
skips outputs that already exist, so a partial failure can be resumed
without re-doing earlier phases.

```bash
ssh "$LAI_BUILD_HOST"
cd "$LAI_WORKDIR"
conda activate lai_bundle
GNOMIX_SHA=0123456789abcdef0123456789abcdef01234567  # same validated SHA from §2
export GNOMIX_EXPECTED_COMMIT="$GNOMIX_SHA"

UNION_CATALOG_TSV="$LAI_WORKDIR/00_raw_downloads/union_sites.tsv" \
WORKDIR="$LAI_WORKDIR" \
LAI_BUNDLE_VERSION=v2.0.0 \
  bash scripts/run_rebuild.sh
```

To resume from a single phase (e.g., re-train Gnomix only):

```bash
GNOMIX_SHA=0123456789abcdef0123456789abcdef01234567  # same validated SHA from §2
export GNOMIX_EXPECTED_COMMIT="$GNOMIX_SHA"
UNION_CATALOG_TSV="$LAI_WORKDIR/00_raw_downloads/union_sites.tsv" \
WORKDIR="$LAI_WORKDIR" \
  bash scripts/run_rebuild.sh 05
```

Phases (Plan §6.4):

| Phase | Script                          | Wall-clock (v1.1 baseline)   |
|-------|---------------------------------|------------------------------|
| 01    | `01_download_panel.sh`          | 2–6 h (network; overnight)   |
| 02    | `02_prepare_sites.sh`           | ~10 min                      |
| 03    | `03_subset_panel.sh`            | 1–2 h                        |
| 04    | `04_admixture_filter.sh`        | 2–4 h                        |
| 05    | `05_train_gnomix.sh`            | 4–12 h                       |
| 06    | `06_validate.sh`                | 8–24 h                       |
| 07    | `07_assemble_bundle.sh`         | ~30 min                      |

Phase 01 derives Gnomix's three-column `chrN<TAB>bp<TAB>cM` maps from the
downloaded GRCh38 PLINK maps. It validates every requested autosome and writes
source and derived SHA-256 values to
`00_raw_downloads/genetic_maps_gnomix/provenance.json`; Phase 07 preserves that
record as `metadata/gnomix_genetic_maps.json` in the bundle. Phase 05 binds each
trained model to its chromosome map checksum, the selected Gnomix commit, the
SHA-256 of the exact effective config, and the native model SHA-256. The
algorithm learns window-specific parameters from configurable training and
model choices, so the paper DOI or algorithm name alone does not identify a
trained artifact [1]. Phase 07 verifies and ships the per-model records as
`metadata/gnomix_model_chrN.provenance.json`, the compatibility map bindings as
`metadata/gnomix_model_map_chrN.sha256`, and an aggregate record as
`metadata/gnomix_training_provenance.json`. `metadata.json::gnomix_training`
publishes the common repository, full commit, effective-config SHA-256, clean
checkout attestation, model count, and aggregate-manifest path.

The effective config is snapshotted separately for every training task before
Gnomix opens it. Existing models without the JSON record are intentionally
stale and retrain once. The full bundle cannot assemble if chromosome records
are missing, if their model/map hashes no longer match, or if their Gnomix
commits/config hashes are mixed. Because `n_cores` is part of the effective
SLURM config, changing `GNOMIX_CPUS` also changes its hash and intentionally
invalidates reuse.

Phase 07 is publication-only and requires `CHROMS` to be exactly autosomes
1–22; it refuses subset bundles so a diagnostic rerun cannot retain stale
chromosome artifacts from an earlier full assembly.

Phases 02 and 03 operate on the union catalog (~2.0M sites; ~1.94M autosomal)
instead of the 23andMe v5 catalog (~605k). The random seed remains locked at
`ADMIXTURE_SEED=42` (Plan §6.3 step 4), and Phase 07 adds the derived-map
provenance record described above to the bundle.

**Phase 05 runs gnomix in its own conda env.** gnomix needs `sklearn_crfsuite`/
`xgboost`, which the `lai_bundle` env lacks; `05_train_gnomix.sh` invokes it via
`conda run -n $GNOMIX_ENV` (default `gnomix`), so the rest of the pipeline stays
in `lai_bundle`. **Phase 06 needs the 1000G pedigree:** place
`20130606_g1k.ped` at `$WORKDIR/06_validation/` (or set `G1K_PED`).

### 6a. SLURM submission (parallel — recommended for the full run)

`run_rebuild_slurm.sh` submits the rebuild as a 4-job SLURM DAG chained by
`afterok` dependencies. A dedicated Phase 01 job always runs first, so a clean
workdir downloads the reference panel and produces the derived Gnomix maps and
provenance before Phase 02 starts. Phase 01 is resumable: a retry skips complete
panel downloads and regenerates the maps deterministically. **Phase 05 (gnomix
training, the long pole) runs as a per-chromosome job array** so ~22 chromosomes
train concurrently instead of sequentially:

```bash
ssh "$LAI_BUILD_HOST"
conda activate lai_bundle           # submitter env; jobs re-source conda
GNOMIX_SHA=0123456789abcdef0123456789abcdef01234567  # same validated SHA from §2
export GNOMIX_EXPECTED_COMMIT="$GNOMIX_SHA"
UNION_CATALOG_TSV="$LAI_WORKDIR/00_raw_downloads/union_sites.tsv" \
WORKDIR="$LAI_WORKDIR" \
G1K_PED="$LAI_WORKDIR/06_validation/20130606_g1k.ped" \
  bash "$LAI_WORKDIR/scripts/run_rebuild_slurm.sh"
#   phase01 (01)        -> job N
#   prep    (02 03 04)  -> job N+1  (after N)
#   gnomix  (05 array)  -> job N+2  (after N+1)
#   finish  (06 07)     -> job N+3  (after N+2)
# Watch: squeue -j N,N+1,N+2,N+3 ; logs under $LAI_WORKDIR/logs/
```

If Phase 01 fails partway through, cancel the stale dependent jobs and submit the
DAG again. The new Phase 01 job resumes from the completed downloads; no manual
out-of-band Phase 01 invocation is required.

Tunables: `SLURM_PARTITION` (cluster partition name; defaults to `gpu` in
`run_rebuild_slurm.sh`),
`GNOMIX_CPUS` (cores per chromosome; also caps gnomix `n_cores`), `GNOMIX_ARRAY`
(e.g. `1-22%11` to throttle concurrency), `CONDA_SH`, `CONDA_ENV`, `GNOMIX_ENV`.
The array parallelizes phase 05 from ~4–12 h sequential down to roughly the
slowest single chromosome (× the number of waves once cores are saturated).

---

## 7. Source data provenance

For every input artifact (gnomAD HGDP+1KG BCFs, liftover chain, 1000G
genetic map, ADMIXTURE binary, Gnomix full commit SHA), record in
`lai_bundle_build/v2_rebuild_log.md`:

- download URL
- SHA-256
- file size
- retrieval date

Where the v1.1 cluster artifacts can be reused, record their SHA-256 and
skip re-download; document any upstream-updated swap explicitly. The
runbook lock file (`docs/lai-bundle-release-runbook-env.lock.yaml`) is
referenced from `metadata.json::tool_versions` so consumers can audit the
build host environment without untarring the bundle.

---

## 8. Bio-validator sign-off

Before publication, bio-validator confirms:

- **LAI accuracy**: mean per-window accuracy ≥ 0.88 on held-out
  single-ancestry samples (Plan §6.4 final paragraph). The report is
  written by `06e_lai_accuracy.py` to `$VALIDATION_DIR/lai_accuracy_report.json`.
  `06e` also fails the build if any target superpopulation is under-represented
  in the training panel (the per-region composition gate, `--min-per-region`).
- **Gnomix-training-held-out per-superpopulation inference accuracy (REQUIRED):**
  The mean per-window accuracy above is *blind to per-population balance*. The
  first v2.0.0 LAI bundle reported **0.97** yet misclassified *every* European —
  the old `04c` `max_q >= 0.95` single-ancestry filter left **EUR = 3** samples
  in training (continentally-intermediate groups never form a clean ADMIXTURE
  component), so a held-out Iberian classified as 94% CSA / 0.3% EUR through the
  production pipeline. Therefore, before publishing, run a held-out
  per-superpopulation **inference** check: hold a few samples per superpopulation
  OUT of the Gnomix training panel, build AncestryDNA-density fixtures from the
  phasing panel, run each through the production `run_lai_analysis` against the
  *assembled* bundle, and confirm each classifies to its own superpopulation
  (EUR must classify as EUR). Scripts:
  `06f_select_heldout.py` → `extract_heldout_fixtures.py` →
  `06f_heldout_superpop_accuracy.py` (per-superpop accuracy; asserts EUR == 1.0).
  The 2026-06-04 rebuild result was EUR/AFR/AMR/CSA/EAS/OCE **5/5**, MID **2/5**
  (MID is a known residual — intermediate and adjacent to the much-larger EUR;
  if MID accuracy matters, rebuild with `--per-region-cap` to balance the large
  classes down and/or add MID training samples). The build-time composition gate
  (`--min-per-region`, default 20) is the *floor* that prevents the EUR=3
  regression; this inference check is the runtime classifier-regression proof.
  The targets remain in the Beagle panel, so this is not independent phasing or
  local-window truth and must not be reused to calibrate sparse coverage. Follow
  the [LAI coverage calibration](maintainer/lai-coverage-calibration.md) contract
  for that purpose.
- **Phasing accuracy**: mean switch error rate ≤ 0.0566 vs. trio-truth
  haplotypes (Plan §6.4). Written by `06d_phasing_accuracy.py` to
  `$VALIDATION_DIR/phasing_accuracy_report.json`.
- **23andMe parity**: the LAI runner produces byte-identical output on
  legacy 23andMe v5 sample DBs against the new bundle (locked by
  `tests/backend/test_lai_runner_telemetry_parity.py` — see Plan §6.6).
- **Sparse-coverage calibration (required before setting a positive minimum):**
  Complete the leak-resistant simulation and confirmation workflow in
  [LAI coverage calibration](maintainer/lai-coverage-calibration.md). A dated
  2026-07-12 exploratory audit found one AMR and no OCE founder after strict
  exclusions, but not every audit-input hash was preserved; rerun and save the
  complete candidate audit before treating those counts as a release fact. The
  executable gate independently requires all seven classes in both founder-disjoint
  splits. It remains fail-closed until that contract passes; the successful `06f`
  classifier-regression check above does not waive it.
- **Re-runnability**: re-running with `ADMIXTURE_SEED=42` reproduces the
  Phase 4 sample map bit-for-bit on the same input (Plan §6.3 step 4).

Drift below targets → blocker ticket, do not publish (Plan §12.2 Validation
gates). Sign-off attaches to the PR as a comment along with both report
JSONs.

### 8a. Coverage-calibration harness workflow

This workflow applies once an eligible all-class founder panel exists. Before generating
fixtures, make the calibration and final-confirmation founder sets disjoint. Put every
founder and every declared close relative into `--validation-isolation-samples`, then
exclude that complete protected set from both the Gnomix training sample map and every
chromosome of the calibration Beagle reference.

The simulation generator must emit a schema-v2 pinned manifest, gap-free founder-tract
truth, and one authoritative marker-truth TSV per simulated IID with this exact
nine-column header and literal tab separators:

```text
sim_iid    chrom    marker_index    position_grch38    rsid    hap0_donor_iid    hap0_source_hap    hap1_donor_iid    hap1_source_hap
```

The manifest pins the founder-mosaic generator, its environment lock and code revision,
genetic-map hashes, recombination model, allowed generations, ancestry-fraction tolerance,
and per-autosome breakpoint envelope. It declares minimum founder, simulation, and truth
haplotype-window counts per class and split; the declared founder and simulation minima
must each be at least two. The harness enforces the declared values, including at least
that many distinct same-class founders actually supplying modal window truth. Every
validation stratum must contain all seven classes. Founder classes come from the pinned
gnomAD HGDP+1KG metadata. Treat each global class as an explicit simulation assumption,
not an observed local tract. The harness verifies that tracts
cover each haplotype's full model-marker interval without gaps or overlaps and that
marker donor/source-haplotype identities agree with those tracts. It then independently
projects the marker contributions with the production model's `W = C // M` windows:
regular windows `w = 0, ..., W-2` consume `[w*M, (w+1)*M)`, and the final window consumes
`[(W-1)*M, C)` including the complete remainder. It chooses the modal model code per
haplotype and resolves ties to the lowest numeric production code. A five-column cached
window-truth file is accepted only if recomputation from marker truth matches exactly.

Execute the repository-owned separate verifier that replays the declared source
haplotypes from the pinned donor VCFs, checks marker rsIDs, and confirms every paired
allele against the simulated fixture while reconciling marker contributions with the
authenticated tract truth. Supply the actual generator source and its environment lock.
The verifier requires that source to be a tracked, clean, reviewed file under
`scripts/lai_bundle_v2`, matches its source, environment, and revision to the manifest,
and proves that the verifier has distinct source bytes. Only that repository-owned
reviewed generator is accepted; a source-authentication or replay failure keeps threshold
selection fail-closed rather than permitting an external or self-attesting substitute.

Verification is split-scoped. Write one schema-v2 stamp for `calibration` and, only after
the policy is frozen, a different stamp for `final_confirmation`. Each stamp binds its
exact split commitment, the generator source and environment lock, the verifier and
committed `uv.lock`, source VCFs and indexes, tract files, and exact verified counts. A
stamp for one split cannot authenticate the other.

Keep mode-specific arrays instead of reusing one broad argument list. Put the complete
calibration inputs, `--dataset-split calibration`, and the calibration stamp destination
in `CALIBRATION_VERIFY_ARGS`; add the generator source and environment lock plus every
autosomal donor VCF/index only to the verification command. Put the inputs needed for
full reference hashing, without `--job-plan` or `--max-jobs`, in
`CALIBRATION_REFERENCE_ARGS`. Put the complete calibration inputs and stamp path, the
masks/matrix axes, and `--max-jobs` in `CALIBRATION_PLAN_ARGS`. Use
`CALIBRATION_RUN_ARGS` only for the bundle/code/dataset
identity, labels, selected fixture and window/marker/tract truth, reference manifest and
stamp, masks/VEP snapshot, private work root, and plan path. Create parallel
`FINAL_VERIFY_ARGS`, `FINAL_REFERENCE_ARGS`, `FINAL_PLAN_ARGS`, and `FINAL_RUN_ARGS`
lists for `final_confirmation`; every final-split list also carries the frozen policy and
its independently recorded digest so no final truth is opened before policy freeze. Then
run the calibration stages in order:

Before the calibration plan is created, freeze canonical schema-v1
`selection-design.json` bytes and record their digest independently. The exact fields are
`schema_version`, `policy_id`, `frozen`, `dataset_id`, `bundle_artifact_sha256`,
`simulation_manifest_sha256`, `code_revision`, `endpoints`, `aggregation`,
`stable_region_rule`, `confirmation_matrix`, and
`final_confirmation_split_commitment_sha256`. The six endpoint entries, in canonical
order, are assignment completeness, local diplotype accuracy, best-orientation local
haplotype accuracy, global-ancestry total-variation distance, per-truth-class assignment
completeness, and per-truth-diplotype local diplotype accuracy, with their fixed `>=` or
`<=` operators and non-vacuous preregistered values. Aggregation requires every cell to
pass across the exact six dimensions documented in the calibration contract, without
averaging biological replicates.

The stable-region algorithm is exactly
`predeclared_complete_region_componentwise_minimum_v1`. Its confirmation matrix contains
all three production masks, exact seeds and drop scenarios including `none`, and a
contiguous high-density suffix of at least two calibration fractions ending in `1`. All
stable cells must pass all six endpoints. The policy predicates are the componentwise
minima of the seven telemetry fields documented in the calibration contract, and every
unsafe production-mask row in the complete sweep must be rejected by at least one of
them: the allowed false-accept count is zero. See
[LAI coverage calibration](maintainer/lai-coverage-calibration.md) for the canonical
field order, decimal encoding, authenticated per-autosome truth-window geometry, and full
refusal contract.

Freeze and review `simulation-design.json` before submission. Donor inputs must be
textual VCF or VCF.gz plus their pinned indexes; BCF is not accepted. Run the real
public-donor generation job through SLURM from a clean, full checkout because the
autosomal inputs and output tree are large; a scripts-only rsync lacks the `.git` and
`uv.lock` provenance required by the generator and verifier. Do not place private user
genomic data in this workflow. The command block below is the payload of that allocated
SLURM job or batch script, not a local-machine execution instruction. Before submission,
provision or fast-forward an authenticated full checkout at the exact intended revision
on the cluster and set `YELIZTLI_CHECKOUT` to its root.
After generation, keep the final-confirmation fixture, truth, and label paths sealed from
the calibration operators until the policy is frozen. Any input or design correction
requires regenerating the complete output tree rather than editing generated files.

```bash
set -euo pipefail

# Generate both founder-disjoint splits from one reviewed, frozen design. Run from a
# clean checkout of the exact revision recorded in the manifest. Do not hand-edit output.
: "${YELIZTLI_CHECKOUT:?set YELIZTLI_CHECKOUT to the clean full-checkout root}"
cd "$YELIZTLI_CHECKOUT"
test -z "$(git status --porcelain=v1 --untracked-files=all)"
test -f uv.lock
install -d -m 0700 "$VALIDATION_DIR/coverage"

SIMULATION_GENERATOR_SCRIPT=scripts/lai_bundle_v2/06g_generate_simulation.py
SIMULATION_GENERATOR_ENVIRONMENT_LOCK=uv.lock
SIMULATION_CODE_REVISION="$(git rev-parse HEAD)"
SIMULATION_OUTPUT_DIR="$VALIDATION_DIR/coverage/simulations"

# SIMULATION_OUTPUT_DIR must not exist. Archive a prior tree or choose a fresh path
# before intentionally regenerating; the generator never overwrites an earlier result.
test ! -e "$SIMULATION_OUTPUT_DIR"

uv run --locked python "$SIMULATION_GENERATOR_SCRIPT" \
  --design "$VALIDATION_DIR/coverage/simulation-design.json" \
  --donor-metadata "$VALIDATION_DIR/coverage/donor-metadata.tsv" \
  --relationships "$VALIDATION_DIR/coverage/relationships.tsv" \
  --model-metadata 1="$MODEL_METADATA_CHR1" \
  --model-metadata 2="$MODEL_METADATA_CHR2" \
  --model-metadata 3="$MODEL_METADATA_CHR3" \
  --model-metadata 4="$MODEL_METADATA_CHR4" \
  --model-metadata 5="$MODEL_METADATA_CHR5" \
  --model-metadata 6="$MODEL_METADATA_CHR6" \
  --model-metadata 7="$MODEL_METADATA_CHR7" \
  --model-metadata 8="$MODEL_METADATA_CHR8" \
  --model-metadata 9="$MODEL_METADATA_CHR9" \
  --model-metadata 10="$MODEL_METADATA_CHR10" \
  --model-metadata 11="$MODEL_METADATA_CHR11" \
  --model-metadata 12="$MODEL_METADATA_CHR12" \
  --model-metadata 13="$MODEL_METADATA_CHR13" \
  --model-metadata 14="$MODEL_METADATA_CHR14" \
  --model-metadata 15="$MODEL_METADATA_CHR15" \
  --model-metadata 16="$MODEL_METADATA_CHR16" \
  --model-metadata 17="$MODEL_METADATA_CHR17" \
  --model-metadata 18="$MODEL_METADATA_CHR18" \
  --model-metadata 19="$MODEL_METADATA_CHR19" \
  --model-metadata 20="$MODEL_METADATA_CHR20" \
  --model-metadata 21="$MODEL_METADATA_CHR21" \
  --model-metadata 22="$MODEL_METADATA_CHR22" \
  --genetic-map 1="$GENETIC_MAP_CHR1" \
  --genetic-map 2="$GENETIC_MAP_CHR2" \
  --genetic-map 3="$GENETIC_MAP_CHR3" \
  --genetic-map 4="$GENETIC_MAP_CHR4" \
  --genetic-map 5="$GENETIC_MAP_CHR5" \
  --genetic-map 6="$GENETIC_MAP_CHR6" \
  --genetic-map 7="$GENETIC_MAP_CHR7" \
  --genetic-map 8="$GENETIC_MAP_CHR8" \
  --genetic-map 9="$GENETIC_MAP_CHR9" \
  --genetic-map 10="$GENETIC_MAP_CHR10" \
  --genetic-map 11="$GENETIC_MAP_CHR11" \
  --genetic-map 12="$GENETIC_MAP_CHR12" \
  --genetic-map 13="$GENETIC_MAP_CHR13" \
  --genetic-map 14="$GENETIC_MAP_CHR14" \
  --genetic-map 15="$GENETIC_MAP_CHR15" \
  --genetic-map 16="$GENETIC_MAP_CHR16" \
  --genetic-map 17="$GENETIC_MAP_CHR17" \
  --genetic-map 18="$GENETIC_MAP_CHR18" \
  --genetic-map 19="$GENETIC_MAP_CHR19" \
  --genetic-map 20="$GENETIC_MAP_CHR20" \
  --genetic-map 21="$GENETIC_MAP_CHR21" \
  --genetic-map 22="$GENETIC_MAP_CHR22" \
  --donor-vcf 1="$DONOR_VCF_CHR1" \
  --donor-vcf 2="$DONOR_VCF_CHR2" \
  --donor-vcf 3="$DONOR_VCF_CHR3" \
  --donor-vcf 4="$DONOR_VCF_CHR4" \
  --donor-vcf 5="$DONOR_VCF_CHR5" \
  --donor-vcf 6="$DONOR_VCF_CHR6" \
  --donor-vcf 7="$DONOR_VCF_CHR7" \
  --donor-vcf 8="$DONOR_VCF_CHR8" \
  --donor-vcf 9="$DONOR_VCF_CHR9" \
  --donor-vcf 10="$DONOR_VCF_CHR10" \
  --donor-vcf 11="$DONOR_VCF_CHR11" \
  --donor-vcf 12="$DONOR_VCF_CHR12" \
  --donor-vcf 13="$DONOR_VCF_CHR13" \
  --donor-vcf 14="$DONOR_VCF_CHR14" \
  --donor-vcf 15="$DONOR_VCF_CHR15" \
  --donor-vcf 16="$DONOR_VCF_CHR16" \
  --donor-vcf 17="$DONOR_VCF_CHR17" \
  --donor-vcf 18="$DONOR_VCF_CHR18" \
  --donor-vcf 19="$DONOR_VCF_CHR19" \
  --donor-vcf 20="$DONOR_VCF_CHR20" \
  --donor-vcf 21="$DONOR_VCF_CHR21" \
  --donor-vcf 22="$DONOR_VCF_CHR22" \
  --donor-vcf-index 1="$DONOR_VCF_CHR1_INDEX" \
  --donor-vcf-index 2="$DONOR_VCF_CHR2_INDEX" \
  --donor-vcf-index 3="$DONOR_VCF_CHR3_INDEX" \
  --donor-vcf-index 4="$DONOR_VCF_CHR4_INDEX" \
  --donor-vcf-index 5="$DONOR_VCF_CHR5_INDEX" \
  --donor-vcf-index 6="$DONOR_VCF_CHR6_INDEX" \
  --donor-vcf-index 7="$DONOR_VCF_CHR7_INDEX" \
  --donor-vcf-index 8="$DONOR_VCF_CHR8_INDEX" \
  --donor-vcf-index 9="$DONOR_VCF_CHR9_INDEX" \
  --donor-vcf-index 10="$DONOR_VCF_CHR10_INDEX" \
  --donor-vcf-index 11="$DONOR_VCF_CHR11_INDEX" \
  --donor-vcf-index 12="$DONOR_VCF_CHR12_INDEX" \
  --donor-vcf-index 13="$DONOR_VCF_CHR13_INDEX" \
  --donor-vcf-index 14="$DONOR_VCF_CHR14_INDEX" \
  --donor-vcf-index 15="$DONOR_VCF_CHR15_INDEX" \
  --donor-vcf-index 16="$DONOR_VCF_CHR16_INDEX" \
  --donor-vcf-index 17="$DONOR_VCF_CHR17_INDEX" \
  --donor-vcf-index 18="$DONOR_VCF_CHR18_INDEX" \
  --donor-vcf-index 19="$DONOR_VCF_CHR19_INDEX" \
  --donor-vcf-index 20="$DONOR_VCF_CHR20_INDEX" \
  --donor-vcf-index 21="$DONOR_VCF_CHR21_INDEX" \
  --donor-vcf-index 22="$DONOR_VCF_CHR22_INDEX" \
  --generator-environment-lock "$SIMULATION_GENERATOR_ENVIRONMENT_LOCK" \
  --code-revision "$SIMULATION_CODE_REVISION" \
  --output-dir "$SIMULATION_OUTPUT_DIR"

# The generator writes simulation-manifest.json, per-IID fixture/marker/tract/window
# truth files, and exact split labels at labels/calibration.tsv and
# labels/final_confirmation.tsv. Build each *_VERIFY_ARGS array from the manifest and
# use only the matching label file; do not edit or rename generated artifacts.

# Independently replay calibration donor alleles and write its split-scoped stamp.
# Repeat both donor options for every autosome 1--22.
uv run --locked python scripts/lai_bundle_v2/06g_calibrate_coverage.py \
  "${CALIBRATION_VERIFY_ARGS[@]}" \
  --verify-simulation \
  --simulation-generator-script "$SIMULATION_GENERATOR_SCRIPT" \
  --simulation-generator-environment-lock "$SIMULATION_GENERATOR_ENVIRONMENT_LOCK" \
  --donor-vcf 1="$DONOR_VCF_CHR1" \
  --donor-vcf-index 1="$DONOR_VCF_CHR1_INDEX"

# Full-hash the live calibration reference once and write an atomic stamp.
uv run --locked python scripts/lai_bundle_v2/06g_calibrate_coverage.py \
  "${CALIBRATION_REFERENCE_ARGS[@]}" \
  --verify-reference

# Validate all inputs, bind the preregistered selector, freeze the matrix, and write the
# authenticated calibration plan. Load the expected design digest from a separately
# reviewed release record; do not calculate the expected value inline from the live file.
: "${SELECTION_DESIGN_SHA256:?independently recorded selection-design digest required}"
uv run --locked python scripts/lai_bundle_v2/06g_calibrate_coverage.py \
  "${CALIBRATION_PLAN_ARGS[@]}" \
  --selection-design "$VALIDATION_DIR/coverage/selection-design.json" \
  --expected-selection-design-sha256 "$SELECTION_DESIGN_SHA256" \
  --list-jobs \
  --job-plan "$VALIDATION_DIR/coverage/calibration-job-plan.json" \
  > "$VALIDATION_DIR/coverage/calibration-jobs.jsonl"

# Independently record the plan digest and the configuration_sha256 printed in every row,
# then load that record into CALIBRATION_JOB_PLAN_SHA256 and CALIBRATION_CONFIG_SHA256.
# Results and locks must be named by the canonical unpadded decimal index: N.jsonl and
# .N.jsonl.lock. Normalize a scheduler value before using it as a filename.
install -d -m 0700 "$VALIDATION_DIR/coverage/calibration-records"
JOB_INDEX=$((10#$SLURM_ARRAY_TASK_ID))
uv run --locked python scripts/lai_bundle_v2/06g_calibrate_coverage.py \
  "${CALIBRATION_RUN_ARGS[@]}" \
  --job-plan "$VALIDATION_DIR/coverage/calibration-job-plan.json" \
  --expected-configuration-sha256 "$CALIBRATION_CONFIG_SHA256" \
  --job-index "$JOB_INDEX" \
  --output "$VALIDATION_DIR/coverage/calibration-records/${JOB_INDEX}.jsonl"

# Run only after every planned result and its harness-created lock exists. The selector
# output directory must not exist. A selected policy's digest is printed on stdout.
: "${CALIBRATION_JOB_PLAN_SHA256:?independently recorded plan digest required}"
test ! -e "$VALIDATION_DIR/coverage/selection"
uv run --locked python scripts/lai_bundle_v2/06h_select_coverage.py select \
  --selection-design "$VALIDATION_DIR/coverage/selection-design.json" \
  --expected-selection-design-sha256 "$SELECTION_DESIGN_SHA256" \
  --job-plan "$VALIDATION_DIR/coverage/calibration-job-plan.json" \
  --expected-job-plan-sha256 "$CALIBRATION_JOB_PLAN_SHA256" \
  --expected-configuration-sha256 "$CALIBRATION_CONFIG_SHA256" \
  --observations-dir "$VALIDATION_DIR/coverage/calibration-records" \
  --output-dir "$VALIDATION_DIR/coverage/selection"

# Stop here and independently record the printed policy digest. Resume only after select
# exits 0 and both selection-report.json and confirmation-policy.json exist.
: "${CONFIRMATION_POLICY_SHA256:?independently recorded policy digest required}"

# Now, and only now, open and independently replay the sealed final split. FINAL_*_ARGS
# all include the frozen policy path and expected policy digest. Repeat donor inputs for
# autosomes 1--22 exactly as in calibration verification.
uv run --locked python scripts/lai_bundle_v2/06g_calibrate_coverage.py \
  "${FINAL_VERIFY_ARGS[@]}" \
  --verify-simulation \
  --simulation-generator-script "$SIMULATION_GENERATOR_SCRIPT" \
  --simulation-generator-environment-lock "$SIMULATION_GENERATOR_ENVIRONMENT_LOCK" \
  --donor-vcf 1="$DONOR_VCF_CHR1" \
  --donor-vcf-index 1="$DONOR_VCF_CHR1_INDEX"

# Final planning derives every matrix axis from the policy; do not pass fraction, seed,
# or drop-scenario overrides.
uv run --locked python scripts/lai_bundle_v2/06g_calibrate_coverage.py \
  "${FINAL_PLAN_ARGS[@]}" \
  --list-jobs \
  --job-plan "$VALIDATION_DIR/coverage/final-job-plan.json" \
  > "$VALIDATION_DIR/coverage/final-jobs.jsonl"

# Independently record the final plan and configuration digests before array execution.
install -d -m 0700 "$VALIDATION_DIR/coverage/final-records"
JOB_INDEX=$((10#$SLURM_ARRAY_TASK_ID))
uv run --locked python scripts/lai_bundle_v2/06g_calibrate_coverage.py \
  "${FINAL_RUN_ARGS[@]}" \
  --job-plan "$VALIDATION_DIR/coverage/final-job-plan.json" \
  --expected-configuration-sha256 "$FINAL_CONFIG_SHA256" \
  --job-index "$JOB_INDEX" \
  --output "$VALIDATION_DIR/coverage/final-records/${JOB_INDEX}.jsonl"

# One-shot final evaluator. It replays the calibration selection lineage before reading
# the exact final matrix. The report path must not exist.
: "${FINAL_JOB_PLAN_SHA256:?independently recorded final-plan digest required}"
test ! -e "$VALIDATION_DIR/coverage/final-confirmation-report.json"
uv run --locked python scripts/lai_bundle_v2/06h_select_coverage.py confirm \
  --confirmation-policy "$VALIDATION_DIR/coverage/selection/confirmation-policy.json" \
  --expected-confirmation-policy-sha256 "$CONFIRMATION_POLICY_SHA256" \
  --dataset-id "$DATASET_ID" \
  --bundle-artifact-sha256 "$BUNDLE_ARTIFACT_SHA256" \
  --simulation-manifest-sha256 "$SIMULATION_MANIFEST_SHA256" \
  --code-revision "$SIMULATION_CODE_REVISION" \
  --final-confirmation-split-commitment-sha256 "$FINAL_SPLIT_COMMITMENT_SHA256" \
  --selection-design "$VALIDATION_DIR/coverage/selection-design.json" \
  --expected-selection-design-sha256 "$SELECTION_DESIGN_SHA256" \
  --calibration-job-plan "$VALIDATION_DIR/coverage/calibration-job-plan.json" \
  --expected-calibration-job-plan-sha256 "$CALIBRATION_JOB_PLAN_SHA256" \
  --expected-calibration-configuration-sha256 "$CALIBRATION_CONFIG_SHA256" \
  --calibration-observations-dir "$VALIDATION_DIR/coverage/calibration-records" \
  --selection-report "$VALIDATION_DIR/coverage/selection/selection-report.json" \
  --final-job-plan "$VALIDATION_DIR/coverage/final-job-plan.json" \
  --expected-final-job-plan-sha256 "$FINAL_JOB_PLAN_SHA256" \
  --expected-final-configuration-sha256 "$FINAL_CONFIG_SHA256" \
  --final-observations-dir "$VALIDATION_DIR/coverage/final-records" \
  --output "$VALIDATION_DIR/coverage/final-confirmation-report.json"
```

The final verification command must write a separate path and use
`--dataset-split final_confirmation`; do not copy, rename, or reuse the calibration stamp.

The simulation-verification pass independently checks exact alleles. The reference pass
full-hashes the calibration bundle and validates VCF sample headers. Planning authenticates
the selected split's simulation stamp and the reference stamp and uses descriptor-pinned
stat fingerprints to detect drift. The schema-v3 planner stores Cartesian axes once,
streams domain-separated Merkle levels and immutable shards through bounded memory, and
checks the plan's peak disk estimate. Each array task reconstructs and authenticates only
its selected row, fixture, and required mask files. An attempt-unique subdirectory under
an operator-owned `0700` work root prevents cross-task cleanup collisions. Cooperative
bundle and per-output locks prevent concurrent mutation or last-writer-wins results;
paths that alias inputs, the bundle, or plan shards are rejected. Array tasks do not
reread a live simulation stamp:
the frozen plan pins its hash and split commitment, and each selected Merkle proof
authenticates that commitment transitively. The reference stamp is checked again after
inference and before atomic output commit. If a stamp, the plan, configuration hash,
Merkle proof, runtime lock, or a fingerprint disagrees with the input state applicable to
its phase, rebuild the appropriate artifact rather than bypassing the check.

Choose a policy using only the calibration split. `select` requires exactly one canonical
result and sibling lock for every plan index, authenticates the design, plan, shards,
configuration, rows, and provenance, and performs two deterministic passes over the
archive. Every stable cell must be eligible and pass all six endpoints. The selector takes
componentwise minima over `emitted_markers.total`,
`model_markers.aggregate.matched`, `model_markers.aggregate.match_rate`,
`phased_autosomes.count`, `analyzed_autosomes.count`,
`haplotype_windows.valid_assigned`, and `haplotype_windows.assignment_rate`, then requires
zero false accepts among all unsafe production-mask calibration rows. It freezes the
calibration-plan and observation hashes, selection code/report hashes, accuracy endpoints,
telemetry predicates, fail-closed aggregation, and exact confirmation matrix in the
policy committed to the still-sealed final-split identity.
The report also records raw simulation-IID and validation-stratum counts and explicitly
marks a biological confidence interval as not estimable from the dependent simulation
sweep; never reinterpret seeds, fractions, masks, windows, or loss scenarios as
independent biological replicates.

`select` exits `0` only when it atomically publishes both `selection-report.json` and
`confirmation-policy.json`. A complete but scientifically unsafe sweep exits `2`, writes
only a deterministic refusal report, and creates no policy. Authentication, canonicality,
completeness, or input-drift errors also exit `2`, but do not publish a scientific refusal
artifact. Never bypass or reinterpret either result. Record a selected artifact's digest
independently and pass it as `--expected-confirmation-policy-sha256` during final
verification, planning, and array execution.

Final-confirmation planning derives its three supported masks, fractions, seeds, and
chromosome-drop scenarios only from that policy and rejects all CLI matrix overrides.
`confirm` independently authenticates and recomputes the complete calibration lineage,
then evaluates the frozen policy once on every founder-disjoint `final_confirmation` cell.
Exit `0` is the only pass. Exit `2` with a report is a scientific confirmation failure;
exit `2` without one is an input or lineage failure. Do not retune after opening the final
split. Any failure requires a new calibration cycle, new preregistered design, and newly
generated sealed confirmation set.

The current candidate panel cannot reach policy selection: the dated 2026-07-12 audit
retained one eligible AMR founder and zero OCE founders, while the executable plan requires
every one of the seven classes. Until a fully hashed audit and eligible founder-disjoint
panel replace that state, the planner must refuse and no positive policy may be issued;
production remains fail-closed.

The application enforces that state independently of the calibration scripts. With bundle
and Java prerequisites installed, LAI status returns the structured reason
`lai_coverage_policy_unavailable`; triggers are refused before enqueue, legacy results are
withheld, and Tier 1 NNLS/PCA ancestry remains available. Do not add a local threshold,
feature flag, settings override, or bundle-version inference to bypass this gate.

After a future policy passes final confirmation, cutting the data release is not enough.
Open a separate production-enforcement PR that authenticates the frozen policy and final
report, evaluates all seven minimum predicates, records the policy identity on accepted
rows, proves equality-at-boundary acceptance and below-boundary rejection, and keeps
unqualified historical rows quarantined. Only that reviewed release may change production
from the structured no-call state.

---

## 9. Cut the GitHub Release

After bio-validator clears the rebuild, push the tarball as a draft
release. The tarball lives in `$WORKDIR` (see Phase 7).

```bash
gh release create lai-bundle-v2.0.0 \
  --repo bioedca/Yeliztli \
  --title "LAI bundle v2.0.0" \
  --notes-file docs/release-notes/lai-bundle-v2.0.0.md \
  --draft \
  "$WORKDIR/genomeinsight_lai_bundle_v2.0.0.tar.gz"
```

Release notes should mirror `metadata.json`: catalog source (union 23andMe
v5 + AncestryDNA v2.0), site count, accuracy metrics, build date, SHA-256,
the pinned Gnomix commit/effective-config SHA-256, and the `min_app_version`
floor (`0.2.0` for v2.0.0).

The tarball is ≥500 MB on every release ≥ v2.0.0 (~700–750 MB at v2.0.0),
so it cannot live on `raw.githubusercontent.com`. Every release ≥ v2.0.0
ships as a GitHub Release asset.

---

## 10. Update `bundles/manifest.json`

```json
"lai_bundle": {
  "version": "v2.0.0",
  "build_date": "YYYY-MM-DD",
  "url": "https://github.com/bioedca/Yeliztli/releases/download/lai-bundle-v2.0.0/genomeinsight_lai_bundle_v2.0.0.tar.gz",
  "sha256": "<64-hex from .sha256 sidecar>",
  "size_bytes": <bytes from stat on the tarball>,
  "min_app_version": "0.2.0"
}
```

Normalize any prior pre-semver fields for clean `packaging.version.Version`
compares — e.g., `"v1.1"` → `"v1.1.0"` on the historical entry. The
manifest's `version` is the contract; the bundle's internal
`metadata.json::bundle_version` is informational.

In the same PR (PR-0c per Plan §18.1), bump
`backend/db/database_registry.py::DATABASES["lai_bundle"]` to the new
`expected_size_bytes` and the new asset URL.

---

## 11. Soft staleness gate (post-publish behaviour)

Per Plan §6.7, the LAI endpoint runs against any AncestryDNA-sourced
sample (or merged sample carrying AncestryDNA contribution) at the
**installed** bundle version. When `lai_bundle.version < v2.0.0`, the
endpoint returns HTTP 200 with `degraded_coverage: true`; the frontend
renders a dismissible banner ("LAI coverage degraded for AncestryDNA —
update bundle to v2.0.0 for full chromosome painting").

23andMe-only samples never carry the flag and never trigger the banner.
This is locked by `test_lai_runner_ancestrydna.py` (Plan §13.1
LAI-00e item ii negative case).

---

## 12. Post-release smoke test

```bash
conda activate GI
python -c "
from backend.db.manifest import fetch_manifest
m = fetch_manifest()
entry = m.bundles['lai_bundle']
print(entry.version, entry.url, entry.sha256, entry.size_bytes, entry.min_app_version)
assert entry.version == 'v2.0.0'
assert entry.min_app_version == '0.2.0'
"
```

If the manifest fetch fails (network, JSON parse), investigate before
announcing the release.

---

## 13. Rollback

Releases on the `lai-bundle-v*` stream are immutable — older tags stay
alive indefinitely so older app versions keep downloading them
(Plan §2.1). Rollback is performed by reverting the `bundles/manifest.json`
change in a new PR, which repoints the manifest at the prior release tag
(`lai-bundle-v1.1.0`). Do not delete the broken release; instead, edit the
release notes to mark it superseded.

If the broken release is already in the wild on installed apps, the next
manifest update repoints them on the manifest's 1 h TTL refresh
(`backend/db/manifest.py::fetch_manifest`).

---

## 14. PR sequence (Plan §18.1)

PR-0c is independent of PR-0a / PR-0b and can interleave. The full
sequence for the v2.0.0 ship is:

- Step 20 — port cluster scripts into `scripts/lai_bundle_v2/` (this PR's
  scope) + this runbook.
- Step 21 — out-of-repo cluster rebuild produces the tarball; manifest +
  `database_registry.py` updated to v2.0.0.
- Steps 22–25a — LAI runner per-source telemetry, soft staleness gate,
  frontend coverage surface, E2E test, slow-tier real-bundle accuracy.

The cluster rebuild and the release-cut are sequenced via this runbook;
the in-repo PRs are sequenced via Plan §18.1.

---

## References

[1] Hilmarsson, H., Kumar, A. S., Rastogi, R., Bustamante, C. D., Mas
Montserrat, D., & Ioannidis, A. G. (2021). [High Resolution Ancestry
Deconvolution for Next Generation Genomic
Data](https://doi.org/10.1101/2021.09.19.460980). *bioRxiv* (preprint, version
1). DOI: 10.1101/2021.09.19.460980.
