"""Regression tests for issue #1750's fail-closed production LAI gate."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

from backend.services.lai_production_coverage import (
    POLICY_UNAVAILABLE_REASON,
    LAICoveragePolicyUnavailableError,
    LAIProductionCoverageDecision,
    decode_lai_insufficient_data_reason,
    encode_lai_insufficient_data_reason,
    get_lai_production_coverage_decision,
    lai_result_matches_current_coverage_policy,
    require_lai_production_coverage_policy,
)


def test_released_bundle_has_one_stable_fail_closed_decision() -> None:
    decision = get_lai_production_coverage_decision()

    assert decision.allowed is False
    assert decision.confirmed_policy_id is None
    assert decision.production_qualified is False
    assert decision.reason == POLICY_UNAVAILABLE_REASON
    assert decision.reason.as_dict() == {
        "code": "lai_coverage_policy_unavailable",
        "category": "insufficient_validation_data",
        "message": (
            "Chromosome painting is unavailable because the current LAI bundle has no "
            "final-confirmed minimum-coverage policy. Tier 1 ancestry remains available."
        ),
        "retryable": False,
    }


def test_decision_accepts_no_runtime_threshold_or_configuration() -> None:
    """No environment/config input may turn missing scientific evidence into a call."""
    assert not inspect.signature(get_lai_production_coverage_decision).parameters
    assert set(POLICY_UNAVAILABLE_REASON.as_dict()) == {
        "code",
        "category",
        "message",
        "retryable",
    }


def test_require_policy_raises_typed_reason() -> None:
    with pytest.raises(LAICoveragePolicyUnavailableError) as caught:
        require_lai_production_coverage_policy()

    assert caught.value.reason == POLICY_UNAVAILABLE_REASON
    assert str(caught.value) == POLICY_UNAVAILABLE_REASON.message


@pytest.mark.parametrize(
    "decision",
    [
        LAIProductionCoverageDecision(allowed=True, confirmed_policy_id=None, reason=None),
        LAIProductionCoverageDecision(
            allowed=True,
            confirmed_policy_id="policy-id",
            reason=POLICY_UNAVAILABLE_REASON,
        ),
    ],
)
def test_internally_inconsistent_future_decision_remains_fail_closed(decision) -> None:
    assert decision.production_qualified is False
    with (
        patch(
            "backend.services.lai_production_coverage._CURRENT_DECISION",
            decision,
        ),
        pytest.raises((RuntimeError, LAICoveragePolicyUnavailableError)),
    ):
        require_lai_production_coverage_policy()


@pytest.mark.parametrize(
    "metadata",
    [None, {}, {"lai_coverage_policy_id": "invented-policy"}],
)
def test_unqualified_result_metadata_never_matches_current_policy(metadata: object) -> None:
    assert lai_result_matches_current_coverage_policy(metadata) is False


def test_job_error_reason_round_trips_canonically() -> None:
    encoded = encode_lai_insufficient_data_reason(POLICY_UNAVAILABLE_REASON)

    assert encoded.startswith("lai_insufficient_data:")
    assert decode_lai_insufficient_data_reason(encoded) == POLICY_UNAVAILABLE_REASON


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "ordinary operational failure",
        "lai_insufficient_data:not-json",
        'lai_insufficient_data:{"code":"missing-fields"}',
        ('lai_insufficient_data:{"category":"x","code":"x","message":"x","retryable":"false"}'),
    ],
)
def test_unrecognized_job_errors_remain_unstructured(value: str | None) -> None:
    assert decode_lai_insufficient_data_reason(value) is None


def test_public_analysis_refuses_before_any_runtime_or_database_side_effect() -> None:
    from backend.analysis.lai import run_lai_analysis

    assert set(inspect.signature(run_lai_analysis).parameters) == {
        "sample_id",
        "sample_engine",
        "progress_callback",
    }

    with (
        patch("backend.analysis.lai.get_settings") as get_settings,
        patch("backend.analysis.lai._ensure_lai_tables") as ensure_tables,
        patch("backend.analysis.lai._store_lai_results") as store_results,
        pytest.raises(LAICoveragePolicyUnavailableError),
    ):
        run_lai_analysis(sample_id=7, sample_engine=MagicMock())

    get_settings.assert_not_called()
    ensure_tables.assert_not_called()
    store_results.assert_not_called()
