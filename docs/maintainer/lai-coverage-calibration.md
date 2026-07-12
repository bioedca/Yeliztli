# LAI coverage calibration

Yeliztli does not treat any positive marker count as scientifically adequate for local
ancestry inference. It also does not borrow a universal minimum from the literature:
marker density, genomic distribution, phasing, reference-panel composition, ancestry,
and model windowing interact, and the available studies do not establish a portable
consumer-array cutoff.[1][][2][][3][][4][]

The production threshold must therefore be calibrated against the exact production
Gnomix models and privacy-safe, site-only masks for every supported input shape:

- 23andMe v5;
- AncestryDNA v2;
- the source-aware union used for merged samples.

This calibration is a leak-free simulation study. Public donor haplotypes supply the
founders for simulated genomes. Their pinned cohort-wide classes are explicit simulation
assumptions; they are **not** observed local-ancestry tracts.

## Validation contract

The current public cohort initially offers 36 1000 Genomes/HGDP candidates: five in each
of AFR, AMR, CSA, EAS, MID, and OCE, plus six EUR candidates. That list is not an eligible
validation panel. A read-only exploratory audit on 2026-07-12 found only one AMR candidate
and no OCE candidate after model-training and declared-relative exclusions. The audit used
gnomAD metadata SHA-256
`e18e7a29d0567b8063edc1a714bd31e57dc422823ba51af2c63fecef0dbc3cf1` and LAI release
artifact SHA-256
`36abb5f2ed95011aff1227c894f52597ef5c31adb5a132fafdf0830eabf14bff`, but its remaining
filter-input hashes were not preserved. Treat those counts as a dated diagnostic, not a
durable release gate. Before calibration, save a reproducible candidate-audit artifact
with every input hash. Independently, the harness refuses either split unless all seven
model classes are present. Do not drop a class, reuse a training or phasing sample, or
choose a threshold from an incomplete panel.

Before a calibration run, define a protected IID set containing every validation donor
and every declared close relative of those donors. Enforce both isolation boundaries:

1. every protected IID is absent from the Gnomix training sample map; and
2. every protected IID is removed from every chromosome of the calibration Beagle
   phasing reference.

The calibration reference is a protected-set-excluded derivative of the candidate
production reference. Freeze the candidate Gnomix models before generating any
calibration genome so that the experiment measures the model that will ship. If the
current model has no eligible holdout for a required class, retrain a candidate model
around pre-reserved founders and then freeze it; never tune a model after exposing the
calibration split. Simulated fixture IIDs must also be absent from both the training
sample map and the calibration phasing panels.

The schema-v2 simulation manifest must predeclare separate `calibration` and
`final_confirmation` splits whose contributing founder-IID sets are disjoint. Its
`simulation_protocol.minimums` declares founder, simulation, and truth haplotype-window
counts per class and split; the founder and simulation values must each be at least two.
The harness enforces the declared values, not merely those floors. Founder coverage is
measured twice: declared contributors and distinct same-class founders that actually
supply the modal ancestry truth for evaluated windows must both meet the declared founder
minimum. Every validation stratum must contain all seven classes, and the simulations
must exercise every predeclared generation. Use only the calibration split to select the
policy. Keep the final-confirmation split sealed until the threshold, coverage predicates,
accuracy endpoints, aggregation rule, and exact confirmation matrix are frozen; a founder
used in one split must never contribute a haplotype to the other.

## Marker-level truth and Gnomix projection

Generate validation genomes from isolated donor haplotypes and preserve the exact donor
and source-haplotype contribution for every production-model marker. The authoritative
marker-truth TSV has exactly these nine columns, in this order, separated by literal tab
characters:

```text
sim_iid    chrom    marker_index    position_grch38    rsid    hap0_donor_iid    hap0_source_hap    hap1_donor_iid    hap1_source_hap
```

