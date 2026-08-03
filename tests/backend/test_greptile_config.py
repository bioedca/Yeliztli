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


def _config() -> dict[str, Any]:
    config = json.loads(GREPTILE_CONFIG.read_text(encoding="utf-8"))
    assert isinstance(config, dict), "greptile.json must be a JSON object"
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


def test_every_configured_key_is_a_documented_greptile_key() -> None:
    unknown = sorted(set(_config()) - DOCUMENTED_KEYS)
    assert not unknown, (
        f"unknown greptile.json key(s) {unknown}: Greptile ignores keys it does "
        f"not recognise, so a misspelled guard silently does nothing"
    )
