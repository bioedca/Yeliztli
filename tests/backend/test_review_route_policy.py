"""Tests for the trusted PR review-route policy and status publisher."""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.validate_review_route import (
    AUTONOMOUS_SCHEMA_MARKER,
    BOT_ACTOR_IDS,
    CODERABBIT_COMPLETION_MARKER,
    CODERABBIT_GATE,
    CODEX_CLEAN_COMPLETION_MARKER,
    CODEX_GATE,
    CODEX_TRIGGER,
    COPILOT_GATE,
    GATES,
    HUMAN_GATE,
    LEGACY_SCHEMA_MARKER,
    RENDERED_ROUTE_ERROR,
    SCHEMA_MARKER,
    ChangedFile,
    _expected_snapshot_body,
    _merge_review_pages,
    minimum_route,
    needs_coderabbit_ledger,
    render_probe_body,
    validate_context,
)

HEAD_SHA = "a" * 40
AUTHOR_ID = 111
HUMAN_ID = 222
COMMITTED_AT = "2026-07-21T12:00:00Z"
CREATED_AT = "2026-07-21T11:50:00Z"
NOW = datetime(2026, 7, 21, 13, 0, tzinfo=UTC)
PR_UPDATED_AT = "2026-07-21T12:45:00Z"
FINALIZER_NODE_ID = "IC_kwDOReviewRouteFinalizer"
FINALIZER_DATABASE_ID = 5_035_499_555
GATE_TIMES = {
    COPILOT_GATE: "2026-07-21T12:10:00Z",
    CODEX_GATE: "2026-07-21T12:20:00Z",
    CODERABBIT_GATE: "2026-07-21T12:30:00Z",
    HUMAN_GATE: "2026-07-21T12:40:00Z",
}
DEFAULT_AUTOMATED_GATE = {
    "Low": COPILOT_GATE,
    "Standard": CODEX_GATE,
    "Load-bearing": CODERABBIT_GATE,
}
RENDER_NONCE = "review-route-render-bind-" + "b" * 48


def _signal_title(
    head: str = HEAD_SHA,
    *,
    pr_number: int = 2183,
    trigger_actor_id: int = AUTHOR_ID,
) -> str:
    return f"review-route-pr-{pr_number}-head-{head}-trigger-{trigger_actor_id}"


def _workflow_step_script(workflow: str, step_name: str) -> str:
    """Extract one literal Actions ``run`` block for executable regression tests."""
    step = workflow.split(f"      - name: {step_name}\n", maxsplit=1)[1]
    block = step.split("        run: |\n", maxsplit=1)[1]
    lines: list[str] = []
    for line in block.splitlines():
        if not line:
            lines.append(line)
        elif line.startswith("          "):
            lines.append(line[10:])
        else:
            break
    return "\n".join(lines)


def _actor(database_id: int, typename: str = "Bot") -> dict[str, object]:
    return {"__typename": typename, "databaseId": database_id, "login": f"actor-{database_id}"}


def _review(
    database_id: int,
    submitted_at: str,
    *,
    typename: str = "Bot",
    state: str = "COMMENTED",
    head_sha: str = HEAD_SHA,
    association: str = "NONE",
    body: str = "Review completed.",
    last_edited_at: str | None = None,
    review_comment_count: int = 0,
) -> dict[str, object]:
    return {
        "author": _actor(database_id, typename),
        "authorAssociation": association,
        "body": body,
        "comments": {"totalCount": review_comment_count},
        "commit": {"oid": head_sha},
        "lastEditedAt": last_edited_at,
        "state": state,
        "submittedAt": submitted_at,
    }


def _copilot_v3_body(changed_files: int, *, generated_comments: int = 0) -> str:
    return (
        "## Copilot's findings"
        + "\n" * 7
        + f"- **Files reviewed:** {changed_files}/{changed_files} changed files\n"
        + f"- **Comments generated:** {generated_comments} new\n\n"
    )


def _coderabbit_v3_body(changed_files: int, *, ignored_files: int | None = None) -> str:
    ignored = (
        ""
        if ignored_files is None
        else (
            "<details>\n"
            f"<summary>⛔ Files ignored due to path filters ({ignored_files})</summary>\n"
            "</details>\n\n"
        )
    )
    return (
        "**Actionable comments posted: 0**\n\n"
        "<details>\n"
        "<summary>📜 Review details</summary>\n\n"
        f"{ignored}"
        f"<summary>📒 Files selected for processing ({changed_files})</summary>\n"
        "</details>\n\n"
        f"<!-- This is an {CODERABBIT_COMPLETION_MARKER} -->"
    )


def _comment(
    database_id: int,
    body: str,
    created_at: str,
    *,
    association: str = "COLLABORATOR",
    updated_at: str | None = None,
    last_edited_at: str | None = None,
    comment_id: int | None = None,
    typename: str = "User",
) -> dict[str, object]:
    if comment_id is None:
        comment_id = int(
            "".join(character for character in created_at if character.isdigit())[-9:]
        )
    return {
        "id": f"IC_kwDOTest{comment_id}",
        "databaseId": comment_id,
        "author": _actor(database_id, typename),
        "authorAssociation": association,
        "body": body,
        "createdAt": created_at,
        "lastEditedAt": last_edited_at,
        "updatedAt": updated_at or created_at,
    }


def _codex_clean_comment(
    created_at: str = GATE_TIMES[CODEX_GATE],
    *,
    actor_id: int = BOT_ACTOR_IDS[CODEX_GATE],
    head_sha: str = HEAD_SHA,
    updated_at: str | None = None,
    last_edited_at: str | None = None,
) -> dict[str, object]:
    return _comment(
        actor_id,
        (
            f"{CODEX_CLEAN_COMPLETION_MARKER}\n\n"
            f"**Reviewed commit:** `{head_sha[:10]}`\n\n"
            "<details><summary>About Codex</summary></details>"
        ),
        created_at,
        association="NONE",
        updated_at=updated_at,
        last_edited_at=last_edited_at,
        typename="Bot",
    )


def _use_codex_clean_comment(
    context: dict[str, object],
    *,
    trigger: dict[str, object] | None = None,
    response: dict[str, object] | None = None,
    include_trigger: bool = True,
) -> tuple[dict[str, object] | None, dict[str, object]]:
    pull_request = context["data"]["repository"]["pullRequest"]
    reviews = pull_request["reviews"]
    reviews["nodes"] = [
        review
        for review in reviews["nodes"]
        if review["author"]["databaseId"] != BOT_ACTOR_IDS[CODEX_GATE]
    ]
    reviews["totalCount"] = len(reviews["nodes"])
    if include_trigger:
        trigger = trigger or _comment(
            AUTHOR_ID,
            CODEX_TRIGGER,
            "2026-07-21T12:19:00Z",
            association="OWNER",
        )
    response = response or _codex_clean_comment()
    comments = [item for item in (trigger, response) if item is not None]
    pull_request["comments"] = {"totalCount": len(comments), "nodes": comments}
    recent = context["data"]["repository"]["recentPullRequests"]["nodes"][0]
    recent["comments"] = {"totalCount": len(comments), "nodes": comments}
    return trigger, response


def _add_coderabbit_completion(context: dict[str, object], submitted_at: str) -> dict[str, object]:
    review = _review(
        BOT_ACTOR_IDS[CODERABBIT_GATE],
        submitted_at,
        body=f"Review completed. <!-- This is an {CODERABBIT_COMPLETION_MARKER} -->",
    )
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]
    reviews["nodes"].append(review)
    reviews["totalCount"] += 1
    return review


def _body(
    route: str,
    *,
    complete: bool = True,
    automated_gates: set[str] | None = None,
    schema_version: int = 2,
) -> str:
    selected = {
        name: "x" if name == route else " " for name in ("Low", "Standard", "Load-bearing")
    }
    selected_bots = {DEFAULT_AUTOMATED_GATE[route]} if automated_gates is None else automated_gates
    required = set(selected_bots)
    if schema_version == 2:
        required.add(HUMAN_GATE)
    reviewer_labels = {
        COPILOT_GATE: "Copilot",
        CODEX_GATE: "Codex",
        CODERABBIT_GATE: "CodeRabbit",
    }
    rows = []
    evidence_gates = GATES if schema_version == 2 else tuple(GATES[:-1])
    for gate in evidence_gates:
        label = (
            "CodeRabbit structured clean review"
            if schema_version == 3 and gate == CODERABBIT_GATE
            else gate
        )
        if not complete:
            head, status = "", ""
        elif gate in required:
            head = HEAD_SHA
            result = "APPROVED" if gate == HUMAN_GATE else "COMPLETE"
            status = f"{GATE_TIMES[gate]} — {result}"
        else:
            head, status = "N/A", "N/A"
        rows.append(f"| {label} | scope | {head} | {status} |")
    return "\n".join(
        [
            "## Review route",
            SCHEMA_MARKER if schema_version == 2 else AUTONOMOUS_SCHEMA_MARKER,
            f"- [{selected['Low']}] Low — docs",
            f"- [{selected['Standard']}] Standard — code",
            f"- [{selected['Load-bearing']}] Load-bearing — governance",
            "<!-- route/provider render boundary -->",
            *[
                f"- [{'x' if gate in selected_bots else ' '}] {label} — automated review"
                for gate, label in reviewer_labels.items()
            ],
            "<!-- provider/table render boundary -->",
            (
                "| Required review gate | Applies to | Head SHA or N/A "
                "| UTC time and status, or N/A |"
            ),
            "| --- | --- | --- | --- |",
            *rows,
            "## Legal",
        ]
    )


def _rendered_route_html(
    route: str,
    *,
    automated_gates: set[str] | None = None,
    nonce: str = RENDER_NONCE,
    schema_version: int = 2,
) -> str:
    selected_bots = {DEFAULT_AUTOMATED_GATE[route]} if automated_gates is None else automated_gates
    route_items = "".join(
        f'<li><input type="checkbox"{" checked" if name == route else ""}> {name} — route</li>'
        for name in ("Low", "Standard", "Load-bearing")
    )
    provider_items = "".join(
        f'<li><input type="checkbox"{" checked" if gate in selected_bots else ""}> '
        f"{label} — automated review</li>"
        for gate, label in (
            (COPILOT_GATE, "Copilot"),
            (CODEX_GATE, "Codex"),
            (CODERABBIT_GATE, "CodeRabbit"),
        )
    )
    provider_group = f"<ul>{provider_items}</ul>" if schema_version in {2, 3} else ""
    required = set(selected_bots)
    if schema_version != 3:
        required.add(HUMAN_GATE)
    evidence_rows = []
    evidence_gates = tuple(GATES[:-1]) if schema_version == 3 else GATES
    for gate in evidence_gates:
        label = (
            "CodeRabbit structured clean review"
            if schema_version == 3 and gate == CODERABBIT_GATE
            else gate
        )
        if gate in required:
            head = HEAD_SHA
            result = "APPROVED" if gate == HUMAN_GATE else "COMPLETE"
            status = f"{GATE_TIMES[gate]} — {result}"
        else:
            head = status = "N/A"
        evidence_rows.append(
            f"<tr><td>{label}</td><td>scope</td><td>{head}</td><td>{status}</td></tr>"
        )
    return (
        f"<h2>Review route</h2><p>{nonce}</p>"
        f"<ul>{route_items}</ul>{provider_group}"
        "<markdown-accessiblity-table><table>"
        "<thead><tr><th>Required review gate</th><th>Applies to</th>"
        "<th>Head SHA or N/A</th><th>UTC time and status, or N/A</th></tr></thead>"
        f"<tbody>{''.join(evidence_rows)}</tbody></table></markdown-accessiblity-table>"
        "<h2>Legal</h2>"
    )


def _validate_rendered(
    context: dict[str, object],
    files: list[ChangedFile],
    rendered_html: str,
) -> list[str]:
    return validate_context(
        context,
        files,
        now=NOW,
        rendered_body=rendered_html,
        render_nonce=RENDER_NONCE,
    )


def _legacy_body(route: str) -> str:
    legacy_bots = {
        "Low": {COPILOT_GATE},
        "Standard": {COPILOT_GATE, CODEX_GATE},
        "Load-bearing": {COPILOT_GATE, CODEX_GATE, CODERABBIT_GATE},
    }[route]
    body = _body(route, automated_gates=legacy_bots).replace(SCHEMA_MARKER, LEGACY_SCHEMA_MARKER)
    return "\n".join(
        line
        for line in body.splitlines()
        if not any(
            line.startswith(f"- [{'x' if gate in legacy_bots else ' '}] {label}")
            for gate, label in (
                (COPILOT_GATE, "Copilot"),
                (CODEX_GATE, "Codex"),
                (CODERABBIT_GATE, "CodeRabbit"),
            )
        )
    )


def _context(
    route: str,
    files: list[ChangedFile],
    *,
    draft: bool = False,
    complete: bool = True,
    body: str | None = None,
    automated_gates: set[str] | None = None,
    schema_version: int = 2,
) -> dict[str, object]:
    selected_bots = {DEFAULT_AUTOMATED_GATE[route]} if automated_gates is None else automated_gates
    human_review = _review(
        HUMAN_ID,
        GATE_TIMES[HUMAN_GATE],
        typename="User",
        state="APPROVED",
        association="COLLABORATOR",
    )
    reviews = [
        _review(
            BOT_ACTOR_IDS[COPILOT_GATE],
            GATE_TIMES[COPILOT_GATE],
            body=(_copilot_v3_body(len(files)) if schema_version == 3 else "Review completed."),
        ),
        _review(
            BOT_ACTOR_IDS[CODEX_GATE],
            GATE_TIMES[CODEX_GATE],
            state="APPROVED" if schema_version == 3 else "COMMENTED",
            body="" if schema_version == 3 else "Review completed.",
        ),
        _review(
            BOT_ACTOR_IDS[CODERABBIT_GATE],
            GATE_TIMES[CODERABBIT_GATE],
            body=(
                _coderabbit_v3_body(len(files))
                if schema_version == 3
                else f"Review completed. <!-- This is an {CODERABBIT_COMPLETION_MARKER} -->"
            ),
        ),
        human_review,
    ]
    comments = []
    if schema_version == 2 and CODERABBIT_GATE in selected_bots:
        comments = [
            _comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:25:00Z"),
            _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:26:00Z"),
        ]
    pull_request = {
        "number": 42,
        "author": _actor(AUTHOR_ID, "User"),
        "body": (
            body
            if body is not None
            else _body(
                route,
                complete=complete,
                automated_gates=selected_bots,
                schema_version=schema_version,
            )
        ),
        "changedFiles": len(files),
        "createdAt": CREATED_AT,
        "headRefOid": HEAD_SHA,
        "isDraft": draft,
        "state": "OPEN",
        "updatedAt": PR_UPDATED_AT,
        "commits": {"nodes": [{"commit": {"committedDate": COMMITTED_AT}}]},
        "timelineItems": {"nodes": []},
        "reviews": {"totalCount": len(reviews), "nodes": reviews},
        "latestHumanOpinions": {"totalCount": 1, "nodes": [human_review]},
        "comments": {"totalCount": len(comments), "nodes": comments},
        "reviewThreads": {"totalCount": 0, "nodes": []},
    }
    return {
        "data": {
            "repository": {
                "codexHeadObject": {
                    "__typename": "Commit",
                    "oid": HEAD_SHA,
                    "abbreviatedOid": HEAD_SHA[:7],
                },
                "pullRequest": pull_request,
                "openPullRequests": {
                    "totalCount": 1,
                    "nodes": [{"headRefOid": HEAD_SHA, "number": 42}],
                },
                "recentPullRequests": {
                    "totalCount": 1,
                    "nodes": [
                        {
                            "updatedAt": PR_UPDATED_AT,
                            "comments": {"totalCount": len(comments), "nodes": comments},
                        }
                    ],
                },
            }
        }
    }


def _review_pages(
    context: dict[str, object],
    all_reviews: list[dict[str, object]],
    *,
    page_size: int = 100,
) -> list[dict[str, object]]:
    materialized: list[dict[str, object]] = []
    for index, review in enumerate(all_reviews):
        node = deepcopy(review)
        node["id"] = f"PRR_test_{index:04d}"
        materialized.append(node)
    pull_request = context["data"]["repository"]["pullRequest"]
    pull_request["reviews"] = {
        "totalCount": len(materialized),
        "nodes": deepcopy(materialized[-100:]),
    }
    chunks = [
        materialized[index : index + page_size] for index in range(0, len(materialized), page_size)
    ] or [[]]
    pages: list[dict[str, object]] = []
    for index, chunk in enumerate(chunks):
        pages.append(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "number": pull_request["number"],
                            "headRefOid": pull_request["headRefOid"],
                            "updatedAt": pull_request["updatedAt"],
                            "reviews": {
                                "totalCount": len(materialized),
                                "pageInfo": {
                                    "hasNextPage": index < len(chunks) - 1,
                                    "endCursor": f"review-cursor-{index}",
                                },
                                "nodes": deepcopy(chunk),
                            },
                        }
                    }
                }
            }
        )
    return pages


def _truncated_review_fixture(
    route: str = "Low",
) -> tuple[dict[str, object], list[dict[str, object]]]:
    context = _context(route, [ChangedFile("docs/guide.md")])
    base_reviews = deepcopy(context["data"]["repository"]["pullRequest"]["reviews"]["nodes"])
    historical_reviews = [
        _review(
            10_000 + index,
            "2026-07-21T11:00:00Z",
            typename="User",
            head_sha="b" * 40,
        )
        for index in range(100)
    ]
    pages = _review_pages(context, [*historical_reviews, *base_reviews])
    return context, pages


def _add_finalizer(
    context: dict[str, object],
    *,
    created_at: str = PR_UPDATED_AT,
    actor_id: int = HUMAN_ID,
    association: str = "COLLABORATOR",
    body: str = "/validate-route",
    updated_at: str | None = None,
    node_id: str = FINALIZER_NODE_ID,
) -> dict[str, object]:
    comment = _comment(
        actor_id,
        body,
        created_at,
        association=association,
        updated_at=updated_at,
        comment_id=FINALIZER_DATABASE_ID,
    )
    comment["id"] = node_id
    pull_request = context["data"]["repository"]["pullRequest"]
    pull_request["comments"]["nodes"].append(comment)
    pull_request["comments"]["totalCount"] += 1
    pull_request["updatedAt"] = created_at
    recent_comments = context["data"]["repository"]["recentPullRequests"]["nodes"][0]["comments"]
    recent_comments["totalCount"] += 1
    return comment


def _finalizer_kwargs(*, created_at: str = PR_UPDATED_AT) -> dict[str, object]:
    return {
        "expected_pr_updated_at": created_at,
        "finalize_comment_node_id": FINALIZER_NODE_ID,
        "finalize_comment_created_at": created_at,
        "finalize_comment_actor_id": HUMAN_ID,
        "finalize_comment_actor_permission": "write",
    }


@pytest.mark.parametrize(
    ("route", "files"),
    [
        ("Low", [ChangedFile("docs/guide.md")]),
        ("Standard", [ChangedFile("frontend/src/hooks/useDialogFocus.ts")]),
        ("Load-bearing", [ChangedFile(".github/workflows/ci.yml")]),
    ],
)
def test_valid_ready_routes(route: str, files: list[ChangedFile]) -> None:
    assert validate_context(_context(route, files), files, now=NOW) == []


def test_real_template_is_accepted_for_a_classified_draft() -> None:
    root = Path(__file__).resolve().parents[2]
    template = (root / ".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
    template = template.replace("- [ ] Load-bearing", "- [x] Load-bearing")
    files = [ChangedFile(".github/workflows/ci.yml")]
    context = _context("Load-bearing", files, draft=True, complete=False, body=template)
    assert validate_context(context, files, now=NOW) == []


def test_real_template_is_rejected_for_an_unclassified_draft() -> None:
    root = Path(__file__).resolve().parents[2]
    template = (root / ".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
    files = [ChangedFile(".github/workflows/ci.yml")]
    context = _context("Load-bearing", files, draft=True, complete=False, body=template)
    assert "select exactly one review route" in validate_context(context, files, now=NOW)


def test_exactly_one_route_is_required() -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files)
    pr = context["data"]["repository"]["pullRequest"]
    pr["body"] = pr["body"].replace("- [ ] Standard", "- [x] Standard")
    assert "select exactly one review route" in validate_context(context, files, now=NOW)


@pytest.mark.parametrize(
    ("route", "path"),
    [
        ("Low", "docs/typo-fix.md"),
        ("Standard", "frontend/src/hooks/useDialogFocus.ts"),
        ("Load-bearing", "README.md"),
    ],
)
@pytest.mark.parametrize("gate", [COPILOT_GATE, CODEX_GATE, CODERABBIT_GATE])
def test_v2_review_providers_are_interchangeable(route: str, path: str, gate: str) -> None:
    files = [ChangedFile(path)]
    context = _context(route, files, automated_gates={gate})
    assert validate_context(context, files, now=NOW) == []


@pytest.mark.parametrize("gate", [COPILOT_GATE, CODEX_GATE, CODERABBIT_GATE])
def test_v3_accepts_one_clean_trusted_provider_without_human_approval(gate: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context(
        "Load-bearing",
        files,
        automated_gates={gate},
        schema_version=3,
    )
    pull_request = context["data"]["repository"]["pullRequest"]
    pull_request["reviews"]["nodes"] = [
        review
        for review in pull_request["reviews"]["nodes"]
        if review["author"]["databaseId"] != HUMAN_ID
    ]
    pull_request["reviews"]["totalCount"] = len(pull_request["reviews"]["nodes"])
    pull_request["latestHumanOpinions"] = {"totalCount": 0, "nodes": []}
    assert validate_context(context, files, now=NOW) == []


def test_v3_requires_exactly_one_hosted_provider_and_no_human_row() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, schema_version=3)
    body = context["data"]["repository"]["pullRequest"]["body"]
    context["data"]["repository"]["pullRequest"]["body"] = body.replace(
        "- [ ] Copilot",
        "- [x] Copilot",
    )
    errors = validate_context(context, files, now=NOW)
    assert "select exactly one automated PR reviewer" in errors

    context = _context("Load-bearing", files, schema_version=3)
    context["data"]["repository"]["pullRequest"]["body"] = context["data"]["repository"][
        "pullRequest"
    ]["body"].replace(
        "## Legal",
        f"| {HUMAN_GATE} | All | {HEAD_SHA} | {GATE_TIMES[HUMAN_GATE]} — APPROVED |\n## Legal",
    )
    assert f"unknown review evidence rows: {HUMAN_GATE}" in validate_context(
        context,
        files,
        now=NOW,
    )


@pytest.mark.parametrize(
    "mutation",
    ["actor", "head", "edited", "attached-comment", "blocking-state"],
)
def test_v3_provider_evidence_is_exact_head_immutable_and_nonblocking(mutation: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context(
        "Load-bearing",
        files,
        automated_gates={CODEX_GATE},
        schema_version=3,
    )
    review = next(
        item
        for item in context["data"]["repository"]["pullRequest"]["reviews"]["nodes"]
        if item["author"]["databaseId"] == BOT_ACTOR_IDS[CODEX_GATE]
    )
    if mutation == "actor":
        review["author"] = _actor(999_999_999)
    elif mutation == "head":
        review["commit"]["oid"] = "b" * 40
    elif mutation == "edited":
        review["lastEditedAt"] = "2026-07-21T12:21:00Z"
    elif mutation == "attached-comment":
        review["comments"]["totalCount"] = 1
    else:
        review["state"] = "CHANGES_REQUESTED"
    assert f"no verified current-head GitHub activity for: {CODEX_GATE}" in validate_context(
        context,
        files,
        now=NOW,
    )


@pytest.mark.parametrize(
    "body",
    [
        _copilot_v3_body(2).replace("2/2", "1/2"),
        _copilot_v3_body(2, generated_comments=1),
        "No findings.",
    ],
)
def test_v3_copilot_requires_its_concise_exhaustive_zero_comment_envelope(body: str) -> None:
    files = [ChangedFile("README.md"), ChangedFile("GOVERNANCE.md")]
    context = _context(
        "Load-bearing",
        files,
        automated_gates={COPILOT_GATE},
        schema_version=3,
    )
    review = next(
        item
        for item in context["data"]["repository"]["pullRequest"]["reviews"]["nodes"]
        if item["author"]["databaseId"] == BOT_ACTOR_IDS[COPILOT_GATE]
    )
    review["body"] = body
    assert f"no verified current-head GitHub activity for: {COPILOT_GATE}" in validate_context(
        context,
        files,
        now=NOW,
    )


@pytest.mark.parametrize(
    "body",
    [
        _coderabbit_v3_body(2).replace(
            "selected for processing (2)", "selected for processing (1)"
        ),
        _coderabbit_v3_body(2, ignored_files=1),
        _coderabbit_v3_body(2).replace(
            "<details>", "<p>Files ignored by provider: 1</p>\n<details>", 1
        ),
        _coderabbit_v3_body(2).replace(
            "Actionable comments posted: 0", "Actionable comments posted: 1"
        ),
        "Review completed.",
    ],
)
def test_v3_coderabbit_trusts_only_its_clean_structured_counts(body: str) -> None:
    files = [ChangedFile("README.md"), ChangedFile("GOVERNANCE.md")]
    context = _context(
        "Load-bearing",
        files,
        automated_gates={CODERABBIT_GATE},
        schema_version=3,
    )
    review = next(
        item
        for item in context["data"]["repository"]["pullRequest"]["reviews"]["nodes"]
        if item["author"]["databaseId"] == BOT_ACTOR_IDS[CODERABBIT_GATE]
    )
    review["body"] = body
    assert f"no verified current-head GitHub activity for: {CODERABBIT_GATE}" in validate_context(
        context,
        files,
        now=NOW,
    )


def test_v3_codex_accepts_the_existing_canonical_immutable_clean_comment() -> None:
    files = [ChangedFile("README.md")]
    context = _context(
        "Load-bearing",
        files,
        automated_gates={CODEX_GATE},
        schema_version=3,
    )
    _use_codex_clean_comment(context, include_trigger=False)
    assert validate_context(context, files, now=NOW) == []


def test_v3_active_human_change_request_blocks_but_approval_is_not_a_gate() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, schema_version=3)
    opinion = context["data"]["repository"]["pullRequest"]["latestHumanOpinions"]["nodes"][0]
    opinion["state"] = "CHANGES_REQUESTED"
    errors = validate_context(context, files, now=NOW)
    assert "an active human maintainer change request remains" in errors


def test_v3_requires_v2_for_changes_to_the_pr_controlled_signal_workflow() -> None:
    files = [ChangedFile(".github/workflows/review-route-invalidation.yml")]
    context = _context("Load-bearing", files, schema_version=3)
    assert (
        "review-route schema v3 cannot modify the PR-controlled review signal workflow; "
        "use the human-gated v2 route"
    ) in validate_context(context, files, now=NOW)


def test_v3_coderabbit_does_not_request_the_v2_global_trigger_ledger() -> None:
    assert not needs_coderabbit_ledger(
        _body(
            "Load-bearing",
            automated_gates={CODERABBIT_GATE},
            schema_version=3,
        )
    )


def test_v3_rendered_route_and_exact_finalizer_validate_without_human_approval() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, schema_version=3)
    pull_request = context["data"]["repository"]["pullRequest"]
    pull_request["reviews"]["nodes"] = [
        review
        for review in pull_request["reviews"]["nodes"]
        if review["author"]["databaseId"] != HUMAN_ID
    ]
    pull_request["reviews"]["totalCount"] = len(pull_request["reviews"]["nodes"])
    pull_request["latestHumanOpinions"] = {"totalCount": 0, "nodes": []}
    _add_finalizer(context)
    assert (
        validate_context(
            context,
            files,
            now=NOW,
            rendered_body=_rendered_route_html("Load-bearing", schema_version=3),
            render_nonce=RENDER_NONCE,
            **_finalizer_kwargs(),
        )
        == []
    )