Rows must cover every model marker on autosomes 1--22 exactly once, in chromosome and
zero-based `marker_index` order. `position_grch38` must equal the corresponding
`metadata.npz::snp_pos` value; donor IIDs must be declared founders; and each source
haplotype must be `0` or `1`.

The marker rows must be derived from a biologically explicit founder mosaic rather than
allowing the donor identity to switch independently at every marker. The manifest pins a
single-pulse, genetic-map Poisson recombination protocol, its per-autosome map hashes,
allowed generations, ancestry-fraction targets and tolerances, and a maximum breakpoint
envelope. Preserve gap-free, half-open founder tracts in a second TSV with the exact
columns `sim_iid`, `chrom`, `haplotype`, `start_marker_index`,
`end_marker_index_exclusive`, `donor_iid`, and `source_hap`. Each haplotype must cover
model markers `[0, C)` exactly, without gaps, overlaps, adjacent duplicate tracts, or an
undeclared/excess breakpoint. The planner verifies that every marker-truth donor and
source haplotype agrees with the tract that covers that marker and that realized ancestry
fractions stay inside the predeclared tolerance.

Marker and tract fields alone are not allele-level proof. Before planning, execute the
repository-owned independent verifier with `--verify-simulation`. It streams every
declared donor/source-haplotype allele from the pinned donor VCFs, confirms each marker
rsID, reconciles every marker contribution with the authenticated tract file, and proves
that the paired alleles equal the simulated fixture genotype. Supply the actual generator
source with `--simulation-generator-script` and its lock with
`--simulation-generator-environment-lock`. The verifier requires a tracked, clean,
reviewed generator under `scripts/lai_bundle_v2`, authenticates the manifest's source,
environment, and repository revision, and proves that its own script hash is distinct.
If that repository-owned reviewed generator is not yet available, do not substitute an
external generator or accept self-attestation: fixture generation and positive-threshold
selection remain fail-closed.

Run verification separately for `--dataset-split calibration` and, after policy freeze,
`--dataset-split final_confirmation`. Each invocation writes a distinct atomic schema-v2
`--simulation-verification-stamp` bound to exactly that split commitment. The stamp also
binds the code revision, verifier script and `uv.lock`, generator source and environment
lock, source VCF and index hashes, declared model identity, per-fixture and tract hashes,
and exact verified row counts. A calibration stamp cannot authenticate final confirmation
or vice versa. The harness separately checks every marker position and model denominator
against the live pinned model metadata. It rejects a missing, incomplete, stale,
wrong-split, or self-attested stamp.

Resolve each founder to one canonical production-model code from the pinned gnomAD
HGDP+1KG metadata after enforcing release, hard-filter, and relatedness fields. This
global donor class is a documented founder-label assumption used to generate simulated
truth. It is not evidence that the real donor carries that ancestry at every local
position, so results describe performance under the declared simulation model rather
than biological ground truth for those people.

Project marker contributions to windows independently using the production Gnomix
semantics. For chromosome marker count `C`, model window size `M`, and `W = C // M`:

- each regular window `w = 0, ..., W-2` uses marker indices
  `[w*M, (w+1)*M)`;
- the final window uses `[(W-1)*M, C)`, including the complete remainder through marker
  `C-1`; and
- each haplotype receives the modal founder ancestry code in the window, with an exact
  tie resolved to the lowest numeric code in the pinned production-model order.

The five-column `chrom/start/end/hap0/hap1` window file is only a cache. At planning
time, recompute it from the authoritative marker truth and the pinned model metadata and
reject the dataset if even one cached window differs. The validation-stratum label is for
stratified reporting only; it is not a substitute for the two-haplotype truth.
Simulation-based evaluation with separate truth and reference samples follows the
validation pattern used in local-ancestry benchmarking.[2][]

Do not use installed user samples, raw uploads, or a union-site fixture mislabeled as a
vendor-specific array. Repeated marker-removal seeds measure sensitivity to site loss;
they do not create new biological replicates.

## Provenance and isolation manifest

