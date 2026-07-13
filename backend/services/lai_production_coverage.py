"""Fail-closed production policy for LAI chromosome painting.

The calibration workflow may issue a positive minimum-coverage policy only
after founder-complete calibration and one-shot final confirmation.  The
current LAI bundle has no such policy, so production must expose a structured
no-call instead of reporting unqualified local-ancestry estimates.

This module deliberately contains no numeric fallback and no configuration
override.  A future, separately reviewed bundle release may replace the
constant decision only after it can authenticate and evaluate a confirmed
policy artifact.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass

import sqlalchemy as sa

_ENCODED_REASON_PREFIX = "lai_insufficient_data:"
UNQUALIFIED_LAI_FINDING_CATEGORY = "local_ancestry"


@dataclass(frozen=True, slots=True)
class LAIInsufficientDataReason:
    """Stable machine-readable reason that chromosome painting is a no-call."""

    code: str
    category: str
    message: str
    retryable: bool

    def as_dict(self) -> dict[str, str | bool]:
        """Return the JSON-safe API representation."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LAIProductionCoverageDecision:
    """Current production eligibility and its fail-closed reason, if any."""

    allowed: bool
    confirmed_policy_id: str | None
    reason: LAIInsufficientDataReason | None

    @property
    def production_qualified(self) -> bool:
        """Whether all invariants required to expose production LAI hold."""
        return self.allowed and bool(self.confirmed_policy_id) and self.reason is None


POLICY_UNAVAILABLE_REASON = LAIInsufficientDataReason(
    code="lai_coverage_policy_unavailable",
    category="insufficient_validation_data",
    message=(
        "Chromosome painting is unavailable because the current LAI bundle has no "
        "final-confirmed minimum-coverage policy. Tier 1 ancestry remains available."
    ),
    retryable=False,
)

_CURRENT_DECISION = LAIProductionCoverageDecision(
    allowed=False,
    confirmed_policy_id=None,
    reason=POLICY_UNAVAILABLE_REASON,
)


class LAICoveragePolicyUnavailableError(RuntimeError):
    """Raised when production LAI is attempted without a confirmed policy."""

    def __init__(self, reason: LAIInsufficientDataReason = POLICY_UNAVAILABLE_REASON) -> None:
        self.reason = reason
        super().__init__(reason.message)


def get_lai_production_coverage_decision() -> LAIProductionCoverageDecision:
    """Return the immutable decision for the released production bundle."""
    return _CURRENT_DECISION


def require_lai_production_coverage_policy() -> None:
    """Refuse production LAI unless a final-confirmed policy is available."""
    decision = get_lai_production_coverage_decision()
    if not decision.production_qualified:
        if decision.reason is None:
            raise RuntimeError("LAI production coverage gate is not internally qualified")
        raise LAICoveragePolicyUnavailableError(decision.reason)


def lai_result_matches_current_coverage_policy(metadata: object) -> bool:
    """Return whether stored output is bound to the active confirmed policy.

    This row-level check remains fail-closed even if a future release enables
    the global gate. A future policy release must both expose a non-empty
    identity here and persist the exact identity with every accepted result.
    """
    decision = get_lai_production_coverage_decision()
    if not decision.production_qualified:
        return False
    if not isinstance(metadata, Mapping):
        return False
    return metadata.get("lai_coverage_policy_id") == decision.confirmed_policy_id


def policy_qualified_finding_clause(category_column: sa.ColumnElement) -> sa.ColumnElement:
    """SQL predicate that quarantines pre-policy LAI findings.

    NULL categories remain eligible for legacy non-LAI findings. A future
    policy release must replace this unconditional category quarantine with an
    exact stored-policy-identity match before exposing new local-ancestry rows.
    """
    return sa.or_(
        category_column.is_(None),
        category_column != UNQUALIFIED_LAI_FINDING_CATEGORY,
    )


def encode_lai_insufficient_data_reason(reason: LAIInsufficientDataReason) -> str:
    """Encode a typed reason for the legacy text-only ``jobs.error`` column."""
    payload = json.dumps(reason.as_dict(), sort_keys=True, separators=(",", ":"))
    return f"{_ENCODED_REASON_PREFIX}{payload}"


def decode_lai_insufficient_data_reason(value: str | None) -> LAIInsufficientDataReason | None:
    """Decode a reason written by :func:`encode_lai_insufficient_data_reason`.

    Operational errors and malformed values return ``None`` so callers retain
    the existing free-text error behavior.
    """
    if not value or not value.startswith(_ENCODED_REASON_PREFIX):
        return None
    try:
        raw = json.loads(value.removeprefix(_ENCODED_REASON_PREFIX))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, dict) or set(raw) != {"code", "category", "message", "retryable"}:
        return None
    if not all(
        isinstance(raw[field], str) and raw[field] for field in ("code", "category", "message")
    ):
        return None
    if not isinstance(raw["retryable"], bool):
        return None
    return LAIInsufficientDataReason(
        code=raw["code"],
        category=raw["category"],
        message=raw["message"],
        retryable=raw["retryable"],
    )
