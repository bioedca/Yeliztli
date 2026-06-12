# `sex_inference_synthetic/` — committed synthetic fixtures

Used exclusively by `tests/backend/test_validate_sex_thresholds.py` (and any
future test that needs deterministic sex-inference inputs without touching a
real export).

**No real genotypes.** Every row in every file here was hand-fabricated to
exercise a specific branch of the Plan §9.4 algorithm. The bio-validator's
real-export attestation lives in `docs/sex_inference_threshold_validation.md`
(Step 53) and reports aggregate counts only.

## Format

AncestryDNA V2.0 raw export (5-column TSV: `rsid chromosome position allele1
allele2`) with the `#AncestryDNA` vendor signature in the header comment block.
The dispatcher detects the vendor, the AncestryDNA parser canonicalizes the
genotype pairs, and `scripts/validate_sex_thresholds.py` then runs the Plan
§9.4 classification against the parsed result.

AncestryDNA chromosome encoding (see `backend/ingestion/chromosomes.py`):

- `23` → chrX (non-PAR by convention; positions still PAR-filtered by coordinate)
- `24` → chrY
- `25` → chrX (PAR; positions in PAR1/PAR2 ranges, always pre-filtered)

## GRCh37 PAR coordinates the algorithm pre-filters on

- PAR1: `chrX:60001 – 2699520`
- PAR2: `chrX:154931044 – 155260560`

Non-PAR positions in every fixture sit at ≥ `50_000_000` to stay well clear
of both PAR intervals.

## Fixtures

### `xx_sample.txt` → classification **`XX`**

XX-supporting: ≥1 het call on non-PAR chrX with chrY at/below the noise floor.

- chr 23 (non-PAR X): 2 het, 2 hom, 1 no-call → `x_nonpar_het = 2`
- chr 25 (PAR X): 2 het rows in PAR1 — pre-filtered out
- chr 24 (Y): 6 no-call rows → `y_rate = 0.0`

### `xy_sample.txt` → classification **`XY`** (confirmed)

Candidate XY on chrX + chrY rate well above the 0.30 confirm threshold.

- chr 23 (non-PAR X): 0 het, 4 hom, 1 no-call → candidate XY
- chr 25 (PAR X): 1 het row in PAR1 — pre-filtered out
- chr 24 (Y): 10 rows, 8 typed, 2 no-call → `y_rate = 0.800`

### `manual_review_sample.txt` → classification **`manual_review`**

Candidate XY on chrX + chrY rate in the `(0.10, 0.30]` intermediate band.

- chr 23 (non-PAR X): 0 het, 4 hom, 1 no-call → candidate XY
- chr 24 (Y): 10 rows, 2 typed, 8 no-call → `y_rate = 0.200`

## Regenerating

These files are hand-edited TSVs — there is no generator. To add a new branch
case, add a new `<name>_sample.txt`, update this README, and parametrize the
new case in `tests/backend/test_validate_sex_thresholds.py`.