Every calibration dataset needs an isolation-set-excluded reference manifest that
anchors:

- the SHA-256 of the source production LAI artifact;
- the complete set of excluded donor and declared-relative IIDs;
- each chromosome's phasing VCF and index SHA-256 and byte size;
- each chromosome's sample count and sample-ID-set SHA-256; and
- the SHA-256 and byte size of inherited bundle files used by phasing, model inference,
  and truth projection.

The manifest also names the one production liftover mapping resolved for the run. If both
supported mapping filenames exist, the named file must be the one production resolves;
the harness rejects an unpinned preferred file.

Run the harness once with `--verify-reference` after assembling the calibration bundle.
That pass full-hashes the live phasing panels, indexes, and inherited files, verifies the
VCF sample headers and isolation claims, and atomically writes the declared
`--reference-verification-stamp`. Planning and array tasks then authenticate that stamp
and use its size/mtime/ctime fingerprints as a cheap drift check instead of re-hashing
the complete reference for every cell. Any fingerprint or manifest drift invalidates the
stamp; repeat the full verification rather than editing the stamp.

Also record hashes for the bundle metadata, donor manifest, donor metadata, relationship
graph, simulation manifest, both split-scoped independent simulation-verification stamps,
generator source and environment lock, Gnomix training sample map, simulated fixtures,
marker-truth and cached-window files, privacy-safe site masks, the pinned production-VEP
rsID membership snapshot, harness script, configuration, and code revision. The resulting
report must be sufficient to identify the exact inputs and reconstruct the deterministic
job matrix.

Run `uv lock --check` before submission and invoke the harness from the committed
environment with `uv run --locked`. The harness requires `backend/`, `pyproject.toml`, and
`uv.lock` to be tracked and clean at the declared Git revision, and records the resolved
Python/package versions, Java identity, and lock digest in every plan and result.

## Planning and array execution

Planning and inference are separate phases. Keep separate mode-specific argument arrays:
`CALIBRATION_VERIFY_ARGS`, `CALIBRATION_REFERENCE_ARGS`, `CALIBRATION_PLAN_ARGS`, and
`CALIBRATION_RUN_ARGS` must select `calibration` and its own
`--simulation-verification-stamp` path, while the corresponding `FINAL_*_ARGS` must select
`final_confirmation` and a different stamp path. Reference arrays must omit planner-only
`--job-plan` and `--max-jobs`. Simulation verification alone receives
`--simulation-generator-script`, `--simulation-generator-environment-lock`, and every
autosomal donor VCF/index. Prefix every harness invocation with `uv run --locked python`
and then the `scripts/lai_bundle_v2/06g_calibrate_coverage.py` path.

Every `FINAL_*_ARGS` array must include the frozen `--confirmation-policy` and its
independently recorded `--expected-confirmation-policy-sha256`. Both the wrapper and the
direct verifier authenticate that binding before opening any final fixture or truth file.

Invoke `--list-jobs` with all declared fixtures, truth files, both required vendor masks,
a one-column VEP rsID-membership
snapshot, and the required `--job-plan` path. The VEP snapshot reproduces production's
same-coordinate alias tiebreaker; one rsID at multiple GRCh37 coordinates is rejected.
The planner validates the complete dataset, recomputes window truth, and writes a
schema-v3 authenticated plan plus immutable per-job Merkle shards. Matrix axes are stored
once and rows are generated arithmetically; Merkle levels and shards are streamed through
bounded memory. `--max-jobs` imposes an explicit cardinality ceiling, and planning checks
the peak disk estimate before publishing the plan commit point. Each printed JSON job row
carries the configuration SHA-256 that must be supplied later as
`--expected-configuration-sha256`.

