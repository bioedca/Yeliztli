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
| `_THRESHOLD_XY_CONFIRM` | `0.30` | on the hemizygous (candidate-XY) branch, a chrY non-no-call rate above this confirms **XY** |
| `_THRESHOLD_PAR_NOISE` | `0.10` | PAR-noise floor: a chrY rate at/below this is treated as no chrY signal |
| `_THRESHOLD_X_HET_HEMIZYGOUS` | `0.05` | non-PAR chrX het **rate** at/below this is one X (male-consistent; tolerates genotyping noise) — issue #519 |
| `_THRESHOLD_X_HET_DIPLOID` | `0.15` | non-PAR chrX het **rate** at/above this is two X (XX / XXY) — issue #519 |
| `MIN_X_NONPAR_TYPED` | `100` | minimum typed non-PAR chrX calls for a confident verdict (#363/#429) |
| `MIN_Y_PROBES` | `50` | minimum chrY probes for a confident verdict (#363/#429) |
| PAR1 (GRCh37) | `60001–2,699,520` | pseudo-autosomal region 1, pre-filtered off chrX |
| PAR2 (GRCh37) | `154,931,044–155,260,560` | pseudo-autosomal region 2, pre-filtered off chrX |

Decision tree (`backend/services/sex_inference.py::_classify`; order is load-bearing):

1. Below `MIN_X_NONPAR_TYPED` (100) typed non-PAR chrX **or** `MIN_Y_PROBES` (50) chrY
   probes → **unknown** (too thin to resolve; a lone non-PAR chrX het is not evidence
   of two X chromosomes — it occurs in males too; Chen et al., PMID 38073250).
2. **X dosage by the non-PAR chrX heterozygosity *rate*, not a binary count (issue #519).**
   A normal 46,XY male's non-PAR X is hemizygous, so his X-het rate is ≈0 (only
   genotyping noise); a diploid-X individual is heterozygous at a large fraction of
   markers (female-level). A rate ≤ `_THRESHOLD_X_HET_HEMIZYGOUS` (0.05) is a
   *candidate XY* (tolerates noise); a rate ≥ `_THRESHOLD_X_HET_DIPLOID` (0.15) is
   diploid-X; a rate in between is ambiguous X dosage → **manual_review**.
3. **Diploid-X** → **XX** when chrY rate ≤ `_THRESHOLD_PAR_NOISE` (0.10); otherwise
   **manual_review** (discordant two-X + chrY signal — the XXY pattern, issue #122).
4. **Candidate XY** → **XY** when chrY rate > `_THRESHOLD_XY_CONFIRM` (0.30);
   **manual_review** when in `(0.10, 0.30]`; **unknown** at/below 0.10.

The Plan §9.4 literature-default chrY thresholds were adopted **verbatim**. The X-het
rate cutoffs (`0.05` / `0.15`, issue #519) replace the original binary `x_nonpar_het >= 1`
test, which over-flagged every diploid-X-male export (non-PAR chrX het genotyping noise)
as `manual_review`; they sit in the wide gap between a male's ≈0 noise rate and a
diploid-X individual's tens-of-percent rate (validated genotype-array sex inference
thresholds on the X-het rate — seXY, PMID 28035028).

## Real AncestryDNA V2.0 export (known ground-truth XX)

Transcribed from the committed Step-53 record (`CHANGELOG.md`); aggregate only.

- **5,998** heterozygous non-PAR chrX calls — a female-level non-PAR chrX het
  *rate*, far above the `_THRESHOLD_X_HET_DIPLOID` (0.15) diploid-X cutoff (#519).
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
| `xx_sample.txt` | 120 | 0.50 | 60 | 0.00 | **XX** (diploid-X het rate ≥ 0.15) |
| `xy_sample.txt` | 120 | 0.00 | 60 | 0.80 | **XY** (chrY rate > 0.30) |
| `xy_xhet_noise_sample.txt` | 120 | 0.025 | 60 | 0.80 | **XY** (X-het rate ≤ 0.05 tolerates noise — #519) |
| `manual_review_sample.txt` | 120 | 0.00 | 60 | 0.20 | **manual_review** (chrY rate in `(0.10, 0.30]`) |

## Reproduction

```bash
python scripts/validate_sex_thresholds.py <export-or-fixture-path> --json
```

Output is aggregate counts and rates only (never genotype rows), so it is paste-safe.
CI exercises the synthetic half via `tests/backend/test_validate_sex_thresholds.py`;
the real-export half is run locally against the (gitignored) `AncestryDNA.txt`.
