# Agent Development Contract

## Authority and priorities

- Keep `main` releasable. Order decisions by privacy/safety, scientific correctness, functional correctness, test integrity, maintainability, then delivery speed.
- Review-route schema v2 is the human-gated legacy route: only the human contributor certifies DCO, an independent human maintainer supplies exact-head approval, and an agent merges only with explicit authorization after every gate passes. Schema v3 uses automated contribution provenance and the dedicated publisher App's exact-head success as merge authority; it requires neither DCO, human approval, nor per-PR human merge authorization. Hosted-provider output alone never implies merge readiness; it becomes merge-relevant only through the schema-required exact-head publisher validation.
- Follow the exact repository issue and PR forms. Never commit to `main`, weaken a test for green CI, fabricate data, expose secrets/genotypes, or mix unrelated work.
- Do not start issue work unless covering `main` CI is terminal green. Allow one merge in flight; the next waits until the prior exact merge SHA's covering `main` run is terminal green.

## Work selection and concurrent ownership

- Prefer an assigned issue. Otherwise choose highest severity, then MoSCoW (`must`, `should`, `could`), then the oldest unclaimed actionable issue.
- For a fleet, one coordinator is the authoritative allocator and assigns distinct issue numbers; comment election is only a best-effort fallback, not a linearizable lock. An autonomous queue loser may try the next eligible issue, at most five elections and only before creating a goal/worktree; an explicitly assigned loser stops.
- Before starting, successfully fetch and verify fresh `origin/main`; inspect the issue, linked PRs, Project, claims, branches, worktrees, and current CI. Reuse valid interrupted work. If the ref cannot be verified or `main` is not green, stop new work.
- Claims are cooperative coordination, not a security boundary: sessions share GitHub authority. Use a `yeliztli-claim:v1` issue comment, public `yz-<UUIDv4>` agent ID, and monotonic `generation`. Only a write-capable collaborator or approved App/bot is eligible; the GitHub author authenticates the record, not the ID. Publish only sanitized phase/state, public SHAs, and proposed branch/worktree aliases—never host/user/path, internal IDs, credentials, private data, vulnerability details, or cluster internals.
- Join the highest non-expired provisional generation; otherwise use exactly `max_seen_generation + 1`. Only named handoff or human reassignment may open a higher generation while older ownership is live. Post `candidate`, wait five seconds, refetch every page twice, and elect lowest immutable comment ID in that generation; the winner marks `active` and refetches again. Stop before goal/worktree/code writes unless confirmed winner.
- `candidate`/`accept` expire after two minutes; `active`/`waiting`/`handoff` are live for 90 minutes from server `updated_at`. Heartbeat every 30 minutes and on state/SHA/PR changes. Before first edit, after resume, and before commit, push, PR mutation, Slurm submission, or merge, refetch and fence on unique winning comment ID + generation; a fetch failure or mismatch stops all writes.
- Before handoff the predecessor stops writes, names exactly one successor ID and `successor_generation = generation + 1`, and never reactivates; competing eligible accepts still elect lowest comment ID. `HUMAN-REASSIGN:` must name one successor ID/generation or explicitly open one lowest-ID election after quarantining predecessor writes/jobs; agents never author that phrase. Terminal records never reactivate.
- The confirmed owner uses one issue = one persistent goal = one branch = one worktree = one PR. Start with `$run-yeliztli-issue #N` or `/goal Resolve #N end to end; done means authorized merge, green main, issue/Project update, and safe cleanup.`
- Every agent that may edit, stage, commit, or run write-producing checks gets a uniquely named dedicated branch/worktree from current `origin/main`; never share a mutable worktree. Secondary writers declare disjoint file/symbol scope; overlaps serialize, and only the primary owner integrates commits.
- Read-only research may share a checkout; reviewers inspect an immutable SHA/diff or clean detached worktree, never a moving checkout. Fetch at start, before final review, before route finalization, and immediately before merge.
- If behind, merge `origin/main` into the issue branch inside its worktree; never rebase a published branch or force-push. Resolve conflicts there, rerun affected gates, and restart the full review route on the new SHA.
- Preserve unrelated dirty files, stashes, branches, and agent work. Never force-push, discard, delete, or rewrite work whose ownership is uncertain.

## Agile delivery loop

