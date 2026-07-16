"""Safety tests for the cancer-PRS analytic reference curation script."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts import derive_cancer_prs_reference as derive


def test_write_mode_never_fetches_or_mutates_disabled_nonreporting_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "weight_sets": [
            {
                "name": "Active score",
                "scoring_enabled": True,
                "reference_mean": 0.0,
                "reference_std": 1.0,
                "weights": [{"rsid": "rsACTIVE", "effect_allele": "A", "weight": 0.2}],
            },
            {
                "name": "Disabled non-reporting score",
                "scoring_enabled": False,
                "reference_mean": 7.0,
                "reference_std": 8.0,
                "weights": [{"rsid": "rsDISABLED", "effect_allele": "T", "weight": 0.9}],
            },
        ]
    }
    weights_path = tmp_path / "weights.json"
    weights_path.write_text(json.dumps(payload), encoding="utf-8")
    disabled_before = payload["weight_sets"][1]
    requested_rsids: list[str] = []

    def fake_fetch(rsids: list[str]) -> dict:
        requested_rsids.extend(rsids)
        return {
            "rsACTIVE": {
                "populations": [
                    {
                        "population": derive.PRIMARY_POP,
                        "allele": "A",
                        "frequency": 0.25,
                    },
                    {
                        "population": derive.PRIMARY_POP,
                        "allele": "G",
                        "frequency": 0.75,
                    },
                ]
            }
        }

    monkeypatch.setattr(derive, "WEIGHTS_PATH", weights_path)
    monkeypatch.setattr(derive, "fetch_populations", fake_fetch)
    monkeypatch.setattr(sys, "argv", ["derive_cancer_prs_reference.py", "--write"])

    derive.main()

    written = json.loads(weights_path.read_text(encoding="utf-8"))
    assert requested_rsids == ["rsACTIVE"]
    assert written["weight_sets"][1] == disabled_before
    assert written["weight_sets"][0]["weights"][0]["effect_allele_freq"] == 0.25
