#!/usr/bin/env bash
set -euo pipefail

snapshot_name="${1:?snapshot name is required}"
snapshot_dir="$RUNNER_TEMP/review-route-$snapshot_name"
mkdir -p "$snapshot_dir"

case "${PR_NUMBER:-}" in
  ''|*[!0-9]*) echo "::error title=Review Route::pull request number is invalid"; exit 1 ;;
esac
if ! [[ "${HEAD_SHA:-}" =~ ^[0-9a-f]{40}$ ]] || \
  ! [[ "${WORKFLOW_SHA:-}" =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo "::error title=Review Route::trusted head identity is invalid"
  exit 1
fi

gh api "repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER" > "$snapshot_dir/pr.json"
jq -e \
  --arg head "$HEAD_SHA" \
  --arg repo "$GITHUB_REPOSITORY" \
  --arg head_repo "$HEAD_REPOSITORY" \
  --arg head_branch "$HEAD_BRANCH" \
  --arg pr "$PR_NUMBER" '
    (.number | tostring) == $pr and
    .state == "open" and .draft == false and
    (.head.sha | ascii_downcase) == $head and
    .head.repo.full_name == $head_repo and .head.ref == $head_branch and
    .base.ref == "main" and .base.repo.full_name == $repo
  ' "$snapshot_dir/pr.json" > /dev/null
jq -e -s '
  .[0].issue.number == .[1].number and
  .[0].issue.body == .[1].body and
  .[0].issue.updated_at == .[1].updated_at
' "$GITHUB_EVENT_PATH" "$snapshot_dir/pr.json" > /dev/null

gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/main" > "$snapshot_dir/main.json"
jq -e --arg trusted "$WORKFLOW_SHA" \
  '.object.sha == $trusted' "$snapshot_dir/main.json" > /dev/null

# GraphQL variables are intentionally literal in the single-quoted query.
# shellcheck disable=SC2016
gh api graphql \
  -F node="$FINALIZE_COMMENT_NODE_ID" \
  -f query='query($node: ID!) {
    node(id: $node) {
      ... on IssueComment {
        id
        author {
          __typename
          login
          ... on User { databaseId }
        }
        authorAssociation
        body
        createdAt
        lastEditedAt
        updatedAt
      }
    }
  }' > "$snapshot_dir/finalizer.json"
finalizer_login="$(jq -er '.data.node.author.login' "$snapshot_dir/finalizer.json")"
[[ "$finalizer_login" =~ ^[A-Za-z0-9][A-Za-z0-9-]*$ ]]
gh api \
  "repos/$GITHUB_REPOSITORY/collaborators/$finalizer_login/permission" \
  > "$snapshot_dir/finalizer-permission.json"
finalizer_permission="$(jq -er --arg actor "$FINALIZE_COMMENT_ACTOR_ID" '
  if type == "object" and
    (.user.id | tostring) == $actor and
    (.permission == "admin" or .permission == "write")
  then .permission
  else error("finalizer lacks bound write permission")
  end
' "$snapshot_dir/finalizer-permission.json")"
jq -e \
  --arg node "$FINALIZE_COMMENT_NODE_ID" \
  --arg actor "$FINALIZE_COMMENT_ACTOR_ID" \
  --arg created "$FINALIZE_COMMENT_CREATED_AT" '
    .data.node.id == $node and
    .data.node.author.__typename == "User" and
    (.data.node.author.databaseId | tostring) == $actor and
    .data.node.body == "/validate-route" and
    .data.node.createdAt == $created and
    .data.node.updatedAt == .data.node.createdAt and
    .data.node.lastEditedAt == null and
    (.data.node.authorAssociation == "OWNER" or
     .data.node.authorAssociation == "MEMBER" or
     .data.node.authorAssociation == "COLLABORATOR")
  ' "$snapshot_dir/finalizer.json" > /dev/null

is_draft=false
pr_updated_at="$(jq -er '.updated_at' "$snapshot_dir/pr.json")"
include_coderabbit_ledger="$(python -c '
import json, sys
from pathlib import Path
from scripts.validate_review_route import needs_coderabbit_ledger
body = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")).get("body") or ""
print(str(needs_coderabbit_ledger(body)).lower())
' "$snapshot_dir/pr.json")"
case "$include_coderabbit_ledger" in
  true|false) ;;
  *) echo "::error title=Review Route::invalid ledger selector"; exit 1 ;;