SLURM array tasks must run from that frozen plan with `--job-index`, `--job-plan`, the
expected configuration hash, and a task-specific `--output`. A task reparses only the
selected fixture and cached window truth, fingerprint-checks its already validated marker
truth, and reads only the site-mask file or files needed for that selected scenario. It
must not reparse every fixture or every unrelated mask in the matrix. The plan's
fingerprints reject drift between planning and execution, while the selected Merkle proof
authenticates one job without loading the full Cartesian matrix. Planning authenticates
the selected split's live simulation stamp and commits its hash and split commitment into
the plan. Array tasks intentionally do not reread that stamp; they trust it transitively
through the authenticated plan and selected Merkle proof. Run mode rejects
planner-only generator, donor, fraction, seed, and drop-scenario options. Each invocation
uses an operator-owned `0700` scratch root and an attempt-unique subdirectory; the
reference stamp is rechecked after inference before output is committed.

The harness holds a shared cooperative lock on the calibration bundle through planning or
inference and an exclusive lock during reference verification. Every task also takes a
nonblocking sibling lock for its result path. Outputs may not alias any input by path,
symlink, or inode, may not live inside the bundle or shard directory, and are written by
fsync plus atomic rename. A same-job retry may reuse its own output identity; replacing a
different job requires explicit `--overwrite-output`.

## Experimental design

Predeclare the full Cartesian matrix of simulated fixture, input shape, retained-marker
fraction, structured chromosome-loss scenario, and seed. For each matrix cell:

1. apply the supplied vendor-mask membership and record whether the mask is
   vendor-published, derived from a build artifact, or an empirical union of observed
   exports;
2. generate deterministic, chromosome-balanced, nested marker subsets, so every lower
   fraction is a subset of the corresponding higher fraction;
3. apply a structured chromosome-loss scenario to the already selected subset, making
   the loss cell the exact no-loss cell minus the declared chromosome markers; and
4. run the normal production phasing and Gnomix inference path with coverage enforcement
   disabled only for this diagnostic harness.

Marker count alone cannot represent genomic distribution: losing chromosome 1 and losing
chromosome 21 are not equivalent. Keep chromosome-loss scenarios separate from the
retained-marker fractions rather than allowing a random inter-chromosome imbalance to
stand in for distribution.[1][][2][]

The harness must validate the complete coverage telemetry schema before it accepts a
successful result. Aggregate and per-autosome emitted markers, model-marker denominators,
phased/analyzed chromosome sets, and expected/assigned window counts must agree with one
another and with the explicit truth-window denominator. Unreadable or inconsistent
telemetry makes the row ineligible; a silently reduced denominator must never make sparse
coverage look better.

## Truth-based metrics

Score every expected truth window, including windows omitted from the production result.
An omitted window is incorrect for both local-accuracy measures and lowers assignment
completeness. Record at least:

- **local diplotype accuracy**: the fraction of expected windows whose unordered pair of
  predicted ancestries matches the unordered truth pair;
- **local haplotype accuracy, best chromosome orientation**: the fraction of expected
  haplotype calls correct after choosing one global haplotype orientation per chromosome,
  never a separate orientation per window;
- **assignment completeness**: returned truth windows divided by all expected truth
  windows; and
- **global ancestry total-variation distance**: the total-variation distance between the
  predicted and truth ancestry-fraction vectors.

The unordered diplotype measure does not penalize a harmless whole-window haplotype swap.
The single best orientation per chromosome permits an arbitrary chromosome-wide phase
orientation while still penalizing within-chromosome switch errors. Report all metrics by
validation stratum and input shape, including the worst stratum; a pooled mean can hide
stratum-specific degradation.[2][][3][]

## Failure semantics

Every matrix cell must produce a structured observation:

- a known, reproducible sparse-coverage boundary is `status="coverage_failure"`; retain it
  as a negative observation;
- an infrastructure, parser, corrupt-input, or otherwise unexpected failure is
  `status="operational_error"`; fix the cause and rerun the cell; and
- only a successful row with complete, internally consistent telemetry may set
  `calibration_eligible=true` and contribute positive accuracy scores.

