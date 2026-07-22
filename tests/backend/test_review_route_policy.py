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
    BOT_ACTOR_IDS,
    CODERABBIT_COMPLETION_MARKER,
    CODERABBIT_GATE,
    CODEX_GATE,
    COPILOT_GATE,
    GATES,
    HUMAN_GATE,
    LEGACY_SCHEMA_MARKER,
    SCHEMA_MARKER,
    ChangedFile,
    minimum_route,
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
) -> dict[str, object]:
    return {
        "author": _actor(database_id, typename),
        "authorAssociation": association,
        "body": body,
        "commit": {"oid": head_sha},
        "state": state,
        "submittedAt": submitted_at,
    }


def _comment(
    database_id: int,
    body: str,
    created_at: str,
    *,
    association: str = "COLLABORATOR",
    updated_at: str | None = None,
    comment_id: int | None = None,
) -> dict[str, object]:
    if comment_id is None:
        comment_id = int(
            "".join(character for character in created_at if character.isdigit())[-9:]
        )
    return {
        "id": f"IC_kwDOTest{comment_id}",
        "databaseId": comment_id,
        "author": _actor(database_id, "User"),
        "authorAssociation": association,
        "body": body,
        "createdAt": created_at,
        "lastEditedAt": None,
        "updatedAt": updated_at or created_at,
    }


def _body(
    route: str,
    *,
    complete: bool = True,
    automated_gates: set[str] | None = None,
) -> str:
    selected = {
        name: "x" if name == route else " " for name in ("Low", "Standard", "Load-bearing")
    }
    selected_bots = {DEFAULT_AUTOMATED_GATE[route]} if automated_gates is None else automated_gates
    required = {*selected_bots, HUMAN_GATE}
    reviewer_labels = {
        COPILOT_GATE: "Copilot",
        CODEX_GATE: "Codex",
        CODERABBIT_GATE: "CodeRabbit",
    }
    rows = []
    for gate in GATES:
        if not complete:
            head, status = "", ""
        elif gate in required:
            head = HEAD_SHA
            result = "APPROVED" if gate == HUMAN_GATE else "COMPLETE"
            status = f"{GATE_TIMES[gate]} — {result}"
        else:
            head, status = "N/A", "N/A"
        rows.append(f"| {gate} | scope | {head} | {status} |")
    return "\n".join(
        [
            "## Review route",
            SCHEMA_MARKER,
            f"- [{selected['Low']}] Low — docs",
            f"- [{selected['Standard']}] Standard — code",
            f"- [{selected['Load-bearing']}] Load-bearing — governance",
            *[
                f"- [{'x' if gate in selected_bots else ' '}] {label} — automated review"
                for gate, label in reviewer_labels.items()
            ],
            (
                "| Required review gate | Applies to | Head SHA or N/A "
                "| UTC time and status, or N/A |"
            ),
            "| --- | --- | --- | --- |",
            *rows,
            "## Legal",
        ]
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
        _review(BOT_ACTOR_IDS[COPILOT_GATE], GATE_TIMES[COPILOT_GATE]),
        _review(BOT_ACTOR_IDS[CODEX_GATE], GATE_TIMES[CODEX_GATE]),
        _review(
            BOT_ACTOR_IDS[CODERABBIT_GATE],
            GATE_TIMES[CODERABBIT_GATE],
            body=(f"Review completed. <!-- This is an {CODERABBIT_COMPLETION_MARKER} -->"),
        ),
        human_review,
    ]
    comments = []
    if CODERABBIT_GATE in selected_bots:
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
            else _body(route, complete=complete, automated_gates=selected_bots)
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
    assert "manual CodeRabbit requests require selecting the CodeRabbit lane" in errors


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


@pytest.mark.parametrize(
    ("route", "path"),
    [
        ("Low", "docs/typo-fix.md"),
        ("Standard", "frontend/src/hooks/useDialogFocus.ts"),
        ("Load-bearing", "README.md"),
    ],
)
def test_v1_route_bodies_keep_the_legacy_gate_matrix(route: str, path: str) -> None:
    files = [ChangedFile(path)]
    context = _context(route, files, body=_legacy_body(route))
    assert validate_context(context, files, now=NOW) == []


