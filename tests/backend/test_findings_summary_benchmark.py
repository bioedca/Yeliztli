"""Keep the performance fixtures discriminating at the card cutoff and final verdict."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

import pytest

from backend.analysis import pharmacogenomics as pgx
from scripts import benchmark_findings_summary as benchmark
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
    response = measure(shape, count, profile=True)
    assert (response["visible"], response["cards"], response["withheld"]) == (count + 1, 5, False)
    assert response["profile"]["_row_to_response"]["calls"] == 5


def test_summary_discards_field_scans_after_each_request(monkeypatch):
    scanned = []
    feed = pgx._RenderedTextHeldPairScanner.feed

    def observe(scanner, chunk):
        if chunk == "Synthetic annotation only":
            scanned.append(chunk)
        return feed(scanner, chunk)

    monkeypatch.setattr(pgx._RenderedTextHeldPairScanner, "feed", observe)
    digests = []
    for _ in range(2):
        before = len(scanned)
        result = measure("low", 64, profile=True)
        assert (result["visible"], result["cards"], result["withheld"]) == (65, 0, False)
        assert result["profile"]["_is_patient_presentable_row"]["calls"] == 66
        assert len(scanned) - before == 1
        digests.append(result["body_sha256"])
    assert digests[0] == digests[1]


def test_plain_label_normalization_preserves_printable_ascii_and_normalizes_other_text():
    printable = "".join(chr(i) for i in range(32, 127))
    with patch.object(pgx.unicodedata, "normalize", wraps=pgx.unicodedata.normalize) as normalize:
        assert pgx._normalize_prescribing_text(printable) == printable
        normalize.assert_not_called()
        assert pgx._normalize_prescribing_text("one\ttwo") == "one two"
        assert pgx._normalize_prescribing_text("café") == "cafe"
        assert normalize.called


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


def test_live_panel_refreshes_the_only_returned_link():
    stored, expected = dataset("links", 8)
    assert [row["rsid"] for row in stored] == ["synthetic-retired", "synthetic-live"]
    assert stored[1]["finding_text"] == "Synthetic card. Former note."
    original_detail = json.loads(stored[1]["detail_json"])
    assert original_detail["to_module"] == "former_destination"
    assert "target_module" not in original_detail
    assert [row["finding_text"] for row in expected] == [
        "Synthetic card. Current synthetic panel note."
    ]
    assert json.loads(expected[0]["detail_json"]) == {
        **original_detail,
        "target_module": "synthetic_destination",
        "cross_module_note": "Current synthetic panel note.",
    }
    result = measure("links", 8, profile=True)
    assert (result["rows"], result["visible"], result["cards"], result["withheld"]) == (
        2,
        1,
        1,
        False,
    )
    assert result["profile"]["_is_patient_presentable_row"]["calls"] == 2
    assert result["profile"]["_row_to_response"]["calls"] == 1


def test_held_only_uses_a_bounded_seed_at_the_largest_supported_size():
    with patch.object(
        benchmark, "_synthetic_rows", return_value=benchmark._synthetic_rows(2)
    ) as seed:
        stored, visible = dataset("held_only", 311468)
    seed.assert_called_once_with(2)
    assert len(stored) == 2
    assert visible == []
