#!/usr/bin/env python3
"""Measure a checkout's findings summary on deterministic, synthetic SQLite data.

Run the same script in both checkouts and compare the complete response digests.
Use --profile separately from timing. No sample database or registry is opened.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import pstats
import sys
import time
from collections import Counter
from contextlib import nullcontext, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.analysis import cross_module_links
from backend.api.routes import findings as route
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings
from scripts.benchmark_payload_gate import _synthetic_rows

SHAPES = ("low", "dense", "wide", "empty", "held_only", "response_refused", "links")

LIVE_LINK = {"module": "synthetic_destination", "note": "Current synthetic panel note."}


def dataset(shape: str, count: int) -> tuple[list[dict], list[dict]]:
    if shape == "empty":
        return [], []
    if shape == "links":
        # Traits persists to_module. The generic response adds target_module
        # from the live panel and preserves the other persisted fields.
        previous = {
            "to_module": "former_destination",
            "link_type": "synthetic-link",
            "note": "Former note.",
            "cross_module_note": "Former note.",
            "trait_domain": "synthetic-domain",
        }
        live = dict(
            id=2,
            module="traits",
            category="cross_module",
            rsid="synthetic-live",
            evidence_level=3,
            finding_text="Synthetic card. Former note.",
            created_at=datetime(2020, 1, 1),
            detail_json=json.dumps(previous),
        )
        retired = {**live, "id": 1, "rsid": "synthetic-retired", "evidence_level": 4}
        current = {
            **previous,
            "target_module": LIVE_LINK["module"],
            "cross_module_note": LIVE_LINK["note"],
        }
        refreshed = {
            **live,
            "finding_text": "Synthetic card. " + LIVE_LINK["note"],
            "detail_json": json.dumps(current),
        }
        return [retired, live], [refreshed]
    if shape == "response_refused":
        # Same two visible records as the production aggregate-refusal regression.
        common = {"module": "fitness", "evidence_level": 4, "created_at": datetime(2020, 1, 1)}
        pair = [
            dict(
                common,
                id=1,
                category="pathway_summary",
                gene_symbol="CYP2D6",
                drug=None,
                finding_text="Safe source row one",
            ),
            dict(
                common,
                id=2,
                category="legacy_note",
                gene_symbol=None,
                drug="tamoxifen",
                finding_text="Safe source row two",
            ),
        ]
        return pair, pair
    if shape == "held_only":
        count = 2
    corpus = _synthetic_rows(count)
    safe = corpus[:-2]
    held = corpus[-2]
    boundary = corpus[-1]
    for index, row in enumerate(safe):
        row["module"] = f"synthetic_{index % 17:02d}"
        if shape == "dense":
            if index % 2 == 0:
                row["evidence_level"] = 4 if index < 10 else 3
        if shape == "wide":
            if index % 100 == 0:
                row["evidence_level"] = 4 if index < 500 else 3
            row["detail_json"] = json.dumps(
                {
                    "sections": [
                        {
                            "label": f"synthetic section {i}",
                            "text": "ordinary neutral description " * 4,
                        }
                        for i in range(8)
                    ]
                }
            )
    # Keep one real payload-gate exclusion and one safe group-boundary control.
    # The unacknowledged module also exercises the persisted disclosure gate.
    held["module"] = "first_synthetic_control"
    held["evidence_level"] = 4
    boundary["module"] = safe[0]["module"]
    boundary["finding_text"] = "Independent synthetic boundary control."
    hidden = {**safe[0], "id": count + 3, "module": "apoe", "evidence_level": 4}
    for row in [*corpus, hidden]:
        row["created_at"] = datetime(2020, 1, 1)
    if shape == "held_only":
        return [held, hidden], []
    return [*corpus, hidden], [*safe, boundary]


def check_body(body: dict, safe: list[dict], refused: bool) -> None:
    if refused or not safe:
        expected = dict(
            total_findings=0,
            modules=[],
            evidence_level_counts=[],
            high_confidence_findings=[],
            withheld=refused,
        )
        if body != expected:
            raise ValueError("Wrong empty or withheld summary")
        return
    if body["withheld"] or body["total_findings"] != len(safe):
        raise ValueError("Wrong visibility or total")
    levels = Counter(row["evidence_level"] for row in safe)

    def counts(counter: Counter) -> list[dict]:
        return [
            {"evidence_level": level, "count": counter[level]}
            for level in sorted(counter, reverse=True)
        ]

    if body["evidence_level_counts"] != counts(levels):
        raise ValueError("Wrong global evidence counts")
    # The fixture is inserted in ID order. The query has no contractual third
    # sort key, so allow any tied text here and compare the exact bodies across lanes.
    grouped: dict[str, list[dict]] = {}
    for row in safe:
        grouped.setdefault(row["module"], []).append(row)
    expected_order = sorted(
        grouped, key=lambda m: (-max(row["evidence_level"] for row in grouped[m]), m)
    )
    if [m["module"] for m in body["modules"]] != expected_order:
        raise ValueError("Wrong module set or order")
    for item in body["modules"]:
        rows = grouped[item["module"]]
        by_level = Counter(row["evidence_level"] for row in rows)
        highest = max(by_level)
        if (item["count"], item["max_evidence_level"], item["evidence_level_counts"]) != (
            len(rows),
            highest,
            counts(by_level),
        ):
            raise ValueError("Wrong module counts")
        if item["top_finding_text"] not in {
            r["finding_text"] for r in rows if r["evidence_level"] == highest
        }:
            raise ValueError("Unexpected module preview")
    eligible = sorted(
        (r for r in safe if r["evidence_level"] >= 3),
        key=lambda r: (-r["evidence_level"], r["module"]),
    )
    cards = body["high_confidence_findings"]
    allowed = {r["id"]: r for r in eligible}
    if len(cards) != min(5, len(eligible)) or len({c["id"] for c in cards}) != len(cards):
        raise ValueError("Wrong card count or duplicate")
    for card, highest_row in zip(cards, eligible):
        source = allowed[card["id"]]
        if (
            (card["evidence_level"], card["module"])
            != (highest_row["evidence_level"], highest_row["module"])
            or card["finding_text"] != source["finding_text"]
            or card["module"] != source["module"]
        ):
            raise ValueError("Unexpected card rank or content")
        if source["category"] == "cross_module" and card["detail"] != json.loads(
            source["detail_json"]
        ):
            raise ValueError("Unexpected current link destination or note")
    strongest = [row for row in eligible if row["evidence_level"] == 4]
    if len(strongest) == 5 and [card["id"] for card in cards] != [row["id"] for row in strongest]:
        raise ValueError("The fifth visible card must survive the higher-ranked excluded row")


def profile_components(profile: cProfile.Profile) -> dict:
    components = {}
    stats = pstats.Stats(profile).stats
    for name, suffix in [
        ("findings_summary", "api/routes/findings.py"),
        ("_is_patient_presentable_row", "api/routes/findings.py"),
        ("_row_to_response", "api/routes/findings.py"),
        ("is_patient_presentable_response_payload", "analysis/pharmacogenomics.py"),
        ("fetchall", "engine/result.py"),
        ("_execute_clauseelement", "engine/base.py"),
    ]:
        matches = [
            s for (file, _, func), s in stats.items() if file.endswith(suffix) and func == name
        ]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous profile function {name}")
        calls, own, cumulative = matches[0][1:4] if matches else (0, 0.0, 0.0)
        components[name] = {"calls": calls, "self_seconds": own, "cumulative_seconds": cumulative}
    return components


def measure(shape: str, count: int, profile: bool) -> dict:
    rows, safe = dataset(shape, count)
    engine = sa.create_engine("sqlite://")
    try:
        create_sample_tables(engine)
        if rows:
            with engine.begin() as conn:
                conn.execute(findings.insert(), rows)
        refused = shape == "response_refused"
        # Supply only panel reference data. The production link resolver,
        # row eligibility, preview refresh and response conversion all run.
        panel = (
            patch.object(
                cross_module_links, "_links_for_module", return_value={"synthetic-live": LIVE_LINK}
            )
            if shape == "links"
            else nullcontext()
        )
        with patch.object(route, "_get_sample_engine", return_value=engine), panel:
            profiler = cProfile.Profile() if profile else None
            with profiler if profiler is not None else nullcontext():
                started = time.perf_counter()
                response = route.findings_summary(sample_id=1)
                seconds = time.perf_counter() - started
        body = response.model_dump(mode="json")
        check_body(body, safe, refused)
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        return {
            "shape": shape,
            "rows": len(rows),
            "seconds": seconds,
            "visible": body["total_findings"],
            "modules": len(body["modules"]),
            "cards": len(body["high_confidence_findings"]),
            "withheld": body["withheld"],
            "body_sha256": hashlib.sha256(encoded).hexdigest(),
            "profile": profile_components(profiler) if profiler is not None else None,
        }
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rows", type=int, default=10000, help="Synthetic observations before controls"
    )
    parser.add_argument("--shape", choices=SHAPES, required=True)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    if not 2 <= args.rows <= 311468:
        parser.error("Choose 2 to 311468 synthetic observations before controls.")
    # The real final-refusal case logs a diagnostic. Reserve stdout for the result.
    with redirect_stdout(sys.stderr):
        result = measure(args.shape, args.rows, args.profile)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
