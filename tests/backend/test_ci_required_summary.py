"""``CI Required`` names the failed or cancelled job and its cause (#2289).

The annotation strings are verbatim from run 31261192144 (PR #2238 at
de8059ec), where two cancelled backend legs had different causes but the old
aggregator reported both as ``A required CI job reported 'cancelled'.``
"""

from __future__ import annotations

import json

import pytest

from scripts.ci_required_summary import (
    blocking_required_jobs,
    describe_leg,
    fetch_failed_legs,
    main,
    summarise,
)

TIMEOUT = "The job has exceeded the maximum execution time of 30m0s"
SUPERSEDED = "Canceling since a higher priority waiting request for CI-refs/pull/2238/merge exists"
CANCELED = "The operation was canceled."
BACKEND_LEG = "Backend Tests (Linux x86_64, py3.12)"
MAIN_JOBS_URL = (
    "https://api.example/repos/owner/repo/actions/runs/7/attempts/1/jobs?per_page=100&page=1"
)
MAIN_ANNOTATIONS_URL = (
    "https://api.example/repos/owner/repo/check-runs/9/annotations?per_page=100&page=1"
)


def _needs(**results: str) -> dict[str, dict[str, object]]:
    return {
        job.replace("_", "-"): {"result": result, "outputs": {}} for job, result in results.items()
    }


def test_passing_and_skipped_jobs_pass() -> None:
    status, lines = summarise(_needs(lint="success", test_e2e="skipped"), legs=[])

    assert status == 0
    assert lines[-1] == "All required jobs passed or were skipped."
    assert not [line for line in lines if line.startswith("::error")]


@pytest.mark.parametrize("result", ["failure", "cancelled", "neutral", "None"])
def test_any_other_result_blocks_and_is_named(result: str) -> None:
    """The verdict is unchanged, and an unexpected result fails closed."""
    status, lines = summarise(_needs(lint="success", test_backend=result), legs=[])

    assert status == 1
    assert f"::error title=CI Required::Required job 'test-backend' reported '{result}'." in lines
    assert blocking_required_jobs(_needs(lint="success", test_backend=result)) == [
        ("test-backend", result)
    ]


def test_a_timed_out_leg_is_named_as_a_time_limit_kill() -> None:
    status, lines = summarise(
        _needs(test_backend="cancelled"),
        legs=[
            {"name": BACKEND_LEG, "conclusion": "cancelled", "annotations": [TIMEOUT, CANCELED]}
        ],
    )

    assert status == 1
    assert (
        f"::error title=CI Required::{BACKEND_LEG}: timed out ({TIMEOUT}). The job was killed at "
        "its time limit; this is not a test failure."
    ) in lines


def test_a_superseded_leg_is_not_reported_as_a_timeout() -> None:
    line = describe_leg(
        {"name": BACKEND_LEG, "conclusion": "cancelled", "annotations": [SUPERSEDED, CANCELED]}
    )

    assert line == f"{BACKEND_LEG}: superseded by a newer run ({SUPERSEDED})."
    assert "timed out" not in line


def test_failed_and_plain_cancelled_legs_are_named_without_a_guessed_cause() -> None:
    assert describe_leg({"name": "Lint", "conclusion": "failure", "annotations": [TIMEOUT]}) == (
        "Lint: failed."
    )
    assert describe_leg(
        {"name": "Lint", "conclusion": "cancelled", "annotations": [CANCELED]}
    ) == ("Lint: cancelled.")


def test_successful_legs_are_not_listed() -> None:
    _, lines = summarise(
        _needs(test_backend="failure"),
        legs=[
            {"name": "Backend Tests (shard 1/3)", "conclusion": "success", "annotations": []},
            {"name": "Backend Tests (shard 2/3)", "conclusion": "failure", "annotations": []},
        ],
    )

    leg_lines = [line for line in lines if "Backend Tests (shard" in line]
    assert leg_lines == ["::error title=CI Required::Backend Tests (shard 2/3): failed."]


def test_unreadable_run_still_fails_and_says_the_detail_is_missing() -> None:
    status, lines = summarise(_needs(test_backend="cancelled"), legs=None)

    assert status == 1
    assert "::error title=CI Required::Required job 'test-backend' reported 'cancelled'." in lines
    assert any(line.startswith("::warning title=CI Required::") for line in lines)


def test_annotation_text_cannot_inject_a_workflow_command() -> None:
    """A PR's own step output can create annotations, so echoed text is escaped."""
    hostile = f"{TIMEOUT}\n::notice::forged%"
    _, lines = summarise(
        _needs(test_backend="cancelled"),
        legs=[{"name": BACKEND_LEG, "conclusion": "cancelled", "annotations": [hostile]}],
    )

    leg_line = next(line for line in lines if BACKEND_LEG in line)
    assert "\n" not in leg_line
    assert "%0A::notice::forged%25" in leg_line


def _fake_api(pages: dict[str, object]) -> tuple[list[str], object]:
    requested: list[str] = []

    def fetch(url: str) -> object:
        requested.append(url)
        return pages[url]

    return requested, fetch


