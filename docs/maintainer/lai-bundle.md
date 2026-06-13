# LAI bundle

The **local-ancestry-inference (LAI)** bundle powers the optional Tier-2 ancestry "chromosome
painting." It is the heaviest artifact to build — a multi-phase, multi-hour pipeline best run
on a compute cluster.

!!! info "The authoritative procedure lives in the repo"
    The complete, step-by-step operator runbook — including exact host conventions, accuracy
    sign-off gates, and the publish/rollback steps — is
    [`docs/lai-bundle-release-runbook.md`](https://github.com/bioedca/Yeliztli/blob/main/docs/lai-bundle-release-runbook.md),
    with its environment lock in
    [`docs/lai-bundle-release-runbook-env.lock.yaml`](https://github.com/bioedca/Yeliztli/blob/main/docs/lai-bundle-release-runbook-env.lock.yaml).
    This page is an orientation summary.

## What's in the bundle

A tarball containing the per-chromosome local-ancestry models, the phasing reference panel
(Beagle), genetic maps, the liftover chain, and a `metadata.json` carrying the bundle version
and tool versions. It is published as a GitHub release asset and pinned in
[`bundles/manifest.json`](https://github.com/bioedca/Yeliztli/blob/main/bundles/manifest.json).

## The build pipeline

The build is a sequence of idempotent phases under
[`scripts/lai_bundle_v2/`](https://github.com/bioedca/Yeliztli/tree/main/scripts/lai_bundle_v2),
orchestrated by `run_rebuild.sh` (sequential) or `run_rebuild_slurm.sh` (cluster):

| Phase | Does |
|-------|------|
| 01 | Download the phased reference panel (gnomAD HGDP + 1KG) and genetic maps |
| 02 | Lift the union catalog GRCh37 → GRCh38 and build the sites/regions files |
| 03 | Subset the reference panel to the union sites |
| 04 | ADMIXTURE-filter to a single-ancestry reference sample map |
| 05 | Train the per-chromosome ancestry models |
| 06 | Validate phasing and local-ancestry accuracy against held-out samples |
| 07 | Assemble the tarball, write `metadata.json`, and emit checksums |

All paths and parameters are set via
[`scripts/lai_bundle_v2/env.sh`](https://github.com/bioedca/Yeliztli/blob/main/scripts/lai_bundle_v2/env.sh)
so the build is cluster-portable.

## Running on a cluster (SLURM)

Heavy phases run as a small SLURM DAG: a *prep* job (phases 02–04), a *train* job that
**parallelises phase 05 across the 22 autosomes** as a job array, and a *finish* job
(phases 06–07). The standard pattern is:

1. `rsync` the `scripts/lai_bundle_v2/` directory to the cluster working directory.
2. On the login node, activate the build environment and submit `run_rebuild_slurm.sh`.
3. Build in node-local scratch; copy only the final tarball back.

Partition, CPU, and array sizing are tunable via environment variables (see the runbook).

## Validation gate

The bundle is only published once it clears the accuracy sign-off in the runbook (mean
per-window local-ancestry accuracy and phasing switch-error thresholds, plus held-out
per-superpopulation checks). Then it follows the same draft → verify → publish flow as the
[other bundles](index.md), using the `bundle-release.yml` workflow with `bundle_key=lai_bundle`.
