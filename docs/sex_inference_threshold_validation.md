# Sex-inference threshold validation (Plan §9.4)

Provenance record for the sex-inference classification thresholds in
`backend/services/sex_inference.py` and the evaluability floors added in #429.
It is the target of the references in `sex_inference.py`,
`scripts/validate_sex_thresholds.py`, `tests/fixtures/sex_inference_synthetic/README.md`,
`tests/backend/test_sex_inference.py`, and `CHANGELOG.md` (Step 53).

> **Provenance note (#435).** The standalone attestation file was described at Step 53
> but never committed. This record restores it from committed sources:
> the **real-export** figures below are transcribed verbatim from the Step-53 record
> preserved in `CHANGELOG.md` (aggregate counts/rates only — no genotype rows, rsIDs,
> or coordinates; the real `AncestryDNA.txt` is local-only and `.gitignore`d, so it is
> **not** re-run here). The **synthetic-fixture** results are independently
> **re-run from the committed fixtures** via the reproduction command below, so that
> half of this record is fully verifiable in CI. This is a reconstructed provenance
> record, not a fresh bio-validator signature against the real export.

## Validated thresholds and floors

Source of truth: the module-level constants in `backend/services/sex_inference.py`
(mirrored in `scripts/validate_sex_thresholds.py` — keep both in sync).

| Constant | Value | Role |
|---|---|---|
| `_THRESHOLD_XY_CONFIRM` | `0.30` | on the all-homozygous (candidate-XY) branch, a chrY non-no-call rate above this confirms **XY** |
| `_THRESHOLD_PAR_NOISE` | `0.10` | PAR-noise floor: a chrY rate at/below this is treated as no chrY signal |
| `MIN_X_NONPAR_TYPED` | `100` | minimum typed non-PAR chrX calls for a confident verdict (#363/#429) |
| `MIN_Y_PROBES` | `50` | minimum chrY probes for a confident verdict (#363/#429) |
| PAR1 (GRCh37) | `60001–2,699,520` | pseudo-autosomal region 1, pre-filtered off chrX |
| PAR2 (GRCh37) | `154,931,044–155,260,560` | pseudo-autosomal region 2, pre-filtered off chrX |

Decision tree (`backend/services/sex_inference.py::_classify`; order is load-bearing):

1. Below `MIN_X_NONPAR_TYPED` (100) typed non-PAR chrX **or** `MIN_Y_PROBES` (50) chrY
   probes → **unknown** (too thin to resolve; a lone non-PAR chrX het is not evidence
   of two X chromosomes — it occurs in males too; Chen et al., PMID 38073250).
2. Any non-PAR chrX **heterozygote** → **XX** when chrY rate ≤ `_THRESHOLD_PAR_NOISE`
   (0.10); otherwise **manual_review** (discordant het-X + chrY signal). This is the
   dispositive branch — a low chrY rate here yields XX, *not* unknown.
3. Otherwise all non-PAR chrX **homozygous** (candidate XY) → **XY** when chrY rate >
   `_THRESHOLD_XY_CONFIRM` (0.30); **manual_review** when in `(0.10, 0.30]`;
   **unknown** at/below 0.10.

The Plan §9.4 literature-default thresholds were adopted **verbatim** — the validation
below required no tuning.

## Real AncestryDNA V2.0 export (known ground-truth XX)

Transcribed from the committed Step-53 record (`CHANGELOG.md`); aggregate only.

- **5,998** heterozygous non-PAR chrX calls → dispositive non-PAR-heterozygous branch.
- chrY non-no-call rate **0.002**, well below the `_THRESHOLD_PAR_NOISE` (0.10) floor.
- Classification: **XX** (correct vs. known ground truth).

No genotype rows, rsIDs, or coordinates from the real export cross the repo boundary
(PRD §11–§12 privacy posture; `.gitignore` keeps `AncestryDNA.txt` local).

## Synthetic fixtures (committed; re-run here)

Re-run from `tests/fixtures/sex_inference_synthetic/*.txt` at the literature-default
thresholds. Each fixture carries ≥100 typed non-PAR chrX and ≥50 chrY probes, so the
issue-#429 evaluability floors are satisfied and the verdict is driven by the het/hom
ratio and chrY rate.

| Fixture | non-PAR chrX typed | non-PAR het rate | chrY probes | chrY rate | Classification |
|---|---|---|---|---|---|
| `xx_sample.txt` | 120 | 0.50 | 60 | 0.00 | **XX** (dispositive het) |
| `xy_sample.txt` | 120 | 0.00 | 60 | 0.80 | **XY** (chrY rate > 0.30) |
| `manual_review_sample.txt` | 120 | 0.00 | 60 | 0.20 | **manual_review** (chrY rate in `(0.10, 0.30]`) |

## Reproduction

```bash
python scripts/validate_sex_thresholds.py <export-or-fixture-path> --json
```

Output is aggregate counts and rates only (never genotype rows), so it is paste-safe.
CI exercises the synthetic half via `tests/backend/test_validate_sex_thresholds.py`;
the real-export half is run locally against the (gitignored) `AncestryDNA.txt`.
