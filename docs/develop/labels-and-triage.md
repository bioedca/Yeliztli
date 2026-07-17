# Labels & triage

This page explains how Yeliztli labels and triages issues and pull requests, so
the tracker stays a legible, prioritised list of real work. Labels are organised
into independent **axes** — an issue usually carries one label from several of
them (a *type*, maybe a *priority*, an *area*, and so on). Every label has a
name, a description, and a colour so it's self-explanatory and scannable.

## The axes

### Type — *what kind of work is this?*

| Label | Meaning |
| --- | --- |
| `bug` | Something isn't working. |
| `enhancement` | A new feature or an improvement to an existing one. |
| `documentation` | Docs (site, README, or in-app text) are wrong, missing, or unclear. |
| `scientific-validity` | Correctness of a biological, statistical, or clinical fact (allele, strand, effect direction, frequency, threshold, citation). |
| `bioinformatics` | A genomics / variant-interpretation domain concern. |
| `test-gap` | A test passes even when the behaviour it names is broken (a vacuous or non-discriminating test). |
| `dead-code` | Unused/unreachable code to remove (maintainability; no behaviour change). |
| `question` | Needs more information before it's actionable. |
| `discussion` | Needs a design decision or team discussion before implementation. |
| `epic` | A large body of work spanning multiple issues/PRs; break it into children. |

### Priority — *how important is it?* (MoSCoW)

| Label | Meaning |
| --- | --- |
| `priority:must` | Without this, there is no usable release. |
| `priority:should` | Important, but a release works without it. |
| `priority:could` | Nice to have; not essential. |
| `priority:wont` | Not this time (recorded, deliberately deferred). |

Work `must`s first, then `should`s if time remains. Priority and severity are
**separate** axes — a low-severity bug can still be a `must` (and vice versa).

### Severity — *how bad is the impact if it's real?*

| Label | Meaning |
| --- | --- |
| `severity:low` | Cosmetic or provenance-only; no wrong result. |
| `severity:medium` | Wrong reference biology in shipped data/fixtures, but no wrong user-facing clinical call today (gated, withheld, or test-only). |
| `severity:high` | A wrong user-facing clinical/risk call is emitted. |
| `severity:critical` | A wrong high-stakes call is shipped to users — must fix before any release; non-deferrable. |

> The project historically used a single `medium-severity` label; it has been
> consolidated into this graded `severity:*` axis (`medium-severity` → `severity:medium`).

For security/threat items, severity is complemented by the threat-modelling
lens (likelihood × impact); a `security` label marks those, and `severity:critical`
is the non-deferrable class.

### Status — *where is it in the flow?*

| Label | Meaning |
| --- | --- |
| `needs-triage` | Newly filed; awaiting a first look. The default on new issues. |
| `status:blocked` | Blocked by another issue or an external dependency. |
| `duplicate` | Already tracked elsewhere. |
| `invalid` | Not reproducible / not applicable. |
| `wontfix` | Acknowledged, but won't be worked on. |

### Area — *which part of the system?*

`area:pgx`, `area:ancestry`, `area:variant-annotation`, `area:risk-panels`,
`area:prs`, `area:frontend`, `area:ci`, `area:data-bundle`, `area:setup-wizard`,
`area:docs`. Area labels route work to the right expertise; an issue can carry
more than one.

### Onboarding — *good ways in for new contributors*

| Label | Meaning |
| --- | --- |
| `good first issue` | A self-contained, beginner-friendly starting point. |
| `help wanted` | The maintainer would welcome help here. |

### Evidence & automation

- `evidence-cited` — the issue carries literature / authoritative-source citations
  (paired with `scientific-validity`).
- `dependencies` — a dependency update (used by Dependabot).
- `slow-tier-regression` / `cross-os-regression` — **filed automatically** by the
  nightly workflow when a slow-tier or cross-OS test regresses. Don't apply these
  by hand.

## The triage flow

1. **File** — a new issue comes in through a [template](https://github.com/bioedca/Yeliztli/issues/new/choose),
   which pre-applies its type label plus `needs-triage`.
2. **Triage** — a maintainer confirms it's actionable and one problem, then sets
   the remaining axes: severity (for bugs/science), priority, area, and — if it's
   a duplicate/invalid — the resolution label. `needs-triage` comes off once triaged.
3. **Plan** — the issue is placed on a milestone (a release it's scoped to) and,
   once GitHub Projects is set up, added to the board with its priority/estimate.
4. **Do** — a pull request references the issue (`Closes #123`), passes the review
   gate and required CI, and merges. Closing the PR closes the issue.

Only maintainers/collaborators can set labels, milestones, and board fields
(GitHub gates those behind repository permissions); anyone can contribute the
issue or PR itself.

## Filtering

Labels are how you slice the backlog. GitHub's search supports combining and
excluding labels — for example:

- All open bugs: `is:issue is:open label:bug`
- Science defects awaiting triage:
  `is:issue is:open label:scientific-validity label:needs-triage`
- PGx work that isn't a bug: `is:issue is:open label:area:pgx -label:bug`
- Beginner-friendly entry points: `is:issue is:open label:"good first issue"`

## See also

- [Contributing](contributing.md) — the test-quality and scientific-accuracy standards.
- [CONTRIBUTING.md](https://github.com/bioedca/Yeliztli/blob/main/CONTRIBUTING.md) — the full contribution workflow and review gate.
- [GOVERNANCE.md](https://github.com/bioedca/Yeliztli/blob/main/GOVERNANCE.md) — roles, decision-making, and who can triage.
