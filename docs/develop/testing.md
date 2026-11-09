# Testing

Yeliztli has backend (pytest), frontend (Vitest), and end-to-end (Playwright) suites, run in
**tiers** so pull requests stay fast while `main` keeps broad coverage.

## Running the tests

```bash
# Backend — fast tier (what PRs run)
python -m pytest tests/backend/ -v -m "not slow"
make test-backend

# Frontend unit/component tests
cd frontend && npm test          # or: make test-frontend

# End-to-end (boots the app; cross-browser)
npx playwright test              # or: make test-e2e

# Everything except E2E
make test
```

The `.[dev,docs]` bootstrap installs Python dependencies, not Java. The existing
`requires_java` marker skips genuine JVM integration tests when `java` is absent
from `PATH`; a run with those skips is limited validation, not complete backend
coverage. Linux and macOS backend CI jobs require `java -version` to succeed
before pytest, so the real-Java cases execute there.

LAI calibration planning tests isolate only the external `java -version` probe
with an explicitly synthetic identity. They still compute the production
Python/package/lock fingerprint and exercise its drift checks, input hashes and
frozen-policy validation. Separate `requires_java` cases bind a real JVM identity
through both calibration and final-confirmation planning/execution. These tests
use synthetic inputs and stub inference; they do not claim to run Beagle phasing
or validate scientific accuracy. To run the full calibration module locally:

```bash
java -version
python -m pytest tests/backend/test_lai_coverage_calibration.py -v --tb=short
```

Non-watch frontend test runs (`npm test`, `npm run test:ci`, and `npm run
test:coverage`) fail on React `not wrapped in act(...)` warnings as well as
Vitest assertion failures. CI and release jobs use the same strict command
through `npm run test:ci`; `npm run test:watch` remains a local watch-mode
command for iteration.

The **slow tier** (`-m slow`) holds long-running benchmarks and accuracy validations; it runs
**nightly**, not on every PR.

The annotation benchmarks share `scripts/benchmark.py::seed_gene_phenotype`, which
models a current MONDO/HPO install with synthetic associations and a version built
by the production loader's stamp helper. The `synthetic-benchmark` source labels
are fixture provenance, not an upstream release or scientific validation. Fast
tests check exact returned associations and confirm that deleting or downgrading
the stamp still withholds MONDO/HPO rows without hiding independent OMIM rows.
The 10k smoke test and nightly 600k benchmark both require gene-phenotype matches;
do not bypass the production gate or remove those assertions to repair a fixture.

For the 10k annotation smoke, retain the **30-second** annotation guard, the
variant/output counts, nonempty gene-phenotype matches and absence of errors.
Run the unchanged production-path test when investigating a timing failure:

```bash
python -m pytest tests/backend/test_benchmark.py::test_annotation_10k_smoke -v --tb=short
python -m pytest tests/backend/test_annotation_upsert_execution.py -v --tb=short
```