Do not convert an operational error into evidence that a coverage level fails, and do not
discard a genuine sparse-coverage failure. Invalid successes and unexpected painting
windows remain in the audit output but are ineligible for threshold selection.

## Threshold selection guardrails

Predeclare the product accuracy, completeness, and global-error targets and the selection
rule before reading the sweep results. A mean-minus-variability rule has precedent as a
calibration form,[4][] but neither that study's target nor its Affymetrix/G-nomix threshold
transfers directly to Yeliztli.

Do not select any threshold until the observed report is a complete, duplicate-free match
to the predeclared matrix. Every operational-error cell must be resolved and rerun; every
expected known coverage failure must remain present as a negative observation. Select a
policy only inside a stable pass region that satisfies all predeclared targets across every
supported input shape, validation stratum, and required chromosome-distribution scenario.

The donor cohort is small within each stratum, and simulated genomes derived from the same
donor pool are not fully independent biological samples. Report uncertainty explicitly;
repeated site-removal seeds do not narrow the biological-sample confidence interval.
Write the selected rule as a frozen confirmation-policy artifact bound to the calibration
plan and complete observation hashes, selector code/report hashes, bundle, simulation
manifest, code revision, and a commitment over the still-sealed final split. The policy
must declare every truth endpoint, require all cells to pass without averaging biological
replicates, and aggregate on the exact dimensions `simulation_iid`, `input_mask`,
`validation_stratum`, `chromosome_drop_scenario`, `fraction`, and `seed`. Keeping
`simulation_iid` as an aggregation dimension prevents simulated biological replicates
from being averaged together. The policy must enumerate its telemetry predicates and fix
the three supported masks, fractions, seeds, and chromosome-drop scenarios.
Final-confirmation planning requires this artifact plus its independently recorded digest in
`--expected-confirmation-policy-sha256`, derives every matrix axis from it, and rejects
CLI overrides or the native unmasked diagnostic scenario. Final array tasks must repeat
both policy arguments, so replacing a policy cannot self-consistently replace its own
purported calibration and selection history.

Evaluate that policy once against the founder-disjoint final-confirmation split before
describing it as supported for the pinned models, donors, masks, and simulation protocol.
It is not evidence for other cohorts, marker layouts, models, or biological ancestry
histories. Do not tune the threshold after opening that split; a failure returns the work
to calibration with a newly generated, still-sealed confirmation set. If the evidence
does not identify a stable pass region, or if any required class lacks eligible founders
(as in the dated exploratory audit above), retain fail-closed behavior and expand
validation instead of choosing an attractive-looking marker count.

## References

1. [Maples BK et al. (2013). “RFMix: A discriminative modeling approach for rapid and robust local-ancestry inference.” *American Journal of Human Genetics*.](https://doi.org/10.1016/j.ajhg.2013.06.020) DOI: 10.1016/j.ajhg.2013.06.020; PMID: 23910464.
2. [Honorato-Mauer J et al. (2025). “Characterizing the impact of genetic diversity on local ancestry inference.” *American Journal of Human Genetics*.](https://doi.org/10.1016/j.ajhg.2024.12.005) DOI: 10.1016/j.ajhg.2024.12.005; PMID: 39753130.
3. [Avadhanam S, Williams AL (2025). “Phase-free local ancestry inference.” *G3*.](https://doi.org/10.1093/g3journal/jkaf122) DOI: 10.1093/g3journal/jkaf122; PMID: 40471844.
4. [Motegi S et al. (2026). “G-nomix: An array-based pipeline for rapid local ancestry inference.” bioRxiv preprint.](https://doi.org/10.64898/2026.05.18.726085) DOI: 10.64898/2026.05.18.726085; PMID: 42239055.

[1]: https://doi.org/10.1016/j.ajhg.2013.06.020
[2]: https://doi.org/10.1016/j.ajhg.2024.12.005
[3]: https://doi.org/10.1093/g3journal/jkaf122
[4]: https://doi.org/10.64898/2026.05.18.726085