@pytest.mark.parametrize("route", ["Low", "Standard", "Load-bearing"])
def test_ledger_selector_is_true_only_for_a_valid_v2_coderabbit_lane(route: str) -> None:
    assert needs_coderabbit_ledger(_body(route, automated_gates={CODERABBIT_GATE}))


@pytest.mark.parametrize(
    "body",
    [
        _body("Load-bearing", automated_gates={COPILOT_GATE}),
        _body("Load-bearing", automated_gates={CODEX_GATE}),
        _body("Load-bearing", automated_gates=set()),
        _body("Load-bearing", automated_gates={CODEX_GATE, CODERABBIT_GATE}),
        _legacy_body("Load-bearing"),
        "## Review route\nmalformed",
    ],
)
def test_ledger_selector_rejects_other_or_malformed_routes(body: str) -> None:
    assert not needs_coderabbit_ledger(body)


@pytest.mark.parametrize("gate", [COPILOT_GATE, CODEX_GATE])
def test_unselected_coderabbit_routes_do_not_require_global_ledger(gate: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={gate})
    del context["data"]["repository"]["recentPullRequests"]
    assert validate_context(context, files, now=NOW) == []


def test_selected_coderabbit_without_global_ledger_fails_closed() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODERABBIT_GATE})
    del context["data"]["repository"]["recentPullRequests"]
    assert "CodeRabbit rolling-hour ledger is unavailable" in validate_context(
        context, files, now=NOW
    )


@pytest.mark.parametrize(
    "ledger",
    [
        {},
        {"totalCount": "1", "nodes": []},
        {"totalCount": 1, "nodes": "not-a-list"},
    ],
)
def test_selected_coderabbit_rejects_malformed_global_ledger(
    ledger: dict[str, object],
) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODERABBIT_GATE})
    context["data"]["repository"]["recentPullRequests"] = ledger
    assert "CodeRabbit rolling-hour ledger has an invalid shape" in validate_context(
        context, files, now=NOW
    )


def test_v2_codex_clean_issue_comment_is_verified_on_the_current_head() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    _use_codex_clean_comment(context)
    assert validate_context(context, files, now=NOW) == []


def test_v2_codex_clean_issue_comment_is_review_evidence_without_a_visible_trigger() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    _use_codex_clean_comment(context, include_trigger=False)
    assert validate_context(context, files, now=NOW) == []


@pytest.mark.parametrize(
    "marker",
    [
        "Codex Review: Didn't find any major issues.",
        "Codex Review: Didn't find any major issues. Already looking forward to the next diff.",
        "Codex Review: Didn't find any major issues. Swish!",
    ],
)
def test_v2_codex_current_clean_issue_comment_markers_are_verified(
    marker: str,
) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    _, response = _use_codex_clean_comment(context)
    response["body"] = response["body"].replace(
        CODEX_CLEAN_COMPLETION_MARKER,
        marker,
        1,
    )
    assert validate_context(context, files, now=NOW) == []


@pytest.mark.parametrize(
    "resolved",
    [
        None,
        {"__typename": "Blob", "oid": HEAD_SHA, "abbreviatedOid": HEAD_SHA[:7]},
        {"__typename": "Commit", "oid": 42, "abbreviatedOid": HEAD_SHA[:7]},
        {"__typename": "Commit", "oid": "b" * 40, "abbreviatedOid": HEAD_SHA[:7]},
        {"__typename": "Commit", "oid": HEAD_SHA, "abbreviatedOid": HEAD_SHA[:3]},
        {"__typename": "Commit", "oid": HEAD_SHA, "abbreviatedOid": HEAD_SHA[:11]},
        {"__typename": "Commit", "oid": HEAD_SHA, "abbreviatedOid": "b" * 7},
        {"__typename": "Commit", "oid": HEAD_SHA, "abbreviatedOid": "A" * 7},
    ],
)
def test_v2_codex_clean_comment_requires_safe_full_oid_resolution(
    resolved: dict[str, object] | None,
) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    _use_codex_clean_comment(context)
    context["data"]["repository"]["codexHeadObject"] = resolved
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {CODEX_GATE}" in errors


def test_formal_codex_review_does_not_depend_on_abbreviated_oid_resolution() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    context["data"]["repository"]["codexHeadObject"] = None
    assert validate_context(context, files, now=NOW) == []


@pytest.mark.parametrize("gate", [COPILOT_GATE, CODEX_GATE, CODERABBIT_GATE])
def test_edited_formal_bot_review_is_not_valid_evidence(gate: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={gate})
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]["nodes"]
    bot_review = next(
        review for review in reviews if review["author"]["databaseId"] == BOT_ACTOR_IDS[gate]
    )
    bot_review["lastEditedAt"] = "2026-07-21T12:21:00Z"
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {gate}" in errors


@pytest.mark.parametrize("gate", [COPILOT_GATE, CODEX_GATE, CODERABBIT_GATE])
def test_formal_bot_review_without_edit_metadata_fails_closed(gate: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={gate})
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]["nodes"]
    bot_review = next(
        review for review in reviews if review["author"]["databaseId"] == BOT_ACTOR_IDS[gate]
    )
    del bot_review["lastEditedAt"]
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {gate}" in errors


@pytest.mark.parametrize(
    "case",
    [
        "wrong_actor",
        "user_actor",
        "edited",
        "updated",
        "wrong_first_line",
        "wrong_completion_sentence",
        "oversized_completion_flourish",
        "wrong_head",
        "duplicate_commit",
        "short_commit",
    ],
)
def test_v2_codex_clean_issue_comment_fails_closed_when_untrusted(case: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    _, response = _use_codex_clean_comment(context)
    if case == "wrong_actor":
        response["author"] = _actor(AUTHOR_ID)
    elif case == "user_actor":
        response["author"]["__typename"] = "User"
    elif case == "edited":
        response["lastEditedAt"] = response["createdAt"]
    elif case == "updated":
        response["updatedAt"] = "2026-07-21T12:21:00Z"
    elif case == "wrong_first_line":
        response["body"] = response["body"].replace(
            CODEX_CLEAN_COMPLETION_MARKER,
            f"> {CODEX_CLEAN_COMPLETION_MARKER}",
            1,
        )
    elif case == "wrong_completion_sentence":
        response["body"] = response["body"].replace(
            CODEX_CLEAN_COMPLETION_MARKER,
            "Codex Review: Didn't complete the review. Swish!",
            1,
        )
    elif case == "oversized_completion_flourish":
        response["body"] = response["body"].replace(
            CODEX_CLEAN_COMPLETION_MARKER,
            "Codex Review: Didn't find any major issues. " + ("x" * 161),
            1,
        )
    elif case == "wrong_head":
        response["body"] = response["body"].replace(HEAD_SHA[:10], "b" * 10)
    elif case == "duplicate_commit":
        response["body"] += f"\n**Reviewed commit:** `{HEAD_SHA[:10]}`"
    elif case == "short_commit":
        response["body"] = response["body"].replace(HEAD_SHA[:10], HEAD_SHA[:9])
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {CODEX_GATE}" in errors


@pytest.mark.parametrize("same_second", [False, True])
def test_later_codex_noncompletion_does_not_supersede_clean_comment(
    same_second: bool,
) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    _use_codex_clean_comment(context)
    when = GATE_TIMES[CODEX_GATE] if same_second else "2026-07-21T12:21:00Z"
    pull_request = context["data"]["repository"]["pullRequest"]
    pull_request["comments"]["nodes"].append(
        _comment(
            BOT_ACTOR_IDS[CODEX_GATE],
            "Codex review did not produce a clean completion.",
            when,
            association="NONE",
            typename="Bot",
        )
    )
    pull_request["comments"]["totalCount"] += 1
    assert validate_context(context, files, now=NOW) == []


@pytest.mark.parametrize("use_clean_comment", [False, True])
@pytest.mark.parametrize("request_state", ["visible", "edited", "deleted"])
def test_codex_request_is_invocation_only(
    use_clean_comment: bool,
    request_state: str,
) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    if use_clean_comment:
        _use_codex_clean_comment(context)
    pull_request = context["data"]["repository"]["pullRequest"]
    request = _comment(
        HUMAN_ID,
        CODEX_TRIGGER,
        "2026-07-21T12:21:00Z",
        association="MEMBER",
    )
    if request_state == "edited":
        request["body"] = "cancelled"
        request["updatedAt"] = "2026-07-21T12:22:00Z"
        request["lastEditedAt"] = "2026-07-21T12:22:00Z"
    if request_state != "deleted":
        pull_request["comments"]["nodes"].append(request)
        pull_request["comments"]["totalCount"] += 1
    assert validate_context(context, files, now=NOW) == []


def test_multiple_codex_requests_can_share_one_later_current_head_result() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    _, response = _use_codex_clean_comment(context)
    pull_request = context["data"]["repository"]["pullRequest"]
    comments = [
        _comment(AUTHOR_ID, CODEX_TRIGGER, "2026-07-21T12:18:00Z", association="OWNER"),
        _comment(HUMAN_ID, CODEX_TRIGGER, "2026-07-21T12:19:00Z", association="MEMBER"),
        response,
    ]
    pull_request["comments"] = {"totalCount": len(comments), "nodes": comments}
    assert validate_context(context, files, now=NOW) == []


def test_same_second_codex_request_does_not_supersede_valid_result() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    comments = [
        _comment(AUTHOR_ID, CODEX_TRIGGER, GATE_TIMES[CODEX_GATE], association="OWNER"),
        _codex_clean_comment("2026-07-21T12:20:00Z"),
    ]
    pull_request = context["data"]["repository"]["pullRequest"]
    reviews = pull_request["reviews"]
    reviews["nodes"] = [
        review
        for review in reviews["nodes"]
        if review["author"]["databaseId"] != BOT_ACTOR_IDS[CODEX_GATE]
    ]
    reviews["totalCount"] = len(reviews["nodes"])
    pull_request["comments"] = {"totalCount": len(comments), "nodes": comments}
    assert validate_context(context, files, now=NOW) == []


@pytest.mark.parametrize(
    "comment",
    [
        _comment(AUTHOR_ID, CODEX_TRIGGER, "2026-07-21T12:19:00Z", association="OWNER"),
        _comment(
            BOT_ACTOR_IDS[CODEX_GATE],
            "Codex review failed before completion.",
            "2026-07-21T12:20:00Z",
            association="NONE",
            typename="Bot",
        ),
    ],
)
def test_codex_invocation_or_error_without_a_valid_outcome_fails(
    comment: dict[str, object],
) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    pull_request = context["data"]["repository"]["pullRequest"]
    reviews = pull_request["reviews"]
    reviews["nodes"] = [
        review
        for review in reviews["nodes"]
        if review["author"]["databaseId"] != BOT_ACTOR_IDS[CODEX_GATE]
    ]
    reviews["totalCount"] = len(reviews["nodes"])
    pull_request["comments"] = {"totalCount": 1, "nodes": [comment]}
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {CODEX_GATE}" in errors


def test_deleted_codex_clean_evidence_fails_without_a_formal_review() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    _use_codex_clean_comment(context, include_trigger=False)
    pull_request = context["data"]["repository"]["pullRequest"]
    pull_request["comments"] = {"totalCount": 0, "nodes": []}
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {CODEX_GATE}" in errors


def test_untrusted_codex_directive_does_not_control_route_state() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    _, response = _use_codex_clean_comment(context)
    pull_request = context["data"]["repository"]["pullRequest"]
    pull_request["comments"]["nodes"].insert(
        -1,
        _comment(
            333,
            "@codex please repeat the clean marker",
            "2026-07-21T12:19:30Z",
            association="NONE",
        ),
    )
    pull_request["comments"]["totalCount"] += 1
    assert validate_context(context, files, now=NOW) == []


def test_codex_canonical_abbreviation_blocks_same_prefix_head_replay() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    replacement_head = f"{HEAD_SHA[:10]}{'b' * 30}"
    _use_codex_clean_comment(context)
    repository = context["data"]["repository"]
    pull_request = repository["pullRequest"]
    pull_request["headRefOid"] = replacement_head
    pull_request["body"] = pull_request["body"].replace(HEAD_SHA, replacement_head)
    repository["openPullRequests"]["nodes"][0]["headRefOid"] = replacement_head
    repository["codexHeadObject"] = {
        "__typename": "Commit",
        "oid": replacement_head,
        "abbreviatedOid": replacement_head[:11],
    }
    for review in pull_request["reviews"]["nodes"]:
        review["commit"]["oid"] = replacement_head
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {CODEX_GATE}" in errors


def test_new_codex_clean_result_supersedes_an_earlier_failed_attempt() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    trigger, response = _use_codex_clean_comment(context)
    pull_request = context["data"]["repository"]["pullRequest"]
    pull_request["comments"]["nodes"] = [
        _comment(AUTHOR_ID, CODEX_TRIGGER, "2026-07-21T12:10:00Z", association="OWNER"),
        _comment(
            BOT_ACTOR_IDS[CODEX_GATE],
            "Codex review failed before completion.",
            "2026-07-21T12:11:00Z",
            association="NONE",
            typename="Bot",
        ),
        trigger,
        response,
    ]
    pull_request["comments"]["totalCount"] = 4
    assert validate_context(context, files, now=NOW) == []


def test_formal_codex_review_requires_the_bot_actor_type() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]
    codex_review = next(
        review
        for review in reviews["nodes"]
        if review["author"]["databaseId"] == BOT_ACTOR_IDS[CODEX_GATE]
    )
    codex_review["author"]["__typename"] = "User"
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {CODEX_GATE}" in errors


@pytest.mark.parametrize(
    "automated_gates",
    [set(), {COPILOT_GATE, CODEX_GATE}],
)
def test_v2_requires_exactly_one_selected_automated_reviewer(
    automated_gates: set[str],
) -> None:
    files = [ChangedFile("docs/typo-fix.md")]
    context = _context("Low", files, automated_gates=automated_gates)
    assert "select exactly one automated PR reviewer" in validate_context(context, files, now=NOW)


def test_v2_requires_each_automated_reviewer_checkbox_once() -> None:
    files = [ChangedFile("docs/typo-fix.md")]
    body = _body("Low").replace(
        "| Required review gate",
        "- [ ] Copilot — duplicate automated review\n| Required review gate",
    )
    context = _context("Low", files, body=body)
    assert "expected each automated reviewer checkbox exactly once" in validate_context(
        context, files, now=NOW
    )


@pytest.mark.parametrize(
    ("canonical", "malformed", "expected_error"),
    [
        ("- [x] Low", "- [x] low", "expected each canonical route checkbox exactly once"),
        (
            "- [x] Copilot",
            "- [x] copilot",
            "expected each automated reviewer checkbox exactly once",
        ),
    ],
)
def test_noncanonical_checkbox_casing_fails_without_crashing(
    canonical: str, malformed: str, expected_error: str
) -> None:
    files = [ChangedFile("docs/typo-fix.md")]
    body = _body("Low").replace(canonical, malformed)
    context = _context("Low", files, body=body)
    assert expected_error in validate_context(context, files, now=NOW)


@pytest.mark.parametrize(
    ("canonical", "malformed", "expected_error"),
    [
        (
            "- [x] Low",
            "-[x] Low",
            "expected each canonical route checkbox exactly once",
        ),
        (
            "- [x] Low",
            "- [x]Low",
            "expected each canonical route checkbox exactly once",
        ),
        (
            "- [x] Low",
            "-\n[x]\nLow",
            "expected each canonical route checkbox exactly once",
        ),
        (
            "- [x] Low",
            "-     [x] Low",
            "expected each canonical route checkbox exactly once",
        ),
        (
            "- [x] Copilot",
            "-[x] Copilot",
            "expected each automated reviewer checkbox exactly once",
        ),
        (
            "- [x] Copilot",
            "- [x]Copilot",
            "expected each automated reviewer checkbox exactly once",
        ),
        (
            "- [x] Copilot",
            "-\n[x]\nCopilot",
            "expected each automated reviewer checkbox exactly once",
        ),
        (
            "- [x] Copilot",
            "-     [x] Copilot",
            "expected each automated reviewer checkbox exactly once",
        ),
    ],
)
def test_task_list_rows_require_horizontal_whitespace(
    canonical: str, malformed: str, expected_error: str
) -> None:
    files = [ChangedFile("docs/typo-fix.md")]
    body = _body("Low").replace(canonical, malformed)
    context = _context("Low", files, body=body)
    assert expected_error in validate_context(context, files, now=NOW)


def test_v2_unused_provider_evidence_must_be_na() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    pull_request = context["data"]["repository"]["pullRequest"]
    pull_request["body"] = pull_request["body"].replace(
        f"| {COPILOT_GATE} | scope | N/A | N/A |",
        (f"| {COPILOT_GATE} | scope | {HEAD_SHA} | {GATE_TIMES[COPILOT_GATE]} — COMPLETE |"),
    )
    errors = validate_context(context, files, now=NOW)
    assert f"nonapplicable gate must use N/A in both cells: {COPILOT_GATE}" in errors


@pytest.mark.parametrize("gate", [COPILOT_GATE, CODEX_GATE, CODERABBIT_GATE])
def test_v2_missing_selected_provider_activity_fails(gate: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={gate})
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]
    reviews["nodes"] = [
        review
        for review in reviews["nodes"]
        if review["author"]["databaseId"] != BOT_ACTOR_IDS[gate]
    ]
    reviews["totalCount"] = len(reviews["nodes"])
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {gate}" in errors


def test_v2_coderabbit_does_not_require_a_codex_review() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODERABBIT_GATE})
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]
    reviews["nodes"] = [
        review
        for review in reviews["nodes"]
        if review["author"]["databaseId"] != BOT_ACTOR_IDS[CODEX_GATE]
    ]
    reviews["totalCount"] = len(reviews["nodes"])
    assert validate_context(context, files, now=NOW) == []


@pytest.mark.parametrize("gate", [COPILOT_GATE, CODEX_GATE])
def test_v2_global_coderabbit_quota_does_not_block_other_providers(gate: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={gate})
    recent = context["data"]["repository"]["recentPullRequests"]
    trigger = _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:19:00Z")
    recent["nodes"].append(
        {
            "updatedAt": "2026-07-21T12:19:00Z",
            "comments": {"totalCount": 6, "nodes": [trigger] * 6},
        }
    )
    recent["totalCount"] += 1
    assert validate_context(context, files, now=NOW) == []


@pytest.mark.parametrize("gate", [COPILOT_GATE, CODEX_GATE])
def test_v2_unselected_manual_coderabbit_request_fails(gate: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={gate})
    repository = context["data"]["repository"]
    comments = [
        _comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:24:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:25:00Z"),
    ]
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    errors = validate_context(context, files, now=NOW)
    assert "manual CodeRabbit triggers require selecting the CodeRabbit lane" in errors


@pytest.mark.parametrize("gate", [COPILOT_GATE, CODEX_GATE])
def test_v2_unselected_coderabbit_trigger_bound_to_old_head_allows_fallback(
    gate: str,
) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={gate})
    repository = context["data"]["repository"]
    comments = [
        _comment(AUTHOR_ID, f"coderabbit-reservation: {'b' * 40}", "2026-07-21T12:24:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:25:00Z"),
    ]
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    assert validate_context(context, files, now=NOW) == []


@pytest.mark.parametrize("gate", [COPILOT_GATE, CODEX_GATE])
def test_v2_abandoned_coderabbit_reservation_allows_provider_fallback(gate: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={gate})
    repository = context["data"]["repository"]
    comments = [_comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:24:00Z")]
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    assert validate_context(context, files, now=NOW) == []


def test_v2_selected_coderabbit_reservation_without_trigger_is_incomplete() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODERABBIT_GATE})
    repository = context["data"]["repository"]
    comments = [_comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:24:00Z")]
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    errors = validate_context(context, files, now=NOW)
    assert "CodeRabbit needs a current-SHA reservation, trigger, then completed review" in errors


def test_v2_selected_coderabbit_rejects_duplicate_reservation_before_trigger() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODERABBIT_GATE})
    repository = context["data"]["repository"]
    comments = [
        _comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:23:00Z"),
        _comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:24:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:25:00Z"),
    ]
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    assert "CodeRabbit reservations and triggers must form one-to-one pairs" in validate_context(
        context, files, now=NOW
    )


def test_v2_selected_coderabbit_accepts_fifo_queued_reservations() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODERABBIT_GATE})
    repository = context["data"]["repository"]
    comments = [
        _comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:22:00Z"),
        _comment(333, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:23:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:24:00Z"),
        _comment(333, "@coderabbitai full review", "2026-07-21T12:26:00Z"),
    ]
    _add_coderabbit_completion(context, "2026-07-21T12:25:00Z")
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    assert validate_context(context, files, now=NOW) == []


def test_v2_coderabbit_triggers_cannot_share_one_completion() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODERABBIT_GATE})
    repository = context["data"]["repository"]
    comments = [
        _comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:22:00Z"),
        _comment(333, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:23:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:24:00Z"),
        _comment(333, "@coderabbitai full review", "2026-07-21T12:25:00Z"),
    ]
    repository["pullRequest"]["comments"] = {"totalCount": len(comments), "nodes": comments}
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    assert (
        "wait for each CodeRabbit review completion before triggering again"
        in validate_context(context, files, now=NOW)
    )


def test_v2_coderabbit_triggers_must_serialize_even_with_two_later_completions() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODERABBIT_GATE})
    repository = context["data"]["repository"]
    comments = [
        _comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:22:00Z"),
        _comment(333, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:23:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:24:00Z"),
        _comment(333, "@coderabbitai full review", "2026-07-21T12:25:00Z"),
    ]
    _add_coderabbit_completion(context, "2026-07-21T12:26:00Z")
    repository["pullRequest"]["comments"] = {"totalCount": len(comments), "nodes": comments}
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    assert (
        "wait for each CodeRabbit review completion before triggering again"
        in validate_context(context, files, now=NOW)
    )


def test_v2_coderabbit_next_trigger_must_follow_completion_by_a_full_second() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODERABBIT_GATE})
    repository = context["data"]["repository"]
    comments = [
        _comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:22:00Z"),
        _comment(333, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:23:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:24:00Z"),
        _comment(333, "@coderabbitai full review", "2026-07-21T12:25:00Z"),
    ]
    _add_coderabbit_completion(context, "2026-07-21T12:25:00Z")
    repository["pullRequest"]["comments"] = {"totalCount": len(comments), "nodes": comments}
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    assert (
        "wait for each CodeRabbit review completion before triggering again"
        in validate_context(context, files, now=NOW)
    )


def test_v2_selected_coderabbit_uses_comment_id_for_same_second_reservation_fifo() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODERABBIT_GATE})
    repository = context["data"]["repository"]
    comments = [
        _comment(
            333,
            f"coderabbit-reservation: {HEAD_SHA}",
            "2026-07-21T12:22:00Z",
            comment_id=1002,
        ),
        _comment(
            AUTHOR_ID,
            f"coderabbit-reservation: {HEAD_SHA}",
            "2026-07-21T12:22:00Z",
            comment_id=1001,
        ),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:24:00Z"),
        _comment(333, "@coderabbitai full review", "2026-07-21T12:25:00Z"),
    ]
    _add_coderabbit_completion(context, "2026-07-21T12:24:30Z")
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    assert validate_context(context, files, now=NOW) == []


def test_v2_selected_coderabbit_allows_cross_attempt_same_second_fifo() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODERABBIT_GATE})
    repository = context["data"]["repository"]
    comments = [
        _comment(
            AUTHOR_ID,
            f"coderabbit-reservation: {HEAD_SHA}",
            "2026-07-21T12:22:00Z",
            comment_id=1000,
        ),
        _comment(
            AUTHOR_ID,
            "@coderabbitai full review",
            "2026-07-21T12:23:00Z",
            comment_id=1001,
        ),
        _comment(
            333,
            f"coderabbit-reservation: {HEAD_SHA}",
            "2026-07-21T12:23:00Z",
            comment_id=1002,
        ),
        _comment(
            333,
            "@coderabbitai full review",
            "2026-07-21T12:24:00Z",
            comment_id=1003,
        ),
    ]
    _add_coderabbit_completion(context, "2026-07-21T12:23:30Z")
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    assert validate_context(context, files, now=NOW) == []


