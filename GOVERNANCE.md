# Governance

This document records how decisions get made in Yeliztli, so the process is
written down rather than assumed. It's intentionally lightweight and matched to
the project's current size — expect it to grow as the community does.

## Project structure

Yeliztli is currently a **single-maintainer project** led by its original
author (**[@bioedca](https://github.com/bioedca)**), in the
"benevolent maintainer" model. The maintainer owns the project's direction,
reviews and merges changes, and is the final decision-maker. Contributions from
anyone are welcome and encouraged.

As more people contribute regularly, this will move toward a small group of
maintainers with shared merge rights and explicit decision-making by consensus.

## Roles

- **Maintainer** — sets direction and priorities, triages issues, reviews and
  merges pull requests, cuts releases, and administers the repository (labels,
  milestones, branch settings). Currently @bioedca.
- **Contributor** — anyone who opens an issue or a pull request. Contributors
  do not need any special permissions to participate; you contribute through
  issues and PRs from a fork.
- **Collaborator** — a contributor granted write access after a track record of
  good contributions, so they can be requested as a reviewer and help triage.
  See "Becoming a collaborator" below.

## Decision-making

- **Everyday changes** (bug fixes, docs, well-scoped features): decided in the
  pull request. A legacy v2 change still needs maintainer approval and explicit
  merge authorization. A v3 change may be squash-merged without per-PR human
  authorization once the exact-head hosted-review route, dedicated publisher
  App status, resolved threads, and all required CI/security checks are green.
- **Design decisions** that need discussion before code: open a **Design
  proposal** issue or an **Ideas** discussion, state the motivation and the
  options, and let it be discussed. The maintainer records the outcome.
- **Disagreements**: the aim is rough consensus, guided by the project's
  priorities (correctness first — especially scientific correctness — then
  reviewable, atomic change; see the priority order in `CONTRIBUTING.md`). Where
  consensus can't be reached, the maintainer decides, and explains why.

The maintainer's north star is that **`main` is always releasable**: nothing
merges while its required checks are red, and scientific claims are verified
against the literature before they're encoded (never on recall).

## Scientific correctness

Because Yeliztli's output can look like clinical information, the project holds a
higher bar for anything resting on a biological, statistical, or clinical fact.
Such changes must carry a citation to an authoritative source, and high-stakes
claims (allele direction/strand, risk-allele identity, a metabolizer call, a
shipped clinical threshold) need **two independent, agreeing sources**; on
disagreement we **withhold and flag** rather than pick a side. This is a
governance rule, not just a coding convention — see `CONTRIBUTING.md` and the
`scientific-validity` / `evidence-cited` labels.

## Contribution provenance

Existing pull requests remain on the human-gated v2 contract until explicitly
migrated to v3, including their DCO acknowledgement, independent approval, and
maintainer merge decision. New v3 pull requests use operational
automated-contribution provenance—issue, exact head, hosted reviewer, tests, and
agent claim ID—without a legal or DCO attestation. The exact-head provider and
publisher-App gates do not weaken the repository's scientific, privacy,
security, or test requirements.

## Becoming a collaborator

There's no formal ladder yet. The path is simply: contribute good issues and
pull requests, help review and triage, and the maintainer will offer write
access when there's a track record of sound, self-reviewed work. Non-code
contributions — documentation, triage, reproducing bugs, scientific review —
count fully.

## Changing this document

This governance model is itself open to discussion. Propose changes via a Design
proposal issue or a pull request editing this file.
