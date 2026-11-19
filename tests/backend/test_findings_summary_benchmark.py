"""Keep the performance fixtures discriminating at the card cutoff and final verdict."""

from __future__ import annotations

import hashlib
import json

import pytest

from scripts.benchmark_findings_summary import dataset, measure


@pytest.mark.parametrize(
    ("shape", "count", "expected_ids"),
    [("dense", 12, [1, 3, 5, 7, 9]), ("wide", 401, [1, 401, 301, 201, 101])],
)
def test_payload_exclusion_precedes_five_distinct_visible_cards(shape, count, expected_ids):
    stored, safe = dataset(shape, count)
    unacknowledged, held = count + 3, count + 1
    eligible = sorted(
        (r for r in stored if r["id"] != unacknowledged and r["evidence_level"] >= 3),
        key=lambda r: (-r["evidence_level"], r["module"]),
    )
    assert [row["id"] for row in eligible[:6]] == [held, *expected_ids]
    assert held not in {row["id"] for row in safe}
    response = measure(shape, count, profile=False)
    assert (response["visible"], response["cards"], response["withheld"]) == (count + 1, 5, False)


def test_real_final_refusal_returns_the_complete_distinct_withheld_body():
    result = measure("response_refused", 8, profile=True)
    expected = {
        "total_findings": 0,
        "modules": [],
        "high_confidence_findings": [],
        "evidence_level_counts": [],
        "withheld": True,
    }
    encoded = json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
    assert result["rows"] == 2
    assert result["body_sha256"] == hashlib.sha256(encoded).hexdigest()
    assert result["profile"]["_is_patient_presentable_row"]["calls"] == 2
    assert result["profile"]["_row_to_response"]["calls"] == 2
    assert result["profile"]["is_patient_presentable_response_payload"]["calls"] == 1