def test_v2_selected_coderabbit_rejects_reverse_reservation_order() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODERABBIT_GATE})
    repository = context["data"]["repository"]
    comments = [
        _comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:22:00Z"),
        _comment(333, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:23:00Z"),
        _comment(333, "@coderabbitai full review", "2026-07-21T12:24:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:25:00Z"),
    ]
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    assert "CodeRabbit triggers must follow reservation FIFO order" in validate_context(
        context, files, now=NOW
    )


@pytest.mark.parametrize("gate", [COPILOT_GATE, CODEX_GATE])
def test_v2_unselected_orphan_coderabbit_trigger_fails_closed(gate: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={gate})
    repository = context["data"]["repository"]
    comments = [_comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:25:00Z")]
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    errors = validate_context(context, files, now=NOW)
    assert "manual CodeRabbit triggers require selecting the CodeRabbit lane" in errors


def test_v2_draft_may_defer_exactly_one_provider_selection() -> None:
    files = [ChangedFile("docs/typo-fix.md")]
    context = _context(
        "Low",
        files,
        draft=True,
        complete=False,
        automated_gates={COPILOT_GATE, CODEX_GATE},
    )
    assert validate_context(context, files, now=NOW) == []


@pytest.mark.parametrize("route", ["Low", "Standard", "Load-bearing"])
@pytest.mark.parametrize("draft", [False, True])
def test_v1_route_bodies_require_migration_to_one_provider(route: str, draft: bool) -> None:
    files = [ChangedFile("README.md")]
    context = _context(route, files, body=_legacy_body(route), draft=draft)
    errors = validate_context(context, files, now=NOW)
    assert (
        "review-route schema v1 is obsolete; migrate to v2 and select one automated reviewer"
        in errors
    )


@pytest.mark.parametrize("route", ["Low", "Standard", "Load-bearing"])
def test_v1_rendered_route_reports_only_the_migration_error(route: str) -> None:
    files = [
        ChangedFile(
            {
                "Low": "docs/typo-fix.md",
                "Standard": "frontend/src/hooks/useDialogFocus.ts",
                "Load-bearing": "README.md",
            }[route]
        )
    ]
    legacy_bots = {
        "Low": {COPILOT_GATE},
        "Standard": {COPILOT_GATE, CODEX_GATE},
        "Load-bearing": {COPILOT_GATE, CODEX_GATE, CODERABBIT_GATE},
    }[route]
    context = _context(route, files, body=_legacy_body(route))
    errors = validate_context(
        context,
        files,
        now=NOW,
        rendered_body=_rendered_route_html(
            route,
            automated_gates=legacy_bots,
            schema_version=1,
        ),
        render_nonce=RENDER_NONCE,
    )
    assert (
        "review-route schema v1 is obsolete; migrate to v2 and select one automated reviewer"
        in errors
    )
    assert RENDERED_ROUTE_ERROR not in errors


def test_protected_rename_raises_route_floor() -> None:
    files = [ChangedFile("docs/ci-history.md", previous_filename=".github/workflows/ci.yml")]
    errors = validate_context(_context("Low", files), files, now=NOW)
    assert "selected route Low is below changed-path minimum Load-bearing" in errors


@pytest.mark.parametrize(
    "path",
    [
        ".github/CODEOWNERS",
        ".github/ISSUE_TEMPLATE/feature_request.md",
        ".github/copilot-instructions.md",
        ".github/instructions/reviews.instructions.md",
        ".github/dependabot.yml",
        ".npmrc",
        ".pre-commit-config.yaml",
        "CODEOWNERS",
        "docs/CODEOWNERS",
        "constraints.txt",
        "constraints-dev.in",
        "environment.yml",
        "setup.cfg",
        "setup.py",
        "uv.lock",
        ".gitattributes",
        ".gitignore",
        ".gitmodules",
        ".graphifyignore",
        "CITATION.cff",
        "CHANGELOG.md",
        "GOVERNANCE.md",
        "LICENSE",
        "NOTICE",
        "README.md",
        "SUPPORT.md",
        "backend/auth.py",
        "backend/__init__.py",
        "backend/api/gating.py",
        "backend/api/routes/backup.py",
        "backend/api/routes/nuclear.py",
        "backend/api/routes/query_builder.py",
        "backend/api/routes/setup.py",
        "backend/config.py",
        "backend/main.py",
        "backend/models/sample.py",
        "backend/query/translator.py",
        "backend/services/sample_delete.py",
        "backend/services/sample_merge.py",
        "backend/tasks/huey_tasks.py",
        "backend/utils/update_checker.py",
        "Dockerfile.dev",
        "docs/_includes/health-disclaimer.md",
        "docs/assets/img/dashboard.png",
        "docs/attribution.md",
        "docs/ancestry-methods.md",
        "docs/bundle-release-runbook.md",
        "docs/external-inputs-strategy.md",
        "docs/develop/architecture.md",
        "docs/features/settings-and-admin.md",
        "docs/getting-started/multi-sample-merging.md",
        "docs/getting-started/reading-your-results.md",
        "docs/index.md",
        "docs/install/reference-data.md",
        "docs/internal/expansion-second-wave-REMAINING.md",
        "docs/internal/sex_inference_threshold_validation.md",
        "docs/lai-bundle-release-runbook-env.lock.yaml",
        "docs/lai-bundle-release-runbook.md",
        "docs/maintainer/release-process.md",
        "docs/modules/health-risk/cancer.md",
        "docs/modules/hla.md",
        "frontend/src/api/auth.ts",
        "frontend/.gitignore",
        "frontend/index.html",
        "frontend/knip.jsonc",
        "frontend/src/api/annotation.ts",
        "frontend/src/api/backup.ts",
        "frontend/src/api/cardiovascular.ts",
        "frontend/src/api/db-health.ts",
        "frontend/src/api/nuclear.ts",
        "frontend/src/api/updates.ts",
        "frontend/scripts/run-vitest-strict.sh",
        "frontend/src/components/AuthGuard.tsx",
        "frontend/src/components/fitness/PathwayCard.tsx",
        "frontend/src/components/layout/IndividualSelector.tsx",
        "frontend/src/components/results/SummaryCard.tsx",
        "frontend/src/components/individuals/MergeWizard.tsx",
        "frontend/src/components/settings/ExportBackup.tsx",
        "frontend/src/components/settings/NuclearDelete.tsx",
        "frontend/src/components/settings/UpdateManager.tsx",
        "frontend/src/components/setup/CredentialsStep.tsx",
        "frontend/src/components/upload/FileUpload.tsx",
        "frontend/src/components/variants/VariantTable.tsx",
        "frontend/src/lib/hgvsInfo.ts",
        "frontend/src/lib/ensemblePathogenicLabel.ts",
        "frontend/src/lib/format.ts",
        "frontend/src/lib/inSilicoScoreInfo.ts",
        "frontend/src/lib/insilico.ts",
        "frontend/src/lib/modules.ts",
        "frontend/src/lib/navigation.ts",
        "frontend/src/lib/nav-routes.ts",
        "frontend/src/lib/pathwayCoverage.ts",
        "frontend/src/lib/resultLabels.ts",
        "frontend/src/lib/snpCategory.ts",
        "frontend/src/constants/thresholds.ts",
        "frontend/src/pages/AllergyView.tsx",
        "frontend/src/pages/FHView.tsx",
        "frontend/src/pages/FitnessView.tsx",
        "frontend/src/pages/SkinView.tsx",
        "frontend/src/pages/SleepView.tsx",
        "frontend/src/pages/TraitsPersonalityView.tsx",
        "frontend/src/pages/Settings.tsx",
        "frontend/src/pages/SetupWizard.tsx",
        "frontend/src/components/igv-browser/IgvBrowser.tsx",
        "services/pyproject.toml",
        "launchd/com.yeliztli.api.plist",
        "mkdocs.yml",
        "systemd/yeliztli-huey.service",
        "tests/backend/test_backup_api.py",
        "tests/backend/annotation_validation/test_m2_truth_set.py",
        "tests/backend/test_annotation_engine.py",
        "tests/backend/test_clinvar_contract.py",
        "tests/backend/test_dbnsfp.py",
        "tests/backend/test_dbsnp.py",
        "tests/backend/test_logging_config.py",
        "tests/backend/test_main_lifespan.py",
        "tests/backend/test_nuclear_delete.py",
        "tests/backend/test_sample_merge.py",
        "tests/backend/test_setup_api.py",
        "tests/backend/test_update_checker.py",
        "frontend/src/test/disclaimer-body.test.tsx",
        "frontend/src/test/annotation-coverage.test.ts",
        "frontend/src/test/cardiovascular.test.tsx",
        "frontend/src/test/ensemblePathogenicLabel.test.ts",
        "frontend/src/test/genome-browser.test.tsx",
        "frontend/src/test/hpo-term-list.test.tsx",
        "frontend/src/test/insilico.test.ts",
        "frontend/src/test/nuclear-delete.test.tsx",
        "frontend/src/test/setup-wizard.test.tsx",
        "frontend/src/test/system-health.test.tsx",
        "frontend/src/types/sleep.ts",
        "frontend/src/test/api-individuals.test.tsx",
        "frontend/src/test/individual-detail.test.tsx",
        "frontend/src/test/waveb.test.tsx",
        "tests/e2e/categorical-indeterminate.spec.ts",
        "tests/e2e/cadd-revel-score-tooltips.spec.ts",
        "tests/e2e/hpo-term-labels.spec.ts",
        "tests/e2e/merge-samples.spec.ts",
        "tests/e2e/new-module.spec.ts",
        "tests/e2e/setup-disclaimer-markdown.spec.ts",
        "tests/e2e/system-health-log-levels.spec.ts",
        "tests/e2e/individuals.spec.ts",
        "tests/conftest.py",
        "tests/fixtures/reference.json",
        "tests/manual/wsl2_checklist.md",
    ],
)
def test_sensitive_paths_require_load_bearing_route(path: str) -> None:
    files = [ChangedFile(path)]
    errors = validate_context(_context("Standard", files), files, now=NOW)
    assert "selected route Standard is below changed-path minimum Load-bearing" in errors


@pytest.mark.parametrize(
    "path",
    [
        ".GITHUB/PULL_REQUEST_TEMPLATE.MD",
        ".GITHUB/WORKFLOWS/CI.YML",
        "README.MD",
        "FRONTEND/SRC/APP.TSX",
        "BACKEND/CONFIG.PY",
    ],
)
def test_sensitive_path_case_variants_remain_load_bearing(path: str) -> None:
    assert minimum_route([ChangedFile(path)]) == "Load-bearing"


@pytest.mark.parametrize(
    ("path", "route"),
    [
        ("README.md", "Load-bearing"),
        ("frontend/src/hooks/useDialogFocus.ts", "Standard"),
        ("FRONTEND/SRC/HOOKS/useDialogFocus.ts", "Standard"),
        ("frontend/src/index.css", "Standard"),
        ("docs/typo-fix.md", "Low"),
    ],
)
def test_non_sensitive_paths_keep_lower_route_floors(path: str, route: str) -> None:
    assert minimum_route([ChangedFile(path)]) == route


def test_changed_file_count_mismatch_fails_closed() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    context["data"]["repository"]["pullRequest"]["changedFiles"] = 2
    assert "changed-file API count does not match the pull request" in validate_context(
        context, files, now=NOW
    )


@pytest.mark.parametrize("gate", [COPILOT_GATE, CODEX_GATE, CODERABBIT_GATE])
def test_wrong_head_sha_invalidates_selected_provider_evidence(gate: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={gate})
    pull_request = context["data"]["repository"]["pullRequest"]
    pull_request["body"] = pull_request["body"].replace(
        f"| {gate} | scope | {HEAD_SHA} |",
        f"| {gate} | scope | {'b' * 40} |",
    )
    errors = validate_context(context, files, now=NOW)
    assert f"required gate is not bound to the current head SHA: {gate}" in errors


def test_expected_head_mismatch_fails_closed() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    errors = validate_context(context, files, now=NOW, expected_head="b" * 40)
    assert "GitHub context head SHA changed during validation" in errors


def test_expected_draft_mismatch_fails_closed() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files, draft=True)
    errors = validate_context(context, files, now=NOW, expected_draft=False)
    assert "GitHub context draft state changed during validation" in errors


def test_expected_pr_update_mismatch_fails_closed() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files)
    errors = validate_context(
        context,
        files,
        now=NOW,
        expected_pr_updated_at="2026-07-21T12:44:59Z",
    )
    assert "pull request state changed during validation" in errors


def test_expected_pr_body_mismatch_fails_closed() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files)
    errors = validate_context(
        context,
        files,
        now=NOW,
        expected_pr_body="different same-second body",
    )
    assert "GitHub context body changed during validation" in errors


@pytest.mark.parametrize(
    "body",
    [False, 0, [], {}],
    ids=["false", "zero", "list", "object"],
)
def test_expected_snapshot_body_rejects_falsy_non_strings(body: object) -> None:
    with pytest.raises(TypeError):
        _expected_snapshot_body({"body": body})


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        ({}, ""),
        ({"body": None}, ""),
        ({"body": ""}, ""),
        ({"body": "route"}, "route"),
    ],
)
def test_expected_snapshot_body_preserves_nullable_rest_contract(
    snapshot: dict[str, object],
    expected: str,
) -> None:
    assert _expected_snapshot_body(snapshot) == expected


def test_expected_snapshot_body_requires_an_object() -> None:
    with pytest.raises(TypeError):
        _expected_snapshot_body([])


def test_valid_immutable_finalizer_after_all_gates_succeeds() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files)
    _add_finalizer(context)
    assert validate_context(context, files, now=NOW, **_finalizer_kwargs()) == []


@pytest.mark.parametrize("permission", ["read", "triage", "maintain", "none"])
def test_finalizer_requires_live_repository_write_permission(permission: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files)
    _add_finalizer(context)
    finalizer = _finalizer_kwargs()
    finalizer["finalize_comment_actor_permission"] = permission
    errors = validate_context(context, files, now=NOW, **finalizer)
    assert "finalizer actor does not have live repository write permission" in errors


def test_partial_finalizer_identity_fails_closed() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files)
    errors = validate_context(
        context,
        files,
        now=NOW,
        finalize_comment_node_id=FINALIZER_NODE_ID,
    )
    assert "finalizer event context is incomplete" in errors


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("missing", "finalizer comment is missing from the live pull request"),
        ("wrong-node", "finalizer comment is missing from the live pull request"),
        ("wrong-actor", "finalizer comment is not from the expected trusted maintainer"),
        ("untrusted", "finalizer comment is not from the expected trusted maintainer"),
        ("wrong-body", "finalizer comment body is not the exact validation command"),
        ("leading-whitespace", "finalizer comment body is not the exact validation command"),
        ("trailing-whitespace", "finalizer comment body is not the exact validation command"),
        ("edited", "finalizer comment was edited after creation"),
        ("same-second-edit", "finalizer comment was edited after creation"),
        ("wrong-time", "finalizer comment creation time does not match the triggering event"),
    ],
)
def test_invalid_finalizer_identity_fails_closed(mutation: str, expected_error: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files)
    if mutation != "missing":
        comment = _add_finalizer(context)
        if mutation == "wrong-node":
            comment["id"] = "IC_kwDODifferentComment"
        elif mutation == "wrong-actor":
            comment["author"] = _actor(333, "User")
        elif mutation == "untrusted":
            comment["authorAssociation"] = "NONE"
        elif mutation == "wrong-body":
            comment["body"] = "please /validate-route"
        elif mutation == "leading-whitespace":
            comment["body"] = " /validate-route"
        elif mutation == "trailing-whitespace":
            comment["body"] = "/validate-route\n"
        elif mutation == "edited":
            comment["lastEditedAt"] = "2026-07-21T12:46:00Z"
            comment["updatedAt"] = "2026-07-21T12:46:00Z"
        elif mutation == "same-second-edit":
            comment["lastEditedAt"] = comment["createdAt"]
        elif mutation == "wrong-time":
            comment["createdAt"] = "2026-07-21T12:44:59Z"
            comment["updatedAt"] = comment["createdAt"]
    errors = validate_context(context, files, now=NOW, **_finalizer_kwargs())
    assert expected_error in errors


@pytest.mark.parametrize("finalizer_at", [GATE_TIMES[HUMAN_GATE], COMMITTED_AT])
def test_finalizer_must_strictly_follow_all_gates(finalizer_at: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files)
    _add_finalizer(context, created_at=finalizer_at)
    errors = validate_context(
        context,
        files,
        now=NOW,
        **_finalizer_kwargs(created_at=finalizer_at),
    )
    assert "finalizer comment must strictly follow every required review gate" in errors


def test_missing_copilot_activity_fails() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]
    reviews["nodes"] = [
        review
        for review in reviews["nodes"]
        if review["author"]["databaseId"] != BOT_ACTOR_IDS[COPILOT_GATE]
    ]
    reviews["totalCount"] = len(reviews["nodes"])
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {COPILOT_GATE}" in errors


@pytest.mark.parametrize("gate", [COPILOT_GATE, CODEX_GATE, CODERABBIT_GATE])
def test_same_second_extra_review_state_fails_closed(gate: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={gate})
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]
    reviews["nodes"].append(
        _review(
            BOT_ACTOR_IDS[gate],
            GATE_TIMES[gate],
            state="DISMISSED",
            body="Review state is ambiguous at this timestamp.",
        )
    )
    reviews["totalCount"] += 1
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {gate}" in errors


@pytest.mark.parametrize("gate", [COPILOT_GATE, CODEX_GATE, CODERABBIT_GATE])
@pytest.mark.parametrize("state", ["CHANGES_REQUESTED", "DISMISSED"])
def test_later_formal_bot_noncompletion_supersedes_older_completion(
    gate: str,
    state: str,
) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={gate})
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]
    reviews["nodes"].append(
        _review(
            BOT_ACTOR_IDS[gate],
            "2026-07-21T12:35:00Z",
            state=state,
            body="A later formal review did not complete cleanly.",
        )
    )
    reviews["totalCount"] += 1
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {gate}" in errors


def test_later_formal_codex_noncompletion_supersedes_clean_comment() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    _use_codex_clean_comment(context)
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]
    reviews["nodes"].append(
        _review(
            BOT_ACTOR_IDS[CODEX_GATE],
            "2026-07-21T12:35:00Z",
            state="CHANGES_REQUESTED",
        )
    )
    reviews["totalCount"] += 1
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {CODEX_GATE}" in errors


def test_formal_codex_completion_after_noncompletion_restores_lane() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    pull_request = context["data"]["repository"]["pullRequest"]
    pull_request["body"] = pull_request["body"].replace(
        GATE_TIMES[CODEX_GATE],
        "2026-07-21T12:35:00Z",
    )
    reviews = pull_request["reviews"]
    reviews["nodes"].extend(
        [
            _review(
                BOT_ACTOR_IDS[CODEX_GATE],
                "2026-07-21T12:30:00Z",
                state="CHANGES_REQUESTED",
            ),
            _review(BOT_ACTOR_IDS[CODEX_GATE], "2026-07-21T12:35:00Z"),
        ]
    )
    reviews["totalCount"] += 2
    assert validate_context(context, files, now=NOW) == []


@pytest.mark.parametrize("gate", [COPILOT_GATE, CODEX_GATE, CODERABBIT_GATE])
def test_later_edited_formal_review_supersedes_older_completion(gate: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={gate})
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]
    reviews["nodes"].append(
        _review(
            BOT_ACTOR_IDS[gate],
            "2026-07-21T12:35:00Z",
            state="CHANGES_REQUESTED",
            last_edited_at="2026-07-21T12:36:00Z",
        )
    )
    reviews["totalCount"] += 1
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {gate}" in errors


def test_bot_completion_is_not_inferred_from_review_body_phrases() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={COPILOT_GATE})
    pr = context["data"]["repository"]["pullRequest"]
    pr["body"] = pr["body"].replace(
        "2026-07-21T12:10:00Z — COMPLETE",
        "2026-07-21T12:15:00Z — COMPLETE",
    )
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]
    reviews["nodes"].append(
        _review(
            BOT_ACTOR_IDS[COPILOT_GATE],
            "2026-07-21T12:15:00Z",
            body="A finding quotes the literal `review failed`; the formal review completed.",
        )
    )
    reviews["totalCount"] += 1
    assert validate_context(context, files, now=NOW) == []


def test_edited_bot_comment_cannot_replace_a_head_bound_review() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    pr = context["data"]["repository"]["pullRequest"]
    reviews = pr["reviews"]
    reviews["nodes"] = [
        review
        for review in reviews["nodes"]
        if review["author"]["databaseId"] != BOT_ACTOR_IDS[COPILOT_GATE]
    ]
    reviews["totalCount"] = len(reviews["nodes"])
    pr["comments"]["nodes"].append(
        {
            "author": _actor(BOT_ACTOR_IDS[COPILOT_GATE]),
            "body": "Review completed.",
            "createdAt": "2026-07-21T12:01:00Z",
            "updatedAt": GATE_TIMES[COPILOT_GATE],
        }
    )
    pr["comments"]["totalCount"] += 1
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {COPILOT_GATE}" in errors


def test_pr_author_cannot_supply_human_approval() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]["nodes"]
    reviews[-1]["author"] = _actor(AUTHOR_ID, "User")
    assert "no current-head approval from an independent human collaborator" in validate_context(
        context, files, now=NOW
    )


def test_later_dismissal_invalidates_human_approval() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    pr = context["data"]["repository"]["pullRequest"]
    dismissed = _review(
        HUMAN_ID,
        "2026-07-21T12:50:00Z",
        typename="User",
        state="DISMISSED",
        association="COLLABORATOR",
    )
    pr["reviews"]["nodes"].append(dismissed)
    pr["reviews"]["totalCount"] += 1
    pr["latestHumanOpinions"] = {"totalCount": 1, "nodes": [dismissed]}
    errors = validate_context(context, files, now=NOW)
    assert "no current-head approval from an independent human collaborator" in errors


def test_later_human_comment_does_not_erase_approval() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files)
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]
    reviews["nodes"].append(
        _review(
            HUMAN_ID,
            "2026-07-21T12:50:00Z",
            typename="User",
            state="COMMENTED",
            association="COLLABORATOR",
        )
    )
    reviews["totalCount"] += 1
    assert validate_context(context, files, now=NOW) == []


def test_active_change_request_blocks_another_maintainers_approval() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    pr = context["data"]["repository"]["pullRequest"]
    request = _review(
        333,
        "2026-07-21T12:45:00Z",
        typename="User",
        state="CHANGES_REQUESTED",
        association="COLLABORATOR",
    )
    pr["latestHumanOpinions"]["nodes"].append(request)
    pr["latestHumanOpinions"]["totalCount"] += 1
    assert "an active human maintainer change request remains" in validate_context(
        context, files, now=NOW
    )


def test_old_head_change_request_remains_active_after_a_push() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    pr = context["data"]["repository"]["pullRequest"]
    request = _review(
        333,
        "2026-07-21T11:55:00Z",
        typename="User",
        state="CHANGES_REQUESTED",
        association="COLLABORATOR",
        head_sha="b" * 40,
    )
    pr["latestHumanOpinions"]["nodes"].append(request)
    pr["latestHumanOpinions"]["totalCount"] += 1
    assert "an active human maintainer change request remains" in validate_context(
        context, files, now=NOW
    )


def test_later_comment_does_not_clear_an_active_change_request() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    pr = context["data"]["repository"]["pullRequest"]
    request = _review(
        333,
        "2026-07-21T12:35:00Z",
        typename="User",
        state="CHANGES_REQUESTED",
        association="COLLABORATOR",
    )
    pr["latestHumanOpinions"]["nodes"].append(request)
    pr["latestHumanOpinions"]["totalCount"] += 1
    pr["reviews"]["nodes"].append(
        _review(
            333,
            "2026-07-21T12:45:00Z",
            typename="User",
            state="COMMENTED",
            association="COLLABORATOR",
        )
    )
    pr["reviews"]["totalCount"] += 1
    assert "an active human maintainer change request remains" in validate_context(
        context, files, now=NOW
    )


def test_later_approval_clears_the_same_reviewers_change_request() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files)
    pr = context["data"]["repository"]["pullRequest"]
    pr["latestHumanOpinions"] = {
        "totalCount": 1,
        "nodes": [
            _review(
                333,
                "2026-07-21T12:45:00Z",
                typename="User",
                state="APPROVED",
                association="COLLABORATOR",
            )
        ],
    }
    assert validate_context(context, files, now=NOW) == []


def test_dismissed_change_request_does_not_block_another_approval() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files)
    pr = context["data"]["repository"]["pullRequest"]
    pr["latestHumanOpinions"]["nodes"].append(
        _review(
            333,
            "2026-07-21T12:45:00Z",
            typename="User",
            state="DISMISSED",
            association="COLLABORATOR",
        )
    )
    pr["latestHumanOpinions"]["totalCount"] += 1
    assert validate_context(context, files, now=NOW) == []


def test_non_collaborator_change_request_does_not_block() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files)
    pr = context["data"]["repository"]["pullRequest"]
    pr["latestHumanOpinions"]["nodes"].append(
        _review(
            333,
            "2026-07-21T12:45:00Z",
            typename="User",
            state="CHANGES_REQUESTED",
            association="NONE",
        )
    )
    pr["latestHumanOpinions"]["totalCount"] += 1
    assert validate_context(context, files, now=NOW) == []


def test_human_opinion_pagination_fails_closed() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    opinions = context["data"]["repository"]["pullRequest"]["latestHumanOpinions"]
    opinions["totalCount"] = 101
    assert "human-opinion pagination cannot prove active review state" in validate_context(
        context, files, now=NOW
    )


def test_unresolved_thread_fails() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    threads = context["data"]["repository"]["pullRequest"]["reviewThreads"]
    threads.update({"totalCount": 1, "nodes": [{"isResolved": False}]})
    assert "unresolved review threads remain" in validate_context(context, files, now=NOW)


def test_coderabbit_quota_overflow_fails() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    recent = context["data"]["repository"]["recentPullRequests"]
    trigger = _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:24:00Z")
    recent["nodes"][0]["comments"] = {"totalCount": 6, "nodes": [trigger] * 6}
    errors = validate_context(
        context,
        files,
        now=datetime(2026, 7, 22, 13, 0, tzinfo=UTC),
    )
    assert "CodeRabbit rolling-hour trigger quota exceeded: 6 > 5" in errors


