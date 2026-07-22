#!/usr/bin/env python3
"""Validate a PR's declared route against live, trusted GitHub review state."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

LEGACY_SCHEMA_MARKER = "<!-- review-route-schema:v1 -->"
SCHEMA_MARKER = "<!-- review-route-schema:v2 -->"
ROUTE_RANK = {"Low": 0, "Standard": 1, "Load-bearing": 2}
COPILOT_GATE = "Copilot PR review"
CODEX_GATE = "Codex @codex review"
CODERABBIT_GATE = "Manual CodeRabbit reservation and @coderabbitai full review"
HUMAN_GATE = "Independent human maintainer review"
BOT_GATES = (COPILOT_GATE, CODEX_GATE, CODERABBIT_GATE)
GATES = (*BOT_GATES, HUMAN_GATE)
AUTOMATED_REVIEW_CHOICES = {
    "Copilot": COPILOT_GATE,
    "Codex": CODEX_GATE,
    "CodeRabbit": CODERABBIT_GATE,
}
BOT_ACTOR_IDS = {
    COPILOT_GATE: 175728472,  # Copilot GitHub App
    CODEX_GATE: 199175422,  # ChatGPT Codex connector GitHub App
    CODERABBIT_GATE: 136622811,  # CodeRabbit GitHub App
}
LOAD_BEARING_EXACT = {
    ".coderabbit.yaml",
    ".gitattributes",
    ".gitignore",
    ".gitmodules",
    ".graphifyignore",
    ".github/CODEOWNERS",
    ".github/copilot-instructions.md",
    ".github/dependabot.yml",
    ".github/PULL_REQUEST_TEMPLATE.md",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CITATION.cff",
    "CONTRIBUTING.md",
    "Dockerfile",
    "GOVERNANCE.md",
    "LICENSE",
    "Makefile",
    "NOTICE",
    "README.md",
    "SECURITY.md",
    "SUPPORT.md",
    "alembic.ini",
    "backend/config.py",
    "backend/__init__.py",
    "backend/disclaimers.py",
    "backend/logging_config.py",
    "backend/main.py",
    "docs/bundle-release-runbook.md",
    "docs/assets/img/dashboard.png",
    "docs/attribution.md",
    "docs/external-inputs-strategy.md",
    "docs/index.md",
    "docs/internal/sex_inference_threshold_validation.md",
    "docs/lai-bundle-release-runbook-env.lock.yaml",
    "docs/lai-bundle-release-runbook.md",
    "frontend/src/App.tsx",
    "frontend/.gitignore",
    "frontend/index.html",
    "frontend/knip.jsonc",
    "frontend/src/main.tsx",
    "mkdocs.yml",
    "package-lock.json",
    "package.json",
    "playwright.config.ts",
    "pyproject.toml",
    "tests/conftest.py",
    "tests/backend/test_review_route_policy.py",
    "vulture_whitelist.py",
}
LOAD_BEARING_NAMES = {
    ".coveragerc",
    ".npmrc",
    ".nvmrc",
    ".pre-commit-config.yaml",
    ".pre-commit-config.yml",
    ".python-version",
    ".yarnrc",
    ".yarnrc.yml",
    "codeowners",
    "pipfile",
    "pipfile.lock",
    "conda-lock.yaml",
    "conda-lock.yml",
    "environment.yaml",
    "environment.yml",
    "mypy.ini",
    "noxfile.py",
    "poetry.lock",
    "pytest.ini",
    "renovate.json",
    "ruff.toml",
    "setup.cfg",
    "setup.py",
    "tox.ini",
    "uv.lock",
}
LOAD_BEARING_PREFIXES = (
    ".github/",
    "alembic/",
    "bundles/",
    "data/",
    "scripts/",
    "backend/analysis/",
    "backend/annotation/",
    "backend/api/",
    "backend/auth",
    "backend/data/",
    "backend/db/",
    "backend/ingestion/",
    "backend/installer",
    "backend/models/",
    "backend/query/",
    "backend/reports/",
    "backend/security",
    "backend/services/",
    "backend/tasks/",
    "backend/updater",
    "backend/utils/",
    "docs/_includes/",
    "docs/ancestry-methods",
    "docs/develop/",
    "docs/features/",
    "docs/getting-started/",
    "docs/install/",
    "docs/internal/",
    "docs/intended-use",
    "docs/maintainer/",
    "docs/modules/",
    "docs/privacy",
    "docs/release-notes/",
    "docs/science",
    # Product pages, rendered result components, result contracts, and their
    # tests can change scientific/clinical meaning even when the filename is
    # generic. Keep these structural surfaces fail-closed; routine hook and
    # styling changes outside them remain eligible for Standard.
    "frontend/scripts/",
    "frontend/src/api/",
    "frontend/src/components/",
    "frontend/src/constants/",
    "frontend/src/lib/",
    "frontend/src/pages/",
    "frontend/src/test/",
    "frontend/src/types/",
    "launchd/",
    "systemd/",
    "tests/backend/",
    "tests/e2e/",
    "tests/fixtures/",
    "tests/manual/",
)
LOAD_BEARING_TEST_TOKENS = (
    "acmg",
    "allele",
    "annotation",
    "ancestry",
    "apoe",
    "auth",
    "backup",
    "bundle",
    "cancer",
    "cadd",
    "carrier",
    "cardiovascular",
    "citation",
    "clinical",
    "clinvar",
    "config",
    "cpic",
    "database",
    "delete",
    "disclaimer",
    "ebmd",
    "evidence",
    "export",
    "finding",
    "gate",
    "gene",
    "genomic",
    "genome",
    "gnomad",
    "gwas",
    "haplogroup",
    "hgvs",
    "hla",
    "hpo",
    "imputation",
    "insilico",
    "ingestion",
    "install",
    "kinship",
    "liftover",
    "logging",
    "merge",
    "metabolic",
    "methylation",
    "migration",
    "nuclear",
    "nutrigenomics",
    "panel",
    "pathogenic",
    "parkinsons",
    "pathway",
    "pgx",
    "pharmacogen",
    "privacy",
    "provenance",
    "prs",
    "query",
    "qc",
    "reference",
    "release",
    "report",
    "revel",
    "restore",
    "risk",
    "roh",
    "sample",
    "schema",
    "security",
    "setup",
    "sex",
    "splice",
    "sql",
    "stale",
    "update",
    "updater",
    "upload",
    "variant",
    "workflow",
    "zygosity",
)
LOW_SUFFIXES = (".md", ".rst", ".txt", ".adoc")
UTC_RESULT = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+[—-]\s+"
    r"(?P<result>COMPLETE|APPROVED)$"
)
TERMINAL_REVIEW_STATES = {"APPROVED", "COMMENTED"}
HUMAN_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
CODEX_CLEAN_COMPLETION_MARKER = (
    "Codex Review: Didn't find any major issues. What shall we delve into next?"
)
CODEX_REVIEWED_COMMIT_LINE = re.compile(
    r"(?m)^\*\*Reviewed commit:\*\* `(?P<sha>[0-9a-f]{10})`\s*$"
)
CODEX_TRIGGER = "@codex review"
CODERABBIT_COMPLETION_MARKER = "auto-generated comment by CodeRabbit for review status"
HUMAN_OPINION_STATES = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
FINALIZE_COMMAND = "/validate-route"


@dataclass(frozen=True)
class Evidence:
    head: str
    status: str


@dataclass(frozen=True)
class ChangedFile:
    filename: str
    previous_filename: str | None = None


def _parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _visible_markdown(body: str) -> str:
    body = re.sub(r"```.*?```|~~~.*?~~~", "", body, flags=re.DOTALL)
    return re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)


def _normalise_gate(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("`", "").strip())


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _parse_route_section(
    body: str,
) -> tuple[str | None, int | None, list[str], dict[str, Evidence], list[str]]:
    errors: list[str] = []
    marker_pattern = re.compile(
        r"(?mi)^## Review route\s*\n"
        r"<!-- review-route-schema:v(?P<version>[12]) -->\s*$"
    )
    marker_matches = list(marker_pattern.finditer(body))
    if len(marker_matches) != 1:
        errors.append(
            "a supported review-route schema marker must appear once directly below its heading"
        )
    schema_version = int(marker_matches[0].group("version")) if len(marker_matches) == 1 else None

    visible = _visible_markdown(body)
    headings = list(re.finditer(r"(?mi)^## Review route\s*$", visible))
    if len(headings) != 1:
        errors.append("expected exactly one '## Review route' heading")
        return None, schema_version, [], {}, errors
    start = headings[0].end()
    following = re.search(r"(?mi)^##\s+", visible[start:])
    section = visible[start : start + following.start()] if following else visible[start:]

    route_rows = re.findall(r"(?m)^\s*-\s*\[([ xX])\]\s*(Low|Standard|Load-bearing)\b", section)
    counts = {name: 0 for name in ROUTE_RANK}
    selected: list[str] = []
    for mark, name in route_rows:
        counts[name] += 1
        if mark.lower() == "x":
            selected.append(name)
    if any(count != 1 for count in counts.values()):
        errors.append("expected each canonical route checkbox exactly once")
    if len(selected) != 1:
        errors.append("select exactly one review route")
    route = selected[0] if len(selected) == 1 else None

    selected_bots: list[str] = []
    if schema_version == 2:
        reviewer_rows = re.findall(
            r"(?m)^\s*-\s*\[([ xX])\]\s*(Copilot|Codex|CodeRabbit)\b",
            section,
        )
        reviewer_counts = {name: 0 for name in AUTOMATED_REVIEW_CHOICES}
        for mark, name in reviewer_rows:
            reviewer_counts[name] += 1
            if mark.lower() == "x":
                selected_bots.append(AUTOMATED_REVIEW_CHOICES[name])
        if any(count != 1 for count in reviewer_counts.values()):
            errors.append("expected each automated reviewer checkbox exactly once")

    expected = set(GATES)
    evidence: dict[str, Evidence] = {}
    unknown: list[str] = []
    for line in section.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = _table_cells(line)
        if len(cells) != 4 or cells[0] in {"Required review gate", "---"}:
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        gate = _normalise_gate(cells[0])
        if gate not in expected:
            unknown.append(gate)
        elif gate in evidence:
            errors.append(f"duplicate review evidence row: {gate}")
        else:
            evidence[gate] = Evidence(head=cells[2], status=cells[3])
    missing = expected - evidence.keys()
    if missing:
        errors.append("missing review evidence rows: " + ", ".join(sorted(missing)))
    if unknown:
        errors.append("unknown review evidence rows: " + ", ".join(sorted(unknown)))
    return route, schema_version, selected_bots, evidence, errors


def needs_coderabbit_ledger(body: str) -> bool:
    """Return whether a valid v2 route selected the global CodeRabbit ledger."""
    _, schema_version, selected_bots, _, errors = _parse_route_section(body)
    return not errors and schema_version == 2 and selected_bots == [CODERABBIT_GATE]


def _is_load_bearing(path: str) -> bool:
    lowered = path.lower()
    name = Path(path).name.lower()
    return (
        path in LOAD_BEARING_EXACT
        or path.startswith(LOAD_BEARING_PREFIXES)
        or name in LOAD_BEARING_NAMES
        or name.startswith(".env")
        or name.startswith("constraints")
        or name.startswith("requirements")
        or name.startswith("dockerfile")
        or name.startswith("docker-compose")
        or name == "pyproject.toml"
        or name.endswith(("package-lock.json", "package.json", ".lock"))
        or (
            lowered.startswith(("tests/", "frontend/"))
            and any(token in lowered for token in LOAD_BEARING_TEST_TOKENS)
        )
    )


def minimum_route(files: list[ChangedFile]) -> str:
    paths = [path for item in files for path in (item.filename, item.previous_filename) if path]
    if not paths:
        raise ValueError("changed-file list is empty")
    if any(_is_load_bearing(path) for path in paths):
        return "Load-bearing"
    if all(path.endswith(LOW_SUFFIXES) for path in paths):
        return "Low"
    return "Standard"


def _actor_id(node: dict[str, Any]) -> int | None:
    author = node.get("author")
    return author.get("databaseId") if isinstance(author, dict) else None


def _connection_truncated_since(
    connection: dict[str, Any],
    *,
    time_key: str,
    since: datetime,
    inclusive: bool = False,
) -> bool:
    nodes = connection.get("nodes") or []
    if connection.get("totalCount", 0) <= len(nodes):
        return False
    if not nodes:
        return True
    times = [_parse_utc(node[time_key]) for node in nodes if node.get(time_key)]
    if not times:
        return True
    oldest = min(times)
    return oldest >= since if inclusive else oldest > since


def _bot_activity(
    pull_request: dict[str, Any],
    gate: str,
    actor_id: int,
    head_sha: str,
    head_epoch: datetime,
    codex_head_safe: bool = False,
) -> datetime | None:
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for review in pull_request["reviews"].get("nodes") or []:
        commit = review.get("commit") or {}
        submitted = review.get("submittedAt")
        author = review.get("author") or {}
        if (
            author.get("__typename") == "Bot"
            and _actor_id(review) == actor_id
            and commit.get("oid") == head_sha
            and submitted
            and "lastEditedAt" in review
            and review["lastEditedAt"] is None
        ):
            when = _parse_utc(submitted)
            if when > head_epoch:
                candidates.append((when, review))
    if gate == CODEX_GATE:
        valid_outcomes = [
            when for when, review in candidates if review.get("state") in TERMINAL_REVIEW_STATES
        ]
        comments = pull_request.get("comments") or {}
        for comment in comments.get("nodes") or []:
            created_raw = comment.get("createdAt")
            updated_raw = comment.get("updatedAt") or created_raw
            author = comment.get("author") or {}
            body = comment.get("body") or ""
            codex_response = author.get("__typename") == "Bot" and _actor_id(comment) == actor_id
            if not codex_response or not created_raw or not updated_raw:
                continue
            created = _parse_utc(created_raw)
            updated = _parse_utc(updated_raw)
            if updated <= head_epoch:
                continue
            database_id = comment.get("databaseId")
            if not isinstance(database_id, int):
                continue
            immutable = created == updated and comment.get("lastEditedAt") is None
            commit_lines = CODEX_REVIEWED_COMMIT_LINE.findall(body)
            clean_completion = (
                immutable
                and created > head_epoch
                and body.splitlines()[0:1] == [CODEX_CLEAN_COMPLETION_MARKER]
                and len(commit_lines) == 1
                and commit_lines[0] == head_sha[:10].lower()
                and codex_head_safe
            )
            if clean_completion:
                valid_outcomes.append(created)
        return max(valid_outcomes) if valid_outcomes else None
    if not candidates:
        return None
    if gate == CODERABBIT_GATE:
        completed = [
            (when, review)
            for when, review in candidates
            if review.get("state") in TERMINAL_REVIEW_STATES
            and CODERABBIT_COMPLETION_MARKER.lower() in (review.get("body") or "").lower()
        ]
        if not completed:
            return None
        completed_at = max(when for when, _ in completed)
        later_noncompletion = [
            review
            for when, review in candidates
            if when >= completed_at
            and (
                review.get("state") not in TERMINAL_REVIEW_STATES
                or (
                    (review.get("body") or "").strip()
                    and CODERABBIT_COMPLETION_MARKER.lower()
                    not in (review.get("body") or "").lower()
                )
            )
        ]
        return None if later_noncompletion else completed_at
    latest_at = max(when for when, _ in candidates)
    latest = [review for when, review in candidates if when == latest_at]
    if any(review.get("state") not in TERMINAL_REVIEW_STATES for review in latest):
        return None
    return latest_at


def _current_human_approval(
    pull_request: dict[str, Any], head_sha: str, head_epoch: datetime
) -> tuple[datetime | None, bool]:
    author_id = _actor_id(pull_request)
    approvals: list[datetime] = []
    active_change_request = False
    opinions = pull_request.get("latestHumanOpinions") or {}
    for review in opinions.get("nodes") or []:
        reviewer_id = _actor_id(review)
        author = review.get("author") or {}
        submitted = review.get("submittedAt")
        if (
            not isinstance(reviewer_id, int)
            or reviewer_id == author_id
            or author.get("__typename") != "User"
            or review.get("authorAssociation") not in HUMAN_ASSOCIATIONS
            or review.get("state") not in HUMAN_OPINION_STATES
            or not submitted
        ):
            continue
        if review.get("state") == "CHANGES_REQUESTED":
            active_change_request = True
            continue
        commit = review.get("commit") or {}
        when = _parse_utc(review["submittedAt"])
        if (
            review.get("state") == "APPROVED"
            and commit.get("oid") == head_sha
            and when > head_epoch
        ):
            approvals.append(when)
    return (max(approvals) if approvals else None, active_change_request)


def _coderabbit_protocol_events(
    pull_request: dict[str, Any],
    head_sha: str,
) -> list[tuple[datetime, str, int, int | None]]:
    reservation_pattern = re.compile(r"(?i)coderabbit-reservation:\s*([0-9a-f]{40})")
    raw_events: list[tuple[datetime, str, int, str | None, int | None]] = []
    for comment in pull_request["comments"].get("nodes") or []:
        body = comment.get("body") or ""
        when = _parse_utc(comment["createdAt"])
        updated = _parse_utc(comment.get("updatedAt") or comment["createdAt"])
        author = comment.get("author") or {}
        actor_id = _actor_id(comment)
        if (
            author.get("__typename") != "User"
            or not isinstance(actor_id, int)
            or comment.get("authorAssociation") not in HUMAN_ASSOCIATIONS
            or updated != when
            or comment.get("lastEditedAt") is not None
        ):
            continue
        comment_id = comment.get("databaseId")
        if not isinstance(comment_id, int):
            comment_id = None
        reservation = reservation_pattern.fullmatch(body.strip())
        if reservation:
            raw_events.append(
                (when, "reservation", actor_id, reservation.group(1).lower(), comment_id)
            )
        elif body.strip().lower() == "@coderabbitai full review":
            raw_events.append((when, "trigger", actor_id, None, comment_id))

    # A trigger carries no SHA. Attribute it to its same-maintainer reservation
    # before filtering for the current head; filtering reservations first can
    # leave a prior head's trigger looking current when a commit predates its push.
    raw_events.sort(key=lambda event: (event[0], event[4] if event[4] is not None else -1))
    pending: dict[int, tuple[datetime, str]] = {}
    protocol: list[tuple[datetime, str, int, int | None]] = []
    current_head = head_sha.lower()
    for when, kind, actor_id, reservation_sha, comment_id in raw_events:
        if kind == "reservation":
            assert reservation_sha is not None
            pending[actor_id] = (when, reservation_sha)
            if reservation_sha == current_head:
                protocol.append((when, "reservation", actor_id, comment_id))
            continue
        reservation = pending.pop(actor_id, None)
        if reservation is None:
            # An exact trusted trigger without a visible reservation is
            # unattributable, so retain it to make current-head checks fail closed.
            protocol.append((when, "trigger", actor_id, comment_id))
            continue
        _, reserved_sha = reservation
        if reserved_sha == current_head:
            protocol.append((when, "trigger", actor_id, comment_id))
    protocol.sort(key=lambda event: (event[0], event[3] if event[3] is not None else -1))
    return protocol


def _coderabbit_trigger_state(
    repository: dict[str, Any],
    pull_request: dict[str, Any],
    head_sha: str,
    review_at: datetime,
    head_epoch: datetime,
    protocol_epoch: datetime,
    protocol_label: str,
) -> list[str]:
    errors: list[str] = []
    protocol = _coderabbit_protocol_events(pull_request, head_sha)

    # GitHub timestamps have one-second resolution; immutable database IDs break
    # ties between different protocol attempts. One maintainer's reservation and
    # trigger must still occur in distinct seconds.
    protocol = [event for event in protocol if event[0] > max(head_epoch, protocol_epoch)]
    protocol.sort(key=lambda event: (event[0], event[3] if event[3] is not None else -1))
    if not protocol:
        errors.append("CodeRabbit needs a current-SHA reservation, trigger, then completed review")
        return errors
    comment_ids = [event[3] for event in protocol]
    if any(comment_id is None for comment_id in comment_ids) or len(comment_ids) != len(
        set(comment_ids)
    ):
        errors.append("CodeRabbit protocol needs unique immutable comment IDs")
        return errors
    pending_by_actor: dict[int, datetime] = {}
    reservation_queue: list[int] = []
    trigger_times: list[datetime] = []
    for when, kind, actor_id, _ in protocol:
        if kind == "reservation":
            if actor_id in pending_by_actor:
                errors.append("CodeRabbit reservations and triggers must form one-to-one pairs")
                return errors
            pending_by_actor[actor_id] = when
            reservation_queue.append(actor_id)
            continue
        if actor_id not in pending_by_actor:
            if pending_by_actor:
                errors.append("the same maintainer must reserve and trigger CodeRabbit")
            else:
                errors.append("CodeRabbit reservations and triggers must form one-to-one pairs")
            return errors
        if not reservation_queue or reservation_queue[0] != actor_id:
            errors.append("CodeRabbit triggers must follow reservation FIFO order")
            return errors
        if when == pending_by_actor[actor_id]:
            errors.append(
                "CodeRabbit needs strictly ordered reservation and trigger comments "
                f"after {protocol_label}"
            )
            return errors
        if when >= review_at:
            errors.append("CodeRabbit was triggered again after its latest completed review")
            return errors
        reservation_queue.pop(0)
        del pending_by_actor[actor_id]
        trigger_times.append(when)
    if reservation_queue or pending_by_actor or not trigger_times:
        errors.append("CodeRabbit needs a current-SHA reservation, trigger, then completed review")
        return errors

    completion_times = sorted(
        _parse_utc(review["submittedAt"])
        for review in pull_request["reviews"].get("nodes") or []
        if (review.get("author") or {}).get("__typename") == "Bot"
        and _actor_id(review) == BOT_ACTOR_IDS[CODERABBIT_GATE]
        and (review.get("commit") or {}).get("oid") == head_sha
        and review.get("submittedAt")
        and _parse_utc(review["submittedAt"]) > head_epoch
        and review.get("state") in TERMINAL_REVIEW_STATES
        and "lastEditedAt" in review
        and review["lastEditedAt"] is None
        and CODERABBIT_COMPLETION_MARKER.lower() in (review.get("body") or "").lower()
    )
    completion_index = 0
    for trigger_index, current_trigger in enumerate(trigger_times):
        while (
            completion_index < len(completion_times)
            and completion_times[completion_index] <= current_trigger
        ):
            completion_index += 1
        if completion_index >= len(completion_times):
            errors.append("each CodeRabbit trigger needs a distinct later completed review")
            return errors
        completion = completion_times[completion_index]
        completion_index += 1
        if (
            trigger_index + 1 < len(trigger_times)
            and trigger_times[trigger_index + 1] <= completion
        ):
            errors.append("wait for each CodeRabbit review completion before triggering again")
            return errors

    trigger_at = trigger_times[-1]
    window = trigger_at - timedelta(hours=1)
    recent = repository.get("recentPullRequests")
    if not isinstance(recent, dict):
        errors.append("CodeRabbit rolling-hour ledger is unavailable")
        return errors
    nodes = recent.get("nodes")
    if not isinstance(recent.get("totalCount"), int) or not isinstance(nodes, list):
        errors.append("CodeRabbit rolling-hour ledger has an invalid shape")
        return errors
    if recent.get("totalCount", 0) > len(nodes):
        if not nodes:
            errors.append("recent PR pagination cannot prove the CodeRabbit hourly quota")
        else:
            oldest_update = min(_parse_utc(node["updatedAt"]) for node in nodes)
            if oldest_update >= window:
                errors.append("recent PR pagination cannot prove the CodeRabbit hourly quota")
    trigger_count = 0
    for pr in nodes:
        comments = pr["comments"]
        if _connection_truncated_since(
            comments,
            time_key="updatedAt",
            since=window,
            inclusive=True,
        ):
            errors.append("comment pagination cannot prove the CodeRabbit hourly quota")
            continue
        trigger_count += sum(
            window <= _parse_utc(comment["createdAt"]) <= trigger_at
            and _parse_utc(comment.get("updatedAt") or comment["createdAt"])
            == _parse_utc(comment["createdAt"])
            and comment.get("lastEditedAt") is None
            and (comment.get("author") or {}).get("__typename") == "User"
            and comment.get("authorAssociation") in HUMAN_ASSOCIATIONS
            and (comment.get("body") or "").strip().lower() == "@coderabbitai full review"
            for comment in comments.get("nodes") or []
        )
    if trigger_count > 5:
        errors.append(f"CodeRabbit rolling-hour trigger quota exceeded: {trigger_count} > 5")
    return errors


def _load_repository(context: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    if context.get("errors"):
        errors.append("GitHub GraphQL context contains errors")
    repository = (context.get("data") or {}).get("repository")
    if not isinstance(repository, dict) or not isinstance(repository.get("pullRequest"), dict):
        errors.append("GitHub context has no pull request")
        return None, errors
    return repository, errors


def _finalizer_activity(
    pull_request: dict[str, Any],
    *,
    comment_node_id: str,
    comment_created_at: str,
    comment_actor_id: int,
) -> tuple[datetime | None, list[str]]:
    errors: list[str] = []
    matches = [
        comment
        for comment in pull_request.get("comments", {}).get("nodes") or []
        if comment.get("id") == comment_node_id
    ]
    if len(matches) != 1:
        return None, ["finalizer comment is missing from the live pull request"]
    comment = matches[0]
    author = comment.get("author") or {}
    if (
        author.get("__typename") != "User"
        or _actor_id(comment) != comment_actor_id
        or comment.get("authorAssociation") not in HUMAN_ASSOCIATIONS
    ):
        errors.append("finalizer comment is not from the expected trusted maintainer")
    if (comment.get("body") or "") != FINALIZE_COMMAND:
        errors.append("finalizer comment body is not the exact validation command")
    created_raw = comment.get("createdAt")
    updated_raw = comment.get("updatedAt")
    try:
        created_at = _parse_utc(created_raw) if created_raw else None
        expected_created_at = _parse_utc(comment_created_at)
        updated_at = _parse_utc(updated_raw) if updated_raw else None
    except (TypeError, ValueError):
        return None, errors + ["finalizer comment has an invalid timestamp"]
    if created_at is None or created_at != expected_created_at:
        errors.append("finalizer comment creation time does not match the triggering event")
    if created_at is None or updated_at != created_at or comment.get("lastEditedAt") is not None:
        errors.append("finalizer comment was edited after creation")
    return created_at, errors


def validate_context(
    context: dict[str, Any],
    files: list[ChangedFile],
    *,
    now: datetime | None = None,
    expected_head: str | None = None,
    expected_draft: bool | None = None,
    expected_pr_updated_at: str | None = None,
    expected_pr_body: str | None = None,
    finalize_comment_node_id: str | None = None,
    finalize_comment_created_at: str | None = None,
    finalize_comment_actor_id: int | None = None,
) -> list[str]:
    now = now or datetime.now(UTC)
    repository, errors = _load_repository(context)
    if repository is None:
        return errors
    pull_request = repository["pullRequest"]
    head_sha = pull_request.get("headRefOid") or ""
    if not re.fullmatch(r"[0-9a-fA-F]{40}", head_sha):
        errors.append("pull request head SHA is not a full 40-character SHA")
    if expected_head is not None and head_sha.lower() != expected_head.lower():
        errors.append("GitHub context head SHA changed during validation")
    if expected_draft is not None and pull_request.get("isDraft") is not expected_draft:
        errors.append("GitHub context draft state changed during validation")
    if expected_pr_body is not None and (pull_request.get("body") or "") != expected_pr_body:
        errors.append("GitHub context body changed during validation")
    if expected_pr_updated_at is not None:
        try:
            actual_updated_at = _parse_utc(pull_request["updatedAt"])
            expected_updated_at = _parse_utc(expected_pr_updated_at)
        except (KeyError, TypeError, ValueError):
            errors.append("pull request has an invalid update timestamp")
        else:
            if actual_updated_at != expected_updated_at:
                errors.append("pull request state changed during validation")
    open_pull_requests = repository.get("openPullRequests") or {}
    open_nodes = open_pull_requests.get("nodes") or []
    if open_pull_requests.get("totalCount") != len(open_nodes):
        errors.append("open pull request pagination cannot prove head uniqueness")
    if pull_request.get("state") != "OPEN":
        errors.append("pull request is not open")
    current_number = pull_request.get("number")
    same_head_numbers = [
        node.get("number")
        for node in open_nodes
        if (node.get("headRefOid") or "").lower() == head_sha.lower()
    ]
    if same_head_numbers != [current_number]:
        errors.append("current head SHA must belong to exactly one open pull request")
    finalizer_values = (
        finalize_comment_node_id,
        finalize_comment_created_at,
        finalize_comment_actor_id,
    )
    has_finalizer = all(value is not None for value in finalizer_values)
    if any(value is not None for value in finalizer_values) and not has_finalizer:
        errors.append("finalizer event context is incomplete")
    commits = (pull_request.get("commits") or {}).get("nodes") or []
    if len(commits) != 1:
        return errors + ["GitHub context does not contain exactly one current head commit"]
    committed_at = _parse_utc(commits[0]["commit"]["committedDate"])
    created_at = pull_request.get("createdAt")
    if not created_at:
        return errors + ["GitHub context has no pull request creation time"]
    head_epoch = max(committed_at, _parse_utc(created_at))
    force_pushes = (pull_request.get("timelineItems") or {}).get("nodes") or []
    force_push_times = [
        _parse_utc(event["createdAt"]) for event in force_pushes if event.get("createdAt")
    ]
    if force_push_times:
        head_epoch = max(head_epoch, *force_push_times)

    route, schema_version, selected_bots, evidence, parse_errors = _parse_route_section(
        pull_request.get("body") or ""
    )
    errors.extend(parse_errors)
    if pull_request.get("changedFiles") != len(files):
        errors.append("changed-file API count does not match the pull request")
    try:
        floor = minimum_route(files)
    except ValueError as exc:
        errors.append(str(exc))
        floor = None
    if route and floor and ROUTE_RANK[route] < ROUTE_RANK[floor]:
        errors.append(f"selected route {route} is below changed-path minimum {floor}")
    if schema_version == 1:
        errors.append(
            "review-route schema v1 is obsolete; migrate to v2 and select one automated reviewer"
        )
        return errors
    if pull_request.get("isDraft") is True or route is None:
        return errors

    reviews = pull_request.get("reviews") or {}
    comments = pull_request.get("comments") or {}
    human_opinions = pull_request.get("latestHumanOpinions") or {}
    if _connection_truncated_since(reviews, time_key="submittedAt", since=head_epoch):
        errors.append("review pagination cannot prove current-head review state")
    if _connection_truncated_since(comments, time_key="updatedAt", since=head_epoch):
        errors.append("comment pagination cannot prove current-head review state")
    if human_opinions.get("totalCount", 0) > len(human_opinions.get("nodes") or []):
        errors.append("human-opinion pagination cannot prove active review state")
    threads = pull_request.get("reviewThreads") or {}
    if threads.get("totalCount", 0) > len(threads.get("nodes") or []):
        errors.append("review-thread pagination is incomplete")
    if any(not thread.get("isResolved") for thread in threads.get("nodes") or []):
        errors.append("unresolved review threads remain")

    if len(selected_bots) != 1:
        errors.append("select exactly one automated PR reviewer")
    required = {*selected_bots, HUMAN_GATE}
    if CODERABBIT_GATE not in required and any(
        when > head_epoch and kind == "trigger"
        for when, kind, _, _ in _coderabbit_protocol_events(pull_request, head_sha)
    ):
        errors.append("manual CodeRabbit triggers require selecting the CodeRabbit lane")
    body_times: dict[str, datetime] = {}
    for gate in GATES:
        row = evidence.get(gate)
        if row is None:
            continue
        if gate not in required:
            if row.head != "N/A" or row.status != "N/A":
                errors.append(f"nonapplicable gate must use N/A in both cells: {gate}")
            continue
        if row.head.lower() != head_sha.lower():
            errors.append(f"required gate is not bound to the current head SHA: {gate}")
        match = UTC_RESULT.fullmatch(row.status)
        expected_result = "APPROVED" if gate == HUMAN_GATE else "COMPLETE"
        if match is None or match.group("result") != expected_result:
            errors.append(
                f"required gate needs exact UTC evidence ending in {expected_result}: {gate}"
            )
            continue
        try:
            when = _parse_utc(match.group("timestamp"))
        except ValueError:
            errors.append(f"required gate has an invalid UTC timestamp: {gate}")
            continue
        if when <= head_epoch or when > now + timedelta(minutes=1):
            errors.append(
                f"required gate timestamp is outside the current-head review window: {gate}"
            )
        body_times[gate] = when

    observed: dict[str, datetime] = {}
    codex_head_object = repository.get("codexHeadObject") or {}
    codex_full_oid = codex_head_object.get("oid")
    codex_abbreviated_oid = codex_head_object.get("abbreviatedOid")
    normalized_head = head_sha.lower()
    codex_head_safe = (
        codex_head_object.get("__typename") == "Commit"
        and isinstance(codex_full_oid, str)
        and codex_full_oid.lower() == normalized_head
        and isinstance(codex_abbreviated_oid, str)
        and re.fullmatch(r"[0-9a-f]{4,10}", codex_abbreviated_oid) is not None
        and normalized_head.startswith(codex_abbreviated_oid)
    )
    for gate, actor_id in BOT_ACTOR_IDS.items():
        if gate not in required:
            continue
        activity = _bot_activity(
            pull_request,
            gate,
            actor_id,
            head_sha,
            head_epoch,
            codex_head_safe,
        )
        if activity is None:
            errors.append(f"no verified current-head GitHub activity for: {gate}")
        else:
            observed[gate] = activity
    if HUMAN_GATE in required:
        approval, active_change_request = _current_human_approval(
            pull_request, head_sha, head_epoch
        )
        if active_change_request:
            errors.append("an active human maintainer change request remains")
        if approval is None:
            errors.append("no current-head approval from an independent human collaborator")
        else:
            observed[HUMAN_GATE] = approval

    for gate, activity in observed.items():
        if gate in body_times and abs((body_times[gate] - activity).total_seconds()) > 600:
            errors.append(
                f"declared evidence time does not match verified GitHub activity: {gate}"
            )

    if schema_version == 2 and selected_bots == [CODERABBIT_GATE] and CODERABBIT_GATE in observed:
        errors.extend(
            _coderabbit_trigger_state(
                repository,
                pull_request,
                head_sha,
                observed[CODERABBIT_GATE],
                head_epoch,
                head_epoch,
                "the current head",
            )
        )

    gate_times = {**body_times, **observed}
    sequence = [*selected_bots, HUMAN_GATE]
    sequence_error = "selected automated review must precede human approval"
    if all(gate in gate_times for gate in sequence):
        times = [gate_times[gate] for gate in sequence]
        if any(left >= right for left, right in zip(times, times[1:], strict=False)):
            errors.append(sequence_error)
    if has_finalizer:
        finalizer_at, finalizer_errors = _finalizer_activity(
            pull_request,
            comment_node_id=finalize_comment_node_id,
            comment_created_at=finalize_comment_created_at,
            comment_actor_id=finalize_comment_actor_id,
        )
        errors.extend(finalizer_errors)
        required_times = [
            timestamp
            for gate in required
            for timestamp in (body_times.get(gate), observed.get(gate))
            if timestamp is not None
        ]
        if finalizer_at is not None and (
            finalizer_at <= head_epoch
            or any(finalizer_at <= timestamp for timestamp in required_times)
        ):
            errors.append("finalizer comment must strictly follow every required review gate")
    return errors


def _load_files(path: Path) -> list[ChangedFile]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    pages = raw if raw and isinstance(raw[0], list) else [raw]
    return [
        ChangedFile(item["filename"], item.get("previous_filename"))
        for page in pages
        for item in page
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--files", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-draft", choices=("true", "false"), required=True)
    parser.add_argument("--expected-pr-updated-at", required=True)
    parser.add_argument("--expected-pr-snapshot", type=Path, required=True)
    parser.add_argument("--finalize-comment-node-id")
    parser.add_argument("--finalize-comment-created-at")
    parser.add_argument("--finalize-comment-actor-id", type=int)
    args = parser.parse_args()
    context = json.loads(args.context.read_text(encoding="utf-8"))
    expected_snapshot = json.loads(args.expected_pr_snapshot.read_text(encoding="utf-8"))
    expected_pr_body = expected_snapshot.get("body") or ""
    if not isinstance(expected_pr_body, str):
        print("::error title=Review Route::REST pull request body has an invalid shape")
        return 1
    errors = validate_context(
        context,
        _load_files(args.files),
        expected_head=args.expected_head,
        expected_draft=args.expected_draft == "true",
        expected_pr_updated_at=args.expected_pr_updated_at,
        expected_pr_body=expected_pr_body,
        finalize_comment_node_id=args.finalize_comment_node_id,
        finalize_comment_created_at=args.finalize_comment_created_at,
        finalize_comment_actor_id=args.finalize_comment_actor_id,
    )
    if errors:
        for error in dict.fromkeys(errors):
            print(f"::error title=Review Route::{error}")
        return 1
    print("Review route is verified against the current PR head and live GitHub state.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