def test_fetch_reads_annotations_only_for_failed_or_cancelled_jobs() -> None:
    api, repo = "https://api.example", "owner/repo"
    jobs_url = f"{api}/repos/{repo}/actions/runs/7/attempts/2/jobs?per_page=100&page=1"
    requested, fetch = _fake_api(
        {
            jobs_url: {
                "jobs": [
                    {"id": 1, "name": "Lint", "conclusion": "success"},
                    {"id": 2, "name": BACKEND_LEG, "conclusion": "cancelled"},
                    {"id": 3, "name": "CI Required", "conclusion": None},
                ]
            },
            f"{api}/repos/{repo}/check-runs/2/annotations?per_page=100&page=1": [
                {"message": TIMEOUT},
                {"message": CANCELED},
            ],
        }
    )

    legs = fetch_failed_legs(api, repo, "7", "2", fetch)

    assert legs == [
        {"name": BACKEND_LEG, "conclusion": "cancelled", "annotations": [TIMEOUT, CANCELED]}
    ]
    assert requested == [
        jobs_url,
        f"{api}/repos/{repo}/check-runs/2/annotations?per_page=100&page=1",
    ]


def test_fetch_follows_a_full_page_of_jobs() -> None:
    api, repo = "https://api.example", "owner/repo"
    first = [{"id": n, "name": f"job {n}", "conclusion": "success"} for n in range(100)]
    second = [{"id": 500, "name": BACKEND_LEG, "conclusion": "failure"}]
    requested, fetch = _fake_api(
        {
            f"{api}/repos/{repo}/actions/runs/7/attempts/1/jobs?per_page=100&page=1": {
                "jobs": first
            },
            f"{api}/repos/{repo}/actions/runs/7/attempts/1/jobs?per_page=100&page=2": {
                "jobs": second
            },
            f"{api}/repos/{repo}/check-runs/500/annotations?per_page=100&page=1": [],
        }
    )

    legs = fetch_failed_legs(api, repo, "7", "1", fetch)

    assert [leg["name"] for leg in legs] == [BACKEND_LEG]
    assert len(requested) == 3


def _environ(needs: dict[str, dict[str, object]]) -> dict[str, str]:
    return {
        "NEEDS_JSON": json.dumps(needs),
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_RUN_ID": "7",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_API_URL": "https://api.example",
        "GITHUB_TOKEN": "t",
    }


def test_main_names_the_timed_out_leg(capsys: pytest.CaptureFixture[str]) -> None:
    _, fetch = _fake_api(
        {
            MAIN_JOBS_URL: {"jobs": [{"id": 9, "name": BACKEND_LEG, "conclusion": "cancelled"}]},
            MAIN_ANNOTATIONS_URL: [{"message": TIMEOUT}],
        }
    )

    status = main(_environ(_needs(lint="success", test_backend="cancelled")), fetch=fetch)

    out = capsys.readouterr().out
    assert status == 1
    assert "Required job 'test-backend' reported 'cancelled'." in out
    assert f"{BACKEND_LEG}: timed out ({TIMEOUT})." in out


def test_main_keeps_the_verdict_when_the_api_fails(capsys: pytest.CaptureFixture[str]) -> None:
    def broken(url: str) -> object:
        raise OSError("network unreachable")

    status = main(_environ(_needs(test_backend="failure")), fetch=broken)

    out = capsys.readouterr().out
    assert status == 1
    assert "Required job 'test-backend' reported 'failure'." in out
    assert "::warning title=CI Required::" in out


def test_main_does_not_call_the_api_when_everything_passed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unexpected(url: str) -> object:
        raise AssertionError(f"no API call expected, got {url}")

    assert main(_environ(_needs(lint="success", test_e2e="skipped")), fetch=unexpected) == 0
    assert "All required jobs passed or were skipped." in capsys.readouterr().out


@pytest.mark.parametrize("needs_json", [None, "not json", "[]", '{"lint": "success"}'])
def test_main_fails_closed_on_unreadable_needs(
    needs_json: str | None, capsys: pytest.CaptureFixture[str]
) -> None:
    environ = _environ(_needs(lint="success"))
    if needs_json is None:
        del environ["NEEDS_JSON"]
    else:
        environ["NEEDS_JSON"] = needs_json

    assert main(environ, fetch=lambda url: {}) == 1
    assert (
        "::error title=CI Required::Cannot read the required job results"
        in capsys.readouterr().out
    )


def test_fetch_follows_a_full_page_of_annotations() -> None:
    """A timeout annotation after the first 100 is still found."""
    api, repo = "https://api.example", "owner/repo"
    annotations = f"{api}/repos/{repo}/check-runs/2/annotations?per_page=100"
    first = [{"message": f"step warning {n}"} for n in range(100)]
    requested, fetch = _fake_api(
        {
            f"{api}/repos/{repo}/actions/runs/7/attempts/1/jobs?per_page=100&page=1": {
                "jobs": [{"id": 2, "name": BACKEND_LEG, "conclusion": "cancelled"}]
            },
            f"{annotations}&page=1": first,
            f"{annotations}&page=2": [{"message": TIMEOUT}],
        }
    )

    [leg] = fetch_failed_legs(api, repo, "7", "1", fetch)

    assert requested[-2:] == [f"{annotations}&page=1", f"{annotations}&page=2"]
    assert describe_leg(leg).startswith(f"{BACKEND_LEG}: timed out ({TIMEOUT})")