def test_prior_head_coderabbit_triggers_still_count_toward_global_quota() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    recent = context["data"]["repository"]["recentPullRequests"]
    old_sha = "b" * 40
    old_pairs = []
    for reservation_minute in (30, 40, 50):
        old_pairs.extend(
            [
                _comment(
                    AUTHOR_ID,
                    f"coderabbit-reservation: {old_sha}",
                    f"2026-07-21T11:{reservation_minute}:00Z",
                ),
                _comment(
                    AUTHOR_ID,
                    "@coderabbitai full review",
                    f"2026-07-21T11:{reservation_minute + 1}:00Z",
                ),
            ]
        )
    for reservation_minute in (0, 10):
        old_pairs.extend(
            [
                _comment(
                    AUTHOR_ID,
                    f"coderabbit-reservation: {old_sha}",
                    f"2026-07-21T12:{reservation_minute:02d}:00Z",
                ),
                _comment(
                    AUTHOR_ID,
                    "@coderabbitai full review",
                    f"2026-07-21T12:{reservation_minute + 1:02d}:00Z",
                ),
            ]
        )
    recent["nodes"].append(
        {
            "updatedAt": "2026-07-21T12:11:00Z",
            "comments": {"totalCount": len(old_pairs), "nodes": old_pairs},
        }
    )
    recent["totalCount"] += 1
    errors = validate_context(context, files, now=NOW)
    assert "CodeRabbit rolling-hour trigger quota exceeded: 6 > 5" in errors


def test_coderabbit_quota_allows_exactly_five_visible_triggers() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    recent = context["data"]["repository"]["recentPullRequests"]
    triggers = [
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T11:26:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T11:45:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:00:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:15:00Z"),
    ]
    recent["nodes"].append(
        {
            "updatedAt": "2026-07-21T12:15:00Z",
            "comments": {"totalCount": len(triggers), "nodes": triggers},
        }
    )
    recent["totalCount"] += 1
    assert validate_context(context, files, now=NOW) == []


def test_coderabbit_recent_pr_pagination_at_inclusive_boundary_fails_closed() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    recent = context["data"]["repository"]["recentPullRequests"]
    recent["nodes"].append(
        {
            "updatedAt": "2026-07-21T11:26:00Z",
            "comments": {"totalCount": 0, "nodes": []},
        }
    )
    recent["totalCount"] = len(recent["nodes"]) + 1
    errors = validate_context(context, files, now=NOW)
    assert "recent PR pagination cannot prove the CodeRabbit hourly quota" in errors


def test_coderabbit_partial_recent_pr_ledger_fails_closed() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    recent = context["data"]["repository"]["recentPullRequests"]
    del recent["nodes"][0]["updatedAt"]
    recent["totalCount"] = len(recent["nodes"]) + 1
    errors = validate_context(context, files, now=NOW)
    assert "recent PR pagination cannot prove the CodeRabbit hourly quota" in errors


def test_coderabbit_partial_comment_ledger_fails_closed() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    recent = context["data"]["repository"]["recentPullRequests"]
    del recent["nodes"][0]["comments"]
    errors = validate_context(context, files, now=NOW)
    assert "comment pagination cannot prove the CodeRabbit hourly quota" in errors


def test_coderabbit_comment_pagination_at_inclusive_boundary_fails_closed() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    recent_comments = context["data"]["repository"]["recentPullRequests"]["nodes"][0]["comments"]
    recent_comments["nodes"].insert(
        0,
        _comment(AUTHOR_ID, "boundary comment", "2026-07-21T11:26:00Z"),
    )
    recent_comments["totalCount"] = len(recent_comments["nodes"]) + 1
    errors = validate_context(context, files, now=NOW)
    assert "comment pagination cannot prove the CodeRabbit hourly quota" in errors


def test_coderabbit_requires_a_marked_completed_review() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]["nodes"]
    coderabbit = next(
        review
        for review in reviews
        if review["author"]["databaseId"] == BOT_ACTOR_IDS[CODERABBIT_GATE]
    )
    coderabbit["body"] = "Review completed."
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {CODERABBIT_GATE}" in errors


def test_empty_coderabbit_inline_review_after_summary_is_ignored() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]
    reviews["nodes"].append(
        _review(BOT_ACTOR_IDS[CODERABBIT_GATE], "2026-07-21T12:35:00Z", body="")
    )
    reviews["totalCount"] += 1
    assert validate_context(context, files, now=NOW) == []


def test_later_nonempty_unmarked_coderabbit_review_invalidates_completion() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]
    reviews["nodes"].append(
        _review(
            BOT_ACTOR_IDS[CODERABBIT_GATE],
            "2026-07-21T12:35:00Z",
            body="Review service reported an unstructured noncompletion.",
        )
    )
    reviews["totalCount"] += 1
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {CODERABBIT_GATE}" in errors


def test_same_second_unmarked_coderabbit_review_invalidates_completion() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]
    reviews["nodes"].append(
        _review(
            BOT_ACTOR_IDS[CODERABBIT_GATE],
            GATE_TIMES[CODERABBIT_GATE],
            body="Review service reported an unstructured noncompletion.",
        )
    )
    reviews["totalCount"] += 1
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {CODERABBIT_GATE}" in errors


def test_coderabbit_completion_marker_allows_quoted_failure_text() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]["nodes"]
    coderabbit = next(
        review
        for review in reviews
        if review["author"]["databaseId"] == BOT_ACTOR_IDS[CODERABBIT_GATE]
    )
    coderabbit["body"] += "\nA finding quotes `review failed`."
    assert validate_context(context, files, now=NOW) == []


def test_old_head_coderabbit_pair_does_not_break_new_head_sequence() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    repository = context["data"]["repository"]
    comments = repository["pullRequest"]["comments"]
    old_pair = [
        _comment(AUTHOR_ID, f"coderabbit-reservation: {'b' * 40}", "2026-07-21T12:05:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:06:00Z"),
    ]
    comments["nodes"][:0] = old_pair
    comments["totalCount"] += len(old_pair)
    recent_comments = repository["recentPullRequests"]["nodes"][0]["comments"]
    recent_comments["totalCount"] += len(old_pair)
    assert validate_context(context, files, now=NOW) == []


@pytest.mark.parametrize("gate", [COPILOT_GATE, CODEX_GATE])
def test_old_head_coderabbit_pair_does_not_block_provider_fallback(gate: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={gate})
    repository = context["data"]["repository"]
    comments = [
        _comment(AUTHOR_ID, f"coderabbit-reservation: {'b' * 40}", "2026-07-21T11:55:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T11:56:00Z"),
    ]
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    assert validate_context(context, files, now=NOW) == []


def test_coderabbit_commands_in_the_same_second_fail_closed() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    repository = context["data"]["repository"]
    comments = [
        _comment(
            AUTHOR_ID,
            f"coderabbit-reservation: {HEAD_SHA}",
            "2026-07-21T12:25:00Z",
            comment_id=1001,
        ),
        _comment(
            AUTHOR_ID,
            "@coderabbitai full review",
            "2026-07-21T12:25:00Z",
            comment_id=1002,
        ),
    ]
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    assert (
        "CodeRabbit needs strictly ordered reservation and trigger comments "
        "after the current head" in validate_context(context, files, now=NOW)
    )


def test_coderabbit_trigger_may_precede_unselected_codex_activity() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    repository = context["data"]["repository"]
    comments = [
        _comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:15:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:16:00Z"),
    ]
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    assert validate_context(context, files, now=NOW) == []


def test_coderabbit_reservation_must_strictly_follow_current_head() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    repository = context["data"]["repository"]
    comments = [
        _comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", COMMITTED_AT),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:01:00Z"),
    ]
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    assert "CodeRabbit reservations and triggers must form one-to-one pairs" in validate_context(
        context, files, now=NOW
    )


def test_one_coderabbit_reservation_cannot_authorize_two_triggers() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    repository = context["data"]["repository"]
    comments = [
        _comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:24:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:25:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:26:00Z"),
    ]
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    assert "CodeRabbit reservations and triggers must form one-to-one pairs" in validate_context(
        context, files, now=NOW
    )


def test_same_maintainer_must_reserve_and_trigger_coderabbit() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    repository = context["data"]["repository"]
    comments = [
        _comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:24:00Z"),
        _comment(333, "@coderabbitai full review", "2026-07-21T12:25:00Z"),
    ]
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    assert "the same maintainer must reserve and trigger CodeRabbit" in validate_context(
        context, files, now=NOW
    )


def test_coderabbit_trigger_after_completion_requires_a_new_completion() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    repository = context["data"]["repository"]
    comments = [
        _comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:24:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:25:00Z"),
        _comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:34:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:35:00Z"),
    ]
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    assert "CodeRabbit was triggered again after its latest completed review" in validate_context(
        context, files, now=NOW
    )


def test_coderabbit_trigger_at_completion_second_requires_a_new_completion() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    repository = context["data"]["repository"]
    comments = [
        _comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:24:00Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:26:00Z"),
        _comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:29:59Z"),
        _comment(AUTHOR_ID, "@coderabbitai full review", GATE_TIMES[CODERABBIT_GATE]),
    ]
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    assert "CodeRabbit was triggered again after its latest completed review" in validate_context(
        context, files, now=NOW
    )


@pytest.mark.parametrize("gate", [COPILOT_GATE, CODEX_GATE, CODERABBIT_GATE])
def test_selected_provider_and_human_in_the_same_second_fail_closed(gate: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={gate})
    pull_request = context["data"]["repository"]["pullRequest"]
    pull_request["body"] = pull_request["body"].replace(
        f"{GATE_TIMES[HUMAN_GATE]} — APPROVED",
        f"{GATE_TIMES[gate]} — APPROVED",
    )
    pull_request["latestHumanOpinions"]["nodes"][0]["submittedAt"] = GATE_TIMES[gate]
    assert "selected automated review must precede human approval" in validate_context(
        context, files, now=NOW
    )


@pytest.mark.parametrize(
    ("comments", "expected_error"),
    [
        (
            [
                _comment(
                    AUTHOR_ID,
                    f"coderabbit-reservation: {HEAD_SHA}\n@coderabbitai full review",
                    "2026-07-21T12:25:00Z",
                )
            ],
            "CodeRabbit needs a current-SHA reservation, trigger, then completed review",
        ),
        (
            [
                _comment(
                    AUTHOR_ID,
                    f"coderabbit-reservation: {HEAD_SHA}",
                    "2026-07-21T12:25:00Z",
                    association="NONE",
                ),
                _comment(
                    AUTHOR_ID,
                    "@coderabbitai full review",
                    "2026-07-21T12:26:00Z",
                    association="NONE",
                ),
            ],
            "CodeRabbit needs a current-SHA reservation, trigger, then completed review",
        ),
        (
            [
                _comment(
                    AUTHOR_ID,
                    f"coderabbit-reservation: {HEAD_SHA}",
                    "2026-07-21T12:25:00Z",
                ),
                _comment(
                    AUTHOR_ID,
                    "Please run @coderabbitai full review",
                    "2026-07-21T12:26:00Z",
                ),
            ],
            "CodeRabbit needs a current-SHA reservation, trigger, then completed review",
        ),
        (
            [
                _comment(
                    AUTHOR_ID,
                    f"coderabbit-reservation: {HEAD_SHA}",
                    "2026-07-21T12:01:00Z",
                    updated_at="2026-07-21T12:25:00Z",
                ),
                _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:26:00Z"),
            ],
            "CodeRabbit reservations and triggers must form one-to-one pairs",
        ),
        (
            [
                {
                    **_comment(
                        AUTHOR_ID,
                        f"coderabbit-reservation: {HEAD_SHA}",
                        "2026-07-21T12:25:00Z",
                    ),
                    "lastEditedAt": "2026-07-21T12:25:00Z",
                },
                _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:26:00Z"),
            ],
            "CodeRabbit reservations and triggers must form one-to-one pairs",
        ),
    ],
)
def test_coderabbit_rejects_combined_untrusted_or_quoted_commands(
    comments: list[dict[str, object]],
    expected_error: str,
) -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    repository = context["data"]["repository"]
    repository["pullRequest"]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    repository["recentPullRequests"]["nodes"][0]["comments"] = {
        "totalCount": len(comments),
        "nodes": comments,
    }
    errors = validate_context(context, files, now=NOW)
    assert expected_error in errors


def test_untrusted_trigger_mentions_do_not_consume_coderabbit_quota() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    recent = context["data"]["repository"]["recentPullRequests"]
    outsider = _comment(
        333,
        "@coderabbitai full review",
        "2026-07-21T12:27:00Z",
        association="NONE",
    )
    recent["nodes"].append(
        {
            "updatedAt": "2026-07-21T12:45:00Z",
            "comments": {"totalCount": 6, "nodes": [outsider] * 6},
        }
    )
    recent["totalCount"] += 1
    assert validate_context(context, files, now=NOW) == []


def test_later_triggers_do_not_retroactively_break_an_earlier_review() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    recent = context["data"]["repository"]["recentPullRequests"]
    later = _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:27:00Z")
    recent["nodes"].append(
        {
            "updatedAt": "2026-07-21T12:45:00Z",
            "comments": {"totalCount": 5, "nodes": [later] * 5},
        }
    )
    recent["totalCount"] += 1
    assert validate_context(context, files, now=NOW) == []


def test_edited_triggers_do_not_count_toward_the_cooperative_quota() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    recent = context["data"]["repository"]["recentPullRequests"]
    edited = _comment(
        AUTHOR_ID,
        "@coderabbitai full review",
        "2026-07-21T12:01:00Z",
        updated_at="2026-07-21T12:24:00Z",
    )
    recent["nodes"].append(
        {
            "updatedAt": "2026-07-21T12:24:00Z",
            "comments": {"totalCount": 6, "nodes": [edited] * 6},
        }
    )
    recent["totalCount"] += 1
    assert validate_context(context, files, now=NOW) == []


def test_same_second_edited_triggers_do_not_count_toward_the_cooperative_quota() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    recent = context["data"]["repository"]["recentPullRequests"]
    edited = _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:01:00Z")
    edited["lastEditedAt"] = edited["createdAt"]
    recent["nodes"].append(
        {
            "updatedAt": "2026-07-21T12:24:00Z",
            "comments": {"totalCount": 6, "nodes": [edited] * 6},
        }
    )
    recent["totalCount"] += 1
    assert validate_context(context, files, now=NOW) == []


def test_invalid_or_out_of_order_timestamps_fail() -> None:
    files = [ChangedFile("frontend/src/App.tsx")]
    context = _context("Standard", files)
    pr = context["data"]["repository"]["pullRequest"]
    pr["body"] = pr["body"].replace(
        "2026-07-21T12:20:00Z — COMPLETE",
        "2026-99-99T25:61:61Z — COMPLETE",
    )
    errors = validate_context(context, files, now=NOW)
    assert any("invalid UTC timestamp" in error for error in errors)


def test_review_pagination_fails_closed_when_current_window_is_truncated() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    context["data"]["repository"]["pullRequest"]["reviews"]["totalCount"] = 101
    assert "review pagination cannot prove current-head review state" in validate_context(
        context, files, now=NOW
    )


def test_review_pagination_fails_closed_when_an_old_review_is_omitted() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]
    reviews["nodes"].insert(
        0,
        _review(
            BOT_ACTOR_IDS[COPILOT_GATE],
            "2026-07-21T11:00:00Z",
            state="COMMENTED",
            head_sha="b" * 40,
        ),
    )
    reviews["totalCount"] = len(reviews["nodes"]) + 1
    assert "review pagination cannot prove current-head review state" in validate_context(
        context, files, now=NOW
    )


def test_empty_truncated_review_connection_fails_closed() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    context["data"]["repository"]["pullRequest"]["reviews"] = {
        "totalCount": 1,
        "nodes": [],
    }
    assert "review pagination cannot prove current-head review state" in validate_context(
        context, files, now=NOW
    )


@pytest.mark.parametrize(
    ("route", "path"),
    [
        ("Low", "docs/guide.md"),
        ("Standard", "frontend/src/hooks/useDialogFocus.ts"),
        ("Load-bearing", ".github/workflows/ci.yml"),
    ],
)
def test_complete_paginated_reviews_validate_beyond_first_hundred(route: str, path: str) -> None:
    files = [ChangedFile(path)]
    context, pages = _truncated_review_fixture(route)
    assert (
        _merge_review_pages(
            context,
            pages,
            expected_head=HEAD_SHA,
            expected_updated_at=PR_UPDATED_AT,
        )
        == []
    )
    assert len(context["data"]["repository"]["pullRequest"]["reviews"]["nodes"]) == 104
    assert validate_context(context, files, now=NOW) == []


def test_later_paginated_noncompletion_supersedes_earlier_bot_review() -> None:
    files = [ChangedFile("docs/guide.md")]
    context, _ = _truncated_review_fixture()
    reviews = deepcopy(context["data"]["repository"]["pullRequest"]["reviews"]["nodes"])
    reviews.append(
        _review(
            BOT_ACTOR_IDS[COPILOT_GATE],
            "2026-07-21T12:50:00Z",
            state="DISMISSED",
        )
    )
    pages = _review_pages(context, reviews)
    assert (
        _merge_review_pages(
            context,
            pages,
            expected_head=HEAD_SHA,
            expected_updated_at=PR_UPDATED_AT,
        )
        == []
    )
    assert len(context["data"]["repository"]["pullRequest"]["reviews"]["nodes"]) == 101
    assert validate_context(context, files, now=NOW) == [
        "no verified current-head GitHub activity for: Copilot PR review"
    ]


@pytest.mark.parametrize(
    ("mode", "expected_error"),
    [
        ("missing-final-page", "page sequence is incomplete"),
        ("changed-total", "total changed between requests"),
        ("duplicate-review", "missing or duplicate review ID"),
        ("duplicate-cursor", "cursor is missing or duplicated"),
        ("empty-terminal-page", "page sequence is incomplete"),
        ("graphql-error", "GraphQL errors or malformed data"),
        ("malformed-data", "GraphQL errors or malformed data"),
        ("changed-head", "snapshot changed between requests"),
        ("changed-update-time", "snapshot changed between requests"),
        ("changed-overlap", "disagrees with the original review snapshot"),
    ],
)
def test_review_page_merge_fails_closed_on_incomplete_or_drifting_ledger(
    mode: str,
    expected_error: str,
) -> None:
    context, pages = _truncated_review_fixture()
    if mode == "missing-final-page":
        pages.pop()
    elif mode == "changed-total":
        pages[1]["data"]["repository"]["pullRequest"]["reviews"]["totalCount"] += 1
    elif mode == "duplicate-review":
        pages[1]["data"]["repository"]["pullRequest"]["reviews"]["nodes"][0]["id"] = pages[0][
            "data"
        ]["repository"]["pullRequest"]["reviews"]["nodes"][0]["id"]
    elif mode == "duplicate-cursor":
        pages[1]["data"]["repository"]["pullRequest"]["reviews"]["pageInfo"]["endCursor"] = pages[
            0
        ]["data"]["repository"]["pullRequest"]["reviews"]["pageInfo"]["endCursor"]
    elif mode == "empty-terminal-page":
        pages[1]["data"]["repository"]["pullRequest"]["reviews"]["pageInfo"] = {
            "hasNextPage": True,
            "endCursor": "review-cursor-extra",
        }
        terminal = deepcopy(pages[1])
        terminal["data"]["repository"]["pullRequest"]["reviews"]["pageInfo"] = {
            "hasNextPage": False,
            "endCursor": None,
        }
        terminal["data"]["repository"]["pullRequest"]["reviews"]["nodes"] = []
        pages.append(terminal)
    elif mode == "graphql-error":
        pages[1]["errors"] = [{"message": "injected failure"}]
    elif mode == "malformed-data":
        pages[1]["data"] = []
    elif mode == "changed-head":
        pages[1]["data"]["repository"]["pullRequest"]["headRefOid"] = "b" * 40
    elif mode == "changed-update-time":
        pages[1]["data"]["repository"]["pullRequest"]["updatedAt"] = "2026-07-21T12:59:00Z"
    elif mode == "changed-overlap":
        pages[1]["data"]["repository"]["pullRequest"]["reviews"]["nodes"][-1]["body"] = "mutated"
    errors = _merge_review_pages(
        context,
        pages,
        expected_head=HEAD_SHA,
        expected_updated_at=PR_UPDATED_AT,
    )
    assert any(expected_error in error for error in errors)


def test_truncated_comment_page_uses_update_order_for_completeness() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    context["data"]["repository"]["pullRequest"]["comments"] = {
        "totalCount": 101,
        "nodes": [
            _comment(
                AUTHOR_ID,
                "old comment edited recently",
                "2026-07-21T11:00:00Z",
                updated_at="2026-07-21T12:50:00Z",
            )
        ],
    }
    assert "comment pagination cannot prove current-head review state" in validate_context(
        context, files, now=NOW
    )


def test_schema_marker_must_be_below_route_heading() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    pr = context["data"]["repository"]["pullRequest"]
    pr["body"] = pr["body"].replace(SCHEMA_MARKER, "") + f"\n```\n{SCHEMA_MARKER}\n```"
    errors = validate_context(context, files, now=NOW)
    assert any("schema marker" in error for error in errors)


def test_fenced_route_heading_and_marker_cannot_authorize_visible_rows() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Standard", files, automated_gates={CODEX_GATE})
    pull_request = context["data"]["repository"]["pullRequest"]
    pull_request["body"] = (
        pull_request["body"].replace(SCHEMA_MARKER, "")
        + f"\n```markdown\n## Review route\n{SCHEMA_MARKER}\n```"
    )
    errors = validate_context(context, files, now=NOW)
    assert any("schema marker" in error for error in errors)


def test_schema_marker_hidden_in_an_html_comment_is_rejected() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Standard", files, automated_gates={CODEX_GATE})
    pull_request = context["data"]["repository"]["pullRequest"]
    pull_request["body"] = (
        pull_request["body"].replace(SCHEMA_MARKER, "")
        + f"\n<!--\n## Review route\n{SCHEMA_MARKER}\n-->"
    )
    errors = validate_context(context, files, now=NOW)
    assert any("schema marker" in error for error in errors)


def test_unclosed_fence_hides_route_rows_and_is_rejected() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Standard", files, automated_gates={CODEX_GATE})
    pull_request = context["data"]["repository"]["pullRequest"]
    pull_request["body"] = pull_request["body"].replace(
        f"{SCHEMA_MARKER}\n",
        f"{SCHEMA_MARKER}\n```markdown\n",
    )
    errors = validate_context(context, files, now=NOW)
    assert "expected each canonical route checkbox exactly once" in errors
    assert "expected each automated reviewer checkbox exactly once" in errors
    assert any(error.startswith("missing review evidence rows:") for error in errors)


@pytest.mark.parametrize("tag", ["pre", "code"])
def test_route_structure_rendered_in_raw_html_code_is_rejected(tag: str) -> None:
    files = [ChangedFile("README.md")]
    context = _context("Standard", files, automated_gates={CODEX_GATE})
    rendered = f"<{tag}>{_rendered_route_html('Standard')}</{tag}>"
    assert RENDERED_ROUTE_ERROR in _validate_rendered(context, files, rendered)


def test_unclosed_raw_html_code_block_hiding_rows_is_rejected() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Standard", files, automated_gates={CODEX_GATE})
    pull_request = context["data"]["repository"]["pullRequest"]
    pull_request["body"] = pull_request["body"].replace(
        f"{SCHEMA_MARKER}\n",
        f"{SCHEMA_MARKER}\n<pre\n",
    )
    errors = validate_context(context, files, now=NOW)
    assert "raw HTML is not allowed in the review-route section" in errors


def test_render_probe_binds_nonce_directly_to_the_unique_source_marker() -> None:
    body = _body("Low")
    rendered_source = render_probe_body(body, RENDER_NONCE)
    assert rendered_source.count(RENDER_NONCE) == 1
    assert f"{SCHEMA_MARKER}\n\n{RENDER_NONCE}\n" in rendered_source


def test_render_probe_preserves_and_binds_crlf_bodies() -> None:
    body = _body("Low").replace("\n", "\r\n")
    rendered_source = render_probe_body(body, RENDER_NONCE)
    assert rendered_source.count(RENDER_NONCE) == 1
    assert f"{SCHEMA_MARKER}\r\n\n{RENDER_NONCE}\n" in rendered_source


@pytest.mark.parametrize("sample_position", ["before", "after"])
def test_render_probe_ignores_fenced_route_examples(sample_position: str) -> None:
    example = f"```markdown\n## Review route\n{SCHEMA_MARKER}\n```"
    parts = [example, _body("Low")]
    if sample_position == "after":
        parts.reverse()
    rendered_source = render_probe_body("\n\n".join(parts), RENDER_NONCE)
    assert rendered_source.count(RENDER_NONCE) == 1
    assert f"{SCHEMA_MARKER}\n\n{RENDER_NONCE}\n" in rendered_source


@pytest.mark.parametrize(
    ("body", "nonce"),
    [
        ("No route", RENDER_NONCE),
        (_body("Low") + "\n" + _body("Low"), RENDER_NONCE),
        (_body("Low"), "predictable"),
    ],
)
def test_render_probe_rejects_unbound_or_ambiguous_inputs(body: str, nonce: str) -> None:
    with pytest.raises(ValueError):
        render_probe_body(body, nonce)


def test_top_level_github_rendered_route_is_accepted() -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files)
    assert _validate_rendered(context, files, _rendered_route_html("Low")) == []


@pytest.mark.parametrize("opening", ["<p />", "<h1 />", "<svg />", "<math />"])
def test_self_closing_visible_html_before_rendered_route_is_accepted(opening: str) -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files, body=f"{opening}\n\n" + _body("Low"))
    assert _validate_rendered(context, files, opening + _rendered_route_html("Low")) == []


@pytest.mark.parametrize(
    "opening",
    [
        "<h1 hidden><table>",
        "<h1 hidden><span>",
        "<p><i hidden>",
        "<p hidden><button>",
        "<p hidden><object>",
        "<p hidden><select>",
        "<p hidden><table>",
    ],
)
def test_implicit_close_cannot_escape_active_html5_container(opening: str) -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files, body=f"{opening}\n\n" + _body("Low"))
    assert RENDERED_ROUTE_ERROR in _validate_rendered(
        context,
        files,
        opening + _rendered_route_html("Low"),
    )


@pytest.mark.parametrize("opening", ["<h1><span></span>", "<p><span>"])
def test_implicit_close_accepts_html5_closable_content(opening: str) -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files, body=f"{opening}\n\n" + _body("Low"))
    assert _validate_rendered(context, files, opening + _rendered_route_html("Low")) == []