def test_v1_standard_still_requires_copilot_and_codex() -> None:
    files = [ChangedFile("frontend/src/hooks/useDialogFocus.ts")]
    context = _context("Standard", files, body=_legacy_body("Standard"))
    reviews = context["data"]["repository"]["pullRequest"]["reviews"]
    reviews["nodes"] = [
        review
        for review in reviews["nodes"]
        if review["author"]["databaseId"] != BOT_ACTOR_IDS[COPILOT_GATE]
    ]
    reviews["totalCount"] = len(reviews["nodes"])
    errors = validate_context(context, files, now=NOW)
    assert f"no verified current-head GitHub activity for: {COPILOT_GATE}" in errors


def test_v1_load_bearing_coderabbit_protocol_still_follows_codex() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files, body=_legacy_body("Load-bearing"))
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
    errors = validate_context(context, files, now=NOW)
    assert "CodeRabbit needs a current-SHA reservation, trigger, then completed review" in errors


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
    ("path", "route"),
    [
        ("README.md", "Load-bearing"),
        ("frontend/src/hooks/useDialogFocus.ts", "Standard"),
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


def test_valid_immutable_finalizer_after_all_gates_succeeds() -> None:
    files = [ChangedFile("README.md")]
    context = _context("Load-bearing", files)
    _add_finalizer(context)
    assert validate_context(context, files, now=NOW, **_finalizer_kwargs()) == []


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


@pytest.mark.parametrize("gate", [COPILOT_GATE, CODEX_GATE])
def test_latest_same_second_terminal_and_nonterminal_bot_reviews_fail_closed(
    gate: str,
) -> None:
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


def test_old_coderabbit_trigger_does_not_break_new_head_sequence() -> None:
    files = [ChangedFile(".github/workflows/review-route.yml")]
    context = _context("Load-bearing", files)
    repository = context["data"]["repository"]
    comments = repository["pullRequest"]["comments"]
    comments["nodes"].insert(
        0, _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T11:55:00Z")
    )
    comments["totalCount"] += 1
    repository["recentPullRequests"]["nodes"][0]["comments"]["totalCount"] += 1
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
    assert any(
        error.startswith("CodeRabbit") for error in validate_context(context, files, now=NOW)
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
    "comments",
    [
        [
            _comment(
                AUTHOR_ID,
                f"coderabbit-reservation: {HEAD_SHA}\n@coderabbitai full review",
                "2026-07-21T12:25:00Z",
            )
        ],
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
        [
            _comment(AUTHOR_ID, f"coderabbit-reservation: {HEAD_SHA}", "2026-07-21T12:25:00Z"),
            _comment(
                AUTHOR_ID,
                "Please run @coderabbitai full review",
                "2026-07-21T12:26:00Z",
            ),
        ],
        [
            _comment(
                AUTHOR_ID,
                f"coderabbit-reservation: {HEAD_SHA}",
                "2026-07-21T12:01:00Z",
                updated_at="2026-07-21T12:25:00Z",
            ),
            _comment(AUTHOR_ID, "@coderabbitai full review", "2026-07-21T12:26:00Z"),
        ],
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
    ],
)
def test_coderabbit_rejects_combined_untrusted_or_quoted_commands(
    comments: list[dict[str, object]],
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
    assert any(error.startswith("CodeRabbit") for error in errors)


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
    assert "pull_request_target:" in workflow
    assert "pull_request_review:" not in workflow
    assert "pull_request_review_comment:" not in workflow
    assert "issue_comment:" in workflow
    assert "workflow_run:" in workflow
    assert 'workflows: ["Review Route Invalidation Signal"]' in workflow
    assert "merge_group:" not in workflow
    assert "Merge queue is intentionally unsupported" in workflow
    assert "types: [created, edited, deleted]" in workflow
    assert "review_requested" in workflow and "review_request_removed" in workflow
    assert "closed" in workflow.split("types:", maxsplit=1)[1].split("\n", maxsplit=1)[0]
    assert "github.event.comment.author_association" in workflow
    assert "environment:\n      name: review-route-publisher" in workflow
    assert "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1" in workflow
    assert "REVIEW_ROUTE_APP_CLIENT_ID" in workflow
    assert "REVIEW_ROUTE_APP_PRIVATE_KEY" in workflow
    assert "permission-statuses: write" in workflow
    assert "permission-contents: read" in workflow
    assert "statuses: write" not in workflow.split("jobs:", maxsplit=1)[0]
    assert workflow.count("GH_TOKEN: ${{ github.token }}") == 2
    privileged_jobs = workflow.split("  invalidate-review-state:", maxsplit=1)[1]
    assert "GH_TOKEN: ${{ github.token }}" not in privileged_jobs
    assert workflow.count("steps.publisher.outputs.token") == 3
    assert "github.event.comment.body == '/validate-route'" in workflow
    assert "FINALIZE_COMMENT_NODE_ID: ${{ github.event.comment.node_id }}" in workflow
    assert "FINALIZE_COMMENT_CREATED_AT: ${{ github.event.comment.created_at }}" in workflow
    assert "FINALIZE_COMMENT_ACTOR_ID: ${{ github.event.comment.user.id }}" in workflow
    assert "WORKFLOW_SHA: ${{ github.workflow_sha }}" in workflow
    assert '[ "$FINALIZE_ROUTE" = "true" ]' in workflow
    assert '"$GITHUB_EVENT_PATH" "$RUNNER_TEMP/pr.json"' in workflow
    assert "Route is complete; a maintainer must comment /validate-route." in workflow
    assert "concurrency:" in workflow.split("jobs:", maxsplit=1)[1]
    assert "ref: ${{ env.TRUSTED_SHA }}" in workflow
    assert "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803" in workflow
    assert workflow.count('.base.ref == "main"') == 5
    assert workflow.count(".base.repo.full_name == $repo") == 5
    assert workflow.count("git/ref/heads/main") == 2
    assert "'.object.sha == $trusted'" in workflow
    assert ".base.sha" not in workflow
    assert "-f state=pending" in workflow
    assert "EVENT_HEAD_SHA: ${{ github.event.pull_request.head.sha }}" in workflow
    validate_job = workflow.split("\n  validate:", maxsplit=1)[1]
    assert validate_job.index('post_pending "$EVENT_HEAD_SHA"') < validate_job.index(
        "pulls/$PR_NUMBER"
    )
    assert validate_job.index('post_pending "$head_sha"') < validate_job.index(
        "git/ref/heads/main"
    )
    assert validate_job.index('post_pending "$head_sha"') < validate_job.index(
        "$GITHUB_EVENT_PATH"
    )
    assert "^[0-9a-fA-F]{40}$" in workflow
    assert workflow.count("for attempt in 1 2 3") == 3
    assert '--expected-head "$HEAD_SHA"' in workflow
    assert '--expected-draft "$IS_DRAFT"' in workflow
    assert '--expected-pr-updated-at "$PR_UPDATED_AT"' in workflow
    assert '--finalize-comment-node-id "$FINALIZE_COMMENT_NODE_ID"' in workflow
    assert '--finalize-comment-created-at "$FINALIZE_COMMENT_CREATED_AT"' in workflow
    assert '--finalize-comment-actor-id "$FINALIZE_COMMENT_ACTOR_ID"' in workflow
    assert 'if .draft == true then "true" elif .draft == false then "false"' in workflow
    assert '-F node="$FINALIZE_COMMENT_NODE_ID"' in workflow
    assert ".data.node.lastEditedAt == null" in workflow
    assert ".[0].body == .[1].body" in workflow
    assert ".[0].updated_at == $updated" in workflow
    assert ".[1].updated_at == $updated" in workflow
    assert "always() && !cancelled()" in workflow
    assert "pulls?state=open&base=main&per_page=100" in workflow
    assert "($matches | length) == 1" in workflow
    assert '[ "$head_unique" = "true" ]' in workflow
    assert workflow.index('> "$RUNNER_TEMP/open-pulls-final.json"') < workflow.index(
        "state=success"
    )
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


def test_review_state_signal_is_credential_free_and_only_drives_pending() -> None:
    root = Path(__file__).resolve().parents[2]
    signal = (root / ".github/workflows/review-route-invalidation.yml").read_text(encoding="utf-8")
    publisher = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    assert (
        "run-name: review-route-pr-${{ github.event.pull_request.number }}-head-"
        "${{ github.event.pull_request.head.sha }}" in signal
    )
    assert "pull_request_review:" in signal
    assert "types: [submitted, edited, dismissed]" in signal
    assert "pull_request_review_comment:" in signal
    assert "types: [created, edited, deleted]" in signal
    assert "pull_request_review_thread:" not in signal
    assert "permissions: {}" in signal
    assert "ACTOR_ASSOCIATION" in signal
    assert "ACTOR_ID" in signal
    assert "EVENT_NAME: ${{ github.event_name }}" in signal
    assert "OWNER|MEMBER|COLLABORATOR" in signal
    assert "175728472|199175422|136622811" in signal
    outsider_comment_path = 'if [ "$EVENT_NAME" = "pull_request_review_comment" ]; then exit 0; fi'
    assert outsider_comment_path in signal
    assert signal.index(outsider_comment_path) < signal.index('case "$ACTOR_ASSOCIATION"')
    assert "Every\n# diff-comment actor emits this credential-free signal" in signal
    assert "Native required conversation resolution" in signal
    assert "actions/checkout" not in signal
    assert "secrets." not in signal
    assert "create-github-app-token" not in signal
    invalidator = publisher.split("  invalidate-review-state:", maxsplit=1)[1].split(
        "\n  validate:", maxsplit=1
    )[0]
    resolver = publisher.split("  resolve_review_state:", maxsplit=1)[1].split(
        "\n  resolve_route_event:", maxsplit=1
    )[0]
    assert "github.event_name == 'workflow_run'" in resolver
    assert "github.event.workflow_run.conclusion == 'success'" in resolver
    assert (
        "github.event.workflow_run.path == "
        "'.github/workflows/review-route-invalidation.yml'" in resolver
    )
    assert 'fromJSON(\'["pull_request_review","pull_request_review_comment"]\')' in resolver
    assert "workflow_run.pull_requests" not in resolver
    assert "any(.workflow_run.pull_requests[]?;" not in resolver
    assert "SOURCE_HEAD_SHA: ${{ github.event.workflow_run.head_sha }}" in resolver
    assert '[ "$signal_head" != "$source_head" ]' in resolver
    assert "(.head.sha | ascii_downcase) == $head" in resolver
    assert "($matches | length) == 1" in resolver
    assert "environment:\n      name: review-route-publisher" in invalidator
    assert "steps.invalidator.outputs.token" in invalidator
    assert "group: review-route-${{ needs.resolve_review_state.outputs.head_sha }}" in invalidator
    validate_job = publisher.split("\n  validate:", maxsplit=1)[1]
    assert "group: review-route-${{ needs.resolve_route_event.outputs.head_sha }}" in validate_job
    assert "^review-route-pr-([0-9]+)-head-([0-9a-fA-F]{40})$" in resolver
    assert invalidator.index('post_pending "$head_sha"') < invalidator.index(
        'post_pending "$EXPECTED_HEAD_SHA"'
    )
    assert 'if ! post_pending "$SIGNAL_HEAD_SHA"' in invalidator
    assert "could not invalidate stale signal head" in invalidator
    assert "PR head changed after review activity; revalidate the route." in invalidator
    assert "stale prior-head invalidation ignored" not in invalidator
    assert "stale pre-success invalidation ignored" not in invalidator
    assert "-f state=pending" in invalidator
    assert "state=success" not in invalidator
    assert "actions/checkout" not in invalidator


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
            "jq() { return 0; }",
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


@pytest.mark.parametrize(
    ("event_name", "association", "actor_id", "expected"),
    [
        ("pull_request_review_comment", "NONE", "999", 0),
        ("pull_request_review", "NONE", "999", 1),
        ("pull_request_review", "COLLABORATOR", "999", 0),
        ("pull_request_review", "NONE", "175728472", 0),
    ],
)
def test_review_signal_executes_actor_and_event_matrix(
    event_name: str,
    association: str,
    actor_id: str,
    expected: int,
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route-invalidation.yml").read_text(
        encoding="utf-8"
    )
    shell = _workflow_step_script(workflow, "Emit trusted review-state signal")
    completed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", shell],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "ACTOR_ASSOCIATION": association,
            "ACTOR_ID": actor_id,
            "EVENT_NAME": event_name,
        },
        text=True,
    )
    assert completed.returncode == expected


