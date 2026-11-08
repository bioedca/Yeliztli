# Issue claim heartbeats

An issue claim's lease comes from GitHub's server `updated_at`, not the time an
agent requested an update. In [#2639](https://github.com/bioedca/Yeliztli/issues/2639),
an identical-body update through the GitHub connector returned success while two
fresh REST snapshots still showed the old timestamp. Its lease was not renewed.
The observation does not establish whether GitHub or the connector suppressed the
no-op, and the procedure below does not depend on that distinction.

The [agent contract](https://github.com/bioedca/Yeliztli/blob/main/AGENTS.md) remains
the authority for eligibility, elections, transitions and ownership checks before
writes. A claim is cooperative coordination, not a lock or a security boundary.
This page concerns an already confirmed, solely owned, unexpired **active** claim.
It does not elect a successor, resume `waiting`, refresh an expired claim or alter
application database/build leases.

## Prepare a changing heartbeat

Use a nonnegative integer `heartbeat_seq` in the claim's JSON record. A legacy
record without the field starts at zero; its first heartbeat writes one. Increment
it by exactly one for each prepared heartbeat. Preserve the existing comment ID,
generation, agent ID, authenticated author, state and all other public fields.
Do not add a local path, host, credential, sample data or other private context.

After the contract's eligibility and ownership checks, run the helper from the
repository root using the actual values of **your own** confirmed claim. Replace
the uppercase placeholders; the author ID is the comment author's public GitHub
numeric ID, independently checked against the expected collaborator or approved App.

```bash
mkdir -p output/claims
python scripts/claim_heartbeat.py prepare \
  --repo bioedca/Yeliztli --issue ISSUE_NUMBER \
  --comment-id COMMENT_ID --author-id AUTHOR_DATABASE_ID \
  --agent-id yz-UUIDV4 --generation GENERATION \
  --plan output/claims/heartbeat-1.json
```

The helper makes only `GET` requests using the existing authenticated `gh` CLI.
Repository owner/name comparisons ignore capitalization, matching GitHub's
canonical URL behavior; the API host, URL shape and issue number must still match.
It fetches every comment page twice and requires matching complete snapshots with
the expected active identity and no other live ownership or provisional record.
It checks expiry **after** both reads. Unknown/malformed claim records fail closed;
inspect them under the existing contract instead of deleting or ignoring them.
Comments containing `yeliztli-claim:v1` must use the supported canonical envelope;
different marker whitespace or multiple claim tokens are rejected, not treated as
unrelated comments that could conceal another owner.

`prepare` writes a new local plan, refusing to overwrite an existing one. It retains
only the selected public claim and the metadata needed to verify renewal, not the
other comments. Its success means a heartbeat is prepared; no GitHub mutation or
renewal has occurred. Keep this evidence under ignored `output/`.

Read the plan's `request.body` and pass that exact string to the GitHub connector's
`update_issue_comment` for the same comment ID. Do not upload the plan or other
workspace files. Apply it promptly after preparation; if delayed or the claim
changes, repeat the preflight with a new plan. Never apply it after the existing
lease expires. A phase, SHA, PR or state transition uses the contract's separate
mutation checks; this helper deliberately prepares only a counter change.

## Prove renewal after the write

Regardless of the mutation response, independently verify the live server state:

```bash
python scripts/claim_heartbeat.py verify \
  --plan output/claims/heartbeat-1.json \
  > output/claims/heartbeat-1-proof.json
```

Only exit status zero **and** the resulting proof establish renewal. The helper
fetches all pages twice again and requires:

- Identical complete snapshots with the same sole active comment, generation,
  agent, author and creation identity, and the exact prepared body/counter.
- A strictly advanced server `updated_at`, before the prior lease's 90-minute
  expiry. A fresh timestamp written after that deadline cannot revive the claim.
- A renewed lease that is still unexpired at verification. The proof's
  `expires_at` is calculated from the observed server timestamp plus 90 minutes.

The client's preparation time only bounds this observation; it never supplies a
lease timestamp. The helper is read-only and does not replace the contract's
fresh ownership checks before subsequent edits, commits, pushes, PR mutations,
submissions or merges. The two-snapshot check is best-effort coordination, not an
atomic compare-and-swap against another session.

## When renewal cannot be proved

On any nonzero result, missing/empty proof, API failure, changed ownership,
inconsistent pages or unchanged server timestamp, stop source writes and retain
the previous verified expiry. A successful mutation response or a changed
`heartbeat_seq` is insufficient. GitHub timestamps have second precision, so even
a body change within the same server second must not be reported as renewal.

While the previous lease remains valid, a retry requires fresh complete ownership
checks and a new plan based on the **currently observed** counter. Do not replay
an old plan or infer success by waiting on a client clock. If the lease expired,
follow the contract's successor-generation/election rules; never refresh or
reactivate the old record. Preserve the failed evidence and claim history.

The transport uses [`gh api --paginate --slurp`](https://cli.github.com/manual/gh_api)
to follow all REST pages and rejects failed/partial reads. It does not accept a
caller-supplied snapshot file or a self-attested `complete` flag as current
ownership evidence. Local plans are task evidence, not authorization tokens.