esac

gh api graphql \
  -F owner="$OWNER" \
  -F repo="$REPO" \
  -F number="$PR_NUMBER" \
  -f headOid="$HEAD_SHA" \
  -F includeCodeRabbitLedger="$include_coderabbit_ledger" \
  -F query=@scripts/review_route_context.graphql \
  > "$snapshot_dir/context.json"

review_page_args=()
if jq -e '
  .data.repository.pullRequest.reviews as $reviews |
  ($reviews | type) == "object" and
  ($reviews.totalCount | type) == "number" and
  ($reviews.nodes | type) == "array" and
  $reviews.totalCount > ($reviews.nodes | length)
' "$snapshot_dir/context.json" > /dev/null; then
  gh api graphql --paginate --slurp \
    -F owner="$OWNER" \
    -F repo="$REPO" \
    -F number="$PR_NUMBER" \
    -F query=@scripts/review_route_reviews.graphql \
    > "$snapshot_dir/review-pages.json"
  review_page_args=(--review-pages "$snapshot_dir/review-pages.json")
fi
thread_page_args=()
if jq -e '
  .data.repository.pullRequest.reviewThreads as $threads |
  ($threads | type) == "object" and
  ($threads.totalCount | type) == "number" and
  ($threads.nodes | type) == "array" and
  $threads.totalCount > ($threads.nodes | length)
' "$snapshot_dir/context.json" > /dev/null; then
  gh api graphql --paginate --slurp \
    -F owner="$OWNER" \
    -F repo="$REPO" \
    -F number="$PR_NUMBER" \
    -F query=@scripts/review_route_threads.graphql \
    > "$snapshot_dir/thread-pages.json"
  thread_page_args=(--thread-pages "$snapshot_dir/thread-pages.json")
fi
# Deliberately reuses the sweep the job already fetched rather than running its
# own. `after-success` executes once `Review Route: success` is published, so a
# ~840-point query rate-limited here would demote a legitimately green route to
# `pending` and force a re-run against an already-drained budget.
greptile_ledger_args=()
if [ -f "$RUNNER_TEMP/review-route-greptile-ledger.json" ]; then
  greptile_ledger_args=(
    --greptile-ledger-pages "$RUNNER_TEMP/review-route-greptile-ledger.json"
  )
fi
gh api --paginate --slurp \
  "repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER/files?per_page=100" \
  > "$snapshot_dir/files.json"

render_nonce="review-route-render-bind-$(python -c \
  'import secrets; print(secrets.token_hex(24))')"
python -c '
import json, sys
from pathlib import Path
from scripts.validate_review_route import render_probe_body
snapshot = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
body = snapshot.get("body")
if not isinstance(body, str):
    raise SystemExit("REST pull request body has an invalid shape")
payload = {"context": sys.argv[4], "mode": "gfm", "text": render_probe_body(body, sys.argv[3])}
Path(sys.argv[2]).write_text(json.dumps(payload), encoding="utf-8")
' "$snapshot_dir/pr.json" "$snapshot_dir/render-request.json" \
  "$render_nonce" "$GITHUB_REPOSITORY"
gh api markdown --input "$snapshot_dir/render-request.json" > "$snapshot_dir/rendered.html"

python scripts/validate_review_route.py \
  --context "$snapshot_dir/context.json" \
  --files "$snapshot_dir/files.json" \
  --expected-head "$HEAD_SHA" \
  --expected-draft "$is_draft" \
  --expected-pr-updated-at "$pr_updated_at" \
  --expected-pr-snapshot "$snapshot_dir/pr.json" \
  --rendered-body "$snapshot_dir/rendered.html" \
  --render-nonce "$render_nonce" \
  "${review_page_args[@]}" \
  "${thread_page_args[@]}" \
  "${greptile_ledger_args[@]}" \
  --finalize-comment-node-id "$FINALIZE_COMMENT_NODE_ID" \
  --finalize-comment-created-at "$FINALIZE_COMMENT_CREATED_AT" \
  --finalize-comment-actor-id "$FINALIZE_COMMENT_ACTOR_ID" \
  --finalize-comment-actor-permission "$finalizer_permission"