@pytest.mark.parametrize(
    ("source_head", "duplicate_head", "expected"),
    [(HEAD_SHA, False, 0), ("b" * 40, False, 1), (HEAD_SHA, True, 1)],
)
def test_review_signal_resolver_executes_fork_safe_live_binding(
    tmp_path: Path,
    source_head: str,
    duplicate_head: bool,
    expected: int,
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/review-route.yml").read_text(encoding="utf-8")
    shell = _workflow_step_script(workflow, "Bind signal to its associated pull request")
    pull = {
        "number": 2183,
        "state": "open",
        "base": {"ref": "main", "repo": {"full_name": "bioedca/Yeliztli"}},
        "head": {"sha": HEAD_SHA},
    }
    open_pulls = [pull]
    if duplicate_head:
        open_pulls.append({**pull, "number": 2184})
    pr_fixture = tmp_path / "fixture-pr.json"
    open_fixture = tmp_path / "open-prs.json"
    event_fixture = tmp_path / "event.json"
    output = tmp_path / "output"
    pr_fixture.write_text(json.dumps(pull), encoding="utf-8")
    open_fixture.write_text(json.dumps([open_pulls]), encoding="utf-8")
    event_fixture.write_text(json.dumps({"workflow_run": {"pull_requests": []}}), encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env bash
set -eu
case "$*" in
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
            "PR_FIXTURE": str(pr_fixture),
            "RUNNER_TEMP": str(tmp_path),
            "SIGNAL_TITLE": f"review-route-pr-2183-head-{HEAD_SHA}",
            "SOURCE_HEAD_SHA": source_head,
        },
        text=True,
    )
    assert completed.returncode == expected, completed.stderr
    if expected == 0:
        assert output.read_text(encoding="utf-8").splitlines() == [
            f"head_sha={HEAD_SHA}",
            "pr_number=2183",
            f"signal_head={HEAD_SHA}",
        ]