@pytest.mark.parametrize(
    "rendered_prefix",
    [
        "<dl><dt>one<dd>two</dl>",
        "<ol><li>one</ol>",
        "<table><tr><td>one</table>",
        "<ul><li>one<li>two</ul>",
        "<svg><g><font hidden /></g></svg>",
        "<svg><g><path /></g></svg>",
        "<svg><foreignObject><svg><path /></svg></foreignObject></svg>",
        "<svg><path /></svg>",
        "<math><mtext><mglyph /></mtext></math>",
        "<math><mi /></math>",
    ],
)
def test_valid_optional_and_foreign_closures_before_route_are_accepted(
    rendered_prefix: str,
) -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files, body=f"{rendered_prefix}\n\n" + _body("Low"))
    assert _validate_rendered(context, files, rendered_prefix + _rendered_route_html("Low")) == []


@pytest.mark.parametrize(
    "rendered_prefix",
    [
        "<li hidden></ul>",
        "<ol><li hidden></ul>",
        "<dt hidden></dl>",
        "<menu><li hidden></ol>",
        "<option hidden></select>",
        "<ruby><rt hidden></rtc>",
        "<select><option hidden></optgroup>",
    ],
)
def test_unmatched_optional_parent_close_cannot_escape_hidden_child(
    rendered_prefix: str,
) -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files, body=f"{rendered_prefix}\n\n" + _body("Low"))
    assert RENDERED_ROUTE_ERROR in _validate_rendered(
        context,
        files,
        rendered_prefix + _rendered_route_html("Low"),
    )


@pytest.mark.parametrize(
    "rendered_prefix",
    [
        "<svg><foreignObject><div hidden /></foreignObject></svg>",
        "<svg><foreignObject><p hidden /></foreignObject></svg>",
        ('<math><annotation-xml encoding="text/html"><div hidden /></annotation-xml></math>'),
        (
            "<details><summary>x</summary>"
            "<svg><foreignObject><div /></foreignObject></svg></details>"
        ),
        "<details><summary>x</summary><svg><desc><div /></desc></svg></details>",
        "<details><summary>x</summary><math><mtext><div /></mtext></math></details>",
        (
            "<details><summary>x</summary>"
            '<math><annotation-xml encoding="text/html"><div />'
            "</annotation-xml></math></details>"
        ),
        (
            "<details><summary>x</summary>"
            '<math><annotation-xml encoding="application/xhtml+xml"><div />'
            "</annotation-xml></math></details>"
        ),
        (
            "<details><summary>x</summary><math>"
            '<annotation-xml encoding="text/html" encoding="application/xml"><div />'
            "</annotation-xml></math></details>"
        ),
        "<svg><g><div hidden /></g></svg>",
        "<math><mrow><div hidden /></mrow></math>",
        '<svg><g><font color="red" hidden /></g></svg>',
    ],
)
def test_foreign_html_transition_cannot_self_close_hidden_html(
    rendered_prefix: str,
) -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files, body=f"{rendered_prefix}\n\n" + _body("Low"))
    assert RENDERED_ROUTE_ERROR in _validate_rendered(
        context,
        files,
        rendered_prefix + _rendered_route_html("Low"),
    )


@pytest.mark.parametrize(
    ("opening", "closing"),
    [
        ("<b><i hidden></b>", "</i>"),
        ("<form hidden><span></form>", "</span>"),
        ("<h1><i hidden></h1>", "</i>"),
        ("<h1 hidden><table></h1>", "</table>"),
        ("<h1 hidden><script></h1>", "</script>"),
        ("<p><i hidden></p>", "</i>"),
        ("<p hidden><object></p>", "</object>"),
        ("<p hidden><select></p>", "</select>"),
        ("<span hidden><div></span>", "</div>"),
    ],
)
def test_end_tag_cannot_cross_html5_scope_or_special_boundary(
    opening: str,
    closing: str,
) -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files, body=f"{opening}\n\n" + _body("Low") + closing)
    rendered = opening + _rendered_route_html("Low") + closing
    assert RENDERED_ROUTE_ERROR in _validate_rendered(context, files, rendered)


def test_combined_visible_task_list_with_root_evidence_table_is_accepted() -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files)
    rendered = _rendered_route_html("Low").replace("</ul><ul>", "", 1)
    rendered = rendered.replace("<li><input", "<li><p><input").replace("</li>", "</p></li>")
    assert _validate_rendered(context, files, rendered) == []


def test_rendered_context_must_supply_body_and_nonce_together() -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files)
    errors = validate_context(
        context,
        files,
        now=NOW,
        rendered_body=_rendered_route_html("Low"),
    )
    assert "rendered review-route context is incomplete" in errors


@pytest.mark.parametrize(
    "rendered",
    [
        f"<details><summary>Hidden</summary>{_rendered_route_html('Low')}</details>",
        f"<details />{_rendered_route_html('Low')}",
        f"<div hidden>{_rendered_route_html('Low')}</div>",
        (
            "<p>ordinary Review route "
            + RENDER_NONCE
            + " [x] Low [ ] Standard [ ] Load-bearing "
            + "Copilot Codex CodeRabbit "
            + " ".join(GATES)
            + "</p>"
        ),
    ],
)
def test_rendered_route_cannot_be_hidden_or_swallowed(rendered: str) -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files)
    assert RENDERED_ROUTE_ERROR in _validate_rendered(context, files, rendered)


@pytest.mark.parametrize(
    "source_prefix",
    [
        "<details>\n\nText \\</details>\n\n",
        "<details>\n\n[ref]: </details>\n\n",
        '<details>\n\n[link](https://example.test "</details>")\n\n',
        "<details>\n\n[link](</details>)\n\n",
        "<details>\n\n![alt </details>](image.png)\n\n",
        "<details>\n\nThe token `</details>` is code.\n\n",
    ],
)
def test_markdown_context_closers_cannot_fake_rendered_containment(
    source_prefix: str,
) -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files, body=source_prefix + _body("Low") + "\n</details>")
    probe = render_probe_body(context["data"]["repository"]["pullRequest"]["body"], RENDER_NONCE)
    assert RENDER_NONCE in probe
    rendered = f"<details><summary>Details</summary>{_rendered_route_html('Low')}</details>"
    assert RENDERED_ROUTE_ERROR in _validate_rendered(context, files, rendered)


@pytest.mark.parametrize(
    "source_prefix",
    [
        "<details><summary>Earlier</summary></details>\n\n",
        "<details>\n\nUse `<div>` in prose.\n\n</details>\n\n",
        "<details>\n\nText \\<details>\n\n</details>\n\n",
        "<details>\n\nVisible text. </details> <https://example.test/path>\n\n",
        "<h1 hidden>\n\n",
        "<image src=x>\n\n",
        "<svg>\n\n",
        "<math>\n\n",
        "ordinary paragraph\n<template>\n\n",
        "unmatched ` paragraph\n\nreal close </details>\n\nstill ` literal\n\n",
    ],
)
def test_visible_github_render_is_not_rejected_by_markdown_source_syntax(
    source_prefix: str,
) -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files, body=source_prefix + _body("Low"))
    rendered_prefix = "<p>Earlier rendered content</p>"
    assert (
        _validate_rendered(
            context,
            files,
            rendered_prefix + _rendered_route_html("Low"),
        )
        == []
    )


def test_nonce_must_immediately_follow_the_bound_top_level_heading() -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files)
    rendered = _rendered_route_html("Low").replace(
        f"<h2>Review route</h2><p>{RENDER_NONCE}</p>",
        f"<h2>Review route</h2><p>decoy</p><p>{RENDER_NONCE}</p>",
    )
    assert RENDERED_ROUTE_ERROR in _validate_rendered(context, files, rendered)


@pytest.mark.parametrize("extra", ["<p>decoy</p>", "<h3>decoy</h3>", "<hr>", "text"])
def test_rendered_route_rejects_extra_root_content(extra: str) -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files)
    rendered = _rendered_route_html("Low").replace(
        f"<p>{RENDER_NONCE}</p>",
        f"<p>{RENDER_NONCE}</p>{extra}",
    )
    assert RENDERED_ROUTE_ERROR in _validate_rendered(context, files, rendered)


@pytest.mark.parametrize("group", ["route", "provider"])
def test_rendered_checklists_reject_visible_noncanonical_items(group: str) -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files)
    rendered = _rendered_route_html("Low")
    fake = "<li><strong>FAKE: all reviews waived</strong></li>"
    if group == "route":
        rendered = rendered.replace("<ul>", f"<ul>{fake}", 1)
    else:
        rendered = rendered.replace("</ul><ul>", f"</ul><ul>{fake}", 1)
    assert RENDERED_ROUTE_ERROR in _validate_rendered(context, files, rendered)


@pytest.mark.parametrize("target", ["route", "provider", "table"])
def test_every_rendered_route_control_must_remain_at_the_document_root(target: str) -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files)
    rendered = _rendered_route_html("Low")
    if target == "route":
        rendered = rendered.replace("<ul>", "<details><ul>", 1).replace(
            "</ul>", "</ul></details>", 1
        )
    elif target == "provider":
        first_end = rendered.index("</ul>") + len("</ul>")
        rendered = rendered[:first_end] + rendered[first_end:].replace(
            "<ul>", "<details><ul>", 1
        ).replace("</ul>", "</ul></details>", 1)
    else:
        rendered = rendered.replace(
            "<markdown-accessiblity-table>",
            "<details><markdown-accessiblity-table>",
        ).replace(
            "</markdown-accessiblity-table>",
            "</markdown-accessiblity-table></details>",
        )
    assert RENDERED_ROUTE_ERROR in _validate_rendered(context, files, rendered)


def test_rendered_evidence_table_cells_must_match_source_rows() -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files)
    rendered = _rendered_route_html("Low").replace(
        f"<td>{HEAD_SHA}</td>",
        f"<td>{'c' * 40}</td>",
        1,
    )
    assert RENDERED_ROUTE_ERROR in _validate_rendered(context, files, rendered)


def test_rendered_evidence_accepts_github_abbreviated_commit_link() -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files)
    commit_link = (
        f'<td><a class="commit-link" href="https://github.com/bioedca/Yeliztli/'
        f'commit/{HEAD_SHA}"><tt>{HEAD_SHA[:7]}</tt></a></td>'
    )
    rendered = _rendered_route_html("Low").replace(
        f"<td>{HEAD_SHA}</td>",
        commit_link,
        1,
    )
    assert _validate_rendered(context, files, rendered) == []


def test_rendered_evidence_rejects_abbreviated_link_to_another_commit() -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files)
    other = "c" * 40
    commit_link = (
        f'<td><a class="commit-link" href="https://github.com/bioedca/Yeliztli/'
        f'commit/{other}"><tt>{HEAD_SHA[:7]}</tt></a></td>'
    )
    rendered = _rendered_route_html("Low").replace(
        f"<td>{HEAD_SHA}</td>",
        commit_link,
        1,
    )
    assert RENDERED_ROUTE_ERROR in _validate_rendered(context, files, rendered)


def test_plain_evidence_rows_cannot_be_substituted_with_a_decoy_table() -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files)
    rendered = _rendered_route_html("Low").replace(
        "<markdown-accessiblity-table>",
        "<p>canonical pipe rows rendered as prose</p><markdown-accessiblity-table>",
    )
    assert RENDERED_ROUTE_ERROR in _validate_rendered(context, files, rendered)


@pytest.mark.parametrize(
    ("route", "automated_gates", "rendered_route", "rendered_gates"),
    [
        ("Low", {COPILOT_GATE}, "Standard", {COPILOT_GATE}),
        ("Standard", {CODEX_GATE}, "Standard", {COPILOT_GATE}),
    ],
)
def test_rendered_selections_must_match_source(
    route: str,
    automated_gates: set[str],
    rendered_route: str,
    rendered_gates: set[str],
) -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context(route, files, automated_gates=automated_gates)
    rendered = _rendered_route_html(
        rendered_route,
        automated_gates=rendered_gates,
    )
    assert RENDERED_ROUTE_ERROR in _validate_rendered(context, files, rendered)


def test_closed_html_before_the_rendered_route_remains_valid() -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context(
        "Low",
        files,
        body="<details><summary>Earlier</summary></details>\n\n" + _body("Low"),
    )
    rendered = "<details><summary>Earlier</summary><p>Text</p></details>" + _rendered_route_html(
        "Low"
    )
    assert _validate_rendered(context, files, rendered) == []


def test_review_route_rows_rendered_as_indented_code_do_not_count() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, automated_gates={CODEX_GATE})
    pull_request = context["data"]["repository"]["pullRequest"]
    pull_request["body"] = "\n".join(
        f"    {line}" if line.startswith(("- [", "|")) else line
        for line in pull_request["body"].splitlines()
    )
    errors = validate_context(context, files, now=NOW)
    assert "expected each canonical route checkbox exactly once" in errors
    assert "expected each automated reviewer checkbox exactly once" in errors
    assert any(error.startswith("missing review evidence rows:") for error in errors)


def test_new_head_invalidates_live_bot_reviews() -> None:
    files = [ChangedFile("frontend/src/App.tsx")]
    context = deepcopy(_context("Standard", files))
    context["data"]["repository"]["pullRequest"]["headRefOid"] = "c" * 40
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {CODEX_GATE}" in errors


def test_duplicate_open_pull_request_head_fails_closed() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    context["data"]["repository"]["openPullRequests"]["nodes"].append(
        {"headRefOid": HEAD_SHA, "number": 43}
    )
    context["data"]["repository"]["openPullRequests"]["totalCount"] = 2
    errors = validate_context(context, files, now=NOW)
    assert "current head SHA must belong to exactly one open pull request" in errors


def test_other_open_pull_request_head_does_not_collide() -> None:
    files = [ChangedFile("docs/guide.md")]
    context = _context("Low", files)
    context["data"]["repository"]["openPullRequests"]["nodes"].append(
        {"headRefOid": "b" * 40, "number": 43}
    )
    context["data"]["repository"]["openPullRequests"]["totalCount"] = 2
    assert validate_context(context, files, now=NOW) == []


def test_open_pull_request_head_must_include_current_pr() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    context["data"]["repository"]["openPullRequests"]["nodes"] = [
        {"headRefOid": "b" * 40, "number": 43}
    ]
    errors = validate_context(context, files, now=NOW)
    assert "current head SHA must belong to exactly one open pull request" in errors


def test_open_pull_request_pagination_fails_closed() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    context["data"]["repository"]["openPullRequests"]["totalCount"] = 101
    errors = validate_context(context, files, now=NOW)
    assert "open pull request pagination cannot prove head uniqueness" in errors


def test_closed_pull_request_fails_closed() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    context["data"]["repository"]["pullRequest"]["state"] = "CLOSED"
    context["data"]["repository"]["openPullRequests"] = {"totalCount": 0, "nodes": []}
    errors = validate_context(context, files, now=NOW)
    assert "pull request is not open" in errors
    assert "current head SHA must belong to exactly one open pull request" in errors


def test_force_push_epoch_invalidates_reintroduced_sha_evidence() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    context["data"]["repository"]["pullRequest"]["timelineItems"] = {
        "nodes": [{"createdAt": "2026-07-21T12:50:00Z"}]
    }
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {COPILOT_GATE}" in errors


def test_bot_review_equal_to_force_push_epoch_fails_closed() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    context["data"]["repository"]["pullRequest"]["timelineItems"] = {
        "nodes": [{"createdAt": GATE_TIMES[COPILOT_GATE]}]
    }
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {COPILOT_GATE}" in errors


def test_human_approval_equal_to_force_push_epoch_fails_closed() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    context["data"]["repository"]["pullRequest"]["timelineItems"] = {
        "nodes": [{"createdAt": GATE_TIMES[HUMAN_GATE]}]
    }
    errors = validate_context(context, files, now=NOW)
    assert "no current-head approval from an independent human collaborator" in errors


def test_body_evidence_equal_to_force_push_epoch_fails_closed() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Low", files)
    context["data"]["repository"]["pullRequest"]["timelineItems"] = {
        "nodes": [{"createdAt": GATE_TIMES[COPILOT_GATE]}]
    }
    errors = validate_context(context, files, now=NOW)
    assert (
        f"required gate timestamp is outside the current-head review window: {COPILOT_GATE}"
        in errors
    )


def test_workflow_uses_trusted_base_and_explicit_head_status() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    assert "pull_request_target:" not in workflow
    assert "pull_request_review:" not in workflow
    assert "pull_request_review_comment:" not in workflow
    assert "issue_comment:" in workflow
    assert "workflow_run:" in workflow
    assert 'workflows: ["Review Route Invalidation Signal"]' in workflow
    assert "merge_group:" not in workflow
    assert "Merge queue is intentionally unsupported" in workflow
    assert "types: [created, edited, deleted]" in workflow
    assert "github.event.comment.author_association" in workflow
    assert "github.event.comment.user.type == 'Bot'" in workflow
    assert "github.event.comment.user.id == 199175422" in workflow
    assert "contains(github.event.comment.body, '@codex')" not in workflow
    assert "startsWith(github.event.comment.body, 'codex-reservation:')" not in workflow
    assert "environment:\n      name: review-route-publisher" in workflow
    assert "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1" in workflow
    assert "REVIEW_ROUTE_APP_CLIENT_ID" in workflow
    assert "REVIEW_ROUTE_APP_PRIVATE_KEY" in workflow
    assert '-f headOid="$HEAD_SHA"' in workflow
    assert "permission-statuses: write" in workflow
    assert "permission-contents: read" in workflow
    assert workflow.count("permission-metadata: read") == 2
    review_fallback_token = workflow.split("id: review-fallback", maxsplit=1)[1].split(
        "\n\n", maxsplit=1
    )[0]
    publisher_token = workflow.split("id: publisher", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]
    assert "permission-metadata: read" in review_fallback_token
    assert "permission-metadata: read" in publisher_token
    assert "statuses: write" not in workflow.split("jobs:", maxsplit=1)[0]
    assert workflow.count("GH_TOKEN: ${{ github.token }}") == 2
    privileged_jobs = workflow.split("  invalidate-review-state:", maxsplit=1)[1]
    assert "GH_TOKEN: ${{ github.token }}" not in privileged_jobs
    assert workflow.count("steps.publisher.outputs.token") == 3
    assert "github.event.comment.body == '/validate-route'" in workflow
    assert "FINALIZE_COMMENT_NODE_ID: ${{ github.event.comment.node_id }}" in workflow
    assert "FINALIZE_COMMENT_CREATED_AT: ${{ github.event.comment.created_at }}" in workflow
    assert "FINALIZE_COMMENT_ACTOR_ID: ${{ github.event.comment.user.id }}" in workflow
    assert "FINALIZE_COMMENT_ACTOR_LOGIN: ${{ github.event.comment.user.login }}" in workflow
    assert "collaborators/$FINALIZE_COMMENT_ACTOR_LOGIN/permission" in workflow
    assert "finalizer lacks bound repository write permission" in workflow
    assert "WORKFLOW_SHA: ${{ github.workflow_sha }}" in workflow
    assert '[ "$FINALIZE_ROUTE" = "true" ]' in workflow
    assert '"$GITHUB_EVENT_PATH" "$RUNNER_TEMP/pr.json"' in workflow
    assert "Route is complete; a maintainer must comment /validate-route." in workflow
    assert "concurrency:" in workflow.split("jobs:", maxsplit=1)[1]
    assert "ref: ${{ env.TRUSTED_SHA }}" in workflow
    assert "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803" in workflow
    assert workflow.count('.base.ref == "main"') == 10
    assert workflow.count(".base.repo.full_name == $repo") == 10
    assert workflow.count("git/ref/heads/main") == 3
    assert "'.object.sha == $trusted'" in workflow
    assert ".base.sha" not in workflow
    assert "-f state=pending" in workflow
    assert "EXPECTED_HEAD_SHA: ${{ needs.resolve_review_state.outputs.head_sha }}" in workflow
    assert "SIGNAL_HEAD_SHA: ${{ needs.resolve_review_state.outputs.signal_head }}" in workflow
    assert "RESOLVED_HEAD_SHA: ${{ needs.resolve_route_event.outputs.head_sha }}" in workflow
    assert "EVENT_HEAD_SHA: ${{ needs.resolve_route_event.outputs.event_head }}" in workflow
    validate_job = workflow.split("\n  validate:", maxsplit=1)[1]
    assert 'post_pending_for_current_pr "$head_sha"' in validate_job
    assert 'post_pending_if_unowned "$head_sha"' in validate_job
    assert 'post_pending_if_unowned "$EVENT_HEAD_SHA"' in validate_job
    assert '[ "$EVENT_HEAD_SHA" != "$head_sha" ]' in validate_job
    assert "^[0-9a-fA-F]{40}$" in workflow
    assert workflow.count("for attempt in 1 2 3") == 13
    assert '--expected-head "$HEAD_SHA"' in workflow
    assert '--expected-draft "$IS_DRAFT"' in workflow
    assert '--expected-pr-updated-at "$PR_UPDATED_AT"' in workflow
    assert "gh api graphql --paginate --slurp" in workflow
    assert "-F query=@scripts/review_route_reviews.graphql" in workflow
    assert '--review-pages "$RUNNER_TEMP/review-route-review-pages.json"' in workflow
    assert "pull request updated_at has an invalid shape" in workflow
    assert (
        '[[ "$pr_updated_at" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]'
    ) in workflow
    assert '--expected-pr-snapshot "$RUNNER_TEMP/pr.json"' in workflow
    assert '--rendered-body "$RUNNER_TEMP/review-route-rendered.html"' in workflow
    assert '--render-nonce "$render_nonce"' in workflow
    assert "from scripts.validate_review_route import needs_coderabbit_ledger" in workflow
    assert "from scripts.validate_review_route import render_probe_body" in workflow
    assert "secrets.token_hex(24)" in workflow
    assert "gh api markdown" in workflow
    assert '--input "$RUNNER_TEMP/review-route-render-request.json"' in workflow
    assert '-F includeCodeRabbitLedger="$include_coderabbit_ledger"' in workflow
    assert '--finalize-comment-node-id "$FINALIZE_COMMENT_NODE_ID"' in workflow
    assert '--finalize-comment-created-at "$FINALIZE_COMMENT_CREATED_AT"' in workflow
    assert '--finalize-comment-actor-id "$FINALIZE_COMMENT_ACTOR_ID"' in workflow
    assert '--finalize-comment-actor-permission "$FINALIZE_COMMENT_ACTOR_PERMISSION"' in workflow
    assert 'if .draft == true then "true" elif .draft == false then "false"' in workflow
    assert '-F node="$FINALIZE_COMMENT_NODE_ID"' in workflow
    assert "collaborators/$finalizer_login/permission" in workflow
    assert ".data.node.lastEditedAt == null" in workflow
    assert ".[0].body == .[1].body" in workflow
    assert ".[0].updated_at == $updated" in workflow
    assert ".[1].updated_at == $updated" in workflow
    assert "always() && !cancelled()" in workflow
    assert "pulls?state=open&base=main&per_page=100" in workflow
    assert "read -r foreign_owner_count claimed_owner_count match_count" in workflow
    assert "head belongs exclusively to a replacement PR; publication skipped" in workflow
    assert '[ "$head_unique" = "true" ]' in workflow
    assert workflow.index("classify_head_ownership()") < workflow.index("state=success")
    assert workflow.index('.[1].state == "open"') < workflow.index("state=success")
    assert '[ "$WORKFLOW_SHA" = "$TRUSTED_SHA" ]' in validate_job
    assert validate_job.index('[ "$WORKFLOW_SHA" = "$TRUSTED_SHA" ]') < validate_job.index(
        "state=success"
    )
    assert "Draft route is classified; current-head reviews remain pending." in workflow
    assert "Draft review route schema or classification is invalid." in workflow
    assert (
        'if [ "$IS_DRAFT" = "true" ] && \\\n'
        '                [ "$VALIDATION_OUTCOME" = "success" ]; then'
    ) in workflow
    assert 'elif [ "$IS_DRAFT" = "true" ]; then' in workflow
    assert 'context="Review Route"' in workflow
    assert "statuses/$HEAD_SHA" in workflow
    assert validate_job.index('publication skipped"') < validate_job.index('statuses/$HEAD_SHA"')
    assert validate_job.rindex("classify_head_ownership") < validate_job.index(
        'statuses/$HEAD_SHA"'
    )
    assert validate_job.rindex("pr-before-post-$attempt.json") < validate_job.index(
        'statuses/$HEAD_SHA"'
    )
    assert validate_job.rindex('verify_finalizer "before-post-$attempt"') < validate_job.index(
        'statuses/$HEAD_SHA"'
    )
    assert (
        "Finalizer authorization, PR state, or trusted main changed before publication."
        in validate_job
    )


def test_v3_workflow_invalidates_to_pending_and_audits_one_published_success() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    helper = (root / "scripts/validate_review_route_snapshot.sh").read_text(encoding="utf-8")
    validate_job = workflow.split("\n  validate:", maxsplit=1)[1]

    assert 'contains("<!-- review-route-schema:v3 -->")' in validate_job
    assert "INVALIDATION_ONLY=%s" in validate_job
    assert "V3 invalidation is complete after publishing pending." in validate_job
    assert (
        "- uses: actions/checkout@" in validate_job
        and "if: ${{ env.INVALIDATION_ONLY != 'true' }}" in validate_job
    )
    assert "bash scripts/validate_review_route_snapshot.sh before-success" in validate_job
    assert "bash scripts/validate_review_route_snapshot.sh after-success" in validate_job
    assert "HEAD_REPOSITORY" in validate_job
    assert "HEAD_BRANCH" in validate_job
    assert validate_job.index("before-success") < validate_job.index('statuses/$HEAD_SHA"')
    assert validate_job.index('statuses/$HEAD_SHA"') < validate_job.index("after-success")
    assert "Post-success audit changed; review route is pending." in validate_job
    assert "post-success audit failed closed" in validate_job
    assert "review_route_ledger.py" not in workflow

    assert "pulls/$PR_NUMBER" in helper
    assert "git/ref/heads/main" in helper
    assert ".head.repo.full_name == $head_repo" in helper
    assert ".head.ref == $head_branch" in helper
    assert "pulls?state=open" not in helper
    assert "-F query=@scripts/review_route_context.graphql" in helper
    assert "pulls/$PR_NUMBER/files?per_page=100" in helper
    assert "collaborators/$finalizer_login/permission" in helper
    assert "python scripts/validate_review_route.py" in helper
    assert ".issue.body == .[1].body" in helper
    assert ".object.sha == $trusted" in helper
    assert "actions/runs?" not in helper
    assert "display_title" not in helper


