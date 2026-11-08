# Copilot review evidence

Schema v3 accepts two observed Copilot completion formats. Both require the
authenticated Copilot bot, `isCopilotCodeReview=true`, `COMMENTED` state, the exact
current head, an unedited review, zero attached comments, and the existing route
checks. The initial and paginated GraphQL review queries both collect the
provenance flag. Missing, false, or malformed provenance fails closed.

## Supported bodies

The older format contains exactly one visible coverage sentence:

```text
Copilot reviewed N out of M changed files in this pull request and generated no comments.
```

The newer format starts with `### 🟢 Approval recommended` and contains exactly
one completion block of this shape:

```html
<details>
<summary>Review details</summary>

- **Files reviewed:** 2/2 changed files
- **Comments generated:** 0
- **Review effort level:** Lite
</details>
```

The newer counts must be positive integers with reviewed files no greater than
the reported total. They are not compared with GitHub's changed-file count:
both counts are provider assertions, and partial coverage remains supported.
The effort display line is optional and does not establish a clean verdict.
Overview prose, file summaries and footer links are not parsed for approval.
The assessment and completion markers must occur once; duplicate markers,
mixed old/new formats, nonzero generated comments and unsupported shapes fail
closed. Fenced or indented code and HTML comments cannot supply the verdict.

Unknown formats, including an unverified approval-mode artifact, require another
selected provider or a separately evidenced format change. A quota refusal with
zero comments is not successful review evidence. Schema v2 keeps its existing
human-gated review semantics, including its refusal check; it does not acquire
the v3 provenance/body requirements.

## Public artifact and API audit

This change addresses [issue #2491](https://github.com/bioedca/Yeliztli/issues/2491)
using existing public artifacts. No exploratory review was requested, and no
account, entitlement, review-effort or repository setting was changed.

The clean fixture is the native body of
[review 5098282786 on PR #2355](https://github.com/bioedca/Yeliztli/pull/2355#pullrequestreview-5098282786)
(accessed 2026-11-08). It was submitted on `2026-09-03T05:52:16Z` for commit
`f501b805a00e692f60d06ed3f0aa74094ef9700d`, by bot database ID `175728472`.
REST and GraphQL agree on its identity and body; GraphQL reports `lastEditedAt:
null`, provenance `true`, effort `LITE`, and zero attached comments. The fixture
in `tests/fixtures/review_route/copilot_approval_recommended.json` preserves the
body, including its mixed line endings, and pins its SHA-256:
`f8104f6b14ddd5fe9ef6a969dbf855ed22e086c17273f0011edbb62081d284bd`.
This is a historical artifact retrieved on the audit date, not an assertion
that every present or future Copilot review has this format.

Two controls were also re-fetched (accessed 2026-11-08):

| Artifact | Provenance | Attached comments | Meaning |
| --- | --- | --- | --- |
| [Review 4839198937, PR #2238](https://github.com/bioedca/Yeliztli/pull/2238#pullrequestreview-4839198937) | `true` | 2 | Older format with findings; not clean |
| [Review 5564230327, PR #2495](https://github.com/bioedca/Yeliztli/pull/2495#pullrequestreview-5564230327) | `true` | 0 | Quota refusal; no completed review |

Read-only introspection of all 1,873 GraphQL types on 2026-11-08 found
`PullRequestReview.isCopilotCodeReview` and `copilotCodeReviewEffort`. The first
distinguishes review provenance from bot authorship; the second reports persisted
effective effort, or null. Neither is a clean-verdict field. No structured
per-review coverage, generated-comment or withheld-finding count was found.
The REST counterparts are `is_copilot_code_review` and
`copilot_code_review_effort`. The quota control also has provenance `true`, so
the flag cannot replace the body envelope.

To reproduce the schema inspection without invoking a review, use:

```graphql
{
  __schema {
    types {
      name
      kind
      fields(includeDeprecated: true) { name description }
      inputFields { name description }
    }
  }
}
```

For an artifact, retrieve its REST review object and GraphQL `PullRequestReview`
node, including `author`, `body`, `commit { oid }`, `state`, `submittedAt`,
`lastEditedAt`, `comments { totalCount }`, `isCopilotCodeReview` and
`copilotCodeReviewEffort`. The clean fixture's node ID is
`PRR_kwDOS2RYvs8AAAABL-GfIg`. Official references:
[REST pull-request reviews](https://docs.github.com/en/rest/pulls/reviews#get-a-review-for-a-pull-request)
and [using Copilot code review](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/use-code-review)
(accessed 2026-11-08).

## Limits and merge authority

The accepted residual from #2256 remains: a clean posted verdict and zero
attached comments cannot establish that no finding was withheld or that all
files were reviewed. Effort metadata does not resolve that gap. Do not scan
free-form prose for guessed suppression wording. Re-probe the authenticated API
before assuming its limitations persist.

The regression suite exercises the real body, legacy compatibility, partial
coverage, provenance types, edited/wrong-head artifacts, findings, refusals,
hidden text and ambiguous verdicts through the production route validator.
Copilot's assessment itself never authorizes a merge. Only exact-head success
from the dedicated Review Route publisher App, together with all other required
gates, supplies schema-v3 merge authority.
