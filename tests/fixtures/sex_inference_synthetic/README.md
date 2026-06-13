# `sex_inference_synthetic/` — committed synthetic fixtures

Used exclusively by `tests/backend/test_validate_sex_thresholds.py` (and any
future test that needs deterministic sex-inference inputs without touching a
real export).

**No real genotypes.** Every row in every file here was hand-fabricated to
exercise a specific branch of the Plan §9.4 algorithm. The bio-validator's
real-export attestation lives in `docs/internal/sex_inference_threshold_validation.md`
(Step 53) and reports aggregate counts only.

**Evaluable densities (issue #363).** A confident `XX`/`XY`/`manual_review`
verdict now requires a minimum aggregate denominator on both sex chromosomes —
`x_nonpar_typed ≥ MIN_X_NONPAR_TYPED` (100) and `y_total ≥ MIN_Y_PROBES` (50)
in `backend/services/sex_inference.py`. Each fixture below therefore carries
**≥100 typed non-PAR chrX and ≥50 chrY probes**; thinner inputs are classified
`unknown` (a single non-PAR chrX het is not evidence of two X chromosomes — it
occurs even in males, Chen et al. PMID 38073250). The het/hom ratio and chrY
rate that select each branch are preserved at the larger scale.

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

XX-supporting: heterozygous non-PAR chrX (het rate 0.5) with chrY at/below the
noise floor, both chromosomes evaluable.

- chr 23 (non-PAR X): 60 het, 60 hom, 1 no-call → `x_nonpar_typed = 120`, `x_nonpar_het = 60`
- chr 25 (PAR X): 2 het rows in PAR1 — pre-filtered out
- chr 24 (Y): 60 no-call rows → `y_total = 60`, `y_rate = 0.0`

### `xy_sample.txt` → classification **`XY`** (confirmed)

Candidate XY on chrX + chrY rate well above the 0.30 confirm threshold.

- chr 23 (non-PAR X): 0 het, 120 hom → candidate XY, `x_nonpar_typed = 120`
- chr 25 (PAR X): 1 het row in PAR1 — pre-filtered out
- chr 24 (Y): 60 rows, 48 typed, 12 no-call → `y_rate = 0.800`

### `manual_review_sample.txt` → classification **`manual_review`**

Candidate XY on chrX + chrY rate in the `(0.10, 0.30]` intermediate band.

- chr 23 (non-PAR X): 0 het, 120 hom → candidate XY, `x_nonpar_typed = 120`
- chr 24 (Y): 60 rows, 12 typed, 48 no-call → `y_rate = 0.200`

## Regenerating

These files are committed TSVs generated to exercise one Plan §9.4 branch each
at evaluable probe densities (≥100 non-PAR chrX, ≥50 chrY — issue #363). To add
a new branch case, add a new `<name>_sample.txt` clearing both floors, update
this README, and parametrize the new case in
`tests/backend/test_validate_sex_thresholds.py`.
