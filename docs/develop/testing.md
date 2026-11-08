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

The AncestryDNA real-bundle fixture copies only ClinVar rows alongside the VEP
bundle. It contains no MONDO/HPO associations, so its unstamped MONDO/HPO warning
does not indicate lost fixture coverage and must not be silenced with invented
installation provenance.

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