1. Turn the issue into a user story, acceptance criteria, risks, and a reproducible baseline. Escalate a missing material design choice to a design issue or Discussion.
2. Deliver the smallest complete vertical slice, including all data, API, UI, docs, and migration effects required by the user-visible contract.
3. Work in short feedback loops: test first when practical, commit cohesive increments, publish early after the privacy gate, and keep the draft PR current.
4. Move through the Project's equivalent of ready, in progress, review, and done. Limit WIP; finish or durably pause one issue before claiming another.
5. Done means code + discriminating tests/validators + docs + provenance + selected review route + required CI + route-authorized merge + green post-merge checks + issue/Project update + cleanup.
6. Turn repeated friction or regressions into a concrete issue, automation, test, skill, or contract improvement instead of relying on memory.

## Tool routing

- Use Context7 for external library/framework/SDK/API/CLI/cloud syntax, setup, configuration, migration, version, or tool-specific debugging. Unless the user supplied `/org/project[/version]`, run `npx ctx7@latest library <official-name> "<full question>"`; then run `npx ctx7@latest docs <id> "<full question>"`.
- Run Context7 outside the sandbox, at most three commands per question, with no secrets. Retry DNS/network failure outside the sandbox; on quota failure report it and suggest `npx ctx7@latest login` or `CONTEXT7_API_KEY`, never guess.
- Do not use Context7 for refactoring, scratch scripts, business-logic debugging, code review, or general programming concepts.
- When `graphify-out/graph.json` exists and matches the inspected commit, query Graphify before raw searches. If stale/unconfirmed, use raw repository search. Keep generated graph changes out of issue commits.
- For UI changes use `$playwright` in a real browser, verify the affected flow and accessibility, then run targeted Playwright tests. Keep sanitized artifacts under ignored `output/playwright/`.
- Prefer the GitHub connector for GitHub state/actions, local `git` for branches/worktrees, and `gh` for Actions logs or unsupported operations.
- Put questions/early ideas in Discussions, actionable work in issues, and durable guidance in MkDocs. Wiki notes are not source of truth.
- Use Projects for ownership, priority, status, and review waiting. Treat Actions, CodeQL, secret scanning, Dependabot, and Insights as feedback; never game metrics or auto-merge risk.

## Cluster and Slurm work

- Offload heavy CPU/GPU work through Slurm, never a login shell. `ssh zero` is direct; `ssh one` and `ssh two` traverse `zero`. Connect serially if the jump host rejects concurrent starts.
- Before each submission inspect `sinfo`, `scontrol show nodes`, storage capacity, path visibility, and equivalent recorded/live jobs. Use `compute` for CPU and `gpu` for GPU only when live state supports it; reuse valid work rather than duplicate it.
- Use `sbatch` with finite `--time`, `--cpus-per-task`, `--mem`, `--partition`, job name, and log paths; request `--gres=gpu:a4000:N` only when needed and cap arrays.
- Use verified shared storage for portable jobs. Treat `/localscr` and `/localdata` as node-local. Namespace immutable inputs and task-owned outputs/logs by issue, commit, and job; never overwrite existing results.
- Record alias/node, workdir, commit, command, environment, inputs, outputs, resources, and job ID. Monitor with `squeue`; verify `sacct`, logs, exit state, and output completeness. Failure, timeout, or missing output is not a pass.
- Treat clusters as remote: stage only public, synthetic, or approved non-genomic data—never real genotype/variant data or credentials. `scancel` only an exact confirmed user-owned job; never blanket-cancel or touch another user's work.

## Scientific evidence, privacy, and security

- Never encode biological, statistical, or clinical claims from memory. Tests verify software behavior; they do not scientifically validate a claim.
- Send Consensus/Scite only approved public or synthetic sanitized inputs. Never submit or durably store PII, secrets, real genotypes, or restricted data; evidence packets contain sanitized payloads only.
- Invoke Consensus (`@App-6943e6f4a928819195962de16fb9ffe4`) and `@Scite` first; then the narrowest Life Science Research skill, and NGS Analysis only for material sequencing inputs/assays/pipelines. Record unavailable/quota fallbacks.
- Store queries, durable resolvable source-appropriate IDs (for example PMID, DOI, database accession, or trial ID), versions/builds, access dates (`YYYY-MM-DD`), licenses, claim mapping, and repository-relative paths to sanitized public/synthetic source payloads in `data/science-evidence/<date-slug>/`; paths must not expose host, user, or internal locations. Cite primary papers/authoritative databases, not discovery tools; one paper surfaced twice counts once. Check corrections/retractions.
- High-stakes facts require two agreeing sources that do not share the same cohort, dataset, or upstream assertion. On conflict or insufficient evidence, withhold the result and file a scientific-validity issue; never guess or placehold.
- Keep primary storage/live-annotation joins on GRCh37/b37 and repository strand/allele conventions. Preserve source-native provenance, build-detection fixtures, and documented build-specific pipelines such as GRCh38 LAI/Gnomix.
- Before every push inspect staged content/history for secrets, real genotypes, raw payloads, generated artifacts, and oversized data. Use synthetic fixtures; report vulnerabilities privately.
- Assess threat likelihood, impact, affected data, and mitigation cost. Resolve blocking CodeQL, secret, dependency, or privacy findings before merge.