@pytest.mark.parametrize(
    ("truncated", "fetch_fails", "expected_returncode", "expected_fetch"),
    [
        (True, False, 0, True),
        (True, True, 1, True),
        (False, True, 0, False),
    ],
)
def test_review_pagination_workflow_fetches_only_a_truncated_ledger(
    tmp_path: Path,
    truncated: bool,
    fetch_fails: bool,
    expected_returncode: int,
    expected_fetch: bool,
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    shell = _workflow_step_script(workflow, "Validate live route state")
    start = shell.index("review_page_args=()")
    end = shell.index(
        "gh api --paginate --slurp \\\n"
        '  "repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER/files?per_page=100"'
    )
    pagination_shell = (
        shell[start:end]
        + '\nif [ "${#review_page_args[@]}" -gt 0 ]; then\n'
        + '  printf \'%s\\n\' "${review_page_args[@]}" > "$ARGS_LOG"\n'
        + "fi\n"
    )
    context_path = tmp_path / "review-route-context.json"
    context_path.write_text(
        json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviews": {
                                "totalCount": 104 if truncated else 100,
                                "nodes": [{} for _ in range(100)],
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    gh_log = tmp_path / "gh.log"
    args_log = tmp_path / "args.log"
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$GH_LOG"
if [ "$FETCH_FAILS" = "true" ]; then exit 1; fi
printf '[{"data":{"repository":{"pullRequest":{}}}}]\n'
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    completed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", pagination_shell],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "ARGS_LOG": str(args_log),
            "FETCH_FAILS": str(fetch_fails).lower(),
            "GH_LOG": str(gh_log),
            "OWNER": "bioedca",
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PR_NUMBER": "2183",
            "REPO": "Yeliztli",
            "RUNNER_TEMP": str(tmp_path),
        },
        text=True,
    )
    assert completed.returncode == expected_returncode, completed.stderr
    assert gh_log.exists() is expected_fetch
    if expected_returncode == 0 and expected_fetch:
        assert args_log.read_text(encoding="utf-8").splitlines() == [
            "--review-pages",
            str(tmp_path / "review-route-review-pages.json"),
        ]
        assert "--paginate --slurp" in gh_log.read_text(encoding="utf-8")
    else:
        assert not args_log.exists()


def test_review_state_signal_is_credential_free_and_only_drives_pending() -> None:
    root = Path(__file__).resolve().parents[2]
    signal = (root / ".github/workflows/review-route-invalidation.yml").read_text(encoding="utf-8")
    publisher = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    assert "pull_request_target:" in signal
    lifecycle_types = (
        "types: [opened, edited, synchronize, reopened, closed, ready_for_review, "
        "converted_to_draft, review_requested, review_request_removed]"
    )
    assert lifecycle_types in signal
    assert (
        "run-name: review-route-pr-${{ github.event.pull_request.number }}-head-"
        "${{ github.event.pull_request.head.sha }}-trigger-${{ github.actor_id }}" in signal
    )
    assert "github.event.review.user.id" not in signal
    assert "github.event.comment.user.id" not in signal
    assert "pull_request_review:" in signal
    assert "types: [submitted, edited, dismissed]" in signal
    assert "pull_request_review_comment:" in signal
    assert "types: [created, edited, deleted]" in signal
    assert "pull_request_review_thread:" not in signal
    assert "permissions: {}" in signal
    assert "ACTOR_ASSOCIATION" not in signal
    assert "ACTOR_ID" not in signal
    assert "untrusted formal-review signal actor" not in signal
    assert "PR lifecycle, formal-review, and diff-comment mutation" in signal
    assert "fork and Dependabot PRs" in signal
    assert "Native required conversation resolution" in signal
    assert "actions/checkout" not in signal
    assert "secrets." not in signal
    assert "create-github-app-token" not in signal
    assert "actions/" not in signal
    assert "cache" in signal and "artifacts" in signal
    invalidator = publisher.split("  invalidate-review-state:", maxsplit=1)[1].split(
        "\n  validate:", maxsplit=1
    )[0]
    resolver = publisher.split("  resolve_review_state:", maxsplit=1)[1].split(
        "\n  resolve_route_event:", maxsplit=1
    )[0]
    route_resolver = publisher.split("  resolve_route_event:", maxsplit=1)[1].split(
        "\n  fail-closed-review-resolution:", maxsplit=1
    )[0]
    review_fallback = publisher.split("  fail-closed-review-resolution:", maxsplit=1)[1].split(
        "\n  fail-closed-route-resolution:", maxsplit=1
    )[0]
    route_fallback = publisher.split("  fail-closed-route-resolution:", maxsplit=1)[1].split(
        "\n  invalidate-review-state:", maxsplit=1
    )[0]
    assert "github.event_name == 'workflow_run'" in resolver
    assert "github.event.workflow_run.conclusion == 'success'" not in resolver
    assert (
        "github.event.workflow_run.path == "
        "'.github/workflows/review-route-invalidation.yml'" in resolver
    )
    assert 'fromJSON(\'["pull_request_review","pull_request_review_comment"]\')' in resolver
    assert "workflow_run.pull_requests" not in resolver
    assert "any(.workflow_run.pull_requests[]?;" not in resolver
    assert "SOURCE_HEAD_SHA: ${{ github.event.workflow_run.head_sha }}" in resolver
    assert "SOURCE_ACTOR_ID: ${{ github.event.workflow_run.actor.id }}" in resolver
    assert "SOURCE_ACTOR_LOGIN: ${{ github.event.workflow_run.actor.login }}" in resolver
    assert "SIGNAL_EVENT: ${{ github.event.workflow_run.event }}" in resolver
    assert "review-state trigger actor is invalid" in resolver
    assert "175728472|199175422|136622811" in resolver
    assert "collaborators/$SOURCE_ACTOR_LOGIN/permission" in resolver
    assert "Untrusted formal-review signal actor is irrelevant" in resolver
    assert "relevant: ${{ steps.resolve.outputs.relevant }}" in resolver
    assert "relevant=false" in resolver
    assert "actor permission lookup was inconclusive; invalidating fail closed" in resolver
    assert '[ "$source_head" != "$signal_head" ]' in resolver
    assert "Review-event workflow_run.head_sha is the reviewed PR head" in resolver
    assert "merge_commit_sha" not in resolver
    assert ".mergeable" not in resolver
    assert "($current_matches | length) == 1" in resolver
    assert "($signal_matches | length) == 0" in resolver
    assert "always() &&" in review_fallback
    assert "needs.resolve_review_state.result != 'success'" in review_fallback
    assert "concurrency:" not in review_fallback
    assert "SOURCE_HEAD_SHA: ${{ github.event.workflow_run.head_sha }}" in review_fallback
    assert "permission-pull-requests: read" in review_fallback
    assert "Confirmed read-only outsider review remains irrelevant" in review_fallback
    assert "failed review signal could not be safely rebound" in review_fallback
    assert "pulls?state=open" in review_fallback
    assert 'post_pending "$current_head"' in review_fallback
    assert 'post_pending_if_unowned "$source_head"' in review_fallback
    assert "review signal resolution failed closed" in review_fallback
    assert "always() &&" in route_fallback
    assert "needs.resolve_route_event.result != 'success'" in route_fallback
    assert "concurrency:" not in route_fallback
    assert "Resolver fallbacks intentionally stay outside concurrency groups" in publisher
    assert "Keep this fallback uncancellable" in publisher
    assert "route event resolution failed closed" in route_fallback
    assert "steps.route-fallback.outputs.token" in route_fallback
    assert "failed route event could not be safely rebound" in route_fallback
    assert "pulls?state=open" in route_fallback
    assert 'post_pending "$current_head"' in route_fallback
    assert 'post_pending_if_unowned "$event_head"' in route_fallback
    assert "stale fallback head belongs to a replacement PR; skipped" in review_fallback
    assert "stale fallback head belongs to a replacement PR; skipped" in route_fallback
    assert "environment:\n      name: review-route-publisher" in invalidator
    assert "steps.invalidator.outputs.token" in invalidator
    assert "group: review-route-${{ needs.resolve_review_state.outputs.head_sha }}" in invalidator
    assert "needs.resolve_review_state.outputs.relevant != 'false'" in invalidator
    validate_job = publisher.split("\n  validate:", maxsplit=1)[1]
    assert "group: review-route-${{ needs.resolve_route_event.outputs.head_sha }}" in validate_job
    assert "^review-route-pr-([0-9]+)-head-([0-9a-fA-F]{40})-trigger-([0-9]+)$" in resolver
    assert invalidator.index('post_pending_for_current_pr "$head_sha"') < invalidator.index(
        'post_pending_if_unowned "$EXPECTED_HEAD_SHA"'
    )
    assert 'post_pending_if_unowned "$head_sha"' in invalidator
    assert 'post_pending_if_unowned "$SIGNAL_HEAD_SHA"' in invalidator
    assert "could not invalidate stale signal head" in invalidator
    assert "head now belongs exclusively to a replacement PR; skipped" in invalidator
    assert "PR head changed after review activity; revalidate the route." in invalidator
    assert "stale prior-head invalidation ignored" not in invalidator
    assert "stale pre-success invalidation ignored" not in invalidator
    assert "-f state=pending" in invalidator
    assert "state=success" not in invalidator
    assert "actions/checkout" not in invalidator
    assert "github.event.workflow_run.event == 'pull_request_target'" in route_resolver
    assert "github.event.workflow_run.conclusion == 'success'" not in route_resolver
    assert "SIGNAL_TITLE: ${{ github.event.workflow_run.display_title }}" in route_resolver
    assert "SOURCE_ACTOR_ID: ${{ github.event.workflow_run.actor.id }}" in route_resolver
    assert "SOURCE_HEAD_SHA" not in route_resolver
    assert "invalid lifecycle signal identity" in route_resolver
    assert "lifecycle trigger actor is invalid" in route_resolver
    assert "event_head=${{ steps.resolve.outputs.event_head }}" not in route_resolver
    assert "event_head: ${{ steps.resolve.outputs.event_head }}" in route_resolver
    assert "(.number | tostring) == $pr" in route_resolver
    assert "event_head=%s\\nhead_sha=%s\\npr_number=%s\\n" in route_resolver
    assert "EVENT_HEAD_SHA: ${{ needs.resolve_route_event.outputs.event_head }}" in validate_job


@pytest.mark.parametrize(
    ("draft", "validation", "workflow_matches", "expected"),
    [
        ("true", "success", True, "pending"),
        ("true", "failure", True, "failure"),
        ("false", "success", True, "success"),
        ("false", "failure", True, "failure"),
        ("true", "success", False, "failure"),
        ("true", "failure", False, "failure"),
        ("false", "success", False, "failure"),
        ("false", "failure", False, "failure"),
    ],
)
def test_publisher_executes_fail_closed_status_matrix(
    tmp_path: Path,
    draft: str,
    validation: str,
    workflow_matches: bool,
    expected: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    start = workflow.index("          state=failure")
    end = workflow.index("          posted=false", start)
    decision = textwrap.dedent(workflow[start:end])
    shell = "\n".join(
        [
            "set -u",
            "gh() { printf '{}\\n'; }",
            "verify_finalizer() { return 0; }",
            (
                'jq() { if [ "${1:-}" = "-er" ] && '
                '[ "${2:-}" = ".data.node.author.login" ]; then '
                "printf 'trusted-reviewer\\n'; else return 0; fi; }"
            ),
            'head_unique="true"',
            'finalizer_ok="true"',
            decision,
            'printf "%s\\t%s\\n" "$state" "$description"',
        ]
    )
    env = {
        **os.environ,
        "FINALIZE_ROUTE": "true",
        "FINALIZE_COMMENT_NODE_ID": FINALIZER_NODE_ID,
        "FINALIZE_COMMENT_ACTOR_ID": str(HUMAN_ID),
        "FINALIZE_COMMENT_CREATED_AT": CREATED_AT,
        "GITHUB_REPOSITORY": "bioedca/Yeliztli",
        "HEAD_SHA": HEAD_SHA,
        "IS_DRAFT": draft,
        "PR_NUMBER": "2183",
        "PR_UPDATED_AT": PR_UPDATED_AT,
        "RUNNER_TEMP": str(tmp_path),
        "TRUSTED_SHA": HEAD_SHA,
        "VALIDATION_OUTCOME": validation,
        "WORKFLOW_SHA": HEAD_SHA if workflow_matches else "b" * 40,
    }
    completed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", shell],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    assert completed.stdout.split("\t", maxsplit=1)[0] == expected


def test_review_signal_contains_no_pr_controlled_trust_decision() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route-invalidation.yml").read_text(
        encoding="utf-8"
    )
    shell = _workflow_step_script(workflow, "Emit review-state signal")
    completed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", shell],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "Review-state invalidation signal emitted."


@pytest.mark.parametrize(
    (
        "signal_event",
        "signal_trigger_actor_id",
        "source_actor_id",
        "source_actor_login",
        "permission",
        "permission_user_id",
        "actor_expected",
        "relevant_expected",
    ),
    [
        (
            "pull_request_review",
            BOT_ACTOR_IDS[CODEX_GATE],
            BOT_ACTOR_IDS[CODEX_GATE],
            "chatgpt-codex-connector[bot]",
            None,
            None,
            0,
            "true",
        ),
        (
            "pull_request_review",
            AUTHOR_ID,
            AUTHOR_ID,
            "maintainer",
            "write",
            AUTHOR_ID,
            0,
            "true",
        ),
        (
            "pull_request_review",
            AUTHOR_ID,
            AUTHOR_ID,
            "outsider",
            "read",
            AUTHOR_ID,
            0,
            "false",
        ),
        (
            "pull_request_review_comment",
            AUTHOR_ID,
            AUTHOR_ID,
            "outsider",
            "read",
            AUTHOR_ID,
            0,
            "true",
        ),
        (
            "pull_request_review",
            HUMAN_ID,
            AUTHOR_ID,
            "maintainer",
            "write",
            AUTHOR_ID,
            1,
            None,
        ),
        (
            "not_a_review_event",
            AUTHOR_ID,
            AUTHOR_ID,
            "maintainer",
            "write",
            AUTHOR_ID,
            1,
            None,
        ),
        (
            "pull_request_review",
            AUTHOR_ID,
            AUTHOR_ID,
            "bad/login",
            "write",
            AUTHOR_ID,
            1,
            None,
        ),
        (
            "pull_request_review",
            AUTHOR_ID,
            AUTHOR_ID,
            "maintainer",
            None,
            None,
            0,
            "true",
        ),
        (
            "pull_request_review",
            AUTHOR_ID,
            AUTHOR_ID,
            "maintainer",
            "write",
            HUMAN_ID,
            0,
            "true",
        ),
    ],
)
@pytest.mark.parametrize(
    (
        "signal_head",
        "source_head_sha",
        "live_head_sha",
        "duplicate_head",
        "signal_owned_elsewhere",
        "state",
        "base_ref",
        "base_repo",
        "expected",
    ),
    [
        (HEAD_SHA, HEAD_SHA, HEAD_SHA, False, False, "open", "main", "bioedca/Yeliztli", 0),
        (
            "b" * 40,
            HEAD_SHA,
            HEAD_SHA,
            False,
            False,
            "open",
            "main",
            "bioedca/Yeliztli",
            1,
        ),
        (HEAD_SHA, "b" * 40, HEAD_SHA, False, False, "open", "main", "bioedca/Yeliztli", 1),
        (HEAD_SHA, HEAD_SHA, "b" * 40, False, False, "open", "main", "bioedca/Yeliztli", 0),
        (HEAD_SHA, HEAD_SHA, "b" * 40, False, True, "open", "main", "bioedca/Yeliztli", 1),
        (HEAD_SHA, HEAD_SHA, HEAD_SHA, True, False, "open", "main", "bioedca/Yeliztli", 1),
        (HEAD_SHA, HEAD_SHA, HEAD_SHA, False, False, "closed", "main", "bioedca/Yeliztli", 1),
        (HEAD_SHA, HEAD_SHA, HEAD_SHA, False, False, "open", "release", "bioedca/Yeliztli", 1),
        (
            HEAD_SHA,
            HEAD_SHA,
            HEAD_SHA,
            False,
            False,
            "open",
            "main",
            "attacker/Yeliztli",
            1,
        ),
    ],
)
def test_review_signal_resolver_binds_source_and_live_head(
    tmp_path: Path,
    signal_event: str,
    signal_trigger_actor_id: int,
    source_actor_id: int,
    source_actor_login: str,
    permission: str | None,
    permission_user_id: int | None,
    actor_expected: int,
    relevant_expected: str | None,
    signal_head: str,
    source_head_sha: str,
    live_head_sha: str,
    duplicate_head: bool,
    signal_owned_elsewhere: bool,
    state: str,
    base_ref: str,
    base_repo: str,
    expected: int,
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    shell = _workflow_step_script(workflow, "Bind signal to its associated pull request")
    pull = {
        "number": 2183,
        "state": state,
        "base": {"ref": base_ref, "repo": {"full_name": base_repo}},
        "head": {"sha": live_head_sha},
    }
    open_pulls = [pull]
    if duplicate_head:
        open_pulls.append({**pull, "number": 2184})
    if signal_owned_elsewhere:
        open_pulls.append(
            {
                **pull,
                "number": 2184,
                "head": {"sha": signal_head},
            }
        )
    pr_fixture = tmp_path / "fixture-pr.json"
    open_fixture = tmp_path / "open-prs.json"
    event_fixture = tmp_path / "event.json"
    permission_fixture = tmp_path / "permission.json"
    output = tmp_path / "output"
    pr_fixture.write_text(json.dumps(pull), encoding="utf-8")
    open_fixture.write_text(json.dumps([open_pulls]), encoding="utf-8")
    event_fixture.write_text(
        json.dumps({"workflow_run": {"head_sha": source_head_sha, "pull_requests": []}}),
        encoding="utf-8",
    )
    if permission is not None:
        permission_fixture.write_text(
            json.dumps({"permission": permission, "user": {"id": permission_user_id}}),
            encoding="utf-8",
        )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env bash
set -eu
case "$*" in
  *"collaborators/"*"/permission"*)
    [ -n "${PERMISSION_FIXTURE:-}" ] || exit 2
    exec /bin/cat "$PERMISSION_FIXTURE"
    ;;
  *"pulls/2183"*) exec /bin/cat "$PR_FIXTURE" ;;
  *"pulls?state=open"*) exec /bin/cat "$OPEN_PRS_FIXTURE" ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    completed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", shell],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "GH_TOKEN": "test-token",
            "GITHUB_EVENT_PATH": str(event_fixture),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_REPOSITORY": "bioedca/Yeliztli",
            "OPEN_PRS_FIXTURE": str(open_fixture),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PERMISSION_FIXTURE": str(permission_fixture) if permission is not None else "",
            "PR_FIXTURE": str(pr_fixture),
            "RUNNER_TEMP": str(tmp_path),
            "SIGNAL_EVENT": signal_event,
            "SIGNAL_TITLE": _signal_title(
                signal_head,
                trigger_actor_id=signal_trigger_actor_id,
            ),
            "SOURCE_ACTOR_ID": str(source_actor_id),
            "SOURCE_ACTOR_LOGIN": source_actor_login,
            "SOURCE_HEAD_SHA": source_head_sha,
        },
        text=True,
    )
    combined_expected = max(expected, actor_expected)
    assert completed.returncode == combined_expected, completed.stderr
    if combined_expected == 0:
        assert output.read_text(encoding="utf-8").splitlines() == [
            f"head_sha={live_head_sha}",
            "pr_number=2183",
            f"relevant={relevant_expected}",
            f"signal_head={signal_head}",
        ]


@pytest.mark.parametrize(
    (
        "source_actor_id",
        "source_actor_login",
        "permission",
        "live_head",
        "stale_owned_elsewhere",
        "expected_pending_heads",
    ),
    [
        (
            BOT_ACTOR_IDS[CODEX_GATE],
            "chatgpt-codex-connector[bot]",
            None,
            HEAD_SHA,
            False,
            [HEAD_SHA],
        ),
        (
            BOT_ACTOR_IDS[CODEX_GATE],
            "chatgpt-codex-connector[bot]",
            None,
            "b" * 40,
            False,
            ["b" * 40, HEAD_SHA],
        ),
        (
            BOT_ACTOR_IDS[CODEX_GATE],
            "chatgpt-codex-connector[bot]",
            None,
            "b" * 40,
            True,
            ["b" * 40],
        ),
        (333, "read-only-outsider", "read", HEAD_SHA, False, []),
    ],
)
def test_failed_review_resolver_fallback_respects_actor_relevance(
    tmp_path: Path,
    source_actor_id: int,
    source_actor_login: str,
    permission: str | None,
    live_head: str,
    stale_owned_elsewhere: bool,
    expected_pending_heads: list[str],
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    shell = _workflow_step_script(workflow, "Invalidate immutable review-signal head")
    pull = {
        "number": 2183,
        "state": "open",
        "base": {"ref": "main", "repo": {"full_name": "bioedca/Yeliztli"}},
        "head": {"sha": live_head},
    }
    pull_fixture = tmp_path / "pull.json"
    open_pulls_fixture = tmp_path / "open-pulls.json"
    permission_fixture = tmp_path / "permission.json"
    status_log = tmp_path / "statuses.log"
    pull_fixture.write_text(json.dumps(pull), encoding="utf-8")
    open_pulls = [pull]
    if stale_owned_elsewhere:
        open_pulls.append(
            {
                **pull,
                "number": 2184,
                "head": {"sha": HEAD_SHA},
            }
        )
    open_pulls_fixture.write_text(json.dumps([open_pulls]), encoding="utf-8")
    if permission is not None:
        permission_fixture.write_text(
            json.dumps({"permission": permission, "user": {"id": source_actor_id}}),
            encoding="utf-8",
        )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env bash
set -eu
case "$*" in
  *"collaborators/"*"/permission"*) exec /bin/cat "$PERMISSION_FIXTURE" ;;
  *"pulls?state=open"*) exec /bin/cat "$OPEN_PRS_FIXTURE" ;;
  *"pulls/2183"*) exec /bin/cat "$PULL_FIXTURE" ;;
  *"statuses/"*) printf '%s\n' "$*" >> "$STATUS_LOG" ; printf '{}\n' ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    completed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", shell],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "GH_TOKEN": "test-token",
            "GITHUB_REPOSITORY": "bioedca/Yeliztli",
            "OPEN_PRS_FIXTURE": str(open_pulls_fixture),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PERMISSION_FIXTURE": str(permission_fixture),
            "PULL_FIXTURE": str(pull_fixture),
            "RUNNER_TEMP": str(tmp_path),
            "RUN_URL": "https://example.test/run",
            "SIGNAL_EVENT": "pull_request_review",
            "SIGNAL_TITLE": _signal_title(HEAD_SHA),
            "SOURCE_ACTOR_ID": str(source_actor_id),
            "SOURCE_ACTOR_LOGIN": source_actor_login,
            "SOURCE_HEAD_SHA": HEAD_SHA.upper(),
            "STATUS_LOG": str(status_log),
        },
        text=True,
    )
    if expected_pending_heads:
        assert completed.returncode == 1
        status_calls = status_log.read_text(encoding="utf-8").splitlines()
        assert [
            next(argument for argument in call.split() if "/statuses/" in argument).rsplit("/", 1)[
                1
            ]
            for call in status_calls
        ] == expected_pending_heads
        assert all("state=pending" in call for call in status_calls)
        assert "review signal resolution failed closed" in completed.stdout
    else:
        assert completed.returncode == 0
        assert not status_log.exists()
        assert "Confirmed read-only outsider review remains irrelevant" in completed.stdout


@pytest.mark.parametrize(
    ("event_name", "live_head", "stale_owned_elsewhere", "expected_pending_heads"),
    [
        ("workflow_run", HEAD_SHA, False, [HEAD_SHA]),
        ("workflow_run", "b" * 40, False, ["b" * 40, HEAD_SHA]),
        ("workflow_run", "b" * 40, True, ["b" * 40]),
        ("issue_comment", HEAD_SHA, False, [HEAD_SHA]),
    ],
)
def test_failed_route_resolver_marks_bound_head_pending(
    tmp_path: Path,
    event_name: str,
    live_head: str,
    stale_owned_elsewhere: bool,
    expected_pending_heads: list[str],
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    shell = _workflow_step_script(workflow, "Resolve fallback head and invalidate it")
    pull_fixture = tmp_path / "pull.json"
    open_pulls_fixture = tmp_path / "open-pulls.json"
    status_log = tmp_path / "statuses.log"
    pull = {
        "number": 2183,
        "state": "open",
        "base": {"ref": "main", "repo": {"full_name": "bioedca/Yeliztli"}},
        "head": {"sha": live_head},
    }
    pull_fixture.write_text(json.dumps(pull), encoding="utf-8")
    open_pulls = [pull]
    if stale_owned_elsewhere:
        open_pulls.append(
            {
                **pull,
                "number": 2184,
                "head": {"sha": HEAD_SHA},
            }
        )
    open_pulls_fixture.write_text(json.dumps([open_pulls]), encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env bash
set -eu
case "$*" in
  *"pulls?state=open"*) exec /bin/cat "$OPEN_PRS_FIXTURE" ;;
  *"pulls/2183"*) exec /bin/cat "$PULL_FIXTURE" ;;
  *"statuses/"*) printf '%s\n' "$*" >> "$STATUS_LOG" ; printf '{}\n' ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    completed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", shell],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "EVENT_NAME": event_name,
            "GH_TOKEN": "test-token",
            "GITHUB_REPOSITORY": "bioedca/Yeliztli",
            "ISSUE_NUMBER": "2183",
            "OPEN_PRS_FIXTURE": str(open_pulls_fixture),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PULL_FIXTURE": str(pull_fixture),
            "RUNNER_TEMP": str(tmp_path),
            "RUN_URL": "https://example.test/run",
            "SIGNAL_TITLE": _signal_title(HEAD_SHA),
            "STATUS_LOG": str(status_log),
        },
        text=True,
    )
    assert completed.returncode == 1
    status_calls = status_log.read_text(encoding="utf-8").splitlines()
    assert [
        next(argument for argument in call.split() if "/statuses/" in argument).rsplit("/", 1)[1]
        for call in status_calls
    ] == expected_pending_heads
    assert all("state=pending" in call for call in status_calls)
    assert "route event resolution failed closed" in completed.stdout


