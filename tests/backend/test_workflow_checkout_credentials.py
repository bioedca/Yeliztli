"""Workflow checkouts do not leave the token in .git/config (#2080).

``actions/checkout`` persists its token into the local git config unless told
not to, where any later step can read it. The CI, release, CodeQL and docs jobs
then run ``pip install`` / ``npm ci`` and the test suites, i.e. third-party code.
None of them pushes or fetches with that token (the repository is public, so an
anonymous fetch works), so every checkout opts out. This keeps a new checkout
from silently reintroducing the default.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

# Checkouts deliberately left on the default, with the reason.
_ALLOWED_PERSISTED = {
    # Runs inside the Copilot coding agent's environment, which then works in
    # that checkout; GitHub documents no contract for how the agent uses its git
    # credentials, and CI cannot exercise the agent. Its token is read-only.
    "copilot-setup-steps.yml",
}


def _checkout_steps(workflows: Path = _WORKFLOWS) -> list[tuple[str, str, dict]]:
    # GitHub Actions reads both extensions.
    paths = sorted([*workflows.glob("*.yml"), *workflows.glob("*.yaml")])
    found = []
    for path in paths:
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in (workflow.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                if str(step.get("uses", "")).startswith("actions/checkout@"):
                    found.append((path.name, job_name, step))
    return found


def _offenders(steps: list[tuple[str, str, dict]]) -> list[str]:
    return [
        f"{workflow}:{job}"
        for workflow, job, step in steps
        if workflow not in _ALLOWED_PERSISTED
        and (step.get("with") or {}).get("persist-credentials") is not False
    ]


def test_every_checkout_opts_out_of_persisted_credentials() -> None:
    steps = _checkout_steps()
    assert len(steps) > 20, f"found only {len(steps)} checkout steps; is the scan broken?"
    offenders = _offenders(steps)
    assert not offenders, (
        f"actions/checkout without `persist-credentials: false` in {offenders}. Set it, "
        "or add the workflow to _ALLOWED_PERSISTED with the reason it needs git "
        "credentials (#2080)."
    )


def test_scan_covers_both_workflow_extensions(tmp_path: Path) -> None:
    checkout = (
        "jobs:\n  build:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - uses: actions/checkout@v6\n"
    )
    (tmp_path / "a.yml").write_text(checkout, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(checkout, encoding="utf-8")
    assert _offenders(_checkout_steps(tmp_path)) == ["a.yml:build", "b.yaml:build"]


def test_allowlisted_workflows_still_exist() -> None:
    names = {workflow for workflow, _, _ in _checkout_steps()}
    assert _ALLOWED_PERSISTED <= names, f"stale allowlist entries: {_ALLOWED_PERSISTED - names}"
