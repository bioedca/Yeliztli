"""Prepare and verify an active issue-claim heartbeat; never write to GitHub.

This is a cooperative coordination check, not a lock or an authorization service.
Election, collaborator eligibility and legal state transitions remain in AGENTS.md.
Only fresh server timestamps establish lease renewal; a successful PATCH does not.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

CLAIM_TOKEN = "yeliztli-claim:v1"
MARKER = f"<!-- {CLAIM_TOKEN} -->"
CLAIM = re.compile(re.escape(MARKER) + r"\n```json\n(?P<record>.*?)\n```(?P<tail>.*)", re.S)
LEASE = timedelta(minutes=90)
PROVISIONAL_LEASE = timedelta(minutes=2)
STATES = {"candidate", "accept", "active", "waiting", "handoff", "done"}


class HeartbeatError(ValueError):
    """Renewal is unproven; stop writes and retain the previous lease deadline."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise HeartbeatError(message)


def _positive(value: Any) -> bool:
    return type(value) is int and value > 0


def _timestamp(value: Any) -> datetime:
    _require(
        isinstance(value, str)
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value) is not None,
        "Missing or invalid server timestamp",
    )
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HeartbeatError("Invalid server timestamp") from exc


def _json(text: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            _require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=unique)
    except json.JSONDecodeError as exc:
        raise HeartbeatError("Malformed JSON evidence") from exc


def _identity(identity: dict[str, Any]) -> None:
    _require(
        isinstance(identity.get("repo"), str)
        and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", identity["repo"]) is not None,
        "Invalid repository identity",
    )
    for field in ("issue", "comment_id", "generation", "author_id"):
        _require(_positive(identity.get(field)), f"Invalid {field}")
    agent = identity.get("agent_id")
    _require(isinstance(agent, str) and agent.startswith("yz-"), "Invalid agent identity")
    try:
        parsed = UUID(agent[3:])
    except ValueError as exc:
        raise HeartbeatError("Invalid agent identity") from exc
    _require(parsed.version == 4 and str(parsed) == agent[3:], "Invalid agent identity")


def _record(body: str) -> tuple[dict[str, Any], re.Match[str]]:
    match = CLAIM.fullmatch(body)
    if match is None or body.count(CLAIM_TOKEN) != 1:
        raise HeartbeatError("Malformed claim record")
    record = _json(match["record"])
    _require(isinstance(record, dict), "Malformed claim record")
    _require(
        isinstance(record.get("state"), str) and record["state"] in STATES,
        "Unknown claim state; inspect the existing protocol",
    )
    _require(_positive(record.get("generation")), "Invalid claim generation")
    _require(isinstance(record.get("agent_id"), str), "Missing claim agent identity")
    counter = record.get("heartbeat_seq", 0)
    _require(type(counter) is int and counter >= 0, "Invalid heartbeat counter")
    return record, match


def _next_body(body: str) -> str:
    record, match = _record(body)
    record["heartbeat_seq"] = record.get("heartbeat_seq", 0) + 1
    return (
        body[: match.start("record")] + json.dumps(record, indent=2) + body[match.end("record") :]
    )


def fetch_pages(repo: str, issue: int) -> list[list[dict[str, Any]]]:
    """Let gh follow every REST Link page; a partial/failed request is not evidence."""
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                "--hostname",
                "github.com",
                "--method",
                "GET",
                f"repos/{repo}/issues/{issue}/comments?per_page=100",
                "--paginate",
                "--slurp",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HeartbeatError("Could not fetch every comment page; no renewal proof") from exc
    pages = _json(result.stdout)
    _require(
        isinstance(pages, list) and bool(pages) and all(isinstance(page, list) for page in pages),
        "Missing complete comment pages",
    )
    return pages


def _active_owner(
    identity: dict[str, Any], now: datetime | None
) -> tuple[dict[str, Any], datetime]:
    _identity(identity)
    first = fetch_pages(identity["repo"], identity["issue"])
    second = fetch_pages(identity["repo"], identity["issue"])
    # Check expiry after both network reads, not at the start of a slow fetch.
    now = datetime.now(UTC) if now is None else now
    _require(now.tzinfo is not None, "Observation time must include a timezone")
    _require(first == second, "Complete comment snapshots disagree")
    _require(
        bool(first) and all(isinstance(page, list) for page in first), "Missing comment pages"
    )
    expected_url = f"https://api.github.com/repos/{identity['repo']}/issues/{identity['issue']}"
    seen: set[int] = set()
    owner = None
    live: list[int] = []
    for page in first:
        for comment in page:
            _require(isinstance(comment, dict), "Malformed comment evidence")
            comment_id = comment.get("id")
            _require(
                _positive(comment_id) and comment_id not in seen, "Missing/duplicate comment ID"
            )
            seen.add(comment_id)
            _require(comment.get("issue_url") == expected_url, "Comment belongs to another issue")
            body = comment.get("body")
            _require(isinstance(body, str), "Missing comment body")
            if CLAIM_TOKEN not in body:
                continue
            record, _ = _record(body)
            created = _timestamp(comment.get("created_at"))
            updated = _timestamp(comment.get("updated_at"))
            _require(created <= updated <= now, "Invalid or future claim timestamp")
            state = record["state"]
            lifetime = PROVISIONAL_LEASE if state in {"candidate", "accept"} else LEASE
            if state != "done" and now < updated + lifetime:
                live.append(comment_id)
            if comment_id == identity["comment_id"]:
                user = comment.get("user")
                _require(
                    isinstance(user, dict)
                    and _positive(user.get("id"))
                    and user["id"] == identity["author_id"],
                    "Claim author changed",
                )
                _require(
                    record["agent_id"] == identity["agent_id"]
                    and record["generation"] == identity["generation"]
                    and state == "active",
                    "Expected active claim identity/generation/state does not match",
                )
                _require(now < updated + LEASE, "Claim is expired; never refresh it")
                owner = comment
    _require(owner is not None, "Expected claim is missing")
    _require(live == [identity["comment_id"]], "Sole live active ownership is not proved")
    return owner, now