## Verification contract

- Behavior/data defects require a regression test that fails without the change. Assert values/bodies, both sides of filters, non-empty collections before loops, and the production path; use the applicable validator for docs/metadata.
- Analyses that emit a finding only when a risk allele is carried require a `hom_ref` non-carrier negative control. Never delete or relax an assertion merely to fit implementation or missing reference data.
- Bootstrap a clean worktree with `python -m pip install -e ".[dev,docs]"`; run focused checks, then every affected workflow job at its declared versions. Local minimum: `ruff check backend/ tests/`; `ruff format --check backend/ tests/`; `vulture`; `python -m pytest tests/backend/ -v --tb=short -m "not slow"`.
- Frontend/UI: `(cd frontend && npm ci && npm run lint && npm run knip && npm run test:ci && npm run build)`; at repository root run `npm ci`, install the required Playwright browser when absent, then `$playwright` and targeted `npx playwright test`. Docs: `mkdocs build --strict`.
- Run affected smoke-install, Docker, actionlint, security, and Tier-2 gates when applicable. Do not claim a check passed unless it ran; record every skip/unavailable gate and why.

## Hard review routes

Classify at draft creation and before merge; mixed/uncertain scope rises. These are agent-hard gates even if platform rules are weaker: report rule drift, never use it as permission. All reviews bind to the head SHA.

| Route | Scope | Preferred hosted lane |
| --- | --- | --- |
| Low | Text-only docs/comments or mechanical metadata; no behavior, public-contract, science, security, dependency, or workflow change | Copilot |
| Standard | Routine code, tests, UI, refactor, or bug fix not protecting a load-bearing area | hosted Codex |
| Load-bearing | Science/clinical/reference data or their tests; privacy/security/auth; schema/migration/data loss; concurrency; dependencies; updater/installer/release; CI/workflows/permissions; core architecture; broad/hard-to-revert change | CodeRabbit structured clean review |

- New PRs use review-route schema v3. Preserve an existing schema-v2 PR exactly as the legacy human-gated route; do not silently convert its body or waive its DCO, independent exact-head human approval, exact `/validate-route`, checks, or explicit merge authorization. A v3 PR must not modify `.github/workflows/review-route-invalidation.yml`; use v2 for that PR-controlled signal workflow.
- Every route requires a final local review on the committed head and exactly one selected hosted provider. Providers substitute for one another on every route; prefer the table's lane, but select an available alternative instead of adding speculative parsers or waiting on quota. Extra reviews are advisory, and every resulting thread must still be resolved; never trigger an unselected advisory lane.
- For schema v3, this contract grants the confirmed owner standing authorization to trigger exactly the provider selected in the canonical PR body and transmit only the public PR diff, review-relevant public repository files, and sanitized public PR metadata for exact-head review. It does not authorize unused providers or submission of secrets, local-only or unpushed work, PII/genotypes, restricted data, vulnerability details, or cluster/internal context.
- For schema v3, push the unchanged reviewed head, mark the draft ready, select one provider in the PR, record its exact 40-character head SHA and immutable completion time, and mark both cells for every unused provider `N/A`. Evidence must be trusted, unedited, provider-authored, author-independent, and exact-head; the accepted envelopes are deliberately narrow:
  - Codex: an exact-head empty formal approval with zero attached comments, or its canonical immutable clean comment.
  - Copilot: an exact-head, unedited concise findings envelope whose two trusted reviewed-file counts both equal GitHub's changed-file count and whose trusted generated- and attached-comment counts are zero.
  - CodeRabbit: an exact-head, unedited structured clean review with zero attached/actionable comments, no ignored files, and a trusted selected-file count equal to GitHub's changed-file count.
