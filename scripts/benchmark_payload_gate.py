#!/usr/bin/env python3
"""Compare summary filtering on bounded synthetic findings, without variant data.

Run CPU-heavy comparisons in a finite compute allocation, for example::

    python scripts/benchmark_payload_gate.py --rows 10000 --repeats 3

The legacy lane freezes the composition replaced by #2445 (main 2951af43).
Identifier matching, normalization and all production summary code are shared
and unchanged. Every timed result must equal the same asserted summary body.
Profiling is a separate diagnostic, excluded from the timing comparison.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import io
import json
import platform
import pstats
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.analysis import pharmacogenomics as pgx
from backend.api.routes import findings as route
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings


def _legacy_strings_mention_held_pair(values: list[str]) -> bool:
    """Original composition; its generic matchers remain production functions."""
    return any(
        (
            any(pgx._text_mentions_identifier(item, gene) for item in values)
            and any(pgx._text_mentions_identifier(item, drug) for item in values)
        )
        or any(pgx._text_mentions_identifier_sequence(item, gene, drug) for item in values)
        for gene, drug in pgx.WITHHELD_PRESCRIBING_ALERT_PAIRS
    )


def _synthetic_rows(count: int) -> list[dict[str, object]]:
    rows = [
        {
            "id": index + 1,
            "module": "synthetic_alpha" if index % 2 == 0 else "synthetic_beta",
            "category": "synthetic_observation",
            "evidence_level": 2,
            "gene_symbol": "SYNTHETIC",
            "finding_text": f"Synthetic observation {index:06d}: independent software control.",
            "conditions": "Synthetic condition label",
            "detail_json": json.dumps(
                {
                    "description": "Synthetic annotation only",
                    "labels": ["bounded", "public control"],
                }
            ),
            "provenance": json.dumps(
                {
                    "pipeline_version": "synthetic",
                    "sources": {"fixture": {"version": "synthetic-v1", "label": "synthetic"}},
                }
            ),
            "pmid_citations": "[]",
        }
        for index in range(count)
    ]
    return [
        *rows,
        {
            **rows[0],
            "id": count + 1,
            "detail_json": json.dumps({"note": "CYP2D6 tamoxifen withheld software control"}),
        },
        {
            **rows[0],
            "id": count + 2,
            "detail_json": json.dumps(
                {
                    "effects": [
                        {"gene": "CYP2D6", "drug": "codeine"},
                        {"gene": "CYP2C19", "drug": "tamoxifen"},
                    ]
                }
            ),
        },
    ]


def _checked_summary(engine: sa.Engine, count: int, rows: list[dict[str, object]]) -> dict:
    with (
        patch.object(route, "_get_sample_engine", return_value=engine),
        patch.object(route, "gated_modules_to_hide", return_value=set()),
    ):
        result = route.findings_summary(sample_id=1).model_dump(mode="json")
    assert result["total_findings"] == count + 1
    assert result["withheld"] is False
    assert result["high_confidence_findings"] == []
    assert len(result["modules"]) == 2
    assert {item["module"]: item["count"] for item in result["modules"]} == {
        "synthetic_alpha": count // 2 + 1,
        "synthetic_beta": count // 2,
    }
    assert {item["module"]: item["top_finding_text"] for item in result["modules"]} == {
        "synthetic_alpha": rows[0]["finding_text"],
        "synthetic_beta": rows[1]["finding_text"],
    }
    assert result["evidence_level_counts"] == [{"evidence_level": 2, "count": count + 1}]
    return result


def benchmark(count: int, repeats: int) -> dict:
    """Compare alternating serial lanes, then profile the optimized predicate."""
    if not 2 <= count <= 20_000 or count % 2:
        raise ValueError("rows must be an even integer between 2 and 20000")
    if not 1 <= repeats <= 10:
        raise ValueError("repeats must be between 1 and 10")
    rows = _synthetic_rows(count)
    scan = pgx._strings_mention_held_pair
    lanes: dict[str, Callable[[list[str]], bool]] = {
        "legacy": _legacy_strings_mention_held_pair,
        "single_scan": scan,
    }
    timings: dict[str, list[float]] = {lane: [] for lane in lanes}
    engine = sa.create_engine("sqlite://")
    try:
        create_sample_tables(engine)
        with engine.begin() as conn:
            conn.execute(findings.insert(), rows)
        # Verify both filter outcomes and record boundaries before timing.
        for matcher in lanes.values():
            with patch.object(pgx, "_strings_mention_held_pair", matcher):
                assert pgx.is_patient_presentable_finding_payload(rows[0])
                assert not pgx.is_patient_presentable_finding_payload(rows[-2])
                assert pgx.is_patient_presentable_finding_payload(rows[-1])

        expected = None
        for repeat in range(repeats):
            order = list(lanes) if repeat % 2 == 0 else list(reversed(lanes))
            for lane in order:
                with patch.object(pgx, "_strings_mention_held_pair", lanes[lane]):
                    started = time.perf_counter()
                    result = _checked_summary(engine, count, rows)
                    seconds = time.perf_counter() - started
                if expected is None:
                    expected = result
                assert result == expected, "Complete summary body changed between lanes"
                timings[lane].append(seconds)
                print(json.dumps({"repeat": repeat, "lane": lane, "seconds": seconds}), flush=True)

        profile = cProfile.Profile()
        profile.enable()
        verdicts = [
            pgx.is_patient_presentable_finding_payload(row) for row in rows[: min(32, count)]
        ]
        profile.disable()
        assert verdicts == [True] * min(32, count)
        stream = io.StringIO()
        pstats.Stats(profile, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(30)
        medians = {lane: statistics.median(values) for lane, values in timings.items()}
        return {
            "synthetic_only": True,
            "python": platform.python_version(),
            "rows": len(rows),
            "safe_rows": count + 1,
            "held_rows": 1,
            "seconds": timings,
            "median_seconds": medians,
            "speedup": medians["legacy"] / medians["single_scan"],
            "input_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
            "summary_sha256": hashlib.sha256(
                json.dumps(expected, sort_keys=True).encode()
            ).hexdigest(),
            "legacy_reference": (
                "2951af43c561050628990883b86f2cf0ad313e5f:_strings_mention_held_pair"
            ),
            "residual_profile": stream.getvalue(),
            "limitations": "Software-only synthetic predicate comparison; not clinical validation "
            "or a measurement of a real sample. Summary row loading remains linear (#2358).",
        }
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10_000)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not __debug__:
        parser.error("Run without Python optimization so acceptance assertions execute")
    try:
        result = benchmark(args.rows, args.repeats)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