def prepare(identity: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Read-only preflight; the caller applies request.body using the GitHub connector."""
    owner, observed = _active_owner(identity, now)
    before = {key: owner[key] for key in ("created_at", "updated_at", "body")}
    return {
        "schema": 1,
        "identity": identity,
        "prepared_at": observed.isoformat(),
        "before": before,
        "request": {"body": _next_body(owner["body"])},
    }


def verify(plan: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Prove renewal using fresh complete snapshots, never the PATCH response."""
    _require(
        isinstance(plan, dict) and type(plan.get("schema")) is int and plan["schema"] == 1,
        "Invalid heartbeat plan",
    )
    identity = plan.get("identity")
    before = plan.get("before")
    request = plan.get("request")
    _require(
        isinstance(identity, dict) and isinstance(before, dict) and isinstance(request, dict),
        "Incomplete heartbeat plan",
    )
    _identity(identity)
    original_body = before.get("body")
    _require(isinstance(original_body, str), "Missing original claim body")
    record, _ = _record(original_body)
    _require(
        record["state"] == "active"
        and record["generation"] == identity["generation"]
        and record["agent_id"] == identity["agent_id"],
        "Original active claim identity does not match",
    )
    old_time = _timestamp(before.get("updated_at"))
    created = _timestamp(before.get("created_at"))
    try:
        prepared = datetime.fromisoformat(plan["prepared_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HeartbeatError("Missing preparation time") from exc
    _require(prepared.tzinfo is not None, "Preparation time must include a timezone")
    _require(created <= old_time <= prepared, "Stale or invalid preparation evidence")
    _require(prepared < old_time + LEASE, "The prior lease was already expired")
    expected = _next_body(original_body)
    _require(
        request.get("body") == expected, "Prepared body changed more than the heartbeat counter"
    )
    owner, observed = _active_owner(identity, now)
    _require(prepared <= observed, "Preparation time is in the future")
    renewed = _timestamp(owner["updated_at"])
    _require(renewed > old_time, "Server timestamp did not advance; retain the prior expiry")
    _require(renewed < old_time + LEASE, "Renewal occurred after the prior lease expired")
    _require(renewed >= prepared.replace(microsecond=0), "Renewal predates its preparation")
    _require(owner["created_at"] == before["created_at"], "Claim creation identity changed")
    _require(owner["body"] == expected, "Observed body does not match the prepared heartbeat")
    return {
        "comment_id": identity["comment_id"],
        "agent_id": identity["agent_id"],
        "generation": identity["generation"],
        "heartbeat_seq": record.get("heartbeat_seq", 0) + 1,
        "updated_at": owner["updated_at"],
        "expires_at": (renewed + LEASE).isoformat().replace("+00:00", "Z"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparation = commands.add_parser("prepare", help="Prepare one active-claim heartbeat")
    for name in ("repo", "agent-id"):
        preparation.add_argument("--" + name, required=True)
    for name in ("issue", "comment-id", "generation", "author-id"):
        preparation.add_argument("--" + name, type=int, required=True)
    preparation.add_argument("--plan", type=Path, required=True)
    verification = commands.add_parser("verify", help="Independently prove server-side renewal")
    verification.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            identity = {
                key: getattr(args, key)
                for key in ("repo", "issue", "comment_id", "generation", "author_id", "agent_id")
            }
            plan = prepare(identity)
            with args.plan.open("x", encoding="utf-8") as stream:
                json.dump(plan, stream, indent=2)
                stream.write("\n")
            print("Heartbeat prepared; no GitHub write occurred and renewal is not yet proved.")
        else:
            result = verify(_json(args.plan.read_text(encoding="utf-8")))
            print(json.dumps(result, indent=2))
    except (HeartbeatError, OSError) as exc:
        print(f"Heartbeat unproven: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