- Use `/review`, `codex review --uncommitted`, or `$coderabbit:code-review` iteratively. The final local gate is `codex review --base origin/main` (or an equivalent CodeRabbit review) on the committed head in its dedicated worktree. When Codex is selected, re-fetch the selected lane and head, then post exact `@codex review`; for a canonical clean comment, GitHub's full-OID lookup must prove the current head's canonical abbreviation is no longer than the bot's 10-hex marker.
- Invalidation events publish `Review Route: pending` on the immutable event head and current PR head when available. Pending is sufficient invalidation: do not build historical Actions-run ledgers, scan workflow history, or encode policy in run titles. A closed PR or closed lifecycle event is invalidation-only: keep the affected head pending, never attempt validation or success, and never overwrite a replacement open PR's status. Privileged jobs never check out or execute PR-controlled code or consume PR artifacts/caches.
- After every thread is resolved, a live write-capable finalizer must post the exact unedited `/validate-route`. Validation uses trusted default-branch code and one fresh complete API snapshot, then immediately before success re-fetches exact head/source identity, body/schema, provider evidence, threads, absence of an active change request, finalizer permission, duplicate-head ownership, and trusted `main`. After success, perform one fresh audit; any change immediately replaces success with pending and fails the run. Prefer clear fail-closed errors and another provider over output fallbacks.
- Accept `Review Route` only when its exact-head success comes from the dedicated publisher App, never the generic GitHub Actions App. Schema v2 additionally requires its completed DCO and independent exact-head human approval; schema v3 does not.
- Every head-changing push invalidates the selected hosted review and schema-applicable merge decision; restart on the new SHA. It also invalidates affected test, UI, science, and Slurm evidence unless rerun or an independent maintainer accepts documented commit/input independence.
- Fix every blocking finding. Only false-positive/nonblocking findings may receive a documented independent-maintainer disposition.
- Immediately before merge fetch/verify ancestry and require the current PR head SHA to pass `CI Required`/`Lint`, applicable security, the schema-required checklist, no blocking thread, and dedicated publisher App `Review Route` success. Schema v2 also requires DCO, independent exact-head human approval, and explicit merge authorization; schema v3 requires none of those three. If `origin/main` advanced, sync and restart gates/reviews.

## PR, merge, and lifecycle

- The draft PR must contain `Closes #N`, what/how/why, acceptance evidence, tests, applicable scientific/Slurm provenance (otherwise explicit `N/A`), route evidence per reviewer/SHA, residual risks, and focus areas. Schema v2 leaves DCO for the human contributor; schema v3 retains the canonical provenance structure in draft and records the exact issue, head, selected provider, test evidence, and public agent claim ID before ready/finalization.
- Merge queue is unsupported. Squash with an imperative subject ending `(#<PR>)`; never bypass the ruleset. Merge schema v2 only after explicit human authorization; schema v3 may merge once its exact-head App route status and every other gate pass. After merge, serialize the queue until the resulting exact merge SHA's Tier-2 macOS/E2E and all covering `main` checks are terminal green.
- On post-merge failure keep the goal/worktree, notify the maintainer, and open/link the corrective issue/PR; do not revert, clean up, or start another merge without authorization.
- After green, close/update the issue and Project; remove only the proven-merged branch/worktree and task artifacts, then prune stale metadata.
- For discovered defects, search first and use the exact bug, scientific-validity, feature, docs, or design form. Link the originating work and defer unless it blocks safety/correctness; never disclose security details, credentials, or genotype data.

## Goal and agent controls

- `/goal` views state; `/goal edit` corrects scope; `/goal pause`/`resume` handle temporary stops; `/goal clear` cancels only after durable handoff. A different active goal must finish, pause with handoff, or be explicitly replaced before another starts.
- Ask Codex to start bounded subagents with named scope/worktree; use `/agent` or the panel to inspect, switch, steer, or stop them. `/stop` stops background terminals, not goals or Slurm jobs.
- Before pause/clear/stop, record branch, PR, SHA, checks, and next step. Every continuing job needs an active acknowledged monitoring owner other than the stopping agent and a checkpoint; otherwise cancel its exact ID and confirm terminal state. On resume reconcile `squeue`, `sacct`, logs, outputs, commit, and inputs before accepting/resubmitting.
