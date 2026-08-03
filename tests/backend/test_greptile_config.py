"""Guard the manual-only Greptile review configuration.

Greptile meters *completed reviews* and this repository has a 16-review monthly
allowance, so an automatic trigger is a budget defect rather than a preference:
one unguarded day of normal fleet activity spends the whole month on pull
requests that never selected Greptile. See ``docs/develop/greptile-reviews.md``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GREPTILE_CONFIG = REPO_ROOT / "greptile.json"

# Keys Greptile documents for ``greptile.json``. Greptile silently ignores a key
# it cannot parse, so a typo or an undocumented spelling does not fail loudly --
# it re-enables whatever the key was meant to disable. One public repository
# ships the undocumented plural ``skipReviews``; this set makes that class of
# mistake a test failure instead of a silent month of spend.
DOCUMENTED_KEYS = frozenset(
    {
        "autoApprove",
        "commentTypes",
        "confidenceScoreSection",
        "context",
        "customContext",
        "disabledLabels",
        "excludeAuthors",
        "excludeBranches",
        "fileChangeLimit",
        "fixWithAI",
        "hideFooter",
        "ignoreKeywords",
        "ignorePatterns",
        "includeAuthors",
        "includeBranches",
        "includeConfidenceScore",
        "includeIssuesTable",
        "includeKeywords",
        "includeSequenceDiagram",
        "instructions",
        "issuesTableSection",
        "labels",
        "patternRepositories",
        "shouldUpdateDescription",
        "skipReview",
        "statusCheck",
        "statusCommentsEnabled",
        "strictness",
        "summarySection",
        "triggerOnDrafts",
        "triggerOnUpdates",
        "updateSummaryOnly",
    }
)


def _effective_config_path(root: Path = REPO_ROOT) -> Path | None:
    """Return the config Greptile actually obeys, or ``None`` if there is none.

    It is the presence of the ``.greptile/`` **folder** — not of
    ``.greptile/config.json`` — that makes ``greptile.json`` ignored entirely.
    So a rules-only folder (``rules.md`` with no ``config.json``) leaves the
    repository with no trigger policy at all while the root file still sits
    there looking authoritative. Return ``None`` for that case so it fails
    loudly instead of passing against a document Greptile never reads.
    """
    folder = root / ".greptile"
    if folder.is_dir():
        folder_config = folder / "config.json"
        return folder_config if folder_config.is_file() else None
    return root / "greptile.json"


def _config() -> dict[str, Any]:
    path = _effective_config_path()
    assert path is not None, (
        ".greptile/ exists without a config.json: its presence makes greptile.json "
        "ignored entirely, so the repository has no manual-only policy at all"
    )
    config = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(config, dict), f"{path.name} must be a JSON object"
    return config


def test_greptile_config_exists_at_the_repository_root() -> None:
    # Greptile reads this file from the repository root of the PR's source
    # branch; anywhere else it is not read at all.
    assert GREPTILE_CONFIG.is_file(), (
        "greptile.json must exist at the repository root or Greptile reverts to "
        "reviewing every opened pull request automatically"
    )
    assert _config(), "greptile.json must not be empty"


def test_automatic_reviews_are_skipped() -> None:
    assert _config().get("skipReview") == "AUTOMATIC", (
        'skipReview must be exactly "AUTOMATIC"; any other value (including the '
        "undocumented plural key skipReviews) leaves automatic review enabled"
    )


@pytest.mark.parametrize("key", ["labels", "includeAuthors", "includeBranches", "includeKeywords"])
def test_no_pr_filter_can_block_the_manual_trigger(key: str) -> None:
    # Automatic review is already blocked twice over: the org dashboard sets
    # fileChangeLimit to 1, and skipReview pins manual-only mode. An allow-list
    # style PR filter adds no third guarantee but risks refusing the manual
    # @greptile-apps trigger, which is the one path that must keep working --
    # Greptile's docs contradict themselves on whether a mention overrides these
    # filters. Losing manual review is worse than an occasional stray credit.
    assert key not in _config(), (
        f"{key} must stay unset: an allow-list PR filter may also refuse the "
        f"manual trigger, and manual review is the only way to spend the budget "
        f"deliberately"
    )


@pytest.mark.parametrize("key", ["triggerOnUpdates", "triggerOnDrafts"])
def test_extra_credit_consuming_triggers_stay_disabled(key: str) -> None:
    assert _config().get(key) is False, (
        f"{key} must stay false: each additional trigger it enables is a "
        f"separate billed review against the 16/month allowance"
    )


def test_dependabot_pull_requests_never_spend_the_budget() -> None:
    # Dependabot opens pull requests on its own schedule. Without this exclusion a
    # dependency-bump wave could spend the month before a human sees it, and the
    # key is silently ignored if misspelled, so assert the concrete value.
    excluded = _config().get("excludeAuthors")
    assert isinstance(excluded, list), "excludeAuthors must be a list"
    assert "dependabot[bot]" in excluded, (
        "excludeAuthors must exclude dependabot[bot]; a dependency-bump wave would "
        "otherwise spend the monthly review budget unattended"
    )


def test_the_review_stays_a_provider_authored_artifact() -> None:
    # With shouldUpdateDescription true, Greptile rewrites the PR body instead of
    # posting a review, leaving nothing provider-authored for a route validator.
    assert _config().get("shouldUpdateDescription") is False, (
        "shouldUpdateDescription must stay false or Greptile leaves no "
        "provider-authored artifact to verify"
    )


def _manual_only_violations(config: dict[str, Any]) -> list[str]:
    """Return the ways ``config`` fails to pin manual-only review."""
    violations: list[str] = []
    if config.get("skipReview") != "AUTOMATIC":
        violations.append('skipReview must be exactly "AUTOMATIC"')
    violations.extend(
        f"{key} must be false"
        for key in ("triggerOnUpdates", "triggerOnDrafts")
        if config.get(key) is not False
    )
    return violations


@pytest.mark.parametrize(
    ("config", "expected_clean"),
    [
        ({"skipReview": "AUTOMATIC", "triggerOnUpdates": False, "triggerOnDrafts": False}, True),
        ({"skipReview": "AUTO", "triggerOnUpdates": False, "triggerOnDrafts": False}, False),
        ({"triggerOnUpdates": False, "triggerOnDrafts": False}, False),
        ({"skipReview": "AUTOMATIC", "triggerOnUpdates": True, "triggerOnDrafts": False}, False),
        ({"skipReview": "AUTOMATIC", "triggerOnUpdates": False, "triggerOnDrafts": True}, False),
    ],
)
def test_manual_only_checker_detects_a_weakened_config(
    config: dict[str, Any], expected_clean: bool
) -> None:
    assert (_manual_only_violations(config) == []) is expected_clean


def test_the_effective_config_follows_greptile_precedence(tmp_path: Path) -> None:
    # Every policy assertion in this module reads _config(), so this resolution
    # is what makes them apply to the document Greptile obeys. If it silently
    # kept returning the root file, a shadow config could weaken the guard while
    # the whole suite stayed green against a file Greptile ignores.
    (tmp_path / "greptile.json").write_text("{}", encoding="utf-8")
    assert _effective_config_path(tmp_path) == tmp_path / "greptile.json"

    # A rules-only folder still shadows the root file, leaving no trigger policy.
    (tmp_path / ".greptile").mkdir()
    (tmp_path / ".greptile" / "rules.md").write_text("review carefully", encoding="utf-8")
    assert _effective_config_path(tmp_path) is None

    (tmp_path / ".greptile" / "config.json").write_text("{}", encoding="utf-8")
    assert _effective_config_path(tmp_path) == tmp_path / ".greptile" / "config.json"


def test_every_configured_key_is_a_documented_greptile_key() -> None:
    unknown = sorted(set(_config()) - DOCUMENTED_KEYS)
    assert not unknown, (
        f"unknown greptile.json key(s) {unknown}: Greptile ignores keys it does "
        f"not recognise, so a misspelled guard silently does nothing"
    )