[#2609](https://github.com/bioedca/Yeliztli/issues/2609) identified SQL construction
as a substantial cost: expanding a wide upsert into multi-row `VALUES` rebuilt
bind parameters for every row. The upsert now passes uniformly normalized rows
to one parameterized Core statement through `Connection.execute(statement,
rows)`. SQLite's variable budget applies to that single-row template, independently
of the number of parameter sets. The dedicated regression tests check bounded SQL
at both 999 and 32766 variable limits, complete stored values in live/staging
tables, sparse and ordered duplicate updates, and rollback after a later error.
They complement the elapsed-time guard; they do not replace it.

Record immutable source SHAs, Python/dependency versions, CPU allocation and actual
process affinity, concurrent work, memory, and filesystem capacity for timing
comparisons. The smoke's databases are shared in-memory SQLite databases; fixture
setup and total pytest wall time are distinct from the timed annotation call.
Repeat baseline/head runs serially with alternating order before a separately
recorded, bounded load experiment. CPU-only contention does not reproduce the
I/O and process mix of concurrent backend shards. A Slurm CPU request alone does
not establish affinity when the cluster has no task-binding plugin.

Use profiling in a separate diagnostic run and compare the engine's lookup,
merge and upsert counters. Deterministic profiling adds overhead; do not report
its elapsed time as acceptance evidence or add overlapping worker cumulative
times together. Keep failing runs and their environment records, compare complete
synthetic outputs, and distinguish implementation cost from resource contention.
A faster run on another host does not by itself reproduce or explain the original
failure, and a loaded timing failure is still a failed smoke test.

The AncestryDNA real-bundle fixture copies only ClinVar rows alongside the VEP
bundle. It contains no MONDO/HPO associations, so its unstamped MONDO/HPO warning
does not indicate lost fixture coverage and must not be silenced with invented
installation provenance.

### Ancestry inference timing

Keep the existing ancestry guards unchanged: `test_projection_under_2_seconds`
times **full inference** and asserts **less than eight seconds**, despite its
historical name. `test_tier1_under_one_second` asserts **less than two seconds on
Linux** (five on other platforms). `projection_time_ms` measures only the PCA
projection, so it cannot explain the cost of the complete inference call.

```bash
python -m pytest tests/backend/test_ancestry.py::TestInferAncestry::test_projection_under_2_seconds -v --tb=short
python -m pytest tests/backend/test_ancestry_e2e.py::TestPerformance::test_tier1_under_one_second -v --tb=short
python -m pytest tests/backend/test_ancestry.py::TestBootstrapAdmixtureNNLS -v --tb=short
```

[#2608](https://github.com/bioedca/Yeliztli/issues/2608) reports an eight-second
failure during concurrent local backend validation. Separate diagnostic profiles
identified repeated dosage standardization in the 100 bootstrap resamples as a
substantial implementation cost. Bootstrap now standardizes the float64 dosages
once, then gathers those values in each resample's original index order alongside
the corresponding loading rows. The random draws, repeated indices, zero
imputation, solver, percentile calculation and rounding remain unchanged.
Production-path regressions bound reference-scale reads for both raw and
annotated samples; numerical controls check sampled operands and intervals.

A matched synthetic comparison on 2026-11-08 used baseline
`9568f045818940026f2a38c55cc09e4c5345bb49` and implementation
`ef172eb69e9ad6ee8492b91f0a4ee1c242bbe54e`. Both clean checkouts used Python
3.13.17, NumPy 2.5.4, SciPy 1.18.1, SQLAlchemy 2.1.6 and pytest 9.1.1, with
identical installed package versions. A CPU-only Slurm allocation was exclusive
for at most 20 minutes with 16 GiB requested; test processes were restricted to
four physical cores through explicit affinity and single-thread OpenMP/OpenBLAS
settings.
Baseline/head order alternated across three repeats per condition. The loaded
condition added one bounded CPU worker on each of those same four cores.

| Full inference case | Condition | Baseline median (s) | Optimized median (s) |
| --- | --- | ---: | ---: |
| Eight-second regression guard | Serial | 0.4363 | 0.0301 |
| Eight-second regression guard | CPU loaded | 0.8581 | 0.0616 |
| Tier 1 Linux two-second guard | Serial | 0.6427 | 0.0434 |
| Tier 1 Linux two-second guard | CPU loaded | 1.2641 | 0.0878 |

All 24 measured runs, four untouched plain tests and four separate profile runs
passed their unchanged assertions. Diagnostic runs fixed the bootstrap seed at
42; plain tests retained production randomness. Complete result fingerprints
matched baseline/head for each fixture after excluding only the volatile
`projection_time_ms` field. The table excludes profile runs. Terminal controller,
exit, log and test-report evidence was verified; Slurm accounting was unavailable.

The original greater-than-eight-second failure **did not reproduce** in this
environment. These measurements demonstrate reduced repeated work under the
recorded conditions, not an explanation of every original slowdown or a runtime
guarantee. The original host, storage and concurrent-shard I/O were not recreated.
Record full-call and projection timings separately, retain failing runs, and use
the same sources, dependencies and resources when comparing changes. Numerical
equivalence tests validate software behavior, not scientific accuracy.

## Tracked science-evidence references

After staging the intended files, run `python scripts/check_science_evidence_paths.py`.
The always-on CI lint job runs the same offline guard. It scans literal dated
`data/science-evidence/<date-slug>` references outside `data/` in Git's index and
requires the exact file, or at least one file beneath a referenced directory, to
be tracked. A local untracked packet cannot hide a missing committed input. The
guard reads indexed contents, so stage both reference edits and intended public
evidence files before running it; do not bulk-add unrelated research.

Only exact source/path pairs for the scientific-validity issue-form example and
the synthetic `.gitignore` probes are exempt. New filenames or the same example
used by a real fixture are checked. Undated placeholders are not concrete packet
references. Path availability does not establish scientific accuracy, licensing,
or the authenticity of an access date.

The #2481 audit on 2026-11-09 found no recoverable historical source bytes in the
available registered checkouts or locally available Git history for these fixtures:

| Fixture | Originally reported access | Missing original packet |
| --- | --- | --- |
| `clinvar_seed_snapshot.json` | 2026-07-16 | `2026-07-16-clinvar-seed-1968` |
| `panel_gene_coordinates_grch37.json` | 2026-06-27 | `2026-06-27-issue-1098-rsid-gene-guard` |

Both fixtures now explicitly record `historical_evidence.status = unavailable`.
Their values and originally reported access dates are retained; this audit does
not independently verify them. The separately committed, later ClinVar record
overrides keep their own evidence pointers. No missing payload was reconstructed,
backdated, or certified for licensing. If the original bytes become available,
audit their public origin and licensing before committing them. A deliberate
refresh must record its actual access date and preserve the distinction between
new evidence and the unavailable historical inputs.

## Complete backend validation with limited temporary storage

A filesystem can have free bytes and inodes while the effective user or project
quota is exhausted. [#2625](https://github.com/bioedca/Yeliztli/issues/2625) records
an incomplete run caused by that distinction. A partial pass or a successful
rerun of only the affected modules is not a complete-suite pass.

Use the existing `--num-shards` / `--shard-id` options to run **every shard
sequentially**, reclaiming its terminal temporary fixtures before the next one.
`tests/conftest.py` registers the collection-time partition in
`scripts/ci_backend_shard.py`; `tests/backend/test_ci_backend_shard.py` checks its
total, disjoint coverage. Assignment is by module, so a whole module still has
to fit. Sharding bounds retained fixtures across processes, not every test's
peak storage. It does not repair unsupported filesystem semantics or replace
the CI platform/runtime matrix.

Before starting:

- Use a clean, committed dedicated worktree and its active virtualenv, bootstrapped
  with `python -m pip install -e '.[dev,docs]'`. Keep the source SHA, dependencies,
  interpreter, runtime availability (including Java), marker expression,
  and shard count identical throughout. Do not edit tests, assertions, timeouts,
  or selection to make a shard pass. Avoid other test jobs competing for the same
  CPU or storage; use the project's Slurm workflow for heavy cluster validation.
- Check available bytes **and** inodes on both scratch and evidence filesystems,
  for example with `df -h` and `df -i`. Check the effective user/project quota
  through the filesystem's quota tooling or administrator, including remaining
  allowance and grace limits. An unavailable quota command is unknown capacity,
  not evidence of unlimited space. Allow room for the largest shard, logs,
  dependencies, and retained failure evidence.
- Choose verified local storage with the repository's required filesystem
  semantics. Set `validation_scratch_parent` below to that existing directory;
  a different mount is not a universal fix. Keep evidence outside disposable
  scratch. Use only repository synthetic fixtures and sanitize any evidence
  shared publicly.

From the worktree root, initialize one validation attempt in a Bash shell:

```bash
set -euo pipefail
test -n "${VIRTUAL_ENV:-}"
test -z "${PYTEST_ADDOPTS:-}"  # no hidden selection or reporting overrides
test -z "$(git status --porcelain)"
validation_sha=$(git rev-parse HEAD)
validation_python=$(command -v python)
validation_scratch_parent=/path/to/verified-local-storage
test -d "$validation_scratch_parent"
mkdir -p output
validation_evidence=$(mktemp -d "$PWD/output/backend-validation.XXXXXX")
printf '%s\n' "$validation_sha" > "$validation_evidence/source-sha.txt"
printf '%s\n' "$validation_python" > "$validation_evidence/python-path.txt"
python --version > "$validation_evidence/python-version.txt" 2>&1
python -m pip freeze > "$validation_evidence/packages.txt"
java -version > "$validation_evidence/java-version.txt" 2>&1

# Capture the complete selected collection, without a shard filter.
collection_scratch=$(mktemp -d "$validation_scratch_parent/backend-collection.XXXXXX")
printf '%s\n' "$collection_scratch" > "$validation_evidence/collection-scratch.txt"
collection_status=0
TMPDIR="$collection_scratch" python -m pytest tests/backend/ \
  --collect-only -q --color=no -m 'not slow' \
  > "$validation_evidence/collection.log" 2>&1 || collection_status=$?
printf '%s\n' "$collection_status" > "$validation_evidence/collection.exit"
test "$collection_status" -eq 0
```

Run the following block once for each `shard_id` **1, 2, and 3**, in that order,
in the same shell. Each invocation gets a fresh directory; never reuse an
earlier attempt's outputs. The direct redirection preserves pytest's exit code
without a logging pipeline masking it.

```bash
shard_id=1  # repeat with 2, then 3, after the terminal-run checks below
test "$(git rev-parse HEAD)" = "$validation_sha"
test -z "$(git status --porcelain)"
test "$(command -v python)" = "$validation_python"
shard_evidence="$validation_evidence/shard-$shard_id"
mkdir "$shard_evidence"  # refuses to overwrite a previous result
shard_scratch=$(mktemp -d "$validation_scratch_parent/backend-shard-$shard_id.XXXXXX")
printf '%s\n' "$shard_scratch" > "$shard_evidence/scratch-path.txt"
printf '%s\n' "$validation_sha" > "$shard_evidence/source-sha.txt"
python -m pip freeze > "$shard_evidence/packages.txt"
cmp "$validation_evidence/packages.txt" "$shard_evidence/packages.txt"
shard_status=0
TMPDIR="$shard_scratch" python -m pytest tests/backend/ -v --tb=short \
  --color=no -r fEspxX -m 'not slow' --num-shards 3 --shard-id "$shard_id" \
  --basetemp="$shard_scratch/pytest" --junitxml="$shard_evidence/results.xml" \
  > "$shard_evidence/pytest.log" 2>&1 || shard_status=$?
printf '%s\n' "$shard_status" > "$shard_evidence/pytest.exit"
git rev-parse HEAD > "$shard_evidence/source-sha-after.txt"
git status --porcelain > "$shard_evidence/source-status-after.txt"
cmp "$validation_evidence/source-sha.txt" "$shard_evidence/source-sha-after.txt"
test ! -s "$shard_evidence/source-status-after.txt"
test "$shard_status" -eq 0
```

Pytest [clears `--basetemp` before a run](https://docs.pytest.org/en/stable/how-to/tmp_path.html#temporary-directory-location-and-retention).
Only pass the new, disposable child shown above, never a worktree, shared temp
root, or evidence directory. After each foreground process exits, verify its
exit file, complete log and XML report, and that no child or remote job still
uses its recorded scratch directory. Confirm that exact directory is owned by
this attempt before removing it; then recheck capacity before the next shard.
The collection scratch can be reclaimed under the same checks after collection
finishes. Do not delete another run's files or sweep a shared temp root.

On failure, interruption, quota exhaustion, or missing output, stop and retain
the failed scratch and evidence. If storage must be reclaimed, first copy the
needed evidence to verified storage and verify the copy and terminal ownership.
Record the attempt as incomplete; use a new attempt after correcting the cause.
Do not count skipped/xfail cases as passes or relax prerequisite policy to fill
a missing result. Reports and exit files stay outside scratch until validation
and review are complete.

Finally, reconcile **test IDs**, not just totals. This check uses the passing
node IDs in pytest's short summary and the unsharded collection from this same
attempt. It rejects missing shards, nonzero exits, empty results, duplicates,
and any omitted or unexpected test. Run with normal Python, without `-O`:

```bash
python - "$validation_evidence" > "$validation_evidence/reconciliation.log" <<'PY'
from collections import Counter
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

root = Path(sys.argv[1])
if not __debug__:
    raise RuntimeError("Run without Python optimization")
assert (root / "collection.exit").read_text().strip() == "0"
expected = [line for line in (root / "collection.log").read_text().splitlines()
            if line.startswith("tests/backend/") and "::" in line]
assert expected and len(expected) == len(set(expected)), "Invalid collection"
seen = []
for shard_id in (1, 2, 3):
    shard = root / f"shard-{shard_id}"
    assert (shard / "pytest.exit").read_text().strip() == "0", shard
    assert (shard / "source-sha.txt").read_text() == (root / "source-sha.txt").read_text()
    assert (shard / "source-sha-after.txt").read_text() == (root / "source-sha.txt").read_text()
    assert not (shard / "source-status-after.txt").read_text().strip()
    assert (shard / "packages.txt").read_text() == (root / "packages.txt").read_text()
    cases = list(ET.parse(shard / "results.xml").iter("testcase"))
    assert cases and all(not any(c.tag in {"failure", "error", "skipped"}
                                for c in case) for case in cases), shard
    passed = [line.removeprefix("PASSED ")
              for line in (shard / "pytest.log").read_text().splitlines()
              if line.startswith("PASSED ")]
    assert passed and len(passed) == len(cases), shard
    seen.extend(passed)
assert len(seen) == len(set(seen)), "Duplicate shard results"
assert Counter(seen) == Counter(expected), "Missing or unexpected test IDs"
print(f"Complete selected suite: {len(seen)} tests passed exactly once")
PY
cat "$validation_evidence/reconciliation.log"
```

Keep the collection, all three logs/XML reports and exit codes, source and
environment records, capacity observations, and reconciliation output together.
An absent shard, truncated summary or allowed prerequisite skip requires an
explicit incomplete/limited validation report; it is not a complete selected
suite pass. The `slow` tier remains separate and is not claimed by this recipe.

## Restricted-sandbox wakeup diagnostic

Before collecting backend tests, the harness sends one byte through a
nonblocking `socket.socketpair()`, once per session. This probes the same
self-pipe operation used by Python 3.12/3.13's selector event loop. Both sockets
close immediately; the check starts no threads and changes no event-loop behavior.

If the operating system refuses that operation with `EPERM` or `EACCES`, pytest
exits nonzero with **`ASYNCIO_WAKEUP_BLOCKED`**. Cross-thread event-loop wakeups
are blocked in that environment, so `TestClient`/AnyIO portal tests can hang.
Run the backend suite outside the restricted sandbox. This is the environment
limitation investigated in [#1892](https://github.com/bioedca/Yeliztli/issues/1892)
and [#2233](https://github.com/bioedca/Yeliztli/issues/2233), rather than evidence
of a Yeliztli or Python 3.13 regression. Tests fail; none are skipped by this check.

Other errors retain their original exception. A timeout alone does not establish
sandbox policy, and a successful send does not prove that every possible startup
hang is absent. CI retains its separately bounded `TestClient startup witness`
(`scripts/ci_startup_witness.py`) for the actual application startup path.

References: Python's
[3.12 selector-loop implementation](https://github.com/python/cpython/blob/3.12/Lib/asyncio/selector_events.py)
and [3.13 implementation](https://github.com/python/cpython/blob/3.13/Lib/asyncio/selector_events.py),
and pytest's [configuration hook](https://docs.pytest.org/en/stable/reference/reference.html#pytest.hookspec.pytest_configure)
and [session exit API](https://docs.pytest.org/en/stable/reference/reference.html#pytest.exit)
(accessed 2026-11-09).

## CI tiers

CI (`.github/workflows/ci.yml`) is organised so a single **`ci-required`** check aggregates
everything:

- **Tier 1 — every PR (Linux):** lint, backend tests (fast), frontend tests, frontend build,
  install smoke test, Docker build, the strict **docs build**, and workflow lint. Path filters
  skip irrelevant jobs (a docs-only PR skips the backend suite, etc.), and skipped jobs count
  as pass.
- **Tier 2 — merge to `main` / merge queue:** the macOS backend + smoke-install legs and the
  three-browser **E2E** suite — portability gates that block the merge rather than every PR.
- **Nightly:** the slow tier (with the 600k annotation benchmark in a job of its own) plus a
  cross-OS backstop. A job that fails or runs past its time budget files a regression issue,
  or comments on the one already open.

Browser installation has a separate 60-minute command budget, a 65-minute step
backstop, and a 120-minute whole-job budget for setup, installation, tests and
diagnostics. The installation allowance retains headroom over the successful
33m09s WebKit install recorded in [#2600](https://github.com/bioedca/Yeliztli/issues/2600).
The command bounds the whole logging
pipeline and its process group, with a 30-second TERM-to-KILL grace period.
The supervisor stays alive through escalation so descendants that ignore TERM
are killed before diagnostics upload. Any nonzero phase exit still fails the gate.

Each browser/run attempt retains a `playwright-install-<browser>-<attempt>`
artifact for seven days, including installer stdout/stderr and JSON outcome,
exit code, elapsed seconds, configured budget, kill grace and whether the
supervisor observed TERM. Exit 124 is labelled `timeout_status`. Exit 137 is
also labelled `timeout_status` when the supervisor recorded TERM during the
deadline cleanup; otherwise it is labelled `kill_status`, since KILL alone
does not identify its cause. These diagnostics upload even when browser tests never
start. All three browser projects remain required.

!!! tip "Verify UI changes before merging"
    Because the E2E suite is Tier 2 (merge-time), it's worth driving a UI change in a real
    browser before merging so a frontend regression doesn't surface only at merge.

## Test conventions

The suite follows specific assertion standards (assert real values, not just non-null;
carriage-gated modules need `hom_ref` negative controls). See [contributing](contributing.md).

Prefix-search plan guards exercise the production predicate with 2,000 synthetic
rows, bound parameters, and both list and count projections, before and after
`ANALYZE`. They require both NOCASE search indexes and reject scans for selective
prefixes; removing either index is a negative control. SQLite's
[cost-based OR optimization](https://www.sqlite.org/optoverview.html#or_optimizations)
is optional, so annotated searches use independent candidate ranges with
`UNION ALL` and `IN` membership. Membership preserves one result per row when
both branches match. Broad prefixes still require work proportional to their
candidate set and may use different plans; these guards assert selective access,
not one fixed plan for every data distribution.