def test_public_template_contains_only_hosted_review_gates() -> None:
    root = Path(__file__).resolve().parents[2]
    template = (root / ".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
    assert SCHEMA_MARKER in template
    assert all(f"- [ ] {reviewer}" in template for reviewer in ("Copilot", "Codex", "CodeRabbit"))
    assert all(gate in template.replace("`", "") for gate in GATES)


def test_contributor_review_routes_match_the_public_template() -> None:
    root = Path(__file__).resolve().parents[2]
    contributing = (root / "CONTRIBUTING.md").read_text(encoding="utf-8")
    normalized = " ".join(contributing.split())
    assert all(route in contributing for route in ("Low", "Standard", "Load-bearing"))
    assert "selects exactly one formal automated GitHub review" in normalized
    assert "providers are substitutes, not a mandatory sequence" in normalized
    assert "five" in contributing and "rolling-hour" in contributing
    assert "/validate-route" in contributing
    assert "two open `main` PRs must never share a head SHA" in normalized
    assert "failed, skipped, or cancelled trusted/relevant signal or publisher run" in normalized
    assert (
        "Diff-comment mutations from every actor, plus trusted formal-review mutations"
        in normalized
    )
    assert "outsider diff comment always invalidates" in normalized
    assert "Native required conversation resolution is therefore the authoritative" in normalized
    assert (
        "conversation-resolution rule must remain enabled whenever `Review Route` is required"
        in normalized
    )
    assert "require the branch to be up to date before merging" in normalized


def test_graphql_context_tracks_comment_association_and_head_epoch() -> None:
    root = Path(__file__).resolve().parents[2]
    query = (root / "scripts/review_route_context.graphql").read_text(encoding="utf-8")
    assert query.count("authorAssociation") == 4
    assert query.count("updatedAt") == 4
    assert "\n          id\n          author" in query
    assert query.count("lastEditedAt") == 2
    assert "latestHumanOpinions: latestOpinionatedReviews(first: 100, writersOnly: true)" in query
    assert query.count("orderBy: {field: UPDATED_AT, direction: ASC}") == 2
    assert "HEAD_REF_FORCE_PUSHED_EVENT" in query
    assert "createdAt" in query
    assert 'openPullRequests: pullRequests(first: 100, states: OPEN, baseRefName: "main")' in query
    assert "headRefOid\n        number" in query