@pytest.mark.parametrize(
    (
        "event_name",
        "signal_title",
        "issue_number",
        "fixture_number",
        "live_head",
        "base_ref",
        "base_repo",
        "author",
        "head_repo",
        "expected",
        "expected_event_head",
    ),
    [
        (
            "workflow_run",
            _signal_title(HEAD_SHA),
            "",
            2183,
            HEAD_SHA,
            "main",
            "bioedca/Yeliztli",
            "dependabot[bot]",
            "bioedca/Yeliztli",
            0,
            HEAD_SHA,
        ),
        (
            "workflow_run",
            _signal_title("b" * 40),
            "",
            2183,
            HEAD_SHA,
            "main",
            "bioedca/Yeliztli",
            "dependabot[bot]",
            "bioedca/Yeliztli",
            0,
            "b" * 40,
        ),
        (
            "workflow_run",
            _signal_title(HEAD_SHA),
            "",
            2183,
            HEAD_SHA,
            "main",
            "bioedca/Yeliztli",
            "outside-contributor",
            "outside/Yeliztli",
            0,
            HEAD_SHA,
        ),
        (
            "issue_comment",
            "",
            "2183",
            2183,
            HEAD_SHA,
            "main",
            "bioedca/Yeliztli",
            "bioedca",
            "bioedca/Yeliztli",
            0,
            HEAD_SHA,
        ),
        (
            "workflow_run",
            "malformed",
            "",
            2183,
            HEAD_SHA,
            "main",
            "bioedca/Yeliztli",
            "dependabot[bot]",
            "bioedca/Yeliztli",
            1,
            None,
        ),
        (
            "workflow_run",
            _signal_title(HEAD_SHA, trigger_actor_id=HUMAN_ID),
            "",
            2183,
            HEAD_SHA,
            "main",
            "bioedca/Yeliztli",
            "dependabot[bot]",
            "bioedca/Yeliztli",
            1,
            None,
        ),
        (
            "workflow_run",
            _signal_title(HEAD_SHA, pr_number=2184),
            "",
            2183,
            HEAD_SHA,
            "main",
            "bioedca/Yeliztli",
            "dependabot[bot]",
            "bioedca/Yeliztli",
            1,
            None,
        ),
        (
            "workflow_run",
            _signal_title(HEAD_SHA),
            "",
            2183,
            "not-a-sha",
            "main",
            "bioedca/Yeliztli",
            "dependabot[bot]",
            "bioedca/Yeliztli",
            1,
            None,
        ),
        (
            "workflow_run",
            _signal_title(HEAD_SHA),
            "",
            2183,
            HEAD_SHA,
            "release",
            "bioedca/Yeliztli",
            "dependabot[bot]",
            "bioedca/Yeliztli",
            1,
            None,
        ),
        (
            "workflow_run",
            _signal_title(HEAD_SHA),
            "",
            2183,
            HEAD_SHA,
            "main",
            "attacker/Yeliztli",
            "dependabot[bot]",
            "bioedca/Yeliztli",
            1,
            None,
        ),
    ],
)
def test_lifecycle_signal_resolver_live_binds_pr_without_source_secrets(
    tmp_path: Path,
    event_name: str,
    signal_title: str,
    issue_number: str,
    fixture_number: int,
    live_head: str,
    base_ref: str,
    base_repo: str,
    author: str,
    head_repo: str,
    expected: int,
    expected_event_head: str | None,
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    shell = _workflow_step_script(workflow, "Resolve live pull request head")
    pull = {
        "number": fixture_number,
        "state": "open",
        "base": {"ref": base_ref, "repo": {"full_name": base_repo}},
        "head": {"sha": live_head, "repo": {"full_name": head_repo}},
        "user": {"login": author},
    }
    pr_fixture = tmp_path / "fixture-pr.json"
    output = tmp_path / "output"
    pr_fixture.write_text(json.dumps(pull), encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env bash
set -eu
case "$*" in
  *"repos/bioedca/Yeliztli/pulls/"*) exec /bin/cat "$PR_FIXTURE" ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    completed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", shell],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "EVENT_NAME": event_name,
            "GH_TOKEN": "test-token",
            "GITHUB_OUTPUT": str(output),
            "GITHUB_REPOSITORY": "bioedca/Yeliztli",
            "ISSUE_NUMBER": issue_number,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PR_FIXTURE": str(pr_fixture),
            "RUNNER_TEMP": str(tmp_path),
            "SIGNAL_TITLE": signal_title,
            "SOURCE_ACTOR_ID": str(AUTHOR_ID),
        },
        text=True,
    )
    assert completed.returncode == expected, completed.stderr
    if expected == 0:
        assert output.read_text(encoding="utf-8").splitlines() == [
            f"event_head={expected_event_head}",
            f"head_sha={live_head}",
            "pr_number=2183",
        ]


@pytest.mark.parametrize(
    (
        "pr_snapshot_mode",
        "stale_owned_elsewhere",
        "duplicate_live_head",
        "malformed_response",
        "stale_post_failures",
        "replace_stale_on_retry",
        "signal_head",
        "expected_statuses",
        "expected_attempts",
        "expected_fetch_attempts",
        "expected_returncode",
    ),
    [
        (
            "normal",
            False,
            False,
            False,
            0,
            False,
            "b" * 40,
            [HEAD_SHA, "b" * 40],
            [HEAD_SHA, "b" * 40],
            1,
            0,
        ),
        (
            "normal",
            True,
            False,
            False,
            0,
            False,
            "b" * 40,
            [HEAD_SHA],
            [HEAD_SHA],
            1,
            0,
        ),
        (
            "normal",
            False,
            False,
            True,
            0,
            False,
            "b" * 40,
            [HEAD_SHA],
            [HEAD_SHA],
            1,
            1,
        ),
        (
            "normal",
            False,
            False,
            False,
            3,
            False,
            "b" * 40,
            [HEAD_SHA],
            [HEAD_SHA, *("b" * 40 for _ in range(3))],
            1,
            1,
        ),
        (
            "normal",
            False,
            True,
            False,
            0,
            False,
            "b" * 40,
            [HEAD_SHA, "b" * 40],
            [HEAD_SHA, "b" * 40],
            1,
            0,
        ),
        (
            "fetch-failure",
            False,
            False,
            False,
            0,
            False,
            "b" * 40,
            ["b" * 40],
            ["b" * 40],
            3,
            1,
        ),
        (
            "malformed",
            False,
            False,
            False,
            0,
            False,
            "b" * 40,
            ["b" * 40],
            ["b" * 40],
            3,
            1,
        ),
        (
            "fetch-failure",
            True,
            False,
            False,
            0,
            False,
            "b" * 40,
            [],
            [],
            3,
            1,
        ),
        (
            "fetch-failure",
            False,
            False,
            False,
            3,
            False,
            "b" * 40,
            [],
            ["b" * 40, "b" * 40, "b" * 40],
            3,
            1,
        ),
        (
            "fetch-failure",
            False,
            False,
            False,
            1,
            False,
            "b" * 40,
            ["b" * 40],
            ["b" * 40, "b" * 40],
            3,
            1,
        ),
        (
            "fetch-failure",
            False,
            False,
            False,
            1,
            True,
            "b" * 40,
            [],
            ["b" * 40],
            3,
            1,
        ),
        (
            "fetch-failure",
            True,
            False,
            False,
            0,
            False,
            "c" * 40,
            ["c" * 40],
            ["c" * 40],
            3,
            1,
        ),
        (
            "transient-failure",
            False,
            False,
            False,
            0,
            False,
            "b" * 40,
            [HEAD_SHA, "b" * 40],
            [HEAD_SHA, "b" * 40],
            2,
            0,
        ),
    ],
)
def test_review_signal_invalidator_rechecks_stale_head_ownership(
    tmp_path: Path,
    pr_snapshot_mode: str,
    stale_owned_elsewhere: bool,
    duplicate_live_head: bool,
    malformed_response: bool,
    stale_post_failures: int,
    replace_stale_on_retry: bool,
    signal_head: str,
    expected_statuses: list[str],
    expected_attempts: list[str],
    expected_fetch_attempts: int,
    expected_returncode: int,
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    shell = _workflow_step_script(workflow, "Mark current PR head pending")
    pull = {
        "number": 2183,
        "state": "open",
        "base": {"ref": "main", "repo": {"full_name": "bioedca/Yeliztli"}},
        "head": {
            "sha": HEAD_SHA.upper(),
            "ref": "issue-2183",
            "repo": {"full_name": "bioedca/Yeliztli"},
        },
    }
    open_pulls = [pull]
    if duplicate_live_head:
        open_pulls.append({**pull, "number": 2184})
    if stale_owned_elsewhere:
        open_pulls.append(
            {
                **pull,
                "number": 2184,
                "head": {"sha": "b" * 40},
            }
        )
    pr_fixture = tmp_path / "fixture-pr.json"
    open_fixture = tmp_path / "open-prs.json"
    later_open_fixture = tmp_path / "later-open-prs.json"
    pr_counter = tmp_path / "pr-counter"
    open_counter = tmp_path / "open-counter"
    status_failure_counter = tmp_path / "status-failure-counter"
    attempt_log = tmp_path / "attempts.log"
    status_details_log = tmp_path / "status-details.log"
    status_log = tmp_path / "statuses.log"
    pr_fixture.write_text(json.dumps(pull), encoding="utf-8")
    open_fixture.write_text(
        json.dumps({"unexpected": []} if malformed_response else [open_pulls]),
        encoding="utf-8",
    )
    later_open_fixture.write_text(
        json.dumps(
            [
                [
                    pull,
                    {
                        **pull,
                        "number": 2184,
                        "head": {"sha": "b" * 40},
                    },
                ]
            ]
        ),
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env bash
set -eu
case "$*" in
  *"pulls/2183"*)
    count=0
    if [ -f "$PR_COUNTER" ]; then count="$(/bin/cat "$PR_COUNTER")"; fi
    count=$((count + 1))
    printf '%s\n' "$count" > "$PR_COUNTER"
    case "$PR_SNAPSHOT_MODE" in
      fetch-failure) exit 1 ;;
      malformed) printf '{}\n' ;;
      transient-failure)
        if [ "$count" = "1" ]; then exit 1; fi
        exec /bin/cat "$PR_FIXTURE"
        ;;
      normal) exec /bin/cat "$PR_FIXTURE" ;;
      *) exit 2 ;;
    esac
    ;;
  *"pulls?state=open"*)
    count=0
    if [ -f "$OPEN_COUNTER" ]; then count="$(/bin/cat "$OPEN_COUNTER")"; fi
    count=$((count + 1))
    printf '%s\n' "$count" > "$OPEN_COUNTER"
    if [ "$REPLACE_STALE_ON_RETRY" = "true" ] && [ "$count" -gt 1 ]; then
      exec /bin/cat "$LATER_OPEN_PRS_FIXTURE"
    fi
    exec /bin/cat "$OPEN_PRS_FIXTURE"
    ;;
  *"statuses/"*)
    sha=""
    state=""
    context=""
    for argument in "$@"; do
      case "$argument" in
        repos/*/statuses/*) sha="${argument##*/}" ;;
        state=*) state="${argument#state=}" ;;
        context=*) context="${argument#context=}" ;;
      esac
    done
    printf '%s\n' "$sha" >> "$ATTEMPT_LOG"
    printf '%s\t%s\t%s\n' "$sha" "$state" "$context" >> "$STATUS_DETAILS_LOG"
    if [ "$sha" = "${FAIL_STATUS_SHA:-}" ]; then
      count=0
      if [ -f "$STATUS_FAILURE_COUNTER" ]; then
        count="$(/bin/cat "$STATUS_FAILURE_COUNTER")"
      fi
      count=$((count + 1))
      printf '%s\n' "$count" > "$STATUS_FAILURE_COUNTER"
      if [ "$count" -le "$FAIL_STATUS_COUNT" ]; then exit 1; fi
    fi
    printf '%s\n' "$sha" >> "$STATUS_LOG"
    printf '{}\n'
    ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    fake_sleep = fake_bin / "sleep"
    fake_sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_sleep.chmod(0o755)
    completed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", shell],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "EXPECTED_HEAD_SHA": "b" * 40,
            "ATTEMPT_LOG": str(attempt_log),
            "FAIL_STATUS_COUNT": str(stale_post_failures),
            "FAIL_STATUS_SHA": "b" * 40 if stale_post_failures else "",
            "GH_TOKEN": "test-token",
            "GITHUB_REPOSITORY": "bioedca/Yeliztli",
            "LATER_OPEN_PRS_FIXTURE": str(later_open_fixture),
            "OPEN_COUNTER": str(open_counter),
            "OPEN_PRS_FIXTURE": str(open_fixture),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PR_COUNTER": str(pr_counter),
            "PR_FIXTURE": str(pr_fixture),
            "PR_NUMBER": "2183",
            "PR_SNAPSHOT_MODE": pr_snapshot_mode,
            "REPLACE_STALE_ON_RETRY": str(replace_stale_on_retry).lower(),
            "RUNNER_TEMP": str(tmp_path),
            "RUN_URL": "https://example.test/run",
            "SIGNAL_HEAD_SHA": signal_head,
            "STATUS_DETAILS_LOG": str(status_details_log),
            "STATUS_FAILURE_COUNTER": str(status_failure_counter),
            "STATUS_LOG": str(status_log),
        },
        text=True,
    )
    assert completed.returncode == expected_returncode, completed.stderr
    actual_statuses = (
        status_log.read_text(encoding="utf-8").splitlines() if status_log.exists() else []
    )
    actual_attempts = (
        attempt_log.read_text(encoding="utf-8").splitlines() if attempt_log.exists() else []
    )
    assert actual_statuses == expected_statuses
    assert actual_attempts == expected_attempts
    assert int(pr_counter.read_text(encoding="utf-8")) == expected_fetch_attempts
    actual_status_details = (
        status_details_log.read_text(encoding="utf-8").splitlines()
        if status_details_log.exists()
        else []
    )
    assert actual_status_details == [f"{sha}\tpending\tReview Route" for sha in expected_attempts]


def test_review_signal_invalidator_rechecks_ownership_before_retry(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    shell = _workflow_step_script(workflow, "Mark current PR head pending")
    claimed = {
        "number": 2183,
        "state": "open",
        "base": {"ref": "main", "repo": {"full_name": "bioedca/Yeliztli"}},
        "head": {"sha": HEAD_SHA.upper()},
    }
    replacement = {**claimed, "number": 2184}
    closed_claim = {**claimed, "state": "closed"}
    initial_pr = tmp_path / "initial-pr.json"
    later_pr = tmp_path / "later-pr.json"
    open_fixture = tmp_path / "open-prs.json"
    pr_counter = tmp_path / "pr-counter"
    attempt_log = tmp_path / "attempts.log"
    status_log = tmp_path / "statuses.log"
    initial_pr.write_text(json.dumps(claimed), encoding="utf-8")
    later_pr.write_text(json.dumps(closed_claim), encoding="utf-8")
    open_fixture.write_text(json.dumps([[replacement]]), encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env bash
set -eu
case "$*" in
  *"pulls/2183"*)
    count=0
    if [ -f "$PR_COUNTER" ]; then read -r count < "$PR_COUNTER"; fi
    if [ "$count" = "0" ]; then
      printf '1\n' > "$PR_COUNTER"
      exec /bin/cat "$INITIAL_PR"
    fi
    printf '2\n' > "$PR_COUNTER"
    exec /bin/cat "$LATER_PR"
    ;;
  *"pulls?state=open"*) exec /bin/cat "$OPEN_PRS_FIXTURE" ;;
  *"statuses/"*)
    for argument in "$@"; do
      case "$argument" in
        repos/*/statuses/*)
          sha="${argument##*/}"
          printf '%s\n' "$sha" >> "$ATTEMPT_LOG"
          if [ "$sha" = "$EXPECTED_HEAD_SHA" ]; then exit 1; fi
          printf '%s\n' "$sha" >> "$STATUS_LOG"
          ;;
      esac
    done
    printf '{}\n'
    ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    completed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", shell],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "ATTEMPT_LOG": str(attempt_log),
            "EXPECTED_HEAD_SHA": HEAD_SHA,
            "GH_TOKEN": "test-token",
            "GITHUB_REPOSITORY": "bioedca/Yeliztli",
            "INITIAL_PR": str(initial_pr),
            "LATER_PR": str(later_pr),
            "OPEN_PRS_FIXTURE": str(open_fixture),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PR_COUNTER": str(pr_counter),
            "PR_NUMBER": "2183",
            "RUNNER_TEMP": str(tmp_path),
            "RUN_URL": "https://example.test/run",
            "SIGNAL_HEAD_SHA": HEAD_SHA,
            "STATUS_LOG": str(status_log),
        },
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert attempt_log.read_text(encoding="utf-8").splitlines() == [HEAD_SHA]
    assert pr_counter.read_text(encoding="utf-8").strip() == "2"
    assert not status_log.exists()


@pytest.mark.parametrize(
    (
        "v3",
        "current_open",
        "replacement_owns_head",
        "malformed_response",
        "fail_status_post",
        "updated_at",
        "expected_statuses",
        "expected_attempts",
        "expected_returncode",
    ),
    [
        (False, False, False, False, False, PR_UPDATED_AT, [HEAD_SHA], [HEAD_SHA], 0),
        (False, False, True, False, False, PR_UPDATED_AT, [], [], 0),
        (False, True, True, False, False, PR_UPDATED_AT, [HEAD_SHA], [HEAD_SHA], 0),
        (False, False, False, True, False, PR_UPDATED_AT, [], [], 1),
        (
            False,
            False,
            False,
            False,
            True,
            PR_UPDATED_AT,
            [],
            [HEAD_SHA, HEAD_SHA, HEAD_SHA],
            1,
        ),
        (False, True, False, True, False, PR_UPDATED_AT, [HEAD_SHA], [HEAD_SHA], 0),
        (
            False,
            True,
            False,
            False,
            True,
            PR_UPDATED_AT,
            [],
            [HEAD_SHA, HEAD_SHA, HEAD_SHA],
            1,
        ),
        (
            False,
            True,
            False,
            False,
            False,
            f"{PR_UPDATED_AT}\nHEAD_SHA={'b' * 40}",
            [HEAD_SHA],
            [HEAD_SHA],
            1,
        ),
        (True, True, False, False, False, PR_UPDATED_AT, [HEAD_SHA], [HEAD_SHA], 0),
    ],
)
def test_route_preinvalidator_does_not_overwrite_replacement_pr_status(
    tmp_path: Path,
    v3: bool,
    current_open: bool,
    replacement_owns_head: bool,
    malformed_response: bool,
    fail_status_post: bool,
    updated_at: str,
    expected_statuses: list[str],
    expected_attempts: list[str],
    expected_returncode: int,
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    shell = _workflow_step_script(
        workflow,
        "Pre-invalidate event head, resolve PR, and mark current head pending",
    )
    pull = {
        "number": 2183,
        "state": "open" if current_open else "closed",
        "base": {"ref": "main", "repo": {"full_name": "bioedca/Yeliztli"}},
        "head": {
            "sha": HEAD_SHA.upper(),
            "ref": "issue-2183",
            "repo": {"full_name": "bioedca/Yeliztli"},
        },
        "draft": False,
        "updated_at": updated_at,
        "body": AUTONOMOUS_SCHEMA_MARKER if v3 else "",
    }
    open_pulls = [pull] if current_open else []
    if replacement_owns_head:
        open_pulls.append(
            {
                **pull,
                "number": 2184,
                "state": "open",
            }
        )
    pr_fixture = tmp_path / "fixture-pr.json"
    open_fixture = tmp_path / "open-prs.json"
    main_fixture = tmp_path / "fixture-main-ref.json"
    event_fixture = tmp_path / "event.json"
    environment_file = tmp_path / "environment"
    attempt_log = tmp_path / "attempts.log"
    status_log = tmp_path / "statuses.log"
    pr_fixture.write_text(json.dumps(pull), encoding="utf-8")
    open_fixture.write_text(
        json.dumps({"unexpected": []} if malformed_response else [open_pulls]),
        encoding="utf-8",
    )
    main_fixture.write_text(json.dumps({"object": {"sha": HEAD_SHA}}), encoding="utf-8")
    event_fixture.write_text("{}", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env bash
set -eu
case "$*" in
  *"pulls?state=open"*) exec /bin/cat "$OPEN_PRS_FIXTURE" ;;
  *"pulls/2183"*) exec /bin/cat "$PR_FIXTURE" ;;
  *"git/ref/heads/main"*) exec /bin/cat "$MAIN_REF_FIXTURE" ;;
      *"statuses/"*)
        for argument in "$@"; do
          case "$argument" in
            repos/*/statuses/*)
              sha="${argument##*/}"
              printf '%s\n' "$sha" >> "$ATTEMPT_LOG"
              if [ "$sha" = "${FAIL_STATUS_SHA:-}" ]; then exit 1; fi
              printf '%s\n' "$sha" >> "$STATUS_LOG"
              ;;
          esac
    done
    printf '{}\n'
    ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    completed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", shell],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "ATTEMPT_LOG": str(attempt_log),
            "EVENT_HEAD_SHA": HEAD_SHA.upper(),
            "FAIL_STATUS_SHA": HEAD_SHA if fail_status_post else "",
            "FINALIZE_ROUTE": "false",
            "GH_TOKEN": "test-token",
            "GITHUB_ENV": str(environment_file),
            "GITHUB_EVENT_PATH": str(event_fixture),
            "GITHUB_REPOSITORY": "bioedca/Yeliztli",
            "MAIN_REF_FIXTURE": str(main_fixture),
            "OPEN_PRS_FIXTURE": str(open_fixture),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PR_FIXTURE": str(pr_fixture),
            "PR_NUMBER": "2183",
            "RESOLVED_HEAD_SHA": HEAD_SHA,
            "RUNNER_TEMP": str(tmp_path),
            "RUN_URL": "https://example.test/run",
            "STATUS_LOG": str(status_log),
        },
        text=True,
    )
    assert completed.returncode == expected_returncode, completed.stderr
    actual_statuses = (
        status_log.read_text(encoding="utf-8").splitlines() if status_log.exists() else []
    )
    actual_attempts = (
        attempt_log.read_text(encoding="utf-8").splitlines() if attempt_log.exists() else []
    )
    assert actual_statuses == expected_statuses
    assert actual_attempts == expected_attempts
    if v3:
        environment = environment_file.read_text(encoding="utf-8")
        assert "SCHEMA_VERSION=3" in environment
        assert "INVALIDATION_ONLY=true" in environment
        publisher_shell = _workflow_step_script(workflow, "Publish required status on PR head")
        publisher = subprocess.run(
            ["/bin/bash", "-e", "-o", "pipefail", "-c", publisher_shell],
            check=False,
            capture_output=True,
            env={
                **os.environ,
                "INVALIDATION_ONLY": "true",
            },
            text=True,
        )
        assert publisher.returncode == 0
        assert "V3 invalidation is complete after publishing pending." in publisher.stdout
    if updated_at != PR_UPDATED_AT:
        environment = (
            environment_file.read_text(encoding="utf-8") if environment_file.exists() else ""
        )
        assert "PR_UPDATED_AT=" not in environment
        assert f"HEAD_SHA={'b' * 40}" not in environment


def test_route_preinvalidator_rejects_nonwrite_finalizer(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    shell = _workflow_step_script(
        workflow,
        "Pre-invalidate event head, resolve PR, and mark current head pending",
    )
    pull = {
        "number": 2183,
        "state": "open",
        "base": {"ref": "main", "repo": {"full_name": "bioedca/Yeliztli"}},
        "head": {"sha": HEAD_SHA},
        "draft": False,
        "updated_at": PR_UPDATED_AT,
    }
    pull_fixture = tmp_path / "pull.json"
    open_fixture = tmp_path / "open.json"
    permission_fixture = tmp_path / "permission.json"
    pull_fixture.write_text(json.dumps(pull), encoding="utf-8")
    open_fixture.write_text(json.dumps([[pull]]), encoding="utf-8")
    permission_fixture.write_text(
        json.dumps({"permission": "read", "user": {"id": HUMAN_ID}}),
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env bash
set -eu
case "$*" in
  *"pulls?state=open"*) exec /bin/cat "$OPEN_FIXTURE" ;;
  *"pulls/2183"*) exec /bin/cat "$PULL_FIXTURE" ;;
  *"collaborators/"*"/permission"*) exec /bin/cat "$PERMISSION_FIXTURE" ;;
  *"statuses/"*) printf '{}\n' ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    completed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", shell],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "EVENT_HEAD_SHA": HEAD_SHA,
            "FINALIZE_COMMENT_ACTOR_ID": str(HUMAN_ID),
            "FINALIZE_COMMENT_ACTOR_LOGIN": "read-only-reviewer",
            "FINALIZE_COMMENT_CREATED_AT": CREATED_AT,
            "FINALIZE_COMMENT_NODE_ID": FINALIZER_NODE_ID,
            "FINALIZE_ROUTE": "true",
            "GH_TOKEN": "test-token",
            "GITHUB_ENV": str(tmp_path / "environment"),
            "GITHUB_REPOSITORY": "bioedca/Yeliztli",
            "OPEN_FIXTURE": str(open_fixture),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PERMISSION_FIXTURE": str(permission_fixture),
            "PULL_FIXTURE": str(pull_fixture),
            "PR_NUMBER": "2183",
            "RESOLVED_HEAD_SHA": HEAD_SHA,
            "RUNNER_TEMP": str(tmp_path),
            "RUN_URL": "https://example.test/run",
        },
        text=True,
    )
    assert completed.returncode == 1
    assert "finalizer lacks bound repository write permission" in completed.stdout


