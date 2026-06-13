# Contributing

Yeliztli keeps a few conventions that make the test suite **honest** — a test should fail when
the behaviour it describes breaks. The most load-bearing ones are below.

## Test assertion standards

Two anti-patterns defeat a test's purpose, because they pass for almost any non-crashing code:

- **`assert x is not None` as the *only* assertion** on a value-producing function. Most
  functions return *something* for any input, so this asserts "it ran," not "it produced the
  right answer." Assert the **value** — the field, the rendered string, the returned set, the
  computed number.
- **`assert response.status_code == 200` as the *only* assertion** on an endpoint that returns
  data. A `200` with a wrong, empty, or duplicated body still passes. Assert the **body** too.

Both are fine as a *first* line (a precondition) when followed by real assertions. Concretely:

- For a value-producing function, assert the value — and where a SQL/text/JSON artifact is
  produced, assert its content (compile a query with `literal_binds` and assert the rendered
  SQL; assert VCF `REF`/`ALT`/`GT`; assert the exact diplotype, not `'3' in str(genotype)`).
- Prefer **two-sided** checks for filters: assert the excluded row is *absent*, not only that
  the returned rows match.
- Don't guard a loop with no membership check — `for item in items: assert ...` passes
  vacuously when `items` is empty. Assert `items` is non-empty first.
- Don't hand-overwrite the column under test in an "end-to-end" fixture; drive the production
  path so the test fails if that path regresses.
- Keep timing/perf assertions self-documenting: inline the real product target next to a
  relaxed regression ceiling.

## `hom_ref` negative controls (carriage-gated modules)

A genotyping chip reports a call at *every* probe regardless of whether the person carries the
variant, so a ClinVar-Pathogenic record at a homozygous-reference position must **not** surface
as a finding. Every analysis module that emits carriage-dependent findings should have at least
one test that seeds a non-carrier (`hom_ref`) Pathogenic variant and asserts it is **absent**.

Shared builders live in `tests/backend/_carriage_fixtures.py` (`hom_ref_pathogenic_row` /
`het_pathogenic_row`). Risk-genotype (dosage-based) modules use the equivalent "all-reference
genotype → no finding" control.

## Enforcement

These standards are **advisory** — surfaced as review comments by CodeRabbit (path-scoped to
`tests/**`), intentionally **not** CI-blocking, since the existing suite predates the
convention. The goal is to stop *new* vacuous assertions at review time and migrate legacy ones
opportunistically.

## Scientific accuracy

Yeliztli's analyses rest on biological and statistical facts. When adding or changing logic
whose correctness depends on such a fact — or asserting one in docs — verify it against the
peer-reviewed literature and carry a citation, rather than relying on recall. See the
[interpretation reference](../modules/interpretation-reference.md) and
[ancestry methods](../ancestry-methods.md) for the citation style used in user-facing docs.
