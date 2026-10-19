"""Decide and explain the ``CI Required`` aggregate check (#2289).

``CI Required`` is the single required status for merging, so a red one has to
say *why* at a glance. It used to print only ``A required CI job reported
'cancelled'.`` from ``join(needs.*.result, ' ')``, which drops the job name and
cannot tell causes apart. On PR #2238 (run 31261192144) two cancelled backend
legs had different causes in their own annotations:

* ``The job has exceeded the maximum execution time of 30m0s``, a time-limit
  kill of a suite that was still passing;
* ``Canceling since a higher priority waiting request for CI-refs/pull/2238/merge
  exists``, a newer push superseding the run.

Both read identically, and as a broken test, so they were re-run in turn rather
than diagnosed.

The verdict is unchanged and comes only from ``needs``: every required job must
be ``success`` or ``skipped``, and anything else fails the check. The run's job
listing and check-run annotations only *explain* that verdict. They name the
exact matrix leg and classify each cancellation. If they cannot be read, the
check still fails or passes exactly as before and says the detail is missing.

Usage (``.github/workflows/ci.yml``, ``ci-required`` job)::

    NEEDS_JSON='${{ toJSON(needs) }}' python3 scripts/ci_required_summary.py

``GITHUB_TOKEN``, ``GITHUB_REPOSITORY``, ``GITHUB_RUN_ID``,
``GITHUB_RUN_ATTEMPT`` and ``GITHUB_API_URL`` locate the run. Reading it needs
``actions: read`` (jobs) and ``checks: read`` (annotations). Standard library
only, because the job installs nothing.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections.abc import Callable, Iterable, Mapping

PASSING_RESULTS = frozenset({"success", "skipped"})
FAILED_CONCLUSIONS = frozenset({"failure", "cancelled"})

# Annotation text GitHub writes on a cancelled job, as observed on run 31261192144.
_TIMEOUT_MARKER = "exceeded the maximum execution time"
_SUPERSEDED_MARKER = "higher priority waiting request"

_PAGE_SIZE = 100

Leg = Mapping[str, object]
Fetch = Callable[[str], object]


def _escape(text: str) -> str:
    """Escape text for a workflow command, so annotation text cannot inject one."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def blocking_required_jobs(needs: Mapping[str, Mapping[str, object]]) -> list[tuple[str, str]]:
    """Return ``(job, result)`` for every required job that blocks the merge."""
    return sorted(
        (job, str(info.get("result")))
        for job, info in needs.items()
        if info.get("result") not in PASSING_RESULTS
    )


def describe_leg(leg: Leg) -> str:
    """Explain one failed or cancelled job of this run from its own annotations."""
    name = str(leg.get("name"))
    if leg.get("conclusion") == "failure":
        return f"{name}: failed."
    messages = [str(message) for message in leg.get("annotations") or ()]
    for message in messages:
        if _TIMEOUT_MARKER in message:
            return (
                f"{name}: timed out ({message}). The job was killed at its time limit; "
                "this is not a test failure."
            )
    for message in messages:
        if _SUPERSEDED_MARKER in message:
            return f"{name}: superseded by a newer run ({message})."
    return f"{name}: cancelled."


def summarise(
    needs: Mapping[str, Mapping[str, object]],
    legs: Iterable[Leg] | None,
) -> tuple[int, list[str]]:
    """Return the exit status and the lines to print for ``CI Required``."""
    lines = ["Aggregated job results:"]
    lines += [f"  {job}: {info.get('result')}" for job, info in sorted(needs.items())]
    blocking = blocking_required_jobs(needs)
    if not blocking:
        lines.append("All required jobs passed or were skipped.")
        return 0, lines

    for job, result in blocking:
        lines.append(f"::error title=CI Required::Required job '{job}' reported '{result}'.")
    if legs is None:
        lines.append(
            "::warning title=CI Required::This run's jobs could not be read, so the "
            "failed or cancelled leg and its cause are not shown."
        )
        return 1, lines
    for leg in legs:
        if leg.get("conclusion") in FAILED_CONCLUSIONS:
            lines.append(f"::error title=CI Required::{_escape(describe_leg(leg))}")
    return 1, lines


def _api_get(url: str, token: str) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https API base
        return json.load(response)


def _all_pages(fetch: Fetch, url: str, items: Callable[[object], list]) -> list:
    """Follow ``page=`` until a short page, so nothing past the first page is missed."""
    collected: list = []
    page = 1
    while True:
        batch = items(fetch(f"{url}?per_page={_PAGE_SIZE}&page={page}"))
        collected += batch
        if len(batch) < _PAGE_SIZE:
            return collected
        page += 1


def fetch_failed_legs(
    api: str, repository: str, run_id: str, attempt: str, fetch: Fetch
) -> list[Leg]:
    """List this run attempt's failed or cancelled jobs with their annotation messages."""
    jobs = _all_pages(
        fetch,
        f"{api}/repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs",
        lambda payload: payload["jobs"] if isinstance(payload, dict) else [],
    )
    legs: list[Leg] = []
    for job in jobs:
        if job.get("conclusion") not in FAILED_CONCLUSIONS:
            continue
        annotations = _all_pages(
            fetch,
            f"{api}/repos/{repository}/check-runs/{job['id']}/annotations",
            lambda payload: payload if isinstance(payload, list) else [],
        )
        legs.append(
            {
                "name": job.get("name"),
                "conclusion": job.get("conclusion"),
                "annotations": [
                    annotation.get("message", "")
                    for annotation in annotations
                    if isinstance(annotation, dict)
                ],
            }
        )
    return legs


def main(environ: Mapping[str, str] = os.environ, fetch: Fetch | None = None) -> int:
    try:
        needs = json.loads(environ["NEEDS_JSON"])
        if not isinstance(needs, dict) or not all(isinstance(v, dict) for v in needs.values()):
            raise ValueError("NEEDS_JSON is not an object of job results")
    except (KeyError, ValueError) as exc:
        print(
            f"::error title=CI Required::Cannot read the required job results: {_escape(str(exc))}"
        )
        return 1

    legs: list[Leg] | None = None
    if blocking_required_jobs(needs):
        token = environ.get("GITHUB_TOKEN", "")
        try:
            legs = fetch_failed_legs(
                environ.get("GITHUB_API_URL", "https://api.github.com"),
                environ["GITHUB_REPOSITORY"],
                environ["GITHUB_RUN_ID"],
                environ.get("GITHUB_RUN_ATTEMPT", "1"),
                fetch or (lambda url: _api_get(url, token)),
            )
        except Exception as exc:  # noqa: BLE001 - the detail is best-effort; the verdict is not
            print(f"Could not read this run's jobs: {_escape(type(exc).__name__)}")

    status, lines = summarise(needs, legs)
    print("\n".join(lines))
    return status


if __name__ == "__main__":
    sys.exit(main())