def test_route_preinvalidator_covers_event_resolved_and_live_heads(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    shell = _workflow_step_script(
        workflow,
        "Pre-invalidate event head, resolve PR, and mark current head pending",
    )
    event_head = "a" * 40
    resolved_head = "b" * 40
    live_head = "c" * 40
    pull = {
        "number": 2183,
        "state": "open",
        "base": {"ref": "main", "repo": {"full_name": "bioedca/Yeliztli"}},
        "head": {"sha": live_head},
        "draft": False,
        "updated_at": PR_UPDATED_AT,
    }
    pr_fixture = tmp_path / "fixture-pr.json"
    open_fixture = tmp_path / "open-prs.json"
    environment_file = tmp_path / "environment"
    status_log = tmp_path / "statuses.log"
    pr_fixture.write_text(json.dumps(pull), encoding="utf-8")
    open_fixture.write_text(json.dumps([[pull]]), encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env bash
set -eu
case "$*" in
  *"pulls?state=open"*) exec /bin/cat "$OPEN_PRS_FIXTURE" ;;
  *"pulls/2183"*) exec /bin/cat "$PR_FIXTURE" ;;
  *"statuses/"*)
    for argument in "$@"; do
      case "$argument" in
        repos/*/statuses/*) printf '%s\n' "${argument##*/}" >> "$STATUS_LOG" ;;
      esac
    done
    printf '{}\n'
    ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    completed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", shell],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "EVENT_HEAD_SHA": event_head,
            "FINALIZE_ROUTE": "false",
            "GH_TOKEN": "test-token",
            "GITHUB_ENV": str(environment_file),
            "GITHUB_REPOSITORY": "bioedca/Yeliztli",
            "OPEN_PRS_FIXTURE": str(open_fixture),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PR_FIXTURE": str(pr_fixture),
            "PR_NUMBER": "2183",
            "RESOLVED_HEAD_SHA": resolved_head,
            "RUNNER_TEMP": str(tmp_path),
            "RUN_URL": "https://example.test/run",
            "STATUS_LOG": str(status_log),
        },
        text=True,
    )
    assert completed.returncode == 1
    assert "head changed after concurrency binding" in completed.stdout
    assert status_log.read_text(encoding="utf-8").splitlines() == [
        live_head,
        event_head,
        resolved_head,
    ]


@pytest.mark.parametrize(
    (
        "owner_mode",
        "malformed_response",
        "fail_status_post",
        "captured_head",
        "resolved_head",
        "expected_returncode",
        "expected_statuses",
        "expected_attempts",
    ),
    [
        ("replacement", False, False, HEAD_SHA, "", 0, [], []),
        ("duplicate", False, False, HEAD_SHA, "", 1, [HEAD_SHA], [HEAD_SHA]),
        (
            "claimed",
            False,
            True,
            HEAD_SHA,
            "",
            1,
            [],
            [HEAD_SHA, HEAD_SHA, HEAD_SHA],
        ),
        ("replacement", True, False, HEAD_SHA, "", 1, [], []),
        ("claimed", False, False, "", HEAD_SHA.upper(), 1, [HEAD_SHA], [HEAD_SHA]),
        ("replacement", False, False, "", HEAD_SHA.upper(), 0, [], []),
        ("claimed", False, False, "b" * 40, HEAD_SHA, 1, ["b" * 40], ["b" * 40]),
        ("claimed", False, False, "", "not-a-sha", 1, [], []),
    ],
)
def test_route_final_publisher_skips_head_owned_by_replacement_pr(
    tmp_path: Path,
    owner_mode: str,
    malformed_response: bool,
    fail_status_post: bool,
    captured_head: str,
    resolved_head: str,
    expected_returncode: int,
    expected_statuses: list[str],
    expected_attempts: list[str],
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    shell = _workflow_step_script(workflow, "Publish required status on PR head")
    effective_head = (captured_head or resolved_head).lower()
    claimed = {
        "number": 2183,
        "state": "open",
        "base": {"ref": "main", "repo": {"full_name": "bioedca/Yeliztli"}},
        "head": {"sha": effective_head.upper()},
    }
    replacement = {**claimed, "number": 2184}
    owners = {
        "claimed": [claimed],
        "duplicate": [claimed, replacement],
        "replacement": [replacement],
    }[owner_mode]
    open_fixture = tmp_path / "open-prs.json"
    attempt_log = tmp_path / "attempts.log"
    status_log = tmp_path / "statuses.log"
    open_fixture.write_text(
        json.dumps({"unexpected": []} if malformed_response else [owners]),
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env bash
set -eu
case "$*" in
  *"pulls?state=open"*) exec /bin/cat "$OPEN_PRS_FIXTURE" ;;
  *"statuses/"*)
    for argument in "$@"; do
      case "$argument" in
        repos/*/statuses/*)
          sha="${argument##*/}"
          printf '%s\n' "$sha" >> "$ATTEMPT_LOG"
          if [ "$sha" = "${FAIL_STATUS_SHA:-}" ]; then exit 1; fi
          printf '%s\n' "$sha" >> "$STATUS_LOG"
          ;;
      esac
    done
    printf '{}\n'
    ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    completed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", shell],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "ATTEMPT_LOG": str(attempt_log),
            "FAIL_STATUS_SHA": effective_head if fail_status_post else "",
            "GH_TOKEN": "test-token",
            "HEAD_SHA": captured_head,
            "OPEN_PRS_FIXTURE": str(open_fixture),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PR_NUMBER": "2183",
            "RESOLVED_HEAD_SHA": resolved_head,
            "RUNNER_TEMP": str(tmp_path),
            "RUN_URL": "https://example.test/run",
            "STATUS_LOG": str(status_log),
            "VALIDATION_OUTCOME": "failure",
        },
        text=True,
    )
    assert completed.returncode == expected_returncode, completed.stderr
    actual_statuses = (
        status_log.read_text(encoding="utf-8").splitlines() if status_log.exists() else []
    )
    actual_attempts = (
        attempt_log.read_text(encoding="utf-8").splitlines() if attempt_log.exists() else []
    )
    assert actual_statuses == expected_statuses
    assert actual_attempts == expected_attempts


@pytest.mark.parametrize(
    (
        "later_mode",
        "final_permission",
        "permission_user_id",
        "permission_lookup_fails",
        "fail_first_status",
        "revoke_on_retry",
        "expected_returncode",
        "expected_states",
    ),
    [
        ("replacement", "write", HUMAN_ID, False, False, False, 0, []),
        ("duplicate", "write", HUMAN_ID, False, False, False, 1, ["failure"]),
        ("claimed", "read", HUMAN_ID, False, False, False, 1, ["failure"]),
        ("claimed", "write", 999, False, False, False, 1, ["failure"]),
        ("claimed", "write", HUMAN_ID, True, False, False, 1, ["failure"]),
        (
            "claimed",
            "write",
            HUMAN_ID,
            False,
            True,
            True,
            1,
            ["success", "failure"],
        ),
    ],
)
def test_route_final_publisher_rechecks_ownership_before_success(
    tmp_path: Path,
    later_mode: str,
    final_permission: str,
    permission_user_id: int,
    permission_lookup_fails: bool,
    fail_first_status: bool,
    revoke_on_retry: bool,
    expected_returncode: int,
    expected_states: list[str],
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    shell = _workflow_step_script(workflow, "Publish required status on PR head")
    claimed = {
        "number": 2183,
        "state": "open",
        "body": "review route evidence",
        "updated_at": PR_UPDATED_AT,
        "draft": False,
        "base": {"ref": "main", "repo": {"full_name": "bioedca/Yeliztli"}},
        "head": {"sha": HEAD_SHA.upper()},
    }
    replacement = {**claimed, "number": 2184}
    initial_owners = tmp_path / "initial-owners.json"
    later_owners = tmp_path / "later-owners.json"
    ownership_counter = tmp_path / "ownership-counter"
    pr_fixture = tmp_path / "fixture-pr-final.json"
    main_fixture = tmp_path / "fixture-main-final.json"
    finalizer_fixture = tmp_path / "fixture-finalizer.json"
    permission_fixture = tmp_path / "fixture-finalizer-permission.json"
    revoked_permission_fixture = tmp_path / "fixture-revoked-permission.json"
    permission_counter = tmp_path / "permission-counter"
    status_counter = tmp_path / "status-counter"
    status_log = tmp_path / "statuses.log"
    initial_owners.write_text(json.dumps([[claimed]]), encoding="utf-8")
    later = {
        "claimed": [claimed],
        "duplicate": [claimed, replacement],
        "replacement": [replacement],
    }[later_mode]
    later_owners.write_text(json.dumps([later]), encoding="utf-8")
    pr_fixture.write_text(json.dumps(claimed), encoding="utf-8")
    main_fixture.write_text(json.dumps({"object": {"sha": HEAD_SHA}}), encoding="utf-8")
    finalizer_fixture.write_text(
        json.dumps(
            {
                "data": {
                    "node": {
                        "id": FINALIZER_NODE_ID,
                        "author": {
                            "__typename": "User",
                            "databaseId": HUMAN_ID,
                            "login": "trusted-reviewer",
                        },
                        "authorAssociation": "MEMBER",
                        "body": "/validate-route",
                        "createdAt": CREATED_AT,
                        "updatedAt": CREATED_AT,
                        "lastEditedAt": None,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    permission_fixture.write_text(
        json.dumps({"permission": final_permission, "user": {"id": permission_user_id}}),
        encoding="utf-8",
    )
    revoked_permission_fixture.write_text(
        json.dumps({"permission": "read", "user": {"id": HUMAN_ID}}),
        encoding="utf-8",
    )
    (tmp_path / "pr.json").write_text(json.dumps(claimed), encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env bash
set -eu
case "$*" in
  *"pulls?state=open"*)
    count=0
    if [ -f "$OWNERSHIP_COUNTER" ]; then read -r count < "$OWNERSHIP_COUNTER"; fi
    if [ "$count" = "0" ]; then
      printf '1\n' > "$OWNERSHIP_COUNTER"
      exec /bin/cat "$INITIAL_OWNERS"
    fi
    printf '2\n' > "$OWNERSHIP_COUNTER"
    exec /bin/cat "$LATER_OWNERS"
    ;;
  *"api graphql"*) exec /bin/cat "$FINALIZER_FIXTURE" ;;
  *"collaborators/"*"/permission"*)
    [ "${PERMISSION_LOOKUP_FAILS:-false}" != "true" ] || exit 2
    count=0
    if [ -f "$PERMISSION_COUNTER" ]; then read -r count < "$PERMISSION_COUNTER"; fi
    count=$((count + 1))
    printf '%s\n' "$count" > "$PERMISSION_COUNTER"
    if [ "${REVOKE_ON_RETRY:-false}" = "true" ] && [ "$count" -ge 3 ]; then
      exec /bin/cat "$REVOKED_PERMISSION_FIXTURE"
    fi
    exec /bin/cat "$PERMISSION_FIXTURE"
    ;;
  *"pulls/2183"*) exec /bin/cat "$PR_FIXTURE" ;;
  *"git/ref/heads/main"*) exec /bin/cat "$MAIN_FIXTURE" ;;
  *"statuses/"*)
    printf '%s\n' "$*" >> "$STATUS_LOG"
    count=0
    if [ -f "$STATUS_COUNTER" ]; then read -r count < "$STATUS_COUNTER"; fi
    count=$((count + 1))
    printf '%s\n' "$count" > "$STATUS_COUNTER"
    if [ "${FAIL_FIRST_STATUS:-false}" = "true" ] && [ "$count" = "1" ]; then
      exit 2
    fi
    printf '{}\n'
    ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    completed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", shell],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "FINALIZE_COMMENT_ACTOR_ID": str(HUMAN_ID),
            "FINALIZE_COMMENT_CREATED_AT": CREATED_AT,
            "FINALIZE_COMMENT_NODE_ID": FINALIZER_NODE_ID,
            "FINALIZE_ROUTE": "true",
            "FINALIZER_FIXTURE": str(finalizer_fixture),
            "FAIL_FIRST_STATUS": str(fail_first_status).lower(),
            "GH_TOKEN": "test-token",
            "GITHUB_REPOSITORY": "bioedca/Yeliztli",
            "HEAD_SHA": HEAD_SHA,
            "INITIAL_OWNERS": str(initial_owners),
            "IS_DRAFT": "false",
            "LATER_OWNERS": str(later_owners),
            "MAIN_FIXTURE": str(main_fixture),
            "OWNERSHIP_COUNTER": str(ownership_counter),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PERMISSION_COUNTER": str(permission_counter),
            "PERMISSION_FIXTURE": str(permission_fixture),
            "PERMISSION_LOOKUP_FAILS": str(permission_lookup_fails).lower(),
            "PR_FIXTURE": str(pr_fixture),
            "PR_NUMBER": "2183",
            "PR_UPDATED_AT": PR_UPDATED_AT,
            "RUNNER_TEMP": str(tmp_path),
            "RUN_URL": "https://example.test/run",
            "REVOKED_PERMISSION_FIXTURE": str(revoked_permission_fixture),
            "REVOKE_ON_RETRY": str(revoke_on_retry).lower(),
            "STATUS_COUNTER": str(status_counter),
            "STATUS_LOG": str(status_log),
            "TRUSTED_SHA": HEAD_SHA,
            "VALIDATION_OUTCOME": "success",
            "WORKFLOW_SHA": HEAD_SHA,
        },
        text=True,
    )
    assert completed.returncode == expected_returncode, completed.stderr
    assert ownership_counter.read_text(encoding="utf-8").strip() == "2"
    if not expected_states:
        assert "publication skipped" in completed.stdout
        assert not status_log.exists()
    else:
        status_calls = status_log.read_text(encoding="utf-8").splitlines()
        assert len(status_calls) == len(expected_states)
        assert all(
            f"state={expected_state}" in status_call
            for expected_state, status_call in zip(expected_states, status_calls, strict=True)
        )
        if later_mode == "duplicate":
            assert "Head ownership changed before publication" in status_calls[-1]
        else:
            assert "finalizer" in status_calls[-1].lower()


@pytest.mark.parametrize(
    (
        "snapshot_plan",
        "fail_first_pending",
        "expected_returncode",
        "expected_statuses",
    ),
    [
        ("pre-fail", False, 1, [(HEAD_SHA, "pending")]),
        ("pass", False, 0, [(HEAD_SHA, "success")]),
        (
            "post-fail",
            True,
            1,
            [
                (HEAD_SHA, "success"),
                (HEAD_SHA, "pending"),
                (HEAD_SHA, "pending"),
                ("b" * 40, "pending"),
            ],
        ),
    ],
)
def test_v3_publisher_executes_fresh_validation_and_audit_rollback(
    tmp_path: Path,
    snapshot_plan: str,
    fail_first_pending: bool,
    expected_returncode: int,
    expected_statuses: list[tuple[str, str]],
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    shell = _workflow_step_script(workflow, "Publish required status on PR head")
    claimed = {
        "number": 2183,
        "state": "open",
        "body": "v3 review route evidence",
        "updated_at": PR_UPDATED_AT,
        "draft": False,
        "base": {"ref": "main", "repo": {"full_name": "bioedca/Yeliztli"}},
        "head": {
            "sha": HEAD_SHA.upper(),
            "ref": "issue-2183",
            "repo": {"full_name": "bioedca/Yeliztli"},
        },
    }
    current = deepcopy(claimed)
    current["head"]["sha"] = ("b" * 40).upper()
    owners_fixture = tmp_path / "owners.json"
    pr_fixture = tmp_path / "pr.json"
    current_pr_fixture = tmp_path / "current-pr.json"
    main_fixture = tmp_path / "main.json"
    finalizer_fixture = tmp_path / "finalizer.json"
    permission_fixture = tmp_path / "permission.json"
    pr_counter = tmp_path / "pr-counter"
    snapshot_counter = tmp_path / "snapshot-counter"
    pending_counter = tmp_path / "pending-counter"
    status_log = tmp_path / "statuses.log"
    owners_fixture.write_text(json.dumps([[claimed]]), encoding="utf-8")
    pr_fixture.write_text(json.dumps(claimed), encoding="utf-8")
    current_pr_fixture.write_text(json.dumps(current), encoding="utf-8")
    main_fixture.write_text(json.dumps({"object": {"sha": HEAD_SHA}}), encoding="utf-8")
    finalizer_fixture.write_text(
        json.dumps(
            {
                "data": {
                    "node": {
                        "id": FINALIZER_NODE_ID,
                        "author": {
                            "__typename": "User",
                            "databaseId": HUMAN_ID,
                            "login": "trusted-reviewer",
                        },
                        "authorAssociation": "MEMBER",
                        "body": "/validate-route",
                        "createdAt": CREATED_AT,
                        "updatedAt": CREATED_AT,
                        "lastEditedAt": None,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    permission_fixture.write_text(
        json.dumps({"permission": "write", "user": {"id": HUMAN_ID}}),
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_bash = fake_bin / "bash"
    fake_bash.write_text(
        """#!/bin/bash
set -eu
if [ "${1:-}" = "scripts/validate_review_route_snapshot.sh" ]; then
  count=0
  if [ -f "$SNAPSHOT_COUNTER" ]; then read -r count < "$SNAPSHOT_COUNTER"; fi
  count=$((count + 1))
  printf '%s\n' "$count" > "$SNAPSHOT_COUNTER"
  if [ "$SNAPSHOT_PLAN" = "pre-fail" ] && [ "$count" = "1" ]; then exit 1; fi
  if [ "$SNAPSHOT_PLAN" = "post-fail" ] && [ "$count" = "2" ]; then exit 1; fi
  exit 0
fi
exec /bin/bash "$@"
""",
        encoding="utf-8",
    )
    fake_bash.chmod(0o755)
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env bash
set -eu
case "$*" in
  *"pulls?state=open"*) exec /bin/cat "$OWNERS_FIXTURE" ;;
  *"api graphql"*) exec /bin/cat "$FINALIZER_FIXTURE" ;;
  *"collaborators/"*"/permission"*) exec /bin/cat "$PERMISSION_FIXTURE" ;;
  *"git/ref/heads/main"*) exec /bin/cat "$MAIN_FIXTURE" ;;
  *"pulls/2183"*)
    if [[ " $* " == *" --jq "* ]]; then
      printf '%s\n' "$(printf 'b%.0s' {1..40})"
      exit 0
    fi
    count=0
    if [ -f "$PR_COUNTER" ]; then read -r count < "$PR_COUNTER"; fi
    count=$((count + 1))
    printf '%s\n' "$count" > "$PR_COUNTER"
    if [ "$count" -ge 3 ]; then exec /bin/cat "$CURRENT_PR_FIXTURE"; fi
    exec /bin/cat "$PR_FIXTURE"
    ;;
  *"statuses/"*)
    sha=""
    state=""
    context=""
    for argument in "$@"; do
      case "$argument" in
        repos/*/statuses/*) sha="${argument##*/}" ;;
        state=*) state="${argument#state=}" ;;
        context=*) context="${argument#context=}" ;;
      esac
    done
    printf '%s\t%s\n' "$sha" "$state" >> "$STATUS_LOG"
    if [ "$state" = "pending" ]; then
      count=0
      if [ -f "$PENDING_COUNTER" ]; then read -r count < "$PENDING_COUNTER"; fi
      count=$((count + 1))
      printf '%s\n' "$count" > "$PENDING_COUNTER"
      if [ "$FAIL_FIRST_PENDING" = "true" ] && [ "$count" = "1" ]; then exit 1; fi
    fi
    printf '{"state":"%s","context":"%s"}\n' "$state" "$context"
    ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    fake_sleep = fake_bin / "sleep"
    fake_sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_sleep.chmod(0o755)
    completed = subprocess.run(
        ["/bin/bash", "-e", "-o", "pipefail", "-c", shell],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "CURRENT_PR_FIXTURE": str(current_pr_fixture),
            "FAIL_FIRST_PENDING": str(fail_first_pending).lower(),
            "FINALIZE_COMMENT_ACTOR_ID": str(HUMAN_ID),
            "FINALIZE_COMMENT_CREATED_AT": CREATED_AT,
            "FINALIZE_COMMENT_NODE_ID": FINALIZER_NODE_ID,
            "FINALIZE_ROUTE": "true",
            "FINALIZER_FIXTURE": str(finalizer_fixture),
            "GH_TOKEN": "test-token",
            "GITHUB_REPOSITORY": "bioedca/Yeliztli",
            "HEAD_BRANCH": "issue-2183",
            "HEAD_REPOSITORY": "bioedca/Yeliztli",
            "HEAD_SHA": HEAD_SHA,
            "IS_DRAFT": "false",
            "MAIN_FIXTURE": str(main_fixture),
            "OWNERS_FIXTURE": str(owners_fixture),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PENDING_COUNTER": str(pending_counter),
            "PERMISSION_FIXTURE": str(permission_fixture),
            "PR_COUNTER": str(pr_counter),
            "PR_FIXTURE": str(pr_fixture),
            "PR_NUMBER": "2183",
            "PR_UPDATED_AT": PR_UPDATED_AT,
            "RESOLVED_HEAD_SHA": HEAD_SHA,
            "RUNNER_TEMP": str(tmp_path),
            "RUN_URL": "https://example.test/run",
            "SCHEMA_VERSION": "3",
            "SNAPSHOT_COUNTER": str(snapshot_counter),
            "SNAPSHOT_PLAN": snapshot_plan,
            "STATUS_LOG": str(status_log),
            "TRUSTED_SHA": HEAD_SHA,
            "VALIDATION_OUTCOME": "success",
            "WORKFLOW_SHA": HEAD_SHA,
        },
        text=True,
    )
    assert completed.returncode == expected_returncode, completed.stderr
    actual_statuses = [
        (sha, state)
        for sha, state in (
            line.split("\t") for line in status_log.read_text(encoding="utf-8").splitlines()
        )
    ]
    assert actual_statuses == expected_statuses
    expected_snapshots = 1 if snapshot_plan == "pre-fail" else 2
    assert int(snapshot_counter.read_text(encoding="utf-8")) == expected_snapshots
    if snapshot_plan == "post-fail":
        assert "post-success audit failed closed" in completed.stdout


def test_public_template_contains_only_hosted_review_gates() -> None:
    root = Path(__file__).resolve().parents[2]
    template = (root / ".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
    assert AUTONOMOUS_SCHEMA_MARKER in template
    assert SCHEMA_MARKER not in template
    assert all(f"- [ ] {reviewer}" in template for reviewer in ("Copilot", "Codex", "CodeRabbit"))
    assert COPILOT_GATE in template
    assert CODEX_GATE in template.replace("`", "")
    assert "CodeRabbit structured clean review" in template
    assert CODERABBIT_GATE not in template.replace("`", "")
    assert HUMAN_GATE not in template
    assert "Developer Certificate of Origin" not in template
    assert "Agent claim ID:" in template
    assert "all changed files reviewed and zero generated/attached comments" in template
    assert "zero actionable/attached comments, no ignored files" in template
    assert "empty zero-comment formal approval" in template
    assert "code block or raw HTML" in template
    route_end = template.index("- [ ] Load-bearing")
    provider_start = template.index("- [ ] Copilot", route_end)
    provider_end = template.index("- [ ] CodeRabbit", provider_start)
    table_start = template.index("| Required review gate", provider_end)
    assert "<!--" in template[route_end:provider_start]
    assert "<!--" in template[provider_end:table_start]


def test_contributor_review_routes_match_the_public_template() -> None:
    root = Path(__file__).resolve().parents[2]
    contributing = (root / "CONTRIBUTING.md").read_text(encoding="utf-8")
    normalized = " ".join(contributing.split())
    assert all(route in contributing for route in ("Low", "Standard", "Load-bearing"))
    assert "New pull requests use `review-route-schema:v3`" in contributing
    assert "Existing v2 pull requests keep their human-gated contract" in normalized
    assert "V3 trusts only a small provider-authenticated terminal envelope" in normalized
    assert "selected-file count equal to GitHub's changed-file count" in normalized
    assert "select another provider instead of guessing" in normalized
    assert "cannot modify the PR-controlled review-signal workflow" in normalized
    assert "/validate-route" in contributing
    assert "audits the result once more" in normalized
    assert "only invalidate affected heads to `pending`" in normalized
    assert "never check out PR code or consume PR artifacts or caches" in normalized
    assert "V2 retains its existing requirements" in normalized
    assert "V3 does not require DCO, independent human approval" in normalized
    assert "never `github-actions[bot]`" in normalized


def test_governance_and_docs_describe_versioned_provenance() -> None:
    root = Path(__file__).resolve().parents[2]
    governance = (root / "GOVERNANCE.md").read_text(encoding="utf-8")
    docs = (root / "docs/develop/contributing.md").read_text(encoding="utf-8")
    normalized = " ".join(governance.split())
    assert "legacy v2 change still needs maintainer approval" in normalized
    assert "v3 change may be squash-merged without per-PR human authorization" in normalized
    assert "without a legal or DCO attestation" in normalized
    normalized_docs = " ".join(docs.replace("\n>", "\n").split())
    assert "versioned review routes, and contribution provenance" in normalized_docs


def test_graphql_context_tracks_comment_association_and_head_epoch() -> None:
    root = Path(__file__).resolve().parents[2]
    query = (root / "scripts/review_route_context.graphql").read_text(encoding="utf-8")
    review_query = (root / "scripts/review_route_reviews.graphql").read_text(encoding="utf-8")
    assert query.count("authorAssociation") == 4
    assert "$headOid: GitObjectID!" in query
    assert "$includeCodeRabbitLedger: Boolean!" in query
    assert "codexHeadObject: object(oid: $headOid)" in query
    assert "abbreviatedOid" in query
    review_block = query.split("reviews(last: 100)", maxsplit=1)[1].split(
        "latestHumanOpinions:", maxsplit=1
    )[0]
    assert "\n          id\n" in review_block
    assert query.count("updatedAt") == 4
    assert "\n          id\n          databaseId\n          author" in query
    assert query.count("lastEditedAt") == 3
    assert "commit { oid }\n          lastEditedAt\n          state" in query
    assert "latestHumanOpinions: latestOpinionatedReviews(first: 100, writersOnly: true)" in query
    assert query.count("orderBy: {field: UPDATED_AT, direction: ASC}") == 2
    assert "HEAD_REF_FORCE_PUSHED_EVENT" in query
    assert "createdAt" in query
    assert 'openPullRequests: pullRequests(first: 100, states: OPEN, baseRefName: "main")' in query
    assert ") @include(if: $includeCodeRabbitLedger) {" in query
    assert "headRefOid\n        number" in query
    assert "$endCursor: String" in review_query
    assert "reviews(first: 100, after: $endCursor)" in review_query
    assert "pageInfo {\n          hasNextPage\n          endCursor" in review_query
    assert "\n          id\n" in review_query
    assert "\n            __typename\n            login\n" in review_query
    assert "... on User { databaseId }" in review_query
    assert "... on Bot { databaseId }" in review_query
    assert "\n          body\n" in review_query
    assert query.count("comments { totalCount }") == 1
    assert "comments { totalCount }" in review_query
    assert all(
        field in review_query
        for field in (
            "authorAssociation",
            "commit { oid }",
            "lastEditedAt",
            "state",
            "submittedAt",
        )
    )
