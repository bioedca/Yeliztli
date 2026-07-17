---
applyTo: "tests/**"
---

# Test-quality instructions

When writing or reviewing tests, enforce the project's test-assertion standards —
a test must **fail when the behavior it describes breaks**. These mirror the
CodeRabbit `tests/**` review guidance in `.coderabbit.yaml`; flag new or modified
tests that violate them.

- **No lone `assert x is not None`** as the only assertion on a value-producing
  function. Most functions return something for any input, so this asserts "it
  ran," not "it produced the right answer." Assert the **value** — the field, the
  rendered string, the returned set, the computed number, the exact diplotype
  (not `'3' in str(genotype)`), the compiled SQL (with `literal_binds`), or the
  VCF `REF`/`ALT`/`GT`.
- **No lone `assert response.status_code == 200`** on an endpoint that returns
  data. A `200` with a wrong, empty, or duplicated body still passes. Assert the
  **body** too — the specific fields, counts, or rows expected. (Both are fine as
  a *first* precondition line when followed by real assertions.)
- **Two-sided filter checks**: assert the excluded row is *absent*, not only that
  the returned rows match.
- **No vacuous loops**: `for item in items: assert ...` passes when `items` is
  empty — assert `items` is non-empty first.
- **Drive the production path**: don't hand-overwrite the column under test in an
  "end-to-end" fixture, or the test won't fail when that path regresses.
- **`hom_ref` negative controls** for carriage-gated modules: seed a non-carrier
  (`hom_ref`) Pathogenic variant and assert it does **not** surface as a finding.
  Use the shared builders in `tests/backend/_carriage_fixtures.py`
  (`hom_ref_pathogenic_row` / `het_pathogenic_row`); dosage-based modules use the
  equivalent "all-reference genotype → no finding" control.
- **Self-documenting perf assertions**: if a relaxed regression ceiling differs
  from the real product target, inline the target next to the assertion.

Do not weaken or delete an assertion to make a failing test pass — fix the code,
or if the test encodes the wrong expectation, say so explicitly with evidence.
