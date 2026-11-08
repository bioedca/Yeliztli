"""Issue coordination renews on server evidence, never on an acknowledged PATCH."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from scripts import claim_heartbeat as heartbeat

TIME = datetime(2026, 1, 1, 12, tzinfo=UTC)
IDENTITY = {
    "repo": "example/project",
    "issue": 7,
    "comment_id": 42,
    "author_id": 17,
    "generation": 3,
    "agent_id": "yz-12345678-1234-4234-9234-123456789abc",
}


def stamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def comment(**changes):
    record = {
        "agent_id": IDENTITY["agent_id"],
        "generation": 3,
        "state": "active",
        "phase": "test",
        "branch": "issue-7-test",
        "worktree_alias": "issue-7-test",
    }
    record.update(changes)
    return {
        "id": 42,
        "user": {"id": 17},
        "issue_url": "https://api.github.com/repos/example/project/issues/7",
        "created_at": stamp(TIME - timedelta(minutes=1)),
        "updated_at": stamp(TIME),
        "body": heartbeat.MARKER
        + "\n```json\n"
        + json.dumps(record, indent=2)
        + "\n```\n\nPublic phase note.\n",
    }


def snapshots(monkeypatch, *pages):
    calls = []
    queue = iter(copy.deepcopy(pages))

    def fetch(repo, issue):
        calls.append((repo, issue))
        return next(queue)

    monkeypatch.setattr(heartbeat, "fetch_pages", fetch)
    return calls


def prepared(monkeypatch, original=None):
    original = comment() if original is None else original
    snapshots(monkeypatch, [[original]], [[original]])
    return heartbeat.prepare(dict(IDENTITY), TIME)


def renewed(plan, when=TIME + timedelta(seconds=1)):
    result = comment()
    result["updated_at"] = stamp(when)
    result["body"] = plan["request"]["body"]
    return result


def test_prepare_changes_only_counter_and_preserves_public_note(monkeypatch):
    original = comment(heartbeat_seq=8)
    plan = prepared(monkeypatch, original)
    before = json.loads(original["body"].split("```json\n")[1].split("\n```")[0])
    after = json.loads(plan["request"]["body"].split("```json\n")[1].split("\n```")[0])
    assert after.pop("heartbeat_seq") == 9
    before.pop("heartbeat_seq")
    assert after == before
    assert plan["before"]["body"] == original["body"]
    assert plan["request"]["body"].endswith("\n```\n\nPublic phase note.\n")
    assert set(plan["before"]) == {"created_at", "updated_at", "body"}


def test_legacy_counter_starts_at_one_and_real_renewal_proves_server_expiry(monkeypatch):
    plan = prepared(monkeypatch)
    after = renewed(plan)
    calls = snapshots(monkeypatch, [[after]], [[after]])
    result = heartbeat.verify(plan, TIME + timedelta(seconds=2))
    assert calls == [("example/project", 7)] * 2
    assert result == {
        "comment_id": 42,
        "agent_id": IDENTITY["agent_id"],
        "generation": 3,
        "heartbeat_seq": 1,
        "updated_at": "2026-01-01T12:00:01Z",
        "expires_at": "2026-01-01T13:30:01Z",
    }


@pytest.mark.parametrize("body_changed", [False, True])
def test_acknowledged_noop_or_counter_change_without_server_advance_is_rejected(
    monkeypatch, body_changed
):
    plan = prepared(monkeypatch)
    after = renewed(plan, TIME) if body_changed else comment()
    snapshots(monkeypatch, [[after]], [[after]])
    with pytest.raises(heartbeat.HeartbeatError, match="Server timestamp did not advance"):
        heartbeat.verify(plan, TIME + timedelta(seconds=2))


@pytest.mark.parametrize("age", [90, 91])
def test_prepare_cannot_refresh_an_expired_claim(monkeypatch, age):
    snapshots(monkeypatch, [[comment()]], [[comment()]])
    with pytest.raises(heartbeat.HeartbeatError, match="expired"):
        heartbeat.prepare(dict(IDENTITY), TIME + timedelta(minutes=age))


def test_late_server_update_cannot_revive_the_old_lease(monkeypatch):
    plan = prepared(monkeypatch)
    after = renewed(plan, TIME + timedelta(minutes=90))
    snapshots(monkeypatch, [[after]], [[after]])
    with pytest.raises(heartbeat.HeartbeatError, match="prior lease expired"):
        heartbeat.verify(plan, TIME + timedelta(minutes=90, seconds=1))


def test_timely_renewal_can_be_verified_after_the_old_expiry(monkeypatch):
    plan = prepared(monkeypatch)
    after = renewed(plan, TIME + timedelta(minutes=89))
    snapshots(monkeypatch, [[after]], [[after]])
    result = heartbeat.verify(plan, TIME + timedelta(minutes=91))
    assert result["expires_at"] == "2026-01-01T14:59:00Z"


def test_expired_new_lease_is_not_a_success(monkeypatch):
    plan = prepared(monkeypatch)
    after = renewed(plan)
    snapshots(monkeypatch, [[after]], [[after]])
    with pytest.raises(heartbeat.HeartbeatError, match="expired"):
        heartbeat.verify(plan, TIME + timedelta(minutes=90, seconds=1))


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 43),
        ("user", {"id": 18}),
        ("issue_url", "https://api.github.com/repos/example/project/issues/8"),
        ("created_at", "2026-01-01T11:59:01Z"),
        ("updated_at", None),
    ],
)
def test_server_identity_or_timestamp_mismatch_fails(monkeypatch, field, value):
    plan = prepared(monkeypatch)
    after = renewed(plan)
    after[field] = value
    snapshots(monkeypatch, [[after]], [[after]])
    with pytest.raises(heartbeat.HeartbeatError):
        heartbeat.verify(plan, TIME + timedelta(seconds=2))


@pytest.mark.parametrize("operation", ["prepare", "verify"])
@pytest.mark.parametrize(
    "requested_repo,server_repo",
    [
        ("EXAMPLE/PROJECT", "example/project"),
        ("Example/project", "example/project"),
        ("example/project", "Example/Project"),
        ("eXample/pRoject", "EXAMPLE/project"),
    ],
)
def test_repository_case_variants_preserve_issue_identity(
    monkeypatch, operation, requested_repo, server_repo
):
    plan = prepared(monkeypatch)
    identity = {**IDENTITY, "repo": requested_repo}
    after = renewed(plan)
    after["issue_url"] = f"https://api.github.com/repos/{server_repo}/issues/7"
    calls = snapshots(monkeypatch, [[after]], [[after]])
    if operation == "prepare":
        result = heartbeat.prepare(identity, TIME + timedelta(seconds=2))
        assert result["identity"] == identity
        assert result["before"]["updated_at"] == after["updated_at"]
    else:
        plan["identity"] = identity
        result = heartbeat.verify(plan, TIME + timedelta(seconds=2))
        assert result["updated_at"] == after["updated_at"] and result["comment_id"] == 42
    assert calls == [(requested_repo, 7)] * 2


@pytest.mark.parametrize("operation", ["prepare", "verify"])
@pytest.mark.parametrize(
    "issue_url",
    [
        "https://api.github.com/repos/EXAMPLE/other/issues/7",
        "https://api.github.com/repos/someone/PROJECT/issues/7",
        "https://api.github.com/repos/EXAMPLE/PROJECT/issues/8",
        "https://example.com/repos/EXAMPLE/PROJECT/issues/7",
        "https://api.github.com/repos/EXAMPLE/PROJECT/issues/7?other=1",
    ],
)
def test_case_normalization_cannot_accept_another_repository_issue_or_host(
    monkeypatch, operation, issue_url
):
    plan = prepared(monkeypatch)
    after = renewed(plan)
    after["issue_url"] = issue_url
    snapshots(monkeypatch, [[after]], [[after]])
    with pytest.raises(heartbeat.HeartbeatError, match="another issue"):
        if operation == "prepare":
            heartbeat.prepare(dict(IDENTITY), TIME + timedelta(seconds=2))
        else:
            heartbeat.verify(plan, TIME + timedelta(seconds=2))


@pytest.mark.parametrize(
    "change",
    [
        {"generation": 4},
        {"agent_id": "yz-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        {"state": "waiting"},
        {"state": "done"},
        {"heartbeat_seq": 2},
        {"phase": "changed"},
    ],
)
def test_heartbeat_cannot_silently_change_claim_fields(monkeypatch, change):
    plan = prepared(monkeypatch)
    after = comment(heartbeat_seq=1, **{k: v for k, v in change.items() if k != "heartbeat_seq"})
    if "heartbeat_seq" in change:
        after = comment(**change)
    after["updated_at"] = stamp(TIME + timedelta(seconds=1))
    snapshots(monkeypatch, [[after]], [[after]])
    with pytest.raises(heartbeat.HeartbeatError):
        heartbeat.verify(plan, TIME + timedelta(seconds=2))


@pytest.mark.parametrize("state", ["active", "waiting", "handoff", "candidate", "accept"])
def test_other_live_claim_on_a_later_page_blocks_renewal(monkeypatch, state):
    plan = prepared(monkeypatch)
    other = comment(state=state)
    other["id"] = 99
    pages = [[renewed(plan)], [other]]
    snapshots(monkeypatch, pages, pages)
    with pytest.raises(heartbeat.HeartbeatError, match="Sole live active"):
        heartbeat.verify(plan, TIME + timedelta(seconds=2))


@pytest.mark.parametrize("operation", ["prepare", "verify"])
@pytest.mark.parametrize(
    "marker",
    [
        "<!--yeliztli-claim:v1-->",
        "<!-- yeliztli-claim:v1-->",
        "<!--\tyeliztli-claim:v1\t-->",
        "yeliztli-claim:v1",
    ],
)
def test_noncanonical_competing_claim_is_rejected_instead_of_ignored(
    monkeypatch, operation, marker
):
    plan = prepared(monkeypatch)
    other = comment()
    other["id"] = 99
    other["body"] = other["body"].replace(heartbeat.MARKER, marker)
    pages = [[renewed(plan)], [other]]
    snapshots(monkeypatch, pages, pages)
    with pytest.raises(heartbeat.HeartbeatError, match="Malformed claim record"):
        if operation == "prepare":
            heartbeat.prepare(dict(IDENTITY), TIME + timedelta(seconds=2))
        else:
            heartbeat.verify(plan, TIME + timedelta(seconds=2))


def test_multiple_claim_tokens_in_one_comment_fail_closed(monkeypatch):
    original = comment()
    original["body"] += "\n<!--yeliztli-claim:v1-->\n"
    snapshots(monkeypatch, [[original]], [[original]])
    with pytest.raises(heartbeat.HeartbeatError, match="Malformed claim record"):
        heartbeat.prepare(dict(IDENTITY), TIME)


def test_unrelated_comment_does_not_block_a_sole_owner(monkeypatch):
    plan = prepared(monkeypatch)
    unrelated = comment()
    unrelated.update(id=99, body="Public progress note without a claim token.")
    pages = [[unrelated], [renewed(plan)]]
    snapshots(monkeypatch, pages, pages)
    assert heartbeat.verify(plan, TIME + timedelta(seconds=2))["heartbeat_seq"] == 1


def test_done_and_expired_history_does_not_block_a_sole_owner(monkeypatch):
    plan = prepared(monkeypatch)
    done = comment(state="done")
    done["id"] = 98
    old = comment()
    old.update(
        id=99,
        created_at=stamp(TIME - timedelta(hours=3)),
        updated_at=stamp(TIME - timedelta(hours=2)),
    )
    pages = [[done, old], [renewed(plan)]]
    snapshots(monkeypatch, pages, pages)
    assert heartbeat.verify(plan, TIME + timedelta(seconds=2))["heartbeat_seq"] == 1


def test_mismatching_complete_snapshots_do_not_prove_renewal(monkeypatch):
    plan = prepared(monkeypatch)
    snapshots(monkeypatch, [[renewed(plan)]], [[comment()]])
    with pytest.raises(heartbeat.HeartbeatError, match="snapshots disagree"):
        heartbeat.verify(plan, TIME + timedelta(seconds=2))


@pytest.mark.parametrize("pages", [[], [[]], [[None]], [[comment(), comment()]]])
def test_missing_or_duplicate_evidence_fails_closed(monkeypatch, pages):
    snapshots(monkeypatch, pages, pages)
    with pytest.raises(heartbeat.HeartbeatError):
        heartbeat.prepare(dict(IDENTITY), TIME)


@pytest.mark.parametrize("counter", [True, -1, "1", None])
def test_malformed_counter_is_not_reset(monkeypatch, counter):
    original = comment(heartbeat_seq=counter)
    snapshots(monkeypatch, [[original]], [[original]])
    with pytest.raises(heartbeat.HeartbeatError, match="counter"):
        heartbeat.prepare(dict(IDENTITY), TIME)


def test_fetch_uses_complete_read_only_pagination(monkeypatch):
    calls = []
    pages = [[comment()], []]

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, json.dumps(pages), "")

    monkeypatch.setattr(heartbeat.subprocess, "run", run)
    assert heartbeat.fetch_pages("example/project", 7) == pages
    assert calls == [
        (
            [
                "gh",
                "api",
                "--hostname",
                "github.com",
                "--method",
                "GET",
                "repos/example/project/issues/7/comments?per_page=100",
                "--paginate",
                "--slurp",
            ],
            {"check": True, "capture_output": True, "text": True, "timeout": 60},
        )
    ]


def test_partial_fetch_failure_cannot_return_success(monkeypatch):
    def run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["gh"], output=json.dumps([[comment()]]))

    monkeypatch.setattr(heartbeat.subprocess, "run", run)
    with pytest.raises(heartbeat.HeartbeatError, match="every comment page"):
        heartbeat.prepare(dict(IDENTITY), TIME)


def test_cli_prepare_then_verify_uses_the_same_production_path(monkeypatch, tmp_path, capsys):
    clock = {"now": TIME + timedelta(seconds=1)}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr(heartbeat, "datetime", Clock)
    now = TIME
    original = comment()
    original["created_at"] = stamp(now - timedelta(minutes=2))
    original["updated_at"] = stamp(now - timedelta(minutes=1))
    path = tmp_path / "heartbeat.json"
    snapshots(monkeypatch, [[original]], [[original]])
    args = ["claim_heartbeat.py", "prepare", "--plan", str(path)]
    for key, value in IDENTITY.items():
        args.extend(["--" + key.replace("_", "-"), str(value)])
    monkeypatch.setattr(sys, "argv", args)
    assert heartbeat.main() == 0
    plan = json.loads(path.read_text())
    assert "no GitHub write occurred" in capsys.readouterr().out
    after = copy.deepcopy(original)
    after.update(body=plan["request"]["body"], updated_at=stamp(now + timedelta(seconds=2)))
    clock["now"] = now + timedelta(seconds=3)
    snapshots(monkeypatch, [[after]], [[after]])
    monkeypatch.setattr(sys, "argv", ["claim_heartbeat.py", "verify", "--plan", str(path)])
    assert heartbeat.main() == 0
    assert json.loads(capsys.readouterr().out)["updated_at"] == stamp(now + timedelta(seconds=2))
    snapshots(monkeypatch, [[original]], [[original]])
    assert heartbeat.main() == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "timestamp did not advance" in captured.err


def test_expiry_is_checked_after_slow_complete_reads(monkeypatch):
    clock = {"now": TIME}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    calls = []

    def fetch(repo, issue):
        calls.append((repo, issue))
        if len(calls) == 2:
            clock["now"] = TIME + timedelta(minutes=90)
        return [[comment()]]

    monkeypatch.setattr(heartbeat, "datetime", Clock)
    monkeypatch.setattr(heartbeat, "fetch_pages", fetch)
    with pytest.raises(heartbeat.HeartbeatError, match="expired"):
        heartbeat.prepare(dict(IDENTITY))
    assert len(calls) == 2
