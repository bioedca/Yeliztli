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

Tests that start database downloads must call `tests/backend/_downloads.py`'s
`wait_for_downloads()` in a `finally` block while their registry, settings,
client and loopback-server fixtures are still active. A persisted `complete`
job status precedes worker exit and cannot substitute for joining the executor.
The autouse leaked-download guard checks this ownership contract after fixture
teardown. The resume test's delayed-worker regression exercises a real synthetic
HTTP Range download and checks cleanup on success and an injected assertion
failure; it uses events rather than additional sleeps.

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

### 600k annotation regression budgets

The 2026-11-18 calibration for [#2554](https://github.com/bioedca/Yeliztli/issues/2554)
uses a [protocol committed before timing extraction or reference execution](https://github.com/bioedca/Yeliztli/blob/7138411bb21f2651a82199e547af109fb75099e5/docs/develop/benchmarks/annotation-budget-2026-11-18-protocol.json).
It fixes seven consecutive successful nightly executions from November 11–17 and
three serial fresh-process reference executions. Every measured execution passed
the original ingest/output/matched-source/error assertions. The indexed synthetic
fixture and parameterized upsert are present in all observations. The
[results and environment record](benchmarks/annotation-budget-2026-11-18-results.json)
and [all ten observations](benchmarks/annotation-budget-2026-11-18-measurements.csv)
retain source SHAs, public CI job links and access dates, versions and sanitized
timing/resource evidence.

| Environment and metric | Observed range | Conservative maximum / median | Headroom, rounded up to minutes | Strict ceiling |
| --- | --- | --- | --- | --- |
| CI: full annotation-test call | 119.99–200.10 s | 200.11 / 191.77 s | `ceil(1.5 × 200.11 / 60) × 60` | **360 s (6 min)** |
| Reference: annotation-only timer | 156.1–158.0 s | 158.1 / 157.8 s | `ceil(1.25 × 158.1 / 60) × 60` | **240 s (4 min)** |

The conservative values add one reporting quantum: 0.01 s for pytest call
durations, 0.1 s for the printed annotation timer. CI call time includes sample
insertion and output checks, so it upper-bounds the narrower annotation timer;
fixture setup is separate. Reference full-call times were 159.50–161.39 s,
annotation fixture setup was 15.91 s, and fresh-process wall times were
181.02–182.94 s. The seven CI observations span Ubuntu 24.04 and 26.04 runner
images with Python 3.12.16; no image or timing was excluded. Historical points
remain evidence at their original heads, with identical recorded benchmark input
objects; they do not substitute for final-head validation.

The reference runs used Python 3.12.3, an exclusive CPU-only allocation, a finite
32-GiB memory request and 90-minute job cap. Although exclusive allocation
reserves the whole CPU node, the process was pinned to four distinct physical
cores verified within it. Both `OMP_NUM_THREADS` and `OPENBLAS_NUM_THREADS` were
one. Source, packages, affinity, capacity, load and absence of co-scheduled jobs
were checked around each run; peak process RSS was about 2.1 GiB. All inputs were
generated in memory. Individual genotype files and binary reference bundles,
including duplicate tracked paths, were excluded and object-audited. An initial
privacy preflight stopped before fetching any payload blob or starting a test;
its failed setup record was retained and the duplicate exclusion corrected before
the three measurements. Detailed machine/job/path records remain private.

Both new ceilings exceed every conservative observation and are below twice
their group's median. Fast regression controls accept the observed maxima and
reject 383.54 s in CI and 315.6 s on the reference path; both old ceilings accepted
those scenarios. The full 600k test calls that same strict guard after all its
correctness assertions. The separate informational PRD targets remain 120/300 s,
and the 10k smoke retains its 30-second guard.

The isolated nightly benchmark keeps the existing fixed overhead margins:
6 minutes of annotation plus 15 minutes for fixture/reporting gives **21 minutes
of pytest**, plus 15 minutes for job setup gives **36 minutes for the job**.
The interrupt signal, 2-minute kill grace and cancelled/failed-job reporter remain
in place. Passed-test output now includes the annotation timer (`-rP`) for future
calibration. These are measured regression guards for the documented environments;
recalibration requires fresh evidence and a declared policy before measurements.

The AncestryDNA real-bundle fixture copies only ClinVar rows alongside the VEP
bundle. It contains no MONDO/HPO associations, so its unstamped MONDO/HPO warning
does not indicate lost fixture coverage and must not be silenced with invented
installation provenance.

### Findings payload filtering

The payload gate scans each individual string once with the same finite matcher
used for rendered text. It combines complete gene/drug matches within the
existing evidence group, while partial identifiers and fused sequences cannot
cross field edges. Independent complete records retain their own boundaries.
Only bounded character/event/state metadata is cached; finding text and payloads
are not cached globally. Unicode normalization, pinned confusables, strict JSON
parsing, opt-in gates and the withheld-versus-empty response contract are unchanged.

The presentation corpus in `test_pharmacogenomics.py` compares every group scan
against the original generic-matcher composition, including all pinned confusables
and safe-record controls. The production-summary regression checks exact counts,
top text and both filter outcomes, with a Unicode-operation budget rather than a
wall-clock threshold.

Run a bounded synthetic comparison in a finite CPU allocation:

```bash
python scripts/benchmark_payload_gate.py --rows 10000 --repeats 3
```

The script alternates legacy and optimized predicate lanes serially on the same
synthetic database and asserts identical complete summary bodies. Record the exact
head, environment, allocation/affinity and resource state with its JSON output.
Its input/summary hashes support comparisons without sample data. The separate
32-row residual profile is diagnostic, excluded from acceptance timings. This
measures software behavior and performance, not scientific accuracy; summary row
loading and aggregation remain linear, with their separate design tracked in
[#2358](https://github.com/bioedca/Yeliztli/issues/2358).

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
It disables inherited Bash monitor mode so a `BASH_ENV` startup file cannot put
the background installer pipeline outside the timeout-controlled process group.

The installer requires **GNU Coreutils timeout**. Its resolver checks
`gnutimeout`, `gtimeout`, then `timeout`, verifying the executable's `--version`
identity before invoking it. A missing or incompatible provider fails with
`timeout_unavailable` before installation starts. The deadline and descendant
controls still execute real GNU timeout; they do not silently skip when a
different implementation owns the unqualified name on Linux. These Ubuntu
installer probes retain their existing skip on other platforms without GNU
timeout; a Linux runner missing GNU fails the prerequisite check.

Ubuntu 26.04 uses uutils for the unqualified `timeout` command and provides GNU
timeout separately as `gnutimeout`. Selecting that existing executable keeps the
current runner and system-wide provider unchanged. See
[Ubuntu's provider documentation](https://ubuntu.com/server/docs/reference/other-tools/sudo-rs/),
the [GNU package file list](https://packages.ubuntu.com/resolute/amd64/gnu-coreutils/filelist)
and the [GNU timeout manual](https://manpages.ubuntu.com/manpages/resolute/man1/gnutimeout.1.html)
(accessed 2026-11-17). The local synthetic probe in #2692 reproduced the
descendant assertion failure with uutils 0.10.0 and passed with GNU timeout;
this comparison does not establish the exact uutils version on a retired CI runner.

Each browser/run attempt retains a `playwright-install-<browser>-<attempt>`
artifact for seven days, including installer stdout/stderr and JSON outcome,
exit code, elapsed seconds, configured budget, kill grace and whether the
supervisor observed TERM. The selected timeout executable and version are
recorded in the outcome and a separate tool metadata JSON. Exit 124 is labelled
`timeout_status`. Exit 137 is
also labelled `timeout_status` when the supervisor recorded TERM during the
deadline cleanup; otherwise it is labelled `kill_status`, since KILL alone
does not identify its cause. These diagnostics upload even when browser tests never
start. All three browser projects remain required.

### Native browser lifecycle evidence

Before the full E2E suite, each browser CI job runs the two synthetic specs in
`playwright.lifecycle.config.ts`: 13 cases per browser, 39 across Chromium,
Firefox and WebKit. This gate uses one worker, zero retries, fresh backend/Vite
servers, disposable application data, and no global setup. The existing
application, accessibility and contrast assertions remain unchanged. A failed
first attempt fails the job.

`DEBUG=pw:browser` records browser launch/exit events. Failure traces are retained
on that first attempt. The JSON report, process log and test artifacts upload
even when the command fails; application databases are excluded. Artifact names
include the browser, workflow run and attempt, with seven-day retention. Reusing
an existing diagnostic directory stops before launch, preserving its evidence.

The E2E SDK is pinned to Playwright 1.64.1 (WebKit 27.2, revision 2370).
Its Linux visual baselines include the light-settings Firefox checkbox and all
eight WebKit page/theme captures. A controlled comparison on the same public
source and native/font runtime passed all 24 existing snapshots with SDK 1.58.2;
SDK 1.64.1 passed 15 and differed on those nine captures. Visual review identified
browser text-metric and native-control rendering changes. The refreshed images
come from those unchanged tests; screenshot assertions, tolerances and coverage
remain unchanged. Preserve the old/new measurements when reviewing future SDK
updates rather than treating every screenshot difference as an application bug.

Use the locked SDK and its matching browser downloads on a provisioned Ubuntu
24.04 runner with Node 22. Install browser system dependencies using the
[upstream installation procedure](https://playwright.dev/docs/ci)
(accessed 2026-11-10), then build the production frontend. From the repository
root, with the development Python environment active:

```bash
set -euo pipefail
npm ci
npm --prefix frontend ci
npm --prefix frontend run build
export YELIZTLI_BROWSER_DIAGNOSTICS="output/playwright/lifecycle-local-$(date -u +%Y%m%dT%H%M%SZ)-$$"
test ! -e "$YELIZTLI_BROWSER_DIAGNOSTICS"
mkdir -p "$YELIZTLI_BROWSER_DIAGNOSTICS"
export YELIZTLI_DATA_DIR="$(cd "$YELIZTLI_BROWSER_DIAGNOSTICS" && pwd)/data"
DEBUG=pw:browser node node_modules/@playwright/test/cli.js test \
  --config=playwright.lifecycle.config.ts --workers=1 --retries=0 \
  2>&1 | tee "$YELIZTLI_BROWSER_DIAGNOSTICS/browser.log"
```

Add `--project=webkit --repeat-each=5` for 65 WebKit executions, using a fresh
diagnostic directory. Preserve every failed attempt and record its source SHA,
actual SDK/browser versions, native dependencies and resources. Private library
overlays must be checked in the browser's effective loader environment: the WPE
wrapper replaces the parent's `LD_LIBRARY_PATH`. A successful parent-process
library probe alone does not establish a working child runtime.

Visual runtime parity also requires complete SDK font/tool provisioning. The
pinned SDK's
[Ubuntu 24.04 dependency lists](https://github.com/microsoft/playwright/blob/v1.64.1/packages/playwright-core/src/server/registry/nativeDeps.ts)
include a shared `tools` list containing fonts, Fontconfig, FreeType and Xvfb,
alongside the browser-specific libraries (accessed 2026-11-18). Preserve the
complete list for the installed SDK, OS and architecture, including transitive
dependencies. CI's `--with-deps` installation follows the
[official setup](https://playwright.dev/docs/ci) (accessed 2026-11-18);
extracting only shared libraries does not reproduce it.

For a private runtime, keep provisioning scoped to the job rather than changing
host packages, font settings or browser sandbox settings. Register the SDK fonts
where the browser can discover and read them, and retain this evidence:

- Record the source SHA, actual SDK/browser versions, target OS/architecture,
  native and font/tool package versions, and hashes of extracted archives and
  font files.
- Record the page's font-family stack and the resolved family, style and file
  for its requested fonts and fallbacks in the browser's effective environment.
  A font directory's existence or a parent-process probe is insufficient.
- Run the existing visual checks in every affected browser and theme with
  unchanged goldens, assertions and tolerances, zero retries, and recorded
  browser exits. Preserve expected/actual/diff images and failed attempts before
  considering a baseline refresh. A passing lifecycle subset does not establish
  full visual-runtime equivalence.

The [font comparison recorded in #2676](https://github.com/bioedca/Yeliztli/issues/2676),
performed during [#2675](https://github.com/bioedca/Yeliztli/pull/2675), illustrates
this distinction: eight unchanged Chromium visual checks failed
with SDKs 1.58.2 and 1.64.1 when the overlay omitted SDK tool fonts. Registering
those fonts privately made all eight pass on both SDKs with zero retries and no
golden or tolerance changes. This identifies that harness mismatch; it does not
explain the historical WebKit closure in #2672.

The two historical 1.58.2 runs in [#2672](https://github.com/bioedca/Yeliztli/issues/2672)
each passed 38 cases and failed a different WebKit case before application
assertions. Neither is a pass. `TargetClosedError` alone does not identify a
native crash: inspect the exit code/signal and whether shutdown was requested.
Distinguish loader failures, native faults, test timeouts and normal server
cleanup; SIGKILL alone does not establish an out-of-memory kill.

History-navigation tests must wait for the destination route to settle before
pressing Back. The Settings index replaces `/settings` with `/settings/updates`;
matching only the intermediate URL can race that replacement. The cross-browser
Back/Forward test retains its URL checks and 30-second timeout and additionally
checks the final Settings route and the visible destination headings. A URL-wait
timeout with a normally exiting browser is separate from a native lifetime fault.

Upstream [WebKit r2370](https://github.com/microsoft/playwright/pull/43084), shipped
with Playwright 1.64, includes an [EGL exit-race fix](https://github.com/WebKit/WebKit/pull/74728)
(accessed 2026-11-10). The [upstream report](https://github.com/microsoft/playwright/issues/42940)
describes crashes after pages close, while #2672 failed before assertions.
Passing instrumented repetitions or selecting a compatible newer runtime does
not prove that this race caused the historical failures.

!!! tip "Verify UI changes before merging"
    Because the E2E suite is Tier 2 (merge-time), it's worth driving a UI change in a real
    browser before merging so a frontend regression doesn't surface only at merge.

### Error colors and contrast

Use `text-destructive-text` for error text and destructive action icons, including
hover/focus variants. Its light and dark values are separate from
`bg-destructive`, destructive borders, and `text-destructive-foreground` (text on
a solid destructive fill). Changing a foreground color must not silently change
those surfaces. Check opacity modifiers against the actual background too:
`StorageStep` uses an 80% opacity explanation on a tinted error panel.

`tests/e2e/error-text-contrast.spec.ts` drives synthetic malformed uploads,
populated overlay deletion controls, and blocked storage setup in both themes.
It checks that the semantic foreground is actually applied (rather than inheriting
neutral text), real error text with Axe, enabled/hover/focus contrast, and the
disabled pending state and recovery. The spec serves unmodified production HTML,
JavaScript, and CSS from `frontend/dist` through Playwright routes; API responses
remain synthetic. This checks the shipped styles without relying on Vite's
unflattened development CSS. Build before running it in all three browser projects:

```bash
npm --prefix frontend run build
npx playwright test tests/e2e/error-text-contrast.spec.ts
```

`tests/e2e/overlay-development-hover.spec.ts` separately checks the populated
Delete control under Vite's development CSS in both themes. It requires the Vite
client and injected source stylesheet, checks the actual 10% destructive tint,
and audits foreground contrast. Run it with
`npx playwright test tests/e2e/overlay-development-hover.spec.ts` in all three
browser projects. Keep the production-asset spec above unchanged: passing one
serving mode does not establish the other.

The development discrepancy in [#2667](https://github.com/bioedca/Yeliztli/issues/2667)
reproduced on identical public app bytes and native/font inputs with Playwright
1.58.2 / WebKit 26.0: hover matched but the background stayed transparent. The
locked 1.64.1 / WebKit 27.2 applied the tint in both themes; both SDKs applied it
with production assets. This comparison supports retaining the SDK upgrade and
the development regression, without a global hover-query override or an
application CSS workaround. It does not identify the first fixed browser version.

The hover background must change when `(hover: hover)` matches, and remain
unchanged when it does not; Chromium and WebKit must exercise the positive path.
Headless Firefox can report `hover: none`. This follows [Tailwind's hover media
query](https://tailwindcss.com/docs/upgrade-guide#hover-styles-on-mobile)
(accessed 2026-11-10), while color and contrast assertions run in either state.

Normal-size text needs at least 4.5:1 contrast, including on hover and keyboard
focus; inactive controls are exempt from that numeric requirement but must retain
their disabled behavior. See [WCAG contrast minimum](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)
(accessed 2026-11-09). Keep audits populated and assert that contrast checks ran;
do not add exclusions to hide an error-color regression. The separate primary
button color decision remains tracked in
[#2525](https://github.com/bioedca/Yeliztli/issues/2525).

### Informational badge contrast

`tests/e2e/required-badge-contrast.spec.ts` checks the Required label on a
downloaded database card while another synthetic download is in progress. It
serves the production bundle and verifies both themes in all browser projects.
The informational label remains eligible for contrast checking while sibling
controls are disabled; their inactive-control exception does not exempt it.
See [WCAG 2.2 contrast minimum](https://www.w3.org/TR/WCAG22/#contrast-minimum)
(accessed 2026-11-18). Keep the target visible and populated, reject incomplete
audits, and require a non-empty contrast pass rather than excluding the badge.

```bash
npm --prefix frontend run build
npx playwright test tests/e2e/required-badge-contrast.spec.ts
```

### Native select contrast

`tests/e2e/individual-select-contrast.spec.ts` covers the populated Settings
Individual selector in all three browser projects. Native WebKit can paint a
light field while its computed CSS background is dark, so an Axe contrast pass
alone can miss unreadable selected text. The spec decodes the actual control
screenshot in the browser, measures its dominant opaque interior fill against
the solid CSS foreground, and requires visible label paint. The interior must
have a measurable dominant fill; empty or ambiguous captures cannot pass.
The ratio uses the [WCAG relative luminance definition](https://www.w3.org/TR/WCAG22/#dfn-relative-luminance)
(accessed 2026-11-18).

Keep the contrast target enabled, opaque, visible, and populated. Test the
disabled pending state separately: the spec defers link and unlink responses,
checks their methods, payloads, response bodies, and resulting selection, then
checks the enabled label again. It also covers app preferences opposite to the
OS preference and live system-theme changes without reloading. Build production
assets before running it:

```bash
npm --prefix frontend run build
npx playwright test tests/e2e/individual-select-contrast.spec.ts
```

## Test conventions

The suite follows specific assertion standards (assert real values, not just non-null;
carriage-gated modules need `hom_ref` negative controls). See [contributing](contributing.md).

When a click starts an asynchronous API mutation, register a
[`page.waitForResponse`](https://playwright.dev/docs/api/class-page#page-wait-for-response)
wait before clicking (accessed 2026-11-17). Match the endpoint and request method,
then await the response and assert its status and request payload. Click completion
does not establish that a route handler has captured the request: the wizard
download checks in [#2687](https://github.com/bioedca/Yeliztli/issues/2687) reproduced
that ordering with delayed dispatch. Keep the exact selected-database and streamed
progress assertions; retries or a fixed sleep do not establish request completion.

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
