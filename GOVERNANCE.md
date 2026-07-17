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
  pull request. A change merges once it meets the [contribution
  requirements](CONTRIBUTING.md) — it passes the review gate and all required CI
  checks are green — and a maintainer approves it.
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

## Contributor agreement (DCO, not a CLA)

Contributions are accepted under a **Developer Certificate of Origin (DCO)**
rather than a Contributor License Agreement. By contributing, you certify that
the contribution is yours to submit and may be included under the project's
[MIT license](LICENSE). We prefer a DCO because a CLA is a barrier to
first-time contributors and creates a maintainer/contributor power imbalance. In
practice: the pull-request template includes a DCO acknowledgement checkbox —
check it to confirm.

## Becoming a collaborator

There's no formal ladder yet. The path is simply: contribute good issues and
pull requests, help review and triage, and the maintainer will offer write
access when there's a track record of sound, self-reviewed work. Non-code
contributions — documentation, triage, reproducing bugs, scientific review —
count fully.

## Changing this document

This governance model is itself open to discussion. Propose changes via a Design
proposal issue or a pull request editing this file.
