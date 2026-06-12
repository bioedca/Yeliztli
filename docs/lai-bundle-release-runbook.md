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
  - Gnomix git commit SHA recorded
  - `fastmixture --version` (or `admixture --version`) + the locked random seed
    (`scripts/lai_bundle_v2/env.sh::ADMIXTURE_SEED` defaults to `42`).
- ~500 GB scratch on `$WORKDIR`.
- `gh` CLI authenticated against the Yeliztli repo with `repo` scope.

The orchestrator script provisions the directory layout on first run; no
manual `mkdir` is needed.

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

UNION_CATALOG_TSV="$LAI_WORKDIR/00_raw_downloads/union_sites.tsv" \
WORKDIR="$LAI_WORKDIR" \
LAI_BUNDLE_VERSION=v2.0.0 \
  bash scripts/run_rebuild.sh
```

To resume from a single phase (e.g., re-train Gnomix only):

```bash
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

Phases 02 and 03 are the only steps that differ from the v1.1 build — they
now operate on the union catalog (~2.0M sites; ~1.94M autosomal) instead of the
23andMe v5 catalog (~605k). Phases 04–07 are byte-identical to v1.1
provided the random seed (`ADMIXTURE_SEED=42`) is unchanged (Plan §6.3
step 4 — the runbook asserts this before publication).

**Phase 05 runs gnomix in its own conda env.** gnomix needs `sklearn_crfsuite`/
`xgboost`, which the `lai_bundle` env lacks; `05_train_gnomix.sh` invokes it via
`conda run -n $GNOMIX_ENV` (default `gnomix`), so the rest of the pipeline stays
in `lai_bundle`. **Phase 06 needs the 1000G pedigree:** place
`20130606_g1k.ped` at `$WORKDIR/06_validation/` (or set `G1K_PED`).

### 6a. SLURM submission (parallel — recommended for the full run)

`run_rebuild_slurm.sh` submits the rebuild as a 3-job SLURM DAG chained by
`afterok` dependencies, with **phase 05 (gnomix training, the long pole) as a
per-chromosome job array** so ~22 chromosomes train concurrently instead of
sequentially:

```bash
ssh "$LAI_BUILD_HOST"
conda activate lai_bundle           # submitter env; jobs re-source conda
UNION_CATALOG_TSV="$LAI_WORKDIR/00_raw_downloads/union_sites.tsv" \
WORKDIR="$LAI_WORKDIR" \
G1K_PED="$LAI_WORKDIR/06_validation/20130606_g1k.ped" \
  bash "$LAI_WORKDIR/scripts/run_rebuild_slurm.sh"
#   prep   (02 03 04)  -> job N
#   gnomix (05 array)  -> job N+1  (after N)
#   finish (06 07)     -> job N+2  (after N+1)
# Watch: squeue -j N,N+1,N+2 ; logs under $LAI_WORKDIR/logs/
```

Tunables: `SLURM_PARTITION` (cluster partition name; defaults to `gpu` in
`run_rebuild_slurm.sh`),
`GNOMIX_CPUS` (cores per chromosome; also caps gnomix `n_cores`), `GNOMIX_ARRAY`
(e.g. `1-22%11` to throttle concurrency), `CONDA_SH`, `CONDA_ENV`, `GNOMIX_ENV`.
The array parallelizes phase 05 from ~4–12 h sequential down to roughly the
slowest single chromosome (× the number of waves once cores are saturated).

---

## 7. Source data provenance

For every input artifact (gnomAD HGDP+1KG BCFs, liftover chain, 1000G
genetic map, ADMIXTURE binary, Gnomix release tag), record in
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
- **Held-out per-superpopulation inference accuracy (gold-standard — REQUIRED):**
  The mean per-window accuracy above is *blind to per-population balance*. The
  first v2.0.0 LAI bundle reported **0.97** yet misclassified *every* European —
  the old `04c` `max_q >= 0.95` single-ancestry filter left **EUR = 3** samples
  in training (continentally-intermediate groups never form a clean ADMIXTURE
  component), so a held-out Iberian classified as 94% CSA / 0.3% EUR through the
  production pipeline. Therefore, before publishing, run a held-out
  per-superpopulation **inference** check: hold a few samples per superpopulation
  OUT of the gnomix training panel, build AncestryDNA-density fixtures from the
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
  regression; this inference check is the *runtime proof*.
- **Phasing accuracy**: mean switch error rate ≤ 0.0566 vs. trio-truth
  haplotypes (Plan §6.4). Written by `06d_phasing_accuracy.py` to
  `$VALIDATION_DIR/phasing_accuracy_report.json`.
- **23andMe parity**: the LAI runner produces byte-identical output on
  legacy 23andMe v5 sample DBs against the new bundle (locked by
  `tests/backend/test_lai_runner_telemetry_parity.py` — see Plan §6.6).
- **Re-runnability**: re-running with `ADMIXTURE_SEED=42` reproduces the
  Phase 4 sample map bit-for-bit on the same input (Plan §6.3 step 4).

Drift below targets → blocker ticket, do not publish (Plan §12.2 Validation
gates). Sign-off attaches to the PR as a comment along with both report
JSONs.

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
and the `min_app_version` floor (`0.2.0` for v2.0.0).

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
